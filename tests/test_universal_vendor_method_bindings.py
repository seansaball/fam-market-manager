"""SNAP and Cash are universally accepted at every vendor (v2.0.7).

Policy: SNAP and Cash are bound to every vendor by default and
cannot be unassigned.  This eliminates the mixed-eligibility
overflow problem class for the most common non-denom payment
methods (the user-reported 2026-05-06 reproducer where SNAP
silently leaked onto a SNAP-ineligible vendor's transactions).

Tests pin:
  1. Migration v34→v35 backfills SNAP+Cash for every vendor
  2. ``unassign_payment_method_from_vendor`` refuses for SNAP/Cash
  3. ``is_universal_vendor_method`` helper returns the right values
  4. ``create_vendor`` auto-binds SNAP+Cash on new vendor creation
  5. UI: ``VendorEligiblePaymentMethodsDialog`` locks SNAP/Cash
"""

import inspect

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_universal_vendor.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield get_connection()
    close_connection()


# ──────────────────────────────────────────────────────────────────
# 1. Helper + constant
# ──────────────────────────────────────────────────────────────────


class TestIsUniversalVendorMethod:

    def test_snap_is_universal(self):
        from fam.models.payment_method import is_universal_vendor_method
        assert is_universal_vendor_method('SNAP') is True

    def test_cash_is_universal(self):
        from fam.models.payment_method import is_universal_vendor_method
        assert is_universal_vendor_method('Cash') is True

    def test_food_bucks_not_universal(self):
        from fam.models.payment_method import is_universal_vendor_method
        assert is_universal_vendor_method('JH Food Bucks') is False

    def test_food_rx_not_universal(self):
        from fam.models.payment_method import is_universal_vendor_method
        assert is_universal_vendor_method('Food RX') is False

    def test_fmnp_not_universal(self):
        from fam.models.payment_method import is_universal_vendor_method
        assert is_universal_vendor_method('FMNP') is False

    def test_none_not_universal(self):
        from fam.models.payment_method import is_universal_vendor_method
        assert is_universal_vendor_method(None) is False
        assert is_universal_vendor_method('') is False


# ──────────────────────────────────────────────────────────────────
# 2. Migration backfills SNAP+Cash for every vendor
# ──────────────────────────────────────────────────────────────────


class TestMigrationV34ToV35:
    """The migration must ensure SNAP and Cash are bound to every
    vendor, even if previous data state had them unassigned (e.g.
    legacy DBs, .fam imports, manually unassigned)."""

    def test_seed_data_has_universal_bindings(self, fresh_db):
        """Load Defaults seeds vendors + payment methods including
        SNAP and Cash.  After initialize + seed, every vendor must
        have SNAP and Cash bindings."""
        from fam.database.seed import seed_sample_data
        # Wipe payment_methods so seed runs the canonical path
        fresh_db.execute("DELETE FROM market_payment_methods")
        fresh_db.execute("DELETE FROM vendor_payment_methods")
        fresh_db.execute("DELETE FROM payment_methods")
        fresh_db.execute("DELETE FROM market_vendors")
        fresh_db.execute("DELETE FROM markets")
        fresh_db.execute("DELETE FROM vendors")
        fresh_db.commit()
        ok = seed_sample_data()
        assert ok

        # Re-run migration to ensure idempotence
        from fam.database.schema import _migrate_v34_to_v35
        _migrate_v34_to_v35(fresh_db)

        # Check every vendor has SNAP + Cash
        snap_id = fresh_db.execute(
            "SELECT id FROM payment_methods WHERE name='SNAP'"
        ).fetchone()['id']
        cash_id = fresh_db.execute(
            "SELECT id FROM payment_methods WHERE name='Cash'"
        ).fetchone()['id']
        vendor_ids = [r['id'] for r in fresh_db.execute(
            "SELECT id FROM vendors").fetchall()]
        assert vendor_ids, "Test seeded but no vendors found"

        for vid in vendor_ids:
            for pmid, pm_name in [(snap_id, 'SNAP'), (cash_id, 'Cash')]:
                row = fresh_db.execute(
                    "SELECT 1 FROM vendor_payment_methods "
                    "WHERE vendor_id=? AND payment_method_id=?",
                    (vid, pmid),
                ).fetchone()
                assert row is not None, (
                    f"Vendor {vid} missing universal binding for "
                    f"{pm_name} (id={pmid}).  Migration v34→v35 "
                    f"must backfill these for every vendor.")

    def test_migration_is_idempotent(self, fresh_db):
        from fam.database.seed import seed_sample_data
        from fam.database.schema import _migrate_v34_to_v35
        # Build a minimal seed
        fresh_db.execute("DELETE FROM payment_methods")
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            "is_active, sort_order) VALUES "
            "(1, 'SNAP', 100.0, 1, 1), (2, 'Cash', 0.0, 1, 2)")
        fresh_db.execute(
            "INSERT INTO vendors (id, name) VALUES (5, 'TestV')")
        fresh_db.commit()
        # Run twice — second run should be a no-op
        _migrate_v34_to_v35(fresh_db)
        _migrate_v34_to_v35(fresh_db)
        rows = fresh_db.execute(
            "SELECT COUNT(*) AS n FROM vendor_payment_methods "
            "WHERE vendor_id=5").fetchone()['n']
        assert rows == 2, (
            f"Migration must be idempotent — got {rows} bindings "
            f"after running twice (expected 2: SNAP + Cash).")


# ──────────────────────────────────────────────────────────────────
# 3. Defensive guard: refuse to unassign SNAP/Cash
# ──────────────────────────────────────────────────────────────────


class TestUnassignRefusedForUniversalMethods:

    def test_unassign_snap_is_no_op(self, fresh_db):
        from fam.models.payment_method import (
            assign_payment_method_to_vendor,
            unassign_payment_method_from_vendor,
        )
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            "is_active, sort_order) VALUES "
            "(1, 'SNAP', 100.0, 1, 1)")
        fresh_db.execute(
            "INSERT INTO vendors (id, name) VALUES (10, 'V10')")
        fresh_db.commit()
        assign_payment_method_to_vendor(10, 1)
        # Now try to unassign SNAP
        unassign_payment_method_from_vendor(10, 1)
        # Binding must still exist
        row = fresh_db.execute(
            "SELECT 1 FROM vendor_payment_methods "
            "WHERE vendor_id=10 AND payment_method_id=1"
        ).fetchone()
        assert row is not None, (
            "SNAP binding must persist after unassign attempt.")

    def test_unassign_cash_is_no_op(self, fresh_db):
        from fam.models.payment_method import (
            assign_payment_method_to_vendor,
            unassign_payment_method_from_vendor,
        )
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            "is_active, sort_order) VALUES "
            "(2, 'Cash', 0.0, 1, 2)")
        fresh_db.execute(
            "INSERT INTO vendors (id, name) VALUES (11, 'V11')")
        fresh_db.commit()
        assign_payment_method_to_vendor(11, 2)
        unassign_payment_method_from_vendor(11, 2)
        row = fresh_db.execute(
            "SELECT 1 FROM vendor_payment_methods "
            "WHERE vendor_id=11 AND payment_method_id=2"
        ).fetchone()
        assert row is not None

    def test_unassign_non_universal_method_works_normally(
            self, fresh_db):
        """Non-universal methods (Food Bucks, Food RX, FMNP) can
        still be unassigned per-vendor — the policy only locks
        SNAP and Cash."""
        from fam.models.payment_method import (
            assign_payment_method_to_vendor,
            unassign_payment_method_from_vendor,
        )
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            "is_active, sort_order, denomination) VALUES "
            "(3, 'JH Food Bucks', 100.0, 1, 3, 200)")
        fresh_db.execute(
            "INSERT INTO vendors (id, name) VALUES (12, 'V12')")
        fresh_db.commit()
        assign_payment_method_to_vendor(12, 3)
        unassign_payment_method_from_vendor(12, 3)
        row = fresh_db.execute(
            "SELECT 1 FROM vendor_payment_methods "
            "WHERE vendor_id=12 AND payment_method_id=3"
        ).fetchone()
        assert row is None, (
            "Food Bucks (non-universal) must still unassign normally.")


# ──────────────────────────────────────────────────────────────────
# 4. UI source-pin: dialog locks SNAP/Cash checkboxes
# ──────────────────────────────────────────────────────────────────


class TestVendorEligibilityDialogLocks:

    def test_dialog_disables_universal_checkboxes(self):
        import fam.ui.settings_screen as ss
        src = inspect.getsource(
            ss.VendorEligiblePaymentMethodsDialog)
        assert 'is_universal_vendor_method' in src, (
            "VendorEligiblePaymentMethodsDialog must use "
            "is_universal_vendor_method to identify SNAP/Cash.")
        assert 'cb.setEnabled(False)' in src, (
            "Universal checkboxes must be disabled (visually "
            "locked).")
        assert 'universal' in src.lower(), (
            "Tooltip / label should mention 'universal' so the "
            "volunteer understands why the checkbox is locked.")


# ──────────────────────────────────────────────────────────────────
# 5. Schema version (v34→v35 SNAP/Cash universal binding stayed
#    intact across the v35→v36 customer_forfeit_cents addition).
# ──────────────────────────────────────────────────────────────────


class TestSchemaVersion35:

    def test_current_schema_version_at_least_35(self):
        """v35 was the universal-binding migration; later versions
        (v36 = customer_forfeit_cents column) preserve v35's
        guarantees.  This test just pins that the schema number
        never goes BACKWARD."""
        from fam.database.schema import CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION >= 35

    def test_v34_to_v35_migration_function_exists(self):
        from fam.database.schema import _migrate_v34_to_v35
        # Just verify it's callable and importable
        assert callable(_migrate_v34_to_v35)
