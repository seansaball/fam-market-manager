"""Tests for FMNP payment-method toggle and Entry-screen independence.

v1.9.8 changes:
  * FMNP can be deactivated from Settings → Payment Methods (previously
    locked as a system method).
  * Deactivating FMNP only hides it from the Receipt Intake / Payment
    Screen.  The dedicated FMNP Entry screen continues to work.
  * Default seed (Load Defaults from tutorial) now inserts FMNP with
    is_active=0 so a fresh install does not show FMNP as a payment-row
    option until a coordinator explicitly enables it.
"""

from unittest.mock import MagicMock, patch

import pytest

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_fmnp_toggle.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield get_connection()
    close_connection()


# ══════════════════════════════════════════════════════════════════
# Default seed — FMNP is inactive on Load Defaults
# ══════════════════════════════════════════════════════════════════
class TestSeedDefault:
    """The Load Defaults flow (run from the tutorial) should produce a
    fresh database where FMNP is present but NOT active as a payment row
    option.  Coordinators turn it on later if they want it.
    """

    def test_seed_inserts_fmnp_as_inactive(self, fresh_db):
        from fam.database.seed import seed_sample_data
        # First initialize_database() created the FMNP row via migrations
        # (with is_active=1).  seed_sample_data clears+repopulates only
        # if markets are empty.  Wipe payment_methods first so seed runs
        # the path we care about.
        fresh_db.execute("DELETE FROM payment_methods")
        fresh_db.commit()
        ok = seed_sample_data()
        assert ok, "seed_sample_data should have populated the empty DB"

        row = fresh_db.execute(
            "SELECT name, is_active FROM payment_methods WHERE name='FMNP'"
        ).fetchone()
        assert row is not None
        assert row['is_active'] == 0, \
            "Default seed must insert FMNP with is_active=0 so Receipt " \
            "Intake does not show it as a payment-row option until a " \
            "coordinator explicitly activates it"

    def test_seed_keeps_other_methods_active(self, fresh_db):
        """Only FMNP is inactive by default — every other method stays
        active."""
        from fam.database.seed import seed_sample_data
        fresh_db.execute("DELETE FROM payment_methods")
        fresh_db.commit()
        seed_sample_data()

        rows = fresh_db.execute(
            "SELECT name, is_active FROM payment_methods "
            "WHERE name != 'FMNP' ORDER BY name"
        ).fetchall()
        assert len(rows) > 0
        for r in rows:
            assert r['is_active'] == 1, \
                f"Method {r['name']} should be active by default — only " \
                "FMNP is intentionally inactive on first run"


# ══════════════════════════════════════════════════════════════════
# Payment Screen filter — inactive FMNP is hidden
# ══════════════════════════════════════════════════════════════════
class TestPaymentScreenFiltersInactiveFmnp:
    """When FMNP is inactive, the active-only payment-method queries
    used by Payment Screen and Receipt Intake must not return it."""

    def _setup(self, conn):
        conn.execute("INSERT INTO markets (id, name) VALUES (1, 'M')")
        # Replace FMNP migration row to ensure it exists in a known state
        conn.execute("DELETE FROM payment_methods WHERE name='FMNP'")
        conn.execute(
            "INSERT INTO payment_methods (name, match_percent, is_active, "
            "sort_order, denomination, photo_required) "
            "VALUES ('FMNP', 100.0, 0, 2, 500, 'Optional')"
        )
        conn.execute("INSERT INTO payment_methods (name, match_percent, "
                     "is_active, sort_order) VALUES ('Cash', 0.0, 1, 1)")
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, payment_method_id) "
            "SELECT 1, id FROM payment_methods"
        )
        conn.commit()

    def test_get_all_active_excludes_inactive_fmnp(self, fresh_db):
        from fam.models.payment_method import get_all_payment_methods
        self._setup(fresh_db)

        active = get_all_payment_methods(active_only=True)
        names = [m['name'] for m in active]
        assert 'Cash' in names
        assert 'FMNP' not in names, \
            "Inactive FMNP must NOT appear in active-only payment list"

    def test_get_for_market_active_excludes_inactive_fmnp(self, fresh_db):
        from fam.models.payment_method import get_payment_methods_for_market
        self._setup(fresh_db)

        market_methods = get_payment_methods_for_market(1, active_only=True)
        names = [m['name'] for m in market_methods]
        assert 'Cash' in names
        assert 'FMNP' not in names

    def test_inactive_fmnp_returns_when_active_only_false(self, fresh_db):
        """For Settings UI listing — including inactive — FMNP should
        still show up (so coordinators can re-activate it)."""
        from fam.models.payment_method import get_all_payment_methods
        self._setup(fresh_db)

        all_methods = get_all_payment_methods(active_only=False)
        names = [m['name'] for m in all_methods]
        assert 'FMNP' in names


# ══════════════════════════════════════════════════════════════════
# FMNP Entry screen independence — works regardless of is_active
# ══════════════════════════════════════════════════════════════════
class TestFmnpEntryScreenIndependence:
    """The dedicated FMNP Entry screen must function whether or not
    FMNP is "active" as a Payment-Screen option.  The screen looks up
    the FMNP payment method by name to read denomination + photo-required
    settings — that lookup MUST NOT filter on is_active.
    """

    def _setup(self, conn, fmnp_active=False):
        conn.execute("DELETE FROM payment_methods WHERE name='FMNP'")
        conn.execute(
            "INSERT INTO payment_methods (name, match_percent, is_active, "
            "sort_order, denomination, photo_required) "
            "VALUES ('FMNP', 100.0, ?, 2, 500, 'Optional')",
            (1 if fmnp_active else 0,)
        )
        conn.commit()

    def test_lookup_by_name_returns_inactive_fmnp(self, fresh_db):
        """get_payment_method_by_name MUST return FMNP regardless of
        is_active — the FMNP Entry screen depends on this."""
        from fam.models.payment_method import get_payment_method_by_name
        self._setup(fresh_db, fmnp_active=False)

        m = get_payment_method_by_name('FMNP')
        assert m is not None, \
            "FMNP Entry screen would break if this lookup filtered on is_active"
        assert m['name'] == 'FMNP'
        assert m['denomination'] == 500
        assert m['photo_required'] == 'Optional'

    def test_lookup_by_name_returns_active_fmnp_too(self, fresh_db):
        """Sanity: lookup also works when FMNP is active."""
        from fam.models.payment_method import get_payment_method_by_name
        self._setup(fresh_db, fmnp_active=True)

        m = get_payment_method_by_name('FMNP')
        assert m is not None
        assert m['is_active'] == 1

    def test_fmnp_entry_create_works_when_method_inactive(self, fresh_db):
        """Verify end-to-end: creating an FMNP entry while the FMNP
        payment method is inactive works perfectly.  This is the core
        promise of the v1.9.8 change."""
        from fam.models.fmnp import create_fmnp_entry, get_fmnp_entry_by_id
        self._setup(fresh_db, fmnp_active=False)
        fresh_db.execute("INSERT INTO markets (id, name) VALUES (1, 'M')")
        fresh_db.execute(
            "INSERT INTO market_days (id, market_id, date, status) "
            "VALUES (1, 1, '2026-04-24', 'Open')")
        fresh_db.execute(
            "INSERT INTO vendors (id, name, is_active) VALUES (1, 'V', 1)")
        fresh_db.commit()

        eid = create_fmnp_entry(
            market_day_id=1, vendor_id=1, amount=1500,
            entered_by='Volunteer', check_count=3)

        entry = get_fmnp_entry_by_id(eid)
        assert entry is not None
        assert entry['amount'] == 1500
        assert entry['check_count'] == 3
        # And the entry is not coupled to the payment_method.is_active
        # state — it lives in fmnp_entries, completely separate.


# ══════════════════════════════════════════════════════════════════
# Settings UI toggle — guards removed for FMNP
# ══════════════════════════════════════════════════════════════════
class TestSettingsToggleNoLongerBlocked:
    """Source-level regression guards that prevent re-introducing the
    FMNP-locked behavior we removed in v1.9.8.  Crude string matching
    against the source — but bullet-proof against accidental revert."""

    def test_no_protected_method_dialog_in_toggle_pm(self):
        import inspect
        import fam.ui.settings_screen as settings_module
        src = inspect.getsource(settings_module)
        assert "FMNP is a system payment method and cannot be deactivated" \
            not in src, \
            "FMNP-cannot-be-deactivated guard was re-introduced. " \
            "FMNP is intentionally togglable as of v1.9.8."

    def test_toggle_pm_does_not_special_case_fmnp(self):
        import inspect
        from fam.ui.settings_screen import SettingsScreen
        src = inspect.getsource(SettingsScreen._toggle_pm)
        # No FMNP-specific branching in _toggle_pm.  All payment methods
        # toggle uniformly via update_payment_method.
        assert "method['name'] == 'FMNP'" not in src, \
            "_toggle_pm must not special-case FMNP — toggle is uniform"
        assert "name == 'FMNP'" not in src

    def test_toggle_pm_calls_update_payment_method(self):
        """Confirm the simplified _toggle_pm still does the actual write."""
        from fam.ui.settings_screen import SettingsScreen
        screen = MagicMock()
        with patch('fam.ui.settings_screen.update_payment_method') as upm:
            SettingsScreen._toggle_pm(screen, 42, current_active=True)
        upm.assert_called_once_with(42, is_active=False)
        screen._load_payment_methods.assert_called_once()

    def test_toggle_pm_flips_inactive_to_active(self):
        from fam.ui.settings_screen import SettingsScreen
        screen = MagicMock()
        with patch('fam.ui.settings_screen.update_payment_method') as upm:
            SettingsScreen._toggle_pm(screen, 42, current_active=False)
        upm.assert_called_once_with(42, is_active=True)


# ══════════════════════════════════════════════════════════════════
# Reports preserve historical FMNP — even after deactivation
# ══════════════════════════════════════════════════════════════════
class TestReportsAfterFmnpDeactivation:
    """Historical transactions that used FMNP must still appear in
    reports after FMNP is deactivated.  Reports query the snapshot
    column (method_name_snapshot) on payment_line_items, not the live
    payment_methods table — so deactivating FMNP cannot retroactively
    erase past FMNP-paid transactions from reports."""

    def test_method_name_snapshot_independent_of_is_active(self, fresh_db):
        """Set up an FMNP transaction, deactivate FMNP, verify the
        snapshot row still shows FMNP."""
        # Minimal market structure
        fresh_db.execute("INSERT INTO markets (id, name) VALUES (1, 'M')")
        fresh_db.execute(
            "INSERT INTO market_days (id, market_id, date, status) "
            "VALUES (1, 1, '2026-04-24', 'Open')")
        fresh_db.execute(
            "INSERT INTO vendors (id, name, is_active) VALUES (1, 'V', 1)")
        fresh_db.execute("DELETE FROM payment_methods WHERE name='FMNP'")
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            "is_active, sort_order) VALUES (99, 'FMNP', 100.0, 1, 1)")
        # Transaction + payment line item with FMNP active
        fresh_db.execute(
            "INSERT INTO transactions (id, fam_transaction_id, market_day_id,"
            " vendor_id, receipt_total, status) "
            "VALUES (1, 'FAM-TEST-1', 1, 1, 2000, 'Confirmed')")
        fresh_db.execute(
            "INSERT INTO payment_line_items (transaction_id, payment_method_id,"
            " method_name_snapshot, match_percent_snapshot, method_amount,"
            " match_amount, customer_charged) "
            "VALUES (1, 99, 'FMNP', 100.0, 2000, 1000, 1000)")
        fresh_db.commit()

        # Now deactivate FMNP
        from fam.models.payment_method import update_payment_method
        update_payment_method(99, is_active=False)

        # Historical record still readable
        row = fresh_db.execute(
            "SELECT method_name_snapshot FROM payment_line_items "
            "WHERE transaction_id=1"
        ).fetchone()
        assert row['method_name_snapshot'] == 'FMNP', \
            "Historical FMNP transactions must remain visible in reports " \
            "after FMNP deactivation"
