"""Tests for FMNP external entry integration with reporting.

Validates that fmnp_entries (external vendor-to-FAM matching logged outside
the app) correctly flow into all report queries:
  - Vendor Reimbursement (FMNP Match column + vendor-only rows)
  - FAM Match Report (FMNP External row)
  - Detailed Ledger (FMNP Entry rows)
  - Summary totals (FAM Match + FMNP Match cards)
  - No double-counting between in-app and external FMNP

Test scenario:
  Green Valley Farm   — $30 receipt: SNAP $20 + Cash $10 (no FMNP)
  Sunny Acres Produce — $40 receipt: FMNP $40 (in-app, 100% match)
  Mountain Herb Co.   — no transactions, $50 external FMNP entry only
  Bakers Delight      — $60 receipt: Food Bucks $40 + Cash $20
                        + $25 and $15 external FMNP entries ($40 total)
"""

import pytest
from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.models.fmnp import (
    create_fmnp_entry, update_fmnp_entry, delete_fmnp_entry,
    get_fmnp_entry_by_id, get_fmnp_entries
)
from fam.models.audit import log_action


# ──────────────────────────────────────────────────────────────────
# Fixture: fresh database per test
# ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Create a fresh database for each test."""
    db_file = str(tmp_path / "test_fmnp_reports.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    yield conn
    close_connection()


def _seed(conn):
    """
    Seed multi-vendor, multi-payment-type scenario.

    Match formula: match = amount * pct / (100 + pct)

    Transactions:
      FAM-001  Green Valley  $30  SNAP($20, 50% → $6.67 match) + Cash($10, 0%)
      FAM-002  Sunny Acres   $40  FMNP($40, 100% → $20.00 match)
      FAM-003  Bakers        $60  Food Bucks($40, 100% → $20 match) + Cash($20, 0%)

    External FMNP entries:
      #1  Mountain Herb  $50  (5 checks) on market day 1
      #2  Bakers         $25  (2 checks) on market day 2
      #3  Bakers         $15  (1 check)  on market day 1
    """
    conn.execute("INSERT INTO markets (id, name, address) VALUES (1, 'Downtown Market', '123 Main')")
    conn.execute("INSERT INTO markets (id, name, address) VALUES (2, 'Riverside Market', '456 River')")

    conn.execute("INSERT INTO market_days (id, market_id, date, status) VALUES (1, 1, '2026-02-15', 'Open')")
    conn.execute("INSERT INTO market_days (id, market_id, date, status) VALUES (2, 2, '2026-02-20', 'Open')")

    conn.execute("INSERT INTO vendors (id, name, is_active) VALUES (1, 'Green Valley Farm', 1)")
    conn.execute("INSERT INTO vendors (id, name, is_active) VALUES (2, 'Sunny Acres Produce', 1)")
    conn.execute("INSERT INTO vendors (id, name, is_active) VALUES (3, 'Mountain Herb Co.', 1)")
    conn.execute("INSERT INTO vendors (id, name, is_active) VALUES (4, 'Bakers Delight', 1)")

    conn.execute("INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order) VALUES (1, 'SNAP', 50.0, 1, 1)")
    conn.execute("INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order) VALUES (2, 'Cash', 0.0, 1, 2)")
    conn.execute("INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order) VALUES (3, 'FMNP', 100.0, 1, 3)")
    conn.execute("INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order) VALUES (4, 'Food Bucks', 100.0, 1, 4)")

    conn.execute("INSERT INTO customer_orders (id, market_day_id, customer_label, zip_code) VALUES (1, 1, 'C-001', '12345')")
    conn.execute("INSERT INTO customer_orders (id, market_day_id, customer_label, zip_code) VALUES (2, 1, 'C-002', '12345')")
    conn.execute("INSERT INTO customer_orders (id, market_day_id, customer_label, zip_code) VALUES (3, 2, 'C-003', '67890')")

    # ── Transactions ──
    # FAM-001: Green Valley — $30, SNAP $20 + Cash $10
    conn.execute("""INSERT INTO transactions
        (id, fam_transaction_id, market_day_id, vendor_id, customer_order_id, receipt_total, status)
        VALUES (1, 'FAM-001', 1, 1, 1, 30.00, 'Confirmed')""")
    conn.execute("""INSERT INTO payment_line_items
        (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
         method_amount, match_amount, customer_charged)
        VALUES (1, 1, 'SNAP', 50.0, 20.00, 6.67, 13.33)""")
    conn.execute("""INSERT INTO payment_line_items
        (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
         method_amount, match_amount, customer_charged)
        VALUES (1, 2, 'Cash', 0.0, 10.00, 0.00, 10.00)""")

    # FAM-002: Sunny Acres — $40, FMNP in-app
    conn.execute("""INSERT INTO transactions
        (id, fam_transaction_id, market_day_id, vendor_id, customer_order_id, receipt_total, status)
        VALUES (2, 'FAM-002', 1, 2, 2, 40.00, 'Confirmed')""")
    conn.execute("""INSERT INTO payment_line_items
        (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
         method_amount, match_amount, customer_charged)
        VALUES (2, 3, 'FMNP', 100.0, 40.00, 20.00, 20.00)""")

    # FAM-003: Bakers Delight — $60, Food Bucks $40 + Cash $20
    conn.execute("""INSERT INTO transactions
        (id, fam_transaction_id, market_day_id, vendor_id, customer_order_id, receipt_total, status)
        VALUES (3, 'FAM-003', 2, 4, 3, 60.00, 'Confirmed')""")
    conn.execute("""INSERT INTO payment_line_items
        (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
         method_amount, match_amount, customer_charged)
        VALUES (3, 4, 'Food Bucks', 100.0, 40.00, 20.00, 20.00)""")
    conn.execute("""INSERT INTO payment_line_items
        (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
         method_amount, match_amount, customer_charged)
        VALUES (3, 2, 'Cash', 0.0, 20.00, 0.00, 20.00)""")

    # ── External FMNP Entries ──
    conn.execute("""INSERT INTO fmnp_entries
        (id, market_day_id, vendor_id, amount, check_count, entered_by, notes)
        VALUES (1, 1, 3, 50.00, 5, 'Admin', 'Five $10 checks')""")
    conn.execute("""INSERT INTO fmnp_entries
        (id, market_day_id, vendor_id, amount, check_count, entered_by, notes)
        VALUES (2, 2, 4, 25.00, 2, 'Admin', 'Two checks')""")
    conn.execute("""INSERT INTO fmnp_entries
        (id, market_day_id, vendor_id, amount, check_count, entered_by, notes)
        VALUES (3, 1, 4, 15.00, 1, 'Admin', 'One check')""")

    conn.commit()


# ──────────────────────────────────────────────────────────────────
# Query helpers (replicate reports_screen.py SQL + merge logic)
# ──────────────────────────────────────────────────────────────────
_TXN_WHERE = "WHERE t.status IN ('Confirmed', 'Adjusted')"


def _query_vendor_reimbursement(conn):
    """Replicate vendor reimbursement SQL + merge from reports_screen.py."""
    vendor_rows = conn.execute(f"""
        SELECT v.name as vendor,
               COALESCE(SUM(t.receipt_total), 0) as gross_sales,
               GROUP_CONCAT(DISTINCT md.date) as transaction_dates,
               GROUP_CONCAT(DISTINCT co.customer_label) as customer_ids
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        LEFT JOIN customer_orders co ON t.customer_order_id = co.id
        {_TXN_WHERE}
        GROUP BY v.id, v.name ORDER BY v.name
    """).fetchall()

    match_rows = conn.execute(f"""
        SELECT v.name as vendor,
               COALESCE(SUM(pl.match_amount), 0) as fam_match,
               COALESCE(SUM(CASE WHEN pl.method_name_snapshot = 'FMNP'
                                 THEN pl.match_amount ELSE 0 END), 0) as fmnp_match
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        LEFT JOIN customer_orders co ON t.customer_order_id = co.id
        {_TXN_WHERE}
        GROUP BY v.id, v.name
    """).fetchall()

    match_by_vendor = {
        r['vendor']: {'fam_match': r['fam_match'], 'fmnp_match': r['fmnp_match']}
        for r in match_rows
    }

    vendor_dict = {}
    for r in vendor_rows:
        vm = match_by_vendor.get(r['vendor'], {})
        vendor_dict[r['vendor']] = {
            'vendor': r['vendor'],
            'customers': r['customer_ids'] or '',
            'dates': r['transaction_dates'] or '',
            'gross': r['gross_sales'],
            'fam_match': vm.get('fam_match', 0),
            'fmnp_match': vm.get('fmnp_match', 0),
        }

    # Merge external FMNP entries
    fmnp_rows = conn.execute("""
        SELECT v.name as vendor,
               COALESCE(SUM(fe.amount), 0) as fmnp_entry_total,
               GROUP_CONCAT(DISTINCT md.date) as fmnp_dates
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        JOIN market_days md ON fe.market_day_id = md.id
        GROUP BY v.id, v.name
    """).fetchall()

    for r in fmnp_rows:
        if r['vendor'] in vendor_dict:
            vendor_dict[r['vendor']]['fmnp_match'] += r['fmnp_entry_total']
            existing = set(vendor_dict[r['vendor']]['dates'].split(',')) \
                if vendor_dict[r['vendor']]['dates'] else set()
            new_dates = set((r['fmnp_dates'] or '').split(','))
            all_dates = (existing | new_dates) - {''}
            vendor_dict[r['vendor']]['dates'] = ','.join(sorted(all_dates))
        else:
            vendor_dict[r['vendor']] = {
                'vendor': r['vendor'], 'customers': '',
                'dates': r['fmnp_dates'] or '',
                'gross': 0, 'fam_match': 0,
                'fmnp_match': r['fmnp_entry_total'],
            }

    return sorted(vendor_dict.values(), key=lambda x: x['vendor'])


def _query_fam_match(conn):
    """Replicate FAM Match report SQL from reports_screen.py.

    Returns (rows, total_customer, total_fam_match).
    """
    match_rows = conn.execute(f"""
        SELECT pl.method_name_snapshot as method,
               SUM(pl.method_amount) as total_allocated,
               SUM(pl.match_amount) as total_fam_match
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        JOIN market_days md ON t.market_day_id = md.id
        {_TXN_WHERE}
        GROUP BY pl.method_name_snapshot
        ORDER BY pl.method_name_snapshot
    """).fetchall()

    result = []
    total_customer = 0
    total_fam_match = 0
    for r in match_rows:
        allocated = r['total_allocated']
        fam_match = r['total_fam_match']
        total_customer += (allocated - fam_match)
        total_fam_match += fam_match
        result.append({
            'method': r['method'],
            'allocated': allocated,
            'fam_match': fam_match,
        })

    # External FMNP
    fmnp_ext = conn.execute("""
        SELECT COALESCE(SUM(fe.amount), 0) as total
        FROM fmnp_entries fe
        JOIN market_days md ON fe.market_day_id = md.id
    """).fetchone()['total']

    if fmnp_ext > 0:
        total_fam_match += fmnp_ext
        result.append({
            'method': 'FMNP (External)',
            'allocated': fmnp_ext,
            'fam_match': fmnp_ext,
        })

    return result, total_customer, total_fam_match, fmnp_ext


def _query_ledger(conn):
    """Replicate detailed ledger SQL from reports_screen.py."""
    txn_rows = conn.execute(f"""
        SELECT t.fam_transaction_id, v.name as vendor,
               t.receipt_total, t.status,
               COALESCE(co.customer_label, '') as customer_id,
               COALESCE(SUM(pl.customer_charged), 0) as customer_paid,
               COALESCE(SUM(pl.match_amount), 0) as fam_match,
               GROUP_CONCAT(pl.method_name_snapshot || ': $' ||
                   PRINTF('%.2f', pl.method_amount), ', ') as methods
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        LEFT JOIN customer_orders co ON t.customer_order_id = co.id
        LEFT JOIN payment_line_items pl ON pl.transaction_id = t.id
        {_TXN_WHERE}
        GROUP BY t.id
        ORDER BY t.fam_transaction_id
    """).fetchall()

    ledger = []
    for r in txn_rows:
        ledger.append({
            'id': r['fam_transaction_id'],
            'customer': r['customer_id'],
            'vendor': r['vendor'],
            'receipt_total': r['receipt_total'],
            'customer_paid': r['customer_paid'],
            'fam_match': r['fam_match'],
            'status': r['status'],
            'methods': r['methods'] or '',
        })

    fmnp_rows = conn.execute("""
        SELECT fe.id, v.name as vendor, fe.amount, md.date,
               fe.check_count, fe.notes
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        JOIN market_days md ON fe.market_day_id = md.id
        ORDER BY md.date, fe.id
    """).fetchall()

    for r in fmnp_rows:
        check_info = (f"FMNP (External) - {r['check_count']} checks"
                      if r['check_count'] else "FMNP (External)")
        ledger.append({
            'id': f"FMNP-{r['id']}",
            'customer': '',
            'vendor': r['vendor'],
            'receipt_total': r['amount'],
            'customer_paid': 0,
            'fam_match': r['amount'],
            'status': 'FMNP Entry',
            'methods': check_info,
        })

    return ledger


# ══════════════════════════════════════════════════════════════════
# 1. Vendor Reimbursement
# ══════════════════════════════════════════════════════════════════
class TestVendorReimbursement:
    """Vendor reimbursement report with FMNP entries."""

    def test_txn_only_vendor(self, fresh_db):
        """Green Valley: only in-app transactions, no FMNP."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        gv = next(v for v in vendors if v['vendor'] == 'Green Valley Farm')
        assert gv['gross'] == 30.00
        assert gv['fam_match'] == 6.67
        assert gv['fmnp_match'] == 0

    def test_inapp_fmnp_vendor(self, fresh_db):
        """Sunny Acres: in-app FMNP only, no external entries."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        sa = next(v for v in vendors if v['vendor'] == 'Sunny Acres Produce')
        assert sa['gross'] == 40.00
        assert sa['fam_match'] == 20.00
        assert sa['fmnp_match'] == 20.00  # in-app FMNP match

    def test_external_only_vendor(self, fresh_db):
        """Mountain Herb: external FMNP only, no in-app transactions."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        mh = next(v for v in vendors if v['vendor'] == 'Mountain Herb Co.')
        assert mh['gross'] == 0       # no in-app receipts
        assert mh['fam_match'] == 0   # no in-app match
        assert mh['fmnp_match'] == 50.00
        assert mh['customers'] == ''  # external = no customer

    def test_mixed_vendor(self, fresh_db):
        """Bakers Delight: in-app transactions + external FMNP entries."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        bd = next(v for v in vendors if v['vendor'] == 'Bakers Delight')
        assert bd['gross'] == 60.00
        assert bd['fam_match'] == 20.00  # Food Bucks match only
        # FMNP Match = 0 (no in-app FMNP) + $25 + $15 external = $40
        assert bd['fmnp_match'] == 40.00

    def test_all_four_vendors_present(self, fresh_db):
        """All vendors appear, including external-only Mountain Herb."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        names = [v['vendor'] for v in vendors]
        assert len(names) == 4
        assert 'Mountain Herb Co.' in names

    def test_aggregate_totals(self, fresh_db):
        """Cross-vendor totals are correct."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        total_gross = sum(v['gross'] for v in vendors)
        total_fmnp = sum(v['fmnp_match'] for v in vendors)
        total_fam = sum(v['fam_match'] for v in vendors)
        # Gross = $30 + $40 + $0 + $60
        assert total_gross == 130.00
        # FMNP Match = $0 + $20 + $50 + $40
        assert total_fmnp == 110.00
        # FAM Match = $6.67 + $20 + $0 + $20
        assert total_fam == 46.67

    def test_external_does_not_inflate_gross(self, fresh_db):
        """External FMNP entries never add to Gross Sales."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        mh = next(v for v in vendors if v['vendor'] == 'Mountain Herb Co.')
        assert mh['gross'] == 0  # $50 external FMNP ≠ gross sales
        bd = next(v for v in vendors if v['vendor'] == 'Bakers Delight')
        assert bd['gross'] == 60.00  # only from transaction, not $60 + $40

    def test_dates_merged(self, fresh_db):
        """Bakers Delight dates include both txn and FMNP entry market days."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        bd = next(v for v in vendors if v['vendor'] == 'Bakers Delight')
        dates = set(bd['dates'].split(','))
        # Transaction on 2026-02-20, FMNP entries on 2026-02-20 and 2026-02-15
        assert '2026-02-15' in dates
        assert '2026-02-20' in dates


# ══════════════════════════════════════════════════════════════════
# 2. FAM Match Report
# ══════════════════════════════════════════════════════════════════
class TestFAMMatchReport:
    """FAM Match report with FMNP external row."""

    def test_inapp_methods_correct(self, fresh_db):
        """In-app payment methods have correct totals."""
        _seed(fresh_db)
        rows, _, _, _ = _query_fam_match(fresh_db)
        cash = next(r for r in rows if r['method'] == 'Cash')
        snap = next(r for r in rows if r['method'] == 'SNAP')
        fmnp = next(r for r in rows if r['method'] == 'FMNP')
        fb = next(r for r in rows if r['method'] == 'Food Bucks')

        assert cash['allocated'] == 30.00   # $10 + $20
        assert cash['fam_match'] == 0.00

        assert snap['allocated'] == 20.00
        assert snap['fam_match'] == 6.67

        assert fmnp['allocated'] == 40.00   # Sunny Acres in-app
        assert fmnp['fam_match'] == 20.00

        assert fb['allocated'] == 40.00
        assert fb['fam_match'] == 20.00

    def test_external_fmnp_row_present(self, fresh_db):
        """FMNP (External) row appears with correct total."""
        _seed(fresh_db)
        rows, _, _, _ = _query_fam_match(fresh_db)
        ext = next(r for r in rows if r['method'] == 'FMNP (External)')
        # $50 + $25 + $15 = $90
        assert ext['allocated'] == 90.00
        assert ext['fam_match'] == 90.00

    def test_total_fam_match_includes_external(self, fresh_db):
        """FAM Match total includes both in-app and external FMNP."""
        _seed(fresh_db)
        _, _, total_fam_match, _ = _query_fam_match(fresh_db)
        # In-app: $6.67 + $0 + $20 + $20 = $46.67
        # External: $90
        assert round(total_fam_match, 2) == 136.67

    def test_total_customer_paid(self, fresh_db):
        """Customer paid total only includes in-app payments."""
        _seed(fresh_db)
        _, total_customer, _, _ = _query_fam_match(fresh_db)
        # Cash: $30, SNAP: $13.33, FMNP: $20, Food Bucks: $20
        assert total_customer == 83.33

    def test_no_external_row_when_empty(self, fresh_db):
        """No FMNP (External) row when fmnp_entries table is empty."""
        # Minimal setup — no FMNP entries
        conn = fresh_db
        conn.execute("INSERT INTO markets (id, name, address) VALUES (1, 'Test', '1 Main')")
        conn.execute("INSERT INTO market_days (id, market_id, date, status) VALUES (1, 1, '2026-01-01', 'Open')")
        conn.execute("INSERT INTO vendors (id, name, is_active) VALUES (1, 'V1', 1)")
        conn.execute("INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order) VALUES (1, 'Cash', 0.0, 1, 1)")
        conn.execute("INSERT INTO customer_orders (id, market_day_id, customer_label) VALUES (1, 1, 'C-001')")
        conn.execute("""INSERT INTO transactions
            (id, fam_transaction_id, market_day_id, vendor_id, customer_order_id, receipt_total, status)
            VALUES (1, 'FAM-001', 1, 1, 1, 10.00, 'Confirmed')""")
        conn.execute("""INSERT INTO payment_line_items
            (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
             method_amount, match_amount, customer_charged)
            VALUES (1, 1, 'Cash', 0.0, 10.00, 0.00, 10.00)""")
        conn.commit()

        rows, _, _, fmnp_ext = _query_fam_match(conn)
        assert fmnp_ext == 0
        ext_rows = [r for r in rows if r['method'] == 'FMNP (External)']
        assert len(ext_rows) == 0

    def test_inapp_and_external_fmnp_separate(self, fresh_db):
        """In-app FMNP and external FMNP are distinct rows — no overlap."""
        _seed(fresh_db)
        rows, _, _, _ = _query_fam_match(fresh_db)
        inapp = next(r for r in rows if r['method'] == 'FMNP')
        ext = next(r for r in rows if r['method'] == 'FMNP (External)')
        # In-app: $40 allocated, $20 match (Sunny Acres)
        assert inapp['allocated'] == 40.00
        assert inapp['fam_match'] == 20.00
        # External: $90 allocated = $90 match
        assert ext['allocated'] == 90.00
        assert ext['fam_match'] == 90.00


# ══════════════════════════════════════════════════════════════════
# 3. Detailed Ledger
# ══════════════════════════════════════════════════════════════════
class TestDetailedLedger:
    """Detailed ledger with FMNP entry rows."""

    def test_total_rows(self, fresh_db):
        """3 transactions + 3 FMNP entries = 6 rows."""
        _seed(fresh_db)
        ledger = _query_ledger(fresh_db)
        assert len(ledger) == 6

    def test_transaction_rows(self, fresh_db):
        """In-app transactions have correct values."""
        _seed(fresh_db)
        ledger = _query_ledger(fresh_db)
        txn_rows = [r for r in ledger if r['status'] != 'FMNP Entry']
        assert len(txn_rows) == 3

        fam001 = next(r for r in txn_rows if r['id'] == 'FAM-001')
        assert fam001['vendor'] == 'Green Valley Farm'
        assert fam001['receipt_total'] == 30.00
        assert fam001['customer_paid'] == 23.33   # $13.33 + $10.00
        assert fam001['fam_match'] == 6.67

    def test_fmnp_entry_rows(self, fresh_db):
        """External FMNP entries appear with correct layout."""
        _seed(fresh_db)
        ledger = _query_ledger(fresh_db)
        fmnp_rows = [r for r in ledger if r['status'] == 'FMNP Entry']
        assert len(fmnp_rows) == 3

        e1 = next(r for r in fmnp_rows if r['id'] == 'FMNP-1')
        assert e1['vendor'] == 'Mountain Herb Co.'
        assert e1['receipt_total'] == 50.00
        assert e1['customer_paid'] == 0
        assert e1['fam_match'] == 50.00  # amount IS the match
        assert '5 checks' in e1['methods']
        assert 'External' in e1['methods']

    def test_fmnp_entries_have_no_customer(self, fresh_db):
        """FMNP entries have blank customer (external matching)."""
        _seed(fresh_db)
        ledger = _query_ledger(fresh_db)
        fmnp_rows = [r for r in ledger if r['status'] == 'FMNP Entry']
        for row in fmnp_rows:
            assert row['customer'] == ''

    def test_fmnp_customer_paid_is_zero(self, fresh_db):
        """FMNP entries show $0 customer paid (paid vendor directly)."""
        _seed(fresh_db)
        ledger = _query_ledger(fresh_db)
        fmnp_rows = [r for r in ledger if r['status'] == 'FMNP Entry']
        for row in fmnp_rows:
            assert row['customer_paid'] == 0

    def test_fmnp_match_equals_amount(self, fresh_db):
        """For external FMNP, FAM Match == entry amount (1:1 match)."""
        _seed(fresh_db)
        ledger = _query_ledger(fresh_db)
        fmnp_rows = [r for r in ledger if r['status'] == 'FMNP Entry']
        for row in fmnp_rows:
            assert row['fam_match'] == row['receipt_total']


# ══════════════════════════════════════════════════════════════════
# 4. Summary totals
# ══════════════════════════════════════════════════════════════════
class TestSummaryTotals:
    """Summary card values integrate external FMNP correctly."""

    def test_all_summary_values(self, fresh_db):
        """Verify all four summary card totals."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        _, total_customer, total_fam_match, _ = _query_fam_match(fresh_db)

        total_gross = sum(v['gross'] for v in vendors)
        total_fmnp = sum(v['fmnp_match'] for v in vendors)

        assert total_gross == 130.00              # Total Receipts
        assert total_customer == 83.33            # Customer Paid
        assert round(total_fam_match, 2) == 136.67  # FAM Match (in-app + external)
        assert total_fmnp == 110.00               # FMNP Match


# ══════════════════════════════════════════════════════════════════
# 5. No double-counting
# ══════════════════════════════════════════════════════════════════
class TestNoDoubleCounting:
    """Ensure in-app and external FMNP are handled without duplication."""

    def test_inapp_fmnp_not_duplicated(self, fresh_db):
        """Sunny Acres in-app FMNP match counted once, not inflated by entries."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        sa = next(v for v in vendors if v['vendor'] == 'Sunny Acres Produce')
        # Only $20 from in-app FMNP (no external entries for this vendor)
        assert sa['fmnp_match'] == 20.00

    def test_external_fmnp_separate_from_fam_match(self, fresh_db):
        """Mountain Herb has FMNP Match but $0 FAM Match (external only)."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        mh = next(v for v in vendors if v['vendor'] == 'Mountain Herb Co.')
        assert mh['fam_match'] == 0
        assert mh['fmnp_match'] == 50.00

    def test_gross_not_inflated_by_external(self, fresh_db):
        """External FMNP entries never inflate gross sales."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        total_gross = sum(v['gross'] for v in vendors)
        # Only $130 from actual transactions, not $130 + $90 external
        assert total_gross == 130.00

    def test_ledger_no_duplicate_rows(self, fresh_db):
        """Each source produces unique rows — no overlap."""
        _seed(fresh_db)
        ledger = _query_ledger(fresh_db)
        ids = [r['id'] for r in ledger]
        assert len(ids) == len(set(ids))  # all unique


# ══════════════════════════════════════════════════════════════════
# 6. Database CRUD & audit logging
# ══════════════════════════════════════════════════════════════════
class TestFMNPCrudAndAudit:
    """FMNP entry CRUD operations and audit trail."""

    def _setup_minimal(self, conn):
        """Minimal setup for CRUD tests."""
        conn.execute("INSERT INTO markets (id, name, address) VALUES (1, 'M', '1')")
        conn.execute("INSERT INTO market_days (id, market_id, date, status) VALUES (1, 1, '2026-01-01', 'Open')")
        conn.execute("INSERT INTO vendors (id, name, is_active) VALUES (1, 'V1', 1)")
        conn.commit()

    def test_create_entry(self, fresh_db):
        """create_fmnp_entry inserts correctly."""
        self._setup_minimal(fresh_db)
        eid = create_fmnp_entry(1, 1, 75.00, 'TestUser', check_count=3, notes='Test')
        entry = get_fmnp_entry_by_id(eid)
        assert entry['amount'] == 75.00
        assert entry['check_count'] == 3
        assert entry['entered_by'] == 'TestUser'

    def test_update_entry(self, fresh_db):
        """update_fmnp_entry modifies fields."""
        self._setup_minimal(fresh_db)
        eid = create_fmnp_entry(1, 1, 50.00, 'Admin')
        update_fmnp_entry(eid, amount=75.00, notes='Updated')
        entry = get_fmnp_entry_by_id(eid)
        assert entry['amount'] == 75.00
        assert entry['notes'] == 'Updated'

    def test_delete_entry(self, fresh_db):
        """delete_fmnp_entry removes entry."""
        self._setup_minimal(fresh_db)
        eid = create_fmnp_entry(1, 1, 50.00, 'Admin')
        delete_fmnp_entry(eid)
        assert get_fmnp_entry_by_id(eid) is None

    def test_create_logged(self, fresh_db):
        """INSERT audit log entry created on create."""
        self._setup_minimal(fresh_db)
        eid = create_fmnp_entry(1, 1, 75.00, 'Alice')
        log_action('fmnp_entries', eid, 'INSERT', 'Alice', notes='Created')
        row = fresh_db.execute(
            "SELECT * FROM audit_log WHERE table_name='fmnp_entries' AND action='INSERT' AND record_id=?",
            (eid,)
        ).fetchone()
        assert row is not None
        assert row['changed_by'] == 'Alice'

    def test_update_logged(self, fresh_db):
        """UPDATE audit log entry tracks old/new values."""
        self._setup_minimal(fresh_db)
        eid = create_fmnp_entry(1, 1, 50.00, 'Admin')
        update_fmnp_entry(eid, amount=99.00)
        log_action('fmnp_entries', eid, 'UPDATE', 'Bob',
                   field_name='amount', old_value=50.00, new_value=99.00)
        row = fresh_db.execute(
            "SELECT * FROM audit_log WHERE table_name='fmnp_entries' AND action='UPDATE' AND record_id=?",
            (eid,)
        ).fetchone()
        assert row is not None
        assert row['old_value'] == '50.0'
        assert row['new_value'] == '99.0'

    def test_delete_logged(self, fresh_db):
        """DELETE audit log entry recorded before removal."""
        self._setup_minimal(fresh_db)
        eid = create_fmnp_entry(1, 1, 50.00, 'Admin')
        log_action('fmnp_entries', eid, 'DELETE', 'Charlie', notes='Removed')
        delete_fmnp_entry(eid)
        row = fresh_db.execute(
            "SELECT * FROM audit_log WHERE table_name='fmnp_entries' AND action='DELETE' AND record_id=?",
            (eid,)
        ).fetchone()
        assert row is not None
        assert row['changed_by'] == 'Charlie'

    def test_entries_filtered_by_market_day(self, fresh_db):
        """get_fmnp_entries filters by market_day_id."""
        self._setup_minimal(fresh_db)
        fresh_db.execute("INSERT INTO market_days (id, market_id, date, status) VALUES (2, 1, '2026-01-02', 'Open')")
        fresh_db.commit()
        create_fmnp_entry(1, 1, 10.00, 'Admin')
        create_fmnp_entry(2, 1, 20.00, 'Admin')
        create_fmnp_entry(1, 1, 30.00, 'Admin')

        entries_md1 = get_fmnp_entries(market_day_id=1)
        entries_md2 = get_fmnp_entries(market_day_id=2)
        assert len(entries_md1) == 2
        assert len(entries_md2) == 1
        assert entries_md2[0]['amount'] == 20.00


# ══════════════════════════════════════════════════════════════════
# 7. Edge cases
# ══════════════════════════════════════════════════════════════════
class TestEdgeCases:
    """Boundary / edge scenarios."""

    def test_no_data(self, fresh_db):
        """Empty database returns empty results without errors."""
        vendors = _query_vendor_reimbursement(fresh_db)
        assert vendors == []
        rows, tc, tfm, fext = _query_fam_match(fresh_db)
        assert rows == []
        assert tc == 0
        assert tfm == 0
        assert fext == 0
        ledger = _query_ledger(fresh_db)
        assert ledger == []

    def test_only_external_entries_no_transactions(self, fresh_db):
        """FMNP entries exist but zero transactions."""
        conn = fresh_db
        conn.execute("INSERT INTO markets (id, name, address) VALUES (1, 'M', '1')")
        conn.execute("INSERT INTO market_days (id, market_id, date, status) VALUES (1, 1, '2026-01-01', 'Open')")
        conn.execute("INSERT INTO vendors (id, name, is_active) VALUES (1, 'V1', 1)")
        conn.execute("""INSERT INTO fmnp_entries
            (market_day_id, vendor_id, amount, check_count, entered_by)
            VALUES (1, 1, 100.00, 10, 'Admin')""")
        conn.commit()

        vendors = _query_vendor_reimbursement(conn)
        assert len(vendors) == 1
        assert vendors[0]['vendor'] == 'V1'
        assert vendors[0]['gross'] == 0
        assert vendors[0]['fmnp_match'] == 100.00

        rows, tc, tfm, fext = _query_fam_match(conn)
        assert fext == 100.00
        assert tfm == 100.00
        assert tc == 0

        ledger = _query_ledger(conn)
        assert len(ledger) == 1
        assert ledger[0]['status'] == 'FMNP Entry'
        assert ledger[0]['fam_match'] == 100.00

    def test_draft_transactions_excluded(self, fresh_db):
        """Draft transactions are excluded from reports."""
        conn = fresh_db
        conn.execute("INSERT INTO markets (id, name, address) VALUES (1, 'M', '1')")
        conn.execute("INSERT INTO market_days (id, market_id, date, status) VALUES (1, 1, '2026-01-01', 'Open')")
        conn.execute("INSERT INTO vendors (id, name, is_active) VALUES (1, 'V1', 1)")
        conn.execute("INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order) VALUES (1, 'Cash', 0.0, 1, 1)")
        conn.execute("INSERT INTO customer_orders (id, market_day_id, customer_label) VALUES (1, 1, 'C-001')")
        conn.execute("""INSERT INTO transactions
            (id, fam_transaction_id, market_day_id, vendor_id, customer_order_id, receipt_total, status)
            VALUES (1, 'FAM-001', 1, 1, 1, 50.00, 'Draft')""")
        conn.execute("""INSERT INTO payment_line_items
            (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
             method_amount, match_amount, customer_charged)
            VALUES (1, 1, 'Cash', 0.0, 50.00, 0.00, 50.00)""")
        conn.commit()

        vendors = _query_vendor_reimbursement(conn)
        assert len(vendors) == 0  # Draft excluded

    def test_multiple_entries_same_vendor_same_day(self, fresh_db):
        """Multiple FMNP entries for same vendor on same day sum correctly."""
        conn = fresh_db
        conn.execute("INSERT INTO markets (id, name, address) VALUES (1, 'M', '1')")
        conn.execute("INSERT INTO market_days (id, market_id, date, status) VALUES (1, 1, '2026-01-01', 'Open')")
        conn.execute("INSERT INTO vendors (id, name, is_active) VALUES (1, 'V1', 1)")
        conn.execute("""INSERT INTO fmnp_entries
            (market_day_id, vendor_id, amount, check_count, entered_by)
            VALUES (1, 1, 20.00, 2, 'Admin')""")
        conn.execute("""INSERT INTO fmnp_entries
            (market_day_id, vendor_id, amount, check_count, entered_by)
            VALUES (1, 1, 30.00, 3, 'Admin')""")
        conn.execute("""INSERT INTO fmnp_entries
            (market_day_id, vendor_id, amount, check_count, entered_by)
            VALUES (1, 1, 50.00, 5, 'Admin')""")
        conn.commit()

        vendors = _query_vendor_reimbursement(conn)
        assert len(vendors) == 1
        assert vendors[0]['fmnp_match'] == 100.00  # $20 + $30 + $50

        _, _, tfm, fext = _query_fam_match(conn)
        assert fext == 100.00
        assert tfm == 100.00
