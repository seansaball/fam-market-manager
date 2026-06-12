"""Model-layer tests for external payment entries (ENH-002, matrix row 3).

Pins the snapshot discipline at its single enforcement point:
``create_fmnp_entry`` resolves the method row and writes the four
config snapshots itself, inside the same transaction.  A later
settings change must never re-value an existing entry; payout is
always derived from the row's snapshots.

Also pins:
  * ``payment_method_id=None`` back-compat (resolves FMNP — every
    pre-v2.1.0 caller gets the historical behavior, including the
    byte-identical audit-note text)
  * the G4 model-layer guard in ``update_payment_method``
    (transition-scoped: blocks PRODUCING external-without-denom,
    never fires on the standing state)
  * ``get_external_payment_methods`` semantics (ignores is_active,
    excludes system methods)
  * ``get_fmnp_entries(payment_method_id=...)`` routing filter
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.models.fmnp import (
    create_fmnp_entry, get_fmnp_entries, update_fmnp_entry,
)
from fam.models.payment_method import (
    get_external_payment_methods, update_payment_method,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_external_model.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    # Minimal world: market, vendor, one closed market day, and the
    # three external-relevant methods with explicit ids.
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit)"
        " VALUES (10, 'Bethel Park', 10000)")
    conn.execute(
        "INSERT INTO vendors (id, name, is_active)"
        " VALUES (20, 'Pitaland Inc.', 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status,"
        " opened_by, closed_by, closed_at)"
        " VALUES (40, 10, '2026-04-26', 'Closed', 'T', 'T',"
        " '2026-04-26 16:00:00')")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order, denomination, photo_required,"
        " external_matching_accepted, vendor_cashes_original)"
        " VALUES (2, 'FMNP', 100.0, 0, 2, 500, 'Optional', 1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order, denomination,"
        " external_matching_accepted, vendor_cashes_original)"
        " VALUES (3, 'Food RX', 100.0, 1, 3, 1000, 1, 0)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order, denomination,"
        " external_matching_accepted, vendor_cashes_original)"
        " VALUES (4, 'JH Food Bucks', 0.0, 1, 4, 200, 1, 0)")
    conn.commit()
    yield conn
    close_connection()


def _entry(conn, entry_id):
    return dict(conn.execute(
        "SELECT * FROM fmnp_entries WHERE id = ?", (entry_id,)
    ).fetchone())


# ──────────────────────────────────────────────────────────────────
# create_fmnp_entry — snapshots + back-compat
# ──────────────────────────────────────────────────────────────────


class TestCreateSnapshots:

    def test_default_method_is_fmnp_with_snapshots(self, fresh_db):
        eid = create_fmnp_entry(40, 20, 1000, 'Coordinator',
                                check_count=2)
        row = _entry(fresh_db, eid)
        assert row['payment_method_id'] == 2
        assert row['method_name_snapshot'] == 'FMNP'
        assert row['match_percent_snapshot'] == 100.0
        assert row['vendor_cashes_original_snapshot'] == 1
        assert row['denomination_snapshot'] == 500

    def test_fmnp_audit_note_keeps_historical_shape(self, fresh_db):
        """Pre-v2.1.0 audit text, byte-identical: the FMNP audit
        trail must not change shape under the generalization."""
        eid = create_fmnp_entry(40, 20, 1000, 'Coordinator',
                                check_count=2)
        note = fresh_db.execute(
            "SELECT notes FROM audit_log WHERE table_name="
            "'fmnp_entries' AND record_id=? AND action='INSERT'",
            (eid,)).fetchone()['notes']
        assert note == ("FMNP entry created: vendor_id=20, "
                        "amount=$10.00, check_count=2")

    def test_explicit_method_snapshots_food_rx(self, fresh_db):
        eid = create_fmnp_entry(40, 20, 1000, 'Coordinator',
                                payment_method_id=3)
        row = _entry(fresh_db, eid)
        assert row['payment_method_id'] == 3
        assert row['method_name_snapshot'] == 'Food RX'
        assert row['match_percent_snapshot'] == 100.0
        assert row['vendor_cashes_original_snapshot'] == 0
        assert row['denomination_snapshot'] == 1000

    def test_external_audit_note_names_method_and_payout(
            self, fresh_db):
        """Non-FMNP entries get the generalized audit text including
        the FAM-owed amount — the G1 money trail in the audit log."""
        eid = create_fmnp_entry(40, 20, 1000, 'Coordinator',
                                payment_method_id=3)
        note = fresh_db.execute(
            "SELECT notes FROM audit_log WHERE table_name="
            "'fmnp_entries' AND record_id=? AND action='INSERT'",
            (eid,)).fetchone()['notes']
        assert note.startswith("External entry created (Food RX):")
        assert "FAM owes=$20.00" in note

    def test_food_bucks_face_only_payout_in_audit(self, fresh_db):
        eid = create_fmnp_entry(40, 20, 1000, 'Coordinator',
                                payment_method_id=4)
        note = fresh_db.execute(
            "SELECT notes FROM audit_log WHERE record_id=?"
            " AND action='INSERT'", (eid,)).fetchone()['notes']
        assert "FAM owes=$10.00" in note

    def test_unknown_method_raises(self, fresh_db):
        with pytest.raises(ValueError, match='not found'):
            create_fmnp_entry(40, 20, 1000, 'Coordinator',
                              payment_method_id=999)


# ──────────────────────────────────────────────────────────────────
# Snapshot discipline — settings changes never re-value entries
# ──────────────────────────────────────────────────────────────────


class TestSnapshotDiscipline:

    def test_settings_change_does_not_revalue_entry(self, fresh_db):
        from fam.utils.external_payout import (
            compute_external_payout_cents)

        eid = create_fmnp_entry(40, 20, 1000, 'Coordinator',
                                payment_method_id=3)
        before = _entry(fresh_db, eid)
        payout_before = compute_external_payout_cents(
            before['amount'], before['match_percent_snapshot'],
            bool(before['vendor_cashes_original_snapshot']))
        assert payout_before == 2000

        # Coordinator later halves the Food RX match and flips
        # cashes-original — the historical entry must not move.
        update_payment_method(3, match_percent=50.0,
                              vendor_cashes_original=True)

        after = _entry(fresh_db, eid)
        assert after['match_percent_snapshot'] == 100.0
        assert after['vendor_cashes_original_snapshot'] == 0
        payout_after = compute_external_payout_cents(
            after['amount'], after['match_percent_snapshot'],
            bool(after['vendor_cashes_original_snapshot']))
        assert payout_after == 2000

        # A NEW entry under the corrected settings snapshots the
        # new config — both rows visible side by side by design.
        eid2 = create_fmnp_entry(40, 20, 1000, 'Coordinator',
                                 payment_method_id=3)
        row2 = _entry(fresh_db, eid2)
        assert row2['match_percent_snapshot'] == 50.0
        assert row2['vendor_cashes_original_snapshot'] == 1

    def test_amount_edit_keeps_snapshots(self, fresh_db):
        """The supported edit surface (amount/vendor/notes) must not
        touch snapshots — and must not trip the immutability
        trigger."""
        eid = create_fmnp_entry(40, 20, 1000, 'Coordinator',
                                payment_method_id=3)
        update_fmnp_entry(eid, amount=2000, changed_by='Coordinator')
        row = _entry(fresh_db, eid)
        assert row['amount'] == 2000
        assert row['match_percent_snapshot'] == 100.0
        assert row['payment_method_id'] == 3


# ──────────────────────────────────────────────────────────────────
# get_fmnp_entries routing filter
# ──────────────────────────────────────────────────────────────────


class TestMethodFilter:

    def test_filter_by_method(self, fresh_db):
        e_fmnp = create_fmnp_entry(40, 20, 500, 'C')
        e_rx = create_fmnp_entry(40, 20, 1000, 'C',
                                 payment_method_id=3)

        fmnp_rows = get_fmnp_entries(market_day_id=40,
                                     payment_method_id=2)
        rx_rows = get_fmnp_entries(market_day_id=40,
                                   payment_method_id=3)
        all_rows = get_fmnp_entries(market_day_id=40)

        assert [r['id'] for r in fmnp_rows] == [e_fmnp]
        assert [r['id'] for r in rx_rows] == [e_rx]
        assert {r['id'] for r in all_rows} == {e_fmnp, e_rx}


# ──────────────────────────────────────────────────────────────────
# update_payment_method G4 guard + get_external_payment_methods
# ──────────────────────────────────────────────────────────────────


class TestG4ModelGuard:

    def test_enabling_external_without_denomination_raises(
            self, fresh_db):
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent,"
            " is_active, sort_order) VALUES (5, 'JH Tokens', 100.0,"
            " 1, 5)")
        fresh_db.commit()
        with pytest.raises(ValueError, match='denomination'):
            update_payment_method(5, external_matching_accepted=True)

    def test_enabling_external_with_denomination_in_same_call_ok(
            self, fresh_db):
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent,"
            " is_active, sort_order) VALUES (5, 'JH Tokens', 100.0,"
            " 1, 5)")
        fresh_db.commit()
        update_payment_method(5, denomination=100,
                              external_matching_accepted=True)
        row = fresh_db.execute(
            "SELECT * FROM payment_methods WHERE id=5").fetchone()
        assert row['external_matching_accepted'] == 1
        assert row['denomination'] == 100

    def test_clearing_denomination_while_external_raises(
            self, fresh_db):
        with pytest.raises(ValueError, match='denomination'):
            update_payment_method(3, denomination=0)

    def test_unrelated_update_on_grandfathered_row_ok(self, fresh_db):
        """A pre-v38 backfill can leave FMNP external-enabled with
        no denomination (legacy v7→v8 DBs).  The guard is
        transition-scoped: unrelated edits must not raise on the
        standing state."""
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent,"
            " is_active, sort_order, external_matching_accepted,"
            " vendor_cashes_original)"
            " VALUES (6, 'Legacy Checks', 100.0, 1, 6, 1, 1)")
        fresh_db.commit()
        update_payment_method(6, sort_order=7)  # must not raise
        row = fresh_db.execute(
            "SELECT sort_order FROM payment_methods WHERE id=6"
        ).fetchone()
        assert row['sort_order'] == 7

    def test_toggles_audited_per_field(self, fresh_db):
        update_payment_method(4, vendor_cashes_original=True,
                              changed_by='Sean')
        audit = fresh_db.execute(
            "SELECT field_name, new_value FROM audit_log"
            " WHERE table_name='payment_methods' AND record_id=4"
            " AND action='UPDATE'").fetchall()
        assert ('vendor_cashes_original', '1') in [
            (r['field_name'], str(r['new_value'])) for r in audit]


class TestGetExternalPaymentMethods:

    def test_returns_external_enabled_ordered(self, fresh_db):
        names = [m['name'] for m in get_external_payment_methods()]
        assert names == ['FMNP', 'Food RX', 'JH Food Bucks']

    def test_ignores_is_active(self, fresh_db):
        """FMNP ships is_active=0 with a fully functional entry
        screen — the external portal must not filter on the booth
        flag."""
        assert any(m['name'] == 'FMNP' and m['is_active'] == 0
                   for m in get_external_payment_methods())

    def test_excludes_system_methods(self, fresh_db):
        from fam.models.payment_method import UNALLOCATED_FUNDS_NAME
        fresh_db.execute(
            "UPDATE payment_methods SET external_matching_accepted=1"
            f" WHERE name = '{UNALLOCATED_FUNDS_NAME}'")
        fresh_db.commit()
        assert all(m['name'] != UNALLOCATED_FUNDS_NAME
                   for m in get_external_payment_methods())
