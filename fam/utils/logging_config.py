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
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    # Force log timestamps to US Eastern regardless of system timezone
    from fam.utils.timezone import eastern_now
    formatter.converter = lambda *_args: eastern_now().timetuple()
    handler.setFormatter(formatter)

    root = logging.getLogger('fam')
    root.setLevel(logging.INFO)
    # Avoid duplicate handlers if setup_logging is called more than once
    if not root.handlers:
        root.addHandler(handler)

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

    # Release any FileHandler streams attached to the ``fam`` logger so
    # the OS unlocks the file on Windows.  We deliberately keep the
    # handler attached to the logger — the next ``emit`` call will
    # transparently reopen ``self.stream`` (mode='a' on a freshly
    # truncated file starts at byte 0).
    fam_logger = logging.getLogger('fam')
    for handler in list(fam_logger.handlers):
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
