"""External Payments Entry screen (ENH-002 P3, matrix row 5).

Pins the generalized entry screen: method dropdown semantics (FMNP
default, sort_order fallback, no-methods lockout), G1 payout
confirmation content, G2 whole-multiple validation per selected
method, G5 large-amount emphasis, edit-mode method immutability +
snapshot-denomination validation, and the new Method / FAM Owes
table columns.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.models.fmnp import create_fmnp_entry
from fam.models.payment_method import update_payment_method


@pytest.fixture(autouse=True)
def screen_db(tmp_path):
    db_file = str(tmp_path / "test_external_screen.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit)"
        " VALUES (10, 'Bethel Park', 10000)")
    conn.execute(
        "INSERT INTO vendors (id, name, is_active)"
        " VALUES (20, 'Pitaland Inc.', 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status,"
        " opened_by) VALUES (41, 10, '2099-05-06', 'Open', 'T')")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order, denomination, photo_required,"
        " external_matching_accepted, vendor_cashes_original)"
        " VALUES (2, 'FMNP', 100.0, 0, 2, 500, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order, denomination,"
        " external_matching_accepted, vendor_cashes_original)"
        " VALUES (3, 'Food RX', 100.0, 1, 3, 1000, 1, 0)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order, denomination,"
        " external_matching_accepted, vendor_cashes_original)"
        " VALUES (4, 'JH Food Bucks', 0.0, 1, 4, 200, 1, 0)")
    conn.commit()
    yield conn
    close_connection()


def _screen(qtbot):
    from fam.ui.fmnp_screen import FMNPScreen
    screen = FMNPScreen()
    qtbot.addWidget(screen)
    return screen


def _pick_market_day(screen, md_id=41):
    for i in range(screen.md_combo.count()):
        if screen.md_combo.itemData(i) == md_id:
            screen.md_combo.setCurrentIndex(i)
            return
    raise AssertionError(f"market day {md_id} not in dropdown")


def _pick_method(screen, pm_id):
    idx = screen.method_combo.findData(pm_id)
    assert idx >= 0, f"method {pm_id} not in dropdown"
    screen.method_combo.setCurrentIndex(idx)


# ──────────────────────────────────────────────────────────────────
# Method dropdown semantics
# ──────────────────────────────────────────────────────────────────


class TestMethodDropdown:

    def test_lists_external_enabled_only_in_sort_order(self, qtbot):
        screen = _screen(qtbot)
        names = [screen.method_combo.itemText(i)
                 for i in range(screen.method_combo.count())]
        assert names == ['FMNP', 'Food RX', 'JH Food Bucks']

    def test_default_selection_is_fmnp(self, qtbot):
        """FMNP is the most common external method and the
        continuity choice — existing users land on a screen that
        behaves like the one they know."""
        screen = _screen(qtbot)
        assert screen.method_combo.currentText() == 'FMNP'

    def test_fallback_first_by_sort_order_when_fmnp_not_external(
            self, qtbot, screen_db):
        screen_db.execute(
            "UPDATE payment_methods SET external_matching_accepted=0"
            " WHERE name='FMNP'")
        screen_db.commit()
        screen = _screen(qtbot)
        assert screen.method_combo.currentText() == 'Food RX'

    def test_no_external_methods_disables_save_with_hint(
            self, qtbot, screen_db):
        screen_db.execute(
            "UPDATE payment_methods SET external_matching_accepted=0")
        screen_db.commit()
        screen = _screen(qtbot)
        _pick_market_day(screen)
        assert not screen.save_btn.isEnabled()
        assert 'Settings' in screen.save_btn.toolTip()
        assert not screen.no_methods_hint.isHidden()

    def test_denomination_follows_selected_method(self, qtbot):
        screen = _screen(qtbot)
        assert screen._fmnp_denomination == 500     # FMNP $5
        _pick_method(screen, 3)
        assert screen._fmnp_denomination == 1000    # Food RX $10
        _pick_method(screen, 4)
        assert screen._fmnp_denomination == 200     # Food Bucks $2


# ──────────────────────────────────────────────────────────────────
# Payout preview (G1 live line)
# ──────────────────────────────────────────────────────────────────


class TestPayoutPreview:

    def test_preview_face_plus_match(self, qtbot):
        screen = _screen(qtbot)
        _pick_method(screen, 3)
        screen.amount_spin.setValue(20.00)
        text = screen.payout_preview.text()
        assert "$40.00" in text
        assert "Face + match" in text
        assert "Pitaland Inc." in text

    def test_preview_match_only_for_fmnp(self, qtbot):
        screen = _screen(qtbot)
        screen.amount_spin.setValue(10.00)
        text = screen.payout_preview.text()
        assert "$10.00" in text
        assert "Match only" in text

    def test_preview_face_only_for_food_bucks(self, qtbot):
        screen = _screen(qtbot)
        _pick_method(screen, 4)
        screen.amount_spin.setValue(2.00)
        text = screen.payout_preview.text()
        assert "$2.00" in text
        assert "Face only" in text

    def test_preview_hidden_at_zero_amount(self, qtbot):
        screen = _screen(qtbot)
        screen.amount_spin.setValue(0)
        assert not screen.payout_preview.isVisibleTo(screen)


# ──────────────────────────────────────────────────────────────────
# Save path — G1 dialog content, G2 validation, G5 emphasis,
# method snapshot
# ──────────────────────────────────────────────────────────────────


def _fill_form(screen, amount, pm_id=3):
    _pick_market_day(screen)
    _pick_method(screen, pm_id)
    screen.amount_spin.setValue(amount)
    screen.entered_by_input.setText('Coordinator')


class TestSavePath:

    def test_save_records_selected_method_snapshot(self, qtbot,
                                                   screen_db):
        screen = _screen(qtbot)
        _fill_form(screen, 20.00, pm_id=3)
        screen._save_entry()
        row = screen_db.execute(
            "SELECT * FROM fmnp_entries").fetchone()
        assert row is not None
        assert row['payment_method_id'] == 3
        assert row['method_name_snapshot'] == 'Food RX'
        assert row['denomination_snapshot'] == 1000

    def test_g1_dialog_spells_out_the_money(self, qtbot,
                                            monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        captured = {}
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda parent, title, text, *a, **kw:
                         captured.update(title=title, text=text)
                         or QMessageBox.StandardButton.Yes))
        screen = _screen(qtbot)
        _fill_form(screen, 20.00, pm_id=3)
        screen._save_entry()
        assert "$40.00" in captured['title']
        assert "FAM will owe Pitaland Inc. $40.00" in captured['text']
        assert "Face value: $20.00" in captured['text']
        assert "Face + match ($20.00 × 2.0)" in captured['text']

    def test_g1_no_backs_out_without_saving(self, qtbot, screen_db,
                                            monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda *a, **kw:
                         QMessageBox.StandardButton.No))
        screen = _screen(qtbot)
        _fill_form(screen, 20.00, pm_id=3)
        screen._save_entry()
        assert screen_db.execute(
            "SELECT COUNT(*) FROM fmnp_entries").fetchone()[0] == 0

    def test_g5_large_payout_warning_in_dialog(self, qtbot,
                                               monkeypatch):
        """Default large-receipt threshold is $100; a $60 Food RX
        batch pays out $120 → the dialog must carry the warning."""
        from PySide6.QtWidgets import QMessageBox
        captured = {}
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda parent, title, text, *a, **kw:
                         captured.update(text=text)
                         or QMessageBox.StandardButton.Yes))
        screen = _screen(qtbot)
        _fill_form(screen, 60.00, pm_id=3)
        screen._save_entry()
        assert "LARGE AMOUNT" in captured['text']

    def test_g5_silent_below_threshold(self, qtbot, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        captured = {}
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda parent, title, text, *a, **kw:
                         captured.update(text=text)
                         or QMessageBox.StandardButton.Yes))
        screen = _screen(qtbot)
        _fill_form(screen, 20.00, pm_id=3)
        screen._save_entry()
        assert "LARGE AMOUNT" not in captured['text']

    def test_g2_multiple_validation_uses_selected_method(
            self, qtbot, screen_db):
        """$15 is a valid FMNP amount ($5 checks) but NOT a valid
        Food RX amount ($10 checks) — the guard follows the
        SELECTED method."""
        screen = _screen(qtbot)
        _fill_form(screen, 15.00, pm_id=3)
        screen._save_entry()
        assert screen_db.execute(
            "SELECT COUNT(*) FROM fmnp_entries").fetchone()[0] == 0
        assert screen.error_label.isVisibleTo(screen)
        assert 'Food RX' in screen.error_label.text()


# ──────────────────────────────────────────────────────────────────
# Edit mode — method immutability + snapshot denomination
# ──────────────────────────────────────────────────────────────────


class TestEditMode:

    def test_method_dropdown_locked_while_editing(self, qtbot):
        eid = create_fmnp_entry(41, 20, 2000, 'C',
                                payment_method_id=3)
        screen = _screen(qtbot)
        screen._edit_entry(eid)
        assert not screen.method_combo.isEnabled()
        assert screen.method_combo.currentData() == 3
        screen._cancel_edit()
        assert screen.method_combo.isEnabled()

    def test_edit_validates_against_denomination_snapshot(
            self, qtbot, screen_db):
        """The entry was recorded under a $10 denomination; the
        method later changed to $7.  Editing the old entry must
        validate against ITS snapshot ($10), not today's setting."""
        eid = create_fmnp_entry(41, 20, 2000, 'C',
                                payment_method_id=3)
        update_payment_method(3, denomination=700)
        screen = _screen(qtbot)
        # Saving (even an edit) requires a specific market day —
        # the pre-existing All-Market-Days guard.
        _pick_market_day(screen)
        screen._edit_entry(eid)
        assert screen._fmnp_denomination == 1000
        screen.amount_spin.setValue(30.00)  # 3000c: ×$10 ✓, ×$7 ✗
        screen.entered_by_input.setText('Coordinator')
        screen._save_entry()
        row = screen_db.execute(
            "SELECT amount FROM fmnp_entries WHERE id=?",
            (eid,)).fetchone()
        assert row['amount'] == 3000

    def test_editing_no_longer_external_method_shows_temp_item(
            self, qtbot):
        """An entry whose method was later external-disabled must
        still display truthfully during edit; the temp dropdown
        item disappears on cancel."""
        eid = create_fmnp_entry(41, 20, 2000, 'C',
                                payment_method_id=3)
        update_payment_method(3, external_matching_accepted=False)
        screen = _screen(qtbot)
        assert screen.method_combo.findData(3) < 0  # not listed
        screen._edit_entry(eid)
        assert screen.method_combo.currentData() == 3
        assert screen.method_combo.currentText() == 'Food RX'
        screen._cancel_edit()
        assert screen.method_combo.findData(3) < 0


# ──────────────────────────────────────────────────────────────────
# Entries table — Method + FAM Owes columns
# ──────────────────────────────────────────────────────────────────


class TestEntriesTable:

    def test_method_and_fam_owes_columns(self, qtbot):
        create_fmnp_entry(41, 20, 1000, 'C')                      # FMNP
        create_fmnp_entry(41, 20, 2000, 'C', payment_method_id=3)  # RX
        screen = _screen(qtbot)
        headers = [screen.table.horizontalHeaderItem(i).text()
                   for i in range(screen.table.columnCount())]
        assert 'Method' in headers
        assert 'FAM Owes' in headers
        m_col = headers.index('Method')
        owes_col = headers.index('FAM Owes')
        amount_col = headers.index('Amount')
        rows = {}
        for r in range(screen.table.rowCount()):
            rows[screen.table.item(r, m_col).text()] = (
                screen.table.item(r, amount_col).text(),
                screen.table.item(r, owes_col).text())
        # FMNP: $10 face → FAM owes $10 (the match).
        assert rows['FMNP'] == ('$10.00', '$10.00')
        # Food RX: $20 face → FAM owes $40 (face + match).
        assert rows['Food RX'] == ('$20.00', '$40.00')

    def test_fam_owes_uses_entry_snapshots_not_current_settings(
            self, qtbot):
        """Settings corrections never re-value history (EP1): the
        table's FAM Owes must come from the row's own snapshots."""
        create_fmnp_entry(41, 20, 2000, 'C', payment_method_id=3)
        update_payment_method(3, match_percent=0.0)
        screen = _screen(qtbot)
        headers = [screen.table.horizontalHeaderItem(i).text()
                   for i in range(screen.table.columnCount())]
        owes_col = headers.index('FAM Owes')
        assert screen.table.item(0, owes_col).text() == '$40.00'
