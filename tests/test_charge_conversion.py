"""Tests for charge ↔ method_amount conversion helpers.

Validates round-trip accuracy at various match percentages,
boundary conditions, and penny precision.
"""

import pytest
from fam.utils.calculations import charge_to_method_amount, method_amount_to_charge


# ══════════════════════════════════════════════════════════════════
# 1. charge_to_method_amount spot checks
# ══════════════════════════════════════════════════════════════════
class TestChargeToMethodAmount:

    def test_0_percent(self):
        """0% match: charge == method_amount (no FAM subsidy)."""
        assert charge_to_method_amount(50.0, 0.0) == 50.0

    def test_50_percent(self):
        """50% match: $50 charge → $75 total allocation."""
        assert charge_to_method_amount(50.0, 50.0) == 75.0

    def test_100_percent(self):
        """100% match (1:1): $50 charge → $100 total allocation."""
        assert charge_to_method_amount(50.0, 100.0) == 100.0

    def test_200_percent(self):
        """200% match (2:1): $50 charge → $150 total allocation."""
        assert charge_to_method_amount(50.0, 200.0) == 150.0

    def test_zero_charge(self):
        """$0 charge → $0 regardless of match percent."""
        assert charge_to_method_amount(0.0, 100.0) == 0.0

    def test_penny_charge(self):
        """$0.01 charge at 100% → $0.02."""
        assert charge_to_method_amount(0.01, 100.0) == 0.02

    def test_large_charge(self):
        """Large charge stays accurate."""
        assert charge_to_method_amount(5000.0, 100.0) == 10000.0

    def test_fractional_match(self):
        """Non-integer match percent (33.33%)."""
        result = charge_to_method_amount(100.0, 33.33)
        assert result == round(100.0 * (1 + 33.33 / 100.0), 2)

    def test_odd_amount_100_percent(self):
        """Odd charge at 100%: $27.50 → $55.00."""
        assert charge_to_method_amount(27.50, 100.0) == 55.0


# ══════════════════════════════════════════════════════════════════
# 2. method_amount_to_charge spot checks
# ══════════════════════════════════════════════════════════════════
class TestMethodAmountToCharge:

    def test_0_percent(self):
        """0% match: method_amount == charge."""
        assert method_amount_to_charge(50.0, 0.0) == 50.0

    def test_50_percent(self):
        """50% match: $75 allocation → $50 charge."""
        assert method_amount_to_charge(75.0, 50.0) == 50.0

    def test_100_percent(self):
        """100% match: $100 allocation → $50 charge."""
        assert method_amount_to_charge(100.0, 100.0) == 50.0

    def test_200_percent(self):
        """200% match: $150 allocation → $50 charge."""
        assert method_amount_to_charge(150.0, 200.0) == 50.0

    def test_zero_amount(self):
        """$0 allocation → $0 charge."""
        assert method_amount_to_charge(0.0, 100.0) == 0.0

    def test_penny_amount(self):
        """$0.02 allocation at 100% → $0.01."""
        assert method_amount_to_charge(0.02, 100.0) == 0.01


# ══════════════════════════════════════════════════════════════════
# 3. Round-trip: charge → method_amount → charge
# ══════════════════════════════════════════════════════════════════
class TestRoundTrip:
    """Converting charge→method_amount→charge should return the original value."""

    @pytest.mark.parametrize("charge,pct", [
        (0.0, 100.0),
        (0.01, 100.0),
        (1.00, 0.0),
        (1.00, 50.0),
        (1.00, 100.0),
        (1.00, 200.0),
        (10.00, 100.0),
        (25.00, 100.0),
        (27.50, 100.0),
        (50.00, 100.0),
        (50.00, 75.0),
        (99.99, 100.0),
        (100.0, 33.0),
        (100.0, 100.0),
        (100.0, 200.0),
        (500.0, 100.0),
        (1000.0, 100.0),
        (5000.0, 50.0),
    ])
    def test_charge_round_trip(self, charge, pct):
        method_amount = charge_to_method_amount(charge, pct)
        recovered = method_amount_to_charge(method_amount, pct)
        assert recovered == charge

    @pytest.mark.parametrize("method_amount,pct", [
        (0.0, 100.0),
        (0.02, 100.0),
        (2.00, 0.0),
        (2.00, 100.0),
        (55.00, 100.0),
        (100.0, 100.0),
        (100.0, 50.0),
        (100.0, 200.0),
        (200.0, 100.0),
        (1000.0, 100.0),
    ])
    def test_method_amount_round_trip(self, method_amount, pct):
        charge = method_amount_to_charge(method_amount, pct)
        recovered = charge_to_method_amount(charge, pct)
        # Allow $0.02 tolerance — double rounding (two round() calls)
        # can accumulate up to ~1.5 cents of drift
        assert abs(recovered - method_amount) <= 0.02


# ══════════════════════════════════════════════════════════════════
# 4. Consistency with calculate_payment_breakdown
# ══════════════════════════════════════════════════════════════════
class TestConsistencyWithBreakdown:
    """Verify that charge_to_method_amount produces values consistent
    with what calculate_payment_breakdown expects."""

    def test_charge_based_entry_matches_breakdown(self):
        """Entering a charge of $50 at 100% should produce a method_amount
        that, when fed to calculate_payment_breakdown, yields
        customer_charged == $50."""
        from fam.utils.calculations import calculate_payment_breakdown

        charge = 50.0
        pct = 100.0
        method_amount = charge_to_method_amount(charge, pct)

        result = calculate_payment_breakdown(method_amount, [
            {'method_amount': method_amount, 'match_percent': pct}
        ])
        assert result['is_valid'] is True
        li = result['line_items'][0]
        assert li['customer_charged'] == charge
        assert li['match_amount'] == charge  # 1:1 match

    @pytest.mark.parametrize("charge,pct", [
        (10.0, 50.0),
        (25.0, 100.0),
        (40.0, 200.0),
        (100.0, 0.0),
        (65.0, 100.0),
    ])
    def test_charge_recovery_via_breakdown(self, charge, pct):
        """For any charge/pct pair, the breakdown should recover the
        original charge as customer_charged."""
        from fam.utils.calculations import calculate_payment_breakdown

        method_amount = charge_to_method_amount(charge, pct)
        result = calculate_payment_breakdown(method_amount, [
            {'method_amount': method_amount, 'match_percent': pct}
        ])
        li = result['line_items'][0]
        assert abs(li['customer_charged'] - charge) <= 0.01


# ══════════════════════════════════════════════════════════════════
# 5. Edge cases
# ══════════════════════════════════════════════════════════════════
class TestEdgeCases:

    def test_very_high_match_percent(self):
        """999% match: charge $10 → method_amount $109.90."""
        result = charge_to_method_amount(10.0, 999.0)
        assert result == round(10.0 * (1 + 999 / 100.0), 2)

    def test_reverse_very_high_match(self):
        """999% match reverse: method_amount → charge."""
        ma = charge_to_method_amount(10.0, 999.0)
        charge = method_amount_to_charge(ma, 999.0)
        assert charge == 10.0

    def test_result_always_rounded_to_cents(self):
        """Output should always be rounded to 2 decimal places."""
        result = charge_to_method_amount(33.33, 33.33)
        # Verify it's a clean 2 decimal result
        assert result == round(result, 2)

        result2 = method_amount_to_charge(77.77, 77.0)
        assert result2 == round(result2, 2)
