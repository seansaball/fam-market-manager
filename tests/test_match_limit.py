"""Tests for the daily match limit (cap) in calculate_payment_breakdown().

Match formula: match_amount = method_amount × match_percent / (100 + match_percent)
  100% match = 1:1 → FAM pays half, customer pays half
  200% match = 2:1 → FAM pays 2/3, customer pays 1/3
   50% match → FAM pays 1/3, customer pays 2/3
"""

import pytest
from fam.utils.calculations import calculate_payment_breakdown


class TestNoMatchLimit:
    """When match_limit is None (default), no capping occurs."""

    def test_full_match_applied(self):
        """100% match (1:1): $200 order → $100 FAM, $100 customer."""
        result = calculate_payment_breakdown(200.0, [
            {'method_amount': 200.0, 'match_percent': 100.0},
        ])
        assert result['fam_subsidy_total'] == 100.0
        assert result['customer_total_paid'] == 100.0
        assert result['match_was_capped'] is False
        assert result['is_valid'] is True

    def test_partial_match(self):
        """50% match: $100 order → $33.33 FAM, $66.67 customer."""
        result = calculate_payment_breakdown(100.0, [
            {'method_amount': 100.0, 'match_percent': 50.0},
        ])
        assert result['fam_subsidy_total'] == 33.33
        assert result['customer_total_paid'] == 66.67
        assert result['match_was_capped'] is False

    def test_no_match(self):
        result = calculate_payment_breakdown(100.0, [
            {'method_amount': 100.0, 'match_percent': 0.0},
        ])
        assert result['fam_subsidy_total'] == 0.0
        assert result['customer_total_paid'] == 100.0
        assert result['match_was_capped'] is False


class TestMatchLimitAboveTotal:
    """When the limit is higher than the total match, no capping occurs."""

    def test_limit_above_total(self):
        """50% match on $100 → $33.33 match, well under $200 limit."""
        result = calculate_payment_breakdown(100.0, [
            {'method_amount': 100.0, 'match_percent': 50.0},
        ], match_limit=200.0)
        assert result['fam_subsidy_total'] == 33.33
        assert result['customer_total_paid'] == 66.67
        assert result['match_was_capped'] is False
        assert result['is_valid'] is True

    def test_limit_exactly_equals_total(self):
        """50% match on $100 → $33.33 match, limit set to exactly $33.33."""
        result = calculate_payment_breakdown(100.0, [
            {'method_amount': 100.0, 'match_percent': 50.0},
        ], match_limit=33.33)
        assert result['fam_subsidy_total'] == 33.33
        assert result['match_was_capped'] is False


class TestMatchLimitCapping:
    """When total match exceeds the limit, cap is applied."""

    def test_single_method_capped(self):
        """$300 at 100% match → $150 match capped to $100.

        100% match on $300: match = 300 × 100/200 = $150, capped to $100.
        """
        result = calculate_payment_breakdown(300.0, [
            {'method_amount': 300.0, 'match_percent': 100.0},
        ], match_limit=100.0)
        assert result['fam_subsidy_total'] == 100.0
        assert result['customer_total_paid'] == 200.0
        assert result['match_was_capped'] is True
        assert result['uncapped_fam_subsidy_total'] == 150.0
        assert result['is_valid'] is True

    def test_half_match_capped(self):
        """$400 at 50% match → $133.33 match capped to $100.

        50% match on $400: match = 400 × 50/150 = $133.33, capped to $100.
        """
        result = calculate_payment_breakdown(400.0, [
            {'method_amount': 400.0, 'match_percent': 50.0},
        ], match_limit=100.0)
        assert result['fam_subsidy_total'] == 100.0
        assert result['customer_total_paid'] == 300.0
        assert result['match_was_capped'] is True
        assert result['uncapped_fam_subsidy_total'] == 133.33
        assert result['is_valid'] is True

    def test_multi_method_proportional_cap(self):
        """Multiple methods: cap is applied proportionally.

        $100 SNAP (50%) → match = 100 × 50/150 = $33.33
        $100 Food Bucks (100%) → match = 100 × 100/200 = $50.00
        Total uncapped = $83.33, cap at $50
        Ratio = 50/83.33 ≈ 0.60
        SNAP match = $33.33 × 0.60 = $20.00
        Food Bucks match = $50.00 × 0.60 = $30.00
        Total capped = $50.00
        """
        result = calculate_payment_breakdown(200.0, [
            {'method_amount': 100.0, 'match_percent': 50.0},
            {'method_amount': 100.0, 'match_percent': 100.0},
        ], match_limit=50.0)
        assert result['match_was_capped'] is True
        assert result['uncapped_fam_subsidy_total'] == 83.33
        assert result['fam_subsidy_total'] == 50.0
        assert result['customer_total_paid'] == 150.0
        assert result['is_valid'] is True

        # Check proportional reduction
        items = result['line_items']
        assert items[0]['match_amount'] == 20.0   # SNAP: 33.33 × 0.60
        assert items[1]['match_amount'] == 30.0    # Food Bucks: 50 × 0.60

    def test_small_limit(self):
        """Very small limit: $0.01."""
        result = calculate_payment_breakdown(100.0, [
            {'method_amount': 100.0, 'match_percent': 100.0},
        ], match_limit=0.01)
        assert result['fam_subsidy_total'] == 0.01
        assert result['customer_total_paid'] == 99.99
        assert result['match_was_capped'] is True

    def test_customer_charged_correctness(self):
        """Verify each line item's customer_charged = method_amount - match_amount."""
        result = calculate_payment_breakdown(300.0, [
            {'method_amount': 100.0, 'match_percent': 50.0},
            {'method_amount': 200.0, 'match_percent': 75.0},
        ], match_limit=50.0)
        for li in result['line_items']:
            assert li['customer_charged'] == round(li['method_amount'] - li['match_amount'], 2)
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 50.0


class TestMatchLimitEdgeCases:
    """Edge cases for the match limit feature."""

    def test_zero_match_with_limit(self):
        """No match methods + limit -> no capping needed."""
        result = calculate_payment_breakdown(100.0, [
            {'method_amount': 100.0, 'match_percent': 0.0},
        ], match_limit=100.0)
        assert result['fam_subsidy_total'] == 0.0
        assert result['match_was_capped'] is False

    def test_none_limit_is_no_cap(self):
        """Explicitly passing None means no cap. 100% match = 1:1 → half."""
        result = calculate_payment_breakdown(100.0, [
            {'method_amount': 100.0, 'match_percent': 100.0},
        ], match_limit=None)
        assert result['fam_subsidy_total'] == 50.0
        assert result['match_was_capped'] is False

    def test_uncapped_total_preserved(self):
        """uncapped_fam_subsidy_total always shows the original before cap.

        $500 at 100% → uncapped match = $250, capped to $100.
        """
        result = calculate_payment_breakdown(500.0, [
            {'method_amount': 500.0, 'match_percent': 100.0},
        ], match_limit=100.0)
        assert result['uncapped_fam_subsidy_total'] == 250.0
        assert result['fam_subsidy_total'] == 100.0

    def test_reconciliation_after_cap(self):
        """customer_total_paid + fam_subsidy_total should still equal receipt_total."""
        result = calculate_payment_breakdown(250.0, [
            {'method_amount': 250.0, 'match_percent': 80.0},
        ], match_limit=100.0)
        assert result['is_valid'] is True
        total = result['customer_total_paid'] + result['fam_subsidy_total']
        assert abs(total - 250.0) <= 0.01


class TestHighMatchPercent:
    """Tests for match percentages above 100% (e.g. 2x, 3x matching).

    With the formula match_amount = amount × pct / (100 + pct),
    customer_charged is always positive — no negative values.
    """

    def test_200_percent_match(self):
        """200% match (2:1): FAM pays 2/3, customer pays 1/3.

        $50 at 200%: match = 50 × 200/300 = $33.33, customer = $16.67.
        """
        result = calculate_payment_breakdown(50.0, [
            {'method_amount': 50.0, 'match_percent': 200.0},
        ])
        assert result['fam_subsidy_total'] == 33.33
        assert result['customer_total_paid'] == 16.67
        assert result['is_valid'] is True
        assert result['match_was_capped'] is False

        li = result['line_items'][0]
        assert li['match_amount'] == 33.33
        assert li['customer_charged'] == 16.67

    def test_150_percent_match_with_cap(self):
        """150% match capped: match is reduced to the cap.

        $100 at 150%: match = 100 × 150/250 = $60, capped to $50.
        """
        result = calculate_payment_breakdown(100.0, [
            {'method_amount': 100.0, 'match_percent': 150.0},
        ], match_limit=50.0)
        assert result['uncapped_fam_subsidy_total'] == 60.0
        assert result['fam_subsidy_total'] == 50.0
        assert result['customer_total_paid'] == 50.0
        assert result['match_was_capped'] is True
        assert result['is_valid'] is True

    def test_300_percent_match(self):
        """300% match (3:1): FAM pays 3/4, customer pays 1/4.

        $25 at 300%: match = 25 × 300/400 = $18.75, customer = $6.25.
        """
        result = calculate_payment_breakdown(25.0, [
            {'method_amount': 25.0, 'match_percent': 300.0},
        ])
        assert result['fam_subsidy_total'] == 18.75
        assert result['customer_total_paid'] == 6.25
        assert result['is_valid'] is True

    def test_mixed_high_low_match(self):
        """Mix of 200% and 50% match methods.

        $100 at 200%: match = 100 × 200/300 = $66.67
        $100 at  50%: match = 100 ×  50/150 = $33.33
        Total match = $100.00, customer pays $100.00.
        """
        result = calculate_payment_breakdown(200.0, [
            {'method_amount': 100.0, 'match_percent': 200.0},
            {'method_amount': 100.0, 'match_percent': 50.0},
        ])
        assert result['fam_subsidy_total'] == 100.0
        assert result['customer_total_paid'] == 100.0
        assert result['is_valid'] is True

        items = result['line_items']
        assert items[0]['match_amount'] == 66.67
        assert items[1]['match_amount'] == 33.33
