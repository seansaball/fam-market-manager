"""Financial calculation logic for FAM transactions."""


def calculate_payment_breakdown(receipt_total: float, payment_entries: list) -> dict:
    """
    Calculate the full payment breakdown for a transaction.

    Args:
        receipt_total: The gross amount on the paper receipt.
        payment_entries: List of dicts, each with:
            - method_amount (float): amount allocated to this payment method
            - discount_percent (float): 0-100

    Returns:
        Dict with:
            - line_items: list of computed line items
            - customer_total_paid: sum of customer_charged
            - fam_subsidy_total: sum of discount_amount
            - is_valid: bool (True if totals reconcile within tolerance)
            - allocated_total: sum of method_amount
            - allocation_remaining: receipt_total - allocated_total
            - errors: list of error message strings
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
        }

    line_items = []
    for entry in payment_entries:
        method_amount = entry.get('method_amount', 0.0)
        discount_percent = entry.get('discount_percent', 0.0)

        if method_amount < 0:
            errors.append(f"Payment amount cannot be negative (got ${method_amount:.2f}).")
        if not (0 <= discount_percent <= 100):
            errors.append(f"Discount percent must be 0-100 (got {discount_percent}%).")

        discount_amount = round(method_amount * (discount_percent / 100.0), 2)
        customer_charged = round(method_amount - discount_amount, 2)

        line_items.append({
            'method_amount': round(method_amount, 2),
            'discount_percent': discount_percent,
            'discount_amount': discount_amount,
            'customer_charged': customer_charged,
        })

    allocated_total = round(sum(li['method_amount'] for li in line_items), 2)
    customer_total_paid = round(sum(li['customer_charged'] for li in line_items), 2)
    fam_subsidy_total = round(sum(li['discount_amount'] for li in line_items), 2)
    allocation_remaining = round(receipt_total - allocated_total, 2)

    # Validate allocation matches receipt total (tolerance ±$0.01)
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
    }
