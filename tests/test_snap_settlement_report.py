"""Tests for the SNAP Settlement report (v2.1.0, ENH-003).

Bryan (Bellevue) matches paper EBT-terminal receipts — ONE per
customer order, covering ALL vendors in the order — against the
system.  The Detailed Ledger emits one row per (vendor)
transaction, scattering a single swipe across N rows; this report
folds each order back into one line.  Pinned guarantees:

  1. One row per customer order; SNAP Total = Σ customer_charged
     across the order's active transactions for SNAP/EBT-named
     methods — the same order-level figure the payment
     confirmation dialog shows for the EBT-swipe acknowledgement
     (receipt-format integrity: a $21.23 swipe shows as a $21.23
     line no matter how many vendors split it).
  2. Multi-vendor orders fold into a single row.
  3. Non-SNAP tenders never contribute; orders with no SNAP are
     absent entirely.
  4. Voided transactions are excluded; Adjusted are included
     (ACTIVE_TX_STATUSES discipline, CLAUDE.md rule 6).
  5. Method detection reuses ``is_external_device_method`` — the
     dialog's keyword test — so any SNAP/EBT-named method counts
     and the two surfaces cannot disagree.
  6. Orders that fired no rewards still appear (Bryan's old
     Generated Rewards workaround was wrong precisely because it
     listed reward-firing orders only).
  7. Date + Market filters apply; Vendor + Payment Type filters
     are deliberately ignored (a vendor sub-total would never
     match the paper receipt).
  8. Local-only: NOT registered in SHEET_KEYS or
     REQUIRED_SYNC_TABS — no cloud tab mirrors this report.
  9. CSV export writes the table's columns.
"""

import inspect

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def settlement_db(tmp_path):
    db_file = str(tmp_path / "snap_settlement.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES "
        " (1, 'Bellevue', 100000, 1), (2, 'Petoskey', 100000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'V1'), (2, 'V2')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1), (1, 2), (2, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) VALUES "
        " (1, 'SNAP', 100.0, 1, 1, NULL), "
        " (2, 'Cash', 0.0, 2, 1, NULL), "
        " (3, 'EBT Card', 100.0, 3, 1, NULL)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES "
        " (1, 1, '2026-04-30', 'Open', 'T'), "
        " (2, 1, '2026-05-07', 'Open', 'T'), "
        " (3, 2, '2026-04-30', 'Open', 'T')")
    conn.commit()
    yield conn
    close_connection()


_NEXT_TXN = {'n': 1000}


def _seed_order(conn, *, order_id, market_day_id=1, customer='C-1',
                zip_code=None, lines=None):
    """Seed one customer order.

    *lines* is a list of per-transaction dicts:
        {'vendor_id', 'status', 'confirmed_at',
         'items': [(method_id, method_name, method_amount,
                    customer_charged, match_amount), ...]}
    """
    conn.execute(
        "INSERT INTO customer_orders (id, market_day_id, "
        " customer_label, zip_code, status) "
        "VALUES (?, ?, ?, ?, 'Confirmed')",
        (order_id, market_day_id, customer, zip_code))
    for line in lines:
        _NEXT_TXN['n'] += 1
        tid = _NEXT_TXN['n']
        receipt = sum(it[2] for it in line['items'])
        conn.execute(
            "INSERT INTO transactions (id, fam_transaction_id, "
            " market_day_id, vendor_id, customer_order_id, "
            " receipt_total, status, confirmed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, f'T-{tid}', market_day_id, line['vendor_id'],
             order_id, receipt, line.get('status', 'Confirmed'),
             line.get('confirmed_at', '2026-04-30 10:00:00')))
        for (mid, mname, m_amt, charged, match) in line['items']:
            conn.execute(
                "INSERT INTO payment_line_items"
                " (transaction_id, payment_method_id, "
                "  method_name_snapshot, match_percent_snapshot, "
                "  method_amount, customer_charged, match_amount)"
                " VALUES (?, ?, ?, 100.0, ?, ?, ?)",
                (tid, mid, mname, m_amt, charged, match))
    conn.commit()


def _make_screen(qtbot):
    from fam.ui.reports_screen import ReportsScreen
    screen = ReportsScreen()
    qtbot.addWidget(screen)
    return screen


def _settlement_rows(screen):
    """Return the settlement table as a list of dicts.

    Column offsets reflect the v2.1.0 layout: Verified is column 0
    (consistent with Vendor Reimbursement), data columns follow.
    """
    t = screen.settlement_table
    out = []
    for r in range(t.rowCount()):
        out.append({
            'Timestamp': t.item(r, 1).text(),
            'Customer': t.item(r, 2).text(),
            'Zip Code': t.item(r, 3).text(),
            'Market': t.item(r, 4).text(),
            'Receipts': t.item(r, 5).text(),
            'SNAP Total': t.item(r, 6).text(),
        })
    return out


class TestOneRowPerSwipe:

    def test_multi_vendor_order_folds_to_one_row(
            self, qtbot, settlement_db):
        """The core requirement: a $21.23 swipe spanning two
        vendors shows as ONE $21.23 line."""
        _seed_order(settlement_db, order_id=10, customer='C-010',
                    zip_code='49770', lines=[
                        {'vendor_id': 1,
                         'items': [(1, 'SNAP', 1000, 500, 500)]},
                        {'vendor_id': 2,
                         'items': [(1, 'SNAP', 3246, 1623, 1623)]},
                    ])
        screen = _make_screen(qtbot)
        rows = _settlement_rows(screen)
        assert len(rows) == 1
        assert rows[0]['Customer'] == 'C-010'
        assert rows[0]['Zip Code'] == '49770'
        assert rows[0]['Market'] == 'Bellevue'
        assert rows[0]['Receipts'] == '2'
        assert rows[0]['SNAP Total'] == '$21.23'
        # CSV-side data mirrors the table.
        assert screen._settlement_data[0]['SNAP Total'] == '21.23'

    def test_mixed_tender_counts_snap_only(
            self, qtbot, settlement_db):
        _seed_order(settlement_db, order_id=10, lines=[
            {'vendor_id': 1, 'items': [
                (1, 'SNAP', 1000, 500, 500),
                (2, 'Cash', 700, 700, 0),
            ]},
        ])
        screen = _make_screen(qtbot)
        rows = _settlement_rows(screen)
        assert len(rows) == 1
        assert rows[0]['SNAP Total'] == '$5.00'

    def test_order_without_snap_absent(self, qtbot, settlement_db):
        _seed_order(settlement_db, order_id=10, lines=[
            {'vendor_id': 1, 'items': [(2, 'Cash', 700, 700, 0)]},
        ])
        screen = _make_screen(qtbot)
        assert _settlement_rows(screen) == []

    def test_voided_excluded_adjusted_included(
            self, qtbot, settlement_db):
        _seed_order(settlement_db, order_id=10, customer='C-010',
                    lines=[
                        {'vendor_id': 1, 'status': 'Adjusted',
                         'items': [(1, 'SNAP', 1000, 500, 500)]},
                        {'vendor_id': 2, 'status': 'Voided',
                         'items': [(1, 'SNAP', 1000, 500, 500)]},
                    ])
        screen = _make_screen(qtbot)
        rows = _settlement_rows(screen)
        assert len(rows) == 1
        assert rows[0]['SNAP Total'] == '$5.00'
        # The voided transaction also drops out of the Receipts count.
        assert rows[0]['Receipts'] == '1'

    def test_ebt_named_method_counts(self, qtbot, settlement_db):
        """Keyword parity with the confirmation dialog: any method
        the dialog flags as an external-device (EBT terminal)
        method belongs in the settlement figure."""
        from fam.ui.widgets.payment_confirmation_dialog import (
            is_external_device_method)
        assert is_external_device_method('EBT Card')
        _seed_order(settlement_db, order_id=10, lines=[
            {'vendor_id': 1,
             'items': [(3, 'EBT Card', 800, 400, 400)]},
        ])
        screen = _make_screen(qtbot)
        rows = _settlement_rows(screen)
        assert len(rows) == 1
        assert rows[0]['SNAP Total'] == '$4.00'

    def test_no_reward_orders_still_listed(
            self, qtbot, settlement_db):
        """Bryan's old workaround (Generated Rewards) listed only
        reward-firing orders.  This report must list EVERY order
        with SNAP — here, one that wrote no generated_rewards row."""
        _seed_order(settlement_db, order_id=10, lines=[
            {'vendor_id': 1, 'items': [(1, 'SNAP', 600, 300, 300)]},
        ])
        n_rewards = settlement_db.execute(
            "SELECT COUNT(*) FROM generated_rewards").fetchone()[0]
        assert n_rewards == 0
        screen = _make_screen(qtbot)
        assert len(_settlement_rows(screen)) == 1

    def test_returning_customer_two_swipes_two_rows(
            self, qtbot, settlement_db):
        """A returning customer who runs SNAP twice gets TWO rows,
        never a merged customer line.  create_customer_order always
        INSERTs a new order row — a returning customer only reuses
        the label string (customer_order.py) — and this report
        folds by co.id, not by label.  Bryan holds two paper EBT
        receipts, so he must see two independently tickable lines."""
        _seed_order(settlement_db, order_id=10, customer='C-001-LB1',
                    lines=[{'vendor_id': 1,
                            'confirmed_at': '2026-04-30 09:00:00',
                            'items': [(1, 'SNAP', 1000, 500, 500)]}])
        _seed_order(settlement_db, order_id=20, customer='C-001-LB1',
                    lines=[{'vendor_id': 2,
                            'confirmed_at': '2026-04-30 13:00:00',
                            'items': [(1, 'SNAP', 2000, 1000, 1000)]}])
        screen = _make_screen(qtbot)
        rows = _settlement_rows(screen)
        assert len(rows) == 2, (
            "two swipes for the same customer label must stay two "
            "rows — merging them would break paper-receipt matching")
        assert [r['Customer'] for r in rows] == ['C-001-LB1',
                                                 'C-001-LB1']
        assert [r['SNAP Total'] for r in rows] == ['$5.00', '$10.00']
        # The Verified marks are per-swipe, not per-customer.
        screen._settlement_checkbox(0).setChecked(True)
        first = settlement_db.execute(
            "SELECT settlement_verified_at FROM customer_orders "
            "WHERE id = 10").fetchone()[0]
        second = settlement_db.execute(
            "SELECT settlement_verified_at FROM customer_orders "
            "WHERE id = 20").fetchone()[0]
        assert first and second is None

    def test_rows_sorted_chronologically(self, qtbot, settlement_db):
        """Rows follow the EBT terminal's paper-stack order —
        earliest swipe first (timestamp = earliest confirm in the
        order)."""
        _seed_order(settlement_db, order_id=20, customer='C-LATE',
                    lines=[{'vendor_id': 1,
                            'confirmed_at': '2026-04-30 14:00:00',
                            'items': [(1, 'SNAP', 200, 100, 100)]}])
        _seed_order(settlement_db, order_id=10, customer='C-EARLY',
                    lines=[{'vendor_id': 1,
                            'confirmed_at': '2026-04-30 09:00:00',
                            'items': [(1, 'SNAP', 200, 100, 100)]}])
        screen = _make_screen(qtbot)
        rows = _settlement_rows(screen)
        assert [r['Customer'] for r in rows] == ['C-EARLY', 'C-LATE']

    def test_detection_reuses_dialog_keyword_test(self):
        """Source pin: the loader must call
        ``is_external_device_method`` (the dialog's own test), not
        a re-implemented keyword list that could drift."""
        from fam.ui.reports_screen import ReportsScreen
        src = inspect.getsource(ReportsScreen._load_snap_settlement)
        assert 'is_external_device_method' in src, (
            "SNAP detection must reuse the confirmation dialog's "
            "is_external_device_method so the settlement figure "
            "and the swipe acknowledgement can never disagree")


class TestFilters:

    def _two_days_two_markets(self, conn):
        _seed_order(conn, order_id=10, customer='C-D1',
                    market_day_id=1, lines=[
                        {'vendor_id': 1,
                         'items': [(1, 'SNAP', 200, 100, 100)]}])
        _seed_order(conn, order_id=20, customer='C-D2',
                    market_day_id=2, lines=[
                        {'vendor_id': 1,
                         'items': [(1, 'SNAP', 200, 100, 100)]}])
        _seed_order(conn, order_id=30, customer='C-M2',
                    market_day_id=3, lines=[
                        {'vendor_id': 1,
                         'items': [(1, 'SNAP', 200, 100, 100)]}])

    def test_date_filter_applies(self, qtbot, settlement_db):
        self._two_days_two_markets(settlement_db)
        screen = _make_screen(qtbot)
        screen.date_range.get_date_range = (
            lambda: ('2026-04-30', '2026-04-30'))
        screen._generate_reports()
        customers = {r['Customer'] for r in _settlement_rows(screen)}
        assert customers == {'C-D1', 'C-M2'}

    def test_market_filter_applies(self, qtbot, settlement_db):
        self._two_days_two_markets(settlement_db)
        screen = _make_screen(qtbot)
        screen.market_combo.checked_data = lambda: [2]
        screen._generate_reports()
        customers = {r['Customer'] for r in _settlement_rows(screen)}
        assert customers == {'C-M2'}

    def test_vendor_filter_ignored(self, qtbot, settlement_db):
        """Receipt-format integrity: a vendor filter must NOT
        shrink the order total — the paper receipt knows nothing
        about vendors."""
        _seed_order(settlement_db, order_id=10, lines=[
            {'vendor_id': 1, 'items': [(1, 'SNAP', 1000, 500, 500)]},
            {'vendor_id': 2, 'items': [(1, 'SNAP', 3246, 1623, 1623)]},
        ])
        screen = _make_screen(qtbot)
        screen.vendor_combo.checked_data = lambda: [2]
        screen._generate_reports()
        rows = _settlement_rows(screen)
        assert len(rows) == 1
        assert rows[0]['SNAP Total'] == '$21.23'

    def test_pay_type_filter_ignored(self, qtbot, settlement_db):
        _seed_order(settlement_db, order_id=10, lines=[
            {'vendor_id': 1, 'items': [(1, 'SNAP', 1000, 500, 500)]},
        ])
        screen = _make_screen(qtbot)
        screen.pay_type_combo.checked_data = lambda: ['Cash']
        screen._generate_reports()
        rows = _settlement_rows(screen)
        assert len(rows) == 1
        assert rows[0]['SNAP Total'] == '$5.00'


class TestTabAndExport:

    def test_tab_registered(self, qtbot, settlement_db):
        screen = _make_screen(qtbot)
        labels = [screen.tabs.tabText(i)
                  for i in range(screen.tabs.count())]
        assert 'SNAP Settlement' in labels
        # Placement (Sean, 2026-06-12): second tab, right after
        # Vendor Reimbursement — the post-market settlement working
        # page earns front placement.
        assert labels.index('SNAP Settlement') == 1
        assert labels.index('Vendor Reimbursement') == 0

    def test_column_headers(self, qtbot, settlement_db):
        # The trailing unlabeled column is a deliberate FILLER: it
        # absorbs leftover viewport width so the data columns stay
        # content-sized (field feedback 2026-06-12 — stretching
        # Market made it absurdly wide).
        screen = _make_screen(qtbot)
        headers = [
            screen.settlement_table.horizontalHeaderItem(i).text()
            for i in range(screen.settlement_table.columnCount())]
        # Verified first — consistent with Vendor Reimbursement
        # (Sean, 2026-06-12).
        assert headers == ["Verified", "Timestamp", "Customer",
                           "Zip Code", "Market", "Receipts",
                           "SNAP Total", ""]

    def test_local_only_no_sync_registration(self):
        """Confirmed requirement (Bryan, 2026-06-12): on-screen +
        CSV only — no Sheets tab."""
        from fam.sync.manager import SyncManager
        from fam.utils.app_settings import REQUIRED_SYNC_TABS
        assert 'SNAP Settlement' not in SyncManager.SHEET_KEYS
        assert 'SNAP Settlement' not in REQUIRED_SYNC_TABS

    def test_export_csv_contains_header_and_row(self, tmp_path):
        from fam.utils.export import export_snap_settlement
        rows = [{
            'Timestamp': '2026-04-30 10:00:00',
            'Customer': 'C-010',
            'Zip Code': '49770',
            'Market': 'Bellevue',
            'Receipts': 2,
            'SNAP Total': '21.23',
            'Verified': 'Yes',
            'Verified At': '2026-04-30 18:00:00',
        }]
        path = str(tmp_path / "settlement.csv")
        export_snap_settlement(rows, path)
        with open(path, newline='') as fh:
            text = fh.read()
        assert 'SNAP Total' in text
        assert '21.23' in text
        assert 'C-010' in text
        assert 'Verified' in text


class TestVerifiedWorkingPage:
    """The Verified checkbox column (ENH-003 follow-up, schema v39).

    A working page only works if ticks survive refreshes, filter
    changes, and app restarts — so the state persists in
    ``customer_orders.settlement_verified_at`` (NULL = unverified).
    Operational reconciliation state, NOT financial data: no audit
    rows, no sync.
    """

    def _one_order(self, conn):
        _seed_order(conn, order_id=10, customer='C-010', lines=[
            {'vendor_id': 1, 'items': [(1, 'SNAP', 1000, 500, 500)]},
        ])

    def test_is_custom_painted_box(self, qtbot, settlement_db):
        """The Verified cell must be the custom-painted
        _SettlementVerifiedBox, NOT a QCheckBox or checkable item.
        History (all field-reported on 2026-06-12): checkable items
        are dead on click under configure_table's NoEditTriggers,
        and a text-less QCheckBox positions its styled indicator
        against the font line box — it clipped an edge in every
        fixed-size/size-policy/alignment combination tried.  The
        custom widget paints its box centered in the cell rect, so
        clipping is geometrically impossible."""
        from fam.ui.reports_screen import _VerifiedBox
        self._one_order(settlement_db)
        screen = _make_screen(qtbot)
        cb = screen._settlement_checkbox(0)
        assert isinstance(cb, _VerifiedBox)
        assert cb.isEnabled()
        # The widget never asks for less room than its painted box.
        assert cb.minimumHeight() >= _VerifiedBox.BOX
        assert cb.minimumWidth() >= _VerifiedBox.BOX
        assert screen.settlement_table.rowHeight(0) >= 28

    def test_mouse_click_toggles_and_persists(
            self, qtbot, settlement_db):
        """A real mouse click must toggle and persist — the
        original 'I can't seem to tick the field' report."""
        from PySide6.QtCore import Qt
        self._one_order(settlement_db)
        screen = _make_screen(qtbot)
        screen.show()
        cb = screen._settlement_checkbox(0)
        qtbot.mouseClick(cb, Qt.LeftButton)
        assert cb.isChecked()
        val = settlement_db.execute(
            "SELECT settlement_verified_at FROM customer_orders "
            "WHERE id = 10").fetchone()[0]
        assert val, "mouse click must persist the verification"
        qtbot.mouseClick(cb, Qt.LeftButton)
        assert not cb.isChecked()
        val = settlement_db.execute(
            "SELECT settlement_verified_at FROM customer_orders "
            "WHERE id = 10").fetchone()[0]
        assert val is None

    def test_tick_persists_to_db(self, qtbot, settlement_db):
        self._one_order(settlement_db)
        screen = _make_screen(qtbot)
        cb = screen._settlement_checkbox(0)
        assert not cb.isChecked()
        cb.setChecked(True)
        val = settlement_db.execute(
            "SELECT settlement_verified_at FROM customer_orders "
            "WHERE id = 10").fetchone()[0]
        assert val, "tick must persist a timestamp"
        # CSV-side mirror updated too.
        assert screen._settlement_data[0]['Verified'] == 'Yes'

    def test_untick_clears_db(self, qtbot, settlement_db):
        self._one_order(settlement_db)
        settlement_db.execute(
            "UPDATE customer_orders SET settlement_verified_at = "
            "'2026-04-30 18:00:00' WHERE id = 10")
        settlement_db.commit()
        screen = _make_screen(qtbot)
        cb = screen._settlement_checkbox(0)
        assert cb.isChecked(), (
            "verified state must restore from the DB on load")
        cb.setChecked(False)
        val = settlement_db.execute(
            "SELECT settlement_verified_at FROM customer_orders "
            "WHERE id = 10").fetchone()[0]
        assert val is None

    def test_state_survives_refresh(self, qtbot, settlement_db):
        self._one_order(settlement_db)
        screen = _make_screen(qtbot)
        screen._settlement_checkbox(0).setChecked(True)
        screen._generate_reports()   # table rebuilt from DB
        assert screen._settlement_checkbox(0).isChecked()
        assert screen._settlement_data[0]['Verified'] == 'Yes'

    def test_no_audit_rows_written(self, qtbot, settlement_db):
        """Verification is a reconciliation scratch mark, not a
        financial mutation — it must not spam the audit log."""
        self._one_order(settlement_db)
        before = settlement_db.execute(
            "SELECT COUNT(*) FROM audit_log").fetchone()[0]
        screen = _make_screen(qtbot)
        screen._settlement_checkbox(0).setChecked(True)
        after = settlement_db.execute(
            "SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert after == before


class TestMigrationV39:

    def test_fresh_install_has_column(self, settlement_db):
        cols = {r[1] for r in settlement_db.execute(
            "PRAGMA table_info(customer_orders)").fetchall()}
        assert 'settlement_verified_at' in cols

    def test_migration_adds_column_and_is_idempotent(self, tmp_path):
        import sqlite3
        from fam.database.schema import _migrate_v38_to_v39
        db = sqlite3.connect(str(tmp_path / "v38_replica.db"))
        # v38-shaped customer_orders (no settlement_verified_at).
        db.execute("""
            CREATE TABLE customer_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_day_id INTEGER NOT NULL,
                customer_label TEXT NOT NULL,
                zip_code TEXT,
                status TEXT DEFAULT 'Draft',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""")
        db.execute(
            "INSERT INTO customer_orders (market_day_id, "
            " customer_label) VALUES (1, 'C-1')")
        db.commit()
        _migrate_v38_to_v39(db)
        cols = {r[1] for r in db.execute(
            "PRAGMA table_info(customer_orders)").fetchall()}
        assert 'settlement_verified_at' in cols
        # Existing rows backfill to NULL (= unverified).
        assert db.execute(
            "SELECT settlement_verified_at FROM customer_orders"
        ).fetchone()[0] is None
        _migrate_v38_to_v39(db)   # idempotent re-run
        db.close()
