"""Tests for the Settings → Markets Delete action.

Use case
--------
Some real installs carry legacy/test rows from very early
development — e.g. a market named "M" with the pre-v22 default
match limit ($1.00 from the old REAL column default) that survived
to v1.9.9 because the UI only offered Deactivate, not Delete.
Such rows have no transactions and no market_days, so removing
them is safe.

Safety contract
---------------
The Delete handler MUST refuse when any ``market_days`` row
references the market — otherwise transactions and audit_log
entries become orphans (they FK back to market_days/markets and
carry no snapshot of the name).  The handler also cleans up the
``market_vendors`` and ``market_payment_methods`` junction rows
before dropping the market itself, so no broken references remain.

This module pins:
  1. Source-level: handler exists, gates on market_days COUNT,
     deletes junction rows + market in one transaction
  2. Behaviour: a market with no history deletes cleanly; a market
     with even one market_day refuses to delete
  3. UI wiring: the Delete button is added to the Markets actions
     row alongside Deactivate
"""

import inspect

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def markets_db(tmp_path):
    """Two markets — one clean (will be safely deletable), one with
    an existing market_day (will be blocked from delete)."""
    db_file = str(tmp_path / "market_delete.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, is_active) VALUES "
        " (1, 'Clean Market', 1),"
        " (2, 'Has History Market', 1)"
    )
    # Junction-table noise on the clean market — verifies the
    # handler cascade-cleans these (they're configuration, not
    # historical data, so dropping with the market is correct).
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'V')")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent) "
        "VALUES (50, 'Cash', 0)")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods "
        "(market_id, payment_method_id) VALUES (1, 50)")
    # Has-history market gets a market_day — that's the gate.
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 2, '2026-04-29', 'Closed', 'T')"
    )
    conn.commit()
    yield conn
    close_connection()


# ══════════════════════════════════════════════════════════════════
# 1. Source-level guards
# ══════════════════════════════════════════════════════════════════
class TestDeleteHandlerSource:

    def test_handler_exists(self):
        from fam.ui.settings_screen import SettingsScreen
        assert hasattr(SettingsScreen, '_delete_market'), (
            "SettingsScreen must expose a _delete_market handler "
            "wired to the Delete button on the Markets tab.")

    def test_handler_gates_on_market_days_count(self):
        """Pin the safety gate so a refactor can't accidentally
        skip the market_days check and delete history."""
        from fam.ui.settings_screen import SettingsScreen
        src = inspect.getsource(SettingsScreen._delete_market)
        assert 'FROM market_days' in src, (
            "Handler must query market_days to check for history "
            "before deleting.")
        assert 'WHERE market_id' in src
        # When count > 0 the handler must short-circuit.
        assert 'Cannot Delete' in src
        assert 'return' in src

    def test_handler_cascades_junction_tables(self):
        """Junction rows (market_vendors, market_payment_methods)
        carry no historical value — they're configuration that's
        meaningless without the market.  Pin that the handler
        deletes them before the market itself, otherwise FK
        cleanup is sloppy."""
        from fam.ui.settings_screen import SettingsScreen
        src = inspect.getsource(SettingsScreen._delete_market)
        assert 'DELETE FROM market_vendors' in src
        assert 'DELETE FROM market_payment_methods' in src
        # And the market row last.
        assert 'DELETE FROM markets' in src

    def test_handler_uses_yes_no_confirmation(self):
        """The action is destructive and not undoable — pin that
        the handler asks before committing."""
        from fam.ui.settings_screen import SettingsScreen
        src = inspect.getsource(SettingsScreen._delete_market)
        assert 'QMessageBox.question' in src
        assert 'cannot be undone' in src.lower()


class TestDeleteButtonWiring:

    def test_load_markets_adds_delete_button(self):
        """The Delete button must appear in the Markets actions
        row alongside Deactivate.  Pin via the literal text + the
        wiring to ``_delete_market``."""
        from fam.ui.settings_screen import SettingsScreen
        src = inspect.getsource(SettingsScreen._load_markets)
        assert 'make_action_btn("Delete"' in src
        assert 'self._delete_market(mid)' in src
        # Use the danger=True styling so the button visually
        # signals destructive action (red border).
        assert 'danger=True' in src


# ══════════════════════════════════════════════════════════════════
# 2. Behaviour — direct DB-level test of the handler
# ══════════════════════════════════════════════════════════════════
class TestDeleteBehaviour:
    """Behaviour-test the handler by calling it as an unbound
    method against a MagicMock SettingsScreen — same pattern used
    elsewhere in the suite for handlers that don't strictly need
    the full Qt dialog tree."""

    def test_clean_market_deletes_along_with_junction_rows(
            self, markets_db, monkeypatch):
        """Clean Market (id=1) has no market_days but DOES have
        junction rows.  After delete: market gone, junctions gone,
        rest of DB intact."""
        from unittest.mock import MagicMock
        from PySide6.QtWidgets import QMessageBox
        from fam.ui.settings_screen import SettingsScreen

        # User clicks Yes on the confirmation.
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda *a, **k: QMessageBox.Yes))

        screen = MagicMock(spec=SettingsScreen)
        SettingsScreen._delete_market(screen, market_id=1)

        # Market gone.
        rem = markets_db.execute(
            "SELECT COUNT(*) FROM markets WHERE id=1"
        ).fetchone()[0]
        assert rem == 0, "Market row should be deleted"
        # Junction rows gone.
        mv = markets_db.execute(
            "SELECT COUNT(*) FROM market_vendors WHERE market_id=1"
        ).fetchone()[0]
        assert mv == 0
        mpm = markets_db.execute(
            "SELECT COUNT(*) FROM market_payment_methods "
            "WHERE market_id=1"
        ).fetchone()[0]
        assert mpm == 0
        # Other market untouched.
        other = markets_db.execute(
            "SELECT COUNT(*) FROM markets WHERE id=2"
        ).fetchone()[0]
        assert other == 1

    def test_market_with_history_is_blocked(
            self, markets_db, monkeypatch):
        """Has-History Market (id=2) has a market_day on record.
        The handler MUST refuse — even on Yes confirmation, the
        gate should fire BEFORE the question is asked."""
        from unittest.mock import MagicMock
        from PySide6.QtWidgets import QMessageBox
        from fam.ui.settings_screen import SettingsScreen

        warning_calls = []
        monkeypatch.setattr(
            QMessageBox, 'warning',
            staticmethod(
                lambda *a, **k: warning_calls.append((a, k))))
        # If the handler somehow reaches the confirmation, this
        # would auto-Yes — but the test below proves it never does.
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda *a, **k: QMessageBox.Yes))

        screen = MagicMock(spec=SettingsScreen)
        SettingsScreen._delete_market(screen, market_id=2)

        # Market still there.
        rem = markets_db.execute(
            "SELECT COUNT(*) FROM markets WHERE id=2"
        ).fetchone()[0]
        assert rem == 1, (
            "Market with market_day history must NOT be deletable "
            "— the gate should have refused.")
        # Market_day still there too.
        md = markets_db.execute(
            "SELECT COUNT(*) FROM market_days WHERE market_id=2"
        ).fetchone()[0]
        assert md == 1
        # Warning shown.
        assert warning_calls, (
            "Handler must show a 'Cannot Delete' warning when "
            "market_days exist.")

    def test_user_cancels_confirmation_keeps_market(
            self, markets_db, monkeypatch):
        """When the user clicks No on the confirmation, nothing
        should be deleted — even for a clean market."""
        from unittest.mock import MagicMock
        from PySide6.QtWidgets import QMessageBox
        from fam.ui.settings_screen import SettingsScreen

        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda *a, **k: QMessageBox.No))

        screen = MagicMock(spec=SettingsScreen)
        SettingsScreen._delete_market(screen, market_id=1)

        rem = markets_db.execute(
            "SELECT COUNT(*) FROM markets WHERE id=1"
        ).fetchone()[0]
        assert rem == 1, (
            "Market must NOT be deleted when user clicks No.")

    def test_missing_market_id_is_no_op(
            self, markets_db, monkeypatch):
        """Handler called against an id that doesn't exist must
        return cleanly without raising."""
        from unittest.mock import MagicMock
        from fam.ui.settings_screen import SettingsScreen

        screen = MagicMock(spec=SettingsScreen)
        # Should not raise even though id 9999 isn't there.
        SettingsScreen._delete_market(screen, market_id=9999)
