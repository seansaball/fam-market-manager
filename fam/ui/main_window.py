"""Main window with sidebar navigation."""

import os
import sys

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QStackedWidget, QLabel, QButtonGroup, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPixmap, QPainter, QColor, QBrush, QIcon

from fam.ui.styles import PRIMARY_GREEN


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
            ("FMNP Entry", 3),
            ("Adjustments", 4),
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

        # Version info
        ver_label = QLabel("  v1.3.0")
        ver_label.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 11px; padding: 10px;")
        sidebar_layout.addWidget(ver_label)

        main_layout.addWidget(sidebar)

        # Content area
        content_frame = QFrame()
        content_frame.setObjectName("content_area")
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)

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
        self.stack.addWidget(self.fmnp_screen)           # 3
        self.stack.addWidget(self.admin_screen)          # 4
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
