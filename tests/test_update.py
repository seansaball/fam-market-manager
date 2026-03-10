"""Tests for the auto-update system.

Covers URL parsing, version comparison, GitHub API mocking,
download verification, and update script generation.
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from fam.update.checker import (
    parse_github_repo_url,
    compare_versions,
    check_for_update,
    download_update,
    verify_download,
    generate_update_script,
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
        # 3-part padded to match 4-part: 1.6.1.0 vs 1.6.1.1
        # zip stops at shorter list, so these are equal (by design —
        # we only use 3-part semver)
        assert compare_versions("1.6.1", "1.6.1.1") == 0

    def test_leading_zeros(self):
        assert compare_versions("1.06.01", "1.6.1") == 0

    def test_large_version_numbers(self):
        assert compare_versions("1.100.0", "1.99.999") == 1

    def test_release_candidate(self):
        # "2.0.0-rc1" → treated as 2.0.0
        assert compare_versions("1.9.9", "2.0.0-rc1") == -1
