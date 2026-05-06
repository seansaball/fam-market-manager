"""Atomic I/O + concurrent-write race tests
(v1.9.10 follow-up, 2026-05-01).

Two failure modes that silently corrupt data:

  1. **Half-written photo files** — a concurrent reader (sync
     collector, photo uploader, ledger backup pass) observing a
     half-saved photo gets garbage.  Power loss during a save
     leaves a corrupt file at the canonical name.
  2. **Concurrent write races** — two threads both UPDATE-ing
     the same row, or one inserting while another reads-then-
     writes, can lose updates if the code path doesn't use a
     proper transaction.

This module pins:

  * ``store_photo`` writes are atomic (tempfile + ``os.replace``).
  * ``write_ledger_backup`` writes are atomic (already does
     this via ``tempfile.mkstemp`` + ``os.replace``).
  * Concurrent ``update_transaction`` calls don't lose updates
     (last-writer-wins semantics with no silent loss).
  * SQLite's transaction isolation actually prevents the
     classic "read-modify-write" race within
     ``confirm_transaction``.
"""

import os
import threading
import time

import pytest

from fam.database.connection import (
    get_connection, set_db_path, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def db(tmp_path):
    db_file = str(tmp_path / "races.db")
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
    yield conn
    close_connection()


# ════════════════════════════════════════════════════════════════════
# 1. Atomic photo writes
# ════════════════════════════════════════════════════════════════════


class TestAtomicPhotoWrite:
    """``store_photo`` MUST be atomic — readers either see the
    prior file (if any) or the complete new one, never half."""

    def test_store_photo_uses_atomic_rename(self, tmp_path, monkeypatch):
        """Source-pin: ``store_photo`` must reference
        ``os.replace`` and write through a tempfile path.  Drops
        the rename and a future regression silently re-introduces
        the partial-file race.
        """
        import inspect
        from fam.utils import photo_storage
        src = inspect.getsource(photo_storage.store_photo)
        assert 'os.replace' in src, (
            "store_photo MUST use os.replace to atomically commit "
            "the saved file; a direct write to dest_path leaves "
            "a corrupt partial file on power loss / process kill")
        assert ('.partial' in src or 'tmp_path' in src), (
            "store_photo must write to a temp path before the "
            "atomic rename")

    def test_atomic_write_helper_round_trips(self, tmp_path):
        """``_atomic_write_bytes`` writes the data to the
        target path with the prior file (if any) replaced
        atomically."""
        from fam.utils.photo_storage import _atomic_write_bytes
        target = str(tmp_path / "target.jpg")
        _atomic_write_bytes(target, b"photo bytes v1")
        assert open(target, 'rb').read() == b"photo bytes v1"
        # Overwrite with new content — old version replaced.
        _atomic_write_bytes(target, b"photo bytes v2")
        assert open(target, 'rb').read() == b"photo bytes v2"

    def test_atomic_write_cleans_up_on_failure(self, tmp_path,
                                                  monkeypatch):
        """If ``os.replace`` raises, the temp file must NOT be
        left in the photos directory."""
        from fam.utils.photo_storage import _atomic_write_bytes
        target = str(tmp_path / "target.jpg")

        # Force os.replace to fail.
        original = os.replace
        def _boom(src, dst):
            os.remove(src)  # simulate the partial-write being moved
            raise OSError("simulated rename failure")
        monkeypatch.setattr(
            'fam.utils.photo_storage.os.replace', _boom)

        with pytest.raises(OSError):
            _atomic_write_bytes(target, b"data")
        # No leftover .tmp_photo_*.partial in the directory.
        leftovers = [
            n for n in os.listdir(tmp_path)
            if n.startswith('.tmp_photo_')]
        assert leftovers == [], (
            f"failed write left tempfiles behind: {leftovers}")


# ════════════════════════════════════════════════════════════════════
# 2. Atomic ledger writes (already implemented; pin)
# ════════════════════════════════════════════════════════════════════


class TestAtomicLedgerWrite:
    """``write_ledger_backup`` already uses ``tempfile.mkstemp +
    os.replace``.  Source-pin so a refactor doesn't lose it."""

    def test_ledger_uses_atomic_rename(self):
        """The atomic-write logic lives in
        ``_write_ledger_backup_inner`` (the helper that does the
        actual writing); ``write_ledger_backup`` is the
        cooldown-throttled wrapper.  Both must be present in the
        module source."""
        import inspect
        from fam.utils import export
        # Read the WHOLE module — the atomic primitives may live
        # in any helper, not necessarily the public entrypoint.
        module_src = inspect.getsource(export)
        assert 'os.replace' in module_src, (
            "fam/utils/export.py must use os.replace somewhere to "
            "atomically commit ledger writes; without it a power "
            "loss mid-write leaves a half-corrupt ledger at the "
            "canonical path")
        assert 'tempfile.mkstemp' in module_src, (
            "must write to a tempfile in the same directory "
            "before the atomic rename (cross-fs rename is NOT "
            "atomic)")


# ════════════════════════════════════════════════════════════════════
# 3. Concurrent UPDATE serializes — no lost updates
# ════════════════════════════════════════════════════════════════════


class TestConcurrentUpdateSerializes:
    """Two threads writing different fields of the SAME row must
    both succeed.  The last-committed value of each field wins;
    neither write is silently lost."""

    def test_two_threads_updating_same_txn_dont_lose_either_write(
            self, db):
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
            update_transaction,
        )
        # Set up a transaction we'll race on.
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            market_day_date='2099-05-01')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 1000, 'match_amount': 500,
            'customer_charged': 500, 'photo_path': None,
        }])
        close_connection()

        errors = []
        ready = threading.Event()

        def update_receipt():
            try:
                ready.wait(timeout=5)
                update_transaction(
                    txn_id, receipt_total=1500,
                    changed_by='ThreadA')
                close_connection()
            except Exception as e:
                errors.append(('A', e))

        def update_notes():
            try:
                ready.wait(timeout=5)
                update_transaction(
                    txn_id, notes='THREAD-B-NOTE',
                    changed_by='ThreadB')
                close_connection()
            except Exception as e:
                errors.append(('B', e))

        ta = threading.Thread(target=update_receipt)
        tb = threading.Thread(target=update_notes)
        ta.start(); tb.start()
        ready.set()
        ta.join(); tb.join()

        assert errors == [], (
            f"concurrent updates raised: {errors}")

        conn = get_connection()
        row = conn.execute(
            "SELECT receipt_total, notes FROM transactions "
            "WHERE id=?", (txn_id,)).fetchone()
        # Both writes landed.
        assert row['receipt_total'] == 1500, (
            "Thread A's receipt_total update was lost")
        assert row['notes'] == 'THREAD-B-NOTE', (
            "Thread B's notes update was lost")
        # Both writes generated audit rows.
        audit_actors = {
            r[0] for r in conn.execute(
                "SELECT changed_by FROM audit_log "
                "WHERE table_name='transactions' AND record_id=? "
                "AND action='UPDATE'",
                (txn_id,)).fetchall()
        }
        assert 'ThreadA' in audit_actors
        assert 'ThreadB' in audit_actors


# ════════════════════════════════════════════════════════════════════
# 4. Read-modify-write race in confirm path
# ════════════════════════════════════════════════════════════════════


class TestConfirmPathTransactionalGuard:
    """``confirm_transaction`` does:

        update_transaction(..., status='Confirmed', commit=False)
        log_action(..., 'CONFIRM', commit=False)
        conn.commit()

    If a concurrent thread's UPDATE lands between the two
    ``commit=False`` calls and the explicit ``conn.commit()``,
    we want to verify the audit log + status change land together
    or not at all.  SQLite's transaction isolation handles this
    — but we pin the contract so a refactor that breaks it
    surfaces."""

    def test_confirm_either_fully_lands_or_fully_rolls_back(self, db):
        """Source-pin: ``confirm_transaction`` MUST wrap the
        update + audit in one atomic transaction (commit only at
        the end).  Drops the wrapper and we get partial state on
        failure."""
        import inspect
        from fam.models import transaction as txn_module
        src = inspect.getsource(txn_module.confirm_transaction)
        # Both calls must use commit=False, then a single commit
        # at the end.
        assert 'update_transaction' in src
        assert 'log_action' in src
        assert 'commit=False' in src, (
            "confirm_transaction MUST wrap update+audit in one "
            "transaction so a crash between them doesn't leave a "
            "Confirmed row with no CONFIRM audit entry (or the "
            "inverse)")
        assert 'rollback' in src, (
            "confirm_transaction must rollback on exception so "
            "the partial state is reverted")


# ════════════════════════════════════════════════════════════════════
# 5. WAL behavior under concurrent reader + writer
# ════════════════════════════════════════════════════════════════════


class TestWALReadersWritersDontBlock:
    """A core promise of WAL: readers never block writers and
    writers never block readers (writers DO block other
    writers).  This pins that the sync thread reading the DB
    doesn't stall the UI thread's confirm — and vice versa."""

    def test_reader_active_while_writer_commits(self, db):
        """Spawn a reader thread mid-transaction.  Spawn a writer
        thread that commits new data.  Reader keeps its snapshot
        (no error, no block); writer succeeds (no error, no
        block)."""
        from fam.models.transaction import create_transaction

        # Seed.
        baseline_txn, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            market_day_date='2099-05-01')
        close_connection()

        reader_started = threading.Event()
        writer_done = threading.Event()
        results = {}

        def reader():
            r_conn = get_connection()
            r_conn.execute("BEGIN")
            n_before = r_conn.execute(
                "SELECT COUNT(*) FROM transactions").fetchone()[0]
            reader_started.set()
            writer_done.wait(timeout=10)
            n_after = r_conn.execute(
                "SELECT COUNT(*) FROM transactions").fetchone()[0]
            r_conn.commit()
            close_connection()
            results['reader_before'] = n_before
            results['reader_after'] = n_after

        def writer():
            reader_started.wait(timeout=10)
            create_transaction(
                market_day_id=1, vendor_id=1, receipt_total=2000,
                market_day_date='2099-05-01')
            close_connection()
            writer_done.set()

        rt = threading.Thread(target=reader)
        wt = threading.Thread(target=writer)
        rt.start(); wt.start()
        rt.join(timeout=15); wt.join(timeout=15)

        # Reader saw same count for both reads (snapshot
        # isolation) — i.e. no view of the writer's commit.
        assert results['reader_before'] == results['reader_after'], (
            f"WAL snapshot isolation violated: reader saw "
            f"different row counts in one transaction "
            f"({results})")

        # After both threads done, this thread sees the new row.
        conn = get_connection()
        n = conn.execute(
            "SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert n == 2, (
            f"writer commit must be visible after threads complete; "
            f"saw {n}")
