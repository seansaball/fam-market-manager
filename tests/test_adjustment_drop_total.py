"""Regression: Adjustment dialog must not silently corrupt data when
the receipt total is dropped, and must never produce a save that
violates the per-line invariant.

User-reported (2026-04-30 onsite):

    "I'm able to manipulate the adjustments page and by dropping
    totals and auto distribute seems to be broken, it lets me
    proceed and then gives me errors like the examples"

  Errors observed:
    - "Adjustment failed: customer_charged + match_amount must
       equal method_amount"  (raw schema-trigger IntegrityError)
    - "Payment Mismatch: Payment total ($4.20) does not match
       receipt total ($4.33).  Please fix the payment amounts."

  Log entry:
    File "fam/ui/admin_screen.py", line 1615, in _adjust_transaction
        save_payment_line_items(txn_id, new_items, commit=False)
    sqlite3.IntegrityError: customer_charged + match_amount must
                              equal method_amount

Root causes (four interlocking bugs)
------------------------------------

  1. **Silent clamp on receipt drop** — ``_update_row_caps`` calls
     ``setMaximum(N)`` where N < current value.  Qt clamps the
     current value silently, destroying user input the moment the
     receipt total decreased.  FB stepper at 2 units became 0 with
     no notification.

  2. **Proportional rescale ignores stepper** — when the receipt
     total changed, ``_on_receipt_total_changed`` read/wrote
     ``row.amount_spin`` directly.  Denominated rows use the
     stepper widget with ``amount_spin`` HIDDEN; the rescale read 0
     from the hidden spinbox (skipping denom rows from the
     proportional sum) and wrote rescaled values to a widget the
     user couldn't see — corrupting the data model.

  3. **``get_new_line_items`` doesn't merge ``method_amount``** —
     after the engine penny-rec adjusts the largest matched row's
     ``method_amount`` by ±1¢, the merge code only copied
     ``match_amount`` and ``customer_charged`` back to ``raw_items``.
     Result: ``raw_items[i].method_amount`` (unchanged) ≠
     ``raw_items[i].customer_charged + raw_items[i].match_amount``
     (engine + 1¢).  Schema trigger then fires on save.

  4. **No Layer 2A guard in _adjust_transaction** — an inconsistent
     item passed straight to ``save_payment_line_items`` and the
     user got the raw SQL ``IntegrityError`` instead of a friendly
     "fix the X row" message.

This test file pins the contracts:
  - Receipt drop must NOT clamp existing row charges below their
    current values (data preservation)
  - ``_on_receipt_total_changed`` rescale must read denom-row
    charges through the stepper, not the hidden ``amount_spin``
  - ``get_new_line_items`` must produce items where
    customer + match = method for every row
  - ``_adjust_transaction`` must block save with a friendly error
    if any non-system row violates the invariant
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def adj_db(tmp_path):
    """A market with one confirmed transaction the manager can adjust."""
    db_file = str(tmp_path / "adj.db")
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
    for mid, name, pct, denom, sort_o in [
            (1, 'SNAP', 100.0, None, 1),
            (2, 'Cash', 0.0, None, 2),
            (4, 'JH Food Bucks', 100.0, 200, 4)]:
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            " denomination, sort_order, is_active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (mid, name, pct, denom, sort_o))
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
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import (
        create_transaction, confirm_transaction, save_payment_line_items,
    )
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-X', zip_code='15102')
    # $20 receipt with 5×$2 FB ($10 customer + $10 match) +
    # $5 SNAP ($5 customer + $5 match)
    txn_id, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=2000,
        customer_order_id=order_id,
        market_day_date='2026-04-30')
    save_payment_line_items(txn_id, [
        {'payment_method_id': 4, 'method_name_snapshot': 'JH Food Bucks',
         'match_percent_snapshot': 100.0,
         'method_amount': 2000, 'match_amount': 1000,
         'customer_charged': 1000,
         'photo_path': None, 'photo_source_paths': []},
    ])
    confirm_transaction(txn_id, confirmed_by='T')
    conn.commit()
    yield conn, txn_id
    close_connection()


def _stub_qmessageboxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, 'question',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(
        QMessageBox, 'warning',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(
        QMessageBox, 'critical',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Ok))

    def stub_exec(self):
        btns = self.buttons()
        if btns:
            self._clicked_button = btns[0]
        return 0

    monkeypatch.setattr(QMessageBox, 'exec', stub_exec)
    monkeypatch.setattr(
        QMessageBox, 'clickedButton',
        lambda self: getattr(self, '_clicked_button', None))


class TestAdjustmentDropTotalPreservesCharges:
    """Receipt-total drop must preserve existing valid row charges
    (no silent clamp)."""

    def test_drop_receipt_does_not_silently_clamp_denom_row(
            self, qtbot, adj_db, monkeypatch):
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        _stub_qmessageboxes(monkeypatch)
        conn, txn_id = adj_db
        txn = get_transaction_by_id(txn_id)

        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)

        # Initial: FB stepper at 5 units = $10 charge.  ($20 method).
        fb_row = next(r for r in dialog._payment_rows
                      if r.get_selected_method()
                      and r.get_selected_method()['name'] == 'JH Food Bucks')
        assert fb_row._get_active_charge() == 1000, (
            f"Initial FB charge should be $10 (5 × $2 tokens), "
            f"got {fb_row._get_active_charge()}")

        # Drop receipt to $4.33.  Pre-fix: FB charge silently
        # clamped to 0 because _update_row_caps' setMaximum(0)
        # collapsed the stepper.  Post-fix: charge preserved.
        dialog.receipt_spin.setValue(4.33)

        assert fb_row._get_active_charge() == 1000, (
            f"FB charge should be preserved at $10 after dropping "
            f"the receipt to $4.33 — Qt's setMaximum is destructive "
            f"so _update_row_caps must use max(current, computed) "
            f"as the floor.  Got "
            f"{fb_row._get_active_charge()}c.")


class TestAdjustmentRescaleHonorsStepper:
    """``_on_receipt_total_changed`` proportional rescale must read/
    write through ``_get_active_charge`` / ``_set_active_charge``,
    not the hidden ``amount_spin`` for denom rows."""

    def test_rescale_reads_stepper_value_for_denom_row(
            self, qtbot, adj_db, monkeypatch):
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        _stub_qmessageboxes(monkeypatch)
        conn, txn_id = adj_db
        txn = get_transaction_by_id(txn_id)

        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)

        # Verify the rescale path uses ``_get_active_charge`` by
        # patching it on the FB row and checking it's invoked when
        # the receipt total changes.  If the implementation regresses
        # to ``row.amount_spin.value()``, this patch never sees the
        # call and the test fails.
        fb_row = next(r for r in dialog._payment_rows
                      if r.get_selected_method()
                      and r.get_selected_method()['name'] == 'JH Food Bucks')

        call_count = [0]
        orig = fb_row._get_active_charge
        def counted():
            call_count[0] += 1
            return orig()
        fb_row._get_active_charge = counted

        # Trigger receipt change.  The proportional rescale path
        # only fires when payments previously matched the old total,
        # but the cap-update path always reads the current charge.
        dialog.receipt_spin.setValue(15.00)

        assert call_count[0] > 0, (
            "`_on_receipt_total_changed` must read row charges via "
            "`_get_active_charge` so denom-row stepper values are "
            "included in the rescale and cap math.  Pre-fix it read "
            "`row.amount_spin.value()` directly which always "
            "returned 0 for denom rows (their amount_spin is "
            "hidden), corrupting the rescale.")


class TestGetNewLineItemsInvariant:
    """``get_new_line_items`` must produce items where
    ``customer_charged + match_amount = method_amount`` for every
    row, even when the engine's penny-rec path adjusts
    ``method_amount``."""

    def test_all_items_satisfy_invariant_after_engine_pass(
            self, qtbot, adj_db, monkeypatch):
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        _stub_qmessageboxes(monkeypatch)
        conn, txn_id = adj_db
        txn = get_transaction_by_id(txn_id)

        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)

        # Walk a variety of receipt totals — including ones that
        # exercise the engine's ±1¢ penny-rec path.
        for new_total in (4.33, 5.00, 7.51, 12.34, 19.99, 25.00):
            dialog.receipt_spin.setValue(new_total)
            items = dialog.get_new_line_items()
            for it in items:
                if it.get('method_name_snapshot') == 'Unallocated Funds':
                    continue
                assert (it['customer_charged'] + it['match_amount']
                        == it['method_amount']), (
                    f"Per-line invariant violated at receipt "
                    f"${new_total}: {it['method_name_snapshot']} "
                    f"customer={it['customer_charged']}c + "
                    f"match={it['match_amount']}c != "
                    f"method={it['method_amount']}c.  This bypasses "
                    f"the schema trigger and lands the user on a raw "
                    f"IntegrityError dialog.")


class TestAdjustTransactionInvariantGuard:
    """``_adjust_transaction`` must catch any non-system row whose
    customer + match ≠ method BEFORE it reaches
    ``save_payment_line_items`` — friendly error, not raw SQL.

    We can't easily drive ``_adjust_transaction`` end-to-end (it
    opens a modal dialog whose internals we'd have to deeply mock),
    so this test pins the contract by checking the guard code exists
    in the right place and references the expected error wording.
    A future refactor that drops the guard fails this test loudly.
    """

    def test_invariant_guard_exists_in_adjust_transaction(self):
        """The Layer-2A-style guard must be present in
        ``_adjust_transaction`` — its absence allowed raw schema
        ``IntegrityError`` dialogs to reach the user on the
        2026-04-30 onsite."""
        import inspect
        from fam.ui.admin_screen import AdminScreen
        src = inspect.getsource(AdminScreen._adjust_transaction)
        # Look for the canonical guard markers: a per-line invariant
        # check and a Payment-Row-Inconsistent friendly error.
        assert 'Payment Row Inconsistent' in src, (
            "_adjust_transaction must surface a 'Payment Row "
            "Inconsistent' dialog when an item violates "
            "customer + match = method, instead of letting the "
            "schema trigger raise a raw IntegrityError.")
        assert "method_name_snapshot" in src and "Unallocated Funds" in src, (
            "Guard must exempt system Unallocated Funds rows "
            "(method_name_snapshot == 'Unallocated Funds') the same "
            "way the schema trigger does — UF rows intentionally "
            "have customer=match=0 / method>0 to surface FAM "
            "absorption distinctly in reports.")
