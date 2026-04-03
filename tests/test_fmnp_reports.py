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
        VALUES (1, 'FAM-001', 1, 1, 1, 3000, 'Confirmed')""")
    conn.execute("""INSERT INTO payment_line_items
        (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
         method_amount, match_amount, customer_charged)
        VALUES (1, 1, 'SNAP', 50.0, 2000, 667, 1333)""")
    conn.execute("""INSERT INTO payment_line_items
        (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
         method_amount, match_amount, customer_charged)
        VALUES (1, 2, 'Cash', 0.0, 1000, 0, 1000)""")

    # FAM-002: Sunny Acres — $40, FMNP in-app
    conn.execute("""INSERT INTO transactions
        (id, fam_transaction_id, market_day_id, vendor_id, customer_order_id, receipt_total, status)
        VALUES (2, 'FAM-002', 1, 2, 2, 4000, 'Confirmed')""")
    conn.execute("""INSERT INTO payment_line_items
        (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
         method_amount, match_amount, customer_charged)
        VALUES (2, 3, 'FMNP', 100.0, 4000, 2000, 2000)""")

    # FAM-003: Bakers Delight — $60, Food Bucks $40 + Cash $20
    conn.execute("""INSERT INTO transactions
        (id, fam_transaction_id, market_day_id, vendor_id, customer_order_id, receipt_total, status)
        VALUES (3, 'FAM-003', 2, 4, 3, 6000, 'Confirmed')""")
    conn.execute("""INSERT INTO payment_line_items
        (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
         method_amount, match_amount, customer_charged)
        VALUES (3, 4, 'Food Bucks', 100.0, 4000, 2000, 2000)""")
    conn.execute("""INSERT INTO payment_line_items
        (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
         method_amount, match_amount, customer_charged)
        VALUES (3, 2, 'Cash', 0.0, 2000, 0, 2000)""")

    # ── External FMNP Entries ──
    conn.execute("""INSERT INTO fmnp_entries
        (id, market_day_id, vendor_id, amount, check_count, entered_by, notes)
        VALUES (1, 1, 3, 5000, 5, 'Admin', 'Five $10 checks')""")
    conn.execute("""INSERT INTO fmnp_entries
        (id, market_day_id, vendor_id, amount, check_count, entered_by, notes)
        VALUES (2, 2, 4, 2500, 2, 'Admin', 'Two checks')""")
    conn.execute("""INSERT INTO fmnp_entries
        (id, market_day_id, vendor_id, amount, check_count, entered_by, notes)
        VALUES (3, 1, 4, 1500, 1, 'Admin', 'One check')""")

    conn.commit()


# ──────────────────────────────────────────────────────────────────
# Query helpers (replicate reports_screen.py SQL + merge logic)
# ──────────────────────────────────────────────────────────────────
_TXN_WHERE = "WHERE t.status IN ('Confirmed', 'Adjusted')"


def _query_vendor_reimbursement(conn):
    """Replicate vendor reimbursement SQL + merge from reports_screen.py.

    Groups by (market, vendor) — one row per unique market+vendor pair.
    """
    vendor_rows = conn.execute(f"""
        SELECT v.name as vendor,
               m.name as market_name,
               COALESCE(SUM(t.receipt_total), 0) as gross_sales,
               GROUP_CONCAT(DISTINCT md.date) as transaction_dates,
               GROUP_CONCAT(DISTINCT co.customer_label) as customer_ids
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        LEFT JOIN customer_orders co ON t.customer_order_id = co.id
        {_TXN_WHERE}
        GROUP BY m.id, v.id, v.name ORDER BY m.name, v.name
    """).fetchall()

    match_rows = conn.execute(f"""
        SELECT v.name as vendor,
               m.name as market_name,
               COALESCE(SUM(pl.match_amount), 0) as fam_match,
               COALESCE(SUM(CASE WHEN pl.method_name_snapshot = 'FMNP'
                                 THEN pl.match_amount ELSE 0 END), 0) as fmnp_match
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        LEFT JOIN customer_orders co ON t.customer_order_id = co.id
        {_TXN_WHERE}
        GROUP BY m.id, v.id, v.name
    """).fetchall()

    match_by_vendor = {
        (r['market_name'], r['vendor']): {
            'fam_match': r['fam_match'], 'fmnp_match': r['fmnp_match']
        }
        for r in match_rows
    }

    vendor_dict = {}
    for r in vendor_rows:
        key = (r['market_name'], r['vendor'])
        vm = match_by_vendor.get(key, {})
        vendor_dict[key] = {
            'vendor': r['vendor'],
            'market_name': r['market_name'],
            'customers': r['customer_ids'] or '',
            'dates': r['transaction_dates'] or '',
            'gross': r['gross_sales'],
            'fam_match': vm.get('fam_match', 0),
            'fmnp_match': vm.get('fmnp_match', 0),
        }

    # Merge external FMNP entries (exclude soft-deleted)
    fmnp_rows = conn.execute("""
        SELECT v.name as vendor,
               m.name as market_name,
               COALESCE(SUM(fe.amount), 0) as fmnp_entry_total,
               GROUP_CONCAT(DISTINCT md.date) as fmnp_dates
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        JOIN market_days md ON fe.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        WHERE fe.status = 'Active'
        GROUP BY m.id, v.id, v.name
    """).fetchall()

    for r in fmnp_rows:
        key = (r['market_name'], r['vendor'])
        if key in vendor_dict:
            vendor_dict[key]['fmnp_match'] += r['fmnp_entry_total']
            existing = set(vendor_dict[key]['dates'].split(',')) \
                if vendor_dict[key]['dates'] else set()
            new_dates = set((r['fmnp_dates'] or '').split(','))
            all_dates = (existing | new_dates) - {''}
            vendor_dict[key]['dates'] = ','.join(sorted(all_dates))
        else:
            vendor_dict[key] = {
                'vendor': r['vendor'], 'market_name': r['market_name'],
                'customers': '',
                'dates': r['fmnp_dates'] or '',
                'gross': 0, 'fam_match': 0,
                'fmnp_match': r['fmnp_entry_total'],
            }

    return sorted(vendor_dict.values(), key=lambda x: (x['market_name'], x['vendor']))


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

    # External FMNP (exclude soft-deleted)
    fmnp_ext = conn.execute("""
        SELECT COALESCE(SUM(fe.amount), 0) as total
        FROM fmnp_entries fe
        JOIN market_days md ON fe.market_day_id = md.id
        WHERE fe.status = 'Active'
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
                   PRINTF('%.2f', pl.method_amount / 100.0), ', ') as methods
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
        WHERE fe.status = 'Active'
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
        gv = next(v for v in vendors
                  if v['vendor'] == 'Green Valley Farm'
                  and v['market_name'] == 'Downtown Market')
        assert gv['gross'] == 3000
        assert gv['fam_match'] == 667
        assert gv['fmnp_match'] == 0

    def test_inapp_fmnp_vendor(self, fresh_db):
        """Sunny Acres: in-app FMNP only, no external entries."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        sa = next(v for v in vendors
                  if v['vendor'] == 'Sunny Acres Produce'
                  and v['market_name'] == 'Downtown Market')
        assert sa['gross'] == 4000
        assert sa['fam_match'] == 2000
        assert sa['fmnp_match'] == 2000  # in-app FMNP match

    def test_external_only_vendor(self, fresh_db):
        """Mountain Herb: external FMNP only, no in-app transactions."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        mh = next(v for v in vendors
                  if v['vendor'] == 'Mountain Herb Co.'
                  and v['market_name'] == 'Downtown Market')
        assert mh['gross'] == 0       # no in-app receipts
        assert mh['fam_match'] == 0   # no in-app match
        assert mh['fmnp_match'] == 5000
        assert mh['customers'] == ''  # external = no customer

    def test_mixed_vendor_riverside(self, fresh_db):
        """Bakers Delight at Riverside: txn $60 + FMNP $25."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        bd_rv = next(v for v in vendors
                     if v['vendor'] == 'Bakers Delight'
                     and v['market_name'] == 'Riverside Market')
        assert bd_rv['gross'] == 6000
        assert bd_rv['fam_match'] == 2000  # Food Bucks match only
        assert bd_rv['fmnp_match'] == 2500  # external FMNP at Riverside

    def test_mixed_vendor_downtown(self, fresh_db):
        """Bakers Delight at Downtown: FMNP $15 only (no transactions)."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        bd_dt = next(v for v in vendors
                     if v['vendor'] == 'Bakers Delight'
                     and v['market_name'] == 'Downtown Market')
        assert bd_dt['gross'] == 0
        assert bd_dt['fam_match'] == 0
        assert bd_dt['fmnp_match'] == 1500

    def test_all_market_vendor_pairs_present(self, fresh_db):
        """All (market, vendor) pairs appear — 5 rows total."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        pairs = [(v['market_name'], v['vendor']) for v in vendors]
        assert len(pairs) == 5
        assert ('Downtown Market', 'Green Valley Farm') in pairs
        assert ('Downtown Market', 'Sunny Acres Produce') in pairs
        assert ('Downtown Market', 'Mountain Herb Co.') in pairs
        assert ('Downtown Market', 'Bakers Delight') in pairs
        assert ('Riverside Market', 'Bakers Delight') in pairs

    def test_aggregate_totals(self, fresh_db):
        """Cross-vendor totals are correct (unchanged by grouping)."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        total_gross = sum(v['gross'] for v in vendors)
        total_fmnp = sum(v['fmnp_match'] for v in vendors)
        total_fam = sum(v['fam_match'] for v in vendors)
        # Gross = $30 + $40 + $0 + $0 + $60
        assert total_gross == 13000
        # FMNP Match = $0 + $20 + $50 + $15 + $25
        assert total_fmnp == 11000
        # FAM Match = $6.67 + $20 + $0 + $0 + $20
        assert total_fam == 4667

    def test_external_does_not_inflate_gross(self, fresh_db):
        """External FMNP entries never add to Gross Sales."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        mh = next(v for v in vendors
                  if v['vendor'] == 'Mountain Herb Co.')
        assert mh['gross'] == 0  # $50 external FMNP ≠ gross sales
        bd_rv = next(v for v in vendors
                     if v['vendor'] == 'Bakers Delight'
                     and v['market_name'] == 'Riverside Market')
        assert bd_rv['gross'] == 6000  # only from transaction

    def test_dates_per_market(self, fresh_db):
        """Bakers Delight dates are split by market."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        bd_rv = next(v for v in vendors
                     if v['vendor'] == 'Bakers Delight'
                     and v['market_name'] == 'Riverside Market')
        assert '2026-02-20' in bd_rv['dates']
        bd_dt = next(v for v in vendors
                     if v['vendor'] == 'Bakers Delight'
                     and v['market_name'] == 'Downtown Market')
        assert '2026-02-15' in bd_dt['dates']


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

        assert cash['allocated'] == 3000   # $10 + $20
        assert cash['fam_match'] == 0

        assert snap['allocated'] == 2000
        assert snap['fam_match'] == 667

        assert fmnp['allocated'] == 4000   # Sunny Acres in-app
        assert fmnp['fam_match'] == 2000

        assert fb['allocated'] == 4000
        assert fb['fam_match'] == 2000

    def test_external_fmnp_row_present(self, fresh_db):
        """FMNP (External) row appears with correct total."""
        _seed(fresh_db)
        rows, _, _, _ = _query_fam_match(fresh_db)
        ext = next(r for r in rows if r['method'] == 'FMNP (External)')
        # $50 + $25 + $15 = $90
        assert ext['allocated'] == 9000
        assert ext['fam_match'] == 9000

    def test_total_fam_match_includes_external(self, fresh_db):
        """FAM Match total includes both in-app and external FMNP."""
        _seed(fresh_db)
        _, _, total_fam_match, _ = _query_fam_match(fresh_db)
        # In-app: $6.67 + $0 + $20 + $20 = $46.67
        # External: $90
        assert total_fam_match == 13667

    def test_total_customer_paid(self, fresh_db):
        """Customer paid total only includes in-app payments."""
        _seed(fresh_db)
        _, total_customer, _, _ = _query_fam_match(fresh_db)
        # Cash: $30, SNAP: $13.33, FMNP: $20, Food Bucks: $20
        assert total_customer == 8333

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
            VALUES (1, 'FAM-001', 1, 1, 1, 1000, 'Confirmed')""")
        conn.execute("""INSERT INTO payment_line_items
            (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
             method_amount, match_amount, customer_charged)
            VALUES (1, 1, 'Cash', 0.0, 1000, 0, 1000)""")
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
        assert inapp['allocated'] == 4000
        assert inapp['fam_match'] == 2000
        # External: $90 allocated = $90 match
        assert ext['allocated'] == 9000
        assert ext['fam_match'] == 9000


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
        assert fam001['receipt_total'] == 3000
        assert fam001['customer_paid'] == 2333   # $13.33 + $10.00
        assert fam001['fam_match'] == 667

    def test_fmnp_entry_rows(self, fresh_db):
        """External FMNP entries appear with correct layout."""
        _seed(fresh_db)
        ledger = _query_ledger(fresh_db)
        fmnp_rows = [r for r in ledger if r['status'] == 'FMNP Entry']
        assert len(fmnp_rows) == 3

        e1 = next(r for r in fmnp_rows if r['id'] == 'FMNP-1')
        assert e1['vendor'] == 'Mountain Herb Co.'
        assert e1['receipt_total'] == 5000
        assert e1['customer_paid'] == 0
        assert e1['fam_match'] == 5000  # amount IS the match
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

        assert total_gross == 13000              # Total Receipts
        assert total_customer == 8333            # Customer Paid
        assert total_fam_match == 13667          # FAM Match (in-app + external)
        assert total_fmnp == 11000               # FMNP Match


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
        assert sa['fmnp_match'] == 2000

    def test_external_fmnp_separate_from_fam_match(self, fresh_db):
        """Mountain Herb has FMNP Match but $0 FAM Match (external only)."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        mh = next(v for v in vendors if v['vendor'] == 'Mountain Herb Co.')
        assert mh['fam_match'] == 0
        assert mh['fmnp_match'] == 5000

    def test_gross_not_inflated_by_external(self, fresh_db):
        """External FMNP entries never inflate gross sales."""
        _seed(fresh_db)
        vendors = _query_vendor_reimbursement(fresh_db)
        total_gross = sum(v['gross'] for v in vendors)
        # Only $130 from actual transactions, not $130 + $90 external
        assert total_gross == 13000

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
        eid = create_fmnp_entry(1, 1, 7500, 'TestUser', check_count=3, notes='Test')
        entry = get_fmnp_entry_by_id(eid)
        assert entry['amount'] == 7500
        assert entry['check_count'] == 3
        assert entry['entered_by'] == 'TestUser'

    def test_update_entry(self, fresh_db):
        """update_fmnp_entry modifies fields."""
        self._setup_minimal(fresh_db)
        eid = create_fmnp_entry(1, 1, 5000, 'Admin')
        update_fmnp_entry(eid, amount=7500, notes='Updated')
        entry = get_fmnp_entry_by_id(eid)
        assert entry['amount'] == 7500
        assert entry['notes'] == 'Updated'

    def test_delete_entry(self, fresh_db):
        """delete_fmnp_entry soft-deletes: row preserved with status='Deleted'."""
        self._setup_minimal(fresh_db)
        eid = create_fmnp_entry(1, 1, 5000, 'Admin')
        delete_fmnp_entry(eid)
        # Row still exists in the database with status='Deleted'
        row = fresh_db.execute(
            "SELECT status FROM fmnp_entries WHERE id=?", (eid,)
        ).fetchone()
        assert row is not None
        assert row['status'] == 'Deleted'
        # But get_fmnp_entries() no longer returns it
        entries = get_fmnp_entries(market_day_id=1)
        assert all(e['id'] != eid for e in entries)

    def test_create_logged(self, fresh_db):
        """INSERT audit log entry created on create."""
        self._setup_minimal(fresh_db)
        eid = create_fmnp_entry(1, 1, 7500, 'Alice')
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
        eid = create_fmnp_entry(1, 1, 5000, 'Admin')
        update_fmnp_entry(eid, amount=9900)
        log_action('fmnp_entries', eid, 'UPDATE', 'Bob',
                   field_name='amount', old_value=5000, new_value=9900)
        row = fresh_db.execute(
            "SELECT * FROM audit_log WHERE table_name='fmnp_entries' AND action='UPDATE' AND record_id=?",
            (eid,)
        ).fetchone()
        assert row is not None
        assert row['old_value'] == '5000'
        assert row['new_value'] == '9900'

    def test_delete_logged(self, fresh_db):
        """DELETE audit log entry recorded for soft-delete."""
        self._setup_minimal(fresh_db)
        eid = create_fmnp_entry(1, 1, 5000, 'Admin')
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
        create_fmnp_entry(1, 1, 1000, 'Admin')
        create_fmnp_entry(2, 1, 2000, 'Admin')
        create_fmnp_entry(1, 1, 3000, 'Admin')

        entries_md1 = get_fmnp_entries(market_day_id=1)
        entries_md2 = get_fmnp_entries(market_day_id=2)
        assert len(entries_md1) == 2
        assert len(entries_md2) == 1
        assert entries_md2[0]['amount'] == 2000

    def test_deleted_entry_excluded_from_reports(self, fresh_db):
        """Soft-deleted FMNP entries are excluded from report queries."""
        self._setup_minimal(fresh_db)
        eid_keep = create_fmnp_entry(1, 1, 4000, 'Admin')
        eid_del = create_fmnp_entry(1, 1, 6000, 'Admin')
        delete_fmnp_entry(eid_del)

        # Vendor reimbursement query should only include the active entry
        report = _query_vendor_reimbursement(fresh_db)
        assert len(report) == 1
        assert report[0]['fmnp_match'] == 4000  # only the kept entry

        # FAM match query should exclude deleted
        _, _, total_match, fmnp_ext = _query_fam_match(fresh_db)
        assert fmnp_ext == 4000

        # Detailed ledger should exclude deleted
        ledger = _query_ledger(fresh_db)
        fmnp_ids = [r['id'] for r in ledger if str(r['id']).startswith('FMNP-')]
        assert f'FMNP-{eid_keep}' in fmnp_ids
        assert f'FMNP-{eid_del}' not in fmnp_ids


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
            VALUES (1, 1, 10000, 10, 'Admin')""")
        conn.commit()

        vendors = _query_vendor_reimbursement(conn)
        assert len(vendors) == 1
        assert vendors[0]['vendor'] == 'V1'
        assert vendors[0]['gross'] == 0
        assert vendors[0]['fmnp_match'] == 10000

        rows, tc, tfm, fext = _query_fam_match(conn)
        assert fext == 10000
        assert tfm == 10000
        assert tc == 0

        ledger = _query_ledger(conn)
        assert len(ledger) == 1
        assert ledger[0]['status'] == 'FMNP Entry'
        assert ledger[0]['fam_match'] == 10000

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
            VALUES (1, 'FAM-001', 1, 1, 1, 5000, 'Draft')""")
        conn.execute("""INSERT INTO payment_line_items
            (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
             method_amount, match_amount, customer_charged)
            VALUES (1, 1, 'Cash', 0.0, 5000, 0, 5000)""")
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
            VALUES (1, 1, 2000, 2, 'Admin')""")
        conn.execute("""INSERT INTO fmnp_entries
            (market_day_id, vendor_id, amount, check_count, entered_by)
            VALUES (1, 1, 3000, 3, 'Admin')""")
        conn.execute("""INSERT INTO fmnp_entries
            (market_day_id, vendor_id, amount, check_count, entered_by)
            VALUES (1, 1, 5000, 5, 'Admin')""")
        conn.commit()

        vendors = _query_vendor_reimbursement(conn)
        assert len(vendors) == 1
        assert vendors[0]['fmnp_match'] == 10000  # $20 + $30 + $50

        _, _, tfm, fext = _query_fam_match(conn)
        assert fext == 10000
        assert tfm == 10000
