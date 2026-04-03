"""Tests for charge ↔ method_amount conversion helpers.

All monetary values are integer cents (e.g. $50.00 = 5000).
Validates round-trip accuracy at various match percentages,
boundary conditions, and cent precision.
"""

import pytest
from fam.utils.calculations import charge_to_method_amount, method_amount_to_charge


# ══════════════════════════════════════════════════════════════════
# 1. charge_to_method_amount spot checks
# ══════════════════════════════════════════════════════════════════
class TestChargeToMethodAmount:

    def test_0_percent(self):
        """0% match: charge == method_amount (no FAM subsidy)."""
        assert charge_to_method_amount(5000, 0.0) == 5000

    def test_50_percent(self):
        """50% match: 5000¢ charge → 7500¢ total allocation."""
        assert charge_to_method_amount(5000, 50.0) == 7500

    def test_100_percent(self):
        """100% match (1:1): 5000¢ charge → 10000¢ total allocation."""
        assert charge_to_method_amount(5000, 100.0) == 10000

    def test_200_percent(self):
        """200% match (2:1): 5000¢ charge → 15000¢ total allocation."""
        assert charge_to_method_amount(5000, 200.0) == 15000

    def test_zero_charge(self):
        """0¢ charge → 0¢ regardless of match percent."""
        assert charge_to_method_amount(0, 100.0) == 0

    def test_penny_charge(self):
        """1¢ charge at 100% → 2¢."""
        assert charge_to_method_amount(1, 100.0) == 2

    def test_large_charge(self):
        """Large charge stays accurate."""
        assert charge_to_method_amount(500000, 100.0) == 1000000

    def test_fractional_match(self):
        """Non-integer match percent (33.33%)."""
        result = charge_to_method_amount(10000, 33.33)
        assert result == round(10000 * (1 + 33.33 / 100.0))

    def test_odd_amount_100_percent(self):
        """Odd charge at 100%: 2750¢ → 5500¢."""
        assert charge_to_method_amount(2750, 100.0) == 5500


# ══════════════════════════════════════════════════════════════════
# 2. method_amount_to_charge spot checks
# ══════════════════════════════════════════════════════════════════
class TestMethodAmountToCharge:

    def test_0_percent(self):
        """0% match: method_amount == charge."""
        assert method_amount_to_charge(5000, 0.0) == 5000

    def test_50_percent(self):
        """50% match: 7500¢ allocation → 5000¢ charge."""
        assert method_amount_to_charge(7500, 50.0) == 5000

    def test_100_percent(self):
        """100% match: 10000¢ allocation → 5000¢ charge."""
        assert method_amount_to_charge(10000, 100.0) == 5000

    def test_200_percent(self):
        """200% match: 15000¢ allocation → 5000¢ charge."""
        assert method_amount_to_charge(15000, 200.0) == 5000

    def test_zero_amount(self):
        """0¢ allocation → 0¢ charge."""
        assert method_amount_to_charge(0, 100.0) == 0

    def test_penny_amount(self):
        """2¢ allocation at 100% → 1¢."""
        assert method_amount_to_charge(2, 100.0) == 1


# ══════════════════════════════════════════════════════════════════
# 3. Round-trip: charge → method_amount → charge
# ══════════════════════════════════════════════════════════════════
class TestRoundTrip:
    """Converting charge→method_amount→charge should return the original value."""

    @pytest.mark.parametrize("charge,pct", [
        (0, 100.0),
        (1, 100.0),
        (100, 0.0),
        (100, 50.0),
        (100, 100.0),
        (100, 200.0),
        (1000, 100.0),
        (2500, 100.0),
        (2750, 100.0),
        (5000, 100.0),
        (5000, 75.0),
        (9999, 100.0),
        (10000, 33.0),
        (10000, 100.0),
        (10000, 200.0),
        (50000, 100.0),
        (100000, 100.0),
        (500000, 50.0),
    ])
    def test_charge_round_trip(self, charge, pct):
        method_amount = charge_to_method_amount(charge, pct)
        recovered = method_amount_to_charge(method_amount, pct)
        assert recovered == charge

    @pytest.mark.parametrize("method_amount,pct", [
        (0, 100.0),
        (2, 100.0),
        (200, 0.0),
        (200, 100.0),
        (5500, 100.0),
        (10000, 100.0),
        (10000, 50.0),
        (10000, 200.0),
        (20000, 100.0),
        (100000, 100.0),
    ])
    def test_method_amount_round_trip(self, method_amount, pct):
        charge = method_amount_to_charge(method_amount, pct)
        recovered = charge_to_method_amount(charge, pct)
        # Allow 2¢ tolerance — double rounding (two round() calls)
        # can accumulate up to ~1.5 cents of drift
        assert abs(recovered - method_amount) <= 2


# ══════════════════════════════════════════════════════════════════
# 4. Consistency with calculate_payment_breakdown
# ══════════════════════════════════════════════════════════════════
class TestConsistencyWithBreakdown:
    """Verify that charge_to_method_amount produces values consistent
    with what calculate_payment_breakdown expects."""

    def test_charge_based_entry_matches_breakdown(self):
        """Entering a charge of 5000¢ at 100% should produce a method_amount
        that, when fed to calculate_payment_breakdown, yields
        customer_charged == 5000¢."""
        from fam.utils.calculations import calculate_payment_breakdown

        charge = 5000
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
        (1000, 50.0),
        (2500, 100.0),
        (4000, 200.0),
        (10000, 0.0),
        (6500, 100.0),
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
        assert abs(li['customer_charged'] - charge) <= 1


# ══════════════════════════════════════════════════════════════════
# 5. Edge cases
# ══════════════════════════════════════════════════════════════════
class TestEdgeCases:

    def test_very_high_match_percent(self):
        """999% match: 1000¢ charge → 10990¢ method_amount."""
        result = charge_to_method_amount(1000, 999.0)
        assert result == round(1000 * (1 + 999 / 100.0))

    def test_reverse_very_high_match(self):
        """999% match reverse: method_amount → charge."""
        ma = charge_to_method_amount(1000, 999.0)
        charge = method_amount_to_charge(ma, 999.0)
        assert charge == 1000

    def test_result_is_integer(self):
        """Output should always be an integer (cents)."""
        result = charge_to_method_amount(3333, 33.33)
        assert isinstance(result, int)

        result2 = method_amount_to_charge(7777, 77.0)
        assert isinstance(result2, int)
