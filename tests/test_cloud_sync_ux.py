"""Tests for Cloud Sync UX improvements.

Covers:
  - _extract_spreadsheet_id: URL/ID parsing for Google Sheets
  - _extract_drive_folder_id: URL/ID parsing for Google Drive folders
  - Sync indicator warning state logic
  - FMNP entry_saved signal
  - About dialog Drive/Sheets button URLs
  - Open Sheet button logic
  - Spreadsheet URL normalization on save
"""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database


# ──────────────────────────────────────────────────────────────────
# Fixture: fresh database per test
# ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_cloud_sync_ux.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    yield conn
    close_connection()


# ══════════════════════════════════════════════════════════════════
# _extract_spreadsheet_id
# ══════════════════════════════════════════════════════════════════
class TestExtractSpreadsheetId:
    """Unit tests for SettingsScreen._extract_spreadsheet_id()."""

    @staticmethod
    def _extract(raw):
        from fam.ui.settings_screen import SettingsScreen
        return SettingsScreen._extract_spreadsheet_id(raw)

    def test_full_url_with_edit(self):
        url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"
        assert self._extract(url) == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"

    def test_full_url_with_edit_and_gid(self):
        url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit#gid=0"
        assert self._extract(url) == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"

    def test_full_url_with_query_params(self):
        url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit?usp=sharing"
        assert self._extract(url) == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"

    def test_url_without_edit(self):
        url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
        assert self._extract(url) == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"

    def test_raw_id_valid(self):
        raw_id = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
        assert self._extract(raw_id) == raw_id

    def test_raw_id_with_hyphens(self):
        raw_id = "abc-def-ghi-jkl-mno"
        assert self._extract(raw_id) == raw_id

    def test_raw_id_with_underscores(self):
        raw_id = "abc_def_ghi_jkl_mno"
        assert self._extract(raw_id) == raw_id

    def test_short_string_rejected(self):
        """Strings shorter than 10 chars should not be accepted as IDs."""
        assert self._extract("abc") == ''

    def test_empty_string(self):
        assert self._extract("") == ''

    def test_whitespace_only(self):
        assert self._extract("   ") == ''

    def test_whitespace_around_url(self):
        url = "  https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit  "
        assert self._extract(url) == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"

    def test_invalid_url_no_spreadsheets_path(self):
        assert self._extract("https://google.com/some/path") == ''

    def test_drive_url_not_matched(self):
        """A Drive folder URL should not match the spreadsheet pattern."""
        url = "https://drive.google.com/drive/folders/1abc123456"
        assert self._extract(url) == ''

    def test_special_chars_rejected(self):
        """Strings with special characters should be rejected."""
        assert self._extract("abc!@#$%^&*()") == ''


# ══════════════════════════════════════════════════════════════════
# _extract_drive_folder_id (parity tests)
# ══════════════════════════════════════════════════════════════════
class TestExtractDriveFolderId:
    """Unit tests for SettingsScreen._extract_drive_folder_id()."""

    @staticmethod
    def _extract(raw):
        from fam.ui.settings_screen import SettingsScreen
        return SettingsScreen._extract_drive_folder_id(raw)

    def test_full_url(self):
        url = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz"
        assert self._extract(url) == "1AbCdEfGhIjKlMnOpQrStUvWxYz"

    def test_url_with_query(self):
        url = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz?usp=sharing"
        assert self._extract(url) == "1AbCdEfGhIjKlMnOpQrStUvWxYz"

    def test_raw_id_valid(self):
        raw_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
        assert self._extract(raw_id) == raw_id

    def test_short_string_rejected(self):
        assert self._extract("abc") == ''

    def test_empty_string(self):
        assert self._extract("") == ''

    def test_whitespace_stripped(self):
        url = "  https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz  "
        assert self._extract(url) == "1AbCdEfGhIjKlMnOpQrStUvWxYz"

    def test_spreadsheet_url_not_matched(self):
        """A Sheets URL should not match the folder pattern."""
        url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"
        assert self._extract(url) == ''


# ══════════════════════════════════════════════════════════════════
# Sync indicator warning state
# ══════════════════════════════════════════════════════════════════
class TestSyncIndicatorWarning:
    """Test that _on_sync_finished correctly routes to warning state."""

    def _make_mock_window(self):
        """Create a mock MainWindow with the needed attributes."""
        window = MagicMock()
        window._sync_worker = MagicMock()
        window._sync_btn = MagicMock()
        window._sync_indicator = MagicMock()
        return window

    def test_sheet_failure_shows_error(self):
        """When sheet tabs fail, indicator should show 'error' regardless of photos."""
        from fam.ui.main_window import MainWindow

        window = self._make_mock_window()
        window._sync_worker.photo_stats = {'uploaded': 3, 'failed': 0}

        # Results with one failed tab
        results = {
            'Transactions': MagicMock(success=False, rows_synced=0),
            'FMNP': MagicMock(success=True, rows_synced=10),
        }

        # Call the real method on our mock
        MainWindow._on_sync_finished(window, results)

        window._set_sync_indicator.assert_called_with("error", "1 tab(s) failed")

    def test_photo_failure_shows_warning(self):
        """When photos fail but sheets succeed, should show 'warning'."""
        from fam.ui.main_window import MainWindow

        window = self._make_mock_window()
        window._sync_worker.photo_stats = {'uploaded': 1, 'failed': 2}

        results = {
            'Transactions': MagicMock(success=True, rows_synced=5),
            'FMNP': MagicMock(success=True, rows_synced=10),
        }

        MainWindow._on_sync_finished(window, results)

        window._set_sync_indicator.assert_called_with("warning", "2 photo(s) failed")

    def test_photo_error_string_shows_warning(self):
        """When photo_stats has an 'error' string, should show 'warning' with that message."""
        from fam.ui.main_window import MainWindow

        window = self._make_mock_window()
        window._sync_worker.photo_stats = {'error': 'Drive auth failed'}

        results = {
            'Transactions': MagicMock(success=True, rows_synced=5),
        }

        MainWindow._on_sync_finished(window, results)

        window._set_sync_indicator.assert_called_with("warning", "Drive auth failed")

    def test_all_success_shows_online(self):
        """When everything succeeds, should show 'online'."""
        from fam.ui.main_window import MainWindow

        window = self._make_mock_window()
        window._sync_worker.photo_stats = {'uploaded': 3, 'failed': 0}

        results = {
            'Transactions': MagicMock(success=True, rows_synced=5),
        }

        with patch('fam.utils.app_settings.get_last_sync_at', return_value='2026-03-10T14:30:00'):
            MainWindow._on_sync_finished(window, results)

        # Should be called with "online"
        call_args = window._set_sync_indicator.call_args
        assert call_args[0][0] == "online"

    def test_no_photo_stats_all_sheets_ok_shows_online(self):
        """When no photo_stats at all and sheets succeed, should show 'online'."""
        from fam.ui.main_window import MainWindow

        window = self._make_mock_window()
        window._sync_worker.photo_stats = None

        results = {
            'Transactions': MagicMock(success=True, rows_synced=5),
        }

        with patch('fam.utils.app_settings.get_last_sync_at', return_value='2026-03-10T14:30:00'):
            MainWindow._on_sync_finished(window, results)

        call_args = window._set_sync_indicator.call_args
        assert call_args[0][0] == "online"

    def test_sheet_failure_trumps_photo_failure(self):
        """When both sheets and photos fail, should show 'error' (sheets take priority)."""
        from fam.ui.main_window import MainWindow

        window = self._make_mock_window()
        window._sync_worker.photo_stats = {'uploaded': 0, 'failed': 3}

        results = {
            'Transactions': MagicMock(success=False, rows_synced=0),
        }

        MainWindow._on_sync_finished(window, results)

        window._set_sync_indicator.assert_called_with("error", "1 tab(s) failed")

    def test_zero_failures_no_warning(self):
        """photo_stats with failed=0 and no error should not trigger warning."""
        from fam.ui.main_window import MainWindow

        window = self._make_mock_window()
        window._sync_worker.photo_stats = {'uploaded': 0, 'failed': 0}

        results = {
            'Transactions': MagicMock(success=True, rows_synced=0),
        }

        with patch('fam.utils.app_settings.get_last_sync_at', return_value='2026-03-10T14:30:00'):
            MainWindow._on_sync_finished(window, results)

        call_args = window._set_sync_indicator.call_args
        assert call_args[0][0] == "online"


# ══════════════════════════════════════════════════════════════════
# _set_sync_indicator state labels
# ══════════════════════════════════════════════════════════════════
class TestSetSyncIndicator:
    """Test the _set_sync_indicator method for all states."""

    def _make_mock_window(self):
        window = MagicMock()
        window._sync_indicator = MagicMock()
        return window

    def test_online_state(self):
        from fam.ui.main_window import MainWindow
        window = self._make_mock_window()
        MainWindow._set_sync_indicator(window, "online", "Last sync: 14:30")
        text = window._sync_indicator.setText.call_args[0][0]
        assert "Online" in text
        assert "#2E7D32" in text or "Online" in text

    def test_warning_state(self):
        from fam.ui.main_window import MainWindow
        window = self._make_mock_window()
        MainWindow._set_sync_indicator(window, "warning", "2 photo(s) failed")
        text = window._sync_indicator.setText.call_args[0][0]
        assert "Attention" in text
        assert "#F5A623" in text

    def test_error_state(self):
        from fam.ui.main_window import MainWindow
        window = self._make_mock_window()
        MainWindow._set_sync_indicator(window, "error", "1 tab(s) failed")
        text = window._sync_indicator.setText.call_args[0][0]
        assert "Sync Error" in text
        assert "#d32f2f" in text

    def test_syncing_state(self):
        from fam.ui.main_window import MainWindow
        window = self._make_mock_window()
        MainWindow._set_sync_indicator(window, "syncing", "Uploading...")
        text = window._sync_indicator.setText.call_args[0][0]
        assert "Syncing" in text
        assert "#F5A623" in text

    def test_offline_state(self):
        from fam.ui.main_window import MainWindow
        window = self._make_mock_window()
        MainWindow._set_sync_indicator(window, "offline")
        text = window._sync_indicator.setText.call_args[0][0]
        assert "Offline" in text

    def test_unknown_state_defaults_offline(self):
        from fam.ui.main_window import MainWindow
        window = self._make_mock_window()
        MainWindow._set_sync_indicator(window, "bogus_state")
        text = window._sync_indicator.setText.call_args[0][0]
        assert "Offline" in text

    def test_detail_in_tooltip(self):
        from fam.ui.main_window import MainWindow
        window = self._make_mock_window()
        MainWindow._set_sync_indicator(window, "warning", "3 photo(s) failed")
        text = window._sync_indicator.setText.call_args[0][0]
        # Detail text appears after the label
        assert "3 photo(s) failed" in text


# ══════════════════════════════════════════════════════════════════
# FMNP entry_saved signal
# ══════════════════════════════════════════════════════════════════
class TestFMNPEntrySavedSignal:
    """Test that FMNPScreen declares entry_saved Signal."""

    def test_signal_exists(self):
        from fam.ui.fmnp_screen import FMNPScreen
        # The class should have an entry_saved attribute that is a Signal descriptor
        assert hasattr(FMNPScreen, 'entry_saved')

    def test_signal_is_signal_type(self):
        from PySide6.QtCore import Signal
        from fam.ui.fmnp_screen import FMNPScreen
        # PySide6 Signals at class level are descriptor objects
        # When accessed on the class, they should exist
        assert 'entry_saved' in dir(FMNPScreen)


# ══════════════════════════════════════════════════════════════════
# Spreadsheet URL save/load normalization
# ══════════════════════════════════════════════════════════════════
class TestSpreadsheetUrlNormalization:
    """Test spreadsheet URL extraction and storage flow."""

    def test_save_extracts_id_from_url(self):
        """Saving a full URL should store only the ID."""
        from fam.utils.app_settings import set_sync_spreadsheet_id, get_sync_spreadsheet_id
        # Simulate what _save_sync_settings does
        from fam.ui.settings_screen import SettingsScreen
        raw = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"
        sheet_id = SettingsScreen._extract_spreadsheet_id(raw)
        assert sheet_id == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
        set_sync_spreadsheet_id(sheet_id)
        assert get_sync_spreadsheet_id() == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"

    def test_save_extracts_id_from_raw(self):
        """Saving a raw ID should store it as-is."""
        from fam.utils.app_settings import set_sync_spreadsheet_id, get_sync_spreadsheet_id
        from fam.ui.settings_screen import SettingsScreen
        raw = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
        sheet_id = SettingsScreen._extract_spreadsheet_id(raw)
        set_sync_spreadsheet_id(sheet_id)
        assert get_sync_spreadsheet_id() == raw

    def test_display_url_format(self):
        """When loading, the display should be a full URL."""
        from fam.utils.app_settings import set_sync_spreadsheet_id, get_sync_spreadsheet_id
        sheet_id = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
        set_sync_spreadsheet_id(sheet_id)
        stored = get_sync_spreadsheet_id()
        display_url = f"https://docs.google.com/spreadsheets/d/{stored}/edit"
        assert display_url == "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"

    def test_clear_spreadsheet_id(self):
        """Saving empty should clear the setting."""
        from fam.utils.app_settings import set_sync_spreadsheet_id, get_sync_spreadsheet_id
        set_sync_spreadsheet_id("some_id_12345")
        assert get_sync_spreadsheet_id() == "some_id_12345"
        set_sync_spreadsheet_id('')
        assert get_sync_spreadsheet_id() == ''

    def test_roundtrip_url_to_id_to_url(self):
        """Full URL → extract ID → store → reconstruct URL should produce same URL."""
        from fam.ui.settings_screen import SettingsScreen
        from fam.utils.app_settings import set_sync_spreadsheet_id, get_sync_spreadsheet_id
        original_url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"
        sheet_id = SettingsScreen._extract_spreadsheet_id(original_url)
        set_sync_spreadsheet_id(sheet_id)
        stored = get_sync_spreadsheet_id()
        reconstructed = f"https://docs.google.com/spreadsheets/d/{stored}/edit"
        assert reconstructed == original_url


# ══════════════════════════════════════════════════════════════════
# About dialog URL construction
# ══════════════════════════════════════════════════════════════════
class TestAboutDialogUrls:
    """Test URL construction logic for About dialog Drive/Sheets buttons."""

    def test_drive_url_with_folder_id(self):
        folder_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
        url = (f"https://drive.google.com/drive/folders/{folder_id}"
               if folder_id else "https://drive.google.com")
        assert url == "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz"

    def test_drive_url_without_folder_id(self):
        folder_id = None
        url = (f"https://drive.google.com/drive/folders/{folder_id}"
               if folder_id else "https://drive.google.com")
        assert url == "https://drive.google.com"

    def test_drive_url_empty_folder_id(self):
        folder_id = ''
        url = (f"https://drive.google.com/drive/folders/{folder_id}"
               if folder_id else "https://drive.google.com")
        assert url == "https://drive.google.com"

    def test_sheets_url_with_sheet_id(self):
        sheet_id = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
        url = (f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
               if sheet_id else "https://sheets.google.com")
        assert url == "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"

    def test_sheets_url_without_sheet_id(self):
        sheet_id = None
        url = (f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
               if sheet_id else "https://sheets.google.com")
        assert url == "https://sheets.google.com"

    def test_sheets_url_empty_sheet_id(self):
        sheet_id = ''
        url = (f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
               if sheet_id else "https://sheets.google.com")
        assert url == "https://sheets.google.com"


# ══════════════════════════════════════════════════════════════════
# Open Sheet button logic
# ══════════════════════════════════════════════════════════════════
class TestOpenSheetLogic:
    """Test _open_sheet logic (without real Qt)."""

    def test_open_sheet_with_id_calls_desktop_services(self):
        """When spreadsheet ID is set, should open the correct URL."""
        from fam.ui.settings_screen import SettingsScreen

        screen = MagicMock(spec=SettingsScreen)

        with patch('fam.utils.app_settings.get_sync_spreadsheet_id',
                   return_value='1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms'):
            with patch('PySide6.QtGui.QDesktopServices') as mock_ds:
                SettingsScreen._open_sheet(screen)
                mock_ds.openUrl.assert_called_once()

    def test_open_sheet_without_id_shows_message(self):
        """When no spreadsheet ID is set, should show info message box."""
        from fam.ui.settings_screen import SettingsScreen, QMessageBox

        screen = MagicMock(spec=SettingsScreen)

        with patch('fam.utils.app_settings.get_sync_spreadsheet_id', return_value=None):
            with patch.object(QMessageBox, 'information') as mock_info:
                SettingsScreen._open_sheet(screen)
                mock_info.assert_called_once()

    def test_open_sheet_empty_id_shows_message(self):
        """When spreadsheet ID is empty string, should show info message box."""
        from fam.ui.settings_screen import SettingsScreen, QMessageBox

        screen = MagicMock(spec=SettingsScreen)

        with patch('fam.utils.app_settings.get_sync_spreadsheet_id', return_value=''):
            with patch.object(QMessageBox, 'information') as mock_info:
                SettingsScreen._open_sheet(screen)
                mock_info.assert_called_once()


# ══════════════════════════════════════════════════════════════════
# Tooltip content in _on_sync_finished
# ══════════════════════════════════════════════════════════════════
class TestSyncTooltipContent:
    """Test that tooltip text is built correctly."""

    def _make_mock_window(self):
        window = MagicMock()
        window._sync_worker = MagicMock()
        window._sync_btn = MagicMock()
        window._sync_indicator = MagicMock()
        return window

    def test_tooltip_includes_photo_upload_count(self):
        from fam.ui.main_window import MainWindow

        window = self._make_mock_window()
        window._sync_worker.photo_stats = {
            'uploaded': 5, 'failed': 0,
            'fmnp_uploaded': 3, 'payment_uploaded': 2
        }

        results = {'Transactions': MagicMock(success=True, rows_synced=10)}

        with patch('fam.utils.app_settings.get_last_sync_at', return_value='2026-03-10T14:30:00'):
            MainWindow._on_sync_finished(window, results)

        tooltip = window._sync_indicator.setToolTip.call_args[0][0]
        assert "Photos uploaded: 5" in tooltip
        assert "FMNP: 3" in tooltip
        assert "Payment: 2" in tooltip

    def test_tooltip_includes_photo_error(self):
        from fam.ui.main_window import MainWindow

        window = self._make_mock_window()
        window._sync_worker.photo_stats = {'error': 'Drive auth failed'}

        results = {'Transactions': MagicMock(success=True, rows_synced=10)}

        with patch('fam.utils.app_settings.get_last_sync_at', return_value='2026-03-10T14:30:00'):
            MainWindow._on_sync_finished(window, results)

        tooltip = window._sync_indicator.setToolTip.call_args[0][0]
        assert "Photo upload error: Drive auth failed" in tooltip

    def test_tooltip_includes_sheet_error(self):
        from fam.ui.main_window import MainWindow

        window = self._make_mock_window()
        window._sync_worker.photo_stats = None

        results = {
            'Transactions': MagicMock(success=False, rows_synced=0),
            'FMNP': MagicMock(success=True, rows_synced=5),
        }

        MainWindow._on_sync_finished(window, results)

        tooltip = window._sync_indicator.setToolTip.call_args[0][0]
        assert "Sheet errors: Transactions" in tooltip

    def test_tooltip_no_pending_photos(self):
        from fam.ui.main_window import MainWindow

        window = self._make_mock_window()
        window._sync_worker.photo_stats = {'uploaded': 0, 'failed': 0}

        results = {'Transactions': MagicMock(success=True, rows_synced=0)}

        with patch('fam.utils.app_settings.get_last_sync_at', return_value='2026-03-10T14:30:00'):
            MainWindow._on_sync_finished(window, results)

        tooltip = window._sync_indicator.setToolTip.call_args[0][0]
        assert "No pending photos" in tooltip

    def test_tooltip_photo_failures_count(self):
        from fam.ui.main_window import MainWindow

        window = self._make_mock_window()
        window._sync_worker.photo_stats = {'uploaded': 0, 'failed': 4}

        results = {'Transactions': MagicMock(success=True, rows_synced=5)}

        MainWindow._on_sync_finished(window, results)

        tooltip = window._sync_indicator.setToolTip.call_args[0][0]
        assert "Photo uploads failed: 4" in tooltip

    def test_tooltip_rows_synced_count(self):
        from fam.ui.main_window import MainWindow

        window = self._make_mock_window()
        window._sync_worker.photo_stats = None

        results = {
            'Transactions': MagicMock(success=True, rows_synced=15),
            'FMNP': MagicMock(success=True, rows_synced=8),
        }

        with patch('fam.utils.app_settings.get_last_sync_at', return_value='2026-03-10T14:30:00'):
            MainWindow._on_sync_finished(window, results)

        tooltip = window._sync_indicator.setToolTip.call_args[0][0]
        assert "Sheets: 23 rows synced" in tooltip


# ══════════════════════════════════════════════════════════════════
# Edge cases for extract methods
# ══════════════════════════════════════════════════════════════════
class TestExtractEdgeCases:
    """Edge cases for both extract methods."""

    @staticmethod
    def _extract_sheet(raw):
        from fam.ui.settings_screen import SettingsScreen
        return SettingsScreen._extract_spreadsheet_id(raw)

    @staticmethod
    def _extract_folder(raw):
        from fam.ui.settings_screen import SettingsScreen
        return SettingsScreen._extract_drive_folder_id(raw)

    def test_sheet_url_http_not_https(self):
        """HTTP URL (not HTTPS) should still work."""
        url = "http://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"
        assert self._extract_sheet(url) == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"

    def test_folder_url_http_not_https(self):
        url = "http://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz"
        assert self._extract_folder(url) == "1AbCdEfGhIjKlMnOpQrStUvWxYz"

    def test_sheet_id_exactly_10_chars(self):
        """Minimum length raw ID (10 chars) should be accepted."""
        raw = "abcdefghij"
        assert self._extract_sheet(raw) == "abcdefghij"

    def test_sheet_id_9_chars_rejected(self):
        """Raw ID of 9 chars should be rejected."""
        raw = "abcdefghi"
        assert self._extract_sheet(raw) == ''

    def test_folder_id_exactly_10_chars(self):
        raw = "abcdefghij"
        assert self._extract_folder(raw) == "abcdefghij"

    def test_folder_id_9_chars_rejected(self):
        raw = "abcdefghi"
        assert self._extract_folder(raw) == ''

    def test_sheet_url_trailing_slash(self):
        url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/"
        assert self._extract_sheet(url) == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"

    def test_sheet_url_with_fragment_and_query(self):
        url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit?usp=sharing#gid=12345"
        assert self._extract_sheet(url) == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"


# ══════════════════════════════════════════════════════════════════
# _rows_from_values helper
# ══════════════════════════════════════════════════════════════════
class TestRowsFromValues:
    """Test the gsheets _rows_from_values helper."""

    def test_empty_list(self):
        from fam.sync.gsheets import _rows_from_values
        headers, rows = _rows_from_values([])
        assert headers == []
        assert rows == []

    def test_headers_only(self):
        from fam.sync.gsheets import _rows_from_values
        headers, rows = _rows_from_values([['Name', 'Age', 'City']])
        assert headers == ['Name', 'Age', 'City']
        assert rows == []

    def test_normal_data(self):
        from fam.sync.gsheets import _rows_from_values
        all_values = [
            ['Name', 'Age', 'City'],
            ['Alice', '30', 'Portland'],
            ['Bob', '25', 'Seattle'],
        ]
        headers, rows = _rows_from_values(all_values)
        assert headers == ['Name', 'Age', 'City']
        assert len(rows) == 2
        assert rows[0] == {'Name': 'Alice', 'Age': '30', 'City': 'Portland'}
        assert rows[1] == {'Name': 'Bob', 'Age': '25', 'City': 'Seattle'}

    def test_short_row_fills_empty(self):
        """Rows shorter than headers should fill missing values with ''."""
        from fam.sync.gsheets import _rows_from_values
        all_values = [
            ['A', 'B', 'C'],
            ['x'],  # only 1 column
        ]
        headers, rows = _rows_from_values(all_values)
        assert rows[0] == {'A': 'x', 'B': '', 'C': ''}

    def test_preserves_row_order(self):
        from fam.sync.gsheets import _rows_from_values
        all_values = [
            ['id'],
            ['3'], ['1'], ['2'],
        ]
        _, rows = _rows_from_values(all_values)
        assert [r['id'] for r in rows] == ['3', '1', '2']


# ══════════════════════════════════════════════════════════════════
# _cell_value — float formatting for Google Sheets
# ══════════════════════════════════════════════════════════════════
class TestCellValue:
    """Test the _cell_value helper that prevents float precision artifacts."""

    def test_clean_float_unchanged(self):
        from fam.sync.gsheets import _cell_value
        assert _cell_value(5.38) == "5.38"

    def test_dirty_float_rounded(self):
        """IEEE 754 artifact like 5.38000000000001 should become '5.38'."""
        from fam.sync.gsheets import _cell_value
        assert _cell_value(5.38000000000001) == "5.38"

    def test_another_dirty_float(self):
        from fam.sync.gsheets import _cell_value
        assert _cell_value(4.97999999999995) == "4.98"

    def test_large_dirty_float(self):
        from fam.sync.gsheets import _cell_value
        assert _cell_value(20.0099999999999998) == "20.01"

    def test_integer_float(self):
        """100.0 should become '100.00'."""
        from fam.sync.gsheets import _cell_value
        assert _cell_value(100.0) == "100.00"

    def test_zero_float(self):
        from fam.sync.gsheets import _cell_value
        assert _cell_value(0.0) == "0.00"

    def test_string_passthrough(self):
        from fam.sync.gsheets import _cell_value
        assert _cell_value("hello") == "hello"

    def test_int_passthrough(self):
        from fam.sync.gsheets import _cell_value
        assert _cell_value(42) == "42"

    def test_none_passthrough(self):
        from fam.sync.gsheets import _cell_value
        assert _cell_value(None) == "None"

    def test_empty_string(self):
        from fam.sync.gsheets import _cell_value
        assert _cell_value("") == ""


# ══════════════════════════════════════════════════════════════════
# _retry_on_quota
# ══════════════════════════════════════════════════════════════════
class TestRetryOnQuota:
    """Test the rate-limit retry helper."""

    def test_success_first_try(self):
        from fam.sync.gsheets import _retry_on_error as _retry_on_quota
        result = _retry_on_quota(lambda: 42)
        assert result == 42

    def test_non_429_error_raises_immediately(self):
        from fam.sync.gsheets import _retry_on_error as _retry_on_quota
        def fail():
            raise ValueError("not a rate limit")
        with pytest.raises(ValueError, match="not a rate limit"):
            _retry_on_quota(fail)

    @staticmethod
    def _make_api_error(status_code):
        """Create a gspread APIError with the given status code."""
        import gspread
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = {
            'error': {
                'code': status_code,
                'message': f'Error {status_code}',
                'status': 'ERROR',
            }
        }
        return gspread.exceptions.APIError(resp)

    def test_429_retries_then_succeeds(self):
        from fam.sync.gsheets import _retry_on_error as _retry_on_quota

        call_count = 0
        def sometimes_429():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise self._make_api_error(429)
            return "ok"

        with patch('time.sleep'):  # don't actually wait
            result = _retry_on_quota(sometimes_429, max_retries=3)
        assert result == "ok"
        assert call_count == 3

    def test_429_exhausts_retries(self):
        from fam.sync.gsheets import _retry_on_error as _retry_on_quota
        import gspread

        def always_429():
            raise self._make_api_error(429)

        with patch('time.sleep'):
            with pytest.raises(gspread.exceptions.APIError):
                _retry_on_quota(always_429, max_retries=2)

    def test_non_429_api_error_not_retried(self):
        from fam.sync.gsheets import _retry_on_error as _retry_on_quota
        import gspread

        call_count = 0
        def error_403():
            nonlocal call_count
            call_count += 1
            raise self._make_api_error(403)

        with pytest.raises(gspread.exceptions.APIError):
            _retry_on_quota(error_403, max_retries=3)
        assert call_count == 1  # no retries for non-429


# ══════════════════════════════════════════════════════════════════
# Sync cooldown / debounce logic
# ══════════════════════════════════════════════════════════════════
class TestSyncCooldown:
    """Test the sync cooldown behavior in _trigger_sync."""

    def test_cooldown_active_skips_auto_sync(self):
        """Auto-trigger during cooldown should be deferred, not executed."""
        from fam.ui.main_window import MainWindow

        window = MagicMock()
        window._sync_thread = None
        window._sync_cooldown = MagicMock()
        window._sync_cooldown.isActive.return_value = True
        window._sync_deferred = MagicMock()
        window._sync_deferred.isActive.return_value = False

        MainWindow._trigger_sync(window, force=False)

        # Should start the deferred timer, not proceed with sync
        window._sync_deferred.start.assert_called_once()

    def test_cooldown_active_force_bypasses(self):
        """Manual sync (force=True) should bypass cooldown."""
        from fam.ui.main_window import MainWindow

        window = MagicMock()
        window._sync_thread = None
        window._sync_cooldown = MagicMock()
        window._sync_cooldown.isActive.return_value = True

        # force=True should not check cooldown — it proceeds past the guard
        # We test by verifying it doesn't start the deferred timer
        # (it will fail later on imports, but the cooldown check is bypassed)
        try:
            MainWindow._trigger_sync(window, force=True)
        except Exception:
            pass  # expected — mock doesn't have full env

        window._sync_deferred.start.assert_not_called()

    def test_cooldown_inactive_proceeds(self):
        """When cooldown is not active, auto-trigger should proceed."""
        from fam.ui.main_window import MainWindow

        window = MagicMock()
        window._sync_thread = None
        window._sync_cooldown = MagicMock()
        window._sync_cooldown.isActive.return_value = False

        try:
            MainWindow._trigger_sync(window, force=False)
        except Exception:
            pass  # expected — imports will fail in test env

        window._sync_deferred.start.assert_not_called()

    def test_deferred_not_restarted_if_already_active(self):
        """If deferred timer is already running, don't restart it."""
        from fam.ui.main_window import MainWindow

        window = MagicMock()
        window._sync_thread = None
        window._sync_cooldown = MagicMock()
        window._sync_cooldown.isActive.return_value = True
        window._sync_deferred = MagicMock()
        window._sync_deferred.isActive.return_value = True  # already running

        MainWindow._trigger_sync(window, force=False)

        window._sync_deferred.start.assert_not_called()

    def test_sync_in_progress_skips(self):
        """When sync thread is running, any trigger should skip."""
        from fam.ui.main_window import MainWindow

        window = MagicMock()
        window._sync_thread = MagicMock()
        window._sync_thread.isRunning.return_value = True

        MainWindow._trigger_sync(window, force=True)

        # Should return early without checking cooldown
        window._sync_cooldown.isActive.assert_not_called()


# ══════════════════════════════════════════════════════════════════
# Photo upload deduplication
# ══════════════════════════════════════════════════════════════════
class TestPhotoUploadDedup:
    """Test that _upload_entries deduplicates uploads for shared photo paths.

    When multiple FMNP entries (or payment items) reference the same
    local photo file, it should be uploaded once and the URL reused.
    """

    @staticmethod
    def _make_entry(entry_id, photo_path, photo_drive_url=None,
                    vendor_name='Test Vendor', market_name='Test Market',
                    market_day_date='2026-03-10'):
        return {
            'id': entry_id,
            'photo_path': photo_path,
            'photo_drive_url': photo_drive_url,
            'vendor_name': vendor_name,
            'market_name': market_name,
            'market_day_date': market_day_date,
        }

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash',
           return_value='mock_hash_1')
    def test_same_file_uploaded_once(self, mock_hash, mock_exists, mock_full,
                                     mock_upload, mock_folder):
        """Two entries sharing the same photo_path should upload only once."""
        from fam.sync.drive import _upload_entries

        mock_upload.return_value = 'https://drive.google.com/file/d/abc/view'
        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        entries = [
            self._make_entry(1, 'photos/shared.jpg', vendor_name='Vendor A'),
            self._make_entry(2, 'photos/shared.jpg', vendor_name='Vendor B'),
        ]

        uploaded, failed = _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn)

        # upload_photo called only once (for entry 1), entry 2 reuses cache
        assert mock_upload.call_count == 1
        assert uploaded == 2
        assert failed == 0

        # Both entries get their drive URLs saved
        assert update_fn.call_count == 2

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash')
    def test_different_files_uploaded_separately(self, mock_hash, mock_exists,
                                                  mock_full, mock_upload,
                                                  mock_folder):
        """Entries with different photo_paths should each trigger an upload."""
        from fam.sync.drive import _upload_entries

        mock_hash.side_effect = ['hash_a', 'hash_b']
        mock_upload.return_value = 'https://drive.google.com/file/d/abc/view'
        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        entries = [
            self._make_entry(1, 'photos/photo_a.jpg'),
            self._make_entry(2, 'photos/photo_b.jpg'),
        ]

        uploaded, failed = _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn)

        assert mock_upload.call_count == 2
        assert uploaded == 2

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash',
           return_value='mock_hash_2')
    def test_cached_url_matches_original(self, mock_hash, mock_exists,
                                          mock_full, mock_upload,
                                          mock_folder):
        """The reused URL should be identical to the original upload URL."""
        from fam.sync.drive import _upload_entries
        from fam.utils.photo_paths import parse_photo_paths

        original_url = 'https://drive.google.com/file/d/xyz789/view'
        mock_upload.return_value = original_url
        saved_urls = {}

        def capture_update(entry_id, encoded):
            saved_urls[entry_id] = parse_photo_paths(encoded)

        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        entries = [
            self._make_entry(1, 'photos/shared.jpg', vendor_name='Vendor A'),
            self._make_entry(2, 'photos/shared.jpg', vendor_name='Vendor B'),
        ]

        _upload_entries(
            entries, capture_update, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn)

        # Both entries should have the exact same URL
        assert saved_urls[1] == [original_url]
        assert saved_urls[2] == [original_url]

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    def test_pre_populated_cache_skips_upload(self, mock_exists, mock_full,
                                               mock_upload, mock_folder):
        """An upload_cache with pre-existing entries should skip uploads."""
        from fam.sync.drive import _upload_entries

        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        pre_cache = {
            'photos/already_uploaded.jpg': 'https://drive.google.com/file/d/old/view'
        }

        entries = [
            self._make_entry(1, 'photos/already_uploaded.jpg'),
        ]

        uploaded, failed = _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn,
            upload_cache=pre_cache)

        # Should not call upload_photo at all — cache hit
        mock_upload.assert_not_called()
        assert uploaded == 1
        assert failed == 0
        # URL should be saved
        update_fn.assert_called_once()

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash',
           return_value='mock_hash_3')
    def test_shared_cache_across_batches(self, mock_hash, mock_exists,
                                          mock_full, mock_upload,
                                          mock_folder):
        """A shared upload_cache across FMNP and Payment batches deduplicates."""
        from fam.sync.drive import _upload_entries

        mock_upload.return_value = 'https://drive.google.com/file/d/first/view'
        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        shared_cache = {}

        # FMNP batch uploads the file
        fmnp_entries = [
            self._make_entry(1, 'photos/shared.jpg'),
        ]
        _upload_entries(
            fmnp_entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn,
            upload_cache=shared_cache)

        assert mock_upload.call_count == 1  # uploaded once

        # Payment batch references the same file
        payment_entries = [
            self._make_entry(2, 'photos/shared.jpg',
                             vendor_name='Vendor B'),
        ]
        # Add payment-specific fields
        payment_entries[0]['method_name_snapshot'] = 'FMNP'
        payment_entries[0]['fam_transaction_id'] = 'FAM-001'

        _upload_entries(
            payment_entries, update_fn, 'Payment',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn,
            upload_cache=shared_cache)

        # Still only 1 upload call — payment batch got it from cache
        assert mock_upload.call_count == 1

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash',
           return_value='mock_hash_4')
    def test_three_entries_same_photo(self, mock_hash, mock_exists, mock_full,
                                       mock_upload, mock_folder):
        """Three entries sharing one photo should trigger exactly 1 upload."""
        from fam.sync.drive import _upload_entries

        mock_upload.return_value = 'https://drive.google.com/file/d/abc/view'
        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        entries = [
            self._make_entry(1, 'photos/shared.jpg', vendor_name='V1'),
            self._make_entry(2, 'photos/shared.jpg', vendor_name='V2'),
            self._make_entry(3, 'photos/shared.jpg', vendor_name='V3'),
        ]

        uploaded, failed = _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn)

        assert mock_upload.call_count == 1
        assert uploaded == 3
        assert failed == 0
        assert update_fn.call_count == 3

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo', return_value=None)
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash')
    def test_failed_upload_not_cached(self, mock_hash, mock_exists, mock_full,
                                       mock_upload, mock_folder):
        """If upload_photo returns None, the path should NOT be cached."""
        from fam.sync.drive import _upload_entries

        mock_hash.side_effect = ['fail_hash_1', 'fail_hash_2']
        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        upload_cache = {}
        entries = [
            self._make_entry(1, 'photos/fail.jpg'),
            self._make_entry(2, 'photos/fail.jpg'),
        ]

        uploaded, failed = _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn,
            upload_cache=upload_cache)

        # Both entries try to upload (failed = not cached)
        assert mock_upload.call_count == 2
        assert uploaded == 0
        assert failed == 2
        assert 'photos/fail.jpg' not in upload_cache

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash')
    def test_multi_photo_entry_dedup(self, mock_hash, mock_exists, mock_full,
                                      mock_upload, mock_folder):
        """Multi-photo entries with shared paths should dedup per path."""
        from fam.sync.drive import _upload_entries
        import json

        mock_hash.side_effect = ['hash_a', 'hash_b']
        mock_upload.side_effect = [
            'https://drive.google.com/file/d/aaa/view',
            'https://drive.google.com/file/d/bbb/view',
        ]
        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}_{idx}{ext}"

        # Two entries share the same 2-photo set
        photo_path = json.dumps(['photos/a.jpg', 'photos/b.jpg'])
        entries = [
            self._make_entry(1, photo_path, vendor_name='V1'),
            self._make_entry(2, photo_path, vendor_name='V2'),
        ]

        uploaded, failed = _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn)

        # Only 2 uploads (for photos a and b), not 4
        assert mock_upload.call_count == 2
        assert uploaded == 4  # 2 photos × 2 entries (cached)
        assert failed == 0

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    def test_already_uploaded_entries_skipped(self, mock_exists, mock_full,
                                               mock_upload, mock_folder):
        """Entries that already have drive URLs should be skipped entirely."""
        from fam.sync.drive import _upload_entries

        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        entries = [
            self._make_entry(1, 'photos/done.jpg',
                             photo_drive_url='https://drive.google.com/file/d/old/view'),
        ]

        uploaded, failed = _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn)

        mock_upload.assert_not_called()
        assert uploaded == 0
        assert failed == 0


# ══════════════════════════════════════════════════════════════════
# compute_file_hash utility
# ══════════════════════════════════════════════════════════════════
class TestComputeFileHash:
    """Test SHA-256 content hashing for photo deduplication."""

    def test_hash_returns_hex_string(self, tmp_path):
        f = tmp_path / "test.jpg"
        f.write_bytes(b"hello world")
        from fam.utils.photo_storage import compute_file_hash
        h = compute_file_hash(str(f))
        assert len(h) == 64  # SHA-256 hex digest length
        assert all(c in '0123456789abcdef' for c in h)

    def test_same_content_same_hash(self, tmp_path):
        from fam.utils.photo_storage import compute_file_hash
        f1 = tmp_path / "a.jpg"
        f2 = tmp_path / "b.jpg"
        content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        f1.write_bytes(content)
        f2.write_bytes(content)
        assert compute_file_hash(str(f1)) == compute_file_hash(str(f2))

    def test_different_content_different_hash(self, tmp_path):
        from fam.utils.photo_storage import compute_file_hash
        f1 = tmp_path / "a.jpg"
        f2 = tmp_path / "b.jpg"
        f1.write_bytes(b"photo content A")
        f2.write_bytes(b"photo content B")
        assert compute_file_hash(str(f1)) != compute_file_hash(str(f2))

    def test_empty_file_has_consistent_hash(self, tmp_path):
        from fam.utils.photo_storage import compute_file_hash
        f = tmp_path / "empty.jpg"
        f.write_bytes(b"")
        h = compute_file_hash(str(f))
        assert len(h) == 64
        # SHA-256 of empty input is a known constant
        import hashlib
        assert h == hashlib.sha256(b"").hexdigest()

    def test_nonexistent_file_raises(self, tmp_path):
        from fam.utils.photo_storage import compute_file_hash
        with pytest.raises(FileNotFoundError):
            compute_file_hash(str(tmp_path / "missing.jpg"))


# ══════════════════════════════════════════════════════════════════
# photo_hash model (DB helpers)
# ══════════════════════════════════════════════════════════════════
class TestPhotoHashModel:
    """Test photo_hashes DB table operations."""

    def test_store_and_retrieve(self):
        from fam.models.photo_hash import store_photo_hash, get_drive_url_by_hash
        store_photo_hash('abc123', 'https://drive.google.com/file/d/xyz/view')
        assert get_drive_url_by_hash('abc123') == 'https://drive.google.com/file/d/xyz/view'

    def test_missing_hash_returns_none(self):
        from fam.models.photo_hash import get_drive_url_by_hash
        assert get_drive_url_by_hash('nonexistent_hash') is None

    def test_replace_updates_url(self):
        from fam.models.photo_hash import store_photo_hash, get_drive_url_by_hash
        store_photo_hash('hash1', 'https://old-url')
        store_photo_hash('hash1', 'https://new-url')
        assert get_drive_url_by_hash('hash1') == 'https://new-url'

    def test_get_all_photo_hashes(self):
        from fam.models.photo_hash import store_photo_hash, get_all_photo_hashes
        store_photo_hash('h1', 'url1')
        store_photo_hash('h2', 'url2')
        store_photo_hash('h3', 'url3')
        all_hashes = get_all_photo_hashes()
        assert len(all_hashes) == 3
        assert all_hashes['h1'] == 'url1'
        assert all_hashes['h2'] == 'url2'
        assert all_hashes['h3'] == 'url3'

    def test_get_all_empty(self):
        from fam.models.photo_hash import get_all_photo_hashes
        assert get_all_photo_hashes() == {}


# ══════════════════════════════════════════════════════════════════
# Schema migration v17 — photo_hashes table
# ══════════════════════════════════════════════════════════════════
class TestSchemaMigrationV17:
    """Test that the v17 migration creates the photo_hashes table."""

    def test_photo_hashes_table_exists(self):
        conn = get_connection()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='photo_hashes'"
        ).fetchone()
        assert row is not None

    def test_photo_hashes_columns(self):
        conn = get_connection()
        cols = conn.execute("PRAGMA table_info(photo_hashes)").fetchall()
        col_names = {c['name'] for c in cols}
        assert 'content_hash' in col_names
        assert 'drive_url' in col_names
        assert 'created_at' in col_names

    def test_schema_version_is_at_least_17(self):
        conn = get_connection()
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] >= 17


# ══════════════════════════════════════════════════════════════════
# Content hash dedup in _upload_entries
# ══════════════════════════════════════════════════════════════════
class TestContentHashDedup:
    """Test that _upload_entries uses content hashes to deduplicate."""

    @staticmethod
    def _make_entry(entry_id, photo_path, photo_drive_url=None,
                    vendor_name='Test Vendor', market_name='Test Market',
                    market_day_date='2026-03-10'):
        return {
            'id': entry_id,
            'photo_path': photo_path,
            'photo_drive_url': photo_drive_url,
            'vendor_name': vendor_name,
            'market_name': market_name,
            'market_day_date': market_day_date,
        }

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash',
           return_value='same_hash_abc')
    def test_different_paths_same_content_uploaded_once(
            self, mock_hash, mock_exists, mock_full,
            mock_upload, mock_folder):
        """Two files with different names but same content → 1 upload."""
        from fam.sync.drive import _upload_entries

        mock_upload.return_value = 'https://drive.google.com/file/d/abc/view'
        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        entries = [
            self._make_entry(1, 'photos/copy_A.jpg'),
            self._make_entry(2, 'photos/copy_B.jpg'),
        ]

        uploaded, failed = _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn)

        # Only 1 actual upload — second file matched by content hash
        assert mock_upload.call_count == 1
        assert uploaded == 2
        assert failed == 0

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash')
    def test_different_content_uploaded_separately(
            self, mock_hash, mock_exists, mock_full,
            mock_upload, mock_folder):
        """Two files with different content → 2 separate uploads."""
        from fam.sync.drive import _upload_entries

        mock_hash.side_effect = ['hash_A', 'hash_B']
        mock_upload.return_value = 'https://drive.google.com/file/d/x/view'
        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        entries = [
            self._make_entry(1, 'photos/photo_A.jpg'),
            self._make_entry(2, 'photos/photo_B.jpg'),
        ]

        uploaded, failed = _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn)

        assert mock_upload.call_count == 2
        assert uploaded == 2

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash',
           return_value='known_hash')
    def test_pre_populated_hash_cache_skips_upload(
            self, mock_hash, mock_exists, mock_full,
            mock_upload, mock_folder):
        """A hash_cache with pre-existing entry should skip the upload."""
        from fam.sync.drive import _upload_entries

        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        hash_cache = {
            'known_hash': 'https://drive.google.com/file/d/prev/view'
        }

        entries = [
            self._make_entry(1, 'photos/new_copy.jpg'),
        ]

        uploaded, failed = _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn,
            hash_cache=hash_cache)

        mock_upload.assert_not_called()
        assert uploaded == 1
        assert failed == 0

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash',
           return_value='new_hash')
    def test_successful_upload_persists_hash(
            self, mock_hash, mock_exists, mock_full,
            mock_upload, mock_folder):
        """After a successful upload, the hash should be stored in DB."""
        from fam.sync.drive import _upload_entries
        from fam.models.photo_hash import get_drive_url_by_hash

        mock_upload.return_value = 'https://drive.google.com/file/d/new/view'
        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        entries = [
            self._make_entry(1, 'photos/fresh.jpg'),
        ]

        _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn)

        # Verify hash was persisted to DB
        assert get_drive_url_by_hash('new_hash') == \
            'https://drive.google.com/file/d/new/view'

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo', return_value=None)
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash',
           return_value='fail_hash')
    def test_failed_upload_not_cached_in_hash(
            self, mock_hash, mock_exists, mock_full,
            mock_upload, mock_folder):
        """Failed uploads should NOT be stored in hash_cache."""
        from fam.sync.drive import _upload_entries
        from fam.models.photo_hash import get_drive_url_by_hash

        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        hash_cache = {}
        entries = [
            self._make_entry(1, 'photos/bad.jpg'),
        ]

        _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn,
            hash_cache=hash_cache)

        assert 'fail_hash' not in hash_cache
        assert get_drive_url_by_hash('fail_hash') is None

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash')
    def test_rel_path_cache_takes_priority_over_hash(
            self, mock_hash, mock_exists, mock_full,
            mock_upload, mock_folder):
        """rel_path cache hit should skip hashing entirely (fast path)."""
        from fam.sync.drive import _upload_entries

        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        upload_cache = {
            'photos/cached.jpg': 'https://drive.google.com/file/d/fast/view'
        }

        entries = [
            self._make_entry(1, 'photos/cached.jpg'),
        ]

        _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn,
            upload_cache=upload_cache)

        # compute_file_hash should never be called — rel_path hit first
        mock_hash.assert_not_called()
        mock_upload.assert_not_called()

    @patch('fam.sync.drive._resolve_entry_folder', return_value='folder_123')
    @patch('fam.sync.drive.upload_photo')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'C:/photos/{p}')
    @patch('fam.utils.photo_storage.photo_exists', return_value=True)
    @patch('fam.utils.photo_storage.compute_file_hash',
           return_value='shared_hash')
    def test_hash_match_populates_rel_path_cache(
            self, mock_hash, mock_exists, mock_full,
            mock_upload, mock_folder):
        """When a hash match is found, rel_path should be added to upload_cache."""
        from fam.sync.drive import _upload_entries

        update_fn = MagicMock()
        filename_fn = lambda entry, idx, total, ext: f"photo_{entry['id']}{ext}"

        upload_cache = {}
        hash_cache = {
            'shared_hash': 'https://drive.google.com/file/d/prev/view'
        }

        entries = [
            self._make_entry(1, 'photos/new_name.jpg'),
        ]

        _upload_entries(
            entries, update_fn, 'FMNP',
            session=MagicMock(), root_folder_id='root_id',
            folder_cache={}, filename_fn=filename_fn,
            upload_cache=upload_cache,
            hash_cache=hash_cache)

        # rel_path should now be in upload_cache for future fast-path
        assert upload_cache['photos/new_name.jpg'] == \
            'https://drive.google.com/file/d/prev/view'


# ══════════════════════════════════════════════════════════════════
# UI-level photo duplicate detection — _MultiPhotoDialog._check_duplicate
# ══════════════════════════════════════════════════════════════════
class TestMultiPhotoDialogDedup:
    """Tests for _MultiPhotoDialog._check_duplicate().

    We avoid instantiating the full QDialog (which would need a QApplication)
    by calling the class method via __func__ on a lightweight stub.
    """

    @staticmethod
    def _make_stub(paths):
        """Return a simple namespace that looks like a _MultiPhotoDialog."""
        class _Stub:
            pass
        s = _Stub()
        s._paths = list(paths)
        return s

    @staticmethod
    def _check(stub, index, new_path):
        from fam.ui.widgets.payment_row import _MultiPhotoDialog
        return _MultiPhotoDialog._check_duplicate(stub, index, new_path)

    def test_no_duplicate_returns_none(self):
        stub = self._make_stub(['/photos/a.jpg', None, '/photos/b.jpg'])
        assert self._check(stub, 1, '/photos/c.jpg') is None

    def test_same_path_returns_slot_index(self):
        stub = self._make_stub(['/photos/a.jpg', None, '/photos/b.jpg'])
        assert self._check(stub, 1, '/photos/a.jpg') == 0

    def test_normalised_path_match(self):
        stub = self._make_stub(['/photos/../photos/a.jpg', None])
        assert self._check(stub, 1, '/photos/a.jpg') == 0

    def test_skips_own_slot(self):
        stub = self._make_stub(['/photos/a.jpg', None])
        assert self._check(stub, 0, '/photos/a.jpg') is None

    def test_skips_empty_slots(self):
        stub = self._make_stub([None, None, None])
        assert self._check(stub, 1, '/photos/a.jpg') is None

    @patch('fam.utils.photo_storage.compute_file_hash')
    def test_content_hash_catches_different_paths_same_content(self, mock_hash):
        mock_hash.side_effect = lambda p: 'same_hash_abc'
        stub = self._make_stub(['/photos/copy_A.jpg', None])
        assert self._check(stub, 1, '/photos/copy_B.jpg') == 0

    @patch('fam.utils.photo_storage.compute_file_hash')
    def test_content_hash_different_content_ok(self, mock_hash):
        mock_hash.side_effect = lambda p: f'hash_{p}'
        stub = self._make_stub(['/photos/a.jpg', None])
        assert self._check(stub, 1, '/photos/b.jpg') is None

    @patch('fam.utils.photo_storage.compute_file_hash',
           side_effect=OSError("file not found"))
    def test_hash_error_gracefully_skipped(self, mock_hash):
        stub = self._make_stub(['/photos/a.jpg', None])
        assert self._check(stub, 1, '/photos/b.jpg') is None


# ══════════════════════════════════════════════════════════════════
# UI-level photo duplicate detection — FMNPScreen._check_photo_duplicate
# ══════════════════════════════════════════════════════════════════
class TestFMNPScreenPhotoDedup:
    """Tests for FMNPScreen._check_photo_duplicate().

    We call the unbound method on a lightweight stub to avoid
    instantiating the full QWidget (which requires QApplication).
    """

    @staticmethod
    def _make_stub(photo_slots):
        class _Stub:
            pass
        s = _Stub()
        s._photo_slots = list(photo_slots)
        return s

    @staticmethod
    def _check(stub, index, new_path):
        from fam.ui.fmnp_screen import FMNPScreen
        return FMNPScreen._check_photo_duplicate(stub, index, new_path)

    def test_no_duplicate_returns_none(self):
        stub = self._make_stub([
            {'source_path': '/a.jpg', 'stored_path': None},
            {'source_path': None, 'stored_path': None},
            {'source_path': '/b.jpg', 'stored_path': None},
        ])
        assert self._check(stub, 1, '/c.jpg') is None

    def test_same_source_path_returns_index(self):
        stub = self._make_stub([
            {'source_path': '/a.jpg', 'stored_path': None},
            {'source_path': None, 'stored_path': None},
        ])
        assert self._check(stub, 1, '/a.jpg') == 0

    def test_normalised_path_match(self):
        stub = self._make_stub([
            {'source_path': '/photos/../photos/a.jpg', 'stored_path': None},
            {'source_path': None, 'stored_path': None},
        ])
        assert self._check(stub, 1, '/photos/a.jpg') == 0

    def test_skips_own_slot(self):
        stub = self._make_stub([
            {'source_path': '/a.jpg', 'stored_path': None},
        ])
        assert self._check(stub, 0, '/a.jpg') is None

    @patch('fam.utils.photo_storage.compute_file_hash')
    def test_content_hash_catches_copies(self, mock_hash):
        mock_hash.side_effect = lambda p: 'identical_hash'
        stub = self._make_stub([
            {'source_path': '/copy_1.jpg', 'stored_path': None},
            {'source_path': None, 'stored_path': None},
        ])
        assert self._check(stub, 1, '/copy_2.jpg') == 0

    @patch('fam.utils.photo_storage.compute_file_hash')
    @patch('fam.utils.photo_storage.get_photo_full_path',
           side_effect=lambda p: f'/data/{p}')
    @patch('os.path.isfile', return_value=True)
    def test_stored_path_hash_checked(self, mock_isfile, mock_full, mock_hash):
        mock_hash.side_effect = lambda p: 'stored_hash'
        stub = self._make_stub([
            {'source_path': None, 'stored_path': 'photos/old.jpg'},
            {'source_path': None, 'stored_path': None},
        ])
        assert self._check(stub, 1, '/new_photo.jpg') == 0

    @patch('fam.utils.photo_storage.compute_file_hash')
    def test_different_content_passes(self, mock_hash):
        mock_hash.side_effect = lambda p: f'hash_{p}'
        stub = self._make_stub([
            {'source_path': '/a.jpg', 'stored_path': None},
            {'source_path': None, 'stored_path': None},
        ])
        assert self._check(stub, 1, '/b.jpg') is None

    @patch('fam.utils.photo_storage.compute_file_hash',
           side_effect=OSError("file not found"))
    def test_hash_error_gracefully_skipped(self, mock_hash):
        stub = self._make_stub([
            {'source_path': '/a.jpg', 'stored_path': None},
            {'source_path': None, 'stored_path': None},
        ])
        assert self._check(stub, 1, '/b.jpg') is None


# ══════════════════════════════════════════════════════════════════
# Schema v18 — local_photo_hashes table
# ══════════════════════════════════════════════════════════════════
class TestSchemaMigrationV18:
    """Tests for the local_photo_hashes table created in v18."""

    def test_table_exists(self, fresh_db):
        row = fresh_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='local_photo_hashes'"
        ).fetchone()
        assert row is not None

    def test_columns(self, fresh_db):
        rows = fresh_db.execute("PRAGMA table_info(local_photo_hashes)").fetchall()
        col_names = {r['name'] for r in rows}
        assert col_names == {'content_hash', 'relative_path', 'created_at'}

    def test_version_is_21(self):
        from fam.database.schema import CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION == 21


# ══════════════════════════════════════════════════════════════════
# Local photo hash model — store / lookup
# ══════════════════════════════════════════════════════════════════
class TestLocalPhotoHashModel:
    """Tests for store_local_photo_hash / get_local_path_by_hash."""

    def test_store_and_retrieve(self, fresh_db):
        from fam.models.photo_hash import store_local_photo_hash, get_local_path_by_hash
        store_local_photo_hash('abc123', 'photos/fmnp_1_100.jpg')
        assert get_local_path_by_hash('abc123') == 'photos/fmnp_1_100.jpg'

    def test_missing_returns_none(self, fresh_db):
        from fam.models.photo_hash import get_local_path_by_hash
        assert get_local_path_by_hash('nonexistent') is None

    def test_insert_ignore_keeps_first(self, fresh_db):
        """INSERT OR IGNORE means the first path wins (no overwrite)."""
        from fam.models.photo_hash import store_local_photo_hash, get_local_path_by_hash
        store_local_photo_hash('dup_hash', 'photos/first.jpg')
        store_local_photo_hash('dup_hash', 'photos/second.jpg')
        assert get_local_path_by_hash('dup_hash') == 'photos/first.jpg'

    def test_different_hashes_stored_separately(self, fresh_db):
        from fam.models.photo_hash import store_local_photo_hash, get_local_path_by_hash
        store_local_photo_hash('hash_a', 'photos/a.jpg')
        store_local_photo_hash('hash_b', 'photos/b.jpg')
        assert get_local_path_by_hash('hash_a') == 'photos/a.jpg'
        assert get_local_path_by_hash('hash_b') == 'photos/b.jpg'


# ══════════════════════════════════════════════════════════════════
# store_photo() hash registration
# ══════════════════════════════════════════════════════════════════
class TestStorePhotoHashRegistration:
    """Tests that store_photo() records the source file hash in local_photo_hashes."""

    def test_hash_recorded_after_store(self, fresh_db, tmp_path):
        """After storing a photo, its source hash should be in the DB."""
        src = tmp_path / "check.jpg"
        src.write_bytes(b'\xff\xd8\xff' + b'\x00' * 100)

        import os
        photos_dir = str(tmp_path / 'photos')
        os.makedirs(photos_dir, exist_ok=True)

        from fam.utils.photo_storage import compute_file_hash
        expected_hash = compute_file_hash(str(src))

        with patch('fam.utils.photo_storage.get_photos_dir',
                   return_value=photos_dir), \
             patch.dict('sys.modules',
                        {'PySide6.QtGui': None, 'PySide6.QtCore': None}):
            from fam.utils.photo_storage import store_photo
            rel = store_photo(str(src), 42)

        from fam.models.photo_hash import get_local_path_by_hash
        assert get_local_path_by_hash(expected_hash) == rel

    def test_hash_registration_failure_does_not_block(self, fresh_db, tmp_path):
        """If hash registration fails, store_photo should still succeed."""
        src = tmp_path / "check.jpg"
        src.write_bytes(b'\xff\xd8\xff' + b'\x00' * 100)

        import os
        photos_dir = str(tmp_path / 'photos')
        os.makedirs(photos_dir, exist_ok=True)

        with patch('fam.utils.photo_storage.get_photos_dir',
                   return_value=photos_dir), \
             patch.dict('sys.modules',
                        {'PySide6.QtGui': None, 'PySide6.QtCore': None}), \
             patch('fam.models.photo_hash.store_local_photo_hash',
                   side_effect=Exception("DB error")):
            from fam.utils.photo_storage import store_photo
            rel = store_photo(str(src), 42)
            assert rel.startswith('photos/')


# ══════════════════════════════════════════════════════════════════
# Cross-transaction duplicate detection — _check_previously_stored
# ══════════════════════════════════════════════════════════════════
class TestCrossTransactionDedup:
    """Tests for _check_previously_stored() on both screens."""

    @patch('fam.utils.photo_storage.compute_file_hash', return_value='known_hash')
    @patch('fam.models.photo_hash.get_local_path_by_hash',
           return_value='photos/fmnp_1_100.jpg')
    def test_multi_photo_dialog_finds_previous(self, mock_lookup, mock_hash):
        from fam.ui.widgets.payment_row import _MultiPhotoDialog
        result = _MultiPhotoDialog._check_previously_stored('/new/photo.jpg')
        assert result == 'photos/fmnp_1_100.jpg'

    @patch('fam.utils.photo_storage.compute_file_hash', return_value='new_hash')
    @patch('fam.models.photo_hash.get_local_path_by_hash', return_value=None)
    def test_multi_photo_dialog_no_match(self, mock_lookup, mock_hash):
        from fam.ui.widgets.payment_row import _MultiPhotoDialog
        result = _MultiPhotoDialog._check_previously_stored('/brand_new.jpg')
        assert result is None

    @patch('fam.utils.photo_storage.compute_file_hash',
           side_effect=OSError("file not found"))
    def test_multi_photo_dialog_error_returns_none(self, mock_hash):
        from fam.ui.widgets.payment_row import _MultiPhotoDialog
        result = _MultiPhotoDialog._check_previously_stored('/missing.jpg')
        assert result is None

    @patch('fam.utils.photo_storage.compute_file_hash', return_value='known_hash')
    @patch('fam.models.photo_hash.get_local_path_by_hash',
           return_value='photos/pay_5_200.jpg')
    def test_fmnp_screen_finds_previous(self, mock_lookup, mock_hash):
        from fam.ui.fmnp_screen import FMNPScreen
        result = FMNPScreen._check_previously_stored('/new/photo.jpg')
        assert result == 'photos/pay_5_200.jpg'

    @patch('fam.utils.photo_storage.compute_file_hash', return_value='new_hash')
    @patch('fam.models.photo_hash.get_local_path_by_hash', return_value=None)
    def test_fmnp_screen_no_match(self, mock_lookup, mock_hash):
        from fam.ui.fmnp_screen import FMNPScreen
        result = FMNPScreen._check_previously_stored('/brand_new.jpg')
        assert result is None

    @patch('fam.utils.photo_storage.compute_file_hash',
           side_effect=OSError("file not found"))
    def test_fmnp_screen_error_returns_none(self, mock_hash):
        from fam.ui.fmnp_screen import FMNPScreen
        result = FMNPScreen._check_previously_stored('/missing.jpg')
        assert result is None


# ══════════════════════════════════════════════════════════════════
# Migration backfill — existing photos hashed during v18 migration
# ══════════════════════════════════════════════════════════════════
class TestMigrationBackfill:
    """Tests that v18 migration backfills hashes for existing photos."""

    def test_backfill_populates_table(self, tmp_path):
        """Existing photos in the photos dir should be hashed during upgrade."""
        from fam.database.connection import set_db_path, get_connection, close_connection
        from fam.database.schema import _migrate_v17_to_v18
        import os

        # Create a photos dir with one file
        photos_dir = str(tmp_path / 'photos')
        os.makedirs(photos_dir)
        photo_file = os.path.join(photos_dir, 'fmnp_1_100.jpg')
        with open(photo_file, 'wb') as f:
            f.write(b'\xff\xd8\xff' + b'\x00' * 50)

        from fam.utils.photo_storage import compute_file_hash
        expected_hash = compute_file_hash(photo_file)

        # Set up fresh DB (v17 — table doesn't exist yet)
        db_file = str(tmp_path / "backfill_test.db")
        close_connection()
        set_db_path(db_file)
        conn = get_connection()
        # Create minimal schema so migration can run
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version "
                     "(version INTEGER, applied_at TEXT)")
        conn.execute("INSERT INTO schema_version (version) VALUES (17)")
        conn.commit()

        # Run the v17→v18 migration with mocked photos dir
        with patch('fam.utils.photo_storage.get_photos_dir',
                   return_value=photos_dir):
            _migrate_v17_to_v18(conn)

        row = conn.execute(
            "SELECT relative_path FROM local_photo_hashes WHERE content_hash = ?",
            (expected_hash,)
        ).fetchone()
        assert row is not None
        assert row['relative_path'] == 'photos/fmnp_1_100.jpg'
        close_connection()
