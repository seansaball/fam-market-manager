"""Randomized fuzz simulator for FAM Market Manager.

A property-based, state-machine-driven fuzzer that generates
realistic-but-unpredictable market-day activity and asserts every
financial invariant after every mutation.  Failure surfaces a
seed + action log that reproduces the exact sequence byte-for-byte.

Discipline
----------
* Seeded random: master seed → per-run seed → per-action seed.
* Bounded randomness: every random value comes from a realistic
  range (no $1M receipts, no impossible vendor counts).
* State-machine moves: only legal moves are generated at each step.
* Invariants after every action — not just at end of run.
* Replayable: ``python -m scripts.fuzz_simulator --seed N`` runs
  the same sequence.  Failure dumps include seed + action log.
* Isolated tempfile DB per seed.  Never touches real data.

Usage
-----
::

    # Default smoke run (5 seeds × 100 actions)
    python -m scripts.fuzz_simulator

    # Single seed, deterministic replay
    python -m scripts.fuzz_simulator --seed 42 --actions 100

    # Stress run (more actions)
    python -m scripts.fuzz_simulator --seeds 100,101,102,103,104 --actions 500

Exit code 0 on full pass; 1 if any invariant fails.
"""

import argparse
import json
import os
import random
import sqlite3
import sys
import tempfile
import traceback
from collections import Counter
from datetime import date

# Must isolate to a temp DB BEFORE importing anything that calls
# get_connection() — otherwise the first import would land on the
# user's real fam_data.db.
_TMP_DIR = tempfile.mkdtemp(prefix='fam_fuzz_')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.utils.calculations import (
    calculate_payment_breakdown, charge_to_method_amount,
)
from fam.utils.app_settings import set_market_code, set_setting


# ════════════════════════════════════════════════════════════════════
# Realistic business bounds (no toy edge cases)
# ════════════════════════════════════════════════════════════════════

BOUNDS = {
    'n_vendors':            (8, 15),         # vendors per market
    'n_methods':            6,               # SNAP, Cash, Food RX, Food Bucks, FMNP, Premium
    'n_customers':          (20, 100),       # per market day
    'n_actions':            100,             # default per seed; CLI overrides
    'receipt_cents':        (50, 25000),     # $0.50 .. $250.00
    'vendors_per_order':    (1, 10),         # 1..10 vendors per customer order
    'methods_per_txn':      (1, 4),          # 1..4 methods per transaction
    'match_cap_cents':      (5000, 50000),   # $50 .. $500 per customer per day
    # Action weights (must sum to 1.0)
    'weight_create_confirm': 0.70,
    'weight_adjust':         0.15,
    'weight_void':           0.10,
    'weight_returning':      0.05,
}


# ════════════════════════════════════════════════════════════════════
# Master fixture: 8-15 vendors, 6 methods, full eligibility
# ════════════════════════════════════════════════════════════════════

METHODS = [
    # (id, name, match_pct, denomination_cents, sort_order)
    (1, 'SNAP',          100.0, None, 1),
    (2, 'Cash',            0.0, None, 2),
    (3, 'Food RX',        50.0, None, 3),
    (4, 'JH Food Bucks', 100.0,  200, 4),
    (5, 'FMNP',          100.0,  500, 5),
    (6, 'Premium Match', 200.0, None, 6),
]


def seed_db(rng: random.Random, db_path: str, match_cap: int):
    close_connection()
    set_db_path(db_path)
    initialize_database()
    set_market_code('FUZZ')
    set_setting('device_id', 'fuzz-device')

    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'FuzzMarket', ?, 1)",
        (match_cap,))

    n_vendors = rng.randint(*BOUNDS['n_vendors'])
    for vid in range(1, n_vendors + 1):
        conn.execute("INSERT INTO vendors (id, name) VALUES (?, ?)",
                     (vid, f'V{vid:02d}'))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))

    for mid, name, pct, denom, sort_o in METHODS:
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            " denomination, sort_order, is_active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (mid, name, pct, denom, sort_o))
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, "
            " payment_method_id) VALUES (1, ?)", (mid,))

    # Heterogeneous eligibility: each vendor accepts a random
    # subset of methods, but always SNAP + Cash so allocation is
    # always feasible.
    for vid in range(1, n_vendors + 1):
        accepted = {1, 2}  # SNAP + Cash always
        for mid in (3, 4, 5, 6):
            if rng.random() < 0.6:
                accepted.add(mid)
        for mid in sorted(accepted):
            conn.execute(
                "INSERT INTO vendor_payment_methods (vendor_id, "
                " payment_method_id) VALUES (?, ?)", (vid, mid))

    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2099-04-15', 'Open', 'Fuzz')")
    conn.commit()
    return conn, n_vendors


# ════════════════════════════════════════════════════════════════════
# Invariant assertions (run after EVERY action)
# ════════════════════════════════════════════════════════════════════

class InvariantFailure(Exception):
    """Raised when any financial invariant fails."""


def check_all_invariants(conn, action_log, action_idx, match_cap):
    """Returns silently on success; raises ``InvariantFailure``
    with a detailed message on any violation."""
    failures = []

    # I1: per-line invariant (system-method exempt)
    bad_lines = conn.execute("""
        SELECT id, transaction_id, method_amount, match_amount,
               customer_charged, method_name_snapshot
        FROM payment_line_items
        WHERE customer_charged + match_amount != method_amount
          AND method_name_snapshot != 'Unallocated Funds'
    """).fetchall()
    if bad_lines:
        for r in bad_lines:
            failures.append(
                f"I1 violated: pli_id={r['id']} txn={r['transaction_id']} "
                f"method={r['method_amount']} customer={r['customer_charged']} "
                f"match={r['match_amount']} (sum={r['customer_charged'] + r['match_amount']})"
            )

    # I2: per-txn reconciliation (Confirmed + Adjusted only)
    bad_txns = conn.execute("""
        SELECT t.id, t.receipt_total,
               COALESCE(SUM(pli.method_amount), 0) AS method_sum,
               t.status
        FROM transactions t
        LEFT JOIN payment_line_items pli ON pli.transaction_id = t.id
        WHERE t.status IN ('Confirmed', 'Adjusted')
        GROUP BY t.id
        HAVING t.receipt_total != COALESCE(SUM(pli.method_amount), 0)
    """).fetchall()
    if bad_txns:
        for r in bad_txns:
            failures.append(
                f"I2 violated: txn={r['id']} status={r['status']} "
                f"receipt={r['receipt_total']} method_sum={r['method_sum']}"
            )

    # I3: report surfaces match DB
    db_receipt = conn.execute("""
        SELECT COALESCE(SUM(receipt_total), 0) FROM transactions
        WHERE status IN ('Confirmed', 'Adjusted') AND market_day_id = 1
    """).fetchone()[0]

    from fam.sync.data_collector import (
        _collect_vendor_reimbursement, _collect_fam_match,
    )
    vr_total = round(sum(r['Total Due to Vendor']
                          for r in _collect_vendor_reimbursement(conn, [1]))
                     * 100)
    if vr_total != db_receipt:
        failures.append(
            f"I3 (Vendor Reimbursement): csv={vr_total}c db={db_receipt}c "
            f"diff={vr_total - db_receipt}c")

    fm_alloc = round(sum(r['Total Allocated']
                          for r in _collect_fam_match(conn, 1))
                     * 100)
    # FAM Match Allocated may include Unallocated Funds
    # method_amount, which IS in DB receipt sum, so they should
    # match.  Allow ±1¢ for any rounding through the report layer.
    if abs(fm_alloc - db_receipt) > 1:
        failures.append(
            f"I3 (FAM Match Allocated): csv={fm_alloc}c db={db_receipt}c")

    # I5: per-customer match cap
    over_cap = conn.execute("""
        SELECT co.customer_label,
               COALESCE(SUM(pli.match_amount), 0) AS total_match
        FROM customer_orders co
        JOIN transactions t ON t.customer_order_id = co.id
        JOIN payment_line_items pli ON pli.transaction_id = t.id
        WHERE co.market_day_id = 1
          AND co.status IN ('Confirmed', 'Adjusted')
          AND t.status IN ('Confirmed', 'Adjusted')
        GROUP BY co.customer_label
        HAVING total_match > ?
    """, (match_cap,)).fetchall()
    if over_cap:
        for r in over_cap:
            failures.append(
                f"I5 violated: customer={r['customer_label']} "
                f"match={r['total_match']}c cap={match_cap}c")

    # I6: no negative amounts
    n = conn.execute("""
        SELECT COUNT(*) FROM payment_line_items
        WHERE method_amount < 0 OR match_amount < 0 OR customer_charged < 0
    """).fetchone()[0]
    if n:
        failures.append(f"I6 violated: {n} negative payment_line_items")

    if failures:
        # Dump action log so the failure is reproducible.
        msg = (f"Invariant failure after action #{action_idx}\n  "
                + "\n  ".join(failures))
        raise InvariantFailure(msg)


# ════════════════════════════════════════════════════════════════════
# State-machine action generators
# ════════════════════════════════════════════════════════════════════

class FuzzState:
    """Tracks live model state so we can pick legal moves."""

    def __init__(self, n_vendors: int, match_cap: int):
        self.n_vendors = n_vendors
        self.match_cap = match_cap
        self.confirmed_txn_ids: list[int] = []
        self.customer_labels: list[str] = []
        self._next_customer = 1

    def new_customer(self) -> str:
        label = f'C-FUZZ-{self._next_customer:04d}'
        self.customer_labels.append(label)
        self._next_customer += 1
        return label

    def maybe_returning_customer(self, rng) -> str:
        """30% chance to reuse an existing label, else new."""
        if self.customer_labels and rng.random() < 0.30:
            return rng.choice(self.customer_labels)
        return self.new_customer()


def _eligible_methods_for_vendor(conn, vendor_id):
    rows = conn.execute(
        "SELECT pm.* FROM payment_methods pm "
        "JOIN vendor_payment_methods vpm ON vpm.payment_method_id = pm.id "
        "WHERE vpm.vendor_id = ? AND pm.is_active = 1",
        (vendor_id,)).fetchall()
    return [dict(r) for r in rows]


def _generate_legal_breakdown(rng, conn, receipt_cents, vendor_id,
                                customer_label, match_cap):
    """Build a payment_entries list whose method_amounts sum to
    ``receipt_cents`` exactly and whose match never exceeds the
    customer's remaining cap.  Returns ``items`` list of dicts.

    Uses the engine to enforce the per-line invariant — the
    fuzzer never hand-crafts invariant-violating rows.
    """
    eligible = _eligible_methods_for_vendor(conn, vendor_id)
    if not eligible:
        return None

    # Compute customer's remaining match cap.
    from fam.models.customer_order import get_customer_prior_match
    prior = get_customer_prior_match(customer_label, 1)
    remaining_cap = max(0, match_cap - prior)

    # Random number of methods to use, capped by available eligible.
    n = min(rng.randint(*BOUNDS['methods_per_txn']), len(eligible))
    methods = rng.sample(eligible, n)

    # Distribute receipt across methods.  To keep things realistic
    # and feasible, assign to non-denom methods proportionally and
    # clip denom to whole units.
    payment_entries = []
    remaining = receipt_cents

    # Sort denominated first (they consume in fixed-unit chunks),
    # then non-denom.
    denom_methods = [m for m in methods if m.get('denomination')]
    non_denom_methods = [m for m in methods if not m.get('denomination')]

    for m in denom_methods:
        denom = m['denomination']
        max_units = max(0, remaining // denom)
        if max_units == 0:
            continue
        units = rng.randint(0, min(max_units, 5))  # cap at 5 units
        if units == 0:
            continue
        ma = units * denom
        payment_entries.append({
            'payment_method_id': m['id'],
            'method_name_snapshot': m['name'],
            'match_percent_snapshot': m['match_percent'],
            'method_amount': ma,
            'match_percent': m['match_percent'],
        })
        remaining -= ma

    if remaining > 0:
        if not non_denom_methods:
            # Need a non-denom absorber but none picked — try Cash
            cash = next((m for m in eligible if m['name'] == 'Cash'), None)
            if cash:
                non_denom_methods = [cash]
            else:
                return None  # can't allocate
        # Spread remaining across non-denom methods.  For simplicity
        # use just one (the most common real-world flow).
        m = rng.choice(non_denom_methods)
        payment_entries.append({
            'payment_method_id': m['id'],
            'method_name_snapshot': m['name'],
            'match_percent_snapshot': m['match_percent'],
            'method_amount': remaining,
            'match_percent': m['match_percent'],
        })
        remaining = 0

    if not payment_entries:
        return None

    # Run breakdown engine to apply cap + penny reconciliation.
    breakdown = calculate_payment_breakdown(
        receipt_cents,
        [{'method_amount': e['method_amount'],
          'match_percent': e['match_percent']}
         for e in payment_entries],
        match_limit=remaining_cap,
    )
    if not breakdown['is_valid']:
        return None

    items = []
    for entry, li in zip(payment_entries, breakdown['line_items']):
        items.append({
            'payment_method_id': entry['payment_method_id'],
            'method_name_snapshot': entry['method_name_snapshot'],
            'match_percent_snapshot': entry['match_percent_snapshot'],
            'method_amount': li['method_amount'],
            'match_amount': li['match_amount'],
            'customer_charged': li['customer_charged'],
        })
    return items


def action_create_and_confirm(rng, conn, state):
    """Create a customer order with random vendors + random
    receipts + legal allocations.  Confirms each transaction."""
    from fam.models.customer_order import (
        create_customer_order, update_customer_order_status,
    )
    from fam.models.transaction import (
        create_transaction, save_payment_line_items,
        confirm_transaction,
    )
    customer_label = state.maybe_returning_customer(rng)
    n_vendors = rng.randint(1, min(BOUNDS['vendors_per_order'][1],
                                     state.n_vendors))
    vendor_ids = rng.sample(range(1, state.n_vendors + 1), n_vendors)

    order_id, _ = create_customer_order(
        market_day_id=1, customer_label=customer_label,
        zip_code='15102')
    actions_taken = 0

    for vid in vendor_ids:
        receipt = rng.randint(*BOUNDS['receipt_cents'])
        items = _generate_legal_breakdown(
            rng, conn, receipt, vid, customer_label, state.match_cap)
        if items is None:
            continue
        try:
            tid, _ = create_transaction(
                market_day_id=1, vendor_id=vid,
                receipt_total=receipt,
                customer_order_id=order_id,
                market_day_date='2099-04-15')
            save_payment_line_items(tid, items, commit=False)
            confirm_transaction(tid, confirmed_by='Fuzz', commit=False)
            update_customer_order_status(order_id, 'Confirmed',
                                          commit=False)
            conn.commit()
            state.confirmed_txn_ids.append(tid)
            actions_taken += 1
        except Exception:
            conn.rollback()
            raise
    return {'op': 'create_and_confirm',
             'customer': customer_label,
             'vendors': vendor_ids,
             'txns_created': actions_taken}


def action_adjust(rng, conn, state):
    """Re-save payment line items with a randomly altered
    allocation on a random confirmed transaction."""
    if not state.confirmed_txn_ids:
        return {'op': 'adjust', 'skipped': 'no_confirmed_txns'}

    from fam.models.transaction import (
        save_payment_line_items, get_transaction_by_id,
    )
    from fam.models.audit import log_action

    tid = rng.choice(state.confirmed_txn_ids)
    t = get_transaction_by_id(tid)
    if t['status'] == 'Voided':
        return {'op': 'adjust', 'skipped': 'voided'}

    # Look up the customer label so cap calc is correct
    co = conn.execute(
        "SELECT customer_label FROM customer_orders "
        "WHERE id = (SELECT customer_order_id FROM transactions WHERE id=?)",
        (tid,)).fetchone()
    customer_label = co['customer_label'] if co else 'unknown'

    # Generate a new legal allocation for the same receipt total.
    items = _generate_legal_breakdown(
        rng, conn, t['receipt_total'], t['vendor_id'],
        customer_label, state.match_cap)
    if items is None:
        return {'op': 'adjust', 'skipped': 'no_legal_allocation'}

    try:
        conn.execute(
            "UPDATE transactions SET status='Adjusted' WHERE id=?",
            (tid,))
        save_payment_line_items(tid, items, commit=False)
        log_action('transactions', tid, 'ADJUST', 'Fuzz',
                   notes='Fuzz random adjustment', commit=False)
        log_action('payment_line_items', tid, 'PAYMENT_ADJUSTED',
                   'Fuzz', notes='Fuzz adjustment', commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {'op': 'adjust', 'txn_id': tid}


def action_void(rng, conn, state):
    """Void a random confirmed/adjusted transaction."""
    if not state.confirmed_txn_ids:
        return {'op': 'void', 'skipped': 'no_confirmed_txns'}

    from fam.models.transaction import (
        void_transaction, get_transaction_by_id,
    )
    from fam.models.customer_order import update_customer_order_status

    candidate_ids = [tid for tid in state.confirmed_txn_ids
                     if get_transaction_by_id(tid)['status'] != 'Voided']
    if not candidate_ids:
        return {'op': 'void', 'skipped': 'all_already_voided'}

    tid = rng.choice(candidate_ids)
    t = get_transaction_by_id(tid)
    void_transaction(tid, voided_by='Fuzz')
    if t.get('customer_order_id'):
        try:
            update_customer_order_status(t['customer_order_id'],
                                           'Voided')
        except Exception:
            pass
    return {'op': 'void', 'txn_id': tid}


# ════════════════════════════════════════════════════════════════════
# Main fuzz loop
# ════════════════════════════════════════════════════════════════════

ACTION_DISPATCH = [
    ('create_confirm', BOUNDS['weight_create_confirm'],
     action_create_and_confirm),
    ('adjust',          BOUNDS['weight_adjust'],
     action_adjust),
    ('void',            BOUNDS['weight_void'],
     action_void),
    ('returning',       BOUNDS['weight_returning'],
     action_create_and_confirm),  # alias of create_confirm
]


def pick_action(rng):
    r = rng.random()
    cum = 0.0
    for name, w, fn in ACTION_DISPATCH:
        cum += w
        if r < cum:
            return name, fn
    return ACTION_DISPATCH[0][0], ACTION_DISPATCH[0][2]


def run_seed(seed: int, n_actions: int) -> dict:
    """Run one fuzz session with the given seed.  Returns a result dict."""
    rng = random.Random(seed)
    db_path = os.path.join(_TMP_DIR, f'fuzz_{seed}.db')
    if os.path.exists(db_path):
        os.unlink(db_path)
    match_cap = rng.randint(*BOUNDS['match_cap_cents'])
    # Round to even cents so cap math stays clean.
    match_cap = (match_cap // 100) * 100

    conn, n_vendors = seed_db(rng, db_path, match_cap)
    state = FuzzState(n_vendors, match_cap)

    action_log = []
    op_counter: Counter = Counter()
    failure = None

    for i in range(n_actions):
        op_name, op_fn = pick_action(rng)
        op_counter[op_name] += 1
        try:
            details = op_fn(rng, conn, state)
            action_log.append({'idx': i, 'op': op_name,
                                'seed': seed, 'details': details})
        except Exception as e:
            failure = {
                'phase': 'action_execution',
                'action_idx': i,
                'op': op_name,
                'error': repr(e),
                'traceback': traceback.format_exc(),
            }
            break
        # Invariant check after every action.
        try:
            check_all_invariants(conn, action_log, i, match_cap)
        except InvariantFailure as e:
            failure = {
                'phase': 'invariant_check',
                'action_idx': i,
                'op': op_name,
                'error': str(e),
            }
            break

    close_connection()
    return {
        'seed': seed,
        'n_actions_attempted': n_actions,
        'n_actions_completed': len(action_log),
        'op_distribution': dict(op_counter),
        'n_confirmed_txns': len(state.confirmed_txn_ids),
        'n_customers': len(state.customer_labels),
        'match_cap_cents': match_cap,
        'n_vendors': n_vendors,
        'failure': failure,
        'action_log': action_log,
        'db_path': db_path,
    }


# ════════════════════════════════════════════════════════════════════
# Reporting
# ════════════════════════════════════════════════════════════════════

def dump_failure_report(result: dict, log_dir: str):
    seed = result['seed']
    fname = os.path.join(log_dir, f'fuzz_failure_seed_{seed}.json')
    with open(fname, 'w', encoding='utf-8') as f:
        json.dump({
            'seed': seed,
            'n_actions_completed': result['n_actions_completed'],
            'failure': result['failure'],
            'op_distribution': result['op_distribution'],
            'last_50_actions': result['action_log'][-50:],
        }, f, indent=2)
    return fname


def print_summary(results: list[dict], log_dir: str):
    total_actions = sum(r['n_actions_completed'] for r in results)
    total_txns = sum(r['n_confirmed_txns'] for r in results)
    total_customers = sum(r['n_customers'] for r in results)
    failures = [r for r in results if r['failure']]

    op_dist: Counter = Counter()
    for r in results:
        for k, v in r['op_distribution'].items():
            op_dist[k] += v

    print(f"\n{'=' * 72}")
    print(f"  FUZZ SIMULATOR — {len(results)} seeds")
    print(f"{'=' * 72}")
    print(f"  Total actions completed: {total_actions:,}")
    print(f"  Total confirmed txns:    {total_txns:,}")
    print(f"  Total unique customers:  {total_customers:,}")
    print(f"  Operation distribution:")
    for op, n in op_dist.most_common():
        print(f"    {op:20s} {n:6,d}")
    print(f"  Per-seed details:")
    for r in results:
        status = 'PASS' if not r['failure'] else 'FAIL'
        print(f"    seed={r['seed']:>5d} {status} "
              f"actions={r['n_actions_completed']:>5d} "
              f"txns={r['n_confirmed_txns']:>4d} "
              f"customers={r['n_customers']:>3d} "
              f"cap=${r['match_cap_cents']/100:>6.2f}")
    print(f"  Failures: {len(failures)}")
    for r in failures:
        f = r['failure']
        print(f"    seed={r['seed']} action_idx={f.get('action_idx')} "
              f"op={f.get('op')} phase={f.get('phase')}")
        rep = dump_failure_report(r, log_dir)
        print(f"      → reproduction artifact: {rep}")
        print(f"      → first 5 lines of error: ")
        for line in f.get('error', '').splitlines()[:5]:
            print(f"          {line}")
    print(f"{'=' * 72}\n")
    return 0 if not failures else 1


def main():
    parser = argparse.ArgumentParser(
        description="FAM Market Manager fuzz simulator")
    parser.add_argument('--seed', type=int,
                         help="Run a single deterministic seed")
    parser.add_argument('--seeds', type=str,
                         help="Comma-separated list of seeds")
    parser.add_argument('--actions', type=int,
                         default=BOUNDS['n_actions'],
                         help="Actions per seed")
    parser.add_argument('--log-dir', type=str,
                         default=_TMP_DIR,
                         help="Directory for failure dumps")
    args = parser.parse_args()

    if args.seed is not None:
        seeds = [args.seed]
    elif args.seeds:
        seeds = [int(s) for s in args.seeds.split(',')]
    else:
        # Default smoke run.
        seeds = [1, 2, 3, 4, 5]

    print(f"  Fuzz log directory: {args.log_dir}")
    print(f"  Seeds: {seeds}  Actions per seed: {args.actions}")

    results = []
    for seed in seeds:
        print(f"  Running seed {seed} ({args.actions} actions)...",
              end=' ', flush=True)
        r = run_seed(seed, args.actions)
        results.append(r)
        print('PASS' if not r['failure'] else 'FAIL')

    return print_summary(results, args.log_dir)


if __name__ == '__main__':
    sys.exit(main())
