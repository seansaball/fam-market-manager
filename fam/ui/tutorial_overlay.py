"""Guided tutorial overlay — step-by-step walkthrough of the application."""

import re
import logging
from dataclasses import dataclass, field

from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QGraphicsDropShadowEffect, QScrollArea, QSizePolicy, QTabWidget
)
from PySide6.QtCore import Qt, Signal, QRectF, QPoint
from PySide6.QtGui import QPainter, QColor, QPainterPath, QPen

from fam.ui.styles import (
    PRIMARY_GREEN, ACCENT_GREEN, HARVEST_GOLD, WHITE, TEXT_COLOR,
    SUBTITLE_GRAY, LIGHT_GRAY, BACKGROUND
)

logger = logging.getLogger('fam.ui.tutorial_overlay')


# ---------------------------------------------------------------------------
# Step / Hint definitions
# ---------------------------------------------------------------------------

@dataclass
class TutorialHint:
    """One detailed hint highlighting a specific UI component within a step."""
    title: str
    description: str
    widget_path: str  # dot-separated attr path from MainWindow


@dataclass
class TutorialStep:
    """One step in the guided tutorial."""
    title: str
    description: str
    widget_path: str          # dot-separated attr path from MainWindow
    position: str = "right"   # card placement: right, left, below, above
    screen_index: int | None = None
    nav_index: int | None = None
    padding: int = 8
    top_offset: int = 0       # extra px to push card down from default position
    hints: list[TutorialHint] | None = None
    is_setup_prompt: bool = False


# ---------------------------------------------------------------------------
# Tutorial content
# ---------------------------------------------------------------------------

TUTORIAL_STEPS = [
    TutorialStep(
        title="Welcome to FAM Market Manager!",
        description=(
            "This quick tutorial will walk you through the main "
            "features of the app.\n\n"
            "Use the Next and Back buttons to navigate, "
            "or click Close at any time to exit."
        ),
        widget_path="sidebar",
        position="right",
    ),
    TutorialStep(
        title="Sidebar Navigation",
        description=(
            "Use this menu on the left to switch between the "
            "different sections of the app.\n\n"
            "The highlighted button shows which screen "
            "you are currently viewing."
        ),
        widget_path="sidebar",
        position="right",
    ),
    TutorialStep(
        title="Open a Market Day",
        description=(
            "Start here at the beginning of each market day.\n\n"
            "Select your market location, type your name, "
            "and click \"Open Market Day\" to begin recording "
            "transactions."
        ),
        widget_path="market_day_screen",
        position="right",
        screen_index=0,
        nav_index=0,
        padding=0,
        hints=[
            TutorialHint(
                "Market Setup",
                "Select your market location, enter your volunteer "
                "name, and click Open Market Day to start recording.",
                "market_day_screen.create_frame",
            ),
            TutorialHint(
                "Market Day Status",
                "Shows the active market day. You can close the "
                "market here at the end of the day, or reopen it "
                "if needed.",
                "market_day_screen.status_frame",
            ),
            TutorialHint(
                "Transaction Overview",
                "A quick-reference list of all transactions "
                "recorded during this market day.",
                "market_day_screen.txn_table",
            ),
        ],
    ),
    TutorialStep(
        title="Record Receipts",
        description=(
            "This is where you record each customer's purchases.\n\n"
            "Pick a vendor from the dropdown, enter the receipt "
            "total, and click \"Add Receipt to Order\".\n\n"
            "You can add multiple receipts per customer. "
            "When you're done, click \"Confirm All \u2013 Proceed to "
            "Payment\" at the bottom of the screen."
        ),
        widget_path="receipt_intake_screen",
        position="right",
        screen_index=1,
        nav_index=1,
        padding=0,
        top_offset=300,
        hints=[
            TutorialHint(
                "Customer Info Bar",
                "Shows the current customer ID, active market, "
                "and zip code field. Use Returning to look up a "
                "previous customer or New Customer to start fresh.",
                "receipt_intake_screen.customer_frame",
            ),
            TutorialHint(
                "Receipt Entry Form",
                "Select the vendor, enter the receipt total, and "
                "click Add Receipt to Order. Optionally add notes "
                "for each receipt.",
                "receipt_intake_screen.form_frame",
            ),
            TutorialHint(
                "Current Order",
                "All receipts for this customer. Review the running "
                "total, remove mistakes, or click Confirm All to "
                "proceed to payment.",
                "receipt_intake_screen.receipts_frame",
            ),
            TutorialHint(
                "Pending Orders",
                "Saved orders not yet fully paid. Resume, add "
                "receipts, or delete pending orders from here.",
                "receipt_intake_screen.pending_frame",
            ),
        ],
    ),
    TutorialStep(
        title="Process Payment",
        description=(
            "After receipts are confirmed, this screen shows "
            "the order summary and lets you add payment methods.\n\n"
            "Enter the amounts for each payment type and the app "
            "calculates the FAM match automatically.\n\n"
            "Make sure the remaining balance reaches $0.00, "
            "then click \"Confirm Payment\"."
        ),
        widget_path="payment_screen",
        position="right",
        screen_index=2,
        nav_index=2,
        padding=0,
        top_offset=200,
        hints=[
            TutorialHint(
                "Order Summary",
                "At-a-glance cards showing the order total, how "
                "much is allocated, what remains, customer amount, "
                "and the FAM match.",
                "payment_screen.summary_row",
            ),
            TutorialHint(
                "Vendor Breakdown",
                "Which vendors are part of this order and their "
                "individual receipt totals.",
                "payment_screen.vendor_table",
            ),
            TutorialHint(
                "Payment Methods",
                "Add payment methods (SNAP, Credit, Cash, etc.) "
                "and enter the amount for each. The FAM match "
                "calculates automatically.",
                "payment_screen.rows_container",
            ),
            TutorialHint(
                "Confirm & Collect",
                "The collection checklist shows exactly what to "
                "collect from the customer. Confirm when done, "
                "or save as draft to finish later.",
                "payment_screen.bottom_frame",
            ),
        ],
    ),
    TutorialStep(
        title="Adjustments & Corrections",
        description=(
            "Need to fix a mistake? Search for any transaction "
            "here to adjust the amount, change the vendor, "
            "or void it entirely.\n\n"
            "All changes are tracked in the audit log below."
        ),
        widget_path="admin_screen",
        position="right",
        screen_index=3,
        nav_index=3,
        padding=0,
        top_offset=350,
        hints=[
            TutorialHint(
                "Search & Filters",
                "Filter by market, status, or search by transaction "
                "ID to find the transaction you need to correct.",
                "admin_screen.filter_frame",
            ),
            TutorialHint(
                "Transaction Results",
                "Matching transactions with details. Use Adjust to "
                "change amounts or payment methods, or Void to "
                "cancel a transaction.",
                "admin_screen.table",
            ),
            TutorialHint(
                "Audit Log",
                "An append-only record of every change \u2014 who "
                "changed what, when, and why. Full accountability "
                "for all adjustments.",
                "admin_screen.audit_table",
            ),
        ],
    ),
    TutorialStep(
        title="FMNP Check Tracking",
        description=(
            "Use this screen to record FMNP (Farmers Market "
            "Nutrition Program) check entries from vendors.\n\n"
            "Select the market day, vendor, and enter the "
            "check details."
        ),
        widget_path="fmnp_screen",
        position="right",
        screen_index=4,
        nav_index=4,
        padding=0,
        top_offset=350,
        hints=[
            TutorialHint(
                "FMNP Entry Form",
                "Select the market day and vendor, enter the check "
                "amount and count, then add the entry.",
                "fmnp_screen.form_frame",
            ),
            TutorialHint(
                "FMNP Entries Table",
                "All FMNP entries for the selected market day. "
                "Edit or delete entries as needed.",
                "fmnp_screen.table",
            ),
        ],
    ),
    TutorialStep(
        title="Reports & Exports",
        description=(
            "View reports, charts, and export data here.\n\n"
            "Use the filters at the top to narrow by date, "
            "market, vendor, or payment type.\n\n"
            "Each tab shows a different report you can export."
        ),
        widget_path="reports_screen",
        position="right",
        screen_index=5,
        nav_index=5,
        padding=0,
        top_offset=350,
        hints=[
            TutorialHint(
                "Report Filters",
                "Narrow results by date range, market, vendor, "
                "or payment type. Filters apply across all tabs.",
                "reports_screen.filter_frame",
            ),
            TutorialHint(
                "Summary Cards",
                "Key totals at a glance: total receipts, customer "
                "payments, FAM match amounts, and FMNP totals.",
                "reports_screen.summary_row",
            ),
            TutorialHint(
                "Report Tabs",
                "Switch between reports: Vendor Reimbursement, "
                "FAM Match, Detailed Ledger, Activity Log, and "
                "more. Each has a CSV export button.",
                "reports_screen.tabs",
            ),
        ],
    ),
    TutorialStep(
        title="Settings",
        description=(
            "Manage your markets, vendors, and payment methods "
            "here.\n\n"
            "Use the tabs at the top to switch between "
            "configuration areas. You can add new items, "
            "update match percentages, and more."
        ),
        widget_path="settings_screen",
        position="right",
        screen_index=6,
        nav_index=6,
        padding=0,
        top_offset=350,
        hints=[
            TutorialHint(
                "Import Settings",
                "New to the app? Click Import Settings to load "
                "your markets, vendors, and payment methods from "
                "a .fam file. You can also export your current "
                "settings to share with another machine.",
                "settings_screen.import_btn",
            ),
            TutorialHint(
                "Configuration Tabs",
                "Markets: add locations and set match limits. "
                "Vendors: manage the vendor list. Payment Methods: "
                "configure match percentages and display order.",
                "settings_screen.tabs",
            ),
            TutorialHint(
                "Cloud Sync",
                "One-way sync that uploads your end-of-day "
                "reports to Google Sheets and FMNP check photos "
                "to Google Drive so coordinators and the finance "
                "team can view data remotely.\n\n"
                "To set up:\n"
                "1. Obtain a Google service account credentials "
                "file (JSON) from your coordinator\n"
                "2. Click \u201cLoad Credentials\u201d to import it\n"
                "3. Enter the Spreadsheet ID from your Google "
                "Sheet URL\n"
                "4. Paste your Google Drive folder URL for "
                "check photos\n"
                "5. Click \u201cSave Sync Settings\u201d\n\n"
                "Then click \u201cSync to Cloud\u201d any time to "
                "upload data and photos. Sync requires an "
                "internet connection \u2014 your local data is "
                "never affected if it fails.",
                "settings_screen.cloud_sync_tab",
            ),
            TutorialHint(
                "Auto-Updates (GitHub Releases)",
                "Check for new versions and install them "
                "directly from the app \u2014 no manual downloads "
                "needed.\n\n"
                "The repository URL is pre-filled with the "
                "official FAM Market Manager repository. "
                "Click \u201cCheck for Updates\u201d to see if a "
                "newer version is available.\n\n"
                "If an update is found, click "
                "\u201cDownload & Install\u201d \u2014 the app "
                "downloads the update, verifies the file, and "
                "restarts automatically. Your data is stored "
                "separately and is never affected.\n\n"
                "By default the app auto-checks once per day "
                "on launch. You can disable this with the "
                "checkbox at the bottom.",
                "settings_screen.updates_tab",
            ),
        ],
    ),
    TutorialStep(
        title="Help is Always One Click Away",
        description=(
            "When something is unclear or you need to look "
            "something up mid-market, the Help sidebar item is "
            "your first stop. Four tabs cover everything:\n\n"
            "\u2022 Walkthrough \u2014 An animated overview of a "
            "full market day. Great for new volunteers; loops "
            "in place so you can watch each step at your own "
            "pace.\n\n"
            "\u2022 Browse \u2014 Over 50 articles grouped by "
            "topic (during the market, FMNP, corrections, "
            "reports, sync, and more). Type any keyword in the "
            "search box to filter live.\n\n"
            "\u2022 Troubleshooting \u2014 Symptom-based guides "
            "(\u201csync is red\u201d, \u201cphoto isn\u2019t "
            "uploading\u201d, \u201capp is slow\u201d) with "
            "step-by-step actions.\n\n"
            "\u2022 System Status \u2014 A live snapshot of this "
            "laptop \u2014 app version, last sync, disk usage, "
            "record counts. The \u201cCopy Diagnostic Info\u201d "
            "button puts everything on your clipboard so you "
            "can paste it into a coordinator email.\n\n"
            "All your data is also kept safe in the background "
            "via automatic backups and an audit log \u2014 see "
            "the Help \u2192 Browse tab for the details."
        ),
        widget_path="help_screen",
        position="right",
        screen_index=7,
        nav_index=7,
        padding=0,
    ),
    TutorialStep(
        title="Quick Setup",
        description=(
            "Would you like to load FAM's default configuration?\n\n"
            "This adds 3 markets (Bethel Park, Bellevue, "
            "Test Market), 23 vendors, and 6 payment methods "
            "so you can start right away.\n\n"
            "You can always add, edit, or remove items later "
            "in Settings."
        ),
        widget_path="settings_screen",
        position="right",
        screen_index=6,
        nav_index=6,
        padding=0,
        top_offset=350,
        is_setup_prompt=True,
    ),
]


# ---------------------------------------------------------------------------
# Overlay widget
# ---------------------------------------------------------------------------

class TutorialOverlay(QWidget):
    """Full-window overlay that guides the user through the tutorial steps."""

    finished = Signal()
    auto_configure_requested = Signal()

    # Overlay dimming (0-255)
    _OVERLAY_ALPHA = 160
    _CARD_MAX_WIDTH = 440
    _CARD_MARGIN = 16  # gap between highlight edge and card
    _EDGE_PAD = 8      # minimum distance from overlay edges

    def __init__(self, main_window, steps: list[TutorialStep] | None = None):
        super().__init__(main_window.centralWidget())
        self._main_window = main_window
        self._steps = steps or TUTORIAL_STEPS
        self._current_index = 0
        self._highlight_rect = None  # QRect in overlay coords

        # Detail-mode state
        self._detail_mode = False
        self._hint_index = 0

        # Block mouse events from passing through
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.StrongFocus)

        # Build the instruction card (child QFrame)
        self._card = self._build_card()

        # Drag state for movable centred cards (steps >= 2)
        self._dragging = False
        self._drag_offset = QPoint()

        # Size to parent, show, grab focus
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus()

        self._show_step(0)

    # ------------------------------------------------------------------
    # Card construction
    # ------------------------------------------------------------------

    def _build_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("tutorial_card")
        card.setStyleSheet(f"""
            #tutorial_card {{
                background-color: {WHITE};
                border: 2px solid {ACCENT_GREEN};
                border-radius: 12px;
            }}
        """)
        card.setMinimumWidth(360)
        card.setMaximumWidth(self._CARD_MAX_WIDTH)

        # Drop shadow
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 60))
        card.setGraphicsEffect(shadow)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(8)

        # Title
        self._title_label = QLabel()
        self._title_label.setWordWrap(True)
        self._title_label.setStyleSheet(f"""
            font-size: 16px;
            font-weight: bold;
            color: {PRIMARY_GREEN};
            background: transparent;
        """)
        layout.addWidget(self._title_label)

        # Description
        self._desc_label = QLabel()
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet(f"""
            font-size: 13px;
            color: {TEXT_COLOR};
            line-height: 1.4;
            background: transparent;
        """)
        layout.addWidget(self._desc_label)

        # "More Details" button (visible when step has hints)
        self._more_details_btn = QPushButton("More Details \u25BE")
        self._more_details_btn.setObjectName("tut_details_btn")
        self._more_details_btn.setCursor(Qt.PointingHandCursor)
        self._more_details_btn.setStyleSheet(f"""
            #tut_details_btn {{
                color: {ACCENT_GREEN};
                font-size: 12px;
                font-weight: bold;
                background: transparent;
                border: 1px solid {LIGHT_GRAY};
                border-radius: 6px;
                padding: 4px 12px;
                min-height: 0px;
            }}
            #tut_details_btn:hover {{
                background-color: #F0EFEB;
                border-color: {ACCENT_GREEN};
            }}
        """)
        self._more_details_btn.clicked.connect(self._enter_detail_mode)
        self._more_details_btn.setVisible(False)
        layout.addWidget(self._more_details_btn)

        # Progress bar (thin strip)
        self._progress_bg = QFrame()
        self._progress_bg.setFixedHeight(4)
        self._progress_bg.setStyleSheet(f"""
            background-color: {LIGHT_GRAY};
            border-radius: 2px;
        """)
        self._progress_fill = QFrame(self._progress_bg)
        self._progress_fill.setFixedHeight(4)
        self._progress_fill.setStyleSheet(f"""
            background-color: {ACCENT_GREEN};
            border-radius: 2px;
        """)
        layout.addWidget(self._progress_bg)

        layout.addSpacing(2)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._back_btn = QPushButton("Back")
        self._back_btn.setObjectName("tut_back_btn")
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.setStyleSheet(f"""
            #tut_back_btn {{
                padding: 6px 14px; font-size: 12px; min-height: 0px;
                border-radius: 6px; border: 1px solid {LIGHT_GRAY};
                background-color: {WHITE}; color: {PRIMARY_GREEN};
            }}
            #tut_back_btn:hover {{
                background-color: #F0EFEB;
                border-color: {PRIMARY_GREEN};
            }}
            #tut_back_btn:disabled {{
                color: {LIGHT_GRAY}; border-color: {LIGHT_GRAY};
            }}
        """)
        self._back_btn.clicked.connect(self._go_back)
        btn_row.addWidget(self._back_btn)

        self._step_label = QLabel()
        self._step_label.setAlignment(Qt.AlignCenter)
        self._step_label.setStyleSheet(f"""
            font-size: 11px;
            color: {SUBTITLE_GRAY};
            background: transparent;
        """)
        btn_row.addWidget(self._step_label, 1)

        # Normal-mode Next button
        self._next_btn = QPushButton("Next")
        self._next_btn.setObjectName("tut_next_btn")
        self._next_btn.setCursor(Qt.PointingHandCursor)
        self._next_btn.setStyleSheet(f"""
            #tut_next_btn {{
                padding: 6px 14px; font-size: 12px; min-height: 0px;
                border-radius: 6px; background-color: {HARVEST_GOLD};
                color: white; font-weight: bold; border: none;
            }}
            #tut_next_btn:hover {{
                background-color: #d47a2e;
            }}
        """)
        self._next_btn.clicked.connect(self._go_next)
        btn_row.addWidget(self._next_btn)

        # Detail-mode: Next Hint button (hidden by default)
        self._next_hint_btn = QPushButton("Next Hint")
        self._next_hint_btn.setObjectName("tut_hint_btn")
        self._next_hint_btn.setCursor(Qt.PointingHandCursor)
        self._next_hint_btn.setStyleSheet(f"""
            #tut_hint_btn {{
                padding: 6px 10px; font-size: 12px; min-height: 0px;
                border-radius: 6px; border: 1px solid {ACCENT_GREEN};
                background-color: {WHITE}; color: {ACCENT_GREEN};
                font-weight: bold;
            }}
            #tut_hint_btn:hover {{
                background-color: #F0EFEB;
            }}
            #tut_hint_btn:disabled {{
                color: {LIGHT_GRAY}; border-color: {LIGHT_GRAY};
            }}
        """)
        self._next_hint_btn.clicked.connect(self._next_hint)
        self._next_hint_btn.setVisible(False)
        btn_row.addWidget(self._next_hint_btn)

        # Detail-mode: Next Step button (hidden by default)
        self._next_step_btn = QPushButton("Next Step")
        self._next_step_btn.setObjectName("tut_step_btn")
        self._next_step_btn.setCursor(Qt.PointingHandCursor)
        self._next_step_btn.setStyleSheet(f"""
            #tut_step_btn {{
                padding: 6px 10px; font-size: 12px; min-height: 0px;
                border-radius: 6px; background-color: {HARVEST_GOLD};
                color: white; font-weight: bold; border: none;
            }}
            #tut_step_btn:hover {{
                background-color: #d47a2e;
            }}
        """)
        self._next_step_btn.clicked.connect(self._next_step)
        self._next_step_btn.setVisible(False)
        btn_row.addWidget(self._next_step_btn)

        self._close_btn = QPushButton("\u2715  Close")
        self._close_btn.setObjectName("tut_close_btn")
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setToolTip("Close tutorial")
        self._close_btn.setStyleSheet("""
            #tut_close_btn {
                padding: 6px 12px; font-size: 12px; min-height: 0px;
                border-radius: 6px; background-color: #DC3545;
                color: white; font-weight: bold; border: none;
            }
            #tut_close_btn:hover {
                background-color: #C82333;
            }
        """)
        self._close_btn.clicked.connect(self._close_tutorial)
        btn_row.addWidget(self._close_btn)

        # Setup-prompt action buttons (hidden by default, shown on final step)
        self._setup_row = QHBoxLayout()
        self._setup_row.setSpacing(10)

        self._setup_yes_btn = QPushButton("Yes \u2014 Load Default Data")
        self._setup_yes_btn.setObjectName("tut_setup_yes")
        self._setup_yes_btn.setCursor(Qt.PointingHandCursor)
        self._setup_yes_btn.setStyleSheet(f"""
            #tut_setup_yes {{
                padding: 10px 18px; font-size: 13px; min-height: 0px;
                border-radius: 8px; background-color: {HARVEST_GOLD};
                color: white; font-weight: bold; border: none;
            }}
            #tut_setup_yes:hover {{
                background-color: #d47a2e;
            }}
        """)
        self._setup_yes_btn.clicked.connect(self._on_setup_yes)
        self._setup_row.addWidget(self._setup_yes_btn)

        self._setup_no_btn = QPushButton("No Thanks \u2014 Start Blank")
        self._setup_no_btn.setObjectName("tut_setup_no")
        self._setup_no_btn.setCursor(Qt.PointingHandCursor)
        self._setup_no_btn.setStyleSheet(f"""
            #tut_setup_no {{
                padding: 10px 18px; font-size: 13px; min-height: 0px;
                border-radius: 8px; border: 1px solid {LIGHT_GRAY};
                background-color: {WHITE}; color: {TEXT_COLOR};
            }}
            #tut_setup_no:hover {{
                background-color: #F0EFEB;
                border-color: {PRIMARY_GREEN};
            }}
        """)
        self._setup_no_btn.clicked.connect(self._on_setup_no)
        self._setup_row.addWidget(self._setup_no_btn)

        # Wrap in a widget for easy show/hide
        self._setup_widget = QWidget()
        self._setup_widget.setStyleSheet("background: transparent;")
        self._setup_widget.setLayout(self._setup_row)
        self._setup_widget.setVisible(False)
        layout.addWidget(self._setup_widget)

        # Button row comes after setup widget so Close appears
        # below the Yes/No buttons on the final setup screen
        layout.addLayout(btn_row)

        return card

    # ------------------------------------------------------------------
    # Widget resolution
    # ------------------------------------------------------------------

    def _resolve_widget(self, widget_path: str) -> QWidget | None:
        """Resolve a dot-separated attribute path to a QWidget."""
        mw = self._main_window

        if widget_path == "sidebar":
            return mw.centralWidget().findChild(QFrame, "sidebar")

        if widget_path == "_tutorial_btn":
            return getattr(mw, '_tutorial_btn', None)

        # nav_group.button(N) pattern
        m = re.match(r'nav_group\.button\((\d+)\)', widget_path)
        if m:
            return mw.nav_group.button(int(m.group(1)))

        # General dot-path traversal
        try:
            obj = mw
            for part in widget_path.split('.'):
                obj = getattr(obj, part)
            return obj if isinstance(obj, QWidget) else None
        except AttributeError:
            logger.warning("Tutorial: could not resolve widget path '%s'", widget_path)
            return None

    def _ensure_visible(self, widget: QWidget):
        """Switch any parent QTabWidget and scroll any parent QScrollArea."""
        parent = widget.parent()
        while parent:
            if isinstance(parent, QTabWidget):
                # Find which tab contains this widget and switch to it
                for i in range(parent.count()):
                    tab_w = parent.widget(i)
                    if tab_w is widget or (tab_w and tab_w.isAncestorOf(widget)):
                        parent.setCurrentIndex(i)
                        break
            elif isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget, 50, 50)
            parent = parent.parent()

    # ------------------------------------------------------------------
    # Step display
    # ------------------------------------------------------------------

    def _show_step(self, index: int):
        index = max(0, min(index, len(self._steps) - 1))
        self._current_index = index
        step = self._steps[index]

        # Reset detail mode
        self._detail_mode = False
        self._hint_index = 0

        # Switch screen if needed
        if step.screen_index is not None:
            self._main_window.stack.setCurrentIndex(step.screen_index)
            btn = self._main_window.nav_group.button(step.screen_index)
            if btn:
                btn.setChecked(True)
        elif step.nav_index is not None:
            btn = self._main_window.nav_group.button(step.nav_index)
            if btn:
                btn.setChecked(True)

        # Resolve the target widget
        widget = self._resolve_widget(step.widget_path)
        if widget and widget.isVisible():
            self._ensure_visible(widget)
            # Map widget rect to overlay coordinates
            top_left = widget.mapToGlobal(QPoint(0, 0))
            top_left = self.mapFromGlobal(top_left)
            rect = widget.rect()
            pad = step.padding
            self._highlight_rect = rect.translated(top_left)
            self._highlight_rect.adjust(-pad, -pad, pad, pad)
        else:
            # Fallback: highlight centre of overlay
            cx, cy = self.width() // 2, self.height() // 2
            self._highlight_rect = None

        # Update card content
        self._title_label.setText(step.title)
        self._desc_label.setText(step.description)

        total = len(self._steps)
        self._step_label.setText(f"Step {index + 1} of {total}")

        # Progress bar
        pct = (index + 1) / total
        bar_width = int(self._progress_bg.width() * pct)
        self._progress_fill.setFixedWidth(max(bar_width, 4))

        # Button states
        self._back_btn.setEnabled(index > 0)
        is_last = index == total - 1
        self._next_btn.setText("Finish" if is_last else "Next")

        # Show/hide detail-mode vs normal-mode vs setup-prompt buttons
        is_setup = getattr(step, 'is_setup_prompt', False)
        has_hints = bool(step.hints)

        if is_setup:
            # Setup prompt: show Yes/No, plus Back/progress/Close like other pages
            self._more_details_btn.setVisible(False)
            self._next_btn.setVisible(False)
            self._next_hint_btn.setVisible(False)
            self._next_step_btn.setVisible(False)
            self._back_btn.setVisible(True)
            self._back_btn.setEnabled(True)
            self._step_label.setVisible(True)
            self._progress_bg.setVisible(True)
            self._setup_widget.setVisible(True)
        else:
            # Normal step
            self._setup_widget.setVisible(False)
            self._back_btn.setVisible(True)
            self._step_label.setVisible(True)
            self._progress_bg.setVisible(True)
            self._more_details_btn.setVisible(has_hints)
            self._next_btn.setVisible(True)
            self._next_hint_btn.setVisible(False)
            self._next_step_btn.setVisible(False)

        # Position the card
        self._dragging = False
        self._position_card(step)
        self._card.show()
        self._card.raise_()

        # Show open-hand cursor on draggable centred cards
        if self._current_index >= 2:
            self._card.setCursor(Qt.OpenHandCursor)
        else:
            self._card.setCursor(Qt.ArrowCursor)

        self.update()  # repaint overlay

    # ------------------------------------------------------------------
    # Detail mode (More Details / hints)
    # ------------------------------------------------------------------

    def _enter_detail_mode(self):
        """Switch to detail mode, showing the first hint for this step."""
        step = self._steps[self._current_index]
        if not step.hints:
            return
        self._detail_mode = True
        self._show_hint(0)

    def _exit_detail_mode(self):
        """Return from detail mode to the step overview."""
        self._detail_mode = False
        self._hint_index = 0
        self._show_step(self._current_index)

    def _show_hint(self, index: int):
        """Display a specific hint within the current step."""
        step = self._steps[self._current_index]
        hints = step.hints
        if not hints:
            return
        index = max(0, min(index, len(hints) - 1))
        self._hint_index = index
        hint = hints[index]

        # Resolve and highlight the hint widget
        widget = self._resolve_widget(hint.widget_path)
        if widget and widget.isVisible():
            self._ensure_visible(widget)
            top_left = widget.mapToGlobal(QPoint(0, 0))
            top_left = self.mapFromGlobal(top_left)
            rect = widget.rect()
            self._highlight_rect = rect.translated(top_left)
            self._highlight_rect.adjust(-8, -8, 8, 8)
        else:
            self._highlight_rect = None

        # Update card content
        self._title_label.setText(hint.title)
        self._desc_label.setText(hint.description)

        total_hints = len(hints)
        self._step_label.setText(f"Detail {index + 1} of {total_hints}")

        # Progress bar tracks hint progress
        pct = (index + 1) / total_hints
        bar_width = int(self._progress_bg.width() * pct)
        self._progress_fill.setFixedWidth(max(bar_width, 4))

        # Button visibility: detail mode
        self._more_details_btn.setVisible(False)
        self._next_btn.setVisible(False)
        self._next_hint_btn.setVisible(True)
        self._next_step_btn.setVisible(True)

        # Back always enabled in detail mode (exits at first hint)
        self._back_btn.setEnabled(True)

        # Next Hint disabled at last hint
        is_last_hint = (index >= total_hints - 1)
        self._next_hint_btn.setEnabled(not is_last_hint)

        # Next Step label for last main step
        is_last_step = (self._current_index >= len(self._steps) - 1)
        self._next_step_btn.setText("Finish" if is_last_step else "Next Step")

        # Re-position card (centred for steps >= 2)
        self._dragging = False
        self._position_card(step)
        self._card.show()
        self._card.raise_()

        # Drag cursor
        if self._current_index >= 2:
            self._card.setCursor(Qt.OpenHandCursor)

        self.update()

    def _next_hint(self):
        """Advance to the next hint, or exit detail mode at the last one."""
        step = self._steps[self._current_index]
        if not step.hints:
            return
        if self._hint_index >= len(step.hints) - 1:
            # Last hint — exit detail mode back to overview
            self._exit_detail_mode()
        else:
            self._show_hint(self._hint_index + 1)

    def _next_step(self):
        """Exit detail mode and advance to the next main step."""
        self._detail_mode = False
        self._hint_index = 0
        if self._current_index >= len(self._steps) - 1:
            self._close_tutorial()
        else:
            self._show_step(self._current_index + 1)

    # ------------------------------------------------------------------
    # Card positioning
    # ------------------------------------------------------------------

    def _position_card(self, step: TutorialStep):
        """Place the instruction card near the highlighted area."""
        # Force layout to calculate real size
        self._card.adjustSize()
        self._card.updateGeometry()
        card_w = max(self._card.sizeHint().width(), self._card.minimumWidth())
        card_h = self._card.sizeHint().height()
        margin = self._CARD_MARGIN
        edge = self._EDGE_PAD

        # Set explicit size so card doesn't get clipped
        self._card.setFixedSize(card_w, card_h)

        # Steps past the sidebar intro (>= 2) are centred horizontally
        # with a fixed top position so the card doesn't bounce when
        # navigating between steps with different content heights
        if self._current_index >= 2:
            x = (self.width() - card_w) // 2
            y = int(self.height() * 0.18)
        elif not self._highlight_rect:
            # Centre on screen (fallback)
            x = (self.width() - card_w) // 2
            y = (self.height() - card_h) // 2
        else:
            hr = self._highlight_rect
            if step.position == "right":
                x = hr.right() + margin
                y = hr.top() + margin + step.top_offset
            elif step.position == "left":
                x = hr.left() - card_w - margin
                y = hr.top() + margin + step.top_offset
            elif step.position == "below":
                x = hr.left() + (hr.width() - card_w) // 2
                y = hr.bottom() + margin
            elif step.position == "above":
                x = hr.left() + (hr.width() - card_w) // 2
                y = hr.top() - card_h - margin
            else:
                x = hr.right() + margin
                y = hr.top()

        # Clamp to overlay bounds
        x = max(edge, min(x, self.width() - card_w - edge))
        y = max(edge, min(y, self.height() - card_h - edge))

        self._card.move(int(x), int(y))

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        overlay_color = QColor(0, 0, 0, self._OVERLAY_ALPHA)

        # Build path: full overlay minus the highlight hole
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))

        if self._highlight_rect:
            hole = QPainterPath()
            hole.addRoundedRect(QRectF(self._highlight_rect), 10, 10)
            path = path.subtracted(hole)

        painter.fillPath(path, overlay_color)

        # Accent border around the highlight
        if self._highlight_rect:
            pen = QPen(QColor(ACCENT_GREEN), 2.5)
            painter.setPen(pen)
            painter.drawRoundedRect(QRectF(self._highlight_rect), 10, 10)

        painter.end()

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        """Block clicks from passing through; start card drag for centred steps."""
        if (self._current_index >= 2
                and event.button() == Qt.LeftButton
                and self._card.geometry().contains(event.pos())):
            self._dragging = True
            self._drag_offset = event.pos() - self._card.pos()
            self._card.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        """Move the card when dragging."""
        if self._dragging:
            new_pos = event.pos() - self._drag_offset
            edge = self._EDGE_PAD
            card_w, card_h = self._card.width(), self._card.height()
            x = max(edge, min(new_pos.x(), self.width() - card_w - edge))
            y = max(edge, min(new_pos.y(), self.height() - card_h - edge))
            self._card.move(x, y)
        event.accept()

    def mouseReleaseEvent(self, event):
        """End card drag."""
        if self._dragging:
            self._dragging = False
            self._card.setCursor(Qt.OpenHandCursor)
        event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            if self._detail_mode:
                self._exit_detail_mode()
            else:
                self._close_tutorial()
        elif key in (Qt.Key_Right, Qt.Key_Return, Qt.Key_Enter):
            if self._detail_mode:
                self._next_hint()
            else:
                self._go_next()
        elif key == Qt.Key_Left:
            self._go_back()
        else:
            event.accept()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_next(self):
        if self._current_index >= len(self._steps) - 1:
            self._close_tutorial()
        else:
            self._show_step(self._current_index + 1)

    def _go_back(self):
        if self._detail_mode:
            if self._hint_index > 0:
                self._show_hint(self._hint_index - 1)
            else:
                self._exit_detail_mode()
        else:
            if self._current_index > 0:
                self._show_step(self._current_index - 1)

    def _on_setup_yes(self):
        """User chose to load default data."""
        self.auto_configure_requested.emit()
        self._close_tutorial()

    def _on_setup_no(self):
        """User chose to start blank."""
        self._close_tutorial()

    def _close_tutorial(self):
        self.finished.emit()
        self.hide()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def refresh_position(self):
        """Re-render current step after a resize."""
        self.setGeometry(self.parentWidget().rect())
        if 0 <= self._current_index < len(self._steps):
            if self._detail_mode:
                self._show_hint(self._hint_index)
            else:
                self._show_step(self._current_index)
