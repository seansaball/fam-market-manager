"""Database schema creation and migrations."""

import logging
import os
import re
import shutil
import sqlite3

from datetime import datetime

from .connection import get_connection, get_db_path

logger = logging.getLogger('fam.database.schema')

CURRENT_SCHEMA_VERSION = 37

# v2.0.1: number of versioned pre-migration .bak files to retain
# alongside the rolling runtime backups in ``backups/``.
_MAX_PRE_MIGRATION_BAKS = 5


def _write_pre_migration_backup(conn, db_file: str,
                                from_version: int, to_version: int) -> str:
    """Copy the live DB to a versioned ``.pre-migration-vN-TS.bak`` file.

    Uses :meth:`sqlite3.Connection.backup` so any uncheckpointed
    commits in the WAL are included.  Old plain
    ``.pre-migration.bak`` (pre-v2.0.1) files get rotated into
    the same versioned naming on next upgrade.

    Returns the absolute path to the new backup file.
    """
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    new_path = (
        f"{db_file}.pre-migration-v{from_version}-to-v{to_version}"
        f"-{ts}.bak"
    )
    # SQLite backup API: opens dest, calls .backup(src), closes.
    dest = sqlite3.connect(new_path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    logger.info("Pre-migration backup created: %s (v%s → v%s)",
                new_path, from_version, to_version)

    # Keep only the most recent N snapshots (lex sort works on
    # ``YYYYMMDD_HHMMSS`` suffix).  Sweep both old and new naming
    # so legacy ``.pre-migration.bak`` is included in retention.
    try:
        _prune_pre_migration_backups(os.path.dirname(db_file) or '.',
                                     os.path.basename(db_file))
    except Exception:
        # v2.0.1: bumped from debug → warning.  A failure here means
        # old .bak files accumulate indefinitely — non-fatal but the
        # operator should see it in the Error Log report.
        logger.warning(
            "Pre-migration backup retention sweep failed",
            exc_info=True)
    return new_path


def _prune_pre_migration_backups(dir_path: str, db_basename: str) -> None:
    """Keep at most ``_MAX_PRE_MIGRATION_BAKS`` versioned snapshots."""
    pattern = re.compile(
        re.escape(db_basename) + r'\.pre-migration(-v\d+-to-v\d+-\d{8}_\d{6})?\.bak$'
    )
    candidates = []
    for name in os.listdir(dir_path):
        if pattern.match(name):
            full = os.path.join(dir_path, name)
            try:
                candidates.append((os.path.getmtime(full), full))
            except OSError:
                continue
    candidates.sort()  # oldest first
    excess = max(0, len(candidates) - _MAX_PRE_MIGRATION_BAKS)
    for _, path in candidates[:excess]:
        try:
            os.remove(path)
            logger.info("Pruned old pre-migration backup: %s", path)
        except OSError:
            logger.debug("Could not remove %s", path, exc_info=True)

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    address TEXT,
    is_active BOOLEAN DEFAULT 1,
    daily_match_limit INTEGER DEFAULT 10000,
    match_limit_active BOOLEAN DEFAULT 1
);

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    contact_info TEXT,
    is_active BOOLEAN DEFAULT 1,
    check_payable_to TEXT,
    street TEXT,
    city TEXT,
    state TEXT,
    zip_code TEXT,
    ach_enabled BOOLEAN DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payment_methods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    match_percent REAL NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    sort_order INTEGER DEFAULT 0,
    denomination INTEGER DEFAULT NULL,
    photo_required TEXT DEFAULT NULL,
    is_system BOOLEAN DEFAULT 0
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
    receipt_total INTEGER NOT NULL,
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
    method_amount INTEGER NOT NULL,
    match_amount INTEGER NOT NULL,
    customer_charged INTEGER NOT NULL,
    -- v2.0.7 (schema v36): customer-side denomination forfeit
    -- (Phase B).  Set ONLY when a denominated unit's face value
    -- exceeded the receipt's remaining capacity AND FAM match
    -- couldn't fully absorb the gap.  ``customer_charged +
    -- customer_forfeit_cents == unit_count × denomination`` —
    -- i.e. the customer's physical handout is recoverable.
    -- Always 0 for non-denom rows and for normal denom rows
    -- where Phase A (FAM match reduction) covered the full
    -- overage.  See FINANCIAL_FORMULA.md §3b.1 for the policy.
    customer_forfeit_cents INTEGER NOT NULL DEFAULT 0,
    -- v2.0.7+ (schema v37, audit 2026-05-07): user-cap flag.
    -- TRUE when the volunteer explicitly typed this row's
    -- charge value (or toggled the ⚡ icon to Locked).  The
    -- engine's cap-aware paths preserve customer_charged for
    -- user-capped rows; Auto-Distribute skips them.
    -- Persisting this flag means a Locked row survives draft
    -- save/restore and adjustment round-trips — without it,
    -- the volunteer's intent silently resets to "auto-fillable"
    -- on every reload, and a tightening cap could re-inflate
    -- a value the volunteer had pinned.
    -- Always 0 for denom rows (they have their own implicit
    -- lock via physical scrip) and for legacy pre-v37 rows
    -- (the migration backfills 0 for everyone).
    user_capped INTEGER NOT NULL DEFAULT 0,
    photo_path TEXT DEFAULT NULL,
    photo_drive_url TEXT DEFAULT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (transaction_id) REFERENCES transactions(id),
    FOREIGN KEY (payment_method_id) REFERENCES payment_methods(id)
);

CREATE TABLE IF NOT EXISTS fmnp_entries (
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
    changed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    app_version TEXT DEFAULT NULL,
    device_id TEXT DEFAULT NULL
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

CREATE TABLE IF NOT EXISTS vendor_payment_methods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id INTEGER NOT NULL,
    payment_method_id INTEGER NOT NULL,
    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
    FOREIGN KEY (payment_method_id) REFERENCES payment_methods(id),
    UNIQUE(vendor_id, payment_method_id)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS photo_hashes (
    content_hash TEXT PRIMARY KEY,
    drive_url TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS local_photo_hashes (
    content_hash TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER,
    applied_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Rewards program (v1.9.10 / schema v29).
--
-- IMPORTANT: this table holds CONFIG ONLY — the rules a market
-- coordinator sets for a customer-facing token-reward program
-- (e.g. "for every $5 of SNAP processed, hand the customer a $2
-- JH Food Bucks token").  The rewards themselves are NEVER stored
-- against transactions or payment_line_items — they are derived
-- on demand from this config × the source-method customer_charged
-- totals.  See ``fam/utils/rewards.py``.
--
-- Rewards do NOT participate in:
--   * Vendor reimbursement (vendors don't see/redeem these tokens)
--   * FAM match calculations
--   * Per-line invariant (customer_charged + match = method_amount)
--   * Daily match cap
--
-- They are physical scrip the FAM rep hands the customer separately,
-- as a marketing/loyalty add-on outside the financial pipeline.
CREATE TABLE IF NOT EXISTS reward_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Source: any active payment method (incl. non-denominated
    -- like SNAP/Cash) — what the customer paid that triggers the
    -- reward.
    source_method_id INTEGER NOT NULL,
    -- For every ``threshold_cents`` of source customer_charged in
    -- a single customer order, the customer gets one ``reward_unit_cents``
    -- worth of the reward method.  ``floor`` math — partial
    -- thresholds earn nothing.
    threshold_cents INTEGER NOT NULL CHECK (threshold_cents > 0),
    -- Reward: must be a denominated payment method (FAM can't hand
    -- out SNAP/Cash/FMNP — only physical scrip like Food Bucks,
    -- Food RX, JH Tokens).  Validation lives in the model layer.
    reward_method_id INTEGER NOT NULL,
    reward_unit_cents INTEGER NOT NULL CHECK (reward_unit_cents > 0),
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_method_id) REFERENCES payment_methods(id),
    FOREIGN KEY (reward_method_id) REFERENCES payment_methods(id),
    -- Source and reward must be different methods — handing out
    -- the same instrument the customer just paid with is a
    -- nonsensical config.
    CHECK (source_method_id != reward_method_id)
);

-- Snapshot of customer-facing rewards generated at payment-
-- confirmation time (v1.9.10 / schema v30).
--
-- HISTORICAL RECORD — write once at confirmation, NEVER modify.
-- Specifically:
--   * Pre-feature transactions never get rows here.
--   * Rule changes / deletions don't touch existing rows
--     (snapshot columns capture the rule state at write time).
--   * Disabling the rewards feature does NOT wipe existing rows —
--     they remain in the Generated Rewards report.
--   * Voiding or adjusting transactions does NOT modify reward
--     rows (the cashier already handed the tokens; this is the
--     receipt-of-record).
--
-- This table contains ZERO load-bearing financial data — vendor
-- reimbursement and FAM match queries do not read from it.
-- See ``docs/FINANCIAL_FORMULA.md § 11`` for the full carve-out.
CREATE TABLE IF NOT EXISTS generated_rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_order_id INTEGER NOT NULL,
    market_day_id INTEGER NOT NULL,
    -- Snapshots — these may diverge from the live tables later
    -- (rule deleted, payment method renamed, etc.); the snapshot
    -- is the historical record of what the rep handed out at
    -- confirmation time.
    rule_id INTEGER,                        -- nullable; rule may be deleted
    source_method_id INTEGER,               -- nullable; method may be deleted
    source_method_name_snapshot TEXT NOT NULL,
    source_total_cents INTEGER NOT NULL,    -- sum of customer_charged at write time
    threshold_cents INTEGER NOT NULL,       -- rule threshold at write time
    reward_method_id INTEGER,
    reward_method_name_snapshot TEXT NOT NULL,
    reward_unit_cents INTEGER NOT NULL,     -- per-unit reward at write time
    n_units INTEGER NOT NULL CHECK (n_units > 0),
    reward_total_cents INTEGER NOT NULL CHECK (reward_total_cents > 0),
    generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    generated_by TEXT,                      -- volunteer name from market day
    FOREIGN KEY (customer_order_id) REFERENCES customer_orders(id),
    FOREIGN KEY (market_day_id) REFERENCES market_days(id)
);
CREATE INDEX IF NOT EXISTS idx_generated_rewards_order
    ON generated_rewards(customer_order_id);
CREATE INDEX IF NOT EXISTS idx_generated_rewards_md
    ON generated_rewards(market_day_id);
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
    """Rename discount columns to match columns; widen percent range to 0-999.

    v1.9.10 follow-up (2026-05-01): the column-rename migrations
    are now idempotent.  Previously a crash between any of the
    three RENAME statements and the schema_version bump would
    error on next launch (re-running RENAME on an already-renamed
    column raises ``no such column: discount_percent``).  We now
    introspect ``pragma_table_info`` and skip the rename when the
    target column already exists.
    """
    logger.info("Running migration v5 to v6: discount -> match rename")

    def _has_col(table: str, col: str) -> bool:
        return any(
            r[1] == col for r in conn.execute(
                f"PRAGMA table_info({table})").fetchall())

    # Rename columns (requires SQLite 3.25.0+; Python 3.12 bundles 3.41+).
    # Each rename is guarded against a partially-completed prior run.
    if _has_col('payment_methods', 'discount_percent') and not _has_col(
            'payment_methods', 'match_percent'):
        conn.execute(
            "ALTER TABLE payment_methods "
            "RENAME COLUMN discount_percent TO match_percent"
        )
    if _has_col('payment_line_items', 'discount_percent_snapshot') and not _has_col(
            'payment_line_items', 'match_percent_snapshot'):
        conn.execute(
            "ALTER TABLE payment_line_items"
            " RENAME COLUMN discount_percent_snapshot TO match_percent_snapshot"
        )
    if _has_col('payment_line_items', 'discount_amount') and not _has_col(
            'payment_line_items', 'match_amount'):
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


def _migrate_v11_to_v12(conn):
    """Add denomination column to payment_methods for increment constraints."""
    logger.info("Running migration v11 to v12: payment method denominations")
    try:
        conn.execute(
            "ALTER TABLE payment_methods ADD COLUMN denomination REAL DEFAULT NULL"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()
    logger.info("Migration v11->v12 complete: denomination column added")


def _migrate_v12_to_v13(conn):
    """Add photo columns to fmnp_entries for check photo attachments."""
    logger.info("Running migration v12 to v13: FMNP photo columns")
    try:
        conn.execute(
            "ALTER TABLE fmnp_entries ADD COLUMN photo_path TEXT DEFAULT NULL"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE fmnp_entries ADD COLUMN photo_drive_url TEXT DEFAULT NULL"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()
    logger.info("Migration v12->v13 complete: photo_path + photo_drive_url added")


def _migrate_v13_to_v14(conn):
    """Add photo_required to payment_methods and photo_path to payment_line_items."""
    logger.info("Running migration v13 to v14: photo receipt requirement")
    try:
        conn.execute(
            "ALTER TABLE payment_methods ADD COLUMN photo_required TEXT DEFAULT NULL"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE payment_line_items ADD COLUMN photo_path TEXT DEFAULT NULL"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()
    logger.info("Migration v13->v14 complete: photo_required + payment photo_path added")


def _migrate_v14_to_v15(conn):
    """Add photo_drive_url to payment_line_items for Drive upload tracking."""
    logger.info("Running migration v14 to v15: payment_line_items photo_drive_url")
    try:
        conn.execute(
            "ALTER TABLE payment_line_items ADD COLUMN photo_drive_url TEXT DEFAULT NULL"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()
    logger.info("Migration v14->v15 complete: payment photo_drive_url added")


def _migrate_v15_to_v16(conn):
    """Add app_version and device_id to audit_log for traceability."""
    logger.info("Running migration v15 to v16: audit_log app_version + device_id")
    for col in ('app_version', 'device_id'):
        try:
            conn.execute(
                f"ALTER TABLE audit_log ADD COLUMN {col} TEXT DEFAULT NULL"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    logger.info("Migration v15->v16 complete: audit_log traceability columns added")


def _migrate_v16_to_v17(conn):
    """Add photo_hashes table for content-based upload deduplication."""
    logger.info("Running migration v16 to v17: photo_hashes table")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS photo_hashes (
            content_hash TEXT PRIMARY KEY,
            drive_url TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    logger.info("Migration v16->v17 complete: photo_hashes table created")


def _migrate_v17_to_v18(conn):
    """Add local_photo_hashes table for cross-transaction duplicate detection.

    Also backfills hashes for any photos already in the photos directory
    so that duplicates of older images are caught immediately.
    """
    logger.info("Running migration v17 to v18: local_photo_hashes table")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS local_photo_hashes (
            content_hash TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # Backfill: hash all existing photos so cross-transaction dedup
    # covers images stored before this migration.
    try:
        from fam.utils.photo_storage import get_photos_dir, compute_file_hash
        photos_dir = get_photos_dir()
        if os.path.isdir(photos_dir):
            count = 0
            for fname in os.listdir(photos_dir):
                fpath = os.path.join(photos_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                try:
                    h = compute_file_hash(fpath)
                    rel = f"photos/{fname}"
                    conn.execute(
                        "INSERT OR IGNORE INTO local_photo_hashes "
                        "(content_hash, relative_path) VALUES (?, ?)",
                        (h, rel))
                    count += 1
                except Exception:
                    pass  # skip unreadable files
            conn.commit()
            if count:
                logger.info("Backfilled %d existing photo hashes", count)
    except Exception:
        logger.warning("Photo hash backfill skipped", exc_info=True)

    logger.info("Migration v17->v18 complete: local_photo_hashes table created")


def _migrate_v18_to_v19(conn):
    """Add vendor registration fields for reimbursement and check writing."""
    logger.info("Running migration v18 to v19: vendor registration fields")
    new_cols = [
        ("check_payable_to", "TEXT"),
        ("street", "TEXT"),
        ("city", "TEXT"),
        ("state", "TEXT"),
        ("zip_code", "TEXT"),
        ("ach_enabled", "BOOLEAN DEFAULT 0"),
    ]
    for col_name, col_type in new_cols:
        try:
            conn.execute(
                f"ALTER TABLE vendors ADD COLUMN {col_name} {col_type}"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    logger.info("Migration v18->v19 complete: vendor registration fields added")


def _migrate_v19_to_v20(conn):
    """Add missing foreign-key indexes for query performance.

    Reports that JOIN on vendor_id, market_day_id, and payment_method_id
    currently require full table scans on the FK side.  These indexes
    make those joins O(log n) as data grows over a market season.
    """
    logger.info("Running migration v19 to v20: FK indexes")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_transactions_vendor
            ON transactions(vendor_id);
        CREATE INDEX IF NOT EXISTS idx_customer_orders_market_day
            ON customer_orders(market_day_id);
        CREATE INDEX IF NOT EXISTS idx_fmnp_entries_vendor
            ON fmnp_entries(vendor_id);
        CREATE INDEX IF NOT EXISTS idx_payment_items_method
            ON payment_line_items(payment_method_id);
    """)
    conn.commit()
    logger.info("Migration v19->v20 complete: 4 FK indexes added")


def _migrate_v20_to_v21(conn):
    """Add indexes for remaining high-traffic query columns.

    - transactions(customer_order_id): JOIN in customer-order receipt lookups
    - market_days(market_id, date): market dropdown + date filtering
    - audit_log(table_name, record_id): transaction log / audit queries
    """
    logger.info("Running migration v20 to v21: additional indexes")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_transactions_customer_order
            ON transactions(customer_order_id);
        CREATE INDEX IF NOT EXISTS idx_market_days_market_date
            ON market_days(market_id, date);
        CREATE INDEX IF NOT EXISTS idx_audit_log_table_record
            ON audit_log(table_name, record_id);
    """)
    conn.commit()
    logger.info("Migration v20->v21 complete: 3 additional indexes added")


def _migrate_v21_to_v22(conn):
    """Convert all monetary REAL columns to INTEGER cents.

    All monetary values were previously stored as dollar floats
    (e.g. 89.99). This migration converts them to integer cents
    (e.g. 8999) for exact arithmetic and future stored-value support.

    Uses ROUND() before CAST to avoid truncation errors from float
    representation (e.g. 5.00 stored as 4.999999 would truncate to
    499 without rounding).
    """
    logger.info("Running migration v21 to v22: monetary values to integer cents")

    conn.executescript("""
        -- markets: daily_match_limit
        UPDATE markets
           SET daily_match_limit = CAST(ROUND(daily_match_limit * 100) AS INTEGER);

        -- payment_methods: denomination (skip NULLs)
        UPDATE payment_methods
           SET denomination = CAST(ROUND(denomination * 100) AS INTEGER)
         WHERE denomination IS NOT NULL;

        -- transactions: receipt_total
        UPDATE transactions
           SET receipt_total = CAST(ROUND(receipt_total * 100) AS INTEGER);

        -- payment_line_items: method_amount, match_amount, customer_charged
        UPDATE payment_line_items
           SET method_amount    = CAST(ROUND(method_amount * 100) AS INTEGER),
               match_amount     = CAST(ROUND(match_amount * 100) AS INTEGER),
               customer_charged = CAST(ROUND(customer_charged * 100) AS INTEGER);

        -- fmnp_entries: amount
        UPDATE fmnp_entries
           SET amount = CAST(ROUND(amount * 100) AS INTEGER);
    """)
    conn.commit()
    logger.info("Migration v21->v22 complete: all monetary values converted to integer cents")


def _migrate_v22_to_v23(conn):
    """Enforce UNIQUE on vendors.name to match markets and payment_methods.

    The original ``vendors`` table allowed duplicate names — the only
    constraint was ``NOT NULL``.  Markets and payment_methods both
    enforce uniqueness at the DB level, so the UI's "name already
    exists" guard worked there but not for vendors, letting two
    "Acme Farm" rows coexist with confusing reporting downstream.

    This migration:

    1. Detects any existing duplicate vendor names (case-sensitive,
       matching the existing market/payment-method comparison rules).
    2. Renames duplicates by appending ``" (2)"``, ``" (3)"`` … on the
       higher-id rows so the older record keeps the canonical name.
       Vendor IDs are not touched — every foreign key (transactions,
       fmnp_entries, market_vendors) stays intact.
    3. Creates a ``UNIQUE INDEX`` on ``vendors(name)``.  SQLite cannot
       add a UNIQUE column constraint via ALTER TABLE, but a UNIQUE
       INDEX enforces the same INSERT/UPDATE behaviour and produces
       the same ``UNIQUE constraint failed`` error string the UI
       already pattern-matches.

    The fresh-install schema in ``TABLES_SQL`` was updated in lockstep
    with this migration so new databases bake the UNIQUE constraint
    into the column itself.
    """
    logger.info("Running migration v22 to v23: vendors.name UNIQUE")

    # ── Step 1: discover and resolve duplicates ────────────────────
    rows = conn.execute(
        "SELECT id, name FROM vendors ORDER BY name, id"
    ).fetchall()

    # Build a set of all currently-used names so the suffixed names we
    # invent don't collide with an unrelated existing vendor (rare but
    # possible: vendor list contains "Acme", "Acme", and "Acme (2)").
    existing_names = {r['name'] for r in rows}

    # Group by name, keep the lowest-id row's name unchanged, rename
    # the rest with " (N)" suffixes.
    by_name: dict[str, list[int]] = {}
    for r in rows:
        by_name.setdefault(r['name'], []).append(r['id'])

    renamed = 0
    for original_name, ids in by_name.items():
        if len(ids) <= 1:
            continue  # no duplicates for this name
        # Lowest id keeps the name; later ids get suffixed.
        for vendor_id in ids[1:]:
            n = 2
            while True:
                candidate = f"{original_name} ({n})"
                if candidate not in existing_names:
                    break
                n += 1
            conn.execute(
                "UPDATE vendors SET name = ? WHERE id = ?",
                (candidate, vendor_id),
            )
            existing_names.add(candidate)
            logger.warning(
                "Renamed duplicate vendor id=%s '%s' -> '%s'",
                vendor_id, original_name, candidate,
            )
            renamed += 1

    # ── Step 2: enforce uniqueness going forward ────────────────────
    # Use IF NOT EXISTS so a fresh install (which already has the
    # column-level UNIQUE) doesn't error if this migration somehow
    # runs against it — the column-level UNIQUE auto-creates an
    # internal sqlite_autoindex which won't conflict by name.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_vendors_name_unique"
        " ON vendors(name)"
    )
    conn.commit()

    if renamed:
        logger.info(
            "Migration v22->v23 complete: vendors.name UNIQUE enforced "
            "(%d duplicate row(s) renamed)", renamed)
    else:
        logger.info(
            "Migration v22->v23 complete: vendors.name UNIQUE enforced "
            "(no duplicates found)")


def _migrate_v23_to_v24(conn):
    """Add vendor_payment_methods junction + permissive default backfill.

    A vendor-level eligibility layer was missing before v24: payment
    methods were assigned at the market level only, so denominated
    instruments like Food Bucks (which are only redeemable at certain
    vendors — typically produce stalls) ended up "spread" across every
    vendor on a multi-receipt order during save.  The downstream
    Vendor Reimbursement report attributed phantom denominated
    payments to vendors who never accepted them.

    This migration introduces ``vendor_payment_methods`` so denominated
    rows on the Payment screen can bind to a single eligible vendor at
    capture time (see PaymentRow vendor dropdown).  No existing
    behaviour breaks because the migration is **permissive**: every
    existing vendor inherits every payment method.  Coordinators then
    tighten eligibility per-vendor via Settings → Vendors → Eligible
    Payment Methods.

    The fresh-install schema in TABLES_SQL was updated in lockstep so
    new databases bake the table in.  This migration also seeds it for
    upgraded installs.
    """
    logger.info("Running migration v23 to v24: vendor_payment_methods")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vendor_payment_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            payment_method_id INTEGER NOT NULL,
            FOREIGN KEY (vendor_id) REFERENCES vendors(id),
            FOREIGN KEY (payment_method_id) REFERENCES payment_methods(id),
            UNIQUE(vendor_id, payment_method_id)
        );
    """)

    # Permissive backfill: every existing vendor gets every existing
    # payment method.  ``INSERT OR IGNORE`` keeps the operation
    # idempotent in case the migration runs twice.
    backfill = conn.execute("""
        INSERT OR IGNORE INTO vendor_payment_methods (vendor_id, payment_method_id)
        SELECT v.id, pm.id
          FROM vendors v
          CROSS JOIN payment_methods pm
    """)
    rows = backfill.rowcount if backfill.rowcount is not None else 0
    conn.commit()
    logger.info(
        "Migration v23->v24 complete: vendor_payment_methods created "
        "(%d permissive backfill rows inserted)", rows)


def _migrate_v24_to_v25(conn):
    """Add ``is_system`` flag + seed the 'Unallocated Funds' system method.

    Background — Adjustments page "customer gone" recovery:
    Historically when a manager reconciles vendor receipts after the
    fact and finds the customer was undercharged, the receipt-total
    bump on the Adjustments screen would either (a) save normally
    pretending the customer paid more (which is a lie — the customer
    is gone) or (b) get blocked by reconciliation validation with no
    clean recovery path.  Either way, FAM's books quietly absorbed
    the loss with no separate accounting.

    This migration introduces a first-class category — *Unallocated
    Funds* — modelled as a payment method so it flows through every
    existing per-method report column for free.  Reports continue to
    aggregate by ``method_name_snapshot`` so the Vendor Reimbursement
    and Detailed Ledger tabs gain an "Unallocated Funds" column with
    no extra plumbing; the FAM Match Report grows a sibling "FAM
    Absorbed" total alongside "FAM Match" so the two flavours of
    FAM-funded dollars stay distinguishable.

    The ``is_system`` column is added so this method (and any future
    system-managed methods) can be:
      * hidden from the Payment screen + Adjustments dropdowns —
        managers cannot pick it manually; it's only auto-injected by
        the "customer is gone" path,
      * locked in Settings → Payment Methods (no rename/delete/
        toggle), preventing a coordinator from breaking the audit
        trail.

    Permissive vendor-eligibility backfill: the new system method is
    inserted into ``vendor_payment_methods`` for every existing
    vendor so it works on every adjustment without per-vendor setup.
    """
    logger.info("Running migration v24 to v25: payment_methods.is_system + "
                "seed Unallocated Funds")

    # Add is_system column if missing (idempotent for re-runs)
    cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(payment_methods)").fetchall()}
    if 'is_system' not in cols:
        conn.execute(
            "ALTER TABLE payment_methods ADD COLUMN is_system "
            "BOOLEAN DEFAULT 0"
        )

    # Seed the system method.  ``INSERT OR IGNORE`` keeps it
    # idempotent if a fresh-install schema (TABLES_SQL) plus this
    # migration both run on the same connection.  match_percent is
    # 0.0 — there's no customer payment for FAM to multiply against;
    # the absorbed amount IS what FAM contributes.
    #
    # The explicit high ID (9999) reserves room for coordinator-
    # created and test-fixture payment methods to use the lower
    # natural-order IDs without colliding with the seed.  Many of
    # the suite's test fixtures pre-allocate ``payment_methods.id =
    # 1, 2, 3...`` for SNAP/Cash/etc; using a low id here would
    # break them all.  Production will never hand-create 10,000
    # payment methods, so the high ID is safe.
    cursor = conn.execute(
        "INSERT OR IGNORE INTO payment_methods "
        "(id, name, match_percent, is_active, sort_order, "
        " denomination, photo_required, is_system) "
        "VALUES (9999, ?, ?, 1, 999, NULL, NULL, 1)",
        ('Unallocated Funds', 0.0)
    )
    seeded = cursor.rowcount or 0

    # If the row already existed (e.g. from a prior partial migration
    # or a hand-inserted dev record), make sure the is_system flag is
    # set so the UI guards engage.
    conn.execute(
        "UPDATE payment_methods SET is_system = 1 WHERE name = ?",
        ('Unallocated Funds',)
    )

    # Permissive vendor-eligibility backfill.  Without this, the
    # Adjustments "customer gone" path would fail vendor eligibility
    # validation on the very first transaction that needed it.
    pm_row = conn.execute(
        "SELECT id FROM payment_methods WHERE name = ?",
        ('Unallocated Funds',)
    ).fetchone()
    if pm_row is not None:
        pm_id = pm_row[0]
        backfill = conn.execute(
            "INSERT OR IGNORE INTO vendor_payment_methods "
            "(vendor_id, payment_method_id) "
            "SELECT id, ? FROM vendors",
            (pm_id,)
        )
        backfilled = backfill.rowcount if backfill.rowcount is not None else 0
    else:
        backfilled = 0

    conn.commit()
    logger.info(
        "Migration v24->v25 complete: Unallocated Funds method seeded "
        "(seeded=%d, vendor backfill rows=%d)", seeded, backfilled)


def _migrate_v26_to_v27(conn):
    """Defensive cleanup for the short-lived v26 schema.

    A 30-minute build of the v1.9.9 device-tagged customer label
    feature briefly bumped the schema to v26 with a UNIQUE INDEX on
    ``(market_day_id, customer_label)``.  The index was reverted in
    the same release because it broke the legitimate "returning
    customer reuses their label across multiple orders" pattern,
    but installs that ran the intermediate build now have:

      * ``schema_version`` stamped with 26
      * possibly ``idx_customer_orders_unique_label`` installed
        (only if the install had no duplicate labels at the time —
        otherwise the buggy migration skipped index creation and
        logged a warning)

    Without this cleanup, those installs:
      1. Would refuse to launch ("DB v26 newer than app v25")
      2. Would silently break returning-customer flow on next use

    This migration drops the rogue index ``IF EXISTS`` and bumps the
    schema to 27.  Idempotent: safe on fresh installs (where the
    index was never created), safe on installs that never reached
    v26 (drop is a no-op), and safe on installs that DID reach v26
    (drops the index if it's there, no-op if it isn't).

    Schema is intentionally bumped two steps from 25 → 27 (skipping
    a separate v25→v26 migration).  The "v26" build was abandoned
    in flight; v27 is the canonical successor to v25, and the chain
    ``if current_version < 27`` runs this migration for every
    pre-v27 install regardless of whether they passed through v26.
    """
    logger.info("Running migration to v27: drop legacy v26 unique index")
    conn.execute(
        "DROP INDEX IF EXISTS idx_customer_orders_unique_label"
    )
    conn.commit()
    logger.info("Migration to v27 complete: legacy index removed (if present)")


def _migrate_v27_to_v28(conn):
    """Add per-line invariant triggers on payment_line_items.

    The contract documented in ``docs/FINANCIAL_FORMULA.md`` is:

        customer_charged + match_amount = method_amount

    The application engine (``calculate_payment_breakdown`` and
    ``save_payment_line_items``) maintains this on every write; up
    through v27, SQLite did NOT enforce it.  v1.9.9's nightmare
    audit (Finding H-3) flagged this as a defense-in-depth gap:
    a future engine bug or a
    direct SQL write could land an inconsistent row.

    This migration adds matching ``BEFORE INSERT`` and
    ``BEFORE UPDATE`` triggers that ABORT any write violating the
    invariant.  Existing rows are NOT retroactively validated —
    triggers only fire on new writes — so the migration is safe
    on any existing install whose engine maintained the invariant.
    For paranoia we run a pre-flight scan: if any existing row
    violates the invariant we WARN (so an operator notices) but
    do not block the migration; the trigger just guards future
    writes from there on.

    **System-method exemption.**  Rows whose ``method_name_snapshot``
    is ``'Unallocated Funds'`` legitimately violate the invariant by
    design: they represent FAM-absorbed value where the customer
    paid nothing and there was no match — only the receipt's
    method_amount is FAM's contribution.  The trigger skips these
    rows via a ``WHEN`` clause so the customer-gone recovery flow
    keeps working.
    """
    logger.info("Running migration to v28: per-line invariant triggers")

    # Tolerate partially-built schemas — some legacy test fixtures
    # bootstrap a stripped-down DB starting from an older schema
    # version that doesn't include payment_line_items, then run
    # migrations forward.  Production never hits this path.
    has_pli = conn.execute(
        "SELECT 1 FROM sqlite_master "
        " WHERE type='table' AND name='payment_line_items'"
    ).fetchone() is not None
    if not has_pli:
        logger.info(
            "v28 migration: payment_line_items table absent; "
            "skipping trigger creation (likely a partial-schema "
            "test fixture).  Production schemas always have the "
            "table.")
        return

    # Pre-flight: scan existing rows for invariant violations
    # (system-method Unallocated Funds rows are exempt by design).
    bad_rows = conn.execute("""
        SELECT COUNT(*) FROM payment_line_items
        WHERE customer_charged + match_amount != method_amount
          AND method_name_snapshot != 'Unallocated Funds'
    """).fetchone()[0]
    if bad_rows:
        logger.warning(
            "v28 migration: %d existing payment_line_items rows "
            "violate the customer_charged + match_amount = "
            "method_amount invariant (excluding system-method "
            "Unallocated Funds rows).  Triggers will protect "
            "future writes; existing rows are left untouched and "
            "may need manual reconciliation.", bad_rows
        )

    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS chk_pli_invariant_insert
        BEFORE INSERT ON payment_line_items
        WHEN NEW.method_name_snapshot != 'Unallocated Funds'
        BEGIN
            SELECT RAISE(ABORT,
                'customer_charged + match_amount must equal method_amount')
            WHERE NEW.customer_charged + NEW.match_amount
                  != NEW.method_amount;
        END;

        CREATE TRIGGER IF NOT EXISTS chk_pli_invariant_update
        BEFORE UPDATE ON payment_line_items
        WHEN NEW.method_name_snapshot != 'Unallocated Funds'
        BEGIN
            SELECT RAISE(ABORT,
                'customer_charged + match_amount must equal method_amount')
            WHERE NEW.customer_charged + NEW.match_amount
                  != NEW.method_amount;
        END;
    """)
    conn.commit()
    logger.info("Migration to v28 complete: per-line invariant trigger added")


def _migrate_v28_to_v29(conn):
    """Add reward_rules table for the customer-facing rewards program.

    v1.9.10 introduces a rewards add-on: when a customer pays for an
    order using SNAP (or any other configured source method), the
    coordinator hands them a fixed amount of reward scrip (e.g. JH
    Food Bucks tokens) — a marketing/loyalty layer outside the
    financial pipeline.

    Critical safety properties of this migration:
      * Adds ONLY a new table.  No changes to ``transactions``,
        ``payment_line_items``, or any reporting view.
      * Does NOT register any trigger that affects writes to other
        tables.  The financial invariant remains untouched.
      * Idempotent — ``CREATE TABLE IF NOT EXISTS`` so reruns are
        safe.
      * No data backfill — the table starts empty and is populated
        either by the seed (default SNAP × $5 → $2 × FB rule) or
        by Settings → Rewards UI.

    Rewards are computed on demand by ``fam/utils/rewards.py`` and
    surfaced ONLY in three places: payment-confirmation dialog,
    printed receipt, and the Generated Rewards report.  No
    persistent reward amount is stored against transactions.
    """
    logger.info("Running migration to v29: reward_rules table")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS reward_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_method_id INTEGER NOT NULL,
            threshold_cents INTEGER NOT NULL
                CHECK (threshold_cents > 0),
            reward_method_id INTEGER NOT NULL,
            reward_unit_cents INTEGER NOT NULL
                CHECK (reward_unit_cents > 0),
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_method_id)
                REFERENCES payment_methods(id),
            FOREIGN KEY (reward_method_id)
                REFERENCES payment_methods(id),
            CHECK (source_method_id != reward_method_id)
        );
    """)
    conn.commit()
    logger.info("Migration to v29 complete: reward_rules table created")


def _migrate_v29_to_v30(conn):
    """Add generated_rewards table — snapshot history of
    customer-facing reward scrip handed out at payment-confirmation.

    Replaces the v29 derived-on-demand approach (which retroactively
    surfaced rewards for pre-feature orders).  The new table is a
    write-once history: rows are inserted at confirmation time and
    NEVER modified after, so:

      * Pre-feature transactions don't appear in the report.
      * Disabling the feature later does not wipe history.
      * Rule edits don't retro-apply to past orders.
      * Voids / adjustments don't change historical reward rows.

    Migration safety:
      * Adds ONE new table + 2 indexes.  No changes to
        ``transactions``, ``payment_line_items``, or any existing
        constraint.
      * Idempotent — ``CREATE TABLE IF NOT EXISTS`` and
        ``CREATE INDEX IF NOT EXISTS`` make reruns safe.
      * No data backfill — the table starts empty.  This is
        deliberate: pre-existing orders did NOT have rewards
        generated for them in real life (cashiers were not
        handing out scrip), so the historical record correctly
        starts empty.
    """
    logger.info("Running migration to v30: generated_rewards table")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS generated_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_order_id INTEGER NOT NULL,
            market_day_id INTEGER NOT NULL,
            rule_id INTEGER,
            source_method_id INTEGER,
            source_method_name_snapshot TEXT NOT NULL,
            source_total_cents INTEGER NOT NULL,
            threshold_cents INTEGER NOT NULL,
            reward_method_id INTEGER,
            reward_method_name_snapshot TEXT NOT NULL,
            reward_unit_cents INTEGER NOT NULL,
            n_units INTEGER NOT NULL CHECK (n_units > 0),
            reward_total_cents INTEGER NOT NULL
                CHECK (reward_total_cents > 0),
            generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            generated_by TEXT,
            FOREIGN KEY (customer_order_id)
                REFERENCES customer_orders(id),
            FOREIGN KEY (market_day_id)
                REFERENCES market_days(id)
        );
        CREATE INDEX IF NOT EXISTS idx_generated_rewards_order
            ON generated_rewards(customer_order_id);
        CREATE INDEX IF NOT EXISTS idx_generated_rewards_md
            ON generated_rewards(market_day_id);
    """)
    conn.commit()
    logger.info("Migration to v30 complete: generated_rewards table created")


def _migrate_v30_to_v31(conn):
    """Tighten DB-level financial integrity (audit findings G1, G3).

    Three new triggers, all idempotent (``IF NOT EXISTS``):

      * ``chk_payment_amount_update`` — non-negativity on UPDATE
        (the v4 trigger only covered INSERT).  Without this, a
        bypass-the-app UPDATE could push ``method_amount`` or
        ``match_amount`` below zero; the per-line invariant
        trigger catches some such cases (when the swap breaks
        ``customer + match = method``) but not all (e.g. setting
        all three to negative values that still satisfy E3).
      * ``chk_transactions_voided_one_way`` — voided transactions
        are terminal at the DB level.  Python enforces this in
        ``update_transaction``; the DB trigger is the
        defense-in-depth so a repair script or future model
        bypass cannot resurrect a void.
      * (G2 — SUM(method)=receipt — is intentionally enforced in
        the application layer per the documented design; pinning
        it as a CHECK trigger is impractical without deferred
        constraints in SQLite.  See test
        ``test_app_restart_persistence::test_db_invariants_hold_post_restart``
        for the post-hoc verifier.)

    Pure additions; no existing rows touched.  Safe to re-run.
    """
    logger.info(
        "Running migration v30 to v31: PLI UPDATE non-negativity "
        "+ Voided one-way trigger")

    # Defensive: synthetic test setups occasionally start with a
    # bare DB that misses parent tables expected by intermediate
    # migrations.  Skip a trigger if its target table doesn't
    # exist — the table-create migrations later in the pipeline
    # don't need this trigger to run.  Real installs always have
    # both tables by the time we reach this point.
    def _table_exists(name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name=?", (name,)).fetchone() is not None

    if _table_exists('payment_line_items'):
        conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS chk_payment_amount_update
            BEFORE UPDATE ON payment_line_items
            BEGIN
                SELECT RAISE(ABORT, 'method_amount must be >= 0')
                WHERE NEW.method_amount < 0;
                SELECT RAISE(ABORT, 'match_amount must be >= 0')
                WHERE NEW.match_amount < 0;
                SELECT RAISE(ABORT, 'customer_charged must be >= 0')
                WHERE NEW.customer_charged < 0;
            END;
        """)

    if _table_exists('transactions'):
        conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS chk_transactions_voided_one_way
            BEFORE UPDATE OF status ON transactions
            WHEN OLD.status = 'Voided' AND NEW.status != 'Voided'
            BEGIN
                SELECT RAISE(ABORT, 'Voided transactions are terminal; cannot transition Voided -> non-Voided');
            END;
        """)
    conn.commit()
    logger.info(
        "Migration v30->v31 complete: 2 triggers added (skipped any "
        "whose parent table didn't exist in this DB)")


def _migrate_v31_to_v32(conn):
    """Add composite indexes that pay off at multi-year scale.

    Year-1 deployments saw most queries finish in <50ms because the
    tables stayed small (a few thousand rows).  Year-3+ projections:

      * ``audit_log`` ~ 500K rows (every mutation × multi-year)
      * ``payment_line_items`` ~ 50K rows
      * ``transactions`` ~ 20K rows
      * ``generated_rewards`` ~ 30K rows

    Three queries dominated CPU when projected to those sizes:

      1. ``get_transaction_log`` correlates each transactions row to
         its most recent audit_log entry.  Existing index
         ``idx_audit_log_table_record(table_name, record_id)`` covers
         the equality match but NOT the ``MAX(changed_at)`` —
         SQLite then sorts the matching rows.  Adding
         ``changed_at`` as the third column lets the optimizer use
         an index-only descending scan.

      2. The Activity Log query filters audit_log by date range +
         ORDER BY changed_at DESC.  ``idx_audit_log_changed_at``
         covers it but adding it explicitly keeps the plan stable
         after many ANALYZEs.

      3. Per-market-day reports filter transactions by
         ``(market_day_id, status)``.  An equality+equality predicate
         is best served by a composite index.  ``idx_transactions_
         market_day`` covers the first column but a second filter
         on status still has to scan all rows for that md.

    Idempotent — every CREATE uses IF NOT EXISTS.  Adding indexes
    is non-destructive; if the underlying tables don't exist (test
    fixture skips), the CREATE silently succeeds at plan time.
    """
    logger.info("Running migration v31 to v32: scaling indexes")
    # Defensive — same table-existence guard as v30→v31.
    def _table_exists(name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name=?", (name,)).fetchone() is not None

    if _table_exists('audit_log'):
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS
                idx_audit_log_record_table_changed_at
                ON audit_log(record_id, table_name, changed_at DESC);
        """)
    if _table_exists('transactions'):
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS
                idx_transactions_md_status
                ON transactions(market_day_id, status);
        """)
    if _table_exists('payment_line_items'):
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS
                idx_pli_method_txn
                ON payment_line_items(payment_method_id, transaction_id);
        """)
    if _table_exists('generated_rewards'):
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS
                idx_generated_rewards_md_order
                ON generated_rewards(market_day_id, customer_order_id);
        """)
    conn.commit()
    logger.info(
        "Migration v31->v32 complete: 4 composite scaling indexes added")


def _migrate_v32_to_v33(conn):
    """Defensive trigger: Unallocated Funds rows must have
    ``customer_charged = 0`` and ``match_amount = 0``.

    The per-line invariant trigger (v28+) explicitly *exempts* UF
    rows so they can carry the absorbed loss in ``method_amount``
    without violating ``customer + match = method``.  Today UF
    rows are written with ``customer_charged = 0`` and
    ``match_amount = 0`` everywhere they're created
    (``admin_screen._inject_unallocated_funds`` and the absorb
    helper).  But there's no schema-level enforcement, so a future
    bug that lands non-zero match on a UF row would silently
    inflate the FAM Match summary tile and trend chart.

    This migration adds an INSERT/UPDATE trigger that enforces
    the contract.  Idempotent — uses ``IF NOT EXISTS``.

    Existing UF rows that already conform are unaffected.  If
    legacy data violates the contract (very unlikely), the trigger
    will reject FUTURE writes to that row but won't touch the
    existing state.
    """
    # Defensive: the trigger references payment_line_items, which
    # may not exist on test fixtures that build a synthetic
    # pre-v6 schema and run migrations forward.  Production
    # schemas always have it (created in v0/v1).  Mirror the
    # has_pli guard from the v28 migration.
    has_pli = conn.execute(
        "SELECT 1 FROM sqlite_master "
        " WHERE type='table' AND name='payment_line_items'"
    ).fetchone() is not None
    if not has_pli:
        logger.info(
            "v33 migration: payment_line_items table absent; "
            "skipping trigger creation (likely a partial-schema "
            "test fixture).")
        return

    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS chk_pli_uf_zero_insert
        BEFORE INSERT ON payment_line_items
        WHEN NEW.method_name_snapshot = 'Unallocated Funds'
        BEGIN
            SELECT RAISE(ABORT,
                'Unallocated Funds rows must have customer_charged=0 and match_amount=0')
            WHERE NEW.customer_charged != 0
               OR NEW.match_amount != 0;
        END;

        CREATE TRIGGER IF NOT EXISTS chk_pli_uf_zero_update
        BEFORE UPDATE ON payment_line_items
        WHEN NEW.method_name_snapshot = 'Unallocated Funds'
        BEGIN
            SELECT RAISE(ABORT,
                'Unallocated Funds rows must have customer_charged=0 and match_amount=0')
            WHERE NEW.customer_charged != 0
               OR NEW.match_amount != 0;
        END;
    """)
    conn.commit()
    logger.info(
        "Migration v32->v33 complete: UF zero-match enforcement triggers added")


def _migrate_v33_to_v34(conn):
    """Forensic hygiene: dedupe ``schema_version`` rows and add a
    UNIQUE INDEX on the ``version`` column.

    Pre-v34 the ``schema_version`` table had no UNIQUE / PRIMARY
    KEY constraint on ``version``.  In normal operation the
    SELECT-then-INSERT guard in the upgrade tail prevented
    duplicates, but partial-init replays, Reset cycles, and
    historical fresh-install paths could leave multiple rows for
    the same version.  ``MAX(version)`` reads still work correctly,
    so the app keeps functioning, but the audit trail "when was
    schema vN applied" is unreliable.

    This migration:
      1. Deletes duplicate rows, keeping the row with the smallest
         ``rowid`` (the original applied_at) for each version.
      2. Adds ``CREATE UNIQUE INDEX IF NOT EXISTS
         idx_schema_version_unique ON schema_version (version)``.

    Idempotent — safe to re-run on a clean table.
    """
    # Step 1: dedupe.  Keep the row with the smallest rowid per
    # version (preserves the original applied_at).  ``rowid`` is
    # SQLite's implicit primary key when no explicit one exists.
    conn.execute("""
        DELETE FROM schema_version
         WHERE rowid NOT IN (
             SELECT MIN(rowid) FROM schema_version GROUP BY version
         )
    """)
    # Step 2: enforce uniqueness going forward.
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_schema_version_unique
            ON schema_version (version)
    """)
    conn.commit()
    logger.info(
        "Migration v33->v34 complete: schema_version deduplicated and unique-indexed")


def _migrate_v34_to_v35(conn):
    """Backfill SNAP and Cash bindings on every vendor.

    v2.0.7 policy change: SNAP and Cash are universally accepted
    at every vendor.  The Settings → Vendors → Eligible Payment
    Methods dialog locks their checkboxes, and the model layer
    refuses to unassign them.  This migration ensures the binding
    is present regardless of how the DB got to v34 — coordinator
    manually unassigning, .fam import dropping rows, legacy data
    drift, etc.

    Mechanism: ``INSERT OR IGNORE`` for every (vendor, method)
    pair where the method's name is in the universal set
    (``UNIVERSAL_VENDOR_METHOD_NAMES``).  Idempotent — safe to
    re-run.

    The user-reported scenario this addresses (2026-05-06): a
    customer ordering across vendors with at least one SNAP-
    ineligible vendor caused mixed-eligibility distribution
    issues (SNAP overflow onto ineligible vendors, contradictory
    per-vendor reconciliation messages).  By making SNAP and Cash
    universal at the data model, the entire mixed-eligibility
    problem class for these methods disappears.  The v2.0.7
    eligibility-aware engine code remains in place as a safety
    net for future methods that DO have real-world eligibility
    constraints (produce-only Food Bucks, etc.).
    """
    # The universal method set is hardcoded by name to mirror how
    # FMNP, Unallocated Funds, and other system-aware references
    # work throughout the codebase.  Coordinators are explicitly
    # warned not to rename SNAP or Cash.
    universal_names = ('SNAP', 'Cash')
    placeholders = ','.join('?' * len(universal_names))
    universal_method_ids = [
        r[0] for r in conn.execute(
            f"SELECT id FROM payment_methods WHERE name IN ({placeholders})",
            universal_names,
        ).fetchall()
    ]
    if not universal_method_ids:
        logger.info(
            "Migration v34->v35: no SNAP/Cash methods found "
            "(unusual fresh-install state); skipping universal "
            "binding backfill")
        conn.commit()
        return

    vendor_ids = [r[0] for r in conn.execute(
        "SELECT id FROM vendors").fetchall()]

    inserted = 0
    for vid in vendor_ids:
        for pmid in universal_method_ids:
            cur = conn.execute(
                "INSERT OR IGNORE INTO vendor_payment_methods "
                "(vendor_id, payment_method_id) VALUES (?, ?)",
                (vid, pmid),
            )
            inserted += cur.rowcount or 0
    conn.commit()
    logger.info(
        "Migration v34->v35 complete: %d universal vendor-method "
        "bindings inserted (SNAP, Cash) across %d vendor(s)",
        inserted, len(vendor_ids))


def _migrate_v35_to_v36(conn):
    """Add ``customer_forfeit_cents`` column to ``payment_line_items``.

    v2.0.7 follow-up (user-reported 2026-05-07): when a customer
    hands a denominated payment unit (e.g. $10 Food RX token) for
    a receipt smaller than the unit's face value (e.g. $6.52),
    Phase B of the engine's forfeit pass reduces ``customer_charged``
    to the receipt-coverage portion ($6.52) AND tags the row with
    ``customer_forfeit_cents`` to record the unaccounted token-value
    portion ($3.48).

    Pre-v36 the column did not exist on the table — Phase B's
    forfeit metadata was computed in-memory by the engine but
    never persisted.  Reports could not show the forfeit, and
    Layer 2A's spinbox-vs-engine guard couldn't read the value
    back from the DB on AdjustmentDialog re-open (so the saved
    sub-denomination ``customer_charged`` looked like drift to the
    snap-back loop, which over-corrected in earlier iterations).

    Migration: ``ALTER TABLE ... ADD COLUMN`` with ``NOT NULL
    DEFAULT 0`` so all pre-existing rows (which conceptually had
    no Phase B forfeit) get a 0 value backfilled automatically.
    Idempotent via the ``IF NOT EXISTS`` table-check pattern that
    SQLite's ``ALTER TABLE`` doesn't natively support — we walk
    pragma_table_info to detect the column first.

    The vendor reimbursement contract is unchanged: ``Total Due
    to Vendor`` still equals ``SUM(t.receipt_total)``.  The new
    column lets reports surface customer forfeit as a separate
    column WITHOUT shifting any vendor's reimbursement check
    amount (which would confuse end-of-month reconciliation).
    """
    # Defensive: skip when ``payment_line_items`` doesn't exist.
    # The full chain of migrations runs in order during upgrade,
    # but tests / fresh-install replays may invoke this migration
    # against a partially-built schema.  The CREATE TABLE in this
    # module's top-level DDL already declares the column for fresh
    # installs, so this migration only needs to handle existing
    # tables that lack it.
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='payment_line_items'"
    ).fetchone()
    if not table_exists:
        logger.info(
            "Migration v35->v36: payment_line_items table not "
            "present yet; skipping (fresh install path will create "
            "the column via CREATE TABLE)")
        conn.commit()
        return

    # Detect the column.  ``ALTER TABLE ... ADD COLUMN IF NOT
    # EXISTS`` is not supported on every SQLite version we ship,
    # so we check the schema explicitly.
    cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(payment_line_items)").fetchall()}
    if 'customer_forfeit_cents' in cols:
        logger.info(
            "Migration v35->v36: customer_forfeit_cents column "
            "already present; skipping (idempotent re-run)")
        conn.commit()
        return
    conn.execute(
        "ALTER TABLE payment_line_items "
        "ADD COLUMN customer_forfeit_cents INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    logger.info(
        "Migration v35->v36 complete: customer_forfeit_cents "
        "column added to payment_line_items (default 0 for all "
        "pre-existing rows)")


def _migrate_v36_to_v37(conn):
    """Add ``user_capped`` column to ``payment_line_items``.

    v2.0.7+ audit (2026-05-07): the user-cap flag (set when a
    volunteer manually types a charge or clicks the ⚡ toggle)
    was previously a runtime-only attribute on ``PaymentRow``
    (``_user_capped``) — never persisted to the DB.  Save+reload
    silently dropped the volunteer's intent: a row pinned at $50
    came back as "auto-fillable", and the next Auto-Distribute
    or cap-aware engine pass could re-inflate the value, undoing
    the lock without warning.

    Persisting the flag makes user-cap a first-class concept
    that survives draft save/restore and adjustment round-trips.
    Combined with the engine's existing user_capped propagation
    (calculations.py cap paths + Pass 4) and the row-layer
    defensive floor in ``set_max_charge``, the volunteer's
    typed value is now end-to-end durable.

    Migration: ``ALTER TABLE ADD COLUMN`` with ``NOT NULL
    DEFAULT 0``.  All pre-existing rows backfill to 0 (= not
    user-capped) — an accurate default because the flag did not
    exist as a concept before this version, so no row could
    have been intentionally locked.  Idempotent re-run check
    via ``pragma_table_info``.
    """
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='payment_line_items'"
    ).fetchone()
    if not table_exists:
        logger.info(
            "Migration v36->v37: payment_line_items table not "
            "present yet; skipping (fresh install path will create "
            "the column via CREATE TABLE)")
        conn.commit()
        return
    cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(payment_line_items)").fetchall()}
    if 'user_capped' in cols:
        logger.info(
            "Migration v36->v37: user_capped column already "
            "present; skipping (idempotent re-run)")
        conn.commit()
        return
    conn.execute(
        "ALTER TABLE payment_line_items "
        "ADD COLUMN user_capped INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    logger.info(
        "Migration v36->v37 complete: user_capped column added "
        "to payment_line_items (default 0 for all pre-existing "
        "rows)")


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

    if current_version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {current_version} is newer than "
            f"this app supports (version {CURRENT_SCHEMA_VERSION}). "
            f"Please update the application."
        )

    if current_version < 1:
        # Fresh install — create all tables + triggers/indexes
        conn.executescript(TABLES_SQL)
        _migrate_v3_to_v4(conn)
        # v5 columns already in TABLES_SQL for fresh installs.
        # Seed system-managed payment methods that the app's UI
        # depends on (Unallocated Funds for the Adjustments
        # "customer gone" path).  The v24→v25 migration is idempotent:
        # ALTER TABLE is guarded, INSERT uses OR IGNORE.
        _migrate_v24_to_v25(conn)
        # v27→v28 trigger is also needed on fresh installs so the
        # per-line invariant is enforced from the very first write.
        # The migration is idempotent (CREATE TRIGGER IF NOT EXISTS).
        _migrate_v27_to_v28(conn)
        # v30→v31 also adds triggers (PLI UPDATE non-negativity +
        # voided-one-way) needed from the first write on fresh
        # installs.  Idempotent — see _migrate_v30_to_v31.
        _migrate_v30_to_v31(conn)
        # v31→v32 composite indexes for multi-year scaling.  Even
        # on fresh installs we want the indexes from day 1 so
        # query plans don't have to be regenerated when tables
        # eventually grow.  Idempotent — see _migrate_v31_to_v32.
        _migrate_v31_to_v32(conn)
        # v32→v33 Unallocated Funds zero-amount triggers — defense-
        # in-depth that fresh installs need just as much as
        # upgraders.  Without this call, brand new v2.0.1+
        # deployments stamp schema_version=33 but never get the
        # ``chk_pli_uf_zero_*`` triggers, so the very protection
        # v33 was added for is missing on the population that
        # needs it most (no migration history to lean on).
        # Idempotent — see _migrate_v32_to_v33.
        _migrate_v32_to_v33(conn)
        # v33→v34 schema_version dedupe + UNIQUE INDEX — also
        # idempotent.  Fresh installs benefit because future
        # Reset cycles will be constraint-protected from
        # producing duplicate version rows.
        _migrate_v33_to_v34(conn)
        # v34→v35 SNAP/Cash universal vendor binding.  No-op on a
        # truly fresh install (no vendors yet), but runs after
        # ``seed_sample_data`` for the Load-Defaults flow.
        _migrate_v34_to_v35(conn)
        # v35→v36 customer_forfeit_cents column.  No-op on fresh
        # install because the column is in the CREATE TABLE
        # definition above; idempotent re-runs are safe.
        _migrate_v35_to_v36(conn)
        # v36→v37 user_capped column.  Same idempotency rationale.
        _migrate_v36_to_v37(conn)
        # ``INSERT OR IGNORE`` is now constraint-protected by the
        # UNIQUE INDEX created above.  On a true fresh install the
        # table is empty so the insert always succeeds; on a
        # partial-init replay (e.g. interrupted seed) the ignore
        # prevents a duplicate row.
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,)
        )
        conn.commit()
        logger.info("Fresh install: schema at version %s", CURRENT_SCHEMA_VERSION)
        return True

    # ── Pre-migration backup ──────────────────────────────────
    # Snapshot the database before modifying it so a failed
    # migration can be recovered by restoring the .bak file.
    #
    # v2.0.1 hardening:
    # 1. Use ``sqlite3.Connection.backup()`` which is WAL-aware
    #    (raw ``shutil.copy2`` could miss uncheckpointed commits
    #    living in ``fam_data.db-wal``).
    # 2. Version-stamp the filename so multiple upgrades preserve
    #    every prior snapshot (the legacy single ``.pre-migration.bak``
    #    would be overwritten on the next upgrade).
    # 3. Retain at most ``_MAX_PRE_MIGRATION_BAKS`` snapshots —
    #    the rotated runtime backup directory provides additional
    #    rollback depth, so we don't need a long history here.
    #
    # v2.0.2 fix (DB-H2): backup failure is now FATAL for the
    # migration step.  Pre-fix this was a logger.warning + continue,
    # which let destructive migrations like v21→v22 (REAL→INTEGER
    # cents conversion) run with no rollback artifact.  If the user
    # later hit a migration error and the runbook said "restore
    # from .bak," the .bak wouldn't exist.  Now we raise so the
    # caller (``fam.app.run``) shows the existing "Database Error"
    # dialog and ``sys.exit(1)``.  The user keeps the original DB
    # untouched and can investigate (disk full / AV interference /
    # locked file) without their data being mid-migrated.
    if current_version < CURRENT_SCHEMA_VERSION:
        db_file = get_db_path()
        if os.path.exists(db_file):
            try:
                _write_pre_migration_backup(
                    conn, db_file, current_version,
                    CURRENT_SCHEMA_VERSION)
            except Exception as e:
                logger.error(
                    "Pre-migration backup failed; aborting migration "
                    "to protect user data.", exc_info=True)
                raise RuntimeError(
                    f"Could not create pre-migration backup before "
                    f"upgrading from schema v{current_version} to "
                    f"v{CURRENT_SCHEMA_VERSION}: {e}.  "
                    f"Migration cancelled to protect your data.  "
                    f"Check for disk space, antivirus interference, "
                    f"or a locked database file, then relaunch the app."
                ) from e

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

    if current_version < 12:
        _migrate_v11_to_v12(conn)
        current_version = 12

    if current_version < 13:
        _migrate_v12_to_v13(conn)
        current_version = 13

    if current_version < 14:
        _migrate_v13_to_v14(conn)
        current_version = 14

    if current_version < 15:
        _migrate_v14_to_v15(conn)
        current_version = 15

    if current_version < 16:
        _migrate_v15_to_v16(conn)
        current_version = 16

    if current_version < 17:
        _migrate_v16_to_v17(conn)
        current_version = 17

    if current_version < 18:
        _migrate_v17_to_v18(conn)
        current_version = 18

    if current_version < 19:
        _migrate_v18_to_v19(conn)
        current_version = 19

    if current_version < 20:
        _migrate_v19_to_v20(conn)
        current_version = 20

    if current_version < 21:
        _migrate_v20_to_v21(conn)
        current_version = 21

    if current_version < 22:
        _migrate_v21_to_v22(conn)
        current_version = 22

    if current_version < 23:
        _migrate_v22_to_v23(conn)
        current_version = 23

    if current_version < 24:
        _migrate_v23_to_v24(conn)
        current_version = 24

    if current_version < 25:
        _migrate_v24_to_v25(conn)
        current_version = 25

    # No v26 migration: see _migrate_v26_to_v27 docstring.  Some
    # installs ran a short-lived v26 build that stamped 26 into
    # schema_version; those installs hit the cleanup migration
    # below and end up at v27 alongside everyone else.
    if current_version < 27:
        _migrate_v26_to_v27(conn)
        current_version = 27

    if current_version < 28:
        _migrate_v27_to_v28(conn)
        current_version = 28

    if current_version < 29:
        _migrate_v28_to_v29(conn)
        current_version = 29

    if current_version < 30:
        _migrate_v29_to_v30(conn)
        current_version = 30

    if current_version < 31:
        _migrate_v30_to_v31(conn)
        current_version = 31

    if current_version < 32:
        _migrate_v31_to_v32(conn)
        current_version = 32

    if current_version < 33:
        _migrate_v32_to_v33(conn)
        current_version = 33

    if current_version < 34:
        _migrate_v33_to_v34(conn)
        current_version = 34

    if current_version < 35:
        _migrate_v34_to_v35(conn)
        current_version = 35

    if current_version < 36:
        _migrate_v35_to_v36(conn)
        current_version = 36

    if current_version < 37:
        _migrate_v36_to_v37(conn)
        current_version = 37

    # Record the final version (avoid duplicate if already at this version).
    # As of v34 there is a UNIQUE INDEX on schema_version.version, so this
    # SELECT-then-INSERT pattern is also constraint-protected.
    existing = conn.execute(
        "SELECT version FROM schema_version WHERE version = ?",
        (CURRENT_SCHEMA_VERSION,)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,)
        )
    conn.commit()
    logger.info("Schema at version %s", CURRENT_SCHEMA_VERSION)
    return True
