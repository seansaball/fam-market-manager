"""Screen C: Payment Processing — supports multi-receipt customer orders."""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QLineEdit, QMessageBox, QTableWidget
)
from PySide6.QtCore import Signal, Qt

from fam.database.connection import get_connection
from fam.models.transaction import (
    get_transaction_by_id, confirm_transaction, save_payment_line_items,
    get_payment_line_items, update_transaction
)
from fam.models.customer_order import (
    get_customer_order, get_order_transactions, get_order_total,
    get_order_vendor_summary, update_customer_order_status,
    get_customer_prior_match
)

logger = logging.getLogger('fam.ui.payment_screen')
from fam.utils.calculations import calculate_payment_breakdown
from fam.ui.widgets.payment_row import PaymentRow
from fam.ui.widgets.summary_card import SummaryRow
from fam.ui.styles import (
    PRIMARY_GREEN, WHITE, LIGHT_GRAY, HARVEST_GOLD, ERROR_COLOR, ACCENT_GREEN,
    BACKGROUND, FIELD_LABEL_BG, MEDIUM_GRAY, SUBTITLE_GRAY, SUCCESS_BG,
    ERROR_BG, WARNING_BG, WARNING_COLOR
)
from fam.ui.helpers import make_field_label, make_item, configure_table


class PaymentScreen(QWidget):
    """Payment Processing screen — handles customer orders with multiple receipts."""

    payment_confirmed = Signal()
    draft_saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_order_id = None
        self._order_transactions = []
        self._order_total = 0.0
        self._match_limit = None  # None = no cap, float = remaining cap value
        self._daily_limit = None  # Full daily limit from market settings
        self._prior_match = 0.0   # FAM match already used by this customer today
        self._payment_rows = []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background-color: {BACKGROUND}; }}")

        inner_widget = QWidget()
        layout = QVBoxLayout(inner_widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # Title
        title = QLabel("Payment Processing")
        title.setObjectName("screen_title")
        layout.addWidget(title)

        subtitle = QLabel("Allocate payment methods and confirm collection")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        # ── Customer order info bar ─────────────────────────────────
        self.order_info_frame = QFrame()
        self.order_info_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
                padding: 12px 16px;
            }}
        """)
        info_layout = QHBoxLayout(self.order_info_frame)

        self.customer_id_label = QLabel("No order loaded")
        self.customer_id_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {PRIMARY_GREEN};"
        )
        info_layout.addWidget(self.customer_id_label)

        self.order_receipts_label = QLabel("")
        self.order_receipts_label.setStyleSheet("font-size: 13px; color: {SUBTITLE_GRAY};")
        info_layout.addWidget(self.order_receipts_label)

        info_layout.addStretch()
        info_layout.addWidget(QLabel("Order Total:"))
        self.order_total_label = QLabel("$0.00")
        self.order_total_label.setStyleSheet(
            f"font-size: 22px; font-weight: bold; color: {HARVEST_GOLD};"
        )
        info_layout.addWidget(self.order_total_label)

        layout.addWidget(self.order_info_frame)

        # Match limit info label
        self.match_limit_label = QLabel("")
        self.match_limit_label.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {SUBTITLE_GRAY}; padding: 2px 4px;"
        )
        self.match_limit_label.setVisible(False)
        layout.addWidget(self.match_limit_label)

        # Match cap warning (shown when the cap is hit)
        self.match_cap_warning = QLabel("")
        self.match_cap_warning.setStyleSheet(f"""
            font-size: 12px; font-weight: bold; color: {HARVEST_GOLD};
            background-color: {WARNING_BG};
            border: 1px solid {WARNING_COLOR};
            border-radius: 6px;
            padding: 6px 10px;
        """)
        self.match_cap_warning.setVisible(False)
        layout.addWidget(self.match_cap_warning)

        # ── Summary cards row (at the top for visibility) ──────────
        self.summary_row = SummaryRow()
        self.summary_row.add_card("allocated", "Total Allocated")
        self.summary_row.add_card("remaining", "Remaining to Allocate", highlight=True)
        self.summary_row.add_card("customer_pays", "Customer Pays")
        self.summary_row.add_card("fam_match", "FAM Match", highlight=True)
        self.summary_row.add_card("vendor_reimburse", "Vendor Reimbursement")

        # Initial color setup: vendor reimburse grey, FAM match green
        self.summary_row.update_card_color("vendor_reimburse", MEDIUM_GRAY)
        self.summary_row.update_card_color("fam_match", PRIMARY_GREEN)

        layout.addWidget(self.summary_row)

        # ── Vendor summary table ────────────────────────────────────
        self.vendor_lbl = QLabel("Vendor Breakdown:")
        self.vendor_lbl.setStyleSheet("font-weight: bold;")
        self.vendor_lbl.setVisible(False)
        layout.addWidget(self.vendor_lbl)

        self.vendor_table = QTableWidget()
        self.vendor_table.setColumnCount(2)
        self.vendor_table.setHorizontalHeaderLabels(["Vendor", "Receipt Total"])
        configure_table(self.vendor_table)
        self.vendor_table.setMinimumHeight(80)
        self.vendor_table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
            }}
        """)
        self.vendor_table.setVisible(False)
        layout.addWidget(self.vendor_table)

        # ── Payment rows area ───────────────────────────────────────
        payment_header = QHBoxLayout()
        pay_lbl = QLabel("Payment Methods:")
        pay_lbl.setStyleSheet("font-weight: bold;")
        payment_header.addWidget(pay_lbl)
        self.add_method_btn = QPushButton("+ Add Payment Method")
        self.add_method_btn.setObjectName("secondary_btn")
        self.add_method_btn.clicked.connect(self._add_payment_row)
        payment_header.addStretch()
        payment_header.addWidget(self.add_method_btn)
        layout.addLayout(payment_header)

        # Scrollable payment rows
        self.rows_container = QWidget()
        self.rows_container.setStyleSheet(f"background-color: {BACKGROUND};")
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(4, 4, 4, 4)
        self.rows_layout.setSpacing(8)
        self.rows_layout.addStretch()

        pay_scroll = QScrollArea()
        pay_scroll.setWidgetResizable(True)
        pay_scroll.setWidget(self.rows_container)
        pay_scroll.setMinimumHeight(120)
        pay_scroll.setMaximumHeight(350)
        pay_scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: {BACKGROUND};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
            }}
        """)
        layout.addWidget(pay_scroll)

        # SNAP reference code
        snap_row = QHBoxLayout()
        snap_row.addWidget(QLabel("SNAP Reference Code (optional):"))
        self.snap_ref_input = QLineEdit()
        self.snap_ref_input.setPlaceholderText("Enter SNAP approval code if applicable")
        self.snap_ref_input.setMaximumWidth(300)
        snap_row.addWidget(self.snap_ref_input)
        snap_row.addStretch()
        layout.addLayout(snap_row)

        # Error/validation message
        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"""
            color: {ERROR_COLOR}; font-weight: bold;
            background-color: {ERROR_BG};
            border: 1px solid {ERROR_COLOR};
            border-radius: 8px;
            padding: 6px 10px;
        """)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        # ── Bottom area: collection checklist + action buttons ──────
        bottom_frame = QFrame()
        bottom_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
                padding: 12px 16px;
            }}
        """)
        bottom_layout = QHBoxLayout(bottom_frame)
        bottom_layout.setSpacing(20)

        # Left side: collection checklist
        collect_side = QVBoxLayout()
        collect_side.setSpacing(4)
        collect_header = QLabel("Collect from Customer:")
        collect_header.setStyleSheet("font-weight: bold; font-size: 13px;")
        collect_side.addWidget(collect_header)

        self.collect_list_layout = QVBoxLayout()
        self.collect_list_layout.setSpacing(2)
        collect_side.addLayout(self.collect_list_layout)

        self.collect_total_label = QLabel("Total: $0.00")
        self.collect_total_label.setStyleSheet(
            f"font-weight: bold; font-size: 14px; color: {HARVEST_GOLD}; padding-top: 4px;"
        )
        collect_side.addWidget(self.collect_total_label)
        collect_side.addStretch()

        bottom_layout.addLayout(collect_side, 1)

        # Right side: action buttons (vertically stacked)
        btn_side = QVBoxLayout()
        btn_side.setSpacing(8)
        btn_side.addStretch()

        self.confirm_btn = QPushButton("Confirm Payment")
        self.confirm_btn.setObjectName("primary_btn")
        self.confirm_btn.clicked.connect(self._confirm_payment)
        btn_side.addWidget(self.confirm_btn)

        self.save_draft_btn = QPushButton("Save as Draft")
        self.save_draft_btn.setObjectName("secondary_btn")
        self.save_draft_btn.clicked.connect(self._save_draft)
        btn_side.addWidget(self.save_draft_btn)

        bottom_layout.addLayout(btn_side)

        layout.addWidget(bottom_frame)

        # Success message
        self.success_frame = QFrame()
        self.success_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {SUCCESS_BG};
                border: 1px solid {ACCENT_GREEN};
                border-radius: 8px;
                padding: 12px 16px;
            }}
        """)
        self.success_frame.setVisible(False)
        success_layout = QVBoxLayout(self.success_frame)
        self.success_msg = QLabel("")
        self.success_msg.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {PRIMARY_GREEN};"
        )
        success_layout.addWidget(self.success_msg)
        layout.addWidget(self.success_frame)

        layout.addStretch()

        scroll.setWidget(inner_widget)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------
    # Load customer order
    # ------------------------------------------------------------------
    def load_customer_order(self, order_id):
        self._current_order_id = order_id
        self.success_frame.setVisible(False)
        self.error_label.setVisible(False)
        self.error_label.setText("")
        self.match_cap_warning.setVisible(False)

        order = get_customer_order(order_id)
        if not order:
            self.customer_id_label.setText("Order not found")
            return

        # Determine match limit from market settings, accounting for prior usage
        if order.get('match_limit_active'):
            self._daily_limit = order.get('daily_match_limit') or 100.00

            # Check for prior FAM match usage by this customer today
            self._prior_match = get_customer_prior_match(
                order['customer_label'],
                order['market_day_id'],
                exclude_order_id=order_id
            )
            remaining_limit = round(max(self._daily_limit - self._prior_match, 0.0), 2)
            self._match_limit = remaining_limit

            if self._prior_match > 0:
                self.match_limit_label.setText(
                    f"Daily match limit: ${self._daily_limit:.2f} per customer  \u2502  "
                    f"Previously redeemed: ${self._prior_match:.2f}  \u2502  "
                    f"Remaining: ${remaining_limit:.2f}"
                )
            else:
                self.match_limit_label.setText(
                    f"Daily match limit: ${self._daily_limit:.2f} per customer"
                )
            self.match_limit_label.setVisible(True)
        else:
            self._match_limit = None
            self._daily_limit = None
            self._prior_match = 0.0
            self.match_limit_label.setText("Match limit: Off")
            self.match_limit_label.setVisible(True)

        self._order_transactions = get_order_transactions(order_id)
        self._order_total = sum(t['receipt_total'] for t in self._order_transactions)

        self.customer_id_label.setText(
            f"Customer {order['customer_label']}  —  {order['market_name']}"
        )
        self.order_receipts_label.setText(
            f"{len(self._order_transactions)} receipt(s)"
        )
        self.order_total_label.setText(f"${self._order_total:.2f}")

        self._populate_vendor_summary()
        self._clear_payment_rows()

        if self._order_transactions:
            first_txn = self._order_transactions[0]
            items = get_payment_line_items(first_txn['id'])
            if items:
                for item in items:
                    row = self._add_payment_row()
                    row.set_data(item['payment_method_id'], item['method_amount'])
            else:
                self._add_payment_row()
        else:
            self._add_payment_row()

        self._update_summary()

        any_editable = any(
            t['status'] in ('Draft', 'Adjusted') for t in self._order_transactions
        )
        self.confirm_btn.setEnabled(any_editable)
        self.save_draft_btn.setEnabled(any_editable)
        self.add_method_btn.setEnabled(any_editable)

    def load_transaction(self, txn_id):
        """Legacy: load a single transaction (e.g. from admin screen)."""
        txn = get_transaction_by_id(txn_id)
        if not txn:
            return
        if txn.get('customer_order_id'):
            self.load_customer_order(txn['customer_order_id'])
        else:
            self._current_order_id = None
            self._match_limit = None
            self._daily_limit = None
            self._prior_match = 0.0
            self.match_limit_label.setVisible(False)
            self.match_cap_warning.setVisible(False)
            self._order_transactions = [txn]
            self._order_total = txn['receipt_total']

            self.customer_id_label.setText(txn['fam_transaction_id'])
            self.order_receipts_label.setText(f"Vendor: {txn['vendor_name']}")
            self.order_total_label.setText(f"${txn['receipt_total']:.2f}")

            self._populate_vendor_summary()
            self._clear_payment_rows()

            items = get_payment_line_items(txn_id)
            if items:
                for item in items:
                    row = self._add_payment_row()
                    row.set_data(item['payment_method_id'], item['method_amount'])
            else:
                self._add_payment_row()

            self._update_summary()

            is_editable = txn['status'] in ('Draft', 'Adjusted')
            self.confirm_btn.setEnabled(is_editable)
            self.save_draft_btn.setEnabled(is_editable)
            self.add_method_btn.setEnabled(is_editable)

    # ------------------------------------------------------------------
    # Vendor summary table
    # ------------------------------------------------------------------
    def _populate_vendor_summary(self):
        vendor_totals = {}
        for t in self._order_transactions:
            name = t['vendor_name']
            vendor_totals[name] = vendor_totals.get(name, 0.0) + t['receipt_total']

        rows = sorted(vendor_totals.items())
        self.vendor_table.setSortingEnabled(False)
        self.vendor_table.setRowCount(len(rows))
        for i, (vname, vtotal) in enumerate(rows):
            self.vendor_table.setItem(i, 0, make_item(vname))
            self.vendor_table.setItem(i, 1, make_item(f"${vtotal:.2f}", vtotal))
            self.vendor_table.setRowHeight(i, 28)
        self.vendor_table.setSortingEnabled(True)

        has_vendors = len(rows) > 0
        self.vendor_lbl.setVisible(has_vendors)
        self.vendor_table.setVisible(has_vendors)

    # ------------------------------------------------------------------
    # Payment rows
    # ------------------------------------------------------------------
    def _add_payment_row(self):
        row = PaymentRow()
        row.changed.connect(self._update_summary)
        row.remove_requested.connect(self._remove_payment_row)
        self._payment_rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        return row

    def _remove_payment_row(self, row):
        if len(self._payment_rows) <= 1:
            return
        self._payment_rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self._update_summary()

    def _clear_payment_rows(self):
        for row in self._payment_rows:
            self.rows_layout.removeWidget(row)
            row.deleteLater()
        self._payment_rows.clear()

    # ------------------------------------------------------------------
    # Summary / breakdown
    # ------------------------------------------------------------------
    def _update_summary(self):
        receipt_total = self._order_total
        entries = []
        for row in self._payment_rows:
            data = row.get_data()
            if data:
                entries.append({
                    'method_amount': data['method_amount'],
                    'match_percent': data['match_percent'],
                    'method_name': data['method_name_snapshot'],
                })

        if entries:
            calc_entries = [
                {'method_amount': e['method_amount'], 'match_percent': e['match_percent']}
                for e in entries
            ]
            result = calculate_payment_breakdown(
                receipt_total, calc_entries, match_limit=self._match_limit
            )
            allocated = result['allocated_total']
            remaining = result['allocation_remaining']
            fam_match = result['fam_subsidy_total']

            self.summary_row.update_card("allocated", f"${allocated:.2f}")
            self.summary_row.update_card("remaining", f"${remaining:.2f}")
            self.summary_row.update_card("customer_pays", f"${result['customer_total_paid']:.2f}")
            self.summary_row.update_card("fam_match", f"${fam_match:.2f}")
            self.summary_row.update_card("vendor_reimburse", f"${receipt_total:.2f}")

            # Show/hide match cap warning
            if result.get('match_was_capped'):
                uncapped = result['uncapped_fam_subsidy_total']
                if self._prior_match > 0:
                    self.match_cap_warning.setText(
                        f"FAM Match capped at ${self._match_limit:.2f} \u2014 "
                        f"this customer already redeemed ${self._prior_match:.2f} "
                        f"of their ${self._daily_limit:.2f} daily limit"
                    )
                else:
                    self.match_cap_warning.setText(
                        f"FAM Match capped at ${self._match_limit:.2f} "
                        f"(daily limit per customer \u2014 "
                        f"uncapped would be ${uncapped:.2f})"
                    )
                self.match_cap_warning.setVisible(True)
            else:
                self.match_cap_warning.setVisible(False)

            # Dynamic color-coding
            # Remaining: green when $0, red when over-allocated, gold when under
            if remaining == 0:
                self.summary_row.update_card_color("remaining", PRIMARY_GREEN)
                self.summary_row.update_card_color("allocated", PRIMARY_GREEN)
            elif remaining < 0:
                self.summary_row.update_card_color("remaining", ERROR_COLOR)
                self.summary_row.update_card_color("allocated", ERROR_COLOR)
            else:
                self.summary_row.update_card_color("remaining", HARVEST_GOLD)
                self.summary_row.update_card_color("allocated", HARVEST_GOLD)

            # Vendor reimbursement: green when fully allocated, grey otherwise
            if remaining == 0:
                self.summary_row.update_card_color("vendor_reimburse", PRIMARY_GREEN)
            else:
                self.summary_row.update_card_color("vendor_reimburse", MEDIUM_GRAY)

            # FAM match: green when there's a match, grey when zero
            if fam_match > 0:
                self.summary_row.update_card_color("fam_match", PRIMARY_GREEN)
            else:
                self.summary_row.update_card_color("fam_match", MEDIUM_GRAY)

            self._update_collection_list(entries, result)
        else:
            self.summary_row.update_card("allocated", "$0.00")
            self.summary_row.update_card("remaining", f"${receipt_total:.2f}")
            self.summary_row.update_card("customer_pays", "$0.00")
            self.summary_row.update_card("fam_match", "$0.00")
            self.summary_row.update_card("vendor_reimburse", f"${receipt_total:.2f}")

            # Reset colors to defaults when no entries
            self.summary_row.update_card_color("allocated", MEDIUM_GRAY)
            self.summary_row.update_card_color("remaining", HARVEST_GOLD)
            self.summary_row.update_card_color("customer_pays", PRIMARY_GREEN)
            self.summary_row.update_card_color("fam_match", MEDIUM_GRAY)
            self.summary_row.update_card_color("vendor_reimburse", MEDIUM_GRAY)

            self._clear_collection_list()
            self.match_cap_warning.setVisible(False)

    def _update_collection_list(self, entries, result):
        """Rebuild the compact collection checklist next to the Confirm button."""
        self._clear_collection_list()

        if not result.get('line_items'):
            return

        customer_total = 0.0
        for i, li in enumerate(result['line_items']):
            method_name = entries[i].get('method_name', 'Unknown')
            customer_charged = li['customer_charged']
            match_amount = li['match_amount']
            method_amount = li['method_amount']

            if method_amount <= 0:
                continue

            customer_total += customer_charged

            text = f"•  ${customer_charged:.2f} via {method_name}"
            if match_amount > 0:
                text += f"  (FAM matches ${match_amount:.2f})"

            lbl = QLabel(text)
            lbl.setStyleSheet("font-size: 13px; padding: 1px 0;")
            self.collect_list_layout.addWidget(lbl)

        self.collect_total_label.setText(f"Total to Collect: ${customer_total:.2f}")

    def _clear_collection_list(self):
        while self.collect_list_layout.count():
            child = self.collect_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.collect_total_label.setText("Total: $0.00")

    # ------------------------------------------------------------------
    # Collect payment data
    # ------------------------------------------------------------------
    def _collect_line_items(self):
        items = []
        for row in self._payment_rows:
            data = row.get_data()
            if data and data['method_amount'] > 0:
                items.append(data)
        return items

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _save_draft(self):
        if not self._order_transactions:
            return
        try:
            items = self._collect_line_items()
            if items:
                save_payment_line_items(self._order_transactions[0]['id'], items)

            snap_ref = self.snap_ref_input.text().strip() or None
            if snap_ref:
                for t in self._order_transactions:
                    update_transaction(t['id'], snap_reference_code=snap_ref)

            self.success_frame.setVisible(True)
            self.success_msg.setText("Draft saved successfully.")

            answer = QMessageBox.question(
                self, "Draft Saved",
                "Draft saved successfully.\n\nReturn to Receipt Intake?",
                QMessageBox.Yes | QMessageBox.No
            )
            if answer == QMessageBox.Yes:
                self.draft_saved.emit()
        except Exception as e:
            logger.exception("Failed to save draft")
            self._show_error(f"Error saving draft: {e}")

    def _confirm_payment(self):
        self.error_label.setVisible(False)
        self.error_label.setText("")
        self.success_frame.setVisible(False)

        if not self._order_transactions:
            self._show_error("No transactions loaded.")
            return

        receipt_total = self._order_total
        items = self._collect_line_items()

        if not items:
            self._show_error("At least one payment method with an amount is required.")
            return

        entries = [
            {'method_amount': it['method_amount'], 'match_percent': it['match_percent']}
            for it in items
        ]
        result = calculate_payment_breakdown(
            receipt_total, entries, match_limit=self._match_limit
        )

        if not result['is_valid']:
            self._show_error("\n".join(result['errors']))
            return

        # ── Pre-confirmation dialog: list what to collect ─────────
        confirm_lines = ["Please confirm you have collected the following "
                         "from the customer:\n"]
        customer_total = 0.0
        if result.get('line_items'):
            for i, li in enumerate(result['line_items']):
                method_name = items[i].get('method_name_snapshot', 'Unknown')
                customer_charged = li['customer_charged']
                match_amount = li['match_amount']
                method_amount = li['method_amount']
                if method_amount <= 0:
                    continue
                customer_total += customer_charged
                line = f"  •  ${customer_charged:.2f} via {method_name}"
                if match_amount > 0:
                    line += f"  (FAM matches ${match_amount:.2f})"
                confirm_lines.append(line)

        confirm_lines.append(f"\nTotal to collect: ${customer_total:.2f}")
        confirm_lines.append(f"Order total (vendor reimbursement): ${receipt_total:.2f}")
        confirm_lines.append(f"\nReceipts: {len(self._order_transactions)}")

        answer = QMessageBox.question(
            self, "Confirm Payment Collection",
            "\n".join(confirm_lines),
            QMessageBox.Yes | QMessageBox.No
        )
        if answer != QMessageBox.Yes:
            return

        # ── Process the confirmed payment (atomic) ─────────────
        conn = get_connection()
        try:
            self._distribute_and_save_payments(items, receipt_total, commit=False)

            snap_ref = self.snap_ref_input.text().strip() or None
            for t in self._order_transactions:
                if snap_ref:
                    update_transaction(t['id'], commit=False,
                                       snap_reference_code=snap_ref)
                confirm_transaction(t['id'], confirmed_by="Volunteer", commit=False)

            if self._current_order_id:
                update_customer_order_status(self._current_order_id, 'Confirmed',
                                             commit=False)

            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.exception("Payment confirmation failed — rolled back")
            self._show_error(f"Payment failed: {e}")
            return

        txn_ids = ", ".join(t['fam_transaction_id'] for t in self._order_transactions)
        logger.info("Payment confirmed for: %s", txn_ids)
        self.success_frame.setVisible(True)
        self.success_msg.setText(f"Payment Confirmed!  Transactions: {txn_ids}")
        self.confirm_btn.setEnabled(False)
        self.save_draft_btn.setEnabled(False)
        self.add_method_btn.setEnabled(False)

        answer = QMessageBox.question(
            self, "Payment Confirmed",
            f"All {len(self._order_transactions)} transaction(s) have been confirmed.\n\n"
            "Would you like to return to Receipt Intake for the next customer?",
            QMessageBox.Yes | QMessageBox.No
        )
        if answer == QMessageBox.Yes:
            self.payment_confirmed.emit()

    def _distribute_and_save_payments(self, items, order_total, commit=True):
        if order_total <= 0:
            return

        # Build all per-transaction line items first
        all_txn_items = []
        for t in self._order_transactions:
            proportion = t['receipt_total'] / order_total
            txn_items = []

            for item in items:
                method_amount = round(item['method_amount'] * proportion, 2)
                match_pct = item['match_percent_snapshot']
                match_amount = round(method_amount * (match_pct / (100.0 + match_pct)), 2)
                customer_charged = round(method_amount - match_amount, 2)

                txn_items.append({
                    'payment_method_id': item['payment_method_id'],
                    'method_name_snapshot': item['method_name_snapshot'],
                    'match_percent_snapshot': match_pct,
                    'method_amount': method_amount,
                    'match_amount': match_amount,
                    'customer_charged': customer_charged,
                })

            all_txn_items.append(txn_items)

        # Apply match-limit cap across all transactions
        if self._match_limit is not None:
            total_match = sum(
                li['match_amount']
                for txn_items in all_txn_items
                for li in txn_items
            )
            if total_match > self._match_limit >= 0:
                cap_ratio = self._match_limit / total_match
                for txn_items in all_txn_items:
                    for li in txn_items:
                        li['match_amount'] = round(
                            li['match_amount'] * cap_ratio, 2
                        )
                        li['customer_charged'] = round(
                            li['method_amount'] - li['match_amount'], 2
                        )

        # Save to DB
        for t, txn_items in zip(self._order_transactions, all_txn_items):
            save_payment_line_items(t['id'], txn_items, commit=commit)

    def _show_error(self, msg):
        self.error_label.setText(msg)
        self.error_label.setVisible(True)
