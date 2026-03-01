"""QApplication setup and initialization."""

import logging
import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

from fam.utils.logging_config import setup_logging
from fam.database.connection import set_db_path, get_connection
from fam.database.schema import initialize_database
from fam.database.seed import seed_if_empty
from fam.ui.styles import GLOBAL_STYLESHEET
from fam.ui.main_window import MainWindow

logger = logging.getLogger('fam.app')


def run():
    """Initialize and run the FAM Market Day Transaction Manager."""
    # Set database path — next to the .exe when frozen, or project root in dev
    if getattr(sys, 'frozen', False):
        project_dir = os.path.dirname(sys.executable)
    else:
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(project_dir, 'fam_data.db')
    set_db_path(db_path)

    # Set up file logging (before anything else touches the DB)
    log_path = setup_logging()
    logger.info("FAM Manager starting up — db=%s  log=%s", db_path, log_path)

    # Initialize database — show user-friendly error if this fails
    try:
        initialize_database()
        seed_if_empty()
        logger.info("Database initialized")
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

    # Create Qt application
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("FAM Market Day Transaction Manager")
    app.setOrganizationName("Food Assistance Match")

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

    exit_code = app.exec()
    logger.info("Application shutting down (exit code %s)", exit_code)
    sys.exit(exit_code)
