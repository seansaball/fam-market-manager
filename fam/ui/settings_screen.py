"""Settings screen for managing markets, vendors, and payment methods."""

import logging
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QCheckBox, QMessageBox, QDialog, QFileDialog, QScrollArea,
    QFormLayout, QDialogButtonBox, QSizePolicy, QProgressBar, QComboBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush

from fam.database.connection import get_connection

logger = logging.getLogger('fam.ui.settings_screen')
from fam.models.vendor import (
    get_all_vendors, get_vendor_by_id, create_vendor, update_vendor,
    get_market_vendor_ids, get_vendor_market_ids,
    assign_vendor_to_market, unassign_vendor_from_market
)
from fam.models.payment_method import (
    get_all_payment_methods, create_payment_method, update_payment_method,
    get_market_payment_method_ids, assign_payment_method_to_market,
    unassign_payment_method_from_market
)
from fam.ui.styles import (
    WHITE, LIGHT_GRAY, ERROR_COLOR, PRIMARY_GREEN, ACCENT_GREEN,
    BACKGROUND, TEXT_COLOR, SUBTITLE_GRAY
)
from fam.ui.helpers import (
    make_field_label, make_item, make_action_btn, configure_table,
    NoScrollDoubleSpinBox
)


_COMPACT_FRAME = f"""
    QFrame {{
        background-color: {WHITE};
        border: 1px solid #E2E2E2;
        border-radius: 8px;
        padding: 6px 10px;
    }}
"""

_FORM_ROW_HEIGHT = 36

# Compact overrides so setFixedHeight can actually win over the global
# stylesheet's generous padding / min-height values.
_FORM_INPUT_STYLE = "min-height: 0px; padding: 6px 10px;"
_FORM_BTN_STYLE = "min-height: 0px; padding: 6px 16px;"


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

        self.match_spin = NoScrollDoubleSpinBox()
        self.match_spin.setRange(0, 999)
        self.match_spin.setDecimals(1)
        self.match_spin.setSuffix("%")
        self.match_spin.setValue(method['match_percent'])
        layout.addRow("Match %:", self.match_spin)

        # Denomination: checkbox + $ value input
        denom_row = QHBoxLayout()
        self.denom_check = QCheckBox("Denomination")
        self.denom_check.setStyleSheet(f"""
            QCheckBox {{
                font-size: 13px; padding: 4px; background-color: transparent;
            }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                background-color: {WHITE};
                border: 2px solid #AAAAAA;
                border-radius: 3px;
            }}
            QCheckBox::indicator:checked {{
                background-color: {ACCENT_GREEN};
                border-color: {PRIMARY_GREEN};
            }}
        """)
        self.denom_check.toggled.connect(self._toggle_denom)
        denom_row.addWidget(self.denom_check)
        self.denom_spin = NoScrollDoubleSpinBox()
        self.denom_spin.setRange(1, 999)
        self.denom_spin.setDecimals(2)
        self.denom_spin.setPrefix("$ ")
        self.denom_spin.setValue(25.0)
        self.denom_spin.setEnabled(False)
        denom_row.addWidget(self.denom_spin)
        layout.addRow("", denom_row)

        # Initialize from existing data
        existing_denom = method.get('denomination')
        if existing_denom and existing_denom > 0:
            self.denom_check.setChecked(True)
            self.denom_spin.setValue(existing_denom)
        else:
            self.denom_check.setChecked(False)

        # Photo Receipt requirement — hidden by default, shown via show_photo_required()
        self._photo_required_label = QLabel("Photo Receipt:")
        self.photo_required_combo = QComboBox()
        self.photo_required_combo.addItems(["Off", "Optional", "Mandatory"])
        self._photo_required_label.setVisible(False)
        self.photo_required_combo.setVisible(False)
        layout.addRow(self._photo_required_label, self.photo_required_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _toggle_denom(self, checked):
        self.denom_spin.setEnabled(checked)

    def get_denomination(self):
        """Return denomination value if active, else None."""
        if self.denom_check.isChecked():
            return self.denom_spin.value()
        return None

    def show_photo_required(self):
        """Show the Photo Receipt dropdown and set its value from the method data."""
        self._photo_required_shown = True
        self._photo_required_label.setVisible(True)
        self.photo_required_combo.setVisible(True)
        current = self.method.get('photo_required') or 'Off'
        idx = self.photo_required_combo.findText(current)
        if idx >= 0:
            self.photo_required_combo.setCurrentIndex(idx)

    def get_photo_required(self):
        """Return the selected photo requirement, or 'Off' if not shown."""
        if not getattr(self, '_photo_required_shown', False):
            return 'Off'
        return self.photo_required_combo.currentText()


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

        self.limit_spin = NoScrollDoubleSpinBox()
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
        scroll_widget.setStyleSheet(f"background-color: {WHITE};")
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(6)

        for v in all_vendors:
            cb = QCheckBox(f"{v['name']}" + ("" if v['is_active'] else " (inactive)"))
            cb.setChecked(v['id'] in assigned_ids)
            cb.setProperty("vendor_id", v['id'])
            cb.setStyleSheet(f"""
                QCheckBox {{
                    font-size: 13px; padding: 4px; background-color: {WHITE};
                }}
                QCheckBox::indicator {{
                    width: 16px; height: 16px;
                    background-color: {WHITE};
                    border: 2px solid #AAAAAA;
                    border-radius: 3px;
                }}
                QCheckBox::indicator:checked {{
                    background-color: {ACCENT_GREEN};
                    border-color: {PRIMARY_GREEN};
                }}
            """)
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


class AssignPaymentMethodsDialog(QDialog):
    """Dialog for assigning/unassigning payment methods to a market via checkboxes."""

    def __init__(self, market, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Assign Payment Methods to: {market['name']}")
        self.setMinimumWidth(420)
        self.setMinimumHeight(400)
        self.market = market
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BACKGROUND}; }}
            QLabel {{ background-color: transparent; color: {TEXT_COLOR}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info = QLabel(f"Check payment methods accepted at {market['name']}:")
        info.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(info)

        # Build checkboxes for all payment methods
        self._checkboxes = []
        assigned_ids = get_market_payment_method_ids(market['id'])
        all_methods = get_all_payment_methods()

        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {LIGHT_GRAY}; border-radius: 6px; }}")
        scroll_widget = QWidget()
        scroll_widget.setStyleSheet(f"background-color: {WHITE};")
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(6)

        for m in all_methods:
            label = f"{m['name']} ({m['match_percent']:.0f}% match)"
            if not m['is_active']:
                label += " (inactive)"
            cb = QCheckBox(label)
            cb.setChecked(m['id'] in assigned_ids)
            cb.setProperty("pm_id", m['id'])
            cb.setStyleSheet(f"""
                QCheckBox {{
                    font-size: 13px; padding: 4px; background-color: {WHITE};
                }}
                QCheckBox::indicator {{
                    width: 16px; height: 16px;
                    background-color: {WHITE};
                    border: 2px solid #AAAAAA;
                    border-radius: 3px;
                }}
                QCheckBox::indicator:checked {{
                    background-color: {ACCENT_GREEN};
                    border-color: {PRIMARY_GREEN};
                }}
            """)
            scroll_layout.addWidget(cb)
            self._checkboxes.append(cb)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_checked_payment_method_ids(self):
        """Return set of payment method IDs that are checked."""
        return {
            cb.property("pm_id")
            for cb in self._checkboxes
            if cb.isChecked()
        }


class AssignMarketsDialog(QDialog):
    """Dialog for assigning/unassigning a vendor to markets via checkboxes."""

    def __init__(self, vendor, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Assign Markets for: {vendor['name']}")
        self.setMinimumWidth(420)
        self.setMinimumHeight(400)
        self.vendor = vendor
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BACKGROUND}; }}
            QLabel {{ background-color: transparent; color: {TEXT_COLOR}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info = QLabel(f"Check markets to assign {vendor['name']} to:")
        info.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(info)

        # Build checkboxes for all markets
        self._checkboxes = []
        assigned_ids = get_vendor_market_ids(vendor['id'])
        conn = get_connection()
        all_markets = [
            dict(r) for r in conn.execute("SELECT * FROM markets ORDER BY name").fetchall()
        ]

        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {LIGHT_GRAY}; border-radius: 6px; }}")
        scroll_widget = QWidget()
        scroll_widget.setStyleSheet(f"background-color: {WHITE};")
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(6)

        for m in all_markets:
            label = m['name']
            if not m.get('is_active', 1):
                label += " (inactive)"
            cb = QCheckBox(label)
            cb.setChecked(m['id'] in assigned_ids)
            cb.setProperty("market_id", m['id'])
            cb.setStyleSheet(f"""
                QCheckBox {{
                    font-size: 13px; padding: 4px; background-color: {WHITE};
                }}
                QCheckBox::indicator {{
                    width: 16px; height: 16px;
                    background-color: {WHITE};
                    border: 2px solid #AAAAAA;
                    border-radius: 3px;
                }}
                QCheckBox::indicator:checked {{
                    background-color: {ACCENT_GREEN};
                    border-color: {PRIMARY_GREEN};
                }}
            """)
            scroll_layout.addWidget(cb)
            self._checkboxes.append(cb)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_checked_market_ids(self):
        """Return set of market IDs that are checked."""
        return {
            cb.property("market_id")
            for cb in self._checkboxes
            if cb.isChecked()
        }


# ── Import Preview Dialog ─────────────────────────────────────

class ImportPreviewDialog(QDialog):
    """Shows a preview of what will be imported before applying changes."""

    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Settings — Preview")
        self.setMinimumWidth(560)
        self.setMinimumHeight(480)
        self.result = result
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BACKGROUND}; }}
            QLabel {{ background-color: transparent; color: {TEXT_COLOR}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 16, 20, 14)

        title = QLabel("Import Preview")
        title.setStyleSheet(f"""
            font-size: 18px; font-weight: bold;
            color: {PRIMARY_GREEN}; background: transparent;
        """)
        layout.addWidget(title)

        # Show errors if any
        if result.errors:
            err_label = QLabel(
                f"⚠ {len(result.errors)} warning(s) during parsing:"
            )
            err_label.setStyleSheet(f"""
                font-size: 12px; font-weight: bold;
                color: {ERROR_COLOR}; background: transparent;
            """)
            layout.addWidget(err_label)
            for err in result.errors[:5]:  # Show max 5 errors
                el = QLabel(f"  • {err}")
                el.setWordWrap(True)
                el.setStyleSheet(f"font-size: 11px; color: {ERROR_COLOR}; background: transparent;")
                layout.addWidget(el)
            if len(result.errors) > 5:
                more = QLabel(f"  … and {len(result.errors) - 5} more")
                more.setStyleSheet(f"font-size: 11px; color: {ERROR_COLOR}; background: transparent;")
                layout.addWidget(more)

        # Scrollable preview content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ border: 1px solid {LIGHT_GRAY}; border-radius: 6px; }}
        """)
        scroll_widget = QWidget()
        scroll_widget.setStyleSheet(f"background-color: {WHITE};")
        sl = QVBoxLayout(scroll_widget)
        sl.setSpacing(8)
        sl.setContentsMargins(14, 10, 14, 10)

        # Markets section
        self._add_section(sl, "Markets",
                          result.new_markets, result.skipped_markets,
                          lambda m: m.name,
                          lambda m: f"${m.daily_match_limit:.2f} limit" + (
                              "" if m.limit_active else " (off)"))

        # Vendors section
        self._add_section(sl, "Vendors",
                          result.new_vendors, result.skipped_vendors,
                          lambda v: v.name,
                          lambda v: v.contact_info or "")

        # Payment Methods section
        self._add_section(sl, "Payment Methods",
                          result.new_payment_methods, result.skipped_payment_methods,
                          lambda p: p.name,
                          lambda p: f"{p.match_percent}% match")

        # Assignments summary
        if result.vendor_assignments or result.pm_assignments:
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet(f"background-color: {LIGHT_GRAY};")
            sl.addWidget(sep)
            assign_label = QLabel("Assignments")
            assign_label.setStyleSheet(f"""
                font-size: 14px; font-weight: bold;
                color: {TEXT_COLOR}; background: transparent;
            """)
            sl.addWidget(assign_label)
            if result.vendor_assignments:
                va = QLabel(f"  {len(result.vendor_assignments)} vendor → market assignment(s)")
                va.setStyleSheet("font-size: 12px; background: transparent;")
                sl.addWidget(va)
            if result.pm_assignments:
                pa = QLabel(f"  {len(result.pm_assignments)} payment method → market assignment(s)")
                pa.setStyleSheet("font-size: 12px; background: transparent;")
                sl.addWidget(pa)

        sl.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        # Summary line
        total_new = (len(result.new_markets) + len(result.new_vendors)
                     + len(result.new_payment_methods))
        total_skipped = (len(result.skipped_markets) + len(result.skipped_vendors)
                         + len(result.skipped_payment_methods))

        if total_new == 0:
            summary_text = "Nothing new to import — all items already exist."
            summary_color = SUBTITLE_GRAY
        else:
            summary_text = (
                f"Will add {total_new} new item(s)"
                + (f", skip {total_skipped} existing" if total_skipped else "")
                + "."
            )
            summary_color = ACCENT_GREEN

        summary = QLabel(summary_text)
        summary.setStyleSheet(f"""
            font-size: 13px; font-weight: bold;
            color: {summary_color}; background: transparent;
            padding: 6px 0;
        """)
        layout.addWidget(summary)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 8px 20px; font-size: 13px; min-height: 0px;
                border: 1px solid {LIGHT_GRAY}; border-radius: 6px;
                background-color: {WHITE}; color: {TEXT_COLOR};
            }}
            QPushButton:hover {{
                background-color: #F0EFEB;
                border-color: {PRIMARY_GREEN};
            }}
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self.import_btn = QPushButton("Import")
        self.import_btn.setCursor(Qt.PointingHandCursor)
        self.import_btn.setObjectName("primary_btn")
        self.import_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 8px 24px; font-size: 13px; min-height: 0px;
                border-radius: 6px; background-color: {PRIMARY_GREEN};
                color: white; font-weight: bold; border: none;
            }}
            QPushButton:hover {{
                background-color: {ACCENT_GREEN};
            }}
            QPushButton:disabled {{
                background-color: {LIGHT_GRAY}; color: #999;
            }}
        """)
        self.import_btn.setEnabled(total_new > 0)
        self.import_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.import_btn)

        layout.addLayout(btn_row)

    def _add_section(self, layout, title, new_items, skipped_items,
                     name_fn, detail_fn):
        """Add a preview section for one entity type."""
        if not new_items and not skipped_items:
            return

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {LIGHT_GRAY};")
        layout.addWidget(sep)

        header = QLabel(f"{title}  ({len(new_items)} new, {len(skipped_items)} existing)")
        header.setStyleSheet(f"""
            font-size: 14px; font-weight: bold;
            color: {TEXT_COLOR}; background: transparent;
        """)
        layout.addWidget(header)

        for item in new_items:
            detail = detail_fn(item)
            text = f"  ✚  {name_fn(item)}"
            if detail:
                text += f"  —  {detail}"
            lbl = QLabel(text)
            lbl.setStyleSheet(f"font-size: 12px; color: {ACCENT_GREEN}; background: transparent;")
            layout.addWidget(lbl)

        for item in skipped_items:
            text = f"  ━  {name_fn(item)}  (already exists)"
            lbl = QLabel(text)
            lbl.setStyleSheet(f"font-size: 12px; color: {SUBTITLE_GRAY}; background: transparent;")
            layout.addWidget(lbl)


# ── Main Settings Screen ─────────────────────────────────────

class SettingsScreen(QWidget):
    """Admin settings for managing reference data."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        # Header row with title + Import/Export buttons
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        title = QLabel("Settings")
        title.setObjectName("screen_title")
        header_row.addWidget(title)

        header_row.addStretch()

        self.import_btn = QPushButton("\U0001F4E5  Import Settings")
        self.import_btn.setObjectName("settings_import_btn")
        self.import_btn.setCursor(Qt.PointingHandCursor)
        self.import_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 7px 16px; font-size: 12px; min-height: 0px;
                border: 1px solid {PRIMARY_GREEN}; border-radius: 6px;
                background-color: {PRIMARY_GREEN}; color: white;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {ACCENT_GREEN};
                border-color: {ACCENT_GREEN};
            }}
        """)
        self.import_btn.clicked.connect(self._import_settings)
        header_row.addWidget(self.import_btn)

        export_btn = QPushButton("\U0001F4E4  Export Settings")
        export_btn.setCursor(Qt.PointingHandCursor)
        export_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 7px 16px; font-size: 12px; min-height: 0px;
                border: 1px solid {LIGHT_GRAY}; border-radius: 6px;
                background-color: {WHITE}; color: {TEXT_COLOR};
            }}
            QPushButton:hover {{
                background-color: #F0EFEB;
                border-color: {PRIMARY_GREEN};
                color: {PRIMARY_GREEN};
            }}
        """)
        export_btn.clicked.connect(self._export_settings)
        header_row.addWidget(export_btn)

        layout.addLayout(header_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_markets_tab(), "Markets")
        self.tabs.addTab(self._build_vendors_tab(), "Vendors")
        self.tabs.addTab(self._build_payment_methods_tab(), "Payment Methods")
        self.tabs.addTab(self._build_preferences_tab(), "Preferences")
        self.cloud_sync_tab = self._build_cloud_sync_tab()
        self.tabs.addTab(self.cloud_sync_tab, "Cloud Sync")
        self.updates_tab = self._build_updates_tab()
        self.tabs.addTab(self.updates_tab, "Updates")
        self.tabs.addTab(self._build_reset_tab(), "Reset")

        layout.addWidget(self.tabs)

    # ── Markets Tab ──────────────────────────────────────────

    def _build_markets_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        form = QFrame()
        form.setStyleSheet(_COMPACT_FRAME)
        fl = QHBoxLayout(form)
        lbl1 = make_field_label("Market Name")
        lbl1.setFixedHeight(_FORM_ROW_HEIGHT)
        fl.addWidget(lbl1)
        self.market_name_input = QLineEdit()
        self.market_name_input.setFixedHeight(_FORM_ROW_HEIGHT)
        self.market_name_input.setStyleSheet(_FORM_INPUT_STYLE)
        self.market_name_input.setPlaceholderText("e.g., Downtown Saturday Market")
        fl.addWidget(self.market_name_input)
        lbl2 = make_field_label("Address")
        lbl2.setFixedHeight(_FORM_ROW_HEIGHT)
        fl.addWidget(lbl2)
        self.market_address_input = QLineEdit()
        self.market_address_input.setFixedHeight(_FORM_ROW_HEIGHT)
        self.market_address_input.setStyleSheet(_FORM_INPUT_STYLE)
        self.market_address_input.setPlaceholderText("Optional address")
        fl.addWidget(self.market_address_input)
        add_btn = QPushButton("Add Market")
        add_btn.setObjectName("primary_btn")
        add_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        add_btn.setStyleSheet(_FORM_BTN_STYLE)
        add_btn.clicked.connect(self._add_market)
        fl.addWidget(add_btn)
        layout.addWidget(form)

        self.markets_table = QTableWidget()
        self.markets_table.setColumnCount(6)
        self.markets_table.setHorizontalHeaderLabels(
            ["ID", "Name", "Address", "Match Limit", "Active", "Actions"]
        )
        configure_table(self.markets_table, actions_col=5, actions_width=450)
        layout.addWidget(self.markets_table)

        return tab

    # ── Vendors Tab ──────────────────────────────────────────

    def _build_vendors_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        form = QFrame()
        form.setStyleSheet(_COMPACT_FRAME)
        fl = QHBoxLayout(form)
        lbl1 = make_field_label("Vendor Name")
        lbl1.setFixedHeight(_FORM_ROW_HEIGHT)
        fl.addWidget(lbl1)
        self.vendor_name_input = QLineEdit()
        self.vendor_name_input.setFixedHeight(_FORM_ROW_HEIGHT)
        self.vendor_name_input.setStyleSheet(_FORM_INPUT_STYLE)
        self.vendor_name_input.setPlaceholderText("e.g., Green Valley Farm")
        fl.addWidget(self.vendor_name_input)
        lbl2 = make_field_label("Contact Info")
        lbl2.setFixedHeight(_FORM_ROW_HEIGHT)
        fl.addWidget(lbl2)
        self.vendor_contact_input = QLineEdit()
        self.vendor_contact_input.setFixedHeight(_FORM_ROW_HEIGHT)
        self.vendor_contact_input.setStyleSheet(_FORM_INPUT_STYLE)
        self.vendor_contact_input.setPlaceholderText("Optional")
        fl.addWidget(self.vendor_contact_input)
        add_btn = QPushButton("Add Vendor")
        add_btn.setObjectName("primary_btn")
        add_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        add_btn.setStyleSheet(_FORM_BTN_STYLE)
        add_btn.clicked.connect(self._add_vendor)
        fl.addWidget(add_btn)
        layout.addWidget(form)

        self.vendors_table = QTableWidget()
        self.vendors_table.setColumnCount(5)
        self.vendors_table.setHorizontalHeaderLabels(["ID", "Name", "Contact", "Active", "Actions"])
        configure_table(self.vendors_table, actions_col=4, actions_width=220)
        layout.addWidget(self.vendors_table)

        return tab

    # ── Payment Methods Tab ──────────────────────────────────

    def _build_payment_methods_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        form = QFrame()
        form.setStyleSheet(_COMPACT_FRAME)
        fl = QHBoxLayout(form)
        lbl1 = make_field_label("Name")
        lbl1.setFixedHeight(_FORM_ROW_HEIGHT)
        fl.addWidget(lbl1)
        self.pm_name_input = QLineEdit()
        self.pm_name_input.setFixedHeight(_FORM_ROW_HEIGHT)
        self.pm_name_input.setStyleSheet(_FORM_INPUT_STYLE)
        self.pm_name_input.setPlaceholderText("e.g., SNAP")
        fl.addWidget(self.pm_name_input)
        lbl2 = make_field_label("Match %")
        lbl2.setFixedHeight(_FORM_ROW_HEIGHT)
        fl.addWidget(lbl2)
        self.pm_match_spin = NoScrollDoubleSpinBox()
        self.pm_match_spin.setFixedHeight(_FORM_ROW_HEIGHT)
        self.pm_match_spin.setStyleSheet(_FORM_INPUT_STYLE)
        self.pm_match_spin.setRange(0, 999)
        self.pm_match_spin.setDecimals(1)
        self.pm_match_spin.setSuffix("%")
        fl.addWidget(self.pm_match_spin)
        self.pm_denom_check = QCheckBox("Denom.")
        self.pm_denom_check.setFixedHeight(_FORM_ROW_HEIGHT)
        self.pm_denom_check.setStyleSheet(f"""
            QCheckBox {{
                font-size: 13px; padding: 4px; background-color: transparent;
            }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                background-color: {WHITE};
                border: 2px solid #AAAAAA;
                border-radius: 3px;
            }}
            QCheckBox::indicator:checked {{
                background-color: {ACCENT_GREEN};
                border-color: {PRIMARY_GREEN};
            }}
        """)
        self.pm_denom_check.toggled.connect(self._toggle_add_denom)
        fl.addWidget(self.pm_denom_check)
        self.pm_denom_spin = NoScrollDoubleSpinBox()
        self.pm_denom_spin.setFixedHeight(_FORM_ROW_HEIGHT)
        self.pm_denom_spin.setStyleSheet(_FORM_INPUT_STYLE)
        self.pm_denom_spin.setRange(1, 999)
        self.pm_denom_spin.setDecimals(2)
        self.pm_denom_spin.setPrefix("$ ")
        self.pm_denom_spin.setValue(25.0)
        self.pm_denom_spin.setEnabled(False)
        fl.addWidget(self.pm_denom_spin)
        add_btn = QPushButton("Add Payment Method")
        add_btn.setObjectName("primary_btn")
        add_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        add_btn.setStyleSheet(_FORM_BTN_STYLE)
        add_btn.clicked.connect(self._add_payment_method)
        fl.addWidget(add_btn)
        layout.addWidget(form)

        self.pm_table = QTableWidget()
        self.pm_table.setColumnCount(6)
        self.pm_table.setHorizontalHeaderLabels(
            ["ID", "Name", "Match %", "Denom.", "Active", "Actions"]
        )
        configure_table(self.pm_table, actions_col=5, actions_width=200)
        layout.addWidget(self.pm_table)

        return tab

    # ── Preferences Tab ───────────────────────────────────────

    def _build_preferences_tab(self):
        from fam.utils.app_settings import (
            get_large_receipt_threshold, set_large_receipt_threshold,
            get_market_code, get_device_id
        )

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(16)

        # ── Section: Device Identity ──────────────────────────
        id_label = QLabel("Device Identity")
        id_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {PRIMARY_GREEN}; "
            "padding: 8px 0 0 0; background: transparent;"
        )
        layout.addWidget(id_label)

        id_desc = QLabel(
            "These identifiers distinguish this device's data when "
            "multiple markets send reports to the finance team. "
            "The market code is automatically derived from the market "
            "name when a market day is opened."
        )
        id_desc.setWordWrap(True)
        id_desc.setStyleSheet(
            f"font-size: 13px; color: {TEXT_COLOR}; padding: 0 0 4px 0; "
            "background: transparent;"
        )
        layout.addWidget(id_desc)

        id_frame = QFrame()
        id_frame.setStyleSheet(_COMPACT_FRAME)
        id_fl = QHBoxLayout(id_frame)

        code_lbl = make_field_label("Market Code")
        code_lbl.setFixedHeight(_FORM_ROW_HEIGHT)
        id_fl.addWidget(code_lbl)

        self._market_code_display = QLabel(get_market_code() or "Not Set")
        self._market_code_display.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {TEXT_COLOR}; "
            "letter-spacing: 3px; background: transparent; padding: 0 8px;"
        )
        self._market_code_display.setFixedHeight(_FORM_ROW_HEIGHT)
        id_fl.addWidget(self._market_code_display)

        id_fl.addSpacing(20)

        device_lbl = make_field_label("Device ID")
        device_lbl.setFixedHeight(_FORM_ROW_HEIGHT)
        id_fl.addWidget(device_lbl)

        device_id = get_device_id() or "Unknown"
        # Show first 8 chars for brevity
        short_id = device_id[:8] + "..." if len(device_id) > 12 else device_id
        device_display = QLabel(short_id)
        device_display.setToolTip(device_id)
        device_display.setStyleSheet(
            f"font-size: 12px; color: {SUBTITLE_GRAY}; "
            "background: transparent; padding: 0 8px;"
        )
        device_display.setFixedHeight(_FORM_ROW_HEIGHT)
        id_fl.addWidget(device_display)

        id_fl.addStretch()
        layout.addWidget(id_frame)

        # ── Section: Receipt Warnings ──────────────────────────
        section_label = QLabel("Receipt Warnings")
        section_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {PRIMARY_GREEN}; "
            "padding: 8px 0 0 0; background: transparent;"
        )
        layout.addWidget(section_label)

        desc = QLabel(
            "Show a confirmation dialog when a receipt total exceeds the "
            "threshold below. This helps catch data-entry mistakes like "
            "typing $1,000 instead of $10.00."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"font-size: 13px; color: {TEXT_COLOR}; padding: 0 0 4px 0; "
            "background: transparent;"
        )
        layout.addWidget(desc)

        form = QFrame()
        form.setStyleSheet(_COMPACT_FRAME)
        fl = QHBoxLayout(form)

        lbl = make_field_label("Warning threshold ($)")
        lbl.setFixedHeight(_FORM_ROW_HEIGHT)
        fl.addWidget(lbl)

        self._threshold_spin = NoScrollDoubleSpinBox()
        self._threshold_spin.setRange(1.00, 99_999.99)
        self._threshold_spin.setDecimals(2)
        self._threshold_spin.setPrefix("$ ")
        self._threshold_spin.setValue(get_large_receipt_threshold())
        self._threshold_spin.setFixedHeight(_FORM_ROW_HEIGHT)
        self._threshold_spin.setFixedWidth(160)
        self._threshold_spin.setStyleSheet(_FORM_INPUT_STYLE)
        fl.addWidget(self._threshold_spin)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("primary_btn")
        save_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        save_btn.setStyleSheet(_FORM_BTN_STYLE)
        save_btn.clicked.connect(self._save_preferences)
        fl.addWidget(save_btn)

        self._pref_status = QLabel("")
        self._pref_status.setStyleSheet(
            f"color: {ACCENT_GREEN}; font-weight: bold; background: transparent;"
        )
        self._pref_status.setVisible(False)
        fl.addWidget(self._pref_status)

        fl.addStretch()
        layout.addWidget(form)

        layout.addStretch()
        return tab

    # ── Cloud Sync Tab ────────────────────────────────────────────

    def _build_cloud_sync_tab(self):
        from fam.utils.app_settings import get_sync_spreadsheet_id, get_setting

        tab = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(16)

        sync_desc = QLabel(
            "Sync market day data to a shared Google Sheet for centralized "
            "reporting. This feature is optional \u2014 the app works fully "
            "offline without it."
        )
        sync_desc.setWordWrap(True)
        sync_desc.setStyleSheet(
            f"font-size: 13px; color: {TEXT_COLOR}; padding: 0 0 4px 0; "
            "background: transparent;"
        )
        layout.addWidget(sync_desc)

        sync_frame = QFrame()
        sync_frame.setStyleSheet(_COMPACT_FRAME)
        sync_fl = QVBoxLayout(sync_frame)
        sync_fl.setSpacing(8)

        # ─ Credentials row ─
        creds_row = QHBoxLayout()
        creds_lbl = make_field_label("Credentials")
        creds_lbl.setFixedHeight(_FORM_ROW_HEIGHT)
        creds_row.addWidget(creds_lbl)

        self._sync_creds_status = QLabel("Not configured")
        self._sync_creds_status.setStyleSheet(
            f"font-size: 13px; color: {SUBTITLE_GRAY}; "
            "background: transparent; padding: 0 8px;"
        )
        self._sync_creds_status.setFixedHeight(_FORM_ROW_HEIGHT)
        creds_row.addWidget(self._sync_creds_status)

        browse_btn = QPushButton("Browse...")
        browse_btn.setObjectName("secondary_btn")
        browse_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        browse_btn.setStyleSheet(_FORM_BTN_STYLE)
        browse_btn.clicked.connect(self._browse_credentials)
        creds_row.addWidget(browse_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        remove_btn.setStyleSheet(_FORM_BTN_STYLE)
        remove_btn.clicked.connect(self._remove_credentials)
        creds_row.addWidget(remove_btn)

        creds_row.addStretch()
        sync_fl.addLayout(creds_row)

        # ─ Spreadsheet URL row ─
        sheet_row = QHBoxLayout()
        sheet_lbl = make_field_label("Spreadsheet URL")
        sheet_lbl.setFixedHeight(_FORM_ROW_HEIGHT)
        sheet_row.addWidget(sheet_lbl)

        self._sheet_id_input = QLineEdit()
        self._sheet_id_input.setPlaceholderText(
            "Paste the full Google Sheet URL")
        current_sheet_id = get_sync_spreadsheet_id() or ''
        if current_sheet_id:
            self._sheet_id_input.setText(
                f"https://docs.google.com/spreadsheets/d/{current_sheet_id}/edit")
        self._sheet_id_input.setFixedHeight(_FORM_ROW_HEIGHT)
        self._sheet_id_input.setMinimumWidth(300)
        self._sheet_id_input.setStyleSheet(_FORM_INPUT_STYLE)
        sheet_row.addWidget(self._sheet_id_input)

        self._view_sheet_btn = QPushButton("Open Sheet")
        self._view_sheet_btn.setObjectName("secondary_btn")
        self._view_sheet_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        self._view_sheet_btn.setStyleSheet(_FORM_BTN_STYLE)
        self._view_sheet_btn.clicked.connect(self._open_sheet)
        sheet_row.addWidget(self._view_sheet_btn)

        sheet_row.addStretch()
        sync_fl.addLayout(sheet_row)

        sheet_hint = QLabel(
            "Paste the full Google Sheet URL.<br>"
            "Example: <b>https://docs.google.com/spreadsheets/d/abc123.../edit</b>"
        )
        sheet_hint.setWordWrap(True)
        sheet_hint.setStyleSheet(
            f"font-size: 11px; color: {SUBTITLE_GRAY}; "
            "background: transparent; padding: 0 0 0 4px;"
        )
        sync_fl.addWidget(sheet_hint)

        # ─ Test Connection + status ─
        conn_row = QHBoxLayout()
        test_btn = QPushButton("Test Connection")
        test_btn.setObjectName("secondary_btn")
        test_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        test_btn.setStyleSheet(_FORM_BTN_STYLE)
        test_btn.clicked.connect(self._test_sync_connection)
        conn_row.addWidget(test_btn)

        self._sync_conn_status = QLabel("")
        self._sync_conn_status.setStyleSheet(
            f"font-weight: bold; background: transparent; "
            f"padding: 0 8px;"
        )
        self._sync_conn_status.setVisible(False)
        conn_row.addWidget(self._sync_conn_status)

        self._drive_conn_status = QLabel("")
        self._drive_conn_status.setStyleSheet(
            f"font-weight: bold; background: transparent; "
            f"padding: 0 8px;"
        )
        self._drive_conn_status.setVisible(False)
        conn_row.addWidget(self._drive_conn_status)

        conn_row.addStretch()
        sync_fl.addLayout(conn_row)

        # ─ Auto-sync checkboxes ─
        self._sync_on_close_cb = QCheckBox(
            "Auto-sync when market day closes")
        self._sync_on_close_cb.setChecked(
            get_setting('sync_on_close') == '1')
        _sync_cb_style = f"""
            QCheckBox {{
                font-size: 13px; padding: 4px; background-color: {WHITE};
            }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                background-color: {WHITE};
                border: 2px solid #AAAAAA;
                border-radius: 3px;
            }}
            QCheckBox::indicator:checked {{
                background-color: {ACCENT_GREEN};
                border-color: {PRIMARY_GREEN};
            }}
        """
        self._sync_on_close_cb.setStyleSheet(_sync_cb_style)
        sync_fl.addWidget(self._sync_on_close_cb)

        self._sync_periodic_cb = QCheckBox(
            "Also sync every 5 minutes while market day is open")
        self._sync_periodic_cb.setChecked(
            get_setting('sync_periodic') == '1')
        self._sync_periodic_cb.setStyleSheet(_sync_cb_style)
        sync_fl.addWidget(self._sync_periodic_cb)

        # ─ Photos folder row ─
        photos_sep = QLabel("Payment Photos Sync")
        photos_sep.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {PRIMARY_GREEN}; "
            "background: transparent; padding: 8px 0 2px 0;"
        )
        sync_fl.addWidget(photos_sep)

        folder_row = QHBoxLayout()
        folder_lbl = make_field_label("Drive Folder")
        folder_lbl.setFixedHeight(_FORM_ROW_HEIGHT)
        folder_row.addWidget(folder_lbl)

        self._photos_folder_input = QLineEdit()
        self._photos_folder_input.setPlaceholderText(
            "Paste Google Drive folder URL or folder ID")
        # Show current folder ID or URL for easy identification
        current_folder_id = get_setting('drive_photos_folder_id') or ''
        if current_folder_id:
            self._photos_folder_input.setText(
                f"https://drive.google.com/drive/folders/{current_folder_id}")
        self._photos_folder_input.setFixedHeight(_FORM_ROW_HEIGHT)
        self._photos_folder_input.setMinimumWidth(300)
        self._photos_folder_input.setStyleSheet(_FORM_INPUT_STYLE)
        folder_row.addWidget(self._photos_folder_input)

        self._view_photos_btn = QPushButton("Open Folder")
        self._view_photos_btn.setObjectName("secondary_btn")
        self._view_photos_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        self._view_photos_btn.setStyleSheet(_FORM_BTN_STYLE)
        self._view_photos_btn.clicked.connect(self._open_photos_folder)
        folder_row.addWidget(self._view_photos_btn)

        folder_row.addStretch()
        sync_fl.addLayout(folder_row)

        folder_hint = QLabel(
            "Use a <b>Shared Drive</b> (not a regular folder). "
            "Paste the folder URL here.<br>"
            "Example: <b>https://drive.google.com/drive/folders/abc123...</b>"
        )
        folder_hint.setWordWrap(True)
        folder_hint.setStyleSheet(
            f"font-size: 11px; color: {SUBTITLE_GRAY}; "
            "background: transparent; padding: 0 0 0 4px;"
        )
        sync_fl.addWidget(folder_hint)

        # ─ Save Sync Settings button ─
        save_row = QHBoxLayout()
        sync_save_btn = QPushButton("Save Sync Settings")
        sync_save_btn.setObjectName("primary_btn")
        sync_save_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        sync_save_btn.setStyleSheet(_FORM_BTN_STYLE)
        sync_save_btn.clicked.connect(self._save_sync_settings)
        save_row.addWidget(sync_save_btn)

        self._sync_save_status = QLabel("")
        self._sync_save_status.setStyleSheet(
            f"color: {ACCENT_GREEN}; font-weight: bold; "
            "background: transparent;"
        )
        self._sync_save_status.setVisible(False)
        save_row.addWidget(self._sync_save_status)

        save_row.addStretch()
        sync_fl.addLayout(save_row)

        layout.addWidget(sync_frame)

        # Update credentials status on load
        self._refresh_sync_creds_status()

        layout.addStretch()
        scroll.setWidget(inner)
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(scroll)
        return tab

    def _save_preferences(self):
        from fam.utils.app_settings import set_large_receipt_threshold
        value = self._threshold_spin.value()
        set_large_receipt_threshold(value)
        self._pref_status.setText(f"Saved — warnings will appear above ${value:.2f}")
        self._pref_status.setVisible(True)
        logger.info("Large receipt threshold set to %.2f", value)

    # ── Sync settings handlers ─────────────────────────────────

    def _refresh_sync_creds_status(self):
        """Update the credentials status label."""
        from fam.sync.gsheets import _get_credentials_path
        path = _get_credentials_path()
        if os.path.isfile(path):
            self._sync_creds_status.setText("google_credentials.json loaded")
            self._sync_creds_status.setStyleSheet(
                f"font-size: 13px; color: {ACCENT_GREEN}; font-weight: bold; "
                "background: transparent; padding: 0 8px;"
            )
        else:
            self._sync_creds_status.setText("Not configured")
            self._sync_creds_status.setStyleSheet(
                f"font-size: 13px; color: {SUBTITLE_GRAY}; "
                "background: transparent; padding: 0 8px;"
            )

    def _browse_credentials(self):
        """Open a file dialog to select a Google credentials JSON file."""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Select Google Service Account Credentials",
            "", "JSON Files (*.json)")
        if not filepath:
            return

        from fam.sync.gsheets import validate_credentials_file, _get_credentials_path
        valid, msg = validate_credentials_file(filepath)
        if not valid:
            QMessageBox.warning(self, "Invalid Credentials", msg)
            return

        # Copy to data directory
        import shutil
        dest = _get_credentials_path()
        shutil.copy2(filepath, dest)

        from fam.utils.app_settings import set_setting
        set_setting('sync_credentials_loaded', '1')
        self._refresh_sync_creds_status()
        logger.info("Google credentials loaded from %s", filepath)

    def _remove_credentials(self):
        """Remove the stored credentials file."""
        from fam.sync.gsheets import _get_credentials_path
        path = _get_credentials_path()
        if os.path.isfile(path):
            os.remove(path)
        from fam.utils.app_settings import set_setting
        set_setting('sync_credentials_loaded', '0')
        self._refresh_sync_creds_status()
        self._sync_conn_status.setVisible(False)
        logger.info("Google credentials removed")

    def _test_sync_connection(self):
        """Test both Google Sheets and Google Drive connections."""
        # Save spreadsheet ID first (extract from URL if needed)
        from fam.utils.app_settings import set_sync_spreadsheet_id
        raw_sheet = self._sheet_id_input.text().strip()
        if raw_sheet:
            sheet_id = self._extract_spreadsheet_id(raw_sheet)
            if sheet_id:
                set_sync_spreadsheet_id(sheet_id)

        ok_style = (f"color: {ACCENT_GREEN}; font-weight: bold; "
                     "background: transparent; padding: 0 8px;")
        fail_style = (f"color: {ERROR_COLOR}; font-weight: bold; "
                       "background: transparent; padding: 0 8px;")

        # ── Test Google Sheets ──
        try:
            from fam.sync.gsheets import GoogleSheetsBackend
            backend = GoogleSheetsBackend()
            result = backend.validate_connection()
            if result.success:
                self._sync_conn_status.setText("\u2705 Sheets: Connected")
                self._sync_conn_status.setStyleSheet(ok_style)
            else:
                self._sync_conn_status.setText(f"\u274c Sheets: {result.error}")
                self._sync_conn_status.setStyleSheet(fail_style)
        except ImportError:
            self._sync_conn_status.setText("\u274c Sheets: gspread not installed")
            self._sync_conn_status.setStyleSheet(fail_style)
        except Exception as e:
            self._sync_conn_status.setText(f"\u274c Sheets: {e}")
            self._sync_conn_status.setStyleSheet(fail_style)
        self._sync_conn_status.setVisible(True)

        # ── Test Google Drive ──
        try:
            from fam.sync.drive import validate_drive_connection
            drive_ok, drive_msg = validate_drive_connection()
            if drive_ok:
                self._drive_conn_status.setText(f"\u2705 {drive_msg}")
                self._drive_conn_status.setStyleSheet(ok_style)
            else:
                self._drive_conn_status.setText(f"\u274c Drive: {drive_msg}")
                self._drive_conn_status.setStyleSheet(fail_style)
        except Exception as e:
            self._drive_conn_status.setText(f"\u274c Drive: {e}")
            self._drive_conn_status.setStyleSheet(fail_style)
        self._drive_conn_status.setVisible(True)

    def _save_sync_settings(self):
        """Persist sync configuration to app_settings."""
        from fam.utils.app_settings import set_setting, set_sync_spreadsheet_id, get_setting

        raw_sheet = self._sheet_id_input.text().strip()
        if raw_sheet:
            sheet_id = self._extract_spreadsheet_id(raw_sheet)
            if sheet_id:
                set_sync_spreadsheet_id(sheet_id)
                self._sheet_id_input.setText(
                    f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit")
            else:
                logger.warning("Could not extract spreadsheet ID from: %s",
                               raw_sheet)
        else:
            set_sync_spreadsheet_id('')

        set_setting('sync_on_close',
                     '1' if self._sync_on_close_cb.isChecked() else '0')
        set_setting('sync_periodic',
                     '1' if self._sync_periodic_cb.isChecked() else '0')

        # Photos folder — extract folder ID from URL or raw ID
        raw_folder = self._photos_folder_input.text().strip()
        if raw_folder:
            folder_id = self._extract_drive_folder_id(raw_folder)
            if folder_id:
                old_id = get_setting('drive_photos_folder_id') or ''
                if folder_id != old_id:
                    set_setting('drive_photos_folder_id', folder_id)
                    logger.info("Photos folder ID updated: %s", folder_id)
                # Normalize the display to full URL
                self._photos_folder_input.setText(
                    f"https://drive.google.com/drive/folders/{folder_id}")
            else:
                logger.warning("Could not extract folder ID from: %s",
                               raw_folder)
        else:
            # Cleared — remove folder ID
            set_setting('drive_photos_folder_id', '')
            logger.info("Photos folder ID cleared")

        self._sync_save_status.setText("Sync settings saved")
        self._sync_save_status.setVisible(True)
        logger.info("Sync settings saved (on_close=%s, periodic=%s)",
                    self._sync_on_close_cb.isChecked(),
                    self._sync_periodic_cb.isChecked())

        # Notify main window so the header indicator refreshes immediately
        main = self.window()
        if hasattr(main, '_update_sync_visibility'):
            main._update_sync_visibility()
        # Start/stop the periodic sync timer based on the new setting
        if hasattr(main, '_update_sync_timer'):
            main._update_sync_timer()

    @staticmethod
    def _extract_drive_folder_id(raw: str) -> str:
        """Extract a Google Drive folder ID from a URL or raw ID string.

        Accepts:
          - https://drive.google.com/drive/folders/FOLDER_ID
          - https://drive.google.com/drive/folders/FOLDER_ID?usp=sharing
          - FOLDER_ID  (raw alphanumeric + hyphens + underscores)

        Returns the folder ID or empty string if not parseable.
        """
        import re
        raw = raw.strip()
        # Try to extract from URL pattern
        m = re.search(r'/folders/([A-Za-z0-9_-]+)', raw)
        if m:
            return m.group(1)
        # Accept raw folder ID (alphanumeric, hyphens, underscores, 10+ chars)
        if re.fullmatch(r'[A-Za-z0-9_-]{10,}', raw):
            return raw
        return ''

    @staticmethod
    def _extract_spreadsheet_id(raw: str) -> str:
        """Extract a Google Sheets spreadsheet ID from a URL or raw ID string.

        Accepts:
          - https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
          - https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit#gid=0
          - SPREADSHEET_ID  (raw alphanumeric + hyphens + underscores)

        Returns the spreadsheet ID or empty string if not parseable.
        """
        import re
        raw = raw.strip()
        m = re.search(r'/spreadsheets/d/([A-Za-z0-9_-]+)', raw)
        if m:
            return m.group(1)
        if re.fullmatch(r'[A-Za-z0-9_-]{10,}', raw):
            return raw
        return ''

    def _open_sheet(self):
        """Open the Google Sheet in the browser."""
        from fam.utils.app_settings import get_sync_spreadsheet_id
        sheet_id = get_sync_spreadsheet_id()
        if sheet_id:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
            QDesktopServices.openUrl(QUrl(url))
        else:
            QMessageBox.information(
                self, "Google Sheet",
                "No spreadsheet configured. Create a Google Sheet, "
                "share it with your service account, then paste the "
                "sheet URL above and save."
            )

    def _open_photos_folder(self):
        """Open the Google Drive photos folder in the browser."""
        from fam.utils.app_settings import get_setting
        folder_id = get_setting('drive_photos_folder_id')
        if folder_id:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            url = f"https://drive.google.com/drive/folders/{folder_id}"
            QDesktopServices.openUrl(QUrl(url))
        else:
            QMessageBox.information(
                self, "Photos Folder",
                "No folder configured. Create a Shared Drive in Google Drive, "
                "add your service account as Content Manager, create a folder "
                "inside it, then paste the folder URL above and save."
            )

    # ── Updates Tab ─────────────────────────────────────────────

    def _build_updates_tab(self):
        import sys
        from fam import __version__
        from fam.utils.app_settings import (
            get_update_repo_url, get_setting, get_last_update_check,
        )

        tab = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(16)

        desc = QLabel(
            "Check for new versions of FAM Market Manager from GitHub "
            "Releases. Your data is stored separately and is never "
            "affected by updates."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"font-size: 13px; color: {TEXT_COLOR}; padding: 0 0 4px 0; "
            "background: transparent;"
        )
        layout.addWidget(desc)

        # ─ Repository frame ─
        repo_frame = QFrame()
        repo_frame.setStyleSheet(_COMPACT_FRAME)
        repo_fl = QVBoxLayout(repo_frame)
        repo_fl.setSpacing(8)

        repo_row = QHBoxLayout()
        repo_lbl = make_field_label("Repository URL")
        repo_lbl.setFixedHeight(_FORM_ROW_HEIGHT)
        repo_row.addWidget(repo_lbl)

        from fam.utils.app_settings import DEFAULT_REPO_URL
        self._update_repo_input = QLineEdit()
        self._update_repo_input.setPlaceholderText(DEFAULT_REPO_URL)
        saved_url = get_update_repo_url() or ''
        self._update_repo_input.setText(saved_url if saved_url else DEFAULT_REPO_URL)
        self._update_repo_input.setFixedHeight(_FORM_ROW_HEIGHT)
        self._update_repo_input.setMinimumWidth(350)
        self._update_repo_input.setStyleSheet(_FORM_INPUT_STYLE)
        repo_row.addWidget(self._update_repo_input)

        validate_btn = QPushButton("Validate URL")
        validate_btn.setObjectName("secondary_btn")
        validate_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        validate_btn.setStyleSheet(_FORM_BTN_STYLE)
        validate_btn.clicked.connect(self._validate_update_url)
        repo_row.addWidget(validate_btn)

        repo_row.addStretch()
        repo_fl.addLayout(repo_row)

        self._update_url_status = QLabel("")
        self._update_url_status.setStyleSheet(
            f"font-size: 12px; background: transparent; padding: 0 0 0 4px;")
        self._update_url_status.setVisible(False)
        repo_fl.addWidget(self._update_url_status)

        repo_hint = QLabel(
            "Enter a GitHub repository URL, e.g. "
            "https://github.com/owner/repo"
        )
        repo_hint.setStyleSheet(
            f"font-size: 11px; color: {SUBTITLE_GRAY}; "
            "background: transparent; padding: 0 0 0 4px;"
        )
        repo_fl.addWidget(repo_hint)

        layout.addWidget(repo_frame)

        # ─ Version info frame ─
        ver_frame = QFrame()
        ver_frame.setStyleSheet(_COMPACT_FRAME)
        ver_fl = QVBoxLayout(ver_frame)
        ver_fl.setSpacing(8)

        cur_row = QHBoxLayout()
        cur_lbl = make_field_label("Current Version")
        cur_lbl.setFixedHeight(_FORM_ROW_HEIGHT)
        cur_row.addWidget(cur_lbl)
        self._update_current_lbl = QLabel(f"v{__version__}")
        self._update_current_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {TEXT_COLOR}; "
            "background: transparent; padding: 0 8px;"
        )
        self._update_current_lbl.setFixedHeight(_FORM_ROW_HEIGHT)
        cur_row.addWidget(self._update_current_lbl)
        cur_row.addStretch()
        ver_fl.addLayout(cur_row)

        latest_row = QHBoxLayout()
        latest_lbl = make_field_label("Latest Version")
        latest_lbl.setFixedHeight(_FORM_ROW_HEIGHT)
        latest_row.addWidget(latest_lbl)
        cached_ver = get_setting('update_last_version')
        self._update_latest_lbl = QLabel(
            f"v{cached_ver}" if cached_ver else "Unknown")
        self._update_latest_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {SUBTITLE_GRAY}; "
            "background: transparent; padding: 0 8px;"
        )
        self._update_latest_lbl.setFixedHeight(_FORM_ROW_HEIGHT)
        latest_row.addWidget(self._update_latest_lbl)
        latest_row.addStretch()
        ver_fl.addLayout(latest_row)

        check_row = QHBoxLayout()
        check_lbl = make_field_label("Last Checked")
        check_lbl.setFixedHeight(_FORM_ROW_HEIGHT)
        check_row.addWidget(check_lbl)
        last_check = get_last_update_check()
        check_text = "Never"
        if last_check:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(last_check)
                check_text = dt.strftime("%b %d, %Y at %I:%M %p")
            except (ValueError, TypeError):
                check_text = last_check
        self._update_last_check_lbl = QLabel(check_text)
        self._update_last_check_lbl.setStyleSheet(
            f"font-size: 13px; color: {SUBTITLE_GRAY}; "
            "background: transparent; padding: 0 8px;"
        )
        self._update_last_check_lbl.setFixedHeight(_FORM_ROW_HEIGHT)
        check_row.addWidget(self._update_last_check_lbl)
        check_row.addStretch()
        ver_fl.addLayout(check_row)

        layout.addWidget(ver_frame)

        # ─ Actions frame ─
        act_frame = QFrame()
        act_frame.setStyleSheet(_COMPACT_FRAME)
        act_fl = QVBoxLayout(act_frame)
        act_fl.setSpacing(8)

        btn_row = QHBoxLayout()
        self._update_check_btn = QPushButton("Check for Updates")
        self._update_check_btn.setObjectName("secondary_btn")
        self._update_check_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        self._update_check_btn.setStyleSheet(_FORM_BTN_STYLE)
        self._update_check_btn.clicked.connect(self._check_for_updates)
        btn_row.addWidget(self._update_check_btn)

        self._update_install_btn = QPushButton("Download && Install")
        self._update_install_btn.setObjectName("primary_btn")
        self._update_install_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        self._update_install_btn.setStyleSheet(_FORM_BTN_STYLE)
        self._update_install_btn.setEnabled(False)
        self._update_install_btn.clicked.connect(self._download_and_install)
        btn_row.addWidget(self._update_install_btn)

        btn_row.addStretch()
        act_fl.addLayout(btn_row)

        self._update_progress = QProgressBar()
        self._update_progress.setFixedHeight(18)
        self._update_progress.setVisible(False)
        self._update_progress.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid #E2E2E2;
                border-radius: 4px;
                background-color: #F5F5F5;
                text-align: center;
                font-size: 11px;
            }}
            QProgressBar::chunk {{
                background-color: {PRIMARY_GREEN};
                border-radius: 3px;
            }}
        """)
        act_fl.addWidget(self._update_progress)

        self._update_status_lbl = QLabel("")
        self._update_status_lbl.setWordWrap(True)
        self._update_status_lbl.setStyleSheet(
            f"font-size: 13px; background: transparent; padding: 2px 0;"
        )
        self._update_status_lbl.setVisible(False)
        act_fl.addWidget(self._update_status_lbl)

        layout.addWidget(act_frame)

        # ─ Auto-check checkbox ─
        _update_cb_style = f"""
            QCheckBox {{
                font-size: 13px; padding: 4px; background-color: {WHITE};
            }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                background-color: {WHITE};
                border: 2px solid #AAAAAA;
                border-radius: 3px;
            }}
            QCheckBox::indicator:checked {{
                background-color: {ACCENT_GREEN};
                border-color: {PRIMARY_GREEN};
            }}
        """
        self._update_auto_check_cb = QCheckBox(
            "Auto-check for updates on launch")
        self._update_auto_check_cb.setChecked(
            get_setting('update_auto_check', '1') == '1')
        self._update_auto_check_cb.setStyleSheet(_update_cb_style)
        layout.addWidget(self._update_auto_check_cb)

        # ─ Save button ─
        save_row = QHBoxLayout()
        save_btn = QPushButton("Save Update Settings")
        save_btn.setObjectName("primary_btn")
        save_btn.setFixedHeight(_FORM_ROW_HEIGHT)
        save_btn.setStyleSheet(_FORM_BTN_STYLE)
        save_btn.clicked.connect(self._save_update_settings)
        save_row.addWidget(save_btn)

        self._update_save_status = QLabel("")
        self._update_save_status.setStyleSheet(
            f"color: {ACCENT_GREEN}; font-weight: bold; "
            "background: transparent;"
        )
        self._update_save_status.setVisible(False)
        save_row.addWidget(self._update_save_status)

        save_row.addStretch()
        layout.addLayout(save_row)

        # ─ Dev mode notice ─
        if not getattr(sys, 'frozen', False):
            dev_notice = QLabel(
                "Note: Download & Install is only available in the "
                "packaged version (.exe). Version checking works in "
                "development mode."
            )
            dev_notice.setWordWrap(True)
            dev_notice.setStyleSheet(
                f"font-size: 11px; color: {SUBTITLE_GRAY}; "
                "background: transparent; padding: 4px 0; "
                "font-style: italic;"
            )
            layout.addWidget(dev_notice)

        # ─ Thread tracking ─
        self._update_check_thread = None
        self._update_check_worker = None
        self._update_dl_thread = None
        self._update_dl_worker = None
        self._update_info = None  # cached result from last check

        layout.addStretch()
        scroll.setWidget(inner)
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(scroll)
        return tab

    # ── Updates handlers ─────────────────────────────────────────

    def _validate_update_url(self):
        """Validate the entered GitHub URL."""
        from fam.update.checker import parse_github_repo_url
        url = self._update_repo_input.text().strip()
        if not url:
            self._update_url_status.setText("Please enter a repository URL")
            self._update_url_status.setStyleSheet(
                f"font-size: 12px; color: {ERROR_COLOR}; "
                "background: transparent; padding: 0 0 0 4px;"
            )
            self._update_url_status.setVisible(True)
            return

        result = parse_github_repo_url(url)
        if result:
            owner, repo = result
            self._update_url_status.setText(
                f"Valid repository: {owner}/{repo}")
            self._update_url_status.setStyleSheet(
                f"font-size: 12px; color: {ACCENT_GREEN}; font-weight: bold; "
                "background: transparent; padding: 0 0 0 4px;"
            )
        else:
            self._update_url_status.setText(
                "Invalid URL — must be a GitHub repository")
            self._update_url_status.setStyleSheet(
                f"font-size: 12px; color: {ERROR_COLOR}; "
                "background: transparent; padding: 0 0 0 4px;"
            )
        self._update_url_status.setVisible(True)

    def _save_update_settings(self):
        """Persist update configuration to app_settings."""
        from fam.utils.app_settings import set_update_repo_url, set_setting

        url = self._update_repo_input.text().strip()
        if url:
            set_update_repo_url(url)
        else:
            set_setting('update_repo_url', '')

        set_setting('update_auto_check',
                     '1' if self._update_auto_check_cb.isChecked() else '0')

        self._update_save_status.setText("Update settings saved")
        self._update_save_status.setVisible(True)
        logger.info("Update settings saved (repo=%s, auto_check=%s)",
                    url, self._update_auto_check_cb.isChecked())

    def _check_for_updates(self):
        """Check GitHub for a newer release."""
        from fam.update.checker import parse_github_repo_url
        from fam import __version__

        url = self._update_repo_input.text().strip()
        if not url:
            self._update_status_lbl.setText(
                "Please enter a repository URL first.")
            self._update_status_lbl.setStyleSheet(
                f"font-size: 13px; color: {ERROR_COLOR}; "
                "background: transparent; padding: 2px 0;"
            )
            self._update_status_lbl.setVisible(True)
            return

        parsed = parse_github_repo_url(url)
        if not parsed:
            self._update_status_lbl.setText(
                "Invalid repository URL.")
            self._update_status_lbl.setStyleSheet(
                f"font-size: 13px; color: {ERROR_COLOR}; "
                "background: transparent; padding: 2px 0;"
            )
            self._update_status_lbl.setVisible(True)
            return

        owner, repo = parsed

        # Prevent overlapping checks
        if (self._update_check_thread and
                self._update_check_thread.isRunning()):
            return

        from PySide6.QtCore import QThread
        from fam.update.worker import UpdateCheckWorker

        self._update_check_btn.setEnabled(False)
        self._update_check_btn.setText("Checking...")
        self._update_status_lbl.setText("Checking for updates...")
        self._update_status_lbl.setStyleSheet(
            f"font-size: 13px; color: {TEXT_COLOR}; "
            "background: transparent; padding: 2px 0;"
        )
        self._update_status_lbl.setVisible(True)

        self._update_check_thread = QThread()
        self._update_check_worker = UpdateCheckWorker(
            owner, repo, __version__)
        self._update_check_worker.moveToThread(self._update_check_thread)
        self._update_check_thread.started.connect(
            self._update_check_worker.run)
        self._update_check_worker.finished.connect(
            self._on_update_check_finished)
        self._update_check_worker.error.connect(
            self._on_update_check_error)
        self._update_check_worker.finished.connect(
            self._update_check_thread.quit)
        self._update_check_worker.error.connect(
            self._update_check_thread.quit)

        self._update_check_thread.start()

    def _on_update_check_finished(self, result: dict):
        """Handle update check result."""
        from datetime import datetime
        from fam.utils.app_settings import set_setting, set_last_update_check

        self._update_check_btn.setEnabled(True)
        self._update_check_btn.setText("Check for Updates")

        now = datetime.now()
        set_last_update_check(now.isoformat())
        self._update_last_check_lbl.setText(
            now.strftime("%b %d, %Y at %I:%M %p"))

        if not result:
            self._update_status_lbl.setText(
                "Could not check for updates. The repository may "
                "have no releases.")
            self._update_status_lbl.setStyleSheet(
                f"font-size: 13px; color: {SUBTITLE_GRAY}; "
                "background: transparent; padding: 2px 0;"
            )
            self._update_status_lbl.setVisible(True)
            return

        version = result.get('version', '?')
        set_setting('update_last_version', version)

        self._update_latest_lbl.setText(f"v{version}")

        if result.get('update_available'):
            self._update_info = result
            self._update_latest_lbl.setStyleSheet(
                f"font-size: 14px; font-weight: bold; "
                f"color: {ACCENT_GREEN}; "
                "background: transparent; padding: 0 8px;"
            )
            self._update_status_lbl.setText(
                f"Update available: v{version}")
            self._update_status_lbl.setStyleSheet(
                f"font-size: 13px; color: {ACCENT_GREEN}; "
                "font-weight: bold; background: transparent; "
                "padding: 2px 0;"
            )
            self._update_install_btn.setEnabled(True)
        else:
            self._update_info = None
            self._update_latest_lbl.setStyleSheet(
                f"font-size: 14px; font-weight: bold; "
                f"color: {TEXT_COLOR}; "
                "background: transparent; padding: 0 8px;"
            )
            self._update_status_lbl.setText(
                f"You are up to date (v{version})")
            self._update_status_lbl.setStyleSheet(
                f"font-size: 13px; color: {TEXT_COLOR}; "
                "background: transparent; padding: 2px 0;"
            )
            self._update_install_btn.setEnabled(False)

        self._update_status_lbl.setVisible(True)

    def _on_update_check_error(self, msg: str):
        """Handle update check failure."""
        self._update_check_btn.setEnabled(True)
        self._update_check_btn.setText("Check for Updates")
        self._update_status_lbl.setText(
            f"Could not check for updates: {msg}")
        self._update_status_lbl.setStyleSheet(
            f"font-size: 13px; color: {ERROR_COLOR}; "
            "background: transparent; padding: 2px 0;"
        )
        self._update_status_lbl.setVisible(True)

    def _download_and_install(self):
        """Download the update and launch the update script."""
        import sys

        if not self._update_info:
            return

        version = self._update_info.get('version', '?')
        asset_name = self._update_info.get('asset_name', '')

        # ── Safety checks ──
        if not getattr(sys, 'frozen', False):
            QMessageBox.information(
                self, "Development Mode",
                "Download & Install is only available in the packaged "
                "version (.exe). In development mode, please update "
                "via git pull."
            )
            return

        # Check for open market day
        try:
            from fam.models.market_day import get_open_market_day
            if get_open_market_day():
                QMessageBox.warning(
                    self, "Market Day Open",
                    "A market day is currently open. Please close the "
                    "market day before updating to avoid data loss."
                )
                return
        except Exception:
            pass

        # Confirmation
        reply = QMessageBox.question(
            self, "Download & Install Update",
            f"Download and install FAM Manager v{version}?\n\n"
            f"File: {asset_name}\n"
            f"Size: {self._update_info.get('asset_size', 0) / 1024 / 1024:.1f} MB\n\n"
            "The app will close and restart after the update.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # ── Start download ──
        from fam.app import get_data_dir

        dl_dir = os.path.join(get_data_dir(), '_update_download')
        os.makedirs(dl_dir, exist_ok=True)
        dest_path = os.path.join(dl_dir, asset_name)

        # Prevent overlapping downloads
        if self._update_dl_thread and self._update_dl_thread.isRunning():
            return

        from PySide6.QtCore import QThread
        from fam.update.worker import UpdateDownloadWorker

        self._update_check_btn.setEnabled(False)
        self._update_install_btn.setEnabled(False)
        self._update_progress.setValue(0)
        self._update_progress.setVisible(True)
        self._update_status_lbl.setText("Downloading update...")
        self._update_status_lbl.setStyleSheet(
            f"font-size: 13px; color: {TEXT_COLOR}; "
            "background: transparent; padding: 2px 0;"
        )
        self._update_status_lbl.setVisible(True)

        self._update_dl_thread = QThread()
        self._update_dl_worker = UpdateDownloadWorker(
            self._update_info['asset_url'],
            self._update_info['asset_size'],
            dest_path,
        )
        self._update_dl_worker.moveToThread(self._update_dl_thread)
        self._update_dl_thread.started.connect(self._update_dl_worker.run)
        self._update_dl_worker.progress.connect(self._on_download_progress)
        self._update_dl_worker.finished.connect(self._on_download_finished)
        self._update_dl_worker.error.connect(self._on_download_error)
        self._update_dl_worker.finished.connect(
            self._update_dl_thread.quit)
        self._update_dl_worker.error.connect(self._update_dl_thread.quit)

        self._update_dl_thread.start()

    def _on_download_progress(self, downloaded: int, total: int):
        """Update the progress bar."""
        if total > 0:
            pct = int(downloaded * 100 / total)
            self._update_progress.setValue(pct)
            mb_dl = downloaded / 1024 / 1024
            mb_total = total / 1024 / 1024
            self._update_status_lbl.setText(
                f"Downloading... {mb_dl:.1f} / {mb_total:.1f} MB ({pct}%)")

    def _on_download_finished(self, zip_path: str):
        """Generate update script and restart."""
        import subprocess
        import sys
        from fam.app import get_app_dir
        from fam.update.checker import generate_update_script

        self._update_progress.setValue(100)
        self._update_status_lbl.setText(
            "Download complete. Applying update...")
        self._update_status_lbl.setStyleSheet(
            f"font-size: 13px; color: {ACCENT_GREEN}; font-weight: bold; "
            "background: transparent; padding: 2px 0;"
        )

        try:
            app_dir = get_app_dir()
            script_path = generate_update_script(app_dir, zip_path)

            # Launch the batch script and exit
            subprocess.Popen(
                ['cmd', '/c', script_path],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            from PySide6.QtWidgets import QApplication
            QApplication.instance().quit()

        except Exception as e:
            logger.exception("Failed to launch update script")
            self._update_status_lbl.setText(
                f"Update failed: {e}")
            self._update_status_lbl.setStyleSheet(
                f"font-size: 13px; color: {ERROR_COLOR}; "
                "background: transparent; padding: 2px 0;"
            )
            self._update_check_btn.setEnabled(True)
            self._update_install_btn.setEnabled(True)
            self._update_progress.setVisible(False)

    def _on_download_error(self, msg: str):
        """Handle download failure."""
        self._update_status_lbl.setText(f"Download failed: {msg}")
        self._update_status_lbl.setStyleSheet(
            f"font-size: 13px; color: {ERROR_COLOR}; "
            "background: transparent; padding: 2px 0;"
        )
        self._update_status_lbl.setVisible(True)
        self._update_progress.setVisible(False)
        self._update_check_btn.setEnabled(True)
        self._update_install_btn.setEnabled(True)

    # ── Reset Tab ────────────────────────────────────────────

    def _build_reset_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(20)

        info = QLabel(
            "Use this to clear all data and start fresh.\n\n"
            "This will permanently delete all market days, transactions, "
            "FMNP entries, audit log entries, and all configured markets, "
            "vendors, and payment methods.\n\n"
            "After reset, the app will be empty — ready for a fresh "
            "configuration via the Settings tabs or by importing a .fam "
            "settings file."
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

        reset_btn = QPushButton("Reset All Data")
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
        # Update the market code display in Preferences tab
        from fam.utils.app_settings import get_market_code
        self._market_code_display.setText(get_market_code() or "Not Set")

    def _load_markets(self):
        conn = get_connection()
        rows = conn.execute("SELECT * FROM markets ORDER BY name").fetchall()
        self.markets_table.setSortingEnabled(False)
        self.markets_table.setRowCount(0)
        self.markets_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.markets_table.setItem(i, 0, make_item(str(r['id']), r['id']))
            self.markets_table.setItem(i, 1, make_item(r['name']))
            self.markets_table.setItem(i, 2, make_item(r['address'] or ''))

            # Match Limit column — green when on, red when off
            limit_active = r['match_limit_active']
            limit_val = r['daily_match_limit'] or 100.00
            if limit_active:
                limit_item = make_item(f"${limit_val:.2f}", limit_val)
                limit_item.setForeground(QBrush(QColor(ACCENT_GREEN)))
            else:
                limit_item = make_item("Off")
                limit_item.setForeground(QBrush(QColor(ERROR_COLOR)))
            self.markets_table.setItem(i, 3, limit_item)

            # Active column — green Yes, red No
            active_item = make_item("Yes" if r['is_active'] else "No")
            active_item.setForeground(QBrush(QColor(ACCENT_GREEN if r['is_active'] else ERROR_COLOR)))
            self.markets_table.setItem(i, 4, active_item)

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

            pay_btn = make_action_btn("Payments", 65)
            pay_btn.setToolTip("Assign Payment Methods to this Market")
            pay_btn.clicked.connect(lambda checked, mid=mid: self._assign_payment_methods(mid))
            al.addWidget(pay_btn)

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
        self.vendors_table.setRowCount(0)
        self.vendors_table.setRowCount(len(vendors))
        for i, v in enumerate(vendors):
            self.vendors_table.setItem(i, 0, make_item(str(v['id']), v['id']))
            self.vendors_table.setItem(i, 1, make_item(v['name']))
            self.vendors_table.setItem(i, 2, make_item(v.get('contact_info') or ''))

            active_item = make_item("Yes" if v['is_active'] else "No")
            active_item.setForeground(QBrush(QColor(ACCENT_GREEN if v['is_active'] else ERROR_COLOR)))
            self.vendors_table.setItem(i, 3, active_item)

            action_widget = QWidget()
            al = QHBoxLayout(action_widget)
            al.setContentsMargins(2, 2, 2, 2)
            al.setSpacing(3)
            vid = v['id']
            is_active = v['is_active']

            edit_btn = make_action_btn("Edit", 45)
            edit_btn.clicked.connect(lambda checked, vid=vid: self._edit_vendor(vid))
            al.addWidget(edit_btn)

            markets_btn = make_action_btn("Markets", 60)
            markets_btn.clicked.connect(lambda checked, vid=vid: self._assign_markets_to_vendor(vid))
            al.addWidget(markets_btn)

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
        self.pm_table.setRowCount(0)
        self.pm_table.setRowCount(len(methods))
        for i, m in enumerate(methods):
            self.pm_table.setItem(i, 0, make_item(str(m['id']), m['id']))
            self.pm_table.setItem(i, 1, make_item(m['name']))
            self.pm_table.setItem(i, 2, make_item(f"{m['match_percent']}%", m['match_percent']))

            denom = m.get('denomination')
            denom_text = f"${denom:.2f}" if denom else "Any"
            self.pm_table.setItem(i, 3, make_item(denom_text, denom or 0))

            active_item = make_item("Yes" if m['is_active'] else "No")
            active_item.setForeground(QBrush(QColor(ACCENT_GREEN if m['is_active'] else ERROR_COLOR)))
            self.pm_table.setItem(i, 4, active_item)

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

            is_fmnp = (m['name'] == 'FMNP')
            toggle_btn = make_action_btn("Deactivate" if is_active else "Activate", 70)
            if is_fmnp:
                toggle_btn.setEnabled(False)
                toggle_btn.setToolTip("FMNP is a system payment method and cannot be deactivated")
            else:
                toggle_btn.clicked.connect(
                    lambda checked, mid=mid, active=is_active: self._toggle_pm(mid, active)
                )
            al.addWidget(toggle_btn)

            self.pm_table.setCellWidget(i, 5, action_widget)
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

    def _assign_markets_to_vendor(self, vendor_id):
        """Open market assignment dialog for a vendor."""
        vendor = get_vendor_by_id(vendor_id)
        if not vendor:
            return

        dialog = AssignMarketsDialog(vendor, self)
        if dialog.exec() == QDialog.Accepted:
            new_ids = dialog.get_checked_market_ids()
            old_ids = get_vendor_market_ids(vendor_id)

            # Add newly checked
            for mid in new_ids - old_ids:
                assign_vendor_to_market(mid, vendor_id)

            # Remove newly unchecked
            for mid in old_ids - new_ids:
                unassign_vendor_from_market(mid, vendor_id)

    def _assign_payment_methods(self, market_id):
        """Open payment method assignment dialog for a market."""
        conn = get_connection()
        row = conn.execute("SELECT * FROM markets WHERE id=?", (market_id,)).fetchone()
        if not row:
            return
        market = dict(row)

        dialog = AssignPaymentMethodsDialog(market, self)
        if dialog.exec() == QDialog.Accepted:
            new_ids = dialog.get_checked_payment_method_ids()
            old_ids = get_market_payment_method_ids(market_id)

            # Add newly checked
            for pid in new_ids - old_ids:
                assign_payment_method_to_market(market_id, pid)

            # Remove newly unchecked
            for pid in old_ids - new_ids:
                unassign_payment_method_from_market(market_id, pid)

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

    def _toggle_add_denom(self, checked):
        self.pm_denom_spin.setEnabled(checked)

    def _add_payment_method(self):
        name = self.pm_name_input.text().strip()
        match_pct = self.pm_match_spin.value()
        denom_val = self.pm_denom_spin.value() if self.pm_denom_check.isChecked() else None
        if not name:
            QMessageBox.warning(self, "Error", "Payment method name is required.")
            return
        try:
            methods = get_all_payment_methods()
            max_sort = max((m['sort_order'] for m in methods), default=0)
            create_payment_method(name, match_pct, max_sort + 1, denomination=denom_val)
            self.pm_name_input.clear()
            self.pm_match_spin.setValue(0)
            self.pm_denom_check.setChecked(False)
            self.pm_denom_spin.setValue(25.0)
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
        # FMNP is a system method — protect its name from being changed
        if method['name'] == 'FMNP':
            dialog.name_input.setEnabled(False)
            dialog.name_input.setToolTip("FMNP is a system payment method and cannot be renamed")
            dialog.show_photo_required()
        if dialog.exec() == QDialog.Accepted:
            new_name = dialog.name_input.text().strip()
            new_match_pct = dialog.match_spin.value()
            new_denom_val = dialog.get_denomination()
            if new_denom_val is None:
                new_denom_val = 0  # 0 tells update_payment_method to clear
            if not new_name:
                QMessageBox.warning(self, "Error", "Payment method name is required.")
                return
            photo_req = dialog.get_photo_required()
            update_payment_method(pm_id, name=new_name, match_percent=new_match_pct,
                                  denomination=new_denom_val,
                                  photo_required=photo_req)
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
        from fam.models.payment_method import get_payment_method_by_id
        method = get_payment_method_by_id(mid)
        if method and method['name'] == 'FMNP':
            QMessageBox.warning(
                self, "Protected Method",
                "FMNP is a system payment method and cannot be deactivated."
            )
            return
        update_payment_method(mid, is_active=not current_active)
        self._load_payment_methods()

    # ── Import / Export ────────────────────────────────────────

    def _export_settings(self):
        """Export all settings to a .fam file."""
        from fam.settings_io import export_settings

        filepath, _ = QFileDialog.getSaveFileName(
            self, "Export Settings",
            os.path.expanduser("~/FAM_Settings.fam"),
            "FAM Settings Files (*.fam);;All Files (*)"
        )
        if not filepath:
            return

        try:
            export_settings(filepath)
            QMessageBox.information(
                self, "Export Complete",
                f"Settings exported successfully to:\n\n{filepath}\n\n"
                "You can open this file with any text editor to review or "
                "edit the settings before importing on another machine."
            )
        except Exception as e:
            logger.exception("Failed to export settings")
            QMessageBox.warning(self, "Export Error", f"Could not export settings:\n\n{e}")

    def _import_settings(self):
        """Import settings from a .fam file with validation and preview."""
        from fam.settings_io import parse_settings_file, apply_import

        filepath, _ = QFileDialog.getOpenFileName(
            self, "Import Settings",
            os.path.expanduser("~"),
            "FAM Settings Files (*.fam);;All Files (*)"
        )
        if not filepath:
            return

        # Parse and validate the file
        result = parse_settings_file(filepath)

        # Check for fatal errors (couldn't parse at all)
        if result.errors and not result.markets and not result.vendors and not result.payment_methods:
            QMessageBox.warning(
                self, "Import Error",
                "Could not parse the settings file:\n\n" + "\n".join(result.errors[:5])
            )
            return

        # Show preview dialog
        preview = ImportPreviewDialog(result, self)
        if preview.exec() != QDialog.Accepted:
            return

        # Apply the import
        try:
            counts = apply_import(result)
            self.refresh()

            # Build summary message
            parts = []
            if counts['markets_added']:
                parts.append(f"{counts['markets_added']} market(s)")
            if counts['vendors_added']:
                parts.append(f"{counts['vendors_added']} vendor(s)")
            if counts['payment_methods_added']:
                parts.append(f"{counts['payment_methods_added']} payment method(s)")

            assignments = counts['vendor_assignments_added'] + counts['pm_assignments_added']
            if assignments:
                parts.append(f"{assignments} assignment(s)")

            if parts:
                msg = "Successfully imported:\n\n  • " + "\n  • ".join(parts)
            else:
                msg = "No new items were imported."

            QMessageBox.information(self, "Import Complete", msg)
        except Exception as e:
            logger.exception("Failed to apply import")
            QMessageBox.warning(self, "Import Error", f"Could not apply import:\n\n{e}")

    # ── Reset to Default ─────────────────────────────────────

    def _reset_to_default(self):
        result = QMessageBox.warning(
            self, "Confirm Reset",
            "Are you sure you want to reset ALL data?\n\n"
            "This will permanently delete:\n"
            "  • All market days\n"
            "  • All transactions and payment records\n"
            "  • All FMNP entries\n"
            "  • All audit log entries\n"
            "  • All markets, vendors, and payment methods\n\n"
            "The app will be completely empty after reset.\n"
            "You can re-configure via Settings or import a .fam file.\n\n"
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
            # Delete all data in dependency order (junction/child tables first)
            conn.execute("DELETE FROM audit_log")
            conn.execute("DELETE FROM payment_line_items")
            conn.execute("DELETE FROM fmnp_entries")
            conn.execute("DELETE FROM transactions")
            conn.execute("DELETE FROM customer_orders")
            conn.execute("DELETE FROM market_days")
            conn.execute("DELETE FROM market_payment_methods")
            conn.execute("DELETE FROM market_vendors")
            conn.execute("DELETE FROM payment_methods")
            conn.execute("DELETE FROM vendors")
            conn.execute("DELETE FROM markets")
            conn.commit()

            self.refresh()
            QMessageBox.information(
                self, "Reset Complete",
                "All data has been cleared.\n\n"
                "Use the Settings tabs to add new markets, vendors, and "
                "payment methods, or import a .fam settings file."
            )
        except Exception as e:
            QMessageBox.critical(self, "Reset Error", f"Failed to reset: {str(e)}")
