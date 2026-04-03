"""Screen C: Payment Processing — supports multi-receipt customer orders."""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QMessageBox, QTableWidget
)
from PySide6.QtCore import Signal, Qt

from fam.database.connection import get_connection
from fam.models.transaction import (
    get_transaction_by_id, confirm_transaction, save_payment_line_items,
    get_payment_line_items, update_transaction
)
from fam.utils.export import write_ledger_backup
from fam.models.customer_order import (
    get_customer_order, get_order_transactions, get_order_total,
    get_order_vendor_summary, update_customer_order_status,
    get_customer_prior_match
)
from fam.models.market_day import get_open_market_day

logger = logging.getLogger('fam.ui.payment_screen')
from fam.utils.calculations import calculate_payment_breakdown, charge_to_method_amount
from fam.utils.money import cents_to_dollars, format_dollars, format_dollars_comma
from fam.ui.widgets.payment_row import PaymentRow
from fam.ui.widgets.summary_card import SummaryRow
from fam.ui.styles import (
    PRIMARY_GREEN, WHITE, LIGHT_GRAY, HARVEST_GOLD, ERROR_COLOR, ACCENT_GREEN,
    BACKGROUND, FIELD_LABEL_BG, MEDIUM_GRAY, SUBTITLE_GRAY, SUCCESS_BG,
    ERROR_BG, WARNING_BG, WARNING_COLOR
)
from fam.ui.helpers import make_field_label, make_section_label, make_item, configure_table


class PaymentScreen(QWidget):
    """Payment Processing screen — handles customer orders with multiple receipts."""

    payment_confirmed = Signal()
    draft_saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_order_id = None
        self._order_transactions = []
        self._order_total = 0       # integer cents
        self._match_limit = None    # None = no cap, int = remaining cap in cents
        self._daily_limit = None    # Full daily limit from market settings (cents)
        self._prior_match = 0       # FAM match already used by this customer (cents)
        self._market_id = None      # Market ID for filtering payment methods
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
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        # Title
        title = QLabel("Payment Processing")
        title.setObjectName("screen_title")
        layout.addWidget(title)

        # ── Combined info + summary row ──────────────────────────────
        self.summary_row = SummaryRow()

        # Customer info card (first in the row)
        order_card = self.summary_row.add_card("order_info", "Customer / Order")
        self.customer_id_label = order_card.title_label
        self.customer_id_label.setText("No order loaded")
        # Override title style: no uppercase (customer names should be natural case)
        self.customer_id_label.setStyleSheet(
            f"font-size: 11px; color: {SUBTITLE_GRAY}; font-weight: bold;"
        )
        self.order_total_label = order_card.value_label
        self.order_total_label.setText("$0.00")
        order_card.set_value_color(HARVEST_GOLD)
        self.order_receipts_label = QLabel("")  # kept for API compat

        self.summary_row.add_card("allocated", "Total Allocated")
        self.summary_row.add_card("remaining", "Remaining", highlight=True)
        self.summary_row.add_card("customer_pays", "Customer Pays")
        self.summary_row.add_card("fam_match", "FAM Match", highlight=True)

        # Initial color setup
        self.summary_row.update_card_color("fam_match", PRIMARY_GREEN)

        layout.addWidget(self.summary_row)

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

        # Denomination overage warning (shown when denominated payments overshoot)
        self.denom_overage_warning = QLabel("")
        self.denom_overage_warning.setStyleSheet(f"""
            font-size: 12px; font-weight: bold; color: {HARVEST_GOLD};
            background-color: {WARNING_BG};
            border: 1px solid {WARNING_COLOR};
            border-radius: 6px;
            padding: 6px 10px;
        """)
        self.denom_overage_warning.setWordWrap(True)
        self.denom_overage_warning.setVisible(False)
        layout.addWidget(self.denom_overage_warning)

        # ── Vendor summary table ────────────────────────────────────
        self.vendor_lbl = make_section_label("Vendor Breakdown")
        self.vendor_lbl.setVisible(False)
        layout.addWidget(self.vendor_lbl)

        self.vendor_table = QTableWidget()
        self.vendor_table.setColumnCount(2)
        self.vendor_table.setHorizontalHeaderLabels(["Vendor", "Receipt Total"])
        configure_table(self.vendor_table)
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

        self.add_method_btn = QPushButton("+ Add Payment Method")
        self.add_method_btn.setObjectName("secondary_btn")
        self.add_method_btn.clicked.connect(self._add_payment_row)
        payment_header.addWidget(self.add_method_btn)

        self.auto_distribute_btn = QPushButton("⚡ Auto-Distribute")
        self.auto_distribute_btn.setObjectName("secondary_btn")
        self.auto_distribute_btn.setToolTip(
            "Automatically fill the remaining balance into the last payment method"
        )
        self.auto_distribute_btn.clicked.connect(self._auto_distribute)
        payment_header.addWidget(self.auto_distribute_btn)

        payment_header.addStretch()

        pay_lbl = make_section_label("Payment Methods")
        payment_header.addWidget(pay_lbl)

        layout.addLayout(payment_header)

        # Scrollable payment rows
        self.rows_container = QWidget()
        self.rows_container.setStyleSheet(f"background-color: {BACKGROUND};")
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(4, 4, 4, 4)
        self.rows_layout.setSpacing(4)
        self.rows_layout.addStretch()

        pay_scroll = QScrollArea()
        pay_scroll.setWidgetResizable(True)
        pay_scroll.setWidget(self.rows_container)
        pay_scroll.setMinimumHeight(150)
        # No maximum height — the area grows dynamically with the window.
        pay_scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: {BACKGROUND};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
            }}
        """)
        layout.addWidget(pay_scroll, 1)  # stretch=1 so it fills available space

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
        self.bottom_frame = bottom_frame  # expose for tutorial hints
        bottom_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {WHITE};
                border: 1px solid #E2E2E2;
                border-radius: 8px;
                padding: 8px 12px;
            }}
        """)
        bottom_layout = QHBoxLayout(bottom_frame)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(12)

        # Left side: collection checklist
        collect_side = QVBoxLayout()
        collect_side.setSpacing(2)
        collect_header = QLabel("Collect from Customer:")
        collect_header.setStyleSheet("font-weight: bold; font-size: 12px;")
        collect_side.addWidget(collect_header)

        self.collect_list_layout = QVBoxLayout()
        self.collect_list_layout.setSpacing(2)
        collect_side.addLayout(self.collect_list_layout)

        self.collect_total_label = QLabel("Total: $0.00")
        self.collect_total_label.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: {HARVEST_GOLD}; padding-top: 2px;"
        )
        collect_side.addWidget(self.collect_total_label)
        collect_side.addStretch()

        bottom_layout.addLayout(collect_side, 1)

        # Right side: action buttons (vertically stacked)
        btn_side = QVBoxLayout()
        btn_side.setSpacing(4)
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

        # Success message + Print Receipt button
        self.success_frame = QFrame()
        self.success_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {SUCCESS_BG};
                border: 1px solid {ACCENT_GREEN};
                border-radius: 8px;
                padding: 8px 12px;
            }}
        """)
        self.success_frame.setVisible(False)
        success_layout = QHBoxLayout(self.success_frame)
        self.success_msg = QLabel("")
        self.success_msg.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {PRIMARY_GREEN};"
        )
        success_layout.addWidget(self.success_msg, 1)

        self._print_receipt_btn = QPushButton("\U0001F5A8  Print Receipt")
        self._print_receipt_btn.setCursor(Qt.PointingHandCursor)
        self._print_receipt_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 6px 16px; font-size: 13px; min-height: 0px;
                border: 1px solid {ACCENT_GREEN}; border-radius: 6px;
                background-color: {WHITE}; color: {PRIMARY_GREEN};
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #F0EFEB;
                border-color: {PRIMARY_GREEN};
            }}
        """)
        self._print_receipt_btn.clicked.connect(self._print_receipt)
        success_layout.addWidget(self._print_receipt_btn)

        self._last_receipt_data = None
        layout.addWidget(self.success_frame)

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
        self.denom_overage_warning.setVisible(False)

        order = get_customer_order(order_id)
        if not order:
            self.customer_id_label.setText("Order not found")
            return

        self._market_id = order.get('market_id')

        # Determine match limit from market settings, accounting for prior usage
        if order.get('match_limit_active'):
            self._daily_limit = order.get('daily_match_limit') or 10000  # cents

            # Check for prior FAM match usage by this customer today
            self._prior_match = get_customer_prior_match(
                order['customer_label'],
                order['market_day_id'],
                exclude_order_id=order_id
            )
            remaining_limit = max(self._daily_limit - self._prior_match, 0)
            self._match_limit = remaining_limit

            if self._prior_match > 0:
                self.match_limit_label.setText(
                    f"Daily match limit: {format_dollars(self._daily_limit)} per customer  \u2502  "
                    f"Previously redeemed: {format_dollars(self._prior_match)}  \u2502  "
                    f"Remaining: {format_dollars(remaining_limit)}"
                )
            else:
                self.match_limit_label.setText(
                    f"Daily match limit: {format_dollars(self._daily_limit)} per customer"
                )
            self.match_limit_label.setVisible(True)
        else:
            self._match_limit = None
            self._daily_limit = None
            self._prior_match = 0
            self.match_limit_label.setText("Match limit: Off")
            self.match_limit_label.setVisible(True)

        self._order_transactions = get_order_transactions(order_id)
        self._order_total = sum(t['receipt_total'] for t in self._order_transactions)

        n_receipts = len(self._order_transactions)
        self.customer_id_label.setText(
            f"Customer {order['customer_label']}  —  {order['market_name']}  |  {n_receipts} receipt(s)"
        )
        self.order_receipts_label.setText(f"{n_receipts} receipt(s)")
        self.order_total_label.setText(format_dollars(self._order_total))

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
            self._market_id = txn.get('market_id')
            self._match_limit = None
            self._daily_limit = None
            self._prior_match = 0
            self.match_limit_label.setVisible(False)
            self.match_cap_warning.setVisible(False)
            self._order_transactions = [txn]
            self._order_total = txn['receipt_total']  # already integer cents

            self.customer_id_label.setText(
                f"{txn['fam_transaction_id']}  —  {txn['vendor_name']}"
            )
            self.order_receipts_label.setText(f"Vendor: {txn['vendor_name']}")
            self.order_total_label.setText(format_dollars(txn['receipt_total']))

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
            vendor_totals[name] = vendor_totals.get(name, 0) + t['receipt_total']

        rows = sorted(vendor_totals.items())
        self.vendor_table.setSortingEnabled(False)
        self.vendor_table.setRowCount(len(rows))
        for i, (vname, vtotal) in enumerate(rows):
            self.vendor_table.setItem(i, 0, make_item(vname))
            self.vendor_table.setItem(i, 1, make_item(format_dollars(vtotal), vtotal))
            self.vendor_table.setRowHeight(i, 30)
        self.vendor_table.setSortingEnabled(True)

        # Auto-fit table height: header + rows + border (no wasted space)
        header_h = self.vendor_table.horizontalHeader().height()
        rows_h = sum(self.vendor_table.rowHeight(i) for i in range(len(rows)))
        self.vendor_table.setFixedHeight(header_h + rows_h + 4)

        has_vendors = len(rows) > 0
        self.vendor_lbl.setVisible(has_vendors)
        self.vendor_table.setVisible(has_vendors)

    # ------------------------------------------------------------------
    # Payment rows
    # ------------------------------------------------------------------
    def _add_payment_row(self):
        row = PaymentRow(market_id=self._market_id)
        row.changed.connect(self._on_row_changed)
        row.remove_requested.connect(self._remove_payment_row)
        self._payment_rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        self._refresh_method_choices()
        return row

    def _remove_payment_row(self, row):
        if len(self._payment_rows) <= 1:
            return
        self._payment_rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self._refresh_method_choices()
        self._update_summary()

    def _on_row_changed(self):
        """Called when any payment row's method or amount changes."""
        self._refresh_method_choices()
        self._update_summary()

    def _auto_distribute(self):
        """Smart auto-distribute: seed 1 unit per denominated method, then fill up.

        Denominated rows keep their manual charge (locked) — they represent
        physical tokens/checks the customer handed over.  Non-denominated
        rows are reset to zero so they act as absorbers for the remaining
        balance.  If no non-denominated row exists and there is remaining
        balance, an overflow row is added automatically (SNAP preferred,
        then Cash, then any non-denominated method).
        """
        if not self._order_total or self._order_total <= 0:
            return

        from fam.utils.calculations import (
            smart_auto_distribute, charge_to_method_amount,
        )

        # Build row descriptors for the algorithm.
        # Non-denominated rows are always reset to 0 (absorbers).
        # Denominated rows with a charge are locked (user's physical count).
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
                # Reset non-denominated row — auto-distribute will refill it
                charge = 0
            row_descriptors.append({
                'index': i,
                'match_pct': method['match_percent'],
                'denomination': method.get('denomination'),
                'sort_order': method.get('sort_order', 0),
                'current_charge': charge,
            })

        if not row_descriptors:
            return

        # ── Overflow detection: add absorber row if needed ────────
        # If every row with a selected method is denominated (locked) and
        # there's remaining balance, add a non-denominated overflow row
        # so smart_auto_distribute has somewhere to put the remainder.
        has_non_denom = any(
            not (d.get('denomination') and d['denomination'] > 0)
            for d in row_descriptors
        )
        if not has_non_denom:
            locked_total = sum(
                charge_to_method_amount(d['current_charge'], d['match_pct'])
                for d in row_descriptors
                if d['current_charge'] > 0
            )
            remaining_balance = self._order_total - locked_total
            if remaining_balance > 1:
                overflow_row = self._add_overflow_row(row_descriptors)
                if overflow_row is not None:
                    method = overflow_row.get_selected_method()
                    new_index = len(self._payment_rows) - 1
                    row_descriptors.append({
                        'index': new_index,
                        'match_pct': method['match_percent'],
                        'denomination': method.get('denomination'),
                        'sort_order': method.get('sort_order', 0),
                        'current_charge': 0,
                    })

        assignments = smart_auto_distribute(self._order_total, row_descriptors)

        # ── Match-cap post-processing ──────────────────────────────
        # When a daily match limit is active, the nominal auto-distribute
        # gives charges based on the full match percentage.  If the total
        # uncapped match exceeds the remaining limit, the customer must
        # cover the deficit — increase matched rows' charges accordingly.
        if self._match_limit is not None and assignments:
            # Sum uncapped match from ALL rows — both new assignments AND
            # locked rows (denominated tokens with charge already set).
            # Without locked rows, the deficit calculation underestimates
            # total match and may not increase charges enough.
            total_uncapped_match = 0
            for a in assignments:
                desc = row_descriptors[
                    next(j for j, d in enumerate(row_descriptors)
                         if d['index'] == a['index'])
                ]
                ma = charge_to_method_amount(a['charge'], desc['match_pct'])
                total_uncapped_match += ma - a['charge']
            for desc in row_descriptors:
                if desc['current_charge'] > 0 and desc['match_pct'] > 0:
                    ma = charge_to_method_amount(
                        desc['current_charge'], desc['match_pct']
                    )
                    total_uncapped_match += ma - desc['current_charge']

            if total_uncapped_match > self._match_limit:
                match_deficit = total_uncapped_match - self._match_limit
                # Distribute deficit across matched (non-denominated) rows
                for a in assignments:
                    desc = row_descriptors[
                        next(j for j, d in enumerate(row_descriptors)
                             if d['index'] == a['index'])
                    ]
                    if desc['match_pct'] > 0 and not (
                        desc.get('denomination') and desc['denomination'] > 0
                    ):
                        row_match = (
                            charge_to_method_amount(
                                a['charge'], desc['match_pct']
                            ) - a['charge']
                        )
                        if total_uncapped_match > 0:
                            share = round(
                                match_deficit * row_match
                                / total_uncapped_match
                            )
                            a['charge'] += share

        # Apply assignments to payment rows
        for assignment in assignments:
            row = self._payment_rows[assignment['index']]
            row._set_active_charge(assignment['charge'])
            row._recompute()

        self._update_summary()

    def _add_overflow_row(self, existing_descriptors):
        """Add a non-denominated overflow row for auto-distribute.

        Picks the best available method not already in use:
          1. SNAP (highest match, most common)
          2. Cash (fallback, 0% match)
          3. Any non-denominated method

        Returns the new PaymentRow, or None if no suitable method exists.
        """
        from fam.models.payment_method import (
            get_payment_methods_for_market, get_all_payment_methods,
        )

        if self._market_id:
            methods = get_payment_methods_for_market(
                self._market_id, active_only=True
            )
            if not methods:
                methods = get_all_payment_methods(active_only=True)
        else:
            methods = get_all_payment_methods(active_only=True)

        # IDs already selected in existing rows
        used_ids = set()
        for row in self._payment_rows:
            mid = row.get_selected_method_id()
            if mid is not None:
                used_ids.add(mid)

        # Filter to non-denominated methods not already in use
        candidates = [
            m for m in methods
            if m['id'] not in used_ids
            and (not m.get('denomination') or m['denomination'] <= 0)
        ]

        if not candidates:
            return None

        # Priority: SNAP-like (name contains 'snap', highest match%) > Cash > any
        snap_like = [m for m in candidates if 'snap' in m['name'].lower()]
        cash_like = [m for m in candidates if 'cash' in m['name'].lower()]

        if snap_like:
            chosen = max(snap_like, key=lambda m: m['match_percent'])
        elif cash_like:
            chosen = cash_like[0]
        else:
            # Pick the candidate with the highest match percent
            chosen = max(candidates, key=lambda m: m['match_percent'])

        # Add the row and select the method
        new_row = self._add_payment_row()
        combo = new_row.method_combo
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data and data.get('id') == chosen['id']:
                combo.setCurrentIndex(i)
                break

        return new_row

    def _refresh_method_choices(self):
        """Disable already-selected methods in other rows, and hide the
        '+ Add' button when all methods are in use."""
        from fam.models.payment_method import get_all_payment_methods, get_payment_methods_for_market
        if self._market_id:
            methods = get_payment_methods_for_market(self._market_id, active_only=True)
            if not methods:
                methods = get_all_payment_methods(active_only=True)
        else:
            methods = get_all_payment_methods(active_only=True)
        total_methods = len(methods)

        selected_ids = set()
        for row in self._payment_rows:
            mid = row.get_selected_method_id()
            if mid is not None:
                selected_ids.add(mid)

        for row in self._payment_rows:
            row.set_excluded_methods(selected_ids)

        # Hide "+ Add" when all payment methods are already in use
        self.add_method_btn.setVisible(len(self._payment_rows) < total_methods)

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

        # ── Step 1: Push input limits FIRST ──────────────────────────
        # This may clamp widget values (signals blocked) so that the
        # subsequent reads see the corrected amounts.  Without this,
        # the summary would display stale pre-clamp values.
        self._push_row_limits()

        # ── Step 2: Refresh each row's per-row labels (Match / Total)
        # so they reflect the (possibly clamped) charge values.
        for row in self._payment_rows:
            row._recompute()

        # ── Step 3: Read final charge values and compute breakdown ───
        # When a daily match cap allows higher charges than the nominal
        # formula would produce, the nominal method_amount (charge × (1 +
        # pct/100)) can exceed the receipt total.  Cap each row's
        # method_amount at the remaining receipt to prevent over-allocation
        # while still letting the customer enter the real amount they pay.
        entries = []
        running_alloc = 0
        for row in self._payment_rows:
            data = row.get_data()
            if data:
                ma = data['method_amount']
                # Only cap non-denominated rows — denominated rows need
                # their overage to flow through so the existing forfeit
                # detection (remaining < 0) works correctly.
                method = row.get_selected_method()
                is_denom = method and method.get('denomination') and method['denomination'] > 0
                if not is_denom:
                    max_ma = max(0, receipt_total - running_alloc)
                    if ma > max_ma:
                        ma = max_ma
                entries.append({
                    'method_amount': ma,
                    'match_percent': data['match_percent'],
                    'method_name': data['method_name_snapshot'],
                })
                running_alloc += ma

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

            self.summary_row.update_card("allocated", format_dollars(allocated))
            self.summary_row.update_card("remaining", format_dollars(remaining))
            self.summary_row.update_card("customer_pays", format_dollars(result['customer_total_paid']))
            self.summary_row.update_card("fam_match", format_dollars(fam_match))

            # Update row display values to reflect the final calculated
            # breakdown.  This covers match-cap adjustments, penny
            # reconciliation, and denomination overage reductions so the
            # row labels always match the collection list and confirmation
            # dialog — no 1-cent visual discrepancies.
            if result.get('line_items'):
                valid_rows = [r for r in self._payment_rows if r.get_data()]
                for i, row in enumerate(valid_rows):
                    if i < len(result['line_items']):
                        li = result['line_items'][i]
                        row.set_display_values(li['match_amount'], li['method_amount'])

            # Show/hide match cap warning
            if result.get('match_was_capped'):
                uncapped = result['uncapped_fam_subsidy_total']
                if self._prior_match > 0:
                    self.match_cap_warning.setText(
                        f"FAM Match capped at {format_dollars(self._match_limit)} \u2014 "
                        f"this customer already redeemed {format_dollars(self._prior_match)} "
                        f"of their {format_dollars(self._daily_limit)} daily limit"
                    )
                else:
                    self.match_cap_warning.setText(
                        f"FAM Match capped at {format_dollars(self._match_limit)} "
                        f"(daily limit per customer \u2014 "
                        f"uncapped would be {format_dollars(uncapped)})"
                    )
                self.match_cap_warning.setVisible(True)
            else:
                self.match_cap_warning.setVisible(False)

            # Dynamic color-coding
            # Remaining: green when $0, red when over-allocated, gold when under

            # Check for denomination overage BEFORE penny normalization so
            # even a $0.01 overage from denominations is properly detected
            # and displayed (adjusted match, warning label, gold color).
            is_denom_overage = False
            denom_overage_amt = 0
            if remaining < 0:
                # Use the *effective* denomination — the method_amount of
                # one denomination unit — as the threshold.  A $5 token with
                # 100% match creates $10 of allocation per unit, so the max
                # legitimate overshoot is up to $10, not $5.
                effective_denom_sum = 0
                for row in self._payment_rows:
                    method = row.get_selected_method()
                    if method and method.get('denomination'):
                        effective_denom_sum += charge_to_method_amount(
                            method['denomination'], method['match_percent']
                        )
                if effective_denom_sum > 0 and abs(remaining) <= effective_denom_sum:
                    is_denom_overage = True
                    denom_overage_amt = abs(remaining)

            # Penny tolerance: treat ±1 cent as fully allocated, but ONLY
            # when it's NOT a denomination overage (those are real forfeit).
            # Also update the allocated display to match the order total so
            # the summary cards are visually consistent (no contradictory
            # "$56.77 order / $56.76 allocated / $0.00 remaining").
            if abs(remaining) <= 1 and not is_denom_overage:
                remaining = 0
                self.summary_row.update_card("remaining", "$0.00")
                self.summary_row.update_card("allocated", format_dollars(receipt_total))

            # When denomination overage exists, adjust displayed totals so
            # the summary cards and collection list show the real match
            # (reduced by the forfeit amount), not the raw calculation.
            if is_denom_overage and denom_overage_amt > 0:
                fam_match = fam_match - denom_overage_amt
                self.summary_row.update_card("fam_match", format_dollars(fam_match))
                # Adjust result line items so collection list shows correct match
                overage_left = denom_overage_amt
                for li in result['line_items']:
                    if overage_left <= 0:
                        break
                    if li['match_amount'] > 0:
                        reduction = min(overage_left, li['match_amount'])
                        li['match_amount'] = li['match_amount'] - reduction
                        li['method_amount'] = li['method_amount'] - reduction
                        overage_left = overage_left - reduction

            if remaining == 0:
                self.summary_row.update_card_color("remaining", PRIMARY_GREEN)
                self.summary_row.update_card_color("allocated", PRIMARY_GREEN)
                self.denom_overage_warning.setVisible(False)
            elif remaining < 0 and is_denom_overage:
                # Denomination overage — warn but don't show as hard error
                self.summary_row.update_card_color("remaining", HARVEST_GOLD)
                self.summary_row.update_card_color("allocated", HARVEST_GOLD)
                self.denom_overage_warning.setText(
                    f"\u26a0  Denomination overage: {format_dollars(denom_overage_amt)} — "
                    f"Customer forfeits {format_dollars(denom_overage_amt)} of FAM match because "
                    f"denominated payment cannot be broken into smaller increments."
                )
                self.denom_overage_warning.setVisible(True)
            elif remaining < 0:
                self.summary_row.update_card_color("remaining", ERROR_COLOR)
                self.summary_row.update_card_color("allocated", ERROR_COLOR)
                self.denom_overage_warning.setVisible(False)
            else:
                self.summary_row.update_card_color("remaining", HARVEST_GOLD)
                self.summary_row.update_card_color("allocated", HARVEST_GOLD)
                self.denom_overage_warning.setVisible(False)

            # FAM match: green when there's a match, grey when zero
            if fam_match > 0:
                self.summary_row.update_card_color("fam_match", PRIMARY_GREEN)
            else:
                self.summary_row.update_card_color("fam_match", MEDIUM_GRAY)

            self._update_collection_list(entries, result)
        else:
            self.summary_row.update_card("allocated", "$0.00")
            self.summary_row.update_card("remaining", format_dollars(receipt_total))
            self.summary_row.update_card("customer_pays", "$0.00")
            self.summary_row.update_card("fam_match", "$0.00")

            # Reset colors to defaults when no entries
            self.summary_row.update_card_color("allocated", MEDIUM_GRAY)
            self.summary_row.update_card_color("remaining", HARVEST_GOLD)
            self.summary_row.update_card_color("customer_pays", PRIMARY_GREEN)
            self.summary_row.update_card_color("fam_match", MEDIUM_GRAY)

            self._clear_collection_list()
            self.match_cap_warning.setVisible(False)
            self.denom_overage_warning.setVisible(False)

    def _push_row_limits(self):
        """Cap each row's input to prevent exceeding the remaining order balance.

        Block signals on all rows first to prevent cascading updates —
        setMaximum() clamps current values which would fire valueChanged
        and re-enter _update_summary in a loop.

        When a daily match limit is active, the max charge must account for
        the reduced effective match.  Without this, a 100% match method would
        cap the charge at ``remaining / 2`` even though the customer must pay
        ``remaining - available_match`` when the cap kicks in.
        """
        from fam.utils.calculations import charge_to_method_amount

        # Block signals on all rows to prevent cascade
        for row in self._payment_rows:
            row.blockSignals(True)

        try:
            for i, row in enumerate(self._payment_rows):
                method = row.get_selected_method()
                if not method:
                    continue
                # Sum method_amount from all OTHER rows (integer cents)
                other_total = 0
                other_match = 0          # match consumed by other rows
                for j, r in enumerate(self._payment_rows):
                    if j == i or not r.has_method_selected():
                        continue
                    other_method = r.get_selected_method()
                    if other_method:
                        other_charge = r._get_active_charge()
                        other_ma = charge_to_method_amount(
                            other_charge,
                            other_method['match_percent']
                        )
                        other_total += other_ma
                        other_match += other_ma - other_charge
                remaining = max(0, self._order_total - other_total)

                # Convert remaining (method_amount space) to charge space.
                # Always floor so the customer never absorbs a rounding
                # penny — any ≤1-cent gap is pushed to FAM match during
                # penny reconciliation.  Uses the same floor logic as
                # smart_auto_distribute so both paths always agree.
                match_pct = method['match_percent']
                divisor = 1.0 + match_pct / 100.0
                max_charge_nominal = int(remaining / divisor)

                # When a daily match limit is active, the nominal formula
                # assumes full match — but the cap may reduce the effective
                # match, requiring the customer to pay more.  Compute the
                # match-limit-aware max charge.
                #
                # IMPORTANT: This inflation ONLY applies to non-denominated
                # methods.  Denominated methods (tokens, checks) are physical
                # units — the customer hands over N × denomination.  Inflating
                # max_charge would allow too many units whose uncapped
                # method_amount far exceeds the receipt total.
                denom = method.get('denomination')
                is_denominated = denom and denom > 0

                if self._match_limit is not None and match_pct > 0 and not is_denominated:
                    available_match = max(0, self._match_limit - other_match)
                    # Uncapped match for the full remaining balance
                    uncapped_match = remaining - max_charge_nominal
                    if uncapped_match > available_match:
                        # Match is capped — customer must cover the gap
                        max_charge_capped = remaining - available_match
                        max_charge = max(max_charge_nominal, max_charge_capped)
                    else:
                        max_charge = max_charge_nominal
                else:
                    max_charge = max_charge_nominal

                # Denominated methods: allow +1 unit for forfeit if there's
                # a gap that exact denomination units can't fill.  The customer
                # hands over a real check — FAM match flexes to absorb the
                # overage.  The denom_overage_warning already shows this in
                # yellow in the summary area.
                if is_denominated:
                    normal_units = int(max_charge / denom)
                    normal_alloc = charge_to_method_amount(
                        normal_units * denom, match_pct
                    )
                    if remaining - normal_alloc > 1:
                        max_charge = (normal_units + 1) * denom

                row.set_max_charge(max_charge)
        finally:
            # Always unblock signals
            for row in self._payment_rows:
                row.blockSignals(False)

    def _update_collection_list(self, entries, result):
        """Rebuild the compact collection checklist next to the Confirm button."""
        self._clear_collection_list()

        if not result.get('line_items'):
            return

        customer_total = 0
        for i, li in enumerate(result['line_items']):
            method_name = entries[i].get('method_name', 'Unknown')
            customer_charged = li['customer_charged']
            match_amount = li['match_amount']
            method_amount = li['method_amount']

            if method_amount <= 0:
                continue

            customer_total += customer_charged

            text = f"•  {format_dollars(customer_charged)} via {method_name}"
            if match_amount > 0:
                text += f"  (FAM matches {format_dollars(match_amount)})"

            lbl = QLabel(text)
            lbl.setStyleSheet("font-size: 13px; padding: 1px 0;")
            self.collect_list_layout.addWidget(lbl)

        self.collect_total_label.setText(f"Total to Collect: {format_dollars(customer_total)}")

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
        running_alloc = 0
        receipt_total = self._order_total
        for row in self._payment_rows:
            data = row.get_data()
            if data and data['method_amount'] > 0:
                # Only cap non-denominated rows — denominated rows need
                # their overage to flow through so forfeit detection and
                # _apply_denomination_forfeit() work correctly.
                method = row.get_selected_method()
                is_denom = method and method.get('denomination') and method['denomination'] > 0
                if not is_denom:
                    max_ma = max(0, receipt_total - running_alloc)
                    if data['method_amount'] > max_ma:
                        data['method_amount'] = max_ma
                running_alloc += data['method_amount']
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
                self._distribute_and_save_payments(items, self._order_total)

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

    def _check_denomination_overage(self, result, receipt_total):
        """Check if an over-allocation is caused by denomination constraints.

        Returns the overage amount (> 0) if denominated payment methods are
        causing the total to exceed the receipt, or 0 if this is not a
        denomination-related issue.

        A denomination overage happens when a denominated payment like FMNP
        ($25 checks) can't be broken into smaller increments, so the
        allocation overshoots the receipt total.  For example, a $49 order
        with a $25 FMNP charge produces $50 total allocation — the $1
        difference is a match forfeit the customer must accept.
        """
        allocated = result.get('allocated_total', 0)
        overage = allocated - receipt_total

        # Only applies when over-allocated (not under or exact).
        if overage <= 0:
            return 0

        # Use effective denomination (method_amount of one unit) as
        # threshold.  A $5 token with 100% match creates $10 allocation
        # per unit, so a single +1 unit can overshoot by up to $10.
        effective_denom_sum = 0
        for row in self._payment_rows:
            method = row.get_selected_method()
            if method and method.get('denomination'):
                effective_denom_sum += charge_to_method_amount(
                    method['denomination'], method['match_percent']
                )

        if effective_denom_sum <= 0:
            return 0

        # Only allow overage up to the effective denomination sum
        if overage > effective_denom_sum:
            return 0

        return overage

    def _apply_denomination_forfeit(self, result, items, overage):
        """Reduce match on denominated line items so vendor total equals receipt.

        When a denomination method (e.g. $5 FMNP checks) over-allocates the
        order, the customer forfeits a portion of FAM match.  This adjusts the
        breakdown so saved data is correct: customer_charged stays the same,
        method_amount and match_amount are reduced by the overage.
        """
        remaining_overage = overage
        for i, li in enumerate(result['line_items']):
            if remaining_overage <= 0:
                break
            if li['match_amount'] > 0:
                reduction = min(remaining_overage, li['match_amount'])
                li['match_amount'] = li['match_amount'] - reduction
                li['method_amount'] = li['method_amount'] - reduction
                # Also update the items list so saved data is correct
                items[i]['method_amount'] = li['method_amount']
                items[i]['match_amount'] = li['match_amount']
                remaining_overage = remaining_overage - reduction

        # Update result totals
        result['allocated_total'] = result['allocated_total'] - overage
        result['fam_subsidy_total'] = result['fam_subsidy_total'] - overage

    def _confirm_payment(self):
        # Prevent double-click from triggering duplicate processing
        self.confirm_btn.setEnabled(False)

        self.error_label.setVisible(False)
        self.error_label.setText("")
        self.success_frame.setVisible(False)

        # Check denomination constraints before proceeding
        for row in self._payment_rows:
            denom_error = row.validate_denomination()
            if denom_error:
                self._show_error(denom_error)
                self.confirm_btn.setEnabled(True)
                return

        # Check photo receipt requirements
        for row in self._payment_rows:
            photo_error = row.validate_photo()
            if photo_error:
                self._show_error(photo_error)
                self.confirm_btn.setEnabled(True)
                return

        if not self._order_transactions:
            self._show_error("No transactions loaded.")
            self.confirm_btn.setEnabled(True)
            return

        receipt_total = self._order_total
        items = self._collect_line_items()

        if not items:
            self._show_error("At least one payment method with an amount is required.")
            self.confirm_btn.setEnabled(True)
            return

        entries = [
            {'method_amount': it['method_amount'], 'match_percent': it['match_percent']}
            for it in items
        ]
        result = calculate_payment_breakdown(
            receipt_total, entries, match_limit=self._match_limit
        )

        # Check if over-allocation is caused by denomination constraints.
        # Always check — even penny-level overages are real forfeit when
        # caused by denomination rounding.
        denom_overage = self._check_denomination_overage(result, receipt_total)
        if denom_overage > 0:
            # Adjust line items: reduce match to account for forfeit so
            # vendor reimbursement stays exactly at receipt_total.
            self._apply_denomination_forfeit(result, items, denom_overage)
        elif not result['is_valid']:
            # Not a denomination issue — hard block
            self._show_error("\n".join(result['errors']))
            self.confirm_btn.setEnabled(True)
            return

        # ── Pre-confirmation dialog: list what to collect ─────────
        confirm_lines = ["Please confirm you have collected the following "
                         "from the customer:\n"]
        customer_total = 0
        if result.get('line_items'):
            for i, li in enumerate(result['line_items']):
                method_name = items[i].get('method_name_snapshot', 'Unknown')
                customer_charged = li['customer_charged']
                match_amount = li['match_amount']
                method_amount = li['method_amount']
                if method_amount <= 0:
                    continue
                customer_total += customer_charged
                line = f"  •  {format_dollars(customer_charged)} via {method_name}"
                if match_amount > 0:
                    line += f"  (FAM matches {format_dollars(match_amount)})"
                confirm_lines.append(line)

        confirm_lines.append(f"\nTotal to collect: {format_dollars(customer_total)}")
        confirm_lines.append(f"Order total (vendor reimbursement): {format_dollars(receipt_total)}")
        confirm_lines.append(f"\nReceipts: {len(self._order_transactions)}")

        if denom_overage > 0:
            confirm_lines.append(
                f"\n⚠  DENOMINATION OVERAGE: {format_dollars(denom_overage)}\n"
                f"The customer is forfeiting {format_dollars(denom_overage)} of FAM match "
                f"because the denominated payment cannot be broken into "
                f"smaller increments."
            )

        answer = QMessageBox.question(
            self, "Confirm Payment Collection",
            "\n".join(confirm_lines),
            QMessageBox.Yes | QMessageBox.No
        )
        if answer != QMessageBox.Yes:
            self.confirm_btn.setEnabled(True)
            return

        # ── Process the confirmed payment (atomic) ─────────────
        conn = get_connection()
        try:
            self._distribute_and_save_payments(items, receipt_total, commit=False)

            # Use the active market day's volunteer name for audit trail
            open_md = get_open_market_day()
            confirmed_by = (open_md.get('opened_by') if open_md else None) or 'Volunteer'
            for t in self._order_transactions:
                confirm_transaction(t['id'], confirmed_by=confirmed_by, commit=False)

            if self._current_order_id:
                update_customer_order_status(self._current_order_id, 'Confirmed',
                                             commit=False)

            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.exception("Payment confirmation failed — rolled back")
            self._show_error(f"Payment failed: {e}")
            self.confirm_btn.setEnabled(True)
            return

        txn_ids = ", ".join(t['fam_transaction_id'] for t in self._order_transactions)
        logger.info("Payment confirmed for: %s", txn_ids)
        write_ledger_backup()

        # Snapshot receipt data for the optional print button
        self._last_receipt_data = self._build_receipt_data()

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
        if not order_total or order_total <= 0:
            return

        # Build all per-transaction line items using remainder-based
        # distribution so that rounded amounts sum exactly to the totals.
        all_txn_items = []
        num_txns = len(self._order_transactions)

        # Track running totals per payment-method index so the last
        # transaction gets the exact remainder (no penny drift).
        allocated_method = [0] * len(items)
        allocated_match = [0] * len(items)

        for t_idx, t in enumerate(self._order_transactions):
            is_last = (t_idx == num_txns - 1)
            proportion = t['receipt_total'] / order_total
            txn_items = []

            for j, item in enumerate(items):
                if is_last:
                    # Last transaction gets the remainder
                    method_amount = item['method_amount'] - allocated_method[j]
                else:
                    method_amount = round(item['method_amount'] * proportion)
                    allocated_method[j] += method_amount

                match_pct = item['match_percent_snapshot']
                if is_last:
                    # Compute the exact match for the original total, then subtract
                    # what was already distributed to previous transactions
                    total_match = round(
                        item['method_amount'] * (match_pct / (100.0 + match_pct))
                    )
                    match_amount = total_match - allocated_match[j]
                else:
                    match_amount = round(method_amount * (match_pct / (100.0 + match_pct)))
                    allocated_match[j] += match_amount

                customer_charged = method_amount - match_amount

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
                            li['match_amount'] * cap_ratio
                        )
                        li['customer_charged'] = (
                            li['method_amount'] - li['match_amount']
                        )

                # Penny adjustment: fix rounding drift so sum == cap exactly
                capped_sum = sum(
                    li['match_amount']
                    for txn_items in all_txn_items for li in txn_items
                )
                penny_diff = self._match_limit - capped_sum
                if penny_diff != 0:
                    # Adjust the line item with the largest match
                    all_lines = [
                        li for txn_items in all_txn_items for li in txn_items
                        if li['match_amount'] > 0
                    ]
                    if all_lines:
                        target = max(all_lines, key=lambda li: li['match_amount'])
                        target['match_amount'] = (
                            target['match_amount'] + penny_diff
                        )
                        target['customer_charged'] = (
                            target['method_amount'] - target['match_amount']
                        )

        # Store photos from payment rows (if any) and attach paths to line items.
        # Photos are stored once and reused across multi-receipt transactions.
        # Supports multiple photos per payment method (e.g. 3 FMNP checks = 3 photos).
        stored_photos = {}  # payment_method_id -> encoded_path (JSON array or single)
        for j, item in enumerate(items):
            source_paths = item.get('photo_source_paths', [])
            if source_paths:
                try:
                    from fam.utils.photo_storage import store_photo
                    from fam.utils.photo_paths import encode_photo_paths
                    pm_id = item['payment_method_id']
                    rel_paths = []
                    for src in source_paths:
                        if src:
                            rel = store_photo(src, pm_id, prefix='pay')
                            rel_paths.append(rel)
                    if rel_paths:
                        stored_photos[pm_id] = encode_photo_paths(rel_paths)
                except Exception:
                    logger.warning("Failed to store payment photo for method %s",
                                   item.get('method_name_snapshot'), exc_info=True)

        # Inject photo_path into all transaction line items
        if stored_photos:
            for txn_items in all_txn_items:
                for li in txn_items:
                    photo = stored_photos.get(li['payment_method_id'])
                    if photo:
                        li['photo_path'] = photo

        # Save to DB
        for t, txn_items in zip(self._order_transactions, all_txn_items):
            save_payment_line_items(t['id'], txn_items, commit=commit)

    def _show_error(self, msg):
        self.error_label.setText(msg)
        self.error_label.setVisible(True)

    # ------------------------------------------------------------------
    # Receipt printing
    # ------------------------------------------------------------------

    def _build_receipt_data(self) -> dict | None:
        """Snapshot all data needed to print a customer receipt."""
        try:
            order = None
            if self._current_order_id:
                order = get_customer_order(self._current_order_id)

            open_md = get_open_market_day()
            market_name = ''
            market_date = ''
            if order:
                market_name = order.get('market_name', '')
                market_date = order.get('market_day_date', '')
            if not market_name and open_md:
                market_name = open_md.get('market_name', '')
                market_date = open_md.get('date', '')

            customer_label = order.get('customer_label', '') if order else ''
            confirmed_by = (open_md.get('opened_by', '') if open_md else '') or 'Volunteer'

            txns = []
            total_receipt = 0
            total_customer = 0
            total_match = 0
            payment_totals: dict[str, dict] = {}

            for t in self._order_transactions:
                txn = get_transaction_by_id(t['id'])
                receipt = txn['receipt_total']  # integer cents
                total_receipt += receipt
                line_items = get_payment_line_items(t['id'])

                txns.append({
                    'fam_id': txn['fam_transaction_id'],
                    'vendor': txn.get('vendor_name', ''),
                    'receipt_total': cents_to_dollars(receipt),
                })

                for li in line_items:
                    method = li['method_name_snapshot']
                    amt = li['method_amount']    # integer cents
                    match = li['match_amount']   # integer cents
                    cust = li['customer_charged']  # integer cents
                    total_customer += cust
                    total_match += match
                    if method not in payment_totals:
                        payment_totals[method] = {
                            'amount': 0, 'match': 0, 'customer': 0,
                        }
                    payment_totals[method]['amount'] += amt
                    payment_totals[method]['match'] += match
                    payment_totals[method]['customer'] += cust

            # Convert accumulated cents to dollars at the display boundary
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
                'transactions': txns,
                'payment_totals': payment_totals_dollars,
                'total_receipt': cents_to_dollars(total_receipt),
                'total_customer': cents_to_dollars(total_customer),
                'total_match': cents_to_dollars(total_match),
            }
        except Exception:
            logger.exception("Failed to build receipt data")
            return None

    def _print_receipt(self):
        """Open a print dialog with a formatted customer receipt."""
        data = self._last_receipt_data
        if not data:
            QMessageBox.information(self, "Print Receipt",
                                    "No receipt data available.")
            return

        html = self._format_receipt_html(data)

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
            logger.info("Customer receipt printed for %s", data.get('customer_label'))

    @staticmethod
    def _format_receipt_html(data: dict) -> str:
        """Build a clean HTML receipt from the snapshot data.

        The *data* dict may contain an optional ``status`` key.  When the
        status is ``'Voided'`` a prominent red banner is displayed at the
        top of the receipt so it cannot be mistaken for a live transaction.
        """
        market = data['market_name'] or 'Market'
        date = data['market_date'] or ''
        customer = data['customer_label'] or ''
        status = data.get('status', 'Confirmed')

        # Voided banner (only shown for voided transactions)
        voided_banner = ""
        if status == 'Voided':
            voided_banner = (
                "<div style='text-align:center; padding:6px; margin-bottom:8px; "
                "background-color:#fde8e8; border:2px solid #c0392b; "
                "border-radius:4px;'>"
                "<span style='font-size:14pt; font-weight:bold; color:#c0392b; "
                "letter-spacing:3px;'>VOIDED</span></div>"
            )

        rows = ""
        for i, t in enumerate(data['transactions'], 1):
            rows += (
                f"<tr>"
                f"<td style='padding:2px 8px 2px 0;'>{i}</td>"
                f"<td style='padding:2px 8px;'>{t['vendor']}</td>"
                f"<td style='padding:2px 0 2px 8px;text-align:right;'>"
                f"${t['receipt_total']:,.2f}</td>"
                f"</tr>"
            )

        payment_rows = ""
        for method, totals in data['payment_totals'].items():
            amt = totals['amount']
            match = totals['match']
            cust = totals['customer']
            payment_rows += (
                f"<tr>"
                f"<td style='padding:2px 8px 2px 0;'>{method}</td>"
                f"<td style='padding:2px 8px;text-align:right;'>${amt:,.2f}</td>"
                f"<td style='padding:2px 0 2px 8px;text-align:right;'>"
                f"${match:,.2f}</td>"
                f"</tr>"
            )

        from fam.utils.app_settings import get_market_code
        receipt_code = get_market_code() or ''

        return f"""
        <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 400px;
                    margin: 0 auto; font-size: 11pt;">

            <h2 style="text-align:center; margin-bottom:2px; color:#2b493b;">
                FAM Market Manager</h2>
            <p style="text-align:center; margin-top:0; color:#666; font-size:10pt;">
                Customer Receipt{f'  ({receipt_code})' if receipt_code else ''}</p>

            {voided_banner}

            <hr style="border: 1px solid #2b493b;">

            <table style="width:100%; font-size:11pt; margin-bottom:8px;">
                <tr>
                    <td><b>Market:</b></td>
                    <td style="text-align:right;">{market}</td>
                </tr>
                <tr>
                    <td><b>Date:</b></td>
                    <td style="text-align:right;">{date}</td>
                </tr>
                <tr>
                    <td><b>Customer:</b></td>
                    <td style="text-align:right;">{customer}</td>
                </tr>
            </table>

            <hr style="border: 0.5px solid #ccc;">

            <p style="font-weight:bold; margin-bottom:4px;">Purchases</p>
            <table style="width:100%; font-size:11pt; border-collapse:collapse;">
                <tr style="border-bottom:1px solid #ccc;">
                    <th style="text-align:left; padding:2px 8px 2px 0;">#</th>
                    <th style="text-align:left; padding:2px 8px;">Vendor</th>
                    <th style="text-align:right; padding:2px 0 2px 8px;">Amount</th>
                </tr>
                {rows}
                <tr style="border-top:1px solid #999;">
                    <td></td>
                    <td style="padding:4px 8px; font-weight:bold;">Subtotal</td>
                    <td style="padding:4px 0 4px 8px; text-align:right; font-weight:bold;">
                        ${data['total_receipt']:,.2f}</td>
                </tr>
            </table>

            <hr style="border: 0.5px solid #ccc;">

            <p style="font-weight:bold; margin-bottom:4px;">Payment Summary</p>
            <table style="width:100%; font-size:11pt; border-collapse:collapse;">
                <tr style="border-bottom:1px solid #ccc;">
                    <th style="text-align:left; padding:2px 8px 2px 0;">Method</th>
                    <th style="text-align:right; padding:2px 8px;">Amount</th>
                    <th style="text-align:right; padding:2px 0 2px 8px;">FAM Match</th>
                </tr>
                {payment_rows}
            </table>

            <hr style="border: 0.5px solid #ccc;">

            <table style="width:100%; font-size:12pt; margin:8px 0;">
                <tr>
                    <td><b>You paid:</b></td>
                    <td style="text-align:right; font-weight:bold; color:#2b493b;">
                        ${data['total_customer']:,.2f}</td>
                </tr>
                <tr>
                    <td><b>FAM matched:</b></td>
                    <td style="text-align:right; font-weight:bold; color:#469a45;">
                        ${data['total_match']:,.2f}</td>
                </tr>
                <tr style="border-top:1px solid #999;">
                    <td style="padding-top:4px;"><b>Vendor total:</b></td>
                    <td style="padding-top:4px; text-align:right; font-weight:bold;">
                        ${data['total_receipt']:,.2f}</td>
                </tr>
            </table>

            <hr style="border: 1px solid #2b493b;">

            <p style="text-align:center; font-size:11pt; color:#2b493b; margin:8px 0 2px;">
                Thank you for shopping at the market!</p>
            <p style="text-align:center; font-size:9pt; color:#999; margin:0;">
                Confirmed by: {data['confirmed_by']}</p>
        </div>
        """
