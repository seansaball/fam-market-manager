"""Tests for the v22→v23 schema migration that enforces UNIQUE on
``vendors.name`` to match the existing market and payment-method
behaviour.

Coverage:

1. Fresh-install schema bakes UNIQUE into the column itself.
2. ``create_vendor()`` raises an IntegrityError on duplicate names.
3. ``update_vendor()`` raises an IntegrityError when renaming a
   vendor to a name another vendor already uses.
4. The v22→v23 migration renames existing duplicates with ``(N)``
   suffixes (preserving the lowest-id row's name) and creates the
   UNIQUE INDEX so subsequent inserts reject duplicates.
5. Vendor IDs are preserved through the migration so all foreign-key
   relationships (transactions, fmnp_entries, market_vendors) remain
   valid.
6. The Settings UI handlers translate the UNIQUE error into the same
   friendly "already exists" message the markets / payment-methods
   handlers produce — source-level guard so future refactors keep
   parity.
"""

import sqlite3

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import (
    CURRENT_SCHEMA_VERSION,
    initialize_database,
    _migrate_v22_to_v23,
)
from fam.models.vendor import create_vendor, update_vendor


# ──────────────────────────────────────────────────────────────────
# Fixture: clean DB per test
# ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_vendor_unique.db")
    close_connection()
    set_db_path(db_file)
    yield tmp_path, db_file
    close_connection()


# ──────────────────────────────────────────────────────────────────
# Fresh-install schema enforces UNIQUE
# ──────────────────────────────────────────────────────────────────
class TestFreshInstallVendorUnique:
    def test_vendors_name_is_unique_on_fresh_install(self, fresh_db):
        """A brand-new database must reject a duplicate vendor name on
        INSERT — same constraint behaviour as markets and payment_methods."""
        initialize_database()
        conn = get_connection()
        conn.execute("INSERT INTO vendors (name) VALUES (?)", ("Acme Farm",))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError) as exc:
            conn.execute("INSERT INTO vendors (name) VALUES (?)",
                         ("Acme Farm",))
            conn.commit()
        assert 'UNIQUE' in str(exc.value).upper()

    def test_schema_version_bumped_to_23(self, fresh_db):
        """Fresh install reports the new schema version."""
        initialize_database()
        assert CURRENT_SCHEMA_VERSION >= 23
        conn = get_connection()
        row = conn.execute(
            "SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] == CURRENT_SCHEMA_VERSION

    def test_markets_payment_methods_still_unique(self, fresh_db):
        """Sanity check: the new constraint didn't break the existing ones."""
        initialize_database()
        conn = get_connection()
        conn.execute(
            "INSERT INTO markets (name) VALUES (?)", ("Downtown",))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO markets (name) VALUES (?)", ("Downtown",))
            conn.commit()
        # Reset the rolled-back transaction and try payment_methods
        conn.rollback()
        conn.execute(
            "INSERT INTO payment_methods (name, match_percent) VALUES (?, ?)",
            ("Cash", 0.0))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO payment_methods (name, match_percent)"
                " VALUES (?, ?)",
                ("Cash", 0.0))
            conn.commit()


# ──────────────────────────────────────────────────────────────────
# Model layer rejects duplicates
# ──────────────────────────────────────────────────────────────────
class TestModelRejectsDuplicates:
    def test_create_vendor_rejects_duplicate(self, fresh_db):
        initialize_database()
        create_vendor("Acme Farm")
        with pytest.raises(sqlite3.IntegrityError) as exc:
            create_vendor("Acme Farm")
        assert 'UNIQUE' in str(exc.value).upper()

    def test_update_vendor_rejects_rename_collision(self, fresh_db):
        """Renaming vendor B to vendor A's name must fail with the same
        UNIQUE error so the UI can surface a friendly dialog."""
        initialize_database()
        a_id = create_vendor("Acme Farm")
        b_id = create_vendor("Bramble Orchard")
        assert a_id != b_id
        with pytest.raises(sqlite3.IntegrityError) as exc:
            update_vendor(b_id, name="Acme Farm")
        assert 'UNIQUE' in str(exc.value).upper()

    def test_update_vendor_keeps_own_name_idempotent(self, fresh_db):
        """Updating a vendor's other fields without changing its name —
        or 'renaming' to its own current name — must still succeed."""
        initialize_database()
        a_id = create_vendor("Acme Farm")
        # Idempotent rename to same name
        update_vendor(a_id, name="Acme Farm", contact_info="555-0100")
        # Should not raise
        conn = get_connection()
        row = conn.execute(
            "SELECT name, contact_info FROM vendors WHERE id=?",
            (a_id,)).fetchone()
        assert row['name'] == 'Acme Farm'
        assert row['contact_info'] == '555-0100'


# ──────────────────────────────────────────────────────────────────
# Migration v22 → v23 dedup logic
# ──────────────────────────────────────────────────────────────────
class TestMigrationDedupesDuplicates:
    """Simulate a pre-v23 database with existing duplicate vendor names
    and verify the migration cleanly disambiguates them."""

    def _build_pre_v23_vendors_table(self):
        """Recreate the v22-era vendors table (no UNIQUE on name) so we
        can seed duplicates before running the migration manually."""
        conn = get_connection()
        # Build the schema as it stood at v22
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                contact_info TEXT,
                is_active BOOLEAN DEFAULT 1,
                check_payable_to TEXT,
                street TEXT,
                city TEXT,
                state TEXT,
                zip_code TEXT,
                ach_enabled BOOLEAN DEFAULT 0
            );
        """)
        conn.commit()
        return conn

    def test_renames_simple_duplicate_pair(self, fresh_db):
        conn = self._build_pre_v23_vendors_table()
        conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'Acme Farm')")
        conn.execute("INSERT INTO vendors (id, name) VALUES (2, 'Acme Farm')")
        conn.commit()

        _migrate_v22_to_v23(conn)

        rows = conn.execute(
            "SELECT id, name FROM vendors ORDER BY id").fetchall()
        # Lower-id keeps the original name, higher-id gets " (2)" suffix
        assert rows[0]['id'] == 1
        assert rows[0]['name'] == 'Acme Farm'
        assert rows[1]['id'] == 2
        assert rows[1]['name'] == 'Acme Farm (2)'

    def test_renames_three_way_duplicate(self, fresh_db):
        conn = self._build_pre_v23_vendors_table()
        for i in (1, 2, 3):
            conn.execute(
                "INSERT INTO vendors (id, name) VALUES (?, 'Acme Farm')",
                (i,))
        conn.commit()

        _migrate_v22_to_v23(conn)

        rows = conn.execute(
            "SELECT id, name FROM vendors ORDER BY id").fetchall()
        assert [r['name'] for r in rows] == [
            'Acme Farm', 'Acme Farm (2)', 'Acme Farm (3)']

    def test_avoids_collision_with_existing_suffixed_name(self, fresh_db):
        """If the natural ' (2)' suffix is already taken by an unrelated
        vendor, skip to ' (3)' instead of failing."""
        conn = self._build_pre_v23_vendors_table()
        conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'Acme Farm')")
        conn.execute("INSERT INTO vendors (id, name) VALUES (2, 'Acme Farm')")
        # An unrelated vendor that happens to be named "Acme Farm (2)"
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (3, 'Acme Farm (2)')")
        conn.commit()

        _migrate_v22_to_v23(conn)

        rows = conn.execute(
            "SELECT id, name FROM vendors ORDER BY id").fetchall()
        assert rows[0]['name'] == 'Acme Farm'
        assert rows[1]['name'] == 'Acme Farm (3)'   # bumped past existing (2)
        assert rows[2]['name'] == 'Acme Farm (2)'   # unrelated row untouched

    def test_no_duplicates_is_a_clean_pass_through(self, fresh_db):
        """Migration must not modify rows that are already unique."""
        conn = self._build_pre_v23_vendors_table()
        conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'Acme')")
        conn.execute("INSERT INTO vendors (id, name) VALUES (2, 'Bramble')")
        conn.commit()

        _migrate_v22_to_v23(conn)

        rows = conn.execute(
            "SELECT id, name FROM vendors ORDER BY id").fetchall()
        assert rows[0]['name'] == 'Acme'
        assert rows[1]['name'] == 'Bramble'

    def test_migration_creates_unique_index(self, fresh_db):
        """After migration, attempting an INSERT of a duplicate name
        must raise — proving the UNIQUE INDEX is in place."""
        conn = self._build_pre_v23_vendors_table()
        conn.execute("INSERT INTO vendors (name) VALUES ('Acme')")
        conn.commit()
        _migrate_v22_to_v23(conn)

        with pytest.raises(sqlite3.IntegrityError) as exc:
            conn.execute("INSERT INTO vendors (name) VALUES ('Acme')")
            conn.commit()
        assert 'UNIQUE' in str(exc.value).upper()

    def test_migration_preserves_vendor_ids(self, fresh_db):
        """Vendor IDs must not change — every FK to vendors.id (in
        transactions, fmnp_entries, market_vendors) depends on this."""
        conn = self._build_pre_v23_vendors_table()
        conn.execute(
            "INSERT INTO vendors (id, name, is_active) VALUES (5, 'Acme', 1)")
        conn.execute(
            "INSERT INTO vendors (id, name, is_active) VALUES (17, 'Acme', 1)")
        conn.execute(
            "INSERT INTO vendors (id, name, is_active) VALUES (42, 'Bramble', 0)")
        conn.commit()

        _migrate_v22_to_v23(conn)

        rows = {r['id']: dict(r) for r in conn.execute(
            "SELECT id, name, is_active FROM vendors").fetchall()}
        assert set(rows.keys()) == {5, 17, 42}
        assert rows[5]['name'] == 'Acme'         # lowest id keeps name
        assert rows[17]['name'] == 'Acme (2)'    # higher id renamed
        assert rows[42]['name'] == 'Bramble'
        # Other columns unchanged
        assert rows[5]['is_active'] == 1
        assert rows[17]['is_active'] == 1
        assert rows[42]['is_active'] == 0


# ──────────────────────────────────────────────────────────────────
# Settings UI translates UNIQUE error to friendly message
# ──────────────────────────────────────────────────────────────────
class TestSettingsScreenFriendlyError:
    """Source-level guard so future refactors don't drop the friendly
    'A vendor with the name X already exists.' translation we just
    added — matching the existing markets and payment-methods pattern."""

    def _vendor_handler_source(self, method_name: str) -> str:
        import inspect
        import fam.ui.settings_screen as ss
        full = inspect.getsource(ss)
        marker = f"def {method_name}("
        start = full.find(marker)
        assert start != -1, f"could not locate {method_name} in source"
        end = full.find('\n    def ', start + len(marker))
        if end == -1:
            end = len(full)
        return full[start:end]

    def test_add_vendor_translates_unique_error(self):
        src = self._vendor_handler_source("_add_vendor")
        assert "'UNIQUE'" in src or '"UNIQUE"' in src, \
            "_add_vendor must detect the UNIQUE-constraint error string"
        assert 'already exists' in src.lower(), \
            "_add_vendor must produce the same 'already exists' wording " \
            "as _add_market and _add_payment_method"
        assert 'vendor' in src.lower()

    def test_edit_vendor_translates_unique_error(self):
        src = self._vendor_handler_source("_edit_vendor")
        assert "'UNIQUE'" in src or '"UNIQUE"' in src, \
            "_edit_vendor must detect the UNIQUE-constraint error string"
        assert 'already exists' in src.lower(), \
            "_edit_vendor must produce the friendly 'already exists' wording"
