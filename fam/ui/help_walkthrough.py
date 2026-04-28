"""Animated 5-stage walkthrough — the splash page of the Help screen.

Walks a brand-new volunteer through the entire FAM market-day cycle,
end to end, in a friendly conversational tone with light animation.

Stages:
    1. Customer shopping at vendors — receipts collected, no payment yet
    2. Customer arrives at the FAM table — central hand-off point
    3. The volunteer's three steps — receipts, payments, food delivery
    4. End-of-market FMNP check logging — record-keeping only
    5. Cloud sync + close-out

Visual design (refined v1.9.8+):
    - Soft FAM-green tinted banner with a quiet ghost "Skip Tour" link
      and a subtle "≈ 90 sec · 5 stages" subtitle
    - Numbered progress rail (5 circular nodes connected by a fill
      line) replaces the old [1] — [2] — [3] ASCII row
    - Unified scene + narrative card with one soft shadow, an inner
      hairline divider, and a gold-accented "key takeaway" strip above
      each paragraph for fast scanning
    - Hierarchical controls — primary FAM-green Next on the right,
      restrained ghost Prev/Pause/Restart elsewhere
    - On the final stage, Next becomes "Tour complete — Browse →" and
      emits ``skip_requested`` so the user lands in the article library

Implementation:
    - Each stage is a WalkthroughScene subclass with its own play()
      animation built from QPropertyAnimation + opacity effects
    - Each scene's animation loops in place; the volunteer chooses
      when to move on by clicking Next (which softly pulses gold after
      the first iteration finishes)
    - Manual prev / next / pause / restart / skip controls
    - All animations native Qt — no QWebEngine, no SVG dep, no extra
      packages
"""

from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QObject, QParallelAnimationGroup,
    QPoint, QPointF, QPropertyAnimation, QRectF,
    QSequentialAnimationGroup, QTimer, Qt, Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QHBoxLayout,
    QLabel, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from fam.ui.help_icons import (
    ArrowIcon, BoxIcon, CardIcon, CashIcon, CheckIcon, CheckmarkIcon,
    ClipboardIcon, CloudIcon, EnvelopeIcon, FileIcon, LaptopIcon,
    ManagerIcon, PersonIcon, ReceiptIcon, RunnerIcon, SceneCard,
    StampIcon, StepBadge, TableIcon, VendorStallIcon,
)
from fam.ui.styles import (
    ACCENT_GREEN, BACKGROUND, HARVEST_GOLD, LIGHT_GRAY, MEDIUM_GRAY,
    PRIMARY_GREEN, SUBTITLE_GRAY, TEXT_COLOR, WHITE,
)


# Pause between animation loop iterations (milliseconds).  After the
# scene's animation finishes, hold the final state briefly so the
# volunteer can absorb it, then replay from the beginning.
_LOOP_REST_MS = 1_500


# ── Visual design tokens (refined v1.9.8+) ─────────────────────
# Banner — light green wash with a hairline bottom border.
_BANNER_TINT = '#EAF1EC'
_BANNER_BORDER = '#D7DED9'
# Card surfaces — soft shadow + 1px border for the unified frame.
_CARD_BORDER = '#E5E5E5'
_DIVIDER = '#ECECEC'
# Progress rail — node sizing + line colors.
_NODE_SIZE = 30
_NODE_BOX = _NODE_SIZE + 12          # extra room for the active-state halo
_RAIL_GAP = 86                       # px between node centers
_RAIL_LINE_PENDING = '#E2E2E2'
_RAIL_LINE_FILLED = ACCENT_GREEN
_NODE_PENDING_RING = '#CFCFCF'
# Key-takeaway strip — warm gold accent on a faint warm-tint background.
_TAKEAWAY_BG = '#FFF8EE'
_TAKEAWAY_ACCENT = HARVEST_GOLD
# Next-button "soft pulse" — warm tint on a thicker gold border, instead
# of a full gold fill that competed visually with the rest of the row.
_NEXT_FLASH_BG = '#FFF1DD'
_NEXT_FLASH_BORDER = HARVEST_GOLD


# ── Helper: caption label ──────────────────────────────────────

def _caption_label(text: str, parent=None) -> QLabel:
    """Small italic caption used below scene icons."""
    lbl = QLabel(text, parent)
    lbl.setStyleSheet(
        f'background:transparent;color:{SUBTITLE_GRAY};'
        f'font-size:11px;font-style:italic;')
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setWordWrap(True)
    return lbl


def _make_opacity_effect(target: QWidget, initial: float = 0.0
                         ) -> QGraphicsOpacityEffect:
    """Attach an opacity effect we can animate, starting hidden by default."""
    eff = QGraphicsOpacityEffect(target)
    eff.setOpacity(initial)
    target.setGraphicsEffect(eff)
    return eff


def _fade_in(target: QWidget, duration_ms: int = 600,
             delay_ms: int = 0) -> QPropertyAnimation:
    """Build a fade-in QPropertyAnimation on the target's opacity effect."""
    eff = target.graphicsEffect()
    if eff is None:
        eff = _make_opacity_effect(target, initial=0.0)
    anim = QPropertyAnimation(eff, b'opacity')
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(duration_ms)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    if delay_ms:
        # Wrap in a sequential group with a pause for delay
        group = QSequentialAnimationGroup()
        pause = QPropertyAnimation(eff, b'opacity')
        pause.setStartValue(0.0)
        pause.setEndValue(0.0)
        pause.setDuration(delay_ms)
        group.addAnimation(pause)
        group.addAnimation(anim)
        return group  # type: ignore[return-value]
    return anim


def _move_to(target: QWidget, end_pos: QPoint,
             duration_ms: int = 800) -> QPropertyAnimation:
    """Slide the target to end_pos using QPropertyAnimation on pos()."""
    anim = QPropertyAnimation(target, b'pos')
    anim.setStartValue(target.pos())
    anim.setEndValue(end_pos)
    anim.setDuration(duration_ms)
    anim.setEasingCurve(QEasingCurve.InOutCubic)
    return anim


# ── Stage scene base class ──────────────────────────────────────

class WalkthroughScene(QFrame):
    """Base class for a single walkthrough stage's visual scene.

    Each subclass arranges its own widgets and orchestrates a single
    animation pass via ``_build_animation()``.  The base class wraps
    that pass in a **looping** controller: when one iteration finishes,
    the scene rests briefly (``_LOOP_REST_MS``) and then replays from
    the start.  Looping continues until ``pause()`` or ``reset()``.

    Each completed iteration emits ``iteration_completed`` so the parent
    walkthrough widget can react (e.g. start flashing the Next button
    after the user has seen the full animation once).
    """

    SCENE_HEIGHT = 220

    iteration_completed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.SCENE_HEIGHT)
        self.setMaximumHeight(self.SCENE_HEIGHT)
        self.setStyleSheet(
            f"QFrame {{ background:{BACKGROUND};border:1px solid {LIGHT_GRAY};"
            f"border-radius:10px; }}")
        self._anim: Optional[QObject] = None
        self._loop_enabled = True
        # Timer that fires the next loop iteration after a brief rest.
        self._loop_rest_timer = QTimer(self)
        self._loop_rest_timer.setSingleShot(True)
        self._loop_rest_timer.timeout.connect(self._on_loop_restart)
        self._build()
        self._reset_state()

    # ── Lifecycle hooks subclasses override ────────────────────

    def _build(self):
        """Compose the static widgets for this scene."""
        raise NotImplementedError

    def _reset_state(self):
        """Reset visual state (opacities, positions) to scene start."""
        raise NotImplementedError

    def _build_animation(self) -> QObject:
        """Return a QSequentialAnimationGroup describing one pass of
        the scene's animation.  The base class handles looping."""
        raise NotImplementedError

    # ── Public API ─────────────────────────────────────────────

    def play(self):
        """Start (or restart) one full pass of the scene animation.

        After the pass completes, ``_on_anim_finished`` schedules the
        next iteration via the rest timer.  Call ``pause()`` or
        ``reset()`` to stop looping."""
        if self._anim is not None:
            try:
                self._anim.stop()
            except RuntimeError:
                pass
        self._loop_rest_timer.stop()
        self._reset_state()
        self._anim = self._build_animation()
        self._anim.finished.connect(self._on_anim_finished)
        self._anim.start()

    def pause(self):
        """Suspend the running animation AND cancel any pending loop
        replay.  Call ``resume()`` to continue."""
        if self._anim is not None:
            try:
                self._anim.pause()
            except RuntimeError:
                pass
        self._loop_rest_timer.stop()

    def resume(self):
        """Resume after ``pause()``.  If the animation was mid-flight
        when paused, it continues; if it had already finished and was
        in the rest period, the next loop iteration starts immediately."""
        if self._anim is not None:
            try:
                state = self._anim.state()
            except RuntimeError:
                state = QAbstractAnimation.Stopped
            if state == QAbstractAnimation.Paused:
                try:
                    self._anim.resume()
                except RuntimeError:
                    pass
                return
            if state == QAbstractAnimation.Stopped:
                # Animation already completed; restart fresh.
                self.play()
                return
        # No prior animation — kick one off.
        self.play()

    def reset(self):
        """Stop everything and return to the visual start state."""
        if self._anim is not None:
            try:
                self._anim.stop()
            except RuntimeError:
                pass
            self._anim = None
        self._loop_rest_timer.stop()
        self._reset_state()

    # ── Internal: loop bookkeeping ─────────────────────────────

    def _on_anim_finished(self):
        """One pass of the animation just finished.  Notify listeners
        and schedule the next iteration."""
        self.iteration_completed.emit()
        if self._loop_enabled:
            self._loop_rest_timer.start(_LOOP_REST_MS)

    def _on_loop_restart(self):
        if self._loop_enabled:
            self.play()


# ── Stage 1: Customer shopping at vendors ───────────────────────

class Stage1Scene(WalkthroughScene):
    """Customer card walks past three vendor stall cards, picks up a
    receipt at each.  Ends with three receipts visible in front of the
    vendor row."""

    def _build(self):
        # Three vendor stall cards on the right
        self._vendor_a = SceneCard(
            VendorStallIcon(size=46), caption='Vendor A',
            card_width=100, card_height=110, parent=self)
        self._vendor_a.move(210, 30)

        self._vendor_b = SceneCard(
            VendorStallIcon(size=46), caption='Vendor B',
            card_width=100, card_height=110, parent=self)
        self._vendor_b.move(330, 30)

        self._vendor_c = SceneCard(
            VendorStallIcon(size=46), caption='Vendor C',
            card_width=100, card_height=110, parent=self)
        self._vendor_c.move(450, 30)

        # The customer (animated card)
        self._customer = SceneCard(
            PersonIcon(size=46, primary=PRIMARY_GREEN),
            caption='Customer',
            card_width=100, card_height=110, parent=self)
        self._customer.move(50, 30)

        # Three receipts that pop in below the vendors as the customer
        # collects them
        self._receipt_1 = ReceiptIcon(size=44, parent=self)
        self._receipt_1.move(238, 150)
        self._receipt_2 = ReceiptIcon(size=44, parent=self)
        self._receipt_2.move(358, 150)
        self._receipt_3 = ReceiptIcon(size=44, parent=self)
        self._receipt_3.move(478, 150)
        for r in (self._receipt_1, self._receipt_2, self._receipt_3):
            _make_opacity_effect(r, initial=0.0)

        self._tagline = _caption_label(
            'No payment yet — vendors hold each order and hand out receipts.',
            parent=self)
        self._tagline.setGeometry(20, 195, 660, 20)

    def _reset_state(self):
        self._customer.move(50, 30)
        for r in (self._receipt_1, self._receipt_2, self._receipt_3):
            eff = r.graphicsEffect()
            if eff is not None:
                eff.setOpacity(0.0)

    def _build_animation(self) -> QObject:
        group = QSequentialAnimationGroup(self)
        # Customer walks to vendor A, receipt fades in
        group.addAnimation(_move_to(self._customer, QPoint(180, 30), 1200))
        group.addAnimation(_fade_in(self._receipt_1, 500))
        # Walks to vendor B
        group.addAnimation(_move_to(self._customer, QPoint(300, 30), 1200))
        group.addAnimation(_fade_in(self._receipt_2, 500))
        # Walks to vendor C
        group.addAnimation(_move_to(self._customer, QPoint(420, 30), 1200))
        group.addAnimation(_fade_in(self._receipt_3, 500))
        return group


# ── Stage 2: Customer arrives at FAM table ──────────────────────

class Stage2Scene(WalkthroughScene):
    """Customer card slides toward the FAM table card.  Big arrow,
    big landing — one central place for every FAM transaction."""

    def _build(self):
        # Customer card with three receipts shown stacked alongside
        self._customer = SceneCard(
            PersonIcon(size=46, primary=PRIMARY_GREEN),
            caption='Customer',
            sub_caption='3 receipts',
            card_width=110, card_height=130, parent=self)
        self._customer.move(40, 35)

        # Animated arrow in the middle
        self._arrow = ArrowIcon(direction='right', size=72, parent=self)
        self._arrow.move(290, 65)
        _make_opacity_effect(self._arrow, initial=0.0)

        # FAM table on the right (final destination)
        self._fam_table = SceneCard(
            TableIcon(size=46),
            caption='FAM Table',
            sub_caption='central hub',
            card_width=110, card_height=130, parent=self)
        self._fam_table.move(540, 35)

        self._tagline = _caption_label(
            'Every FAM customer comes to one place — your table is the '
            'central hub for the entire market.',
            parent=self)
        self._tagline.setGeometry(20, 185, 660, 20)

    def _reset_state(self):
        self._customer.move(40, 35)
        eff = self._arrow.graphicsEffect()
        if eff is not None:
            eff.setOpacity(0.0)

    def _build_animation(self) -> QObject:
        group = QSequentialAnimationGroup(self)
        group.addAnimation(_fade_in(self._arrow, 600))
        # Slide the customer card toward the table
        group.addAnimation(_move_to(self._customer, QPoint(390, 35), 1800))
        return group


# ── Stage 3: The volunteer's three steps ────────────────────────

class Stage3Scene(WalkthroughScene):
    """Three-beat scene showing the volunteer's day.

    Beat 1: receipts → laptop (data entry)
    Beat 2: payment methods appear (card, check, cash)
    Beat 3: two parallel paths — food runner OR stamp
    """

    SCENE_HEIGHT = 240

    def _build(self):
        # ── Beat 1: receipts → laptop ──────────────────────────
        self._step1_badge = StepBadge(1, parent=self)
        self._step1_badge.move(20, 14)
        self._step1_label = _caption_label('Enter receipts', parent=self)
        self._step1_label.setStyleSheet(
            f'background:transparent;color:{TEXT_COLOR};'
            f'font-size:11px;font-weight:bold;')
        self._step1_label.setGeometry(46, 12, 120, 18)
        self._step1_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self._receipts_card = SceneCard(
            ReceiptIcon(size=40), caption='Receipts',
            card_width=80, card_height=80, parent=self)
        self._receipts_card.move(40, 38)

        self._arrow_1 = ArrowIcon(direction='right', size=40, parent=self)
        self._arrow_1.move(122, 60)
        _make_opacity_effect(self._arrow_1, initial=0.0)

        self._laptop_card = SceneCard(
            LaptopIcon(size=40), caption='FAM App',
            card_width=80, card_height=80, parent=self)
        self._laptop_card.move(170, 38)

        # ── Beat 2: payment methods (cluster on the right) ─────
        self._step2_badge = StepBadge(2, parent=self)
        self._step2_badge.move(290, 14)
        self._step2_label = _caption_label('Collect payment', parent=self)
        self._step2_label.setStyleSheet(
            f'background:transparent;color:{TEXT_COLOR};'
            f'font-size:11px;font-weight:bold;')
        self._step2_label.setGeometry(316, 12, 200, 18)
        self._step2_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self._snap_card = SceneCard(
            CardIcon(size=40), caption='SNAP',
            sub_caption='via EBT terminal',
            card_width=98, card_height=84, parent=self)
        self._snap_card.move(290, 38)

        self._fmnp_card = SceneCard(
            CheckIcon(size=40), caption='FMNP',
            card_width=84, card_height=84, parent=self)
        self._fmnp_card.move(396, 38)

        self._cash_card = SceneCard(
            CashIcon(size=40), caption='Cash',
            card_width=84, card_height=84, parent=self)
        self._cash_card.move(488, 38)

        for w in (self._snap_card, self._fmnp_card, self._cash_card):
            _make_opacity_effect(w, initial=0.0)

        # ── Beat 3: two paths ──────────────────────────────────
        self._step3_badge = StepBadge(3, parent=self)
        self._step3_badge.move(20, 132)
        self._step3_label = _caption_label('Get them their food', parent=self)
        self._step3_label.setStyleSheet(
            f'background:transparent;color:{TEXT_COLOR};'
            f'font-size:11px;font-weight:bold;')
        self._step3_label.setGeometry(46, 130, 200, 18)
        self._step3_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        path_y = 156

        # Path A: Food runner → box
        self._runner_card = SceneCard(
            RunnerIcon(size=40), caption='Food runner',
            card_width=98, card_height=80, parent=self)
        self._runner_card.move(40, path_y)

        self._runner_arrow = ArrowIcon(direction='right', size=32, parent=self)
        self._runner_arrow.move(140, path_y + 22)

        self._box_card = SceneCard(
            BoxIcon(size=40), caption='Customer',
            card_width=84, card_height=80, parent=self)
        self._box_card.move(178, path_y)

        for w in (self._runner_card, self._runner_arrow, self._box_card):
            _make_opacity_effect(w, initial=0.0)

        # OR divider
        self._or = QLabel('or', parent=self)
        self._or.setStyleSheet(
            f'background:transparent;color:{MEDIUM_GRAY};'
            f'font-weight:bold;font-size:11px;font-style:italic;')
        self._or.setGeometry(280, path_y + 30, 30, 16)
        self._or.setAlignment(Qt.AlignCenter)
        _make_opacity_effect(self._or, initial=0.0)

        # Path B: Stamp → stamped receipts → customer collects
        self._stamp_card = SceneCard(
            StampIcon(size=40), caption='Stamp receipts',
            card_width=98, card_height=80, parent=self)
        self._stamp_card.move(320, path_y)

        self._stamp_arrow = ArrowIcon(direction='right', size=32, parent=self)
        self._stamp_arrow.move(420, path_y + 22)

        self._stamped_card = SceneCard(
            CheckmarkIcon(size=40, primary=ACCENT_GREEN),
            caption='Paid mark',
            card_width=84, card_height=80, parent=self)
        self._stamped_card.move(458, path_y)

        self._stamp_arrow2 = ArrowIcon(direction='right', size=32, parent=self)
        self._stamp_arrow2.move(544, path_y + 22)

        self._customer_collects = SceneCard(
            PersonIcon(size=40, primary=PRIMARY_GREEN),
            caption='Customer collects',
            card_width=98, card_height=80, parent=self)
        self._customer_collects.move(578, path_y)

        for w in (self._stamp_card, self._stamp_arrow,
                   self._stamped_card, self._stamp_arrow2,
                   self._customer_collects):
            _make_opacity_effect(w, initial=0.0)

    def _reset_state(self):
        for w in (
            self._arrow_1,
            self._snap_card, self._fmnp_card, self._cash_card,
            self._runner_card, self._runner_arrow, self._box_card, self._or,
            self._stamp_card, self._stamp_arrow,
            self._stamped_card, self._stamp_arrow2, self._customer_collects,
        ):
            eff = w.graphicsEffect()
            if eff is not None:
                eff.setOpacity(0.0)

    def _build_animation(self) -> QObject:
        group = QSequentialAnimationGroup(self)

        # Beat 1: receipts → laptop arrow appears
        group.addAnimation(_fade_in(self._arrow_1, 500))

        # Beat 2: payment cards appear one by one
        for card in (self._snap_card, self._fmnp_card, self._cash_card):
            group.addAnimation(_fade_in(card, 400))

        # Beat 3a: runner path
        runner_par = QParallelAnimationGroup(self)
        runner_par.addAnimation(_fade_in(self._runner_card, 500))
        runner_par.addAnimation(_fade_in(self._runner_arrow, 500))
        runner_par.addAnimation(_fade_in(self._box_card, 500))
        group.addAnimation(runner_par)

        # OR divider
        group.addAnimation(_fade_in(self._or, 300))

        # Beat 3b: stamp path
        stamp_par = QParallelAnimationGroup(self)
        for w in (self._stamp_card, self._stamp_arrow, self._stamped_card,
                   self._stamp_arrow2, self._customer_collects):
            stamp_par.addAnimation(_fade_in(w, 500))
        group.addAnimation(stamp_par)

        return group


# ── Stage 4: End-of-market FMNP returns ─────────────────────────

class Stage4Scene(WalkthroughScene):
    """Vendor hands FMNP checks to the manager, who logs them.  The
    important callout: log only, FAM does NOT add a match, vendor
    cashes the check themselves."""

    def _build(self):
        # Vendor card on the left
        self._vendor_card = SceneCard(
            VendorStallIcon(size=40), caption='Vendor',
            card_width=90, card_height=90, parent=self)
        self._vendor_card.move(40, 25)

        # Check stack appearing between vendor and manager
        self._checks_card = SceneCard(
            CheckIcon(size=40), caption='FMNP checks',
            card_width=100, card_height=90, parent=self)
        self._checks_card.move(155, 25)
        _make_opacity_effect(self._checks_card, initial=0.0)

        self._arrow_1 = ArrowIcon(direction='right', size=36, parent=self)
        self._arrow_1.move(265, 50)
        _make_opacity_effect(self._arrow_1, initial=0.0)

        # Manager card
        self._manager_card = SceneCard(
            ManagerIcon(size=40),
            caption='Market Manager',
            sub_caption='usually not volunteers',
            card_width=120, card_height=110, parent=self)
        self._manager_card.move(310, 15)

        self._arrow_2 = ArrowIcon(direction='right', size=36, parent=self)
        self._arrow_2.move(440, 50)
        _make_opacity_effect(self._arrow_2, initial=0.0)

        # FMNP entry page (clipboard)
        self._clipboard_card = SceneCard(
            ClipboardIcon(size=40), caption='FMNP Entry page',
            sub_caption='log → reimburse',
            card_width=120, card_height=110, parent=self)
        self._clipboard_card.move(485, 15)
        _make_opacity_effect(self._clipboard_card, initial=0.0)

        # Important callout — the key clarification
        self._callout = QLabel(
            "  ✱  <b>FAM reimburses the face value of FMNP checks</b> "
            "(no match percent added). The vendor already applied their "
            "match at the booth and cashes the check separately — so "
            "they're made whole on the matched value they gave the customer.",
            parent=self)
        self._callout.setStyleSheet(
            f"background:{WHITE};color:{TEXT_COLOR};"
            f"border:1px solid {LIGHT_GRAY};"
            f"border-left:4px solid {HARVEST_GOLD};"
            f"border-radius:6px;"
            f"padding:10px 12px;font-size:12px;")
        self._callout.setWordWrap(True)
        self._callout.setGeometry(20, 145, 660, 65)
        _make_opacity_effect(self._callout, initial=0.0)

    def _reset_state(self):
        for w in (self._checks_card, self._arrow_1, self._arrow_2,
                   self._clipboard_card, self._callout):
            eff = w.graphicsEffect()
            if eff is not None:
                eff.setOpacity(0.0)

    def _build_animation(self) -> QObject:
        group = QSequentialAnimationGroup(self)
        group.addAnimation(_fade_in(self._checks_card, 500))
        group.addAnimation(_fade_in(self._arrow_1, 400))
        group.addAnimation(_fade_in(self._arrow_2, 400))
        group.addAnimation(_fade_in(self._clipboard_card, 600))
        group.addAnimation(_fade_in(self._callout, 800))
        return group


# ── Stage 5: Cloud sync + close-out ─────────────────────────────

class Stage5Scene(WalkthroughScene):
    """Laptop syncs to Google Sheets / Drive in the cloud.  Backup path
    branches to CSV + email when Wi-Fi is unavailable."""

    def _build(self):
        # Laptop (centered lower portion of the scene)
        self._laptop_card = SceneCard(
            LaptopIcon(size=42), caption='Your laptop',
            sub_caption='data is safe locally',
            card_width=130, card_height=110, parent=self)
        self._laptop_card.move(60, 80)

        # Up arrow + cloud (sync path)
        self._sync_arrow = ArrowIcon(direction='up', size=44,
                                      primary=ACCENT_GREEN, parent=self)
        self._sync_arrow.move(225, 50)
        _make_opacity_effect(self._sync_arrow, initial=0.0)

        self._cloud_card = SceneCard(
            CloudIcon(size=42), caption='Google Sheets + Drive',
            card_width=160, card_height=110, parent=self)
        self._cloud_card.move(195, 5)
        _make_opacity_effect(self._cloud_card, initial=0.0)

        # OR divider
        self._or = QLabel('or, if Wi-Fi is down:', parent=self)
        self._or.setStyleSheet(
            f'background:transparent;color:{MEDIUM_GRAY};'
            f'font-style:italic;font-size:11px;')
        self._or.setGeometry(380, 90, 160, 16)
        self._or.setAlignment(Qt.AlignCenter)
        _make_opacity_effect(self._or, initial=0.0)

        # Backup path: CSV → email
        self._csv_card = SceneCard(
            FileIcon(size=40), caption='Export CSV',
            card_width=100, card_height=90, parent=self)
        self._csv_card.move(385, 110)

        self._backup_arrow = ArrowIcon(direction='right', size=32, parent=self)
        self._backup_arrow.move(485, 132)

        self._email_card = SceneCard(
            EnvelopeIcon(size=40), caption='Email when online',
            card_width=120, card_height=90, parent=self)
        self._email_card.move(525, 110)

        for w in (self._csv_card, self._backup_arrow, self._email_card):
            _make_opacity_effect(w, initial=0.0)

        self._tagline = _caption_label(
            "That's the cycle. Thanks for being here.",
            parent=self)
        self._tagline.setStyleSheet(
            f'background:transparent;color:{ACCENT_GREEN};'
            f'font-weight:bold;font-size:12px;')
        self._tagline.setGeometry(20, 215, 660, 20)
        _make_opacity_effect(self._tagline, initial=0.0)

    def _reset_state(self):
        for w in (self._sync_arrow, self._cloud_card, self._or,
                   self._csv_card, self._backup_arrow, self._email_card,
                   self._tagline):
            eff = w.graphicsEffect()
            if eff is not None:
                eff.setOpacity(0.0)

    def _build_animation(self) -> QObject:
        group = QSequentialAnimationGroup(self)
        # Sync path
        group.addAnimation(_fade_in(self._sync_arrow, 500))
        group.addAnimation(_fade_in(self._cloud_card, 700))
        # Backup path
        group.addAnimation(_fade_in(self._or, 400))
        backup_par = QParallelAnimationGroup(self)
        for w in (self._csv_card, self._backup_arrow, self._email_card):
            backup_par.addAnimation(_fade_in(w, 500))
        group.addAnimation(backup_par)
        group.addAnimation(_fade_in(self._tagline, 500))
        return group


# ── Stage definitions (data) ────────────────────────────────────

@dataclass(frozen=True)
class StageDef:
    number: int
    title: str
    role_label: str
    narrative: str  # HTML allowed (rendered into a QLabel with rich text)
    scene_factory: Callable[[], WalkthroughScene]
    # One-sentence distillation of the stage shown in a gold-accented
    # callout strip above the narrative.  Should be scannable in 2-3
    # seconds — a volunteer who only reads the takeaways still walks
    # away with the right mental model.
    key_takeaway: str = ''


STAGES: tuple[StageDef, ...] = (
    StageDef(
        number=1,
        title='Shopping at the Market',
        role_label='The Customer',
        key_takeaway=(
            "Vendors hold every order — no money changes hands until the "
            "customer brings their stack of receipts to your table."
        ),
        narrative=(
            "It actually starts before you even see them. A FAM customer "
            "shops at participating vendors, identifies themselves as a "
            "FAM patron at each booth, and the vendor <b>holds the order</b> "
            "— no payment yet. The vendor hands them a paper receipt with "
            "the total. Repeat for every vendor on their list. By the "
            "time they're heading to your table, they've got a stack of "
            "receipts in hand."
        ),
        scene_factory=Stage1Scene,
    ),
    StageDef(
        number=2,
        title='Arriving at the FAM Table',
        role_label='The Hand-Off',
        key_takeaway=(
            "Your table is the central hub — every FAM customer ends up "
            "here for a single, tracked transaction."
        ),
        narrative=(
            "Once they're done shopping, every FAM customer comes to "
            "<b>one place</b> — your table. You're the central point that "
            "handles all FAM transactions for the entire market. The "
            "customer hands you their receipts, and that's where your "
            "three-step process begins."
        ),
        scene_factory=Stage2Scene,
    ),
    StageDef(
        number=3,
        title='Your Three Steps',
        role_label='FAM Volunteer · Your Role',
        key_takeaway=(
            "Three steps, in order — enter receipts, collect payments, "
            "then get the customer their food."
        ),
        narrative=(
            "Three things, in order. <b>First</b>, enter every receipt "
            "into the app — vendor and total. <b>Second</b>, collect "
            "their payment methods. SNAP gets charged on the secondary "
            "EBT terminal next to you (that's a separate device — the "
            "app records the amount after the swipe). FMNP checks, cash, "
            "anything else gets entered too. <b>Third</b>, get them their "
            "food: a food runner takes the receipts back to each vendor "
            "and brings the orders to the customer so they don't wait in "
            "line twice. If runners aren't available, you stamp each "
            "receipt as paid and the customer collects their own orders. "
            "Then on to the next customer. This is the bulk of your day "
            "— rinse and repeat."
        ),
        scene_factory=Stage3Scene,
    ),
    StageDef(
        number=4,
        title='End-of-Day FMNP Logging',
        role_label='Market Manager · Usually Not Volunteers',
        key_takeaway=(
            "FAM reimburses the face value of FMNP checks at end-of-month "
            "— no extra match added. The vendor already applied 2× at the "
            "booth and cashes the check separately."
        ),
        narrative=(
            "After the market closes, the manager (this part isn't "
            "usually a volunteer's job) collects any FMNP checks the "
            "vendors took that day and logs them on the FMNP Entry page. "
            "<b>Here's the math:</b> a participating-FAM vendor treats "
            "an FMNP check at double its face value at the booth — a $5 "
            "check counts as $10 of food. The vendor cashes the check "
            "directly with the program for the original $5, and FAM "
            "<b>reimburses the same $5 face value</b> at end-of-month so "
            "the vendor ends up whole on the match they applied. We do "
            "<b>not</b> add a match percent on top — the vendor already "
            "did. The check amounts show up under 'FMNP (External)' in "
            "the Vendor Reimbursement report and are included in the "
            "total reimbursement check we cut to the vendor."
        ),
        scene_factory=Stage4Scene,
    ),
    StageDef(
        number=5,
        title='Wrapping Up the Market',
        role_label='Closing the Loop',
        key_takeaway=(
            "Sync once the market closes. If Wi-Fi is down, export CSV "
            "and email later — your data is always safe locally."
        ),
        narrative=(
            "Once the market is closed and every transaction is final, "
            "the manager triggers <b>Cloud Sync</b> — that pushes "
            "everything to Google Sheets and Drive so coordinators can "
            "review today's data. If Wi-Fi is unavailable at the venue, "
            "no problem: reports can be exported to CSV and emailed "
            "once the laptop's home on a network. Either way, your data "
            "is safe locally on the laptop the whole time. That's the "
            "cycle."
        ),
        scene_factory=Stage5Scene,
    ),
)


# ── Progress rail (numbered circular nodes + fill line) ─────────

class StageIndicator(QFrame):
    """One node in the progress rail — a numbered circle with three
    visual states:

    * **pending** — white fill, soft grey ring, grey numeral
    * **active**  — solid FAM-green fill, white numeral, soft halo
    * **complete** — solid FAM-green fill, white checkmark

    Public API kept stable for the test suite:
    ``setActive(bool)`` toggles the active state, and ``clicked(int)``
    emits the node's 1-based number when the user clicks it.
    """

    clicked = Signal(int)

    def __init__(self, number: int, parent=None):
        super().__init__(parent)
        self._number = number
        self._state = 'pending'  # 'pending' | 'active' | 'complete'
        self.setFixedSize(_NODE_BOX, _NODE_BOX)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # No layout — we paint the circle directly so we get pixel-
        # perfect control over the halo, ring, and checkmark.

    def setActive(self, active: bool):
        """Test-compatible setter.  ``True`` → active state.  ``False``
        falls back to pending; the ``ProgressRail`` will subsequently
        upgrade past nodes to ``complete`` via ``set_active(idx)``."""
        if active:
            self._state = 'active'
        elif self._state == 'active':
            self._state = 'pending'
        self.update()

    def setStageState(self, state: str):
        """Direct state setter used by the parent rail to mark past
        nodes complete and future nodes pending."""
        if state not in ('pending', 'active', 'complete'):
            return
        self._state = state
        self.update()

    def state(self) -> str:
        return self._state

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._number)
        super().mousePressEvent(event)

    # ── Painting ──────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        r = _NODE_SIZE / 2.0
        circle = QRectF(cx - r, cy - r, _NODE_SIZE, _NODE_SIZE)

        if self._state == 'active':
            # Soft halo — a translucent ring slightly larger than the node.
            halo = QColor(ACCENT_GREEN)
            halo.setAlpha(55)
            painter.setBrush(QBrush(halo))
            painter.setPen(Qt.NoPen)
            halo_r = r + 5
            painter.drawEllipse(QRectF(cx - halo_r, cy - halo_r,
                                       halo_r * 2, halo_r * 2))
            # Filled circle
            painter.setBrush(QBrush(QColor(ACCENT_GREEN)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(circle)
            # White numeral
            self._draw_number(painter, circle, QColor(WHITE), bold=True)

        elif self._state == 'complete':
            # Filled circle (slightly muted vs active so the active node
            # remains the visual anchor of the row).
            painter.setBrush(QBrush(QColor(ACCENT_GREEN)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(circle)
            # White checkmark
            pen = QPen(QColor(WHITE), 2.4, Qt.SolidLine,
                       Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            offset = _NODE_SIZE * 0.18
            painter.drawLine(QPointF(cx - offset, cy + 0.5),
                             QPointF(cx - offset / 4.0, cy + offset * 0.9))
            painter.drawLine(QPointF(cx - offset / 4.0, cy + offset * 0.9),
                             QPointF(cx + offset * 1.05, cy - offset * 0.9))

        else:  # pending
            painter.setBrush(QBrush(QColor(WHITE)))
            painter.setPen(QPen(QColor(_NODE_PENDING_RING), 1.4))
            painter.drawEllipse(circle.adjusted(0.7, 0.7, -0.7, -0.7))
            self._draw_number(painter, circle,
                              QColor(MEDIUM_GRAY), bold=True)

        painter.end()

    def _draw_number(self, painter: QPainter, rect: QRectF,
                     color: QColor, bold: bool = True):
        font = QFont('Inter')
        font.setBold(bold)
        font.setPointSize(11)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(rect, Qt.AlignCenter, str(self._number))


class ProgressRail(QWidget):
    """Horizontal stepper of numbered circular nodes connected by a
    thin line that fills FAM-green left-to-right as the user advances
    through the stages.

    The rail composes ``StageIndicator`` instances and exposes the
    list as ``.nodes`` so the parent widget can store them as
    ``self._indicators`` (preserving the test-suite API).
    """

    indicator_clicked = Signal(int)  # 1-based stage number

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self._count = max(0, int(count))
        self._active_idx = 0
        self.nodes: list[StageIndicator] = []

        total_w = (self._count - 1) * _RAIL_GAP + _NODE_BOX if self._count else 0
        self.setFixedSize(total_w, _NODE_BOX)
        self.setAttribute(Qt.WA_TranslucentBackground)

        for i in range(self._count):
            node = StageIndicator(i + 1, parent=self)
            node.clicked.connect(self.indicator_clicked.emit)
            node.move(i * _RAIL_GAP, 0)
            self.nodes.append(node)

    def set_active(self, idx: int):
        """Mark stage ``idx`` (0-based) as active; earlier stages
        become ``complete``, later stages become ``pending``."""
        if not (0 <= idx < self._count):
            return
        self._active_idx = idx
        for i, n in enumerate(self.nodes):
            if i < idx:
                n.setStageState('complete')
            elif i == idx:
                n.setStageState('active')
            else:
                n.setStageState('pending')
        self.update()  # repaint connecting line

    def paintEvent(self, event):
        if not self.nodes:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Line runs through the centers of the first and last nodes.
        first_center = self.nodes[0].geometry().center()
        last_center = self.nodes[-1].geometry().center()
        y = float(first_center.y())
        # Tuck the line ends slightly inside the outermost nodes so it
        # peeks out from under the circles rather than passing through
        # them visibly at the outer edges.
        inset = _NODE_SIZE / 2.0 - 1
        x_start = float(first_center.x()) + inset
        x_end = float(last_center.x()) - inset

        # Pending segment (full width, light grey).
        pen = QPen(QColor(_RAIL_LINE_PENDING), 2.0)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(x_start, y), QPointF(x_end, y))

        # Filled segment up to the active node.
        if self._active_idx > 0:
            active_center_x = float(
                self.nodes[self._active_idx].geometry().center().x())
            x_filled_end = active_center_x - inset
            pen = QPen(QColor(_RAIL_LINE_FILLED), 2.4)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(x_start, y), QPointF(x_filled_end, y))

        painter.end()


# ── Main walkthrough widget ─────────────────────────────────────

class WorkflowWalkthroughWidget(QWidget):
    """The animated 5-stage walkthrough.  Lives as the first tab of the
    Help screen; auto-plays from stage 1 the first time it's shown."""

    skip_requested = Signal()  # user clicked "Skip Tour" → switch to Browse

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_index = 0
        self._is_paused = False
        self._has_played_once = False

        # Build scenes once and stash them in a stack.  Each scene
        # animation loops in place — there's no auto-advance.  The
        # volunteer chooses when to move on by clicking Next (which
        # we flash to draw attention after the first iteration).
        self._scenes: list[WalkthroughScene] = [
            stage.scene_factory() for stage in STAGES
        ]
        for scene in self._scenes:
            scene.iteration_completed.connect(self._on_scene_iteration_done)

        # Next-button flash bookkeeping.  We toggle between the default
        # button stylesheet and a highlighted (gold-on-white) stylesheet
        # via a recurring QTimer.  Started when a scene's first iteration
        # finishes; stopped on user navigation or pause.
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(700)
        self._flash_timer.timeout.connect(self._toggle_next_flash)
        self._flash_visible = False
        self._next_btn_default_style = ""   # populated in _build_ui
        self._next_btn_flash_style = ""     # populated in _build_ui

        self._build_ui()
        self._show_stage(0, autoplay=False)

    # ── UI assembly ────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        # 4 / 8 / 16 / 24 vertical rhythm.  Outer margin = 24,
        # block-to-block spacing = 16 (the layout default), and individual
        # blocks tighten internally to 4 / 8 where appropriate.
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # ── Top banner — soft tinted surface, ghost Skip Tour ──
        layout.addWidget(self._build_banner())

        # ── Progress rail ──────────────────────────────────────
        rail_row = QHBoxLayout()
        rail_row.setContentsMargins(0, 0, 0, 0)
        rail_row.setSpacing(0)
        rail_row.addStretch()
        self._progress_rail = ProgressRail(len(STAGES), parent=self)
        self._progress_rail.indicator_clicked.connect(
            self._on_indicator_clicked)
        # Preserve the test-protected `_indicators` API — a list of
        # StageIndicator instances each exposing setActive(bool).
        self._indicators: list[StageIndicator] = list(
            self._progress_rail.nodes)
        rail_row.addWidget(self._progress_rail)
        rail_row.addStretch()
        layout.addLayout(rail_row)

        # ── Eyebrow + title block ──────────────────────────────
        title_block = QVBoxLayout()
        title_block.setContentsMargins(0, 0, 0, 0)
        title_block.setSpacing(4)

        self._stage_role = QLabel()
        self._stage_role.setStyleSheet(
            f"color:{SUBTITLE_GRAY};font-size:11px;font-weight:600;"
            f"letter-spacing:1.4px;background:transparent;")
        self._stage_role.setAlignment(Qt.AlignCenter)
        title_block.addWidget(self._stage_role)

        self._stage_title = QLabel()
        self._stage_title.setStyleSheet(
            f"color:{PRIMARY_GREEN};font-size:22px;font-weight:700;"
            f"background:transparent;letter-spacing:-0.2px;")
        self._stage_title.setAlignment(Qt.AlignCenter)
        title_block.addWidget(self._stage_title)

        layout.addLayout(title_block)

        # ── Unified scene + narrative card ─────────────────────
        layout.addWidget(self._build_unified_card(), 1)

        # ── Controls ───────────────────────────────────────────
        layout.addLayout(self._build_controls_row())

    # ── Banner ─────────────────────────────────────────────────

    def _build_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName('walkthrough_banner')
        banner.setStyleSheet(
            f"#walkthrough_banner {{"
            f"  background:{_BANNER_TINT};"
            f"  border:1px solid {_BANNER_BORDER};"
            f"  border-radius:10px;"
            f"}}")

        wrap = QHBoxLayout(banner)
        wrap.setContentsMargins(20, 14, 16, 14)
        wrap.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(2)

        title = QLabel('Your Day at the Market')
        title.setStyleSheet(
            f"color:{PRIMARY_GREEN};font-size:18px;font-weight:700;"
            f"background:transparent;letter-spacing:-0.2px;")
        title_box.addWidget(title)

        sub = QLabel('≈ 90 second walkthrough · 5 stages')
        sub.setStyleSheet(
            f"color:{SUBTITLE_GRAY};font-size:11.5px;background:transparent;")
        title_box.addWidget(sub)

        wrap.addLayout(title_box)
        wrap.addStretch()

        skip_btn = QPushButton('Skip tour  →')
        skip_btn.setCursor(Qt.PointingHandCursor)
        skip_btn.setObjectName('walkthrough_skip')
        skip_btn.setStyleSheet(
            f"#walkthrough_skip {{"
            f"  background:transparent;color:{SUBTITLE_GRAY};"
            f"  border:none;padding:6px 4px;"
            f"  font-size:12px;font-weight:600;"
            f"}}"
            f"#walkthrough_skip:hover {{ color:{PRIMARY_GREEN}; }}")
        skip_btn.clicked.connect(self.skip_requested.emit)
        wrap.addWidget(skip_btn, 0, Qt.AlignVCenter)

        return banner

    # ── Unified card ───────────────────────────────────────────

    def _build_unified_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName('walkthrough_card')
        card.setStyleSheet(
            f"#walkthrough_card {{"
            f"  background:{WHITE};"
            f"  border:1px solid {_CARD_BORDER};"
            f"  border-radius:12px;"
            f"}}")
        # Soft elevation that ties the scene + narrative together.
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setColor(QColor(0, 0, 0, 30))
        shadow.setOffset(0, 4)
        card.setGraphicsEffect(shadow)

        card_v = QVBoxLayout(card)
        card_v.setContentsMargins(0, 0, 0, 0)
        card_v.setSpacing(0)

        # ── Scene area (top half) ─────────────────────────────
        scene_holder = QFrame()
        scene_holder.setObjectName('walkthrough_scene_holder')
        scene_holder.setStyleSheet(
            f"#walkthrough_scene_holder {{"
            f"  background:transparent;"
            f"  border-top-left-radius:12px;"
            f"  border-top-right-radius:12px;"
            f"}}")
        scene_v = QVBoxLayout(scene_holder)
        scene_v.setContentsMargins(20, 20, 20, 20)
        scene_v.setSpacing(0)

        self._scene_stack = QStackedWidget()
        self._scene_stack.setStyleSheet('background:transparent;')
        for scene in self._scenes:
            self._scene_stack.addWidget(scene)
        scene_v.addWidget(self._scene_stack)
        card_v.addWidget(scene_holder)

        # ── Inner divider hairline ────────────────────────────
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(
            f"background:{_DIVIDER};border:none;")
        card_v.addWidget(divider)

        # ── Narrative section (bottom half) ───────────────────
        narrative_holder = QFrame()
        narrative_holder.setObjectName('walkthrough_narrative_holder')
        narrative_holder.setStyleSheet(
            f"#walkthrough_narrative_holder {{"
            f"  background:{WHITE};"
            f"  border-bottom-left-radius:12px;"
            f"  border-bottom-right-radius:12px;"
            f"}}")
        narr_v = QVBoxLayout(narrative_holder)
        narr_v.setContentsMargins(22, 18, 22, 20)
        narr_v.setSpacing(12)

        # Key takeaway strip — gold-accented warm callout.
        self._takeaway = QLabel()
        self._takeaway.setObjectName('walkthrough_takeaway')
        self._takeaway.setWordWrap(True)
        self._takeaway.setTextFormat(Qt.RichText)
        self._takeaway.setStyleSheet(
            f"#walkthrough_takeaway {{"
            f"  background:{_TAKEAWAY_BG};"
            f"  color:{TEXT_COLOR};"
            f"  border-left:3px solid {_TAKEAWAY_ACCENT};"
            f"  border-top-right-radius:6px;"
            f"  border-bottom-right-radius:6px;"
            f"  padding:10px 14px;"
            f"  font-size:12.5px;"
            f"  font-weight:500;"
            f"}}")
        narr_v.addWidget(self._takeaway)

        # Narrative paragraph.
        self._narrative = QLabel()
        self._narrative.setWordWrap(True)
        self._narrative.setTextFormat(Qt.RichText)
        self._narrative.setStyleSheet(
            f"color:{TEXT_COLOR};font-size:13px;line-height:1.65;"
            f"background:transparent;")
        self._narrative.setMinimumHeight(70)
        self._narrative.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)
        narr_v.addWidget(self._narrative, 1)

        card_v.addWidget(narrative_holder, 1)

        return card

    # ── Controls row ───────────────────────────────────────────

    def _build_controls_row(self) -> QHBoxLayout:
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)

        # Prev — restrained ghost button on the left.
        self._prev_btn = self._make_ghost_button('←  Prev')
        self._prev_btn.clicked.connect(self._on_prev)
        controls.addWidget(self._prev_btn)

        controls.addStretch()

        # Pause + Restart — small ghost icon-style buttons grouped centrally.
        self._pause_btn = self._make_ghost_button('❚❚  Pause', compact=True)
        self._pause_btn.clicked.connect(self._on_pause_toggle)
        controls.addWidget(self._pause_btn)

        self._restart_btn = self._make_ghost_button('↻  Restart',
                                                     compact=True)
        self._restart_btn.clicked.connect(self._on_restart)
        controls.addWidget(self._restart_btn)

        controls.addStretch()

        # Next — primary FAM-green pill on the right.
        self._next_btn = self._make_primary_button('Next  →')
        self._next_btn.clicked.connect(self._on_next)
        controls.addWidget(self._next_btn)

        # Cache the default + flash stylesheets for the Next button so
        # we can toggle without recomputing the strings each tick.
        self._next_btn_default_style = self._primary_button_style()
        self._next_btn_flash_style = self._primary_button_flash_style()

        return controls

    # ── Button factories + styles ─────────────────────────────

    def _ghost_button_style(self, compact: bool = False) -> str:
        pad = '6px 10px' if compact else '8px 14px'
        return (
            f"QPushButton {{"
            f"  background:transparent;color:{TEXT_COLOR};"
            f"  border:1px solid transparent;border-radius:8px;"
            f"  padding:{pad};font-size:12px;font-weight:600;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background:#F0EFEB;color:{PRIMARY_GREEN};"
            f"  border-color:{LIGHT_GRAY};"
            f"}}"
            f"QPushButton:disabled {{"
            f"  color:#BDBDBD;background:transparent;"
            f"  border-color:transparent;"
            f"}}")

    def _primary_button_style(self) -> str:
        """Default Next button — solid FAM green pill with white text."""
        return (
            f"QPushButton {{"
            f"  background:{ACCENT_GREEN};color:{WHITE};"
            f"  border:1px solid {ACCENT_GREEN};border-radius:8px;"
            f"  padding:9px 22px;font-size:12.5px;font-weight:700;"
            f"  letter-spacing:0.2px;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background:{PRIMARY_GREEN};border-color:{PRIMARY_GREEN};"
            f"}}"
            f"QPushButton:disabled {{"
            f"  background:#F2F2F2;color:#BDBDBD;border-color:#E2E2E2;"
            f"}}")

    def _primary_button_flash_style(self) -> str:
        """Soft warm pulse — warm-tinted bg with a thicker gold border.
        Reads as 'this is your next move' without alarming the eye."""
        return (
            f"QPushButton {{"
            f"  background:{_NEXT_FLASH_BG};color:{PRIMARY_GREEN};"
            f"  border:2px solid {_NEXT_FLASH_BORDER};border-radius:8px;"
            f"  padding:8px 21px;font-size:12.5px;font-weight:700;"
            f"  letter-spacing:0.2px;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background:{HARVEST_GOLD};color:{WHITE};"
            f"  border-color:{HARVEST_GOLD};"
            f"}}"
            f"QPushButton:disabled {{"
            f"  background:#F2F2F2;color:#BDBDBD;border-color:#E2E2E2;"
            f"}}")

    def _make_ghost_button(self, text: str,
                            compact: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumWidth(96 if not compact else 88)
        btn.setStyleSheet(self._ghost_button_style(compact=compact))
        return btn

    def _make_primary_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumWidth(150)
        btn.setStyleSheet(self._primary_button_style())
        return btn

    # ── Stage transitions ──────────────────────────────────────

    def _show_stage(self, idx: int, autoplay: bool = True):
        """Switch to stage idx (0-based).  Resets the previous scene,
        swaps the visible scene, updates indicators + narrative.

        Each scene's animation now LOOPS in place — there's no
        auto-advance timer.  The volunteer chooses when to move on by
        clicking Next (which we flash after the first iteration)."""
        if idx < 0 or idx >= len(STAGES):
            return

        # Reset outgoing scene's animation, stop any flash in progress
        if 0 <= self._current_index < len(self._scenes):
            self._scenes[self._current_index].reset()
        self._stop_next_flash()

        self._current_index = idx
        stage = STAGES[idx]

        # Update indicator highlight (test-asserted bool toggle on every node).
        for i, ind in enumerate(self._indicators):
            ind.setActive(i == idx)
        # Also drive the rail's past/active/future visual state — this
        # is what makes earlier nodes display checkmarks and the
        # connecting line fill green up to the active node.
        if hasattr(self, '_progress_rail'):
            self._progress_rail.set_active(idx)

        # Update text
        self._stage_role.setText(stage.role_label.upper())
        self._stage_title.setText(stage.title)
        if hasattr(self, '_takeaway'):
            self._takeaway.setText(stage.key_takeaway or '')
        self._narrative.setText(stage.narrative)

        # Show the scene
        self._scene_stack.setCurrentIndex(idx)

        # Update prev/next button states + label.  On the final stage
        # the Next button transforms into a "tour complete" CTA that
        # routes the user to the Browse tab via the existing
        # ``skip_requested`` signal — no disabled dead-end.
        self._prev_btn.setEnabled(idx > 0)
        is_last = (idx >= len(STAGES) - 1)
        self._next_btn.setEnabled(True)
        if is_last:
            self._next_btn.setText('Tour complete  ·  Browse →')
        else:
            self._next_btn.setText('Next  →')
        # Reset to the default primary style after any flash residue.
        self._next_btn.setStyleSheet(self._next_btn_default_style)

        # Play the scene (it loops on its own; pause control is honored)
        if autoplay and not self._is_paused:
            self._scenes[idx].play()
        elif autoplay:
            # Honor the paused state — scene is built but not running
            self._scenes[idx].play()
            self._scenes[idx].pause()

    # ── Scene iteration callback ──────────────────────────────

    def _on_scene_iteration_done(self):
        """One pass of the active scene's animation just completed.

        Start flashing the Next button so the volunteer knows it's safe
        to move on.  Skip if Next is disabled (we're on the last stage),
        if the user has paused, or if the flash is already running."""
        if not self._next_btn.isEnabled():
            return
        if self._is_paused:
            return
        if self._flash_timer.isActive():
            return
        self._start_next_flash()

    # ── Next-button flash ─────────────────────────────────────

    def _start_next_flash(self):
        """Begin the flashing call-to-action on the Next button."""
        self._flash_visible = False
        self._flash_timer.start()

    def _stop_next_flash(self):
        """Stop the flash and restore the Next button's default look."""
        self._flash_timer.stop()
        self._flash_visible = False
        self._next_btn.setStyleSheet(self._next_btn_default_style)

    def _toggle_next_flash(self):
        self._flash_visible = not self._flash_visible
        if self._flash_visible:
            self._next_btn.setStyleSheet(self._next_btn_flash_style)
        else:
            self._next_btn.setStyleSheet(self._next_btn_default_style)

    # ── Control handlers ───────────────────────────────────────

    def _on_prev(self):
        self._stop_next_flash()
        if self._current_index > 0:
            self._show_stage(self._current_index - 1)

    def _on_next(self):
        self._stop_next_flash()
        if self._current_index < len(STAGES) - 1:
            self._show_stage(self._current_index + 1)
        else:
            # Final stage — Next has been rebranded as the "tour complete"
            # CTA that hands the user off to the Browse tab via the
            # already-wired ``skip_requested`` signal.
            self.skip_requested.emit()

    def _on_restart(self):
        self._stop_next_flash()
        self._is_paused = False
        self._pause_btn.setText('❚❚  Pause')
        self._show_stage(0)

    def _on_pause_toggle(self):
        self._is_paused = not self._is_paused
        if self._is_paused:
            self._scenes[self._current_index].pause()
            self._stop_next_flash()
            self._pause_btn.setText('▶  Play')
        else:
            self._scenes[self._current_index].resume()
            self._pause_btn.setText('❚❚  Pause')

    def _on_indicator_clicked(self, number: int):
        # 1-based number → 0-based index
        self._stop_next_flash()
        self._show_stage(number - 1)

    # ── External activation hook ───────────────────────────────

    def start_if_first_view(self):
        """Called by the parent screen when this tab becomes visible.

        Auto-plays from stage 1 the first time the user lands here in a
        session.  Subsequent activations resume wherever they left off
        without restarting (so a volunteer can switch tabs and come
        back without losing progress).
        """
        if not self._has_played_once:
            self._has_played_once = True
            self._show_stage(0, autoplay=True)
