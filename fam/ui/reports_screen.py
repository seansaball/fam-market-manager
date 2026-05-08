"""Screen F: Reports and Exports."""

import logging
import os
import tempfile
import webbrowser

import pandas as pd
import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QFileDialog, QMessageBox, QScrollArea, QTextEdit, QCheckBox, QSplitter
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush, QFont

from fam.database.connection import get_connection
from fam.models.market_day import get_all_market_days, get_all_markets
from fam.models.vendor import get_all_vendors
from fam.models.payment_method import get_all_payment_methods
from fam.utils.export import (
    export_vendor_reimbursement, export_fam_match_report, export_detailed_ledger,
    export_activity_log, export_geolocation_report, export_transaction_log,
    export_error_log, export_generated_rewards, generate_export_filename
)
from fam.models.audit import get_transaction_log, ACTION_LABELS
from fam.utils.log_reader import parse_log_file
from fam.utils.logging_config import get_log_path
from fam.ui.styles import (
    WHITE, LIGHT_GRAY, PRIMARY_GREEN, HARVEST_GOLD, SUBTITLE_GRAY,
    ACCENT_GREEN, BACKGROUND, TEXT_COLOR, MEDIUM_GRAY, WARNING_COLOR, ERROR_COLOR,
    FIELD_LABEL_BG
)
from fam.ui.widgets.summary_card import SummaryCard, SummaryRow
from fam.ui.helpers import (
    make_field_label, make_item, configure_table, CheckableComboBox,
    DateRangeWidget, NoScrollComboBox
)

logger = logging.getLogger('fam.ui.reports_screen')
from fam.utils.money import cents_to_dollars


class ReportsScreen(QWidget):
    """Reports and Exports screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._vendor_data = []
        self._match_data = []
        self._ledger_data = []
        self._activity_data = []
        self._geo_data = []
        self._txn_log_data = []
        self._error_log_data = []
        self._rewards_data = []
        self._error_log_loaded = False
        self._chart_pie_data = []
        self._chart_trend_data = []
        self._chart_fmnp_data = []
        self._chart_traffic_data = []
        self._chart_vendor_match = []
        self._populating = False
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        title = QLabel("Reports & Exports")
        title.setObjectName("screen_title")
        layout.addWidget(title)

        # ── Filter bar — 4 checkable dropdowns ───────────────────
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
        filter_layout.setSpacing(6)

        # Compact label style for the filter row
        _filter_label_ss = (
            f"background-color: {FIELD_LABEL_BG}; border: 1px solid #D5D2CB;"
            " border-radius: 6px; padding: 4px 8px;"
            " font-weight: bold; font-size: 12px; color: #555;"
        )
        # Compact combo style
        _filter_combo_ss = "font-size: 11px;"

        def _flbl(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(_filter_label_ss)
            return lbl

        # Date range filter
        filter_layout.addWidget(_flbl("Date"))
        self.date_range = DateRangeWidget()
        self.date_range.setStyleSheet(_filter_combo_ss)
        self.date_range.range_changed.connect(self._on_filter_changed)
        filter_layout.addWidget(self.date_range, 1)

        # Market filter
        filter_layout.addWidget(_flbl("Market"))
        self.market_combo = CheckableComboBox(placeholder="All Markets")
        self.market_combo.setStyleSheet(_filter_combo_ss)
        self.market_combo.selection_changed.connect(self._on_filter_changed)
        filter_layout.addWidget(self.market_combo, 1)

        # Vendor filter
        filter_layout.addWidget(_flbl("Vendor"))
        self.vendor_combo = CheckableComboBox(placeholder="All Vendors")
        self.vendor_combo.setStyleSheet(_filter_combo_ss)
        self.vendor_combo.selection_changed.connect(self._on_filter_changed)
        filter_layout.addWidget(self.vendor_combo, 1)

        # Payment Type filter
        filter_layout.addWidget(_flbl("Type"))
        self.pay_type_combo = CheckableComboBox(placeholder="All Types")
        self.pay_type_combo.setStyleSheet(_filter_combo_ss)
        self.pay_type_combo.selection_changed.connect(self._on_filter_changed)
        filter_layout.addWidget(self.pay_type_combo, 1)

        layout.addWidget(filter_frame)

        # ── Summary cards ────────────────────────────────────────
        self.summary_row = SummaryRow()
        self.summary_row.add_card("total_receipts", "Total Receipts")
        self.summary_row.add_card("customer_paid", "Customer Paid")
        self.summary_row.add_card("fam_match", "FAM Match", highlight=True)
        self.summary_row.add_card("fmnp_total", "FMNP Checks", highlight=True)
        # FAM Absorbed (Unallocated Funds, schema v25+) — losses FAM
        # eats when an adjustment finds a customer was undercharged
        # but the customer has already left the market.  Shown as a
        # distinct tile so it's clearly NOT lumped in with the regular
        # FAM Match contribution (which is a multiplier on what the
        # customer actually paid, not pure absorption).
        self.summary_row.add_card("fam_absorbed", "FAM Absorbed",
                                  highlight=True)
        # v2.0.7+ denomination-integrity: Customer Forfeit summary.
        # Mirrors the Customer Forfeit column in Vendor Reimbursement
        # + Detailed Ledger and the Customer Forfeit card on the
        # Payment Screen.  The math identity reads:
        #   Total Receipts =
        #     Customer Paid + FAM Match - Customer Forfeit
        #     + FAM Absorbed + FMNP_External
        # Without this card the header sums diverge from the per-row
        # values (per-row Customer Paid is denomination-true, so the
        # header CUSTOMER PAID is also denomination-true; the forfeit
        # subtraction term needs its own visible tile).
        self.summary_row.add_card("customer_forfeit", "Customer Forfeit")

        layout.addWidget(self.summary_row)

        # ── Tabs for reports ─────────────────────────────────────
        self.tabs = QTabWidget()

        # Vendor Reimbursement tab
        self.vendor_table = QTableWidget()
        configure_table(self.vendor_table, resizable=True)

        vendor_tab = QWidget()
        vl = QVBoxLayout(vendor_tab)
        vl.addWidget(self.vendor_table)
        export_btn1 = QPushButton("Export Vendor Reimbursement CSV")
        export_btn1.setObjectName("secondary_btn")
        export_btn1.clicked.connect(lambda: self._export("vendor_reimbursement"))
        vl.addWidget(export_btn1)
        self.tabs.addTab(vendor_tab, "Vendor Reimbursement")

        # FAM Match Report tab
        # The "FAM Absorbed" column (4th) is non-zero ONLY for the
        # 'Unallocated Funds' system method — for every other row it
        # shows $0.  Keeping it as a column on every row (rather than
        # a separate report) keeps the grid uniform and the export
        # CSV stable in shape regardless of whether any losses
        # occurred during the filter window.
        self.match_table = QTableWidget()
        self.match_table.setColumnCount(4)
        self.match_table.setHorizontalHeaderLabels(
            ["Payment Method", "Total Allocated",
             "Total FAM Match", "FAM Absorbed"]
        )
        configure_table(self.match_table, resizable=True)

        match_tab = QWidget()
        sl = QVBoxLayout(match_tab)
        sl.addWidget(self.match_table)
        export_btn2 = QPushButton("Export FAM Match Report CSV")
        export_btn2.setObjectName("secondary_btn")
        export_btn2.clicked.connect(lambda: self._export("fam_match_report"))
        sl.addWidget(export_btn2)
        self.tabs.addTab(match_tab, "FAM Match Report")

        # Detailed Ledger tab.
        # v2.0.6: added Zip Code column (between Customer and Vendor)
        # so coordinators can correlate which customer demographics
        # used which payment methods at which vendors.
        self.ledger_table = QTableWidget()
        # v2.0.7 (schema v36): added "Customer Forfeit" column for
        # Phase B token-value forfeit visibility.  Always 0 except
        # when a customer hands a denomination unit larger than
        # the receipt's remaining capacity.
        self.ledger_table.setColumnCount(11)
        self.ledger_table.setHorizontalHeaderLabels(
            ["Transaction ID", "Timestamp", "Customer", "Zip Code",
             "Vendor", "Receipt Total",
             "Customer Paid", "FAM Match", "Customer Forfeit",
             "Status", "Payment Methods"]
        )
        configure_table(self.ledger_table, resizable=True)

        ledger_tab = QWidget()
        ll = QVBoxLayout(ledger_tab)
        ll.addWidget(self.ledger_table)
        export_btn3 = QPushButton("Export Detailed Ledger CSV")
        export_btn3.setObjectName("secondary_btn")
        export_btn3.clicked.connect(lambda: self._export("detailed_ledger"))
        ll.addWidget(export_btn3)
        self.tabs.addTab(ledger_tab, "Detailed Ledger")

        # Transaction Log tab (human-friendly view of audit data)
        self.tabs.addTab(self._build_transaction_log_tab(), "Transaction Log")

        # Activity Log tab
        self.activity_table = QTableWidget()
        self.activity_table.setColumnCount(10)
        self.activity_table.setHorizontalHeaderLabels(
            ["Timestamp", "Action", "Table", "Record ID", "Field",
             "Old Value", "New Value", "Reason", "Notes", "Changed By"]
        )
        configure_table(self.activity_table, resizable=True)

        activity_tab = QWidget()
        al = QVBoxLayout(activity_tab)
        al.addWidget(self.activity_table)
        export_btn4 = QPushButton("Export Activity Log CSV")
        export_btn4.setObjectName("secondary_btn")
        export_btn4.clicked.connect(lambda: self._export("activity_log"))
        al.addWidget(export_btn4)
        self.tabs.addTab(activity_tab, "Activity Log")

        # Generated Rewards tab (v1.9.10+)
        # Customer-facing rewards add-on — derived view, not stored.
        # Hidden when the rewards feature flag is off; otherwise shows
        # one row per (customer order × rule that fired) recomputed
        # against the current state of the DB on every refresh.
        # Voided/adjusted transactions reflow naturally because the
        # underlying source-total query excludes voided txns.
        #
        # Disclaimer banner above the table reminds the reader this
        # is NOT a financial report — rewards are physical scrip
        # the FAM rep handed the customer separately and have zero
        # impact on vendor reimbursement / FAM match.
        rewards_tab = QWidget()
        rewards_outer = QVBoxLayout(rewards_tab)
        rewards_banner = QLabel(
            "<i>Rewards are a customer-facing marketing/loyalty "
            "add-on — physical scrip the FAM rep handed the "
            "customer separately.  These rows are NOT part of "
            "vendor reimbursement, NOT part of FAM match, and "
            "NOT linked to any payment_line_item.  Each row is a "
            "<b>write-once historical snapshot</b> of what was "
            "given at payment-confirmation time — voids and "
            "adjustments do NOT modify or remove these rows.</i>"
        )
        rewards_banner.setWordWrap(True)
        rewards_banner.setStyleSheet(
            "padding: 8px; background-color: #F3E5F5; "
            "border: 1px solid #7B1FA2; border-radius: 6px; "
            "font-size: 12px; color: #4A148C;")
        rewards_outer.addWidget(rewards_banner)
        # v2.0.6: Zip Code column lets coordinators see which customer
        # demographics earned which rewards (the rewards rows already
        # carry customer_label; pulling zip from the customer_order is
        # a single-hop join).
        self.rewards_table = QTableWidget()
        self.rewards_table.setColumnCount(11)
        self.rewards_table.setHorizontalHeaderLabels([
            "Market Name", "Date", "Customer", "Zip Code",
            "Source Method", "Source Total",
            "Threshold",
            "Reward Method", "Reward Unit",
            "Units Earned", "Reward Total",
        ])
        configure_table(self.rewards_table, resizable=True)
        rewards_outer.addWidget(self.rewards_table)
        rewards_btn_row = QHBoxLayout()
        export_rewards_btn = QPushButton("Export Generated Rewards CSV")
        export_rewards_btn.setObjectName("secondary_btn")
        export_rewards_btn.clicked.connect(
            lambda: self._export("generated_rewards"))
        rewards_btn_row.addWidget(export_rewards_btn)
        rewards_btn_row.addStretch()
        rewards_outer.addLayout(rewards_btn_row)
        self.tabs.addTab(rewards_tab, "Generated Rewards")

        # Geolocation Report tab
        geo_tab = QWidget()
        geo_outer = QVBoxLayout(geo_tab)
        geo_outer.setContentsMargins(0, 0, 0, 0)
        geo_outer.setSpacing(8)

        geo_scroll = QScrollArea()
        geo_scroll.setWidgetResizable(True)
        geo_scroll.setFrameShape(QFrame.NoFrame)
        geo_scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {BACKGROUND}; }}"
        )

        geo_content = QWidget()
        geo_layout = QVBoxLayout(geo_content)
        geo_layout.setContentsMargins(8, 8, 8, 8)
        geo_layout.setSpacing(12)

        self.geo_table = QTableWidget()
        self.geo_table.setColumnCount(5)
        self.geo_table.setHorizontalHeaderLabels(
            ["Zip Code", "# Customers", "# Receipts",
             "Total Spend", "Total FAM Match"]
        )
        configure_table(self.geo_table, resizable=True)
        geo_layout.addWidget(self.geo_table)

        geo_chart_header = QLabel("Customer Distribution by Zip Code")
        geo_chart_header.setObjectName("section_header")
        geo_layout.addWidget(geo_chart_header)

        self._geo_figure = Figure(figsize=(12, 5), dpi=100, facecolor=BACKGROUND)
        self._geo_canvas = FigureCanvasQTAgg(self._geo_figure)
        self._geo_canvas.setMinimumHeight(420)
        geo_layout.addWidget(self._geo_canvas)

        geo_layout.addStretch()
        geo_scroll.setWidget(geo_content)
        geo_outer.addWidget(geo_scroll)

        geo_btn_row = QHBoxLayout()
        export_geo_btn = QPushButton("Export Geolocation CSV")
        export_geo_btn.setObjectName("secondary_btn")
        export_geo_btn.clicked.connect(lambda: self._export("geolocation"))
        geo_btn_row.addWidget(export_geo_btn)

        heatmap_btn = QPushButton("View Heat Map in Browser")
        heatmap_btn.setObjectName("secondary_btn")
        heatmap_btn.clicked.connect(self._open_heatmap)
        geo_btn_row.addWidget(heatmap_btn)

        geo_btn_row.addStretch()
        geo_outer.addLayout(geo_btn_row)

        self.tabs.addTab(geo_tab, "Geolocation")

        # Charts tab
        charts_tab = QWidget()
        charts_outer = QVBoxLayout(charts_tab)
        charts_outer.setContentsMargins(0, 0, 0, 0)
        charts_outer.setSpacing(8)

        chart_scroll = QScrollArea()
        chart_scroll.setWidgetResizable(True)
        chart_scroll.setFrameShape(QFrame.NoFrame)
        chart_scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {BACKGROUND}; }}")

        chart_content = QWidget()
        chart_layout = QVBoxLayout(chart_content)
        chart_layout.setContentsMargins(8, 8, 8, 8)
        chart_layout.setSpacing(12)

        # Pie chart
        pie_header = QLabel("Payment Methods Breakdown")
        pie_header.setObjectName("section_header")
        chart_layout.addWidget(pie_header)

        self._pie_figure = Figure(figsize=(12, 5), dpi=100, facecolor=BACKGROUND)
        self._pie_canvas = FigureCanvasQTAgg(self._pie_figure)
        self._pie_canvas.setMinimumHeight(420)
        chart_layout.addWidget(self._pie_canvas)

        # Line chart
        line_header = QLabel("Totals & FAM Match Over Time")
        line_header.setObjectName("section_header")
        chart_layout.addWidget(line_header)

        self._line_figure = Figure(figsize=(12, 5), dpi=100, facecolor=BACKGROUND)
        self._line_canvas = FigureCanvasQTAgg(self._line_figure)
        self._line_canvas.setMinimumHeight(420)
        chart_layout.addWidget(self._line_canvas)

        # Customer traffic chart
        traffic_header = QLabel("Customer & Receipt Traffic Over Time")
        traffic_header.setObjectName("section_header")
        chart_layout.addWidget(traffic_header)

        self._traffic_figure = Figure(figsize=(12, 5), dpi=100, facecolor=BACKGROUND)
        self._traffic_canvas = FigureCanvasQTAgg(self._traffic_figure)
        self._traffic_canvas.setMinimumHeight(420)
        chart_layout.addWidget(self._traffic_canvas)

        # Vendor match distribution chart
        vendor_match_header = QLabel("FAM Match by Vendor")
        vendor_match_header.setObjectName("section_header")
        chart_layout.addWidget(vendor_match_header)

        self._vendor_match_figure = Figure(figsize=(12, 5), dpi=100, facecolor=BACKGROUND)
        self._vendor_match_canvas = FigureCanvasQTAgg(self._vendor_match_figure)
        self._vendor_match_canvas.setMinimumHeight(420)
        chart_layout.addWidget(self._vendor_match_canvas)

        chart_layout.addStretch()
        chart_scroll.setWidget(chart_content)
        charts_outer.addWidget(chart_scroll)

        export_charts_btn = QPushButton("Export Charts as PNG")
        export_charts_btn.setObjectName("secondary_btn")
        export_charts_btn.clicked.connect(self._export_charts)
        charts_outer.addWidget(export_charts_btn)

        self.tabs.addTab(charts_tab, "Charts")

        # Error Log tab (parsed from fam_manager.log file)
        self.tabs.addTab(self._build_error_log_tab(), "Error Log")

        # Lazy-load error log when its tab is selected
        self.tabs.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(self.tabs)

    # ------------------------------------------------------------------
    # Populate filters
    # ------------------------------------------------------------------
    def refresh(self):
        self._populate_filters()
        self._generate_reports()

    def _populate_filters(self):
        """Load all four filter dropdowns from DB. Suppresses auto-regeneration."""
        self._populating = True

        # Date range — set calendar bounds from market_days min/max dates
        days = get_all_market_days()
        if days:
            all_dates = [d['date'] for d in days]
            self.date_range.set_date_bounds(min(all_dates), max(all_dates))

        # Market
        markets = get_all_markets()
        self.market_combo.set_items([(m['name'], m['id']) for m in markets])

        # Vendor
        vendors = get_all_vendors(active_only=False)
        self.vendor_combo.set_items([(v['name'], v['id']) for v in vendors])

        # Payment Type
        methods = get_all_payment_methods(active_only=False)
        self.pay_type_combo.set_items([(pm['name'], pm['name']) for pm in methods])

        self._populating = False

    def _on_filter_changed(self):
        """Re-generate reports when any filter checkbox changes."""
        if not self._populating:
            self._generate_reports()

    # ------------------------------------------------------------------
    # Build dynamic WHERE clause from all 4 multi-select filters
    # ------------------------------------------------------------------
    @staticmethod
    def _in_clause(column, values):
        """Return '(column IN (?, ?, ...))' and param list, or (None, [])."""
        if not values:
            return None, []
        placeholders = ", ".join("?" for _ in values)
        return f"{column} IN ({placeholders})", list(values)

    def _build_where(self):
        """Return (where_sql, params) reflecting all active filters.

        Empty checked_data() means "All" → no restriction for that filter.
        """
        clauses = ["t.status IN ('Confirmed', 'Adjusted')"]
        params = []

        # Date range filter
        from_date, to_date = self.date_range.get_date_range()
        if from_date and to_date:
            clauses.append("md.date BETWEEN ? AND ?")
            params.extend([from_date, to_date])

        # Market filter (multi)
        market_ids = self.market_combo.checked_data()
        if market_ids:
            sql, p = self._in_clause("md.market_id", market_ids)
            clauses.append(sql)
            params.extend(p)

        # Vendor filter (multi)
        vendor_ids = self.vendor_combo.checked_data()
        if vendor_ids:
            sql, p = self._in_clause("t.vendor_id", vendor_ids)
            clauses.append(sql)
            params.extend(p)

        # Payment Type filter (multi — sub-query)
        pay_types = self.pay_type_combo.checked_data()
        if pay_types:
            placeholders = ", ".join("?" for _ in pay_types)
            clauses.append(
                f"t.id IN (SELECT pli.transaction_id FROM payment_line_items pli "
                f"WHERE pli.method_name_snapshot IN ({placeholders}))"
            )
            params.extend(pay_types)

        where = "WHERE " + " AND ".join(clauses)
        return where, params

    def _build_fmnp_where(self):
        """Return (where_sql, params) for fmnp_entries queries, applying active filters."""
        clauses = ["fe.status = 'Active'"]
        params = []

        from_date, to_date = self.date_range.get_date_range()
        if from_date and to_date:
            clauses.append("md.date BETWEEN ? AND ?")
            params.extend([from_date, to_date])

        market_ids = self.market_combo.checked_data()
        if market_ids:
            sql, p = self._in_clause("md.market_id", market_ids)
            clauses.append(sql)
            params.extend(p)

        vendor_ids = self.vendor_combo.checked_data()
        if vendor_ids:
            sql, p = self._in_clause("fe.vendor_id", vendor_ids)
            clauses.append(sql)
            params.extend(p)

        # If payment type filter is active and FMNP not included, exclude all
        pay_types = self.pay_type_combo.checked_data()
        if pay_types and 'FMNP' not in pay_types:
            return "WHERE 1=0", []

        if clauses:
            return "WHERE " + " AND ".join(clauses), params
        return "", params

    # ------------------------------------------------------------------
    # Generate all three reports
    # ------------------------------------------------------------------
    def _generate_reports(self):
        conn = get_connection()
        where, params = self._build_where()
        fmnp_where, fmnp_params = self._build_fmnp_where()

        # v2.0.1: open a single read transaction so every SELECT
        # below sees the SAME WAL snapshot.  Without this, a
        # background sync write or another screen's mutation can
        # land between independent SELECTs and the Vendor /
        # Detailed Ledger / Match Report tables end up reflecting
        # different points in time.
        _opened_read_txn = False
        try:
            conn.execute("BEGIN")
            _opened_read_txn = True
        except Exception:
            logger.debug("Could not BEGIN read transaction in "
                         "_generate_reports", exc_info=True)

        # ── Vendor reimbursement ─────────────────────────────────
        # Check if v19 vendor columns exist
        try:
            conn.execute("SELECT check_payable_to FROM vendors LIMIT 1")
            cpt_expr = "COALESCE(v.check_payable_to, v.name)"
            addr_cols = ", v.street, v.city, v.state, v.zip_code"
        except Exception:
            cpt_expr = "v.name"
            addr_cols = ", NULL AS street, NULL AS city, NULL AS state, NULL AS zip_code"

        vendor_rows = conn.execute(f"""
            SELECT v.name AS vendor,
                   {cpt_expr} AS check_payable_to,
                   m.name AS market_name,
                   COALESCE(SUM(t.receipt_total), 0) AS gross_sales,
                   GROUP_CONCAT(DISTINCT md.date) AS transaction_dates
                   {addr_cols}
            FROM transactions t
            JOIN vendors v ON t.vendor_id = v.id
            JOIN market_days md ON t.market_day_id = md.id
            JOIN markets m ON md.market_id = m.id
            {where}
            GROUP BY m.id, v.id, v.name
            ORDER BY m.name, v.name
        """, params).fetchall()

        # Per-method physical-instrument totals + per-vendor FAM Match.
        # See ``_collect_vendor_reimbursement`` for the column-semantics
        # rationale (v1.9.10+: per-method columns show customer_charged,
        # FAM Match is its own dedicated column).
        method_rows = conn.execute(f"""
            SELECT v.name AS vendor,
                   m.name AS market_name,
                   pl.method_name_snapshot AS method,
                   COALESCE(SUM(pl.customer_charged), 0) AS customer_total,
                   COALESCE(SUM(pl.match_amount), 0) AS match_total,
                   COALESCE(SUM(pl.method_amount), 0) AS method_total,
                   COALESCE(SUM(pl.customer_forfeit_cents), 0) AS forfeit_total
            FROM payment_line_items pl
            JOIN transactions t ON pl.transaction_id = t.id
            JOIN vendors v ON t.vendor_id = v.id
            JOIN market_days md ON t.market_day_id = md.id
            JOIN markets m ON md.market_id = m.id
            {where}
            GROUP BY m.id, v.id, v.name, pl.method_name_snapshot
        """, params).fetchall()

        # Per-method column shows customer_charged for ordinary
        # methods; for the system-managed Unallocated Funds row it
        # shows ``method_amount`` (= the absorbed loss) so the row
        # identity ``Σ method-cols + FAM Match + FMNP_External =
        # Total Due`` holds.  Mirrors the same fix in
        # ``_collect_vendor_reimbursement``.
        from fam.models.payment_method import UNALLOCATED_FUNDS_NAME
        all_methods = sorted({r['method'] for r in method_rows})
        method_by_vendor: dict[tuple, dict[str, float]] = {}
        fam_match_by_vendor: dict[tuple, int] = {}
        # v2.0.7 (schema v36): per-vendor sum of Phase B customer-
        # side forfeit.  Surfaced as a "Customer Forfeit" column —
        # see _collect_vendor_reimbursement for the full rationale.
        customer_forfeit_by_vendor: dict[tuple, int] = {}
        for r in method_rows:
            key = (r['market_name'], r['vendor'])
            if r['method'] == UNALLOCATED_FUNDS_NAME:
                value = r['method_total']
            else:
                # v2.0.7+ denomination-integrity: per-method column
                # = customer's actual denomination payment
                # (customer_charged + forfeit).  Mirrors
                # _collect_vendor_reimbursement in
                # fam/sync/data_collector.py — see that file for
                # the full rationale.  The Customer Forfeit column
                # subtracts the over-tender so the row identity is:
                #   Σ(method-cols) + FAM Match - Customer Forfeit
                #     = Total Due to Vendor
                value = (r['customer_total']
                         + (r['forfeit_total'] or 0))
            method_by_vendor.setdefault(key, {})[r['method']] = value
            fam_match_by_vendor[key] = (
                fam_match_by_vendor.get(key, 0) + r['match_total'])
            customer_forfeit_by_vendor[key] = (
                customer_forfeit_by_vendor.get(key, 0)
                + (r['forfeit_total'] or 0))

        # Build combined vendor dict from transactions
        def _build_addr(row):
            parts = []
            street = row['street'] if row['street'] else ''
            city = row['city'] if row['city'] else ''
            state = row['state'] if row['state'] else ''
            zc = row['zip_code'] if row['zip_code'] else ''
            if street:
                parts.append(street)
            csz = ', '.join(filter(None, [city, state]))
            if zc:
                csz = f"{csz} {zc}".strip()
            if csz:
                parts.append(csz)
            return ', '.join(parts)

        # v1.9.10 follow-up (2026-05-01): keep money in integer
        # cents internally; emit floats only at the final pass.
        # See _collect_vendor_reimbursement in data_collector.py
        # for the same fix and the float-drift rationale.
        vendor_dict = {}
        for r in vendor_rows:
            month_str = ''
            if r['transaction_dates']:
                from datetime import datetime
                month_str = datetime.strptime(r['transaction_dates'][:7], '%Y-%m').strftime('%B')
            key = (r['market_name'], r['vendor'])
            vendor_dict[key] = {
                'market_name': r['market_name'] or '',
                'vendor': r['vendor'],
                'check_payable_to': r['check_payable_to'],
                'address': _build_addr(r),
                'month': month_str,
                'dates': r['transaction_dates'] or '',
                '_total_due_cents': r['gross_sales'],
                '_fam_match_cents': fam_match_by_vendor.get(key, 0),
                '_customer_forfeit_cents':
                    customer_forfeit_by_vendor.get(key, 0),
                '_method_cents': dict(method_by_vendor.get(key, {})),
                '_fmnp_external_cents': 0,
            }

        # Merge external FMNP entries (from fmnp_entries table)
        fmnp_vendor_rows = conn.execute(f"""
            SELECT v.name AS vendor,
                   {cpt_expr} AS check_payable_to,
                   m.name AS market_name,
                   COALESCE(SUM(fe.amount), 0) AS fmnp_entry_total,
                   GROUP_CONCAT(DISTINCT md.date) AS fmnp_dates
                   {addr_cols}
            FROM fmnp_entries fe
            JOIN vendors v ON fe.vendor_id = v.id
            JOIN market_days md ON fe.market_day_id = md.id
            JOIN markets m ON md.market_id = m.id
            {fmnp_where}
            GROUP BY m.id, v.id, v.name
        """, fmnp_params).fetchall()

        for r in fmnp_vendor_rows:
            key = (r['market_name'], r['vendor'])
            fmnp_cents = r['fmnp_entry_total']
            if key in vendor_dict:
                vendor_dict[key]['_fmnp_external_cents'] = fmnp_cents
                vendor_dict[key]['_total_due_cents'] += fmnp_cents
                existing = set(vendor_dict[key]['dates'].split(',')) \
                    if vendor_dict[key]['dates'] else set()
                new_dates = set((r['fmnp_dates'] or '').split(','))
                all_dates = (existing | new_dates) - {''}
                vendor_dict[key]['dates'] = ','.join(sorted(all_dates))
            else:
                month_str = ''
                if r['fmnp_dates']:
                    from datetime import datetime
                    month_str = datetime.strptime(r['fmnp_dates'][:7], '%Y-%m').strftime('%B')
                vendor_dict[key] = {
                    'market_name': r['market_name'] or '',
                    'vendor': r['vendor'],
                    'check_payable_to': r['check_payable_to'],
                    'address': _build_addr(r),
                    'month': month_str,
                    'dates': r['fmnp_dates'] or '',
                    '_total_due_cents': fmnp_cents,
                    '_fam_match_cents': 0,
                    '_customer_forfeit_cents': 0,
                    '_method_cents': {},
                    '_fmnp_external_cents': fmnp_cents,
                }

        # Final emission: cents → dollars, exactly once per field.
        vendor_list = []
        for key in sorted(vendor_dict.keys()):
            rc = vendor_dict[key]
            vendor_list.append({
                'market_name': rc['market_name'],
                'vendor': rc['vendor'],
                'check_payable_to': rc['check_payable_to'],
                'address': rc['address'],
                'month': rc['month'],
                'dates': rc['dates'],
                'total_due': cents_to_dollars(rc['_total_due_cents']),
                'fam_match': cents_to_dollars(rc['_fam_match_cents']),
                'customer_forfeit': cents_to_dollars(
                    rc.get('_customer_forfeit_cents', 0)),
                'methods': {m: cents_to_dollars(c)
                             for m, c in rc['_method_cents'].items()},
                'fmnp_external': cents_to_dollars(
                    rc['_fmnp_external_cents']),
            })

        # Build dynamic table columns.
        # v1.9.10 layout: 'FAM Match' is its own column between
        # 'Total Due to Vendor' and the per-method columns so the
        # manager can see FAM's contribution at a glance without
        # having to subtract.  Per-method columns now show
        # customer_charged (physical-instrument count × face value).
        # v2.0.7 (schema v36): 'Customer Forfeit' appears AFTER
        # the per-method + FMNP-External columns so the row reads
        # as (vendor reimbursement breakdown) → (customer-side
        # forfeit) → (admin metadata).  Phase A (FAM match
        # forfeit) is NOT counted here — those are unused FAM
        # funds, not customer losses.  Only Phase B (token-value
        # forfeit) lands in this column.
        fixed_cols = ['Market Name', 'Vendor',
                      'Month', 'Date(s)', 'Total Due to Vendor',
                      'FAM Match']
        method_cols = list(all_methods)
        tail_cols = ['FMNP (External)', 'Customer Forfeit',
                     'Check Payable To', 'Address']
        all_cols = fixed_cols + method_cols + tail_cols

        self.vendor_table.setSortingEnabled(False)
        self.vendor_table.setColumnCount(len(all_cols))
        self.vendor_table.setHorizontalHeaderLabels(all_cols)
        configure_table(self.vendor_table, resizable=True)
        self.vendor_table.setRowCount(len(vendor_list))

        self._vendor_data = []
        total_gross = 0
        total_fmnp = 0
        for i, v in enumerate(vendor_list):
            total_gross += v['total_due'] - v['fmnp_external']
            total_fmnp += v['fmnp_external']

            col = 0
            self.vendor_table.setItem(i, col, make_item(v['market_name'])); col += 1
            self.vendor_table.setItem(i, col, make_item(v['vendor'])); col += 1
            self.vendor_table.setItem(i, col, make_item(v['month'])); col += 1
            self.vendor_table.setItem(i, col, make_item(v['dates'])); col += 1
            self.vendor_table.setItem(i, col, make_item(
                f"${v['total_due']:.2f}", v['total_due'])); col += 1
            self.vendor_table.setItem(i, col, make_item(
                f"${v['fam_match']:.2f}", v['fam_match'])); col += 1

            for m in method_cols:
                amt = v['methods'].get(m, 0)
                self.vendor_table.setItem(i, col, make_item(
                    f"${amt:.2f}", amt)); col += 1

            self.vendor_table.setItem(i, col, make_item(
                f"${v['fmnp_external']:.2f}", v['fmnp_external'])); col += 1
            self.vendor_table.setItem(i, col, make_item(
                f"${v['customer_forfeit']:.2f}",
                v['customer_forfeit'])); col += 1
            self.vendor_table.setItem(i, col, make_item(v['check_payable_to'])); col += 1
            self.vendor_table.setItem(i, col, make_item(v['address']))

            row_data = {
                'Market Name': v['market_name'],
                'Vendor': v['vendor'],
                'Month': v['month'],
                'Date(s)': v['dates'],
                'Total Due to Vendor': v['total_due'],
                'FAM Match': v['fam_match'],
            }
            for m in method_cols:
                row_data[m] = v['methods'].get(m, 0)
            row_data['FMNP (External)'] = v['fmnp_external']
            row_data['Customer Forfeit'] = v['customer_forfeit']
            row_data['Check Payable To'] = v['check_payable_to']
            row_data['Address'] = v['address']
            self._vendor_data.append(row_data)
        self.vendor_table.resizeColumnsToContents()
        self.vendor_table.setSortingEnabled(True)

        # ── FAM Match by payment method ──────────────────────────
        # The shared WHERE filters at the transaction level.  For the
        # FAM-Match query (grouped by payment method) we also need to
        # filter individual payment_line_items rows directly so that
        # selecting e.g. "Cash" doesn't still show SNAP rows.
        pay_types = self.pay_type_combo.checked_data()
        match_extra = ""
        match_extra_params = []
        if pay_types:
            ph = ", ".join("?" for _ in pay_types)
            match_extra = f"AND pl.method_name_snapshot IN ({ph})"
            match_extra_params = list(pay_types)

        match_rows = conn.execute(f"""
            SELECT pl.method_name_snapshot as method,
                   SUM(pl.method_amount) as total_allocated,
                   SUM(pl.match_amount) as total_fam_match,
                   COALESCE(SUM(pl.customer_forfeit_cents), 0)
                       as total_customer_forfeit
            FROM payment_line_items pl
            JOIN transactions t ON pl.transaction_id = t.id
            JOIN market_days md ON t.market_day_id = md.id
            {where} {match_extra}
            GROUP BY pl.method_name_snapshot
            ORDER BY pl.method_name_snapshot
        """, params + match_extra_params).fetchall()

        self._match_data = []
        self.match_table.setSortingEnabled(False)
        self.match_table.setRowCount(len(match_rows))
        total_customer = 0
        total_fam_match = 0
        total_fam_absorbed = 0
        # v2.0.7+ denomination-integrity: aggregate Phase B forfeit
        # for the Customer Forfeit summary card.  Powers the math
        # identity in the header tile row:
        #   Total Receipts =
        #     Customer Paid + FAM Match - Customer Forfeit
        #     + FAM Absorbed + FMNP_External
        total_customer_forfeit = 0
        # v2.0.1: the "FMNP Checks" summary tile must include
        # FMNP captured via Payment Screen (Path 1) AND via the
        # FMNP Entry screen (Path 2 — fmnp_external below).
        # Earlier versions tallied only Path 2, showing $0 on
        # markets that record FMNP exclusively through the
        # Payment Screen.  Path 1 physical face value =
        # (method_amount - match_amount) = customer_charged.
        total_fmnp_path1 = 0
        from fam.models.payment_method import UNALLOCATED_FUNDS_NAME
        for i, r in enumerate(match_rows):
            allocated = cents_to_dollars(r['total_allocated'])
            fam_match = cents_to_dollars(r['total_fam_match'])
            # v2.0.7+ denomination-integrity: customer_paid for the
            # header CUSTOMER PAID card must be denomination-true
            # (= what the customer literally handed over, including
            # the over-tender that gets forfeited).  Without this
            # the header card showed $54.42 while the per-row
            # Customer Paid columns now sum to $62.97 — visibly
            # inconsistent.  Adding the forfeit here keeps the
            # card in lockstep with the per-row values.
            forfeit = cents_to_dollars(r['total_customer_forfeit'])
            # Unallocated Funds method_amount IS the absorbed loss —
            # FAM funds the entire amount because the customer didn't
            # pay it.  Keep it OUT of "customer_paid" (the customer
            # paid $0 of it) and OUT of "total_fam_match" (it isn't a
            # match — it's pure absorption).
            is_absorbed = (r['method'] == UNALLOCATED_FUNDS_NAME)
            fam_absorbed = allocated if is_absorbed else 0
            if is_absorbed:
                total_fam_absorbed += fam_absorbed
            else:
                total_customer += (allocated - fam_match + forfeit)
                total_fam_match += fam_match
                total_customer_forfeit += forfeit
                if r['method'] == 'FMNP':
                    total_fmnp_path1 += (allocated - fam_match + forfeit)

            self.match_table.setItem(i, 0, make_item(r['method']))
            self.match_table.setItem(i, 1, make_item(f"${allocated:.2f}", allocated))
            self.match_table.setItem(i, 2, make_item(f"${fam_match:.2f}", fam_match))
            self.match_table.setItem(i, 3, make_item(
                f"${fam_absorbed:.2f}", fam_absorbed))

            self._match_data.append({
                'Payment Method': r['method'],
                'Total Allocated': allocated,
                'Total FAM Match': fam_match,
                'FAM Absorbed': fam_absorbed,
            })

        # Add external FMNP entries to FAM Match report
        fmnp_ext_total = conn.execute(f"""
            SELECT COALESCE(SUM(fe.amount), 0) as total
            FROM fmnp_entries fe
            JOIN market_days md ON fe.market_day_id = md.id
            {fmnp_where}
        """, fmnp_params).fetchone()['total']

        fmnp_ext_dollars = cents_to_dollars(fmnp_ext_total)
        if fmnp_ext_dollars > 0:
            row_idx = self.match_table.rowCount()
            self.match_table.setRowCount(row_idx + 1)
            self.match_table.setItem(row_idx, 0, make_item("FMNP (External)"))
            self.match_table.setItem(row_idx, 1, make_item(
                f"${fmnp_ext_dollars:.2f}", fmnp_ext_dollars))
            self.match_table.setItem(row_idx, 2, make_item(
                "$0.00", 0))
            self.match_table.setItem(row_idx, 3, make_item(
                "$0.00", 0))
            # No FAM match — external checks are vendor reimbursements only

            self._match_data.append({
                'Payment Method': 'FMNP (External)',
                'Total Allocated': fmnp_ext_dollars,
                'Total FAM Match': 0,
                'FAM Absorbed': 0,
            })
        self.match_table.resizeColumnsToContents()
        self.match_table.setSortingEnabled(True)

        # Update summary cards.  ``total_fmnp`` aggregates Path 2
        # (fmnp_entries.amount, accumulated above) and Path 1
        # (FMNP captured via Payment Screen — physical face
        # value = customer_charged — accumulated as
        # ``total_fmnp_path1``).
        self.summary_row.update_card("total_receipts", f"${total_gross:.2f}")
        self.summary_row.update_card("customer_paid", f"${total_customer:.2f}")
        self.summary_row.update_card("fam_match", f"${total_fam_match:.2f}")
        self.summary_row.update_card(
            "fmnp_total", f"${total_fmnp + total_fmnp_path1:.2f}")
        self.summary_row.update_card("fam_absorbed",
                                     f"${total_fam_absorbed:.2f}")
        self.summary_row.update_card(
            "customer_forfeit", f"${total_customer_forfeit:.2f}")

        # ── Detailed ledger ──────────────────────────────────────
        # v2.0.7+ denomination-integrity:
        #   * customer_paid = customer_charged + forfeit (what
        #     the customer literally handed over).
        #   * Payment Methods column = same value broken down per
        #     method (NOT method_amount, which intermingles FAM
        #     match).  Mirrors _collect_detailed_ledger in
        #     fam/sync/data_collector.py — see that file for the
        #     full rationale.  Reconciliation per row:
        #       Customer Paid + FAM Match - Customer Forfeit
        #         = Receipt Total
        # EXCEPTION — Unallocated Funds (system-managed, schema v25+)
        # records the FAM-absorbed gap from a customer-gone
        # adjustment.  customer_charged = 0 (customer didn't pay
        # it), so the ordinary denomination-true expression would
        # render "Unallocated Funds: $0.00" for every adjusted
        # transaction — hiding the absorbed loss.  CASE carve-out
        # uses method_amount for that one method (matches the per-
        # method carve-out already in the Vendor Reimbursement
        # column).  User-reported 2026-05-07.
        from fam.models.payment_method import UNALLOCATED_FUNDS_NAME
        ledger_rows = conn.execute(f"""
            SELECT t.fam_transaction_id, v.name as vendor,
                   t.receipt_total, t.status, t.created_at,
                   COALESCE(co.customer_label, '') as customer_id,
                   COALESCE(co.zip_code, '') as zip_code,
                   COALESCE(SUM(pl.customer_charged
                                + pl.customer_forfeit_cents), 0)
                       as customer_paid,
                   COALESCE(SUM(pl.match_amount), 0) as fam_match,
                   COALESCE(SUM(pl.customer_forfeit_cents), 0) as customer_forfeit,
                   GROUP_CONCAT(pl.method_name_snapshot || ': $' ||
                       PRINTF('%.2f',
                              CASE WHEN pl.method_name_snapshot
                                        = '{UNALLOCATED_FUNDS_NAME}'
                                   THEN pl.method_amount / 100.0
                                   ELSE (pl.customer_charged
                                         + pl.customer_forfeit_cents)
                                        / 100.0
                              END),
                       ', ') as methods
            FROM transactions t
            JOIN vendors v ON t.vendor_id = v.id
            JOIN market_days md ON t.market_day_id = md.id
            LEFT JOIN customer_orders co ON t.customer_order_id = co.id
            LEFT JOIN payment_line_items pl ON pl.transaction_id = t.id
            {where}
            GROUP BY t.id
            ORDER BY t.fam_transaction_id
        """, params).fetchall()

        self._ledger_data = []
        self.ledger_table.setSortingEnabled(False)
        self.ledger_table.setRowCount(len(ledger_rows))
        for i, r in enumerate(ledger_rows):
            rt = cents_to_dollars(r['receipt_total'])
            cp = cents_to_dollars(r['customer_paid'])
            fm = cents_to_dollars(r['fam_match'])
            cf = cents_to_dollars(r['customer_forfeit'])
            self.ledger_table.setItem(i, 0, make_item(r['fam_transaction_id']))
            self.ledger_table.setItem(i, 1, make_item(r['created_at'] or ''))
            self.ledger_table.setItem(i, 2, make_item(r['customer_id']))
            self.ledger_table.setItem(i, 3, make_item(r['zip_code'] or ''))
            self.ledger_table.setItem(i, 4, make_item(r['vendor']))
            self.ledger_table.setItem(i, 5, make_item(f"${rt:.2f}", rt))
            self.ledger_table.setItem(i, 6, make_item(f"${cp:.2f}", cp))
            self.ledger_table.setItem(i, 7, make_item(f"${fm:.2f}", fm))
            self.ledger_table.setItem(i, 8, make_item(f"${cf:.2f}", cf))
            self.ledger_table.setItem(i, 9, make_item(r['status']))
            self.ledger_table.setItem(i, 10, make_item(r['methods'] or ''))

            self._ledger_data.append({
                'Transaction ID': r['fam_transaction_id'],
                'Timestamp': r['created_at'] or '',
                'Customer': r['customer_id'],
                'Zip Code': r['zip_code'] or '',
                'Vendor': r['vendor'],
                'Receipt Total': rt,
                'Customer Paid': cp,
                'FAM Match': fm,
                'Customer Forfeit': cf,
                'Status': r['status'],
                'Payment Methods': r['methods'] or ''
            })
        self.ledger_table.setSortingEnabled(True)

        # Append external FMNP entries to detailed ledger
        fmnp_ledger_rows = conn.execute(f"""
            SELECT fe.id, v.name as vendor, fe.amount, md.date,
                   fe.check_count, fe.notes, fe.entered_by,
                   fe.created_at
            FROM fmnp_entries fe
            JOIN vendors v ON fe.vendor_id = v.id
            JOIN market_days md ON fe.market_day_id = md.id
            {fmnp_where}
            ORDER BY md.date, fe.id
        """, fmnp_params).fetchall()

        if fmnp_ledger_rows:
            self.ledger_table.setSortingEnabled(False)
            offset = len(ledger_rows)
            self.ledger_table.setRowCount(offset + len(fmnp_ledger_rows))
            for i, r in enumerate(fmnp_ledger_rows):
                row_idx = offset + i
                check_info = (f"FMNP (External) - {r['check_count']} checks"
                              if r['check_count'] else "FMNP (External)")
                amt = cents_to_dollars(r['amount'])
                self.ledger_table.setItem(row_idx, 0, make_item(f"FMNP-{r['id']}"))
                self.ledger_table.setItem(row_idx, 1, make_item(r['created_at'] or ''))
                self.ledger_table.setItem(row_idx, 2, make_item(''))
                # Zip Code (column 3) — empty for external FMNP entries,
                # which aren't tied to a customer order.
                self.ledger_table.setItem(row_idx, 3, make_item(''))
                self.ledger_table.setItem(row_idx, 4, make_item(r['vendor']))
                self.ledger_table.setItem(row_idx, 5, make_item(
                    f"${amt:.2f}", amt))
                self.ledger_table.setItem(row_idx, 6, make_item("$0.00", 0))
                self.ledger_table.setItem(row_idx, 7, make_item(
                    f"${amt:.2f}", amt))
                # Customer Forfeit (col 8) — always $0 for external
                # FMNP entries (vendor matched the check at booth;
                # no denomination forfeit applies).
                self.ledger_table.setItem(row_idx, 8, make_item("$0.00", 0))
                self.ledger_table.setItem(row_idx, 9, make_item("FMNP Entry"))
                self.ledger_table.setItem(row_idx, 10, make_item(check_info))

                self._ledger_data.append({
                    'Transaction ID': f"FMNP-{r['id']}",
                    'Timestamp': r['created_at'] or '',
                    'Customer': '',
                    'Zip Code': '',
                    'Vendor': r['vendor'],
                    'Receipt Total': amt,
                    'Customer Paid': 0,
                    'FAM Match': amt,
                    'Customer Forfeit': 0,
                    'Status': 'FMNP Entry',
                    'Payment Methods': check_info
                })
            self.ledger_table.resizeColumnsToContents()
            self.ledger_table.setSortingEnabled(True)

        # ── Chart data: time-series for trending ──────────────────
        trend_rows = conn.execute(f"""
            SELECT md.date,
                   COALESCE(SUM(t.receipt_total), 0)   AS gross_total,
                   COALESCE(SUM(pl_agg.match_total), 0) AS fam_match_total,
                   COALESCE(SUM(pl_agg.customer_total), 0) AS customer_paid_total
            FROM transactions t
            JOIN market_days md ON t.market_day_id = md.id
            LEFT JOIN (
                SELECT transaction_id,
                       SUM(match_amount) as match_total,
                       SUM(customer_charged) as customer_total
                FROM payment_line_items
                GROUP BY transaction_id
            ) pl_agg ON pl_agg.transaction_id = t.id
            {where}
            GROUP BY md.date
            ORDER BY md.date
        """, params).fetchall()

        self._chart_trend_data = [
            {'date': r['date'], 'gross': cents_to_dollars(r['gross_total']),
             'match': cents_to_dollars(r['fam_match_total']),
             'customer': cents_to_dollars(r['customer_paid_total'])}
            for r in trend_rows
        ]

        # FMNP match time-series (from payment_line_items)
        fmnp_trend = conn.execute(f"""
            SELECT md.date,
                   COALESCE(SUM(pl.match_amount), 0) AS fmnp_match
            FROM payment_line_items pl
            JOIN transactions t ON pl.transaction_id = t.id
            JOIN market_days md ON t.market_day_id = md.id
            LEFT JOIN customer_orders co ON t.customer_order_id = co.id
            {where}
            AND pl.method_name_snapshot = 'FMNP'
            GROUP BY md.date
            ORDER BY md.date
        """, params).fetchall()

        fmnp_inapp_map = {r['date']: cents_to_dollars(r['fmnp_match']) for r in fmnp_trend}

        # External FMNP entries trend
        fmnp_entry_trend = conn.execute(f"""
            SELECT md.date,
                   COALESCE(SUM(fe.amount), 0) AS fmnp_entry_total
            FROM fmnp_entries fe
            JOIN market_days md ON fe.market_day_id = md.id
            {fmnp_where}
            GROUP BY md.date
            ORDER BY md.date
        """, fmnp_params).fetchall()

        fmnp_entry_map = {r['date']: cents_to_dollars(r['fmnp_entry_total']) for r in fmnp_entry_trend}
        all_fmnp_dates = sorted(set(fmnp_inapp_map.keys()) | set(fmnp_entry_map.keys()))
        self._chart_fmnp_data = [
            {'date': d, 'fmnp': fmnp_inapp_map.get(d, 0) + fmnp_entry_map.get(d, 0)}
            for d in all_fmnp_dates
        ]

        # Pie chart data — reuse already-fetched match_rows
        self._chart_pie_data = [
            {'method': r['method'], 'total': cents_to_dollars(r['total_allocated'])}
            for r in match_rows
        ]
        if fmnp_ext_dollars > 0:
            self._chart_pie_data.append({
                'method': 'FMNP (External)', 'total': fmnp_ext_dollars
            })

        # Customer & receipt traffic time-series
        traffic_rows = conn.execute(f"""
            SELECT md.date,
                   COUNT(DISTINCT co.customer_label) AS unique_customers,
                   COUNT(DISTINCT t.id) AS receipt_count
            FROM transactions t
            JOIN market_days md ON t.market_day_id = md.id
            LEFT JOIN customer_orders co ON t.customer_order_id = co.id
            {where}
            GROUP BY md.date
            ORDER BY md.date
        """, params).fetchall()

        self._chart_traffic_data = [
            {'date': r['date'], 'customers': r['unique_customers'],
             'receipts': r['receipt_count']}
            for r in traffic_rows
        ]

        # Vendor match distribution — query match totals per vendor for chart
        chart_match_rows = conn.execute(f"""
            SELECT v.name AS vendor,
                   COALESCE(SUM(pl.match_amount), 0) AS total_match
            FROM payment_line_items pl
            JOIN transactions t ON pl.transaction_id = t.id
            JOIN vendors v ON t.vendor_id = v.id
            JOIN market_days md ON t.market_day_id = md.id
            {where}
            GROUP BY v.id, v.name
        """, params).fetchall()
        chart_match_by_vendor = {r['vendor']: cents_to_dollars(r['total_match']) for r in chart_match_rows}
        # Add external FMNP to vendor match totals (already converted to dollars above)
        for r in fmnp_vendor_rows:
            chart_match_by_vendor[r['vendor']] = \
                chart_match_by_vendor.get(r['vendor'], 0) + cents_to_dollars(r['fmnp_entry_total'])

        self._chart_vendor_match = sorted(
            [{'vendor': name, 'match': amt}
             for name, amt in chart_match_by_vendor.items() if amt > 0],
            key=lambda x: x['match'], reverse=True
        )[:12]

        self._update_charts()

        # ── Geolocation report ─────────────────────────────────────
        self._load_geolocation_report(conn, where, params)

        # ── Activity log (full extract — no filters) ──────────────
        self._load_activity_log(conn)

        # ── Generated Rewards (full extract — no filters in v1) ─
        # Recomputed on every refresh from current state — no
        # per-filter reflow yet (deliberate v1 simplicity per the
        # 2026-04-30 spec: "supplemental info, doesn't need to be
        # complicated").
        self._load_generated_rewards(conn)

        # ── Transaction log (human-friendly audit view) ───────────
        self._load_transaction_log()

        # End the snapshot read transaction.
        if _opened_read_txn:
            try:
                conn.commit()
            except Exception:
                logger.debug("Could not commit read transaction",
                             exc_info=True)

    def _load_activity_log(self, conn):
        """Load the most recent audit log entries into the Activity
        Log tab.

        v2.0.3 fix (NEW-CRIT-2): cap the result at 1000 rows.  Pre-fix
        this read EVERY ``audit_log`` row into Python memory and built
        a QTableWidgetItem per cell.  At year 2-3 of usage with
        500K+ audit rows this froze the UI thread for 30-60 seconds
        when the operator opened Reports → Activity Log.  The peer
        ``_load_transaction_log`` already uses ``limit=500``; this was
        the lone unbounded loader.

        The Transaction Log tab and the cloud-synced Audit Log sheet
        retain the full history; the Activity Log here is a "what
        happened recently" view, not the canonical audit record.
        """
        rows = conn.execute("""
            SELECT id, table_name, record_id, action, field_name,
                   old_value, new_value, reason_code, notes,
                   changed_by, changed_at
            FROM audit_log
            ORDER BY changed_at DESC, id DESC
            LIMIT 1000
        """).fetchall()

        self._activity_data = []
        self.activity_table.setSortingEnabled(False)
        self.activity_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.activity_table.setItem(i, 0, make_item(r['changed_at'] or ''))
            self.activity_table.setItem(i, 1, make_item(r['action'] or ''))
            self.activity_table.setItem(i, 2, make_item(r['table_name'] or ''))
            self.activity_table.setItem(i, 3, make_item(
                str(r['record_id']), r['record_id']
            ))
            self.activity_table.setItem(i, 4, make_item(r['field_name'] or ''))
            self.activity_table.setItem(i, 5, make_item(r['old_value'] or ''))
            self.activity_table.setItem(i, 6, make_item(r['new_value'] or ''))
            self.activity_table.setItem(i, 7, make_item(r['reason_code'] or ''))
            self.activity_table.setItem(i, 8, make_item(r['notes'] or ''))
            self.activity_table.setItem(i, 9, make_item(r['changed_by'] or ''))

            self._activity_data.append({
                'Timestamp': r['changed_at'] or '',
                'Action': r['action'] or '',
                'Table': r['table_name'] or '',
                'Record ID': r['record_id'],
                'Field': r['field_name'] or '',
                'Old Value': r['old_value'] or '',
                'New Value': r['new_value'] or '',
                'Reason': r['reason_code'] or '',
                'Notes': r['notes'] or '',
                'Changed By': r['changed_by'] or '',
            })
        self.activity_table.resizeColumnsToContents()
        self.activity_table.setSortingEnabled(True)

    # ------------------------------------------------------------------
    # Generated Rewards (v1.9.10+)
    # ------------------------------------------------------------------
    def _load_generated_rewards(self, conn):
        """Read the persisted generated_rewards snapshot rows for
        the report.  Iterates every market_day via the same
        collector used by cloud sync so report and sheet show
        identical rows.

        Read-only: the underlying ``generated_rewards`` table is
        write-once (see ``fam/models/generated_reward.py``), so
        this report reflects the historical record of what the
        cashier handed out at the time of payment, NOT a
        recomputation against current data.  Disabling the
        rewards feature does NOT remove rows here, and rule edits
        do NOT retro-apply.
        """
        from fam.sync.data_collector import _collect_generated_rewards
        all_rows: list[dict] = []
        try:
            md_rows = conn.execute(
                "SELECT id FROM market_days ORDER BY date"
            ).fetchall()
            for md in md_rows:
                try:
                    all_rows.extend(
                        _collect_generated_rewards(conn, md['id']))
                except Exception:
                    logger.exception(
                        "Failed to derive rewards for market_day %s",
                        md['id'])
        except Exception:
            logger.exception("Failed to load generated rewards")

        self._rewards_data = all_rows
        self.rewards_table.setSortingEnabled(False)
        self.rewards_table.setRowCount(len(all_rows))
        for i, r in enumerate(all_rows):
            self.rewards_table.setItem(
                i, 0, make_item(r.get('Market Name') or ''))
            self.rewards_table.setItem(
                i, 1, make_item(r.get('Date') or ''))
            self.rewards_table.setItem(
                i, 2, make_item(r.get('Customer') or ''))
            # v2.0.6: Zip Code (column 3) — sourced from the same
            # _collect_generated_rewards collector that feeds the
            # cloud sheet, so report + sheet stay in sync.
            self.rewards_table.setItem(
                i, 3, make_item(r.get('Zip Code') or ''))
            self.rewards_table.setItem(
                i, 4, make_item(r.get('Source Method') or ''))
            self.rewards_table.setItem(i, 5, make_item(
                f"${r.get('Source Total', 0):.2f}",
                r.get('Source Total', 0)))
            self.rewards_table.setItem(i, 6, make_item(
                f"${r.get('Threshold', 0):.2f}",
                r.get('Threshold', 0)))
            self.rewards_table.setItem(
                i, 7, make_item(r.get('Reward Method') or ''))
            self.rewards_table.setItem(i, 8, make_item(
                f"${r.get('Reward Unit', 0):.2f}",
                r.get('Reward Unit', 0)))
            self.rewards_table.setItem(i, 9, make_item(
                str(r.get('Units Earned', 0)),
                r.get('Units Earned', 0)))
            self.rewards_table.setItem(i, 10, make_item(
                f"${r.get('Reward Total', 0):.2f}",
                r.get('Reward Total', 0)))
        self.rewards_table.resizeColumnsToContents()
        self.rewards_table.setSortingEnabled(True)

    # ------------------------------------------------------------------
    # Geolocation
    # ------------------------------------------------------------------
    def _load_geolocation_report(self, conn, where, params):
        """Load zip code aggregated data into the Geolocation tab."""
        geo_rows = conn.execute(f"""
            SELECT co.zip_code,
                   COUNT(DISTINCT co.customer_label) as customer_count,
                   COUNT(t.id) as receipt_count,
                   COALESCE(SUM(t.receipt_total), 0) as total_spend,
                   COALESCE(SUM(pli_agg.total_match), 0) as total_match
            FROM customer_orders co
            JOIN transactions t ON t.customer_order_id = co.id
            JOIN market_days md ON t.market_day_id = md.id
            JOIN vendors v ON t.vendor_id = v.id
            LEFT JOIN (
                SELECT transaction_id, SUM(match_amount) as total_match
                FROM payment_line_items
                GROUP BY transaction_id
            ) pli_agg ON pli_agg.transaction_id = t.id
            {where}
              AND co.zip_code IS NOT NULL AND co.zip_code != ''
            GROUP BY co.zip_code
            ORDER BY customer_count DESC
        """, params).fetchall()

        self._geo_data = []
        self.geo_table.setSortingEnabled(False)
        self.geo_table.setRowCount(len(geo_rows))
        for i, r in enumerate(geo_rows):
            spend = cents_to_dollars(r['total_spend'])
            match = cents_to_dollars(r['total_match'])
            self.geo_table.setItem(i, 0, make_item(r['zip_code']))
            self.geo_table.setItem(
                i, 1, make_item(str(r['customer_count']), r['customer_count'])
            )
            self.geo_table.setItem(
                i, 2, make_item(str(r['receipt_count']), r['receipt_count'])
            )
            self.geo_table.setItem(
                i, 3, make_item(f"${spend:.2f}", spend)
            )
            self.geo_table.setItem(
                i, 4, make_item(f"${match:.2f}", match)
            )

            self._geo_data.append({
                'Zip Code': r['zip_code'],
                '# Customers': r['customer_count'],
                '# Receipts': r['receipt_count'],
                'Total Spend': spend,
                'Total FAM Match': match,
            })
        self.geo_table.resizeColumnsToContents()
        self.geo_table.setSortingEnabled(True)

        self._draw_geo_chart()

    def _draw_geo_chart(self):
        """Draw a horizontal bar chart of customer count by zip code."""
        self._geo_figure.clear()
        ax = self._geo_figure.add_subplot(111)
        ax.set_facecolor(WHITE)

        if not self._geo_data:
            self._show_no_data(ax, "No zip code data available")
            self._geo_canvas.draw()
            return

        # Show top 15 zip codes
        display_data = self._geo_data[:15]
        zips = [d['Zip Code'] for d in display_data][::-1]
        counts = [d['# Customers'] for d in display_data][::-1]

        colors = [ACCENT_GREEN if c > 1 else PRIMARY_GREEN for c in counts]
        ax.barh(zips, counts, color=colors, edgecolor=WHITE, linewidth=0.5)

        ax.set_title('Top Zip Codes by Customer Count', fontsize=12,
                      fontweight='bold', color=TEXT_COLOR, pad=12)
        ax.set_xlabel('# Customers', fontsize=10, color=TEXT_COLOR)
        ax.tick_params(axis='y', labelsize=9, colors=TEXT_COLOR)
        ax.tick_params(axis='x', labelsize=9, colors=TEXT_COLOR)

        # Force integer ticks only — no decimals (can't have 0.5 customers)
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        max_count = max(counts) if counts else 1
        ax.set_xlim(left=0, right=max(max_count * 1.15, 1.5))

        ax.grid(True, axis='x', linestyle='--', alpha=0.3, color=MEDIUM_GRAY)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color(LIGHT_GRAY)
        ax.spines['bottom'].set_color(LIGHT_GRAY)

        self._geo_figure.tight_layout()
        self._geo_canvas.draw()

    def _open_heatmap(self):
        """Generate a folium heat map HTML file and open it in the browser."""
        if not self._geo_data:
            QMessageBox.information(
                self, "No Data", "No zip code data to map."
            )
            return

        try:
            import pgeocode
            import folium
            from folium.plugins import HeatMap
        except ImportError:
            QMessageBox.warning(
                self, "Missing Libraries",
                "The folium and pgeocode libraries are required for "
                "heat map visualization.\n\n"
                "Install them with:\n  pip install folium pgeocode"
            )
            return

        nomi = pgeocode.Nominatim('us')
        points = []
        for row in self._geo_data:
            result = nomi.query_postal_code(row['Zip Code'])
            if result is not None and not pd.isna(result.latitude):
                points.append({
                    'lat': result.latitude,
                    'lon': result.longitude,
                    'zip': row['Zip Code'],
                    'customers': row['# Customers'],
                    'spend': row['Total Spend'],
                    'match': row['Total FAM Match'],
                })

        if not points:
            QMessageBox.information(
                self, "No Geocoded Data",
                "Could not geocode any of the zip codes.\n"
                "The bar chart above shows the distribution."
            )
            return

        # Center map on the mean of all points
        avg_lat = sum(p['lat'] for p in points) / len(points)
        avg_lon = sum(p['lon'] for p in points) / len(points)

        m = folium.Map(location=[avg_lat, avg_lon], zoom_start=10)

        # Heat map layer (weighted by customer count)
        heat_data = [[p['lat'], p['lon'], p['customers']] for p in points]
        HeatMap(heat_data, radius=25, blur=15).add_to(m)

        # Markers with popup info
        for p in points:
            folium.CircleMarker(
                location=[p['lat'], p['lon']],
                radius=max(5, p['customers'] * 3),
                color=PRIMARY_GREEN,
                fill=True,
                fill_opacity=0.7,
                popup=(
                    f"Zip: {p['zip']}<br>"
                    f"Customers: {p['customers']}<br>"
                    f"Spend: ${p['spend']:.2f}<br>"
                    f"FAM Match: ${p['match']:.2f}"
                ),
            ).add_to(m)

        # Save to temp file and open in browser
        tmp = tempfile.NamedTemporaryFile(
            suffix='.html', delete=False, prefix='fam_heatmap_'
        )
        m.save(tmp.name)
        tmp.close()
        webbrowser.open(f'file://{tmp.name}')

    # ------------------------------------------------------------------
    # Charts
    # ------------------------------------------------------------------
    # Curated pie-chart palette — 10 hues with wide angular spacing,
    # tested for contrast, colorblind safety, and white/dark text legibility.
    _PIE_PALETTE = [
        '#3a7d5e',  # Forest green  (brand-aligned)
        '#e68a3e',  # Harvest gold  (brand accent)
        '#4e79a7',  # Steel blue
        '#c85c4a',  # Terra cotta
        '#6aab8d',  # Sage
        '#d4a03c',  # Amber
        '#8b6fae',  # Plum
        '#e8927c',  # Peach coral
        '#5898a0',  # Teal
        '#9c755f',  # Warm brown
    ]

    @staticmethod
    def _text_color_for_bg(hex_color):
        """Return white or dark text for best contrast on *hex_color*."""
        r = int(hex_color[1:3], 16) / 255
        g = int(hex_color[3:5], 16) / 255
        b = int(hex_color[5:7], 16) / 255
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        return '#FFFFFF' if luminance < 0.55 else '#2C2C2C'

    def _get_pie_colors(self, count):
        """Return a list of theme-appropriate colors for pie chart slices."""
        pal = self._PIE_PALETTE
        return [pal[i % len(pal)] for i in range(count)]

    def _show_no_data(self, ax, message="No data available"):
        """Display a centered 'no data' message on an empty axes."""
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, message, ha='center', va='center',
                fontsize=14, fontstyle='italic', color=SUBTITLE_GRAY,
                transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

    def _update_charts(self):
        """Redraw all charts with current data."""
        self._draw_pie_chart()
        self._draw_line_chart()
        self._draw_traffic_chart()
        self._draw_vendor_match_chart()

    def _draw_pie_chart(self):
        """Draw a pie chart of payment method distribution."""
        self._pie_figure.clear()
        ax = self._pie_figure.add_subplot(111)
        ax.set_facecolor(WHITE)

        if not self._chart_pie_data:
            self._show_no_data(ax)
            self._pie_canvas.draw()
            return

        labels = [d['method'] for d in self._chart_pie_data]
        sizes = [d['total'] for d in self._chart_pie_data]
        colors = self._get_pie_colors(len(labels))

        wedges, texts, autotexts = ax.pie(
            sizes, labels=None, autopct='%1.1f%%',
            colors=colors, startangle=90, pctdistance=0.75,
            wedgeprops={'edgecolor': WHITE, 'linewidth': 1.5}
        )

        # Style percentage labels — adapt text color to slice luminance
        for t, c in zip(autotexts, colors):
            t.set_fontsize(9)
            t.set_fontweight('bold')
            t.set_color(self._text_color_for_bg(c))

        ax.legend(
            wedges, labels, loc='center left', bbox_to_anchor=(1.0, 0.5),
            fontsize=9, frameon=False, labelcolor=TEXT_COLOR
        )
        ax.set_title('Payment Methods Breakdown', fontsize=12,
                      fontweight='bold', color=TEXT_COLOR, pad=12)

        self._pie_figure.tight_layout()
        self._pie_canvas.draw()

    def _draw_line_chart(self):
        """Draw a trending line chart for totals & FAM match over time."""
        self._line_figure.clear()
        ax = self._line_figure.add_subplot(111)
        ax.set_facecolor(WHITE)

        if not self._chart_trend_data:
            self._show_no_data(ax)
            self._line_canvas.draw()
            return

        dates = [d['date'] for d in self._chart_trend_data]
        gross = [d['gross'] for d in self._chart_trend_data]
        match = [d['match'] for d in self._chart_trend_data]

        # Build FMNP data aligned to the same date axis
        fmnp_map = {d['date']: d['fmnp'] for d in self._chart_fmnp_data}
        fmnp = [fmnp_map.get(d, 0) for d in dates]

        ax.plot(dates, gross, color=PRIMARY_GREEN, marker='o', markersize=5,
                linewidth=2, label='Gross Sales', solid_capstyle='round')
        ax.plot(dates, match, color=HARVEST_GOLD, marker='s', markersize=5,
                linewidth=2, label='FAM Match', solid_capstyle='round')
        ax.plot(dates, fmnp, color=ACCENT_GREEN, marker='^', markersize=5,
                linewidth=2, linestyle='--', label='FMNP Match', solid_capstyle='round')

        # Formatting
        ax.set_title('Totals & FAM Match Over Time', fontsize=12,
                      fontweight='bold', color=TEXT_COLOR, pad=12)
        ax.set_xlabel('Market Date', fontsize=10, color=TEXT_COLOR)
        ax.set_ylabel('Amount ($)', fontsize=10, color=TEXT_COLOR)

        # Rotate date labels for readability
        ax.tick_params(axis='x', rotation=45, labelsize=8, colors=TEXT_COLOR)
        ax.tick_params(axis='y', labelsize=9, colors=TEXT_COLOR)

        # Grid and spines
        ax.grid(True, linestyle='--', alpha=0.3, color=MEDIUM_GRAY)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color(LIGHT_GRAY)
        ax.spines['bottom'].set_color(LIGHT_GRAY)

        ax.legend(loc='upper left', fontsize=9, frameon=True,
                  facecolor=WHITE, edgecolor=LIGHT_GRAY, labelcolor=TEXT_COLOR)

        self._line_figure.tight_layout()
        self._line_canvas.draw()

    def _draw_traffic_chart(self):
        """Draw customer & receipt traffic over time."""
        self._traffic_figure.clear()
        ax = self._traffic_figure.add_subplot(111)
        ax.set_facecolor(WHITE)

        if not self._chart_traffic_data:
            self._show_no_data(ax, "No customer traffic data available")
            self._traffic_canvas.draw()
            return

        dates = [d['date'] for d in self._chart_traffic_data]
        customers = [d['customers'] for d in self._chart_traffic_data]
        receipts = [d['receipts'] for d in self._chart_traffic_data]

        ax.plot(dates, customers, color=PRIMARY_GREEN, marker='o', markersize=5,
                linewidth=2, label='Unique Customers', solid_capstyle='round')
        ax.plot(dates, receipts, color=HARVEST_GOLD, marker='s', markersize=5,
                linewidth=2, linestyle='--', label='Receipts', solid_capstyle='round')

        ax.set_title('Customer & Receipt Traffic Over Time', fontsize=12,
                      fontweight='bold', color=TEXT_COLOR, pad=12)
        ax.set_xlabel('Market Date', fontsize=10, color=TEXT_COLOR)
        ax.set_ylabel('Count', fontsize=10, color=TEXT_COLOR)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))

        ax.tick_params(axis='x', rotation=45, labelsize=8, colors=TEXT_COLOR)
        ax.tick_params(axis='y', labelsize=9, colors=TEXT_COLOR)

        ax.grid(True, linestyle='--', alpha=0.3, color=MEDIUM_GRAY)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color(LIGHT_GRAY)
        ax.spines['bottom'].set_color(LIGHT_GRAY)

        ax.legend(loc='upper left', fontsize=9, frameon=True,
                  facecolor=WHITE, edgecolor=LIGHT_GRAY, labelcolor=TEXT_COLOR)

        self._traffic_figure.tight_layout()
        self._traffic_canvas.draw()

    def _draw_vendor_match_chart(self):
        """Draw horizontal bar chart of FAM match distribution by vendor."""
        self._vendor_match_figure.clear()
        ax = self._vendor_match_figure.add_subplot(111)
        ax.set_facecolor(WHITE)

        if not self._chart_vendor_match:
            self._show_no_data(ax, "No vendor match data available")
            self._vendor_match_canvas.draw()
            return

        # Reverse so largest bar is at the top
        display = self._chart_vendor_match[::-1]
        vendors = [d['vendor'] for d in display]
        amounts = [d['match'] for d in display]
        max_amt = max(amounts) if amounts else 1

        # Green gradient: lighter for smaller amounts, deeper for larger
        light_green = (0.78, 0.93, 0.82)   # #c7edcf – soft mint
        dark_green  = (0.16, 0.40, 0.28)   # #296647 – deep forest
        colors = []
        for a in amounts:
            t = a / max_amt if max_amt else 0
            r = light_green[0] + t * (dark_green[0] - light_green[0])
            g = light_green[1] + t * (dark_green[1] - light_green[1])
            b = light_green[2] + t * (dark_green[2] - light_green[2])
            colors.append((r, g, b))

        bars = ax.barh(vendors, amounts, color=colors,
                       edgecolor=WHITE, linewidth=0.5, height=0.6)

        # Dollar labels at end of each bar
        for bar, amt in zip(bars, amounts):
            ax.text(bar.get_width() + max_amt * 0.02,
                    bar.get_y() + bar.get_height() / 2,
                    f'${amt:,.2f}', va='center', fontsize=8, color=TEXT_COLOR)

        ax.set_title('FAM Match by Vendor', fontsize=12,
                      fontweight='bold', color=TEXT_COLOR, pad=12)
        ax.set_xlabel('FAM Match ($)', fontsize=10, color=TEXT_COLOR)
        ax.set_xlim(left=0, right=max_amt * 1.18)

        ax.tick_params(axis='y', labelsize=9, colors=TEXT_COLOR)
        ax.tick_params(axis='x', labelsize=9, colors=TEXT_COLOR)

        ax.grid(True, axis='x', linestyle='--', alpha=0.3, color=MEDIUM_GRAY)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color(LIGHT_GRAY)
        ax.spines['bottom'].set_color(LIGHT_GRAY)

        self._vendor_match_figure.tight_layout()
        self._vendor_match_canvas.draw()

    def _export_charts(self):
        """Export all charts as PNG files."""
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Export Charts", "fam_charts.png",
            "PNG Files (*.png)"
        )
        if not filepath:
            return

        try:
            base, ext = os.path.splitext(filepath)
            pie_path = f"{base}_pie{ext}"
            trend_path = f"{base}_trend{ext}"
            traffic_path = f"{base}_traffic{ext}"
            vendor_match_path = f"{base}_vendor_match{ext}"

            self._pie_figure.savefig(pie_path, dpi=150, bbox_inches='tight',
                                     facecolor=self._pie_figure.get_facecolor())
            self._line_figure.savefig(trend_path, dpi=150, bbox_inches='tight',
                                      facecolor=self._line_figure.get_facecolor())
            self._traffic_figure.savefig(traffic_path, dpi=150, bbox_inches='tight',
                                         facecolor=self._traffic_figure.get_facecolor())
            self._vendor_match_figure.savefig(vendor_match_path, dpi=150, bbox_inches='tight',
                                              facecolor=self._vendor_match_figure.get_facecolor())

            QMessageBox.information(
                self, "Export Complete",
                f"Charts saved to:\n{pie_path}\n{trend_path}"
                f"\n{traffic_path}\n{vendor_match_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export charts: {str(e)}")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _export(self, report_type):
        filename = generate_export_filename(report_type)
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", filename, "CSV Files (*.csv)"
        )
        if not filepath:
            return

        try:
            if report_type == "vendor_reimbursement":
                export_vendor_reimbursement(self._vendor_data, filepath)
            elif report_type == "fam_match_report":
                export_fam_match_report(self._match_data, filepath)
            elif report_type == "detailed_ledger":
                export_detailed_ledger(self._ledger_data, filepath)
            elif report_type == "activity_log":
                export_activity_log(self._activity_data, filepath)
            elif report_type == "geolocation":
                export_geolocation_report(self._geo_data, filepath)
            elif report_type == "transaction_log":
                export_transaction_log(self._txn_log_data, filepath)
            elif report_type == "error_log":
                export_error_log(self._error_log_data, filepath)
            elif report_type == "generated_rewards":
                export_generated_rewards(
                    self._rewards_data or [], filepath)
            QMessageBox.information(self, "Export Complete", f"Report saved to:\n{filepath}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export: {str(e)}")

    # ------------------------------------------------------------------
    # Transaction Log tab
    # ------------------------------------------------------------------
    def _build_transaction_log_tab(self):
        """Build the Transaction Log tab with filters, table, and export."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Filter row ────────────────────────────────────────────
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        # "Today Only" toggle
        self._txn_today_cb = QCheckBox("Today Only")
        self._txn_today_cb.setStyleSheet("font-size: 12px; font-weight: bold;")
        self._txn_today_cb.stateChanged.connect(self._on_txn_log_filter_changed)
        filter_row.addWidget(self._txn_today_cb)

        # Action filter combo
        action_lbl = QLabel("Action:")
        action_lbl.setStyleSheet(
            f"background-color: {FIELD_LABEL_BG}; border: 1px solid #D5D2CB;"
            " border-radius: 6px; padding: 4px 8px;"
            " font-weight: bold; font-size: 12px; color: #555;"
        )
        filter_row.addWidget(action_lbl)

        self._txn_action_combo = NoScrollComboBox()
        self._txn_action_combo.addItem("All Actions", "")
        for code, label in ACTION_LABELS.items():
            self._txn_action_combo.addItem(label, code)
        self._txn_action_combo.setStyleSheet("font-size: 11px; min-height: 0px; padding: 4px 8px;")
        self._txn_action_combo.currentIndexChanged.connect(self._on_txn_log_filter_changed)
        filter_row.addWidget(self._txn_action_combo)

        filter_row.addStretch()

        # Search field
        search_lbl = QLabel("Search:")
        search_lbl.setStyleSheet(
            f"background-color: {FIELD_LABEL_BG}; border: 1px solid #D5D2CB;"
            " border-radius: 6px; padding: 4px 8px;"
            " font-weight: bold; font-size: 12px; color: #555;"
        )
        filter_row.addWidget(search_lbl)

        self._txn_search = QLineEdit()
        self._txn_search.setPlaceholderText("Filter rows...")
        self._txn_search.setStyleSheet("font-size: 12px; min-height: 0px; padding: 4px 8px;")
        self._txn_search.setMaximumWidth(200)
        self._txn_search.textChanged.connect(self._on_txn_log_search)
        filter_row.addWidget(self._txn_search)

        layout.addLayout(filter_row)

        # ── Table ─────────────────────────────────────────────────
        # v2.0.6: added Customer + Zip Code columns so audit-trail
        # entries can be correlated with customer demographics.
        self.txn_log_table = QTableWidget()
        self.txn_log_table.setColumnCount(8)
        self.txn_log_table.setHorizontalHeaderLabels(
            ["Time", "Action", "Transaction", "Customer", "Zip Code",
             "Vendor", "Details", "By"]
        )
        configure_table(self.txn_log_table, resizable=True)
        layout.addWidget(self.txn_log_table)

        # ── Export button ─────────────────────────────────────────
        export_btn = QPushButton("Export Transaction Log CSV")
        export_btn.setObjectName("secondary_btn")
        export_btn.clicked.connect(lambda: self._export("transaction_log"))
        layout.addWidget(export_btn)

        return tab

    def _load_transaction_log(self):
        """Query audit data and populate the Transaction Log table."""
        from fam.utils.timezone import eastern_today

        # Determine filters
        today_only = self._txn_today_cb.isChecked()
        action_code = self._txn_action_combo.currentData()

        date_from = None
        date_to = None
        if today_only:
            today_str = eastern_today().isoformat()
            date_from = today_str
            date_to = today_str

        action_filter = [action_code] if action_code else None

        rows = get_transaction_log(
            date_from=date_from,
            date_to=date_to,
            action_filter=action_filter,
            limit=500
        )

        self._txn_log_data = []
        self.txn_log_table.setSortingEnabled(False)
        self.txn_log_table.setRowCount(0)
        self.txn_log_table.setRowCount(len(rows))

        for i, r in enumerate(rows):
            # Time — just the timestamp
            timestamp = r.get('changed_at', '')
            self.txn_log_table.setItem(i, 0, make_item(timestamp))

            # Action — friendly label
            action_raw = r.get('action', '')
            action_label = ACTION_LABELS.get(action_raw, action_raw)
            self.txn_log_table.setItem(i, 1, make_item(action_label))

            # Transaction ID
            txn_id = r.get('fam_transaction_id', '') or ''
            self.txn_log_table.setItem(i, 2, make_item(txn_id))

            # Customer + Zip Code (v2.0.6) — joined from
            # customer_orders via the audit row's transaction.
            # Empty for non-transaction audit entries.
            customer = r.get('customer_label', '') or ''
            zip_code = r.get('zip_code', '') or ''
            self.txn_log_table.setItem(i, 3, make_item(customer))
            self.txn_log_table.setItem(i, 4, make_item(zip_code))

            # Vendor
            vendor = r.get('vendor_name', '') or ''
            self.txn_log_table.setItem(i, 5, make_item(vendor))

            # Details — built from field changes and notes
            detail = self._format_txn_detail(r)
            self.txn_log_table.setItem(i, 6, make_item(detail))

            # By
            changed_by = r.get('changed_by', '') or ''
            self.txn_log_table.setItem(i, 7, make_item(changed_by))

            self._txn_log_data.append({
                'Time': timestamp,
                'Action': action_label,
                'Transaction': txn_id,
                'Customer': customer,
                'Zip Code': zip_code,
                'Vendor': vendor,
                'Details': detail,
                'By': changed_by,
            })

        self.txn_log_table.resizeColumnsToContents()
        self.txn_log_table.setSortingEnabled(True)

        # Re-apply any active search filter
        self._on_txn_log_search(self._txn_search.text())

    @staticmethod
    def _format_txn_detail(row):
        """Build a human-readable Details string from audit row data."""
        parts = []
        field = row.get('field_name')
        old = row.get('old_value')
        new = row.get('new_value')
        reason = row.get('reason_code')
        notes = row.get('notes')

        if field:
            if old and new:
                parts.append(f"{field}: {old} → {new}")
            elif new:
                parts.append(f"{field}: {new}")
            elif old:
                parts.append(f"{field}: was {old}")
            else:
                parts.append(field)

        if reason:
            parts.append(f"Reason: {reason}")
        if notes:
            parts.append(notes)

        # Include table context if no transaction ID
        if not row.get('fam_transaction_id'):
            table = row.get('table_name', '')
            record = row.get('record_id', '')
            if table:
                parts.insert(0, f"[{table} #{record}]")

        return " | ".join(parts) if parts else ""

    def _on_txn_log_filter_changed(self):
        """Re-query transaction log when Today Only or Action filter changes."""
        self._load_transaction_log()

    def _on_txn_log_search(self, text):
        """Client-side row filtering on the transaction log table."""
        search = text.strip().lower()
        for row in range(self.txn_log_table.rowCount()):
            if not search:
                self.txn_log_table.setRowHidden(row, False)
                continue
            visible = False
            for col in range(self.txn_log_table.columnCount()):
                item = self.txn_log_table.item(row, col)
                if item and search in item.text().lower():
                    visible = True
                    break
            self.txn_log_table.setRowHidden(row, not visible)

    # ------------------------------------------------------------------
    # Error Log tab
    # ------------------------------------------------------------------
    def _build_error_log_tab(self):
        """Build the Error Log tab with filters, table, detail panel, and export."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Filter row ────────────────────────────────────────────
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        # Level filter
        level_lbl = QLabel("Level:")
        level_lbl.setStyleSheet(
            f"background-color: {FIELD_LABEL_BG}; border: 1px solid #D5D2CB;"
            " border-radius: 6px; padding: 4px 8px;"
            " font-weight: bold; font-size: 12px; color: #555;"
        )
        filter_row.addWidget(level_lbl)

        self._error_level_combo = NoScrollComboBox()
        # "Errors" here means CRITICAL + ERROR (both indicate
        # broken behavior).  Pre-v2.0.2 "Errors Only" silently
        # excluded CRITICAL — kept the label short for the dropdown
        # but the filter logic in ``_apply_error_log_filters`` now
        # correctly matches both levels.
        self._error_level_combo.addItem("Errors & Warnings", "both")
        self._error_level_combo.addItem("Errors Only", "errors")
        self._error_level_combo.addItem("Warnings Only", "warnings")
        self._error_level_combo.setStyleSheet("font-size: 11px; min-height: 0px; padding: 4px 8px;")
        self._error_level_combo.currentIndexChanged.connect(self._on_error_log_filter_changed)
        filter_row.addWidget(self._error_level_combo)

        # Area filter
        area_lbl = QLabel("Area:")
        area_lbl.setStyleSheet(
            f"background-color: {FIELD_LABEL_BG}; border: 1px solid #D5D2CB;"
            " border-radius: 6px; padding: 4px 8px;"
            " font-weight: bold; font-size: 12px; color: #555;"
        )
        filter_row.addWidget(area_lbl)

        self._error_area_combo = NoScrollComboBox()
        self._error_area_combo.addItem("All Areas", "")
        self._error_area_combo.setStyleSheet("font-size: 11px; min-height: 0px; padding: 4px 8px;")
        self._error_area_combo.currentIndexChanged.connect(self._on_error_log_filter_changed)
        filter_row.addWidget(self._error_area_combo)

        filter_row.addStretch()

        # Search
        err_search_lbl = QLabel("Search:")
        err_search_lbl.setStyleSheet(
            f"background-color: {FIELD_LABEL_BG}; border: 1px solid #D5D2CB;"
            " border-radius: 6px; padding: 4px 8px;"
            " font-weight: bold; font-size: 12px; color: #555;"
        )
        filter_row.addWidget(err_search_lbl)

        self._error_search = QLineEdit()
        self._error_search.setPlaceholderText("Filter rows...")
        self._error_search.setStyleSheet("font-size: 12px; min-height: 0px; padding: 4px 8px;")
        self._error_search.setMaximumWidth(200)
        self._error_search.textChanged.connect(self._on_error_log_filter_changed)
        filter_row.addWidget(self._error_search)

        layout.addLayout(filter_row)

        # ── Splitter: table on top, detail panel on bottom ────────
        splitter = QSplitter(Qt.Vertical)

        # Table
        self.error_log_table = QTableWidget()
        self.error_log_table.setColumnCount(4)
        self.error_log_table.setHorizontalHeaderLabels(
            ["Time", "Level", "Area", "What Happened"]
        )
        configure_table(self.error_log_table, resizable=True)
        self.error_log_table.currentCellChanged.connect(self._on_error_row_selected)
        splitter.addWidget(self.error_log_table)

        # Detail panel — read-only monospace text showing traceback
        self._error_detail = QTextEdit()
        self._error_detail.setReadOnly(True)
        self._error_detail.setFont(QFont("Consolas", 10))
        self._error_detail.setPlaceholderText("Click an error row to see full details here...")
        self._error_detail.setMaximumHeight(180)
        self._error_detail.setStyleSheet(f"""
            QTextEdit {{
                background-color: #FAFAFA;
                border: 1px solid {LIGHT_GRAY};
                border-radius: 6px;
                padding: 8px;
                font-size: 11px;
            }}
        """)
        splitter.addWidget(self._error_detail)

        # Default split: table gets 70%, detail gets 30%
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)

        # ── Button row ────────────────────────────────────────────
        btn_row = QHBoxLayout()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("secondary_btn")
        refresh_btn.clicked.connect(self._reload_error_log)
        btn_row.addWidget(refresh_btn)

        export_btn = QPushButton("Export Error Log CSV")
        export_btn.setObjectName("secondary_btn")
        export_btn.clicked.connect(lambda: self._export("error_log"))
        btn_row.addWidget(export_btn)

        btn_row.addStretch()

        # Clear Errors — destructive, lives on the right side of the
        # row (visually separated from refresh/export) and styled red
        # to match other danger actions.  Truncates fam_manager.log
        # locally + rotated backups, and (when sync is configured)
        # clears the "Error Log" Google Sheets tab so coordinators
        # don't have to scroll past stale entries when triaging.
        clear_btn = QPushButton("Clear Errors")
        clear_btn.setObjectName("danger_btn")
        clear_btn.setStyleSheet(f"""
            #danger_btn {{
                background-color: {WHITE};
                color: {ERROR_COLOR};
                border: 1.5px solid {ERROR_COLOR};
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: bold;
            }}
            #danger_btn:hover {{
                background-color: {ERROR_COLOR};
                color: {WHITE};
            }}
        """)
        clear_btn.setToolTip(
            "Erase all entries from fam_manager.log (local) AND from "
            "the Error Log tab on Google Sheets.  This action cannot "
            "be undone.")
        clear_btn.clicked.connect(self._clear_error_log)
        btn_row.addWidget(clear_btn)

        layout.addLayout(btn_row)

        return tab

    def _load_error_log(self):
        """Parse the log file and populate the Error Log table."""
        # parse_log_file (line 33) and get_log_path (line 34) are at
        # module level — only ``get_friendly_module`` is local.
        from fam.utils.log_reader import get_friendly_module

        log_path = get_log_path()
        entries = parse_log_file(log_path, limit=500)

        # Store full entries for detail view and filtering
        self._error_log_entries = entries

        # Populate the Area filter with unique module labels
        current_area = self._error_area_combo.currentData()
        self._error_area_combo.blockSignals(True)
        self._error_area_combo.clear()
        self._error_area_combo.addItem("All Areas", "")
        area_labels = sorted(set(e['module_label'] for e in entries))
        for label in area_labels:
            self._error_area_combo.addItem(label, label)
        # Restore previous selection if still valid
        idx = self._error_area_combo.findData(current_area)
        if idx >= 0:
            self._error_area_combo.setCurrentIndex(idx)
        self._error_area_combo.blockSignals(False)

        self._apply_error_log_filters()

    def _apply_error_log_filters(self):
        """Filter and display error log entries based on current filter state."""
        entries = getattr(self, '_error_log_entries', [])

        # Level filter.
        #
        # v2.0.2 fix: "Errors Only" must include CRITICAL.  The
        # ``_global_exception_handler`` writes unhandled crashes at
        # CRITICAL (``fam/app.py:36``) and the v2.0.1 log_reader
        # default level set explicitly added CRITICAL.  Pre-fix,
        # this comparator silently dropped every CRITICAL entry —
        # so "Errors Only" hid exactly the failures users care
        # most about.  The cloud-side Error Log sync at
        # ``data_collector.py:836`` correctly includes CRITICAL,
        # so before this fix local UI and cloud diverged on the
        # most important class of entry.
        level_filter = self._error_level_combo.currentData()
        if level_filter == "errors":
            entries = [e for e in entries if e['level'] in ('ERROR', 'CRITICAL')]
        elif level_filter == "warnings":
            entries = [e for e in entries if e['level'] == 'WARNING']

        # Area filter
        area_filter = self._error_area_combo.currentData()
        if area_filter:
            entries = [e for e in entries if e['module_label'] == area_filter]

        # Search filter
        search = self._error_search.text().strip().lower()
        if search:
            entries = [
                e for e in entries
                if search in e['timestamp'].lower()
                or search in e['level'].lower()
                or search in e['module_label'].lower()
                or search in e['friendly_message'].lower()
                or search in e['message'].lower()
            ]

        # Populate table
        self._error_log_data = []
        self.error_log_table.setSortingEnabled(False)
        self.error_log_table.setRowCount(0)
        self.error_log_table.setRowCount(len(entries))

        for i, e in enumerate(entries):
            # Time
            self.error_log_table.setItem(i, 0, make_item(e['timestamp']))

            # Level — color-coded
            level_item = make_item(e['level'].capitalize())
            if e['level'] == 'ERROR':
                level_item.setForeground(QBrush(QColor(ERROR_COLOR)))
            elif e['level'] == 'WARNING':
                level_item.setForeground(QBrush(QColor(WARNING_COLOR)))
            self.error_log_table.setItem(i, 1, level_item)

            # Area
            self.error_log_table.setItem(i, 2, make_item(e['module_label']))

            # What Happened
            self.error_log_table.setItem(i, 3, make_item(e['friendly_message']))

            # Store index into _error_log_entries for detail lookup
            # (We use the traceback from the original entry)
            detail_text = e.get('traceback', '').strip()
            raw_msg = e.get('message', '')

            self._error_log_data.append({
                'Timestamp': e['timestamp'],
                'Level': e['level'],
                'Area': e['module_label'],
                'Module': e.get('module', ''),
                'What Happened': e['friendly_message'],
                'Message': raw_msg,
                'Traceback': detail_text,
            })

        self.error_log_table.resizeColumnsToContents()
        self.error_log_table.setSortingEnabled(True)
        self._error_detail.clear()

    def _on_error_log_filter_changed(self):
        """Re-filter error log display when any filter changes."""
        if self._error_log_loaded:
            self._apply_error_log_filters()

    def _on_error_row_selected(self, row, _col, _prev_row, _prev_col):
        """Show full details for the selected error log entry."""
        if row < 0 or row >= len(self._error_log_data):
            self._error_detail.clear()
            return

        entry = self._error_log_data[row]
        parts = []
        parts.append(f"Time: {entry['Timestamp']}")
        parts.append(f"Level: {entry['Level']}")
        parts.append(f"Area: {entry['Area']}")
        parts.append(f"Module: {entry['Module']}")
        parts.append(f"Message: {entry['Message']}")
        if entry.get('Traceback'):
            parts.append("")
            parts.append("Traceback:")
            parts.append(entry['Traceback'])

        self._error_detail.setPlainText("\n".join(parts))

    def _reload_error_log(self):
        """Force reload the error log from disk."""
        self._error_log_loaded = False
        self._load_error_log()
        self._error_log_loaded = True

    def _clear_error_log(self):
        """Erase the error log everywhere a coordinator might see it.

        Targets (v1.9.9+):
        * **Local file** — ``fam_manager.log`` and any rotated
          backups (``.1``, ``.2``, ``.3``).  Reuses
          :func:`fam.utils.logging_config.clear_log_files`, which
          also handles the open-handler-on-Windows quirk by
          releasing the active stream before truncation.
        * **Google Sheets** — clears the "Error Log" tab via the
          existing sync infrastructure.  Best-effort: a network
          failure or missing credentials never prevents the local
          truncation from completing.

        Confirmation dialog uses two-stage destruction so the
        action can't be triggered by an accidental double-click.

        The audit log (Reports → Activity Log) is intentionally
        NOT touched here — that's regulatory/business audit
        history, not error noise.
        """
        # First confirmation — explain what's about to happen.
        first = QMessageBox.warning(
            self, "Clear Error Log?",
            "This will erase ALL entries from:\n\n"
            "  •  fam_manager.log on this laptop (and rotated backups)\n"
            "  •  the 'Error Log' tab on your Google Sheet, if sync is "
            "configured\n\n"
            "It will NOT touch the Activity Log (audit history of "
            "transactions, voids, adjustments) — that lives in a "
            "different place and is preserved.\n\n"
            "This action CANNOT be undone.  Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if first != QMessageBox.Yes:
            return

        # Second confirmation — last chance.
        second = QMessageBox.critical(
            self, "Confirm Clear",
            "Last chance — proceed with clearing the error log?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if second != QMessageBox.Yes:
            return

        # ── Local file truncate ──────────────────────────────
        local_ok = False
        local_msg = ''
        try:
            from fam.utils.logging_config import clear_log_files
            local_ok, local_msg = clear_log_files()
        except Exception as e:
            local_ok = False
            local_msg = f"Could not clear local log: {e}"

        # ── Google Sheets clear ──────────────────────────────
        sheets_status = self._clear_sheets_error_log_tab()

        # ── Refresh the in-app view ──────────────────────────
        try:
            self._reload_error_log()
        except Exception:
            pass

        # ── Report back to the user ──────────────────────────
        lines = []
        if local_ok:
            lines.append("✅  Local fam_manager.log cleared.")
        else:
            lines.append(f"⚠  Local clear had issues: {local_msg}")
        lines.append(sheets_status)
        QMessageBox.information(
            self, "Error Log Cleared",
            "\n".join(lines),
        )

    def _clear_sheets_error_log_tab(self) -> str:
        """Clear THIS DEVICE's rows from the 'Error Log' tab on the
        configured Google Sheet.

        IMPORTANT: this is intentionally **device-scoped** via
        ``delete_rows(sheet, market_code, device_id)``.  In a
        multi-device deployment, every coordinator's laptop syncs to
        the same Google Sheet — using gspread's ``ws.clear()`` would
        wipe other devices' error rows along with our own.  Coordinators
        wanted "clear noise from MY laptop", not "delete every
        device's history from the shared sheet".

        Returns a one-line status string for the post-action dialog
        — never raises, so a sync failure can't block the local
        truncation that already succeeded.
        """
        # Lazy-import the backend so installs that never sync don't
        # pay the cost on the Reports screen render path.
        try:
            from fam.sync.gsheets import GoogleSheetsBackend
            from fam.utils.app_settings import (
                get_market_code, get_device_id)
        except Exception as e:
            return f"ℹ  Google Sheets backend not available: {e}"

        backend = GoogleSheetsBackend()
        if not backend.is_configured():
            return "ℹ  Google Sheets not configured for sync — skipped."

        market_code = str(get_market_code() or '')
        device_id = str(get_device_id() or '')
        if not market_code or not device_id:
            # No identity → no rows on the sheet are attributed to
            # us.  Skip rather than risk an unscoped wipe.
            return ("ℹ  Device identity not configured yet — no "
                    "rows on the sheet are attributed to this device, "
                    "so nothing to clear there.")

        # delete_rows() handles the lazy-import + authorize internally
        # and returns SyncResult instead of raising.  It only deletes
        # rows where market_code + device_id match the current device,
        # so other devices' rows are preserved.
        try:
            result = backend.delete_rows(
                'Error Log', market_code, device_id)
        except Exception as e:
            logger.warning(
                "Failed to clear Error Log worksheet contents",
                exc_info=True)
            return (f"⚠  Could not clear Google Sheets tab: {e}. "
                    f"Local file was still cleared.")

        if not result.success:
            return (f"⚠  Could not clear Google Sheets tab: "
                    f"{result.error}. Local file was still cleared.")

        return (f"✅  Google Sheets 'Error Log' tab: removed "
                f"{result.rows_synced} row(s) for this device "
                f"(other devices' rows preserved).")

    # ------------------------------------------------------------------
    # Tab change handler (lazy + auto-refresh Error Log)
    # ------------------------------------------------------------------
    def _on_tab_changed(self, index):
        """Lazy-load the Error Log on first selection AND re-load it
        on every subsequent selection so new log entries appended
        since the last view become visible without an explicit
        refresh click.

        v2.0.6 fix (2026-05-06): pre-fix the Error Log was loaded
        ONCE on first click and only refreshed via the manual
        Refresh button.  The cloud-side Error Log sync re-parses
        ``fam_manager.log`` fresh on every sync cycle, so the cloud
        sheet showed entries that the local UI silently dropped —
        coordinator-reported divergence (DB-fragmentation WARNING
        present in Sheets, missing from local Error Log tab).
        Re-loading on tab switch keeps the UI and cloud aligned
        without forcing the operator to click refresh.

        Performance note: ``parse_log_file`` is capped at 500
        entries and reads the rotating log file (typically <1MB
        even at year-3 scale), so the per-tab-switch cost is in the
        single-digit-ms range — far below interaction noise.
        """
        tab_text = self.tabs.tabText(index)
        if tab_text == "Error Log":
            self._load_error_log()
            self._error_log_loaded = True
