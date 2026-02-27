"""Screen A: Market Day Setup."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFrame,
    QLineEdit
)
from PySide6.QtCore import Qt, Signal
from datetime import date

from fam.database.connection import get_connection
from fam.models.market_day import (
    get_all_market_days, get_market_day_by_id, create_market_day,
    close_market_day, reopen_market_day, get_market_day_transactions_summary,
    get_open_market_day, find_market_day
)
from fam.models.transaction import get_draft_transactions
from fam.ui.styles import PRIMARY_GREEN, HARVEST_GOLD, WHITE, LIGHT_GRAY, FIELD_LABEL_BG
from fam.ui.helpers import make_field_label as _make_field_label_fn, make_item, configure_table


class MarketDayScreen(QWidget):
    """Market Day Setup screen."""

    market_day_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _make_field_label(self, text):
        """Create a styled field label."""
        return _make_field_label_fn(text)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Title
        title = QLabel("Market Day Setup")
        title.setObjectName("screen_title")
        layout.addWidget(title)

        subtitle = QLabel("Open or manage a market day to start recording transactions")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        # Create / select area
        create_frame = QFrame()
        create_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
                padding: 16px;
            }}
        """)
        create_layout = QVBoxLayout(create_frame)

        row1 = QHBoxLayout()
        row1.addWidget(self._make_field_label("Market Location"))
        self.market_combo = QComboBox()
        self.market_combo.setMinimumWidth(250)
        row1.addWidget(self.market_combo)
        row1.addStretch()
        create_layout.addLayout(row1)

        # Volunteer name field
        row_vol = QHBoxLayout()
        row_vol.addWidget(self._make_field_label("Volunteer Name"))
        self.volunteer_input = QLineEdit()
        self.volunteer_input.setPlaceholderText("Enter your name")
        self.volunteer_input.setMinimumWidth(200)
        self.volunteer_input.setText("Volunteer")
        row_vol.addWidget(self.volunteer_input)
        row_vol.addStretch()
        create_layout.addLayout(row_vol)

        row2 = QHBoxLayout()
        self.open_btn = QPushButton("Open Market Day (Today)")
        self.open_btn.setObjectName("primary_btn")
        self.open_btn.clicked.connect(self._open_market_day)
        row2.addWidget(self.open_btn)
        row2.addStretch()
        create_layout.addLayout(row2)

        layout.addWidget(create_frame)

        # Current market day status
        self.status_frame = QFrame()
        self.status_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
                padding: 16px;
            }}
        """)
        status_layout = QVBoxLayout(self.status_frame)

        self.status_header = QLabel("No Active Market Day")
        self.status_header.setObjectName("section_header")
        status_layout.addWidget(self.status_header)

        self.status_info = QLabel("")
        status_layout.addWidget(self.status_info)

        btn_row = QHBoxLayout()
        self.close_btn = QPushButton("Close Market Day")
        self.close_btn.setObjectName("secondary_btn")
        self.close_btn.clicked.connect(self._close_market_day)
        self.close_btn.setVisible(False)
        btn_row.addWidget(self.close_btn)

        self.reopen_btn = QPushButton("Reopen Market Day")
        self.reopen_btn.setObjectName("secondary_btn")
        self.reopen_btn.clicked.connect(self._reopen_market_day)
        self.reopen_btn.setVisible(False)
        btn_row.addWidget(self.reopen_btn)
        btn_row.addStretch()
        status_layout.addLayout(btn_row)

        layout.addWidget(self.status_frame)

        # Existing market days list
        layout.addWidget(QLabel("Recent Market Days:"))
        self.market_day_combo = QComboBox()
        self.market_day_combo.currentIndexChanged.connect(self._on_market_day_selected)
        layout.addWidget(self.market_day_combo)

        # Transactions table
        layout.addWidget(QLabel("Transactions for Selected Market Day:"))
        self.txn_table = QTableWidget()
        self.txn_table.setColumnCount(5)
        self.txn_table.setHorizontalHeaderLabels(
            ["Transaction ID", "Vendor", "Receipt Total", "Status", "Created"]
        )
        configure_table(self.txn_table)
        layout.addWidget(self.txn_table)

        layout.addStretch()

    def refresh(self):
        """Reload all data."""
        self._load_markets()
        self._load_market_days()
        self._update_status()

    def _load_markets(self):
        conn = get_connection()
        rows = conn.execute("SELECT * FROM markets WHERE is_active=1 ORDER BY name").fetchall()
        self.market_combo.clear()
        for r in rows:
            self.market_combo.addItem(r['name'], userData=r['id'])

    def _load_market_days(self):
        days = get_all_market_days()
        self.market_day_combo.blockSignals(True)
        self.market_day_combo.clear()
        self.market_day_combo.addItem("-- Select a Market Day --", userData=None)
        for d in days:
            status_tag = "[OPEN]" if d['status'] == 'Open' else "[Closed]"
            self.market_day_combo.addItem(
                f"{d['market_name']} - {d['date']} {status_tag}",
                userData=d['id']
            )
        self.market_day_combo.blockSignals(False)

        # Auto-select open market day
        open_md = get_open_market_day()
        if open_md:
            for i in range(self.market_day_combo.count()):
                if self.market_day_combo.itemData(i) == open_md['id']:
                    self.market_day_combo.setCurrentIndex(i)
                    break

    def _update_status(self):
        open_md = get_open_market_day()
        if open_md:
            self.status_header.setText(f"Active: {open_md['market_name']} - {open_md['date']}")
            self.status_info.setText(f"Status: Open  |  Opened by: {open_md.get('opened_by', 'N/A')}")
            self.close_btn.setVisible(True)
            self.reopen_btn.setVisible(False)
            self._load_transactions(open_md['id'])
        else:
            selected_id = self.market_day_combo.currentData()
            if selected_id:
                md = get_market_day_by_id(selected_id)
                if md and md['status'] == 'Closed':
                    self.status_header.setText(f"Viewing: {md['market_name']} - {md['date']}")
                    self.status_info.setText(f"Status: Closed  |  Closed by: {md.get('closed_by', 'N/A')}")
                    self.close_btn.setVisible(False)
                    self.reopen_btn.setVisible(True)
                    self._load_transactions(selected_id)
                    return
            self.status_header.setText("No Active Market Day")
            self.status_info.setText("Open a new market day to start recording transactions.")
            self.close_btn.setVisible(False)
            self.reopen_btn.setVisible(False)

    def _on_market_day_selected(self):
        md_id = self.market_day_combo.currentData()
        if md_id:
            self._load_transactions(md_id)
            self._update_status()

    def _load_transactions(self, market_day_id):
        txns = get_market_day_transactions_summary(market_day_id)
        self.txn_table.setSortingEnabled(False)
        self.txn_table.setRowCount(len(txns))
        for i, t in enumerate(txns):
            self.txn_table.setItem(i, 0, make_item(t['fam_transaction_id']))
            self.txn_table.setItem(i, 1, make_item(t['vendor_name']))
            self.txn_table.setItem(i, 2, make_item(f"${t['receipt_total']:.2f}", t['receipt_total']))
            self.txn_table.setItem(i, 3, make_item(t['status']))
            self.txn_table.setItem(i, 4, make_item(str(t.get('created_at', ''))))
        self.txn_table.setSortingEnabled(True)

    def _get_volunteer_name(self):
        name = self.volunteer_input.text().strip()
        return name if name else "Volunteer"

    def _open_market_day(self):
        market_id = self.market_combo.currentData()
        if not market_id:
            QMessageBox.warning(self, "Error", "Please select a market location.")
            return

        # Check for existing open market day
        open_md = get_open_market_day()
        if open_md:
            QMessageBox.warning(
                self, "Market Day Already Open",
                f"There is already an open market day:\n"
                f"{open_md['market_name']} - {open_md['date']}\n\n"
                "Please close it before opening a new one."
            )
            return

        today = date.today().isoformat()
        volunteer = self._get_volunteer_name()

        # Check if a market day already exists for this market+date (prevents duplicates)
        existing = find_market_day(market_id, today)
        if existing:
            # Reopen the existing record instead of creating a duplicate
            reopen_market_day(existing['id'], opened_by=volunteer)
        else:
            create_market_day(market_id, today, opened_by=volunteer)

        self.refresh()
        self.market_day_changed.emit()

    def _close_market_day(self):
        open_md = get_open_market_day()
        if not open_md:
            return

        volunteer = self._get_volunteer_name()

        # Warn about draft transactions
        drafts = get_draft_transactions(open_md['id'])
        if drafts:
            result = QMessageBox.warning(
                self, "Unconfirmed Transactions",
                f"There are {len(drafts)} unconfirmed (Draft) transaction(s).\n"
                "Are you sure you want to close the market day?",
                QMessageBox.Yes | QMessageBox.No
            )
            if result != QMessageBox.Yes:
                return

        close_market_day(open_md['id'], closed_by=volunteer)
        self.refresh()
        self.market_day_changed.emit()

    def _reopen_market_day(self):
        md_id = self.market_day_combo.currentData()
        if not md_id:
            return
        open_md = get_open_market_day()
        if open_md:
            QMessageBox.warning(
                self, "Cannot Reopen",
                f"Another market day is already open:\n"
                f"{open_md['market_name']} - {open_md['date']}"
            )
            return
        volunteer = self._get_volunteer_name()
        reopen_market_day(md_id, opened_by=volunteer)
        self.refresh()
        self.market_day_changed.emit()
