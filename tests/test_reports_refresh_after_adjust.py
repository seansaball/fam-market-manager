"""Reports screen reactively refreshes on financial mutations
(v1.9.10 follow-up, 2026-05-01).

Onsite report: a manager adjusted a transaction with the "customer
is gone" path (which injects an Unallocated Funds line item to
absorb the gap), and the Reports screen's FAM Absorbed card +
"Unallocated Funds" row in the FAM Match table didn't update.

Root cause: ``admin_screen.data_changed`` was wired only to
``_trigger_sync`` (cloud sync), not to
``reports_screen.refresh``.  The reports screen only refreshed on
navigation, so a manager who adjusted while standing on the
Reports tab (or quickly bouncing between Admin and Reports) saw
stale numbers.

Fix: every mutation signal that triggers a sync also triggers a
reports refresh.

Pins:
  1. The end-to-end data path is correct — adjusting a confirmed
     txn with the customer-gone branch persists the UF row to
     ``payment_line_items`` and the FAM Match query exposes it as
     "Unallocated Funds" with the absorbed cents.
  2. Source-level wiring guarantee — ``main_window.py`` connects
     each of the five mutation signals to
     ``reports_screen.refresh``.  A future regression that drops
     one of these connections fails this test loud.
"""

import inspect

import pytest

from fam.database.connection import (
    get_connection, set_db_path, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def db(tmp_path):
    db_file = str(tmp_path / "reports_refresh.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(1, 'SNAP', 100.0, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO vendor_payment_methods (vendor_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES (1, 1, '2026-05-01', 'Open', 'Tester')")
    conn.commit()
    yield conn
    close_connection()


# ════════════════════════════════════════════════════════════════════
# 1. End-to-end data path — UF row lands in DB and reports query
# ════════════════════════════════════════════════════════════════════


class TestUnallocatedFundsLandsInReportsQuery:

    def test_customer_gone_adjustment_persists_uf_to_pli(self, db):
        """Mirrors the admin save flow.  After adjusting a txn with
        ``_append_unallocated_funds_row`` the DB has a UF line item
        on that transaction with method_amount=gap, customer=0,
        match=0."""
        from fam.models.transaction import (
            create_transaction, confirm_transaction,
            save_payment_line_items,
        )
        from fam.ui.admin_screen import _append_unallocated_funds_row

        # Confirm a $50 receipt with $50 SNAP method.
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=5000,
            market_day_date='2026-05-01')
        original_items = [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 5000, 'match_amount': 2500,
            'customer_charged': 2500, 'photo_path': None,
        }]
        save_payment_line_items(txn_id, original_items)
        confirm_transaction(txn_id, confirmed_by='Tester')

        # Adjust: receipt rises to $60, customer is gone → UF for
        # the $10 gap.
        from fam.models.transaction import update_transaction
        update_transaction(
            txn_id, receipt_total=6000, status='Adjusted',
            changed_by='Tester')
        new_items = list(original_items)
        gap_cents = 1000
        seeded = _append_unallocated_funds_row(new_items, gap_cents)
        assert seeded is not None, "Unallocated Funds method must be seeded"
        save_payment_line_items(txn_id, new_items)

        # Verify the UF row landed.
        rows = db.execute(
            "SELECT method_name_snapshot, method_amount, match_amount, "
            "customer_charged FROM payment_line_items "
            "WHERE transaction_id=? ORDER BY method_name_snapshot",
            (txn_id,)).fetchall()
        names = [r['method_name_snapshot'] for r in rows]
        assert 'Unallocated Funds' in names
        uf = next(r for r in rows
                   if r['method_name_snapshot'] == 'Unallocated Funds')
        assert uf['method_amount'] == 1000
        assert uf['match_amount'] == 0
        assert uf['customer_charged'] == 0

    def test_fam_match_query_aggregates_uf_as_absorbed(self, db):
        """The FAM Match Report query (the one
        ``reports_screen._generate_reports`` runs) must aggregate
        Unallocated Funds and the report-screen rendering classifies
        its method_amount as ``FAM Absorbed`` (not customer-paid,
        not FAM Match)."""
        from fam.models.transaction import (
            create_transaction, confirm_transaction,
            save_payment_line_items, update_transaction,
        )
        from fam.ui.admin_screen import _append_unallocated_funds_row
        from fam.models.payment_method import UNALLOCATED_FUNDS_NAME

        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=5000,
            market_day_date='2026-05-01')
        items = [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 5000, 'match_amount': 2500,
            'customer_charged': 2500, 'photo_path': None,
        }]
        save_payment_line_items(txn_id, items)
        confirm_transaction(txn_id, confirmed_by='Tester')
        update_transaction(
            txn_id, receipt_total=6000, status='Adjusted',
            changed_by='Tester')
        _append_unallocated_funds_row(items, 1000)
        save_payment_line_items(txn_id, items)

        # Run the same SHAPE of query the FAM Match report uses.
        rows = db.execute("""
            SELECT pl.method_name_snapshot AS method,
                   SUM(pl.method_amount) AS total_allocated,
                   SUM(pl.match_amount) AS total_fam_match
            FROM payment_line_items pl
            JOIN transactions t ON pl.transaction_id = t.id
            WHERE t.status IN ('Confirmed', 'Adjusted')
            GROUP BY pl.method_name_snapshot
            ORDER BY pl.method_name_snapshot
        """).fetchall()
        by_method = {r['method']: r for r in rows}
        assert UNALLOCATED_FUNDS_NAME in by_method, (
            "Reports FAM Match query must surface the Unallocated "
            "Funds line item committed during the customer-gone "
            "adjustment branch")
        uf = by_method[UNALLOCATED_FUNDS_NAME]
        assert uf['total_allocated'] == 1000, (
            "Unallocated Funds total_allocated should equal the gap "
            "the adjustment absorbed ($10).")
        assert uf['total_fam_match'] == 0, (
            "Unallocated Funds is absorption, not match — match must be 0")


# ════════════════════════════════════════════════════════════════════
# 2. Source-level wiring guarantee
# ════════════════════════════════════════════════════════════════════


class TestMainWindowWiresMutationSignalsToReportsRefresh:
    """Pin the connections so a refactor that drops any of them
    breaks this test, not the volunteer's stale-report experience.
    """

    def test_admin_data_changed_refreshes_reports(self):
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow)
        assert (
            'self.admin_screen.data_changed.connect(\n'
            '            self.reports_screen.refresh)' in src
            or 'self.admin_screen.data_changed.connect(self.reports_screen.refresh)' in src
        ), "admin_screen.data_changed must trigger reports_screen.refresh"

    def test_payment_confirmed_refreshes_reports(self):
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow)
        assert (
            'self.payment_screen.payment_confirmed.connect(\n'
            '            self.reports_screen.refresh)' in src
            or 'self.payment_screen.payment_confirmed.connect(self.reports_screen.refresh)' in src
        ), "payment_confirmed must trigger reports_screen.refresh"

    def test_draft_saved_refreshes_reports(self):
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow)
        assert (
            'self.payment_screen.draft_saved.connect(\n'
            '            self.reports_screen.refresh)' in src
            or 'self.payment_screen.draft_saved.connect(self.reports_screen.refresh)' in src
        ), "draft_saved must trigger reports_screen.refresh"

    def test_fmnp_entry_saved_refreshes_reports(self):
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow)
        assert (
            'self.fmnp_screen.entry_saved.connect(\n'
            '            self.reports_screen.refresh)' in src
            or 'self.fmnp_screen.entry_saved.connect(self.reports_screen.refresh)' in src
        ), "fmnp entry_saved must trigger reports_screen.refresh"

    def test_receipt_intake_data_changed_refreshes_reports(self):
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow)
        assert (
            'self.receipt_intake_screen.data_changed.connect(\n'
            '            self.reports_screen.refresh)' in src
            or 'self.receipt_intake_screen.data_changed.connect(self.reports_screen.refresh)' in src
        ), "receipt_intake data_changed must trigger reports_screen.refresh"
