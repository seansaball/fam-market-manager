"""Tests for the AdjustmentDialog smart per-row cap (v1.9.9).

Background — the screenshot bug
-------------------------------
A coordinator opened an adjustment, edited the receipt down from a
larger value to $11.11, and the JH Food Bucks ($2 denom, 100% match)
stepper happily kept its 19-unit value (= $38 charge / $76 method
amount).  The "Payment total ($76.00) does not match receipt total
($11.11)" reconciliation banner caught it at save time, but the smart
input cap that the Payment screen has had since v1.9.1 was never
applied here — the AdjustmentDialog only capped each row at the FULL
receipt total (so two rows could each consume the receipt independently)
and only re-ran the cap on receipt-change / auto-distribute, not on
every value edit.

This module pins:
  1. ``AdjustmentDialog._update_row_caps`` mirrors the Payment screen's
     ``_push_row_limits`` — per-row remaining = receipt − OTHER rows,
     not the full receipt.
  2. The cap recomputes on every payment change (method pick OR value
     edit), so reopening a transaction with already-bloated values
     clamps them down on the first signal hop.
  3. Denomination forfeit allowance: +1 unit when remaining can't be
     filled by exact denominations — the customer's physical check
     must still be enterable.
  4. Match-limit-aware inflation for non-denominated rows when the
     daily match cap is partially consumed.
"""

import inspect

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


# ══════════════════════════════════════════════════════════════════
# Fixture: DB matching the screenshot scenario
# ══════════════════════════════════════════════════════════════════
@pytest.fixture
def adj_db(tmp_path):
    """One market with daily match cap, one vendor, three methods:

    - SNAP (id=1, 100% match, non-denom)
    - Cash (id=2, 0% match,   non-denom)
    - JH Food Bucks (id=3, 100% match, $2 denom)

    Plus a $50.00 Confirmed transaction the test can adjust.
    """
    db_file = str(tmp_path / "adj_smart_cap.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'Test Mkt', 10000, 1)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " is_active, sort_order) VALUES "
        " (1, 'SNAP', 100.0, 1, 1), "
        " (2, 'Cash', 0.0,  1, 2)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " is_active, sort_order, denomination) VALUES "
        " (3, 'JH Food Bucks', 100.0, 1, 3, 200)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, payment_method_id) "
        "VALUES (1, 1), (1, 2), (1, 3)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-28', 'Open', 'Tester')")
    # Confirmed transaction — receipt $50 with one SNAP $50 line item.
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


def _select_method(row, partial_name):
    combo = row.method_combo
    for i in range(combo.count()):
        if partial_name.lower() in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            return
    raise AssertionError(f"Method matching {partial_name!r} not in combo")


def _open_dialog(qtbot, txn_id=1):
    from fam.models.transaction import get_transaction_by_id
    from fam.ui.admin_screen import AdjustmentDialog
    txn = get_transaction_by_id(txn_id)
    dlg = AdjustmentDialog(txn)
    qtbot.addWidget(dlg)
    return dlg


# ══════════════════════════════════════════════════════════════════
# 1. Source-level guards (cheap, no Qt instantiation)
# ══════════════════════════════════════════════════════════════════
class TestUpdateRowCapsSourceContract:

    def _src(self):
        from fam.ui.admin_screen import AdjustmentDialog
        return inspect.getsource(AdjustmentDialog._update_row_caps)

    def test_caps_subtract_other_rows_from_receipt(self):
        """Per-row remaining must subtract OTHER rows' allocations,
        not just clamp at the full receipt total — otherwise two
        rows can each consume the entire receipt independently."""
        src = self._src()
        # The marker variable from the new logic.
        assert 'other_total' in src, (
            "_update_row_caps must accumulate other_total (sum of "
            "method_amount from OTHER rows).  Without this, the cap "
            "is just the receipt total and double-allocation is "
            "still possible.")
        assert 'receipt_cents - other_total' in src

    def test_uses_charge_to_method_amount(self):
        """The cross-row sum is in method_amount space, so each
        other row's charge must be converted via the match-aware
        helper (NOT just summed as charges)."""
        src = self._src()
        assert 'charge_to_method_amount' in src

    def test_match_limit_branch_excludes_denominated(self):
        """Match-limit inflation only applies to non-denominated
        rows — inflating denom max would let a volunteer enter way
        more physical checks than the receipt can absorb."""
        src = self._src()
        assert 'not is_denominated' in src

    def test_denomination_forfeit_allows_one_extra_unit(self):
        """Pin the +1 forfeit unit logic so a future refactor that
        drops it can't silently regress.  Without forfeit, a $5
        physical check can't be entered against an $8 remaining
        balance with $5 denom — the cap rounds down to 1 unit."""
        src = self._src()
        assert '(normal_units + 1)' in src
        assert 'remaining - normal_alloc > 1' in src, (
            "The forfeit allowance must check that there's a real "
            "gap (more than a penny) before allowing the +1 unit; "
            "otherwise rounding noise could spuriously add a unit.")

    def test_blocks_signals_around_cap_writes(self):
        """``setMaximum`` clamps current values, which fires
        valueChanged → _on_payment_changed → _update_row_caps in a
        loop.  Source-pin the blockSignals dance."""
        src = self._src()
        assert 'blockSignals(True)' in src
        assert 'blockSignals(False)' in src


class TestPaymentChangedRecomputesCaps:
    """The cap MUST recompute on every value/method edit, not just
    on receipt change + auto-distribute (the pre-v1.9.9 wiring).
    Without this hook, reopening a transaction whose stepper was
    already past-cap leaves the bad value in place."""

    def test_on_payment_changed_calls_update_row_caps(self):
        from fam.ui.admin_screen import AdjustmentDialog
        src = inspect.getsource(AdjustmentDialog._on_payment_changed)
        assert 'self._update_row_caps()' in src, (
            "_on_payment_changed must call _update_row_caps so caps "
            "tighten/loosen as the manager edits any row.  Pin this "
            "in source so a refactor can't drop it.")


# ══════════════════════════════════════════════════════════════════
# 2. Behaviour — actually instantiate the dialog and watch caps
# ══════════════════════════════════════════════════════════════════
class TestNonDenominatedSingleRowCap:
    """Single SNAP row, $50 receipt, 100% match.  max_charge_nominal
    = 5000 / (1 + 1.0) = $25.  The spinbox max should reflect that."""

    def test_single_snap_row_caps_at_half_receipt(self, qtbot, adj_db):
        dlg = _open_dialog(qtbot)
        # Existing SNAP row was loaded from DB.
        row = dlg._payment_rows[0]
        # Cap applied at end of __init__; verify the spinbox caps.
        # SNAP at 100% match against $50 → max charge $25.
        assert row.amount_spin.maximum() == pytest.approx(25.0), (
            f"Single SNAP row on $50 receipt with 100% match must cap "
            f"at $25.00 (receipt / 2).  Got "
            f"{row.amount_spin.maximum()}.")


class TestMultiRowCrossInfluence:
    """Two rows must each be capped against the OTHER row's
    allocation, not the full receipt — pin that behavior here."""

    def test_second_row_cap_reflects_first_row_allocation(
            self, qtbot, adj_db):
        dlg = _open_dialog(qtbot)
        # Row 0 is SNAP $50 method_amount (so $25 charge after match).
        # Add a Cash row.
        dlg._add_payment_row()
        cash_row = dlg._payment_rows[1]
        _select_method(cash_row, 'Cash')

        # Receipt $50, SNAP method_amount $50 → receipt - other = $0.
        # Cash row should be capped at $0 (Cash has 0% match, so
        # $0 method_amount = $0 charge).
        assert cash_row.amount_spin.maximum() == pytest.approx(0.0), (
            "When SNAP already consumes the full $50 receipt at "
            "$50 method_amount, Cash row's max charge must be $0 — "
            "got "
            f"{cash_row.amount_spin.maximum()}")

    def test_decreasing_one_row_loosens_the_other(self, qtbot, adj_db):
        """Drop SNAP charge → Cash cap should rise correspondingly."""
        dlg = _open_dialog(qtbot)
        snap_row = dlg._payment_rows[0]
        dlg._add_payment_row()
        cash_row = dlg._payment_rows[1]
        _select_method(cash_row, 'Cash')

        # Reduce SNAP charge from $25 → $10 ($20 method_amount).
        # Other_total for Cash row drops from $50 → $20, leaving
        # $30 receipt headroom for Cash (0% match → $30 charge max).
        snap_row._set_active_charge(1000)  # $10 cents
        # Trigger the change signal manually since blockSignals
        # would suppress.  In the dialog flow this happens via the
        # spinbox's own valueChanged.
        snap_row.changed.emit()

        assert cash_row.amount_spin.maximum() == pytest.approx(30.0), (
            f"After SNAP drops to $10 charge ($20 method_amount), "
            f"Cash row's max should rise to $30.  Got "
            f"{cash_row.amount_spin.maximum()}")


class TestDenominationForfeitAllowance:
    """The screenshot bug: $11.11 receipt, JH Food Bucks $2 denom
    100% match.  Without forfeit allowance the cap would round to
    floor($5.55 / $2) = 2 units ($4 charge / $8 method_amount).
    The customer is handing over real checks though — if they bring
    3 × $2 ($6 charge / $12 method_amount) we MUST accept it; FAM
    match flexes down on save."""

    def test_screenshot_scenario_caps_at_three_units_not_nineteen(
            self, qtbot, adj_db):
        # Recreate the screenshot's $11.11 receipt + Food Bucks row.
        # Adjust the existing transaction's receipt_total to $11.11,
        # then open the dialog.  We use a fresh transaction to avoid
        # legacy-loaded items.
        conn = get_connection()
        conn.execute(
            "INSERT INTO transactions (id, fam_transaction_id, "
            " market_day_id, vendor_id, receipt_total, status) VALUES"
            " (2, 'TX-screenshot', 1, 1, 1111, 'Confirmed')")
        conn.commit()
        dlg = _open_dialog(qtbot, txn_id=2)
        # No existing payment rows on the new txn → there should be
        # exactly one empty row.
        assert len(dlg._payment_rows) == 1
        row = dlg._payment_rows[0]
        _select_method(row, 'Food Bucks')

        # Receipt $11.11, 100% match → max method_amount $11.11
        # max_charge_nominal = floor(1111/2) = 555 cents
        # normal_units = floor(555/200) = 2 → normal_alloc =
        #   charge_to_method_amount(2*200, 100) = 800 cents
        # remaining - normal_alloc = 1111 - 800 = 311 cents > 1
        # → forfeit allowance: max_charge = (2+1)*200 = 600 cents
        # → max stepper count = floor(600/200) = 3
        stepper = row._stepper
        assert stepper._count_spin.maximum() == 3, (
            f"Food Bucks ($2 denom, 100% match) on $11.11 receipt "
            f"must cap stepper at 3 units (2 normal + 1 forfeit).  "
            f"Got max={stepper._count_spin.maximum()}.  This is the "
            f"screenshot scenario where 19 units snuck in.")

    def test_no_forfeit_when_receipt_divides_evenly(
            self, qtbot, adj_db):
        """$10 receipt / $2 denom / 100% match: max_charge_nominal
        = 500, normal_units = floor(500/200) = 2 (charge $4, alloc
        $8), remaining $2.  remaining - normal_alloc = 200, that's
        > 1 → forfeit allows 3 units.  Wait — so forfeit DOES kick
        in here too because $2 > 0 in method_amount space.

        The clean case where forfeit should NOT fire is when the
        full cap fits exactly: $8 receipt, $2 denom, 100% match →
        max_charge_nominal = 400, normal_units = 2 → exact fit, no
        forfeit unit needed."""
        conn = get_connection()
        conn.execute(
            "INSERT INTO transactions (id, fam_transaction_id, "
            " market_day_id, vendor_id, receipt_total, status) VALUES"
            " (3, 'TX-exact', 1, 1, 800, 'Confirmed')")
        conn.commit()
        dlg = _open_dialog(qtbot, txn_id=3)
        row = dlg._payment_rows[0]
        _select_method(row, 'Food Bucks')

        # $8 receipt, 100% match → max method_amount $8
        # max_charge_nominal = floor(800/2) = 400 cents
        # normal_units = floor(400/200) = 2 → normal_alloc = 800 cents
        # remaining - normal_alloc = 0 → no forfeit unit
        # → max stepper count = 2
        stepper = row._stepper
        assert stepper._count_spin.maximum() == 2, (
            "When the receipt divides evenly, no forfeit unit is "
            "needed — max should be exact.  Got "
            f"{stepper._count_spin.maximum()} (expected 2).")


# ══════════════════════════════════════════════════════════════════
# 3. Save-flow denomination forfeit acceptance (the screenshot bug)
# ══════════════════════════════════════════════════════════════════
class TestSaveFlowDenominationForfeit:
    """Source-level guards on ``_adjust_transaction``'s forfeit
    handling.  Behaviour-testing the full save path requires a real
    AdminScreen + an open AdjustmentDialog + a confirmation popup,
    which is heavy.  Pin the contract at the source level so a
    refactor can't silently regress to the v1.9.9-pre 'hard block on
    any overage' behaviour the screenshot caught."""

    def _src(self):
        from fam.ui.admin_screen import AdminScreen
        return inspect.getsource(AdminScreen._adjust_transaction)

    def test_negative_gap_triggers_forfeit_branch(self):
        """``gap < -1`` (over-allocation) must enter the forfeit
        check, not fall straight through to the Payment Mismatch
        hard error."""
        src = self._src()
        assert 'if gap < -1:' in src, (
            "_adjust_transaction must branch on gap < -1 to detect "
            "denomination overage.  Without this branch, the "
            "screenshot scenario (6 × $2 against $21 receipt) is "
            "blocked at save with a 'Payment Mismatch' error.")

    def test_effective_denom_sum_bounds_acceptance(self):
        """Forfeit is ONLY accepted when the overage fits within one
        unit of effective denomination.  An overage of $25 with a $5
        denom method (100% match → $10 effective) must NOT be
        silently absorbed — that'd hide a real over-allocation bug."""
        src = self._src()
        assert 'effective_denom_sum' in src
        assert 'overage <= effective_denom_sum' in src, (
            "Forfeit acceptance must be gated by the overage being "
            "no larger than one unit of effective denomination — "
            "otherwise a manager could absorb arbitrarily large "
            "over-allocations as 'forfeit'.")

    def test_forfeit_reduces_match_not_customer_charged(self):
        """The customer paid in real physical instruments — Phase A
        forfeit must come out of FAM match, never out of what the
        customer paid.  Phase B (token-value forfeit) intentionally
        DOES reduce customer_charged but only when match is fully
        consumed first (see Phase A vs Phase B distinction in
        FINANCIAL_FORMULA.md §3b.1).

        v2.0.7-final consolidation (Option B): the inline first-
        with-match Phase-A-only loop in AdjustmentDialog has been
        replaced with a delegate to the canonical
        ``apply_denomination_forfeit`` function.  This pin source-
        checks the canonical function instead.  The canonical
        function reduces match BEFORE customer (Phase A then
        Phase B) so the customer is never under-credited unless
        FAM match is already exhausted."""
        from fam.utils.calculations import apply_denomination_forfeit
        canonical_src = inspect.getsource(apply_denomination_forfeit)
        # Phase A: match reduction comes first.
        assert "li['match_amount'] -= match_red" in canonical_src
        assert "li['method_amount'] -= match_red" in canonical_src
        # Phase B: customer reduction only after match is exhausted
        # (gated on `v_remain > 0 and li['customer_charged'] > 0`).
        assert "if v_remain > 0 and li['customer_charged'] > 0" in canonical_src
        assert "li['customer_charged'] -= cust_red" in canonical_src
        # AdjustmentDialog now delegates — no inline match
        # reduction loop in _adjust_transaction.
        src = self._src()
        assert "it['match_amount'] -= reduction" not in src, (
            "AdjustmentDialog must NOT have an inline forfeit "
            "loop after the v2.0.7-final consolidation — it "
            "delegates to the canonical apply_denomination_forfeit "
            "function in fam.utils.calculations.")
        assert 'apply_denomination_forfeit' in src, (
            "AdjustmentDialog must delegate to the canonical "
            "apply_denomination_forfeit function so its forfeit "
            "logic stays in lock-step with PaymentScreen's.")

    def test_user_cancels_forfeit_returns_without_save(self):
        """Pin the cancellation path: when the manager dismisses the
        forfeit confirmation (or clicks Cancel when there's no
        customer-pay delta), the function returns BEFORE the atomic
        save block.  Otherwise a no-confirm would still commit the
        un-modified line items.

        Updated for the v1.9.9 enhancement where the popup grew
        addButton-based options to surface the customer-pay delta
        when applicable — the simple ``QMessageBox.question`` call
        was replaced with a custom ``QMessageBox`` instance, but the
        cancellation contract (return BEFORE save) must still hold."""
        src = self._src()
        forfeit_block_idx = src.find('Denomination Overage')
        assert forfeit_block_idx > 0, "Forfeit prompt missing"
        # Walk forward to the next major section header.
        cancellation_window = src[forfeit_block_idx:forfeit_block_idx + 4000]
        # The cancel branch returns on either a None click
        # (X-dismissed) or the explicit Cancel button when there's
        # no customer-pay delta to act on.
        assert 'clicked is None' in cancellation_window
        assert 'return' in cancellation_window

    def test_post_save_message_distinguishes_forfeit_from_unallocated(
            self):
        """The post-save dialog for the denomination-overage path
        must be a distinct branch — not 'Customer Impact' (which
        would tell the manager to collect more) and not the
        'Unallocated Funds Logged' branch (orthogonal customer-
        gone path).

        v2.0.7 (user-reported 2026-05-07): the dialog title is
        'Adjustment Saved' (NOT 'Denomination Forfeit Saved') —
        Phase A FAM-match reduction is no longer labeled as a
        'forfeit' anywhere in the user-facing surface.  'Customer
        Forfeit' terminology is reserved for Phase B token-value
        loss (which only fires from the PaymentScreen flow,
        never from Adjustment).
        """
        src = self._src()
        # The branch fires only for the denom_overage_cents > 0
        # case (distinguishes it from generic customer-impact).
        assert 'denom_overage_cents > 0' in src
        # Body wording must mention the FAM match reduction
        # mechanism.  No longer uses 'Forfeit' in the title.
        assert 'FAM match reduced by' in src


class TestInlineImpactPanelOverageWarning:
    """The ``_update_customer_impact`` panel must distinguish a
    denomination overage (yellow warning, save will absorb) from a
    real over-allocation (red error, blocks save).  Without this
    distinction the manager sees a red 'Payment Mismatch' the
    moment the cap-permitted +1 unit is entered, which is the same
    confusing UX the screenshot called out."""

    def test_update_customer_impact_branches_on_denom_overage(self):
        from fam.ui.admin_screen import AdjustmentDialog
        src = inspect.getsource(
            AdjustmentDialog._update_customer_impact)
        assert 'effective_denom_sum' in src, (
            "_update_customer_impact must compute effective_denom_sum "
            "to distinguish denomination overage from real "
            "over-allocation.")
        assert 'Denomination overage' in src

    def test_overage_warning_uses_warning_styling(self):
        """The yellow/warning styling distinguishes the forfeit
        notice from the red 'Payment Mismatch' hard error."""
        from fam.ui.admin_screen import AdjustmentDialog
        src = inspect.getsource(
            AdjustmentDialog._update_customer_impact)
        # WARNING_BG/WARNING_COLOR are imported from styles for the
        # yellow appearance — pin them so the styling can't get
        # silently swapped for the error red.
        assert 'WARNING_BG' in src
        assert 'WARNING_COLOR' in src
