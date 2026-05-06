"""FMNP entries added to CLOSED market days reach cloud sync
(v2.0.6 fix).

Coordinator-reported behavior gap: FMNP entries are frequently
added to closed market days after the fact (paper checks delivered
later, end-of-month batch entry).  Pre-v2.0.6 the auto-sync after
``fmnp_screen.entry_saved`` narrowed scope to the currently-OPEN
market day — silently skipping the closed day's new entry.  The
collector itself was always ready (no market_day status filter),
but the orchestration scoped the sync away from the affected day.

Fix:
  * ``entry_saved`` signal now carries the affected market_day_id
  * ``_on_fmnp_entry_saved`` slot in main_window passes it to
    ``_trigger_sync(scope_md_id_override=md_id)``
  * The sync collects from THAT market day regardless of whether
    it's open or closed.

Plus the secondary UX fix: the FMNP screen's market-day dropdown
now defaults to the currently-OPEN market day (or the most recent
day if none open) on first load, instead of silently selecting the
oldest historical entry.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_fmnp_closed_sync.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield tmp_path
    close_connection()


def _setup_market_with_closed_and_open_days(conn):
    """Two market days under one market: one CLOSED, one OPEN."""
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit) "
        " VALUES (10, 'Bethel Park', 10000)")
    conn.execute(
        "INSERT INTO vendors (id, name, is_active) "
        " VALUES (20, '1.11 Juice Bar', 1)")
    conn.execute(
        "INSERT INTO market_days "
        " (id, market_id, date, status, opened_by, closed_by, "
        "  closed_at) "
        " VALUES (40, 10, '2026-04-26', 'Closed', 'Tester', "
        "  'Tester', '2026-04-26 16:00:00')")
    conn.execute(
        "INSERT INTO market_days "
        " (id, market_id, date, status, opened_by) "
        " VALUES (41, 10, '2026-05-06', 'Open', 'Tester')")
    conn.commit()


# ─── Sync collector: covers closed-day FMNP entries ──────────────


class TestFmnpCollectorIncludesClosedDayEntries:

    def test_collector_returns_active_entry_on_closed_market_day(self):
        """Direct test of the collector — no market_day-status
        filter, only the entry's own Active/Deleted status."""
        from fam.sync.data_collector import _collect_fmnp_entries
        conn = get_connection()
        _setup_market_with_closed_and_open_days(conn)

        # Add an FMNP entry to the CLOSED market day (40)
        conn.execute(
            "INSERT INTO fmnp_entries "
            " (id, market_day_id, vendor_id, amount, status, "
            "  entered_by, created_at) "
            " VALUES (100, 40, 20, 500, 'Active', 'Coordinator', "
            "  '2026-05-06 09:00:00')")
        conn.commit()

        rows = _collect_fmnp_entries(conn, md_id=40)
        assert len(rows) >= 1, (
            "FMNP collector must return entries on closed market "
            "days.  The 'fe.status = Active' filter is the entry's "
            "own state (not the market_day's), so closed-market "
            "entries belong in the cloud sheet just like open-"
            "market ones.")
        # Confirm the entry's amount made it through
        assert any(
            r['Total Amount'] == 5.0
            for r in rows if r['Source'] == 'FMNP Entry')


# ─── _on_fmnp_entry_saved scopes the sync correctly ──────────────


class TestOnFmnpEntrySavedScopesToAffectedDay:
    """The slot in main_window must pass the emitted md_id through
    to _trigger_sync as ``scope_md_id_override``, NOT fall back to
    the open-market-day lookup."""

    def test_slot_passes_md_id_as_override(self, monkeypatch):
        """Source-pin: ``_on_fmnp_entry_saved`` calls
        ``_trigger_sync(scope_md_id_override=md_id)`` when md_id
        is non-zero.  We can't drive the full main_window without
        a Qt environment + DB, so verify by source inspection
        that the wiring is present."""
        import inspect
        import fam.ui.main_window as mw
        src = inspect.getsource(mw.MainWindow._on_fmnp_entry_saved)
        assert 'scope_md_id_override' in src, (
            "_on_fmnp_entry_saved must call _trigger_sync with "
            "scope_md_id_override so the affected day is collected, "
            "regardless of whether it's currently open.")
        assert 'md_id' in src

    def test_trigger_sync_signature_accepts_override(self):
        import inspect
        import fam.ui.main_window as mw
        sig = inspect.signature(mw.MainWindow._trigger_sync)
        assert 'scope_md_id_override' in sig.parameters, (
            "_trigger_sync must accept a scope_md_id_override "
            "keyword argument so the FMNP entry-save path can scope "
            "the sync to a CLOSED market day.")

    def test_signal_emits_market_day_id(self):
        """The Qt signal carries an int payload (the affected
        market_day_id).  Pre-fix the signal was payload-less
        (Signal()), which left the slot guessing about scope."""
        import inspect
        import fam.ui.fmnp_screen as fs
        src = inspect.getsource(fs)
        # The new signal declaration
        assert 'entry_saved = Signal(int)' in src, (
            "FMNP entry_saved signal must carry an int payload "
            "(the affected market_day_id) so the sync handler can "
            "scope correctly.")
        # And both emit sites must pass the md_id
        assert 'self.entry_saved.emit(int(md_id)' in src or \
               'self.entry_saved.emit(int(affected_md_id)' in src, (
            "Both _save_entry and _delete_entry must emit the "
            "affected market_day_id, not a bare emit().")


# ─── AdminScreen has the same closed-day sync issue ──────────────


class TestAdminScreenClosedDayMutationsScopeCorrectly:
    """v2.0.6: AdminScreen has the same closed-day-mutation scope
    bug as FMNP.  Adjustments and voids on transactions in CLOSED
    market days must reach the cloud sync.  AdminScreen now passes
    the affected market_day_id through ``data_changed`` so the sync
    handler can scope to that specific day."""

    def test_admin_data_changed_signal_carries_market_day_id(self):
        """Source-pin: ``AdminScreen.data_changed`` is now
        ``Signal(int)`` (the affected market_day_id).  Pre-fix it
        was a payload-less ``Signal()`` which left the slot
        guessing about scope."""
        import inspect
        import fam.ui.admin_screen as adm
        src = inspect.getsource(adm)
        assert 'data_changed = Signal(int)' in src, (
            "AdminScreen data_changed signal must carry an int "
            "payload (the affected market_day_id) so adjustments "
            "and voids on CLOSED market days reach the cloud sync.")

    def test_admin_emit_sites_pass_market_day_id(self):
        """Both _adjust_transaction and _void_transaction must
        pass the affected market_day_id to data_changed.emit(),
        not bare emit()."""
        import inspect
        import fam.ui.admin_screen as adm
        src = inspect.getsource(adm)
        # The new emit pattern
        assert 'self.data_changed.emit(int(' in src, (
            "AdminScreen emit sites must pass the affected "
            "market_day_id, not bare emit().")
        # Bare emit() should NOT appear (would indicate one of the
        # call sites was missed)
        assert 'self.data_changed.emit()' not in src, (
            "Found bare data_changed.emit() in admin_screen — at "
            "least one emit site is missing the market_day_id "
            "payload.")

    def test_on_admin_data_changed_slot_uses_override(self):
        """Source-pin: ``_on_admin_data_changed`` calls
        ``_trigger_sync(scope_md_id_override=md_id)`` — same pattern
        as ``_on_fmnp_entry_saved``."""
        import inspect
        import fam.ui.main_window as mw
        src = inspect.getsource(mw.MainWindow._on_admin_data_changed)
        assert 'scope_md_id_override' in src

    def test_main_window_wires_admin_to_dedicated_slot(self):
        """The data_changed connection must go to the dedicated
        slot, not the bare _trigger_sync."""
        import inspect
        import fam.ui.main_window as mw_module
        src = inspect.getsource(mw_module)
        assert ('admin_screen.data_changed.connect(self._on_admin_data_changed)'
                in src), (
            "AdminScreen data_changed must connect to "
            "_on_admin_data_changed so closed-day mutations scope "
            "the sync correctly.")


# ─── Default selection in dropdown ────────────────────────────────


class TestFmnpDropdownDefaultSelection:
    """The market-day dropdown defaults to the currently-OPEN
    market day on first load, falling back to the most recent day
    if none open.  Pre-fix the dropdown silently selected the
    oldest entry (because get_all_market_days returns oldest-first
    and Qt picks index 0 by default)."""

    def test_pick_default_returns_open_market_day_when_one_exists(
            self, qtbot):
        from fam.ui.fmnp_screen import FMNPScreen
        conn = get_connection()
        _setup_market_with_closed_and_open_days(conn)

        screen = FMNPScreen()
        qtbot.addWidget(screen)
        # The screen's __init__ → refresh → _load_market_days
        # populates _market_days_data already.
        default_id = screen._pick_default_market_day_id()
        assert default_id == 41, (
            f"Expected the OPEN market day (id=41) to be picked "
            f"as default, got {default_id}.")

    def test_pick_default_falls_back_to_most_recent_when_none_open(
            self, qtbot):
        from fam.ui.fmnp_screen import FMNPScreen
        conn = get_connection()
        # Two CLOSED market days, no open one
        conn.execute(
            "INSERT INTO markets (id, name, daily_match_limit) "
            " VALUES (50, 'M', 10000)")
        conn.execute(
            "INSERT INTO market_days "
            " (id, market_id, date, status, opened_by, closed_by, "
            "  closed_at) "
            " VALUES (200, 50, '2026-04-01', 'Closed', 'T', 'T', "
            "  '2026-04-01 16:00:00'),"
            " (201, 50, '2026-04-15', 'Closed', 'T', 'T', "
            "  '2026-04-15 16:00:00')")
        conn.commit()

        screen = FMNPScreen()
        qtbot.addWidget(screen)
        default_id = screen._pick_default_market_day_id()
        assert default_id == 201, (
            f"Expected the most recent closed market day (id=201, "
            f"date=2026-04-15) to be picked as default when no day "
            f"is open.  Got {default_id}.")

    def test_pick_default_returns_none_when_no_market_days(
            self, qtbot):
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        default = screen._pick_default_market_day_id()
        assert default is None

    def test_dropdown_selects_open_market_day_on_first_load(
            self, qtbot):
        """End-to-end: after refresh(), the dropdown's currentData()
        is the OPEN market day's id."""
        from fam.ui.fmnp_screen import FMNPScreen
        conn = get_connection()
        _setup_market_with_closed_and_open_days(conn)
        # FMNP method must be seeded so the screen's denomination
        # configurator doesn't error out.
        conn.execute(
            "INSERT INTO payment_methods "
            " (name, match_percent, is_active, sort_order, "
            "  denomination, photo_required) "
            " VALUES ('FMNP', 100.0, 1, 2, 500, 'Optional')")
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            " VALUES (10, 20)")
        conn.commit()

        screen = FMNPScreen()
        qtbot.addWidget(screen)
        # Re-call refresh to drive _load_market_days against our
        # seeded data
        screen.refresh()
        assert screen.md_combo.currentData() == 41, (
            f"Dropdown should default to the OPEN market day "
            f"(id=41).  Got currentData={screen.md_combo.currentData()}")
