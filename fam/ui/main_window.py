"""Main window with sidebar navigation."""

import logging
import os
import sys

logger = logging.getLogger('fam.ui.main_window')

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QStackedWidget, QLabel, QButtonGroup, QFrame, QSizePolicy,
    QDialog, QDialogButtonBox
)
from PySide6.QtCore import Qt, QRect, QEvent, QUrl, QTimer, QThread
from PySide6.QtGui import QPixmap, QPainter, QColor, QBrush, QIcon, QDesktopServices

from fam import __version__
from fam.ui.styles import PRIMARY_GREEN, WHITE, LIGHT_GRAY, SUBTITLE_GRAY


def _resolve_asset(filename: str) -> str:
    """Return absolute path to a UI asset, handling frozen (PyInstaller) mode."""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, "fam", "ui", filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


class _PatternSidebar(QFrame):
    """Sidebar with a subtle tiled background pattern over the brand colour."""

    def __init__(self, pattern_path: str, parent=None):
        super().__init__(parent)
        self._tile = QPixmap(pattern_path) if pattern_path else QPixmap()
        self._base = QColor(PRIMARY_GREEN)
        self._opacity = 0.40  # branded texture over the dark green

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 1. Solid brand-green base
        painter.fillRect(self.rect(), self._base)

        # 2. Tile the pattern on top at low opacity
        if not self._tile.isNull():
            painter.setOpacity(self._opacity)
            tw, th = self._tile.width(), self._tile.height()
            for y in range(0, self.height(), th):
                for x in range(0, self.width(), tw):
                    painter.drawPixmap(x, y, self._tile)
            painter.setOpacity(1.0)

        painter.end()


from fam.ui.market_day_screen import MarketDayScreen
from fam.ui.receipt_intake_screen import ReceiptIntakeScreen
from fam.ui.payment_screen import PaymentScreen
from fam.ui.fmnp_screen import FMNPScreen
from fam.ui.admin_screen import AdminScreen
from fam.ui.reports_screen import ReportsScreen
from fam.ui.settings_screen import SettingsScreen
from fam.ui.help_screen import HelpScreen


class MainWindow(QMainWindow):
    """Main application window with sidebar navigation."""

    def __init__(self):
        super().__init__()
        from fam.utils.app_settings import get_market_code
        market_code = get_market_code()
        title = "FAM Market Day Transaction Manager"
        if market_code:
            title += f"  [{market_code}]"
        self.setWindowTitle(title)
        icon_path = _resolve_asset("fam_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setMinimumSize(1200, 750)
        self.resize(1400, 850)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar with subtle tiled background pattern
        bg_path = _resolve_asset("_fam_background.jpg")
        sidebar = _PatternSidebar(bg_path)
        sidebar.setObjectName("sidebar")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        # Logo in sidebar
        logo_path = _resolve_asset("_fam_logo_white.png")

        logo_label = QLabel()
        logo_label.setObjectName("sidebar_logo")
        logo_label.setAlignment(Qt.AlignCenter)
        logo_label.setStyleSheet("background-color: transparent; padding: 20px 20px 4px 20px;")
        pixmap = QPixmap(logo_path)
        if not pixmap.isNull():
            scaled = pixmap.scaledToWidth(160, Qt.SmoothTransformation)
            logo_label.setPixmap(scaled)
        else:
            # Fallback if image not found
            logo_label.setText("FAM")
            logo_label.setStyleSheet(
                "color: white; font-size: 28px; font-weight: bold; "
                "background-color: transparent; padding: 20px 20px 4px 20px;"
            )
        sidebar_layout.addWidget(logo_label)

        sub_label = QLabel("FAM Market Manager")
        sub_label.setObjectName("sidebar_subtitle")
        sub_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(sub_label)

        # Navigation buttons
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        nav_items = [
            ("Market", 0),
            ("Receipt Intake", 1),
            ("Payment", 2),
            ("Adjustments", 3),
            ("FMNP Entry", 4),
            ("Reports", 5),
            ("Settings", 6),
            ("Help", 7),
        ]

        for label, idx in nav_items:
            btn = QPushButton(f"  {label}")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            self.nav_group.addButton(btn, idx)
            sidebar_layout.addWidget(btn)

        sidebar_layout.addStretch()

        # About button (version + clickable)
        self._about_btn = QPushButton(f"  v{__version__}  \u2022  About")
        self._about_btn.setCursor(Qt.PointingHandCursor)
        self._about_btn.setStyleSheet("""
            QPushButton {
                color: rgba(255,255,255,0.5);
                font-size: 11px;
                padding: 10px;
                background: transparent;
                border: none;
                text-align: left;
                min-height: 0px;
            }
            QPushButton:hover {
                color: rgba(255,255,255,0.85);
            }
        """)
        self._about_btn.clicked.connect(self._show_about)
        sidebar_layout.addWidget(self._about_btn)

        main_layout.addWidget(sidebar)

        # Content area
        content_frame = QFrame()
        content_frame.setObjectName("content_area")
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Header bar with tutorial button
        header_bar = QFrame()
        header_bar.setObjectName("header_bar")
        header_bar.setFixedHeight(40)
        header_bar.setStyleSheet(f"""
            #header_bar {{
                background-color: {WHITE};
                border-bottom: 1px solid {LIGHT_GRAY};
            }}
        """)
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(16, 4, 16, 4)
        header_layout.addStretch()

        # ── Device tag (multi-laptop disambiguator) ─────────────
        # Customer labels carry a 3-char device tag (e.g.
        # "C-005-A1B"); displaying the tag here lets a coordinator
        # see at a glance which device they're on without opening
        # Settings.  Critical when 5 laptops are deployed at one
        # market — without this, "I'm working on C-007" is
        # ambiguous about which laptop.
        from fam.utils.app_settings import get_device_tag
        self._device_tag_label = QLabel(f"Device: {get_device_tag()}")
        self._device_tag_label.setStyleSheet(f"""
            QLabel {{
                font-size: 12px;
                font-weight: bold;
                color: {SUBTITLE_GRAY};
                padding: 4px 10px;
                border: 1px solid {LIGHT_GRAY};
                border-radius: 6px;
                background-color: {WHITE};
                margin-right: 8px;
            }}
        """)
        self._device_tag_label.setToolTip(
            "This device's tag.  Every customer label generated on "
            "this laptop ends in '-{tag}' (e.g. C-005-{tag}) so "
            "labels stay unique even when multiple laptops are "
            "deployed at the same market.\n\n"
            "Override the tag in Settings → About this Device "
            "(useful when you want a friendly label like 'LB1' "
            "instead of the auto-generated hash).".format(
                tag=get_device_tag()
            )
        )
        header_layout.addWidget(self._device_tag_label)

        # ── Sync indicator + button (hidden until configured) ───
        self._sync_indicator = QLabel("")
        self._sync_indicator.setVisible(False)
        header_layout.addWidget(self._sync_indicator)
        self._set_sync_indicator("offline")

        self._sync_btn = QPushButton("☁️  Sync to Cloud")
        self._sync_btn.setCursor(Qt.PointingHandCursor)
        self._sync_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 5px 14px;
                font-size: 12px;
                min-height: 0px;
                border: 1px solid {LIGHT_GRAY};
                border-radius: 6px;
                background-color: {WHITE};
                color: {SUBTITLE_GRAY};
            }}
            QPushButton:hover {{
                background-color: #F0EFEB;
                color: {PRIMARY_GREEN};
                border-color: {PRIMARY_GREEN};
            }}
            QPushButton:disabled {{
                color: #ccc;
                border-color: #ddd;
            }}
        """)
        self._sync_btn.clicked.connect(lambda: self._trigger_sync(force=True))
        self._sync_btn.setVisible(False)
        header_layout.addWidget(self._sync_btn)

        self._tutorial_btn = QPushButton("\U0001F4D6  Start Tutorial")
        self._tutorial_btn.setCursor(Qt.PointingHandCursor)
        self._tutorial_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 5px 14px;
                font-size: 12px;
                min-height: 0px;
                border: 1px solid {LIGHT_GRAY};
                border-radius: 6px;
                background-color: {WHITE};
                color: {SUBTITLE_GRAY};
            }}
            QPushButton:hover {{
                background-color: #F0EFEB;
                color: {PRIMARY_GREEN};
                border-color: {PRIMARY_GREEN};
            }}
        """)
        self._tutorial_btn.clicked.connect(self.start_tutorial)
        header_layout.addWidget(self._tutorial_btn)

        content_layout.addWidget(header_bar)

        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack)
        main_layout.addWidget(content_frame, 1)

        # Create screens
        self.market_day_screen = MarketDayScreen()
        self.receipt_intake_screen = ReceiptIntakeScreen()
        self.payment_screen = PaymentScreen()
        self.fmnp_screen = FMNPScreen()
        self.admin_screen = AdminScreen()
        self.reports_screen = ReportsScreen()
        self.settings_screen = SettingsScreen()
        self.help_screen = HelpScreen()

        self.stack.addWidget(self.market_day_screen)   # 0
        self.stack.addWidget(self.receipt_intake_screen) # 1
        self.stack.addWidget(self.payment_screen)        # 2
        self.stack.addWidget(self.admin_screen)          # 3
        self.stack.addWidget(self.fmnp_screen)           # 4
        self.stack.addWidget(self.reports_screen)        # 5
        self.stack.addWidget(self.settings_screen)       # 6
        self.stack.addWidget(self.help_screen)           # 7

        # Connect navigation
        self.nav_group.idClicked.connect(self._navigate)

        # Connect signals
        self.market_day_screen.market_day_changed.connect(self._on_market_day_changed)
        self.receipt_intake_screen.customer_order_ready.connect(self._on_customer_order_ready)
        # Navigation signals — move the volunteer to the right screen after
        # specific UI actions.
        self.payment_screen.return_to_intake_requested.connect(self._on_return_to_intake)
        self.payment_screen.draft_saved.connect(self._on_draft_saved)
        # Sync triggers — every data mutation fires into _trigger_sync, which
        # enforces a 60-second cooldown so rapid changes don't flood the
        # Google Sheets API.  FMNP save/update/delete, payment confirm, draft
        # save, admin adjust/void, and receipt-intake void all route here so
        # the sync indicator reflects reality after any user action.
        self.payment_screen.payment_confirmed.connect(self._trigger_sync)
        self.payment_screen.draft_saved.connect(self._trigger_sync)
        # v2.0.6: FMNP and Admin signals carry the affected
        # market_day_id so mutations against CLOSED market days
        # (FMNP after-the-fact entries, Admin adjustments / voids
        # of historical receipts) reach the cloud.  See
        # ``_on_fmnp_entry_saved`` and ``_on_admin_data_changed``
        # for the scope-override logic.  Receipt Intake's
        # data_changed always fires on the OPEN day (the screen
        # is bound to ``_active_market_day``) so it stays on the
        # bare ``_trigger_sync`` slot.
        self.fmnp_screen.entry_saved.connect(self._on_fmnp_entry_saved)
        self.admin_screen.data_changed.connect(self._on_admin_data_changed)
        self.receipt_intake_screen.data_changed.connect(self._trigger_sync)
        # v2.0.6: Settings mutations (vendor / market / payment-method
        # adds, edits, toggles, assignments, reward rules) affect rows
        # across ALL markets and time, so they need a full-scope sync —
        # not the narrow per-md auto-sync used for receipt intake.
        # ``_on_settings_changed`` forces ``delete_stale=True`` so
        # whole-dataset tabs (Vendor Reimbursement, Error Log)
        # re-converge on the new config.
        self.settings_screen.settings_changed.connect(
            self._on_settings_changed)

        # Reports refresh — every financial mutation also bumps the
        # Reports screen so its tables (Vendor Reimbursement, FAM
        # Match, Detailed Ledger, summary cards including FAM
        # Absorbed) reflect new data without the operator having
        # to re-navigate.  v1.9.10 follow-up (2026-05-01, onsite
        # report): a manager adjusted a transaction to "customer
        # is gone" and saw an Unallocated Funds line item committed
        # to the DB, but the FAM Absorbed card on the Reports tab
        # remained at its old value because navigation-driven
        # refresh only fires on tab switch.  Now any of these
        # signals also re-runs ``reports_screen.refresh()``.
        self.payment_screen.payment_confirmed.connect(
            self.reports_screen.refresh)
        self.payment_screen.draft_saved.connect(
            self.reports_screen.refresh)
        self.fmnp_screen.entry_saved.connect(
            self.reports_screen.refresh)
        self.admin_screen.data_changed.connect(
            self.reports_screen.refresh)
        self.receipt_intake_screen.data_changed.connect(
            self.reports_screen.refresh)

        # Select first screen
        first_btn = self.nav_group.button(0)
        if first_btn:
            first_btn.setChecked(True)
        self.stack.setCurrentIndex(0)

        # Tutorial overlay (created on demand)
        self._tutorial_overlay = None
        self.centralWidget().installEventFilter(self)

        # v1.9.9+ stale-market-day auto-close: if any market days were
        # left open across calendar boundaries, close them now and
        # surface a one-time dialog so the volunteer knows what
        # happened.  Defer past the first paint so the dialog has a
        # parent window to anchor to.
        QTimer.singleShot(300, self._check_stale_market_days)

        # Auto-launch tutorial on first run (after the window is painted)
        QTimer.singleShot(500, self._maybe_auto_tutorial)

        # Periodic database backup timer (active only while a market day is open)
        self._backup_timer = QTimer(self)
        self._backup_timer.setInterval(5 * 60 * 1000)  # 5 minutes
        self._backup_timer.timeout.connect(self._on_backup_timer)

        # Periodic sync timer (opt-in, active only while a market day is open)
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(5 * 60 * 1000)  # 5 minutes
        self._sync_timer.timeout.connect(self._on_sync_timer)
        self._sync_thread = None
        self._sync_worker = None

        # Sync cooldown — prevents rapid-fire auto-syncs that hit API rate limits
        self._sync_cooldown = QTimer(self)
        self._sync_cooldown.setSingleShot(True)
        self._sync_cooldown.setInterval(60_000)  # 60 seconds
        self._sync_deferred = QTimer(self)
        self._sync_deferred.setSingleShot(True)
        self._sync_deferred.setInterval(60_000)  # fire when cooldown would expire
        self._sync_deferred.timeout.connect(self._trigger_sync)

        # v2.0.7: Sync-watchdog timer.  When a sync is triggered we
        # set the button to "Syncing..." (disabled) and start the
        # background QThread/SyncWorker.  In the happy path the
        # worker emits ``finished`` or ``error`` and the slot
        # restores the button.  But there are real failure modes
        # where neither signal fires:
        #
        #   * an exception between the button update and
        #     ``thread.start()`` (UnboundLocalError, ImportError, etc.)
        #     leaves the button "Syncing..." with no worker running;
        #   * a Qt cross-thread signal-delivery hiccup or a worker
        #     run() that exits via a code path we didn't anticipate;
        #   * the user-reported v2.0.7 incident — sync indicator says
        #     "Last sync OK" with a stale timestamp while the button
        #     is still pinned in the disabled "Syncing..." state with
        #     no actual sync running, and no way for the volunteer
        #     to recover (the disabled button is unclickable).
        #
        # The watchdog fires 5 minutes after _trigger_sync starts.
        # On fire it force-resets the button + indicator + nulls the
        # thread refs so the user can re-click Sync to Cloud.  The
        # happy path stops the watchdog in the finished/error
        # handlers, so it only ever fires on stuck states.
        self._sync_watchdog = QTimer(self)
        self._sync_watchdog.setSingleShot(True)
        self._sync_watchdog.setInterval(5 * 60 * 1000)  # 5 minutes
        self._sync_watchdog.timeout.connect(self._on_sync_watchdog_fired)

        # Auto-update check thread tracking
        self._update_check_thread = None
        self._update_check_worker = None

        # OS-level network reachability monitor (Qt 6 QNetworkInformation).
        # Used so the sync indicator can honestly say "No network" when the
        # laptop is disconnected, rather than painting a stale "Last sync OK"
        # green based on a historical timestamp.
        self._network_info = None
        self._setup_network_monitor()

        # If a market day is already open at startup (e.g. after crash), start timers
        QTimer.singleShot(1000, self._update_backup_timer)
        QTimer.singleShot(1500, self._update_sync_visibility)
        QTimer.singleShot(2000, self._update_sync_timer)

        # Auto-check for updates 5 seconds after launch
        QTimer.singleShot(5000, self._auto_check_for_updates)

    def _navigate(self, idx):
        self.stack.setCurrentIndex(idx)
        # Refresh the target screen
        widget = self.stack.widget(idx)
        if hasattr(widget, 'refresh'):
            widget.refresh()
        # Refresh sync indicator (e.g. after saving sync settings)
        self._update_sync_visibility()

    def _on_market_day_changed(self):
        """Refresh dependent screens when market day changes."""
        self.receipt_intake_screen.refresh()
        self._update_backup_timer()
        self._update_title_bar()
        self._maybe_sync_on_close()

    def _update_title_bar(self):
        """Update the title bar to reflect the current market code."""
        from fam.utils.app_settings import get_market_code
        market_code = get_market_code()
        title = "FAM Market Day Transaction Manager"
        if market_code:
            title += f"  [{market_code}]"
        self.setWindowTitle(title)

    def _update_backup_timer(self):
        """Start or stop periodic backup based on market day state."""
        from fam.models.market_day import get_open_market_day
        if get_open_market_day():
            if not self._backup_timer.isActive():
                self._backup_timer.start()
        else:
            self._backup_timer.stop()

    def _on_backup_timer(self):
        """5-minute periodic backup while market day is open."""
        from fam.database.backup import create_backup
        create_backup(reason="auto")

    # ── Network reachability monitor ────────────────────────────

    def _setup_network_monitor(self):
        """Load the Qt network-information backend so the sync indicator
        can react to the laptop being disconnected.

        This uses the OS's own reporting (Windows: Network List Manager)
        and does *not* make outbound network requests.  If the backend
        cannot load for any reason, we silently fall through — the
        indicator will simply not show "No network" but every other
        state continues to work as before.
        """
        try:
            from PySide6.QtNetwork import QNetworkInformation
            if QNetworkInformation.loadDefaultBackend():
                info = QNetworkInformation.instance()
                if info is not None:
                    info.reachabilityChanged.connect(
                        self._on_network_reachability_changed)
                    self._network_info = info
                    logger.info("Network monitor active: backend=%s reach=%s",
                                info.backendName(), info.reachability())
        except Exception:
            logger.exception("Could not initialize network monitor")
            self._network_info = None

    def _os_network_connected(self) -> bool:
        """Return True unless the OS explicitly reports no network.

        Conservative by design: we only return False when reachability
        is Disconnected.  Unknown / Local / Site / Online all count as
        'probably ok' so that a missing or uncertain backend never
        misleads the user with a false "No network" indicator.
        """
        if self._network_info is None:
            return True
        try:
            from PySide6.QtNetwork import QNetworkInformation
            reach = self._network_info.reachability()
            return reach != QNetworkInformation.Reachability.Disconnected
        except Exception:
            return True

    def _on_network_reachability_changed(self, _reachability):
        """Qt slot: OS reported a change in network reachability.

        Repaint the indicator so green/gray reflects the new reality
        without waiting for the next user action.
        """
        logger.info("Network reachability changed: %s", _reachability)
        # Only refresh the idle indicator — don't disturb an in-flight sync
        if not (self._sync_thread and self._sync_thread.isRunning()):
            self._update_sync_visibility()

    # ── Cloud sync ─────────────────────────────────────────────

    def refresh_device_tag_display(self):
        """Re-read the device tag and update the header label.

        Called by the Settings screen after the override is changed
        so the header reflects the new value without an app restart.
        Idempotent — safe to call when nothing has changed.
        """
        from fam.utils.app_settings import get_device_tag
        if hasattr(self, '_device_tag_label'):
            tag = get_device_tag()
            self._device_tag_label.setText(f"Device: {tag}")
            self._device_tag_label.setToolTip(
                f"This device's tag.  Every customer label generated "
                f"on this laptop ends in '-{tag}' (e.g. C-005-{tag}) "
                f"so labels stay unique even when multiple laptops "
                f"are deployed at the same market.\n\n"
                f"Override the tag in Settings → About this Device.")

    def _set_sync_indicator(self, state: str, detail: str = ""):
        """Update the sync-health / network indicator.

        Labels describe what the app actually knows — not a claim about
        live connectivity we cannot prove.  Prior to v1.9.5 the states
        were "Online" / "Offline" which misled users into thinking the
        app had verified internet access; it had not.

        *state*:
            ``"online"``      — last sync attempt succeeded   (green)
            ``"no_network"``  — OS reports no network         (gray)
            ``"never"``       — configured, no sync yet       (gray)
            ``"syncing"``     — sync in progress              (amber)
            ``"warning"``     — last sync had photo issues    (amber)
            ``"error"``       — last sync attempt failed      (red)
            ``"offline"``     — legacy alias for "never"      (gray)
        *detail*: optional extra text shown to the right (timestamp,
        error summary, etc.).
        """
        if state == "online":
            color = PRIMARY_GREEN
            label = "Last sync OK"
        elif state == "syncing":
            color = "#F5A623"  # amber
            label = "Syncing…"
        elif state == "warning":
            color = "#F5A623"  # amber
            label = "Attention"
        elif state == "error":
            color = "#d32f2f"
            label = "Sync failed"
        elif state == "no_network":
            color = SUBTITLE_GRAY
            label = "No network"
        else:
            # "never" and legacy "offline" both land here
            color = SUBTITLE_GRAY
            label = "Not synced yet"

        text = f"<span style='color:{color}; font-size:14px;'>●</span>"
        text += f"&nbsp;<span style='color:{color}; font-weight:600; font-size:12px;'>{label}</span>"
        if detail:
            text += f"&nbsp;&nbsp;<span style='color:{SUBTITLE_GRAY}; font-size:11px;'>{detail}</span>"
        self._sync_indicator.setText(text)
        self._sync_indicator.setStyleSheet("background: transparent; padding: 0 8px;")

    def _update_sync_visibility(self):
        """Show/hide the sync button + indicator based on configuration.

        Indicator priority (highest first):
            OS network disconnected   -> "No network"           (gray)
            Configured + never synced -> "Not synced yet"       (gray)
            Configured + last sync ok -> "Last sync OK"         (green)
            Not configured            -> indicator hidden
        Live "Syncing…" and "Sync failed" states are set by the sync
        completion handlers, not here; this method only paints the
        idle-state indicator.
        """
        try:
            from fam.utils.app_settings import is_sync_configured, get_last_sync_at
            configured = is_sync_configured()
            self._sync_btn.setVisible(configured)
            self._sync_indicator.setVisible(configured)
            if not configured:
                self._set_sync_indicator("never")
                return

            # OS-level reachability takes priority — if the laptop is
            # disconnected we don't want a stale "Last sync OK" green
            # to imply the volunteer is currently online.  Their data
            # is still safe locally; the detail tooltip makes that clear.
            if not self._os_network_connected():
                last = get_last_sync_at()
                if last:
                    short = last[11:16] if len(last) > 16 else last
                    self._set_sync_indicator(
                        "no_network", f"Last sync: {short} (data safe locally)")
                else:
                    self._set_sync_indicator("no_network")
                return

            last = get_last_sync_at()
            if last:
                short = last[11:16] if len(last) > 16 else last
                self._set_sync_indicator("online", f"Last sync: {short}")
            else:
                self._set_sync_indicator("never")
        except Exception:
            logger.exception("Could not refresh sync indicator")

    def _maybe_sync_on_close(self):
        """Trigger sync when market day closes, if enabled."""
        from fam.utils.app_settings import get_setting
        from fam.models.market_day import get_open_market_day
        if get_open_market_day() is None and get_setting('sync_on_close') == '1':
            self._trigger_sync()
        # Also update sync timer state
        self._update_sync_timer()

    def _update_sync_timer(self):
        """Start or stop the periodic sync timer."""
        from fam.utils.app_settings import get_setting
        from fam.models.market_day import get_open_market_day
        if (get_open_market_day() and
                get_setting('sync_periodic') == '1'):
            if not self._sync_timer.isActive():
                self._sync_timer.start()
        else:
            self._sync_timer.stop()

    def _on_sync_timer(self):
        """Periodic sync while market day is open."""
        self._trigger_sync()

    def _on_fmnp_entry_saved(self, md_id: int):
        """Slot for ``fmnp_screen.entry_saved(int)``.

        v2.0.6 fix: trigger a sync scoped to the FMNP entry's market
        day rather than the currently-open market day.  Coordinators
        regularly add FMNP entries to CLOSED market days after the
        fact (paper checks delivered later, end-of-month batch
        entry).  Pre-fix, the auto-sync narrowed scope to the open
        market day and silently skipped the closed day's new
        entries — they wouldn't reach the cloud sheet until a manual
        full sync was triggered.  Now the affected day is collected
        regardless of open/closed state.

        ``md_id == 0`` means the signal couldn't determine the
        affected day (defensive default) — fall back to the standard
        narrow-scope behavior in that case.
        """
        if md_id and md_id > 0:
            self._trigger_sync(scope_md_id_override=md_id)
        else:
            self._trigger_sync()

    def _on_admin_data_changed(self, md_id: int):
        """Slot for ``admin_screen.data_changed(int)``.

        v2.0.6 fix: same problem-shape as FMNP.  Adjustments and
        voids in the Admin tab routinely target transactions on
        CLOSED market days (coordinators reconciling historical
        receipts).  Pre-fix the auto-sync narrowed scope to the
        currently-open day and silently skipped the closed-day
        mutation.  AdminScreen now passes the affected
        market_day_id through this slot so the sync collects from
        THAT day.

        ``md_id == 0`` falls back to the standard narrow-scope
        behavior (defensive default).
        """
        if md_id and md_id > 0:
            self._trigger_sync(scope_md_id_override=md_id)
        else:
            self._trigger_sync()

    def _on_settings_changed(self):
        """Slot for ``settings_screen.settings_changed()``.

        v2.0.6: settings mutations (vendor adds/edits/toggles, market
        adds/edits/toggles, payment-method adds/edits/toggles, market↔
        vendor / market↔payment-method assignments, vendor↔payment-
        method eligibility, reward rules) affect rows ACROSS markets
        and time:

          * Renaming a vendor changes the Vendor column on Vendor
            Reimbursement (whole-dataset) and on FMNP Entries / per-md
            tabs going forward
          * Changing market name / address can shift the derived
            ``market_code`` (warned in-line) — historical cloud rows
            under the old code may need cleanup
          * Toggling a payment method or changing reward rules
            affects which Generated Rewards rows materialize on
            future transactions

        A narrow per-md auto-sync would miss whole-dataset tabs.  We
        force a full-scope sync so ``delete_stale=True`` drives the
        whole-dataset cleanup and the cloud sheet re-converges on the
        new config.  The 60-second cooldown still applies — rapid
        clicks in Settings won't hammer the Sheets API.
        """
        self._trigger_sync(force=True)

    def _trigger_sync(self, force=False, scope_md_id_override: int | None = None):
        """Execute a background sync. Never blocks the UI.

        When *force* is False (auto-triggers) a 60-second cooldown
        prevents rapid-fire calls that exhaust the Sheets API quota.
        The manual sync button passes *force=True* to bypass this.

        *scope_md_id_override* (v2.0.6): when set, scopes the auto-sync
        to that specific market_day_id instead of looking up the
        currently-open market day.  Used by ``_on_fmnp_entry_saved``
        so entries added to CLOSED market days reach the cloud.
        Ignored when *force=True* (manual full sweeps cover everything).
        """
        if self._sync_thread and self._sync_thread.isRunning():
            logger.info("Sync already in progress — skipping")
            return

        if not force and self._sync_cooldown.isActive():
            # A sync completed recently — defer until cooldown expires
            if not self._sync_deferred.isActive():
                logger.info("Sync cooldown active — deferring auto-sync")
                self._sync_deferred.start()
            return

        try:
            from fam.sync.gsheets import GoogleSheetsBackend
            from fam.sync.manager import SyncManager
            from fam.sync.worker import SyncWorker

            backend = GoogleSheetsBackend()
            if not backend.is_configured():
                return

            manager = SyncManager(backend)
            self._sync_thread = QThread()
            # Data collection now happens on the worker thread (not here).
            #
            # Scoping (v1.9.10 follow-up, 2026-05-01): auto-triggered
            # syncs (any mutation signal — confirm/adjust/void/FMNP/
            # draft/intake) restrict the collection to the **open
            # market day only**.  At year-scale a full collection
            # walks every historical market day (50+ per year ×
            # 8 per-md-collectors = 400 SQL queries) every time a
            # single $5 transaction is confirmed — gross overkill
            # AND a real risk of bumping the Sheets API rate limit.
            #
            # Manual ``Sync to Cloud`` button passes ``force=True``
            # which still triggers a full sweep; same for the
            # market-close auto-sync.  The diff-based upsert in
            # ``upsert_rows`` already prevents wire writes for
            # untouched rows, but the LOCAL collection cost was
            # unscoped — this fixes that.
            #
            # When no market day is open, ``open_md`` is None and we
            # collect everything (the no-open-md state is rare —
            # outside market hours, mutations are unusual).
            #
            # v2.0.6: ``scope_md_id_override`` takes precedence over
            # the open-market-day lookup.  Used by
            # ``_on_fmnp_entry_saved`` so an FMNP entry added to a
            # CLOSED market day still reaches the cloud — the
            # affected day's id is passed in directly.
            scope_md_id = None
            if not force:
                if scope_md_id_override is not None:
                    scope_md_id = scope_md_id_override
                else:
                    from fam.models.market_day import get_open_market_day
                    open_md = get_open_market_day()
                    if open_md:
                        scope_md_id = open_md.get('id')
            self._sync_worker = SyncWorker(
                manager, market_day_id=scope_md_id)
            self._sync_worker.moveToThread(self._sync_thread)
            self._sync_thread.started.connect(self._sync_worker.run)
            self._sync_worker.finished.connect(self._on_sync_finished)
            self._sync_worker.error.connect(self._on_sync_error)
            self._sync_worker.progress.connect(self._on_sync_progress)
            self._sync_worker.finished.connect(self._sync_thread.quit)
            self._sync_worker.error.connect(self._sync_thread.quit)
            self._sync_thread.finished.connect(self._cleanup_sync_thread)

            self._sync_btn.setEnabled(False)
            self._sync_btn.setText("Syncing...")
            self._set_sync_indicator("syncing")
            # v2.0.7: arm the watchdog BEFORE thread.start() so that
            # any exception in start() still lands in the except
            # handler below where we'll cancel the watchdog and
            # restore the button (the button-was-set-but-worker-
            # never-ran failure mode).
            self._sync_watchdog.start()
            self._sync_thread.start()

        except ImportError:
            pass  # gspread not installed
        except Exception:
            logger.exception("Failed to trigger sync")
            # v2.0.7: if we already flipped the button to "Syncing..."
            # before the exception, restore it here — otherwise the
            # button stays disabled forever with no sync running.
            # Idempotent: setting the button to its already-current
            # text/enabled is a no-op.
            self._sync_watchdog.stop()
            self._sync_btn.setEnabled(True)
            self._sync_btn.setText("☁️  Sync to Cloud")
            # Don't change the indicator — leave it on whatever the
            # last successful sync set it to.  We only know that
            # THIS attempt didn't run, not that the previous sync
            # state is invalid.

    def _on_sync_progress(self, message):
        """Show sync progress in the status indicator."""
        self._set_sync_indicator("syncing", message)
        logger.info("Sync progress: %s", message)

    def _on_sync_finished(self, results):
        """Handle sync completion."""
        # v2.0.7: cancel the watchdog — sync completed normally.
        self._sync_watchdog.stop()
        self._sync_btn.setEnabled(True)
        self._sync_btn.setText("☁️  Sync to Cloud")
        failed = [name for name, r in results.items() if not r.success]
        total = sum(r.rows_synced for r in results.values() if r.success)

        # Build tooltip with photo upload details
        tooltip_parts = []
        photo_stats = getattr(self._sync_worker, 'photo_stats', None) if self._sync_worker else None
        if photo_stats:
            if photo_stats.get('error'):
                tooltip_parts.append(f"Photo upload error: {photo_stats['error']}")
            elif photo_stats.get('uploaded', 0) > 0:
                tooltip_parts.append(
                    f"Photos uploaded: {photo_stats['uploaded']}"
                    f" (FMNP: {photo_stats.get('fmnp_uploaded', 0)},"
                    f" Payment: {photo_stats.get('payment_uploaded', 0)})")
            elif photo_stats.get('failed', 0) > 0:
                tooltip_parts.append(
                    f"Photo uploads failed: {photo_stats['failed']}")
            else:
                tooltip_parts.append("No pending photos")
        tooltip_parts.append(f"Sheets: {total} rows synced")
        if failed:
            tooltip_parts.append(f"Sheet errors: {', '.join(failed)}")

        has_photo_issues = photo_stats and (
            photo_stats.get('failed', 0) > 0 or photo_stats.get('error'))

        if failed:
            self._set_sync_indicator(
                "error", f"{len(failed)} tab(s) failed")
        elif has_photo_issues:
            detail = (photo_stats.get('error')
                      or f"{photo_stats['failed']} photo(s) failed")
            self._set_sync_indicator("warning", detail)
        else:
            from fam.utils.app_settings import get_last_sync_at
            last = get_last_sync_at() or ''
            short = last[11:16] if len(last) > 16 else 'now'
            self._set_sync_indicator("online", f"Last sync: {short}")
        self._sync_indicator.setToolTip('\n'.join(tooltip_parts))
        self._sync_cooldown.start()  # prevent rapid-fire auto-syncs
        logger.info("Sync finished: %d tabs, %d failed, photos=%s",
                    len(results), len(failed), photo_stats)

    def _on_sync_error(self, error_msg):
        """Handle sync failure."""
        # v2.0.7: cancel the watchdog — sync ended (with an error,
        # but it ENDED).  The error path also restores the button.
        self._sync_watchdog.stop()
        self._sync_btn.setEnabled(True)
        self._sync_btn.setText("☁️  Sync to Cloud")
        self._set_sync_indicator("error", "Sync failed")
        self._sync_indicator.setToolTip(error_msg)
        self._sync_cooldown.start()  # prevent rapid-fire retries
        logger.error("Sync failed: %s", error_msg)

    def _cleanup_sync_thread(self):
        """Release sync worker and thread after sync completes.

        Called by the thread's ``finished`` signal.  Nulls the Python
        references *after* scheduling C++ deletion so that closeEvent
        never touches a deleted QThread.
        """
        if self._sync_worker:
            self._sync_worker.deleteLater()
            self._sync_worker = None
        if self._sync_thread:
            self._sync_thread.deleteLater()
            self._sync_thread = None

    def _on_sync_watchdog_fired(self):
        """v2.0.7: force-recover from a stuck "Syncing..." button.

        Triggered 5 minutes after ``_trigger_sync`` started a sync
        IF neither ``_on_sync_finished`` nor ``_on_sync_error``
        cancelled the watchdog by then.  In normal operation a sync
        completes in seconds (single market day) to a couple of
        minutes (full sweep on a long-offline laptop with many
        photo uploads), so 5 min is a safe upper bound that rarely
        false-fires.

        The user-reported v2.0.7 incident: the indicator showed
        "Last sync OK" with a stale timestamp (10:35) but the
        button was pinned in disabled "Syncing..." state with no
        actual sync in flight, and there was no recovery path for
        the volunteer (the disabled button can't be clicked).  The
        watchdog ensures the button always returns to a clickable
        state within 5 minutes regardless of which corner case the
        sync state machine got stuck in.
        """
        # If the thread genuinely is still running, don't kill it —
        # extend the watchdog one more round and let it finish.
        # This protects against false fires on a legitimately slow
        # sync (large historical photo upload, slow Sheets API
        # response).  Only force-reset when the thread is gone OR
        # not running.
        thread_alive = (
            self._sync_thread is not None
            and self._sync_thread.isRunning()
        )
        if thread_alive:
            logger.warning(
                "Sync watchdog: thread still running after 5 min — "
                "extending watchdog one more cycle")
            self._sync_watchdog.start()  # restart for another 5 min
            return

        logger.warning(
            "Sync watchdog: 'Syncing...' button stuck with no live "
            "thread — force-resetting UI to recoverable state")
        self._sync_btn.setEnabled(True)
        self._sync_btn.setText("☁️  Sync to Cloud")
        # Indicator: warn rather than error.  We don't know whether
        # the sync actually wrote anything to Sheets/Drive before
        # going silent — calling it "Sync failed" would be a
        # presumption.  "Sync stuck — recovered" is the honest
        # description and prompts the volunteer to click again.
        self._set_sync_indicator(
            "warning", "Sync recovered — please retry")
        self._sync_indicator.setToolTip(
            "The previous sync attempt did not return a "
            "completion signal within 5 minutes.  Click Sync to "
            "Cloud to retry.  (See Help → System Status → Copy "
            "Diagnostic Info if this keeps happening.)")
        # Defensive cleanup: if a zombie thread reference is still
        # set, null it so the next click can proceed past the
        # "isRunning" check.  Don't deleteLater() — the thread
        # might still be running underneath, in which case the
        # main window's closeEvent waits for it (v2.0.3 fix).
        if self._sync_thread and not self._sync_thread.isRunning():
            self._sync_thread = None
            self._sync_worker = None

    # ------------------------------------------------------------------
    # Auto-update check on launch
    # ------------------------------------------------------------------

    # v2.0.1: auto-update notification behavior
    # ──────────────────────────────────────────────────────────────
    # Lifetime of an update notification:
    #   1. ``_auto_check_for_updates`` runs ~5s after launch.
    #   2. It first **replays a cached pending update** — if a prior
    #      check (this session or a previous launch) found a remote
    #      version newer than ours and the user didn't permanently
    #      Ignore it, we show the popup using the cached info
    #      *without making a network call*.  Replay honors a
    #      "remind me later" snooze (set by clicking OK on the
    #      popup).
    #   3. After replay, if the on-the-wire cooldown has expired
    #      (6 hours since the last successful API call), a fresh
    #      check fires in a background QThread.
    #   4. The popup shows two buttons:
    #         OK     → snooze for 6h (``update_remind_after``)
    #         Ignore → silence this exact version forever
    #                 (``update_dismissed_version``)
    #
    # This replaces the pre-v2.0.1 flow where the popup was tied
    # 1-to-1 to the network check.  If you closed the app within
    # the 24-hour cooldown, you'd never see the popup again until
    # a full day passed — easy to miss a release entirely.

    _AUTO_CHECK_COOLDOWN_HOURS = 6
    _SNOOZE_HOURS = 6

    def _auto_check_for_updates(self):
        """Silently check for app updates on launch.

        Two-stage:
          1. Replay any cached pending update immediately (no API call).
          2. If on-the-wire cooldown has expired, run a fresh check.
        """
        try:
            from fam.utils.app_settings import (
                get_update_repo_url, is_auto_update_check_enabled,
                get_last_update_check,
            )

            if not is_auto_update_check_enabled():
                return
            repo_url = get_update_repo_url()
            if not repo_url:
                return

            # Stage 1: replay cached pending update (fast, no network).
            self._maybe_replay_cached_update()

            # Stage 2: rate-limited fresh check.
            from datetime import timedelta
            from fam.utils.timezone import eastern_now, EASTERN
            from datetime import datetime as _dt
            last = get_last_update_check()
            if last:
                try:
                    last_dt = _dt.fromisoformat(last)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=EASTERN)
                    if eastern_now() - last_dt < timedelta(
                            hours=self._AUTO_CHECK_COOLDOWN_HOURS):
                        logger.debug(
                            "Auto-update fresh check skipped (cooldown)")
                        return
                except (ValueError, TypeError):
                    pass

            from fam.update.checker import parse_github_repo_url
            parsed = parse_github_repo_url(repo_url)
            if not parsed:
                return

            owner, repo = parsed

            # Prevent overlapping checks
            if (self._update_check_thread and
                    self._update_check_thread.isRunning()):
                return

            from fam.update.worker import UpdateCheckWorker

            self._update_check_thread = QThread()
            self._update_check_worker = UpdateCheckWorker(
                owner, repo, __version__)
            self._update_check_worker.moveToThread(
                self._update_check_thread)
            self._update_check_thread.started.connect(
                self._update_check_worker.run)
            self._update_check_worker.finished.connect(
                self._on_auto_update_check_finished)
            self._update_check_worker.error.connect(
                lambda msg: logger.info("Auto-update check failed: %s", msg))
            self._update_check_worker.finished.connect(
                self._update_check_thread.quit)
            self._update_check_worker.error.connect(
                self._update_check_thread.quit)

            self._update_check_thread.start()
            logger.info("Auto-update check started")

        except Exception:
            logger.debug("Auto-update check skipped", exc_info=True)

    def _maybe_replay_cached_update(self):
        """Re-show the update-available popup using cached info from a
        prior check.

        Skipped when:
          * No cached version is recorded (never checked, or always failed)
          * Cached version isn't newer than ``__version__`` (we caught up)
          * User permanently Ignored that exact version
          * User clicked OK recently and the snooze hasn't expired

        Network-free: doesn't hit the GitHub API.  This is what makes
        the popup actually surface — without it, the network check is
        silenced by the cooldown for the entire window between releases.
        """
        try:
            from fam.utils.app_settings import get_setting
            from fam.update.checker import compare_versions

            cached_ver = get_setting('update_last_version')
            if not cached_ver:
                return  # never seen any remote version
            if compare_versions(__version__, cached_ver) >= 0:
                return  # local has caught up to or surpassed cache
            dismissed = get_setting('update_dismissed_version')
            if dismissed == cached_ver:
                return  # user clicked Ignore on this exact version
            # Honor "remind me later" snooze (set by clicking OK).
            remind_after = get_setting('update_remind_after')
            if remind_after:
                from datetime import datetime as _dt
                from fam.utils.timezone import eastern_now, EASTERN
                try:
                    after_dt = _dt.fromisoformat(remind_after)
                    if after_dt.tzinfo is None:
                        after_dt = after_dt.replace(tzinfo=EASTERN)
                    if eastern_now() < after_dt:
                        logger.debug(
                            "Auto-update popup snoozed until %s",
                            remind_after)
                        return
                except (ValueError, TypeError):
                    pass
            logger.info("Auto-update replay: cached pending v%s",
                        cached_ver)
            self._show_update_available_popup(cached_ver)
        except Exception:
            logger.debug("Cached-update replay failed", exc_info=True)

    def _on_auto_update_check_finished(self, result: dict):
        """Handle the background update check result."""
        from fam.utils.timezone import eastern_timestamp
        from fam.utils.app_settings import (
            set_last_update_check, set_setting, get_setting,
        )

        set_last_update_check(eastern_timestamp())

        if not result or not result.get('update_available'):
            return

        version = result.get('version', '?')
        set_setting('update_last_version', version)

        # Don't nag if the user permanently Ignored this version, or
        # clicked OK recently and we're still inside the snooze.
        dismissed = get_setting('update_dismissed_version')
        if dismissed == version:
            return
        remind_after = get_setting('update_remind_after')
        if remind_after:
            from datetime import datetime as _dt
            from fam.utils.timezone import eastern_now, EASTERN
            try:
                after_dt = _dt.fromisoformat(remind_after)
                if after_dt.tzinfo is None:
                    after_dt = after_dt.replace(tzinfo=EASTERN)
                if eastern_now() < after_dt:
                    return
            except (ValueError, TypeError):
                pass

        self._show_update_available_popup(version)

    def _show_update_available_popup(self, version: str):
        """Modal "Update Available" dialog with OK (snooze 6h) /
        Ignore (silence forever) buttons.

        Called by both the cache-replay path and the post-network-check
        path so the user-facing message is identical regardless of
        whether the check was fresh or cached.
        """
        from fam.utils.app_settings import set_setting
        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.information(
            self,
            "Update Available",
            f"A new version of FAM Manager is available!\n\n"
            f"Current version: v{__version__}\n"
            f"Latest version:  v{version}\n\n"
            f"Go to Settings → Updates to download and install.\n\n"
            f"Click OK to be reminded later, or Ignore to silence "
            f"this version permanently.",
            QMessageBox.StandardButton.Ok |
            QMessageBox.StandardButton.Ignore,
            QMessageBox.StandardButton.Ok,
        )

        if reply == QMessageBox.StandardButton.Ignore:
            set_setting('update_dismissed_version', version)
            # Clear any prior snooze so a future newer version
            # isn't accidentally suppressed.
            set_setting('update_remind_after', '')
            logger.info(
                "User permanently dismissed update notification "
                "for v%s", version)
        else:
            # OK = snooze for ``_SNOOZE_HOURS`` hours.
            from datetime import timedelta
            from fam.utils.timezone import eastern_now
            until = eastern_now() + timedelta(hours=self._SNOOZE_HOURS)
            set_setting('update_remind_after', until.isoformat())
            logger.info(
                "User snoozed update notification for v%s until %s",
                version, until.isoformat())

    def _on_customer_order_ready(self, order_id):
        """Navigate to payment screen with the customer order."""
        self.payment_screen.load_customer_order(order_id)
        self.stack.setCurrentIndex(2)
        btn = self.nav_group.button(2)
        if btn:
            btn.setChecked(True)

    def _on_return_to_intake(self):
        """Volunteer chose to return to Receipt Intake after confirming
        a payment.  Navigation-only; sync was already triggered by the
        ``payment_confirmed`` signal."""
        self.receipt_intake_screen.start_fresh_after_payment()
        self.stack.setCurrentIndex(1)
        btn = self.nav_group.button(1)
        if btn:
            btn.setChecked(True)

    def _on_draft_saved(self):
        """After draft is saved, return to receipt intake and refresh."""
        self.receipt_intake_screen.start_fresh_after_payment()
        self.stack.setCurrentIndex(1)
        btn = self.nav_group.button(1)
        if btn:
            btn.setChecked(True)

    # ------------------------------------------------------------------
    # About dialog
    # ------------------------------------------------------------------

    def _show_about(self):
        """Show the About dialog."""
        # Local import covers names NOT already at module level
        # (ACCENT_GREEN, HARVEST_GOLD, TEXT_COLOR, BACKGROUND).
        # PRIMARY_GREEN, WHITE, LIGHT_GRAY come from the module-level
        # import (line 18) — re-importing them locally would shadow
        # those module-level bindings and risk UnboundLocalError on
        # any future conditional edit.
        from fam.ui.styles import (
            ACCENT_GREEN, HARVEST_GOLD, TEXT_COLOR, BACKGROUND,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("About FAM Market Manager")
        dlg.setFixedWidth(420)
        dlg.setStyleSheet(f"QDialog {{ background-color: {BACKGROUND}; }}")

        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 16)

        # Title
        title = QLabel("FAM Market Manager")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"""
            font-size: 20px; font-weight: bold;
            color: {PRIMARY_GREEN}; background: transparent;
        """)
        layout.addWidget(title)

        # Version
        version = QLabel(f"Version {__version__}")
        version.setAlignment(Qt.AlignCenter)
        version.setStyleSheet(f"""
            font-size: 13px; color: {TEXT_COLOR}; background: transparent;
        """)
        layout.addWidget(version)

        # Description
        desc = QLabel(
            "A transaction management tool for farmers market "
            "incentive programs. Tracks customer payments, calculates "
            "FAM match amounts, and generates reports for vendor "
            "reimbursement."
        )
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet(f"""
            font-size: 12px; color: {TEXT_COLOR};
            padding: 8px 12px; background: transparent;
        """)
        layout.addWidget(desc)

        # Data location
        from fam.database.connection import get_db_path
        data_folder = os.path.dirname(os.path.abspath(get_db_path()))
        data_label = QLabel(f"Data folder:\n{data_folder}")
        data_label.setWordWrap(True)
        data_label.setAlignment(Qt.AlignCenter)
        data_label.setStyleSheet(f"""
            font-size: 11px; color: {TEXT_COLOR};
            padding: 6px 12px; background: transparent;
        """)
        layout.addWidget(data_label)

        # Open Data Folder button
        open_data_btn = QPushButton("Open Data Folder")
        open_data_btn.setCursor(Qt.PointingHandCursor)
        open_data_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 8px 16px; font-size: 12px; min-height: 0px;
                border: 1px solid {LIGHT_GRAY}; border-radius: 6px;
                background-color: {WHITE}; color: {PRIMARY_GREEN};
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #F0EFEB;
                border-color: {PRIMARY_GREEN};
            }}
        """)
        open_data_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(data_folder)
            )
        )
        layout.addWidget(open_data_btn, alignment=Qt.AlignCenter)

        # GitHub link
        repo_btn = QPushButton("View Source Code on GitHub")
        repo_btn.setCursor(Qt.PointingHandCursor)
        repo_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 8px 16px; font-size: 12px; min-height: 0px;
                border: 1px solid {LIGHT_GRAY}; border-radius: 6px;
                background-color: {WHITE}; color: {ACCENT_GREEN};
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #F0EFEB;
                border-color: {ACCENT_GREEN};
            }}
        """)
        from fam.utils.app_settings import DEFAULT_REPO_URL, get_update_repo_url
        _repo_url = get_update_repo_url() or DEFAULT_REPO_URL
        repo_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(_repo_url))
        )
        layout.addWidget(repo_btn, alignment=Qt.AlignCenter)

        # Cloud links row
        from fam.utils.app_settings import get_setting, get_sync_spreadsheet_id
        _link_btn_style = f"""
            QPushButton {{
                padding: 8px 16px; font-size: 12px; min-height: 0px;
                border: 1px solid {LIGHT_GRAY}; border-radius: 6px;
                background-color: {WHITE}; color: {ACCENT_GREEN};
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #F0EFEB;
                border-color: {ACCENT_GREEN};
            }}
        """

        cloud_row = QHBoxLayout()
        cloud_row.setSpacing(8)

        drive_btn = QPushButton("Open Google Drive")
        drive_btn.setCursor(Qt.PointingHandCursor)
        drive_btn.setStyleSheet(_link_btn_style)
        _folder_id = get_setting('drive_photos_folder_id')
        _drive_url = (f"https://drive.google.com/drive/folders/{_folder_id}"
                      if _folder_id else "https://drive.google.com")
        drive_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(_drive_url))
        )
        cloud_row.addWidget(drive_btn)

        sheets_btn = QPushButton("Open Google Sheets")
        sheets_btn.setCursor(Qt.PointingHandCursor)
        sheets_btn.setStyleSheet(_link_btn_style)
        _sheet_id = get_sync_spreadsheet_id()
        _sheets_url = (f"https://docs.google.com/spreadsheets/d/{_sheet_id}/edit"
                       if _sheet_id else "https://sheets.google.com")
        sheets_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(_sheets_url))
        )
        cloud_row.addWidget(sheets_btn)

        layout.addLayout(cloud_row)

        # Close button
        close_btn = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn.rejected.connect(dlg.close)
        layout.addWidget(close_btn)

        dlg.exec()

    # ------------------------------------------------------------------
    # Tutorial
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # First-run detection (app_settings table)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_first_run() -> bool:
        """Return True if the tutorial has never been shown."""
        from fam.utils.app_settings import get_setting
        val = get_setting('tutorial_shown')
        return val is None or val != '1'

    @staticmethod
    def _mark_tutorial_shown():
        """Record that the tutorial has been shown so it won't auto-launch again."""
        from fam.utils.app_settings import set_setting
        set_setting('tutorial_shown', '1')

    def _maybe_auto_tutorial(self):
        """Launch the tutorial automatically on first run only."""
        if self._is_first_run():
            self.start_tutorial()

    def _check_stale_market_days(self):
        """Auto-close any market days left Open past their own date and
        surface a friendly notification (v1.9.9+).

        The model layer's ``auto_close_stale_market_days`` does the
        actual work and returns descriptors of what was closed; if
        anything was closed we route the user to the Market Day screen
        so they can open today's market.  No-op when nothing was
        stale (the typical case).
        """
        try:
            from fam.models.market_day import auto_close_stale_market_days
            closed = auto_close_stale_market_days()
        except Exception:
            logger.exception("Stale market-day auto-close failed")
            return
        if not closed:
            return

        # Refresh the market-day screen so the closed days reflect
        # in its UI immediately.
        try:
            self.market_day_screen.refresh()
        except Exception:
            pass

        # Build the message.  Cap the per-day list to keep the
        # dialog readable when many days got closed at once.
        from PySide6.QtWidgets import QMessageBox
        lines = [
            "One or more market days were left open past their own "
            "calendar date.  To prevent transactions from being "
            "mis-attributed to a previous date, the system has "
            "automatically closed them:\n"
        ]
        for c in closed[:5]:
            lines.append(
                f"  •  {c['market_name']} — date {c['date']}"
                f" (originally opened by {c.get('opened_by') or 'unknown'})"
            )
        if len(closed) > 5:
            lines.append(f"  …and {len(closed) - 5} more.")
        lines.append(
            "\nTo continue recording transactions, open today's "
            "market day from the Market Day screen.  Tip: close the "
            "market at the end of each day (or use Cloud Sync, which "
            "implies an end-of-day workflow) so this doesn't happen "
            "again."
        )

        QMessageBox.warning(
            self, "Stale Market Day Auto-Closed",
            "\n".join(lines)
        )

        # Route the user to the Market Day screen.
        for idx, btn in enumerate(self.nav_group.buttons()):
            # Market Day is the first nav item (index 0).
            if idx == 0:
                btn.setChecked(True)
                break
        self.stack.setCurrentIndex(0)

    def start_tutorial(self):
        """Launch the guided tutorial overlay."""
        from fam.ui.tutorial_overlay import TutorialOverlay, TUTORIAL_STEPS
        self._end_tutorial()
        self._tutorial_overlay = TutorialOverlay(self, TUTORIAL_STEPS)
        self._tutorial_overlay.finished.connect(self._end_tutorial)
        self._tutorial_overlay.auto_configure_requested.connect(self._auto_configure)

    def _auto_configure(self):
        """Load default seed data when user clicks Yes during tutorial."""
        from fam.database.seed import seed_sample_data
        success = seed_sample_data()
        if success:
            self.settings_screen.refresh()
            self.market_day_screen.refresh()
            logger.info("Auto-configure: default data loaded successfully")
        else:
            logger.warning("Auto-configure: data already exists, skipping")

    def _end_tutorial(self):
        """Remove the tutorial overlay if active."""
        if self._tutorial_overlay:
            self._tutorial_overlay.hide()
            self._tutorial_overlay.deleteLater()
            self._tutorial_overlay = None
            self._mark_tutorial_shown()

    def closeEvent(self, event):  # noqa: N802
        """Clean up timers and background threads before closing.

        Ensures an in-progress sync completes (up to 10 s) so that
        Google Sheets is never left in a partially-written state.
        """
        # Stop all periodic timers
        self._backup_timer.stop()
        self._sync_timer.stop()
        self._sync_cooldown.stop()
        self._sync_deferred.stop()

        # Wait for sync thread to finish (may already be None via _cleanup)
        try:
            if self._sync_thread and self._sync_thread.isRunning():
                logger.info("Waiting for sync to complete before exit…")
                self._sync_thread.quit()
                if not self._sync_thread.wait(10_000):
                    logger.warning("Sync thread did not finish in 10 s — terminating")
                    self._sync_thread.terminate()
                    self._sync_thread.wait(2_000)
        except RuntimeError:
            pass  # C++ object already deleted — thread is done

        # Wait for update check thread
        if self._update_check_thread and self._update_check_thread.isRunning():
            self._update_check_thread.quit()
            self._update_check_thread.wait(3_000)

        # v2.0.3 fix (NEW-CRIT-1): wait for the download thread that
        # lives on SettingsScreen.  Pre-fix the closeEvent walked the
        # main_window's two threads but never touched
        # ``settings_screen._update_dl_thread``.  A volunteer closing
        # the app mid-download (10–60 MB ZIP, 30s–min on conference
        # Wi-Fi) left an orphan QThread.  When the download eventually
        # finished, ``_on_download_finished`` would fire against a
        # destroyed parent widget — uncaught C++ exception or zombie.
        try:
            settings = getattr(self, 'settings_screen', None)
            dl_thread = getattr(settings, '_update_dl_thread', None) if settings else None
            if dl_thread is not None and dl_thread.isRunning():
                logger.info(
                    "Waiting for update download to complete before exit…")
                dl_thread.quit()
                if not dl_thread.wait(10_000):
                    logger.warning(
                        "Update download thread did not finish in 10s — terminating")
                    dl_thread.terminate()
                    dl_thread.wait(2_000)
        except RuntimeError:
            pass  # C++ already deleted — nothing to wait on

        # Close the main-thread database connection
        from fam.database.connection import close_connection
        close_connection()

        logger.info("Application shutdown complete")
        event.accept()

    def eventFilter(self, obj, event):
        """Resize the tutorial overlay when the central widget resizes."""
        if obj is self.centralWidget() and event.type() == QEvent.Resize:
            if self._tutorial_overlay:
                self._tutorial_overlay.refresh_position()
        return super().eventFilter(obj, event)
