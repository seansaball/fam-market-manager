"""Tests for the v1.9.9 error-log version preservation + Clear Errors UI.

Two issues from the v1.9.9 onsite findings:

1. Upgrading the app rewrote previous error-log entries' "App
   Version" column to the current version, destroying provenance.
   Fixed by embedding ``[vX.Y.Z]`` in every log line at write time
   and parsing it back out per-entry — entries with no embedded
   version (legacy lines from before this fix) surface as
   ``Unknown`` rather than being silently re-attributed.

2. Coordinators wanted a way to clear noise from the error log.
   The Reports → Error Log tab now has a "Clear Errors" button
   that truncates ``fam_manager.log`` (+ rotated backups) locally
   AND clears the corresponding Google Sheets tab when sync is
   configured.  The audit log (Activity Log report) is intentionally
   untouched — that's regulatory history, not error noise.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest


# ══════════════════════════════════════════════════════════════════
# Logger formatter embeds version
# ══════════════════════════════════════════════════════════════════
class TestLoggerEmbedsVersion:

    def test_setup_logging_writes_version_token_per_line(
            self, tmp_path, monkeypatch):
        """Every line written by the rotating handler should carry
        the current app version inside ``[vX.Y.Z]`` between the level
        and the logger name."""
        # Detach existing handlers so our temp setup is the only one
        # writing during this test.
        fam_logger = logging.getLogger('fam')
        saved = list(fam_logger.handlers)
        for h in saved:
            fam_logger.removeHandler(h)
        try:
            from fam.utils.logging_config import setup_logging
            from fam import __version__
            log_path = setup_logging(data_dir=str(tmp_path))

            test_logger = logging.getLogger('fam.test_versioning')
            test_logger.error("synthetic test error")
            for h in fam_logger.handlers:
                h.flush()

            with open(log_path, 'r', encoding='utf-8') as f:
                content = f.read()
            assert f'[v{__version__}]' in content, (
                f"Expected [v{__version__}] in log line, got:\n{content!r}"
            )
            assert 'synthetic test error' in content
        finally:
            for h in list(fam_logger.handlers):
                try:
                    h.close()
                except Exception:
                    pass
                fam_logger.removeHandler(h)
            for h in saved:
                fam_logger.addHandler(h)


# ══════════════════════════════════════════════════════════════════
# log_reader parses the version
# ══════════════════════════════════════════════════════════════════
class TestLogReaderParsesVersion:

    def _write(self, tmp_path, content):
        p = tmp_path / 'fam_manager.log'
        p.write_text(content, encoding='utf-8')
        return str(p)

    def test_v1_9_9_format_extracts_version(self, tmp_path):
        from fam.utils.log_reader import parse_log_file
        content = (
            "2026-04-29 10:51:25 [ERROR] [v1.9.9] fam.ui.payment_screen: "
            "boom\n"
        )
        entries = parse_log_file(self._write(tmp_path, content))
        assert len(entries) == 1
        assert entries[0]['app_version'] == '1.9.9'
        assert entries[0]['module'] == 'fam.ui.payment_screen'
        assert entries[0]['message'] == 'boom'

    def test_legacy_format_marks_version_unknown(self, tmp_path):
        from fam.utils.log_reader import parse_log_file
        # Pre-v1.9.9 line — no [vX.Y.Z] token between the level and
        # the logger name.
        content = (
            "2026-04-23 11:55:05 [ERROR] fam.sync.gsheets: "
            "upsert_rows failed\n"
        )
        entries = parse_log_file(self._write(tmp_path, content))
        assert len(entries) == 1
        assert entries[0]['app_version'] == 'Unknown', (
            f"Legacy log lines must surface as 'Unknown' so they're "
            f"NOT silently re-attributed to the current "
            f"__version__ — got {entries[0]['app_version']!r}"
        )

    def test_mixed_log_preserves_per_line_version(self, tmp_path):
        """A real upgrade scenario: file contains pre-v1.9.9 lines AND
        post-v1.9.9 lines.  Each should carry its own version (or
        'Unknown' for the pre lines)."""
        from fam.utils.log_reader import parse_log_file
        content = (
            "2026-03-10 09:00:00 [ERROR] fam.app: pre-upgrade boom\n"
            "2026-04-29 10:51:25 [ERROR] [v1.9.9] fam.app: post-upgrade boom\n"
        )
        entries = parse_log_file(self._write(tmp_path, content))
        # parse_log_file returns newest-first
        assert entries[0]['app_version'] == '1.9.9'
        assert entries[0]['message'] == 'post-upgrade boom'
        assert entries[1]['app_version'] == 'Unknown'
        assert entries[1]['message'] == 'pre-upgrade boom'

    def test_traceback_continuation_lines_attach_to_parent(
            self, tmp_path):
        """The version-token regex change must NOT break the existing
        multi-line traceback grouping."""
        from fam.utils.log_reader import parse_log_file
        content = (
            "2026-04-29 10:51:25 [ERROR] [v1.9.9] fam.app: boom\n"
            "Traceback (most recent call last):\n"
            "  File \"x.py\", line 1, in <module>\n"
            "    raise RuntimeError\n"
            "RuntimeError\n"
        )
        entries = parse_log_file(self._write(tmp_path, content))
        assert len(entries) == 1
        assert 'Traceback' in entries[0]['traceback']
        assert 'RuntimeError' in entries[0]['traceback']


# ══════════════════════════════════════════════════════════════════
# Clear Errors button source-level + behaviour
# ══════════════════════════════════════════════════════════════════
class TestClearErrorsButton:
    """Source-level guards + a focused behaviour test of the helper
    that clears both the local log file and the Google Sheets tab."""

    def test_reports_screen_has_clear_button_and_handler(self):
        import inspect
        import fam.ui.reports_screen as rs
        src = inspect.getsource(rs)
        assert 'Clear Errors' in src, (
            "Reports → Error Log must surface a 'Clear Errors' "
            "button so coordinators can wipe error noise without "
            "needing dev access to the laptop")
        assert '_clear_error_log' in src
        assert '_clear_sheets_error_log_tab' in src

    def test_clear_error_log_uses_two_stage_confirmation(self):
        """The handler must double-confirm before truncating —
        single-click safety net for an irreversible operation."""
        import inspect
        from fam.ui.reports_screen import ReportsScreen
        src = inspect.getsource(ReportsScreen._clear_error_log)
        # Two QMessageBox calls = two confirmations.
        assert src.count('QMessageBox') >= 2, (
            "Clear Errors must require two confirmation dialogs "
            "before wiping the log")
        assert 'cannot be undone' in src.lower()

    def test_clear_handler_does_not_touch_audit_log(self):
        """The audit log (Activity Log) is regulatory; the Clear
        Errors button must not delete from it.  Source-level guard
        so a future refactor can't silently expand the blast radius."""
        import inspect
        from fam.ui.reports_screen import ReportsScreen
        src = inspect.getsource(ReportsScreen._clear_error_log)
        # Permitted writes: clear_log_files (file), worksheet.clear()
        # (sheets), refresh + dialog.  Audit-log table writes would
        # show up as a DELETE FROM audit_log — none should appear.
        assert 'audit_log' not in src.lower(), (
            "Clear Errors must not touch audit_log (Activity Log) — "
            "that's separate audit history.")
        assert 'DELETE' not in src.upper(), (
            "Clear Errors must not issue raw DELETE statements")

    def test_sheets_clear_skips_when_unconfigured(self, monkeypatch):
        """If sync is not configured, the helper must return a
        graceful 'skipped' status rather than raising."""
        from fam.ui.reports_screen import ReportsScreen
        from fam.sync.gsheets import GoogleSheetsBackend
        # Patch is_configured to return False
        monkeypatch.setattr(
            GoogleSheetsBackend, 'is_configured', lambda self: False)
        # Call the helper on a stub instance — the unbound method
        # is callable with a sentinel `self`.
        screen = MagicMock(spec=ReportsScreen)
        status = ReportsScreen._clear_sheets_error_log_tab(screen)
        assert 'not configured' in status.lower()
        # Most importantly: didn't raise.

    def test_sheets_clear_swallows_exceptions(self, monkeypatch):
        """A sync failure must NOT prevent the local truncation —
        the helper has to return a status string, never raise."""
        from fam.ui.reports_screen import ReportsScreen
        from fam.sync.gsheets import GoogleSheetsBackend
        from fam.utils import app_settings
        monkeypatch.setattr(
            GoogleSheetsBackend, 'is_configured', lambda self: True)
        monkeypatch.setattr(app_settings, 'get_market_code', lambda: 'ABC')
        monkeypatch.setattr(app_settings, 'get_device_id', lambda: 'dev-1')

        def _explode(self, sheet, mc, did):
            raise RuntimeError("network down")
        monkeypatch.setattr(GoogleSheetsBackend, 'delete_rows', _explode)
        screen = MagicMock(spec=ReportsScreen)
        status = ReportsScreen._clear_sheets_error_log_tab(screen)
        assert 'could not' in status.lower() or 'still cleared' in status.lower()

    def test_sheets_clear_is_device_scoped_not_full_wipe(
            self, monkeypatch):
        """Multi-device deployment guard: when device A clicks
        Clear Errors, device B's rows on the shared Google Sheet
        must survive.

        Concrete failure mode: if the helper uses gspread's
        ``ws.clear()`` (truncates the entire worksheet) instead of
        the device-scoped ``backend.delete_rows(sheet, market_code,
        device_id)`` (filters by ``market_code`` + ``device_id``
        before deleting), every device's error history disappears
        on a single coordinator's click.

        Pin the device-scoped contract in a test so a future
        refactor can't silently regress to a global wipe."""
        from fam.ui.reports_screen import ReportsScreen
        from fam.sync.gsheets import GoogleSheetsBackend, SyncResult
        from fam.utils import app_settings

        monkeypatch.setattr(
            GoogleSheetsBackend, 'is_configured', lambda self: True)
        monkeypatch.setattr(app_settings, 'get_market_code',
                            lambda: 'WC')
        monkeypatch.setattr(app_settings, 'get_device_id',
                            lambda: 'device-A-guid')

        captured = {}

        def _fake_delete_rows(self, sheet_name, market_code, device_id):
            captured['sheet'] = sheet_name
            captured['market_code'] = market_code
            captured['device_id'] = device_id
            return SyncResult(success=True, rows_synced=7)

        monkeypatch.setattr(
            GoogleSheetsBackend, 'delete_rows', _fake_delete_rows)

        # Trip-wire: any unscoped global-wipe path would call
        # ``ws.clear()`` via ``_authorize()``.  If the helper takes
        # that path, fail the test loudly — a global wipe must NEVER
        # happen.
        def _no_authorize(self):
            raise AssertionError(
                "Clear Errors must not authorize the worksheet "
                "directly — that's the path to ws.clear(), which "
                "wipes every device's rows.  Use delete_rows() "
                "with market_code + device_id instead.")
        monkeypatch.setattr(
            GoogleSheetsBackend, '_authorize', _no_authorize)

        screen = MagicMock(spec=ReportsScreen)
        status = ReportsScreen._clear_sheets_error_log_tab(screen)

        assert captured.get('sheet') == 'Error Log', (
            f"Expected delete_rows on 'Error Log' tab, got "
            f"{captured.get('sheet')!r}")
        assert captured.get('market_code') == 'WC', (
            "delete_rows must receive THIS device's market_code so "
            "other markets' rows are preserved")
        assert captured.get('device_id') == 'device-A-guid', (
            "delete_rows must receive THIS device's device_id so "
            "other devices' rows on the same sheet survive")
        # And the user-facing status reassures the coordinator that
        # other devices weren't touched.
        assert ('this device' in status.lower() or
                'preserved' in status.lower()), (
            f"Status string should make device-scoping visible to "
            f"the coordinator; got: {status!r}")

    def test_sheets_clear_does_not_call_ws_clear(self):
        """Source-level guard mirroring the behaviour test above —
        ``worksheet.clear()`` is the gspread API that wipes the
        entire tab, so it must not appear in the helper.  A future
        edit that reintroduces it (perhaps because someone thinks
        it's "simpler") would silently destroy other devices'
        error history.

        Walks the AST instead of doing a string match so that
        docstrings/comments mentioning ``ws.clear()`` for
        explanatory purposes don't trip the guard."""
        import ast
        import inspect
        import textwrap
        from fam.ui.reports_screen import ReportsScreen

        src = textwrap.dedent(inspect.getsource(
            ReportsScreen._clear_sheets_error_log_tab))
        tree = ast.parse(src)

        # Any Call whose callee is `<something>.clear()` would be a
        # global-wipe regression.  delete_rows is fine — that's the
        # scoped path.
        offending = []
        delete_rows_seen = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Attribute)
                        and func.attr == 'clear'):
                    offending.append(ast.unparse(node))
                if (isinstance(func, ast.Attribute)
                        and func.attr == 'delete_rows'):
                    delete_rows_seen = True

        assert not offending, (
            f"Clear Errors must not call .clear() — that wipes "
            f"EVERY device's rows on the shared Google Sheet. "
            f"Use backend.delete_rows(sheet, market_code, "
            f"device_id) instead, which filters by this device's "
            f"identity.  Offending calls: {offending}")
        assert delete_rows_seen, (
            "Clear Errors must use delete_rows() so the deletion "
            "is scoped to this device's market_code + device_id.")


# ══════════════════════════════════════════════════════════════════
# Source-level guard: the formatter must keep embedding the version
# ══════════════════════════════════════════════════════════════════
class TestFormatterSourceGuard:
    """The version-embed token in the log formatter is what makes
    error provenance survive upgrades.  Pin it in source so a
    future formatter rewrite can't silently regress."""

    def test_setup_logging_uses_app_version_in_format(self):
        import inspect
        import fam.utils.logging_config as lc
        src = inspect.getsource(lc.setup_logging)
        assert '__version__' in src, (
            "setup_logging must reference fam.__version__ to embed "
            "the version in every log line — without this, error "
            "provenance is lost on every upgrade")
        # The format string must include the [v...] marker so
        # log_reader's regex finds it.
        assert '[v' in src
