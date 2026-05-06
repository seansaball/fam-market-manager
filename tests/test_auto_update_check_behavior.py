"""Auto-update-check launch behavior
(v2.0.1, 2026-05-01).

Pin the enhanced launch-time auto-update flow.  Pre-v2.0.1 the
popup was tied 1-to-1 to the on-the-wire GitHub API call, so a
24-hour cooldown silenced the popup for the entire window between
release publication and the volunteer's next-day launch.  Many
volunteers reported never seeing the popup despite running an
older version.

The v2.0.1 enhancements pinned by this file:

  1. **Cache-replay on launch.**  When a previous check (this
     session or a prior launch) cached a remote version newer than
     ``__version__`` and the user hasn't permanently Ignored it,
     the popup re-fires every launch — no API call needed.  This
     is what makes the popup actually surface for users who had
     never seen it.

  2. **Cooldown reduced from 24h to 6h.**  GitHub's anonymous API
     rate limit is 60/h; 6h per laptop is comfortably under.

  3. **OK = snooze 6h.**  Clicking OK on the popup writes
     ``update_remind_after`` so the popup is suppressed for 6h
     across launches.  After the snooze expires it re-fires.

  4. **Ignore = silence forever for that exact version.**  Same
     as before, but now also clears any prior snooze so a future
     newer version isn't accidentally suppressed.

  5. **Diagnostic visibility.**  Help → System Status now exposes
     the auto-check state (``enabled``, ``last_check``,
     ``latest_known_remote``, ``dismissed_version``,
     ``snoozed_until``, ``eligible_now``) so a coordinator
     triaging "popup never fires" can see exactly which gate is
     closed.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


# ════════════════════════════════════════════════════════════════════
# 1. Cache-replay surfaces the popup without a network call
# ════════════════════════════════════════════════════════════════════


class TestCachedUpdateReplay:
    """When a prior check cached a newer remote version and the
    user hasn't dismissed/snoozed it, ``_maybe_replay_cached_update``
    must show the popup — even within the on-the-wire cooldown."""

    def test_replay_fires_when_cache_newer_and_no_dismiss_no_snooze(
            self, qtbot, monkeypatch, tmp_path):
        from fam.ui.main_window import MainWindow
        from fam.utils.app_settings import set_setting
        from fam import __version__

        # Stage a cached pending version newer than ours.
        set_setting('update_last_version', '999.0.0')
        set_setting('update_dismissed_version', '')
        set_setting('update_remind_after', '')

        # Capture the popup call — don't actually open a dialog.
        called = {'count': 0, 'version': None}
        monkeypatch.setattr(
            MainWindow, '_show_update_available_popup',
            lambda self, v: called.update(
                {'count': called['count'] + 1, 'version': v}),
        )

        win = MainWindow()
        qtbot.addWidget(win)
        win._maybe_replay_cached_update()

        assert called['count'] == 1, (
            "Cache-replay must show the popup when the cached "
            "version is newer than __version__ and not dismissed")
        assert called['version'] == '999.0.0'

    def test_replay_skipped_when_local_version_caught_up(
            self, qtbot, monkeypatch):
        from fam.ui.main_window import MainWindow
        from fam.utils.app_settings import set_setting
        from fam import __version__

        # Cache a version equal to ours — we've caught up.
        set_setting('update_last_version', __version__)
        set_setting('update_dismissed_version', '')
        set_setting('update_remind_after', '')

        called = {'count': 0}
        monkeypatch.setattr(
            MainWindow, '_show_update_available_popup',
            lambda self, v: called.update(
                {'count': called['count'] + 1}),
        )

        win = MainWindow()
        qtbot.addWidget(win)
        win._maybe_replay_cached_update()

        assert called['count'] == 0

    def test_replay_skipped_when_user_dismissed_that_exact_version(
            self, qtbot, monkeypatch):
        from fam.ui.main_window import MainWindow
        from fam.utils.app_settings import set_setting

        set_setting('update_last_version', '999.0.0')
        set_setting('update_dismissed_version', '999.0.0')
        set_setting('update_remind_after', '')

        called = {'count': 0}
        monkeypatch.setattr(
            MainWindow, '_show_update_available_popup',
            lambda self, v: called.update(
                {'count': called['count'] + 1}),
        )

        win = MainWindow()
        qtbot.addWidget(win)
        win._maybe_replay_cached_update()

        assert called['count'] == 0, (
            "Permanent Ignore must keep replay silent for that "
            "exact version")

    def test_replay_skipped_during_snooze_window(
            self, qtbot, monkeypatch):
        from fam.ui.main_window import MainWindow
        from fam.utils.app_settings import set_setting
        from fam.utils.timezone import eastern_now

        set_setting('update_last_version', '999.0.0')
        set_setting('update_dismissed_version', '')
        # Snooze active for another 30 minutes.
        set_setting(
            'update_remind_after',
            (eastern_now() + timedelta(minutes=30)).isoformat())

        called = {'count': 0}
        monkeypatch.setattr(
            MainWindow, '_show_update_available_popup',
            lambda self, v: called.update(
                {'count': called['count'] + 1}),
        )

        win = MainWindow()
        qtbot.addWidget(win)
        win._maybe_replay_cached_update()

        assert called['count'] == 0

    def test_replay_fires_after_snooze_expires(
            self, qtbot, monkeypatch):
        from fam.ui.main_window import MainWindow
        from fam.utils.app_settings import set_setting
        from fam.utils.timezone import eastern_now

        set_setting('update_last_version', '999.0.0')
        set_setting('update_dismissed_version', '')
        # Snooze expired 5 minutes ago.
        set_setting(
            'update_remind_after',
            (eastern_now() - timedelta(minutes=5)).isoformat())

        called = {'count': 0}
        monkeypatch.setattr(
            MainWindow, '_show_update_available_popup',
            lambda self, v: called.update(
                {'count': called['count'] + 1}),
        )

        win = MainWindow()
        qtbot.addWidget(win)
        win._maybe_replay_cached_update()

        assert called['count'] == 1

    def test_replay_silent_when_no_cache(self, qtbot, monkeypatch):
        from fam.ui.main_window import MainWindow
        from fam.utils.app_settings import set_setting

        # Wipe any cached state.
        set_setting('update_last_version', '')
        set_setting('update_dismissed_version', '')
        set_setting('update_remind_after', '')

        called = {'count': 0}
        monkeypatch.setattr(
            MainWindow, '_show_update_available_popup',
            lambda self, v: called.update(
                {'count': called['count'] + 1}),
        )

        win = MainWindow()
        qtbot.addWidget(win)
        win._maybe_replay_cached_update()

        assert called['count'] == 0, (
            "First-ever launch (no cache) must not pop a stale "
            "popup")


# ════════════════════════════════════════════════════════════════════
# 2. Cooldown is 6 hours, not 24
# ════════════════════════════════════════════════════════════════════


class TestCooldownTuning:

    def test_cooldown_is_six_hours(self):
        from fam.ui.main_window import MainWindow
        assert MainWindow._AUTO_CHECK_COOLDOWN_HOURS == 6, (
            "Auto-check cooldown was lowered to 6 hours in v2.0.1 "
            "so users see the popup soon after a release goes up "
            "instead of waiting a full day")

    def test_snooze_is_six_hours(self):
        from fam.ui.main_window import MainWindow
        assert MainWindow._SNOOZE_HOURS == 6


# ════════════════════════════════════════════════════════════════════
# 3. OK button snoozes; Ignore button silences forever
# ════════════════════════════════════════════════════════════════════


class TestPopupButtonBehavior:
    """``_show_update_available_popup`` writes the right setting
    based on the user's button choice."""

    def test_ok_button_writes_snooze_timestamp(
            self, qtbot, monkeypatch):
        from fam.ui.main_window import MainWindow
        from fam.utils.app_settings import set_setting, get_setting
        from PySide6.QtWidgets import QMessageBox

        set_setting('update_remind_after', '')
        set_setting('update_dismissed_version', '')

        # Stub QMessageBox.information to return Ok.
        monkeypatch.setattr(
            QMessageBox, 'information',
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))

        win = MainWindow()
        qtbot.addWidget(win)
        win._show_update_available_popup('999.0.0')

        snoozed_until = get_setting('update_remind_after', '')
        assert snoozed_until, (
            "Clicking OK must set ``update_remind_after`` so the "
            "popup is suppressed for the snooze window")
        # And dismiss flag should NOT have been set.
        dismissed = get_setting('update_dismissed_version', '')
        assert dismissed != '999.0.0'

    def test_ignore_button_writes_dismissed_version(
            self, qtbot, monkeypatch):
        from fam.ui.main_window import MainWindow
        from fam.utils.app_settings import set_setting, get_setting
        from PySide6.QtWidgets import QMessageBox

        set_setting('update_remind_after', '')
        set_setting('update_dismissed_version', '')

        monkeypatch.setattr(
            QMessageBox, 'information',
            staticmethod(lambda *a, **k:
                          QMessageBox.StandardButton.Ignore))

        win = MainWindow()
        qtbot.addWidget(win)
        win._show_update_available_popup('999.0.0')

        dismissed = get_setting('update_dismissed_version', '')
        assert dismissed == '999.0.0', (
            "Clicking Ignore must permanently silence this exact "
            "version")
        # Snooze MUST be cleared so a future newer version isn't
        # accidentally suppressed by a stale snooze.
        snoozed = get_setting('update_remind_after', '')
        assert snoozed == '', (
            "Ignore must clear any prior snooze so a future newer "
            "version's popup isn't blocked by stale state")


# ════════════════════════════════════════════════════════════════════
# 4. System Status diagnostic exposes the auto-check state
# ════════════════════════════════════════════════════════════════════


class TestDiagnosticVisibility:
    """A coordinator triaging "the popup never fires" can read the
    System Status block and see exactly which gate is closed."""

    def test_status_exposes_auto_check_fields(self):
        from fam.help.system_status import collect_status
        s = collect_status()
        for key in (
                'auto_check_enabled',
                'auto_check_last',
                'auto_check_eligible_now',
                'auto_check_latest_known_remote',
                'auto_check_dismissed_version',
                'auto_check_snoozed_until'):
            assert key in s, f"System Status must expose {key!r}"

    def test_clipboard_text_includes_auto_check_block(self):
        from fam.help.system_status import (
            collect_status, format_status_for_clipboard,
        )
        text = format_status_for_clipboard(collect_status())
        assert 'Auto-check' in text
        assert 'Cooldown' in text
        assert 'Latest known' in text
        assert 'Dismissed version' in text
        assert 'Snoozed until' in text

    def test_eligible_now_true_when_no_prior_check(self):
        from fam.help.system_status import _safe_auto_check_state
        from fam.utils.app_settings import set_setting
        set_setting('update_last_check', '')
        s = _safe_auto_check_state()
        assert s['eligible_now'] is True

    def test_eligible_now_false_within_cooldown(self):
        from fam.help.system_status import _safe_auto_check_state
        from fam.utils.app_settings import set_setting
        from fam.utils.timezone import eastern_now
        # Last check was 1 hour ago — within 6h cooldown.
        recent = (eastern_now() - timedelta(hours=1)).isoformat()
        set_setting('update_last_check', recent)
        s = _safe_auto_check_state()
        assert s['eligible_now'] is False

    def test_eligible_now_true_past_cooldown(self):
        from fam.help.system_status import _safe_auto_check_state
        from fam.utils.app_settings import set_setting
        from fam.utils.timezone import eastern_now
        # Last check was 10 hours ago — well past 6h cooldown.
        old = (eastern_now() - timedelta(hours=10)).isoformat()
        set_setting('update_last_check', old)
        s = _safe_auto_check_state()
        assert s['eligible_now'] is True


# ════════════════════════════════════════════════════════════════════
# 5. The launch flow combines cache-replay + cooldown gate
# ════════════════════════════════════════════════════════════════════


class TestLaunchFlow:
    """``_auto_check_for_updates`` is called ~5s after launch.  It
    runs cache-replay first (no API), then a fresh check if the
    cooldown has expired."""

    def test_launch_replays_cache_even_within_cooldown(
            self, qtbot, monkeypatch):
        """The pre-v2.0.1 bug class: cooldown silences a known
        pending update for 24h.  Now the cache-replay path runs
        unconditionally on launch."""
        from fam.ui.main_window import MainWindow
        from fam.utils.app_settings import set_setting
        from fam.utils.timezone import eastern_now

        # Within 6h cooldown — fresh check would skip.
        set_setting(
            'update_last_check',
            (eastern_now() - timedelta(hours=1)).isoformat())
        set_setting('update_last_version', '999.0.0')
        set_setting('update_dismissed_version', '')
        set_setting('update_remind_after', '')
        set_setting('update_repo_url',
                     'https://github.com/seansaball/fam-market-manager')

        replayed = {'count': 0}
        monkeypatch.setattr(
            MainWindow, '_show_update_available_popup',
            lambda self, v: replayed.update(
                {'count': replayed['count'] + 1}),
        )
        # Ensure the network worker is NOT spun up — assert via patch.
        from fam.update import worker as worker_mod
        original_worker = worker_mod.UpdateCheckWorker
        worker_calls = {'count': 0}

        class _Counted(original_worker):
            def __init__(self, *a, **k):
                worker_calls['count'] += 1
                super().__init__(*a, **k)

        monkeypatch.setattr(
            worker_mod, 'UpdateCheckWorker', _Counted)

        win = MainWindow()
        qtbot.addWidget(win)
        win._auto_check_for_updates()

        assert replayed['count'] == 1, (
            "Cache-replay must fire on launch even when within "
            "cooldown — that's the whole point of the v2.0.1 fix")
        assert worker_calls['count'] == 0, (
            "Within-cooldown launch must NOT make a network call; "
            "the cached popup is sufficient")

    def test_launch_runs_fresh_check_past_cooldown(
            self, qtbot, monkeypatch):
        """Past the 6h cooldown, a fresh API call IS made (so we
        learn about new releases)."""
        from fam.ui.main_window import MainWindow
        from fam.utils.app_settings import set_setting
        from fam.utils.timezone import eastern_now

        set_setting(
            'update_last_check',
            (eastern_now() - timedelta(hours=10)).isoformat())
        set_setting('update_last_version', '')
        set_setting('update_repo_url',
                     'https://github.com/seansaball/fam-market-manager')

        from fam.update import worker as worker_mod
        worker_calls = {'count': 0}
        original_init = worker_mod.UpdateCheckWorker.__init__

        def _spy(self, *a, **k):
            worker_calls['count'] += 1
            original_init(self, *a, **k)

        monkeypatch.setattr(
            worker_mod.UpdateCheckWorker, '__init__', _spy)

        # Stub the worker's run method so no real network call happens.
        monkeypatch.setattr(
            worker_mod.UpdateCheckWorker, 'run',
            lambda self: None)

        win = MainWindow()
        qtbot.addWidget(win)
        win._auto_check_for_updates()

        assert worker_calls['count'] == 1, (
            "Past the 6h cooldown, the fresh-check worker must be "
            "instantiated")
