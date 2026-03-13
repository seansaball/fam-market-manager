"""Comprehensive tests for settings import/export.

Covers:
  - export_settings() — round-trip export to .fam file
  - parse_settings_file() — parsing, validation, sanitization
  - apply_import() — database insertion from parsed data
  - _sanitize_text() — control char removal, whitespace normalization
  - Edge cases: empty DB, truncation warnings, invalid values, duplicates
"""

import os
import pytest
from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.settings_io import (
    export_settings, parse_settings_file, apply_import, _sanitize_text,
    MAX_NAME_LEN, MAX_PM_NAME_LEN, MAX_ADDRESS_LEN, MAX_CONTACT_LEN,
    ImportResult, ImportMarket, ImportVendor, ImportPaymentMethod, ImportAssignment,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_settings_io.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    yield conn
    close_connection()


def _seed(conn):
    """Seed markets, vendors, payment methods, and assignments."""
    conn.execute(
        "INSERT INTO markets (id, name, address, daily_match_limit, match_limit_active)"
        " VALUES (1, 'Downtown', '123 Main St', 50.00, 1)"
    )
    conn.execute(
        "INSERT INTO markets (id, name, address, daily_match_limit, match_limit_active)"
        " VALUES (2, 'Riverside', '456 River Rd', 100.00, 0)"
    )
    conn.execute("INSERT INTO vendors (id, name, contact_info) VALUES (1, 'Farm A', 'farm@a.com')")
    conn.execute("INSERT INTO vendors (id, name, contact_info) VALUES (2, 'Farm B', NULL)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, sort_order, is_active)"
        " VALUES (1, 'SNAP', 100.0, 1, 1)"
    )
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, sort_order, is_active)"
        " VALUES (2, 'Cash', 0.0, 2, 1)"
    )
    conn.execute("INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, 1)")
    conn.execute("INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, 2)")
    conn.execute("INSERT INTO market_payment_methods (market_id, payment_method_id) VALUES (1, 1)")
    conn.commit()


# ══════════════════════════════════════════════════════════════════
# _sanitize_text()
# ══════════════════════════════════════════════════════════════════
class TestSanitizeText:

    def test_strips_control_chars(self):
        assert _sanitize_text("Hello\x00World") == "HelloWorld"
        assert _sanitize_text("Tab\there") == "Tabhere"
        assert _sanitize_text("New\nline") == "Newline"

    def test_collapses_spaces(self):
        assert _sanitize_text("too   many   spaces") == "too many spaces"

    def test_strips_whitespace(self):
        assert _sanitize_text("  padded  ") == "padded"

    def test_preserves_normal_text(self):
        assert _sanitize_text("Hello World") == "Hello World"

    def test_empty_string(self):
        assert _sanitize_text("") == ""

    def test_only_control_chars(self):
        assert _sanitize_text("\x00\x01\x02") == ""

    def test_unicode_preserved(self):
        assert _sanitize_text("Café Résumé") == "Café Résumé"


# ══════════════════════════════════════════════════════════════════
# export_settings()
# ══════════════════════════════════════════════════════════════════
class TestExportSettings:

    def test_export_creates_file(self, fresh_db, tmp_path):
        _seed(fresh_db)
        filepath = str(tmp_path / "export.fam")
        result = export_settings(filepath)
        assert os.path.exists(result)

    def test_export_contains_sections(self, fresh_db, tmp_path):
        _seed(fresh_db)
        filepath = str(tmp_path / "export.fam")
        export_settings(filepath)
        with open(filepath, 'r') as f:
            content = f.read()
        assert '=== Markets ===' in content
        assert '=== Vendors ===' in content
        assert '=== Payment Methods ===' in content
        assert '=== Market Vendors ===' in content
        assert '=== Market Payment Methods ===' in content

    def test_export_market_data(self, fresh_db, tmp_path):
        _seed(fresh_db)
        filepath = str(tmp_path / "export.fam")
        export_settings(filepath)
        with open(filepath, 'r') as f:
            content = f.read()
        assert 'Downtown' in content
        assert '123 Main St' in content
        assert '50.00' in content

    def test_export_vendor_data(self, fresh_db, tmp_path):
        _seed(fresh_db)
        filepath = str(tmp_path / "export.fam")
        export_settings(filepath)
        with open(filepath, 'r') as f:
            content = f.read()
        assert 'Farm A' in content
        assert 'farm@a.com' in content

    def test_export_assignment_data(self, fresh_db, tmp_path):
        _seed(fresh_db)
        filepath = str(tmp_path / "export.fam")
        export_settings(filepath)
        with open(filepath, 'r') as f:
            content = f.read()
        # Market vendors section should have Downtown | Farm A
        assert 'Downtown | Farm A' in content

    def test_export_empty_db(self, fresh_db, tmp_path):
        filepath = str(tmp_path / "export.fam")
        export_settings(filepath)
        with open(filepath, 'r') as f:
            content = f.read()
        assert '=== Markets ===' in content
        # File should still be valid even with no data rows

    def test_export_limit_active_no(self, fresh_db, tmp_path):
        _seed(fresh_db)
        filepath = str(tmp_path / "export.fam")
        export_settings(filepath)
        with open(filepath, 'r') as f:
            content = f.read()
        # Riverside has match_limit_active=0 → "No"
        assert 'No' in content


# ══════════════════════════════════════════════════════════════════
# parse_settings_file()
# ══════════════════════════════════════════════════════════════════
class TestParseSettingsFile:

    def _write_fam(self, tmp_path, content):
        filepath = str(tmp_path / "import.fam")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return filepath

    def test_parse_valid_file(self, fresh_db, tmp_path):
        content = """# FAM Settings
=== Markets ===
Name | Address | Daily Match Limit | Limit Active
New Market | 789 Oak St | 75.00 | Yes

=== Vendors ===
Name | Contact Info
New Vendor | vendor@test.com

=== Payment Methods ===
Name | Match % | Sort Order
Food Bucks | 100.0 | 3
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.markets) == 1
        assert result.markets[0].name == 'New Market'
        assert result.markets[0].daily_match_limit == 75.0
        assert len(result.vendors) == 1
        assert result.vendors[0].name == 'New Vendor'
        assert len(result.payment_methods) == 1
        assert result.payment_methods[0].match_percent == 100.0

    def test_parse_skips_comments(self, fresh_db, tmp_path):
        content = """# Comment line
=== Markets ===
Name | Address
# Another comment
Test Market | 123 St
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.markets) == 1

    def test_parse_empty_name_error(self, fresh_db, tmp_path):
        content = """=== Vendors ===
Name | Contact Info
 | empty@test.com
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.vendors) == 0
        assert any('empty' in e.lower() for e in result.errors)

    def test_parse_invalid_match_percent(self, fresh_db, tmp_path):
        content = """=== Markets ===
Name | Address

=== Payment Methods ===
Name | Match % | Sort Order
BadMethod | abc | 1
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.payment_methods) == 1
        assert result.payment_methods[0].match_percent == 0.0
        assert any("Invalid match" in e for e in result.errors)

    def test_parse_match_percent_out_of_range(self, fresh_db, tmp_path):
        content = """=== Markets ===
Name | Address

=== Payment Methods ===
Name | Match % | Sort Order
TooHigh | 1500 | 1
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert result.payment_methods[0].match_percent == 999
        assert any("0 and 999" in e for e in result.errors)

    def test_parse_negative_match_percent(self, fresh_db, tmp_path):
        content = """=== Markets ===
Name | Address

=== Payment Methods ===
Name | Match %
Negative | -50
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert result.payment_methods[0].match_percent == 0

    def test_parse_name_truncation(self, fresh_db, tmp_path):
        long_name = "A" * 200
        content = f"""=== Vendors ===
Name | Contact Info
{long_name} | test@test.com
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.vendors[0].name) == MAX_NAME_LEN
        assert any("truncated" in e.lower() for e in result.errors)

    def test_parse_pm_name_truncation(self, fresh_db, tmp_path):
        long_name = "B" * 100
        content = f"""=== Markets ===
Name | Address

=== Payment Methods ===
Name | Match %
{long_name} | 50
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.payment_methods[0].name) == MAX_PM_NAME_LEN

    def test_parse_address_truncation(self, fresh_db, tmp_path):
        long_addr = "X" * 300
        content = f"""=== Markets ===
Name | Address
Test Market | {long_addr}
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.markets[0].address) <= MAX_ADDRESS_LEN

    def test_parse_contact_truncation(self, fresh_db, tmp_path):
        long_contact = "Y" * 300
        content = f"""=== Vendors ===
Name | Contact Info
Test Vendor | {long_contact}
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.vendors[0].contact_info) <= MAX_CONTACT_LEN

    def test_parse_invalid_file_format(self, fresh_db, tmp_path):
        content = "This is not a valid FAM file at all."
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.errors) > 0
        assert len(result.markets) == 0

    def test_parse_nonexistent_file(self, fresh_db, tmp_path):
        result = parse_settings_file(str(tmp_path / "nonexistent.fam"))
        assert len(result.errors) > 0

    def test_parse_vendor_assignments(self, fresh_db, tmp_path):
        content = """=== Markets ===
Name | Address
TestMarket | Addr

=== Vendors ===
Name | Contact
TestVendor | info

=== Market Vendors ===
Market | Vendor
TestMarket | TestVendor
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.vendor_assignments) == 1
        assert result.vendor_assignments[0].market_name == 'TestMarket'
        assert result.vendor_assignments[0].entity_name == 'TestVendor'

    def test_parse_pm_assignments(self, fresh_db, tmp_path):
        content = """=== Markets ===
Name | Address
TestMarket | Addr

=== Payment Methods ===
Name | Match %
SNAP | 100

=== Market Payment Methods ===
Market | Payment Method
TestMarket | SNAP
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.pm_assignments) == 1

    def test_parse_default_match_limit(self, fresh_db, tmp_path):
        content = """=== Markets ===
Name | Address
TestMarket | Addr
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert result.markets[0].daily_match_limit == 100.00

    def test_parse_limit_active_variations(self, fresh_db, tmp_path):
        content = """=== Markets ===
Name | Address | Limit | Active
Yes Market | Addr | 50 | yes
No Market | Addr | 50 | no
True Market | Addr | 50 | true
One Market | Addr | 50 | 1
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert result.markets[0].limit_active is True
        assert result.markets[1].limit_active is False
        assert result.markets[2].limit_active is True
        assert result.markets[3].limit_active is True

    def test_parse_classifies_existing(self, fresh_db, tmp_path):
        _seed(fresh_db)
        content = """=== Vendors ===
Name | Contact Info
Farm A | existing@test.com
Brand New | new@test.com
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.vendors) == 2
        assert len(result.new_vendors) == 1
        assert result.new_vendors[0].name == 'Brand New'
        assert len(result.skipped_vendors) == 1
        assert result.skipped_vendors[0].name == 'Farm A'

    def test_control_chars_stripped(self, fresh_db, tmp_path):
        content = """=== Vendors ===
Name | Contact Info
Farm\x00Bad | test@test.com
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert result.vendors[0].name == 'FarmBad'

    def test_assignment_missing_field(self, fresh_db, tmp_path):
        content = """=== Markets ===
Name
TestMarket

=== Market Vendors ===
Market | Vendor
OnlyOne
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.vendor_assignments) == 0
        assert any("Assignment" in e for e in result.errors)


# ══════════════════════════════════════════════════════════════════
# apply_import()
# ══════════════════════════════════════════════════════════════════
class TestApplyImport:

    def test_apply_inserts_new_markets(self, fresh_db):
        result = ImportResult()
        result.markets = [ImportMarket('New Market', '123 St', 75.0, True)]
        result.existing_market_names = set()
        counts = apply_import(result)
        assert counts['markets_added'] == 1
        row = fresh_db.execute("SELECT * FROM markets WHERE name='New Market'").fetchone()
        assert row is not None
        assert row['daily_match_limit'] == 75.0

    def test_apply_skips_existing_markets(self, fresh_db):
        _seed(fresh_db)
        result = ImportResult()
        result.markets = [ImportMarket('Downtown', '999 New', 200.0, True)]
        result.existing_market_names = {'Downtown'}
        counts = apply_import(result)
        assert counts['markets_added'] == 0

    def test_apply_inserts_new_vendors(self, fresh_db):
        result = ImportResult()
        result.vendors = [ImportVendor('New Vendor', 'info@test.com')]
        result.existing_vendor_names = set()
        counts = apply_import(result)
        assert counts['vendors_added'] == 1

    def test_apply_inserts_new_payment_methods(self, fresh_db):
        result = ImportResult()
        result.payment_methods = [ImportPaymentMethod('Food Bucks', 100.0, 3)]
        result.existing_pm_names = set()
        counts = apply_import(result)
        assert counts['payment_methods_added'] == 1
        row = fresh_db.execute("SELECT * FROM payment_methods WHERE name='Food Bucks'").fetchone()
        assert row['match_percent'] == 100.0

    def test_apply_vendor_assignments(self, fresh_db):
        _seed(fresh_db)
        result = ImportResult()
        result.vendor_assignments = [ImportAssignment('Riverside', 'Farm A')]
        counts = apply_import(result)
        assert counts['vendor_assignments_added'] == 1
        row = fresh_db.execute(
            "SELECT * FROM market_vendors WHERE market_id=2 AND vendor_id=1"
        ).fetchone()
        assert row is not None

    def test_apply_pm_assignments(self, fresh_db):
        _seed(fresh_db)
        result = ImportResult()
        result.pm_assignments = [ImportAssignment('Riverside', 'SNAP')]
        counts = apply_import(result)
        assert counts['pm_assignments_added'] == 1

    def test_apply_assignment_unknown_market(self, fresh_db):
        _seed(fresh_db)
        result = ImportResult()
        result.vendor_assignments = [ImportAssignment('Nonexistent', 'Farm A')]
        counts = apply_import(result)
        assert counts['vendor_assignments_added'] == 0

    def test_apply_assignment_unknown_vendor(self, fresh_db):
        _seed(fresh_db)
        result = ImportResult()
        result.vendor_assignments = [ImportAssignment('Downtown', 'Nonexistent')]
        counts = apply_import(result)
        assert counts['vendor_assignments_added'] == 0


# ══════════════════════════════════════════════════════════════════
# Round-trip: export then import
# ══════════════════════════════════════════════════════════════════
class TestRoundTrip:

    def test_export_import_round_trip(self, fresh_db, tmp_path):
        """Export settings, clear DB, re-import — data should match."""
        _seed(fresh_db)
        filepath = str(tmp_path / "roundtrip.fam")
        export_settings(filepath)

        # Clear and re-import into fresh DB
        fresh_db.execute("DELETE FROM market_payment_methods")
        fresh_db.execute("DELETE FROM market_vendors")
        fresh_db.execute("DELETE FROM payment_methods")
        fresh_db.execute("DELETE FROM vendors")
        fresh_db.execute("DELETE FROM markets")
        fresh_db.commit()

        result = parse_settings_file(filepath)
        assert len(result.errors) == 0
        assert len(result.markets) == 2
        assert len(result.vendors) == 2
        assert len(result.payment_methods) == 2
        assert len(result.vendor_assignments) == 2
        assert len(result.pm_assignments) == 1

        counts = apply_import(result)
        assert counts['markets_added'] == 2
        assert counts['vendors_added'] == 2
        assert counts['payment_methods_added'] == 2

    def test_has_new_data_property(self, fresh_db, tmp_path):
        result = ImportResult()
        assert result.has_new_data is False
        result.markets = [ImportMarket('New', '', 100.0, True)]
        result.existing_market_names = set()
        assert result.has_new_data is True

    def test_has_new_data_all_existing(self, fresh_db, tmp_path):
        result = ImportResult()
        result.markets = [ImportMarket('Existing', '', 100.0, True)]
        result.existing_market_names = {'Existing'}
        assert result.has_new_data is False


# ══════════════════════════════════════════════════════════════════
# Vendor registration fields — export, parse, apply
# ══════════════════════════════════════════════════════════════════
class TestVendorRegistrationFields:

    def _write_fam(self, tmp_path, content):
        filepath = str(tmp_path / "vendor_reg.fam")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return filepath

    def test_export_includes_new_vendor_columns(self, fresh_db, tmp_path):
        """Export should include Check Payable To, Street, City, State, Zip, ACH."""
        fresh_db.execute(
            "INSERT INTO vendors (name, contact_info, check_payable_to,"
            " street, city, state, zip_code, ach_enabled)"
            " VALUES ('Test Farm', 'info@farm.com', 'Farm LLC',"
            " '100 Farm Rd', 'Pittsburgh', 'PA', '15213', 1)")
        fresh_db.commit()
        filepath = str(tmp_path / "vendor_export.fam")
        export_settings(filepath)
        with open(filepath, 'r') as f:
            content = f.read()
        assert 'Farm LLC' in content
        assert '100 Farm Rd' in content
        assert 'Pittsburgh' in content
        assert 'PA' in content
        assert '15213' in content
        assert 'Yes' in content  # ach_enabled

    def test_export_vendor_header_has_new_columns(self, fresh_db, tmp_path):
        """Vendor header should list all 8 columns."""
        filepath = str(tmp_path / "vendor_header.fam")
        export_settings(filepath)
        with open(filepath, 'r') as f:
            content = f.read()
        assert 'Name | Contact Info | Check Payable To | Street | City | State | Zip | ACH' in content

    def test_parse_new_8_column_vendor_format(self, fresh_db, tmp_path):
        """Parsing 8-column vendor rows should populate all new fields."""
        content = """=== Vendors ===
Name | Contact Info | Check Payable To | Street | City | State | Zip | ACH
Organic Farm | org@farm.com | Organic Farm LLC | 200 Green St | Bellevue | PA | 15202 | Yes
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.vendors) == 1
        v = result.vendors[0]
        assert v.name == 'Organic Farm'
        assert v.contact_info == 'org@farm.com'
        assert v.check_payable_to == 'Organic Farm LLC'
        assert v.street == '200 Green St'
        assert v.city == 'Bellevue'
        assert v.state == 'PA'
        assert v.zip_code == '15202'
        assert v.ach_enabled is True

    def test_parse_old_2_column_vendor_backward_compat(self, fresh_db, tmp_path):
        """Old-format 2-column vendor rows should still parse with defaults."""
        content = """=== Vendors ===
Name | Contact Info
Legacy Farm | legacy@farm.com
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.vendors) == 1
        v = result.vendors[0]
        assert v.name == 'Legacy Farm'
        assert v.contact_info == 'legacy@farm.com'
        assert v.check_payable_to == ''
        assert v.street == ''
        assert v.city == ''
        assert v.state == ''
        assert v.zip_code == ''
        assert v.ach_enabled is False

    def test_parse_ach_variations(self, fresh_db, tmp_path):
        """ACH field should accept 'Yes', 'true', '1', 'on'."""
        content = """=== Vendors ===
Name | Contact | Payable | Street | City | State | Zip | ACH
Farm1 | | | | | | | Yes
Farm2 | | | | | | | true
Farm3 | | | | | | | 1
Farm4 | | | | | | | no
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert result.vendors[0].ach_enabled is True
        assert result.vendors[1].ach_enabled is True
        assert result.vendors[2].ach_enabled is True
        assert result.vendors[3].ach_enabled is False

    def test_parse_state_truncated_to_2_chars(self, fresh_db, tmp_path):
        """State should be truncated to 2 characters."""
        content = """=== Vendors ===
Name | Contact | Payable | Street | City | State | Zip | ACH
Test | | | | | Pennsylvania | |
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert result.vendors[0].state == 'Pe'

    def test_parse_zip_truncated_to_10_chars(self, fresh_db, tmp_path):
        """Zip should be truncated to 10 characters."""
        content = """=== Vendors ===
Name | Contact | Payable | Street | City | State | Zip | ACH
Test | | | | | PA | 15213-456789 |
"""
        filepath = self._write_fam(tmp_path, content)
        result = parse_settings_file(filepath)
        assert len(result.vendors[0].zip_code) <= 10

    def test_apply_import_vendor_with_new_fields(self, fresh_db):
        """apply_import should insert vendors with all new registration fields."""
        result = ImportResult()
        result.vendors = [ImportVendor(
            'New Farm', 'new@farm.com', 'New Farm LLC',
            '300 New Rd', 'Moon', 'PA', '15108', True
        )]
        result.existing_vendor_names = set()
        counts = apply_import(result)
        assert counts['vendors_added'] == 1

        row = fresh_db.execute(
            "SELECT * FROM vendors WHERE name='New Farm'"
        ).fetchone()
        assert row is not None
        assert row['contact_info'] == 'new@farm.com'
        assert row['check_payable_to'] == 'New Farm LLC'
        assert row['street'] == '300 New Rd'
        assert row['city'] == 'Moon'
        assert row['state'] == 'PA'
        assert row['zip_code'] == '15108'
        assert row['ach_enabled'] == 1

    def test_apply_import_vendor_defaults(self, fresh_db):
        """apply_import with minimal vendor should use defaults for new fields."""
        result = ImportResult()
        result.vendors = [ImportVendor('Minimal Farm', 'min@farm.com')]
        result.existing_vendor_names = set()
        counts = apply_import(result)
        assert counts['vendors_added'] == 1

        row = fresh_db.execute(
            "SELECT * FROM vendors WHERE name='Minimal Farm'"
        ).fetchone()
        assert row['check_payable_to'] is None
        assert row['street'] is None
        assert row['ach_enabled'] == 0

    def test_round_trip_with_new_vendor_fields(self, fresh_db, tmp_path):
        """Export vendors with new fields, then re-import — data should match."""
        fresh_db.execute(
            "INSERT INTO vendors (name, contact_info, check_payable_to,"
            " street, city, state, zip_code, ach_enabled)"
            " VALUES ('Round Trip Farm', 'rt@farm.com', 'RT LLC',"
            " '500 RT Blvd', 'Robinson', 'PA', '15205', 1)")
        fresh_db.commit()

        filepath = str(tmp_path / "rt_vendor.fam")
        export_settings(filepath)

        # Clear vendors and re-import
        fresh_db.execute("DELETE FROM market_payment_methods")
        fresh_db.execute("DELETE FROM market_vendors")
        fresh_db.execute("DELETE FROM vendors")
        fresh_db.commit()

        result = parse_settings_file(filepath)
        assert len(result.errors) == 0
        v = result.vendors[0]
        assert v.name == 'Round Trip Farm'
        assert v.check_payable_to == 'RT LLC'
        assert v.street == '500 RT Blvd'
        assert v.city == 'Robinson'
        assert v.state == 'PA'
        assert v.zip_code == '15205'
        assert v.ach_enabled is True

        counts = apply_import(result)
        assert counts['vendors_added'] == 1

        row = fresh_db.execute(
            "SELECT * FROM vendors WHERE name='Round Trip Farm'"
        ).fetchone()
        assert row['check_payable_to'] == 'RT LLC'
        assert row['ach_enabled'] == 1
