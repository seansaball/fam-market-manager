"""Tests for the auto-distribute algorithm.

Since the auto-distribute logic lives in payment_screen.py (a UI method),
we replicate the core math here to validate it without the Qt dependency.
This mirrors the same pattern used in test_match_formula.py for multi-
transaction distribution.
"""

import pytest
from fam.utils.calculations import charge_to_method_amount, method_amount_to_charge


# ──────────────────────────────────────────────────────────────────
# Helper: replicate auto-distribute algorithm (pure math, no Qt)
# ──────────────────────────────────────────────────────────────────
def auto_distribute(order_total, other_rows, target_match_pct, target_denom=None):
    """Replicate _auto_distribute() math from payment_screen.py.

    Args:
        order_total: The full order total (receipt total).
        other_rows: List of dicts with 'charge' and 'match_pct' for non-target rows.
        target_match_pct: Match percent of the target (last) row.
        target_denom: Optional denomination constraint for the target row.

    Returns:
        Dict with 'charge', 'match', 'total', 'remaining' for the target row.
    """
    # Sum method_amount from other rows
    other_total = 0.0
    for row in other_rows:
        ma = charge_to_method_amount(row['charge'], row['match_pct'])
        other_total += ma

    remaining = round(order_total - other_total, 2)

    if remaining <= 0:
        return {'charge': 0.0, 'match': 0.0, 'total': 0.0, 'remaining': remaining}

    # charge = remaining / (1 + match_pct/100)
    charge = round(remaining / (1.0 + target_match_pct / 100.0), 2)

    # Denomination: round DOWN to nearest multiple
    if target_denom and target_denom > 0 and charge > 0:
        charge = round(int(charge / target_denom) * target_denom, 2)

    match_amt = round(charge * (target_match_pct / 100.0), 2)
    total = round(charge + match_amt, 2)

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
        Auto-distribute: charge = 55/(1+1) = $27.50, match = $27.50, total = $55."""
        r = auto_distribute(55.0, [], 100.0)
        assert r['charge'] == 27.50
        assert r['match'] == 27.50
        assert r['total'] == 55.0

    def test_medium_fmnp_plus_snap(self):
        """$130 basket. FMNP: $40 charge (100% match) = $80 total.
        SNAP auto-distributes remaining $50: charge = $25, match = $25."""
        r = auto_distribute(130.0, [{'charge': 40.0, 'match_pct': 100.0}], 100.0)
        assert r['charge'] == 25.0
        assert r['match'] == 25.0
        assert r['total'] == 50.0

    def test_nightmare_three_methods_snap_last(self):
        """$270 basket. Food RX $10 charge (100%), FMNP $50 charge (100%),
        Vet Bucks $10 charge (100%) = $140 allocated.
        SNAP remaining $130: charge = $65, match = $65, total = $130."""
        other = [
            {'charge': 10.0, 'match_pct': 100.0},   # Food RX: $20 total
            {'charge': 50.0, 'match_pct': 100.0},   # FMNP: $100 total
            {'charge': 10.0, 'match_pct': 100.0},   # Vet Bucks: $20 total
        ]
        r = auto_distribute(270.0, other, 100.0)
        assert r['charge'] == 65.0
        assert r['match'] == 65.0
        assert r['total'] == 130.0

    def test_nightmare_with_cash_added(self):
        """Same $270, but after adding $50 cash (0% match), SNAP recalculates.
        Other total: Food RX $20 + FMNP $100 + Vet Bucks $20 + Cash $50 = $190.
        SNAP remaining $80: charge = $40, match = $40."""
        other = [
            {'charge': 10.0, 'match_pct': 100.0},   # Food RX: $20 total
            {'charge': 50.0, 'match_pct': 100.0},   # FMNP: $100 total
            {'charge': 10.0, 'match_pct': 100.0},   # Vet Bucks: $20 total
            {'charge': 50.0, 'match_pct': 0.0},     # Cash: $50 total
        ]
        r = auto_distribute(270.0, other, 100.0)
        assert r['charge'] == 40.0
        assert r['match'] == 40.0
        assert r['total'] == 80.0


# ══════════════════════════════════════════════════════════════════
# 2. Basic auto-distribute scenarios
# ══════════════════════════════════════════════════════════════════
class TestBasicAutoDistribute:

    def test_single_method_covers_all(self):
        """Only one method, covers entire order."""
        r = auto_distribute(100.0, [], 100.0)
        assert r['charge'] == 50.0
        assert r['total'] == 100.0

    def test_single_method_0_percent(self):
        """0% match: charge == order total (no FAM subsidy)."""
        r = auto_distribute(100.0, [], 0.0)
        assert r['charge'] == 100.0
        assert r['match'] == 0.0
        assert r['total'] == 100.0

    def test_single_method_200_percent(self):
        """200% match: charge = 100/3 = $33.33."""
        r = auto_distribute(100.0, [], 200.0)
        assert r['charge'] == 33.33
        assert r['total'] == round(33.33 + 33.33 * 2, 2)  # 33.33 + 66.66 = 99.99

    def test_two_methods_50_percent(self):
        """$100 order: first method $30 charge at 50%, second method auto at 100%.
        First total: 30 * 1.5 = $45. Remaining: $55. Charge = 55/2 = $27.50."""
        r = auto_distribute(100.0, [{'charge': 30.0, 'match_pct': 50.0}], 100.0)
        assert r['charge'] == 27.50
        assert r['total'] == 55.0

    def test_exact_allocation_no_remaining(self):
        """Other rows already fully cover the order — target gets $0."""
        r = auto_distribute(100.0, [{'charge': 50.0, 'match_pct': 100.0}], 100.0)
        assert r['charge'] == 0.0
        assert r['total'] == 0.0

    def test_over_allocated(self):
        """Other rows exceed order total — target gets $0."""
        r = auto_distribute(
            50.0,
            [{'charge': 50.0, 'match_pct': 100.0}],  # $100 total
            100.0
        )
        assert r['charge'] == 0.0


# ══════════════════════════════════════════════════════════════════
# 3. Denomination snapping
# ══════════════════════════════════════════════════════════════════
class TestDenominationSnapping:

    def test_snaps_down_to_25(self):
        """Charge $27.50 with $25 denomination → snaps down to $25.00."""
        r = auto_distribute(55.0, [], 100.0, target_denom=25.0)
        # charge = 55/2 = 27.50, snaps to 25
        assert r['charge'] == 25.0

    def test_exact_denomination_multiple(self):
        """Charge is already a multiple of denomination → unchanged."""
        r = auto_distribute(100.0, [], 100.0, target_denom=25.0)
        # charge = 100/2 = 50, which is 2×25 → stays at 50
        assert r['charge'] == 50.0

    def test_snaps_down_to_50(self):
        """$50 denomination snapping."""
        r = auto_distribute(170.0, [], 100.0, target_denom=50.0)
        # charge = 170/2 = 85, snaps down to 50
        assert r['charge'] == 50.0

    def test_denomination_with_other_rows(self):
        """Auto-distribute with denomination and existing rows."""
        # $200 order, SNAP $30 charge (100%), FMNP auto-distributes with $25 denom
        # SNAP total: $60. Remaining: $140. FMNP charge = 140/2 = $70, snaps to $50.
        r = auto_distribute(
            200.0,
            [{'charge': 30.0, 'match_pct': 100.0}],
            100.0,
            target_denom=25.0
        )
        assert r['charge'] % 25 == 0
        assert r['charge'] == 50.0  # 70 → nearest 25 below = 50

    def test_denomination_null_no_effect(self):
        """None denomination doesn't affect calculation."""
        r1 = auto_distribute(100.0, [], 100.0, target_denom=None)
        r2 = auto_distribute(100.0, [], 100.0)
        assert r1['charge'] == r2['charge']

    def test_charge_less_than_denomination(self):
        """When charge < denomination, snaps to 0."""
        r = auto_distribute(40.0, [], 100.0, target_denom=25.0)
        # charge = 40/2 = 20, which is < 25 → snaps to 0
        assert r['charge'] == 0.0


# ══════════════════════════════════════════════════════════════════
# 4. Auto-distribute total reconciliation
# ══════════════════════════════════════════════════════════════════
class TestReconciliation:
    """Verify that other_total + target_total == order_total (when no denomination)."""

    @pytest.mark.parametrize("order_total,other_rows,target_pct", [
        (100.0, [], 100.0),
        (100.0, [], 0.0),
        (200.0, [{'charge': 30.0, 'match_pct': 100.0}], 100.0),
        (500.0, [
            {'charge': 50.0, 'match_pct': 100.0},
            {'charge': 100.0, 'match_pct': 0.0},
        ], 100.0),
        (55.0, [], 100.0),
        (130.0, [{'charge': 40.0, 'match_pct': 100.0}], 100.0),
    ])
    def test_total_allocation_matches_order(self, order_total, other_rows, target_pct):
        """Sum of all method_amounts should equal order_total."""
        r = auto_distribute(order_total, other_rows, target_pct)

        other_alloc = sum(
            charge_to_method_amount(row['charge'], row['match_pct'])
            for row in other_rows
        )
        target_alloc = charge_to_method_amount(r['charge'], target_pct)
        total_alloc = round(other_alloc + target_alloc, 2)

        assert abs(total_alloc - order_total) <= 0.01


# ══════════════════════════════════════════════════════════════════
# 5. Zero and small order edge cases
# ══════════════════════════════════════════════════════════════════
class TestEdgeCases:

    def test_zero_order_total(self):
        """$0 order total."""
        r = auto_distribute(0.0, [], 100.0)
        assert r['charge'] == 0.0

    def test_penny_order(self):
        """$0.01 order total."""
        r = auto_distribute(0.01, [], 100.0)
        assert r['charge'] == 0.01  # 0.01 / 2 = 0.005 → rounds to 0.01
        assert r['charge'] >= 0

    def test_large_order(self):
        """$10,000 order with auto-distribute."""
        r = auto_distribute(10000.0, [], 100.0)
        assert r['charge'] == 5000.0
        assert r['total'] == 10000.0

    def test_many_other_rows(self):
        """Multiple other rows leaving small remainder for target."""
        others = [
            {'charge': 20.0, 'match_pct': 100.0},
            {'charge': 30.0, 'match_pct': 50.0},
            {'charge': 10.0, 'match_pct': 0.0},
            {'charge': 15.0, 'match_pct': 100.0},
        ]
        # Other totals: 40 + 45 + 10 + 30 = 125
        r = auto_distribute(200.0, others, 100.0)
        # Remaining = 200 - 125 = 75. Charge = 75/2 = 37.50
        assert r['charge'] == 37.50
        assert r['total'] == 75.0
