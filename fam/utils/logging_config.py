"""Centralized logging configuration with rotating file handler."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_log_path = None


def setup_logging(data_dir: str | None = None):
    """Set up file-based rotating log in the data directory.

    Parameters
    ----------
    data_dir : str, optional
        Directory for the log file.  When ``None``, falls back to the
        legacy behaviour (next to the executable or project root).

    Returns the log file path.  5 MB per file, 3 backups = 20 MB max.
    """
    global _log_path

    if data_dir:
        log_dir = data_dir
    elif getattr(sys, 'frozen', False):
        log_dir = os.path.dirname(sys.executable)
    else:
        log_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

    _log_path = os.path.join(log_dir, 'fam_manager.log')

    handler = RotatingFileHandler(
        _log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
    )
    # Embed the app version in every log line (v1.9.9+) so historical
    # entries keep their original version when the user upgrades —
    # without this, a re-sync after an upgrade attributes every old
    # error to the *current* __version__, destroying provenance.
    #
    # Format: "2026-04-29 10:51:25 [ERROR] [v1.9.9] fam.module: msg"
    # Pre-v1.9.9 log lines lack the [vX.Y.Z] token; log_reader treats
    # those as version "Unknown" rather than misattributing them.
    from fam import __version__ as _app_version
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] [v' + _app_version + '] '
        '%(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    # Force log timestamps to US Eastern regardless of system timezone
    from fam.utils.timezone import eastern_now
    formatter.converter = lambda *_args: eastern_now().timetuple()
    handler.setFormatter(formatter)

    # v2.0.1: attach the rotating file handler to the ROOT logger
    # so every relevant log source reaches the file:
    #
    #   * ``fam.*`` records propagate up to root and hit the handler.
    #     The fam logger's level is INFO so its own INFO records pass
    #     the originating-level check; root's level (WARNING) does
    #     NOT re-filter propagated records (Python logging only
    #     checks level at the ORIGINATING logger, not at ancestors
    #     during propagation).
    #
    #   * Third-party loggers (gspread, urllib3, requests, google.auth)
    #     originate at NOTSET → walk up to root → WARNING+ passes
    #     and reaches the handler.  Lower-priority third-party
    #     chatter (e.g. urllib3 connection-pool INFO) is filtered
    #     at the originating level so the rotating file doesn't
    #     fill in minutes.
    #
    # Pre-v2.0.1 the handler was attached ONLY to the ``fam`` logger,
    # so third-party records emitted on root never reached the file —
    # gspread auth errors, urllib3 retry warnings, google.auth token
    # refresh failures all silently vanished.
    fam_logger = logging.getLogger('fam')
    fam_logger.setLevel(logging.INFO)
    # IMPORTANT: keep propagate=True (the default).  fam records must
    # propagate up to root where the handler now lives.  Setting
    # propagate=False would re-introduce the pre-v2.0.1 silence for
    # any record emitted on a fam.* logger.
    fam_logger.propagate = True
    # Strip any rotating file handler that was previously attached
    # directly to fam (legacy code path).  Otherwise we'd double-emit
    # every fam record once it propagates to root.
    for h in list(fam_logger.handlers):
        if isinstance(h, RotatingFileHandler):
            fam_logger.removeHandler(h)

    root_logger = logging.getLogger()
    # Root level WARNING filters routine third-party INFO chatter
    # (urllib3 connection pool, gspread debug noise, etc.) at the
    # ORIGINATING level — so those records don't even reach the
    # callHandlers chain.  Only affects records originating from
    # loggers whose effective level resolves up to root.  fam.*
    # records have an explicit INFO level via fam_logger.setLevel
    # above, so they pass independently.
    if root_logger.level == logging.NOTSET or root_logger.level > logging.WARNING:
        root_logger.setLevel(logging.WARNING)
    # Idempotent: only attach our handler once.  Identity check by
    # baseFilename so re-running setup_logging() doesn't pile up
    # rotating-file handlers (which would lock the file under each
    # other on Windows).
    already_attached = any(
        isinstance(h, RotatingFileHandler)
        and getattr(h, 'baseFilename', None) == handler.baseFilename
        for h in root_logger.handlers
    )
    if not already_attached:
        root_logger.addHandler(handler)

    # v2.0.1: route ``warnings.warn(...)`` calls (DeprecationWarning,
    # ResourceWarning, etc.) through the logging system so they too
    # reach the rotating file and the Error Log report.  Without
    # this, Python warnings print to stderr and vanish.
    logging.captureWarnings(True)

    return _log_path


def get_log_path():
    """Return the log file path (available after setup_logging()).

    Falls back to computing the path the same way setup_logging() does
    if called before setup has run.
    """
    if _log_path:
        return _log_path
    if getattr(sys, 'frozen', False):
        log_dir = os.path.dirname(sys.executable)
    else:
        log_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
    return os.path.join(log_dir, 'fam_manager.log')


def clear_log_files() -> tuple[bool, str]:
    """Truncate the active log file and delete any rotated backups.

    Called from Settings → Reset to Defaults so the Error Log tab
    doesn't keep showing pre-reset entries after the user wipes their
    data.  Best-effort: failures are reported in the return tuple but
    never raised — Reset itself must still succeed even if the log
    file is locked or missing.

    Returns
    -------
    (ok, message) : tuple
        ``ok`` is True if the active log file was successfully cleared
        (rotated-backup deletion is best-effort and doesn't affect
        ``ok``).  ``message`` is a human-readable status string for
        diagnostics — empty on success.
    """
    log_path = get_log_path()
    if not log_path:
        return True, ''

    # Release any FileHandler streams attached to the ``fam`` AND
    # the ROOT logger so the OS unlocks the file on Windows.
    # v2.0.1: the rotating file handler now lives on the ROOT
    # logger (so third-party records reach it).  Pre-v2.0.1 this
    # function only walked ``fam``'s handlers — that left the
    # root-attached handler holding an exclusive Windows lock,
    # making the subsequent ``open(log_path, 'w')`` raise
    # PermissionError.  We deliberately keep the handler attached
    # to the logger — the next ``emit`` call transparently reopens
    # ``self.stream`` (mode='a' on a freshly truncated file starts
    # at byte 0).
    for logger_name in ('fam', ''):  # '' is the root logger
        target = logging.getLogger(logger_name)
        for handler in list(target.handlers):
            if isinstance(handler, logging.FileHandler):
                try:
                    handler.acquire()
                    try:
                        handler.flush()
                        if handler.stream is not None:
                            try:
                                handler.stream.close()
                            except Exception:
                                pass
                            handler.stream = None
                    finally:
                        handler.release()
                except Exception:
                    # Don't let a stuck handler block the rest of the
                    # cleanup — we'll still try to truncate / delete files.
                    pass

    # Truncate the active log file.
    truncated = False
    if os.path.exists(log_path):
        try:
            with open(log_path, 'w', encoding='utf-8'):
                pass
            truncated = True
        except OSError as e:
            return False, f"Could not truncate {log_path}: {e}"
    else:
        truncated = True  # nothing to truncate is fine

    # Delete rotated backups.  Default backupCount is 3, but be generous
    # in case it was raised in the past — anything missing is harmless.
    deleted = 0
    skipped = 0
    for i in range(1, 10):
        rotated = f"{log_path}.{i}"
        if os.path.exists(rotated):
            try:
                os.remove(rotated)
                deleted += 1
            except OSError:
                skipped += 1

    msg = ''
    if not truncated or skipped:
        msg = (f"truncated={truncated} backups_deleted={deleted}"
               f" backups_skipped={skipped}")
    return truncated, msg
