"""Reusable payment method entry row widget."""

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QComboBox, QDoubleSpinBox, QLabel, QPushButton
)
from PySide6.QtCore import Signal
from fam.models.payment_method import get_all_payment_methods
from fam.ui.styles import LIGHT_GRAY, WHITE, ERROR_COLOR, SUBTITLE_GRAY


class PaymentRow(QFrame):
    """A single payment method entry row."""

    changed = Signal()
    remove_requested = Signal(object)  # emits self

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            PaymentRow {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
                padding: 6px 8px;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)

        # Payment method combo
        self.method_combo = QComboBox()
        self.method_combo.setMinimumWidth(160)
        self._load_methods()
        self.method_combo.currentIndexChanged.connect(self._on_changed)
        layout.addWidget(self.method_combo)

        # Discount label
        self.discount_label = QLabel("0%")
        self.discount_label.setMinimumWidth(50)
        self.discount_label.setStyleSheet(f"font-weight: bold; color: {SUBTITLE_GRAY};")
        layout.addWidget(self.discount_label)

        # Amount input
        layout.addWidget(QLabel("Amount: $"))
        self.amount_spin = QDoubleSpinBox()
        self.amount_spin.setRange(0, 99999.99)
        self.amount_spin.setDecimals(2)
        self.amount_spin.setSingleStep(1.00)
        self.amount_spin.setMinimumWidth(110)
        self.amount_spin.valueChanged.connect(self._on_changed)
        layout.addWidget(self.amount_spin)

        # Computed fields
        layout.addWidget(QLabel("Discount:"))
        self.discount_amount_label = QLabel("$0.00")
        self.discount_amount_label.setStyleSheet("font-weight: bold;")
        self.discount_amount_label.setMinimumWidth(70)
        layout.addWidget(self.discount_amount_label)

        layout.addWidget(QLabel("Customer Pays:"))
        self.customer_charged_label = QLabel("$0.00")
        self.customer_charged_label.setStyleSheet("font-weight: bold;")
        self.customer_charged_label.setMinimumWidth(70)
        layout.addWidget(self.customer_charged_label)

        # Remove button
        self.remove_btn = QPushButton("X")
        self.remove_btn.setFixedSize(32, 32)
        self.remove_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ERROR_COLOR};
                color: white;
                border-radius: 16px;
                font-weight: bold;
                font-size: 12px;
                border: none;
            }}
            QPushButton:hover {{ background-color: #B71C1C; }}
        """)
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(self.remove_btn)

        self._update_discount_label()

    def _load_methods(self):
        self.method_combo.clear()
        methods = get_all_payment_methods(active_only=True)
        for m in methods:
            self.method_combo.addItem(
                f"{m['name']} ({m['discount_percent']:.0f}% off)",
                userData=m
            )

    def _update_discount_label(self):
        method = self.get_selected_method()
        if method:
            self.discount_label.setText(f"{method['discount_percent']:.0f}% off")
        else:
            self.discount_label.setText("--")

    def _on_changed(self):
        self._update_discount_label()
        self._recompute()
        self.changed.emit()

    def _recompute(self):
        method = self.get_selected_method()
        amount = self.amount_spin.value()
        if method:
            discount_pct = method['discount_percent']
        else:
            discount_pct = 0.0
        discount = round(amount * (discount_pct / 100.0), 2)
        charged = round(amount - discount, 2)
        self.discount_amount_label.setText(f"${discount:.2f}")
        self.customer_charged_label.setText(f"${charged:.2f}")

    def get_selected_method(self):
        return self.method_combo.currentData()

    def get_data(self):
        method = self.get_selected_method()
        if not method:
            return None
        amount = self.amount_spin.value()
        discount_pct = method['discount_percent']
        discount_amount = round(amount * (discount_pct / 100.0), 2)
        customer_charged = round(amount - discount_amount, 2)
        return {
            'payment_method_id': method['id'],
            'method_name_snapshot': method['name'],
            'discount_percent_snapshot': discount_pct,
            'method_amount': amount,
            'discount_percent': discount_pct,
            'discount_amount': discount_amount,
            'customer_charged': customer_charged,
        }

    def set_data(self, payment_method_id, amount):
        """Set the row from existing data."""
        for i in range(self.method_combo.count()):
            m = self.method_combo.itemData(i)
            if m and m['id'] == payment_method_id:
                self.method_combo.setCurrentIndex(i)
                break
        self.amount_spin.setValue(amount)
