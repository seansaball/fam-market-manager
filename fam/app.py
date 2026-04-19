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

# ── Single-instance prevention (Windows named mutex) ───────────
_ERROR_ALREADY_EXISTS = 183
_mutex_handle = None  # kept alive for the process lifetime


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


def _ensure_single_instance():
    """Create a named mutex; exit with a warning if another instance is running."""
    global _mutex_handle
    kernel32 = ctypes.windll.kernel32
    _mutex_handle = kernel32.CreateMutexW(None, False, "FAM_MarketManager_SingleInstance")
    if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        logger.warning("Another instance of FAM Manager is already running — exiting")
        _app = QApplication.instance() or QApplication(sys.argv)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(
            None, "Already Running",
            "FAM Market Manager is already open.\n\n"
            "Only one copy can run at a time to protect your data.\n"
            "Check your taskbar for the existing window."
        )
        sys.exit(0)


def run():
    """Initialize and run the FAM Market Day Transaction Manager."""
    # ── Single-instance check (before anything else) ───────────
    _ensure_single_instance()

    # ── Resolve data directory and migrate legacy installs ─────
    data_dir = get_data_dir()
    _migrate_legacy_data(data_dir)

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

        # Capture device fingerprint on every launch
        from fam.utils.app_settings import capture_device_id
        device_id = capture_device_id()
        logger.info("Device ID: %s", device_id)
    except Exception as e:
        logger.exception("Failed to initialize database")
        # Need a QApplication to show a dialog
        _app = QApplication.instance() or QApplication(sys.argv)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None, "Database Error",
            f"FAM Market Manager could not open the database.\n\n"
            f"Path: {db_path}\n"
            f"Error: {e}\n\n"
            f"Check that the folder is writable and the file is not corrupted.\n"
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

    # Create and show main window
    window = MainWindow()
    window.show()
    logger.info("Application window opened")

    # If a previous update attempt left a pending-version marker, check
    # whether the installed version matches and surface the outcome.
    # This converts silent updater failures (where the user ends up on
    # the same version they started on) into a visible, actionable error.
    try:
        from fam import __version__
        from fam.update.checker import check_pending_update_result
        result = check_pending_update_result(__version__)
        if result is not None and result.get('status') == 'failed':
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                window,
                "Update did not complete",
                f"An update to v{result['target_version']} was started "
                f"but the application is still running v{result['actual_version']}.\n\n"
                "The update log is at %APPDATA%\\FAM Market Manager\\_fam_update.log.\n\n"
                "Please re-download the latest release manually from the "
                "project's GitHub page.",
            )
    except Exception:
        logger.exception("Pending-update check failed")

    exit_code = app.exec()
    logger.info("Application shutting down (exit code %s)", exit_code)
    sys.exit(exit_code)
