"""Reusable payment method entry row widget."""

import logging
import os

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QFileDialog,
    QDialog, QDialogButtonBox, QMessageBox, QWidget, QSpinBox
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QStandardItemModel, QColor, QBrush
from fam.models.payment_method import get_all_payment_methods, get_payment_methods_for_market
from fam.utils.calculations import charge_to_method_amount, method_amount_to_charge
from fam.utils.money import dollars_to_cents, cents_to_dollars, format_dollars

logger = logging.getLogger('fam.ui.widgets.payment_row')
from fam.ui.styles import (
    LIGHT_GRAY, WHITE, ERROR_COLOR, SUBTITLE_GRAY, MEDIUM_GRAY,
    HARVEST_GOLD, ACCENT_GREEN, PRIMARY_GREEN,
)
from fam.ui.helpers import (
    NoScrollDoubleSpinBox, NoScrollSpinBox, NoScrollComboBox,
)


class DenominationStepper(QWidget):
    """A unit-count stepper for denominated payment methods.

    Shows [ − ] [ count ] [ + ]  $value  instead of a free-text dollar spinbox.
    Emits valueChanged(int) with the charge in integer cents (count × denomination).
    All monetary values (denomination, value, max_remaining) are integer cents.
    """

    valueChanged = Signal(int)

    def __init__(self, denomination: int = 100, parent=None):
        super().__init__(parent)
        self._denomination = denomination
        self._count = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Stepper elements use the exact same sizing rules as the Charge
        # spinbox (amount_spin) in PaymentRow so they naturally render at
        # the same height: border 2px + padding 4px top/bottom + min-height 22px.
        _shared_box = (
            f"border: 2px solid {HARVEST_GOLD}; border-radius: 6px; "
            f"background-color: {WHITE}; font-weight: bold; "
            f"padding: 4px 0px; min-height: 22px;"
        )

        self._minus_btn = QPushButton("−")
        self._minus_btn.setFixedWidth(26)
        self._minus_btn.setStyleSheet(f"""
            QPushButton {{ {_shared_box} font-size: 15px; color: {HARVEST_GOLD}; }}
            QPushButton:hover {{ background-color: {HARVEST_GOLD}; color: {WHITE}; }}
            QPushButton:pressed {{ background-color: {ACCENT_GREEN}; border-color: {ACCENT_GREEN}; color: {WHITE}; }}
            QPushButton:disabled {{ background-color: {LIGHT_GRAY}; border-color: {LIGHT_GRAY}; color: #aaa; }}
        """)
        self._minus_btn.clicked.connect(self._decrement)
        layout.addWidget(self._minus_btn)

        # NoScrollSpinBox (not raw QSpinBox) so the count field
        # gets the same overtype + cents-builder typing UX as every
        # other numeric input in the app — consistency was the
        # 2026-04-30 ask.  No-scroll behaviour also prevents the
        # mouse wheel from accidentally bumping the unit count
        # mid-payment.
        self._count_spin = NoScrollSpinBox()
        self._count_spin.setRange(0, 9999)
        self._count_spin.setValue(0)
        self._count_spin.setAlignment(Qt.AlignCenter)
        self._count_spin.setButtonSymbols(QSpinBox.NoButtons)
        self._count_spin.setFixedWidth(42)
        self._count_spin.setStyleSheet(f"""
            QSpinBox {{ {_shared_box} font-size: 14px; color: #222; }}
            QSpinBox:focus {{ border-color: {ACCENT_GREEN}; background-color: #FEFFFE; }}
        """)
        self._count_spin.valueChanged.connect(self._on_spin_changed)
        layout.addWidget(self._count_spin)

        self._plus_btn = QPushButton("+")
        self._plus_btn.setFixedWidth(26)
        self._plus_btn.setStyleSheet(f"""
            QPushButton {{ {_shared_box} font-size: 15px; color: {HARVEST_GOLD}; }}
            QPushButton:hover {{ background-color: {HARVEST_GOLD}; color: {WHITE}; }}
            QPushButton:pressed {{ background-color: {ACCENT_GREEN}; border-color: {ACCENT_GREEN}; color: {WHITE}; }}
            QPushButton:disabled {{ background-color: {LIGHT_GRAY}; border-color: {LIGHT_GRAY}; color: #aaa; }}
        """)
        self._plus_btn.clicked.connect(self._increment)
        layout.addWidget(self._plus_btn)

        self._dollar_label = QLabel("$0.00")
        self._dollar_label.setStyleSheet(
            f"font-weight: bold; color: {ACCENT_GREEN}; font-size: 13px; "
            f"padding: 4px 0px; min-height: 22px;"
        )
        self._dollar_label.setMinimumWidth(60)
        layout.addWidget(self._dollar_label)

    def setDenomination(self, denomination: int):
        """Update the denomination value (integer cents) and refresh display."""
        self._denomination = denomination
        self._refresh_display()

    def count(self) -> int:
        """Return the current unit count."""
        return self._count_spin.value()

    def value(self) -> int:
        """Return the current charge in integer cents (count × denomination)."""
        return self._count_spin.value() * self._denomination

    def setValue(self, cents: int):
        """Set the count from a cents amount (reverse-engineers count)."""
        if self._denomination > 0:
            c = max(0, int(cents / self._denomination))
        else:
            c = 0
        self._count_spin.blockSignals(True)
        self._count_spin.setValue(c)
        self._count_spin.blockSignals(False)
        self._refresh_display()

    def setCount(self, count: int):
        """Set the unit count directly."""
        self._count_spin.setValue(max(0, count))

    def _on_spin_changed(self, val):
        """Called when user types a value or spinbox changes."""
        self._refresh_display()
        self.valueChanged.emit(self.value())

    def _increment(self):
        self._count_spin.setValue(self._count_spin.value() + 1)

    def _decrement(self):
        v = self._count_spin.value()
        if v > 0:
            self._count_spin.setValue(v - 1)

    def setMaxCharge(self, max_remaining: int):
        """Cap stepper based on remaining order balance (integer cents).

        For denominated methods, max_remaining is the raw remaining balance
        (not divided by match) — the customer's check is the denomination,
        and the match flexes to fit.

        Blocks signals to prevent cascading updates when setMaximum()
        clamps the current value.
        """
        if self._denomination > 0:
            max_count = max(0, int(max_remaining / self._denomination))
        else:
            max_count = 0
        self._count_spin.blockSignals(True)
        self._count_spin.setMaximum(max_count)
        self._count_spin.blockSignals(False)
        self._refresh_display()

    def _refresh_display(self):
        self._dollar_label.setText(format_dollars(self.value()))
        self._minus_btn.setEnabled(self._count_spin.value() > 0)
        self._plus_btn.setEnabled(
            self._count_spin.value() < self._count_spin.maximum()
        )


class PaymentRow(QFrame):
    """A single payment method entry row.

    Parameters
    ----------
    market_id : int, optional
        Constrains the method dropdown to methods registered for this
        market.
    single_vendor_mode : bool, default False
        When True the per-row vendor dropdown is hidden entirely.  Used
        by AdjustmentDialog (which always edits a single transaction —
        no vendor ambiguity) and as the natural fall-back for
        single-transaction orders on the Payment screen.

    Vendor binding (denominated rows)
    ---------------------------------
    Denominated payment methods (Food Bucks, FMNP-as-payment) bind to
    a single vendor at capture time so reimbursement reports attribute
    the physical instrument to the correct vendor.  Callers populate
    the vendor pool via :meth:`set_order_vendors`; the row then filters
    that pool by per-vendor eligibility (``vendor_payment_methods``)
    and shows an inline dropdown next to the method combo whenever the
    selected method has a denomination.
    """

    changed = Signal()
    remove_requested = Signal(object)  # emits self
    # v2.0.7+ user-cap radio-button (user-reported 2026-05-07):
    # emitted when this row's ⚡ toggle flips from Locked → Active.
    # The PaymentScreen connects this to a handler that Locks all
    # OTHER non-denom rows, enforcing the "exactly one overflow
    # target" invariant.  Auto-Distribute then has a single,
    # unambiguous row to absorb the remainder.
    auto_distribute_activated = Signal(object)  # emits self

    def __init__(self, parent=None, market_id=None,
                 single_vendor_mode: bool = False):
        super().__init__(parent)
        self._market_id = market_id
        self._single_vendor_mode = bool(single_vendor_mode)
        # Order-level vendor pool (set by parent screen via
        # set_order_vendors).  When the selected method is denominated
        # this list is intersected with vendor-level eligibility to
        # populate the per-row vendor dropdown.
        self._order_vendors: list[dict] = []
        # v2.0.7+ user-cap (user-reported 2026-05-07): when the user
        # types a charge into a non-denom row's amount_spin, mark
        # this row as "user-capped" — the engine's cap-aware Pass 4
        # give-back must NOT inflate this row to absorb the budget,
        # and the UI write-back must NOT clobber the typed value
        # with the engine's inflated value.  Pre-fix, typing $125
        # SNAP for a customer who only had $125 on their EBT card
        # caused the engine to silently inflate to $138.09 ("absorb
        # the rest") instead of letting Remaining > 0 surface so
        # the volunteer could add a Cash row for the gap.
        # Reset when the method changes (different method = different
        # constraint) or when Auto-Distribute runs (an explicit user
        # request to redistribute everything).
        self._user_capped = False
        self.setStyleSheet(f"""
            PaymentRow {{
                background-color: {WHITE};
                border: 1px solid {LIGHT_GRAY};
                border-radius: 8px;
            }}
        """)

        layout = QHBoxLayout(self)
        # Tighter inner padding + spacing per the v1.9.9 row redesign so
        # the new vendor dropdown fits inline without forcing horizontal
        # scrollbars on typical laptop displays.
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        # Payment method combo
        self.method_combo = NoScrollComboBox()
        self.method_combo.setMinimumWidth(160)
        self._load_methods()
        self.method_combo.currentIndexChanged.connect(self._on_changed)
        layout.addWidget(self.method_combo)

        # Match percent label
        self.match_label = QLabel("0%")
        self.match_label.setMinimumWidth(40)
        self.match_label.setStyleSheet(
            f"font-weight: bold; color: {SUBTITLE_GRAY};")
        layout.addWidget(self.match_label)

        # Vendor combo — visible only when a denominated method is
        # selected AND single_vendor_mode is False AND there are
        # multiple vendors in the order.  Same minimum width as the
        # method combo so the row reads as a uniform "method | vendor"
        # pair when both are visible.
        self.vendor_combo = NoScrollComboBox()
        self.vendor_combo.setMinimumWidth(160)
        self.vendor_combo.setToolTip(
            "Vendor receiving this denominated payment.  Only vendors "
            "registered for the selected method appear here — configure "
            "eligibility in Settings → Vendors → Methods.")
        self.vendor_combo.currentIndexChanged.connect(
            lambda _: self.changed.emit())
        self.vendor_combo.setVisible(False)
        layout.addWidget(self.vendor_combo)

        # Charge input — what the customer pays for this payment method
        amount_label = QLabel("Charge:")
        amount_label.setStyleSheet(
            f"font-weight: bold; color: {HARVEST_GOLD};")
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
        # v2.0.7+ user-cap: amount_spin.valueChanged is user-only
        # (programmatic writes via _set_active_charge block signals
        # on this spinbox), so we can use it as a reliable signal
        # that the user has explicitly typed a charge.  Mark the
        # row as user-capped FIRST (so _on_changed sees the flag
        # when it dispatches the engine recalc), then delegate.
        self.amount_spin.valueChanged.connect(
            self._on_amount_user_changed)
        layout.addWidget(self.amount_spin)

        # v2.0.7+ auto-distribute toggle (user-reported 2026-05-07).
        # Visible only on non-denom rows.  Two states:
        #   * Active (green ⚡): the row is in Auto-Distribute's
        #     redistribution pool — clicking Auto-Distribute will
        #     reset and refill it.  This is the default for newly-
        #     added rows.
        #   * Locked (amber ⚡): the row's value is pinned by the
        #     volunteer — Auto-Distribute will SKIP it and
        #     redistribute around it.  Set automatically when the
        #     volunteer types into the amount field; can be
        #     toggled back to Active by clicking the icon (no need
        #     to delete + re-add the row to release the cap).
        # Denom rows always hide this button — physical scrip is
        # inherently locked by its tangible nature; the stepper
        # already conveys that.
        self.auto_distribute_btn = QPushButton("⚡")  # ⚡
        self.auto_distribute_btn.setFixedSize(28, 28)
        self.auto_distribute_btn.setCursor(Qt.PointingHandCursor)
        self.auto_distribute_btn.clicked.connect(
            self._on_auto_distribute_btn_clicked)
        # Hidden until a non-denom method is selected (see
        # _update_input_mode for the visibility rule).  When
        # revealed, the initial style reflects user_capped=False
        # (Active state) — fresh rows are in Auto-Distribute's
        # pool until the volunteer types.
        self.auto_distribute_btn.setVisible(False)
        self._refresh_auto_distribute_btn_style()
        layout.addWidget(self.auto_distribute_btn)

        # Denomination hint (visible when method has denomination set but stepper not active)
        self.denom_hint = QLabel("")
        self.denom_hint.setStyleSheet(
            f"font-weight: bold; color: {ACCENT_GREEN}; font-size: 13px;")
        self.denom_hint.setVisible(False)
        layout.addWidget(self.denom_hint)

        # Denomination stepper — replaces the dollar spinbox for denominated methods
        self._stepper = DenominationStepper(denomination=100, parent=self)
        self._stepper.setVisible(False)
        self._stepper.valueChanged.connect(self._on_changed)
        layout.addWidget(self._stepper)
        self._stepper_active = False

        # Computed fields — labels are now compact, reducing the wasted
        # whitespace circled in the v1.9.9 mockup.
        match_lbl = QLabel("Match:")
        match_lbl.setStyleSheet(f"color: {SUBTITLE_GRAY}; font-size: 12px;")
        layout.addWidget(match_lbl)
        self.match_amount_label = QLabel("$0.00")
        self.match_amount_label.setStyleSheet("font-weight: bold;")
        self.match_amount_label.setMinimumWidth(60)
        layout.addWidget(self.match_amount_label)

        total_lbl = QLabel("Total:")
        total_lbl.setStyleSheet(f"color: {SUBTITLE_GRAY}; font-size: 12px;")
        layout.addWidget(total_lbl)
        self.total_label = QLabel("$0.00")
        self.total_label.setStyleSheet("font-weight: bold;")
        self.total_label.setMinimumWidth(60)
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
        """Populate the method dropdown.

        The pool is filtered in two passes:

        1. **Market eligibility** — only methods registered for this
           row's ``_market_id`` (existing behavior).
        2. **Vendor eligibility** (v1.9.9+) — when
           :meth:`set_order_vendors` has been called, additionally
           filter to methods that *at least one* vendor in the order
           is registered for via ``vendor_payment_methods``.  For
           single-vendor orders this collapses to "only methods the
           lone vendor accepts" — which prevents the volunteer from
           selecting JH Food Bucks for a vendor that isn't registered
           for it (the v1.9.9-onsite bug).
        """
        # Remember the user's current selection so we can restore it
        # after re-loading (set_order_vendors triggers re-load).
        current_id = self.get_selected_method_id()
        # Block signals during the reload so we don't fire spurious
        # ``currentIndexChanged`` events that would re-enter
        # ``_on_changed`` while we're rebuilding state.
        self.method_combo.blockSignals(True)
        try:
            self.method_combo.clear()
            self.method_combo.addItem("Select Payment Type...")
            # ``include_system=False`` filters out system-managed
            # methods (Unallocated Funds, schema v25+) so coordinators
            # can't pick them manually from the dropdown.  Unallocated
            # Funds is only ever auto-injected by the Adjustments
            # "customer gone" path — letting it appear here would
            # turn a controlled FAM-absorbs-loss recovery into an
            # ad-hoc workaround for "I don't feel like collecting".
            if self._market_id:
                methods = get_payment_methods_for_market(
                    self._market_id, active_only=True,
                    include_system=False)
                if not methods:
                    methods = get_all_payment_methods(
                        active_only=True, include_system=False)
            else:
                methods = get_all_payment_methods(
                    active_only=True, include_system=False)

            # Vendor-eligibility narrowing.  Skip when there's no
            # order context yet (e.g. PaymentRow constructed before
            # the screen has loaded an order) — the parent screen
            # will call set_order_vendors after the order loads,
            # which triggers a re-load of this combo with the filter
            # applied.
            #
            # Graceful fallback: if NO vendor in the pool has any
            # vendor_payment_methods rows configured at all, skip the
            # filter entirely.  Treats an uninitialized eligibility
            # table as "permissive — anything goes" (matches the v24
            # migration's permissive default and avoids hiding every
            # method from databases that pre-date vendor-eligibility
            # configuration).
            if self._order_vendors:
                eligible_pm_ids = self._collect_eligible_pm_ids()
                if eligible_pm_ids is not None:
                    methods = [m for m in methods if m['id'] in eligible_pm_ids]

            for m in methods:
                self.method_combo.addItem(
                    f"{m['name']} ({m['match_percent']:.0f}% match)",
                    userData=m,
                )

            # Restore prior selection if still present in the filtered
            # pool, otherwise fall back to placeholder.
            if current_id is not None:
                for i in range(self.method_combo.count()):
                    data = self.method_combo.itemData(i)
                    if data and data.get('id') == current_id:
                        self.method_combo.setCurrentIndex(i)
                        break
        finally:
            self.method_combo.blockSignals(False)

    def _collect_eligible_pm_ids(self):
        """Return the set of payment_method ids registered for at least
        one vendor in the current order pool.

        Returns ``None`` when **no** vendor in the pool has any
        vendor_payment_methods rows — this is the graceful permissive
        fallback for uninitialized eligibility tables (legacy data,
        pre-v1.9.9 test fixtures, etc.).  Callers should treat ``None``
        as "do not filter".
        """
        if not self._order_vendors:
            return None
        try:
            from fam.models.payment_method import (
                get_vendor_payment_method_ids,
            )
        except ImportError:
            return None
        out: set[int] = set()
        any_configured = False
        for v in self._order_vendors:
            ids = get_vendor_payment_method_ids(v['id'])
            if ids:
                any_configured = True
            out |= ids
        return out if any_configured else None

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
        self._update_input_mode()
        self._update_photo_button()
        self._refresh_vendor_combo()
        self._recompute()
        self.changed.emit()

    # ── User-cap on charge (v2.0.7+) ────────────────────────────────

    def _on_amount_user_changed(self, _val):
        """User typed a value into amount_spin → mark the row as
        user-capped so the engine respects it as a hard maximum.

        Programmatic writes via ``_set_active_charge`` block signals
        on amount_spin, so this handler fires only for genuine user
        edits.

        The cap survives method changes — a volunteer who typed
        $125 before picking SNAP (or vice-versa) intends $125 to
        stick once the method is selected.  The cap can be cleared
        explicitly by clicking the row's auto-distribute toggle
        (⚡ icon) — no need to delete and re-add the row."""
        self._user_capped = True
        self._refresh_auto_distribute_btn_style()
        self._on_changed()

    def _on_auto_distribute_btn_clicked(self):
        """Toggle the row's auto-distribute eligibility.

        Active → Locked: pin the row's current value (the
        volunteer wants Auto-Distribute to stop touching it).

        Locked → Active: release the cap AND become the single
        overflow target.  Emits ``auto_distribute_activated`` so
        the PaymentScreen can Lock all OTHER non-denom rows
        (radio-button semantics — exactly one overflow target).

        This lets the volunteer change their mind without
        deleting and re-adding the row, and ensures Auto-
        Distribute always has an unambiguous target."""
        was_locked = self._user_capped
        self._user_capped = not self._user_capped
        self._refresh_auto_distribute_btn_style()
        if was_locked and not self._user_capped:
            # Locked → Active: claim the overflow-target role.
            # Screen handler locks the previously-Active row.
            self.auto_distribute_activated.emit(self)
        # Notify the screen so any downstream summary / breakdown
        # recompute reflects the change immediately.
        self.changed.emit()

    def _refresh_auto_distribute_btn_style(self):
        """Update the ⚡ icon's visual state to match
        ``_user_capped``.

        Two visual states with SOLID fill colours so the state
        is unmistakable at a glance across multiple rows:

          * Active (user_capped=False): solid green ⚡ on white,
            tooltip says "Auto-Distribute will fill this row".
          * Locked (user_capped=True): solid grey ⚡ on white,
            tooltip says "Locked — Auto-Distribute will skip".

        Grey (not orange/warning) is the right semantic for the
        locked state — locking a row isn't a warning, it's a
        neutral "this row is fixed by the volunteer, leave it
        alone" signal.  Reserves the warm/orange palette for
        actual problems (cap warnings, validation errors)."""
        if not hasattr(self, 'auto_distribute_btn'):
            return
        if self._user_capped:
            # Locked — SOLID medium-grey fill, white ⚡, hover
            # darkens.  Reads as neutral/inactive (the row is
            # disabled from Auto-Distribute's perspective).
            self.auto_distribute_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {MEDIUM_GRAY};
                    color: white;
                    border: 1.5px solid {MEDIUM_GRAY};
                    border-radius: 14px;
                    font-weight: bold;
                    font-size: 13px;
                    padding: 0px;
                }}
                QPushButton:hover {{
                    background-color: {SUBTITLE_GRAY};
                    border-color: {SUBTITLE_GRAY};
                }}
            """)
            self.auto_distribute_btn.setToolTip(
                "Locked — Auto-Distribute will skip this row.\n"
                "Click to release: the next Auto-Distribute will "
                "refill it.")
        else:
            # Active — SOLID green fill, white ⚡, hover darkens.
            self.auto_distribute_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {ACCENT_GREEN};
                    color: white;
                    border: 1.5px solid {ACCENT_GREEN};
                    border-radius: 14px;
                    font-weight: bold;
                    font-size: 13px;
                    padding: 0px;
                }}
                QPushButton:hover {{
                    background-color: {PRIMARY_GREEN};
                    border-color: {PRIMARY_GREEN};
                }}
            """)
            self.auto_distribute_btn.setToolTip(
                "Auto-Distribute will fill this row.\n"
                "Click to lock the current value (Auto-Distribute "
                "will then skip it).")

    def is_user_capped(self) -> bool:
        """True when the user has explicitly typed a charge into
        this row's amount_spin since the last cap-clear.  The
        engine and UI must honour the typed value and not inflate
        it during cap-aware give-back."""
        return self._user_capped

    def clear_user_cap(self):
        """Clear the user-cap flag.  Currently unused by Auto-
        Distribute (it skips user-capped rows instead of
        clearing).  Reserved for explicit reset paths."""
        self._user_capped = False
        self._refresh_auto_distribute_btn_style()

    # ── Vendor binding (denominated rows only) ──────────────────────

    def set_order_vendors(self, vendors: list):
        """Provide the pool of vendors that appear on the current order.

        Triggers two filters:

        * The **method dropdown** is re-loaded with vendor-eligibility
          applied — methods that no vendor in the order is registered
          for are excluded entirely.  For single-vendor orders this
          locks the dropdown to that vendor's eligible methods.
        * The per-row **vendor dropdown** (visible only when a
          denominated method is selected on a multi-vendor order) is
          repopulated with the eligible-vendor subset.

        Pass ``[]`` to clear the pool (e.g. when no order is loaded);
        the method combo falls back to the unfiltered market-level
        pool and the vendor combo hides.
        """
        self._order_vendors = list(vendors or [])
        # Re-filter the method dropdown so ineligible methods drop
        # out the moment the order context is known.
        self._load_methods()
        self._refresh_vendor_combo()

    def get_bound_vendor_id(self):
        """Return the vendor id this row is bound to, or None.

        Returns None when:
        - the row's method is non-denominated (binding is implicit
          via proportional distribution at save time),
        - the row is in single_vendor_mode (AdjustmentDialog), or
        - the order has only one vendor (binding is implicit).

        We check the same state used to decide combo *visibility*
        rather than Qt's ``isVisible()`` runtime flag — the latter
        only flips True after a parent is shown, which makes
        headless-test assertions unreliable.
        """
        method = self.get_selected_method()
        is_denom = bool(
            method and method.get('denomination')
            and method['denomination'] > 0
        )
        if not is_denom:
            return None
        if self._single_vendor_mode:
            return None
        if len(self._order_vendors) <= 1:
            return None
        data = self.vendor_combo.currentData()
        return data['id'] if data else None

    def set_bound_vendor_id(self, vendor_id):
        """Programmatically select a vendor in the dropdown.

        Used by draft restore so reloaded denominated rows return to
        their original vendor binding.  Falls back to the placeholder
        if the vendor isn't in the current eligible pool.
        """
        if vendor_id is None:
            self.vendor_combo.setCurrentIndex(0)
            return
        for i in range(self.vendor_combo.count()):
            data = self.vendor_combo.itemData(i)
            if data and data.get('id') == vendor_id:
                self.vendor_combo.setCurrentIndex(i)
                return
        # Vendor not in current eligible pool — leave at placeholder
        # so the eligibility guard (Layer 2 confirm-time check) flags
        # it for the volunteer.
        self.vendor_combo.setCurrentIndex(0)

    def _refresh_vendor_combo(self):
        """Repopulate the vendor combo against the current method.

        Called from ``_on_changed`` whenever the user switches methods,
        and from ``set_order_vendors`` when the parent screen updates
        the vendor pool.  Preserves the user's current selection when
        possible — only resets to placeholder if the previously-bound
        vendor is no longer in the eligible pool.
        """
        method = self.get_selected_method()
        is_denom = bool(method and method.get('denomination')
                         and method['denomination'] > 0)

        # Determine visibility:
        # - hide entirely in single_vendor_mode (AdjustmentDialog)
        # - hide when method is non-denominated (binding is implicit)
        # - hide when there's only one vendor in the order pool
        #   (auto-bound; no choice to make)
        should_show = (is_denom
                        and not self._single_vendor_mode
                        and len(self._order_vendors) > 1)
        self.vendor_combo.setVisible(should_show)
        if not should_show:
            return

        # Filter the pool by per-vendor eligibility.
        try:
            from fam.models.payment_method import (
                get_eligible_vendors_for_payment_method,
            )
            pool_ids = [v['id'] for v in self._order_vendors]
            eligible = get_eligible_vendors_for_payment_method(
                method['id'], pool_ids
            )
        except Exception:
            # Fall back to the full order pool if the eligibility query
            # fails for any reason — safer to show too many vendors than
            # to lock the volunteer out entirely.  Layer 2 confirm-time
            # guard still catches an ineligible binding.
            eligible = list(self._order_vendors)

        # Preserve current selection if it's still in the eligible set.
        current_id = self.get_bound_vendor_id()
        # Setting the combo programmatically must not refire the parent
        # ``changed`` signal — block it during the rebuild.
        self.vendor_combo.blockSignals(True)
        try:
            self.vendor_combo.clear()
            self.vendor_combo.addItem("Select vendor…")  # placeholder
            for v in eligible:
                self.vendor_combo.addItem(v['name'], userData=v)
            if current_id is not None:
                for i in range(self.vendor_combo.count()):
                    data = self.vendor_combo.itemData(i)
                    if data and data.get('id') == current_id:
                        self.vendor_combo.setCurrentIndex(i)
                        break
                else:
                    self.vendor_combo.setCurrentIndex(0)
        finally:
            self.vendor_combo.blockSignals(False)

    def _update_input_mode(self):
        """Swap between stepper (denominated) and spinbox (non-denominated)."""
        method = self.get_selected_method()
        if method and method.get('denomination') and method['denomination'] > 0:
            denom = method['denomination']  # integer cents
            if not self._stepper_active:
                # Transfer current charge to stepper (spinbox is dollars → cents)
                current_cents = dollars_to_cents(self.amount_spin.value())
                self._stepper.setDenomination(denom)
                self._stepper.setValue(current_cents)
                self.amount_spin.setVisible(False)
                self.denom_hint.setVisible(False)
                self._stepper.setVisible(True)
                self._stepper_active = True
            else:
                # Already active — just update denomination if it changed
                self._stepper.setDenomination(denom)
        else:
            if self._stepper_active:
                # Transfer stepper value back to spinbox (cents → dollars)
                current_cents = self._stepper.value()
                self.amount_spin.blockSignals(True)
                self.amount_spin.setValue(cents_to_dollars(current_cents))
                self.amount_spin.blockSignals(False)
                self._stepper.setVisible(False)
                self.denom_hint.setVisible(False)
                self.amount_spin.setVisible(True)
                self.amount_spin.setSingleStep(1.00)
                self._stepper_active = False
            else:
                self.denom_hint.setVisible(False)
                self.amount_spin.setSingleStep(1.00)
        # v2.0.7+ auto-distribute toggle: visible only on non-
        # denom methods (denom rows are inherently locked by
        # physical scrip — the stepper conveys that already, the
        # ⚡ toggle would be confusing).  Also hide when no
        # method is selected (the placeholder row's intent is
        # ambiguous — show the toggle once a real method is
        # picked).
        if hasattr(self, 'auto_distribute_btn'):
            is_real_non_denom = bool(
                method
                and not (method.get('denomination')
                         and method['denomination'] > 0))
            self.auto_distribute_btn.setVisible(is_real_non_denom)

    def _get_active_charge(self) -> int:
        """Return charge in integer cents from whichever input is active."""
        if self._stepper_active:
            return self._stepper.value()
        return dollars_to_cents(self.amount_spin.value())

    def _set_active_charge(self, cents: int):
        """Set charge in integer cents on whichever input is active."""
        if self._stepper_active:
            self._stepper.setValue(cents)
        else:
            self.amount_spin.blockSignals(True)
            self.amount_spin.setValue(cents_to_dollars(cents))
            self.amount_spin.blockSignals(False)

    def set_max_charge(self, max_charge_cents: int):
        """Cap the input to prevent exceeding remaining order balance.

        *max_charge_cents* is in integer cents.
        Blocks signals to prevent cascading updates when setMaximum()
        clamps the current value.

        Note: Qt's ``setMaximum()`` will silently clamp the current
        value if it exceeds the new max.  This is intentional for
        cap-aware write-back paths (where the engine adjusts
        customer_charged due to a daily-match-cap, and the spinbox
        must follow).  Upstream callers (``_push_row_limits``,
        AdjustmentDialog ``_update_row_caps``) are responsible for
        computing a correct max that reflects the row's *actual*
        constraint — the v1.9.10 fix in ``_push_row_limits`` removed
        the obsolete ``legacy_order_remaining`` floor for bound
        denom rows so they no longer get a phantom max=0 when
        cap-inflated non-denom rows over-count consumption.

        v2.0.7+ user-cap (user-reported 2026-05-07): for non-denom
        rows the user has explicitly capped (typed a value or
        toggled the ⚡ icon to Locked), the max is FLOORED at the
        row's current charge.  Without this, _push_row_limits
        running after Auto-Distribute would compute Cash's max as 0
        (because SNAP absorbed the whole budget) and Qt's silent
        setMaximum() clamp would zero out the volunteer's typed
        $50.  This is the lowest-layer defence — protects against
        any current OR future caller passing a sub-current max for
        a user-capped row.
        """
        if self._stepper_active:
            self._stepper.setMaxCharge(max_charge_cents)
        else:
            if (getattr(self, '_user_capped', False)
                    and not self._stepper_active):
                # Floor the max at the row's current charge so a
                # tighter computed max (e.g. from _push_row_limits
                # after another row absorbed the budget) doesn't
                # silently clamp the volunteer's typed value.  The
                # max can still RAISE above current — that just
                # gives the spinbox more headroom, no clamping.
                current_cents = dollars_to_cents(
                    self.amount_spin.value())
                if max_charge_cents < current_cents:
                    max_charge_cents = current_cents
            self.amount_spin.blockSignals(True)
            self.amount_spin.setMaximum(max(cents_to_dollars(max_charge_cents), 0.0))
            self.amount_spin.blockSignals(False)

    def _recompute(self):
        """Recompute FAM Match and Total from the charge amount input."""
        method = self.get_selected_method()
        charge = self._get_active_charge()  # integer cents
        match_pct = method['match_percent'] if method else 0.0
        total = charge_to_method_amount(charge, match_pct)  # integer cents
        match_amt = total - charge  # integer cents
        self.match_amount_label.setText(format_dollars(match_amt))
        self.total_label.setText(format_dollars(total))

    def get_selected_method(self):
        return self.method_combo.currentData()

    def get_data(self):
        """Return payment data with all monetary values in integer cents.

        ``bound_vendor_id`` carries the per-row vendor binding for
        denominated payments; it's ``None`` for non-denominated rows
        and for rows operating in single_vendor_mode (where the binding
        is implicit in the caller's transaction context).
        """
        method = self.get_selected_method()
        if not method:
            return None
        charge = self._get_active_charge()  # integer cents
        match_pct = method['match_percent']
        method_amount = charge_to_method_amount(charge, match_pct)  # integer cents
        match_amount = method_amount - charge  # integer cents
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
            'bound_vendor_id': self.get_bound_vendor_id(),
            # Carrying the denomination through to the save path lets the
            # rearchitected _distribute_and_save_payments distinguish
            # denominated rows (commit entire amount to a single vendor's
            # transaction) from non-denominated rows (proportional split
            # against per-vendor remaining balance).  None / 0 means
            # non-denominated.
            'denomination': method.get('denomination'),
            # v2.0.7 (schema v36): preserve any prior Phase B
            # token-value forfeit through Adjustment round-trips.
            # set_data() stashes the value here on load; the
            # forfeit pass updates it during edits; get_data()
            # returns it so the save path persists the latest.
            'customer_forfeit_cents': getattr(
                self, '_customer_forfeit_cents', 0),
            # v2.0.7+ user-cap (user-reported 2026-05-07): True
            # when the user has explicitly typed this row's
            # charge.  The engine's Pass 4 cap-aware give-back
            # MUST skip user-capped rows (don't inflate their
            # customer_charged to absorb match-cap shrinkage),
            # and the UI write-back MUST NOT clobber the typed
            # value with the engine's inflated value.  Effect:
            # the typed amount survives recalculation; any
            # under-coverage surfaces as Remaining > 0 so the
            # volunteer can add another row to absorb the gap
            # (e.g. Cash for the rest of the receipt).
            'user_capped': bool(self._user_capped),
        }

    def get_selected_method_id(self):
        """Return the ID of the currently selected payment method, or None."""
        method = self.get_selected_method()
        return method['id'] if method else None

    def has_method_selected(self):
        """Return True if a real payment method is selected (not the placeholder)."""
        return self.get_selected_method() is not None

    def reset_to_default(self):
        """Wipe the row back to its initial empty state.

        Used by the Payment screen when the volunteer clicks the red X
        on the only row in the order — prior versions silently no-op'd
        because there must always be ≥1 row, leaving the volunteer to
        manually clear each field.  This restores intuitive behavior:
        X always *means something*, even on the last row.

        Clears: method selection, charge amount, vendor binding, photo
        attachments, denomination stepper state.
        """
        # Block signals during the reset so we don't fire a cascade of
        # change emissions for each cleared widget — emit once at the end.
        widgets_to_block = [self, self.method_combo, self.amount_spin,
                            self.vendor_combo, self._stepper]
        for w in widgets_to_block:
            try:
                w.blockSignals(True)
            except Exception:
                pass
        try:
            self.method_combo.setCurrentIndex(0)  # placeholder
            self.amount_spin.setValue(0.0)
            self._stepper.setValue(0)
            self._stepper_active = False
            self._stepper.setVisible(False)
            self.amount_spin.setVisible(True)
            self.denom_hint.setVisible(False)
            self.vendor_combo.setCurrentIndex(0)
            self.vendor_combo.setVisible(False)
            # Clear photo state
            self._photo_source_paths = []
            self._expected_photo_count = 0
            self._style_photo_btn(attached=False)
            self.photo_btn.setVisible(False)
            # Reset display labels
            self.match_label.setText("--")
            self.match_amount_label.setText("$0.00")
            self.total_label.setText("$0.00")
        finally:
            for w in widgets_to_block:
                try:
                    w.blockSignals(False)
                except Exception:
                    pass
        self.changed.emit()

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
        """Override the displayed match and total values (e.g., after cap).

        Values are in integer cents.
        """
        self.match_amount_label.setText(format_dollars(match_amount))
        self.total_label.setText(format_dollars(total))

    def set_data(self, payment_method_id, method_amount,
                 customer_charged=None, bound_vendor_id=None,
                 customer_forfeit_cents=0, user_capped=False):
        """Set the row from existing data (DB row).

        Parameters
        ----------
        payment_method_id : int
            Selects the matching method in the combo box.
        method_amount : int
            Total allocation in cents (charge + FAM match).
        customer_charged : int, optional
            The actual customer charge in cents.  When provided this is
            written to the spinbox directly — preserving cap-inflated
            charges across save/reload cycles.  When ``None`` (legacy
            callers), falls back to deriving the charge from
            ``method_amount`` via the inverse of charge_to_method_amount.
        bound_vendor_id : int, optional
            For denominated draft restore — selects the saved vendor
            binding in the row's vendor dropdown.  When ``None`` the
            dropdown stays at its placeholder.
        customer_forfeit_cents : int, optional
            v2.0.7 (schema v36): Phase B token-value forfeit
            preserved on this row from a prior save.  Stored on the
            widget so ``get_data()`` can return it on the next save
            cycle (preserves forfeit through Adjustment edits that
            don't change the denomination).  Default 0 (no forfeit).
        user_capped : bool, optional
            v2.0.7+ (schema v37, audit 2026-05-07): restore the
            user-cap flag on a row loaded from DB.  When True, the
            row comes back Locked (gold ⚡) — Auto-Distribute
            skips it and the engine preserves customer_charged
            across cap-aware passes.  Default False so legacy
            callers (no flag in DB) keep the existing "auto-
            fillable" semantics.  Set AFTER the method is
            selected so the toggle button refresh has the right
            visibility context.

        The customer_charged fall-back path produces the wrong charge
        whenever the daily match cap was applied: the cap inflates
        ``customer_charged`` above ``method_amount/(1+match%)``, and
        only the saved ``customer_charged`` field reflects what the
        customer actually paid.  Always pass ``customer_charged`` from
        DB rows when available.
        """
        # Stash the forfeit so get_data() can round-trip it.
        self._customer_forfeit_cents = int(customer_forfeit_cents or 0)
        for i in range(self.method_combo.count()):
            m = self.method_combo.itemData(i)
            if m and m['id'] == payment_method_id:
                self.method_combo.setCurrentIndex(i)
                break

        # v1.9.10 onsite-finding fix: restore vendor binding BEFORE
        # setting charge.  The screen-level ``_update_summary`` fires
        # after the method combo change (signal cascade) and computes
        # this row's max via ``_push_row_limits``.  An UNBOUND denom
        # row falls into the non-denom else-branch which uses
        # ``order_remaining = effective_order_total - other_total``.
        # During draft restore, prior non-denom rows can already have
        # high method amounts loaded, so ``other_total`` exceeds the
        # order total → ``max_charge = 0``.  Then
        # ``_set_active_charge(customer_charged)`` is silently clamped
        # to 0 by ``QSpinBox.setMaximum(0)``, and the FB row comes
        # back at $0 instead of its saved customer charge.  Setting
        # the binding first lets ``_push_row_limits`` use the bound-
        # denom branch (per-vendor cap) when the charge write happens.
        if bound_vendor_id is not None:
            self.set_bound_vendor_id(bound_vendor_id)

        if customer_charged is not None:
            # Authoritative path — use the saved charge directly.
            self._set_active_charge(customer_charged)
        else:
            # Legacy fall-back — derive charge from method_amount.
            method = self.get_selected_method()
            if method:
                charge = method_amount_to_charge(
                    method_amount, method['match_percent'])
            else:
                charge = method_amount
            self._set_active_charge(charge)

        # v2.0.7+ (schema v37, audit 2026-05-07): apply restored
        # user-cap flag AFTER the method + charge are set so the
        # toggle button's visibility refresh has the right context
        # (button shows only on non-denom rows with a real method).
        # Refreshing the style at the end ensures the icon colour
        # matches the restored flag immediately on load.
        self._user_capped = bool(user_capped)
        self._refresh_auto_distribute_btn_style()

    def validate_denomination(self):
        """Return error string if charge violates denomination, else None."""
        # Stepper enforces valid denominations by construction
        if self._stepper_active:
            return None
        method = self.get_selected_method()
        if not method or not method.get('denomination'):
            return None
        charge = self._get_active_charge()  # integer cents
        denom = method['denomination']  # integer cents
        if charge > 0 and charge % denom != 0:
            return (f"{method['name']} must be in {format_dollars(denom)} increments "
                    f"(entered {format_dollars(charge)})")
        return None

    # ── Photo receipt (multi-photo aware) ────────────────────

    def _get_check_count(self) -> int:
        """Return the number of checks/photos expected for this payment row.

        When stepper is active, reads the count directly.
        Otherwise, count = int(charge_cents / denomination_cents) or 1.
        Returns 0 when charge is 0 or no method selected.
        """
        method = self.get_selected_method()
        if not method:
            return 0
        if self._stepper_active:
            return self._stepper.count()
        charge = self._get_active_charge()  # integer cents
        if charge <= 0:
            return 0
        denom = method.get('denomination')  # integer cents
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
        charge_cents = self._get_active_charge()
        if charge_cents <= 0:
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
