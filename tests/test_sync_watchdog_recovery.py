"""Sync watchdog auto-recovers a stuck "Syncing..." button
(v2.0.7, 2026-05-07).

User-reported incident: the header showed "Last sync OK / Last sync:
10:35" alongside a permanently-disabled "Syncing..." button.  No
actual sync was in flight (thread had been cleaned up), but because
neither ``_on_sync_finished`` nor ``_on_sync_error`` had run, the
button stayed in the disabled state with no way for the volunteer
to recover (a disabled button can't be clicked to retry).

Failure modes that produce this state:

  * Exception between the button update (``self._sync_btn.setEnabled
    (False)``, ``setText("Syncing...")``) and ``thread.start()`` —
    the worker never runs so neither finished nor error fires.
  * Qt cross-thread signal-delivery hiccup that drops the worker's
    finished/error emit before it reaches the main thread.
  * Worker run() that exits via a code path neither emits — should
    be impossible per the worker's try/except shape, but defense in
    depth.

Fix: a 5-minute QTimer watchdog armed alongside the button update.
The happy path stops the watchdog in finished/error.  When the
watchdog fires it force-resets the button + indicator, freeing the
user to retry.  If the thread is genuinely still running (slow
sync, large photo upload) the watchdog extends one more cycle
instead of killing the in-flight work.

This file pins:

  1. **Watchdog wired up** — main window owns a single-shot QTimer
     with a 5-minute interval connected to a recovery handler.
  2. **Watchdog armed by ``_trigger_sync``** — the button-stuck
     window starts the moment the button flips to "Syncing...".
  3. **Watchdog cancelled on success** — ``_on_sync_finished``
     stops it.
  4. **Watchdog cancelled on error** — ``_on_sync_error`` stops it.
  5. **Watchdog cancelled in trigger-exception handler** — if
     ``_trigger_sync`` raises after the button was flipped, the
     except handler cancels the watchdog AND restores the button.
  6. **Recovery handler resets the button when no thread is alive**
     — the user-reported failure shape.
  7. **Recovery handler extends the watchdog when the thread IS
     alive** — protects against false-fires on legitimately slow
     syncs.
"""

import inspect


class TestWatchdogWiring:
    """Static source pins — the watchdog must exist with the right
    shape and be armed/disarmed from the right hooks."""

    def test_main_window_declares_sync_watchdog(self):
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow.__init__)
        assert '_sync_watchdog' in src, (
            "MainWindow must declare a `_sync_watchdog` QTimer for "
            "the user-reported stuck-Syncing-button recovery path.")
        assert 'setSingleShot(True)' in src, (
            "Watchdog must be single-shot — it should fire once "
            "and be re-armed by `_trigger_sync` for each new sync.")
        assert '5 * 60 * 1000' in src or '300000' in src, (
            "Watchdog interval must be 5 minutes (300000ms).  "
            "Shorter is too aggressive (false-fires on slow "
            "Sheets API), longer leaves the user stuck for too long.")

    def test_watchdog_connected_to_recovery_handler(self):
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow.__init__)
        assert '_on_sync_watchdog_fired' in src, (
            "Watchdog timeout signal must be connected to "
            "`_on_sync_watchdog_fired` so the recovery logic runs.")
        assert hasattr(MainWindow, '_on_sync_watchdog_fired'), (
            "MainWindow must define `_on_sync_watchdog_fired`.")

    def test_trigger_sync_arms_the_watchdog(self):
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._trigger_sync)
        assert 'self._sync_watchdog.start()' in src, (
            "`_trigger_sync` must arm the watchdog when it sets "
            "the button to 'Syncing...' — without this, the "
            "button-stuck recovery never fires.")

    def test_trigger_sync_exception_handler_resets_button_and_watchdog(self):
        """The user's exact failure mode: button flipped to
        Syncing... then an exception fired before the worker ran.
        The except handler must cancel the watchdog and restore
        the button so the user can retry immediately rather than
        wait the full 5 minutes for the watchdog."""
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._trigger_sync)
        assert 'self._sync_watchdog.stop()' in src, (
            "`_trigger_sync` exception handler must stop the "
            "watchdog AND restore the button so the user isn't "
            "stuck waiting 5 minutes after a synchronous failure.")
        # The except branch should also restore the button text
        # and enable it.
        assert 'setEnabled(True)' in src, (
            "Exception handler must call `setEnabled(True)` to "
            "make the button clickable again.")
        assert 'Sync to Cloud' in src, (
            "Exception handler must restore the original button "
            "text (`☁️  Sync to Cloud`).")

    def test_on_sync_finished_stops_watchdog(self):
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._on_sync_finished)
        assert 'self._sync_watchdog.stop()' in src, (
            "`_on_sync_finished` must stop the watchdog so it "
            "doesn't false-fire 5 min after a successful sync.")

    def test_on_sync_error_stops_watchdog(self):
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._on_sync_error)
        assert 'self._sync_watchdog.stop()' in src, (
            "`_on_sync_error` must stop the watchdog so it doesn't "
            "false-fire 5 min after a failed sync (the failure was "
            "already handled — no second recovery needed).")


class TestWatchdogRecoveryHandler:
    """Source pins on `_on_sync_watchdog_fired` — the actual
    recovery behaviour."""

    def test_recovery_handler_resets_button_when_no_thread(self):
        """The user-reported shape: thread has been cleaned up
        (``_sync_thread is None`` or not running) but the button
        is still in disabled "Syncing..." state."""
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._on_sync_watchdog_fired)
        assert 'setEnabled(True)' in src, (
            "Recovery handler must re-enable the sync button — "
            "that's the whole point of the watchdog.")
        assert 'Sync to Cloud' in src, (
            "Recovery handler must restore the original button "
            "text so the volunteer sees 'Sync to Cloud' instead "
            "of a perpetual 'Syncing...'.")

    def test_recovery_handler_extends_when_thread_still_alive(self):
        """A 5-minute sync is unusual but possible (large photo
        backlog after a long offline period, slow Sheets API).
        The watchdog must NOT kill an in-flight thread — it should
        extend one more cycle and check again."""
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._on_sync_watchdog_fired)
        assert 'isRunning()' in src, (
            "Recovery handler must check `_sync_thread.isRunning()` "
            "before force-resetting — protects legitimately slow "
            "syncs from being killed.")
        assert 'self._sync_watchdog.start()' in src, (
            "When the thread is still running, the watchdog must "
            "restart (re-arm for another cycle) rather than reset "
            "the button while a sync is genuinely in flight.")

    def test_recovery_handler_logs_warnings(self):
        """The recovery path must leave a breadcrumb so a coordinator
        triaging "the user reports the sync stuck" sees what
        happened."""
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._on_sync_watchdog_fired)
        assert 'logger.warning' in src, (
            "Recovery handler must `logger.warning(...)` so the "
            "stuck-state event reaches fam_manager.log and the "
            "Reports → Error Log surface.")
        # Both branches log: the genuinely-slow extension AND the
        # actual force-reset.
        assert src.count('logger.warning') >= 2, (
            "Both watchdog branches (extend-while-running and "
            "force-reset-when-stuck) must log a WARNING so post-"
            "mortem analysis can distinguish them.")

    def test_recovery_handler_uses_warning_indicator_not_error(self):
        """Honest indicator state: we don't know whether the
        sync actually wrote anything before going silent.  Calling
        the state 'Sync failed' would be a presumption.  'Sync
        recovered — please retry' or similar warning state is
        correct."""
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._on_sync_watchdog_fired)
        assert "_set_sync_indicator(\n            \"warning\"" in src or \
               "_set_sync_indicator('warning'" in src or \
               "'warning'" in src, (
            "Recovery handler must set indicator to 'warning' "
            "state, not 'error'.  We don't know if the sync wrote "
            "anything before going silent; an 'error' label would "
            "imply we know it failed.")


class TestWatchdogRuntimeRecovery:
    """Runtime smoke tests — actually call the recovery handler in
    the stuck state and verify the button comes back."""

    def test_force_reset_button_when_thread_is_none(self, qtbot):
        """The user-reported shape: ``_sync_thread is None`` (the
        thread was already cleaned up) but the button is still in
        disabled "Syncing..." state.  Calling the recovery handler
        must re-enable the button and reset its text."""
        from fam.ui.main_window import MainWindow

        win = MainWindow()
        qtbot.addWidget(win)

        # Simulate the stuck state: button shows "Syncing..." and
        # is disabled, but no thread is alive.
        win._sync_btn.setEnabled(False)
        win._sync_btn.setText("Syncing...")
        win._sync_thread = None
        win._sync_worker = None

        # Fire the watchdog handler.
        win._on_sync_watchdog_fired()

        # Button must be back to a clickable, recognisable state.
        assert win._sync_btn.isEnabled(), (
            "Watchdog must re-enable the sync button.")
        assert 'Sync to Cloud' in win._sync_btn.text(), (
            f"Watchdog must restore the original button text — "
            f"got {win._sync_btn.text()!r}")

    def test_extend_when_thread_still_running(self, qtbot, monkeypatch):
        """If the thread is genuinely still running (slow Sheets
        API, large photo upload), the watchdog must NOT kill it.
        Instead it restarts itself for one more cycle."""
        from fam.ui.main_window import MainWindow

        win = MainWindow()
        qtbot.addWidget(win)

        # Simulate an in-flight sync: button "Syncing..." disabled,
        # thread alive.
        win._sync_btn.setEnabled(False)
        win._sync_btn.setText("Syncing...")

        # Build a fake QThread-like object with isRunning()=True so
        # we don't need to actually start a real thread.  Includes
        # the methods MainWindow.closeEvent calls during teardown
        # (quit / wait) so the fixture cleanup doesn't AttributeError.
        class _FakeRunningThread:
            def __init__(self):
                self._still_running = True
            def isRunning(self):
                return self._still_running
            def quit(self):
                self._still_running = False
            def wait(self, *a, **k):
                return True
        win._sync_thread = _FakeRunningThread()

        watchdog_started = []
        monkeypatch.setattr(
            win._sync_watchdog, 'start',
            lambda: watchdog_started.append(True),
        )

        win._on_sync_watchdog_fired()

        assert watchdog_started == [True], (
            "Watchdog must restart itself when the thread is still "
            "running — got no restart call.")
        # Button should NOT have been reset (sync is still in
        # flight; killing it would be wrong).
        assert not win._sync_btn.isEnabled(), (
            "Watchdog must NOT re-enable the button when the "
            "thread is still genuinely running.")
        assert 'Syncing' in win._sync_btn.text(), (
            "Watchdog must NOT change the button text when the "
            "thread is still running.")

        # Defensive cleanup so the fixture teardown's closeEvent
        # path doesn't traffic with our fake.
        win._sync_thread = None

    def test_trigger_sync_failure_restores_button_immediately(
            self, qtbot, monkeypatch):
        """If ``_trigger_sync`` raises after the button is flipped
        to "Syncing...", the except handler must restore the
        button immediately rather than wait for the watchdog."""
        from fam.ui.main_window import MainWindow

        win = MainWindow()
        qtbot.addWidget(win)
        win._sync_btn.setVisible(True)  # bypass "no backend" hide

        # Make the backend's is_configured() raise to force
        # ``_trigger_sync`` to crash AFTER the button update —
        # mimics the user-reported failure mode.
        def _boom(*a, **k):
            raise RuntimeError("simulated sync trigger failure")

        # Patch a step DEEP in _trigger_sync so the button has
        # been set first.  ``backend.is_configured`` is called
        # before the button update — patch SyncManager instead
        # so the failure happens AFTER button update.
        import fam.sync.manager as mgr_module
        original_init = mgr_module.SyncManager.__init__

        def patched_init(self, backend):
            # Set button to test the post-flip path: simulate
            # arrival at the post-button-update code.
            raise RuntimeError("simulated post-button-update failure")

        monkeypatch.setattr(
            mgr_module.SyncManager, '__init__', patched_init)

        # Trigger should not raise; the except handler restores
        # the button.
        win._trigger_sync(force=True)

        assert win._sync_btn.isEnabled(), (
            "Trigger-sync exception handler must re-enable the "
            "button — without this the user is stuck for 5 min "
            "until the watchdog fires.")
        assert 'Sync to Cloud' in win._sync_btn.text(), (
            f"Trigger-sync exception handler must restore button "
            f"text — got {win._sync_btn.text()!r}")
        assert not win._sync_watchdog.isActive(), (
            "Trigger-sync exception handler must stop the watchdog "
            "(no point in armed timer if there's no live sync).")
