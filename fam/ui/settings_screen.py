"""Settings screen for managing markets, vendors, and payment methods."""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QDoubleSpinBox, QSpinBox, QCheckBox, QMessageBox, QDialog,
    QFormLayout, QDialogButtonBox
)
from PySide6.QtCore import Qt

from fam.database.connection import get_connection

logger = logging.getLogger('fam.ui.settings_screen')
from fam.models.vendor import (
    get_all_vendors, create_vendor, update_vendor,
    get_market_vendor_ids, assign_vendor_to_market, unassign_vendor_from_market
)
from fam.models.payment_method import (
    get_all_payment_methods, create_payment_method, update_payment_method
)
from fam.ui.styles import (
    WHITE, LIGHT_GRAY, ERROR_COLOR, PRIMARY_GREEN, BACKGROUND, TEXT_COLOR,
    CARD_FRAME_STYLE
)
from fam.ui.helpers import make_field_label, make_item, make_action_btn, configure_table


# ── Edit Dialogs ─────────────────────────────────────────────

class EditMarketDialog(QDialog):
    """Dialog for editing a market's name and address."""

    def __init__(self, market, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit Market: {market['name']}")
        self.setMinimumWidth(400)
        self.market = market
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BACKGROUND}; }}
            QLabel {{ background-color: transparent; color: {TEXT_COLOR}; }}
        """)

        layout = QFormLayout(self)
        self.name_input = QLineEdit()
        self.name_input.setText(market['name'])
        layout.addRow("Market Name:", self.name_input)

        self.address_input = QLineEdit()
        self.address_input.setText(market.get('address') or '')
        layout.addRow("Address:", self.address_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class EditVendorDialog(QDialog):
    """Dialog for editing a vendor's name and contact info."""

    def __init__(self, vendor, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit Vendor: {vendor['name']}")
        self.setMinimumWidth(400)
        self.vendor = vendor
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BACKGROUND}; }}
            QLabel {{ background-color: transparent; color: {TEXT_COLOR}; }}
        """)

        layout = QFormLayout(self)
        self.name_input = QLineEdit()
        self.name_input.setText(vendor['name'])
        layout.addRow("Vendor Name:", self.name_input)

        self.contact_input = QLineEdit()
        self.contact_input.setText(vendor.get('contact_info') or '')
        layout.addRow("Contact Info:", self.contact_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class EditPaymentMethodDialog(QDialog):
    """Dialog for editing a payment method's name and match %."""

    def __init__(self, method, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit Payment Method: {method['name']}")
        self.setMinimumWidth(400)
        self.method = method
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BACKGROUND}; }}
            QLabel {{ background-color: transparent; color: {TEXT_COLOR}; }}
        """)

        layout = QFormLayout(self)
        self.name_input = QLineEdit()
        self.name_input.setText(method['name'])
        layout.addRow("Method Name:", self.name_input)

        self.match_spin = QDoubleSpinBox()
        self.match_spin.setRange(0, 999)
        self.match_spin.setDecimals(1)
        self.match_spin.setSuffix("%")
        self.match_spin.setValue(method['match_percent'])
        layout.addRow("Match %:", self.match_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MatchLimitDialog(QDialog):
    """Dialog for setting a market's daily match limit."""

    def __init__(self, market, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Match Limit: {market['name']}")
        self.setMinimumWidth(350)
        self.market = market
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BACKGROUND}; }}
            QLabel {{ background-color: transparent; color: {TEXT_COLOR}; }}
        """)

        layout = QFormLayout(self)

        info = QLabel("Set the maximum FAM match per customer per market day.")
        info.setWordWrap(True)
        info.setStyleSheet("font-size: 12px; padding-bottom: 8px;")
        layout.addRow(info)

        self.limit_spin = QDoubleSpinBox()
        self.limit_spin.setRange(0.01, 99999.99)
        self.limit_spin.setDecimals(2)
        self.limit_spin.setPrefix("$")
        self.limit_spin.setValue(market.get('daily_match_limit') or 100.00)
        layout.addRow("Daily Match Limit:", self.limit_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class AssignVendorsDialog(QDialog):
    """Dialog for assigning/unassigning vendors to a market via checkboxes."""

    def __init__(self, market, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Assign Vendors to: {market['name']}")
        self.setMinimumWidth(420)
        self.setMinimumHeight(400)
        self.market = market
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BACKGROUND}; }}
            QLabel {{ background-color: transparent; color: {TEXT_COLOR}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info = QLabel(f"Check vendors to assign to {market['name']}:")
        info.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(info)

        # Build checkboxes for all vendors
        self._checkboxes = []
        assigned_ids = get_market_vendor_ids(market['id'])
        all_vendors = get_all_vendors()

        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {LIGHT_GRAY}; border-radius: 6px; }}")
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(6)

        for v in all_vendors:
            cb = QCheckBox(f"{v['name']}" + ("" if v['is_active'] else " (inactive)"))
            cb.setChecked(v['id'] in assigned_ids)
            cb.setProperty("vendor_id", v['id'])
            cb.setStyleSheet("font-size: 13px; padding: 4px;")
            scroll_layout.addWidget(cb)
            self._checkboxes.append(cb)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_checked_vendor_ids(self):
        """Return set of vendor IDs that are checked."""
        return {
            cb.property("vendor_id")
            for cb in self._checkboxes
            if cb.isChecked()
        }


# ── Main Settings Screen ─────────────────────────────────────

class SettingsScreen(QWidget):
    """Admin settings for managing reference data."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Settings")
        title.setObjectName("screen_title")
        layout.addWidget(title)

        subtitle = QLabel("Manage markets, vendors, and payment methods")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_markets_tab(), "Markets")
        self.tabs.addTab(self._build_vendors_tab(), "Vendors")
        self.tabs.addTab(self._build_payment_methods_tab(), "Payment Methods")
        self.tabs.addTab(self._build_reset_tab(), "Reset")

        layout.addWidget(self.tabs)

    # ── Markets Tab ──────────────────────────────────────────

    def _build_markets_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        form = QFrame()
        form.setStyleSheet(CARD_FRAME_STYLE)
        fl = QHBoxLayout(form)
        fl.addWidget(make_field_label("Market Name"))
        self.market_name_input = QLineEdit()
        self.market_name_input.setPlaceholderText("e.g., Downtown Saturday Market")
        fl.addWidget(self.market_name_input)
        fl.addWidget(make_field_label("Address"))
        self.market_address_input = QLineEdit()
        self.market_address_input.setPlaceholderText("Optional address")
        fl.addWidget(self.market_address_input)
        add_btn = QPushButton("Add Market")
        add_btn.setObjectName("primary_btn")
        add_btn.clicked.connect(self._add_market)
        fl.addWidget(add_btn)
        layout.addWidget(form)

        self.markets_table = QTableWidget()
        self.markets_table.setColumnCount(6)
        self.markets_table.setHorizontalHeaderLabels(
            ["ID", "Name", "Address", "Match Limit", "Active", "Actions"]
        )
        configure_table(self.markets_table, actions_col=5, actions_width=380)
        layout.addWidget(self.markets_table)

        return tab

    # ── Vendors Tab ──────────────────────────────────────────

    def _build_vendors_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        form = QFrame()
        form.setStyleSheet(CARD_FRAME_STYLE)
        fl = QHBoxLayout(form)
        fl.addWidget(make_field_label("Vendor Name"))
        self.vendor_name_input = QLineEdit()
        self.vendor_name_input.setPlaceholderText("e.g., Green Valley Farm")
        fl.addWidget(self.vendor_name_input)
        fl.addWidget(make_field_label("Contact Info"))
        self.vendor_contact_input = QLineEdit()
        self.vendor_contact_input.setPlaceholderText("Optional")
        fl.addWidget(self.vendor_contact_input)
        add_btn = QPushButton("Add Vendor")
        add_btn.setObjectName("primary_btn")
        add_btn.clicked.connect(self._add_vendor)
        fl.addWidget(add_btn)
        layout.addWidget(form)

        self.vendors_table = QTableWidget()
        self.vendors_table.setColumnCount(5)
        self.vendors_table.setHorizontalHeaderLabels(["ID", "Name", "Contact", "Active", "Actions"])
        configure_table(self.vendors_table, actions_col=4, actions_width=140)
        layout.addWidget(self.vendors_table)

        return tab

    # ── Payment Methods Tab ──────────────────────────────────

    def _build_payment_methods_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        form = QFrame()
        form.setStyleSheet(CARD_FRAME_STYLE)
        fl = QHBoxLayout(form)
        fl.addWidget(make_field_label("Name"))
        self.pm_name_input = QLineEdit()
        self.pm_name_input.setPlaceholderText("e.g., SNAP")
        fl.addWidget(self.pm_name_input)
        fl.addWidget(make_field_label("Match %"))
        self.pm_match_spin = QDoubleSpinBox()
        self.pm_match_spin.setRange(0, 999)
        self.pm_match_spin.setDecimals(1)
        self.pm_match_spin.setSuffix("%")
        fl.addWidget(self.pm_match_spin)
        add_btn = QPushButton("Add Payment Method")
        add_btn.setObjectName("primary_btn")
        add_btn.clicked.connect(self._add_payment_method)
        fl.addWidget(add_btn)
        layout.addWidget(form)

        self.pm_table = QTableWidget()
        self.pm_table.setColumnCount(5)
        self.pm_table.setHorizontalHeaderLabels(
            ["ID", "Name", "Match %", "Active", "Actions"]
        )
        configure_table(self.pm_table, actions_col=4, actions_width=200)
        layout.addWidget(self.pm_table)

        return tab

    # ── Reset Tab ────────────────────────────────────────────

    def _build_reset_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(20)

        info = QLabel(
            "Use this to reset all stored data back to the default generic data.\n"
            "This will delete all market days, transactions, FMNP entries, "
            "audit log entries, and restore the default markets, vendors, "
            "and payment methods."
        )
        info.setWordWrap(True)
        info.setStyleSheet("font-size: 14px; padding: 12px;")
        layout.addWidget(info)

        warning = QLabel(
            "WARNING: This action cannot be undone! All existing data will be permanently lost."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(f"color: {ERROR_COLOR}; font-weight: bold; font-size: 14px; padding: 12px;")
        layout.addWidget(warning)

        reset_btn = QPushButton("Reset to Default")
        reset_btn.setObjectName("danger_btn")
        reset_btn.setMaximumWidth(250)
        reset_btn.clicked.connect(self._reset_to_default)
        layout.addWidget(reset_btn)

        layout.addStretch()
        return tab

    # ── Data Loading ─────────────────────────────────────────

    def refresh(self):
        self._load_markets()
        self._load_vendors()
        self._load_payment_methods()

    def _load_markets(self):
        conn = get_connection()
        rows = conn.execute("SELECT * FROM markets ORDER BY name").fetchall()
        self.markets_table.setSortingEnabled(False)
        self.markets_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.markets_table.setItem(i, 0, make_item(str(r['id']), r['id']))
            self.markets_table.setItem(i, 1, make_item(r['name']))
            self.markets_table.setItem(i, 2, make_item(r['address'] or ''))

            # Match Limit column
            limit_active = r['match_limit_active']
            limit_val = r['daily_match_limit'] or 100.00
            if limit_active:
                self.markets_table.setItem(i, 3, make_item(f"${limit_val:.2f}", limit_val))
            else:
                self.markets_table.setItem(i, 3, make_item("Off"))

            self.markets_table.setItem(i, 4, make_item("Yes" if r['is_active'] else "No"))

            action_widget = QWidget()
            al = QHBoxLayout(action_widget)
            al.setContentsMargins(2, 2, 2, 2)
            al.setSpacing(3)
            mid = r['id']

            edit_btn = make_action_btn("Edit", 50)
            edit_btn.clicked.connect(lambda checked, mid=mid: self._edit_market(mid))
            al.addWidget(edit_btn)

            assign_btn = make_action_btn("Vendors", 60)
            assign_btn.setToolTip("Assign Vendors to this Market")
            assign_btn.clicked.connect(lambda checked, mid=mid: self._assign_vendors(mid))
            al.addWidget(assign_btn)

            limit_btn = make_action_btn("Match Limit", 75)
            limit_btn.setToolTip("Set daily FAM match limit per customer")
            limit_btn.clicked.connect(lambda checked, mid=mid: self._edit_match_limit(mid))
            al.addWidget(limit_btn)

            limit_on = bool(limit_active)
            limit_toggle = make_action_btn("Limit On" if limit_on else "Limit Off", 65)
            limit_toggle.clicked.connect(
                lambda checked, mid=mid, active=limit_on: self._toggle_match_limit(mid, active)
            )
            al.addWidget(limit_toggle)

            is_active = r['is_active']
            toggle_btn = make_action_btn("Deactivate" if is_active else "Activate", 70)
            toggle_btn.clicked.connect(
                lambda checked, mid=mid, active=is_active: self._toggle_market(mid, active)
            )
            al.addWidget(toggle_btn)

            self.markets_table.setCellWidget(i, 5, action_widget)
            self.markets_table.setRowHeight(i, 42)
        self.markets_table.setSortingEnabled(True)

    def _load_vendors(self):
        vendors = get_all_vendors()
        self.vendors_table.setSortingEnabled(False)
        self.vendors_table.setRowCount(len(vendors))
        for i, v in enumerate(vendors):
            self.vendors_table.setItem(i, 0, make_item(str(v['id']), v['id']))
            self.vendors_table.setItem(i, 1, make_item(v['name']))
            self.vendors_table.setItem(i, 2, make_item(v.get('contact_info') or ''))
            self.vendors_table.setItem(i, 3, make_item("Yes" if v['is_active'] else "No"))

            action_widget = QWidget()
            al = QHBoxLayout(action_widget)
            al.setContentsMargins(2, 2, 2, 2)
            al.setSpacing(3)
            vid = v['id']
            is_active = v['is_active']

            edit_btn = make_action_btn("Edit", 45)
            edit_btn.clicked.connect(lambda checked, vid=vid: self._edit_vendor(vid))
            al.addWidget(edit_btn)

            toggle_btn = make_action_btn("Deactivate" if is_active else "Activate", 70)
            toggle_btn.clicked.connect(
                lambda checked, vid=vid, active=is_active: self._toggle_vendor(vid, active)
            )
            al.addWidget(toggle_btn)

            self.vendors_table.setCellWidget(i, 4, action_widget)
            self.vendors_table.setRowHeight(i, 42)
        self.vendors_table.setSortingEnabled(True)

    def _load_payment_methods(self):
        methods = get_all_payment_methods()
        self.pm_table.setSortingEnabled(False)
        self.pm_table.setRowCount(len(methods))
        for i, m in enumerate(methods):
            self.pm_table.setItem(i, 0, make_item(str(m['id']), m['id']))
            self.pm_table.setItem(i, 1, make_item(m['name']))
            self.pm_table.setItem(i, 2, make_item(f"{m['match_percent']}%", m['match_percent']))
            self.pm_table.setItem(i, 3, make_item("Yes" if m['is_active'] else "No"))

            action_widget = QWidget()
            al = QHBoxLayout(action_widget)
            al.setContentsMargins(2, 2, 2, 2)
            al.setSpacing(3)
            mid = m['id']
            is_active = m['is_active']
            sort_order = m['sort_order']

            edit_btn = make_action_btn("Edit", 40)
            edit_btn.clicked.connect(lambda checked, mid=mid: self._edit_pm(mid))
            al.addWidget(edit_btn)

            up_btn = make_action_btn("\u25B2", 24)
            up_btn.setToolTip("Move up")
            up_btn.clicked.connect(lambda checked, mid=mid, so=sort_order: self._move_pm(mid, so, -1))
            al.addWidget(up_btn)

            down_btn = make_action_btn("\u25BC", 24)
            down_btn.setToolTip("Move down")
            down_btn.clicked.connect(lambda checked, mid=mid, so=sort_order: self._move_pm(mid, so, 1))
            al.addWidget(down_btn)

            toggle_btn = make_action_btn("Deactivate" if is_active else "Activate", 70)
            toggle_btn.clicked.connect(
                lambda checked, mid=mid, active=is_active: self._toggle_pm(mid, active)
            )
            al.addWidget(toggle_btn)

            self.pm_table.setCellWidget(i, 4, action_widget)
            self.pm_table.setRowHeight(i, 42)
        self.pm_table.setSortingEnabled(True)

    # ── Market Actions ───────────────────────────────────────

    def _add_market(self):
        name = self.market_name_input.text().strip()
        address = self.market_address_input.text().strip() or None
        if not name:
            QMessageBox.warning(self, "Error", "Market name is required.")
            return
        try:
            conn = get_connection()
            conn.execute("INSERT INTO markets (name, address) VALUES (?, ?)", (name, address))
            conn.commit()
            self.market_name_input.clear()
            self.market_address_input.clear()
            self._load_markets()
        except Exception as e:
            logger.exception("Failed to add market '%s'", name)
            msg = str(e)
            if 'UNIQUE' in msg.upper():
                msg = f"A market with the name \"{name}\" already exists."
            QMessageBox.warning(self, "Error", msg)

    def _edit_market(self, market_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM markets WHERE id=?", (market_id,)).fetchone()
        if not row:
            return
        market = dict(row)

        dialog = EditMarketDialog(market, self)
        if dialog.exec() == QDialog.Accepted:
            new_name = dialog.name_input.text().strip()
            new_address = dialog.address_input.text().strip() or None
            if not new_name:
                QMessageBox.warning(self, "Error", "Market name is required.")
                return
            try:
                conn = get_connection()
                conn.execute("UPDATE markets SET name=?, address=? WHERE id=?",
                             (new_name, new_address, market_id))
                conn.commit()
                self._load_markets()
            except Exception as e:
                logger.exception("Failed to edit market %s", market_id)
                msg = str(e)
                if 'UNIQUE' in msg.upper():
                    msg = f"A market with the name \"{new_name}\" already exists."
                QMessageBox.warning(self, "Error", msg)

    def _toggle_market(self, market_id, current_active):
        conn = get_connection()
        conn.execute("UPDATE markets SET is_active=? WHERE id=?",
                     (0 if current_active else 1, market_id))
        conn.commit()
        self._load_markets()

    def _edit_match_limit(self, market_id):
        """Open dialog to adjust a market's daily match limit."""
        conn = get_connection()
        row = conn.execute("SELECT * FROM markets WHERE id=?", (market_id,)).fetchone()
        if not row:
            return
        market = dict(row)

        dialog = MatchLimitDialog(market, self)
        if dialog.exec() == QDialog.Accepted:
            new_limit = dialog.limit_spin.value()
            try:
                conn = get_connection()
                conn.execute(
                    "UPDATE markets SET daily_match_limit=? WHERE id=?",
                    (new_limit, market_id)
                )
                conn.commit()
                self._load_markets()
            except Exception as e:
                logger.exception("Failed to update match limit for market %s", market_id)
                QMessageBox.warning(self, "Error", f"Could not update match limit: {e}")

    def _toggle_match_limit(self, market_id, current_active):
        """Toggle the match limit on/off for a market."""
        conn = get_connection()
        conn.execute(
            "UPDATE markets SET match_limit_active=? WHERE id=?",
            (0 if current_active else 1, market_id)
        )
        conn.commit()
        self._load_markets()

    def _assign_vendors(self, market_id):
        """Open vendor assignment dialog for a market."""
        conn = get_connection()
        row = conn.execute("SELECT * FROM markets WHERE id=?", (market_id,)).fetchone()
        if not row:
            return
        market = dict(row)

        dialog = AssignVendorsDialog(market, self)
        if dialog.exec() == QDialog.Accepted:
            new_ids = dialog.get_checked_vendor_ids()
            old_ids = get_market_vendor_ids(market_id)

            # Add newly checked
            for vid in new_ids - old_ids:
                assign_vendor_to_market(market_id, vid)

            # Remove newly unchecked
            for vid in old_ids - new_ids:
                unassign_vendor_from_market(market_id, vid)

    # ── Vendor Actions ───────────────────────────────────────

    def _add_vendor(self):
        name = self.vendor_name_input.text().strip()
        contact = self.vendor_contact_input.text().strip() or None
        if not name:
            QMessageBox.warning(self, "Error", "Vendor name is required.")
            return
        try:
            create_vendor(name, contact)
            self.vendor_name_input.clear()
            self.vendor_contact_input.clear()
            self._load_vendors()
        except Exception as e:
            logger.exception("Failed to add vendor '%s'", name)
            QMessageBox.warning(self, "Error", f"Could not add vendor: {e}")

    def _edit_vendor(self, vendor_id):
        from fam.models.vendor import get_vendor_by_id
        vendor = get_vendor_by_id(vendor_id)
        if not vendor:
            return

        dialog = EditVendorDialog(vendor, self)
        if dialog.exec() == QDialog.Accepted:
            new_name = dialog.name_input.text().strip()
            new_contact = dialog.contact_input.text().strip() or None
            if not new_name:
                QMessageBox.warning(self, "Error", "Vendor name is required.")
                return
            try:
                update_vendor(vendor_id, name=new_name, contact_info=new_contact)
                self._load_vendors()
            except Exception as e:
                logger.exception("Failed to edit vendor %s", vendor_id)
                QMessageBox.warning(self, "Error", f"Could not update vendor: {e}")

    def _toggle_vendor(self, vid, current_active):
        update_vendor(vid, is_active=not current_active)
        self._load_vendors()

    # ── Payment Method Actions ───────────────────────────────

    def _add_payment_method(self):
        name = self.pm_name_input.text().strip()
        match_pct = self.pm_match_spin.value()
        if not name:
            QMessageBox.warning(self, "Error", "Payment method name is required.")
            return
        try:
            methods = get_all_payment_methods()
            max_sort = max((m['sort_order'] for m in methods), default=0)
            create_payment_method(name, match_pct, max_sort + 1)
            self.pm_name_input.clear()
            self.pm_match_spin.setValue(0)
            self._load_payment_methods()
        except Exception as e:
            logger.exception("Failed to add payment method '%s'", name)
            msg = str(e)
            if 'UNIQUE' in msg.upper():
                msg = f"A payment method with the name \"{name}\" already exists."
            QMessageBox.warning(self, "Error", msg)

    def _edit_pm(self, pm_id):
        from fam.models.payment_method import get_payment_method_by_id
        method = get_payment_method_by_id(pm_id)
        if not method:
            return

        dialog = EditPaymentMethodDialog(method, self)
        if dialog.exec() == QDialog.Accepted:
            new_name = dialog.name_input.text().strip()
            new_match_pct = dialog.match_spin.value()
            if not new_name:
                QMessageBox.warning(self, "Error", "Payment method name is required.")
                return
            update_payment_method(pm_id, name=new_name, match_percent=new_match_pct)
            self._load_payment_methods()

    def _move_pm(self, pm_id, current_sort, direction):
        """Move a payment method up (-1) or down (+1) in sort order."""
        methods = get_all_payment_methods()
        idx = None
        for i, m in enumerate(methods):
            if m['id'] == pm_id:
                idx = i
                break
        if idx is None:
            return

        swap_idx = idx + direction
        if swap_idx < 0 or swap_idx >= len(methods):
            return

        other = methods[swap_idx]
        update_payment_method(pm_id, sort_order=other['sort_order'])
        update_payment_method(other['id'], sort_order=current_sort)
        self._load_payment_methods()

    def _toggle_pm(self, mid, current_active):
        update_payment_method(mid, is_active=not current_active)
        self._load_payment_methods()

    # ── Reset to Default ─────────────────────────────────────

    def _reset_to_default(self):
        result = QMessageBox.warning(
            self, "Confirm Reset",
            "Are you sure you want to reset ALL data to defaults?\n\n"
            "This will permanently delete:\n"
            "  - All market days\n"
            "  - All transactions and payment records\n"
            "  - All FMNP entries\n"
            "  - All audit log entries\n"
            "  - All custom markets, vendors, and payment methods\n\n"
            "Default sample data will be restored.\n\n"
            "This action CANNOT be undone!",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if result != QMessageBox.Yes:
            return

        # Double-confirm
        result2 = QMessageBox.critical(
            self, "Final Confirmation",
            "This is your last chance! ALL data will be lost.\n\nProceed with reset?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if result2 != QMessageBox.Yes:
            return

        try:
            conn = get_connection()
            # Delete all data in dependency order
            conn.execute("DELETE FROM audit_log")
            conn.execute("DELETE FROM payment_line_items")
            conn.execute("DELETE FROM fmnp_entries")
            conn.execute("DELETE FROM transactions")
            conn.execute("DELETE FROM customer_orders")
            conn.execute("DELETE FROM market_days")
            conn.execute("DELETE FROM payment_methods")
            conn.execute("DELETE FROM market_vendors")
            conn.execute("DELETE FROM vendors")
            conn.execute("DELETE FROM markets")
            conn.commit()

            # Re-seed with defaults
            from fam.database.seed import seed_if_empty
            seed_if_empty()

            self.refresh()
            QMessageBox.information(self, "Reset Complete",
                                    "All data has been reset to defaults.")
        except Exception as e:
            QMessageBox.critical(self, "Reset Error", f"Failed to reset: {str(e)}")
