"""Google Drive photo upload — uses REST API directly via google-auth.

No additional dependencies beyond what gspread already requires.
Uses AuthorizedSession from google-auth to make direct REST calls
to the Drive API v3.
"""

import logging
import os
import random
import re
import time as _time
from enum import Enum
from typing import Optional

logger = logging.getLogger('fam.sync.drive')

# Lazy imports (mirrors gsheets.py pattern)
_Credentials = None
_AuthorizedSession = None

DEFAULT_DRIVE_FOLDER_NAME = "FAM Market Manager Photos"
DRIVE_API_BASE = "https://www.googleapis.com"

# Maximum retries for transient Drive API errors
_MAX_RETRIES = 3

# How often the verification sweep may run.  Heavy-FMNP markets can
# have hundreds of photo URLs to verify per cycle; running that every
# 60 seconds consumes Drive API quota without catching anything new
# (Drive deletions are rare events).  Throttle to once every 10 minutes
# regardless of how many syncs fire.  New-photo upload is not throttled.
_VERIFICATION_MIN_INTERVAL_SEC = 600


class VerifyResult(Enum):
    """Tri-state result of a Drive file verification.

    Replaces the old boolean return that conflated "confirmed missing"
    with "couldn't verify right now" — a transient DNS or auth hiccup
    would cause the caller to CLEAR the URL from the DB, producing a
    spurious re-upload on the next sync (the 'Drive re-upload storm').
    """

    EXISTS = 'exists'                      # verified present and not trashed
    TRASHED_OR_MISSING = 'missing'         # verified 404 or trashed=true
    UNKNOWN = 'unknown'                    # network/auth error — do not clear


def _drive_retry(fn, max_retries=_MAX_RETRIES, label="Drive API"):
    """Retry *fn* with exponential back-off on transient Drive errors.

    Handles HTTP 429 (rate limit), 500/502/503 (server errors),
    and network-level failures (ConnectionError, TimeoutError).
    """
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            is_retryable = (
                status in (429, 500, 502, 503)
                or isinstance(exc, (ConnectionError, TimeoutError, OSError))
            )
            if is_retryable and attempt < max_retries - 1:
                wait = (2 ** attempt) + random.uniform(0.5, 2.0)
                logger.warning(
                    "%s transient error (status=%s, %s), retrying in %.1fs "
                    "(attempt %d/%d)…",
                    label, status, type(exc).__name__,
                    wait, attempt + 1, max_retries,
                )
                _time.sleep(wait)
            else:
                raise


def get_drive_folder_name() -> str:
    """Return the configured Drive folder name, or the default."""
    from fam.utils.app_settings import get_setting
    return get_setting('drive_photos_folder_name') or DEFAULT_DRIVE_FOLDER_NAME


def _ensure_imports():
    """Import google-auth classes on first use."""
    global _Credentials, _AuthorizedSession
    if _Credentials is None:
        from google.oauth2.service_account import Credentials
        from google.auth.transport.requests import AuthorizedSession
        _Credentials = Credentials
        _AuthorizedSession = AuthorizedSession


def _get_session():
    """Create an authorized session using the existing service account credentials."""
    _ensure_imports()
    from fam.sync.gsheets import _get_credentials_path
    creds_path = _get_credentials_path()
    if not os.path.isfile(creds_path):
        raise FileNotFoundError(f"Credentials not found: {creds_path}")

    scopes = ['https://www.googleapis.com/auth/drive']
    creds = _Credentials.from_service_account_file(creds_path, scopes=scopes)
    return _AuthorizedSession(creds)


def validate_drive_connection() -> tuple[bool, str]:
    """Test that Drive API auth works. Returns (success, message)."""
    try:
        _ensure_imports()
        session = _get_session()
        # Lightweight "About" call — confirms auth without side effects
        resp = session.get(
            f"{DRIVE_API_BASE}/drive/v3/about",
            params={'fields': 'user(emailAddress)'}
        )
        resp.raise_for_status()
        email = resp.json().get('user', {}).get('emailAddress', 'unknown')
        return True, f"Drive connected ({email})"
    except ImportError:
        return False, "google-auth not installed"
    except FileNotFoundError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Drive connection failed: {e}"


def _get_or_create_folder(session, folder_name: str) -> str:
    """Find or create a Drive folder. Returns the folder ID."""
    # Search for existing folder
    query = (f"name = '{folder_name}' and "
             f"mimeType = 'application/vnd.google-apps.folder' and "
             f"trashed = false")

    def _search():
        r = session.get(
            f"{DRIVE_API_BASE}/drive/v3/files",
            params={'q': query, 'fields': 'files(id,name)', 'spaces': 'drive',
                    'corpora': 'allDrives',
                    'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})
        r.raise_for_status()
        return r

    resp = _drive_retry(_search, label="Drive folder search")
    files = resp.json().get('files', [])
    if files:
        return files[0]['id']

    # Create folder
    metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }

    def _create():
        r = session.post(
            f"{DRIVE_API_BASE}/drive/v3/files",
            json=metadata,
            params={'fields': 'id', 'supportsAllDrives': 'true'})
        r.raise_for_status()
        return r

    resp = _drive_retry(_create, label="Drive folder create")
    folder_id = resp.json()['id']
    logger.info("Created Drive folder '%s' (ID: %s)", folder_name, folder_id)
    return folder_id


def _sanitize_drive_name(name: str, max_length: int = 100) -> str:
    """Sanitize a string for use as a Drive folder or file name."""
    cleaned = re.sub(r'[/\\:*?"<>|]', '_', name)
    cleaned = re.sub(r'[_\s]+', ' ', cleaned).strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip()
    return cleaned or 'Unknown'


def _get_or_create_subfolder(session, parent_id: str, folder_name: str) -> str:
    """Find or create a subfolder within a specific parent. Returns folder ID."""
    safe_name = _sanitize_drive_name(folder_name)

    query = (f"name = '{safe_name}' and "
             f"'{parent_id}' in parents and "
             f"mimeType = 'application/vnd.google-apps.folder' and "
             f"trashed = false")

    def _search():
        r = session.get(
            f"{DRIVE_API_BASE}/drive/v3/files",
            params={'q': query, 'fields': 'files(id,name)', 'spaces': 'drive',
                    'corpora': 'allDrives',
                    'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})
        r.raise_for_status()
        return r

    resp = _drive_retry(_search, label="Drive subfolder search")
    files = resp.json().get('files', [])
    if files:
        logger.debug("Found existing subfolder '%s' under %s (ID: %s)",
                      safe_name, parent_id, files[0]['id'])
        return files[0]['id']

    metadata = {
        'name': safe_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_id],
    }

    def _create():
        r = session.post(
            f"{DRIVE_API_BASE}/drive/v3/files",
            json=metadata,
            params={'fields': 'id', 'supportsAllDrives': 'true'})
        r.raise_for_status()
        return r

    resp = _drive_retry(_create, label="Drive subfolder create")
    folder_id = resp.json()['id']
    logger.info("Created subfolder '%s' under %s (ID: %s)",
                safe_name, parent_id, folder_id)
    return folder_id


def _resolve_entry_folder(session, root_folder_id: str, market_name: str,
                          payment_type: str, folder_cache: dict) -> str:
    """Resolve Root > Market Name > Payment Type folder, using cache."""
    cache_key = (market_name, payment_type)
    if cache_key in folder_cache:
        return folder_cache[cache_key]

    market_cache_key = (market_name, None)
    if market_cache_key in folder_cache:
        market_folder_id = folder_cache[market_cache_key]
    else:
        market_folder_id = _get_or_create_subfolder(
            session, root_folder_id, market_name)
        folder_cache[market_cache_key] = market_folder_id

    payment_folder_id = _get_or_create_subfolder(
        session, market_folder_id, payment_type)
    folder_cache[cache_key] = payment_folder_id
    logger.info("Resolved folder: %s/%s -> %s", market_name, payment_type,
                payment_folder_id)
    return payment_folder_id


def _make_fmnp_filename(entry: dict, photo_index: int, total_photos: int,
                        ext: str) -> str:
    """Build a traceable filename for an FMNP photo.

    Format: FMNP_{id}_{vendor}_{date}[_N].ext
    """
    entry_id = entry['id']
    vendor = _sanitize_drive_name(entry.get('vendor_name', 'unknown'), 40)
    date = entry.get('market_day_date', '').replace('-', '')
    base = f"FMNP_{entry_id}_{vendor}_{date}"
    if total_photos > 1:
        base += f"_{photo_index + 1}"
    return f"{base}{ext}"


def _make_payment_filename(entry: dict, photo_index: int, total_photos: int,
                           ext: str) -> str:
    """Build a traceable filename for a payment photo.

    Format: {txn_id}_{vendor}_{method}[_N].ext
    """
    txn_id = entry.get('fam_transaction_id', f"PLI_{entry['id']}")
    vendor = _sanitize_drive_name(entry.get('vendor_name', 'unknown'), 40)
    method = _sanitize_drive_name(entry.get('method_name_snapshot', 'unknown'), 20)
    base = f"{txn_id}_{vendor}_{method}"
    if total_photos > 1:
        base += f"_{photo_index + 1}"
    return f"{base}{ext}"



def _verify_file_in_drive(session, file_id: str) -> VerifyResult:
    """Verify that a file exists in Drive and is not trashed.

    Returns one of:
        VerifyResult.EXISTS             — confirmed present and not trashed
        VerifyResult.TRASHED_OR_MISSING — 404 or trashed=true — clear URL
        VerifyResult.UNKNOWN            — could not verify right now
                                          (network, auth, rate limit, 5xx)
                                          callers MUST NOT treat as missing

    Critical invariant: UNKNOWN results must never cause a caller to clear
    a URL from the database.  Treating a transient network failure as
    "file missing" causes a re-upload storm on flaky Wi-Fi — the bug
    that prompted this fix in v1.9.7.
    """
    # Identify network errors precisely so we can log them quietly.
    # A bare except for ImportError makes this work whether or not the
    # requests/google-auth packages are importable at test time.
    try:
        import requests.exceptions as _rexc
        _network_error_types: tuple = (_rexc.ConnectionError, _rexc.Timeout)
    except ImportError:
        _network_error_types = ()
    try:
        import google.auth.exceptions as _gauthexc
        _network_error_types = _network_error_types + (_gauthexc.TransportError,)
    except ImportError:
        pass

    try:
        resp = session.get(
            f"{DRIVE_API_BASE}/drive/v3/files/{file_id}",
            params={'fields': 'id,name,size,trashed',
                    'supportsAllDrives': 'true'}
        )
    except _network_error_types as e:
        # Offline, DNS hiccup, IPv6 flap, handshake reset — quiet WARN,
        # no traceback.  Caller will retry on the next cycle.
        logger.warning(
            "Drive verification skipped (network): file=%s error=%s",
            file_id, type(e).__name__)
        return VerifyResult.UNKNOWN
    except Exception:
        # Unexpected error class — keep the traceback at ERROR for
        # diagnostic value, but still return UNKNOWN so the caller
        # does not clear the URL.
        logger.exception(
            "Drive verification unexpected error for file %s", file_id)
        return VerifyResult.UNKNOWN

    if resp.status_code == 200:
        info = resp.json()
        if info.get('trashed'):
            logger.warning(
                "Drive file %s (%s) is in Trash — treating as deleted",
                file_id, info.get('name'))
            return VerifyResult.TRASHED_OR_MISSING
        logger.info("Drive verification OK: %s (size=%s)",
                    info.get('name'), info.get('size'))
        return VerifyResult.EXISTS

    if resp.status_code == 404:
        logger.warning("Drive file %s not found (404) — treating as deleted",
                       file_id)
        return VerifyResult.TRASHED_OR_MISSING

    if resp.status_code in (401, 403):
        # Auth expired, scope changed, file access revoked.  Do NOT clear —
        # if it's a transient auth issue a re-upload won't help, and if
        # it's a permanent permission loss the operator needs to fix the
        # service-account config, not silently re-upload.
        logger.warning(
            "Drive verification auth issue %d for file %s — skipping",
            resp.status_code, file_id)
        return VerifyResult.UNKNOWN

    if resp.status_code == 429 or resp.status_code >= 500:
        # Rate limited or Drive is having a bad day.  Retry next cycle.
        logger.warning(
            "Drive verification %d for file %s — will retry next cycle",
            resp.status_code, file_id)
        return VerifyResult.UNKNOWN

    # Any other status — be conservative, do not clear.
    logger.error("Drive verification unexpected status %d for file %s — %s",
                 resp.status_code, file_id, resp.text[:200])
    return VerifyResult.UNKNOWN


def _verify_folder_access(session, folder_id: str) -> tuple[bool, str]:
    """Verify the service account can access the target folder.

    Returns (success, message).
    """
    resp = session.get(
        f"{DRIVE_API_BASE}/drive/v3/files/{folder_id}",
        params={'fields': 'id,name,mimeType,capabilities',
                'supportsAllDrives': 'true'}
    )
    if resp.status_code == 200:
        info = resp.json()
        name = info.get('name', 'unknown')
        can_edit = info.get('capabilities', {}).get('canEdit', False)
        can_add = info.get('capabilities', {}).get('canAddChildren', False)
        if can_add or can_edit:
            logger.info("Folder access OK: '%s' (canEdit=%s, canAddChildren=%s)",
                        name, can_edit, can_add)
            return True, f"Folder '{name}' accessible"
        else:
            msg = (f"Folder '{name}' found but service account lacks write access. "
                   f"Please share the folder as 'Editor' with the service account.")
            logger.error(msg)
            return False, msg
    elif resp.status_code == 404:
        msg = (f"Folder ID '{folder_id}' not found. "
               f"Either the folder was deleted or the service account "
               f"doesn't have access. Please re-share the folder with "
               f"the service account as 'Editor'.")
        logger.error(msg)
        return False, msg
    else:
        msg = f"Folder access check failed: HTTP {resp.status_code} — {resp.text}"
        logger.error(msg)
        return False, msg


def upload_photo(local_path: str, filename: str,
                 _session=None, _folder_id=None) -> Optional[str]:
    """Upload a photo to Drive and return the shareable view URL.

    Returns the URL like 'https://drive.google.com/file/d/{id}/view'
    or None if upload fails.  Includes post-upload verification to
    confirm the file actually exists in Drive.

    *_session* and *_folder_id* can be passed to reuse an existing
    session/folder (avoids re-creating per photo in batch uploads).
    """
    try:
        _ensure_imports()
        session = _session or _get_session()

        # Get the photos folder ID
        from fam.utils.app_settings import get_setting, set_setting
        folder_id = _folder_id or get_setting('drive_photos_folder_id')
        if not folder_id:
            # Try to find/create folder by name as fallback
            folder_name = get_drive_folder_name()
            folder_id = _get_or_create_folder(session, folder_name)
            set_setting('drive_photos_folder_id', folder_id)
            logger.info("Auto-resolved Drive folder ID: %s", folder_id)

        # Determine MIME type
        ext = os.path.splitext(local_path)[1].lower()
        mime_map = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png', '.bmp': 'image/bmp',
            '.gif': 'image/gif',
        }
        mime_type = mime_map.get(ext, 'application/octet-stream')

        # Verify local file exists and has content
        if not os.path.isfile(local_path):
            logger.error("Local photo file not found: %s", local_path)
            return None
        file_size = os.path.getsize(local_path)
        if file_size == 0:
            logger.error("Local photo file is empty: %s", local_path)
            return None

        # File metadata
        metadata = {
            'name': filename,
            'parents': [folder_id],
        }

        # Initiate resumable upload (supportsAllDrives for shared folders)
        logger.info("Uploading %s (%d bytes) to Drive folder %s",
                     filename, file_size, folder_id)

        def _initiate():
            r = session.post(
                f"{DRIVE_API_BASE}/upload/drive/v3/files",
                params={'uploadType': 'resumable', 'fields': 'id',
                        'supportsAllDrives': 'true'},
                json=metadata,
                headers={'X-Upload-Content-Type': mime_type})
            r.raise_for_status()
            return r

        resp = _drive_retry(_initiate, label="Drive upload initiate")
        upload_url = resp.headers['Location']

        # Upload file content
        def _upload_content():
            with open(local_path, 'rb') as f:
                r = session.put(
                    upload_url,
                    data=f,
                    headers={
                        'Content-Type': mime_type,
                        'Content-Length': str(file_size),
                    })
            # Check for storage quota before generic raise_for_status
            if r.status_code == 403 and 'storageQuotaExceeded' in r.text:
                raise PermissionError(
                    "Service account has no storage quota. "
                    "Please use a Shared Drive instead of a regular folder. "
                    "In Google Drive → Shared drives → create one, add the "
                    "service account as Content Manager, then update the "
                    "folder ID in Settings."
                )
            r.raise_for_status()
            return r

        resp = _drive_retry(_upload_content, label="Drive upload content")
        file_id = resp.json()['id']

        # Files inherit the parent folder's sharing permissions.

        # Post-upload verification — confirm file exists in Drive.
        # Only EXISTS counts as successful upload; TRASHED_OR_MISSING means
        # something actually went wrong with the upload.  UNKNOWN (network
        # error verifying) is treated optimistically: the upload API said
        # it succeeded, so we trust that and skip re-verification.  The
        # next sync's verification pass will catch it if the upload was
        # in fact corrupt.
        post_result = _verify_file_in_drive(session, file_id)
        if post_result == VerifyResult.TRASHED_OR_MISSING:
            logger.error(
                "Upload appeared to succeed but verification reports the "
                "file missing/trashed for %s (file_id=%s)",
                filename, file_id)
            return None
        elif post_result == VerifyResult.UNKNOWN:
            logger.warning(
                "Post-upload verification inconclusive for %s (file_id=%s) — "
                "upload API reported success; will re-verify on next sync",
                filename, file_id)

        # Build shareable URL
        view_url = f"https://drive.google.com/file/d/{file_id}/view"
        logger.info("Uploaded + verified photo: %s -> %s", filename, view_url)
        return view_url

    except ImportError:
        logger.error("google-auth not installed — cannot upload to Drive")
        return None
    except FileNotFoundError as e:
        logger.error("Drive upload failed — credentials missing: %s", e)
        return None
    except PermissionError:
        raise  # Let storageQuotaExceeded bubble up to stop the batch
    except Exception:
        logger.exception("Drive photo upload failed for %s", local_path)
        return None


def _extract_file_id(url: str) -> Optional[str]:
    """Extract the Drive file ID from a shareable URL.

    Handles: https://drive.google.com/file/d/{FILE_ID}/view
    Returns None if the URL doesn't match the expected format.
    """
    if not url:
        return None
    m = re.search(r'/file/d/([^/]+)', url)
    return m.group(1) if m else None


def _rename_file_in_drive(session, file_id: str, new_name: str) -> bool:
    """Rename a file in Drive. Returns True on success."""
    try:
        resp = session.patch(
            f"{DRIVE_API_BASE}/drive/v3/files/{file_id}",
            json={'name': new_name},
            params={'supportsAllDrives': 'true'}
        )
        if resp.status_code == 200:
            logger.info("Renamed Drive file %s -> %s", file_id, new_name)
            return True
        logger.error("Rename failed for %s: HTTP %d — %s",
                      file_id, resp.status_code, resp.text)
        return False
    except Exception:
        logger.exception("Error renaming Drive file %s", file_id)
        return False


def _get_file_name_in_drive(session, file_id: str) -> Optional[str]:
    """Get the current filename of a Drive file. Returns None if not found."""
    try:
        resp = session.get(
            f"{DRIVE_API_BASE}/drive/v3/files/{file_id}",
            params={'fields': 'name', 'supportsAllDrives': 'true'}
        )
        if resp.status_code == 200:
            return resp.json().get('name')
        return None
    except Exception:
        logger.exception("Error getting filename for %s", file_id)
        return None


def _verification_throttled() -> bool:
    """Return True if the verification sweep ran recently and should skip
    this cycle.

    Verification is throttled to at most one pass per
    ``_VERIFICATION_MIN_INTERVAL_SEC`` (default 10 minutes) because:

    - Drive deletions / trash events are rare (hours/days between),
      so 10-minute latency to detect them is fine.
    - Heavy-FMNP markets can have 200+ photo URLs; verifying on every
      60-second sync burns Drive API quota for no practical benefit.
    - Upload of *new* photos is NOT throttled — that still runs every
      sync so freshly-added receipts upload promptly.
    """
    from fam.utils.app_settings import get_setting
    raw = get_setting('drive_verification_last_run')
    if not raw:
        return False
    try:
        last = float(raw)
    except (ValueError, TypeError):
        return False
    age = _time.time() - last
    if age < _VERIFICATION_MIN_INTERVAL_SEC:
        logger.info(
            "Drive URL verification throttled — last run %.0fs ago "
            "(interval=%ds); upload of new photos continues normally",
            age, _VERIFICATION_MIN_INTERVAL_SEC)
        return True
    return False


def _mark_verification_complete() -> None:
    """Record that the verification sweep just finished so the throttle
    can skip the next near-term call."""
    from fam.utils.app_settings import set_setting
    set_setting('drive_verification_last_run', str(_time.time()))


def _verify_and_clear_dead_urls(session) -> int:
    """Check all entries with Drive URLs and clear any that are confirmed
    missing or trashed.

    Critical correctness property: only ``TRASHED_OR_MISSING`` results
    cause a URL to be cleared.  ``UNKNOWN`` (network, auth, 5xx) leaves
    the URL in place so the next verification cycle can re-check — this
    is the fix for the 'Drive re-upload storm' bug where a transient
    DNS hiccup during verification would spuriously clear every URL in
    its path and trigger mass re-uploads on the next sync.

    Rate-limited via :func:`_verification_throttled`.  Returns the number
    of URLs cleared; returns 0 when throttled.
    """
    if _verification_throttled():
        return 0

    from fam.models.fmnp import (get_fmnp_entries_with_drive_urls,
                                  update_fmnp_photo_drive_url)
    from fam.models.transaction import (get_payment_items_with_drive_urls,
                                         update_payment_photo_drive_url)
    from fam.models.photo_hash import delete_photo_hash_by_url
    from fam.utils.photo_paths import parse_photo_paths, encode_photo_paths

    cleared = 0

    # ── FMNP entries ──
    fmnp_entries = get_fmnp_entries_with_drive_urls()
    for entry in fmnp_entries:
        urls = parse_photo_paths(entry['photo_drive_url'])
        live_urls = []
        changed = False
        for url in urls:
            file_id = _extract_file_id(url)
            if not file_id:
                # Malformed URL — drop it
                logger.warning("FMNP entry %d: unparseable Drive URL %s — "
                               "will re-upload", entry['id'], url)
                delete_photo_hash_by_url(url)
                changed = True
                cleared += 1
                continue
            result = _verify_file_in_drive(session, file_id)
            if result == VerifyResult.EXISTS:
                live_urls.append(url)
            elif result == VerifyResult.TRASHED_OR_MISSING:
                logger.warning("FMNP entry %d: Drive file missing for %s — "
                               "will re-upload", entry['id'], url)
                delete_photo_hash_by_url(url)
                changed = True
                cleared += 1
            else:
                # UNKNOWN — preserve the URL, try again next cycle.  This
                # is the critical branch: we MUST NOT clear on UNKNOWN or
                # we'd re-upload every photo on every network flap.
                live_urls.append(url)
        if changed:
            new_val = encode_photo_paths(live_urls) if live_urls else None
            update_fmnp_photo_drive_url(entry['id'], new_val)

    # ── Payment line items ──
    payment_items = get_payment_items_with_drive_urls()
    for item in payment_items:
        urls = parse_photo_paths(item['photo_drive_url'])
        live_urls = []
        changed = False
        for url in urls:
            file_id = _extract_file_id(url)
            if not file_id:
                logger.warning("Payment item %d: unparseable Drive URL %s — "
                               "will re-upload", item['id'], url)
                delete_photo_hash_by_url(url)
                changed = True
                cleared += 1
                continue
            result = _verify_file_in_drive(session, file_id)
            if result == VerifyResult.EXISTS:
                live_urls.append(url)
            elif result == VerifyResult.TRASHED_OR_MISSING:
                logger.warning("Payment item %d: Drive file missing for %s — "
                               "will re-upload", item['id'], url)
                delete_photo_hash_by_url(url)
                changed = True
                cleared += 1
            else:
                # UNKNOWN — preserve the URL.  Same reasoning as FMNP.
                live_urls.append(url)
        if changed:
            new_val = encode_photo_paths(live_urls) if live_urls else None
            update_payment_photo_drive_url(item['id'], new_val)

    if cleared:
        logger.info("Cleared %d dead Drive URLs (will re-upload)", cleared)
    # Mark completion so the throttle can skip the next near-term call.
    _mark_verification_complete()
    return cleared


def _process_voided_photos(session) -> int:
    """Rename Drive photos for voided/deleted entries to VOID_ prefix.

    Returns the number of files renamed.
    """
    from fam.models.fmnp import get_deleted_fmnp_with_photos
    from fam.models.transaction import get_voided_payment_photos
    from fam.utils.photo_paths import parse_photo_paths

    renamed = 0

    # ── Deleted FMNP entries ──
    deleted_fmnp = get_deleted_fmnp_with_photos()
    for entry in deleted_fmnp:
        urls = parse_photo_paths(entry['photo_drive_url'])
        for url in urls:
            file_id = _extract_file_id(url)
            if not file_id:
                continue
            name = _get_file_name_in_drive(session, file_id)
            if name is None:
                continue  # File already deleted from Drive
            if not name.startswith('VOID_'):
                if _rename_file_in_drive(session, file_id, f"VOID_{name}"):
                    renamed += 1

    # ── Voided transaction payment items ──
    voided_payments = get_voided_payment_photos()
    for item in voided_payments:
        urls = parse_photo_paths(item['photo_drive_url'])
        for url in urls:
            file_id = _extract_file_id(url)
            if not file_id:
                continue
            name = _get_file_name_in_drive(session, file_id)
            if name is None:
                continue
            if not name.startswith('VOID_'):
                if _rename_file_in_drive(session, file_id, f"VOID_{name}"):
                    renamed += 1

    if renamed:
        logger.info("Renamed %d voided/deleted photos to VOID_ prefix", renamed)
    return renamed


def _upload_entries(entries, update_fn, source_label: str,
                    session=None, root_folder_id=None,
                    folder_cache=None, filename_fn=None,
                    upload_cache=None, hash_cache=None) -> tuple[int, int]:
    """Upload pending photos for a list of entries from any source table.

    *entries*: list of dicts with 'id', 'photo_path', 'photo_drive_url'
               plus context fields (market_name, vendor_name, etc.)
    *update_fn*: callable(entry_id, encoded_urls) to persist Drive URLs
    *source_label*: "FMNP" or "Payment" for logging
    *root_folder_id*: root Drive folder for the hierarchy
    *folder_cache*: shared dict for subfolder ID caching
    *filename_fn*: callable(entry, idx, total, ext) -> str
    *upload_cache*: shared dict mapping rel_path → drive_url, used to
                    avoid uploading the same local file more than once
                    when multiple entries reference the same photo
    *hash_cache*: shared dict mapping content_hash → drive_url, used to
                  catch duplicate content even when files have different
                  names (persisted to DB across sync cycles)

    Returns (uploaded_count, failed_count).
    """
    from fam.utils.photo_storage import get_photo_full_path, photo_exists
    from fam.utils.photo_storage import compute_file_hash
    from fam.utils.photo_paths import parse_photo_paths, encode_photo_paths
    from fam.models.photo_hash import store_photo_hash

    if upload_cache is None:
        upload_cache = {}
    if hash_cache is None:
        hash_cache = {}

    uploaded = 0
    failed = 0

    for entry in entries:
        local_paths = parse_photo_paths(entry['photo_path'])
        existing_urls = parse_photo_paths(entry.get('photo_drive_url'))
        already_done = len(existing_urls)

        logger.info("[%s] Entry %d: %d local paths, %d already uploaded, raw=%r",
                    source_label, entry['id'], len(local_paths),
                    already_done, entry['photo_path'])

        if already_done >= len(local_paths):
            continue

        # Resolve target subfolder: Root > Market Name > Payment Type
        market_name = entry.get('market_name', 'Unknown Market')
        if source_label == 'FMNP':
            payment_type = 'FMNP'
        else:
            payment_type = entry.get('method_name_snapshot', 'Other')
        target_folder_id = _resolve_entry_folder(
            session, root_folder_id, market_name, payment_type,
            folder_cache)

        # Upload remaining photos
        all_urls = list(existing_urls)
        for idx, rel_path in enumerate(local_paths[already_done:],
                                       start=already_done):
            if not photo_exists(rel_path):
                logger.warning("[%s] Photo file missing for entry %d: %s",
                               source_label, entry['id'], rel_path)
                failed += 1
                continue

            # Layer 1: reuse URL if this exact rel_path was already uploaded
            if rel_path in upload_cache:
                cached_url = upload_cache[rel_path]
                logger.info("[%s] Entry %d: reusing cached URL for %s",
                            source_label, entry['id'], rel_path)
                all_urls.append(cached_url)
                uploaded += 1
                continue

            full_path = get_photo_full_path(rel_path)

            # Layer 2: content hash — catches identical content under
            # different filenames, and persists across sync cycles
            try:
                content_hash = compute_file_hash(full_path)
            except OSError:
                logger.warning("[%s] Cannot hash file for entry %d: %s",
                               source_label, entry['id'], rel_path)
                failed += 1
                continue

            if content_hash in hash_cache:
                cached_url = hash_cache[content_hash]
                upload_cache[rel_path] = cached_url
                logger.info("[%s] Entry %d: content hash match for %s",
                            source_label, entry['id'], rel_path)
                all_urls.append(cached_url)
                uploaded += 1
                continue

            ext = os.path.splitext(rel_path)[1].lower() or '.jpg'

            # Generate traceable filename
            filename = filename_fn(entry, idx, len(local_paths), ext)

            try:
                url = upload_photo(full_path, filename,
                                   _session=session,
                                   _folder_id=target_folder_id)
            except PermissionError:
                raise
            if url:
                all_urls.append(url)
                upload_cache[rel_path] = url
                hash_cache[content_hash] = url
                # Persist hash → URL to DB for cross-cycle dedup
                try:
                    store_photo_hash(content_hash, url)
                except Exception:
                    logger.warning("Failed to persist photo hash",
                                   exc_info=True)
                uploaded += 1
            else:
                failed += 1
                logger.error("[%s] Upload failed for entry %d photo: %s",
                             source_label, entry['id'], rel_path)

        # Save progress (even partial) so we don't re-upload on next cycle
        if len(all_urls) > already_done:
            encoded = encode_photo_paths(all_urls)
            update_fn(entry['id'], encoded)

    return uploaded, failed


def upload_pending_photos() -> dict:
    """Upload all photos (FMNP + Payment) that have local paths but incomplete Drive URLs.

    Handles both single-photo (legacy) and multi-photo entries.
    Called during the sync cycle.  Returns a dict with upload statistics:
        {'uploaded': int, 'failed': int, 'fmnp_uploaded': int, 'payment_uploaded': int}

    Existing Drive URLs are verified each cycle; dead ones are cleared so
    the corresponding local photos get re-uploaded automatically.

    Failures are logged and retried on the next cycle.
    """
    from fam.models.fmnp import get_pending_photo_uploads, update_fmnp_photo_drive_url
    from fam.models.transaction import (
        get_pending_payment_photo_uploads, update_payment_photo_drive_url
    )

    stats = {'uploaded': 0, 'failed': 0,
             'fmnp_uploaded': 0, 'payment_uploaded': 0}

    logger.info("Checking for pending photo uploads...")

    # ── Create session early — needed for verification steps ──
    _ensure_imports()
    session = _get_session()

    # ── Step 1: Verify existing Drive URLs and clear dead ones ──
    # If a photo was deleted from Drive, clear its URL so it re-uploads.
    try:
        cleared = _verify_and_clear_dead_urls(session)
        if cleared:
            stats['cleared_dead_urls'] = cleared
    except Exception:
        logger.exception("Error during Drive URL verification — continuing")

    # ── Step 2: Rename voided/deleted entry photos ──
    try:
        renamed = _process_voided_photos(session)
        if renamed:
            stats['voided_renamed'] = renamed
    except Exception:
        logger.exception("Error during VOID rename — continuing")

    # ── Step 3: Collect pending uploads (after clearing dead URLs) ──
    fmnp_pending = get_pending_photo_uploads()
    logger.info("FMNP pending photo entries: %d", len(fmnp_pending))

    payment_pending = get_pending_payment_photo_uploads()
    logger.info("Payment pending photo items: %d", len(payment_pending))

    if not fmnp_pending and not payment_pending:
        logger.info("No photos to upload")
        return stats

    from fam.utils.app_settings import get_setting, set_setting
    folder_id = get_setting('drive_photos_folder_id')
    if not folder_id:
        folder_name = get_drive_folder_name()
        folder_id = _get_or_create_folder(session, folder_name)
        set_setting('drive_photos_folder_id', folder_id)
        logger.info("Auto-resolved Drive folder ID: %s", folder_id)

    # Verify the service account can actually write to the folder
    ok, msg = _verify_folder_access(session, folder_id)
    if not ok:
        logger.error("Folder access check failed — aborting photo uploads: %s", msg)
        total_pending = len(fmnp_pending) + len(payment_pending)
        stats['failed'] = total_pending
        stats['error'] = msg
        return stats

    folder_cache = {}   # Shared across FMNP and Payment batches
    upload_cache = {}   # Dedup layer 1: rel_path → drive_url

    # Dedup layer 2: content hash → drive_url (persisted in DB)
    from fam.models.photo_hash import get_all_photo_hashes
    try:
        hash_cache = get_all_photo_hashes()
        if hash_cache:
            logger.info("Loaded %d photo content hashes from DB", len(hash_cache))
    except Exception:
        logger.warning("Could not load photo hashes — skipping hash dedup",
                       exc_info=True)
        hash_cache = {}

    # ── Upload FMNP photos ──
    try:
        if fmnp_pending:
            logger.info("Uploading %d FMNP entries...", len(fmnp_pending))
            up, fail = _upload_entries(
                fmnp_pending, update_fmnp_photo_drive_url, "FMNP",
                session=session, root_folder_id=folder_id,
                folder_cache=folder_cache,
                filename_fn=_make_fmnp_filename,
                upload_cache=upload_cache,
                hash_cache=hash_cache)
            stats['fmnp_uploaded'] = up
            stats['uploaded'] += up
            stats['failed'] += fail

        # ── Upload Payment photos ──
        if payment_pending:
            logger.info("Uploading %d payment items...", len(payment_pending))
            up, fail = _upload_entries(
                payment_pending, update_payment_photo_drive_url, "Payment",
                session=session, root_folder_id=folder_id,
                folder_cache=folder_cache,
                filename_fn=_make_payment_filename,
                upload_cache=upload_cache,
                hash_cache=hash_cache)
            stats['payment_uploaded'] = up
            stats['uploaded'] += up
            stats['failed'] += fail
    except PermissionError as e:
        logger.error("Storage quota error — use a Shared Drive: %s", e)
        total_pending = len(fmnp_pending) + len(payment_pending)
        stats['failed'] = total_pending - stats['uploaded']
        stats['error'] = str(e)
        return stats

    if stats['uploaded'] or stats['failed']:
        logger.info("Photo upload complete: %d succeeded, %d failed "
                     "(FMNP: %d, Payment: %d)",
                     stats['uploaded'], stats['failed'],
                     stats['fmnp_uploaded'], stats['payment_uploaded'])

    return stats
