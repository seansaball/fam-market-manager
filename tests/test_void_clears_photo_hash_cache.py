"""Voiding a transaction drops the photo-hash cache row pointing
to its Drive URL (v2.0.6 fix).

User-reported (2026-05-06): uploaded a receipt image, voided the
transaction, then uploaded the SAME image to a new transaction.
The local app accepted the new entry but the image never appeared
in Google Drive.

Root cause: the ``photo_hashes`` table caches ``content_hash →
drive_url`` mappings for sync-time dedup.  When a transaction is
voided, ``_process_voided_photos`` renames its Drive file to
``VOID_<orig>`` on the next sync — but the hash cache row stays
unchanged.  The next upload of the same image content short-
circuits to the VOIDed URL (line 911 in drive.py:_upload_entries)
instead of doing a fresh upload, leaving the new transaction's
``photo_drive_url`` pointing to a renamed file the user can't
find under the expected name.

Fix: ``void_transaction`` now calls
``cleanup_orphaned_hashes_for_transaction`` which drops cache
rows for URLs unique to the voided transaction.  Photos shared
by another active transaction stay cached so dedup remains
correct for them.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_void_clears_hash.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield tmp_path
    close_connection()


def _seed_minimal(conn):
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit) "
        "VALUES (1, 'M', 10000)")
    conn.execute(
        "INSERT INTO vendors (id, name, is_active) "
        "VALUES (1, 'V', 1)")
    conn.execute(
        "INSERT INTO market_days "
        "(id, market_id, date, status, opened_by) "
        "VALUES (1, 1, '2026-05-06', 'Open', 'T')")
    conn.execute(
        "INSERT INTO payment_methods "
        "(id, name, match_percent, is_active, sort_order) "
        "VALUES (1, 'Cash', 100.0, 1, 1)")
    conn.commit()


def _create_transaction_with_photo(conn, txn_id: int,
                                   drive_url: str,
                                   content_hash: str | None = None):
    """Create a Confirmed transaction with one line item that has
    the given drive_url, and (optionally) seed the photo_hashes
    cache row for the given content_hash → drive_url."""
    conn.execute(
        "INSERT INTO transactions "
        "(id, fam_transaction_id, market_day_id, vendor_id, "
        " receipt_total, status, created_at) "
        "VALUES (?, ?, 1, 1, 2000, 'Confirmed', "
        "        '2026-05-06 12:00:00')",
        (txn_id, f'FAM-T-{txn_id}'))
    conn.execute(
        "INSERT INTO payment_line_items "
        "(transaction_id, payment_method_id, "
        " method_name_snapshot, match_percent_snapshot, "
        " method_amount, match_amount, "
        " customer_charged, photo_path, photo_drive_url, "
        " created_at) "
        "VALUES (?, 1, 'Cash', 100.0, 2000, 1000, 1000, "
        "        'photos/receipt.jpg', ?, "
        "        '2026-05-06 12:00:00')",
        (txn_id, drive_url))
    if content_hash:
        from fam.models.photo_hash import store_photo_hash
        store_photo_hash(content_hash, drive_url)
    conn.commit()


def _hash_count(conn, drive_url: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM photo_hashes WHERE drive_url = ?",
        (drive_url,)).fetchone()[0]


# ─── Cleanup happens for the unique-photo case ──────────────────


class TestVoidDropsHashWhenPhotoUniqueToVoidedTxn:
    """The user's reported scenario: one transaction owns the
    photo, gets voided, hash cache row goes away."""

    def test_hash_row_removed_after_void(self):
        from fam.models.transaction import void_transaction

        conn = get_connection()
        _seed_minimal(conn)

        url = 'https://drive.google.com/file/d/abc123/view'
        ch = 'sha256:deadbeef'
        _create_transaction_with_photo(
            conn, txn_id=100, drive_url=url, content_hash=ch)

        assert _hash_count(conn, url) == 1, (
            "fixture setup error — expected hash cache to be "
            "seeded with one row before void")

        void_transaction(100, voided_by='Tester')

        assert _hash_count(conn, url) == 0, (
            "Voiding the only transaction that referenced this "
            "Drive URL must drop the photo_hashes cache row.  "
            "Pre-fix the row stayed, and the next upload of the "
            "same image content short-circuited to the (now "
            "VOID_-prefixed) Drive file instead of doing a fresh "
            "upload.")


# ─── Cleanup is conservative when photo is shared ───────────────


class TestVoidPreservesHashWhenPhotoSharedWithActiveTxn:
    """If two transactions share the same Drive URL (because dedup
    pointed both at the same file), voiding ONE must not drop the
    hash cache — the other transaction still legitimately uses it."""

    def test_hash_row_retained_when_active_txn_still_references(
            self):
        from fam.models.transaction import void_transaction

        conn = get_connection()
        _seed_minimal(conn)

        url = 'https://drive.google.com/file/d/shared/view'
        ch = 'sha256:cafef00d'
        _create_transaction_with_photo(
            conn, txn_id=200, drive_url=url, content_hash=ch)
        _create_transaction_with_photo(
            conn, txn_id=201, drive_url=url, content_hash=None)

        assert _hash_count(conn, url) == 1

        # Void only one of the two — the other is still active
        void_transaction(200, voided_by='Tester')

        assert _hash_count(conn, url) == 1, (
            "Voiding one of two transactions that shared a Drive "
            "URL must NOT drop the cache row — the OTHER, still-"
            "active transaction legitimately references this URL "
            "and dedup of new uploads should still hit the cache.")

        # Now void the second one too — cache should clear
        void_transaction(201, voided_by='Tester')

        assert _hash_count(conn, url) == 0, (
            "After ALL transactions referencing a URL are voided, "
            "the cache row must finally clear so a fresh upload "
            "of the same content goes through.")


# ─── FMNP cross-source check ────────────────────────────────────


class TestVoidPreservesHashWhenSharedWithActiveFmnp:
    """The hash cache is shared across the receipt-photo and FMNP
    upload paths.  An ACTIVE FMNP entry referencing the same URL
    must keep the cache row alive when a transaction is voided."""

    def test_hash_retained_when_active_fmnp_references_same_url(
            self):
        from fam.models.transaction import void_transaction

        conn = get_connection()
        _seed_minimal(conn)

        url = 'https://drive.google.com/file/d/fmnp_shared/view'
        ch = 'sha256:fmnp123'
        _create_transaction_with_photo(
            conn, txn_id=300, drive_url=url, content_hash=ch)

        # Active FMNP entry referencing the same URL
        conn.execute(
            "INSERT INTO fmnp_entries "
            "(id, market_day_id, vendor_id, amount, status, "
            " entered_by, created_at, photo_drive_url) "
            "VALUES (1000, 1, 1, 500, 'Active', 'T', "
            "        '2026-05-06 12:00:00', ?)",
            (url,))
        conn.commit()

        void_transaction(300, voided_by='Tester')

        assert _hash_count(conn, url) == 1, (
            "Active FMNP entry references the same URL — the "
            "cache row must stay so FMNP dedup still works.")


# ─── Multi-photo (JSON-array) URL handling ──────────────────────


class TestVoidHandlesMultiPhotoUrls:
    """``photo_drive_url`` may be a JSON array for multi-photo line
    items.  ``cleanup_orphaned_hashes_for_transaction`` must parse
    each URL out and check each one for active references."""

    def test_each_url_in_multi_photo_array_is_checked(self):
        import json
        from fam.models.transaction import void_transaction
        from fam.models.photo_hash import store_photo_hash

        conn = get_connection()
        _seed_minimal(conn)

        url_a = 'https://drive.google.com/file/d/A/view'
        url_b = 'https://drive.google.com/file/d/B/view'
        json_arr = json.dumps([url_a, url_b])

        store_photo_hash('hash-A', url_a)
        store_photo_hash('hash-B', url_b)
        _create_transaction_with_photo(
            conn, txn_id=400, drive_url=json_arr,
            content_hash=None)

        assert _hash_count(conn, url_a) == 1
        assert _hash_count(conn, url_b) == 1

        void_transaction(400, voided_by='Tester')

        assert _hash_count(conn, url_a) == 0, (
            "Both URLs in the JSON array must be cleaned up.")
        assert _hash_count(conn, url_b) == 0


# ─── Atomic with the void itself ────────────────────────────────


class TestCleanupIsAtomicWithVoid:
    """The cleanup runs inside the same transaction as the status
    flip.  When the caller passes ``commit=False`` (bundling with
    a parent customer_order flip), the cleanup must NOT commit
    independently — otherwise a rollback on the parent flip leaves
    the cache row missing while the txn is still Confirmed."""

    def test_cleanup_does_not_commit_when_caller_holds_transaction(
            self, monkeypatch):
        from fam.models.transaction import void_transaction
        from fam.models import photo_hash as ph

        conn = get_connection()
        _seed_minimal(conn)

        url = 'https://drive.google.com/file/d/atomic/view'
        ch = 'sha256:atomic'
        _create_transaction_with_photo(
            conn, txn_id=500, drive_url=url, content_hash=ch)

        # Spy on commits — capture the calls cleanup makes.
        helper_commits = []
        original = ph.cleanup_orphaned_hashes_for_transaction

        def _spy(txn_id, commit=True):
            helper_commits.append(commit)
            return original(txn_id, commit=commit)

        monkeypatch.setattr(
            ph, 'cleanup_orphaned_hashes_for_transaction', _spy)
        # The import cache in transaction.py also needs the spy
        import fam.models.transaction as tx_mod
        # void_transaction does ``from fam.models.photo_hash import
        # cleanup_orphaned_hashes_for_transaction`` inside the
        # function body — patch the source module so the import
        # picks up the spy.
        # (already patched on ph above; the local import resolves
        # against the patched module attr at call time.)

        void_transaction(500, voided_by='Tester', commit=False)
        # Caller must commit explicitly; do that now
        conn.commit()

        assert helper_commits == [False], (
            f"void_transaction must call the cleanup helper with "
            f"commit=False so the cleanup is bundled into the "
            f"caller's atomic transaction.  Got commit calls: "
            f"{helper_commits}.")
