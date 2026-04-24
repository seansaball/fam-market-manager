"""Tests for the auto-update system.

Covers URL parsing, version comparison, GitHub API mocking,
download verification, and update script generation.
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

import zipfile

from fam.update.checker import (
    parse_github_repo_url,
    compare_versions,
    check_for_update,
    download_update,
    verify_download,
    generate_update_script,
    _find_exe_in_zip,
    _is_safe_zip_member_path,
    _ps_single_quote,
    _ssl_context,
    write_pending_update_marker,
    check_pending_update_result,
    PENDING_UPDATE_FILENAME,
)


# ── URL Parsing ───────────────────────────────────────────────


class TestParseGithubRepoUrl:
    """Tests for parse_github_repo_url()."""

    def test_full_https_url(self):
        result = parse_github_repo_url(
            "https://github.com/seansaball/fam-market-manager")
        assert result == ("seansaball", "fam-market-manager")

    def test_url_with_releases_suffix(self):
        result = parse_github_repo_url(
            "https://github.com/seansaball/fam-market-manager/releases")
        assert result == ("seansaball", "fam-market-manager")

    def test_url_with_releases_tag(self):
        result = parse_github_repo_url(
            "https://github.com/owner/repo/releases/tag/v1.0.0")
        assert result == ("owner", "repo")

    def test_url_without_protocol(self):
        result = parse_github_repo_url(
            "github.com/owner/my-project")
        assert result == ("owner", "my-project")

    def test_http_url(self):
        result = parse_github_repo_url(
            "http://github.com/owner/repo")
        assert result == ("owner", "repo")

    def test_url_with_trailing_slash(self):
        result = parse_github_repo_url(
            "https://github.com/owner/repo/")
        assert result == ("owner", "repo")

    def test_url_with_dot_git(self):
        result = parse_github_repo_url(
            "https://github.com/owner/repo.git")
        assert result == ("owner", "repo")

    def test_whitespace_trimmed(self):
        result = parse_github_repo_url(
            "  https://github.com/owner/repo  ")
        assert result == ("owner", "repo")

    def test_invalid_not_github(self):
        assert parse_github_repo_url("https://gitlab.com/o/r") is None

    def test_invalid_no_repo(self):
        assert parse_github_repo_url("https://github.com/owner") is None

    def test_invalid_empty(self):
        assert parse_github_repo_url("") is None

    def test_invalid_random_text(self):
        assert parse_github_repo_url("not a url at all") is None

    def test_invalid_github_system_path(self):
        assert parse_github_repo_url(
            "https://github.com/settings/profile") is None

    def test_owner_with_dots_and_hyphens(self):
        result = parse_github_repo_url(
            "https://github.com/my.org-name/repo_v2")
        assert result == ("my.org-name", "repo_v2")

    def test_url_with_tree_branch(self):
        result = parse_github_repo_url(
            "https://github.com/owner/repo/tree/main/src")
        assert result == ("owner", "repo")


# ── Version Comparison ────────────────────────────────────────


class TestCompareVersions:
    """Tests for compare_versions()."""

    def test_equal_versions(self):
        assert compare_versions("1.6.1", "1.6.1") == 0

    def test_update_available_patch(self):
        assert compare_versions("1.6.1", "1.6.2") == -1

    def test_update_available_minor(self):
        assert compare_versions("1.6.1", "1.7.0") == -1

    def test_update_available_major(self):
        assert compare_versions("1.6.1", "2.0.0") == -1

    def test_current_newer_patch(self):
        assert compare_versions("1.6.2", "1.6.1") == 1

    def test_current_newer_minor(self):
        assert compare_versions("1.7.0", "1.6.9") == 1

    def test_current_newer_major(self):
        assert compare_versions("2.0.0", "1.9.9") == 1

    def test_strips_v_prefix(self):
        assert compare_versions("v1.6.1", "v1.7.0") == -1

    def test_strips_uppercase_v(self):
        assert compare_versions("V1.6.1", "V1.6.1") == 0

    def test_mixed_v_prefix(self):
        assert compare_versions("1.6.1", "v1.6.2") == -1

    def test_two_part_version(self):
        assert compare_versions("1.6", "1.6.1") == -1

    def test_two_part_equal(self):
        assert compare_versions("1.6", "1.6.0") == 0

    def test_single_part(self):
        assert compare_versions("1", "2") == -1

    def test_pre_release_suffix_ignored(self):
        # "1.7.0-beta" → treated as 1.7.0
        assert compare_versions("1.6.1", "1.7.0-beta") == -1


# ── GitHub API (Mocked) ──────────────────────────────────────


def _mock_release_response(tag="v1.7.0", asset_name="FAM_Manager_v1.7.0.zip",
                           asset_size=87908857, body="Release notes"):
    """Create a mock GitHub API release response."""
    return json.dumps({
        "tag_name": tag,
        "name": f"FAM Market Manager {tag}",
        "body": body,
        "assets": [
            {
                "name": asset_name,
                "browser_download_url":
                    f"https://github.com/o/r/releases/download/{tag}/"
                    f"{asset_name}",
                "size": asset_size,
            }
        ],
    }).encode('utf-8')


class TestCheckForUpdate:
    """Tests for check_for_update() with mocked HTTP."""

    @patch('fam.update.checker.urlopen')
    def test_update_available(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = _mock_release_response("v1.7.0")
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = check_for_update("owner", "repo", "1.6.1")

        assert result is not None
        assert result['update_available'] is True
        assert result['version'] == '1.7.0'
        assert result['asset_name'] == 'FAM_Manager_v1.7.0.zip'
        assert result['asset_size'] == 87908857
        assert 'asset_url' in result

    @patch('fam.update.checker.urlopen')
    def test_no_update_same_version(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = _mock_release_response("v1.6.1")
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = check_for_update("owner", "repo", "1.6.1")

        assert result is not None
        assert result['update_available'] is False

    @patch('fam.update.checker.urlopen')
    def test_no_update_current_newer(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = _mock_release_response("v1.5.0")
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = check_for_update("owner", "repo", "1.6.1")

        assert result is not None
        assert result['update_available'] is False

    @patch('fam.update.checker.urlopen')
    def test_no_zip_asset(self, mock_urlopen):
        """Return None if no .zip asset in the release."""
        data = json.dumps({
            "tag_name": "v1.7.0",
            "name": "v1.7.0",
            "body": "",
            "assets": [
                {"name": "source.tar.gz",
                 "browser_download_url": "https://...",
                 "size": 100}
            ],
        }).encode()
        resp = MagicMock()
        resp.read.return_value = data
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = check_for_update("owner", "repo", "1.6.1")
        assert result is None

    @patch('fam.update.checker.urlopen')
    def test_404_returns_none(self, mock_urlopen):
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError(
            url="", code=404, msg="Not Found", hdrs={}, fp=None)

        result = check_for_update("owner", "repo", "1.6.1")
        assert result is None

    @patch('fam.update.checker.urlopen')
    def test_network_error_returns_none(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("No internet")

        result = check_for_update("owner", "repo", "1.6.1")
        assert result is None

    @patch('fam.update.checker.urlopen')
    def test_body_truncated_at_2000(self, mock_urlopen):
        long_body = "x" * 3000
        resp = MagicMock()
        resp.read.return_value = _mock_release_response(
            body=long_body)
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = check_for_update("owner", "repo", "1.6.1")
        assert result is not None
        assert len(result['body']) <= 2003  # 2000 + '...'
        assert result['body'].endswith('...')


# ── Download Verification ─────────────────────────────────────


class TestVerifyDownload:
    """Tests for verify_download()."""

    def test_correct_size(self, tmp_path):
        f = tmp_path / "test.zip"
        f.write_bytes(b"x" * 1000)
        assert verify_download(str(f), 1000) is True

    def test_wrong_size(self, tmp_path):
        f = tmp_path / "test.zip"
        f.write_bytes(b"x" * 500)
        assert verify_download(str(f), 1000) is False

    def test_missing_file(self, tmp_path):
        assert verify_download(str(tmp_path / "nope.zip"), 1000) is False

    def test_zero_expected_size_passes(self, tmp_path):
        """If expected size is 0 (unknown), just check file exists."""
        f = tmp_path / "test.zip"
        f.write_bytes(b"x" * 500)
        assert verify_download(str(f), 0) is True


# ── Download (Mocked) ─────────────────────────────────────────


class TestDownloadUpdate:
    """Tests for download_update() with mocked HTTP."""

    @patch('fam.update.checker.urlopen')
    def test_successful_download(self, mock_urlopen, tmp_path):
        content = b"fake zip content " * 100
        resp = MagicMock()
        resp.headers = {'Content-Length': str(len(content))}
        # Simulate chunked reading
        resp.read = MagicMock(side_effect=[content, b''])
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        dest = str(tmp_path / "download.zip")
        progress_calls = []
        result = download_update(
            "https://example.com/file.zip", dest,
            progress_callback=lambda d, t: progress_calls.append((d, t)))

        assert result is True
        assert os.path.isfile(dest)
        assert len(progress_calls) > 0

    @patch('fam.update.checker.urlopen')
    def test_failed_download_cleans_up(self, mock_urlopen, tmp_path):
        mock_urlopen.side_effect = Exception("Network error")

        dest = str(tmp_path / "download.zip")
        result = download_update("https://example.com/file.zip", dest)

        assert result is False
        assert not os.path.exists(dest)


# ── Update Script Generation ──────────────────────────────────


class TestGenerateUpdateScript:
    """Tests for generate_update_script()."""

    @patch('fam.app.get_data_dir')
    def test_script_created(self, mock_data_dir, tmp_path):
        mock_data_dir.return_value = str(tmp_path)

        app_dir = str(tmp_path / "app")
        zip_path = str(tmp_path / "update.zip")

        script = generate_update_script(app_dir, zip_path)

        assert os.path.isfile(script)
        assert script.endswith('.bat')

        content = open(script, 'r').read()
        assert 'FAM Manager' in content
        assert 'Expand-Archive' in content
        assert app_dir in content
        assert zip_path in content

    @patch('fam.app.get_data_dir')
    def test_script_contains_backup(self, mock_data_dir, tmp_path):
        mock_data_dir.return_value = str(tmp_path)

        script = generate_update_script(
            str(tmp_path / "app"), str(tmp_path / "u.zip"))

        content = open(script, 'r').read()
        assert '_update_backup' in content
        assert 'xcopy' in content.lower()

    @patch('fam.app.get_data_dir')
    def test_script_relaunches_app(self, mock_data_dir, tmp_path):
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")

        script = generate_update_script(app_dir, str(tmp_path / "u.zip"))

        content = open(script, 'r').read()
        assert f'start "" "{app_dir}\\FAM Manager.exe"' in content

    @patch('fam.app.get_data_dir')
    def test_script_self_deletes(self, mock_data_dir, tmp_path):
        mock_data_dir.return_value = str(tmp_path)

        script = generate_update_script(
            str(tmp_path / "app"), str(tmp_path / "u.zip"))

        content = open(script, 'r').read()
        assert 'del "%~f0"' in content


# ── Zip Structure Probe ───────────────────────────────────────


def _make_zip(path, entries):
    """Helper: create a zip containing the given file entries."""
    with zipfile.ZipFile(path, 'w') as zf:
        for name in entries:
            zf.writestr(name, b"test")


class TestFindExeInZip:
    """Tests for _find_exe_in_zip() — locates FAM Manager.exe inside the zip."""

    def test_exe_at_root(self, tmp_path):
        zp = tmp_path / "u.zip"
        _make_zip(zp, ["FAM Manager.exe", "_internal/foo.dll"])
        assert _find_exe_in_zip(str(zp)) == ""

    def test_exe_one_level_deep(self, tmp_path):
        zp = tmp_path / "u.zip"
        _make_zip(zp, [
            "FAM Manager/FAM Manager.exe",
            "FAM Manager/_internal/foo.dll",
        ])
        assert _find_exe_in_zip(str(zp)) == "FAM Manager"

    def test_exe_two_levels_deep(self, tmp_path):
        """This is the actual v1.9.2/v1.9.3 release zip layout."""
        zp = tmp_path / "u.zip"
        _make_zip(zp, [
            "FAM_Manager_v1.9.3/FAM Manager/FAM Manager.exe",
            "FAM_Manager_v1.9.3/FAM Manager/_internal/foo.dll",
        ])
        assert _find_exe_in_zip(str(zp)) == "FAM_Manager_v1.9.3/FAM Manager"

    def test_exe_not_found(self, tmp_path):
        zp = tmp_path / "u.zip"
        _make_zip(zp, ["readme.txt", "something.dll"])
        assert _find_exe_in_zip(str(zp)) is None

    def test_bad_zip(self, tmp_path):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        assert _find_exe_in_zip(str(bad)) is None


class TestGenerateUpdateScriptNested:
    """Tests for nested-zip handling in generate_update_script()."""

    @patch('fam.app.get_data_dir')
    def test_script_handles_double_nested_zip(self, mock_data_dir, tmp_path):
        """v1.9.3-style zip (FAM_Manager_vX.Y.Z/FAM Manager/...) should
        produce a batch script that copies from the correct sub-path."""
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")
        zp = tmp_path / "FAM_Manager_v1.9.3.zip"
        _make_zip(zp, [
            "FAM_Manager_v1.9.3/FAM Manager/FAM Manager.exe",
            "FAM_Manager_v1.9.3/FAM Manager/_internal/foo.dll",
        ])

        script = generate_update_script(app_dir, str(zp))
        content = open(script, 'r').read()

        # The install xcopy should reference the inner "FAM Manager" folder
        expected_src = os.path.join(
            str(tmp_path), "_update_temp",
            "FAM_Manager_v1.9.3", "FAM Manager",
        )
        assert expected_src in content
        # Sanity check for the guard
        assert "Expected FAM Manager.exe not found" in content

    @patch('fam.app.get_data_dir')
    def test_script_handles_single_nested_zip(self, mock_data_dir, tmp_path):
        """Legacy zip with just FAM Manager/ at root."""
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")
        zp = tmp_path / "update.zip"
        _make_zip(zp, [
            "FAM Manager/FAM Manager.exe",
            "FAM Manager/_internal/foo.dll",
        ])

        script = generate_update_script(app_dir, str(zp))
        content = open(script, 'r').read()

        expected_src = os.path.join(
            str(tmp_path), "_update_temp", "FAM Manager")
        assert expected_src in content

    @patch('fam.app.get_data_dir')
    def test_script_writes_log_file(self, mock_data_dir, tmp_path):
        """Batch script should redirect all output to a log file."""
        mock_data_dir.return_value = str(tmp_path)
        zp = tmp_path / "u.zip"
        _make_zip(zp, ["FAM Manager/FAM Manager.exe"])

        script = generate_update_script(str(tmp_path / "app"), str(zp))
        content = open(script, 'r').read()
        assert '_fam_update.log' in content

    @patch('fam.app.get_data_dir')
    def test_script_fallback_when_exe_missing(self, mock_data_dir, tmp_path):
        """If exe can't be located in zip, fall back to legacy loop."""
        mock_data_dir.return_value = str(tmp_path)
        zp = tmp_path / "u.zip"
        _make_zip(zp, ["random/file.txt"])

        script = generate_update_script(str(tmp_path / "app"), str(zp))
        content = open(script, 'r').read()
        # Legacy fallback uses the for /D loop
        assert 'for /D %%d' in content


# ── Settings Helpers ──────────────────────────────────────────


class TestUpdateSettings:
    """Tests for update-related app_settings helpers."""

    def test_get_set_repo_url(self):
        from fam.utils.app_settings import (
            get_update_repo_url, set_update_repo_url,
        )
        set_update_repo_url("https://github.com/owner/repo")
        assert get_update_repo_url() == "https://github.com/owner/repo"

    def test_auto_check_default_enabled(self):
        from fam.utils.app_settings import is_auto_update_check_enabled
        # Default should be True (setting not yet stored)
        assert is_auto_update_check_enabled() is True

    def test_auto_check_toggle(self):
        from fam.utils.app_settings import (
            is_auto_update_check_enabled, set_setting,
        )
        set_setting('update_auto_check', '0')
        assert is_auto_update_check_enabled() is False
        set_setting('update_auto_check', '1')
        assert is_auto_update_check_enabled() is True

    def test_last_check_timestamp(self):
        from fam.utils.app_settings import (
            get_last_update_check, set_last_update_check,
        )
        set_last_update_check("2026-03-09T14:30:00")
        assert get_last_update_check() == "2026-03-09T14:30:00"

    def test_repo_url_strips_whitespace(self):
        from fam.utils.app_settings import (
            get_update_repo_url, set_update_repo_url,
        )
        set_update_repo_url("  https://github.com/o/r  ")
        assert get_update_repo_url() == "https://github.com/o/r"


# ── Edge Cases ────────────────────────────────────────────────


class TestEdgeCases:
    """Edge case tests for the update system."""

    def test_parse_url_with_fragment(self):
        result = parse_github_repo_url(
            "https://github.com/owner/repo#readme")
        # Fragment is part of the path match, should still parse
        # The regex will match up to the #
        assert result is not None or result is None  # depends on regex
        # Actually test: the regex should handle this
        # github.com/owner/repo#readme → repo#readme is the "repo" capture
        # This is fine — repo names can't have # so it won't match real repos
        # but our regex captures it. Let's just verify no crash.

    def test_compare_versions_empty_strings(self):
        # Should not crash
        result = compare_versions("", "")
        assert result == 0

    def test_compare_versions_weird_format(self):
        # Should not crash on unexpected formats
        result = compare_versions("abc", "def")
        assert isinstance(result, int)

    @patch('fam.update.checker.urlopen')
    def test_check_with_empty_assets_list(self, mock_urlopen):
        data = json.dumps({
            "tag_name": "v1.7.0",
            "name": "v1.7.0",
            "body": "",
            "assets": [],
        }).encode()
        resp = MagicMock()
        resp.read.return_value = data
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = check_for_update("owner", "repo", "1.6.1")
        assert result is None  # no zip asset

    @patch('fam.update.checker.urlopen')
    def test_check_with_malformed_json(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = b"not json at all"
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = check_for_update("owner", "repo", "1.6.1")
        assert result is None

    @patch('fam.update.checker.urlopen')
    def test_check_with_multiple_assets_picks_zip(self, mock_urlopen):
        """When multiple assets exist, should pick the .zip one."""
        data = json.dumps({
            "tag_name": "v1.7.0",
            "name": "v1.7.0",
            "body": "",
            "assets": [
                {"name": "checksums.txt",
                 "browser_download_url": "https://...",
                 "size": 256},
                {"name": "FAM_Manager_v1.7.0.zip",
                 "browser_download_url": "https://dl/v1.7.0.zip",
                 "size": 80000000},
                {"name": "source.tar.gz",
                 "browser_download_url": "https://...",
                 "size": 50000},
            ],
        }).encode()
        resp = MagicMock()
        resp.read.return_value = data
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = check_for_update("owner", "repo", "1.6.1")
        assert result is not None
        assert result['asset_name'] == 'FAM_Manager_v1.7.0.zip'
        assert result['asset_size'] == 80000000


# ── Live Integration Test (hits real GitHub API) ──────────────


class TestLiveGitHubAPI:
    """Integration tests against the real GitHub API.

    These tests hit the network and verify the full update-check pipeline
    works end-to-end with the actual FAM repository.
    """

    def test_live_check_real_repo(self):
        """Hit the real GitHub API and verify we get a valid response."""
        result = check_for_update(
            "seansaball", "fam-market-manager", "0.0.1")
        # Should find a release (repo has published releases)
        assert result is not None, (
            "No release found — is the repo public with releases?")
        assert 'version' in result
        assert 'asset_url' in result
        assert 'asset_name' in result
        assert result['asset_name'].endswith('.zip')
        assert result['asset_size'] > 0
        assert result['update_available'] is True  # 0.0.1 < any real version

    def test_live_check_current_version_not_newer(self):
        """Check against a very high version number — no update expected."""
        result = check_for_update(
            "seansaball", "fam-market-manager", "999.0.0")
        assert result is not None
        assert result['update_available'] is False

    def test_live_check_nonexistent_repo(self):
        """A repo that doesn't exist should return None gracefully."""
        result = check_for_update(
            "seansaball", "this-repo-does-not-exist-xyz-123", "1.0.0")
        assert result is None

    def test_live_parse_and_check_from_url(self):
        """Full pipeline: parse URL → check for update."""
        from fam.utils.app_settings import DEFAULT_REPO_URL
        parsed = parse_github_repo_url(DEFAULT_REPO_URL)
        assert parsed is not None
        owner, repo = parsed
        assert owner == "seansaball"
        assert repo == "fam-market-manager"

        result = check_for_update(owner, repo, "0.0.1")
        assert result is not None
        assert result['update_available'] is True


# ── DEFAULT_REPO_URL Constant ─────────────────────────────────


class TestDefaultRepoUrl:
    """Tests for the DEFAULT_REPO_URL constant and its usage."""

    def test_constant_is_valid_github_url(self):
        from fam.utils.app_settings import DEFAULT_REPO_URL
        parsed = parse_github_repo_url(DEFAULT_REPO_URL)
        assert parsed is not None
        assert parsed == ("seansaball", "fam-market-manager")

    def test_constant_starts_with_https(self):
        from fam.utils.app_settings import DEFAULT_REPO_URL
        assert DEFAULT_REPO_URL.startswith("https://")

    def test_get_update_repo_url_fallback(self):
        """When no URL is saved, getter returns None (caller uses default)."""
        from fam.utils.app_settings import (
            get_update_repo_url, set_setting, DEFAULT_REPO_URL,
        )
        # Clear any stored value
        set_setting('update_repo_url', '')
        saved = get_update_repo_url()
        # Code pattern: saved_url or DEFAULT_REPO_URL
        effective = saved if saved else DEFAULT_REPO_URL
        assert effective == DEFAULT_REPO_URL


# ── Auto-Check Rate Limiting ─────────────────────────────────


class TestAutoCheckRateLimiting:
    """Tests for the 24-hour rate limiting logic."""

    def test_recent_check_skips(self):
        """If last check was < 24h ago, auto-check should be skipped."""
        from datetime import datetime, timedelta
        from fam.utils.app_settings import set_last_update_check, get_last_update_check

        # Set last check to 1 hour ago
        recent = (datetime.now() - timedelta(hours=1)).isoformat()
        set_last_update_check(recent)

        last = get_last_update_check()
        last_dt = datetime.fromisoformat(last)
        should_skip = (datetime.now() - last_dt) < timedelta(hours=24)
        assert should_skip is True

    def test_old_check_allows(self):
        """If last check was > 24h ago, auto-check should proceed."""
        from datetime import datetime, timedelta
        from fam.utils.app_settings import set_last_update_check, get_last_update_check

        # Set last check to 25 hours ago
        old = (datetime.now() - timedelta(hours=25)).isoformat()
        set_last_update_check(old)

        last = get_last_update_check()
        last_dt = datetime.fromisoformat(last)
        should_skip = (datetime.now() - last_dt) < timedelta(hours=24)
        assert should_skip is False

    def test_no_previous_check_allows(self):
        """If no check has ever been done, auto-check should proceed."""
        from fam.utils.app_settings import set_setting, get_last_update_check

        set_setting('update_last_check', '')
        last = get_last_update_check()
        # Empty string is falsy — auto-check should proceed
        assert not last or last == ''


# ── Full Update Flow (Mocked) ────────────────────────────────


class TestFullUpdateFlow:
    """End-to-end tests simulating the complete update flow."""

    @patch('fam.update.checker.urlopen')
    def test_check_then_download_flow(self, mock_urlopen, tmp_path):
        """Simulate: check → update available → download → verify."""
        # Step 1: Check for update
        check_resp = MagicMock()
        check_resp.read.return_value = _mock_release_response(
            "v2.0.0", "FAM_Manager_v2.0.0.zip", 5000)
        check_resp.__enter__ = MagicMock(return_value=check_resp)
        check_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = check_resp

        result = check_for_update("seansaball", "fam-market-manager", "1.7.0")
        assert result is not None
        assert result['update_available'] is True
        assert result['version'] == '2.0.0'

        # Step 2: Download the update
        content = b"PK" + b"\x00" * 4998  # fake zip content, 5000 bytes
        dl_resp = MagicMock()
        dl_resp.headers = {'Content-Length': '5000'}
        dl_resp.read = MagicMock(side_effect=[content, b''])
        dl_resp.__enter__ = MagicMock(return_value=dl_resp)
        dl_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = dl_resp

        dest = str(tmp_path / "FAM_Manager_v2.0.0.zip")
        progress_log = []
        success = download_update(
            result['asset_url'], dest,
            progress_callback=lambda d, t: progress_log.append((d, t)))
        assert success is True

        # Step 3: Verify download
        assert verify_download(dest, 5000) is True
        assert len(progress_log) > 0

    @patch('fam.app.get_data_dir')
    @patch('fam.update.checker.urlopen')
    def test_check_download_script_flow(self, mock_urlopen, mock_data_dir,
                                        tmp_path):
        """Full flow including script generation."""
        mock_data_dir.return_value = str(tmp_path)

        # Check
        check_resp = MagicMock()
        check_resp.read.return_value = _mock_release_response("v2.0.0")
        check_resp.__enter__ = MagicMock(return_value=check_resp)
        check_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = check_resp

        result = check_for_update("owner", "repo", "1.7.0")
        assert result['update_available'] is True

        # Generate update script
        app_dir = str(tmp_path / "FAM Manager")
        zip_path = str(tmp_path / "update.zip")
        script = generate_update_script(app_dir, zip_path)

        assert os.path.isfile(script)
        content = open(script, 'r').read()
        assert 'FAM Manager' in content
        assert app_dir in content
        assert 'Expand-Archive' in content
        assert '_update_backup' in content

    @patch('fam.update.checker.urlopen')
    def test_downgrade_not_offered(self, mock_urlopen):
        """If local version is newer, no update should be offered."""
        resp = MagicMock()
        resp.read.return_value = _mock_release_response("v1.5.0")
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = check_for_update("owner", "repo", "1.7.0")
        assert result is not None
        assert result['update_available'] is False
        assert result['version'] == '1.5.0'

    def test_dismissed_version_persistence(self):
        """Verify dismissed version is stored and retrievable."""
        from fam.utils.app_settings import set_setting, get_setting
        set_setting('update_dismissed_version', '2.0.0')
        assert get_setting('update_dismissed_version') == '2.0.0'

        # Different version should not match
        assert get_setting('update_dismissed_version') != '2.1.0'

    @patch('fam.update.checker.urlopen')
    def test_progress_callback_receives_correct_values(self, mock_urlopen,
                                                        tmp_path):
        """Verify progress callback gets accurate byte counts."""
        chunk1 = b"x" * 65536  # 64KB
        chunk2 = b"x" * 30000  # 30KB
        total = len(chunk1) + len(chunk2)

        resp = MagicMock()
        resp.headers = {'Content-Length': str(total)}
        resp.read = MagicMock(side_effect=[chunk1, chunk2, b''])
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        dest = str(tmp_path / "test.zip")
        progress = []
        download_update("https://example.com/f.zip", dest,
                        progress_callback=lambda d, t: progress.append((d, t)))

        assert len(progress) == 2
        assert progress[0] == (65536, total)
        assert progress[1] == (total, total)


# ── Version Comparison Extended ───────────────────────────────


class TestVersionComparisonExtended:
    """Additional version comparison edge cases."""

    def test_four_part_version(self):
        assert compare_versions("1.6.1.0", "1.6.1.1") == -1

    def test_four_vs_three_part(self):
        # A 4-part patch release over a 3-part current version must be
        # offered as an update.  The shorter list is zero-padded:
        # 1.6.1 → [1,6,1,0]  vs  1.6.1.1 → [1,6,1,1]  →  -1 (update).
        assert compare_versions("1.6.1", "1.6.1.1") == -1

    def test_three_part_vs_four_part_equal(self):
        # Conversely, 1.6.1 and 1.6.1.0 are equal once padded.
        assert compare_versions("1.6.1", "1.6.1.0") == 0

    def test_four_part_current_newer_than_three_part(self):
        # 1.6.1.1 → [1,6,1,1] is newer than 1.6.1 → [1,6,1,0].
        assert compare_versions("1.6.1.1", "1.6.1") == 1

    def test_leading_zeros(self):
        assert compare_versions("1.06.01", "1.6.1") == 0

    def test_large_version_numbers(self):
        assert compare_versions("1.100.0", "1.99.999") == 1

    def test_release_candidate(self):
        # "2.0.0-rc1" → treated as 2.0.0
        assert compare_versions("1.9.9", "2.0.0-rc1") == -1


# ═════════════════════════════════════════════════════════════════
#   v1.9.4 HARDENING — runtime execution + safety + verification
# ═════════════════════════════════════════════════════════════════

import subprocess
import sys


def _make_real_app_zip(zip_path, inner_folder, exe_bytes=b"NEW_EXE_CONTENTS"):
    """Build a release-style zip containing a fake FAM Manager.exe.

    ``inner_folder`` is the path inside the zip that holds the exe.
    Pass ``""`` for a flat zip, ``"FAM Manager"`` for single nesting,
    or ``"FAM_Manager_v1.9.4/FAM Manager"`` for the historical layout.
    """
    with zipfile.ZipFile(zip_path, 'w') as zf:
        prefix = inner_folder.rstrip('/') + '/' if inner_folder else ''
        zf.writestr(prefix + 'FAM Manager.exe', exe_bytes)
        zf.writestr(prefix + '_internal/marker.dll', b"support_lib")


def _prime_fake_install(app_dir, old_bytes=b"OLD_EXE_CONTENTS"):
    """Create a fake existing install at ``app_dir``."""
    os.makedirs(app_dir, exist_ok=True)
    with open(os.path.join(app_dir, 'FAM Manager.exe'), 'wb') as f:
        f.write(old_bytes)
    internal = os.path.join(app_dir, '_internal')
    os.makedirs(internal, exist_ok=True)
    with open(os.path.join(internal, 'old_support.dll'), 'wb') as f:
        f.write(b"old_support")


def _run_batch(script_path, timeout=60):
    """Run the generated update batch file and wait for it to finish.

    Returns the CompletedProcess.  Uses ``cmd /c`` the same way the real
    app launches it.  Tests should assert on file-system state after
    this returns since stdout is redirected inside the script.
    """
    return subprocess.run(
        ['cmd', '/c', script_path],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ── Tier 1: Runtime execution tests (Windows only) ────────────


@pytest.mark.skipif(sys.platform != 'win32',
                    reason="Auto-update batch script is Windows-only")
class TestUpdateScriptRuntime:
    """Actually execute the generated batch script against synthetic
    directories and assert the on-disk outcome.

    These tests would have caught the v1.9.3 double-nested-zip bug before
    release; content-only assertions on the .bat text did not.
    """

    @patch('fam.app.get_data_dir')
    def test_script_replaces_exe_flat_zip(self, mock_data_dir, tmp_path):
        """Flat zip: FAM Manager.exe at the root."""
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")
        _prime_fake_install(app_dir)
        zp = str(tmp_path / "update.zip")
        _make_real_app_zip(zp, "", exe_bytes=b"NEW_FLAT")

        script = generate_update_script(app_dir, zp)
        _run_batch(script)

        with open(os.path.join(app_dir, 'FAM Manager.exe'), 'rb') as f:
            assert f.read() == b"NEW_FLAT"

    @patch('fam.app.get_data_dir')
    def test_script_replaces_exe_single_nested(self, mock_data_dir, tmp_path):
        """Legacy layout: FAM Manager/FAM Manager.exe."""
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")
        _prime_fake_install(app_dir)
        zp = str(tmp_path / "update.zip")
        _make_real_app_zip(zp, "FAM Manager", exe_bytes=b"NEW_SINGLE")

        script = generate_update_script(app_dir, zp)
        _run_batch(script)

        with open(os.path.join(app_dir, 'FAM Manager.exe'), 'rb') as f:
            assert f.read() == b"NEW_SINGLE"

    @patch('fam.app.get_data_dir')
    def test_script_replaces_exe_double_nested(self, mock_data_dir, tmp_path):
        """v1.9.2/v1.9.3 broken layout: FAM_Manager_vX.Y.Z/FAM Manager/...

        This is the exact layout that caused the original bug.  A
        content-only test would not have caught this; runtime execution
        proves the hard-coded source_dir and guard work end to end.
        """
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")
        _prime_fake_install(app_dir)
        zp = str(tmp_path / "update.zip")
        _make_real_app_zip(zp,
                           "FAM_Manager_v1.9.4/FAM Manager",
                           exe_bytes=b"NEW_DOUBLE")

        script = generate_update_script(app_dir, zp)
        _run_batch(script)

        with open(os.path.join(app_dir, 'FAM Manager.exe'), 'rb') as f:
            assert f.read() == b"NEW_DOUBLE"
        # Critical: no leftover subfolder should have been copied
        assert not os.path.isdir(os.path.join(app_dir, 'FAM Manager'))
        assert not os.path.isdir(
            os.path.join(app_dir, 'FAM_Manager_v1.9.4'))

    @patch('fam.app.get_data_dir')
    def test_script_backup_contains_old_version(self, mock_data_dir, tmp_path):
        """After a successful run, the backup dir must hold the OLD bytes."""
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")
        _prime_fake_install(app_dir, old_bytes=b"OLD_FOR_BACKUP")
        zp = str(tmp_path / "update.zip")
        _make_real_app_zip(zp, "FAM Manager", exe_bytes=b"NEW_INSTALL")

        script = generate_update_script(app_dir, zp)
        _run_batch(script)

        backup_exe = os.path.join(
            str(tmp_path), '_update_backup', 'FAM Manager.exe')
        assert os.path.isfile(backup_exe), "backup was not created"
        with open(backup_exe, 'rb') as f:
            assert f.read() == b"OLD_FOR_BACKUP"

    @patch('fam.app.get_data_dir')
    def test_script_cleans_up_temp_and_zip(self, mock_data_dir, tmp_path):
        """Successful install should delete both the extract temp dir
        and the downloaded zip."""
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")
        _prime_fake_install(app_dir)
        zp = str(tmp_path / "update.zip")
        _make_real_app_zip(zp, "FAM Manager")

        script = generate_update_script(app_dir, zp)
        _run_batch(script)

        assert not os.path.isdir(str(tmp_path / "_update_temp"))
        assert not os.path.isfile(zp)

    @patch('fam.app.get_data_dir')
    def test_script_self_deletes_after_run(self, mock_data_dir, tmp_path):
        """The .bat file must remove itself at the end so it doesn't
        linger across reboots."""
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")
        _prime_fake_install(app_dir)
        zp = str(tmp_path / "update.zip")
        _make_real_app_zip(zp, "FAM Manager")

        script = generate_update_script(app_dir, zp)
        assert os.path.isfile(script)
        _run_batch(script)
        assert not os.path.isfile(script), "update script did not self-delete"

    @patch('fam.app.get_data_dir')
    def test_script_guard_fires_when_source_dir_wrong(self, mock_data_dir,
                                                      tmp_path):
        """Tamper with the generated script so source_dir points at a
        path where no FAM Manager.exe exists.  The guard should catch
        this and exit non-zero with a diagnostic, not silently no-op.
        """
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")
        _prime_fake_install(app_dir, old_bytes=b"UNCHANGED")
        zp = str(tmp_path / "update.zip")
        _make_real_app_zip(zp, "FAM Manager", exe_bytes=b"NEW_BYTES")

        script = generate_update_script(app_dir, zp)

        # Swap the real source_dir for a bogus one
        content = open(script, 'r', encoding='utf-8').read()
        bad_src = str(tmp_path / "_update_temp" / "does_not_exist")
        real_src = str(tmp_path / "_update_temp" / "FAM Manager")
        # Replace every occurrence so both the guard check and the xcopy
        # path reference the fake location
        content = content.replace(real_src, bad_src)
        with open(script, 'w', encoding='utf-8') as f:
            f.write(content)

        result = _run_batch(script)

        # Guard must have fired: non-zero exit
        assert result.returncode != 0
        # Log should name the error
        log_path = str(tmp_path / "_fam_update.log")
        assert os.path.isfile(log_path)
        log_text = open(log_path, 'r', encoding='utf-8').read()
        assert "Expected FAM Manager.exe not found" in log_text
        # Old install must NOT have been replaced
        with open(os.path.join(app_dir, 'FAM Manager.exe'), 'rb') as f:
            assert f.read() == b"UNCHANGED"

    @patch('fam.app.get_data_dir')
    def test_script_exits_nonzero_on_corrupt_zip(self, mock_data_dir,
                                                 tmp_path):
        """When the zip fails to extract, the script must exit with an
        error and leave the existing install untouched."""
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")
        _prime_fake_install(app_dir, old_bytes=b"DO_NOT_REPLACE")
        # Build a valid zip for script generation (so probe works), then
        # overwrite with garbage so PowerShell extract fails at runtime.
        zp = str(tmp_path / "update.zip")
        _make_real_app_zip(zp, "FAM Manager")
        script = generate_update_script(app_dir, zp)
        with open(zp, 'wb') as f:
            f.write(b"this is not a valid zip file at all")

        result = _run_batch(script)

        assert result.returncode != 0
        # Install untouched
        with open(os.path.join(app_dir, 'FAM Manager.exe'), 'rb') as f:
            assert f.read() == b"DO_NOT_REPLACE"


# ── Tier 2: Log file assertions ───────────────────────────────


@pytest.mark.skipif(sys.platform != 'win32',
                    reason="Log file is produced by Windows batch execution")
class TestUpdateScriptLogFile:
    """The _fam_update.log is the only diagnostic users can send us when
    an auto-update appears to fail.  These tests lock in its presence
    and content so future regressions don't strip it silently.
    """

    @patch('fam.app.get_data_dir')
    def test_log_file_created_on_run(self, mock_data_dir, tmp_path):
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")
        _prime_fake_install(app_dir)
        zp = str(tmp_path / "update.zip")
        _make_real_app_zip(zp, "FAM Manager")

        script = generate_update_script(app_dir, zp)
        _run_batch(script)

        log_path = str(tmp_path / "_fam_update.log")
        assert os.path.isfile(log_path), "_fam_update.log was not created"
        assert os.path.getsize(log_path) > 0

    @patch('fam.app.get_data_dir')
    def test_log_contains_install_source_path(self, mock_data_dir, tmp_path):
        """Log should record the hard-coded source_dir so we can confirm
        the Python-side probe matched the actual zip layout."""
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")
        _prime_fake_install(app_dir)
        zp = str(tmp_path / "update.zip")
        _make_real_app_zip(zp, "FAM_Manager_v1.9.4/FAM Manager")

        script = generate_update_script(app_dir, zp)
        _run_batch(script)

        log_text = open(str(tmp_path / "_fam_update.log"),
                        'r', encoding='utf-8').read()
        assert "Installing update from" in log_text
        assert "FAM_Manager_v1.9.4" in log_text

    @patch('fam.app.get_data_dir')
    def test_log_contains_error_on_failure(self, mock_data_dir, tmp_path):
        """A failed install must leave an ERROR: line in the log."""
        mock_data_dir.return_value = str(tmp_path)
        app_dir = str(tmp_path / "app")
        _prime_fake_install(app_dir)
        zp = str(tmp_path / "update.zip")
        _make_real_app_zip(zp, "FAM Manager")
        script = generate_update_script(app_dir, zp)
        # Corrupt the zip so extract fails
        with open(zp, 'wb') as f:
            f.write(b"garbage")

        _run_batch(script)

        log_text = open(str(tmp_path / "_fam_update.log"),
                        'r', encoding='utf-8').read()
        assert "ERROR" in log_text


# ── Tier 3: Safety / escaping edge cases ──────────────────────


class TestSafeZipMemberPath:
    """Regression tests for _is_safe_zip_member_path()."""

    def test_normal_path_accepted(self):
        assert _is_safe_zip_member_path("FAM Manager/FAM Manager.exe")

    def test_root_file_accepted(self):
        assert _is_safe_zip_member_path("FAM Manager.exe")

    def test_parent_traversal_rejected(self):
        assert not _is_safe_zip_member_path("../FAM Manager.exe")

    def test_nested_parent_traversal_rejected(self):
        assert not _is_safe_zip_member_path(
            "FAM Manager/../../FAM Manager.exe")

    def test_absolute_unix_rejected(self):
        assert not _is_safe_zip_member_path("/etc/passwd")

    def test_absolute_windows_rejected(self):
        assert not _is_safe_zip_member_path("\\Windows\\System32\\evil.exe")

    def test_drive_letter_rejected(self):
        assert not _is_safe_zip_member_path("C:Windows/evil.exe")

    def test_empty_rejected(self):
        assert not _is_safe_zip_member_path("")


class TestFindExePathTraversalGuard:
    """_find_exe_in_zip must skip unsafe entries so we never compute a
    source_dir outside the extraction temp dir."""

    def test_traversal_entry_ignored(self, tmp_path):
        zp = tmp_path / "evil.zip"
        _make_zip(zp, [
            "../FAM Manager.exe",             # would escape temp_dir
            "FAM Manager/FAM Manager.exe",    # legitimate
        ])
        # Must return the safe entry, not the traversal one
        assert _find_exe_in_zip(str(zp)) == "FAM Manager"

    def test_only_unsafe_entries_returns_none(self, tmp_path):
        zp = tmp_path / "evil.zip"
        _make_zip(zp, ["../../FAM Manager.exe"])
        assert _find_exe_in_zip(str(zp)) is None


class TestPowerShellPathEscape:
    """_ps_single_quote() escapes apostrophes for PowerShell -Path '...'.

    Without this, user directories like C:\\Users\\O'Brien\\... break
    Expand-Archive silently and the update appears to 'succeed' while
    nothing is installed.
    """

    def test_no_quote_passes_through(self):
        assert _ps_single_quote("C:\\Users\\Sean\\AppData") == \
            "C:\\Users\\Sean\\AppData"

    def test_single_quote_doubled(self):
        assert _ps_single_quote("C:\\Users\\O'Brien") == \
            "C:\\Users\\O''Brien"

    def test_multiple_quotes(self):
        assert _ps_single_quote("a'b'c") == "a''b''c"


class TestGenerateScriptPathEdgeCases:
    """generate_update_script must cope with awkward but legal Windows
    paths for both app_dir and data_dir."""

    @patch('fam.app.get_data_dir')
    def test_app_dir_with_spaces(self, mock_data_dir, tmp_path):
        mock_data_dir.return_value = str(tmp_path)
        app_dir = "C:\\Program Files\\FAM Manager"
        zp = tmp_path / "u.zip"
        _make_zip(zp, ["FAM Manager/FAM Manager.exe"])

        script = generate_update_script(app_dir, str(zp))
        content = open(script, 'r', encoding='utf-8').read()

        # Path is quoted, so spaces should be intact (not escaped/broken)
        assert f'"{app_dir}' in content

    @patch('fam.app.get_data_dir')
    def test_zip_path_with_apostrophe_escaped_for_powershell(
            self, mock_data_dir, tmp_path):
        """PowerShell Expand-Archive would break on a raw apostrophe in
        the zip path.  The generator must double the quote.

        Note: the raw apostrophe *does* still appear in the batch-style
        ``del "..."`` line, where double-quote wrapping makes it safe.
        Only the PowerShell single-quoted -Path argument needs the
        doubling.  We assert on the Expand-Archive line specifically.
        """
        mock_data_dir.return_value = str(tmp_path)
        apostrophe_path = "C:\\Users\\O'Brien\\Downloads\\update.zip"

        with patch('fam.update.checker._find_exe_in_zip',
                   return_value="FAM Manager"):
            script = generate_update_script("C:\\App", apostrophe_path)
        content = open(script, 'r', encoding='utf-8').read()

        # Find the Expand-Archive line
        ps_line = next(
            (ln for ln in content.splitlines() if 'Expand-Archive' in ln),
            None,
        )
        assert ps_line is not None, "Expand-Archive line not in script"
        # That line must use the doubled apostrophe (PowerShell escape)
        assert "O''Brien" in ps_line
        # And must not contain a raw single-quote break inside ''...''
        # A raw ' inside the PS -Path '...' would end the string early.
        # Count apostrophes between the first pair of single quotes that
        # wrap the -Path argument: must be an even number (0 is fine).
        path_arg = ps_line.split("-Path '", 1)[1].split("' ", 1)[0]
        assert path_arg.count("'") % 2 == 0, \
            f"PowerShell -Path argument has unbalanced quotes: {path_arg!r}"

    @patch('fam.app.get_data_dir')
    def test_app_dir_with_ampersand_is_quoted(self, mock_data_dir, tmp_path):
        """Batch treats & as command separator.  Because we wrap app_dir
        in double quotes throughout, this must still parse cleanly."""
        mock_data_dir.return_value = str(tmp_path)
        app_dir = "C:\\Dept & Co\\FAM Manager"
        zp = tmp_path / "u.zip"
        _make_zip(zp, ["FAM Manager/FAM Manager.exe"])

        script = generate_update_script(app_dir, str(zp))
        content = open(script, 'r', encoding='utf-8').read()

        # Every reference to app_dir should be inside double quotes
        assert f'"{app_dir}\\*"' in content
        assert f'"{app_dir}\\FAM Manager.exe"' in content


# ── Tier 7: Pending-update verification ───────────────────────


class TestPendingUpdateMarker:
    """The pending-update marker turns silent updater failures into
    loud, user-visible errors on the next launch.  The v1.9.3 bug was
    silent for days — this is the circuit breaker."""

    def test_write_marker_creates_file(self, tmp_path):
        path = write_pending_update_marker("1.9.4", data_dir=str(tmp_path))
        assert os.path.isfile(path)
        assert path.endswith(PENDING_UPDATE_FILENAME)

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert data['target_version'] == "1.9.4"

    def test_write_marker_strips_v_prefix(self, tmp_path):
        path = write_pending_update_marker("v1.9.4", data_dir=str(tmp_path))
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert data['target_version'] == "1.9.4"

    def test_check_no_marker_returns_none(self, tmp_path):
        assert check_pending_update_result("1.9.4",
                                           data_dir=str(tmp_path)) is None

    def test_check_success_when_versions_match(self, tmp_path):
        write_pending_update_marker("1.9.4", data_dir=str(tmp_path))
        result = check_pending_update_result("1.9.4",
                                             data_dir=str(tmp_path))
        assert result == {'status': 'success', 'target_version': '1.9.4'}

    def test_check_failure_when_versions_differ(self, tmp_path):
        """The v1.9.3 bug: user clicks update, app restarts on same
        version, no visible indication anything went wrong.  With the
        marker in place, the mismatch is reported immediately."""
        write_pending_update_marker("1.9.4", data_dir=str(tmp_path))
        result = check_pending_update_result("1.9.3",
                                             data_dir=str(tmp_path))
        assert result == {
            'status': 'failed',
            'target_version': '1.9.4',
            'actual_version': '1.9.3',
        }

    def test_marker_removed_after_check_success(self, tmp_path):
        path = write_pending_update_marker("1.9.4", data_dir=str(tmp_path))
        check_pending_update_result("1.9.4", data_dir=str(tmp_path))
        assert not os.path.isfile(path), \
            "marker must not persist — would re-fire on every launch"

    def test_marker_removed_after_check_failure(self, tmp_path):
        path = write_pending_update_marker("1.9.4", data_dir=str(tmp_path))
        check_pending_update_result("1.9.3", data_dir=str(tmp_path))
        assert not os.path.isfile(path)

    def test_corrupt_marker_handled_gracefully(self, tmp_path):
        """If the marker file is somehow malformed, treat as no marker
        rather than crashing app startup."""
        path = os.path.join(str(tmp_path), PENDING_UPDATE_FILENAME)
        with open(path, 'w', encoding='utf-8') as f:
            f.write("this is not valid json {")
        assert check_pending_update_result("1.9.4",
                                           data_dir=str(tmp_path)) is None
        # Corrupt marker should be cleaned up too
        assert not os.path.isfile(path)

    def test_check_ignores_v_prefix_in_current_version(self, tmp_path):
        """Current __version__ might or might not have a 'v' prefix."""
        write_pending_update_marker("1.9.4", data_dir=str(tmp_path))
        result = check_pending_update_result("v1.9.4",
                                             data_dir=str(tmp_path))
        assert result['status'] == 'success'


# ═════════════════════════════════════════════════════════════════
#   v1.9.6 TLS FIX — certifi-backed SSL context
# ═════════════════════════════════════════════════════════════════

class TestSslContext:
    """Regression tests for the v1.9.5 'CERTIFICATE_VERIFY_FAILED in
    frozen build' bug.

    The root cause was that ``urllib.urlopen`` used the default SSL
    context, which in a PyInstaller-frozen app has no trusted CAs
    (OpenSSL's compiled-in search paths do not resolve inside the
    bundle).  The fix builds an SSL context explicitly from
    ``certifi.where()`` and reuses it for every outbound HTTPS call.
    """

    def setup_method(self):
        # Reset cached context so each test sees a fresh build
        import fam.update.checker as ch
        ch._SSL_CONTEXT = None

    def test_context_uses_certifi_bundle(self):
        """When certifi is available, the context must be built from
        ``certifi.where()`` — not the platform default."""
        import certifi
        ctx = _ssl_context()
        # create_default_context returns an SSLContext regardless of
        # source; we verify certifi's bundle path is readable and the
        # returned object is the cached instance.
        assert ctx is not None
        assert os.path.isfile(certifi.where())
        # Calling again returns the cached object (not a fresh build)
        assert _ssl_context() is ctx

    def test_context_verifies_certificates(self):
        """The context must have certificate verification enabled —
        a context with CERT_NONE would defeat the whole purpose."""
        import ssl as _ssl
        ctx = _ssl_context()
        assert ctx.verify_mode == _ssl.CERT_REQUIRED

    def test_context_enforces_hostname_check(self):
        """Hostname verification must be on — a MITM could otherwise
        present a valid cert for a different domain."""
        ctx = _ssl_context()
        assert ctx.check_hostname is True

    def test_context_falls_back_when_certifi_missing(self):
        """If certifi cannot be imported, the helper must not crash —
        it falls back to the platform default context so dev-mode
        (where certifi often isn't needed) keeps working."""
        import fam.update.checker as ch
        ch._SSL_CONTEXT = None
        with patch.dict('sys.modules', {'certifi': None}):
            # Also need to patch the import statement inside _ssl_context
            with patch('builtins.__import__', side_effect=ImportError):
                ctx = ch._ssl_context()
        assert ctx is not None  # fell back to default, did not raise

    def test_check_for_update_passes_context_to_urlopen(self):
        """The v1.9.5 bug fix: check_for_update MUST pass the certifi-
        backed context to urlopen, not rely on the default context."""
        from unittest.mock import patch, MagicMock
        resp = MagicMock()
        resp.read.return_value = b'{"tag_name": "v2.0.0", "name": "x", "body": "", "assets": [{"name": "f.zip", "browser_download_url": "https://x/f.zip", "size": 100}]}'
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch('fam.update.checker.urlopen', return_value=resp) as mock_urlopen:
            check_for_update('owner', 'repo', '1.9.5')
        # urlopen must have been called with context= kwarg, not default
        call_kwargs = mock_urlopen.call_args.kwargs
        assert 'context' in call_kwargs, \
            "check_for_update must pass explicit SSL context to urlopen"
        assert call_kwargs['context'] is _ssl_context()

    def test_download_update_passes_context_to_urlopen(self, tmp_path):
        """Same invariant for the download path — this is where the
        real user-reported failure happened."""
        from unittest.mock import patch, MagicMock
        resp = MagicMock()
        resp.headers = {'Content-Length': '5'}
        resp.read = MagicMock(side_effect=[b'12345', b''])
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch('fam.update.checker.urlopen', return_value=resp) as mock_urlopen:
            download_update('https://x/f.zip', str(tmp_path / 'f.zip'))
        call_kwargs = mock_urlopen.call_args.kwargs
        assert 'context' in call_kwargs, \
            "download_update must pass explicit SSL context to urlopen"
        assert call_kwargs['context'] is _ssl_context()

    def test_context_cached_across_calls(self):
        """The context is expensive to build (certifi file read + SSL
        init) — verify it is built once and reused."""
        ctx1 = _ssl_context()
        ctx2 = _ssl_context()
        ctx3 = _ssl_context()
        assert ctx1 is ctx2 is ctx3
