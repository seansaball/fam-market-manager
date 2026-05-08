"""Payment Confirmation Dialog (v1.9.9 redesign).

Replaces the v1.9.x ``QMessageBox.question`` with a structured
visual layout that separates *informative* content from *actionable*
content from *warning* content — and uses a marching-ants animated
border around the actionable zone so volunteers can't miss what they
need to do.

The redesign was prompted by the realisation that volunteers were
clicking Confirm without fully reading a long blurb of plain text,
sometimes forgetting to actually process SNAP on the external EBT
terminal first.  The new dialog enforces that step with a required
checkbox per external-device method that gates the Confirm button.

Visual zones (top to bottom):

  1. **Title strip** — "Confirm Payment Collection" + receipt count
     and order total context (small, gray).

  2. **Action zone** — wrapped in a ``MarchingAntsFrame`` whose
     dashed border animates ~14fps so it draws the eye.  Per-method
     "COLLECT" rows with large bold amounts, plus:

       * **External-device** methods (SNAP/EBT — name-detected):
         red "⚠ EXTERNAL DEVICE — process on EBT terminal first"
         badge AND a *required* checkbox.  The Confirm button stays
         disabled until every external-device row's checkbox is
         ticked.  This is the forcing function.

       * **Denominated** methods (Food Bucks, FMNP-as-payment with
         a non-null denomination): blue "📋 PHYSICAL — collect N × $D"
         badge.  Informational; the physical instruments either exist
         or they don't, no checkbox needed.

       * Other methods: plain row, no badge.

     Footer of the action zone: a "TOTAL TO COLLECT" row in 24px
     bold green — what the customer actually hands over.

  3. **Warning zone** (only when ``denom_overage > 0``) — amber
     background, distinct border, "DENOMINATION OVERAGE — $X forfeit"
     header + explanation.  Visually segregated from the action zone
     so the volunteer doesn't conflate "FAM forfeit" with "collect
     more from customer".

  4. **Informative footer** — vendor reimbursement + FAM match
     totals in small gray text.  Context only; never an action.

  5. **Buttons** — large color-coded Cancel + Confirm.  Confirm
     turns green when all required checkboxes are ticked, otherwise
     gray + disabled.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from fam.ui.styles import (
    ACCENT_GREEN, BACKGROUND, ERROR_COLOR, PRIMARY_GREEN,
    SUBTITLE_GRAY, TEXT_COLOR, WARNING_BG, WARNING_COLOR, WHITE,
)
from fam.utils.money import format_dollars


# ── Method-type detection ────────────────────────────────────────
#
# External-device methods: payment types whose value transfers
# happen on a separate physical device the volunteer must operate
# (EBT/SNAP terminal).  These get the most aggressive UX treatment —
# a red badge + required checkbox — because forgetting to process
# the swipe is the canonical "money disappeared" failure mode at
# every market.  Detection is by substring on the method name; the
# keywords cover the common variants.
_EXTERNAL_DEVICE_KEYWORDS: tuple[str, ...] = ('snap', 'ebt')


def is_external_device_method(method_name: str) -> bool:
    """True when *method_name* designates a payment processed on an
    external device (the volunteer must run a separate transaction
    at an EBT/SNAP terminal before confirming in FAM Manager).

    Module-level + public so tests can import it directly without
    instantiating the dialog.
    """
    if not method_name:
        return False
    lower = method_name.lower()
    return any(kw in lower for kw in _EXTERNAL_DEVICE_KEYWORDS)


def is_denominated_method(denomination_cents) -> bool:
    """True when the method has a non-null, positive denomination
    (i.e. backed by a physical instrument with a fixed face value
    — Food Bucks tokens, FMNP checks)."""
    return bool(denomination_cents) and denomination_cents > 0


# ── Marching-ants animated border ────────────────────────────────

class MarchingAntsFrame(QFrame):
    """A ``QFrame`` whose border is animated as 'marching ants' — a
    dashed outline whose dashes flow along the perimeter at a steady
    rate.  Used to wrap the dialog's action zone so volunteers
    actively notice the rows they need to act on, without being
    visually overwhelming.

    Stop the animation explicitly via :meth:`stopAnimation` after
    the volunteer has acknowledged the actions (typically when they
    click Confirm).  The widget is otherwise idempotent — drop into
    a layout, the animation auto-starts.

    Animation rate: ~14fps (70ms timer interval, 0.6 px phase
    advance).  Tuned by hand to be readable rather than seizure-
    inducing; do not turn this up.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase: float = 0.0
        self._animating: bool = True
        # Use a QTimer rather than QPropertyAnimation so the rate is
        # decoupled from any global Qt animation pause / step.
        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._advance)
        self._timer.start()

    def _advance(self):
        # Dash period is 8 (4 dash + 4 gap); phase wraps so the
        # animation loops smoothly with no visible jump.
        self._phase = (self._phase + 0.6) % 8.0
        self.update()

    def stopAnimation(self):
        """Halt the marching-ants animation.  After calling, the
        border draws statically with no further timer ticks."""
        self._animating = False
        self._timer.stop()
        self.update()

    def isAnimating(self) -> bool:
        return self._animating

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(ACCENT_GREEN), 2.5)
        pen.setDashPattern([4, 4])
        # Negative offset so the dashes appear to flow clockwise —
        # natural reading direction.  When stopped, the offset
        # freezes wherever the last tick left it (acceptable; the
        # static dashes still distinguish the action zone).
        pen.setDashOffset(-self._phase)
        painter.setPen(pen)
        margin = 3
        rect = self.rect().adjusted(margin, margin, -margin, -margin)
        painter.drawRoundedRect(rect, 10, 10)


# ── Dialog ───────────────────────────────────────────────────────

class PaymentConfirmationDialog(QDialog):
    """Final confirmation dialog before committing a payment.

    Drop-in replacement for the previous ``QMessageBox.question``;
    accepts the engine's ``line_items``, the per-row ``items`` list
    (with method_name_snapshot + optional denomination), the receipt
    total, the ``denom_overage`` (or 0), and the receipt count.

    Returns ``QDialog.Accepted`` when the volunteer confirms (and
    every required checkbox is ticked), otherwise ``QDialog.Rejected``.
    """

    def __init__(self, line_items, items, receipt_total, denom_overage,
                 receipt_count, parent=None, reward_lines=None):
        """Construct the confirmation dialog.

        Args:
            line_items, items, receipt_total, denom_overage,
            receipt_count: as before — the financial pipeline data.
            reward_lines: optional list of ``RewardLine`` (from
                ``fam.utils.rewards``).  When non-empty, an
                informational rewards zone appears below the action
                zone instructing the cashier to hand the customer
                physical scrip tokens.  Rewards are NOT financial:
                they don't affect the action zone, the totals, or
                any reimbursement.  When ``None`` or empty, the
                rewards zone is suppressed entirely (rewards
                feature disabled, or no rule fired for this order).
        """
        super().__init__(parent)
        self.setWindowTitle("Confirm Payment Collection")
        self.setMinimumWidth(580)
        self.setStyleSheet(f"QDialog {{ background-color: {BACKGROUND}; }}")

        # State holders — populated as the action rows are built so
        # the Confirm-enabled gate sees them.
        self._required_checkboxes: list[QCheckBox] = []
        self._marching_ants_frames: list[MarchingAntsFrame] = []

        # Aggregate the action rows up-front so totals + warning
        # visibility decisions happen once.
        action_items = []
        for i, li in enumerate(line_items):
            if li['method_amount'] <= 0:
                continue
            method_name = items[i].get('method_name_snapshot', 'Unknown')
            denomination = items[i].get('denomination')
            action_items.append({
                'method_name': method_name,
                'customer_charged': li['customer_charged'],
                'match_amount': li['match_amount'],
                'method_amount': li['method_amount'],
                'is_external': is_external_device_method(method_name),
                'is_denominated': is_denominated_method(denomination),
                'denomination': denomination,
            })

        customer_total = sum(it['customer_charged'] for it in action_items)
        match_total = sum(it['match_amount'] for it in action_items)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 18)
        outer.setSpacing(12)

        outer.addWidget(self._build_title())
        outer.addWidget(self._build_subtitle(receipt_count, receipt_total))
        outer.addWidget(self._build_action_zone(action_items, customer_total))
        # v2.0.7 final policy (user-reported 2026-05-07): the
        # warning zone fires ONLY for Phase B forfeit (true
        # customer-side token-value loss).  Phase A (FAM match
        # reduction without token-value loss) is NOT a forfeit
        # from the customer's perspective — the customer never
        # had the FAM match money to lose; FAM just contributes
        # less because the receipt has no headroom.  The vendor
        # still gets the full receipt amount, the customer still
        # gets the full token's worth of food.  No alarm needed.
        # ``denom_overage`` (= Phase A + Phase B summed) is kept
        # as a constructor argument for backward compatibility
        # and downstream tests, but is no longer the trigger.
        customer_forfeit_total = sum(
            li.get('customer_forfeit_cents', 0) or 0
            for li in line_items)
        if customer_forfeit_total > 0:
            outer.addWidget(
                self._build_warning_zone(customer_forfeit_total))
        # Rewards zone (v1.9.10+) — purely informational.  Lives
        # BELOW the action+warning zones so the volunteer always
        # finishes the financial transaction first; rewards are an
        # add-on, not a gating step.
        if reward_lines:
            outer.addWidget(self._build_rewards_zone(reward_lines))
        outer.addWidget(self._build_info_footer(
            receipt_total, match_total,
            customer_forfeit_total))
        outer.addLayout(self._build_button_row())

        self._update_confirm_enabled()

    # ── Layout helpers ───────────────────────────────────────────

    def _build_title(self) -> QLabel:
        title = QLabel("💳  Confirm Payment Collection")
        title.setStyleSheet(f"""
            QLabel {{
                font-size: 18px;
                font-weight: bold;
                color: {TEXT_COLOR};
                background: transparent;
            }}
        """)
        return title

    def _build_subtitle(self, receipt_count: int,
                        receipt_total: int) -> QLabel:
        plural = 's' if receipt_count != 1 else ''
        sub = QLabel(
            f"{receipt_count} receipt{plural}  ·  Order total: "
            f"<b>{format_dollars(receipt_total)}</b>"
        )
        # Bumped from 12px → 14px for accessibility.  Elderly
        # volunteers reported the original size was hard to scan.
        # 14px is the floor for contextual text in this dialog.
        sub.setStyleSheet(f"""
            QLabel {{
                font-size: 14px;
                color: {SUBTITLE_GRAY};
                background: transparent;
            }}
        """)
        return sub

    def _build_action_zone(self, action_items: list,
                           customer_total: int) -> MarchingAntsFrame:
        wrap = MarchingAntsFrame()
        self._marching_ants_frames.append(wrap)
        wrap.setStyleSheet(f"""
            MarchingAntsFrame {{
                background-color: {WHITE};
                border-radius: 10px;
            }}
        """)
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        header = QLabel("📋  COLLECT FROM CUSTOMER")
        header.setStyleSheet(f"""
            QLabel {{
                font-size: 14px;
                font-weight: bold;
                color: {ACCENT_GREEN};
                letter-spacing: 1.5px;
                background: transparent;
            }}
        """)
        lay.addWidget(header)

        for it in action_items:
            lay.addWidget(self._build_action_row(it))

        # Separator + grand total
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #E0E0E0;")
        lay.addWidget(sep)

        total_row = QHBoxLayout()
        total_lbl = QLabel("TOTAL TO COLLECT")
        total_lbl.setStyleSheet(f"""
            QLabel {{
                font-size: 13px;
                font-weight: bold;
                color: {TEXT_COLOR};
                letter-spacing: 1px;
                background: transparent;
            }}
        """)
        total_amt = QLabel(format_dollars(customer_total))
        total_amt.setStyleSheet(f"""
            QLabel {{
                font-size: 24px;
                font-weight: bold;
                color: {PRIMARY_GREEN};
                background: transparent;
            }}
        """)
        total_amt.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        total_row.addWidget(total_lbl)
        total_row.addStretch()
        total_row.addWidget(total_amt)
        lay.addLayout(total_row)

        return wrap

    def _build_action_row(self, item: dict) -> QWidget:
        """A single 'collect $X via Y' row.  Adds a red badge +
        required checkbox for external-device methods, or a blue
        physical-instrument badge for denominated methods.

        Layout (post-2026-04-29 UX feedback):

            • SNAP: $5.50    FAM matches $5.50

        The method name and the amount are visually paired ("METHOD:
        $X.XX") rather than split to opposite edges of the card.
        Coordinator feedback: the previous left-name / right-amount
        layout disconnected the two halves of what's really a
        single fact ("collect $X for METHOD"), forcing the eye to
        track across whitespace.
        """
        wrap = QWidget()
        wlay = QVBoxLayout(wrap)
        wlay.setContentsMargins(0, 0, 0, 0)
        wlay.setSpacing(4)

        # Main row: bullet • Name: $Amount   FAM matches $Y
        # Spacing 6px keeps the name+amount tight as a pair while
        # leaving comfortable air around the auxiliary match note.
        h = QHBoxLayout()
        h.setSpacing(6)

        bullet = QLabel("•")
        bullet.setStyleSheet(
            f"color: {ACCENT_GREEN}; font-size: 18px; "
            f"font-weight: bold; background: transparent;")
        h.addWidget(bullet)

        # Name and amount as a tight pair.  Two adjacent QLabels
        # (rather than one rich-text QLabel) so the amount can
        # carry its own colour and weight (PRIMARY_GREEN bold)
        # while the name stays neutral TEXT_COLOR.
        name = QLabel(f"{item['method_name']}:")
        name.setStyleSheet(f"""
            QLabel {{
                font-size: 16px;
                font-weight: bold;
                color: {TEXT_COLOR};
                background: transparent;
            }}
        """)
        h.addWidget(name)

        amount = QLabel(format_dollars(item['customer_charged']))
        amount.setStyleSheet(f"""
            QLabel {{
                font-size: 18px;
                font-weight: bold;
                color: {PRIMARY_GREEN};
                background: transparent;
            }}
        """)
        h.addWidget(amount)

        # Optional FAM-match note follows after a small gap.
        # Lighter weight than the name+amount pair so the hierarchy
        # stays clear: "what to collect" first, "how it's funded"
        # second.
        if item['match_amount'] > 0:
            h.addSpacing(8)
            match_note = QLabel(
                f"FAM matches {format_dollars(item['match_amount'])}")
            match_note.setStyleSheet(f"""
                QLabel {{
                    font-size: 13px;
                    color: {TEXT_COLOR};
                    background: transparent;
                }}
            """)
            h.addWidget(match_note)

        # Trailing stretch absorbs the remaining width on the
        # right.  We deliberately do NOT push the amount to the
        # right edge — the previous design did, and coordinators
        # said it disconnected the amount from the method name.
        h.addStretch()
        wlay.addLayout(h)

        # External-device row gets the red badge + required checkbox
        if item['is_external']:
            badge_row = QHBoxLayout()
            badge_row.setContentsMargins(20, 0, 0, 0)
            badge = QLabel(
                "⚠  EXTERNAL DEVICE — process on EBT terminal first")
            # Bumped from 10px → 12px for accessibility.  The badge
            # carries a critical instruction that elderly volunteers
            # need to read at a glance — undersizing it defeated the
            # forcing-function design intent.
            badge.setStyleSheet(f"""
                QLabel {{
                    background-color: {ERROR_COLOR};
                    color: {WHITE};
                    font-size: 12px;
                    font-weight: bold;
                    letter-spacing: 0.5px;
                    padding: 5px 12px;
                    border-radius: 4px;
                }}
            """)
            badge_row.addWidget(badge)
            badge_row.addStretch()
            wlay.addLayout(badge_row)

            cb_row = QHBoxLayout()
            cb_row.setContentsMargins(20, 0, 0, 0)
            cb = QCheckBox(
                f"I have processed {item['method_name']} on the EBT "
                f"terminal and the transaction was approved")
            # Bumped from 12px → 14px for accessibility — this is
            # the load-bearing microcopy of the entire forcing
            # function, the volunteer literally has to read it
            # before the Confirm button enables.  Indicator also
            # grew from 18 → 22px so the click target is finger-
            # friendly on touch laptops.
            cb.setStyleSheet(f"""
                QCheckBox {{
                    font-size: 14px;
                    color: {TEXT_COLOR};
                    padding: 4px;
                    background: transparent;
                }}
                QCheckBox::indicator {{
                    width: 22px;
                    height: 22px;
                    border: 2px solid {ERROR_COLOR};
                    border-radius: 4px;
                    background-color: {WHITE};
                }}
                QCheckBox::indicator:checked {{
                    background-color: {ACCENT_GREEN};
                    border-color: {PRIMARY_GREEN};
                }}
            """)
            cb.toggled.connect(self._update_confirm_enabled)
            self._required_checkboxes.append(cb)
            cb_row.addWidget(cb)
            cb_row.addStretch()
            wlay.addLayout(cb_row)

        # Denominated row gets the blue physical-instrument badge
        elif item['is_denominated'] and item['denomination']:
            count = max(1, int(round(
                item['customer_charged'] / item['denomination']
            )))
            denom_dollars = item['denomination'] / 100.0
            badge_row = QHBoxLayout()
            badge_row.setContentsMargins(20, 0, 0, 0)
            badge = QLabel(
                f"📋  PHYSICAL — collect {count} × ${denom_dollars:.0f}"
            )
            # Bumped from 10px → 12px for accessibility (matches
            # the EXTERNAL DEVICE badge size).  This badge tells
            # the volunteer the exact unit count to collect — the
            # opposite of fine print.
            badge.setStyleSheet(f"""
                QLabel {{
                    background-color: #4A90E2;
                    color: {WHITE};
                    font-size: 12px;
                    font-weight: bold;
                    letter-spacing: 0.5px;
                    padding: 4px 12px;
                    border-radius: 4px;
                }}
            """)
            badge_row.addWidget(badge)
            badge_row.addStretch()
            wlay.addLayout(badge_row)

        return wrap

    def _build_warning_zone(
            self, customer_forfeit_cents: int) -> QFrame:
        """Build the amber warning zone for a Phase B token-value
        forfeit (the only forfeit class we surface to volunteers).

        v2.0.7 final policy (user-reported 2026-05-07):

          * **Phase A — FAM match reduction.** When a denominated
            payment's normal FAM match wouldn't fit the receipt,
            the engine reduces match to keep the per-receipt
            allocation accurate.  This is NOT a forfeit from the
            customer's perspective — the customer never had the
            match money to lose; FAM just contributes less.  The
            warning zone does NOT fire for this case.  No alarm,
            no language about "FAM match forfeit" anywhere in
            the UI or reports.

          * **Phase B — token-value forfeit.** When the
            customer's denomination unit ALSO exceeds the receipt
            even after match is fully reduced (e.g. $10 Food RX
            token to a $6.52 receipt), the excess portion of the
            token's face value doesn't reach the vendor.  The
            customer physically handed over more scrip than the
            transaction needed; that excess is unaccounted in
            program-policy terms.  THIS is the only forfeit the
            volunteer needs to be aware of — they may want to
            offer the customer a smaller denomination, a
            different payment method, or confirm the customer
            accepts the loss.

        Args:
            customer_forfeit_cents: Phase B forfeit amount (token
                face value the customer handed over but did not
                translate to vendor reimbursement).  Caller is
                responsible for passing only positive values —
                this method does not fire when the value is 0.
        """
        # Single outer border on a uniquely-named frame; the inner
        # QLabels explicitly carry ``border: none`` so the parent
        # stylesheet can't cascade an unwanted nested box around
        # the title or body text (the 2026-04-29 UX feedback found
        # an apparent nested border in the warning section — this
        # rewrite makes it structurally impossible).
        warn = QFrame()
        warn.setObjectName("denomOverageWarning")
        warn.setStyleSheet(f"""
            QFrame#denomOverageWarning {{
                background-color: {WARNING_BG};
                border: 1.5px solid {WARNING_COLOR};
                border-radius: 8px;
            }}
        """)
        wlay = QVBoxLayout(warn)
        wlay.setContentsMargins(14, 12, 14, 12)
        wlay.setSpacing(4)

        title = QLabel(
            f"⚠  CUSTOMER FORFEIT — "
            f"{format_dollars(customer_forfeit_cents)} of customer's "
            f"denomination not used"
        )
        title.setStyleSheet(f"""
            QLabel {{
                font-size: 14px;
                font-weight: bold;
                color: {WARNING_COLOR};
                letter-spacing: 0.5px;
                background: transparent;
                border: none;
                padding: 0;
            }}
        """)
        wlay.addWidget(title)

        body = QLabel(
            f"The customer is handing over "
            f"<b>{format_dollars(customer_forfeit_cents)}</b> more "
            f"in denominated payment than this receipt covers.  "
            f"The vendor will be reimbursed the full receipt total, "
            f"but the excess token face value is not credited "
            f"anywhere — it's a real loss for the customer.<br><br>"
            f"<b>Recommended:</b> if the customer has smaller "
            f"denominations or other payment methods (Cash, SNAP), "
            f"cancel and re-enter — they'll keep the full token "
            f"value.  Otherwise the customer is accepting this "
            f"loss to use the token they have."
        )
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setStyleSheet(f"""
            QLabel {{
                font-size: 13px;
                color: {TEXT_COLOR};
                background: transparent;
                border: none;
                padding: 0;
            }}
        """)
        wlay.addWidget(body)
        return warn

    def _build_rewards_zone(self, reward_lines: list) -> QFrame:
        """Customer-facing rewards (v1.9.10+).

        Visually segregated from the action zone — coordinator
        feedback was unambiguous: rewards must NOT look like another
        thing-to-collect-from-the-customer.  Distinct purple/violet
        accent + explicit "GIVE TO CUSTOMER" verb so the cashier
        always reads it as a separate motion (hand out scrip), not
        a payment item.

        Disclaimer line at the bottom: "Rewards do not affect
        vendor reimbursement or FAM match" — pinned by the spec
        so a future reader of the dialog can't confuse the rewards
        section with the financial flow.
        """
        zone = QFrame()
        zone.setObjectName("rewardsZone")
        # Purple-violet accent, distinct from the green action zone
        # and the amber denomination-overage warning.  Not red —
        # this isn't a warning; not green — this isn't an action.
        zone.setStyleSheet(f"""
            QFrame#rewardsZone {{
                background-color: #F3E5F5;
                border: 1.5px solid #7B1FA2;
                border-radius: 8px;
            }}
        """)
        lay = QVBoxLayout(zone)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        header = QLabel("🎁  GIVE TO CUSTOMER (rewards)")
        header.setStyleSheet(f"""
            QLabel {{
                font-size: 14px;
                font-weight: bold;
                color: #6A1B9A;
                letter-spacing: 0.8px;
                background: transparent;
                border: none;
                padding: 0;
            }}
        """)
        lay.addWidget(header)

        for rl in reward_lines:
            denom_dollars = rl.reward_unit_cents / 100.0
            row = QLabel(
                f"   • {rl.n_units} × ${denom_dollars:.2f} "
                f"{rl.reward_method_name}  "
                f"(earned from ${rl.source_total_cents/100:.2f} "
                f"of {rl.source_method_name})"
            )
            row.setStyleSheet(f"""
                QLabel {{
                    font-size: 13px;
                    color: {TEXT_COLOR};
                    background: transparent;
                    border: none;
                    padding: 0;
                }}
            """)
            lay.addWidget(row)

        disclaimer = QLabel(
            "Marketing/loyalty add-on — NOT vendor reimbursement, "
            "NOT FAM match, NOT part of this payment."
        )
        disclaimer.setStyleSheet(f"""
            QLabel {{
                font-size: 11px;
                color: {SUBTITLE_GRAY};
                font-style: italic;
                background: transparent;
                border: none;
                padding: 4px 0 0 0;
            }}
        """)
        disclaimer.setWordWrap(True)
        lay.addWidget(disclaimer)
        return zone

    def _build_info_footer(self, receipt_total: int,
                           match_total: int,
                           customer_forfeit_total: int = 0) -> QLabel:
        # v2.0.7-final (Option B, schema v36): include Customer
        # Forfeit in the footer when Phase B fired.  Mirrors the
        # PaymentScreen Customer Forfeit summary card so the
        # volunteer sees the same number on the confirm dialog
        # they saw on the order screen.  When forfeit is $0
        # (Phase A only or no overage), the segment is omitted —
        # don't clutter the typical-case footer with an always-
        # zero field.
        forfeit_segment = ""
        if customer_forfeit_total > 0:
            forfeit_segment = (
                f"  ·  "
                f"<span style='color:{WARNING_COLOR}'>"
                f"Customer forfeit: "
                f"<b>{format_dollars(customer_forfeit_total)}</b>"
                f"</span>"
            )
        info = QLabel(
            f"<span style='color:{SUBTITLE_GRAY}'>"
            f"Vendor reimbursement: <b>{format_dollars(receipt_total)}</b>  ·  "
            f"FAM match: <b>{format_dollars(match_total)}</b>"
            f"</span>"
            f"{forfeit_segment}"
        )
        # Bumped from 11px → 13px for accessibility.  Even
        # informative-only text needs to clear the 12px floor for
        # elderly volunteers — bookkeeping totals matter for
        # post-shift reconciliation conversations.
        info.setStyleSheet(f"""
            QLabel {{
                font-size: 13px;
                background: transparent;
                padding: 4px 0;
            }}
        """)
        return info

    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(38)
        cancel_btn.setMinimumWidth(110)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {WHITE};
                color: {TEXT_COLOR};
                border: 1.5px solid {SUBTITLE_GRAY};
                border-radius: 6px;
                padding: 8px 18px;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {BACKGROUND}; }}
        """)
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(cancel_btn)

        self._confirm_btn = QPushButton("✓  Confirm — Payment Collected")
        self._confirm_btn.setFixedHeight(38)
        self._confirm_btn.setMinimumWidth(220)
        self._confirm_btn.setDefault(True)
        self._confirm_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {PRIMARY_GREEN};
                color: {WHITE};
                border: 1.5px solid {PRIMARY_GREEN};
                border-radius: 6px;
                padding: 8px 22px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {ACCENT_GREEN}; }}
            QPushButton:disabled {{
                background-color: #BDBDBD;
                border-color: #BDBDBD;
                color: {WHITE};
            }}
        """)
        self._confirm_btn.clicked.connect(self._on_confirm)
        row.addWidget(self._confirm_btn)
        return row

    # ── Behaviour ────────────────────────────────────────────────

    def _update_confirm_enabled(self):
        """The Confirm button stays disabled until every required
        (external-device) checkbox is ticked.  Called from each
        checkbox's ``toggled`` signal AND once at the end of
        ``__init__`` so the initial state is correct."""
        all_checked = all(
            cb.isChecked() for cb in self._required_checkboxes
        )
        self._confirm_btn.setEnabled(all_checked)

    def _on_confirm(self):
        # Stop the marching-ants animation as a final visual cue
        # that the volunteer has completed the action.  The dialog
        # accepts immediately afterwards so this is mostly cosmetic
        # — but if the dialog is left open via parent code, the
        # static border distinguishes "still waiting" from "done".
        for f in self._marching_ants_frames:
            f.stopAnimation()
        self.accept()
