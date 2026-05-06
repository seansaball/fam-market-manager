"""Activating FMNP from Settings → Payment Methods must surface a
confirmation dialog (v1.9.10 fix, regression-test in v2.0.6).

The dialog text steers the operator toward leaving FMNP inactive
unless explicitly instructed by a FAM rep — FAM doesn't currently
accept or cash physical FMNP checks, so in-line matching during
Receipt Intake / Payment is rarely the right call.

User-reported regression (2026-05-05): "we added a warning message
for marking FMNP as active and I never tested it, I just tried and
nothing happens, it seems to just mark it as active without an Are
you sure? dialog warning."

This test exercises the actual ``_toggle_pm`` code path with FMNP
to verify the dialog DOES fire, and that clicking No actually
backs out of the activation.
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fresh_settings_db(tmp_path, monkeypatch):
    from fam.database.connection import (
        set_db_path, close_connection, get_connection,
    )
    from fam.database.schema import initialize_database

    db_file = str(tmp_path / "fmnp_warning.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    # Seed FMNP method matching production seed:
    #   * is_active=0 (inactive by default per v1.9.8)
    #   * denomination=500 cents ($5)
    #   * sort_order=2 (after SNAP)
    conn.execute(
        "INSERT INTO payment_methods "
        " (name, match_percent, is_active, sort_order, "
        "  denomination, photo_required) "
        " VALUES ('FMNP', 100.0, 0, 2, 500, 'Optional')")
    conn.commit()
    yield conn
    close_connection()


def _get_fmnp_id(conn):
    return conn.execute(
        "SELECT id FROM payment_methods WHERE name = 'FMNP'"
    ).fetchone()['id']


class TestFMNPActivationWarning:
    """Activating FMNP fires the confirm dialog; deactivating does
    NOT (no warning needed for the safer state)."""

    def test_activating_fmnp_fires_confirm_dialog(
            self, qtbot, fresh_settings_db, monkeypatch):
        """The user-reported regression: clicking Activate on FMNP
        must show the confirmation dialog before flipping is_active."""
        from fam.ui.settings_screen import SettingsScreen
        from PySide6.QtWidgets import QMessageBox

        screen = SettingsScreen()
        qtbot.addWidget(screen)

        fmnp_id = _get_fmnp_id(fresh_settings_db)

        # Capture whether the dialog was shown and what its text was.
        dialog_shown = {'shown': False, 'text': '', 'title': ''}
        # Patch QMessageBox.exec at the INSTANCE level so the v1.9.10
        # ``QMessageBox(self)`` instance — not the static helpers —
        # is captured.  conftest's auto-stub stubs the static
        # warning/critical/etc., not instance .exec().
        original_exec = QMessageBox.exec

        def captured_exec(self):
            dialog_shown['shown'] = True
            dialog_shown['text'] = self.text() + ' ' + (
                self.informativeText() or '')
            dialog_shown['title'] = self.windowTitle()
            return QMessageBox.Yes  # user clicks Yes

        monkeypatch.setattr(QMessageBox, 'exec', captured_exec)

        # FMNP is currently inactive (is_active=0); calling _toggle_pm
        # with current_active=0 is the "about to activate" code path.
        screen._toggle_pm(fmnp_id, current_active=0)

        # Restore so other tests don't see the patch
        monkeypatch.setattr(QMessageBox, 'exec', original_exec)

        assert dialog_shown['shown'], (
            "Activating FMNP must show a confirmation dialog.  "
            "Pre-fix the warning code (settings_screen._toggle_pm) "
            "didn't fire — clicking Activate just flipped is_active "
            "to 1 silently, which is the user-reported regression.")

        assert 'FMNP' in dialog_shown['title']
        # Steer text mentions the safer alternative
        text = dialog_shown['text']
        assert 'inactive' in text.lower()
        # Dialog must reassure the operator that the dedicated FMNP
        # Entry screen is unaffected by this toggle.  The exact
        # phrasing has drifted between versions ("FMNP Entry screen"
        # vs "FMNP Check Entry screen") so accept either.
        assert ('fmnp entry screen' in text.lower()
                or 'fmnp check entry screen' in text.lower())

    def test_user_clicking_no_aborts_activation(
            self, qtbot, fresh_settings_db, monkeypatch):
        """If the operator clicks No on the warning, FMNP must
        remain INACTIVE (the activation was aborted)."""
        from fam.ui.settings_screen import SettingsScreen
        from PySide6.QtWidgets import QMessageBox

        screen = SettingsScreen()
        qtbot.addWidget(screen)
        fmnp_id = _get_fmnp_id(fresh_settings_db)

        # User clicks No on the warning dialog
        original_exec = QMessageBox.exec
        monkeypatch.setattr(
            QMessageBox, 'exec',
            lambda self: QMessageBox.No)

        screen._toggle_pm(fmnp_id, current_active=0)

        monkeypatch.setattr(QMessageBox, 'exec', original_exec)

        # FMNP must STILL be inactive
        active = fresh_settings_db.execute(
            "SELECT is_active FROM payment_methods WHERE id = ?",
            (fmnp_id,)
        ).fetchone()['is_active']
        assert active == 0, (
            "Clicking No on the FMNP-activation warning must abort "
            "the toggle.  FMNP stayed inactive in the DB?  Got "
            f"is_active={active} (expected 0).")

    def test_user_clicking_yes_completes_activation(
            self, qtbot, fresh_settings_db, monkeypatch):
        """The happy path: user reads the warning, decides to
        proceed, FMNP flips to active."""
        from fam.ui.settings_screen import SettingsScreen
        from PySide6.QtWidgets import QMessageBox

        screen = SettingsScreen()
        qtbot.addWidget(screen)
        fmnp_id = _get_fmnp_id(fresh_settings_db)

        original_exec = QMessageBox.exec
        monkeypatch.setattr(
            QMessageBox, 'exec',
            lambda self: QMessageBox.Yes)

        screen._toggle_pm(fmnp_id, current_active=0)

        monkeypatch.setattr(QMessageBox, 'exec', original_exec)

        active = fresh_settings_db.execute(
            "SELECT is_active FROM payment_methods WHERE id = ?",
            (fmnp_id,)
        ).fetchone()['is_active']
        assert active == 1

    def test_deactivating_fmnp_does_NOT_fire_warning(
            self, qtbot, fresh_settings_db, monkeypatch):
        """Going from active → inactive is the SAFER state (matches
        the v1.9.8 default).  No confirmation needed."""
        from fam.ui.settings_screen import SettingsScreen
        from PySide6.QtWidgets import QMessageBox

        # Set FMNP to active first
        fresh_settings_db.execute(
            "UPDATE payment_methods SET is_active = 1 "
            " WHERE name = 'FMNP'")
        fresh_settings_db.commit()
        fmnp_id = _get_fmnp_id(fresh_settings_db)

        screen = SettingsScreen()
        qtbot.addWidget(screen)

        dialog_count = {'n': 0}
        original_exec = QMessageBox.exec
        def captured(self):
            dialog_count['n'] += 1
            return QMessageBox.Yes
        monkeypatch.setattr(QMessageBox, 'exec', captured)

        # current_active=1 (currently active) → flip to inactive
        screen._toggle_pm(fmnp_id, current_active=1)

        monkeypatch.setattr(QMessageBox, 'exec', original_exec)

        assert dialog_count['n'] == 0, (
            "Deactivating FMNP must NOT show a confirmation dialog "
            "(deactivation is the safer state).  Got "
            f"{dialog_count['n']} dialog(s) shown.")

        # And FMNP is now inactive
        active = fresh_settings_db.execute(
            "SELECT is_active FROM payment_methods WHERE id = ?",
            (fmnp_id,)
        ).fetchone()['is_active']
        assert active == 0


class TestFMNPMarketAssignmentWarning:
    """v2.0.6: the same FMNP warning fires when CHECKING the FMNP
    box in Settings → Markets → 'Assign Payment Methods to: [Market]'.
    Pre-fix the operator could silently add FMNP to a market with
    no confirmation, defeating the parent v1.9.10 guard."""

    def test_assigning_fmnp_to_market_fires_warning(
            self, qtbot, fresh_settings_db, monkeypatch):
        """End-to-end: open the AssignPaymentMethodsDialog, simulate
        the user CHECKING the FMNP box, click OK, and confirm the
        warning dialog fires before the assignment lands."""
        from fam.ui.settings_screen import (
            SettingsScreen, AssignPaymentMethodsDialog,
        )
        from PySide6.QtWidgets import QMessageBox, QDialog

        # Create a market that does NOT have FMNP yet
        fresh_settings_db.execute(
            "INSERT INTO markets "
            " (id, name, daily_match_limit) "
            " VALUES (700, 'Bethel Park', 10000)")
        fresh_settings_db.commit()

        screen = SettingsScreen()
        qtbot.addWidget(screen)

        fmnp_id = _get_fmnp_id(fresh_settings_db)

        # Stub the AssignPaymentMethodsDialog: skip the dialog UI and
        # return Accepted with FMNP checked.
        monkeypatch.setattr(
            AssignPaymentMethodsDialog, 'exec',
            lambda self: QDialog.Accepted)
        monkeypatch.setattr(
            AssignPaymentMethodsDialog, 'get_checked_payment_method_ids',
            lambda self: {fmnp_id})

        # Capture whether the FMNP confirmation dialog was shown
        # AND track what the operator clicked.
        dialog_calls = {'count': 0}
        def captured_exec(self):
            dialog_calls['count'] += 1
            dialog_calls['title'] = self.windowTitle()
            return QMessageBox.Yes  # operator clicks Yes
        monkeypatch.setattr(QMessageBox, 'exec', captured_exec)

        screen._assign_payment_methods(700)

        # The warning dialog must have fired exactly once
        assert dialog_calls['count'] == 1, (
            "Adding FMNP to a market via the assignment dialog must "
            "show the FMNP-specific warning once.  Pre-fix the path "
            "silently assigned without confirmation.")
        assert 'FMNP' in dialog_calls['title']

        # FMNP is now assigned to the market (user clicked Yes)
        assigned = fresh_settings_db.execute(
            "SELECT 1 FROM market_payment_methods "
            " WHERE market_id = 700 AND payment_method_id = ?",
            (fmnp_id,)
        ).fetchone()
        assert assigned is not None

    def test_clicking_no_aborts_fmnp_assignment(
            self, qtbot, fresh_settings_db, monkeypatch):
        """Clicking No on the warning must drop FMNP from the
        additions — but other checked methods still get assigned."""
        from fam.ui.settings_screen import (
            SettingsScreen, AssignPaymentMethodsDialog,
        )
        from PySide6.QtWidgets import QMessageBox, QDialog

        # Set up: market with no methods, plus a non-FMNP method
        fresh_settings_db.execute(
            "INSERT INTO markets "
            " (id, name, daily_match_limit) "
            " VALUES (701, 'Bellevue', 10000)")
        fresh_settings_db.execute(
            "INSERT INTO payment_methods "
            " (id, name, match_percent, is_active, sort_order) "
            " VALUES (300, 'Cash', 0.0, 1, 6)")
        fresh_settings_db.commit()

        screen = SettingsScreen()
        qtbot.addWidget(screen)

        fmnp_id = _get_fmnp_id(fresh_settings_db)

        # User checks BOTH FMNP and Cash, clicks OK
        monkeypatch.setattr(
            AssignPaymentMethodsDialog, 'exec',
            lambda self: QDialog.Accepted)
        monkeypatch.setattr(
            AssignPaymentMethodsDialog, 'get_checked_payment_method_ids',
            lambda self: {fmnp_id, 300})

        # User clicks No on the FMNP warning
        monkeypatch.setattr(
            QMessageBox, 'exec',
            lambda self: QMessageBox.No)

        screen._assign_payment_methods(701)

        # FMNP is NOT assigned (user backed out)
        fmnp_assigned = fresh_settings_db.execute(
            "SELECT 1 FROM market_payment_methods "
            " WHERE market_id = 701 AND payment_method_id = ?",
            (fmnp_id,)
        ).fetchone()
        assert fmnp_assigned is None, (
            "Clicking No on FMNP warning must NOT assign FMNP — "
            "the user explicitly backed out.")

        # But Cash IS assigned (user wasn't asked about it)
        cash_assigned = fresh_settings_db.execute(
            "SELECT 1 FROM market_payment_methods "
            " WHERE market_id = 701 AND payment_method_id = 300"
        ).fetchone()
        assert cash_assigned is not None, (
            "Clicking No on FMNP warning must NOT cancel the entire "
            "save — other checked methods still get assigned.")

    def test_reassigning_already_assigned_fmnp_no_warning(
            self, qtbot, fresh_settings_db, monkeypatch):
        """If FMNP is ALREADY assigned to the market, opening the
        dialog and re-saving (with FMNP still checked) must NOT
        re-fire the warning — the user isn't ADDING anything new."""
        from fam.ui.settings_screen import (
            SettingsScreen, AssignPaymentMethodsDialog,
        )
        from PySide6.QtWidgets import QMessageBox, QDialog

        fresh_settings_db.execute(
            "INSERT INTO markets "
            " (id, name, daily_match_limit) "
            " VALUES (702, 'Sewickley', 10000)")
        fresh_settings_db.commit()

        fmnp_id = _get_fmnp_id(fresh_settings_db)
        # FMNP already assigned
        fresh_settings_db.execute(
            "INSERT INTO market_payment_methods "
            " (market_id, payment_method_id) VALUES (702, ?)",
            (fmnp_id,))
        fresh_settings_db.commit()

        screen = SettingsScreen()
        qtbot.addWidget(screen)

        monkeypatch.setattr(
            AssignPaymentMethodsDialog, 'exec',
            lambda self: QDialog.Accepted)
        monkeypatch.setattr(
            AssignPaymentMethodsDialog, 'get_checked_payment_method_ids',
            lambda self: {fmnp_id})  # FMNP STILL checked (no change)

        dialog_count = {'n': 0}
        def captured(self):
            dialog_count['n'] += 1
            return QMessageBox.Yes
        monkeypatch.setattr(QMessageBox, 'exec', captured)

        screen._assign_payment_methods(702)

        assert dialog_count['n'] == 0, (
            "FMNP was already assigned; the warning must only fire "
            "when the operator is ADDING FMNP for the first time.")


class TestNonFMNPMethodsDoNotFireWarning:
    """The warning is FMNP-specific.  Toggling Cash / SNAP / etc.
    must not show a confirmation dialog."""

    def test_activating_cash_does_not_fire_warning(
            self, qtbot, fresh_settings_db, monkeypatch):
        from fam.ui.settings_screen import SettingsScreen
        from PySide6.QtWidgets import QMessageBox

        # Seed an inactive Cash method
        fresh_settings_db.execute(
            "INSERT INTO payment_methods "
            " (name, match_percent, is_active, sort_order) "
            " VALUES ('Cash', 0.0, 0, 6)")
        fresh_settings_db.commit()
        cash_id = fresh_settings_db.execute(
            "SELECT id FROM payment_methods WHERE name = 'Cash'"
        ).fetchone()['id']

        screen = SettingsScreen()
        qtbot.addWidget(screen)

        dialog_count = {'n': 0}
        original_exec = QMessageBox.exec
        def captured(self):
            dialog_count['n'] += 1
            return QMessageBox.Yes
        monkeypatch.setattr(QMessageBox, 'exec', captured)

        screen._toggle_pm(cash_id, current_active=0)

        monkeypatch.setattr(QMessageBox, 'exec', original_exec)

        assert dialog_count['n'] == 0, (
            "Only FMNP gets the activation warning; Cash and other "
            "regular methods toggle without confirmation.")
