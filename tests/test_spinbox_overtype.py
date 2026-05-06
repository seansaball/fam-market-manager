"""Regression: payment-screen spinboxes should support overtype
(type-to-replace) and cents-builder shift-left behaviour so the user
can re-type numbers without manually deleting the existing digits.

User-reported (2026-04-30):

    "If you are trying to type a number, it only enters the single
     number in the space that you are in and you have to highlight
     the next number in order to overwrite it ...  Ideally you
     should be allowed to type normally and overwrite the next
     number in the space without needing to manually delete it."

Follow-up clarification (same day):

    "first keystroke: $1.00
     second keystroke: $1.10
     third keystroke: $1.11
     fourth keystroke (missing): $11.11"

Pinned behaviour (the canonical 7-keystroke ``11111111`` ladder
on a $-prefix decimals=2 spinbox starting at $0.00 with cursor
positioned right after the prefix):

    1st  selection-replace:  $0.00 → $1.00
    2nd  overtype on '.':    $1.00 → $1.10
    3rd  overtype on '0':    $1.10 → $1.11
    4th  shift-left:         $1.11 → $11.11
    5th  shift-left:         $11.11 → $111.11
    6th  shift-left:         $111.11 → $1111.11
    7th  shift-left:         $1111.11 → $11111.11

Implementation: two helpers in ``fam/ui/helpers.py`` —
``_try_overtype_next_char`` (handles keystrokes 2-3) and
``_shift_left_append`` (handles keystrokes 4+).  Selection-replace
(keystroke 1) keeps using the existing del_() path.

Backspace, Delete, arrows, Ctrl+A, paste — all behave normally;
only digit keys are intercepted.
"""
import pytest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest


# ──────────────────────────────────────────────────────────────────
# NoScrollDoubleSpinBox — the dollar-amount fields users mainly hit
# ──────────────────────────────────────────────────────────────────


class TestDoubleSpinBoxOvertype:

    @staticmethod
    def _make_spin(qtbot, value=0.0, prefix="$ "):
        from fam.ui.helpers import NoScrollDoubleSpinBox
        spin = NoScrollDoubleSpinBox()
        spin.setRange(0.0, 99999.99)
        spin.setDecimals(2)
        spin.setPrefix(prefix)
        spin.setValue(value)
        qtbot.addWidget(spin)
        spin.show()
        qtbot.waitExposed(spin)
        return spin

    def _press_digit_at(self, qtbot, spin, key, cursor_pos):
        """Place cursor at *cursor_pos* (no selection) then send *key*."""
        spin.setFocus()
        # The select-all-on-focus is QTimer.singleShot(0,...) — let it
        # fire, then we deselect and place the cursor where the test
        # wants it.
        QTest.qWait(20)
        spin.lineEdit().deselect()
        spin.lineEdit().setCursorPosition(cursor_pos)
        QTest.keyClick(spin, key)

    def test_overtype_replaces_digit_under_cursor(self, qtbot):
        """$ 1|23.45 + key '9' → $ 193.45 (the '2' got replaced)."""
        spin = self._make_spin(qtbot, value=123.45)
        # Text is "$ 123.45" — positions: $=0, ' '=1, 1=2, 2=3, 3=4,
        # .=5, 4=6, 5=7.  Cursor at 3 sits on the '2'.
        self._press_digit_at(qtbot, spin, Qt.Key_9, cursor_pos=3)
        assert spin.value() == 193.45, (
            f"Cursor on '2', typed '9' → expected 193.45, "
            f"got {spin.value()}.  Pre-fix this would have inserted "
            f"to give '$ 1923.45' (rounded to 1923.45).")

    def test_overtype_skips_decimal_point(self, qtbot):
        """$ 12|.34 + key '7' → $ 127.34 (skipped '.', replaced '3')."""
        spin = self._make_spin(qtbot, value=12.34)
        # "$ 12.34" — positions: $=0, ' '=1, 1=2, 2=3, .=4, 3=5, 4=6.
        # Cursor at 4 sits on '.'.  Overtype must skip past it and
        # eat the '3' so the typed '7' lands at position 5.
        self._press_digit_at(qtbot, spin, Qt.Key_7, cursor_pos=4)
        assert spin.value() == 12.74, (
            f"Cursor on '.', typed '7' → expected 12.74, "
            f"got {spin.value()}")

    def test_cursor_at_end_triggers_shift_left(self, qtbot):
        """Cursor past last digit → cents-builder shift-left.
        $5.00 + key '9' → $50.09 (5.00 * 10 + 0.09 = 50.09)."""
        spin = self._make_spin(qtbot, value=5.00)
        # Text "$ 5.00" length=6.  Cursor at 6 = end-of-text.
        self._press_digit_at(qtbot, spin, Qt.Key_9, cursor_pos=6)
        assert spin.value() == 50.09, (
            f"Cursor at end + key '9': expected shift-left to "
            f"50.09 (= 5.00 * 10 + 0.09), got {spin.value()}")

    def test_first_keystroke_after_focus_still_replaces_selection(
            self, qtbot):
        """The pre-existing select-all-on-focus path must keep
        working — typing a digit right after focus should replace
        the entire 0.00, NOT just overtype one char."""
        spin = self._make_spin(qtbot, value=0.0)
        spin.setFocus()
        QTest.qWait(50)  # let the QTimer.singleShot(0) selectAll fire
        # Don't deselect: we want to confirm the selected-text branch.
        assert spin.lineEdit().hasSelectedText(), (
            "Sanity: focus should have selected all text via "
            "the existing select-all-on-focus pattern")
        QTest.keyClick(spin, Qt.Key_5)
        assert spin.value() == 5.0, (
            f"Typing '5' on focused empty field → expected 5.00, "
            f"got {spin.value()}")

    def test_consecutive_digits_each_overtype(self, qtbot):
        """User types 5, 5, 5 starting at cursor pos 2 (right after
        '$ ' prefix).  Each '5' should replace the digit it lands on
        — '$ 0.00' → '$ 5.00' → '$ 5.50' → '$ 5.55'."""
        spin = self._make_spin(qtbot, value=0.0)
        spin.setFocus()
        QTest.qWait(20)
        spin.lineEdit().deselect()
        # First keystroke at pos 2 ('0' integer part).
        spin.lineEdit().setCursorPosition(2)
        QTest.keyClick(spin, Qt.Key_5)
        assert spin.value() == 5.0
        # After Qt's reformat, cursor should be at 3.  Confirm and
        # send another '5' that overwrites the decimal-side first 0.
        # (Cursor sitting on '.' → overtype skips '.' and eats the
        # next digit.)
        QTest.keyClick(spin, Qt.Key_5)
        assert spin.value() == 5.5, (
            f"After 5 then 5 → expected 5.50, got {spin.value()}")
        QTest.keyClick(spin, Qt.Key_5)
        assert spin.value() == 5.55, (
            f"After 5,5,5 → expected 5.55, got {spin.value()}")

    def test_user_canonical_7_keystroke_ladder(self, qtbot):
        """The exact pattern the user pinned in their follow-up:
        starting at $ 0.00 with cursor at pos 2 (right after '$ '),
        typing '1' seven times in a row should produce the ladder
        $1.00 → $1.10 → $1.11 → $11.11 → $111.11 → $1111.11 → $11111.11."""
        spin = self._make_spin(qtbot, value=0.0)
        spin.setFocus()
        QTest.qWait(20)
        spin.lineEdit().deselect()
        spin.lineEdit().setCursorPosition(2)

        expected = [1.00, 1.10, 1.11, 11.11, 111.11, 1111.11, 11111.11]
        for i, want in enumerate(expected, start=1):
            QTest.keyClick(spin, Qt.Key_1)
            assert spin.value() == want, (
                f"Keystroke {i}: expected ${want:.2f}, "
                f"got ${spin.value():.2f} (text={spin.lineEdit().text()!r})")

    def test_receipt_total_bug_typing_12_gives_1_20_not_10_02(
            self, qtbot):
        """Regression: receipt_total field uses
        ``setSpecialValueText`` which made Qt show ``$ 1`` (no
        ``.00`` suffix) after the first keystroke — leaving cursor
        at end and triggering shift-left on the *second* keystroke.

        User report (2026-04-30): "if I type 12 it some reason goes
        to 10.02 and skips two places".

        Pinned: typing ``1`` then ``2`` on a fresh receipt_total
        spinbox must produce $1.20 (matching the user's pinned
        ladder pattern), NOT $10.02 (the bug)."""
        from fam.ui.helpers import NoScrollDoubleSpinBox
        spin = NoScrollDoubleSpinBox()
        spin.setRange(0.00, 99999.99)
        spin.setDecimals(2)
        spin.setSingleStep(1.00)
        spin.setPrefix("$ ")
        spin.setValue(0.00)
        spin.setSpecialValueText("$ 0.00")  # ← THE TRIGGER
        qtbot.addWidget(spin)
        spin.show()
        qtbot.waitExposed(spin)
        spin.setFocus()
        QTest.qWait(50)  # let select-all fire

        QTest.keyClick(spin, Qt.Key_1)
        assert spin.value() == 1.0, (
            f"After 1st keystroke: expected $1.00, got "
            f"${spin.value():.2f}")
        QTest.keyClick(spin, Qt.Key_2)
        assert spin.value() == 1.20, (
            f"After 1,2 keystrokes: expected $1.20 (pinned ladder), "
            f"got ${spin.value():.2f}.  Pre-fix this was $10.02 "
            f"(the receipt_total bug).")

    def test_receipt_total_typing_1234_gives_12_34(self, qtbot):
        """Full 4-keystroke ladder on receipt_total: '1234' → $12.34."""
        from fam.ui.helpers import NoScrollDoubleSpinBox
        spin = NoScrollDoubleSpinBox()
        spin.setRange(0.00, 99999.99)
        spin.setDecimals(2)
        spin.setPrefix("$ ")
        spin.setValue(0.00)
        spin.setSpecialValueText("$ 0.00")
        qtbot.addWidget(spin)
        spin.show()
        qtbot.waitExposed(spin)
        spin.setFocus()
        QTest.qWait(50)

        for key in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4):
            QTest.keyClick(spin, key)
        assert spin.value() == 12.34, (
            f"Typing '1234' on receipt_total: expected $12.34, "
            f"got ${spin.value():.2f}")

    def test_shift_left_clamps_at_max(self, qtbot):
        """When shift-left would exceed ``maximum()``, the keystroke
        is silently absorbed (matches Qt's step-up clamp behaviour)."""
        spin = self._make_spin(qtbot, value=99999.99)
        # Already at max.  Cursor at end → next keystroke should be
        # absorbed without changing value.
        spin.setFocus()
        QTest.qWait(20)
        spin.lineEdit().deselect()
        spin.lineEdit().setCursorPosition(len(spin.lineEdit().text()))
        QTest.keyClick(spin, Qt.Key_5)
        assert spin.value() == 99999.99, (
            f"At max, extra keystrokes must be ignored; "
            f"got {spin.value()}")

    def test_backspace_unchanged(self, qtbot):
        """Overtype must NOT touch backspace — it should still
        delete-left as in vanilla Qt."""
        spin = self._make_spin(qtbot, value=12.34)
        spin.setFocus()
        QTest.qWait(20)
        spin.lineEdit().deselect()
        spin.lineEdit().setCursorPosition(3)  # right after '1'
        QTest.keyClick(spin, Qt.Key_Backspace)
        # '1' deleted → "$ 2.34" → 2.34.
        assert spin.value() == 2.34, (
            f"Backspace at pos 3 should delete '1' → 2.34, "
            f"got {spin.value()}")

    def test_non_digit_keys_dont_overtype(self, qtbot):
        """My overtype branch only fires for ``event.text() in
        '0123456789'`` — non-digit keys must not delete the next
        char.  Sanity-check with the literal '.' key (which has its
        own carve-out, but still must not overtype)."""
        spin = self._make_spin(qtbot, value=0.0)
        spin.setFocus()
        QTest.qWait(20)
        # Pre-condition: starting from focus, all selected.
        assert spin.lineEdit().hasSelectedText()
        # Type '.': the existing carve-out clears the selection so
        # super() can insert.  The result is implementation-defined
        # (Qt's QDoubleSpinBox may treat '.' specially), but the
        # value must remain in range and not crash.
        QTest.keyClick(spin, Qt.Key_Period)
        assert 0.0 <= spin.value() <= 99999.99


# ──────────────────────────────────────────────────────────────────
# NoScrollSpinBox — integer (used for the denomination stepper count)
# ──────────────────────────────────────────────────────────────────


class TestSpinBoxOvertype:

    @staticmethod
    def _make_spin(qtbot, value=0):
        from fam.ui.helpers import NoScrollSpinBox
        spin = NoScrollSpinBox()
        spin.setRange(0, 9999)
        spin.setValue(value)
        qtbot.addWidget(spin)
        spin.show()
        qtbot.waitExposed(spin)
        return spin

    def test_overtype_replaces_digit(self, qtbot):
        """123 with cursor on '2' + key '9' → 193."""
        spin = self._make_spin(qtbot, value=123)
        spin.setFocus()
        QTest.qWait(20)
        spin.lineEdit().deselect()
        spin.lineEdit().setCursorPosition(1)  # on '2'
        QTest.keyClick(spin, Qt.Key_9)
        assert spin.value() == 193, (
            f"Integer overtype: cursor on '2', typed '9' → expected "
            f"193, got {spin.value()}")

    def test_first_keystroke_replaces_selection(self, qtbot):
        spin = self._make_spin(qtbot, value=10)
        spin.setFocus()
        QTest.qWait(50)
        assert spin.lineEdit().hasSelectedText()
        QTest.keyClick(spin, Qt.Key_7)
        assert spin.value() == 7, (
            f"Selection-replace: typing '7' on focused 10 → expected "
            f"7, got {spin.value()}")

    def test_integer_shift_left_at_end_of_text(self, qtbot):
        """Integer spinbox: cursor at end + digit → multiply-by-ten
        and append.  ``5`` typed at end of ``5`` → ``55``."""
        spin = self._make_spin(qtbot, value=5)
        spin.setFocus()
        QTest.qWait(20)
        spin.lineEdit().deselect()
        spin.lineEdit().setCursorPosition(len(spin.lineEdit().text()))
        QTest.keyClick(spin, Qt.Key_5)
        assert spin.value() == 55, (
            f"Integer shift-left: 5 + digit '5' → expected 55, "
            f"got {spin.value()}")
        QTest.keyClick(spin, Qt.Key_5)
        assert spin.value() == 555, (
            f"Integer shift-left: 55 + digit '5' → expected 555, "
            f"got {spin.value()}")


# ──────────────────────────────────────────────────────────────────
# Regression: the helper itself — pure logic, no Qt event loop
# ──────────────────────────────────────────────────────────────────


class TestOvertypeHelper:
    """Pure-function tests for ``_overtype_eat_next_digit``.  These
    don't need the Qt event loop and run fast — they pin the precise
    branch logic so a future refactor can't silently regress."""

    @staticmethod
    def _make_line(text, cursor_pos, selection=None):
        from PySide6.QtWidgets import QLineEdit
        line = QLineEdit()
        line.setText(text)
        line.setCursorPosition(cursor_pos)
        if selection is not None:
            line.setSelection(*selection)
        return line

    def test_cursor_on_digit_deletes_it_returns_true(self, qtbot):
        from fam.ui.helpers import _try_overtype_next_char
        line = self._make_line("$ 123.45", 3)  # on '2'
        qtbot.addWidget(line)
        consumed = _try_overtype_next_char(line)
        assert consumed is True
        assert line.text() == "$ 13.45", (
            f"Should have deleted the '2' at cursor; "
            f"got {line.text()!r}")

    def test_cursor_on_decimal_skips_returns_true(self, qtbot):
        from fam.ui.helpers import _try_overtype_next_char
        line = self._make_line("$ 12.34", 4)  # on '.'
        qtbot.addWidget(line)
        consumed = _try_overtype_next_char(line)
        assert consumed is True
        # '.' kept, '3' (next digit) deleted.
        assert line.text() == "$ 12.4", (
            f"Should have skipped '.' and deleted '3'; "
            f"got {line.text()!r}")

    def test_cursor_on_prefix_space_returns_false(self, qtbot):
        """Non-digit non-'.' under cursor → caller falls back to
        shift-left.  Helper must signal this with ``False``."""
        from fam.ui.helpers import _try_overtype_next_char
        line = self._make_line("$ 5.00", 1)  # on the space of "$ "
        qtbot.addWidget(line)
        consumed = _try_overtype_next_char(line)
        assert consumed is False
        assert line.text() == "$ 5.00"  # untouched

    def test_cursor_at_end_returns_false(self, qtbot):
        """End-of-text → caller falls back to shift-left."""
        from fam.ui.helpers import _try_overtype_next_char
        line = self._make_line("$ 5.00", 6)  # past last digit
        qtbot.addWidget(line)
        consumed = _try_overtype_next_char(line)
        assert consumed is False
        assert line.text() == "$ 5.00"

    def test_selection_present_returns_false(self, qtbot):
        """Selection present → caller's del_() branch handles it;
        helper signals it didn't do anything via ``False``."""
        from fam.ui.helpers import _try_overtype_next_char
        line = self._make_line("$ 12.34", 0, selection=(2, 5))
        qtbot.addWidget(line)
        consumed = _try_overtype_next_char(line)
        assert consumed is False
        # Selection still in place; text unchanged.
        assert line.text() == "$ 12.34"
        assert line.hasSelectedText()

    def test_backward_compat_alias_present(self):
        """The original name ``_overtype_eat_next_digit`` is exported
        as an alias for any external import paths."""
        from fam.ui.helpers import (
            _overtype_eat_next_digit, _try_overtype_next_char,
        )
        assert _overtype_eat_next_digit is _try_overtype_next_char


# ──────────────────────────────────────────────────────────────────
# Pure-function test for the shift-left helper
# ──────────────────────────────────────────────────────────────────


class TestShiftLeftAppend:

    @staticmethod
    def _make_double_spin(qtbot, value, decimals=2, maximum=99999.99):
        from fam.ui.helpers import NoScrollDoubleSpinBox
        spin = NoScrollDoubleSpinBox()
        spin.setRange(0.0, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        qtbot.addWidget(spin)
        return spin

    @staticmethod
    def _make_int_spin(qtbot, value, maximum=9999):
        from fam.ui.helpers import NoScrollSpinBox
        spin = NoScrollSpinBox()
        spin.setRange(0, maximum)
        spin.setValue(value)
        qtbot.addWidget(spin)
        return spin

    def test_currency_decimals2_typical(self, qtbot):
        """1.11 + digit 1 → 11.11 (the user's canonical step)."""
        from fam.ui.helpers import _shift_left_append
        spin = self._make_double_spin(qtbot, 1.11)
        _shift_left_append(spin, 1)
        assert spin.value() == 11.11

    def test_decimals1_percent(self, qtbot):
        """5.0 + digit 0 → 50.0 (match-percent fields, decimals=1)."""
        from fam.ui.helpers import _shift_left_append
        spin = self._make_double_spin(qtbot, 5.0, decimals=1, maximum=999)
        _shift_left_append(spin, 0)
        assert spin.value() == 50.0

    def test_integer(self, qtbot):
        """5 + digit 5 → 55 (integer denomination-stepper count)."""
        from fam.ui.helpers import _shift_left_append
        spin = self._make_int_spin(qtbot, 5)
        _shift_left_append(spin, 5)
        assert spin.value() == 55

    def test_clamp_at_max_absorbs_keystroke(self, qtbot):
        """Result above maximum → no change."""
        from fam.ui.helpers import _shift_left_append
        spin = self._make_double_spin(qtbot, 99999.99)
        _shift_left_append(spin, 5)
        assert spin.value() == 99999.99  # unchanged

    def test_zero_starting_value(self, qtbot):
        """0.00 + digit 5 → 0.05 (cents-builder cold start)."""
        from fam.ui.helpers import _shift_left_append
        spin = self._make_double_spin(qtbot, 0.0)
        _shift_left_append(spin, 5)
        assert spin.value() == 0.05
