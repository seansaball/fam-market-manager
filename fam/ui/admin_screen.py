"""Screen E: Admin Adjustments."""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QTextEdit, QDialog, QDialogButtonBox,
    QFormLayout
)
from PySide6.QtCore import Qt, Signal

from fam.database.connection import get_connection
from fam.models.market_day import get_all_market_days, get_open_market_day
from fam.models.vendor import get_all_vendors
from fam.models.payment_method import get_all_payment_methods, get_payment_methods_for_market
from fam.models.transaction import (
    search_transactions, get_transaction_by_id, update_transaction,
    get_payment_line_items, save_payment_line_items
)
from fam.models.audit import log_action, get_audit_log
from fam.utils.export import write_ledger_backup
from fam.ui.styles import (
    WHITE, LIGHT_GRAY, ERROR_COLOR, PRIMARY_GREEN, WARNING_COLOR,
    BACKGROUND, TEXT_COLOR, SUBTITLE_GRAY,
    SUCCESS_BG, ACCENT_GREEN, WARNING_BG
)
from fam.ui.helpers import (
    make_field_label, make_item, make_action_btn, configure_table,
    NoScrollDoubleSpinBox, NoScrollComboBox, DateRangeWidget
)
from fam.ui.widgets.payment_row import PaymentRow
from fam.utils.money import dollars_to_cents, cents_to_dollars, format_dollars

logger = logging.getLogger('fam.ui.admin_screen')


REASON_CODES = {
    "Data Entry Error": "data_entry_error",
    "Vendor Correction": "vendor_correction",
    "Admin Adjustment": "admin_adjustment",
    "Customer Dispute": "customer_dispute",
    "Other": "other",
}


def _customer_prior_match_excluding_txn(txn_id: int) -> int:
    """Sum a customer's daily FAM match consumption EXCLUDING the
    given transaction.

    Used by ``AdjustmentDialog`` to compute the effective match cap
    available for the transaction being adjusted.  Without excluding
    this single transaction (rather than the whole order), an order
    with multiple transactions would over-count this transaction's
    match if any of its sibling transactions also live in the same
    order.

    Returns integer cents.  Returns 0 if the transaction has no
    customer_order (legacy data) or no market_day_id is resolvable.
    """
    conn = get_connection()
    info = conn.execute(
        """SELECT t.market_day_id, co.customer_label
           FROM transactions t
           LEFT JOIN customer_orders co
                ON co.id = t.customer_order_id
           WHERE t.id = ?""",
        (txn_id,)
    ).fetchone()
    if info is None or info['customer_label'] is None:
        return 0
    customer_label = info['customer_label']
    market_day_id = info['market_day_id']
    if market_day_id is None:
        return 0

    row = conn.execute(
        """SELECT COALESCE(SUM(pli.match_amount), 0)
           FROM customer_orders co
           JOIN transactions t
             ON t.customer_order_id = co.id
            AND t.status IN ('Confirmed', 'Adjusted')
           JOIN payment_line_items pli
             ON pli.transaction_id = t.id
           WHERE co.market_day_id = ?
             AND co.customer_label = ?
             AND co.status IN ('Confirmed', 'Adjusted')
             AND t.id != ?""",
        (market_day_id, customer_label, txn_id)
    ).fetchone()
    return int(row[0]) if row else 0


def _recompute_match_limit_for_txn(txn_row) -> int | None:
    """Re-derive the effective match cap excluding *txn_row* using
    the live DB state.

    Used by AdjustmentDialog's pre-save TOCTOU re-check (UI-H8).
    The dialog snapshots ``_match_limit`` at construction; this
    helper recomputes it just before save so a concurrent
    confirmation on another device can be detected.

    Returns:
        int (cents): the cap budget remaining for THIS transaction
            after subtracting the customer's prior-match consumption
            and respecting market-level cap toggles.
        None: when the cap doesn't apply to this market (no daily
            limit set, ``match_limit_active=0``, or missing market).

    Defensive: any DB failure raises so the caller's outer
    try/except can decide whether to fail open or closed.
    """
    if txn_row is None or 'market_day_id' not in txn_row.keys():
        return None
    conn = get_connection()
    md_market = conn.execute(
        """SELECT m.daily_match_limit, m.match_limit_active
           FROM market_days md
           JOIN markets m ON m.id = md.market_id
           WHERE md.id = ?""",
        (txn_row['market_day_id'],)
    ).fetchone()
    if md_market is None or not md_market['match_limit_active']:
        return None
    daily_cap = md_market['daily_match_limit'] or 10000
    prior_match = _customer_prior_match_excluding_txn(txn_row['id'])
    return max(0, daily_cap - prior_match)


def _absorb_customer_pay_delta(new_items, delta_cents):
    """Reduce customer_charged on existing rows proportionally and
    return the corresponding method_amount that should be injected as
    Unallocated Funds to keep the receipt total balanced.

    Used when an adjustment increases the customer's required payment
    but the manager confirms the customer is unavailable to actually
    hand over the additional cash/instruments.  Keeps the saved
    customer_charged equal to what the customer truly paid (= the
    original amount), and surfaces the gap as Unallocated Funds.

    Mutates ``new_items`` in place: reduces ``customer_charged``,
    ``match_amount``, and ``method_amount`` on rows with a positive
    ``customer_charged``, distributing the reduction proportionally to
    each row's share of the new total.

    Returns the total method_amount that was removed from the rows.
    Caller is responsible for appending an Unallocated Funds row with
    that ``method_amount`` so the receipt total stays balanced.

    All values in integer cents.  Rounding goes to the last row to
    avoid losing pennies.
    """
    if delta_cents <= 0:
        return 0
    total_customer = sum(it.get('customer_charged', 0) for it in new_items)
    if total_customer <= 0:
        return 0

    total_method_reduction = 0
    remaining_delta = delta_cents
    # Index of the last row with non-zero customer_charged so we can
    # assign any rounding remainder there.
    last_chargeable_idx = max(
        (i for i, it in enumerate(new_items)
         if it.get('customer_charged', 0) > 0),
        default=-1,
    )

    for i, it in enumerate(new_items):
        if it.get('customer_charged', 0) <= 0:
            continue
        if i == last_chargeable_idx:
            # Last chargeable row absorbs whatever delta is left so
            # rounding errors don't drift the total.
            customer_reduction = min(remaining_delta,
                                     it['customer_charged'])
        else:
            line_share = it['customer_charged'] / total_customer
            customer_reduction = int(round(delta_cents * line_share))
            customer_reduction = min(customer_reduction,
                                     it['customer_charged'])
            customer_reduction = min(customer_reduction, remaining_delta)
        if customer_reduction <= 0:
            continue
        match_pct = it.get('match_percent_snapshot',
                           it.get('match_percent', 0))
        # Match reduces by the same fraction so each line stays
        # internally consistent (customer + match = method).
        match_reduction = int(round(
            customer_reduction * (match_pct or 0) / 100.0
        ))
        match_reduction = min(match_reduction, it.get('match_amount', 0))
        method_reduction = customer_reduction + match_reduction
        it['customer_charged'] -= customer_reduction
        it['match_amount'] = it.get('match_amount', 0) - match_reduction
        it['method_amount'] -= method_reduction
        total_method_reduction += method_reduction
        remaining_delta -= customer_reduction

    return total_method_reduction


def _append_unallocated_funds_row(new_items, method_amount_cents):
    """Append a system-managed Unallocated Funds line item with the
    given method_amount.  Returns the seeded payment_method dict
    or ``None`` if the seed is missing (caller must error out)."""
    if method_amount_cents <= 0:
        return {}  # nothing to append, but caller can treat as success
    from fam.models.payment_method import get_unallocated_funds_method
    uf_method = get_unallocated_funds_method()
    if uf_method is None:
        return None
    new_items.append({
        'payment_method_id': uf_method['id'],
        'method_name_snapshot': uf_method['name'],
        'match_percent_snapshot': uf_method['match_percent'],
        'match_percent': uf_method['match_percent'],
        'method_amount': method_amount_cents,
        'match_amount': 0,
        'customer_charged': 0,
        'photo_path': None,
        'photo_source_paths': [],
    })
    return uf_method


class AdjustmentDialog(QDialog):
    """Dialog for adjusting a transaction — receipt, vendor, and payment methods."""

    def __init__(self, txn, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Adjust Transaction {txn['fam_transaction_id']}")
        self.setMinimumWidth(700)
        self.txn = txn
        self._market_id = txn.get('market_id')
        self._payment_rows = []

        # Retrieve the match limit applicable to THIS transaction.
        #
        # v1.9.10 onsite-finding fix: previously this set
        # ``_match_limit = market['daily_match_limit']`` (the full
        # $100 daily cap) regardless of how much of that cap the
        # customer had already consumed in OTHER transactions today.
        # The engine then recomputed ``customer_charged`` for this
        # transaction as if the cap were fully available, producing
        # a smaller customer-paid value than what was actually saved
        # (because at original-save time the cap WAS active and
        # inflated customer_charged above the formula value).  The
        # impact panel's diff against the saved value then surfaced
        # a phantom "refund $X to customer" message the moment the
        # dialog opened — even though the manager hadn't changed
        # anything yet.
        #
        # Fix: subtract the customer's prior cap usage (across all
        # other confirmed/adjusted transactions today, excluding
        # this transaction) from the daily cap.  The engine then
        # sees the same effective cap that was in force when the
        # original transaction was saved, and reproduces the same
        # customer_charged when nothing has changed → no phantom
        # refund.  Mirrors PaymentScreen.load_customer_order's
        # ``_prior_match`` accounting.
        self._match_limit = None
        self._daily_limit = None
        if self._market_id:
            conn = get_connection()
            market = conn.execute(
                "SELECT daily_match_limit, match_limit_active FROM markets WHERE id=?",
                (self._market_id,)
            ).fetchone()
            if market and market['match_limit_active']:
                daily_cap = market['daily_match_limit'] or 10000
                self._daily_limit = daily_cap
                prior_match = _customer_prior_match_excluding_txn(
                    txn['id'])
                self._match_limit = max(0, daily_cap - prior_match)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {BACKGROUND};
            }}
            QLabel {{
                background-color: transparent;
                color: {TEXT_COLOR};
            }}
        """)

        main_layout = QVBoxLayout(self)

        # ── Top fields (form layout) ──────────────────────────
        form = QFormLayout()

        self.receipt_spin = NoScrollDoubleSpinBox()
        self.receipt_spin.setRange(0.01, 99999.99)
        self.receipt_spin.setDecimals(2)
        self.receipt_spin.setPrefix("$ ")
        self.receipt_spin.setValue(cents_to_dollars(txn['receipt_total']))
        self._last_receipt_total = cents_to_dollars(txn['receipt_total'])  # track for proportional rescale
        self.receipt_spin.valueChanged.connect(self._on_receipt_total_changed)
        form.addRow("Receipt Total:", self.receipt_spin)

        self.vendor_combo = NoScrollComboBox()
        vendors = get_all_vendors(active_only=True)
        for v in vendors:
            self.vendor_combo.addItem(v['name'], userData=v['id'])
        for i in range(self.vendor_combo.count()):
            if self.vendor_combo.itemData(i) == txn['vendor_id']:
                self.vendor_combo.setCurrentIndex(i)
                break
        form.addRow("Vendor:", self.vendor_combo)

        self.reason_combo = NoScrollComboBox()
        for display_label, code in REASON_CODES.items():
            self.reason_combo.addItem(display_label, userData=code)
        form.addRow("Reason:", self.reason_combo)

        self.notes_input = QTextEdit()
        self.notes_input.setMaximumHeight(80)
        self.notes_input.setPlaceholderText("Explain the reason for this adjustment...")
        form.addRow("Notes:", self.notes_input)

        self.adjusted_by_input = QLineEdit()
        self.adjusted_by_input.setPlaceholderText("Your name")
        form.addRow("Adjusted By:", self.adjusted_by_input)

        main_layout.addLayout(form)

        # ── Separator ─────────────────────────────────────────
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet(f"color: {LIGHT_GRAY};")
        main_layout.addWidget(separator)

        # ── Payment Methods section ───────────────────────────
        pay_header = QHBoxLayout()
        pay_title = QLabel("Payment Methods")
        pay_title.setStyleSheet(f"""
            font-size: 13px; font-weight: bold;
            color: {SUBTITLE_GRAY}; padding: 2px 0px;
        """)
        pay_header.addWidget(pay_title)
        pay_header.addStretch()
        self.add_method_btn = QPushButton("+ Add Payment Method")
        self.add_method_btn.setCursor(Qt.PointingHandCursor)
        self.add_method_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 4px 12px; min-height: 0px; font-size: 12px;
                border: 1px solid {LIGHT_GRAY}; border-radius: 6px;
                background-color: {WHITE}; color: {TEXT_COLOR};
            }}
            QPushButton:hover {{
                background-color: #F0EFEB; color: {PRIMARY_GREEN};
                border-color: {PRIMARY_GREEN};
            }}
        """)
        self.add_method_btn.clicked.connect(self._add_payment_row)
        pay_header.addWidget(self.add_method_btn)

        # ── Auto-Distribute button (parity with payment screen) ──
        # Mirrors PaymentScreen's ⚡ Auto-Distribute so a volunteer can
        # change the receipt total and one-click redistribute across
        # payment methods rather than hand-balancing each row.
        self.auto_distribute_btn = QPushButton("⚡ Auto-Distribute")
        self.auto_distribute_btn.setCursor(Qt.PointingHandCursor)
        self.auto_distribute_btn.setToolTip(
            "Reset non-denominated rows and redistribute the receipt total "
            "across payment methods."
        )
        self.auto_distribute_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 4px 12px; min-height: 0px; font-size: 12px;
                border: 1px solid {LIGHT_GRAY}; border-radius: 6px;
                background-color: {WHITE}; color: {TEXT_COLOR};
            }}
            QPushButton:hover {{
                background-color: #F0EFEB; color: {PRIMARY_GREEN};
                border-color: {PRIMARY_GREEN};
            }}
        """)
        self.auto_distribute_btn.clicked.connect(self._auto_distribute)
        pay_header.addWidget(self.auto_distribute_btn)
        main_layout.addLayout(pay_header)

        # Payment rows container
        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(4)
        self.rows_layout.addStretch()
        main_layout.addWidget(self.rows_container)

        # Allocation error label
        self.payment_error_label = QLabel("")
        self.payment_error_label.setStyleSheet(
            f"color: {ERROR_COLOR}; font-weight: bold; font-size: 12px;"
        )
        self.payment_error_label.setVisible(False)
        main_layout.addWidget(self.payment_error_label)

        # ── Customer impact info panel ────────────────────────
        self.customer_impact_label = QLabel("")
        self.customer_impact_label.setWordWrap(True)
        self.customer_impact_label.setVisible(False)
        main_layout.addWidget(self.customer_impact_label)

        # ── Buttons ───────────────────────────────────────────
        # Hold onto the OK button so ``_update_customer_impact`` can
        # disable it when the payment allocation doesn't match the
        # receipt total.  v1.9.10 onsite-finding fix: the dialog used
        # to let the manager click OK on a mis-allocated state, then
        # surface a ``QMessageBox`` ("Customer Available?") with a
        # YES path that fell through to a "Payment Mismatch" return
        # and a NO path that injected Unallocated Funds.  The NO
        # path was easy to mis-click on partially-entered payments,
        # producing erroneous Unallocated Funds rows that polluted
        # the FAM-absorbed-loss reports.  Mirroring the PaymentScreen
        # contract — "you can't click Confirm if the order isn't
        # fully allocated" — the OK button is now gated on the same
        # allocation-matches-receipt invariant the impact panel
        # surfaces.
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_btn = buttons.button(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)

        # ── Load existing payment line items ──────────────────
        existing_items = get_payment_line_items(txn['id'])
        self._original_items = existing_items
        self._original_customer_paid = sum(
            it['customer_charged'] for it in existing_items
        )  # integer cents from DB

        if existing_items:
            for item in existing_items:
                row = self._add_payment_row()
                # Pass customer_charged so cap-inflated values from
                # the saved transaction are preserved across reload —
                # mirrors the Payment-screen draft-restore fix.
                # v2.0.7 (schema v36): also pass customer_forfeit_cents
                # so a Phase B forfeit on the saved row survives the
                # Adjustment round-trip.  Pre-fix this was silently
                # dropped — re-saving from Adjustment would lose the
                # forfeit data entirely (the Reports → Customer
                # Forfeit column would zero out for any txn the
                # manager touched).
                row.set_data(item['payment_method_id'],
                             item['method_amount'],
                             customer_charged=item.get('customer_charged'),
                             customer_forfeit_cents=item.get(
                                 'customer_forfeit_cents', 0) or 0,
                             # v2.0.7+ (schema v37, audit
                             # 2026-05-07): restore user-cap flag
                             # so an AdjustmentDialog re-open of
                             # a transaction with a previously-
                             # locked row comes back Locked
                             # (gold ⚡).  Without this, the
                             # manager would see Active and a
                             # new Auto-Distribute click could
                             # silently re-distribute the locked
                             # value.
                             user_capped=bool(item.get(
                                 'user_capped', False)))
        else:
            self._add_payment_row()

        # Final cap pass after every existing row has its method +
        # value loaded.  ``_add_payment_row`` calls ``_update_row_caps``
        # before ``set_data`` picks the method, so the stepper-vs-
        # spinbox branch hadn't decided yet.  Re-running it here
        # gives every row a method-aware cap from the start.
        self._update_row_caps()
        self._update_customer_impact()

    # ── Payment row management ────────────────────────────────

    def _add_payment_row(self):
        """Add a new PaymentRow widget to the dialog.

        AdjustmentDialog edits a single transaction so there is exactly
        one vendor — denominated payments bind implicitly to that
        vendor.  ``single_vendor_mode=True`` hides the per-row vendor
        dropdown entirely while keeping every other behavior identical
        to the Payment screen.
        """
        row = PaymentRow(market_id=self._market_id,
                          single_vendor_mode=True)
        row.changed.connect(self._on_payment_changed)
        row.remove_requested.connect(self._remove_payment_row)
        self._payment_rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        # Push the transaction's vendor as a single-element pool so the
        # method dropdown filters by that vendor's eligibility (Settings
        # → Vendors → Methods).  Without this the volunteer could pick
        # a method the vendor isn't registered for during adjustment —
        # parallel of the Payment-screen single-vendor fix.
        vendor_id = self.txn.get('vendor_id')
        vendor_name = self.txn.get('vendor_name', '')
        if vendor_id is not None:
            row.set_order_vendors([
                {'id': vendor_id, 'name': vendor_name}
            ])
        self._refresh_method_choices()
        self._update_row_caps()
        return row

    def _remove_payment_row(self, row):
        """Remove a PaymentRow from the dialog."""
        if len(self._payment_rows) <= 1:
            return
        self._payment_rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self._refresh_method_choices()
        self._on_payment_changed()

    def _update_row_caps(self):
        """Smart per-row cap mirroring the Payment screen.

        Each row's charge input is capped against
        ``receipt_total - sum(other rows' method_amount)`` so the
        manager cannot enter more than what's actually owed.  This
        matches the Payment screen's ``_push_row_limits`` for parity:
        the volunteer experience on the receipt-intake side and the
        manager experience here behave the same way.

        Three behaviours layer on top:

        * **Non-denominated** rows convert the remaining method-amount
          space to charge space via ``charge / (1 + match%/100)`` and
          floor — the customer never absorbs a rounding penny;
          remainder goes to FAM match.
        * **Match-limit-aware** non-denom inflation: when a daily
          match cap is active and other rows have already consumed
          most of it, the customer must cover the gap.  We bump the
          max charge so the spinbox actually allows that — without
          this the cap silently blocks valid entries when the match
          pool is exhausted.
        * **Denomination forfeit** allowance: a customer hands over
          physical $5 FMNP checks; if remaining is $9 and denom is
          $5 we still allow 2 checks ($10 method_amount) — the +1
          unit overage is real money the manager must capture.  FAM
          match flexes down to keep the receipt balanced (handled
          by ``calculate_payment_breakdown`` on save).

        Single-vendor by design: AdjustmentDialog edits one
        transaction so the per-vendor binding logic from the Payment
        screen collapses to the trivial case — no need to track
        which row binds to which vendor.

        Block signals on all rows first to prevent ``setMaximum()``
        clamping from cascading back into ``_on_payment_changed``
        and re-entering this method recursively.
        """
        from fam.utils.calculations import charge_to_method_amount

        receipt_cents = dollars_to_cents(self.receipt_spin.value())

        # Block signals across the board before any cap mutations
        # touch the spinboxes/steppers.
        for row in self._payment_rows:
            row.blockSignals(True)
        try:
            for i, row in enumerate(self._payment_rows):
                method = row.get_selected_method()
                if not method:
                    continue

                # Aggregate method_amount + match_amount from all
                # OTHER rows so this row's headroom is "what's left
                # of the receipt after everyone else's allocation".
                other_total = 0
                other_match = 0
                for j, r in enumerate(self._payment_rows):
                    if j == i or not r.has_method_selected():
                        continue
                    r_method = r.get_selected_method()
                    if not r_method:
                        continue
                    r_charge = r._get_active_charge()
                    if r_charge <= 0:
                        continue
                    r_ma = charge_to_method_amount(
                        r_charge, r_method['match_percent']
                    )
                    other_total += r_ma
                    other_match += r_ma - r_charge

                remaining = max(0, receipt_cents - other_total)

                match_pct = method['match_percent']
                divisor = 1.0 + match_pct / 100.0
                # Floor so the customer never absorbs a rounding
                # penny — pin parity with smart_auto_distribute.
                max_charge_nominal = int(remaining / divisor)

                denom = method.get('denomination')
                is_denominated = bool(denom and denom > 0)

                # Match-limit awareness — only meaningful for non-
                # denominated rows.  Denominated charges are physical
                # units; inflating them past the nominal cap would
                # let the volunteer enter way more checks than the
                # receipt can absorb (the original Payment-screen
                # bug this branch was added to avoid).
                if (self._match_limit is not None and match_pct > 0
                        and not is_denominated):
                    available_match = max(
                        0, self._match_limit - other_match)
                    uncapped_match = remaining - max_charge_nominal
                    if uncapped_match > available_match:
                        max_charge_capped = remaining - available_match
                        max_charge = max(
                            max_charge_nominal, max_charge_capped)
                    else:
                        max_charge = max_charge_nominal
                else:
                    max_charge = max_charge_nominal

                # Denomination forfeit: when remaining doesn't divide
                # evenly into the denomination, allow +1 unit so a
                # real check can be entered.  Engine match-cap takes
                # the hit on save.
                if is_denominated:
                    normal_units = int(max_charge / denom)
                    normal_alloc = charge_to_method_amount(
                        normal_units * denom, match_pct
                    )
                    if remaining - normal_alloc > 1:
                        max_charge = (normal_units + 1) * denom

                # v1.9.10 onsite-finding fix: never silently clamp an
                # existing valid charge below itself.  Qt's
                # ``setMaximum`` clamps the current value when the new
                # max is smaller — which silently destroyed user
                # input the moment the receipt total was dropped (the
                # cap shrunk and clobbered existing FB/SNAP entries).
                # Preserve the current value as a floor; the impact
                # panel + Layer 2A invariant guard surface the real
                # over-allocation so the manager knows to reduce
                # rows manually instead of having data silently
                # disappear.
                current_charge = row._get_active_charge()
                row.set_max_charge(max(max_charge, current_charge))
        finally:
            for row in self._payment_rows:
                row.blockSignals(False)

    def _on_payment_changed(self):
        """Called when any payment row value changes."""
        self._refresh_method_choices()
        # Re-cap every row so adjustments to one row's amount tighten
        # or loosen the headroom of the others in lock-step (e.g.
        # decreasing FMNP frees space for SNAP).  Without this hook
        # the cap was only applied on receipt change + auto-distribute,
        # so a manager reopening a transaction with already-bloated
        # values (e.g. 19 × $2 against an $11.11 receipt) saw the
        # values stay in place even though the receipt total wouldn't
        # fit them.
        self._update_row_caps()
        self._update_customer_impact()

    def _refresh_method_choices(self):
        """Disable already-selected methods in other rows.

        Excludes ``is_system`` methods (Unallocated Funds) from the
        available pool so they're never countable as a "next method
        to add" — they're only auto-injected by the customer-gone
        path during save, never picked manually.
        """
        if self._market_id:
            methods = get_payment_methods_for_market(
                self._market_id, active_only=True, include_system=False)
            if not methods:
                methods = get_all_payment_methods(
                    active_only=True, include_system=False)
        else:
            methods = get_all_payment_methods(
                active_only=True, include_system=False)

        selected_ids = set()
        for row in self._payment_rows:
            mid = row.get_selected_method_id()
            if mid is not None:
                selected_ids.add(mid)
        for row in self._payment_rows:
            row.set_excluded_methods(selected_ids)

        self.add_method_btn.setVisible(len(self._payment_rows) < len(methods))

    # ── Receipt total change → proportional rescale ──────────

    def _on_receipt_total_changed(self, new_total):
        """When receipt total changes, proportionally rescale all payment amounts.

        The spinbox emits dollar values; we convert to integer cents for
        proportional math.

        v1.9.10 onsite-finding fix: read/write through
        ``_get_active_charge`` / ``_set_active_charge`` instead of the
        raw ``amount_spin``.  Denominated rows use the stepper widget
        (with ``amount_spin`` hidden), so the old code read 0 from the
        hidden spinbox and skipped denom rows entirely from the
        proportional sum, then wrote the rescaled value to a widget
        the user couldn't see — corrupting the data model the moment
        the receipt total was edited on a transaction with denom
        payments.
        """
        old_total_cents = dollars_to_cents(self._last_receipt_total)
        new_total_cents = dollars_to_cents(new_total)
        if old_total_cents > 0 and new_total_cents != old_total_cents:
            # Read each row's CURRENT charge — uses stepper for
            # denominated rows, amount_spin for non-denom.
            amounts_cents = [
                row._get_active_charge() for row in self._payment_rows
            ]
            current_sum_cents = sum(amounts_cents)
            # Only rescale if payments previously matched the old total (within 1 cent)
            if current_sum_cents > 0 and abs(current_sum_cents - old_total_cents) <= 1:
                rescaled = [
                    round(a * new_total_cents / current_sum_cents)
                    for a in amounts_cents
                ]
                # Fix rounding drift — adjust the largest row
                drift = new_total_cents - sum(rescaled)
                if drift != 0 and rescaled:
                    idx = rescaled.index(max(rescaled))
                    rescaled[idx] += drift
                # Apply new amounts via the row's active-input setter
                # so denom steppers update correctly (in whole-unit
                # multiples — values not on a denom boundary truncate
                # but at least the model and UI stay consistent).
                for row, amt_cents in zip(self._payment_rows, rescaled):
                    row.blockSignals(True)
                    try:
                        row._set_active_charge(amt_cents)
                    finally:
                        row.blockSignals(False)
        self._last_receipt_total = new_total
        # Re-cap every row against the NEW receipt total so the per-row
        # maximum tracks the target.  Without this, changing the receipt
        # would let a row exceed the new total until the user tabbed out.
        self._update_row_caps()
        self._update_customer_impact()

    # ── Auto-Distribute (parity with payment screen) ─────────────

    def _auto_distribute(self):
        """One-click redistribution of the receipt total across payment rows.

        Volunteer flow: change the receipt total, click this button, rows
        auto-fill based on each method's match percent and denomination.
        Denominated rows with an existing charge are treated as locked
        (they represent physical checks/tokens the customer handed over);
        non-denominated rows are reset and refilled as absorbers.

        Simpler than PaymentScreen's equivalent because the adjustment
        flow does not need the "add overflow row" escape hatch — if the
        user has no non-denominated row and can't absorb the remainder,
        the existing save-time validator surfaces the mismatch so they
        can add a row manually.
        """
        from fam.utils.calculations import (
            smart_auto_distribute, charge_to_method_amount,
        )

        receipt_cents = dollars_to_cents(self.receipt_spin.value())
        if receipt_cents <= 0 or not self._payment_rows:
            return

        # Build row descriptors for the algorithm.  Non-denominated rows
        # are always reset to 0 (absorbers).  Denominated rows with a
        # charge stay locked.
        row_descriptors = []
        for i, row in enumerate(self._payment_rows):
            method = row.get_selected_method()
            if not method:
                continue
            is_denom = (
                method.get('denomination') and method['denomination'] > 0
            )
            charge = row._get_active_charge()
            if not is_denom and charge > 0:
                charge = 0   # absorber — let distributor refill
            row_descriptors.append({
                'index': i,
                'match_pct': method['match_percent'],
                'denomination': method.get('denomination'),
                'sort_order': method.get('sort_order', 0),
                'current_charge': charge,
            })

        if not row_descriptors:
            return

        assignments = smart_auto_distribute(receipt_cents, row_descriptors)

        # Apply non-denominated reset first so the UI reflects the new
        # state even for rows the distributor chose not to fill.
        for desc in row_descriptors:
            is_denom = (
                desc.get('denomination') and desc['denomination'] > 0
            )
            if not is_denom and desc['current_charge'] == 0:
                # Temporarily suppress change notifications while we
                # bulk-update so we don't trigger N cascading recalcs.
                row = self._payment_rows[desc['index']]
                row.blockSignals(True)
                row._set_active_charge(0)
                row.blockSignals(False)

        # Apply assignments from the distributor
        for a in assignments:
            row = self._payment_rows[a['index']]
            row.blockSignals(True)
            row._set_active_charge(a['charge'])
            row.blockSignals(False)

        # Re-cap and recompute the impact panel after all rows settle.
        self._update_row_caps()
        self._on_payment_changed()

    # ── Customer impact calculation ───────────────────────────

    def _update_customer_impact(self):
        """Update the customer impact info panel based on current payment data.

        All internal monetary values are integer cents.  The receipt spinbox
        is in dollars and is converted at the boundary.
        """
        from fam.utils.calculations import calculate_payment_breakdown

        new_total_cents = dollars_to_cents(self.receipt_spin.value())

        # Collect valid payment data from rows (all values already in cents)
        # and cap each non-denom row's method_amount at the receipt total
        # so the engine reproduces the saved cap-active state on dialog
        # open (mirrors PaymentScreen ``_update_summary`` step 3).
        #
        # v1.9.10 onsite-finding fix: previously this passed
        # ``data['method_amount']`` straight to the engine.  But
        # ``PaymentRow.get_data`` re-derives method_amount from the
        # spinbox's charge via ``charge × (1 + pct/100)`` — which is
        # the *uncapped* formula value.  When the saved transaction
        # was cap-inflated (customer > formula because match was
        # capped at save time), the loaded row has charge=saved
        # customer, so get_data returns method=saved_customer×2 (≠
        # saved method).  The engine then treats this inflated
        # method as ground truth, applies the cap, and produces a
        # customer_charged that doesn't match the saved value — and
        # the writeback below overwrites the spinbox with the wrong
        # number.  Capping at receipt mirrors the on-screen reality:
        # vendor reimbursement = receipt total = sum of method
        # amounts.  Pre-summing total denom contribution makes the
        # cap order-independent (same fix as PaymentScreen).
        calc_entries = []
        active_rows = []
        allocated = 0  # integer cents
        has_payments = False

        total_denom_alloc = 0
        for r in self._payment_rows:
            d = r.get_data()
            if not d or d['method_amount'] <= 0:
                continue
            m = r.get_selected_method()
            if m and m.get('denomination') and m['denomination'] > 0:
                total_denom_alloc += d['method_amount']

        non_denom_running = 0
        for row in self._payment_rows:
            data = row.get_data()
            if data and data['method_amount'] > 0:
                ma = data['method_amount']
                method = row.get_selected_method()
                is_denom = bool(
                    method and method.get('denomination')
                    and method['denomination'] > 0)
                # Cap non-denom method at receipt minus denom total
                # minus other non-denom already counted.  Single-row
                # SNAP-only adjustment collapses to "method = receipt".
                if not is_denom:
                    max_ma = max(
                        0,
                        new_total_cents
                        - total_denom_alloc
                        - non_denom_running,
                    )
                    if ma > max_ma:
                        ma = max_ma
                    non_denom_running += ma
                has_payments = True
                allocated += ma
                # v1.9.10: pass denomination so the engine's cap
                # logic keeps customer_charged FIXED on denom rows
                # (avoids Layer 2A "row mismatch" blocks when cap
                # would otherwise inflate denom customer to a non-
                # denom-multiple value).
                calc_entries.append({
                    'method_amount': ma,
                    'match_percent': data['match_percent'],
                    'denomination': data.get('denomination'),
                    # v2.0.7+ user-cap (audit 2026-05-07):
                    # propagate the row's user-cap flag to the
                    # engine's customer-impact preview.  Without
                    # this, the impact panel shows engine-inflated
                    # customer_charged even though the volunteer
                    # locked the row, causing a "row mismatch"
                    # surprise at confirm time when the actual
                    # save respects user_capped.  This mirrors
                    # the propagation in get_new_line_items
                    # (via resolve_payment_state) so the preview
                    # and the save use identical engine input.
                    'user_capped': bool(
                        data.get('user_capped', False)),
                })
                active_rows.append(row)

        if not has_payments:
            self.customer_impact_label.setVisible(False)
            self.payment_error_label.setVisible(False)
            # No payment rows but a non-zero receipt is still
            # under-allocated — block save.  Zero receipt is the
            # only legitimate "nothing to save" state.
            if hasattr(self, 'ok_btn') and self.ok_btn is not None:
                if new_total_cents > 0:
                    self.ok_btn.setEnabled(False)
                    self.ok_btn.setToolTip(
                        "No payment methods entered.  Add at least "
                        "one payment row matching the receipt total "
                        "before saving."
                    )
                else:
                    self.ok_btn.setEnabled(True)
                    self.ok_btn.setToolTip("")
            return

        # Show allocation error if payment total doesn't match receipt
        # total.  Exception: when the over-allocation is bounded by a
        # denominated payment (the customer's physical check exceeds
        # remaining headroom by less than one full unit), surface it as
        # a forfeit warning instead of a hard error — the save flow
        # accepts it after a Yes/No confirmation, mirroring the Payment
        # screen.  Without this branch the impact panel shows a red
        # "Payment Mismatch" the moment the cap-permitted +1 unit is
        # entered, which is confusing.
        #
        # Track ``allocation_blocks_save`` so the OK button can be
        # gated on a fully-allocated state.  Denom-overage forfeit
        # is the ONE allowed exception — that's a legitimate
        # match-reduction path the save flow handles correctly.
        # Everything else (under-allocation, over-allocation by
        # non-denom rows) blocks save.
        allocation_blocks_save = False
        gap_cents = new_total_cents - allocated   # positive = under
        if gap_cents < -1:
            from fam.utils.calculations import charge_to_method_amount
            effective_denom_sum = 0
            for r in self._payment_rows:
                m = r.get_selected_method()
                if m and m.get('denomination'):
                    effective_denom_sum += charge_to_method_amount(
                        m['denomination'], m['match_percent']
                    )
            overage = -gap_cents
            if (effective_denom_sum > 0
                    and overage <= effective_denom_sum):
                # Soft warning — yellow.  Save is allowed (the
                # math just gets balanced via match reduction).
                #
                # v2.0.7 (user-reported 2026-05-07): do NOT call
                # this a "customer forfeit" — Phase A reduction
                # is FAM contributing less, not a customer-side
                # loss.  Reserve "Customer Forfeit" terminology
                # for Phase B (token-value loss).
                self.payment_error_label.setText(
                    f"⚠  Denomination overage: "
                    f"{format_dollars(overage)} — FAM match will "
                    f"be reduced to keep the vendor "
                    f"reimbursement exact.  Vendor still "
                    f"receives the full receipt amount."
                )
                self.payment_error_label.setStyleSheet(f"""
                    font-size: 13px; font-weight: bold;
                    padding: 8px 12px; border-radius: 6px;
                    background-color: {WARNING_BG};
                    color: {WARNING_COLOR};
                    border: 1px solid {WARNING_COLOR};
                """)
                self.payment_error_label.setVisible(True)
            else:
                # Real over-allocation, not denom-bounded — block save.
                self.payment_error_label.setText(
                    f"Payment total ({format_dollars(allocated)}) does not match "
                    f"receipt total ({format_dollars(new_total_cents)}). "
                    f"Remaining: {format_dollars(gap_cents)}"
                )
                self.payment_error_label.setStyleSheet("")
                self.payment_error_label.setVisible(True)
                allocation_blocks_save = True
        elif abs(allocated - new_total_cents) > 1:
            # Under-allocation — block save until the manager either
            # adds payment rows or reduces the receipt total.  Pre-fix
            # the dialog let the OK click through to a
            # ``QMessageBox.question`` that nudged the manager toward
            # injecting Unallocated Funds — a real foot-gun on
            # partially-entered states (the "customer is gone" path
            # was easy to misclick into when really the manager just
            # hadn't finished entering payments).
            self.payment_error_label.setText(
                f"Payment total ({format_dollars(allocated)}) does not match "
                f"receipt total ({format_dollars(new_total_cents)}). "
                f"Remaining: {format_dollars(gap_cents)}"
            )
            self.payment_error_label.setStyleSheet("")
            self.payment_error_label.setVisible(True)
            allocation_blocks_save = True
        else:
            self.payment_error_label.setVisible(False)

        # Gate the OK button on a fully-allocated state.  Mirrors
        # PaymentScreen's "Confirm Payment" enable/disable contract:
        # the manager cannot commit a transaction whose payment
        # methods don't reconcile to the receipt total.
        if hasattr(self, 'ok_btn') and self.ok_btn is not None:
            self.ok_btn.setEnabled(not allocation_blocks_save)
            if allocation_blocks_save:
                self.ok_btn.setToolTip(
                    "Payment total must match the receipt total "
                    "before saving.  Adjust the payment rows or "
                    "click ⚡ Auto-Distribute to redistribute "
                    "across methods."
                )
            else:
                self.ok_btn.setToolTip("")

        # Use calculate_payment_breakdown with match limit for accurate totals
        result = calculate_payment_breakdown(
            new_total_cents, calc_entries, match_limit=self._match_limit
        )
        # v2.0.7-final consolidation (Option B, schema v36): apply
        # denomination forfeit BEFORE displaying impact totals so
        # the manager sees what will actually be saved.  Pre-fix,
        # the impact panel showed pre-forfeit FAM match (e.g. "$10
        # match") while the save flow applied Phase A reduction
        # to bring it down (e.g. to "$6.52 match") — manager
        # confusion bug.  Now the panel shows the correct post-
        # forfeit values.
        allocated_pre_forfeit = result.get('allocated_total', 0)
        if allocated_pre_forfeit > new_total_cents:
            overage_for_panel = (
                allocated_pre_forfeit - new_total_cents)
            # Compute effective denom sum to verify the overage is
            # denom-caused (mirrors _check_denomination_overage
            # logic).  Skip forfeit if non-denom over-allocation.
            from fam.utils.calculations import (
                charge_to_method_amount, apply_denomination_forfeit,
            )
            effective_denom_sum = 0
            for r in self._payment_rows:
                m = r.get_selected_method()
                if m and m.get('denomination'):
                    effective_denom_sum += charge_to_method_amount(
                        m['denomination'], m['match_percent'])
            if (effective_denom_sum > 0
                    and overage_for_panel <= effective_denom_sum):
                # Build a vendor_receipts map from the txn under
                # edit (single-vendor scope for AdjustmentDialog).
                vendor_receipts: dict = {}
                if (self.txn
                        and self.txn.get('vendor_id') is not None):
                    vendor_receipts[
                        self.txn['vendor_id']] = new_total_cents
                # Pass calc_entries through so the canonical fn
                # can update them in lock-step.
                apply_denomination_forfeit(
                    result, calc_entries, overage_for_panel,
                    vendor_receipts=vendor_receipts)
        new_customer_paid = result['customer_total_paid']  # cents
        new_fam_match = result['fam_subsidy_total']  # cents

        # Push capped values back to each PaymentRow.  Two writes:
        #
        # (a) ``set_display_values(match_amount, method_amount)`` updates
        #     the read-only "Match" / "Total" labels next to the
        #     spinbox.  ``method_amount`` is the vendor reimbursement
        #     (charge + match), NOT customer_charged — passing
        #     customer_charged here was a latent display bug that
        #     mis-labelled the Total column whenever the match cap
        #     trimmed the FAM contribution.
        # (b) When the engine's capped ``customer_charged`` differs
        #     from what the spinbox shows (e.g. match cap inflated
        #     the customer's share past the typed value), write the
        #     true charge back into the spinbox so it stays in sync
        #     with the impact panel and the eventual save.  Without
        #     this, the manager could see "$100" in the spinbox but
        #     the impact panel would show "$110" — and the Layer 2A
        #     charge-integrity guard (in _adjust_transaction) would
        #     refuse to save.  Mirrors PaymentScreen ``_update_summary``
        #     lines 1384-1400 verbatim so both flows write back the
        #     same way.
        for row, capped_li in zip(active_rows, result['line_items']):
            row.set_display_values(
                capped_li['match_amount'], capped_li['method_amount']
            )
            true_charge = capped_li['customer_charged']
            if true_charge != row._get_active_charge():
                # Block the row's ``changed`` signal so this write-back
                # doesn't re-enter ``_on_payment_changed`` →
                # ``_update_customer_impact`` while we're still inside
                # this method.
                row.blockSignals(True)
                try:
                    row._set_active_charge(true_charge)
                    row._recompute()
                finally:
                    row.blockSignals(False)

        # Customer impact comparison (all cents)
        diff = new_customer_paid - self._original_customer_paid

        # Build match limit info string
        limit_note = ""
        if self._match_limit is not None:
            limit_note = f"  (Match limit: {format_dollars(self._match_limit)})"
        if result.get('match_was_capped'):
            limit_note = f"  (Match capped at {format_dollars(self._match_limit)})"

        if abs(diff) < 1:
            self.customer_impact_label.setText(
                f"No change to customer amount. "
                f"Customer pays {format_dollars(new_customer_paid)}, "
                f"FAM match {format_dollars(new_fam_match)}.{limit_note}"
            )
            self.customer_impact_label.setStyleSheet(f"""
                font-size: 13px; font-weight: bold;
                padding: 8px 12px; border-radius: 6px;
                background-color: {LIGHT_GRAY}; color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GRAY};
            """)
        elif diff > 0:
            self.customer_impact_label.setText(
                f"If the original payment was collected, "
                f"collect {format_dollars(diff)} more from customer. "
                f"(Was {format_dollars(self._original_customer_paid)}, "
                f"now {format_dollars(new_customer_paid)})  "
                f"FAM match: {format_dollars(new_fam_match)}{limit_note}"
            )
            self.customer_impact_label.setStyleSheet(f"""
                font-size: 13px; font-weight: bold;
                padding: 8px 12px; border-radius: 6px;
                background-color: {WARNING_BG}; color: {WARNING_COLOR};
                border: 1px solid {WARNING_COLOR};
            """)
        else:
            self.customer_impact_label.setText(
                f"If the original payment was collected, "
                f"refund {format_dollars(abs(diff))} to customer. "
                f"(Was {format_dollars(self._original_customer_paid)}, "
                f"now {format_dollars(new_customer_paid)})  "
                f"FAM match: {format_dollars(new_fam_match)}{limit_note}"
            )
            self.customer_impact_label.setStyleSheet(f"""
                font-size: 13px; font-weight: bold;
                padding: 8px 12px; border-radius: 6px;
                background-color: {SUCCESS_BG}; color: {ACCENT_GREEN};
                border: 1px solid {ACCENT_GREEN};
            """)
        self.customer_impact_label.setVisible(True)

    # ── Public getters for caller ─────────────────────────────

    def get_new_line_items(self):
        """Return list of payment line item dicts with match limit applied.

        All monetary values are integer cents.

        Phase 6 consolidation (v1.9.10): delegates to the canonical
        ``resolve_payment_state`` engine instead of duplicating cap +
        forfeit + items-sync logic locally.  PaymentScreen's
        ``_resolve_engine_state`` and the AdjustmentDialog's
        ``get_new_line_items`` now share the same code path —
        eliminating the parallel-implementation bug class (#5, #9,
        #11, #12, #13, #17, #18 from the audit).

        AdjustmentDialog edits a single transaction so the dialog
        runs without per-vendor binding info and without a
        per-vendor-aware forfeit function.  ``resolve_payment_state``
        falls back to detecting the order-level denom overage and
        returning it on ``denom_overage_cents`` for the caller to
        handle (the existing ``_adjust_transaction`` flow already
        does that).
        """
        from fam.utils.calculations import resolve_payment_state

        raw_items = []
        for row in self._payment_rows:
            data = row.get_data()
            if data and data['method_amount'] > 0:
                raw_items.append(data)

        if not raw_items:
            return []

        # Cap each non-denom row's method_amount at the receipt total
        # BEFORE passing to the canonical engine — this mirrors
        # PaymentScreen's ``_collect_line_items`` cap step so both
        # surfaces produce identical engine input on identical UI
        # state.  Pre-summing total denom keeps the cap order-
        # independent (same fix as PaymentScreen, applied to the
        # adjustment flow for parity).
        new_total_cents = dollars_to_cents(self.receipt_spin.value())
        total_denom_alloc = sum(
            it['method_amount'] for it in raw_items
            if it.get('denomination') and it['denomination'] > 0
        )
        non_denom_running = 0
        for it in raw_items:
            denom = it.get('denomination')
            is_denom = bool(denom and denom > 0)
            if not is_denom:
                max_ma = max(
                    0,
                    new_total_cents
                    - total_denom_alloc
                    - non_denom_running,
                )
                if it['method_amount'] > max_ma:
                    it['method_amount'] = max_ma
                non_denom_running += it['method_amount']

        # Canonical engine handles cap-aware fallback, denom forfeit
        # detection, and items-sync in one call.  AdjustmentDialog
        # passes apply_denomination_forfeit_fn=None — order-level
        # forfeit handling is done downstream in _adjust_transaction.
        resolve_payment_state(
            new_total_cents, raw_items,
            match_limit=self._match_limit,
            apply_denomination_forfeit_fn=None,
        )
        return raw_items

    def payments_changed(self):
        """Return True if the payment methods/amounts differ from the original."""
        new_items = self.get_new_line_items()
        if len(new_items) != len(self._original_items):
            return True
        for new_it, old_it in zip(
            sorted(new_items, key=lambda x: x['payment_method_id']),
            sorted(self._original_items, key=lambda x: x['payment_method_id'])
        ):
            if (new_it['payment_method_id'] != old_it['payment_method_id'] or
                    abs(new_it['method_amount'] - old_it['method_amount']) > 0):
                return True
        return False


class AdminScreen(QWidget):
    """Admin Adjustments screen."""

    # Fired on any CUD operation this screen performs (adjustment save,
    # transaction void) so the main window can trigger a cloud sync.
    # v2.0.6 fix: emit the affected market_day_id so the sync handler
    # can scope to THAT specific day, not the currently-OPEN day.
    # Adjustments / voids regularly target transactions on CLOSED
    # market days (coordinators reconciling historical receipts).
    # Pre-fix the auto-sync narrowed scope to the open day and
    # silently skipped closed-day mutations from reaching the cloud
    # sheet until a manual full sync was triggered.  Same pattern
    # as ``fmnp_screen.entry_saved`` — see main_window's
    # ``_on_admin_data_changed`` slot for the override logic.
    data_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        title = QLabel("Adjustments & Corrections")
        title.setObjectName("screen_title")
        layout.addWidget(title)

        # Filter bar
        filter_frame = QFrame()
        self.filter_frame = filter_frame  # expose for tutorial hints
        filter_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {WHITE};
                border: 1px solid #E2E2E2;
                border-radius: 8px;
                padding: 6px 10px;
            }}
        """)
        filter_layout = QHBoxLayout(filter_frame)

        filter_layout.addWidget(make_field_label("Market"))
        self.md_filter = NoScrollComboBox()
        self.md_filter.setMinimumWidth(200)
        filter_layout.addWidget(self.md_filter)

        # Date range filter — auto-triggers a search on change so it
        # feels live like Reports.  Filters on ``last_updated`` (most
        # recent audit_log change OR created_at as fallback) so the
        # window matches the coordinator's mental model of "what I
        # worked on in this period" rather than "what business day
        # the transaction belongs to."
        #
        # Different semantic from the Reports screen on purpose:
        # Reports filters by md.date because it aggregates revenue
        # by business day; Adjustments filters by activity date
        # because the workflow is reviewing one's own session work.
        filter_layout.addWidget(make_field_label("Last Updated"))
        self.date_range = DateRangeWidget()
        self.date_range.setMinimumWidth(200)
        self.date_range.setToolTip(
            "Filter by when each transaction was last touched — "
            "either created or most recently adjusted/voided.\n\n"
            "Pick a single day to see only the transactions you "
            "worked on that day, regardless of which market day "
            "they belong to.  An adjustment to a 6-month-old "
            "transaction made today shows up in today's window.\n\n"
            "NOTE: this is intentionally DIFFERENT from the "
            "Reports screen's date filter, which uses the "
            "transaction's market day (business date).  Adjustments "
            "is a session-review workflow, not a revenue-aggregation "
            "workflow."
        )
        self.date_range.range_changed.connect(self._search)
        filter_layout.addWidget(self.date_range)

        filter_layout.addWidget(make_field_label("Status"))
        self.status_filter = NoScrollComboBox()
        self.status_filter.addItems(["All", "Draft", "Confirmed", "Adjusted", "Voided"])
        filter_layout.addWidget(self.status_filter)

        filter_layout.addWidget(make_field_label("Transaction ID"))
        self.id_search = QLineEdit()
        self.id_search.setPlaceholderText("Search FAM-...")
        self.id_search.setMaximumWidth(180)
        filter_layout.addWidget(self.id_search)

        search_btn = QPushButton("Search")
        search_btn.setObjectName("primary_btn")
        search_btn.setStyleSheet("padding: 4px 16px; min-height: 0px;")
        search_btn.clicked.connect(self._search)
        filter_layout.addWidget(search_btn)
        filter_layout.addStretch()

        layout.addWidget(filter_frame)

        # Results table.  v1.9.9 evolution:
        #   * Market split into "Market" + "Market Date" so the
        #     business-context date is visible per row.
        #   * "Last Updated" column added so the date filter's
        #     target ("when did we last touch this txn?") is also
        #     visible.  Reads from the ``last_updated`` field
        #     ``search_transactions`` derives via MAX(audit_log)
        #     fallback to ``created_at``.
        #   * Created kept for forensic value (when first entered).
        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels(
            ["Transaction ID", "Customer ID", "Market", "Market Date",
             "Vendor", "Receipt Total", "Status", "Created",
             "Last Updated", "Actions"]
        )
        configure_table(self.table, actions_col=9, actions_width=170)
        layout.addWidget(self.table)

        # Audit log preview (includes Changed By column)
        audit_label = QLabel("Recent Audit Log")
        audit_label.setStyleSheet(f"""
            font-size: 12px;
            font-weight: bold;
            color: {SUBTITLE_GRAY};
            padding: 2px 0px;
        """)
        layout.addWidget(audit_label)
        self.audit_table = QTableWidget()
        self.audit_table.setColumnCount(8)
        self.audit_table.setHorizontalHeaderLabels(
            ["Time", "Table", "Record ID", "Action", "Changed By",
             "Field", "Old Value", "New Value"]
        )
        configure_table(self.audit_table)
        self.audit_table.setMaximumHeight(220)
        layout.addWidget(self.audit_table)

    def refresh(self):
        self._load_market_days()
        self._search()
        self._load_audit_log()

    def _load_market_days(self):
        # v2.0.2 fix (UF-H4): preserve the manager's selected market
        # day across refresh.  Pre-fix every refresh / data_changed
        # signal cleared the combo and reset to "All" — losing the
        # filter mid-reconciliation when the manager clicked Adjust
        # on a transaction (the dialog's data_changed emit triggers
        # this code path).  Capture the current selection before
        # clearing and restore it if the same market day still exists.
        previous_data = self.md_filter.currentData() if hasattr(
            self, 'md_filter') and self.md_filter.count() > 0 else None
        days = get_all_market_days()
        self.md_filter.blockSignals(True)
        try:
            self.md_filter.clear()
            self.md_filter.addItem("All", userData=None)
            for d in days:
                self.md_filter.addItem(
                    f"{d['market_name']} - {d['date']}", userData=d['id'])
            # Restore selection if the previously-selected market day
            # is still present.  ``findData(None)`` matches the "All"
            # entry, so the empty-table case still defaults sensibly.
            if previous_data is not None:
                idx = self.md_filter.findData(previous_data)
                if idx >= 0:
                    self.md_filter.setCurrentIndex(idx)
        finally:
            self.md_filter.blockSignals(False)

        # Push the activity-date span (transactions.created_at and
        # the most recent audit_log entries) onto the date picker so
        # its popup spinboxes can't roam past the actual data
        # window.  This is the range the Last Updated filter
        # targets, so it should also be what bounds the picker.
        # Falls back to the 6-month default the widget computes on
        # its own when there's no data yet.
        conn = get_connection()
        bounds = conn.execute("""
            SELECT
              MIN(DATE(b.d)) AS min_d,
              MAX(DATE(b.d)) AS max_d
            FROM (
              SELECT created_at AS d FROM transactions
              UNION ALL
              SELECT changed_at AS d FROM audit_log
              WHERE table_name IN ('transactions', 'payment_line_items')
            ) b
        """).fetchone()
        if bounds and bounds['min_d'] and bounds['max_d']:
            self.date_range.set_date_bounds(
                bounds['min_d'], bounds['max_d'])

    def _search(self):
        md_id = self.md_filter.currentData()
        status = self.status_filter.currentText()
        if status == "All":
            status = None
        fam_id = self.id_search.text().strip() or None
        date_from, date_to = self.date_range.get_date_range()

        txns = search_transactions(
            market_day_id=md_id, status=status, fam_id_search=fam_id,
            date_from=date_from, date_to=date_to,
        )

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(txns))
        for i, t in enumerate(txns):
            self.table.setItem(i, 0, make_item(t['fam_transaction_id']))
            self.table.setItem(i, 1, make_item(t.get('customer_label') or ''))
            # Market name + Market Date as separate columns: the
            # Market Date is the business-day context, no longer
            # the filter target.
            self.table.setItem(i, 2, make_item(t['market_name']))
            self.table.setItem(i, 3, make_item(
                str(t.get('market_day_date', ''))))
            self.table.setItem(i, 4, make_item(t['vendor_name']))
            self.table.setItem(i, 5, make_item(format_dollars(t['receipt_total']), t['receipt_total']))
            self.table.setItem(i, 6, make_item(t['status']))
            self.table.setItem(i, 7, make_item(str(t.get('created_at', ''))))
            # Last Updated — what the date filter targets.  Reads
            # the derived field ``search_transactions`` produces via
            # MAX(audit_log.changed_at) with created_at fallback.
            self.table.setItem(i, 8, make_item(
                str(t.get('last_updated', '') or '')))

            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(3)

            txn_id = t['id']
            if t['status'] != 'Voided':
                adj_btn = make_action_btn("Adjust", 50)
                adj_btn.clicked.connect(lambda checked, tid=txn_id: self._adjust_transaction(tid))
                action_layout.addWidget(adj_btn)

                void_btn = make_action_btn("Void", 40, danger=True)
                void_btn.clicked.connect(lambda checked, tid=txn_id: self._void_transaction(tid))
                action_layout.addWidget(void_btn)

            if t['status'] != 'Draft':
                print_btn = make_action_btn("Print", 44)
                print_btn.clicked.connect(lambda checked, tid=txn_id: self._print_receipt(tid))
                action_layout.addWidget(print_btn)

            self.table.setCellWidget(i, 9, action_widget)
            self.table.setRowHeight(i, 42)

        self.table.setSortingEnabled(True)

    def _adjust_transaction(self, txn_id):
        txn = get_transaction_by_id(txn_id)
        if not txn:
            return

        if txn['status'] == 'Voided':
            QMessageBox.warning(self, "Cannot Adjust",
                                "Voided transactions cannot be adjusted.")
            return

        # v2.0.7: financial-integrity safety gate.  Adjusting a
        # transaction whose payments include denominated methods
        # (Food RX, JH Food Bucks, FMNP) has repeatedly surfaced
        # data-integrity edge cases — denomination snap drift,
        # cross-receipt allocation mismatches in multi-receipt
        # single-vendor orders, customer-side forfeit producing
        # non-aligned customer_charged.
        #
        # Rather than chase every edge case, gate the entry point.
        # Volunteers get a clear warning and a recommended path
        # (Void → recreate) but can still override if they
        # understand the risk.  The override is logged so a
        # subsequent reconciliation issue can be traced to a
        # deliberate adjust-anyway decision.
        denom_methods, sibling_count = (
            self._detect_adjustment_risk(txn))
        if denom_methods:
            method_list = ', '.join(sorted(denom_methods))
            multi_receipt_note = ''
            if sibling_count > 1:
                multi_receipt_note = (
                    f"<p><b>Additional risk:</b> this customer's "
                    f"order has <b>{sibling_count}</b> receipts at "
                    f"the same vendor.  Adjusting one of them can "
                    f"misallocate payment breakdowns across the "
                    f"siblings.</p>")
            warn = QMessageBox(self)
            warn.setIcon(QMessageBox.Warning)
            warn.setWindowTitle("Adjustment Risk — Denominated Payments")
            warn.setText(
                f"This transaction was paid with denominated "
                f"methods: <b>{method_list}</b>."
            )
            warn.setInformativeText(
                f"<p>Adjusting transactions with denominated payments "
                f"can cause financial-integrity issues — the "
                f"customer's physical token count can't always be "
                f"preserved through receipt-total changes, and the "
                f"resulting reports can show fractional values that "
                f"don't match what the customer actually handed "
                f"over.</p>"
                f"{multi_receipt_note}"
                f"<p><b>Recommended:</b> click <b>Void Instead</b>, "
                f"then re-enter the transaction with the corrected "
                f"receipt total in Receipt Intake.  The volunteer "
                f"keeps the same physical denomination handout.</p>"
                f"<p>If you understand the risk and need to proceed "
                f"anyway, click <b>Adjust Anyway</b>.</p>"
            )
            void_btn = warn.addButton(
                "Void Instead", QMessageBox.AcceptRole)
            adjust_btn = warn.addButton(
                "Adjust Anyway", QMessageBox.DestructiveRole)
            cancel_btn = warn.addButton(QMessageBox.Cancel)
            warn.setDefaultButton(void_btn)
            warn.exec()
            clicked = warn.clickedButton()
            if clicked is void_btn:
                # Audit-log the choice so the decision is traceable
                # alongside the subsequent VOID action.
                logger.info(
                    "Adjustment-risk gate: user chose Void Instead "
                    "for txn=%s (denom methods: %s, siblings=%d)",
                    txn_id, method_list, sibling_count)
                self._void_transaction(txn_id)
                return
            if clicked is not adjust_btn:
                # Cancel or close — abort entirely
                return
            # User chose Adjust Anyway — log the override before
            # opening the dialog so the audit trail captures the
            # informed decision.
            #
            # IMPORTANT: do NOT add ``from fam.models.audit import
            # log_action`` here.  ``log_action`` is already imported
            # at module level (line 21).  A function-local import
            # would promote ``log_action`` to a function-local for
            # the entire body, shadowing the module-level binding,
            # and any reference outside this branch (e.g. the save
            # path's ``log_action(...)`` calls below) would raise
            # ``UnboundLocalError`` on Cash-only / SNAP-only / any
            # non-denom adjustment that bypasses this gate.  This
            # was the v2.0.7 user-reported "Adjustment failed:
            # cannot access local variable 'log_action'" crash.
            # The same scoping footgun bit v2.0.1 with
            # ``get_transaction_by_id`` — see
            # ``tests/test_adjust_transaction_no_local_shadow.py``
            # and ``tests/test_codebase_hygiene.py::TestNoUnbound\
            # LocalShadows`` for the static + runtime pins.
            logger.warning(
                "Adjustment-risk gate: user chose Adjust Anyway "
                "for txn=%s despite denom-method warning (methods: "
                "%s, siblings=%d)",
                txn_id, method_list, sibling_count)
            log_action(
                'transactions', txn_id, 'ADJUST_OVERRIDE',
                'System',
                notes=(f"Volunteer overrode denom-payment "
                       f"adjustment warning; methods={method_list}; "
                       f"siblings_at_vendor={sibling_count}"),
            )

        dialog = AdjustmentDialog(txn, self)

        # Pre-fill "Adjusted By" with the open market day's volunteer
        open_md = get_open_market_day()
        if open_md and open_md.get('opened_by'):
            dialog.adjusted_by_input.setText(open_md['opened_by'])

        if dialog.exec() == QDialog.Accepted:
            # v2.0.1: re-fetch the transaction's status before saving.
            # The dialog can be open for minutes; another screen
            # (Receipt Intake's remove-receipt, a sibling void in
            # Adjustments) may have voided the transaction during
            # that window.  Saving over a Voided txn would either
            # resurrect it (forbidden by the voided-one-way trigger)
            # or write through and break audit-trail invariants.
            #
            # ``get_transaction_by_id`` is already imported at the
            # module level (line 18); a redundant local import here
            # shadowed it and caused an UnboundLocalError on the
            # earlier reference at the start of this function (the
            # ``txn`` lookup) — Python promotes any name bound in a
            # function body to a local for the WHOLE body.
            current = get_transaction_by_id(txn['id'])
            if current is None or current.get('status') == 'Voided':
                QMessageBox.warning(
                    self,
                    "Transaction Voided in Another Window",
                    "This transaction was voided while the adjustment "
                    "dialog was open.  Your changes have NOT been "
                    "saved.\n\n"
                    "If you still need to make a change, re-open the "
                    "transaction (you may need to first locate the "
                    "void in the Activity Log).",
                )
                return

            # v2.0.2 fix (UI-H8): re-check the daily match cap right
            # before saving.  ``dialog._match_limit`` was captured at
            # dialog construction; if another laptop confirmed a
            # different transaction for the same customer during the
            # dialog's open window, the cap may have shrunk below
            # this transaction's match.  Saving under the original
            # (looser) cap would exceed the daily limit across the
            # customer's same-day transactions — a real fund-
            # stewardship issue in multi-laptop deployments.
            if dialog._match_limit is not None:
                try:
                    fresh_limit = _recompute_match_limit_for_txn(current)
                    if (fresh_limit is not None
                            and fresh_limit < dialog._match_limit):
                        prepared_match_total = sum(
                            int(it.get('match_amount', 0))
                            for it in dialog.get_payment_data()
                        )
                        if prepared_match_total > fresh_limit:
                            QMessageBox.warning(
                                self,
                                "Daily Match Cap Reduced",
                                "The customer's remaining daily match "
                                "cap has been reduced (likely by a "
                                "concurrent confirmation on another "
                                "device) since this dialog was "
                                "opened.\n\n"
                                f"Prepared match: "
                                f"{format_dollars(prepared_match_total)}\n"
                                f"Now allowed: "
                                f"{format_dollars(fresh_limit)}\n\n"
                                "Re-open the adjustment to recompute "
                                "with the current cap.",
                            )
                            return
                except Exception:
                    # Fail open on the re-check itself: we already
                    # have a layered guard via the engine's cap-
                    # fallback path; if the recompute crashes due
                    # to schema weirdness, log and proceed.
                    logger.exception(
                        "Could not re-verify match cap at adjustment "
                        "save; proceeding with dialog snapshot")

            adjusted_by = dialog.adjusted_by_input.text().strip() or "Admin"
            reason = dialog.reason_combo.currentData() or dialog.reason_combo.currentText()
            notes = dialog.notes_input.toPlainText().strip()
            new_total_dollars = dialog.receipt_spin.value()
            new_total_cents = dollars_to_cents(new_total_dollars)
            new_vendor = dialog.vendor_combo.currentData()

            if new_total_cents <= 0:
                QMessageBox.warning(self, "Error",
                                    "Receipt total must be greater than $0.00.")
                return

            # ── Denomination + photo validation (parity with PaymentScreen) ──
            # PaymentScreen `_confirm_payment` validates these before
            # any DB write (lines 1924-1937).  Adjustments was missing
            # both: a manager could remove a mandatory photo or hand-
            # type a non-denomination amount during the edit and the
            # save would silently let it through.  Mirror the order
            # used in PaymentScreen so the user sees the same error
            # message for the same problem on either screen.
            for row in dialog._payment_rows:
                denom_error = row.validate_denomination()
                if denom_error:
                    QMessageBox.warning(
                        self, "Denomination Error", denom_error)
                    return
            for row in dialog._payment_rows:
                photo_error = row.validate_photo()
                if photo_error:
                    QMessageBox.warning(
                        self, "Photo Receipt Required", photo_error)
                    return

            # Warn if adjusted total exceeds the configurable threshold
            # (threshold is in dollars from app_settings)
            from fam.utils.app_settings import get_large_receipt_threshold
            threshold = get_large_receipt_threshold()
            if new_total_dollars > threshold:
                answer = QMessageBox.warning(
                    self, "Large Receipt",
                    f"Adjusted receipt total ${new_total_dollars:,.2f} exceeds the "
                    f"warning threshold of ${threshold:,.2f}.\n\n"
                    f"Are you sure this amount is correct?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return

            # Validate payment allocation if payments were edited (all cents)
            new_items = dialog.get_new_line_items()
            # Both flags live at this scope so the post-save message
            # path (further down) can read them whether or not the
            # ``if new_items:`` branch ran.
            unallocated_funds_cents = 0   # set when "customer is gone" path triggers
            denom_overage_cents = 0       # set when denomination forfeit accepted

            # ── Layer 2A: charge-integrity guard (parity with PaymentScreen) ──
            # The engine's capped ``customer_charged`` is what the
            # save will write — but if the manager edited the
            # spinbox after ``_update_customer_impact`` last ran (or
            # if the impact panel never recomputed because signals
            # were blocked), the spinbox value can disagree with the
            # engine output.  PaymentScreen ``_confirm_payment``
            # (lines 1985-2017) refuses to confirm in that state with
            # an explicit "Auto-Distribute or correct" prompt.  Mirror
            # the same guard here so the two flows behave identically
            # under the match cap — silent under-/over-charge bugs
            # caused real losses on the 2026-04 onsite stress test.
            if new_items:
                # Walk only the rows that have a method + non-zero
                # method_amount, in the same order ``get_new_line_items``
                # built ``new_items`` from.
                valid_rows = [
                    r for r in dialog._payment_rows
                    if r.get_data() and r.get_data()['method_amount'] > 0
                ]
                for i, row in enumerate(valid_rows):
                    if i >= len(new_items):
                        break
                    expected_charge = new_items[i]['customer_charged']
                    actual_charge = row._get_active_charge()
                    if expected_charge != actual_charge:
                        method = row.get_selected_method()
                        method_name = (method['name'] if method
                                       else f"row {i + 1}")
                        logger.error(
                            "Adjust charge-integrity guard tripped: "
                            "%s row shows %d cents but engine "
                            "computed %d cents — refusing to save",
                            method_name, actual_charge,
                            expected_charge,
                        )
                        QMessageBox.warning(
                            self, "Payment Row Mismatch",
                            f"Adjustment was blocked because the "
                            f"{method_name} row shows "
                            f"{format_dollars(actual_charge)} but "
                            f"the calculated charge after applying "
                            f"caps and reconciliation is "
                            f"{format_dollars(expected_charge)}.\n\n"
                            f"Click ⚡ Auto-Distribute or correct "
                            f"the {method_name} amount manually so "
                            f"the row matches the customer-impact "
                            f"panel, then save again."
                        )
                        return

            if new_items:
                allocated = sum(it['method_amount'] for it in new_items)
                gap = new_total_cents - allocated  # positive = customer owes more

                # ``customer_pay_delta`` measures how much MORE the
                # customer would be charged compared to the original
                # transaction.  Used by all three gates below so the
                # manager always knows whether they need to physically
                # collect more cash/instruments — regardless of whether
                # the gap shows up as a receipt-vs-allocation gap, a
                # denomination overage, or a balanced-allocation
                # breakdown shift.  Computed once up-front so all
                # branches see a consistent number.
                old_customer_paid = sum(
                    it.get('customer_charged', 0)
                    for it in dialog._original_items
                )
                new_customer_paid = sum(
                    it.get('customer_charged', 0) for it in new_items
                )
                customer_pay_delta = (
                    new_customer_paid - old_customer_paid
                )

                # ── Gap-triggered customer availability gate (v1.9.9+) ──
                # When an adjustment leaves a positive gap (the receipt
                # rose, or a payment shrank, faster than the breakdown
                # was rebuilt to match), most of the time the customer
                # has already left — managers reconcile vendor receipts
                # *after* market close.  Rather than blocking the save
                # with a "fix the payment" error and forcing the manager
                # to fabricate a fake payment row, we ask: can you still
                # charge them?  If no, FAM absorbs the gap as an
                # Unallocated Funds line item so the books stay
                # internally consistent and the loss is visible in
                # reports (separate from the FAM Match column).
                # Refunds (negative gap) are intentionally NOT prompted —
                # those follow the existing reconciliation-error path.
                if gap > 1:  # > 1 cent prevents float-rounding false alarms
                    msg = QMessageBox(self)
                    msg.setIcon(QMessageBox.Warning)
                    msg.setWindowTitle("Customer Available?")
                    msg.setText(
                        f"This adjustment leaves "
                        f"{format_dollars(gap)} unaccounted for — the "
                        f"customer would need to pay this much more."
                    )
                    msg.setInformativeText(
                        "Can the customer still be charged?\n\n"
                        "  • Yes — you'll collect the additional amount "
                        "before saving (you'll be returned to the "
                        "dialog to add the payment).\n"
                        "  • No — the customer has left.  Log "
                        f"{format_dollars(gap)} as Unallocated Funds — "
                        "FAM will absorb the loss and it will be "
                        "tracked separately in reports."
                    )
                    yes_btn = msg.addButton(
                        "Yes — collect from customer",
                        QMessageBox.YesRole)
                    no_btn = msg.addButton(
                        "No — customer is gone",
                        QMessageBox.NoRole)
                    msg.setDefaultButton(yes_btn)
                    msg.exec()

                    if msg.clickedButton() is no_btn:
                        # Inject Unallocated Funds for the gap.
                        # match_amount = 0 (this is FAM absorption,
                        # not a match), customer_charged = 0 (they
                        # didn't hand it over).
                        if _append_unallocated_funds_row(
                                new_items, gap) is None:
                            QMessageBox.critical(
                                self, "System Error",
                                "The 'Unallocated Funds' payment "
                                "method is missing.  This usually "
                                "means the schema migration to v25 "
                                "did not complete.  Restart the app "
                                "and try again, or contact support.")
                            return
                        unallocated_funds_cents = gap
                        # Recompute allocation so the standard
                        # reconciliation check below passes.
                        allocated = sum(it['method_amount']
                                        for it in new_items)
                    # If yes_btn (or dialog dismissed): fall through to
                    # the reconciliation error so the dialog can be
                    # reopened with the missing payment added manually.

                # ── Denomination overage (forfeit) ────────────────
                # A negative gap (allocation > receipt) is permitted
                # when caused by denominated payments whose face
                # value exceeds the remaining receipt headroom.  The
                # customer literally hands over physical checks/tokens
                # that can't be broken down — FAM matches only up to
                # the receipt and the customer forfeits the rest of
                # the FAM match they would have gotten.  Mirrors the
                # Payment screen's _check_denomination_overage +
                # _apply_denomination_forfeit accept-with-warning
                # flow so adjustments behave the same as initial
                # capture in this scenario.
                if gap < -1:
                    overage = -gap
                    from fam.utils.calculations import (
                        charge_to_method_amount,
                    )
                    effective_denom_sum = 0
                    for r in dialog._payment_rows:
                        m = r.get_selected_method()
                        if m and m.get('denomination'):
                            effective_denom_sum += (
                                charge_to_method_amount(
                                    m['denomination'],
                                    m['match_percent'],
                                )
                            )
                    # Bound check: overage must fit within ONE unit
                    # of effective denomination.  An overage larger
                    # than that means the manager added too many
                    # units (or non-denom rows are over-allocating)
                    # — fall through to the hard error.
                    if (effective_denom_sum > 0
                            and overage <= effective_denom_sum):
                        # Build the popup text dynamically.  The
                        # forfeit explanation always shows; the
                        # customer-collection prompt only when the
                        # adjustment ALSO bumps customer_charged
                        # over what was originally collected (the
                        # "you also need to collect $X more" gap the
                        # 2026-04 onsite found in the original
                        # popup wording).
                        # v2.0.7 (user-reported 2026-05-07): the
                        # adjust-screen forfeit pass is Phase A only
                        # (reduces match to balance the receipt).
                        # That's NOT a customer forfeit per the
                        # final policy — the customer never had the
                        # FAM match money to lose.  Phrase the
                        # popup as a math-balancing notification,
                        # not a customer-loss alert.
                        msg_lines = [
                            f"This adjustment over-allocates the "
                            f"receipt by {format_dollars(overage)} "
                            f"because the denominated payment "
                            f"cannot be broken into smaller "
                            f"increments.",
                            "",
                            f"FAM match will be reduced by "
                            f"{format_dollars(overage)} to keep the "
                            f"vendor reimbursement exact.  Vendor "
                            f"still receives the full receipt "
                            f"amount.",
                        ]
                        if customer_pay_delta > 1:
                            msg_lines += [
                                "",
                                f"⚠  The customer must also be "
                                f"charged "
                                f"{format_dollars(customer_pay_delta)} "
                                f"more than originally collected "
                                f"(was "
                                f"{format_dollars(old_customer_paid)}, "
                                f"now "
                                f"{format_dollars(new_customer_paid)})."
                                f"  Can the customer still be "
                                f"charged this additional amount?",
                            ]
                        else:
                            msg_lines += ["", "Save this adjustment?"]

                        msg = QMessageBox(self)
                        msg.setIcon(QMessageBox.Warning)
                        msg.setWindowTitle("Denomination Overage")
                        msg.setText("\n".join(msg_lines))
                        if customer_pay_delta > 1:
                            yes_btn = msg.addButton(
                                f"Yes — customer paid the extra "
                                f"{format_dollars(customer_pay_delta)}",
                                QMessageBox.YesRole)
                            no_btn = msg.addButton(
                                f"No — customer is gone, log "
                                f"{format_dollars(customer_pay_delta)} "
                                f"as Unallocated Funds",
                                QMessageBox.NoRole)
                        else:
                            yes_btn = msg.addButton(
                                "Yes", QMessageBox.YesRole)
                            no_btn = msg.addButton(
                                "Cancel", QMessageBox.RejectRole)
                        msg.setDefaultButton(yes_btn)
                        msg.exec()
                        clicked = msg.clickedButton()

                        if clicked is None or (
                                customer_pay_delta <= 1
                                and clicked is no_btn):
                            return  # cancelled / dismissed

                        # Apply forfeit FIRST (always required for
                        # denom overage so the line items reconcile
                        # to receipt total).
                        #
                        # v2.0.7-final consolidation (Option B,
                        # schema v36): delegate to the canonical
                        # ``apply_denomination_forfeit`` function
                        # in ``fam.utils.calculations`` so the
                        # vendor-aware Phase A + Phase B logic is
                        # identical to PaymentScreen's.  Pre-
                        # consolidation this branch ran a
                        # first-with-match Phase-A-only inline
                        # loop that diverged from PaymentScreen
                        # (no vendor binding, no Phase B
                        # token-value forfeit, no
                        # ``customer_forfeit_cents`` tags).  That
                        # divergence dropped Phase B forfeit data
                        # whenever a manager re-saved an
                        # adjustment.  Now both screens share one
                        # implementation and the parity test
                        # ``test_forfeit_consolidation_parity.py``
                        # pins byte-identical output across every
                        # realistic scenario.
                        denom_overage_cents = overage
                        # Build a minimal result dict shape the
                        # canonical fn expects.  Adjustment edits
                        # operate on a single transaction, so
                        # vendor_receipts has one entry: the
                        # transaction's vendor → receipt total.
                        forfeit_result = {
                            'line_items': new_items,
                            'allocated_total': sum(
                                int(it.get('method_amount', 0))
                                for it in new_items),
                            'fam_subsidy_total': sum(
                                int(it.get('match_amount', 0))
                                for it in new_items),
                            'customer_total_paid': sum(
                                int(it.get('customer_charged', 0))
                                for it in new_items),
                            'match_was_capped': bool(
                                dialog._match_limit is not None
                                and (sum(
                                    int(it.get('match_amount', 0))
                                    for it in new_items)
                                  >= dialog._match_limit)),
                        }
                        vendor_receipts: dict = {}
                        if current and current.get('vendor_id') is not None:
                            vendor_receipts[
                                current['vendor_id']] = new_total_cents
                        from fam.utils.calculations import (
                            apply_denomination_forfeit,
                        )
                        apply_denomination_forfeit(
                            forfeit_result, new_items, overage,
                            vendor_receipts=vendor_receipts)

                        # Then, if the manager said the customer is
                        # gone (No path), absorb the customer-pay
                        # delta into Unallocated Funds.  Reduces the
                        # customer_charged on existing rows so the
                        # saved customer_charged equals what was
                        # truly collected (the original amount), and
                        # injects an Unallocated Funds row for the
                        # method shortfall.
                        if (customer_pay_delta > 1
                                and clicked is no_btn):
                            uf_method_amount = (
                                _absorb_customer_pay_delta(
                                    new_items, customer_pay_delta)
                            )
                            if uf_method_amount > 0:
                                if _append_unallocated_funds_row(
                                        new_items,
                                        uf_method_amount) is None:
                                    QMessageBox.critical(
                                        self, "System Error",
                                        "Unallocated Funds method "
                                        "missing — cannot record "
                                        "absorption.")
                                    return
                                unallocated_funds_cents += (
                                    uf_method_amount
                                )

                        allocated = sum(it['method_amount']
                                        for it in new_items)

                # ── Customer-pay-delta gate (balanced allocation) ────
                # Independent of receipt-vs-allocation gap and of
                # denomination overage: when the manager rebuilt the
                # breakdown so the customer is recorded as paying MORE
                # than originally, prompt to confirm.  Without this
                # branch the screenshot scenario (Food Bucks 2 → 5,
                # receipt unchanged at $20) just shows a polite post-
                # save "collect $4.45 more from customer" info box,
                # giving the manager no chance to mark the gap as
                # Unallocated Funds when the customer's already gone.
                #
                # Only fires when neither of the prior two gates ran
                # (otherwise the customer-pay implication was already
                # surfaced in their popup wording).
                already_handled = (
                    unallocated_funds_cents > 0
                    or denom_overage_cents > 0
                )
                if (not already_handled
                        and customer_pay_delta > 1):
                    msg = QMessageBox(self)
                    msg.setIcon(QMessageBox.Warning)
                    msg.setWindowTitle("Customer Available?")
                    msg.setText(
                        f"This adjustment increases the customer's "
                        f"payment from "
                        f"{format_dollars(old_customer_paid)} to "
                        f"{format_dollars(new_customer_paid)} — they "
                        f"would need to pay "
                        f"{format_dollars(customer_pay_delta)} more "
                        f"than originally collected."
                    )
                    msg.setInformativeText(
                        "Can the customer still be charged?\n\n"
                        f"  • Yes — they paid the additional "
                        f"{format_dollars(customer_pay_delta)}.\n"
                        f"  • No — the customer has left.  Log "
                        f"{format_dollars(customer_pay_delta)} as "
                        f"Unallocated Funds (FAM absorbs)."
                    )
                    yes_btn = msg.addButton(
                        f"Yes — customer paid the extra "
                        f"{format_dollars(customer_pay_delta)}",
                        QMessageBox.YesRole)
                    no_btn = msg.addButton(
                        "No — customer is gone",
                        QMessageBox.NoRole)
                    msg.setDefaultButton(yes_btn)
                    msg.exec()

                    if msg.clickedButton() is no_btn:
                        uf_method_amount = (
                            _absorb_customer_pay_delta(
                                new_items, customer_pay_delta)
                        )
                        if uf_method_amount > 0:
                            if _append_unallocated_funds_row(
                                    new_items,
                                    uf_method_amount) is None:
                                QMessageBox.critical(
                                    self, "System Error",
                                    "Unallocated Funds method "
                                    "missing — cannot record "
                                    "absorption.")
                                return
                            unallocated_funds_cents = uf_method_amount
                            allocated = sum(it['method_amount']
                                            for it in new_items)
                    elif msg.clickedButton() is not yes_btn:
                        # Dismissed (X-out) — treat as cancel.
                        return

                if abs(allocated - new_total_cents) > 1:
                    QMessageBox.warning(
                        self, "Payment Mismatch",
                        f"Payment total ({format_dollars(allocated)}) does not match "
                        f"receipt total ({format_dollars(new_total_cents)}). "
                        f"Please fix the payment amounts."
                    )
                    return

            # ── Per-line invariant guard (Layer 2A parity) ───────────
            # The schema (v28+) enforces ``customer_charged +
            # match_amount = method_amount`` for every non-system
            # row via the ``chk_pli_invariant_*`` triggers.  Without
            # this guard, any future code path that produces an
            # inconsistent item lands the user on a raw SQL
            # ``IntegrityError`` dialog: "Adjustment failed:
            # customer_charged + match_amount must equal
            # method_amount."  Fail-fast here with a friendlier
            # message that names the row, so the manager can
            # identify which payment to fix instead of having to
            # parse the SQL error.
            #
            # Excludes Unallocated Funds (the ``method_name_snapshot``
            # the schema trigger also exempts) — UF rows intentionally
            # have customer=0, match=0, method>0 to surface FAM
            # absorption distinctly in reports.
            if new_items:
                for i, it in enumerate(new_items):
                    if it.get('method_name_snapshot') == 'Unallocated Funds':
                        continue
                    invariant = (
                        it.get('customer_charged', 0)
                        + it.get('match_amount', 0)
                    )
                    if invariant != it.get('method_amount', 0):
                        method_name = it.get(
                            'method_name_snapshot',
                            f'row {i + 1}')
                        logger.error(
                            "Adjust per-line invariant guard tripped: "
                            "%s row has customer_charged=%dc + "
                            "match_amount=%dc != method_amount=%dc — "
                            "refusing to save",
                            method_name,
                            it.get('customer_charged', 0),
                            it.get('match_amount', 0),
                            it.get('method_amount', 0),
                        )
                        QMessageBox.warning(
                            self, "Payment Row Inconsistent",
                            f"The {method_name} row's amounts don't "
                            f"reconcile internally:\n\n"
                            f"  customer_charged = "
                            f"{format_dollars(it.get('customer_charged', 0))}\n"
                            f"  match_amount     = "
                            f"{format_dollars(it.get('match_amount', 0))}\n"
                            f"  method_amount    = "
                            f"{format_dollars(it.get('method_amount', 0))}\n\n"
                            f"customer_charged + match_amount must "
                            f"equal method_amount.\n\n"
                            f"Click ⚡ Auto-Distribute or correct the "
                            f"{method_name} amount manually, then save "
                            f"again."
                        )
                        return

            # ── Atomic adjustment: all DB writes in one transaction ──

            conn = get_connection()
            try:
                anything_changed = False

                if new_total_cents != txn['receipt_total']:
                    log_action('transactions', txn_id, 'ADJUST', adjusted_by,
                                field_name='receipt_total',
                                old_value=txn['receipt_total'],
                                new_value=new_total_cents,
                                reason_code=reason, notes=notes, commit=False)
                    # v2.0.2 fix (UF-H6): _skip_audit=True suppresses the
                    # per-field UPDATE row that update_transaction would
                    # otherwise emit, eliminating duplicate audit rows
                    # (the explicit ADJUST row above carries the same
                    # before/after pair plus richer reason_code/notes).
                    update_transaction(
                        txn_id, receipt_total=new_total_cents,
                        commit=False, _skip_audit=True)
                    anything_changed = True

                if new_vendor != txn['vendor_id']:
                    log_action('transactions', txn_id, 'ADJUST', adjusted_by,
                                field_name='vendor_id',
                                old_value=txn['vendor_id'],
                                new_value=new_vendor,
                                reason_code=reason, notes=notes, commit=False)
                    update_transaction(
                        txn_id, vendor_id=new_vendor, commit=False,
                        _skip_audit=True)
                    anything_changed = True

                # Save payment line items if changed.
                # Auto-injected Unallocated Funds (the "customer gone"
                # path above) counts as a payment change even if the
                # manager didn't touch the breakdown rows themselves —
                # ``dialog.payments_changed()`` compares against the
                # dialog's UI state, which doesn't include the row we
                # just appended.  Force the save through in that case.
                old_items = dialog._original_items
                payments_did_change = (
                    dialog.payments_changed()
                    or unallocated_funds_cents > 0
                )
                if payments_did_change and new_items:
                    old_summary = ", ".join(
                        f"{it['method_name_snapshot']}={format_dollars(it['method_amount'])}"
                        for it in old_items
                    )
                    new_summary = ", ".join(
                        f"{it['method_name_snapshot']}={format_dollars(it['method_amount'])}"
                        for it in new_items
                    )
                    log_action('payment_line_items', txn_id,
                               'PAYMENT_ADJUSTED', adjusted_by,
                               field_name='payment_methods',
                               old_value=old_summary,
                               new_value=new_summary,
                               reason_code=reason, notes=notes, commit=False)
                    save_payment_line_items(txn_id, new_items, commit=False)
                    anything_changed = True

                # Dedicated audit entry for an Unallocated Funds
                # injection.  Distinct from PAYMENT_ADJUSTED so the
                # Activity Log surfaces "FAM absorbed $X because the
                # customer was unavailable" as its own story — the
                # reason_code is auto-set so the manager isn't asked
                # to pick it from the dropdown (the popup answer
                # already disambiguated the intent).
                if unallocated_funds_cents > 0:
                    log_action(
                        'payment_line_items', txn_id,
                        'UNALLOCATED_FUNDS', adjusted_by,
                        field_name='unallocated_amount',
                        old_value=0,
                        new_value=unallocated_funds_cents,
                        reason_code='unallocated_funds',
                        notes=(
                            f"Customer unavailable to pay "
                            f"{format_dollars(unallocated_funds_cents)} "
                            f"after adjustment - FAM absorbing as "
                            f"Unallocated Funds."
                            + (f"  Manager note: {notes}" if notes else "")
                        ),
                        commit=False,
                    )

                # Only mark as Adjusted if something actually changed.
                # _skip_audit=True so we don't double-emit per-field
                # rows; the ADJUST + PAYMENT_ADJUSTED rows above already
                # carry the explanation.
                if anything_changed:
                    update_transaction(
                        txn_id, status='Adjusted', commit=False,
                        _skip_audit=True)

                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.exception("Failed to adjust transaction %s", txn_id)
                # v2.0.7: include the log path in the error dialog
                # so the volunteer / coordinator can find the full
                # traceback without having to figure out whether
                # they're on the .exe (which writes to %APPDATA%
                # \FAM Market Manager\fam_manager.log) or running
                # from source (which writes to <project root>\
                # fam_manager.log).  The user-reported v2.0.7
                # UnboundLocalError incident WAS logged correctly,
                # but the user looked at the .exe path while
                # actually running from source — so they saw "no
                # error in the logs" when in fact the error was
                # one directory away.  Surfacing the path inline
                # closes that diagnostic gap.
                try:
                    from fam.utils.logging_config import get_log_path
                    log_path_str = get_log_path() or '(unknown)'
                except Exception:
                    log_path_str = '(unknown)'
                QMessageBox.critical(
                    self,
                    "Error",
                    f"Adjustment failed: {e}\n\n"
                    f"Full traceback was written to:\n"
                    f"{log_path_str}\n\n"
                    f"You can also view it in Reports → Error Log "
                    f"or copy a diagnostic from Help → System "
                    f"Status → Copy Diagnostic Info.",
                )
                return

            write_ledger_backup()
            self._search()
            self._load_audit_log()

            # Notify listeners (main window wires this to cloud sync) that
            # transaction/payment data changed.  Fires on any successful
            # adjustment, even if only the vendor or notes changed.
            #
            # v2.0.6: emit the affected market_day_id so the sync
            # collects from THAT day (which may be CLOSED).  Pre-fix
            # the bare emit() left the sync to narrow to whatever
            # day was open right now, silently skipping closed-day
            # adjustments.
            if anything_changed:
                affected_md_id = (current.get('market_day_id')
                                   or txn.get('market_day_id') or 0)
                self.data_changed.emit(int(affected_md_id))

            # Show customer impact message after save.
            # When the "customer gone" path injected Unallocated
            # Funds, the diff message would otherwise tell the
            # manager to "collect more from the customer" — the
            # exact opposite of the intent the popup just confirmed.
            # Show a tailored message instead so the loss is visible.
            if payments_did_change and new_items:
                old_customer = sum(
                    it['customer_charged'] for it in old_items
                )  # cents
                new_customer = sum(
                    it['customer_charged'] for it in new_items
                )  # cents
                diff = new_customer - old_customer
                if unallocated_funds_cents > 0:
                    QMessageBox.information(
                        self, "Unallocated Funds Logged",
                        f"Adjustment saved.\n\n"
                        f"{format_dollars(unallocated_funds_cents)} "
                        f"recorded as Unallocated Funds — FAM "
                        f"absorbing this amount because the customer "
                        f"was unavailable.\n\n"
                        f"This appears as a separate column in "
                        f"Vendor Reimbursement and the Detailed "
                        f"Ledger, and as 'FAM Absorbed' in the FAM "
                        f"Match Report."
                    )
                elif denom_overage_cents > 0:
                    # Customer paid in physical denominated
                    # instruments that overshot the receipt.  The
                    # math-balance was already explained + accepted
                    # in the pre-save QMessageBox; this is just a
                    # post-save confirmation so the manager knows
                    # the adjustment landed with the reduced match.
                    #
                    # v2.0.7 (user-reported 2026-05-07): dialog
                    # title and wording avoid "Forfeit" terminology
                    # — Phase A FAM-match reduction is not a
                    # customer-side loss.  "Customer Forfeit" is
                    # reserved for Phase B token-value loss.
                    QMessageBox.information(
                        self, "Adjustment Saved",
                        f"Adjustment saved.\n\n"
                        f"FAM match reduced by "
                        f"{format_dollars(denom_overage_cents)} to "
                        f"keep the vendor reimbursement exact.  "
                        f"Customer paid "
                        f"{format_dollars(new_customer)} in physical "
                        f"instruments; FAM contributes "
                        f"{format_dollars(new_total_cents - new_customer)} "
                        f"to bring the vendor's reimbursement to "
                        f"the receipt total of "
                        f"{format_dollars(new_total_cents)}."
                    )
                elif abs(diff) >= 1:
                    if diff > 0:
                        impact_msg = (
                            f"Adjustment saved.\n\n"
                            f"If the original payment was collected, "
                            f"collect {format_dollars(diff)} more from the customer.\n"
                            f"(Was {format_dollars(old_customer)}, "
                            f"now {format_dollars(new_customer)})"
                        )
                    else:
                        impact_msg = (
                            f"Adjustment saved.\n\n"
                            f"If the original payment was collected, "
                            f"refund {format_dollars(abs(diff))} to the customer.\n"
                            f"(Was {format_dollars(old_customer)}, "
                            f"now {format_dollars(new_customer)})"
                        )
                    QMessageBox.information(
                        self, "Customer Impact", impact_msg
                    )

    def _detect_adjustment_risk(self, txn) -> tuple[set, int]:
        """Inspect a transaction for denom-payment adjustment risk.

        Returns ``(denom_method_names, sibling_count)``:
          * ``denom_method_names`` is the set of distinct
            denominated payment-method names attached to this
            transaction's payment_line_items.  Empty set means
            no denom payments — adjusting is safe.
          * ``sibling_count`` is the total number of transactions
            in this customer's order that share the same vendor
            (including the transaction being adjusted).  When > 1
            the multi-receipt single-vendor shape applies and
            adjustments are higher-risk.

        Returns (set(), 0) when txn lacks the metadata to evaluate.
        """
        conn = get_connection()
        denom_methods: set[str] = set()
        try:
            rows = conn.execute(
                "SELECT pli.method_name_snapshot, pm.denomination "
                "FROM payment_line_items pli "
                "LEFT JOIN payment_methods pm "
                "  ON pm.id = pli.payment_method_id "
                "WHERE pli.transaction_id = ?",
                (txn['id'],),
            ).fetchall()
            for r in rows:
                denom = r['denomination']
                if denom and denom > 0:
                    denom_methods.add(r['method_name_snapshot'])
        except Exception:
            logger.exception(
                "Adjustment-risk detection failed reading PLI for "
                "txn=%s; defaulting to no denom risk", txn.get('id'))
            return set(), 0

        # Sibling count at same vendor in same customer order
        sibling_count = 0
        order_id = txn.get('customer_order_id')
        vendor_id = txn.get('vendor_id')
        if order_id is not None and vendor_id is not None:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM transactions "
                    "WHERE customer_order_id = ? "
                    "  AND vendor_id = ? "
                    "  AND status IN ('Confirmed', 'Adjusted')",
                    (order_id, vendor_id),
                ).fetchone()
                if row:
                    sibling_count = row['n']
            except Exception:
                logger.exception(
                    "Adjustment-risk sibling-count query failed "
                    "for txn=%s", txn.get('id'))

        return denom_methods, sibling_count

    def _void_transaction(self, txn_id):
        txn = get_transaction_by_id(txn_id)
        if not txn:
            return

        if txn['status'] == 'Voided':
            QMessageBox.warning(self, "Already Voided",
                                "This transaction has already been voided.")
            return

        result = QMessageBox.warning(
            self, "Void Transaction",
            f"Are you sure you want to void transaction {txn['fam_transaction_id']}?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )
        if result == QMessageBox.Yes:

            conn = get_connection()
            try:
                # v1.9.10 follow-up (2026-05-01): delegate to the
                # model's ``void_transaction`` so the void code path
                # is single-sourced.
                #
                # v2.0.2 fix (UF-H1): pass ``commit=False`` and bundle
                # the void + parent-order-status flip into a single
                # ``conn.commit()`` below.  Pre-fix the model
                # committed before the order-status query ran — a
                # transient ``database is locked`` between them
                # would leave the txn voided but the order still
                # Confirmed, so reports/audit silently disagreed
                # about whether the order was active.
                from fam.models.transaction import void_transaction
                from fam.models.customer_order import (
                    update_customer_order_status,
                )
                open_md = get_open_market_day()
                changed_by = (open_md.get('opened_by')
                              if open_md else None) or 'Admin'
                void_transaction(txn_id, voided_by=changed_by,
                                 commit=False)

                # M1 fix: when the LAST non-voided transaction in
                # the parent customer_order is voided, also flip
                # the order's status to 'Voided'.  This keeps the
                # order-level state aligned with the underlying
                # transactions so reports that filter by
                # ``customer_orders.status`` don't miss
                # functionally-voided orders.
                if txn.get('customer_order_id'):
                    co_id = txn['customer_order_id']
                    remaining = conn.execute(
                        "SELECT COUNT(*) FROM transactions "
                        "WHERE customer_order_id=? AND status != 'Voided'",
                        (co_id,)
                    ).fetchone()[0]
                    if remaining == 0:
                        update_customer_order_status(
                            co_id, 'Voided',
                            changed_by=changed_by, commit=False)

                # Single atomic commit for the whole void+order
                # status flip — either both land or neither.
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.exception("Failed to void transaction %s", txn_id)
                QMessageBox.critical(self, "Error", f"Void failed: {e}")
                return
            write_ledger_backup()
            self._search()
            self._load_audit_log()
            # Void is a data mutation — signal the main window to sync.
            # v2.0.6: pass the affected market_day_id so the sync
            # scopes to THAT day (which may be CLOSED) rather than
            # only the currently-open day.
            self.data_changed.emit(int(txn.get('market_day_id') or 0))

    def _load_audit_log(self):
        entries = get_audit_log(limit=20)
        self.audit_table.setSortingEnabled(False)
        self.audit_table.setRowCount(len(entries))
        for i, e in enumerate(entries):
            self.audit_table.setItem(i, 0, make_item(str(e.get('changed_at', ''))))
            self.audit_table.setItem(i, 1, make_item(e['table_name']))
            self.audit_table.setItem(i, 2, make_item(str(e['record_id']), e['record_id']))
            self.audit_table.setItem(i, 3, make_item(e['action']))
            self.audit_table.setItem(i, 4, make_item(e.get('changed_by') or ''))
            self.audit_table.setItem(i, 5, make_item(e.get('field_name') or ''))
            self.audit_table.setItem(i, 6, make_item(str(e.get('old_value') or '')))
            self.audit_table.setItem(i, 7, make_item(str(e.get('new_value') or '')))
            self.audit_table.setRowHeight(i, 30)
        self.audit_table.setSortingEnabled(True)

    # ------------------------------------------------------------------
    # Receipt printing
    # ------------------------------------------------------------------

    def _build_receipt_data_for_transaction(self, txn_id: int) -> dict | None:
        """Build receipt data dict for a transaction (and its sibling order transactions)."""
        try:
            from fam.models.customer_order import get_customer_order, get_order_transactions

            txn = get_transaction_by_id(txn_id)
            if not txn:
                return None

            market_name = txn.get('market_name', '')
            market_date = txn.get('market_day_date', '')
            confirmed_by = txn.get('confirmed_by', '') or 'Volunteer'
            status = txn.get('status', 'Confirmed')
            customer_label = ''

            # Gather all transactions belonging to the same customer order
            order_txns = []
            order_id = txn.get('customer_order_id')
            if order_id:
                order = get_customer_order(order_id)
                if order:
                    customer_label = order.get('customer_label', '')
                # Include all non-voided sibling transactions
                order_txns = get_order_transactions(order_id)
            if not order_txns:
                # Standalone transaction (no order) — just use this one
                order_txns = [txn]

            txns = []
            total_receipt = 0  # cents
            total_customer = 0  # cents
            total_match = 0  # cents
            payment_totals: dict[str, dict] = {}

            for t in order_txns:
                tid = t['id']
                full_txn = get_transaction_by_id(tid)
                receipt = full_txn['receipt_total']  # integer cents
                total_receipt += receipt
                line_items = get_payment_line_items(tid)

                txns.append({
                    'fam_id': full_txn['fam_transaction_id'],
                    'vendor': full_txn.get('vendor_name', ''),
                    'receipt_total': cents_to_dollars(receipt),
                })

                for li in line_items:
                    method = li['method_name_snapshot']
                    amt = li['method_amount']  # cents
                    match = li['match_amount']  # cents
                    cust = li['customer_charged']  # cents
                    total_customer += cust
                    total_match += match
                    if method not in payment_totals:
                        payment_totals[method] = {
                            'amount': 0, 'match': 0, 'customer': 0,
                        }
                    payment_totals[method]['amount'] += amt
                    payment_totals[method]['match'] += match
                    payment_totals[method]['customer'] += cust

            # Convert accumulated cents to dollars at the UI boundary
            payment_totals_dollars = {
                m: {
                    'amount': cents_to_dollars(v['amount']),
                    'match': cents_to_dollars(v['match']),
                    'customer': cents_to_dollars(v['customer']),
                }
                for m, v in payment_totals.items()
            }

            return {
                'market_name': market_name,
                'market_date': market_date,
                'customer_label': customer_label,
                'confirmed_by': confirmed_by,
                'status': status,
                'transactions': txns,
                'payment_totals': payment_totals_dollars,
                'total_receipt': cents_to_dollars(total_receipt),
                'total_customer': cents_to_dollars(total_customer),
                'total_match': cents_to_dollars(total_match),
            }
        except Exception:
            logger.exception("Failed to build receipt data for txn %s", txn_id)
            return None

    def _print_receipt(self, txn_id: int):
        """Open a print dialog with a formatted customer receipt for a transaction."""
        data = self._build_receipt_data_for_transaction(txn_id)
        if not data:
            QMessageBox.information(self, "Print Receipt",
                                    "No receipt data available for this transaction.")
            return

        from fam.ui.payment_screen import PaymentScreen
        html = PaymentScreen._format_receipt_html(data)

        from PySide6.QtPrintSupport import QPrinter, QPrintDialog
        from PySide6.QtGui import QTextDocument

        printer = QPrinter(QPrinter.HighResolution)
        printer.setDocName("FAM_Receipt")

        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("Print Customer Receipt")
        if dlg.exec() == QPrintDialog.Accepted:
            doc = QTextDocument()
            doc.setHtml(html)
            doc.print_(printer)
            logger.info("Receipt printed from Adjustments for txn %s", txn_id)
