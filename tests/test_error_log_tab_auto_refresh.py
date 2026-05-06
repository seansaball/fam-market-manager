"""Error Log tab auto-refreshes on every selection (v2.0.6).

User-reported (2026-05-06): a DB-fragmentation WARNING was visible
in the cloud Error Log sheet but missing from the local Reports →
Error Log tab.  Root cause: the tab was lazy-loaded ONCE on first
selection (``if not self._error_log_loaded``) and only manually
refreshable thereafter.  The cloud sync re-parses ``fam_manager.log``
fresh on every cycle, so the cloud picked up new entries while the
local UI silently dropped them.

Fix: re-load on every tab switch, not just the first.  ``parse_log_file``
is capped at 500 entries and reads the rotating log file (typically
under 1MB), so the per-switch cost is in the single-digit-ms range.
"""

import inspect


class TestErrorLogTabReloadsOnEverySelect:
    """Source-pin: ``_on_tab_changed`` must re-load the Error Log
    on every selection — not gate on ``_error_log_loaded``."""

    def test_tab_change_handler_does_not_skip_when_already_loaded(
            self):
        import fam.ui.reports_screen as rs
        src = inspect.getsource(
            rs.ReportsScreen._on_tab_changed)

        # The pre-fix gate "and not self._error_log_loaded" must be
        # gone — the tab must reload regardless of prior load state.
        assert 'and not self._error_log_loaded' not in src, (
            "_on_tab_changed must NOT short-circuit when "
            "_error_log_loaded is True.  Pre-fix the tab loaded "
            "once on first click and never auto-refreshed, so new "
            "log entries (e.g. WARNING entries appended during a "
            "sync cycle) were invisible in the UI even though the "
            "cloud sync picked them up.")

        # And the body must still call _load_error_log when the
        # active tab is Error Log.
        assert '_load_error_log()' in src
        assert '"Error Log"' in src or "'Error Log'" in src, (
            "_on_tab_changed must compare the active tab text to "
            "the literal 'Error Log' before re-loading.")
