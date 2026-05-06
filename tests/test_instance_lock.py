"""Tests for the cross-platform single-instance lock helper
(v1.9.10 follow-up, 2026-05-01).

The Windows-named-mutex check in ``fam/app.py::_ensure_single_instance``
covers the production-Windows case.  ``InstanceLock`` is a
cross-platform companion: data-dir-scoped (so two instances
pointed at the SAME data dir collide regardless of install
location), advisory file-lock based (so the OS reclaims it on
process kill), and tested.

These tests run on all platforms — Windows uses ``msvcrt.locking``,
POSIX uses ``fcntl.flock``.  Both are advisory; the lock blocks
cooperating processes that call ``acquire()``, not unrelated
filesystem readers.
"""

import os
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from fam.database.instance_lock import (
    InstanceLock, InstanceLockError, LOCK_FILENAME,
)


@pytest.fixture
def data_dir(tmp_path):
    return str(tmp_path / "data")


# ════════════════════════════════════════════════════════════════════
# 1. Single-process acquire/release
# ════════════════════════════════════════════════════════════════════


class TestBasicAcquireRelease:

    def test_acquire_creates_lock_file(self, data_dir):
        lock = InstanceLock(data_dir)
        lock.acquire()
        try:
            assert os.path.isfile(
                os.path.join(data_dir, LOCK_FILENAME))
            assert lock.is_held()
        finally:
            lock.release()

    def test_release_clears_held_state(self, data_dir):
        lock = InstanceLock(data_dir)
        lock.acquire()
        lock.release()
        assert not lock.is_held()

    def test_double_acquire_is_idempotent(self, data_dir):
        """Calling acquire() twice on the same object is a no-op
        (already held).  Doesn't raise, doesn't unlock."""
        lock = InstanceLock(data_dir)
        lock.acquire()
        try:
            lock.acquire()  # must not raise
            assert lock.is_held()
        finally:
            lock.release()

    def test_double_release_is_idempotent(self, data_dir):
        lock = InstanceLock(data_dir)
        lock.acquire()
        lock.release()
        lock.release()  # must not raise

    def test_context_manager_releases_on_exit(self, data_dir):
        with InstanceLock(data_dir) as lock:
            assert lock.is_held()
        # After the with-block, lock is released.
        # Verify another acquire works.
        with InstanceLock(data_dir):
            pass

    def test_pid_written_for_debug(self, data_dir):
        """Verify the PID landed in the lock file.  PID is
        written at offset 1 (past the locked byte 0 that the
        OS protects), so we read from byte 1 onward.  Reading
        post-release is safest cross-platform.

        The byte-0 padding is a no-op for human readers — they
        ``cat`` or ``type`` the file and see the PID after a
        single null byte.
        """
        lock = InstanceLock(data_dir)
        lock.acquire()
        lock.release()
        with open(
                os.path.join(data_dir, LOCK_FILENAME), 'rb') as f:
            content = f.read()
        # Strip the leading lock-byte placeholder.
        text = content[1:].decode(errors='replace').strip()
        assert text == str(os.getpid())


# ════════════════════════════════════════════════════════════════════
# 2. Same-process contention (sequential acquire/release works)
# ════════════════════════════════════════════════════════════════════


class TestSameProcessSequential:
    """In a single process, after release the lock can be re-
    acquired immediately.  This is the test-suite re-use case."""

    def test_acquire_after_release_succeeds(self, data_dir):
        lock1 = InstanceLock(data_dir)
        lock1.acquire()
        lock1.release()

        lock2 = InstanceLock(data_dir)
        lock2.acquire()
        try:
            assert lock2.is_held()
        finally:
            lock2.release()

    def test_different_data_dirs_dont_collide(self, tmp_path):
        """Two locks pointing at DIFFERENT data dirs are
        independent — both can be held at once."""
        dir_a = str(tmp_path / "a")
        dir_b = str(tmp_path / "b")
        with InstanceLock(dir_a):
            with InstanceLock(dir_b):
                pass


# ════════════════════════════════════════════════════════════════════
# 3. Cross-process contention (two instances against same dir)
# ════════════════════════════════════════════════════════════════════


_HOLDER_SCRIPT = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, {repo_root!r})
    from fam.database.instance_lock import InstanceLock
    data_dir = sys.argv[1]
    ready_path = sys.argv[2]
    release_path = sys.argv[3]
    lock = InstanceLock(data_dir)
    lock.acquire()
    open(ready_path, 'w').close()
    deadline = time.time() + 30
    while time.time() < deadline:
        if os.path.exists(release_path):
            break
        time.sleep(0.05)
    lock.release()
""").lstrip()


class TestCrossProcessContention:
    """The lock's whole point: a second process must NOT acquire
    while the first holds.  Uses ``subprocess.Popen`` to spawn
    a real second OS process (avoids ``multiprocessing`` re-
    importing the test module and creating fixture-state
    confusion)."""

    @pytest.mark.skipif(
        os.name not in ('nt', 'posix'),
        reason="only Windows + POSIX supported")
    def test_second_process_cannot_acquire(self, data_dir, tmp_path):
        ready = str(tmp_path / "ready")
        release = str(tmp_path / "release")
        repo_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))
        script = _HOLDER_SCRIPT.format(repo_root=repo_root)

        p = subprocess.Popen(
            [sys.executable, '-c', script, data_dir, ready, release],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            # Wait for subprocess to report lock-acquired.
            for _ in range(200):  # up to 20s
                if os.path.exists(ready):
                    break
                time.sleep(0.1)
            assert os.path.exists(ready), (
                f"subprocess never reported lock-acquired; stderr: "
                f"{p.communicate(timeout=2)[1].decode(errors='replace')}")

            # Try to acquire from THIS process — must fail.
            with pytest.raises(InstanceLockError):
                InstanceLock(data_dir).acquire()
        finally:
            open(release, 'w').close()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)

        # After subprocess released, this process can acquire.
        with InstanceLock(data_dir) as lock:
            assert lock.is_held()


# ════════════════════════════════════════════════════════════════════
# 4. App.py wires InstanceLock — production single-instance guard
# ════════════════════════════════════════════════════════════════════


class TestInstanceLockWiredIntoApp:
    """v2.0.1: ``fam.app.run`` uses :class:`InstanceLock` as the
    single-instance guard.  The previous implementation used a
    Windows kernel mutex — per-machine rather than per-data-folder —
    so two laptops pointing at the same shared `%APPDATA%` could both
    launch and clobber each other on the shared sheet.  The kernel
    mutex was replaced; this test source-pins the new wiring so a
    refactor that drops it doesn't silently re-introduce the old
    cross-laptop hazard."""

    def test_ensure_single_instance_uses_instance_lock(self):
        import inspect
        import fam.app
        src = inspect.getsource(fam.app)
        assert 'InstanceLock' in src, (
            "fam/app.py must use the file-based InstanceLock "
            "(per-data-folder) as its single-instance guard. "
            "The pre-v2.0.1 kernel mutex was per-machine and "
            "could not protect shared data folders.")
        assert '_ensure_single_instance' in src

    def test_app_calls_single_instance_before_db_init(self):
        """The check must run BEFORE any DB access — its whole
        point is to bail before the second instance touches
        anything."""
        import inspect
        import fam.app
        src = inspect.getsource(fam.app.run)
        idx_check = src.find('_ensure_single_instance(')
        idx_setdb = src.find('set_db_path(')
        idx_init = src.find('initialize_database()')
        assert idx_check >= 0
        assert idx_check < idx_setdb < idx_init, (
            "single-instance check must run first; got order "
            f"check={idx_check} set_db={idx_setdb} init={idx_init}")

    def test_kernel_mutex_no_longer_present(self):
        """Belt-and-suspenders: confirm the old mutex code path is
        gone, not just that InstanceLock is also imported."""
        import inspect
        import fam.app
        src = inspect.getsource(fam.app)
        assert 'CreateMutexW' not in src, (
            "Old per-machine kernel-mutex single-instance check "
            "must NOT coexist with InstanceLock — they would "
            "both bid on the lock with different semantics.")
        assert 'FAM_MarketManager_SingleInstance' not in src
