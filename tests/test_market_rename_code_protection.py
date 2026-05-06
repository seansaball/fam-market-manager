"""Code-changing market renames are blocked once the market has
history (v2.0.6 / option 2).

Background

  market_code is part of every cloud-sync composite key.  Renaming a
  market in a way that shifts the derived market_code orphans rows on
  the shared Google Sheet:

    * Per-md tabs (Detailed Ledger, FAM Match, Transaction Log, FMNP
      Entries, Generated Rewards, Geolocation, Activity Log, Market
      Day Summary) leave old-code rows stranded forever — those tabs
      are scoped per-md, and the OLD market_code's rows aren't in
      the new collection so cleanup never visits them.
    * Whole-dataset tabs (Vendor Reimbursement, Error Log) would
      eventually clean up old-code rows but also lose the historical
      trail.
    * Multi-workstation race: an offline workstation resyncs under
      the OLD code, creating two market_code identities for the
      same physical market.

Decision (option 2):
  * BLOCK code-changing renames when market has any market_days.
  * ALLOW code-changing renames when market has no market_days
    (no cloud rows to orphan; show informational dialog).
  * ALWAYS ALLOW code-stable renames (typo fixes, casing) — the
    cloud identity doesn't move.

These tests pin the behavior via source inspection plus a small
integration test that drives ``_edit_market`` end-to-end.
"""

import inspect

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_market_rename.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield tmp_path
    close_connection()


# ─── Source pin ──────────────────────────────────────────────────


class TestEditMarketBlocksCodeChangeWithHistory:
    """The handler source must show the block-on-history pattern."""

    def test_source_checks_market_days_count_before_block(self):
        import fam.ui.settings_screen as ss
        src = inspect.getsource(ss.SettingsScreen._edit_market)
        # The block must be conditioned on market_days count, not
        # an unconditional warning-with-Yes/No.
        assert 'market_days WHERE market_id' in src, (
            "_edit_market must query market_days to decide whether "
            "to block the code-changing rename.")
        assert 'Rename Blocked' in src, (
            "_edit_market must surface a 'Rename Blocked' dialog "
            "when the rename would shift market_code AND the "
            "market has history.")

    def test_source_allows_pre_history_rename_with_info_dialog(self):
        import fam.ui.settings_screen as ss
        src = inspect.getsource(ss.SettingsScreen._edit_market)
        # The pre-history path uses an Information icon (allow) not
        # Critical (block).
        assert 'QMessageBox.Information' in src, (
            "_edit_market must use Information-icon dialog for "
            "pre-history code-changing renames (no cloud rows to "
            "orphan, so the rename is safe).")

    def test_source_uses_critical_icon_for_block(self):
        import fam.ui.settings_screen as ss
        src = inspect.getsource(ss.SettingsScreen._edit_market)
        assert 'QMessageBox.Critical' in src, (
            "_edit_market must use Critical-icon dialog for the "
            "block path so the operator immediately recognises this "
            "is not a soft warning.")


# ─── Integration: drive the handler end-to-end ──────────────────


def _setup_market(conn, market_id: int, name: str,
                  with_market_day: bool):
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit) "
        "VALUES (?, ?, 10000)", (market_id, name))
    if with_market_day:
        conn.execute(
            "INSERT INTO market_days "
            "(market_id, date, status, opened_by) "
            "VALUES (?, '2026-04-01', 'Closed', 'T')",
            (market_id,))
    conn.commit()


class TestEditMarketEndToEnd:
    """Drive ``_edit_market`` by stubbing the dialog and the
    QMessageBox surfaces.  Verifies the DB state matches the
    expected branch."""

    def _drive(self, qtbot, market_id, new_name,
               click_through_pre_history=True,
               monkeypatch=None):
        """Return (final_name_in_db, dialogs_seen)."""
        from PySide6.QtWidgets import QDialog, QMessageBox
        from fam.ui.settings_screen import SettingsScreen

        dialogs_seen: list[str] = []
        screen = SettingsScreen()
        qtbot.addWidget(screen)

        # Stub EditMarketDialog.exec to Accept and pre-fill name
        from fam.ui import settings_screen as ss

        class FakeEditMarketDialog:
            def __init__(self, market, parent):
                from PySide6.QtWidgets import QLineEdit
                self.name_input = QLineEdit(new_name)
                self.address_input = QLineEdit('')
            def exec(self):
                return QDialog.Accepted

        monkeypatch.setattr(ss, 'EditMarketDialog', FakeEditMarketDialog)

        # Capture which QMessageBox flavour was shown.  Whatever
        # icon was set, we pick the same default response — Yes for
        # the pre-history Information dialog (proceed), Ok for the
        # Critical block (acknowledge), no-op for any other case.
        original_qmb = QMessageBox

        class FakeQMessageBox:
            Critical = original_qmb.Critical
            Warning = original_qmb.Warning
            Information = original_qmb.Information
            Yes = original_qmb.Yes
            No = original_qmb.No
            Ok = original_qmb.Ok

            def __init__(self, parent=None):
                self._icon = None
                self._title = ''
                self._buttons = original_qmb.Ok
                self._default = None
            def setIcon(self, icon):
                self._icon = icon
                if icon == original_qmb.Critical:
                    dialogs_seen.append('Critical')
                elif icon == original_qmb.Warning:
                    dialogs_seen.append('Warning')
                elif icon == original_qmb.Information:
                    dialogs_seen.append('Information')
            def setWindowTitle(self, t): self._title = t
            def setText(self, t): pass
            def setInformativeText(self, t): pass
            def setStandardButtons(self, b): self._buttons = b
            def setDefaultButton(self, b): self._default = b
            def exec(self):
                if self._icon == original_qmb.Critical:
                    return original_qmb.Ok
                # Information: respect the test's intent
                if self._icon == original_qmb.Information:
                    return (original_qmb.Yes
                            if click_through_pre_history
                            else original_qmb.No)
                return original_qmb.Ok

            # Static methods used elsewhere in the handler — keep
            # the autouse stub semantics
            @staticmethod
            def warning(*a, **kw): return original_qmb.Ok
            @staticmethod
            def critical(*a, **kw): return original_qmb.Ok
            @staticmethod
            def information(*a, **kw): return original_qmb.Ok
            @staticmethod
            def question(*a, **kw): return original_qmb.Yes

        monkeypatch.setattr(ss, 'QMessageBox', FakeQMessageBox)

        screen._edit_market(market_id)

        conn = get_connection()
        row = conn.execute(
            "SELECT name FROM markets WHERE id=?",
            (market_id,)).fetchone()
        return (row['name'] if row else None), dialogs_seen

    def test_blocks_code_changing_rename_when_market_has_history(
            self, qtbot, monkeypatch):
        """Market with a market_day on record cannot have its name
        changed in a way that shifts the derived code."""
        conn = get_connection()
        _setup_market(conn, 100, 'Bethel Park', with_market_day=True)

        final_name, dialogs = self._drive(
            qtbot, 100, 'Pittsburgh South',
            monkeypatch=monkeypatch)

        # Name unchanged in DB
        assert final_name == 'Bethel Park', (
            f"Expected name to remain 'Bethel Park' (rename "
            f"blocked), got {final_name!r}.")
        assert 'Critical' in dialogs, (
            "Expected the Critical-icon block dialog to fire for "
            "code-changing renames on markets with history.")

    def test_allows_code_stable_rename_with_history(
            self, qtbot, monkeypatch):
        """Typo fixes that preserve the derived code are always
        allowed — even on markets with history."""
        from fam.utils.app_settings import derive_market_code
        # 'Bethel Park' and 'Bethel Park ' both derive to 'BP' —
        # use a real typo-class case.
        old = 'Bethal Park'  # typo
        new = 'Bethel Park'  # corrected
        # Both should derive to the same code (first letters of words)
        assert derive_market_code(old) == derive_market_code(new), (
            "test fixture invalid — pick a pair where derived "
            "codes match")

        conn = get_connection()
        _setup_market(conn, 101, old, with_market_day=True)

        final_name, dialogs = self._drive(
            qtbot, 101, new, monkeypatch=monkeypatch)

        assert final_name == new, (
            f"Expected typo fix to go through (codes match: "
            f"{derive_market_code(old)}).  Got {final_name!r}.")
        # No code-change dialog should fire for code-stable renames
        assert 'Critical' not in dialogs
        assert 'Information' not in dialogs

    def test_allows_code_changing_rename_when_no_market_days(
            self, qtbot, monkeypatch):
        """Markets with no market_days yet can be freely renamed —
        no cloud rows to orphan.  An Information dialog confirms,
        but the rename proceeds when the operator clicks Yes."""
        conn = get_connection()
        _setup_market(conn, 102, 'Old Name', with_market_day=False)

        final_name, dialogs = self._drive(
            qtbot, 102, 'Pittsburgh South',
            click_through_pre_history=True,
            monkeypatch=monkeypatch)

        assert final_name == 'Pittsburgh South', (
            f"Pre-history code-changing rename should be allowed "
            f"when operator confirms.  Got {final_name!r}.")
        assert 'Information' in dialogs, (
            "Expected Information-icon dialog (not Critical-block) "
            "for pre-history code change.")
        assert 'Critical' not in dialogs

    def test_pre_history_rename_can_be_cancelled(
            self, qtbot, monkeypatch):
        """If operator clicks No on the pre-history info dialog,
        the rename does not happen."""
        conn = get_connection()
        _setup_market(conn, 103, 'Old Name', with_market_day=False)

        final_name, dialogs = self._drive(
            qtbot, 103, 'Pittsburgh South',
            click_through_pre_history=False,
            monkeypatch=monkeypatch)

        assert final_name == 'Old Name', (
            f"Operator declined the rename — DB should be "
            f"unchanged.  Got {final_name!r}.")
        assert 'Information' in dialogs
