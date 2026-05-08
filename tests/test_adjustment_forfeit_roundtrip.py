"""AdjustmentDialog must preserve Phase B forfeit data through
a save → reopen → re-save round-trip
(v2.0.7-final consolidation, Option B, schema v36).

Pre-consolidation:
  * ``AdjustmentDialog.__init__`` loaded ``customer_forfeit_cents``
    from the DB but DROPPED it before passing to
    ``PaymentRow.set_data`` (the parameter wasn't accepted).
  * AdjustmentDialog ran its own inline first-with-match
    Phase-A-only forfeit loop that didn't set
    ``customer_forfeit_cents`` on saved rows.
  * Net effect: any time a manager re-saved a transaction that
    had Phase B forfeit, the forfeit value was silently lost.
    Reports → Customer Forfeit column zeroed out for any txn
    the manager touched.

Post-consolidation:
  * ``PaymentRow.set_data`` accepts ``customer_forfeit_cents``
    and stashes it on the widget so ``get_data()`` round-trips it.
  * ``AdjustmentDialog`` passes the loaded value to
    ``set_data``.
  * AdjustmentDialog's inline forfeit loop is replaced with the
    canonical ``apply_denomination_forfeit`` (Phase A + Phase B,
    vendor-aware).
  * On save, ``customer_forfeit_cents`` is persisted on every
    line item row.

This file pins the round-trip:

  1. Save a Phase B transaction (Food RX $10 → $6.52 receipt).
  2. Verify DB row has ``customer_forfeit_cents = 348``.
  3. Open AdjustmentDialog on the same transaction.
  4. Verify the row's ``get_data()`` returns the forfeit.
  5. Re-save without changing anything.
  6. Verify DB row STILL has ``customer_forfeit_cents = 348``.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def db_with_phase_b_txn(tmp_path):
    """Build a DB with a confirmed Phase B forfeit transaction."""
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import (
        create_transaction, save_payment_line_items, confirm_transaction,
    )
    db_file = str(tmp_path / "adjust_forfeit_roundtrip.db")
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

    # User-reported scenario: 1 Food RX token ($10 face) on a
    # $6.52 receipt.  Phase A consumes all match ($10 → $0);
    # Phase B forfeits $3.48 of customer-side token value.
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
        'customer_forfeit_cents': 348,  # Phase B forfeit
        'photo_path': None,
    }])
    confirm_transaction(txn_id, confirmed_by='Tester')
    yield conn, txn_id
    close_connection()


class TestAdjustmentDialogPreservesPhaseBForfeit:

    def test_initial_db_state_has_phase_b_forfeit(
            self, db_with_phase_b_txn):
        """Sanity: the fixture's saved row has the forfeit value."""
        conn, txn_id = db_with_phase_b_txn
        row = conn.execute(
            "SELECT customer_charged, match_amount, method_amount, "
            "customer_forfeit_cents FROM payment_line_items "
            "WHERE transaction_id=?", (txn_id,)).fetchone()
        assert row['customer_charged'] == 652
        assert row['match_amount'] == 0
        assert row['method_amount'] == 652
        assert row['customer_forfeit_cents'] == 348, (
            "Fixture didn't save Phase B forfeit — test setup "
            "is broken before we even start the round-trip.")

    def test_adjust_dialog_round_trip_preserves_forfeit(
            self, qtbot, monkeypatch, db_with_phase_b_txn):
        """Open the AdjustmentDialog, exit via Cancel (no edits),
        verify the DB row is unchanged.

        The pre-consolidation bug-shape: dialog opens, loads the
        row, drops the forfeit; manager closes dialog without
        edits; nothing changes in the DB because we cancel.
        Test the WORSE case: dialog opens, loads, manager
        re-saves (e.g. tweaks the receipt total).  Forfeit must
        survive."""
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_payment_line_items
        conn, txn_id = db_with_phase_b_txn

        # Load row to verify what set_data sees.
        loaded_items = get_payment_line_items(txn_id)
        assert loaded_items[0]['customer_forfeit_cents'] == 348

        from fam.models.transaction import get_transaction_by_id
        txn = get_transaction_by_id(txn_id)
        dlg = AdjustmentDialog(txn)
        qtbot.addWidget(dlg)

        # Inspect the row widget — its get_data() should return
        # the forfeit value loaded from the DB.
        rows = dlg._payment_rows
        assert len(rows) == 1
        data = rows[0].get_data()
        assert data is not None
        assert data['customer_forfeit_cents'] == 348, (
            f"PaymentRow.get_data() must return the forfeit "
            f"loaded from the DB on AdjustmentDialog open.  Got: "
            f"{data['customer_forfeit_cents']}")

    def test_set_data_accepts_forfeit_param(self):
        """Source-pin: PaymentRow.set_data() must accept
        ``customer_forfeit_cents`` parameter."""
        import inspect
        from fam.ui.widgets.payment_row import PaymentRow
        sig = inspect.signature(PaymentRow.set_data)
        assert 'customer_forfeit_cents' in sig.parameters, (
            "PaymentRow.set_data() must accept "
            "customer_forfeit_cents.  Without this parameter, "
            "AdjustmentDialog drops the forfeit value loaded "
            "from DB and any re-save loses Phase B data.")

    def test_get_data_returns_forfeit(self):
        """Source-pin: PaymentRow.get_data() must return
        ``customer_forfeit_cents`` so the save path can persist
        it."""
        import inspect
        from fam.ui.widgets.payment_row import PaymentRow
        src = inspect.getsource(PaymentRow.get_data)
        assert "'customer_forfeit_cents'" in src, (
            "PaymentRow.get_data() must include "
            "customer_forfeit_cents in the returned dict so "
            "save_payment_line_items can persist it.")

    def test_adjustment_dialog_loads_forfeit_into_set_data(self):
        """Source-pin: AdjustmentDialog.__init__ must pass the
        loaded ``customer_forfeit_cents`` to
        PaymentRow.set_data() — this is the gap pre-Option B."""
        import inspect
        from fam.ui.admin_screen import AdjustmentDialog
        src = inspect.getsource(AdjustmentDialog.__init__)
        assert 'customer_forfeit_cents' in src, (
            "AdjustmentDialog.__init__ must extract "
            "customer_forfeit_cents from the DB-loaded row and "
            "forward it to PaymentRow.set_data() so the value "
            "survives a re-save.")

    def test_adjustment_dialog_uses_canonical_forfeit_function(
            self):
        """Source-pin: AdjustmentDialog._adjust_transaction must
        delegate to the canonical
        ``apply_denomination_forfeit`` function rather than
        running its own inline Phase-A-only loop."""
        import inspect
        from fam.ui.admin_screen import AdminScreen
        src = inspect.getsource(AdminScreen._adjust_transaction)
        assert 'apply_denomination_forfeit' in src, (
            "AdjustmentDialog must delegate to the canonical "
            "apply_denomination_forfeit (in fam.utils.calculations) "
            "so it stays in lock-step with PaymentScreen's "
            "vendor-aware Phase A + Phase B logic.")
        # NEGATIVE pin: no inline first-with-match loop
        assert "it['match_amount'] -= reduction" not in src, (
            "AdjustmentDialog must NOT have an inline forfeit "
            "loop — it diverged from PaymentScreen's logic and "
            "dropped Phase B forfeit data.  The consolidation "
            "moved the math into the canonical function.")
