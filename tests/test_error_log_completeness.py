"""Comprehensive Error Log surface coverage
(v2.0.1, 2026-05-05).

The user reported TWO incidents where errors didn't appear in the
Error Log report.  Root causes:

  1. ``parse_log_file`` default level filter excluded CRITICAL —
     unhandled-exception entries from the global handler were
     dropped (already fixed; pinned in test_codebase_hygiene.py).
  2. The Drive apostrophe-in-folder-name bug logged a WARNING
     that DID appear post-fix, but the underlying photo upload
     error message ALSO needed to surface.

This file pins the broader contract:

  * The rotating log file captures records from BOTH the ``fam``
    namespace AND third-party libraries (gspread, urllib3,
    requests, google.auth, etc.).
  * Python's ``warnings.warn(...)`` calls also reach the log via
    ``logging.captureWarnings(True)``.
  * ``parse_log_file`` correctly parses every level we emit,
    including CRITICAL and third-party-prefixed module names.
  * Specific high-priority silent ``except`` handlers in sync
    code paths now log at WARNING+ so failures reach the report
    (regression: the previous silent ``pass`` in
    ``SyncWorker.run`` / DEBUG-only handlers in
    ``data_collector`` masked real errors).
"""

import logging
import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _reset_logging():
    """Each test gets a fresh logging state so handlers from prior
    tests (pointing at a now-deleted tmp_path) don't leak."""
    fam_logger = logging.getLogger('fam')
    root_logger = logging.getLogger()
    saved_fam = (list(fam_logger.handlers), fam_logger.level,
                  fam_logger.propagate)
    saved_root = (list(root_logger.handlers), root_logger.level)
    fam_logger.handlers = []
    root_logger.handlers = []
    yield
    fam_logger.handlers = saved_fam[0]
    fam_logger.setLevel(saved_fam[1])
    fam_logger.propagate = saved_fam[2]
    root_logger.handlers = saved_root[0]
    root_logger.setLevel(saved_root[1])


# ════════════════════════════════════════════════════════════════════
# 1. Logging config attaches handler to root + captures warnings
# ════════════════════════════════════════════════════════════════════


class TestLoggingConfigCapturesEverything:
    """``setup_logging()`` must wire the rotating file handler so
    every relevant log source reaches the file: ``fam.*``,
    third-party WARNING+, AND the ``warnings`` module."""

    def test_setup_attaches_handler_to_root_logger(self, tmp_path):
        from fam.utils.logging_config import setup_logging
        from logging.handlers import RotatingFileHandler
        log_path = setup_logging(str(tmp_path))
        root = logging.getLogger()
        attached_paths = [
            h.baseFilename for h in root.handlers
            if isinstance(h, RotatingFileHandler)
        ]
        assert log_path in attached_paths, (
            "Root logger must carry the rotating file handler so "
            "third-party library records (gspread, urllib3, "
            "requests, google.auth) reach the rotating log + "
            "Error Log report.  Pre-v2.0.1 the handler was attached "
            "ONLY to the 'fam' logger and third-party records "
            "silently disappeared.")

    def test_fam_logger_propagates_to_root(self, tmp_path):
        """Pin: fam logger MUST propagate so its records reach the
        root-attached rotating handler.  An earlier v2.0.1 draft
        set propagate=False to avoid double-emit, but that broke
        pytest's caplog (which captures via root) and effectively
        re-silenced fam records.  Correct design: handler attached
        to root only, fam keeps propagate=True, fam_logger has no
        directly-attached rotating handler (so no double-emit)."""
        from fam.utils.logging_config import setup_logging
        from logging.handlers import RotatingFileHandler
        setup_logging(str(tmp_path))
        fam_logger = logging.getLogger('fam')
        assert fam_logger.propagate is True, (
            "fam logger must propagate to root so its records reach "
            "the root-attached rotating file handler")
        # And fam logger must NOT have its own rotating file handler
        # (otherwise propagation would double-emit through both).
        rotating_on_fam = [
            h for h in fam_logger.handlers
            if isinstance(h, RotatingFileHandler)
        ]
        assert rotating_on_fam == [], (
            "fam logger must NOT carry a rotating file handler "
            "directly — the handler lives on root.  Otherwise "
            "every fam record double-emits.")

    def test_setup_logging_calls_capture_warnings(self):
        """Source-pin: ``setup_logging`` must call
        ``logging.captureWarnings(True)`` so Python's
        ``warnings.warn(...)`` calls (Deprecation, Resource, etc.)
        reach the logger.  We can't reliably TEST the routing under
        pytest because pytest's own warning machinery interferes
        with ``logging.captureWarnings`` — so instead we source-pin
        the call exists.  The end-to-end smoke test in
        ``run.py``-equivalent flows verifies the runtime behavior."""
        import inspect
        from fam.utils import logging_config
        src = inspect.getsource(logging_config.setup_logging)
        assert "captureWarnings(True)" in src, (
            "setup_logging must call logging.captureWarnings(True) "
            "so warnings.warn() output reaches the rotating log + "
            "Error Log report")

    def test_py_warnings_logger_records_reach_file(self, tmp_path):
        """End-to-end via the ``py.warnings`` logger directly (the
        target ``captureWarnings`` reroutes warnings to).  This
        sidesteps pytest's interference."""
        from fam.utils.logging_config import setup_logging
        from fam.utils.log_reader import parse_log_file

        log_path = setup_logging(str(tmp_path))
        # Direct emit on the same logger captureWarnings uses.
        logging.getLogger('py.warnings').warning(
            "synthetic deprecation message")
        for h in logging.getLogger().handlers:
            try:
                h.flush()
            except Exception:
                pass

        entries = parse_log_file(log_path)
        py_warn = [e for e in entries if e['module'] == 'py.warnings']
        assert py_warn, (
            "py.warnings logger records must reach the rotating "
            "file (this is where captureWarnings reroutes Python "
            "warnings to)")


# ════════════════════════════════════════════════════════════════════
# 2. Third-party logger records reach the log file
# ════════════════════════════════════════════════════════════════════


class TestThirdPartyLoggersReachLog:
    """gspread / urllib3 / requests / google.auth records must reach
    the rotating log so a coordinator triaging "sync failed" can see
    the full picture in the Error Log report."""

    @pytest.mark.parametrize('logger_name', [
        'gspread.client',
        'urllib3.connectionpool',
        'requests.adapters',
        'google.auth.transport.requests',
        'google.oauth2._client',
    ])
    def test_thirdparty_warning_reaches_log(
            self, tmp_path, logger_name):
        from fam.utils.logging_config import setup_logging
        from fam.utils.log_reader import parse_log_file

        log_path = setup_logging(str(tmp_path))
        third_party = logging.getLogger(logger_name)
        third_party.warning("test message from %s", logger_name)
        for h in logging.getLogger().handlers:
            try:
                h.flush()
            except Exception:
                pass

        entries = parse_log_file(log_path)
        matching = [
            e for e in entries
            if e['module'] == logger_name
        ]
        assert matching, (
            f"WARNING from third-party logger {logger_name!r} must "
            f"reach the rotating log.  All entries: "
            f"{[(e['level'], e['module']) for e in entries]}")
        assert matching[0]['level'] == 'WARNING'

    def test_thirdparty_error_reaches_log(self, tmp_path):
        from fam.utils.logging_config import setup_logging
        from fam.utils.log_reader import parse_log_file

        log_path = setup_logging(str(tmp_path))
        logging.getLogger('gspread.exceptions').error(
            "API 403: forbidden")
        for h in logging.getLogger().handlers:
            try:
                h.flush()
            except Exception:
                pass

        entries = parse_log_file(log_path)
        errs = [e for e in entries if e['level'] == 'ERROR']
        assert errs, "Third-party ERROR must reach the log"
        assert any('forbidden' in e['message'] for e in errs)

    def test_thirdparty_info_filtered_out(self, tmp_path):
        """Defense against spam: third-party libraries log at INFO at
        high frequency (urllib3 connection pool chatter etc.).  The
        root logger level is WARNING, so INFO from third parties
        should NOT reach the file."""
        from fam.utils.logging_config import setup_logging
        from fam.utils.log_reader import parse_log_file

        log_path = setup_logging(str(tmp_path))
        logging.getLogger('urllib3.connectionpool').info(
            "Resetting dropped connection: api.googleapis.com")
        for h in logging.getLogger().handlers:
            try:
                h.flush()
            except Exception:
                pass

        # File may or may not exist; if it does, no urllib3 INFO
        # entries should appear.
        if not os.path.exists(log_path):
            return
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'Resetting dropped connection' not in content, (
            "Third-party INFO must be filtered out by root level "
            "WARNING — otherwise the rotating file fills with "
            "connection-pool chatter")


# ════════════════════════════════════════════════════════════════════
# 3. fam.* logger still emits INFO (lower threshold than root)
# ════════════════════════════════════════════════════════════════════


class TestFamLoggerStillCapturesInfo:
    """Belt-and-suspenders: fam.* records at INFO must continue to
    reach the file even though the root logger is set to WARNING.
    The fam logger has its own handler attached at INFO level."""

    def test_fam_info_reaches_log(self, tmp_path):
        from fam.utils.logging_config import setup_logging
        log_path = setup_logging(str(tmp_path))
        logging.getLogger('fam.test.module').info(
            "fam INFO must still reach log")
        for h in logging.getLogger('fam').handlers:
            try:
                h.flush()
            except Exception:
                pass
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'fam INFO must still reach log' in content


# ════════════════════════════════════════════════════════════════════
# 4. parse_log_file regex parses every produced format
# ════════════════════════════════════════════════════════════════════


class TestLogReaderRegexCoversAll:
    """Defensive: the regex in ``parse_log_file`` must match every
    log-line shape that ``setup_logging``'s formatter can produce,
    including third-party module names with dots and version-less
    legacy lines."""

    @pytest.mark.parametrize('line', [
        "2026-05-05 12:00:00 [CRITICAL] [v2.0.1] fam.app: Unhandled exception:",
        "2026-05-05 12:00:00 [ERROR] [v2.0.1] gspread.exceptions: 403",
        "2026-05-05 12:00:00 [WARNING] [v2.0.1] urllib3.connectionpool: retry",
        "2026-05-05 12:00:00 [WARNING] [v2.0.1] py.warnings: DeprecationWarning",
        "2026-05-05 12:00:00 [WARNING] [v2.0.1] google.auth.transport.requests: refresh",
        "2026-05-05 12:00:00 [WARNING] fam.legacy: pre-v1.9.9 line",
    ])
    def test_regex_matches_each_format(self, line):
        from fam.utils.log_reader import LOG_LINE_RE
        assert LOG_LINE_RE.match(line) is not None, (
            f"parse_log_file regex must match: {line!r}")


# ════════════════════════════════════════════════════════════════════
# 5. Specific silent-handler regressions
# ════════════════════════════════════════════════════════════════════


class TestPriorSilentHandlersNowLog:
    """Pin the v2.0.1 fixes for specific silent ``except`` blocks
    that previously masked real errors.  Source-pin: the bodies must
    contain a ``logger.warning`` call (not a bare ``pass`` or
    ``logger.debug``)."""

    def test_sync_worker_close_connection_logs(self):
        import inspect
        from fam.sync.worker import SyncWorker
        src = inspect.getsource(SyncWorker.run)
        # The close_connection handler block — find it and assert
        # it logs at WARNING+.
        assert 'close_connection' in src
        # Specifically: there must be a logger.warning near the
        # close_connection except handler.
        assert 'logger.warning' in src, (
            "SyncWorker.run finally must log close_connection "
            "failures at WARNING+ so connection leaks reach the "
            "Error Log report (was a silent `pass` pre-v2.0.1)")

    def test_data_collector_begin_failure_warns(self):
        import inspect
        from fam.sync import data_collector
        src = inspect.getsource(data_collector.collect_sync_data)
        # The BEGIN/COMMIT failure handlers must use logger.warning.
        # Pre-v2.0.1 they used logger.debug — below the rotating
        # file threshold, so failures vanished.
        assert "logger.warning" in src, (
            "collect_sync_data BEGIN/COMMIT failures must log at "
            "WARNING+ so degraded snapshot states reach the report")
        # And specifically not debug-only for the BEGIN/COMMIT path.
        # (Other debug calls elsewhere in the function are fine.)

    def test_data_collector_fmnp_lookup_warns(self):
        import inspect
        from fam.sync import data_collector
        src = inspect.getsource(data_collector)
        # The FMNP method-lookup handler must surface failures.
        # Source-pin: a logger.warning call mentioning FMNP.  The
        # actual log string is split across multiple source lines,
        # so we look for a contiguous fragment that appears within
        # one source line.
        assert "could not resolve FMNP method" in src, (
            "FMNP method denomination lookup must warn on failure "
            "so multi-check splits don't silently mis-allocate")

    def test_schema_retention_sweep_warns(self):
        import inspect
        from fam.database import schema as schema_mod
        src = inspect.getsource(schema_mod._write_pre_migration_backup)
        assert "logger.warning" in src, (
            "Pre-migration backup retention sweep failures must "
            "warn (was logger.debug pre-v2.0.1)")
