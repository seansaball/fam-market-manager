"""Schema v37 -> v38 migration tests (v2.1.0 / ENH-002, matrix row 2).

Covers:
  * fresh install lands at v38 with columns + triggers + index
  * direct v37 -> v38 migration: column adds, FMNP toggle backfill,
    entry backfill (LITERAL 100.0 match snapshot — never the live
    setting), idempotent re-run
  * NOT-NULL insert trigger (denomination_snapshot exempt)
  * snapshot-immutability trigger (value changes blocked,
    NULL -> value repair allowed, unrelated column updates fine)
  * defensive FMNP re-insert when the method row is missing
  * pre-existing amount triggers still fire
  * downgrade guard refuses a newer-schema DB
"""

import sqlite3

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import (
    CURRENT_SCHEMA_VERSION,
    initialize_database,
    _migrate_v37_to_v38,
)


@pytest.fixture
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_migration_v38.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield get_connection()
    close_connection()


# ── A v37-shaped scratch DB (just the two tables the migration
# touches, replicating the pre-v38 DDL) ──────────────────────────


V37_DDL = """
CREATE TABLE payment_methods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    match_percent REAL NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    sort_order INTEGER DEFAULT 0,
    denomination INTEGER DEFAULT NULL,
    photo_required TEXT DEFAULT NULL,
    is_system BOOLEAN DEFAULT 0
);
CREATE TABLE fmnp_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_day_id INTEGER NOT NULL,
    vendor_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    check_count INTEGER,
    notes TEXT,
    photo_path TEXT DEFAULT NULL,
    photo_drive_url TEXT DEFAULT NULL,
    entered_by TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    status TEXT DEFAULT 'Active'
);
"""


@pytest.fixture
def v37_conn():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(V37_DDL)
    yield conn
    conn.close()


def _cols(conn, table):
    return {row[1] for row in conn.execute(
        f"PRAGMA table_info({table})").fetchall()}


def _triggers(conn):
    return {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'"
    ).fetchall()}


# ──────────────────────────────────────────────────────────────────
# Fresh install
# ──────────────────────────────────────────────────────────────────


class TestFreshInstall:

    def test_version_is_current(self, fresh_db):
        # 📌 re-pinned 38 → 39 → 40 → 41 (2026-06-12; ENH-003
        # Verified column, ENH-006 vendor_day_verifications, and
        # ENH-006 rev 2 vendor_range_verifications; v2.1.0 still
        # unreleased, so all four migrations ship in one release).
        v = fresh_db.execute(
            "SELECT MAX(version) FROM schema_version").fetchone()[0]
        assert v == CURRENT_SCHEMA_VERSION == 41

    def test_payment_methods_columns(self, fresh_db):
        cols = _cols(fresh_db, 'payment_methods')
        assert 'external_matching_accepted' in cols
        assert 'vendor_cashes_original' in cols

    def test_fmnp_entries_columns(self, fresh_db):
        cols = _cols(fresh_db, 'fmnp_entries')
        for c in ('payment_method_id', 'method_name_snapshot',
                  'match_percent_snapshot',
                  'vendor_cashes_original_snapshot',
                  'denomination_snapshot'):
            assert c in cols, f"missing column {c}"

    def test_triggers_and_index_present(self, fresh_db):
        trig = _triggers(fresh_db)
        assert 'chk_fmnp_entry_method_insert' in trig
        assert 'chk_fmnp_entry_snapshot_immutable' in trig
        idx = {row[0] for row in fresh_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert 'idx_fmnp_entries_method' in idx

    def test_seeded_fmnp_external_flags(self, fresh_db):
        """Load Defaults seeds FMNP external-enabled +
        cashes-original; everything else opted out."""
        from fam.database.seed import seed_sample_data
        assert seed_sample_data()
        rows = fresh_db.execute(
            "SELECT name, match_percent, external_matching_accepted,"
            " vendor_cashes_original FROM payment_methods"
            " WHERE COALESCE(is_system, 0) = 0").fetchall()
        by_name = {r['name']: r for r in rows}
        assert by_name['FMNP']['external_matching_accepted'] == 1
        assert by_name['FMNP']['vendor_cashes_original'] == 1
        for name in ('SNAP', 'Food RX', 'JH Food Bucks', 'JH Tokens',
                     'Cash'):
            assert by_name[name]['external_matching_accepted'] == 0
            assert by_name[name]['vendor_cashes_original'] == 0

    def test_seeded_food_bucks_zero_match(self, fresh_db):
        """ENH-001: reward-type scrip must not be matched again —
        the match was applied when the scrip was earned.  Drives
        both booth AND external ("Face only") math."""
        from fam.database.seed import seed_sample_data
        assert seed_sample_data()
        row = fresh_db.execute(
            "SELECT match_percent FROM payment_methods"
            " WHERE name = 'JH Food Bucks'").fetchone()
        assert row['match_percent'] == 0.0


# ──────────────────────────────────────────────────────────────────
# Direct v37 -> v38 migration
# ──────────────────────────────────────────────────────────────────


class TestV37ToV38Migration:

    def _seed_v37(self, conn, fmnp_match=100.0):
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent,"
            " is_active, sort_order, denomination, photo_required)"
            " VALUES (2, 'FMNP', ?, 0, 2, 500, 'Optional')",
            (fmnp_match,))
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent,"
            " is_active, sort_order, denomination)"
            " VALUES (3, 'Food RX', 100.0, 1, 3, 1000)")
        conn.execute(
            "INSERT INTO fmnp_entries (id, market_day_id, vendor_id,"
            " amount, check_count, entered_by)"
            " VALUES (1, 40, 20, 1000, 2, 'Coordinator')")
        conn.execute(
            "INSERT INTO fmnp_entries (id, market_day_id, vendor_id,"
            " amount, entered_by, status)"
            " VALUES (2, 40, 21, 500, 'Coordinator', 'Deleted')")
        conn.commit()

    def test_columns_added_and_fmnp_toggles_backfilled(self, v37_conn):
        self._seed_v37(v37_conn)
        _migrate_v37_to_v38(v37_conn)

        assert 'external_matching_accepted' in _cols(
            v37_conn, 'payment_methods')
        fmnp = v37_conn.execute(
            "SELECT * FROM payment_methods WHERE name='FMNP'"
        ).fetchone()
        assert fmnp['external_matching_accepted'] == 1
        assert fmnp['vendor_cashes_original'] == 1
        rx = v37_conn.execute(
            "SELECT * FROM payment_methods WHERE name='Food RX'"
        ).fetchone()
        assert rx['external_matching_accepted'] == 0
        assert rx['vendor_cashes_original'] == 0

    def test_entry_backfill_values(self, v37_conn):
        """EVERY pre-existing row (any status) backfills to the FMNP
        method with the snapshot set that reproduces payout == face."""
        self._seed_v37(v37_conn)
        _migrate_v37_to_v38(v37_conn)

        rows = v37_conn.execute(
            "SELECT * FROM fmnp_entries ORDER BY id").fetchall()
        assert len(rows) == 2
        for r in rows:
            assert r['payment_method_id'] == 2
            assert r['method_name_snapshot'] == 'FMNP'
            assert r['match_percent_snapshot'] == 100.0
            assert r['vendor_cashes_original_snapshot'] == 1
            assert r['denomination_snapshot'] is None

    def test_backfill_match_snapshot_is_literal_100_not_live_setting(
            self, v37_conn):
        """THE identity-preservation pin: today's code pays face
        value regardless of the configured FMNP match %.  A DB whose
        FMNP match was set to 55% must STILL backfill 100.0 — using
        the live value would re-value history (a $10 check would
        suddenly report a $5.50 payout)."""
        self._seed_v37(v37_conn, fmnp_match=55.0)
        _migrate_v37_to_v38(v37_conn)

        r = v37_conn.execute(
            "SELECT match_percent_snapshot FROM fmnp_entries"
            " WHERE id=1").fetchone()
        assert r['match_percent_snapshot'] == 100.0

        from fam.utils.external_payout import (
            compute_external_payout_cents)
        assert compute_external_payout_cents(1000, 100.0, True) == 1000

    def test_idempotent_rerun(self, v37_conn):
        self._seed_v37(v37_conn)
        _migrate_v37_to_v38(v37_conn)
        before = [dict(r) for r in v37_conn.execute(
            "SELECT * FROM fmnp_entries ORDER BY id").fetchall()]
        _migrate_v37_to_v38(v37_conn)  # must not raise
        after = [dict(r) for r in v37_conn.execute(
            "SELECT * FROM fmnp_entries ORDER BY id").fetchall()]
        assert before == after

    def test_defensive_fmnp_reinsert_when_method_missing(self):
        """Ancient-DB path: fmnp_entries rows exist but the FMNP
        method row is gone — the migration re-inserts FMNP so the
        backfill has a target."""
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.executescript(V37_DDL)
        conn.execute(
            "INSERT INTO fmnp_entries (id, market_day_id, vendor_id,"
            " amount, entered_by) VALUES (1, 40, 20, 500, 'C')")
        conn.commit()
        try:
            _migrate_v37_to_v38(conn)
            fmnp = conn.execute(
                "SELECT * FROM payment_methods WHERE name='FMNP'"
            ).fetchone()
            assert fmnp is not None
            assert fmnp['external_matching_accepted'] == 1
            assert fmnp['vendor_cashes_original'] == 1
            entry = conn.execute(
                "SELECT * FROM fmnp_entries WHERE id=1").fetchone()
            assert entry['payment_method_id'] == fmnp['id']
            assert entry['match_percent_snapshot'] == 100.0
        finally:
            conn.close()


# ──────────────────────────────────────────────────────────────────
# Triggers
# ──────────────────────────────────────────────────────────────────


class TestV38Triggers:

    def _fmnp_id(self, conn):
        return conn.execute(
            "SELECT id FROM payment_methods WHERE name='FMNP'"
        ).fetchone()[0]

    def _setup(self, fresh_db):
        from fam.database.seed import seed_sample_data
        assert seed_sample_data()
        fresh_db.execute(
            "INSERT INTO market_days (id, market_id, date, status,"
            " opened_by) VALUES (40, 1, '2026-04-26', 'Closed', 'T')")
        fresh_db.commit()

    def test_insert_without_method_blocked(self, fresh_db):
        self._setup(fresh_db)
        with pytest.raises(sqlite3.IntegrityError,
                           match='payment_method_id'):
            fresh_db.execute(
                "INSERT INTO fmnp_entries (market_day_id, vendor_id,"
                " amount, entered_by) VALUES (40, 1, 500, 'C')")

    def test_insert_with_full_snapshots_ok_and_null_denom_ok(
            self, fresh_db):
        """denomination_snapshot is exempt from the NOT-NULL rule —
        legacy no-denomination FMNP configs must keep working."""
        self._setup(fresh_db)
        pm = self._fmnp_id(fresh_db)
        fresh_db.execute(
            "INSERT INTO fmnp_entries (market_day_id, vendor_id,"
            " amount, entered_by, payment_method_id,"
            " method_name_snapshot, match_percent_snapshot,"
            " vendor_cashes_original_snapshot, denomination_snapshot)"
            " VALUES (40, 1, 500, 'C', ?, 'FMNP', 100.0, 1, NULL)",
            (pm,))
        fresh_db.commit()

    def test_snapshot_immutability_blocks_changes(self, fresh_db):
        self._setup(fresh_db)
        pm = self._fmnp_id(fresh_db)
        cur = fresh_db.execute(
            "INSERT INTO fmnp_entries (market_day_id, vendor_id,"
            " amount, entered_by, payment_method_id,"
            " method_name_snapshot, match_percent_snapshot,"
            " vendor_cashes_original_snapshot, denomination_snapshot)"
            " VALUES (40, 1, 500, 'C', ?, 'FMNP', 100.0, 1, 500)",
            (pm,))
        entry_id = cur.lastrowid
        fresh_db.commit()
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            fresh_db.execute(
                "UPDATE fmnp_entries SET match_percent_snapshot = 50.0"
                " WHERE id = ?", (entry_id,))
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            fresh_db.execute(
                "UPDATE fmnp_entries SET payment_method_id = NULL"
                " WHERE id = ?", (entry_id,))

    def test_normal_field_updates_still_work(self, fresh_db):
        """The immutability trigger watches only the snapshot
        columns — amount/vendor/notes edits (the supported edit
        surface) are untouched."""
        self._setup(fresh_db)
        pm = self._fmnp_id(fresh_db)
        cur = fresh_db.execute(
            "INSERT INTO fmnp_entries (market_day_id, vendor_id,"
            " amount, entered_by, payment_method_id,"
            " method_name_snapshot, match_percent_snapshot,"
            " vendor_cashes_original_snapshot, denomination_snapshot)"
            " VALUES (40, 1, 500, 'C', ?, 'FMNP', 100.0, 1, 500)",
            (pm,))
        entry_id = cur.lastrowid
        fresh_db.execute(
            "UPDATE fmnp_entries SET amount = 1000, notes = 'fixed'"
            " WHERE id = ?", (entry_id,))
        fresh_db.commit()

    def test_null_to_value_repair_allowed(self, fresh_db):
        """NULL -> value (backfill-style repair) is allowed; the
        immutability rule only locks values once set."""
        self._setup(fresh_db)
        pm = self._fmnp_id(fresh_db)
        cur = fresh_db.execute(
            "INSERT INTO fmnp_entries (market_day_id, vendor_id,"
            " amount, entered_by, payment_method_id,"
            " method_name_snapshot, match_percent_snapshot,"
            " vendor_cashes_original_snapshot, denomination_snapshot)"
            " VALUES (40, 1, 500, 'C', ?, 'FMNP', 100.0, 1, NULL)",
            (pm,))
        entry_id = cur.lastrowid
        fresh_db.execute(
            "UPDATE fmnp_entries SET denomination_snapshot = 500"
            " WHERE id = ?", (entry_id,))
        fresh_db.commit()

    def test_legacy_amount_trigger_still_fires(self, fresh_db):
        self._setup(fresh_db)
        pm = self._fmnp_id(fresh_db)
        with pytest.raises(sqlite3.IntegrityError,
                           match='FMNP amount'):
            fresh_db.execute(
                "INSERT INTO fmnp_entries (market_day_id, vendor_id,"
                " amount, entered_by, payment_method_id,"
                " method_name_snapshot, match_percent_snapshot,"
                " vendor_cashes_original_snapshot)"
                " VALUES (40, 1, 0, 'C', ?, 'FMNP', 100.0, 1)",
                (pm,))


# ──────────────────────────────────────────────────────────────────
# Downgrade guard
# ──────────────────────────────────────────────────────────────────


class TestDowngradeGuard:

    def test_newer_schema_refused(self, tmp_path):
        """A v38+ DB must refuse to open under an app whose ceiling
        is lower — this is the structural guarantee that old builds
        never misread non-FMNP external entries as FMNP checks."""
        db_file = str(tmp_path / "newer_schema.db")
        close_connection()
        set_db_path(db_file)
        initialize_database()
        conn = get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version)"
            " VALUES (?)", (CURRENT_SCHEMA_VERSION + 1,))
        conn.commit()
        close_connection()
        set_db_path(db_file)
        with pytest.raises(RuntimeError, match='newer than'):
            initialize_database()
        close_connection()
