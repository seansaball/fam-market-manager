"""Pin the Market Location dropdown sync on the Market Day screen.

The 2026-04-29 onsite caught this bug: a coordinator opens "Test
Market" but the Market Location dropdown above the Open button still
shows whatever they last clicked (e.g. "Bellevue Farmers Market").
The dropdown is grayed out (disabled while a day is open), but the
*selected text* is wrong — directly contradicting the "Active: Test
Market" status header below.

The fix: ``_update_status`` syncs the combo to the open market's
``market_id`` whenever there's an open market day.  This module
pins the contract at the source level (cheap) and verifies the
behaviour end-to-end with qtbot (so the wiring is honest).
"""

import inspect

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def two_market_db(tmp_path):
    """Two markets, one of which has an open market day.  Forces a
    discrepancy: the combo's first-loaded item ("Bellevue") is NOT
    the same as the open market ("Test Market")."""
    db_file = str(tmp_path / "dropdown_sync.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    # IDs chosen so the alphabetical order (Bellevue first) doesn't
    # match the open market — exposing whether the sync logic
    # actually runs vs accidentally picking the right one.
    conn.execute(
        "INSERT INTO markets (id, name, is_active) VALUES "
        " (1, 'Bellevue Farmers Market', 1),"
        " (2, 'Test Market', 1)"
    )
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 2, '2026-04-29', 'Open', 'Tester')"
    )
    conn.commit()
    yield conn
    close_connection()


# ══════════════════════════════════════════════════════════════════
# Source-level guard — the fix must stay wired
# ══════════════════════════════════════════════════════════════════
class TestUpdateStatusSyncsCombo:

    def test_update_status_seeks_combo_to_open_market_id(self):
        """Pin the iteration that finds the matching combo entry
        and calls ``setCurrentIndex``.  Without this, the combo
        sits on whatever the coordinator last clicked — which
        regressed the 2026-04-29 onsite scenario."""
        from fam.ui.market_day_screen import MarketDayScreen
        src = inspect.getsource(MarketDayScreen._update_status)
        assert "open_md['market_id']" in src, (
            "_update_status must read open_md['market_id'] to find "
            "the matching combo entry — without this the dropdown "
            "stays on a stale selection.")
        assert 'self.market_combo.setCurrentIndex' in src, (
            "_update_status must call setCurrentIndex on the "
            "Market Location combo so the displayed text matches "
            "the actually-open market.")


# ══════════════════════════════════════════════════════════════════
# Behaviour — the dropdown actually shows the open market
# ══════════════════════════════════════════════════════════════════
class TestDropdownShowsOpenMarket:

    def test_open_market_selected_in_combo_after_refresh(
            self, qtbot, two_market_db):
        """End-to-end: build the screen, refresh, verify the combo's
        currentData matches the open market's id (Test Market = id
        2), NOT the alphabetically-first item (Bellevue = id 1)."""
        from fam.ui.market_day_screen import MarketDayScreen
        screen = MarketDayScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        assert screen.market_combo.currentData() == 2, (
            f"Market Location combo must show the open market "
            f"(Test Market, id=2) after refresh.  Got "
            f"id={screen.market_combo.currentData()} / "
            f"text={screen.market_combo.currentText()!r}.")

    def test_combo_is_disabled_while_market_day_open(
            self, qtbot, two_market_db):
        """The sync only matters if the combo is also locked —
        otherwise a coordinator could click and reopen a different
        market while the current one is still open.  Pin both."""
        from fam.ui.market_day_screen import MarketDayScreen
        screen = MarketDayScreen()
        qtbot.addWidget(screen)
        screen.refresh()
        assert not screen.market_combo.isEnabled()

    def test_combo_re_enables_when_no_market_open(
            self, qtbot, tmp_path):
        """After closing the active market day, the combo must
        re-enable so the next market can be chosen."""
        from fam.ui.market_day_screen import MarketDayScreen
        # Build a DB with two markets but NO open market day.
        db_file = str(tmp_path / "no_open_md.db")
        close_connection()
        set_db_path(db_file)
        initialize_database()
        conn = get_connection()
        conn.execute(
            "INSERT INTO markets (id, name, is_active) VALUES "
            " (1, 'Bellevue Farmers Market', 1),"
            " (2, 'Test Market', 1)"
        )
        conn.commit()

        screen = MarketDayScreen()
        qtbot.addWidget(screen)
        screen.refresh()
        assert screen.market_combo.isEnabled()

        close_connection()


# ══════════════════════════════════════════════════════════════════
# Lifecycle — close-and-reopen cycle integrity
# ══════════════════════════════════════════════════════════════════
class TestCloseAndReopenLifecycle:
    """Audit follow-up: the basic sync test verifies the combo
    lands on the open market when one exists, but doesn't exercise
    the full close-then-reopen cycle.  These tests pin that the
    combo stays consistent across status transitions."""

    def test_close_then_reopen_different_market(
            self, qtbot, two_market_db):
        """Open Test Market → close it → open Bellevue.  After each
        transition, refresh() must leave the combo on the
        currently-active market (or unlocked if none is open)."""
        from fam.ui.market_day_screen import MarketDayScreen
        screen = MarketDayScreen()
        qtbot.addWidget(screen)
        screen.refresh()
        # Initially: Test Market (id 2) is open.
        assert screen.market_combo.currentData() == 2
        assert not screen.market_combo.isEnabled()

        # Close Test Market.
        conn = get_connection()
        conn.execute(
            "UPDATE market_days SET status='Closed' WHERE id=1")
        conn.commit()
        screen.refresh()
        # Combo should re-enable; selection is now whatever the
        # combo defaulted to (don't pin a specific id, just the
        # state).
        assert screen.market_combo.isEnabled()

        # Open Bellevue (id 1) — simulate by inserting a new
        # market_day with status='Open' for market_id=1.
        conn.execute(
            "INSERT INTO market_days (id, market_id, date, status, "
            " opened_by) VALUES (2, 1, '2026-04-30', 'Open', 'Tester')"
        )
        conn.commit()
        screen.refresh()
        # Combo must seek to Bellevue (id 1) and lock.
        assert screen.market_combo.currentData() == 1, (
            f"After reopening Bellevue, combo must seek to id=1; "
            f"got {screen.market_combo.currentData()}")
        assert not screen.market_combo.isEnabled()

    def test_no_markets_at_all(self, qtbot, tmp_path):
        """Edge case: no markets defined yet.  The combo must not
        crash on refresh and the screen must remain usable."""
        from fam.ui.market_day_screen import MarketDayScreen
        db_file = str(tmp_path / "no_markets.db")
        close_connection()
        set_db_path(db_file)
        initialize_database()

        screen = MarketDayScreen()
        qtbot.addWidget(screen)
        # Must not raise; combo should be empty + enabled (no
        # active day, nothing to lock against).
        screen.refresh()
        assert screen.market_combo.count() == 0
        assert screen.market_combo.isEnabled()

        close_connection()
