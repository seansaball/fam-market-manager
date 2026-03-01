"""Reusable financial summary card widget."""

from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QHBoxLayout
from PySide6.QtCore import Qt
from fam.ui.styles import (
    PRIMARY_GREEN, HARVEST_GOLD, WHITE, LIGHT_GRAY, TEXT_COLOR, ACCENT_GREEN,
    SUBTITLE_GRAY
)


class SummaryCard(QFrame):
    """A card displaying a financial summary with label and value."""

    def __init__(self, title="", value="$0.00", highlight=False, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            SummaryCard {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 10px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(f"""
            font-size: 11px;
            color: {SUBTITLE_GRAY};
            font-weight: bold;
            text-transform: uppercase;
        """)

        self.value_label = QLabel(value)
        color = HARVEST_GOLD if highlight else PRIMARY_GREEN
        self.value_label.setStyleSheet(f"""
            font-size: 17px;
            font-weight: bold;
            color: {color};
        """)

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: str):
        self.value_label.setText(value)

    def set_value_color(self, color: str):
        """Dynamically change the value text color."""
        self.value_label.setStyleSheet(f"""
            font-size: 17px;
            font-weight: bold;
            color: {color};
        """)

    def set_title(self, title: str):
        self.title_label.setText(title)


class SummaryRow(QFrame):
    """Horizontal row of summary cards."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(8)
        self.cards = {}

    def add_card(self, key, title, value="$0.00", highlight=False):
        card = SummaryCard(title, value, highlight)
        self.cards[key] = card
        self.layout.addWidget(card)
        return card

    def update_card(self, key, value):
        if key in self.cards:
            self.cards[key].set_value(value)

    def update_card_color(self, key, color):
        if key in self.cards:
            self.cards[key].set_value_color(color)
