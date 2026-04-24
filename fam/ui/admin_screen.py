"""Screen E: Admin Adjustments."""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QTextEdit, QDialog, QDialogButtonBox,
    QFormLayout
)
from PySide6.QtCore import Qt, Signal

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
from fam.utils.money import dollars_to_cents, cents_to_dollars, format_dollars

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
                self._match_limit = market['daily_match_limit'] or 10000
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
        self.receipt_spin.setValue(cents_to_dollars(txn['receipt_total']))
        self._last_receipt_total = cents_to_dollars(txn['receipt_total'])  # track for proportional rescale
        self.receipt_spin.valueChanged.connect(self._on_receipt_total_changed)
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

        # ── Auto-Distribute button (parity with payment screen) ──
        # Mirrors PaymentScreen's ⚡ Auto-Distribute so a volunteer can
        # change the receipt total and one-click redistribute across
        # payment methods rather than hand-balancing each row.
        self.auto_distribute_btn = QPushButton("⚡ Auto-Distribute")
        self.auto_distribute_btn.setCursor(Qt.PointingHandCursor)
        self.auto_distribute_btn.setToolTip(
            "Reset non-denominated rows and redistribute the receipt total "
            "across payment methods."
        )
        self.auto_distribute_btn.setStyleSheet(f"""
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
        self.auto_distribute_btn.clicked.connect(self._auto_distribute)
        pay_header.addWidget(self.auto_distribute_btn)
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
        )  # integer cents from DB

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
        self._update_row_caps()
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

    def _update_row_caps(self):
        """Cap every payment row's charge input at the current receipt total.

        Mirrors the payment-screen behavior that prevented volunteers from
        entering a single-row charge that exceeds the receipt.  Using the
        receipt total as the cap (rather than 'receipt total - other rows')
        keeps the UX simple: any row can independently absorb the entire
        receipt, but none can exceed it.  Cross-row validation is handled
        at save time by ``calculate_payment_breakdown``.
        """
        receipt_cents = dollars_to_cents(self.receipt_spin.value())
        for row in self._payment_rows:
            row.set_max_charge(receipt_cents)

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

    # ── Receipt total change → proportional rescale ──────────

    def _on_receipt_total_changed(self, new_total):
        """When receipt total changes, proportionally rescale all payment amounts.

        The spinbox emits dollar values; we convert to integer cents for
        proportional math and convert back to dollars before writing to
        the spinboxes.
        """
        old_total_cents = dollars_to_cents(self._last_receipt_total)
        new_total_cents = dollars_to_cents(new_total)
        if old_total_cents > 0 and new_total_cents != old_total_cents:
            # Collect current amounts in cents
            amounts_cents = [
                dollars_to_cents(row.amount_spin.value())
                for row in self._payment_rows
            ]
            current_sum_cents = sum(amounts_cents)
            # Only rescale if payments previously matched the old total (within 1 cent)
            if current_sum_cents > 0 and abs(current_sum_cents - old_total_cents) <= 1:
                rescaled = [
                    round(a * new_total_cents / current_sum_cents)
                    for a in amounts_cents
                ]
                # Fix rounding drift — adjust the largest row
                drift = new_total_cents - sum(rescaled)
                if drift != 0 and rescaled:
                    idx = rescaled.index(max(rescaled))
                    rescaled[idx] += drift
                # Apply new amounts (block signals to avoid recursion)
                for row, amt_cents in zip(self._payment_rows, rescaled):
                    row.amount_spin.blockSignals(True)
                    row.amount_spin.setValue(cents_to_dollars(amt_cents))
                    row.amount_spin.blockSignals(False)
        self._last_receipt_total = new_total
        # Re-cap every row against the NEW receipt total so the per-row
        # maximum tracks the target.  Without this, changing the receipt
        # would let a row exceed the new total until the user tabbed out.
        self._update_row_caps()
        self._update_customer_impact()

    # ── Auto-Distribute (parity with payment screen) ─────────────

    def _auto_distribute(self):
        """One-click redistribution of the receipt total across payment rows.

        Volunteer flow: change the receipt total, click this button, rows
        auto-fill based on each method's match percent and denomination.
        Denominated rows with an existing charge are treated as locked
        (they represent physical checks/tokens the customer handed over);
        non-denominated rows are reset and refilled as absorbers.

        Simpler than PaymentScreen's equivalent because the adjustment
        flow does not need the "add overflow row" escape hatch — if the
        user has no non-denominated row and can't absorb the remainder,
        the existing save-time validator surfaces the mismatch so they
        can add a row manually.
        """
        from fam.utils.calculations import (
            smart_auto_distribute, charge_to_method_amount,
        )

        receipt_cents = dollars_to_cents(self.receipt_spin.value())
        if receipt_cents <= 0 or not self._payment_rows:
            return

        # Build row descriptors for the algorithm.  Non-denominated rows
        # are always reset to 0 (absorbers).  Denominated rows with a
        # charge stay locked.
        row_descriptors = []
        for i, row in enumerate(self._payment_rows):
            method = row.get_selected_method()
            if not method:
                continue
            is_denom = (
                method.get('denomination') and method['denomination'] > 0
            )
            charge = row._get_active_charge()
            if not is_denom and charge > 0:
                charge = 0   # absorber — let distributor refill
            row_descriptors.append({
                'index': i,
                'match_pct': method['match_percent'],
                'denomination': method.get('denomination'),
                'sort_order': method.get('sort_order', 0),
                'current_charge': charge,
            })

        if not row_descriptors:
            return

        assignments = smart_auto_distribute(receipt_cents, row_descriptors)

        # Apply non-denominated reset first so the UI reflects the new
        # state even for rows the distributor chose not to fill.
        for desc in row_descriptors:
            is_denom = (
                desc.get('denomination') and desc['denomination'] > 0
            )
            if not is_denom and desc['current_charge'] == 0:
                # Temporarily suppress change notifications while we
                # bulk-update so we don't trigger N cascading recalcs.
                row = self._payment_rows[desc['index']]
                row.blockSignals(True)
                row._set_active_charge(0)
                row.blockSignals(False)

        # Apply assignments from the distributor
        for a in assignments:
            row = self._payment_rows[a['index']]
            row.blockSignals(True)
            row._set_active_charge(a['charge'])
            row.blockSignals(False)

        # Re-cap and recompute the impact panel after all rows settle.
        self._update_row_caps()
        self._on_payment_changed()

    # ── Customer impact calculation ───────────────────────────

    def _update_customer_impact(self):
        """Update the customer impact info panel based on current payment data.

        All internal monetary values are integer cents.  The receipt spinbox
        is in dollars and is converted at the boundary.
        """
        from fam.utils.calculations import calculate_payment_breakdown

        new_total_cents = dollars_to_cents(self.receipt_spin.value())

        # Collect valid payment data from rows (all values already in cents)
        calc_entries = []
        active_rows = []
        allocated = 0  # integer cents
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
        if abs(allocated - new_total_cents) > 1:
            self.payment_error_label.setText(
                f"Payment total ({format_dollars(allocated)}) does not match "
                f"receipt total ({format_dollars(new_total_cents)}). "
                f"Remaining: {format_dollars(new_total_cents - allocated)}"
            )
            self.payment_error_label.setVisible(True)
        else:
            self.payment_error_label.setVisible(False)

        # Use calculate_payment_breakdown with match limit for accurate totals
        result = calculate_payment_breakdown(
            new_total_cents, calc_entries, match_limit=self._match_limit
        )
        new_customer_paid = result['customer_total_paid']  # cents
        new_fam_match = result['fam_subsidy_total']  # cents

        # Push capped values back to each PaymentRow's display labels (cents)
        for row, capped_li in zip(active_rows, result['line_items']):
            row.set_display_values(
                capped_li['match_amount'], capped_li['customer_charged']
            )

        # Customer impact comparison (all cents)
        diff = new_customer_paid - self._original_customer_paid

        # Build match limit info string
        limit_note = ""
        if self._match_limit is not None:
            limit_note = f"  (Match limit: {format_dollars(self._match_limit)})"
        if result.get('match_was_capped'):
            limit_note = f"  (Match capped at {format_dollars(self._match_limit)})"

        if abs(diff) < 1:
            self.customer_impact_label.setText(
                f"No change to customer amount. "
                f"Customer pays {format_dollars(new_customer_paid)}, "
                f"FAM match {format_dollars(new_fam_match)}.{limit_note}"
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
                f"collect {format_dollars(diff)} more from customer. "
                f"(Was {format_dollars(self._original_customer_paid)}, "
                f"now {format_dollars(new_customer_paid)})  "
                f"FAM match: {format_dollars(new_fam_match)}{limit_note}"
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
                f"refund {format_dollars(abs(diff))} to customer. "
                f"(Was {format_dollars(self._original_customer_paid)}, "
                f"now {format_dollars(new_customer_paid)})  "
                f"FAM match: {format_dollars(new_fam_match)}{limit_note}"
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
        """Return list of payment line item dicts with match limit applied.

        All monetary values are integer cents.
        """
        from fam.utils.calculations import calculate_payment_breakdown

        raw_items = []
        for row in self._payment_rows:
            data = row.get_data()
            if data and data['method_amount'] > 0:
                raw_items.append(data)

        if not raw_items:
            return []

        # Apply match limit cap via calculate_payment_breakdown (all cents)
        new_total_cents = dollars_to_cents(self.receipt_spin.value())
        calc_entries = [
            {'method_amount': it['method_amount'], 'match_percent': it['match_percent']}
            for it in raw_items
        ]
        result = calculate_payment_breakdown(
            new_total_cents, calc_entries, match_limit=self._match_limit
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
                    abs(new_it['method_amount'] - old_it['method_amount']) > 0):
                return True
        return False


class AdminScreen(QWidget):
    """Admin Adjustments screen."""

    # Fired on any CUD operation this screen performs (adjustment save,
    # transaction void) so the main window can trigger a cloud sync.
    data_changed = Signal()

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
        configure_table(self.table, actions_col=7, actions_width=170)
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
            self.table.setItem(i, 4, make_item(format_dollars(t['receipt_total']), t['receipt_total']))
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

            if t['status'] != 'Draft':
                print_btn = make_action_btn("Print", 44)
                print_btn.clicked.connect(lambda checked, tid=txn_id: self._print_receipt(tid))
                action_layout.addWidget(print_btn)

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
            new_total_dollars = dialog.receipt_spin.value()
            new_total_cents = dollars_to_cents(new_total_dollars)
            new_vendor = dialog.vendor_combo.currentData()

            if new_total_cents <= 0:
                QMessageBox.warning(self, "Error",
                                    "Receipt total must be greater than $0.00.")
                return

            # Warn if adjusted total exceeds the configurable threshold
            # (threshold is in dollars from app_settings)
            from fam.utils.app_settings import get_large_receipt_threshold
            threshold = get_large_receipt_threshold()
            if new_total_dollars > threshold:
                answer = QMessageBox.warning(
                    self, "Large Receipt",
                    f"Adjusted receipt total ${new_total_dollars:,.2f} exceeds the "
                    f"warning threshold of ${threshold:,.2f}.\n\n"
                    f"Are you sure this amount is correct?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return

            # Validate payment allocation if payments were edited (all cents)
            new_items = dialog.get_new_line_items()
            if new_items:
                allocated = sum(it['method_amount'] for it in new_items)
                if abs(allocated - new_total_cents) > 1:
                    QMessageBox.warning(
                        self, "Payment Mismatch",
                        f"Payment total ({format_dollars(allocated)}) does not match "
                        f"receipt total ({format_dollars(new_total_cents)}). "
                        f"Please fix the payment amounts."
                    )
                    return

            # ── Atomic adjustment: all DB writes in one transaction ──

            conn = get_connection()
            try:
                anything_changed = False

                if new_total_cents != txn['receipt_total']:
                    log_action('transactions', txn_id, 'ADJUST', adjusted_by,
                                field_name='receipt_total',
                                old_value=txn['receipt_total'],
                                new_value=new_total_cents,
                                reason_code=reason, notes=notes, commit=False)
                    update_transaction(txn_id, receipt_total=new_total_cents, commit=False)
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
                        f"{it['method_name_snapshot']}={format_dollars(it['method_amount'])}"
                        for it in old_items
                    )
                    new_summary = ", ".join(
                        f"{it['method_name_snapshot']}={format_dollars(it['method_amount'])}"
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

            # Notify listeners (main window wires this to cloud sync) that
            # transaction/payment data changed.  Fires on any successful
            # adjustment, even if only the vendor or notes changed.
            if anything_changed:
                self.data_changed.emit()

            # Show customer impact message after save
            if payments_did_change and new_items:
                old_customer = sum(
                    it['customer_charged'] for it in old_items
                )  # cents
                new_customer = sum(
                    it['customer_charged'] for it in new_items
                )  # cents
                diff = new_customer - old_customer
                if abs(diff) >= 1:
                    if diff > 0:
                        impact_msg = (
                            f"Adjustment saved.\n\n"
                            f"If the original payment was collected, "
                            f"collect {format_dollars(diff)} more from the customer.\n"
                            f"(Was {format_dollars(old_customer)}, "
                            f"now {format_dollars(new_customer)})"
                        )
                    else:
                        impact_msg = (
                            f"Adjustment saved.\n\n"
                            f"If the original payment was collected, "
                            f"refund {format_dollars(abs(diff))} to the customer.\n"
                            f"(Was {format_dollars(old_customer)}, "
                            f"now {format_dollars(new_customer)})"
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
            # Void is a data mutation — signal the main window to sync.
            self.data_changed.emit()

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

    # ------------------------------------------------------------------
    # Receipt printing
    # ------------------------------------------------------------------

    def _build_receipt_data_for_transaction(self, txn_id: int) -> dict | None:
        """Build receipt data dict for a transaction (and its sibling order transactions)."""
        try:
            from fam.models.customer_order import get_customer_order, get_order_transactions

            txn = get_transaction_by_id(txn_id)
            if not txn:
                return None

            market_name = txn.get('market_name', '')
            market_date = txn.get('market_day_date', '')
            confirmed_by = txn.get('confirmed_by', '') or 'Volunteer'
            status = txn.get('status', 'Confirmed')
            customer_label = ''

            # Gather all transactions belonging to the same customer order
            order_txns = []
            order_id = txn.get('customer_order_id')
            if order_id:
                order = get_customer_order(order_id)
                if order:
                    customer_label = order.get('customer_label', '')
                # Include all non-voided sibling transactions
                order_txns = get_order_transactions(order_id)
            if not order_txns:
                # Standalone transaction (no order) — just use this one
                order_txns = [txn]

            txns = []
            total_receipt = 0  # cents
            total_customer = 0  # cents
            total_match = 0  # cents
            payment_totals: dict[str, dict] = {}

            for t in order_txns:
                tid = t['id']
                full_txn = get_transaction_by_id(tid)
                receipt = full_txn['receipt_total']  # integer cents
                total_receipt += receipt
                line_items = get_payment_line_items(tid)

                txns.append({
                    'fam_id': full_txn['fam_transaction_id'],
                    'vendor': full_txn.get('vendor_name', ''),
                    'receipt_total': cents_to_dollars(receipt),
                })

                for li in line_items:
                    method = li['method_name_snapshot']
                    amt = li['method_amount']  # cents
                    match = li['match_amount']  # cents
                    cust = li['customer_charged']  # cents
                    total_customer += cust
                    total_match += match
                    if method not in payment_totals:
                        payment_totals[method] = {
                            'amount': 0, 'match': 0, 'customer': 0,
                        }
                    payment_totals[method]['amount'] += amt
                    payment_totals[method]['match'] += match
                    payment_totals[method]['customer'] += cust

            # Convert accumulated cents to dollars at the UI boundary
            payment_totals_dollars = {
                m: {
                    'amount': cents_to_dollars(v['amount']),
                    'match': cents_to_dollars(v['match']),
                    'customer': cents_to_dollars(v['customer']),
                }
                for m, v in payment_totals.items()
            }

            return {
                'market_name': market_name,
                'market_date': market_date,
                'customer_label': customer_label,
                'confirmed_by': confirmed_by,
                'status': status,
                'transactions': txns,
                'payment_totals': payment_totals_dollars,
                'total_receipt': cents_to_dollars(total_receipt),
                'total_customer': cents_to_dollars(total_customer),
                'total_match': cents_to_dollars(total_match),
            }
        except Exception:
            logger.exception("Failed to build receipt data for txn %s", txn_id)
            return None

    def _print_receipt(self, txn_id: int):
        """Open a print dialog with a formatted customer receipt for a transaction."""
        data = self._build_receipt_data_for_transaction(txn_id)
        if not data:
            QMessageBox.information(self, "Print Receipt",
                                    "No receipt data available for this transaction.")
            return

        from fam.ui.payment_screen import PaymentScreen
        html = PaymentScreen._format_receipt_html(data)

        from PySide6.QtPrintSupport import QPrinter, QPrintDialog
        from PySide6.QtGui import QTextDocument

        printer = QPrinter(QPrinter.HighResolution)
        printer.setDocName("FAM_Receipt")

        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("Print Customer Receipt")
        if dlg.exec() == QPrintDialog.Accepted:
            doc = QTextDocument()
            doc.setHtml(html)
            doc.print_(printer)
            logger.info("Receipt printed from Adjustments for txn %s", txn_id)
