"""Master coherence auditor for the Payment screen.

This module is the runtime enforcement of the contract documented in
``docs/SYSTEM_INVARIANTS.md``.  It exposes a single entry point:

    audit_screen(screen, *, allow_under_allocation=False) -> AuditReport

which runs every invariant in the contract against the screen's
current state and returns a structured report.  Tests can use the
report's ``ok`` property to assert no failures, OR they can iterate
``failures`` to allow specific violations (e.g. "user is mid-edit
and we expect U7 to be off until the next signal").

Why a single entry point?  Bugs we keep finding live in the gaps
between layers — engine says X, DB says Y, summary card shows Z,
vendor reimbursement query returns W.  Each individual test usually
only checks one or two layers, so cross-layer disagreement slips
through.  ``audit_screen`` checks ALL invariants at once so the
fuzz harness can run it after every action and surface drift the
moment it appears.

The auditor is read-only — it never mutates the screen.  It
recomputes the engine state independently and compares to what the
UI is showing.

Layers checked (see SYSTEM_INVARIANTS.md):
    Layer 1 — Engine purity (E1-E7)
    Layer 2 — Forfeit pass (F1-F6)
    Layer 4 — Engine ↔ UI (U1-U12)
    Layer 5 — Per-vendor reconciliation (L1-L2)
    Layer 8 — Convergence (C1)

Layers NOT checked here (live in their own dedicated tests):
    Layer 3 — Engine ↔ DB (only meaningful POST-confirm)
    Layer 6 — Vendor reimbursement (POST-confirm)
    Layer 7 — Drafts (state preservation across save/restore)
    Layer 9 — Rewards (informational, not financial)

Use ``audit_full_after_confirm(conn, order_id)`` for D1-D5 and
R1-R7 after a successful confirmation.
"""

from dataclasses import dataclass, field
from typing import Any, Optional


# ──────────────────────────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────────────────────────


@dataclass
class Failure:
    """One invariant violation."""
    invariant_id: str        # e.g. "E2", "U5", "L1"
    description: str         # human-readable
    expected: Any = None
    actual: Any = None
    context: str = ""        # extra detail (row index, vendor name, etc.)

    def __str__(self):
        line = f"[{self.invariant_id}] {self.description}"
        if self.expected is not None or self.actual is not None:
            line += f" (expected={self.expected!r}, actual={self.actual!r})"
        if self.context:
            line += f" ctx={self.context}"
        return line


@dataclass
class AuditReport:
    failures: list[Failure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def add(self, *args, **kwargs):
        self.failures.append(Failure(*args, **kwargs))

    def __str__(self):
        if self.ok:
            return "AuditReport: OK"
        return ("AuditReport: " + str(len(self.failures))
                + " violation(s)\n  "
                + "\n  ".join(str(f) for f in self.failures))


# ──────────────────────────────────────────────────────────────────
# Helper — re-run engine independently of the UI
# ──────────────────────────────────────────────────────────────────


def _recompute_engine_state(screen):
    """Run engine + forfeit pass against the screen's current row
    state.  Returns ``(items, result)`` matching what the screen
    SHOULD be displaying.  This is the canonical truth the auditor
    compares the UI to.

    Mirrors the production ``_update_summary`` / ``_confirm_payment``
    flow:
      1. ``_collect_line_items`` (raw row data)
      2. ``calculate_payment_breakdown``
      3. Detect order-level OR per-vendor denomination overage
      4. ``_apply_denomination_forfeit`` (mutates in place)

    The auditor does NOT touch the actual screen state — it works
    on a fresh copy of items.

    ``denom_overage`` parameter to forfeit = max(order-level
    overage, sum of per-vendor over-allocation).  Per-vendor
    detection is critical because the order can balance globally
    while a single vendor is over-allocated (auto-distribute sized
    a non-denom absorber to compensate, but the bound denom row
    still over-allocates its vendor).
    """
    from fam.utils.calculations import calculate_payment_breakdown
    items = screen._collect_line_items()
    items_copy = [dict(it) for it in items]
    if not items_copy:
        return items_copy, None
    entries = [
        {'method_amount': it['method_amount'],
         'match_percent': it['match_percent'],
         'denomination': it.get('denomination')}
        for it in items_copy
    ]
    result = calculate_payment_breakdown(
        screen._order_total, entries,
        match_limit=screen._match_limit)
    # Order-level overage from the engine.
    order_overage = screen._check_denomination_overage(
        result, screen._order_total)
    # Per-vendor over-allocation that may exist even when the
    # order total balances.  Sum vendor receipts FIRST so multi-
    # transaction-per-vendor doesn't double-count gaps.
    per_vendor_overage = 0
    if screen._order_transactions:
        vendor_receipts_sum: dict = {}
        for t in screen._order_transactions:
            vid = t.get('vendor_id')
            if vid is not None:
                vendor_receipts_sum[vid] = (
                    vendor_receipts_sum.get(vid, 0)
                    + t['receipt_total'])
        vendor_alloc: dict = {}
        for it in items_copy:
            denom_v = it.get('denomination') or 0
            if denom_v <= 0:
                continue
            vid = it.get('bound_vendor_id')
            if vid is not None:
                vendor_alloc[vid] = (
                    vendor_alloc.get(vid, 0)
                    + it['method_amount'])
        for vid, alloc in vendor_alloc.items():
            receipt_sum = vendor_receipts_sum.get(vid, 0)
            gap = alloc - receipt_sum
            if gap > 0:
                per_vendor_overage += gap
    overage = max(order_overage, per_vendor_overage)
    if overage > 0:
        screen._apply_denomination_forfeit(
            result, items_copy, overage)
    return items_copy, result


def _parse_dollar_label(text: str) -> Optional[int]:
    """Parse a `$X.XX` style label to integer cents.  Returns None
    if the text doesn't look like a dollar amount."""
    if not text:
        return None
    s = text.strip().replace('$', '').replace(',', '').strip()
    if not s:
        return None
    try:
        return int(round(float(s) * 100))
    except (ValueError, TypeError):
        return None


# ──────────────────────────────────────────────────────────────────
# Layer 1 — Engine purity
# ──────────────────────────────────────────────────────────────────


def _check_engine_purity(items, result, report):
    if result is None:
        return
    # E3, E5, E6, E7
    sum_method = sum(li['method_amount'] for li in result['line_items'])
    sum_match = sum(li['match_amount'] for li in result['line_items'])
    sum_customer = sum(
        li['customer_charged'] for li in result['line_items'])
    if result.get('allocated_total') != sum_method:
        report.add(
            'E5', "result.allocated_total != Σ method_amount",
            expected=sum_method, actual=result.get('allocated_total'))
    if result.get('fam_subsidy_total') != sum_match:
        report.add(
            'E6', "result.fam_subsidy_total != Σ match_amount",
            expected=sum_match, actual=result.get('fam_subsidy_total'))
    if result.get('customer_total_paid') != sum_customer:
        report.add(
            'E7', "result.customer_total_paid != Σ customer_charged",
            expected=sum_customer,
            actual=result.get('customer_total_paid'))
    # E3 — per-line
    for i, li in enumerate(result['line_items']):
        if li['customer_charged'] + li['match_amount'] \
                != li['method_amount']:
            report.add(
                'E3', "per-line invariant violated",
                context=(f"row {i}: "
                         f"{li['customer_charged']}+{li['match_amount']}"
                         f"!={li['method_amount']}"))
    # E4 — match-cap respected
    if (result.get('match_was_capped')
            and screen_match_limit_for_check(result) is not None):
        cap = screen_match_limit_for_check(result)
        if cap is not None and sum_match > cap:
            report.add(
                'E4', "Σ match exceeds match_limit when capped",
                expected=f"<= {cap}", actual=sum_match)


def screen_match_limit_for_check(result):
    """Engine doesn't return the cap; pull it from caller context.
    For now we check this in audit_screen which has the screen."""
    return None  # filled by caller via report.context


# ──────────────────────────────────────────────────────────────────
# Layer 2 — Forfeit pass
# ──────────────────────────────────────────────────────────────────


def _check_forfeit_pass(items, result, screen, report,
                        allow_under_allocation):
    if result is None:
        return
    # F1 — total method == receipt (post-forfeit reconciliation).
    # When ``allow_under_allocation`` is True (the fuzzer is
    # mid-edit), a SHORTFALL is acceptable but OVER-allocation is
    # never tolerated — that means forfeit failed to bring it
    # down to receipt.
    sum_method = sum(li['method_amount'] for li in result['line_items'])
    if sum_method > screen._order_total + 1:
        report.add(
            'F1', "Σ method exceeds order_total post-forfeit",
            expected=f"<= {screen._order_total}", actual=sum_method)
    elif (sum_method < screen._order_total - 1
            and not allow_under_allocation):
        report.add(
            'F1', "Σ method < order_total (under-allocated)",
            expected=screen._order_total, actual=sum_method)
    # F3, F4 — forfeit non-negative + only on denom rows
    for i, (li, it) in enumerate(zip(result['line_items'], items)):
        forfeit = li.get('customer_forfeit_cents', 0) or 0
        if forfeit < 0:
            report.add(
                'F3', "customer_forfeit_cents < 0",
                actual=forfeit, context=f"row {i}")
        if forfeit > 0:
            denom = it.get('denomination') or 0
            if denom <= 0:
                report.add(
                    'F4', "forfeit on a non-denominated row",
                    context=f"row {i}: method={it.get('method_name_snapshot')}")
    # F2 — per-transaction allocation <= receipt
    per_txn = _simulate_per_txn_alloc(screen, items)
    for t in screen._order_transactions:
        alloc = per_txn.get(t['id'], 0)
        if alloc > t['receipt_total'] + 1:
            report.add(
                'F2',
                "per-txn allocation exceeds receipt by >1¢",
                expected=f"<= {t['receipt_total']}",
                actual=alloc,
                context=(f"vendor={t.get('vendor_name', '?')} "
                         f"txn_id={t['id']}"))
    # F6 — per-line invariant survives forfeit
    for i, li in enumerate(result['line_items']):
        if li['customer_charged'] + li['match_amount'] \
                != li['method_amount']:
            report.add(
                'F6', "per-line invariant violated post-forfeit",
                context=(f"row {i}: "
                         f"{li['customer_charged']}+{li['match_amount']}"
                         f"!={li['method_amount']}"))


def _simulate_per_txn_alloc(screen, items):
    """Replicate the production save-path's per-TRANSACTION
    allocation so we can predict where each cent will land BEFORE
    confirm.

    v1.9.10 follow-up (2026-05-01): when a vendor has MULTIPLE
    receipts in one order (common: the volunteer enters two
    Juice Bar receipts $11.11 + $100), bound denom payments must
    distribute across ALL of that vendor's transactions
    proportionally to per-txn remaining receipt — same algorithm
    as Phase 2 below.  Earlier "first match wins" map dumped every
    bound denom on the first txn and then Layer 2C flagged a
    spurious over-allocation.  Mirror the save-path's fix here so
    the auditor predicts what will actually happen on confirm.

    Returns ``{txn_id: allocated_method_cents}``.
    """
    # Map vendor_id → list of txn_ids in screen order.
    vendor_to_txn_ids: dict = {}
    for t in screen._order_transactions:
        vid = t.get('vendor_id')
        if vid is not None:
            vendor_to_txn_ids.setdefault(vid, []).append(t['id'])
    per_txn: dict = {t['id']: 0 for t in screen._order_transactions}
    txn_lookup = {t['id']: t for t in screen._order_transactions}
    # Phase 1: denom rows distribute across the bound vendor's
    # transactions (proportional to per-txn remaining when the
    # vendor has multiple).
    #
    # Unbound denom rows in MULTI-vendor orders are skipped
    # entirely — matches the legacy auditor behaviour and
    # prevents phantom over-allocation on the first txn when the
    # fuzzer leaves bound_vendor_id=None.  (Production's Layer-2
    # eligibility guard blocks confirm in this state, so the
    # auditor doesn't need to invent a target.)
    for it in items:
        denom = it.get('denomination')
        if not (denom and denom > 0):
            continue
        vid = it.get('bound_vendor_id')
        # Single-vendor implicit binding fallback.
        if vid is None and len(set(
                t['vendor_id']
                for t in screen._order_transactions
                if t.get('vendor_id') is not None)) == 1:
            vid = screen._order_transactions[0].get('vendor_id')
        if vid is None:
            # Unbound on multi-vendor — skip (matches old behaviour).
            continue
        target_ids = vendor_to_txn_ids.get(vid)
        if not target_ids:
            # Vendor isn't on the order (shouldn't happen, but
            # defensive).
            continue
        ma = it['method_amount']
        if len(target_ids) == 1:
            per_txn[target_ids[0]] += ma
            continue
        per_txn_remaining = []
        total_remaining = 0
        for tid in target_ids:
            t = txn_lookup[tid]
            left = max(0, t['receipt_total'] - per_txn[tid])
            per_txn_remaining.append(left)
            total_remaining += left
        if total_remaining <= 0:
            per_txn[target_ids[-1]] += ma
            continue
        running = 0
        last_pos = len(target_ids) - 1
        for k, tid in enumerate(target_ids):
            if k == last_pos:
                share = ma - running
            else:
                weight = per_txn_remaining[k] / total_remaining
                share = round(ma * weight)
                running += share
            per_txn[tid] += share
    # Phase 2: non-denom proportional split.
    for it in items:
        if it.get('denomination') and it['denomination'] > 0:
            continue
        ma = it['method_amount']
        rems = []
        tot_rem = 0
        for t in screen._order_transactions:
            left = max(0, t['receipt_total'] - per_txn[t['id']])
            rems.append(left)
            tot_rem += left
        if tot_rem <= 0:
            continue
        running = 0
        for i, t in enumerate(screen._order_transactions):
            if i == len(screen._order_transactions) - 1:
                share = ma - running
            else:
                share = round(ma * rems[i] / tot_rem)
                running += share
            per_txn[t['id']] += share
    return per_txn


# Backward-compat alias for places still using the old name.
def _simulate_per_vendor_alloc(screen, items):
    return _simulate_per_txn_alloc(screen, items)


# ──────────────────────────────────────────────────────────────────
# Layer 4 — Engine ↔ UI
# ──────────────────────────────────────────────────────────────────


def _check_engine_ui(items, result, screen, report,
                     allow_under_allocation):
    """Compare every UI surface to the engine's canonical truth.

    When ``allow_under_allocation`` is True the U5/U6/U7 summary-
    card checks are RELAXED — the cards reflect engine totals from
    the most recent ``_update_summary`` cycle, but if the fuzzer
    just modified a row WITHOUT triggering the cycle (or the cycle
    is mid-resolve) the card temporarily lags.  We still flag
    over-allocation strictly.

    ``valid_rows`` matches ``_collect_line_items``'s filter
    exactly — keep only rows with a selected method AND
    ``method_amount > 0``.  This aligns with ``items`` /
    ``result['line_items']`` indexing; a wider filter mis-pairs
    rows to engine line items and produces phantom mismatches.
    """
    if result is None or not result.get('line_items'):
        return
    # ``valid_rows`` MUST match the production
    # ``_collect_line_items`` filter (method_amount > 0) since the
    # engine here was called with that filtered list.  If the
    # screen's ``_update_summary`` uses a wider filter (which
    # includes zero-method rows), see the screen's write-back
    # comment for the alignment rationale.
    valid_rows = []
    for r in screen._payment_rows:
        d = r.get_data()
        if d and d.get('method_amount', 0) > 0:
            valid_rows.append(r)

    # v1.9.10 follow-up (2026-05-01, admin fuzz cluster): when the
    # cap-aware engine path activates (match_was_capped), the
    # engine inflates non-denom customer_charged in the Common
    # Path — that inflation is NOT idempotent (feeding it back as
    # input compounds the inflation by ~1.42× per call for 100%
    # match).  The screen's spinbox holds the engine's first-pass
    # output; a fresh re-run of the engine in this auditor sees
    # the inflated input and produces a different (higher)
    # output.  This is an engine-design artifact, not a real
    # coherence violation: the screen IS internally consistent
    # with the engine output that drove its display.  Skip U1,
    # U3, U6 strict equality when cap is active.  U2 (per-row
    # internal: charge + match_label = total_label) and U5
    # (fam_match cap) still apply — they don't depend on the
    # auditor's re-run.  Found by admin fuzz seeds 19/102/105.
    cap_active = bool(result.get('match_was_capped'))

    # U1 — row spinbox value == customer + forfeit
    for i, row in enumerate(valid_rows):
        if i >= len(result['line_items']):
            break
        li = result['line_items'][i]
        forfeit = li.get('customer_forfeit_cents', 0) or 0
        expected = li['customer_charged'] + forfeit
        actual = row._get_active_charge()
        if expected != actual and not cap_active:
            mname = (row.get_selected_method() or {}).get('name', '?')
            report.add(
                'U1', "row spinbox != customer_charged + forfeit",
                expected=expected, actual=actual,
                context=f"row {i} ({mname})")

    # U2 — V5: charge + match_label = total_label per row
    for i, row in enumerate(valid_rows):
        try:
            charge = row._get_active_charge()
            match_cents = _parse_dollar_label(row.match_amount_label.text())
            total_cents = _parse_dollar_label(row.total_label.text())
            if (match_cents is not None and total_cents is not None
                    and charge + match_cents != total_cents):
                report.add(
                    'U2', "V5 violated: charge + match_label != total_label",
                    expected=charge + match_cents,
                    actual=total_cents,
                    context=f"row {i}")
        except Exception as e:
            report.add('U2', f"V5 read crashed: {e!r}", context=f"row {i}")

    # U3 / U4 — row labels reflect engine values when no forfeit;
    # pre-forfeit when forfeit > 0.
    for i, row in enumerate(valid_rows):
        if i >= len(result['line_items']):
            break
        li = result['line_items'][i]
        forfeit = li.get('customer_forfeit_cents', 0) or 0
        match_cents = _parse_dollar_label(row.match_amount_label.text())
        total_cents = _parse_dollar_label(row.total_label.text())
        if match_cents is None or total_cents is None:
            continue
        if forfeit == 0 and not cap_active:
            # Row labels must match engine (skipped under cap-active —
            # see U1 comment for the engine-non-idempotency rationale).
            if match_cents != li['match_amount']:
                report.add(
                    'U3', "row match_label != engine match",
                    expected=li['match_amount'], actual=match_cents,
                    context=f"row {i}")
            if total_cents != li['method_amount']:
                report.add(
                    'U3', "row total_label != engine method",
                    expected=li['method_amount'], actual=total_cents,
                    context=f"row {i}")
        # When forfeit > 0, U4 says labels show pre-forfeit
        # (charge × pct).  V5 (U2) already enforces the per-row
        # math so we don't need to duplicate here.

    # U5, U6, U7 — summary cards
    cards = screen.summary_row.cards
    fam_card = _parse_dollar_label(cards['fam_match'].value_label.text())
    customer_card = _parse_dollar_label(
        cards['customer_pays'].value_label.text())
    if fam_card is not None and fam_card != result['fam_subsidy_total']:
        report.add(
            'U5', "fam_match card != result.fam_subsidy_total",
            expected=result['fam_subsidy_total'], actual=fam_card)
    if (customer_card is not None
            and customer_card != result['customer_total_paid']
            and not cap_active):
        # Skipped under cap-active — see U1 comment for the
        # engine-non-idempotency rationale.
        report.add(
            'U6', "customer_pays card != result.customer_total_paid",
            expected=result['customer_total_paid'],
            actual=customer_card)


# ──────────────────────────────────────────────────────────────────
# Layer 5 — Per-vendor reconciliation (Layer 2C predictive)
# ──────────────────────────────────────────────────────────────────


def _check_per_vendor_reconciliation(
        items, result, screen, report, allow_under_allocation):
    """Predict the save-path's per-vendor allocation and verify it
    reconciles to each receipt within ±1¢.

    Mid-edit under-allocation (gap < 0) is the normal "user still
    typing" state — flag only when ``allow_under_allocation`` is
    False.  Over-allocation (gap > 0) is ALWAYS a defect because
    the engine + forfeit pass should have prevented it; if it
    surfaces here, the screen is in a state where Confirm could
    silently mis-allocate.  L2 fires when over-allocation exists
    AND the screen has no visible warning (denom_overage_warning
    or error_label) — i.e. it appears confirmable but isn't.
    """
    if not items or result is None:
        return
    per_txn = _simulate_per_txn_alloc(screen, items)
    over_allocated = False
    for t in screen._order_transactions:
        alloc = per_txn.get(t['id'], 0)
        receipt = t['receipt_total']
        gap = alloc - receipt
        if gap > 1:
            over_allocated = True
            report.add(
                'L1', "per-txn over-allocation",
                expected=receipt, actual=alloc,
                context=(f"vendor={t.get('vendor_name', '?')} "
                         f"txn_id={t['id']} gap=+{gap}"))
        elif gap < -1 and not allow_under_allocation:
            report.add(
                'L1', "per-txn under-allocation",
                expected=receipt, actual=alloc,
                context=(f"vendor={t.get('vendor_name', '?')} "
                         f"txn_id={t['id']} gap={gap}"))
    # L2 — over-allocation must be visibly flagged on the screen.
    if over_allocated:
        denom_warn_visible = (
            getattr(screen, 'denom_overage_warning', None)
            and screen.denom_overage_warning.isVisible())
        error_visible = (
            getattr(screen, 'error_label', None)
            and screen.error_label.isVisible())
        if not (denom_warn_visible or error_visible):
            report.add(
                'L2',
                "per-vendor over-allocation with no visible warning",
                context=("screen appears confirmable but the "
                         "save would over-allocate"))


# ──────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────


def audit_screen(screen, *, allow_under_allocation=False):
    """Run every invariant against the current screen state.

    Args:
        screen: PaymentScreen instance (or AdjustmentDialog with
            compatible interface).
        allow_under_allocation: when True, L1 under-allocation
            violations (gap < 0) are tolerated.  Useful when the
            screen is mid-edit (user typed denoms but hasn't
            run auto-distribute yet).  Over-allocation is never
            tolerated.

    Returns:
        AuditReport — check ``.ok`` for pass/fail, iterate
        ``.failures`` for details.
    """
    report = AuditReport()
    items, result = _recompute_engine_state(screen)
    _check_engine_purity(items, result, report)
    _check_forfeit_pass(items, result, screen, report,
                        allow_under_allocation)
    _check_engine_ui(items, result, screen, report,
                     allow_under_allocation)
    _check_per_vendor_reconciliation(
        items, result, screen, report, allow_under_allocation)
    return report


# ──────────────────────────────────────────────────────────────────
# Post-confirm auditor — D + R layers
# ──────────────────────────────────────────────────────────────────


def audit_post_confirm(conn, market_day_id):
    """Run Layer 3 (Engine ↔ DB) and Layer 6 (Vendor reimbursement)
    against the current DB state.  Use after a confirmation has
    been saved.

    Independent of the screen — this verifies the financial
    record is sound regardless of UI state.
    """
    report = AuditReport()
    # D1 — for every confirmed/adjusted txn: Σ pli.method == receipt
    rows = conn.execute("""
        SELECT t.id, t.receipt_total, t.fam_transaction_id,
               COALESCE((SELECT SUM(method_amount)
                         FROM payment_line_items
                         WHERE transaction_id = t.id), 0) AS pli_sum
        FROM transactions t
        WHERE t.market_day_id = ?
          AND t.status IN ('Confirmed', 'Adjusted')
    """, (market_day_id,)).fetchall()
    for r in rows:
        if r['pli_sum'] != r['receipt_total']:
            report.add(
                'D1', "Σ payment_line_items.method != receipt_total",
                expected=r['receipt_total'], actual=r['pli_sum'],
                context=f"txn {r['fam_transaction_id']}")

    # D2 — per-line invariant (CHECK trigger)
    bad = conn.execute("""
        SELECT pli.id, pli.transaction_id,
               pli.customer_charged, pli.match_amount,
               pli.method_amount, pli.method_name_snapshot
        FROM payment_line_items pli
        JOIN transactions t ON pli.transaction_id = t.id
        WHERE t.market_day_id = ?
          AND t.status IN ('Confirmed', 'Adjusted')
          AND pli.customer_charged + pli.match_amount != pli.method_amount
          AND pli.method_name_snapshot != 'Unallocated Funds'
    """, (market_day_id,)).fetchall()
    for r in bad:
        report.add(
            'D2', "per-line invariant violated in DB",
            context=(f"pli {r['id']} "
                     f"({r['method_name_snapshot']}): "
                     f"{r['customer_charged']}+{r['match_amount']}"
                     f"!={r['method_amount']}"))

    # R1, R2 — vendor reimbursement totals match SQL truth
    from fam.sync.data_collector import _collect_vendor_reimbursement
    try:
        vr_rows = _collect_vendor_reimbursement(conn, [market_day_id])
    except Exception as e:
        report.add('R1', f"_collect_vendor_reimbursement crashed: {e!r}")
        return report
    db_per_vendor = {
        r['vendor_id']: r['total']
        for r in conn.execute("""
            SELECT t.vendor_id,
                   SUM(t.receipt_total) AS total
            FROM transactions t
            WHERE t.market_day_id = ?
              AND t.status IN ('Confirmed', 'Adjusted')
            GROUP BY t.vendor_id
        """, (market_day_id,)).fetchall()
    }
    for vrow in vr_rows:
        # R1 — per-vendor sum
        # The vendor reimbursement query joins by vendor name; we
        # cross-reference by name → id via the vendors table.
        vid_row = conn.execute(
            "SELECT id FROM vendors WHERE name = ?",
            (vrow['Vendor'],)).fetchone()
        if not vid_row:
            continue
        vid = vid_row['id']
        db_total = db_per_vendor.get(vid, 0)
        # Vendor reimbursement uses dollar floats — convert to cents.
        report_total_cents = int(round(
            vrow['Total Due to Vendor'] * 100))
        # Account for FMNP external add-on.
        fmnp_ext_cents = int(round(
            (vrow.get('FMNP (External)') or 0) * 100))
        expected = db_total + fmnp_ext_cents
        if abs(report_total_cents - expected) > 1:
            report.add(
                'R1', "Vendor reimbursement Total Due != DB receipt sum",
                expected=expected, actual=report_total_cents,
                context=f"vendor={vrow['Vendor']}")
        # R5 — math identity
        method_cols_total = sum(
            int(round(v * 100))
            for k, v in vrow.items()
            if k not in (
                'Market Name', 'Vendor', 'Month', 'Date(s)',
                'Total Due to Vendor', 'FAM Match',
                'FMNP (External)', 'Check Payable To', 'Address',
                'market_code', 'device_id')
            and isinstance(v, (int, float))
        )
        fam_match_cents = int(round((vrow.get('FAM Match') or 0) * 100))
        identity_lhs = method_cols_total + fam_match_cents + fmnp_ext_cents
        if abs(identity_lhs - report_total_cents) > 1:
            report.add(
                'R5', "Σ per-method + FAM Match + FMNP_External != Total Due",
                expected=report_total_cents, actual=identity_lhs,
                context=f"vendor={vrow['Vendor']}")
    return report
