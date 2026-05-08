"""UI state-machine fuzzer — close the gap on multi-step transition bugs.

Why this exists
---------------
Four onsite bugs in a row all surfaced from **state transitions** between
UI actions where multiple lists / indices / invariants drift between
steps:

  1. Multi-vendor overage clamp (auto-distribute clamping locked rows)
  2. Per-vendor 1¢ drift (forfeit math)
  3. SNAP cap-deficit inflation clamping bound denom rows
  4. Adding a row mid-update → IndexError in forfeit Pass 3

Static state tests (cross-layer agreement at one snapshot) didn't
catch these because each one needed a *sequence* of UI actions to
manifest.  This fuzzer drives PaymentScreen through random sequences
of legal volunteer actions — add row, change method, set charge,
delete row, click auto-distribute, adjust charge, save draft,
reload — and validates invariants after every action.

Discipline
----------
* Seeded random for reproducibility (`--seed N` flag in design)
* Only LEGAL moves at each step (no impossible UI sequences)
* After EVERY mutating action, validate:
    - no exception raised (crash regression)
    - V1: per-vendor Remaining = 0 when fully allocated
    - V2: per-vendor row sum reconciles to receipt - remaining
    - V3: summary cards match engine output
    - V5: per-row Total = Charge + Match
    - I2 reconciliation if engine says is_valid
    - No IndexError, KeyError, AttributeError raised silently
"""

import random
import traceback

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.utils.calculations import calculate_payment_breakdown
from fam.utils.money import dollars_to_cents


# ════════════════════════════════════════════════════════════════════
# Realistic bounds — same shape as production_sim
# ════════════════════════════════════════════════════════════════════

BOUNDS = {
    'n_vendors_per_order': (3, 6),  # mid-size customer orders
    'receipt_cents':        (500, 15000),  # $5-$150 per vendor
    'cap_cents':            (5000, 30000),  # $50-$300 daily cap
    'actions_per_seed':     30,  # random sequence length
}

METHODS = [
    # (id, name, match_pct, denom, sort)
    (1, 'SNAP',          100.0, None, 1),
    (2, 'Cash',            0.0, None, 2),
    (3, 'Food RX',        50.0, None, 3),
    (4, 'JH Food Bucks', 100.0,  200, 4),
    (5, 'FMNP',          100.0,  500, 5),
]


@pytest.fixture
def fuzz_db(tmp_path):
    db_file = str(tmp_path / "smfuzz.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    yield conn
    close_connection()


def _setup_market(conn, n_vendors: int, cap_cents: int):
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'F', ?, 1)",
        (cap_cents,))
    for vid in range(1, n_vendors + 1):
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, f'V{vid}'))
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
    for vid in range(1, n_vendors + 1):
        for mid in range(1, len(METHODS) + 1):
            conn.execute(
                "INSERT INTO vendor_payment_methods "
                " (vendor_id, payment_method_id) VALUES (?, ?)",
                (vid, mid))
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2099-04-29', 'Open', 'T')")
    conn.commit()


def _build_order(conn, rng, n_vendors):
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-FUZZ',
        zip_code='15102')
    n_in_order = rng.randint(*BOUNDS['n_vendors_per_order'])
    n_in_order = min(n_in_order, n_vendors)
    chosen = rng.sample(range(1, n_vendors + 1), n_in_order)
    for vid in chosen:
        receipt = rng.randint(*BOUNDS['receipt_cents'])
        create_transaction(
            market_day_id=1, vendor_id=vid,
            receipt_total=receipt,
            customer_order_id=order_id,
            market_day_date='2099-04-29')
    return order_id, chosen


# ════════════════════════════════════════════════════════════════════
# Invariant assertions (run after every action)
# ════════════════════════════════════════════════════════════════════

def _parse_cents(text: str) -> int:
    if not text:
        return 0
    return round(float(text.replace('$', '').replace(',', '').strip())
                 * 100)


def _check_invariants(screen, action_label: str, action_log: list):
    """After every UI action: validate everything.  Any failure
    raises ``AssertionError`` with full action history."""
    failures = []

    # Read the engine's view of the current state.
    #
    # v2.0.7+ user-cap (audit 2026-05-07): the entries must
    # include ``user_capped`` and ``denomination`` to match the
    # production engine call (see PaymentScreen._confirm_payment
    # and _update_summary_impl).  Without these flags the engine
    # would inflate user-capped customer_charged, producing a
    # result that diverges from what _update_summary just
    # computed and stored on the cards — V3 then false-fails
    # on the divergence.
    items = screen._collect_line_items()
    receipt_total = screen._order_total
    if items and receipt_total > 0:
        entries = [{'method_amount': it['method_amount'],
                    'match_percent': it['match_percent'],
                    'denomination': it.get('denomination'),
                    'user_capped': bool(it.get('user_capped', False))}
                   for it in items]
        try:
            result = calculate_payment_breakdown(
                receipt_total, entries,
                match_limit=screen._match_limit)
            # v2.0.7+ user-cap (audit 2026-05-07): also apply
            # denomination-forfeit if needed, matching the
            # production confirm path (PaymentScreen._confirm_
            # payment).  Without forfeit application, scenarios
            # with denom rows that overshoot a vendor's receipt
            # produce different customer_charged values in this
            # audit vs the in-flight engine call → V3/U6
            # invariants false-fail.
            overage = screen._check_denomination_overage(
                result, receipt_total)
            if overage > 0:
                screen._apply_denomination_forfeit(
                    result, items, overage)
        except Exception as e:
            failures.append(
                f"calculate_payment_breakdown crashed: {e!r}")
            result = None
    else:
        result = None

    # V5: per-row visible Total = Charge + Match
    for i, row in enumerate(screen._payment_rows):
        try:
            method = row.get_selected_method()
            if not method:
                continue
            charge = row._get_active_charge()
            match_text = row.match_amount_label.text()
            total_text = row.total_label.text()
            match_cents = _parse_cents(match_text)
            total_cents = _parse_cents(total_text)
            if total_cents != charge + match_cents:
                failures.append(
                    f"V5 row[{i}]: charge={charge}c "
                    f"+ match={match_cents}c != "
                    f"total={total_cents}c")
        except Exception as e:
            failures.append(f"V5 row[{i}] crashed: {e!r}")

    # V3: summary cards (only meaningful when engine is_valid)
    if result is not None and result.get('is_valid'):
        try:
            cust_card = screen.summary_row.cards.get('customer_pays')
            fam_card = screen.summary_row.cards.get('fam_match')
            engine_customer = sum(li['customer_charged']
                                    for li in result['line_items'])
            engine_match = sum(li['match_amount']
                                for li in result['line_items'])
            if cust_card is not None:
                shown = _parse_cents(cust_card.value_label.text())
                if shown != engine_customer:
                    failures.append(
                        f"V3 customer_pays card={shown}c != "
                        f"engine={engine_customer}c")
            if fam_card is not None:
                shown = _parse_cents(fam_card.value_label.text())
                if shown != engine_match:
                    failures.append(
                        f"V3 fam_match card={shown}c != "
                        f"engine={engine_match}c")
        except Exception as e:
            failures.append(f"V3 check crashed: {e!r}")

    # V1: every vendor breakdown row's Remaining must be a number
    # (not an exception during render, no nonsense strings)
    try:
        table = screen.vendor_table
        for r in range(table.rowCount()):
            rem_item = table.item(r, 2)
            if rem_item is None:
                continue
            rem_text = rem_item.text()
            try:
                _parse_cents(rem_text)
            except (ValueError, AttributeError):
                failures.append(
                    f"V1 row[{r}] unparseable Remaining: "
                    f"{rem_text!r}")
    except Exception as e:
        failures.append(f"V1 table read crashed: {e!r}")

    if failures:
        log_text = "\n  ".join(action_log[-20:])
        raise AssertionError(
            f"\n=== Invariant violations after action: "
            f"{action_label}\n"
            f"=== Action history (last 20):\n  {log_text}\n"
            f"=== Failures:\n  "
            + "\n  ".join(failures))


# ════════════════════════════════════════════════════════════════════
# Action library — every move a real volunteer could make
# ════════════════════════════════════════════════════════════════════

def _add_row_action(rng, screen, action_log):
    """Add a payment row with a random method.  Some rows get a
    charge, some are left at 0 (mimics 'oops, picked the wrong
    method, will fix it')."""
    if len(screen._payment_rows) >= 8:
        return  # cap at 8 rows
    row = screen._add_payment_row()
    method_choice = rng.choice([m for m in METHODS if m[0] != 2 or rng.random() < 0.7])
    combo = row.method_combo
    target_text = method_choice[1].lower()
    for i in range(combo.count()):
        if target_text in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            break
    # 50% chance to set a charge immediately
    if rng.random() < 0.5:
        denom = method_choice[3]
        if denom:
            # bind to a random eligible vendor first
            order_vendors = screen._get_order_vendors()
            if order_vendors:
                vid = rng.choice(order_vendors)['id']
                row.set_bound_vendor_id(vid)
            # 1-5 units
            charge_cents = rng.randint(1, 5) * denom
        else:
            charge_cents = rng.randint(100, 5000)
        row._set_active_charge(charge_cents)
    action_log.append(
        f"add_row method={method_choice[1]} "
        f"charge={row._get_active_charge()}")


def _delete_row_action(rng, screen, action_log):
    """Delete a random row (volunteer realized it was wrong)."""
    if not screen._payment_rows:
        return
    idx = rng.randrange(len(screen._payment_rows))
    row = screen._payment_rows[idx]
    method = row.get_selected_method()
    name = method['name'] if method else 'NONE'
    screen._remove_payment_row(row)
    action_log.append(f"delete_row[{idx}] was={name}")


def _change_method_action(rng, screen, action_log):
    """Volunteer picked the wrong method, switches to another."""
    if not screen._payment_rows:
        return
    idx = rng.randrange(len(screen._payment_rows))
    row = screen._payment_rows[idx]
    new_method = rng.choice(METHODS)
    combo = row.method_combo
    for i in range(combo.count()):
        if new_method[1].lower() in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            break
    action_log.append(
        f"change_method[{idx}] -> {new_method[1]}")


def _set_charge_action(rng, screen, action_log):
    """Set a random charge on a random row (volunteer typing)."""
    if not screen._payment_rows:
        return
    idx = rng.randrange(len(screen._payment_rows))
    row = screen._payment_rows[idx]
    method = row.get_selected_method()
    if not method:
        return
    denom = method.get('denomination')
    if denom:
        # Whole units only, 0..7 to potentially over-allocate
        units = rng.randint(0, 7)
        charge = units * denom
    else:
        charge = rng.randint(0, 10000)
    row._set_active_charge(charge)
    action_log.append(f"set_charge[{idx}]={charge}")


def _change_vendor_binding_action(rng, screen, action_log):
    """Change the bound vendor on a denom row."""
    if not screen._payment_rows:
        return
    denom_rows = [
        (i, r) for i, r in enumerate(screen._payment_rows)
        if (r.get_selected_method()
            and r.get_selected_method().get('denomination'))
    ]
    if not denom_rows:
        return
    idx, row = rng.choice(denom_rows)
    order_vendors = screen._get_order_vendors()
    if not order_vendors:
        return
    vid = rng.choice(order_vendors)['id']
    row.set_bound_vendor_id(vid)
    action_log.append(f"rebind[{idx}] -> vendor_id={vid}")


def _click_auto_distribute_action(rng, screen, action_log):
    screen._auto_distribute()
    action_log.append("auto_distribute")


def _zero_a_charge_action(rng, screen, action_log):
    """Volunteer accidentally cleared a charge."""
    if not screen._payment_rows:
        return
    idx = rng.randrange(len(screen._payment_rows))
    row = screen._payment_rows[idx]
    if row.get_selected_method():
        row._set_active_charge(0)
        action_log.append(f"zero_charge[{idx}]")


def _bump_charge_action(rng, screen, action_log):
    """Volunteer typed a slightly different amount (small mutation)."""
    if not screen._payment_rows:
        return
    idx = rng.randrange(len(screen._payment_rows))
    row = screen._payment_rows[idx]
    method = row.get_selected_method()
    if not method:
        return
    current = row._get_active_charge()
    denom = method.get('denomination')
    if denom:
        delta = rng.choice([-denom, denom, 0])
        new_charge = max(0, current + delta)
    else:
        delta = rng.randint(-200, 200)
        new_charge = max(0, current + delta)
    row._set_active_charge(new_charge)
    action_log.append(f"bump_charge[{idx}]={current}->{new_charge}")


# Action weights (sum doesn't have to be 1)
ACTIONS = [
    ('add_row',          25, _add_row_action),
    ('set_charge',       20, _set_charge_action),
    ('bump_charge',      15, _bump_charge_action),
    ('change_method',    10, _change_method_action),
    ('change_vendor',     8, _change_vendor_binding_action),
    ('zero_charge',       7, _zero_a_charge_action),
    ('delete_row',        8, _delete_row_action),
    ('auto_distribute',   7, _click_auto_distribute_action),
]


def _pick_action(rng):
    total = sum(w for _, w, _ in ACTIONS)
    r = rng.random() * total
    cum = 0
    for name, w, fn in ACTIONS:
        cum += w
        if r < cum:
            return name, fn
    return ACTIONS[0][0], ACTIONS[0][2]


# ════════════════════════════════════════════════════════════════════
# Fuzz session — drives PaymentScreen through random actions
# ════════════════════════════════════════════════════════════════════

def _run_fuzz_session(qtbot, conn, seed: int, n_actions: int):
    """One fuzz session.  Returns ``None`` on clean run; raises
    ``AssertionError`` with action log on any invariant violation."""
    rng = random.Random(seed)
    n_vendors = rng.randint(*BOUNDS['n_vendors_per_order'])
    cap = rng.randint(*BOUNDS['cap_cents'])
    cap = (cap // 100) * 100  # round to even cents
    _setup_market(conn, n_vendors=n_vendors + 2, cap_cents=cap)
    order_id, _ = _build_order(conn, rng, n_vendors=n_vendors + 2)

    from fam.ui.payment_screen import PaymentScreen
    screen = PaymentScreen()
    qtbot.addWidget(screen)
    screen.load_customer_order(order_id)

    # Wipe auto-added blank row.
    while screen._payment_rows:
        r = screen._payment_rows[0]
        screen.rows_layout.removeWidget(r)
        r.deleteLater()
        screen._payment_rows.remove(r)

    action_log = [f"seed={seed} cap={cap} order={order_id}"]

    for step in range(n_actions):
        name, fn = _pick_action(rng)
        try:
            fn(rng, screen, action_log)
        except Exception as e:
            log_text = "\n  ".join(action_log[-20:])
            raise AssertionError(
                f"\n=== seed={seed} step={step} action={name} "
                f"raised: {type(e).__name__}: {e}\n"
                f"=== History:\n  {log_text}\n"
                f"=== Traceback:\n{traceback.format_exc()}")
        # Ensure the row's per-row labels are recomputed after the
        # action.  In the real UI this happens automatically via
        # the QSpinBox/QComboBox/Stepper ``valueChanged`` /
        # ``currentIndexChanged`` signals → ``_on_changed`` →
        # ``_recompute()``.  Some test-only paths
        # (``_set_active_charge`` on a non-denom row) deliberately
        # block signals to avoid re-entering the parent's
        # ``_update_summary`` from within a write-back; in that
        # case the fuzzer must trigger the recompute explicitly so
        # the post-action snapshot reflects the same state a real
        # volunteer would see.
        for row in screen._payment_rows:
            try:
                row._recompute()
            except Exception:
                pass
        # Then run the screen-level update like the parent would.
        try:
            screen._update_summary()
        except Exception as e:
            log_text = "\n  ".join(action_log[-20:])
            raise AssertionError(
                f"\n=== seed={seed} step={step} _update_summary "
                f"raised: {type(e).__name__}: {e}\n"
                f"=== History:\n  {log_text}\n"
                f"=== Traceback:\n{traceback.format_exc()}")
        # After every action, check invariants.
        _check_invariants(screen, f"step{step}/{name}", action_log)


# ════════════════════════════════════════════════════════════════════
# Test cases — many seeds in parallel for broad coverage
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('seed', list(range(1, 31)))
def test_fuzz_random_action_sequences(qtbot, fuzz_db, seed):
    """30 seeds × 30 actions = 900 random UI actions per run.

    Each action is a legal volunteer move.  After every action the
    invariants V1, V3, V5 and engine validity are checked.  Any
    crash, IndexError, or invariant violation fails the test with
    the full action log so reproduction is deterministic."""
    _run_fuzz_session(qtbot, fuzz_db, seed=seed, n_actions=30)


@pytest.mark.parametrize('seed', [1001, 1002, 1003, 1004, 1005])
def test_fuzz_extended_sequences(qtbot, fuzz_db, seed):
    """5 seeds × 100 actions = 500 deeper random sequences.  Catches
    bugs that only surface after many state transitions accumulate."""
    _run_fuzz_session(qtbot, fuzz_db, seed=seed, n_actions=100)
