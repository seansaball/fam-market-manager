"""Tests for the animated 5-stage walkthrough on the Help screen.

These tests focus on **structural correctness and content accuracy**
rather than animation timing.  Qt's animation framework is well-tested
upstream; we verify that:

  * Every stage has the right metadata (title, role, narrative)
  * Stage 4 contains the corrected FMNP language (record-keeping only,
    FAM does NOT match, FAM does NOT collect check money, vendor cashes
    the check directly)
  * Stage 3 covers all three sub-steps the volunteer performs
  * Auto-advance / pause / restart logic flips the right state
  * The walkthrough is registered as the FIRST tab in HelpScreen
"""

from unittest.mock import MagicMock, patch

import pytest

from fam.ui.help_walkthrough import (
    STAGES, StageDef, WorkflowWalkthroughWidget,
    Stage1Scene, Stage2Scene, Stage3Scene, Stage4Scene, Stage5Scene,
    StageIndicator,
)


# ══════════════════════════════════════════════════════════════════
# STAGE DATA
# ══════════════════════════════════════════════════════════════════
class TestStageData:
    """The 5 stages must be present in the right order with non-empty
    content.  Catches accidental deletion / reordering."""

    def test_five_stages_present(self):
        assert len(STAGES) == 5

    def test_stages_numbered_one_through_five(self):
        nums = [s.number for s in STAGES]
        assert nums == [1, 2, 3, 4, 5]

    def test_every_stage_has_title(self):
        for s in STAGES:
            assert s.title and s.title.strip()

    def test_every_stage_has_role_label(self):
        for s in STAGES:
            assert s.role_label and s.role_label.strip()

    def test_every_stage_has_narrative_with_minimum_length(self):
        for s in STAGES:
            assert len(s.narrative) >= 100, \
                f"Stage {s.number} narrative looks like a stub: {s.narrative!r}"

    def test_every_stage_has_scene_factory(self):
        for s in STAGES:
            assert callable(s.scene_factory)

    def test_stages_have_no_auto_advance_duration(self):
        """v1.9.x removed auto-advance.  StageDef no longer carries a
        duration_ms field — each scene loops in place until the user
        clicks Next."""
        for s in STAGES:
            assert not hasattr(s, 'duration_ms') or s.duration_ms is None, \
                f"Stage {s.number} still has duration_ms — auto-advance " \
                f"was removed in favor of looping animation + Next-flash"


# ══════════════════════════════════════════════════════════════════
# CONTENT ACCURACY — the FAM-specific terminology
# ══════════════════════════════════════════════════════════════════
class TestStage1Content:
    """Stage 1 — customer shops at vendors, no payment yet."""

    def _stage(self):
        return STAGES[0]

    def test_stage_1_about_shopping(self):
        s = self._stage()
        assert s.number == 1
        # Mentions vendors and receipts (the two key concepts of stage 1)
        text = (s.title + ' ' + s.narrative).lower()
        assert 'vendor' in text
        assert 'receipt' in text

    def test_stage_1_emphasizes_no_payment_yet(self):
        """Stage 1 must convey that vendors hold orders and don't process
        payment — that's a key new-volunteer concept."""
        s = self._stage()
        text = s.narrative.lower()
        assert 'no payment' in text or "hold" in text, \
            "Stage 1 narrative must convey that vendors hold orders without payment"


class TestStage3Content:
    """Stage 3 is the volunteer's actual job — must cover all 3 steps."""

    def _stage(self):
        return STAGES[2]

    def test_stage_3_role_label_marks_volunteer(self):
        assert 'volunteer' in self._stage().role_label.lower()

    def test_stage_3_covers_receipt_entry(self):
        text = self._stage().narrative.lower()
        assert 'receipt' in text and 'enter' in text

    def test_stage_3_covers_payment_methods_with_snap_ebt_clarification(self):
        """The SNAP-via-secondary-EBT-terminal clarification is critical —
        a volunteer who thinks the app charges the EBT card itself would
        be very confused."""
        text = self._stage().narrative.lower()
        assert 'ebt' in text, \
            "Stage 3 narrative must mention the EBT terminal so volunteers " \
            "understand SNAP charging happens on a separate device"
        assert 'snap' in text

    def test_stage_3_covers_food_runner_path(self):
        text = self._stage().narrative.lower()
        assert 'runner' in text or 'food runner' in text

    def test_stage_3_covers_stamp_path(self):
        """The stamp fallback path is part of the standard workflow when
        runners aren't available — must not be skipped."""
        text = self._stage().narrative.lower()
        assert 'stamp' in text


class TestStage4Content:
    """Stage 4 has the most user-correction-prone content — the FMNP
    reimbursement language must be precise per the v1.9.x clarifications.

    The corrected model (v1.9.8+):
      - FAM does NOT add a match percent (the vendor already applied
        their match at the booth at 2x face value)
      - FAM DOES reimburse the face value of the check at end-of-month
      - The vendor cashes the original check directly with the FMNP
        program, so vendor + FAM together cover the matched value
      - FMNP (External) IS included in 'Total Due to Vendor'
    """

    def _stage(self):
        return STAGES[3]

    def test_stage_4_role_label_indicates_market_manager(self):
        assert 'manager' in self._stage().role_label.lower()
        assert 'volunteer' in self._stage().role_label.lower(), \
            "Role label should also call out 'usually not volunteers' so " \
            "the volunteer audience knows this isn't typically their step"

    def test_stage_4_says_fam_does_not_add_match_percent(self):
        """FAM does NOT apply a match percentage on top.  This is the
        critical clarification — the vendor already matched at the booth."""
        text = self._stage().narrative.lower()
        assert 'not' in text and 'match' in text, \
            "Stage 4 must clarify FAM does not add a match percent"
        # Specifically must NOT claim FAM doesn't reimburse anything
        assert 'does not collect the check money' not in text, \
            "Old language was wrong — FAM DOES reimburse face value. " \
            "The 'doesn't collect check money' phrasing is misleading."

    def test_stage_4_says_fam_reimburses_face_value(self):
        """FAM DOES reimburse the face value at end of month — the
        vendor needs to be made whole on the match they applied."""
        text = self._stage().narrative.lower()
        assert 'reimburse' in text, \
            "Stage 4 must state FAM reimburses the vendor"
        assert ('face value' in text or
                'check amount' in text or
                'the same' in text), \
            "Stage 4 must clarify the reimbursement is face value, not match"

    def test_stage_4_says_vendor_cashes_check(self):
        text = self._stage().narrative.lower()
        assert 'cash' in text and 'vendor' in text, \
            "Stage 4 must explain the vendor cashes the check themselves"

    def test_stage_4_says_vendor_applied_match_at_booth(self):
        """The vendor's match is applied at the booth, at 2x face value."""
        text = self._stage().narrative.lower()
        assert 'booth' in text, \
            "Stage 4 must mention the booth (where the vendor applied the match)"
        # The 2x / double mechanic should be mentioned
        assert ('double' in text or '$10' in text or
                'twice' in text or 'whole' in text), \
            "Stage 4 should convey the vendor's 2x match at the booth"

    def test_stage_4_mentions_fmnp_external_label(self):
        """Volunteers might see 'FMNP (External)' in reports later — the
        narrative should pre-explain that label."""
        text = self._stage().narrative
        assert 'FMNP (External)' in text or 'External' in text

    def test_stage_4_mentions_total_reimbursement_check(self):
        """Convey that the FMNP reimbursement is part of the same
        end-of-month check FAM cuts to the vendor — not a separate
        program flow.  Replaces the old 'paid through a different
        program mechanism' misconception."""
        text = self._stage().narrative.lower()
        assert ('total reimbursement' in text or
                'reimbursement check' in text or
                'included in the total' in text), \
            "Stage 4 should connect FMNP (External) to the regular " \
            "vendor reimbursement check"


class TestStage5Content:
    def _stage(self):
        return STAGES[4]

    def test_stage_5_covers_cloud_sync(self):
        text = self._stage().narrative.lower()
        assert 'sync' in text and ('cloud' in text or 'google' in text)

    def test_stage_5_covers_offline_csv_email_fallback(self):
        text = self._stage().narrative.lower()
        assert 'csv' in text and 'email' in text


# ══════════════════════════════════════════════════════════════════
# WALKTHROUGH WIDGET BEHAVIOR
# ══════════════════════════════════════════════════════════════════
class TestWorkflowWalkthroughWidget:
    """Behavior of the controller widget — auto-advance, pause, restart,
    skip-tour signal.  Tests use a mock widget rather than a full Qt
    instance to avoid needing a QApplication."""

    def _make_mock(self):
        widget = MagicMock(spec=WorkflowWalkthroughWidget)
        widget._current_index = 0
        widget._is_paused = False
        widget._has_played_once = False
        widget._scenes = [MagicMock() for _ in STAGES]
        widget._indicators = [MagicMock() for _ in STAGES]
        widget._stage_role = MagicMock()
        widget._stage_title = MagicMock()
        widget._narrative = MagicMock()
        widget._scene_stack = MagicMock()
        widget._prev_btn = MagicMock()
        widget._next_btn = MagicMock()
        widget._pause_btn = MagicMock()
        widget._flash_timer = MagicMock()
        widget._flash_timer.isActive.return_value = False
        widget._flash_visible = False
        widget._next_btn_default_style = ''
        widget._next_btn_flash_style = ''
        return widget

    def test_show_stage_resets_outgoing_scene(self):
        widget = self._make_mock()
        widget._current_index = 1
        WorkflowWalkthroughWidget._show_stage(widget, 2, autoplay=False)
        # Scene 1 (the outgoing scene) should have had reset() called
        widget._scenes[1].reset.assert_called_once()

    def test_show_stage_stops_flash_on_transition(self):
        """Switching stages must stop any active Next-button flash so
        the new stage starts clean."""
        widget = self._make_mock()
        WorkflowWalkthroughWidget._show_stage(widget, 2, autoplay=False)
        widget._stop_next_flash.assert_called()

    def test_show_stage_highlights_correct_indicator(self):
        widget = self._make_mock()
        WorkflowWalkthroughWidget._show_stage(widget, 2, autoplay=False)
        widget._indicators[2].setActive.assert_called_with(True)
        widget._indicators[0].setActive.assert_called_with(False)
        widget._indicators[4].setActive.assert_called_with(False)

    def test_show_stage_disables_prev_at_first(self):
        widget = self._make_mock()
        WorkflowWalkthroughWidget._show_stage(widget, 0, autoplay=False)
        widget._prev_btn.setEnabled.assert_called_with(False)
        widget._next_btn.setEnabled.assert_called_with(True)

    def test_show_stage_keeps_next_enabled_at_last_for_browse_handoff(self):
        """v1.9.8+ design refinement: on the final stage, Next no longer
        disables to a dead end — it transforms into a 'Tour complete ·
        Browse →' CTA that emits ``skip_requested`` to hand the user off
        to the Browse tab.  Prev still becomes enabled at the last stage."""
        widget = self._make_mock()
        WorkflowWalkthroughWidget._show_stage(widget, 4, autoplay=False)
        widget._prev_btn.setEnabled.assert_called_with(True)
        # Next stays enabled so the user can complete the tour.
        widget._next_btn.setEnabled.assert_called_with(True)
        # Label changes to a "tour complete" handoff cue.
        widget._next_btn.setText.assert_called()
        last_text = widget._next_btn.setText.call_args[0][0].lower()
        assert 'browse' in last_text or 'complete' in last_text, \
            "Next button on the final stage must label itself as a " \
            "completion / Browse handoff CTA — saw %r" % last_text

    def test_next_click_on_last_stage_emits_skip_requested(self):
        """v1.9.8+: Next on the last stage emits ``skip_requested`` so the
        Help screen switches to the Browse tab — no dead-end disabled state."""
        widget = self._make_mock()
        widget._current_index = len(STAGES) - 1
        widget._show_stage = MagicMock()
        widget.skip_requested = MagicMock()
        WorkflowWalkthroughWidget._on_next(widget)
        widget._stop_next_flash.assert_called()
        # Should NOT advance past the last stage…
        widget._show_stage.assert_not_called()
        # …but SHOULD emit skip_requested for the Browse handoff.
        widget.skip_requested.emit.assert_called_once()

    def test_show_stage_invalid_index_is_noop(self):
        widget = self._make_mock()
        WorkflowWalkthroughWidget._show_stage(widget, -1)
        WorkflowWalkthroughWidget._show_stage(widget, 99)
        widget._scene_stack.setCurrentIndex.assert_not_called()

    # ── Loop + flash behavior (replaces old auto-advance tests) ──

    def test_iteration_done_starts_flash_when_next_enabled(self):
        """When the active scene completes one loop iteration, the Next
        button should start flashing as a call-to-action."""
        widget = self._make_mock()
        widget._next_btn.isEnabled.return_value = True
        widget._is_paused = False
        widget._flash_timer.isActive.return_value = False
        WorkflowWalkthroughWidget._on_scene_iteration_done(widget)
        widget._start_next_flash.assert_called_once()

    def test_iteration_done_does_not_flash_on_last_stage(self):
        """Last stage has Next disabled — no point flashing."""
        widget = self._make_mock()
        widget._next_btn.isEnabled.return_value = False
        widget._is_paused = False
        WorkflowWalkthroughWidget._on_scene_iteration_done(widget)
        widget._start_next_flash.assert_not_called()

    def test_iteration_done_does_not_flash_when_paused(self):
        widget = self._make_mock()
        widget._next_btn.isEnabled.return_value = True
        widget._is_paused = True
        WorkflowWalkthroughWidget._on_scene_iteration_done(widget)
        widget._start_next_flash.assert_not_called()

    def test_iteration_done_idempotent_when_flash_already_running(self):
        """Subsequent loop iterations shouldn't re-start the flash if
        it's already running — that would reset the visible state."""
        widget = self._make_mock()
        widget._next_btn.isEnabled.return_value = True
        widget._is_paused = False
        widget._flash_timer.isActive.return_value = True
        WorkflowWalkthroughWidget._on_scene_iteration_done(widget)
        widget._start_next_flash.assert_not_called()

    def test_pause_stops_flash(self):
        widget = self._make_mock()
        widget._is_paused = False
        widget._current_index = 2
        WorkflowWalkthroughWidget._on_pause_toggle(widget)
        assert widget._is_paused is True
        widget._scenes[2].pause.assert_called_once()
        widget._stop_next_flash.assert_called()

    def test_pause_toggle_resumes_when_already_paused(self):
        widget = self._make_mock()
        widget._is_paused = True
        widget._current_index = 2
        WorkflowWalkthroughWidget._on_pause_toggle(widget)
        assert widget._is_paused is False
        widget._scenes[2].resume.assert_called_once()

    def test_next_click_stops_flash_and_advances(self):
        widget = self._make_mock()
        widget._current_index = 1
        widget._show_stage = MagicMock()
        WorkflowWalkthroughWidget._on_next(widget)
        widget._stop_next_flash.assert_called()
        widget._show_stage.assert_called_once_with(2)

    def test_prev_click_stops_flash(self):
        widget = self._make_mock()
        widget._current_index = 2
        widget._show_stage = MagicMock()
        WorkflowWalkthroughWidget._on_prev(widget)
        widget._stop_next_flash.assert_called()
        widget._show_stage.assert_called_once_with(1)

    def test_restart_stops_flash_and_unpauses(self):
        widget = self._make_mock()
        widget._is_paused = True
        widget._show_stage = MagicMock()
        WorkflowWalkthroughWidget._on_restart(widget)
        widget._stop_next_flash.assert_called()
        assert widget._is_paused is False
        widget._show_stage.assert_called_once_with(0)

    def test_indicator_click_stops_flash_and_jumps(self):
        widget = self._make_mock()
        widget._show_stage = MagicMock()
        WorkflowWalkthroughWidget._on_indicator_clicked(widget, 3)
        widget._stop_next_flash.assert_called()
        widget._show_stage.assert_called_once_with(2)

    def test_start_next_flash_starts_timer(self):
        widget = self._make_mock()
        WorkflowWalkthroughWidget._start_next_flash(widget)
        widget._flash_timer.start.assert_called_once()
        assert widget._flash_visible is False  # always start in default state

    def test_stop_next_flash_resets_button(self):
        widget = self._make_mock()
        widget._next_btn_default_style = 'DEFAULT_STYLE'
        WorkflowWalkthroughWidget._stop_next_flash(widget)
        widget._flash_timer.stop.assert_called_once()
        widget._next_btn.setStyleSheet.assert_called_with('DEFAULT_STYLE')

    def test_toggle_next_flash_alternates_styles(self):
        widget = self._make_mock()
        widget._flash_visible = False
        widget._next_btn_default_style = 'DEFAULT'
        widget._next_btn_flash_style = 'FLASH'

        WorkflowWalkthroughWidget._toggle_next_flash(widget)
        assert widget._flash_visible is True
        widget._next_btn.setStyleSheet.assert_called_with('FLASH')

        WorkflowWalkthroughWidget._toggle_next_flash(widget)
        assert widget._flash_visible is False
        widget._next_btn.setStyleSheet.assert_called_with('DEFAULT')

    def test_start_if_first_view_only_plays_once(self):
        widget = self._make_mock()
        widget._has_played_once = False
        widget._show_stage = MagicMock()

        WorkflowWalkthroughWidget.start_if_first_view(widget)
        widget._show_stage.assert_called_once_with(0, autoplay=True)
        assert widget._has_played_once is True

        widget._show_stage.reset_mock()
        WorkflowWalkthroughWidget.start_if_first_view(widget)
        widget._show_stage.assert_not_called()


# ══════════════════════════════════════════════════════════════════
# WALKTHROUGHSCENE LOOPING BEHAVIOR
# ══════════════════════════════════════════════════════════════════
class TestWalkthroughSceneLooping:
    """The scene base class wraps each animation pass in a self-restarting
    loop with a brief rest between iterations.  These tests verify the
    loop wiring without needing a QApplication event loop to run."""

    def test_scene_class_has_iteration_completed_signal(self):
        from fam.ui.help_walkthrough import WalkthroughScene
        assert hasattr(WalkthroughScene, 'iteration_completed')

    def test_loop_rest_constant_is_reasonable(self):
        """A 1-3 second rest between loops keeps the animation
        unobtrusive but engaging."""
        from fam.ui.help_walkthrough import _LOOP_REST_MS
        assert 500 <= _LOOP_REST_MS <= 5000

    def test_anim_finished_emits_iteration_completed(self, qtbot):
        """When the underlying animation finishes, the scene must emit
        iteration_completed and arm the rest timer."""
        from fam.ui.help_walkthrough import Stage1Scene
        scene = Stage1Scene()
        qtbot.addWidget(scene)
        with qtbot.waitSignal(scene.iteration_completed, timeout=1000):
            # Manually invoke the finished handler
            scene._on_anim_finished()

    def test_anim_finished_arms_rest_timer_when_loop_enabled(self, qtbot):
        from fam.ui.help_walkthrough import Stage1Scene
        scene = Stage1Scene()
        qtbot.addWidget(scene)
        scene._loop_enabled = True
        scene._on_anim_finished()
        assert scene._loop_rest_timer.isActive()

    def test_anim_finished_does_not_arm_rest_timer_when_loop_disabled(self, qtbot):
        from fam.ui.help_walkthrough import Stage1Scene
        scene = Stage1Scene()
        qtbot.addWidget(scene)
        scene._loop_enabled = False
        scene._on_anim_finished()
        assert not scene._loop_rest_timer.isActive()

    def test_pause_stops_rest_timer(self, qtbot):
        from fam.ui.help_walkthrough import Stage1Scene
        scene = Stage1Scene()
        qtbot.addWidget(scene)
        scene._loop_rest_timer.start(10000)
        scene.pause()
        assert not scene._loop_rest_timer.isActive()

    def test_reset_stops_rest_timer(self, qtbot):
        from fam.ui.help_walkthrough import Stage1Scene
        scene = Stage1Scene()
        qtbot.addWidget(scene)
        scene._loop_rest_timer.start(10000)
        scene.reset()
        assert not scene._loop_rest_timer.isActive()


# ══════════════════════════════════════════════════════════════════
# HELP SCREEN INTEGRATION
# ══════════════════════════════════════════════════════════════════
class TestHelpScreenIntegration:
    """Source-level guards that the Help screen registers Walkthrough as
    the FIRST tab and wires the skip_requested signal."""

    def test_help_screen_imports_walkthrough(self):
        import inspect
        import fam.ui.help_screen as hs
        src = inspect.getsource(hs)
        assert 'WorkflowWalkthroughWidget' in src

    def test_help_screen_registers_walkthrough_as_first_tab(self):
        import inspect
        import fam.ui.help_screen as hs
        src = inspect.getsource(hs)
        # The first addTab call inside _build_ui should be the walkthrough tab
        # (we use _build_walkthrough_tab() as the factory)
        assert 'self.tabs.addTab(self._build_walkthrough_tab(), "Walkthrough")' in src
        # And it must come before the Browse tab
        wt_idx = src.find('"Walkthrough"')
        br_idx = src.find('"Browse"')
        assert wt_idx > 0 and br_idx > 0
        assert wt_idx < br_idx, \
            "Walkthrough tab must be registered before Browse so it appears " \
            "as the first tab a new volunteer sees"

    def test_help_screen_wires_skip_requested(self):
        import inspect
        import fam.ui.help_screen as hs
        src = inspect.getsource(hs)
        assert 'skip_requested.connect' in src, \
            "Help screen must wire walkthrough.skip_requested to switch " \
            "to the Browse tab"

    def test_help_screen_starts_walkthrough_on_first_view(self):
        import inspect
        import fam.ui.help_screen as hs
        src = inspect.getsource(hs)
        assert 'start_if_first_view' in src, \
            "Help screen must call walkthrough.start_if_first_view() so " \
            "the animation auto-plays on first activation"


# ══════════════════════════════════════════════════════════════════
# MAIN WINDOW INTEGRATION (Help is in the sidebar)
# ══════════════════════════════════════════════════════════════════
class TestMainWindowHelpNav:
    """The Help screen must remain the 8th sidebar item.  Tested at
    source level since instantiating MainWindow needs full app setup."""

    def test_help_screen_in_main_window(self):
        import inspect
        import fam.ui.main_window as mw
        src = inspect.getsource(mw)
        assert 'self.help_screen = HelpScreen()' in src
        assert 'self.stack.addWidget(self.help_screen)' in src
        assert '("Help", 7)' in src
