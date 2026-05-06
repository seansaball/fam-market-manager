"""Single-instance lock for the data directory
(v1.9.10 follow-up, 2026-05-01).

Two FAM Market Manager processes pointing at the same DB file
are functionally safe under SQLite WAL — but they are
operationally a hazard: the audit log records ``device_id``
once at first launch, both instances stamp every audit row with
the SAME device id, and the cloud-sync composite-key collision
makes their rows overwrite each other on the shared sheet.

This module provides an ADVISORY file-lock helper.  The lock
isn't claimed during tests (so the test suite never collides
with a real running app) — only when the GUI launches.  It's
purely opt-in via ``acquire()``; the existing connection /
schema / model layer doesn't depend on it.

Cross-platform behavior:

  * **Windows** uses ``msvcrt.locking()`` (advisory byte-range
    lock).  The lock is released when the process exits; if the
    process is force-killed the OS releases it.
  * **POSIX** uses ``fcntl.flock()`` (advisory file lock).  Same
    process-exit cleanup semantics.

The lock file lives at ``<data_dir>/.fam_instance.lock`` and is
created on first acquire.  The file's contents are the PID of
the holder, written for human debugging only — the lock itself
is OS-managed.
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

LOCK_FILENAME = '.fam_instance.lock'


class InstanceLockError(RuntimeError):
    """Raised when another instance already holds the lock."""


class InstanceLock:
    """Advisory file lock scoped to a data directory.

    Usage::

        lock = InstanceLock(data_dir)
        try:
            lock.acquire()  # raises InstanceLockError on contention
        except InstanceLockError:
            sys.exit(1)
        # ... run app ...
        lock.release()

    Or as a context manager::

        with InstanceLock(data_dir):
            run_app()
    """

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        self._path = os.path.join(data_dir, LOCK_FILENAME)
        self._fh = None
        self._is_locked = False

    def acquire(self) -> None:
        """Try to acquire the lock.  Raises ``InstanceLockError``
        if another process holds it.  No-op if already held by
        this object."""
        if self._is_locked:
            return
        os.makedirs(self._data_dir, exist_ok=True)
        # Open in 'r+' if the file already exists, else 'w+'.
        # We deliberately AVOID 'a+' because Windows
        # ``msvcrt.locking()`` locks bytes starting at the
        # current file-pointer position — and 'a+' mode parks
        # the pointer at end-of-file, so two processes opening
        # at different file sizes would lock DIFFERENT bytes
        # and both think they hold the lock (they hold disjoint
        # byte ranges).  We always seek(0) before locking and
        # always lock byte 0 so the contention check is real.
        if os.path.exists(self._path):
            self._fh = open(self._path, 'r+', encoding='utf-8')
        else:
            self._fh = open(self._path, 'w+', encoding='utf-8')
        try:
            self._fh.seek(0)
            if sys.platform == 'win32':
                import msvcrt
                # Lock 1 byte starting at the CURRENT pointer
                # (we just seek'd to 0).  _LK_NBLCK fails
                # immediately if already locked.
                msvcrt.locking(
                    self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                # LOCK_EX | LOCK_NB — whole-file exclusive,
                # non-blocking.
                fcntl.flock(
                    self._fh.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as e:
            self._fh.close()
            self._fh = None
            raise InstanceLockError(
                f"Another FAM Market Manager instance is already "
                f"running with this data directory ({self._data_dir}). "
                f"Close the other instance first; running two copies "
                f"against the same DB corrupts cross-device "
                f"coordination on the shared Google Sheet."
            ) from e

        # Record our PID for debugging (the lock itself is OS-
        # managed; the file contents are advisory text only).
        # Write to position AFTER the locked byte so we don't
        # disturb the lock region on Windows (where the lock
        # is byte-range-specific).
        try:
            self._fh.seek(1)  # past the locked byte 0
            self._fh.truncate()
            self._fh.write(f"{os.getpid()}\n")
            self._fh.flush()
        except OSError:
            pass  # PID write is best-effort.

        self._is_locked = True
        logger.info(
            "Instance lock acquired at %s (pid=%d)",
            self._path, os.getpid())

    def release(self) -> None:
        """Release the lock.  No-op if not held."""
        if not self._is_locked:
            return
        try:
            self._fh.seek(0)
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            logger.debug(
                "Instance lock release: OS-level unlock raised "
                "(it may already be reclaimed); continuing",
                exc_info=True)
        finally:
            try:
                if self._fh is not None:
                    self._fh.close()
            except OSError:
                pass
            self._fh = None
            self._is_locked = False
        logger.info("Instance lock released at %s", self._path)

    def is_held(self) -> bool:
        return self._is_locked

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False
