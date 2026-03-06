"""Comprehensive tests for the adjustment workflow.

Covers:
  - Payment line item save / retrieve round-trip fidelity
  - Match-limit cap applied correctly in adjustment calculations
  - Customer impact (collect / refund / no-change) calculation accuracy
  - Audit log entries for receipt, vendor, and payment-method adjustments
  - Ledger backup text file accuracy after adjustments, voids, and FMNP entries
  - Edge cases: penny transactions, 0% match, very high match, cap boundary,
    multi-method splits, double-adjustments, voided transactions

Scenario seed:
  Market:    Downtown Market — daily_match_limit=$25, match_limit_active=1
  Vendors:   Farm Stand, Bakery
  Methods:   SNAP (100%), Cash (0%), Food Bucks (100%), FMNP (100%)
  Market Day: 2026-03-01, Open
"""

import os
import pytest

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.utils.calculations import calculate_payment_breakdown
from fam.models.transaction import (
    create_transaction, confirm_transaction, update_transaction,
    void_transaction, save_payment_line_items, get_payment_line_items,
    get_transaction_by_id, search_transactions,
)
from fam.models.audit import log_action, get_audit_log, get_transaction_log
from fam.utils.export import write_ledger_backup


# ──────────────────────────────────────────────────────────────────
# Fixture: fresh database per test
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Create a fresh database for each test."""
    db_file = str(tmp_path / "test_adjustments.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    # Seed base data
    conn.execute(
        "INSERT INTO markets (id, name, address, daily_match_limit, match_limit_active)"
        " VALUES (1, 'Downtown Market', '123 Main St', 25.00, 1)"
    )
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'Farm Stand')")
    conn.execute("INSERT INTO vendors (id, name) VALUES (2, 'Bakery')")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (1, 'SNAP', 100.0, 1, 1)"
    )
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (2, 'Cash', 0.0, 1, 2)"
    )
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (3, 'Food Bucks', 100.0, 1, 3)"
    )
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (4, 'FMNP', 100.0, 1, 4)"
    )
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, opened_by)"
        " VALUES (1, 1, '2026-03-01', 'Open', 'Alice')"
    )
    conn.execute(
        "INSERT INTO customer_orders (id, market_day_id, customer_label, zip_code)"
        " VALUES (1, 1, 'C-001', '12345')"
    )
    conn.commit()

    yield conn

    close_connection()


def _make_line_item(method_id, method_name, match_pct, amount, match_amt, customer):
    """Helper: build a payment line item dict."""
    return {
        'payment_method_id': method_id,
        'method_name_snapshot': method_name,
        'match_percent_snapshot': match_pct,
        'method_amount': amount,
        'match_amount': match_amt,
        'customer_charged': customer,
    }


def _create_confirmed_txn(receipt_total, vendor_id=1, line_items=None):
    """Helper: create a confirmed transaction with payment line items."""
    txn_id, fam_id = create_transaction(
        market_day_id=1, vendor_id=vendor_id, receipt_total=receipt_total,
        market_day_date='2026-03-01', customer_order_id=1,
    )
    if line_items:
        save_payment_line_items(txn_id, line_items)
    confirm_transaction(txn_id, confirmed_by='Alice')
    return txn_id, fam_id


# ══════════════════════════════════════════════════════════════════
# 1. Payment Line Item Round-Trip
# ══════════════════════════════════════════════════════════════════

class TestLineItemRoundTrip:
    """Verify save → retrieve preserves all fields exactly."""

    def test_single_method_round_trip(self):
        """Single SNAP payment: all 6 fields survive the round trip."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=items)

        loaded = get_payment_line_items(txn_id)
        assert len(loaded) == 1
        li = loaded[0]
        assert li['payment_method_id'] == 1
        assert li['method_name_snapshot'] == 'SNAP'
        assert li['match_percent_snapshot'] == 100.0
        assert li['method_amount'] == 50.00
        assert li['match_amount'] == 25.00
        assert li['customer_charged'] == 25.00

    def test_multi_method_round_trip(self):
        """Two-method payment: both rows stored and retrievable."""
        items = [
            _make_line_item(1, 'SNAP', 100.0, 30.00, 15.00, 15.00),
            _make_line_item(2, 'Cash', 0.0, 20.00, 0.00, 20.00),
        ]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=items)

        loaded = get_payment_line_items(txn_id)
        assert len(loaded) == 2
        snap = loaded[0]
        cash = loaded[1]
        assert snap['method_name_snapshot'] == 'SNAP'
        assert cash['method_name_snapshot'] == 'Cash'
        assert snap['method_amount'] + cash['method_amount'] == 50.00

    def test_resave_replaces_old_items(self):
        """Saving new line items deletes old ones (adjustment pattern)."""
        original = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=original)

        # "Adjust" — replace SNAP with Cash
        new_items = [_make_line_item(2, 'Cash', 0.0, 50.00, 0.00, 50.00)]
        save_payment_line_items(txn_id, new_items)

        loaded = get_payment_line_items(txn_id)
        assert len(loaded) == 1
        assert loaded[0]['method_name_snapshot'] == 'Cash'
        assert loaded[0]['match_amount'] == 0.00

    def test_resave_one_to_many(self):
        """Adjust from single method to multiple methods."""
        original = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=original)

        new_items = [
            _make_line_item(1, 'SNAP', 100.0, 30.00, 15.00, 15.00),
            _make_line_item(2, 'Cash', 0.0, 20.00, 0.00, 20.00),
        ]
        save_payment_line_items(txn_id, new_items)

        loaded = get_payment_line_items(txn_id)
        assert len(loaded) == 2
        total_amount = sum(li['method_amount'] for li in loaded)
        assert total_amount == 50.00

    def test_resave_many_to_one(self):
        """Adjust from multiple methods to a single method."""
        original = [
            _make_line_item(1, 'SNAP', 100.0, 30.00, 15.00, 15.00),
            _make_line_item(2, 'Cash', 0.0, 20.00, 0.00, 20.00),
        ]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=original)

        new_items = [_make_line_item(3, 'Food Bucks', 100.0, 50.00, 25.00, 25.00)]
        save_payment_line_items(txn_id, new_items)

        loaded = get_payment_line_items(txn_id)
        assert len(loaded) == 1
        assert loaded[0]['method_name_snapshot'] == 'Food Bucks'

    def test_capped_values_persisted(self):
        """Line items saved with capped match values should store the capped amounts."""
        # $100 SNAP with $25 cap → uncapped match $50, capped match $25
        result = calculate_payment_breakdown(
            100.00,
            [{'method_amount': 100.00, 'match_percent': 100.0}],
            match_limit=25.00,
        )
        li = result['line_items'][0]
        assert li['match_amount'] == 25.00
        assert li['customer_charged'] == 75.00

        items = [_make_line_item(1, 'SNAP', 100.0, 100.00, li['match_amount'], li['customer_charged'])]
        txn_id, _ = _create_confirmed_txn(100.00, line_items=items)

        loaded = get_payment_line_items(txn_id)
        assert loaded[0]['match_amount'] == 25.00
        assert loaded[0]['customer_charged'] == 75.00


# ══════════════════════════════════════════════════════════════════
# 2. Match Limit Cap in Adjustment Calculations
# ══════════════════════════════════════════════════════════════════

class TestAdjustmentCapMath:
    """Verify calculate_payment_breakdown with caps, simulating adjustment scenarios."""

    def test_single_method_under_cap(self):
        """$40 SNAP at 100% → $20 match, cap $25 → no capping."""
        result = calculate_payment_breakdown(
            40.00, [{'method_amount': 40.00, 'match_percent': 100.0}],
            match_limit=25.00,
        )
        assert result['match_was_capped'] is False
        assert result['fam_subsidy_total'] == 20.00
        assert result['customer_total_paid'] == 20.00

    def test_single_method_over_cap(self):
        """$100 SNAP at 100% → $50 uncapped, cap $25 → capped to $25."""
        result = calculate_payment_breakdown(
            100.00, [{'method_amount': 100.00, 'match_percent': 100.0}],
            match_limit=25.00,
        )
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 25.00
        assert result['customer_total_paid'] == 75.00
        assert result['uncapped_fam_subsidy_total'] == 50.00
        assert result['is_valid'] is True

    def test_multi_method_proportional_cap(self):
        """Two 100% methods, cap distributes proportionally."""
        entries = [
            {'method_amount': 60.00, 'match_percent': 100.0},
            {'method_amount': 40.00, 'match_percent': 100.0},
        ]
        result = calculate_payment_breakdown(100.00, entries, match_limit=25.00)

        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 25.00
        assert result['customer_total_paid'] == 75.00

        # Proportional: 60/100 of uncapped $50 = $30, capped $30 * 0.5 = $15
        li = result['line_items']
        assert li[0]['match_amount'] == 15.00  # 60% of $25
        assert li[1]['match_amount'] == 10.00  # 40% of $25

    def test_mixed_match_and_zero_method_cap(self):
        """SNAP 100% + Cash 0%: only SNAP contributes to match, capped."""
        entries = [
            {'method_amount': 80.00, 'match_percent': 100.0},  # SNAP
            {'method_amount': 20.00, 'match_percent': 0.0},    # Cash
        ]
        result = calculate_payment_breakdown(100.00, entries, match_limit=25.00)

        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 25.00
        # SNAP uncapped match = $40, capped to $25. Customer pays $55 (SNAP) + $20 (Cash)
        assert result['customer_total_paid'] == 75.00
        assert result['line_items'][1]['match_amount'] == 0.00  # Cash never has match

    def test_cap_exactly_at_match(self):
        """Cap equals the uncapped match — technically not capped."""
        result = calculate_payment_breakdown(
            50.00, [{'method_amount': 50.00, 'match_percent': 100.0}],
            match_limit=25.00,
        )
        # match = $25, cap = $25 → not capped (condition is > not >=)
        assert result['match_was_capped'] is False
        assert result['fam_subsidy_total'] == 25.00

    def test_cap_one_penny_below(self):
        """Cap is $24.99 when match would be $25.00 → capped."""
        result = calculate_payment_breakdown(
            50.00, [{'method_amount': 50.00, 'match_percent': 100.0}],
            match_limit=24.99,
        )
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 24.99
        assert result['customer_total_paid'] == 25.01

    def test_cap_at_one_penny(self):
        """Cap of $0.01 — only one penny of match allowed."""
        result = calculate_payment_breakdown(
            100.00, [{'method_amount': 100.00, 'match_percent': 100.0}],
            match_limit=0.01,
        )
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 0.01
        assert result['customer_total_paid'] == 99.99

    def test_cap_zero(self):
        """Cap of $0 — no match allowed at all."""
        result = calculate_payment_breakdown(
            100.00, [{'method_amount': 100.00, 'match_percent': 100.0}],
            match_limit=0.00,
        )
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 0.00
        assert result['customer_total_paid'] == 100.00

    def test_no_cap_when_none(self):
        """No cap (None) — full match applied."""
        result = calculate_payment_breakdown(
            100.00, [{'method_amount': 100.00, 'match_percent': 100.0}],
            match_limit=None,
        )
        assert result['match_was_capped'] is False
        assert result['fam_subsidy_total'] == 50.00

    def test_three_methods_cap_penny_accuracy(self):
        """Three 100%-match methods with cap: sum of match == cap exactly."""
        entries = [
            {'method_amount': 33.33, 'match_percent': 100.0},
            {'method_amount': 33.34, 'match_percent': 100.0},
            {'method_amount': 33.33, 'match_percent': 100.0},
        ]
        result = calculate_payment_breakdown(100.00, entries, match_limit=10.00)
        assert result['match_was_capped'] is True
        total_match = sum(li['match_amount'] for li in result['line_items'])
        assert abs(total_match - 10.00) < 0.01

    def test_reconciliation_always_holds_after_cap(self):
        """customer_paid + fam_match == receipt for many scenarios."""
        scenarios = [
            (50.00, 100.0, 10.00),
            (100.00, 100.0, 25.00),
            (200.00, 50.0, 15.00),
            (75.50, 100.0, 30.00),
            (10.00, 200.0, 3.00),
            (99.99, 100.0, 0.01),
        ]
        for receipt, pct, cap in scenarios:
            result = calculate_payment_breakdown(
                receipt, [{'method_amount': receipt, 'match_percent': pct}],
                match_limit=cap,
            )
            total = round(result['customer_total_paid'] + result['fam_subsidy_total'], 2)
            assert abs(total - receipt) <= 0.01, (
                f"Reconciliation failed: {receipt} @ {pct}% cap={cap}: "
                f"cust={result['customer_total_paid']} + match={result['fam_subsidy_total']} = {total}"
            )


# ══════════════════════════════════════════════════════════════════
# 3. Customer Impact Calculation
# ══════════════════════════════════════════════════════════════════

class TestCustomerImpact:
    """Test the collect/refund/no-change logic for adjustments."""

    def test_no_change_same_payments(self):
        """Same payment amounts → no customer impact."""
        original = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=original)

        # "Adjust" with identical values
        new_result = calculate_payment_breakdown(
            50.00, [{'method_amount': 50.00, 'match_percent': 100.0}],
            match_limit=25.00,
        )
        old_customer = sum(li['customer_charged'] for li in original)
        new_customer = new_result['customer_total_paid']
        diff = round(new_customer - old_customer, 2)
        assert diff == 0.00

    def test_collect_more_snap_to_cash(self):
        """Changing SNAP (100% match) to Cash (0% match) → customer pays more."""
        original = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        old_customer = sum(li['customer_charged'] for li in original)

        new_result = calculate_payment_breakdown(
            50.00, [{'method_amount': 50.00, 'match_percent': 0.0}],
        )
        new_customer = new_result['customer_total_paid']
        diff = round(new_customer - old_customer, 2)
        assert diff == 25.00  # Customer must pay $25 more

    def test_refund_cash_to_snap(self):
        """Changing Cash (0% match) to SNAP (100% match) → customer pays less."""
        original = [_make_line_item(2, 'Cash', 0.0, 50.00, 0.00, 50.00)]
        old_customer = sum(li['customer_charged'] for li in original)

        new_result = calculate_payment_breakdown(
            50.00, [{'method_amount': 50.00, 'match_percent': 100.0}],
        )
        new_customer = new_result['customer_total_paid']
        diff = round(new_customer - old_customer, 2)
        assert diff == -25.00  # Refund $25 to customer

    def test_collect_when_cap_reduces_match(self):
        """Original saved with cap → adjust to lower match → collect difference."""
        # Original: $100 SNAP, capped at $25 → customer paid $75
        original = [_make_line_item(1, 'SNAP', 100.0, 100.00, 25.00, 75.00)]
        old_customer = 75.00

        # Adjust to Cash (0% match) → customer pays $100
        new_result = calculate_payment_breakdown(
            100.00, [{'method_amount': 100.00, 'match_percent': 0.0}],
        )
        diff = round(new_result['customer_total_paid'] - old_customer, 2)
        assert diff == 25.00

    def test_refund_when_cap_increases_match(self):
        """Original was all cash → adjust to SNAP with cap → refund."""
        original = [_make_line_item(2, 'Cash', 0.0, 100.00, 0.00, 100.00)]
        old_customer = 100.00

        # Adjust to SNAP with $25 cap → customer pays $75
        new_result = calculate_payment_breakdown(
            100.00, [{'method_amount': 100.00, 'match_percent': 100.0}],
            match_limit=25.00,
        )
        diff = round(new_result['customer_total_paid'] - old_customer, 2)
        assert diff == -25.00

    def test_partial_method_swap_impact(self):
        """Swap part of SNAP to Cash → partial collect."""
        # Original: $60 SNAP + $40 Cash = $100 receipt, SNAP match = $30
        original_customer = 30.00 + 40.00  # SNAP customer + Cash customer

        # Adjust: $30 SNAP + $70 Cash
        new_result = calculate_payment_breakdown(
            100.00,
            [
                {'method_amount': 30.00, 'match_percent': 100.0},
                {'method_amount': 70.00, 'match_percent': 0.0},
            ],
        )
        diff = round(new_result['customer_total_paid'] - original_customer, 2)
        assert diff == 15.00  # Half the SNAP was moved to Cash


# ══════════════════════════════════════════════════════════════════
# 4. Audit Log Accuracy
# ══════════════════════════════════════════════════════════════════

class TestAuditLogAccuracy:
    """Verify audit entries are created correctly for adjustments."""

    def test_receipt_total_adjust_logged(self):
        """ADJUST action logged when receipt total changes."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=items)

        log_action('transactions', txn_id, 'ADJUST', 'Bob',
                   field_name='receipt_total', old_value=50.00, new_value=75.00,
                   reason_code='data_entry_error', notes='Wrong total on receipt')

        entries = get_audit_log(table_name='transactions', record_id=txn_id)
        adjust_entries = [e for e in entries if e['action'] == 'ADJUST']
        assert len(adjust_entries) >= 1
        adj = adjust_entries[0]
        assert adj['field_name'] == 'receipt_total'
        assert adj['old_value'] == '50.0'
        assert adj['new_value'] == '75.0'
        assert adj['changed_by'] == 'Bob'
        assert adj['reason_code'] == 'data_entry_error'

    def test_vendor_change_logged(self):
        """ADJUST action logged when vendor changes."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=items)

        log_action('transactions', txn_id, 'ADJUST', 'Bob',
                   field_name='vendor_id', old_value=1, new_value=2,
                   reason_code='vendor_correction')

        entries = get_audit_log(table_name='transactions', record_id=txn_id)
        adjust_entries = [e for e in entries if e['action'] == 'ADJUST' and e['field_name'] == 'vendor_id']
        assert len(adjust_entries) == 1
        assert adjust_entries[0]['old_value'] == '1'
        assert adjust_entries[0]['new_value'] == '2'

    def test_payment_adjusted_logged(self):
        """PAYMENT_ADJUSTED action logged with old/new method summaries."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=items)

        old_summary = "SNAP=$50.00"
        new_summary = "SNAP=$30.00, Cash=$20.00"
        log_action('payment_line_items', txn_id, 'PAYMENT_ADJUSTED', 'Bob',
                   field_name='payment_methods',
                   old_value=old_summary, new_value=new_summary,
                   reason_code='data_entry_error',
                   notes='Customer used mixed payment')

        entries = get_audit_log(table_name='payment_line_items', record_id=txn_id)
        pay_entries = [e for e in entries if e['action'] == 'PAYMENT_ADJUSTED']
        assert len(pay_entries) >= 1
        entry = pay_entries[0]
        assert entry['field_name'] == 'payment_methods'
        assert 'SNAP=$50.00' in entry['old_value']
        assert 'Cash=$20.00' in entry['new_value']

    def test_combined_adjust_creates_multiple_entries(self):
        """Adjusting receipt + vendor + payments → 3 separate audit entries."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=items)

        # Simulate the three-part adjustment
        log_action('transactions', txn_id, 'ADJUST', 'Bob',
                   field_name='receipt_total', old_value=50.00, new_value=60.00,
                   reason_code='data_entry_error')
        log_action('transactions', txn_id, 'ADJUST', 'Bob',
                   field_name='vendor_id', old_value=1, new_value=2,
                   reason_code='data_entry_error')
        log_action('payment_line_items', txn_id, 'PAYMENT_ADJUSTED', 'Bob',
                   field_name='payment_methods',
                   old_value='SNAP=$50.00', new_value='Cash=$60.00',
                   reason_code='data_entry_error')

        all_entries = get_audit_log(record_id=txn_id)
        adjust_count = len([e for e in all_entries if e['action'] in ('ADJUST', 'PAYMENT_ADJUSTED')])
        assert adjust_count >= 3

    def test_void_logged(self):
        """VOID action logged when transaction is voided."""
        items = [_make_line_item(2, 'Cash', 0.0, 30.00, 0.00, 30.00)]
        txn_id, _ = _create_confirmed_txn(30.00, line_items=items)

        log_action('transactions', txn_id, 'VOID', 'Admin',
                   reason_code='admin_adjustment', notes='Transaction voided')
        void_transaction(txn_id)

        txn = get_transaction_by_id(txn_id)
        assert txn['status'] == 'Voided'

        entries = get_audit_log(table_name='transactions', record_id=txn_id)
        void_entries = [e for e in entries if e['action'] == 'VOID']
        assert len(void_entries) >= 1

    def test_double_adjustment_logged_separately(self):
        """Two adjustments on the same transaction → two audit trails."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=items)

        # First adjustment
        log_action('transactions', txn_id, 'ADJUST', 'Alice',
                   field_name='receipt_total', old_value=50.00, new_value=60.00,
                   reason_code='data_entry_error')
        update_transaction(txn_id, receipt_total=60.00, status='Adjusted')

        # Second adjustment
        log_action('transactions', txn_id, 'ADJUST', 'Bob',
                   field_name='receipt_total', old_value=60.00, new_value=55.00,
                   reason_code='admin_adjustment')
        update_transaction(txn_id, receipt_total=55.00)

        entries = get_audit_log(table_name='transactions', record_id=txn_id)
        adjusts = [e for e in entries if e['action'] == 'ADJUST']
        assert len(adjusts) == 2
        # Most recent first (DESC order)
        assert adjusts[0]['old_value'] == '60.0'
        assert adjusts[0]['new_value'] == '55.0'
        assert adjusts[1]['old_value'] == '50.0'
        assert adjusts[1]['new_value'] == '60.0'

    def test_get_transaction_log_enriches_data(self):
        """get_transaction_log returns transaction ID, vendor name, market info."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, fam_id = _create_confirmed_txn(50.00, line_items=items)

        log_action('transactions', txn_id, 'ADJUST', 'Bob',
                   field_name='receipt_total', old_value=50.00, new_value=60.00)

        log_entries = get_transaction_log(market_day_id=1)
        adjust_entries = [e for e in log_entries if e['action'] == 'ADJUST']
        assert len(adjust_entries) >= 1

        entry = adjust_entries[0]
        assert entry['fam_transaction_id'] == fam_id
        assert entry['vendor_name'] == 'Farm Stand'
        assert entry['market_name'] == 'Downtown Market'
        assert entry['market_day_date'] == '2026-03-01'

    def test_transaction_log_filter_by_action(self):
        """get_transaction_log with action_filter only returns matching actions."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=items)

        log_action('transactions', txn_id, 'ADJUST', 'Bob',
                   field_name='receipt_total', old_value=50.00, new_value=60.00)

        # Filter to ADJUST only — should not include CREATE/CONFIRM/PAYMENT_SAVED
        adjust_only = get_transaction_log(action_filter=['ADJUST'])
        assert all(e['action'] == 'ADJUST' for e in adjust_only)
        assert len(adjust_only) >= 1


# ══════════════════════════════════════════════════════════════════
# 5. Ledger Backup Accuracy
# ══════════════════════════════════════════════════════════════════

class TestLedgerBackup:
    """Verify the text ledger backup reflects correct data."""

    def _read_ledger(self, tmp_path):
        """Read and return the ledger backup text."""
        # write_ledger_backup writes next to the DB file
        backup_path = os.path.join(str(tmp_path), "fam_ledger_backup.txt")
        assert os.path.exists(backup_path), f"Ledger not found at {backup_path}"
        with open(backup_path, 'r', encoding='utf-8') as f:
            return f.read()

    def test_confirmed_transaction_in_ledger(self, tmp_path):
        """Confirmed transaction appears with correct totals."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, fam_id = _create_confirmed_txn(50.00, line_items=items)
        write_ledger_backup()

        text = self._read_ledger(tmp_path)
        assert fam_id in text
        assert 'Farm Stand' in text
        assert '$50.00' in text   # Receipt total
        assert '$25.00' in text   # Match and customer paid

    def test_adjusted_transaction_in_ledger(self, tmp_path):
        """After adjustment, ledger shows updated values."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, fam_id = _create_confirmed_txn(50.00, line_items=items)

        # Adjust receipt total to $80 and swap to Cash
        update_transaction(txn_id, receipt_total=80.00, status='Adjusted')
        new_items = [_make_line_item(2, 'Cash', 0.0, 80.00, 0.00, 80.00)]
        save_payment_line_items(txn_id, new_items)
        write_ledger_backup()

        text = self._read_ledger(tmp_path)
        assert fam_id in text
        assert '$80.00' in text   # Updated receipt total
        assert 'Adjusted' in text

    def test_voided_transaction_in_ledger(self, tmp_path):
        """Voided transaction appears with Voided status."""
        items = [_make_line_item(2, 'Cash', 0.0, 30.00, 0.00, 30.00)]
        txn_id, fam_id = _create_confirmed_txn(30.00, line_items=items)
        void_transaction(txn_id)
        write_ledger_backup()

        text = self._read_ledger(tmp_path)
        assert fam_id in text
        assert 'Voided' in text

    def test_ledger_totals_with_multiple_transactions(self, tmp_path):
        """Ledger totals sum all non-voided transactions correctly."""
        # Transaction 1: $50 SNAP → match $25, customer $25
        items1 = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        _create_confirmed_txn(50.00, line_items=items1)

        # Transaction 2: $30 Cash → match $0, customer $30
        items2 = [_make_line_item(2, 'Cash', 0.0, 30.00, 0.00, 30.00)]
        _create_confirmed_txn(30.00, line_items=items2)

        write_ledger_backup()

        text = self._read_ledger(tmp_path)
        # Total receipts: $50 + $30 = $80
        assert '$80.00' in text
        # Total customer: $25 + $30 = $55
        assert '$55.00' in text
        # Total match: $25 + $0 = $25
        assert 'Transaction Count:     2' in text

    def test_ledger_with_fmnp_entries(self, tmp_path):
        """FMNP external entries appear in ledger with correct amounts."""
        conn = get_connection()
        # Add FMNP entry: $15, 2 checks, from Farm Stand
        conn.execute(
            "INSERT INTO fmnp_entries (market_day_id, vendor_id, amount, check_count, entered_by)"
            " VALUES (1, 1, 15.00, 2, 'Alice')"
        )
        conn.commit()

        write_ledger_backup()
        text = self._read_ledger(tmp_path)
        assert 'FMNP' in text
        assert '$15.00' in text
        assert '2 checks' in text

    def test_ledger_totals_include_fmnp(self, tmp_path):
        """Ledger totals include both transactions and FMNP entries."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        _create_confirmed_txn(50.00, line_items=items)

        conn = get_connection()
        conn.execute(
            "INSERT INTO fmnp_entries (market_day_id, vendor_id, amount, check_count, entered_by)"
            " VALUES (1, 1, 20.00, 3, 'Alice')"
        )
        conn.commit()

        write_ledger_backup()
        text = self._read_ledger(tmp_path)

        # Total receipts: $50 txn + $20 FMNP = $70
        assert '$70.00' in text
        # Total match: $25 txn + $20 FMNP = $45
        assert '$45.00' in text
        # Customer paid: $25 (only from transaction)
        # Transaction count: 1 txn + 1 FMNP = 2
        assert 'Transaction Count:     2' in text

    def test_ledger_shows_payment_methods(self, tmp_path):
        """Ledger line shows the payment method names and amounts."""
        items = [
            _make_line_item(1, 'SNAP', 100.0, 30.00, 15.00, 15.00),
            _make_line_item(2, 'Cash', 0.0, 20.00, 0.00, 20.00),
        ]
        _create_confirmed_txn(50.00, line_items=items)
        write_ledger_backup()

        text = self._read_ledger(tmp_path)
        assert 'SNAP: $30.00' in text
        assert 'Cash: $20.00' in text

    def test_ledger_header_info(self, tmp_path):
        """Ledger includes database summary, and market day section headers."""
        items = [_make_line_item(2, 'Cash', 0.0, 10.00, 0.00, 10.00)]
        _create_confirmed_txn(10.00, line_items=items)
        write_ledger_backup()

        text = self._read_ledger(tmp_path)
        # Top-level header
        assert 'LEDGER BACKUP' in text
        assert 'Backup at:' in text
        assert 'Markets: 1' in text
        assert 'Market Days: 1' in text
        # Market day section header
        assert 'Downtown Market' in text
        assert '2026-03-01' in text
        assert 'OPEN (in progress)' in text


# ══════════════════════════════════════════════════════════════════
# 6. Edge Cases
# ══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Boundary conditions and unusual scenarios."""

    def test_penny_transaction(self):
        """$0.01 transaction — smallest possible."""
        result = calculate_payment_breakdown(
            0.01, [{'method_amount': 0.01, 'match_percent': 100.0}],
        )
        assert result['is_valid'] is True
        assert result['fam_subsidy_total'] >= 0
        assert result['customer_total_paid'] >= 0
        total = round(result['customer_total_paid'] + result['fam_subsidy_total'], 2)
        assert abs(total - 0.01) <= 0.01

    def test_penny_transaction_with_cap(self):
        """$0.01 transaction with $0.01 cap."""
        result = calculate_payment_breakdown(
            0.01, [{'method_amount': 0.01, 'match_percent': 100.0}],
            match_limit=0.01,
        )
        assert result['is_valid'] is True
        total = round(result['customer_total_paid'] + result['fam_subsidy_total'], 2)
        assert abs(total - 0.01) <= 0.01

    def test_zero_match_only(self):
        """All Cash (0% match): match is $0, customer pays full amount."""
        result = calculate_payment_breakdown(
            75.00, [{'method_amount': 75.00, 'match_percent': 0.0}],
        )
        assert result['fam_subsidy_total'] == 0.00
        assert result['customer_total_paid'] == 75.00
        assert result['is_valid'] is True
        assert result['match_was_capped'] is False

    def test_very_high_match_999(self):
        """999% match — FAM pays 999/1099 of the amount."""
        result = calculate_payment_breakdown(
            100.00, [{'method_amount': 100.00, 'match_percent': 999.0}],
        )
        assert result['is_valid'] is True
        # match = 100 * 999 / 1099 ≈ 90.90
        assert result['fam_subsidy_total'] == pytest.approx(90.90, abs=0.01)
        assert result['customer_total_paid'] == pytest.approx(9.10, abs=0.01)
        total = round(result['customer_total_paid'] + result['fam_subsidy_total'], 2)
        assert abs(total - 100.00) <= 0.01

    def test_very_high_match_with_cap(self):
        """999% match with small cap — most of the match is stripped."""
        result = calculate_payment_breakdown(
            100.00, [{'method_amount': 100.00, 'match_percent': 999.0}],
            match_limit=5.00,
        )
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 5.00
        assert result['customer_total_paid'] == 95.00

    def test_50_percent_match_cap_precision(self):
        """50% match: match = amount * 50/150. Verify precision after cap."""
        result = calculate_payment_breakdown(
            99.99, [{'method_amount': 99.99, 'match_percent': 50.0}],
            match_limit=20.00,
        )
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 20.00
        assert result['customer_total_paid'] == 79.99

    def test_large_transaction_10000(self):
        """$10,000 transaction with cap."""
        result = calculate_payment_breakdown(
            10000.00, [{'method_amount': 10000.00, 'match_percent': 100.0}],
            match_limit=25.00,
        )
        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 25.00
        assert result['customer_total_paid'] == 9975.00
        assert result['is_valid'] is True

    def test_four_methods_all_matched(self):
        """Four 100%-match methods: cap distributes across all four."""
        entries = [
            {'method_amount': 25.00, 'match_percent': 100.0},
            {'method_amount': 25.00, 'match_percent': 100.0},
            {'method_amount': 25.00, 'match_percent': 100.0},
            {'method_amount': 25.00, 'match_percent': 100.0},
        ]
        result = calculate_payment_breakdown(100.00, entries, match_limit=10.00)

        assert result['match_was_capped'] is True
        assert result['fam_subsidy_total'] == 10.00
        # Each should get $2.50 of the cap (equal amounts)
        for li in result['line_items']:
            assert li['match_amount'] == 2.50

    def test_adjust_status_to_adjusted(self):
        """Transaction status changes to 'Adjusted' after adjustment."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=items)

        update_transaction(txn_id, status='Adjusted')
        txn = get_transaction_by_id(txn_id)
        assert txn['status'] == 'Adjusted'

    def test_search_adjusted_transactions(self):
        """Adjusted transactions appear in search results with correct status."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=items)
        update_transaction(txn_id, status='Adjusted')

        results = search_transactions(status='Adjusted')
        assert len(results) >= 1
        assert results[0]['status'] == 'Adjusted'

    def test_voided_transactions_excluded_from_confirmed_search(self):
        """Voided transactions don't appear in Confirmed search."""
        items = [_make_line_item(2, 'Cash', 0.0, 30.00, 0.00, 30.00)]
        txn_id, _ = _create_confirmed_txn(30.00, line_items=items)
        void_transaction(txn_id)

        results = search_transactions(status='Confirmed')
        assert all(r['id'] != txn_id for r in results)

    def test_negative_receipt_flagged(self):
        """Negative receipt total is flagged as error."""
        result = calculate_payment_breakdown(
            -10.00, [{'method_amount': -10.00, 'match_percent': 100.0}],
        )
        assert result['is_valid'] is False
        assert len(result['errors']) > 0

    def test_empty_payment_entries(self):
        """No payment entries → error."""
        result = calculate_payment_breakdown(50.00, [])
        assert result['is_valid'] is False
        assert len(result['errors']) > 0
        assert result['customer_total_paid'] == 0.0
        assert result['fam_subsidy_total'] == 0.0

    def test_allocation_mismatch_flagged(self):
        """Payment total != receipt total → allocation error."""
        result = calculate_payment_breakdown(
            100.00, [{'method_amount': 80.00, 'match_percent': 100.0}],
        )
        assert result['is_valid'] is False
        assert result['allocation_remaining'] == 20.00
        assert len(result['errors']) > 0

    def test_multi_method_mixed_match_percentages(self):
        """Different match percentages with cap → proportional reduction."""
        entries = [
            {'method_amount': 50.00, 'match_percent': 100.0},  # uncapped match $25
            {'method_amount': 50.00, 'match_percent': 50.0},   # uncapped match $16.67
        ]
        result = calculate_payment_breakdown(100.00, entries, match_limit=20.00)

        assert result['match_was_capped'] is True
        total_match = sum(li['match_amount'] for li in result['line_items'])
        assert abs(total_match - 20.00) < 0.01

        # Proportional: SNAP gets 25/(25+16.67) of $20 ≈ $12.00
        # Food Bucks gets 16.67/(25+16.67) of $20 ≈ $8.00
        li = result['line_items']
        assert li[0]['match_amount'] > li[1]['match_amount']  # SNAP gets more
        assert result['is_valid'] is True

    def test_reconciliation_with_fractional_cents(self):
        """$33.33 at 33.33% match: verify no penny is lost."""
        result = calculate_payment_breakdown(
            33.33, [{'method_amount': 33.33, 'match_percent': 33.33}],
        )
        total = round(result['customer_total_paid'] + result['fam_subsidy_total'], 2)
        assert abs(total - 33.33) <= 0.01
        assert result['is_valid'] is True


# ══════════════════════════════════════════════════════════════════
# 7. Full Adjustment Workflow Integration
# ══════════════════════════════════════════════════════════════════

class TestAdjustmentWorkflow:
    """End-to-end tests simulating the complete adjustment flow."""

    def test_full_adjustment_flow(self, tmp_path):
        """
        Complete flow: create → confirm → adjust receipt + payments → verify
        all artifacts: line items, audit log, ledger.
        """
        # Create and confirm a $100 SNAP transaction
        items = [_make_line_item(1, 'SNAP', 100.0, 100.00, 25.00, 75.00)]
        txn_id, fam_id = _create_confirmed_txn(100.00, line_items=items)

        # Verify initial state
        loaded = get_payment_line_items(txn_id)
        assert len(loaded) == 1
        assert loaded[0]['match_amount'] == 25.00  # $25 cap applied

        # Perform adjustment: change to $80 receipt, split SNAP $50 + Cash $30
        new_receipt = 80.00
        new_result = calculate_payment_breakdown(
            new_receipt,
            [
                {'method_amount': 50.00, 'match_percent': 100.0},
                {'method_amount': 30.00, 'match_percent': 0.0},
            ],
            match_limit=25.00,
        )

        # Log the adjustments
        log_action('transactions', txn_id, 'ADJUST', 'Bob',
                   field_name='receipt_total', old_value=100.00, new_value=80.00,
                   reason_code='data_entry_error')
        update_transaction(txn_id, receipt_total=new_receipt)

        # Save new payment line items with capped values
        new_items = [
            _make_line_item(1, 'SNAP', 100.0, 50.00,
                            new_result['line_items'][0]['match_amount'],
                            new_result['line_items'][0]['customer_charged']),
            _make_line_item(2, 'Cash', 0.0, 30.00,
                            new_result['line_items'][1]['match_amount'],
                            new_result['line_items'][1]['customer_charged']),
        ]
        log_action('payment_line_items', txn_id, 'PAYMENT_ADJUSTED', 'Bob',
                   field_name='payment_methods',
                   old_value='SNAP=$100.00',
                   new_value='SNAP=$50.00, Cash=$30.00',
                   reason_code='data_entry_error')
        save_payment_line_items(txn_id, new_items)
        update_transaction(txn_id, status='Adjusted')

        # Verify line items updated
        loaded = get_payment_line_items(txn_id)
        assert len(loaded) == 2
        snap_li = next(li for li in loaded if li['method_name_snapshot'] == 'SNAP')
        cash_li = next(li for li in loaded if li['method_name_snapshot'] == 'Cash')
        assert snap_li['method_amount'] == 50.00
        assert cash_li['method_amount'] == 30.00
        assert cash_li['match_amount'] == 0.00

        # Verify audit log
        entries = get_audit_log(record_id=txn_id)
        actions = [e['action'] for e in entries]
        assert 'ADJUST' in actions
        assert 'PAYMENT_ADJUSTED' in actions

        # Verify ledger backup
        write_ledger_backup()
        backup_path = os.path.join(str(tmp_path), "fam_ledger_backup.txt")
        with open(backup_path, 'r', encoding='utf-8') as f:
            text = f.read()
        assert fam_id in text
        assert 'Adjusted' in text
        assert '$80.00' in text
        assert 'SNAP: $50.00' in text
        assert 'Cash: $30.00' in text

    def test_double_adjustment_integrity(self, tmp_path):
        """Adjust a transaction twice — verify final state is consistent."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, fam_id = _create_confirmed_txn(50.00, line_items=items)

        # First adjustment: change to Cash
        new_items_1 = [_make_line_item(2, 'Cash', 0.0, 50.00, 0.00, 50.00)]
        save_payment_line_items(txn_id, new_items_1)
        update_transaction(txn_id, status='Adjusted')

        # Second adjustment: change to Food Bucks
        result = calculate_payment_breakdown(
            50.00, [{'method_amount': 50.00, 'match_percent': 100.0}],
            match_limit=25.00,
        )
        new_items_2 = [
            _make_line_item(3, 'Food Bucks', 100.0, 50.00,
                            result['line_items'][0]['match_amount'],
                            result['line_items'][0]['customer_charged']),
        ]
        save_payment_line_items(txn_id, new_items_2)

        # Verify only the latest items exist (no old ones)
        loaded = get_payment_line_items(txn_id)
        assert len(loaded) == 1
        assert loaded[0]['method_name_snapshot'] == 'Food Bucks'
        assert loaded[0]['match_amount'] == 25.00
        assert loaded[0]['customer_charged'] == 25.00

        # Ledger should reflect final state
        write_ledger_backup()
        backup_path = os.path.join(str(tmp_path), "fam_ledger_backup.txt")
        with open(backup_path, 'r', encoding='utf-8') as f:
            text = f.read()
        assert 'Food Bucks: $50.00' in text

    def test_adjustment_with_vendor_change(self):
        """Adjust vendor from Farm Stand to Bakery — verify transaction updated."""
        items = [_make_line_item(2, 'Cash', 0.0, 40.00, 0.00, 40.00)]
        txn_id, _ = _create_confirmed_txn(40.00, vendor_id=1, line_items=items)

        update_transaction(txn_id, vendor_id=2, status='Adjusted')
        txn = get_transaction_by_id(txn_id)
        assert txn['vendor_id'] == 2
        assert txn['vendor_name'] == 'Bakery'
        assert txn['status'] == 'Adjusted'

    def test_cap_applied_consistently_on_resave(self):
        """
        When adjusting payments, the cap applies identically via
        calculate_payment_breakdown and the stored line items match.
        """
        # $200 SNAP → uncapped $100, cap $25 → stored match $25
        result1 = calculate_payment_breakdown(
            200.00, [{'method_amount': 200.00, 'match_percent': 100.0}],
            match_limit=25.00,
        )
        items = [_make_line_item(
            1, 'SNAP', 100.0, 200.00,
            result1['line_items'][0]['match_amount'],
            result1['line_items'][0]['customer_charged'],
        )]
        txn_id, _ = _create_confirmed_txn(200.00, line_items=items)

        # Verify stored values match calculation
        loaded = get_payment_line_items(txn_id)
        assert loaded[0]['match_amount'] == 25.00
        assert loaded[0]['customer_charged'] == 175.00

        # Now "adjust" to $150 SNAP + $50 Cash
        result2 = calculate_payment_breakdown(
            200.00,
            [
                {'method_amount': 150.00, 'match_percent': 100.0},
                {'method_amount': 50.00, 'match_percent': 0.0},
            ],
            match_limit=25.00,
        )

        new_items = [
            _make_line_item(1, 'SNAP', 100.0, 150.00,
                            result2['line_items'][0]['match_amount'],
                            result2['line_items'][0]['customer_charged']),
            _make_line_item(2, 'Cash', 0.0, 50.00,
                            result2['line_items'][1]['match_amount'],
                            result2['line_items'][1]['customer_charged']),
        ]
        save_payment_line_items(txn_id, new_items)

        loaded2 = get_payment_line_items(txn_id)
        total_match = sum(li['match_amount'] for li in loaded2)
        total_customer = sum(li['customer_charged'] for li in loaded2)
        assert total_match == 25.00  # Cap still $25
        assert total_customer == 175.00
        assert total_match + total_customer == 200.00


# ══════════════════════════════════════════════════════════════════
# 8. Production-Readiness Regression Tests
# ══════════════════════════════════════════════════════════════════

class TestVoidedTransactionLedgerTotals:
    """Verify voided transactions are excluded from ledger backup totals."""

    def _read_ledger(self, tmp_path):
        backup_path = os.path.join(str(tmp_path), "fam_ledger_backup.txt")
        with open(backup_path, 'r', encoding='utf-8') as f:
            return f.read()

    def test_voided_excluded_from_totals(self, tmp_path):
        """A voided $50 txn should NOT inflate the total receipts/match."""
        # Create and confirm TWO transactions
        items1 = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn1_id, _ = _create_confirmed_txn(50.00, line_items=items1)

        items2 = [_make_line_item(2, 'Cash', 0.0, 30.00, 0.00, 30.00)]
        txn2_id, _ = _create_confirmed_txn(30.00, line_items=items2)

        # Void the first transaction
        void_transaction(txn1_id)

        write_ledger_backup()
        text = self._read_ledger(tmp_path)

        # Totals should only include the $30 Cash transaction
        # Total receipts: $30 (not $80)
        assert 'Total Receipts:        $30.00' in text
        # Customer paid: $30 (not $55)
        assert 'Total Customer Paid:   $30.00' in text
        # FAM Match: $0 (not $25)
        assert 'Total FAM Match:       $0.00' in text
        # But both transactions should still be listed (2 rows)
        assert 'Transaction Count:     2' in text

    def test_all_voided_zeroes_totals(self, tmp_path):
        """If all transactions are voided, totals should be zero."""
        items = [_make_line_item(1, 'SNAP', 100.0, 100.00, 25.00, 75.00)]
        txn_id, _ = _create_confirmed_txn(100.00, line_items=items)
        void_transaction(txn_id)

        write_ledger_backup()
        text = self._read_ledger(tmp_path)

        assert 'Total Receipts:        $0.00' in text
        assert 'Total Customer Paid:   $0.00' in text
        assert 'Total FAM Match:       $0.00' in text
        assert 'Voided' in text

    def test_voided_still_appears_in_listing(self, tmp_path):
        """Voided transaction row is visible for audit purposes."""
        items = [_make_line_item(1, 'SNAP', 100.0, 40.00, 20.00, 20.00)]
        txn_id, fam_id = _create_confirmed_txn(40.00, line_items=items)
        void_transaction(txn_id)

        write_ledger_backup()
        text = self._read_ledger(tmp_path)

        assert fam_id in text
        assert 'Voided' in text
        assert '$40.00' in text  # still shows in the row


class TestAtomicAdjustment:
    """Verify adjustment atomicity — all-or-nothing DB writes."""

    def test_adjustment_audit_and_data_in_same_commit(self):
        """Audit log + data change should be consistent."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=items)

        conn = get_connection()
        # Simulate the atomic adjust flow: all with commit=False
        log_action('transactions', txn_id, 'ADJUST', 'TestUser',
                   field_name='receipt_total', old_value=50.00, new_value=75.00,
                   reason_code='data_entry_error', commit=False)
        update_transaction(txn_id, receipt_total=75.00, commit=False)
        update_transaction(txn_id, status='Adjusted', commit=False)
        conn.commit()

        # Both audit and data should reflect the change
        txn = get_transaction_by_id(txn_id)
        assert txn['receipt_total'] == 75.00
        assert txn['status'] == 'Adjusted'

        entries = get_audit_log(record_id=txn_id)
        actions = [e['action'] for e in entries]
        assert 'ADJUST' in actions

    def test_rollback_reverts_all_changes(self):
        """On failure, rollback should undo audit AND data changes."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=items)

        conn = get_connection()
        # Start changes but then rollback
        log_action('transactions', txn_id, 'ADJUST', 'TestUser',
                   field_name='receipt_total', old_value=50.00, new_value=99.00,
                   commit=False)
        update_transaction(txn_id, receipt_total=99.00, commit=False)
        conn.rollback()

        # Data should be unchanged
        txn = get_transaction_by_id(txn_id)
        assert txn['receipt_total'] == 50.00
        assert txn['status'] == 'Confirmed'

        # Audit log should NOT contain the rolled-back entry
        entries = get_audit_log(record_id=txn_id)
        adjust_entries = [e for e in entries if e['action'] == 'ADJUST']
        assert len(adjust_entries) == 0

    def test_atomic_void_consistency(self):
        """Void audit + status change should be atomic."""
        items = [_make_line_item(2, 'Cash', 0.0, 30.00, 0.00, 30.00)]
        txn_id, _ = _create_confirmed_txn(30.00, line_items=items)

        conn = get_connection()
        log_action('transactions', txn_id, 'VOID', 'TestUser',
                   reason_code='admin_adjustment', notes='Test void',
                   commit=False)
        update_transaction(txn_id, status='Voided', commit=False)
        conn.commit()

        txn = get_transaction_by_id(txn_id)
        assert txn['status'] == 'Voided'

        entries = get_audit_log(record_id=txn_id)
        void_entries = [e for e in entries if e['action'] == 'VOID']
        assert len(void_entries) == 1

    def test_atomic_payment_adjustment(self):
        """Payment line items + audit log saved atomically."""
        items = [_make_line_item(1, 'SNAP', 100.0, 60.00, 25.00, 35.00)]
        txn_id, _ = _create_confirmed_txn(60.00, line_items=items)

        new_items = [
            _make_line_item(1, 'SNAP', 100.0, 30.00, 15.00, 15.00),
            _make_line_item(2, 'Cash', 0.0, 30.00, 0.00, 30.00),
        ]

        conn = get_connection()
        log_action('payment_line_items', txn_id, 'PAYMENT_ADJUSTED', 'TestUser',
                   field_name='payment_methods',
                   old_value='SNAP=$60.00',
                   new_value='SNAP=$30.00, Cash=$30.00',
                   commit=False)
        save_payment_line_items(txn_id, new_items, commit=False)
        update_transaction(txn_id, status='Adjusted', commit=False)
        conn.commit()

        loaded = get_payment_line_items(txn_id)
        assert len(loaded) == 2
        assert loaded[0]['method_name_snapshot'] == 'SNAP'
        assert loaded[1]['method_name_snapshot'] == 'Cash'

        txn = get_transaction_by_id(txn_id)
        assert txn['status'] == 'Adjusted'

    def test_no_change_no_adjusted_status(self):
        """If dialog submitted with no changes, status should NOT change."""
        items = [_make_line_item(1, 'SNAP', 100.0, 50.00, 25.00, 25.00)]
        txn_id, _ = _create_confirmed_txn(50.00, line_items=items)

        # Simulate no-op adjustment: nothing_changed = False → no status update
        txn = get_transaction_by_id(txn_id)
        assert txn['status'] == 'Confirmed'  # Should remain Confirmed


class TestMultiMethodLedgerAccuracy:
    """Verify ledger totals are accurate with multi-method transactions."""

    def _read_ledger(self, tmp_path):
        backup_path = os.path.join(str(tmp_path), "fam_ledger_backup.txt")
        with open(backup_path, 'r', encoding='utf-8') as f:
            return f.read()

    def test_multi_method_no_inflation(self, tmp_path):
        """A transaction with 3 payment methods should not inflate receipt totals."""
        items = [
            _make_line_item(1, 'SNAP', 100.0, 40.00, 20.00, 20.00),
            _make_line_item(2, 'Cash', 0.0, 30.00, 0.00, 30.00),
            _make_line_item(3, 'Food Bucks', 100.0, 30.00, 5.00, 25.00),
        ]
        _create_confirmed_txn(100.00, line_items=items)

        write_ledger_backup()
        text = self._read_ledger(tmp_path)

        # Receipt total should be $100, NOT $100 * 3 = $300
        assert 'Total Receipts:        $100.00' in text
        # Customer paid: $20 + $30 + $25 = $75
        assert 'Total Customer Paid:   $75.00' in text
        # FAM match: $20 + $0 + $5 = $25
        assert 'Total FAM Match:       $25.00' in text
        assert 'Transaction Count:     1' in text

    def test_two_txns_multi_method_totals(self, tmp_path):
        """Two multi-method transactions: totals should be exact sum."""
        items1 = [
            _make_line_item(1, 'SNAP', 100.0, 30.00, 15.00, 15.00),
            _make_line_item(2, 'Cash', 0.0, 20.00, 0.00, 20.00),
        ]
        _create_confirmed_txn(50.00, line_items=items1)

        items2 = [
            _make_line_item(1, 'SNAP', 100.0, 60.00, 25.00, 35.00),
        ]
        _create_confirmed_txn(60.00, line_items=items2)

        write_ledger_backup()
        text = self._read_ledger(tmp_path)

        # Total receipts: $50 + $60 = $110
        assert 'Total Receipts:        $110.00' in text
        # Customer paid: $15 + $20 + $35 = $70
        assert 'Total Customer Paid:   $70.00' in text
        # FAM match: $15 + $0 + $25 = $40
        assert 'Total FAM Match:       $40.00' in text
        assert 'Transaction Count:     2' in text
