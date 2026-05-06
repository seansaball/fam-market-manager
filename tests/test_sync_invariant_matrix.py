"""Cloud-sync invariant: every local mutation path emits a sync
trigger that reaches every relevant cloud-sheet tab (v2.0.6).

Background — the invariant we want to prove

  All changes to the local database that affect cloud-bound rows
  must trigger a sync to the centralized Google Sheet, with no
  silent gaps for closed market days, non-primary markets, or
  configuration-only changes.

Layer 1 (static audit) — already done in companion tests:
  * test_fmnp_closed_market_sync.py: closed-day FMNP entries
  * test_delete_stale_multi_market.py: cleanup gates by device_id only
  * test_settings_changed_signal.py: settings handlers emit

Layer 2 (this file) — parametric matrix:
  Every (mutation_source, slot) pair we ship must:
    1. Exist as a Qt signal on its source class
    2. Be connected to a slot on MainWindow
    3. Route to ``_trigger_sync`` (directly or via wrapper slot)

The test matrix below pins those wirings.  Adding a new mutation
source without a sync trigger will require updating this file —
that's the point: it forces the question "does this need sync?"
into the PR conversation.

Companion: SHEET_KEYS in fam.sync.manager.SyncManager defines what
each tab keys on; this file pins that the SOURCES are wired.  The
DATA collection logic is exercised by per-tab integration tests.
"""

import inspect

import pytest


# ─── Mutation sources we expect to fire a sync trigger ──────────


# Each row: (source_class_qualname, signal_name, slot_attr_name).
#
# - source_class_qualname: dotted path to the Qt class declaring the
#   signal (e.g. "fam.ui.fmnp_screen.FMNPScreen")
# - signal_name: class attribute name (e.g. "entry_saved")
# - slot_attr_name: the MainWindow attribute the signal is connected
#   to (or "_trigger_sync" if connected directly)
MUTATION_SOURCES = [
    # Receipt confirmation — bound to currently-open market day, so
    # the bare _trigger_sync (narrow per-md scope) is correct.
    ("fam.ui.payment_screen.PaymentScreen",
     "payment_confirmed", "_trigger_sync"),
    # Drafts (pause / resume) — same scope rationale as confirms.
    ("fam.ui.payment_screen.PaymentScreen",
     "draft_saved", "_trigger_sync"),
    # FMNP entries — may target closed market days, must use the
    # override slot.
    ("fam.ui.fmnp_screen.FMNPScreen",
     "entry_saved", "_on_fmnp_entry_saved"),
    # Admin adjustments / voids — same closed-day shape as FMNP.
    ("fam.ui.admin_screen.AdminScreen",
     "data_changed", "_on_admin_data_changed"),
    # Receipt intake — open day only, narrow-scope is correct.
    ("fam.ui.receipt_intake_screen.ReceiptIntakeScreen",
     "data_changed", "_trigger_sync"),
    # Settings — affects rows across all markets/time, must full-sync.
    ("fam.ui.settings_screen.SettingsScreen",
     "settings_changed", "_on_settings_changed"),
]


def _import_class(dotted: str):
    """Import and return a class from a dotted path."""
    mod_path, _, cls_name = dotted.rpartition('.')
    mod = __import__(mod_path, fromlist=[cls_name])
    return getattr(mod, cls_name)


class TestMutationSignalsExistOnSourceClasses:
    """Each declared signal must exist as a class attribute on its
    source class.  This is the structural pre-condition — without
    the signal, MainWindow has nothing to connect to."""

    @pytest.mark.parametrize(
        "source_qualname,signal_name,slot_name", MUTATION_SOURCES)
    def test_signal_attribute_present(
            self, source_qualname, signal_name, slot_name):
        cls = _import_class(source_qualname)
        # Qt Signal descriptors live on the class, not instances.
        # ``hasattr`` is sufficient — failure mode is AttributeError.
        assert hasattr(cls, signal_name), (
            f"{source_qualname} must declare a class-level "
            f"``{signal_name}`` Signal.  This is the cloud-sync "
            f"trigger source for this mutation path.")


class TestMainWindowConnectsEverySource:
    """Source-pin: every mutation signal must be ``.connect()``-ed
    in MainWindow's __init__.  We walk the source of __init__ and
    check that the connection line is present."""

    @pytest.mark.parametrize(
        "source_qualname,signal_name,slot_name", MUTATION_SOURCES)
    def test_signal_is_connected_in_main_window(
            self, source_qualname, signal_name, slot_name):
        import fam.ui.main_window as mw
        # Inspect the whole module so cross-method connections are
        # also covered (e.g. event handlers wiring on demand).
        src = inspect.getsource(mw)

        # Derive the screen attribute name from the source class.
        # By convention, FMNPScreen → fmnp_screen, AdminScreen →
        # admin_screen, ReceiptIntakeScreen → receipt_intake_screen,
        # PaymentScreen → payment_screen, SettingsScreen →
        # settings_screen.
        cls_name = source_qualname.rsplit('.', 1)[-1]
        # CamelCase → snake_case (drop trailing "Screen")
        import re
        if cls_name.endswith('Screen'):
            cls_name = cls_name[:-len('Screen')]
        snake = re.sub(r'(?<!^)(?=[A-Z])', '_', cls_name).lower()
        attr = f"{snake}_screen"
        # Special-case FMNP — class is FMNPScreen, attr is fmnp_screen
        if attr == 'f_m_n_p_screen':
            attr = 'fmnp_screen'

        # Allow any whitespace between connect call and slot, since
        # multi-line connects are common for readability.
        pattern_strict = (
            f"{attr}.{signal_name}.connect(self.{slot_name})")
        pattern_multiline = (
            f"{attr}.{signal_name}.connect(")

        assert (pattern_strict in src
                or (pattern_multiline in src
                    and f"self.{slot_name}" in src)), (
            f"MainWindow must connect "
            f"{attr}.{signal_name} to self.{slot_name}.  "
            f"Without this connection, mutations from "
            f"{source_qualname} will not trigger a cloud sync.")


class TestSlotsRouteToTriggerSync:
    """Every wrapper slot must end up calling ``_trigger_sync``.
    This catches a regression where someone refactors a slot and
    forgets the trigger."""

    @pytest.mark.parametrize(
        "source_qualname,signal_name,slot_name", MUTATION_SOURCES)
    def test_slot_calls_trigger_sync(
            self, source_qualname, signal_name, slot_name):
        if slot_name == '_trigger_sync':
            # The signal connects directly — nothing to inspect.
            return
        import fam.ui.main_window as mw
        slot = getattr(mw.MainWindow, slot_name, None)
        assert slot is not None, (
            f"MainWindow must define {slot_name}.")
        src = inspect.getsource(slot)
        assert '_trigger_sync' in src, (
            f"MainWindow.{slot_name} must call _trigger_sync — "
            f"the slot is the cloud-sync delivery vehicle for the "
            f"{signal_name} signal from {source_qualname}.")


# ─── Closed-day-aware slots use scope override ─────────────────


class TestClosedDayAwareSlotsUseScopeOverride:
    """The two slots that exist specifically because their source
    can target CLOSED market days (FMNP, Admin) must pass
    ``scope_md_id_override`` so the sync collects from the affected
    day, not the currently-open one."""

    @pytest.mark.parametrize("slot_name", [
        "_on_fmnp_entry_saved",
        "_on_admin_data_changed",
    ])
    def test_closed_day_slot_uses_scope_override(self, slot_name):
        import fam.ui.main_window as mw
        slot = getattr(mw.MainWindow, slot_name)
        src = inspect.getsource(slot)
        assert 'scope_md_id_override' in src, (
            f"MainWindow.{slot_name} must pass "
            f"scope_md_id_override to _trigger_sync.  Without it, "
            f"mutations on CLOSED market days would be silently "
            f"dropped from the sync (the auto-sync narrow-scope "
            f"path defaults to the OPEN day).")


# ─── Settings full-sweep is full-sweep ──────────────────────────


class TestSettingsSlotForcesFullScope:
    """Settings changes affect rows across all markets and time,
    so the slot must call _trigger_sync(force=True) — narrow
    per-md scope would miss whole-dataset tabs (Vendor Reimbursement,
    Error Log) which is exactly where vendor renames need to land."""

    def test_on_settings_changed_uses_force_true(self):
        import fam.ui.main_window as mw
        src = inspect.getsource(mw.MainWindow._on_settings_changed)
        assert 'force=True' in src, (
            "_on_settings_changed must call _trigger_sync with "
            "force=True so the sync runs full-scope.  Settings "
            "mutations (e.g. vendor renames) need to land on "
            "whole-dataset tabs (Vendor Reimbursement, Error Log) "
            "which a narrow per-md sync skips entirely.")


# ─── Sheet keys are deterministic across versions ───────────────


class TestSheetKeysAreStable:
    """Composite-key columns are part of the cloud sheet's identity.
    Changing them on a deployed sheet leaves orphan rows under the
    old key.  Pin the currently-shipping shape so a reckless rename
    triggers a failing test rather than a silent identity shift."""

    def test_sheet_keys_match_v2_0_6_shape(self):
        from fam.sync.manager import SyncManager
        expected = {
            'Vendor Reimbursement': [
                'market_code', 'device_id', 'Market Name', 'Vendor'],
            'FAM Match Report': [
                'market_code', 'device_id', 'Payment Method', 'Date'],
            'Detailed Ledger': [
                'market_code', 'device_id', 'Transaction ID'],
            'Transaction Log': [
                'market_code', 'device_id', 'Time',
                'Transaction', 'Action'],
            'Activity Log': [
                'market_code', 'device_id', 'Timestamp',
                'Record ID', 'Action'],
            'Geolocation': [
                'market_code', 'device_id', 'Zip Code', 'Date'],
            'FMNP Entries': [
                'market_code', 'device_id', 'Entry ID'],
            'Market Day Summary': [
                'market_code', 'device_id', 'Date'],
            'Error Log': [
                'market_code', 'device_id', 'Timestamp',
                'Module', 'Level'],
            'Generated Rewards': [
                'market_code', 'device_id', 'Date',
                'Customer', 'Source Method', 'Reward Method'],
            'Agent Tracker': ['device_id'],
        }
        for tab, key in expected.items():
            assert SyncManager.SHEET_KEYS.get(tab) == key, (
                f"SHEET_KEYS[{tab!r}] changed from {key} to "
                f"{SyncManager.SHEET_KEYS.get(tab)}.  Composite-key "
                f"changes leave orphan rows on deployed sheets — "
                f"if this is intentional, plan a migration and "
                f"update this expectation.")


class TestWholeDatasetTabsKeyOnDeviceIdNotMarketCode:
    """Whole-dataset tabs (collected across ALL market days) span
    multiple markets per device.  The ``upsert_rows`` cleanup gate
    must recognize this — pre-v2.0.6 it gated on market_code AND
    device_id, silently keeping stale rows for non-primary markets.
    The fix is to gate on device_id only.  This test pins the
    current behavior."""

    def test_upsert_cleanup_gates_by_device_id_only(self):
        import fam.sync.gsheets as gs
        src = inspect.getsource(gs.GoogleSheetsBackend.upsert_rows)
        # Pre-fix pattern (must NOT be present):
        assert "ex_row.get('market_code', '')) == my_mc and" \
               not in src, (
            "upsert_rows cleanup must NOT gate on market_code == "
            "my_mc.  Whole-dataset tabs (Vendor Reimbursement, "
            "Error Log) legitimately emit rows for ALL of this "
            "device's markets, but my_mc is the SINGLE primary "
            "market_code from app_settings — this gate silently "
            "left stale rows from other markets in the sheet "
            "indefinitely.  Gate on device_id only.")
