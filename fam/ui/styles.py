"""Global QSS stylesheet and theme constants."""

import os as _os
import sys as _sys

# Path to the dropdown arrow image — handle both dev and PyInstaller frozen mode
if getattr(_sys, 'frozen', False):
    _ARROW_PATH = _os.path.join(
        _sys._MEIPASS, "fam", "ui", "_dropdown_arrow.png"
    ).replace("\\", "/")
else:
    _ARROW_PATH = _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)), "_dropdown_arrow.png"
    ).replace("\\", "/")

# Color palette — FAM official brand colors
PRIMARY_GREEN = "#2b493b"   # Dark Green
ACCENT_GREEN = "#469a45"    # Light Green
HARVEST_GOLD = "#e68a3e"    # Dark Orange
BACKGROUND = "#F7F6F2"
TEXT_COLOR = "#2C2C2C"
WARNING_COLOR = "#f79841"   # Light Orange
ERROR_COLOR = "#D32F2F"
WHITE = "#FFFFFF"
LIGHT_GRAY = "#E0E0E0"
MEDIUM_GRAY = "#9E9E9E"
SUBTITLE_GRAY = "#757575"
CARD_SHADOW = "#22000000"
FIELD_LABEL_BG = "#ECEAE4"  # Warm tinted label background

# Semantic background tints
SUCCESS_BG = "#e4ede8"
ERROR_BG = "#FFEBEE"
WARNING_BG = "#fef2e6"

# Font stacks
FONT_FAMILY = "'Inter', 'Source Sans Pro', 'Segoe UI', 'Arial', sans-serif"

# Canonical card frame style — use in all screens for consistent card appearance
CARD_FRAME_STYLE = f"""
    QFrame {{
        background-color: {WHITE};
        border: 1px solid #E2E2E2;
        border-radius: 10px;
        padding: 16px 20px;
    }}
"""

GLOBAL_STYLESHEET = f"""
/* ===== BASE ===== */
QWidget {{
    font-family: {FONT_FAMILY};
    font-size: 13px;
    color: {TEXT_COLOR};
    background-color: {BACKGROUND};
}}

QMainWindow {{
    background-color: {BACKGROUND};
}}

/* ===== DIALOGS & MESSAGE BOXES - light backgrounds ===== */
QDialog {{
    background-color: {BACKGROUND};
}}

QMessageBox {{
    background-color: {BACKGROUND};
}}

QMessageBox QLabel {{
    color: {TEXT_COLOR};
    background-color: transparent;
}}

/* ===== SIDEBAR ===== */
#sidebar {{
    background-color: transparent;
    min-width: 240px;
    max-width: 240px;
}}

#sidebar QPushButton {{
    background-color: transparent;
    color: white;
    border: none;
    text-align: left;
    padding: 14px 24px;
    font-size: 14px;
    border-radius: 6px;
    margin: 1px 8px;
}}

#sidebar QPushButton:hover {{
    background-color: rgba(255, 255, 255, 0.15);
}}

#sidebar QPushButton:checked {{
    background-color: rgba(255, 255, 255, 0.25);
    font-weight: bold;
    border-left: 3px solid rgba(255, 255, 255, 0.8);
}}

#sidebar_title {{
    color: white;
    font-size: 17px;
    font-weight: bold;
    padding: 20px 20px 10px 20px;
    background-color: transparent;
}}

#sidebar_subtitle {{
    color: rgba(255, 255, 255, 0.9);
    font-size: 17px;
    font-weight: bold;
    padding: 0px 20px 16px 20px;
    background-color: transparent;
}}

/* ===== CONTENT AREA ===== */
#content_area {{
    background-color: {BACKGROUND};
    padding: 8px;
}}

/* ===== CARDS ===== */
.card {{
    background-color: {WHITE};
    border-radius: 10px;
    border: 1px solid #E2E2E2;
    padding: 16px 20px;
}}

/* ===== TABLES ===== */
QTableWidget {{
    background-color: {WHITE};
    border: 1px solid {LIGHT_GRAY};
    border-radius: 8px;
    gridline-color: #ECECEC;
    selection-background-color: {SUCCESS_BG};
    selection-color: {TEXT_COLOR};
    alternate-background-color: #FAFAFA;
}}

QTableWidget::item {{
    padding: 8px 12px;
    background-color: {WHITE};
}}

/* Horizontal header - column headers */
QHeaderView::section {{
    background-color: #F5F5F5;
    color: {TEXT_COLOR};
    font-weight: bold;
    padding: 8px 12px;
    border: none;
    border-bottom: 2px solid {LIGHT_GRAY};
    border-right: 1px solid #ECECEC;
}}

/* HIDE vertical row-number header (fixes dark grey leading column) */
QTableWidget QHeaderView::section:vertical {{
    background-color: {WHITE};
    border: none;
}}

/* Table corner button */
QTableCornerButton::section {{
    background-color: #F5F5F5;
    border: none;
}}

/* ===== PRIMARY BUTTON ===== */
QPushButton#primary_btn, QPushButton.primary {{
    background-color: {HARVEST_GOLD};
    color: white;
    border: 2px solid {HARVEST_GOLD};
    border-radius: 6px;
    padding: 10px 24px;
    font-size: 14px;
    font-weight: bold;
    min-height: 36px;
}}

QPushButton#primary_btn:hover, QPushButton.primary:hover {{
    background-color: #c97430;
    border-color: #c97430;
}}

QPushButton#primary_btn:pressed, QPushButton.primary:pressed {{
    background-color: #b5662a;
    border-color: #b5662a;
}}

QPushButton#primary_btn:disabled, QPushButton.primary:disabled {{
    background-color: {MEDIUM_GRAY};
    border-color: {MEDIUM_GRAY};
}}

/* ===== SECONDARY BUTTON ===== */
QPushButton#secondary_btn, QPushButton.secondary {{
    background-color: {WHITE};
    color: {PRIMARY_GREEN};
    border: 2px solid {PRIMARY_GREEN};
    border-radius: 6px;
    padding: 10px 24px;
    font-size: 14px;
    font-weight: bold;
    min-height: 36px;
}}

QPushButton#secondary_btn:hover, QPushButton.secondary:hover {{
    background-color: {SUCCESS_BG};
}}

QPushButton#secondary_btn:pressed, QPushButton.secondary:pressed {{
    background-color: #c8ddd0;
}}

/* ===== DANGER BUTTON ===== */
QPushButton#danger_btn, QPushButton.danger {{
    background-color: {ERROR_COLOR};
    color: white;
    border: 2px solid {ERROR_COLOR};
    border-radius: 6px;
    padding: 10px 24px;
    font-size: 14px;
    font-weight: bold;
    min-height: 36px;
}}

QPushButton#danger_btn:hover, QPushButton.danger:hover {{
    background-color: #B71C1C;
    border-color: #B71C1C;
}}

QPushButton#danger_btn:pressed, QPushButton.danger:pressed {{
    background-color: #9A1515;
    border-color: #9A1515;
}}

/* ===== DEFAULT BUTTON ===== */
QPushButton {{
    background-color: {WHITE};
    color: {TEXT_COLOR};
    border: 1px solid {LIGHT_GRAY};
    border-radius: 6px;
    padding: 8px 16px;
    min-height: 32px;
}}

QPushButton:hover {{
    background-color: #F5F5F5;
    border-color: {MEDIUM_GRAY};
}}

QPushButton:pressed {{
    background-color: #EAEAEA;
}}

/* ===== INPUTS ===== */
QLineEdit, QDoubleSpinBox, QSpinBox {{
    border: 2px solid {LIGHT_GRAY};
    border-radius: 6px;
    padding: 10px 14px;
    background-color: {WHITE};
    font-size: 14px;
    min-height: 22px;
}}

QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {{
    border-color: {ACCENT_GREEN};
    background-color: #FEFFFE;
    outline: none;
}}

/* ===== COMBOBOX with visible dropdown caret ===== */
QComboBox {{
    border: 2px solid {LIGHT_GRAY};
    border-radius: 6px;
    padding: 10px 32px 10px 14px;
    background-color: {WHITE};
    font-size: 14px;
    min-height: 22px;
}}

QComboBox:focus {{
    border-color: {ACCENT_GREEN};
    background-color: #FEFFFE;
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 30px;
    border-left: 1px solid {LIGHT_GRAY};
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    background-color: #F5F5F5;
}}

QComboBox::down-arrow {{
    image: url({_ARROW_PATH});
    width: 12px;
    height: 8px;
}}

QComboBox QAbstractItemView {{
    background-color: {WHITE};
    selection-background-color: {SUCCESS_BG};
    border: 1px solid {LIGHT_GRAY};
    border-radius: 6px;
    padding: 4px;
    outline: none;
}}

QComboBox QAbstractItemView::item {{
    padding: 6px 10px;
    border-radius: 4px;
    min-height: 24px;
}}

QComboBox QAbstractItemView::item:hover {{
    background-color: #F0F0F0;
}}

QComboBox QAbstractItemView::item:selected {{
    background-color: {SUCCESS_BG};
    border-left: 2px solid {ACCENT_GREEN};
}}

/* ===== SPIN BOX — hide up/down arrows for cleaner UI ===== */
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QSpinBox::up-button, QSpinBox::down-button {{
    width: 0px;
    border: none;
}}

/* ===== TEXT AREA ===== */
QTextEdit, QPlainTextEdit {{
    border: 2px solid {LIGHT_GRAY};
    border-radius: 6px;
    padding: 10px 14px;
    background-color: {WHITE};
    font-size: 13px;
}}

QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT_GREEN};
    background-color: #FEFFFE;
}}

/* ===== LABELS ===== */
QLabel {{
    color: {TEXT_COLOR};
    background-color: transparent;
}}

QLabel#section_header {{
    font-size: 18px;
    font-weight: bold;
    color: {PRIMARY_GREEN};
    padding: 4px 0px;
}}

QLabel#screen_title {{
    font-size: 22px;
    font-weight: bold;
    color: {TEXT_COLOR};
    padding: 0px 0px 4px 0px;
}}

QLabel#subtitle {{
    font-size: 13px;
    color: {SUBTITLE_GRAY};
    padding: 0px 0px 8px 0px;
}}

QLabel.gold_total {{
    font-size: 22px;
    font-weight: bold;
    color: {HARVEST_GOLD};
}}

QLabel.error_text {{
    color: {ERROR_COLOR};
    font-size: 12px;
}}

QLabel.success_text {{
    color: {ACCENT_GREEN};
    font-size: 12px;
    font-weight: bold;
}}

/* ===== FORM FIELD LABELS - visually distinct, height-matched to inputs ===== */
QLabel.field_label {{
    background-color: {FIELD_LABEL_BG};
    border: 2px solid #D5D2CB;
    border-radius: 6px;
    padding: 10px 14px;
    font-weight: bold;
    font-size: 13px;
    color: #555555;
    min-height: 22px;
    max-height: 38px;
}}

/* ===== STATUS BADGES ===== */
QLabel#status_open {{
    background-color: {SUCCESS_BG};
    color: {PRIMARY_GREEN};
    border-radius: 10px;
    padding: 4px 12px;
    font-weight: bold;
    font-size: 12px;
}}

QLabel#status_closed {{
    background-color: {WARNING_BG};
    color: {HARVEST_GOLD};
    border-radius: 10px;
    padding: 4px 12px;
    font-weight: bold;
    font-size: 12px;
}}

/* ===== DATE EDIT ===== */
QDateEdit {{
    border: 2px solid {LIGHT_GRAY};
    border-radius: 6px;
    padding: 10px 14px;
    background-color: {WHITE};
    font-size: 14px;
    min-height: 22px;
}}

QDateEdit:focus {{
    border-color: {ACCENT_GREEN};
    background-color: #FEFFFE;
}}

/* ===== SCROLL AREA ===== */
QScrollArea {{
    border: none;
    background-color: transparent;
}}

QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}

QScrollBar:vertical {{
    background-color: transparent;
    width: 8px;
    border-radius: 4px;
    margin: 2px 0px;
}}

QScrollBar::handle:vertical {{
    background-color: rgba(0, 0, 0, 0.15);
    border-radius: 4px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: rgba(0, 0, 0, 0.30);
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QScrollBar:horizontal {{
    background-color: transparent;
    height: 8px;
    border-radius: 4px;
    margin: 0px 2px;
}}

QScrollBar::handle:horizontal {{
    background-color: rgba(0, 0, 0, 0.15);
    border-radius: 4px;
    min-width: 30px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: rgba(0, 0, 0, 0.30);
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}

/* ===== GROUP BOX ===== */
QGroupBox {{
    font-weight: bold;
    border: 1px solid {LIGHT_GRAY};
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 16px;
    background-color: {WHITE};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 16px;
    padding: 0 8px;
}}

/* ===== TAB WIDGET ===== */
QTabWidget::pane {{
    border: 1px solid {LIGHT_GRAY};
    border-radius: 0 0 6px 6px;
    background-color: {WHITE};
}}

QTabBar::tab {{
    background-color: #F5F5F5;
    border: 1px solid {LIGHT_GRAY};
    padding: 10px 24px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}

QTabBar::tab:hover {{
    background-color: #EFEFEF;
}}

QTabBar::tab:selected {{
    background-color: {WHITE};
    border-bottom-color: {WHITE};
    font-weight: bold;
    color: {PRIMARY_GREEN};
}}

/* ===== MESSAGE BARS ===== */
#message_bar_success {{
    background-color: {SUCCESS_BG};
    color: {PRIMARY_GREEN};
    border: 1px solid {ACCENT_GREEN};
    border-radius: 6px;
    padding: 10px 16px;
    font-weight: bold;
}}

#message_bar_error {{
    background-color: {ERROR_BG};
    color: {ERROR_COLOR};
    border: 1px solid {ERROR_COLOR};
    border-radius: 6px;
    padding: 10px 16px;
    font-weight: bold;
}}

#message_bar_warning {{
    background-color: {WARNING_BG};
    color: {WARNING_COLOR};
    border: 1px solid {WARNING_COLOR};
    border-radius: 6px;
    padding: 10px 16px;
    font-weight: bold;
}}

/* ===== FORM LAYOUT FIX - equal height labels and fields ===== */
QFormLayout QLabel {{
    padding: 8px 4px;
}}
"""
