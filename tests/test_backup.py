"""Tests for fam.database.backup — backup creation, retention, edge cases."""

import os
import sqlite3
import time

import pytest

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.database import backup as backup_mod
from fam.database.backup import (
    create_backup,
    get_backup_dir,
    _enforce_retention,
    BACKUP_RETENTION_COUNT,
)


# ──────────────────────────────────────────────────────────────────
# Fixture: fresh database per test
# ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_backup.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield tmp_path
    close_connection()


# ──────────────────────────────────────────────────────────────────
# get_backup_dir
# ──────────────────────────────────────────────────────────────────
class TestGetBackupDir:
    def test_creates_directory(self, fresh_db):
        bdir = get_backup_dir()
        assert os.path.isdir(bdir)
        assert bdir.endswith("backups")

    def test_idempotent(self, fresh_db):
        bdir1 = get_backup_dir()
        bdir2 = get_backup_dir()
        assert bdir1 == bdir2

    def test_under_data_dir(self, fresh_db):
        """Backup dir should be a sibling of the database file."""
        from fam.database.connection import get_db_path
        db_path = get_db_path()
        data_dir = os.path.dirname(os.path.abspath(db_path))
        bdir = get_backup_dir()
        assert os.path.dirname(bdir) == data_dir


# ──────────────────────────────────────────────────────────────────
# create_backup — happy path
# ──────────────────────────────────────────────────────────────────
class TestCreateBackup:
    def test_returns_path(self, fresh_db):
        result = create_backup(reason="test")
        assert result is not None
        assert os.path.exists(result)

    def test_filename_contains_reason(self, fresh_db):
        result = create_backup(reason="market_open")
        assert "market_open" in os.path.basename(result)

    def test_filename_contains_timestamp(self, fresh_db):
        result = create_backup(reason="auto")
        basename = os.path.basename(result)
        assert basename.startswith("fam_backup_")
        assert basename.endswith("_auto.db")

    def test_backup_is_valid_sqlite(self, fresh_db):
        """The backup should be a valid SQLite database with tables."""
        result = create_backup(reason="test")
        conn = sqlite3.connect(result)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        table_names = [t[0] for t in tables]
        assert "markets" in table_names
        assert "vendors" in table_names
        assert "transactions" in table_names

    def test_backup_preserves_data(self, fresh_db):
        """Data written before backup should appear in the backup file."""
        conn = get_connection()
        conn.execute(
            "INSERT INTO markets (name, address) VALUES ('Test Market', '1 Test St')"
        )
        conn.commit()

        result = create_backup(reason="test")
        backup_conn = sqlite3.connect(result)
        row = backup_conn.execute(
            "SELECT name FROM markets WHERE name='Test Market'"
        ).fetchone()
        backup_conn.close()
        assert row is not None
        assert row[0] == "Test Market"

    def test_multiple_backups(self, fresh_db):
        """Creating multiple backups should produce unique files."""
        results = []
        for i in range(3):
            r = create_backup(reason=f"test{i}")
            results.append(r)
            # Small delay to ensure unique timestamps
            time.sleep(0.05)
        # All paths should be different
        assert len(set(results)) == 3

    def test_default_reason(self, fresh_db):
        result = create_backup()
        assert "manual" in os.path.basename(result)

    def test_different_reasons(self, fresh_db):
        for reason in ("market_open", "market_close", "auto", "manual"):
            result = create_backup(reason=reason)
            assert reason in os.path.basename(result)
            time.sleep(0.05)


# ──────────────────────────────────────────────────────────────────
# create_backup — edge cases
# ──────────────────────────────────────────────────────────────────
class TestCreateBackupEdgeCases:
    def test_missing_db_returns_none(self, fresh_db):
        """If the DB file doesn't exist, should return None gracefully."""
        close_connection()
        set_db_path(str(fresh_db / "nonexistent.db"))
        result = create_backup(reason="test")
        assert result is None

    def test_never_raises(self, fresh_db, monkeypatch):
        """create_backup should never raise, even if inner logic errors."""
        def _explode(reason):
            raise RuntimeError("boom")
        monkeypatch.setattr(backup_mod, "_create_backup_inner", _explode)
        result = create_backup(reason="test")
        assert result is None

    def test_backup_dir_created_automatically(self, fresh_db):
        """Even if backup dir doesn't exist yet, create_backup should work."""
        bdir = get_backup_dir()
        # Remove and recreate
        os.rmdir(bdir)
        assert not os.path.exists(bdir)
        result = create_backup(reason="test")
        assert result is not None
        assert os.path.exists(bdir)


# ──────────────────────────────────────────────────────────────────
# _enforce_retention
# ──────────────────────────────────────────────────────────────────
class TestEnforceRetention:
    def test_keeps_up_to_limit(self, fresh_db):
        """Should not delete anything when at or below the limit."""
        bdir = get_backup_dir()
        for i in range(BACKUP_RETENTION_COUNT):
            path = os.path.join(bdir, f"fam_backup_20260101_{i:06d}_auto.db")
            with open(path, "w") as f:
                f.write("x")
        _enforce_retention(bdir)
        remaining = [f for f in os.listdir(bdir) if f.startswith("fam_backup_")]
        assert len(remaining) == BACKUP_RETENTION_COUNT

    def test_deletes_oldest_beyond_limit(self, fresh_db):
        """Should delete oldest files when count exceeds limit."""
        bdir = get_backup_dir()
        total = BACKUP_RETENTION_COUNT + 5
        for i in range(total):
            path = os.path.join(bdir, f"fam_backup_20260101_{i:06d}_auto.db")
            with open(path, "w") as f:
                f.write("x")
        _enforce_retention(bdir)
        remaining = sorted(
            f for f in os.listdir(bdir) if f.startswith("fam_backup_")
        )
        assert len(remaining) == BACKUP_RETENTION_COUNT
        # The oldest 5 (indexes 0-4) should be gone; remaining starts at 5
        assert remaining[0] == f"fam_backup_20260101_{5:06d}_auto.db"

    def test_ignores_non_backup_files(self, fresh_db):
        """Non-backup files in the directory should not be touched."""
        bdir = get_backup_dir()
        # Create some non-backup files
        other_file = os.path.join(bdir, "other_data.txt")
        with open(other_file, "w") as f:
            f.write("important")
        # Create backups over the limit
        for i in range(BACKUP_RETENTION_COUNT + 3):
            path = os.path.join(bdir, f"fam_backup_20260101_{i:06d}_auto.db")
            with open(path, "w") as f:
                f.write("x")
        _enforce_retention(bdir)
        assert os.path.exists(other_file)

    def test_empty_directory(self, fresh_db):
        """Should not error on an empty backup directory."""
        bdir = get_backup_dir()
        _enforce_retention(bdir)  # Should not raise

    def test_single_file(self, fresh_db):
        """Single backup file should be retained."""
        bdir = get_backup_dir()
        path = os.path.join(bdir, "fam_backup_20260101_000000_auto.db")
        with open(path, "w") as f:
            f.write("x")
        _enforce_retention(bdir)
        assert os.path.exists(path)


# ──────────────────────────────────────────────────────────────────
# Integration: backup + retention together
# ──────────────────────────────────────────────────────────────────
class TestBackupRetentionIntegration:
    def test_creates_and_enforces_retention(self, fresh_db):
        """Creating many backups should auto-clean old ones."""
        bdir = get_backup_dir()
        # Pre-seed with enough backups to be at the limit
        for i in range(BACKUP_RETENTION_COUNT):
            path = os.path.join(bdir, f"fam_backup_20260101_{i:06d}_auto.db")
            with open(path, "w") as f:
                f.write("x")

        # Create one more real backup
        result = create_backup(reason="test")
        assert result is not None

        remaining = [f for f in os.listdir(bdir) if f.startswith("fam_backup_")]
        assert len(remaining) == BACKUP_RETENTION_COUNT

    def test_backup_file_is_in_backup_dir(self, fresh_db):
        result = create_backup(reason="test")
        bdir = get_backup_dir()
        assert os.path.dirname(result) == bdir
