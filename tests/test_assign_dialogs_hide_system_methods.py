"""``Assign Payment Methods`` dialogs must hide system-managed
methods (v2.0.6 fix).

User-reported: Settings → Markets → "Assign Payment Methods to:
[Market]" showed ``Unallocated Funds`` as a checkbox the operator
could untick.  But UF is system-managed (``is_system=1``):
  * The Adjustments "customer gone" path injects an UF row to
    absorb the gap regardless of any market_payment_methods row.
  * The v34 trigger forces ``customer_charged=0`` and
    ``match_amount=0`` on UF rows so they never affect totals.
  * Unticking the box has no effect at the engine layer.

Showing UF as a checkbox in market / vendor assignment dialogs was
just confusing.  v2.0.6 hides system methods from these dialogs.
The Settings → Payment Methods tab still LISTS UF (with edit /
toggle / reorder buttons disabled) so the operator knows it
exists; this test pins that the assignment-checkbox dialogs
specifically do NOT include it.
"""

import inspect

import pytest


class TestAssignPaymentMethodsDialogHidesSystem:

    def test_market_assign_dialog_uses_include_system_false(self):
        """Source-pin: ``AssignPaymentMethodsDialog`` calls
        ``get_all_payment_methods(include_system=False)`` so UF is
        excluded from the checkbox list."""
        from fam.ui.settings_screen import AssignPaymentMethodsDialog
        src = inspect.getsource(AssignPaymentMethodsDialog)
        assert 'include_system=False' in src, (
            "AssignPaymentMethodsDialog must pass "
            "include_system=False to get_all_payment_methods so the "
            "system-managed Unallocated Funds method (and any future "
            "is_system=1 method) does not appear as a coordinator-"
            "tickable checkbox in Settings → Markets → 'Assign "
            "Payment Methods'.")

    def test_vendor_eligible_methods_dialog_uses_include_system_false(self):
        """Same contract for the per-vendor eligibility dialog."""
        from fam.ui.settings_screen import VendorEligiblePaymentMethodsDialog
        src = inspect.getsource(VendorEligiblePaymentMethodsDialog)
        assert 'include_system=False' in src, (
            "VendorEligiblePaymentMethodsDialog must also exclude "
            "system methods.  UF isn't a vendor-acceptable method.")


class TestPaymentMethodTabStillShowsUF:
    """The Settings → Payment Methods tab itself MUST continue to
    list UF (with locked Edit / Toggle / Reorder buttons) so the
    operator can see that the system method exists.  Hiding it
    entirely would be confusing — they'd see references to "system
    method" everywhere else with no way to inspect what it is."""

    def test_pm_table_loader_does_not_filter_system(self):
        """``_load_payment_methods`` populates the Payment Methods
        TABLE in Settings.  This must still include UF so the
        operator can see the row (even though buttons are disabled)."""
        from fam.ui.settings_screen import SettingsScreen
        src = inspect.getsource(SettingsScreen._load_payment_methods)
        # The table loader uses bare get_all_payment_methods() (no
        # include_system filter) — keep it that way.
        assert 'include_system=False' not in src, (
            "_load_payment_methods (the Settings → Payment Methods "
            "TABLE loader) must NOT filter out system methods.  "
            "The operator needs to see UF exists; they just can't "
            "edit/toggle/reorder it (buttons disabled with tooltip).")
