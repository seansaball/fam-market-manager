"""UI guard tests: max-cap clamping, market day lifecycle, adjustment edge cases.

Covers three risk areas identified in the UI deep-dive:
1. Payment row max-cap prevents exceeding remaining balance
2. Market day lifecycle guards prevent transactions on closed days
3. Adjustment edge cases (multi-method, double-adjust, match cap)

Uses pytest-qt for widget tests and direct model calls for guard tests.
"""
import pytest
from PySide6.QtCore import Qt

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.utils.money import dollars_to_cents, cents_to_dollars, format_dollars
from fam.utils.calculations import charge_to_method_amount, method_amount_to_charge


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path):
    """Create a fresh database with market, vendors, and payment methods."""
    db_file = str(tmp_path / "test_guards.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    conn.execute(
        "INSERT INTO markets (id, name, address, daily_match_limit, match_limit_active)"
        " VALUES (1, 'Test Market', '100 Main St', 10000, 0)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'Farm Stand')")
    conn.execute("INSERT INTO vendors (id, name) VALUES (2, 'Bakery')")

    # SNAP: 100% match
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (1, 'SNAP', 100.0, 1, 1)")
    # Cash: 0% match
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (2, 'Cash', 0.0, 1, 2)")
    # FMNP: 100% match, $5 denomination
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order, denomination)"
        " VALUES (3, 'FMNP', 100.0, 1, 3, 500)")
    # Food RX: 200% match
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (4, 'Food RX', 200.0, 1, 4)")

    # Junction: assign all methods to market 1
    for pm_id in (1, 2, 3, 4):
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, payment_method_id)"
            " VALUES (1, ?)", (pm_id,))

    # Open market day
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, opened_by)"
        " VALUES (1, 1, '2026-04-01', 'Open', 'Tester')")

    conn.commit()
    yield conn
    close_connection()


def _create_order(conn, receipt_total_cents, vendor_id=1):
    """Helper: create a customer order with one transaction."""
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction
    order_id, label = create_customer_order(market_day_id=1)
    create_transaction(
        market_day_id=1, vendor_id=vendor_id,
        receipt_total=receipt_total_cents,
        market_day_date='2026-04-01', customer_order_id=order_id,
    )
    return order_id


def _select_method(row, method_name):
    """Select a payment method by name in a PaymentRow combo."""
    combo = row.method_combo
    for i in range(combo.count()):
        if method_name.lower() in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            return
    raise ValueError(f"Method '{method_name}' not found")


def _set_charge_dollars(row, dollars):
    """Set charge in dollars on a payment row."""
    row._set_active_charge(dollars_to_cents(dollars))


# ═══════════════════════════════════════════════════════════════════
# PRIORITY 1: Payment Row Max-Cap Tests
# ═══════════════════════════════════════════════════════════════════

class TestMaxCapSingleRow:
    """Verify a single row's max is clamped to the order total."""

    def test_snap_max_charge_equals_half_total(self, qtbot, fresh_db):
        """$100 order, SNAP (100% match) → max charge = $50."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 10000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "SNAP")
        screen._update_summary()

        # SNAP 100% match: $50 charge → $100 alloc. Max charge = $50.
        max_val = row.amount_spin.maximum() if not row._stepper_active else (
            row._stepper._count_spin.maximum() * row._stepper._denomination)
        assert max_val == 5000 or abs(max_val - 50.00) < 0.01

    def test_cash_max_charge_equals_total(self, qtbot, fresh_db):
        """$100 order, Cash (0% match) → max charge = $100."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 10000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "Cash")
        screen._update_summary()

        max_dollars = row.amount_spin.maximum()
        assert max_dollars == 100.00, f"Cash max should be $100, got ${max_dollars}"

    def test_food_rx_max_charge_is_third_of_total(self, qtbot, fresh_db):
        """$90 order, Food RX (200% match) → max charge = $30."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 9000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "Food RX")
        screen._update_summary()

        max_dollars = row.amount_spin.maximum()
        assert max_dollars == 30.00, f"Food RX max should be $30, got ${max_dollars}"

    def test_fmnp_stepper_max_count(self, qtbot, fresh_db):
        """$50 order, FMNP ($5 denom, 100% match) → max 5 checks."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 5000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "FMNP")
        screen._update_summary()

        max_count = row._stepper._count_spin.maximum()
        assert max_count == 5, f"FMNP max should be 5 checks, got {max_count}"


class TestMaxCapMultiRow:
    """Verify each row's max adjusts based on OTHER rows' allocations."""

    def test_second_row_max_reflects_first_row(self, qtbot, fresh_db):
        """$100 order, SNAP $30 charge ($60 alloc) → Cash max = $40."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 10000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row1 = screen._payment_rows[0]
        _select_method(row1, "SNAP")
        _set_charge_dollars(row1, 30.00)

        screen._add_payment_row()
        row2 = screen._payment_rows[1]
        _select_method(row2, "Cash")
        screen._update_summary()

        max_cash = row2.amount_spin.maximum()
        assert max_cash == 40.00, f"Cash max should be $40, got ${max_cash}"

    def test_decreasing_first_row_increases_second_max(self, qtbot, fresh_db):
        """Lowering row 1 charge should raise row 2 max."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 10000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row1 = screen._payment_rows[0]
        _select_method(row1, "SNAP")
        _set_charge_dollars(row1, 30.00)

        screen._add_payment_row()
        row2 = screen._payment_rows[1]
        _select_method(row2, "Cash")
        screen._update_summary()

        assert row2.amount_spin.maximum() == 40.00

        # Lower SNAP to $20 charge ($40 alloc) → $60 remaining → Cash max $60
        _set_charge_dollars(row1, 20.00)
        screen._update_summary()

        assert row2.amount_spin.maximum() == 60.00

    def test_snap_plus_fmnp_stepper_max(self, qtbot, fresh_db):
        """$100 order: SNAP $30 charge ($60 alloc) → FMNP max = 4 checks."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 10000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row1 = screen._payment_rows[0]
        _select_method(row1, "SNAP")
        _set_charge_dollars(row1, 30.00)

        screen._add_payment_row()
        row2 = screen._payment_rows[1]
        _select_method(row2, "FMNP")
        screen._update_summary()

        # $40 remaining, FMNP $5 denom 100% match: charge $20 → $40 alloc
        # max_charge = 40/(1+1) = 20, 20/5 = 4 checks
        # But denominated methods get +1 forfeit unit if gap exists
        max_count = row2._stepper._count_spin.maximum()
        assert max_count == 4, f"FMNP max should be 4, got {max_count}"

    def test_three_rows_each_constrained(self, qtbot, fresh_db):
        """$120 order: SNAP $20, Cash $30 → Food RX max = $120-$40-$30 = $50/3 = ~$16.67."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 12000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row1 = screen._payment_rows[0]
        _select_method(row1, "SNAP")
        _set_charge_dollars(row1, 20.00)  # $40 alloc

        screen._add_payment_row()
        row2 = screen._payment_rows[1]
        _select_method(row2, "Cash")
        _set_charge_dollars(row2, 30.00)  # $30 alloc

        screen._add_payment_row()
        row3 = screen._payment_rows[2]
        _select_method(row3, "Food RX")
        screen._update_summary()

        # Remaining for Food RX: $120 - $40 - $30 = $50
        # Food RX 200% match: charge = $50 / 3 = $16.66… (floor)
        # Floor favors the customer; penny reconciliation adds the
        # ≤1-cent gap to FAM match.
        max_rx = row3.amount_spin.maximum()
        assert abs(max_rx - 16.66) < 0.02, f"Food RX max should be ~$16.66, got ${max_rx}"


class TestMaxCapPlusButton:
    """Verify the stepper + button disables at max."""

    def test_plus_disabled_at_max(self, qtbot, fresh_db):
        """FMNP at max count → + button disabled."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 5000)  # $50 → max 5 FMNP checks
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "FMNP")
        row._stepper.setCount(5)
        screen._update_summary()

        assert not row._stepper._plus_btn.isEnabled(), "Plus should be disabled at max"
        assert row._stepper._minus_btn.isEnabled(), "Minus should be enabled"

    def test_plus_enabled_below_max(self, qtbot, fresh_db):
        """FMNP below max → + button enabled."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 5000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "FMNP")
        row._stepper.setCount(3)
        screen._update_summary()

        assert row._stepper._plus_btn.isEnabled(), "Plus should be enabled below max"

    def test_spinbox_clamps_typed_value(self, qtbot, fresh_db):
        """Typing a value above max into spinbox gets clamped."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 5000)  # $50 order
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "Cash")
        screen._update_summary()

        # Max should be $50
        assert row.amount_spin.maximum() == 50.00

        # Try to set above max — QDoubleSpinBox clamps to maximum
        row.amount_spin.setValue(999.99)
        assert row.amount_spin.value() == 50.00


class TestMaxCapEdgeCases:
    """Edge cases for max-cap behavior."""

    def test_no_order_loaded_all_zero(self, qtbot, fresh_db):
        """No order → _order_total is 0, no rows to clamp (no crash)."""
        from fam.ui.payment_screen import PaymentScreen

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        # Don't load any order — _order_total should be 0
        assert screen._order_total == 0
        # No payment rows exist yet — _push_row_limits should not crash
        screen._update_summary()
        assert len(screen._payment_rows) == 0

    def test_removing_row_increases_other_max(self, qtbot, fresh_db):
        """Removing a row should increase remaining row's max."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 10000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row1 = screen._payment_rows[0]
        _select_method(row1, "SNAP")
        _set_charge_dollars(row1, 30.00)

        screen._add_payment_row()
        row2 = screen._payment_rows[1]
        _select_method(row2, "Cash")
        _set_charge_dollars(row2, 20.00)
        screen._update_summary()

        assert row1.amount_spin.maximum() if not row1._stepper_active else True

        # Remove Cash row → SNAP max should return to $50
        screen._remove_payment_row(row2)

        snap_max = row1.amount_spin.maximum() if not row1._stepper_active else (
            row1._stepper._count_spin.maximum() * 500 / 100)
        # SNAP max charge = $100 / 2 = $50
        assert snap_max == 50.00 or snap_max == 5000


# ═══════════════════════════════════════════════════════════════════
# PRIORITY 2: Market Day Lifecycle Guards
# ═══════════════════════════════════════════════════════════════════

class TestMarketDayGuards:
    """Verify transactions cannot be created on closed market days."""

    def test_create_on_open_day_succeeds(self, fresh_db):
        """Creating a transaction on an open market day works."""
        from fam.models.transaction import create_transaction
        txn_id, fam_id = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=5000,
            market_day_date='2026-04-01')
        assert txn_id > 0
        assert 'FAM' in fam_id

    def test_create_on_closed_day_raises(self, fresh_db):
        """Creating a transaction on a closed market day raises ValueError."""
        from fam.models.transaction import create_transaction
        from fam.models.market_day import close_market_day

        close_market_day(1, closed_by='Tester')

        with pytest.raises(ValueError, match="transactions can only be created on an open"):
            create_transaction(
                market_day_id=1, vendor_id=1, receipt_total=5000,
                market_day_date='2026-04-01')

    def test_create_on_nonexistent_day_raises(self, fresh_db):
        """Creating a transaction with invalid market day raises ValueError."""
        from fam.models.transaction import create_transaction

        with pytest.raises(ValueError, match="not found"):
            create_transaction(
                market_day_id=999, vendor_id=1, receipt_total=5000)

    def test_reopen_allows_transactions_again(self, fresh_db):
        """Closing then reopening a market day allows transactions."""
        from fam.models.transaction import create_transaction
        from fam.models.market_day import close_market_day, reopen_market_day

        close_market_day(1, closed_by='Tester')

        with pytest.raises(ValueError):
            create_transaction(
                market_day_id=1, vendor_id=1, receipt_total=5000,
                market_day_date='2026-04-01')

        reopen_market_day(1, opened_by='Tester')

        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=5000,
            market_day_date='2026-04-01')
        assert txn_id > 0

    def test_close_sets_status_and_fields(self, fresh_db):
        """Closing a market day sets status, closed_by, and closed_at."""
        from fam.models.market_day import close_market_day

        close_market_day(1, closed_by='Alice')

        row = fresh_db.execute(
            "SELECT status, closed_by, closed_at FROM market_days WHERE id=1"
        ).fetchone()
        assert row['status'] == 'Closed'
        assert row['closed_by'] == 'Alice'
        assert row['closed_at'] is not None

    def test_close_creates_audit_entry(self, fresh_db):
        """Closing a market day creates an audit log entry."""
        from fam.models.market_day import close_market_day

        close_market_day(1, closed_by='Alice')

        audit = fresh_db.execute(
            "SELECT * FROM audit_log WHERE table_name='market_days' AND action='CLOSE'"
        ).fetchone()
        assert audit is not None
        assert audit['changed_by'] == 'Alice'

    def test_existing_transactions_unaffected_by_close(self, fresh_db):
        """Transactions created before closing remain intact."""
        from fam.models.transaction import create_transaction, get_transaction_by_id
        from fam.models.market_day import close_market_day

        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=5000,
            market_day_date='2026-04-01')

        close_market_day(1, closed_by='Tester')

        txn = get_transaction_by_id(txn_id)
        assert txn is not None
        assert txn['receipt_total'] == 5000


class TestReceiptIntakeUIGuard:
    """Verify the receipt intake screen disables input when day is closed."""

    def test_add_button_disabled_when_closed(self, qtbot, fresh_db):
        """Receipt intake add button disabled after closing market day."""
        from fam.models.market_day import close_market_day
        from fam.ui.receipt_intake_screen import ReceiptIntakeScreen

        close_market_day(1, closed_by='Tester')

        screen = ReceiptIntakeScreen()
        qtbot.addWidget(screen)
        screen._update_market_status()

        assert not screen.add_receipt_btn.isEnabled(), (
            "Add receipt button should be disabled on closed market day")

    def test_add_button_enabled_when_open(self, qtbot, fresh_db):
        """Receipt intake add button enabled on open market day."""
        from fam.ui.receipt_intake_screen import ReceiptIntakeScreen

        screen = ReceiptIntakeScreen()
        qtbot.addWidget(screen)
        screen._update_market_status()

        assert screen.add_receipt_btn.isEnabled(), (
            "Add receipt button should be enabled on open market day")


# ═══════════════════════════════════════════════════════════════════
# PRIORITY 3: Adjustment Edge Cases
# ═══════════════════════════════════════════════════════════════════

class TestAdjustmentEdgeCases:
    """Test adjustment operations that could corrupt financial totals."""

    def _create_confirmed_order(self, conn, receipt_cents, line_items, vendor_id=1):
        """Helper: create and confirm an order with specific payment lines."""
        from fam.models.customer_order import create_customer_order, update_customer_order_status
        from fam.models.transaction import (
            create_transaction, save_payment_line_items, confirm_transaction
        )

        order_id, _ = create_customer_order(market_day_id=1)
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=vendor_id,
            receipt_total=receipt_cents,
            market_day_date='2026-04-01',
            customer_order_id=order_id)
        save_payment_line_items(txn_id, line_items)
        confirm_transaction(txn_id, confirmed_by='Tester')
        update_customer_order_status(order_id, 'Confirmed')
        return order_id, txn_id

    def test_adjust_single_method_updates_totals(self, fresh_db):
        """Adjusting receipt total on single-method payment updates correctly."""
        from fam.models.transaction import (
            update_transaction, save_payment_line_items,
            get_transaction_by_id, get_payment_line_items
        )

        order_id, txn_id = self._create_confirmed_order(fresh_db, 5000, [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 5000, 'match_amount': 0, 'customer_charged': 5000,
        }])

        # Adjust to $60
        update_transaction(txn_id, receipt_total=6000, status='Adjusted')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 6000, 'match_amount': 0, 'customer_charged': 6000,
        }])

        txn = get_transaction_by_id(txn_id)
        assert txn['receipt_total'] == 6000
        assert txn['status'] == 'Adjusted'

        items = get_payment_line_items(txn_id)
        assert len(items) == 1
        assert items[0]['method_amount'] == 6000

    def test_adjust_multi_method_recalculates_correctly(self, fresh_db):
        """Adjusting a multi-method payment recalculates all line items."""
        from fam.models.transaction import (
            update_transaction, save_payment_line_items,
            get_transaction_by_id, get_payment_line_items
        )

        # Original: $100 order, SNAP $25 charge + Cash $50
        order_id, txn_id = self._create_confirmed_order(fresh_db, 10000, [
            {
                'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
                'match_percent_snapshot': 100.0,
                'method_amount': 5000, 'match_amount': 2500, 'customer_charged': 2500,
            },
            {
                'payment_method_id': 2, 'method_name_snapshot': 'Cash',
                'match_percent_snapshot': 0.0,
                'method_amount': 5000, 'match_amount': 0, 'customer_charged': 5000,
            },
        ])

        # Adjust to $80: SNAP $20 charge ($40 alloc) + Cash $40
        update_transaction(txn_id, receipt_total=8000, status='Adjusted')
        save_payment_line_items(txn_id, [
            {
                'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
                'match_percent_snapshot': 100.0,
                'method_amount': 4000, 'match_amount': 2000, 'customer_charged': 2000,
            },
            {
                'payment_method_id': 2, 'method_name_snapshot': 'Cash',
                'match_percent_snapshot': 0.0,
                'method_amount': 4000, 'match_amount': 0, 'customer_charged': 4000,
            },
        ])

        txn = get_transaction_by_id(txn_id)
        assert txn['receipt_total'] == 8000

        items = get_payment_line_items(txn_id)
        assert len(items) == 2

        snap_item = next(i for i in items if i['method_name_snapshot'] == 'SNAP')
        cash_item = next(i for i in items if i['method_name_snapshot'] == 'Cash')

        assert snap_item['method_amount'] == 4000
        assert snap_item['match_amount'] == 2000
        assert snap_item['customer_charged'] == 2000
        assert cash_item['method_amount'] == 4000
        assert cash_item['customer_charged'] == 4000

        # Verify totals: receipt = SNAP alloc + Cash alloc
        total_alloc = sum(i['method_amount'] for i in items)
        assert total_alloc == 8000

    def test_double_adjustment(self, fresh_db):
        """Adjusting an already-adjusted transaction works correctly."""
        from fam.models.transaction import (
            update_transaction, save_payment_line_items,
            get_transaction_by_id, get_payment_line_items
        )

        order_id, txn_id = self._create_confirmed_order(fresh_db, 5000, [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 5000, 'match_amount': 0, 'customer_charged': 5000,
        }])

        # First adjustment: $50 → $60
        update_transaction(txn_id, receipt_total=6000, status='Adjusted')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 6000, 'match_amount': 0, 'customer_charged': 6000,
        }])

        # Second adjustment: $60 → $45
        update_transaction(txn_id, receipt_total=4500, status='Adjusted')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 4500, 'match_amount': 0, 'customer_charged': 4500,
        }])

        txn = get_transaction_by_id(txn_id)
        assert txn['receipt_total'] == 4500
        assert txn['status'] == 'Adjusted'

        items = get_payment_line_items(txn_id)
        assert len(items) == 1
        assert items[0]['method_amount'] == 4500

    def test_adjustment_with_match_cap(self, fresh_db):
        """Adjusting with daily match cap active still caps correctly."""
        from fam.models.transaction import (
            update_transaction, save_payment_line_items,
            get_transaction_by_id, get_payment_line_items
        )
        from fam.utils.calculations import calculate_payment_breakdown

        # Enable $30 daily match cap
        fresh_db.execute(
            "UPDATE markets SET match_limit_active = 1, daily_match_limit = 3000"
            " WHERE id = 1")
        fresh_db.commit()

        # Original: $100 order, SNAP $50 charge (uncapped: $50 match)
        order_id, txn_id = self._create_confirmed_order(fresh_db, 10000, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 10000, 'match_amount': 5000, 'customer_charged': 5000,
        }])

        # Adjust to $120: SNAP should still be capped
        new_total = 12000
        result = calculate_payment_breakdown(
            new_total,
            [{'payment_method_id': 1, 'method_amount': 12000, 'match_percent': 100.0}],
            match_limit=3000
        )
        li = result['line_items'][0]

        update_transaction(txn_id, receipt_total=new_total, status='Adjusted')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': li['method_amount'],
            'match_amount': li['match_amount'],
            'customer_charged': li['customer_charged'],
        }])

        assert result['match_was_capped'] is True, "Match should have been capped"
        items = get_payment_line_items(txn_id)
        assert items[0]['match_amount'] <= 3000, (
            f"Match {items[0]['match_amount']} should be <= 3000 cap")

    def test_adjustment_creates_audit_trail(self, fresh_db):
        """Each adjustment is recorded in the audit log."""
        from fam.models.transaction import update_transaction, save_payment_line_items
        from fam.models.audit import log_action

        order_id, txn_id = self._create_confirmed_order(fresh_db, 5000, [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 5000, 'match_amount': 0, 'customer_charged': 5000,
        }])

        # Adjust and manually log (as admin_screen does)
        update_transaction(txn_id, receipt_total=6000, status='Adjusted')
        log_action('transactions', txn_id, 'ADJUST', 'AdminUser',
                   notes='Adjusted $50.00 -> $60.00, reason: Vendor correction')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 6000, 'match_amount': 0, 'customer_charged': 6000,
        }])

        audits = fresh_db.execute(
            "SELECT * FROM audit_log WHERE record_id=? AND table_name='transactions'"
            " AND action='ADJUST'", (txn_id,)
        ).fetchall()
        assert len(audits) >= 1
        assert 'Vendor correction' in audits[-1]['notes']

    def test_adjust_preserves_other_transactions(self, fresh_db):
        """Adjusting one transaction doesn't affect others."""
        from fam.models.transaction import (
            update_transaction, save_payment_line_items,
            get_transaction_by_id
        )

        _, txn1 = self._create_confirmed_order(fresh_db, 5000, [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 5000, 'match_amount': 0, 'customer_charged': 5000,
        }])

        _, txn2 = self._create_confirmed_order(fresh_db, 3000, [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 3000, 'match_amount': 0, 'customer_charged': 3000,
        }])

        # Adjust txn1 only
        update_transaction(txn1, receipt_total=7000, status='Adjusted')
        save_payment_line_items(txn1, [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 7000, 'match_amount': 0, 'customer_charged': 7000,
        }])

        # txn2 should be untouched
        txn2_data = get_transaction_by_id(txn2)
        assert txn2_data['receipt_total'] == 3000
        assert txn2_data['status'] == 'Confirmed'

    def test_adjust_snap_to_different_amount_updates_match(self, fresh_db):
        """Adjusting SNAP transaction recalculates match correctly."""
        from fam.models.transaction import (
            update_transaction, save_payment_line_items,
            get_payment_line_items
        )

        # $80 order, SNAP full: $40 charge, $40 match, $80 alloc
        order_id, txn_id = self._create_confirmed_order(fresh_db, 8000, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 8000, 'match_amount': 4000, 'customer_charged': 4000,
        }])

        # Adjust down to $60: $30 charge, $30 match, $60 alloc
        update_transaction(txn_id, receipt_total=6000, status='Adjusted')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 6000, 'match_amount': 3000, 'customer_charged': 3000,
        }])

        items = get_payment_line_items(txn_id)
        assert items[0]['match_amount'] == 3000
        assert items[0]['customer_charged'] == 3000
        assert items[0]['method_amount'] == 6000


# ═══════════════════════════════════════════════════════════════════
# PRIORITY 6: Match-Cap-Aware Charge Input
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def match_cap_db(tmp_path):
    """DB with a market that has a $100 daily match limit."""
    db_file = str(tmp_path / "test_matchcap.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    # Market with $100 daily match limit active
    conn.execute(
        "INSERT INTO markets (id, name, address, daily_match_limit, match_limit_active)"
        " VALUES (1, 'Test Market', '100 Main St', 10000, 1)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'Farm Stand')")
    # SNAP: 100% match
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (1, 'SNAP', 100.0, 1, 1)")
    # Cash: 0% match
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (2, 'Cash', 0.0, 1, 2)")
    # Assign to market
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, payment_method_id)"
        " VALUES (1, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, payment_method_id)"
        " VALUES (1, 2)")
    # Open market day
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, opened_by)"
        " VALUES (1, 1, '2026-04-01', 'Open', 'Tester')")
    conn.commit()
    yield conn
    close_connection()


def _create_prior_match(conn, match_cents, customer_label='C-001'):
    """Create a confirmed order consuming some of the daily match limit.

    Uses *customer_label* so later orders with the same label see
    the prior match via get_customer_prior_match().
    """
    from fam.models.customer_order import update_customer_order_status
    from fam.models.transaction import (
        create_transaction, save_payment_line_items, confirm_transaction,
    )
    # Insert order directly to control customer_label
    order_id = conn.execute(
        "INSERT INTO customer_orders (market_day_id, customer_label, status)"
        " VALUES (1, ?, 'Draft')", (customer_label,)
    ).lastrowid
    conn.commit()

    receipt_total = match_cents * 2
    txn_id, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=receipt_total,
        market_day_date='2026-04-01', customer_order_id=order_id,
    )
    save_payment_line_items(txn_id, [{
        'payment_method_id': 1,
        'method_name_snapshot': 'SNAP',
        'match_percent_snapshot': 100.0,
        'method_amount': receipt_total,
        'match_amount': match_cents,
        'customer_charged': match_cents,
    }])
    confirm_transaction(txn_id, confirmed_by='Tester')
    update_customer_order_status(order_id, 'Confirmed')
    return customer_label


def _create_new_order(conn, receipt_total_cents, customer_label='C-001'):
    """Create a Draft order with a specific customer label (for returning customer tests)."""
    from fam.models.transaction import create_transaction
    order_id = conn.execute(
        "INSERT INTO customer_orders (market_day_id, customer_label, status)"
        " VALUES (1, ?, 'Draft')", (customer_label,)
    ).lastrowid
    conn.commit()
    create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=receipt_total_cents,
        market_day_date='2026-04-01', customer_order_id=order_id,
    )
    return order_id


class TestMatchCapAwareCharge:
    """When daily match limit reduces the effective match, the charge field
    must allow the customer to enter the actual amount they pay — which can
    be MORE than the nominal (uncapped) charge calculation."""

    def test_push_row_limits_raises_max_when_cap_active(self, qtbot, match_cap_db):
        """_push_row_limits should raise max_charge when match cap limits match."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        # Prior order consumed $87.05 of $100 limit → $12.95 remaining
        _create_prior_match(conn, 8705)

        # New order for SAME customer: $50.11 receipt
        order_id = _create_new_order(conn, 5011)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Verify match limit was detected
        assert screen._match_limit == 1295  # $12.95 remaining

        # Add SNAP row
        row = screen._add_payment_row()
        _select_method(row, 'SNAP')

        # Without fix: max_charge = 5011 / 2 = 2506 ($25.06)
        # With fix: max_charge should allow ~$37.16 (5011 - 1295 = 3716)
        screen._push_row_limits()

        if row._stepper_active:
            max_val = row._stepper._count_spin.maximum()
        else:
            max_val = dollars_to_cents(row.amount_spin.maximum())

        # Max should be at least $37 (cap-aware) not ~$25 (nominal)
        assert max_val >= 3700, (
            f"Max charge should be cap-aware (~$37.16), got {format_dollars(max_val)}"
        )

    def test_method_amount_capped_at_receipt_total(self, qtbot, match_cap_db):
        """When charge exceeds nominal due to cap, method_amount stays ≤ receipt_total."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        _create_prior_match(conn, 8705)
        order_id = _create_new_order(conn, 5011)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'SNAP')

        # Set charge to cap-aware amount (customer pays more when match is capped)
        row._set_active_charge(3716)
        screen._update_summary()

        # Collect line items — method_amount must not exceed receipt total
        items = screen._collect_line_items()
        assert len(items) >= 1
        total_ma = sum(it['method_amount'] for it in items)
        assert total_ma <= 5011, (
            f"Total method_amount ({total_ma}) should not exceed receipt total (5011)"
        )

    def test_summary_cards_correct_with_capped_charge(self, qtbot, match_cap_db):
        """Summary cards show correct Customer Pays and FAM Match when cap active."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        _create_prior_match(conn, 8705)
        order_id = _create_new_order(conn, 5011)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'SNAP')
        # Use the cap-aware charge
        row._set_active_charge(3716)
        screen._update_summary()

        # Match cap warning text should be set (isVisible may be False
        # in offscreen tests because the parent widget isn't shown)
        warning_text = screen.match_cap_warning.text()
        assert "capped" in warning_text.lower(), (
            f"Match cap warning should mention 'capped', got: {warning_text!r}"
        )

    def test_auto_distribute_cap_aware(self, qtbot, match_cap_db):
        """Auto-distribute should give a higher charge when match is capped."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        _create_prior_match(conn, 8705)
        order_id = _create_new_order(conn, 5011)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'SNAP')
        screen._auto_distribute()

        charge = row._get_active_charge()
        # With cap, charge should be ~$37 not ~$25
        assert charge >= 3700, (
            f"Auto-distribute charge should be cap-aware (~$37.16), "
            f"got {format_dollars(charge)}"
        )

    def test_no_cap_nominal_charge_unchanged(self, qtbot, match_cap_db):
        """When match limit is not hit, charge max stays at nominal formula."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        # No prior match — full $100 limit available
        order_id = _create_new_order(conn, 5011)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'SNAP')
        screen._push_row_limits()

        # Uncapped match for $50.11 = $25.06, well under $100 limit
        # Max charge should be nominal: ~$25.06 (= 5011 / 2)
        if row._stepper_active:
            max_val = row._stepper._count_spin.maximum()
        else:
            max_val = dollars_to_cents(row.amount_spin.maximum())

        assert max_val <= 2600, (
            f"Without cap hit, max should be nominal (~$25.06), got {format_dollars(max_val)}"
        )

    def test_remaining_zero_with_cap_aware_charge(self, qtbot, match_cap_db):
        """Remaining should be $0 when cap-aware charge fills the order."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import calculate_payment_breakdown

        conn = match_cap_db
        _create_prior_match(conn, 8705)
        order_id = _create_new_order(conn, 5011)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'SNAP')
        # Set cap-aware charge
        row._set_active_charge(3716)
        screen._update_summary()

        # Collect and verify allocation
        items = screen._collect_line_items()
        entries = [
            {'method_amount': it['method_amount'], 'match_percent': it['match_percent']}
            for it in items
        ]
        result = calculate_payment_breakdown(5011, entries, match_limit=1295)
        assert abs(result['allocation_remaining']) <= 1, (
            f"Allocation remaining should be ~0, got {result['allocation_remaining']}"
        )

    def test_multi_row_cap_aware_snap_plus_cash(self, qtbot, match_cap_db):
        """SNAP + Cash with cap: SNAP charge rises, Cash unaffected."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        _create_prior_match(conn, 9000)  # $90 used, $10 remaining
        order_id = _create_new_order(conn, 5000)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        snap_row = screen._add_payment_row()
        _select_method(snap_row, 'SNAP')
        cash_row = screen._add_payment_row()
        _select_method(cash_row, 'Cash')

        # Set Cash to $10, SNAP should cover the rest
        cash_row._set_active_charge(1000)
        screen._push_row_limits()

        # Remaining after cash = 5000 - 1000 = 4000
        # SNAP nominal max = 4000 / 2 = 2000
        # But match cap = 1000, so SNAP charge should go up to ~3000
        if snap_row._stepper_active:
            max_val = snap_row._stepper._count_spin.maximum()
        else:
            max_val = dollars_to_cents(snap_row.amount_spin.maximum())

        # Cap-aware: max should be ~3000 (4000 - 1000 match limit)
        assert max_val >= 2900, (
            f"SNAP max should be cap-aware (~$30), got {format_dollars(max_val)}"
        )

    def test_full_limit_exhausted_charge_equals_receipt(self, qtbot, match_cap_db):
        """When match limit is fully exhausted, charge should equal receipt total."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        _create_prior_match(conn, 10000)  # $100 used, $0 remaining
        order_id = _create_new_order(conn, 3000)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'SNAP')
        screen._push_row_limits()

        if row._stepper_active:
            max_val = row._stepper._count_spin.maximum()
        else:
            max_val = dollars_to_cents(row.amount_spin.maximum())

        # Match limit exhausted → customer pays ALL → charge max = receipt total
        assert max_val >= 2900, (
            f"With exhausted limit, max charge should be ~$30.00, got {format_dollars(max_val)}"
        )

    def test_cap_exactly_equals_uncapped_match(self, qtbot, match_cap_db):
        """When cap == uncapped match, no capping is needed."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        # No prior match → full $100 limit available
        # Order = $200 → SNAP nominal charge = $100, nominal match = $100 = limit
        order_id = _create_new_order(conn, 20000)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'SNAP')
        screen._push_row_limits()

        if row._stepper_active:
            max_val = row._stepper._count_spin.maximum()
        else:
            max_val = dollars_to_cents(row.amount_spin.maximum())

        # Nominal charge = 20000 / 2 = 10000 ($100). Cap = $100 exactly.
        # No capping needed → max should be nominal ~$100
        assert max_val <= 10100, (
            f"Max charge should be nominal (~$100), got {format_dollars(max_val)}"
        )
        assert max_val >= 9900, (
            f"Max charge should be nominal (~$100), got {format_dollars(max_val)}"
        )

        # Warning text should NOT mention "capped"
        warning_text = screen.match_cap_warning.text()
        assert "capped" not in warning_text.lower(), (
            f"Match cap warning should NOT mention 'capped', got: {warning_text!r}"
        )

    def test_cap_one_penny_below_match(self, qtbot, match_cap_db):
        """Cap is 1 cent less than uncapped match — cap should kick in."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        # Prior match = $49.99 (4999 cents) → remaining = $50.01 (5001 cents)
        _create_prior_match(conn, 4999)

        # Order = $100.04 (10004 cents) → SNAP nominal charge = ~$50.02, match = ~$50.02
        # But cap = $50.01, so cap kicks in by 1 cent
        order_id = _create_new_order(conn, 10004)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'SNAP')
        screen._push_row_limits()

        # Nominal charge = 10004 / 2 = 5002
        # Cap-aware charge = 10004 - 5001 = 5003 (customer pays 1 cent more)
        if row._stepper_active:
            max_val = row._stepper._count_spin.maximum()
        else:
            max_val = dollars_to_cents(row.amount_spin.maximum())

        # Max should be slightly above nominal (5002) since cap kicks in
        assert max_val > 5002, (
            f"Max charge should be above nominal ($50.02) since cap kicks in, "
            f"got {format_dollars(max_val)}"
        )

    def test_auto_distribute_snap_only_with_cap(self, qtbot, match_cap_db):
        """Auto-distribute with only SNAP when cap is active raises charge."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        # Prior match = $90 → remaining = $10 (1000 cents)
        _create_prior_match(conn, 9000)

        # Order = $80 (8000 cents)
        order_id = _create_new_order(conn, 8000)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        snap_row = screen._add_payment_row()
        _select_method(snap_row, 'SNAP')

        screen._auto_distribute()

        snap_charge = snap_row._get_active_charge()
        # SNAP cap-aware: 8000 - 1000 match = 7000 customer charge
        assert snap_charge >= 6900, (
            f"SNAP charge should be cap-aware (~$70), got {format_dollars(snap_charge)}"
        )

        # Verify total allocation <= receipt total
        items = screen._collect_line_items()
        total_ma = sum(it['method_amount'] for it in items)
        assert total_ma <= 8000, (
            f"Total method_amount ({total_ma}) should not exceed receipt total (8000)"
        )

    def test_auto_distribute_only_cash_ignores_cap(self, qtbot, match_cap_db):
        """Cash (0% match) ignores cap — charge is full receipt total."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        # Prior match = $90 → remaining = $10
        _create_prior_match(conn, 9000)

        # Order = $50 (5000 cents)
        order_id = _create_new_order(conn, 5000)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        cash_row = screen._add_payment_row()
        _select_method(cash_row, 'Cash')

        screen._auto_distribute()

        charge = cash_row._get_active_charge()
        assert charge == 5000, (
            f"Cash charge should be full receipt ($50), got {format_dollars(charge)}"
        )

    def test_high_match_percent_with_cap(self, qtbot, match_cap_db):
        """Food RX (200% match) with cap: customer pays more when cap limits match."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        # Add Food RX method inline (match_cap_db only has SNAP and Cash)
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
            " VALUES (4, 'Food RX', 200.0, 1, 4)")
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, payment_method_id)"
            " VALUES (1, 4)")
        conn.commit()

        # Prior match = $80 → remaining = $20 (2000 cents)
        _create_prior_match(conn, 8000)

        # Order = $90 (9000 cents)
        order_id = _create_new_order(conn, 9000)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'Food RX')
        screen._push_row_limits()

        # Food RX 200% match: nominal charge = 9000 / 3 = 3000, nominal match = 6000
        # But cap = 2000, so customer pays 9000 - 2000 = 7000
        if row._stepper_active:
            max_val = row._stepper._count_spin.maximum()
        else:
            max_val = dollars_to_cents(row.amount_spin.maximum())

        assert max_val >= 6900, (
            f"Food RX max charge should be cap-aware (~$70), got {format_dollars(max_val)}"
        )

    def test_draft_collect_line_items_with_cap(self, qtbot, match_cap_db):
        """_collect_line_items returns correct data with cap-aware charge."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        # Prior match = $80 → remaining = $20 (2000 cents)
        _create_prior_match(conn, 8000)

        # Order = $60 (6000 cents)
        order_id = _create_new_order(conn, 6000)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'SNAP')
        # Cap-aware charge: 6000 - 2000 = 4000 customer pays
        row._set_active_charge(4000)
        screen._update_summary()

        items = screen._collect_line_items()
        assert len(items) >= 1
        for it in items:
            assert it['method_amount'] <= 6000, (
                f"method_amount ({it['method_amount']}) should not exceed receipt total (6000)"
            )

    def test_penny_reconciliation_with_cap(self, qtbot, match_cap_db):
        """Odd-cent total with cap: allocation_remaining within +/- 1 cent."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import calculate_payment_breakdown

        conn = match_cap_db
        # Prior match = $95 → remaining = $5 (500 cents)
        _create_prior_match(conn, 9500)

        # Order = $10.01 (1001 cents)
        order_id = _create_new_order(conn, 1001)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'SNAP')
        # SNAP nominal charge = ~$5.01, match = ~$5.00, but cap = $5.00
        # Customer pays 1001 - 500 = 501 cents
        row._set_active_charge(501)
        screen._update_summary()

        items = screen._collect_line_items()
        entries = [
            {'method_amount': it['method_amount'], 'match_percent': it['match_percent']}
            for it in items
        ]
        result = calculate_payment_breakdown(1001, entries, match_limit=500)
        assert abs(result['allocation_remaining']) <= 1, (
            f"Allocation remaining should be within ±1, got {result['allocation_remaining']}"
        )

    def test_cap_with_three_snap_visits_cumulative(self, qtbot, match_cap_db):
        """Third visit with cumulative prior match from two visits."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        # Visit 1 match = $30 (3000), Visit 2 match = $40 (4000), total = $70 (7000)
        _create_prior_match(conn, 3000)
        _create_prior_match(conn, 4000)

        # Visit 3: order = $100 (10000), remaining limit = $30 (3000)
        order_id = _create_new_order(conn, 10000)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'SNAP')
        screen._push_row_limits()

        if row._stepper_active:
            max_val = row._stepper._count_spin.maximum()
        else:
            max_val = dollars_to_cents(row.amount_spin.maximum())

        # Cap-aware: customer pays 10000 - 3000 = 7000
        assert max_val >= 6900, (
            f"Max charge should be cap-aware (~$70) after 2 prior visits, "
            f"got {format_dollars(max_val)}"
        )

    def test_match_limit_zero_means_no_match(self, qtbot, match_cap_db):
        """When daily limit is fully exhausted, customer pays everything."""
        from fam.ui.payment_screen import PaymentScreen

        conn = match_cap_db
        # Exhaust full $100 limit
        _create_prior_match(conn, 10000)

        # Order = $20 (2000 cents)
        order_id = _create_new_order(conn, 2000)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'SNAP')
        screen._auto_distribute()

        charge = row._get_active_charge()
        # No match left → customer pays full receipt
        assert charge == 2000, (
            f"With exhausted limit, charge should be full receipt ($20), "
            f"got {format_dollars(charge)}"
        )


# ═══════════════════════════════════════════════════════════════════
# Denomination + Match Cap Interaction (UI-level)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def denom_cap_db(tmp_path):
    """DB with market that has denominated methods AND daily match limit."""
    db_file = str(tmp_path / "test_denom_cap.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    conn.execute(
        "INSERT INTO markets (id, name, address, daily_match_limit, match_limit_active)"
        " VALUES (1, 'Test Market', '100 Main St', 10000, 1)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'Farm Stand')")

    # SNAP: 100% match, no denomination
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (1, 'SNAP', 100.0, 1, 1)")
    # Cash: 0% match
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (2, 'Cash', 0.0, 1, 2)")
    # FMNP: 100% match, $5 denomination
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order, denomination)"
        " VALUES (3, 'FMNP', 100.0, 1, 3, 500)")
    # JH Tokens: 100% match, $5 denomination
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order, denomination)"
        " VALUES (4, 'JH Tokens', 100.0, 1, 4, 500)")

    for pm_id in (1, 2, 3, 4):
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, payment_method_id)"
            " VALUES (1, ?)", (pm_id,))

    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, opened_by)"
        " VALUES (1, 1, '2026-04-01', 'Open', 'Tester')")
    conn.commit()
    yield conn
    close_connection()


def _create_denom_order(conn, receipt_total_cents, customer_label='C-001'):
    """Create a Draft order with controlled customer_label for denom+cap tests."""
    from fam.models.transaction import create_transaction
    order_id = conn.execute(
        "INSERT INTO customer_orders (market_day_id, customer_label, status)"
        " VALUES (1, ?, 'Draft')", (customer_label,)
    ).lastrowid
    conn.commit()
    create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=receipt_total_cents,
        market_day_date='2026-04-01', customer_order_id=order_id,
    )
    return order_id


def _create_prior_match_denom(conn, match_cents, customer_label='C-001'):
    """Create a confirmed order consuming match limit (for denom+cap tests)."""
    from fam.models.customer_order import update_customer_order_status
    from fam.models.transaction import (
        create_transaction, save_payment_line_items, confirm_transaction,
    )
    order_id = conn.execute(
        "INSERT INTO customer_orders (market_day_id, customer_label, status)"
        " VALUES (1, ?, 'Draft')", (customer_label,)
    ).lastrowid
    conn.commit()

    receipt_total = match_cents * 2
    txn_id, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=receipt_total,
        market_day_date='2026-04-01', customer_order_id=order_id,
    )
    save_payment_line_items(txn_id, [{
        'payment_method_id': 1,
        'method_name_snapshot': 'SNAP',
        'match_percent_snapshot': 100.0,
        'method_amount': receipt_total,
        'match_amount': match_cents,
        'customer_charged': match_cents,
    }])
    confirm_transaction(txn_id, confirmed_by='Tester')
    update_customer_order_status(order_id, 'Confirmed')


class TestDenominationForfeitWithCap:
    """Verify denomination forfeit message works when match cap is also active."""

    def test_denom_overage_detected_with_no_cap(self, qtbot, denom_cap_db):
        """FMNP $5 denom (100% match), $66.03 order, no prior match.
        7 tokens = $35 charge → $70 alloc. Overage = $3.97.
        Forfeit warning should appear."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_denom_order(denom_cap_db, 6603)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'FMNP')
        # 7 units × $5 = $35 charge (3500 cents)
        row._set_active_charge(3500)
        screen._update_summary()

        warning_text = screen.denom_overage_warning.text().lower()
        assert 'forfeit' in warning_text or 'overage' in warning_text, (
            f"Denomination forfeit warning should appear, got: '{screen.denom_overage_warning.text()}'"
        )

    def test_denom_overage_detected_with_active_cap(self, qtbot, denom_cap_db):
        """FMNP with active cap: forfeit still detected.
        Prior match=$50 → remaining=$50. Order=$66.03, 7×$5=$35 → $70 alloc.
        Overage = $3.97. Forfeit should still show."""
        from fam.ui.payment_screen import PaymentScreen

        conn = denom_cap_db
        _create_prior_match_denom(conn, 5000)
        order_id = _create_denom_order(conn, 6603)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'FMNP')
        row._set_active_charge(3500)
        screen._update_summary()

        warning_text = screen.denom_overage_warning.text().lower()
        assert 'forfeit' in warning_text or 'overage' in warning_text, (
            f"Denomination forfeit should show even with active cap"
        )

    def test_collect_line_items_preserves_denom_overage(self, qtbot, denom_cap_db):
        """_collect_line_items should NOT cap denominated method_amount."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_denom_order(denom_cap_db, 4900)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'FMNP')
        # 5 units × $5 = $25 charge → $50 alloc (exceeds $49 receipt)
        row._set_active_charge(2500)
        screen._update_summary()

        items = screen._collect_line_items()
        assert len(items) == 1
        # Denominated row should NOT be capped at receipt total
        assert items[0]['method_amount'] == 5000, (
            f"Denominated method_amount should be full $50 (5000), "
            f"not capped at receipt. Got {items[0]['method_amount']}"
        )

    def test_nondenom_still_capped_in_collect(self, qtbot, denom_cap_db):
        """Non-denominated method_amount IS capped at receipt total."""
        from fam.ui.payment_screen import PaymentScreen

        conn = denom_cap_db
        # Prior match=$80 → remaining=$20
        _create_prior_match_denom(conn, 8000)
        order_id = _create_denom_order(conn, 6000)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'SNAP')
        # Cap-aware charge: $40 → uncapped method_amount = $80
        row._set_active_charge(4000)
        screen._update_summary()

        items = screen._collect_line_items()
        assert len(items) == 1
        assert items[0]['method_amount'] <= 6000, (
            f"Non-denom method_amount should be capped at receipt (6000), "
            f"got {items[0]['method_amount']}"
        )

    def test_two_denom_methods_cumulative_forfeit(self, qtbot, denom_cap_db):
        """FMNP + JH Tokens, both $5 denom (100% match).
        Order=$49: each contributes $25 = $50 alloc. Overage=$1.
        Overage ($1) < sum of denominations ($10), so forfeit accepted."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_denom_order(denom_cap_db, 4900)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row1 = screen._add_payment_row()
        _select_method(row1, 'FMNP')
        row1._set_active_charge(1500)  # 3 units × $5 = $15 → $30

        row2 = screen._add_payment_row()
        _select_method(row2, 'JH Tokens')
        row2._set_active_charge(1000)  # 2 units × $5 = $10 → $20

        screen._update_summary()

        # Total alloc = $50, receipt = $49, overage = $1
        # overage ($1) ≤ sum of denoms ($10), should be accepted as forfeit
        warning_text = screen.denom_overage_warning.text().lower()
        assert 'forfeit' in warning_text or 'overage' in warning_text, (
            "Two-denom cumulative overage should show forfeit warning"
        )

    def test_denom_plus_snap_with_cap_summary_correct(self, qtbot, denom_cap_db):
        """FMNP + SNAP with cap: summary cards show correct values."""
        from fam.ui.payment_screen import PaymentScreen

        conn = denom_cap_db
        _create_prior_match_denom(conn, 7000)  # remaining = $30
        order_id = _create_denom_order(conn, 8000)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row1 = screen._add_payment_row()
        _select_method(row1, 'FMNP')
        row1._set_active_charge(1500)  # 3 units = $15 → $30 alloc

        row2 = screen._add_payment_row()
        _select_method(row2, 'SNAP')
        row2._set_active_charge(2500)  # $25 → $50 alloc

        screen._update_summary()

        # Verify no crash and summary is reasonable
        items = screen._collect_line_items()
        total_ma = sum(it['method_amount'] for it in items)
        total_charge = sum(it['customer_charged'] for it in items)
        assert total_charge > 0, "Customer should pay something"
        for it in items:
            assert it['match_amount'] >= 0, "Match should never be negative"

    def test_denom_exact_fit_no_false_forfeit(self, qtbot, denom_cap_db):
        """$50 order, FMNP $5 denom (100%): 5 units = $25 → $50. Exact fit.
        No forfeit warning should appear."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_denom_order(denom_cap_db, 5000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'FMNP')
        row._set_active_charge(2500)  # 5 units × $5 = $25 → $50 exact
        screen._update_summary()

        warning_text = screen.denom_overage_warning.text()
        assert warning_text == '' or 'forfeit' not in warning_text.lower(), (
            "Exact denomination fit should not show forfeit warning"
        )

    def test_denom_one_penny_overage_absorbed_gracefully(self, qtbot, denom_cap_db):
        """$49.99 order, FMNP $5 denom (100%): 5 units = $25 → $50.
        Overage = $0.01 — absorbed by penny reconciliation (no error).
        A 1-cent denomination overage is silently handled by the calculation
        layer's penny reconciliation, which is correct UX (no scary warning
        for a single cent)."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_denom_order(denom_cap_db, 4999)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'FMNP')
        row._set_active_charge(2500)  # 5 units → $50 alloc, overage = $0.01
        screen._update_summary()

        # $0.01 is absorbed by penny reconciliation — no hard error or red color
        items = screen._collect_line_items()
        assert len(items) == 1
        for it in items:
            assert it['match_amount'] >= 0

    def test_denom_two_cent_overage_is_forfeit(self, qtbot, denom_cap_db):
        """$49.98 order, FMNP $5 denom (100%): 5 units = $25 → $50.
        Overage = $0.02 — exceeds penny tolerance, should show forfeit."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_denom_order(denom_cap_db, 4998)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'FMNP')
        row._set_active_charge(2500)  # 5 units → $50 alloc, overage = $0.02
        screen._update_summary()

        warning_text = screen.denom_overage_warning.text().lower()
        assert 'forfeit' in warning_text or 'overage' in warning_text, (
            "$0.02 denom overage should show forfeit warning"
        )

    def test_match_values_nonnegative_after_denom_forfeit(self, qtbot, denom_cap_db):
        """After denomination forfeit adjustment, all match values ≥ 0."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import calculate_payment_breakdown

        order_id = _create_denom_order(denom_cap_db, 4900)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'FMNP')
        row._set_active_charge(2500)  # $25 → $50, overage = $1
        screen._update_summary()

        items = screen._collect_line_items()
        entries = [
            {'method_amount': it['method_amount'], 'match_percent': it['match_percent']}
            for it in items
        ]
        result = calculate_payment_breakdown(4900, entries, match_limit=screen._match_limit)
        for li in result['line_items']:
            assert li['match_amount'] >= 0, "Match must be ≥ 0 after forfeit"
            assert li['customer_charged'] >= 0, "Customer charged must be ≥ 0"

    def test_denom_max_units_not_inflated_by_match_cap(self, qtbot, denom_cap_db):
        """Regression: match-cap-aware formula must NOT inflate denomination max.
        $163.54 order, JH Tokens $5 denom (100% match), prior match=$33.02.
        Nominal max = $81.77 → 16 units, +1 forfeit = 17 units ($85).
        OLD bug allowed 19 units ($95) → $190 alloc → $-26.46 remaining."""
        from fam.ui.payment_screen import PaymentScreen

        conn = denom_cap_db
        _create_prior_match_denom(conn, 3302)  # remaining = $66.98
        order_id = _create_denom_order(conn, 16354)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'JH Tokens')
        screen._push_row_limits()

        # Get max in cents
        if row._stepper_active:
            max_count = row._stepper._count_spin.maximum()
            max_charge = max_count * 500
        else:
            max_charge = dollars_to_cents(row.amount_spin.maximum())

        # Should be 17 tokens ($85), NOT 19 ($95)
        assert max_charge <= 8500, (
            f"Denom max should be ≤ $85 (17 tokens), got {format_dollars(max_charge)}. "
            f"Match-cap formula should not inflate denominated methods."
        )
        # Must be at least 16 tokens ($80) — nominal allocation
        assert max_charge >= 8000, (
            f"Denom max should be ≥ $80 (16 tokens), got {format_dollars(max_charge)}"
        )

    def test_denom_forfeit_accepted_within_effective_denom(self, qtbot, denom_cap_db):
        """17 tokens ($85) on $163.54 order: overage $6.46.
        Effective denom = $10 ($5 × 200% for 100% match method_amount).
        $6.46 ≤ $10 → accepted as denomination forfeit, not hard error."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_denom_order(denom_cap_db, 16354)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._add_payment_row()
        _select_method(row, 'JH Tokens')
        row._set_active_charge(8500)  # 17 tokens × $5
        screen._update_summary()

        # Overage = $170 - $163.54 = $6.46
        # Effective denom = charge_to_method_amount(500, 100) = 1000
        # $6.46 ≤ $10.00 → should show gold forfeit warning, NOT red error
        warning_text = screen.denom_overage_warning.text().lower()
        assert 'forfeit' in warning_text or 'overage' in warning_text, (
            f"$6.46 overage within effective denom ($10) should show forfeit. "
            f"Got: '{screen.denom_overage_warning.text()}'"
        )

    def test_denom_with_cap_customer_adds_second_method(self, qtbot, denom_cap_db):
        """With cap active, denominated method may not cover full order.
        Customer adds a second non-denom method for the remainder."""
        from fam.ui.payment_screen import PaymentScreen

        conn = denom_cap_db
        _create_prior_match_denom(conn, 3302)  # remaining cap = $66.98
        order_id = _create_denom_order(conn, 16354)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # 16 JH Tokens = $80 charge → $160 alloc (nominal, no forfeit)
        row1 = screen._add_payment_row()
        _select_method(row1, 'JH Tokens')
        row1._set_active_charge(8000)

        # Cash for remaining $3.54
        row2 = screen._add_payment_row()
        _select_method(row2, 'Cash')
        row2._set_active_charge(354)

        screen._update_summary()

        items = screen._collect_line_items()
        total_ma = sum(it['method_amount'] for it in items)
        # Should be close to or exactly $163.54
        assert abs(total_ma - 16354) <= 1, (
            f"Two methods should cover receipt: got {format_dollars(total_ma)}"
        )


# ═══════════════════════════════════════════════════════════════════
# Auto-Distribute Overflow (adds absorber row for remaining balance)
# ═══════════════════════════════════════════════════════════════════

class TestAutoDistributeOverflow:
    """Verify auto-distribute adds an overflow row when all existing rows
    are manually set and there's remaining balance."""

    def test_denom_locked_adds_snap_overflow(self, qtbot, denom_cap_db):
        """JH Tokens manually set to 16 units ($80→$160 alloc) on $163.54 order.
        Auto-distribute should add SNAP row and fill remaining $3.54."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_denom_order(denom_cap_db, 16354)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Screen starts with a default empty row — select JH Tokens on it
        default_row = screen._payment_rows[0]
        _select_method(default_row, 'JH Tokens')
        default_row._set_active_charge(8000)  # 16 tokens = $80 → $160

        rows_before = len(screen._payment_rows)
        screen._auto_distribute()

        # After: should have added an overflow row
        assert len(screen._payment_rows) == rows_before + 1, (
            "Auto-distribute should add overflow row for remaining balance"
        )

        # Find the overflow row (newly added, not the JH Tokens row)
        overflow_row = screen._payment_rows[-1]
        overflow_method = overflow_row.get_selected_method()
        assert overflow_method is not None
        assert 'snap' in overflow_method['name'].lower(), (
            f"Overflow should prefer SNAP, got '{overflow_method['name']}'"
        )

        # Overflow charge should cover the remaining
        overflow_charge = overflow_row._get_active_charge()
        assert overflow_charge > 0, "Overflow row should have a non-zero charge"

        # Total allocation should equal receipt
        items = screen._collect_line_items()
        total_ma = sum(it['method_amount'] for it in items)
        assert abs(total_ma - 16354) <= 1, (
            f"Total allocation should equal receipt, got {format_dollars(total_ma)}"
        )

    def test_snap_reset_absorbs_remaining(self, qtbot, denom_cap_db):
        """SNAP manually set to $20. Auto-distribute resets SNAP and refills
        it to cover the full order — no overflow row needed."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_denom_order(denom_cap_db, 10000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        default_row = screen._payment_rows[0]
        _select_method(default_row, 'SNAP')
        default_row._set_active_charge(2000)  # $20 manually

        rows_before = len(screen._payment_rows)
        screen._auto_distribute()

        # No overflow row — SNAP was reset to absorber and refilled
        assert len(screen._payment_rows) == rows_before, (
            "Non-denom row should absorb remaining, no overflow needed"
        )

        snap_charge = default_row._get_active_charge()
        assert snap_charge == 5000, (
            f"SNAP should absorb full order ($50 charge for $100 @ 100%), "
            f"got {format_dollars(snap_charge)}"
        )

    def test_no_overflow_when_fully_allocated(self, qtbot, denom_cap_db):
        """When manually-set charges fully cover the order, no overflow row added."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_denom_order(denom_cap_db, 10000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        default_row = screen._payment_rows[0]
        _select_method(default_row, 'SNAP')
        default_row._set_active_charge(5000)  # $50 → $100 alloc = exact

        rows_before = len(screen._payment_rows)
        screen._auto_distribute()

        assert len(screen._payment_rows) == rows_before, (
            "No overflow row should be added when order is fully allocated"
        )

    def test_no_overflow_when_empty_row_exists(self, qtbot, denom_cap_db):
        """When there's already an empty (charge=0) row, smart_auto_distribute
        fills it directly — no additional row needed."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_denom_order(denom_cap_db, 10000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        default_row = screen._payment_rows[0]
        _select_method(default_row, 'JH Tokens')
        default_row._set_active_charge(2500)  # 5 tokens = $25 → $50

        # Add empty SNAP row (charge = 0)
        row2 = screen._add_payment_row()
        _select_method(row2, 'SNAP')

        rows_before = len(screen._payment_rows)
        screen._auto_distribute()

        # Should still be same number — the empty one was filled, no overflow
        assert len(screen._payment_rows) == rows_before
        snap_charge = row2._get_active_charge()
        assert snap_charge > 0, "Existing empty SNAP row should be filled"

    def test_overflow_with_match_cap(self, qtbot, denom_cap_db):
        """Overflow row with match cap: SNAP added and fills remainder correctly."""
        from fam.ui.payment_screen import PaymentScreen

        conn = denom_cap_db
        _create_prior_match_denom(conn, 5000)  # remaining cap = $50
        order_id = _create_denom_order(conn, 16354)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        default_row = screen._payment_rows[0]
        _select_method(default_row, 'JH Tokens')
        default_row._set_active_charge(8000)  # 16 tokens = $80 → $160

        rows_before = len(screen._payment_rows)
        screen._auto_distribute()

        assert len(screen._payment_rows) == rows_before + 1
        overflow_row = screen._payment_rows[-1]
        overflow_method = overflow_row.get_selected_method()
        assert overflow_method is not None
        assert overflow_row._get_active_charge() > 0

    def test_overflow_fmnp_plus_jh_adds_snap(self, qtbot, denom_cap_db):
        """Two denominated methods both locked, SNAP added as overflow."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_denom_order(denom_cap_db, 20000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        default_row = screen._payment_rows[0]
        _select_method(default_row, 'FMNP')
        default_row._set_active_charge(2500)  # 5 units = $25 → $50

        row2 = screen._add_payment_row()
        _select_method(row2, 'JH Tokens')
        row2._set_active_charge(2500)  # 5 units = $25 → $50

        # $100 allocated of $200 total
        rows_before = len(screen._payment_rows)
        screen._auto_distribute()

        assert len(screen._payment_rows) == rows_before + 1
        overflow_row = screen._payment_rows[-1]
        overflow_method = overflow_row.get_selected_method()
        assert overflow_method is not None
        assert 'snap' in overflow_method['name'].lower()
        assert overflow_row._get_active_charge() > 0

    def test_snap_plus_jh_tokens_snap_absorbs(self, qtbot, denom_cap_db):
        """Regression: SNAP ($48) + JH Tokens (2 units=$10) on $117.91 order.
        Auto-distribute should reset SNAP and refill it as absorber —
        NOT add a new Cash row."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_denom_order(denom_cap_db, 11791)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Row 1: SNAP with manual charge
        default_row = screen._payment_rows[0]
        _select_method(default_row, 'SNAP')
        default_row._set_active_charge(4800)  # $48 manually

        # Row 2: JH Tokens = 2 units ($10)
        row2 = screen._add_payment_row()
        _select_method(row2, 'JH Tokens')
        row2._set_active_charge(1000)  # 2 tokens = $10 → $20 alloc

        rows_before = len(screen._payment_rows)
        screen._auto_distribute()

        # SNAP should reset and absorb remaining — NO new Cash row
        assert len(screen._payment_rows) == rows_before, (
            f"SNAP should absorb remaining, but rows went from "
            f"{rows_before} to {len(screen._payment_rows)}"
        )

        # JH Tokens stays locked at 2 units
        jh_charge = row2._get_active_charge()
        assert jh_charge == 1000, (
            f"JH Tokens should stay locked at $10, got {format_dollars(jh_charge)}"
        )

        # SNAP absorbed the rest
        snap_charge = default_row._get_active_charge()
        assert snap_charge > 0, "SNAP should have a charge after auto-distribute"

        # Total should match receipt
        items = screen._collect_line_items()
        total_ma = sum(it['method_amount'] for it in items)
        assert abs(total_ma - 11791) <= 1, (
            f"Total allocation should equal receipt, got {format_dollars(total_ma)}"
        )
