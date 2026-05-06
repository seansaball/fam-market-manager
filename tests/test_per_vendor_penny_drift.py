"""Per-vendor reconciliation penny-drift bug reproducer + regression alarm.

User-reported screenshot (Customer C-007-LB1, 6 vendors, $101.71):

    1.11 Juice Bar    receipt $48.60   showed "$0.01 remaining"
    Hello Hummus      receipt $12.50   $0.00
    Elfinwild Farms   receipt $5.20    showed "-$0.01 remaining"
    Pgh Dumplingz     receipt $11.11   $0.00
    Sturges Orchards  receipt $12.30   $0.00
    Pond Hill Farm    receipt $12.00   $0.00

Volunteer entered:
  - 2 × JH Food Bucks  @ Elfinwild ($4 customer, $8 method uncapped)
  - 2 × JH Food Bucks  @ Pond Hill ($4 customer, $8 method)
  - $20 Cash
  - $34.25 SNAP

Root cause (v1.9.10 finding)
----------------------------
``_apply_denomination_forfeit`` capped each vendor's reduction at the
*order-level* overage rather than its *per-vendor* overage.  Elfinwild
needed $2.80 of forfeit (its per-vendor overage); the order-level
overage was only $2.79.  The cap stopped the reduction 1¢ short, so:

  - Elfinwild's bound FB row stayed at $5.21 (1¢ over its $5.20 receipt)
  - Juice Bar's proportional non-denom share landed 1¢ short of $48.60

Both gaps fell within Layer 2C's ±1¢ tolerance and the user could
Confirm — but per-vendor reimbursement reports would have paid Juice
Bar 1¢ less than the receipt and Elfinwild 1¢ more, violating I2.

This test reproduces the screenshot scenario through the actual
``_apply_denomination_forfeit`` method, then runs the same per-vendor
proportional split as the save path.  Every vendor must land at
exactly $0 remaining.
"""

import pytest
from unittest.mock import MagicMock

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.utils.calculations import calculate_payment_breakdown


@pytest.fixture
def six_vendor_screenshot_db(tmp_path):
    db_file = str(tmp_path / "screenshot.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'Test Market', 10000, 1)")
    vendors = [
        (1, '1.11 Juice Bar'),
        (2, 'Hello Hummus'),
        (3, 'Elfinwild Farms'),
        (4, 'Pgh Dumplingz'),
        (5, 'Sturges Orchards'),
        (6, 'Pond Hill Farm LLC'),
    ]
    for vid, name in vendors:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))
    methods = [
        (1, 'SNAP',          100.0, None, 1),
        (2, 'Cash',            0.0, None, 2),
        (3, 'Food RX',        50.0, None, 3),
        (4, 'JH Food Bucks', 100.0,  200, 4),
        (5, 'JH Tokens',     100.0,  500, 5),
    ]
    for mid, name, pct, denom, sort_o in methods:
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            " denomination, sort_order, is_active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (mid, name, pct, denom, sort_o))
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, "
            " payment_method_id) VALUES (1, ?)", (mid,))
    for vid in (1, 2, 3, 4, 5, 6):
        for mid in (1, 2, 3, 5):
            conn.execute(
                "INSERT INTO vendor_payment_methods "
                " (vendor_id, payment_method_id) VALUES (?, ?)",
                (vid, mid))
    for vid in (2, 3, 4, 5, 6):
        conn.execute(
            "INSERT INTO vendor_payment_methods "
            " (vendor_id, payment_method_id) VALUES (?, 4)", (vid,))
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-29', 'Open', 'T')")
    conn.commit()
    yield conn
    close_connection()


def _drive_forfeit_then_distribute(receipts, items_input, screen_mock):
    """Helper: apply the forfeit + per-vendor save-time distribution
    exactly as ``_confirm_payment`` does, and return the final
    per-vendor allocation map.

    Each ``items_input`` entry must include ``denomination`` (None for
    non-denom) and ``bound_vendor_id`` (None for non-denom).
    """
    from fam.ui.payment_screen import PaymentScreen

    receipt_total = sum(r for _, r in receipts)
    entries = [
        {'method_amount': it['method_amount'],
         'match_percent': it['match_percent_snapshot']}
        for it in items_input
    ]
    result = calculate_payment_breakdown(receipt_total, entries,
                                          match_limit=10000)

    # Mirror _confirm_payment's denom-overage check + forfeit step.
    overage = result.get('allocated_total', 0) - receipt_total
    if overage > 0:
        # Use the real method via PaymentScreen so the test exercises
        # production code, not a copy.  We bind the minimum state the
        # method touches: ``self._order_transactions``.
        ps = PaymentScreen.__new__(PaymentScreen)
        ps._order_transactions = [
            {'id': vid, 'vendor_id': vid, 'receipt_total': r}
            for vid, r in receipts
        ]
        # _apply_denomination_forfeit mutates ``items`` list in place
        # so we keep a sibling copy for the post-pass distribution.
        items = [dict(it) for it in items_input]
        ps._apply_denomination_forfeit(result, items, overage)
    else:
        items = items_input

    # Now run the per-vendor proportional distribution that the save
    # path uses (Phase 1: denom claims; Phase 2: non-denom proportional).
    per_txn_alloc = {vid: 0 for vid, _ in receipts}
    receipt_by_vid = dict(receipts)

    # Phase 1
    for it in items:
        denom = it.get('denomination')
        if denom and denom > 0:
            target = it.get('bound_vendor_id')
            if target in per_txn_alloc:
                per_txn_alloc[target] += it['method_amount']

    # Phase 2
    txn_ids = [vid for vid, _ in receipts]
    for it in items:
        denom = it.get('denomination')
        if denom and denom > 0:
            continue
        ma_total = it['method_amount']
        per_txn_remaining = []
        total_remaining = 0
        for vid in txn_ids:
            left = max(0, receipt_by_vid[vid] - per_txn_alloc[vid])
            per_txn_remaining.append(left)
            total_remaining += left
        if total_remaining <= 0:
            continue
        running = 0
        last_idx = len(txn_ids) - 1
        for ti, vid in enumerate(txn_ids):
            if ti == last_idx:
                share = ma_total - running
            else:
                weight = per_txn_remaining[ti] / total_remaining
                share = round(ma_total * weight)
                running += share
            per_txn_alloc[vid] += share

    return per_txn_alloc


def test_user_screenshot_per_vendor_reconciliation_zero_drift(
        six_vendor_screenshot_db):
    """v1.9.10 fix: per-vendor reconciliation is exactly $0 on every
    vendor in the user's exact screenshot scenario."""
    receipts = [
        (1, 4860),  # Juice Bar
        (2, 1250),  # Hello Hummus
        (3,  520),  # Elfinwild Farms
        (4, 1111),  # Pgh Dumplingz
        (5, 1230),  # Sturges Orchards
        (6, 1200),  # Pond Hill Farm
    ]
    items_input = [
        {'payment_method_id': 4, 'method_name_snapshot': 'JH Food Bucks',
         'match_percent_snapshot': 100.0,
         'method_amount': 800,        # 2 × $2 × 2 (with 100% match)
         'match_amount': 400,
         'customer_charged': 400,
         'denomination': 200, 'bound_vendor_id': 3},  # Elfinwild
        {'payment_method_id': 4, 'method_name_snapshot': 'JH Food Bucks',
         'match_percent_snapshot': 100.0,
         'method_amount': 800,
         'match_amount': 400,
         'customer_charged': 400,
         'denomination': 200, 'bound_vendor_id': 6},  # Pond Hill
        {'payment_method_id': 2, 'method_name_snapshot': 'Cash',
         'match_percent_snapshot': 0.0,
         'method_amount': 2000,
         'match_amount': 0,
         'customer_charged': 2000,
         'denomination': None, 'bound_vendor_id': None},
        {'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
         'match_percent_snapshot': 100.0,
         'method_amount': 6850,
         'match_amount': 3425,
         'customer_charged': 3425,
         'denomination': None, 'bound_vendor_id': None},
    ]

    per_txn = _drive_forfeit_then_distribute(receipts, items_input,
                                               six_vendor_screenshot_db)

    drifts = []
    for vid, receipt in receipts:
        alloc = per_txn[vid]
        diff = alloc - receipt
        if diff != 0:
            drifts.append((vid, receipt, alloc, diff))
    if drifts:
        msg = "Per-vendor reconciliation drift (should be ±0¢):\n"
        for vid, receipt, alloc, diff in drifts:
            msg += (f"  vid={vid}  receipt={receipt}c  alloc={alloc}c  "
                     f"drift={diff:+d}c\n")
        pytest.fail(msg)


def test_order_total_still_reconciles_after_fix(
        six_vendor_screenshot_db):
    """Order-level reconciliation must still hold after the per-vendor fix."""
    receipts = [
        (1, 4860), (2, 1250), (3, 520),
        (4, 1111), (5, 1230), (6, 1200),
    ]
    items_input = [
        {'payment_method_id': 4, 'method_name_snapshot': 'JH Food Bucks',
         'match_percent_snapshot': 100.0,
         'method_amount': 800, 'match_amount': 400, 'customer_charged': 400,
         'denomination': 200, 'bound_vendor_id': 3},
        {'payment_method_id': 4, 'method_name_snapshot': 'JH Food Bucks',
         'match_percent_snapshot': 100.0,
         'method_amount': 800, 'match_amount': 400, 'customer_charged': 400,
         'denomination': 200, 'bound_vendor_id': 6},
        {'payment_method_id': 2, 'method_name_snapshot': 'Cash',
         'match_percent_snapshot': 0.0,
         'method_amount': 2000, 'match_amount': 0, 'customer_charged': 2000,
         'denomination': None, 'bound_vendor_id': None},
        {'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
         'match_percent_snapshot': 100.0,
         'method_amount': 6850, 'match_amount': 3425, 'customer_charged': 3425,
         'denomination': None, 'bound_vendor_id': None},
    ]
    per_txn = _drive_forfeit_then_distribute(receipts, items_input,
                                               six_vendor_screenshot_db)
    receipt_total = sum(r for _, r in receipts)
    assert sum(per_txn.values()) == receipt_total


def test_single_vendor_overage_unchanged(
        six_vendor_screenshot_db):
    """No regression on the simpler single-vendor-overage case
    (one bound denom row, one non-denom absorber, single vendor)."""
    receipts = [(1, 1900)]  # $19 vendor receipt
    items_input = [
        {'payment_method_id': 4, 'method_name_snapshot': 'JH Food Bucks',
         'match_percent_snapshot': 100.0,
         # 5 × $2 = $10 customer, $20 method (overshoots $19 by $1)
         'method_amount': 2000, 'match_amount': 1000, 'customer_charged': 1000,
         'denomination': 200, 'bound_vendor_id': 1},
    ]
    per_txn = _drive_forfeit_then_distribute(receipts, items_input,
                                               six_vendor_screenshot_db)
    assert per_txn[1] == 1900  # exactly the receipt
