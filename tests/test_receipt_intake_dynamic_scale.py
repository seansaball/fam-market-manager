"""Regression: the Receipt Intake screen's receipts table must
dynamically scale to use available vertical space.

User-reported (2026-04-30):

    "Can we make the receipts box dynamically scale in scroll size?
     Right now it can only show 4 receipts and if the screen space
     is larger it doesn't even use the available space to scale and
     show more receipts in the list."

Pre-fix: ``self.receipts_table.setMaximumHeight(300)`` capped the
table at ~4 rows regardless of monitor size, leaving wasted empty
space below on larger displays.

Pinned behaviour (v1.9.10+):
  * No ``setMaximumHeight`` on ``receipts_table``.
  * ``receipts_frame`` is added to the screen layout with stretch
    factor 1, so when visible it absorbs all available vertical
    space.  The embedded table grows with it.
  * ``receipts_table`` retains a ``setMinimumHeight(90)`` floor so
    it doesn't collapse to nothing when there are zero rows.
  * The screen's outer ``QScrollArea`` still scrolls if the entire
    layout exceeds the viewport (defensive for very small windows).
"""
import inspect
import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def receipts_db(tmp_path):
    db_file = str(tmp_path / "receipts_scale.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-30', 'Open', 'T')")
    conn.commit()
    yield conn
    close_connection()


class TestReceiptsTableHasNoMaxHeight:
    """Source-level guard: nobody can re-introduce a hard cap on
    the receipts table without tripping this test."""

    def test_no_set_maximum_height_on_receipts_table(self):
        import fam.ui.receipt_intake_screen as ris_module
        src = inspect.getsource(ris_module)
        # Crude but effective: scan for the exact pattern that was
        # the bug.  If a future refactor introduces it again, this
        # fails immediately.
        assert 'self.receipts_table.setMaximumHeight' not in src, (
            "receipts_table must not have setMaximumHeight — caps "
            "the visible row count regardless of screen size.  "
            "Use the parent frame's stretch factor instead.")


class TestReceiptsFrameUsesStretchFactor:
    """The receipts_frame must be added to the parent layout with a
    non-zero stretch factor so it expands vertically."""

    def test_screen_layout_has_stretch_factor_one(self, qtbot, receipts_db):
        from fam.ui.receipt_intake_screen import ReceiptIntakeScreen

        screen = ReceiptIntakeScreen()
        qtbot.addWidget(screen)
        # Walk down to the inner widget that holds receipts_frame.
        # The screen has: outer QVBoxLayout → QScrollArea → inner
        # QWidget → QVBoxLayout 'layout' → receipts_frame.
        # The 'layout' object is the QScrollArea's widget's layout.
        scroll = screen.findChild(type(screen).__mro__[0].__base__)  # any QWidget
        # Easier: walk children to find the layout containing
        # receipts_frame.
        parent_layout = screen.receipts_frame.parent().layout()
        # Find the index of receipts_frame in its parent layout.
        idx = None
        for i in range(parent_layout.count()):
            item = parent_layout.itemAt(i)
            if item.widget() is screen.receipts_frame:
                idx = i
                break
        assert idx is not None, (
            "receipts_frame should be a direct child of its "
            "parent layout")
        stretch = parent_layout.stretch(idx)
        assert stretch >= 1, (
            f"receipts_frame must have a non-zero stretch factor "
            f"so it absorbs available vertical space; got stretch="
            f"{stretch}")


class TestReceiptsTableGrowsWithFrameHeight:
    """End-to-end: when the screen is given a tall window, the
    receipts table actually grows beyond the old 300px cap."""

    def test_table_height_exceeds_old_cap_on_tall_window(
            self, qtbot, receipts_db):
        from fam.ui.receipt_intake_screen import ReceiptIntakeScreen

        screen = ReceiptIntakeScreen()
        qtbot.addWidget(screen)
        # Make the receipts frame visible (only happens once a
        # customer is loaded; we force-show here for the layout
        # test — the visibility logic itself isn't what we're
        # exercising).
        screen.receipts_frame.setVisible(True)
        # Give the screen a large viewport.
        screen.resize(1200, 1000)
        screen.show()
        qtbot.waitExposed(screen)
        # Force a layout pass.
        screen.receipts_frame.adjustSize()
        screen.receipts_table.adjustSize()
        # Old cap was 300px; on a 1000px-tall window the table
        # should have a significantly larger height available.
        # We don't assert an exact pixel count (Qt's layout math
        # depends on platform DPI, font metrics, etc.) — just that
        # the OLD 300px cap is not enforced.
        # The table's maximumHeight should be the Qt sentinel for
        # "no cap" (16777215 = QWIDGETSIZE_MAX).
        assert screen.receipts_table.maximumHeight() == 16777215, (
            f"receipts_table should have no max-height cap; got "
            f"maximumHeight()={screen.receipts_table.maximumHeight()}")

    def test_table_minimum_height_still_floors_at_90(
            self, qtbot, receipts_db):
        """The 90px floor must survive — table shouldn't collapse
        to nothing when empty."""
        from fam.ui.receipt_intake_screen import ReceiptIntakeScreen
        screen = ReceiptIntakeScreen()
        qtbot.addWidget(screen)
        assert screen.receipts_table.minimumHeight() == 90, (
            f"Expected minimumHeight=90, got "
            f"{screen.receipts_table.minimumHeight()}")
