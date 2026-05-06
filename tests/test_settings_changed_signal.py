"""Settings mutations trigger a cloud-sync signal (v2.0.6 fix).

Coordinator-reported gap: pre-fix, renaming a vendor or market in
Settings → didn't update the centralized Google Sheet until the next
unrelated mutation (a new transaction, a market close) happened to
fire ``data_changed``.  Multi-workstation coordinators saw stale
names on the shared sheet for hours.

Fix shape (mirrors the FMNP closed-day pattern):
  * ``SettingsScreen.settings_changed = Signal()`` declared at class
    level.
  * Every mutation handler that affects cloud-bound rows emits the
    signal after a successful change:
      - market add / edit / toggle / delete
      - match-limit edit / toggle
      - market↔vendor and market↔payment-method assignment
      - vendor add / edit / toggle and vendor↔market assignment
      - vendor↔payment-method eligibility
      - payment method add / edit / toggle
      - reward rule add / toggle / delete
  * MainWindow connects ``settings_changed`` → ``_on_settings_changed``
    which calls ``_trigger_sync(force=True)`` for a FULL-scope sweep.
    A narrow per-md scope would miss whole-dataset tabs (Vendor
    Reimbursement, Error Log) which is exactly where renames need to
    propagate.

These tests verify the wiring exists.  They run as source-pin tests
so they don't require Qt + DB plumbing for every individual emit
site — that would be a maintenance liability.  Source-pin catches
regressions where someone refactors a handler and forgets the emit.
"""

import inspect

import pytest


# ─── Signal declaration & main_window slot ────────────────────────


class TestSettingsChangedSignalDeclared:
    """The class-level ``settings_changed = Signal()`` declaration
    must exist so connections in main_window resolve."""

    def test_settings_screen_has_settings_changed_signal(self):
        import fam.ui.settings_screen as ss
        src = inspect.getsource(ss.SettingsScreen)
        assert 'settings_changed = Signal()' in src, (
            "SettingsScreen must declare ``settings_changed = "
            "Signal()`` at class level so MainWindow can connect to "
            "it for cloud-sync notification on settings mutations.")


class TestMainWindowWiresSettingsToFullSync:
    """Connection + slot must exist in main_window so settings
    mutations actually reach the cloud."""

    def test_settings_screen_signal_is_connected(self):
        import fam.ui.main_window as mw
        src = inspect.getsource(mw)
        assert ('settings_screen.settings_changed.connect('
                'self._on_settings_changed)' in src
                or 'settings_screen.settings_changed.connect(\n'
                in src), (
            "MainWindow must connect "
            "settings_screen.settings_changed to "
            "_on_settings_changed.")

    def test_on_settings_changed_slot_exists(self):
        import fam.ui.main_window as mw
        assert hasattr(mw.MainWindow, '_on_settings_changed'), (
            "MainWindow must define _on_settings_changed slot.")

    def test_on_settings_changed_forces_full_scope(self):
        """The slot must call ``_trigger_sync(force=True)`` so
        whole-dataset tabs (Vendor Reimbursement, Error Log) are
        included.  A narrow per-md sync would miss them entirely."""
        import fam.ui.main_window as mw
        src = inspect.getsource(mw.MainWindow._on_settings_changed)
        assert 'force=True' in src, (
            "_on_settings_changed must call _trigger_sync with "
            "force=True.  Settings changes affect rows across all "
            "markets and time — a narrow per-md auto-sync would "
            "miss whole-dataset tabs (Vendor Reimbursement, Error "
            "Log).  Full-scope ensures whole-dataset cleanup runs.")


# ─── Every mutation handler emits the signal ─────────────────────


class TestEveryMutationHandlerEmits:
    """Source-pin: every settings mutation handler must contain
    ``self.settings_changed.emit()`` so the cloud sheet stays in
    sync.  Adding a new mutation handler without an emit will fail
    its corresponding test below."""

    @pytest.mark.parametrize("handler_name", [
        # Markets tab
        '_add_market',
        '_edit_market',
        '_toggle_market',
        '_delete_market',
        '_edit_match_limit',
        '_toggle_match_limit',
        '_assign_vendors',
        '_assign_payment_methods',
        # Vendors tab
        '_add_vendor',
        '_edit_vendor',
        '_toggle_vendor',
        '_assign_markets_to_vendor',
        '_assign_payment_methods_to_vendor',
        # Payment methods tab
        '_add_payment_method',
        '_edit_pm',
        '_toggle_pm',
        # Rewards tab
        '_add_reward_rule',
        '_toggle_reward_rule',
        '_delete_reward_rule',
    ])
    def test_handler_emits_settings_changed(self, handler_name):
        import fam.ui.settings_screen as ss
        handler = getattr(ss.SettingsScreen, handler_name, None)
        assert handler is not None, (
            f"SettingsScreen.{handler_name} no longer exists — "
            f"either the handler was renamed (update this test) "
            f"or removed (update the parametrize list).")
        src = inspect.getsource(handler)
        assert 'self.settings_changed.emit()' in src, (
            f"SettingsScreen.{handler_name} must emit "
            f"self.settings_changed after a successful mutation so "
            f"the cloud sheet picks up the change.  Pre-v2.0.6, "
            f"settings changes (e.g. vendor renames) only reached "
            f"the cloud when an unrelated mutation happened to "
            f"trigger a sync — sometimes hours later.")


# ─── Market-code change warning ──────────────────────────────────


class TestMarketCodeRenameWarning:
    """v2.0.6: renaming a market in a way that changes the derived
    market_code is destructive on the cloud (existing rows under the
    old code go stale).  The handler must surface a warning before
    proceeding."""

    def test_edit_market_uses_derive_market_code_for_warning(self):
        import fam.ui.settings_screen as ss
        src = inspect.getsource(ss.SettingsScreen._edit_market)
        assert 'derive_market_code' in src, (
            "_edit_market must compare derived market_code "
            "before/after the rename to surface a warning when "
            "the cloud-sheet identity is about to shift.")
        assert 'Market Code Will Change' in src or \
               'market code' in src.lower(), (
            "_edit_market must surface an explicit warning dialog "
            "when the market_code derivation would change.  Pre-fix "
            "renames silently moved cloud-sheet identity, leaving "
            "old-code rows stranded for whole-dataset cleanup.")
