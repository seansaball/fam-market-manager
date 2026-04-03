"""Tests for payment method denominations.

Covers:
  - Schema migration v11→v12 (denomination column)
  - Model layer (create/update with denomination)
  - PaymentRow.validate_denomination() logic (replicated without Qt)
  - Settings I/O (export/import denomination field)
"""

import sqlite3
import pytest
from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database, CURRENT_SCHEMA_VERSION


# ──────────────────────────────────────────────────────────────────
# Fixture: fresh database per test
# ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_denomination.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    yield conn
    close_connection()


# ══════════════════════════════════════════════════════════════════
# 1. Schema: denomination column exists
# ══════════════════════════════════════════════════════════════════
class TestSchemaColumn:

    def test_denomination_column_exists(self, fresh_db):
        """payment_methods table should have a denomination column."""
        cols = {r[1] for r in fresh_db.execute(
            "PRAGMA table_info(payment_methods)"
        ).fetchall()}
        assert 'denomination' in cols

    def test_denomination_default_null(self, fresh_db):
        """New payment method without denomination should default to NULL."""
        fresh_db.execute(
            "INSERT INTO payment_methods (name, match_percent) VALUES ('SNAP', 100)"
        )
        fresh_db.commit()
        row = fresh_db.execute(
            "SELECT denomination FROM payment_methods WHERE name='SNAP'"
        ).fetchone()
        assert row[0] is None

    def test_denomination_can_be_set(self, fresh_db):
        """Denomination value can be explicitly set."""
        fresh_db.execute(
            "INSERT INTO payment_methods (name, match_percent, denomination) "
            "VALUES ('FMNP', 100, 2500)"
        )
        fresh_db.commit()
        row = fresh_db.execute(
            "SELECT denomination FROM payment_methods WHERE name='FMNP'"
        ).fetchone()
        assert row[0] == 2500


# ══════════════════════════════════════════════════════════════════
# 2. Schema migration v11 → v12
# ══════════════════════════════════════════════════════════════════
class TestMigrationV11ToV12:

    def _create_v11_db(self, db_file):
        """Create a minimal v11 database without denomination column."""
        conn = sqlite3.connect(db_file)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE markets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                address TEXT,
                is_active BOOLEAN DEFAULT 1,
                daily_match_limit INTEGER DEFAULT 10000,
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
                receipt_total INTEGER NOT NULL,
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
                method_amount INTEGER NOT NULL,
                match_amount INTEGER NOT NULL,
                customer_charged INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (transaction_id) REFERENCES transactions(id),
                FOREIGN KEY (payment_method_id) REFERENCES payment_methods(id)
            );
            CREATE TABLE fmnp_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_day_id INTEGER NOT NULL,
                vendor_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                check_count INTEGER,
                notes TEXT,
                entered_by TEXT NOT NULL,
                status TEXT DEFAULT 'Active',
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
            CREATE TABLE market_payment_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id INTEGER NOT NULL,
                payment_method_id INTEGER NOT NULL,
                FOREIGN KEY (market_id) REFERENCES markets(id),
                FOREIGN KEY (payment_method_id) REFERENCES payment_methods(id),
                UNIQUE(market_id, payment_method_id)
            );
            CREATE TABLE app_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE schema_version (
                version INTEGER,
                applied_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO schema_version (version) VALUES (11);
        """)
        # Seed data to test survival
        conn.execute(
            "INSERT INTO payment_methods (name, match_percent, sort_order)"
            " VALUES ('SNAP', 100.0, 1)"
        )
        conn.execute(
            "INSERT INTO payment_methods (name, match_percent, sort_order)"
            " VALUES ('Cash', 0.0, 2)"
        )
        conn.commit()
        conn.close()

    def test_migration_adds_column(self, fresh_db, tmp_path):
        """v11→v12 migration adds denomination column."""
        db_file = str(tmp_path / "v11_migrate.db")
        close_connection()
        self._create_v11_db(db_file)
        set_db_path(db_file)
        initialize_database()

        conn = get_connection()
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(payment_methods)"
        ).fetchall()}
        assert 'denomination' in cols

    def test_migration_preserves_data(self, fresh_db, tmp_path):
        """Existing payment methods survive migration."""
        db_file = str(tmp_path / "v11_data.db")
        close_connection()
        self._create_v11_db(db_file)
        set_db_path(db_file)
        initialize_database()

        conn = get_connection()
        snap = conn.execute(
            "SELECT name, match_percent, denomination FROM payment_methods WHERE name='SNAP'"
        ).fetchone()
        assert snap is not None
        assert snap[1] == 100.0
        assert snap[2] is None  # denomination should be NULL for existing

    def test_migration_version_bumped(self, fresh_db, tmp_path):
        """Schema version should be at current after migration."""
        db_file = str(tmp_path / "v11_version.db")
        close_connection()
        self._create_v11_db(db_file)
        set_db_path(db_file)
        initialize_database()

        conn = get_connection()
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        assert version == CURRENT_SCHEMA_VERSION


# ══════════════════════════════════════════════════════════════════
# 3. Model layer: create/update with denomination
# ══════════════════════════════════════════════════════════════════
class TestModelLayer:

    def test_create_without_denomination(self, fresh_db):
        from fam.models.payment_method import create_payment_method, get_all_payment_methods
        pm_id = create_payment_method('SNAP', 100.0, sort_order=1)
        methods = get_all_payment_methods()
        snap = next(m for m in methods if m['id'] == pm_id)
        assert snap['denomination'] is None

    def test_create_with_denomination(self, fresh_db):
        from fam.models.payment_method import create_payment_method, get_all_payment_methods
        pm_id = create_payment_method('FMNP', 100.0, sort_order=1, denomination=2500)
        methods = get_all_payment_methods()
        fmnp = next(m for m in methods if m['id'] == pm_id)
        assert fmnp['denomination'] == 2500

    def test_update_add_denomination(self, fresh_db):
        from fam.models.payment_method import create_payment_method, update_payment_method, get_all_payment_methods
        pm_id = create_payment_method('FMNP', 100.0, sort_order=1)
        update_payment_method(pm_id, 'FMNP', 100.0, sort_order=1, denomination=5000)
        methods = get_all_payment_methods()
        fmnp = next(m for m in methods if m['id'] == pm_id)
        assert fmnp['denomination'] == 5000

    def test_update_clear_denomination(self, fresh_db):
        from fam.models.payment_method import create_payment_method, update_payment_method, get_all_payment_methods
        pm_id = create_payment_method('FMNP', 100.0, sort_order=1, denomination=2500)
        # Setting denomination to 0 clears it to NULL
        update_payment_method(pm_id, 'FMNP', 100.0, sort_order=1, denomination=0)
        methods = get_all_payment_methods()
        fmnp = next(m for m in methods if m['id'] == pm_id)
        assert fmnp['denomination'] is None

    def test_update_change_denomination(self, fresh_db):
        from fam.models.payment_method import create_payment_method, update_payment_method, get_all_payment_methods
        pm_id = create_payment_method('FMNP', 100.0, sort_order=1, denomination=5000)
        update_payment_method(pm_id, 'FMNP', 100.0, sort_order=1, denomination=2500)
        methods = get_all_payment_methods()
        fmnp = next(m for m in methods if m['id'] == pm_id)
        assert fmnp['denomination'] == 2500


# ══════════════════════════════════════════════════════════════════
# 4. Denomination validation logic (replicated without Qt)
# ══════════════════════════════════════════════════════════════════
def validate_denomination(charge, denomination):
    """Replicate PaymentRow.validate_denomination() math.

    Returns error string if invalid, None if OK.
    charge and denomination are in cents (integers).
    """
    if denomination is None or denomination <= 0:
        return None
    if charge > 0 and charge % denomination != 0:
        return f"Must be in ${denomination // 100} increments (entered ${charge / 100:.2f})"
    return None


class TestDenominationValidation:

    def test_valid_exact_multiple(self):
        """$50 in $25 increments → valid."""
        assert validate_denomination(5000, 2500) is None

    def test_valid_single_increment(self):
        """$25 in $25 increments → valid."""
        assert validate_denomination(2500, 2500) is None

    def test_valid_zero_charge(self):
        """$0 charge → always valid (no charge, no check)."""
        assert validate_denomination(0, 2500) is None

    def test_invalid_non_multiple(self):
        """$30 in $25 increments → error."""
        result = validate_denomination(3000, 2500)
        assert result is not None
        assert "$25" in result

    def test_invalid_partial_increment(self):
        """$12.50 in $25 increments → error."""
        result = validate_denomination(1250, 2500)
        assert result is not None

    def test_null_denomination_always_valid(self):
        """No denomination set → any amount is valid."""
        assert validate_denomination(12345, None) is None

    def test_large_valid_multiple(self):
        """$100 in $25 increments → valid."""
        assert validate_denomination(10000, 2500) is None

    def test_50_denomination(self):
        """$50 increment: $100 valid, $75 invalid."""
        assert validate_denomination(10000, 5000) is None
        assert validate_denomination(7500, 5000) is not None

    def test_odd_charge_with_denomination(self):
        """$33 in $25 increments → error."""
        result = validate_denomination(3300, 2500)
        assert result is not None

    @pytest.mark.parametrize("charge", [2500, 5000, 7500, 10000, 12500, 15000, 17500, 20000])
    def test_valid_multiples_of_25(self, charge):
        """All multiples of 2500 cents should be valid."""
        assert validate_denomination(charge, 2500) is None

    @pytest.mark.parametrize("charge", [100, 1000, 2400, 2600, 4900, 5100, 9900, 10100])
    def test_invalid_non_multiples_of_25(self, charge):
        """Non-multiples of 2500 cents should be invalid."""
        assert validate_denomination(charge, 2500) is not None


# ══════════════════════════════════════════════════════════════════
# 5. Settings I/O: denomination in export/import
# ══════════════════════════════════════════════════════════════════
class TestSettingsIO:

    def test_export_includes_denomination(self, fresh_db, tmp_path):
        """Exported .fam file should include denomination column."""
        from fam.settings_io import export_settings
        from fam.models.payment_method import create_payment_method
        create_payment_method('FMNP', 100.0, sort_order=1, denomination=2500)
        create_payment_method('SNAP', 100.0, sort_order=2)

        filepath = str(tmp_path / "export.fam")
        export_settings(filepath)

        with open(filepath, 'r') as f:
            content = f.read()
        assert 'Denomination' in content
        assert '25.0' in content

    def test_import_with_denomination(self, fresh_db, tmp_path):
        """Import a .fam file that includes denomination values."""
        from fam.settings_io import parse_settings_file
        content = """# FAM Settings
=== Markets ===
Name | Address
TestMarket | Addr

=== Payment Methods ===
Name | Match % | Sort Order | Denomination
FMNP | 100.0 | 1 | 25.0
SNAP | 100.0 | 2 |
Cash | 0.0 | 3
"""
        filepath = str(tmp_path / "import.fam")
        with open(filepath, 'w') as f:
            f.write(content)

        result = parse_settings_file(filepath)
        assert len(result.payment_methods) == 3
        assert result.payment_methods[0].denomination == 25.0
        assert result.payment_methods[1].denomination is None
        assert result.payment_methods[2].denomination is None

    def test_import_old_format_no_denomination(self, fresh_db, tmp_path):
        """Old .fam files without denomination column still import fine."""
        from fam.settings_io import parse_settings_file
        content = """# FAM Settings
=== Markets ===
Name | Address
TestMarket | Addr

=== Payment Methods ===
Name | Match % | Sort Order
SNAP | 100.0 | 1
Cash | 0.0 | 2
"""
        filepath = str(tmp_path / "old_import.fam")
        with open(filepath, 'w') as f:
            f.write(content)

        result = parse_settings_file(filepath)
        assert len(result.payment_methods) == 2
        assert result.payment_methods[0].denomination is None
        assert result.payment_methods[1].denomination is None
        assert len(result.errors) == 0

    def test_import_invalid_denomination(self, fresh_db, tmp_path):
        """Invalid denomination value generates error but still imports."""
        from fam.settings_io import parse_settings_file
        content = """# FAM Settings
=== Markets ===
Name | Address
TestMarket | Addr

=== Payment Methods ===
Name | Match % | Sort Order | Denomination
BadDenom | 100.0 | 1 | abc
"""
        filepath = str(tmp_path / "bad_denom.fam")
        with open(filepath, 'w') as f:
            f.write(content)

        result = parse_settings_file(filepath)
        assert len(result.payment_methods) == 1
        assert result.payment_methods[0].denomination is None
        assert any("denomination" in e.lower() for e in result.errors)

    def test_import_negative_denomination(self, fresh_db, tmp_path):
        """Negative denomination generates error."""
        from fam.settings_io import parse_settings_file
        content = """# FAM Settings
=== Markets ===
Name | Address
TestMarket | Addr

=== Payment Methods ===
Name | Match % | Sort Order | Denomination
BadDenom | 100.0 | 1 | -25.0
"""
        filepath = str(tmp_path / "neg_denom.fam")
        with open(filepath, 'w') as f:
            f.write(content)

        result = parse_settings_file(filepath)
        assert result.payment_methods[0].denomination is None
        assert any("positive" in e.lower() for e in result.errors)

    def test_apply_import_with_denomination(self, fresh_db, tmp_path):
        """apply_import inserts denomination into the database."""
        from fam.settings_io import parse_settings_file, apply_import
        content = """# FAM Settings
=== Markets ===
Name | Address
TestMarket | Addr

=== Payment Methods ===
Name | Match % | Sort Order | Denomination
FMNP | 100.0 | 1 | 25.0
SNAP | 100.0 | 2 |
"""
        filepath = str(tmp_path / "apply_denom.fam")
        with open(filepath, 'w') as f:
            f.write(content)

        result = parse_settings_file(filepath)
        counts = apply_import(result)
        assert counts['payment_methods_added'] == 2

        row = fresh_db.execute(
            "SELECT denomination FROM payment_methods WHERE name='FMNP'"
        ).fetchone()
        assert row[0] == 2500

        row2 = fresh_db.execute(
            "SELECT denomination FROM payment_methods WHERE name='SNAP'"
        ).fetchone()
        assert row2[0] is None

    def test_round_trip_with_denomination(self, fresh_db, tmp_path):
        """Export → clear → import preserves denomination."""
        from fam.settings_io import export_settings, parse_settings_file, apply_import
        from fam.models.payment_method import create_payment_method
        create_payment_method('FMNP', 100.0, sort_order=1, denomination=2500)
        create_payment_method('Cash', 0.0, sort_order=2)

        filepath = str(tmp_path / "rt_denom.fam")
        export_settings(filepath)

        # Clear
        fresh_db.execute("DELETE FROM payment_methods")
        fresh_db.commit()

        result = parse_settings_file(filepath)
        assert len(result.errors) == 0
        counts = apply_import(result)
        assert counts['payment_methods_added'] == 2

        fmnp = fresh_db.execute(
            "SELECT denomination FROM payment_methods WHERE name='FMNP'"
        ).fetchone()
        assert fmnp[0] == 2500

        cash = fresh_db.execute(
            "SELECT denomination FROM payment_methods WHERE name='Cash'"
        ).fetchone()
        assert cash[0] is None
