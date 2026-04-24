"""Auto-update logic — GitHub Releases version check, download, and update script.

Pure Python (no Qt dependency) so it can be tested independently.
Uses only stdlib: urllib, json, re, os, ssl, zipfile.  Relies on the
bundled ``certifi`` package for TLS root certificates when running as
a PyInstaller-frozen build (where OpenSSL's default CA search paths do
not resolve inside the one-file bundle).
"""

import json
import logging
import os
import re
import ssl
import sys
import textwrap
import zipfile
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger('fam.update.checker')


# ── TLS root certificate handling ────────────────────────────

# Cached SSL context so we don't rebuild it on every call.  Built on
# first access via :func:`_ssl_context`.
_SSL_CONTEXT: Optional[ssl.SSLContext] = None


def _ssl_context() -> ssl.SSLContext:
    """Return a shared SSL context backed by certifi's CA bundle.

    In PyInstaller-frozen builds, :func:`ssl.create_default_context`
    produces a context with no trusted CAs because OpenSSL's compiled-
    in default search paths do not exist inside the frozen bundle.
    Connecting to GitHub's CDN then fails with
    ``CERTIFICATE_VERIFY_FAILED`` — the bug that blocked auto-update
    on v1.9.5 and earlier.

    We explicitly build the context against :func:`certifi.where()`,
    which returns the CA bundle bundled with our app (the PyInstaller
    spec file includes ``collect_data_files('certifi')``).

    If ``certifi`` cannot be imported for any reason (e.g. the dev
    environment on a machine where it is not installed), we fall back
    to the platform default — which works fine in non-frozen mode
    because system Python picks up the OS certificate store.
    """
    global _SSL_CONTEXT
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT

    try:
        import certifi
        ca_path = certifi.where()
        ctx = ssl.create_default_context(cafile=ca_path)
        logger.info("TLS: using certifi CA bundle at %s", ca_path)
    except Exception:
        logger.exception(
            "TLS: could not load certifi bundle, falling back to "
            "platform default (may fail in frozen builds)")
        ctx = ssl.create_default_context()

    _SSL_CONTEXT = ctx
    return ctx

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

    Handles any number of dotted components (2-part, 3-part, 4-part, …).
    The shorter version is padded with zeros to match the longer one so
    that ``1.6.1`` compares as older than ``1.6.1.1`` (4-part patch
    release over a 3-part current version is correctly offered as an
    update).

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
        # Pad to at least 3 parts (handles 1.6 vs 1.6.0 equivalence)
        while len(parts) < 3:
            parts.append(0)
        return parts

    cur = _parse(current)
    rem = _parse(remote)

    # Pad the shorter list with zeros so a 4-part release is compared
    # fully against a 3-part current version (otherwise zip truncates
    # and 1.6.1 vs 1.6.1.1 would compare as equal).
    max_len = max(len(cur), len(rem))
    while len(cur) < max_len:
        cur.append(0)
    while len(rem) < max_len:
        rem.append(0)

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
        with urlopen(req, timeout=10, context=_ssl_context()) as resp:
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

        with urlopen(req, timeout=120, context=_ssl_context()) as resp:
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

def _is_safe_zip_member_path(name: str) -> bool:
    """Return True if ``name`` is a safe zip member path (no traversal,
    no absolute paths, no drive letters).

    Rejects ``..`` segments, leading ``/`` or ``\\``, and Windows drive
    letters.  Used by :func:`_find_exe_in_zip` to avoid computing a
    source directory outside the extraction temp dir.
    """
    if not name:
        return False
    # Reject absolute paths
    if name.startswith('/') or name.startswith('\\'):
        return False
    # Reject Windows drive letters (e.g. ``C:foo``)
    if len(name) >= 2 and name[1] == ':':
        return False
    # Reject any path segment equal to ``..``
    # zipfile always uses forward slashes, but defensively split on both
    segments = name.replace('\\', '/').split('/')
    if any(seg == '..' for seg in segments):
        return False
    return True


def _find_exe_in_zip(zip_path: str, exe_name: str = 'FAM Manager.exe') -> Optional[str]:
    """Inspect a release zip and return the path *inside* the zip of the
    directory containing ``exe_name``.

    Release zips have historically been packaged with varying levels of
    nesting (``FAM Manager/``, ``FAM_Manager_v1.9.3/FAM Manager/`` etc.).
    This probe walks the archive listing and returns the parent folder
    of the first match, or ``None`` if the exe is not found.

    Unsafe entries (path traversal, absolute paths) are skipped silently
    to avoid directing the installer at a location outside the temp dir.

    The returned path uses forward slashes (zipfile convention) and has
    no trailing slash.  An empty string means the exe sits at the zip
    root.
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                if not _is_safe_zip_member_path(name):
                    continue
                # zipfile always uses forward slashes regardless of OS
                if name.endswith('/' + exe_name) or name == exe_name:
                    parent = name.rsplit('/', 1)[0] if '/' in name else ''
                    return parent
    except (zipfile.BadZipFile, OSError) as e:
        logger.error("Failed to inspect zip %s: %s", zip_path, e)
        return None
    return None


def _ps_single_quote(path: str) -> str:
    """Escape a path for use inside a PowerShell single-quoted string.

    PowerShell single-quoted strings treat every character literally
    except ``'`` itself, which is escaped by doubling.  Used to pass
    paths containing apostrophes (e.g. ``C:\\Users\\O'Brien\\...``) to
    ``Expand-Archive -Path '...'`` without breaking the command.
    """
    return path.replace("'", "''")


# ── Post-update verification ─────────────────────────────────

PENDING_UPDATE_FILENAME = '_pending_update.json'


def write_pending_update_marker(target_version: str, data_dir: Optional[str] = None) -> str:
    """Write a marker file recording which version the updater is installing.

    Called just before the update batch script is launched.  On the next
    app launch, :func:`check_pending_update_result` compares the marker's
    ``target_version`` to the running ``__version__`` and reports success
    or failure.

    Returns the path to the marker file.
    """
    if data_dir is None:
        from fam.app import get_data_dir
        data_dir = get_data_dir()
    marker_path = os.path.join(data_dir, PENDING_UPDATE_FILENAME)
    payload = {
        'target_version': target_version.lstrip('vV'),
    }
    with open(marker_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f)
    logger.info("Pending-update marker written: target=%s (%s)",
                target_version, marker_path)
    return marker_path


def check_pending_update_result(current_version: str,
                                data_dir: Optional[str] = None) -> Optional[dict]:
    """Check whether a pending update completed successfully.

    Returns:
        - ``None`` — no pending update marker present (normal launch)
        - ``{'status': 'success', 'target_version': ...}`` if the marker's
          target matches ``current_version``
        - ``{'status': 'failed', 'target_version': ..., 'actual_version': ...}``
          if the marker exists but the versions disagree (silent install failure)

    In all cases where the marker existed, it is removed after reading so
    the check only fires once per update attempt.
    """
    if data_dir is None:
        from fam.app import get_data_dir
        data_dir = get_data_dir()
    marker_path = os.path.join(data_dir, PENDING_UPDATE_FILENAME)
    if not os.path.isfile(marker_path):
        return None

    try:
        with open(marker_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        target = str(payload.get('target_version', '')).lstrip('vV')
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read pending-update marker %s: %s",
                       marker_path, e)
        try:
            os.remove(marker_path)
        except OSError:
            pass
        return None

    # Always remove the marker so we only report once.
    try:
        os.remove(marker_path)
    except OSError:
        logger.warning("Could not remove pending-update marker %s", marker_path)

    current_clean = current_version.lstrip('vV')
    if compare_versions(current_clean, target) == 0:
        logger.info("Pending update succeeded: %s", target)
        return {'status': 'success', 'target_version': target}
    logger.error("Pending update FAILED: expected %s, running %s",
                 target, current_clean)
    return {
        'status': 'failed',
        'target_version': target,
        'actual_version': current_clean,
    }


def generate_update_script(app_dir: str, zip_path: str) -> str:
    """Write a batch script that replaces the app after it exits.

    Returns the path to the generated ``.bat`` file.

    The script probes the downloaded zip at generation time to locate
    the folder containing ``FAM Manager.exe``, then hard-codes that
    exact path into the batch file.  This avoids brittle assumptions
    about zip nesting structure.
    """
    from fam.app import get_data_dir
    data_dir = get_data_dir()

    backup_dir = os.path.join(data_dir, '_update_backup')
    temp_dir = os.path.join(data_dir, '_update_temp')
    script_path = os.path.join(data_dir, '_fam_update.bat')
    log_path = os.path.join(data_dir, '_fam_update.log')

    # Probe zip to find the directory containing FAM Manager.exe.
    # If not found, fall back to the old "first subfolder" heuristic.
    inner_path = _find_exe_in_zip(zip_path)
    if inner_path is None:
        logger.warning(
            "Could not locate 'FAM Manager.exe' inside %s — "
            "falling back to first-subfolder copy", zip_path)
        # Fallback: copy first top-level folder (legacy behaviour)
        source_dir = None
    else:
        # Convert zip-style forward slashes to Windows backslashes
        inner_win = inner_path.replace('/', '\\')
        source_dir = os.path.join(temp_dir, inner_win) if inner_win else temp_dir
        logger.info("Update source dir inside zip: %r → %s", inner_path, source_dir)

    if source_dir is not None:
        # Known source path — direct copy.  No ``pause`` here: the whole
        # script's stdout/stderr is redirected to a log file, so a pause
        # would hang waiting for stdin that never arrives.
        install_block = f"""\
        REM ── Copy extracted files over app directory ──
        echo Installing update from {source_dir}...
        if not exist "{source_dir}\\FAM Manager.exe" (
            echo ERROR: Expected FAM Manager.exe not found in extracted zip.
            echo Looked in: {source_dir}
            exit /b 1
        )
        xcopy /E /I /Q /Y "{source_dir}\\*" "{app_dir}\\"
        if %ERRORLEVEL% NEQ 0 (
            echo ERROR: Failed to copy update files. Your backup is at {backup_dir}
            exit /b 1
        )
"""
    else:
        # Legacy fallback — first top-level folder
        install_block = f"""\
        REM ── Copy extracted files over app directory (fallback) ──
        echo Installing update (legacy fallback)...
        for /D %%d in ("{temp_dir}\\*") do (
            xcopy /E /I /Q /Y "%%d\\*" "{app_dir}\\"
            goto DONE_COPY
        )
        :DONE_COPY
"""

    # Escape paths for batch script (double backslashes not needed
    # because we're writing raw strings)
    script = textwrap.dedent(f"""\
        @echo off
        REM FAM Market Manager — Update Script
        REM Generated automatically. Do not edit.

        REM Redirect all output to log file for post-mortem debugging
        call :MAIN > "{log_path}" 2>&1
        exit /b %ERRORLEVEL%

        :MAIN
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
        xcopy /E /I /Q /Y "{app_dir}\\*" "{backup_dir}\\"
        if %ERRORLEVEL% NEQ 0 (
            echo WARNING: Backup may be incomplete, but continuing with update.
        )
        echo Backup complete.

        REM ── Extract new version ──
        echo Extracting update...
        if exist "{temp_dir}" rmdir /s /q "{temp_dir}"
        mkdir "{temp_dir}"
        powershell -NoProfile -Command "Expand-Archive -Path '{_ps_single_quote(zip_path)}' -DestinationPath '{_ps_single_quote(temp_dir)}' -Force"
        if %ERRORLEVEL% NEQ 0 (
            echo.
            echo ERROR: Failed to extract update. Your current version is unchanged.
            echo The previous version backup is at: {backup_dir}
            exit /b 1
        )

{install_block}
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
        exit /b 0
    """)

    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(script)

    logger.info("Update script written to %s (log: %s)", script_path, log_path)
    return script_path
