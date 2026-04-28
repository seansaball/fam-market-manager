"""Flat-style pictogram icons for the Help walkthrough.

Hand-painted via QPainter — vector, crisp at any DPI, FAM-branded.
No external dependencies (no SVG parser, no icon font, no pixmap art
pipeline).  Each icon is a small QWidget that renders itself in
``paintEvent``.

Visual style:
    - Solid filled shapes (no gradients, no shadows)
    - Rounded corners on rectangles (4-6 px radius)
    - Optional accent stripe in HARVEST_GOLD for emphasis
    - Background-on-card style — icons sit inside cards, not on raw backdrop
    - Composable via the SceneCard wrapper

Adding a new icon:
    1. Subclass FlatIcon
    2. Implement ``_paint(p: QPainter)`` — coordinates are 0..1 in a
       normalized box; the base class scales them to widget size
    3. Set DEFAULT_PRIMARY / DEFAULT_ACCENT class attributes if your
       icon should default to specific colors
"""

from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from fam.ui.styles import (
    ACCENT_GREEN, BACKGROUND, HARVEST_GOLD, LIGHT_GRAY, MEDIUM_GRAY,
    PRIMARY_GREEN, SUBTITLE_GRAY, TEXT_COLOR, WHITE,
)


# ── Base flat icon ──────────────────────────────────────────────

class FlatIcon(QWidget):
    """Base class for hand-painted flat icons.

    Subclasses implement ``_paint(painter, w, h)`` where ``w`` and ``h``
    are the integer pixel dimensions of the widget.  The base class
    handles antialiasing setup and translucent background.
    """

    DEFAULT_PRIMARY = ACCENT_GREEN
    DEFAULT_ACCENT = HARVEST_GOLD
    DEFAULT_SIZE = 56

    def __init__(self, size: int = None, primary: str = None,
                 accent: str = None, parent=None):
        super().__init__(parent)
        self._size = size or self.DEFAULT_SIZE
        self._primary = QColor(primary or self.DEFAULT_PRIMARY)
        self._accent = QColor(accent or self.DEFAULT_ACCENT)
        self.setFixedSize(self._size, self._size)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self._paint(p, self.width(), self.height())
        p.end()

    def _paint(self, p: QPainter, w: int, h: int):  # pragma: no cover - subclass impl
        raise NotImplementedError


# ── Concrete icons ──────────────────────────────────────────────

class PersonIcon(FlatIcon):
    """Stylized person silhouette — head + body."""

    def _paint(self, p: QPainter, w: int, h: int):
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        # Head: circle in the upper third
        head_d = w * 0.40
        head_x = (w - head_d) / 2
        head_y = h * 0.10
        p.drawEllipse(QRectF(head_x, head_y, head_d, head_d))
        # Body: rounded shoulders + torso
        torso_w = w * 0.60
        torso_h = h * 0.45
        torso_x = (w - torso_w) / 2
        torso_y = h * 0.50
        path = QPainterPath()
        path.addRoundedRect(QRectF(torso_x, torso_y, torso_w, torso_h),
                             torso_w * 0.30, torso_w * 0.30)
        p.drawPath(path)


class VendorStallIcon(FlatIcon):
    """Vendor stall: striped awning + booth body with a produce shape."""

    def _paint(self, p: QPainter, w: int, h: int):
        # Awning (HARVEST_GOLD triangle with curve)
        p.setBrush(QBrush(self._accent))
        p.setPen(Qt.NoPen)
        awning = QPolygonF([
            QPointF(w * 0.05, h * 0.30),
            QPointF(w * 0.95, h * 0.30),
            QPointF(w * 0.85, h * 0.40),
            QPointF(w * 0.15, h * 0.40),
        ])
        p.drawPolygon(awning)
        # Awning post left + right
        p.setBrush(QBrush(QColor(MEDIUM_GRAY)))
        p.drawRect(QRectF(w * 0.10, h * 0.30, w * 0.04, h * 0.55))
        p.drawRect(QRectF(w * 0.86, h * 0.30, w * 0.04, h * 0.55))
        # Booth counter
        p.setBrush(QBrush(QColor(WHITE)))
        p.setPen(QPen(QColor(LIGHT_GRAY), 2))
        p.drawRoundedRect(QRectF(w * 0.14, h * 0.55, w * 0.72, h * 0.30),
                          4, 4)
        # Produce on counter (small green circles)
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        for cx in (w * 0.30, w * 0.50, w * 0.70):
            p.drawEllipse(QPointF(cx, h * 0.65), w * 0.04, w * 0.04)


class ReceiptIcon(FlatIcon):
    """Paper receipt — rectangle with horizontal text lines."""

    def _paint(self, p: QPainter, w: int, h: int):
        # Paper with light shadow accent
        p.setBrush(QBrush(QColor(WHITE)))
        p.setPen(QPen(self._primary, 2))
        margin = w * 0.18
        rect = QRectF(margin, h * 0.10, w - 2 * margin, h * 0.80)
        p.drawRoundedRect(rect, 3, 3)
        # Text lines
        p.setPen(QPen(QColor(SUBTITLE_GRAY), 1.5))
        for i in range(4):
            y = h * (0.25 + i * 0.13)
            x_end = rect.right() - w * 0.06 - (w * 0.10 if i == 3 else 0)
            p.drawLine(QPointF(rect.left() + w * 0.06, y),
                       QPointF(x_end, y))


class LaptopIcon(FlatIcon):
    """Laptop — hinged screen + keyboard base."""

    def _paint(self, p: QPainter, w: int, h: int):
        # Screen
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        screen = QRectF(w * 0.18, h * 0.15, w * 0.64, h * 0.50)
        p.drawRoundedRect(screen, 4, 4)
        # Screen inner (lighter)
        p.setBrush(QBrush(QColor(WHITE)))
        inner = QRectF(w * 0.22, h * 0.20, w * 0.56, h * 0.40)
        p.drawRoundedRect(inner, 2, 2)
        # Keyboard base
        p.setBrush(QBrush(self._primary))
        keyboard = QRectF(w * 0.10, h * 0.65, w * 0.80, h * 0.10)
        p.drawRoundedRect(keyboard, 3, 3)
        # Trackpad/notch
        p.setBrush(QBrush(QColor(WHITE)))
        p.drawRect(QRectF(w * 0.42, h * 0.60, w * 0.16, h * 0.04))


class CardIcon(FlatIcon):
    """Payment card with chip and stripe (for SNAP/EBT)."""

    DEFAULT_PRIMARY = HARVEST_GOLD

    def _paint(self, p: QPainter, w: int, h: int):
        # Card body
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        card = QRectF(w * 0.08, h * 0.20, w * 0.84, h * 0.60)
        p.drawRoundedRect(card, 6, 6)
        # Magnetic stripe
        p.setBrush(QBrush(QColor(0, 0, 0, 100)))
        p.drawRect(QRectF(w * 0.08, h * 0.30, w * 0.84, h * 0.08))
        # Chip
        p.setBrush(QBrush(QColor(255, 255, 255, 200)))
        p.drawRoundedRect(QRectF(w * 0.18, h * 0.50, w * 0.18, h * 0.18),
                          2, 2)
        # Card label dots (signature)
        p.setBrush(QBrush(QColor(255, 255, 255, 180)))
        for cx in (w * 0.50, w * 0.58, w * 0.66, w * 0.74):
            p.drawEllipse(QPointF(cx, h * 0.62), w * 0.018, w * 0.018)


class CheckIcon(FlatIcon):
    """Paper check (FMNP) — rectangle with header bar + signature line."""

    def _paint(self, p: QPainter, w: int, h: int):
        # Paper
        p.setBrush(QBrush(QColor(WHITE)))
        p.setPen(QPen(self._primary, 2))
        body = QRectF(w * 0.08, h * 0.22, w * 0.84, h * 0.56)
        p.drawRoundedRect(body, 3, 3)
        # Top color band
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        p.drawRect(QRectF(w * 0.08, h * 0.22, w * 0.84, h * 0.10))
        # "FMNP" suggestion (just a thin gold horizontal bar)
        p.setBrush(QBrush(self._accent))
        p.drawRect(QRectF(w * 0.14, h * 0.40, w * 0.30, h * 0.04))
        # Amount line
        p.setPen(QPen(QColor(SUBTITLE_GRAY), 1.5))
        p.drawLine(QPointF(w * 0.14, h * 0.55), QPointF(w * 0.86, h * 0.55))
        # Signature line
        p.drawLine(QPointF(w * 0.14, h * 0.70), QPointF(w * 0.50, h * 0.70))


class CashIcon(FlatIcon):
    """Cash bill — rounded rectangle with center medallion."""

    DEFAULT_PRIMARY = "#3a7d3a"  # slightly different green for visual distinction

    def _paint(self, p: QPainter, w: int, h: int):
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        bill = QRectF(w * 0.08, h * 0.30, w * 0.84, h * 0.40)
        p.drawRoundedRect(bill, 5, 5)
        # Inner border
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(WHITE), 1.5))
        inner = QRectF(w * 0.13, h * 0.36, w * 0.74, h * 0.28)
        p.drawRoundedRect(inner, 3, 3)
        # Center circle (medallion)
        p.setBrush(QBrush(QColor(WHITE)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(w * 0.50, h * 0.50), w * 0.07, w * 0.07)
        p.setBrush(QBrush(self._primary))
        p.drawEllipse(QPointF(w * 0.50, h * 0.50), w * 0.04, w * 0.04)


class RunnerIcon(FlatIcon):
    """Person in motion — running figure with motion lines."""

    DEFAULT_PRIMARY = HARVEST_GOLD

    def _paint(self, p: QPainter, w: int, h: int):
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        # Head
        p.drawEllipse(QPointF(w * 0.55, h * 0.22), w * 0.10, w * 0.10)
        # Body (leaning forward)
        body = QPolygonF([
            QPointF(w * 0.50, h * 0.32),
            QPointF(w * 0.65, h * 0.32),
            QPointF(w * 0.55, h * 0.62),
            QPointF(w * 0.40, h * 0.62),
        ])
        p.drawPolygon(body)
        # Arms (one forward, one back)
        p.setPen(QPen(self._primary, 6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(w * 0.55, h * 0.40), QPointF(w * 0.78, h * 0.50))
        p.drawLine(QPointF(w * 0.50, h * 0.40), QPointF(w * 0.30, h * 0.30))
        # Legs (one forward, one back)
        p.drawLine(QPointF(w * 0.50, h * 0.62), QPointF(w * 0.65, h * 0.85))
        p.drawLine(QPointF(w * 0.45, h * 0.62), QPointF(w * 0.25, h * 0.85))
        # Motion lines behind
        p.setPen(QPen(QColor(MEDIUM_GRAY), 2, Qt.SolidLine, Qt.RoundCap))
        for y, length in ((h * 0.30, w * 0.18), (h * 0.45, w * 0.20),
                          (h * 0.60, w * 0.16)):
            p.drawLine(QPointF(w * 0.05, y), QPointF(w * 0.05 + length, y))


class BoxIcon(FlatIcon):
    """Package box — square with cross tape."""

    def _paint(self, p: QPainter, w: int, h: int):
        # Box body
        p.setBrush(QBrush(self._accent))
        p.setPen(Qt.NoPen)
        box = QRectF(w * 0.15, h * 0.20, w * 0.70, h * 0.65)
        p.drawRoundedRect(box, 3, 3)
        # Tape cross (lighter shade)
        p.setBrush(QBrush(QColor(255, 255, 255, 180)))
        # Vertical tape
        p.drawRect(QRectF(w * 0.42, h * 0.20, w * 0.16, h * 0.65))
        # Horizontal tape
        p.drawRect(QRectF(w * 0.15, h * 0.46, w * 0.70, h * 0.10))


class StampIcon(FlatIcon):
    """Rubber stamp — handle on top, base with pad."""

    DEFAULT_PRIMARY = HARVEST_GOLD

    def _paint(self, p: QPainter, w: int, h: int):
        # Handle
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(w * 0.35, h * 0.10, w * 0.30, h * 0.30),
                          4, 4)
        # Top knob
        p.drawEllipse(QPointF(w * 0.50, h * 0.12), w * 0.08, w * 0.04)
        # Base
        p.drawRoundedRect(QRectF(w * 0.18, h * 0.42, w * 0.64, h * 0.18),
                          3, 3)
        # Stamp pad/footprint
        p.setBrush(QBrush(self._accent))
        p.drawRoundedRect(QRectF(w * 0.10, h * 0.62, w * 0.80, h * 0.14),
                          3, 3)
        # Imprint indicator below (semi-transparent gray smudge)
        smudge = QColor(MEDIUM_GRAY)
        smudge.setAlpha(80)
        p.setBrush(QBrush(smudge))
        p.drawRoundedRect(QRectF(w * 0.20, h * 0.82, w * 0.60, h * 0.06),
                          2, 2)


class CloudIcon(FlatIcon):
    """Cloud silhouette — three overlapping circles + flat base."""

    DEFAULT_PRIMARY = ACCENT_GREEN

    def _paint(self, p: QPainter, w: int, h: int):
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        # Three bumps
        path = QPainterPath()
        # Left bump
        path.addEllipse(QPointF(w * 0.30, h * 0.50), w * 0.18, w * 0.18)
        # Middle bump (taller)
        path.addEllipse(QPointF(w * 0.50, h * 0.42), w * 0.22, w * 0.22)
        # Right bump
        path.addEllipse(QPointF(w * 0.70, h * 0.50), w * 0.18, w * 0.18)
        # Flat base
        path.addRoundedRect(QRectF(w * 0.15, h * 0.55, w * 0.70, h * 0.18),
                             w * 0.09, w * 0.09)
        p.drawPath(path.simplified())


class FileIcon(FlatIcon):
    """Document — paper with corner fold and content lines."""

    def _paint(self, p: QPainter, w: int, h: int):
        # Paper (with corner cut for fold)
        p.setBrush(QBrush(QColor(WHITE)))
        p.setPen(QPen(self._primary, 2))
        path = QPainterPath()
        path.moveTo(w * 0.20, h * 0.15)
        path.lineTo(w * 0.65, h * 0.15)
        path.lineTo(w * 0.80, h * 0.30)
        path.lineTo(w * 0.80, h * 0.85)
        path.lineTo(w * 0.20, h * 0.85)
        path.closeSubpath()
        p.drawPath(path)
        # Folded corner triangle
        p.setBrush(QBrush(self._primary, Qt.SolidPattern))
        p.setPen(Qt.NoPen)
        corner = QPolygonF([
            QPointF(w * 0.65, h * 0.15),
            QPointF(w * 0.80, h * 0.30),
            QPointF(w * 0.65, h * 0.30),
        ])
        p.drawPolygon(corner)
        # Content lines
        p.setPen(QPen(QColor(SUBTITLE_GRAY), 1.5))
        for i in range(4):
            y = h * (0.42 + i * 0.10)
            x_end = w * 0.72 - (w * 0.12 if i == 3 else 0)
            p.drawLine(QPointF(w * 0.28, y), QPointF(x_end, y))


class EnvelopeIcon(FlatIcon):
    """Envelope — rectangle with V-shaped flap."""

    def _paint(self, p: QPainter, w: int, h: int):
        # Body
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        body = QRectF(w * 0.10, h * 0.28, w * 0.80, h * 0.50)
        p.drawRoundedRect(body, 3, 3)
        # Flap
        flap = QPolygonF([
            QPointF(w * 0.10, h * 0.30),
            QPointF(w * 0.50, h * 0.58),
            QPointF(w * 0.90, h * 0.30),
        ])
        p.setBrush(QBrush(QColor(self._primary).lighter(115)))
        p.drawPolygon(flap)
        # Bottom highlight line
        p.setPen(QPen(QColor(self._primary).darker(120), 1.5))
        p.drawLine(QPointF(w * 0.10, h * 0.78), QPointF(w * 0.90, h * 0.78))


class ManagerIcon(FlatIcon):
    """Person with tie/lapel — distinguishes 'manager' from generic person."""

    DEFAULT_PRIMARY = PRIMARY_GREEN

    def _paint(self, p: QPainter, w: int, h: int):
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        # Head
        head_d = w * 0.36
        p.drawEllipse(QRectF((w - head_d) / 2, h * 0.12, head_d, head_d))
        # Body (V-collar)
        body = QPolygonF([
            QPointF(w * 0.20, h * 0.55),
            QPointF(w * 0.42, h * 0.50),
            QPointF(w * 0.50, h * 0.65),
            QPointF(w * 0.58, h * 0.50),
            QPointF(w * 0.80, h * 0.55),
            QPointF(w * 0.80, h * 0.95),
            QPointF(w * 0.20, h * 0.95),
        ])
        p.drawPolygon(body)
        # Tie (HARVEST_GOLD accent)
        p.setBrush(QBrush(self._accent))
        tie = QPolygonF([
            QPointF(w * 0.46, h * 0.55),
            QPointF(w * 0.54, h * 0.55),
            QPointF(w * 0.56, h * 0.85),
            QPointF(w * 0.50, h * 0.92),
            QPointF(w * 0.44, h * 0.85),
        ])
        p.drawPolygon(tie)


class ClipboardIcon(FlatIcon):
    """Clipboard — board with clip and form lines (the FMNP entry page)."""

    def _paint(self, p: QPainter, w: int, h: int):
        # Board
        p.setBrush(QBrush(QColor(WHITE)))
        p.setPen(QPen(self._primary, 2))
        board = QRectF(w * 0.15, h * 0.20, w * 0.70, h * 0.70)
        p.drawRoundedRect(board, 4, 4)
        # Clip at top
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        clip = QRectF(w * 0.36, h * 0.10, w * 0.28, h * 0.16)
        p.drawRoundedRect(clip, 3, 3)
        # Form rows with checkboxes
        p.setPen(QPen(QColor(SUBTITLE_GRAY), 1.5))
        for i in range(3):
            y = h * (0.40 + i * 0.13)
            # Checkbox
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(w * 0.22, y - h * 0.04, h * 0.06, h * 0.06),
                              1, 1)
            # Line
            p.drawLine(QPointF(w * 0.36, y), QPointF(w * 0.78, y))


class TableIcon(FlatIcon):
    """Folding table with a laptop — the FAM table at the market."""

    def _paint(self, p: QPainter, w: int, h: int):
        # Laptop on top (small)
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        # Screen
        screen = QRectF(w * 0.30, h * 0.18, w * 0.40, h * 0.25)
        p.drawRoundedRect(screen, 2, 2)
        p.setBrush(QBrush(QColor(WHITE)))
        inner = QRectF(w * 0.32, h * 0.21, w * 0.36, h * 0.19)
        p.drawRect(inner)
        # Base
        p.setBrush(QBrush(self._primary))
        p.drawRect(QRectF(w * 0.26, h * 0.43, w * 0.48, h * 0.04))
        # Table top
        p.setBrush(QBrush(self._accent))
        p.drawRoundedRect(QRectF(w * 0.10, h * 0.50, w * 0.80, h * 0.10),
                          3, 3)
        # Table legs
        p.drawRect(QRectF(w * 0.16, h * 0.60, w * 0.06, h * 0.30))
        p.drawRect(QRectF(w * 0.78, h * 0.60, w * 0.06, h * 0.30))


class ArrowIcon(FlatIcon):
    """Right-pointing arrow — used as connector between cards.

    Direction can be ``'right'`` (default), ``'down'``, ``'up'``, ``'left'``.
    Renders as a thin line with a triangular head.
    """

    DEFAULT_PRIMARY = MEDIUM_GRAY

    def __init__(self, direction: str = 'right', size: int = None,
                 primary: str = None, parent=None):
        super().__init__(size=size, primary=primary, parent=parent)
        self._direction = direction

    def _paint(self, p: QPainter, w: int, h: int):
        pen = QPen(self._primary, 2.5, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(QBrush(self._primary))
        cy = h / 2
        cx = w / 2
        # Default: right arrow
        # Line
        if self._direction == 'right':
            p.drawLine(QPointF(w * 0.10, cy), QPointF(w * 0.75, cy))
            head = QPolygonF([
                QPointF(w * 0.75, cy - h * 0.18),
                QPointF(w * 0.92, cy),
                QPointF(w * 0.75, cy + h * 0.18),
            ])
            p.drawPolygon(head)
        elif self._direction == 'up':
            p.drawLine(QPointF(cx, h * 0.90), QPointF(cx, h * 0.25))
            head = QPolygonF([
                QPointF(cx - w * 0.18, h * 0.25),
                QPointF(cx, h * 0.08),
                QPointF(cx + w * 0.18, h * 0.25),
            ])
            p.drawPolygon(head)
        elif self._direction == 'down':
            p.drawLine(QPointF(cx, h * 0.10), QPointF(cx, h * 0.75))
            head = QPolygonF([
                QPointF(cx - w * 0.18, h * 0.75),
                QPointF(cx, h * 0.92),
                QPointF(cx + w * 0.18, h * 0.75),
            ])
            p.drawPolygon(head)
        else:  # left
            p.drawLine(QPointF(w * 0.90, cy), QPointF(w * 0.25, cy))
            head = QPolygonF([
                QPointF(w * 0.25, cy - h * 0.18),
                QPointF(w * 0.08, cy),
                QPointF(w * 0.25, cy + h * 0.18),
            ])
            p.drawPolygon(head)


class CheckmarkIcon(FlatIcon):
    """Green checkmark in a circle — confirmation indicator."""

    DEFAULT_PRIMARY = ACCENT_GREEN

    def _paint(self, p: QPainter, w: int, h: int):
        # Filled circle
        p.setBrush(QBrush(self._primary))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(w / 2, h / 2), w * 0.42, w * 0.42)
        # Checkmark
        p.setPen(QPen(QColor(WHITE), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(QPointF(w * 0.30, h * 0.52),
                   QPointF(w * 0.46, h * 0.66))
        p.drawLine(QPointF(w * 0.46, h * 0.66),
                   QPointF(w * 0.70, h * 0.36))


# ── Scene card — icon with label below ──────────────────────────

class SceneCard(QFrame):
    """An icon paired with a caption label, displayed as a soft white card.

    Used as the building block for walkthrough scene compositions.  The
    card has a thin border + slight background tint to feel like a
    discrete element on the scene canvas.
    """

    def __init__(self, icon: FlatIcon, caption: str = '',
                 sub_caption: str = '', card_width: int = 110,
                 card_height: int = 110, parent=None):
        super().__init__(parent)
        self.setFixedSize(card_width, card_height)
        self.setStyleSheet(
            f"QFrame {{ background:{WHITE};border:1px solid {LIGHT_GRAY};"
            f"border-radius:10px; }}"
            f"QLabel {{ background:transparent; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(2)

        # Icon centered
        icon_row = QHBoxLayout()
        icon_row.addStretch()
        icon_row.addWidget(icon)
        icon_row.addStretch()
        layout.addLayout(icon_row)

        if caption:
            cap = QLabel(caption)
            cap.setAlignment(Qt.AlignCenter)
            cap.setStyleSheet(
                f"color:{TEXT_COLOR};font-size:11px;font-weight:bold;")
            layout.addWidget(cap)

        if sub_caption:
            sub = QLabel(sub_caption)
            sub.setAlignment(Qt.AlignCenter)
            sub.setWordWrap(True)
            sub.setStyleSheet(
                f"color:{SUBTITLE_GRAY};font-size:9px;font-style:italic;")
            layout.addWidget(sub)

        layout.addStretch()


class StepBadge(QLabel):
    """Numbered badge — '1' '2' '3' for the three sub-steps in stage 3.

    Solid green circle with white number.  Used as a small inline label
    on stage 3 to clarify the ordering of the volunteer's three actions.
    """

    def __init__(self, number: int, parent=None):
        super().__init__(str(number), parent)
        self.setFixedSize(20, 20)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            f"background:{ACCENT_GREEN};color:{WHITE};"
            f"border-radius:10px;font-weight:bold;font-size:11px;")
