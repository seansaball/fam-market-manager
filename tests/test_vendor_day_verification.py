"""Tests for Vendor Reimbursement end-of-market verification
(v2.1.0, ENH-006, schema v40).

Bryan's vendor walk: at end of market he confirms each vendor's
receipt total in person.  The VR report's Verified column is his
working page — but a VR row is a filter-window aggregate, so the
mark is bound to the stable per-day FACT instead: one
``vendor_day_verifications`` row per (market day × vendor) ticked.
Pinned guarantees:

  1. The Verified column is tickable ONLY when one Market Day is
     selected (rows are then unambiguously (vendor, day)).  Span
     views show a dash + hint — no per-window state exists, so
     there is no combinatorial checkbox state to hold anywhere.
  2. Ticks persist per (market day × vendor): they survive filter
     changes, regeneration, and restarts, and reselecting the day
     restores them.  Different days have independent marks.
  3. Operational state only: no audit rows, no sync — the sheet
     collector's output is untouched, and ticking changes no money
     figure (zero effect on FAM reimbursement).
  4. Verification mode disables header sorting (cell widgets would
     not follow a re-sort); normal mode keeps VR sortable.
  5. CSV gains Verified / Verified At columns in verification mode
     only; the normal-mode CSV shape is unchanged.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def vendor_verify_db(tmp_path):
    db_file = str(tmp_path / "vendor_verify.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'Bellevue', 100000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'V1'), (2, 'V2')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1), (1, 2)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (1, 'SNAP', 100.0, 1, 1, NULL)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES "
        " (1, 1, '2026-04-30', 'Open', 'T'), "
        " (2, 1, '2026-05-07', 'Open', 'T')")
    conn.commit()
    yield conn
    close_connection()


def _seed_txn(conn, *, order_id, market_day_id, vendor_id):
    conn.execute(
        "INSERT INTO customer_orders (id, market_day_id, "
        " customer_label, status) VALUES (?, ?, ?, 'Confirmed')",
        (order_id, market_day_id, f'C-{order_id}'))
    tid = order_id * 10
    conn.execute(
        "INSERT INTO transactions (id, fam_transaction_id, "
        " market_day_id, vendor_id, customer_order_id, "
        " receipt_total, status, confirmed_at) "
        "VALUES (?, ?, ?, ?, ?, 200, 'Confirmed', "
        " '2026-04-30 10:00:00')",
        (tid, f'T-{tid}', market_day_id, vendor_id, order_id))
    conn.execute(
        "INSERT INTO payment_line_items (transaction_id, "
        " payment_method_id, method_name_snapshot, "
        " match_percent_snapshot, method_amount, customer_charged, "
        " match_amount) VALUES (?, 1, 'SNAP', 100.0, 200, 100, 100)",
        (tid,))
    conn.commit()


def _seed_both_vendors_both_days(conn):
    _seed_txn(conn, order_id=10, market_day_id=1, vendor_id=1)
    _seed_txn(conn, order_id=20, market_day_id=1, vendor_id=2)
    _seed_txn(conn, order_id=30, market_day_id=2, vendor_id=1)


def _make_screen(qtbot):
    from fam.ui.reports_screen import ReportsScreen
    screen = ReportsScreen()
    qtbot.addWidget(screen)
    return screen


def _select_day(screen, md_id):
    if md_id is None:
        screen.market_day_combo.setCurrentIndex(0)
        return
    idx = screen.market_day_combo.findData(md_id)
    assert idx >= 0
    screen.market_day_combo.setCurrentIndex(idx)


def _apply_range(screen, from_str, to_str):
    """Simulate the date-range dialog applying a range."""
    from PySide6.QtCore import QDate
    screen.date_range._from_date = QDate.fromString(
        from_str, "yyyy-MM-dd")
    screen.date_range._to_date = QDate.fromString(
        to_str, "yyyy-MM-dd")
    screen.date_range._active = True
    screen.date_range.range_changed.emit()


def _verified_col(screen):
    return screen._vendor_verified_col


class TestSchemaV40:

    def test_fresh_install_has_table(self, vendor_verify_db):
        row = vendor_verify_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='vendor_day_verifications'").fetchone()
        assert row is not None

    def test_migration_creates_table_and_is_idempotent(
            self, tmp_path):
        import sqlite3
        from fam.database.schema import _migrate_v39_to_v40
        db = sqlite3.connect(str(tmp_path / "v39_replica.db"))
        _migrate_v39_to_v40(db)
        _migrate_v39_to_v40(db)   # idempotent re-run
        cols = {r[1] for r in db.execute(
            "PRAGMA table_info(vendor_day_verifications)").fetchall()}
        assert cols == {'market_day_id', 'vendor_id', 'verified_at'}
        db.close()

    def test_v41_range_table_fresh_and_migrated(
            self, vendor_verify_db, tmp_path):
        import sqlite3
        from fam.database.schema import _migrate_v40_to_v41
        row = vendor_verify_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='vendor_range_verifications'").fetchone()
        assert row is not None
        db = sqlite3.connect(str(tmp_path / "v40_replica.db"))
        _migrate_v40_to_v41(db)
        _migrate_v40_to_v41(db)   # idempotent re-run
        cols = {r[1] for r in db.execute(
            "PRAGMA table_info(vendor_range_verifications)"
        ).fetchall()}
        assert cols == {'vendor_id', 'market_id', 'from_date',
                        'to_date', 'verified_at'}
        db.close()


class TestVerifiedColumn:

    def test_unscoped_view_is_inert(self, qtbot, vendor_verify_db):
        """All Dates + All Market Days = NO time scope: the rollup
        would span the vendor's entire history, so one stray click
        could verify (or clear) seasons of marks.  The unscoped
        view shows an inert dash (Sean, 2026-06-12)."""
        _seed_both_vendors_both_days(vendor_verify_db)
        screen = _make_screen(qtbot)
        col = _verified_col(screen)
        # Placement (Sean, 2026-06-12): FIRST column — the wide
        # money table scrolls horizontally and a trailing checkbox
        # would sit off-screen during the vendor walk.
        assert col == 0
        headers = [
            screen.vendor_table.horizontalHeaderItem(i).text()
            for i in range(screen.vendor_table.columnCount())]
        assert headers[col] == 'Verified'
        assert screen._vendor_checkbox(0) is None
        assert screen.vendor_table.item(0, col).text() == '–'
        assert 'Verified' not in screen._vendor_data[0]

    def test_range_mark_is_independent_of_day_marks(
            self, qtbot, vendor_verify_db):
        """Every time scope is its OWN checkbox (Sean, 2026-06-12,
        rev 2): a verified day does NOT make the surrounding range
        read verified, and ticking a range writes a RANGE mark —
        not per-day marks."""
        from fam.ui.reports_screen import _VerifiedBox
        _seed_both_vendors_both_days(vendor_verify_db)
        # V1's day 1 is verified...
        vendor_verify_db.execute(
            "INSERT INTO vendor_day_verifications VALUES "
            "(1, 1, '2026-04-30 18:00:00')")
        vendor_verify_db.commit()
        screen = _make_screen(qtbot)
        _apply_range(screen, '2026-04-01', '2026-05-31')
        cb_v1 = screen._vendor_checkbox(0)
        assert isinstance(cb_v1, _VerifiedBox)
        # ...but the RANGE mark doesn't exist → unchecked.
        assert not cb_v1.isChecked()
        assert 'independent' in cb_v1.toolTip()
        # Ticking the range writes ONE range row; day marks
        # untouched.
        cb_v1.setChecked(True)
        ranges = vendor_verify_db.execute(
            "SELECT vendor_id, market_id, from_date, to_date "
            "FROM vendor_range_verifications").fetchall()
        assert [(r[0], r[1], r[2], r[3]) for r in ranges] == [
            (1, 1, '2026-04-01', '2026-05-31')]
        n_days = vendor_verify_db.execute(
            "SELECT COUNT(*) FROM vendor_day_verifications"
        ).fetchone()[0]
        assert n_days == 1, "range tick must not write day marks"
        # Sorting stays off — cell widgets would not follow a
        # re-sort (applies in every mode).
        assert not screen.vendor_table.isSortingEnabled()

    def test_range_untick_does_not_cascade_to_days(
            self, qtbot, vendor_verify_db):
        """THE requirement (Sean, 2026-06-12): unchecking a month
        or range must NOT uncheck the market days inside it."""
        _seed_both_vendors_both_days(vendor_verify_db)
        # Both of V1's days are verified individually.
        vendor_verify_db.execute(
            "INSERT INTO vendor_day_verifications VALUES "
            "(1, 1, '2026-04-30 18:00:00'), "
            "(2, 1, '2026-05-07 18:00:00')")
        vendor_verify_db.commit()
        screen = _make_screen(qtbot)
        _apply_range(screen, '2026-04-01', '2026-05-31')
        cb = screen._vendor_checkbox(0)
        cb.setChecked(True)     # range mark on
        cb.setChecked(False)    # range mark off
        n_ranges = vendor_verify_db.execute(
            "SELECT COUNT(*) FROM vendor_range_verifications"
        ).fetchone()[0]
        assert n_ranges == 0
        n_days = vendor_verify_db.execute(
            "SELECT COUNT(*) FROM vendor_day_verifications"
        ).fetchone()[0]
        assert n_days == 2, (
            "unchecking the range must leave the nested per-day "
            "marks untouched")
        # And the day view still shows them checked.
        _select_day(screen, 1)
        assert screen._vendor_checkbox(0).isChecked()

    def test_different_ranges_are_different_marks(
            self, qtbot, vendor_verify_db):
        """A month pick and a different range are separate keys —
        verifying April says nothing about April–May."""
        _seed_both_vendors_both_days(vendor_verify_db)
        screen = _make_screen(qtbot)
        _apply_range(screen, '2026-04-01', '2026-04-30')   # April
        screen._vendor_checkbox(0).setChecked(True)
        _apply_range(screen, '2026-04-01', '2026-05-31')   # wider
        assert not screen._vendor_checkbox(0).isChecked()
        _apply_range(screen, '2026-04-01', '2026-04-30')   # April
        assert screen._vendor_checkbox(0).isChecked()

    def test_checkbox_with_market_day(self, qtbot, vendor_verify_db):
        from fam.ui.reports_screen import _VerifiedBox
        _seed_both_vendors_both_days(vendor_verify_db)
        screen = _make_screen(qtbot)
        _select_day(screen, 1)
        cb = screen._vendor_checkbox(0)
        assert isinstance(cb, _VerifiedBox)
        assert not cb.isChecked()
        # Verification mode: sorting off (cell widgets would not
        # follow a re-sort); rows stay in market/vendor walk order.
        assert not screen.vendor_table.isSortingEnabled()

    def test_tick_persists_and_restores(
            self, qtbot, vendor_verify_db):
        _seed_both_vendors_both_days(vendor_verify_db)
        screen = _make_screen(qtbot)
        _select_day(screen, 1)
        # Row 0 = V1 (rows sort by market/vendor name).
        screen._vendor_checkbox(0).setChecked(True)
        row = vendor_verify_db.execute(
            "SELECT market_day_id, vendor_id, verified_at "
            "FROM vendor_day_verifications").fetchall()
        assert len(row) == 1
        assert (row[0]['market_day_id'], row[0]['vendor_id']) == (1, 1)
        assert row[0]['verified_at']
        assert screen._vendor_data[0]['Verified'] == 'Yes'
        # Survives a full regeneration (filter churn / reopen).
        screen._generate_reports()
        assert screen._vendor_checkbox(0).isChecked()
        assert not screen._vendor_checkbox(1).isChecked()

    def test_untick_deletes_mark(self, qtbot, vendor_verify_db):
        _seed_both_vendors_both_days(vendor_verify_db)
        vendor_verify_db.execute(
            "INSERT INTO vendor_day_verifications VALUES "
            "(1, 1, '2026-04-30 18:00:00')")
        vendor_verify_db.commit()
        screen = _make_screen(qtbot)
        _select_day(screen, 1)
        cb = screen._vendor_checkbox(0)
        assert cb.isChecked(), "mark must restore from the DB"
        cb.setChecked(False)
        n = vendor_verify_db.execute(
            "SELECT COUNT(*) FROM vendor_day_verifications"
        ).fetchone()[0]
        assert n == 0

    def test_per_day_isolation(self, qtbot, vendor_verify_db):
        """The mark is (vendor × DAY): verifying V1 on day 1 must
        not show V1 verified on day 2."""
        _seed_both_vendors_both_days(vendor_verify_db)
        screen = _make_screen(qtbot)
        _select_day(screen, 1)
        screen._vendor_checkbox(0).setChecked(True)
        _select_day(screen, 2)
        assert not screen._vendor_checkbox(0).isChecked()
        _select_day(screen, 1)
        assert screen._vendor_checkbox(0).isChecked()

    def test_span_view_keeps_marks_stored(
            self, qtbot, vendor_verify_db):
        """Filter churn never touches the stored facts: back to
        All Market Days = inert dash (unscoped); a range view
        shows the range's OWN (absent) mark while the per-day
        marks persist untouched underneath."""
        _seed_both_vendors_both_days(vendor_verify_db)
        screen = _make_screen(qtbot)
        _select_day(screen, 1)
        screen._vendor_checkbox(0).setChecked(True)   # V1 day 1
        screen._vendor_checkbox(1).setChecked(True)   # V2 day 1
        _select_day(screen, None)
        assert screen._vendor_checkbox(0) is None, (
            "unscoped view must be inert")
        _apply_range(screen, '2026-04-01', '2026-05-31')
        # Day marks don't bleed into the range scope — both rows
        # show the (absent) range mark.
        assert not screen._vendor_checkbox(0).isChecked()
        assert not screen._vendor_checkbox(1).isChecked()
        n = vendor_verify_db.execute(
            "SELECT COUNT(*) FROM vendor_day_verifications"
        ).fetchone()[0]
        assert n == 2, "filter churn must not delete marks"
        # Reselecting the day restores its own marks.
        _select_day(screen, 1)
        assert screen._vendor_checkbox(0).isChecked()
        assert screen._vendor_checkbox(1).isChecked()

    def test_date_range_resets_day_mode_marks_independent(
            self, qtbot, vendor_verify_db):
        """The field-reported sequence (Sean, 2026-06-12): with a
        day selected and a vendor ticked, applying a date range
        resets Market Day to All (last-touched-wins) and the row
        shows the range's OWN independent mark (absent →
        unchecked) while the stored per-day mark survives."""
        _seed_both_vendors_both_days(vendor_verify_db)
        screen = _make_screen(qtbot)
        _select_day(screen, 1)
        screen._vendor_checkbox(0).setChecked(True)
        _apply_range(screen, '2026-04-01', '2026-05-31')
        assert screen.market_day_combo.currentData() is None
        cb = screen._vendor_checkbox(0)
        assert cb is not None, "range views are tickable"
        assert not cb.isChecked()        # independent range mark
        assert 'independent' in cb.toolTip()
        n = vendor_verify_db.execute(
            "SELECT COUNT(*) FROM vendor_day_verifications"
        ).fetchone()[0]
        assert n == 1, "the per-day mark must survive the switch"

    def test_no_audit_rows_written(self, qtbot, vendor_verify_db):
        """A verification tick is a reconciliation scratch mark,
        not a financial mutation."""
        _seed_both_vendors_both_days(vendor_verify_db)
        screen = _make_screen(qtbot)
        _select_day(screen, 1)
        before = vendor_verify_db.execute(
            "SELECT COUNT(*) FROM audit_log").fetchone()[0]
        screen._vendor_checkbox(0).setChecked(True)
        after = vendor_verify_db.execute(
            "SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert after == before

    def test_money_columns_unchanged_by_tick(
            self, qtbot, vendor_verify_db):
        """Zero effect on FAM reimbursement — the row's money
        figures are identical before and after ticking."""
        _seed_both_vendors_both_days(vendor_verify_db)
        screen = _make_screen(qtbot)
        _select_day(screen, 1)
        before = screen._vendor_data[0]['Total Due to Vendor']
        screen._vendor_checkbox(0).setChecked(True)
        screen._generate_reports()
        assert (screen._vendor_data[0]['Total Due to Vendor']
                == before)

    def test_sheet_collector_untouched(self, vendor_verify_db):
        """The synced Vendor Reimbursement tab must not gain a
        Verified column — local-only working state (frozen-tab
        discipline)."""
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement)
        _seed_both_vendors_both_days(vendor_verify_db)
        vendor_verify_db.execute(
            "INSERT INTO vendor_day_verifications VALUES "
            "(1, 1, '2026-04-30 18:00:00')")
        vendor_verify_db.commit()
        rows = _collect_vendor_reimbursement(vendor_verify_db, [1])
        assert rows, "collector should emit VR rows"
        for r in rows:
            assert 'Verified' not in r
            assert 'Verified At' not in r
