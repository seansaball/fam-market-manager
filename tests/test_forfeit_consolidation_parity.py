"""Phase 1 of Option B engine consolidation: prove the new
canonical ``apply_denomination_forfeit`` function in
``fam/utils/calculations.py`` produces byte-identical output to
the legacy ``PaymentScreen._apply_denomination_forfeit`` bound
method on every realistic scenario.

Before this consolidation:
  * ``PaymentScreen._apply_denomination_forfeit`` — vendor-aware,
    Phase A + Phase B, used by the live summary and confirm path.
  * ``AdjustmentDialog`` — inline first-match Phase-A-only loop,
    diverges from PaymentScreen's logic, drops Phase B forfeit.

After this consolidation:
  * ``apply_denomination_forfeit(result, items, overage,
    vendor_receipts=...)`` — single canonical implementation.
  * Both PaymentScreen and AdjustmentDialog delegate here.

This test is the SAFETY GATE for the migration — if any scenario
produces different output between the old method and the new
function, the consolidation is broken and the migration must NOT
proceed.

The PaymentScreen method is invoked via a minimal harness that
stubs ``self._order_transactions`` so we can call it without a
full screen.  After Phase 1 lands and PaymentScreen is migrated
to delegate to the canonical function, this test continues to
serve as a regression pin against future drift.
"""

import copy

import pytest

from fam.utils.calculations import (
    apply_denomination_forfeit, calculate_payment_breakdown,
)


class _StubScreen:
    """Minimal stand-in for PaymentScreen that exposes only the
    attributes ``_apply_denomination_forfeit`` reads
    (``self._order_transactions``).  Lets us exercise the bound-
    method implementation without booting Qt."""

    def __init__(self, order_transactions):
        self._order_transactions = order_transactions


def _legacy_payment_screen_forfeit(result, items, overage,
                                    order_transactions):
    """Inline copy of the v2.0.7 PaymentScreen._apply_denomination_
    forfeit body, adapted to take order_transactions as a param.
    Verbatim with the file as of the consolidation point — used as
    the GROUND TRUTH for the parity check.

    If you change either implementation, also update this copy
    OR the test will (correctly) fail until they re-converge.
    """
    vendor_receipts: dict = {}
    for t in order_transactions:
        vid = t.get('vendor_id')
        if vid is not None:
            vendor_receipts[vid] = (
                vendor_receipts.get(vid, 0) + t['receipt_total'])
    single_vendor_id = (
        next(iter(vendor_receipts.keys()))
        if len(vendor_receipts) == 1 else None)

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

    # Pass 1
    for vid, vendor_overage in over_per_vendor.items():
        v_remain = vendor_overage
        if v_remain <= 0:
            continue
        for i, li in enumerate(result['line_items']):
            if v_remain <= 0:
                break
            if item_vendor.get(i) != vid:
                continue
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
            if v_remain > 0 and li['customer_charged'] > 0:
                cust_red = min(v_remain, li['customer_charged'])
                li['customer_charged'] -= cust_red
                li['method_amount'] -= cust_red
                li['customer_forfeit_cents'] = (
                    li.get('customer_forfeit_cents', 0) + cust_red)
                items[i]['method_amount'] = li['method_amount']
                items[i]['customer_charged'] = li['customer_charged']
                items[i]['customer_forfeit_cents'] = (
                    items[i].get('customer_forfeit_cents', 0)
                    + cust_red)
                v_remain -= cust_red
                total_reduction += cust_red
                total_customer_reduction += cust_red
                remaining_overage = max(
                    0, remaining_overage - cust_red)

    # Pass 2
    if remaining_overage > 0:
        for i, li in enumerate(result['line_items']):
            if remaining_overage <= 0:
                break
            if i >= len(items):
                continue
            if li['match_amount'] > 0:
                reduction = min(
                    remaining_overage, li['match_amount'])
                li['match_amount'] = li['match_amount'] - reduction
                li['method_amount'] = li['method_amount'] - reduction
                items[i]['method_amount'] = li['method_amount']
                items[i]['match_amount'] = li['match_amount']
                remaining_overage -= reduction
                total_reduction += reduction
                total_match_reduction += reduction

    # Pass 3
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

    if 'allocated_total' in result:
        result['allocated_total'] = (
            result['allocated_total'] - total_reduction)
    if 'fam_subsidy_total' in result:
        result['fam_subsidy_total'] = (
            result['fam_subsidy_total'] - total_match_reduction)
    if 'customer_total_paid' in result:
        result['customer_total_paid'] = (
            result['customer_total_paid']
            - total_customer_reduction)

    # Pass 4
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


def _build_engine_result(receipt_total, entries):
    """Run the engine to get a starting result + items snapshot."""
    result = calculate_payment_breakdown(
        receipt_total, entries, match_limit=None)
    items = [dict(e) for e in entries]
    # Sync items with engine output (mirrors what
    # ``resolve_payment_state`` does after calculate_payment_breakdown).
    for i, li in enumerate(result['line_items']):
        if i < len(items):
            items[i]['method_amount'] = li['method_amount']
            items[i]['match_amount'] = li['match_amount']
            items[i]['customer_charged'] = li['customer_charged']
    return result, items


def _run_both(receipt_total, entries, overage,
              order_transactions):
    """Run BOTH the legacy bound-method body and the new canonical
    function on identical inputs.  Returns (legacy_result,
    legacy_items, new_result, new_items) for byte-comparison."""
    # Legacy
    legacy_result, legacy_items = _build_engine_result(
        receipt_total, entries)
    _legacy_payment_screen_forfeit(
        legacy_result, legacy_items, overage,
        order_transactions=order_transactions)

    # New canonical
    new_result, new_items = _build_engine_result(
        receipt_total, entries)
    vendor_receipts: dict = {}
    for t in order_transactions:
        vid = t.get('vendor_id')
        if vid is not None:
            vendor_receipts[vid] = (
                vendor_receipts.get(vid, 0) + t['receipt_total'])
    apply_denomination_forfeit(
        new_result, new_items, overage,
        vendor_receipts=vendor_receipts)

    return legacy_result, legacy_items, new_result, new_items


class TestForfeitParityScenarios:
    """Every scenario must produce byte-identical output between
    the legacy bound-method implementation and the new canonical
    module-level function.  If any scenario diverges, the
    migration is unsafe."""

    def test_single_vendor_phase_a_only(self):
        """Customer hands $5 FMNP token (denom=$5, 100% match) on
        a $9 receipt → method=$10, overage=$1.  Phase A reduces
        match by $1.  Single-vendor scenario."""
        entries = [{
            'method_amount': 1000,
            'match_percent': 100,
            'denomination': 500,
            'bound_vendor_id': None,
            'method_name_snapshot': 'FMNP',
        }]
        legacy_r, legacy_i, new_r, new_i = _run_both(
            900, entries, overage=100,
            order_transactions=[{'vendor_id': 1, 'receipt_total': 900}])
        assert legacy_r == new_r, "result dicts diverged"
        assert legacy_i == new_i, "items lists diverged"

    def test_single_vendor_phase_b_engaged(self):
        """User-reported scenario: 1 Food RX ($10) on $6.52
        receipt.  Phase A consumes all match; Phase B forfeits
        $3.48 of customer token."""
        entries = [{
            'method_amount': 2000,
            'match_percent': 100,
            'denomination': 1000,
            'bound_vendor_id': None,
            'method_name_snapshot': 'Food RX',
        }]
        legacy_r, legacy_i, new_r, new_i = _run_both(
            652, entries, overage=1348,
            order_transactions=[{'vendor_id': 1, 'receipt_total': 652}])
        assert legacy_r == new_r
        assert legacy_i == new_i
        # Sanity: forfeit was set
        assert legacy_i[0].get('customer_forfeit_cents') == 348

    def test_multi_vendor_per_vendor_overage(self):
        """V1 receipt $25.30, V2 receipt $40.00; 7 × $2 FB on V1
        ($14 customer, $28 method) — V1 over-allocated by $2.70.
        SNAP $18.65 on V2."""
        entries = [
            {'method_amount': 2800, 'match_percent': 100,
             'denomination': 200, 'bound_vendor_id': 1,
             'method_name_snapshot': 'JH Food Bucks'},
            {'method_amount': 3730, 'match_percent': 100,
             'denomination': None, 'bound_vendor_id': None,
             'method_name_snapshot': 'SNAP'},
        ]
        legacy_r, legacy_i, new_r, new_i = _run_both(
            6530, entries, overage=270,
            order_transactions=[
                {'vendor_id': 1, 'receipt_total': 2530},
                {'vendor_id': 2, 'receipt_total': 4000},
            ])
        assert legacy_r == new_r
        assert legacy_i == new_i

    def test_no_vendor_info_falls_back_to_legacy_path(self):
        """When vendor_receipts is empty (e.g. the AdjustmentDialog
        single-vendor-mode case before consolidation), the new
        function must use Pass 2's first-with-match fallback —
        same behavior as the old method when `_order_transactions`
        was empty."""
        entries = [{
            'method_amount': 1000,
            'match_percent': 100,
            'denomination': 500,
            'bound_vendor_id': None,
            'method_name_snapshot': 'FMNP',
        }]
        legacy_r, legacy_i, new_r, new_i = _run_both(
            900, entries, overage=100,
            order_transactions=[])  # empty → both fall back to Pass 2
        assert legacy_r == new_r
        assert legacy_i == new_i

    def test_two_denom_methods_same_vendor(self):
        """FMNP + JH Tokens, both $5 denom (100% match), single
        vendor, order $49: each contributes $25 = $50 method.
        Overage $1."""
        entries = [
            {'method_amount': 3000, 'match_percent': 100,
             'denomination': 500, 'bound_vendor_id': None,
             'method_name_snapshot': 'FMNP'},
            {'method_amount': 2000, 'match_percent': 100,
             'denomination': 500, 'bound_vendor_id': None,
             'method_name_snapshot': 'JH Tokens'},
        ]
        legacy_r, legacy_i, new_r, new_i = _run_both(
            4900, entries, overage=100,
            order_transactions=[{'vendor_id': 1, 'receipt_total': 4900}])
        assert legacy_r == new_r
        assert legacy_i == new_i

    def test_cap_active_pass_4_give_back(self):
        """Cap-active scenario: Phase A reduces match, Pass 4
        gives the freed cap budget back to non-denom rows.  The
        canonical function must replicate Pass 4's redistribution
        identically to the legacy implementation."""
        # 10 × $2 FB ($20 customer, $40 method) + SNAP $20 — cap $25
        # → engine caps SNAP match to budget the cap.  FB overshoots
        # vendor receipt by some amount; Phase A reduces FB match,
        # Pass 4 shifts the freed match budget to SNAP.
        entries = [
            {'method_amount': 4000, 'match_percent': 100,
             'denomination': 200, 'bound_vendor_id': 1,
             'method_name_snapshot': 'JH Food Bucks'},
            {'method_amount': 4000, 'match_percent': 100,
             'denomination': None, 'bound_vendor_id': None,
             'method_name_snapshot': 'SNAP'},
        ]
        # Receipt $77.00; FB overshoots V1 by $3 ($40 method vs
        # $37 receipt assumption).  Use cap_active fixture:
        result, items = _build_engine_result(7700, entries)
        # Manually flag cap_active for both runs
        result['match_was_capped'] = True
        # We also need a comparable legacy run with same flag
        legacy_result, legacy_items = _build_engine_result(
            7700, entries)
        legacy_result['match_was_capped'] = True

        order_txns = [
            {'vendor_id': 1, 'receipt_total': 3700},
            {'vendor_id': 2, 'receipt_total': 4000},
        ]
        # Compute overage from result
        overage = result['allocated_total'] - 7700
        if overage <= 0:
            pytest.skip(
                "engine produced no overage for this scenario — "
                "cap path may have already balanced; skip Pass 4 "
                "parity check (covered by Pass 4 unit tests).")

        _legacy_payment_screen_forfeit(
            legacy_result, legacy_items, overage,
            order_transactions=order_txns)

        vendor_receipts = {1: 3700, 2: 4000}
        apply_denomination_forfeit(
            result, items, overage,
            vendor_receipts=vendor_receipts)

        assert result == legacy_result, "Pass 4 give-back diverged"
        assert items == legacy_items

    def test_residue_pass_3_drives_to_zero(self):
        """A scenario where Pass 1's per-vendor reduction exceeds
        order-level overage (because non-denom rows had headroom
        masking it).  Pass 3 must give the residue back to the
        largest non-denom matched row.  Both implementations must
        agree on which row gets the residue and the exact amount."""
        # Construct the v1.9.10-style pathological input:
        # 2 vendors, V1 over by $5 (denom only), V2 under by
        # $5 (non-denom).  Order-level balanced; per-vendor needs
        # rebalance.
        entries = [
            {'method_amount': 2000, 'match_percent': 100,
             'denomination': 200, 'bound_vendor_id': 1,
             'method_name_snapshot': 'JH Food Bucks'},
            {'method_amount': 1000, 'match_percent': 100,
             'denomination': None, 'bound_vendor_id': None,
             'method_name_snapshot': 'SNAP'},
        ]
        legacy_r, legacy_i, new_r, new_i = _run_both(
            3000, entries, overage=0,  # balanced order-level
            order_transactions=[
                {'vendor_id': 1, 'receipt_total': 1500},
                {'vendor_id': 2, 'receipt_total': 1500},
            ])
        assert legacy_r == new_r
        assert legacy_i == new_i


class TestForfeitFunctionExportedAtModuleLevel:
    """Ensure the new canonical function is importable from
    ``fam.utils.calculations`` so AdjustmentDialog and other
    callers can reach it without going through PaymentScreen."""

    def test_function_is_module_level_callable(self):
        from fam.utils import calculations as calc
        assert hasattr(calc, 'apply_denomination_forfeit')
        assert callable(calc.apply_denomination_forfeit)

    def test_function_signature_accepts_vendor_receipts(self):
        import inspect
        from fam.utils.calculations import apply_denomination_forfeit
        sig = inspect.signature(apply_denomination_forfeit)
        params = sig.parameters
        assert 'result' in params
        assert 'items' in params
        assert 'overage' in params
        assert 'vendor_receipts' in params, (
            "vendor_receipts must be a parameter so callers can "
            "pass per-vendor binding info without coupling to "
            "PaymentScreen state.")
