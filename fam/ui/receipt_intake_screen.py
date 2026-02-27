"""Screen B: Receipt Intake — multi-receipt customer order flow."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QDoubleSpinBox, QPushButton, QFrame, QMessageBox, QTableWidget,
    QAbstractItemView, QScrollArea
)
from PySide6.QtCore import Signal, Qt

from fam.models.market_day import get_open_market_day
from fam.models.vendor import get_all_vendors, get_vendors_for_market
from fam.models.transaction import create_transaction, void_transaction
from fam.models.customer_order import (
    create_customer_order, get_order_transactions, get_order_total,
    void_customer_order
)
from fam.ui.styles import (
    PRIMARY_GREEN, WHITE, LIGHT_GRAY, HARVEST_GOLD, ERROR_COLOR,
    FIELD_LABEL_BG, ACCENT_GREEN, BACKGROUND, SUBTITLE_GRAY, ERROR_BG,
    WARNING_COLOR, WARNING_BG
)
from fam.ui.helpers import make_field_label, make_item, make_action_btn, configure_table


class ReceiptIntakeScreen(QWidget):
    """Receipt Intake screen with multi-receipt customer order tracking."""

    customer_order_ready = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_market_day = None
        self._current_order_id = None
        self._current_customer_label = None
        self._order_receipts = []
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Wrap everything in a scroll area so tall content is never clipped
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background-color: {BACKGROUND}; }}")

        inner_widget = QWidget()
        layout = QVBoxLayout(inner_widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Title
        title = QLabel("Receipt Intake")
        title.setObjectName("screen_title")
        layout.addWidget(title)

        subtitle = QLabel("Add one or more receipts for a customer, then proceed to payment")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        # Status message (hidden by default)
        self.status_label = QLabel("")
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        # ── Customer / Market info bar ──────────────────────────────
        self.customer_frame = QFrame()
        self.customer_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
                padding: 12px 16px;
            }}
        """)
        cust_layout = QHBoxLayout(self.customer_frame)
        cust_layout.setSpacing(12)
        cust_layout.setContentsMargins(0, 0, 0, 0)

        cust_layout.addWidget(make_field_label("Customer"))
        self.customer_label = QLabel("—")
        self.customer_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {HARVEST_GOLD};"
            f" min-height: 20px; max-height: 20px;"
            f" padding: 8px 12px; border: 2px solid transparent;"
        )
        cust_layout.addWidget(self.customer_label)

        cust_layout.addWidget(make_field_label("Market"))
        self.market_label = QLabel("No active market day")
        self.market_label.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: #555555;"
            f" background-color: {FIELD_LABEL_BG}; border: 2px solid #D5D2CB; border-radius: 6px;"
            f" min-height: 20px; max-height: 20px; padding: 8px 12px;"
        )
        cust_layout.addWidget(self.market_label, 1)

        # Status message (top-right, hidden by default)
        self.status_msg_label = QLabel("")
        self.status_msg_label.setVisible(False)
        self.status_msg_label.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {PRIMARY_GREEN};"
            f" min-height: 20px; max-height: 20px;"
            f" padding: 8px 8px; border: 2px solid transparent;"
        )
        cust_layout.addWidget(self.status_msg_label)

        self.new_customer_btn = QPushButton("New Customer")
        self.new_customer_btn.setObjectName("secondary_btn")
        self.new_customer_btn.setStyleSheet(
            "min-height: 20px; max-height: 20px; padding: 8px 16px;"
        )
        self.new_customer_btn.clicked.connect(self._start_new_customer)
        cust_layout.addWidget(self.new_customer_btn)

        layout.addWidget(self.customer_frame)

        # ── Receipt entry form ──────────────────────────────────────
        form_frame = QFrame()
        form_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
                padding: 12px 16px;
            }}
        """)
        form_layout = QVBoxLayout(form_frame)
        form_layout.setSpacing(8)

        # Vendor + Receipt total on one row
        row_top = QHBoxLayout()
        row_top.addWidget(make_field_label("Vendor"))
        self.vendor_combo = QComboBox()
        self.vendor_combo.setMinimumWidth(250)
        row_top.addWidget(self.vendor_combo, 1)

        row_top.addWidget(make_field_label("Receipt Total"))
        self.receipt_total_spin = QDoubleSpinBox()
        self.receipt_total_spin.setRange(0.00, 99999.99)
        self.receipt_total_spin.setDecimals(2)
        self.receipt_total_spin.setSingleStep(1.00)
        self.receipt_total_spin.setPrefix("$")
        self.receipt_total_spin.setMinimumWidth(140)
        self.receipt_total_spin.setValue(0.00)
        self.receipt_total_spin.setSpecialValueText("$0.00")
        # Select all text on focus so user can just type a new value
        self.receipt_total_spin.lineEdit().installEventFilter(self)
        row_top.addWidget(self.receipt_total_spin)
        form_layout.addLayout(row_top)

        # Notes + Add button on one row
        row_bottom = QHBoxLayout()
        row_bottom.addWidget(make_field_label("Notes"))
        self.notes_input = QLineEdit()
        self.notes_input.setPlaceholderText("Optional")
        row_bottom.addWidget(self.notes_input, 1)

        self.add_receipt_btn = QPushButton("Add Receipt to Order")
        self.add_receipt_btn.setObjectName("primary_btn")
        self.add_receipt_btn.clicked.connect(self._add_receipt)
        row_bottom.addWidget(self.add_receipt_btn)
        form_layout.addLayout(row_bottom)

        # Error message
        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"""
            color: {ERROR_COLOR}; font-weight: bold;
            background-color: {ERROR_BG};
            border: 1px solid {ERROR_COLOR};
            border-radius: 8px;
            padding: 6px 10px;
        """)
        self.error_label.setVisible(False)
        form_layout.addWidget(self.error_label)

        layout.addWidget(form_frame)

        # Success frame removed — status messages now appear at top-right

        # ── Receipts table for this customer ────────────────────────
        self.receipts_frame = QFrame()
        self.receipts_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
                padding: 12px 16px;
            }}
        """)
        receipts_inner = QVBoxLayout(self.receipts_frame)
        receipts_inner.setSpacing(8)

        self.receipts_header = QLabel("Receipts for this Customer:")
        self.receipts_header.setObjectName("section_header")
        receipts_inner.addWidget(self.receipts_header)

        self.receipts_table = QTableWidget()
        self.receipts_table.setColumnCount(5)
        self.receipts_table.setHorizontalHeaderLabels(
            ["Transaction ID", "Vendor", "Receipt Total", "Notes", "Actions"]
        )
        configure_table(self.receipts_table, actions_col=4, actions_width=80)
        # No max height — let the table show all rows comfortably
        self.receipts_table.setMinimumHeight(120)
        receipts_inner.addWidget(self.receipts_table)

        # Running total + action buttons in one row
        action_row = QHBoxLayout()

        self.void_all_btn = QPushButton("Reset / Void All")
        self.void_all_btn.setObjectName("danger_btn")
        self.void_all_btn.clicked.connect(self._void_all)
        action_row.addWidget(self.void_all_btn)

        action_row.addStretch()

        action_row.addWidget(QLabel("Order Total:"))
        self.running_total_label = QLabel("$0.00")
        self.running_total_label.setStyleSheet(
            f"font-size: 22px; font-weight: bold; color: {HARVEST_GOLD};"
        )
        action_row.addWidget(self.running_total_label)

        self.proceed_btn = QPushButton("Confirm All \u2013 Proceed to Payment \u2192")
        self.proceed_btn.setObjectName("primary_btn")
        self.proceed_btn.clicked.connect(self._proceed_to_payment)
        action_row.addWidget(self.proceed_btn)

        receipts_inner.addLayout(action_row)

        self.receipts_frame.setVisible(False)
        layout.addWidget(self.receipts_frame)

        layout.addStretch()

        scroll.setWidget(inner_widget)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    def refresh(self):
        self._load_vendors()
        self._update_market_status()
        self._reset_customer_session()

    def _load_vendors(self):
        """Load vendors — filtered to market assignments when available."""
        self.vendor_combo.clear()
        if self._active_market_day:
            market_id = self._active_market_day.get('market_id')
            vendors = get_vendors_for_market(market_id) if market_id else []
            if not vendors:
                # Fallback: no assignments yet → show all active vendors
                vendors = get_all_vendors(active_only=True)
        else:
            vendors = get_all_vendors(active_only=True)
        for v in vendors:
            self.vendor_combo.addItem(v['name'], userData=v['id'])

    def eventFilter(self, obj, event):
        """Select all text in receipt total on focus for easy overwrite."""
        from PySide6.QtCore import QEvent as _QE
        if obj is self.receipt_total_spin.lineEdit() and event.type() == _QE.FocusIn:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.receipt_total_spin.selectAll)
        return super().eventFilter(obj, event)

    def _update_market_status(self):
        open_md = get_open_market_day()
        if open_md:
            self.market_label.setText(f"{open_md['market_name']} — {open_md['date']}")
            self.add_receipt_btn.setEnabled(True)
            self.status_label.setVisible(False)
            self._active_market_day = open_md
        else:
            self.market_label.setText("No active market day")
            self.add_receipt_btn.setEnabled(False)
            self.status_label.setText(
                "Please open a market day first from the Market screen."
            )
            self.status_label.setStyleSheet(f"""
                background-color: {WARNING_BG}; color: #E65100;
                border: 1px solid {WARNING_COLOR}; border-radius: 8px;
                padding: 10px 16px; font-weight: bold;
            """)
            self.status_label.setVisible(True)
            self._active_market_day = None

    # ------------------------------------------------------------------
    # Customer session management
    # ------------------------------------------------------------------
    def _reset_customer_session(self):
        self._current_order_id = None
        self._current_customer_label = None
        self._order_receipts = []
        self.customer_label.setText("—")
        self.receipts_frame.setVisible(False)
        self.status_msg_label.setVisible(False)
        self.error_label.setVisible(False)
        self.error_label.setText("")
        self.receipt_total_spin.setValue(0.00)
        self.notes_input.clear()
        self._refresh_receipts_table()

    def _ensure_customer_order(self):
        if self._current_order_id is not None:
            return
        if not self._active_market_day:
            return
        order_id, label = create_customer_order(self._active_market_day['id'])
        self._current_order_id = order_id
        self._current_customer_label = label
        self.customer_label.setText(label)

    def _start_new_customer(self):
        if self._order_receipts:
            answer = QMessageBox.question(
                self, "Start New Customer?",
                f"Customer {self._current_customer_label} has "
                f"{len(self._order_receipts)} receipt(s) in progress.\n\n"
                "Do you want to void all current entries and start a new customer?",
                QMessageBox.Yes | QMessageBox.No
            )
            if answer != QMessageBox.Yes:
                return
            if self._current_order_id:
                void_customer_order(self._current_order_id)
        self._reset_customer_session()

    # ------------------------------------------------------------------
    # Receipt CRUD
    # ------------------------------------------------------------------
    def _add_receipt(self):
        self.error_label.setVisible(False)
        self.error_label.setText("")
        self.status_msg_label.setVisible(False)

        if not self._active_market_day:
            self._show_error("No active market day. Please open one first.")
            return

        vendor_id = self.vendor_combo.currentData()
        if not vendor_id:
            self._show_error("Please select a vendor.")
            return

        receipt_total = self.receipt_total_spin.value()
        if receipt_total <= 0:
            self._show_error("Receipt total must be greater than $0.00.")
            return

        notes_text = self.notes_input.text().strip() or None
        vendor_name = self.vendor_combo.currentText()

        try:
            self._ensure_customer_order()
            txn_id, fam_txn_id = create_transaction(
                market_day_id=self._active_market_day['id'],
                vendor_id=vendor_id,
                receipt_total=receipt_total,
                notes=notes_text,
                market_day_date=self._active_market_day['date'],
                customer_order_id=self._current_order_id,
            )

            self._order_receipts.append({
                'txn_id': txn_id,
                'fam_txn_id': fam_txn_id,
                'vendor_name': vendor_name,
                'receipt_total': receipt_total,
                'notes': notes_text or '',
            })

            # Show brief status at top-right of the customer info bar
            self.status_msg_label.setText(f"\u2714 Receipt Added  ({fam_txn_id})")
            self.status_msg_label.setVisible(True)

            self.receipt_total_spin.setValue(0.00)
            self.notes_input.clear()

            self._refresh_receipts_table()
            self.receipts_frame.setVisible(True)

        except Exception as e:
            self._show_error(f"Error saving receipt: {str(e)}")

    def _remove_receipt(self, index):
        if index < 0 or index >= len(self._order_receipts):
            return
        entry = self._order_receipts[index]
        void_transaction(entry['txn_id'])
        self._order_receipts.pop(index)
        self._refresh_receipts_table()

        if not self._order_receipts:
            self.receipts_frame.setVisible(False)

    def _refresh_receipts_table(self):
        self.receipts_table.setSortingEnabled(False)
        self.receipts_table.setRowCount(len(self._order_receipts))

        running_total = 0.0
        for i, r in enumerate(self._order_receipts):
            self.receipts_table.setItem(i, 0, make_item(r['fam_txn_id']))
            self.receipts_table.setItem(i, 1, make_item(r['vendor_name']))
            self.receipts_table.setItem(
                i, 2, make_item(f"${r['receipt_total']:.2f}", r['receipt_total'])
            )
            self.receipts_table.setItem(i, 3, make_item(r.get('notes', '')))

            remove_btn = make_action_btn("✕", 40, danger=True)
            remove_btn.clicked.connect(
                lambda checked, idx=i: self._remove_receipt(idx)
            )
            self.receipts_table.setCellWidget(i, 4, remove_btn)
            self.receipts_table.setRowHeight(i, 32)
            running_total += r['receipt_total']

        self.receipts_table.setSortingEnabled(True)
        self.running_total_label.setText(f"${running_total:.2f}")
        count = len(self._order_receipts)
        self.receipts_header.setText(
            f"Receipts for Customer {self._current_customer_label or '—'}:  "
            f"({count} receipt{'s' if count != 1 else ''})"
        )

    # ------------------------------------------------------------------
    # Order-level actions
    # ------------------------------------------------------------------
    def _void_all(self):
        if not self._order_receipts:
            return
        answer = QMessageBox.warning(
            self, "Void All Receipts?",
            f"This will void all {len(self._order_receipts)} receipt(s) "
            f"for customer {self._current_customer_label}.\n\nAre you sure?",
            QMessageBox.Yes | QMessageBox.No
        )
        if answer != QMessageBox.Yes:
            return
        if self._current_order_id:
            void_customer_order(self._current_order_id)
        self._reset_customer_session()

    def _proceed_to_payment(self):
        if not self._order_receipts:
            self._show_error("No receipts to process. Add at least one receipt first.")
            return
        if not self._current_order_id:
            return
        self.customer_order_ready.emit(self._current_order_id)

    def start_fresh_after_payment(self):
        self._reset_customer_session()
        self._update_market_status()
        self._load_vendors()

    # ------------------------------------------------------------------
    def _show_error(self, msg):
        self.error_label.setText(msg)
        self.error_label.setVisible(True)
