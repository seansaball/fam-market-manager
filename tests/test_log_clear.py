"""Tests for log-file clearing on Reset to Defaults.

The Reset to Defaults flow in Settings now invokes
``fam.utils.logging_config.clear_log_files()`` so the Error Log tab
doesn't keep showing pre-reset entries after the user wipes their
data.  These tests verify:

1. The active log file gets truncated.
2. Rotated backups (``.1``, ``.2``, ``.3``) get deleted.
3. The function is best-effort — a missing file is fine, a partially-
   failed cleanup still reports a sensible (ok, message) tuple.
4. After clearing, the ``fam`` logger can keep emitting cleanly — the
   detached handler reopens its stream lazily on the next ``emit``.
5. Source-level guard that ``settings_screen._reset_to_default`` calls
   ``clear_log_files`` (so future refactors don't accidentally drop
   the wiring).
"""

import importlib
import logging
import os
from pathlib import Path

import pytest

from fam.utils import logging_config
from fam.utils.logging_config import clear_log_files, setup_logging


# ── Helpers ─────────────────────────────────────────────────────

@pytest.fixture
def isolated_logger(tmp_path, monkeypatch):
    """Spin up a temp log directory with the real RotatingFileHandler
    pointed at it, and tear down cleanly afterwards.

    Yields the log file path.
    """
    # Reset the module-level cache so setup_logging picks up the temp dir
    monkeypatch.setattr(logging_config, '_log_path', None, raising=False)

    # Detach any handlers a prior test/session left on the 'fam' logger
    fam_logger = logging.getLogger('fam')
    saved_handlers = list(fam_logger.handlers)
    for h in saved_handlers:
        fam_logger.removeHandler(h)

    log_path = setup_logging(data_dir=str(tmp_path))
    try:
        yield Path(log_path)
    finally:
        # Tear down: detach our temp handler, restore originals
        for h in list(fam_logger.handlers):
            try:
                h.close()
            except Exception:
                pass
            fam_logger.removeHandler(h)
        for h in saved_handlers:
            fam_logger.addHandler(h)


# ── Active-log truncation ───────────────────────────────────────

class TestClearActiveLog:
    def test_truncates_existing_log(self, isolated_logger):
        log_path = isolated_logger
        logger = logging.getLogger('fam.test_log_clear')
        logger.error("This entry should be wiped by clear_log_files()")
        # Force the handler to flush so the bytes hit disk
        for h in logging.getLogger('fam').handlers:
            h.flush()

        assert log_path.stat().st_size > 0, \
            "Sanity check: log file should have content before clearing"

        ok, msg = clear_log_files()
        assert ok, f"clear_log_files reported failure: {msg!r}"
        assert log_path.exists(), "log file should still exist (just empty)"
        assert log_path.stat().st_size == 0, \
            "log file should be truncated to zero bytes"

    def test_missing_log_file_is_ok(self, isolated_logger):
        """If the log file doesn't exist yet, clear_log_files succeeds
        silently — there's nothing to do and that's fine."""
        log_path = isolated_logger
        # Detach handlers and remove the file before clearing
        for h in list(logging.getLogger('fam').handlers):
            h.close()
            logging.getLogger('fam').removeHandler(h)
        if log_path.exists():
            log_path.unlink()

        ok, msg = clear_log_files()
        assert ok
        # Either no message or a benign one — definitely no traceback
        assert 'Could not' not in msg

    def test_no_log_path_is_ok(self, monkeypatch):
        """When the module-level path was never set, the function must
        not crash — it returns (True, '') and does nothing."""
        monkeypatch.setattr(logging_config, '_log_path', None,
                            raising=False)
        # Replace get_log_path() so it returns falsy
        monkeypatch.setattr(logging_config, 'get_log_path',
                            lambda: '')
        ok, msg = clear_log_files()
        assert ok
        assert msg == ''


# ── Rotated backups ─────────────────────────────────────────────

class TestClearRotatedBackups:
    def test_deletes_rotated_backups(self, isolated_logger):
        log_path = isolated_logger
        # Simulate the RotatingFileHandler having rolled over a few times
        for i in range(1, 4):
            (log_path.parent / f"fam_manager.log.{i}").write_text(
                f"old log {i}\n", encoding='utf-8')

        ok, _ = clear_log_files()
        assert ok
        for i in range(1, 4):
            assert not (log_path.parent / f"fam_manager.log.{i}").exists(), \
                f"rotated backup .{i} should have been deleted"

    def test_deletes_extra_high_indexed_backups(self, isolated_logger):
        """Be generous about backupCount — if a previous version had
        more than 3 backups, those should still get cleaned up."""
        log_path = isolated_logger
        for i in (1, 2, 3, 4, 5, 6, 7, 8, 9):
            (log_path.parent / f"fam_manager.log.{i}").write_text(
                f"old log {i}\n", encoding='utf-8')

        ok, _ = clear_log_files()
        assert ok
        for i in (1, 2, 3, 4, 5, 6, 7, 8, 9):
            assert not (log_path.parent / f"fam_manager.log.{i}").exists()


# ── Logger keeps working after clear ────────────────────────────

class TestLoggingResumes:
    def test_logger_keeps_emitting_after_clear(self, isolated_logger):
        """After clear, the next log emit must reopen the handler's
        stream and write to the now-empty file.  This guards against
        the 'detached handler with stream=None never reopens' regression."""
        log_path = isolated_logger
        logger = logging.getLogger('fam.test_log_clear')

        logger.error("pre-clear entry")
        for h in logging.getLogger('fam').handlers:
            h.flush()
        assert log_path.stat().st_size > 0

        ok, _ = clear_log_files()
        assert ok
        assert log_path.stat().st_size == 0

        # The handler should reopen on this next emit
        logger.error("post-clear entry")
        for h in logging.getLogger('fam').handlers:
            h.flush()

        post_size = log_path.stat().st_size
        assert post_size > 0, \
            "logger.error() after clear_log_files() did not reach disk — " \
            "the detached handler stream did not reopen"
        contents = log_path.read_text(encoding='utf-8')
        assert 'post-clear entry' in contents
        assert 'pre-clear entry' not in contents, \
            "old entry leaked through — file was not truly truncated"


# ── Settings-screen wiring (source-level guard) ─────────────────

class TestSettingsScreenWiring:
    """Source-level guards so future refactors of _reset_to_default
    don't silently drop the log-clear step we just added."""

    def _reset_source(self) -> str:
        """Locate the _reset_to_default method body in the settings
        screen source.  We slice the module source rather than using
        inspect.getsource(method) because the method lives on a class
        we don't want to instantiate just to read its source."""
        import inspect
        import fam.ui.settings_screen as ss
        full = inspect.getsource(ss)
        marker = 'def _reset_to_default('
        start = full.find(marker)
        assert start != -1, "could not locate _reset_to_default in source"
        # End at the next top-level method def (4-space indent on Settings
        # screen methods); fall back to end-of-file.
        end = full.find('\n    def ', start + len(marker))
        if end == -1:
            end = len(full)
        return full[start:end]

    def test_reset_to_default_imports_clear_log_files(self):
        src = self._reset_source()
        assert 'clear_log_files' in src, \
            "_reset_to_default must call clear_log_files() so the " \
            "Error Log tab doesn't show pre-reset entries"

    def test_reset_dialog_mentions_error_log(self):
        src = self._reset_source()
        assert 'Error Log' in src or 'error log' in src, \
            "Reset confirmation dialog must tell the user the error " \
            "log will be cleared too — otherwise it's a surprise"
