"""Screen F: Reports and Exports."""

import os

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QFileDialog, QMessageBox, QScrollArea
)
from PySide6.QtCore import Qt

from fam.database.connection import get_connection
from fam.models.market_day import get_all_market_days, get_all_markets
from fam.models.vendor import get_all_vendors
from fam.models.payment_method import get_all_payment_methods
from fam.utils.export import (
    export_vendor_reimbursement, export_fam_match_report, export_detailed_ledger,
    export_activity_log, generate_export_filename
)
from fam.ui.styles import (
    WHITE, LIGHT_GRAY, PRIMARY_GREEN, HARVEST_GOLD, SUBTITLE_GRAY,
    ACCENT_GREEN, BACKGROUND, TEXT_COLOR, MEDIUM_GRAY, WARNING_COLOR, ERROR_COLOR
)
from fam.ui.helpers import make_field_label, make_item, configure_table, CheckableComboBox, DateRangeWidget


class ReportsScreen(QWidget):
    """Reports and Exports screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._vendor_data = []
        self._match_data = []
        self._ledger_data = []
        self._activity_data = []
        self._chart_pie_data = []
        self._chart_trend_data = []
        self._chart_fmnp_data = []
        self._populating = False
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Reports & Exports")
        title.setObjectName("screen_title")
        layout.addWidget(title)

        # ── Filter bar — 4 checkable dropdowns ───────────────────
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
        filter_layout.setSpacing(6)

        # Compact label style for the filter row
        _filter_label_ss = (
            f"background-color: {LIGHT_GRAY}; border: 1px solid #C8C8C8;"
            " border-radius: 4px; padding: 3px 6px;"
            " font-weight: bold; font-size: 11px; color: #555;"
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
        self.summary_frame = QFrame()
        self.summary_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
                padding: 16px;
            }}
        """)
        summary_layout = QHBoxLayout(self.summary_frame)

        self.total_receipts_label = self._make_summary("Total Receipts", "$0.00")
        summary_layout.addWidget(self.total_receipts_label)

        self.total_customer_label = self._make_summary("Customer Paid", "$0.00")
        summary_layout.addWidget(self.total_customer_label)

        self.total_match_label = self._make_summary("FAM Match", "$0.00")
        summary_layout.addWidget(self.total_match_label)

        self.total_fmnp_label = self._make_summary("FMNP Total", "$0.00")
        summary_layout.addWidget(self.total_fmnp_label)

        layout.addWidget(self.summary_frame)

        # ── Tabs for reports ─────────────────────────────────────
        self.tabs = QTabWidget()

        # Vendor Reimbursement tab
        self.vendor_table = QTableWidget()
        self.vendor_table.setColumnCount(6)
        self.vendor_table.setHorizontalHeaderLabels(
            ["Vendor", "Customer(s)", "Date(s)", "Gross Sales", "FMNP", "Total Reimbursement"]
        )
        configure_table(self.vendor_table)

        vendor_tab = QWidget()
        vl = QVBoxLayout(vendor_tab)
        vl.addWidget(self.vendor_table)
        export_btn1 = QPushButton("Export Vendor Reimbursement CSV")
        export_btn1.setObjectName("secondary_btn")
        export_btn1.clicked.connect(lambda: self._export("vendor_reimbursement"))
        vl.addWidget(export_btn1)
        self.tabs.addTab(vendor_tab, "Vendor Reimbursement")

        # FAM Match Report tab
        self.match_table = QTableWidget()
        self.match_table.setColumnCount(3)
        self.match_table.setHorizontalHeaderLabels(
            ["Payment Method", "Total Allocated", "Total Discount"]
        )
        configure_table(self.match_table)

        match_tab = QWidget()
        sl = QVBoxLayout(match_tab)
        sl.addWidget(self.match_table)
        export_btn2 = QPushButton("Export FAM Match Report CSV")
        export_btn2.setObjectName("secondary_btn")
        export_btn2.clicked.connect(lambda: self._export("fam_match_report"))
        sl.addWidget(export_btn2)
        self.tabs.addTab(match_tab, "FAM Match Report")

        # Detailed Ledger tab
        self.ledger_table = QTableWidget()
        self.ledger_table.setColumnCount(8)
        self.ledger_table.setHorizontalHeaderLabels(
            ["Transaction ID", "Customer", "Vendor", "Receipt Total", "Customer Paid",
             "FAM Match", "Status", "Payment Methods"]
        )
        configure_table(self.ledger_table)

        ledger_tab = QWidget()
        ll = QVBoxLayout(ledger_tab)
        ll.addWidget(self.ledger_table)
        export_btn3 = QPushButton("Export Detailed Ledger CSV")
        export_btn3.setObjectName("secondary_btn")
        export_btn3.clicked.connect(lambda: self._export("detailed_ledger"))
        ll.addWidget(export_btn3)
        self.tabs.addTab(ledger_tab, "Detailed Ledger")

        # Activity Log tab
        self.activity_table = QTableWidget()
        self.activity_table.setColumnCount(10)
        self.activity_table.setHorizontalHeaderLabels(
            ["Timestamp", "Action", "Table", "Record ID", "Field",
             "Old Value", "New Value", "Reason", "Notes", "Changed By"]
        )
        configure_table(self.activity_table)

        activity_tab = QWidget()
        al = QVBoxLayout(activity_tab)
        al.addWidget(self.activity_table)
        export_btn4 = QPushButton("Export Activity Log CSV")
        export_btn4.setObjectName("secondary_btn")
        export_btn4.clicked.connect(lambda: self._export("activity_log"))
        al.addWidget(export_btn4)
        self.tabs.addTab(activity_tab, "Activity Log")

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

        self._pie_figure = Figure(figsize=(9, 4), dpi=100, facecolor=BACKGROUND)
        self._pie_canvas = FigureCanvasQTAgg(self._pie_figure)
        self._pie_canvas.setMinimumHeight(350)
        chart_layout.addWidget(self._pie_canvas)

        # Line chart
        line_header = QLabel("Totals & FAM Match Over Time")
        line_header.setObjectName("section_header")
        chart_layout.addWidget(line_header)

        self._line_figure = Figure(figsize=(9, 4), dpi=100, facecolor=BACKGROUND)
        self._line_canvas = FigureCanvasQTAgg(self._line_figure)
        self._line_canvas.setMinimumHeight(350)
        chart_layout.addWidget(self._line_canvas)

        chart_layout.addStretch()
        chart_scroll.setWidget(chart_content)
        charts_outer.addWidget(chart_scroll)

        export_charts_btn = QPushButton("Export Charts as PNG")
        export_charts_btn.setObjectName("secondary_btn")
        export_charts_btn.clicked.connect(self._export_charts)
        charts_outer.addWidget(export_charts_btn)

        self.tabs.addTab(charts_tab, "Charts")

        layout.addWidget(self.tabs)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _make_summary(self, label_text, value_text):
        frame = QFrame()
        frame.setStyleSheet("background-color: transparent;")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(12, 8, 12, 8)
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"font-size: 12px; color: {SUBTITLE_GRAY}; font-weight: bold; text-transform: uppercase;")
        fl.addWidget(lbl)
        val = QLabel(value_text)
        val.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {PRIMARY_GREEN};")
        fl.addWidget(val)
        frame._value_label = val
        return frame

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
        """Return (where_sql, params) for the FMNP sub-query.

        FMNP entries link to market_days (not transactions), so
        Date, Market, and Vendor filters apply here.
        """
        clauses = []
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
            sql, p = self._in_clause("f.vendor_id", vendor_ids)
            clauses.append(sql)
            params.extend(p)

        if clauses:
            where = "WHERE " + " AND ".join(clauses)
        else:
            where = ""
        return where, params

    # ------------------------------------------------------------------
    # Generate all three reports
    # ------------------------------------------------------------------
    def _generate_reports(self):
        conn = get_connection()
        where, params = self._build_where()

        # ── Vendor reimbursement ─────────────────────────────────
        vendor_rows = conn.execute(f"""
            SELECT v.name as vendor,
                   COALESCE(SUM(t.receipt_total), 0) as gross_sales,
                   GROUP_CONCAT(DISTINCT md.date) as transaction_dates,
                   GROUP_CONCAT(DISTINCT co.customer_label) as customer_ids
            FROM transactions t
            JOIN vendors v ON t.vendor_id = v.id
            JOIN market_days md ON t.market_day_id = md.id
            LEFT JOIN customer_orders co ON t.customer_order_id = co.id
            {where}
            GROUP BY v.id, v.name
            ORDER BY v.name
        """, params).fetchall()

        # FMNP sub-query (separate table — Date/Market/Vendor apply)
        fmnp_where, fmnp_params = self._build_fmnp_where()
        dr_from, _ = self.date_range.get_date_range()
        needs_md_join = bool(dr_from or self.market_combo.checked_data())
        fmnp_join = "JOIN market_days md ON f.market_day_id = md.id" if needs_md_join else ""

        fmnp_rows = conn.execute(f"""
            SELECT v.name as vendor,
                   COALESCE(SUM(f.amount), 0) as fmnp_total
            FROM fmnp_entries f
            JOIN vendors v ON f.vendor_id = v.id
            {fmnp_join}
            {fmnp_where}
            GROUP BY v.id, v.name
        """, fmnp_params).fetchall()

        fmnp_by_vendor = {r['vendor']: r['fmnp_total'] for r in fmnp_rows}

        self._vendor_data = []
        self.vendor_table.setSortingEnabled(False)
        self.vendor_table.setRowCount(len(vendor_rows))
        total_gross = 0
        total_fmnp = 0
        for i, r in enumerate(vendor_rows):
            gross = r['gross_sales']
            fmnp_amt = fmnp_by_vendor.get(r['vendor'], 0)
            total_reimburse = gross + fmnp_amt
            total_gross += gross
            total_fmnp += fmnp_amt
            dates_str = r['transaction_dates'] or ''
            customers_str = r['customer_ids'] or ''

            self.vendor_table.setItem(i, 0, make_item(r['vendor']))
            self.vendor_table.setItem(i, 1, make_item(customers_str))
            self.vendor_table.setItem(i, 2, make_item(dates_str))
            self.vendor_table.setItem(i, 3, make_item(f"${gross:.2f}", gross))
            self.vendor_table.setItem(i, 4, make_item(f"${fmnp_amt:.2f}", fmnp_amt))
            self.vendor_table.setItem(i, 5, make_item(f"${total_reimburse:.2f}", total_reimburse))

            self._vendor_data.append({
                'Vendor': r['vendor'], 'Customer(s)': customers_str,
                'Date(s)': dates_str,
                'Gross Sales': gross, 'FMNP': fmnp_amt,
                'Total Reimbursement': total_reimburse
            })
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
                   SUM(pl.discount_amount) as total_discount
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
        total_discount = 0
        for i, r in enumerate(match_rows):
            allocated = r['total_allocated']
            discount = r['total_discount']
            total_customer += (allocated - discount)
            total_discount += discount

            self.match_table.setItem(i, 0, make_item(r['method']))
            self.match_table.setItem(i, 1, make_item(f"${allocated:.2f}", allocated))
            self.match_table.setItem(i, 2, make_item(f"${discount:.2f}", discount))

            self._match_data.append({
                'Payment Method': r['method'],
                'Total Allocated': allocated,
                'Total Discount': discount
            })
        self.match_table.setSortingEnabled(True)

        # Update summary cards
        self.total_receipts_label._value_label.setText(f"${total_gross:.2f}")
        self.total_customer_label._value_label.setText(f"${total_customer:.2f}")
        self.total_match_label._value_label.setText(f"${total_discount:.2f}")
        self.total_fmnp_label._value_label.setText(f"${total_fmnp:.2f}")

        # ── Detailed ledger ──────────────────────────────────────
        ledger_rows = conn.execute(f"""
            SELECT t.fam_transaction_id, v.name as vendor,
                   t.receipt_total, t.status,
                   COALESCE(co.customer_label, '') as customer_id,
                   COALESCE(SUM(pl.customer_charged), 0) as customer_paid,
                   COALESCE(SUM(pl.discount_amount), 0) as fam_match,
                   GROUP_CONCAT(pl.method_name_snapshot || ': $' ||
                       PRINTF('%.2f', pl.method_amount), ', ') as methods
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
            self.ledger_table.setItem(i, 0, make_item(r['fam_transaction_id']))
            self.ledger_table.setItem(i, 1, make_item(r['customer_id']))
            self.ledger_table.setItem(i, 2, make_item(r['vendor']))
            self.ledger_table.setItem(i, 3, make_item(f"${r['receipt_total']:.2f}", r['receipt_total']))
            self.ledger_table.setItem(i, 4, make_item(f"${r['customer_paid']:.2f}", r['customer_paid']))
            self.ledger_table.setItem(i, 5, make_item(f"${r['fam_match']:.2f}", r['fam_match']))
            self.ledger_table.setItem(i, 6, make_item(r['status']))
            self.ledger_table.setItem(i, 7, make_item(r['methods'] or ''))

            self._ledger_data.append({
                'Transaction ID': r['fam_transaction_id'],
                'Customer': r['customer_id'],
                'Vendor': r['vendor'],
                'Receipt Total': r['receipt_total'],
                'Customer Paid': r['customer_paid'],
                'FAM Match': r['fam_match'],
                'Status': r['status'],
                'Payment Methods': r['methods'] or ''
            })
        self.ledger_table.setSortingEnabled(True)

        # ── Chart data: time-series for trending ──────────────────
        trend_rows = conn.execute(f"""
            SELECT md.date,
                   COALESCE(SUM(t.receipt_total), 0)   AS gross_total,
                   COALESCE(SUM(pl.discount_amount), 0) AS fam_match_total,
                   COALESCE(SUM(pl.customer_charged), 0) AS customer_paid_total
            FROM transactions t
            JOIN market_days md ON t.market_day_id = md.id
            LEFT JOIN payment_line_items pl ON pl.transaction_id = t.id
            {where}
            GROUP BY md.date
            ORDER BY md.date
        """, params).fetchall()

        self._chart_trend_data = [
            {'date': r['date'], 'gross': r['gross_total'],
             'match': r['fam_match_total'], 'customer': r['customer_paid_total']}
            for r in trend_rows
        ]

        # FMNP time-series
        fmnp_trend = conn.execute(f"""
            SELECT md.date,
                   COALESCE(SUM(f.amount), 0) AS fmnp_total
            FROM fmnp_entries f
            JOIN market_days md ON f.market_day_id = md.id
            JOIN vendors v ON f.vendor_id = v.id
            {fmnp_where}
            GROUP BY md.date
            ORDER BY md.date
        """, fmnp_params).fetchall()

        self._chart_fmnp_data = [
            {'date': r['date'], 'fmnp': r['fmnp_total']}
            for r in fmnp_trend
        ]

        # Pie chart data — reuse already-fetched match_rows
        self._chart_pie_data = [
            {'method': r['method'], 'total': r['total_allocated']}
            for r in match_rows
        ]

        self._update_charts()

        # ── Activity log (full extract — no filters) ──────────────
        self._load_activity_log(conn)

    def _load_activity_log(self, conn):
        """Load all audit log entries into the Activity Log tab."""
        rows = conn.execute("""
            SELECT id, table_name, record_id, action, field_name,
                   old_value, new_value, reason_code, notes,
                   changed_by, changed_at
            FROM audit_log
            ORDER BY changed_at DESC
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
        self.activity_table.setSortingEnabled(True)

    # ------------------------------------------------------------------
    # Charts
    # ------------------------------------------------------------------
    def _get_pie_colors(self, count):
        """Return a list of theme-appropriate colors for pie chart slices."""
        palette = [
            PRIMARY_GREEN, HARVEST_GOLD, ACCENT_GREEN,
            WARNING_COLOR, ERROR_COLOR, SUBTITLE_GRAY,
            '#5B9BD5', '#8E6FBF',  # blue, purple
        ]
        # Cycle through palette if more slices than colors
        return [palette[i % len(palette)] for i in range(count)]

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
        """Redraw both charts with current data."""
        self._draw_pie_chart()
        self._draw_line_chart()

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

        # Style percentage labels
        for t in autotexts:
            t.set_fontsize(9)
            t.set_fontweight('bold')
            t.set_color(WHITE)

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
                linewidth=2, linestyle='--', label='FMNP', solid_capstyle='round')

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

    def _export_charts(self):
        """Export both charts as PNG files."""
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

            self._pie_figure.savefig(pie_path, dpi=150, bbox_inches='tight',
                                     facecolor=self._pie_figure.get_facecolor())
            self._line_figure.savefig(trend_path, dpi=150, bbox_inches='tight',
                                      facecolor=self._line_figure.get_facecolor())

            QMessageBox.information(
                self, "Export Complete",
                f"Charts saved to:\n{pie_path}\n{trend_path}"
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
            QMessageBox.information(self, "Export Complete", f"Report saved to:\n{filepath}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export: {str(e)}")
