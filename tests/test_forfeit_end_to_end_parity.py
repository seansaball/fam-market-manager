"""End-to-end Customer Forfeit parity across every user-visible
surface (v2.0.7-final, Option B, schema v36).

For a single Phase B transaction (1 Food RX token on a $6.52
receipt → $3.48 forfeit), this file verifies that EVERY surface
shows the same forfeit value:

  1. PaymentScreen Customer Forfeit summary card.
  2. PaymentConfirmationDialog warning zone (post-confirm
     attempt) — already pinned in test_under_denomination_forfeit.
  3. Saved row in payment_line_items table.
  4. Reports → Vendor Reimbursement → Customer Forfeit column.
  5. Reports → Detailed Ledger → Customer Forfeit column.
  6. AdjustmentDialog re-open → PaymentRow.get_data() round-trip.

Pre-Option B these surfaces drifted from each other (e.g. card
showed pre-forfeit phantom, DB had post-forfeit). The
consolidation makes them all agree dollar-for-dollar.

This test file is the FINAL ROW IDENTITY check: if any of these
surfaces shows a different number, something has regressed.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def phase_b_transaction(tmp_path):
    """Build a saved Phase B transaction: 1 Food RX token ($10
    face) on a $6.52 receipt → $3.48 customer forfeit."""
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import (
        create_transaction, save_payment_line_items,
        confirm_transaction,
    )
    db_file = str(tmp_path / "e2e_forfeit_parity.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(1, 'Food RX', 100.0, 1000, 1, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'Fungetarian')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1)")
    conn.execute(
        "INSERT INTO vendor_payment_methods (vendor_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES "
        "(1, 1, '2099-05-01', 'Open', 'Tester')")
    conn.commit()
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-001-LB1')
    txn_id, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=652,
        customer_order_id=order_id,
        market_day_date='2099-05-01')
    save_payment_line_items(txn_id, [{
        'payment_method_id': 1,
        'method_name_snapshot': 'Food RX',
        'match_percent_snapshot': 100.0,
        'method_amount': 652,
        'match_amount': 0,
        'customer_charged': 652,
        'customer_forfeit_cents': 348,
        'photo_path': None,
    }])
    confirm_transaction(txn_id, confirmed_by='Tester')
    yield conn, txn_id
    close_connection()


class TestForfeitEndToEndParity:
    """Every user-visible surface shows the same $3.48 for the
    user-reported Phase B scenario."""

    def test_db_row_has_forfeit_348(self, phase_b_transaction):
        conn, txn_id = phase_b_transaction
        row = conn.execute(
            "SELECT customer_forfeit_cents FROM "
            "payment_line_items WHERE transaction_id=?",
            (txn_id,)).fetchone()
        assert row['customer_forfeit_cents'] == 348

    def test_vendor_reimbursement_column_shows_348(
            self, phase_b_transaction):
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement,
        )
        conn, _txn_id = phase_b_transaction
        rows = _collect_vendor_reimbursement(conn, [1])
        fungetarian = next(
            r for r in rows if r['Vendor'] == 'Fungetarian')
        assert fungetarian['Customer Forfeit'] == 3.48, (
            f"Vendor Reimbursement Customer Forfeit column must "
            f"show $3.48.  Got: ${fungetarian['Customer Forfeit']:.2f}")
        # Math identity: vendor still gets receipt total
        assert fungetarian['Total Due to Vendor'] == 6.52

    def test_detailed_ledger_column_shows_348(
            self, phase_b_transaction):
        from fam.sync.data_collector import _collect_detailed_ledger
        conn, _txn_id = phase_b_transaction
        rows = _collect_detailed_ledger(conn, 1)
        confirmed = [
            r for r in rows if r.get('Status') == 'Confirmed']
        assert len(confirmed) == 1
        assert confirmed[0]['Customer Forfeit'] == 3.48

    def test_payment_screen_card_shows_348(
            self, qtbot, phase_b_transaction):
        """Open the PaymentScreen on the same order and verify
        the Customer Forfeit summary card matches the DB +
        Reports values exactly."""
        conn, txn_id = phase_b_transaction
        # Find the customer_order_id for this txn
        co_id = conn.execute(
            "SELECT customer_order_id FROM transactions "
            "WHERE id=?", (txn_id,)).fetchone()['customer_order_id']
        # Re-set the order back to Draft so PaymentScreen will
        # load it as an editable order with the saved rows.
        conn.execute(
            "UPDATE customer_orders SET status='Draft' WHERE id=?",
            (co_id,))
        conn.execute(
            "UPDATE transactions SET status='Draft' WHERE id=?",
            (txn_id,))
        conn.commit()

        from fam.ui.payment_screen import PaymentScreen
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(co_id)
        screen._update_summary()

        card_text = (
            screen.summary_row.cards['customer_forfeit']
            .value_label.text())
        assert card_text == '$3.48', (
            f"PaymentScreen Customer Forfeit card must show "
            f"$3.48 (matches DB + Reports).  Got: {card_text!r}")

    def test_adjustment_dialog_round_trip_matches_348(
            self, qtbot, phase_b_transaction):
        """AdjustmentDialog opens the same txn and surfaces the
        forfeit through PaymentRow.get_data()."""
        from fam.models.transaction import get_transaction_by_id
        from fam.ui.admin_screen import AdjustmentDialog
        _conn, txn_id = phase_b_transaction
        txn = get_transaction_by_id(txn_id)
        dlg = AdjustmentDialog(txn)
        qtbot.addWidget(dlg)
        rows = dlg._payment_rows
        assert len(rows) == 1
        data = rows[0].get_data()
        assert data['customer_forfeit_cents'] == 348


class TestRowMathIdentityAcrossSurfaces:
    """The math identity ``customer_charged + customer_forfeit_cents
    == N × denomination`` must hold for every surface that
    aggregates per-row forfeit data.  This pins the invariant F5
    in SYSTEM_INVARIANTS.md."""

    def test_phase_b_row_recovers_token_face_value(
            self, phase_b_transaction):
        conn, txn_id = phase_b_transaction
        row = conn.execute(
            "SELECT pl.customer_charged, pl.customer_forfeit_cents, "
            "pm.denomination "
            "FROM payment_line_items pl "
            "JOIN payment_methods pm "
            "  ON pm.id = pl.payment_method_id "
            "WHERE pl.transaction_id=?",
            (txn_id,)).fetchone()
        # F5 invariant: cc + forfeit recovers a multiple of denom.
        recovered = (row['customer_charged']
                     + row['customer_forfeit_cents'])
        assert recovered == 1000, (
            f"customer_charged ({row['customer_charged']}) + "
            f"customer_forfeit_cents ({row['customer_forfeit_cents']}) "
            f"must equal the customer's physical token face value "
            f"($10 = 1000 cents = 1 × $10 Food RX denomination).  "
            f"Got: {recovered} cents.")
        assert recovered % row['denomination'] == 0
