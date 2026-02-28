"""Comprehensive tests for the match formula: amount x pct / (100 + pct).

This file validates the core math across a wide range of real-world
scenarios, boundary conditions, and penny-rounding edge cases to ensure
the 1:1 match semantics are correct everywhere.

Key identity:
    match_amount + customer_charged == method_amount   (always)
    customer_total_paid + fam_subsidy_total == receipt_total  (when fully allocated)
"""

import pytest
from fam.utils.calculations import calculate_payment_breakdown


# ──────────────────────────────────────────────────────────────────
# Helper: run a single-method breakdown and return a flat dict
# ──────────────────────────────────────────────────────────────────
def _simple(receipt, pct, limit=None):
    """Shortcut for a single payment method covering the full receipt."""
    result = calculate_payment_breakdown(receipt, [
        {'method_amount': receipt, 'match_percent': pct},
    ], match_limit=limit)
    li = result['line_items'][0]
    return {
        'match': li['match_amount'],
        'customer': li['customer_charged'],
        'fam_total': result['fam_subsidy_total'],
        'cust_total': result['customer_total_paid'],
        'valid': result['is_valid'],
        'capped': result['match_was_capped'],
        'uncapped': result.get('uncapped_fam_subsidy_total', 0),
    }


# ══════════════════════════════════════════════════════════════════
# 1. Core formula spot-checks
# ══════════════════════════════════════════════════════════════════
class TestCoreFormula:
    """Verify the formula match = amount * pct / (100 + pct) for key percentages."""

    def test_100pct_is_1_to_1(self):
        """100% match means 1:1 — FAM and customer split evenly."""
        r = _simple(100.0, 100.0)
        assert r['match'] == 50.0
        assert r['customer'] == 50.0

    def test_100pct_on_odd_amount(self):
        """$99 at 100%: match = $49.50, customer = $49.50."""
        r = _simple(99.0, 100.0)
        assert r['match'] == 49.50
        assert r['customer'] == 49.50

    def test_50pct_match(self):
        """50% match: FAM pays 1/3, customer pays 2/3."""
        r = _simple(150.0, 50.0)
        # 150 * 50/150 = 50.0
        assert r['match'] == 50.0
        assert r['customer'] == 100.0

    def test_200pct_is_2_to_1(self):
        """200% match means 2:1 — FAM pays 2/3, customer pays 1/3."""
        r = _simple(300.0, 200.0)
        # 300 * 200/300 = 200.0
        assert r['match'] == 200.0
        assert r['customer'] == 100.0

    def test_25pct_match(self):
        """25% match: 100 * 25/125 = $20."""
        r = _simple(100.0, 25.0)
        assert r['match'] == 20.0
        assert r['customer'] == 80.0

    def test_75pct_match(self):
        """75% match: 100 * 75/175 = $42.86."""
        r = _simple(100.0, 75.0)
        assert r['match'] == 42.86
        assert r['customer'] == 57.14

    def test_10pct_match(self):
        """10% match: 100 * 10/110 = $9.09."""
        r = _simple(100.0, 10.0)
        assert r['match'] == 9.09
        assert r['customer'] == 90.91

    def test_500pct_match(self):
        """500% match (5:1): 100 * 500/600 = $83.33."""
        r = _simple(100.0, 500.0)
        assert r['match'] == 83.33
        assert r['customer'] == 16.67


# ══════════════════════════════════════════════════════════════════
# 2. The golden rule: match + customer == method_amount always
# ══════════════════════════════════════════════════════════════════
class TestReconciliation:
    """match_amount + customer_charged must always equal method_amount."""

    @pytest.mark.parametrize("receipt,pct", [
        (100.0, 0.0),
        (100.0, 10.0),
        (100.0, 25.0),
        (100.0, 33.0),
        (100.0, 50.0),
        (100.0, 75.0),
        (100.0, 100.0),
        (100.0, 150.0),
        (100.0, 200.0),
        (100.0, 300.0),
        (100.0, 999.0),
        (1.00, 100.0),
        (0.01, 100.0),
        (9999.99, 100.0),
        (73.47, 67.0),
        (123.45, 123.0),
    ])
    def test_line_item_reconciles(self, receipt, pct):
        r = _simple(receipt, pct)
        assert r['match'] + r['customer'] == receipt
        assert r['valid'] is True

    @pytest.mark.parametrize("receipt,pct", [
        (100.0, 0.0),
        (100.0, 50.0),
        (100.0, 100.0),
        (100.0, 200.0),
        (100.0, 300.0),
        (50.0, 100.0),
        (1.00, 100.0),
        (9999.99, 100.0),
    ])
    def test_totals_reconcile(self, receipt, pct):
        """fam_total + cust_total == receipt."""
        r = _simple(receipt, pct)
        assert abs((r['fam_total'] + r['cust_total']) - receipt) <= 0.01

    def test_multi_method_reconciles(self):
        """Multiple payment methods still sum to receipt total."""
        result = calculate_payment_breakdown(500.0, [
            {'method_amount': 200.0, 'match_percent': 100.0},
            {'method_amount': 150.0, 'match_percent': 50.0},
            {'method_amount': 150.0, 'match_percent': 0.0},
        ])
        assert result['is_valid'] is True
        total = result['customer_total_paid'] + result['fam_subsidy_total']
        assert abs(total - 500.0) <= 0.01

        # Each line item reconciles individually
        for li in result['line_items']:
            assert li['match_amount'] + li['customer_charged'] == li['method_amount']

    def test_multi_method_with_cap_reconciles(self):
        """Multiple methods + cap: totals still reconcile."""
        result = calculate_payment_breakdown(400.0, [
            {'method_amount': 200.0, 'match_percent': 100.0},
            {'method_amount': 200.0, 'match_percent': 50.0},
        ], match_limit=80.0)
        assert result['is_valid'] is True
        assert result['match_was_capped'] is True
        total = result['customer_total_paid'] + result['fam_subsidy_total']
        assert abs(total - 400.0) <= 0.01

        for li in result['line_items']:
            assert li['customer_charged'] == round(li['method_amount'] - li['match_amount'], 2)


# ══════════════════════════════════════════════════════════════════
# 3. Customer always pays a non-negative amount
# ══════════════════════════════════════════════════════════════════
class TestCustomerNeverNegative:
    """With the new formula, customer_charged >= 0 for all valid inputs."""

    @pytest.mark.parametrize("pct", [0, 10, 50, 100, 150, 200, 300, 500, 999])
    def test_customer_charged_non_negative(self, pct):
        r = _simple(100.0, float(pct))
        assert r['customer'] >= 0.0

    @pytest.mark.parametrize("pct", [100, 200, 300, 500, 999])
    def test_match_never_exceeds_receipt(self, pct):
        """FAM match is always less than the receipt total."""
        r = _simple(100.0, float(pct))
        assert r['match'] < 100.0
        assert r['match'] > 0.0


# ══════════════════════════════════════════════════════════════════
# 4. Boundary conditions and zero-amount edge cases
# ══════════════════════════════════════════════════════════════════
class TestBoundaryConditions:

    def test_zero_receipt(self):
        """$0 receipt: everything is zero."""
        r = _simple(0.0, 100.0)
        assert r['match'] == 0.0
        assert r['customer'] == 0.0

    def test_zero_percent(self):
        """0% match: no FAM subsidy."""
        r = _simple(100.0, 0.0)
        assert r['match'] == 0.0
        assert r['customer'] == 100.0

    def test_penny_receipt(self):
        """$0.01 receipt at 100%: each side gets a penny or zero."""
        r = _simple(0.01, 100.0)
        # 0.01 * 100/200 = 0.005 rounds to 0.01
        assert r['match'] == 0.01
        assert r['customer'] == 0.0
        # This is the one case where customer = 0 at 100% due to rounding

    def test_two_cent_receipt(self):
        """$0.02 receipt at 100%: perfectly splits."""
        r = _simple(0.02, 100.0)
        assert r['match'] == 0.01
        assert r['customer'] == 0.01

    def test_large_receipt(self):
        """$10,000 order at 100%: clean 50/50 split."""
        r = _simple(10000.0, 100.0)
        assert r['match'] == 5000.0
        assert r['customer'] == 5000.0

    def test_very_high_match_percent(self):
        """999% match: FAM pays 999/1099 ≈ 90.9%."""
        r = _simple(100.0, 999.0)
        assert r['match'] == 90.9  # round(100 * 999/1099, 2) = 90.9
        assert r['customer'] == 9.1
        assert r['match'] + r['customer'] == 100.0

    def test_fractional_match_percent(self):
        """Non-integer match percent (e.g. 33.33%)."""
        r = _simple(100.0, 33.33)
        expected_match = round(100.0 * 33.33 / 133.33, 2)
        assert r['match'] == expected_match
        assert r['match'] + r['customer'] == 100.0

    def test_empty_payment_entries(self):
        """No payment entries: early return with is_valid False."""
        result = calculate_payment_breakdown(100.0, [])
        assert result['is_valid'] is False
        assert result['fam_subsidy_total'] == 0.0
        assert len(result['errors']) > 0

    def test_negative_receipt_flagged(self):
        """Negative receipt total creates an error."""
        result = calculate_payment_breakdown(-50.0, [
            {'method_amount': -50.0, 'match_percent': 100.0},
        ])
        assert len(result['errors']) > 0


# ══════════════════════════════════════════════════════════════════
# 5. Match limit (cap) edge cases
# ══════════════════════════════════════════════════════════════════
class TestCapEdgeCases:

    def test_cap_exactly_at_computed_match(self):
        """When cap == computed match, no capping occurs (not strictly greater)."""
        # $100 at 100% → match = $50.00
        r = _simple(100.0, 100.0, limit=50.0)
        assert r['match'] == 50.0
        assert r['capped'] is False

    def test_cap_one_penny_below_match(self):
        """Cap at $49.99 when match would be $50.00 → capped."""
        r = _simple(100.0, 100.0, limit=49.99)
        assert r['fam_total'] == 49.99
        assert r['capped'] is True

    def test_cap_zero(self):
        """Cap of $0 blocks all match."""
        r = _simple(100.0, 100.0, limit=0.0)
        assert r['fam_total'] == 0.0
        assert r['cust_total'] == 100.0
        assert r['capped'] is True

    def test_cap_preserves_uncapped_total(self):
        """Uncapped total always reflects the pre-cap value."""
        r = _simple(200.0, 100.0, limit=25.0)
        assert r['uncapped'] == 100.0   # 200 * 100/200 = 100
        assert r['fam_total'] == 25.0

    def test_three_methods_capped_proportionally(self):
        """Three payment methods capped proportionally."""
        result = calculate_payment_breakdown(300.0, [
            {'method_amount': 100.0, 'match_percent': 100.0},   # match = 50
            {'method_amount': 100.0, 'match_percent': 50.0},    # match = 33.33
            {'method_amount': 100.0, 'match_percent': 200.0},   # match = 66.67
        ], match_limit=60.0)
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 60.0
        assert result['is_valid'] is True

        # Each line's customer_charged is correct
        for li in result['line_items']:
            assert li['customer_charged'] == round(li['method_amount'] - li['match_amount'], 2)

        # Total reconciles
        total = result['customer_total_paid'] + result['fam_subsidy_total']
        assert abs(total - 300.0) <= 0.01


# ══════════════════════════════════════════════════════════════════
# 6. Real-world farmer's market scenarios
# ══════════════════════════════════════════════════════════════════
class TestRealWorldScenarios:

    def test_snap_100pct_match_small_purchase(self):
        """Customer buys $12 of produce with SNAP (100% match).
        Customer pays $6, FAM pays $6."""
        r = _simple(12.0, 100.0)
        assert r['match'] == 6.0
        assert r['customer'] == 6.0

    def test_snap_100pct_match_large_purchase(self):
        """Customer buys $87.50 of produce with SNAP (100% match).
        Customer pays $43.75, FAM pays $43.75."""
        r = _simple(87.50, 100.0)
        assert r['match'] == 43.75
        assert r['customer'] == 43.75

    def test_mixed_snap_and_cash(self):
        """$50 total: $30 via SNAP (100% match), $20 via cash (0% match).
        SNAP: customer $15, FAM $15. Cash: customer $20. Total collect: $35."""
        result = calculate_payment_breakdown(50.0, [
            {'method_amount': 30.0, 'match_percent': 100.0},
            {'method_amount': 20.0, 'match_percent': 0.0},
        ])
        assert result['is_valid'] is True
        items = result['line_items']

        # SNAP line
        assert items[0]['match_amount'] == 15.0
        assert items[0]['customer_charged'] == 15.0

        # Cash line
        assert items[1]['match_amount'] == 0.0
        assert items[1]['customer_charged'] == 20.0

        assert result['fam_subsidy_total'] == 15.0
        assert result['customer_total_paid'] == 35.0

    def test_daily_cap_scenario(self):
        """Customer with $20 daily limit remaining buys $50 at 100% match.
        Uncapped match = $25, capped to $20. Customer pays $30."""
        r = _simple(50.0, 100.0, limit=20.0)
        assert r['fam_total'] == 20.0
        assert r['cust_total'] == 30.0
        assert r['capped'] is True

    def test_returning_customer_exhausted_limit(self):
        """Returning customer with $0 remaining — no FAM benefit at all."""
        r = _simple(40.0, 100.0, limit=0.0)
        assert r['fam_total'] == 0.0
        assert r['cust_total'] == 40.0

    def test_food_bucks_and_snap_with_cap(self):
        """$100 split: $60 Food Bucks (100%), $40 SNAP (100%), cap $30.
        Uncapped: 30 + 20 = $50, capped to $30.
        Ratio = 30/50 = 0.6
        Food Bucks match: 30 * 0.6 = $18, SNAP match: 20 * 0.6 = $12.
        """
        result = calculate_payment_breakdown(100.0, [
            {'method_amount': 60.0, 'match_percent': 100.0},
            {'method_amount': 40.0, 'match_percent': 100.0},
        ], match_limit=30.0)
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 30.0
        assert result['customer_total_paid'] == 70.0
        assert result['is_valid'] is True

        items = result['line_items']
        assert items[0]['match_amount'] == 18.0
        assert items[1]['match_amount'] == 12.0
