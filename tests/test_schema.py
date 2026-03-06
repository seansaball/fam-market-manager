"""Tests for fam.database.schema — fresh install, migrations, constraints, triggers."""

import sqlite3

import pytest

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import (
    initialize_database,
    CURRENT_SCHEMA_VERSION,
)


# ──────────────────────────────────────────────────────────────────
# Fixture: fresh database per test
# ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_schema.db")
    close_connection()
    set_db_path(db_file)
    yield tmp_path, db_file
    close_connection()


# ──────────────────────────────────────────────────────────────────
# Fresh install
# ──────────────────────────────────────────────────────────────────
class TestFreshInstall:
    def test_creates_all_tables(self, fresh_db):
        initialize_database()
        conn = get_connection()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = sorted(t[0] for t in tables)
        expected = sorted([
            "markets", "vendors", "payment_methods", "market_days",
            "customer_orders", "transactions", "payment_line_items",
            "fmnp_entries", "audit_log", "market_vendors",
            "market_payment_methods", "app_settings", "schema_version",
        ])
        for t in expected:
            assert t in table_names, f"Missing table: {t}"

    def test_schema_version_set(self, fresh_db):
        initialize_database()
        conn = get_connection()
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] == CURRENT_SCHEMA_VERSION

    def test_returns_true(self, fresh_db):
        result = initialize_database()
        assert result is True

    def test_idempotent(self, fresh_db):
        """Running initialize_database twice should not error."""
        initialize_database()
        close_connection()
        set_db_path(fresh_db[1])
        result = initialize_database()
        assert result is True

    def test_wal_mode_enabled(self, fresh_db):
        initialize_database()
        conn = get_connection()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_foreign_keys_enabled(self, fresh_db):
        initialize_database()
        conn = get_connection()
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


# ──────────────────────────────────────────────────────────────────
# Table structure verification
# ──────────────────────────────────────────────────────────────────
class TestTableStructure:
    def test_markets_columns(self, fresh_db):
        initialize_database()
        conn = get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(markets)").fetchall()}
        assert {"id", "name", "address", "is_active", "daily_match_limit",
                "match_limit_active"}.issubset(cols)

    def test_vendors_columns(self, fresh_db):
        initialize_database()
        conn = get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(vendors)").fetchall()}
        assert {"id", "name", "contact_info", "is_active"}.issubset(cols)

    def test_payment_methods_columns(self, fresh_db):
        initialize_database()
        conn = get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(payment_methods)").fetchall()}
        assert {"id", "name", "match_percent", "is_active", "sort_order"}.issubset(cols)

    def test_transactions_columns(self, fresh_db):
        initialize_database()
        conn = get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()}
        expected = {"id", "fam_transaction_id", "market_day_id", "vendor_id",
                    "receipt_total", "receipt_number", "status", "snap_reference_code",
                    "confirmed_by", "confirmed_at", "created_at", "notes",
                    "customer_order_id"}
        assert expected.issubset(cols)

    def test_payment_line_items_columns(self, fresh_db):
        initialize_database()
        conn = get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(payment_line_items)").fetchall()}
        expected = {"id", "transaction_id", "payment_method_id",
                    "method_name_snapshot", "match_percent_snapshot",
                    "method_amount", "match_amount", "customer_charged", "created_at"}
        assert expected.issubset(cols)

    def test_fmnp_entries_has_status(self, fresh_db):
        initialize_database()
        conn = get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(fmnp_entries)").fetchall()}
        assert "status" in cols

    def test_customer_orders_has_zip_code(self, fresh_db):
        initialize_database()
        conn = get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(customer_orders)").fetchall()}
        assert "zip_code" in cols

    def test_market_vendors_unique_constraint(self, fresh_db):
        initialize_database()
        conn = get_connection()
        conn.execute("INSERT INTO markets (id, name) VALUES (1, 'M1')")
        conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V1')")
        conn.execute("INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, 1)")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, 1)")

    def test_market_payment_methods_unique_constraint(self, fresh_db):
        initialize_database()
        conn = get_connection()
        conn.execute("INSERT INTO markets (id, name) VALUES (1, 'M1')")
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent) VALUES (1, 'PM1', 50)"
        )
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, payment_method_id) VALUES (1, 1)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO market_payment_methods (market_id, payment_method_id) VALUES (1, 1)"
            )

    def test_app_settings_primary_key(self, fresh_db):
        initialize_database()
        conn = get_connection()
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('k1', 'v1')")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('k1', 'v2')")


# ──────────────────────────────────────────────────────────────────
# Triggers (constraint enforcement)
# ──────────────────────────────────────────────────────────────────
class TestTriggers:
    def _seed(self, conn):
        """Seed required parent rows for FK constraints."""
        conn.execute("INSERT INTO markets (id, name) VALUES (1, 'Market')")
        conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'Vendor')")
        conn.execute(
            "INSERT INTO market_days (id, market_id, date, status, opened_by)"
            " VALUES (1, 1, '2026-01-01', 'Open', 'Admin')"
        )
        conn.commit()

    def test_transaction_receipt_total_must_be_positive(self, fresh_db):
        initialize_database()
        conn = get_connection()
        self._seed(conn)
        with pytest.raises(sqlite3.IntegrityError, match="receipt_total must be > 0"):
            conn.execute(
                "INSERT INTO transactions (fam_transaction_id, market_day_id, vendor_id, receipt_total)"
                " VALUES ('FAM-001', 1, 1, 0)"
            )

    def test_transaction_negative_receipt_total(self, fresh_db):
        initialize_database()
        conn = get_connection()
        self._seed(conn)
        with pytest.raises(sqlite3.IntegrityError, match="receipt_total must be > 0"):
            conn.execute(
                "INSERT INTO transactions (fam_transaction_id, market_day_id, vendor_id, receipt_total)"
                " VALUES ('FAM-001', 1, 1, -5.00)"
            )

    def test_transaction_positive_receipt_total_ok(self, fresh_db):
        initialize_database()
        conn = get_connection()
        self._seed(conn)
        conn.execute(
            "INSERT INTO transactions (fam_transaction_id, market_day_id, vendor_id, receipt_total)"
            " VALUES ('FAM-001', 1, 1, 10.00)"
        )
        conn.commit()  # Should not raise

    def test_payment_line_item_negative_method_amount(self, fresh_db):
        initialize_database()
        conn = get_connection()
        self._seed(conn)
        conn.execute(
            "INSERT INTO transactions (id, fam_transaction_id, market_day_id, vendor_id, receipt_total)"
            " VALUES (1, 'FAM-001', 1, 1, 10.00)"
        )
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent) VALUES (1, 'Cash', 0)"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="method_amount must be >= 0"):
            conn.execute(
                "INSERT INTO payment_line_items"
                " (transaction_id, payment_method_id, method_name_snapshot,"
                "  match_percent_snapshot, method_amount, match_amount, customer_charged)"
                " VALUES (1, 1, 'Cash', 0, -5.00, 0, -5.00)"
            )

    def test_fmnp_amount_must_be_positive(self, fresh_db):
        initialize_database()
        conn = get_connection()
        self._seed(conn)
        with pytest.raises(sqlite3.IntegrityError, match="FMNP amount must be > 0"):
            conn.execute(
                "INSERT INTO fmnp_entries (market_day_id, vendor_id, amount, entered_by)"
                " VALUES (1, 1, 0, 'Admin')"
            )

    def test_fmnp_negative_amount(self, fresh_db):
        initialize_database()
        conn = get_connection()
        self._seed(conn)
        with pytest.raises(sqlite3.IntegrityError, match="FMNP amount must be > 0"):
            conn.execute(
                "INSERT INTO fmnp_entries (market_day_id, vendor_id, amount, entered_by)"
                " VALUES (1, 1, -10.00, 'Admin')"
            )

    def test_fmnp_update_amount_must_be_positive(self, fresh_db):
        initialize_database()
        conn = get_connection()
        self._seed(conn)
        conn.execute(
            "INSERT INTO fmnp_entries (id, market_day_id, vendor_id, amount, entered_by)"
            " VALUES (1, 1, 1, 5.00, 'Admin')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="FMNP amount must be > 0"):
            conn.execute("UPDATE fmnp_entries SET amount = 0 WHERE id = 1")

    def test_match_percent_range_insert(self, fresh_db):
        initialize_database()
        conn = get_connection()
        with pytest.raises(sqlite3.IntegrityError, match="match_percent must be between"):
            conn.execute(
                "INSERT INTO payment_methods (name, match_percent) VALUES ('Bad', -1)"
            )

    def test_match_percent_over_999(self, fresh_db):
        initialize_database()
        conn = get_connection()
        with pytest.raises(sqlite3.IntegrityError, match="match_percent must be between"):
            conn.execute(
                "INSERT INTO payment_methods (name, match_percent) VALUES ('Bad', 1000)"
            )

    def test_match_percent_valid_range(self, fresh_db):
        initialize_database()
        conn = get_connection()
        # 0 and 999 should both be valid
        conn.execute(
            "INSERT INTO payment_methods (name, match_percent) VALUES ('Zero', 0)"
        )
        conn.execute(
            "INSERT INTO payment_methods (name, match_percent) VALUES ('Max', 999)"
        )
        conn.commit()  # Should not raise

    def test_match_percent_update_trigger(self, fresh_db):
        initialize_database()
        conn = get_connection()
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent) VALUES (1, 'Test', 50)"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="match_percent must be between"):
            conn.execute("UPDATE payment_methods SET match_percent = -5 WHERE id = 1")


# ──────────────────────────────────────────────────────────────────
# Indexes
# ──────────────────────────────────────────────────────────────────
class TestIndexes:
    def test_performance_indexes_exist(self, fresh_db):
        initialize_database()
        conn = get_connection()
        indexes = {r[1] for r in conn.execute(
            "SELECT * FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        expected_indexes = [
            "idx_transactions_market_day",
            "idx_transactions_status",
            "idx_transactions_fam_id",
            "idx_payment_items_txn",
            "idx_fmnp_market_day",
            "idx_audit_log_changed_at",
        ]
        for idx in expected_indexes:
            assert idx in indexes, f"Missing index: {idx}"


# ──────────────────────────────────────────────────────────────────
# Migration chain (simulate upgrading from an older version)
# ──────────────────────────────────────────────────────────────────
class TestMigrationChain:
    def _create_v7_db(self, db_file):
        """Create a v7 database to test the remaining migration chain (v7→current).

        We start at v7 (post-rename, post-triggers) because the current
        v3→v4 migration code references column names (match_amount) that
        only exist after v6.  In practice no real users have a pre-v7 DB.
        """
        conn = sqlite3.connect(db_file)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        conn.executescript("""
            CREATE TABLE markets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                address TEXT,
                is_active BOOLEAN DEFAULT 1,
                daily_match_limit REAL DEFAULT 100.00,
                match_limit_active BOOLEAN DEFAULT 1
            );
            CREATE TABLE vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                contact_info TEXT,
                is_active BOOLEAN DEFAULT 1
            );
            CREATE TABLE payment_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                match_percent REAL NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                sort_order INTEGER DEFAULT 0
            );
            CREATE TABLE market_days (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                status TEXT DEFAULT 'Open',
                opened_by TEXT, closed_by TEXT, closed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (market_id) REFERENCES markets(id)
            );
            CREATE TABLE customer_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_day_id INTEGER NOT NULL,
                customer_label TEXT NOT NULL,
                zip_code TEXT,
                status TEXT DEFAULT 'Draft',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (market_day_id) REFERENCES market_days(id)
            );
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fam_transaction_id TEXT NOT NULL UNIQUE,
                market_day_id INTEGER NOT NULL,
                vendor_id INTEGER NOT NULL,
                receipt_total REAL NOT NULL,
                receipt_number TEXT,
                status TEXT DEFAULT 'Draft',
                snap_reference_code TEXT,
                confirmed_by TEXT, confirmed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                notes TEXT,
                customer_order_id INTEGER,
                FOREIGN KEY (market_day_id) REFERENCES market_days(id),
                FOREIGN KEY (vendor_id) REFERENCES vendors(id),
                FOREIGN KEY (customer_order_id) REFERENCES customer_orders(id)
            );
            CREATE TABLE payment_line_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id INTEGER NOT NULL,
                payment_method_id INTEGER NOT NULL,
                method_name_snapshot TEXT NOT NULL,
                match_percent_snapshot REAL NOT NULL,
                method_amount REAL NOT NULL,
                match_amount REAL NOT NULL,
                customer_charged REAL NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (transaction_id) REFERENCES transactions(id),
                FOREIGN KEY (payment_method_id) REFERENCES payment_methods(id)
            );
            CREATE TABLE fmnp_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_day_id INTEGER NOT NULL,
                vendor_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                check_count INTEGER,
                notes TEXT,
                entered_by TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY (market_day_id) REFERENCES market_days(id),
                FOREIGN KEY (vendor_id) REFERENCES vendors(id)
            );
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                record_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                field_name TEXT, old_value TEXT, new_value TEXT,
                reason_code TEXT, notes TEXT,
                changed_by TEXT NOT NULL,
                changed_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE market_vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id INTEGER NOT NULL,
                vendor_id INTEGER NOT NULL,
                FOREIGN KEY (market_id) REFERENCES markets(id),
                FOREIGN KEY (vendor_id) REFERENCES vendors(id),
                UNIQUE(market_id, vendor_id)
            );
            CREATE TABLE schema_version (
                version INTEGER,
                applied_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO schema_version (version) VALUES (7);
        """)

        # Seed data to verify it survives migration
        conn.execute(
            "INSERT INTO markets (name, address) VALUES ('Old Market', '100 Old St')"
        )
        conn.execute("INSERT INTO vendors (name) VALUES ('Old Vendor')")
        conn.execute(
            "INSERT INTO payment_methods (name, match_percent, sort_order)"
            " VALUES ('Token', 50.0, 1)"
        )
        conn.commit()
        conn.close()

    def test_v7_to_current(self, fresh_db):
        """Migrating a v7 database to current should produce all tables and columns."""
        tmp_path, _ = fresh_db
        db_file = str(tmp_path / "v7_migrate.db")
        close_connection()
        self._create_v7_db(db_file)
        set_db_path(db_file)

        result = initialize_database()
        assert result is True

        conn = get_connection()

        # Schema version should be current
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        assert version == CURRENT_SCHEMA_VERSION

        # All tables should exist (v8+ additions)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "market_payment_methods" in tables
        assert "app_settings" in tables

        # fmnp_entries should have status column (v11)
        fmnp_cols = {r[1] for r in conn.execute("PRAGMA table_info(fmnp_entries)").fetchall()}
        assert "status" in fmnp_cols

    def test_data_survives_migration(self, fresh_db):
        """Pre-existing data should not be lost during migration."""
        tmp_path, _ = fresh_db
        db_file = str(tmp_path / "v7_data.db")
        close_connection()
        self._create_v7_db(db_file)
        set_db_path(db_file)

        initialize_database()
        conn = get_connection()

        market = conn.execute("SELECT name FROM markets WHERE name='Old Market'").fetchone()
        assert market is not None

        vendor = conn.execute("SELECT name FROM vendors WHERE name='Old Vendor'").fetchone()
        assert vendor is not None

        pm = conn.execute(
            "SELECT name, match_percent FROM payment_methods WHERE name='Token'"
        ).fetchone()
        assert pm is not None
        assert pm[1] == 50.0

    def test_pre_migration_backup_created(self, fresh_db):
        """When migrating from an older version, a .pre-migration.bak should be created."""
        tmp_path, _ = fresh_db
        db_file = str(tmp_path / "v7_bak.db")
        close_connection()
        self._create_v7_db(db_file)
        set_db_path(db_file)

        initialize_database()

        bak_path = db_file + ".pre-migration.bak"
        assert os.path.exists(bak_path), "Pre-migration backup should exist"

    def test_no_backup_on_fresh_install(self, fresh_db):
        """Fresh install should NOT create a .pre-migration.bak file."""
        _, db_file = fresh_db
        initialize_database()
        bak_path = db_file + ".pre-migration.bak"
        assert not os.path.exists(bak_path)


# Need os for the backup file checks
import os


# ──────────────────────────────────────────────────────────────────
# Defaults verification
# ──────────────────────────────────────────────────────────────────
class TestDefaults:
    def test_market_defaults(self, fresh_db):
        initialize_database()
        conn = get_connection()
        conn.execute("INSERT INTO markets (name) VALUES ('Test')")
        conn.commit()
        row = conn.execute("SELECT * FROM markets WHERE name='Test'").fetchone()
        assert row["is_active"] == 1
        assert row["daily_match_limit"] == 100.00
        assert row["match_limit_active"] == 1

    def test_vendor_defaults(self, fresh_db):
        initialize_database()
        conn = get_connection()
        conn.execute("INSERT INTO vendors (name) VALUES ('Test')")
        conn.commit()
        row = conn.execute("SELECT * FROM vendors WHERE name='Test'").fetchone()
        assert row["is_active"] == 1

    def test_market_day_defaults(self, fresh_db):
        initialize_database()
        conn = get_connection()
        conn.execute("INSERT INTO markets (id, name) VALUES (1, 'M')")
        conn.execute(
            "INSERT INTO market_days (market_id, date) VALUES (1, '2026-01-01')"
        )
        conn.commit()
        row = conn.execute("SELECT * FROM market_days").fetchone()
        assert row["status"] == "Open"

    def test_transaction_defaults(self, fresh_db):
        initialize_database()
        conn = get_connection()
        conn.execute("INSERT INTO markets (id, name) VALUES (1, 'M')")
        conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
        conn.execute(
            "INSERT INTO market_days (id, market_id, date) VALUES (1, 1, '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO transactions (fam_transaction_id, market_day_id, vendor_id, receipt_total)"
            " VALUES ('FAM-001', 1, 1, 10.00)"
        )
        conn.commit()
        row = conn.execute("SELECT * FROM transactions").fetchone()
        assert row["status"] == "Draft"

    def test_fmnp_entry_defaults(self, fresh_db):
        initialize_database()
        conn = get_connection()
        conn.execute("INSERT INTO markets (id, name) VALUES (1, 'M')")
        conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
        conn.execute(
            "INSERT INTO market_days (id, market_id, date) VALUES (1, 1, '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO fmnp_entries (market_day_id, vendor_id, amount, entered_by)"
            " VALUES (1, 1, 5.00, 'Admin')"
        )
        conn.commit()
        row = conn.execute("SELECT * FROM fmnp_entries").fetchone()
        assert row["status"] == "Active"

    def test_customer_order_defaults(self, fresh_db):
        initialize_database()
        conn = get_connection()
        conn.execute("INSERT INTO markets (id, name) VALUES (1, 'M')")
        conn.execute(
            "INSERT INTO market_days (id, market_id, date) VALUES (1, 1, '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO customer_orders (market_day_id, customer_label)"
            " VALUES (1, 'C-001')"
        )
        conn.commit()
        row = conn.execute("SELECT * FROM customer_orders").fetchone()
        assert row["status"] == "Draft"
