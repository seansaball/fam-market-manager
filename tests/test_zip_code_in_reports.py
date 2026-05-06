"""Zip Code column added to Detailed Ledger / Transaction Log /
Generated Rewards / FMNP Entries (v2.0.6).

Coordinator-requested feature: surface zip code on the per-customer
reports so demographic analysis is possible — "which zip codes use
which payment methods at which vendors, and how much."

Pre-fix only the Geolocation report had zip code (it's the whole
point of that report).  Other reports knew the customer label but
not the zip — which made cross-correlation impossible without a
manual join via the Geolocation report.

These tests pin the column presence and the join-source semantics
so a future schema or query refactor can't silently drop the
column.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_zip_code_reports.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield tmp_path
    close_connection()


def _seed_market_with_zip_customer(conn):
    """Build the minimal scaffolding to drive a real transaction
    through the collectors: market + market_day + vendor + payment
    method + customer_order with a known zip + transaction +
    payment_line_item."""
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit) "
        " VALUES (10, 'Bethel Park', 10000)")
    conn.execute(
        "INSERT INTO vendors (id, name, is_active) "
        " VALUES (20, '1.11 Juice Bar', 1)")
    conn.execute(
        "INSERT INTO payment_methods "
        " (id, name, match_percent, is_active, sort_order) "
        " VALUES (30, 'SNAP', 100.0, 1, 1)")
    conn.execute(
        "INSERT INTO market_days "
        " (id, market_id, date, status, opened_by) "
        " VALUES (40, 10, '2026-05-05', 'Open', 'Tester')")
    conn.execute(
        "INSERT INTO customer_orders "
        " (id, market_day_id, customer_label, status, zip_code) "
        " VALUES (50, 40, 'C-001-LB1', 'Confirmed', '15102')")
    conn.execute(
        "INSERT INTO transactions "
        " (id, market_day_id, vendor_id, customer_order_id, "
        "  receipt_total, status, fam_transaction_id, created_at) "
        " VALUES (60, 40, 20, 50, 2000, 'Confirmed', "
        "  'FAM-BP-20260505-0001', '2026-05-05 10:00:00')")
    conn.execute(
        "INSERT INTO payment_line_items "
        " (transaction_id, payment_method_id, method_name_snapshot, "
        "  match_percent_snapshot, method_amount, customer_charged, "
        "  match_amount) "
        " VALUES (60, 30, 'SNAP', 100.0, 2000, 1000, 1000)")
    conn.commit()


# ─── Sync — Detailed Ledger ───────────────────────────────────────


class TestSyncDetailedLedgerHasZipCode:

    def test_zip_code_present_on_real_transaction(self):
        from fam.sync.data_collector import _collect_detailed_ledger
        conn = get_connection()
        _seed_market_with_zip_customer(conn)

        rows = _collect_detailed_ledger(conn, md_id=40)
        assert len(rows) >= 1
        txn_row = next(
            r for r in rows
            if r['Transaction ID'] == 'FAM-BP-20260505-0001')
        assert 'Zip Code' in txn_row, (
            "Detailed Ledger row must include 'Zip Code' column for "
            "demographic correlation (v2.0.6).")
        assert txn_row['Zip Code'] == '15102', (
            f"Zip code joined from customer_orders must reach the "
            f"sync row.  Got {txn_row['Zip Code']!r} expected '15102'.")

    def test_external_fmnp_entries_have_empty_zip_column(self):
        """External FMNP entries (the dedicated Entry tab) aren't
        tied to a customer order — the column is still present for
        schema parity, just empty."""
        from fam.sync.data_collector import _collect_detailed_ledger
        conn = get_connection()
        _seed_market_with_zip_customer(conn)
        # Add an external FMNP entry
        conn.execute(
            "INSERT INTO fmnp_entries "
            " (id, market_day_id, vendor_id, amount, status, "
            "  entered_by, created_at) "
            " VALUES (70, 40, 20, 500, 'Active', 'Tester', "
            "  '2026-05-05 11:00:00')")
        conn.commit()

        rows = _collect_detailed_ledger(conn, md_id=40)
        fmnp_row = next(
            r for r in rows if r['Transaction ID'] == 'FMNP-70')
        assert 'Zip Code' in fmnp_row
        assert fmnp_row['Zip Code'] == ''


# ─── Sync — Transaction Log ───────────────────────────────────────


class TestSyncTransactionLogHasZipCode:

    def test_audit_row_for_transaction_has_customer_and_zip(self):
        from fam.sync.data_collector import _collect_transaction_log
        from fam.models.audit import log_action
        conn = get_connection()
        _seed_market_with_zip_customer(conn)

        # Emit an audit row for the transaction
        log_action(
            'transactions', 60, 'CONFIRM', 'Tester',
            notes='Test confirm', commit=True)

        rows = _collect_transaction_log(md_id=40)
        confirm_rows = [r for r in rows if r['Action']]
        assert len(confirm_rows) >= 1
        # Find the row we just added
        my_row = next(
            (r for r in confirm_rows
             if r['Transaction'] == 'FAM-BP-20260505-0001'),
            None)
        assert my_row is not None, "Transaction Log must include the audit row"
        assert 'Customer' in my_row
        assert 'Zip Code' in my_row
        assert my_row['Customer'] == 'C-001-LB1'
        assert my_row['Zip Code'] == '15102'


# ─── Sync — Generated Rewards ─────────────────────────────────────


class TestSyncGeneratedRewardsHasZipCode:

    def test_reward_row_has_zip_code(self):
        from fam.sync.data_collector import _collect_generated_rewards
        conn = get_connection()
        _seed_market_with_zip_customer(conn)
        # Seed a generated_rewards row
        conn.execute(
            "INSERT INTO generated_rewards "
            " (customer_order_id, market_day_id, source_method_id, "
            "  source_method_name_snapshot, source_total_cents, "
            "  threshold_cents, reward_method_id, "
            "  reward_method_name_snapshot, reward_unit_cents, "
            "  n_units, reward_total_cents, generated_by, generated_at) "
            " VALUES (50, 40, 30, 'SNAP', 1000, 500, 30, "
            "  'JH Food Bucks', 200, 2, 400, 'Tester', "
            "  '2026-05-05 10:01:00')")
        conn.commit()

        rows = _collect_generated_rewards(conn, md_id=40)
        assert len(rows) == 1
        assert 'Zip Code' in rows[0]
        assert rows[0]['Zip Code'] == '15102'
        assert rows[0]['Customer'] == 'C-001-LB1'


# ─── Sync — FMNP Entries (Source B = payment-flow) ───────────────


class TestSyncFmnpEntriesHasZipCode:

    def test_payment_flow_fmnp_entry_has_zip_code(self):
        from fam.sync.data_collector import _collect_fmnp_entries
        conn = get_connection()
        _seed_market_with_zip_customer(conn)
        # Seed an FMNP payment_line_item bound to txn 60
        conn.execute(
            "INSERT INTO payment_methods "
            " (id, name, match_percent, is_active, sort_order, "
            "  denomination) "
            " VALUES (35, 'FMNP', 100.0, 1, 2, 500)")
        conn.execute(
            "INSERT INTO payment_line_items "
            " (transaction_id, payment_method_id, "
            "  method_name_snapshot, match_percent_snapshot, "
            "  method_amount, customer_charged, match_amount, "
            "  created_at) "
            " VALUES (60, 35, 'FMNP', 100.0, 1000, 500, 500, "
            "  '2026-05-05 10:02:00')")
        conn.commit()

        rows = _collect_fmnp_entries(conn, md_id=40)
        # Source B rows (payment-flow) should carry zip code
        pay_rows = [r for r in rows if r['Source'] == 'Payment']
        assert len(pay_rows) >= 1, "Expected at least one payment-flow FMNP entry"
        assert all('Zip Code' in r for r in pay_rows)
        assert all(r['Zip Code'] == '15102' for r in pay_rows), (
            f"Payment-flow FMNP entries must carry the customer's "
            f"zip code.  Got: {[r['Zip Code'] for r in pay_rows]}")
        assert all(r['Customer'] == 'C-001-LB1' for r in pay_rows)

    def test_source_a_fmnp_entry_has_empty_zip(self):
        """Source A (manual FMNP Entry tab) isn't tied to a
        customer order — the column is present for schema parity
        but empty."""
        from fam.sync.data_collector import _collect_fmnp_entries
        conn = get_connection()
        _seed_market_with_zip_customer(conn)
        conn.execute(
            "INSERT INTO payment_methods "
            " (id, name, match_percent, is_active, sort_order, "
            "  denomination) "
            " VALUES (35, 'FMNP', 100.0, 1, 2, 500)")
        conn.execute(
            "INSERT INTO fmnp_entries "
            " (id, market_day_id, vendor_id, amount, status, "
            "  entered_by, created_at) "
            " VALUES (80, 40, 20, 500, 'Active', 'Tester', "
            "  '2026-05-05 11:00:00')")
        conn.commit()

        rows = _collect_fmnp_entries(conn, md_id=40)
        entry_rows = [r for r in rows if r['Source'] == 'FMNP Entry']
        assert len(entry_rows) >= 1
        assert all('Zip Code' in r for r in entry_rows)
        assert all(r['Zip Code'] == '' for r in entry_rows)
        assert all(r['Customer'] == '' for r in entry_rows)


# ─── audit.get_transaction_log surfaces customer + zip ────────────


class TestGetTransactionLogJoinsCustomerOrder:
    """The model layer that feeds both the sync collector AND the
    Reports → Transaction Log UI must join customer_orders so both
    surfaces have access to the zip code."""

    def test_get_transaction_log_returns_customer_label_and_zip_code(self):
        from fam.models.audit import get_transaction_log, log_action
        conn = get_connection()
        _seed_market_with_zip_customer(conn)
        log_action(
            'transactions', 60, 'CONFIRM', 'Tester',
            notes='Test confirm', commit=True)

        rows = get_transaction_log(market_day_id=40, limit=10)
        # Find the row for our transaction
        my_row = next(
            (r for r in rows if r.get('record_id') == 60),
            None)
        assert my_row is not None
        assert 'customer_label' in my_row, (
            "get_transaction_log must return customer_label key — "
            "joined from customer_orders.")
        assert 'zip_code' in my_row
        assert my_row['customer_label'] == 'C-001-LB1'
        assert my_row['zip_code'] == '15102'


# ─── Internal UI table column counts ──────────────────────────────


class TestInternalReportsTableColumnCount:
    """Source-pin the new column on the UI tables so a future
    setColumnCount edit can't silently drop the new Zip Code header."""

    def test_detailed_ledger_table_has_10_columns(self):
        import inspect
        import fam.ui.reports_screen as rs
        src = inspect.getsource(rs.ReportsScreen)
        # The exact-string pin is brittle; the structural intent is
        # 'Customer' must be followed by 'Zip Code' in the header
        # list, regardless of total column count.
        assert '"Customer", "Zip Code"' in src.replace(
            "'", '"'), (
            "Detailed Ledger header list must place Zip Code "
            "directly after Customer.")

    def test_transaction_log_table_has_zip_code_header(self):
        import inspect
        import fam.ui.reports_screen as rs
        src = inspect.getsource(rs.ReportsScreen)
        # Transaction Log: "Customer", "Zip Code" must appear
        # consecutively after "Transaction".
        assert '"Customer", "Zip Code"' in src.replace(
            "'", '"')

    def test_rewards_table_has_zip_code_header(self):
        import inspect
        import fam.ui.reports_screen as rs
        src = inspect.getsource(rs.ReportsScreen)
        # Rewards: "Customer", "Zip Code" must appear consecutively
        assert '"Customer", "Zip Code"' in src.replace(
            "'", '"')
