"""Financial calculation logic for FAM transactions.

All monetary values are integer cents (e.g. $89.99 = 8999).
Match percentages remain as floats (e.g. 100.0 for 1:1 match).
"""
import math
from fam.utils.money import format_dollars


def charge_to_method_amount(charge: int, match_percent: float) -> int:
    """Convert a customer charge (cents) to total allocation (method_amount).

    The charge is what the customer pays for this payment method.
    method_amount = charge + FAM match = charge × (1 + match_percent / 100).
    """
    return round(charge * (1.0 + match_percent / 100.0))


def method_amount_to_charge(method_amount: int, match_percent: float) -> int:
    """Convert total allocation (method_amount) back to the customer charge.

    Inverse of charge_to_method_amount.
    charge = method_amount / (1 + match_percent / 100).
    """
    divisor = 1.0 + match_percent / 100.0
    return round(method_amount / divisor) if divisor > 0 else method_amount


def smart_auto_distribute(order_total: int, rows: list) -> list:
    """Distribute an order total across multiple payment rows intelligently.

    Two-pass algorithm:
      Pass 1 — Seed each denominated row with 1 unit (minimum valid amount).
      Pass 2 — Fill remaining balance into rows by sort_order, denominated
               rows get additional whole units, last non-denominated row
               absorbs the exact remainder.

    Args:
        order_total: The receipt/order total in integer cents.
        rows: List of dicts, each with:
            - index (int): original row position (returned unchanged)
            - match_pct (float): match percent (0-999)
            - denomination (int or None): denomination value in cents, or None
            - sort_order (int): lower = higher display priority
            - current_charge (int): user-entered charge in cents (>0 means locked)

    Returns:
        List of dicts with {index, charge} for each row that should be updated.
        charge is in integer cents.  Only includes rows whose charge changed
        (auto rows that received an allocation). Locked rows are never modified.
    """
    if order_total <= 0 or not rows:
        return []

    # Partition into locked (user entered) vs auto (empty, to be filled).
    #
    # v2.0.7+ user-cap (audit 2026-05-07): rows marked
    # user_capped=True go to locked even at charge=0.  Without
    # this, a default-Locked new row (added when an existing
    # Active overflow target was present) would silently absorb
    # the remainder, contradicting the volunteer's "this row is
    # not the overflow target" intent.
    locked = []
    auto = []
    for r in rows:
        if r['current_charge'] > 0 or r.get('user_capped'):
            locked.append(r)
        else:
            auto.append(dict(r))  # copy so we can mutate

    if not auto:
        return []

    # Sort auto rows by sort_order ASC (lower = higher priority)
    auto.sort(key=lambda r: r['sort_order'])

    # Calculate remaining after locked rows
    locked_total = sum(
        charge_to_method_amount(r['current_charge'], r['match_pct'])
        for r in locked
    )
    remaining = order_total - locked_total

    if remaining <= 0:
        return []

    # Initialize charges for auto rows
    for r in auto:
        r['_charge'] = 0

    # ── Pass 1: Seed each denominated row with 1 unit ──
    for r in auto:
        denom = r.get('denomination')
        if not denom or denom <= 0:
            continue
        unit_cost = charge_to_method_amount(denom, r['match_pct'])
        if remaining >= unit_cost:
            r['_charge'] = denom
            remaining -= unit_cost

    # ── Pass 2: Fill up with remaining balance ──
    # Identify the best non-denominated absorber: the auto row with the
    # highest match_pct absorbs the remainder.  This ensures the FAM
    # match covers rounding pennies (via penny reconciliation) rather
    # than a 0% row making the customer pay them.
    non_denom_auto = [
        r for r in auto
        if not r.get('denomination') or r.get('denomination', 0) <= 0
    ]
    best_absorber = None
    if non_denom_auto:
        # Highest match_pct wins; ties broken by lowest sort_order (highest
        # priority), then lowest index (first added).
        best_absorber = max(
            non_denom_auto,
            key=lambda r: (r['match_pct'], -r['sort_order'], -r['index']),
        )

    for r in auto:
        if remaining <= 0:
            break

        denom = r.get('denomination')
        if denom and denom > 0:
            # Add whole denomination units
            max_charge = remaining / (1.0 + r['match_pct'] / 100.0)
            additional_units = int(max_charge / denom)
            if additional_units > 0:
                additional_charge = additional_units * denom
                additional_cost = charge_to_method_amount(
                    additional_charge, r['match_pct']
                )
                r['_charge'] += additional_charge
                remaining -= additional_cost
        else:
            # Non-denominated: only the best-match row absorbs the
            # remainder.  Other non-denom rows stay at 0.
            if r is not best_absorber:
                continue

            # Always floor the charge so the customer never pays a
            # rounding penny.  Any ≤1-cent gap left over is absorbed
            # by FAM match during penny reconciliation in
            # calculate_payment_breakdown().
            divisor = 1.0 + r['match_pct'] / 100.0
            raw = remaining / divisor
            charge = int(raw)          # floor to nearest cent
            if charge > 0:
                r['_charge'] = charge
                remaining -= charge_to_method_amount(charge, r['match_pct'])

    # ── Pass 3: Denomination forfeit ──────────────────────────────
    # If all auto rows are denominated and there's still remaining,
    # the gap can't be filled with whole units.  Allow +1 unit on the
    # best-fit denominated row — the customer forfeits a small amount
    # of FAM match (the overage is capped at that method's denomination).
    if remaining > 1:  # more than 1 cent remaining
        has_non_denom_auto = any(
            not r.get('denomination') or r.get('denomination', 0) <= 0
            for r in auto
        )
        if not has_non_denom_auto:
            for r in reversed(auto):
                denom = r.get('denomination')
                if denom and denom > 0 and r['_charge'] > 0:
                    unit_cost = charge_to_method_amount(denom, r['match_pct'])
                    overage = unit_cost - remaining
                    # Only forfeit when the overage is small — the charge
                    # portion must exceed the wasted match (overage < denom).
                    if 0 < overage < denom:
                        r['_charge'] += denom
                        remaining -= unit_cost
                    break

    # Build result — only rows that got an allocation
    result = []
    for r in auto:
        if r['_charge'] > 0:
            result.append({'index': r['index'], 'charge': r['_charge']})

    return result


def calculate_payment_breakdown(receipt_total: int, payment_entries: list,
                                match_limit: int | None = None) -> dict:
    """
    Calculate the full payment breakdown for a transaction.

    All monetary values are integer cents.

    Args:
        receipt_total: The gross amount on the paper receipt (cents).
        payment_entries: List of dicts, each with:
            - method_amount (int): amount allocated in cents
            - match_percent (float): 0-999, e.g. 100 = 1:1 match, 200 = 2:1
            - denomination (int, optional): denomination in cents.  When
              present and > 0, the row is treated as **denominated** —
              its ``customer_charged`` is FIXED by the physical
              instruments handed over (N × denomination) and will not
              be inflated by cap-aware deficit redistribution.  The
              cap deficit is absorbed entirely by non-denominated
              rows in that case.  Omit (or set None/0) for non-
              denominated rows whose customer can flex.
        match_limit: Optional per-customer daily match cap in cents.

    Returns:
        Dict with:
            - line_items: list of computed line items (all cents)
            - customer_total_paid: sum of customer_charged (cents)
            - fam_subsidy_total: sum of match_amount after cap (cents)
            - is_valid: bool (True if totals reconcile within 1 cent)
            - allocated_total: sum of method_amount (cents)
            - allocation_remaining: receipt_total - allocated_total (cents)
            - errors: list of error message strings
            - match_was_capped: bool (True if the match limit was applied)
            - uncapped_fam_subsidy_total: original match total before cap (cents)
    """
    errors = []

    if receipt_total < 0:
        errors.append("Receipt total cannot be negative.")

    if not payment_entries:
        errors.append("At least one payment method is required.")
        return {
            'line_items': [],
            'customer_total_paid': 0,
            'fam_subsidy_total': 0,
            'is_valid': False,
            'allocated_total': 0,
            'allocation_remaining': receipt_total,
            'errors': errors,
            'match_was_capped': False,
            'uncapped_fam_subsidy_total': 0,
        }

    line_items = []
    for entry in payment_entries:
        method_amount = entry.get('method_amount', 0)
        match_percent = entry.get('match_percent', 0.0)
        denomination = entry.get('denomination') or 0
        is_denom = denomination > 0

        if method_amount < 0:
            errors.append(
                f"Payment amount cannot be negative "
                f"(got {format_dollars(method_amount)})."
            )
        if match_percent < 0:
            errors.append(f"Match percent cannot be negative (got {match_percent}%).")

        match_amount = round(method_amount * (match_percent / (100.0 + match_percent)))
        customer_charged = method_amount - match_amount

        line_items.append({
            'method_amount': method_amount,
            'match_percent': match_percent,
            'match_amount': match_amount,
            'customer_charged': customer_charged,
            '_is_denom': is_denom,
            # v2.0.7+ user-cap (user-reported 2026-05-07): when
            # True, the engine MUST preserve customer_charged for
            # this row across all cap-aware adjustments below.
            # The volunteer typed this value as a hard cap (e.g.
            # "customer only has $125 on their EBT card"); the
            # engine respects it and surfaces any cap-shrinkage
            # gap as allocation_remaining > 0 instead of silently
            # bumping customer_charged.  When False (default), the
            # existing cap-aware behaviour applies — customer_
            # charged inflates to absorb match-cap shrinkage so
            # allocated_total == receipt_total.
            'user_capped': bool(entry.get('user_capped', False)),
        })

    # ── Apply match-limit cap ────────────────────────────────────
    uncapped_total = sum(li['match_amount'] for li in line_items)
    match_was_capped = False

    if match_limit is not None and uncapped_total > match_limit >= 0:
        match_was_capped = True

        # v1.9.10 onsite-finding fix: denominated rows have FIXED
        # customer_charged (= physical units × denomination).  The
        # naive proportional cap (``match_amount = uncapped × cap_ratio``
        # for every row, then ``customer = method - match``) inflates
        # customer_charged on denom rows when cap kicks in — but the
        # customer didn't hand over more physical units, so the
        # spinbox can't mirror that value, Layer 2A then blocks the
        # confirm with a "row mismatch" error, and the volunteer is
        # stuck.  Fix: when total denom uncapped match ≤ cap,
        # absorb the cap deficit ENTIRELY on non-denom rows, leaving
        # denom rows' customer_charged intact.  Falls back to the
        # legacy proportional reduction only when denom matches alone
        # already exceed the cap (rare; volunteer must reduce a
        # denom row).
        denom_uncapped = sum(li['match_amount']
                              for li in line_items if li['_is_denom'])
        non_denom_uncapped = uncapped_total - denom_uncapped

        if denom_uncapped <= match_limit and non_denom_uncapped > 0:
            # Common path: cap deficit fits within non-denom flex.
            available_for_non_denom = match_limit - denom_uncapped
            non_denom_cap_ratio = (
                available_for_non_denom / non_denom_uncapped)
            for li in line_items:
                if li['_is_denom']:
                    # Denom: customer_charged FIXED, match unchanged
                    # at uncapped.  Method may need to reduce later
                    # via denomination-forfeit if it over-allocates
                    # the bound vendor; that's not the cap's job.
                    pass
                else:
                    li['match_amount'] = round(
                        li['match_amount'] * non_denom_cap_ratio)
                    if li.get('user_capped'):
                        # v2.0.7+ user-cap (user-reported 2026-05-07):
                        # the volunteer typed this row's customer_
                        # charged as a hard cap.  Adjust method down
                        # instead of inflating customer.  The match-
                        # cap shrinkage surfaces as
                        # allocation_remaining > 0 (Confirm blocked
                        # by is_valid=False until the volunteer adds
                        # another row to absorb the gap).
                        li['method_amount'] = (
                            li['customer_charged']
                            + li['match_amount'])
                    else:
                        # Existing behaviour for non-capped rows:
                        # method_amount fixed (= the row's input),
                        # customer inflates to absorb the match-cap
                        # shrinkage so allocated_total ==
                        # receipt_total.  Used by Auto-Distribute
                        # which intentionally lets the engine balance
                        # the math.
                        li['customer_charged'] = (
                            li['method_amount'] - li['match_amount'])
        else:
            # Fallback: denom uncapped match alone meets/exceeds cap.
            # v1.9.10 onsite-finding fix: previously this did a
            # naive proportional reduction across ALL rows, which
            # inflated ``customer_charged`` on denom rows (because
            # the formula computed customer = method - reduced_match
            # without recognizing that denom customer is FIXED at
            # ``unit_count × denomination``).  Layer 2A then blocked
            # confirm with "row mismatch" — the user's screen
            # showed FB Bucks $6 but engine wanted $10.31, with no
            # way forward (Auto-Distribute didn't fix it).
            #
            # Correct behaviour: snap each denom row's customer
            # back to its FIXED value, reduce match (and method) by
            # the cap ratio, then inflate non-denom rows' method to
            # cover the receipt-balance gap.  Non-denom rows absorb
            # the residual cap budget (likely near zero).  After
            # forfeit + cap-aware give-back run downstream, the
            # final state has correct denom-multiple customers and
            # full cap utilization.
            if denom_uncapped > 0:
                denom_cap_ratio = min(1.0, match_limit / denom_uncapped)
            else:
                denom_cap_ratio = 0.0

            denom_method_reduction = 0
            denom_new_match_total = 0
            for li in line_items:
                if li['_is_denom']:
                    # Recover the original (fixed) customer charge
                    # before any cap math.
                    fixed_customer = (
                        li['method_amount'] - li['match_amount'])
                    new_match = round(
                        li['match_amount'] * denom_cap_ratio)
                    old_method = li['method_amount']
                    li['match_amount'] = new_match
                    li['method_amount'] = fixed_customer + new_match
                    # customer_charged stays at fixed_customer (=
                    # unit_count × denomination).  Set explicitly so
                    # downstream callers don't have to re-derive.
                    li['customer_charged'] = fixed_customer
                    denom_method_reduction += (
                        old_method - li['method_amount'])
                    denom_new_match_total += new_match

            # Inflate non-denom row methods to absorb the denom
            # method shrinkage so the engine's allocated_total stays
            # equal to the input total.  Distributes proportionally
            # to existing non-denom method weights so multi-method
            # orders share the absorption fairly.
            #
            # v2.0.7+ user-cap (user-reported 2026-05-07): exclude
            # user-capped rows from the inflation pool.  When ALL
            # non-denom rows are user-capped, no inflation happens
            # at all and the denom-method shrinkage surfaces as
            # allocation_remaining > 0 (Confirm blocked by
            # is_valid=False until the volunteer adds another row).
            non_denom_lis = [
                li for li in line_items
                if not li['_is_denom'] and not li.get('user_capped')]
            if non_denom_lis and denom_method_reduction > 0:
                nd_method_sum = sum(
                    li['method_amount'] for li in non_denom_lis)
                if nd_method_sum > 0:
                    running = 0
                    for k, li in enumerate(non_denom_lis):
                        if k == len(non_denom_lis) - 1:
                            inflate = (
                                denom_method_reduction - running)
                        else:
                            inflate = round(
                                denom_method_reduction
                                * li['method_amount']
                                / nd_method_sum)
                            running += inflate
                        li['method_amount'] += inflate

            # Distribute the remaining cap budget across non-denom
            # rows proportional to their (possibly inflated) method.
            # Customer = method - new_match.
            #
            # NB: this loop also uses the user-cap-filtered
            # ``non_denom_lis`` — the budget that would have been
            # distributed to a user-capped row stays as
            # allocation_remaining instead of inflating
            # customer_charged on a row the user has pinned.
            non_denom_budget = max(
                0, match_limit - denom_new_match_total)
            if non_denom_lis:
                nd_method_sum_post = sum(
                    li['method_amount'] for li in non_denom_lis)
                running_match = 0
                for k, li in enumerate(non_denom_lis):
                    if k == len(non_denom_lis) - 1:
                        new_match = non_denom_budget - running_match
                    elif nd_method_sum_post > 0:
                        new_match = round(
                            non_denom_budget
                            * li['method_amount']
                            / nd_method_sum_post)
                        running_match += new_match
                    else:
                        new_match = 0
                    li['match_amount'] = new_match
                    li['customer_charged'] = (
                        li['method_amount'] - new_match)

        # Cent adjustment: fix rounding drift so sum of match == cap exactly.
        # Prefer adjusting a non-denom row to keep denom customer_charged
        # untouched.  Falls back to any matched line if no non-denom
        # candidate exists.
        #
        # v2.0.7+ user-cap (user-reported 2026-05-07): prefer non-
        # user-capped non-denom rows so the cent adjustment doesn't
        # silently inflate a row the volunteer has pinned.
        capped_sum = sum(li['match_amount'] for li in line_items)
        cent_diff = match_limit - capped_sum
        if cent_diff != 0:
            non_denom_matched = [
                li for li in line_items
                if li['match_amount'] > 0
                and not li['_is_denom']
                and not li.get('user_capped')
            ]
            if non_denom_matched:
                target = max(non_denom_matched,
                              key=lambda li: li['match_amount'])
            else:
                target = max(
                    (li for li in line_items
                     if li['match_amount'] > 0
                     and not li.get('user_capped')),
                    key=lambda li: li['match_amount'],
                    default=None,
                )
            if target:
                target['match_amount'] += cent_diff
                target['customer_charged'] = (
                    target['method_amount'] - target['match_amount']
                )

    # ── Totals ───────────────────────────────────────────────────
    allocated_total = sum(li['method_amount'] for li in line_items)
    customer_total_paid = sum(li['customer_charged'] for li in line_items)
    fam_subsidy_total = sum(li['match_amount'] for li in line_items)
    allocation_remaining = receipt_total - allocated_total

    # ── Penny reconciliation ────────────────────────────────────
    # When total allocated is within ±1 cent of receipt_total (rounding
    # artifact from matched methods with odd-cent totals), absorb the
    # gap by adjusting the match of the largest matched line item.
    # This keeps method_amount == receipt_total exactly, so vendor
    # reimbursement reconciles to the penny.  The customer charge
    # stays unchanged — only the FAM subsidy absorbs the rounding.
    if 0 < abs(allocation_remaining) <= 1 and line_items:
        # v2.0.7 fix: prefer non-denom targets to preserve
        # denomination alignment.  If the only matched rows are
        # denom rows, the penny adjustment must still avoid
        # touching customer_charged (which represents physical
        # token count × face value — never fractional).  Drop the
        # 1¢ artifact rather than break alignment.
        non_denom_matched = [
            li for li in line_items
            if li['match_percent'] > 0 and not li.get('_is_denom')]
        matched = [li for li in line_items if li['match_percent'] > 0]
        if non_denom_matched:
            target = max(non_denom_matched,
                          key=lambda li: li['method_amount'])
        elif matched:
            target = max(matched, key=lambda li: li['method_amount'])
        else:
            target = None
        if target:
            is_target_denom = bool(target.get('_is_denom'))
            # Guard: never push match_amount below zero — if the
            # adjustment would do that, absorb in customer_charged instead.
            if target['match_amount'] + allocation_remaining >= 0:
                target['method_amount'] += allocation_remaining
                target['match_amount'] += allocation_remaining
            elif not is_target_denom:
                # Non-denom: customer_charged has no alignment
                # constraint, safe to absorb the residual.
                target['method_amount'] += allocation_remaining
                target['customer_charged'] += allocation_remaining
            else:
                # Denom + match insufficient to absorb: clamp
                # match at 0, leave the 1¢ drift in
                # allocation_remaining.  Better than silently
                # corrupting denom alignment.
                target['method_amount'] -= target['match_amount']
                target['match_amount'] = 0
            # Recalculate ALL totals after adjustment.
            #
            # v2.0.2 fix (F-H1): the negative-match-guard branch
            # mutates ``target['customer_charged']``, so
            # ``customer_total_paid`` must also be recomputed
            # alongside ``allocated_total`` and ``fam_subsidy_total``
            # — otherwise the returned dict carries a stale
            # ``customer_total_paid`` and the downstream validation
            # at line 458 ("customer + match == receipt") spuriously
            # reports is_valid=False.  This was a 1¢ drift visible
            # to the user in the summary card / confirmation dialog.
            allocated_total = sum(li['method_amount'] for li in line_items)
            customer_total_paid = sum(
                li['customer_charged'] for li in line_items)
            fam_subsidy_total = sum(li['match_amount'] for li in line_items)
            allocation_remaining = receipt_total - allocated_total

    # Validate allocation matches receipt total (tolerance ±1 cent)
    if abs(allocated_total - receipt_total) > 1:
        errors.append(
            f"Total allocated ({format_dollars(allocated_total)}) does not match "
            f"receipt total ({format_dollars(receipt_total)}). "
            f"Remaining: {format_dollars(allocation_remaining)}."
        )

    # Validate customer + subsidy = receipt total
    reconciled_total = customer_total_paid + fam_subsidy_total
    if abs(reconciled_total - receipt_total) > 1:
        errors.append(
            f"Customer paid ({format_dollars(customer_total_paid)}) + FAM Match "
            f"({format_dollars(fam_subsidy_total)}) = {format_dollars(reconciled_total)}, "
            f"does not match receipt total ({format_dollars(receipt_total)})."
        )

    is_valid = len(errors) == 0

    # Strip internal flag before returning (callers shouldn't depend
    # on this implementation detail).
    for li in line_items:
        li.pop('_is_denom', None)

    return {
        'line_items': line_items,
        'customer_total_paid': customer_total_paid,
        'fam_subsidy_total': fam_subsidy_total,
        'is_valid': is_valid,
        'allocated_total': allocated_total,
        'allocation_remaining': allocation_remaining,
        'errors': errors,
        'match_was_capped': match_was_capped,
        'uncapped_fam_subsidy_total': uncapped_total,
    }


# ════════════════════════════════════════════════════════════════════
# Canonical payment-state resolver (Phase 6 engine consolidation)
# ════════════════════════════════════════════════════════════════════
#
# Single source of truth for the post-cap-aware-fallback-with-
# forfeit-and-give-back state.  All UI surfaces, save paths, and
# reports should call this — not the lower-level
# ``calculate_payment_breakdown`` and not their own private cap math.
#
# The function takes:
#
#   receipt_total   — vendor reimbursement target (cents)
#   items           — list of item dicts (typically PaymentRow.get_data()
#                     output): each must carry method_amount, match_percent,
#                     denomination, and ideally bound_vendor_id +
#                     method_name_snapshot.  Mutated IN PLACE so callers
#                     can pass items directly through to the save path.
#   match_limit     — remaining cap available for THIS resolution.
#                     Caller is responsible for subtracting prior
#                     consumption (see ``get_customer_prior_match`` /
#                     ``_customer_prior_match_excluding_txn``).
#
# Returns the canonical engine result dict (same shape as
# ``calculate_payment_breakdown`` plus a ``denom_overage_cents``
# field) AFTER:
#
#   1. cap-aware match reduction (with proper denom-customer-fixed
#      handling on both common and fallback paths)
#   2. denomination forfeit reduction (per-vendor when bindings
#      provided, else order-level)
#   3. cap-aware Pass 4 give-back to non-denom rows
#   4. items[] mutated to reflect the final cap-aware state (method,
#      match, customer_charged all consistent with result.line_items)
#
# Bug classes this consolidation eliminates:
#
#   * Engine cap fallback inflating denom customer (#5, #17 from
#     onsite findings)
#   * _collect_line_items capping method without updating match /
#     customer (drift between items and result.line_items)
#   * Save path's own cap step diverging from engine cap step (#9, #18)
#   * AdjustmentDialog's parallel cap implementation drifting (#13, #14)
#
# The function is additive — existing callers can keep using
# ``calculate_payment_breakdown`` directly.  Migration is per-call-site.

def apply_denomination_forfeit(
    result: dict,
    items: list,
    overage: int,
    vendor_receipts: dict | None = None,
) -> None:
    """Reduce match (Phase A) and customer (Phase B) on denominated
    line items so vendor reimbursement equals receipt total.

    Canonical module-level implementation (v2.0.7-final, schema v36).
    Both PaymentScreen and AdjustmentDialog delegate here so the
    forfeit math is computed exactly once in the codebase.

    Mutates ``result`` and ``items`` in place.

    Args:
        result: engine output dict (from ``calculate_payment_breakdown``).
            Must contain ``line_items`` list of per-row dicts; may
            also contain ``allocated_total``, ``fam_subsidy_total``,
            ``customer_total_paid``, ``match_was_capped``.  Whichever
            of these are present get updated post-forfeit.
        items: list of caller's item dicts (typically PaymentRow.get_data()
            output OR existing payment_line_items rows).  Each
            position MUST line up 1:1 with ``result['line_items']``.
            Each item may carry ``denomination``, ``bound_vendor_id``,
            ``match_percent_snapshot``/``match_percent``,
            ``customer_forfeit_cents``.
        overage: total over-allocation (= allocated - receipt) the
            forfeit must absorb.  Caller is responsible for detecting
            denom-overage (overage > 0 AND overage <= effective_denom_sum)
            before invoking — this function does NOT re-validate.
        vendor_receipts: optional dict mapping vendor_id → receipt_total
            for vendor-aware Phase A attribution.  When provided AND
            non-empty, Pass 1 uses per-vendor binding to attribute
            forfeit reductions to the over-allocated vendor's denom
            row.  When None or empty, Pass 1 is skipped and the
            legacy first-with-match fallback (Pass 2) handles the
            entire reduction.

    Two-phase model (user-reported 2026-05-07 final policy):

      * **Phase A (FAM match reduction)** is silent — the customer
        never had the FAM match money to lose; FAM is just
        contributing less because the receipt has no headroom.

      * **Phase B (token-value forfeit)** is the customer's real
        loss — when the denomination unit overshoots the receipt
        even after match is fully reduced, the EXCESS portion of
        the token's face value doesn't reach the vendor.  Tracked
        in ``customer_forfeit_cents`` on the line item AND the
        items dict.

    Algorithm:
      1. Pass 1 — vendor-aware: for each over-allocated vendor,
         apply Phase A then Phase B on the bound denom row until
         the vendor's overage is absorbed.
      2. Pass 2 — legacy fallback: any remaining overage walks all
         line items reducing match wherever positive (Phase A only).
      3. Pass 3 — penny reconciliation: when Pass 1's per-vendor
         reduction exceeded order-level overage (because non-denom
         rows had headroom that masked it), give the residue back
         to the largest non-denom matched row so the order ties
         out to ±0¢.
      4. Pass 4 — cap-aware give-back: when ``match_was_capped`` is
         True and total_reduction > 0, redistribute the freed cap
         capacity to non-denom rows (raise their match, drop their
         customer by the same amount, method unchanged).

    Returns: None (mutates in place).
    """
    if vendor_receipts is None:
        vendor_receipts = {}
    single_vendor_id = (
        next(iter(vendor_receipts.keys()))
        if len(vendor_receipts) == 1 else None)

    # Per-vendor allocation from CURRENT line items, plus a reverse
    # map (item index → bound vendor) so forfeit reductions land
    # surgically on the over-allocated vendor's bound denom row.
    # Only denominated rows can carry forfeit; non-denom rows are
    # excluded because they distribute by remainder.
    vendor_alloc: dict = {vid: 0 for vid in vendor_receipts}
    item_vendor: dict = {}
    for i, li in enumerate(result['line_items']):
        if li['method_amount'] <= 0:
            continue
        if i >= len(items):
            continue
        item = items[i]
        denom = item.get('denomination')
        if not (denom and denom > 0):
            continue
        bound_vid = item.get('bound_vendor_id')
        if bound_vid is None and single_vendor_id is not None:
            bound_vid = single_vendor_id
        if bound_vid in vendor_receipts:
            vendor_alloc[bound_vid] += li['method_amount']
            item_vendor[i] = bound_vid

    over_per_vendor = {
        vid: vendor_alloc[vid] - vendor_receipts[vid]
        for vid in vendor_receipts
        if vendor_alloc[vid] > vendor_receipts[vid]
    }

    total_reduction = 0
    total_match_reduction = 0
    total_customer_reduction = 0
    remaining_overage = overage

    # ── Pass 1: vendor-aware Phase A + Phase B ──────────────────
    for vid, vendor_overage in over_per_vendor.items():
        v_remain = vendor_overage
        if v_remain <= 0:
            continue
        for i, li in enumerate(result['line_items']):
            if v_remain <= 0:
                break
            if item_vendor.get(i) != vid:
                continue
            # Phase A: reduce match
            if li['match_amount'] > 0:
                match_red = min(v_remain, li['match_amount'])
                li['match_amount'] -= match_red
                li['method_amount'] -= match_red
                items[i]['method_amount'] = li['method_amount']
                items[i]['match_amount'] = li['match_amount']
                v_remain -= match_red
                total_reduction += match_red
                total_match_reduction += match_red
                remaining_overage = max(
                    0, remaining_overage - match_red)
            # Phase B: reduce customer_charged when match exhausted
            if v_remain > 0 and li['customer_charged'] > 0:
                cust_red = min(v_remain, li['customer_charged'])
                li['customer_charged'] -= cust_red
                li['method_amount'] -= cust_red
                li['customer_forfeit_cents'] = (
                    li.get('customer_forfeit_cents', 0)
                    + cust_red)
                items[i]['method_amount'] = li['method_amount']
                items[i]['customer_charged'] = (
                    li['customer_charged'])
                items[i]['customer_forfeit_cents'] = (
                    items[i].get('customer_forfeit_cents', 0)
                    + cust_red)
                v_remain -= cust_red
                total_reduction += cust_red
                total_customer_reduction += cust_red
                remaining_overage = max(
                    0, remaining_overage - cust_red)

    # ── Pass 2: legacy fallback (Phase A only) ──────────────────
    if remaining_overage > 0:
        for i, li in enumerate(result['line_items']):
            if remaining_overage <= 0:
                break
            if i >= len(items):
                continue
            if li['match_amount'] > 0:
                reduction = min(remaining_overage, li['match_amount'])
                li['match_amount'] = li['match_amount'] - reduction
                li['method_amount'] = li['method_amount'] - reduction
                items[i]['method_amount'] = li['method_amount']
                items[i]['match_amount'] = li['match_amount']
                remaining_overage -= reduction
                total_reduction += reduction
                total_match_reduction += reduction

    # ── Pass 3: residual penny reconciliation ───────────────────
    residue = total_reduction - overage
    if residue > 0:
        best_idx = None
        best_method = -1
        for i, li in enumerate(result['line_items']):
            if i >= len(items):
                continue
            item = items[i]
            denom = item.get('denomination')
            if denom and denom > 0:
                continue
            pct = item.get('match_percent_snapshot') or item.get('match_percent') or 0
            if pct <= 0:
                continue
            if li['method_amount'] > best_method:
                best_method = li['method_amount']
                best_idx = i
        if best_idx is not None and best_idx < len(items):
            target = result['line_items'][best_idx]
            target['method_amount'] += residue
            target['match_amount'] += residue
            items[best_idx]['method_amount'] = target['method_amount']
            items[best_idx]['match_amount'] = target['match_amount']
            total_reduction -= residue
            total_match_reduction -= residue

    # Update result totals
    if 'allocated_total' in result:
        result['allocated_total'] = (
            result['allocated_total'] - total_reduction)
    if 'fam_subsidy_total' in result:
        result['fam_subsidy_total'] = (
            result['fam_subsidy_total'] - total_match_reduction)
    if 'customer_total_paid' in result:
        result['customer_total_paid'] = (
            result['customer_total_paid'] - total_customer_reduction)

    # ── Pass 4: cap-aware match give-back ───────────────────────
    if (result.get('match_was_capped')
            and total_reduction > 0):
        headrooms: list = []
        for i, li in enumerate(result['line_items']):
            if i >= len(items):
                continue
            item = items[i]
            denom = item.get('denomination')
            if denom and denom > 0:
                continue
            pct = (item.get('match_percent_snapshot')
                   or item.get('match_percent') or 0)
            if pct <= 0:
                continue
            # v2.0.7+ user-cap (user-reported 2026-05-07): skip
            # rows the volunteer has explicitly capped.  Pass 4
            # would otherwise inflate the typed customer_charged
            # to absorb forfeit-pass shrinkage.
            if item.get('user_capped'):
                continue
            uncapped = round(
                li['method_amount'] * pct / (100.0 + pct))
            room = uncapped - li['match_amount']
            if room > 0:
                headrooms.append((i, room))

        if headrooms:
            room_total = sum(r for _, r in headrooms)
            give_back_total = min(total_reduction, room_total)
            running = 0
            for k, (idx, room) in enumerate(headrooms):
                if k == len(headrooms) - 1:
                    give = give_back_total - running
                else:
                    give = round(
                        give_back_total * room / room_total)
                    running += give
                give = min(give, room)
                if give <= 0:
                    continue
                target = result['line_items'][idx]
                target['match_amount'] += give
                target['customer_charged'] -= give
                items[idx]['match_amount'] = target['match_amount']
                items[idx]['customer_charged'] = (
                    target['customer_charged'])
            result['fam_subsidy_total'] = (
                result['fam_subsidy_total'] + give_back_total)
            result['customer_total_paid'] = (
                result['customer_total_paid'] - give_back_total)


def resolve_payment_state(
    receipt_total: int,
    items: list,
    match_limit: int | None = None,
    apply_denomination_forfeit_fn=None,
) -> dict:
    """Compute the canonical post-cap, post-forfeit, post-give-back
    payment state.  See module-level comment for full contract.

    The forfeit step requires per-vendor binding awareness which
    lives in ``PaymentScreen._apply_denomination_forfeit``.  Since
    ``calculations.py`` doesn't know about vendors, the caller
    passes the forfeit function as a parameter (typically the
    bound method ``screen._apply_denomination_forfeit``).  When
    ``apply_denomination_forfeit_fn`` is None, the order-level
    denom-overage detection runs but no per-vendor reduction
    happens — appropriate for engine-only test contexts.
    """
    if not items:
        return calculate_payment_breakdown(
            receipt_total, [], match_limit=match_limit)

    # Build engine entries from items — passing through method_amount,
    # match_percent, and denomination so the engine's denom-aware
    # cap path engages correctly.
    entries = [
        {'method_amount': it['method_amount'],
         'match_percent': it['match_percent'],
         'denomination': it.get('denomination'),
         # v2.0.7+ user-cap (user-reported 2026-05-07): propagate
         # the row's user-cap flag to the engine so cap-aware
         # paths preserve customer_charged for user-typed values.
         'user_capped': bool(it.get('user_capped', False))}
        for it in items
    ]
    result = calculate_payment_breakdown(
        receipt_total, entries, match_limit=match_limit)

    # Detect denomination overage (allocated > receipt by ≤ one
    # effective unit per denom row).
    allocated = result.get('allocated_total', 0)
    overage = allocated - receipt_total
    denom_overage = 0
    if overage > 0:
        effective_denom_sum = 0
        for it in items:
            denom = it.get('denomination')
            if denom and denom > 0:
                effective_denom_sum += charge_to_method_amount(
                    denom, it['match_percent'])
        if effective_denom_sum > 0 and overage <= effective_denom_sum:
            denom_overage = overage

    if denom_overage > 0 and apply_denomination_forfeit_fn is not None:
        apply_denomination_forfeit_fn(result, items, denom_overage)

    # Sync items from result.line_items so downstream callers (save
    # path, Layer 2C, reports) see the final cap-aware state.
    # ``_apply_denomination_forfeit`` updates items for denom rows
    # AND non-denom rows in Pass 4, but doesn't propagate the
    # engine's cap-fallback non-denom-method INFLATION (which only
    # exists on result.line_items).
    for i, li in enumerate(result['line_items']):
        if i < len(items):
            items[i]['method_amount'] = li['method_amount']
            items[i]['match_amount'] = li['match_amount']
            items[i]['customer_charged'] = li['customer_charged']

    # v2.0.7 fix (user-reported 2026-05-06): defense-in-depth
    # denomination snap.  The cap-aware fallback and penny-
    # reconciliation branches in ``calculate_payment_breakdown``
    # are supposed to keep denom rows' ``customer_charged`` at
    # integer multiples of ``denomination`` (= physical token
    # face value × unit count).  Edge cases — particularly the
    # AdjustmentDialog flow with no per-vendor forfeit callback,
    # combined with match-cap fallback ``round()`` artifacts —
    # can leak fractional drift onto a denom row's customer_
    # charged.  The DB ``chk_pli_invariant_*`` trigger only
    # validates customer + match = method, so the misaligned
    # row saves successfully and shows up in reports as e.g.
    # "Food Bucks $0.47" — meaningless for a $2-denom method.
    #
    # Snap each denom row down to the nearest multiple of its
    # denomination, absorbing the drift into ``match_amount``.
    # ``method_amount`` is preserved (= customer + match still
    # holds), and the row's vendor-reimbursement value is
    # unchanged.  The customer's "physical token count" gets
    # rounded down to a valid integer; the difference becomes
    # FAM match.
    #
    # Also self-heals existing misaligned rows that load through
    # AdjustmentDialog and feed back into ``resolve_payment_state``
    # — so the next save corrects the drift instead of
    # propagating it.
    #
    # v2.0.7 follow-up (user-reported 2026-05-07, Food RX under-
    # denomination scenario): skip the snap when the row has
    # ``customer_forfeit_cents > 0``.  Phase B of
    # ``_apply_denomination_forfeit`` LEGITIMATELY produces a
    # sub-denomination ``customer_charged`` when the customer
    # hands over a denomination unit larger than the receipt
    # remaining (e.g. $10 Food RX token to a $6.52 receipt → cc=
    # $6.52, match=$0, forfeit=$3.48).  The snap-back's "round
    # down to nearest multiple" rule would compute snapped_cc=$0
    # for that case and dump the entire $6.52 into match_amount —
    # which then misrepresents the transaction as "FAM funded
    # everything, customer paid nothing" in every report.
    # ``customer_forfeit_cents > 0`` is the unambiguous signal
    # that the Phase B step was applied; skipping snap-back on
    # those rows preserves the engine's intended sub-denomination
    # state.
    for it in items:
        denom = it.get('denomination') or 0
        if denom <= 0:
            continue
        cc = it.get('customer_charged', 0)
        if cc < 0:
            continue
        if it.get('customer_forfeit_cents', 0) > 0:
            # Phase B forfeit was applied — sub-denomination
            # customer_charged is intentional.  Verify the
            # forfeit + customer math reconciles to a valid
            # token count as a defensive belt-and-suspenders
            # check, then leave the row alone.
            assert (cc + it['customer_forfeit_cents']) % denom == 0, (
                f"Phase B forfeit produced inconsistent state: "
                f"customer_charged={cc} + forfeit="
                f"{it['customer_forfeit_cents']} is not a multiple "
                f"of denomination={denom}.  Forfeit accounting is "
                f"corrupted.")
            continue
        snapped_cc = (cc // denom) * denom
        if snapped_cc != cc:
            drift = cc - snapped_cc
            it['customer_charged'] = snapped_cc
            it['match_amount'] = it.get('match_amount', 0) + drift
            # method_amount preserved: it = (cc - drift) + (match + drift)

    result['denom_overage_cents'] = denom_overage
    return result
