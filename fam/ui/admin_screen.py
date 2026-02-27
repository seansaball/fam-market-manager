"""Screen E: Admin Adjustments."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QDoubleSpinBox, QTextEdit, QDialog, QDialogButtonBox,
    QFormLayout
)
from PySide6.QtCore import Qt

from fam.models.market_day import get_all_market_days, get_open_market_day
from fam.models.vendor import get_all_vendors
from fam.models.transaction import (
    search_transactions, get_transaction_by_id, update_transaction,
    void_transaction, get_payment_line_items, save_payment_line_items
)
from fam.models.audit import log_action, get_audit_log
from fam.ui.styles import (
    WHITE, LIGHT_GRAY, ERROR_COLOR, PRIMARY_GREEN, WARNING_COLOR,
    BACKGROUND, TEXT_COLOR
)
from fam.ui.helpers import make_field_label, make_item, make_action_btn, configure_table


REASON_CODES = [
    "data_entry_error",
    "vendor_correction",
    "admin_adjustment",
    "customer_dispute",
    "other"
]


class AdjustmentDialog(QDialog):
    """Dialog for making an adjustment to a transaction."""

    def __init__(self, txn, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Adjust Transaction {txn['fam_transaction_id']}")
        self.setMinimumWidth(450)
        self.txn = txn
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {BACKGROUND};
            }}
            QLabel {{
                background-color: transparent;
                color: {TEXT_COLOR};
            }}
        """)

        layout = QFormLayout(self)

        self.receipt_spin = QDoubleSpinBox()
        self.receipt_spin.setRange(0.01, 99999.99)
        self.receipt_spin.setDecimals(2)
        self.receipt_spin.setPrefix("$")
        self.receipt_spin.setValue(txn['receipt_total'])
        layout.addRow("Receipt Total:", self.receipt_spin)

        self.vendor_combo = QComboBox()
        vendors = get_all_vendors(active_only=True)
        for v in vendors:
            self.vendor_combo.addItem(v['name'], userData=v['id'])
        for i in range(self.vendor_combo.count()):
            if self.vendor_combo.itemData(i) == txn['vendor_id']:
                self.vendor_combo.setCurrentIndex(i)
                break
        layout.addRow("Vendor:", self.vendor_combo)

        self.reason_combo = QComboBox()
        for rc in REASON_CODES:
            self.reason_combo.addItem(rc)
        layout.addRow("Reason:", self.reason_combo)

        self.notes_input = QTextEdit()
        self.notes_input.setMaximumHeight(80)
        self.notes_input.setPlaceholderText("Explain the reason for this adjustment...")
        layout.addRow("Notes:", self.notes_input)

        self.adjusted_by_input = QLineEdit()
        self.adjusted_by_input.setPlaceholderText("Your name")
        layout.addRow("Adjusted By:", self.adjusted_by_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class AdminScreen(QWidget):
    """Admin Adjustments screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Adjustments & Corrections")
        title.setObjectName("screen_title")
        layout.addWidget(title)

        subtitle = QLabel("Search, adjust, or void transactions with full audit trail")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        # Filter bar
        filter_frame = QFrame()
        filter_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
                padding: 12px 16px;
            }}
        """)
        filter_layout = QHBoxLayout(filter_frame)

        filter_layout.addWidget(make_field_label("Market"))
        self.md_filter = QComboBox()
        self.md_filter.setMinimumWidth(200)
        filter_layout.addWidget(self.md_filter)

        filter_layout.addWidget(make_field_label("Status"))
        self.status_filter = QComboBox()
        self.status_filter.addItems(["All", "Draft", "Confirmed", "Adjusted", "Voided"])
        filter_layout.addWidget(self.status_filter)

        filter_layout.addWidget(make_field_label("Transaction ID"))
        self.id_search = QLineEdit()
        self.id_search.setPlaceholderText("Search FAM-...")
        self.id_search.setMaximumWidth(180)
        filter_layout.addWidget(self.id_search)

        search_btn = QPushButton("Search")
        search_btn.setObjectName("primary_btn")
        search_btn.clicked.connect(self._search)
        filter_layout.addWidget(search_btn)
        filter_layout.addStretch()

        layout.addWidget(filter_frame)

        # Results table (with Customer ID column)
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["Transaction ID", "Customer ID", "Market", "Vendor", "Receipt Total",
             "Status", "Created", "Actions"]
        )
        configure_table(self.table, actions_col=7, actions_width=120)
        layout.addWidget(self.table)

        # Audit log preview (includes Changed By column)
        layout.addWidget(QLabel("Recent Audit Log:"))
        self.audit_table = QTableWidget()
        self.audit_table.setColumnCount(8)
        self.audit_table.setHorizontalHeaderLabels(
            ["Time", "Table", "Record ID", "Action", "Changed By",
             "Field", "Old Value", "New Value"]
        )
        configure_table(self.audit_table)
        self.audit_table.setMaximumHeight(200)
        layout.addWidget(self.audit_table)

        layout.addStretch()

    def refresh(self):
        self._load_market_days()
        self._search()
        self._load_audit_log()

    def _load_market_days(self):
        days = get_all_market_days()
        self.md_filter.clear()
        self.md_filter.addItem("All", userData=None)
        for d in days:
            self.md_filter.addItem(f"{d['market_name']} - {d['date']}", userData=d['id'])

    def _search(self):
        md_id = self.md_filter.currentData()
        status = self.status_filter.currentText()
        if status == "All":
            status = None
        fam_id = self.id_search.text().strip() or None

        txns = search_transactions(
            market_day_id=md_id, status=status, fam_id_search=fam_id
        )

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(txns))
        for i, t in enumerate(txns):
            self.table.setItem(i, 0, make_item(t['fam_transaction_id']))
            self.table.setItem(i, 1, make_item(t.get('customer_label') or ''))
            self.table.setItem(i, 2, make_item(f"{t['market_name']} - {t['market_day_date']}"))
            self.table.setItem(i, 3, make_item(t['vendor_name']))
            self.table.setItem(i, 4, make_item(f"${t['receipt_total']:.2f}", t['receipt_total']))
            self.table.setItem(i, 5, make_item(t['status']))
            self.table.setItem(i, 6, make_item(str(t.get('created_at', ''))))

            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(3)

            txn_id = t['id']
            if t['status'] != 'Voided':
                adj_btn = make_action_btn("Adjust", 50)
                adj_btn.clicked.connect(lambda checked, tid=txn_id: self._adjust_transaction(tid))
                action_layout.addWidget(adj_btn)

                void_btn = make_action_btn("Void", 40, danger=True)
                void_btn.clicked.connect(lambda checked, tid=txn_id: self._void_transaction(tid))
                action_layout.addWidget(void_btn)

            self.table.setCellWidget(i, 7, action_widget)
            self.table.setRowHeight(i, 32)

        self.table.setSortingEnabled(True)

    def _adjust_transaction(self, txn_id):
        txn = get_transaction_by_id(txn_id)
        if not txn:
            return

        dialog = AdjustmentDialog(txn, self)

        # Pre-fill "Adjusted By" with the open market day's volunteer
        open_md = get_open_market_day()
        if open_md and open_md.get('opened_by'):
            dialog.adjusted_by_input.setText(open_md['opened_by'])

        if dialog.exec() == QDialog.Accepted:
            adjusted_by = dialog.adjusted_by_input.text().strip() or "Admin"
            reason = dialog.reason_combo.currentText()
            notes = dialog.notes_input.toPlainText().strip()
            new_total = dialog.receipt_spin.value()
            new_vendor = dialog.vendor_combo.currentData()

            if new_total != txn['receipt_total']:
                log_action('transactions', txn_id, 'ADJUST', adjusted_by,
                            field_name='receipt_total',
                            old_value=txn['receipt_total'],
                            new_value=new_total,
                            reason_code=reason, notes=notes)
                update_transaction(txn_id, receipt_total=new_total)

            if new_vendor != txn['vendor_id']:
                log_action('transactions', txn_id, 'ADJUST', adjusted_by,
                            field_name='vendor_id',
                            old_value=txn['vendor_id'],
                            new_value=new_vendor,
                            reason_code=reason, notes=notes)
                update_transaction(txn_id, vendor_id=new_vendor)

            update_transaction(txn_id, status='Adjusted')
            self._search()
            self._load_audit_log()

    def _void_transaction(self, txn_id):
        txn = get_transaction_by_id(txn_id)
        if not txn:
            return

        result = QMessageBox.warning(
            self, "Void Transaction",
            f"Are you sure you want to void transaction {txn['fam_transaction_id']}?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )
        if result == QMessageBox.Yes:
            # Use the open market day volunteer's name as changed_by
            open_md = get_open_market_day()
            changed_by = (open_md.get('opened_by') if open_md else None) or 'Admin'
            log_action('transactions', txn_id, 'VOID', changed_by,
                        reason_code='admin_adjustment', notes='Transaction voided')
            void_transaction(txn_id)
            self._search()
            self._load_audit_log()

    def _load_audit_log(self):
        entries = get_audit_log(limit=20)
        self.audit_table.setSortingEnabled(False)
        self.audit_table.setRowCount(len(entries))
        for i, e in enumerate(entries):
            self.audit_table.setItem(i, 0, make_item(str(e.get('changed_at', ''))))
            self.audit_table.setItem(i, 1, make_item(e['table_name']))
            self.audit_table.setItem(i, 2, make_item(str(e['record_id']), e['record_id']))
            self.audit_table.setItem(i, 3, make_item(e['action']))
            self.audit_table.setItem(i, 4, make_item(e.get('changed_by') or ''))
            self.audit_table.setItem(i, 5, make_item(e.get('field_name') or ''))
            self.audit_table.setItem(i, 6, make_item(str(e.get('old_value') or '')))
            self.audit_table.setItem(i, 7, make_item(str(e.get('new_value') or '')))
        self.audit_table.setSortingEnabled(True)
