"""Layer 2B capacity check skips when no ineligible vendors exist
(v2.0.7 follow-up, 2026-05-06).

Context: Layer 2B was added to surface a clear error when the
volunteer over-allocates a non-denom method to vendors that
include some ineligible-for-the-method.  After the universal
SNAP/Cash binding policy, those mixed-eligibility scenarios
disappear for SNAP/Cash — but the Layer 2B arithmetic still
detects "non-denom method exceeds remaining receipt capacity
after denom allocation".  In that case the issue is **over-
allocation, not eligibility**, and the message blaming
"vendors that cannot accept SNAP" is misleading because every
vendor accepts SNAP.

Fix: Layer 2B's eligibility-blamed error now fires ONLY when
``ineligible_vendor_names`` is non-empty.  The over-allocation
case falls through to Layer 2C's per-receipt message — accurate
and actionable.
"""

import inspect


class TestLayer2BSkipsWhenAllVendorsEligible:
    """Source-pin: the eligibility-blamed error must require
    ineligible_vendor_names to be truthy before firing."""

    def test_layer_2b_gated_on_ineligible_vendor_names(self):
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._confirm_payment)
        # The if-condition must check both overshoot AND
        # ineligible_vendor_names — without the latter, the
        # eligibility-blamed error fires misleadingly when SNAP
        # overshoots due to over-allocation, not eligibility.
        assert 'overshoot > 1 and ineligible_vendor_names' in src, (
            "Layer 2B's error must be gated on "
            "`ineligible_vendor_names` being non-empty.  Without "
            "that gate, the eligibility-blamed message fires "
            "even when all vendors accept the method (e.g. with "
            "the universal SNAP/Cash binding policy), confusing "
            "the volunteer about the actual cause.")

    def test_layer_2b_logs_eligibility_bounded_distinguishes(self):
        """When Layer 2B does fire, the log line should call out
        that it's the eligibility-bounded path (not just
        over-allocation) so post-mortem analysis distinguishes
        the two error classes."""
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._confirm_payment)
        assert 'eligibility-bounded' in src, (
            "Layer 2B's logger.warning should label the path as "
            "'eligibility-bounded' so future debugging can tell "
            "whether the error fired due to a real eligibility "
            "constraint vs. a generic over-allocation.")
