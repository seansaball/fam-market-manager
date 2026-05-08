"""Layer 2C reconciliation handles single-vendor multi-receipt orders
(v2.0.7 fix, user-reported 2026-05-06).

Reproducer:
  Customer has 3 receipts at the same vendor (Rockin' Cat Organic
  Coffee & Tea) for $1.45 + $25.32 + $25.41 = $52.18 total.  The
  Vendor Breakdown grid correctly sums these into a single
  $52.18 row for the vendor.

  Volunteer adds: 4 × $2 JH Food Bucks ($8 charge, $4.18 match
  after forfeit, $12.18 method) + 2 × $10 Food RX ($20 charge,
  $20 match, $40 method).  Sum: $52.18 — exactly matches the
  vendor's total.

  Click Confirm Payment → "Over-allocation on Rockin' Cat Organic
  Coffee & Tea's receipt: $52.18 of payments are being applied
  to a $1.45 receipt".

Root cause: Layer 2C builds a per-vendor → list-of-txn-ids map,
but for single-vendor orders the row has ``bound_vendor_id=None``
(the binding is implicit in the order context).  The lookup falls
back to ``[first_transaction_id]`` and dumps the entire
method_amount against the first receipt, which legitimately is
over-allocation for THAT receipt alone.

Fix: when ``bound_vid`` is None and the order has exactly one
distinct vendor, treat target_ids as ALL of that vendor's
transactions so the proportional split runs.

The actual SAVE-path algorithm at line ~2664 of payment_screen.py
already has the same single-vendor fallback (``single_vendor_id``).
This test pins parity between the simulation (Layer 2C) and the
save algorithm, so they don't drift again.
"""

import pytest


def _simulate_layer2c(items, transactions):
    """Replica of the Layer 2C per-transaction reconciliation logic
    in PaymentScreen._confirm_payment, exposed as a pure function
    for testing.  Returns ``per_txn_alloc`` dict.

    Mirrors lines ~3213-3304 of payment_screen.py.  Kept narrow
    (denom Phase 1 only — the user's reproducer has no non-denom
    rows) but the fix is the same for both phases.
    """
    per_txn_alloc = {t['id']: 0 for t in transactions}
    vendor_to_txn_ids: dict[int, list[int]] = {}
    for t in transactions:
        vid = t.get('vendor_id')
        if vid is not None:
            vendor_to_txn_ids.setdefault(vid, []).append(t['id'])
    single_order_vendor_id = (
        next(iter(vendor_to_txn_ids.keys()))
        if len(vendor_to_txn_ids) == 1 else None)

    txn_lookup = {t['id']: t for t in transactions}
    for item in items:
        denom = item.get('denomination')
        if not (denom and denom > 0):
            continue
        bound_vid = item.get('bound_vendor_id')
        if bound_vid is None and single_order_vendor_id is not None:
            bound_vid = single_order_vendor_id
        target_ids = (
            vendor_to_txn_ids.get(bound_vid)
            if bound_vid is not None else None)
        if not target_ids:
            target_ids = [transactions[0]['id']] if transactions else []
            if not target_ids:
                continue
        ma = item['method_amount']
        if len(target_ids) == 1:
            per_txn_alloc[target_ids[0]] += ma
        else:
            per_txn_remaining = []
            total_remaining = 0
            for tid in target_ids:
                t = txn_lookup[tid]
                left = max(0, t['receipt_total'] - per_txn_alloc[tid])
                per_txn_remaining.append(left)
                total_remaining += left
            if total_remaining <= 0:
                per_txn_alloc[target_ids[-1]] += ma
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
                per_txn_alloc[tid] += share
    return per_txn_alloc


# ──────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────


class TestSingleVendorMultiReceiptLayer2C:

    def test_user_reproducer_three_receipts_same_vendor(self):
        """The exact user reproducer: 3 receipts at Rockin' Cat,
        $52.18 total, denom rows with bound_vendor_id=None
        (single-vendor mode)."""
        # 3 receipts at vendor_id=42
        transactions = [
            {'id': 1, 'vendor_id': 42, 'receipt_total': 145},
            {'id': 2, 'vendor_id': 42, 'receipt_total': 2532},
            {'id': 3, 'vendor_id': 42, 'receipt_total': 2541},
        ]
        # Two denom rows, both with bound_vendor_id=None (single-
        # vendor implicit binding):
        items = [
            # 4 × $2 Food Bucks → $12.18 method (after forfeit
            # reducing match from $8 to $4.18)
            {'method_amount': 1218, 'denomination': 200,
             'bound_vendor_id': None,
             'method_name_snapshot': 'JH Food Bucks'},
            # 2 × $10 Food RX → $40 method
            {'method_amount': 4000, 'denomination': 1000,
             'bound_vendor_id': None,
             'method_name_snapshot': 'Food RX'},
        ]

        per_txn_alloc = _simulate_layer2c(items, transactions)

        # Each receipt should get its proportional share, summing
        # to the receipt total within ±1¢.
        for t in transactions:
            allocated = per_txn_alloc[t['id']]
            receipt = t['receipt_total']
            assert abs(allocated - receipt) <= 1, (
                f"txn {t['id']} (receipt ${receipt/100:.2f}) got "
                f"${allocated/100:.2f} allocated.  Pre-fix this "
                f"was the over-allocation bug — single-vendor "
                f"multi-receipt orders had bound_vendor_id=None, "
                f"which fell back to 'first txn only' dumping the "
                f"entire $52.18 against the $1.45 receipt.")

    def test_total_allocation_equals_total_receipt(self):
        """Sanity: even with the fix, the SUM of per-txn allocations
        must equal the SUM of method_amounts (== order total)."""
        transactions = [
            {'id': 1, 'vendor_id': 42, 'receipt_total': 145},
            {'id': 2, 'vendor_id': 42, 'receipt_total': 2532},
            {'id': 3, 'vendor_id': 42, 'receipt_total': 2541},
        ]
        items = [
            {'method_amount': 1218, 'denomination': 200,
             'bound_vendor_id': None,
             'method_name_snapshot': 'JH Food Bucks'},
            {'method_amount': 4000, 'denomination': 1000,
             'bound_vendor_id': None,
             'method_name_snapshot': 'Food RX'},
        ]
        per_txn_alloc = _simulate_layer2c(items, transactions)
        assert sum(per_txn_alloc.values()) == 5218

    def test_multi_vendor_unbound_denom_unchanged(self):
        """The fix is gated on len(vendor_to_txn_ids) == 1 —
        multi-vendor orders MUST still require explicit binding
        (the dropdown enforces this in the UI; Layer 2C falls back
        to first-txn for safety)."""
        # Two different vendors
        transactions = [
            {'id': 1, 'vendor_id': 42, 'receipt_total': 1000},
            {'id': 2, 'vendor_id': 99, 'receipt_total': 1000},
        ]
        items = [
            {'method_amount': 400, 'denomination': 200,
             'bound_vendor_id': None,
             'method_name_snapshot': 'JH Food Bucks'},
        ]
        per_txn_alloc = _simulate_layer2c(items, transactions)
        # Multi-vendor + no binding → falls back to first-txn (the
        # legacy defensive default).  This is the case the
        # eligibility / Layer-2 guards earlier in the function are
        # supposed to catch BEFORE we reach Layer 2C.
        assert per_txn_alloc[1] == 400
        assert per_txn_alloc[2] == 0

    def test_single_vendor_single_receipt_unchanged(self):
        """Single-vendor single-receipt: the old single-target
        fast path still applies — fix doesn't alter behavior."""
        transactions = [
            {'id': 1, 'vendor_id': 42, 'receipt_total': 1000},
        ]
        items = [
            {'method_amount': 1000, 'denomination': 200,
             'bound_vendor_id': None,
             'method_name_snapshot': 'JH Food Bucks'},
        ]
        per_txn_alloc = _simulate_layer2c(items, transactions)
        assert per_txn_alloc[1] == 1000

    def test_explicit_binding_overrides_implicit(self):
        """If a denom row HAS an explicit bound_vendor_id, it's
        used as-is — the single-vendor fallback only kicks in
        when bound_vid is None."""
        transactions = [
            {'id': 1, 'vendor_id': 42, 'receipt_total': 145},
            {'id': 2, 'vendor_id': 42, 'receipt_total': 2532},
            {'id': 3, 'vendor_id': 42, 'receipt_total': 2541},
        ]
        items = [
            {'method_amount': 1218, 'denomination': 200,
             'bound_vendor_id': 42,  # explicit
             'method_name_snapshot': 'JH Food Bucks'},
        ]
        per_txn_alloc = _simulate_layer2c(items, transactions)
        # Same proportional split — explicit binding gives same
        # vendor's transactions
        assert sum(per_txn_alloc.values()) == 1218


class TestParityWithSavePathLogic:
    """The save-path algorithm at line ~2664 of payment_screen.py
    already has the single_vendor_id fallback.  Pin the simulation
    matches that behavior so the two algorithms can't silently
    drift again."""

    def test_layer2c_matches_save_algorithm_signature(self):
        """Source-pin: PaymentScreen._confirm_payment Layer 2C
        and PaymentScreen._apply_denomination_forfeit must both
        use the ``bound_vid is None and single_vendor_id is not None``
        fallback pattern.  If either drops it, multi-receipt
        orders silently misallocate."""
        import inspect
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import apply_denomination_forfeit

        confirm_src = inspect.getsource(
            PaymentScreen._confirm_payment)
        # v2.0.7-final consolidation: forfeit math lives in
        # fam.utils.calculations.apply_denomination_forfeit (a
        # module-level function shared by PaymentScreen and
        # AdjustmentDialog).  Source-pin against it directly.
        forfeit_src = inspect.getsource(apply_denomination_forfeit)

        # Both must contain the implicit-binding fallback
        assert (
            'bound_vid is None and single_order_vendor_id is not None'
            in confirm_src), (
            "Layer 2C in _confirm_payment must use the "
            "single-vendor fallback for unbound denom rows.")
        assert (
            'bound_vid is None and single_vendor_id is not None'
            in forfeit_src), (
            "Canonical apply_denomination_forfeit must use the "
            "single-vendor fallback for unbound denom rows.")
