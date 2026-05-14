"""Vendor Reimbursement is keyed by year-month (v2.0.9).

Pre-v2.0.9 the collector emitted one row per (market × vendor) with
all-time cumulative totals.  Month-over-month reconciliation was
impossible — once the calendar month rolled over, new transactions
piled onto the existing row with no way to separate them, and the
``Month`` column showed whichever date sorted alphabetically first
across the vendor's entire history.

v2.0.9 emits one row per (market × vendor × year-month).  Each
calendar month gets its own row so a coordinator can compare
"vendor X — April 2026" to "vendor X — May 2026" at a glance.

Tests cover:

  * Single vendor with transactions spanning two months → two rows.
  * Math identity holds within each monthly row
    (Σ method-cols + FAM Match − Customer Forfeit + FMNP_External
    = Total Due to Vendor).
  * Closed-market-day mutation in a prior month updates THAT
    month's row, not the current month's.
  * Cross-device: two laptops processing the same vendor in May
    produce two separate rows (one per device, both in May).
  * FMNP-only vendor with entries across multiple months produces
    one row per month.
  * Vendor with both transactions and FMNP entries in the SAME
    month produces ONE merged monthly row, not two.
  * Voiding a transaction decreases that month's row totals.
  * Upsert: re-syncing the same vendor across two months produces
    two distinct sheet rows that do NOT overwrite each other.
"""

import pytest

from fam.database.connection import (
    close_connection, get_connection, set_db_path,
)
from fam.database.schema import initialize_database
from fam.database.seed import seed_sample_data
from fam.sync.base import SyncBackend, SyncResult


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_vr_monthly.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    seed_sample_data()
    yield tmp_path
    close_connection()


# ──────────────────────────────────────────────────────────────────
# In-memory sheet backend (upsert-only, no stale-removal modeling).
# Used by the cross-device and upsert-keying tests to exercise the
# real SHEET_KEYS composite-key logic from SyncManager.
# ──────────────────────────────────────────────────────────────────
class InMemorySheetBackend(SyncBackend):

    def __init__(self):
        self.sheets: dict[str, list[dict]] = {}

    def is_configured(self):
        return True

    def validate_connection(self):
        return SyncResult(success=True)

    def upsert_rows(self, sheet_name, rows, key_columns,
                    delete_stale=True):
        if sheet_name not in self.sheets:
            self.sheets[sheet_name] = []
        existing = self.sheets[sheet_name]
        index = {tuple(str(r.get(c, '')) for c in key_columns): i
                 for i, r in enumerate(existing)}
        for row in rows:
            key = tuple(str(row.get(c, '')) for c in key_columns)
            if key in index:
                existing[index[key]] = dict(row)
            else:
                existing.append(dict(row))
        return SyncResult(success=True, rows_synced=len(rows))

    def delete_rows(self, sheet_name, market_code, device_id):
        return SyncResult(success=True, rows_synced=0)

    def read_rows(self, sheet_name, market_code=None, device_id=None):
        return list(self.sheets.get(sheet_name, []))


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────
def _ids():
    """Pull (market_id, vendor_id, payment_method_id, method_name,
    match_percent) for a vendor + non-system payment method.

    Skipping system-managed methods (Unallocated Funds) is essential:
    the v33 zero-amount trigger rejects non-zero customer_charged /
    match_amount on UF rows, so synthetic test PLIs must use a
    regular method like SNAP / JH Food Bucks.
    """
    conn = get_connection()
    market = conn.execute(
        "SELECT id, name FROM markets LIMIT 1").fetchone()
    vendor = conn.execute(
        "SELECT id, name FROM vendors LIMIT 1").fetchone()
    pm = conn.execute(
        "SELECT id, name, match_percent FROM payment_methods "
        "WHERE COALESCE(is_system, 0) = 0 "
        "ORDER BY id LIMIT 1"
    ).fetchone()
    return market, vendor, pm


def _add_txn(md_id, vendor_id, pm_id, pm_name, match_percent,
             receipt_cents=2500, customer_cents=1250,
             match_cents=1250, fam_tid='FAM-TST-00001'):
    """Insert one Confirmed transaction with one payment line item.

    receipt = customer_charged + match_amount by default
    (this is the v2.0.7+ denomination-integrity invariant — the
    customer's physical handout + FAM's match = the vendor's
    receipt total, with no Phase B forfeit).
    """
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO transactions
           (market_day_id, vendor_id, receipt_total, status,
            fam_transaction_id)
           VALUES (?, ?, ?, 'Confirmed', ?)""",
        (md_id, vendor_id, receipt_cents, fam_tid))
    txn_id = cur.lastrowid
    conn.execute(
        """INSERT INTO payment_line_items
           (transaction_id, payment_method_id, method_name_snapshot,
            match_percent_snapshot, method_amount,
            customer_charged, match_amount)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (txn_id, pm_id, pm_name, match_percent,
         receipt_cents, customer_cents, match_cents))
    conn.commit()
    return txn_id


def _open_md(market_id, md_date):
    from fam.models.market_day import create_market_day
    return create_market_day(market_id, md_date, opened_by='Tester')


def _set_identity(market_code='TST', device_id='dev-01'):
    from fam.utils.app_settings import set_setting
    set_setting('market_code', market_code)
    set_setting('device_id', device_id)


# ──────────────────────────────────────────────────────────────────
# 1. Two months → two rows
# ──────────────────────────────────────────────────────────────────
class TestMonthlySplit:
    """One vendor with transactions spanning two months produces two
    distinct rows.  Pre-v2.0.9 they merged into one cumulative row."""

    def test_two_months_two_rows(self):
        _set_identity()
        market, vendor, pm = _ids()
        md_apr = _open_md(market['id'], '2026-04-15')
        md_may = _open_md(market['id'], '2026-05-15')
        _add_txn(md_apr, vendor['id'], pm['id'], pm['name'],
                 pm['match_percent'],
                 receipt_cents=1000, customer_cents=500, match_cents=500,
                 fam_tid='FAM-TST-20260415-0001')
        _add_txn(md_may, vendor['id'], pm['id'], pm['name'],
                 pm['match_percent'],
                 receipt_cents=2000, customer_cents=1000, match_cents=1000,
                 fam_tid='FAM-TST-20260515-0001')

        from fam.sync.data_collector import collect_sync_data
        rows = collect_sync_data()['Vendor Reimbursement']
        vrows = [r for r in rows if r['Vendor'] == vendor['name']]
        assert len(vrows) == 2, (
            f"Expected one row per month; got {len(vrows)}: "
            f"{[(r['Month'], r['Total Due to Vendor']) for r in vrows]}")

        by_month = {r['Year-Month']: r for r in vrows}
        assert set(by_month.keys()) == {'2026-04', '2026-05'}
        assert by_month['2026-04']['Total Due to Vendor'] == 10.00
        assert by_month['2026-05']['Total Due to Vendor'] == 20.00
        # Month label is the human-readable "Month YYYY" form.
        assert by_month['2026-04']['Month'] == 'April 2026'
        assert by_month['2026-05']['Month'] == 'May 2026'

    def test_three_months_three_rows(self):
        """Sanity: April + May + June produces three monthly rows."""
        _set_identity()
        market, vendor, pm = _ids()
        for i, d in enumerate(['2026-04-01', '2026-05-01', '2026-06-01']):
            md = _open_md(market['id'], d)
            _add_txn(md, vendor['id'], pm['id'], pm['name'],
                     pm['match_percent'],
                     fam_tid=f'FAM-TST-{d.replace("-", "")}-0001')

        from fam.sync.data_collector import collect_sync_data
        rows = collect_sync_data()['Vendor Reimbursement']
        vrows = [r for r in rows if r['Vendor'] == vendor['name']]
        assert len(vrows) == 3
        assert {r['Year-Month'] for r in vrows} == {
            '2026-04', '2026-05', '2026-06'}

    def test_within_one_month_consolidated(self):
        """Two market days inside the same calendar month merge into
        one monthly row (year-month is what separates rows, not date)."""
        _set_identity()
        market, vendor, pm = _ids()
        md1 = _open_md(market['id'], '2026-04-01')
        md2 = _open_md(market['id'], '2026-04-22')
        _add_txn(md1, vendor['id'], pm['id'], pm['name'],
                 pm['match_percent'],
                 receipt_cents=1500, customer_cents=750, match_cents=750,
                 fam_tid='FAM-TST-20260401-0001')
        _add_txn(md2, vendor['id'], pm['id'], pm['name'],
                 pm['match_percent'],
                 receipt_cents=2500, customer_cents=1250, match_cents=1250,
                 fam_tid='FAM-TST-20260422-0001')

        from fam.sync.data_collector import collect_sync_data
        rows = collect_sync_data()['Vendor Reimbursement']
        vrows = [r for r in rows if r['Vendor'] == vendor['name']]
        assert len(vrows) == 1
        assert vrows[0]['Year-Month'] == '2026-04'
        # Receipt totals summed: 1500 + 2500 = 4000 cents = $40.
        assert vrows[0]['Total Due to Vendor'] == 40.00


# ──────────────────────────────────────────────────────────────────
# 2. Math identity within each monthly row
# ──────────────────────────────────────────────────────────────────
class TestMonthlyMathIdentity:
    """Σ(method-cols) + FAM Match − Customer Forfeit + FMNP (External)
    = Total Due to Vendor — holds within EVERY monthly row.

    Customer Forfeit is zero in these scenarios (no denomination
    over-tender) so the reconciliation reduces to:
      Σ(method-cols) + FAM Match + FMNP (External) = Total Due.
    """

    def test_identity_per_month(self):
        _set_identity()
        market, vendor, pm = _ids()
        md_apr = _open_md(market['id'], '2026-04-15')
        md_may = _open_md(market['id'], '2026-05-15')
        _add_txn(md_apr, vendor['id'], pm['id'], pm['name'],
                 pm['match_percent'],
                 receipt_cents=1000, customer_cents=500, match_cents=500,
                 fam_tid='FAM-TST-20260415-0001')
        _add_txn(md_may, vendor['id'], pm['id'], pm['name'],
                 pm['match_percent'],
                 receipt_cents=2000, customer_cents=1000, match_cents=1000,
                 fam_tid='FAM-TST-20260515-0001')

        from fam.sync.data_collector import collect_sync_data
        rows = collect_sync_data()['Vendor Reimbursement']
        vrows = [r for r in rows if r['Vendor'] == vendor['name']]
        assert len(vrows) == 2

        for row in vrows:
            # Collect every per-method dollar column.  The visible
            # columns are everything between FAM Match and FMNP
            # (External).  Math is done in cents to avoid float drift.
            non_method = {
                'Market Name', 'Vendor', 'Month', 'Year-Month',
                'Date(s)', 'Total Due to Vendor', 'FAM Match',
                'FMNP (External)', 'Customer Forfeit',
                'Check Payable To', 'Address',
            }
            method_sum = sum(
                round(v * 100) for k, v in row.items()
                if k not in non_method and isinstance(v, (int, float))
            )
            fam_match = round(row['FAM Match'] * 100)
            fmnp_ext = round(row['FMNP (External)'] * 100)
            forfeit = round(row['Customer Forfeit'] * 100)
            total_due = round(row['Total Due to Vendor'] * 100)
            assert method_sum + fam_match - forfeit + fmnp_ext == total_due, (
                f"{row['Year-Month']} row violates the monthly identity: "
                f"methods={method_sum} + match={fam_match} - "
                f"forfeit={forfeit} + fmnp={fmnp_ext} != "
                f"total_due={total_due} (cents)")


# ──────────────────────────────────────────────────────────────────
# 3. Closed-market-day mutation in a prior month
# ──────────────────────────────────────────────────────────────────
class TestPriorMonthMutation:
    """A void on a prior-month transaction must update THAT month's
    monthly row, not the current month's.  The collector runs on the
    whole dataset, so each sync re-emits every month — a corrected
    prior month surfaces correctly without touching unrelated
    months."""

    def test_prior_month_void_decreases_prior_month_row(self):
        _set_identity()
        market, vendor, pm = _ids()
        md_apr = _open_md(market['id'], '2026-04-15')
        md_may = _open_md(market['id'], '2026-05-15')
        apr_txn = _add_txn(
            md_apr, vendor['id'], pm['id'], pm['name'],
            pm['match_percent'],
            receipt_cents=3000, customer_cents=1500, match_cents=1500,
            fam_tid='FAM-TST-20260415-0001')
        _add_txn(
            md_may, vendor['id'], pm['id'], pm['name'],
            pm['match_percent'],
            receipt_cents=2000, customer_cents=1000, match_cents=1000,
            fam_tid='FAM-TST-20260515-0001')

        from fam.sync.data_collector import collect_sync_data

        # Baseline: $30 in April, $20 in May.
        baseline = {
            r['Year-Month']: r['Total Due to Vendor']
            for r in collect_sync_data()['Vendor Reimbursement']
            if r['Vendor'] == vendor['name']
        }
        assert baseline == {'2026-04': 30.00, '2026-05': 20.00}

        # Void the April transaction (simulates an admin adjustment
        # / void on a closed-day mutation pathway).
        conn = get_connection()
        conn.execute("UPDATE transactions SET status='Voided' WHERE id=?",
                     (apr_txn,))
        conn.commit()

        # April row should be GONE (its only txn is now voided).
        # May row is untouched.
        post = {
            r['Year-Month']: r['Total Due to Vendor']
            for r in collect_sync_data()['Vendor Reimbursement']
            if r['Vendor'] == vendor['name']
        }
        assert post == {'2026-05': 20.00}, (
            f"April void should NOT have touched May totals; got {post}")


# ──────────────────────────────────────────────────────────────────
# 4. Cross-device: two laptops, same vendor, same month
# ──────────────────────────────────────────────────────────────────
class TestCrossDeviceSameMonth:
    """Two laptops both processing the same vendor in May should
    produce two SEPARATE rows on the shared sheet — one per device,
    both in May.  device_id is part of the upsert key, so each
    laptop's monthly row stays isolated.

    This test drives the upsert directly with constructed row dicts
    so the multi-device scenario isn't conflated with single-DB
    aggregation behaviour.  It's the device_id × Year-Month tuple
    that has to stay unique in the sheet's row identity."""

    def test_two_devices_same_vendor_same_month(self):
        from fam.sync.manager import SyncManager
        backend = InMemorySheetBackend()
        manager = SyncManager(backend, throttle_writes=False)

        # Laptop A's May row + Laptop B's May row arrive at the sheet
        # via two separate syncs (whichever device runs first).
        row_a = {
            'market_code': 'BFM', 'device_id': 'dev-A',
            'Market Name': 'Big Farm Market', 'Vendor': 'Farm A',
            'Month': 'May 2026', 'Year-Month': '2026-05',
            'Total Due to Vendor': 25.00,
        }
        row_b = {
            'market_code': 'BFM', 'device_id': 'dev-B',
            'Market Name': 'Big Farm Market', 'Vendor': 'Farm A',
            'Month': 'May 2026', 'Year-Month': '2026-05',
            'Total Due to Vendor': 18.00,
        }

        manager.sync_all({'Vendor Reimbursement': [row_a]})
        manager.sync_all({'Vendor Reimbursement': [row_b]})

        sheet = backend.sheets['Vendor Reimbursement']
        may_rows = [r for r in sheet if r['Year-Month'] == '2026-05']
        assert len(may_rows) == 2, (
            "Each device must keep its own May row — device_id is in "
            "the upsert key.")
        assert {r['device_id'] for r in may_rows} == {'dev-A', 'dev-B'}


# ──────────────────────────────────────────────────────────────────
# 5. FMNP-only vendor across multiple months
# ──────────────────────────────────────────────────────────────────
class TestFmnpOnlyMultiMonth:
    """A vendor with ONLY external FMNP entries (no transactions)
    spanning multiple months produces one row per month — the FMNP
    query is grouped by year_month just like the transaction
    query."""

    def test_fmnp_only_two_months(self):
        _set_identity()
        conn = get_connection()
        market, _vendor, _pm = _ids()
        cur = conn.execute(
            "INSERT INTO vendors (name, check_payable_to) "
            "VALUES ('Flower Stand LLC', 'Flower Stand LLC')")
        flower_vendor_id = cur.lastrowid

        md_apr = _open_md(market['id'], '2026-04-08')
        md_may = _open_md(market['id'], '2026-05-08')

        conn.execute(
            "INSERT INTO fmnp_entries "
            "(market_day_id, vendor_id, amount, entered_by) "
            "VALUES (?, ?, 1500, 'Tester')",
            (md_apr, flower_vendor_id))
        conn.execute(
            "INSERT INTO fmnp_entries "
            "(market_day_id, vendor_id, amount, entered_by) "
            "VALUES (?, ?, 2500, 'Tester')",
            (md_may, flower_vendor_id))
        conn.commit()

        from fam.sync.data_collector import collect_sync_data
        rows = collect_sync_data()['Vendor Reimbursement']
        flower_rows = [r for r in rows if r['Vendor'] == 'Flower Stand LLC']
        assert len(flower_rows) == 2
        by_month = {r['Year-Month']: r for r in flower_rows}
        assert by_month['2026-04']['FMNP (External)'] == 15.00
        assert by_month['2026-04']['Total Due to Vendor'] == 15.00
        assert by_month['2026-05']['FMNP (External)'] == 25.00
        assert by_month['2026-05']['Total Due to Vendor'] == 25.00


# ──────────────────────────────────────────────────────────────────
# 6. Same-month merge — transactions + FMNP into ONE row
# ──────────────────────────────────────────────────────────────────
class TestSameMonthFmnpTxnMerge:
    """When a vendor has BOTH a transaction and an external FMNP
    entry within the same calendar month, they merge into ONE
    monthly row — not two."""

    def test_txn_plus_fmnp_same_month_one_row(self):
        _set_identity()
        market, vendor, pm = _ids()
        md = _open_md(market['id'], '2026-06-10')
        _add_txn(md, vendor['id'], pm['id'], pm['name'],
                 pm['match_percent'],
                 receipt_cents=4000, customer_cents=2000, match_cents=2000,
                 fam_tid='FAM-TST-20260610-0001')

        conn = get_connection()
        conn.execute(
            "INSERT INTO fmnp_entries "
            "(market_day_id, vendor_id, amount, entered_by) "
            "VALUES (?, ?, 1000, 'Tester')",
            (md, vendor['id']))
        conn.commit()

        from fam.sync.data_collector import collect_sync_data
        rows = collect_sync_data()['Vendor Reimbursement']
        vrows = [r for r in rows if r['Vendor'] == vendor['name']]
        assert len(vrows) == 1, (
            "Txn + FMNP in the same month must merge into ONE row")
        assert vrows[0]['Year-Month'] == '2026-06'
        # Total Due = receipt total ($40) + FMNP external ($10) = $50.
        assert vrows[0]['Total Due to Vendor'] == 50.00
        assert vrows[0]['FMNP (External)'] == 10.00


# ──────────────────────────────────────────────────────────────────
# 7. Voiding decreases that month's row
# ──────────────────────────────────────────────────────────────────
class TestVoidDecreasesMonthRow:

    def test_void_one_of_two_txns_in_a_month(self):
        _set_identity()
        market, vendor, pm = _ids()
        md = _open_md(market['id'], '2026-07-01')
        t1 = _add_txn(
            md, vendor['id'], pm['id'], pm['name'], pm['match_percent'],
            receipt_cents=3000, customer_cents=1500, match_cents=1500,
            fam_tid='FAM-TST-20260701-0001')
        _add_txn(
            md, vendor['id'], pm['id'], pm['name'], pm['match_percent'],
            receipt_cents=1000, customer_cents=500, match_cents=500,
            fam_tid='FAM-TST-20260701-0002')

        from fam.sync.data_collector import collect_sync_data
        rows = collect_sync_data()['Vendor Reimbursement']
        before = [r for r in rows if r['Vendor'] == vendor['name']
                  and r['Year-Month'] == '2026-07'][0]
        assert before['Total Due to Vendor'] == 40.00

        conn = get_connection()
        conn.execute("UPDATE transactions SET status='Voided' WHERE id=?",
                     (t1,))
        conn.commit()

        rows = collect_sync_data()['Vendor Reimbursement']
        after = [r for r in rows if r['Vendor'] == vendor['name']
                 and r['Year-Month'] == '2026-07'][0]
        assert after['Total Due to Vendor'] == 10.00, (
            f"July row should drop from $40 to $10 after voiding the "
            f"$30 txn; got ${after['Total Due to Vendor']:.2f}")


# ──────────────────────────────────────────────────────────────────
# 8. Upsert keying: two monthly rows do NOT overwrite each other
# ──────────────────────────────────────────────────────────────────
class TestUpsertKeyingPreservesMonthlyRows:
    """The SHEET_KEYS entry for Vendor Reimbursement includes
    Year-Month (v2.0.9).  Without that, May would overwrite April
    on every sync because the row identity ``(mc, did, market,
    vendor)`` would match across months."""

    def test_two_monthly_rows_persist_across_syncs(self):
        _set_identity(market_code='BFM', device_id='dev-A')
        market, vendor, pm = _ids()
        _add_txn(
            _open_md(market['id'], '2026-04-15'),
            vendor['id'], pm['id'], pm['name'], pm['match_percent'],
            receipt_cents=1000, customer_cents=500, match_cents=500,
            fam_tid='FAM-BFM-20260415-0001')
        _add_txn(
            _open_md(market['id'], '2026-05-15'),
            vendor['id'], pm['id'], pm['name'], pm['match_percent'],
            receipt_cents=2000, customer_cents=1000, match_cents=1000,
            fam_tid='FAM-BFM-20260515-0001')

        from fam.sync.data_collector import collect_sync_data
        from fam.sync.manager import SyncManager

        backend = InMemorySheetBackend()
        manager = SyncManager(backend, throttle_writes=False)
        manager.sync_all(collect_sync_data())

        sheet = backend.sheets['Vendor Reimbursement']
        # Two rows for our vendor, one per month — NOT one
        # overwritten cumulative row.
        vrows = [r for r in sheet if r['Vendor'] == vendor['name']]
        assert len(vrows) == 2, (
            f"Each month gets its own sheet row; got {len(vrows)}")
        assert {r['Year-Month'] for r in vrows} == {'2026-04', '2026-05'}

        # Re-sync without any data changes — should still be two rows.
        manager.sync_all(collect_sync_data())
        sheet = backend.sheets['Vendor Reimbursement']
        vrows = [r for r in sheet if r['Vendor'] == vendor['name']]
        assert len(vrows) == 2
        assert {r['Year-Month'] for r in vrows} == {'2026-04', '2026-05'}

    def test_sheet_keys_include_year_month(self):
        """Pin: SHEET_KEYS['Vendor Reimbursement'] must include
        Year-Month.  Removing it would make May overwrite April."""
        from fam.sync.manager import SyncManager
        key = SyncManager.SHEET_KEYS['Vendor Reimbursement']
        assert 'Year-Month' in key, (
            f"Year-Month MUST be part of the Vendor Reimbursement "
            f"upsert key (was {key})")
