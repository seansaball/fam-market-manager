"""Tests for the v1.9.9 PaymentConfirmationDialog redesign.

The previous QMessageBox.question dialog was a wall of plain text;
the v1.9.9 redesign:

  * Visually separates *informative* / *actionable* / *warning* content
  * Wraps the action zone in a marching-ants animated border so
    volunteers can't miss what to do
  * Adds a REQUIRED checkbox per external-device method (SNAP/EBT)
    that the volunteer must tick before Confirm enables — forcing
    function so SNAP doesn't get auto-confirmed without first being
    processed at the EBT terminal

Pinned dimensions:
  1. External-device detection (case-insensitive substring on
     'snap' / 'ebt')
  2. Marching-ants frame: animation auto-starts; stopAnimation halts it
  3. Required-checkbox gate: Confirm disabled until every external-
     device row's checkbox is ticked
  4. Warning zone visible only when denom_overage > 0
  5. Source guard: PaymentScreen._confirm_payment uses the new
     dialog (not the old QMessageBox.question)
"""

import inspect

import pytest


# ══════════════════════════════════════════════════════════════════
# 1. Pure helpers — external-device + denominated detection
# ══════════════════════════════════════════════════════════════════
class TestMethodDetection:

    def test_snap_is_external_device(self):
        from fam.ui.widgets.payment_confirmation_dialog import (
            is_external_device_method,
        )
        assert is_external_device_method('SNAP') is True
        assert is_external_device_method('snap') is True
        assert is_external_device_method('Snap (EBT)') is True

    def test_ebt_is_external_device(self):
        from fam.ui.widgets.payment_confirmation_dialog import (
            is_external_device_method,
        )
        assert is_external_device_method('EBT') is True
        assert is_external_device_method('ebt') is True

    def test_cash_is_not_external_device(self):
        from fam.ui.widgets.payment_confirmation_dialog import (
            is_external_device_method,
        )
        assert is_external_device_method('Cash') is False
        assert is_external_device_method('FMNP') is False
        assert is_external_device_method('Food Bucks') is False

    def test_empty_or_none_safely_returns_false(self):
        from fam.ui.widgets.payment_confirmation_dialog import (
            is_external_device_method,
        )
        assert is_external_device_method('') is False
        assert is_external_device_method(None) is False

    def test_denominated_detection(self):
        from fam.ui.widgets.payment_confirmation_dialog import (
            is_denominated_method,
        )
        assert is_denominated_method(500) is True   # $5 token
        assert is_denominated_method(0) is False
        assert is_denominated_method(None) is False


# ══════════════════════════════════════════════════════════════════
# 2. MarchingAntsFrame animation lifecycle
# ══════════════════════════════════════════════════════════════════
class TestMarchingAntsFrame:

    def test_starts_animating_on_construction(self, qtbot):
        from fam.ui.widgets.payment_confirmation_dialog import (
            MarchingAntsFrame,
        )
        frame = MarchingAntsFrame()
        qtbot.addWidget(frame)
        assert frame.isAnimating() is True

    def test_stop_animation_halts_timer(self, qtbot):
        """``stopAnimation`` must both stop the QTimer and flip the
        ``isAnimating`` flag — without both, the border keeps
        flickering even after the volunteer confirms."""
        from fam.ui.widgets.payment_confirmation_dialog import (
            MarchingAntsFrame,
        )
        frame = MarchingAntsFrame()
        qtbot.addWidget(frame)
        frame.stopAnimation()
        assert frame.isAnimating() is False
        assert not frame._timer.isActive()

    def test_phase_advances_over_time(self, qtbot):
        """Sanity: the animation actually advances the phase
        between ticks.  Catches regressions where the timer fires
        but ``_advance`` is no-op'd."""
        from fam.ui.widgets.payment_confirmation_dialog import (
            MarchingAntsFrame,
        )
        frame = MarchingAntsFrame()
        qtbot.addWidget(frame)
        initial = frame._phase
        # Manually tick a few times rather than waiting on the
        # real timer (avoids flake under load).
        frame._advance()
        frame._advance()
        assert frame._phase != initial


# ══════════════════════════════════════════════════════════════════
# 3. Dialog behaviour — required checkboxes gate the Confirm button
# ══════════════════════════════════════════════════════════════════

def _build_dialog(qtbot, line_items, items, receipt_total=2000,
                  denom_overage=0, receipt_count=1):
    from fam.ui.widgets.payment_confirmation_dialog import (
        PaymentConfirmationDialog,
    )
    dlg = PaymentConfirmationDialog(
        line_items=line_items, items=items,
        receipt_total=receipt_total,
        denom_overage=denom_overage,
        receipt_count=receipt_count,
    )
    qtbot.addWidget(dlg)
    return dlg


class TestConfirmGate:
    """The Confirm button is the forcing function — it stays
    disabled until every external-device row's checkbox is ticked.
    Pin every state transition."""

    def test_no_external_methods_confirm_enabled_immediately(
            self, qtbot):
        """Cash-only or FMNP-only orders have no external-device
        rows, so Confirm should enable immediately on dialog open."""
        line_items = [
            {'method_amount': 2000, 'customer_charged': 2000,
             'match_amount': 0},
        ]
        items = [{'method_name_snapshot': 'Cash'}]
        dlg = _build_dialog(qtbot, line_items, items)
        assert dlg._confirm_btn.isEnabled() is True

    def test_snap_row_creates_required_checkbox(self, qtbot):
        line_items = [
            {'method_amount': 2000, 'customer_charged': 1000,
             'match_amount': 1000},
        ]
        items = [{'method_name_snapshot': 'SNAP'}]
        dlg = _build_dialog(qtbot, line_items, items)
        assert len(dlg._required_checkboxes) == 1, (
            "SNAP row must add exactly one required checkbox — "
            "without it, the EBT-terminal forcing function isn't "
            "enforced.")

    def test_confirm_disabled_until_snap_checkbox_ticked(self, qtbot):
        line_items = [
            {'method_amount': 2000, 'customer_charged': 1000,
             'match_amount': 1000},
        ]
        items = [{'method_name_snapshot': 'SNAP'}]
        dlg = _build_dialog(qtbot, line_items, items)
        # Initially: checkbox unticked → Confirm disabled.
        assert dlg._confirm_btn.isEnabled() is False
        # Tick → enables.
        dlg._required_checkboxes[0].setChecked(True)
        assert dlg._confirm_btn.isEnabled() is True
        # Untick → disables again.
        dlg._required_checkboxes[0].setChecked(False)
        assert dlg._confirm_btn.isEnabled() is False

    def test_multiple_snap_rows_all_required(self, qtbot):
        """If a customer somehow has two SNAP rows (e.g. split
        across vendors), both checkboxes must be ticked.  Pin
        that we don't shortcut after the first one."""
        line_items = [
            {'method_amount': 1000, 'customer_charged': 500,
             'match_amount': 500},
            {'method_amount': 1000, 'customer_charged': 500,
             'match_amount': 500},
        ]
        items = [
            {'method_name_snapshot': 'SNAP'},
            {'method_name_snapshot': 'SNAP'},
        ]
        dlg = _build_dialog(qtbot, line_items, items)
        assert len(dlg._required_checkboxes) == 2
        # Tick only the first.
        dlg._required_checkboxes[0].setChecked(True)
        assert dlg._confirm_btn.isEnabled() is False
        # Tick the second.
        dlg._required_checkboxes[1].setChecked(True)
        assert dlg._confirm_btn.isEnabled() is True

    def test_confirm_stops_marching_ants(self, qtbot):
        """Final visual cue when the volunteer hits Confirm — the
        marching-ants animation stops, signalling that the action
        is acknowledged."""
        line_items = [
            {'method_amount': 2000, 'customer_charged': 2000,
             'match_amount': 0},
        ]
        items = [{'method_name_snapshot': 'Cash'}]
        dlg = _build_dialog(qtbot, line_items, items)
        # Animation running before confirm.
        assert dlg._marching_ants_frames[0].isAnimating() is True
        dlg._on_confirm()
        # Stopped after confirm.
        assert dlg._marching_ants_frames[0].isAnimating() is False


class TestActionRowsBuilt:
    """Sanity that the right number of action rows / badges /
    checkboxes are constructed for various method mixes."""

    def test_zero_method_amount_row_skipped(self, qtbot):
        """Rows where method_amount == 0 must not appear in the
        dialog (they'd be confusing 'collect $0 via X' lines).
        Pin via the constructed checkbox count: a SNAP row with
        method_amount=0 should NOT add a required checkbox."""
        line_items = [
            {'method_amount': 0, 'customer_charged': 0,
             'match_amount': 0},
            {'method_amount': 2000, 'customer_charged': 2000,
             'match_amount': 0},
        ]
        items = [
            {'method_name_snapshot': 'SNAP'},   # zero — skip
            {'method_name_snapshot': 'Cash'},   # positive — show
        ]
        dlg = _build_dialog(qtbot, line_items, items)
        assert len(dlg._required_checkboxes) == 0, (
            "A method with method_amount=0 must NOT generate a "
            "required checkbox — those rows are skipped entirely.")

    def test_denominated_method_does_not_block_confirm(self, qtbot):
        """Denominated methods get an informational badge but NOT
        a required checkbox — the physical instruments either exist
        or they don't, no acknowledgement step needed."""
        line_items = [
            {'method_amount': 2000, 'customer_charged': 1000,
             'match_amount': 1000},
        ]
        items = [{
            'method_name_snapshot': 'JH Food Bucks',
            'denomination': 200,
        }]
        dlg = _build_dialog(qtbot, line_items, items)
        assert len(dlg._required_checkboxes) == 0
        assert dlg._confirm_btn.isEnabled() is True


# ══════════════════════════════════════════════════════════════════
# 4. Accessibility — minimum font-size floor for elderly volunteers
# ══════════════════════════════════════════════════════════════════
class TestAccessibilityFontSizes:
    """Multiple FAM market deployments are run by elderly volunteers
    who reported the original 10-12px auxiliary text was hard to
    scan.  v1.9.9 set a **12px floor** on every text element in this
    dialog, with the load-bearing labels (subtitle, FAM-match notes,
    informative footer, EBT-acknowledgement checkbox) bumped to
    13-14px.

    These tests pin those minimums against the dialog's actual
    rendered widgets so a future style refactor that drops a size
    below the floor regresses loudly instead of silently shipping
    illegible text to production."""

    # Hard floor — anything below this isn't readable for the
    # elderly user demographic.
    MIN_FONT_PX = 12

    def _font_size_of(self, widget) -> int:
        """Read the effective rendered font size from a widget.
        Uses Qt's resolved font (post-stylesheet application)
        rather than parsing the stylesheet text — that way the
        test catches both ``font-size:`` and ``setFont`` paths."""
        return widget.font().pointSizeF() and \
            int(widget.fontInfo().pixelSize()) or \
            int(widget.font().pixelSize())

    def _assert_at_least(self, widget, minimum_px: int, label: str):
        size = self._font_size_of(widget)
        assert size >= minimum_px, (
            f"{label} font size is {size}px — below the "
            f"accessibility floor of {minimum_px}px.  Elderly "
            f"volunteers reported readability issues at smaller "
            f"sizes; do not undersize this element.")

    def test_dialog_title_at_least_16px(self, qtbot):
        """The 18px dialog title is the largest text and sets the
        visual anchor.  Floor at 16px (16-18px is the typical
        QDialog title range)."""
        line_items = [{'method_amount': 2000,
                       'customer_charged': 2000, 'match_amount': 0}]
        items = [{'method_name_snapshot': 'Cash'}]
        dlg = _build_dialog(qtbot, line_items, items)
        # Find the title QLabel (first one in the layout).
        title = dlg.findChild(__import__('PySide6.QtWidgets',
                                         fromlist=['QLabel']).QLabel)
        assert title is not None
        self._assert_at_least(title, 16, 'Dialog title')

    def test_no_label_or_checkbox_below_12px(self, qtbot):
        """Sweep every QLabel and QCheckBox in the dialog and
        assert each is ≥ 12px.  This catches regressions on any
        text element — title, subtitle, action rows, badges,
        informative footer, EBT-acknowledgement checkbox."""
        from PySide6.QtWidgets import QLabel, QCheckBox
        line_items = [
            {'method_amount': 2000, 'customer_charged': 1000,
             'match_amount': 1000},
            {'method_amount': 1000, 'customer_charged': 1000,
             'match_amount': 0},
        ]
        items = [
            {'method_name_snapshot': 'SNAP'},
            {'method_name_snapshot': 'JH Food Bucks',
             'denomination': 200},
        ]
        # Include a denom_overage so the warning zone is present
        # in the sweep too — its title + body must clear the floor.
        dlg = _build_dialog(qtbot, line_items, items,
                            denom_overage=100)

        # Force Qt to apply pending stylesheets.
        dlg.ensurePolished()

        offenders = []
        for w in dlg.findChildren(QLabel) + dlg.findChildren(QCheckBox):
            text = w.text().strip()
            if not text:
                continue   # skip empty/spacer labels
            size = w.fontInfo().pixelSize()
            if size < self.MIN_FONT_PX:
                offenders.append((text[:60], size))
        assert not offenders, (
            f"Found {len(offenders)} text element(s) below the "
            f"{self.MIN_FONT_PX}px accessibility floor:\n"
            + "\n".join(f"  - {t!r} = {s}px" for t, s in offenders)
        )

    def test_external_device_badge_at_least_12px(self, qtbot):
        """The EXTERNAL DEVICE badge carries the load-bearing
        instruction ('process on EBT terminal first').  Pin its
        size as a dedicated test so the badge can't be silently
        miniaturised even if the floor sweep changes."""
        from PySide6.QtWidgets import QLabel
        line_items = [{'method_amount': 2000,
                       'customer_charged': 1000, 'match_amount': 1000}]
        items = [{'method_name_snapshot': 'SNAP'}]
        dlg = _build_dialog(qtbot, line_items, items)
        dlg.ensurePolished()

        # Find the badge by its distinctive text.
        badge = None
        for w in dlg.findChildren(QLabel):
            if 'EXTERNAL DEVICE' in w.text():
                badge = w
                break
        assert badge is not None, "EXTERNAL DEVICE badge missing"
        self._assert_at_least(badge, 12, 'EXTERNAL DEVICE badge')

    def test_ebt_acknowledgement_checkbox_at_least_13px(self, qtbot):
        """The EBT-acknowledgement checkbox label is the literal
        microcopy the volunteer must read before the Confirm
        button enables.  Floor at 13px (this is a forcing-function
        instruction, not casual auxiliary text)."""
        from PySide6.QtWidgets import QCheckBox
        line_items = [{'method_amount': 2000,
                       'customer_charged': 1000, 'match_amount': 1000}]
        items = [{'method_name_snapshot': 'SNAP'}]
        dlg = _build_dialog(qtbot, line_items, items)
        dlg.ensurePolished()

        cb = dlg.findChild(QCheckBox)
        assert cb is not None
        self._assert_at_least(cb, 13, 'EBT-acknowledgement checkbox')


# ══════════════════════════════════════════════════════════════════
# 4b. Layout contract (post-2026-04-29 UX feedback)
# ══════════════════════════════════════════════════════════════════
class TestActionRowLayout:
    """Coordinator feedback called out two specific layout issues:

      1. The amount was pushed to the FAR RIGHT of the row,
         disconnected visually from the method name on the left.
         Forces the eye to track across whitespace to read what
         is conceptually a single fact ("collect $X for METHOD").
         Fix: the method name and amount are now adjacent, with
         the format ``METHOD: $X.XX`` and a trailing stretch
         absorbing the rest of the row.

      2. The denomination-overage warning section had what
         appeared to be a nested border (an outer orange box plus
         an inner border around the title text).  Fix: the outer
         frame uses an ``objectName`` selector so its border is
         scoped to itself, and inner labels carry explicit
         ``border: none`` so cascading can't reintroduce the
         nesting.

    These tests pin both fixes structurally so a future stylesheet
    refactor can't silently regress them.
    """

    def test_method_name_and_amount_are_adjacent_in_layout(
            self, qtbot):
        """The amount QLabel must sit next to the name QLabel in
        the row's QHBoxLayout — NOT separated by an
        ``addStretch()`` (which is what pushed it to the right
        edge in the previous design).

        Use a uniquely-priced row ($7.50) so we can find the
        amount label by exact text match without colliding with
        the totals row.  The single-row dialog otherwise has both
        the row amount AND the TOTAL TO COLLECT amount equal —
        which would yield two QLabels with the same text and
        ambiguous matches.
        """
        from PySide6.QtWidgets import QLabel
        # Two rows so the row amounts ($7.50, $5.50) differ from
        # the TOTAL ($13.00) — eliminates ambiguity.
        line_items = [
            {'method_amount': 1500, 'customer_charged': 750,
             'match_amount': 750},   # SNAP $7.50
            {'method_amount': 1100, 'customer_charged': 550,
             'match_amount': 550},   # SNAP $5.50
        ]
        items = [
            {'method_name_snapshot': 'SNAP'},
            {'method_name_snapshot': 'SNAP'},
        ]
        dlg = _build_dialog(qtbot, line_items, items)

        # Find name labels (there are two — one per row).
        labels = dlg.findChildren(QLabel)
        name_labels = [w for w in labels if w.text() == 'SNAP:']
        assert len(name_labels) == 2, (
            f"Expected two 'SNAP:' name labels (one per row), "
            f"got {len(name_labels)}.")

        # For each name label, its row should contain a sibling
        # QLabel with the row's specific dollar amount.  The
        # parent widget of the name label IS the row's wrap — its
        # children are the bullet, name, amount, and optional
        # match-note + badge widgets.
        for name_label, expected_amount in zip(
                name_labels, ['$7.50', '$5.50']):
            row_wrap = name_label.parent()
            row_labels = row_wrap.findChildren(QLabel)
            row_texts = [w.text() for w in row_labels]
            assert expected_amount in row_texts, (
                f"Row containing name '{name_label.text()}' must "
                f"include the amount label '{expected_amount}' "
                f"as a sibling in the same row widget.  Found "
                f"labels: {row_texts}")

    def test_amount_does_not_use_far_right_alignment(self):
        """Source-level pin: the amount QLabel should NOT be
        AlignRight in the new layout (that was the symptom of the
        far-right disconnect).  A future refactor that re-adds
        ``setAlignment(Qt.AlignRight)`` to the amount label would
        regress the fix."""
        import inspect
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        src = inspect.getsource(
            PaymentConfirmationDialog._build_action_row)
        # Check for the specific old-layout fingerprint.
        assert 'amount.setAlignment(Qt.AlignRight' not in src, (
            "Action-row amount label must not use AlignRight — "
            "the post-2026-04-29 design pairs name+amount on the "
            "left edge.  The right-aligned amount was the exact "
            "thing coordinators called out.")

    def test_method_name_label_includes_trailing_colon(self):
        """The user explicitly asked for the 'METHOD: $X.XX'
        format.  Pin the colon so a future stylistic edit that
        drops it (or replaces with em-dash, etc.) requires an
        explicit decision."""
        import inspect
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        src = inspect.getsource(
            PaymentConfirmationDialog._build_action_row)
        assert "f\"{item['method_name']}:\"" in src, (
            "Method name label must be formatted with trailing "
            "colon — the coordinator-requested 'METHOD: $X.XX' "
            "pair format.")


class TestWarningSectionNoNestedBorder:
    """The denomination-overage warning had what looked like a
    nested border.  Pin the structural rules that prevent it."""

    def test_warning_frame_uses_objectname_scoped_border(self):
        """The outer warning frame's stylesheet must scope the
        border to a specific objectName — otherwise any nested
        QFrame would inherit the same border treatment."""
        import inspect
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        src = inspect.getsource(
            PaymentConfirmationDialog._build_warning_zone)
        # The objectName must be set BEFORE the stylesheet so the
        # selector finds a name to match.
        assert 'setObjectName("denomOverageWarning")' in src
        # The stylesheet selector must use the # objectName syntax.
        assert 'QFrame#denomOverageWarning' in src, (
            "Warning frame's border must be scoped via "
            "QFrame#objectName so it doesn't cascade to nested "
            "QFrames.  A bare ``QFrame { border: ... }`` would "
            "apply to every QFrame inside the dialog tree.")

    def test_warning_inner_labels_explicit_border_none(self):
        """Belt-and-suspenders: the inner title/body QLabels each
        explicitly declare ``border: none`` so even an accidental
        cascade from the parent stylesheet can't paint a border
        around them.  Without this, the visual nested-border bug
        is one stylesheet change away."""
        import inspect
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        src = inspect.getsource(
            PaymentConfirmationDialog._build_warning_zone)
        # Both the title and body labels carry the explicit
        # ``border: none`` declaration.
        assert src.count('border: none') >= 2, (
            f"Both inner QLabels in the warning zone must have "
            f"``border: none`` to prevent any cascading nested-"
            f"border treatment.  Found "
            f"{src.count('border: none')} occurrences; expected "
            f"at least 2.")


# ══════════════════════════════════════════════════════════════════
# 5. Source-level guard — PaymentScreen uses the new dialog
# ══════════════════════════════════════════════════════════════════
class TestPaymentScreenUsesNewDialog:

    def test_confirm_payment_uses_payment_confirmation_dialog(self):
        """Pin that the PaymentScreen confirm flow no longer relies
        on the old plain-text QMessageBox.question for the
        collect-from-customer prompt.  A regression that swaps it
        back would mean SNAP confirmations slip through without
        the EBT-terminal acknowledgement.

        NB: ``_confirm_payment`` legitimately uses
        ``QMessageBox.question`` for OTHER prompts (e.g. the post-
        save "return to Receipt Intake?" navigation question), so
        we narrow the regression check to the old prompt's
        distinctive text rather than blanket-banning the API."""
        import inspect
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._confirm_payment)
        assert 'PaymentConfirmationDialog' in src, (
            "_confirm_payment must instantiate "
            "PaymentConfirmationDialog — without it the SNAP "
            "forcing-function checkbox isn't enforced.")
        # The old prompt's distinctive opening phrase.  Pin its
        # absence in code (a comment that mentions it for context
        # is fine — that's a reference, not a re-introduction).
        old_prompt = 'Please confirm you have collected the following'
        assert old_prompt not in src, (
            "The old plain-text confirmation prompt has been "
            "re-introduced.  Use PaymentConfirmationDialog "
            "instead so SNAP rows trigger the required-checkbox "
            "forcing function.")
