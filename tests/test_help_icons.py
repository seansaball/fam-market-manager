"""Tests for the custom flat-icon library used in the Help walkthrough.

These tests focus on **structural correctness**:
  * Every icon class can be instantiated without error
  * Icons have configurable size and color
  * The painting code does not raise (calling ``paintEvent`` on each)
  * SceneCard composes an icon + label correctly
  * StepBadge renders the right number

Visual quality (do the icons LOOK good?) is editorial — these tests
catch regressions that would crash the walkthrough or render an empty
square.
"""

import pytest

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QImage, QPainter

from fam.ui.help_icons import (
    ArrowIcon, BoxIcon, CardIcon, CashIcon, CheckIcon, CheckmarkIcon,
    ClipboardIcon, CloudIcon, EnvelopeIcon, FileIcon, FlatIcon,
    LaptopIcon, ManagerIcon, PersonIcon, ReceiptIcon, RunnerIcon,
    SceneCard, StampIcon, StepBadge, TableIcon, VendorStallIcon,
)


# Every concrete icon class we expose
ICON_CLASSES = (
    PersonIcon, VendorStallIcon, ReceiptIcon, LaptopIcon, CardIcon,
    CheckIcon, CashIcon, RunnerIcon, BoxIcon, StampIcon, CloudIcon,
    FileIcon, EnvelopeIcon, ManagerIcon, ClipboardIcon, TableIcon,
    CheckmarkIcon,
    # ArrowIcon takes a direction kwarg, tested separately
)


# ══════════════════════════════════════════════════════════════════
# Icon instantiation + painting
# ══════════════════════════════════════════════════════════════════
class TestIconInstantiation:

    @pytest.mark.parametrize('icon_cls', ICON_CLASSES)
    def test_default_construction(self, qtbot, icon_cls):
        """Every icon must instantiate with no arguments."""
        icon = icon_cls()
        qtbot.addWidget(icon)
        assert icon is not None

    @pytest.mark.parametrize('icon_cls', ICON_CLASSES)
    def test_custom_size(self, qtbot, icon_cls):
        icon = icon_cls(size=80)
        qtbot.addWidget(icon)
        assert icon.size() == QSize(80, 80)

    @pytest.mark.parametrize('icon_cls', ICON_CLASSES)
    def test_custom_primary_color(self, qtbot, icon_cls):
        icon = icon_cls(primary='#ff0000')
        qtbot.addWidget(icon)
        assert icon._primary.name() == '#ff0000'

    @pytest.mark.parametrize('icon_cls', ICON_CLASSES)
    def test_inherits_flat_icon(self, icon_cls):
        """All concrete icons must subclass the FlatIcon base.  No Qt
        instance needed for issubclass."""
        assert issubclass(icon_cls, FlatIcon)

    @pytest.mark.parametrize('icon_cls', ICON_CLASSES)
    def test_paint_does_not_raise(self, qtbot, icon_cls):
        """Render the icon to an off-screen QImage and confirm the
        paintEvent path does not throw.  Catches typos in the QPainter
        calls (wrong arg count, missing brushes, etc.)."""
        icon = icon_cls(size=64)
        qtbot.addWidget(icon)
        image = QImage(64, 64, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        try:
            icon._paint(painter, 64, 64)
        finally:
            painter.end()
        # Image should have content (some non-zero pixels)
        # Sample a handful of pixels — if all are transparent black,
        # the icon didn't paint anything.
        had_content = False
        for x, y in ((16, 16), (32, 32), (48, 48), (32, 16), (32, 48)):
            if image.pixel(x, y) != 0:
                had_content = True
                break
        assert had_content, \
            f"{icon_cls.__name__} _paint produced no visible pixels"


# ══════════════════════════════════════════════════════════════════
# ArrowIcon directions
# ══════════════════════════════════════════════════════════════════
class TestArrowIcon:
    @pytest.mark.parametrize('direction',
                              ['right', 'left', 'up', 'down'])
    def test_each_direction_paints(self, qtbot, direction):
        icon = ArrowIcon(direction=direction, size=64)
        qtbot.addWidget(icon)
        image = QImage(64, 64, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        try:
            icon._paint(painter, 64, 64)
        finally:
            painter.end()


# ══════════════════════════════════════════════════════════════════
# SceneCard composition
# ══════════════════════════════════════════════════════════════════
class TestSceneCard:
    def test_scene_card_with_icon_and_caption(self, qtbot):
        icon = PersonIcon(size=40)
        card = SceneCard(icon, caption='Customer',
                         card_width=100, card_height=110)
        qtbot.addWidget(card)
        assert card.size() == QSize(100, 110)

    def test_scene_card_with_sub_caption(self, qtbot):
        icon = PersonIcon(size=40)
        card = SceneCard(icon, caption='Customer',
                         sub_caption='3 receipts',
                         card_width=100, card_height=120)
        qtbot.addWidget(card)
        assert card is not None

    def test_scene_card_default_size(self, qtbot):
        icon = PersonIcon(size=40)
        card = SceneCard(icon, caption='Test')
        qtbot.addWidget(card)
        # Default 110x110 per current SceneCard signature
        assert card.size() == QSize(110, 110)


# ══════════════════════════════════════════════════════════════════
# StepBadge
# ══════════════════════════════════════════════════════════════════
class TestStepBadge:
    @pytest.mark.parametrize('n', [1, 2, 3])
    def test_step_badge_renders_number(self, qtbot, n):
        badge = StepBadge(n)
        qtbot.addWidget(badge)
        assert badge.text() == str(n)

    def test_step_badge_is_circular_size(self, qtbot):
        badge = StepBadge(1)
        qtbot.addWidget(badge)
        assert badge.size() == QSize(20, 20)


# ══════════════════════════════════════════════════════════════════
# Sanity: walkthrough scenes use the new icon library, not emoji
# ══════════════════════════════════════════════════════════════════
class TestWalkthroughUsesFlatIcons:
    """Source-level guard that the walkthrough scenes import from the
    flat-icon library and do NOT fall back to emoji-only labels."""

    def test_walkthrough_imports_icon_library(self):
        import inspect
        import fam.ui.help_walkthrough as wt
        src = inspect.getsource(wt)
        assert 'from fam.ui.help_icons import' in src

    def test_walkthrough_no_longer_uses_emoji_label_helper(self):
        """The old _emoji_label helper was deprecated in v1.9.x — it
        should be removed.  Catches re-introduction during refactors."""
        import inspect
        import fam.ui.help_walkthrough as wt
        src = inspect.getsource(wt)
        assert 'def _emoji_label' not in src, \
            "_emoji_label helper was re-introduced; the walkthrough " \
            "should use FlatIcon-based widgets instead"

    def test_scenes_use_scene_card(self):
        """Each scene composes its layout from SceneCard widgets."""
        import inspect
        import fam.ui.help_walkthrough as wt
        src = inspect.getsource(wt)
        assert 'SceneCard(' in src, \
            "Walkthrough scenes must use SceneCard for composition"
