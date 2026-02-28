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
    min-width: 220px;
    max-width: 220px;
}}

#sidebar QPushButton {{
    background-color: transparent;
    color: white;
    border: none;
    text-align: left;
    padding: 14px 20px;
    font-size: 14px;
    border-radius: 0px;
}}

#sidebar QPushButton:hover {{
    background-color: rgba(255, 255, 255, 0.15);
}}

#sidebar QPushButton:checked {{
    background-color: rgba(255, 255, 255, 0.25);
    font-weight: bold;
}}

#sidebar_title {{
    color: white;
    font-size: 17px;
    font-weight: bold;
    padding: 20px 20px 10px 20px;
    background-color: transparent;
}}

#sidebar_subtitle {{
    color: rgba(255, 255, 255, 0.8);
    font-size: 11px;
    padding: 0px 20px 16px 20px;
    background-color: transparent;
}}

/* ===== CONTENT AREA ===== */
#content_area {{
    background-color: {BACKGROUND};
}}

/* ===== CARDS ===== */
.card {{
    background-color: {WHITE};
    border-radius: 8px;
    border: 1px solid {LIGHT_GRAY};
    padding: 16px;
}}

/* ===== TABLES ===== */
QTableWidget {{
    background-color: {WHITE};
    border: 1px solid {LIGHT_GRAY};
    border-radius: 6px;
    gridline-color: {LIGHT_GRAY};
    selection-background-color: {SUCCESS_BG};
    selection-color: {TEXT_COLOR};
    alternate-background-color: #FAFAFA;
}}

QTableWidget::item {{
    padding: 6px 10px;
    background-color: {WHITE};
}}

/* Horizontal header - column headers */
QHeaderView::section {{
    background-color: #F5F5F5;
    color: {TEXT_COLOR};
    font-weight: bold;
    padding: 8px 10px;
    border: none;
    border-bottom: 2px solid {LIGHT_GRAY};
    border-right: 1px solid {LIGHT_GRAY};
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
    padding: 8px 20px;
    font-size: 14px;
    font-weight: bold;
    min-height: 20px;
}}

QPushButton#primary_btn:hover, QPushButton.primary:hover {{
    background-color: #c97430;
    border-color: #c97430;
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
    padding: 8px 20px;
    font-size: 14px;
    font-weight: bold;
    min-height: 20px;
}}

QPushButton#secondary_btn:hover, QPushButton.secondary:hover {{
    background-color: {SUCCESS_BG};
}}

/* ===== DANGER BUTTON ===== */
QPushButton#danger_btn, QPushButton.danger {{
    background-color: {ERROR_COLOR};
    color: white;
    border: 2px solid {ERROR_COLOR};
    border-radius: 6px;
    padding: 8px 20px;
    font-size: 14px;
    font-weight: bold;
    min-height: 20px;
}}

QPushButton#danger_btn:hover, QPushButton.danger:hover {{
    background-color: #B71C1C;
    border-color: #B71C1C;
}}

/* ===== DEFAULT BUTTON ===== */
QPushButton {{
    background-color: {WHITE};
    color: {TEXT_COLOR};
    border: 1px solid {LIGHT_GRAY};
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 28px;
}}

QPushButton:hover {{
    background-color: #F5F5F5;
    border-color: {MEDIUM_GRAY};
}}

/* ===== INPUTS ===== */
QLineEdit, QDoubleSpinBox, QSpinBox {{
    border: 2px solid {LIGHT_GRAY};
    border-radius: 6px;
    padding: 8px 12px;
    background-color: {WHITE};
    font-size: 14px;
    min-height: 20px;
}}

QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {{
    border-color: {ACCENT_GREEN};
    outline: none;
}}

/* ===== COMBOBOX with visible dropdown caret ===== */
QComboBox {{
    border: 2px solid {LIGHT_GRAY};
    border-radius: 6px;
    padding: 8px 32px 8px 12px;
    background-color: {WHITE};
    font-size: 14px;
    min-height: 20px;
}}

QComboBox:focus {{
    border-color: {ACCENT_GREEN};
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
    padding: 8px 12px;
    background-color: {WHITE};
    font-size: 13px;
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
    font-size: 20px;
    font-weight: bold;
    color: {TEXT_COLOR};
    padding: 0px;
}}

QLabel#subtitle {{
    font-size: 12px;
    color: {MEDIUM_GRAY};
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
    padding: 8px 12px;
    font-weight: bold;
    font-size: 13px;
    color: #555555;
    min-height: 20px;
    max-height: 20px;
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
    padding: 8px 12px;
    background-color: {WHITE};
    font-size: 14px;
    min-height: 20px;
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
    background-color: #F0F0F0;
    width: 10px;
    border-radius: 5px;
}}

QScrollBar::handle:vertical {{
    background-color: {MEDIUM_GRAY};
    border-radius: 5px;
    min-height: 30px;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
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
    padding: 8px 20px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}

QTabBar::tab:selected {{
    background-color: {WHITE};
    border-bottom-color: {WHITE};
    font-weight: bold;
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
