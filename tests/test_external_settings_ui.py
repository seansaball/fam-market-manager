"""Settings UI for external matching (ENH-002 P2, matrix row 4).

Covers the EditPaymentMethodDialog "External Matching" section
(toggle dependency, live payout preview fed by the SAME fields the
snapshot will capture, photo-dropdown gate), the G4 lint gate at
dialog-OK (HARD blocks, SOFT requires explicit confirm), and the
payment-methods table's External column incl. the G3 booth+external
config warning.
"""

import inspect

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def settings_db(tmp_path):
    db_file = str(tmp_path / "test_external_settings.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order, denomination, photo_required,"
        " external_matching_accepted, vendor_cashes_original)"
        " VALUES (2, 'FMNP', 100.0, 0, 2, 500, 'Optional', 1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order, denomination,"
        " external_matching_accepted, vendor_cashes_original)"
        " VALUES (3, 'Food RX', 100.0, 1, 3, 1000, 1, 0)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order)"
        " VALUES (5, 'SNAP', 100.0, 1, 1)")
    conn.commit()
    yield conn
    close_connection()


def _method(**overrides):
    base = {
        'id': 3, 'name': 'Food RX', 'match_percent': 100.0,
        'is_active': 1, 'sort_order': 3, 'denomination': 1000,
        'photo_required': None, 'is_system': 0,
        'external_matching_accepted': 0, 'vendor_cashes_original': 0,
    }
    base.update(overrides)
    return base


def _dialog(qtbot, **overrides):
    from fam.ui.settings_screen import EditPaymentMethodDialog
    dlg = EditPaymentMethodDialog(_method(**overrides))
    qtbot.addWidget(dlg)
    return dlg


# ──────────────────────────────────────────────────────────────────
# Dialog — toggles, preview, photo gate
# ──────────────────────────────────────────────────────────────────


class TestDialogExternalSection:

    def test_cashes_toggle_disabled_until_external_on(self, qtbot):
        dlg = _dialog(qtbot)
        assert not dlg.external_check.isChecked()
        assert not dlg.cashes_check.isEnabled()
        dlg.external_check.setChecked(True)
        assert dlg.cashes_check.isEnabled()
        dlg.external_check.setChecked(False)
        assert not dlg.cashes_check.isEnabled()

    def test_initialized_from_method_row(self, qtbot):
        dlg = _dialog(qtbot, external_matching_accepted=1,
                      vendor_cashes_original=1)
        assert dlg.external_check.isChecked()
        assert dlg.cashes_check.isChecked()
        assert dlg.cashes_check.isEnabled()

    def test_preview_hidden_when_external_off(self, qtbot):
        # isVisibleTo(dlg): effective visibility as-if the dialog
        # were shown — plain isVisible() is always False for
        # children of a never-shown dialog.
        dlg = _dialog(qtbot)
        assert not dlg.external_preview.isVisibleTo(dlg)

    def test_preview_face_plus_match(self, qtbot):
        """Food RX shape: $10 denom @ 100%, FAM collects → $20."""
        dlg = _dialog(qtbot, external_matching_accepted=1)
        assert dlg.external_preview.text() == (
            "For a $10.00 instrument FAM will owe the vendor: "
            "$20.00 — Face + match ($10.00 × 2.0)")

    def test_preview_match_only_fmnp_shape(self, qtbot):
        dlg = _dialog(qtbot, name='FMNP', denomination=500,
                      external_matching_accepted=1,
                      vendor_cashes_original=1)
        assert dlg.external_preview.text() == (
            "For a $5.00 instrument FAM will owe the vendor: "
            "$5.00 — Match only ($5.00 × 100%)")

    def test_preview_tracks_match_spin_live(self, qtbot):
        """The preview reads the dialog's CURRENT match % — the same
        inherited field the entry snapshot will capture."""
        dlg = _dialog(qtbot, external_matching_accepted=1)
        dlg.match_spin.setValue(50.0)
        assert "$15.00" in dlg.external_preview.text()
        assert "× 1.5" in dlg.external_preview.text()

    def test_preview_face_only_at_zero_match(self, qtbot):
        dlg = _dialog(qtbot, match_percent=0.0,
                      denomination=200,
                      external_matching_accepted=1)
        assert dlg.external_preview.text() == (
            "For a $2.00 instrument FAM will owe the vendor: "
            "$2.00 — Face only")

    def test_enabling_external_reveals_photo_dropdown(self, qtbot):
        """Photo requirements become configurable for any
        external-enabled method (was FMNP-only)."""
        dlg = _dialog(qtbot)
        assert not dlg.photo_required_combo.isVisibleTo(dlg)
        dlg.external_check.setChecked(True)
        assert dlg.photo_required_combo.isVisibleTo(dlg)


# ──────────────────────────────────────────────────────────────────
# Dialog — G4 lint gate on OK
# ──────────────────────────────────────────────────────────────────


class TestDialogLintGate:

    def test_hard_finding_blocks_accept(self, qtbot, monkeypatch):
        """External-enabled without a denomination must NOT save."""
        from PySide6.QtWidgets import QMessageBox
        warned = {}
        monkeypatch.setattr(
            QMessageBox, 'warning',
            staticmethod(lambda parent, title, text, *a, **kw:
                         warned.update(title=title, text=text)
                         or QMessageBox.Ok))
        dlg = _dialog(qtbot, denomination=None,
                      external_matching_accepted=1)
        dlg._validate_and_accept()
        assert 'denomination' in warned['text']
        assert dlg.result() != dlg.DialogCode.Accepted

    def test_soft_finding_no_backs_out(self, qtbot, monkeypatch):
        """Cashes-original @ 0% match (FAM owes $0) needs explicit
        confirmation; answering No keeps the dialog open."""
        from PySide6.QtWidgets import QMessageBox
        asked = {}
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda parent, title, text, *a, **kw:
                         asked.update(text=text)
                         or QMessageBox.StandardButton.No))
        dlg = _dialog(qtbot, match_percent=0.0,
                      external_matching_accepted=1,
                      vendor_cashes_original=1)
        dlg._validate_and_accept()
        assert '$0.00' in asked['text']
        assert dlg.result() != dlg.DialogCode.Accepted

    def test_soft_finding_yes_accepts(self, qtbot, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda *a, **kw:
                         QMessageBox.StandardButton.Yes))
        dlg = _dialog(qtbot, match_percent=0.0,
                      external_matching_accepted=1,
                      vendor_cashes_original=1)
        dlg._validate_and_accept()
        assert dlg.result() == dlg.DialogCode.Accepted

    def test_clean_config_accepts_silently(self, qtbot):
        dlg = _dialog(qtbot, external_matching_accepted=1)
        dlg._validate_and_accept()
        assert dlg.result() == dlg.DialogCode.Accepted


# ──────────────────────────────────────────────────────────────────
# _edit_pm wiring (source pin, house style) + table column
# ──────────────────────────────────────────────────────────────────


class TestEditPmWiring:

    def test_edit_pm_passes_both_toggles(self):
        from fam.ui.settings_screen import SettingsScreen
        src = inspect.getsource(SettingsScreen._edit_pm)
        assert 'external_matching_accepted=' in src
        assert 'vendor_cashes_original=' in src
        assert 'show_photo_required' in src


class TestPaymentMethodsTableExternalColumn:

    def _column_texts(self, screen):
        """{name: external-cell-text} from the rendered table."""
        out = {}
        for row in range(screen.pm_table.rowCount()):
            name = screen.pm_table.item(row, 1).text()
            out[name] = screen.pm_table.item(row, 4).text()
        return out

    def test_external_column_renders_shapes(self, qtbot, settings_db):
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        cells = self._column_texts(screen)
        # FMNP: external + cashes-original, booth-INACTIVE → no warn
        assert cells['FMNP'] == "✓ match only"
        # Food RX: external + booth-ACTIVE → G3 config warning
        assert cells['Food RX'] == "⚠ ✓ face + match"
        # SNAP: not external
        assert cells['SNAP'] == "—"

    def test_header_has_external_column(self, qtbot, settings_db):
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        headers = [screen.pm_table.horizontalHeaderItem(c).text()
                   for c in range(screen.pm_table.columnCount())]
        assert headers == ["ID", "Name", "Match %", "Denom.",
                           "External", "Active", "Actions"]

    def test_actions_still_render_in_last_column(self, qtbot,
                                                 settings_db):
        """The actions cell moved 5 → 6 with the new column — the
        buttons must land in the Actions column, not under Active."""
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        assert screen.pm_table.rowCount() > 0
        for row in range(screen.pm_table.rowCount()):
            assert screen.pm_table.cellWidget(row, 6) is not None
            assert screen.pm_table.cellWidget(row, 5) is None
