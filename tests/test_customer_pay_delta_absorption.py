"""Tests for the customer-pay-delta gate on AdjustmentDialog.

Background — the second screenshot bug
--------------------------------------
The first iteration of the customer-gone popup only fired when there
was a *receipt-vs-allocation gap* (the manager raised the receipt or
shrank a payment without rebuilding the breakdown).  Two real-world
scenarios slipped through:

  Scenario A — balanced allocation, breakdown change
    Receipt $20.  Original: customer paid $5.55.  Manager rebuilds
    the breakdown so customer now pays $10.  Allocation balances
    receipt (no gap), so the existing popup didn't fire — but the
    customer's required payment went UP $4.45 and the manager got
    no chance to mark it as Unallocated Funds.

  Scenario B — denom overage popup didn't mention customer collection
    Receipt $21, 6 × $2 Food Bucks at 100% match → $12 customer pay
    + $12 match = $24 method = $3 over receipt.  Existing popup
    only said "FAM forfeits $3 of match" — never mentioned that
    the customer ALSO needs to hand over $X more in physical
    Food Bucks (vs the original recorded payment).

Both fixed in the same patch by:
  * Computing ``customer_pay_delta = new_customer_paid -
    old_customer_paid`` once, used by every popup branch.
  * Beefing the denom overage popup to include the customer-pay
    delta and offer the same Yes/No customer-gone option.
  * Adding a dedicated balanced-allocation popup for Scenario A.
  * A shared ``_absorb_customer_pay_delta`` helper that reduces
    customer_charged + match_amount + method_amount on existing
    rows and returns the corresponding Unallocated Funds amount —
    so the saved customer_charged equals what was truly collected
    and the loss surfaces as a separate ledger column.
"""

import inspect

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


# ══════════════════════════════════════════════════════════════════
# 1. Pure math: _absorb_customer_pay_delta helper
# ══════════════════════════════════════════════════════════════════
class TestAbsorptionMath:
    """The helper is a pure function over a list of line item dicts.
    No DB or Qt — just arithmetic."""

    def test_returns_zero_for_zero_delta(self):
        from fam.ui.admin_screen import _absorb_customer_pay_delta
        items = [{'customer_charged': 1000, 'match_amount': 1000,
                  'method_amount': 2000, 'match_percent_snapshot': 100.0}]
        assert _absorb_customer_pay_delta(items, 0) == 0
        # No mutation either.
        assert items[0]['customer_charged'] == 1000
        assert items[0]['match_amount'] == 1000

    def test_returns_zero_when_no_chargeable_rows(self):
        """If every row has customer_charged == 0, there's nothing
        to reduce.  Helper must return 0 cleanly rather than
        dividing by zero."""
        from fam.ui.admin_screen import _absorb_customer_pay_delta
        items = [
            {'customer_charged': 0, 'match_amount': 0,
             'method_amount': 500, 'match_percent_snapshot': 0.0},
        ]
        assert _absorb_customer_pay_delta(items, 100) == 0

    def test_screenshot_scenario_food_bucks_100_pct_match(self):
        """Recreate the screenshot: 5 × $2 Food Bucks (100% match),
        customer paid $10, original was $5.55, delta = $4.45.

        After absorption the saved Food Bucks line should reflect
        that the customer truly only paid $5.55 (matching the
        original) and FAM matched only $5.55 (the rest is FAM
        absorption captured on the Unallocated Funds row).

          Food Bucks: customer 555, match 555, method 1110
          Unallocated Funds method: 890

        Verifies: total method (1110 + 890) == receipt (2000), and
        total customer_charged equals the original (555)."""
        from fam.ui.admin_screen import _absorb_customer_pay_delta
        items = [{
            'customer_charged': 1000,    # $10 in cents
            'match_amount': 1000,        # $10 match
            'method_amount': 2000,       # $20 method (5 × $4)
            'match_percent_snapshot': 100.0,
        }]
        delta = 1000 - 555   # $4.45 in cents
        uf_amount = _absorb_customer_pay_delta(items, delta)
        assert items[0]['customer_charged'] == 555
        assert items[0]['match_amount'] == 555
        assert items[0]['method_amount'] == 1110
        assert uf_amount == 890
        # Receipt invariant: existing line method + Unallocated
        # Funds method == original receipt.
        assert items[0]['method_amount'] + uf_amount == 2000

    def test_distributes_proportionally_across_multiple_rows(self):
        """Two rows, customer pay $40 + $10 = $50 total, delta = $10.
        Reduction should hit row 0 by ~$8 and row 1 by ~$2 (4:1
        proportion).  The last chargeable row absorbs any rounding."""
        from fam.ui.admin_screen import _absorb_customer_pay_delta
        items = [
            {'customer_charged': 4000, 'match_amount': 0,
             'method_amount': 4000, 'match_percent_snapshot': 0.0},
            {'customer_charged': 1000, 'match_amount': 0,
             'method_amount': 1000, 'match_percent_snapshot': 0.0},
        ]
        uf = _absorb_customer_pay_delta(items, 1000)
        # Each row should take roughly its proportional share.
        assert items[0]['customer_charged'] == 4000 - 800
        # Row 1 absorbs the remainder (10 cents to last chargeable
        # row to avoid penny drift).
        assert items[1]['customer_charged'] == 1000 - 200
        # 0% match — no match reduction.
        assert items[0]['match_amount'] == 0
        assert items[1]['match_amount'] == 0
        # Method reduction = customer reduction (no match component).
        assert items[0]['method_amount'] == 4000 - 800
        assert items[1]['method_amount'] == 1000 - 200
        # Unallocated Funds amount equals the total method reduction.
        assert uf == 1000

    def test_match_amount_reduces_proportionally_to_match_percent(self):
        """A 100% match row's match_amount reduces by the same amount
        as customer_charged; a 0% match row's match_amount stays 0.
        Pin via SNAP (100%) + Cash (0%) two-row example."""
        from fam.ui.admin_screen import _absorb_customer_pay_delta
        items = [
            # SNAP row at 100% match
            {'customer_charged': 1000, 'match_amount': 1000,
             'method_amount': 2000, 'match_percent_snapshot': 100.0},
            # Cash row at 0% match
            {'customer_charged': 1000, 'match_amount': 0,
             'method_amount': 1000, 'match_percent_snapshot': 0.0},
        ]
        uf = _absorb_customer_pay_delta(items, 200)  # $2 delta
        # 200 cents customer delta split 50/50 (equal customer_charged).
        snap, cash = items
        assert snap['customer_charged'] == 1000 - 100
        assert cash['customer_charged'] == 1000 - 100
        # SNAP: match reduced by 100 (= 100 * 100% / 100); method
        # reduced by 100 + 100 = 200.
        assert snap['match_amount'] == 1000 - 100
        assert snap['method_amount'] == 2000 - 200
        # Cash: match unchanged (0%); method reduced by customer
        # reduction only.
        assert cash['match_amount'] == 0
        assert cash['method_amount'] == 1000 - 100
        # UF picks up combined method shortfall.
        assert uf == (200 + 100)


# ══════════════════════════════════════════════════════════════════
# 2. Source-level guards on the new popup wiring
# ══════════════════════════════════════════════════════════════════
class TestSourceContractAdjustTransaction:

    def _src(self):
        from fam.ui.admin_screen import AdminScreen
        return inspect.getsource(AdminScreen._adjust_transaction)

    def test_customer_pay_delta_is_computed_once(self):
        """All three popup branches share the same delta so they
        agree on the dollar amount — pin that the variable is
        computed up-front rather than recomputed inconsistently."""
        src = self._src()
        assert (
            'customer_pay_delta = (\n'
            '                    new_customer_paid - old_customer_paid' in src
            or 'customer_pay_delta = new_customer_paid - old_customer_paid'
            in src
        ), "customer_pay_delta must be computed once for all popup branches"

    def test_denom_overage_popup_includes_customer_pay_delta(self):
        """When delta > 1, the denom overage popup must mention the
        customer-collection requirement, not just the FAM forfeit."""
        src = self._src()
        # The popup text builder branches on customer_pay_delta > 1
        # and appends a "must also be charged $X more" line.  The
        # literal is broken across f-string fragments in source so
        # search for the distinctive token.
        assert 'must also be' in src and 'charged' in src
        # And the buttons offer Yes/No when delta > 1, with the
        # No path triggering the absorption helper.
        assert 'customer paid the extra' in src
        # Pin the conditional so the wording only appears in the
        # delta > 1 branch (not unconditionally).
        assert 'if customer_pay_delta > 1' in src

    def test_balanced_allocation_popup_fires_only_when_other_paths_did_not(
            self):
        """The new popup must NOT fire when gap > 1 or the denom
        overage popup already handled the case — otherwise the
        manager sees two consecutive popups asking the same
        question."""
        src = self._src()
        assert 'already_handled' in src
        # Pin the predicate.
        assert 'unallocated_funds_cents > 0' in src
        assert 'denom_overage_cents > 0' in src

    def test_no_path_uses_absorption_helper(self):
        """All three No-path branches call _absorb_customer_pay_delta
        + _append_unallocated_funds_row so the math goes through one
        well-tested code path."""
        src = self._src()
        assert '_absorb_customer_pay_delta' in src
        assert '_append_unallocated_funds_row' in src
        # And the helper is called for both the denom-overage No path
        # and the balanced-allocation No path.
        assert src.count('_absorb_customer_pay_delta') >= 2

    def test_yes_path_does_not_modify_line_items(self):
        """When the manager confirms the customer paid the additional
        amount, save proceeds as-entered with no absorption.  Pin
        that the helper is only invoked on the No-button branch."""
        src = self._src()
        # Find the balanced-allocation popup section and verify the
        # absorption call sits inside the no_btn branch.
        balanced = src[src.find("This adjustment increases the customer"):]
        # Walk forward until the next section header.
        next_section = balanced.find('# ── ')
        balanced = (balanced[:next_section]
                    if next_section > 0 else balanced)
        absorb_pos = balanced.find('_absorb_customer_pay_delta')
        no_btn_pos = balanced.find('clickedButton() is no_btn')
        assert absorb_pos > 0 and no_btn_pos > 0
        assert absorb_pos > no_btn_pos, (
            "Absorption must only run inside the No-button branch — "
            "running it on the Yes path would silently double-charge "
            "the customer.")


class TestAppendUnallocatedFundsRow:

    def test_returns_none_when_seed_missing(self, monkeypatch):
        """If the v25 migration didn't seed Unallocated Funds, the
        helper must return None so the caller can show a 'system
        error' message rather than silently failing."""
        from fam.ui import admin_screen
        from fam.models import payment_method

        monkeypatch.setattr(payment_method,
                            'get_unallocated_funds_method',
                            lambda: None)
        items = []
        # Seed lookup is via the caller-level import inside the
        # helper; patch the module path it uses.
        result = admin_screen._append_unallocated_funds_row(items, 500)
        assert result is None
        # No row appended.
        assert items == []

    def test_appends_row_with_correct_shape(self, tmp_path):
        from fam.ui.admin_screen import _append_unallocated_funds_row
        # Need a real DB so the seeded method can be looked up.
        close_connection()
        set_db_path(str(tmp_path / "uf.db"))
        initialize_database()
        try:
            items = []
            uf = _append_unallocated_funds_row(items, 1234)
            assert uf is not None
            assert len(items) == 1
            row = items[0]
            assert row['method_name_snapshot'] == 'Unallocated Funds'
            assert row['method_amount'] == 1234
            assert row['match_amount'] == 0
            assert row['customer_charged'] == 0
            assert row['photo_path'] is None
            assert 'payment_method_id' in row
        finally:
            close_connection()

    def test_no_op_for_zero_amount(self, tmp_path):
        """A zero method_amount means there's nothing to absorb —
        the helper must NOT append a phantom $0 row to the saved
        line items.  Otherwise the ledger gets junk entries."""
        from fam.ui.admin_screen import _append_unallocated_funds_row
        close_connection()
        set_db_path(str(tmp_path / "uf2.db"))
        initialize_database()
        try:
            items = []
            uf = _append_unallocated_funds_row(items, 0)
            # Helper signals "nothing to do" by returning a non-None
            # sentinel (so caller doesn't treat as missing seed) but
            # leaves items unchanged.
            assert uf is not None
            assert items == []
        finally:
            close_connection()
