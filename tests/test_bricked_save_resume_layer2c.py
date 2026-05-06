"""Regression: bricked-transaction scenario must round-trip cleanly
through save-as-draft → resume → confirm without Layer 2C
under-allocation errors.

User-reported (2026-04-30 onsite, follow-up to the bricked-txn fix):

    "heres the new error on the same bricked transaction after I
     resume from draft"
    [screenshot: Layer 2C error]

      "Under-allocation on Elfinwild Farms's receipt: only $10.48
       is being applied to a $11.11 receipt.  Add more payment to
       cover the gap or use Auto-Distribute."

Root cause
----------
After the engine cap-fallback was fixed to keep denom customer
fixed (and inflate non-denom method to absorb the denom-method
shrinkage), there was still a missing piece: ``_collect_line_items``
caps each non-denom row's method_amount at the pre-engine value
(``effective_total − running_alloc``).  When the engine inflates
that method downstream, the post-engine ``result['line_items'][i]``
disagrees with ``items[i]`` for non-denom rows.

``_apply_denomination_forfeit`` already syncs items for DENOM rows
(Pass 1, 2, 3 update both ``method_amount`` and ``match_amount``)
and partially for non-denom rows (Pass 4 syncs match + customer
only — not method).  So after engine + forfeit, ``items[snap]``
had:

    method = $66.66       ← stale, from _collect_line_items pre-engine cap
    match  = $7.56        ← from forfeit Pass 4 give-back
    customer = $68.41     ← from forfeit Pass 4 give-back

``customer + match = $75.97 ≠ method = $66.66``.

Layer 2C's per-vendor distribution then used the stale ``$66.66``
SNAP method, distributed it across the four vendors weighted by
their (correct) ``per_txn_remaining`` totals (which sum to
$75.97), and undershot every vendor by the proportional share of
the missing $9.31.  Elfinwild's share of the under-allocation
was $0.63 — the user's screenshot value.

Same bug affected ``_save_draft``: it never ran the engine before
``_distribute_and_save_payments``, so the save path's own cap
fallback (which has the same proportional-reduction-inflates-denom-
customer bug the engine fix solved) ran on raw items.  Saved DB
rows had inflated denom customer values that loaded back wrong on
resume.

Fix
---
1. New helper ``PaymentScreen._resolve_engine_state(items)`` runs
   the engine + denomination-forfeit and syncs the post-cap-aware
   values back onto each item dict (``method_amount``,
   ``match_amount``, ``customer_charged`` all consistent).

2. ``_save_draft`` now calls ``_resolve_engine_state`` before
   ``_distribute_and_save_payments`` so saved DB rows reflect the
   final cap-aware state — denom customer at unit_count × denom,
   non-denom absorbing the cap-driven method inflation, per-vendor
   reconciliation exact.

3. ``_confirm_payment`` syncs items from ``result.line_items``
   after engine + forfeit so Layer 2C and the save path see the
   engine's final method values.

This test pins the round-trip contract: the user's exact bricked
scenario must save as draft, resume, and pass Layer 2C without
under-allocation errors on any vendor.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def bricked_resume_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "bricked_resume.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    for vid, name in [(1, 'Elfinwild'), (2, 'Fungetarian'),
                       (3, 'Hughes'), (4, 'Pond Hill')]:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))
    for mid, name, pct, denom in [
            (1, 'SNAP', 100.0, None),
            (3, 'Food RX', 100.0, 1000),
            (4, 'JH Food Bucks', 100.0, 200)]:
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            " denomination, sort_order, is_active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (mid, name, pct, denom, mid))
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, "
            " payment_method_id) VALUES (1, ?)", (mid,))
    for vid in (1, 2, 3, 4):
        for mid in (1, 3, 4):
            conn.execute(
                "INSERT INTO vendor_payment_methods "
                "(vendor_id, payment_method_id) VALUES (?, ?)",
                (vid, mid))
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-30', 'Open', 'T')")
    from fam.models.customer_order import (
        create_customer_order, update_customer_order_status,
    )
    from fam.models.transaction import (
        create_transaction, confirm_transaction,
        save_payment_line_items,
    )
    # Prior order: $83.31 of match consumed.
    prior_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-001-LB1',
        zip_code='15102')
    pt_id, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=16662,
        customer_order_id=prior_id,
        market_day_date='2026-04-30')
    save_payment_line_items(pt_id, [
        {'payment_method_id': 1,
         'method_name_snapshot': 'SNAP',
         'match_percent_snapshot': 100.0,
         'method_amount': 16662, 'match_amount': 8331,
         'customer_charged': 8331,
         'photo_path': None, 'photo_source_paths': []}])
    confirm_transaction(pt_id, confirmed_by='T')
    update_customer_order_status(prior_id, 'Confirmed')

    # The new bricked-scenario order under test.
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-001-LB1',
        zip_code='15102')
    for vid, receipt in [(1, 1111), (2, 2222),
                          (3, 3333), (4, 4444)]:
        create_transaction(
            market_day_id=1, vendor_id=vid,
            receipt_total=receipt,
            customer_order_id=order_id,
            market_day_date='2026-04-30')
    conn.commit()
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, 'question',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.No))
    yield conn, order_id
    close_connection()


def _add(screen, method_sub, charge=0, vid=None):
    row = screen._add_payment_row()
    combo = row.method_combo
    for i in range(combo.count()):
        if method_sub.lower() in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            break
    if vid is not None:
        row.set_bound_vendor_id(vid)
    if charge > 0:
        row._set_active_charge(charge)
    return row


class TestSaveDraftPreservesDenomCustomer:
    """Saved DB rows after save-as-draft on the bricked-transaction
    scenario must have:
      - Per-line invariant (customer + match = method) on every row
      - Denom customer = unit_count × denomination (preserved)
      - Per-vendor sum of method = vendor's receipt (exact)
      - Total match ≤ remaining cap"""

    def test_saved_rows_satisfy_per_vendor_reconciliation(
            self, qtbot, bricked_resume_db):
        from fam.ui.payment_screen import PaymentScreen

        conn, order_id = bricked_resume_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add(screen, 'Food Bucks', 600, vid=1)
        _add(screen, 'Food RX', 2000, vid=3)
        screen._auto_distribute()
        screen._save_draft()

        rows = conn.execute("""
            SELECT t.vendor_id, pli.method_name_snapshot,
                   pli.method_amount, pli.match_amount,
                   pli.customer_charged
            FROM payment_line_items pli
            JOIN transactions t ON pli.transaction_id = t.id
            WHERE t.customer_order_id=?
            ORDER BY t.vendor_id, pli.method_name_snapshot
        """, (order_id,)).fetchall()

        # Per-line invariant.
        cols = ['vendor', 'method', 'method_amount',
                'match_amount', 'customer_charged']
        for r in rows:
            invariant = r[3] + r[4]
            row_dict = dict(zip(cols, r))
            assert invariant == r[2], (
                f"Per-line invariant violated on saved row "
                f"{row_dict}: customer + match = {invariant} != "
                f"method = {r[2]}")

        # Denom customer fixed at unit_count × denom.
        fb_rows = [r for r in rows if r[1] == 'JH Food Bucks']
        assert sum(r[4] for r in fb_rows) == 600, (
            f"FB total customer must = $6.00 (3 × $2 tokens), got "
            f"${sum(r[4] for r in fb_rows)/100:.2f}.  Pre-fix the "
            f"save path's cap fallback inflated this to $10.31 via "
            f"the same proportional-reduction bug the engine fix "
            f"solved.")
        food_rx_rows = [r for r in rows if r[1] == 'Food RX']
        assert sum(r[4] for r in food_rx_rows) == 2000, (
            f"Food RX total customer must = $20.00 (2 × $10 "
            f"checks), got "
            f"${sum(r[4] for r in food_rx_rows)/100:.2f}")

        # Per-vendor reconciliation: each vendor's allocated must
        # equal their receipt exactly.
        for vid in (1, 2, 3, 4):
            receipt = conn.execute(
                "SELECT receipt_total FROM transactions "
                " WHERE vendor_id=? AND customer_order_id=?",
                (vid, order_id)).fetchone()[0]
            alloc = sum(r[2] for r in rows if r[0] == vid)
            assert abs(alloc - receipt) <= 1, (
                f"Vendor {vid} allocation off from receipt: "
                f"alloc={alloc}c, receipt={receipt}c, "
                f"diff={alloc-receipt}c")

        # Total match must ≤ remaining cap ($16.69 = $100 daily −
        # $83.31 prior).
        total_match = sum(r[3] for r in rows)
        assert total_match <= 1669, (
            f"Total match {total_match}c exceeded remaining cap of "
            f"1669c ($16.69)")


class TestResumeFromBrickedDraftPassesLayer2C:
    """Resuming the saved bricked-transaction draft must produce a
    state where Layer 2C per-vendor reconciliation passes — the
    user can confirm without "Under-allocation on X" errors."""

    def test_resume_state_satisfies_layer_2c(
            self, qtbot, bricked_resume_db):
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import calculate_payment_breakdown

        conn, order_id = bricked_resume_db

        # Save the draft.
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)
        _add(screen, 'Food Bucks', 600, vid=1)
        _add(screen, 'Food RX', 2000, vid=3)
        screen._auto_distribute()
        screen._save_draft()

        # Resume on a fresh PaymentScreen.
        screen2 = PaymentScreen()
        qtbot.addWidget(screen2)
        screen2.load_customer_order(order_id)

        # Run the same engine + forfeit + Layer 2C check
        # ``_confirm_payment`` runs.
        items = screen2._collect_line_items()
        result = screen2._resolve_engine_state(items)
        assert result is not None

        # Now items has post-engine, post-forfeit values.  Verify
        # per-vendor reconciliation by mirroring Layer 2C's logic.
        per_txn_alloc = {
            t['id']: 0 for t in screen2._order_transactions}
        vendor_to_txn_id = {}
        for t in screen2._order_transactions:
            vid = t.get('vendor_id')
            if vid is not None and vid not in vendor_to_txn_id:
                vendor_to_txn_id[vid] = t['id']

        # Phase 1: denom committed to bound vendor.
        for item in items:
            denom = item.get('denomination')
            if not (denom and denom > 0):
                continue
            bound_vid = item.get('bound_vendor_id')
            target_id = (vendor_to_txn_id.get(bound_vid)
                         if bound_vid is not None else None)
            if target_id is not None:
                per_txn_alloc[target_id] += item['method_amount']

        # Phase 2: non-denom proportional to remaining.
        for item in items:
            denom = item.get('denomination')
            if denom and denom > 0:
                continue
            ma_total = item['method_amount']
            per_txn_remaining = []
            total_remaining = 0
            for t in screen2._order_transactions:
                left = max(0, t['receipt_total']
                           - per_txn_alloc[t['id']])
                per_txn_remaining.append(left)
                total_remaining += left
            if total_remaining <= 0:
                continue
            running = 0
            last_idx = len(screen2._order_transactions) - 1
            for t_idx, t in enumerate(screen2._order_transactions):
                if t_idx == last_idx:
                    share = ma_total - running
                else:
                    weight = (per_txn_remaining[t_idx]
                              / total_remaining
                              if total_remaining > 0 else 0)
                    share = round(ma_total * weight)
                    running += share
                per_txn_alloc[t['id']] += share

        # Layer 2C: every vendor must reconcile to its receipt
        # within ±1¢.  Pre-fix the user got "Elfinwild only $10.48
        # being applied to a $11.11 receipt".
        for t in screen2._order_transactions:
            allocated = per_txn_alloc[t['id']]
            receipt = t['receipt_total']
            assert abs(allocated - receipt) <= 1, (
                f"Layer 2C would block confirm: vendor "
                f"{t.get('vendor_name', t['vendor_id'])} "
                f"alloc=${allocated/100:.2f} != "
                f"receipt=${receipt/100:.2f} "
                f"(diff=${(allocated-receipt)/100:.2f}).  This is "
                f"the 2026-04-30 onsite Layer 2C-on-resume bug.")

    def test_round_trip_idempotent(self, qtbot, bricked_resume_db):
        """Save-resume-save again must produce the same DB state.
        Pre-fix, repeated save rounds drifted the customer/match
        values further on each cycle as the buggy cap-fallback
        compounded its mistakes."""
        from fam.ui.payment_screen import PaymentScreen

        conn, order_id = bricked_resume_db

        # First save.
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)
        _add(screen, 'Food Bucks', 600, vid=1)
        _add(screen, 'Food RX', 2000, vid=3)
        screen._auto_distribute()
        screen._save_draft()

        rows_a = conn.execute("""
            SELECT t.vendor_id, pli.method_name_snapshot,
                   pli.method_amount, pli.match_amount,
                   pli.customer_charged
            FROM payment_line_items pli
            JOIN transactions t ON pli.transaction_id = t.id
            WHERE t.customer_order_id=?
            ORDER BY t.vendor_id, pli.method_name_snapshot
        """, (order_id,)).fetchall()

        # Resume + save again (no manual edits).
        screen2 = PaymentScreen()
        qtbot.addWidget(screen2)
        screen2.load_customer_order(order_id)
        screen2._save_draft()

        rows_b = conn.execute("""
            SELECT t.vendor_id, pli.method_name_snapshot,
                   pli.method_amount, pli.match_amount,
                   pli.customer_charged
            FROM payment_line_items pli
            JOIN transactions t ON pli.transaction_id = t.id
            WHERE t.customer_order_id=?
            ORDER BY t.vendor_id, pli.method_name_snapshot
        """, (order_id,)).fetchall()

        # Compare row-by-row.
        assert len(rows_a) == len(rows_b), (
            f"Round-trip changed row count: "
            f"first save {len(rows_a)} rows, second save "
            f"{len(rows_b)} rows")
        for a, b in zip(rows_a, rows_b):
            assert tuple(a) == tuple(b), (
                f"Round-trip drift detected.  First save: {tuple(a)}, "
                f"second save: {tuple(b)}.  Save+resume+save must "
                f"be idempotent.")
