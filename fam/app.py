"""QApplication setup and initialization."""

import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

from fam.database.connection import set_db_path, get_connection
from fam.database.schema import initialize_database
from fam.database.seed import seed_if_empty
from fam.ui.styles import GLOBAL_STYLESHEET
from fam.ui.main_window import MainWindow


def run():
    """Initialize and run the FAM Market Day Transaction Manager."""
    # Set database path relative to the project root
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(project_dir, 'fam_data.db')
    set_db_path(db_path)

    # Initialize database
    initialize_database()
    seed_if_empty()

    # Create Qt application
    app = QApplication(sys.argv)
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

    sys.exit(app.exec())
