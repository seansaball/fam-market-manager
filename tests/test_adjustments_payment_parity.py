"""Tests pinning Adjustment-vs-Payment screen parity (v1.9.9).

Background — the post-audit fixes
---------------------------------
A logic audit comparing ``PaymentScreen._confirm_payment`` against
``AdminScreen._adjust_transaction`` found three guards that the
Payment screen had but Adjustments was missing.  Each had a real
financial- or data-integrity consequence; this module pins the
fixes so a future refactor can't silently regress them.

  1. **Layer 2A charge-integrity guard** — when the match cap is
     active and a manager hand-edits a spinbox after the impact
     panel updates, save can commit values that disagree with the
     engine.  PaymentScreen blocks the confirm with an "Auto-
     Distribute or correct" prompt; Adjustments now does the same.

  2. **Spinbox write-back in `_update_customer_impact`** — when the
     engine's capped ``customer_charged`` differs from the spinbox
     value, the spinbox must be updated so it stays in sync with
     the impact panel and so guard #1 never spuriously fires.  Also
     fixes a latent bug where ``set_display_values`` was being
     called with ``customer_charged`` instead of ``method_amount``,
     mis-labelling the Total column under match cap.

  3. **Photo + denomination validation on adjustment save** —
     PaymentScreen calls ``row.validate_denomination()`` and
     ``row.validate_photo()`` before any DB write; Adjustments
     skipped both, allowing missing mandatory photos and
     non-denomination amounts to slip through on edit.
"""

import inspect

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


# ══════════════════════════════════════════════════════════════════
# Shared fixture
# ══════════════════════════════════════════════════════════════════
@pytest.fixture
def adj_db(tmp_path):
    """One market with a low match cap (so cap inflation kicks in
    for any 100% match row above the cap), one vendor, three methods
    (SNAP non-denom 100%, Cash non-denom 0%, FMNP $5 denom 100% with
    Mandatory photo), plus a Confirmed transaction we can adjust."""
    db_file = str(tmp_path / "adj_parity.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'Test Mkt', 5000, 1)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " is_active, sort_order) VALUES "
        " (1, 'SNAP', 100.0, 1, 1), "
        " (2, 'Cash', 0.0,  1, 2)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " is_active, sort_order, denomination, photo_required) "
        "VALUES (3, 'FMNP', 100.0, 1, 3, 500, 'Mandatory')")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, payment_method_id) "
        "VALUES (1, 1), (1, 2), (1, 3)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-28', 'Open', 'Tester')")
    conn.execute(
        "INSERT INTO transactions (id, fam_transaction_id, "
        " market_day_id, vendor_id, receipt_total, status) VALUES "
        " (1, 'TX-1', 1, 1, 5000, 'Confirmed')")
    conn.execute(
        "INSERT INTO payment_line_items (transaction_id, "
        " payment_method_id, method_name_snapshot, "
        " match_percent_snapshot, method_amount, match_amount, "
        " customer_charged) VALUES "
        " (1, 1, 'SNAP', 100.0, 5000, 2500, 2500)")
    conn.commit()
    yield conn
    close_connection()


def _open_dialog(qtbot, txn_id=1):
    from fam.models.transaction import get_transaction_by_id
    from fam.ui.admin_screen import AdjustmentDialog
    txn = get_transaction_by_id(txn_id)
    dlg = AdjustmentDialog(txn)
    qtbot.addWidget(dlg)
    return dlg


# ══════════════════════════════════════════════════════════════════
# Fix #1 — charge-integrity guard
# ══════════════════════════════════════════════════════════════════
class TestChargeIntegrityGuard:
    """Mirror of ``PaymentScreen._confirm_payment`` lines 1985-2017.
    When the engine's capped ``customer_charged`` differs from the
    row's spinbox value, save MUST be blocked with an explicit
    error rather than silently committing the disagreeing value."""

    def _src(self):
        from fam.ui.admin_screen import AdminScreen
        return inspect.getsource(AdminScreen._adjust_transaction)

    def test_guard_compares_engine_charge_to_active_charge(self):
        src = self._src()
        # Pin the comparison shape — ``expected_charge`` (engine)
        # vs ``actual_charge`` (spinbox).  Drift here would mean
        # the guard is comparing the wrong fields.
        assert 'expected_charge' in src
        assert 'actual_charge' in src
        assert "new_items[i]['customer_charged']" in src, (
            "The guard MUST read customer_charged from the engine "
            "output (new_items / get_new_line_items result), NOT "
            "from the row directly — that defeats the purpose.")
        assert 'row._get_active_charge()' in src, (
            "The guard MUST read the row's spinbox value via "
            "_get_active_charge() so it sees what the manager "
            "actually typed.")

    def test_guard_blocks_save_with_message_box(self):
        """On mismatch the function must return a QMessageBox.warning
        and exit BEFORE the atomic save block.  Without the early
        return the guard is informational only."""
        src = self._src()
        # The mismatch branch closes with a `return` before the
        # atomic save.  Pin via the literal title token used.
        assert 'Payment Row Mismatch' in src
        # And the cancellation path returns rather than `pass`.
        guard_block = src[src.find('Payment Row Mismatch'):]
        # Look for the return statement within the same function
        # before hitting the next major branch (`if new_items:`).
        next_branch = guard_block.find('if new_items:')
        assert next_branch > 0, "Guard not followed by save flow"
        cancellation_window = guard_block[:next_branch]
        assert 'return' in cancellation_window, (
            "The mismatch branch must return before the save block.")

    def test_guard_logs_error_with_row_identity(self):
        """The error must be logged so post-mortem audits can find
        instances where the cap was producing surprising values
        and managers were tripping the guard."""
        src = self._src()
        assert 'logger.error' in src
        assert 'charge-integrity guard tripped' in src

    def test_guard_skips_rows_without_method_or_zero_amount(self):
        """``new_items`` is built from rows with method + non-zero
        ``method_amount``; iterating ALL rows and indexing into
        ``new_items`` would walk past the end on rows the engine
        skipped.  Pin the filter."""
        src = self._src()
        # The guard builds a parallel ``valid_rows`` list using the
        # same filter the dialog uses internally.  Quote the
        # filter expression so a refactor that uses a different
        # one (and goes out of sync with new_items) regresses.
        assert "method_amount'] > 0" in src


# ══════════════════════════════════════════════════════════════════
# Fix #2 — spinbox write-back in _update_customer_impact
# ══════════════════════════════════════════════════════════════════
class TestSpinboxWriteBack:
    """Mirror of ``PaymentScreen._update_summary`` lines 1384-1400.
    When the match cap inflates the customer's share past what they
    typed, the spinbox MUST be updated to reflect the engine output
    so the impact panel and the spinbox don't show different
    numbers (and so the Layer 2A guard never spuriously fires)."""

    def _src(self):
        from fam.ui.admin_screen import AdjustmentDialog
        return inspect.getsource(
            AdjustmentDialog._update_customer_impact)

    def test_set_display_values_uses_method_amount_not_customer_charged(
            self):
        """Pre-fix bug: the dialog passed ``customer_charged`` as
        the second arg to ``set_display_values``, mis-labelling the
        Total column.  ``set_display_values(match_amount, total)``
        — total = method_amount (vendor reimbursement)."""
        src = self._src()
        # Pin the corrected call shape.
        assert (
            "set_display_values(\n"
            "                capped_li['match_amount'], "
            "capped_li['method_amount']" in src
            or "set_display_values(capped_li['match_amount'], "
               "capped_li['method_amount']" in src
        ), (
            "_update_customer_impact must call set_display_values "
            "with (match_amount, method_amount), not "
            "(match_amount, customer_charged) — passing "
            "customer_charged shows the customer's share where the "
            "vendor's reimbursement total should be.")

    def test_writes_engine_charge_back_to_spinbox(self):
        src = self._src()
        # The conditional write-back: only update when the spinbox
        # disagrees with the engine, to avoid no-op signal
        # cascades.
        assert "if true_charge != row._get_active_charge():" in src
        assert "row._set_active_charge(true_charge)" in src

    def test_blocks_signals_during_write_back(self):
        """Without blockSignals, the write-back fires the row's
        ``changed`` signal which calls ``_on_payment_changed`` →
        ``_update_customer_impact`` → infinite re-entry."""
        src = self._src()
        write_back_block = src[src.find('true_charge'):]
        assert 'blockSignals(True)' in write_back_block
        assert 'blockSignals(False)' in write_back_block

    def test_recompute_called_after_write_back(self):
        """After updating ``_active_charge`` we must call
        ``_recompute()`` so the row's match/total labels reflect
        the new charge — same flow as the Payment screen."""
        src = self._src()
        assert 'row._recompute()' in src


# ══════════════════════════════════════════════════════════════════
# Fix #3 — denomination + photo validation
# ══════════════════════════════════════════════════════════════════
class TestSaveValidation:

    def _src(self):
        from fam.ui.admin_screen import AdminScreen
        return inspect.getsource(AdminScreen._adjust_transaction)

    def test_validates_denomination_before_save(self):
        """Each row's ``validate_denomination()`` must be called
        before the DB transaction so a non-denomination amount
        (e.g. typed $4 against a $5 denom) is caught with a
        friendly error instead of corrupting the line items."""
        src = self._src()
        assert 'validate_denomination' in src
        # And it must short-circuit the save.
        denom_block = src[src.find('validate_denomination'):]
        next_section = denom_block.find('validate_photo')
        assert next_section > 0
        assert 'return' in denom_block[:next_section]

    def test_validates_photo_before_save(self):
        """``row.validate_photo()`` must be called for every row
        before any DB write — mirrors PaymentScreen ``_confirm_payment``
        lines 1931-1937 verbatim."""
        src = self._src()
        assert 'validate_photo' in src
        photo_block = src[src.find('validate_photo'):]
        # The error path returns; no save proceeds.
        next_break = photo_block.find('new_items = dialog')
        assert next_break > 0
        assert 'return' in photo_block[:next_break]

    def test_photo_validation_runs_for_every_row(self):
        """Pin that the validation iterates every row (not just
        the first), since a manager could remove a photo on row 2
        while keeping row 1's photo intact."""
        src = self._src()
        # Should see a `for row in dialog._payment_rows:` loop near
        # the validate_photo call.
        photo_pos = src.find('validate_photo')
        window = src[max(0, photo_pos - 200):photo_pos]
        assert 'for row in dialog._payment_rows' in window


# ══════════════════════════════════════════════════════════════════
# Behaviour test — the parity scenario end-to-end
# ══════════════════════════════════════════════════════════════════
class TestSpinboxStaysInSyncUnderMatchCap:
    """End-to-end: open AdjustmentDialog against a transaction that
    exercises the match cap.  Type a charge that the engine will
    inflate, then trigger ``_update_customer_impact``.  After it
    runs the spinbox must show the engine's capped value, not the
    manager-typed value."""

    def test_match_cap_inflation_writes_back_to_spinbox(
            self, qtbot, adj_db):
        # Fixture sets daily_match_limit=$50.  The stock SNAP
        # transaction has $25 charge / $25 match — exactly at the
        # cap.  Bumping the receipt to $80 with a single SNAP row
        # should require $40 from customer (so $40 match exists,
        # under the $50 cap).  Set up a tighter scenario: modify
        # the transaction to receipt $200 with SNAP charge $50,
        # method_amount $100, match $50.  Cap is $50 so that's
        # exactly at the limit.  Now bump receipt to $300 — the
        # impact panel will compute customer_charged inflated past
        # $50 because the match can't grow.
        conn = get_connection()
        # Reset the seed transaction to a clean state we control.
        conn.execute(
            "UPDATE transactions SET receipt_total = 30000 "
            "WHERE id = 1")
        conn.execute(
            "UPDATE payment_line_items SET method_amount=10000, "
            "match_amount=5000, customer_charged=5000 "
            "WHERE transaction_id=1")
        conn.commit()

        dlg = _open_dialog(qtbot)
        # Forced cap: change the receipt to $300 from the dialog.
        # The single SNAP row currently has charge=$50, but with
        # receipt=$300 and 100% match the un-capped allocation
        # would need charge=$150 / match=$150.  Cap=$50 forces
        # match to $50 → customer_charged=$250.  The spinbox
        # currently shows $50 (the typed value).
        # Push the receipt by hand and trigger the recompute.
        dlg.receipt_spin.setValue(300.00)
        # _on_receipt_total_changed fires _update_row_caps +
        # _update_customer_impact which is what we want to test.

        snap_row = dlg._payment_rows[0]
        # The cap-aware logic should now have set the spinbox to
        # the engine's customer_charged value.  Without the write-
        # back fix the spinbox would still show $50 (or whatever
        # the typed value was before the receipt change).
        # NB: the actual numeric value depends on the cap math —
        # what we're really testing is that the spinbox is NOT
        # below the engine's expectation.  The Layer 2A guard's
        # criterion is exact equality, so verify that.
        engine_items = dlg.get_new_line_items()
        assert engine_items, "Expected at least one line item"
        engine_charge = engine_items[0]['customer_charged']
        spinbox_charge = snap_row._get_active_charge()
        assert spinbox_charge == engine_charge, (
            f"Spinbox ({spinbox_charge}) and engine "
            f"({engine_charge}) disagree after receipt change.  "
            f"The Layer 2 guard would block save in this state.  "
            f"_update_customer_impact must write the engine's "
            f"customer_charged back to the spinbox so the two "
            f"stay in sync.")
