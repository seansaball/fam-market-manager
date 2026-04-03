"""Screen D: FMNP Entry."""

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
from fam.models.audit import log_action
from fam.utils.export import write_ledger_backup
from fam.utils.money import dollars_to_cents, cents_to_dollars, format_dollars
from fam.ui.styles import WHITE, LIGHT_GRAY, ERROR_COLOR, PRIMARY_GREEN, ERROR_BG, SUBTITLE_GRAY, ACCENT_GREEN
from fam.ui.helpers import (
    make_field_label, make_item, make_section_label, make_action_btn,
    configure_table, NoScrollDoubleSpinBox, NoScrollSpinBox, NoScrollComboBox
)

logger = logging.getLogger('fam.ui.fmnp_screen')


class FMNPScreen(QWidget):
    """FMNP Entry screen."""

    entry_saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editing_id = None
        # Multi-photo slots: list of dicts with 'source_path' and 'stored_path'
        self._photo_slots: list[dict] = []
        self._photo_slot_widgets: list[dict] = []  # UI widgets per slot
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        title = QLabel("FMNP Check Tracking")
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
        layout.addWidget(make_section_label("FMNP Entries for Selected Market"))
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Vendor", "Amount", "Checks", "Entered By", "Notes",
             "Photo", "Status", "Actions"]
        )
        configure_table(self.table, actions_col=8, actions_width=160)
        layout.addWidget(self.table)

    def refresh(self):
        self._configure_fmnp_denomination()
        self._load_market_days()
        self._load_vendors()
        self._load_entries()
        self._cancel_edit()

    def _configure_fmnp_denomination(self):
        """Look up FMNP payment method and configure denomination + photo requirement."""
        from fam.models.payment_method import get_payment_method_by_name
        self._fmnp_denomination = None
        self._photo_required = 'Off'
        fmnp = get_payment_method_by_name('FMNP')
        if fmnp:
            if fmnp.get('denomination'):
                denom_cents = fmnp['denomination']  # DB stores cents
                self._fmnp_denomination = denom_cents
                denom_dollars = cents_to_dollars(denom_cents)
                self.amount_spin.setSingleStep(denom_dollars)
                self.denom_hint.setText(f"({format_dollars(denom_cents)} increments)")
                self.denom_hint.setVisible(True)
            else:
                self.amount_spin.setSingleStep(1.00)
                self.denom_hint.setVisible(False)
            self._photo_required = fmnp.get('photo_required') or 'Off'
        else:
            self.amount_spin.setSingleStep(1.00)
            self.denom_hint.setVisible(False)

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
        # Refresh FMNP settings (denomination + photo requirement) in case
        # they were changed in Settings since the screen was created.
        self._configure_fmnp_denomination()

        md_id = self.md_combo.currentData()
        if not md_id:
            self.table.setRowCount(0)
            return

        entries = get_fmnp_entries(md_id, active_only=False)
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
            self.table.setItem(i, 1, make_item(e['vendor_name']))
            amount_dollars = cents_to_dollars(e['amount'])
            self.table.setItem(i, 2, make_item(f"${amount_dollars:.2f}", amount_dollars))
            self.table.setItem(i, 3, make_item(str(e.get('check_count') or ''),
                                                e.get('check_count') or 0))
            self.table.setItem(i, 4, make_item(e['entered_by']))
            self.table.setItem(i, 5, make_item(e.get('notes') or ''))

            # Photo indicator column — show count when multiple
            if photo_count > 1:
                photo_text = f"\U0001f4f7 {photo_count}"
            elif photo_count == 1:
                photo_text = "\U0001f4f7"
            else:
                photo_text = "\u2014"
            self.table.setItem(i, 6, make_item(photo_text))

            self.table.setItem(i, 7, make_item(e.get('status', 'Active')))

            # Grey out all cells for deleted entries
            if is_deleted:
                for col in range(8):
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

            self.table.setCellWidget(i, 8, action_widget)
            self.table.setRowHeight(i, 42)

        self.table.setSortingEnabled(True)

    # ── Photo attachment ─────────────────────────────────────

    def _get_expected_photo_count(self) -> int:
        """Return the number of photo slots based on amount / denomination."""
        amount_cents = dollars_to_cents(self.amount_spin.value())
        if amount_cents <= 0:
            return 1
        if self._fmnp_denomination and self._fmnp_denomination > 0:
            return max(1, int(amount_cents / self._fmnp_denomination))
        return 1

    def _on_amount_changed(self):
        """Rebuild photo slots when amount changes (affects check count)."""
        expected = self._get_expected_photo_count()
        if len(self._photo_slots) != expected:
            self._rebuild_photo_slots(expected)

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

            label_text = f"Check {i + 1}:" if count > 1 else "Photo:"
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
            self, f"Select Check Photo {index + 1}", "",
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

        # Validate denomination constraint (both in cents for exact integer math)
        amount_cents = dollars_to_cents(amount)
        if self._fmnp_denomination and self._fmnp_denomination > 0:
            denom = self._fmnp_denomination  # already in cents
            if amount_cents % denom != 0:
                self._show_error(
                    f"Amount must be a multiple of {format_dollars(denom)} (FMNP denomination). "
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
                if changed or new_encoded != old_encoded:
                    update_fmnp_entry(self._editing_id, amount=amount_cents,
                                      vendor_id=vendor_id,
                                      check_count=check_count, notes=notes,
                                      photo_path=new_encoded)
                else:
                    update_fmnp_entry(self._editing_id, amount=amount_cents,
                                      vendor_id=vendor_id,
                                      check_count=check_count, notes=notes)

                log_action('fmnp_entries', self._editing_id, 'UPDATE', entered_by,
                            field_name='amount', old_value=old.get('amount'),
                            new_value=amount_cents, reason_code='edit', notes='FMNP entry updated')
                self._cancel_edit()
            else:
                entry_id = create_fmnp_entry(md_id, vendor_id, amount_cents,
                                              entered_by, check_count, notes)
                log_action('fmnp_entries', entry_id, 'INSERT', entered_by,
                           notes='FMNP entry created')

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
                    update_fmnp_entry(entry_id, photo_path=encoded)

            self.amount_spin.setValue(0)
            self.check_count_spin.setValue(0)
            self.notes_input.clear()
            self._clear_all_photos()
            self._load_entries()
            self.entry_saved.emit()
        except Exception as e:
            logger.exception("Failed to save FMNP entry")
            self._show_error(f"Error saving entry: {e}")

    def _edit_entry(self, entry_id):
        entry = get_fmnp_entry_by_id(entry_id)
        if not entry:
            return
        self._editing_id = entry_id
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

        self.save_btn.setText("Update FMNP Entry")
        self.cancel_edit_btn.setVisible(True)

    def _cancel_edit(self):
        self._editing_id = None
        self.save_btn.setText("Add FMNP Entry")
        self.cancel_edit_btn.setVisible(False)
        self._clear_all_photos()

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
                write_ledger_backup()
                self._load_entries()
            except Exception as e:
                logger.exception("Failed to delete FMNP entry %s", entry_id)
                self._show_error(f"Error deleting entry: {e}")

    def _show_error(self, msg):
        self.error_label.setText(msg)
        self.error_label.setVisible(True)
