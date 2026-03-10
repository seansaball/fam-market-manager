"""Auto-update logic — GitHub Releases version check, download, and update script.

Pure Python (no Qt dependency) so it can be tested independently.
Uses only stdlib: urllib, json, re, os, zipfile.
"""

import json
import logging
import os
import re
import sys
import textwrap
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger('fam.update.checker')

# ── URL parsing ───────────────────────────────────────────────

_GITHUB_RE = re.compile(
    r'^(?:https?://)?github\.com/'
    r'([A-Za-z0-9_.-]+)/'           # owner
    r'([A-Za-z0-9_.-]+)'            # repo
    r'(?:/.*)?$'
)


def parse_github_repo_url(url: str) -> Optional[tuple[str, str]]:
    """Validate a GitHub URL and extract (owner, repo).

    Accepts:
        https://github.com/owner/repo
        https://github.com/owner/repo/releases
        github.com/owner/repo
    Returns None if the URL is not a valid GitHub repository.
    """
    url = url.strip().rstrip('/')
    m = _GITHUB_RE.match(url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    # Reject GitHub system paths
    if owner.lower() in ('settings', 'notifications', 'login', 'signup'):
        return None
    # Strip .git suffix if present
    if repo.endswith('.git'):
        repo = repo[:-4]
    return owner, repo


# ── Version comparison ────────────────────────────────────────

def compare_versions(current: str, remote: str) -> int:
    """Compare semantic versions.

    Returns:
        -1 if current < remote  (update available)
         0 if equal
         1 if current > remote  (current is newer)
    """
    def _parse(v: str) -> list[int]:
        v = v.strip().lstrip('vV')
        parts = []
        for segment in v.split('.'):
            # Handle pre-release suffixes like "1.7.0-beta"
            num = re.match(r'(\d+)', segment)
            parts.append(int(num.group(1)) if num else 0)
        # Pad to at least 3 parts
        while len(parts) < 3:
            parts.append(0)
        return parts

    cur = _parse(current)
    rem = _parse(remote)

    for c, r in zip(cur, rem):
        if c < r:
            return -1
        if c > r:
            return 1
    return 0


# ── GitHub API ────────────────────────────────────────────────

def check_for_update(owner: str, repo: str,
                     current_version: str) -> Optional[dict]:
    """Check GitHub Releases for a newer version.

    Returns a dict with release info if successful, None on any error.
    The 'update_available' key indicates whether the remote version is newer.
    """
    from fam import __version__
    api_url = f'https://api.github.com/repos/{owner}/{repo}/releases/latest'

    req = Request(api_url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': f'FAM-Market-Manager/{__version__}',
    })

    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except HTTPError as e:
        if e.code == 404:
            logger.info("No releases found for %s/%s", owner, repo)
        else:
            logger.warning("GitHub API error %d for %s/%s",
                           e.code, owner, repo)
        return None
    except (URLError, OSError) as e:
        logger.warning("Network error checking for updates: %s", e)
        return None
    except Exception:
        logger.exception("Unexpected error checking for updates")
        return None

    tag = data.get('tag_name', '')
    version = tag.lstrip('vV')
    name = data.get('name', tag)
    body = data.get('body', '') or ''
    if len(body) > 2000:
        body = body[:2000] + '...'

    # Find the first .zip asset
    asset_name = ''
    asset_url = ''
    asset_size = 0
    for asset in data.get('assets', []):
        if asset.get('name', '').lower().endswith('.zip'):
            asset_name = asset['name']
            asset_url = asset.get('browser_download_url', '')
            asset_size = asset.get('size', 0)
            break

    if not asset_url:
        logger.info("No .zip asset found in release %s", tag)
        return None

    update_available = compare_versions(current_version, version) < 0

    return {
        'tag_name': tag,
        'version': version,
        'name': name,
        'body': body,
        'asset_name': asset_name,
        'asset_url': asset_url,
        'asset_size': asset_size,
        'update_available': update_available,
    }


# ── Download ──────────────────────────────────────────────────

def download_update(asset_url: str, dest_path: str,
                    progress_callback=None) -> bool:
    """Download a release asset to *dest_path*.

    Calls ``progress_callback(bytes_downloaded, total_bytes)`` on each
    64 KB chunk.  Returns True on success.
    """
    from fam import __version__

    req = Request(asset_url, headers={
        'User-Agent': f'FAM-Market-Manager/{__version__}',
    })

    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        with urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get('Content-Length', 0))
            downloaded = 0

            with open(dest_path, 'wb') as f:
                while True:
                    chunk = resp.read(65536)  # 64 KB
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)

        logger.info("Downloaded %s (%d bytes)", dest_path, downloaded)
        return True

    except Exception:
        logger.exception("Failed to download update from %s", asset_url)
        # Clean up partial download
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except OSError:
            pass
        return False


def verify_download(file_path: str, expected_size: int) -> bool:
    """Verify downloaded file size matches the expected size."""
    if not os.path.isfile(file_path):
        return False
    actual = os.path.getsize(file_path)
    if expected_size > 0 and actual != expected_size:
        logger.warning("Size mismatch: expected %d, got %d",
                       expected_size, actual)
        return False
    return True


# ── Update script generation ─────────────────────────────────

def generate_update_script(app_dir: str, zip_path: str) -> str:
    """Write a batch script that replaces the app after it exits.

    Returns the path to the generated ``.bat`` file.
    """
    from fam.app import get_data_dir
    data_dir = get_data_dir()

    backup_dir = os.path.join(data_dir, '_update_backup')
    temp_dir = os.path.join(data_dir, '_update_temp')
    script_path = os.path.join(data_dir, '_fam_update.bat')

    # Escape paths for batch script (double backslashes not needed
    # because we're writing raw strings)
    script = textwrap.dedent(f"""\
        @echo off
        REM FAM Market Manager — Update Script
        REM Generated automatically. Do not edit.

        echo ============================================
        echo   FAM Market Manager — Applying Update
        echo ============================================
        echo.

        echo Waiting for FAM Manager to close...
        set RETRIES=0

        :WAIT_LOOP
        tasklist /FI "IMAGENAME eq FAM Manager.exe" 2>NUL | find /I "FAM Manager.exe" >NUL
        if %ERRORLEVEL%==0 (
            set /A RETRIES+=1
            if %RETRIES% GEQ 30 (
                echo.
                echo ERROR: FAM Manager did not close after 30 seconds.
                echo Update cancelled.
                pause
                exit /b 1
            )
            timeout /t 1 /nobreak >NUL
            goto WAIT_LOOP
        )

        echo FAM Manager has closed.
        echo.

        REM ── Backup current installation ──
        echo Backing up current version...
        if exist "{backup_dir}" rmdir /s /q "{backup_dir}"
        mkdir "{backup_dir}"
        xcopy /E /I /Q /Y "{app_dir}\\*" "{backup_dir}\\" >NUL 2>&1
        if %ERRORLEVEL% NEQ 0 (
            echo WARNING: Backup may be incomplete, but continuing with update.
        )
        echo Backup complete.

        REM ── Extract new version ──
        echo Extracting update...
        if exist "{temp_dir}" rmdir /s /q "{temp_dir}"
        mkdir "{temp_dir}"
        powershell -NoProfile -Command "Expand-Archive -Path '{zip_path}' -DestinationPath '{temp_dir}' -Force"
        if %ERRORLEVEL% NEQ 0 (
            echo.
            echo ERROR: Failed to extract update. Your current version is unchanged.
            echo The previous version backup is at: {backup_dir}
            pause
            exit /b 1
        )

        REM ── Copy extracted files over app directory ──
        echo Installing update...
        for /D %%d in ("{temp_dir}\\*") do (
            xcopy /E /I /Q /Y "%%d\\*" "{app_dir}\\" >NUL 2>&1
            goto DONE_COPY
        )
        :DONE_COPY

        REM ── Clean up ──
        echo Cleaning up...
        rmdir /s /q "{temp_dir}" >NUL 2>&1
        del /q "{zip_path}" >NUL 2>&1

        echo.
        echo ============================================
        echo   Update applied successfully!
        echo ============================================
        echo.
        echo Starting FAM Manager...

        REM ── Relaunch ──
        start "" "{app_dir}\\FAM Manager.exe"

        REM ── Self-delete ──
        (goto) 2>NUL & del "%~f0"
    """)

    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(script)

    logger.info("Update script written to %s", script_path)
    return script_path
