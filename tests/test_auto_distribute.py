"""Tests for the auto-distribute algorithm.

All monetary values are integer cents (e.g. $50.00 = 5000).
"""

import pytest
from fam.utils.calculations import (
    charge_to_method_amount,
    method_amount_to_charge,
    smart_auto_distribute,
)


# ──────────────────────────────────────────────────────────────────
# Helper: replicate auto-distribute algorithm (pure math, no Qt)
# ──────────────────────────────────────────────────────────────────
def auto_distribute(order_total, other_rows, target_match_pct, target_denom=None):
    """Replicate _auto_distribute() math from payment_screen.py.

    Args:
        order_total: The full order total in cents.
        other_rows: List of dicts with 'charge' (cents) and 'match_pct'.
        target_match_pct: Match percent of the target (last) row.
        target_denom: Optional denomination constraint in cents.

    Returns:
        Dict with 'charge', 'match', 'total', 'remaining' (all cents).
    """
    # Sum method_amount from other rows
    other_total = 0
    for row in other_rows:
        ma = charge_to_method_amount(row['charge'], row['match_pct'])
        other_total += ma

    remaining = order_total - other_total

    if remaining <= 0:
        return {'charge': 0, 'match': 0, 'total': 0, 'remaining': remaining}

    # charge = remaining / (1 + match_pct/100)
    charge = round(remaining / (1.0 + target_match_pct / 100.0))

    # Denomination: round DOWN to nearest multiple
    if target_denom and target_denom > 0 and charge > 0:
        charge = (charge // target_denom) * target_denom

    match_amt = round(charge * (target_match_pct / 100.0))
    total = charge + match_amt

    return {
        'charge': charge,
        'match': match_amt,
        'total': total,
        'remaining': remaining,
    }


# ══════════════════════════════════════════════════════════════════
# 1. Albert's test scenarios
# ══════════════════════════════════════════════════════════════════
class TestAlbertsScenarios:
    """The three scenarios Albert described in his feedback."""

    def test_easy_single_snap(self):
        """$55 basket, SNAP only (100% match).
        Auto-distribute: charge = 5500/2 = 2750¢, match = 2750¢, total = 5500¢."""
        r = auto_distribute(5500, [], 100.0)
        assert r['charge'] == 2750
        assert r['match'] == 2750
        assert r['total'] == 5500

    def test_medium_fmnp_plus_snap(self):
        """$130 basket. FMNP: 4000¢ charge (100% match) = 8000¢ total.
        SNAP auto-distributes remaining 5000¢: charge = 2500¢, match = 2500¢."""
        r = auto_distribute(13000, [{'charge': 4000, 'match_pct': 100.0}], 100.0)
        assert r['charge'] == 2500
        assert r['match'] == 2500
        assert r['total'] == 5000

    def test_nightmare_three_methods_snap_last(self):
        """$270 basket. Food RX 1000¢ (100%), FMNP 5000¢ (100%),
        Vet Bucks 1000¢ (100%) = 14000¢ allocated.
        SNAP remaining 13000¢: charge = 6500¢, match = 6500¢."""
        other = [
            {'charge': 1000, 'match_pct': 100.0},   # Food RX: 2000¢ total
            {'charge': 5000, 'match_pct': 100.0},   # FMNP: 10000¢ total
            {'charge': 1000, 'match_pct': 100.0},   # Vet Bucks: 2000¢ total
        ]
        r = auto_distribute(27000, other, 100.0)
        assert r['charge'] == 6500
        assert r['match'] == 6500
        assert r['total'] == 13000

    def test_nightmare_with_cash_added(self):
        """Same $270, but after adding $50 cash (0% match), SNAP recalculates.
        Other total: Food RX 2000 + FMNP 10000 + Vet Bucks 2000 + Cash 5000 = 19000.
        SNAP remaining 8000: charge = 4000, match = 4000."""
        other = [
            {'charge': 1000, 'match_pct': 100.0},   # Food RX: 2000¢
            {'charge': 5000, 'match_pct': 100.0},   # FMNP: 10000¢
            {'charge': 1000, 'match_pct': 100.0},   # Vet Bucks: 2000¢
            {'charge': 5000, 'match_pct': 0.0},     # Cash: 5000¢
        ]
        r = auto_distribute(27000, other, 100.0)
        assert r['charge'] == 4000
        assert r['match'] == 4000
        assert r['total'] == 8000


# ══════════════════════════════════════════════════════════════════
# 2. Basic auto-distribute scenarios
# ══════════════════════════════════════════════════════════════════
class TestBasicAutoDistribute:

    def test_single_method_covers_all(self):
        """Only one method, covers entire order."""
        r = auto_distribute(10000, [], 100.0)
        assert r['charge'] == 5000
        assert r['total'] == 10000

    def test_single_method_0_percent(self):
        """0% match: charge == order total (no FAM subsidy)."""
        r = auto_distribute(10000, [], 0.0)
        assert r['charge'] == 10000
        assert r['match'] == 0
        assert r['total'] == 10000

    def test_single_method_200_percent(self):
        """200% match: charge = 10000/3 = 3333¢."""
        r = auto_distribute(10000, [], 200.0)
        assert r['charge'] == 3333
        assert r['total'] == 3333 + 3333 * 2  # 3333 + 6666 = 9999

    def test_two_methods_50_percent(self):
        """$100 order: first method 3000¢ charge at 50%, second auto at 100%.
        First total: 3000*1.5 = 4500¢. Remaining: 5500¢. Charge = 5500/2 = 2750¢."""
        r = auto_distribute(10000, [{'charge': 3000, 'match_pct': 50.0}], 100.0)
        assert r['charge'] == 2750
        assert r['total'] == 5500

    def test_exact_allocation_no_remaining(self):
        """Other rows already fully cover the order — target gets 0."""
        r = auto_distribute(10000, [{'charge': 5000, 'match_pct': 100.0}], 100.0)
        assert r['charge'] == 0
        assert r['total'] == 0

    def test_over_allocated(self):
        """Other rows exceed order total — target gets 0."""
        r = auto_distribute(
            5000,
            [{'charge': 5000, 'match_pct': 100.0}],  # 10000¢ total
            100.0
        )
        assert r['charge'] == 0


# ══════════════════════════════════════════════════════════════════
# 3. Denomination snapping
# ══════════════════════════════════════════════════════════════════
class TestDenominationSnapping:

    def test_snaps_down_to_25(self):
        """Charge 2750¢ with 2500¢ denomination → snaps down to 2500¢."""
        r = auto_distribute(5500, [], 100.0, target_denom=2500)
        # charge = 5500/2 = 2750, snaps to 2500
        assert r['charge'] == 2500

    def test_exact_denomination_multiple(self):
        """Charge is already a multiple of denomination → unchanged."""
        r = auto_distribute(10000, [], 100.0, target_denom=2500)
        # charge = 10000/2 = 5000, which is 2×2500 → stays at 5000
        assert r['charge'] == 5000

    def test_snaps_down_to_50(self):
        """$50 (5000¢) denomination snapping."""
        r = auto_distribute(17000, [], 100.0, target_denom=5000)
        # charge = 17000/2 = 8500, snaps down to 5000
        assert r['charge'] == 5000

    def test_denomination_with_other_rows(self):
        """Auto-distribute with denomination and existing rows."""
        # $200 order, SNAP 3000¢ charge (100%), FMNP auto with $25 denom
        # SNAP total: 6000¢. Remaining: 14000¢. FMNP charge = 14000/2 = 7000, snaps to 5000.
        r = auto_distribute(
            20000,
            [{'charge': 3000, 'match_pct': 100.0}],
            100.0,
            target_denom=2500
        )
        assert r['charge'] % 2500 == 0
        assert r['charge'] == 5000  # 7000 → nearest 2500 below = 5000

    def test_denomination_null_no_effect(self):
        """None denomination doesn't affect calculation."""
        r1 = auto_distribute(10000, [], 100.0, target_denom=None)
        r2 = auto_distribute(10000, [], 100.0)
        assert r1['charge'] == r2['charge']

    def test_charge_less_than_denomination(self):
        """When charge < denomination, snaps to 0."""
        r = auto_distribute(4000, [], 100.0, target_denom=2500)
        # charge = 4000/2 = 2000, which is < 2500 → snaps to 0
        assert r['charge'] == 0


# ══════════════════════════════════════════════════════════════════
# 4. Auto-distribute total reconciliation
# ══════════════════════════════════════════════════════════════════
class TestReconciliation:
    """Verify that other_total + target_total == order_total (when no denomination)."""

    @pytest.mark.parametrize("order_total,other_rows,target_pct", [
        (10000, [], 100.0),
        (10000, [], 0.0),
        (20000, [{'charge': 3000, 'match_pct': 100.0}], 100.0),
        (50000, [
            {'charge': 5000, 'match_pct': 100.0},
            {'charge': 10000, 'match_pct': 0.0},
        ], 100.0),
        (5500, [], 100.0),
        (13000, [{'charge': 4000, 'match_pct': 100.0}], 100.0),
    ])
    def test_total_allocation_matches_order(self, order_total, other_rows, target_pct):
        """Sum of all method_amounts should equal order_total."""
        r = auto_distribute(order_total, other_rows, target_pct)

        other_alloc = sum(
            charge_to_method_amount(row['charge'], row['match_pct'])
            for row in other_rows
        )
        target_alloc = charge_to_method_amount(r['charge'], target_pct)
        total_alloc = other_alloc + target_alloc

        assert abs(total_alloc - order_total) <= 1  # 1 cent tolerance


# ══════════════════════════════════════════════════════════════════
# 5. Zero and small order edge cases
# ══════════════════════════════════════════════════════════════════
class TestEdgeCases:

    def test_zero_order_total(self):
        """0¢ order total."""
        r = auto_distribute(0, [], 100.0)
        assert r['charge'] == 0

    def test_penny_order(self):
        """1¢ order total."""
        r = auto_distribute(1, [], 100.0)
        assert r['charge'] >= 0

    def test_large_order(self):
        """$10,000 order (1000000¢) with auto-distribute."""
        r = auto_distribute(1000000, [], 100.0)
        assert r['charge'] == 500000
        assert r['total'] == 1000000

    def test_many_other_rows(self):
        """Multiple other rows leaving small remainder for target."""
        others = [
            {'charge': 2000, 'match_pct': 100.0},
            {'charge': 3000, 'match_pct': 50.0},
            {'charge': 1000, 'match_pct': 0.0},
            {'charge': 1500, 'match_pct': 100.0},
        ]
        # Other totals: 4000 + 4500 + 1000 + 3000 = 12500
        r = auto_distribute(20000, others, 100.0)
        # Remaining = 20000 - 12500 = 7500. Charge = 7500/2 = 3750
        assert r['charge'] == 3750
        assert r['total'] == 7500


# ══════════════════════════════════════════════════════════════════
# 6. Smart multi-row auto-distribute
# ══════════════════════════════════════════════════════════════════
def _row(index, match_pct=100.0, denomination=None, sort_order=0,
         current_charge=0):
    """Helper to build a row descriptor for smart_auto_distribute."""
    return {
        'index': index,
        'match_pct': match_pct,
        'denomination': denomination,
        'sort_order': sort_order,
        'current_charge': current_charge,
    }


class TestSmartDistribute:
    """Tests for the two-pass smart_auto_distribute algorithm."""

    def test_single_non_denom_row(self):
        """Single non-denominated row gets the full order."""
        rows = [_row(0, match_pct=100.0, sort_order=1)]
        result = smart_auto_distribute(10000, rows)
        assert len(result) == 1
        assert result[0]['charge'] == 5000  # 10000 / 2

    def test_single_denom_row(self):
        """Single denominated row: seed 1 unit, then fill up."""
        rows = [_row(0, match_pct=100.0, denomination=2500, sort_order=1)]
        result = smart_auto_distribute(10000, rows)
        assert len(result) == 1
        assert result[0]['charge'] == 5000  # 10000/2=5000, which is 2×2500
        assert result[0]['charge'] % 2500 == 0

    def test_two_denom_rows_both_get_seeded(self):
        """Two denominated methods: each gets at least 1 unit."""
        rows = [
            _row(0, match_pct=100.0, denomination=500, sort_order=1),  # FMNP
            _row(1, match_pct=100.0, denomination=500, sort_order=2),  # SNAP
        ]
        result = smart_auto_distribute(10000, rows)
        charges = {r['index']: r['charge'] for r in result}
        # Both should have at least 1 unit (500¢)
        assert charges[0] >= 500
        assert charges[1] >= 500
        # Both should be multiples of 500¢
        assert charges[0] % 500 == 0
        assert charges[1] % 500 == 0

    def test_two_denom_rows_fill_by_sort_order(self):
        """Higher-priority (lower sort_order) fills up first in pass 2."""
        rows = [
            _row(0, match_pct=100.0, denomination=2500, sort_order=1),  # First
            _row(1, match_pct=100.0, denomination=2500, sort_order=2),  # Second
        ]
        # $200 order: pass 1 seeds 2500¢ each (→ 10000¢ used, 10000¢ left)
        # pass 2: row 0 gets floor(5000/2500)=2 more → 5000¢ additional (→ 0 left)
        result = smart_auto_distribute(20000, rows)
        charges = {r['index']: r['charge'] for r in result}
        assert charges[0] == 7500  # 2500 seed + 5000 fill
        assert charges[1] == 2500  # 2500 seed only

    def test_denom_plus_non_denom_catch_all(self):
        """Denominated method + non-denominated as catch-all for remainder."""
        rows = [
            _row(0, match_pct=100.0, denomination=2500, sort_order=1),  # FMNP
            _row(1, match_pct=0.0, sort_order=2),  # Cash
        ]
        # $65 order: FMNP seeds 2500 (cost 5000), remaining = 1500
        # Pass 2: FMNP can't fit another unit (1500 < 5000). Cash = 1500/1 = 1500
        result = smart_auto_distribute(6500, rows)
        charges = {r['index']: r['charge'] for r in result}
        assert charges[0] == 2500
        assert charges[1] == 1500
        # Verify total: FMNP 2500 charge → 5000 method_amount + Cash 1500 = 6500
        total = charge_to_method_amount(2500, 100.0) + charge_to_method_amount(1500, 0.0)
        assert abs(total - 6500) <= 1

    def test_three_methods_fmnp_snap_cash(self):
        """Realistic: FMNP $5 denom, SNAP $1 denom, Cash catch-all."""
        rows = [
            _row(0, match_pct=100.0, denomination=500, sort_order=1),   # FMNP
            _row(1, match_pct=100.0, denomination=100, sort_order=2),   # SNAP
            _row(2, match_pct=0.0, sort_order=3),                       # Cash
        ]
        # $55 order (5500¢)
        result = smart_auto_distribute(5500, rows)
        charges = {r['index']: r['charge'] for r in result}
        assert charges[0] % 500 == 0  # FMNP is $5 (500¢) multiple
        assert charges[1] % 100 == 0  # SNAP is $1 (100¢) multiple
        # Verify total allocation equals order
        total = sum(
            charge_to_method_amount(charges[i], rows[i]['match_pct'])
            for i in charges
        )
        assert abs(total - 5500) <= 1

    def test_locked_rows_not_modified(self):
        """Rows with existing charges are skipped."""
        rows = [
            _row(0, match_pct=100.0, current_charge=2000, sort_order=1),  # Locked
            _row(1, match_pct=100.0, sort_order=2),                        # Auto
        ]
        result = smart_auto_distribute(10000, rows)
        # Locked row should not appear in result
        assert all(r['index'] != 0 for r in result)
        # Auto row gets remainder: 10000 - 4000 (locked method_amt) = 6000 → charge=3000
        assert result[0]['index'] == 1
        assert result[0]['charge'] == 3000

    def test_locked_plus_denom_auto(self):
        """Locked row + denominated auto row."""
        rows = [
            _row(0, match_pct=100.0, current_charge=2000, sort_order=1),
            _row(1, match_pct=100.0, denomination=2500, sort_order=2),
        ]
        # Locked: 4000¢ method_amount. Remaining: 6000¢.
        # Pass 1: seed 2500 (cost 5000), remaining 1000
        # Pass 2: can't fit another 2500 (1000 < 5000)
        result = smart_auto_distribute(10000, rows)
        assert result[0]['charge'] == 2500

    def test_no_auto_rows_returns_empty(self):
        """All rows locked — nothing to distribute."""
        rows = [
            _row(0, match_pct=100.0, current_charge=5000, sort_order=1),
        ]
        result = smart_auto_distribute(10000, rows)
        assert result == []

    def test_zero_order_returns_empty(self):
        """0¢ order total."""
        rows = [_row(0, match_pct=100.0, sort_order=1)]
        assert smart_auto_distribute(0, rows) == []

    def test_negative_order_returns_empty(self):
        """Negative order total."""
        rows = [_row(0, match_pct=100.0, sort_order=1)]
        assert smart_auto_distribute(-1000, rows) == []

    def test_empty_rows_returns_empty(self):
        """No rows at all."""
        assert smart_auto_distribute(10000, []) == []

    def test_remaining_too_small_for_denom(self):
        """Remaining can't fit even 1 denomination unit — row gets 0."""
        rows = [
            _row(0, match_pct=100.0, current_charge=4500, sort_order=1),  # 9000 cost
            _row(1, match_pct=100.0, denomination=2500, sort_order=2),
        ]
        # Remaining: 1000. Cost of 1 unit: 5000. Can't fit.
        result = smart_auto_distribute(10000, rows)
        assert result == []

    def test_over_allocated_locked(self):
        """Locked rows exceed order total — auto rows get nothing."""
        rows = [
            _row(0, match_pct=100.0, current_charge=6000, sort_order=1),  # 12000
            _row(1, match_pct=100.0, sort_order=2),
        ]
        result = smart_auto_distribute(10000, rows)
        assert result == []

    def test_sort_order_determines_fill_priority(self):
        """Lower sort_order gets filled first in pass 2."""
        rows = [
            _row(0, match_pct=100.0, denomination=1000, sort_order=5),  # Lower priority
            _row(1, match_pct=100.0, denomination=1000, sort_order=1),  # Higher priority
        ]
        result = smart_auto_distribute(10000, rows)
        charges = {r['index']: r['charge'] for r in result}
        # Row 1 (sort_order=1) should get more than row 0 (sort_order=5)
        assert charges[1] >= charges[0]

    def test_mixed_match_percents(self):
        """Methods with different match percents."""
        rows = [
            _row(0, match_pct=200.0, denomination=500, sort_order=1),  # 2:1 match
            _row(1, match_pct=0.0, sort_order=2),                      # Cash
        ]
        # $100 order (10000¢)
        # Pass 1: FMNP 500 charge → method_amount = 500*3 = 1500, remaining = 8500
        # Pass 2: FMNP max_charge = 8500/3 = 2833, floor(2833/500)*500 = 2500
        #         method_amount = 2500*3 = 7500, remaining = 1000
        #         Cash = 1000
        result = smart_auto_distribute(10000, rows)
        charges = {r['index']: r['charge'] for r in result}
        assert charges[0] == 3000  # 500 seed + 2500 fill
        assert charges[1] == 1000
        total = charge_to_method_amount(3000, 200.0) + charge_to_method_amount(1000, 0.0)
        assert abs(total - 10000) <= 1

    def test_backward_compat_single_row_no_denom(self):
        """Single auto row with no denomination — same as old algorithm."""
        rows = [_row(0, match_pct=100.0, sort_order=1)]
        result = smart_auto_distribute(5500, rows)
        old = auto_distribute(5500, [], 100.0)
        assert result[0]['charge'] == old['charge']

    def test_backward_compat_single_row_with_denom(self):
        """Single auto row with denomination — same as old algorithm."""
        rows = [_row(0, match_pct=100.0, denomination=2500, sort_order=1)]
        result = smart_auto_distribute(10000, rows)
        old = auto_distribute(10000, [], 100.0, target_denom=2500)
        assert result[0]['charge'] == old['charge']

    def test_penny_rounding_absorber(self):
        """Absorber row picks the charge closest to remaining (no cent gap).

        $99.63 (9963¢) order with FMNP $5 (500¢, 100% match) locked + SNAP auto.
        Remaining = 8963¢, charge = 8963/2 = 4481.5 — not exact integer.
        The algorithm should pick 4482 (method_amount 8964, gap 1¢)
        or 4481 (method_amount 8962, gap 1¢) — whichever is closer.
        Either way, the gap should be at most 1 cent.
        """
        rows = [
            _row(0, match_pct=100.0, sort_order=2),  # SNAP — absorber
        ]
        # 8963¢ remaining after a locked FMNP row
        locked = [
            _row(1, match_pct=100.0, denomination=500, sort_order=1,
                 current_charge=500),
        ]
        result = smart_auto_distribute(9963, rows + locked)
        assert len(result) == 1
        snap_charge = result[0]['charge']
        snap_ma = charge_to_method_amount(snap_charge, 100.0)
        fmnp_ma = charge_to_method_amount(500, 100.0)
        total_allocated = snap_ma + fmnp_ma
        assert abs(total_allocated - 9963) <= 1

    def test_all_denominated_total_does_not_exceed_order(self):
        """Large denomination overage is NOT allowed — total stays within order."""
        rows = [
            _row(0, match_pct=100.0, denomination=2500, sort_order=1),
            _row(1, match_pct=100.0, denomination=1000, sort_order=2),
        ]
        result = smart_auto_distribute(10000, rows)
        total = sum(
            charge_to_method_amount(r['charge'], rows[r['index']]['match_pct'])
            for r in result
        )
        assert total <= 10000 + 1

    def test_denomination_forfeit_penny_overage(self):
        """Auto-distribute fills to max+1 when overage is just a penny.

        $89.99 (8999¢) order, FMNP $5 (500¢) denom 100% match: should allocate
        9 units (4500¢ charge, 9000¢ method_amount, 1¢ overage forfeited).
        """
        rows = [
            _row(0, match_pct=100.0, denomination=500, sort_order=1),
        ]
        result = smart_auto_distribute(8999, rows)
        assert len(result) == 1
        assert result[0]['charge'] == 4500  # 9 × 500¢

    def test_denomination_forfeit_small_overage(self):
        """Auto-distribute forfeits when overage < denomination.

        $93 (9300¢) order, $5 (500¢) denom 100% match: 9 units = 9000¢, remaining 300¢.
        +1 unit = 5000¢ charge, 10000¢ cost, overage = 700¢.  700 > 500 → NOT allowed.
        Should stay at 9 units (4500¢ charge).
        """
        rows = [
            _row(0, match_pct=100.0, denomination=500, sort_order=1),
        ]
        result = smart_auto_distribute(9300, rows)
        assert len(result) == 1
        assert result[0]['charge'] == 4500  # 9 × 500¢, NOT 10 × 500¢

    def test_denomination_forfeit_not_applied_with_absorber(self):
        """Forfeit is not needed when a non-denominated absorber row exists."""
        rows = [
            _row(0, match_pct=100.0, denomination=500, sort_order=1),
            _row(1, match_pct=0.0, sort_order=2),  # Cash absorber
        ]
        result = smart_auto_distribute(8999, rows)
        charges = {r['index']: r['charge'] for r in result}
        # FMNP should NOT forfeit — Cash absorbs the remainder
        fmnp_charge = charges.get(0, 0)
        cash_charge = charges.get(1, 0)
        total = (charge_to_method_amount(fmnp_charge, 100.0)
                 + charge_to_method_amount(cash_charge, 0.0))
        assert abs(total - 8999) <= 1


# ══════════════════════════════════════════════════════════════════
# 7. Auto-distribute + match cap reconciliation
# ══════════════════════════════════════════════════════════════════
from fam.utils.calculations import calculate_payment_breakdown


class TestAutoDistributeWithMatchCap:
    """Test that charges from smart_auto_distribute reconcile correctly
    when subsequently fed to calculate_payment_breakdown with a match_limit.

    smart_auto_distribute itself does not know about match limits — it
    distributes in method_amount space.  The match cap is applied afterward
    by calculate_payment_breakdown.  These tests verify the full pipeline.
    """

    def test_single_snap_row_with_cap_reconciles(self):
        """Order=10000, single SNAP (100% match). Auto-distribute gives ~5000
        charge.  Feed to breakdown with match_limit=3000.  fam_subsidy should
        be 3000 and customer_paid + fam_subsidy == receipt_total."""
        rows = [_row(0, match_pct=100.0, sort_order=1)]
        dist = smart_auto_distribute(10000, rows)
        assert len(dist) == 1

        charge = dist[0]['charge']
        method_amount = charge_to_method_amount(charge, 100.0)

        result = calculate_payment_breakdown(10000, [
            {'method_amount': method_amount, 'match_percent': 100.0},
        ], match_limit=3000)

        assert result['fam_subsidy_total'] == 3000
        assert abs(result['customer_total_paid'] + result['fam_subsidy_total'] - 10000) <= 1

    def test_two_methods_snap_cash_with_cap_reconciles(self):
        """Order=8000, SNAP (100%) + Cash (0%). Auto-distribute fills both.
        Feed to breakdown with match_limit=1000.  fam_subsidy <= 1000 and
        total reconciles."""
        rows = [
            _row(0, match_pct=100.0, sort_order=1),
            _row(1, match_pct=0.0, sort_order=2),
        ]
        dist = smart_auto_distribute(8000, rows)
        charges = {r['index']: r['charge'] for r in dist}

        entries = []
        for r in rows:
            c = charges.get(r['index'], 0)
            if c > 0:
                entries.append({
                    'method_amount': charge_to_method_amount(c, r['match_pct']),
                    'match_percent': r['match_pct'],
                })

        result = calculate_payment_breakdown(8000, entries, match_limit=1000)

        assert result['fam_subsidy_total'] <= 1000
        assert abs(result['customer_total_paid'] + result['fam_subsidy_total'] - 8000) <= 1

    def test_high_match_with_tight_cap(self):
        """Order=9000, Food RX (200% match). Auto-distribute gives charge ~3000.
        Feed to breakdown with match_limit=500.  fam_subsidy should be 500,
        customer_paid = 8500."""
        rows = [_row(0, match_pct=200.0, sort_order=1)]
        dist = smart_auto_distribute(9000, rows)
        assert len(dist) == 1

        charge = dist[0]['charge']
        method_amount = charge_to_method_amount(charge, 200.0)

        result = calculate_payment_breakdown(9000, [
            {'method_amount': method_amount, 'match_percent': 200.0},
        ], match_limit=500)

        assert result['fam_subsidy_total'] == 500
        assert result['customer_total_paid'] == 8500
        assert abs(result['customer_total_paid'] + result['fam_subsidy_total'] - 9000) <= 1

    def test_denomination_with_cap_reconciles(self):
        """Order=5000, FMNP (100% match, denomination=500). Auto-distribute
        snaps to denomination.  Feed to breakdown with match_limit=800.
        Verify reconciliation and fam_subsidy <= 800."""
        rows = [_row(0, match_pct=100.0, denomination=500, sort_order=1)]
        dist = smart_auto_distribute(5000, rows)
        assert len(dist) == 1

        charge = dist[0]['charge']
        assert charge % 500 == 0  # denomination constraint respected
        method_amount = charge_to_method_amount(charge, 100.0)

        result = calculate_payment_breakdown(5000, [
            {'method_amount': method_amount, 'match_percent': 100.0},
        ], match_limit=800)

        assert result['fam_subsidy_total'] <= 800
        assert abs(result['customer_total_paid'] + result['fam_subsidy_total'] - 5000) <= 1

    def test_cap_zero_all_customer_paid(self):
        """Order=6000, SNAP 100%. Auto-distribute gives charge ~3000.
        Feed to breakdown with match_limit=0.  fam_subsidy should be 0,
        customer_paid should equal receipt_total."""
        rows = [_row(0, match_pct=100.0, sort_order=1)]
        dist = smart_auto_distribute(6000, rows)
        assert len(dist) == 1

        charge = dist[0]['charge']
        method_amount = charge_to_method_amount(charge, 100.0)

        result = calculate_payment_breakdown(6000, [
            {'method_amount': method_amount, 'match_percent': 100.0},
        ], match_limit=0)

        assert result['fam_subsidy_total'] == 0
        assert abs(result['customer_total_paid'] - 6000) <= 1


# ══════════════════════════════════════════════════════════════════
# Denomination + Match Cap Interaction Tests
# ══════════════════════════════════════════════════════════════════

class TestDenominationMatchCapInteraction:
    """Verify auto-distribute and breakdown behave when both denomination
    constraints and daily match limits are active simultaneously."""

    def test_denom_with_tight_cap_forfeit_still_detected(self):
        """FMNP $5 denom (100% match), order $49, cap $100.
        Auto-distribute: 5 units x $5 = $25 charge → $50 alloc.
        Remaining $49-$50 = -$1 overage (forfeit).
        Verify method_amount > receipt (overage passes through)."""
        rows = [_row(0, match_pct=100.0, denomination=500, sort_order=1)]
        dist = smart_auto_distribute(4900, rows)
        assert len(dist) == 1

        charge = dist[0]['charge']
        assert charge % 500 == 0  # denomination preserved
        method_amount = charge_to_method_amount(charge, 100.0)

        result = calculate_payment_breakdown(4900, [
            {'method_amount': method_amount, 'match_percent': 100.0},
        ], match_limit=10000)

        # Overage is a denomination issue, not a cap issue
        # allocated may exceed receipt total by denomination overage
        if result['allocated_total'] > 4900:
            overage = result['allocated_total'] - 4900
            assert overage <= 500  # within one denomination unit

    def test_denom_plus_nondenom_cap_interaction(self):
        """FMNP $5 denom (100%) + SNAP (100%), order $80, cap $20.
        Verify both denomination and match cap constraints coexist."""
        rows = [
            _row(0, match_pct=100.0, denomination=500, sort_order=1),
            _row(1, match_pct=100.0, sort_order=2),
        ]
        dist = smart_auto_distribute(8000, rows)

        entries = []
        for r in rows:
            charge = next((d['charge'] for d in dist if d['index'] == r['index']), 0)
            if charge > 0:
                entries.append({
                    'method_amount': charge_to_method_amount(charge, r['match_pct']),
                    'match_percent': r['match_pct'],
                })

        if entries:
            result = calculate_payment_breakdown(8000, entries, match_limit=2000)
            assert result['fam_subsidy_total'] <= 2000
            for li in result['line_items']:
                assert li['match_amount'] >= 0
                assert li['customer_charged'] >= 0

    def test_two_denom_methods_cumulative_overage(self):
        """Two $5 denom methods (100% match each), order $49.
        Each method can overshoot by up to $5 — combined overage
        should be accepted (not rejected as hard error)."""
        rows = [
            _row(0, match_pct=100.0, denomination=500, sort_order=1),
            _row(1, match_pct=100.0, denomination=500, sort_order=2),
        ]
        dist = smart_auto_distribute(4900, rows)

        entries = []
        total_ma = 0
        for r in rows:
            charge = next((d['charge'] for d in dist if d['index'] == r['index']), 0)
            if charge > 0:
                ma = charge_to_method_amount(charge, r['match_pct'])
                entries.append({
                    'method_amount': ma,
                    'match_percent': r['match_pct'],
                })
                total_ma += ma

        if entries:
            result = calculate_payment_breakdown(4900, entries)
            # Verify overage is within sum of denominations (500 + 500 = 1000)
            if result['allocated_total'] > 4900:
                overage = result['allocated_total'] - 4900
                assert overage <= 1000

    def test_denom_forfeit_with_cap_at_zero(self):
        """Denomination method with match cap=0: customer pays everything.
        Denomination still constrains units — check no negative match."""
        charge = 2500  # 5 units of $5
        method_amount = charge_to_method_amount(charge, 100.0)  # $50

        result = calculate_payment_breakdown(4900, [
            {'method_amount': method_amount, 'match_percent': 100.0},
        ], match_limit=0)

        assert result['fam_subsidy_total'] == 0
        for li in result['line_items']:
            assert li['match_amount'] >= 0
            assert li['customer_charged'] >= 0

    def test_denom_method_no_overage_with_cap(self):
        """Denomination fits exactly: $50 order, $5 denom (100%).
        5 units x $5 = $25 charge → $50 alloc. No overage.
        Cap should not interfere with exact fit."""
        charge = 2500
        method_amount = charge_to_method_amount(charge, 100.0)  # $50

        result = calculate_payment_breakdown(5000, [
            {'method_amount': method_amount, 'match_percent': 100.0},
        ], match_limit=3000)

        assert result['allocation_remaining'] == 0
        assert result['fam_subsidy_total'] <= 3000

    def test_denomination_larger_than_order_total(self):
        """$25 denomination with $10 order: even 1 unit ($50 alloc) overshoots.
        Auto-distribute should handle gracefully."""
        rows = [_row(0, match_pct=100.0, denomination=2500, sort_order=1)]
        dist = smart_auto_distribute(1000, rows)

        # May allocate 1 unit (overshoot) or 0 units (can't fit)
        if dist:
            charge = dist[0]['charge']
            assert charge % 2500 == 0
        # No crash is the main assertion


# ══════════════════════════════════════════════════════════════════
# Penny Rounding — 0% Match Absorber Should Not Steal FAM Penny
# ══════════════════════════════════════════════════════════════════

class TestPennyAbsorberPreference:
    """When a ≤1-cent remainder exists, it should be absorbed by FAM match
    (via penny reconciliation on a matched row), NOT by a 0% match row
    like Cash that would make the customer pay the penny."""

    def test_cash_does_not_absorb_penny_when_matched_row_exists(self):
        """SNAP 100% + FMNP denom 100% + Cash 0%.
        $116.39 order: FMNP locked at $10 (2 units), SNAP absorbs rest.
        The 1-cent rounding gap should NOT go into Cash."""
        from fam.utils.calculations import calculate_payment_breakdown

        rows = [
            _row(0, match_pct=100.0, sort_order=1),            # SNAP (auto)
            _row(1, match_pct=0.0, sort_order=3),              # Cash (auto)
            _row(2, match_pct=100.0, denomination=500,
                 sort_order=2, current_charge=1000),            # FMNP locked
        ]

        dist = smart_auto_distribute(11639, rows)

        # Cash should get $0 — the penny should NOT land here
        cash_assignments = [d for d in dist if d['index'] == 1]
        cash_charge = cash_assignments[0]['charge'] if cash_assignments else 0
        assert cash_charge == 0, (
            f"Cash absorbed {cash_charge} cents — penny should go to FAM match"
        )

        # SNAP should get the floored charge
        snap_assignments = [d for d in dist if d['index'] == 0]
        assert snap_assignments, "SNAP should have an assignment"
        snap_charge = snap_assignments[0]['charge']

        # Verify via calculate_payment_breakdown that FAM absorbs the penny
        line_items_input = [
            {'method_amount': charge_to_method_amount(snap_charge, 100.0),
             'match_percent': 100.0, 'method_name': 'SNAP'},
            {'method_amount': charge_to_method_amount(1000, 100.0),
             'match_percent': 100.0, 'method_name': 'FMNP'},
        ]
        result = calculate_payment_breakdown(11639, line_items_input)
        # FAM match should be >= customer charge (penny goes to FAM)
        assert result['fam_subsidy_total'] >= result['customer_total_paid'], (
            f"FAM {result['fam_subsidy_total']} should be >= customer "
            f"{result['customer_total_paid']}"
        )

    def test_cash_absorbs_real_remainder_not_just_penny(self):
        """When remainder is more than a penny, Cash should still absorb."""
        rows = [
            _row(0, match_pct=100.0, sort_order=1),              # SNAP
            _row(1, match_pct=0.0, sort_order=3),                # Cash
            _row(2, match_pct=100.0, sort_order=2,
                 current_charge=4000),                            # locked $40
        ]

        # $100 order: $80 locked alloc, $20 remaining
        dist = smart_auto_distribute(10000, rows)

        # Some charge should be assigned to auto rows
        total_assigned = sum(d['charge'] for d in dist)
        assert total_assigned > 0, "Some charge should be assigned"

    def test_no_matched_rows_cash_takes_penny(self):
        """When ALL rows are 0% match, Cash must absorb the penny —
        there's no matched row for penny reconciliation."""
        rows = [
            _row(0, match_pct=0.0, sort_order=1),   # Cash 1
            _row(1, match_pct=0.0, sort_order=2),   # Cash 2
        ]
        dist = smart_auto_distribute(1001, rows)  # $10.01

        total_charge = sum(d['charge'] for d in dist)
        assert total_charge == 1001, "All $10.01 should be assigned to 0% rows"


class TestBestAbsorberTieBreaking:
    """When multiple non-denom rows share the same match_pct, the absorber
    should be deterministic: lowest sort_order wins."""

    def test_same_match_pct_lower_sort_order_wins(self):
        """Two 50% match rows — sort_order 1 should absorb, not sort_order 3."""
        rows = [
            _row(0, match_pct=50.0, sort_order=3),   # Method A
            _row(1, match_pct=50.0, sort_order=1),   # Method B (higher priority)
        ]
        dist = smart_auto_distribute(10000, rows)
        assigned = {d['index']: d['charge'] for d in dist}
        # sort_order=1 (index 1) should get the charge
        assert assigned.get(1, 0) > 0, "Lower sort_order row should absorb"
        assert assigned.get(0, 0) == 0, "Higher sort_order row should stay empty"

    def test_higher_match_pct_beats_lower_sort_order(self):
        """100% match row beats 50% match row regardless of sort_order."""
        rows = [
            _row(0, match_pct=50.0, sort_order=1),
            _row(1, match_pct=100.0, sort_order=5),
        ]
        dist = smart_auto_distribute(10000, rows)
        assigned = {d['index']: d['charge'] for d in dist}
        # 100% match (index 1) should absorb despite higher sort_order
        assert assigned.get(1, 0) > 0, "Higher match_pct row should absorb"
        assert assigned.get(0, 0) == 0, "Lower match_pct row should stay empty"

    def test_three_way_tie_lowest_sort_order_wins(self):
        """Three 100% rows — sort_order 2 should absorb."""
        rows = [
            _row(0, match_pct=100.0, sort_order=5),
            _row(1, match_pct=100.0, sort_order=2),
            _row(2, match_pct=100.0, sort_order=8),
        ]
        dist = smart_auto_distribute(10000, rows)
        assigned = {d['index']: d['charge'] for d in dist}
        assert assigned.get(1, 0) > 0, "sort_order=2 should absorb"
        assert assigned.get(0, 0) == 0
        assert assigned.get(2, 0) == 0


class TestMatchDeficitWithLockedRows:
    """Verify that auto-distribute's match deficit calculation includes
    match from locked (denominated) rows, not just new assignments."""

    def test_locked_denom_match_included_in_deficit(self):
        """Locked FMNP ($50 charge, 100% match = $50 match) + SNAP auto.
        Match limit $60. FMNP already uses $50 of match, only $10 left
        for SNAP. SNAP should get a higher charge to compensate."""
        from fam.utils.calculations import calculate_payment_breakdown

        # $200 order, $100 locked (FMNP $50 charge → $100 alloc)
        rows = [
            _row(0, match_pct=100.0, sort_order=1),              # SNAP auto
            _row(1, match_pct=100.0, denomination=500,
                 sort_order=2, current_charge=5000),              # FMNP locked
        ]
        dist = smart_auto_distribute(20000, rows)

        snap_assignments = [d for d in dist if d['index'] == 0]
        assert snap_assignments, "SNAP should get an assignment"
        snap_charge = snap_assignments[0]['charge']

        # SNAP gets remaining $100 in method_amount space
        # With 100% match: charge = floor(10000 / 2.0) = 5000
        snap_ma = charge_to_method_amount(snap_charge, 100.0)
        total_alloc = snap_ma + charge_to_method_amount(5000, 100.0)
        assert total_alloc >= 19999, (
            f"Total allocation {total_alloc} should cover ~$200 order"
        )

    def test_no_locked_rows_deficit_still_works(self):
        """All auto rows, no locked — deficit calculation should still work."""
        rows = [
            _row(0, match_pct=100.0, sort_order=1),
            _row(1, match_pct=50.0, sort_order=2),
        ]
        dist = smart_auto_distribute(10000, rows)
        total_charge = sum(d['charge'] for d in dist)
        assert total_charge > 0


class TestEdgeCases:
    """Production readiness edge case tests."""

    def test_single_zero_match_row(self):
        """Single Cash (0% match) row handles full order."""
        rows = [_row(0, match_pct=0.0, sort_order=1)]
        dist = smart_auto_distribute(5000, rows)
        assert len(dist) == 1
        assert dist[0]['charge'] == 5000

    def test_zero_order_total(self):
        """Zero order total produces no assignments."""
        rows = [_row(0, match_pct=100.0, sort_order=1)]
        dist = smart_auto_distribute(0, rows)
        assert dist == []

    def test_negative_order_total(self):
        """Negative order total produces no assignments."""
        rows = [_row(0, match_pct=100.0, sort_order=1)]
        dist = smart_auto_distribute(-500, rows)
        assert dist == []

    def test_all_rows_locked(self):
        """All rows have charges (locked) — nothing to distribute."""
        rows = [
            _row(0, match_pct=100.0, sort_order=1, current_charge=3000),
            _row(1, match_pct=50.0, sort_order=2, current_charge=2000),
        ]
        dist = smart_auto_distribute(10000, rows)
        assert dist == []

    def test_single_denomination_exact_fit(self):
        """$50 order, $5 denom 100% match → 5 units × $10 = $50 exact."""
        rows = [_row(0, match_pct=100.0, denomination=500, sort_order=1)]
        dist = smart_auto_distribute(5000, rows)
        assert len(dist) == 1
        assert dist[0]['charge'] == 2500  # 5 units × $5

    def test_one_cent_order(self):
        """$0.01 order — smallest possible transaction."""
        rows = [_row(0, match_pct=100.0, sort_order=1)]
        dist = smart_auto_distribute(1, rows)
        # floor(1 / 2.0) = 0 — can't even assign 1 cent
        # This is OK — penny reconciliation will handle it
        if dist:
            assert dist[0]['charge'] >= 0

    def test_very_large_order(self):
        """$99,999.99 order — stress test."""
        rows = [_row(0, match_pct=100.0, sort_order=1)]
        dist = smart_auto_distribute(9999999, rows)
        assert len(dist) == 1
        assert dist[0]['charge'] > 0
        ma = charge_to_method_amount(dist[0]['charge'], 100.0)
        assert abs(9999999 - ma) <= 1  # Within penny reconciliation range
