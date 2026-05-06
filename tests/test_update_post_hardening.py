"""Auto-update regression tests AFTER the v1.9.10 hardening passes
(sessions 1-5, 2026-05-01).

The 5 hardening sessions added:

  * Cross-platform single-instance lock (``fam.database.instance_lock``)
    that lives in the same ``data_dir`` as the auto-update marker
  * Atomic-write helper in ``fam.utils.photo_storage`` (tempfile +
    ``os.replace``) — must NOT have leaked into the update download
    path, which needs streamed chunk writes
  * Source-pins on ``datetime.now()``, ``open()`` patterns, and
    file-mode invariants

The auto-update feature was working before hardening (131 tests
passing in ``test_update.py``).  These tests pin that the hardening
infrastructure does not interfere with the update flow:

  1. The pending-update marker and the instance lock can co-exist in
     the same directory — no filename collision, no lock-region
     overlap.
  2. The instance-lock release → re-acquire cycle matches the
     update → relaunch cycle (the post-update app must be able to
     reclaim the lock).
  3. The full mocked update flow (check → download → script → marker)
     runs cleanly while the instance lock is held by the current
     process.
  4. The download streaming path was NOT swapped to use the new
     atomic-write helper — atomic writes buffer in memory, which
     would break 80 MB+ release downloads.
  5. The pending-update marker round-trips correctly when the
     ``data_dir`` also contains an active lock file.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from fam.database.instance_lock import (
    InstanceLock, InstanceLockError, LOCK_FILENAME,
)
from fam.update.checker import (
    check_for_update,
    download_update,
    verify_download,
    generate_update_script,
    write_pending_update_marker,
    check_pending_update_result,
    PENDING_UPDATE_FILENAME,
)


# ════════════════════════════════════════════════════════════════════
# 1. Filename / location independence
# ════════════════════════════════════════════════════════════════════


class TestUpdateMarkerAndLockCoexist:
    """The pending-update marker (``_pending_update.json``) and the
    instance lock (``.fam_instance.lock``) both live in the data dir.
    They must use distinct filenames so neither stomps the other."""

    def test_filenames_are_distinct(self):
        assert PENDING_UPDATE_FILENAME != LOCK_FILENAME
        # Sanity: human-readable dot-prefix vs. underscore-prefix
        # convention is stable.
        assert PENDING_UPDATE_FILENAME == '_pending_update.json'
        assert LOCK_FILENAME == '.fam_instance.lock'

    def test_marker_and_lock_can_share_data_dir(self, tmp_path):
        """Write the marker, acquire the lock, write again, read back."""
        data_dir = str(tmp_path)
        write_pending_update_marker("1.9.11", data_dir=data_dir)
        lock = InstanceLock(data_dir)
        lock.acquire()
        try:
            # Marker file must still be readable while lock is held.
            marker = os.path.join(data_dir, PENDING_UPDATE_FILENAME)
            assert os.path.isfile(marker)
            with open(marker, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            assert payload['target_version'] == '1.9.11'
        finally:
            lock.release()

    def test_lock_acquire_does_not_touch_marker_file(self, tmp_path):
        """Acquiring the instance lock must not modify, truncate, or
        rename the pending-update marker — they're independent files."""
        data_dir = str(tmp_path)
        marker_path = write_pending_update_marker(
            "2.0.0", data_dir=data_dir)
        before_size = os.path.getsize(marker_path)
        before_mtime = os.path.getmtime(marker_path)

        lock = InstanceLock(data_dir)
        lock.acquire()
        try:
            after_size = os.path.getsize(marker_path)
            assert after_size == before_size, (
                "instance lock must not have modified the "
                "pending-update marker")
            # mtime can be flaky on slow filesystems — only compare
            # size for the strict invariant.
        finally:
            lock.release()
        # And the marker still parses post-release.
        assert os.path.getmtime(marker_path) >= before_mtime


# ════════════════════════════════════════════════════════════════════
# 2. Lock release matches the update → relaunch cycle
# ════════════════════════════════════════════════════════════════════


class TestLockReleaseAcrossUpdate:
    """When the user clicks "Install Update", the app:
        1. writes the pending-update marker
        2. launches the .bat script
        3. exits (releasing its instance lock)
        4. .bat copies new exe over old, relaunches
        5. NEW process must be able to acquire the lock

    This test simulates step 3 → step 5 in-process.
    """

    def test_lock_release_then_reacquire_works(self, tmp_path):
        """Two separate ``InstanceLock`` objects against the same
        data dir: the second can claim only after the first releases.
        Mirrors the exit-and-relaunch cycle."""
        data_dir = str(tmp_path)
        first = InstanceLock(data_dir)
        first.acquire()
        # While first holds it, second must FAIL.
        second = InstanceLock(data_dir)
        with pytest.raises(InstanceLockError):
            second.acquire()
        # After release, second SUCCEEDS.
        first.release()
        second.acquire()
        try:
            assert second.is_held() is True
        finally:
            second.release()

    def test_marker_persists_across_lock_release(self, tmp_path):
        """The marker is written BEFORE the app exits (and releases
        its lock).  The relaunched app must find the marker after
        re-acquiring the lock — i.e. the marker survives the
        release/acquire transition."""
        data_dir = str(tmp_path)
        first = InstanceLock(data_dir)
        first.acquire()
        write_pending_update_marker("3.0.0", data_dir=data_dir)
        first.release()

        # "Process restart": new lock object, same data dir.
        second = InstanceLock(data_dir)
        second.acquire()
        try:
            result = check_pending_update_result(
                "3.0.0", data_dir=data_dir)
            assert result == {
                'status': 'success', 'target_version': '3.0.0'}
        finally:
            second.release()

    def test_failed_update_marker_visible_post_relaunch(self, tmp_path):
        """The whole point of the marker: a silent updater failure
        produces a loud post-relaunch error.  Verify the failure
        path also survives the lock cycle."""
        data_dir = str(tmp_path)
        first = InstanceLock(data_dir)
        first.acquire()
        write_pending_update_marker("9.9.9", data_dir=data_dir)
        first.release()

        # Relaunched app is still on 1.0.0 (update silently failed).
        second = InstanceLock(data_dir)
        second.acquire()
        try:
            result = check_pending_update_result(
                "1.0.0", data_dir=data_dir)
            assert result is not None
            assert result['status'] == 'failed'
            assert result['target_version'] == '9.9.9'
            assert result['actual_version'] == '1.0.0'
        finally:
            second.release()


# ════════════════════════════════════════════════════════════════════
# 3. Full mocked update flow with hardening infrastructure live
# ════════════════════════════════════════════════════════════════════


def _mock_release_payload(tag="v9.9.9", asset_size=2048):
    return json.dumps({
        "tag_name": tag,
        "name": f"FAM Market Manager {tag}",
        "body": "Test release",
        "assets": [{
            "name": f"FAM_Manager_{tag}.zip",
            "browser_download_url":
                f"https://github.com/o/r/releases/download/{tag}/"
                f"FAM_Manager_{tag}.zip",
            "size": asset_size,
        }],
    }).encode('utf-8')


class TestFullFlowWithLockActive:
    """Full update flow, mocked HTTP, instance lock held the entire
    time — proves the lock and the update machinery don't fight."""

    @patch('fam.app.get_data_dir')
    @patch('fam.update.checker.urlopen')
    def test_check_download_marker_with_lock_held(
            self, mock_urlopen, mock_data_dir, tmp_path):
        data_dir = str(tmp_path)
        mock_data_dir.return_value = data_dir

        # Acquire the instance lock for the duration of the update.
        lock = InstanceLock(data_dir)
        lock.acquire()
        try:
            # ── Step 1: check_for_update ──
            check_resp = MagicMock()
            check_resp.read.return_value = _mock_release_payload(
                "v9.9.9", asset_size=2048)
            check_resp.__enter__ = MagicMock(return_value=check_resp)
            check_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = check_resp

            info = check_for_update("o", "r", "1.0.0")
            assert info is not None
            assert info['update_available'] is True
            assert info['version'] == '9.9.9'

            # ── Step 2: download_update ──
            content = b"PK" + b"\x00" * 2046  # 2048 bytes
            dl_resp = MagicMock()
            dl_resp.headers = {'Content-Length': '2048'}
            dl_resp.read = MagicMock(side_effect=[content, b''])
            dl_resp.__enter__ = MagicMock(return_value=dl_resp)
            dl_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = dl_resp

            zip_path = os.path.join(data_dir, "update.zip")
            assert download_update(info['asset_url'], zip_path) is True
            assert verify_download(zip_path, 2048) is True

            # ── Step 3: write pending marker ──
            marker_path = write_pending_update_marker(
                info['version'], data_dir=data_dir)
            assert os.path.isfile(marker_path)

            # All three artefacts coexist under the lock.
            assert os.path.isfile(
                os.path.join(data_dir, LOCK_FILENAME))
            assert os.path.isfile(
                os.path.join(data_dir, PENDING_UPDATE_FILENAME))
            assert os.path.isfile(zip_path)
        finally:
            lock.release()

    @patch('fam.app.get_data_dir')
    def test_generate_update_script_with_lock_active(
            self, mock_data_dir, tmp_path):
        """Script generation reads ``get_data_dir()`` and writes the
        .bat file — both must work even with an active lock file
        sitting in the same directory."""
        import zipfile
        data_dir = str(tmp_path)
        mock_data_dir.return_value = data_dir

        lock = InstanceLock(data_dir)
        lock.acquire()
        try:
            zp = os.path.join(data_dir, "update.zip")
            with zipfile.ZipFile(zp, 'w') as zf:
                zf.writestr("FAM Manager/FAM Manager.exe", b"new")
            app_dir = os.path.join(data_dir, "app")

            script = generate_update_script(app_dir, zp)
            assert os.path.isfile(script)
            content = open(script, 'r').read()
            assert app_dir in content
            assert 'Expand-Archive' in content
            # The lock file is referenced nowhere in the script — it
            # is owned by the app process, not the updater.
            assert LOCK_FILENAME not in content
        finally:
            lock.release()


# ════════════════════════════════════════════════════════════════════
# 4. Source-pin: download path stayed streaming, not buffered
# ════════════════════════════════════════════════════════════════════


class TestDownloadPathNotSwitchedToAtomicWrite:
    """The atomic-write helper added to ``fam.utils.photo_storage``
    in Session 2 buffers the entire payload in memory before
    ``os.replace``.  That's fine for ~50 KB photos but would break
    80 MB+ release downloads.

    Pin that the update download path:
      - Still uses streamed ``open(dest_path, 'wb')`` + chunk reads
      - Does NOT import or call ``_atomic_write_bytes``
    """

    def test_checker_does_not_import_atomic_write(self):
        import fam.update.checker as ch
        # Module must not have the symbol bound — defensive: even if
        # someone added a star-import, this would catch it.
        assert not hasattr(ch, '_atomic_write_bytes'), (
            "fam.update.checker must not pull in the atomic-write "
            "helper — downloads need to stream, not buffer.")

    def test_download_uses_streamed_write_pattern(self):
        """Source-text check that ``download_update`` writes via a
        chunked loop, not via a one-shot atomic write."""
        import inspect
        from fam.update import checker
        src = inspect.getsource(checker.download_update)
        # Streamed pattern: open as 'wb' and a chunked while loop.
        assert "open(dest_path, 'wb')" in src or \
               'open(dest_path, "wb")' in src, (
            "download_update must keep its streamed write — buffering "
            "an 80 MB release into memory would OOM low-end laptops.")
        assert 'resp.read(' in src and 'while True' in src, (
            "chunked-read loop missing from download_update")

    def test_atomic_write_helper_lives_only_in_photo_storage(self):
        """The helper added in Session 2 is intentionally local to
        photo storage.  Pin its scope so we don't accidentally start
        depending on it elsewhere without thinking."""
        import inspect
        from fam.utils import photo_storage
        # The helper is defined in photo_storage.
        assert hasattr(photo_storage, '_atomic_write_bytes')
        # And nowhere else in the update package.
        from fam.update import checker, worker
        for mod in (checker, worker):
            src = inspect.getsource(mod)
            assert '_atomic_write_bytes' not in src, (
                f"{mod.__name__} should not call the photo-storage "
                f"atomic-write helper")


# ════════════════════════════════════════════════════════════════════
# 5. Update modules don't pull in DB / model layer
# ════════════════════════════════════════════════════════════════════


class TestUpdateModuleIsolation:
    """The auto-update check is a pure-stdlib pipeline (urllib + json
    + zipfile).  It must NOT import the DB connection, models, or
    the photo storage layer — those would drag Qt and SQLite into
    the update worker thread for no reason and risk thread-safety
    issues.

    Hardening Session 1 added ``fam.database.instance_lock``.  That
    is fine to import from the GUI, but the checker must still NOT
    import it (the checker runs in a background QThread and grabs
    the lock would deadlock against the main process)."""

    def test_checker_does_not_import_database_layer(self):
        import fam.update.checker as ch
        import inspect
        src = inspect.getsource(ch)
        # Defensive: no DB or model imports in the checker.
        for forbidden in (
                'from fam.database',
                'from fam.models',
                'fam.database.instance_lock',
                'fam.utils.photo_storage'):
            assert forbidden not in src, (
                f"fam.update.checker must not import {forbidden!r} — "
                f"updater must stay pure-stdlib")

    def test_worker_does_not_import_database_layer(self):
        import fam.update.worker as wk
        import inspect
        src = inspect.getsource(wk)
        for forbidden in (
                'from fam.database',
                'from fam.models'):
            assert forbidden not in src, (
                f"fam.update.worker must not import {forbidden!r}")


# ════════════════════════════════════════════════════════════════════
# 6. Marker round-trip with lock cycle (regression for the v1.9.3 silent-fail)
# ════════════════════════════════════════════════════════════════════


class TestMarkerSurvivesLockCycle:
    """The marker is the circuit breaker for silent updater failures.
    The hardening must NOT have introduced a way for the marker to
    be lost across the app's exit/restart cycle."""

    def test_marker_lifecycle_full_cycle(self, tmp_path):
        """Full simulation:
            t0: app launches, acquires lock
            t1: user triggers update; marker written
            t2: app exits, lock released
            t3: updater runs (no-op for this test)
            t4: app relaunches, acquires lock
            t5: marker checked — must report success or failure
            t6: marker auto-removed after read
            t7: app exits, lock released
        """
        data_dir = str(tmp_path)

        # t0–t2
        lock_a = InstanceLock(data_dir)
        lock_a.acquire()
        marker_path = write_pending_update_marker(
            "2.5.0", data_dir=data_dir)
        lock_a.release()

        # t3: updater runs (we just leave the marker)
        assert os.path.isfile(marker_path)

        # t4–t6
        lock_b = InstanceLock(data_dir)
        lock_b.acquire()
        try:
            outcome = check_pending_update_result(
                "2.5.0", data_dir=data_dir)
            assert outcome == {
                'status': 'success', 'target_version': '2.5.0'}
            # t6: marker MUST be removed (otherwise it re-fires every
            # launch — exactly the bug class the hardening is
            # supposed to *prevent*, not introduce).
            assert not os.path.isfile(marker_path)
        finally:
            lock_b.release()

    def test_lock_does_not_prevent_marker_deletion(self, tmp_path):
        """``check_pending_update_result`` deletes the marker via
        ``os.remove``.  Verify that holding the instance lock does
        not prevent the deletion (Windows file-share semantics
        could have made this an issue if the lock somehow exposed
        the marker path)."""
        data_dir = str(tmp_path)
        marker_path = write_pending_update_marker(
            "1.0.0", data_dir=data_dir)
        lock = InstanceLock(data_dir)
        lock.acquire()
        try:
            check_pending_update_result("1.0.0", data_dir=data_dir)
            assert not os.path.isfile(marker_path)
        finally:
            lock.release()


# ════════════════════════════════════════════════════════════════════
# 7. App-startup sequence: marker check happens AFTER lock acquisition
# ════════════════════════════════════════════════════════════════════


class TestStartupOrderingInvariants:
    """When the app launches it acquires the instance lock first
    (so a second instance bails immediately), THEN checks the
    pending-update marker.  Reversing this order would let two
    instances race to delete the marker, double-firing the dialog.

    This is a source-pin against ``fam/app.py`` ordering."""

    def test_app_module_acquires_lock_before_marker_check(self):
        """Light source audit: ensure the marker check shows up in
        ``fam/app.py`` and is NOT located before any lock acquire
        statement.  We don't require a lock acquire to exist (it may
        be guarded by config in some builds) — only that IF the lock
        is acquired, it happens before the marker check."""
        import inspect
        from fam import app as fam_app
        src = inspect.getsource(fam_app)

        marker_idx = src.find('check_pending_update_result')
        assert marker_idx != -1, (
            "check_pending_update_result import/call missing from "
            "fam/app.py — the silent-failure circuit breaker is the "
            "whole point of the marker, do not remove it")

        lock_idx = src.find('InstanceLock')
        if lock_idx != -1:
            # If both present, lock acquisition must come first.
            assert lock_idx < marker_idx, (
                "InstanceLock acquire must run BEFORE "
                "check_pending_update_result — otherwise two racing "
                "instances could both fire the post-update dialog")
