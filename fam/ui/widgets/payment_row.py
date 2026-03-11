"""Reusable payment method entry row widget."""

import logging
import os

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QFileDialog,
    QDialog, QDialogButtonBox, QMessageBox
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QStandardItemModel, QColor, QBrush
from fam.models.payment_method import get_all_payment_methods, get_payment_methods_for_market
from fam.utils.calculations import charge_to_method_amount, method_amount_to_charge

logger = logging.getLogger('fam.ui.widgets.payment_row')
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

        # Charge input — what the customer pays for this payment method
        amount_label = QLabel("Charge:")
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

        # Denomination hint (visible when method has denomination set)
        self.denom_hint = QLabel("")
        self.denom_hint.setStyleSheet(f"font-weight: bold; color: {ACCENT_GREEN}; font-size: 13px;")
        self.denom_hint.setVisible(False)
        layout.addWidget(self.denom_hint)

        # Computed fields
        layout.addWidget(QLabel("FAM Match:"))
        self.match_amount_label = QLabel("$0.00")
        self.match_amount_label.setStyleSheet("font-weight: bold;")
        self.match_amount_label.setMinimumWidth(70)
        layout.addWidget(self.match_amount_label)

        layout.addWidget(QLabel("Total:"))
        self.total_label = QLabel("$0.00")
        self.total_label.setStyleSheet("font-weight: bold;")
        self.total_label.setMinimumWidth(70)
        layout.addWidget(self.total_label)

        # Photo button — visible when method has photo_required set
        # Supports multiple photos for denominated methods (e.g. 3 FMNP checks = 3 photos)
        self._photo_source_paths: list[str | None] = []
        self._expected_photo_count = 0
        self.photo_btn = QPushButton("\U0001f4f7")  # camera emoji
        self.photo_btn.setFixedSize(36, 28)
        self.photo_btn.setToolTip("Attach photo receipt")
        self._style_photo_btn(attached=False)
        self.photo_btn.setVisible(False)
        self.photo_btn.clicked.connect(self._select_photo)
        layout.addWidget(self.photo_btn)

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
        self._update_denomination_hint()
        self._update_photo_button()
        self._recompute()
        self.changed.emit()

    def _update_denomination_hint(self):
        """Show denomination hint and set spinbox step when method has denomination."""
        method = self.get_selected_method()
        if method and method.get('denomination'):
            denom = method['denomination']
            self.denom_hint.setText(f"(${denom:.0f} increments)")
            self.denom_hint.setVisible(True)
            self.amount_spin.setSingleStep(denom)
        else:
            self.denom_hint.setVisible(False)
            self.amount_spin.setSingleStep(1.00)

    def _recompute(self):
        """Recompute FAM Match and Total from the charge amount input."""
        method = self.get_selected_method()
        charge = self.amount_spin.value()
        if method:
            match_pct = method['match_percent']
        else:
            match_pct = 0.0
        match_amt = round(charge * (match_pct / 100.0), 2)
        total = round(charge + match_amt, 2)
        self.match_amount_label.setText(f"${match_amt:.2f}")
        self.total_label.setText(f"${total:.2f}")

    def get_selected_method(self):
        return self.method_combo.currentData()

    def get_data(self):
        method = self.get_selected_method()
        if not method:
            return None
        charge = self.amount_spin.value()
        match_pct = method['match_percent']
        match_amount = round(charge * (match_pct / 100.0), 2)
        method_amount = charge_to_method_amount(charge, match_pct)
        # Return list of photo source paths (filtered to non-None)
        photo_paths = [p for p in self._photo_source_paths if p]
        return {
            'payment_method_id': method['id'],
            'method_name_snapshot': method['name'],
            'match_percent_snapshot': match_pct,
            'method_amount': method_amount,
            'match_percent': match_pct,
            'match_amount': match_amount,
            'customer_charged': charge,
            'photo_source_paths': photo_paths,
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

    def set_display_values(self, match_amount, total):
        """Override the displayed match and total values (e.g., after cap)."""
        self.match_amount_label.setText(f"${match_amount:.2f}")
        self.total_label.setText(f"${total:.2f}")

    def set_data(self, payment_method_id, method_amount):
        """Set the row from existing data (method_amount is total allocation from DB)."""
        for i in range(self.method_combo.count()):
            m = self.method_combo.itemData(i)
            if m and m['id'] == payment_method_id:
                self.method_combo.setCurrentIndex(i)
                break
        # Convert total allocation back to charge amount for the spinbox
        method = self.get_selected_method()
        if method:
            charge = method_amount_to_charge(method_amount, method['match_percent'])
        else:
            charge = method_amount
        self.amount_spin.setValue(charge)

    def validate_denomination(self):
        """Return error string if charge violates denomination, else None."""
        method = self.get_selected_method()
        if not method or not method.get('denomination'):
            return None
        charge = self.amount_spin.value()
        denom = method['denomination']
        if charge > 0 and round(charge % denom, 2) != 0:
            return (f"{method['name']} must be in ${denom:.0f} increments "
                    f"(entered ${charge:.2f})")
        return None

    # ── Photo receipt (multi-photo aware) ────────────────────

    def _get_check_count(self) -> int:
        """Return the number of checks/photos expected for this payment row.

        For denominated methods, count = int(charge / denomination).
        Otherwise 1.  Returns 0 when charge is 0 or no method selected.
        """
        method = self.get_selected_method()
        if not method:
            return 0
        charge = self.amount_spin.value()
        if charge <= 0:
            return 0
        denom = method.get('denomination')
        if denom and denom > 0:
            return max(1, int(charge / denom))
        return 1

    def _style_photo_btn(self, attached=False):
        """Style the photo button — green border when photo attached, gray when not."""
        border_color = ACCENT_GREEN if attached else SUBTITLE_GRAY
        bg_hover = ACCENT_GREEN if attached else LIGHT_GRAY
        self.photo_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1.5px solid {border_color};
                border-radius: 14px;
                font-size: 12px;
                padding: 0px 2px;
            }}
            QPushButton:hover {{
                background-color: {bg_hover};
            }}
        """)

    def _update_photo_button(self):
        """Show/hide photo button and update count badge based on method and amount."""
        method = self.get_selected_method()
        if method and method.get('photo_required') in ('Optional', 'Mandatory'):
            self.photo_btn.setVisible(True)
            count = self._get_check_count()
            if count != self._expected_photo_count:
                self._resize_photo_paths(count)
            self._update_photo_badge()
        else:
            self.photo_btn.setVisible(False)
            self._photo_source_paths.clear()
            self._expected_photo_count = 0
            self._style_photo_btn(attached=False)

    def _resize_photo_paths(self, new_count: int):
        """Resize the photo paths list, preserving existing photos."""
        old = list(self._photo_source_paths)
        self._photo_source_paths = [None] * new_count
        for i in range(min(len(old), new_count)):
            self._photo_source_paths[i] = old[i]
        self._expected_photo_count = new_count

    def _update_photo_badge(self):
        """Update the camera button text/style to show photo count badge."""
        count = self._expected_photo_count
        filled = sum(1 for p in self._photo_source_paths if p)
        if count <= 1:
            # Single photo mode — just camera emoji
            self.photo_btn.setText("\U0001f4f7")
            self.photo_btn.setFixedSize(36, 28)
            self._style_photo_btn(attached=(filled > 0))
            if filled:
                path = self._photo_source_paths[0]
                fname = path.split('/')[-1].split(chr(92))[-1] if path else ''
                self.photo_btn.setToolTip(f"Photo: {fname}")
            else:
                self.photo_btn.setToolTip("Attach photo receipt")
        else:
            # Multi-photo mode — show count badge
            self.photo_btn.setText(f"\U0001f4f7 {filled}/{count}")
            self.photo_btn.setFixedSize(64, 28)
            all_filled = (filled == count)
            self._style_photo_btn(attached=all_filled)
            self.photo_btn.setToolTip(
                f"{filled} of {count} photos attached"
                if not all_filled else f"All {count} photos attached"
            )

    def _select_photo(self):
        """Open file dialog or multi-photo dialog depending on check count."""
        count = self._expected_photo_count
        if count <= 0:
            return
        if count == 1:
            # Single photo — simple file dialog
            path, _ = QFileDialog.getOpenFileName(
                self, "Select Photo Receipt", "",
                "Images (*.jpg *.jpeg *.png *.bmp *.gif);;All Files (*)"
            )
            if path:
                prev = _MultiPhotoDialog._check_previously_stored(path)
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
                self._photo_source_paths = [path]
                self._update_photo_badge()
        else:
            # Multi-photo — open dialog with numbered slots
            self._open_multi_photo_dialog()

    def _open_multi_photo_dialog(self):
        """Open a dialog with numbered photo slots for denominated checks."""
        dialog = _MultiPhotoDialog(
            count=self._expected_photo_count,
            existing_paths=list(self._photo_source_paths),
            parent=self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._photo_source_paths = dialog.get_paths()
            self._update_photo_badge()

    def validate_photo(self):
        """Return error string if method requires mandatory photo and any slot is missing."""
        method = self.get_selected_method()
        if not method or method.get('photo_required') != 'Mandatory':
            return None
        charge = self.amount_spin.value()
        if charge <= 0:
            return None  # No charge = no photo required
        count = self._get_check_count()
        filled = sum(1 for p in self._photo_source_paths if p)
        if filled < count:
            if count == 1:
                return (f"{method['name']} requires a photo receipt. "
                        f"Click the camera button to attach one.")
            else:
                return (f"{method['name']} requires {count} photo receipts "
                        f"({filled} of {count} attached). "
                        f"Click the camera button to attach them.")
        return None

    def get_photo_paths(self):
        """Return list of selected photo source paths (non-None only)."""
        return [p for p in self._photo_source_paths if p]

    def get_photo_path(self):
        """Return the first selected photo source path, or None (backward compat)."""
        paths = self.get_photo_paths()
        return paths[0] if paths else None


class _MultiPhotoDialog(QDialog):
    """Dialog with numbered photo slots for attaching multiple check photos."""

    def __init__(self, count: int, existing_paths: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Attach {count} Check Photos")
        self.setMinimumWidth(420)
        self._count = count
        self._paths: list[str | None] = [None] * count
        for i in range(min(len(existing_paths), count)):
            self._paths[i] = existing_paths[i]

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        info = QLabel(f"Attach a photo for each of the {count} checks:")
        info.setStyleSheet("font-weight: bold; font-size: 13px; margin-bottom: 4px;")
        layout.addWidget(info)

        self._slot_labels: list[QLabel] = []
        self._slot_btns: list[QPushButton] = []
        self._clear_btns: list[QPushButton] = []

        for i in range(count):
            row = QHBoxLayout()
            row.setSpacing(6)

            label = QLabel(f"Check {i + 1}:")
            label.setMinimumWidth(60)
            label.setStyleSheet("font-weight: bold;")
            row.addWidget(label)

            attach_btn = QPushButton("Attach...")
            attach_btn.setMinimumWidth(80)
            attach_btn.clicked.connect(lambda checked, idx=i: self._attach_at(idx))
            row.addWidget(attach_btn)
            self._slot_btns.append(attach_btn)

            file_label = QLabel("No photo")
            file_label.setMinimumWidth(180)
            file_label.setStyleSheet(f"color: {SUBTITLE_GRAY};")
            row.addWidget(file_label, 1)
            self._slot_labels.append(file_label)

            clear_btn = QPushButton("Clear")
            clear_btn.setMinimumWidth(60)
            clear_btn.setVisible(False)
            clear_btn.clicked.connect(lambda checked, idx=i: self._clear_at(idx))
            row.addWidget(clear_btn)
            self._clear_btns.append(clear_btn)

            layout.addLayout(row)

        # Pre-populate existing paths in the display
        for i in range(count):
            self._update_slot_display(i)

        # Dialog buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _attach_at(self, index: int):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select Photo for Check {index + 1}", "",
            "Images (*.jpg *.jpeg *.png *.bmp *.gif);;All Files (*)"
        )
        if path:
            # Within-entry duplicate — hard block
            dup = self._check_duplicate(index, path)
            if dup is not None:
                QMessageBox.warning(
                    self, "Duplicate Photo",
                    f"This photo is already attached as Check {dup + 1}. "
                    "Please select a different image for each check.")
                return
            # Cross-transaction duplicate — soft warning with override
            prev = self._check_previously_stored(path)
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
            self._paths[index] = path
            self._update_slot_display(index)

    def _clear_at(self, index: int):
        self._paths[index] = None
        self._update_slot_display(index)

    def _update_slot_display(self, index: int):
        path = self._paths[index]
        if path:
            fname = path.split('/')[-1].split(chr(92))[-1]
            self._slot_labels[index].setText(fname)
            self._slot_labels[index].setStyleSheet(f"color: {ACCENT_GREEN}; font-weight: bold;")
            self._clear_btns[index].setVisible(True)
        else:
            self._slot_labels[index].setText("No photo")
            self._slot_labels[index].setStyleSheet(f"color: {SUBTITLE_GRAY};")
            self._clear_btns[index].setVisible(False)

    def _check_duplicate(self, index: int, new_path: str):
        """Return the slot index of a duplicate, or None.

        Checks by normalised file path first (fast), then by SHA-256
        content hash (catches identical files saved under different names).
        """
        normalised = os.path.normpath(new_path)
        for i, existing in enumerate(self._paths):
            if i == index or not existing:
                continue
            if os.path.normpath(existing) == normalised:
                return i

        # Content-hash check (catches copies under different filenames)
        try:
            from fam.utils.photo_storage import compute_file_hash
            new_hash = compute_file_hash(new_path)
            for i, existing in enumerate(self._paths):
                if i == index or not existing:
                    continue
                try:
                    if compute_file_hash(existing) == new_hash:
                        return i
                except OSError:
                    pass
        except Exception:
            logger.debug("Content-hash duplicate check skipped", exc_info=True)

        return None

    @staticmethod
    def _check_previously_stored(new_path: str):
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
            logger.debug("Cross-transaction hash check skipped", exc_info=True)
            return None

    def get_paths(self) -> list[str | None]:
        return list(self._paths)
