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

    # Partition into locked (user entered) vs auto (empty, to be filled)
    locked = []
    auto = []
    for r in rows:
        if r['current_charge'] > 0:
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
        })

    # ── Apply match-limit cap ────────────────────────────────────
    uncapped_total = sum(li['match_amount'] for li in line_items)
    match_was_capped = False

    if match_limit is not None and uncapped_total > match_limit >= 0:
        match_was_capped = True
        cap_ratio = match_limit / uncapped_total
        for li in line_items:
            li['match_amount'] = round(li['match_amount'] * cap_ratio)
            li['customer_charged'] = li['method_amount'] - li['match_amount']

        # Cent adjustment: fix rounding drift so sum of match == cap exactly
        capped_sum = sum(li['match_amount'] for li in line_items)
        cent_diff = match_limit - capped_sum
        if cent_diff != 0:
            # Adjust the line item with the largest match (least % impact)
            target = max(
                (li for li in line_items if li['match_amount'] > 0),
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
        matched = [li for li in line_items if li['match_percent'] > 0]
        if matched:
            target = max(matched, key=lambda li: li['method_amount'])
            # Guard: never push match_amount below zero — if the
            # adjustment would do that, absorb in customer_charged instead.
            if target['match_amount'] + allocation_remaining >= 0:
                target['method_amount'] += allocation_remaining
                target['match_amount'] += allocation_remaining
            else:
                target['method_amount'] += allocation_remaining
                target['customer_charged'] += allocation_remaining
            # Recalculate totals after adjustment
            allocated_total = sum(li['method_amount'] for li in line_items)
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
