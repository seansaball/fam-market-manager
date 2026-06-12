"""EP-series invariants + G3 double-count guard (ENH-002 P5,
matrix rows 9–10; docs/SYSTEM_INVARIANTS.md Layer 10).

EP1/EP2 via the coherence auditor (clean state passes, seeded
violations are caught); EP4 structurally (external entries cannot
consume customer match cap or generate rewards); G3 via the entry
screen's booth-activity review prompt.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.models.fmnp import create_fmnp_entry


@pytest.fixture
def world(tmp_path):
    db_file = str(tmp_path / "test_external_invariants.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit)"
        " VALUES (10, 'Bethel Park', 10000)")
    conn.execute(
        "INSERT INTO vendors (id, name, is_active)"
        " VALUES (20, 'Pitaland Inc.', 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status,"
        " opened_by) VALUES (41, 10, '2099-05-06', 'Open', 'T')")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order, denomination,"
        " external_matching_accepted, vendor_cashes_original)"
        " VALUES (2, 'FMNP', 100.0, 0, 2, 500, 1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order, denomination,"
        " external_matching_accepted, vendor_cashes_original)"
        " VALUES (3, 'Food RX', 100.0, 1, 3, 1000, 1, 0)")
    conn.commit()
    yield conn
    close_connection()


# ──────────────────────────────────────────────────────────────────
# EP auditor
# ──────────────────────────────────────────────────────────────────


class TestExternalAuditor:

    def test_clean_state_passes(self, world):
        from tests._coherence import audit_external_entries
        create_fmnp_entry(41, 20, 1000, 'C')                       # FMNP
        create_fmnp_entry(41, 20, 2000, 'C', payment_method_id=3)  # RX
        report = audit_external_entries(world, 41)
        assert report.ok, report.failures

    def test_ep2_catches_non_multiple_amount(self, world):
        """A face value that isn't a whole multiple of the entry's
        own denomination snapshot is flagged.  (Can only arise via
        direct DB writes — the screen's G2 guard blocks it — which
        is exactly what an auditor is for.)"""
        from tests._coherence import audit_external_entries
        eid = create_fmnp_entry(41, 20, 2000, 'C',
                                payment_method_id=3)
        # Bypass the UI: corrupt the amount (immutability trigger
        # only watches snapshot columns; amount is editable).
        world.execute(
            "UPDATE fmnp_entries SET amount = 1500 WHERE id = ?",
            (eid,))
        world.commit()
        report = audit_external_entries(world, 41)
        assert not report.ok
        assert any(f.invariant_id == 'EP2' for f in report.failures)

    def test_ep2_exempts_null_denomination_snapshot(self, world):
        """Pre-v38 backfills carry NULL denomination snapshots —
        the historical denomination is unknowable, so EP2 skips.
        (Built by direct INSERT: the immutability trigger rightly
        refuses to CLEAR a snapshot on an existing row.)"""
        from tests._coherence import audit_external_entries
        world.execute(
            "INSERT INTO fmnp_entries (market_day_id, vendor_id,"
            " amount, entered_by, payment_method_id,"
            " method_name_snapshot, match_percent_snapshot,"
            " vendor_cashes_original_snapshot, denomination_snapshot)"
            " VALUES (41, 20, 1500, 'C', 3, 'Food RX', 100.0, 0,"
            " NULL)")
        world.commit()
        report = audit_external_entries(world, 41)
        assert report.ok, report.failures

    def test_post_confirm_auditor_handles_external_columns(
            self, world):
        """audit_post_confirm's R1/R5 must account for the new
        external columns adding into Total Due (EP3 delegation)."""
        from tests._coherence import audit_post_confirm
        create_fmnp_entry(41, 20, 2000, 'C', payment_method_id=3)
        report = audit_post_confirm(world, 41)
        assert report.ok, report.failures


# ──────────────────────────────────────────────────────────────────
# EP4 — structural: no cap consumption, no rewards
# ──────────────────────────────────────────────────────────────────


class TestEP4Structural:

    def test_external_entries_never_consume_match_cap(self, world):
        """get_customer_prior_match joins customer_orders →
        transactions → payment_line_items; fmnp_entries rows have
        no customer order and no line items, so cap consumption is
        structurally impossible.  Pin by behavior: a vendor-day
        full of external entries leaves every customer's prior
        match at zero."""
        from fam.models.customer_order import get_customer_prior_match
        create_fmnp_entry(41, 20, 2000, 'C', payment_method_id=3)
        create_fmnp_entry(41, 20, 1000, 'C')
        assert get_customer_prior_match('C-001', 41) == 0

    def test_external_entries_never_generate_rewards(self, world):
        """generated_rewards rows reference customer_order_id;
        external entries have none.  Pin by behavior: creating
        entries writes zero reward rows."""
        create_fmnp_entry(41, 20, 2000, 'C', payment_method_id=3)
        count = world.execute(
            "SELECT COUNT(*) FROM generated_rewards").fetchone()[0]
        assert count == 0


# ──────────────────────────────────────────────────────────────────
# G3 — booth + external double-count review prompt
# ──────────────────────────────────────────────────────────────────


def _add_booth_rx_txn(conn):
    """A confirmed booth transaction paying $10 Food RX at vendor 20."""
    conn.execute(
        "INSERT INTO customer_orders (id, market_day_id,"
        " customer_label, status)"
        " VALUES (1, 41, 'C-001', 'Confirmed')")
    conn.execute(
        "INSERT INTO transactions (id, fam_transaction_id,"
        " market_day_id, vendor_id, receipt_total, status,"
        " customer_order_id)"
        " VALUES (1, 'FAM-G3-1', 41, 20, 2000, 'Confirmed', 1)")
    conn.execute(
        "INSERT INTO payment_line_items (transaction_id,"
        " payment_method_id, method_name_snapshot,"
        " match_percent_snapshot, method_amount, match_amount,"
        " customer_charged)"
        " VALUES (1, 3, 'Food RX', 100.0, 2000, 1000, 1000)")
    conn.commit()


class TestG3DoubleCountGuard:

    def _screen(self, qtbot):
        from fam.ui.fmnp_screen import FMNPScreen
        screen = FMNPScreen()
        qtbot.addWidget(screen)
        for i in range(screen.md_combo.count()):
            if screen.md_combo.itemData(i) == 41:
                screen.md_combo.setCurrentIndex(i)
                break
        idx = screen.method_combo.findData(3)
        screen.method_combo.setCurrentIndex(idx)
        screen.amount_spin.setValue(20.00)
        screen.entered_by_input.setText('Coordinator')
        return screen

    def test_review_prompt_fires_on_booth_activity(
            self, qtbot, world, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        _add_booth_rx_txn(world)
        prompts = []
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda parent, title, text, *a, **kw:
                         prompts.append((title, text))
                         or QMessageBox.StandardButton.Yes))
        screen = self._screen(qtbot)
        screen._save_entry()
        # First prompt is the G3 review, then the G1 confirmation.
        assert len(prompts) == 2
        g3_title, g3_text = prompts[0]
        assert 'Booth Activity' in g3_title
        assert '$10.00' in g3_text          # booth Food RX total
        assert 'TWICE' in g3_text
        # Entry was saved after Yes + Yes.
        assert world.execute(
            "SELECT COUNT(*) FROM fmnp_entries").fetchone()[0] == 1

    def test_no_backs_out_without_saving(self, qtbot, world,
                                         monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        _add_booth_rx_txn(world)
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda *a, **kw:
                         QMessageBox.StandardButton.No))
        screen = self._screen(qtbot)
        screen._save_entry()
        assert world.execute(
            "SELECT COUNT(*) FROM fmnp_entries").fetchone()[0] == 0

    def test_silent_without_booth_activity(self, qtbot, world,
                                           monkeypatch):
        """No booth activity for the (vendor, method, day) → only
        the G1 confirmation fires."""
        from PySide6.QtWidgets import QMessageBox
        prompts = []
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda parent, title, text, *a, **kw:
                         prompts.append(title)
                         or QMessageBox.StandardButton.Yes))
        screen = self._screen(qtbot)
        screen._save_entry()
        assert len(prompts) == 1
        assert 'Booth Activity' not in prompts[0]

    def test_voided_booth_txns_do_not_trigger(self, qtbot, world,
                                              monkeypatch):
        """ACTIVE_TX_STATUSES discipline: a voided booth txn is not
        booth activity."""
        from PySide6.QtWidgets import QMessageBox
        _add_booth_rx_txn(world)
        world.execute(
            "UPDATE transactions SET status='Voided' WHERE id=1")
        world.commit()
        prompts = []
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda parent, title, text, *a, **kw:
                         prompts.append(title)
                         or QMessageBox.StandardButton.Yes))
        screen = self._screen(qtbot)
        screen._save_entry()
        assert all('Booth Activity' not in t for t in prompts)

    def test_different_method_does_not_trigger(self, qtbot, world,
                                               monkeypatch):
        """Booth FMNP activity must not flag a Food RX external
        entry — the guard keys on the METHOD."""
        from PySide6.QtWidgets import QMessageBox
        world.execute(
            "INSERT INTO customer_orders (id, market_day_id,"
            " customer_label, status)"
            " VALUES (1, 41, 'C-001', 'Confirmed')")
        world.execute(
            "INSERT INTO transactions (id, fam_transaction_id,"
            " market_day_id, vendor_id, receipt_total, status,"
            " customer_order_id)"
            " VALUES (1, 'FAM-G3-2', 41, 20, 1000, 'Confirmed', 1)")
        world.execute(
            "INSERT INTO payment_line_items (transaction_id,"
            " payment_method_id, method_name_snapshot,"
            " match_percent_snapshot, method_amount, match_amount,"
            " customer_charged)"
            " VALUES (1, 2, 'FMNP', 100.0, 1000, 500, 500)")
        world.commit()
        prompts = []
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda parent, title, text, *a, **kw:
                         prompts.append(title)
                         or QMessageBox.StandardButton.Yes))
        screen = self._screen(qtbot)   # Food RX selected
        screen._save_entry()
        assert all('Booth Activity' not in t for t in prompts)
