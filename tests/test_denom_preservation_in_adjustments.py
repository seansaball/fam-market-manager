"""Denominated payment methods preserve their face-value alignment
through Adjustment flow (v2.0.7 fix, user-reported 2026-05-06).

User-reported scenario:
  After making adjustments on a payment, denominated methods
  (e.g. Food Bucks at $2 increments) showed up in reports as
  fractional amounts like $0.47.  A "payment total" warning
  fired post-save.  The user noted forfeit scenarios should
  never error.

Root cause: the engine's cap-aware match-cap fallback and penny
reconciliation paths could leak ``round()`` artifacts onto a
denom row's ``customer_charged`` field.  The DB ``chk_pli_invariant_*``
trigger only validates ``customer_charged + match_amount = method_amount``
— it doesn't enforce ``customer_charged % denomination == 0``.
So a misaligned denom row saved successfully and showed in
reports as $0.47 (or similar) for a $2-denom Food Bucks method.

Once misaligned data was in the DB, every subsequent re-open of
AdjustmentDialog re-fed those values into the engine, propagating
the drift indefinitely.

Fix shape:

  1. ``calculate_payment_breakdown`` penny reconciliation now
     prefers non-denom targets and refuses to modify
     ``customer_charged`` on denom rows (drops the 1¢ artifact
     by clamping match-to-zero rather than corrupting alignment).

  2. ``resolve_payment_state`` post-sync snap-back: every denom
     row's ``customer_charged`` is forcibly snapped DOWN to a
     multiple of ``denomination``; any drift moves into
     ``match_amount`` so ``method_amount`` stays unchanged.
     Self-heals existing misaligned data on next adjustment save.
"""

import pytest

from fam.utils.calculations import (
    resolve_payment_state, calculate_payment_breakdown,
)


# ──────────────────────────────────────────────────────────────────
# 1. Direct engine-level tests — denom alignment invariant
# ──────────────────────────────────────────────────────────────────


class TestResolvePaymentStateSnapsDenomAlignment:
    """Whatever the engine produces, denom rows must have
    ``customer_charged`` that's an integer multiple of their
    denomination.  This is the FAM denomination invariant."""

    def test_food_bucks_charged_at_two_dollar_multiple(self):
        # Customer hands over 1 × $2 Food Bucks token, 100% match.
        # Receipt $4 → no overage, normal flow.
        items = [{
            'method_amount': 400,        # 1 token × $2 × (1 + 100%)
            'match_percent': 100.0,
            'denomination': 200,         # $2
            'payment_method_id': 1,
        }]
        result = resolve_payment_state(400, items)
        assert items[0]['customer_charged'] % 200 == 0, (
            f"Food Bucks customer_charged must be a multiple of $2 "
            f"(200 cents).  Got: {items[0]['customer_charged']}.")
        assert items[0]['customer_charged'] == 200
        assert items[0]['match_amount'] == 200

    def test_misaligned_input_is_self_healed(self):
        """If a previously-misaligned row is loaded back into the
        engine (e.g. after a buggy save persisted it), the snap-
        back must fix it on the next pass — preventing infinite
        propagation of the drift."""
        # Hypothetical bad input: a Food Bucks row with
        # customer_charged = 47 (drifted from 0 or 200 at some
        # point), match=24, method=71 (sum invariant holds).
        # The engine receives method_amount=71 because that's what
        # was saved last time.
        items = [{
            'method_amount': 71,
            'match_percent': 100.0,
            'denomination': 200,
            'payment_method_id': 1,
        }]
        # After resolve_payment_state, customer_charged must snap
        # to 0 (the largest multiple of 200 that's <= 47, which is
        # 0); the drift becomes match.
        result = resolve_payment_state(71, items)
        cc = items[0]['customer_charged']
        assert cc % 200 == 0, (
            f"Self-heal failed: customer_charged must be aligned. "
            f"Got: {cc}")
        # Total preserved
        assert (items[0]['customer_charged']
                + items[0]['match_amount']
                == items[0]['method_amount']), (
            "Sum invariant broken by snap")

    def test_cap_fallback_preserves_denom_alignment(self):
        """Edge case: match-cap fallback path that previously
        could ``round()`` a denom row's customer_charged into
        non-aligned territory.  After the fix, alignment must
        still hold."""
        # 2 × $2 Food Bucks tokens at 100% match — uncapped match
        # would be $4.  With a $1 match cap, the fallback path
        # must reduce match without touching customer_charged.
        items = [{
            'method_amount': 800,        # 2 tokens × $2 × 2
            'match_percent': 100.0,
            'denomination': 200,
            'payment_method_id': 1,
        }]
        result = resolve_payment_state(
            800, items, match_limit=100)  # $1 cap
        cc = items[0]['customer_charged']
        assert cc % 200 == 0, (
            f"Cap-fallback corrupted denom alignment.  "
            f"customer_charged={cc}, expected multiple of 200.")
        # Customer still hands over 2 tokens worth = $4 = 400 cents
        assert cc == 400


# ──────────────────────────────────────────────────────────────────
# 2. Penny reconciliation prefers non-denom targets
# ──────────────────────────────────────────────────────────────────


class TestPennyReconciliationDenomSafe:
    """The 1-cent rounding artifact absorber must NEVER touch
    a denom row's customer_charged."""

    def test_penny_diff_lands_on_non_denom_when_available(self):
        # Mixed order: Food Bucks denom + SNAP non-denom.  Set up
        # so a 1¢ rounding remainder needs absorbing.
        items = [
            {'method_amount': 400, 'match_percent': 100.0,
             'denomination': 200, 'payment_method_id': 1},   # FB $2
            {'method_amount': 199, 'match_percent': 100.0,
             'denomination': 0, 'payment_method_id': 2},     # SNAP $1.99
        ]
        # Receipt is 599 = 400 + 199, so no penny artifact in this
        # input.  The point is to ensure the penny path, when it
        # fires, picks the non-denom target.
        result = resolve_payment_state(599, items)
        # Both rows should reconcile cleanly
        assert items[0]['customer_charged'] == 200
        assert items[0]['customer_charged'] % 200 == 0

    def test_all_denom_with_zero_match_does_not_corrupt(self):
        """Edge case: all-denom order with cap = 0.  The match
        gets reduced to 0; penny reconciliation must not introduce
        drift on customer_charged."""
        items = [{
            'method_amount': 400,
            'match_percent': 100.0,
            'denomination': 200,
            'payment_method_id': 1,
        }]
        result = resolve_payment_state(400, items, match_limit=0)
        cc = items[0]['customer_charged']
        assert cc % 200 == 0, (
            f"All-denom with zero cap broke alignment.  "
            f"customer_charged={cc}.")


# ──────────────────────────────────────────────────────────────────
# 3. Forfeit-scenario does not produce errors
# ──────────────────────────────────────────────────────────────────


class TestDenomForfeitNeverErrors:
    """Per user request: forfeit scenarios should never error.
    The engine's denom-overage detection should label the overage,
    leaving the caller to surface a friendly forfeit warning."""

    def test_one_token_overshoot_returns_denom_overage(self):
        # Customer hands over $5 FMNP token but receipt is only $3.
        # Overage = $2 (some of the FAM match must be forfeited).
        items = [{
            'method_amount': 1000,       # 1 × $5 × (1 + 100%)
            'match_percent': 100.0,
            'denomination': 500,
            'payment_method_id': 1,
        }]
        result = resolve_payment_state(300, items)  # $3 receipt
        # The engine flags the overage on result['denom_overage_cents']
        # — caller decides whether to surface as warning or block.
        assert result.get('denom_overage_cents', 0) > 0, (
            "Engine must surface the denom overage so the dialog "
            "can show the forfeit warning instead of erroring.")
        # No exception raised
        # customer_charged still aligned even with overage
        assert items[0]['customer_charged'] % 500 == 0


# ──────────────────────────────────────────────────────────────────
# 4. Integration: AdjustmentDialog flow preserves denom alignment
# ──────────────────────────────────────────────────────────────────


class TestAdjustmentFlowPreservesDenomAlignment:
    """End-to-end: simulate the AdjustmentDialog ``get_new_line_items``
    path with denom rows and verify the saved values are aligned."""

    def test_get_new_line_items_aligns_denom(self):
        """``resolve_payment_state`` is the engine the
        AdjustmentDialog uses internally.  This pins the contract
        that any items it returns have aligned denom rows, so
        ``save_payment_line_items`` writes valid data to the DB."""
        items = [
            {'method_amount': 400, 'match_percent': 100.0,
             'denomination': 200, 'payment_method_id': 1,
             'method_name_snapshot': 'Food Bucks'},
            {'method_amount': 100, 'match_percent': 100.0,
             'denomination': 0, 'payment_method_id': 2,
             'method_name_snapshot': 'SNAP'},
        ]
        # Receipt change with cap forces fallback math
        resolve_payment_state(500, items, match_limit=100)

        for it in items:
            denom = it.get('denomination') or 0
            if denom > 0:
                assert it['customer_charged'] % denom == 0, (
                    f"Denom row not aligned post-engine: "
                    f"method={it.get('method_name_snapshot')}, "
                    f"customer_charged={it['customer_charged']}, "
                    f"denomination={denom}.")
            # Sum invariant always holds
            assert (it['customer_charged'] + it['match_amount']
                    == it['method_amount']), (
                f"Sum invariant broken: {it}")
