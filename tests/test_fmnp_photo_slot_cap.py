"""Regression: FMNP Entry screen must cap the number of photo
upload rows it renders so a typo in the amount field can't freeze
the UI thread.

User-reported (2026-04-30 onsite, screenshot of FMNP Check Tracking):

    Volunteer typed $4533 in the Amount field.  FMNP denomination
    is $5 → 906 expected checks.  The screen tried to render 906
    photo upload rows (each = 1 QFrame + label + Attach button +
    file label + Clear button = ~5 widgets), totalling ~4500
    widgets created synchronously.  App froze ("Not Responding"
    in the title bar).

Fix: ``MAX_PHOTO_SLOTS`` constant in ``fmnp_screen.py`` (default
50) caps the number of UI rows rendered.  When the amount /
denomination would exceed the cap, a warning label surfaces
explaining the cap and recommending the user split the entry
into multiple smaller ones.  The saved ``check_count`` value
still reflects the true count from the spinbox so reports stay
accurate.
"""
import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def fmnp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "fmnp_cap.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V1')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1)")
    # FMNP method with $5 denomination, like seed.
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " denomination, sort_order, is_active, photo_required) "
        "VALUES (2, 'FMNP', 100.0, 500, 2, 1, 'Optional')")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        " payment_method_id) VALUES (1, 2)")
    conn.execute(
        "INSERT INTO vendor_payment_methods "
        "(vendor_id, payment_method_id) VALUES (1, 2)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-30', 'Open', 'T')")
    conn.commit()
    yield conn
    close_connection()


class TestFMNPPhotoSlotCap:

    def test_max_slots_constant_is_reasonable(self):
        """The cap must be a small integer (Qt can render thousands
        of widgets but ~50 keeps the screen interactive)."""
        from fam.ui.fmnp_screen import MAX_PHOTO_SLOTS
        assert 10 <= MAX_PHOTO_SLOTS <= 200, (
            f"MAX_PHOTO_SLOTS={MAX_PHOTO_SLOTS} outside reasonable "
            f"range [10, 200]")

    def test_oversized_amount_does_not_render_thousands_of_rows(
            self, qtbot, fmnp_db):
        """User's exact scenario: $4533 with $5 denom = 906 checks.
        Only ``MAX_PHOTO_SLOTS`` rows must render."""
        from fam.ui.fmnp_screen import FMNPScreen, MAX_PHOTO_SLOTS

        screen = FMNPScreen()
        qtbot.addWidget(screen)
        # Force-set the FMNP denomination as if the user picked the
        # FMNP method on a market that has it active.
        screen._fmnp_denomination = 500
        screen.amount_spin.setValue(4533.00)
        # _on_amount_changed fires automatically via valueChanged.

        rendered = len(screen._photo_slot_widgets)
        assert rendered <= MAX_PHOTO_SLOTS, (
            f"Rendered {rendered} photo slots but cap is "
            f"{MAX_PHOTO_SLOTS}.  Pre-fix this would have rendered "
            f"906 slots and frozen the UI thread.")
        assert rendered == MAX_PHOTO_SLOTS, (
            f"For a $4533 / $5 entry, expected exactly "
            f"{MAX_PHOTO_SLOTS} rows (the cap), got {rendered}")

    def test_warning_visible_when_cap_hit(self, qtbot, fmnp_db):
        """When the amount would exceed the cap, the warning label
        must explain what happened."""
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        screen._fmnp_denomination = 500
        screen.amount_spin.setValue(4533.00)

        text = screen.photo_cap_warning.text()
        assert '906' in text, (
            f"Warning should name the un-capped check count (906): "
            f"{text!r}")
        assert ('split' in text.lower()
                or 'multiple' in text.lower()), (
            f"Warning should recommend splitting: {text!r}")

    def test_warning_hidden_when_within_cap(self, qtbot, fmnp_db):
        """A normal-sized amount must not show the warning."""
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        screen._fmnp_denomination = 500
        screen.amount_spin.setValue(50.00)  # 10 checks
        # photo_cap_warning starts hidden; should stay hidden after
        # this normal-sized amount.
        assert screen.photo_cap_warning.text() == "" or \
               not screen.photo_cap_warning.isVisible() or \
               '50' not in screen.photo_cap_warning.text()

    def test_uncapped_count_still_accurate_for_save(
            self, qtbot, fmnp_db):
        """The TRUE check count (used for the saved record) must
        not be affected by the UI cap — only the rendered widget
        count is capped."""
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        screen._fmnp_denomination = 500
        screen.amount_spin.setValue(4533.00)
        assert screen._get_uncapped_check_count() == 906, (
            f"Uncapped check count must reflect the true value "
            f"(906), got {screen._get_uncapped_check_count()}")

    def test_amount_within_cap_renders_exact_count(
            self, qtbot, fmnp_db):
        """For a normal-sized entry, the rendered count equals the
        true check count."""
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        screen._fmnp_denomination = 500
        screen.amount_spin.setValue(25.00)  # 5 checks
        assert len(screen._photo_slot_widgets) == 5, (
            f"5-check entry should render 5 rows, got "
            f"{len(screen._photo_slot_widgets)}")
