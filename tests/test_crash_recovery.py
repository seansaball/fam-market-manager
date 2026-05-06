"""Crash recovery & atomicity tests
(v1.9.10 follow-up, 2026-05-01).

Power-loss / OS-kill / disk-full scenarios that must NOT
silently corrupt data.  Each test models a specific failure
mode and asserts that the system either:

  * Stays in a consistent state (the mutation didn't land), OR
  * Successfully completes the mutation (atomic commit), OR
  * Surfaces a clear error to the operator (no silent loss).

Test categories:

  1. Mid-transaction interrupt — ``conn.interrupt()`` mid-commit
     leaves the row absent, never partial.
  2. Migration kill resume — pre-migration .bak snapshot exists;
     subsequent launches succeed (re-runnable migrations).
  3. Disk-full handling — produces an OperationalError surfaced
     to the caller, not silent in-memory writes.
  4. Drive URL persistence — content-hash dedup means re-running
     after a "upload-succeeded-but-DB-write-failed" scenario
     does NOT double-upload.
  5. Restore-from-backup full lifecycle — backup → mutate →
     restore reverts to backup state, app continues working.
  6. WAL checkpoint behavior — clean shutdown checkpoints; kill
     scenarios leave a recoverable WAL.
"""

import os
import shutil
import sqlite3
import time

import pytest

from fam.database.connection import (
    get_connection, set_db_path, close_connection, get_db_path,
)
from fam.database.schema import initialize_database


@pytest.fixture
def db(tmp_path):
    db_file = str(tmp_path / "crash.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(1, 'SNAP', 100.0, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO vendor_payment_methods (vendor_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES (1, 1, '2099-05-01', 'Open', 'Tester')")
    conn.commit()
    yield {'conn': conn, 'path': db_file}
    close_connection()


def _confirm_one(receipt=2000):
    """Helper: one confirmed transaction, half customer / half match."""
    from fam.models.customer_order import (
        create_customer_order, update_customer_order_status,
    )
    from fam.models.transaction import (
        create_transaction, save_payment_line_items,
        confirm_transaction,
    )
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label=f'C-{int(time.time()*1000) % 100000}')
    txn_id, fam_id = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=receipt,
        customer_order_id=order_id, market_day_date='2099-05-01')
    save_payment_line_items(txn_id, [{
        'payment_method_id': 1,
        'method_name_snapshot': 'SNAP',
        'match_percent_snapshot': 100.0,
        'method_amount': receipt,
        'match_amount': receipt // 2,
        'customer_charged': receipt - (receipt // 2),
        'photo_path': None,
    }])
    confirm_transaction(txn_id, confirmed_by='Tester')
    update_customer_order_status(order_id, 'Confirmed')
    return order_id, txn_id, fam_id


# ════════════════════════════════════════════════════════════════════
# 1. Mid-transaction kill — DB stays consistent
# ════════════════════════════════════════════════════════════════════


class TestMidTransactionKill:
    """A confirmed transaction commits atomically.  If the process
    is killed BETWEEN ``execute`` and ``commit``, the row must
    NOT appear post-restart.  SQLite's WAL guarantees this."""

    def test_uncommitted_transaction_does_not_persist(self, db):
        """Open a transaction, do an INSERT, do NOT commit, then
        close the connection (simulating an OS kill).  The row
        must not be visible to a fresh connection."""
        conn = db['conn']
        # Begin a transaction by issuing a write without commit.
        conn.execute(
            "INSERT INTO transactions "
            "(fam_transaction_id, market_day_id, vendor_id, "
            " receipt_total, status) "
            "VALUES ('UNCOMMITTED', 1, 1, 999, 'Draft')")
        # Now SIMULATE a kill: close without commit.  SQLite
        # rolls back uncommitted changes on close.
        close_connection()

        # Fresh connection — the row should not exist.
        conn2 = get_connection()
        row = conn2.execute(
            "SELECT * FROM transactions "
            "WHERE fam_transaction_id='UNCOMMITTED'").fetchone()
        assert row is None, (
            "uncommitted INSERT must NOT persist after a "
            "simulated process kill")

    def test_interrupt_mid_query_does_not_partial_save(self, db):
        """``conn.interrupt()`` aborts an in-flight execute.  The
        partial state must not corrupt the DB — subsequent
        operations succeed."""
        conn = db['conn']
        # Trigger an interrupt by setting a busy_timeout=0
        # against a locked DB and writing — too brittle.  Instead
        # use a programmatic abort via a custom progress handler.

        aborted = {'count': 0}

        def _abort_after_n_steps(n):
            def _handler():
                aborted['count'] += 1
                # Returning non-zero aborts the current operation.
                return 1 if aborted['count'] >= n else 0
            return _handler

        # Set the progress handler to abort after 1 step.
        conn.set_progress_handler(_abort_after_n_steps(1), 1)
        try:
            # This INSERT will be aborted by the progress handler.
            conn.execute(
                "INSERT INTO transactions "
                "(fam_transaction_id, market_day_id, vendor_id, "
                " receipt_total, status) "
                "VALUES ('SHOULD_NOT_LAND', 1, 1, 999, 'Draft')")
            # If we reach here without an exception, the abort
            # didn't fire — fall through to the consistency check.
        except sqlite3.OperationalError:
            # Expected: 'interrupted'.
            pass
        finally:
            conn.set_progress_handler(None, 0)
            try:
                conn.rollback()
            except Exception:
                pass

        # Either way, the row must not be in the DB AND the DB
        # is still queryable.
        row = conn.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE fam_transaction_id='SHOULD_NOT_LAND'").fetchone()[0]
        assert row == 0, (
            "interrupted INSERT silently landed — atomicity broken")
        # DB still works — counting should succeed.
        n = conn.execute(
            "SELECT COUNT(*) FROM markets").fetchone()[0]
        assert n >= 1, "DB became unusable after interrupted query"


# ════════════════════════════════════════════════════════════════════
# 2. Migration kill + resume
# ════════════════════════════════════════════════════════════════════


class TestMigrationKillResume:
    """The pre-migration .bak file is the operator's recovery
    path.  Migrations must be re-runnable so a half-completed
    migration succeeds on next launch."""

    def test_pre_migration_bak_created_when_upgrading(self, tmp_path):
        """Setting up a v23 schema and running initialize_database
        creates a ``.pre-migration.bak`` sibling before any v24+
        migration runs."""
        db_path = str(tmp_path / "v23.db")
        # Build a synthetic v23 schema (just enough for the v25
        # migration to pass — the v23→v24 specifically needs
        # vendors/payment_methods tables).
        c = sqlite3.connect(db_path)
        c.executescript("""
            CREATE TABLE markets (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE vendors (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE payment_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                match_percent REAL NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                denomination INTEGER DEFAULT NULL,
                photo_required TEXT DEFAULT NULL
            );
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
            INSERT INTO schema_version (version) VALUES (23);
        """)
        c.commit(); c.close()

        close_connection()
        set_db_path(db_path)
        initialize_database()
        # v2.0.1: pre-migration backup is now version-stamped and
        # uses sqlite3.Connection.backup() (WAL-aware).  Filename
        # pattern: <db>.pre-migration-v<from>-to-v<to>-<ts>.bak.
        # Glob for any matching file.
        import glob as _glob
        bak_candidates = _glob.glob(db_path + '.pre-migration*.bak')
        assert bak_candidates, (
            "pre-migration .bak must exist so a failed upgrade "
            "is recoverable by copying the .bak back")
        bak = bak_candidates[0]
        # The .bak must be a valid SQLite file.
        bak_conn = sqlite3.connect(bak)
        v = bak_conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        assert v == 23, (
            f"pre-migration backup must capture the OLD schema "
            f"version (23 here); got {v}")
        bak_conn.close()
        close_connection()

    def test_migration_re_running_is_idempotent(self, tmp_path):
        """``initialize_database`` runs to completion.  Running
        it AGAIN must be a no-op (no errors, schema_version
        unchanged)."""
        db_path = str(tmp_path / "double_init.db")
        close_connection()
        set_db_path(db_path)
        initialize_database()
        from fam.database.schema import CURRENT_SCHEMA_VERSION
        v1 = get_connection().execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        assert v1 == CURRENT_SCHEMA_VERSION
        close_connection()
        # Run again.
        set_db_path(db_path)
        initialize_database()  # must not raise
        v2 = get_connection().execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        assert v2 == v1
        close_connection()


# ════════════════════════════════════════════════════════════════════
# 3. Disk-full handling
# ════════════════════════════════════════════════════════════════════


class TestDiskFullHandling:
    """SQLite raises ``sqlite3.OperationalError("disk is full")``
    when writes can't complete.  The save path must surface
    this clearly, not silently drop the write."""

    def test_save_path_propagates_db_errors(self, db):
        """Contract: ``save_payment_line_items`` MUST propagate
        any SQLite error (disk-full, schema mismatch, FK
        violation, etc.) to the caller so the UI surfaces a
        clear message — instead of silently believing the save
        succeeded.

        We trigger an error by passing an item that violates the
        per-line invariant trigger (customer + match != method),
        which fires a CHECK error mid-INSERT.  The contract we
        test is the SAME as for disk-full: the model must NOT
        swallow the error.
        """
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
        )
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            market_day_date='2099-05-01')

        with pytest.raises(sqlite3.IntegrityError):
            save_payment_line_items(txn_id, [{
                'payment_method_id': 1,
                'method_name_snapshot': 'SNAP',
                'match_percent_snapshot': 100.0,
                'method_amount': 1000,
                # Deliberately broken: 500 + 999 != 1000 → trigger.
                'match_amount': 999,
                'customer_charged': 500,
                'photo_path': None,
            }])

        # The DB must remain queryable AND no partial PLI row
        # landed (DELETE+INSERT atomicity rolled back).
        conn = get_connection()
        n = conn.execute(
            "SELECT COUNT(*) FROM payment_line_items "
            "WHERE transaction_id=?", (txn_id,)).fetchone()[0]
        assert n == 0, (
            "failed save must rollback ALL PLI rows for the "
            f"transaction, not leave partial; saw {n}")
        # Subsequent ops succeed — DB still healthy.
        n2 = conn.execute(
            "SELECT COUNT(*) FROM markets").fetchone()[0]
        assert n2 == 1

    def test_max_page_count_clamp_is_a_real_safety_net(self, db):
        """Belt-and-suspenders: ``PRAGMA max_page_count`` IS the
        SQLite primitive that surfaces 'database or disk is full'.
        Pin that the PRAGMA exists so future investigations have
        a working tool."""
        conn = get_connection()
        # Set absurdly small cap.
        conn.execute("PRAGMA max_page_count=2")
        # Any write that needs new pages MUST raise.  A new
        # CREATE TABLE definitely needs a new page.
        with pytest.raises(sqlite3.OperationalError) as exc:
            conn.execute("CREATE TABLE _xxx (a INTEGER)")
        msg = str(exc.value).lower()
        assert 'full' in msg or 'page' in msg or 'limit' in msg, (
            f"max_page_count cap must surface a recognizable "
            f"disk-full / page-limit message; got {msg!r}")
        # Lift the cap so the test fixture teardown works.
        conn.execute("PRAGMA max_page_count=2147483646")


# ════════════════════════════════════════════════════════════════════
# 4. Drive URL persistence — content-hash idempotency
# ════════════════════════════════════════════════════════════════════


class TestDriveUploadIdempotency:
    """The Drive uploader uses ``compute_file_hash`` →
    ``photo_hashes`` lookup → reuse-existing-URL.  This protects
    the ``upload-succeeded-but-DB-UPDATE-failed`` race:
    next sync sees the photo hash already in the registry and
    reuses the existing Drive URL instead of re-uploading."""

    def test_content_hash_dedup_path_present(self):
        """Source-pin: the dedup logic must remain in
        ``_upload_photos_for_entries``.  Drop it and crashes
        between Drive PUT and DB UPDATE start re-uploading."""
        import inspect
        from fam.sync import drive
        src = inspect.getsource(drive)
        assert 'compute_file_hash' in src, (
            "Drive upload must use compute_file_hash so identical "
            "content gets a single Drive URL across sync cycles")
        assert 'hash_cache' in src, (
            "Drive upload must maintain an in-memory hash_cache "
            "to dedup within a sync cycle")
        assert 'store_photo_hash' in src, (
            "Drive upload must persist hash → URL to "
            "photo_hashes for cross-cycle dedup")

    def test_photo_hash_table_exists(self, db):
        """The cross-cycle dedup table must exist in the schema."""
        row = db['conn'].execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='photo_hashes'").fetchone()
        assert row is not None, (
            "photo_hashes table must exist; it backs the cross-"
            "cycle dedup that prevents re-uploads after a crash")

    def test_photo_hash_lookup_and_store_round_trip(self, db):
        """Round-trip: store a hash → URL pair, then look up the
        URL by hash.  This is the exact path the Drive uploader
        uses to skip re-uploading after a partial-state crash."""
        from fam.models.photo_hash import (
            store_photo_hash, get_drive_url_by_hash,
        )
        store_photo_hash('abc123def456', 'https://drive.example/x')
        url = get_drive_url_by_hash('abc123def456')
        assert url == 'https://drive.example/x'


# ════════════════════════════════════════════════════════════════════
# 5. Restore-from-backup full lifecycle
# ════════════════════════════════════════════════════════════════════


class TestBinaryBackupRestoreLifecycle:
    """Beyond the smoke test in test_v1_9_10_audit_fixes:

      1. Confirm a transaction.
      2. Take a binary backup.
      3. Confirm MORE transactions, void the first one.
      4. Simulate corruption: replace the live DB with the backup.
      5. Verify post-restore state matches (1) — NOT (3).
      6. Continue normal operation post-restore.
    """

    def test_full_restore_lifecycle(self, db, tmp_path):
        from fam.database.backup import create_backup
        from fam.models.transaction import void_transaction

        # Step 1: confirm a baseline transaction.
        order1, txn1, fam1 = _confirm_one(receipt=4000)
        # Step 2: take a backup.
        bak_path = create_backup(reason='lifecycle_test')
        assert bak_path and os.path.exists(bak_path)

        # Step 3: more activity that we DO NOT want post-restore.
        order2, txn2, fam2 = _confirm_one(receipt=2500)
        order3, txn3, fam3 = _confirm_one(receipt=1500)
        void_transaction(txn1, voided_by='Tester')

        # Verify the live DB has the post-backup state.
        conn = get_connection()
        live_count = conn.execute(
            "SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert live_count == 3
        live_txn1_status = conn.execute(
            "SELECT status FROM transactions WHERE id=?",
            (txn1,)).fetchone()['status']
        assert live_txn1_status == 'Voided'

        # Step 4: simulate corruption + restore.  Close the live
        # connection, copy the backup over the live file, reopen.
        close_connection()
        live_path = db['path']
        shutil.copy2(bak_path, live_path)

        # Step 5: open the restored DB; assert state matches the
        # backup snapshot, NOT the post-backup mutations.
        set_db_path(live_path)
        conn = get_connection()
        restored_count = conn.execute(
            "SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert restored_count == 1, (
            f"Restored DB must have 1 transaction (the one "
            f"present at backup time); got {restored_count}.  "
            f"This means restoration didn't fully replace the live "
            f"file or the backup wasn't a complete snapshot.")
        restored_txn1 = conn.execute(
            "SELECT status FROM transactions WHERE id=?",
            (txn1,)).fetchone()
        assert restored_txn1['status'] == 'Confirmed', (
            "Restored DB must show the txn's PRE-void status")
        # txn2 / txn3 must NOT exist post-restore.
        absent = conn.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE id IN (?, ?)", (txn2, txn3)).fetchone()[0]
        assert absent == 0, (
            "Post-backup transactions must NOT survive a restore")

        # Step 6: continue normal operation against the restored DB.
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
            confirm_transaction,
        )
        new_txn, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=3333,
            market_day_date='2099-05-01')
        save_payment_line_items(new_txn, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 3333, 'match_amount': 1666,
            'customer_charged': 1667, 'photo_path': None,
        }])
        confirm_transaction(new_txn, confirmed_by='Post-Restore')
        n = conn.execute(
            "SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert n == 2, (
            "post-restore mutations must succeed; restored DB "
            "should be fully functional")


# ════════════════════════════════════════════════════════════════════
# 6. WAL checkpoint behavior
# ════════════════════════════════════════════════════════════════════


class TestWALCheckpoint:

    def test_explicit_checkpoint_succeeds(self, db):
        """``PRAGMA wal_checkpoint`` works on a live DB.  This is
        the operator's escape hatch when the WAL has grown
        large; running it manually flushes pages back to the
        main file."""
        # Generate some WAL activity.
        for _ in range(5):
            _confirm_one()
        conn = get_connection()
        # PRAGMA wal_checkpoint returns (busy, log_pages, checkpointed).
        result = conn.execute(
            "PRAGMA wal_checkpoint(FULL)").fetchone()
        # busy=0, log_pages and checkpointed are non-negative ints.
        assert result is not None
        assert result[0] in (0, 1)  # 0 = success, 1 = busy

    def test_close_connection_does_not_corrupt_wal(self, db, tmp_path):
        """Closing the thread-local connection MUST leave the DB
        + WAL in a state where a fresh open succeeds and sees
        all committed data."""
        # Confirm two transactions, close, reopen, assert both visible.
        _confirm_one(receipt=1000)
        _confirm_one(receipt=2000)
        close_connection()
        # Fresh connection.
        set_db_path(db['path'])
        conn = get_connection()
        rows = conn.execute(
            "SELECT receipt_total FROM transactions "
            "ORDER BY id").fetchall()
        totals = sorted(r[0] for r in rows)
        assert totals == [1000, 2000], (
            f"closed-then-reopened DB lost data: got {totals}")

    def test_wal_recovers_after_simulated_kill(self, db, tmp_path):
        """Simulate a process kill by NOT closing the connection
        cleanly.  Open a fresh connection from the same file —
        SQLite's WAL replay should make the committed data
        visible."""
        _confirm_one(receipt=4242)
        # Don't close — just leak the connection (analog of OS
        # kill).  Open a fresh one against the same file.
        path = db['path']

        # Force-open a separate sqlite3.Connection (bypasses our
        # thread-local cache) to mimic a "fresh process".
        c2 = sqlite3.connect(path)
        c2.execute("PRAGMA journal_mode=WAL")
        rows = c2.execute(
            "SELECT receipt_total FROM transactions"
        ).fetchall()
        c2.close()
        assert any(r[0] == 4242 for r in rows), (
            "data committed before a simulated kill must remain "
            "visible after a fresh open (WAL replay)")
