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

        self.stack.addWidget(self.market_day_screen)   # 0
        self.stack.addWidget(self.receipt_intake_screen) # 1
        self.stack.addWidget(self.payment_screen)        # 2
        self.stack.addWidget(self.admin_screen)          # 3
        self.stack.addWidget(self.fmnp_screen)           # 4
        self.stack.addWidget(self.reports_screen)        # 5
        self.stack.addWidget(self.settings_screen)       # 6

        # Connect navigation
        self.nav_group.idClicked.connect(self._navigate)

        # Connect signals
        self.market_day_screen.market_day_changed.connect(self._on_market_day_changed)
        self.receipt_intake_screen.customer_order_ready.connect(self._on_customer_order_ready)
        self.payment_screen.payment_confirmed.connect(self._on_payment_confirmed)
        self.payment_screen.draft_saved.connect(self._on_draft_saved)
        self.fmnp_screen.entry_saved.connect(self._trigger_sync)

        # Select first screen
        first_btn = self.nav_group.button(0)
        if first_btn:
            first_btn.setChecked(True)
        self.stack.setCurrentIndex(0)

        # Tutorial overlay (created on demand)
        self._tutorial_overlay = None
        self.centralWidget().installEventFilter(self)

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

        # Auto-update check thread tracking
        self._update_check_thread = None
        self._update_check_worker = None

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

    # ── Cloud sync ─────────────────────────────────────────────

    def _set_sync_indicator(self, state: str, detail: str = ""):
        """Update the online/offline indicator.

        *state*: ``"online"``, ``"offline"``, ``"syncing"``, ``"warning"``,
        or ``"error"``.
        *detail*: optional extra text (e.g. timestamp or error summary).
        """
        if state == "online":
            color = PRIMARY_GREEN
            label = "Online"
        elif state == "syncing":
            color = "#F5A623"  # amber
            label = "Syncing…"
        elif state == "warning":
            color = "#F5A623"  # amber
            label = "Attention"
        elif state == "error":
            color = "#d32f2f"
            label = "Sync Error"
        else:
            color = SUBTITLE_GRAY
            label = "Offline"

        text = f"<span style='color:{color}; font-size:14px;'>●</span>"
        text += f"&nbsp;<span style='color:{color}; font-weight:600; font-size:12px;'>{label}</span>"
        if detail:
            text += f"&nbsp;&nbsp;<span style='color:{SUBTITLE_GRAY}; font-size:11px;'>{detail}</span>"
        self._sync_indicator.setText(text)
        self._sync_indicator.setStyleSheet("background: transparent; padding: 0 8px;")

    def _update_sync_visibility(self):
        """Show/hide the sync button + indicator based on configuration."""
        try:
            from fam.utils.app_settings import is_sync_configured, get_last_sync_at
            configured = is_sync_configured()
            self._sync_btn.setVisible(configured)
            self._sync_indicator.setVisible(configured)
            if configured:
                last = get_last_sync_at()
                if last:
                    short = last[11:16] if len(last) > 16 else last
                    self._set_sync_indicator("online", f"Last sync: {short}")
                else:
                    self._set_sync_indicator("offline")
            else:
                self._set_sync_indicator("offline")
        except Exception:
            pass

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

    def _trigger_sync(self, force=False):
        """Execute a background sync. Never blocks the UI.

        When *force* is False (auto-triggers) a 60-second cooldown
        prevents rapid-fire calls that exhaust the Sheets API quota.
        The manual sync button passes *force=True* to bypass this.
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
            # Data collection now happens on the worker thread (not here)
            self._sync_worker = SyncWorker(manager)
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
            self._sync_thread.start()

        except ImportError:
            pass  # gspread not installed
        except Exception:
            logger.exception("Failed to trigger sync")

    def _on_sync_progress(self, message):
        """Show sync progress in the status indicator."""
        self._set_sync_indicator("syncing", message)
        logger.info("Sync progress: %s", message)

    def _on_sync_finished(self, results):
        """Handle sync completion."""
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

    # ------------------------------------------------------------------
    # Auto-update check on launch
    # ------------------------------------------------------------------

    def _auto_check_for_updates(self):
        """Silently check for app updates on launch (once per 24 hours)."""
        try:
            from fam.utils.app_settings import (
                get_update_repo_url, is_auto_update_check_enabled,
                get_last_update_check, get_setting, set_setting,
                set_last_update_check,
            )

            if not is_auto_update_check_enabled():
                return
            repo_url = get_update_repo_url()
            if not repo_url:
                return

            # Rate limit: max once per 24 hours
            last = get_last_update_check()
            if last:
                from datetime import datetime, timedelta
                from fam.utils.timezone import eastern_now, EASTERN
                try:
                    last_dt = datetime.fromisoformat(last)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=EASTERN)
                    if eastern_now() - last_dt < timedelta(hours=24):
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

            from fam import __version__
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

        # Don't nag if the user dismissed this version
        dismissed = get_setting('update_dismissed_version')
        if dismissed == version:
            return

        from fam import __version__
        reply = QMessageBox.information(
            self,
            "Update Available",
            f"A new version of FAM Manager is available!\n\n"
            f"Current version: v{__version__}\n"
            f"Latest version:  v{version}\n\n"
            f"Go to Settings → Updates to download and install.",
            QMessageBox.StandardButton.Ok |
            QMessageBox.StandardButton.Ignore,
            QMessageBox.StandardButton.Ok,
        )

        if reply == QMessageBox.StandardButton.Ignore:
            set_setting('update_dismissed_version', version)
            logger.info("User dismissed update notification for v%s", version)

    def _on_customer_order_ready(self, order_id):
        """Navigate to payment screen with the customer order."""
        self.payment_screen.load_customer_order(order_id)
        self.stack.setCurrentIndex(2)
        btn = self.nav_group.button(2)
        if btn:
            btn.setChecked(True)

    def _on_payment_confirmed(self):
        """After payment is confirmed, go back to receipt intake for next customer."""
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
        from fam.ui.styles import (
            PRIMARY_GREEN, ACCENT_GREEN, HARVEST_GOLD,
            WHITE, TEXT_COLOR, BACKGROUND, LIGHT_GRAY
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
