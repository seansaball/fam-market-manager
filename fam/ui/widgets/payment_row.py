"""Reusable payment method entry row widget."""

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QStandardItemModel, QColor, QBrush
from fam.models.payment_method import get_all_payment_methods, get_payment_methods_for_market
from fam.ui.styles import LIGHT_GRAY, WHITE, ERROR_COLOR, SUBTITLE_GRAY, HARVEST_GOLD, ACCENT_GREEN
from fam.ui.helpers import NoScrollDoubleSpinBox, NoScrollComboBox


class PaymentRow(QFrame):
    """A single payment method entry row."""

    changed = Signal()
    remove_requested = Signal(object)  # emits self

    def __init__(self, parent=None, market_id=None):
        super().__init__(parent)
        self._market_id = market_id
        self.setStyleSheet(f"""
            PaymentRow {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)

        # Payment method combo
        self.method_combo = NoScrollComboBox()
        self.method_combo.setMinimumWidth(160)
        self._load_methods()
        self.method_combo.currentIndexChanged.connect(self._on_changed)
        layout.addWidget(self.method_combo)

        # Match percent label
        self.match_label = QLabel("0%")
        self.match_label.setMinimumWidth(50)
        self.match_label.setStyleSheet(f"font-weight: bold; color: {SUBTITLE_GRAY};")
        layout.addWidget(self.match_label)

        # Amount input — visually prominent so volunteers know to enter a value
        amount_label = QLabel("Amount:")
        amount_label.setStyleSheet(f"font-weight: bold; color: {HARVEST_GOLD};")
        layout.addWidget(amount_label)
        self.amount_spin = NoScrollDoubleSpinBox()
        self.amount_spin.setRange(0, 99999.99)
        self.amount_spin.setDecimals(2)
        self.amount_spin.setSingleStep(1.00)
        self.amount_spin.setPrefix("$ ")
        self.amount_spin.setMinimumWidth(120)
        self.amount_spin.setStyleSheet(f"""
            QDoubleSpinBox {{
                border: 2px solid {HARVEST_GOLD};
                border-radius: 6px;
                padding: 4px 8px;
                background-color: {WHITE};
                font-size: 14px;
                font-weight: bold;
                min-height: 22px;
            }}
            QDoubleSpinBox:focus {{
                border-color: {ACCENT_GREEN};
                background-color: #FEFFFE;
            }}
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
                width: 0px;
                border: none;
            }}
        """)
        self.amount_spin.valueChanged.connect(self._on_changed)
        layout.addWidget(self.amount_spin)

        # Computed fields
        layout.addWidget(QLabel("FAM Match:"))
        self.match_amount_label = QLabel("$0.00")
        self.match_amount_label.setStyleSheet("font-weight: bold;")
        self.match_amount_label.setMinimumWidth(70)
        layout.addWidget(self.match_amount_label)

        layout.addWidget(QLabel("Customer Pays:"))
        self.customer_charged_label = QLabel("$0.00")
        self.customer_charged_label.setStyleSheet("font-weight: bold;")
        self.customer_charged_label.setMinimumWidth(70)
        layout.addWidget(self.customer_charged_label)

        # Remove button — red outline + red X, matching danger action buttons
        self.remove_btn = QPushButton("✕")
        self.remove_btn.setFixedSize(28, 28)
        self.remove_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {ERROR_COLOR};
                border: 1.5px solid {ERROR_COLOR};
                border-radius: 14px;
                font-weight: bold;
                font-size: 13px;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: {ERROR_COLOR};
                color: white;
            }}
        """)
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(self.remove_btn)

        self._update_match_label()

    def _load_methods(self):
        self.method_combo.clear()
        # Placeholder item — no userData so get_selected_method() returns None
        self.method_combo.addItem("Select Payment Type...")
        if self._market_id:
            methods = get_payment_methods_for_market(self._market_id, active_only=True)
            if not methods:
                # Fallback: if no methods assigned to market, show all active
                methods = get_all_payment_methods(active_only=True)
        else:
            methods = get_all_payment_methods(active_only=True)
        for m in methods:
            self.method_combo.addItem(
                f"{m['name']} ({m['match_percent']:.0f}% match)",
                userData=m
            )

    def reload_methods(self, market_id=None):
        """Reload the payment method dropdown, optionally filtered by market."""
        self._market_id = market_id
        self._load_methods()

    def _update_match_label(self):
        method = self.get_selected_method()
        if method:
            self.match_label.setText(f"{method['match_percent']:.0f}% match")
        else:
            self.match_label.setText("--")

    def _on_changed(self):
        self._update_match_label()
        self._recompute()
        self.changed.emit()

    def _recompute(self):
        method = self.get_selected_method()
        amount = self.amount_spin.value()
        if method:
            match_pct = method['match_percent']
        else:
            match_pct = 0.0
        match_amt = round(amount * (match_pct / (100.0 + match_pct)), 2)
        charged = round(amount - match_amt, 2)
        self.match_amount_label.setText(f"${match_amt:.2f}")
        self.customer_charged_label.setText(f"${charged:.2f}")

    def get_selected_method(self):
        return self.method_combo.currentData()

    def get_data(self):
        method = self.get_selected_method()
        if not method:
            return None
        amount = self.amount_spin.value()
        match_pct = method['match_percent']
        match_amount = round(amount * (match_pct / (100.0 + match_pct)), 2)
        customer_charged = round(amount - match_amount, 2)
        return {
            'payment_method_id': method['id'],
            'method_name_snapshot': method['name'],
            'match_percent_snapshot': match_pct,
            'method_amount': amount,
            'match_percent': match_pct,
            'match_amount': match_amount,
            'customer_charged': customer_charged,
        }

    def get_selected_method_id(self):
        """Return the ID of the currently selected payment method, or None."""
        method = self.get_selected_method()
        return method['id'] if method else None

    def has_method_selected(self):
        """Return True if a real payment method is selected (not the placeholder)."""
        return self.get_selected_method() is not None

    def set_excluded_methods(self, excluded_ids):
        """Gray out payment methods that are already selected in other rows.

        The row's own current selection is never disabled.
        The placeholder item (index 0) is always left enabled.
        Uses both flags (prevents selection) and foreground color (visual gray).
        """
        model = self.method_combo.model()
        if not isinstance(model, QStandardItemModel):
            return
        my_id = self.get_selected_method_id()
        gray = QBrush(QColor(180, 180, 180))
        normal = QBrush(QColor(0, 0, 0))
        for i in range(model.rowCount()):
            item = model.item(i)
            m = self.method_combo.itemData(i)
            if m is None:
                # Placeholder — always enabled
                continue
            mid = m['id']
            if mid in excluded_ids and mid != my_id:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                item.setForeground(gray)
            else:
                item.setFlags(item.flags() | Qt.ItemIsEnabled)
                item.setForeground(normal)

    def set_data(self, payment_method_id, amount):
        """Set the row from existing data."""
        for i in range(self.method_combo.count()):
            m = self.method_combo.itemData(i)
            if m and m['id'] == payment_method_id:
                self.method_combo.setCurrentIndex(i)
                break
        self.amount_spin.setValue(amount)
