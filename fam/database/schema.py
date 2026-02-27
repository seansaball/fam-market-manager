"""Database schema creation and migrations."""

from .connection import get_connection

CURRENT_SCHEMA_VERSION = 3

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    address TEXT,
    is_active BOOLEAN DEFAULT 1
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
    discount_percent REAL NOT NULL,
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
    discount_percent_snapshot REAL NOT NULL,
    method_amount REAL NOT NULL,
    discount_amount REAL NOT NULL,
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
    except Exception:
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
        # Fresh install — create all tables
        conn.executescript(TABLES_SQL)
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,)
        )
        conn.commit()
        return True

    if current_version < 2:
        _migrate_v1_to_v2(conn)
        current_version = 2

    if current_version < 3:
        _migrate_v2_to_v3(conn)
        current_version = 3

    if current_version >= 2:
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,)
        )
        conn.commit()
        return True

    return False
