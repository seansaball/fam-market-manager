"""Tests for market code + device ID helpers and their integration points.

Covers:
  - app_settings.py: get/set_market_code, derive_market_code,
                     update_market_code_from_name,
                     get/capture_device_id, _read_machine_guid
  - transaction.py: generate_transaction_id with market code
  - export.py: generate_export_filename, export_dataframe_to_csv identity columns
  - backup.py: backup filename with market code
"""

import os

import pandas as pd
import pytest

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.utils.app_settings import (
    get_market_code, set_market_code, get_device_id,
    capture_device_id, _read_machine_guid,
    derive_market_code, update_market_code_from_name,
)
from fam.models.transaction import generate_transaction_id, create_transaction
from fam.utils.export import generate_export_filename, export_dataframe_to_csv


# ──────────────────────────────────────────────────────────────────
# Fixture: fresh database per test
# ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_market_code.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield tmp_path
    close_connection()


def _seed_market_day(date='2026-03-01'):
    """Seed minimal data to create transactions."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, address) VALUES (1, 'Test Market', '1 St')"
    )
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'Vendor A')"
    )
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, sort_order) "
        "VALUES (1, 'SNAP', 100.0, 1)"
    )
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status) "
        f"VALUES (1, 1, '{date}', 'Open')"
    )
    conn.commit()


# ══════════════════════════════════════════════════════════════════
# derive_market_code (pure function, no DB needed)
# ══════════════════════════════════════════════════════════════════
class TestDeriveMarketCode:

    def test_two_words(self, fresh_db):
        assert derive_market_code("Downtown Market") == "DM"

    def test_three_words(self, fresh_db):
        assert derive_market_code("West Side Farmers") == "WSF"

    def test_four_words_clamped(self, fresh_db):
        assert derive_market_code("West Side Farmers Market") == "WSFM"

    def test_five_words_clamped_to_four(self, fresh_db):
        assert derive_market_code("Big Old North Side Market") == "BONS"

    def test_single_word(self, fresh_db):
        assert derive_market_code("Riverside") == "RI"

    def test_single_short_word(self, fresh_db):
        assert derive_market_code("Ok") == "OK"

    def test_lowercase_input(self, fresh_db):
        assert derive_market_code("downtown market") == "DM"

    def test_mixed_case(self, fresh_db):
        assert derive_market_code("North Market") == "NM"

    def test_fallback_for_empty(self, fresh_db):
        assert derive_market_code("") == "MK"

    def test_fallback_for_numeric(self, fresh_db):
        assert derive_market_code("123") == "MK"


# ══════════════════════════════════════════════════════════════════
# Market Code helpers (DB-backed)
# ══════════════════════════════════════════════════════════════════
class TestMarketCode:

    def test_get_market_code_default_none(self, fresh_db):
        assert get_market_code() is None

    def test_set_and_get(self, fresh_db):
        set_market_code("DT")
        assert get_market_code() == "DT"

    def test_set_lowercased_input(self, fresh_db):
        set_market_code("ws")
        assert get_market_code() == "WS"

    def test_set_strips_whitespace(self, fresh_db):
        set_market_code("  AB  ")
        assert get_market_code() == "AB"

    def test_set_single_char(self, fresh_db):
        """Single char allowed (e.g. derived from single-initial market)."""
        set_market_code("M")
        assert get_market_code() == "M"

    def test_set_four_chars(self, fresh_db):
        set_market_code("FARM")
        assert get_market_code() == "FARM"

    def test_reject_five_chars(self, fresh_db):
        with pytest.raises(ValueError):
            set_market_code("ABCDE")

    def test_reject_digits(self, fresh_db):
        with pytest.raises(ValueError):
            set_market_code("D1")

    def test_reject_special(self, fresh_db):
        with pytest.raises(ValueError):
            set_market_code("D-T")

    def test_reject_empty(self, fresh_db):
        with pytest.raises(ValueError):
            set_market_code("")

    def test_overwrite(self, fresh_db):
        set_market_code("DT")
        set_market_code("WS")
        assert get_market_code() == "WS"

    def test_update_from_name(self, fresh_db):
        code = update_market_code_from_name("Downtown Market")
        assert code == "DM"
        assert get_market_code() == "DM"

    def test_update_from_name_overwrites(self, fresh_db):
        update_market_code_from_name("Downtown Market")
        update_market_code_from_name("West Side Farmers")
        assert get_market_code() == "WSF"


# ══════════════════════════════════════════════════════════════════
# Device ID helpers
# ══════════════════════════════════════════════════════════════════
class TestDeviceId:

    def test_get_device_id_default_none(self, fresh_db):
        assert get_device_id() is None

    def test_capture_stores_value(self, fresh_db):
        result = capture_device_id()
        assert result is not None
        assert len(result) > 0
        assert get_device_id() == result

    def test_capture_idempotent(self, fresh_db):
        first = capture_device_id()
        second = capture_device_id()
        assert first == second

    def test_read_machine_guid_returns_string(self, fresh_db):
        guid = _read_machine_guid()
        assert isinstance(guid, str)
        assert len(guid) > 0

    def test_read_machine_guid_fallback(self, fresh_db, monkeypatch):
        """When winreg fails, should fall back to hostname."""
        # Mock winreg.OpenKey to raise so fallback triggers
        try:
            import winreg
            monkeypatch.setattr(winreg, 'OpenKey', lambda *a: (_ for _ in ()).throw(OSError("mocked")))
        except ImportError:
            pass  # Not on Windows, fallback will happen anyway

        guid = _read_machine_guid()
        assert isinstance(guid, str)
        assert len(guid) > 0


# ══════════════════════════════════════════════════════════════════
# Transaction ID format with market code
# ══════════════════════════════════════════════════════════════════
class TestTransactionIdWithCode:

    def test_without_code_legacy_format(self, fresh_db):
        _seed_market_day()
        fam_id = generate_transaction_id('2026-03-01')
        assert fam_id == 'FAM-20260301-0001'

    def test_with_code_new_format(self, fresh_db):
        _seed_market_day()
        set_market_code("DT")
        fam_id = generate_transaction_id('2026-03-01')
        assert fam_id == 'FAM-DT-20260301-0001'

    def test_sequential_with_code(self, fresh_db):
        _seed_market_day()
        set_market_code("DT")
        create_transaction(1, 1, 50.0)
        fam_id = generate_transaction_id('2026-03-01')
        assert fam_id == 'FAM-DT-20260301-0002'

    def test_backward_compat_sequence(self, fresh_db):
        """If old-format transactions exist, new-format should continue the sequence."""
        _seed_market_day()
        # Create a transaction without market code (old format)
        create_transaction(1, 1, 50.0)  # FAM-20260301-0001

        # Now set a market code
        set_market_code("DT")
        fam_id = generate_transaction_id('2026-03-01')
        # Should continue from 0001, making it 0002
        assert fam_id == 'FAM-DT-20260301-0002'

    def test_create_transaction_returns_new_format(self, fresh_db):
        _seed_market_day()
        set_market_code("WS")
        tid, fam_id = create_transaction(1, 1, 50.0)
        assert fam_id.startswith("FAM-WS-")
        assert fam_id.endswith("-0001")

    def test_derived_code_in_transaction_id(self, fresh_db):
        """End-to-end: derive code from market name, verify transaction ID."""
        _seed_market_day()
        update_market_code_from_name("Downtown Market")
        fam_id = generate_transaction_id('2026-03-01')
        assert fam_id == 'FAM-DM-20260301-0001'


# ══════════════════════════════════════════════════════════════════
# Export filename with market code
# ══════════════════════════════════════════════════════════════════
class TestExportFilename:

    def test_without_code(self, fresh_db):
        name = generate_export_filename("vendor reimbursement")
        assert name.startswith("fam_vendor_reimbursement_")
        assert name.endswith(".csv")

    def test_with_code(self, fresh_db):
        set_market_code("DT")
        name = generate_export_filename("vendor reimbursement")
        assert name.startswith("fam_DT_vendor_reimbursement_")
        assert name.endswith(".csv")

    def test_custom_extension(self, fresh_db):
        set_market_code("NMK")
        name = generate_export_filename("report", extension="xlsx")
        assert name.startswith("fam_NMK_report_")
        assert name.endswith(".xlsx")

    def test_derived_code_in_filename(self, fresh_db):
        update_market_code_from_name("West Side Farmers")
        name = generate_export_filename("ledger")
        assert name.startswith("fam_WSF_ledger_")


# ══════════════════════════════════════════════════════════════════
# CSV export identity columns
# ══════════════════════════════════════════════════════════════════
class TestCsvIdentityColumns:

    def test_columns_injected(self, fresh_db, tmp_path):
        set_market_code("DT")
        capture_device_id()

        df = pd.DataFrame({'vendor': ['A', 'B'], 'amount': [10, 20]})
        outpath = str(tmp_path / "test.csv")
        export_dataframe_to_csv(df, outpath)

        result = pd.read_csv(outpath)
        assert list(result.columns[:2]) == ['market_code', 'device_id']
        assert all(result['market_code'] == 'DT')
        assert len(result['device_id'][0]) > 0

    def test_columns_empty_without_settings(self, fresh_db, tmp_path):
        df = pd.DataFrame({'col1': [1]})
        outpath = str(tmp_path / "test.csv")
        export_dataframe_to_csv(df, outpath)

        result = pd.read_csv(outpath)
        assert 'market_code' in result.columns
        assert 'device_id' in result.columns

    def test_original_df_not_mutated(self, fresh_db, tmp_path):
        set_market_code("WS")
        df = pd.DataFrame({'x': [1, 2, 3]})
        original_cols = list(df.columns)
        outpath = str(tmp_path / "test.csv")
        export_dataframe_to_csv(df, outpath)
        assert list(df.columns) == original_cols


# ══════════════════════════════════════════════════════════════════
# Backup filename with market code
# ══════════════════════════════════════════════════════════════════
class TestBackupFilename:

    def test_backup_without_code(self, fresh_db):
        from fam.database.backup import create_backup
        result = create_backup(reason="test")
        assert result is not None
        basename = os.path.basename(result)
        assert basename.startswith("fam_backup_")
        assert basename.endswith("_test.db")

    def test_backup_with_code(self, fresh_db):
        set_market_code("DT")
        from fam.database.backup import create_backup
        result = create_backup(reason="auto")
        assert result is not None
        basename = os.path.basename(result)
        assert basename.startswith("fam_DT_backup_")
        assert basename.endswith("_auto.db")

    def test_retention_matches_both_formats(self, fresh_db):
        """Retention should clean up both old and new format backup files."""
        from fam.database.backup import get_backup_dir, _enforce_retention, BACKUP_RETENTION_COUNT
        bdir = get_backup_dir()

        # Create a mix of old and new format files
        total = BACKUP_RETENTION_COUNT + 5
        for i in range(total):
            if i % 2 == 0:
                name = f"fam_backup_20260101_{i:06d}_auto.db"
            else:
                name = f"fam_DT_backup_20260101_{i:06d}_auto.db"
            with open(os.path.join(bdir, name), "w") as f:
                f.write("x")

        _enforce_retention(bdir)
        remaining = [f for f in os.listdir(bdir)
                     if f.startswith("fam_") and f.endswith(".db") and "backup_" in f]
        assert len(remaining) == BACKUP_RETENTION_COUNT
