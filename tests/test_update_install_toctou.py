"""Update install pre-launch TOCTOU re-check (v2.0.2 fix C5).

Pre-v2.0.2 ``_download_and_install`` checked for an open market day
once before kicking off the download, but the download itself takes
30s–several minutes — long enough for a volunteer to navigate to
Market Day and click Open mid-download.  Without a re-check at
``_on_download_finished``, the install would proceed and the app
would silently quit — losing in-flight Receipt Intake state.

These tests pin the source-level re-check so future refactors of
``_on_download_finished`` can't accidentally drop the guard.
"""

import inspect


def _get_on_download_finished_source() -> str:
    """Return the source of ``_on_download_finished`` from the
    Settings screen.  We slice the module source rather than using
    inspect.getsource on the method to avoid having to instantiate
    the screen (it depends on Qt + DB + active singletons)."""
    import fam.ui.settings_screen as ss
    full = inspect.getsource(ss)
    marker = 'def _on_download_finished('
    start = full.find(marker)
    assert start != -1, (
        "could not locate _on_download_finished in source — "
        "method may have been renamed; update this test to match.")
    end = full.find('\n    def ', start + len(marker))
    if end == -1:
        end = len(full)
    return full[start:end]


def _get_download_and_install_source() -> str:
    import fam.ui.settings_screen as ss
    full = inspect.getsource(ss)
    marker = 'def _download_and_install('
    start = full.find(marker)
    assert start != -1
    end = full.find('\n    def ', start + len(marker))
    if end == -1:
        end = len(full)
    return full[start:end]


class TestPreInstallMarketDayRecheck:
    """``_on_download_finished`` must re-query open-market-day state
    before launching the install script."""

    def test_calls_get_open_market_day(self):
        src = _get_on_download_finished_source()
        assert 'get_open_market_day' in src, (
            "_on_download_finished must call get_open_market_day() "
            "to re-verify the state before launching the install — "
            "the pre-download guard alone allows a TOCTOU window "
            "during the download.")

    def test_aborts_on_open_market_day(self):
        src = _get_on_download_finished_source()
        # Look for the abort path: a return statement gated by the
        # open-market-day check.
        assert 'Market Day Opened During Download' in src or \
               'market day was opened' in src, (
            "_on_download_finished must show a clear abort message "
            "when a market day was opened during the download.")

    def test_renables_buttons_on_abort(self):
        """When the install aborts, the user must be able to retry
        — leaving the Install button disabled would be a UX dead-end."""
        src = _get_on_download_finished_source()
        assert '_update_install_btn.setEnabled(True)' in src or \
               '_update_check_btn.setEnabled(True)' in src

    def test_fails_closed_on_db_error(self):
        """If the re-check raises (DB locked, schema corruption),
        the install must be cancelled — silent fall-through to the
        install would lose data."""
        src = _get_on_download_finished_source()
        assert 'except Exception' in src
        # The except branch must abort, not silently continue.
        assert 'Cannot Verify State' in src or \
               'cancelled' in src.lower()


class TestPreDownloadGuardStillPresent:
    """The v2.0.1 pre-download guard must remain.  C5 ADDS a second
    check; it doesn't replace the first."""

    def test_pre_download_check(self):
        src = _get_download_and_install_source()
        assert 'get_open_market_day' in src, (
            "_download_and_install must still check for an open "
            "market day before kicking off the download (the "
            "pre-download guard).  C5 added a SECOND check at "
            "install time but the first one must remain.")


class TestUpdateRepoAllowListCheckAtInstall:
    """C4 defense-in-depth: install must refuse non-allow-listed
    repo URLs even if a malformed value slipped past the save path."""

    def test_install_validates_allow_list(self):
        src = _get_download_and_install_source()
        assert '_is_allowed_repo_url' in src, (
            "_download_and_install must validate the saved repo URL "
            "against the allow-list before proceeding.  Otherwise a "
            "tampered DB row could pivot the auto-update channel.")

    def test_blocks_with_clear_message(self):
        src = _get_download_and_install_source()
        assert 'Update Blocked' in src or 'allow-list' in src
