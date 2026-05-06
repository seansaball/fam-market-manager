"""``_collect_error_log`` Message field must contain the full
multi-line traceback (v2.0.5 fix).

User-reported regression: in the synced Google Sheet, the ``Message``
column for a CRITICAL entry showed only ``"Unhandled exception:"``
(the first line) — the multi-line traceback was either missing or
in a separate ``Traceback`` column the coordinator didn't notice.

Local Reports → Error Log detail panel had always shown the full
content (Time / Level / Area / Module / Message / Traceback) by
stitching the two fields together; the Sheet didn't.

Fix: ``_collect_error_log`` now embeds ``"<message>\\n\\nTraceback:\\n
<traceback>"`` in the Message column when a traceback is present, so
the Sheet matches the local detail-panel format.  The separate
``Traceback`` column is preserved for backward compatibility with
existing Sheets / dashboards.

Pin both fields here so a future refactor can't silently revert.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_error_log_traceback.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield tmp_path
    close_connection()


def _write_log(tmp_path, content):
    """Write *content* to a temp log file and patch get_log_path."""
    log_path = str(tmp_path / "fam_manager.log")
    with open(log_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return log_path


class TestMessageContainsFullTraceback:

    def test_critical_entry_message_includes_traceback(
            self, tmp_path, monkeypatch):
        """A CRITICAL entry with a multi-line traceback must end up in
        the Message column with the full traceback embedded — not
        just the first ``"Unhandled exception:"`` line."""
        log = (
            "2026-05-05 12:05:56 [CRITICAL] [v2.0.5] fam.app: "
            "Unhandled exception:\n"
            "Traceback (most recent call last):\n"
            '  File "admin_screen.py", line 1420, in _adjust_transaction\n'
            "    txn = get_transaction_by_id(txn_id)\n"
            "UnboundLocalError: cannot access local variable\n"
        )
        log_path = _write_log(tmp_path, log)

        # Patch get_log_path so _collect_error_log reads our temp file
        from fam.utils import logging_config
        monkeypatch.setattr(
            logging_config, 'get_log_path', lambda: log_path)

        from fam.sync.data_collector import _collect_error_log
        rows = _collect_error_log()
        assert len(rows) == 1
        msg = rows[0]['Message']

        # Message MUST start with the original first line
        assert msg.startswith('Unhandled exception:'), (
            "Message field must begin with the log entry's first line "
            "(unchanged from pre-fix).")

        # Message MUST also contain the traceback text
        assert 'Traceback (most recent call last):' in msg, (
            "Message field must EMBED the multi-line traceback so "
            "the coordinator viewing the Sheet sees the full crash "
            "context — not just 'Unhandled exception:' as a "
            "standalone first line.")
        assert 'UnboundLocalError' in msg
        assert 'admin_screen.py' in msg

        # The separate Traceback column is preserved for back-compat
        assert 'UnboundLocalError' in rows[0]['Traceback']

    def test_warning_entry_without_traceback_message_unchanged(
            self, tmp_path, monkeypatch):
        """A WARNING with no continuation lines should keep its
        Message as just the warning text — no spurious 'Traceback:'
        suffix added."""
        log = (
            "2026-05-05 10:00:00 [WARNING] [v2.0.5] fam.sync: "
            "Network unavailable, will retry\n"
        )
        log_path = _write_log(tmp_path, log)
        from fam.utils import logging_config
        monkeypatch.setattr(
            logging_config, 'get_log_path', lambda: log_path)

        from fam.sync.data_collector import _collect_error_log
        rows = _collect_error_log()
        assert len(rows) == 1
        assert rows[0]['Message'] == 'Network unavailable, will retry'
        assert rows[0]['Traceback'] == ''

    def test_message_column_is_what_sheet_will_display(
            self, tmp_path, monkeypatch):
        """End-to-end: the cell value the Sheet sync will write into
        the Message column for a CRITICAL row must contain BOTH the
        original first line AND the full traceback text.  This is
        the user-visible contract."""
        log = (
            "2026-05-05 12:05:56 [CRITICAL] [v2.0.5] fam.app: "
            "Unhandled exception:\n"
            "Traceback (most recent call last):\n"
            "  File \"main_window.py\", line 896, in closeEvent\n"
            "RuntimeError: Internal C++ object already deleted.\n"
        )
        log_path = _write_log(tmp_path, log)
        from fam.utils import logging_config
        monkeypatch.setattr(
            logging_config, 'get_log_path', lambda: log_path)

        from fam.sync.data_collector import _collect_error_log
        from fam.sync.gsheets import _cell_value
        rows = _collect_error_log()

        cell = _cell_value(rows[0]['Message'])
        # The cell that lands in Sheets must contain everything
        for required in (
                'Unhandled exception:',
                'Traceback',
                'main_window.py',
                'RuntimeError',
                'Internal C++ object',
        ):
            assert required in cell, (
                f"Sheet's Message cell missing {required!r}: {cell!r}")


class TestErrorLogCompositeKey:
    """v2.0.5 changed the Error Log composite key away from including
    Message (which is now multi-KB) to using Level instead.  Verify
    the new key is in place."""

    def test_error_log_key_uses_level_not_message(self):
        from fam.sync.manager import SyncManager
        key = SyncManager.SHEET_KEYS.get('Error Log')
        assert key is not None
        assert 'Message' not in key, (
            "Error Log composite key must NOT include 'Message' — "
            "Message now contains the full multi-line traceback "
            "(multi-KB).  Including it in the key is wasteful and "
            "brittle against newline/whitespace normalisation.")
        assert 'Level' in key, (
            "Error Log composite key must include 'Level' so two "
            "errors at the same second from the same module but "
            "different severities (WARNING + CRITICAL) don't dedupe.")
        assert 'Timestamp' in key
        assert 'Module' in key
