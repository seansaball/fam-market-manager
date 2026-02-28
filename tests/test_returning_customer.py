"""Tests for returning-customer match-limit tracking across multiple orders."""

import os
import tempfile
import pytest

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.utils.calculations import calculate_payment_breakdown
from fam.models.customer_order import (
    create_customer_order, get_customer_order,
    get_confirmed_customers_for_market_day, get_customer_prior_match,
    update_customer_order_status
)
from fam.models.transaction import (
    create_transaction, confirm_transaction, save_payment_line_items
)


@pytest.fixture(autouse=True)
def temp_db():
    """Create a fresh temporary database for each test."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    set_db_path(path)
    initialize_database()

    # Seed minimal data: one market, one vendor, one open market day
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, address, daily_match_limit, match_limit_active)"
        " VALUES (1, 'Test Market', '123 Main St', 100.00, 1)"
    )
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'Farm Stand')"
    )
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status)"
        " VALUES (1, 1, '2026-02-27', 'Open')"
    )
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (1, 'Food Bucks', 100.0, 1, 1)"
    )
    conn.commit()

    yield

    close_connection()
    try:
        os.remove(path)
    except OSError:
        pass


def _create_confirmed_order(market_day_id, receipt_total, match_amount,
                            customer_label=None):
    """Helper: create an order, add a transaction, save payment lines, confirm it."""
    order_id, label = create_customer_order(market_day_id, customer_label=customer_label)
    txn_id, _ = create_transaction(
        market_day_id=market_day_id,
        vendor_id=1,
        receipt_total=receipt_total,
        market_day_date='2026-02-27',
        customer_order_id=order_id,
    )
    customer_charged = round(receipt_total - match_amount, 2)
    save_payment_line_items(txn_id, [{
        'payment_method_id': 1,
        'method_name_snapshot': 'Food Bucks',
        'match_percent_snapshot': 100.0,
        'method_amount': receipt_total,
        'match_amount': match_amount,
        'customer_charged': customer_charged,
    }])
    confirm_transaction(txn_id, confirmed_by='Test')
    update_customer_order_status(order_id, 'Confirmed')
    return order_id, label


# ──────────────────────────────────────────────────────────────
# Model tests: get_confirmed_customers_for_market_day
# ──────────────────────────────────────────────────────────────
class TestGetConfirmedCustomers:

    def test_no_confirmed_orders(self):
        """No confirmed orders -> empty list."""
        result = get_confirmed_customers_for_market_day(1)
        assert result == []

    def test_single_confirmed_customer(self):
        """One confirmed order appears in the list."""
        _create_confirmed_order(1, 80.0, 80.0)
        customers = get_confirmed_customers_for_market_day(1)
        assert len(customers) == 1
        assert customers[0]['customer_label'] == 'C-001'
        assert customers[0]['total_match'] == 80.0
        assert customers[0]['receipt_count'] == 1

    def test_multiple_confirmed_customers(self):
        """Multiple distinct customers returned."""
        _create_confirmed_order(1, 80.0, 80.0)   # C-001
        _create_confirmed_order(1, 50.0, 25.0)   # C-002
        customers = get_confirmed_customers_for_market_day(1)
        assert len(customers) == 2
        labels = [c['customer_label'] for c in customers]
        assert 'C-001' in labels
        assert 'C-002' in labels

    def test_draft_orders_excluded(self):
        """Draft (unconfirmed) orders are not returned."""
        create_customer_order(1)  # Draft — no confirmation
        customers = get_confirmed_customers_for_market_day(1)
        assert customers == []

    def test_returning_customer_aggregated(self):
        """Two confirmed orders with the same label are aggregated."""
        _create_confirmed_order(1, 60.0, 60.0)  # C-001 first visit
        # Second visit reusing the same label
        _create_confirmed_order(1, 40.0, 40.0, customer_label='C-001')
        customers = get_confirmed_customers_for_market_day(1)
        assert len(customers) == 1
        assert customers[0]['customer_label'] == 'C-001'
        assert customers[0]['total_match'] == 100.0
        assert customers[0]['order_count'] == 2
        assert customers[0]['receipt_count'] == 2


# ──────────────────────────────────────────────────────────────
# Model tests: get_customer_prior_match
# ──────────────────────────────────────────────────────────────
class TestGetCustomerPriorMatch:

    def test_no_prior_orders(self):
        """No confirmed orders -> 0 prior match."""
        assert get_customer_prior_match('C-001', 1) == 0.0

    def test_single_prior_order(self):
        """One confirmed order's match total is returned."""
        _create_confirmed_order(1, 80.0, 80.0)  # C-001, $80 matched
        assert get_customer_prior_match('C-001', 1) == 80.0

    def test_excludes_current_order(self):
        """exclude_order_id prevents double-counting the current order."""
        order_id, _ = _create_confirmed_order(1, 80.0, 80.0)
        assert get_customer_prior_match('C-001', 1, exclude_order_id=order_id) == 0.0

    def test_multiple_orders_summed(self):
        """Prior match from multiple orders is summed correctly."""
        _create_confirmed_order(1, 60.0, 60.0)  # C-001 first visit
        _create_confirmed_order(1, 30.0, 30.0, customer_label='C-001')  # second visit
        assert get_customer_prior_match('C-001', 1) == 90.0

    def test_different_customers_isolated(self):
        """Different customer labels have independent match totals."""
        _create_confirmed_order(1, 80.0, 80.0)   # C-001
        _create_confirmed_order(1, 50.0, 25.0)   # C-002
        assert get_customer_prior_match('C-001', 1) == 80.0
        assert get_customer_prior_match('C-002', 1) == 25.0


# ──────────────────────────────────────────────────────────────
# Model tests: create_customer_order with returning label
# ──────────────────────────────────────────────────────────────
class TestReturningCustomerOrder:

    def test_new_customer_gets_sequential_label(self):
        """Default behavior: new sequential label."""
        _, label1 = create_customer_order(1)
        _, label2 = create_customer_order(1)
        assert label1 == 'C-001'
        assert label2 == 'C-002'

    def test_returning_customer_reuses_label(self):
        """Passing customer_label reuses the existing label."""
        _, label1 = create_customer_order(1)
        order_id2, label2 = create_customer_order(1, customer_label='C-001')
        assert label2 == 'C-001'
        # The order ID is different
        assert order_id2 is not None

    def test_get_customer_order_returns_match_fields(self):
        """get_customer_order includes match limit fields from the market."""
        order_id, _ = create_customer_order(1)
        order = get_customer_order(order_id)
        assert order['daily_match_limit'] == 100.0
        assert order['match_limit_active'] == 1


# ──────────────────────────────────────────────────────────────
# Integration: effective remaining limit calculation
# ──────────────────────────────────────────────────────────────
class TestEffectiveRemainingLimit:
    """Simulate the Payment screen's logic for computing the effective limit."""

    def _compute_effective_limit(self, order_id):
        """Replicate the Payment screen logic: daily_limit - prior_match."""
        order = get_customer_order(order_id)
        if not order.get('match_limit_active'):
            return None
        daily_limit = order.get('daily_match_limit') or 100.00
        prior = get_customer_prior_match(
            order['customer_label'],
            order['market_day_id'],
            exclude_order_id=order_id
        )
        return round(max(daily_limit - prior, 0.0), 2)

    def test_first_visit_full_limit(self):
        """First visit gets the full daily limit."""
        order_id, _ = create_customer_order(1)
        limit = self._compute_effective_limit(order_id)
        assert limit == 100.0

    def test_returning_customer_reduced_limit(self):
        """Returning customer's limit is reduced by prior match usage."""
        # First visit: $80 matched out of $100 limit
        _create_confirmed_order(1, 80.0, 80.0)

        # Second visit (returning customer)
        order_id2, _ = create_customer_order(1, customer_label='C-001')
        limit = self._compute_effective_limit(order_id2)
        assert limit == 20.0  # $100 - $80 = $20 remaining

    def test_limit_exhausted(self):
        """Customer who used full $100 gets $0 remaining limit."""
        _create_confirmed_order(1, 100.0, 100.0)

        order_id2, _ = create_customer_order(1, customer_label='C-001')
        limit = self._compute_effective_limit(order_id2)
        assert limit == 0.0

    def test_calculation_with_reduced_limit(self):
        """calculate_payment_breakdown correctly caps with the reduced limit."""
        # Prior $80 matched → only $20 remaining
        result = calculate_payment_breakdown(50.0, [
            {'method_amount': 50.0, 'match_percent': 100.0},
        ], match_limit=20.0)
        assert result['fam_subsidy_total'] == 20.0
        assert result['customer_total_paid'] == 30.0
        assert result['match_was_capped'] is True
        assert result['is_valid'] is True

    def test_calculation_under_reduced_limit(self):
        """When the order match is under the remaining limit, no cap applied.

        $15 at 100% match (1:1) → match = $7.50, under $20 limit.
        """
        result = calculate_payment_breakdown(15.0, [
            {'method_amount': 15.0, 'match_percent': 100.0},
        ], match_limit=20.0)
        assert result['fam_subsidy_total'] == 7.50
        assert result['customer_total_paid'] == 7.50
        assert result['match_was_capped'] is False
        assert result['is_valid'] is True

    def test_three_visits_cumulative(self):
        """Three visits: limit decreases with each confirmed visit."""
        # Visit 1: $40 matched
        _create_confirmed_order(1, 40.0, 40.0)

        # Visit 2: $35 matched (returning)
        _create_confirmed_order(1, 35.0, 35.0, customer_label='C-001')

        # Visit 3: should have $100 - $75 = $25 remaining
        order_id3, _ = create_customer_order(1, customer_label='C-001')
        limit = self._compute_effective_limit(order_id3)
        assert limit == 25.0

        # $80 purchase at 100% → uncapped match = $40, capped to $25
        result = calculate_payment_breakdown(80.0, [
            {'method_amount': 80.0, 'match_percent': 100.0},
        ], match_limit=limit)
        assert result['fam_subsidy_total'] == 25.0
        assert result['customer_total_paid'] == 55.0
        assert result['match_was_capped'] is True

    def test_zero_remaining_limit_blocks_match(self):
        """When limit is fully exhausted ($0 remaining), no match applies."""
        # $100 daily limit fully used
        _create_confirmed_order(1, 100.0, 100.0)

        # Returning visit: remaining = $0
        order_id2, _ = create_customer_order(1, customer_label='C-001')
        limit = self._compute_effective_limit(order_id2)
        assert limit == 0.0

        # Even with 100% match, FAM match should be $0
        result = calculate_payment_breakdown(50.0, [
            {'method_amount': 50.0, 'match_percent': 100.0},
        ], match_limit=0.0)
        assert result['fam_subsidy_total'] == 0.0
        assert result['customer_total_paid'] == 50.0
        assert result['match_was_capped'] is True
        assert result['is_valid'] is True

    def test_zero_limit_multi_method(self):
        """Zero remaining limit zeroes out all match amounts."""
        result = calculate_payment_breakdown(200.0, [
            {'method_amount': 100.0, 'match_percent': 50.0},
            {'method_amount': 100.0, 'match_percent': 100.0},
        ], match_limit=0.0)
        assert result['fam_subsidy_total'] == 0.0
        assert result['customer_total_paid'] == 200.0
        assert result['match_was_capped'] is True
        for li in result['line_items']:
            assert li['match_amount'] == 0.0
            assert li['customer_charged'] == li['method_amount']
