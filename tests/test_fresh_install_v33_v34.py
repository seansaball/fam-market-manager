"""Fresh-install path must run all migrations including v32→v33
and v33→v34 (v2.0.2 fix).

Pre-v2.0.2 the fresh-install branch in ``initialize_database``
created the tables, ran v3→v4 / v24→v25 / v27→v28 / v30→v31 /
v31→v32 — but **not** v32→v33.  New deployments stamped
``schema_version=33`` without the ``chk_pli_uf_zero_*`` triggers
that v33 added.  These tests pin the fix.

Also covers v33→v34 (schema_version dedup + UNIQUE INDEX).
"""

import sqlite3

import pytest

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import (
    CURRENT_SCHEMA_VERSION,
    initialize_database,
    _migrate_v33_to_v34,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_v33_v34.db")
    close_connection()
    set_db_path(db_file)
    yield tmp_path, db_file
    close_connection()


class TestFreshInstallV33Triggers:
    """v33 added the UF zero-match enforcement triggers.  Fresh
    installs MUST get them — they were the population that needed
    the defense-in-depth most."""

    def test_uf_zero_insert_trigger_exists(self):
        initialize_database()
        conn = get_connection()
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            " WHERE type='trigger' AND name='chk_pli_uf_zero_insert'"
        ).fetchone()
        assert row is not None, (
            "Fresh install must create chk_pli_uf_zero_insert "
            "trigger.  Without it, a brand new v2.0.1+ deployment "
            "is missing the v33 defense-in-depth.")

    def test_uf_zero_update_trigger_exists(self):
        initialize_database()
        conn = get_connection()
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            " WHERE type='trigger' AND name='chk_pli_uf_zero_update'"
        ).fetchone()
        assert row is not None, (
            "Fresh install must create chk_pli_uf_zero_update trigger")

    def test_schema_version_recorded(self):
        initialize_database()
        conn = get_connection()
        version = conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        assert version == CURRENT_SCHEMA_VERSION


class TestFreshInstallV34UniqueIndex:
    """v34 added a UNIQUE INDEX on schema_version.version + dedupe.
    Fresh installs MUST get the index so future Reset cycles can't
    produce duplicate version rows."""

    def test_unique_index_exists(self):
        initialize_database()
        conn = get_connection()
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            " WHERE type='index' AND name='idx_schema_version_unique'"
        ).fetchone()
        assert row is not None, (
            "Fresh install must create the schema_version unique index "
            "so duplicate version rows can never accumulate")

    def test_duplicate_version_rejected(self):
        """The unique index turns a duplicate INSERT into a
        constraint violation.  This pins that the constraint actually
        enforces what we claim."""
        initialize_database()
        conn = get_connection()
        with pytest.raises(sqlite3.IntegrityError):
            # The current schema version is already in the row;
            # a plain INSERT (no OR IGNORE) of the same version
            # MUST be rejected.
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (CURRENT_SCHEMA_VERSION,)
            )

    def test_or_ignore_silently_dedupes(self):
        """``INSERT OR IGNORE`` of an existing version is a no-op."""
        initialize_database()
        conn = get_connection()
        before = conn.execute(
            "SELECT COUNT(*) FROM schema_version "
            " WHERE version = ?",
            (CURRENT_SCHEMA_VERSION,)
        ).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,)
        )
        after = conn.execute(
            "SELECT COUNT(*) FROM schema_version "
            " WHERE version = ?",
            (CURRENT_SCHEMA_VERSION,)
        ).fetchone()[0]
        assert before == after == 1


class TestV33ToV34DedupesExistingDuplicates:
    """The v33→v34 migration must dedupe pre-existing duplicate
    version rows on legacy databases."""

    def test_existing_duplicates_collapsed(self, tmp_path):
        """Build a synthetic legacy DB with duplicate schema_version
        rows, run the migration, confirm only one row per version
        remains."""
        db_path = str(tmp_path / 'legacy_dupes.db')
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE schema_version (
                version INTEGER,
                applied_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Insert duplicates for the same version (the bug).
        for v in (1, 1, 1, 5, 5, 33, 33, 33, 33):
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (v,))
        conn.commit()

        _migrate_v33_to_v34(conn)

        rows = conn.execute(
            "SELECT version, COUNT(*) FROM schema_version GROUP BY version"
        ).fetchall()
        for version, count in rows:
            assert count == 1, (
                f"Version {version} still has {count} rows "
                f"after v33->v34 dedupe")

        # And the unique index is in place going forward.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (1)")
        conn.close()

    def test_idempotent_on_clean_table(self):
        """Running v33→v34 a second time on an already-deduped
        table must be a no-op."""
        initialize_database()
        conn = get_connection()
        before = conn.execute(
            "SELECT COUNT(*) FROM schema_version"
        ).fetchone()[0]
        _migrate_v33_to_v34(conn)
        after = conn.execute(
            "SELECT COUNT(*) FROM schema_version"
        ).fetchone()[0]
        assert before == after
