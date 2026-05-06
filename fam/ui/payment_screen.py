"""Screen C: Payment Processing — supports multi-receipt customer orders."""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QMessageBox, QTableWidget, QDialog,
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

    payment_confirmed = Signal()           # fires on every confirm (drives sync)
    draft_saved = Signal()                  # fires on every draft save (drives sync)
    return_to_intake_requested = Signal()   # fires only when volunteer clicks "Yes" to return to intake

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
                # Group items by payment method so denominated rows that
                # already span multiple vendors round-trip with their
                # bindings intact (one row per vendor for a given
                # denominated method).  For non-denominated rows the
                # existing one-row-per-method shape still works because
                # we save them with bound_vendor_id=None.
                grouped = self._group_saved_line_items_for_restore(items)
                for entry in grouped:
                    row = self._add_payment_row()
                    row.set_data(
                        entry['payment_method_id'],
                        entry['method_amount'],
                        customer_charged=entry.get('customer_charged'),
                        bound_vendor_id=entry.get('bound_vendor_id'),
                    )
            else:
                self._add_payment_row()
        else:
            self._add_payment_row()

        # Push the (now-known) order vendor pool to every row so
        # denominated rows can populate their per-row vendor combo.
        self._push_order_vendors_to_rows()

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
                    row.set_data(
                        item['payment_method_id'],
                        item['method_amount'],
                        customer_charged=item.get('customer_charged'),
                    )
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
        """Build the per-vendor breakdown table for the current order.

        Layout (v1.9.9+):

            Vendor | Receipt | Remaining | Method 1 | Method 2 | ...

        Where each method column shows:
        - red ✗ if the vendor isn't registered for that method
        - green ✓ otherwise; for denominated methods that already have
          rows bound to this vendor, also "N×$D = $T" so the volunteer
          can see at a glance how many physical instruments are
          allocated where.

        Built once when an order loads (column count depends on the
        market's active methods) — the *data* is then refreshed on every
        row change via :meth:`_refresh_vendor_breakdown`.
        """
        # Collect this market's active methods to determine the column set.
        # ``include_system=False`` keeps system-managed methods
        # (Unallocated Funds) out of the live Payment screen — they
        # only ever surface in retrospective reports, never as a
        # column volunteers see during entry.
        from fam.models.payment_method import (
            get_payment_methods_for_market, get_all_payment_methods,
        )
        if self._market_id:
            methods = get_payment_methods_for_market(
                self._market_id, active_only=True, include_system=False)
            if not methods:
                methods = get_all_payment_methods(
                    active_only=True, include_system=False)
        else:
            methods = get_all_payment_methods(
                active_only=True, include_system=False)
        # Stable order: the existing sort_order then name (matches the
        # method-dropdown order for a coherent reading flow).
        methods.sort(key=lambda m: (m.get('sort_order') or 0, m['name']))
        self._breakdown_methods = methods  # cache for the refresh path

        # Vendor-pool order: stable by transaction creation (which is
        # how transactions appear on the receipt-intake screen).  We
        # collapse duplicate-vendor rows the same way the legacy table
        # did — vendor totals roll up across their multi-receipt
        # contribution.
        seen = set()
        vendor_pool = []
        for t in self._order_transactions:
            vid = t.get('vendor_id')
            vname = t.get('vendor_name', '')
            if vid is None or vid in seen:
                continue
            seen.add(vid)
            vendor_pool.append({'id': vid, 'name': vname})
        self._breakdown_vendors = vendor_pool

        # Rebuild table columns: Vendor + Receipt + Remaining + 1 per method
        col_count = 3 + len(methods)
        self.vendor_table.clear()
        self.vendor_table.setColumnCount(col_count)
        headers = ['Vendor', 'Receipt', 'Remaining'] + [
            m['name'] for m in methods
        ]
        self.vendor_table.setHorizontalHeaderLabels(headers)
        configure_table(self.vendor_table)
        self.vendor_table.setRowCount(len(vendor_pool))

        has_vendors = len(vendor_pool) > 0
        self.vendor_lbl.setVisible(has_vendors)
        self.vendor_table.setVisible(has_vendors)

        # Initial render — the data refresh path will keep it current.
        self._refresh_vendor_breakdown()

    # ── Per-vendor allocation helpers (v1.9.9+) ─────────────────────

    def _compute_per_vendor_state(self, engine_line_items=None) -> dict:
        """Snapshot of allocation state per vendor in the current order.

        Returns ``{vendor_id: state}`` where ``state`` carries the
        receipt total, what's allocated by denominated rows bound to
        that vendor, the proportional share of non-denominated rows,
        the resulting remaining balance, and a per-method breakdown
        with eligibility + count + allocated amount.

        This is the single source of truth for both the breakdown
        table and the smart per-vendor stepper caps.

        When ``engine_line_items`` is provided (the result.line_items
        from :func:`calculate_payment_breakdown`), the per-row
        ``method_amount`` values are taken from the engine *after* its
        order-level penny reconciliation and denomination-forfeit
        adjustments.  This keeps the breakdown table aligned with what
        the save path will commit — without it, an order whose engine
        bumps a non-denom row by +1¢ shows a stale ¢-level remainder
        on the last vendor.
        """
        from fam.models.payment_method import (
            get_vendor_payment_method_ids,
        )

        # Step 1: vendor_id → cumulative receipt_total (rolls up
        # multi-receipt orders that have ≥1 transaction per vendor).
        vendor_state: dict[int, dict] = {}
        for t in self._order_transactions:
            vid = t.get('vendor_id')
            if vid is None:
                continue
            entry = vendor_state.setdefault(vid, {
                'name': t.get('vendor_name', ''),
                'receipt': 0,
                'denom_alloc': 0,
                'non_denom_share': 0,
                'per_method': {},
            })
            entry['receipt'] += t['receipt_total']

        if not vendor_state:
            return vendor_state

        # Step 2: eligibility flags per (vendor, method).
        methods = getattr(self, '_breakdown_methods', None)
        if methods is None:
            from fam.models.payment_method import (
                get_payment_methods_for_market, get_all_payment_methods,
            )
            if self._market_id:
                methods = get_payment_methods_for_market(
                    self._market_id, active_only=True,
                    include_system=False)
                if not methods:
                    methods = get_all_payment_methods(
                        active_only=True, include_system=False)
            else:
                methods = get_all_payment_methods(
                    active_only=True, include_system=False)
        for vid, state in vendor_state.items():
            eligible_ids = get_vendor_payment_method_ids(vid)
            # Treat empty eligibility as permissive (mirrors the
            # graceful fallback in the dropdown filter so legacy /
            # uninitialized data doesn't show every method as ✗).
            permissive = not eligible_ids
            for m in methods:
                state['per_method'][m['id']] = {
                    'eligible': permissive or (m['id'] in eligible_ids),
                    'count': 0,
                    'method_amount': 0,
                    'is_denom': bool(m.get('denomination')
                                      and m['denomination'] > 0),
                    'denomination': m.get('denomination') or 0,
                }

        # Step 3: walk the live PaymentRows.  Phase 1 — denominated
        # rows attribute their full method_amount to the bound vendor.
        rows_data = []
        for row in self._payment_rows:
            data = row.get_data()
            if not data:
                continue
            rows_data.append(data)

        # If the caller passed engine-adjusted line items, override
        # each row's method_amount + match_amount with the engine's
        # post-penny-rec / post-forfeit values.  Same row order: rows
        # with method_amount > 0 are kept in the same sequence
        # ``_collect_line_items`` builds them.
        if engine_line_items is not None:
            # The engine line_items list is filtered to method_amount > 0
            # before construction; rows_data is filtered to data not None
            # but may still include rows whose method_amount is 0.
            # Build a parallel index of rows_data entries that should
            # match by position with engine_line_items.
            li_idx = 0
            for d in rows_data:
                if d['method_amount'] <= 0:
                    continue
                if li_idx >= len(engine_line_items):
                    break
                li = engine_line_items[li_idx]
                d['method_amount'] = li['method_amount']
                d['match_amount'] = li.get('match_amount', d.get('match_amount', 0))
                d['customer_charged'] = li.get(
                    'customer_charged', d.get('customer_charged', 0))
                li_idx += 1

        for d in rows_data:
            denom = d.get('denomination')
            is_denom = bool(denom and denom > 0)
            if not is_denom:
                continue
            bound_vid = d.get('bound_vendor_id')
            if bound_vid is None and len(vendor_state) == 1:
                # Single-vendor order — implicit binding to the only vendor
                bound_vid = next(iter(vendor_state.keys()))
            if bound_vid not in vendor_state:
                continue
            state = vendor_state[bound_vid]
            ma = d['method_amount']
            charge = d.get('customer_charged', 0)
            unit_count = (charge // denom) if denom > 0 else 0
            state['denom_alloc'] += ma
            pm = state['per_method'].get(d['payment_method_id'])
            if pm is not None:
                pm['count'] += unit_count
                pm['method_amount'] += ma

        # Phase 2 — non-denominated rows: distribute by per-vendor
        # remaining (after denominated reservation), matching the
        # save algorithm.
        for d in rows_data:
            denom = d.get('denomination')
            if denom and denom > 0:
                continue
            ma_total = d['method_amount']
            # Compute per-vendor remaining BEFORE this row's share
            per_vendor_remaining = {
                vid: max(0, s['receipt'] - s['denom_alloc']
                          - s['non_denom_share'])
                for vid, s in vendor_state.items()
            }
            total_remaining = sum(per_vendor_remaining.values())
            if total_remaining <= 0:
                continue
            # Stable iteration so the "last vendor gets remainder"
            # path is deterministic.
            vids = list(vendor_state.keys())
            running = 0
            for i, vid in enumerate(vids):
                if i == len(vids) - 1:
                    share = ma_total - running
                else:
                    weight = (per_vendor_remaining[vid] / total_remaining
                              if total_remaining > 0 else 0)
                    share = round(ma_total * weight)
                    running += share
                if share <= 0:
                    continue
                state = vendor_state[vid]
                state['non_denom_share'] += share
                pm = state['per_method'].get(d['payment_method_id'])
                if pm is not None:
                    pm['method_amount'] += share

        # Step 4: derive allocated + remaining.
        for s in vendor_state.values():
            s['allocated'] = s['denom_alloc'] + s['non_denom_share']
            s['remaining'] = s['receipt'] - s['allocated']

        return vendor_state

    def _refresh_vendor_breakdown(self, engine_line_items=None):
        """Re-populate the vendor breakdown table from current state.

        Called from :meth:`_populate_vendor_summary` (initial render —
        no engine result yet) and from :meth:`_update_summary` whenever
        rows change.  When the engine result is available pass its
        ``line_items`` so the per-vendor breakdown reflects the
        post-penny-rec / post-forfeit method_amounts the save path
        will actually commit.
        """
        if not getattr(self, '_breakdown_vendors', None):
            return
        state_by_vid = self._compute_per_vendor_state(
            engine_line_items=engine_line_items)
        methods = self._breakdown_methods

        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QBrush
        from fam.ui.styles import (
            ACCENT_GREEN, HARVEST_GOLD, ERROR_COLOR, MEDIUM_GRAY,
        )

        self.vendor_table.setSortingEnabled(False)
        for i, vendor in enumerate(self._breakdown_vendors):
            vid = vendor['id']
            s = state_by_vid.get(vid)
            if s is None:
                continue
            # Vendor name
            self.vendor_table.setItem(i, 0, make_item(vendor['name']))
            # Receipt
            receipt_item = make_item(format_dollars(s['receipt']),
                                      s['receipt'])
            self.vendor_table.setItem(i, 1, receipt_item)
            # Remaining (color-coded)
            rem = s['remaining']
            rem_item = make_item(format_dollars(rem), rem)
            if rem == 0:
                rem_item.setForeground(QBrush(QColor(ACCENT_GREEN)))
            elif rem < 0:
                rem_item.setForeground(QBrush(QColor(ERROR_COLOR)))
            else:
                rem_item.setForeground(QBrush(QColor(HARVEST_GOLD)))
            self.vendor_table.setItem(i, 2, rem_item)
            # Per-method cells
            for col_offset, m in enumerate(methods):
                col = 3 + col_offset
                pm = s['per_method'].get(m['id'])
                if pm is None or not pm['eligible']:
                    cell = make_item("✗")
                    cell.setForeground(
                        QBrush(QColor(ERROR_COLOR)))
                    cell.setTextAlignment(Qt.AlignCenter)
                    self.vendor_table.setItem(i, col, cell)
                    continue
                # Eligible — show running allocation
                if pm['is_denom']:
                    if pm['count'] > 0:
                        denom_dollars = pm['denomination'] / 100.0
                        total_dollars = pm['method_amount'] / 100.0
                        text = (f"✓  {pm['count']} × ${denom_dollars:.2f}"
                                f" = ${total_dollars:.2f}")
                    else:
                        text = "✓"
                else:
                    if pm['method_amount'] > 0:
                        text = f"✓  {format_dollars(pm['method_amount'])}"
                    else:
                        text = "✓"
                cell = make_item(text)
                cell.setForeground(QBrush(QColor(ACCENT_GREEN)))
                if text == "✓":
                    cell.setTextAlignment(Qt.AlignCenter)
                self.vendor_table.setItem(i, col, cell)
            self.vendor_table.setRowHeight(i, 30)
        self.vendor_table.setSortingEnabled(True)

        # Auto-fit table height: header + rows + border.
        header_h = self.vendor_table.horizontalHeader().height()
        rows_h = sum(
            self.vendor_table.rowHeight(i)
            for i in range(len(self._breakdown_vendors))
        )
        self.vendor_table.setFixedHeight(header_h + rows_h + 4)

    # ------------------------------------------------------------------
    # Payment rows
    # ------------------------------------------------------------------
    def _add_payment_row(self):
        row = PaymentRow(market_id=self._market_id)
        row.changed.connect(self._on_row_changed)
        row.remove_requested.connect(self._remove_payment_row)
        self._payment_rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        # Push the current order's vendor pool so denominated rows can
        # populate their per-row vendor dropdown.  Safe to call when no
        # order is loaded — the row hides the dropdown if the pool is
        # empty or single-vendor.
        row.set_order_vendors(self._get_order_vendors())
        self._refresh_method_choices()
        return row

    def _group_saved_line_items_for_restore(self, _seed_items) -> list:
        """Re-derive logical PaymentRows from the saved line_items
        across every transaction in the current order.

        Per the v1.9.9 rearchitecture:

        * Denominated rows are saved entirely on the bound vendor's
          transaction.  Each (payment_method_id, vendor_id) pair maps
          back to a single row with that vendor binding restored.
        * Non-denominated rows are still proportionally distributed
          across every transaction.  All line_items sharing a
          payment_method_id roll up to ONE logical row with
          ``bound_vendor_id=None`` and totals summed across rows.

        ``_seed_items`` is the line_items from the FIRST transaction
        only — kept as a fall-back trigger but no longer the sole
        source.  We always re-read across the full order.
        """
        from fam.models.transaction import get_payment_line_items
        from fam.models.payment_method import get_payment_method_by_id

        # Accumulator: key → entry dict
        # Key: (payment_method_id, vendor_id_or_None)
        denom_groups: dict = {}
        non_denom_groups: dict = {}

        for t in self._order_transactions or []:
            items = get_payment_line_items(t['id'])
            txn_vendor_id = t.get('vendor_id')
            for li in items:
                pm_id = li['payment_method_id']
                # Resolve denomination flag for this payment method.
                method = get_payment_method_by_id(pm_id)
                is_denom = bool(method and method.get('denomination')
                                 and method['denomination'] > 0)
                ma = li.get('method_amount', 0)
                cc = li.get('customer_charged', 0)
                if is_denom:
                    key = (pm_id, txn_vendor_id)
                    g = denom_groups.setdefault(key, {
                        'payment_method_id': pm_id,
                        'method_amount': 0,
                        'customer_charged': 0,
                        'bound_vendor_id': txn_vendor_id,
                    })
                else:
                    key = pm_id
                    g = non_denom_groups.setdefault(key, {
                        'payment_method_id': pm_id,
                        'method_amount': 0,
                        'customer_charged': 0,
                        'bound_vendor_id': None,
                    })
                g['method_amount'] += ma
                g['customer_charged'] += cc

        # v1.9.10 onsite-finding fix: return DENOMINATED rows first,
        # then non-denominated.  The previous order (non-denom first)
        # caused a draft-resume reconciliation drift because
        # ``_update_summary`` walks rows in order and applies the
        # ``effective_total_for_cap - running_alloc`` cap to non-denom
        # rows.  When SNAP was first, it claimed the FULL effective
        # order total; subsequently-added denom rows then added their
        # method on top, producing total method = receipt + denom
        # rows' worth (e.g. $381.50 on a $310.80 order).  Putting
        # denom rows first ensures their fixed method amounts are
        # consumed by ``running_alloc`` BEFORE the non-denom row hits
        # its cap, mirroring the order a volunteer would naturally
        # enter rows (physical instruments first, then SNAP/Cash to
        # cover the rest).
        return list(denom_groups.values()) + list(non_denom_groups.values())

    def _get_order_vendors(self) -> list:
        """Return a unique list of vendor dicts (id, name) from the
        currently-loaded order's transactions.

        Returns ``[]`` when no order is loaded.  Order is preserved by
        first-appearance in ``self._order_transactions`` so the vendor
        dropdown reads consistently across reloads.
        """
        seen = set()
        out = []
        for t in (self._order_transactions or []):
            vid = t.get('vendor_id')
            if vid is None or vid in seen:
                continue
            seen.add(vid)
            out.append({'id': vid, 'name': t.get('vendor_name', '')})
        return out

    def _push_order_vendors_to_rows(self):
        """Refresh the order-vendor pool on every row.

        Called after any change to the order's transactions so each
        row's vendor dropdown reflects the current pool (e.g. a draft
        reload, an admin-side adjustment that voided a transaction).
        """
        pool = self._get_order_vendors()
        for row in self._payment_rows:
            row.set_order_vendors(pool)

    def _compute_effective_order_total(self) -> int:
        """Return the order total *expanded* by per-vendor under-allocation
        when bound denom rows over-allocate their vendor.

        For caps and Auto-Distribute purposes:

          effective = locked_bound_denom_method_amount
                      + Σ max(0, vendor_receipt − vendor_bound_denom_alloc)

        In the no-overage case this collapses to ``self._order_total``
        exactly, so existing behaviour (v1.9.1 match-limit-cap input,
        the original auto-distribute math) is preserved.

        In the overage case it adds back the headroom that *other*
        vendors still need so non-denominated absorbers can fully
        cover them; the engine's denomination-forfeit path (in
        :meth:`_apply_denomination_forfeit`) then reduces the
        over-allocated denom row's match by exactly the overage so
        the order reconciles to the receipt total.
        """
        if not self._order_transactions:
            return self._order_total or 0

        from fam.utils.calculations import charge_to_method_amount

        # Vendor receipts (collapse multi-receipt-per-vendor sums).
        vendor_receipts: dict[int, int] = {}
        for t in self._order_transactions:
            vid = t.get('vendor_id')
            if vid is not None:
                vendor_receipts[vid] = vendor_receipts.get(vid, 0) + t['receipt_total']
        if not vendor_receipts:
            return self._order_total or 0

        single_vid = (next(iter(vendor_receipts.keys()))
                       if len(vendor_receipts) == 1 else None)
        bound_denom_alloc: dict[int, int] = {
            vid: 0 for vid in vendor_receipts
        }
        for r in self._payment_rows:
            mthd = r.get_selected_method()
            if not (mthd and mthd.get('denomination')
                     and mthd['denomination'] > 0):
                continue
            charge = r._get_active_charge()
            if charge <= 0:
                continue
            vid = r.get_bound_vendor_id()
            if vid is None and single_vid is not None:
                vid = single_vid
            if vid not in bound_denom_alloc:
                continue
            bound_denom_alloc[vid] += charge_to_method_amount(
                charge, mthd['match_percent']
            )
        non_denom_needed = sum(
            max(0, vendor_receipts[v] - bound_denom_alloc[v])
            for v in vendor_receipts
        )
        locked_bound_denom = sum(bound_denom_alloc.values())
        return locked_bound_denom + non_denom_needed

    def _remove_payment_row(self, row):
        # Phase 6 of the v1.9.9 rearchitecture: when the user clicks
        # the red X on the only row, reset it to default state instead
        # of silently no-op'ing.  Prior behavior left the volunteer
        # having to clear each field manually.
        if len(self._payment_rows) <= 1:
            row.reset_to_default()
            self._refresh_method_choices()
            self._update_summary()
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

        # ── Compute effective order total for the distribution math ──
        # When a denominated row is bound to a vendor whose receipt is
        # smaller than the row's method_amount, the customer is
        # *forfeiting* the overage of FAM match — the volunteer is told
        # the forfeit amount and accepts it.  Auto-Distribute should
        # then size non-denominated absorbers to fully cover the OTHER
        # vendors' receipts, NOT just (order_total − locked_total) which
        # would short-fund any vendor whose receipt is bigger than its
        # locked-denom share.
        #
        # Effective target = locked-denom method_amount  +  Σ (max(0, vendor_receipt
        #                                                          − vendor_denom_alloc))
        #
        # In the no-overage case this collapses to the order total
        # (existing behaviour preserved).  In the overage case it adds
        # back the headroom that other vendors still need; the engine's
        # denomination-forfeit path then reduces the over-allocated
        # denom row's match amount by exactly the overage so the order
        # reconciles.
        effective_order_total = self._order_total
        # Build per-vendor denom-alloc map from locked rows so we can
        # compute under-allocation without re-querying row state.
        if self._order_transactions:
            # Vendor receipts (collapse multi-receipt-per-vendor sums).
            vendor_receipts: dict[int, int] = {}
            for t in self._order_transactions:
                vid = t.get('vendor_id')
                if vid is not None:
                    vendor_receipts[vid] = vendor_receipts.get(vid, 0) + t['receipt_total']
            # Locked-denom method_amount per vendor.
            denom_alloc_per_vendor: dict[int, int] = {
                vid: 0 for vid in vendor_receipts
            }
            single_vid = (next(iter(vendor_receipts.keys()))
                           if len(vendor_receipts) == 1 else None)
            for d in row_descriptors:
                if not (d.get('denomination') and d['denomination'] > 0):
                    continue
                if d['current_charge'] <= 0:
                    continue  # auto-row, nothing locked yet
                # Resolve binding (mirror _push_row_limits)
                row = self._payment_rows[d['index']]
                bound_vid = row.get_bound_vendor_id()
                if bound_vid is None and single_vid is not None:
                    bound_vid = single_vid
                if bound_vid not in denom_alloc_per_vendor:
                    continue
                denom_alloc_per_vendor[bound_vid] += charge_to_method_amount(
                    d['current_charge'], d['match_pct']
                )
            non_denom_needed = sum(
                max(0, vendor_receipts[vid] - denom_alloc_per_vendor[vid])
                for vid in vendor_receipts
            )
            locked_denom_total = sum(denom_alloc_per_vendor.values())
            effective_order_total = locked_denom_total + non_denom_needed

        assignments = smart_auto_distribute(effective_order_total, row_descriptors)

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

        # Apply assignments to payment rows.
        #
        # v1.9.10 follow-up (2026-05-01): the row-level
        # ``set_max_charge`` from a prior ``_push_row_limits`` may
        # have a max that's TIGHTER than what the new auto-
        # distribute assignment wants — Qt's ``setMaximum``
        # silently clamps the value, so ``_set_active_charge`` can
        # appear to take but actually leave the row at the old
        # (now wrong) value.  Raise every row's ceiling to the
        # spinbox/stepper's natural max BEFORE applying
        # assignments; ``_update_summary``'s ``_push_row_limits``
        # at the bottom of this function then computes the
        # correct constraint.  Found by the admin state-machine
        # fuzzer (seed 4) where a re-auto-distribute with denom +
        # multi-Cash failed to apply because earlier maxes had
        # collapsed to 0.
        for row in self._payment_rows:
            try:
                row.set_max_charge(99999_99)  # ~$99,999.99
            except Exception:
                pass
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
                self._market_id, active_only=True,
                include_system=False,
            )
            if not methods:
                methods = get_all_payment_methods(
                    active_only=True, include_system=False)
        else:
            methods = get_all_payment_methods(
                active_only=True, include_system=False)

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
        """Update each row's method dropdown to reflect what's already
        selected on other rows.

        v1.9.9 rearchitecture: only **non-denominated** methods are
        deduplicated.  Denominated methods (Food Bucks, FMNP-as-payment)
        can appear on multiple rows because each row binds the
        instrument to a *different vendor* — e.g. "1 × Food Bucks →
        Vendor A" and "1 × Food Bucks → Vendor B".  Non-denominated
        methods (SNAP, Cash) are still one-row-per-method by design.

        The "+ Add Payment Method" button stays visible whenever any
        slot remains available — that's any unused non-denominated
        method, OR any denominated method (those never run out since
        you can keep adding new vendor bindings).
        """
        from fam.models.payment_method import (
            get_all_payment_methods, get_payment_methods_for_market,
        )
        if self._market_id:
            methods = get_payment_methods_for_market(
                self._market_id, active_only=True,
                include_system=False)
            if not methods:
                methods = get_all_payment_methods(
                    active_only=True, include_system=False)
        else:
            methods = get_all_payment_methods(
                active_only=True, include_system=False)

        # Partition methods by denomination.
        denom_method_ids = {
            m['id'] for m in methods
            if m.get('denomination') and m['denomination'] > 0
        }
        non_denom_method_count = len(methods) - len(denom_method_ids)

        # Only non-denominated methods get deduplicated across rows —
        # denominated methods are intentionally re-selectable.
        excluded_non_denom_ids = set()
        used_non_denom_ids = set()
        for row in self._payment_rows:
            mid = row.get_selected_method_id()
            if mid is not None and mid not in denom_method_ids:
                excluded_non_denom_ids.add(mid)
                used_non_denom_ids.add(mid)

        for row in self._payment_rows:
            row.set_excluded_methods(excluded_non_denom_ids)

        # Visibility for "+ Add Payment Method":
        # - Any denominated method present in this market means we can
        #   always add another vendor-bound row → always visible.
        # - Otherwise show only when at least one unused non-denominated
        #   method remains.
        if denom_method_ids:
            self.add_method_btn.setVisible(True)
        else:
            self.add_method_btn.setVisible(
                len(used_non_denom_ids) < non_denom_method_count
            )

    def _clear_payment_rows(self):
        for row in self._payment_rows:
            self.rows_layout.removeWidget(row)
            row.deleteLater()
        self._payment_rows.clear()

    # ------------------------------------------------------------------
    # Summary / breakdown
    # ------------------------------------------------------------------
    def _update_summary(self):
        # v2.0.1: re-entry guard.
        #
        # Running the engine on POST-write-back row values
        # produces divergent results that look like coherence
        # violations but are actually engine-design artefacts
        # (the engine is non-idempotent on its own outputs).
        # If a slot inside this method indirectly triggers
        # another _update_summary — e.g. a spinbox
        # ``valueChanged`` re-emitting from clamp — the second
        # pass produces different totals than the first, the
        # UI flickers, and the Layer 2A guard at confirm time
        # can refuse a perfectly valid order.  The guard
        # returns silently on re-entry: the inner call's
        # results are discarded; the outer call owns the
        # authoritative state.
        if getattr(self, '_in_update_summary', False):
            logger.debug(
                "_update_summary: re-entry detected — skipping "
                "inner call to preserve engine-output coherence")
            return
        self._in_update_summary = True
        try:
            self._update_summary_impl()
        finally:
            self._in_update_summary = False

    def _update_summary_impl(self):
        receipt_total = self._order_total

        # Clear the auditor's stashed-result cache up-front so any
        # early-return path (no entries, no result, etc.) doesn't leave
        # a stale ``_last_update_result`` from a prior cycle that the
        # auditor would treat as authoritative.  Re-set below if the
        # engine runs.
        self._last_update_entries = None
        self._last_update_result = None
        # Cleared so the trailing _push_row_limits' cap-aware floor
        # only applies when the engine has just declared
        # match_was_capped on this cycle.
        self._last_match_was_capped = False

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
        #
        # The cap is computed against the *effective* order total
        # (= self._order_total in the no-overage case; > self._order_total
        # when a bound denom row over-allocates its vendor).  This
        # allows non-denom absorbers to fully cover the un-overflowed
        # vendors during a denomination-overage scenario; the engine's
        # forfeit path then reduces the over-allocated denom row's
        # match so the order reconciles to the actual receipt total.
        effective_total_for_cap = self._compute_effective_order_total()
        entries = []

        # v1.9.10 onsite-finding fix: pre-sum the total denominated
        # method_amount BEFORE iterating, so non-denom row caps see
        # the full denom contribution regardless of visual row order.
        #
        # Bug: ``max_ma = effective_total - running_alloc`` is a
        # running-budget cap.  When a non-denom row was iterated
        # BEFORE a denom row (e.g. user typed SNAP $106.80 first,
        # then added an FB row on Haffey), the non-denom cap saw
        # ``running_alloc=0`` and let SNAP take the full
        # ``effective_total``.  The trailing FB row then pushed the
        # allocation over the receipt total by exactly the denom
        # method_amount, producing "Allocated $224.80 / Receipt
        # $211.91, off by $12.89" with multiple negative per-vendor
        # remainders.  Save+resume masked the bug because
        # ``_group_saved_line_items_for_restore`` returns denom rows
        # first by design — but only after a round-trip, not on
        # first input.
        #
        # Pre-summing keeps the row iteration in visual order (so
        # downstream alignment with ``valid_rows`` and
        # ``forfeit_items`` is preserved) while making the
        # non-denom cap order-independent.
        total_denom_alloc = 0
        for row in self._payment_rows:
            data = row.get_data()
            if not data:
                continue
            method = row.get_selected_method()
            if (method and method.get('denomination')
                    and method['denomination'] > 0):
                total_denom_alloc += data['method_amount']

        non_denom_running = 0
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
                    max_ma = max(
                        0,
                        effective_total_for_cap
                        - total_denom_alloc
                        - non_denom_running,
                    )
                    if ma > max_ma:
                        ma = max_ma
                    non_denom_running += ma
                # Capture the row's denomination so the engine's cap
                # logic can keep customer_charged FIXED on denom rows
                # (v1.9.10 onsite-finding fix — Layer 2A was blocking
                # confirms because the proportional cap inflated denom
                # customer to non-denom-multiple values).
                entries.append({
                    'method_amount': ma,
                    'match_percent': data['match_percent'],
                    'method_name': data['method_name_snapshot'],
                    'denomination': (method.get('denomination')
                                      if method else None),
                })

        if entries:
            calc_entries = [
                {'method_amount': e['method_amount'],
                 'match_percent': e['match_percent'],
                 'denomination': e.get('denomination')}
                for e in entries
            ]
            result = calculate_payment_breakdown(
                receipt_total, calc_entries, match_limit=self._match_limit
            )
            # v1.9.10 follow-up (2026-05-01): stash a reference to
            # ``result`` and the entries that produced it so the
            # auditor can verify "screen state is consistent with the
            # engine output that DROVE this update_summary cycle"
            # rather than re-running the engine on the post-write-
            # back spinbox values (which is not idempotent under
            # cap-aware paths — feeding inflated customer_charged
            # back as input compounds the inflation, producing
            # divergent results that look like coherence violations
            # but are actually engine-design artifacts).  Found by
            # admin fuzz seeds 7/14/19/20/101/102/104/105.
            self._last_update_entries = entries
            self._last_update_result = result
            # Stash the cap-active flag for the trailing
            # _push_row_limits (see its current_charge floor).
            self._last_match_was_capped = bool(
                result.get('match_was_capped'))
            allocated = result['allocated_total']
            remaining = result['allocation_remaining']
            fam_match = result['fam_subsidy_total']

            self.summary_row.update_card("allocated", format_dollars(allocated))
            self.summary_row.update_card("remaining", format_dollars(remaining))
            self.summary_row.update_card("customer_pays", format_dollars(result['customer_total_paid']))
            self.summary_row.update_card("fam_match", format_dollars(fam_match))

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

            # v1.9.10 follow-up (2026-05-01): also detect PER-VENDOR
            # over-allocation even when the order total balances.
            # Scenario: user binds a $10 Food RX to a vendor whose
            # receipt is $5.14, then auto-distribute sizes Cash to
            # cover the OTHER vendors.  Order total balances but
            # vendor A is over-allocated by ($10 customer - $5.14
            # receipt) = $4.86 (plus any FAM match overage).  The
            # admin state-machine fuzzer found this scenario producing
            # an invisible per-vendor inconsistency that Layer 2C
            # would only catch at Confirm time.  Surfacing it here
            # ensures the screen never appears confirmable while
            # the math is broken.
            #
            # We take ``max(order_overage, per_vendor_overage)`` so
            # forfeit catches both the order-level overshoot AND
            # any per-vendor overage that's bigger.  This matches
            # the auditor's ``_recompute_engine_state``.
            per_vendor_overage_amt = 0
            if self._order_transactions:
                # Sum receipts per vendor FIRST (multi-transaction-
                # per-vendor case) so each vendor's overage is
                # counted ONCE.  Walking transactions and
                # accumulating gap per-row would double-count when
                # vendor V has 2 receipts.
                vendor_receipts_sum: dict = {}
                for t in self._order_transactions:
                    vid = t.get('vendor_id')
                    if vid is not None:
                        vendor_receipts_sum[vid] = (
                            vendor_receipts_sum.get(vid, 0)
                            + t['receipt_total'])
                vendor_alloc: dict = {}
                for row in self._payment_rows:
                    data = row.get_data()
                    if not data or data['method_amount'] <= 0:
                        continue
                    denom_v = data.get('denomination') or 0
                    if denom_v <= 0:
                        continue
                    vid = data.get('bound_vendor_id')
                    if vid is not None:
                        vendor_alloc[vid] = (
                            vendor_alloc.get(vid, 0)
                            + data['method_amount'])
                for vid, alloc in vendor_alloc.items():
                    receipt_sum = vendor_receipts_sum.get(vid, 0)
                    gap = alloc - receipt_sum
                    if gap > 0:
                        per_vendor_overage_amt += gap
            # Only swap to per-vendor when order-level shows nothing.
            # When BOTH exist, the order-level overage is always
            # >= the per-vendor sum (per-vendor gaps are a strict
            # subset of order-level over-allocation).  Legacy
            # screenshots and existing tests rely on the order-level
            # path being the single source of truth when it's
            # non-zero.
            if denom_overage_amt == 0 and per_vendor_overage_amt > 0:
                is_denom_overage = True
                denom_overage_amt = per_vendor_overage_amt

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
            #
            # v1.9.10 onsite-finding fix: previously this routine had
            # its OWN forfeit reduction loop that walked line items in
            # order and reduced match on the FIRST positive-match
            # row by the *order-level* overage.  That had two bugs:
            # (1) it wasn't vendor-aware, so the forfeit landed on
            # the wrong row when the over-allocated vendor's bound
            # row wasn't first; (2) it used order-level instead of
            # per-vendor overage, leaving a 1¢ drift on every
            # multi-vendor scenario where one vendor's overage
            # exceeded the order-level overage.  The screenshot bug
            # the user found (Juice Bar +$0.01 / Elfinwild -$0.01
            # in the Vendor Breakdown table) was the visible
            # manifestation.  Now we delegate to the same
            # ``_apply_denomination_forfeit`` the save path uses so
            # the breakdown display can never drift from what
            # ``_confirm_payment`` would commit.
            if is_denom_overage and denom_overage_amt > 0:
                # Build items in the shape ``_apply_denomination_forfeit``
                # expects (with ``bound_vendor_id``, ``denomination``,
                # etc).  Match the existing ``result.line_items`` order
                # by walking PaymentRow.get_data() in the same order
                # the engine saw them — that's how
                # ``_collect_line_items`` is built upstream.
                forfeit_items = []
                for row in self._payment_rows:
                    d = row.get_data()
                    if d and d['method_amount'] > 0:
                        forfeit_items.append(d)
                self._apply_denomination_forfeit(
                    result, forfeit_items, denom_overage_amt)
                fam_match = result['fam_subsidy_total']
                self.summary_row.update_card(
                    "fam_match", format_dollars(fam_match))
                # Forfeit may have given back match capacity to non-denom
                # rows (Pass 4) when cap was active, reducing customer
                # charges.  Re-write customer_pays card.
                self.summary_row.update_card(
                    "customer_pays",
                    format_dollars(result['customer_total_paid']))

            # Update row display values to reflect the final calculated
            # breakdown.  This MUST run AFTER ``_apply_denomination_forfeit``
            # so the row labels reflect the post-forfeit values (denom
            # match reduction, non-denom give-back from cap-aware Pass 4),
            # not the pre-forfeit engine output.
            #
            # CRITICAL — Layer 1 of the charge-integrity fix: the engine
            # may inflate ``customer_charged`` above what the user typed
            # (e.g. when the daily match cap forces the customer to cover
            # the gap a capped match no longer fills).  We MUST write the
            # engine's customer_charged back to the row's input field so
            # the volunteer sees the same number everywhere — spinbox,
            # row labels, summary cards, collect panel, confirm dialog,
            # and the saved DB row.
            #
            # Order within the loop: ``_set_active_charge`` first, then
            # ``set_display_values`` (NOT ``_recompute``).  ``_recompute``
            # would overwrite the post-cap labels with the spinbox's raw
            # ``charge × pct`` (uncapped), producing the v1.9.10
            # onsite-finding bug where the SNAP row Match/Total fields
            # displayed values that exceeded the order total after
            # Auto-Distribute.
            if result.get('line_items'):
                # CRITICAL: ``valid_rows`` MUST match the filter used
                # by ``_collect_line_items`` (method_amount > 0) so
                # the index mapping to ``result['line_items']`` is
                # correct.  Including rows with method_amount == 0
                # (e.g. an empty Food RX row sitting between two
                # active rows) shifts every subsequent row's
                # ``li`` lookup by 1 — write-back lands on the
                # wrong row, the labels go stale, and the auditor
                # flags U3 mismatches.  Found by the admin
                # state-machine fuzzer on 2026-05-01 (seed 5).
                # ``valid_rows`` MUST match the entries-build loop
                # at lines 1395-1425 EXACTLY — that loop appends an
                # entry for EVERY row with non-None ``data`` and a
                # selected method (regardless of method_amount).  A
                # zero-method row produces a (0, 0, 0) line item in
                # ``result['line_items']``.  Filtering them out here
                # shifts every subsequent row's ``li`` lookup by 1
                # — write-back lands on the wrong row, the labels
                # go stale, and (worst case) ``true_charge`` from
                # the zero-line falls through to clobber a
                # neighbouring active row's charge to 0.  Found by
                # the admin state-machine fuzzer (seed 4) on
                # 2026-05-01.
                # ``valid_rows`` MUST match the entries-build filter
                # exactly (``if data:``) so the index mapping to
                # ``result['line_items']`` is correct.
                valid_rows = []
                for r in self._payment_rows:
                    d = r.get_data()
                    if d:
                        valid_rows.append(r)
                # v1.9.10 follow-up (2026-05-01): the cap-aware
                # engine path INFLATES non-denom customer_charged
                # to absorb the denom-method shrinkage (e.g. Cash
                # $2.16 → $90.16 to compensate for FB $4 → $2.88
                # cap reduction).  The spinbox/stepper max from
                # the prior _push_row_limits is sized to the
                # PRE-engine remaining budget, so a naive
                # ``setValue(9016)`` would silently clamp back to
                # the old $2.16 max.  Raise every row's ceiling
                # before write-back; the trailing _push_row_limits
                # at the bottom of _update_summary recomputes the
                # correct constraint.  Found by admin fuzz seed 4
                # — Cash row spinbox stuck at $2.16 while engine
                # said $90.16.
                for r in valid_rows:
                    try:
                        r.set_max_charge(99999_99)
                    except Exception:
                        pass
                for i, row in enumerate(valid_rows):
                    if i < len(result['line_items']):
                        li = result['line_items'][i]
                        # v1.9.10 follow-up (2026-05-01): when Phase B
                        # forfeit reduced ``customer_charged`` on a denom
                        # row (because the customer over-handed-over
                        # physical scrip), the spinbox value
                        # (= physical scrip face value) MUST NOT be
                        # written back to the post-forfeit
                        # ``customer_charged`` — the stepper would
                        # truncate $7.86 to 0 units of a $10 token.
                        # The visible charge stays at the customer's
                        # physical handover; the engine's reduced
                        # ``customer_charged`` is the effective
                        # contribution post-forfeit.  Layer 2A
                        # accepts the gap because it equals
                        # ``customer_forfeit_cents``.
                        forfeit_cents = (
                            li.get('customer_forfeit_cents', 0) or 0)
                        true_charge = (
                            li['customer_charged'] + forfeit_cents)
                        if true_charge != row._get_active_charge():
                            # Block the row's `changed` signal so this
                            # write-back doesn't cause a re-entry into
                            # _update_summary while we're still inside it.
                            row.blockSignals(True)
                            try:
                                row._set_active_charge(true_charge)
                            finally:
                                row.blockSignals(False)
                        # Per-row label policy under customer-side forfeit:
                        #
                        #   * No forfeit (forfeit_cents == 0): row labels
                        #     mirror the engine's post-cap match/method —
                        #     this keeps the row consistent with what gets
                        #     saved.
                        #   * Customer-side forfeit (forfeit_cents > 0):
                        #     row labels show pre-forfeit (uncapped) values
                        #     via ``_recompute`` so the per-row visible
                        #     invariant ``charge + match = total`` still
                        #     holds.  The post-forfeit reduction is shown
                        #     in the summary cards and the
                        #     "Collect from Customer" panel — the user
                        #     sees the customer's loss at the order level
                        #     without confusing the row's local math.
                        if (forfeit_cents == 0
                                and row._get_active_charge() == true_charge):
                            row.set_display_values(
                                li['match_amount'], li['method_amount'])
                        else:
                            row._recompute()

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

        # ── Step 4: refresh the per-vendor breakdown table (v1.9.9+) ──
        # Always called, regardless of whether any rows have data, so
        # the Remaining column tracks back to each vendor's full receipt
        # when payments are cleared.  Pass the engine's post-rec
        # line_items when available so the breakdown reflects what the
        # save path will commit, not the pre-rec nominal values (which
        # produced a stale $0.01 last-vendor remainder before this fix).
        engine_line_items = None
        try:
            engine_line_items = result.get('line_items')  # noqa: F821
        except (NameError, UnboundLocalError, AttributeError):
            engine_line_items = None
        self._refresh_vendor_breakdown(
            engine_line_items=engine_line_items)

        # ── Step 5: Recompute row input ceilings AFTER write-back ──
        # The cap-aware engine write-back loop above raises every row's
        # ``set_max_charge`` to $99,999.99 so that inflated
        # ``customer_charged`` values can be written into the spinbox
        # without silent clamping from the prior ``_push_row_limits``.
        # Restore the proper per-vendor / order-level constraints now
        # that the new charges are settled — without this, the row
        # ceilings remain at $99,999.99 indefinitely (a regression
        # for the per-vendor stepper cap and similar guards).  Note:
        # the cap-aware Common Path's customer_charged INFLATION
        # (e.g. Cash $2.16 → $90.16) WILL be silently clamped back
        # by this trailing call when the budget-based max is below
        # the inflated value.  That clamping is the EXPECTED
        # behaviour for tests like
        # ``test_lifecycle_seven_steps_all_consistent`` (Cash $60 →
        # $50 when receipt is $50).  The admin-fuzz audit's U1/U3/U6
        # checks tolerate this divergence via ``cap_active``
        # short-circuit since the engine is non-idempotent under cap.
        self._push_row_limits()

    def _push_row_limits(self):
        """Cap each row's input to prevent over-allocation.

        Two-tier capping (v1.9.9+):

        * **Per-vendor cap** — denominated rows bound to a specific
          vendor are capped by that vendor's remaining receipt
          balance (= vendor_receipt − sum of OTHER denom rows bound
          to the same vendor).  This prevents the volunteer from
          incrementing a stepper past what fits on the bound
          vendor's transaction, even if the order as a whole has
          room elsewhere.
        * **Order-level cap** — non-denominated rows and unbound
          denom rows continue to use the original order-wide
          remaining balance.

        Block signals on all rows first to prevent cascading updates —
        setMaximum() clamps current values which would fire valueChanged
        and re-enter _update_summary in a loop.

        When a daily match limit is active, the max charge must account for
        the reduced effective match.  Without this, a 100% match method would
        cap the charge at ``remaining / 2`` even though the customer must pay
        ``remaining - available_match`` when the cap kicks in.
        """
        from fam.utils.calculations import charge_to_method_amount

        # Pre-compute per-vendor receipt totals + the implicit-binding
        # vendor for single-vendor orders (where the vendor combo is
        # hidden and bound_vendor_id is None by design).
        vendor_receipts: dict[int, int] = {}
        for t in self._order_transactions:
            vid = t.get('vendor_id')
            if vid is not None:
                vendor_receipts[vid] = vendor_receipts.get(vid, 0) + t['receipt_total']
        single_vendor_id = (next(iter(vendor_receipts.keys()))
                             if len(vendor_receipts) == 1 else None)

        def _resolve_bound_vid(row):
            """Return the effective bound vendor for this row's
            denominated payment, or None if not denominated."""
            mthd = row.get_selected_method()
            if not (mthd and mthd.get('denomination')
                     and mthd['denomination'] > 0):
                return None
            vid = row.get_bound_vendor_id()
            if vid is None and single_vendor_id is not None:
                vid = single_vendor_id
            return vid

        # Compute the *effective* order total for non-denom row caps.
        # Mirror of the Auto-Distribute fix: when a bound denom row
        # over-allocates its vendor's receipt the customer is forfeiting
        # FAM match, but other vendors still need to be fully covered.
        # Sum (max(0, vendor_receipt − bound_denom_alloc)) gives the
        # under-allocation that non-denom rows must fill; adding the
        # locked bound-denom total yields the effective order total.
        # No-overage case collapses to self._order_total exactly, so
        # the existing v1.9.1 cap-aware behaviour is preserved bit-
        # for-bit.
        bound_denom_alloc: dict[int, int] = {
            vid: 0 for vid in vendor_receipts
        }
        for r in self._payment_rows:
            r_method = r.get_selected_method()
            if not r_method:
                continue
            if not (r_method.get('denomination')
                     and r_method['denomination'] > 0):
                continue
            r_bound = _resolve_bound_vid(r)
            if r_bound not in bound_denom_alloc:
                continue
            r_charge = r._get_active_charge()
            if r_charge <= 0:
                continue
            bound_denom_alloc[r_bound] += charge_to_method_amount(
                r_charge, r_method['match_percent']
            )
        if vendor_receipts:
            non_denom_needed = sum(
                max(0, vendor_receipts[v] - bound_denom_alloc[v])
                for v in vendor_receipts
            )
            locked_bound_denom = sum(bound_denom_alloc.values())
            effective_order_total = locked_bound_denom + non_denom_needed
        else:
            effective_order_total = self._order_total

        # Block signals on all rows to prevent cascade
        for row in self._payment_rows:
            row.blockSignals(True)

        try:
            for i, row in enumerate(self._payment_rows):
                method = row.get_selected_method()
                if not method:
                    continue
                denom = method.get('denomination')
                is_denominated = bool(denom and denom > 0)
                this_bound_vid = _resolve_bound_vid(row) if is_denominated else None

                # Sum method_amount from all OTHER rows (integer cents)
                # — used for the order-level cap branch + match-limit
                # accounting (the cap is per-customer, not per-vendor,
                # so other_match always sums the whole order).
                other_total = 0
                other_match = 0          # match consumed by other rows
                # Per-vendor accumulator: only counts OTHER denom rows
                # bound to the SAME vendor as this row.
                other_to_same_vendor = 0
                # Order-level consumption accumulator (v1.9.9 onsite
                # fix): like other_total, but each *bound denom* row's
                # contribution is capped at its bound vendor's receipt.
                # Any overage above that is FAM forfeit, NOT real
                # order-capacity consumption — without this cap, a
                # bound denom row at vendor A appears to "consume"
                # capacity that's actually being forfeited at vendor B,
                # which clamps A's max below its current charge and
                # silently lowers the user-set value via QSpinBox.
                # setMaximum.  See test_multi_vendor_denom_overage.py.
                other_order_consumption = 0
                for j, r in enumerate(self._payment_rows):
                    if j == i or not r.has_method_selected():
                        continue
                    other_method = r.get_selected_method()
                    if not other_method:
                        continue
                    other_charge = r._get_active_charge()
                    other_ma = charge_to_method_amount(
                        other_charge,
                        other_method['match_percent']
                    )
                    other_total += other_ma
                    other_match += other_ma - other_charge
                    # Per-vendor pre-claim:
                    #   - Other DENOM rows bound to the SAME vendor
                    #     always pre-claim that vendor's capacity.
                    #   - In SINGLE-VENDOR orders, non-denom rows
                    #     also allocate to the only vendor, so they
                    #     pre-claim too.  In multi-vendor orders,
                    #     non-denom rows distribute across the OTHER
                    #     vendors' remaining capacity, not this row's
                    #     bound vendor — they don't pre-claim here.
                    other_denom = other_method.get('denomination')
                    is_single_vendor = (len(vendor_receipts) == 1)
                    if other_denom and other_denom > 0:
                        other_bound = _resolve_bound_vid(r)
                        if (this_bound_vid is not None
                                and other_bound == this_bound_vid):
                            other_to_same_vendor += other_ma
                        # Bound denom row: cap order-level
                        # consumption at the bound vendor's receipt
                        # — overage is FAM forfeit, not consumption.
                        if other_bound and other_bound in vendor_receipts:
                            other_order_consumption += min(
                                other_ma,
                                vendor_receipts[other_bound])
                        else:
                            # Unbound denom (single-vendor mode or
                            # vendor not on order) — count full so
                            # we don't under-cap by accident.
                            other_order_consumption += other_ma
                    else:
                        # Non-denom row.  In single-vendor orders
                        # it allocates to the same (only) vendor as
                        # this bound row, so it pre-claims that
                        # vendor's capacity.
                        if is_single_vendor and this_bound_vid is not None:
                            other_to_same_vendor += other_ma
                        # Non-denom rows have no forfeit concept —
                        # their full method_amount consumes order
                        # capacity.
                        other_order_consumption += other_ma

                # Cap selection — for denominated rows bound to a
                # vendor we use the TIGHTER of the per-vendor and the
                # order-level caps:
                #
                #   per_vendor_remaining = vendor_receipt
                #                          − other denom on same vendor
                #   order_remaining      = order_total − sum of other rows
                #
                # Per-vendor catches "you can't put 5 Food Bucks on a
                # $20 vendor".  Order-level catches "you can't put
                # 5 Food Bucks on a $20 vendor when SNAP already
                # claims $80 of the $100 order".  Both are real
                # constraints; the binding row must respect both.
                #
                # For single-vendor orders the two collapse to the same
                # value, so the existing v1.9.1 cap-aware behaviour is
                # preserved bit-for-bit.
                #
                # For *non-denominated* rows in a multi-vendor order
                # with a denomination overage we substitute the
                # effective order total (= locked-bound-denom + sum
                # of per-vendor under-allocation) for self._order_total
                # so SNAP/Cash can fully cover the un-overflowed
                # vendors.  No-overage cases preserve the legacy cap.
                order_remaining = max(0, effective_order_total - other_total)
                if (this_bound_vid is not None
                        and this_bound_vid in vendor_receipts):
                    vendor_receipt = vendor_receipts[this_bound_vid]
                    per_vendor_remaining = max(
                        0, vendor_receipt - other_to_same_vendor)
                    # v1.9.10 onsite-finding fix #2: the per-vendor
                    # cap is the only meaningful constraint for
                    # bound denom rows.  Earlier versions also
                    # min'd against ``legacy_order_remaining``
                    # (= self._order_total − other_total or
                    # − other_order_consumption) on the theory that
                    # "the bound row can never push past the actual
                    # order total".  But in v1.9.10's vendor-binding
                    # architecture, a bound denom row only allocates
                    # to its bound vendor.  Non-denom rows distribute
                    # proportionally across the *other* vendors'
                    # remaining capacity.  There is no order-level
                    # competition between this row and SNAP/Cash on
                    # this row's own vendor — per-vendor is the
                    # canonical (and only) constraint.
                    #
                    # Keeping the legacy floor caused another silent-
                    # clamp regression: when a non-denom row's
                    # *uncapped* method amount exceeded the order
                    # total (e.g. Auto-Distribute's cap-deficit
                    # inflation pushing SNAP to a high charge, or
                    # the user typing a large SNAP amount manually),
                    # ``legacy_order_remaining`` clamped to 0 and
                    # ``QSpinBox.setMaximum(0)`` silently zeroed the
                    # user's locked denom units.  Per-vendor alone
                    # is sufficient and correct under vendor binding.
                    remaining = per_vendor_remaining
                else:
                    remaining = order_remaining

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

                    # v1.9.10 onsite-finding fix: when denom uncapped
                    # match alone exceeds the daily cap, the engine's
                    # cap-fallback shrinks denom method (denom customer
                    # is fixed at ``unit_count × denom`` and absorbs no
                    # cap deficit; their match drops, and the lost
                    # method capacity flows to non-denom rows).  The
                    # ``other_match >= self._match_limit`` branch
                    # raises the non-denom row's max_charge ceiling to
                    # ``effective_total − sum(OTHER row's fixed
                    # customer-portion)`` so the engine's true output
                    # fits inside the spinbox.  Without this bump the
                    # cap-write-back path silently clamps the spinbox
                    # below the engine's value, Layer 2A then blocks
                    # confirm with "row mismatch", and Auto-Distribute
                    # can't recover (the user reported this as a
                    # "bricked transaction").
                    if other_match >= self._match_limit:
                        other_fixed_customer = 0
                        for j, r in enumerate(self._payment_rows):
                            if j == i or not r.has_method_selected():
                                continue
                            rm = r.get_selected_method()
                            if not rm:
                                continue
                            rch = r._get_active_charge()
                            if rch <= 0:
                                continue
                            rdenom = rm.get('denomination')
                            if rdenom and rdenom > 0:
                                # Denom: customer is FIXED at the
                                # spinbox value (= unit_count × denom).
                                other_fixed_customer += rch
                            else:
                                # Non-denom OTHER rows: their full
                                # method consumes capacity.
                                other_fixed_customer += (
                                    charge_to_method_amount(
                                        rch, rm['match_percent']))
                        max_charge_cap_aware = max(
                            0,
                            effective_order_total
                            - other_fixed_customer)
                        max_charge = max(
                            max_charge, max_charge_cap_aware)
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

                # v1.9.10 follow-up (2026-05-01, admin fuzz seed 4):
                # When the daily match cap activated this cycle, the
                # engine has authoritative final values for all rows
                # (Common Path inflated non-denom customer; FALLBACK
                # path keeps denom customer fixed but reduces match
                # below uncapped).  Either way, the budget-based max
                # computed above can be tighter than what the engine
                # accepted — clamping the spinbox/stepper down would
                # silently undo the engine's output, breaking V5
                # (charge + match_label = total_label).  Floor
                # max_charge at the row's current charge so the
                # engine's value survives the trailing ceiling
                # refresh.  ONLY applies when ``_last_match_was_capped``
                # is True (i.e. the engine produced these values
                # this cycle) — this preserves the leading
                # ``_push_row_limits`` semantics for tests like
                # ``test_per_vendor_stepper_caps_at_vendor_receipt``
                # (no daily cap → floor not applied → leading clamp
                # tightens to vendor budget) and
                # ``test_lifecycle_seven_steps_all_consistent``
                # (no daily cap → typed-too-high clamps down).
                if getattr(self, '_last_match_was_capped', False):
                    current_charge = row._get_active_charge()
                    if current_charge > max_charge:
                        max_charge = current_charge

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
        # Use the effective order total so non-denom absorbers can
        # fully cover un-overflowed vendors during a denomination-
        # overage scenario.  No-overage case collapses to the actual
        # order total, preserving prior behaviour.
        effective_total = self._compute_effective_order_total()

        # v1.9.10 onsite-finding fix: pre-sum the total denominated
        # method_amount BEFORE iterating, so non-denom row caps see
        # the full denom contribution regardless of visual row order.
        # See ``_update_summary`` for the full bug rationale — same
        # cap-budget order-dependence applied to the save/confirm
        # path.  Without this fix, a SNAP-first / FB-second visual
        # row order produces an over-allocated ``items`` list that
        # violates Layer 2C per-vendor reconciliation at confirm
        # time, even though save+resume would round-trip cleanly.
        total_denom_alloc = 0
        for row in self._payment_rows:
            data = row.get_data()
            if not data or data['method_amount'] <= 0:
                continue
            method = row.get_selected_method()
            if (method and method.get('denomination')
                    and method['denomination'] > 0):
                total_denom_alloc += data['method_amount']

        non_denom_running = 0
        for row in self._payment_rows:
            data = row.get_data()
            if data and data['method_amount'] > 0:
                # Only cap non-denominated rows — denominated rows need
                # their overage to flow through so forfeit detection and
                # _apply_denomination_forfeit() work correctly.
                method = row.get_selected_method()
                is_denom = method and method.get('denomination') and method['denomination'] > 0
                if not is_denom:
                    max_ma = max(
                        0,
                        effective_total
                        - total_denom_alloc
                        - non_denom_running,
                    )
                    if data['method_amount'] > max_ma:
                        data['method_amount'] = max_ma
                    non_denom_running += data['method_amount']
                items.append(data)
        return items

    def _resolve_engine_state(self, items):
        """Thin wrapper around the canonical
        ``resolve_payment_state`` engine (Phase 6 consolidation).

        Replaces the previous local implementation that duplicated
        engine + forfeit + items-sync logic.  See
        ``fam/utils/calculations.py::resolve_payment_state`` for the
        full contract.

        ``apply_denomination_forfeit_fn`` is bound to this screen's
        own per-vendor-aware forfeit method so the canonical engine
        gets vendor-binding info it doesn't otherwise know about.
        """
        from fam.utils.calculations import resolve_payment_state

        receipt_total = self._order_total
        if not items or receipt_total <= 0:
            return None

        return resolve_payment_state(
            receipt_total, items,
            match_limit=self._match_limit,
            apply_denomination_forfeit_fn=(
                self._apply_denomination_forfeit),
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _save_draft(self):
        if not self._order_transactions:
            return
        try:
            items = self._collect_line_items()
            if items:
                # Run engine + forfeit + sync so saved DB rows reflect
                # the cap-aware end state (denom customer fixed at
                # unit_count × denom, non-denom absorbing inflation).
                # Without this the save path's own cap fallback hits
                # the same non-denom-multiple-customer bug the engine
                # fix solved, and the saved DB has inflated denom
                # customers that get loaded back on resume.
                self._resolve_engine_state(items)
                self._distribute_and_save_payments(items, self._order_total)

            self.success_frame.setVisible(True)
            self.success_msg.setText("Draft saved successfully.")

            # ── Post-draft navigation: auto-return to Receipt Intake ──
            # CANONICAL UX (v1.9.10+, 2026-05-01): mirrors the
            # post-confirm flow — see ``_confirm_payment``'s
            # post-confirm comment for the full rationale.  Saving
            # a draft is almost always followed by attending to
            # another customer; bouncing the volunteer back to
            # Receipt Intake (where they need to be next) is the
            # right default.  No modal popup.
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

        v1.9.9 fix — vendor-aware attribution
        --------------------------------------
        The original implementation reduced match on the FIRST line item
        with positive match, regardless of which vendor was over-allocated.
        On multi-vendor orders that mis-attributed the forfeit to a vendor
        with headroom (e.g. Hello Hummus FB went $24 → $22) instead of the
        actually-over-allocated vendor (e.g. KizzleFoods FB stayed at $12
        against a $10 receipt).  Downstream the per-vendor SNAP allocation
        then split incorrectly and left a third vendor under-allocated —
        the 2026-04 onsite surfaced this as a hung Auto-Distribute.

        New algorithm:
          1. Compute per-vendor allocation by walking the line items'
             ``bound_vendor_id`` (with single_vendor_id fallback).
          2. For each over-allocated vendor, reduce the match on the
             denominated line item bound to THAT vendor by exactly
             that vendor's overage.
          3. If any overage remains after the per-vendor pass (rare —
             would indicate inconsistent state), fall back to the legacy
             first-with-match algorithm so the totals still reconcile.

        The fix preserves customer_charged exactly (the customer paid in
        physical instruments — that count never changes); only the FAM
        match portion is reduced, and only on the line item that
        actually caused the overage.
        """
        from fam.utils.calculations import charge_to_method_amount

        # Map of vendor_id → receipt total, plus the implicit binding
        # for single-vendor orders (where bound_vendor_id is None by
        # design — the AdjustmentDialog single_vendor_mode case).
        vendor_receipts: dict[int, int] = {}
        for t in self._order_transactions:
            vid = t.get('vendor_id')
            if vid is not None:
                vendor_receipts[vid] = (
                    vendor_receipts.get(vid, 0) + t['receipt_total'])
        single_vendor_id = (
            next(iter(vendor_receipts.keys()))
            if len(vendor_receipts) == 1 else None)

        # Per-vendor allocation from the CURRENT line items, plus a
        # reverse map (item index → bound vendor) so we can target
        # forfeit reductions surgically.  Only denominated rows can
        # carry forfeit; non-denom rows are excluded.
        vendor_alloc: dict[int, int] = {vid: 0 for vid in vendor_receipts}
        item_vendor: dict[int, int] = {}
        for i, li in enumerate(result['line_items']):
            if li['method_amount'] <= 0:
                continue
            # Bounds-check: line_items can be longer than items when a
            # row was added mid-update (e.g. an empty Cash row
            # contributes a 0-method line_item but is filtered out of
            # items via ``method_amount > 0``).
            if i >= len(items):
                continue
            item = items[i]
            denom = item.get('denomination')
            if not (denom and denom > 0):
                continue
            bound_vid = item.get('bound_vendor_id')
            if bound_vid is None and single_vendor_id is not None:
                bound_vid = single_vendor_id
            if bound_vid in vendor_receipts:
                vendor_alloc[bound_vid] += li['method_amount']
                item_vendor[i] = bound_vid

        # Vendors whose denom allocation exceeds their receipt — those
        # are the ones whose forfeit needs to be applied.
        over_per_vendor = {
            vid: vendor_alloc[vid] - vendor_receipts[vid]
            for vid in vendor_receipts
            if vendor_alloc[vid] > vendor_receipts[vid]
        }

        # Track total reduction for the post-pass penny-reconciliation
        # step (see Pass 3 below).  In v1.9.10 we no longer cap the
        # per-vendor reduction by ``remaining_overage`` — see the
        # comment on Pass 1.
        total_reduction = 0
        # v1.9.10 follow-up (2026-05-01): track Phase A (match)
        # and Phase B (customer) reductions SEPARATELY.  The
        # totals on ``result`` adjust differently: ``method`` is
        # reduced by both, ``fam_subsidy_total`` only by Phase A,
        # ``customer_total_paid`` only by Phase B.  Without this
        # split, ``audit_screen`` flagged E6/E7 mismatches
        # (fam_subsidy / customer_total drifting from
        # Σ(match) / Σ(customer) post-forfeit).
        total_match_reduction = 0
        total_customer_reduction = 0
        remaining_overage = overage  # legacy meaning, kept for fallback

        # ── Pass 1: vendor-aware forfeit ─────────────────────────────
        # For each over-allocated vendor, reduce match on the line
        # items bound to that vendor by exactly that vendor's
        # **per-vendor** overage.
        #
        # v1.9.10 onsite-finding fix: previously this reduced by
        # ``min(vendor_overage, remaining_overage)``, capping at the
        # *order-level* overage.  When a single vendor's per-vendor
        # overage exceeds the order-level overage (because OTHER
        # vendors' non-denom rows had headroom that hid part of the
        # over-allocation), the cap stopped the reduction 1¢ short.
        # The bound denom row ended 1¢ over its vendor's receipt and
        # the proportional non-denom split landed the 1¢ shortfall
        # on a different vendor.  Both transactions ended up off by
        # ±1¢ — invisible to Layer 2C's ±1¢ tolerance but a real I2
        # violation in the saved DB rows.
        #
        # Now: each over-allocated vendor's bound denom row is
        # reduced by exactly its vendor_overage so per-vendor
        # reconciliation is exact.  The order-level total may now
        # be UNDER receipt by a few cents (because we reduced more
        # than the order-level overage); Pass 3 absorbs that
        # residue into a non-denom match line so the order also
        # reconciles to ±0¢.
        #
        # v1.9.10 follow-up (2026-05-01): two-phase reduction.
        # Phase A reduces ``match_amount`` (the original behaviour —
        # FAM contributes less and the customer's physical scrip
        # still covers receipt within match headroom).  Phase B
        # kicks in when a vendor's overage exceeds the row's match
        # amount (e.g. customer hands $10 Food RX to a $7.86 receipt
        # vendor — overage $12.14 vs match $10 → match insufficient).
        # Phase B reduces ``customer_charged`` AND tags the line
        # item with ``customer_forfeit_cents`` so callers know how
        # much of the customer's physical scrip didn't translate to
        # vendor reimbursement.  Layer 2A reads ``customer_forfeit_cents``
        # to allow the spinbox-vs-engine mismatch on denom rows
        # whose customer-side forfeit is fully accounted for.
        for vid, vendor_overage in over_per_vendor.items():
            v_remain = vendor_overage
            if v_remain <= 0:
                continue
            for i, li in enumerate(result['line_items']):
                if v_remain <= 0:
                    break
                if item_vendor.get(i) != vid:
                    continue
                # Phase A: reduce match
                if li['match_amount'] > 0:
                    match_red = min(v_remain, li['match_amount'])
                    li['match_amount'] -= match_red
                    li['method_amount'] -= match_red
                    items[i]['method_amount'] = li['method_amount']
                    items[i]['match_amount'] = li['match_amount']
                    v_remain -= match_red
                    total_reduction += match_red
                    total_match_reduction += match_red
                    remaining_overage = max(
                        0, remaining_overage - match_red)
                # Phase B: reduce customer_charged when match
                # exhausted but vendor still over-allocated.
                # This is "customer-side forfeit" — the customer
                # handed more denomination than the receipt
                # required.  customer_charged in the saved row
                # represents the EFFECTIVE customer contribution
                # to the order; the gap (``customer_forfeit_cents``)
                # is recorded for Layer 2A and receipt rendering.
                if v_remain > 0 and li['customer_charged'] > 0:
                    cust_red = min(v_remain, li['customer_charged'])
                    li['customer_charged'] -= cust_red
                    li['method_amount'] -= cust_red
                    li['customer_forfeit_cents'] = (
                        li.get('customer_forfeit_cents', 0)
                        + cust_red)
                    items[i]['method_amount'] = li['method_amount']
                    items[i]['customer_charged'] = (
                        li['customer_charged'])
                    items[i]['customer_forfeit_cents'] = (
                        items[i].get('customer_forfeit_cents', 0)
                        + cust_red)
                    v_remain -= cust_red
                    total_reduction += cust_red
                    total_customer_reduction += cust_red
                    remaining_overage = max(
                        0, remaining_overage - cust_red)

        # ── Pass 2: legacy fall-back ─────────────────────────────────
        # Any overage left after the vendor-aware pass means the
        # vendor map didn't cover the full forfeit (rare — could
        # happen on a single-vendor order where ``vendor_receipts``
        # is empty because no transactions are loaded yet, or on
        # numerical drift).  Walk all line items in order and reduce
        # match wherever it's positive — same algorithm as the
        # pre-v1.9.9 code so the totals still reconcile cleanly.
        if remaining_overage > 0:
            for i, li in enumerate(result['line_items']):
                if remaining_overage <= 0:
                    break
                # Bounds-check (same rationale as Pass 1).
                if i >= len(items):
                    continue
                if li['match_amount'] > 0:
                    reduction = min(remaining_overage, li['match_amount'])
                    li['match_amount'] = li['match_amount'] - reduction
                    li['method_amount'] = li['method_amount'] - reduction
                    items[i]['method_amount'] = li['method_amount']
                    items[i]['match_amount'] = li['match_amount']
                    remaining_overage -= reduction
                    total_reduction += reduction
                    total_match_reduction += reduction

        # ── Pass 3: residual penny reconciliation ───────────────────
        # v1.9.10 onsite-finding fix.  Pass 1 reduces by per-vendor
        # overage exactly so each over-allocated vendor's bound denom
        # row matches its receipt.  But ``total_reduction`` may exceed
        # the order-level ``overage`` when a single vendor's overage
        # is bigger than the order-level overage (the rest of the
        # order had non-denom headroom that masked it).  In that
        # case the order is now UNDER the receipt total; we add the
        # residue back to the largest non-denom match line so the
        # order also ties out to ±0¢.
        residue = total_reduction - overage
        if residue > 0:
            best_idx = None
            best_method = -1
            for i, li in enumerate(result['line_items']):
                # Bounds-check: ``result['line_items']`` and ``items``
                # may have different lengths when a row was added
                # mid-update_summary (e.g. user clicks "+ Add Payment
                # Method" → an empty Cash row appears as a 0-method
                # line_item from the engine while ``items`` was built
                # by ``_collect_line_items`` filtering on
                # ``method_amount > 0``).  Accessing ``items[i]``
                # without the bounds check raised
                # ``IndexError: list index out of range`` on the
                # 2026-04-30 onsite.
                if i >= len(items):
                    continue
                item = items[i]
                denom = item.get('denomination')
                if denom and denom > 0:
                    continue  # Skip denom rows (they'd reintroduce overage)
                pct = item.get('match_percent_snapshot') or item.get('match_percent') or 0
                if pct <= 0:
                    continue  # Skip 0%-match rows (no match to grow)
                if li['method_amount'] > best_method:
                    best_method = li['method_amount']
                    best_idx = i
            if best_idx is not None and best_idx < len(items):
                target = result['line_items'][best_idx]
                target['method_amount'] += residue
                target['match_amount'] += residue
                items[best_idx]['method_amount'] = target['method_amount']
                items[best_idx]['match_amount'] = target['match_amount']
                # The order has reconciled — total_reduction is now
                # effectively ``overage``.
                total_reduction -= residue
                # The residue add-back is a MATCH increase on a
                # non-denom row, so it cancels match-side
                # reductions (not customer-side).
                total_match_reduction -= residue
            # If no eligible non-denom matched row exists, the
            # residue stays unabsorbed and the caller's per-txn
            # guard will surface it.  Real-world this can only
            # happen on all-denom-or-Cash orders, which are
            # already pathological.

        # Update result totals.  ``method_amount`` (= allocated)
        # changes by the full ``total_reduction`` since both
        # phases reduce method.  ``fam_subsidy_total`` changes by
        # the Phase A sum; ``customer_total_paid`` changes by the
        # Phase B sum.  Pre-fix this conflated all three —
        # ``audit_screen`` flagged the resulting E6/E7 drift on the
        # 2026-05-01 multi-denom-overage scenario.
        #
        # Defensive ``.get(..., 0)``: some legacy test fixtures
        # construct minimal ``result`` dicts without the totals
        # keys.  Skip the update for missing keys rather than
        # crash — production callers always have all three.
        if 'allocated_total' in result:
            result['allocated_total'] = (
                result['allocated_total'] - total_reduction)
        if 'fam_subsidy_total' in result:
            result['fam_subsidy_total'] = (
                result['fam_subsidy_total'] - total_match_reduction)
        if 'customer_total_paid' in result:
            result['customer_total_paid'] = (
                result['customer_total_paid'] - total_customer_reduction)

        # ── Pass 4: cap-aware match give-back ───────────────────────
        # v1.9.10 onsite-finding fix.  When the daily match cap was
        # active, the engine had already squeezed non-denom match
        # below its uncapped value (e.g. SNAP uncapped $122.75 →
        # capped $66 to fit cap=$100 alongside denom $34).  Pass 1's
        # denom forfeit just freed some of the cap budget by reducing
        # denom match (e.g. FB Healthy $14 → $11.30, freeing $2.70).
        # Without this pass, that freed budget evaporates: FAM Match
        # drops below the cap (e.g. $100 → $97.30) for no reason
        # other than the order had a denom overage.
        #
        # The user's expectation is "FAM match should show $100
        # when the cap is met" — so when the cap was active, we
        # transfer the freed capacity to non-denom rows (raise their
        # match_amount, drop their customer_charged by the same
        # amount, method_amount unchanged).  Net result:
        #   - FAM match stays at the cap
        #   - Customer pays less by exactly the forfeit amount
        #   - Per-vendor reconciliation still holds (non-denom rows
        #     distribute across the same vendors regardless of
        #     match-vs-customer split internally)
        #
        # When the cap was NOT active, no give-back applies —
        # there's no freed capacity (FAM was paying full uncapped
        # match, and the forfeit really is a permanent loss to
        # FAM's match budget for over-handed-over denom).
        if (result.get('match_was_capped')
                and total_reduction > 0):
            # Find non-denom line items that have headroom (uncapped
            # match > current match).  Headroom = how much more match
            # this row could absorb.
            headrooms: list[tuple[int, int]] = []
            for i, li in enumerate(result['line_items']):
                if i >= len(items):
                    continue
                item = items[i]
                denom = item.get('denomination')
                if denom and denom > 0:
                    continue  # denom rows: customer fixed, skip
                pct = (item.get('match_percent_snapshot')
                       or item.get('match_percent') or 0)
                if pct <= 0:
                    continue  # 0%-match rows have no match to grow
                # Uncapped match for this method_amount.
                uncapped = round(
                    li['method_amount'] * pct / (100.0 + pct))
                room = uncapped - li['match_amount']
                if room > 0:
                    headrooms.append((i, room))

            if headrooms:
                room_total = sum(r for _, r in headrooms)
                give_back_total = min(total_reduction, room_total)
                # Distribute proportionally to headroom; last row
                # absorbs the exact remainder so the sum is exact
                # to the cent.
                running = 0
                for k, (idx, room) in enumerate(headrooms):
                    if k == len(headrooms) - 1:
                        give = give_back_total - running
                    else:
                        give = round(
                            give_back_total * room / room_total)
                        running += give
                    give = min(give, room)
                    if give <= 0:
                        continue
                    target = result['line_items'][idx]
                    target['match_amount'] += give
                    target['customer_charged'] -= give
                    items[idx]['match_amount'] = target['match_amount']
                    items[idx]['customer_charged'] = (
                        target['customer_charged'])
                # Update result totals: match goes back up, customer
                # goes down by the same amount, method unchanged.
                result['fam_subsidy_total'] = (
                    result['fam_subsidy_total'] + give_back_total)
                result['customer_total_paid'] = (
                    result['customer_total_paid'] - give_back_total)

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
            {'method_amount': it['method_amount'],
             'match_percent': it['match_percent'],
             'denomination': it.get('denomination')}
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

        # Sync post-engine, post-forfeit values back to items so
        # Layer 2C and ``_distribute_and_save_payments`` see the
        # engine's final method_amount.  v1.9.10 onsite-finding fix:
        # when the engine's cap-fallback inflates non-denom method
        # to absorb denom-method shrinkage (denom customer fixed,
        # match reduced → method reduced), ``items[non_denom].method``
        # was stale at ``_collect_line_items``'s pre-engine cap.
        # Layer 2C's per-vendor distribution then under-allocated
        # vendors with denom rows (only the partial SNAP share fit,
        # producing "Elfinwild $10.48 of $11.11" mis-allocation on
        # the bricked-transaction repro).
        for i, li in enumerate(result['line_items']):
            if i < len(items):
                items[i]['method_amount'] = li['method_amount']
                items[i]['match_amount'] = li['match_amount']
                items[i]['customer_charged'] = li['customer_charged']

        # ── Layer 2A: charge-integrity guard ────────────────────────────
        # Refuse to save if any row's input field shows a different charge
        # than the engine is about to commit.  This catches *any* drift
        # between the spinbox and the saved customer_charged — cap
        # inflation that didn't sync, signal-ordering quirks, draft
        # restore using stale fields, future regressions, etc.
        #
        # If this fires the volunteer must use Auto-Distribute or
        # manually correct the row before retrying — better a hard stop
        # than a silent undercharge.  Layer 1 in _update_summary keeps
        # the rows in sync during normal operation, so this is a safety
        # net rather than a routine guard.
        if result.get('line_items'):
            valid_rows = [r for r in self._payment_rows if r.get_data()]
            for i, row in enumerate(valid_rows):
                if i >= len(result['line_items']):
                    break
                li = result['line_items'][i]
                expected_charge = li['customer_charged']
                actual_charge = row._get_active_charge()
                # v1.9.10 follow-up (2026-05-01): denom rows whose
                # customer_charged was reduced by Phase B forfeit
                # (because the customer handed more denomination
                # than the bound vendor's receipt) carry
                # ``customer_forfeit_cents``.  The spinbox still
                # shows the physical scrip (``actual_charge``); the
                # engine's ``customer_charged`` is the
                # post-forfeit effective contribution.  Layer 2A
                # accepts the gap when it exactly equals the
                # forfeit recorded by the engine.
                forfeit_cents = li.get('customer_forfeit_cents', 0) or 0
                expected_pre_forfeit = expected_charge + forfeit_cents
                if expected_pre_forfeit != actual_charge:
                    method = row.get_selected_method()
                    method_name = (method['name'] if method
                                   else f"row {i + 1}")
                    logger.error(
                        "Charge-integrity guard tripped: %s row shows "
                        "%d cents but engine computed %d cents "
                        "(forfeit %d) — refusing to confirm",
                        method_name, actual_charge, expected_charge,
                        forfeit_cents,
                    )
                    self._show_error(
                        f"Payment row mismatch detected and confirmation "
                        f"was blocked.\n\n"
                        f"The {method_name} input shows "
                        f"{format_dollars(actual_charge)} but the "
                        f"calculated charge after applying caps and "
                        f"reconciliation is "
                        f"{format_dollars(expected_charge)}.\n\n"
                        f"Click Auto-Distribute or correct the "
                        f"{method_name} amount, verify the row matches "
                        f"the Collect-from-Customer panel, then try "
                        f"Confirm Payment again."
                    )
                    self.confirm_btn.setEnabled(True)
                    return

        # ── Layer 2B: vendor-eligibility guard (v1.9.9) ─────────────────
        # Every denominated row must commit to a vendor that
        # (a) appears in this order's transactions and
        # (b) is registered for that payment method via
        #     vendor_payment_methods (Settings → Vendors → Methods).
        #
        # For multi-vendor orders the binding comes from the row's
        # vendor dropdown.  For single-vendor orders the binding is
        # *implicit* in the only transaction — but the eligibility
        # check still applies, otherwise a volunteer could pick a
        # method the lone vendor isn't registered for and slip past
        # the dropdown filter.  This was the v1.9.9 onsite finding.
        order_vendors = self._get_order_vendors()
        if order_vendors:
            from fam.models.payment_method import (
                get_vendor_payment_method_ids,
            )
            order_vendor_ids = {v['id'] for v in order_vendors}
            for i, item in enumerate(items):
                denom = item.get('denomination')
                if not (denom and denom > 0):
                    continue  # non-denominated distributes — no binding
                bound_vid = item.get('bound_vendor_id')
                method_name = item.get('method_name_snapshot',
                                        f"row {i + 1}")

                # Resolve the effective vendor for this row.
                # Single-vendor pool → implicit binding to the lone
                # vendor.  Multi-vendor → explicit row.bound_vendor_id.
                if bound_vid is None:
                    if len(order_vendors) == 1:
                        bound_vid = order_vendors[0]['id']
                    else:
                        logger.warning(
                            "Vendor-eligibility guard: %s row has no "
                            "vendor bound — refused to confirm",
                            method_name,
                        )
                        self._show_error(
                            f"{method_name} needs a vendor selected.  "
                            f"Choose the vendor that received this "
                            f"denominated payment from the dropdown "
                            f"next to the method."
                        )
                        self.confirm_btn.setEnabled(True)
                        return

                if bound_vid not in order_vendor_ids:
                    logger.warning(
                        "Vendor-eligibility guard: %s bound to "
                        "vendor_id=%s which isn't on this order — "
                        "refused to confirm",
                        method_name, bound_vid,
                    )
                    self._show_error(
                        f"{method_name} is bound to a vendor that isn't "
                        f"on this customer's order.  Re-select the "
                        f"vendor from the dropdown next to the method."
                    )
                    self.confirm_btn.setEnabled(True)
                    return

                eligible_pm_ids = get_vendor_payment_method_ids(bound_vid)
                # Graceful permissive fallback: a vendor with no
                # configured vendor_payment_methods rows (legacy /
                # uninitialized data) skips the eligibility check so
                # we don't break flows that pre-date v1.9.9.  This
                # mirrors the dropdown-filter behavior.
                if eligible_pm_ids and (
                        item['payment_method_id'] not in eligible_pm_ids):
                    vendor_name = next(
                        (v['name'] for v in order_vendors
                         if v['id'] == bound_vid), 'this vendor')
                    logger.warning(
                        "Vendor-eligibility guard: vendor=%s not "
                        "registered for method=%s — refused to confirm",
                        vendor_name, method_name,
                    )
                    self._show_error(
                        f"{vendor_name} isn't registered to accept "
                        f"{method_name}.  Either choose a different "
                        f"method, change the vendor's eligible methods "
                        f"in Settings → Vendors → Methods, or skip this "
                        f"payment for that vendor."
                    )
                    self.confirm_btn.setEnabled(True)
                    return

        # ── Layer 2C: per-transaction reconciliation guard (v1.9.9) ─────
        # Sum of method_amounts that will be saved against each
        # transaction must equal that transaction's receipt_total
        # (within ±1¢ for penny reconciliation).  Catches the case
        # where a denominated payment was bound to a vendor whose
        # receipt is smaller than the bound charge — e.g. binding a
        # $25 token to a $10 receipt.
        per_txn_alloc: dict[int, int] = {
            t['id']: 0 for t in self._order_transactions
        }
        # Map vendor_id → list of txn_ids.  v1.9.10 follow-up
        # (2026-05-01): a vendor can have MULTIPLE receipts in one
        # order; the guard must mirror the save path's distribution
        # algorithm, not the obsolete "first match wins" assumption.
        vendor_to_txn_ids: dict[int, list[int]] = {}
        for t in self._order_transactions:
            vid = t.get('vendor_id')
            if vid is not None:
                vendor_to_txn_ids.setdefault(vid, []).append(t['id'])

        # Phase 1: denominated bindings claim their target transaction(s).
        # Mirrors the save-path: single-txn vendors take the whole
        # method_amount; multi-txn vendors split proportionally to
        # per-transaction remaining balance.
        for item in items:
            denom = item.get('denomination')
            if not (denom and denom > 0):
                continue
            bound_vid = item.get('bound_vendor_id')
            target_ids = vendor_to_txn_ids.get(bound_vid) if bound_vid is not None else None
            if not target_ids:
                # Defensive default — eligibility/Layer-2 guards
                # should have blocked unbound denom on multi-vendor
                # orders before reaching here.
                if self._order_transactions:
                    target_ids = [self._order_transactions[0]['id']]
                else:
                    continue
            ma = item['method_amount']
            if len(target_ids) == 1:
                per_txn_alloc[target_ids[0]] += ma
            else:
                per_txn_remaining = []
                total_remaining = 0
                # Build receipt-total lookup keyed by txn_id for
                # this vendor's transactions (in the order they
                # appear in self._order_transactions).
                txn_lookup = {t['id']: t for t in self._order_transactions}
                for tid in target_ids:
                    t = txn_lookup[tid]
                    left = max(0, t['receipt_total'] - per_txn_alloc[tid])
                    per_txn_remaining.append(left)
                    total_remaining += left
                if total_remaining <= 0:
                    per_txn_alloc[target_ids[-1]] += ma
                    continue
                running = 0
                last_pos = len(target_ids) - 1
                for k, tid in enumerate(target_ids):
                    if k == last_pos:
                        share = ma - running
                    else:
                        weight = per_txn_remaining[k] / total_remaining
                        share = round(ma * weight)
                        running += share
                    per_txn_alloc[tid] += share
        # Phase 2: distribute non-denom proportionally to remaining,
        # matching the save algorithm's behavior.
        for item in items:
            denom = item.get('denomination')
            if denom and denom > 0:
                continue
            ma_total = item['method_amount']
            per_txn_remaining = []
            total_remaining = 0
            for t in self._order_transactions:
                left = max(0, t['receipt_total'] - per_txn_alloc[t['id']])
                per_txn_remaining.append(left)
                total_remaining += left
            if total_remaining <= 0:
                continue
            running = 0
            last_idx = len(self._order_transactions) - 1
            for t_idx, t in enumerate(self._order_transactions):
                if t_idx == last_idx:
                    share = ma_total - running
                else:
                    weight = (per_txn_remaining[t_idx] / total_remaining
                              if total_remaining > 0 else 0)
                    share = round(ma_total * weight)
                    running += share
                per_txn_alloc[t['id']] += share

        for t in self._order_transactions:
            allocated = per_txn_alloc[t['id']]
            receipt = t['receipt_total']
            if abs(allocated - receipt) > 1:
                vendor_name = t.get('vendor_name', 'a vendor')
                if allocated > receipt:
                    self._show_error(
                        f"Over-allocation on {vendor_name}'s receipt: "
                        f"{format_dollars(allocated)} of payments are "
                        f"being applied to a {format_dollars(receipt)} "
                        f"receipt.  Reduce a denominated payment bound "
                        f"to {vendor_name}, or change its vendor in "
                        f"the row."
                    )
                else:
                    self._show_error(
                        f"Under-allocation on {vendor_name}'s receipt: "
                        f"only {format_dollars(allocated)} is being "
                        f"applied to a {format_dollars(receipt)} "
                        f"receipt.  Add more payment to cover the gap "
                        f"or use Auto-Distribute."
                    )
                # WARNING (not ERROR): this guard fires during normal
                # data-entry validation — the volunteer typed payments
                # that don't reconcile per-vendor and the system
                # politely refused.  ERROR severity would falsely flag
                # this as a code bug worth investigating.
                logger.warning(
                    "Per-vendor reconciliation guard tripped: "
                    "vendor=%s receipt=%d alloc=%d gap=%d",
                    vendor_name, receipt, allocated, allocated - receipt,
                )
                self.confirm_btn.setEnabled(True)
                return

        # ── Pre-confirmation dialog: list what to collect ─────────
        # v1.9.9: replaced the old plain-text QMessageBox.question
        # with a structured PaymentConfirmationDialog.  The new
        # dialog visually separates *informative* content (vendor
        # reimbursement, FAM match) from *actionable* content
        # (collect $X via method Y) from *warning* content
        # (denomination forfeit), wraps the action zone in a
        # marching-ants animated border so it's impossible to miss,
        # and adds a REQUIRED checkbox per external-device method
        # (SNAP/EBT) that the volunteer must tick before the Confirm
        # button enables — forcing function so SNAP doesn't get
        # auto-confirmed without first being processed at the
        # external EBT terminal.
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        # Rewards add-on (v1.9.10+) — derive the customer-facing
        # reward lines from this order's source-method totals BEFORE
        # the payment is committed.  Computed from the dialog's
        # items + post-engine line_items so the dialog shows what
        # the cashier actually has to hand out at confirmation
        # time.  When the feature flag is off, or no rule fires,
        # ``reward_lines`` is an empty list and the dialog
        # suppresses its rewards zone entirely.
        #
        # NOTE: this computation is read-only and informational —
        # it does NOT touch the financial pipeline (line_items,
        # action zone, totals, denom_overage, vendor reimbursement).
        reward_lines: list = []
        try:
            from fam.utils.app_settings import is_rewards_enabled
            if is_rewards_enabled():
                from fam.models.reward_rule import (
                    get_active_reward_rules, get_method_lookup,
                )
                from fam.utils.rewards import (
                    compute_rewards_for_order,
                )
                # Sum customer_charged per method from the engine's
                # post-forfeit line_items (matches what will actually
                # land in payment_line_items once committed).
                engine_lines = result.get('line_items') or []
                source_totals: dict[int, int] = {}
                for i, li in enumerate(engine_lines):
                    if i >= len(items):
                        break
                    pm_id = items[i].get('payment_method_id')
                    if pm_id is None:
                        continue
                    source_totals[pm_id] = source_totals.get(
                        pm_id, 0) + li.get('customer_charged', 0)
                rules = get_active_reward_rules()
                if rules and source_totals:
                    reward_lines = compute_rewards_for_order(
                        source_totals, rules, get_method_lookup())
        except Exception:
            # Defensive: rewards are an informational add-on; a
            # bug in derivation must NOT block payment confirmation.
            logger.exception(
                "Failed to compute rewards for confirmation "
                "dialog; suppressing rewards zone")
            reward_lines = []
        dlg = PaymentConfirmationDialog(
            line_items=result.get('line_items') or [],
            items=items,
            receipt_total=receipt_total,
            denom_overage=denom_overage,
            receipt_count=len(self._order_transactions),
            parent=self,
            reward_lines=reward_lines,
        )
        if dlg.exec() != QDialog.Accepted:
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

            # Rewards add-on (v1.9.10+) — write the snapshot of any
            # generated rewards atomically with the payment commit.
            # Write-once history: reward rows are NEVER modified
            # after this insert (voids/adjustments/rule changes
            # do not retro-touch them).  See
            # ``fam/models/generated_reward.py`` for the full
            # contract.
            #
            # v2.0.2 fix (UF-H10): a failed reward insert MUST roll
            # the entire transaction back.  Pre-fix the exception was
            # swallowed and ``conn.commit()`` ran anyway — payment +
            # line items were durably saved but the rewards rows the
            # customer was about to be handed physical tokens for
            # never persisted.  Coordinator inventory reconciliation
            # later silently mismatched.  By re-raising here, a real
            # transient DB-locked failure surfaces as
            # "Payment failed: please retry" BEFORE the clerk hands
            # over tokens, and the rolled-back state means a retry
            # cleanly succeeds.
            if reward_lines and self._current_order_id and open_md:
                from fam.models.generated_reward import (
                    record_generated_rewards,
                )
                record_generated_rewards(
                    customer_order_id=self._current_order_id,
                    market_day_id=open_md['id'],
                    reward_lines=reward_lines,
                    generated_by=confirmed_by,
                    conn=conn,
                )

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

        # ── Post-confirm navigation: auto-return to Receipt Intake ──
        #
        # CANONICAL UX (v1.9.10+, 2026-05-01): a successful confirm
        # auto-navigates back to Receipt Intake.  No modal "would
        # you like to..." dialog.  Two reasons:
        #
        #   1. **Workflow fidelity** — at the market table, the
        #      next customer is already standing there with their
        #      receipts the moment the previous one is confirmed.
        #      Receipt Intake is where the volunteer spends 95%+
        #      of their time; landing them there is the right
        #      default.  The success_frame banner on this screen
        #      stays visible for a beat before the navigation
        #      lands, giving them a moment to verify.
        #
        #   2. **Reliability** — the original
        #      ``QMessageBox.question(...)`` modal hung
        #      intermittently because ``payment_confirmed.emit()``
        #      synchronously fires the background sync ``QThread``
        #      and the modal's button events occasionally got lost
        #      in the event-loop transition.  Auto-navigation
        #      sidesteps the failure mode entirely.
        #
        # If a volunteer needs to stay on this screen post-confirm
        # (rare — usually only to print the receipt), they can
        # navigate back via the main nav.  ``payment_confirmed``
        # always fires first so cloud sync triggers regardless of
        # navigation.
        self.payment_confirmed.emit()
        self.return_to_intake_requested.emit()

    def _distribute_and_save_payments(self, items, order_total, commit=True):
        """Save the order's payment line items, with v1.9.9 per-vendor
        binding for denominated rows.

        Algorithm
        ---------
        1. **Denominated rows** commit ENTIRELY to the bound vendor's
           transaction.  No proportional spread — a $5 Food Bucks check
           never shows up split $3-Vendor-A / $2-Vendor-B in reports.
        2. **Non-denominated rows** distribute across transactions
           proportionally to *per-transaction remaining balance* (after
           denominated rows have claimed their share), so each
           transaction's receipt_total is exactly covered.
        3. Match-cap is applied across the whole order (the cap is
           per-customer, not per-vendor).
        4. Per-transaction penny reconciliation absorbs ±1¢ rounding
           drift into the largest matched line item on that
           transaction, so SUM(method_amount) == receipt_total exactly.

        For a single-transaction order the algorithm collapses into the
        legacy proportional behavior because every row's bound_vendor_id
        either matches the only vendor or is None (which falls through
        to the non-denom phase against a single transaction).
        """
        if not order_total or order_total <= 0:
            return

        num_txns = len(self._order_transactions)
        all_txn_items: list[list[dict]] = [[] for _ in range(num_txns)]

        # Map vendor_id → list of transaction indices.  v1.9.10
        # follow-up (2026-05-01): a single vendor can have MULTIPLE
        # receipts in one order (the volunteer entered, e.g.,
        # "1.11 Juice Bar $11.11" and later "1.11 Juice Bar $100"
        # at the receipt-intake screen).  The earlier "first match
        # wins" map silently dumped every bound denom payment onto
        # the first transaction, then Layer 2C flagged
        # "Over-allocation on Juice Bar's receipt: $111.11 of
        # payments are being applied to a $11.11 receipt" — even
        # though the customer's $111.11 of Food RX legitimately
        # covered both Juice Bar receipts.  Now bound denom items
        # distribute proportionally across ALL of the vendor's
        # transactions, just like non-denom rows do in Phase 2.
        vendor_to_txn_idxs: dict[int, list[int]] = {}
        for t_idx, t in enumerate(self._order_transactions):
            vid = t.get('vendor_id')
            if vid is not None:
                vendor_to_txn_idxs.setdefault(vid, []).append(t_idx)

        # Track per-transaction allocated method_amount as denominated
        # rows commit so the non-denom phase knows what's already
        # claimed (and thus how much receipt is left to fill).
        txn_method_alloc = [0] * num_txns

        def _line_item_for(item: dict, method_amount: int,
                            match_amount: int) -> dict:
            return {
                'payment_method_id': item['payment_method_id'],
                'method_name_snapshot': item['method_name_snapshot'],
                'match_percent_snapshot': item['match_percent_snapshot'],
                'method_amount': method_amount,
                'match_amount': match_amount,
                'customer_charged': method_amount - match_amount,
                # v1.9.10: carry denomination through so the
                # cap-application step can distinguish denom rows
                # (whose customer_charged is fixed by physical units)
                # from non-denom rows (which absorb cap deficit).
                'denomination': item.get('denomination'),
            }

        # ── Phase 1: Denominated rows → bound vendor's transaction(s) ──
        # Each denominated payment instrument is physical paper handed
        # over to one specific vendor.  When that vendor has a SINGLE
        # transaction in this order (the common case), the whole
        # method_amount + match commits to that transaction intact.
        # When the vendor has MULTIPLE transactions (multi-receipt
        # per vendor), distribute proportionally to per-transaction
        # remaining receipt-balance — same algorithm as Phase 2 below.
        # Reports still attribute the payment to the bound vendor;
        # they just split it across that vendor's receipt rows.
        for item in items:
            denom = item.get('denomination')
            is_denom = bool(denom and denom > 0)
            if not is_denom:
                continue
            bound_vid = item.get('bound_vendor_id')
            target_idxs = vendor_to_txn_idxs.get(bound_vid) if bound_vid is not None else None
            if not target_idxs:
                # Single-transaction order, single_vendor_mode dialog,
                # or unbound row in a multi-vendor order.  In the first
                # two cases the binding is implicit on the only txn.
                # In the third, eligibility/Layer-2 guards should have
                # blocked confirmation; default to txn 0 defensively.
                target_idxs = [0]
            mat_pct = item['match_percent_snapshot']
            ma = item['method_amount']
            # Honor the caller-supplied ``match_amount`` when present
            # (e.g. ``_apply_denomination_forfeit`` reduced match below
            # the formula value because customer over-handed-over physical
            # denom).  Recomputing match from the formula here would
            # silently undo the forfeit reduction and corrupt
            # ``customer_charged`` on save.  v1.9.10 onsite-finding fix
            # — the saved DB previously had FB Healthy customer=$12.65
            # for a customer who handed over 7 × $2 = $14 in tokens.
            total_match = item.get('match_amount')
            if total_match is None or total_match < 0:
                total_match = round(ma * (mat_pct / (100.0 + mat_pct)))

            if len(target_idxs) == 1:
                # Single-transaction vendor — fast path, identical to
                # the legacy behaviour.  Avoids any rounding drift
                # the multi-txn split could introduce.
                idx = target_idxs[0]
                all_txn_items[idx].append(
                    _line_item_for(item, ma, total_match))
                txn_method_alloc[idx] += ma
            else:
                # Multi-receipt vendor: split ma + match across the
                # vendor's transactions weighted by per-transaction
                # remaining receipt-balance (= receipt − already
                # claimed by other denom rows on the same vendor).
                per_txn_remaining = []
                total_remaining = 0
                for ti in target_idxs:
                    t = self._order_transactions[ti]
                    left = max(0, t['receipt_total'] - txn_method_alloc[ti])
                    per_txn_remaining.append(left)
                    total_remaining += left

                # Edge case: every receipt for this vendor is
                # already filled (e.g. by an earlier denom row for
                # the same vendor).  Dump remainder on the LAST
                # vendor txn; the per-vendor reconciliation guard
                # will surface the over-allocation to the volunteer.
                if total_remaining <= 0:
                    idx = target_idxs[-1]
                    all_txn_items[idx].append(
                        _line_item_for(item, ma, total_match))
                    txn_method_alloc[idx] += ma
                    continue

                running_method = 0
                running_match = 0
                last_pos = len(target_idxs) - 1
                for k, ti in enumerate(target_idxs):
                    if k == last_pos:
                        share_method = ma - running_method
                        share_match = total_match - running_match
                    else:
                        weight = per_txn_remaining[k] / total_remaining
                        share_method = round(ma * weight)
                        share_match = round(total_match * weight)
                        running_method += share_method
                        running_match += share_match
                    if share_method == 0:
                        continue
                    all_txn_items[ti].append(
                        _line_item_for(item, share_method, share_match))
                    txn_method_alloc[ti] += share_method

        # ── Phase 2: Non-denominated rows → proportional split by
        # per-transaction REMAINING balance ──
        non_denom_items = [
            (idx, it) for idx, it in enumerate(items)
            if not (it.get('denomination') and it['denomination'] > 0)
        ]

        # For each non-denom row, distribute its method_amount across
        # the transactions weighted by what's left to allocate on each.
        for _row_idx, item in non_denom_items:
            ma_total = item['method_amount']
            mat_pct = item['match_percent_snapshot']

            # Per-txn remaining = receipt_total − already-claimed.
            per_txn_remaining = []
            total_remaining = 0
            for t_idx, t in enumerate(self._order_transactions):
                left = max(0, t['receipt_total'] - txn_method_alloc[t_idx])
                per_txn_remaining.append(left)
                total_remaining += left

            if total_remaining <= 0:
                # Every transaction already fully claimed by denominated
                # rows — nothing to distribute.  Per-vendor reconciliation
                # guard (Layer 3 in _confirm_payment) catches the
                # over-allocation case and surfaces an error before save.
                # For now: drop this row's allocation entirely.
                continue

            # Remainder-based distribution — last txn gets the exact
            # leftover so SUM(method_amount) == ma_total to the cent.
            running_method = 0
            running_match = 0
            total_match = round(ma_total * (mat_pct / (100.0 + mat_pct)))
            last_idx = num_txns - 1
            for t_idx in range(num_txns):
                if t_idx == last_idx:
                    share_method = ma_total - running_method
                    share_match = total_match - running_match
                else:
                    weight = (per_txn_remaining[t_idx] / total_remaining
                              if total_remaining > 0 else 0)
                    share_method = round(ma_total * weight)
                    share_match = round(
                        share_method * (mat_pct / (100.0 + mat_pct)))
                    running_method += share_method
                    running_match += share_match
                if share_method == 0:
                    continue
                all_txn_items[t_idx].append(
                    _line_item_for(item, share_method, share_match))
                txn_method_alloc[t_idx] += share_method

        # Apply match-limit cap across all transactions.
        #
        # v1.9.10 onsite-finding fix: this is a parallel implementation
        # of the cap logic that also lived in
        # ``calculate_payment_breakdown``.  Both used naive
        # proportional reduction across ALL line items, which inflated
        # ``customer_charged`` on denominated rows above their physical
        # ``unit_count × denomination``.  When the user saved the order
        # as a draft, those inflated customer values landed in the DB,
        # then on reload the draft restorer wrote them back to the
        # spinbox where the stepper truncated to a non-matching unit
        # count.  Confirm/Re-save then drifted further.
        #
        # Fix mirrors the engine fix: when total denom uncapped match
        # ≤ cap, the cap deficit is absorbed ENTIRELY on non-denom
        # rows.  Denom rows keep their customer_charged FIXED at
        # unit_count × denomination.  Falls back to legacy proportional
        # only when denom matches alone exceed the cap (rare).
        if self._match_limit is not None:
            total_match = sum(
                li['match_amount']
                for txn_items in all_txn_items
                for li in txn_items
            )
            if total_match > self._match_limit >= 0:
                # Identify denom vs non-denom line items.  A line item
                # is "denom" if its source method has a denomination
                # value > 0; the line dicts carry ``denomination``
                # via ``_line_item_for``.
                denom_lines = []
                non_denom_lines = []
                for txn_items in all_txn_items:
                    for li in txn_items:
                        if li.get('denomination') and li['denomination'] > 0:
                            denom_lines.append(li)
                        else:
                            non_denom_lines.append(li)

                denom_uncapped = sum(li['match_amount']
                                      for li in denom_lines)
                non_denom_uncapped = sum(li['match_amount']
                                          for li in non_denom_lines)

                if (denom_uncapped <= self._match_limit
                        and non_denom_uncapped > 0):
                    # Common path: keep denom customer_charged fixed,
                    # flex non-denom rows to absorb cap deficit.
                    available_for_non_denom = (
                        self._match_limit - denom_uncapped)
                    non_denom_cap_ratio = (
                        available_for_non_denom / non_denom_uncapped)
                    for li in non_denom_lines:
                        li['match_amount'] = round(
                            li['match_amount'] * non_denom_cap_ratio)
                        li['customer_charged'] = (
                            li['method_amount'] - li['match_amount'])
                    # Denom lines: untouched.
                else:
                    # Fallback (rare): denom matches alone exceed cap.
                    cap_ratio = self._match_limit / total_match
                    for txn_items in all_txn_items:
                        for li in txn_items:
                            li['match_amount'] = round(
                                li['match_amount'] * cap_ratio)
                            li['customer_charged'] = (
                                li['method_amount'] - li['match_amount'])

                # Penny adjustment: fix rounding drift so sum == cap
                # exactly.  Prefer adjusting a non-denom row to keep
                # denom customer_charged untouched.
                capped_sum = sum(
                    li['match_amount']
                    for txn_items in all_txn_items for li in txn_items
                )
                penny_diff = self._match_limit - capped_sum
                if penny_diff != 0:
                    non_denom_matched = [
                        li for li in non_denom_lines
                        if li['match_amount'] > 0
                    ]
                    candidates = (non_denom_matched if non_denom_matched
                                   else [
                        li for txn_items in all_txn_items
                        for li in txn_items
                        if li['match_amount'] > 0
                    ])
                    if candidates:
                        target = max(candidates,
                                      key=lambda li: li['match_amount'])
                        target['match_amount'] = (
                            target['match_amount'] + penny_diff
                        )
                        target['customer_charged'] = (
                            target['method_amount'] - target['match_amount']
                        )

        # ── Penny reconciliation per transaction ─────────────────────
        # The method_amounts collected from payment rows are computed
        # independently per row.  Their sum can be ±1¢ off from the
        # receipt_total due to rounding in charge_to_method_amount().
        # Absorb the gap into the FAM match of the largest matched
        # line item so that SUM(method_amount) == receipt_total exactly.
        #
        # v1.9.10 follow-up (2026-05-01, onsite report): pick a
        # target line item whose method/match has enough headroom
        # to absorb the gap without going negative.  The user
        # reported a "Payment failed: method_amount must be >= 0"
        # crash on a multi-receipt-per-vendor order — Phase 1's
        # proportional split left a 1¢ over-allocation on a small
        # txn whose only line item had method_amount=1¢; the old
        # blind ``+= gap`` pushed method to -1¢ and the DB CHECK
        # trigger rejected the insert.  Now we filter candidates
        # by required headroom and skip the adjustment if no
        # line item can absorb it (the ±1¢ drift is documented
        # tolerance).
        for t, txn_items in zip(self._order_transactions, all_txn_items):
            txn_receipt = t['receipt_total']  # integer cents
            txn_alloc = sum(li['method_amount'] for li in txn_items)
            gap = txn_receipt - txn_alloc
            if gap != 0 and abs(gap) <= len(txn_items):
                # When gap > 0 we're under-allocated: any line item
                # can absorb (method/match grow).  When gap < 0
                # we're over-allocated: target must have
                # ``method_amount + gap >= 0`` AND
                # ``match_amount + gap >= 0`` to stay non-negative.
                if gap >= 0:
                    candidates = [
                        li for li in txn_items
                        if li['match_percent_snapshot'] > 0
                    ] or list(txn_items)
                else:
                    headroom = -gap
                    candidates = [
                        li for li in txn_items
                        if li['match_percent_snapshot'] > 0
                        and li['method_amount'] >= headroom
                        and li['match_amount'] >= headroom
                    ]
                    # Fall back to ANY line item with enough
                    # method-headroom (zero-match Cash absorbs into
                    # method only — set match_amount to 0 floor below).
                    if not candidates:
                        candidates = [
                            li for li in txn_items
                            if li['method_amount'] >= headroom
                        ]
                if candidates:
                    target = max(
                        candidates,
                        key=lambda li: li['method_amount'],
                    )
                    new_method = target['method_amount'] + gap
                    new_match = target['match_amount'] + gap
                    # Final guard — if (somehow) the candidate
                    # filter let through a row whose match would
                    # go negative, clamp to 0 rather than failing
                    # the save.  customer_charged absorbs the
                    # difference on the match-clamped path so the
                    # row's per-line invariant
                    # ``customer + match = method`` holds.
                    if new_match < 0:
                        new_method -= new_match  # add back the deficit
                        target['customer_charged'] += -new_match
                        new_match = 0
                    if new_method < 0:
                        # Last-resort: skip — leaves the ±1¢ drift
                        # unresolved but never a corrupt save.
                        logger.warning(
                            "Penny reconciliation skipped on txn=%s — "
                            "no line item could absorb gap=%d cents "
                            "without going negative",
                            t['id'], gap,
                        )
                    else:
                        target['method_amount'] = new_method
                        target['match_amount'] = new_match
                else:
                    logger.warning(
                        "Penny reconciliation: no candidate line "
                        "item with enough headroom on txn=%s gap=%d",
                        t['id'], gap,
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

        # v1.9.10 follow-up (2026-05-01, onsite report): defensive
        # final sweep — clamp any negative method/match to 0 with a
        # loud warning before save.  The DB CHECK trigger rejects
        # negative values; rather than crashing the save and leaving
        # the volunteer stuck, salvage the commit while logging
        # enough context to root-cause the bug post-hoc.  This
        # should never trigger after the per-txn reconciliation
        # fix above; if it does, the log captures the offending
        # line item for forensics.
        for t, txn_items in zip(self._order_transactions, all_txn_items):
            for li in txn_items:
                if li['method_amount'] < 0 or li['match_amount'] < 0:
                    logger.error(
                        "DEFENSIVE CLAMP: negative amount on save "
                        "txn=%s method=%s method_amount=%d "
                        "match_amount=%d customer_charged=%d — "
                        "clamping to 0.  This indicates an upstream "
                        "bug in distribute_and_save_payments; please "
                        "report with this log line.",
                        t['id'], li.get('method_name_snapshot'),
                        li['method_amount'], li['match_amount'],
                        li.get('customer_charged', 0),
                    )
                    if li['match_amount'] < 0:
                        li['match_amount'] = 0
                    if li['method_amount'] < 0:
                        # Re-derive method from non-negative
                        # customer + match so the per-line
                        # invariant survives the clamp.
                        li['method_amount'] = max(
                            0,
                            li.get('customer_charged', 0)
                            + li['match_amount'])

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

            # Rewards add-on (v1.9.10+) — read the persisted
            # snapshot rows from ``generated_rewards`` written by
            # ``record_generated_rewards`` at confirmation time.
            # Reading from the stored snapshot (not recomputing)
            # ensures the receipt matches exactly what the cashier
            # handed out, immune to any subsequent rule edits or
            # void/adjust activity.
            rewards_lines: list[dict] = []
            try:
                if self._current_order_id:
                    from fam.models.generated_reward import (
                        get_generated_rewards_for_order,
                    )
                    for r in get_generated_rewards_for_order(
                            self._current_order_id):
                        rewards_lines.append({
                            'source_method':
                                r['source_method_name_snapshot'],
                            'source_total': cents_to_dollars(
                                r['source_total_cents']),
                            'reward_method':
                                r['reward_method_name_snapshot'],
                            'reward_unit': cents_to_dollars(
                                r['reward_unit_cents']),
                            'n_units': r['n_units'],
                            'reward_total': cents_to_dollars(
                                r['reward_total_cents']),
                        })
            except Exception:
                logger.exception(
                    "Failed to read generated rewards for receipt; "
                    "suppressing rewards section")
                rewards_lines = []

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
                'rewards': rewards_lines,
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

        # Rewards section (v1.9.10+) — purely informational, no
        # dollar contribution to vendor reimbursement.  Hidden
        # entirely when the rewards feature is off or no rule fired.
        rewards = data.get('rewards') or []
        rewards_section = ""
        if rewards:
            reward_rows = ""
            for r in rewards:
                reward_rows += (
                    f"<tr>"
                    f"<td style='padding:2px 8px 2px 0;'>"
                    f"{r['n_units']} × ${r['reward_unit']:.2f} "
                    f"{r['reward_method']}</td>"
                    f"<td style='padding:2px 0 2px 8px; "
                    f"text-align:right;'>"
                    f"earned from ${r['source_total']:.2f} "
                    f"{r['source_method']}</td>"
                    f"</tr>"
                )
            rewards_section = f"""
            <hr style="border: 0.5px solid #ccc;">
            <p style="font-weight:bold; margin-bottom:4px;
                     color:#6A1B9A;">
                🎁 Rewards Earned (handed separately)
            </p>
            <table style="width:100%; font-size:10pt;
                          border-collapse:collapse;">
                {reward_rows}
            </table>
            <p style="font-size:9pt; color:#888; font-style:italic;
                      margin: 4px 0 0 0;">
                Rewards are a marketing/loyalty add-on — NOT part
                of vendor reimbursement or FAM match.
            </p>
            """

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

            {rewards_section}

            <hr style="border: 1px solid #2b493b;">

            <p style="text-align:center; font-size:11pt; color:#2b493b; margin:8px 0 2px;">
                Thank you for shopping at the market!</p>
            <p style="text-align:center; font-size:9pt; color:#999; margin:0;">
                Confirmed by: {data['confirmed_by']}</p>
        </div>
        """
