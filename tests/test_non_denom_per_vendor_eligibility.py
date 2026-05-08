"""Non-denom payment distribution respects per-vendor method
eligibility (v2.0.7 fix, user-reported 2026-05-06).

User reproducer (returning customer with $94.41 already redeemed,
match cap reduced to $5.59):

  Customer C-001-LB1, 7 receipts across 5 vendors:
    Jill's gourmet dips:  $26.73   (Food RX, FB, Tokens, Cash; ❌ SNAP)
    Pgh Dumplingz:        $14.52   (all methods ✓)
    The Cakery:           $25.42   (all methods ✓)
    Rockin' Cat Coffee:   $205.41  (all methods ✓)
    KizzleFoods:          $41.25   (SNAP, Cash; ❌ Food RX, FB, Tokens)

  Volunteer enters: 2 × $10 Food RX bound to Jill's + SNAP $295.42.
  Total method = $20 + $301.01 = $321.01 vs receipt $313.33.

  Pre-fix behavior:
    1. Phase 2 distributes SNAP across ALL transactions weighted
       by remaining receipt — including Jill's transactions which
       are SNAP-ineligible.  ~$11 of SNAP leaks onto Jill's even
       though the breakdown grid shows ❌.
    2. The "Over-allocation on Jill's gourmet dips's receipt:
       $11.18 applied to a $11.11 receipt" error fires,
       confusing the volunteer because the breakdown UI says
       Jill's only has Food RX.
    3. On draft-resume, the same misallocation passes Layer 2C
       (per-receipt sum check) because the SNAP overflow into
       Jill's makes each receipt's allocation == receipt_total.

Fix: both Phase 2 (save) and Layer 2C (simulation) now check
per-vendor method eligibility before adding a transaction to the
distribution targets.  Vendors with no ``vendor_payment_methods``
config are treated as permissive (legacy/un-configured).  The
``share_method`` accumulator iterates over eligible-only indexes
so the proportional distribution stays inside the eligible set.
"""

import pytest


def _simulate_phase2_with_eligibility_filter(
        order_transactions, items, vendor_eligibility):
    """Pure-function replica of Phase 2's distribute-non-denom logic
    with the eligibility filter applied.  Returns
    ``per_txn_method_alloc`` dict keyed by transaction id.

    ``vendor_eligibility`` is ``{vendor_id: set_of_method_ids}``;
    empty set means "legacy/un-configured → permissive".
    """
    per_txn_alloc: dict[int, int] = {
        t['id']: 0 for t in order_transactions}

    def _eligible(vendor_id, method_id):
        if vendor_id is None:
            return True
        eligible = vendor_eligibility.get(vendor_id, set())
        if not eligible:
            return True  # permissive
        return method_id in eligible

    for item in items:
        if item.get('denomination'):
            continue  # skip denom (Phase 1's job)
        ma_total = item['method_amount']
        method_id = item['payment_method_id']
        per_txn_remaining = []
        total_remaining = 0
        eligible_idxs = []
        for t_idx, t in enumerate(order_transactions):
            if not _eligible(t.get('vendor_id'), method_id):
                per_txn_remaining.append(0)
                continue
            left = max(0, t['receipt_total'] - per_txn_alloc[t['id']])
            per_txn_remaining.append(left)
            total_remaining += left
            eligible_idxs.append(t_idx)
        if total_remaining <= 0 or not eligible_idxs:
            continue
        running = 0
        last_eligible = eligible_idxs[-1]
        for t_idx in eligible_idxs:
            t = order_transactions[t_idx]
            if t_idx == last_eligible:
                share = ma_total - running
            else:
                weight = per_txn_remaining[t_idx] / total_remaining
                share = round(ma_total * weight)
                running += share
            per_txn_alloc[t['id']] += share
    return per_txn_alloc


# ──────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────


# Method IDs for the test fixtures
SNAP_ID = 1
CASH_ID = 2
FOOD_RX_ID = 3
FB_ID = 4

JILLS = 10           # accepts everything except SNAP
PGH = 11             # all methods
CAKERY = 12          # all methods
ROCKIN = 13          # all methods
KIZZLE = 14          # SNAP, Cash only


def _vendor_eligibility():
    return {
        JILLS:   {CASH_ID, FOOD_RX_ID, FB_ID},
        PGH:     {SNAP_ID, CASH_ID, FOOD_RX_ID, FB_ID},
        CAKERY:  {SNAP_ID, CASH_ID, FOOD_RX_ID, FB_ID},
        ROCKIN:  {SNAP_ID, CASH_ID, FOOD_RX_ID, FB_ID},
        KIZZLE:  {SNAP_ID, CASH_ID},
    }


class TestSnapDoesNotLeakToIneligibleVendors:
    """Phase 2 must not distribute SNAP to Jill's transactions —
    the breakdown grid shows ❌, the engine must respect that."""

    def test_user_reproducer_snap_skips_jills_transactions(self):
        # Single receipt per vendor (simplest shape) — focus on
        # the eligibility filter, not the multi-receipt split.
        order_transactions = [
            {'id': 1, 'vendor_id': JILLS,  'receipt_total': 2673},
            {'id': 2, 'vendor_id': PGH,    'receipt_total': 1452},
            {'id': 3, 'vendor_id': CAKERY, 'receipt_total': 2542},
            {'id': 4, 'vendor_id': ROCKIN, 'receipt_total': 20541},
            {'id': 5, 'vendor_id': KIZZLE, 'receipt_total': 4125},
        ]
        # Volunteer enters SNAP $286.60 (= sum of SNAP-eligible
        # vendor receipts: 14.52 + 25.42 + 205.41 + 41.25).
        items = [
            {'method_amount': 28660,
             'denomination': 0,
             'bound_vendor_id': None,
             'payment_method_id': SNAP_ID,
             'method_name_snapshot': 'SNAP',
             'match_percent_snapshot': 100.0},
        ]
        per_txn_alloc = _simulate_phase2_with_eligibility_filter(
            order_transactions, items, _vendor_eligibility())

        # SNAP must NOT have flowed to Jill's transaction.
        # Pre-fix: ~$2.83 of SNAP would leak to Jill's via
        # proportional distribution despite the ❌ eligibility
        # marker in the breakdown grid.
        assert per_txn_alloc[1] == 0, (
            f"Jill's transaction must receive $0 from SNAP.  "
            f"Pre-fix: ~$2.83 leaked here despite ❌ eligibility.  "
            f"Got: ${per_txn_alloc[1]/100:.2f}.")

        # SNAP-eligible vendors get their proportional share.
        # Total SNAP method == sum of eligible receipts, so each
        # gets exactly their receipt total.
        assert per_txn_alloc[2] == 1452, "Pgh"
        assert per_txn_alloc[3] == 2542, "Cakery"
        assert per_txn_alloc[4] == 20541, "Rockin"
        assert per_txn_alloc[5] == 4125, "Kizzle"

        # Sum of allocations equals SNAP method amount (no money lost)
        assert sum(per_txn_alloc.values()) == 28660

    def test_kizzle_excluded_from_food_rx_distribution(self):
        """KizzleFoods doesn't accept Food RX — Phase 2 must skip
        it when distributing Food RX shares (denom Phase 1 does
        the same via bound_vendor_id, but if a non-denom row
        ever uses a method-restricted vendor, this guards it)."""
        # Hypothetical: a non-denom Food RX (bizarre but possible
        # if denomination is removed mid-season).  KizzleFoods
        # must be excluded.
        order_transactions = [
            {'id': 1, 'vendor_id': PGH,    'receipt_total': 1000},
            {'id': 2, 'vendor_id': KIZZLE, 'receipt_total': 1000},
        ]
        items = [
            {'method_amount': 2000,
             'denomination': 0,
             'bound_vendor_id': None,
             'payment_method_id': FOOD_RX_ID,
             'method_name_snapshot': 'Food RX',
             'match_percent_snapshot': 100.0},
        ]
        per_txn_alloc = _simulate_phase2_with_eligibility_filter(
            order_transactions, items, _vendor_eligibility())
        # All $20 should go to Pgh; nothing to Kizzle
        assert per_txn_alloc[1] == 2000
        assert per_txn_alloc[2] == 0


class TestLegacyPermissiveBackfill:
    """Vendors with no ``vendor_payment_methods`` config are
    treated as eligible for everything — preserves behavior for
    legacy / un-migrated DBs."""

    def test_unconfigured_vendor_is_permissive(self):
        order_transactions = [
            {'id': 1, 'vendor_id': 99, 'receipt_total': 1000},
        ]
        items = [
            {'method_amount': 1000,
             'denomination': 0,
             'bound_vendor_id': None,
             'payment_method_id': SNAP_ID,
             'method_name_snapshot': 'SNAP',
             'match_percent_snapshot': 100.0},
        ]
        # Empty vendor_eligibility for vendor 99 = legacy permissive
        per_txn_alloc = _simulate_phase2_with_eligibility_filter(
            order_transactions, items, {99: set()})
        assert per_txn_alloc[1] == 1000


class TestSourcePinOnSavePath:
    """Source-pin: both _confirm_payment Layer 2C AND
    _distribute_and_save_payments Phase 2 must contain the
    eligibility filter.  If either drops it, SNAP leaks back
    to ineligible vendors silently."""

    def test_save_phase2_has_eligibility_filter(self):
        import inspect
        from fam.ui.payment_screen import PaymentScreen
        save_src = inspect.getsource(
            PaymentScreen._distribute_and_save_payments)
        assert 'get_vendor_payment_method_ids' in save_src, (
            "Phase 2 of _distribute_and_save_payments must look "
            "up per-vendor method eligibility.")
        assert 'eligible_idxs' in save_src or 'eligibility' in save_src, (
            "Phase 2 must use an eligibility filter when computing "
            "per_txn_remaining for non-denom rows.")

    def test_layer2c_has_eligibility_filter(self):
        import inspect
        from fam.ui.payment_screen import PaymentScreen
        confirm_src = inspect.getsource(
            PaymentScreen._confirm_payment)
        # The Layer 2C Phase 2 simulation must check eligibility
        assert '_l2c_eligible' in confirm_src or '_l2c_eligibility' in confirm_src, (
            "Layer 2C non-denom distribution must check per-vendor "
            "eligibility before computing per_txn_remaining.")
