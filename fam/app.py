"""QApplication setup and initialization."""

import ctypes
import logging
import shutil
import sys
import os
import traceback
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

from fam.utils.logging_config import setup_logging
from fam.database.connection import set_db_path, get_connection
from fam.database.schema import initialize_database
from fam.database.seed import seed_if_empty
from fam.ui.styles import GLOBAL_STYLESHEET
from fam.ui.main_window import MainWindow

logger = logging.getLogger('fam.app')


# ── Global exception handler ──────────────────────────────────
# In windowed mode (no console), unhandled exceptions inside Qt
# callbacks vanish silently.  This hook ensures they are always
# logged and shown to the user so bugs are visible.

def _global_exception_handler(exc_type, exc_value, exc_tb):
    """Log the exception and show an error dialog if the app is running."""
    # KeyboardInterrupt should still exit immediately
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    # Format the full traceback for the log file
    tb_text = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.critical("Unhandled exception:\n%s", tb_text)

    # Try to show a user-facing dialog
    try:
        from PySide6.QtWidgets import QMessageBox
        app = QApplication.instance()
        if app:
            from fam.utils.logging_config import get_log_path
            log_path = get_log_path()
            QMessageBox.critical(
                None, "Unexpected Error",
                "An unexpected error occurred. Your data has been saved.\n\n"
                f"Error: {exc_value}\n\n"
                f"Details have been written to the log file:\n{log_path}\n\n"
                "You can continue using the application, but if problems "
                "persist, please restart."
            )
    except Exception:
        pass  # dialog failed — at least the log entry exists


sys.excepthook = _global_exception_handler

# ── Data directory ─────────────────────────────────────────────
# All persistent data (database, log, ledger backup) lives in
# %APPDATA%/FAM Market Manager/ so the application folder can be
# freely replaced during upgrades without losing data.
APP_DATA_DIR_NAME = "FAM Market Manager"

# ── Single-instance prevention (file lock on data dir) ─────────
# The lock is per-data-directory, not per-machine, so two app
# copies pointing at the same shared %APPDATA% (or network share)
# cannot both launch.  A held lock writes a `.fam_instance.lock`
# file that the System Status diagnostic exposes to coordinators.
# Held for process lifetime; released on clean exit (best-effort).
_instance_lock = None  # type: ignore[var-annotated]


def get_data_dir() -> str:
    """Return the data directory for persistent files.

    Frozen (PyInstaller):  %APPDATA%/FAM Market Manager/
    Development:           <project_root>/   (keeps dev experience unchanged)

    The directory is created if it does not exist.
    """
    if getattr(sys, 'frozen', False):
        appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
        data_dir = os.path.join(appdata, APP_DATA_DIR_NAME)
    else:
        # Development — project root (same as before)
        data_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def get_app_dir() -> str:
    """Return the application install directory (where the .exe lives).

    Frozen:      directory containing the .exe
    Development: project root
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _migrate_legacy_data(data_dir: str):
    """One-time migration: move data files from the old exe-adjacent location
    to the new %APPDATA% data directory.

    Only runs for frozen builds when the AppData DB does not yet exist
    but an old fam_data.db sits next to the executable.
    """
    if not getattr(sys, 'frozen', False):
        return  # nothing to migrate in dev

    app_dir = os.path.dirname(sys.executable)
    new_db = os.path.join(data_dir, 'fam_data.db')

    if os.path.exists(new_db):
        return  # already migrated

    old_db = os.path.join(app_dir, 'fam_data.db')
    if not os.path.exists(old_db):
        return  # clean install, nothing to migrate

    # Move the database (and optional companions) to AppData
    files_to_move = ['fam_data.db', 'fam_ledger_backup.txt', 'fam_manager.log']
    for filename in files_to_move:
        src = os.path.join(app_dir, filename)
        dst = os.path.join(data_dir, filename)
        if os.path.exists(src):
            try:
                shutil.move(src, dst)
            except Exception:
                # Fall back to copy if move fails (e.g. file in use)
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass  # will be logged once logging is set up


def _ensure_single_instance(data_dir: str):
    """Acquire the data-directory file lock; exit if another instance has it.

    Uses :class:`fam.database.instance_lock.InstanceLock` so the lock is
    scoped to the *data directory*, not the machine.  This protects
    against two copies pointing at the same shared `%APPDATA%` or network
    share — a per-machine kernel mutex (the previous implementation)
    cannot.

    The lock is held in the module-level ``_instance_lock`` so it lives
    for the process lifetime; release happens via the
    :func:`atexit`-registered :func:`_release_instance_lock`.
    """
    global _instance_lock
    from fam.database.instance_lock import InstanceLock, InstanceLockError
    lock = InstanceLock(data_dir)
    try:
        lock.acquire()
    except InstanceLockError as e:
        logger.warning("Another instance holds the data-dir lock — exiting: %s", e)
        _app = QApplication.instance() or QApplication(sys.argv)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(
            None, "Already Running",
            "FAM Market Manager is already open against this data folder.\n\n"
            "Only one copy can run at a time to protect your data.\n"
            "Check your taskbar for the existing window.\n\n"
            "If you believe no other copy is running:\n"
            "  1. Open Task Manager (Ctrl+Shift+Esc)\n"
            "  2. End any 'FAM Manager.exe' processes\n"
            "  3. Try launching again\n"
            "  4. If that still fails, delete\n"
            "     %APPDATA%\\FAM Market Manager\\.fam_instance.lock\n"
            "     and try once more."
        )
        sys.exit(0)
    _instance_lock = lock
    # Release the lock on clean exit (best-effort; OS reclaims on crash).
    import atexit
    atexit.register(_release_instance_lock)


def _release_instance_lock():
    """Release the instance lock if held — registered via atexit."""
    global _instance_lock
    if _instance_lock is not None:
        try:
            _instance_lock.release()
        except Exception:
            logger.warning("Failed to release instance lock cleanly", exc_info=True)
        _instance_lock = None


def run():
    """Initialize and run the FAM Market Day Transaction Manager."""
    # ── Resolve data directory FIRST (lock is data-dir-scoped) ──
    data_dir = get_data_dir()

    # ── Single-instance check (against THIS data directory) ────
    _ensure_single_instance(data_dir)

    # ── Migrate legacy installs ────────────────────────────────
    _migrate_legacy_data(data_dir)

    # v2.0.3 fix (MED-SEC-3): clean up stale auto-update temp dirs.
    # If the previous run was killed mid-update, ``_update_temp/``
    # and ``_update_download/`` survived in ``data_dir``.  These
    # widen the window an attacker has to plant a payload that the
    # next update could theoretically pick up.  No attacker is
    # required for this cleanup to be useful — they're also wasted
    # disk space.  Best-effort: never raise.
    for _stale in ('_update_temp', '_update_download'):
        _stale_path = os.path.join(data_dir, _stale)
        if os.path.isdir(_stale_path):
            import shutil as _shutil
            try:
                _shutil.rmtree(_stale_path, ignore_errors=True)
            except Exception:
                # ignore_errors=True already swallows; outer try is
                # belt-and-braces against unforeseen permission errors
                # so a stuck temp dir can never block app startup.
                pass

    db_path = os.path.join(data_dir, 'fam_data.db')
    set_db_path(db_path)

    # Set up file logging (writes to data directory)
    log_path = setup_logging(data_dir)
    logger.info("FAM Manager starting up — db=%s  log=%s  data_dir=%s",
                db_path, log_path, data_dir)

    # Initialize database — show user-friendly error if this fails
    try:
        initialize_database()
        seed_if_empty()
        logger.info("Database initialized")
        from fam.database.backup import get_backup_dir
        logger.info("Backup directory: %s", get_backup_dir())

        # v2.0.6: self-heal markets that were created via the pre-fix
        # _add_market UI (which inserted only the markets row and left
        # market_vendors / market_payment_methods junctions empty).
        # The runtime fallback in receipt_intake_screen / payment_screen
        # papered over this by showing all entries when a junction was
        # empty, but the Settings → Vendors / Settings → Markets UIs
        # correctly read the empty junction and showed all checkboxes
        # unticked — making Settings feel disconnected from Intake.
        # On launch, any market with ZERO assignments (in either
        # junction) is treated as never-configured and back-filled with
        # the cross-product of every active vendor / payment method,
        # mirroring what the v2.0.6 _add_market path now does at
        # creation time.  Idempotent via INSERT OR IGNORE.  Markets
        # that have been deliberately curated (any non-empty assignment)
        # are left alone — only ALL-empty markets are treated as
        # legacy-uninitialised.
        try:
            from fam.database.connection import get_connection
            _conn = get_connection()
            _empty_vendor_markets = _conn.execute(
                "SELECT m.id FROM markets m "
                " WHERE NOT EXISTS ("
                "   SELECT 1 FROM market_vendors mv "
                "    WHERE mv.market_id = m.id)"
            ).fetchall()
            _empty_method_markets = _conn.execute(
                "SELECT m.id FROM markets m "
                " WHERE NOT EXISTS ("
                "   SELECT 1 FROM market_payment_methods mpm "
                "    WHERE mpm.market_id = m.id)"
            ).fetchall()
            for _row in _empty_vendor_markets:
                _conn.execute(
                    "INSERT OR IGNORE INTO market_vendors "
                    " (market_id, vendor_id) "
                    " SELECT ?, id FROM vendors WHERE is_active = 1",
                    (_row[0],))
            for _row in _empty_method_markets:
                _conn.execute(
                    "INSERT OR IGNORE INTO market_payment_methods "
                    " (market_id, payment_method_id) "
                    " SELECT ?, id FROM payment_methods "
                    "  WHERE is_active = 1",
                    (_row[0],))
            _conn.commit()
            _vendor_healed = len(_empty_vendor_markets)
            _method_healed = len(_empty_method_markets)
            if _vendor_healed or _method_healed:
                logger.info(
                    "Self-heal: back-filled %d markets with vendors, "
                    "%d markets with payment methods (legacy "
                    "pre-v2.0.6 _add_market state).",
                    _vendor_healed, _method_healed)
        except Exception:
            # Self-heal must never block launch.  Log and continue
            # — the runtime fallback still produces a usable UI.
            logger.exception(
                "Could not self-heal market junction tables; "
                "the runtime fallback will still show all vendors / "
                "methods at affected markets.")

        # Capture device fingerprint on every launch.
        #
        # v1.9.10 follow-up (2026-05-01): hard-fail the launch if
        # the captured ID is empty.  An empty device_id collides
        # with another empty-id device when both sync to the same
        # Google Sheet (`SHEET_KEYS` composite-key upserts treat
        # `device_id=''` as a single identity, so device A's rows
        # silently overwrite device B's).  Better to refuse to
        # launch than to ship money data through a sync that
        # corrupts cross-device coordination.
        from fam.utils.app_settings import (
            capture_device_id, _is_hostname_fallback_id,
        )
        device_id = capture_device_id()
        # v2.0.2 fix (B-H8): also refuse to launch when ``device_id``
        # is the synthetic ``hostname-XXX`` fallback.  Pre-fix the
        # registry-read failure path produced ``hostname-DESKTOP-ABC``
        # which was non-empty (so this hard-fail never fired) but
        # was identical across all image-cloned fleet laptops sharing
        # a hostname — leading to silent cross-device row collisions
        # on the shared Sheet.
        if not device_id or _is_hostname_fallback_id(device_id):
            # Tag this exception class so the launch error handler
            # below shows a targeted dialog rather than the generic
            # "Database Error" message — this is a device-identity
            # config problem, not a DB problem.
            err = RuntimeError(
                "capture_device_id() could not read a real "
                "MachineGuid (got %r).  Cloud sync MUST have a "
                "stable, unique device_id to prevent cross-device "
                "row collisions on the shared Google Sheet.  This "
                "usually means the registry value at "
                "HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid "
                "is missing — common on freshly-imaged fleet "
                "laptops.  Resolve before launch."
                % device_id)
            err._fam_kind = 'device_id'
            raise err
        logger.info("Device ID: %s", device_id)
    except Exception as e:
        logger.exception("Failed to initialize database")
        # Need a QApplication to show a dialog
        _app = QApplication.instance() or QApplication(sys.argv)
        from PySide6.QtWidgets import QMessageBox
        # v2.0.6: differentiate the device_id hard-fail from a true
        # DB failure.  Pre-fix any post-DB-init exception fell
        # through to the generic "Database Error" dialog, which
        # told fielded image-cloned-laptop users to "check that
        # the folder is writable" — wrong remediation for a
        # missing-MachineGuid registry value.  The targeted dialog
        # below points at the actual cause and the fix.
        if getattr(e, '_fam_kind', None) == 'device_id':
            QMessageBox.critical(
                None, "Device Identity Required",
                "FAM Market Manager cannot launch because this "
                "computer does not have a unique device identity.\n\n"
                "Cloud sync requires every workstation to have a "
                "stable, unique ID so that records from one device "
                "don't overwrite records from another on the shared "
                "Google Sheet.\n\n"
                "Most likely cause: the Windows registry value\n"
                "  HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid\n"
                "is missing or unreadable.  This commonly happens "
                "on laptops that were set up by cloning an image "
                "from another machine.\n\n"
                "Resolution (one of):\n"
                "  • Run sysprep on the cloned image so each device "
                "gets its own MachineGuid.\n"
                "  • As a per-device workaround, run this in an "
                "elevated PowerShell:\n"
                "      $g = [guid]::NewGuid().ToString()\n"
                "      Set-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft"
                "\\Cryptography' MachineGuid $g\n\n"
                f"Log file: {log_path}"
            )
        else:
            QMessageBox.critical(
                None, "Database Error",
                f"FAM Market Manager could not open the database.\n\n"
                f"Path: {db_path}\n"
                f"Error: {e}\n\n"
                f"Check that the folder is writable and the file "
                f"is not corrupted.\n"
                f"Log file: {log_path}"
            )
        sys.exit(1)

    # Tell Windows this is its own application (not Python) so the
    # taskbar shows our icon instead of the default Python icon.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "FoodAssistanceMatch.FAMManager"
        )
    except Exception:
        pass  # non-Windows or missing API — harmless

    # Create Qt application
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("FAM Market Day Transaction Manager")
    app.setOrganizationName("Food Assistance Match")

    # Set app-level icon so the taskbar picks it up
    from fam.ui.main_window import _resolve_asset
    _icon_path = _resolve_asset("fam_icon.ico")
    if os.path.exists(_icon_path):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(_icon_path))

    # Set global stylesheet
    app.setStyleSheet(GLOBAL_STYLESHEET)

    # Set default font
    font = QFont("Inter", 10)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)

    # Create main window (do not show yet — pending-update check first)
    window = MainWindow()

    # If a previous update attempt left a pending-version marker, check
    # whether the installed version matches and surface the outcome.
    # This converts silent updater failures (where the user ends up on
    # the same version they started on) into a visible, actionable error.
    #
    # v2.0.1: this check runs BEFORE window.show() so the user is told
    # about the failure before they can interact with the UI.  Previously
    # the warning fired AFTER the volunteer could already have started
    # taking transactions on the still-old version.
    try:
        from fam import __version__
        from fam.update.checker import check_pending_update_result
        result = check_pending_update_result(__version__)
        if result is not None and result.get('status') == 'failed':
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                None,
                "Update did not complete",
                f"An update to v{result['target_version']} was started "
                f"but the application is still running v{result['actual_version']}.\n\n"
                "The update log is at %APPDATA%\\FAM Market Manager\\_fam_update.log.\n\n"
                "Please re-download the latest release manually from the "
                "project's GitHub page.",
            )
    except Exception:
        logger.exception("Pending-update check failed")

    # Now show the main window — user can begin work
    window.show()
    logger.info("Application window opened")

    exit_code = app.exec()
    logger.info("Application shutting down (exit code %s)", exit_code)
    sys.exit(exit_code)
