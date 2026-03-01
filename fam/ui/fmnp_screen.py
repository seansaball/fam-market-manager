"""Screen D: FMNP Entry."""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QTextEdit
)
from PySide6.QtCore import Qt

from fam.models.market_day import get_all_market_days
from fam.models.vendor import get_all_vendors, get_vendors_for_market
from fam.models.fmnp import (
    get_fmnp_entries, create_fmnp_entry, update_fmnp_entry, delete_fmnp_entry,
    get_fmnp_entry_by_id
)
from fam.models.audit import log_action
from fam.ui.styles import WHITE, LIGHT_GRAY, ERROR_COLOR, PRIMARY_GREEN, ERROR_BG, CARD_FRAME_STYLE
from fam.ui.helpers import (
    make_field_label, make_item, make_action_btn, configure_table,
    NoScrollDoubleSpinBox, NoScrollSpinBox, NoScrollComboBox
)

logger = logging.getLogger('fam.ui.fmnp_screen')


class FMNPScreen(QWidget):
    """FMNP Entry screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editing_id = None
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("FMNP Entry")
        title.setObjectName("screen_title")
        layout.addWidget(title)

        subtitle = QLabel("Log FMNP checks collected by vendors for FAM match reimbursement — tracks external matching handled outside the app")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        # Form
        form_frame = QFrame()
        form_frame.setStyleSheet(CARD_FRAME_STYLE)
        form_layout = QVBoxLayout(form_frame)
        form_layout.setSpacing(10)

        row1 = QHBoxLayout()
        row1.addWidget(make_field_label("Market"))
        self.md_combo = NoScrollComboBox()
        self.md_combo.setMinimumWidth(300)
        self.md_combo.currentIndexChanged.connect(self._on_market_day_changed)
        row1.addWidget(self.md_combo)
        row1.addStretch()
        form_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(make_field_label("Vendor"))
        self.vendor_combo = NoScrollComboBox()
        self.vendor_combo.setMinimumWidth(180)
        self.vendor_combo.setMaximumWidth(280)
        row2.addWidget(self.vendor_combo)

        row2.addWidget(make_field_label("Amount ($)"))
        self.amount_spin = NoScrollDoubleSpinBox()
        self.amount_spin.setRange(0, 99999.99)
        self.amount_spin.setDecimals(2)
        self.amount_spin.setPrefix("$")
        self.amount_spin.setSingleStep(1.00)
        row2.addWidget(self.amount_spin)

        row2.addWidget(make_field_label("Check Count"))
        self.check_count_spin = NoScrollSpinBox()
        self.check_count_spin.setRange(0, 9999)
        self.check_count_spin.setSpecialValueText("N/A")
        row2.addWidget(self.check_count_spin)
        row2.addStretch()
        form_layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(make_field_label("Entered By"))
        self.entered_by_input = QLineEdit()
        self.entered_by_input.setPlaceholderText("Your name")
        self.entered_by_input.setMaximumWidth(200)
        row3.addWidget(self.entered_by_input)

        row3.addWidget(make_field_label("Notes"))
        self.notes_input = QLineEdit()
        self.notes_input.setPlaceholderText("Optional notes")
        self.notes_input.setMaximumWidth(500)
        row3.addWidget(self.notes_input)
        form_layout.addLayout(row3)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"""
            color: {ERROR_COLOR}; font-weight: bold;
            background-color: {ERROR_BG};
            border: 1px solid {ERROR_COLOR};
            border-radius: 6px;
            padding: 6px 10px;
        """)
        self.error_label.setVisible(False)
        form_layout.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("Add FMNP Entry")
        self.save_btn.setObjectName("primary_btn")
        self.save_btn.clicked.connect(self._save_entry)
        btn_row.addWidget(self.save_btn)

        self.cancel_edit_btn = QPushButton("Cancel Edit")
        self.cancel_edit_btn.setObjectName("secondary_btn")
        self.cancel_edit_btn.clicked.connect(self._cancel_edit)
        self.cancel_edit_btn.setVisible(False)
        btn_row.addWidget(self.cancel_edit_btn)

        btn_row.addStretch()
        form_layout.addLayout(btn_row)

        layout.addWidget(form_frame)

        # Entries table
        layout.addWidget(QLabel("FMNP Entries for Selected Market:"))
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Vendor", "Amount", "Check Count", "Entered By", "Notes", "Actions"]
        )
        configure_table(self.table, actions_col=6, actions_width=120)
        layout.addWidget(self.table)

        layout.addStretch()

    def refresh(self):
        self._load_market_days()
        self._load_vendors()
        self._load_entries()
        self._cancel_edit()

    def _load_market_days(self):
        self._market_days_data = get_all_market_days()
        self.md_combo.blockSignals(True)
        self.md_combo.clear()
        for d in self._market_days_data:
            status = "[OPEN]" if d['status'] == 'Open' else "[Closed]"
            self.md_combo.addItem(
                f"{d['market_name']} - {d['date']} {status}",
                userData=d['id']
            )
        self.md_combo.blockSignals(False)

    def _load_vendors(self):
        """Load vendors — filtered to selected market's assignments when available."""
        self.vendor_combo.clear()
        # Determine market_id from the selected market day
        market_id = None
        md_id = self.md_combo.currentData()
        if md_id and hasattr(self, '_market_days_data'):
            for d in self._market_days_data:
                if d['id'] == md_id:
                    market_id = d.get('market_id')
                    break
        if market_id:
            vendors = get_vendors_for_market(market_id)
            if not vendors:
                # Fallback: no assignments yet → show all active vendors
                vendors = get_all_vendors(active_only=True)
        else:
            vendors = get_all_vendors(active_only=True)
        for v in vendors:
            self.vendor_combo.addItem(v['name'], userData=v['id'])

    def _on_market_day_changed(self):
        """When market day changes, reload vendors filtered by that market."""
        self._load_vendors()
        self._load_entries()

    def _load_entries(self):
        md_id = self.md_combo.currentData()
        if not md_id:
            self.table.setRowCount(0)
            return

        entries = get_fmnp_entries(md_id)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(entries))

        for i, e in enumerate(entries):
            self.table.setItem(i, 0, make_item(str(e['id']), e['id']))
            self.table.setItem(i, 1, make_item(e['vendor_name']))
            self.table.setItem(i, 2, make_item(f"${e['amount']:.2f}", e['amount']))
            self.table.setItem(i, 3, make_item(str(e.get('check_count') or ''),
                                                e.get('check_count') or 0))
            self.table.setItem(i, 4, make_item(e['entered_by']))
            self.table.setItem(i, 5, make_item(e.get('notes') or ''))

            # Action buttons
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(3)

            entry_id = e['id']
            edit_btn = make_action_btn("Edit", 45)
            edit_btn.clicked.connect(lambda checked, eid=entry_id: self._edit_entry(eid))
            action_layout.addWidget(edit_btn)

            del_btn = make_action_btn("Delete", 55, danger=True)
            del_btn.clicked.connect(lambda checked, eid=entry_id: self._delete_entry(eid))
            action_layout.addWidget(del_btn)

            self.table.setCellWidget(i, 6, action_widget)
            self.table.setRowHeight(i, 42)

        self.table.setSortingEnabled(True)

    def _save_entry(self):
        self.error_label.setVisible(False)
        md_id = self.md_combo.currentData()
        vendor_id = self.vendor_combo.currentData()
        amount = self.amount_spin.value()
        check_count = self.check_count_spin.value() if self.check_count_spin.value() > 0 else None
        entered_by = self.entered_by_input.text().strip()
        notes = self.notes_input.text().strip() or None

        if not md_id:
            self._show_error("Please select a market day.")
            return
        if not vendor_id:
            self._show_error("Please select a vendor.")
            return
        if not entered_by:
            self._show_error("Please enter your name.")
            return
        if amount <= 0:
            self._show_error("Amount must be greater than $0.00.")
            return

        try:
            if self._editing_id:
                old = get_fmnp_entry_by_id(self._editing_id)
                update_fmnp_entry(self._editing_id, amount=amount, vendor_id=vendor_id,
                                  check_count=check_count, notes=notes)
                log_action('fmnp_entries', self._editing_id, 'UPDATE', entered_by,
                            field_name='amount', old_value=old.get('amount'),
                            new_value=amount, reason_code='edit', notes='FMNP entry updated')
                self._cancel_edit()
            else:
                entry_id = create_fmnp_entry(md_id, vendor_id, amount, entered_by, check_count, notes)
                log_action('fmnp_entries', entry_id, 'INSERT', entered_by, notes='FMNP entry created')

            self.amount_spin.setValue(0)
            self.check_count_spin.setValue(0)
            self.notes_input.clear()
            self._load_entries()
        except Exception as e:
            logger.exception("Failed to save FMNP entry")
            self._show_error(f"Error saving entry: {e}")

    def _edit_entry(self, entry_id):
        entry = get_fmnp_entry_by_id(entry_id)
        if not entry:
            return
        self._editing_id = entry_id
        self.amount_spin.setValue(entry['amount'])
        self.check_count_spin.setValue(entry.get('check_count') or 0)
        self.notes_input.setText(entry.get('notes') or '')

        # Select matching vendor
        for i in range(self.vendor_combo.count()):
            if self.vendor_combo.itemData(i) == entry['vendor_id']:
                self.vendor_combo.setCurrentIndex(i)
                break

        self.save_btn.setText("Update FMNP Entry")
        self.cancel_edit_btn.setVisible(True)

    def _cancel_edit(self):
        self._editing_id = None
        self.save_btn.setText("Add FMNP Entry")
        self.cancel_edit_btn.setVisible(False)

    def _delete_entry(self, entry_id):
        result = QMessageBox.question(
            self, "Delete FMNP Entry",
            "Are you sure you want to delete this FMNP entry?",
            QMessageBox.Yes | QMessageBox.No
        )
        if result == QMessageBox.Yes:
            try:
                entered_by = self.entered_by_input.text().strip() or "System"
                log_action('fmnp_entries', entry_id, 'DELETE', entered_by,
                            notes='FMNP entry deleted')
                delete_fmnp_entry(entry_id)
                self._load_entries()
            except Exception as e:
                logger.exception("Failed to delete FMNP entry %s", entry_id)
                self._show_error(f"Error deleting entry: {e}")

    def _show_error(self, msg):
        self.error_label.setText(msg)
        self.error_label.setVisible(True)
