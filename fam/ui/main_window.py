"""Main window with sidebar navigation."""

import os
import sys

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QStackedWidget, QLabel, QButtonGroup, QFrame, QSizePolicy,
    QDialog, QDialogButtonBox
)
from PySide6.QtCore import Qt, QRect, QEvent, QUrl, QTimer
from PySide6.QtGui import QPixmap, QPainter, QColor, QBrush, QIcon, QDesktopServices

from fam.ui.styles import PRIMARY_GREEN, WHITE, LIGHT_GRAY, SUBTITLE_GRAY


def _resolve_asset(filename: str) -> str:
    """Return absolute path to a UI asset, handling frozen (PyInstaller) mode."""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, "fam", "ui", filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


class _PatternSidebar(QFrame):
    """Sidebar with a subtle tiled background pattern over the brand colour."""

    def __init__(self, pattern_path: str, parent=None):
        super().__init__(parent)
        self._tile = QPixmap(pattern_path) if pattern_path else QPixmap()
        self._base = QColor(PRIMARY_GREEN)
        self._opacity = 0.40  # branded texture over the dark green

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 1. Solid brand-green base
        painter.fillRect(self.rect(), self._base)

        # 2. Tile the pattern on top at low opacity
        if not self._tile.isNull():
            painter.setOpacity(self._opacity)
            tw, th = self._tile.width(), self._tile.height()
            for y in range(0, self.height(), th):
                for x in range(0, self.width(), tw):
                    painter.drawPixmap(x, y, self._tile)
            painter.setOpacity(1.0)

        painter.end()


from fam.ui.market_day_screen import MarketDayScreen
from fam.ui.receipt_intake_screen import ReceiptIntakeScreen
from fam.ui.payment_screen import PaymentScreen
from fam.ui.fmnp_screen import FMNPScreen
from fam.ui.admin_screen import AdminScreen
from fam.ui.reports_screen import ReportsScreen
from fam.ui.settings_screen import SettingsScreen


class MainWindow(QMainWindow):
    """Main application window with sidebar navigation."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("FAM Market Day Transaction Manager")
        icon_path = _resolve_asset("fam_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setMinimumSize(1200, 750)
        self.resize(1400, 850)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar with subtle tiled background pattern
        bg_path = _resolve_asset("_fam_background.jpg")
        sidebar = _PatternSidebar(bg_path)
        sidebar.setObjectName("sidebar")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        # Logo in sidebar
        logo_path = _resolve_asset("_fam_logo_white.png")

        logo_label = QLabel()
        logo_label.setObjectName("sidebar_logo")
        logo_label.setAlignment(Qt.AlignCenter)
        logo_label.setStyleSheet("background-color: transparent; padding: 20px 20px 4px 20px;")
        pixmap = QPixmap(logo_path)
        if not pixmap.isNull():
            scaled = pixmap.scaledToWidth(160, Qt.SmoothTransformation)
            logo_label.setPixmap(scaled)
        else:
            # Fallback if image not found
            logo_label.setText("FAM")
            logo_label.setStyleSheet(
                "color: white; font-size: 28px; font-weight: bold; "
                "background-color: transparent; padding: 20px 20px 4px 20px;"
            )
        sidebar_layout.addWidget(logo_label)

        sub_label = QLabel("FAM Market Manager")
        sub_label.setObjectName("sidebar_subtitle")
        sub_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(sub_label)

        # Navigation buttons
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        nav_items = [
            ("Market", 0),
            ("Receipt Intake", 1),
            ("Payment", 2),
            ("Adjustments", 3),
            ("FMNP Entry", 4),
            ("Reports", 5),
            ("Settings", 6),
        ]

        for label, idx in nav_items:
            btn = QPushButton(f"  {label}")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            self.nav_group.addButton(btn, idx)
            sidebar_layout.addWidget(btn)

        sidebar_layout.addStretch()

        # About button (version + clickable)
        self._about_btn = QPushButton("  v1.5.1  \u2022  About")
        self._about_btn.setCursor(Qt.PointingHandCursor)
        self._about_btn.setStyleSheet("""
            QPushButton {
                color: rgba(255,255,255,0.5);
                font-size: 11px;
                padding: 10px;
                background: transparent;
                border: none;
                text-align: left;
                min-height: 0px;
            }
            QPushButton:hover {
                color: rgba(255,255,255,0.85);
            }
        """)
        self._about_btn.clicked.connect(self._show_about)
        sidebar_layout.addWidget(self._about_btn)

        main_layout.addWidget(sidebar)

        # Content area
        content_frame = QFrame()
        content_frame.setObjectName("content_area")
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Header bar with tutorial button
        header_bar = QFrame()
        header_bar.setObjectName("header_bar")
        header_bar.setFixedHeight(40)
        header_bar.setStyleSheet(f"""
            #header_bar {{
                background-color: {WHITE};
                border-bottom: 1px solid {LIGHT_GRAY};
            }}
        """)
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(16, 4, 16, 4)
        header_layout.addStretch()

        self._tutorial_btn = QPushButton("\U0001F4D6  Start Tutorial")
        self._tutorial_btn.setCursor(Qt.PointingHandCursor)
        self._tutorial_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 5px 14px;
                font-size: 12px;
                min-height: 0px;
                border: 1px solid {LIGHT_GRAY};
                border-radius: 6px;
                background-color: {WHITE};
                color: {SUBTITLE_GRAY};
            }}
            QPushButton:hover {{
                background-color: #F0EFEB;
                color: {PRIMARY_GREEN};
                border-color: {PRIMARY_GREEN};
            }}
        """)
        self._tutorial_btn.clicked.connect(self.start_tutorial)
        header_layout.addWidget(self._tutorial_btn)

        content_layout.addWidget(header_bar)

        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack)
        main_layout.addWidget(content_frame, 1)

        # Create screens
        self.market_day_screen = MarketDayScreen()
        self.receipt_intake_screen = ReceiptIntakeScreen()
        self.payment_screen = PaymentScreen()
        self.fmnp_screen = FMNPScreen()
        self.admin_screen = AdminScreen()
        self.reports_screen = ReportsScreen()
        self.settings_screen = SettingsScreen()

        self.stack.addWidget(self.market_day_screen)   # 0
        self.stack.addWidget(self.receipt_intake_screen) # 1
        self.stack.addWidget(self.payment_screen)        # 2
        self.stack.addWidget(self.admin_screen)          # 3
        self.stack.addWidget(self.fmnp_screen)           # 4
        self.stack.addWidget(self.reports_screen)        # 5
        self.stack.addWidget(self.settings_screen)       # 6

        # Connect navigation
        self.nav_group.idClicked.connect(self._navigate)

        # Connect signals
        self.market_day_screen.market_day_changed.connect(self._on_market_day_changed)
        self.receipt_intake_screen.customer_order_ready.connect(self._on_customer_order_ready)
        self.payment_screen.payment_confirmed.connect(self._on_payment_confirmed)
        self.payment_screen.draft_saved.connect(self._on_draft_saved)

        # Select first screen
        first_btn = self.nav_group.button(0)
        if first_btn:
            first_btn.setChecked(True)
        self.stack.setCurrentIndex(0)

        # Tutorial overlay (created on demand)
        self._tutorial_overlay = None
        self.centralWidget().installEventFilter(self)

        # Auto-launch tutorial on first run (after the window is painted)
        QTimer.singleShot(500, self._maybe_auto_tutorial)

    def _navigate(self, idx):
        self.stack.setCurrentIndex(idx)
        # Refresh the target screen
        widget = self.stack.widget(idx)
        if hasattr(widget, 'refresh'):
            widget.refresh()

    def _on_market_day_changed(self):
        """Refresh dependent screens when market day changes."""
        self.receipt_intake_screen.refresh()

    def _on_customer_order_ready(self, order_id):
        """Navigate to payment screen with the customer order."""
        self.payment_screen.load_customer_order(order_id)
        self.stack.setCurrentIndex(2)
        btn = self.nav_group.button(2)
        if btn:
            btn.setChecked(True)

    def _on_payment_confirmed(self):
        """After payment is confirmed, go back to receipt intake for next customer."""
        self.receipt_intake_screen.start_fresh_after_payment()
        self.stack.setCurrentIndex(1)
        btn = self.nav_group.button(1)
        if btn:
            btn.setChecked(True)

    def _on_draft_saved(self):
        """After draft is saved, return to receipt intake and refresh."""
        self.receipt_intake_screen.start_fresh_after_payment()
        self.stack.setCurrentIndex(1)
        btn = self.nav_group.button(1)
        if btn:
            btn.setChecked(True)

    # ------------------------------------------------------------------
    # About dialog
    # ------------------------------------------------------------------

    def _show_about(self):
        """Show the About dialog."""
        from fam.ui.styles import (
            PRIMARY_GREEN, ACCENT_GREEN, HARVEST_GOLD,
            WHITE, TEXT_COLOR, BACKGROUND, LIGHT_GRAY
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("About FAM Market Manager")
        dlg.setFixedWidth(420)
        dlg.setStyleSheet(f"QDialog {{ background-color: {BACKGROUND}; }}")

        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 16)

        # Title
        title = QLabel("FAM Market Manager")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"""
            font-size: 20px; font-weight: bold;
            color: {PRIMARY_GREEN}; background: transparent;
        """)
        layout.addWidget(title)

        # Version
        version = QLabel("Version 1.5.1")
        version.setAlignment(Qt.AlignCenter)
        version.setStyleSheet(f"""
            font-size: 13px; color: {TEXT_COLOR}; background: transparent;
        """)
        layout.addWidget(version)

        # Description
        desc = QLabel(
            "A transaction management tool for farmers market "
            "incentive programs. Tracks customer payments, calculates "
            "FAM match amounts, and generates reports for vendor "
            "reimbursement."
        )
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet(f"""
            font-size: 12px; color: {TEXT_COLOR};
            padding: 8px 12px; background: transparent;
        """)
        layout.addWidget(desc)

        # GitHub link
        repo_btn = QPushButton("View Source Code on GitHub")
        repo_btn.setCursor(Qt.PointingHandCursor)
        repo_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 8px 16px; font-size: 12px; min-height: 0px;
                border: 1px solid {LIGHT_GRAY}; border-radius: 6px;
                background-color: {WHITE}; color: {ACCENT_GREEN};
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #F0EFEB;
                border-color: {ACCENT_GREEN};
            }}
        """)
        repo_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/seansaball/fam-market-manager")
            )
        )
        layout.addWidget(repo_btn, alignment=Qt.AlignCenter)

        # Close button
        close_btn = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn.rejected.connect(dlg.close)
        layout.addWidget(close_btn)

        dlg.exec()

    # ------------------------------------------------------------------
    # Tutorial
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # First-run detection (app_settings table)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_first_run() -> bool:
        """Return True if the tutorial has never been shown."""
        from fam.database.connection import get_connection
        try:
            row = get_connection().execute(
                "SELECT value FROM app_settings WHERE key = 'tutorial_shown'"
            ).fetchone()
            return row is None or row[0] != '1'
        except Exception:
            return False  # table might not exist yet — don't crash

    @staticmethod
    def _mark_tutorial_shown():
        """Record that the tutorial has been shown so it won't auto-launch again."""
        from fam.database.connection import get_connection
        try:
            conn = get_connection()
            conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('tutorial_shown', '1')"
            )
            conn.commit()
        except Exception:
            pass  # best-effort — never crash

    def _maybe_auto_tutorial(self):
        """Launch the tutorial automatically on first run only."""
        if self._is_first_run():
            self.start_tutorial()

    def start_tutorial(self):
        """Launch the guided tutorial overlay."""
        from fam.ui.tutorial_overlay import TutorialOverlay, TUTORIAL_STEPS
        self._end_tutorial()
        self._tutorial_overlay = TutorialOverlay(self, TUTORIAL_STEPS)
        self._tutorial_overlay.finished.connect(self._end_tutorial)

    def _end_tutorial(self):
        """Remove the tutorial overlay if active."""
        if self._tutorial_overlay:
            self._tutorial_overlay.hide()
            self._tutorial_overlay.deleteLater()
            self._tutorial_overlay = None
            self._mark_tutorial_shown()

    def eventFilter(self, obj, event):
        """Resize the tutorial overlay when the central widget resizes."""
        if obj is self.centralWidget() and event.type() == QEvent.Resize:
            if self._tutorial_overlay:
                self._tutorial_overlay.refresh_position()
        return super().eventFilter(obj, event)
