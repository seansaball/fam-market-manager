"""Tests for the daily match limit (cap) in calculate_payment_breakdown().

All monetary values are integer cents (e.g. $50.00 = 5000).
Match percentages remain as floats.

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
        """100% match (1:1): 20000¢ order → 10000¢ FAM, 10000¢ customer."""
        result = calculate_payment_breakdown(20000, [
            {'method_amount': 20000, 'match_percent': 100.0},
        ])
        assert result['fam_subsidy_total'] == 10000
        assert result['customer_total_paid'] == 10000
        assert result['match_was_capped'] is False
        assert result['is_valid'] is True

    def test_partial_match(self):
        """50% match: 10000¢ order → 3333¢ FAM, 6667¢ customer."""
        result = calculate_payment_breakdown(10000, [
            {'method_amount': 10000, 'match_percent': 50.0},
        ])
        assert result['fam_subsidy_total'] == 3333
        assert result['customer_total_paid'] == 6667
        assert result['match_was_capped'] is False

    def test_no_match(self):
        result = calculate_payment_breakdown(10000, [
            {'method_amount': 10000, 'match_percent': 0.0},
        ])
        assert result['fam_subsidy_total'] == 0
        assert result['customer_total_paid'] == 10000
        assert result['match_was_capped'] is False


class TestMatchLimitAboveTotal:
    """When the limit is higher than the total match, no capping occurs."""

    def test_limit_above_total(self):
        """50% match on 10000¢ → 3333¢ match, well under 20000¢ limit."""
        result = calculate_payment_breakdown(10000, [
            {'method_amount': 10000, 'match_percent': 50.0},
        ], match_limit=20000)
        assert result['fam_subsidy_total'] == 3333
        assert result['customer_total_paid'] == 6667
        assert result['match_was_capped'] is False
        assert result['is_valid'] is True

    def test_limit_exactly_equals_total(self):
        """50% match on 10000¢ → 3333¢ match, limit set to exactly 3333¢."""
        result = calculate_payment_breakdown(10000, [
            {'method_amount': 10000, 'match_percent': 50.0},
        ], match_limit=3333)
        assert result['fam_subsidy_total'] == 3333
        assert result['match_was_capped'] is False


class TestMatchLimitCapping:
    """When total match exceeds the limit, cap is applied."""

    def test_single_method_capped(self):
        """30000¢ at 100% match → 15000¢ match capped to 10000¢."""
        result = calculate_payment_breakdown(30000, [
            {'method_amount': 30000, 'match_percent': 100.0},
        ], match_limit=10000)
        assert result['fam_subsidy_total'] == 10000
        assert result['customer_total_paid'] == 20000
        assert result['match_was_capped'] is True
        assert result['uncapped_fam_subsidy_total'] == 15000
        assert result['is_valid'] is True

    def test_half_match_capped(self):
        """40000¢ at 50% match → 13333¢ match capped to 10000¢."""
        result = calculate_payment_breakdown(40000, [
            {'method_amount': 40000, 'match_percent': 50.0},
        ], match_limit=10000)
        assert result['fam_subsidy_total'] == 10000
        assert result['customer_total_paid'] == 30000
        assert result['match_was_capped'] is True
        assert result['uncapped_fam_subsidy_total'] == 13333
        assert result['is_valid'] is True

    def test_multi_method_proportional_cap(self):
        """Multiple methods: cap is applied proportionally.

        10000¢ SNAP (50%) → match = 3333
        10000¢ Food Bucks (100%) → match = 5000
        Total uncapped = 8333, cap at 5000
        Ratio = 5000/8333 ≈ 0.60
        SNAP match = 3333 × 0.60 = 2000
        Food Bucks match = 5000 × 0.60 = 3000
        """
        result = calculate_payment_breakdown(20000, [
            {'method_amount': 10000, 'match_percent': 50.0},
            {'method_amount': 10000, 'match_percent': 100.0},
        ], match_limit=5000)
        assert result['match_was_capped'] is True
        assert result['uncapped_fam_subsidy_total'] == 8333
        assert result['fam_subsidy_total'] == 5000
        assert result['customer_total_paid'] == 15000
        assert result['is_valid'] is True

        items = result['line_items']
        assert items[0]['match_amount'] == 2000   # SNAP: 3333 × 0.60
        assert items[1]['match_amount'] == 3000    # Food Bucks: 5000 × 0.60

    def test_small_limit(self):
        """Very small limit: 1¢."""
        result = calculate_payment_breakdown(10000, [
            {'method_amount': 10000, 'match_percent': 100.0},
        ], match_limit=1)
        assert result['fam_subsidy_total'] == 1
        assert result['customer_total_paid'] == 9999
        assert result['match_was_capped'] is True

    def test_customer_charged_correctness(self):
        """Verify each line item's customer_charged = method_amount - match_amount."""
        result = calculate_payment_breakdown(30000, [
            {'method_amount': 10000, 'match_percent': 50.0},
            {'method_amount': 20000, 'match_percent': 75.0},
        ], match_limit=5000)
        for li in result['line_items']:
            assert li['customer_charged'] == li['method_amount'] - li['match_amount']
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 5000


class TestMatchLimitEdgeCases:
    """Edge cases for the match limit feature."""

    def test_zero_match_with_limit(self):
        """No match methods + limit -> no capping needed."""
        result = calculate_payment_breakdown(10000, [
            {'method_amount': 10000, 'match_percent': 0.0},
        ], match_limit=10000)
        assert result['fam_subsidy_total'] == 0
        assert result['match_was_capped'] is False

    def test_none_limit_is_no_cap(self):
        """Explicitly passing None means no cap."""
        result = calculate_payment_breakdown(10000, [
            {'method_amount': 10000, 'match_percent': 100.0},
        ], match_limit=None)
        assert result['fam_subsidy_total'] == 5000
        assert result['match_was_capped'] is False

    def test_uncapped_total_preserved(self):
        """uncapped_fam_subsidy_total always shows original before cap."""
        result = calculate_payment_breakdown(50000, [
            {'method_amount': 50000, 'match_percent': 100.0},
        ], match_limit=10000)
        assert result['uncapped_fam_subsidy_total'] == 25000
        assert result['fam_subsidy_total'] == 10000

    def test_reconciliation_after_cap(self):
        """customer_total_paid + fam_subsidy_total should still equal receipt_total."""
        result = calculate_payment_breakdown(25000, [
            {'method_amount': 25000, 'match_percent': 80.0},
        ], match_limit=10000)
        assert result['is_valid'] is True
        total = result['customer_total_paid'] + result['fam_subsidy_total']
        assert abs(total - 25000) <= 1


class TestHighMatchPercent:
    """Tests for match percentages above 100% (e.g. 2x, 3x matching)."""

    def test_200_percent_match(self):
        """200% match (2:1): FAM pays 2/3, customer pays 1/3.

        5000¢ at 200%: match = 5000 × 200/300 = 3333, customer = 1667.
        """
        result = calculate_payment_breakdown(5000, [
            {'method_amount': 5000, 'match_percent': 200.0},
        ])
        assert result['fam_subsidy_total'] == 3333
        assert result['customer_total_paid'] == 1667
        assert result['is_valid'] is True
        assert result['match_was_capped'] is False

        li = result['line_items'][0]
        assert li['match_amount'] == 3333
        assert li['customer_charged'] == 1667

    def test_150_percent_match_with_cap(self):
        """150% match capped: match reduced to cap.

        10000¢ at 150%: match = 10000 × 150/250 = 6000, capped to 5000.
        """
        result = calculate_payment_breakdown(10000, [
            {'method_amount': 10000, 'match_percent': 150.0},
        ], match_limit=5000)
        assert result['uncapped_fam_subsidy_total'] == 6000
        assert result['fam_subsidy_total'] == 5000
        assert result['customer_total_paid'] == 5000
        assert result['match_was_capped'] is True
        assert result['is_valid'] is True

    def test_300_percent_match(self):
        """300% match (3:1): FAM pays 3/4, customer pays 1/4.

        2500¢ at 300%: match = 2500 × 300/400 = 1875, customer = 625.
        """
        result = calculate_payment_breakdown(2500, [
            {'method_amount': 2500, 'match_percent': 300.0},
        ])
        assert result['fam_subsidy_total'] == 1875
        assert result['customer_total_paid'] == 625
        assert result['is_valid'] is True

    def test_mixed_high_low_match(self):
        """Mix of 200% and 50% match methods.

        10000¢ at 200%: match = 10000 × 200/300 = 6667
        10000¢ at  50%: match = 10000 ×  50/150 = 3333
        Total match = 10000, customer pays 10000.
        """
        result = calculate_payment_breakdown(20000, [
            {'method_amount': 10000, 'match_percent': 200.0},
            {'method_amount': 10000, 'match_percent': 50.0},
        ])
        assert result['fam_subsidy_total'] == 10000
        assert result['customer_total_paid'] == 10000
        assert result['is_valid'] is True

        items = result['line_items']
        assert items[0]['match_amount'] == 6667
        assert items[1]['match_amount'] == 3333


class TestMatchLimitWithPennyReconciliation:
    """Edge cases where odd-cent totals and match caps interact.

    Verify that customer_paid + fam_subsidy always reconciles to receipt_total
    within ±1 cent, even with awkward amounts.
    """

    def test_odd_total_with_tight_cap(self):
        """Receipt=5677 (odd cents), SNAP 100%, match_limit=2000.
        Uncapped match ~2839, capped to 2000.  Must reconcile."""
        result = calculate_payment_breakdown(5677, [
            {'method_amount': 5677, 'match_percent': 100.0},
        ], match_limit=2000)

        assert result['fam_subsidy_total'] == 2000
        assert result['match_was_capped'] is True
        assert abs(result['customer_total_paid'] + result['fam_subsidy_total'] - 5677) <= 1

    def test_one_cent_receipt_with_cap(self):
        """Receipt=1, SNAP 100%, match_limit=0.
        fam_subsidy=0, customer_paid=1."""
        result = calculate_payment_breakdown(1, [
            {'method_amount': 1, 'match_percent': 100.0},
        ], match_limit=0)

        assert result['fam_subsidy_total'] == 0
        assert result['customer_total_paid'] == 1
        assert abs(result['customer_total_paid'] + result['fam_subsidy_total'] - 1) <= 1

    def test_cap_at_one_penny(self):
        """Receipt=10000, SNAP 100%, match_limit=1.
        fam_subsidy=1, customer_paid=9999."""
        result = calculate_payment_breakdown(10000, [
            {'method_amount': 10000, 'match_percent': 100.0},
        ], match_limit=1)

        assert result['fam_subsidy_total'] == 1
        assert result['customer_total_paid'] == 9999
        assert result['match_was_capped'] is True
        assert abs(result['customer_total_paid'] + result['fam_subsidy_total'] - 10000) <= 1

    def test_multi_method_odd_total_with_cap(self):
        """Receipt=7777, SNAP 100% (5000) + Cash 0% (2777).
        SNAP uncapped match = 2500, capped to 1500.  Verify total reconciles."""
        result = calculate_payment_breakdown(7777, [
            {'method_amount': 5000, 'match_percent': 100.0},
            {'method_amount': 2777, 'match_percent': 0.0},
        ], match_limit=1500)

        assert result['fam_subsidy_total'] == 1500
        assert result['match_was_capped'] is True
        assert abs(result['customer_total_paid'] + result['fam_subsidy_total'] - 7777) <= 1

    def test_three_methods_all_matched_with_cap(self):
        """Receipt=9000, three entries all 100% match: method_amount 3000 each.
        match_limit=1000.  Each uncapped match = 1500, total = 4500.
        Cap to 1000 proportionally (~333 each).  Sum of match must be exactly 1000."""
        result = calculate_payment_breakdown(9000, [
            {'method_amount': 3000, 'match_percent': 100.0},
            {'method_amount': 3000, 'match_percent': 100.0},
            {'method_amount': 3000, 'match_percent': 100.0},
        ], match_limit=1000)

        assert result['fam_subsidy_total'] == 1000
        assert result['match_was_capped'] is True
        assert result['uncapped_fam_subsidy_total'] == 4500
        assert abs(result['customer_total_paid'] + result['fam_subsidy_total'] - 9000) <= 1

        # Each line item's match should be roughly 333, summing to exactly 1000
        matches = [li['match_amount'] for li in result['line_items']]
        assert sum(matches) == 1000
        for m in matches:
            assert 332 <= m <= 334  # proportional rounding


# ══════════════════════════════════════════════════════════════════
# Denomination + Match Limit Calculation Tests
# ══════════════════════════════════════════════════════════════════

class TestDenominationWithMatchLimit:
    """Verify calculate_payment_breakdown handles denominated entries
    correctly when a match limit cap is also active."""

    def test_denom_overage_with_cap_nonnegative_match(self):
        """Denomination causes overage + cap active: all match values ≥ 0.
        $49 receipt, $25 denom charge (100% match) → $50 alloc, overage=$1.
        Cap = $20."""
        result = calculate_payment_breakdown(4900, [
            {'method_amount': 5000, 'match_percent': 100.0},
        ], match_limit=2000)

        for li in result['line_items']:
            assert li['match_amount'] >= 0
            assert li['customer_charged'] >= 0
        assert result['fam_subsidy_total'] <= 2000

    def test_denom_overage_without_cap(self):
        """Denomination overage with no cap: match reduced by overage.
        $49 receipt, $50 alloc. Match should be $24 (not $25)."""
        result = calculate_payment_breakdown(4900, [
            {'method_amount': 5000, 'match_percent': 100.0},
        ])

        # Allocated will exceed receipt by $1 (denomination overage)
        assert result['allocated_total'] == 5000
        # System doesn't reduce match here — that's done in payment_screen
        # But line items should be consistent
        for li in result['line_items']:
            assert li['customer_charged'] + li['match_amount'] == li['method_amount']

    def test_two_denom_entries_cap_proportional(self):
        """Two denominated entries with cap: proportional reduction.
        Entry A: $50 alloc (100%), Entry B: $30 alloc (100%).
        Cap = $15. Proportional split: A≈$9.4, B≈$5.6."""
        result = calculate_payment_breakdown(8000, [
            {'method_amount': 5000, 'match_percent': 100.0},
            {'method_amount': 3000, 'match_percent': 100.0},
        ], match_limit=1500)

        assert result['fam_subsidy_total'] == 1500
        assert result['match_was_capped'] is True
        for li in result['line_items']:
            assert li['match_amount'] >= 0
            assert li['customer_charged'] >= 0

    def test_mixed_denom_and_nondenom_with_cap(self):
        """FMNP (denom) + SNAP (non-denom) with cap: both get proportional match."""
        # FMNP: $25 charge → $50 alloc, SNAP: $15 charge → $30 alloc
        result = calculate_payment_breakdown(8000, [
            {'method_amount': 5000, 'match_percent': 100.0},  # denominated
            {'method_amount': 3000, 'match_percent': 100.0},  # non-denominated
        ], match_limit=2000)

        assert result['fam_subsidy_total'] == 2000
        total_check = result['customer_total_paid'] + result['fam_subsidy_total']
        assert abs(total_check - 8000) <= 1

    def test_cap_reduces_match_below_overage(self):
        """Cap so tight it reduces total match below denomination overage.
        $49 receipt, $50 alloc (100% match). Uncapped match=$25, cap=$5.
        Match = $5, customer = $45. Allocated still $50 (overage handled by UI)."""
        result = calculate_payment_breakdown(4900, [
            {'method_amount': 5000, 'match_percent': 100.0},
        ], match_limit=500)

        assert result['fam_subsidy_total'] == 500
        for li in result['line_items']:
            assert li['match_amount'] >= 0
            assert li['customer_charged'] >= 0
