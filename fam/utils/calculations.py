"""Financial calculation logic for FAM transactions."""


def calculate_payment_breakdown(receipt_total: float, payment_entries: list,
                                match_limit: float | None = None) -> dict:
    """
    Calculate the full payment breakdown for a transaction.

    Args:
        receipt_total: The gross amount on the paper receipt.
        payment_entries: List of dicts, each with:
            - method_amount (float): amount allocated to this payment method
            - match_percent (float): 0-999, e.g. 100 = 1:1 match, 200 = 2:1
        match_limit: Optional per-customer daily match cap.  When provided and
            the uncapped FAM subsidy exceeds this value, all match amounts
            are proportionally reduced so the total matches the limit.

    Returns:
        Dict with:
            - line_items: list of computed line items
            - customer_total_paid: sum of customer_charged
            - fam_subsidy_total: sum of match_amount (after cap)
            - is_valid: bool (True if totals reconcile within tolerance)
            - allocated_total: sum of method_amount
            - allocation_remaining: receipt_total - allocated_total
            - errors: list of error message strings
            - match_was_capped: bool (True if the match limit was applied)
            - uncapped_fam_subsidy_total: original match total before cap
    """
    errors = []

    if receipt_total < 0:
        errors.append("Receipt total cannot be negative.")

    if not payment_entries:
        errors.append("At least one payment method is required.")
        return {
            'line_items': [],
            'customer_total_paid': 0.0,
            'fam_subsidy_total': 0.0,
            'is_valid': False,
            'allocated_total': 0.0,
            'allocation_remaining': receipt_total,
            'errors': errors,
            'match_was_capped': False,
            'uncapped_fam_subsidy_total': 0.0,
        }

    line_items = []
    for entry in payment_entries:
        method_amount = entry.get('method_amount', 0.0)
        match_percent = entry.get('match_percent', 0.0)

        if method_amount < 0:
            errors.append(f"Payment amount cannot be negative (got ${method_amount:.2f}).")
        if match_percent < 0:
            errors.append(f"Match percent cannot be negative (got {match_percent}%).")

        match_amount = round(method_amount * (match_percent / (100.0 + match_percent)), 2)
        customer_charged = round(method_amount - match_amount, 2)

        line_items.append({
            'method_amount': round(method_amount, 2),
            'match_percent': match_percent,
            'match_amount': match_amount,
            'customer_charged': customer_charged,
        })

    # ── Apply match-limit cap ────────────────────────────────────
    uncapped_total = round(sum(li['match_amount'] for li in line_items), 2)
    match_was_capped = False

    if match_limit is not None and uncapped_total > match_limit >= 0:
        match_was_capped = True
        cap_ratio = match_limit / uncapped_total
        for li in line_items:
            li['match_amount'] = round(li['match_amount'] * cap_ratio, 2)
            li['customer_charged'] = round(li['method_amount'] - li['match_amount'], 2)

    # ── Totals ───────────────────────────────────────────────────
    allocated_total = round(sum(li['method_amount'] for li in line_items), 2)
    customer_total_paid = round(sum(li['customer_charged'] for li in line_items), 2)
    fam_subsidy_total = round(sum(li['match_amount'] for li in line_items), 2)
    allocation_remaining = round(receipt_total - allocated_total, 2)

    # Validate allocation matches receipt total (tolerance +/-$0.01)
    if abs(allocated_total - receipt_total) > 0.01:
        errors.append(
            f"Total allocated (${allocated_total:.2f}) does not match "
            f"receipt total (${receipt_total:.2f}). "
            f"Remaining: ${allocation_remaining:.2f}."
        )

    # Validate customer + subsidy = receipt total
    reconciled_total = round(customer_total_paid + fam_subsidy_total, 2)
    if abs(reconciled_total - receipt_total) > 0.01:
        errors.append(
            f"Customer paid (${customer_total_paid:.2f}) + FAM Match "
            f"(${fam_subsidy_total:.2f}) = ${reconciled_total:.2f}, "
            f"does not match receipt total (${receipt_total:.2f})."
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
