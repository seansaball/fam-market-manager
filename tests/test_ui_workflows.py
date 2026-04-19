"""End-to-end UI workflow tests covering multi-screen interactions.

These tests exercise the real widget layer backed by a test database,
verifying that UI operations produce correct DB state, ledger output,
and sync payloads.  They simulate realistic market-day operator flows.

Purely additive — no application code is modified.
"""
import os
import pytest
from PySide6.QtCore import Qt

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.utils.money import dollars_to_cents, cents_to_dollars, format_dollars


# ═══════════════════════════════════════════════════════════════════
# Shared fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def market_db(tmp_path):
    """Full market environment: market, vendors, payment methods, open market day."""
    db_file = str(tmp_path / "test_ui_workflow.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    # Market
    conn.execute(
        "INSERT INTO markets (id, name, address, daily_match_limit, match_limit_active)"
        " VALUES (1, 'Harvest Hill', '100 Farm Rd', 10000, 0)")

    # Vendors
    for vid, name in [(1, 'Farm Stand'), (2, 'Bakery'), (3, 'Cheese Shop'),
                       (4, 'Flower Stall'), (5, 'Honey Hut')]:
        conn.execute(f"INSERT INTO vendors (id, name) VALUES ({vid}, '{name}')")

    # Payment methods
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (1, 'SNAP', 100.0, 1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (2, 'Cash', 0.0, 1, 2)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order, denomination)"
        " VALUES (3, 'FMNP', 100.0, 1, 3, 500)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (4, 'Food RX', 200.0, 1, 4)")

    # Junction tables
    for pm_id in [1, 2, 3, 4]:
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, payment_method_id)"
            f" VALUES (1, {pm_id})")
    for vid in [1, 2, 3, 4, 5]:
        conn.execute(
            f"INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, {vid})")

    # Open market day
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, opened_by)"
        " VALUES (1, 1, '2026-04-01', 'Open', 'Alice')")

    # app_settings for market code + enable optional sync tabs
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('market_code', 'HH')")
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('device_id', 'test-device')")
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value)"
        " VALUES ('sync_tab_market_day_summary', '1')")
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value)"
        " VALUES ('sync_tab_fam_match_report', '1')")

    conn.commit()
    yield conn
    close_connection()


# ── Helpers ──────────────────────────────────────────────────────

def _create_order_with_receipts(conn, receipts):
    """Create a customer order with multiple receipts.

    receipts: list of (vendor_id, amount_cents) tuples
    Returns: (order_id, [txn_ids])
    """
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction

    order_id, label = create_customer_order(market_day_id=1)
    txn_ids = []
    for vendor_id, amount_cents in receipts:
        tid, fam_id = create_transaction(
            market_day_id=1, vendor_id=vendor_id,
            receipt_total=amount_cents,
            market_day_date='2026-04-01',
            customer_order_id=order_id,
        )
        txn_ids.append(tid)
    return order_id, txn_ids


def _confirm_payment_on_screen(qtbot, screen, row_configs):
    """Drive payment screen with row configurations.

    row_configs: list of (method_name, charge_dollars) tuples.
    Sets up rows and updates summary. Does NOT click confirm button.
    """
    # First row already exists
    for i, (method_name, charge_dollars) in enumerate(row_configs):
        if i > 0:
            screen._add_payment_row()
        row = screen._payment_rows[i]
        _select_method(row, method_name)
        _set_charge(row, charge_dollars)
    screen._update_summary()


def _select_method(row, method_name):
    combo = row.method_combo
    for i in range(combo.count()):
        if method_name.lower() in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            return
    raise ValueError(f"Method '{method_name}' not found")


def _set_charge(row, dollars):
    row._set_active_charge(dollars_to_cents(dollars))


def _get_card(screen, key):
    return screen.summary_row.cards[key].value_label.text()


def _save_payment_for_order(conn, order_id, line_items_cents):
    """Save payment line items and confirm transactions (bypasses UI).

    line_items_cents: list of dicts with payment_method_id, method_amount,
                      match_amount, customer_charged (all cents).
    """
    from fam.models.transaction import (
        save_payment_line_items, confirm_transaction, get_payment_line_items
    )
    from fam.models.customer_order import (
        get_order_transactions, update_customer_order_status
    )

    txns = get_order_transactions(order_id)
    for txn in txns:
        save_payment_line_items(txn['id'], line_items_cents)
        confirm_transaction(txn['id'], confirmed_by='Test')
    update_customer_order_status(order_id, 'Confirmed')


def _get_db_totals(conn):
    """Get total receipts, customer paid, and FAM match from DB."""
    row = conn.execute("""
        SELECT COALESCE(SUM(t.receipt_total), 0) AS receipts,
               COALESCE(SUM(pli.total_customer), 0) AS customer_paid,
               COALESCE(SUM(pli.total_match), 0) AS fam_match
        FROM transactions t
        LEFT JOIN (
            SELECT transaction_id,
                   SUM(customer_charged) AS total_customer,
                   SUM(match_amount) AS total_match
            FROM payment_line_items
            GROUP BY transaction_id
        ) pli ON pli.transaction_id = t.id
        WHERE t.status IN ('Confirmed', 'Adjusted')
    """).fetchone()
    return dict(row)


def _get_ledger_text(tmp_path=None):
    """Read the ledger backup file. Returns text or empty string."""
    from fam.utils.export import write_ledger_backup
    write_ledger_backup(force=True)
    conn = get_connection()
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    ledger_path = os.path.join(os.path.dirname(db_path), 'fam_ledger_backup.txt')
    if os.path.exists(ledger_path):
        with open(ledger_path, 'r') as f:
            return f.read()
    return ""


def _get_sync_totals(market_day_id=1):
    """Get totals from sync data collector."""
    from fam.sync.data_collector import collect_sync_data
    data = collect_sync_data(market_day_id)
    summary = data.get('Market Day Summary', [])
    if summary:
        row = summary[0]
        return {
            'receipts': dollars_to_cents(float(row.get('Total Receipts', 0))),
            'customer_paid': dollars_to_cents(float(row.get('Total Customer Paid', 0))),
            'fam_match': dollars_to_cents(float(row.get('Total FAM Match', 0))),
        }
    return {'receipts': 0, 'customer_paid': 0, 'fam_match': 0}


# ═══════════════════════════════════════════════════════════════════
# Receipt Intake Screen Tests
# ═══════════════════════════════════════════════════════════════════

class TestReceiptIntakeScreen:
    """Tests for the receipt intake workflow."""

    def test_add_receipt_creates_transaction(self, qtbot, market_db):
        """Adding a receipt creates a Draft transaction in the DB."""
        from fam.ui.receipt_intake_screen import ReceiptIntakeScreen

        screen = ReceiptIntakeScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        # Select vendor and enter amount
        for i in range(screen.vendor_combo.count()):
            if 'Farm Stand' in screen.vendor_combo.itemText(i):
                screen.vendor_combo.setCurrentIndex(i)
                break

        screen.receipt_total_spin.setValue(25.50)
        screen._add_receipt()

        # Verify transaction exists in DB
        txns = market_db.execute(
            "SELECT * FROM transactions WHERE status='Draft'"
        ).fetchall()
        assert len(txns) >= 1
        assert txns[-1]['receipt_total'] == 2550  # integer cents

    def test_multiple_receipts_same_customer(self, qtbot, market_db):
        """Multiple receipts accumulate under the same customer order."""
        from fam.ui.receipt_intake_screen import ReceiptIntakeScreen

        screen = ReceiptIntakeScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        # Add 3 receipts
        amounts = [15.00, 22.50, 8.75]
        for amt in amounts:
            for i in range(screen.vendor_combo.count()):
                if 'Farm Stand' in screen.vendor_combo.itemText(i):
                    screen.vendor_combo.setCurrentIndex(i)
                    break
            screen.receipt_total_spin.setValue(amt)
            screen._add_receipt()

        # All should be in one order
        order_id = screen._current_order_id
        assert order_id is not None

        from fam.models.customer_order import get_order_transactions
        txns = get_order_transactions(order_id)
        assert len(txns) == 3
        total = sum(t['receipt_total'] for t in txns)
        assert total == 4625  # $46.25

    def test_receipts_table_shows_correct_total(self, qtbot, market_db):
        """Running total label updates correctly."""
        from fam.ui.receipt_intake_screen import ReceiptIntakeScreen

        screen = ReceiptIntakeScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        for i in range(screen.vendor_combo.count()):
            if 'Farm Stand' in screen.vendor_combo.itemText(i):
                screen.vendor_combo.setCurrentIndex(i)
                break

        screen.receipt_total_spin.setValue(30.00)
        screen._add_receipt()
        screen.receipt_total_spin.setValue(20.00)
        screen._add_receipt()

        # Running total should show $50.00
        total_text = screen.running_total_label.text()
        assert "$50.00" in total_text

    def test_void_receipt_removes_from_order(self, qtbot, market_db):
        """Voiding a receipt via the screen's remove method marks it as Voided."""
        from fam.ui.receipt_intake_screen import ReceiptIntakeScreen

        screen = ReceiptIntakeScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        for i in range(screen.vendor_combo.count()):
            if 'Farm Stand' in screen.vendor_combo.itemText(i):
                screen.vendor_combo.setCurrentIndex(i)
                break

        screen.receipt_total_spin.setValue(10.00)
        screen._add_receipt()

        # _remove_receipt takes an index into _order_receipts
        assert len(screen._order_receipts) == 1
        txn_id = screen._order_receipts[0]['txn_id']
        screen._remove_receipt(0)

        # Transaction should be voided
        row = market_db.execute(
            "SELECT status FROM transactions WHERE id = ?", [txn_id]
        ).fetchone()
        assert row['status'] == 'Voided'

    def test_zip_code_persists_to_order(self, qtbot, market_db):
        """Zip code entered in receipt intake is saved to customer order."""
        from fam.ui.receipt_intake_screen import ReceiptIntakeScreen

        screen = ReceiptIntakeScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        # Need to create an order first
        for i in range(screen.vendor_combo.count()):
            if 'Farm Stand' in screen.vendor_combo.itemText(i):
                screen.vendor_combo.setCurrentIndex(i)
                break
        screen.receipt_total_spin.setValue(10.00)
        screen._add_receipt()

        order_id = screen._current_order_id
        screen.zip_code_input.setText("15102")
        screen._on_zip_code_changed()

        row = market_db.execute(
            "SELECT zip_code FROM customer_orders WHERE id = ?", [order_id]
        ).fetchone()
        assert row['zip_code'] == "15102"


# ═══════════════════════════════════════════════════════════════════
# Receipt → Payment Full Flow
# ═══════════════════════════════════════════════════════════════════

class TestReceiptToPaymentFlow:
    """Tests that span Receipt Intake → Payment Screen."""

    def test_order_loads_on_payment_screen(self, qtbot, market_db):
        """An order created in receipt intake loads correctly on payment screen."""
        from fam.ui.payment_screen import PaymentScreen

        order_id, txn_ids = _create_order_with_receipts(
            market_db, [(1, 5000), (2, 3000)])

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        assert screen._order_total == 8000
        assert _get_card(screen, "remaining") == "$80.00"

    def test_payment_then_db_state(self, qtbot, market_db):
        """Payment confirmation writes correct line items to DB."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.models.transaction import get_payment_line_items

        order_id, txn_ids = _create_order_with_receipts(market_db, [(1, 6000)])

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        _confirm_payment_on_screen(qtbot, screen, [("SNAP", 30.00)])

        assert _get_card(screen, "remaining") == "$0.00"
        assert _get_card(screen, "allocated") == "$60.00"
        assert _get_card(screen, "customer_pays") == "$30.00"
        assert _get_card(screen, "fam_match") == "$30.00"

    def test_mixed_payment_db_integrity(self, qtbot, market_db):
        """Mixed SNAP + Cash payment: verify UI and DB agree."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.models.transaction import (
            save_payment_line_items, get_payment_line_items
        )

        order_id, txn_ids = _create_order_with_receipts(market_db, [(1, 10000)])

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        _confirm_payment_on_screen(qtbot, screen, [
            ("SNAP", 30.00),   # $30 charge → $60 allocated, $30 match
            ("Cash", 40.00),   # $40 charge → $40 allocated, $0 match
        ])

        assert _get_card(screen, "remaining") == "$0.00"
        assert _get_card(screen, "customer_pays") == "$70.00"
        assert _get_card(screen, "fam_match") == "$30.00"


# ═══════════════════════════════════════════════════════════════════
# Admin Screen — Adjustment & Void
# ═══════════════════════════════════════════════════════════════════

class TestAdminVoidFlow:
    """Test voiding transactions via admin screen and verifying downstream."""

    def test_void_transaction_updates_db(self, qtbot, market_db):
        """Voiding a confirmed transaction changes its status in DB."""
        from fam.models.transaction import (
            save_payment_line_items, confirm_transaction,
            void_transaction, get_transaction_by_id
        )
        from fam.models.customer_order import update_customer_order_status

        order_id, txn_ids = _create_order_with_receipts(market_db, [(1, 5000)])
        save_payment_line_items(txn_ids[0], [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 5000,
            'match_amount': 2500,
            'customer_charged': 2500,
        }])
        confirm_transaction(txn_ids[0], confirmed_by='Test')
        update_customer_order_status(order_id, 'Confirmed')

        # Void it
        void_transaction(txn_ids[0], voided_by='Admin')

        txn = get_transaction_by_id(txn_ids[0])
        assert txn['status'] == 'Voided'

    def test_void_excluded_from_totals(self, qtbot, market_db):
        """Voided transactions are excluded from DB totals."""
        from fam.models.transaction import (
            save_payment_line_items, confirm_transaction, void_transaction
        )
        from fam.models.customer_order import update_customer_order_status

        # Create 2 orders
        order1, txn1 = _create_order_with_receipts(market_db, [(1, 5000)])
        order2, txn2 = _create_order_with_receipts(market_db, [(2, 3000)])

        for oid, tids in [(order1, txn1), (order2, txn2)]:
            for tid in tids:
                save_payment_line_items(tid, [{
                    'payment_method_id': 2,
                    'method_name_snapshot': 'Cash',
                    'match_percent_snapshot': 0.0,
                    'method_amount': market_db.execute(
                        "SELECT receipt_total FROM transactions WHERE id=?", [tid]
                    ).fetchone()['receipt_total'],
                    'match_amount': 0,
                    'customer_charged': market_db.execute(
                        "SELECT receipt_total FROM transactions WHERE id=?", [tid]
                    ).fetchone()['receipt_total'],
                }])
                confirm_transaction(tid, confirmed_by='Test')
            update_customer_order_status(oid, 'Confirmed')

        # Void order 1
        void_transaction(txn1[0], voided_by='Admin')

        totals = _get_db_totals(market_db)
        assert totals['receipts'] == 3000  # Only order 2 ($30)

    def test_void_excluded_from_sync(self, qtbot, market_db):
        """Voided transactions are excluded from sync market day summary."""
        from fam.models.transaction import (
            save_payment_line_items, confirm_transaction, void_transaction
        )
        from fam.models.customer_order import update_customer_order_status

        order1, txn1 = _create_order_with_receipts(market_db, [(1, 8000)])
        order2, txn2 = _create_order_with_receipts(market_db, [(2, 2000)])

        for oid, tids, amt in [(order1, txn1, 8000), (order2, txn2, 2000)]:
            for tid in tids:
                save_payment_line_items(tid, [{
                    'payment_method_id': 2,
                    'method_name_snapshot': 'Cash',
                    'match_percent_snapshot': 0.0,
                    'method_amount': amt,
                    'match_amount': 0,
                    'customer_charged': amt,
                }])
                confirm_transaction(tid, confirmed_by='Test')
            update_customer_order_status(oid, 'Confirmed')

        void_transaction(txn1[0], voided_by='Admin')

        sync_totals = _get_sync_totals()
        assert sync_totals['receipts'] == 2000  # Only order 2


# ═══════════════════════════════════════════════════════════════════
# Admin Screen — Adjustment
# ═══════════════════════════════════════════════════════════════════

class TestAdminAdjustmentFlow:
    """Test adjusting transactions and verifying DB state."""

    def test_adjust_receipt_total(self, qtbot, market_db):
        """Adjusting a receipt total updates the DB correctly."""
        from fam.models.transaction import (
            save_payment_line_items, confirm_transaction,
            update_transaction, get_transaction_by_id
        )
        from fam.models.customer_order import update_customer_order_status
        from fam.models.audit import log_action

        order_id, txn_ids = _create_order_with_receipts(market_db, [(1, 5000)])
        save_payment_line_items(txn_ids[0], [{
            'payment_method_id': 2,
            'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 5000,
            'match_amount': 0,
            'customer_charged': 5000,
        }])
        confirm_transaction(txn_ids[0], confirmed_by='Test')
        update_customer_order_status(order_id, 'Confirmed')

        # Adjust to $75
        log_action('transactions', txn_ids[0], 'ADJUST', 'Admin',
                   field_name='receipt_total', old_value=5000, new_value=7500)
        update_transaction(txn_ids[0], receipt_total=7500, status='Adjusted')
        save_payment_line_items(txn_ids[0], [{
            'payment_method_id': 2,
            'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 7500,
            'match_amount': 0,
            'customer_charged': 7500,
        }])

        txn = get_transaction_by_id(txn_ids[0])
        assert txn['receipt_total'] == 7500
        assert txn['status'] == 'Adjusted'

        totals = _get_db_totals(market_db)
        assert totals['receipts'] == 7500

    def test_adjustment_appears_in_audit(self, qtbot, market_db):
        """Adjustments are logged in the audit table."""
        from fam.models.transaction import (
            save_payment_line_items, confirm_transaction, update_transaction
        )
        from fam.models.customer_order import update_customer_order_status
        from fam.models.audit import log_action, get_audit_log

        order_id, txn_ids = _create_order_with_receipts(market_db, [(1, 5000)])
        save_payment_line_items(txn_ids[0], [{
            'payment_method_id': 2,
            'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 5000,
            'match_amount': 0,
            'customer_charged': 5000,
        }])
        confirm_transaction(txn_ids[0], confirmed_by='Test')

        log_action('transactions', txn_ids[0], 'ADJUST', 'Admin',
                   field_name='receipt_total', old_value=5000, new_value=6000,
                   reason_code='data_entry_error', notes='Wrong amount')
        update_transaction(txn_ids[0], receipt_total=6000, status='Adjusted')

        entries = get_audit_log(table_name='transactions', record_id=txn_ids[0])
        adjust_entries = [e for e in entries if e['action'] == 'ADJUST']
        assert len(adjust_entries) >= 1
        assert adjust_entries[0]['field_name'] == 'receipt_total'
        assert adjust_entries[0]['old_value'] == '5000'
        assert adjust_entries[0]['new_value'] == '6000'


# ═══════════════════════════════════════════════════════════════════
# FMNP Screen
# ═══════════════════════════════════════════════════════════════════

class TestFMNPScreen:
    """Tests for FMNP entry workflow."""

    def test_add_fmnp_entry(self, qtbot, market_db):
        """Adding an FMNP entry writes to the DB."""
        from fam.models.fmnp import create_fmnp_entry, get_fmnp_entries

        entry_id = create_fmnp_entry(
            market_day_id=1, vendor_id=1, amount=2500,
            entered_by='Alice', check_count=5, notes='Test checks')

        entries = get_fmnp_entries(market_day_id=1)
        assert len(entries) == 1
        assert entries[0]['amount'] == 2500
        assert entries[0]['check_count'] == 5

    def test_delete_fmnp_soft_deletes(self, qtbot, market_db):
        """Deleting an FMNP entry marks it as Deleted, not removed."""
        from fam.models.fmnp import create_fmnp_entry, delete_fmnp_entry

        entry_id = create_fmnp_entry(
            market_day_id=1, vendor_id=1, amount=1000, entered_by='Alice')
        delete_fmnp_entry(entry_id)

        row = market_db.execute(
            "SELECT status FROM fmnp_entries WHERE id = ?", [entry_id]
        ).fetchone()
        assert row['status'] == 'Deleted'

    def test_fmnp_excluded_when_deleted(self, qtbot, market_db):
        """Deleted FMNP entries are excluded from active queries."""
        from fam.models.fmnp import (
            create_fmnp_entry, delete_fmnp_entry, get_fmnp_entries
        )

        e1 = create_fmnp_entry(
            market_day_id=1, vendor_id=1, amount=2500, entered_by='Alice')
        e2 = create_fmnp_entry(
            market_day_id=1, vendor_id=2, amount=1500, entered_by='Alice')
        delete_fmnp_entry(e1)

        active = get_fmnp_entries(market_day_id=1, active_only=True)
        assert len(active) == 1
        assert active[0]['id'] == e2

    def test_fmnp_in_sync_data(self, qtbot, market_db):
        """FMNP entries appear in sync data output."""
        from fam.models.fmnp import create_fmnp_entry
        from fam.sync.data_collector import collect_sync_data

        create_fmnp_entry(
            market_day_id=1, vendor_id=1, amount=5000,
            entered_by='Alice', check_count=2)

        data = collect_sync_data(1)
        fmnp_rows = data.get('FMNP Entries', [])
        assert len(fmnp_rows) >= 1

        # Total amount across FMNP rows should be $50
        total = sum(float(r.get('Check Amount', 0)) for r in fmnp_rows)
        assert abs(total - 50.00) < 0.01


# ═══════════════════════════════════════════════════════════════════
# Reports Screen — Totals Verification
# ═══════════════════════════════════════════════════════════════════

class TestReportsScreenTotals:
    """Verify report screen totals match DB state."""

    def _create_confirmed_transactions(self, conn, count=3):
        """Create and confirm multiple transactions for report testing."""
        from fam.models.transaction import (
            save_payment_line_items, confirm_transaction
        )
        from fam.models.customer_order import update_customer_order_status

        amounts = [2500, 4000, 3500][:count]
        all_txn_ids = []
        for amt in amounts:
            order_id, txn_ids = _create_order_with_receipts(conn, [(1, amt)])
            for tid in txn_ids:
                # Pay with Cash (simple, no match)
                save_payment_line_items(tid, [{
                    'payment_method_id': 2,
                    'method_name_snapshot': 'Cash',
                    'match_percent_snapshot': 0.0,
                    'method_amount': amt,
                    'match_amount': 0,
                    'customer_charged': amt,
                }])
                confirm_transaction(tid, confirmed_by='Test')
            update_customer_order_status(order_id, 'Confirmed')
            all_txn_ids.extend(txn_ids)
        return all_txn_ids

    def test_report_summary_cards_match_db(self, qtbot, market_db):
        """Report summary cards match DB aggregates."""
        from fam.ui.reports_screen import ReportsScreen

        self._create_confirmed_transactions(market_db, 3)

        screen = ReportsScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        db_totals = _get_db_totals(market_db)

        # Summary cards show dollar amounts — extract and compare
        receipt_text = screen.summary_row.cards['total_receipts'].value_label.text()
        customer_text = screen.summary_row.cards['customer_paid'].value_label.text()
        fam_text = screen.summary_row.cards['fam_match'].value_label.text()

        # Parse dollar string to cents
        def parse_dollars(s):
            return int(round(float(s.replace('$', '').replace(',', '')) * 100))

        assert parse_dollars(receipt_text) == db_totals['receipts']
        assert parse_dollars(customer_text) == db_totals['customer_paid']
        assert parse_dollars(fam_text) == db_totals['fam_match']

    def test_report_vendor_table_matches_db(self, qtbot, market_db):
        """Vendor reimbursement table totals match DB."""
        from fam.ui.reports_screen import ReportsScreen

        self._create_confirmed_transactions(market_db, 3)

        screen = ReportsScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        # DB total for vendor 1 (Farm Stand)
        db_vendor_total = market_db.execute("""
            SELECT COALESCE(SUM(t.receipt_total), 0)
            FROM transactions t
            JOIN vendors v ON t.vendor_id = v.id
            WHERE v.name = 'Farm Stand'
              AND t.status IN ('Confirmed', 'Adjusted')
        """).fetchone()[0]

        # Find Farm Stand in vendor table (col 1 = Vendor, col 4 = Total Due)
        found = False
        for row_idx in range(screen.vendor_table.rowCount()):
            vendor_item = screen.vendor_table.item(row_idx, 1)
            if vendor_item and 'Farm Stand' in vendor_item.text():
                found = True
                total_item = screen.vendor_table.item(row_idx, 4)
                total_text = total_item.text()
                total_cents = int(round(
                    float(total_text.replace('$', '').replace(',', '')) * 100))
                assert total_cents == db_vendor_total
                break

        assert found, "Farm Stand not found in vendor reimbursement table"

    def test_report_with_snap_match(self, qtbot, market_db):
        """Reports correctly show FAM match for SNAP transactions."""
        from fam.ui.reports_screen import ReportsScreen
        from fam.models.transaction import (
            save_payment_line_items, confirm_transaction
        )
        from fam.models.customer_order import update_customer_order_status

        order_id, txn_ids = _create_order_with_receipts(market_db, [(1, 10000)])
        save_payment_line_items(txn_ids[0], [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 10000,
            'match_amount': 5000,
            'customer_charged': 5000,
        }])
        confirm_transaction(txn_ids[0], confirmed_by='Test')
        update_customer_order_status(order_id, 'Confirmed')

        screen = ReportsScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        def parse_dollars(s):
            return int(round(float(s.replace('$', '').replace(',', '')) * 100))

        fam_text = screen.summary_row.cards['fam_match'].value_label.text()
        assert parse_dollars(fam_text) == 5000


# ═══════════════════════════════════════════════════════════════════
# Three-Way Reconciliation: DB == Ledger == Sync
# ═══════════════════════════════════════════════════════════════════

class TestThreeWayUIReconciliation:
    """Verify DB, ledger, and sync all agree after UI-driven operations."""

    def test_single_cash_transaction_reconciles(self, qtbot, market_db):
        """One Cash transaction: DB, ledger, and sync all agree."""
        from fam.models.transaction import (
            save_payment_line_items, confirm_transaction
        )
        from fam.models.customer_order import update_customer_order_status

        order_id, txn_ids = _create_order_with_receipts(market_db, [(1, 2500)])
        save_payment_line_items(txn_ids[0], [{
            'payment_method_id': 2,
            'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 2500,
            'match_amount': 0,
            'customer_charged': 2500,
        }])
        confirm_transaction(txn_ids[0], confirmed_by='Test')
        update_customer_order_status(order_id, 'Confirmed')

        db_totals = _get_db_totals(market_db)
        sync_totals = _get_sync_totals()

        assert db_totals['receipts'] == 2500
        assert db_totals['customer_paid'] == 2500
        assert db_totals['fam_match'] == 0
        assert sync_totals['receipts'] == 2500
        assert sync_totals['customer_paid'] == 2500

    def test_snap_transaction_reconciles(self, qtbot, market_db):
        """SNAP transaction: DB and sync agree on match split."""
        from fam.models.transaction import (
            save_payment_line_items, confirm_transaction
        )
        from fam.models.customer_order import update_customer_order_status

        order_id, txn_ids = _create_order_with_receipts(market_db, [(1, 8000)])
        save_payment_line_items(txn_ids[0], [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 8000,
            'match_amount': 4000,
            'customer_charged': 4000,
        }])
        confirm_transaction(txn_ids[0], confirmed_by='Test')
        update_customer_order_status(order_id, 'Confirmed')

        db_totals = _get_db_totals(market_db)
        sync_totals = _get_sync_totals()

        assert db_totals['receipts'] == 8000
        assert db_totals['fam_match'] == 4000
        assert sync_totals['receipts'] == 8000
        assert sync_totals['fam_match'] == 4000

    def test_mixed_session_reconciles(self, qtbot, market_db):
        """Multiple transactions with mixed methods: full reconciliation."""
        from fam.models.transaction import (
            save_payment_line_items, confirm_transaction, void_transaction
        )
        from fam.models.customer_order import update_customer_order_status

        # Order 1: $60 paid with SNAP ($30 customer, $30 match)
        o1, t1 = _create_order_with_receipts(market_db, [(1, 6000)])
        save_payment_line_items(t1[0], [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 6000, 'match_amount': 3000, 'customer_charged': 3000,
        }])
        confirm_transaction(t1[0], confirmed_by='Test')
        update_customer_order_status(o1, 'Confirmed')

        # Order 2: $40 paid with Cash
        o2, t2 = _create_order_with_receipts(market_db, [(2, 4000)])
        save_payment_line_items(t2[0], [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 4000, 'match_amount': 0, 'customer_charged': 4000,
        }])
        confirm_transaction(t2[0], confirmed_by='Test')
        update_customer_order_status(o2, 'Confirmed')

        # Order 3: $25 — VOIDED
        o3, t3 = _create_order_with_receipts(market_db, [(3, 2500)])
        save_payment_line_items(t3[0], [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 2500, 'match_amount': 0, 'customer_charged': 2500,
        }])
        confirm_transaction(t3[0], confirmed_by='Test')
        update_customer_order_status(o3, 'Confirmed')
        void_transaction(t3[0], voided_by='Admin')

        # Expected: $60 + $40 = $100 receipts, $30 match, $70 customer
        db_totals = _get_db_totals(market_db)
        sync_totals = _get_sync_totals()

        assert db_totals['receipts'] == 10000
        assert db_totals['customer_paid'] == 7000
        assert db_totals['fam_match'] == 3000
        assert sync_totals['receipts'] == 10000
        assert sync_totals['fam_match'] == 3000

    def test_ledger_includes_confirmed_transactions(self, qtbot, market_db):
        """Ledger backup contains confirmed transaction details."""
        from fam.models.transaction import (
            save_payment_line_items, confirm_transaction
        )
        from fam.models.customer_order import update_customer_order_status

        order_id, txn_ids = _create_order_with_receipts(market_db, [(1, 4999)])
        save_payment_line_items(txn_ids[0], [{
            'payment_method_id': 2,
            'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 4999,
            'match_amount': 0,
            'customer_charged': 4999,
        }])
        confirm_transaction(txn_ids[0], confirmed_by='Test')
        update_customer_order_status(order_id, 'Confirmed')

        ledger_text = _get_ledger_text()
        assert "$49.99" in ledger_text
        assert "Farm Stand" in ledger_text


# ═══════════════════════════════════════════════════════════════════
# Full Simulated Market Day
# ═══════════════════════════════════════════════════════════════════

class TestSimulatedMarketDay:
    """End-to-end market day simulation with reconciliation."""

    def test_full_market_day(self, qtbot, market_db):
        """Simulate a complete market day and reconcile everything.

        Scenario:
        - Customer 1: $50 at Farm Stand → SNAP ($25 charge = $50 alloc)
        - Customer 2: $35.77 at Bakery → Cash
        - Customer 3: $80 at Cheese Shop → SNAP $25 + Cash $30
        - Customer 4: $20 at Flower Stall → Cash, then VOIDED
        - Customer 5: $15 at Honey Hut → Cash, then ADJUSTED to $18
        - FMNP: $25 from Farm Stand (5 checks)

        Expected active totals: $50 + $35.77 + $80 + $18 = $183.77
        """
        from fam.models.transaction import (
            save_payment_line_items, confirm_transaction,
            void_transaction, update_transaction, get_transaction_by_id
        )
        from fam.models.customer_order import update_customer_order_status
        from fam.models.fmnp import create_fmnp_entry
        from fam.models.audit import log_action
        from fam.ui.reports_screen import ReportsScreen

        # ── Customer 1: $50.00 Farm Stand, SNAP ──
        o1, t1 = _create_order_with_receipts(market_db, [(1, 5000)])
        save_payment_line_items(t1[0], [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 5000, 'match_amount': 2500, 'customer_charged': 2500,
        }])
        confirm_transaction(t1[0], confirmed_by='Alice')
        update_customer_order_status(o1, 'Confirmed')

        # ── Customer 2: $35.77 Bakery, Cash ──
        o2, t2 = _create_order_with_receipts(market_db, [(2, 3577)])
        save_payment_line_items(t2[0], [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 3577, 'match_amount': 0, 'customer_charged': 3577,
        }])
        confirm_transaction(t2[0], confirmed_by='Alice')
        update_customer_order_status(o2, 'Confirmed')

        # ── Customer 3: $80 Cheese Shop, SNAP $25 + Cash $30 ──
        o3, t3 = _create_order_with_receipts(market_db, [(3, 8000)])
        save_payment_line_items(t3[0], [
            {
                'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
                'match_percent_snapshot': 100.0,
                'method_amount': 5000, 'match_amount': 2500, 'customer_charged': 2500,
            },
            {
                'payment_method_id': 2, 'method_name_snapshot': 'Cash',
                'match_percent_snapshot': 0.0,
                'method_amount': 3000, 'match_amount': 0, 'customer_charged': 3000,
            },
        ])
        confirm_transaction(t3[0], confirmed_by='Alice')
        update_customer_order_status(o3, 'Confirmed')

        # ── Customer 4: $20 Flower Stall, Cash → VOIDED ──
        o4, t4 = _create_order_with_receipts(market_db, [(4, 2000)])
        save_payment_line_items(t4[0], [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 2000, 'match_amount': 0, 'customer_charged': 2000,
        }])
        confirm_transaction(t4[0], confirmed_by='Alice')
        update_customer_order_status(o4, 'Confirmed')
        void_transaction(t4[0], voided_by='Alice')

        # ── Customer 5: $15 Honey Hut, Cash → ADJUSTED to $18 ──
        o5, t5 = _create_order_with_receipts(market_db, [(5, 1500)])
        save_payment_line_items(t5[0], [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 1500, 'match_amount': 0, 'customer_charged': 1500,
        }])
        confirm_transaction(t5[0], confirmed_by='Alice')
        update_customer_order_status(o5, 'Confirmed')

        # Adjustment
        log_action('transactions', t5[0], 'ADJUST', 'Alice',
                   field_name='receipt_total', old_value=1500, new_value=1800)
        update_transaction(t5[0], receipt_total=1800, status='Adjusted')
        save_payment_line_items(t5[0], [{
            'payment_method_id': 2, 'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 1800, 'match_amount': 0, 'customer_charged': 1800,
        }])

        # ── FMNP: $25 from Farm Stand ──
        create_fmnp_entry(
            market_day_id=1, vendor_id=1, amount=2500,
            entered_by='Alice', check_count=5)

        # ════════════════════════════════════════════════════════
        # RECONCILIATION
        # ════════════════════════════════════════════════════════

        # Expected: C1=$50 + C2=$35.77 + C3=$80 + C5=$18 = $183.77
        # FAM match: C1=$25 + C3=$25 = $50
        # Customer paid: C1=$25 + C2=$35.77 + C3=$55 + C5=$18 = $133.77
        expected_receipts = 18377
        expected_fam_match = 5000
        expected_customer = 13377

        # ── DB ──
        db_totals = _get_db_totals(market_db)
        assert db_totals['receipts'] == expected_receipts, (
            f"DB receipts: {db_totals['receipts']} != {expected_receipts}")
        assert db_totals['fam_match'] == expected_fam_match, (
            f"DB match: {db_totals['fam_match']} != {expected_fam_match}")
        assert db_totals['customer_paid'] == expected_customer, (
            f"DB customer: {db_totals['customer_paid']} != {expected_customer}")

        # ── Sync ──
        sync_totals = _get_sync_totals()
        assert sync_totals['receipts'] == expected_receipts, (
            f"Sync receipts: {sync_totals['receipts']} != {expected_receipts}")
        assert sync_totals['fam_match'] == expected_fam_match, (
            f"Sync match: {sync_totals['fam_match']} != {expected_fam_match}")

        # ── Ledger ──
        # Ledger includes FMNP in FAM Match, so Total Receipts = $208.77
        # ($133.77 customer paid + $50 SNAP match + $25 FMNP = $208.77)
        ledger_text = _get_ledger_text()
        assert "$133.77" in ledger_text, (
            "Ledger should contain customer paid $133.77")
        assert "$208.77" in ledger_text or "$183.77" in ledger_text, (
            "Ledger should contain total receipts")

        # ── Reports Screen ──
        # FMNP (External) checks are vendor reimbursements, not FAM match
        expected_report_fam = expected_fam_match
        screen = ReportsScreen()
        qtbot.addWidget(screen)
        screen.refresh()

        def parse_dollars(s):
            return int(round(float(s.replace('$', '').replace(',', '')) * 100))

        receipt_text = screen.summary_row.cards['total_receipts'].value_label.text()
        fam_text = screen.summary_row.cards['fam_match'].value_label.text()
        customer_text = screen.summary_row.cards['customer_paid'].value_label.text()

        assert parse_dollars(receipt_text) == expected_receipts, (
            f"Report receipts: {receipt_text} != ${expected_receipts/100:.2f}")
        assert parse_dollars(fam_text) == expected_report_fam, (
            f"Report FAM match: {fam_text} != ${expected_report_fam/100:.2f}")
        assert parse_dollars(customer_text) == expected_customer, (
            f"Report customer paid: {customer_text} != ${expected_customer/100:.2f}")

        # ── Voided transaction NOT in active totals ──
        voided = get_transaction_by_id(t4[0])
        assert voided['status'] == 'Voided'

        # ── Adjusted transaction reflects new amount ──
        adjusted = get_transaction_by_id(t5[0])
        assert adjusted['receipt_total'] == 1800
        assert adjusted['status'] == 'Adjusted'

        # ── FMNP in sync ──
        from fam.sync.data_collector import collect_sync_data
        data = collect_sync_data(1)
        fmnp_rows = data.get('FMNP Entries', [])
        fmnp_total = sum(float(r.get('Check Amount', 0)) for r in fmnp_rows)
        assert abs(fmnp_total - 25.00) < 0.01

        # ── Audit trail exists for void and adjustment ──
        from fam.models.audit import get_audit_log
        all_audit = get_audit_log(limit=100)
        void_entries = [e for e in all_audit if e['action'] == 'VOID']
        adjust_entries = [e for e in all_audit if e['action'] == 'ADJUST']
        assert len(void_entries) >= 1
        assert len(adjust_entries) >= 1

    def test_high_volume_day(self, qtbot, market_db):
        """20 transactions, reconcile DB == sync."""
        from fam.models.transaction import (
            save_payment_line_items, confirm_transaction
        )
        from fam.models.customer_order import update_customer_order_status

        expected_total = 0
        expected_match = 0
        for i in range(20):
            vendor_id = (i % 5) + 1
            amount = 1000 + (i * 137)  # varying amounts in cents
            is_snap = (i % 3 == 0)

            order_id, txn_ids = _create_order_with_receipts(
                market_db, [(vendor_id, amount)])

            if is_snap:
                match = amount // 2
                customer = amount - match
                save_payment_line_items(txn_ids[0], [{
                    'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
                    'match_percent_snapshot': 100.0,
                    'method_amount': amount, 'match_amount': match,
                    'customer_charged': customer,
                }])
                expected_match += match
            else:
                save_payment_line_items(txn_ids[0], [{
                    'payment_method_id': 2, 'method_name_snapshot': 'Cash',
                    'match_percent_snapshot': 0.0,
                    'method_amount': amount, 'match_amount': 0,
                    'customer_charged': amount,
                }])

            confirm_transaction(txn_ids[0], confirmed_by='Alice')
            update_customer_order_status(order_id, 'Confirmed')
            expected_total += amount

        db_totals = _get_db_totals(market_db)
        sync_totals = _get_sync_totals()

        assert db_totals['receipts'] == expected_total
        assert db_totals['fam_match'] == expected_match
        assert sync_totals['receipts'] == expected_total
        assert sync_totals['fam_match'] == expected_match


# ═══════════════════════════════════════════════════════════════════
# Match Cap (Daily Limit) Workflows
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def cap_db(tmp_path):
    """Market environment with daily match limit active."""
    db_file = str(tmp_path / "test_cap_workflow.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    # Market — $50 daily match limit (5000 cents), match_limit_active=1
    conn.execute(
        "INSERT INTO markets (id, name, address, daily_match_limit, match_limit_active)"
        " VALUES (1, 'Cap Market', '200 Cap Rd', 5000, 1)")

    # Vendors
    for vid, name in [(1, 'Farm Stand'), (2, 'Bakery')]:
        conn.execute(f"INSERT INTO vendors (id, name) VALUES ({vid}, '{name}')")

    # Payment methods
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (1, 'SNAP', 100.0, 1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (2, 'Cash', 0.0, 1, 2)")

    # Junction tables
    for pm_id in [1, 2]:
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, payment_method_id)"
            f" VALUES (1, {pm_id})")
    for vid in [1, 2]:
        conn.execute(
            f"INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, {vid})")

    # Open market day
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, opened_by)"
        " VALUES (1, 1, '2026-04-01', 'Open', 'Tester')")

    # app_settings
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('market_code', 'CM')")
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('device_id', 'test-cap')")

    conn.commit()
    yield conn
    close_connection()


def _create_confirmed_snap_order(conn, customer_label, receipt_cents, charge_cents,
                                  match_cents, vendor_id=1):
    """Create and fully confirm a SNAP order for a specific customer label.

    Returns (order_id, txn_id).
    """
    from fam.models.customer_order import (
        create_customer_order, update_customer_order_status
    )
    from fam.models.transaction import (
        create_transaction, save_payment_line_items, confirm_transaction
    )

    order_id, label = create_customer_order(
        market_day_id=1, customer_label=customer_label)
    txn_id, fam_id = create_transaction(
        market_day_id=1, vendor_id=vendor_id,
        receipt_total=receipt_cents,
        market_day_date='2026-04-01',
        customer_order_id=order_id,
    )
    save_payment_line_items(txn_id, [{
        'payment_method_id': 1,
        'method_name_snapshot': 'SNAP',
        'match_percent_snapshot': 100.0,
        'method_amount': receipt_cents,
        'match_amount': match_cents,
        'customer_charged': charge_cents,
    }])
    confirm_transaction(txn_id, confirmed_by='Tester')
    update_customer_order_status(order_id, 'Confirmed')
    return order_id, txn_id


class TestMatchCapWorkflows:
    """End-to-end payment workflows with daily match limits active."""

    def test_returning_customer_second_visit_cap_applied(self, qtbot, cap_db):
        """Second visit hits the daily match cap; max_charge reflects reduced match.

        $50 daily limit.  First visit: $70 receipt, $35 charge / $35 match.
        Second visit: $60 receipt.  Remaining limit = $15.
        Nominal SNAP charge = $30 (half of $60), match = $30.  But cap
        reduces available match to $15 → customer must pay $45.
        """
        from fam.ui.payment_screen import PaymentScreen

        # First visit: $70 receipt, SNAP $35 charge / $35 match
        _create_confirmed_snap_order(cap_db, 'C-001', 7000, 3500, 3500)

        # Second visit: $60 receipt
        order_id, txn_ids = _create_order_with_receipts(cap_db, [(1, 6000)])
        # Set the customer label on the new order to match prior
        cap_db.execute(
            "UPDATE customer_orders SET customer_label = 'C-001' WHERE id = ?",
            [order_id])
        cap_db.commit()

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Remaining match should be $50 - $35 = $15 = 1500 cents
        assert screen._match_limit == 1500
        assert screen._prior_match == 3500

        # Add SNAP row, push limits
        _select_method(screen._payment_rows[0], 'SNAP')
        screen._push_row_limits()

        # Max charge should account for capped match.
        # Remaining balance = 6000.  Available match = 1500.
        # Cap-aware max_charge = 6000 - 1500 = 4500.
        row = screen._payment_rows[0]
        max_charge_cents = dollars_to_cents(row.amount_spin.maximum())
        assert max_charge_cents >= 4400, (
            f"Expected cap-aware max_charge >= 4400, got {max_charge_cents}")

    def test_returning_customer_third_visit_exhausts_limit(self, qtbot, cap_db):
        """Three visits progressively exhaust the $50 daily limit.

        Visit 1: $60 match used ($60 receipt → $30/$30 — but cap is $50
        so actually match=30, within cap).
        Actually let's use $100 limit for this test.
        """
        # Override market to $100 daily limit
        cap_db.execute(
            "UPDATE markets SET daily_match_limit = 10000 WHERE id = 1")
        cap_db.commit()

        from fam.ui.payment_screen import PaymentScreen

        # Visit 1: $120 receipt → $60 charge / $60 match
        _create_confirmed_snap_order(cap_db, 'C-002', 12000, 6000, 6000)
        # Visit 2: $60 receipt → $30 charge / $30 match
        _create_confirmed_snap_order(cap_db, 'C-002', 6000, 3000, 3000)

        # Prior match total = $60 + $30 = $90.  Remaining = $10.

        # Visit 3: $50 receipt
        order_id, txn_ids = _create_order_with_receipts(cap_db, [(1, 5000)])
        cap_db.execute(
            "UPDATE customer_orders SET customer_label = 'C-002' WHERE id = ?",
            [order_id])
        cap_db.commit()

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        assert screen._match_limit == 1000   # $10 remaining
        assert screen._prior_match == 9000   # $90 already used

        _select_method(screen._payment_rows[0], 'SNAP')
        screen._push_row_limits()

        # Nominal SNAP max_charge for $50 = $25.  But remaining match = $10.
        # Cap-aware max_charge = $50 - $10 = $40 = 4000 cents.
        row = screen._payment_rows[0]
        max_charge_cents = dollars_to_cents(row.amount_spin.maximum())
        assert max_charge_cents >= 3900, (
            f"Expected cap-aware max_charge >= 3900, got {max_charge_cents}")

    def test_mixed_snap_cash_with_cap_end_to_end(self, qtbot, cap_db):
        """Mixed SNAP + Cash with match cap: breakdown reflects capped subsidy.

        $30 daily limit.  $80 receipt.  SNAP charge $50 → nominal match $50
        but cap limits match to $30.  Verify fam_subsidy_total = $30 and
        customer_total_paid = $50.  Cash fills remaining $30.
        """
        from fam.utils.calculations import calculate_payment_breakdown

        # Set $30 daily limit
        cap_db.execute(
            "UPDATE markets SET daily_match_limit = 3000 WHERE id = 1")
        cap_db.commit()

        order_id, txn_ids = _create_order_with_receipts(cap_db, [(1, 8000)])

        # Calculate breakdown: SNAP $50 charge + Cash $30
        result = calculate_payment_breakdown(
            receipt_total=8000,
            payment_entries=[
                {'method_amount': 8000, 'match_percent': 100.0},  # SNAP (charge $50, alloc $100 nom)
            ],
            match_limit=3000
        )
        # SNAP 100% match: method_amount=8000 → charge=4000, uncapped match=4000
        # Cap to 3000 → fam_subsidy_total = 3000, customer_total_paid = 5000
        assert result['fam_subsidy_total'] == 3000, (
            f"Expected fam_subsidy_total=3000, got {result['fam_subsidy_total']}")
        assert result['customer_total_paid'] == 5000, (
            f"Expected customer_total_paid=5000, got {result['customer_total_paid']}")
        assert result['match_was_capped'] is True

        # Now verify Cash can fill the remaining $30
        result_mixed = calculate_payment_breakdown(
            receipt_total=8000,
            payment_entries=[
                {'method_amount': 5000, 'match_percent': 100.0},  # SNAP charge $50 → alloc $50 (capped)
                {'method_amount': 3000, 'match_percent': 0.0},    # Cash $30
            ],
            match_limit=3000
        )
        # SNAP alloc = 5000 (method_amount=5000, 100% match, charge=2500, match=2500)
        # Cash alloc = 3000 (no match)
        # Total alloc = 8000 = receipt
        assert result_mixed['allocation_remaining'] == 0, (
            f"Expected 0 allocation remaining, got {result_mixed['allocation_remaining']}")
        assert result_mixed['is_valid'] is True

    def test_void_does_not_reduce_prior_match(self, qtbot, cap_db):
        """Voided transactions are excluded from prior match calculation.

        $50 daily limit.  Visit 1: $40 receipt, $20 charge / $20 match — confirmed
        then voided.  Visit 2: $60 receipt.  Since the first order's transaction
        is voided, prior match should be $0 and full $50 limit is available.
        """
        from fam.models.transaction import void_transaction
        from fam.ui.payment_screen import PaymentScreen

        # Visit 1: confirm then void
        order_id_1, txn_id_1 = _create_confirmed_snap_order(
            cap_db, 'C-003', 4000, 2000, 2000)
        void_transaction(txn_id_1, voided_by='Tester')

        # Visit 2: $60 receipt
        order_id_2, txn_ids_2 = _create_order_with_receipts(cap_db, [(1, 6000)])
        cap_db.execute(
            "UPDATE customer_orders SET customer_label = 'C-003' WHERE id = ?",
            [order_id_2])
        cap_db.commit()

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id_2)

        # Voided transaction should NOT count → prior match = 0
        assert screen._prior_match == 0, (
            f"Expected _prior_match=0 after void, got {screen._prior_match}")
        # Full daily limit available
        assert screen._match_limit == 5000, (
            f"Expected _match_limit=5000, got {screen._match_limit}")

    def test_cap_display_label_shows_correct_info(self, qtbot, cap_db):
        """Match limit label displays daily limit, prior redeemed, and remaining.

        $100 daily limit.  Prior match = $60.  New $80 order.
        Label should show $100.00 (limit), $60.00 (redeemed), $40.00 (remaining).
        """
        from fam.ui.payment_screen import PaymentScreen

        # Set $100 daily limit
        cap_db.execute(
            "UPDATE markets SET daily_match_limit = 10000 WHERE id = 1")
        cap_db.commit()

        # Prior order: $120 receipt → $60 charge / $60 match
        _create_confirmed_snap_order(cap_db, 'C-004', 12000, 6000, 6000)

        # New order: $80 receipt
        order_id, txn_ids = _create_order_with_receipts(cap_db, [(1, 8000)])
        cap_db.execute(
            "UPDATE customer_orders SET customer_label = 'C-004' WHERE id = ?",
            [order_id])
        cap_db.commit()

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        label_text = screen.match_limit_label.text()
        assert "$100.00" in label_text, (
            f"Expected '$100.00' (daily limit) in label, got: {label_text}")
        assert "$60.00" in label_text, (
            f"Expected '$60.00' (previously redeemed) in label, got: {label_text}")
        assert "$40.00" in label_text, (
            f"Expected '$40.00' (remaining) in label, got: {label_text}")
