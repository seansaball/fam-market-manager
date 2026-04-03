"""Automated UI tests for the Payment Processing screen.

Uses pytest-qt to instantiate real PySide6 widgets backed by a test
database, drive them programmatically, and assert that summary cards,
row values, and validation logic behave correctly.

These tests are purely additive — they do not modify any application code.
"""
import pytest
from PySide6.QtCore import Qt

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.utils.money import format_dollars, dollars_to_cents


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path):
    """Create a fresh database with a market, vendors, and payment methods."""
    db_file = str(tmp_path / "test_ui.db")
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
    # Food RX: 200% match (2:1)
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (4, 'Food RX', 200.0, 1, 4)")

    # Junction: assign all methods to market 1
    conn.execute("INSERT INTO market_payment_methods (market_id, payment_method_id) VALUES (1, 1)")
    conn.execute("INSERT INTO market_payment_methods (market_id, payment_method_id) VALUES (1, 2)")
    conn.execute("INSERT INTO market_payment_methods (market_id, payment_method_id) VALUES (1, 3)")
    conn.execute("INSERT INTO market_payment_methods (market_id, payment_method_id) VALUES (1, 4)")

    # Market day
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, opened_by)"
        " VALUES (1, 1, '2026-04-01', 'Open', 'Tester')")

    conn.commit()
    yield conn
    close_connection()


def _create_order(conn, receipt_total_cents, vendor_id=1):
    """Helper: create a customer order with one transaction, return order_id."""
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction

    order_id, label = create_customer_order(market_day_id=1)
    create_transaction(
        market_day_id=1,
        vendor_id=vendor_id,
        receipt_total=receipt_total_cents,
        market_day_date='2026-04-01',
        customer_order_id=order_id,
    )
    return order_id


def _create_multi_receipt_order(conn, amounts_cents, vendor_ids=None):
    """Helper: create a customer order with multiple receipts, return order_id."""
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction

    order_id, label = create_customer_order(market_day_id=1)
    for i, amt in enumerate(amounts_cents):
        vid = vendor_ids[i] if vendor_ids else 1
        create_transaction(
            market_day_id=1,
            vendor_id=vid,
            receipt_total=amt,
            market_day_date='2026-04-01',
            customer_order_id=order_id,
        )
    return order_id


def _get_card_value(screen, key):
    """Read the current text from a summary card."""
    return screen.summary_row.cards[key].value_label.text()


def _select_method(row, method_name):
    """Select a payment method by name in a PaymentRow's combo box."""
    combo = row.method_combo
    for i in range(combo.count()):
        if method_name.lower() in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            return
    raise ValueError(f"Method '{method_name}' not found in combo box")


def _set_charge_dollars(row, dollars):
    """Set the charge amount on a payment row (works for both spinbox and stepper)."""
    cents = dollars_to_cents(dollars)
    row._set_active_charge(cents)


# ── Test: Summary Cards Update ──────────────────────────────────────

class TestSummaryCardUpdates:
    """Verify summary cards reflect correct values as charges are entered."""

    def test_initial_load_shows_order_total(self, qtbot, fresh_db):
        """Loading an order shows correct total and $0 allocated."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 5000)  # $50.00
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        assert _get_card_value(screen, "remaining") == "$50.00"
        assert _get_card_value(screen, "allocated") == "$0.00"
        assert _get_card_value(screen, "customer_pays") == "$0.00"
        assert _get_card_value(screen, "fam_match") == "$0.00"

    def test_snap_full_allocation(self, qtbot, fresh_db):
        """$100 order, $50 SNAP charge → $100 allocated, $0 remaining."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 10000)  # $100.00
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "SNAP")
        _set_charge_dollars(row, 50.00)
        screen._update_summary()

        assert _get_card_value(screen, "allocated") == "$100.00"
        assert _get_card_value(screen, "remaining") == "$0.00"
        assert _get_card_value(screen, "customer_pays") == "$50.00"
        assert _get_card_value(screen, "fam_match") == "$50.00"

    def test_cash_full_allocation(self, qtbot, fresh_db):
        """$30 order, $30 Cash charge → $30 allocated, $0 remaining, $0 match."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 3000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "Cash")
        _set_charge_dollars(row, 30.00)
        screen._update_summary()

        assert _get_card_value(screen, "allocated") == "$30.00"
        assert _get_card_value(screen, "remaining") == "$0.00"
        assert _get_card_value(screen, "customer_pays") == "$30.00"
        assert _get_card_value(screen, "fam_match") == "$0.00"

    def test_partial_allocation_shows_remaining(self, qtbot, fresh_db):
        """$80 order, $20 SNAP charge ($40 allocated) → $40 remaining."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 8000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "SNAP")
        _set_charge_dollars(row, 20.00)
        screen._update_summary()

        assert _get_card_value(screen, "allocated") == "$40.00"
        assert _get_card_value(screen, "remaining") == "$40.00"
        assert _get_card_value(screen, "customer_pays") == "$20.00"
        assert _get_card_value(screen, "fam_match") == "$20.00"


# ── Test: Penny Reconciliation Display ──────────────────────────────

class TestPennyReconciliation:
    """Verify the UI handles odd-cent totals correctly (no contradictory display)."""

    def test_odd_cent_snap_shows_zero_remaining(self, qtbot, fresh_db):
        """$56.77 order with 100% SNAP → remaining should show $0.00."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 5677)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "SNAP")
        # $56.77 / 2 = $28.385, rounds to $28.39 charge
        # method_amount = 28.39 * 2 = 56.78 or charge_to_method_amount(2839, 100) = 5678
        # That's 1 cent over. Penny reconciliation should absorb it.
        _set_charge_dollars(row, 28.39)
        screen._update_summary()

        remaining = _get_card_value(screen, "remaining")
        allocated = _get_card_value(screen, "allocated")
        assert remaining == "$0.00", f"Expected $0.00 remaining, got {remaining}"
        assert allocated == "$56.77", f"Expected $56.77 allocated, got {allocated}"

    def test_odd_cent_one_penny_total(self, qtbot, fresh_db):
        """$0.01 order — edge case minimum."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 1)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "Cash")
        _set_charge_dollars(row, 0.01)
        screen._update_summary()

        assert _get_card_value(screen, "remaining") == "$0.00"
        assert _get_card_value(screen, "allocated") == "$0.01"

    def test_odd_cent_99_cents(self, qtbot, fresh_db):
        """$0.99 order with SNAP → penny reconciliation kicks in."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 99)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "SNAP")
        # $0.99 / 2 = $0.495 → charge $0.50
        # method_amount = charge_to_method_amount(50, 100) = 100 = $1.00
        # That's 1 cent over. Try $0.49 instead:
        # charge_to_method_amount(49, 100) = 98 = $0.98 → 1 cent under
        # Either way, penny reconciliation should show $0.00 remaining
        _set_charge_dollars(row, 0.50)
        screen._update_summary()

        remaining = _get_card_value(screen, "remaining")
        # With penny tolerance, ±1 cent shows as $0.00
        assert remaining == "$0.00"


# ── Test: Multi-Method Payment ──────────────────────────────────────

class TestMultiMethodPayment:
    """Test split payments across multiple payment methods."""

    def test_snap_plus_cash(self, qtbot, fresh_db):
        """$100 order: $40 SNAP charge ($80 alloc) + $20 Cash = $100."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 10000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Row 1: SNAP
        row1 = screen._payment_rows[0]
        _select_method(row1, "SNAP")
        _set_charge_dollars(row1, 40.00)

        # Add row 2: Cash
        screen._add_payment_row()
        row2 = screen._payment_rows[1]
        _select_method(row2, "Cash")
        _set_charge_dollars(row2, 20.00)

        screen._update_summary()

        assert _get_card_value(screen, "allocated") == "$100.00"
        assert _get_card_value(screen, "remaining") == "$0.00"
        assert _get_card_value(screen, "customer_pays") == "$60.00"
        assert _get_card_value(screen, "fam_match") == "$40.00"

    def test_three_methods(self, qtbot, fresh_db):
        """$60 order: SNAP $20 charge + FMNP $10 charge + Cash $10."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 6000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Row 1: SNAP — $20 charge → $40 allocated
        row1 = screen._payment_rows[0]
        _select_method(row1, "SNAP")
        _set_charge_dollars(row1, 20.00)

        # Row 2: FMNP — $10 charge (2 × $5 denom) → $20 allocated
        screen._add_payment_row()
        row2 = screen._payment_rows[1]
        _select_method(row2, "FMNP")
        _set_charge_dollars(row2, 10.00)

        screen._update_summary()
        # At this point: $40 + $20 = $60 allocated, $0 remaining
        assert _get_card_value(screen, "remaining") == "$0.00"
        assert _get_card_value(screen, "customer_pays") == "$30.00"
        assert _get_card_value(screen, "fam_match") == "$30.00"

    def test_add_then_remove_row(self, qtbot, fresh_db):
        """Adding then removing a payment row returns to previous state."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 10000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Start with SNAP at $30 charge ($60 allocated), leaving $40 remaining
        row1 = screen._payment_rows[0]
        _select_method(row1, "SNAP")
        _set_charge_dollars(row1, 30.00)
        screen._update_summary()

        assert _get_card_value(screen, "remaining") == "$40.00"
        original_charge = row1._get_active_charge()

        # Add a second row with Cash $20
        screen._add_payment_row()
        row2 = screen._payment_rows[1]
        _select_method(row2, "Cash")
        _set_charge_dollars(row2, 20.00)
        screen._update_summary()

        # Remaining should drop by $20
        assert _get_card_value(screen, "remaining") == "$20.00"

        # Remove second row
        screen._remove_payment_row(row2)

        # Should be back to $40 remaining
        assert _get_card_value(screen, "remaining") == "$40.00"


# ── Test: Denomination Stepper ──────────────────────────────────────

class TestDenominationStepper:
    """Test denominated payment methods (e.g., FMNP $5 checks)."""

    def test_fmnp_stepper_activates(self, qtbot, fresh_db):
        """Selecting FMNP activates the denomination stepper."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 5000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "FMNP")

        # The stepper should be active for denominated methods
        assert row._stepper_active is True
        assert row._stepper is not None

    def test_fmnp_stepper_charge_multiples(self, qtbot, fresh_db):
        """FMNP stepper produces charges in $5 multiples."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 5000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "FMNP")

        # Set count to 3 checks ($15 charge)
        row._stepper.setCount(3)
        charge = row._get_active_charge()
        assert charge == 1500, f"Expected 1500 cents ($15), got {charge}"

    def test_fmnp_five_checks_full_allocation(self, qtbot, fresh_db):
        """$50 order, 5 FMNP checks ($25 charge) → $50 allocated."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 5000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "FMNP")
        row._stepper.setCount(5)  # 5 × $5 = $25 charge, $50 method_amount at 100%
        screen._update_summary()

        assert _get_card_value(screen, "allocated") == "$50.00"
        assert _get_card_value(screen, "remaining") == "$0.00"
        assert _get_card_value(screen, "customer_pays") == "$25.00"
        assert _get_card_value(screen, "fam_match") == "$25.00"


# ── Test: Food RX (200% match) ──────────────────────────────────────

class TestHighMatchPercent:
    """Test payment methods with match > 100%."""

    def test_food_rx_200_percent(self, qtbot, fresh_db):
        """$30 order, Food RX 200% match: $10 charge → $30 allocated."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 3000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "Food RX")
        _set_charge_dollars(row, 10.00)
        screen._update_summary()

        assert _get_card_value(screen, "allocated") == "$30.00"
        assert _get_card_value(screen, "remaining") == "$0.00"
        assert _get_card_value(screen, "customer_pays") == "$10.00"
        assert _get_card_value(screen, "fam_match") == "$20.00"


# ── Test: Multi-Receipt Orders ──────────────────────────────────────

class TestMultiReceiptOrder:
    """Test orders with multiple vendor receipts."""

    def test_two_receipts_total_sums(self, qtbot, fresh_db):
        """Order with $30 + $20 receipts = $50 total."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_multi_receipt_order(fresh_db, [3000, 2000])
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Verify order total is sum
        assert screen._order_total == 5000

        # Verify remaining shows full amount
        assert _get_card_value(screen, "remaining") == "$50.00"

    def test_three_receipts_snap_covers_all(self, qtbot, fresh_db):
        """3 receipts totaling $100, fully paid with SNAP."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_multi_receipt_order(fresh_db, [4000, 3500, 2500])
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        assert screen._order_total == 10000

        row = screen._payment_rows[0]
        _select_method(row, "SNAP")
        _set_charge_dollars(row, 50.00)
        screen._update_summary()

        assert _get_card_value(screen, "allocated") == "$100.00"
        assert _get_card_value(screen, "remaining") == "$0.00"


# ── Test: Row Data Collection ───────────────────────────────────────

class TestRowDataCollection:
    """Verify get_data() returns correct integer-cents values."""

    def test_snap_get_data(self, qtbot, fresh_db):
        """SNAP row with $25 charge returns correct data dict."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 5000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "SNAP")
        _set_charge_dollars(row, 25.00)

        data = row.get_data()
        assert data is not None
        assert data['customer_charged'] == 2500
        assert data['match_amount'] == 2500
        assert data['method_amount'] == 5000
        assert data['match_percent'] == 100.0

    def test_cash_get_data(self, qtbot, fresh_db):
        """Cash row with $15 charge: match = 0, method_amount = charge."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 1500)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "Cash")
        _set_charge_dollars(row, 15.00)

        data = row.get_data()
        assert data['customer_charged'] == 1500
        assert data['match_amount'] == 0
        assert data['method_amount'] == 1500

    def test_no_method_selected_returns_none(self, qtbot, fresh_db):
        """Row with no method selected returns None from get_data()."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 5000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        # Don't select any method
        data = row.get_data()
        assert data is None


# ── Test: Match Limit (Cap) ─────────────────────────────────────────

class TestMatchLimitUI:
    """Test daily match limit behavior in the UI."""

    def test_match_limit_caps_fam_match(self, qtbot, fresh_db):
        """Enable $50 daily cap → SNAP match capped when exceeded."""
        from fam.ui.payment_screen import PaymentScreen

        # Enable match limit on market
        fresh_db.execute(
            "UPDATE markets SET match_limit_active = 1, daily_match_limit = 5000"
            " WHERE id = 1")
        fresh_db.commit()

        order_id = _create_order(fresh_db, 20000)  # $200 order
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "SNAP")
        _set_charge_dollars(row, 100.00)  # $100 charge → $100 match uncapped
        screen._update_summary()

        # Match should be capped to $50
        fam_match = _get_card_value(screen, "fam_match")
        assert fam_match == "$50.00", f"Expected capped match $50.00, got {fam_match}"

        # Warning should have been activated (isVisibleTo checks the widget's
        # own visibility flag, not the parent chain which is hidden in headless tests)
        assert not screen.match_cap_warning.isHidden(), "Match cap warning should be shown"


# ── Test: Auto-Distribute ───────────────────────────────────────────

class TestAutoDistribute:
    """Test the auto-distribute button fills rows correctly."""

    def test_auto_distribute_single_snap(self, qtbot, fresh_db):
        """Auto-distribute with one SNAP row fills it completely."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 10000)  # $100
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "SNAP")

        screen._auto_distribute()
        screen._update_summary()

        assert _get_card_value(screen, "remaining") == "$0.00"
        charge = row._get_active_charge()
        assert charge == 5000, f"Expected $50 charge, got {charge} cents"

    def test_auto_distribute_snap_plus_cash(self, qtbot, fresh_db):
        """Auto-distribute with SNAP + Cash fills both correctly."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 10000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row1 = screen._payment_rows[0]
        _select_method(row1, "SNAP")

        screen._add_payment_row()
        row2 = screen._payment_rows[1]
        _select_method(row2, "Cash")

        screen._auto_distribute()
        screen._update_summary()

        assert _get_card_value(screen, "remaining") == "$0.00"


# ── Test: Edge Cases ────────────────────────────────────────────────

class TestEdgeCases:
    """Various boundary and edge-case scenarios."""

    def test_zero_charge_no_allocation(self, qtbot, fresh_db):
        """Selecting a method but leaving charge at $0 → no allocation."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 5000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "SNAP")
        # Charge stays at 0
        screen._update_summary()

        assert _get_card_value(screen, "remaining") == "$50.00"
        assert _get_card_value(screen, "allocated") == "$0.00"

    def test_large_order(self, qtbot, fresh_db):
        """$9,999.99 order → values display correctly."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 999999)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "Cash")
        _set_charge_dollars(row, 9999.99)
        screen._update_summary()

        assert _get_card_value(screen, "remaining") == "$0.00"

    def test_one_cent_order(self, qtbot, fresh_db):
        """$0.01 order with Cash."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_order(fresh_db, 1)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "Cash")
        _set_charge_dollars(row, 0.01)
        screen._update_summary()

        assert _get_card_value(screen, "remaining") == "$0.00"
        assert _get_card_value(screen, "customer_pays") == "$0.01"

    @pytest.mark.parametrize("total_cents", [
        1, 49, 50, 99, 100, 101, 999, 1000, 4999, 5677, 10000, 99999
    ])
    def test_snap_any_amount_remaining_zero_or_penny(self, qtbot, fresh_db, total_cents):
        """For any order total, SNAP at half (rounded) should leave ≤1¢ remaining."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import method_amount_to_charge, charge_to_method_amount

        order_id = _create_order(fresh_db, total_cents)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, "SNAP")

        # Compute the correct charge for full allocation
        charge = method_amount_to_charge(total_cents, 100.0)
        _set_charge_dollars(row, charge / 100.0)
        screen._update_summary()

        remaining = _get_card_value(screen, "remaining")
        assert remaining == "$0.00", (
            f"Order ${total_cents/100:.2f}: expected $0.00 remaining, got {remaining}"
        )
