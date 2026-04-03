"""Comprehensive tests for multi-photo support.

Covers:
  - photo_paths.py: parse_photo_paths, encode_photo_paths
  - photo_storage.py: store_photo with prefix, photo_exists
  - fmnp.py: get_pending_photo_uploads (multi-photo awareness)
  - drive.py: upload_pending_photos (multi-photo upload flow)
  - data_collector.py: _collect_fmnp_entries (multi-photo URL display)
  - payment_screen integration: photo_source_paths in get_data/storage
"""

import json
import os
import shutil
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.utils.photo_paths import parse_photo_paths, encode_photo_paths


# ──────────────────────────────────────────────────────────────────
# Fixture: fresh database per test
# ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_multi_photo.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    yield conn
    close_connection()


def _seed_fmnp(conn):
    """Seed data for FMNP-related tests."""
    conn.execute(
        "INSERT INTO markets (id, name) VALUES (1, 'Test Market')"
    )
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, opened_by)"
        " VALUES (1, 1, '2026-03-10', 'Open', 'Alice')"
    )
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'Farm Stand')"
    )
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, denomination, photo_required)"
        " VALUES (1, 'FMNP', 100.0, 2500, 'Mandatory')"
    )
    conn.commit()


# ══════════════════════════════════════════════════════════════════
# parse_photo_paths
# ══════════════════════════════════════════════════════════════════
class TestParsePhotoPaths:
    """Unit tests for parse_photo_paths()."""

    def test_none_returns_empty(self):
        assert parse_photo_paths(None) == []

    def test_empty_string_returns_empty(self):
        assert parse_photo_paths("") == []

    def test_whitespace_only_returns_empty(self):
        assert parse_photo_paths("   ") == []

    def test_legacy_single_path(self):
        result = parse_photo_paths("photos/fmnp_42_123.jpg")
        assert result == ["photos/fmnp_42_123.jpg"]

    def test_json_array_single(self):
        result = parse_photo_paths('["photos/a.jpg"]')
        assert result == ["photos/a.jpg"]

    def test_json_array_multiple(self):
        result = parse_photo_paths('["photos/a.jpg", "photos/b.jpg", "photos/c.jpg"]')
        assert result == ["photos/a.jpg", "photos/b.jpg", "photos/c.jpg"]

    def test_json_array_filters_empty_strings(self):
        """Empty strings inside a JSON array should be filtered out."""
        result = parse_photo_paths('["photos/a.jpg", "", "photos/c.jpg"]')
        assert result == ["photos/a.jpg", "photos/c.jpg"]

    def test_json_array_filters_none_values(self):
        """null values inside a JSON array should be filtered out."""
        result = parse_photo_paths('["photos/a.jpg", null, "photos/c.jpg"]')
        assert result == ["photos/a.jpg", "photos/c.jpg"]

    def test_malformed_json_falls_back_to_single(self):
        """Malformed JSON starting with [ should fall back to single-path."""
        result = parse_photo_paths("[this is not json")
        assert result == ["[this is not json"]

    def test_json_object_falls_back_to_single(self):
        """A JSON object (not array) should fall back to single-path."""
        result = parse_photo_paths('{"path": "photos/a.jpg"}')
        assert result == ['{"path": "photos/a.jpg"}']

    def test_whitespace_around_json(self):
        """Leading/trailing whitespace around JSON should be handled."""
        result = parse_photo_paths('  ["photos/a.jpg"]  ')
        assert result == ["photos/a.jpg"]

    def test_legacy_windows_backslash_path(self):
        """Windows-style paths should work as legacy single paths."""
        result = parse_photo_paths("photos\\fmnp_42_123.jpg")
        assert result == ["photos\\fmnp_42_123.jpg"]

    def test_empty_json_array(self):
        """An empty JSON array should return empty list."""
        result = parse_photo_paths("[]")
        assert result == []

    def test_json_array_all_empty_strings(self):
        """A JSON array of only empty strings should return empty list."""
        result = parse_photo_paths('["", "", ""]')
        assert result == []

    def test_json_array_preserves_order(self):
        paths = '["photos/c.jpg", "photos/a.jpg", "photos/b.jpg"]'
        result = parse_photo_paths(paths)
        assert result == ["photos/c.jpg", "photos/a.jpg", "photos/b.jpg"]


# ══════════════════════════════════════════════════════════════════
# encode_photo_paths
# ══════════════════════════════════════════════════════════════════
class TestEncodePhotoPaths:
    """Unit tests for encode_photo_paths()."""

    def test_empty_list_returns_none(self):
        assert encode_photo_paths([]) is None

    def test_list_of_empty_strings_returns_none(self):
        assert encode_photo_paths(["", "", ""]) is None

    def test_single_path(self):
        result = encode_photo_paths(["photos/a.jpg"])
        assert result == '["photos/a.jpg"]'
        # Should be valid JSON
        assert json.loads(result) == ["photos/a.jpg"]

    def test_multiple_paths(self):
        result = encode_photo_paths(["photos/a.jpg", "photos/b.jpg"])
        parsed = json.loads(result)
        assert parsed == ["photos/a.jpg", "photos/b.jpg"]

    def test_filters_empty_strings(self):
        result = encode_photo_paths(["photos/a.jpg", "", "photos/c.jpg"])
        parsed = json.loads(result)
        assert parsed == ["photos/a.jpg", "photos/c.jpg"]

    def test_filters_none_values(self):
        result = encode_photo_paths(["photos/a.jpg", None, "photos/c.jpg"])
        parsed = json.loads(result)
        assert parsed == ["photos/a.jpg", "photos/c.jpg"]

    def test_list_of_only_none_returns_none(self):
        assert encode_photo_paths([None, None]) is None


# ══════════════════════════════════════════════════════════════════
# Round-trip: encode → parse
# ══════════════════════════════════════════════════════════════════
class TestRoundTrip:
    """Ensure encode → parse produces the original list."""

    def test_single_path_round_trip(self):
        original = ["photos/fmnp_1_12345.jpg"]
        encoded = encode_photo_paths(original)
        decoded = parse_photo_paths(encoded)
        assert decoded == original

    def test_multi_path_round_trip(self):
        original = ["photos/fmnp_1_111.jpg", "photos/fmnp_1_222.jpg",
                     "photos/fmnp_1_333.jpg"]
        encoded = encode_photo_paths(original)
        decoded = parse_photo_paths(encoded)
        assert decoded == original

    def test_legacy_path_re_encoded(self):
        """A legacy single path should survive parse → encode → parse."""
        legacy = "photos/fmnp_42_old.jpg"
        decoded_once = parse_photo_paths(legacy)
        re_encoded = encode_photo_paths(decoded_once)
        decoded_again = parse_photo_paths(re_encoded)
        assert decoded_again == ["photos/fmnp_42_old.jpg"]

    def test_empty_round_trip(self):
        """None/empty should survive round-trip."""
        assert encode_photo_paths(parse_photo_paths(None)) is None
        assert encode_photo_paths(parse_photo_paths("")) is None


# ══════════════════════════════════════════════════════════════════
# photo_storage.py — store_photo with prefix
# ══════════════════════════════════════════════════════════════════
class TestPhotoStorage:
    """Tests for store_photo prefix parameter and photo_exists.

    We mock PySide6 QPixmap to avoid Qt initialization, which can hang
    in headless test environments without a QApplication.
    """

    @staticmethod
    def _mock_qpixmap():
        """Patch PySide6 so store_photo uses the shutil fallback."""
        return patch('fam.utils.photo_storage.QPixmap',
                     side_effect=ImportError("mocked out"),
                     create=True)

    def test_store_photo_default_prefix(self, tmp_path, fresh_db):
        """Default prefix should be 'fmnp'."""
        src = tmp_path / "test_image.jpg"
        src.write_bytes(b'\xff\xd8\xff' + b'\x00' * 100)  # minimal JPEG header

        with patch('fam.utils.photo_storage.get_photos_dir',
                   return_value=str(tmp_path / 'photos')):
            os.makedirs(tmp_path / 'photos', exist_ok=True)
            from fam.utils.photo_storage import store_photo
            # Force shutil fallback by making QPixmap import fail
            with patch.dict('sys.modules', {'PySide6.QtGui': None, 'PySide6.QtCore': None}):
                rel_path = store_photo(str(src), 42)
            assert rel_path.startswith("photos/fmnp_42_")
            assert rel_path.endswith(".jpg")

    def test_store_photo_pay_prefix(self, tmp_path, fresh_db):
        """Custom 'pay' prefix for payment screen photos."""
        src = tmp_path / "receipt.png"
        src.write_bytes(b'\x89PNG' + b'\x00' * 100)

        with patch('fam.utils.photo_storage.get_photos_dir',
                   return_value=str(tmp_path / 'photos')):
            os.makedirs(tmp_path / 'photos', exist_ok=True)
            from fam.utils.photo_storage import store_photo
            with patch.dict('sys.modules', {'PySide6.QtGui': None, 'PySide6.QtCore': None}):
                rel_path = store_photo(str(src), 7, prefix='pay')
            assert rel_path.startswith("photos/pay_7_")
            assert rel_path.endswith(".png")

    def test_store_multiple_photos_unique_filenames(self, tmp_path, fresh_db):
        """Multiple photos for same entry get unique filenames via timestamp."""
        src = tmp_path / "test.jpg"
        src.write_bytes(b'\xff\xd8\xff' + b'\x00' * 100)

        with patch('fam.utils.photo_storage.get_photos_dir',
                   return_value=str(tmp_path / 'photos')):
            os.makedirs(tmp_path / 'photos', exist_ok=True)
            from fam.utils.photo_storage import store_photo
            with patch.dict('sys.modules', {'PySide6.QtGui': None, 'PySide6.QtCore': None}):
                with patch('fam.utils.photo_storage.time') as mock_time:
                    mock_time.time.return_value = 1000
                    p1 = store_photo(str(src), 1)
                    mock_time.time.return_value = 1001
                    p2 = store_photo(str(src), 1)
                    mock_time.time.return_value = 1002
                    p3 = store_photo(str(src), 1)
            # All should be unique
            assert p1 != p2 != p3

    def test_photo_exists_true(self, tmp_path, fresh_db):
        """photo_exists returns True for existing files."""
        photos_dir = tmp_path / 'photos'
        photos_dir.mkdir()
        (photos_dir / 'test.jpg').write_bytes(b'data')

        with patch('fam.utils.photo_storage.get_photo_full_path',
                   return_value=str(photos_dir / 'test.jpg')):
            from fam.utils.photo_storage import photo_exists
            assert photo_exists('photos/test.jpg') is True

    def test_photo_exists_false_missing(self, tmp_path, fresh_db):
        """photo_exists returns False for missing files."""
        with patch('fam.utils.photo_storage.get_photo_full_path',
                   return_value=str(tmp_path / 'nonexistent.jpg')):
            from fam.utils.photo_storage import photo_exists
            assert photo_exists('photos/nonexistent.jpg') is False

    def test_photo_exists_false_empty_path(self, fresh_db):
        """photo_exists returns False for empty/None paths."""
        from fam.utils.photo_storage import photo_exists
        assert photo_exists("") is False
        assert photo_exists(None) is False


# ══════════════════════════════════════════════════════════════════
# fmnp.py — get_pending_photo_uploads (multi-photo aware)
# ══════════════════════════════════════════════════════════════════
class TestPendingPhotoUploads:
    """Tests for get_pending_photo_uploads with multi-photo entries."""

    def test_no_entries_returns_empty(self, fresh_db):
        from fam.models.fmnp import get_pending_photo_uploads
        assert get_pending_photo_uploads() == []

    def test_entry_without_photo_not_pending(self, fresh_db):
        """Entries with no photo_path should not be pending."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, get_pending_photo_uploads
        create_fmnp_entry(1, 1, 2500, 'Alice')  # no photo_path
        assert get_pending_photo_uploads() == []

    def test_single_photo_no_url_is_pending(self, fresh_db):
        """Entry with photo_path but no drive_url should be pending."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, get_pending_photo_uploads
        create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')
        pending = get_pending_photo_uploads()
        assert len(pending) == 1
        assert pending[0]['photo_path'] == 'photos/a.jpg'

    def test_single_photo_with_url_not_pending(self, fresh_db):
        """Entry with both photo_path and drive_url should not be pending."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, update_fmnp_photo_drive_url, get_pending_photo_uploads
        entry_id = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')
        update_fmnp_photo_drive_url(entry_id, 'https://drive.google.com/file/d/abc/view')
        assert get_pending_photo_uploads() == []

    def test_multi_photo_all_uploaded_not_pending(self, fresh_db):
        """3 photos with 3 URLs should not be pending."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, update_fmnp_photo_drive_url, get_pending_photo_uploads
        paths = encode_photo_paths(['photos/a.jpg', 'photos/b.jpg', 'photos/c.jpg'])
        entry_id = create_fmnp_entry(1, 1, 7500, 'Alice', photo_path=paths)
        urls = encode_photo_paths([
            'https://drive.google.com/file/d/1/view',
            'https://drive.google.com/file/d/2/view',
            'https://drive.google.com/file/d/3/view',
        ])
        update_fmnp_photo_drive_url(entry_id, urls)
        assert get_pending_photo_uploads() == []

    def test_multi_photo_partial_upload_is_pending(self, fresh_db):
        """3 photos with only 2 URLs should be pending."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, update_fmnp_photo_drive_url, get_pending_photo_uploads
        paths = encode_photo_paths(['photos/a.jpg', 'photos/b.jpg', 'photos/c.jpg'])
        entry_id = create_fmnp_entry(1, 1, 7500, 'Alice', photo_path=paths)
        urls = encode_photo_paths([
            'https://drive.google.com/file/d/1/view',
            'https://drive.google.com/file/d/2/view',
        ])
        update_fmnp_photo_drive_url(entry_id, urls)
        pending = get_pending_photo_uploads()
        assert len(pending) == 1

    def test_multi_photo_no_uploads_is_pending(self, fresh_db):
        """3 photos with no URLs should be pending."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, get_pending_photo_uploads
        paths = encode_photo_paths(['photos/a.jpg', 'photos/b.jpg', 'photos/c.jpg'])
        create_fmnp_entry(1, 1, 7500, 'Alice', photo_path=paths)
        pending = get_pending_photo_uploads()
        assert len(pending) == 1

    def test_legacy_single_path_pending(self, fresh_db):
        """Legacy single-path entry (not JSON) should be detected as pending."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, get_pending_photo_uploads
        # Old-style single path (not JSON encoded)
        create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/fmnp_1_old.jpg')
        pending = get_pending_photo_uploads()
        assert len(pending) == 1

    def test_deleted_entry_not_pending(self, fresh_db):
        """Deleted FMNP entries should not appear in pending uploads."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, delete_fmnp_entry, get_pending_photo_uploads
        entry_id = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')
        delete_fmnp_entry(entry_id)
        assert get_pending_photo_uploads() == []

    def test_multiple_entries_mixed_status(self, fresh_db):
        """Only entries with incomplete uploads should be pending."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import (
            create_fmnp_entry, update_fmnp_photo_drive_url, get_pending_photo_uploads
        )
        # Entry 1: complete (1 photo, 1 URL)
        e1 = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')
        update_fmnp_photo_drive_url(e1, 'https://drive/1')

        # Entry 2: pending (2 photos, 0 URLs)
        paths2 = encode_photo_paths(['photos/b1.jpg', 'photos/b2.jpg'])
        create_fmnp_entry(1, 1, 5000, 'Bob', photo_path=paths2)

        # Entry 3: no photo
        create_fmnp_entry(1, 1, 2500, 'Carol')

        pending = get_pending_photo_uploads()
        assert len(pending) == 1
        assert 'b1.jpg' in pending[0]['photo_path']


# ══════════════════════════════════════════════════════════════════
# drive.py — upload_pending_photos multi-photo flow
# ══════════════════════════════════════════════════════════════════
class TestDriveUploadMultiPhoto:
    """Tests for upload_pending_photos with multi-photo entries."""

    def test_no_pending_returns_zero(self, fresh_db):
        from fam.sync.drive import upload_pending_photos
        with patch('fam.sync.drive._ensure_imports'), \
             patch('fam.sync.drive._get_session', return_value='fake_session'), \
             patch('fam.sync.drive._verify_and_clear_dead_urls', return_value=0), \
             patch('fam.sync.drive._process_voided_photos', return_value=0):
            result = upload_pending_photos()
        assert result['uploaded'] == 0

    # Common mock set for all drive upload tests
    _DRIVE_MOCKS = [
        'fam.sync.drive._ensure_imports',
        'fam.sync.drive._get_session',
        'fam.sync.drive._verify_folder_access',
        'fam.sync.drive._resolve_entry_folder',
    ]

    def _drive_patches(self, upload_side_effect=None, upload_return=None,
                       photo_exists_side_effect=None, full_path_side_effect=None):
        """Return a list of context managers for common drive mocks."""
        patches = [
            patch('fam.sync.drive._ensure_imports'),
            patch('fam.sync.drive._get_session', return_value='fake_session'),
            patch('fam.sync.drive._verify_folder_access', return_value=(True, 'OK')),
            patch('fam.sync.drive._resolve_entry_folder', return_value='subfolder_id'),
            patch('fam.utils.app_settings.get_setting', return_value='fake_folder_id'),
            patch('fam.sync.drive._verify_and_clear_dead_urls', return_value=0),
            patch('fam.sync.drive._process_voided_photos', return_value=0),
        ]
        if upload_side_effect:
            patches.append(patch('fam.sync.drive.upload_photo', side_effect=upload_side_effect))
        elif upload_return is not None:
            patches.append(patch('fam.sync.drive.upload_photo', return_value=upload_return))

        if photo_exists_side_effect:
            patches.append(patch('fam.utils.photo_storage.photo_exists', side_effect=photo_exists_side_effect))
        else:
            patches.append(patch('fam.utils.photo_storage.photo_exists', return_value=True))

        if full_path_side_effect:
            patches.append(patch('fam.utils.photo_storage.get_photo_full_path', side_effect=full_path_side_effect))
        else:
            patches.append(patch('fam.utils.photo_storage.get_photo_full_path', side_effect=lambda p: f'/full/{p}'))

        # Content hash mock — each file gets a unique hash based on its path
        patches.append(patch('fam.utils.photo_storage.compute_file_hash',
                             side_effect=lambda p: f'hash_{os.path.basename(p)}'))

        return patches

    def test_single_photo_upload(self, fresh_db):
        """Single photo upload should work as before."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, get_fmnp_entry_by_id

        entry_id = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')

        patches = self._drive_patches(upload_return='https://drive/url_a')
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patches[10]:
            from fam.sync.drive import upload_pending_photos
            stats = upload_pending_photos()

        assert stats['uploaded'] == 1
        assert stats['fmnp_uploaded'] == 1

        entry = get_fmnp_entry_by_id(entry_id)
        urls = parse_photo_paths(entry['photo_drive_url'])
        assert urls == ['https://drive/url_a']

    def test_multi_photo_all_succeed(self, fresh_db):
        """All 3 photos upload successfully."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, get_fmnp_entry_by_id

        paths = encode_photo_paths(['photos/a.jpg', 'photos/b.jpg', 'photos/c.jpg'])
        entry_id = create_fmnp_entry(1, 1, 7500, 'Alice', photo_path=paths)

        upload_results = iter([
            'https://drive/url_a',
            'https://drive/url_b',
            'https://drive/url_c',
        ])

        patches = self._drive_patches(upload_side_effect=lambda *a, **kw: next(upload_results))
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patches[10]:
            from fam.sync.drive import upload_pending_photos
            stats = upload_pending_photos()

        assert stats['uploaded'] == 3
        assert stats['fmnp_uploaded'] == 3
        entry = get_fmnp_entry_by_id(entry_id)
        urls = parse_photo_paths(entry['photo_drive_url'])
        assert len(urls) == 3
        assert urls[0] == 'https://drive/url_a'
        assert urls[2] == 'https://drive/url_c'

    def test_multi_photo_partial_success_saves_progress(self, fresh_db):
        """If photo 3 fails, photos 1-2 should be saved as progress."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, get_fmnp_entry_by_id

        paths = encode_photo_paths(['photos/a.jpg', 'photos/b.jpg', 'photos/c.jpg'])
        entry_id = create_fmnp_entry(1, 1, 7500, 'Alice', photo_path=paths)

        upload_results = iter([
            'https://drive/url_a',
            'https://drive/url_b',
            None,  # photo c fails
        ])

        patches = self._drive_patches(upload_side_effect=lambda *a, **kw: next(upload_results))
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patches[10]:
            from fam.sync.drive import upload_pending_photos
            stats = upload_pending_photos()

        assert stats['uploaded'] == 2
        assert stats['failed'] == 1
        entry = get_fmnp_entry_by_id(entry_id)
        urls = parse_photo_paths(entry['photo_drive_url'])
        assert len(urls) == 2
        assert urls == ['https://drive/url_a', 'https://drive/url_b']

    def test_multi_photo_resume_from_partial(self, fresh_db):
        """Second sync cycle should resume from where we left off."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, update_fmnp_photo_drive_url, get_fmnp_entry_by_id

        paths = encode_photo_paths(['photos/a.jpg', 'photos/b.jpg', 'photos/c.jpg'])
        entry_id = create_fmnp_entry(1, 1, 7500, 'Alice', photo_path=paths)

        partial_urls = encode_photo_paths(['https://drive/url_a', 'https://drive/url_b'])
        update_fmnp_photo_drive_url(entry_id, partial_urls)

        patches = self._drive_patches(upload_return='https://drive/url_c')
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patches[10]:
            from fam.sync.drive import upload_pending_photos
            stats = upload_pending_photos()

        assert stats['uploaded'] == 1
        assert stats['fmnp_uploaded'] == 1

        entry = get_fmnp_entry_by_id(entry_id)
        urls = parse_photo_paths(entry['photo_drive_url'])
        assert len(urls) == 3

    def test_missing_photo_file_skipped(self, fresh_db):
        """Missing photo files should be skipped with a warning."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, get_fmnp_entry_by_id

        paths = encode_photo_paths(['photos/a.jpg', 'photos/missing.jpg', 'photos/c.jpg'])
        entry_id = create_fmnp_entry(1, 1, 7500, 'Alice', photo_path=paths)

        upload_results = iter([
            'https://drive/url_a',
            'https://drive/url_c',
        ])

        patches = self._drive_patches(
            upload_side_effect=lambda *a, **kw: next(upload_results),
            photo_exists_side_effect=lambda p: 'missing' not in p)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patches[10]:
            from fam.sync.drive import upload_pending_photos
            stats = upload_pending_photos()

        assert stats['uploaded'] == 2
        assert stats['failed'] == 1
        entry = get_fmnp_entry_by_id(entry_id)
        urls = parse_photo_paths(entry['photo_drive_url'])
        assert len(urls) == 2

    def test_legacy_single_path_upload(self, fresh_db):
        """Legacy single-path (non-JSON) should still upload correctly."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, get_fmnp_entry_by_id

        entry_id = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/old_single.jpg')

        patches = self._drive_patches(upload_return='https://drive/url_old')
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patches[10]:
            from fam.sync.drive import upload_pending_photos
            stats = upload_pending_photos()

        assert stats['uploaded'] == 1
        assert stats['fmnp_uploaded'] == 1
        entry = get_fmnp_entry_by_id(entry_id)
        urls = parse_photo_paths(entry['photo_drive_url'])
        assert len(urls) == 1
        assert urls[0] == 'https://drive/url_old'


# ══════════════════════════════════════════════════════════════════
# data_collector.py — FMNP entries multi-photo URL display
# ══════════════════════════════════════════════════════════════════
class TestDataCollectorMultiPhoto:
    """Tests for _collect_fmnp_entries per-check row expansion."""

    def test_no_photo_url(self, fresh_db):
        """Entry with no photo → 1 row with empty Photo field."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry
        create_fmnp_entry(1, 1, 2500, 'Alice')

        from fam.sync.data_collector import _collect_fmnp_entries
        result = _collect_fmnp_entries(fresh_db, 1)
        assert len(result) == 1
        assert result[0]['Photo'] == ''
        assert result[0]['Source'] == 'FMNP Entry'
        assert result[0]['Check'] == '1 of 1'
        assert 'Entry ID' in result[0]

    def test_single_photo_url(self, fresh_db):
        """Single photo → 1 row with that URL."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, update_fmnp_photo_drive_url
        entry_id = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')
        update_fmnp_photo_drive_url(entry_id, 'https://drive.google.com/file/d/1/view')

        from fam.sync.data_collector import _collect_fmnp_entries
        result = _collect_fmnp_entries(fresh_db, 1)
        assert len(result) == 1
        assert result[0]['Photo'] == 'https://drive.google.com/file/d/1/view'
        assert result[0]['Check'] == '1 of 1'

    def test_multi_photo_urls_expand_to_rows(self, fresh_db):
        """Multiple photos → one row per photo with individual URLs."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, update_fmnp_photo_drive_url
        paths = encode_photo_paths(['photos/a.jpg', 'photos/b.jpg'])
        entry_id = create_fmnp_entry(1, 1, 5000, 'Alice', photo_path=paths)
        urls = encode_photo_paths([
            'https://drive/1',
            'https://drive/2',
        ])
        update_fmnp_photo_drive_url(entry_id, urls)

        from fam.sync.data_collector import _collect_fmnp_entries
        result = _collect_fmnp_entries(fresh_db, 1)
        assert len(result) == 2
        assert result[0]['Photo'] == 'https://drive/1'
        assert result[0]['Check'] == '1 of 2'
        assert result[0]['Check Amount'] == 25.0
        assert result[0]['Total Amount'] == 50.0
        assert result[1]['Photo'] == 'https://drive/2'
        assert result[1]['Check'] == '2 of 2'

    def test_legacy_url_string(self, fresh_db):
        """Legacy single URL string (non-JSON) → 1 row."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, update_fmnp_photo_drive_url
        entry_id = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')
        update_fmnp_photo_drive_url(entry_id, 'https://drive.google.com/file/d/old/view')

        from fam.sync.data_collector import _collect_fmnp_entries
        result = _collect_fmnp_entries(fresh_db, 1)
        assert len(result) == 1
        assert result[0]['Photo'] == 'https://drive.google.com/file/d/old/view'


# ══════════════════════════════════════════════════════════════════
# Payment method photo_required setting
# ══════════════════════════════════════════════════════════════════
class TestPaymentMethodPhotoRequired:
    """Tests for the photo_required column on payment_methods."""

    def test_default_photo_required_is_null(self, fresh_db):
        """New payment methods should have photo_required = NULL by default."""
        from fam.models.payment_method import create_payment_method, get_payment_method_by_id
        pm_id = create_payment_method("Test", 50.0)
        pm = get_payment_method_by_id(pm_id)
        assert pm['photo_required'] is None

    def test_set_photo_required_mandatory(self, fresh_db):
        from fam.models.payment_method import create_payment_method, update_payment_method, get_payment_method_by_id
        pm_id = create_payment_method("FMNP", 100.0)
        update_payment_method(pm_id, photo_required='Mandatory')
        pm = get_payment_method_by_id(pm_id)
        assert pm['photo_required'] == 'Mandatory'

    def test_set_photo_required_optional(self, fresh_db):
        from fam.models.payment_method import create_payment_method, update_payment_method, get_payment_method_by_id
        pm_id = create_payment_method("FMNP", 100.0)
        update_payment_method(pm_id, photo_required='Optional')
        pm = get_payment_method_by_id(pm_id)
        assert pm['photo_required'] == 'Optional'

    def test_set_photo_required_off_clears_to_null(self, fresh_db):
        """Setting photo_required to 'Off' should store NULL in DB."""
        from fam.models.payment_method import create_payment_method, update_payment_method, get_payment_method_by_id
        pm_id = create_payment_method("FMNP", 100.0)
        update_payment_method(pm_id, photo_required='Mandatory')
        update_payment_method(pm_id, photo_required='Off')
        pm = get_payment_method_by_id(pm_id)
        assert pm['photo_required'] is None


# ══════════════════════════════════════════════════════════════════
# Transaction photo_path storage
# ══════════════════════════════════════════════════════════════════
class TestTransactionPhotoPath:
    """Tests for photo_path column on payment_line_items."""

    def _seed_transaction(self, conn):
        """Create a complete transaction for testing."""
        _seed_fmnp(conn)
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
            " VALUES (2, 'Cash', 0.0, 1, 2)"
        )
        conn.execute(
            "INSERT INTO customer_orders (id, market_day_id, customer_label)"
            " VALUES (1, 1, 'C-001')"
        )
        conn.execute(
            "INSERT INTO transactions (id, market_day_id, vendor_id, receipt_total,"
            " fam_transaction_id, customer_order_id, status)"
            " VALUES (1, 1, 1, 5000, 'FAM-001', 1, 'Draft')"
        )
        conn.commit()
        return 1  # transaction_id

    def test_save_with_single_photo(self, fresh_db):
        """Single photo path should be stored in line item."""
        txn_id = self._seed_transaction(fresh_db)
        from fam.models.transaction import save_payment_line_items, get_payment_line_items

        items = [{
            'payment_method_id': 1,
            'method_name_snapshot': 'FMNP',
            'match_percent_snapshot': 100.0,
            'method_amount': 5000,
            'match_amount': 2500,
            'customer_charged': 2500,
            'photo_path': 'photos/pay_1_123.jpg',
        }]
        save_payment_line_items(txn_id, items)

        saved = get_payment_line_items(txn_id)
        assert len(saved) == 1
        assert saved[0]['photo_path'] == 'photos/pay_1_123.jpg'

    def test_save_with_multi_photo_json(self, fresh_db):
        """JSON array of photo paths should be stored in line item."""
        txn_id = self._seed_transaction(fresh_db)
        from fam.models.transaction import save_payment_line_items, get_payment_line_items

        photo_json = encode_photo_paths([
            'photos/pay_1_111.jpg',
            'photos/pay_1_222.jpg',
            'photos/pay_1_333.jpg',
        ])
        items = [{
            'payment_method_id': 1,
            'method_name_snapshot': 'FMNP',
            'match_percent_snapshot': 100.0,
            'method_amount': 5000,
            'match_amount': 2500,
            'customer_charged': 2500,
            'photo_path': photo_json,
        }]
        save_payment_line_items(txn_id, items)

        saved = get_payment_line_items(txn_id)
        assert len(saved) == 1
        paths = parse_photo_paths(saved[0]['photo_path'])
        assert len(paths) == 3
        assert 'pay_1_111.jpg' in paths[0]

    def test_save_without_photo(self, fresh_db):
        """Line items without photos should have NULL photo_path."""
        txn_id = self._seed_transaction(fresh_db)
        from fam.models.transaction import save_payment_line_items, get_payment_line_items

        items = [{
            'payment_method_id': 2,  # Cash — no photo
            'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 20.0,
            'match_amount': 0.0,
            'customer_charged': 20.0,
        }]
        save_payment_line_items(txn_id, items)

        saved = get_payment_line_items(txn_id)
        assert saved[0]['photo_path'] is None


# ══════════════════════════════════════════════════════════════════
# Edge cases — denomination-based photo count calculations
# ══════════════════════════════════════════════════════════════════
class TestDenominationPhotoCount:
    """Test the check-count logic used in both FMNP screen and PaymentRow."""

    def _calc_fmnp_count(self, amount, denomination):
        """Replicate FMNPScreen._get_expected_photo_count logic."""
        if amount <= 0:
            return 1  # FMNP screen always shows at least 1
        if denomination and denomination > 0:
            return max(1, int(amount / denomination))
        return 1

    def _calc_payment_count(self, charge, denomination):
        """Replicate PaymentRow._get_check_count logic."""
        if charge <= 0:
            return 0  # PaymentRow returns 0 when no charge
        if denomination and denomination > 0:
            return max(1, int(charge / denomination))
        return 1

    def test_fmnp_zero_amount(self):
        assert self._calc_fmnp_count(0, 25) == 1

    def test_fmnp_single_check(self):
        assert self._calc_fmnp_count(25, 25) == 1

    def test_fmnp_two_checks(self):
        assert self._calc_fmnp_count(50, 25) == 2

    def test_fmnp_three_checks(self):
        assert self._calc_fmnp_count(75, 25) == 3

    def test_fmnp_ten_checks(self):
        assert self._calc_fmnp_count(250, 25) == 10

    def test_fmnp_no_denomination(self):
        assert self._calc_fmnp_count(100, None) == 1

    def test_fmnp_denomination_zero(self):
        assert self._calc_fmnp_count(100, 0) == 1

    def test_fmnp_non_multiple_rounds_down(self):
        """$60 with $25 denomination = 2 checks (rounds down from 2.4)."""
        assert self._calc_fmnp_count(60, 25) == 2

    def test_payment_zero_charge(self):
        assert self._calc_payment_count(0, 25) == 0

    def test_payment_single_check(self):
        assert self._calc_payment_count(25, 25) == 1

    def test_payment_three_checks(self):
        assert self._calc_payment_count(75, 25) == 3

    def test_payment_no_denomination(self):
        assert self._calc_payment_count(100, None) == 1

    def test_payment_small_amount_below_denom(self):
        """$10 with $25 denomination should still be 1 check (min 1)."""
        assert self._calc_payment_count(10, 25) == 1

    def test_large_denomination(self):
        """$100 with $50 denomination = 2 checks."""
        assert self._calc_fmnp_count(100, 50) == 2
        assert self._calc_payment_count(100, 50) == 2


# ══════════════════════════════════════════════════════════════════
# FMNP create/update with multi-photo — integration tests
# ══════════════════════════════════════════════════════════════════
class TestFMNPMultiPhotoIntegration:
    """Integration tests for FMNP entry creation/update with multi-photo paths."""

    def test_create_entry_with_multi_photo_path(self, fresh_db):
        """Creating an entry with a JSON-encoded multi-photo path."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, get_fmnp_entry_by_id

        photo_path = encode_photo_paths([
            'photos/fmnp_1_100.jpg',
            'photos/fmnp_1_101.jpg',
            'photos/fmnp_1_102.jpg',
        ])
        entry_id = create_fmnp_entry(1, 1, 7500, 'Alice', check_count=3,
                                      photo_path=photo_path)
        entry = get_fmnp_entry_by_id(entry_id)
        paths = parse_photo_paths(entry['photo_path'])
        assert len(paths) == 3

    def test_update_entry_add_photos(self, fresh_db):
        """Updating an entry to add photos."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, update_fmnp_entry, get_fmnp_entry_by_id

        entry_id = create_fmnp_entry(1, 1, 2500, 'Alice')
        # Initially no photos
        entry = get_fmnp_entry_by_id(entry_id)
        assert entry['photo_path'] is None

        # Add a photo
        new_path = encode_photo_paths(['photos/fmnp_1_200.jpg'])
        update_fmnp_entry(entry_id, photo_path=new_path)
        entry = get_fmnp_entry_by_id(entry_id)
        paths = parse_photo_paths(entry['photo_path'])
        assert len(paths) == 1

    def test_update_entry_clear_photos(self, fresh_db):
        """Updating an entry to clear all photos."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, update_fmnp_entry, get_fmnp_entry_by_id

        photo_path = encode_photo_paths(['photos/fmnp_1_100.jpg'])
        entry_id = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path=photo_path)

        # Clear photos by setting to None
        update_fmnp_entry(entry_id, photo_path=None)
        entry = get_fmnp_entry_by_id(entry_id)
        assert entry['photo_path'] is None

    def test_update_amount_keeps_photos(self, fresh_db):
        """Updating amount only should not affect photos."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, update_fmnp_entry, get_fmnp_entry_by_id

        photo_path = encode_photo_paths(['photos/a.jpg', 'photos/b.jpg'])
        entry_id = create_fmnp_entry(1, 1, 5000, 'Alice', photo_path=photo_path)

        # Update amount only (photo_path is _UNSET by default)
        update_fmnp_entry(entry_id, amount=7500)

        entry = get_fmnp_entry_by_id(entry_id)
        assert entry['amount'] == 7500
        paths = parse_photo_paths(entry['photo_path'])
        assert len(paths) == 2  # Photos preserved


# ══════════════════════════════════════════════════════════════════
# Edge cases — photo_paths with special characters
# ══════════════════════════════════════════════════════════════════
class TestPhotoPathEdgeCases:
    """Edge cases with unusual photo path strings."""

    def test_path_with_spaces(self):
        """Paths with spaces should round-trip correctly."""
        paths = ['photos/my photo.jpg', 'photos/check 2.jpg']
        encoded = encode_photo_paths(paths)
        decoded = parse_photo_paths(encoded)
        assert decoded == paths

    def test_path_with_unicode(self):
        """Unicode in paths should round-trip correctly."""
        paths = ['photos/café_check.jpg', 'photos/日本語.png']
        encoded = encode_photo_paths(paths)
        decoded = parse_photo_paths(encoded)
        assert decoded == paths

    def test_very_long_path(self):
        """Very long paths should work."""
        long_path = 'photos/' + 'a' * 200 + '.jpg'
        paths = [long_path]
        encoded = encode_photo_paths(paths)
        decoded = parse_photo_paths(encoded)
        assert decoded == paths

    def test_many_photos(self):
        """Large number of photos should work."""
        paths = [f'photos/check_{i}.jpg' for i in range(50)]
        encoded = encode_photo_paths(paths)
        decoded = parse_photo_paths(encoded)
        assert decoded == paths
        assert len(decoded) == 50

    def test_path_starting_with_bracket(self):
        """Edge case: legacy single path that starts with '['."""
        # This would be interpreted as JSON — but if it's invalid JSON,
        # it falls back to single-path
        result = parse_photo_paths("[not_valid_json.jpg")
        assert result == ["[not_valid_json.jpg"]

    def test_json_string_not_array(self):
        """A JSON-encoded string (not array) should be treated as legacy."""
        result = parse_photo_paths('"photos/a.jpg"')
        # Doesn't start with [ so treated as legacy single path
        assert result == ['"photos/a.jpg"']

    def test_nested_json_array(self):
        """Nested arrays should be flattened (inner arrays treated as truthy)."""
        result = parse_photo_paths('[["photos/a.jpg"], "photos/b.jpg"]')
        # The first element is a list (truthy, not filtered), second is string
        # This is technically incorrect usage but shouldn't crash
        assert len(result) == 2


# ══════════════════════════════════════════════════════════════════
# drive.py — folder structure & filename helpers
# ══════════════════════════════════════════════════════════════════
class TestDriveFolderHelpers:
    """Tests for _sanitize_drive_name and filename generator functions."""

    def test_sanitize_basic(self):
        from fam.sync.drive import _sanitize_drive_name
        assert _sanitize_drive_name("Downtown Market") == "Downtown Market"

    def test_sanitize_slashes(self):
        from fam.sync.drive import _sanitize_drive_name
        assert _sanitize_drive_name("A/B\\C") == "A B C"

    def test_sanitize_special_chars(self):
        from fam.sync.drive import _sanitize_drive_name
        result = _sanitize_drive_name('Test:Market*Name?"<>|')
        assert '/' not in result
        assert ':' not in result
        assert '*' not in result

    def test_sanitize_truncates(self):
        from fam.sync.drive import _sanitize_drive_name
        long_name = "A" * 200
        result = _sanitize_drive_name(long_name, max_length=50)
        assert len(result) <= 50

    def test_sanitize_empty_returns_unknown(self):
        from fam.sync.drive import _sanitize_drive_name
        assert _sanitize_drive_name("") == "Unknown"
        assert _sanitize_drive_name("///") == "Unknown"

    def test_fmnp_filename_single(self):
        from fam.sync.drive import _make_fmnp_filename
        entry = {'id': 42, 'vendor_name': 'Happy Farm', 'market_day_date': '2026-03-08'}
        result = _make_fmnp_filename(entry, 0, 1, '.jpg')
        assert result == 'FMNP_42_Happy Farm_20260308.jpg'

    def test_fmnp_filename_multi(self):
        from fam.sync.drive import _make_fmnp_filename
        entry = {'id': 42, 'vendor_name': 'Happy Farm', 'market_day_date': '2026-03-08'}
        r1 = _make_fmnp_filename(entry, 0, 3, '.jpg')
        r2 = _make_fmnp_filename(entry, 1, 3, '.jpg')
        r3 = _make_fmnp_filename(entry, 2, 3, '.jpg')
        assert '_1.jpg' in r1
        assert '_2.jpg' in r2
        assert '_3.jpg' in r3

    def test_payment_filename_single(self):
        from fam.sync.drive import _make_payment_filename
        entry = {
            'id': 7, 'fam_transaction_id': 'FAM-DT-0c2a-20260308-0003',
            'vendor_name': 'Happy Farm', 'method_name_snapshot': 'SNAP',
        }
        result = _make_payment_filename(entry, 0, 1, '.jpg')
        assert 'FAM-DT-0c2a-20260308-0003' in result
        assert 'Happy Farm' in result
        assert 'SNAP' in result
        assert result.endswith('.jpg')

    def test_payment_filename_multi(self):
        from fam.sync.drive import _make_payment_filename
        entry = {
            'id': 7, 'fam_transaction_id': 'FAM-001',
            'vendor_name': 'Farm', 'method_name_snapshot': 'FMNP',
        }
        r1 = _make_payment_filename(entry, 0, 2, '.png')
        r2 = _make_payment_filename(entry, 1, 2, '.png')
        assert '_1.png' in r1
        assert '_2.png' in r2

    def test_payment_filename_missing_txn_id(self):
        from fam.sync.drive import _make_payment_filename
        entry = {'id': 99, 'vendor_name': 'Farm', 'method_name_snapshot': 'Cash'}
        result = _make_payment_filename(entry, 0, 1, '.jpg')
        assert 'PLI_99' in result


# ══════════════════════════════════════════════════════════════════
# drive.py — _extract_file_id helper
# ══════════════════════════════════════════════════════════════════
class TestExtractFileId:
    """Tests for _extract_file_id URL parsing."""

    def test_standard_url(self):
        from fam.sync.drive import _extract_file_id
        url = 'https://drive.google.com/file/d/1aBcDeFgHiJkLmNo/view'
        assert _extract_file_id(url) == '1aBcDeFgHiJkLmNo'

    def test_url_with_query_params(self):
        from fam.sync.drive import _extract_file_id
        url = 'https://drive.google.com/file/d/ABC123/view?usp=sharing'
        assert _extract_file_id(url) == 'ABC123'

    def test_none_url(self):
        from fam.sync.drive import _extract_file_id
        assert _extract_file_id(None) is None

    def test_empty_url(self):
        from fam.sync.drive import _extract_file_id
        assert _extract_file_id('') is None

    def test_invalid_url(self):
        from fam.sync.drive import _extract_file_id
        assert _extract_file_id('https://example.com/not-a-drive-url') is None


# ══════════════════════════════════════════════════════════════════
# Model query functions for Drive verification & VOID rename
# ══════════════════════════════════════════════════════════════════
class TestDriveModelQueries:
    """Tests for model functions used by verification and VOID rename."""

    def test_get_fmnp_entries_with_drive_urls(self, fresh_db):
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import (create_fmnp_entry, update_fmnp_photo_drive_url,
                                      get_fmnp_entries_with_drive_urls)

        # Entry with drive URL
        e1 = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')
        update_fmnp_photo_drive_url(e1, 'https://drive.google.com/file/d/1/view')

        # Entry without drive URL (pending)
        create_fmnp_entry(1, 1, 5000, 'Bob', photo_path='photos/b.jpg')

        results = get_fmnp_entries_with_drive_urls()
        assert len(results) == 1
        assert results[0]['id'] == e1

    def test_get_deleted_fmnp_with_photos(self, fresh_db):
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import (create_fmnp_entry, update_fmnp_photo_drive_url,
                                      delete_fmnp_entry, get_deleted_fmnp_with_photos)

        e1 = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')
        update_fmnp_photo_drive_url(e1, 'https://drive.google.com/file/d/1/view')
        delete_fmnp_entry(e1)

        # Active entry with URL — should NOT appear
        e2 = create_fmnp_entry(1, 1, 5000, 'Bob', photo_path='photos/b.jpg')
        update_fmnp_photo_drive_url(e2, 'https://drive.google.com/file/d/2/view')

        results = get_deleted_fmnp_with_photos()
        assert len(results) == 1
        assert results[0]['id'] == e1

    def test_get_payment_items_with_drive_urls(self, fresh_db):
        _seed_fmnp(fresh_db)
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
            " VALUES (2, 'Cash', 0.0, 1, 2)")
        fresh_db.execute(
            "INSERT INTO customer_orders (id, market_day_id, customer_label)"
            " VALUES (1, 1, 'C-001')")
        fresh_db.execute(
            "INSERT INTO transactions (id, market_day_id, vendor_id, receipt_total,"
            " fam_transaction_id, customer_order_id, status)"
            " VALUES (1, 1, 1, 5000, 'FAM-001', 1, 'Confirmed')")
        fresh_db.commit()

        from fam.models.transaction import (save_payment_line_items,
                                             get_payment_items_with_drive_urls,
                                             update_payment_photo_drive_url)
        items = [{
            'payment_method_id': 1, 'method_name_snapshot': 'FMNP',
            'match_percent_snapshot': 100.0, 'method_amount': 5000,
            'match_amount': 2500, 'customer_charged': 2500,
            'photo_path': 'photos/p.jpg',
        }]
        save_payment_line_items(1, items)
        # Get the line item id
        row = fresh_db.execute("SELECT id FROM payment_line_items LIMIT 1").fetchone()
        update_payment_photo_drive_url(row['id'], 'https://drive.google.com/file/d/X/view')

        results = get_payment_items_with_drive_urls()
        assert len(results) == 1

    def test_get_voided_payment_photos(self, fresh_db):
        _seed_fmnp(fresh_db)
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
            " VALUES (2, 'Cash', 0.0, 1, 2)")
        fresh_db.execute(
            "INSERT INTO customer_orders (id, market_day_id, customer_label)"
            " VALUES (1, 1, 'C-001')")
        fresh_db.execute(
            "INSERT INTO transactions (id, market_day_id, vendor_id, receipt_total,"
            " fam_transaction_id, customer_order_id, status)"
            " VALUES (1, 1, 1, 5000, 'FAM-001', 1, 'Voided')")
        fresh_db.commit()

        from fam.models.transaction import (save_payment_line_items,
                                             get_voided_payment_photos,
                                             update_payment_photo_drive_url)
        items = [{
            'payment_method_id': 1, 'method_name_snapshot': 'FMNP',
            'match_percent_snapshot': 100.0, 'method_amount': 5000,
            'match_amount': 2500, 'customer_charged': 2500,
            'photo_path': 'photos/p.jpg',
        }]
        save_payment_line_items(1, items)
        row = fresh_db.execute("SELECT id FROM payment_line_items LIMIT 1").fetchone()
        update_payment_photo_drive_url(row['id'], 'https://drive.google.com/file/d/Y/view')

        results = get_voided_payment_photos()
        assert len(results) == 1

    def test_voided_excluded_from_active_query(self, fresh_db):
        """Voided transaction items should NOT appear in active drive URL query."""
        _seed_fmnp(fresh_db)
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
            " VALUES (2, 'Cash', 0.0, 1, 2)")
        fresh_db.execute(
            "INSERT INTO customer_orders (id, market_day_id, customer_label)"
            " VALUES (1, 1, 'C-001')")
        fresh_db.execute(
            "INSERT INTO transactions (id, market_day_id, vendor_id, receipt_total,"
            " fam_transaction_id, customer_order_id, status)"
            " VALUES (1, 1, 1, 5000, 'FAM-001', 1, 'Voided')")
        fresh_db.commit()

        from fam.models.transaction import (save_payment_line_items,
                                             get_payment_items_with_drive_urls,
                                             update_payment_photo_drive_url)
        items = [{
            'payment_method_id': 1, 'method_name_snapshot': 'FMNP',
            'match_percent_snapshot': 100.0, 'method_amount': 5000,
            'match_amount': 2500, 'customer_charged': 2500,
            'photo_path': 'photos/p.jpg',
        }]
        save_payment_line_items(1, items)
        row = fresh_db.execute("SELECT id FROM payment_line_items LIMIT 1").fetchone()
        update_payment_photo_drive_url(row['id'], 'https://drive.google.com/file/d/Y/view')

        # Should be excluded from active query
        results = get_payment_items_with_drive_urls()
        assert len(results) == 0


# ══════════════════════════════════════════════════════════════════
# drive.py — verify_and_clear_dead_urls
# ══════════════════════════════════════════════════════════════════
class TestVerifyAndClearDeadUrls:
    """Tests for _verify_and_clear_dead_urls."""

    def test_clears_dead_fmnp_url(self, fresh_db):
        """Dead Drive URL should be cleared so photo gets re-uploaded."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import (create_fmnp_entry, update_fmnp_photo_drive_url,
                                      get_fmnp_entry_by_id)
        from fam.sync.drive import _verify_and_clear_dead_urls

        e1 = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')
        update_fmnp_photo_drive_url(e1, 'https://drive.google.com/file/d/DEAD/view')

        with patch('fam.sync.drive._verify_file_in_drive', return_value=False):
            cleared = _verify_and_clear_dead_urls('fake_session')

        assert cleared == 1
        entry = get_fmnp_entry_by_id(e1)
        assert entry['photo_drive_url'] is None

    def test_keeps_live_fmnp_url(self, fresh_db):
        """Live Drive URL should not be cleared."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import (create_fmnp_entry, update_fmnp_photo_drive_url,
                                      get_fmnp_entry_by_id)
        from fam.sync.drive import _verify_and_clear_dead_urls

        e1 = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')
        url = 'https://drive.google.com/file/d/LIVE/view'
        update_fmnp_photo_drive_url(e1, url)

        with patch('fam.sync.drive._verify_file_in_drive', return_value=True):
            cleared = _verify_and_clear_dead_urls('fake_session')

        assert cleared == 0
        entry = get_fmnp_entry_by_id(e1)
        assert entry['photo_drive_url'] == url

    def test_partial_clear_multi_url(self, fresh_db):
        """With multiple URLs, only dead ones should be cleared."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import (create_fmnp_entry, update_fmnp_photo_drive_url,
                                      get_fmnp_entry_by_id)
        from fam.sync.drive import _verify_and_clear_dead_urls

        paths = encode_photo_paths(['photos/a.jpg', 'photos/b.jpg'])
        e1 = create_fmnp_entry(1, 1, 5000, 'Alice', photo_path=paths)
        urls = encode_photo_paths([
            'https://drive.google.com/file/d/LIVE1/view',
            'https://drive.google.com/file/d/DEAD1/view',
        ])
        update_fmnp_photo_drive_url(e1, urls)

        def mock_verify(session, file_id):
            return file_id == 'LIVE1'

        with patch('fam.sync.drive._verify_file_in_drive', side_effect=mock_verify):
            cleared = _verify_and_clear_dead_urls('fake_session')

        assert cleared == 1
        entry = get_fmnp_entry_by_id(e1)
        remaining = parse_photo_paths(entry['photo_drive_url'])
        assert len(remaining) == 1
        assert 'LIVE1' in remaining[0]


# ══════════════════════════════════════════════════════════════════
# drive.py — process_voided_photos (VOID rename)
# ══════════════════════════════════════════════════════════════════
class TestProcessVoidedPhotos:
    """Tests for _process_voided_photos."""

    def test_renames_deleted_fmnp_photo(self, fresh_db):
        """Deleted FMNP entry photo should be renamed to VOID_ prefix."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import (create_fmnp_entry, update_fmnp_photo_drive_url,
                                      delete_fmnp_entry)
        from fam.sync.drive import _process_voided_photos

        e1 = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')
        update_fmnp_photo_drive_url(e1, 'https://drive.google.com/file/d/F1/view')
        delete_fmnp_entry(e1)

        with patch('fam.sync.drive._get_file_name_in_drive', return_value='FMNP_1_Alice_20260310.jpg'), \
             patch('fam.sync.drive._rename_file_in_drive', return_value=True) as mock_rename:
            renamed = _process_voided_photos('fake_session')

        assert renamed == 1
        mock_rename.assert_called_once_with('fake_session', 'F1', 'VOID_FMNP_1_Alice_20260310.jpg')

    def test_skips_already_voided_filename(self, fresh_db):
        """File already named VOID_ should not be renamed again."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import (create_fmnp_entry, update_fmnp_photo_drive_url,
                                      delete_fmnp_entry)
        from fam.sync.drive import _process_voided_photos

        e1 = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')
        update_fmnp_photo_drive_url(e1, 'https://drive.google.com/file/d/F1/view')
        delete_fmnp_entry(e1)

        with patch('fam.sync.drive._get_file_name_in_drive', return_value='VOID_already.jpg'), \
             patch('fam.sync.drive._rename_file_in_drive') as mock_rename:
            renamed = _process_voided_photos('fake_session')

        assert renamed == 0
        mock_rename.assert_not_called()

    def test_renames_voided_transaction_photo(self, fresh_db):
        """Voided transaction payment photo should be renamed."""
        _seed_fmnp(fresh_db)
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
            " VALUES (2, 'Cash', 0.0, 1, 2)")
        fresh_db.execute(
            "INSERT INTO customer_orders (id, market_day_id, customer_label)"
            " VALUES (1, 1, 'C-001')")
        fresh_db.execute(
            "INSERT INTO transactions (id, market_day_id, vendor_id, receipt_total,"
            " fam_transaction_id, customer_order_id, status)"
            " VALUES (1, 1, 1, 5000, 'FAM-001', 1, 'Voided')")
        fresh_db.commit()

        from fam.models.transaction import (save_payment_line_items,
                                             update_payment_photo_drive_url)
        from fam.sync.drive import _process_voided_photos

        items = [{
            'payment_method_id': 1, 'method_name_snapshot': 'FMNP',
            'match_percent_snapshot': 100.0, 'method_amount': 5000,
            'match_amount': 2500, 'customer_charged': 2500,
            'photo_path': 'photos/p.jpg',
        }]
        save_payment_line_items(1, items)
        row = fresh_db.execute("SELECT id FROM payment_line_items LIMIT 1").fetchone()
        update_payment_photo_drive_url(row['id'], 'https://drive.google.com/file/d/PF1/view')

        with patch('fam.sync.drive._get_file_name_in_drive', return_value='pay_photo.jpg'), \
             patch('fam.sync.drive._rename_file_in_drive', return_value=True) as mock_rename:
            renamed = _process_voided_photos('fake_session')

        assert renamed == 1
        mock_rename.assert_called_once_with('fake_session', 'PF1', 'VOID_pay_photo.jpg')

    def test_handles_deleted_drive_file_gracefully(self, fresh_db):
        """If file is already deleted from Drive, skip without error."""
        _seed_fmnp(fresh_db)
        from fam.models.fmnp import (create_fmnp_entry, update_fmnp_photo_drive_url,
                                      delete_fmnp_entry)
        from fam.sync.drive import _process_voided_photos

        e1 = create_fmnp_entry(1, 1, 2500, 'Alice', photo_path='photos/a.jpg')
        update_fmnp_photo_drive_url(e1, 'https://drive.google.com/file/d/GONE/view')
        delete_fmnp_entry(e1)

        with patch('fam.sync.drive._get_file_name_in_drive', return_value=None), \
             patch('fam.sync.drive._rename_file_in_drive') as mock_rename:
            renamed = _process_voided_photos('fake_session')

        assert renamed == 0
        mock_rename.assert_not_called()
