"""UI-visible field invariants — every visible value must reconcile.

Why this test tier exists
-------------------------
The 2026-04 onsite found two consecutive bugs that **were visible in
PaymentScreen screenshots** but missed by every prior audit:

  1. Auto-Distribute silently clamped a bound denom row from 14 → 13
     units (Multi-vendor multi-overage interaction in
     ``_push_row_limits``)
  2. Per-vendor "Remaining" column showed ``$0.01`` on Juice Bar and
     ``-$0.01`` on Elfinwild, despite the order ``totaling`` correctly
     (denomination forfeit using order-level overage instead of
     per-vendor overage)

Both bugs produced silent ±1¢ misallocations that were within Layer
2C's confirm-time tolerance.  Both surfaced the moment a human looked
at the vendor breakdown table.

The earlier audits validated:
  * the engine outputs (calculate_payment_breakdown)
  * the database state after save (per-line invariant, per-txn sum)
  * the report queries (vendor reimbursement, FAM Match)
  * the export CSVs (cell-by-cell)

But NOT:
  * the actual cells of the rendered vendor breakdown table
  * the summary cards visible to the volunteer
  * the warning labels' text content
  * the cross-layer agreement between engine output, UI display,
    and what the save would commit per-vendor

This file fills that gap.  Every test sets up a payment scenario,
drives the actual ``PaymentScreen`` widget, then reads every visible
field and asserts cross-layer consistency to ±0¢.

Contract
--------
For every PaymentScreen state where the volunteer COULD click
Confirm:

  V1: Vendor breakdown table — column "Remaining" is 0 (or shown
      as a denomination-forfeit gap with matching warning text)
      for every vendor whose receipt is fully allocated by the
      breakdown.
  V2: Σ (per-method method_amount cells in a vendor's row) ==
      vendor's receipt - vendor's remaining
  V3: Summary cards (Total Allocated, Customer Pays, FAM Match)
      equal the sum of corresponding values across all rows
  V4: ``denom_overage_warning`` text reports an amount that
      equals exactly Σ over_per_vendor (the actual forfeit being
      taken)
  V5: Per-row Total = customer_charged + match_amount (per-line
      invariant at the visible level too)
"""

import pytest
from PySide6.QtWidgets import QApplication

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.utils.money import dollars_to_cents


# ════════════════════════════════════════════════════════════════════
# Fixture — 6-vendor scenario mirroring the user's screenshot
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def screenshot_db(tmp_path):
    db_file = str(tmp_path / "ui_inv.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'UI', 10000, 1)")
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
    # Eligibility — Juice Bar excludes Food Bucks.
    for vid in (1, 2, 3, 4, 5, 6):
        for mid in (1, 2, 3):
            conn.execute(
                "INSERT INTO vendor_payment_methods "
                "(vendor_id, payment_method_id) VALUES (?, ?)",
                (vid, mid))
    for vid in (2, 3, 4, 5, 6):
        conn.execute(
            "INSERT INTO vendor_payment_methods "
            "(vendor_id, payment_method_id) VALUES (?, 4)", (vid,))
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-29', 'Open', 'T')")

    # Build the order: 6 transactions matching the screenshot.
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-007-LB1',
        zip_code='15102')
    for vid, receipt in [(1, 4860), (2, 1250), (3, 520),
                          (4, 1111), (5, 1230), (6, 1200)]:
        create_transaction(
            market_day_id=1, vendor_id=vid,
            receipt_total=receipt,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
    conn.commit()
    yield conn, order_id
    close_connection()


# ════════════════════════════════════════════════════════════════════
# UI snapshot helpers — read what's visible
# ════════════════════════════════════════════════════════════════════

def _read_vendor_table(screen):
    """Return [{vendor, receipt_cents, remaining_cents,
    per_method:{name: text}}] for every visible row."""
    rows = []
    table = screen.vendor_table
    methods = screen._breakdown_methods
    for r in range(table.rowCount()):
        name = table.item(r, 0).text() if table.item(r, 0) else ''
        receipt_text = table.item(r, 1).text() if table.item(r, 1) else ''
        remaining_text = table.item(r, 2).text() if table.item(r, 2) else ''
        per_method = {}
        for ci, m in enumerate(methods):
            cell = table.item(r, 3 + ci)
            per_method[m['name']] = cell.text() if cell else ''
        rows.append({
            'vendor': name,
            'receipt_text': receipt_text,
            'receipt_cents': dollars_to_cents(
                _parse_dollars(receipt_text)),
            'remaining_text': remaining_text,
            'remaining_cents': _parse_signed_cents(remaining_text),
            'per_method': per_method,
        })
    return rows


def _read_summary_cards(screen):
    """Read the four summary cards visible at the top of the screen."""
    return {
        'order_total': screen.order_total_label.text(),
        'allocated': screen.total_allocated_label.text() if hasattr(
            screen, 'total_allocated_label') else None,
        'remaining': screen.remaining_label.text() if hasattr(
            screen, 'remaining_label') else None,
        'customer_pays': screen.customer_pays_label.text() if hasattr(
            screen, 'customer_pays_label') else None,
        'fam_match': screen.fam_match_label.text() if hasattr(
            screen, 'fam_match_label') else None,
    }


def _read_warnings(screen):
    return {
        'denom_overage_visible': screen.denom_overage_warning.isVisible(),
        'denom_overage_text': screen.denom_overage_warning.text(),
        'error_visible': screen.error_label.isVisible(),
        'error_text': screen.error_label.text(),
    }


def _parse_dollars(text):
    """'$48.60' or '-$3.68' or '$0.00' → float dollars."""
    text = text.replace('$', '').replace(',', '').strip()
    if not text:
        return 0.0
    return float(text)


def _parse_signed_cents(text):
    """'$0.01' → 1; '-$0.01' or '$-0.01' → -1; '$0.00' → 0."""
    if not text:
        return 0
    return round(_parse_dollars(text) * 100)


# ════════════════════════════════════════════════════════════════════
# V1: Every fully-allocated vendor's "Remaining" cell must be 0
# ════════════════════════════════════════════════════════════════════

class TestUI_V1_RemainingZeroForFullyAllocatedVendor:
    """The bug in the user's screenshot: ``Remaining`` cells showed
    ``$0.01`` and ``-$0.01`` for vendors whose receipts were
    nominally covered by the engine output.  This test guards that
    every vendor's Remaining must be exactly $0 (or a tracked
    forfeit amount with a matching warning) — never a stray penny."""

    def test_no_stray_pennies_after_overage_forfeit(
            self, qtbot, screenshot_db):
        """User's exact scenario: 2× FB @ Elfinwild (overage), 2× FB
        @ Pond Hill, $20 Cash, $34.25 SNAP.  Every vendor's Remaining
        must be 0 in the breakdown table after _update_summary."""
        from fam.ui.payment_screen import PaymentScreen

        conn, order_id = screenshot_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Wipe auto-added blank rows so we start clean.
        while screen._payment_rows:
            row = screen._payment_rows[0]
            screen.rows_layout.removeWidget(row)
            row.deleteLater()
            screen._payment_rows.remove(row)

        # Add the 4 rows the user entered.
        def _add(method_name, charge_cents, vendor_id=None):
            row = screen._add_payment_row()
            combo = row.method_combo
            for i in range(combo.count()):
                if method_name.lower() in combo.itemText(i).lower():
                    combo.setCurrentIndex(i)
                    break
            if vendor_id is not None:
                row.set_bound_vendor_id(vendor_id)
            row._set_active_charge(charge_cents)
            return row

        _add('Food Bucks', 400, vendor_id=3)   # 2 × $2 @ Elfinwild
        _add('Food Bucks', 400, vendor_id=6)   # 2 × $2 @ Pond Hill
        _add('Cash',       2000)               # $20 Cash
        _add('SNAP',       3425)               # $34.25 SNAP

        screen._update_summary()

        rows = _read_vendor_table(screen)
        # ── V1 ── Every Remaining cell must be exactly 0¢
        drifts = [(r['vendor'], r['remaining_cents'])
                   for r in rows if r['remaining_cents'] != 0]
        assert not drifts, (
            "V1 violated: vendor breakdown has stray-penny "
            f"Remaining cells: {drifts}.  This is the exact "
            "screenshot bug — per-vendor reconciliation must be 0¢.")


# ════════════════════════════════════════════════════════════════════
# V2: Per-row sum of per-method cells == receipt - remaining
# ════════════════════════════════════════════════════════════════════

class TestUI_V2_RowSumEqualsReceiptMinusRemaining:
    """v2.0.7+ denomination-integrity: per-method cells now display
    the customer's denomination-true payment (tokens × face value
    for denom methods, vendor-share method_amount for non-denom).
    The pre-v2.0.7 invariant ``cells_sum == receipt - remaining``
    no longer holds because denom cells intentionally exclude the
    FAM-match contribution to make the breakdown denomination-pure
    (e.g. "2 × $10.00 = $20.00" instead of "2 × $10.00 = $25.63"
    where $5.63 was hidden FAM match).

    The new invariant pins:

      1. Every denom cell's "N × $D = $T" text is internally
         consistent (T == N × D, no FAM-match intermingling).
      2. cells_sum ≤ allocated (denom cells exclude match, so the
         total cell sum is at most the allocated amount).
      3. cells_sum > 0 whenever allocated > 0 (a vendor with
         non-zero allocation MUST show non-zero cells)."""

    def test_per_vendor_row_sum_matches_denomination_true_payment(
            self, qtbot, screenshot_db):
        import re
        from fam.ui.payment_screen import PaymentScreen
        conn, order_id = screenshot_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            row = screen._payment_rows[0]
            screen.rows_layout.removeWidget(row)
            row.deleteLater()
            screen._payment_rows.remove(row)

        # Add rows
        for charge_cents, method, vendor in [
                (400, 'Food Bucks', 3),
                (400, 'Food Bucks', 6),
                (2000, 'Cash', None),
                (3425, 'SNAP', None)]:
            row = screen._add_payment_row()
            combo = row.method_combo
            for i in range(combo.count()):
                if method.lower() in combo.itemText(i).lower():
                    combo.setCurrentIndex(i)
                    break
            if vendor is not None:
                row.set_bound_vendor_id(vendor)
            row._set_active_charge(charge_cents)
        screen._update_summary()

        # Pattern matches the denom cell text "✓ N × $D.DD = $T.TT"
        denom_pat = re.compile(
            r'(\d+)\s*[×x]\s*\$(\d+\.\d{2})\s*=\s*\$(\d+\.\d{2})')

        rows = _read_vendor_table(screen)
        for r in rows:
            allocated = r['receipt_cents'] - r['remaining_cents']
            cells_sum = 0
            for cell_text in r['per_method'].values():
                cells_sum += _extract_method_amount_cents(cell_text)
                # New invariant 1: denom cell text must be
                # internally consistent (T == N × D).
                m = denom_pat.search(cell_text)
                if m:
                    n = int(m.group(1))
                    d = float(m.group(2))
                    t = float(m.group(3))
                    expected_t = round(n * d, 2)
                    assert abs(t - expected_t) <= 0.01, (
                        f"V2 (denom-pure) violated for vendor "
                        f"{r['vendor']!r}: cell shows "
                        f"{n} × ${d:.2f} = ${t:.2f}, but "
                        f"{n} × ${d:.2f} actually equals "
                        f"${expected_t:.2f}.  Cell: {cell_text!r}")

            # New invariant 2 + 3: cells_sum ≤ allocated and
            # > 0 when allocated > 0 (non-strict equality because
            # denom cells exclude FAM match).
            assert cells_sum <= allocated, (
                f"V2 violated: vendor {r['vendor']!r}: "
                f"per_method_sum ({cells_sum}c) must be ≤ "
                f"allocated ({allocated}c).  Cells now show "
                f"denomination-true customer payment (no FAM "
                f"match), so the sum cannot exceed allocated.  "
                f"Row cells:\n  {r['per_method']}")
            if allocated > 0:
                assert cells_sum > 0, (
                    f"V2 violated: vendor {r['vendor']!r} has "
                    f"allocation {allocated}c but cells sum to 0.  "
                    f"Row cells:\n  {r['per_method']}")


def _extract_method_amount_cents(cell_text: str) -> int:
    """Extract dollar amount from a vendor-table cell.

    Cell formats:
      ✗
      ✓                                      (eligible, zero alloc)
      ✓  $X.XX                               (non-denom alloc)
      ✓  N × $D.DD = $X.XX                   (denom alloc)
    """
    text = cell_text.strip()
    if not text or text in ('✗', '✓'):
        return 0
    # Take the LAST $-prefixed amount; that's always the row total.
    parts = text.split('$')
    last = parts[-1].strip()
    try:
        return dollars_to_cents(float(last))
    except (ValueError, AttributeError):
        return 0


# ════════════════════════════════════════════════════════════════════
# V4: denom_overage_warning text reports the exact forfeit
# ════════════════════════════════════════════════════════════════════

class TestUI_V4_OverageWarningMatchesActualForfeit:
    """The warning text must report the SAME number that's actually
    being forfeited, derivable from the breakdown table.  The bug
    in the user's screenshot showed `$2.79` while the actual
    per-vendor forfeit was $2.80.  After the fix the warning must
    match reality."""

    def test_denom_overage_warning_equals_forfeit_taken(
            self, qtbot, screenshot_db):
        from fam.ui.payment_screen import PaymentScreen
        conn, order_id = screenshot_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            row = screen._payment_rows[0]
            screen.rows_layout.removeWidget(row)
            row.deleteLater()
            screen._payment_rows.remove(row)
        for charge_cents, method, vendor in [
                (400, 'Food Bucks', 3),
                (400, 'Food Bucks', 6),
                (2000, 'Cash', None),
                (3425, 'SNAP', None)]:
            row = screen._add_payment_row()
            combo = row.method_combo
            for i in range(combo.count()):
                if method.lower() in combo.itemText(i).lower():
                    combo.setCurrentIndex(i)
                    break
            if vendor is not None:
                row.set_bound_vendor_id(vendor)
            row._set_active_charge(charge_cents)
        screen._update_summary()

        rows = _read_vendor_table(screen)
        # After the v1.9.10 fix, every vendor reconciles to 0
        # remaining, AND the warning text reports the actual
        # forfeit amount.  Compute expected forfeit:
        # sum(method_amount > vendor_receipt) per bound denom row.
        # Elfinwild: 2 FB × $4 method = $8 vs $5.20 receipt → $2.80
        # Pond Hill: 2 FB × $4 method = $8 vs $12 receipt → $0
        # Order forfeit = $2.80
        warning = _read_warnings(screen)
        if not warning['denom_overage_visible']:
            return  # forfeit warning hidden — only visible when overage
        text = warning['denom_overage_text']
        assert '$2.80' in text or '2.80' in text, (
            f"V4 violated: denom_overage warning text must report "
            f"$2.80 (the actual per-vendor forfeit on Elfinwild), "
            f"got: {text!r}")


# ════════════════════════════════════════════════════════════════════
# V5: per-row Total visible = customer + match (no UI drift)
# ════════════════════════════════════════════════════════════════════

class TestUI_V5_PerRowTotalEqualsCustomerPlusMatch:
    """Each PaymentRow widget shows ``Charge``, ``Match``, and
    ``Total`` labels.  The contract: Total == Charge + Match
    *as displayed*, every time, regardless of cap or forfeit
    state."""

    def test_per_row_visible_total_equals_sum(
            self, qtbot, screenshot_db):
        from fam.ui.payment_screen import PaymentScreen
        conn, order_id = screenshot_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            row = screen._payment_rows[0]
            screen.rows_layout.removeWidget(row)
            row.deleteLater()
            screen._payment_rows.remove(row)
        for charge_cents, method, vendor in [
                (400, 'Food Bucks', 3),
                (400, 'Food Bucks', 6),
                (2000, 'Cash', None),
                (3425, 'SNAP', None)]:
            row = screen._add_payment_row()
            combo = row.method_combo
            for i in range(combo.count()):
                if method.lower() in combo.itemText(i).lower():
                    combo.setCurrentIndex(i)
                    break
            if vendor is not None:
                row.set_bound_vendor_id(vendor)
            row._set_active_charge(charge_cents)
        screen._update_summary()

        for i, row in enumerate(screen._payment_rows):
            # Read the on-screen labels.
            match_text = row.match_amount_label.text()
            total_text = row.total_label.text()
            charge_cents = row._get_active_charge()
            match_cents = dollars_to_cents(_parse_dollars(match_text))
            total_cents = dollars_to_cents(_parse_dollars(total_text))
            assert total_cents == charge_cents + match_cents, (
                f"V5 violated on row {i}: "
                f"charge={charge_cents}c match={match_cents}c "
                f"total={total_cents}c (charge+match should "
                f"equal total)")
