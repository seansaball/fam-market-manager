"""Screen D: External Payments Entry (formerly FMNP Entry).

v2.1.0 (ENH-002): generalized from FMNP-only to any payment method
with ``external_matching_accepted`` ON.  The selected method drives
the denomination (slot count, whole-multiple validation, G2), the
photo requirement, and the payout preview (G1) — all inherited from
the method's existing Settings fields; entries snapshot that config
at save time (see fam/models/fmnp.py).  The class name FMNPScreen
and the ``entry_saved`` signal are kept — both are pinned by the
sync mutation-wiring tests and main_window plumbing.
"""

import logging
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QTextEdit,
    QFileDialog, QScrollArea
)
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices

from fam.models.market_day import get_all_market_days
from fam.models.vendor import get_all_vendors, get_vendors_for_market
from fam.models.fmnp import (
    get_fmnp_entries, create_fmnp_entry, update_fmnp_entry, delete_fmnp_entry,
    get_fmnp_entry_by_id
)
from fam.utils.export import write_ledger_backup
from fam.utils.money import dollars_to_cents, cents_to_dollars, format_dollars
from fam.ui.styles import WHITE, LIGHT_GRAY, ERROR_COLOR, PRIMARY_GREEN, ERROR_BG, SUBTITLE_GRAY, ACCENT_GREEN, HARVEST_GOLD, WARNING_BG
from fam.ui.helpers import (
    make_field_label, make_item, make_section_label, make_action_btn,
    configure_table, NoScrollDoubleSpinBox, NoScrollSpinBox, NoScrollComboBox,
    DateRangeWidget,
)

logger = logging.getLogger('fam.ui.fmnp_screen')


# Hard cap on the number of photo upload slots the screen will render.
# A typo (e.g. $4533 with $5 denomination = 906 slots) used to lock up
# the UI thread because each slot creates ~5 widgets — 906 slots ×
# 5 widgets = ~4500 widgets, which Qt renders synchronously.  The
# real-world maximum a single FMNP entry should ever represent is
# well under this cap; batches larger than this should be split into
# multiple entries so the photos remain manageable.
MAX_PHOTO_SLOTS = 50


class FMNPScreen(QWidget):
    """FMNP Entry screen."""

    # v2.0.6 fix: emit the saved entry's market_day_id so the sync
    # handler can scope to THAT specific day — not the currently-
    # open market day.  Coordinators frequently add FMNP entries
    # to closed market days after the fact (paper checks delivered
    # later, end-of-month batch entry).  Pre-fix, the sync narrowed
    # scope to the open market day and silently skipped the closed
    # day's new entries.  Now main_window listens to this signal
    # and passes the emitted md_id to the sync worker so the
    # affected day is collected, regardless of open / closed state.
    entry_saved = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editing_id = None
        # v2.1.0 (ENH-002): while editing, the entry's own config
        # snapshots ((match%, cashes_original) tuple or None) and
        # denomination snapshot drive payout + validation; the
        # method dropdown is locked.  _temp_method_item marks a
        # dropdown item added just to display a no-longer-external
        # method during an edit.
        self._editing_snapshots = None
        self._editing_denom_snapshot = None
        self._temp_method_item = False
        # Multi-photo slots: list of dicts with 'source_path' and 'stored_path'
        self._photo_slots: list[dict] = []
        self._photo_slot_widgets: list[dict] = []  # UI widgets per slot
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        title = QLabel("External Payments Entry")
        title.setObjectName("screen_title")
        layout.addWidget(title)

        # Form
        form_frame = QFrame()
        self.form_frame = form_frame  # expose for tutorial hints
        form_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {WHITE};
                border: 1px solid #E2E2E2;
                border-radius: 8px;
                padding: 6px 10px;
            }}
        """)
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

        # ── Payment method (v2.1.0 / ENH-002) ─────────────────
        # External-enabled methods only (Settings → Payment
        # Methods → External Matching).  Defaults to FMNP — the
        # most common external method, and the continuity choice
        # for existing FMNP users.
        row1b = QHBoxLayout()
        row1b.addWidget(make_field_label("Payment Method"))
        self.method_combo = NoScrollComboBox()
        self.method_combo.setMinimumWidth(180)
        self.method_combo.currentIndexChanged.connect(
            self._on_method_changed)
        row1b.addWidget(self.method_combo)
        # Hint when NO method is external-enabled (the screen is
        # unusable until Settings opts one in).
        self.no_methods_hint = QLabel(
            "No payment methods are enabled for external matching "
            "— enable one in Settings → Payment Methods.")
        self.no_methods_hint.setStyleSheet(f"""
            color: {HARVEST_GOLD};
            background-color: {WARNING_BG};
            border: 1px solid {HARVEST_GOLD};
            border-radius: 6px;
            padding: 6px 10px;
            font-weight: bold;
            font-size: 12px;
        """)
        self.no_methods_hint.setVisible(False)
        row1b.addWidget(self.no_methods_hint)
        row1b.addStretch()
        form_layout.addLayout(row1b)

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
        self.amount_spin.setPrefix("$ ")
        self.amount_spin.setSingleStep(1.00)
        row2.addWidget(self.amount_spin)

        # Denomination hint (configured dynamically from FMNP payment method)
        self.denom_hint = QLabel("")
        self.denom_hint.setStyleSheet(f"font-weight: bold; color: {ACCENT_GREEN}; font-size: 13px;")
        self.denom_hint.setVisible(False)
        row2.addWidget(self.denom_hint)

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
        row3.addStretch()
        form_layout.addLayout(row3)

        # Row 4: Dynamic check photo slots (rebuilt when amount/denomination changes)
        photo_header = QHBoxLayout()
        photo_header.addWidget(make_field_label("Check Photos"))
        photo_header.addStretch()
        form_layout.addLayout(photo_header)

        # Cap warning — shown when amount/denomination would generate
        # more than ``MAX_PHOTO_SLOTS`` photo rows.  Tells the user
        # exactly what's happening and how to recover.  Hidden when
        # the count is within the cap.
        self.photo_cap_warning = QLabel("")
        self.photo_cap_warning.setWordWrap(True)
        self.photo_cap_warning.setStyleSheet(f"""
            color: {ERROR_COLOR}; font-weight: bold; font-size: 12px;
            background-color: {ERROR_BG};
            border: 1px solid {ERROR_COLOR};
            border-radius: 6px;
            padding: 6px 10px;
        """)
        self.photo_cap_warning.setVisible(False)
        form_layout.addWidget(self.photo_cap_warning)

        # Scrollable container for photo slots — fixed height fits ~3 rows
        self._photo_scroll_area = QScrollArea()
        self._photo_scroll_area.setWidgetResizable(True)
        self._photo_scroll_area.setFrameShape(QFrame.NoFrame)
        self._photo_scroll_area.setFixedHeight(160)
        self._photo_scroll_area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        photo_scroll_widget = QWidget()
        photo_scroll_widget.setStyleSheet("background: transparent;")
        self.photo_container = QVBoxLayout(photo_scroll_widget)
        self.photo_container.setSpacing(4)
        self.photo_container.setContentsMargins(0, 0, 0, 0)
        self._photo_scroll_area.setWidget(photo_scroll_widget)
        form_layout.addWidget(self._photo_scroll_area)

        # Connect amount changes to rebuild photo slots
        self.amount_spin.valueChanged.connect(self._on_amount_changed)

        # ── Payout preview (G1, v2.1.0 / ENH-002) ─────────────
        # Live money line driven by the SELECTED method's match %
        # and cashes-original config — the same values the entry
        # will snapshot at save time.
        self.payout_preview = QLabel("")
        self.payout_preview.setWordWrap(True)
        self.payout_preview.setStyleSheet(f"""
            color: {PRIMARY_GREEN}; font-weight: bold;
            font-size: 13px; padding: 2px;
        """)
        self.payout_preview.setVisible(False)
        form_layout.addWidget(self.payout_preview)
        self.vendor_combo.currentIndexChanged.connect(
            self._update_payout_preview)

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
        self.save_btn = QPushButton("Add Entry")
        self.save_btn.setObjectName("primary_btn")
        self.save_btn.clicked.connect(self._save_entry)
        btn_row.addWidget(self.save_btn)

        self.cancel_edit_btn = QPushButton("Cancel Edit")
        self.cancel_edit_btn.setObjectName("secondary_btn")
        self.cancel_edit_btn.clicked.connect(self._cancel_edit)
        self.cancel_edit_btn.setVisible(False)
        btn_row.addWidget(self.cancel_edit_btn)

        # v2.0.7+ (user-reported 2026-05-07): visible hint when
        # the Save button is disabled because "All Market Days"
        # is selected.  Tooltips alone aren't discoverable —
        # volunteers might not realise they need to pick a
        # specific market day before adding a new entry.  The
        # hint sits inline next to the disabled button so it's
        # impossible to miss.
        self.pick_md_hint_label = QLabel(
            "← Pick a specific market day above to add a new entry")
        self.pick_md_hint_label.setStyleSheet(f"""
            color: {HARVEST_GOLD};
            background-color: {WARNING_BG};
            border: 1px solid {HARVEST_GOLD};
            border-radius: 6px;
            padding: 6px 10px;
            font-weight: bold;
            font-size: 12px;
        """)
        self.pick_md_hint_label.setVisible(False)
        btn_row.addWidget(self.pick_md_hint_label)

        btn_row.addStretch()
        form_layout.addLayout(btn_row)

        layout.addWidget(form_frame)

        # ── Filter row above the entries table (v2.0.7+) ─────────
        # Date range filter mirrors the Reports / Adjustments
        # screens so volunteers searching for a historical FMNP
        # entry by date span have a consistent UX.  The market
        # filter is the existing dropdown above (re-purposed —
        # see ``_load_market_days`` for the "All Market Days"
        # sentinel).
        layout.addWidget(make_section_label(
            "External Payment Entries (filter below)"))
        filter_row = QHBoxLayout()
        filter_row.addWidget(make_field_label("Date range"))
        self.date_range = DateRangeWidget()
        self.date_range.setMinimumWidth(200)
        self.date_range.setToolTip(
            "Filter entries by the market day's calendar date.  "
            "Leave as 'All Dates' to show every entry in the "
            "selected market (or every entry across all markets "
            "when 'All Market Days' is selected above)."
        )
        self.date_range.range_changed.connect(self._load_entries)
        filter_row.addWidget(self.date_range)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Entries table
        # v2.0.7+: added "Market Day" column so volunteers can
        # identify which market each entry belongs to when the
        # "All Market Days" filter is active and the table mixes
        # entries from multiple market days.
        # v2.1.0 (ENH-002): added "Method" (entry's snapshot — the
        # table mixes methods now) and "FAM Owes" (payout derived
        # from the entry's OWN config snapshots, never the method's
        # current settings).
        self.table = QTableWidget()
        self.table.setColumnCount(12)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Market Day", "Method", "Vendor", "Amount",
             "FAM Owes", "Count", "Entered By", "Notes", "Photo",
             "Status", "Actions"]
        )
        configure_table(self.table, actions_col=11, actions_width=160)
        layout.addWidget(self.table)

    def refresh(self):
        # v2.0.2 fix (UF-H5): don't blow away mid-edit form state.
        # Pre-fix every refresh / data_changed signal called
        # ``_cancel_edit()`` unconditionally — silently discarding
        # an in-progress entry (amount, vendor, notes, attached
        # photos) when the volunteer briefly navigated away to
        # check Reports.  Now we only cancel if there's nothing in
        # progress to lose.
        self._load_methods()
        self._configure_selected_method()
        self._load_market_days()
        self._load_vendors()
        self._load_entries()
        if not self._has_in_progress_edit():
            self._cancel_edit()

    # ── Method selection (v2.1.0 / ENH-002) ───────────────────

    def _load_methods(self):
        """Populate the method dropdown with external-enabled
        methods, preserving the current selection across refresh.
        Default: FMNP when external-enabled (the common case and
        the continuity choice), else the first method by
        sort_order."""
        from fam.models.payment_method import (
            get_external_payment_methods)
        if getattr(self, '_editing_id', None):
            # The dropdown is LOCKED to the entry's method during an
            # edit (possibly showing a temp item for a no-longer-
            # external method) — rebuilding would clobber that.
            return
        previous = (self.method_combo.currentData()
                    if self.method_combo.count() else None)
        self._external_methods = get_external_payment_methods()
        self.method_combo.blockSignals(True)
        try:
            self.method_combo.clear()
            for m in self._external_methods:
                self.method_combo.addItem(m['name'], userData=m['id'])
            if self.method_combo.count():
                target = -1
                if previous is not None:
                    target = self.method_combo.findData(previous)
                if target < 0:
                    target = self.method_combo.findText('FMNP')
                if target < 0:
                    target = 0
                self.method_combo.setCurrentIndex(target)
        finally:
            self.method_combo.blockSignals(False)
        if hasattr(self, 'no_methods_hint'):
            self.no_methods_hint.setVisible(
                self.method_combo.count() == 0)
        self._refresh_save_button_state()

    def _selected_method(self):
        """The selected method's row dict, or None."""
        mid = (self.method_combo.currentData()
               if hasattr(self, 'method_combo') else None)
        for m in getattr(self, '_external_methods', []):
            if m['id'] == mid:
                return m
        return None

    def _on_method_changed(self):
        self._configure_selected_method()
        self._on_amount_changed()
        self._update_payout_preview()
        self._refresh_save_button_state()

    def _configure_selected_method(self):
        """Configure denomination + photo requirement from the
        SELECTED method (pre-v2.1.0 this was hardwired to FMNP).

        ``self._fmnp_denomination`` keeps its historical name (it
        now means "the denomination driving the form": several
        tests set it directly) and holds, in priority order:
          1. the entry's own ``denomination_snapshot`` while
             EDITING an entry that has one — so a legacy entry
             still validates against the denomination it was
             recorded under, not today's setting;
          2. the selected method's current denomination.
        """
        method = self._selected_method()
        self._fmnp_denomination = None
        self._photo_required = 'Off'
        denom_cents = None
        if method:
            denom_cents = method.get('denomination')
            self._photo_required = method.get('photo_required') or 'Off'
        if (getattr(self, '_editing_id', None)
                and getattr(self, '_editing_denom_snapshot', None)):
            denom_cents = self._editing_denom_snapshot
        if denom_cents:
            self._fmnp_denomination = denom_cents  # DB stores cents
            denom_dollars = cents_to_dollars(denom_cents)
            self.amount_spin.setSingleStep(denom_dollars)
            self.denom_hint.setText(
                f"({format_dollars(denom_cents)} increments)")
            self.denom_hint.setVisible(True)
        else:
            self.amount_spin.setSingleStep(1.00)
            self.denom_hint.setVisible(False)

    def _update_payout_preview(self):
        """G1 live preview: what FAM will owe the vendor for the
        amount currently typed, under the selected method's
        config."""
        if not hasattr(self, 'payout_preview'):
            return
        method = self._selected_method()
        amount_cents = dollars_to_cents(self.amount_spin.value())
        if not method or amount_cents <= 0:
            self.payout_preview.setVisible(False)
            return
        from fam.utils.external_payout import (
            compute_external_payout_cents, reimbursement_basis)
        payout = compute_external_payout_cents(
            amount_cents, method['match_percent'],
            bool(method.get('vendor_cashes_original')))
        basis = reimbursement_basis(
            amount_cents, method['match_percent'],
            bool(method.get('vendor_cashes_original')))
        vendor = self.vendor_combo.currentText() or "the vendor"
        self.payout_preview.setText(
            f"FAM will owe {vendor} {format_dollars(payout)} — {basis}")
        self.payout_preview.setVisible(True)

    def _load_market_days(self):
        # v2.0.2 fix (UF-H5): preserve the volunteer's selected
        # market day across refresh.  Capture the current data
        # value, rebuild, then restore the index if the same
        # market day still exists.
        #
        # v2.0.7+ (user-reported 2026-05-07): "All Market Days" is
        # now the first option (sentinel ``None``) and the default
        # on first load.  Volunteers landing on the FMNP Check
        # Tracking page see EVERY entry across the season at a
        # glance instead of having to scroll past historical
        # market days to find what they're looking for.  When
        # "All Market Days" is selected, the Save button is
        # disabled (tooltip explains why) — entering a NEW FMNP
        # entry still requires picking a specific market day so
        # the entry is correctly attributed.
        #
        # The legacy "default to the currently-open / most-recent
        # market day" behaviour is retained as the
        # ``_pick_default_market_day_id`` helper but no longer
        # auto-fires; users who want that view click the dropdown
        # and pick a specific day.
        previous_data = self._current_md_combo_data()
        self._market_days_data = get_all_market_days()
        self.md_combo.blockSignals(True)
        try:
            self.md_combo.clear()
            # Sentinel: userData=None means "All Market Days".
            self.md_combo.addItem("All Market Days", userData=None)
            for d in self._market_days_data:
                status = "[OPEN]" if d['status'] == 'Open' else "[Closed]"
                self.md_combo.addItem(
                    f"{d['market_name']} - {d['date']} {status}",
                    userData=d['id']
                )
            if previous_data is not None:
                idx = self.md_combo.findData(previous_data)
                if idx >= 0:
                    self.md_combo.setCurrentIndex(idx)
                # else: previous market day was deleted; fall back
                # to "All Market Days" (idx 0, already current).
            elif self._previous_was_explicit_all:
                # Volunteer was on "All Market Days" before the
                # refresh; preserve that.
                self.md_combo.setCurrentIndex(0)
            else:
                # First load: default to "All Market Days" (idx 0).
                # The user can pick a specific day to enter new
                # FMNP checks against.
                self.md_combo.setCurrentIndex(0)
        finally:
            self.md_combo.blockSignals(False)
        # Sync save-button enabled state with the new selection.
        self._refresh_save_button_state()

    def _current_md_combo_data(self):
        """Return the currently-selected market_day_id, or
        ``None`` for either no-selection or the "All Market Days"
        sentinel.  Helper to disambiguate "no current selection"
        from "All Market Days selected" — both surface as ``None``
        from ``currentData()`` but only the latter is a deliberate
        user choice we should preserve across refresh."""
        if self.md_combo.count() == 0:
            return None
        return self.md_combo.currentData()

    @property
    def _previous_was_explicit_all(self) -> bool:
        """True when the dropdown was previously showing the
        'All Market Days' sentinel.  Stored as an attribute so
        we can preserve that selection across ``_load_market_days``
        rebuilds (which clear the combo)."""
        return getattr(self, '_md_was_all', False)

    def _pick_default_market_day_id(self) -> int | None:
        """Return the market_day_id to default-select on first load.

        Preference order:
          1. The currently-OPEN market day (only one can be open at
             a time; coordinators usually want to log against today's
             active market first).
          2. The most recent market day by date (handles after-the-
             fact entry on closed days when no market is open right
             now).
          3. None when no market days exist at all.
        """
        if not self._market_days_data:
            return None
        # 1. Open market day, if any
        for d in self._market_days_data:
            if d.get('status') == 'Open':
                return d['id']
        # 2. Most recent by date.  ``get_all_market_days`` ordering
        # depends on the model — sort defensively here so we always
        # land on the latest regardless of upstream order.
        latest = max(
            self._market_days_data,
            key=lambda d: (d.get('date') or '', d.get('id') or 0))
        return latest['id']

    def _has_in_progress_edit(self) -> bool:
        """Return True if the form has unsaved user input that a
        ``_cancel_edit()`` would silently discard.  Used by
        ``refresh()`` to avoid stomping mid-entry state when an
        external ``data_changed`` signal triggers a refresh."""
        # In edit mode (existing entry being modified) — always
        # consider it in-progress so the manager doesn't lose their
        # work.
        if getattr(self, '_editing_id', None):
            return True
        # New-entry mode — consider any non-default field "in progress."
        try:
            if self.amount_spin.value() > 0:
                return True
        except Exception:
            pass
        try:
            if self.notes_input.toPlainText().strip():
                return True
        except Exception:
            pass
        # Photo slots populated?  ``_photo_slots`` is a list of
        # dicts with keys ``source_path`` (newly attached file) and
        # ``stored_path`` (already-stored file when editing).
        try:
            for slot in getattr(self, '_photo_slots', []):
                if isinstance(slot, dict) and (
                        slot.get('source_path') or slot.get('stored_path')):
                    return True
        except Exception:
            pass
        return False

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
        """When market day changes, reload vendors filtered by that market.

        v2.0.7+: also tracks whether the volunteer is in
        "All Market Days" browse mode and refreshes the Save
        button enable/disable accordingly."""
        self._md_was_all = self.md_combo.currentData() is None
        self._refresh_save_button_state()
        self._load_vendors()
        self._load_entries()

    def _refresh_save_button_state(self):
        """Enable/disable the Save button based on whether a
        specific market day is selected.

        v2.0.7+: when "All Market Days" is selected the form
        is in browse mode and Save is disabled (you can't
        attribute an FMNP entry to "all markets").  Tooltip
        explains the constraint, and a visible hint label sits
        inline next to the button so volunteers don't need to
        hover to discover why it's greyed out."""
        if not hasattr(self, 'save_btn'):
            return  # called during _build_ui before save_btn exists
        md_selected = self.md_combo.currentData() is not None
        # v2.1.0: also requires an external-enabled method to exist.
        has_method = (not hasattr(self, 'method_combo')
                      or self.method_combo.count() > 0)
        self.save_btn.setEnabled(md_selected and has_method)
        if md_selected:
            self.save_btn.setToolTip(
                "" if has_method else
                "No payment methods are enabled for external "
                "matching — enable one in Settings → Payment "
                "Methods.")
        else:
            self.save_btn.setToolTip(
                "Pick a specific market day above before adding "
                "an entry.  'All Market Days' is a browse-"
                "only filter for searching existing entries.")
        # Show the inline hint label only when the button is
        # disabled.  The tooltip stays as a fallback for
        # accessibility tools / hover discovery.
        if hasattr(self, 'pick_md_hint_label'):
            self.pick_md_hint_label.setVisible(not md_selected)

    def _load_entries(self):
        # Refresh method settings (denomination + photo requirement)
        # in case they were changed in Settings since the screen was
        # created.
        self._load_methods()
        self._configure_selected_method()

        # v2.0.7+: when md_id is None the "All Market Days"
        # sentinel is selected; pass through to the model layer
        # which now supports cross-market-day queries.  The
        # date_range filter further narrows the result by the
        # market day's calendar date (mirrors the Reports +
        # Adjustments date-range UX).
        md_id = self.md_combo.currentData()
        date_from, date_to = (
            self.date_range.get_date_range()
            if hasattr(self, 'date_range') else (None, None))

        entries = get_fmnp_entries(
            market_day_id=md_id, active_only=False,
            date_from=date_from, date_to=date_to)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(entries))

        grey = QColor(SUBTITLE_GRAY)

        from fam.utils.photo_paths import parse_photo_paths

        for i, e in enumerate(entries):
            is_deleted = e.get('status') == 'Deleted'
            photo_paths = parse_photo_paths(e.get('photo_path'))
            photo_count = len(photo_paths)
            has_photo = photo_count > 0

            self.table.setItem(i, 0, make_item(str(e['id']), e['id']))
            # v2.0.7+: Market Day column at index 1.  Critical
            # for "All Market Days" mode where rows from
            # multiple markets share the table — without this
            # column the volunteer can't tell which market each
            # entry belongs to.  Sort key uses the raw ISO date
            # so column-sort is chronological.
            md_label_parts = [
                e.get('market_day_date', ''),
                f"({e.get('market_name', '')})"
                if e.get('market_name') else '']
            md_label = ' '.join(p for p in md_label_parts if p)
            self.table.setItem(
                i, 1, make_item(md_label, e.get('market_day_date', '')))
            # v2.1.0: Method column — the entry's name SNAPSHOT, so
            # a later method rename never re-labels history.
            self.table.setItem(
                i, 2, make_item(e.get('method_name_snapshot') or 'FMNP'))
            self.table.setItem(i, 3, make_item(e['vendor_name']))
            amount_dollars = cents_to_dollars(e['amount'])
            self.table.setItem(i, 4, make_item(f"${amount_dollars:.2f}", amount_dollars))

            # v2.1.0: FAM Owes — derived from the entry's OWN
            # config snapshots (EP1: payout is never stored, and
            # never read from current settings).
            from fam.utils.external_payout import (
                compute_external_payout_cents)
            match_snap = e.get('match_percent_snapshot')
            payout_cents = compute_external_payout_cents(
                e['amount'],
                match_snap if match_snap is not None else 100.0,
                bool(e.get('vendor_cashes_original_snapshot', 1)))
            payout_dollars = cents_to_dollars(payout_cents)
            self.table.setItem(
                i, 5, make_item(f"${payout_dollars:.2f}", payout_dollars))

            self.table.setItem(i, 6, make_item(str(e.get('check_count') or ''),
                                                e.get('check_count') or 0))
            self.table.setItem(i, 7, make_item(e['entered_by']))
            self.table.setItem(i, 8, make_item(e.get('notes') or ''))

            # Photo indicator column — show count when multiple
            if photo_count > 1:
                photo_text = f"📷 {photo_count}"
            elif photo_count == 1:
                photo_text = "📷"
            else:
                photo_text = "—"
            self.table.setItem(i, 9, make_item(photo_text))

            self.table.setItem(i, 10, make_item(e.get('status', 'Active')))

            # Grey out all cells for deleted entries
            if is_deleted:
                for col in range(11):
                    item = self.table.item(i, col)
                    if item:
                        item.setForeground(grey)

            # Action buttons (hidden for deleted entries)
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(3)

            if not is_deleted:
                entry_id = e['id']
                edit_btn = make_action_btn("Edit", 45)
                edit_btn.clicked.connect(lambda checked, eid=entry_id: self._edit_entry(eid))
                action_layout.addWidget(edit_btn)

                if has_photo:
                    view_btn = make_action_btn("View", 45)
                    view_btn.clicked.connect(
                        lambda checked, path=e['photo_path']: self._view_photo(path))
                    action_layout.addWidget(view_btn)

                del_btn = make_action_btn("Delete", 55, danger=True)
                del_btn.clicked.connect(lambda checked, eid=entry_id: self._delete_entry(eid))
                action_layout.addWidget(del_btn)

            self.table.setCellWidget(i, 11, action_widget)
            self.table.setRowHeight(i, 42)

        self.table.setSortingEnabled(True)

    # ── Photo attachment ─────────────────────────────────────

    def _get_expected_photo_count(self) -> int:
        """Return the number of photo slots based on amount /
        denomination, hard-capped at ``MAX_PHOTO_SLOTS`` so a typo
        in the amount field can't render thousands of widgets and
        freeze the UI thread.

        The capped value is the number of widgets RENDERED.  The
        true check count (still accessible via ``_get_uncapped_check_count``)
        is preserved for the save record so the entry's check_count
        column stays accurate even when the photo UI is truncated.
        """
        return min(MAX_PHOTO_SLOTS, self._get_uncapped_check_count())

    def _get_uncapped_check_count(self) -> int:
        """Return the un-capped expected check count from amount /
        denomination.  Used for the warning label and the saved
        ``check_count`` value."""
        amount_cents = dollars_to_cents(self.amount_spin.value())
        if amount_cents <= 0:
            return 1
        if self._fmnp_denomination and self._fmnp_denomination > 0:
            return max(1, int(amount_cents / self._fmnp_denomination))
        return 1

    def _on_amount_changed(self):
        """Rebuild photo slots when amount changes (affects check count)."""
        expected = self._get_expected_photo_count()
        uncapped = self._get_uncapped_check_count()
        if len(self._photo_slots) != expected:
            self._rebuild_photo_slots(expected)
        # Surface a friendly warning when the amount would have
        # produced more rows than the cap allows.  The warning
        # explains the cap and recommends splitting the entry.
        self._update_payout_preview()
        if hasattr(self, 'photo_cap_warning'):
            if uncapped > MAX_PHOTO_SLOTS:
                self.photo_cap_warning.setText(
                    f"⚠  This amount represents {uncapped} items, but "
                    f"only {MAX_PHOTO_SLOTS} photo upload rows will be "
                    f"shown to keep the screen responsive.  If you "
                    f"need photos for every item, split this into "
                    f"multiple smaller entries.  The saved "
                    f"count will still record the full "
                    f"{uncapped}."
                )
                self.photo_cap_warning.setVisible(True)
            else:
                self.photo_cap_warning.setVisible(False)

    def _rebuild_photo_slots(self, count: int):
        """Rebuild the dynamic photo slot UI to match the expected count."""
        # Preserve existing data
        old_slots = list(self._photo_slots)

        # Clear old widgets from container
        for w in self._photo_slot_widgets:
            widget = w.get('frame')
            if widget:
                self.photo_container.removeWidget(widget)
                widget.deleteLater()
        self._photo_slot_widgets.clear()

        # Build new slots, preserving data from old slots where possible
        self._photo_slots = []
        for i in range(count):
            if i < len(old_slots):
                slot = old_slots[i]
            else:
                slot = {'source_path': None, 'stored_path': None}
            self._photo_slots.append(slot)

            # Create UI for this slot
            frame = QFrame()
            row = QHBoxLayout(frame)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)

            method = self._selected_method()
            unit = "Check" if (method or {}).get('name', 'FMNP') == 'FMNP' else "Item"
            label_text = f"{unit} {i + 1}:" if count > 1 else "Photo:"
            label = QLabel(label_text)
            label.setMinimumWidth(60)
            label.setStyleSheet(f"font-size: 12px; color: {SUBTITLE_GRAY};")
            row.addWidget(label)

            attach_btn = QPushButton("Attach...")
            attach_btn.setObjectName("secondary_btn")
            attach_btn.setMinimumWidth(80)
            attach_btn.clicked.connect(
                lambda checked, idx=i: self._select_photo_at(idx))
            row.addWidget(attach_btn)

            file_label = QLabel("")
            file_label.setStyleSheet(f"font-size: 12px; padding: 2px;")
            row.addWidget(file_label)

            clear_btn = QPushButton("Clear")
            clear_btn.setObjectName("secondary_btn")
            clear_btn.setMinimumWidth(60)
            clear_btn.setVisible(False)
            clear_btn.clicked.connect(
                lambda checked, idx=i: self._clear_photo_at(idx))
            row.addWidget(clear_btn)

            row.addStretch()

            # Set initial display state
            self._update_slot_display(i, file_label, clear_btn, slot)

            self.photo_container.addWidget(frame)
            self._photo_slot_widgets.append({
                'frame': frame,
                'file_label': file_label,
                'clear_btn': clear_btn,
            })

        # Push rows to the top so they don't stretch to fill the scroll area
        self.photo_container.addStretch()

    def _update_slot_display(self, index, file_label, clear_btn, slot):
        """Update a photo slot's label and clear button visibility."""
        if slot.get('source_path'):
            filename = os.path.basename(slot['source_path'])
            file_label.setText(f"Selected: {filename}")
            file_label.setStyleSheet(
                f"color: {ACCENT_GREEN}; font-size: 12px; padding: 2px; font-weight: bold;")
            clear_btn.setVisible(True)
        elif slot.get('stored_path'):
            filename = os.path.basename(slot['stored_path'])
            file_label.setText(f"Current: {filename}")
            file_label.setStyleSheet(
                f"color: {ACCENT_GREEN}; font-size: 12px; padding: 2px; font-weight: bold;")
            clear_btn.setVisible(True)
        else:
            file_label.setText("No photo")
            file_label.setStyleSheet(
                f"color: {SUBTITLE_GRAY}; font-size: 12px; padding: 2px;")
            clear_btn.setVisible(False)

    def _select_photo_at(self, index):
        """Open file dialog for a specific photo slot."""
        filepath, _ = QFileDialog.getOpenFileName(
            self, f"Select Photo {index + 1}", "",
            "Image Files (*.jpg *.jpeg *.png *.bmp *.gif);;All Files (*)"
        )
        if not filepath:
            return
        if index < len(self._photo_slots):
            # Within-entry duplicate — hard block
            dup = self._check_photo_duplicate(index, filepath)
            if dup is not None:
                QMessageBox.warning(
                    self, "Duplicate Photo",
                    f"This photo is already attached as Check {dup + 1}. "
                    "Please select a different image for each check.")
                return
            # Cross-entry duplicate — soft warning with override
            prev = self._check_previously_stored(filepath)
            if prev is not None:
                answer = QMessageBox.question(
                    self, "Previously Used Photo",
                    f"This image was previously attached to another entry "
                    f"(stored as {prev}).\n\n"
                    "Do you still want to use it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self._photo_slots[index]['source_path'] = filepath
            w = self._photo_slot_widgets[index]
            self._update_slot_display(
                index, w['file_label'], w['clear_btn'], self._photo_slots[index])

    def _check_photo_duplicate(self, index, new_path):
        """Return slot index of a duplicate photo, or None.

        Checks other slots by normalised path (fast) then SHA-256 content
        hash (catches identical files saved under different names).  Also
        checks against already-stored paths when editing an existing entry.
        """
        normalised = os.path.normpath(new_path)
        for i, slot in enumerate(self._photo_slots):
            if i == index:
                continue
            existing = slot.get('source_path')
            if existing and os.path.normpath(existing) == normalised:
                return i

        # Content-hash check
        try:
            from fam.utils.photo_storage import compute_file_hash, get_photo_full_path
            new_hash = compute_file_hash(new_path)
            for i, slot in enumerate(self._photo_slots):
                if i == index:
                    continue
                # Check against other source paths
                existing_src = slot.get('source_path')
                if existing_src:
                    try:
                        if compute_file_hash(existing_src) == new_hash:
                            return i
                    except OSError:
                        pass
                # Check against already-stored paths (editing existing entry)
                stored = slot.get('stored_path')
                if stored:
                    try:
                        full = get_photo_full_path(stored)
                        if os.path.isfile(full) and compute_file_hash(full) == new_hash:
                            return i
                    except OSError:
                        pass
        except Exception:
            logger.debug("Content-hash duplicate check skipped", exc_info=True)

        return None

    @staticmethod
    def _check_previously_stored(new_path):
        """Return the relative path of a previous match, or None.

        Queries local_photo_hashes to see if this file's content was
        ever stored for another entry.
        """
        try:
            from fam.utils.photo_storage import compute_file_hash
            from fam.models.photo_hash import get_local_path_by_hash
            content_hash = compute_file_hash(new_path)
            return get_local_path_by_hash(content_hash)
        except Exception:
            logger.debug("Cross-entry hash check skipped", exc_info=True)
            return None

    def _clear_photo_at(self, index):
        """Clear a specific photo slot."""
        if index < len(self._photo_slots):
            self._photo_slots[index] = {'source_path': None, 'stored_path': None}
            w = self._photo_slot_widgets[index]
            self._update_slot_display(
                index, w['file_label'], w['clear_btn'], self._photo_slots[index])

    def _clear_all_photos(self):
        """Clear all photo slots and rebuild to default count."""
        self._photo_slots.clear()
        self._rebuild_photo_slots(self._get_expected_photo_count())

    def _view_photo(self, raw_photo_path):
        """Open photo(s) in the system's default image viewer."""
        from fam.utils.photo_storage import get_photo_full_path, photo_exists
        from fam.utils.photo_paths import parse_photo_paths
        paths = parse_photo_paths(raw_photo_path)
        missing = []
        for p in paths:
            if photo_exists(p):
                full_path = get_photo_full_path(p)
                QDesktopServices.openUrl(QUrl.fromLocalFile(full_path))
            else:
                missing.append(p)
        if missing:
            QMessageBox.warning(
                self, "Photo Not Found",
                f"{len(missing)} photo file(s) not found on disk:\n"
                + "\n".join(missing))

    # ── Entry CRUD ───────────────────────────────────────────

    def _save_entry(self):
        self.error_label.setVisible(False)
        md_id = self.md_combo.currentData()
        vendor_id = self.vendor_combo.currentData()
        amount = self.amount_spin.value()
        check_count = self.check_count_spin.value() if self.check_count_spin.value() > 0 else None
        entered_by = self.entered_by_input.text().strip()
        notes = self.notes_input.text().strip() or None

        if not md_id:
            # v2.0.7+: ``md_id is None`` now also means the
            # "All Market Days" sentinel is selected.  Save is
            # already disabled in that state (see
            # ``_refresh_save_button_state``); this is a defensive
            # second line of defense in case the button is
            # programmatically clicked.
            self._show_error(
                "Please pick a specific market day above before "
                "adding an entry.  'All Market Days' is a "
                "browse-only filter for searching existing "
                "entries — you can't attribute a new entry to "
                "all markets.")
            return
        method = self._selected_method()
        if not method:
            self._show_error(
                "No payment method is enabled for external matching "
                "— enable one in Settings → Payment Methods first.")
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

        # G2: validate denomination constraint (both in cents for
        # exact integer math).  While editing, the denomination is
        # the ENTRY's own snapshot when it has one (see
        # _configure_selected_method).
        amount_cents = dollars_to_cents(amount)
        if self._fmnp_denomination and self._fmnp_denomination > 0:
            denom = self._fmnp_denomination  # already in cents
            if amount_cents % denom != 0:
                self._show_error(
                    f"Amount must be a multiple of {format_dollars(denom)} "
                    f"({method['name']} denomination). "
                    f"e.g. {format_dollars(denom)}, {format_dollars(denom * 2)}, {format_dollars(denom * 3)}"
                )
                return

        # Validate mandatory photo receipt (all slots must be filled)
        if self._photo_required == 'Mandatory':
            expected = self._get_expected_photo_count()
            filled = sum(1 for s in self._photo_slots
                         if s.get('source_path') or s.get('stored_path'))
            if filled < expected:
                missing = expected - filled
                label = "photo" if missing == 1 else "photos"
                self._show_error(
                    f"Photo receipt is required. {missing} {label} still needed "
                    f"({filled}/{expected} attached).")
                return

        # ── G3 double-count review (new entries only) ─────────
        # The same physical scrip can only have come in through ONE
        # channel: if this (vendor, method, market day) already has
        # booth transactions, an external entry for it needs an
        # explicit review.  Warn, don't block — mixed-flow markets
        # can legitimately see both channels in one day; the prompt
        # leaves an audit-aware human decision either way.  (When
        # RX/Bucks booth acceptance arrives in a future season,
        # this prompt is the load-bearing guard.)
        if not self._editing_id:
            booth = self._booth_activity(
                md_id, vendor_id, self.method_combo.currentData())
            if booth:
                booth_total_cents, booth_txns = booth
                answer = QMessageBox.question(
                    self, "Review — Booth Activity Exists",
                    f"{self.vendor_combo.currentText()} already has "
                    f"{booth_txns} booth transaction(s) totalling "
                    f"{format_dollars(booth_total_cents)} of "
                    f"{method['name']} on this market day.\n\n"
                    f"A token paid at the booth AND turned in by the "
                    f"vendor as an external entry would be reimbursed "
                    f"TWICE.  Only continue if this batch is separate "
                    f"scrip the vendor accepted directly.\n\n"
                    f"Record the external entry anyway?",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return

        # ── G1 payout confirmation (+ G5 large-amount emphasis) ──
        # The manager confirms the MONEY, not just the count.  For
        # an EDIT, the payout is derived from the entry's snapshot
        # config (method/config are immutable; only face value can
        # change), which _selected_method reflects because the
        # dropdown is locked to the entry's method.
        from fam.utils.external_payout import (
            compute_external_payout_cents, reimbursement_basis)
        if self._editing_id and self._editing_snapshots:
            snap_match, snap_cashes = self._editing_snapshots
        else:
            snap_match = method['match_percent']
            snap_cashes = bool(method.get('vendor_cashes_original'))
        payout_cents = compute_external_payout_cents(
            amount_cents, snap_match, snap_cashes)
        basis = reimbursement_basis(
            amount_cents, snap_match, snap_cashes)
        vendor_name = self.vendor_combo.currentText()
        confirm_lines = [
            f"FAM will owe {vendor_name} "
            f"{format_dollars(payout_cents)} for this entry.",
            "",
            f"Method: {method['name']}",
            f"Face value: {format_dollars(amount_cents)}",
            f"Basis: {basis}",
        ]
        # G5: reuse the existing large-receipt threshold on the
        # computed payout (same guard as Receipt Intake + Admin).
        from fam.utils.app_settings import get_large_receipt_threshold
        if cents_to_dollars(payout_cents) > get_large_receipt_threshold():
            confirm_lines += [
                "",
                "⚠ LARGE AMOUNT — this payout exceeds the large-"
                "receipt threshold. Double-check the face value "
                "before confirming."]
        confirm_lines += ["", "Record this entry?"]
        answer = QMessageBox.question(
            self, "Confirm Entry — FAM Owes "
            + format_dollars(payout_cents),
            "\n".join(confirm_lines),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            from fam.utils.photo_storage import store_photo
            from fam.utils.photo_paths import encode_photo_paths

            if self._editing_id:
                old = get_fmnp_entry_by_id(self._editing_id)

                # Build photo paths list from slots
                final_paths = []
                changed = False
                for slot in self._photo_slots:
                    if slot.get('source_path'):
                        # New photo — store it
                        rel = store_photo(slot['source_path'], self._editing_id)
                        final_paths.append(rel)
                        changed = True
                    elif slot.get('stored_path'):
                        final_paths.append(slot['stored_path'])
                    else:
                        changed = True  # Slot was cleared

                # Determine if we need to update photo_path in DB
                new_encoded = encode_photo_paths(final_paths)
                old_encoded = old.get('photo_path')
                # Model auto-logs per-field UPDATE audit rows for any actual
                # change; no-op if nothing changed.
                if changed or new_encoded != old_encoded:
                    update_fmnp_entry(self._editing_id, amount=amount_cents,
                                      vendor_id=vendor_id,
                                      check_count=check_count, notes=notes,
                                      photo_path=new_encoded,
                                      changed_by=entered_by)
                else:
                    update_fmnp_entry(self._editing_id, amount=amount_cents,
                                      vendor_id=vendor_id,
                                      check_count=check_count, notes=notes,
                                      changed_by=entered_by)
                self._cancel_edit()
            else:
                # create_fmnp_entry auto-logs an INSERT audit row and
                # snapshots the selected method's config at insert
                # time (single enforcement point for the snapshot
                # discipline — see fam/models/fmnp.py).
                entry_id = create_fmnp_entry(
                    md_id, vendor_id, amount_cents,
                    entered_by, check_count, notes,
                    payment_method_id=self.method_combo.currentData())

                # Store all photos after create (need entry_id for filenames)
                photo_paths = []
                for slot in self._photo_slots:
                    if slot.get('source_path'):
                        try:
                            rel = store_photo(slot['source_path'], entry_id)
                            photo_paths.append(rel)
                        except Exception as e:
                            logger.warning("Failed to store photo for entry %d: %s",
                                           entry_id, e)
                if photo_paths:
                    encoded = encode_photo_paths(photo_paths)
                    # Photo attachment is a change → auto-logged UPDATE
                    update_fmnp_entry(entry_id, photo_path=encoded,
                                      changed_by=entered_by)

            self.amount_spin.setValue(0)
            self.check_count_spin.setValue(0)
            self.notes_input.clear()
            self._clear_all_photos()
            self._load_entries()
            # v2.0.6: emit the affected market_day_id so the sync
            # handler scopes to THAT day (not the open day).  Closed-
            # market entries added after-the-fact reach the cloud.
            self.entry_saved.emit(int(md_id) if md_id else 0)
        except Exception as e:
            logger.exception("Failed to save FMNP entry")
            self._show_error(f"Error saving entry: {e}")

    def _edit_entry(self, entry_id):
        entry = get_fmnp_entry_by_id(entry_id)
        if not entry:
            return
        self._editing_id = entry_id

        # v2.1.0 (ENH-002): the entry's method and config snapshots
        # are IMMUTABLE (DB trigger enforces; corrections are
        # void + re-enter).  Lock the dropdown to the entry's
        # method; validation + payout derive from the entry's OWN
        # snapshots so a later settings change can't re-value or
        # re-validate history.  If the method is no longer
        # external-enabled, temporarily add it so the edit shows
        # truthfully.
        self._editing_snapshots = (
            (entry['match_percent_snapshot'],
             bool(entry['vendor_cashes_original_snapshot']))
            if entry.get('match_percent_snapshot') is not None
            else None)
        self._editing_denom_snapshot = entry.get('denomination_snapshot')
        target_pm = entry.get('payment_method_id')
        idx = self.method_combo.findData(target_pm)
        if idx < 0 and target_pm is not None:
            self.method_combo.addItem(
                entry.get('method_name_snapshot') or 'Unknown',
                userData=target_pm)
            idx = self.method_combo.count() - 1
            self._temp_method_item = True
        if idx >= 0:
            self.method_combo.setCurrentIndex(idx)
        self.method_combo.setEnabled(False)
        self._configure_selected_method()

        self.amount_spin.setValue(cents_to_dollars(entry['amount']))
        self.check_count_spin.setValue(entry.get('check_count') or 0)
        self.notes_input.setText(entry.get('notes') or '')

        # Select matching vendor
        for i in range(self.vendor_combo.count()):
            if self.vendor_combo.itemData(i) == entry['vendor_id']:
                self.vendor_combo.setCurrentIndex(i)
                break

        # Restore photo slots from stored paths
        from fam.utils.photo_paths import parse_photo_paths
        stored_paths = parse_photo_paths(entry.get('photo_path'))
        expected = self._get_expected_photo_count()
        count = max(expected, len(stored_paths))

        self._photo_slots = []
        for i in range(count):
            stored = stored_paths[i] if i < len(stored_paths) else None
            self._photo_slots.append({'source_path': None, 'stored_path': stored})
        self._rebuild_photo_slots(count)

        self.save_btn.setText("Update Entry")
        self.cancel_edit_btn.setVisible(True)

    def _cancel_edit(self):
        self._editing_id = None
        self._editing_snapshots = None
        self._editing_denom_snapshot = None
        if getattr(self, '_temp_method_item', False):
            # Drop the dropdown item that existed only to display a
            # no-longer-external method during the edit.
            self.method_combo.blockSignals(True)
            try:
                self.method_combo.removeItem(
                    self.method_combo.count() - 1)
            finally:
                self.method_combo.blockSignals(False)
            self._temp_method_item = False
        if hasattr(self, 'method_combo'):
            self.method_combo.setEnabled(True)
            self._configure_selected_method()
        self.save_btn.setText("Add Entry")
        self.cancel_edit_btn.setVisible(False)
        self._clear_all_photos()

    def _delete_entry(self, entry_id):
        result = QMessageBox.question(
            self, "Delete Entry",
            "Are you sure you want to delete this entry?\n\n"
            "Deleting (voiding) is the supported correction for an "
            "entry recorded under the wrong method or settings — "
            "delete it, fix Settings if needed, then re-enter.",
            QMessageBox.Yes | QMessageBox.No
        )
        if result == QMessageBox.Yes:
            try:
                # Capture the entry's market_day_id BEFORE delete so
                # the post-delete sync can scope to the affected day
                # (which may be a closed market day).
                pre_delete = get_fmnp_entry_by_id(entry_id)
                affected_md_id = (pre_delete or {}).get('market_day_id') or 0

                entered_by = self.entered_by_input.text().strip() or "System"
                # delete_fmnp_entry auto-logs a DELETE audit row
                delete_fmnp_entry(entry_id, changed_by=entered_by)
                write_ledger_backup()
                self._load_entries()
                # Deletion is a mutation — emit the same signal as save so
                # the main window triggers a cloud sync.  The 60-second
                # sync cooldown prevents any rapid-fire overload.
                self.entry_saved.emit(int(affected_md_id))
            except Exception as e:
                logger.exception("Failed to delete FMNP entry %s", entry_id)
                self._show_error(f"Error deleting entry: {e}")

    def _booth_activity(self, md_id, vendor_id, payment_method_id):
        """(physical-instrument total cents, txn count) of ACTIVE
        booth activity for (vendor, method, market day), or None.

        G3 input: the total is denomination-true (customer_charged
        + forfeit — same semantics as the Vendor Reimbursement
        per-method columns), i.e. the scrip the booth already saw.
        Status filtering via ACTIVE_TX_STATUSES (never ad-hoc
        literals — CLAUDE.md rule 6)."""
        from fam.database.connection import get_connection
        from fam.models.transaction import active_tx_status_clause
        conn = get_connection()
        row = conn.execute(f"""
            SELECT COALESCE(SUM(pl.customer_charged
                                + pl.customer_forfeit_cents), 0)
                       AS total,
                   COUNT(DISTINCT t.id) AS txns
            FROM payment_line_items pl
            JOIN transactions t ON pl.transaction_id = t.id
            WHERE t.market_day_id = ?
              AND t.vendor_id = ?
              AND pl.payment_method_id = ?
              AND {active_tx_status_clause('t')}
        """, (md_id, vendor_id, payment_method_id)).fetchone()
        if row and row['txns']:
            return (row['total'], row['txns'])
        return None

    def _show_error(self, msg):
        self.error_label.setText(msg)
        self.error_label.setVisible(True)
