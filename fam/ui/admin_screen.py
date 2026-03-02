"""Screen E: Admin Adjustments."""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QTextEdit, QDialog, QDialogButtonBox,
    QFormLayout
)
from PySide6.QtCore import Qt

from fam.database.connection import get_connection
from fam.models.market_day import get_all_market_days, get_open_market_day
from fam.models.vendor import get_all_vendors
from fam.models.payment_method import get_all_payment_methods, get_payment_methods_for_market
from fam.models.transaction import (
    search_transactions, get_transaction_by_id, update_transaction,
    get_payment_line_items, save_payment_line_items
)
from fam.models.audit import log_action, get_audit_log
from fam.utils.export import write_ledger_backup
from fam.ui.styles import (
    WHITE, LIGHT_GRAY, ERROR_COLOR, PRIMARY_GREEN, WARNING_COLOR,
    BACKGROUND, TEXT_COLOR, SUBTITLE_GRAY,
    SUCCESS_BG, ACCENT_GREEN, WARNING_BG
)
from fam.ui.helpers import (
    make_field_label, make_item, make_action_btn, configure_table,
    NoScrollDoubleSpinBox, NoScrollComboBox
)
from fam.ui.widgets.payment_row import PaymentRow

logger = logging.getLogger('fam.ui.admin_screen')


REASON_CODES = {
    "Data Entry Error": "data_entry_error",
    "Vendor Correction": "vendor_correction",
    "Admin Adjustment": "admin_adjustment",
    "Customer Dispute": "customer_dispute",
    "Other": "other",
}


class AdjustmentDialog(QDialog):
    """Dialog for adjusting a transaction — receipt, vendor, and payment methods."""

    def __init__(self, txn, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Adjust Transaction {txn['fam_transaction_id']}")
        self.setMinimumWidth(700)
        self.txn = txn
        self._market_id = txn.get('market_id')
        self._payment_rows = []

        # Retrieve the market's match limit for accurate calculations
        self._match_limit = None
        if self._market_id:

            conn = get_connection()
            market = conn.execute(
                "SELECT daily_match_limit, match_limit_active FROM markets WHERE id=?",
                (self._market_id,)
            ).fetchone()
            if market and market['match_limit_active']:
                self._match_limit = market['daily_match_limit'] or 100.00
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {BACKGROUND};
            }}
            QLabel {{
                background-color: transparent;
                color: {TEXT_COLOR};
            }}
        """)

        main_layout = QVBoxLayout(self)

        # ── Top fields (form layout) ──────────────────────────
        form = QFormLayout()

        self.receipt_spin = NoScrollDoubleSpinBox()
        self.receipt_spin.setRange(0.01, 99999.99)
        self.receipt_spin.setDecimals(2)
        self.receipt_spin.setPrefix("$")
        self.receipt_spin.setValue(txn['receipt_total'])
        self.receipt_spin.valueChanged.connect(self._update_customer_impact)
        form.addRow("Receipt Total:", self.receipt_spin)

        self.vendor_combo = NoScrollComboBox()
        vendors = get_all_vendors(active_only=True)
        for v in vendors:
            self.vendor_combo.addItem(v['name'], userData=v['id'])
        for i in range(self.vendor_combo.count()):
            if self.vendor_combo.itemData(i) == txn['vendor_id']:
                self.vendor_combo.setCurrentIndex(i)
                break
        form.addRow("Vendor:", self.vendor_combo)

        self.reason_combo = NoScrollComboBox()
        for display_label, code in REASON_CODES.items():
            self.reason_combo.addItem(display_label, userData=code)
        form.addRow("Reason:", self.reason_combo)

        self.notes_input = QTextEdit()
        self.notes_input.setMaximumHeight(80)
        self.notes_input.setPlaceholderText("Explain the reason for this adjustment...")
        form.addRow("Notes:", self.notes_input)

        self.adjusted_by_input = QLineEdit()
        self.adjusted_by_input.setPlaceholderText("Your name")
        form.addRow("Adjusted By:", self.adjusted_by_input)

        main_layout.addLayout(form)

        # ── Separator ─────────────────────────────────────────
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet(f"color: {LIGHT_GRAY};")
        main_layout.addWidget(separator)

        # ── Payment Methods section ───────────────────────────
        pay_header = QHBoxLayout()
        pay_title = QLabel("Payment Methods")
        pay_title.setStyleSheet(f"""
            font-size: 13px; font-weight: bold;
            color: {SUBTITLE_GRAY}; padding: 2px 0px;
        """)
        pay_header.addWidget(pay_title)
        pay_header.addStretch()
        self.add_method_btn = QPushButton("+ Add Payment Method")
        self.add_method_btn.setCursor(Qt.PointingHandCursor)
        self.add_method_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 4px 12px; min-height: 0px; font-size: 12px;
                border: 1px solid {LIGHT_GRAY}; border-radius: 6px;
                background-color: {WHITE}; color: {TEXT_COLOR};
            }}
            QPushButton:hover {{
                background-color: #F0EFEB; color: {PRIMARY_GREEN};
                border-color: {PRIMARY_GREEN};
            }}
        """)
        self.add_method_btn.clicked.connect(self._add_payment_row)
        pay_header.addWidget(self.add_method_btn)
        main_layout.addLayout(pay_header)

        # Payment rows container
        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(4)
        self.rows_layout.addStretch()
        main_layout.addWidget(self.rows_container)

        # Allocation error label
        self.payment_error_label = QLabel("")
        self.payment_error_label.setStyleSheet(
            f"color: {ERROR_COLOR}; font-weight: bold; font-size: 12px;"
        )
        self.payment_error_label.setVisible(False)
        main_layout.addWidget(self.payment_error_label)

        # ── Customer impact info panel ────────────────────────
        self.customer_impact_label = QLabel("")
        self.customer_impact_label.setWordWrap(True)
        self.customer_impact_label.setVisible(False)
        main_layout.addWidget(self.customer_impact_label)

        # ── Buttons ───────────────────────────────────────────
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)

        # ── Load existing payment line items ──────────────────
        existing_items = get_payment_line_items(txn['id'])
        self._original_items = existing_items
        self._original_customer_paid = sum(
            it['customer_charged'] for it in existing_items
        )

        if existing_items:
            for item in existing_items:
                row = self._add_payment_row()
                row.set_data(item['payment_method_id'], item['method_amount'])
        else:
            self._add_payment_row()

        self._update_customer_impact()

    # ── Payment row management ────────────────────────────────

    def _add_payment_row(self):
        """Add a new PaymentRow widget to the dialog."""
        row = PaymentRow(market_id=self._market_id)
        row.changed.connect(self._on_payment_changed)
        row.remove_requested.connect(self._remove_payment_row)
        self._payment_rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        self._refresh_method_choices()
        return row

    def _remove_payment_row(self, row):
        """Remove a PaymentRow from the dialog."""
        if len(self._payment_rows) <= 1:
            return
        self._payment_rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self._refresh_method_choices()
        self._on_payment_changed()

    def _on_payment_changed(self):
        """Called when any payment row value changes."""
        self._refresh_method_choices()
        self._update_customer_impact()

    def _refresh_method_choices(self):
        """Disable already-selected methods in other rows."""
        if self._market_id:
            methods = get_payment_methods_for_market(self._market_id, active_only=True)
            if not methods:
                methods = get_all_payment_methods(active_only=True)
        else:
            methods = get_all_payment_methods(active_only=True)

        selected_ids = set()
        for row in self._payment_rows:
            mid = row.get_selected_method_id()
            if mid is not None:
                selected_ids.add(mid)
        for row in self._payment_rows:
            row.set_excluded_methods(selected_ids)

        self.add_method_btn.setVisible(len(self._payment_rows) < len(methods))

    # ── Customer impact calculation ───────────────────────────

    def _update_customer_impact(self):
        """Update the customer impact info panel based on current payment data."""
        from fam.utils.calculations import calculate_payment_breakdown

        new_total = self.receipt_spin.value()

        # Collect valid payment data from rows
        calc_entries = []
        active_rows = []
        allocated = 0.0
        has_payments = False
        for row in self._payment_rows:
            data = row.get_data()
            if data and data['method_amount'] > 0:
                has_payments = True
                allocated += data['method_amount']
                calc_entries.append({
                    'method_amount': data['method_amount'],
                    'match_percent': data['match_percent'],
                })
                active_rows.append(row)

        if not has_payments:
            self.customer_impact_label.setVisible(False)
            self.payment_error_label.setVisible(False)
            return

        # Show allocation error if payment total doesn't match receipt total
        if abs(allocated - new_total) > 0.01:
            remaining = new_total - allocated
            self.payment_error_label.setText(
                f"Payment total (${allocated:.2f}) does not match "
                f"receipt total (${new_total:.2f}). "
                f"Remaining: ${remaining:.2f}"
            )
            self.payment_error_label.setVisible(True)
        else:
            self.payment_error_label.setVisible(False)

        # Use calculate_payment_breakdown with match limit for accurate totals
        result = calculate_payment_breakdown(
            new_total, calc_entries, match_limit=self._match_limit
        )
        new_customer_paid = result['customer_total_paid']
        new_fam_match = result['fam_subsidy_total']

        # Push capped values back to each PaymentRow's display labels
        for row, capped_li in zip(active_rows, result['line_items']):
            row.set_display_values(
                capped_li['match_amount'], capped_li['customer_charged']
            )

        # Customer impact comparison
        diff = round(new_customer_paid - self._original_customer_paid, 2)

        # Build match limit info string
        limit_note = ""
        if self._match_limit is not None:
            limit_note = f"  (Match limit: ${self._match_limit:.2f})"
        if result.get('match_was_capped'):
            limit_note = f"  (Match capped at ${self._match_limit:.2f})"

        if abs(diff) < 0.01:
            self.customer_impact_label.setText(
                f"No change to customer amount. "
                f"Customer pays ${new_customer_paid:.2f}, "
                f"FAM match ${new_fam_match:.2f}.{limit_note}"
            )
            self.customer_impact_label.setStyleSheet(f"""
                font-size: 13px; font-weight: bold;
                padding: 8px 12px; border-radius: 6px;
                background-color: {LIGHT_GRAY}; color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GRAY};
            """)
        elif diff > 0:
            self.customer_impact_label.setText(
                f"If the original payment was collected, "
                f"collect ${diff:.2f} more from customer. "
                f"(Was ${self._original_customer_paid:.2f}, "
                f"now ${new_customer_paid:.2f})  "
                f"FAM match: ${new_fam_match:.2f}{limit_note}"
            )
            self.customer_impact_label.setStyleSheet(f"""
                font-size: 13px; font-weight: bold;
                padding: 8px 12px; border-radius: 6px;
                background-color: {WARNING_BG}; color: {WARNING_COLOR};
                border: 1px solid {WARNING_COLOR};
            """)
        else:
            self.customer_impact_label.setText(
                f"If the original payment was collected, "
                f"refund ${abs(diff):.2f} to customer. "
                f"(Was ${self._original_customer_paid:.2f}, "
                f"now ${new_customer_paid:.2f})  "
                f"FAM match: ${new_fam_match:.2f}{limit_note}"
            )
            self.customer_impact_label.setStyleSheet(f"""
                font-size: 13px; font-weight: bold;
                padding: 8px 12px; border-radius: 6px;
                background-color: {SUCCESS_BG}; color: {ACCENT_GREEN};
                border: 1px solid {ACCENT_GREEN};
            """)
        self.customer_impact_label.setVisible(True)

    # ── Public getters for caller ─────────────────────────────

    def get_new_line_items(self):
        """Return list of payment line item dicts with match limit applied."""
        from fam.utils.calculations import calculate_payment_breakdown

        raw_items = []
        for row in self._payment_rows:
            data = row.get_data()
            if data and data['method_amount'] > 0:
                raw_items.append(data)

        if not raw_items:
            return []

        # Apply match limit cap via calculate_payment_breakdown
        new_total = self.receipt_spin.value()
        calc_entries = [
            {'method_amount': it['method_amount'], 'match_percent': it['match_percent']}
            for it in raw_items
        ]
        result = calculate_payment_breakdown(
            new_total, calc_entries, match_limit=self._match_limit
        )

        # Merge capped match_amount and customer_charged back into items
        for item, capped in zip(raw_items, result['line_items']):
            item['match_amount'] = capped['match_amount']
            item['customer_charged'] = capped['customer_charged']

        return raw_items

    def payments_changed(self):
        """Return True if the payment methods/amounts differ from the original."""
        new_items = self.get_new_line_items()
        if len(new_items) != len(self._original_items):
            return True
        for new_it, old_it in zip(
            sorted(new_items, key=lambda x: x['payment_method_id']),
            sorted(self._original_items, key=lambda x: x['payment_method_id'])
        ):
            if (new_it['payment_method_id'] != old_it['payment_method_id'] or
                    abs(new_it['method_amount'] - old_it['method_amount']) > 0.001):
                return True
        return False


class AdminScreen(QWidget):
    """Admin Adjustments screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        title = QLabel("Adjustments & Corrections")
        title.setObjectName("screen_title")
        layout.addWidget(title)

        # Filter bar
        filter_frame = QFrame()
        self.filter_frame = filter_frame  # expose for tutorial hints
        filter_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {WHITE};
                border: 1px solid #E2E2E2;
                border-radius: 8px;
                padding: 6px 10px;
            }}
        """)
        filter_layout = QHBoxLayout(filter_frame)

        filter_layout.addWidget(make_field_label("Market"))
        self.md_filter = NoScrollComboBox()
        self.md_filter.setMinimumWidth(200)
        filter_layout.addWidget(self.md_filter)

        filter_layout.addWidget(make_field_label("Status"))
        self.status_filter = NoScrollComboBox()
        self.status_filter.addItems(["All", "Draft", "Confirmed", "Adjusted", "Voided"])
        filter_layout.addWidget(self.status_filter)

        filter_layout.addWidget(make_field_label("Transaction ID"))
        self.id_search = QLineEdit()
        self.id_search.setPlaceholderText("Search FAM-...")
        self.id_search.setMaximumWidth(180)
        filter_layout.addWidget(self.id_search)

        search_btn = QPushButton("Search")
        search_btn.setObjectName("primary_btn")
        search_btn.setStyleSheet("padding: 4px 16px; min-height: 0px;")
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
        audit_label = QLabel("Recent Audit Log")
        audit_label.setStyleSheet(f"""
            font-size: 12px;
            font-weight: bold;
            color: {SUBTITLE_GRAY};
            padding: 2px 0px;
        """)
        layout.addWidget(audit_label)
        self.audit_table = QTableWidget()
        self.audit_table.setColumnCount(8)
        self.audit_table.setHorizontalHeaderLabels(
            ["Time", "Table", "Record ID", "Action", "Changed By",
             "Field", "Old Value", "New Value"]
        )
        configure_table(self.audit_table)
        self.audit_table.setMaximumHeight(220)
        layout.addWidget(self.audit_table)

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
            self.table.setRowHeight(i, 42)

        self.table.setSortingEnabled(True)

    def _adjust_transaction(self, txn_id):
        txn = get_transaction_by_id(txn_id)
        if not txn:
            return

        if txn['status'] == 'Voided':
            QMessageBox.warning(self, "Cannot Adjust",
                                "Voided transactions cannot be adjusted.")
            return

        dialog = AdjustmentDialog(txn, self)

        # Pre-fill "Adjusted By" with the open market day's volunteer
        open_md = get_open_market_day()
        if open_md and open_md.get('opened_by'):
            dialog.adjusted_by_input.setText(open_md['opened_by'])

        if dialog.exec() == QDialog.Accepted:
            adjusted_by = dialog.adjusted_by_input.text().strip() or "Admin"
            reason = dialog.reason_combo.currentData() or dialog.reason_combo.currentText()
            notes = dialog.notes_input.toPlainText().strip()
            new_total = dialog.receipt_spin.value()
            new_vendor = dialog.vendor_combo.currentData()

            if new_total <= 0:
                QMessageBox.warning(self, "Error",
                                    "Receipt total must be greater than $0.00.")
                return

            # Validate payment allocation if payments were edited
            new_items = dialog.get_new_line_items()
            if new_items:
                allocated = sum(it['method_amount'] for it in new_items)
                if abs(allocated - new_total) > 0.01:
                    QMessageBox.warning(
                        self, "Payment Mismatch",
                        f"Payment total (${allocated:.2f}) does not match "
                        f"receipt total (${new_total:.2f}). "
                        f"Please fix the payment amounts."
                    )
                    return

            # ── Atomic adjustment: all DB writes in one transaction ──

            conn = get_connection()
            try:
                anything_changed = False

                if new_total != txn['receipt_total']:
                    log_action('transactions', txn_id, 'ADJUST', adjusted_by,
                                field_name='receipt_total',
                                old_value=txn['receipt_total'],
                                new_value=new_total,
                                reason_code=reason, notes=notes, commit=False)
                    update_transaction(txn_id, receipt_total=new_total, commit=False)
                    anything_changed = True

                if new_vendor != txn['vendor_id']:
                    log_action('transactions', txn_id, 'ADJUST', adjusted_by,
                                field_name='vendor_id',
                                old_value=txn['vendor_id'],
                                new_value=new_vendor,
                                reason_code=reason, notes=notes, commit=False)
                    update_transaction(txn_id, vendor_id=new_vendor, commit=False)
                    anything_changed = True

                # Save payment line items if changed
                old_items = dialog._original_items
                payments_did_change = dialog.payments_changed()
                if payments_did_change and new_items:
                    old_summary = ", ".join(
                        f"{it['method_name_snapshot']}=${it['method_amount']:.2f}"
                        for it in old_items
                    )
                    new_summary = ", ".join(
                        f"{it['method_name_snapshot']}=${it['method_amount']:.2f}"
                        for it in new_items
                    )
                    log_action('payment_line_items', txn_id,
                               'PAYMENT_ADJUSTED', adjusted_by,
                               field_name='payment_methods',
                               old_value=old_summary,
                               new_value=new_summary,
                               reason_code=reason, notes=notes, commit=False)
                    save_payment_line_items(txn_id, new_items, commit=False)
                    anything_changed = True

                # Only mark as Adjusted if something actually changed
                if anything_changed:
                    update_transaction(txn_id, status='Adjusted', commit=False)

                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.exception("Failed to adjust transaction %s", txn_id)
                QMessageBox.critical(self, "Error", f"Adjustment failed: {e}")
                return

            write_ledger_backup()
            self._search()
            self._load_audit_log()

            # Show customer impact message after save
            if payments_did_change and new_items:
                old_customer = sum(
                    it['customer_charged'] for it in old_items
                )
                new_customer = sum(
                    it['customer_charged'] for it in new_items
                )
                diff = round(new_customer - old_customer, 2)
                if abs(diff) >= 0.01:
                    if diff > 0:
                        impact_msg = (
                            f"Adjustment saved.\n\n"
                            f"If the original payment was collected, "
                            f"collect ${diff:.2f} more from the customer.\n"
                            f"(Was ${old_customer:.2f}, "
                            f"now ${new_customer:.2f})"
                        )
                    else:
                        impact_msg = (
                            f"Adjustment saved.\n\n"
                            f"If the original payment was collected, "
                            f"refund ${abs(diff):.2f} to the customer.\n"
                            f"(Was ${old_customer:.2f}, "
                            f"now ${new_customer:.2f})"
                        )
                    QMessageBox.information(
                        self, "Customer Impact", impact_msg
                    )

    def _void_transaction(self, txn_id):
        txn = get_transaction_by_id(txn_id)
        if not txn:
            return

        if txn['status'] == 'Voided':
            QMessageBox.warning(self, "Already Voided",
                                "This transaction has already been voided.")
            return

        result = QMessageBox.warning(
            self, "Void Transaction",
            f"Are you sure you want to void transaction {txn['fam_transaction_id']}?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )
        if result == QMessageBox.Yes:

            conn = get_connection()
            try:
                # Use the open market day volunteer's name as changed_by
                open_md = get_open_market_day()
                changed_by = (open_md.get('opened_by') if open_md else None) or 'Admin'
                log_action('transactions', txn_id, 'VOID', changed_by,
                            reason_code='admin_adjustment',
                            notes='Transaction voided', commit=False)
                update_transaction(txn_id, status='Voided', commit=False)
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.exception("Failed to void transaction %s", txn_id)
                QMessageBox.critical(self, "Error", f"Void failed: {e}")
                return
            write_ledger_backup()
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
            self.audit_table.setRowHeight(i, 30)
        self.audit_table.setSortingEnabled(True)
