"""FMNP Check Tracking page filter enhancements
(v2.0.7+, user-reported 2026-05-07).

User feedback: "on the FMNP Check Tracking page can we add a date
span search filter like the other reporting pages, and on the
market drop down default to All market days as the top choice
instead of requiring the user to iterate through all previous
market days."

Two enhancements pinned here:

  1. ``get_fmnp_entries`` accepts ``date_from`` / ``date_to``
     filters (ISO ``yyyy-MM-dd``) targeting the market day's
     calendar date.  Mirrors the date-range filter pattern used
     on Reports + Adjustments screens.
  2. The market dropdown surfaces "All Market Days" as the FIRST
     option (sentinel ``userData=None``) and defaults to it on
     first load.  When selected, the table aggregates entries
     across ALL market days; the form's Save button is disabled
     until the volunteer picks a specific market day to attribute
     a new entry to.

Together these let coordinators search the full FMNP entry
history at a glance — no scrolling through dozens of historical
market days to find one entry — while keeping new-entry
attribution explicit.
"""

import pytest

from fam.database.connection import (
    set_db_path, close_connection, get_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def fmnp_db_three_market_days(tmp_path):
    """DB with 3 market days spanning 3 distinct dates and 1
    FMNP entry per day, so date-range and market-day filters
    have non-trivial inputs."""
    from fam.models.fmnp import create_fmnp_entry
    db_file = str(tmp_path / "fmnp_filters.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES "
        "(1, 'Vendor A'), (2, 'Vendor B')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1), (1, 2)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES "
        "(10, 1, '2099-04-01', 'Closed', 'Tester'), "
        "(11, 1, '2099-04-15', 'Closed', 'Tester'), "
        "(12, 1, '2099-05-01', 'Open', 'Tester')")
    conn.commit()
    create_fmnp_entry(market_day_id=10, vendor_id=1, amount=500,
                      entered_by='T1')
    create_fmnp_entry(market_day_id=11, vendor_id=2, amount=1000,
                      entered_by='T2')
    create_fmnp_entry(market_day_id=12, vendor_id=1, amount=1500,
                      entered_by='T3')
    yield conn
    close_connection()


# ──────────────────────────────────────────────────────────────────
# Model layer: get_fmnp_entries date filter
# ──────────────────────────────────────────────────────────────────


class TestGetFmnpEntriesDateFilter:

    def test_no_filter_returns_all_active_entries(
            self, fmnp_db_three_market_days):
        from fam.models.fmnp import get_fmnp_entries
        rows = get_fmnp_entries(active_only=True)
        assert len(rows) == 3
        # Sorted by market day date DESC.
        dates = [r['market_day_date'] for r in rows]
        assert dates == ['2099-05-01', '2099-04-15', '2099-04-01']

    def test_market_day_id_still_works(
            self, fmnp_db_three_market_days):
        """Backward compat: passing a single market_day_id
        narrows to that day."""
        from fam.models.fmnp import get_fmnp_entries
        rows = get_fmnp_entries(market_day_id=11)
        assert len(rows) == 1
        assert rows[0]['market_day_date'] == '2099-04-15'

    def test_date_from_inclusive(
            self, fmnp_db_three_market_days):
        from fam.models.fmnp import get_fmnp_entries
        rows = get_fmnp_entries(date_from='2099-04-15')
        # Excludes the 2099-04-01 entry; includes 04-15 and 05-01.
        dates = [r['market_day_date'] for r in rows]
        assert set(dates) == {'2099-04-15', '2099-05-01'}

    def test_date_to_inclusive(
            self, fmnp_db_three_market_days):
        from fam.models.fmnp import get_fmnp_entries
        rows = get_fmnp_entries(date_to='2099-04-15')
        # Excludes the 2099-05-01 entry; includes 04-01 and 04-15.
        dates = [r['market_day_date'] for r in rows]
        assert set(dates) == {'2099-04-01', '2099-04-15'}

    def test_date_range_both_bounds(
            self, fmnp_db_three_market_days):
        from fam.models.fmnp import get_fmnp_entries
        rows = get_fmnp_entries(
            date_from='2099-04-10', date_to='2099-04-30')
        # Only the middle entry survives both bounds.
        assert len(rows) == 1
        assert rows[0]['market_day_date'] == '2099-04-15'

    def test_date_range_combined_with_market_day_id(
            self, fmnp_db_three_market_days):
        """When BOTH market_day_id and date_range are set, the
        intersection wins.  This shouldn't change behavior in
        practice (a specific market_day_id implies a specific
        date) but the SQL must handle the combination cleanly."""
        from fam.models.fmnp import get_fmnp_entries
        rows = get_fmnp_entries(
            market_day_id=11, date_from='2099-04-10',
            date_to='2099-04-30')
        assert len(rows) == 1
        assert rows[0]['market_day_date'] == '2099-04-15'

    def test_date_range_no_match_returns_empty(
            self, fmnp_db_three_market_days):
        from fam.models.fmnp import get_fmnp_entries
        rows = get_fmnp_entries(
            date_from='2099-12-01', date_to='2099-12-31')
        assert rows == []


# ──────────────────────────────────────────────────────────────────
# UI layer: All Market Days dropdown + date filter wiring
# ──────────────────────────────────────────────────────────────────


class TestFmnpScreenAllMarketDaysDefault:

    def test_dropdown_first_option_is_all_market_days(
            self, qtbot, fmnp_db_three_market_days):
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        # First item is the "All Market Days" sentinel.
        assert screen.md_combo.itemText(0) == "All Market Days"
        assert screen.md_combo.itemData(0) is None
        # And it's selected on first load.
        assert screen.md_combo.currentIndex() == 0
        assert screen.md_combo.currentData() is None, (
            "Default selection must be 'All Market Days' so the "
            "volunteer sees every entry without scrolling.")

    def test_save_button_disabled_when_all_market_days_selected(
            self, qtbot, fmnp_db_three_market_days):
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        # All Market Days selected by default → Save disabled.
        assert not screen.save_btn.isEnabled(), (
            "Save button must be disabled when 'All Market Days' "
            "is selected — you can't attribute a new entry to "
            "'all markets'.")
        # Tooltip explains the constraint.
        tooltip = screen.save_btn.toolTip()
        assert 'specific market day' in tooltip.lower()

    def test_save_button_enabled_when_specific_day_selected(
            self, qtbot, fmnp_db_three_market_days):
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        # Pick a real market day from the dropdown (skip
        # "All Market Days" at index 0).
        screen.md_combo.setCurrentIndex(1)
        assert screen.save_btn.isEnabled()
        assert screen.save_btn.toolTip() == ""

    def test_pick_md_hint_label_visible_when_disabled(
            self, qtbot, fmnp_db_three_market_days):
        """v2.0.7+ (user-reported 2026-05-07): when 'All Market
        Days' is selected and Save is greyed out, an inline
        hint label appears next to the button explaining the
        constraint.  Tooltips alone aren't discoverable; the
        visible hint prevents volunteers from getting stuck
        wondering why Save doesn't work."""
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        assert hasattr(screen, 'pick_md_hint_label'), (
            "FMNPScreen must expose a pick_md_hint_label widget "
            "so the disabled-Save state has a visible "
            "explanation, not just a tooltip.")
        # Default state: All Market Days selected → hint visible.
        # We check isHidden() because the screen isn't shown via
        # show() in the test, so isVisible() returns False even
        # for a "visible" widget.  isHidden()=False means it
        # would appear if the parent were shown.
        assert not screen.pick_md_hint_label.isHidden(), (
            "Hint label must be NOT hidden when Save is "
            "disabled (All Market Days selected).")
        # Hint text mentions picking a market day.
        text = screen.pick_md_hint_label.text().lower()
        assert 'market day' in text
        assert 'pick' in text or 'select' in text

    def test_pick_md_hint_label_hidden_when_save_enabled(
            self, qtbot, fmnp_db_three_market_days):
        """When the volunteer picks a specific market day, the
        Save button becomes enabled and the inline hint label
        hides — no visual clutter once the form is usable."""
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        # Pick a real market day.
        screen.md_combo.setCurrentIndex(1)
        assert screen.save_btn.isEnabled()
        assert screen.pick_md_hint_label.isHidden(), (
            "Hint label must hide once a specific market day "
            "is selected (Save is enabled, no explanation "
            "needed).")

    def test_table_shows_entries_from_all_market_days_in_default(
            self, qtbot, fmnp_db_three_market_days):
        """Default state: All Market Days selected.  The table
        must show entries from every market day."""
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        # 3 entries created in fixture → table should show all 3.
        assert screen.table.rowCount() == 3

    def test_table_filters_to_specific_market_when_selected(
            self, qtbot, fmnp_db_three_market_days):
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        # Find and select market day 11 (the one with 1 entry).
        for i in range(screen.md_combo.count()):
            if screen.md_combo.itemData(i) == 11:
                screen.md_combo.setCurrentIndex(i)
                break
        assert screen.table.rowCount() == 1


class TestFmnpScreenDateRangeFilter:

    def test_date_range_widget_is_present(
            self, qtbot, fmnp_db_three_market_days):
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        assert hasattr(screen, 'date_range'), (
            "FMNP screen must expose a date_range filter widget "
            "matching the Reports + Adjustments pattern.")

    def test_table_has_market_day_column(
            self, qtbot, fmnp_db_three_market_days):
        """When 'All Market Days' is selected, the table mixes
        rows from multiple markets — it MUST have a Market Day
        column so the volunteer can identify each row."""
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        headers = [
            screen.table.horizontalHeaderItem(i).text()
            for i in range(screen.table.columnCount())]
        assert 'Market Day' in headers, (
            f"FMNP table must include a Market Day column.  "
            f"Got: {headers}")
