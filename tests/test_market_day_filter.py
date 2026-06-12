"""Tests for the Reports Market Day shortcut filter (v2.1.0, ENH-005).

Bryan's post-market-closure exercise: coordinators reconcile per
market day ("show me last Tuesday"), but the only date control was
a from/to range — pinning both ends to one date was clumsy and
ambiguous when two markets ran on the same date.  Pinned behavior:

  1. A "Market Day" dropdown sits in the shared filter bar with an
     "All Market Days" default; entries are "<date> — <market>"
     (most recent first) so same-date markets are unambiguous.
  2. Selecting a day constrains every report to exactly that day's
     market day (md.id, not just the date).
  3. The selection OVERRIDES the date range: the range widget is
     disabled while a day is selected, and the date clause is
     skipped (a stale range can't blank out the day's data).
  4. "All Market Days" re-enables the date range.
  5. The selection survives a filter repopulation (refresh()).
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def md_filter_db(tmp_path):
    db_file = str(tmp_path / "md_filter.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES "
        " (1, 'Bellevue', 100000, 1), (2, 'Petoskey', 100000, 1)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V1')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1), (2, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (1, 'SNAP', 100.0, 1, 1, NULL)")
    # Two markets share 2026-04-30 (the ambiguity ENH-005 calls out)
    # plus a second Bellevue day a week later.
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES "
        " (1, 1, '2026-04-30', 'Open', 'T'), "
        " (2, 1, '2026-05-07', 'Open', 'T'), "
        " (3, 2, '2026-04-30', 'Open', 'T')")
    conn.commit()
    yield conn
    close_connection()


def _seed_snap_order(conn, *, order_id, market_day_id, customer):
    conn.execute(
        "INSERT INTO customer_orders (id, market_day_id, "
        " customer_label, status) VALUES (?, ?, ?, 'Confirmed')",
        (order_id, market_day_id, customer))
    tid = order_id * 10
    conn.execute(
        "INSERT INTO transactions (id, fam_transaction_id, "
        " market_day_id, vendor_id, customer_order_id, "
        " receipt_total, status, confirmed_at) "
        "VALUES (?, ?, ?, 1, ?, 200, 'Confirmed', "
        " '2026-04-30 10:00:00')",
        (tid, f'T-{tid}', market_day_id, order_id))
    conn.execute(
        "INSERT INTO payment_line_items (transaction_id, "
        " payment_method_id, method_name_snapshot, "
        " match_percent_snapshot, method_amount, customer_charged, "
        " match_amount) VALUES (?, 1, 'SNAP', 100.0, 200, 100, 100)",
        (tid,))
    conn.commit()


def _seed_all_three_days(conn):
    _seed_snap_order(conn, order_id=10, market_day_id=1,
                     customer='C-MD1')
    _seed_snap_order(conn, order_id=20, market_day_id=2,
                     customer='C-MD2')
    _seed_snap_order(conn, order_id=30, market_day_id=3,
                     customer='C-MD3')


def _make_screen(qtbot):
    from fam.ui.reports_screen import ReportsScreen
    screen = ReportsScreen()
    qtbot.addWidget(screen)
    return screen


def _select_day(screen, md_id):
    idx = screen.market_day_combo.findData(md_id)
    assert idx >= 0, f"market day {md_id} not in the dropdown"
    screen.market_day_combo.setCurrentIndex(idx)


def _settlement_customers(screen):
    # Customer is column 2 (Verified moved to column 0, v2.1.0).
    t = screen.settlement_table
    return {t.item(r, 2).text() for r in range(t.rowCount())}


class TestDropdown:

    def test_default_is_all_market_days(self, qtbot, md_filter_db):
        screen = _make_screen(qtbot)
        assert screen.market_day_combo.currentText() == "All Market Days"
        assert screen.market_day_combo.currentData() is None
        assert screen.date_range.isEnabled()

    def test_entries_carry_date_and_market_name(
            self, qtbot, md_filter_db):
        """Two markets on the same date must be distinguishable."""
        screen = _make_screen(qtbot)
        labels = [screen.market_day_combo.itemText(i)
                  for i in range(screen.market_day_combo.count())]
        assert "2026-04-30 — Bellevue" in labels
        assert "2026-04-30 — Petoskey" in labels

    def test_selection_survives_refresh(self, qtbot, md_filter_db):
        screen = _make_screen(qtbot)
        _select_day(screen, 2)
        screen.refresh()   # repopulates the dropdowns
        assert screen.market_day_combo.currentData() == 2


class TestDayConstrainsReports:

    def test_settlement_tab(self, qtbot, md_filter_db):
        _seed_all_three_days(md_filter_db)
        screen = _make_screen(qtbot)
        _select_day(screen, 1)
        assert _settlement_customers(screen) == {'C-MD1'}

    def test_same_date_other_market_excluded(
            self, qtbot, md_filter_db):
        """md.id filtering, not date filtering: Petoskey ran the
        same date but must not appear."""
        _seed_all_three_days(md_filter_db)
        screen = _make_screen(qtbot)
        _select_day(screen, 3)
        assert _settlement_customers(screen) == {'C-MD3'}

    def test_detailed_ledger_tab(self, qtbot, md_filter_db):
        _seed_all_three_days(md_filter_db)
        screen = _make_screen(qtbot)
        assert screen.ledger_table.rowCount() == 3
        _select_day(screen, 2)
        assert screen.ledger_table.rowCount() == 1

    def test_generated_rewards_tab(self, qtbot, md_filter_db):
        _seed_all_three_days(md_filter_db)
        for order_id, md in [(10, 1), (20, 2)]:
            md_filter_db.execute(
                "INSERT INTO generated_rewards (customer_order_id, "
                " market_day_id, source_method_name_snapshot, "
                " source_total_cents, threshold_cents, "
                " reward_method_name_snapshot, reward_unit_cents, "
                " n_units, reward_total_cents, generated_by) "
                "VALUES (?, ?, 'SNAP', 200, 100, 'Bucks', 200, 1, "
                " 200, 'T')", (order_id, md))
        md_filter_db.commit()
        screen = _make_screen(qtbot)
        assert screen.rewards_table.rowCount() == 2
        _select_day(screen, 1)
        assert screen.rewards_table.rowCount() == 1


class TestLastTouchedWins:
    """Sean field feedback 2026-06-12: the original greyed-out-range
    design left both filters showing values at once (a range set
    BEFORE picking a day kept displaying, and verification mode
    survived an attempted range change).  New contract: whichever
    filter was touched LAST wins, and the other one visibly resets
    — the two can never appear set simultaneously."""

    @staticmethod
    def _apply_range(screen, from_str, to_str):
        """Simulate the date-range dialog applying a range."""
        from PySide6.QtCore import QDate
        screen.date_range._from_date = QDate.fromString(
            from_str, "yyyy-MM-dd")
        screen.date_range._to_date = QDate.fromString(
            to_str, "yyyy-MM-dd")
        screen.date_range._active = True
        screen.date_range.range_changed.emit()

    def test_picking_day_clears_range(self, qtbot, md_filter_db):
        screen = _make_screen(qtbot)
        self._apply_range(screen, '2026-04-01', '2026-05-31')
        assert screen.date_range.get_date_range() != (None, None)
        _select_day(screen, 1)
        assert screen.date_range.get_date_range() == (None, None), (
            "picking a day must reset the range display to All "
            "Dates — no both-set state")

    def test_applying_range_exits_day_mode(self, qtbot, md_filter_db):
        _seed_all_three_days(md_filter_db)
        screen = _make_screen(qtbot)
        _select_day(screen, 1)
        assert _settlement_customers(screen) == {'C-MD1'}
        self._apply_range(screen, '2026-04-01', '2026-05-31')
        assert screen.market_day_combo.currentData() is None, (
            "applying a date range must reset Market Day to All")
        # The range now governs: all three days fall inside it.
        assert _settlement_customers(screen) == {
            'C-MD1', 'C-MD2', 'C-MD3'}

    def test_month_quick_pick_returns_month_bounds(
            self, qtbot, md_filter_db):
        """The date dialog's whole-month mode (Bryan QoL,
        2026-06-12): either/or with the custom range; choosing a
        month applies its first..last day as an ordinary range."""
        from PySide6.QtCore import QDate
        from fam.ui.helpers import _DateRangeDialog
        dlg = _DateRangeDialog(
            QDate(2026, 4, 30), QDate(2026, 5, 7),
            QDate(2026, 4, 30), QDate(2026, 5, 7))
        qtbot.addWidget(dlg)
        # WHOLE MONTH opens as the active default (Sean,
        # 2026-06-12 — custom-first "felt inverted"); the custom
        # side is faded but EVERY control stays live — a
        # disabled-until-radio combo proved undiscoverable in the
        # field ("the dropdown isn't clickable").
        assert dlg.month_mode.isChecked()
        assert dlg.start_month.isEnabled()
        assert '#9A9A9A' not in dlg.month_combo.styleSheet()
        assert '#9A9A9A' in dlg.start_month.styleSheet(), (
            "inactive custom-range side must be visually faded")
        # Months listed newest-first across the data bounds.
        labels = [dlg.month_combo.itemText(i)
                  for i in range(dlg.month_combo.count())]
        assert labels == ['May 2026', 'April 2026']
        idx = dlg.month_combo.findData('2026-04')
        assert idx >= 0
        dlg.month_combo.setCurrentIndex(idx)
        dlg.month_combo.activated.emit(idx)
        assert dlg.selected_from() == QDate(2026, 4, 1)
        assert dlg.selected_to() == QDate(2026, 4, 30)
        # Touching a custom-range field flips to range mode — one
        # or the other, per the requirement — and the fade swaps.
        dlg.start_day.valueChanged.emit(dlg.start_day.value())
        assert dlg.range_mode.isChecked()
        assert '#9A9A9A' in dlg.month_combo.styleSheet(), (
            "inactive month side must be visually faded")
        assert '#9A9A9A' not in dlg.start_month.styleSheet()

    def test_dialog_opens_reflecting_active_selection(
            self, qtbot, md_filter_db):
        """Honesty exception to the month-first default: an ACTIVE
        custom range opens in custom mode; an active range that is
        exactly a whole month opens in month mode with that month
        preselected."""
        from PySide6.QtCore import QDate
        from fam.ui.helpers import _DateRangeDialog
        # Active non-month range → custom mode.
        dlg = _DateRangeDialog(
            QDate(2026, 4, 30), QDate(2026, 5, 7),
            QDate(2026, 4, 1), QDate(2026, 5, 31),
            range_active=True)
        qtbot.addWidget(dlg)
        assert dlg.range_mode.isChecked()
        # Active whole-month range → month mode, month preselected.
        dlg2 = _DateRangeDialog(
            QDate(2026, 4, 1), QDate(2026, 4, 30),
            QDate(2026, 4, 1), QDate(2026, 5, 31),
            range_active=True)
        qtbot.addWidget(dlg2)
        assert dlg2.month_mode.isChecked()
        assert dlg2.month_combo.currentData() == '2026-04'

    def test_day_wins_over_stale_range(self, qtbot, md_filter_db):
        """Safety net in the where-builders: even if a range value
        somehow coexists with a selected day (e.g. monkeypatched
        here), the day's md.id clause governs and the date clause
        is skipped — a stale range can never blank the day."""
        _seed_all_three_days(md_filter_db)
        screen = _make_screen(qtbot)
        screen.date_range.get_date_range = (
            lambda: ('2030-01-01', '2030-01-02'))   # matches nothing
        _select_day(screen, 1)
        assert _settlement_customers(screen) == {'C-MD1'}
        assert screen.ledger_table.rowCount() == 1
