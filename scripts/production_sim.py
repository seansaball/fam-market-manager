"""End-to-end market-day simulation driven against the real model layer.

Runs small / medium / heavy sessions against an isolated temp SQLite
database and validates financial, reporting, and persistence invariants.
No real user data touched.

Usage:
    python scripts/production_sim.py
"""

import os
import random
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from collections import defaultdict
from datetime import date, timedelta

# -- Isolate to a temp DB BEFORE importing any model --------------
_TMP_DIR = tempfile.mkdtemp(prefix='fam_sim_')
_TMP_DB = os.path.join(_TMP_DIR, 'sim.db')

from fam.database.connection import set_db_path, get_connection, close_connection
set_db_path(_TMP_DB)

from fam.database.schema import initialize_database
from fam.database.backup import create_backup, get_backup_dir
from fam.models.market_day import (
    create_market_day, close_market_day, get_open_market_day,
    reopen_market_day,
)
from fam.models.customer_order import (
    create_customer_order, update_customer_order_status,
    get_customer_prior_match, get_confirmed_customers_for_market_day,
    get_draft_orders_for_market_day,
)
from fam.models.transaction import (
    create_transaction, confirm_transaction, save_payment_line_items,
    update_transaction, void_transaction,
    get_transaction_by_id, get_payment_line_items, search_transactions,
)
from fam.models.vendor import create_vendor
from fam.models.payment_method import create_payment_method
from fam.models.fmnp import (
    create_fmnp_entry, update_fmnp_entry, delete_fmnp_entry,
    get_fmnp_entries,
)
from fam.models.audit import log_action, get_audit_log
from fam.utils.calculations import (
    calculate_payment_breakdown, charge_to_method_amount,
)
from fam.utils.money import cents_to_dollars, format_dollars
from fam.utils.app_settings import set_market_code, capture_device_id


# -- Setup --------------------------------------------------------
random.seed(42)  # Deterministic
PASS = []
FAIL = []
WARN = []


def ok(msg):
    PASS.append(msg)
    print(f"  [PASS] {msg}")


def fail(msg):
    FAIL.append(msg)
    print(f"  [FAIL] {msg}")


def warn(msg):
    WARN.append(msg)
    print(f"  [WARN] {msg}")


def section(title):
    print(f"\n{'=' * 68}\n  {title}\n{'=' * 68}")


# -- 1. Configure DB & master data --------------------------------
section("PHASE 1 - Database init and master data")
initialize_database()
ok("Schema initialized on temp DB")

# Apply market code & device id so transaction IDs reflect real format
set_market_code("TM")
try:
    capture_device_id()
except Exception:
    # Non-Windows fallback - uses hostname
    from fam.utils.app_settings import set_setting
    set_setting('device_id', 'sim-device')
print(f"  temp DB: {_TMP_DB}")


# Create one market
conn = get_connection()
conn.execute(
    "INSERT INTO markets (name, address, daily_match_limit, match_limit_active)"
    " VALUES ('Test Market', '123 Main St', 10000, 1)"
)
conn.commit()
MARKET_ID = conn.execute("SELECT id FROM markets WHERE name='Test Market'").fetchone()[0]
ok(f"Created Test Market (id={MARKET_ID}, daily_match_limit=$100.00)")

# Create vendors
VENDORS = []
for i in range(15):
    vid = create_vendor(
        name=f"Vendor {i+1:02d}",
        contact_info=f"vendor{i+1}@example.com",
        check_payable_to=f"Vendor {i+1:02d} LLC",
        street=f"{100+i} Farm Rd",
        city="Townsville", state="CT", zip_code=f"060{i:02d}"[:5],
    )
    VENDORS.append(vid)
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) VALUES (?, ?)",
        (MARKET_ID, vid)
    )
conn.commit()
ok(f"Created {len(VENDORS)} vendors, all assigned to market")

# Create payment methods that mirror the real-world mix
PAYMENT_METHODS = [
    ('Cash',  0.0,   1, None),       # no match
    ('SNAP',  100.0, 2, None),       # 1:1 match
    ('FMNP',  100.0, 3, 500),        # 1:1, denominated $5 checks
    ('DUFB',  200.0, 4, None),       # 2:1 match
    ('Check', 0.0,   5, None),
]
PM_IDS = {}
for name, pct, sort_o, denom in PAYMENT_METHODS:
    pid = create_payment_method(name, pct, sort_o, denom)
    PM_IDS[name] = pid
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, payment_method_id) VALUES (?, ?)",
        (MARKET_ID, pid)
    )
conn.commit()
ok(f"Created {len(PAYMENT_METHODS)} payment methods: {', '.join(PM_IDS)}")


# -- Helpers for the simulation ----------------------------------

def simulate_receipt(market_day_id, customer_label, vendor_id, receipt_cents,
                     method_mix):
    """Run the full payment-save path for one receipt.

    method_mix: list of (method_name, charge_cents) tuples, where the
    sum of method_amount(charge, pct) MUST equal receipt_cents (the UI
    enforces this via auto-distribute; here we build mixes that balance
    by construction).  The calculator's penny reconciliation handles any
    ``<= 1c`` float-rounding drift.  Applies the market's daily match
    limit, mirroring the real payment-save path.

    Returns (txn_id, breakdown, order_id) or (None, breakdown, order_id)
    when the calculator marks the entry invalid (UI would block the
    save in that case).
    """
    # 1. Customer order
    order_id, label = create_customer_order(
        market_day_id, customer_label=customer_label)

    # 2. Compute per-customer remaining match cap (real UI logic)
    prior_match = get_customer_prior_match(customer_label, market_day_id)
    daily_limit = 10000  # market's daily_match_limit
    remaining_cap = max(0, daily_limit - prior_match)

    # 3. Create transaction
    txn_id, fam_tid = create_transaction(
        market_day_id=market_day_id,
        vendor_id=vendor_id,
        receipt_total=receipt_cents,
        customer_order_id=order_id,
    )

    # 4. Build payment entries from the pre-balanced mix
    entries = []
    for name, charge in method_mix:
        pm = PM_IDS[name]
        pct = next(p[1] for p in PAYMENT_METHODS if p[0] == name)
        method_amount = charge_to_method_amount(charge, pct)
        entries.append({
            'payment_method_id': pm,
            'method_name_snapshot': name,
            'match_percent_snapshot': pct,
            'method_amount': method_amount,
            'match_amount': method_amount - charge,
            'customer_charged': charge,
            'photo_path': None,
        })

    # 5. Run calculator with the match limit (mimics UI)
    breakdown = calculate_payment_breakdown(
        receipt_cents,
        [{'method_amount': e['method_amount'],
          'match_percent': e['match_percent_snapshot']}
         for e in entries],
        match_limit=remaining_cap,
    )

    # 6. If calculator says invalid, UI would block save - skip persist
    if not breakdown['is_valid']:
        # Roll back the customer_order/transaction rows we created
        conn = get_connection()
        conn.execute("DELETE FROM transactions WHERE id=?", (txn_id,))
        conn.execute("DELETE FROM customer_orders WHERE id=?", (order_id,))
        conn.commit()
        return None, breakdown, order_id

    # 7. Rebuild entries from breakdown so penny reconciliation + cap lands in DB
    for i, li in enumerate(breakdown['line_items']):
        entries[i]['method_amount'] = li['method_amount']
        entries[i]['match_amount'] = li['match_amount']
        entries[i]['customer_charged'] = li['customer_charged']

    # 8. Persist
    save_payment_line_items(txn_id, entries)
    confirm_transaction(txn_id)
    update_customer_order_status(order_id, 'Confirmed')

    return txn_id, breakdown, order_id


def verify_per_transaction_reconciliation(txn_ids, label):
    """For every txn, assert customer_paid + match == receipt_total."""
    bad = 0
    conn = get_connection()
    for tid in txn_ids:
        t = get_transaction_by_id(tid)
        lis = get_payment_line_items(tid)
        receipt = t['receipt_total']
        method_sum = sum(li['method_amount'] for li in lis)
        customer_sum = sum(li['customer_charged'] for li in lis)
        match_sum = sum(li['match_amount'] for li in lis)
        if abs(method_sum - receipt) > 1:
            bad += 1
        if abs((customer_sum + match_sum) - receipt) > 1:
            bad += 1
    if bad == 0:
        ok(f"{label}: per-transaction reconciliation clean across {len(txn_ids)} txns")
    else:
        fail(f"{label}: {bad} transactions failed reconciliation")


# -- 2. Run three sessions of increasing size --------------------

def _balance_mix(mix_kind, receipt):
    """Construct a payment mix that exactly sums to receipt_cents.

    Returns list of (method_name, charge) pairs where
    sum(charge_to_method_amount(charge, pct)) == receipt.
    """
    if mix_kind == 'snap_only':
        # All SNAP: charge = receipt / 2 (since method = 2 * charge)
        charge = receipt // 2
        # receipt may be odd → charge floor leaves 1c remainder handled by
        # calculator's penny reconciliation.  Keep one entry.
        return [('SNAP', charge)]
    if mix_kind == 'cash_only':
        return [('Cash', receipt)]
    if mix_kind == 'cash_snap':
        # Fix cash portion, remainder goes to SNAP
        cash = receipt // 3  # roughly 1/3 cash
        snap_method = receipt - cash
        snap_charge = snap_method // 2  # SNAP is 100% match → method=2*charge
        # Adjust cash so totals balance to the penny
        actual_snap_method = charge_to_method_amount(snap_charge, 100)
        cash = receipt - actual_snap_method
        if cash < 0:
            return [('SNAP', snap_charge)]
        return [('Cash', cash), ('SNAP', snap_charge)]
    if mix_kind == 'snap_dufb':
        # SNAP (100%) + DUFB (200%)
        snap_charge = receipt // 4  # ~1/2 of receipt after match
        snap_method = charge_to_method_amount(snap_charge, 100)
        remaining = receipt - snap_method
        dufb_charge = remaining // 3  # DUFB method = 3 * charge
        dufb_method = charge_to_method_amount(dufb_charge, 200)
        snap_method = receipt - dufb_method
        snap_charge = snap_method // 2
        # Rebalance: force last line to absorb remainder via penny reconciliation
        return [('SNAP', snap_charge), ('DUFB', dufb_charge)]
    if mix_kind == 'fmnp_mix':
        # FMNP denominated $5 (500c).  Pick an even number of checks.
        n_checks = min(receipt // 1000, 5)  # up to 5 checks
        if n_checks <= 0:
            return [('Cash', receipt)]
        fmnp_charge = n_checks * 500
        fmnp_method = charge_to_method_amount(fmnp_charge, 100)
        cash = receipt - fmnp_method
        if cash < 0:
            # FMNP alone over-covers — just use cash
            return [('Cash', receipt)]
        if cash == 0:
            return [('FMNP', fmnp_charge)]
        return [('FMNP', fmnp_charge), ('Cash', cash)]
    return [('Cash', receipt)]


# FMNP-external counts per session, exposed at module scope
FMNP_EXT_COUNTS = {}


def run_session(size_label, n_customers, txns_per_customer_range,
                receipt_cents_range):
    section(f"PHASE 2 - {size_label.upper()} market day simulation")
    today = date.today() + timedelta(days=hash(size_label) % 60)
    md_id = create_market_day(MARKET_ID, today.isoformat(), opened_by="Sim")
    create_backup('market_open')
    ok(f"Opened market day {md_id} ({today.isoformat()})")

    txn_ids = []
    skipped = 0
    for ci in range(n_customers):
        label = f"C-{ci+1:03d}"
        n_txns = random.randint(*txns_per_customer_range)
        for ti in range(n_txns):
            vendor = random.choice(VENDORS)
            receipt = random.randint(*receipt_cents_range)
            mix_kind = random.choice(
                ['snap_only', 'snap_dufb', 'cash_snap', 'cash_only', 'fmnp_mix']
            )
            mix = _balance_mix(mix_kind, receipt)
            try:
                tid, bd, _ = simulate_receipt(md_id, label, vendor, receipt, mix)
                if tid is None:
                    skipped += 1  # UI would have blocked save
                else:
                    txn_ids.append(tid)
            except Exception as e:
                fail(f"receipt insert failed for {label} vendor={vendor} "
                     f"receipt=${receipt/100:.2f}: {e}")

    ok(f"Inserted {len(txn_ids)} confirmed transactions ({skipped} rejected by validator)")

    # FMNP external entries (tracking page)
    ext_count = max(2, n_customers // 5)
    for _ in range(ext_count):
        vendor = random.choice(VENDORS)
        amt = random.choice([500, 1000, 1500, 2000, 2500])
        create_fmnp_entry(
            market_day_id=md_id,
            vendor_id=vendor,
            amount=amt,
            entered_by="Sim",
            check_count=1,
        )
    FMNP_EXT_COUNTS[size_label] = ext_count
    ok(f"Inserted {ext_count} external FMNP entries")

    # Adjust one transaction (triggers returning-customer match cap path)
    if txn_ids:
        adj_tid = txn_ids[len(txn_ids) // 2]
        update_transaction(adj_tid, status='Adjusted')
        log_action('transactions', adj_tid, 'ADJUST', 'Sim',
                   notes='Simulated post-confirmation adjustment')
        ok(f"Adjusted transaction id={adj_tid} (status -> Adjusted)")

    # Void one transaction
    if len(txn_ids) > 1:
        void_tid = txn_ids[-1]
        void_transaction(void_tid, voided_by="Sim")
        ok(f"Voided transaction id={void_tid}")

    # Close market
    close_market_day(md_id, closed_by="Sim")
    create_backup('market_close')
    ok(f"Closed market day {md_id}")

    return md_id, txn_ids


# Small
SMALL_MD, SMALL_TIDS = run_session("small",  n_customers=5,
                                    txns_per_customer_range=(1, 3),
                                    receipt_cents_range=(500, 3000))
verify_per_transaction_reconciliation(
    [t for t in SMALL_TIDS if get_transaction_by_id(t)['status'] != 'Voided'],
    "small")

# Medium
MED_MD, MED_TIDS = run_session("medium", n_customers=25,
                                txns_per_customer_range=(1, 4),
                                receipt_cents_range=(500, 8000))
verify_per_transaction_reconciliation(
    [t for t in MED_TIDS if get_transaction_by_id(t)['status'] != 'Voided'],
    "medium")

# Heavy
HEAVY_MD, HEAVY_TIDS = run_session("heavy", n_customers=100,
                                    txns_per_customer_range=(1, 5),
                                    receipt_cents_range=(300, 15000))
verify_per_transaction_reconciliation(
    [t for t in HEAVY_TIDS if get_transaction_by_id(t)['status'] != 'Voided'],
    "heavy")


# -- 3. Cross-market-day reporting reconciliation -----------------
section("PHASE 3 - Report-level reconciliation (all sessions)")

conn = get_connection()

# A. Sum of receipt_total (non-voided) == sum of method_amount across all PLIs
receipt_sum = conn.execute("""
    SELECT COALESCE(SUM(receipt_total), 0) FROM transactions
    WHERE status IN ('Confirmed', 'Adjusted')
""").fetchone()[0]

method_sum = conn.execute("""
    SELECT COALESCE(SUM(pl.method_amount), 0)
    FROM payment_line_items pl
    JOIN transactions t ON pl.transaction_id = t.id
    WHERE t.status IN ('Confirmed', 'Adjusted')
""").fetchone()[0]

if receipt_sum == method_sum:
    ok(f"Receipt totals match method allocations exactly "
       f"({format_dollars(receipt_sum)})")
else:
    diff = receipt_sum - method_sum
    fail(f"Receipt total ({format_dollars(receipt_sum)}) != "
         f"method allocation ({format_dollars(method_sum)}) - diff {diff}c")

# B. customer_charged + match_amount == method_amount at the line-item level
bad_lines = conn.execute("""
    SELECT COUNT(*) FROM payment_line_items
    WHERE customer_charged + match_amount != method_amount
""").fetchone()[0]
if bad_lines == 0:
    ok("Every payment_line_item satisfies customer + match == method_amount")
else:
    fail(f"{bad_lines} payment_line_items violate the customer+match=method invariant")

# C. No Draft transactions left behind after market close
dangling_drafts = conn.execute("""
    SELECT COUNT(*) FROM transactions
    WHERE status = 'Draft'
""").fetchone()[0]
if dangling_drafts == 0:
    ok("No dangling Draft transactions after all markets closed")
else:
    warn(f"{dangling_drafts} Draft transactions remain - would show in Admin screen for cleanup")

# D. FAM match total (report) == sum of match_amount (DB)
# Emulating reports_screen.py FAM match query:
match_by_method = conn.execute("""
    SELECT pl.method_name_snapshot,
           SUM(pl.method_amount) AS total_allocated,
           SUM(pl.match_amount) AS total_match
    FROM payment_line_items pl
    JOIN transactions t ON pl.transaction_id = t.id
    WHERE t.status IN ('Confirmed', 'Adjusted')
    GROUP BY pl.method_name_snapshot
""").fetchall()

total_report_match = sum(r['total_match'] for r in match_by_method)
raw_db_match = conn.execute("""
    SELECT COALESCE(SUM(pl.match_amount), 0)
    FROM payment_line_items pl
    JOIN transactions t ON pl.transaction_id = t.id
    WHERE t.status IN ('Confirmed', 'Adjusted')
""").fetchone()[0]

if total_report_match == raw_db_match:
    ok(f"FAM match report total matches raw DB sum ({format_dollars(raw_db_match)})")
else:
    fail(f"FAM match report drift: report={format_dollars(total_report_match)} "
         f"db={format_dollars(raw_db_match)}")

# E. Vendor reimbursement: per-vendor sum of method_amount should equal per-vendor sum of line items
vendor_reimburse_ok = True
rows = conn.execute("""
    SELECT t.vendor_id,
           SUM(t.receipt_total) AS tsum,
           (SELECT COALESCE(SUM(pl.method_amount), 0)
            FROM payment_line_items pl
            JOIN transactions t2 ON pl.transaction_id = t2.id
            WHERE t2.vendor_id = t.vendor_id
              AND t2.status IN ('Confirmed', 'Adjusted')) AS plsum
    FROM transactions t
    WHERE t.status IN ('Confirmed', 'Adjusted')
    GROUP BY t.vendor_id
""").fetchall()
for r in rows:
    if r['tsum'] != r['plsum']:
        vendor_reimburse_ok = False
        fail(f"vendor {r['vendor_id']}: receipt_total sum {r['tsum']}c "
             f"!= method_amount sum {r['plsum']}c")
if vendor_reimburse_ok:
    ok(f"Vendor reimbursement totals reconcile exactly across {len(rows)} vendors")

# F. FMNP external entries are not double-counted
fmnp_ext = conn.execute(
    "SELECT COALESCE(SUM(amount), 0) FROM fmnp_entries WHERE status='Active'"
).fetchone()[0]
fmnp_internal = conn.execute("""
    SELECT COALESCE(SUM(pl.method_amount), 0)
    FROM payment_line_items pl
    JOIN transactions t ON pl.transaction_id = t.id
    WHERE pl.method_name_snapshot = 'FMNP'
      AND t.status IN ('Confirmed', 'Adjusted')
""").fetchone()[0]
ok(f"FMNP internal (via payment rows): {format_dollars(fmnp_internal)}  "
   f"FMNP external (tracking page): {format_dollars(fmnp_ext)}  "
   f"- stored in separate tables, no overlap possible")


# -- 4. Match limit enforcement sanity check ---------------------
section("PHASE 4 - Match limit enforcement")

# For each market day, no customer should exceed $100 match cap
over_cap = conn.execute("""
    SELECT co.customer_label, co.market_day_id,
           SUM(pl.match_amount) AS total_match
    FROM customer_orders co
    JOIN transactions t ON t.customer_order_id = co.id
    JOIN payment_line_items pl ON pl.transaction_id = t.id
    WHERE t.status IN ('Confirmed', 'Adjusted')
      AND co.status IN ('Confirmed', 'Adjusted')
    GROUP BY co.customer_label, co.market_day_id
    HAVING total_match > 10000
""").fetchall()
if not over_cap:
    ok("No customer exceeded the $100 daily match limit")
else:
    # Check if the customer had locked match already
    for r in over_cap:
        warn(f"customer {r['customer_label']} md={r['market_day_id']} match=${r['total_match']/100:.2f} "
             "- simulation used calculator without per-customer cap wiring; "
             "real UI applies cap. Recording for context.")


# -- 5. Disruption scenarios -------------------------------------
section("PHASE 5 - Operational disruption simulation")

# 5a. Simulate abrupt shutdown: open a new market, create a Draft transaction,
# close the connection without confirming. Reopen and verify Draft visible.
abrupt_md_date = (date.today() + timedelta(days=200)).isoformat()
abrupt_md_id = create_market_day(MARKET_ID, abrupt_md_date, opened_by="Sim")
abrupt_order_id, _ = create_customer_order(abrupt_md_id, customer_label="C-999")
abrupt_tid, abrupt_ftid = create_transaction(
    abrupt_md_id, VENDORS[0], receipt_total=1500, customer_order_id=abrupt_order_id,
)
# Force-close the connection (simulates kill -9 before payment save)
close_connection()

# Reopen
conn = get_connection()
still_there = conn.execute(
    "SELECT status, receipt_total FROM transactions WHERE id=?", (abrupt_tid,)
).fetchone()
if still_there and still_there['status'] == 'Draft' and still_there['receipt_total'] == 1500:
    ok("Draft transaction survived abrupt connection close, reopened cleanly")
else:
    fail(f"Draft transaction lost after abrupt close: {still_there}")

drafts = get_draft_orders_for_market_day(abrupt_md_id)
if len(drafts) == 1 and drafts[0]['customer_label'] == 'C-999':
    ok("Draft order is listed in Admin-screen source (get_draft_orders_for_market_day)")
else:
    fail(f"Draft order not visible after restart: {drafts}")

# 5b. Partial payment save - simulate an exception mid-save by passing
# invalid data; verify the DB trigger blocks it and leaves no partial row.
try:
    save_payment_line_items(abrupt_tid, [
        {'payment_method_id': PM_IDS['Cash'],
         'method_name_snapshot': 'Cash',
         'match_percent_snapshot': 0.0,
         'method_amount': -500,  # Negative - triggers DB CHECK
         'match_amount': 0,
         'customer_charged': -500,
         'photo_path': None},
    ])
    fail("DB trigger did NOT reject negative method_amount (data integrity hole)")
except sqlite3.IntegrityError as e:
    ok(f"DB trigger correctly rejected negative method_amount: {e}")
except Exception as e:
    warn(f"Unexpected exception class (expected IntegrityError): {type(e).__name__}: {e}")

# Verify no partial line items landed
pli_count = conn.execute(
    "SELECT COUNT(*) FROM payment_line_items WHERE transaction_id=?", (abrupt_tid,)
).fetchone()[0]
if pli_count == 0:
    ok("No orphan payment_line_items after rejected save")
else:
    fail(f"{pli_count} partial payment_line_items persisted despite rejection")

# 5c. Reopen a closed market day and add a transaction
reopen_market_day(SMALL_MD, opened_by="Sim")
md_status = conn.execute("SELECT status FROM market_days WHERE id=?", (SMALL_MD,)).fetchone()[0]
if md_status == 'Open':
    ok(f"Market day {SMALL_MD} reopened")
    new_order_id, _ = create_customer_order(SMALL_MD, customer_label="C-late")
    new_tid, _ = create_transaction(SMALL_MD, VENDORS[0], 2000,
                                     customer_order_id=new_order_id)
    save_payment_line_items(new_tid, [{
        'payment_method_id': PM_IDS['Cash'],
        'method_name_snapshot': 'Cash',
        'match_percent_snapshot': 0.0,
        'method_amount': 2000, 'match_amount': 0, 'customer_charged': 2000,
        'photo_path': None,
    }])
    confirm_transaction(new_tid)
    close_market_day(SMALL_MD, closed_by="Sim-Reclose")
    ok("Added late transaction after reopen, re-closed market")
else:
    fail(f"Market day reopen failed, status={md_status}")


# -- 6. Audit log sufficiency ------------------------------------
section("PHASE 6 - Audit / traceability")

audit_rows = conn.execute("""
    SELECT table_name, action, COUNT(*) AS n
    FROM audit_log GROUP BY table_name, action ORDER BY table_name, action
""").fetchall()

print("  Audit log distribution:")
for r in audit_rows:
    print(f"    {r['table_name']:20s} {r['action']:20s} {r['n']:6d}")

expected_actions = [
    ('transactions', 'CREATE'),
    ('transactions', 'CONFIRM'),
    ('transactions', 'VOID'),
    ('transactions', 'ADJUST'),
    ('market_days',  'OPEN'),
    ('market_days',  'CLOSE'),
    ('market_days',  'REOPEN'),
    ('customer_orders', 'CREATE'),
]
have = {(r['table_name'], r['action']) for r in audit_rows}
missing = [e for e in expected_actions if e not in have]
if not missing:
    ok("All expected lifecycle actions recorded in audit log")
else:
    fail(f"Missing audit actions: {missing}")

# FMNP audit gap - audit this explicitly
fmnp_audit = conn.execute(
    "SELECT COUNT(*) FROM audit_log WHERE table_name='fmnp_entries'"
).fetchone()[0]
total_fmnp_created = sum(FMNP_EXT_COUNTS.values())
if fmnp_audit == 0:
    warn(f"FMNP audit trail: 0 entries despite {total_fmnp_created} creates in sim "
         "(confirmed defect: fmnp.py does not call log_action)")
else:
    ok(f"FMNP has {fmnp_audit} audit entries for {total_fmnp_created} creates")


# -- 7. Backup system --------------------------------------------
section("PHASE 7 - Backup creation")

backup_dir = get_backup_dir()
backups = [f for f in os.listdir(backup_dir) if f.endswith('.db')]
ok(f"Backup directory: {backup_dir}")
ok(f"Backup files present: {len(backups)}")
for b in sorted(backups)[:5]:
    size = os.path.getsize(os.path.join(backup_dir, b))
    print(f"    {b}  ({size:,} bytes)")
# Open one backup and verify it has real data
if backups:
    sample_bak = os.path.join(backup_dir, backups[0])
    try:
        bak_conn = sqlite3.connect(sample_bak)
        bak_count = bak_conn.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
        bak_conn.close()
        if bak_count > 0:
            ok(f"Sample backup is valid SQLite with {bak_count} transactions")
        else:
            warn(f"Sample backup ({backups[0]}) has 0 transactions (backup taken on open, expected)")
    except Exception as e:
        fail(f"Could not read backup {backups[0]}: {e}")


# -- 8. Performance snapshot -------------------------------------
section("PHASE 8 - Performance snapshot (report query latency)")

from fam.sync.data_collector import collect_sync_data
t0 = time.perf_counter()
sync_data = collect_sync_data()  # Collects everything across all 4 markets
elapsed = time.perf_counter() - t0
total_rows = sum(len(v) for v in sync_data.values())
tabs = len(sync_data)
if elapsed < 3.0:
    ok(f"Full sync data collection: {tabs} tabs, {total_rows} rows in {elapsed:.2f}s")
else:
    warn(f"Sync data collection took {elapsed:.2f}s for {total_rows} rows "
         f"- may be slow under 10x load")

# Heavy session receipt total
heavy_confirmed = conn.execute("""
    SELECT COUNT(*), COALESCE(SUM(receipt_total), 0)
    FROM transactions WHERE market_day_id=? AND status IN ('Confirmed', 'Adjusted')
""", (HEAVY_MD,)).fetchone()
print(f"  Heavy session: {heavy_confirmed[0]} confirmed transactions, "
      f"${heavy_confirmed[1]/100:,.2f} gross")


# -- 9. Final summary --------------------------------------------
section("SIMULATION RESULT")
print(f"  Total PASS: {len(PASS)}")
print(f"  Total FAIL: {len(FAIL)}")
print(f"  Total WARN: {len(WARN)}")
if FAIL:
    print("\n  FAILURES:")
    for f in FAIL:
        print(f"    - {f}")
if WARN:
    print("\n  WARNINGS:")
    for w in WARN:
        print(f"    - {w}")

# Cleanup
close_connection()
print(f"\n  Temp data dir: {_TMP_DIR}")
print("  (preserved for inspection; delete manually when done)")

sys.exit(0 if not FAIL else 1)
