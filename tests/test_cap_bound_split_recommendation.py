"""Cap-bound impossible-to-balance scenarios surface a clear
split-order recommendation (v2.0.7-final, 2026-05-06).

Background
----------
A returning customer with most of their daily FAM match cap already
redeemed can hit a deterministically unbalanceable state when they
combine a denominated payment (Food RX, Food Bucks) with a non-denom
payment (SNAP) where:

  * the cap headroom is smaller than the denom row's uncapped match,
  * the volunteer's spinbox shows non-denom $X but the engine wants
    less because the cap-fallback path inflates non-denom method
    to absorb the denom-row's match shrinkage.

We tried to auto-rebalance the non-denom row in the UI (v2.0.7
intermediate) but the engine's deterministic cap-aware Path B + Pass 4
overwrote the rebalance on every ``_update_summary`` cycle, creating
either UI flicker or Layer 2A "row mismatch" errors at confirm.

Per the user's audit feedback: "instead of letting the user break the
app they can always just split the transactions out into multiple ones
and not cram everything into one, so if that error comes up I'd like
that to be a recommendation as well."

Fix: Layer 2A's mismatch error is enriched when it detects the
cap-bound + denom + non-denom-overshoot pattern.  The enriched message:

  1. Names the cap-binding as the root cause (not "you typed the
     wrong amount").
  2. States the exact gap to reduce (so the volunteer can fix it
     in one edit if they prefer).
  3. RECOMMENDS splitting the customer's receipts into multiple
     orders so each gets its own cap allocation — the cleanest
     mental model for the volunteer.

This is a defensive UX improvement; the underlying engine behaviour
is unchanged.  Other Layer 2A mismatches (non-cap-bound, denom-only,
spinbox under engine, etc.) still get the original error message.
"""

import inspect


class TestSplitRecommendationSourcePins:
    """The defensive block must exist in source with the right
    detection criteria and message content."""

    def test_layer_2a_enriches_cap_bound_case(self):
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._confirm_payment)
        # Must compute the cap-bound + denom + non-denom-overshoot
        # detection before deciding which message to show.
        assert 'show_split_recommendation' in src, (
            "Layer 2A must compute a `show_split_recommendation` "
            "flag so the cap-bound impossible-to-balance scenario "
            "gets the enriched dialog instead of the generic "
            "'Payment row mismatch' message.")
        assert 'is_cap_bound' in src
        assert "result.get('match_was_capped')" in src, (
            "Detection must source `match_was_capped` from the "
            "engine result — the only authoritative cap-binding "
            "signal.")
        assert 'is_non_denom_row' in src, (
            "Detection must distinguish non-denom rows from denom "
            "rows — the recommendation only applies when the "
            "spinbox-vs-engine gap is on a non-denom row (denom "
            "rows have customer_forfeit_cents handling).")
        assert 'has_denom_row' in src, (
            "Detection must require a denom row to exist — without "
            "one, the cap-bound case can't produce this specific "
            "mismatch pattern.")
        assert 'spinbox_overshoot' in src, (
            "Detection must require the spinbox to be HIGHER than "
            "the engine wants (overshoot > 0).  The under-shoot "
            "case is a different bug class.")

    def test_split_recommendation_mentions_split_into_orders(self):
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._confirm_payment)
        # The recommendation must explicitly call out splitting
        # the order — that's the user's preferred resolution.
        assert 'Split this customer' in src or 'split this customer' in src.lower(), (
            "The defensive message must explicitly recommend "
            "splitting the customer's receipts into two orders — "
            "this was the user-requested fallback after the auto-"
            "rebalance approach was reverted.")
        assert 'Receipt Intake' in src, (
            "The recommendation should name the screen the "
            "volunteer needs to navigate to ('Receipt Intake') so "
            "the resolution path is unambiguous.")
        assert 'cap' in src.lower(), (
            "The message should mention the cap as the root cause "
            "so the volunteer understands why the math doesn't add "
            "up — without that context the recommendation feels "
            "arbitrary.")

    def test_split_recommendation_states_the_exact_gap(self):
        """Volunteers who prefer a one-edit fix need to know the
        exact dollar amount to reduce."""
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._confirm_payment)
        assert 'gap_str = format_dollars(spinbox_overshoot)' in src, (
            "The enriched message must format and surface the "
            "exact gap in dollars — option 1 of the recommendation "
            "is 'reduce by exactly $X.YY'.")

    def test_layer_2a_logs_cap_bound_path_distinctly(self):
        """Post-mortem analysis needs to distinguish cap-bound
        rejections from other Layer 2A mismatches."""
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._confirm_payment)
        assert 'Cap-bound impossible-to-balance scenario' in src, (
            "Logger should label the cap-bound branch distinctly "
            "(not just 'Charge-integrity guard tripped') so future "
            "debugging can tell whether the rejection was the "
            "common cap-bound case vs. a real coherence violation.")

    def test_generic_mismatch_message_still_present(self):
        """Non-cap-bound mismatches must still get the original
        actionable message (Auto-Distribute hint).  We're enriching,
        not replacing."""
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._confirm_payment)
        assert 'Click Auto-Distribute or correct' in src, (
            "The generic Layer 2A message (with Auto-Distribute "
            "hint) must still exist for non-cap-bound mismatches — "
            "those are correctable via Auto-Distribute and don't "
            "need the split-order recommendation.")


class TestRevertedAutoRebalance:
    """The auto-rebalance method that fought the engine
    deterministically must STAY removed.  This pins the revert so a
    future contributor doesn't re-introduce it without understanding
    why it failed."""

    def test_auto_rebalance_method_is_absent(self):
        from fam.ui.payment_screen import PaymentScreen
        assert not hasattr(PaymentScreen, '_auto_rebalance_non_denom'), (
            "PaymentScreen._auto_rebalance_non_denom was reverted "
            "in v2.0.7-final because it fought the engine's "
            "deterministic cap-aware Path B + Pass 4.  Even with "
            "correct math, the engine overwrote the rebalanced "
            "value on the next _update_summary cycle, producing "
            "either UI flicker or Layer 2A mismatches at confirm.  "
            "If you're considering re-adding it, see the cap-bound "
            "split recommendation in _confirm_payment instead — "
            "that's the durable defensive approach.")

    def test_payment_row_does_not_emit_denom_quantity_changed(self):
        """The ``denom_quantity_changed`` signal was the targeted
        hook for auto-rebalance.  With auto-rebalance reverted, the
        signal has no consumer and should be removed to keep the
        widget surface clean."""
        from fam.ui.widgets.payment_row import PaymentRow
        assert not hasattr(PaymentRow, 'denom_quantity_changed'), (
            "PaymentRow.denom_quantity_changed was removed alongside "
            "the auto-rebalance revert — there's no longer a "
            "consumer for it.  Reintroducing it without a consumer "
            "is dead-code.  See _confirm_payment's split-order "
            "recommendation for the current defensive approach.")
