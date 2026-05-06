"""Editing or deleting an FMNP entry clears its photo dedup state
so the next sync correctly re-uploads (v2.0.6 fix).

User-reported (2026-05-06): edited an FMNP entry, cleared the
attached image, reattached the same image, clicked "Yes" on the
duplicate-warning popup, saved.  The new image never appeared in
Google Drive.

Two compounding bugs:

  1. ``update_fmnp_entry`` only ever wrote ``photo_path``; it left
     ``photo_drive_url`` untouched.  ``get_pending_photo_uploads``
     queues entries where ``photo_drive_url`` has FEWER URLs than
     ``photo_path``.  On a single-photo edit (paths=1, urls=1) the
     count matched, so the entry was never queued for upload —
     the upload pipeline simply never ran.

  2. Even if the pipeline had run, the hash-cache short-circuit
     in ``drive.py::_upload_entries`` would have skipped the
     upload because the same content hash was still mapped to
     the old Drive URL.

Fix shape:

  * ``update_fmnp_entry`` now clears ``photo_drive_url`` on any
    ``photo_path`` change, so the sync re-evaluates uploads.
  * Before the clear, it runs ``cleanup_orphaned_hashes_for_fmnp``
    so the dedup cache loses any URL that's no longer referenced
    by an active record.  Mirrors the ``void_transaction``
    pattern for transactions.
  * ``delete_fmnp_entry`` runs the same cleanup so the next
    ``_process_voided_photos`` cycle's VOID_-rename of this
    entry's Drive file doesn't strand future uploads.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_fmnp_edit_clears.db")
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
    conn.commit()


def _create_fmnp(conn, entry_id: int, photo_path: str | None,
                 photo_drive_url: str | None):
    conn.execute(
        "INSERT INTO fmnp_entries "
        "(id, market_day_id, vendor_id, amount, status, "
        " entered_by, created_at, photo_path, photo_drive_url) "
        "VALUES (?, 1, 1, 500, 'Active', 'T', "
        "        '2026-05-06 12:00:00', ?, ?)",
        (entry_id, photo_path, photo_drive_url))
    conn.commit()


def _drive_url_of(conn, entry_id: int) -> str | None:
    row = conn.execute(
        "SELECT photo_drive_url FROM fmnp_entries WHERE id = ?",
        (entry_id,)).fetchone()
    return row['photo_drive_url'] if row else None


def _hash_count(conn, drive_url: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM photo_hashes WHERE drive_url = ?",
        (drive_url,)).fetchone()[0]


# ─── update_fmnp_entry: photo_path change clears Drive URL ──────


class TestUpdateClearsDriveUrlOnPhotoPathChange:
    """The core fix: ``update_fmnp_entry`` now clears
    ``photo_drive_url`` when the photo path changes."""

    def test_photo_path_change_clears_photo_drive_url(self):
        from fam.models.fmnp import update_fmnp_entry

        conn = get_connection()
        _seed_minimal(conn)
        old_url = 'https://drive.google.com/file/d/old/view'
        _create_fmnp(conn, 1, 'photos/fmnp_1_a.jpg', old_url)

        update_fmnp_entry(1, photo_path='photos/fmnp_1_b.jpg',
                          changed_by='Tester')

        new_url = _drive_url_of(conn, 1)
        assert new_url is None, (
            f"photo_drive_url must be cleared when photo_path "
            f"changes so the sync re-uploads.  Got: {new_url!r}.")

    def test_photo_path_unchanged_leaves_drive_url_alone(self):
        """Edits that don't touch the photo (changing notes, amount,
        etc.) must NOT clear the Drive URL — the upload still
        applies."""
        from fam.models.fmnp import update_fmnp_entry

        conn = get_connection()
        _seed_minimal(conn)
        url = 'https://drive.google.com/file/d/keep/view'
        _create_fmnp(conn, 2, 'photos/fmnp_2.jpg', url)

        update_fmnp_entry(2, amount=999, notes='note edit',
                          changed_by='Tester')

        assert _drive_url_of(conn, 2) == url, (
            "Edits that leave photo_path alone must preserve "
            "photo_drive_url.  Clearing it on every update would "
            "force pointless re-uploads.")

    def test_no_op_update_does_not_clear_drive_url(self):
        """An update with all-unchanged values must be a no-op,
        same as before the fix."""
        from fam.models.fmnp import update_fmnp_entry

        conn = get_connection()
        _seed_minimal(conn)
        url = 'https://drive.google.com/file/d/noop/view'
        _create_fmnp(conn, 3, 'photos/fmnp_3.jpg', url)

        # All values match current state
        update_fmnp_entry(3, amount=500, vendor_id=1,
                          photo_path='photos/fmnp_3.jpg',
                          changed_by='Tester')

        assert _drive_url_of(conn, 3) == url


# ─── update_fmnp_entry: cleans hash cache on photo change ──────


class TestUpdateCleansHashCacheOnPhotoChange:
    """The dedup cache row must be dropped on photo change so a
    later upload of the same content goes fresh."""

    def test_old_url_hash_cache_row_removed(self):
        from fam.models.fmnp import update_fmnp_entry
        from fam.models.photo_hash import store_photo_hash

        conn = get_connection()
        _seed_minimal(conn)
        old_url = 'https://drive.google.com/file/d/old_url/view'
        _create_fmnp(conn, 10, 'photos/fmnp_10_a.jpg', old_url)
        store_photo_hash('hash-orig', old_url)

        assert _hash_count(conn, old_url) == 1

        update_fmnp_entry(10, photo_path='photos/fmnp_10_b.jpg',
                          changed_by='Tester')

        assert _hash_count(conn, old_url) == 0, (
            "The hash cache row pointing at the now-orphaned URL "
            "must be cleared so a future upload of the same "
            "content does not short-circuit to the old (and "
            "potentially VOID_-renamed) Drive file.")

    def test_shared_url_kept_when_active_record_still_references(
            self):
        """If two FMNP entries share a Drive URL, editing one
        must NOT clear the cache row — the other still uses it."""
        from fam.models.fmnp import update_fmnp_entry
        from fam.models.photo_hash import store_photo_hash

        conn = get_connection()
        _seed_minimal(conn)
        url = 'https://drive.google.com/file/d/shared/view'
        _create_fmnp(conn, 20, 'photos/a.jpg', url)
        _create_fmnp(conn, 21, 'photos/a.jpg', url)
        store_photo_hash('hash-shared', url)

        update_fmnp_entry(20, photo_path='photos/b.jpg',
                          changed_by='Tester')

        assert _hash_count(conn, url) == 1, (
            "Cache row must stay alive while another active "
            "record references the same URL.")


# ─── delete_fmnp_entry: cleans hash cache ──────────────────────


class TestDeleteFmnpDropsHashCache:

    def test_delete_drops_unique_hash(self):
        from fam.models.fmnp import delete_fmnp_entry
        from fam.models.photo_hash import store_photo_hash

        conn = get_connection()
        _seed_minimal(conn)
        url = 'https://drive.google.com/file/d/del/view'
        _create_fmnp(conn, 30, 'photos/fmnp_30.jpg', url)
        store_photo_hash('hash-del', url)

        delete_fmnp_entry(30, changed_by='Tester')

        assert _hash_count(conn, url) == 0, (
            "Soft-deleting an FMNP entry must drop the cache row "
            "for its unique photo URL — same shape as "
            "void_transaction.")

    def test_delete_keeps_hash_when_shared_with_active_txn(self):
        """If a transaction (active) and an FMNP entry share a URL
        because of dedup, deleting the FMNP entry alone keeps the
        cache row."""
        from fam.models.fmnp import delete_fmnp_entry
        from fam.models.photo_hash import store_photo_hash

        conn = get_connection()
        _seed_minimal(conn)
        url = 'https://drive.google.com/file/d/cross/view'
        # Active transaction referencing the URL
        conn.execute(
            "INSERT INTO payment_methods "
            "(id, name, match_percent, is_active, sort_order) "
            "VALUES (1, 'Cash', 100.0, 1, 1)")
        conn.execute(
            "INSERT INTO transactions "
            "(id, fam_transaction_id, market_day_id, vendor_id, "
            " receipt_total, status, created_at) "
            "VALUES (1, 'FAM-T-1', 1, 1, 1000, 'Confirmed', "
            "        '2026-05-06 12:00:00')")
        conn.execute(
            "INSERT INTO payment_line_items "
            "(transaction_id, payment_method_id, "
            " method_name_snapshot, match_percent_snapshot, "
            " method_amount, match_amount, customer_charged, "
            " photo_drive_url, created_at) "
            "VALUES (1, 1, 'Cash', 100.0, 2000, 1000, 1000, "
            "        ?, '2026-05-06 12:00:00')",
            (url,))
        conn.commit()

        _create_fmnp(conn, 40, 'photos/cross.jpg', url)
        store_photo_hash('hash-cross', url)

        delete_fmnp_entry(40, changed_by='Tester')

        assert _hash_count(conn, url) == 1, (
            "Active transaction still references the URL — the "
            "cache row must survive an FMNP delete.")
