"""Database schema creation and migrations."""

import logging
import os
import shutil
import sqlite3

from .connection import get_connection, get_db_path

logger = logging.getLogger('fam.database.schema')

CURRENT_SCHEMA_VERSION = 11

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    address TEXT,
    is_active BOOLEAN DEFAULT 1,
    daily_match_limit REAL DEFAULT 100.00,
    match_limit_active BOOLEAN DEFAULT 1
);

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    contact_info TEXT,
    is_active BOOLEAN DEFAULT 1
);

CREATE TABLE IF NOT EXISTS payment_methods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    match_percent REAL NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS market_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    status TEXT DEFAULT 'Open',
    opened_by TEXT,
    closed_by TEXT,
    closed_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (market_id) REFERENCES markets(id)
);

CREATE TABLE IF NOT EXISTS customer_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_day_id INTEGER NOT NULL,
    customer_label TEXT NOT NULL,
    zip_code TEXT,
    status TEXT DEFAULT 'Draft',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (market_day_id) REFERENCES market_days(id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fam_transaction_id TEXT NOT NULL UNIQUE,
    market_day_id INTEGER NOT NULL,
    vendor_id INTEGER NOT NULL,
    receipt_total REAL NOT NULL,
    receipt_number TEXT,
    status TEXT DEFAULT 'Draft',
    snap_reference_code TEXT,
    confirmed_by TEXT,
    confirmed_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    customer_order_id INTEGER,
    FOREIGN KEY (market_day_id) REFERENCES market_days(id),
    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
    FOREIGN KEY (customer_order_id) REFERENCES customer_orders(id)
);

CREATE TABLE IF NOT EXISTS payment_line_items (
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

CREATE TABLE IF NOT EXISTS fmnp_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_day_id INTEGER NOT NULL,
    vendor_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    check_count INTEGER,
    notes TEXT,
    entered_by TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    status TEXT DEFAULT 'Active',
    FOREIGN KEY (market_day_id) REFERENCES market_days(id),
    FOREIGN KEY (vendor_id) REFERENCES vendors(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    field_name TEXT,
    old_value TEXT,
    new_value TEXT,
    reason_code TEXT,
    notes TEXT,
    changed_by TEXT NOT NULL,
    changed_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id INTEGER NOT NULL,
    vendor_id INTEGER NOT NULL,
    FOREIGN KEY (market_id) REFERENCES markets(id),
    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
    UNIQUE(market_id, vendor_id)
);

CREATE TABLE IF NOT EXISTS market_payment_methods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id INTEGER NOT NULL,
    payment_method_id INTEGER NOT NULL,
    FOREIGN KEY (market_id) REFERENCES markets(id),
    FOREIGN KEY (payment_method_id) REFERENCES payment_methods(id),
    UNIQUE(market_id, payment_method_id)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER,
    applied_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _migrate_v1_to_v2(conn):
    """Add customer_orders table and customer_order_id column to transactions."""
    # Create new table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_day_id INTEGER NOT NULL,
            customer_label TEXT NOT NULL,
            status TEXT DEFAULT 'Draft',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (market_day_id) REFERENCES market_days(id)
        )
    """)

    # Add customer_order_id to transactions (if not already present)
    try:
        conn.execute("ALTER TABLE transactions ADD COLUMN customer_order_id INTEGER REFERENCES customer_orders(id)")
    except sqlite3.OperationalError:
        pass  # Column already exists

    conn.commit()


def _migrate_v2_to_v3(conn):
    """Add market_vendors junction table for assigning vendors to markets."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id INTEGER NOT NULL,
            vendor_id INTEGER NOT NULL,
            FOREIGN KEY (market_id) REFERENCES markets(id),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id),
            UNIQUE(market_id, vendor_id)
        )
    """)
    conn.commit()


def _migrate_v3_to_v4(conn):
    """Add CHECK-constraint triggers, performance indexes, and audit indexes.

    SQLite cannot ALTER TABLE ADD CHECK, so we use BEFORE INSERT/UPDATE
    triggers to enforce constraints on monetary and percentage fields.
    """
    logger.info("Running migration v3 to v4: triggers + indexes")

    trigger_sql = """
    -- receipt_total must be > 0
    CREATE TRIGGER IF NOT EXISTS chk_transaction_amount_insert
    BEFORE INSERT ON transactions
    BEGIN
        SELECT RAISE(ABORT, 'receipt_total must be > 0')
        WHERE NEW.receipt_total <= 0;
    END;

    CREATE TRIGGER IF NOT EXISTS chk_transaction_amount_update
    BEFORE UPDATE OF receipt_total ON transactions
    BEGIN
        SELECT RAISE(ABORT, 'receipt_total must be > 0')
        WHERE NEW.receipt_total <= 0;
    END;

    -- payment_line_items amounts must be >= 0
    CREATE TRIGGER IF NOT EXISTS chk_payment_amount_insert
    BEFORE INSERT ON payment_line_items
    BEGIN
        SELECT RAISE(ABORT, 'method_amount must be >= 0')
        WHERE NEW.method_amount < 0;
        SELECT RAISE(ABORT, 'match_amount must be >= 0')
        WHERE NEW.match_amount < 0;
    END;

    -- FMNP amount must be > 0
    CREATE TRIGGER IF NOT EXISTS chk_fmnp_amount_insert
    BEFORE INSERT ON fmnp_entries
    BEGIN
        SELECT RAISE(ABORT, 'FMNP amount must be > 0')
        WHERE NEW.amount <= 0;
    END;

    CREATE TRIGGER IF NOT EXISTS chk_fmnp_amount_update
    BEFORE UPDATE OF amount ON fmnp_entries
    BEGIN
        SELECT RAISE(ABORT, 'FMNP amount must be > 0')
        WHERE NEW.amount <= 0;
    END;

    -- match_percent must be between 0 and 999
    CREATE TRIGGER IF NOT EXISTS chk_match_percent_insert
    BEFORE INSERT ON payment_methods
    BEGIN
        SELECT RAISE(ABORT, 'match_percent must be between 0 and 999')
        WHERE NEW.match_percent < 0 OR NEW.match_percent > 999;
    END;

    CREATE TRIGGER IF NOT EXISTS chk_match_percent_update
    BEFORE UPDATE OF match_percent ON payment_methods
    BEGIN
        SELECT RAISE(ABORT, 'match_percent must be between 0 and 999')
        WHERE NEW.match_percent < 0 OR NEW.match_percent > 999;
    END;

    -- Performance indexes
    CREATE INDEX IF NOT EXISTS idx_transactions_market_day ON transactions(market_day_id);
    CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
    CREATE INDEX IF NOT EXISTS idx_transactions_fam_id ON transactions(fam_transaction_id);
    CREATE INDEX IF NOT EXISTS idx_payment_items_txn ON payment_line_items(transaction_id);
    CREATE INDEX IF NOT EXISTS idx_fmnp_market_day ON fmnp_entries(market_day_id);
    CREATE INDEX IF NOT EXISTS idx_audit_log_changed_at ON audit_log(changed_at);
    """

    conn.executescript(trigger_sql)
    conn.commit()
    logger.info("Migration v3->v4 complete: 8 triggers + 6 indexes created")


def _migrate_v4_to_v5(conn):
    """Add daily_match_limit and match_limit_active to markets table."""
    logger.info("Running migration v4 to v5: daily match limit columns")
    try:
        conn.execute(
            "ALTER TABLE markets ADD COLUMN daily_match_limit REAL DEFAULT 100.00"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE markets ADD COLUMN match_limit_active BOOLEAN DEFAULT 1"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()
    logger.info("Migration v4->v5 complete: daily_match_limit + match_limit_active added")


def _migrate_v5_to_v6(conn):
    """Rename discount columns to match columns; widen percent range to 0-999."""
    logger.info("Running migration v5 to v6: discount -> match rename")

    # Rename columns (requires SQLite 3.25.0+; Python 3.12 bundles 3.41+)
    conn.execute(
        "ALTER TABLE payment_methods RENAME COLUMN discount_percent TO match_percent"
    )
    conn.execute(
        "ALTER TABLE payment_line_items"
        " RENAME COLUMN discount_percent_snapshot TO match_percent_snapshot"
    )
    conn.execute(
        "ALTER TABLE payment_line_items"
        " RENAME COLUMN discount_amount TO match_amount"
    )

    # Drop old triggers
    conn.execute("DROP TRIGGER IF EXISTS chk_discount_percent_insert")
    conn.execute("DROP TRIGGER IF EXISTS chk_discount_percent_update")
    conn.execute("DROP TRIGGER IF EXISTS chk_payment_amount_insert")

    # Recreate triggers with new column names and expanded range
    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS chk_match_percent_insert
        BEFORE INSERT ON payment_methods
        BEGIN
            SELECT RAISE(ABORT, 'match_percent must be between 0 and 999')
            WHERE NEW.match_percent < 0 OR NEW.match_percent > 999;
        END;

        CREATE TRIGGER IF NOT EXISTS chk_match_percent_update
        BEFORE UPDATE OF match_percent ON payment_methods
        BEGIN
            SELECT RAISE(ABORT, 'match_percent must be between 0 and 999')
            WHERE NEW.match_percent < 0 OR NEW.match_percent > 999;
        END;

        CREATE TRIGGER IF NOT EXISTS chk_payment_amount_insert
        BEFORE INSERT ON payment_line_items
        BEGIN
            SELECT RAISE(ABORT, 'method_amount must be >= 0')
            WHERE NEW.method_amount < 0;
            SELECT RAISE(ABORT, 'match_amount must be >= 0')
            WHERE NEW.match_amount < 0;
        END;
    """)

    conn.commit()
    logger.info("Migration v5->v6 complete: discount -> match rename done")


def _migrate_v6_to_v7(conn):
    """Add zip_code column to customer_orders for geolocation tracking."""
    logger.info("Running migration v6 to v7: add zip_code to customer_orders")
    try:
        conn.execute("ALTER TABLE customer_orders ADD COLUMN zip_code TEXT")
    except sqlite3.OperationalError as e:
        logger.warning("zip_code column may already exist: %s", e)
    conn.commit()
    logger.info("Migration v6->v7 complete: zip_code column added")


def _migrate_v7_to_v8(conn):
    """Add FMNP as a default payment method (100% match)."""
    logger.info("Running migration v7 to v8: add FMNP payment method")
    try:
        conn.execute(
            "INSERT INTO payment_methods (name, match_percent, is_active, sort_order)"
            " VALUES ('FMNP', 100.0, 1, 6)"
        )
    except sqlite3.IntegrityError as e:
        # FMNP may already exist if user added it manually
        logger.warning("FMNP payment method may already exist: %s", e)
    conn.commit()
    logger.info("Migration v7->v8 complete: FMNP payment method added")


def _migrate_v8_to_v9(conn):
    """Add market_payment_methods junction table and auto-assign all methods to all markets."""
    logger.info("Running migration v8 to v9: market_payment_methods table")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_payment_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id INTEGER NOT NULL,
            payment_method_id INTEGER NOT NULL,
            FOREIGN KEY (market_id) REFERENCES markets(id),
            FOREIGN KEY (payment_method_id) REFERENCES payment_methods(id),
            UNIQUE(market_id, payment_method_id)
        )
    """)
    # Auto-assign all active payment methods to all active markets
    # so existing users see no behavior change
    conn.execute("""
        INSERT OR IGNORE INTO market_payment_methods (market_id, payment_method_id)
        SELECT m.id, pm.id
        FROM markets m
        CROSS JOIN payment_methods pm
        WHERE m.is_active = 1 AND pm.is_active = 1
    """)
    conn.commit()
    logger.info("Migration v8->v9 complete: market_payment_methods table created")


def _migrate_v9_to_v10(conn):
    """Add app_settings key-value table for application preferences."""
    logger.info("Running migration v9 to v10: app_settings table")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    logger.info("Migration v9->v10 complete: app_settings table created")


def _migrate_v10_to_v11(conn):
    """Add status column to fmnp_entries for soft-delete support."""
    logger.info("Running migration v10 to v11: fmnp_entries soft-delete")
    conn.execute(
        "ALTER TABLE fmnp_entries ADD COLUMN status TEXT DEFAULT 'Active'"
    )
    conn.execute("UPDATE fmnp_entries SET status = 'Active' WHERE status IS NULL")
    conn.commit()
    logger.info("Migration v10->v11 complete: fmnp_entries.status column added")


def initialize_database():
    """Create all tables and set schema version if needed."""
    conn = get_connection()
    cursor = conn.cursor()

    # Check if schema_version table exists
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    has_version_table = cursor.fetchone() is not None

    if has_version_table:
        cursor.execute("SELECT MAX(version) FROM schema_version")
        row = cursor.fetchone()
        current_version = row[0] if row and row[0] else 0
    else:
        current_version = 0

    if current_version < 1:
        # Fresh install — create all tables + triggers/indexes
        conn.executescript(TABLES_SQL)
        _migrate_v3_to_v4(conn)
        # v5 columns already in TABLES_SQL for fresh installs
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,)
        )
        conn.commit()
        logger.info("Fresh install: schema at version %s", CURRENT_SCHEMA_VERSION)
        return True

    # ── Pre-migration backup ──────────────────────────────────
    # Snapshot the database before modifying it so a failed
    # migration can be recovered by restoring the .bak file.
    if current_version < CURRENT_SCHEMA_VERSION:
        db_file = get_db_path()
        if os.path.exists(db_file):
            backup_path = db_file + '.pre-migration.bak'
            try:
                shutil.copy2(db_file, backup_path)
                logger.info("Pre-migration backup created: %s (v%s → v%s)",
                            backup_path, current_version, CURRENT_SCHEMA_VERSION)
            except Exception:
                logger.warning("Could not create pre-migration backup", exc_info=True)

    if current_version < 2:
        _migrate_v1_to_v2(conn)
        current_version = 2

    if current_version < 3:
        _migrate_v2_to_v3(conn)
        current_version = 3

    if current_version < 4:
        _migrate_v3_to_v4(conn)
        current_version = 4

    if current_version < 5:
        _migrate_v4_to_v5(conn)
        current_version = 5

    if current_version < 6:
        _migrate_v5_to_v6(conn)
        current_version = 6

    if current_version < 7:
        _migrate_v6_to_v7(conn)
        current_version = 7

    if current_version < 8:
        _migrate_v7_to_v8(conn)
        current_version = 8

    if current_version < 9:
        _migrate_v8_to_v9(conn)
        current_version = 9

    if current_version < 10:
        _migrate_v9_to_v10(conn)
        current_version = 10

    if current_version < 11:
        _migrate_v10_to_v11(conn)
        current_version = 11

    # Record the final version (avoid duplicate if already at this version)
    existing = conn.execute(
        "SELECT version FROM schema_version WHERE version = ?",
        (CURRENT_SCHEMA_VERSION,)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,)
        )
    conn.commit()
    logger.info("Schema at version %s", CURRENT_SCHEMA_VERSION)
    return True
