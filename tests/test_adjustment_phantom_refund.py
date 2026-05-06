"""Regression: AdjustmentDialog must NOT show a phantom "refund $X"
message when the manager opens it on a cap-inflated transaction
WITHOUT making any changes.

User-reported (2026-04-30 onsite, screenshot of FAM-TM-0c2a-...-0022):

    Receipt $23.30, single SNAP row.  Saved customer_charged was
    $16.17 (cap-inflated because the customer's other transactions
    today consumed most of the $100 daily cap, leaving only $7.13
    for this transaction's match).

    Manager opens Adjust dialog.  Doesn't change anything.  Bottom
    banner reads:

        "If the original payment was collected, refund $4.52 to
         customer.  (Was $16.17, now $11.65)  FAM match: $11.65
         (Match limit: $100.00)"

    The "now $11.65" is the engine's recomputed customer_charged
    using the FULL daily cap ($100) — ignoring the customer's prior
    consumption.  At save time the cap was $7.13 (effective), so
    customer was $16.17.  At adjust time the dialog used $100 (raw)
    and re-derived $11.65, producing a phantom "refund $4.52".
    Worse, the cap-write-back path then OVERWRITES the spinbox with
    the wrong $11.65 value, corrupting the saved data the moment
    the manager hits OK.

Two interlocking bugs caused this
---------------------------------

  1. ``AdjustmentDialog.__init__`` set ``_match_limit`` to the
     market's full ``daily_match_limit`` (e.g. $100) without
     subtracting the customer's prior match consumption.  Mirrored
     PaymentScreen's ``_prior_match`` accounting now to match
     production behaviour.

  2. ``_update_customer_impact`` and ``get_new_line_items`` passed
     ``data['method_amount']`` straight to the engine.  But
     ``PaymentRow.get_data`` *re-derives* method_amount from the
     spinbox's charge via the ``charge × (1 + pct/100)`` formula —
     which is the *uncapped* formula value.  When the saved
     transaction was cap-inflated (charge=customer > formula
     value), get_data returned method = customer × 2 (wrong;
     ≠ saved method).  The engine then treated this inflated
     method as ground truth.

     Fix: cap each non-denom row's method_amount at receipt minus
     pre-summed total denom, mirroring PaymentScreen's
     ``_update_summary`` step 3.  Single-row SNAP-only adjustment
     collapses to "method = receipt".

This test pins the contract: open an adjustment dialog on a
cap-inflated transaction, make no changes, and ``get_new_line_items``
must return the saved values exactly.  No phantom refund.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def cap_inflated_db(tmp_path):
    """A market where customer C-X has TWO confirmed transactions:
      T1: $185.74, customer paid $92.87, FAM matched $92.87 (no cap)
      T2: $23.30, customer paid $16.17, FAM matched $7.13
            (cap-inflated because T1 consumed $92.87 of the $100 cap)

    Yields (conn, t2_id, order_id) for adjusting T2.
    """
    db_file = str(tmp_path / "phantom.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    for vid, name in [(1, 'Saucy'), (2, 'JuiceBar')]:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " denomination, sort_order, is_active) "
        "VALUES (1, 'SNAP', 100.0, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        " payment_method_id) VALUES (1, 1)")
    for vid in (1, 2):
        conn.execute(
            "INSERT INTO vendor_payment_methods "
            "(vendor_id, payment_method_id) VALUES (?, 1)",
            (vid,))
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
    t1_id, _ = create_transaction(
        market_day_id=1, vendor_id=2, receipt_total=18574,
        customer_order_id=order_id,
        market_day_date='2026-04-30')
    save_payment_line_items(t1_id, [
        {'payment_method_id': 1,
         'method_name_snapshot': 'SNAP',
         'match_percent_snapshot': 100.0,
         'method_amount': 18574,
         'match_amount': 9287,
         'customer_charged': 9287,
         'photo_path': None, 'photo_source_paths': []}])
    confirm_transaction(t1_id, confirmed_by='T')
    t2_id, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=2330,
        customer_order_id=order_id,
        market_day_date='2026-04-30')
    save_payment_line_items(t2_id, [
        {'payment_method_id': 1,
         'method_name_snapshot': 'SNAP',
         'match_percent_snapshot': 100.0,
         'method_amount': 2330,
         'match_amount': 713,
         'customer_charged': 1617,
         'photo_path': None, 'photo_source_paths': []}])
    confirm_transaction(t2_id, confirmed_by='T')
    update_customer_order_status(order_id, 'Confirmed')
    conn.commit()
    yield conn, t2_id, order_id
    close_connection()


def _stub_qmessageboxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, 'question',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Yes))


class TestNoPhantomRefundOnDialogOpen:
    """When the AdjustmentDialog opens on a cap-inflated saved
    transaction with NO user changes, the engine must reproduce the
    saved values exactly — so the impact panel reads "No change"
    and the spinbox keeps the saved customer_charged.

    Without these fixes the manager saw "refund $4.52" on dialog
    open and the cap-write-back overwrote the spinbox from $16.17
    to $11.65 — silently corrupting the saved transaction the
    moment they clicked OK.
    """

    def test_match_limit_subtracts_prior_consumption(
            self, qtbot, cap_inflated_db, monkeypatch):
        """``_match_limit`` must equal the customer's REMAINING cap
        budget after subtracting OTHER transactions' match, not the
        full daily cap."""
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        _stub_qmessageboxes(monkeypatch)
        _, t2_id, _ = cap_inflated_db
        txn = get_transaction_by_id(t2_id)

        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)

        assert dialog._match_limit == 713, (
            f"_match_limit must = $7.13 (= $100 daily cap − $92.87 "
            f"consumed by T1), not $100.  Got "
            f"${dialog._match_limit / 100:.2f}.  Without subtracting "
            f"prior consumption, the engine treats the cap as fully "
            f"available and recomputes a smaller customer_charged "
            f"than was saved → phantom refund.")

    def test_get_new_line_items_reproduces_saved_values_unchanged(
            self, qtbot, cap_inflated_db, monkeypatch):
        """No user changes → engine must produce the same
        customer_charged, match_amount, and method_amount that were
        saved.  This is the post-fix contract."""
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        _stub_qmessageboxes(monkeypatch)
        _, t2_id, _ = cap_inflated_db
        txn = get_transaction_by_id(t2_id)

        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)

        items = dialog.get_new_line_items()
        assert len(items) == 1, (
            f"Should have exactly 1 SNAP item, got {len(items)}")
        snap = items[0]
        assert snap['method_amount'] == 2330, (
            f"method_amount must = $23.30 (= receipt total), got "
            f"${snap['method_amount']/100:.2f}.  Pre-fix, get_data "
            f"derived method = charge × 2 = $32.34 from the "
            f"cap-inflated customer charge — corrupting downstream "
            f"engine math.")
        assert snap['customer_charged'] == 1617, (
            f"customer_charged must = saved $16.17, got "
            f"${snap['customer_charged']/100:.2f}.  Mismatch means "
            f"the dialog will SHOW a phantom refund/collect-more "
            f"banner and the writeback will silently overwrite the "
            f"saved value.")
        assert snap['match_amount'] == 713, (
            f"match_amount must = saved $7.13, got "
            f"${snap['match_amount']/100:.2f}")

    def test_impact_label_shows_no_change_on_unchanged_open(
            self, qtbot, cap_inflated_db, monkeypatch):
        """The customer-impact label must say "No change" (or be
        hidden) when the manager hasn't touched anything."""
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        _stub_qmessageboxes(monkeypatch)
        _, t2_id, _ = cap_inflated_db
        txn = get_transaction_by_id(t2_id)

        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)

        text = dialog.customer_impact_label.text()
        # Either no-change wording, or a refund/collect-more wording.
        assert 'refund' not in text.lower(), (
            f"Impact label suggests a refund on an unchanged "
            f"adjustment dialog: {text!r}.  This is the 2026-04-30 "
            f"onsite phantom-refund regression.")
        assert 'collect' not in text.lower() or 'collect from' in text.lower() == False, (
            f"Impact label suggests collecting more on an unchanged "
            f"adjustment dialog: {text!r}")

    def test_spinbox_charge_preserved_on_dialog_open(
            self, qtbot, cap_inflated_db, monkeypatch):
        """The cap-write-back in ``_update_customer_impact`` MUST
        NOT silently overwrite the saved customer_charged on dialog
        open.  Pre-fix, opening the dialog rewrote the spinbox from
        $16.17 to $11.65 — corrupting state before the manager
        could even click OK."""
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        _stub_qmessageboxes(monkeypatch)
        _, t2_id, _ = cap_inflated_db
        txn = get_transaction_by_id(t2_id)

        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)

        snap_row = dialog._payment_rows[0]
        # The saved customer_charged is $16.17 = 1617 cents.
        assert snap_row._get_active_charge() == 1617, (
            f"SNAP row charge must remain at saved $16.17 after "
            f"dialog open.  Got ${snap_row._get_active_charge()/100:.2f}.  "
            f"If $11.65, the cap-write-back path overwrote the saved "
            f"value because the engine recomputed customer_charged "
            f"with the wrong cap budget.")
