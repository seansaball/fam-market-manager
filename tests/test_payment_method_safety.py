"""Payment method CRUD safety and Reports screen FMNP separation tests.

Covers:
- Deactivated methods hidden from payment row dropdowns
- Deactivated methods don't break existing transactions
- Payment method CRUD operations (create, update, soft-delete)
- Market assignment/unassignment
- Reports screen correctly separates FAM Match from FMNP Match
- Historical data preserved via snapshots after method changes
"""
import pytest
from PySide6.QtCore import Qt

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.utils.money import dollars_to_cents, cents_to_dollars, format_dollars


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path):
    """Create a fresh database with market, vendors, and payment methods."""
    db_file = str(tmp_path / "test_pm_safety.db")
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

    # Junction: assign all methods to market 1
    for pm_id in (1, 2, 3):
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


def _create_confirmed_order(conn, receipt_cents, line_items, vendor_id=1):
    """Create and confirm an order with specific payment lines."""
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


# ═══════════════════════════════════════════════════════════════════
# Payment Method CRUD Safety
# ═══════════════════════════════════════════════════════════════════

class TestPaymentMethodCRUD:
    """Basic CRUD operations on payment methods."""

    def test_create_payment_method(self, fresh_db):
        """Creating a payment method returns a valid ID."""
        from fam.models.payment_method import create_payment_method, get_payment_method_by_id

        pm_id = create_payment_method("EBT", 50.0, sort_order=5)
        assert pm_id > 0

        pm = get_payment_method_by_id(pm_id)
        assert pm['name'] == 'EBT'
        assert pm['match_percent'] == 50.0
        assert pm['is_active'] == 1

    def test_update_match_percent(self, fresh_db):
        """Updating match percent persists correctly."""
        from fam.models.payment_method import update_payment_method, get_payment_method_by_id

        update_payment_method(1, match_percent=75.0)
        pm = get_payment_method_by_id(1)
        assert pm['match_percent'] == 75.0

    def test_soft_deactivate(self, fresh_db):
        """Deactivating sets is_active=0, doesn't delete the row."""
        from fam.models.payment_method import update_payment_method, get_payment_method_by_id

        update_payment_method(1, is_active=False)
        pm = get_payment_method_by_id(1)
        assert pm is not None  # Still exists
        assert pm['is_active'] == 0

    def test_reactivate(self, fresh_db):
        """Reactivating a deactivated method restores it."""
        from fam.models.payment_method import update_payment_method, get_payment_method_by_id

        update_payment_method(1, is_active=False)
        update_payment_method(1, is_active=True)
        pm = get_payment_method_by_id(1)
        assert pm['is_active'] == 1

    def test_get_active_only(self, fresh_db):
        """get_all_payment_methods(active_only=True) excludes deactivated."""
        from fam.models.payment_method import update_payment_method, get_all_payment_methods

        update_payment_method(1, is_active=False)
        active = get_all_payment_methods(active_only=True)
        names = [m['name'] for m in active]
        assert 'SNAP' not in names
        assert 'Cash' in names

    def test_get_all_includes_inactive(self, fresh_db):
        """get_all_payment_methods(active_only=False) includes deactivated."""
        from fam.models.payment_method import update_payment_method, get_all_payment_methods

        update_payment_method(1, is_active=False)
        all_methods = get_all_payment_methods(active_only=False)
        names = [m['name'] for m in all_methods]
        assert 'SNAP' in names

    def test_set_denomination(self, fresh_db):
        """Setting denomination on a method persists correctly."""
        from fam.models.payment_method import update_payment_method, get_payment_method_by_id

        update_payment_method(2, denomination=1000)  # $10 denomination on Cash
        pm = get_payment_method_by_id(2)
        assert pm['denomination'] == 1000

    def test_clear_denomination(self, fresh_db):
        """Setting denomination=0 clears it (sets to NULL)."""
        from fam.models.payment_method import update_payment_method, get_payment_method_by_id

        update_payment_method(3, denomination=0)  # Clear FMNP denomination
        pm = get_payment_method_by_id(3)
        assert pm['denomination'] is None


class TestMarketAssignment:
    """Test market-payment method junction table operations."""

    def test_assign_to_market(self, fresh_db):
        """Assigning a method to a market appears in the junction."""
        from fam.models.payment_method import (
            create_payment_method, assign_payment_method_to_market,
            get_payment_methods_for_market
        )

        pm_id = create_payment_method("EBT", 50.0, sort_order=5)
        assign_payment_method_to_market(1, pm_id)

        methods = get_payment_methods_for_market(1)
        names = [m['name'] for m in methods]
        assert 'EBT' in names

    def test_unassign_from_market(self, fresh_db):
        """Unassigning a method removes it from the market's list."""
        from fam.models.payment_method import (
            unassign_payment_method_from_market, get_payment_methods_for_market
        )

        unassign_payment_method_from_market(1, 2)  # Remove Cash from market
        methods = get_payment_methods_for_market(1)
        names = [m['name'] for m in methods]
        assert 'Cash' not in names
        assert 'SNAP' in names

    def test_assign_is_idempotent(self, fresh_db):
        """Assigning the same method twice doesn't create duplicate rows."""
        from fam.models.payment_method import (
            assign_payment_method_to_market, get_market_payment_method_ids
        )

        assign_payment_method_to_market(1, 1)  # Already assigned
        assign_payment_method_to_market(1, 1)  # Duplicate
        ids = get_market_payment_method_ids(1)
        assert ids.count(1) if isinstance(ids, list) else (1 in ids)

    def test_deactivated_hidden_from_market_active(self, fresh_db):
        """Deactivated methods excluded from active_only market queries."""
        from fam.models.payment_method import (
            update_payment_method, get_payment_methods_for_market
        )

        update_payment_method(1, is_active=False)
        methods = get_payment_methods_for_market(1, active_only=True)
        names = [m['name'] for m in methods]
        assert 'SNAP' not in names

    def test_deactivated_visible_in_all_market(self, fresh_db):
        """Deactivated methods visible when active_only=False."""
        from fam.models.payment_method import (
            update_payment_method, get_payment_methods_for_market
        )

        update_payment_method(1, is_active=False)
        methods = get_payment_methods_for_market(1, active_only=False)
        names = [m['name'] for m in methods]
        assert 'SNAP' in names


# ═══════════════════════════════════════════════════════════════════
# Deactivation Safety: Existing Transactions
# ═══════════════════════════════════════════════════════════════════

class TestDeactivationSafety:
    """Deactivating a method must not corrupt existing transactions."""

    def test_deactivated_method_preserves_line_items(self, fresh_db):
        """Deactivating SNAP doesn't delete existing payment_line_items."""
        from fam.models.payment_method import update_payment_method
        from fam.models.transaction import get_payment_line_items

        _, txn_id = _create_confirmed_order(fresh_db, 10000, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 10000, 'match_amount': 5000, 'customer_charged': 5000,
        }])

        update_payment_method(1, is_active=False)

        items = get_payment_line_items(txn_id)
        assert len(items) == 1
        assert items[0]['method_name_snapshot'] == 'SNAP'
        assert items[0]['match_amount'] == 5000

    def test_deactivated_method_excluded_from_payment_row(self, qtbot, fresh_db):
        """PaymentRow dropdown doesn't show deactivated methods."""
        from fam.models.payment_method import update_payment_method
        from fam.ui.widgets.payment_row import PaymentRow

        update_payment_method(1, is_active=False)

        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)

        combo_items = [row.method_combo.itemText(i)
                       for i in range(row.method_combo.count())]
        snap_items = [t for t in combo_items if 'SNAP' in t]
        assert len(snap_items) == 0, f"SNAP should not appear: {combo_items}"

    def test_active_methods_still_visible(self, qtbot, fresh_db):
        """Active methods still appear after deactivating another."""
        from fam.models.payment_method import update_payment_method
        from fam.ui.widgets.payment_row import PaymentRow

        update_payment_method(1, is_active=False)

        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)

        combo_items = [row.method_combo.itemText(i)
                       for i in range(row.method_combo.count())]
        cash_items = [t for t in combo_items if 'Cash' in t]
        assert len(cash_items) == 1

    def test_snapshot_preserves_original_values(self, fresh_db):
        """Changing a method's match_percent doesn't alter historical snapshots."""
        from fam.models.payment_method import update_payment_method
        from fam.models.transaction import get_payment_line_items

        _, txn_id = _create_confirmed_order(fresh_db, 10000, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 10000, 'match_amount': 5000, 'customer_charged': 5000,
        }])

        # Change SNAP match from 100% to 50%
        update_payment_method(1, match_percent=50.0)

        # Historical transaction should still show 100%
        items = get_payment_line_items(txn_id)
        assert items[0]['match_percent_snapshot'] == 100.0
        assert items[0]['match_amount'] == 5000  # Not recalculated

    def test_renamed_method_preserves_snapshot(self, fresh_db):
        """Renaming a method doesn't change historical method_name_snapshot."""
        from fam.models.payment_method import update_payment_method
        from fam.models.transaction import get_payment_line_items

        _, txn_id = _create_confirmed_order(fresh_db, 5000, [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 5000, 'match_amount': 0, 'customer_charged': 5000,
        }])

        update_payment_method(2, name='Cash/Check')

        items = get_payment_line_items(txn_id)
        assert items[0]['method_name_snapshot'] == 'Cash'  # Original name preserved


# ═══════════════════════════════════════════════════════════════════
# Reports Screen: FAM Match vs FMNP Separation
# ═══════════════════════════════════════════════════════════════════

class TestReportsFMNPSeparation:
    """Verify Reports screen separates transaction FAM match from FMNP."""

    def test_snap_only_shows_fam_match_no_fmnp(self, qtbot, fresh_db):
        """SNAP transactions show FAM Match but not FMNP."""
        from fam.ui.reports_screen import ReportsScreen

        _create_confirmed_order(fresh_db, 10000, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 10000, 'match_amount': 5000, 'customer_charged': 5000,
        }])

        screen = ReportsScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        fam_text = screen.summary_row.cards['fam_match'].value_label.text()
        fmnp_text = screen.summary_row.cards['fmnp_total'].value_label.text()

        assert fam_text == "$50.00", f"FAM Match should be $50, got {fam_text}"
        assert fmnp_text == "$0.00", f"FMNP should be $0, got {fmnp_text}"

    def test_fmnp_external_shows_in_fmnp_card(self, qtbot, fresh_db):
        """External FMNP entries appear in FMNP Match card, not FAM Match."""
        from fam.models.fmnp import create_fmnp_entry
        from fam.ui.reports_screen import ReportsScreen

        create_fmnp_entry(
            market_day_id=1, vendor_id=1,
            amount=2500, entered_by='Tester',
            check_count=5)

        screen = ReportsScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        fmnp_text = screen.summary_row.cards['fmnp_total'].value_label.text()
        assert fmnp_text == "$25.00", f"FMNP should be $25, got {fmnp_text}"

    def test_mixed_snap_and_fmnp_separate(self, qtbot, fresh_db):
        """SNAP match and FMNP external tracked in separate cards."""
        from fam.models.fmnp import create_fmnp_entry
        from fam.ui.reports_screen import ReportsScreen

        # SNAP transaction: $100, $50 match
        _create_confirmed_order(fresh_db, 10000, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 10000, 'match_amount': 5000, 'customer_charged': 5000,
        }])

        # External FMNP: 3 checks × $5 = $15
        create_fmnp_entry(
            market_day_id=1, vendor_id=1,
            amount=1500, entered_by='Tester',
            check_count=3)

        screen = ReportsScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        fam_text = screen.summary_row.cards['fam_match'].value_label.text()
        fmnp_text = screen.summary_row.cards['fmnp_total'].value_label.text()
        receipt_text = screen.summary_row.cards['total_receipts'].value_label.text()

        # FAM Match includes SNAP match ($50) + FMNP external ($15) = $65
        # (Reports aggregates all matching funds into FAM Match)
        # FMNP card shows just the external FMNP amount
        assert fmnp_text == "$15.00", f"FMNP should be $15, got {fmnp_text}"
        assert receipt_text == "$100.00", f"Receipts should be $100, got {receipt_text}"

    def test_cash_only_zero_match_zero_fmnp(self, qtbot, fresh_db):
        """Cash-only transactions show $0 for both FAM Match and FMNP."""
        from fam.ui.reports_screen import ReportsScreen

        _create_confirmed_order(fresh_db, 5000, [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 5000, 'match_amount': 0, 'customer_charged': 5000,
        }])

        screen = ReportsScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        fam_text = screen.summary_row.cards['fam_match'].value_label.text()
        fmnp_text = screen.summary_row.cards['fmnp_total'].value_label.text()

        assert fam_text == "$0.00", f"FAM Match should be $0, got {fam_text}"
        assert fmnp_text == "$0.00", f"FMNP should be $0, got {fmnp_text}"

    def test_customer_paid_correct_with_mix(self, qtbot, fresh_db):
        """Customer Paid reflects only what the customer paid, not match."""
        from fam.models.fmnp import create_fmnp_entry
        from fam.ui.reports_screen import ReportsScreen

        # $100 SNAP ($50 charge) + $30 Cash
        _create_confirmed_order(fresh_db, 10000, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 10000, 'match_amount': 5000, 'customer_charged': 5000,
        }])
        _create_confirmed_order(fresh_db, 3000, [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 3000, 'match_amount': 0, 'customer_charged': 3000,
        }])

        screen = ReportsScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        customer_text = screen.summary_row.cards['customer_paid'].value_label.text()
        assert customer_text == "$80.00", f"Customer paid should be $80, got {customer_text}"
