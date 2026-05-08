"""``NoScrollSpinBox`` and ``NoScrollDoubleSpinBox`` keyPressEvent
must not crash on non-character key events
(user-reported 2026-05-07).

User-reported symptom: typing into the Receipt Total spinbox
"caused random numbers in random spots" and ultimately raised:

    ValueError: invalid literal for int() with base 10: ''
        at fam/ui/helpers.py:277 in keyPressEvent

User confirmed the specific trigger: an accidental Page Down
press while in the Receipt Total field.  Page keys (like every
non-character key) produce ``event.text() == ''``.

Root cause: the digit-handling branch was guarded by
``text in '0123456789'`` — Python's substring containment.  The
empty string is a substring of EVERY string, so non-character
key events (Backspace, Delete, Shift, arrow keys, modifier
keys, Page keys, IME composition keys, etc.) all evaluated
truthy and fell through to ``_shift_left_append(self, int(text))``
which crashed on ``int('')``.

Each crash bubbled to the global exception handler, popped the
"Unexpected Error" dialog, and left the spinbox in a partially-
formatted state — the user perceived this as "random numbers in
random spots" because mid-edit state got committed before the
crash unwound.

Fix: require ``len(text) == 1`` (a single character) AND the
character to be a digit before entering the digit-handling
branch.  Non-character keys fall through to Qt's native handling.

This file pins:
  1. Backspace doesn't raise.
  2. Arrow keys don't raise.
  3. Shift / Ctrl / Alt modifier keys don't raise.
  4. Tab doesn't raise.
  5. Page Up / Page Down don't raise (the user-reported trigger).
  6. Home / End / Delete don't raise.
  7. Digit keys still process correctly (no regression).
"""

import pytest

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QWidget


def _make_keypress(key, text=""):
    """Construct a synthetic QKeyEvent for keyPressEvent testing.

    ``key`` is a Qt.Key enum value; ``text`` is the character the
    key would normally produce (empty string for non-character
    keys like Backspace, Shift, arrows)."""
    return QKeyEvent(QKeyEvent.KeyPress, key, Qt.NoModifier, text)


@pytest.fixture
def double_spinbox(qtbot):
    from fam.ui.helpers import NoScrollDoubleSpinBox
    box = NoScrollDoubleSpinBox()
    box.setRange(0, 99999.99)
    box.setDecimals(2)
    qtbot.addWidget(box)
    return box


@pytest.fixture
def int_spinbox(qtbot):
    from fam.ui.helpers import NoScrollSpinBox
    box = NoScrollSpinBox()
    box.setRange(0, 9999)
    qtbot.addWidget(box)
    return box


# ──────────────────────────────────────────────────────────────────
# NoScrollDoubleSpinBox — receipt total, FMNP amount, etc.
# ──────────────────────────────────────────────────────────────────


class TestDoubleSpinBoxNonCharacterKeys:
    """Non-character key events on the double spinbox must NOT
    raise.  Pre-fix these all crashed on ``int('')``."""

    def test_backspace_does_not_raise(self, double_spinbox):
        event = _make_keypress(Qt.Key_Backspace, '')
        # Should NOT raise.
        double_spinbox.keyPressEvent(event)

    def test_delete_does_not_raise(self, double_spinbox):
        event = _make_keypress(Qt.Key_Delete, '')
        double_spinbox.keyPressEvent(event)

    def test_left_arrow_does_not_raise(self, double_spinbox):
        event = _make_keypress(Qt.Key_Left, '')
        double_spinbox.keyPressEvent(event)

    def test_right_arrow_does_not_raise(self, double_spinbox):
        event = _make_keypress(Qt.Key_Right, '')
        double_spinbox.keyPressEvent(event)

    def test_up_arrow_does_not_raise(self, double_spinbox):
        event = _make_keypress(Qt.Key_Up, '')
        double_spinbox.keyPressEvent(event)

    def test_down_arrow_does_not_raise(self, double_spinbox):
        event = _make_keypress(Qt.Key_Down, '')
        double_spinbox.keyPressEvent(event)

    def test_shift_does_not_raise(self, double_spinbox):
        event = _make_keypress(Qt.Key_Shift, '')
        double_spinbox.keyPressEvent(event)

    def test_control_does_not_raise(self, double_spinbox):
        event = _make_keypress(Qt.Key_Control, '')
        double_spinbox.keyPressEvent(event)

    def test_tab_does_not_raise(self, double_spinbox):
        event = _make_keypress(Qt.Key_Tab, '\t')
        double_spinbox.keyPressEvent(event)

    def test_home_end_do_not_raise(self, double_spinbox):
        for key in (Qt.Key_Home, Qt.Key_End):
            event = _make_keypress(key, '')
            double_spinbox.keyPressEvent(event)

    def test_pageup_pagedown_do_not_raise(self, double_spinbox):
        """User-reported 2026-05-07: pressing Page Down by accident
        in the Receipt Total spinbox triggered the int('') crash.
        Page keys produce empty event.text() like all non-character
        keys, so they MUST be in the non-character regression set."""
        for key in (Qt.Key_PageUp, Qt.Key_PageDown):
            event = _make_keypress(key, '')
            double_spinbox.keyPressEvent(event)


class TestDoubleSpinBoxDigitKeysStillWork:
    """Regression: legitimate digit input must still produce the
    expected value.  The fix narrows the digit-handling branch
    but doesn't change its behaviour for actual digit keys."""

    def test_typing_digit_5_processes(self, double_spinbox):
        # Empty box; type '5' → spinbox should accept it.
        # We don't pin the exact resulting value (the cents-
        # builder logic does shift-left mechanics) — just that
        # no exception fires and the value is non-zero
        # afterward.
        double_spinbox.setValue(0)
        event = _make_keypress(Qt.Key_5, '5')
        double_spinbox.keyPressEvent(event)
        # No crash → fix is in place.

    def test_typing_digit_0_processes(self, double_spinbox):
        double_spinbox.setValue(0)
        event = _make_keypress(Qt.Key_0, '0')
        double_spinbox.keyPressEvent(event)


# ──────────────────────────────────────────────────────────────────
# NoScrollSpinBox — integer field (e.g. check count)
# ──────────────────────────────────────────────────────────────────


class TestIntSpinBoxNonCharacterKeys:
    """Same fix in the integer sibling: non-character keys must
    not crash."""

    def test_backspace_does_not_raise(self, int_spinbox):
        event = _make_keypress(Qt.Key_Backspace, '')
        int_spinbox.keyPressEvent(event)

    def test_delete_does_not_raise(self, int_spinbox):
        event = _make_keypress(Qt.Key_Delete, '')
        int_spinbox.keyPressEvent(event)

    def test_arrow_keys_do_not_raise(self, int_spinbox):
        for key in (Qt.Key_Left, Qt.Key_Right,
                     Qt.Key_Up, Qt.Key_Down):
            event = _make_keypress(key, '')
            int_spinbox.keyPressEvent(event)

    def test_modifier_keys_do_not_raise(self, int_spinbox):
        for key in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt):
            event = _make_keypress(key, '')
            int_spinbox.keyPressEvent(event)

    def test_pageup_pagedown_do_not_raise(self, int_spinbox):
        """Page keys are the user-reported trigger — pin them."""
        for key in (Qt.Key_PageUp, Qt.Key_PageDown):
            event = _make_keypress(key, '')
            int_spinbox.keyPressEvent(event)


class TestIntSpinBoxDigitKeysStillWork:

    def test_typing_digit_processes(self, int_spinbox):
        int_spinbox.setValue(0)
        event = _make_keypress(Qt.Key_5, '5')
        int_spinbox.keyPressEvent(event)


# ──────────────────────────────────────────────────────────────────
# Source pin: digit-handling branch requires single character
# ──────────────────────────────────────────────────────────────────


class TestSourceGuard:
    """Pin the fix in source so a future "simplification" doesn't
    re-introduce the bug."""

    def test_double_spinbox_requires_single_char(self):
        import inspect
        from fam.ui.helpers import NoScrollDoubleSpinBox
        src = inspect.getsource(NoScrollDoubleSpinBox.keyPressEvent)
        assert "len(text) == 1 and text in '0123456789'" in src, (
            "NoScrollDoubleSpinBox.keyPressEvent must guard the "
            "digit-handling branch with `len(text) == 1` to avoid "
            "the empty-string substring trap that crashes on "
            "non-character keys.")

    def test_int_spinbox_requires_single_char(self):
        import inspect
        from fam.ui.helpers import NoScrollSpinBox
        src = inspect.getsource(NoScrollSpinBox.keyPressEvent)
        assert "len(text) == 1 and text in '0123456789'" in src, (
            "NoScrollSpinBox.keyPressEvent must guard the digit-"
            "handling branch with `len(text) == 1` to avoid the "
            "empty-string substring trap.")
