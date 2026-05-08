"""Layer 2B capacity check: non-denom method can't exceed eligible-
vendor coverage (v2.0.7, user-reported 2026-05-06).

User reproducer:
  Returning customer with $94.41 already redeemed, 7 receipts:
    Jill's gourmet dips:  $26.73 (❌ SNAP)
    Pgh + Cakery + Rockin + Kizzle = $286.60 (all SNAP-eligible)

  Volunteer enters Food RX 2 × $10 bound to Jill's + SNAP $295.42.

  SNAP-eligible vendor capacity = $286.60.
  SNAP customer = $295.42 → overshoots by $8.82.

  Pre-fix: the system tried to absorb the overshoot via proportional
  distribution, leaking SNAP onto Jill's transactions despite the
  ❌ eligibility marker.  The resulting per-vendor reconciliation
  produced contradictory error messages — over-allocation on
  Jill's overall, but under-allocation on a Jill's sub-receipt.

Fix: a new Layer 2B check fires BEFORE Layer 2C.  For each non-
denom method, sum the receipts of vendors that accept it and
subtract any denom allocations bound to those eligible vendors.
If the volunteer's non-denom method_amount exceeds this capacity,
fire ONE clear error: "[Method] of $X exceeds eligible-vendor
capacity of $Y by $Z.  Reduce [Method] by $Z and add another
method bound to [ineligible vendor] for the residual."
"""

import inspect


class TestLayer2BSourcePin:
    """Pin that the new check is in place and contains the right
    pieces — the runtime test would require driving the full
    PaymentScreen confirm flow which has heavy DB + UI deps."""

    def test_layer_2b_capacity_check_exists(self):
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._confirm_payment)
        assert 'Layer 2B' in src, (
            "_confirm_payment must contain a Layer 2B header for "
            "the non-denom capacity check.")
        assert 'eligible_capacity' in src, (
            "Layer 2B must compute eligible_capacity per non-denom "
            "method.")
        # f-string is line-broken in source, so check fragments
        assert 'exceeds the eligible-' in src and 'vendor capacity' in src, (
            "Layer 2B error message must clearly say the method "
            "exceeds eligible-vendor capacity (not the cryptic "
            "per-receipt over/under message).")

    def test_layer_2b_runs_before_layer_2c(self):
        """The capacity check must fire BEFORE the per-receipt
        reconciliation so the user sees the actionable error first
        instead of the confusing per-receipt math."""
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._confirm_payment)
        # Match the actual section headers, not stray references
        b2_marker = 'Layer 2B: non-denom method capacity check'
        c_marker = 'Layer 2C: per-transaction reconciliation'
        b2_idx = src.find(b2_marker)
        c_idx = src.find(c_marker)
        assert b2_idx > 0, (
            f"Layer 2B section header not found in "
            f"_confirm_payment source.")
        assert c_idx > 0, (
            f"Layer 2C section header not found in "
            f"_confirm_payment source.")
        assert b2_idx < c_idx, (
            "Layer 2B (capacity check) must run before Layer 2C "
            "(per-receipt reconciliation).")

    def test_error_names_method_and_dollar_overshoot(self):
        """The error string must include the method name and the
        exact dollar amount to reduce — not a generic 'reduce'
        instruction."""
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._confirm_payment)
        # The error message format string
        assert 'method_name' in src and 'overshoot' in src, (
            "Error must format the method name and overshoot "
            "amount into the message.")
        assert 'reduce' in src.lower(), (
            "Error must instruct the volunteer to REDUCE the "
            "method (not 'increase' or 'fix').")

    def test_permissive_legacy_vendors_still_pass(self):
        """Source-pin: the eligibility check must treat legacy /
        un-configured vendors (empty vendor_payment_methods) as
        permissive, consistent with Phase 2 save-side logic."""
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._confirm_payment)
        # The function _b2_eligible must have a legacy fallback
        assert 'legacy/permissive' in src or 'permissive' in src, (
            "Layer 2B must include the legacy/permissive fallback "
            "for vendors with no eligibility config.")
