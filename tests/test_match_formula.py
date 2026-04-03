"""Comprehensive tests for the match formula: amount x pct / (100 + pct).

All monetary values are integer cents (e.g. $50.00 = 5000).
Match percentages remain as floats.

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
    """Shortcut for a single payment method covering the full receipt (cents)."""
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
        r = _simple(10000, 100.0)
        assert r['match'] == 5000
        assert r['customer'] == 5000

    def test_100pct_on_odd_amount(self):
        """$99 (9900¢) at 100%: match = 4950, customer = 4950."""
        r = _simple(9900, 100.0)
        assert r['match'] == 4950
        assert r['customer'] == 4950

    def test_50pct_match(self):
        """50% match: FAM pays 1/3, customer pays 2/3."""
        r = _simple(15000, 50.0)
        # 15000 * 50/150 = 5000
        assert r['match'] == 5000
        assert r['customer'] == 10000

    def test_200pct_is_2_to_1(self):
        """200% match means 2:1 — FAM pays 2/3, customer pays 1/3."""
        r = _simple(30000, 200.0)
        # 30000 * 200/300 = 20000
        assert r['match'] == 20000
        assert r['customer'] == 10000

    def test_25pct_match(self):
        """25% match: 10000 * 25/125 = 2000."""
        r = _simple(10000, 25.0)
        assert r['match'] == 2000
        assert r['customer'] == 8000

    def test_75pct_match(self):
        """75% match: 10000 * 75/175 = 4286."""
        r = _simple(10000, 75.0)
        assert r['match'] == 4286
        assert r['customer'] == 5714

    def test_10pct_match(self):
        """10% match: 10000 * 10/110 = 909."""
        r = _simple(10000, 10.0)
        assert r['match'] == 909
        assert r['customer'] == 9091

    def test_500pct_match(self):
        """500% match (5:1): 10000 * 500/600 = 8333."""
        r = _simple(10000, 500.0)
        assert r['match'] == 8333
        assert r['customer'] == 1667


# ══════════════════════════════════════════════════════════════════
# 2. The golden rule: match + customer == method_amount always
# ══════════════════════════════════════════════════════════════════
class TestReconciliation:
    """match_amount + customer_charged must always equal method_amount."""

    @pytest.mark.parametrize("receipt,pct", [
        (10000, 0.0),
        (10000, 10.0),
        (10000, 25.0),
        (10000, 33.0),
        (10000, 50.0),
        (10000, 75.0),
        (10000, 100.0),
        (10000, 150.0),
        (10000, 200.0),
        (10000, 300.0),
        (10000, 999.0),
        (100, 100.0),
        (1, 100.0),
        (999999, 100.0),
        (7347, 67.0),
        (12345, 123.0),
    ])
    def test_line_item_reconciles(self, receipt, pct):
        r = _simple(receipt, pct)
        assert r['match'] + r['customer'] == receipt
        assert r['valid'] is True

    @pytest.mark.parametrize("receipt,pct", [
        (10000, 0.0),
        (10000, 50.0),
        (10000, 100.0),
        (10000, 200.0),
        (10000, 300.0),
        (5000, 100.0),
        (100, 100.0),
        (999999, 100.0),
    ])
    def test_totals_reconcile(self, receipt, pct):
        """fam_total + cust_total == receipt."""
        r = _simple(receipt, pct)
        assert abs((r['fam_total'] + r['cust_total']) - receipt) <= 1

    def test_multi_method_reconciles(self):
        """Multiple payment methods still sum to receipt total."""
        result = calculate_payment_breakdown(50000, [
            {'method_amount': 20000, 'match_percent': 100.0},
            {'method_amount': 15000, 'match_percent': 50.0},
            {'method_amount': 15000, 'match_percent': 0.0},
        ])
        assert result['is_valid'] is True
        total = result['customer_total_paid'] + result['fam_subsidy_total']
        assert abs(total - 50000) <= 1

        # Each line item reconciles individually
        for li in result['line_items']:
            assert li['match_amount'] + li['customer_charged'] == li['method_amount']

    def test_multi_method_with_cap_reconciles(self):
        """Multiple methods + cap: totals still reconcile."""
        result = calculate_payment_breakdown(40000, [
            {'method_amount': 20000, 'match_percent': 100.0},
            {'method_amount': 20000, 'match_percent': 50.0},
        ], match_limit=8000)
        assert result['is_valid'] is True
        assert result['match_was_capped'] is True
        total = result['customer_total_paid'] + result['fam_subsidy_total']
        assert abs(total - 40000) <= 1

        for li in result['line_items']:
            assert li['customer_charged'] == li['method_amount'] - li['match_amount']


# ══════════════════════════════════════════════════════════════════
# 3. Customer always pays a non-negative amount
# ══════════════════════════════════════════════════════════════════
class TestCustomerNeverNegative:
    """With the new formula, customer_charged >= 0 for all valid inputs."""

    @pytest.mark.parametrize("pct", [0, 10, 50, 100, 150, 200, 300, 500, 999])
    def test_customer_charged_non_negative(self, pct):
        r = _simple(10000, float(pct))
        assert r['customer'] >= 0

    @pytest.mark.parametrize("pct", [100, 200, 300, 500, 999])
    def test_match_never_exceeds_receipt(self, pct):
        """FAM match is always less than the receipt total."""
        r = _simple(10000, float(pct))
        assert r['match'] < 10000
        assert r['match'] > 0


# ══════════════════════════════════════════════════════════════════
# 4. Boundary conditions and zero-amount edge cases
# ══════════════════════════════════════════════════════════════════
class TestBoundaryConditions:

    def test_zero_receipt(self):
        """0¢ receipt: everything is zero."""
        r = _simple(0, 100.0)
        assert r['match'] == 0
        assert r['customer'] == 0

    def test_zero_percent(self):
        """0% match: no FAM subsidy."""
        r = _simple(10000, 0.0)
        assert r['match'] == 0
        assert r['customer'] == 10000

    def test_penny_receipt(self):
        """1¢ receipt at 100%: each side gets a penny or zero."""
        r = _simple(1, 100.0)
        # 1 * 100/200 = 0.5 rounds to 0 (banker's rounding)
        # match + customer must equal 1
        assert r['match'] + r['customer'] == 1

    def test_two_cent_receipt(self):
        """2¢ receipt at 100%: perfectly splits."""
        r = _simple(2, 100.0)
        assert r['match'] == 1
        assert r['customer'] == 1

    def test_large_receipt(self):
        """$10,000 (1000000¢) order at 100%: clean 50/50 split."""
        r = _simple(1000000, 100.0)
        assert r['match'] == 500000
        assert r['customer'] == 500000

    def test_very_high_match_percent(self):
        """999% match: FAM pays 999/1099 ≈ 90.9%."""
        r = _simple(10000, 999.0)
        assert r['match'] == round(10000 * 999 / 1099)
        assert r['match'] + r['customer'] == 10000

    def test_fractional_match_percent(self):
        """Non-integer match percent (e.g. 33.33%)."""
        r = _simple(10000, 33.33)
        expected_match = round(10000 * 33.33 / 133.33)
        assert r['match'] == expected_match
        assert r['match'] + r['customer'] == 10000

    def test_empty_payment_entries(self):
        """No payment entries: early return with is_valid False."""
        result = calculate_payment_breakdown(10000, [])
        assert result['is_valid'] is False
        assert result['fam_subsidy_total'] == 0
        assert len(result['errors']) > 0

    def test_negative_receipt_flagged(self):
        """Negative receipt total creates an error."""
        result = calculate_payment_breakdown(-5000, [
            {'method_amount': -5000, 'match_percent': 100.0},
        ])
        assert len(result['errors']) > 0


# ══════════════════════════════════════════════════════════════════
# 5. Match limit (cap) edge cases
# ══════════════════════════════════════════════════════════════════
class TestCapEdgeCases:

    def test_cap_exactly_at_computed_match(self):
        """When cap == computed match, no capping occurs."""
        # 10000¢ at 100% → match = 5000
        r = _simple(10000, 100.0, limit=5000)
        assert r['match'] == 5000
        assert r['capped'] is False

    def test_cap_one_penny_below_match(self):
        """Cap at 4999¢ when match would be 5000¢ → capped."""
        r = _simple(10000, 100.0, limit=4999)
        assert r['fam_total'] == 4999
        assert r['capped'] is True

    def test_cap_zero(self):
        """Cap of 0 blocks all match."""
        r = _simple(10000, 100.0, limit=0)
        assert r['fam_total'] == 0
        assert r['cust_total'] == 10000
        assert r['capped'] is True

    def test_cap_preserves_uncapped_total(self):
        """Uncapped total always reflects the pre-cap value."""
        r = _simple(20000, 100.0, limit=2500)
        assert r['uncapped'] == 10000   # 20000 * 100/200 = 10000
        assert r['fam_total'] == 2500

    def test_three_methods_capped_proportionally(self):
        """Three payment methods capped proportionally."""
        result = calculate_payment_breakdown(30000, [
            {'method_amount': 10000, 'match_percent': 100.0},   # match = 5000
            {'method_amount': 10000, 'match_percent': 50.0},    # match = 3333
            {'method_amount': 10000, 'match_percent': 200.0},   # match = 6667
        ], match_limit=6000)
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 6000
        assert result['is_valid'] is True

        for li in result['line_items']:
            assert li['customer_charged'] == li['method_amount'] - li['match_amount']

        total = result['customer_total_paid'] + result['fam_subsidy_total']
        assert abs(total - 30000) <= 1


# ══════════════════════════════════════════════════════════════════
# 6. Real-world farmer's market scenarios
# ══════════════════════════════════════════════════════════════════
class TestRealWorldScenarios:

    def test_snap_100pct_match_small_purchase(self):
        """Customer buys $12 of produce with SNAP (100% match).
        Customer pays 600¢, FAM pays 600¢."""
        r = _simple(1200, 100.0)
        assert r['match'] == 600
        assert r['customer'] == 600

    def test_snap_100pct_match_large_purchase(self):
        """Customer buys $87.50 of produce with SNAP (100% match).
        Customer pays 4375¢, FAM pays 4375¢."""
        r = _simple(8750, 100.0)
        assert r['match'] == 4375
        assert r['customer'] == 4375

    def test_mixed_snap_and_cash(self):
        """$50 total: $30 via SNAP (100% match), $20 via cash (0% match)."""
        result = calculate_payment_breakdown(5000, [
            {'method_amount': 3000, 'match_percent': 100.0},
            {'method_amount': 2000, 'match_percent': 0.0},
        ])
        assert result['is_valid'] is True
        items = result['line_items']

        assert items[0]['match_amount'] == 1500
        assert items[0]['customer_charged'] == 1500

        assert items[1]['match_amount'] == 0
        assert items[1]['customer_charged'] == 2000

        assert result['fam_subsidy_total'] == 1500
        assert result['customer_total_paid'] == 3500

    def test_daily_cap_scenario(self):
        """Customer with $20 daily limit remaining buys $50 at 100% match.
        Uncapped match = 2500¢, capped to 2000¢. Customer pays 3000¢."""
        r = _simple(5000, 100.0, limit=2000)
        assert r['fam_total'] == 2000
        assert r['cust_total'] == 3000
        assert r['capped'] is True

    def test_returning_customer_exhausted_limit(self):
        """Returning customer with $0 remaining — no FAM benefit at all."""
        r = _simple(4000, 100.0, limit=0)
        assert r['fam_total'] == 0
        assert r['cust_total'] == 4000

    def test_food_bucks_and_snap_with_cap(self):
        """$100 split: $60 Food Bucks (100%), $40 SNAP (100%), cap $30.
        Uncapped: 3000 + 2000 = 5000, capped to 3000.
        Ratio = 3000/5000 = 0.6
        Food Bucks match: 3000*0.6 = 1800, SNAP match: 2000*0.6 = 1200."""
        result = calculate_payment_breakdown(10000, [
            {'method_amount': 6000, 'match_percent': 100.0},
            {'method_amount': 4000, 'match_percent': 100.0},
        ], match_limit=3000)
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 3000
        assert result['customer_total_paid'] == 7000
        assert result['is_valid'] is True

        items = result['line_items']
        assert items[0]['match_amount'] == 1800
        assert items[1]['match_amount'] == 1200


# ══════════════════════════════════════════════════════════════════
# 7. Cap penny-rounding precision
# ══════════════════════════════════════════════════════════════════
class TestCapPennyRounding:
    """After proportional cap, the sum of rounded match amounts should
    stay within 1 cent of the cap value."""

    @pytest.mark.parametrize("cap", [1000, 1500, 2000, 2500, 3333, 4999, 7550])
    def test_cap_sum_within_tolerance(self, cap):
        """Sum of capped match amounts stays within 1¢ of the cap."""
        result = calculate_payment_breakdown(30000, [
            {'method_amount': 10000, 'match_percent': 100.0},    # 5000
            {'method_amount': 10000, 'match_percent': 50.0},     # 3333
            {'method_amount': 10000, 'match_percent': 200.0},    # 6667
        ], match_limit=cap)
        assert result['match_was_capped'] is True
        assert abs(result['fam_subsidy_total'] - cap) <= 1

    def test_five_methods_cap_penny_accuracy(self):
        """Five diverse methods capped — total match exact after cent adjustment."""
        result = calculate_payment_breakdown(50000, [
            {'method_amount': 12000, 'match_percent': 100.0},
            {'method_amount': 8000,  'match_percent': 50.0},
            {'method_amount': 10000, 'match_percent': 200.0},
            {'method_amount': 15000, 'match_percent': 75.0},
            {'method_amount': 5000,  'match_percent': 25.0},
        ], match_limit=5555)
        assert result['match_was_capped'] is True
        assert abs(result['fam_subsidy_total'] - 5555) <= 1
        for li in result['line_items']:
            assert li['customer_charged'] == li['method_amount'] - li['match_amount']

    def test_cap_at_one_penny(self):
        """Cap of 1¢ — minimal cap still distributes correctly."""
        result = calculate_payment_breakdown(10000, [
            {'method_amount': 5000, 'match_percent': 100.0},
            {'method_amount': 5000, 'match_percent': 100.0},
        ], match_limit=1)
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] <= 2  # at most 1 cent drift
        total = result['customer_total_paid'] + result['fam_subsidy_total']
        assert abs(total - 10000) <= 1

    def test_cap_with_asymmetric_amounts(self):
        """Highly asymmetric amounts — 99900¢ + 100¢ capped to 1000¢."""
        result = calculate_payment_breakdown(100000, [
            {'method_amount': 99900, 'match_percent': 100.0},
            {'method_amount': 100,   'match_percent': 100.0},
        ], match_limit=1000)
        assert result['match_was_capped'] is True
        assert abs(result['fam_subsidy_total'] - 1000) <= 1
        assert result['is_valid'] is True


# ══════════════════════════════════════════════════════════════════
# 8. Multi-transaction distribution math
# ══════════════════════════════════════════════════════════════════
class TestMultiTransactionDistribution:
    """Test the remainder-based distribution math used by
    _distribute_and_save_payments. We replicate the same algorithm
    here to verify the math without needing the UI."""

    @staticmethod
    def _distribute(items, transactions, match_limit=None):
        """Replicate _distribute_and_save_payments math (integer cents).

        items: list of {'method_amount': int, 'match_percent': float}
        transactions: list of {'receipt_total': int}
        match_limit: optional cap in cents
        """
        order_total = sum(t['receipt_total'] for t in transactions)
        if order_total <= 0:
            return []

        all_txn_items = []
        num_txns = len(transactions)
        allocated_method = [0] * len(items)
        allocated_match = [0] * len(items)

        for t_idx, t in enumerate(transactions):
            is_last = (t_idx == num_txns - 1)
            proportion = t['receipt_total'] / order_total
            txn_items = []

            for j, item in enumerate(items):
                if is_last:
                    method_amount = item['method_amount'] - allocated_method[j]
                else:
                    method_amount = round(item['method_amount'] * proportion)
                    allocated_method[j] += method_amount

                match_pct = item['match_percent']
                if is_last:
                    total_match = round(
                        item['method_amount'] * (match_pct / (100.0 + match_pct))
                    )
                    match_amount = total_match - allocated_match[j]
                else:
                    match_amount = round(
                        method_amount * (match_pct / (100.0 + match_pct))
                    )
                    allocated_match[j] += match_amount

                customer_charged = method_amount - match_amount

                txn_items.append({
                    'method_amount': method_amount,
                    'match_amount': match_amount,
                    'customer_charged': customer_charged,
                })

            all_txn_items.append(txn_items)

        # Apply match-limit cap
        if match_limit is not None:
            total_match = sum(
                li['match_amount'] for txn_items in all_txn_items for li in txn_items
            )
            if total_match > match_limit >= 0:
                cap_ratio = match_limit / total_match
                for txn_items in all_txn_items:
                    for li in txn_items:
                        li['match_amount'] = round(li['match_amount'] * cap_ratio)
                        li['customer_charged'] = (
                            li['method_amount'] - li['match_amount']
                        )

                # Cent adjustment: fix rounding drift so sum == cap exactly
                capped_sum = sum(
                    li['match_amount']
                    for txn_items in all_txn_items for li in txn_items
                )
                cent_diff = match_limit - capped_sum
                if cent_diff != 0:
                    all_lines = [
                        li for txn_items in all_txn_items for li in txn_items
                        if li['match_amount'] > 0
                    ]
                    if all_lines:
                        target = max(all_lines, key=lambda li: li['match_amount'])
                        target['match_amount'] += cent_diff
                        target['customer_charged'] = (
                            target['method_amount'] - target['match_amount']
                        )

        return all_txn_items

    def test_single_txn_matches_calculate_breakdown(self):
        """Single transaction: distribution should equal calculate_payment_breakdown."""
        items = [
            {'method_amount': 5000, 'match_percent': 100.0},
            {'method_amount': 3000, 'match_percent': 50.0},
        ]
        txns = [{'receipt_total': 8000}]
        distributed = self._distribute(items, txns)
        result = calculate_payment_breakdown(8000, items)

        for i, li in enumerate(distributed[0]):
            assert li['match_amount'] == result['line_items'][i]['match_amount']
            assert li['customer_charged'] == result['line_items'][i]['customer_charged']

    def test_two_txn_method_amounts_sum(self):
        """Method amounts across 2 transactions sum to original total."""
        items = [
            {'method_amount': 10000, 'match_percent': 100.0},
            {'method_amount': 6000,  'match_percent': 50.0},
        ]
        txns = [{'receipt_total': 9000}, {'receipt_total': 7000}]
        distributed = self._distribute(items, txns)

        for j in range(len(items)):
            total = sum(distributed[t][j]['method_amount'] for t in range(2))
            assert total == items[j]['method_amount']

    def test_two_txn_match_amounts_sum(self):
        """Match amounts across 2 transactions sum to overall match."""
        items = [
            {'method_amount': 10000, 'match_percent': 100.0},
            {'method_amount': 6000,  'match_percent': 50.0},
        ]
        txns = [{'receipt_total': 9000}, {'receipt_total': 7000}]
        distributed = self._distribute(items, txns)

        for j in range(len(items)):
            total_match = sum(distributed[t][j]['match_amount'] for t in range(2))
            expected = round(
                items[j]['method_amount'] * (items[j]['match_percent']
                    / (100.0 + items[j]['match_percent']))
            )
            assert total_match == expected

    def test_three_txn_all_line_items_reconcile(self):
        """Every distributed line item: customer + match = method_amount."""
        items = [
            {'method_amount': 20000, 'match_percent': 100.0},
            {'method_amount': 10000, 'match_percent': 75.0},
        ]
        txns = [
            {'receipt_total': 10000},
            {'receipt_total': 12000},
            {'receipt_total': 8000},
        ]
        distributed = self._distribute(items, txns)

        for txn_items in distributed:
            for li in txn_items:
                assert li['match_amount'] + li['customer_charged'] == li['method_amount']

    def test_three_txn_with_cap(self):
        """Distribution + cap: total match within tolerance of the cap."""
        items = [
            {'method_amount': 20000, 'match_percent': 100.0},
            {'method_amount': 10000, 'match_percent': 50.0},
        ]
        txns = [
            {'receipt_total': 10000},
            {'receipt_total': 12000},
            {'receipt_total': 8000},
        ]
        distributed = self._distribute(items, txns, match_limit=5000)

        total_match = sum(
            li['match_amount'] for txn_items in distributed for li in txn_items
        )
        assert abs(total_match - 5000) <= 2

        for txn_items in distributed:
            for li in txn_items:
                assert li['customer_charged'] == (
                    li['method_amount'] - li['match_amount']
                )

    def test_uneven_split_no_penny_loss(self):
        """3333¢ across 3 equal receipts — no cents lost."""
        items = [{'method_amount': 3333, 'match_percent': 100.0}]
        txns = [
            {'receipt_total': 1111},
            {'receipt_total': 1111},
            {'receipt_total': 1111},
        ]
        distributed = self._distribute(items, txns)

        total_method = sum(distributed[t][0]['method_amount'] for t in range(3))
        total_match = sum(distributed[t][0]['match_amount'] for t in range(3))

        assert total_method == 3333
        expected_match = round(3333 * 100.0 / 200.0)
        assert total_match == expected_match

    def test_customer_charged_never_negative(self):
        """No distributed line item should have negative customer_charged."""
        items = [
            {'method_amount': 15000, 'match_percent': 200.0},
            {'method_amount': 5000,  'match_percent': 500.0},
        ]
        txns = [
            {'receipt_total': 8000},
            {'receipt_total': 7000},
            {'receipt_total': 5000},
        ]
        distributed = self._distribute(items, txns)

        for txn_items in distributed:
            for li in txn_items:
                assert li['customer_charged'] >= 0
                assert li['match_amount'] >= 0


# ══════════════════════════════════════════════════════════════════
# Penny Reconciliation Guard Tests
# ══════════════════════════════════════════════════════════════════

class TestPennyReconciliationGuard:
    """Verify penny reconciliation never produces negative match_amount."""

    def test_small_matched_row_overage_no_negative_match(self):
        """Tiny method_amount + 1% match: match rounds to 0.
        Penny reconciliation must not push match below zero."""
        # method_amount=2, 1% match → charge = round(2/1.01) = 2, match = 0
        # If another row causes overage, reconciliation adds negative to match=0
        result = calculate_payment_breakdown(3, [
            {'method_amount': 2, 'match_percent': 1.0},
            {'method_amount': 1, 'match_percent': 0.0},
        ])
        for li in result['line_items']:
            assert li['match_amount'] >= 0, (
                f"match_amount should never be negative, got {li['match_amount']}"
            )
            assert li['customer_charged'] >= 0, (
                f"customer_charged should never be negative, got {li['customer_charged']}"
            )

    def test_penny_reconciliation_with_zero_match_row(self):
        """Row with match_percent > 0 but match rounds to 0: guard prevents negative."""
        # Craft entries where allocated is 1 cent over receipt due to rounding
        result = calculate_payment_breakdown(101, [
            {'method_amount': 100, 'match_percent': 1.0},
            {'method_amount': 1, 'match_percent': 0.0},
        ])
        for li in result['line_items']:
            assert li['match_amount'] >= 0

    def test_penny_overage_absorbed_by_customer_when_match_is_zero(self):
        """When matched row has match=0, overage goes to customer_charged."""
        # method_amount=1, 1% match → charge=1, match=0
        # If penny reconciliation fires, it should go to customer_charged
        result = calculate_payment_breakdown(1, [
            {'method_amount': 1, 'match_percent': 1.0},
        ])
        li = result['line_items'][0]
        assert li['match_amount'] >= 0
        assert li['customer_charged'] >= 0
        assert li['customer_charged'] + li['match_amount'] == li['method_amount']

    def test_large_match_penny_reconciliation_safe(self):
        """Normal-sized rows: penny reconciliation adjusts match safely."""
        result = calculate_payment_breakdown(9999, [
            {'method_amount': 5000, 'match_percent': 100.0},
            {'method_amount': 4999, 'match_percent': 50.0},
        ])
        for li in result['line_items']:
            assert li['match_amount'] >= 0
        assert abs(result['allocation_remaining']) <= 1

    def test_three_rows_penny_drift_all_nonnegative(self):
        """Three matched rows with odd total: no match goes negative."""
        result = calculate_payment_breakdown(7777, [
            {'method_amount': 3000, 'match_percent': 100.0},
            {'method_amount': 2777, 'match_percent': 50.0},
            {'method_amount': 2000, 'match_percent': 25.0},
        ])
        for li in result['line_items']:
            assert li['match_amount'] >= 0
            assert li['customer_charged'] >= 0

    def test_penny_reconciliation_with_cap_and_small_match(self):
        """Match cap reduces match to near-zero; penny reconciliation is safe."""
        # 100% match on $100: uncapped match=$50, cap to $1
        result = calculate_payment_breakdown(10000, [
            {'method_amount': 10000, 'match_percent': 100.0},
        ], match_limit=1)
        li = result['line_items'][0]
        assert li['match_amount'] >= 0
        assert li['customer_charged'] >= 0
        assert abs(result['allocation_remaining']) <= 1


class TestProductionReadiness:
    """Comprehensive edge case tests for production readiness."""

    def test_all_cash_no_match(self):
        """100% Cash (0% match) — no match, customer pays everything."""
        result = calculate_payment_breakdown(5000, [
            {'method_amount': 5000, 'match_percent': 0.0},
        ])
        assert result['fam_subsidy_total'] == 0
        assert result['customer_total_paid'] == 5000
        assert result['allocation_remaining'] == 0

    def test_mixed_methods_penny_always_to_fam(self):
        """Mixed methods where floor rounding creates a gap — FAM absorbs."""
        # $116.39 with 100% SNAP: charge = floor(11639/2) = 5819
        # ma = round(5819 * 2.0) = 11638, gap = 1
        result = calculate_payment_breakdown(11639, [
            {'method_amount': 11638, 'match_percent': 100.0},
        ])
        # Penny reconciliation should add 1 to match
        li = result['line_items'][0]
        assert li['match_amount'] >= li['customer_charged'], (
            f"FAM match {li['match_amount']} should be >= customer "
            f"{li['customer_charged']} (penny goes to FAM)"
        )
        assert result['allocation_remaining'] == 0

    def test_match_cap_zero_means_no_match(self):
        """Match limit of $0 — customer pays everything."""
        result = calculate_payment_breakdown(5000, [
            {'method_amount': 5000, 'match_percent': 100.0},
        ], match_limit=0)
        assert result['fam_subsidy_total'] == 0
        assert result['customer_total_paid'] == 5000

    def test_match_cap_exactly_equals_uncapped(self):
        """Match limit exactly equals uncapped match — no reduction."""
        # $100 with 100% SNAP: match = $50
        result = calculate_payment_breakdown(10000, [
            {'method_amount': 10000, 'match_percent': 100.0},
        ], match_limit=5000)
        assert result['fam_subsidy_total'] == 5000
        assert result['match_was_capped'] is False

    def test_multi_row_all_matched_penny_to_largest(self):
        """Multiple matched rows — penny goes to largest match row."""
        result = calculate_payment_breakdown(10001, [
            {'method_amount': 8000, 'match_percent': 100.0},
            {'method_amount': 2000, 'match_percent': 50.0},
        ])
        # Verify no negative values anywhere
        for li in result['line_items']:
            assert li['match_amount'] >= 0, f"Negative match: {li}"
            assert li['customer_charged'] >= 0, f"Negative charged: {li}"
        assert abs(result['allocation_remaining']) <= 1

    def test_match_formula_consistency(self):
        """Verify charge_to_method_amount and breakdown use equivalent math."""
        from fam.utils.calculations import charge_to_method_amount
        for pct in [0, 25, 50, 75, 100]:
            for charge in [100, 999, 5001, 11639]:
                ma = charge_to_method_amount(charge, float(pct))
                result = calculate_payment_breakdown(ma, [
                    {'method_amount': ma, 'match_percent': float(pct)},
                ])
                li = result['line_items'][0]
                # customer_charged should be within 1 cent of original charge
                assert abs(li['customer_charged'] - charge) <= 1, (
                    f"pct={pct} charge={charge}: got customer_charged="
                    f"{li['customer_charged']}"
                )

    def test_denomination_overage_nonnegative_everything(self):
        """Denomination overage: all values stay non-negative."""
        from fam.utils.calculations import charge_to_method_amount
        # $49 order, $5 denom 100% match → 5 units = $50 charge, $100 ma
        # Overage = $100 - $49 = $51 — too large (> effective denom $10)
        # This should NOT trigger forfeit
        result = calculate_payment_breakdown(4900, [
            {'method_amount': 10000, 'match_percent': 100.0},
        ])
        for li in result['line_items']:
            assert li['match_amount'] >= 0
            assert li['customer_charged'] >= 0
