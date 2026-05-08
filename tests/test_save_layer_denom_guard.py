"""Save-layer guard refuses to persist misaligned denom rows
(v2.0.7 fix, user-reported 2026-05-06 reproducer).

User reproducer: dropped a Jill's gourmet dips order's receipt
total down via the Adjustment dialog.  Resulting Detailed Ledger
row showed "JH Food Bucks: $0.73, SNAP: $0.72" against a $1.45
receipt — Food Bucks is a $2-denomination method, so $0.73 is
impossible (the customer can't physically hand over fractional
tokens).

The engine's ``resolve_payment_state`` snap-back catches most
cases, but the user's specific path (likely involving multiple
adjustment cycles, daily match cap, or Phase B customer-side
forfeit) bypassed it.  This save-layer guard is the absolute
last line of defense: any misaligned denom row gets snapped at
the write boundary, with a logger.warning so future occurrences
are diagnosable from fam_manager.log.

The DB ``chk_pli_invariant_*`` trigger only enforces
``customer_charged + match_amount = method_amount`` — it does
NOT enforce denomination alignment, by design (the trigger has
no per-method-denomination knowledge).  This guard fills that gap
at the model layer.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_save_guard.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield get_connection()
    close_connection()


def _seed_jills_with_fb(conn):
    """Set up the user's reproducer shape: Jill's vendor + Food
    Bucks ($2 denom, 100% match) + SNAP (non-denom, 100% match)."""
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 10000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES "
        "(1, \"Jill's gourmet dips\")")
    conn.execute(
        "INSERT INTO payment_methods "
        "(id, name, match_percent, is_active, sort_order, "
        " denomination) VALUES "
        "(1, 'JH Food Bucks', 100.0, 1, 1, 200), "
        "(2, 'SNAP', 100.0, 1, 2, NULL)")
    conn.execute(
        "INSERT INTO market_days "
        "(id, market_id, date, status, opened_by) VALUES "
        "(1, 1, '2026-05-06', 'Open', 'T')")
    conn.execute(
        "INSERT INTO transactions "
        "(id, fam_transaction_id, market_day_id, vendor_id, "
        " receipt_total, status, created_at) VALUES "
        "(100, 'FAM-T-100', 1, 1, 145, 'Confirmed', "
        " '2026-05-06 12:00:00')")
    conn.commit()


# ──────────────────────────────────────────────────────────────────
# Save-layer guard tests
# ──────────────────────────────────────────────────────────────────


class TestSaveLayerSnapsMisalignedDenom:
    """Whatever state callers pass in, the save layer must produce
    aligned rows in the DB.  Last line of defense."""

    def test_misaligned_jh_food_bucks_is_snapped(self, fresh_db):
        """The exact user-reported shape: a JH Food Bucks row
        with method_amount=73 (not a $2 multiple) gets snapped
        DOWN at save."""
        from fam.models.transaction import save_payment_line_items

        _seed_jills_with_fb(fresh_db)

        # Pathological input — what the engine would have to produce
        # (or be passed) for the bug to manifest pre-fix:
        # customer_charged=48, match_amount=25, method_amount=73.
        # 48 % 200 != 0 — misaligned.
        line_items = [{
            'payment_method_id': 1,
            'method_name_snapshot': 'JH Food Bucks',
            'match_percent_snapshot': 100.0,
            'method_amount': 73,
            'customer_charged': 48,
            'match_amount': 25,
        }]

        save_payment_line_items(100, line_items)

        # Read back what was actually written
        row = fresh_db.execute(
            "SELECT customer_charged, match_amount, method_amount "
            "FROM payment_line_items WHERE transaction_id=100"
        ).fetchone()
        assert row is not None

        assert row['customer_charged'] % 200 == 0, (
            f"Save layer must snap misaligned denom row.  "
            f"customer_charged={row['customer_charged']!r}, "
            f"expected multiple of 200.")
        # Sum invariant preserved
        assert (row['customer_charged']
                + row['match_amount']
                == row['method_amount']), (
            "Sum invariant broken by save snap")
        # Specifically: 48 -> 0 (largest multiple of 200 ≤ 48).
        # Drift (48) goes into match.  method stays at 73.
        assert row['customer_charged'] == 0
        assert row['match_amount'] == 73
        assert row['method_amount'] == 73

    def test_aligned_row_passes_through_unchanged(self, fresh_db):
        """Aligned denom rows must NOT be modified by the guard
        — only misaligned rows are touched."""
        from fam.models.transaction import save_payment_line_items

        _seed_jills_with_fb(fresh_db)

        # Aligned: 1 token = $2, 100% match, full match → method=$4
        line_items = [{
            'payment_method_id': 1,
            'method_name_snapshot': 'JH Food Bucks',
            'match_percent_snapshot': 100.0,
            'method_amount': 400,
            'customer_charged': 200,
            'match_amount': 200,
        }]
        save_payment_line_items(100, line_items)

        row = fresh_db.execute(
            "SELECT customer_charged, match_amount, method_amount "
            "FROM payment_line_items WHERE transaction_id=100"
        ).fetchone()
        assert row['customer_charged'] == 200
        assert row['match_amount'] == 200
        assert row['method_amount'] == 400

    def test_non_denom_row_unaffected(self, fresh_db):
        """SNAP and other non-denom methods have no alignment
        constraint — the guard must skip them entirely."""
        from fam.models.transaction import save_payment_line_items

        _seed_jills_with_fb(fresh_db)

        line_items = [{
            'payment_method_id': 2,  # SNAP
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 73,
            'customer_charged': 37,
            'match_amount': 36,
        }]
        save_payment_line_items(100, line_items)

        row = fresh_db.execute(
            "SELECT customer_charged, match_amount, method_amount "
            "FROM payment_line_items WHERE transaction_id=100"
        ).fetchone()
        # SNAP has no denomination — values pass through verbatim.
        assert row['customer_charged'] == 37
        assert row['match_amount'] == 36
        assert row['method_amount'] == 73

    def test_aligned_via_phase_b_forfeit_passes_through(
            self, fresh_db):
        """Phase B forfeit (PaymentScreen._apply_denomination_forfeit)
        legitimately reduces customer_charged on a denom row when
        the customer's tokens overshoot the receipt.  The post-
        forfeit ``customer_charged`` is intentionally NOT a denom
        multiple (e.g. customer hands $2 Food Bucks to a $1.45
        receipt → customer_charged becomes $1.45, forfeit_cents=$0.55).

        v2.0.7 follow-up (user-reported 2026-05-07, schema v36):
        the save-layer snap-back now SKIPS rows where
        ``customer_forfeit_cents > 0``.  That field is set ONLY
        by Phase B and is the unambiguous "this sub-denomination
        customer_charged is intentional" signal.  Snapping it
        down to $0 corrupts the carefully-computed Phase B state
        (the customer's actual contribution gets dumped into FAM
        match, falsely showing reports as "FAM funded everything").

        New behavior: row passes through verbatim, with the
        forfeit invariant ``customer_charged + customer_forfeit_cents
        == N × denomination`` preserved on disk.  Reports surface
        the forfeit as a distinct "Customer Forfeit" column."""
        from fam.models.transaction import save_payment_line_items

        _seed_jills_with_fb(fresh_db)

        line_items = [{
            'payment_method_id': 1,
            'method_name_snapshot': 'JH Food Bucks',
            'match_percent_snapshot': 100.0,
            'method_amount': 145,
            'customer_charged': 145,  # Phase B output — NOT a $2 multiple
            'match_amount': 0,
            'customer_forfeit_cents': 55,  # 200 (token face) - 145 (effective)
        }]
        save_payment_line_items(100, line_items)

        row = fresh_db.execute(
            "SELECT customer_charged, match_amount, method_amount, "
            "customer_forfeit_cents "
            "FROM payment_line_items WHERE transaction_id=100"
        ).fetchone()
        # Phase B output preserved: customer_charged is intentionally
        # sub-denom; forfeit_cents records the lost token value.
        assert row['customer_charged'] == 145
        assert row['match_amount'] == 0
        assert row['method_amount'] == 145
        assert row['customer_forfeit_cents'] == 55
        # Forfeit invariant: cc + forfeit IS a multiple of denom
        # (= unit_count × denomination = 1 × $2 = $2).
        assert (row['customer_charged']
                + row['customer_forfeit_cents']) % 200 == 0


# ──────────────────────────────────────────────────────────────────
# Existing data is self-healed on next save
# ──────────────────────────────────────────────────────────────────


class TestExistingMisalignedDataSelfHeals:
    """The real-world scenario: misaligned data is already in the
    DB from a pre-fix code path.  When that data flows through any
    code path that re-saves the row (Adjustment, Confirm, etc.),
    the save layer guard snaps it back to alignment."""

    def test_save_replaces_old_misaligned_with_aligned(self, fresh_db):
        from fam.models.transaction import save_payment_line_items

        _seed_jills_with_fb(fresh_db)

        # Simulate pre-existing bad data in the DB by inserting
        # directly without the guard.  Schema's invariant trigger
        # accepts because the SUM is still valid.
        fresh_db.execute(
            "INSERT INTO payment_line_items "
            "(transaction_id, payment_method_id, "
            " method_name_snapshot, match_percent_snapshot, "
            " method_amount, match_amount, customer_charged) "
            "VALUES (100, 1, 'JH Food Bucks', 100.0, "
            "        73, 25, 48)")
        fresh_db.commit()

        # Sanity: confirm the bad data is in
        row = fresh_db.execute(
            "SELECT customer_charged FROM payment_line_items "
            "WHERE transaction_id=100").fetchone()
        assert row['customer_charged'] == 48

        # Now re-save (mimics what happens on the next adjustment)
        save_payment_line_items(100, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'JH Food Bucks',
            'match_percent_snapshot': 100.0,
            'method_amount': 73,
            'customer_charged': 48,
            'match_amount': 25,
        }])

        row = fresh_db.execute(
            "SELECT customer_charged, match_amount, method_amount "
            "FROM payment_line_items WHERE transaction_id=100"
        ).fetchone()
        assert row['customer_charged'] % 200 == 0, (
            "Re-save must self-heal pre-existing misaligned rows.")
