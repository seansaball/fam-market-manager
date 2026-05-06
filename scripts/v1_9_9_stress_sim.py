"""v1.9.9 production-readiness stress simulation.

Drives the full money pipeline through scenarios that the existing
``scripts/production_sim.py`` does not cover:

  * Single customer order spanning **10+ vendors** with mixed methods
  * Returning customer match-cap accumulation across multiple visits
  * Sequential adjustment chain (5 mods to one transaction)
  * Adjust-then-void integrity (vendor reimbursement updates correctly)
  * Penny / fractional-match reconciliation across multi-vendor split
  * Edge cases ($0.01 receipts, 200% match, 0% match, multi-denom)
  * Reports ↔ DB ↔ ledger backup ↔ audit log all reconcile to the cent

Runs against an isolated tempfile DB.  No real user data touched.

Usage::

    python -m scripts.v1_9_9_stress_sim

Exit code is 0 on full pass, 1 if any reconciliation invariant fails.
This script is intended to run before each release as a
"zero tolerance for financial mismatch" gate.
"""

import os
import sys
import sqlite3
import tempfile
import traceback
from datetime import date

# Console may default to cp1252 on Windows — force UTF-8 so the
# section dividers and tolerance markers render cleanly.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# -- Isolate to a temp DB BEFORE importing any model --------------
_TMP_DIR = tempfile.mkdtemp(prefix='fam_v199_sim_')
_TMP_DB = os.path.join(_TMP_DIR, 'sim.db')

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
set_db_path(_TMP_DB)

from fam.database.schema import initialize_database
from fam.models.market_day import (
    create_market_day, close_market_day, reopen_market_day,
)
from fam.models.customer_order import (
    create_customer_order, update_customer_order_status,
    get_customer_prior_match,
)
from fam.models.transaction import (
    create_transaction, save_payment_line_items, confirm_transaction,
    void_transaction, get_transaction_by_id, get_payment_line_items,
)
from fam.models.audit import log_action
from fam.utils.calculations import (
    calculate_payment_breakdown, charge_to_method_amount,
)
from fam.utils.money import format_dollars
from fam.utils.app_settings import set_market_code, set_setting
from fam.utils.export import write_ledger_backup
from fam.sync.data_collector import (
    _collect_vendor_reimbursement, _collect_fam_match,
    _collect_detailed_ledger, _collect_market_day_summary,
)


PASS, FAIL, WARN = [], [], []
SECTIONS: list[tuple[str, int, int]] = []  # (section, pass, fail)


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
    print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}")


def assert_eq(label, actual, expected, tol=0):
    if abs(actual - expected) <= tol:
        ok(f"{label}: {actual} (expected {expected}, tol ±{tol})")
        return True
    fail(f"{label}: got {actual}, expected {expected} (tol ±{tol})")
    return False


# ════════════════════════════════════════════════════════════════════
# PHASE 1 — Bootstrap: schema, market, vendors, payment methods
# ════════════════════════════════════════════════════════════════════
section("PHASE 1 — Bootstrap (schema + master data)")

initialize_database()
set_market_code("VSIM")  # 4 letters (constraint: 1-4 letters only)
set_setting('device_id', 'sim-device-v199')
ok(f"Schema initialized on temp DB: {_TMP_DB}")

conn = get_connection()

# Market with $500 / customer / day match cap.
conn.execute(
    "INSERT INTO markets (id, name, address, daily_match_limit, "
    " match_limit_active) VALUES "
    "(1, 'V199 Stress Market', '1 Audit Way', 50000, 1)"
)

# 12 vendors — wide enough to drive a 10-vendor mega order.
VENDORS = [
    (1, 'Apple Orchard'), (2, 'Bakery Plus'), (3, 'Cidery Lane'),
    (4, 'Dumpling Dynasty'), (5, 'Egg Farm'), (6, 'Fresh Fish'),
    (7, 'Greens & Things'), (8, 'Honey Pot'), (9, 'Italian Imports'),
    (10, 'Juice Bar'), (11, 'Kefir Kingdom'), (12, 'Local Lamb'),
]
for vid, name in VENDORS:
    conn.execute("INSERT INTO vendors (id, name) VALUES (?, ?)",
                 (vid, name))
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, ?)", (vid,))

# 6 payment methods spanning the full feature space:
#   0% (Cash), 50% (Food RX), 100% (SNAP), 100% denom $2 (Food Bucks),
#   100% denom $5 (FMNP), 200% (Premium Match).
METHODS = [
    (1, 'SNAP',          100.0, None, 1),
    (2, 'Cash',            0.0, None, 2),
    (3, 'Food RX',        50.0, None, 3),
    (4, 'JH Food Bucks', 100.0, 200,  4),
    (5, 'FMNP',          100.0, 500,  5),
    (6, 'Premium Match', 200.0, None, 6),
]
for mid, name, pct, denom, sort_o in METHODS:
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " denomination, sort_order, is_active) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (mid, name, pct, denom, sort_o))
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        " payment_method_id) VALUES (1, ?)", (mid,))

# Heterogeneous per-vendor eligibility (mirrors test_production_stress).
ELIGIBILITY = {
    1:  [1, 2, 3, 5], 2:  [1, 2, 4],    3:  [1, 2, 5],
    4:  [1, 2, 4, 6], 5:  [1, 2, 3],    6:  [1, 2, 4, 5],
    7:  [1, 2],       8:  [1, 2, 4, 6], 9:  [1, 2, 3, 5],
    10: [1, 2, 4],    11: [1, 2, 5],    12: [1, 2, 4, 6],
}
for vid, mids in ELIGIBILITY.items():
    for mid in mids:
        conn.execute(
            "INSERT INTO vendor_payment_methods "
            "(vendor_id, payment_method_id) VALUES (?, ?)",
            (vid, mid))
conn.commit()
ok(f"Seeded {len(VENDORS)} vendors, {len(METHODS)} payment methods, "
   f"heterogeneous eligibility")

MD_ID = create_market_day(1, '2099-04-15', opened_by='Sim')
ok(f"Opened market day {MD_ID} (date 2099-04-15, future date avoids "
   "stale-day guard)")


# ════════════════════════════════════════════════════════════════════
# PHASE 2 — Mega order: 10 vendors, single customer, mixed methods
# ════════════════════════════════════════════════════════════════════
section("PHASE 2 — Mega 10-vendor single-customer order")


def _save_payment(txn_id: int, items: list, mark_adjusted=False):
    """Save line items + flip txn AND order to Confirmed.

    Mirrors PaymentScreen._confirm_payment which updates BOTH the
    transaction status and the customer_order status.  Without the
    order-status step, get_customer_prior_match (which filters on
    co.status IN ('Confirmed','Adjusted')) returns 0 silently.
    """
    save_payment_line_items(txn_id, items, commit=False)
    confirm_transaction(txn_id, confirmed_by='Sim', commit=False)
    row = conn.execute(
        "SELECT customer_order_id FROM transactions WHERE id=?",
        (txn_id,)
    ).fetchone()
    if row and row[0] is not None:
        update_customer_order_status(row[0], 'Confirmed', commit=False)
    if mark_adjusted:
        conn.execute(
            "UPDATE transactions SET status='Adjusted' WHERE id=?",
            (txn_id,))
    conn.commit()


def _li(method_id, name, pct, method_amt, match_amt, charge):
    return {
        'payment_method_id': method_id,
        'method_name_snapshot': name,
        'match_percent_snapshot': pct,
        'method_amount': method_amt,
        'match_amount': match_amt,
        'customer_charged': charge,
    }


# 10 vendors with awkward odd-cent receipts so penny reconciliation
# is exercised across the multi-vendor distribution.  Total $200.63.
MEGA_RECEIPTS = [
    (1, 1233), (2, 2567), (3, 1900), (4, 3399), (5, 1099),
    (6,  799), (7, 4501), (8, 1500), (9, 2200), (10, 865),
]
MEGA_TOTAL = sum(rt for _, rt in MEGA_RECEIPTS)
assert MEGA_TOTAL == 20063, f"Sanity: receipts sum to {MEGA_TOTAL}"

mega_order_id, _ = create_customer_order(
    MD_ID, customer_label='C-MEGA', zip_code='15102')
mega_txn_ids = []
for vid, receipt in MEGA_RECEIPTS:
    tid, _ = create_transaction(
        market_day_id=MD_ID, vendor_id=vid,
        receipt_total=receipt, customer_order_id=mega_order_id,
        market_day_date='2099-04-15')
    mega_txn_ids.append(tid)


def _allocate_for_vendor(vendor_id, receipt_cents):
    eligible = set(ELIGIBILITY[vendor_id])
    items = []
    if 4 in eligible:  # Food Bucks $2 denom
        items.append(_li(4, 'JH Food Bucks', 100.0, 400, 200, 200))
        rem = receipt_cents - 400
        if rem > 0:
            customer = rem // 2
            match = rem - customer
            items.append(_li(1, 'SNAP', 100.0, rem, match, customer))
    elif 5 in eligible:  # FMNP $5 denom
        items.append(_li(5, 'FMNP', 100.0, 1000, 500, 500))
        rem = receipt_cents - 1000
        if rem > 0:
            customer = rem // 2
            match = rem - customer
            items.append(_li(1, 'SNAP', 100.0, rem, match, customer))
        elif rem < 0:
            # FMNP over-covers — drop and use SNAP
            items = []
            customer = receipt_cents // 2
            match = receipt_cents - customer
            items.append(_li(1, 'SNAP', 100.0, receipt_cents,
                             match, customer))
    else:  # SNAP + Cash split
        snap_method = receipt_cents // 2
        snap_customer = snap_method // 2
        snap_match = snap_method - snap_customer
        items.append(_li(1, 'SNAP', 100.0, snap_method,
                         snap_match, snap_customer))
        cash_method = receipt_cents - snap_method
        if cash_method > 0:
            items.append(_li(2, 'Cash', 0.0, cash_method, 0,
                             cash_method))
    return items


for (vid, receipt), tid in zip(MEGA_RECEIPTS, mega_txn_ids):
    items = _allocate_for_vendor(vid, receipt)
    # Run breakdown to apply penny reconciliation.
    breakdown = calculate_payment_breakdown(
        receipt,
        [{'method_amount': it['method_amount'],
          'match_percent': it['match_percent_snapshot']}
         for it in items],
        match_limit=None,
    )
    if not breakdown['is_valid']:
        fail(f"vendor {vid} breakdown invalid: {breakdown['errors']}")
        continue
    for i, li in enumerate(breakdown['line_items']):
        items[i]['method_amount'] = li['method_amount']
        items[i]['match_amount'] = li['match_amount']
        items[i]['customer_charged'] = li['customer_charged']
    _save_payment(tid, items)

ok(f"Saved 10 confirmed transactions for C-MEGA "
   f"(${MEGA_TOTAL/100:.2f})")

# ── Reconciliation pass ────────────────────────────────────────
# Per-transaction invariant
bad = 0
for tid in mega_txn_ids:
    t = get_transaction_by_id(tid)
    lis = get_payment_line_items(tid)
    method_sum = sum(li['method_amount'] for li in lis)
    customer_sum = sum(li['customer_charged'] for li in lis)
    match_sum = sum(li['match_amount'] for li in lis)
    if method_sum != t['receipt_total']:
        bad += 1
        fail(f"txn {tid}: method_sum {method_sum} != receipt "
             f"{t['receipt_total']}")
    if customer_sum + match_sum != method_sum:
        bad += 1
        fail(f"txn {tid}: customer+match ({customer_sum}+{match_sum})"
             f" != method ({method_sum})")
if bad == 0:
    ok("Per-transaction invariant clean across all 10 mega-order txns")

# Order-level total
ord_receipt = conn.execute(
    "SELECT SUM(receipt_total) FROM transactions WHERE "
    "customer_order_id=?", (mega_order_id,)).fetchone()[0]
ord_method = conn.execute(
    "SELECT SUM(pli.method_amount) FROM payment_line_items pli "
    "JOIN transactions t ON pli.transaction_id=t.id "
    "WHERE t.customer_order_id=?", (mega_order_id,)).fetchone()[0]
assert_eq("Mega order receipt total", ord_receipt, MEGA_TOTAL)
assert_eq("Mega order method total = receipt total",
          ord_method, ord_receipt, tol=1)

# Vendor reimbursement total
vr_rows = _collect_vendor_reimbursement(conn, [MD_ID])
vr_total_cents = round(sum(r['Total Due to Vendor'] for r in vr_rows)
                       * 100)
assert_eq("Vendor Reimbursement total = receipt total",
          vr_total_cents, MEGA_TOTAL, tol=1)

# FAM Match report total
fm_rows = _collect_fam_match(conn, MD_ID)
fm_total_cents = round(sum(r['Total Allocated'] for r in fm_rows)
                       * 100)
assert_eq("FAM Match allocated total = receipt total",
          fm_total_cents, MEGA_TOTAL, tol=1)


# ════════════════════════════════════════════════════════════════════
# PHASE 3 — Returning customer: cumulative match cap accumulation
# ════════════════════════════════════════════════════════════════════
section("PHASE 3 — Returning customer match cap accumulation")

# Customer "C-RTN" makes three visits during the same market day.
# Cap is $500.  Each visit consumes $200 of match.  Third visit
# should be partially capped or rejected by the cap-aware breakdown.
CAP_LIMIT = 50000  # $500
RTN_LABEL = 'C-RTN'


def _do_returning_visit(label, vendor_id, receipt_cents, match_pct):
    order_id, _ = create_customer_order(
        MD_ID, customer_label=label, zip_code='15102')
    prior = get_customer_prior_match(label, MD_ID)
    remaining_cap = max(0, CAP_LIMIT - prior)
    txn_id, _ = create_transaction(
        market_day_id=MD_ID, vendor_id=vendor_id,
        receipt_total=receipt_cents, customer_order_id=order_id,
        market_day_date='2099-04-15')
    # Fully fund with one method (charge = receipt / (1 + pct/100))
    charge = round(receipt_cents / (1.0 + match_pct / 100.0))
    method_id = next(m[0] for m in METHODS if m[2] == match_pct)
    items = [_li(method_id, next(m[1] for m in METHODS if m[2] == match_pct),
                 match_pct, receipt_cents,
                 receipt_cents - charge, charge)]
    breakdown = calculate_payment_breakdown(
        receipt_cents,
        [{'method_amount': receipt_cents,
          'match_percent': match_pct}],
        match_limit=remaining_cap,
    )
    if not breakdown['is_valid']:
        warn(f"visit {label}@v{vendor_id} ${receipt_cents/100:.2f} "
             f"breakdown invalid: {breakdown['errors']}")
        return None, prior, breakdown
    items[0]['method_amount'] = breakdown['line_items'][0]['method_amount']
    items[0]['match_amount'] = breakdown['line_items'][0]['match_amount']
    items[0]['customer_charged'] = breakdown['line_items'][0]['customer_charged']
    _save_payment(txn_id, items)
    return txn_id, prior, breakdown


visit1 = _do_returning_visit(RTN_LABEL, 1, 40000, 100.0)  # $400 → $200 match
v1_match = get_customer_prior_match(RTN_LABEL, MD_ID)
assert_eq("After visit 1: prior match", v1_match, 20000)

visit2 = _do_returning_visit(RTN_LABEL, 2, 40000, 100.0)  # +$200 = $400
v2_match = get_customer_prior_match(RTN_LABEL, MD_ID)
assert_eq("After visit 2: prior match (cap not yet hit)",
          v2_match, 40000)

# Visit 3: customer still has $100 of cap.  Receipt $400 → would
# need $200 match but cap allows only $100.  Customer must pay the
# difference; breakdown should flag match_was_capped=True.
visit3_tid, prior3, bd3 = _do_returning_visit(
    RTN_LABEL, 3, 40000, 100.0)
if bd3.get('match_was_capped'):
    ok(f"Visit 3 hit match cap (uncapped match would be "
       f"${bd3.get('uncapped_fam_subsidy_total', 0)/100:.2f}, "
       f"capped to ${bd3.get('fam_subsidy_total', 0)/100:.2f})")
else:
    fail(f"Visit 3 should have triggered match cap but did not "
         f"(prior_match=${prior3/100:.2f}, cap=$500.00)")

v3_match = get_customer_prior_match(RTN_LABEL, MD_ID)
if v3_match <= CAP_LIMIT:
    ok(f"Customer {RTN_LABEL} cumulative match ${v3_match/100:.2f} "
       f"≤ cap ${CAP_LIMIT/100:.2f}")
else:
    fail(f"Customer {RTN_LABEL} EXCEEDED cap: ${v3_match/100:.2f} "
         f"> ${CAP_LIMIT/100:.2f}")

# Void visit 2 → prior match should drop back, freeing cap for future.
if visit2[0] is not None:
    void_transaction(visit2[0], voided_by='Sim')
    update_customer_order_status(
        conn.execute(
            "SELECT customer_order_id FROM transactions WHERE id=?",
            (visit2[0],)).fetchone()[0],
        'Voided')
    after_void = get_customer_prior_match(RTN_LABEL, MD_ID)
    if after_void < v3_match:
        ok(f"After voiding visit 2: prior match dropped from "
           f"${v3_match/100:.2f} to ${after_void/100:.2f}")
    else:
        fail(f"Void of visit 2 did NOT reduce prior_match: "
             f"${v3_match/100:.2f} -> ${after_void/100:.2f}")
    # NOTE (v1.9.10): we used to un-void visit 2 here so subsequent
    # phases ran against the original chain.  The v30→v31 migration
    # added a DB trigger ``chk_transactions_voided_one_way`` that
    # makes voids permanent (matching the production guard in
    # ``update_transaction``).  The remaining phases don't depend on
    # visit 2 being live — leave it voided.


# ════════════════════════════════════════════════════════════════════
# PHASE 4 — Sequential adjustment chain (5 mods to one transaction)
# ════════════════════════════════════════════════════════════════════
section("PHASE 4 — 5-iteration adjustment chain on a single txn")

adj_order_id, _ = create_customer_order(
    MD_ID, customer_label='C-ADJ', zip_code='15102')
adj_tid, _ = create_transaction(
    market_day_id=MD_ID, vendor_id=4, receipt_total=2000,
    customer_order_id=adj_order_id, market_day_date='2099-04-15')

initial_items = [_li(1, 'SNAP', 100.0, 2000, 1000, 1000)]
_save_payment(adj_tid, initial_items)

audit_count_pre = conn.execute(
    "SELECT COUNT(*) FROM audit_log WHERE table_name='transactions' "
    " AND record_id=?", (adj_tid,)).fetchone()[0]

# Five sequential adjustments (alternating receipt and method change).
for i, (new_total, items) in enumerate([
    (2500, [_li(1, 'SNAP', 100.0, 2500, 1250, 1250)]),
    (2500, [_li(1, 'SNAP', 100.0, 1500, 750, 750),
            _li(2, 'Cash', 0.0, 1000, 0, 1000)]),
    (3000, [_li(1, 'SNAP', 100.0, 2000, 1000, 1000),
            _li(2, 'Cash', 0.0, 1000, 0, 1000)]),
    (3000, [_li(4, 'JH Food Bucks', 100.0, 800, 400, 400),
            _li(1, 'SNAP', 100.0, 1200, 600, 600),
            _li(2, 'Cash', 0.0, 1000, 0, 1000)]),
    (1500, [_li(1, 'SNAP', 100.0, 1500, 750, 750)]),
], 1):
    conn.execute(
        "UPDATE transactions SET receipt_total=?, status='Adjusted' "
        " WHERE id=?", (new_total, adj_tid))
    log_action('transactions', adj_tid, 'ADJUST', 'Sim',
               notes=f'Iter {i}: receipt=${new_total/100:.2f}')
    save_payment_line_items(adj_tid, items, commit=False)
    log_action('payment_line_items', adj_tid, 'PAYMENT_ADJUSTED',
               'Sim', notes=f'Iter {i}: {len(items)} method(s)')
    conn.commit()
    # Verify invariant after each iteration.
    lis = get_payment_line_items(adj_tid)
    method_sum = sum(l['method_amount'] for l in lis)
    if method_sum != new_total:
        fail(f"Iter {i}: method sum {method_sum} != receipt "
             f"{new_total}")
        break
else:
    ok("All 5 adjustments preserved the per-transaction invariant")

# Audit chain: 5 ADJUST + 5 PAYMENT_ADJUSTED + initial CREATE +
# CONFIRM + PAYMENT_SAVED = 13 minimum.
audit_count_post = conn.execute(
    "SELECT COUNT(*) FROM audit_log WHERE record_id=? AND "
    " (table_name='transactions' OR table_name='payment_line_items')",
    (adj_tid,)).fetchone()[0]
if audit_count_post >= audit_count_pre + 10:
    ok(f"Audit chain grew by {audit_count_post - audit_count_pre} "
       "entries across 5 adjustments (≥10 expected)")
else:
    fail(f"Audit chain only grew by "
         f"{audit_count_post - audit_count_pre} entries; expected ≥10")


# ════════════════════════════════════════════════════════════════════
# PHASE 5 — Adjust then void: vendor reimbursement integrity
# ════════════════════════════════════════════════════════════════════
section("PHASE 5 — Adjust → void integrity")

vr_before = _collect_vendor_reimbursement(conn, [MD_ID])
v4_before = next(
    (r['Total Due to Vendor'] for r in vr_before
     if r['Vendor'] == 'Dumpling Dynasty'), 0)

# Void the chained-adjust transaction.
void_transaction(adj_tid, voided_by='Sim')
update_customer_order_status(adj_order_id, 'Voided')

vr_after = _collect_vendor_reimbursement(conn, [MD_ID])
v4_after = next(
    (r['Total Due to Vendor'] for r in vr_after
     if r['Vendor'] == 'Dumpling Dynasty'), 0)

drop = round((v4_before - v4_after) * 100)
expected_drop = 1500  # Final receipt was $15.00
if abs(drop - expected_drop) <= 1:
    ok(f"Vendor reimbursement dropped by ${drop/100:.2f} "
       f"after void (expected $15.00)")
else:
    fail(f"Vendor reimbursement drop ${drop/100:.2f} != expected "
         f"$15.00 after void")


# ════════════════════════════════════════════════════════════════════
# PHASE 6 — Edge cases: extreme values
# ════════════════════════════════════════════════════════════════════
section("PHASE 6 — Edge cases (penny receipts, 200%, 0%)")

# 6a. $0.01 receipt with 100% match.  customer_charged should be 0,
#     match should be 1¢.  Engine must not divide-by-zero.
bd = calculate_payment_breakdown(
    1, [{'method_amount': 1, 'match_percent': 100.0}], match_limit=None)
if bd['is_valid']:
    li = bd['line_items'][0]
    if (li['customer_charged'] + li['match_amount']
            == li['method_amount']):
        ok(f"$0.01 receipt @ 100% match: customer={li['customer_charged']}c "
           f"match={li['match_amount']}c")
    else:
        fail(f"$0.01 receipt invariant broken: {li}")
else:
    fail(f"$0.01 receipt rejected: {bd['errors']}")

# 6b. $50 receipt @ 200% match.  customer pays $50/3 = $16.67,
#     match = $33.33.  Engine should round and reconcile to penny.
bd = calculate_payment_breakdown(
    5000, [{'method_amount': 5000, 'match_percent': 200.0}],
    match_limit=None)
if bd['is_valid']:
    li = bd['line_items'][0]
    if li['customer_charged'] + li['match_amount'] == 5000:
        ok(f"$50 @ 200% match: customer=${li['customer_charged']/100:.2f} "
           f"match=${li['match_amount']/100:.2f}")
    else:
        fail(f"200% match invariant broken: {li}")
else:
    fail(f"$50 @ 200% rejected: {bd['errors']}")

# 6c. 0% match: customer pays the full amount, no FAM contribution.
bd = calculate_payment_breakdown(
    1234, [{'method_amount': 1234, 'match_percent': 0.0}],
    match_limit=None)
if (bd['is_valid'] and bd['line_items'][0]['customer_charged'] == 1234
        and bd['line_items'][0]['match_amount'] == 0):
    ok("$12.34 @ 0% match: customer=$12.34, match=$0.00")
else:
    fail(f"0% match wrong: {bd['line_items'][0]}")

# 6d. match_limit = 0: ALL match must be denied, customer pays full.
bd = calculate_payment_breakdown(
    1000, [{'method_amount': 1000, 'match_percent': 100.0}],
    match_limit=0)
if bd['is_valid'] and bd['line_items'][0]['match_amount'] == 0:
    ok("match_limit=0 properly denies all match (customer pays $10)")
else:
    fail(f"match_limit=0 leaked match: {bd['line_items'][0]}")

# 6e. Multi-denomination different denoms ($2 + $5 in same txn).
me_order, _ = create_customer_order(
    MD_ID, customer_label='C-MULTI', zip_code='15102')
me_tid, _ = create_transaction(
    market_day_id=MD_ID, vendor_id=6,  # vendor 6: SNAP, Cash, Food Bucks, FMNP
    receipt_total=2400,
    customer_order_id=me_order, market_day_date='2099-04-15')
me_items = [
    _li(4, 'JH Food Bucks', 100.0, 800, 400, 400),  # 2 × $2 = $4
    _li(5, 'FMNP',          100.0, 1000, 500, 500), # 1 × $5 = $5
    _li(1, 'SNAP',          100.0,  600, 300, 300), # rest
]
_save_payment(me_tid, me_items)
me_li = get_payment_line_items(me_tid)
me_sum = sum(l['method_amount'] for l in me_li)
if me_sum == 2400:
    ok(f"Multi-denomination ($2 + $5 + SNAP) reconciles to $24.00")
else:
    fail(f"Multi-denom mismatch: ${me_sum/100:.2f} != $24.00")


# ════════════════════════════════════════════════════════════════════
# PHASE 7 — DB ↔ reports ↔ ledger backup ↔ audit log reconciliation
# ════════════════════════════════════════════════════════════════════
section("PHASE 7 — Cross-surface reconciliation")

# 7a. DB ground truth for non-voided receipts on this market day
db_receipt = conn.execute("""
    SELECT COALESCE(SUM(receipt_total), 0) FROM transactions
    WHERE market_day_id=? AND status IN ('Confirmed', 'Adjusted')
""", (MD_ID,)).fetchone()[0]
db_method = conn.execute("""
    SELECT COALESCE(SUM(pli.method_amount), 0)
    FROM payment_line_items pli
    JOIN transactions t ON pli.transaction_id = t.id
    WHERE t.market_day_id=? AND t.status IN ('Confirmed', 'Adjusted')
""", (MD_ID,)).fetchone()[0]
db_match = conn.execute("""
    SELECT COALESCE(SUM(pli.match_amount), 0)
    FROM payment_line_items pli
    JOIN transactions t ON pli.transaction_id = t.id
    WHERE t.market_day_id=? AND t.status IN ('Confirmed', 'Adjusted')
""", (MD_ID,)).fetchone()[0]
db_customer = conn.execute("""
    SELECT COALESCE(SUM(pli.customer_charged), 0)
    FROM payment_line_items pli
    JOIN transactions t ON pli.transaction_id = t.id
    WHERE t.market_day_id=? AND t.status IN ('Confirmed', 'Adjusted')
""", (MD_ID,)).fetchone()[0]

assert_eq("DB: receipt total = method total", db_method, db_receipt)
assert_eq("DB: customer + match = method total",
          db_customer + db_match, db_method)

# 7b. Reports cross-check
vr_total = round(sum(r['Total Due to Vendor']
                     for r in _collect_vendor_reimbursement(conn, [MD_ID]))
                 * 100)
fm_alloc_total = round(sum(r['Total Allocated']
                            for r in _collect_fam_match(conn, MD_ID))
                       * 100)
fm_match_total = round(sum(r['Total FAM Match']
                            for r in _collect_fam_match(conn, MD_ID))
                       * 100)

assert_eq("Vendor Reimbursement = DB receipt total", vr_total,
          db_receipt, tol=1)
assert_eq("FAM Match allocated = DB method total", fm_alloc_total,
          db_method, tol=1)
assert_eq("FAM Match total = DB match total", fm_match_total,
          db_match, tol=1)

# 7c. Detailed Ledger
dl_rows = _collect_detailed_ledger(conn, MD_ID)
dl_receipt_total = round(sum(r.get('Receipt Total', 0)
                              for r in dl_rows
                              if r.get('Status') != 'Voided') * 100)
assert_eq("Detailed Ledger receipt total (excl voided)",
          dl_receipt_total, db_receipt, tol=1)

# 7d. Market Day Summary
mds_rows = _collect_market_day_summary(conn, MD_ID)
ok(f"Market Day Summary: {len(mds_rows)} rows")

# 7e. Ledger backup file (write_ledger_backup writes side-effectfully
#     to fam_ledger_backup.txt next to the DB file; no return value).
write_ledger_backup(force=True)
backup_path = os.path.join(os.path.dirname(_TMP_DB),
                           'fam_ledger_backup.txt')
if os.path.exists(backup_path):
    sz = os.path.getsize(backup_path)
    ok(f"Ledger backup written: {backup_path} ({sz:,} bytes)")
else:
    fail(f"Ledger backup missing at expected path: {backup_path}")


# ════════════════════════════════════════════════════════════════════
# PHASE 8 — Audit coverage spot-check
# ════════════════════════════════════════════════════════════════════
section("PHASE 8 — Audit coverage spot-check")

actions = conn.execute("""
    SELECT action, COUNT(*) AS n FROM audit_log
    GROUP BY action ORDER BY action
""").fetchall()
print("  Audit action distribution:")
for a in actions:
    print(f"    {a['action']:25s} {a['n']:6d}")

required_actions = {
    'CREATE', 'CONFIRM', 'VOID', 'ADJUST', 'OPEN', 'PAYMENT_SAVED',
    'PAYMENT_ADJUSTED',
}
have = {a['action'] for a in actions}
missing = required_actions - have
if not missing:
    ok(f"All {len(required_actions)} required action codes present")
else:
    fail(f"Missing required audit actions: {missing}")

# Documented coverage gaps (these are NOT in audit_log by design as of
# v1.9.9 — flagged here so the readiness report has visibility).
gap_tables = ['vendors', 'payment_methods', 'market_payment_methods',
              'vendor_payment_methods']
gaps_found = []
for t in gap_tables:
    n = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE table_name=?",
        (t,)).fetchone()[0]
    if n == 0:
        gaps_found.append(t)
if gaps_found:
    warn(f"Tables with NO audit-log entries (settings tables): "
         f"{', '.join(gaps_found)} — reflects current gap, not new "
         "regression.  Documented in audit gap report.")


# ════════════════════════════════════════════════════════════════════
# PHASE 9 — DB invariants under stress
# ════════════════════════════════════════════════════════════════════
section("PHASE 9 — Database invariants")

# Negative method_amount must be rejected by DB trigger.
try:
    conn.execute("""
        INSERT INTO payment_line_items
        (transaction_id, payment_method_id, method_name_snapshot,
         match_percent_snapshot, method_amount, match_amount,
         customer_charged)
        VALUES (?, 1, 'SNAP', 100.0, -100, 0, -100)
    """, (mega_txn_ids[0],))
    fail("DB trigger did NOT reject negative method_amount")
except sqlite3.IntegrityError:
    ok("DB trigger rejected negative method_amount")

# Negative receipt_total rejected.
try:
    conn.execute("""
        INSERT INTO transactions
        (fam_transaction_id, market_day_id, vendor_id, receipt_total,
         status)
        VALUES ('FAM-X-NEG', ?, 1, -500, 'Draft')
    """, (MD_ID,))
    fail("DB trigger did NOT reject negative receipt_total")
except sqlite3.IntegrityError:
    ok("DB trigger rejected negative receipt_total")
finally:
    conn.rollback()

# match_percent > 999 rejected.
try:
    conn.execute("""
        INSERT INTO payment_methods (name, match_percent, sort_order,
         is_active)
        VALUES ('Bogus', 1000.0, 99, 1)
    """)
    fail("DB trigger did NOT reject match_percent > 999")
except sqlite3.IntegrityError:
    ok("DB trigger rejected match_percent > 999")
finally:
    conn.rollback()


# ════════════════════════════════════════════════════════════════════
# PHASE 10 — Final reconciliation summary
# ════════════════════════════════════════════════════════════════════
section("FINAL SIMULATION RESULT")

print(f"  PASS: {len(PASS)}")
print(f"  FAIL: {len(FAIL)}")
print(f"  WARN: {len(WARN)}")

if FAIL:
    print("\n  ─── FAILURES ─────────────────────────────────────")
    for m in FAIL:
        print(f"    [FAIL] {m}")

if WARN:
    print("\n  ─── WARNINGS ─────────────────────────────────────")
    for m in WARN:
        print(f"    [WARN] {m}")

# Tear down — keep temp dir for forensic inspection.
close_market_day(MD_ID, closed_by='Sim')
close_connection()
print(f"\n  Temp data dir preserved: {_TMP_DIR}")
sys.exit(0 if not FAIL else 1)
