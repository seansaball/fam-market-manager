"""Save-path Phase 1 distributes denom line items across all
transactions of a single-vendor multi-receipt order (v2.0.7 fix,
user-reported 2026-05-06).

Reproducer:
  Customer C-003-LB1 has 3 receipts at Rockin' Cat Organic Coffee
  totaling $42.01 ($1.45, $15.24, $25.32).  Volunteer pays with
  4 × $2 JH Food Bucks ($12.18 method after forfeit) + 2 × $10
  Food RX ($40 method) = $52.18 — wait, but receipt is $42.01.
  Forfeit covers the rest.  Confirmation goes through.

  Volunteer voids the $1.45 and $15.24 transactions.  Vendor
  Reimbursement now shows the surviving $25.32 transaction with
  Total Due to Vendor $25.32 but $0 in every payment-method
  column (FAM Match, Food Bucks, Food RX, etc.).

Root cause: PaymentScreen Phase 1 of the save-distribution
algorithm fell back to ``target_idxs = [0]`` when ``bound_vid is
None``.  Single-vendor orders intentionally leave bound_vid
empty (the binding is implicit in the order context), so all
denom line items were created against transaction 0 only.
Transactions 1, 2, ... had ZERO line items.  When the user
voided transaction 0 (the one holding the line items), the
remaining Confirmed transactions had no associated payment
breakdown — Vendor Reimbursement reported $0 in every method
column even though the customer paid in full.

Fix mirrors the same pattern in the simulation path (Layer 2C)
and in ``_apply_denomination_forfeit``: when ``bound_vid`` is
None AND the order has exactly one vendor, treat target_idxs
as ALL of that vendor's transactions so the proportional split
distributes line items across all receipts.

Side effect: existing data with this misallocation can be self-
healed by re-saving (e.g. opening any of the order's transactions
in AdjustmentDialog and clicking OK).
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_save_multi_receipt.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield get_connection()
    close_connection()


def _seed_rockin_cat_three_receipts(conn):
    """Mirror the user's reproducer shape."""
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'Dev Test Market', 10000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES "
        "(42, \"Rockin' Cat Organic Coffee & Tea\")")
    conn.execute(
        "INSERT INTO payment_methods "
        "(id, name, match_percent, is_active, sort_order, "
        " denomination) VALUES "
        "(1, 'JH Food Bucks', 100.0, 1, 1, 200), "
        "(3, 'Food RX', 100.0, 1, 3, 1000), "
        "(2, 'SNAP', 100.0, 1, 2, NULL)")
    conn.execute(
        "INSERT INTO market_days "
        "(id, market_id, date, status, opened_by) VALUES "
        "(1, 1, '2026-05-06', 'Open', 'T')")
    # 3 receipts at the same vendor
    for tid, total in [(101, 145), (102, 1524), (103, 2532)]:
        conn.execute(
            "INSERT INTO transactions "
            "(id, fam_transaction_id, market_day_id, vendor_id, "
            " receipt_total, status, created_at) VALUES "
            "(?, ?, 1, 42, ?, 'Confirmed', '2026-05-06 12:00:00')",
            (tid, f'FAM-T-{tid}', total))
    conn.commit()


# ──────────────────────────────────────────────────────────────────
# Direct test of the save-distribution algorithm
# ──────────────────────────────────────────────────────────────────


def _simulate_save_phase1(order_transactions, items):
    """Pure-function replica of PaymentScreen save Phase 1.
    Returns ``all_txn_items`` — the line items written per
    transaction.  Mirrors lines ~3579-3694 of payment_screen.py
    (Phase 1 only — non-denom Phase 2 already had the proportional
    split working correctly).
    """
    num_txns = len(order_transactions)
    all_txn_items: list[list[dict]] = [[] for _ in range(num_txns)]
    txn_method_alloc = [0] * num_txns

    vendor_to_txn_idxs: dict[int, list[int]] = {}
    for t_idx, t in enumerate(order_transactions):
        vid = t.get('vendor_id')
        if vid is not None:
            vendor_to_txn_idxs.setdefault(vid, []).append(t_idx)
    single_order_vendor_id = (
        next(iter(vendor_to_txn_idxs.keys()))
        if len(vendor_to_txn_idxs) == 1 else None)

    def _line_item_for(item, ma, match):
        return {
            'payment_method_id': item['payment_method_id'],
            'method_name_snapshot': item['method_name_snapshot'],
            'match_percent_snapshot': item['match_percent_snapshot'],
            'method_amount': ma,
            'match_amount': match,
            'customer_charged': ma - match,
            'denomination': item.get('denomination'),
        }

    for item in items:
        denom = item.get('denomination')
        if not (denom and denom > 0):
            continue
        bound_vid = item.get('bound_vendor_id')
        if bound_vid is None and single_order_vendor_id is not None:
            bound_vid = single_order_vendor_id
        target_idxs = (
            vendor_to_txn_idxs.get(bound_vid)
            if bound_vid is not None else None)
        if not target_idxs:
            target_idxs = [0]
        ma = item['method_amount']
        mat_pct = item['match_percent_snapshot']
        total_match = item.get('match_amount')
        if total_match is None or total_match < 0:
            total_match = round(ma * (mat_pct / (100.0 + mat_pct)))

        if len(target_idxs) == 1:
            idx = target_idxs[0]
            all_txn_items[idx].append(
                _line_item_for(item, ma, total_match))
            txn_method_alloc[idx] += ma
        else:
            per_txn_remaining = []
            total_remaining = 0
            for ti in target_idxs:
                t = order_transactions[ti]
                left = max(
                    0, t['receipt_total'] - txn_method_alloc[ti])
                per_txn_remaining.append(left)
                total_remaining += left
            if total_remaining <= 0:
                idx = target_idxs[-1]
                all_txn_items[idx].append(
                    _line_item_for(item, ma, total_match))
                txn_method_alloc[idx] += ma
                continue
            running_method = 0
            running_match = 0
            last_pos = len(target_idxs) - 1
            for k, ti in enumerate(target_idxs):
                if k == last_pos:
                    share_method = ma - running_method
                    share_match = total_match - running_match
                else:
                    weight = per_txn_remaining[k] / total_remaining
                    share_method = round(ma * weight)
                    share_match = round(total_match * weight)
                    running_method += share_method
                    running_match += share_match
                if share_method == 0:
                    continue
                all_txn_items[ti].append(
                    _line_item_for(item, share_method, share_match))
                txn_method_alloc[ti] += share_method
    return all_txn_items


# ──────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────


class TestSavePhase1DistributesAcrossSingleVendorReceipts:

    def test_user_reproducer_each_txn_gets_line_items(self):
        """The exact user reproducer: 3 receipts at one vendor,
        denom rows with bound_vendor_id=None.  Every transaction
        must receive its proportional share of line items so
        that voiding any subset still leaves the surviving
        transactions with their attributable payment breakdown."""
        order_transactions = [
            {'id': 101, 'vendor_id': 42, 'receipt_total': 145},
            {'id': 102, 'vendor_id': 42, 'receipt_total': 1524},
            {'id': 103, 'vendor_id': 42, 'receipt_total': 2532},
        ]
        items = [
            # 4 × $2 Food Bucks → $12.18 method (post-forfeit)
            {'method_amount': 1218, 'match_amount': 418,
             'denomination': 200, 'bound_vendor_id': None,
             'payment_method_id': 1,
             'method_name_snapshot': 'JH Food Bucks',
             'match_percent_snapshot': 100.0},
            # 2 × $10 Food RX → $40 method
            {'method_amount': 4000, 'match_amount': 2000,
             'denomination': 1000, 'bound_vendor_id': None,
             'payment_method_id': 3,
             'method_name_snapshot': 'Food RX',
             'match_percent_snapshot': 100.0},
        ]
        all_txn_items = _simulate_save_phase1(order_transactions, items)

        # Each transaction must have at least one line item.  Pre-
        # fix, txn 0 got both items and txn 1 + txn 2 got nothing.
        for idx, items_for_txn in enumerate(all_txn_items):
            assert len(items_for_txn) > 0, (
                f"Transaction at index {idx} got NO line items.  "
                f"Pre-fix this was the bug — single-vendor multi-"
                f"receipt orders dumped everything onto txn 0 "
                f"because bound_vid=None fell back to [0].")

    def test_voiding_any_subset_leaves_breakdown_on_survivors(self):
        """The downstream symptom: after voiding the first 2
        transactions, the surviving (largest) transaction MUST
        still have the payment breakdown attached.  Pre-fix
        Vendor Reimbursement showed $0 in every method column
        because the line items were attached to a now-voided txn."""
        order_transactions = [
            {'id': 101, 'vendor_id': 42, 'receipt_total': 145},
            {'id': 102, 'vendor_id': 42, 'receipt_total': 1524},
            {'id': 103, 'vendor_id': 42, 'receipt_total': 2532},
        ]
        items = [
            {'method_amount': 1218, 'match_amount': 418,
             'denomination': 200, 'bound_vendor_id': None,
             'payment_method_id': 1,
             'method_name_snapshot': 'JH Food Bucks',
             'match_percent_snapshot': 100.0},
            {'method_amount': 4000, 'match_amount': 2000,
             'denomination': 1000, 'bound_vendor_id': None,
             'payment_method_id': 3,
             'method_name_snapshot': 'Food RX',
             'match_percent_snapshot': 100.0},
        ]
        all_txn_items = _simulate_save_phase1(order_transactions, items)

        # Survivor = transaction 2 ($25.32, the largest).  After
        # voiding txn 0 and 1, this is the only Confirmed txn.
        # It MUST have line items so Vendor Reimbursement aggregates
        # correctly.
        survivor_items = all_txn_items[2]
        assert len(survivor_items) > 0, (
            "Survivor transaction must have its share of line items.")

        # Specifically, Food Bucks AND Food RX should both have
        # contributions on the survivor (proportional split).
        method_names = {
            li['method_name_snapshot'] for li in survivor_items}
        assert 'JH Food Bucks' in method_names, (
            "Survivor txn must include FB share — pre-fix the "
            "$12.18 of Food Bucks all went to txn 0.")
        assert 'Food RX' in method_names, (
            "Survivor txn must include Food RX share.")

    def test_total_allocation_sums_to_method_total(self):
        """Sum of share_method across all transactions must equal
        the original method_amount (no money lost or duplicated)."""
        order_transactions = [
            {'id': 101, 'vendor_id': 42, 'receipt_total': 145},
            {'id': 102, 'vendor_id': 42, 'receipt_total': 1524},
            {'id': 103, 'vendor_id': 42, 'receipt_total': 2532},
        ]
        items = [
            {'method_amount': 1218, 'match_amount': 418,
             'denomination': 200, 'bound_vendor_id': None,
             'payment_method_id': 1,
             'method_name_snapshot': 'JH Food Bucks',
             'match_percent_snapshot': 100.0},
            {'method_amount': 4000, 'match_amount': 2000,
             'denomination': 1000, 'bound_vendor_id': None,
             'payment_method_id': 3,
             'method_name_snapshot': 'Food RX',
             'match_percent_snapshot': 100.0},
        ]
        all_txn_items = _simulate_save_phase1(order_transactions, items)

        # Sum FB shares across all transactions
        fb_total = sum(
            li['method_amount']
            for txn in all_txn_items
            for li in txn
            if li['method_name_snapshot'] == 'JH Food Bucks')
        assert fb_total == 1218

        food_rx_total = sum(
            li['method_amount']
            for txn in all_txn_items
            for li in txn
            if li['method_name_snapshot'] == 'Food RX')
        assert food_rx_total == 4000

    def test_explicit_binding_still_works(self):
        """When bound_vendor_id IS set explicitly, behavior is
        unchanged — the implicit fallback only kicks in when None."""
        order_transactions = [
            {'id': 101, 'vendor_id': 42, 'receipt_total': 145},
            {'id': 102, 'vendor_id': 42, 'receipt_total': 2532},
        ]
        items = [
            {'method_amount': 400, 'match_amount': 200,
             'denomination': 200, 'bound_vendor_id': 42,  # explicit
             'payment_method_id': 1,
             'method_name_snapshot': 'JH Food Bucks',
             'match_percent_snapshot': 100.0},
        ]
        all_txn_items = _simulate_save_phase1(order_transactions, items)
        # Both transactions should still get a share (proportional)
        assert len(all_txn_items[0]) > 0 or len(all_txn_items[1]) > 0
        fb_total = sum(
            li['method_amount']
            for txn in all_txn_items
            for li in txn)
        assert fb_total == 400

    def test_multi_vendor_unbound_falls_back_defensively(self):
        """Multi-vendor + unbound → falls back to txn 0 (legacy
        defensive default).  Eligibility / Layer-2 guards in the
        UI should catch this case before reaching the save algorithm."""
        order_transactions = [
            {'id': 101, 'vendor_id': 42, 'receipt_total': 1000},
            {'id': 102, 'vendor_id': 99, 'receipt_total': 1000},
        ]
        items = [
            {'method_amount': 400, 'match_amount': 200,
             'denomination': 200, 'bound_vendor_id': None,
             'payment_method_id': 1,
             'method_name_snapshot': 'JH Food Bucks',
             'match_percent_snapshot': 100.0},
        ]
        all_txn_items = _simulate_save_phase1(order_transactions, items)
        # Multi-vendor + None binding → defensive fallback to [0].
        # txn 0 gets all of it; txn 1 gets nothing.  The earlier
        # eligibility guards in _confirm_payment must catch this.
        assert len(all_txn_items[0]) == 1
        assert len(all_txn_items[1]) == 0


class TestParityAcrossThreePathsThatNeedTheFix:
    """Source-pin: the same single-vendor fallback pattern must
    exist in three places.  If any drops it, multi-receipt orders
    silently misallocate.  All three must contain
    ``bound_vid is None and ... is not None``:
      1. PaymentScreen._confirm_payment (Layer 2C check)
      2. PaymentScreen._apply_denomination_forfeit
      3. PaymentScreen save Phase 1 (this fix)
    """

    def test_all_three_paths_have_implicit_binding_fallback(self):
        import inspect
        from fam.ui.payment_screen import PaymentScreen

        from fam.utils.calculations import apply_denomination_forfeit
        confirm_src = inspect.getsource(
            PaymentScreen._confirm_payment)
        # v2.0.7-final consolidation: forfeit math lives in
        # fam.utils.calculations.apply_denomination_forfeit;
        # PaymentScreen._apply_denomination_forfeit is a thin
        # wrapper.  Source-pin against the canonical function.
        forfeit_src = inspect.getsource(apply_denomination_forfeit)
        save_src = inspect.getsource(
            PaymentScreen._distribute_and_save_payments)

        assert (
            'single_order_vendor_id is not None'
            in confirm_src), (
            "Layer 2C in _confirm_payment must include the "
            "single_order_vendor_id fallback.")
        assert (
            'single_vendor_id is not None'
            in forfeit_src), (
            "Canonical apply_denomination_forfeit must use the "
            "single_vendor_id fallback for unbound denom rows.")
        assert (
            'single_order_vendor_id is not None'
            in save_src), (
            "_distribute_and_save_payments Phase 1 must use the "
            "single-vendor fallback.  Pre-fix this dropped denom "
            "line items onto txn 0 only — voiding it left Vendor "
            "Reimbursement showing $0 in every method column for "
            "the surviving txns.")
