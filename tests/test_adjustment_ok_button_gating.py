"""Regression: AdjustmentDialog's OK button must be disabled when
the payment allocation doesn't match the receipt total.

User-reported (2026-04-30 onsite):

    "can we prevent the OK button from being clicked if the order
     total isn't fully allocated?  Resulting in erroneous
     unallocated funds I'm concerned about this being prone to
     user error it has to be dummy proof just like the payment
     processing screen so you can't mess it up"

  Two screenshots demonstrating the problem:

    1. Receipt $30.00, SNAP charge $10.00 (allocates $20.00).  Red
       "Payment total ($20.00) does not match receipt total
       ($30.00).  Remaining: $10.00".  OK button still clickable.

    2. Receipt $20.00, SNAP charge $1.00 (allocates $2.00).  Green
       "refund $9.00 to customer" panel + red "Payment total ($2.00)
       does not match receipt total ($20.00).  Remaining: $18.00".
       OK button still clickable.

  Pre-fix flow on OK click: a "Customer Available?" popup asked
  Yes/No.  The "No — customer is gone" path injected Unallocated
  Funds for the gap.  On a partially-entered state this was easy
  to mis-click into, producing erroneous Unallocated Funds rows
  that polluted FAM-absorbed-loss reports.

  Post-fix: OK is hard-disabled while the allocation doesn't
  reconcile, mirroring PaymentScreen's "Confirm Payment" gating —
  the manager either fixes the rows, clicks Auto-Distribute, or
  Cancels.  No silent UF injection on partial entries.

Allowed exception: a denomination overage (customer's physical
checks/tokens exceed remaining headroom by < 1 full unit) is a
legitimate forfeit scenario that the save flow handles correctly,
so OK stays enabled in that specific case.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def adj_db(tmp_path):
    """A market with one confirmed transaction the manager can adjust."""
    db_file = str(tmp_path / "ok_gate.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1)")
    for mid, name, pct, denom in [
            (1, 'SNAP', 100.0, None),
            (2, 'Cash', 0.0, None),
            (4, 'JH Food Bucks', 100.0, 200)]:
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            " denomination, sort_order, is_active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (mid, name, pct, denom, mid))
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, "
            " payment_method_id) VALUES (1, ?)", (mid,))
    for mid in (1, 2, 4):
        conn.execute(
            "INSERT INTO vendor_payment_methods "
            "(vendor_id, payment_method_id) VALUES (1, ?)", (mid,))
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-30', 'Open', 'T')")
    from fam.models.customer_order import (
        create_customer_order, update_customer_order_status,
    )
    from fam.models.transaction import (
        create_transaction, confirm_transaction,
        save_payment_line_items,
    )
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-X', zip_code='15102')
    # $20 receipt, SNAP $10 customer + $10 match = $20 method
    txn_id, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=2000,
        customer_order_id=order_id,
        market_day_date='2026-04-30')
    save_payment_line_items(txn_id, [
        {'payment_method_id': 1,
         'method_name_snapshot': 'SNAP',
         'match_percent_snapshot': 100.0,
         'method_amount': 2000, 'match_amount': 1000,
         'customer_charged': 1000,
         'photo_path': None, 'photo_source_paths': []}])
    confirm_transaction(txn_id, confirmed_by='T')
    update_customer_order_status(order_id, 'Confirmed')
    conn.commit()
    yield conn, txn_id
    close_connection()


def _stub_qmessageboxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, 'question',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Yes))


class TestOKButtonGatedOnAllocation:
    """The OK button must be disabled while the payment total
    doesn't match the receipt total.  Re-enables the moment the
    allocation matches (or the gap is a denom-overage forfeit)."""

    def test_ok_enabled_on_initial_load_when_saved_state_balanced(
            self, qtbot, adj_db, monkeypatch):
        """Opening on a clean saved transaction (allocation matches
        receipt) → OK enabled."""
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        _stub_qmessageboxes(monkeypatch)
        _, txn_id = adj_db
        txn = get_transaction_by_id(txn_id)

        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)

        assert dialog.ok_btn.isEnabled(), (
            "OK must be enabled when the allocation matches the "
            "receipt total on dialog open (no changes yet).")

    def test_ok_disabled_when_receipt_increased_above_payment(
            self, qtbot, adj_db, monkeypatch):
        """Receipt $20 → $30 with SNAP unchanged at $10 (allocates
        $20).  Allocation under by $10 → OK disabled."""
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        _stub_qmessageboxes(monkeypatch)
        _, txn_id = adj_db
        txn = get_transaction_by_id(txn_id)

        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)
        dialog.receipt_spin.setValue(30.00)

        assert not dialog.ok_btn.isEnabled(), (
            "OK must be disabled when the receipt total exceeds the "
            "payment allocation.  Pre-fix the manager could click "
            "OK and inject erroneous Unallocated Funds.")
        assert 'must match' in dialog.ok_btn.toolTip().lower() or \
               'auto-distribute' in dialog.ok_btn.toolTip().lower(), (
            f"OK tooltip must explain why save is blocked.  Got: "
            f"{dialog.ok_btn.toolTip()!r}")

    def test_ok_disabled_when_payment_charge_dropped_below_receipt(
            self, qtbot, adj_db, monkeypatch):
        """Receipt $20, SNAP charge $10 → drop SNAP to $1 (allocates
        $2).  Allocation under by $18 → OK disabled."""
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        _stub_qmessageboxes(monkeypatch)
        _, txn_id = adj_db
        txn = get_transaction_by_id(txn_id)

        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)
        snap_row = dialog._payment_rows[0]
        snap_row._set_active_charge(100)  # $1
        dialog._on_payment_changed()

        assert not dialog.ok_btn.isEnabled(), (
            "OK must be disabled when SNAP charge is reduced below "
            "what's needed to cover the receipt.")

    def test_ok_re_enables_when_allocation_restored(
            self, qtbot, adj_db, monkeypatch):
        """OK toggles back ON when the manager fixes the
        under-allocation."""
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        _stub_qmessageboxes(monkeypatch)
        _, txn_id = adj_db
        txn = get_transaction_by_id(txn_id)

        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)
        snap_row = dialog._payment_rows[0]

        # Break the allocation.
        snap_row._set_active_charge(100)
        dialog._on_payment_changed()
        assert not dialog.ok_btn.isEnabled()

        # Restore it (SNAP $10 + $10 match = $20 method = receipt).
        snap_row._set_active_charge(1000)
        dialog._on_payment_changed()
        assert dialog.ok_btn.isEnabled(), (
            "OK must re-enable when the manager fixes the "
            "allocation back to a balanced state.")

    def test_ok_disabled_when_no_payments_and_nonzero_receipt(
            self, qtbot, adj_db, monkeypatch):
        """No payment rows but a non-zero receipt → OK disabled."""
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        _stub_qmessageboxes(monkeypatch)
        _, txn_id = adj_db
        txn = get_transaction_by_id(txn_id)

        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)
        # Clear the SNAP row's charge.
        dialog._payment_rows[0]._set_active_charge(0)
        dialog._on_payment_changed()

        assert not dialog.ok_btn.isEnabled(), (
            "OK must be disabled when the receipt is non-zero but "
            "no payment rows have been entered.")

    def test_ok_enabled_for_legitimate_denom_forfeit(
            self, qtbot, adj_db, monkeypatch):
        """Denomination overage (physical FB check exceeds remaining
        receipt headroom by < 1 full unit) is a legitimate save
        path.  OK must stay enabled — the save flow's forfeit
        Yes/No popup handles the actual confirmation."""
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        _stub_qmessageboxes(monkeypatch)
        _, txn_id = adj_db
        txn = get_transaction_by_id(txn_id)

        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)

        # Remove the SNAP row, replace with FB ($2 denom).
        # Receipt = $19, FB = 5 × $2 = $10 customer + $10 match =
        # $20 method.  Over receipt by $1 — within one FB-unit
        # effective denomination ($4) → legitimate forfeit.
        while dialog._payment_rows:
            r = dialog._payment_rows[0]
            dialog.rows_layout.removeWidget(r)
            r.deleteLater()
            dialog._payment_rows.remove(r)

        dialog.receipt_spin.setValue(19.00)
        row = dialog._add_payment_row()
        for i in range(row.method_combo.count()):
            if 'food bucks' in row.method_combo.itemText(i).lower():
                row.method_combo.setCurrentIndex(i)
                break
        row._set_active_charge(1000)  # 5 × $2 = $10
        dialog._on_payment_changed()

        assert dialog.ok_btn.isEnabled(), (
            "OK must stay enabled for a denom-overage forfeit "
            "(over by $1 with $4 denom-unit headroom) — that's a "
            "legitimate save scenario the save flow handles via "
            "the forfeit confirmation popup.")
