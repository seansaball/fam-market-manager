"""Main window with sidebar navigation."""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QStackedWidget, QLabel, QButtonGroup, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt

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
        self.setMinimumSize(1200, 750)
        self.resize(1400, 850)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        # App title in sidebar
        title_label = QLabel("FAM Manager")
        title_label.setObjectName("sidebar_title")
        sidebar_layout.addWidget(title_label)

        sub_label = QLabel("Market Day Transactions")
        sub_label.setObjectName("sidebar_subtitle")
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
        ver_label = QLabel("  v1.1.0")
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
