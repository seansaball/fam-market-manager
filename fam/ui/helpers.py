"""Shared UI helper functions for tables, form fields, and action buttons."""

from PySide6.QtWidgets import (
    QTableWidgetItem, QHeaderView, QLabel, QPushButton, QAbstractItemView,
    QComboBox, QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QDateEdit,
    QDialog, QDialogButtonBox, QSpinBox, QDoubleSpinBox, QFormLayout,
    QStyledItemDelegate, QRadioButton
)
from PySide6.QtGui import QStandardItemModel, QStandardItem, QPalette
from PySide6.QtCore import Qt, Signal, QEvent, QDate, QTimer, QObject
from fam.ui.styles import (
    FIELD_LABEL_BG, ERROR_COLOR, PRIMARY_GREEN, TEXT_COLOR, LIGHT_GRAY, WHITE
)

# Style override for small action buttons inside table cells
ACTION_BTN_STYLE = "min-height: 0px; max-height: 28px; padding: 4px 6px; font-size: 11px; border-radius: 4px;"


class ColorPreservingDelegate(QStyledItemDelegate):
    """Item delegate that keeps custom foreground colors when rows are selected.

    Without this, QTableWidget's selection-color stylesheet property overrides
    any per-item foreground set via setForeground(), turning green/red status
    text back to the default text color when the row is clicked or highlighted.
    """

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        fg = index.data(Qt.ForegroundRole)
        if fg is not None:
            color = fg.color() if hasattr(fg, 'color') else fg
            option.palette.setColor(QPalette.HighlightedText, color)


# ── Scroll-safe input widgets ────────────────────────────────────
# Prevent accidental value changes when scrolling the page.

def _safe_select_all(widget):
    """Call ``widget.selectAll()`` if the underlying C++ object is
    still alive.

    ``focusInEvent`` schedules a deferred ``selectAll`` via
    ``QTimer.singleShot(0, ...)`` so the focus's selection-collapse
    behaviour doesn't override it.  But if the widget gets deleted
    between focus-in and the timer firing — e.g. the user clicked a
    spinbox in a payment row, then immediately clicked Proceed-to-
    Payment, and ``_clear_payment_rows()``'s ``deleteLater`` destroys
    the spinbox before the deferred ``selectAll`` runs — the bound
    method points at a freed C++ object and raises:

        RuntimeError: Internal C++ object
        (NoScrollDoubleSpinBox) already deleted.

    The exception bubbles to ``sys.excepthook`` and surfaces as the
    "Unexpected Error" dialog the user saw on 2026-04-30.  Guarding
    the call here is the right place: every NoScroll spin-box uses
    the same focusIn pattern, and the call is purely cosmetic
    (auto-select-on-focus is a UX nicety, not load-bearing).
    """
    try:
        widget.selectAll()
    except RuntimeError:
        # C++ object already deleted between focusIn and singleShot
        # firing.  Nothing to select; safe to swallow.
        pass


# ── Money/number typing model (v2.1.0, ENH-007) ─────────────────
# The NoScroll spin boxes type the STANDARD way: left-to-right,
# what-you-type-is-what-you-see, native Qt editing.  Type "85" and
# commit → $85.00; type "85.5" → $85.50; the decimal key inserts a
# real decimal point; the value formats to two decimals when the
# field commits (Tab / Enter / focus-out).
#
# HISTORY — do not re-introduce the "cents-builder" ladder: from
# 2026-04-30 to 2026-06-12 these widgets implemented an ATM/POS
# cents-first typing system (overtype + shift-left: typing 8,5
# produced $8.50, the decimal key was silently swallowed).  Field
# evidence killed it: volunteers type calculator-style, so "85"
# became $8.50 — perceived as "a random extra 0 at the end" — and
# "85.5" became $8.55 (10× off).  Amounts typed with exactly two
# decimals coincidentally worked, which made the failures look
# random.  The ladder, its helpers (_try_overtype_next_char,
# _shift_left_append, _reformat_and_park_cursor_before_decimal)
# and its custom keyPressEvent overrides were removed wholesale —
# native Qt typing has no custom digit branch, which also
# structurally eliminates the int('') crash class fixed on
# 2026-05-07 (see tests/test_spinbox_keypress_empty_text.py).


def _clamp_selection_to_value(spin):
    """Clamp the current selection to the editable VALUE region —
    called at EDIT-KEYSTROKE time only, never while selecting.

    QAbstractSpinBox's own ``selectAll()`` (our focus-in behavior)
    deliberately excludes the prefix — but a USER Ctrl+A or drag
    selects the "$ " prefix/suffix too, and Qt refuses edits that
    would damage them: Backspace just collapses the cursor to the
    front and typing PREPENDS instead of replacing (field-reported
    2026-06-12, the highlight-and-retype bug).

    TIMING MATTERS (second field report, same day): an earlier cut
    clamped on ``selectionChanged`` — but correcting a selection
    DURING a mouse drag resets Qt's drag anchor, so dragging left
    past the "$" flipped the selection to the prefix alone and the
    prepend bug returned.  So the visual selection stays fully
    native (the "$" may highlight, as in any Qt app); the clamp
    runs from a KeyPress event filter immediately before the edit
    applies, when no gesture can be in progress.

    A selection lying entirely inside the prefix/suffix collapses
    to the nearest value edge so the keystroke lands predictably.
    """
    line = spin.lineEdit()
    if not line.hasSelectedText():
        return
    text = line.text()
    prefix = spin.prefix()
    suffix = spin.suffix()
    lo = len(prefix) if prefix and text.startswith(prefix) else 0
    hi = (len(text) - len(suffix)
          if suffix and text.endswith(suffix) else len(text))
    start = line.selectionStart()
    end = start + len(line.selectedText())
    new_start = max(start, lo)
    new_end = min(end, hi)
    if (new_start, new_end) == (start, end):
        return
    if new_end > new_start:
        line.setSelection(new_start, new_end - new_start)
    else:
        # Entirely inside the prefix (or suffix): deselect and park
        # the cursor at the nearest value edge.
        line.setCursorPosition(lo if end <= lo else hi)


class _PrefixSafeEditFilter(QObject):
    """Dedicated event-filter object for the NoScroll spin boxes:
    clamps prefix/suffix-spanning selections to the value region at
    the moment an edit keystroke arrives (see
    ``_clamp_selection_to_value`` for the full story).

    A standalone QObject child ON PURPOSE — an earlier cut made the
    spinbox subclass its own filter via a Python mixin overriding
    ``eventFilter``, and the full suite hard-crashed mid-run
    (interpreter death, no traceback) at a deterministic point:
    PySide6 dispatching a C++ virtual into a non-QObject Python
    mixin for every lineEdit event is exactly the pattern that
    breaks during heavy widget churn.  The filter never consumes or
    rewrites events — it only adjusts the selection and returns
    False so Qt processes the key natively; the
    no-custom-keyPressEvent guarantee (ENH-007) stands."""

    def __init__(self, spin):
        super().__init__(spin)
        self._spin = spin

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            try:
                line = self._spin.lineEdit()
                if obj is line and line.hasSelectedText():
                    _clamp_selection_to_value(self._spin)
            except RuntimeError:
                pass   # C++ object already mid-destruction
        return False


class _PrefixSafeEditingMixin:
    """Install hook shared by the NoScroll spin boxes (the actual
    filtering lives in ``_PrefixSafeEditFilter`` — see its
    docstring for why it is a separate QObject)."""

    def _install_prefix_safe_editing(self):
        self._prefix_safe_filter = _PrefixSafeEditFilter(self)
        self.lineEdit().installEventFilter(self._prefix_safe_filter)


class NoScrollSpinBox(_PrefixSafeEditingMixin, QSpinBox):
    """QSpinBox with scroll-safe focus and select-all-on-focus.

    Typing is NATIVE Qt — left-to-right, exactly what you see (see
    the ENH-007 history note above; no custom keyPressEvent, on
    purpose).  Select-all on focus gives type-to-replace: tabbing
    into the field and typing starts a fresh number.

    Keyboard tracking is OFF (ENH-007): ``valueChanged`` fires on
    COMMIT (Tab / Enter / focus-out / arrows), not per keystroke.
    See the double-spin sibling for the trace that forced this.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setKeyboardTracking(False)
        self._install_prefix_safe_editing()

    def wheelEvent(self, event):
        event.ignore()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        QTimer.singleShot(0, lambda: _safe_select_all(self))


class NoScrollDoubleSpinBox(_PrefixSafeEditingMixin, QDoubleSpinBox):
    """QDoubleSpinBox with scroll-safe focus and select-all-on-focus.

    Typing is NATIVE Qt — left-to-right, exactly what you see: type
    ``85`` and commit → ``$ 85.00``; type ``85.5`` → ``$ 85.50``;
    the decimal key inserts a real decimal point and Qt's validator
    caps the fraction at ``decimals()`` digits.  The value formats
    on commit (Tab / Enter / focus-out).  See the ENH-007 history
    note above for why there is deliberately NO custom keyPressEvent
    here.  Select-all on focus gives type-to-replace: tabbing into
    the field and typing starts a fresh number.

    Keyboard tracking is OFF (ENH-007): ``valueChanged`` fires on
    COMMIT, not per keystroke.  With tracking on, app handlers
    listening to valueChanged (payment-row recompute, payout
    previews) read ``.value()`` mid-edit, which makes Qt re-render
    the editor text — traced on the payment row: after typing "8"
    the text snapped back to "$ 8.00", so the volunteer's "."
    keystroke was rejected (a dot already existed) and "8.5" became
    $85.  Tracking-off is Qt's canonical remedy: keystrokes never
    run value handlers, so nothing can rewrite the editor while the
    user is typing.  Programmatic ``setValue`` still emits
    valueChanged immediately (tracking only affects typing).
    Listeners that need a FIRST-KEYSTROKE signal (payment-row ⚡
    lock) hook ``lineEdit().textEdited`` instead — it fires only
    for user edits, never for programmatic writes.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setKeyboardTracking(False)
        self._install_prefix_safe_editing()

    def wheelEvent(self, event):
        event.ignore()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        QTimer.singleShot(0, lambda: _safe_select_all(self))


class NoScrollComboBox(QComboBox):
    """QComboBox that ignores mouse wheel events."""

    def wheelEvent(self, event):
        event.ignore()


class SortableTableItem(QTableWidgetItem):
    """Table item that sorts numerically when UserRole data is set,
    otherwise falls back to case-insensitive text comparison."""

    def __lt__(self, other):
        if not isinstance(other, QTableWidgetItem):
            return False

        my_val = self.data(Qt.UserRole)
        other_val = other.data(Qt.UserRole)

        # Both have numeric sort values → compare numerically
        if my_val is not None and other_val is not None:
            try:
                return float(my_val) < float(other_val)
            except (TypeError, ValueError):
                pass

        # Fallback: case-insensitive text comparison
        my_text = (self.text() or "").lower()
        other_text = (other.text() or "").lower()
        return my_text < other_text


class CheckableComboBox(QComboBox):
    """Multi-select combo box with checkboxes on each item.

    The first row is always a "Select All" toggle.  When no data items are
    checked the display reads *placeholder* (e.g. "All Dates") and
    ``checked_data()`` returns an empty list — meaning "no filter".

    Uses an event-filter approach so the popup stays open while toggling
    checkboxes but closes normally when clicking outside or pressing Escape.

    Signals:
        selection_changed: emitted whenever the set of checked items changes.
    """

    _SELECT_ALL = "__select_all__"

    selection_changed = Signal()

    def __init__(self, placeholder="All", parent=None):
        super().__init__(parent)
        self._placeholder = placeholder
        self._updating = False              # guard against recursive toggling
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self._model.dataChanged.connect(self._on_data_changed)

        # Editable line-edit for display text control (read-only to user).
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setAlignment(Qt.AlignCenter)
        self.lineEdit().setText(self._placeholder)

        # Event filter on popup viewport: toggle checks without closing.
        self.view().viewport().installEventFilter(self)

        # Light grey list background with white checkbox indicators.
        self.view().setStyleSheet("""
            QListView {
                background-color: #F7F7F7;
                outline: none;
                border-radius: 6px;
                padding: 4px;
            }
            QListView::item {
                background-color: #F7F7F7;
                border: none;
                padding: 5px 8px;
                min-height: 24px;
                border-radius: 4px;
            }
            QListView::item:hover {
                background-color: #E8E8EA;
            }
            QListView::indicator {
                width: 16px;
                height: 16px;
                background-color: #FFFFFF;
                border: 1px solid #BBBBBB;
                border-radius: 3px;
            }
            QListView::indicator:checked {
                background-color: #469a45;
                border: 1px solid #2b493b;
            }
            QListView::indicator:indeterminate {
                background-color: #8dc08c;
                border: 1px solid #469a45;
            }
        """)

    # -- public API ---------------------------------------------------

    def add_checkable_item(self, text, data=None):
        """Append a checkable item with optional userData."""
        item = QStandardItem(text)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Unchecked)
        item.setData(data, Qt.UserRole)
        self._model.appendRow(item)

    def set_items(self, items):
        """Replace all items.  *items* is a list of (text, userData) tuples.

        A "Select All" row is automatically prepended.
        """
        self._model.blockSignals(True)
        self._model.clear()

        # First row: Select All toggle
        sa = QStandardItem("Select All")
        sa.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        sa.setCheckState(Qt.Unchecked)
        sa.setData(self._SELECT_ALL, Qt.UserRole)
        self._model.appendRow(sa)

        for text, data in items:
            self.add_checkable_item(text, data)

        self._model.blockSignals(False)
        self._update_display()

    def checked_data(self):
        """Return list of userData values for every checked *data* item.

        Returns an empty list (meaning "no filter") when nothing is checked
        OR when everything is checked (i.e. Select All is on).
        """
        out = []
        total_data = 0
        for row in range(self._model.rowCount()):
            item = self._model.item(row)
            if item.data(Qt.UserRole) == self._SELECT_ALL:
                continue
            total_data += 1
            if item.checkState() == Qt.Checked:
                out.append(item.data(Qt.UserRole))
        # All checked = no filter, same as none checked
        if len(out) == total_data:
            return []
        return out

    def clear_checks(self):
        """Uncheck every item (resets to "All")."""
        self._model.blockSignals(True)
        for row in range(self._model.rowCount()):
            self._model.item(row).setCheckState(Qt.Unchecked)
        self._model.blockSignals(False)
        self._update_display()

    # -- event filter (keeps popup open on item click) ----------------

    def eventFilter(self, obj, event):
        """Intercept clicks on the popup list to toggle checks without closing."""
        if obj is self.view().viewport() and event.type() == QEvent.MouseButtonRelease:
            index = self.view().indexAt(event.pos())
            if index.isValid():
                item = self._model.itemFromIndex(index)
                if item and item.flags() & Qt.ItemIsUserCheckable:
                    new_state = Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
                    item.setCheckState(new_state)
                    return True          # consume → popup stays open
        return super().eventFilter(obj, event)

    # -- internals ----------------------------------------------------

    def _on_data_changed(self, top_left, bottom_right, roles):
        if self._updating:
            return
        self._updating = True

        changed_item = self._model.item(top_left.row())
        if changed_item and changed_item.data(Qt.UserRole) == self._SELECT_ALL:
            # "Select All" was toggled → apply its state to every data row
            target = changed_item.checkState()
            self._model.blockSignals(True)
            for row in range(1, self._model.rowCount()):
                self._model.item(row).setCheckState(target)
            self._model.blockSignals(False)
        else:
            # A data row changed → update "Select All" to reflect the group
            self._sync_select_all()

        self._update_display()
        self._updating = False
        self.selection_changed.emit()

    def _sync_select_all(self):
        """Set "Select All" to Checked if every data row is checked,
        Unchecked if none are, PartiallyChecked otherwise."""
        if self._model.rowCount() < 2:
            return
        sa = self._model.item(0)
        total = self._model.rowCount() - 1
        checked = sum(
            1 for row in range(1, self._model.rowCount())
            if self._model.item(row).checkState() == Qt.Checked
        )
        self._model.blockSignals(True)
        if checked == 0:
            sa.setCheckState(Qt.Unchecked)
        elif checked == total:
            sa.setCheckState(Qt.Checked)
        else:
            sa.setCheckState(Qt.PartiallyChecked)
        self._model.blockSignals(False)

    def _update_display(self):
        checked = []
        for row in range(self._model.rowCount()):
            item = self._model.item(row)
            if item.data(Qt.UserRole) == self._SELECT_ALL:
                continue
            if item.checkState() == Qt.Checked:
                checked.append(item.text())

        total_data = self._model.rowCount() - 1  # exclude Select All row
        if not checked or len(checked) == total_data:
            # Nothing checked or everything checked → show placeholder
            self.lineEdit().setText(self._placeholder)
        elif len(checked) == 1:
            self.lineEdit().setText(checked[0])
        else:
            self.lineEdit().setText("(Multiple)")


class _DateRangeDialog(QDialog):
    """Popup dialog with month/day/year dropdowns for selecting a date range."""

    _MONTHS = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]

    def __init__(self, from_date, to_date, min_date, max_date,
                 parent=None, range_active=False):
        super().__init__(parent)
        self.setWindowTitle("Select Date Range")
        self.setMinimumWidth(420)
        self._cleared = False

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # ── Whole-month quick pick (v2.1.0, Bryan QoL) ───────────
        # Month-end reconciliation is "show me June" — making the
        # coordinator type 6/1 and 6/30 is busywork.  Either/or
        # with the custom range: the radios gate which inputs are
        # live, and selected_from()/selected_to() answer from the
        # active mode, so callers see an ordinary date range.
        month_row = QHBoxLayout()
        month_row.setSpacing(8)
        self.month_mode = QRadioButton("Whole month:")
        self.month_mode.setStyleSheet(
            "font-weight: bold; font-size: 14px;")
        month_row.addWidget(self.month_mode)
        self.month_combo = NoScrollComboBox()
        _cursor = QDate(max_date.year(), max_date.month(), 1)
        _floor = QDate(min_date.year(), min_date.month(), 1)
        while _cursor >= _floor:
            self.month_combo.addItem(
                f"{self._MONTHS[_cursor.month() - 1]} "
                f"{_cursor.year()}",
                _cursor.toString("yyyy-MM"))
            _cursor = _cursor.addMonths(-1)
        month_row.addWidget(self.month_combo, 1)
        layout.addLayout(month_row)

        self.range_mode = QRadioButton("Custom range:")
        self.range_mode.setStyleSheet(
            "font-weight: bold; font-size: 14px;")
        self.range_mode.setChecked(True)
        layout.addWidget(self.range_mode)
        # Every control stays LIVE; interacting with one side
        # auto-selects its radio (a disabled-until-radio month
        # combo proved undiscoverable in the field — "the dropdown
        # isn't clickable", Sean 2026-06-12).  The radios indicate
        # which side Apply will use.
        self.month_combo.activated.connect(
            lambda _i: self.month_mode.setChecked(True))

        # ── Start Date row ────────────────────────────────────────
        self._start_lbl = QLabel("Start Date")
        self._start_lbl.setStyleSheet(
            "font-weight: bold; font-size: 14px;")
        layout.addWidget(self._start_lbl)

        start_row = QHBoxLayout()
        start_row.setSpacing(8)

        self.start_month = NoScrollComboBox()
        for m in self._MONTHS:
            self.start_month.addItem(m)
        self.start_month.setCurrentIndex(from_date.month() - 1)
        self.start_month.currentIndexChanged.connect(self._clamp_start_day)
        start_row.addWidget(self.start_month, 3)

        self.start_day = NoScrollSpinBox()
        self.start_day.setRange(1, from_date.daysInMonth())
        self.start_day.setValue(from_date.day())
        start_row.addWidget(self.start_day, 1)

        self.start_year = NoScrollSpinBox()
        self.start_year.setRange(min_date.year(), max_date.year())
        self.start_year.setValue(from_date.year())
        self.start_year.valueChanged.connect(self._clamp_start_day)
        start_row.addWidget(self.start_year, 2)

        layout.addLayout(start_row)

        # ── End Date row ──────────────────────────────────────────
        self._end_lbl = QLabel("End Date")
        self._end_lbl.setStyleSheet(
            "font-weight: bold; font-size: 14px;")
        layout.addWidget(self._end_lbl)

        end_row = QHBoxLayout()
        end_row.setSpacing(8)

        self.end_month = NoScrollComboBox()
        for m in self._MONTHS:
            self.end_month.addItem(m)
        self.end_month.setCurrentIndex(to_date.month() - 1)
        self.end_month.currentIndexChanged.connect(self._clamp_end_day)
        end_row.addWidget(self.end_month, 3)

        self.end_day = NoScrollSpinBox()
        self.end_day.setRange(1, to_date.daysInMonth())
        self.end_day.setValue(to_date.day())
        end_row.addWidget(self.end_day, 1)

        self.end_year = NoScrollSpinBox()
        self.end_year.setRange(min_date.year(), max_date.year())
        self.end_year.setValue(to_date.year())
        self.end_year.valueChanged.connect(self._clamp_end_day)
        end_row.addWidget(self.end_year, 2)

        layout.addLayout(end_row)

        # Editing any custom-range field auto-selects range mode
        # (counterpart of the month-combo connection above).
        for _w in (self.start_month, self.end_month):
            _w.activated.connect(
                lambda _i: self.range_mode.setChecked(True))
        for _w in (self.start_day, self.start_year,
                   self.end_day, self.end_year):
            _w.valueChanged.connect(
                lambda _v: self.range_mode.setChecked(True))

        # Default mode (Sean, 2026-06-12): WHOLE MONTH opens
        # active — it's the primary reconciliation flow ("when I
        # first open the window I want the custom range greyed
        # out").  Exceptions keep the dialog honest about the
        # CURRENT selection: an active range that is exactly a
        # whole calendar month opens in month mode with that month
        # preselected; any other active range opens in custom mode.
        if range_active:
            _first = QDate(from_date.year(), from_date.month(), 1)
            _last = QDate(_first.year(), _first.month(),
                          _first.daysInMonth())
            _ym_idx = self.month_combo.findData(
                _first.toString("yyyy-MM"))
            if (from_date == _first and to_date == _last
                    and _ym_idx >= 0):
                self.month_combo.setCurrentIndex(_ym_idx)
                self.month_mode.setChecked(True)
            else:
                self.range_mode.setChecked(True)
        else:
            self.month_mode.setChecked(True)

        # Active side highlighted, inactive side faded (Sean,
        # 2026-06-12 — "it's not clear which is taking effect").
        # The radios are exclusive, so one toggled hook covers both.
        self.month_mode.toggled.connect(self._update_mode_styles)
        self._update_mode_styles()

        # ── Validation message ────────────────────────────────────
        self._error_label = QLabel("")
        self._error_label.setStyleSheet(f"color: {ERROR_COLOR}; font-size: 12px;")
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        # ── Buttons ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        clear_btn = QPushButton("All Dates (Clear)")
        clear_btn.clicked.connect(self._on_clear)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        apply_btn = QPushButton("Apply Range")
        apply_btn.setObjectName("primary_btn")
        apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(apply_btn)

        layout.addLayout(btn_row)

    # ── Mode emphasis ─────────────────────────────────────────────
    _FADED_CSS = "color: #9A9A9A;"
    _FADED_FIELD_CSS = "color: #9A9A9A; background-color: #F5F4F1;"

    def _update_mode_styles(self):
        """Highlight the active mode's controls and FADE the
        inactive side's — so it's obvious which one Apply Range
        will use (Sean, 2026-06-12).

        The faded side stays ENABLED on purpose: disabling it
        brought back the dead-click trap ("the dropdown isn't
        clickable") — interacting with a faded control is exactly
        how you switch modes.  Fading is purely the visual cue.
        """
        month_on = self.month_mode.isChecked()
        radio_active = ("font-weight: bold; font-size: 14px; "
                        f"color: {PRIMARY_GREEN};")
        radio_idle = ("font-weight: bold; font-size: 14px; "
                      + self._FADED_CSS)
        self.month_mode.setStyleSheet(
            radio_active if month_on else radio_idle)
        self.range_mode.setStyleSheet(
            radio_idle if month_on else radio_active)
        self.month_combo.setStyleSheet(
            "" if month_on else self._FADED_FIELD_CSS)
        custom_css = self._FADED_FIELD_CSS if month_on else ""
        for w in (self.start_month, self.start_day, self.start_year,
                  self.end_month, self.end_day, self.end_year):
            w.setStyleSheet(custom_css)
        lbl_css = "font-weight: bold; font-size: 14px;"
        if month_on:
            lbl_css += " " + self._FADED_CSS
        self._start_lbl.setStyleSheet(lbl_css)
        self._end_lbl.setStyleSheet(lbl_css)

    # ── Day clamping (adjusts max day when month/year changes) ────
    def _clamp_start_day(self):
        month = self.start_month.currentIndex() + 1
        year = self.start_year.value()
        max_day = QDate(year, month, 1).daysInMonth()
        self.start_day.setMaximum(max_day)

    def _clamp_end_day(self):
        month = self.end_month.currentIndex() + 1
        year = self.end_year.value()
        max_day = QDate(year, month, 1).daysInMonth()
        self.end_day.setMaximum(max_day)

    # ── Actions ───────────────────────────────────────────────────
    def _on_apply(self):
        """Validate and accept."""
        f = self.selected_from()
        t = self.selected_to()
        if not f.isValid():
            self._error_label.setText("Start date is not valid.")
            self._error_label.setVisible(True)
            return
        if not t.isValid():
            self._error_label.setText("End date is not valid.")
            self._error_label.setVisible(True)
            return
        self._error_label.setVisible(False)
        self.accept()

    def _on_clear(self):
        self._cleared = True
        self.accept()

    def was_cleared(self):
        return self._cleared

    def selected_from(self):
        if self.month_mode.isChecked():
            ym = self.month_combo.currentData() or ''
            first = QDate.fromString(f"{ym}-01", "yyyy-MM-dd")
            if first.isValid():
                return first
            return QDate()   # invalid → _on_apply shows the error
        return QDate(
            self.start_year.value(),
            self.start_month.currentIndex() + 1,
            self.start_day.value(),
        )

    def selected_to(self):
        if self.month_mode.isChecked():
            first = self.selected_from()
            if first.isValid():
                return QDate(first.year(), first.month(),
                             first.daysInMonth())
            return QDate()
        return QDate(
            self.end_year.value(),
            self.end_month.currentIndex() + 1,
            self.end_day.value(),
        )


class DateRangeWidget(QWidget):
    """Date range picker that opens a popup dialog with month/day/year fields.

    Shows a clickable display field: "All Dates" when no range is active,
    or "M/d/yyyy – M/d/yyyy" when a range is selected.  Clicking the field
    opens a dialog with simple dropdowns and spin boxes for selecting
    start and end dates.

    Signals:
        range_changed: emitted whenever the effective date range changes.
    """

    range_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        from fam.utils.timezone import eastern_today
        _today = eastern_today()
        _qtoday = QDate(_today.year, _today.month, _today.day)
        self._from_date = _qtoday.addMonths(-6)
        self._to_date = _qtoday
        self._min_date = self._from_date
        self._max_date = self._to_date

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Clickable display field
        self._display = QLineEdit("All Dates")
        self._display.setReadOnly(True)
        self._display.setAlignment(Qt.AlignCenter)
        self._display.setCursor(Qt.PointingHandCursor)
        self._display.installEventFilter(self)
        layout.addWidget(self._display, 1)

    # -- event filter (click to open dialog) -------------------------------

    def eventFilter(self, obj, event):
        if obj is self._display and event.type() == QEvent.MouseButtonRelease:
            self._open_dialog()
            return True
        return super().eventFilter(obj, event)

    # -- public API --------------------------------------------------------

    def get_date_range(self):
        """Return (from_str, to_str) in 'yyyy-MM-dd' format, or (None, None)."""
        if not self._active:
            return None, None
        return (
            self._from_date.toString("yyyy-MM-dd"),
            self._to_date.toString("yyyy-MM-dd"),
        )

    def set_date_bounds(self, min_date_str, max_date_str):
        """Set the available date range from 'yyyy-MM-dd' strings."""
        qmin = QDate.fromString(min_date_str, "yyyy-MM-dd")
        qmax = QDate.fromString(max_date_str, "yyyy-MM-dd")
        if not qmin.isValid():
            from fam.utils.timezone import eastern_today
            _t = eastern_today()
            _qt = QDate(_t.year, _t.month, _t.day)
            qmin = _qt.addMonths(-6)
        if not qmax.isValid():
            from fam.utils.timezone import eastern_today
            _t = eastern_today()
            qmax = QDate(_t.year, _t.month, _t.day)
        self._min_date = qmin
        self._max_date = qmax
        self._from_date = qmin
        self._to_date = qmax

    def clear_range(self):
        """Return to "All Dates" (no filter) mode."""
        self._active = False
        self._display.setText("All Dates")
        self.range_changed.emit()

    # -- internals ---------------------------------------------------------

    def _open_dialog(self):
        dlg = _DateRangeDialog(
            self._from_date, self._to_date,
            self._min_date, self._max_date,
            parent=self.window(),
            range_active=self._active,
        )
        if dlg.exec() == QDialog.Accepted:
            if dlg.was_cleared():
                self.clear_range()
            else:
                self._from_date = dlg.selected_from()
                self._to_date = dlg.selected_to()
                # Swap if user picked end before start
                if self._from_date > self._to_date:
                    self._from_date, self._to_date = self._to_date, self._from_date
                self._active = True
                self._display.setText(
                    f"{self._from_date.toString('M/d/yyyy')}  –  "
                    f"{self._to_date.toString('M/d/yyyy')}"
                )
                self.range_changed.emit()


def make_item(text, sort_value=None):
    """Create a SortableTableItem with tooltip and optional numeric sort value.

    Args:
        text: Display text for the cell.
        sort_value: Numeric value for sorting (if None, sorts alphabetically).
    """
    item = SortableTableItem(str(text))
    item.setToolTip(str(text))
    if sort_value is not None:
        item.setData(Qt.UserRole, sort_value)
    return item


def make_field_label(text):
    """Create a styled field label that matches input field heights exactly.

    Uses compact padding so it visually aligns with QLineEdit / QComboBox / QSpinBox.
    """
    lbl = QLabel(text)
    lbl.setStyleSheet(f"""
        background-color: {FIELD_LABEL_BG};
        border: 1px solid #D5D2CB;
        border-radius: 4px;
        padding: 4px 8px;
        font-weight: bold;
        font-size: 12px;
        color: #555555;
    """)
    return lbl


def make_section_label(text):
    """Create a styled section header label for visual hierarchy.

    Used above tables, grouped controls, and content sections to provide
    a clear, consistent heading that volunteers can scan quickly.
    """
    from fam.ui.styles import SUBTITLE_GRAY
    lbl = QLabel(text)
    lbl.setStyleSheet(f"""
        font-size: 12px;
        font-weight: bold;
        color: {SUBTITLE_GRAY};
        padding: 2px 0px;
    """)
    return lbl


def make_action_btn(text, width=50, danger=False):
    """Create a compact action button for use inside table cells.

    Args:
        text: Button label.
        width: Fixed width in pixels.
        danger: If True, style with red text/border.
    """
    btn = QPushButton(text)
    btn.setFixedSize(width, 28)
    if danger:
        btn.setStyleSheet(
            ACTION_BTN_STYLE + f" color: {ERROR_COLOR}; border: 1px solid {ERROR_COLOR};"
        )
    else:
        btn.setStyleSheet(ACTION_BTN_STYLE)
    return btn


def configure_table(table, actions_col=None, actions_width=140,
                    resizable=False):
    """Configure a QTableWidget with sorting and column sizing.

    - Default (resizable=False): all data columns use Stretch mode (equal
      widths that fill the table).  Columns cannot be dragged.
    - resizable=True: columns use Interactive mode so the user can drag
      column borders to resize.  The last column stretches to fill
      remaining space.  Call ``table.resizeColumnsToContents()`` after
      populating data so initial widths match content.
    - If actions_col is set, that column is Fixed-width.
    - Sorting is enabled; clicking any column header toggles asc/desc.

    Args:
        table: QTableWidget to configure.
        actions_col: Column index of the fixed Actions column (or None).
        actions_width: Pixel width for the Actions column.
        resizable: If True, columns are manually resizable and auto-fit
            to content instead of using equal Stretch widths.
    """
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setItemDelegate(ColorPreservingDelegate(table))

    header = table.horizontalHeader()
    header.setSortIndicatorShown(True)
    header.setFixedHeight(36)

    if resizable:
        header.setStretchLastSection(True)
    else:
        header.setStretchLastSection(False)

    # Explicit header style — overrides any inherited QFrame padding/border
    # that cascades from parent frame stylesheets (QHeaderView is a QFrame
    # subclass, so parent `QFrame { padding: 12px; }` rules would clip text).
    header.setStyleSheet(f"""
        QHeaderView {{
            padding: 0px;
            border: none;
            background-color: transparent;
        }}
        QHeaderView::section {{
            background-color: #F5F5F5;
            color: {TEXT_COLOR};
            font-weight: bold;
            font-size: 12px;
            padding: 6px 10px;
            border: none;
            border-bottom: 2px solid {LIGHT_GRAY};
            border-right: 1px solid #ECECEC;
        }}
        QHeaderView::section:vertical {{
            background-color: {WHITE};
            border: none;
        }}
    """)

    col_count = table.columnCount()
    for i in range(col_count):
        if actions_col is not None and i == actions_col:
            header.setSectionResizeMode(i, QHeaderView.Fixed)
            table.setColumnWidth(i, actions_width)
        elif resizable:
            header.setSectionResizeMode(i, QHeaderView.Interactive)
        else:
            header.setSectionResizeMode(i, QHeaderView.Stretch)

    # Sorting is enabled but should be toggled off during population
    table.setSortingEnabled(True)
