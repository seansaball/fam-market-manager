"""Standard (calculator-style) money typing — v2.1.0, ENH-007.

Replaces tests/test_spinbox_overtype.py, which pinned the
2026-04-30 "cents-builder ladder" (ATM-style: typing 8,5 produced
$8.50 and the decimal key was silently swallowed).  Field evidence
killed the ladder (Bryan's market, 2026-06-12): volunteers type
calculator-style, so "85" became $8.50 — perceived as "a random
extra 0 at the end" — and "85.5" became $8.55 (10× off).  Amounts
typed with exactly two decimals coincidentally worked, which made
the failures look random.

Pinned contract (every NoScroll spin box, hence every money field):

  1. Typing is NATIVE Qt — left-to-right, what-you-type-is-what-
     you-see.  "85" → $85; "85.5" → $85.50; "1234" → $1,234.00
     (the ladder gave $12.34).
  2. The decimal key inserts a REAL decimal point; a second "."
     and fraction digits beyond ``decimals()`` are rejected by
     Qt's validator.
  3. Select-all on focus → type-to-replace (tab in, type, the old
     value is gone).
  4. The widgets define NO custom keyPressEvent — that's the
     structural guarantee, pinned below, that neither the ladder
     nor its int('') crash class (2026-05-07) can return.
  5. Wheel-scroll still ignored (scroll-safety is independent of
     the typing model and survives).
"""

import pytest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest


def _make_money_spin(qtbot, *, special_value_text=None, range_min=0.00):
    from fam.ui.helpers import NoScrollDoubleSpinBox
    spin = NoScrollDoubleSpinBox()
    spin.setRange(range_min, 99999.99)
    spin.setDecimals(2)
    spin.setSingleStep(1.00)
    spin.setPrefix("$ ")
    spin.setValue(0.00)
    if special_value_text is not None:
        spin.setSpecialValueText(special_value_text)
    qtbot.addWidget(spin)
    spin.show()
    qtbot.waitExposed(spin)
    spin.setFocus()
    QTest.qWait(50)   # let select-all-on-focus fire
    return spin


def _type(spin, chars):
    for ch in chars:
        if ch == '.':
            QTest.keyClick(spin, Qt.Key_Period)
        else:
            QTest.keyClick(spin, getattr(Qt, f'Key_{ch}'))


def _typed_value(spin, chars):
    """Type *chars* and return the COMMITTED value.

    Keyboard tracking is off (ENH-007), so ``value()`` reflects the
    last committed state — commits happen on Tab / Enter /
    focus-out in the app (button clicks move focus first, so every
    real read path sees committed values).  ``interpretText()`` is
    Qt's public commit-now API and stands in for the focus change
    here."""
    _type(spin, chars)
    spin.interpretText()
    return spin.value()


class TestCalculatorStyleTyping:

    def test_whole_dollars_type_as_typed(self, qtbot):
        """THE field report: '85' must mean $85 — not $8.50 with a
        phantom trailing zero."""
        spin = _make_money_spin(qtbot)
        assert _typed_value(spin, '85') == 85.00

    def test_decimal_key_works(self, qtbot):
        """'85.5' → $85.50.  Under the ladder the '.' was silently
        swallowed and this produced $8.55 (10× off)."""
        spin = _make_money_spin(qtbot)
        assert _typed_value(spin, '85.5') == 85.50

    def test_two_decimal_amount(self, qtbot):
        spin = _make_money_spin(qtbot)
        assert _typed_value(spin, '25.00') == 25.00

    def test_four_digits_are_dollars(self, qtbot):
        """'1234' → $1,234 — the ladder turned this into $12.34."""
        spin = _make_money_spin(qtbot)
        assert _typed_value(spin, '1234') == 1234.00

    def test_leading_decimal(self, qtbot):
        spin = _make_money_spin(qtbot)
        assert _typed_value(spin, '.75') == 0.75

    def test_third_fraction_digit_rejected(self, qtbot):
        spin = _make_money_spin(qtbot)
        assert _typed_value(spin, '12.345') == 12.34

    def test_second_decimal_point_rejected(self, qtbot):
        spin = _make_money_spin(qtbot)
        assert _typed_value(spin, '8.5.5') == 8.55

    def test_type_to_replace_on_focus(self, qtbot):
        """Tab in with an existing value and type → fresh number."""
        spin = _make_money_spin(qtbot)
        spin.setValue(42.00)
        spin.clearFocus()
        QTest.qWait(20)
        spin.setFocus()
        QTest.qWait(50)   # select-all fires
        assert _typed_value(spin, '7') == 7.00

    def test_special_value_text_field(self, qtbot):
        """The Receipt Total configuration (setSpecialValueText) —
        the exact field from the report — types the same way."""
        spin = _make_money_spin(qtbot, special_value_text="$ 0.00")
        assert _typed_value(spin, '85') == 85.00

    def test_commit_formats_two_decimals(self, qtbot):
        spin = _make_money_spin(qtbot)
        _type(spin, '85')
        QTest.keyClick(spin, Qt.Key_Return)
        assert spin.value() == 85.00
        assert spin.text() == "$ 85.00"


class TestIntegerSpinTyping:

    def test_integer_field_types_as_typed(self, qtbot):
        from fam.ui.helpers import NoScrollSpinBox
        spin = NoScrollSpinBox()
        spin.setRange(0, 9999)
        qtbot.addWidget(spin)
        spin.show()
        qtbot.waitExposed(spin)
        spin.setFocus()
        QTest.qWait(50)
        _type(spin, '45')
        spin.interpretText()
        assert spin.value() == 45


@pytest.fixture
def payment_row_db(tmp_path):
    from fam.database.connection import (
        set_db_path, get_connection, close_connection)
    from fam.database.schema import initialize_database
    db_file = str(tmp_path / "typing_row.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (1, 'SNAP', 100.0, 1, 1, NULL)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        " payment_method_id) VALUES (1, 1)")
    conn.commit()
    yield conn
    close_connection()


class TestPaymentRowSmartFieldIntegration:
    """The payment screen's smart-field logic must survive the
    typing-model change (Sean's explicit condition on ENH-007).

    The contract (payment_row.py): the ⚡ lock (user_capped) flips
    on the FIRST keystroke via ``lineEdit().textEdited`` (user
    edits only — programmatic writes never emit it), while the
    engine recompute chain runs on COMMIT via ``valueChanged``
    (keyboard tracking off).  Programmatic engine writes go
    through ``_set_active_charge`` with signals blocked and must
    never lock.  Pinned here with REAL keystrokes (which no prior
    test ever simulated).
    """

    def _make_row(self, qtbot):
        from PySide6.QtTest import QTest as _QT
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        row.show()
        qtbot.waitExposed(row)
        for i in range(row.method_combo.count()):
            if row.method_combo.itemText(i) == 'SNAP':
                row.method_combo.setCurrentIndex(i)
                break
        _QT.qWait(20)
        return row

    def test_real_typing_locks_row_and_types_standard(
            self, qtbot, payment_row_db):
        row = self._make_row(qtbot)
        assert not row.is_user_capped()
        row.amount_spin.setFocus()
        QTest.qWait(50)   # select-all-on-focus
        QTest.keyClick(row.amount_spin, Qt.Key_8)
        assert row.is_user_capped(), (
            "FIRST real keystroke must flip the ⚡ lock "
            "(user_capped) — the smart-field contract, preserved "
            "via the lineEdit().textEdited hook now that "
            "valueChanged fires on commit (keyboard tracking off)")
        QTest.keyClick(row.amount_spin, Qt.Key_5)
        row.amount_spin.interpretText()   # commit, as focus-out would
        assert row.amount_spin.value() == 85.00, (
            "and the typed value must read standard: '85' = $85")
        assert row.is_user_capped()

    def test_engine_write_does_not_lock(self, qtbot, payment_row_db):
        """Programmatic cap-aware write-back must NOT lock the row
        — _set_active_charge blocks signals (payment_row.py)."""
        row = self._make_row(qtbot)
        row._set_active_charge(5000)   # engine-style write: $50
        assert not row.is_user_capped(), (
            "programmatic writes must never set user_capped")
        assert row.amount_spin.value() == 50.00

    def test_decimal_typing_locks_too(self, qtbot, payment_row_db):
        """The traced regression: with keyboard tracking ON, the
        row's per-keystroke recompute chain read .value() mid-edit,
        Qt re-rendered the editor ("$ 8.00" reappeared), the
        volunteer's '.' was rejected, and '8.5' became $85.
        Tracking-off means no handler runs mid-edit, so decimal
        typing in a payment row is exactly WYSIWYG."""
        row = self._make_row(qtbot)
        row.amount_spin.setFocus()
        QTest.qWait(50)
        for key in (Qt.Key_8, Qt.Key_Period, Qt.Key_5):
            QTest.keyClick(row.amount_spin, key)
        row.amount_spin.interpretText()   # commit, as focus-out would
        assert row.amount_spin.value() == 8.50
        assert row.is_user_capped()


class TestManualSelectionEditing:
    """Two same-day field reports (Sean, 2026-06-12):

    1. Highlight the full value → Backspace just jumped the cursor
       to the front; typing prepended.  Cause: manual Ctrl+A/drag
       selections include the "$ " prefix (the spinbox's own
       selectAll excludes it) and Qt refuses prefix-damaging edits.
    2. A first fix clamped the selection on selectionChanged — but
       correcting a selection DURING a drag resets Qt's drag
       anchor, so dragging left past the "$" flipped the highlight
       to the prefix alone and the prepend bug returned.

    Final model: the VISUAL selection stays fully native (the "$"
    may highlight, like any Qt app); the clamp runs from a KeyPress
    event filter immediately before the edit applies — so deleting
    and retyping always act on the value region, and no gesture is
    ever disturbed."""

    def test_selection_visuals_stay_native(self, qtbot):
        """Pin the timing fix: the selection itself must NOT be
        rewritten while selecting — fighting it mid-drag is what
        produced the $-only-highlight bug."""
        spin = _make_money_spin(qtbot)
        spin.setValue(25.00)
        line = spin.lineEdit()
        line.setSelection(0, len(line.text()))   # manual select-ALL
        assert line.selectionStart() == 0, (
            "the visual selection must stay native (clamping "
            "happens only at edit-keystroke time)")

    def test_highlight_then_backspace_deletes_value(self, qtbot):
        """Repro 1: highlight everything (prefix included), hit
        Backspace → the value must actually delete."""
        spin = _make_money_spin(qtbot)
        spin.setValue(25.00)
        line = spin.lineEdit()
        line.setSelection(0, len(line.text()))
        QTest.keyClick(spin, Qt.Key_Backspace)
        assert line.text() == spin.prefix(), (
            f"Backspace on a full highlight must clear the value — "
            f"text is {line.text()!r}")
        # And typing a fresh value afterwards works normally.
        assert _typed_value(spin, '7') == 7.00

    def test_highlight_then_type_replaces(self, qtbot):
        """Repro 2: highlight everything and type → the new digits
        must REPLACE the old value, not prepend to it.  This also
        covers the drag-left-past-the-$ flow: after the drag ends
        the selection spans prefix + value, exactly like here."""
        spin = _make_money_spin(qtbot)
        spin.setValue(25.00)
        line = spin.lineEdit()
        line.setSelection(0, len(line.text()))
        assert _typed_value(spin, '9') == 9.00, (
            "typing over a full highlight must replace, not prepend")

    def test_prefix_only_selection_is_safe(self, qtbot):
        """Selecting just the '$ ' and pressing Backspace must not
        mangle anything — the selection collapses to the value
        edge and the prefix survives."""
        spin = _make_money_spin(qtbot)
        spin.setValue(25.00)
        line = spin.lineEdit()
        line.setSelection(0, len(spin.prefix()))
        QTest.keyClick(spin, Qt.Key_Backspace)
        spin.interpretText()
        assert spin.value() == 25.00
        assert line.text().startswith(spin.prefix())

    def test_suffix_field_full_highlight_replaces(self, qtbot):
        """Percent-style fields (suffix) get the same treatment."""
        from fam.ui.helpers import NoScrollDoubleSpinBox
        spin = NoScrollDoubleSpinBox()
        spin.setRange(0, 999)
        spin.setDecimals(1)
        spin.setSuffix(" %")
        spin.setValue(50.0)
        qtbot.addWidget(spin)
        spin.show()
        qtbot.waitExposed(spin)
        line = spin.lineEdit()
        line.setSelection(0, len(line.text()))
        QTest.keyClick(spin, Qt.Key_7)
        spin.interpretText()
        assert spin.value() == 7.0, (
            "typing over a full highlight incl. suffix must replace")


class TestStructuralGuarantees:

    def test_no_custom_keypress_overrides(self):
        """The widgets must NOT define keyPressEvent.  Native Qt
        typing IS the contract — any custom digit branch is how
        both the cents-builder ladder and the int('') crash class
        (2026-05-07) got in.  If a future need arises, it must be
        designed against tests/test_money_typing_standard.py and
        the ENH-007 record in BUGS_BACKLOG.md."""
        from fam.ui.helpers import NoScrollSpinBox, NoScrollDoubleSpinBox
        assert 'keyPressEvent' not in NoScrollSpinBox.__dict__
        assert 'keyPressEvent' not in NoScrollDoubleSpinBox.__dict__

    def test_wheel_scroll_still_ignored(self, qtbot):
        """Scroll-safety is independent of the typing model and
        must survive the ladder removal."""
        from fam.ui.helpers import NoScrollDoubleSpinBox
        spin = NoScrollDoubleSpinBox()
        spin.setRange(0, 100)
        spin.setValue(50)
        qtbot.addWidget(spin)
        assert 'wheelEvent' in NoScrollDoubleSpinBox.__dict__
