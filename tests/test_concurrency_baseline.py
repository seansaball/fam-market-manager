"""Concurrency & process-safety baseline + hardening tests
(v1.9.10 follow-up, 2026-05-01).

Pins the current concurrency posture so a future change that
silently downgrades safety (e.g. someone disabling WAL, lowering
busy_timeout, switching off FK enforcement) breaks loud here.

Plus adds tests that EXPOSE the assumptions we rely on, so we
have a clear record of what's guaranteed and what's not:

  * WAL journal mode is enabled on every connection
  * Foreign-key enforcement is ON on every connection
  * Busy-timeout absorbs brief lock contention instead of raising
  * Thread-local connections — each thread gets its own
  * Cross-thread visibility under WAL: writes commit-then-visible
    semantics
  * Concurrent writes from two threads serialize correctly (the
    DB never silently loses an UPDATE)
  * Atomic file writes (photos, ledger) — no partial-file
    corruption visible to readers

These are PURELY ADDITIVE tests.  No production code changes for
the baseline pin.  Hardening that grows out of these (single-
instance lock, etc.) lives in companion files.
"""

import os
import sqlite3
import threading
import time

import pytest

from fam.database.connection import (
    get_connection, set_db_path, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def db(tmp_path):
    db_file = str(tmp_path / "concurrency.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield db_file
    close_connection()


# ════════════════════════════════════════════════════════════════════
# 1. PRAGMA pins — WAL, foreign keys, busy_timeout
# ════════════════════════════════════════════════════════════════════


class TestConnectionPragmas:
    """Every new thread-local connection MUST come up with the
    safety PRAGMAs set.  A regression that silently disables WAL
    (e.g. on a non-writable directory, falling back to
    journal_mode=DELETE) must surface here."""

    def test_journal_mode_is_wal(self, db):
        conn = get_connection()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == 'wal', (
            f"WAL mode must be active for crash safety + readers-"
            f"don't-block-writers semantics; got {mode!r}.  Check "
            f"that the DB directory is writable and WAL files can "
            f"be created.")

    def test_foreign_keys_are_on(self, db):
        conn = get_connection()
        on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert on == 1, (
            "Foreign keys must be enforced; without this the v25 "
            "migration's vendor_payment_methods FK validation is "
            "moot")

    def test_busy_timeout_is_5_seconds(self, db):
        conn = get_connection()
        ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert ms >= 5000, (
            f"busy_timeout must be ≥5s so brief lock contention "
            f"(sync thread reading while UI thread commits) doesn't "
            f"raise 'database is locked'; got {ms}ms")

    def test_pragmas_per_new_thread(self, db):
        """Each thread gets its OWN connection — and each must
        come up with the same safety PRAGMAs."""
        results = {}

        def _check(label):
            conn = get_connection()
            results[label] = {
                'mode': conn.execute("PRAGMA journal_mode").fetchone()[0],
                'fk': conn.execute("PRAGMA foreign_keys").fetchone()[0],
                'bt': conn.execute("PRAGMA busy_timeout").fetchone()[0],
            }
            close_connection()

        ts = [threading.Thread(target=_check, args=(f't{i}',))
              for i in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        for label, vals in results.items():
            assert vals['mode'].lower() == 'wal', (
                f"thread {label} did not get WAL: {vals}")
            assert vals['fk'] == 1
            assert vals['bt'] >= 5000


# ════════════════════════════════════════════════════════════════════
# 2. Cross-thread visibility under WAL
# ════════════════════════════════════════════════════════════════════


class TestCrossThreadVisibility:
    """Under WAL, a writer thread's COMMIT must be visible to a
    reader thread on its NEXT transaction.  This is the contract
    the sync thread relies on: when the UI thread confirms a txn,
    the sync thread (different connection) must see the new
    row."""

    def test_writer_commit_visible_to_new_reader_txn(self, db):
        # Writer thread commits.
        def writer():
            conn = get_connection()
            conn.execute(
                "INSERT INTO markets (name, daily_match_limit, "
                "match_limit_active) VALUES ('X', 100, 0)")
            conn.commit()
            close_connection()

        wt = threading.Thread(target=writer)
        wt.start()
        wt.join()

        # Reader on this thread (different connection) sees it
        # because we open a fresh transaction.
        conn = get_connection()
        row = conn.execute(
            "SELECT name FROM markets WHERE name='X'").fetchone()
        assert row is not None, (
            "writer commit must be visible to other-thread reader "
            "on a fresh read")

    def test_reader_sees_consistent_snapshot_during_writer(self, db):
        """A reader started at time T1 must NOT see a writer's
        in-flight changes that commit at T2.  WAL gives readers a
        snapshot view as of when their transaction began."""
        conn = get_connection()
        conn.execute(
            "INSERT INTO markets (id, name, daily_match_limit, "
            "match_limit_active) VALUES (1, 'A', 100, 1)")
        conn.commit()
        close_connection()

        # Reader thread takes a snapshot.
        snapshot = {}
        reader_ready = threading.Event()
        writer_done = threading.Event()

        def reader():
            r_conn = get_connection()
            r_conn.execute("BEGIN")
            snapshot['before'] = r_conn.execute(
                "SELECT match_limit_active FROM markets "
                "WHERE id=1").fetchone()[0]
            reader_ready.set()
            writer_done.wait(timeout=5)
            # Same transaction — reader should see ITS snapshot
            # value, not the writer's commit.
            snapshot['during'] = r_conn.execute(
                "SELECT match_limit_active FROM markets "
                "WHERE id=1").fetchone()[0]
            r_conn.commit()
            close_connection()

        rt = threading.Thread(target=reader)
        rt.start()
        reader_ready.wait(timeout=5)

        # Writer commits a change.
        w_conn = sqlite3.connect(db)
        w_conn.execute("PRAGMA journal_mode=WAL")
        w_conn.execute(
            "UPDATE markets SET match_limit_active=0 WHERE id=1")
        w_conn.commit()
        w_conn.close()
        writer_done.set()
        rt.join()

        # Snapshot isolation: reader saw the same value across
        # both reads in its transaction.
        assert snapshot['before'] == snapshot['during'] == 1, (
            f"WAL snapshot isolation violated: reader saw "
            f"{snapshot} across one transaction")


# ════════════════════════════════════════════════════════════════════
# 3. Concurrent writes — busy_timeout serializes correctly
# ════════════════════════════════════════════════════════════════════


class TestConcurrentWrites:
    """Two threads each writing to the DB must both succeed —
    SQLite serializes writes, busy_timeout absorbs the contention
    so neither side raises 'database is locked'."""

    def test_two_writers_both_commit(self, db):
        """100 inserts split across two threads.  Final row count
        must be 100 — none lost to lock contention."""
        N = 100

        # Set up a target table.
        conn = get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS scratch (n INTEGER)")
        conn.commit()
        close_connection()

        errors = []

        def writer(start, count):
            try:
                c = get_connection()
                for i in range(count):
                    c.execute("INSERT INTO scratch (n) VALUES (?)",
                              (start + i,))
                    c.commit()
                close_connection()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=writer, args=(0, N // 2))
        t2 = threading.Thread(target=writer, args=(N // 2, N // 2))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert errors == [], (
            f"concurrent writes raised: {errors}")
        c = get_connection()
        count = c.execute("SELECT COUNT(*) FROM scratch").fetchone()[0]
        assert count == N, (
            f"expected {N} rows after concurrent writes, got {count}; "
            f"silent loss is the worst failure mode")


# ════════════════════════════════════════════════════════════════════
# 4. Single-flight sync — UI's existing lock works
# ════════════════════════════════════════════════════════════════════


class TestSingleFlightSyncLock:
    """``MainWindow._trigger_sync`` short-circuits when a sync
    thread is already running.  This is enforced by the
    ``isRunning()`` check.  Source-level pin guards against
    refactor that drops the check."""

    def test_trigger_sync_short_circuits_when_running(self):
        import inspect
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._trigger_sync)
        assert ('isRunning()' in src
                and 'Sync already in progress' in src
                and 'return' in src), (
            "MainWindow._trigger_sync MUST short-circuit when a "
            "sync thread is already running; without this, rapid "
            "mutations could spawn parallel sync workers")


# ════════════════════════════════════════════════════════════════════
# 5. WAL checkpoint behavior — ensures no infinite WAL growth
# ════════════════════════════════════════════════════════════════════


class TestWALCheckpoint:
    """Long-running apps with no checkpoint can grow the
    ``-wal`` file unbounded.  SQLite auto-checkpoints every
    1000 pages by default; we just verify that's still in effect
    (a future opt-out via ``wal_autocheckpoint=0`` would silently
    grow the WAL forever)."""

    def test_wal_autocheckpoint_is_active(self, db):
        conn = get_connection()
        # Default in SQLite is 1000 pages.  Anything > 0 is fine
        # — we just want to confirm the operator hasn't disabled
        # it.
        n = conn.execute(
            "PRAGMA wal_autocheckpoint").fetchone()[0]
        assert n > 0, (
            f"wal_autocheckpoint must be active (>0) so the "
            f"-wal file doesn't grow unbounded; got {n}")


# ════════════════════════════════════════════════════════════════════
# 6. DB file safety: handles missing / read-only gracefully
# ════════════════════════════════════════════════════════════════════


class TestDBFileSafety:
    """Edge: launching against a corrupt / unwritable / missing
    DB file must produce a clear error, not silent data loss."""

    def test_clear_error_on_unwritable_dir(self, tmp_path):
        """Pointing at a path inside a non-existent directory
        raises a clear OSError on first connect, not a silent
        in-memory DB."""
        bad = str(tmp_path / "nonexistent_dir" / "fam.db")
        close_connection()
        set_db_path(bad)
        with pytest.raises((sqlite3.OperationalError, OSError)):
            conn = get_connection()
            conn.execute("CREATE TABLE x (a INTEGER)")
            conn.commit()
        close_connection()

    def test_corrupt_db_file_raises_clearly(self, tmp_path):
        """A non-SQLite file at the DB path raises on first real
        query — never silently 'works' against bad data."""
        bad = str(tmp_path / "garbage.db")
        with open(bad, 'wb') as f:
            f.write(b'NOT A SQLITE FILE')
        close_connection()
        set_db_path(bad)
        with pytest.raises(sqlite3.DatabaseError):
            conn = get_connection()
            # Schema initialization must blow up here — better than
            # an unintelligible failure 5 mutations later.
            initialize_database()
        close_connection()
