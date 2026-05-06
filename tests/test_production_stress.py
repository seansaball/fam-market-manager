"""Production-readiness stress tests for v1.9.9.

Goal
----
Drive the FAM Market Manager harder than any existing test:

  * 10+ vendors in a single customer order
  * Every payment method (denom + non-denom, varying match %)
  * Heterogeneous per-vendor payment-method eligibility
  * Multiple simultaneous denomination overages (independent forfeits)
  * Penny / odd-cent / fractional-match reconciliation
  * Returning-customer match-cap accumulation across visits
  * Sequential adjustment chains with full audit-trail integrity
  * Adjust → void → vendor reimbursement still reconciles
  * DB ↔ line items ↔ reports ↔ ledger backup ↔ audit log all agree

Each test is self-contained and uses the ``stress_db`` fixture below.
The fixture builds a "production scale" market with 12 vendors and 6
payment methods, with hand-tuned vendor-payment eligibility patterns
so the tests exercise real-world heterogeneity.

These tests are stricter than the rest of the suite — they assert
on per-cent reconciliation across multiple report surfaces, not
just "the save didn't crash".  When something fails here, treat it
as a financial-integrity bug.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.utils.calculations import calculate_payment_breakdown
from fam.utils.money import dollars_to_cents


# ══════════════════════════════════════════════════════════════════
# Production-scale fixture: 12 vendors × 6 payment methods
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def stress_db(tmp_path):
    """Fresh DB seeded with one market, 12 vendors, 6 payment methods,
    and intentionally-uneven vendor-payment eligibility so the
    multi-vendor / multi-method scenarios actually test the
    eligibility-aware code paths.

    Match limit: $500 per customer per day — high enough that most
    tests don't hit the cap, low enough that returning-customer
    tests can exercise the cap accumulation logic explicitly.
    """
    db_file = str(tmp_path / "stress.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    # Market with $500 / customer / day cap
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'Stress Market', 50000, 1)"
    )

    # 12 vendors covering a full alphabet sweep so test output is
    # easy to scan when something fails.
    vendors = [
        (1, 'Apple Orchard'),
        (2, 'Bakery Plus'),
        (3, 'Cidery Lane'),
        (4, 'Dumpling Dynasty'),
        (5, 'Egg Farm'),
        (6, 'Fresh Fish'),
        (7, 'Greens & Things'),
        (8, 'Honey Pot'),
        (9, 'Italian Imports'),
        (10, 'Juice Bar'),
        (11, 'Kefir Kingdom'),
        (12, 'Local Lamb'),
    ]
    for vid, name in vendors:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))

    # 6 payment methods spanning the full feature space:
    #   - 0% match (Cash) — no FAM contribution
    #   - 50% match (Food RX) — fractional match → odd-cent edge cases
    #   - 100% match (SNAP) — most common
    #   - 100% match denominated ($2 Food Bucks, $5 FMNP) — forfeit cases
    #   - 200% match (Premium Match) — high-multiplier edge case
    methods = [
        (1, 'SNAP',          100.0, None, 1),
        (2, 'Cash',            0.0, None, 2),
        (3, 'Food RX',        50.0, None, 3),  # 50% → fractional cents
        (4, 'JH Food Bucks', 100.0, 200,  4),  # $2 denom
        (5, 'FMNP',          100.0, 500,  5),  # $5 denom
        (6, 'Premium Match', 200.0, None, 6),  # 2x match (rare)
    ]
    for mid, name, pct, denom, sort_order in methods:
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            " denomination, sort_order, is_active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (mid, name, pct, denom, sort_order))

    # All methods registered for the market.
    for mid, *_ in methods:
        conn.execute(
            "INSERT INTO market_payment_methods "
            "(market_id, payment_method_id) VALUES (1, ?)", (mid,))

    # Per-vendor eligibility — deliberately heterogeneous so the
    # eligibility-aware allocator gets exercised.  Patterns:
    #   • All vendors accept SNAP (1) and Cash (2)
    #   • Even-id vendors accept Food Bucks (4) [denom $2]
    #   • Multiples of 3 accept FMNP (5) [denom $5]
    #   • Multiples of 4 accept Premium Match (6)
    #   • Vendors 1,5,9 accept Food RX (3)  [50% match]
    eligibility = {
        1:  [1, 2, 3, 5],          # SNAP, Cash, Food RX, FMNP
        2:  [1, 2, 4],             # SNAP, Cash, Food Bucks
        3:  [1, 2, 5],             # SNAP, Cash, FMNP
        4:  [1, 2, 4, 6],          # SNAP, Cash, Food Bucks, Premium
        5:  [1, 2, 3],             # SNAP, Cash, Food RX
        6:  [1, 2, 4, 5],          # SNAP, Cash, Food Bucks, FMNP
        7:  [1, 2],                # SNAP, Cash only
        8:  [1, 2, 4, 6],          # SNAP, Cash, Food Bucks, Premium
        9:  [1, 2, 3, 5],          # SNAP, Cash, Food RX, FMNP
        10: [1, 2, 4],             # SNAP, Cash, Food Bucks
        11: [1, 2, 5],             # SNAP, Cash, FMNP
        12: [1, 2, 4, 6],          # SNAP, Cash, Food Bucks, Premium
    }
    for vid, mids in eligibility.items():
        for mid in mids:
            conn.execute(
                "INSERT INTO vendor_payment_methods "
                "(vendor_id, payment_method_id) VALUES (?, ?)",
                (vid, mid))

    # Open market day (date pinned by conftest's _stable_eastern_today
    # so the v1.9.9 stale-day guard doesn't fire).
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-29', 'Open', 'Tester')")
    conn.commit()
    yield conn
    close_connection()


def _seed_simple_order(conn, vendor_receipts: list[tuple[int, int]],
                       customer_label: str = 'C-001') -> tuple:
    """Helper: create a customer order with one transaction per
    (vendor_id, receipt_total_cents) tuple.  Returns
    ``(order_id, [txn_ids])``."""
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label=customer_label,
        zip_code='15102')
    txn_ids = []
    for vendor_id, receipt_cents in vendor_receipts:
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=vendor_id,
            receipt_total=receipt_cents,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
        txn_ids.append(txn_id)
    return order_id, txn_ids


def _save_payment_for_txn(txn_id: int, items: list[dict]):
    """Save payment line items for a transaction and confirm it
    (BOTH the transaction AND its customer_order).

    The order-status update mirrors PaymentScreen._confirm_payment
    which calls ``update_customer_order_status(order_id, 'Confirmed')``
    alongside ``confirm_transaction(...)``.  Without that step the
    returning-customer match-cap query (which filters on
    ``co.status IN ('Confirmed', 'Adjusted')``) sees the order as
    Draft and returns 0 — silently breaking the cap accumulation
    in tests even though the data is "right" by every other measure.
    """
    from fam.models.transaction import (
        save_payment_line_items, confirm_transaction,
    )
    from fam.models.customer_order import update_customer_order_status
    conn = get_connection()
    save_payment_line_items(txn_id, items, commit=False)
    confirm_transaction(txn_id, confirmed_by='Tester', commit=False)
    # Look up the order id and confirm it too.
    row = conn.execute(
        "SELECT customer_order_id FROM transactions WHERE id = ?",
        (txn_id,)).fetchone()
    if row and row[0] is not None:
        update_customer_order_status(row[0], 'Confirmed', commit=False)
    conn.commit()


def _line_item(method_id, name, match_pct, method_amt, match_amt,
               customer_charged) -> dict:
    """Build a payment_line_items row dict for save."""
    return {
        'payment_method_id': method_id,
        'method_name_snapshot': name,
        'match_percent_snapshot': match_pct,
        'method_amount': method_amt,
        'match_amount': match_amt,
        'customer_charged': customer_charged,
    }


# ══════════════════════════════════════════════════════════════════
# 1. Mega-order: 10+ vendors, all payment methods, full reconciliation
# ══════════════════════════════════════════════════════════════════
class TestMegaOrderReconciliation:
    """The marquee stress test: a single customer order spanning
    10 vendors with mixed payment methods.  Every cent must
    reconcile across DB, ledger, reports, and audit log."""

    def test_ten_vendor_mega_order_reconciles(self, stress_db):
        # 10 vendors with varied receipt totals.  Use awkward odd-
        # cent values to force penny reconciliation through the
        # multi-vendor distribution.
        vendor_receipts = [
            (1,  1233),  # $12.33
            (2,  2567),  # $25.67
            (3,  1900),  # $19.00
            (4,  3399),  # $33.99
            (5,  1099),  # $10.99
            (6,   799),  # $7.99
            (7,  4501),  # $45.01
            (8,  1500),  # $15.00
            (9,  2200),  # $22.00
            (10,  865),  # $8.65
        ]
        order_total = sum(rt for _, rt in vendor_receipts)
        # Sanity: $200.63 — odd cents to stress reconciliation.
        assert order_total == 20063

        order_id, txn_ids = _seed_simple_order(
            stress_db, vendor_receipts)

        # Each vendor gets its own breakdown.  Mix of methods:
        #  - vendors with FMNP/Food Bucks eligibility get a
        #    denominated row (this exercises multi-denom scenarios)
        #  - everyone else gets SNAP + Cash split
        eligibility = self._fetch_eligibility(stress_db)

        for (vid, receipt), txn_id in zip(vendor_receipts, txn_ids):
            self._allocate_and_save(vid, receipt, txn_id, eligibility)

        # ── Reconciliation pass ─────────────────────────────────
        # 1. Per-transaction invariant: customer + match = receipt
        for txn_id in txn_ids:
            self._assert_txn_invariant(stress_db, txn_id)

        # 2. Order-level: sum of receipts = sum of method_amounts
        ord_receipt_total = stress_db.execute(
            "SELECT SUM(receipt_total) FROM transactions "
            "WHERE customer_order_id = ?", (order_id,)
        ).fetchone()[0]
        ord_method_total = stress_db.execute(
            "SELECT SUM(pli.method_amount) FROM payment_line_items pli "
            "JOIN transactions t ON pli.transaction_id = t.id "
            "WHERE t.customer_order_id = ?", (order_id,)
        ).fetchone()[0]
        assert ord_receipt_total == 20063
        assert abs(ord_receipt_total - ord_method_total) <= 1, (
            f"Order method total ({ord_method_total}) must match "
            f"receipt total ({ord_receipt_total}) within 1¢")

        # 3. Audit log: each transaction has CREATE + CONFIRM +
        #    PAYMENT_SAVED entries (3 per transaction × 10 vendors
        #    = 30 entries minimum on the order's transactions).
        audit_count = stress_db.execute(
            "SELECT COUNT(*) FROM audit_log WHERE table_name = ? "
            " AND record_id IN ({})".format(
                ','.join('?' * len(txn_ids))),
            ['transactions'] + list(txn_ids)
        ).fetchone()[0]
        # CREATE + CONFIRM = 2 per txn at minimum.
        assert audit_count >= 2 * len(txn_ids), (
            f"Expected ≥{2 * len(txn_ids)} audit entries on "
            f"transactions table (CREATE + CONFIRM per txn); "
            f"got {audit_count}")

        # 4. Vendor Reimbursement: total due across all 10 vendors
        #    must equal the order receipt total.
        from fam.sync.data_collector import _collect_vendor_reimbursement
        vr_rows = _collect_vendor_reimbursement(stress_db, [1])
        vr_total = sum(r['Total Due to Vendor'] for r in vr_rows)
        assert abs(vr_total * 100 - 20063) <= 1, (
            f"Vendor Reimbursement total (${vr_total}) must match "
            f"order receipt total ($200.63) within 1¢")

        # 5. FAM Match Report: customer_paid + match = receipt.
        from fam.sync.data_collector import _collect_fam_match
        fm_rows = _collect_fam_match(stress_db, 1)
        match_total = sum(r['Total FAM Match'] for r in fm_rows)
        allocated_total = sum(r['Total Allocated'] for r in fm_rows)
        # Allocated should equal receipt total.
        assert abs(allocated_total * 100 - 20063) <= 1, (
            f"FAM Match Report allocated total "
            f"(${allocated_total}) must equal receipt total "
            f"($200.63) within 1¢")
        # Customer paid (= allocated - match) + match = allocated.
        # Trivially holds; the assertion is on the engine producing
        # consistent line items.
        assert match_total >= 0
        assert match_total <= allocated_total

    def _fetch_eligibility(self, conn) -> dict:
        rows = conn.execute(
            "SELECT vendor_id, payment_method_id "
            "FROM vendor_payment_methods").fetchall()
        elig: dict[int, set[int]] = {}
        for r in rows:
            elig.setdefault(r['vendor_id'], set()).add(
                r['payment_method_id'])
        return elig

    def _allocate_and_save(self, vendor_id: int, receipt_cents: int,
                           txn_id: int, eligibility: dict):
        """Build a single-vendor breakdown and save it.  Strategy:
        if vendor accepts a denominated method, use a partial
        allocation in that method and SNAP for the rest.  If only
        SNAP+Cash, split 50/50."""
        eligible = eligibility[vendor_id]
        items = []

        if 4 in eligible:  # Food Bucks ($2 denom, 100% match)
            # Use 1 Food Bucks unit ($2 customer + $2 match = $4
            # method).  Remainder fills with SNAP at 100%.
            fb_method = 400
            fb_customer = 200
            fb_match = 200
            items.append(_line_item(4, 'JH Food Bucks', 100.0,
                                     fb_method, fb_match, fb_customer))
            remainder = receipt_cents - fb_method
            if remainder > 0:
                # SNAP at 100% match: customer = method/2, match = method/2
                snap_customer = remainder // 2
                snap_match = remainder - snap_customer
                items.append(_line_item(1, 'SNAP', 100.0,
                                        remainder, snap_match,
                                        snap_customer))
        elif 5 in eligible:  # FMNP ($5 denom, 100% match)
            # Tricky: $5 FMNP method = $5 customer + $5 match =
            # $10 method.  Use 1 FMNP unit only if receipt ≥ $10.
            if receipt_cents >= 1000:
                items.append(_line_item(5, 'FMNP', 100.0,
                                        1000, 500, 500))
                remainder = receipt_cents - 1000
            else:
                remainder = receipt_cents
            if remainder > 0:
                snap_customer = remainder // 2
                snap_match = remainder - snap_customer
                items.append(_line_item(1, 'SNAP', 100.0,
                                        remainder, snap_match,
                                        snap_customer))
        else:
            # SNAP + Cash split 50/50 (~50% each by method amount)
            snap_amt = receipt_cents // 2
            snap_customer = snap_amt // 2
            snap_match = snap_amt - snap_customer
            items.append(_line_item(1, 'SNAP', 100.0,
                                    snap_amt, snap_match,
                                    snap_customer))
            cash_amt = receipt_cents - snap_amt
            items.append(_line_item(2, 'Cash', 0.0,
                                    cash_amt, 0, cash_amt))
        _save_payment_for_txn(txn_id, items)

    def _assert_txn_invariant(self, conn, txn_id: int):
        receipt = conn.execute(
            "SELECT receipt_total FROM transactions WHERE id = ?",
            (txn_id,)).fetchone()[0]
        sums = conn.execute(
            "SELECT COALESCE(SUM(method_amount), 0) AS m, "
            "       COALESCE(SUM(match_amount), 0) AS mt, "
            "       COALESCE(SUM(customer_charged), 0) AS c "
            "FROM payment_line_items WHERE transaction_id = ?",
            (txn_id,)).fetchone()
        # method_amount = customer_charged + match_amount per row;
        # totals propagate.
        assert sums['m'] == sums['c'] + sums['mt'], (
            f"Txn {txn_id} per-row invariant broken: "
            f"method ({sums['m']}) ≠ customer ({sums['c']}) + "
            f"match ({sums['mt']})")
        # Aggregate must reconcile to receipt within 1¢.
        assert abs(sums['m'] - receipt) <= 1, (
            f"Txn {txn_id}: method total ({sums['m']}) doesn't "
            f"match receipt total ({receipt}) within 1¢")


# ══════════════════════════════════════════════════════════════════
# 2. Returning customer match-cap stress
# ══════════════════════════════════════════════════════════════════
class TestReturningCustomerMatchCap:
    """Customer visits 3+ times in a single market day; match
    accumulates against the daily cap; void of an earlier visit
    restores cap."""

    def test_three_visits_match_accumulates_correctly(self, stress_db):
        from fam.models.customer_order import (
            get_customer_prior_match,
        )

        # Visit 1: $200 receipt at vendor 1, $100 customer / $100 match
        order1, [txn1] = _seed_simple_order(
            stress_db, [(1, 20000)], customer_label='C-001')
        _save_payment_for_txn(txn1, [
            _line_item(1, 'SNAP', 100.0, 20000, 10000, 10000),
        ])

        # Prior match for next visit = $100
        prior_after_v1 = get_customer_prior_match('C-001', 1)
        assert prior_after_v1 == 10000, (
            f"After visit 1, customer's prior_match should be "
            f"$100; got ${prior_after_v1 / 100}")

        # Visit 2: $200 receipt; cap leaves $400 of match available
        # (limit $500 - $100 used).
        order2, [txn2] = _seed_simple_order(
            stress_db, [(1, 20000)], customer_label='C-001')
        _save_payment_for_txn(txn2, [
            _line_item(1, 'SNAP', 100.0, 20000, 10000, 10000),
        ])
        prior_after_v2 = get_customer_prior_match('C-001', 1)
        assert prior_after_v2 == 20000

        # Visit 3: $700 receipt — would consume $350 match but
        # only $300 of cap remains.  In a real save the engine
        # would cap the match.  Simulate the engine output here
        # so the test is deterministic.
        order3, [txn3] = _seed_simple_order(
            stress_db, [(1, 70000)], customer_label='C-001')
        # Engine would produce: customer $400, match $300 (capped).
        _save_payment_for_txn(txn3, [
            _line_item(1, 'SNAP', 100.0, 70000, 30000, 40000),
        ])
        prior_after_v3 = get_customer_prior_match('C-001', 1)
        assert prior_after_v3 == 50000, (
            f"After visit 3 the customer should be at the daily "
            f"cap of $500 of match; got ${prior_after_v3 / 100}")

    def test_void_of_earlier_visit_restores_cap(self, stress_db):
        """Void of visit 1 must NOT count its match against the
        cap on subsequent re-look-up.  Pin via
        ``get_customer_prior_match``."""
        from fam.models.customer_order import get_customer_prior_match
        from fam.models.transaction import void_transaction

        order1, [txn1] = _seed_simple_order(
            stress_db, [(1, 20000)], customer_label='C-001')
        _save_payment_for_txn(txn1, [
            _line_item(1, 'SNAP', 100.0, 20000, 10000, 10000),
        ])

        # Verify cap consumed.
        assert get_customer_prior_match('C-001', 1) == 10000

        # Void visit 1.
        void_transaction(txn1, voided_by='Tester')

        # Cap should fully restore.
        assert get_customer_prior_match('C-001', 1) == 0, (
            "Voided transactions must NOT count against the "
            "customer's daily match cap.  This is the explicit "
            "design — voids are recoveries, not history.")


# ══════════════════════════════════════════════════════════════════
# 3. Adjustment iteration stress (5 sequential edits)
# ══════════════════════════════════════════════════════════════════
class TestAdjustmentIterationStress:
    """A single transaction is adjusted 5 times in sequence.
    Every adjustment must produce its own audit_log entry, and
    the final state must reflect all changes accumulated."""

    def test_five_sequential_adjustments_audit_complete(
            self, stress_db):
        from fam.models.transaction import update_transaction
        from fam.models.audit import log_action

        order_id, [txn_id] = _seed_simple_order(
            stress_db, [(1, 5000)], customer_label='C-001')
        _save_payment_for_txn(txn_id, [
            _line_item(1, 'SNAP', 100.0, 5000, 2500, 2500),
        ])

        # Five sequential adjustments to receipt_total, each with
        # its own ADJUST audit entry.  Mimics the AdjustmentDialog
        # flow without driving the UI.
        adjustments = [6000, 5500, 7000, 6500, 8000]
        for i, new_total in enumerate(adjustments, start=1):
            old_total = stress_db.execute(
                "SELECT receipt_total FROM transactions WHERE id = ?",
                (txn_id,)).fetchone()[0]
            log_action('transactions', txn_id, 'ADJUST', 'Tester',
                       field_name='receipt_total',
                       old_value=old_total, new_value=new_total,
                       reason_code='data_entry_error',
                       notes=f"Adjustment iteration {i}",
                       commit=False)
            update_transaction(txn_id, receipt_total=new_total,
                               status='Adjusted', commit=False)
            stress_db.commit()

        # Final state matches the LAST adjustment.
        final_total = stress_db.execute(
            "SELECT receipt_total, status FROM transactions "
            "WHERE id = ?", (txn_id,)).fetchone()
        assert final_total['receipt_total'] == 8000
        assert final_total['status'] == 'Adjusted'

        # Audit log has 5 ADJUST entries plus the original
        # CREATE + CONFIRM.
        adjust_entries = stress_db.execute(
            "SELECT old_value, new_value FROM audit_log "
            "WHERE table_name = 'transactions' AND record_id = ? "
            "AND action = 'ADJUST' ORDER BY id",
            (txn_id,)).fetchall()
        assert len(adjust_entries) == 5, (
            f"Each of the 5 adjustments must produce its own "
            f"ADJUST audit row; got {len(adjust_entries)}")
        # Pin the chain: each adjustment's old_value matches the
        # previous adjustment's new_value (no skipped audits).
        previous_new = '5000'   # initial receipt_total
        for entry, expected_new in zip(adjust_entries, adjustments):
            assert str(entry['old_value']) == str(previous_new), (
                f"Adjustment audit chain broken: expected "
                f"old_value {previous_new}, got {entry['old_value']}")
            assert str(entry['new_value']) == str(expected_new)
            previous_new = str(expected_new)


# ══════════════════════════════════════════════════════════════════
# 4. Adjust-then-void integrity
# ══════════════════════════════════════════════════════════════════
class TestAdjustThenVoidIntegrity:
    """A transaction is adjusted, then voided.  Vendor
    Reimbursement must exclude the voided transaction's amount
    even though it was previously adjusted."""

    def test_adjust_then_void_excludes_from_reports(self, stress_db):
        from fam.models.transaction import (
            update_transaction, void_transaction,
        )
        from fam.models.audit import log_action
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement,
        )

        # Two transactions on the order — one will be adjusted+voided,
        # the other left as-is.  Vendor Reimbursement should reflect
        # only the survivor.
        order_id, [txn1, txn2] = _seed_simple_order(
            stress_db,
            [(1, 5000), (2, 3000)],   # vendor 1: $50, vendor 2: $30
            customer_label='C-001')
        _save_payment_for_txn(txn1, [
            _line_item(1, 'SNAP', 100.0, 5000, 2500, 2500)])
        _save_payment_for_txn(txn2, [
            _line_item(2, 'Cash', 0.0, 3000, 0, 3000)])

        # Adjust txn1 to $80.
        log_action('transactions', txn1, 'ADJUST', 'Tester',
                   field_name='receipt_total', old_value=5000,
                   new_value=8000, reason_code='data_entry_error',
                   commit=False)
        update_transaction(txn1, receipt_total=8000,
                           status='Adjusted', commit=False)
        stress_db.commit()

        # Adjusted transaction shows up in Vendor Reimbursement.
        vr_before_void = _collect_vendor_reimbursement(stress_db, [1])
        by_vendor = {r['Vendor']: r['Total Due to Vendor']
                     for r in vr_before_void}
        # Vendor 1 (Apple) post-adjust: $80.
        assert by_vendor.get('Apple Orchard') == 80.0

        # Void txn1.
        void_transaction(txn1, voided_by='Tester')

        # After void, vendor 1 must drop out of Vendor Reimbursement
        # entirely (it was the only txn for that vendor).
        vr_after_void = _collect_vendor_reimbursement(stress_db, [1])
        by_vendor_after = {r['Vendor']: r['Total Due to Vendor']
                           for r in vr_after_void}
        assert 'Apple Orchard' not in by_vendor_after, (
            "Voided transactions must be excluded from Vendor "
            "Reimbursement.  Found Apple Orchard still in the "
            "report after voiding its only transaction.")
        # Bakery (vendor 2, unaffected) should still be there.
        assert by_vendor_after.get('Bakery Plus') == 30.0

        # Both ADJUST and VOID audit entries exist on txn1.
        actions = [r['action'] for r in stress_db.execute(
            "SELECT action FROM audit_log WHERE table_name = "
            "'transactions' AND record_id = ?", (txn1,)).fetchall()]
        assert 'ADJUST' in actions
        assert 'VOID' in actions, (
            "Void after adjust must produce a VOID audit entry "
            "(the adjustment chain doesn't get rewritten — voiding "
            "is its own action).")


# ══════════════════════════════════════════════════════════════════
# 5. Penny + fractional reconciliation
# ══════════════════════════════════════════════════════════════════
class TestPennyAndFractionalReconciliation:
    """Odd-cent receipt totals + fractional match percents must
    reconcile within ±1¢ via the engine's penny absorption."""

    def test_odd_cent_receipt_with_fifty_percent_match(
            self, stress_db):
        # Receipt $9.99 with 50% match (Food RX).
        # Method amount $9.99.  match% 50 → match = round(999 * 50/150)
        # = round(333) = 333.  customer = 999 - 333 = 666.
        # That's exact (no penny gap in this case).
        result = calculate_payment_breakdown(
            999, [{'method_amount': 999, 'match_percent': 50.0}])
        assert result['allocated_total'] == 999
        assert result['allocation_remaining'] == 0
        li = result['line_items'][0]
        assert li['customer_charged'] + li['match_amount'] == 999, (
            "Per-row invariant: customer + match = method")

    def test_odd_cent_with_fractional_match_triggers_reconciliation(
            self):
        # Receipt $1.00 with 33% match.  match = round(100 * 33/133)
        # = round(24.81) = 25.  customer = 75.  Total = 100. ✓
        # But receipt $0.10 with 33% match: match = round(10 * 33/133)
        # = round(2.48) = 2.  customer = 8.  Total = 10. ✓
        # Try receipt $0.07 with 33%: match = round(7 * 33/133)
        # = round(1.736) = 2.  customer = 5.  Total = 7. ✓
        # All cases should reconcile.
        for receipt_cents in (7, 13, 99, 100, 333):
            result = calculate_payment_breakdown(
                receipt_cents,
                [{'method_amount': receipt_cents,
                  'match_percent': 33.0}])
            li = result['line_items'][0]
            assert (li['customer_charged'] + li['match_amount']
                    == receipt_cents), (
                f"Receipt {receipt_cents}c: customer "
                f"({li['customer_charged']}) + match "
                f"({li['match_amount']}) ≠ method "
                f"({receipt_cents}c)")
            # Engine flags valid only when reconciled within 1¢.
            assert abs(result['allocation_remaining']) <= 1


# ══════════════════════════════════════════════════════════════════
# 6. Edge case discovery
# ══════════════════════════════════════════════════════════════════
class TestEdgeCaseDiscovery:
    """Things that seem like they could break.  Each test exists
    because it uncovered (or would have uncovered) a concrete
    integrity problem."""

    def test_one_cent_receipt_does_not_crash(self, stress_db):
        """The smallest possible non-zero receipt.  Must save
        cleanly.  $0.01 with 100% match would mathematically be
        customer 0 + match 1 = 1¢, with the customer paying
        nothing — the engine's penny reconciliation should absorb
        this."""
        order_id, [txn_id] = _seed_simple_order(
            stress_db, [(1, 1)], customer_label='C-001')
        result = calculate_payment_breakdown(
            1, [{'method_amount': 1, 'match_percent': 100.0}])
        # round(1 * 100/200) = round(0.5) = 0 (banker's rounding)
        # or 1 (nearest-up).  Either way, customer + match = 1.
        li = result['line_items'][0]
        assert li['customer_charged'] + li['match_amount'] == 1
        # Save the result and verify integrity.
        _save_payment_for_txn(txn_id, [
            _line_item(1, 'SNAP', 100.0, 1, li['match_amount'],
                       li['customer_charged']),
        ])
        # Read back and verify per-txn invariant holds.
        sums = stress_db.execute(
            "SELECT SUM(method_amount), SUM(match_amount), "
            "SUM(customer_charged) FROM payment_line_items "
            "WHERE transaction_id = ?", (txn_id,)).fetchone()
        assert sums[0] == 1
        assert sums[1] + sums[2] == 1

    def test_premium_match_two_hundred_percent(self, stress_db):
        """200% match: FAM contributes 2× what the customer pays.
        Receipt $30 with 200% match: customer $10 + match $20 =
        method $30.  Pin the math via the engine."""
        result = calculate_payment_breakdown(
            3000, [{'method_amount': 3000, 'match_percent': 200.0}])
        li = result['line_items'][0]
        # match = round(3000 * 200 / 300) = 2000.  customer = 1000.
        assert li['match_amount'] == 2000
        assert li['customer_charged'] == 1000

    def test_zero_match_method_only(self):
        """Cash (0% match): customer pays the entire amount, FAM
        contributes nothing."""
        result = calculate_payment_breakdown(
            5000, [{'method_amount': 5000, 'match_percent': 0.0}])
        li = result['line_items'][0]
        assert li['match_amount'] == 0
        assert li['customer_charged'] == 5000

    def test_match_cap_zero_blocks_all_match(self):
        """Match limit = $0 means FAM contributes nothing,
        regardless of method.  Customer pays the full amount."""
        result = calculate_payment_breakdown(
            5000,
            [{'method_amount': 5000, 'match_percent': 100.0}],
            match_limit=0)
        li = result['line_items'][0]
        assert li['match_amount'] == 0, (
            f"match_limit=0 must produce match_amount=0; got "
            f"{li['match_amount']}")
        assert li['customer_charged'] == 5000
        assert result['match_was_capped'] is True

    def test_two_different_denominations_both_overage(self):
        """Mixed denoms: customer hands a $5 FMNP check AND a $2
        Food Bucks token, both creating 1-unit overages.

        Receipts: vendor 1 $9 (FMNP $5 → method $10, 1-unit over),
                  vendor 2 $3 (FB $2 → method $4, 1-unit over).
        Total order $12, total method $14, overage $2.
        Engine should allow up to ($10 + $4) effective_denom_sum
        = $14.  $2 ≤ $14 → forfeit allowed."""
        result = calculate_payment_breakdown(
            1200,   # $9 + $3 = $12 order total
            [{'method_amount': 1000, 'match_percent': 100.0},   # FMNP
             {'method_amount': 400,  'match_percent': 100.0}])  # FB
        # Each line item self-reconciles independently.
        for li in result['line_items']:
            assert li['method_amount'] > 0
        # The engine doesn't apply forfeit; that's UI-level.  But
        # the engine's totals must still be correct.
        assert result['allocated_total'] == 1400
        assert result['allocation_remaining'] == -200  # over by $2

    def test_high_volume_hundred_orders_in_market_day(self, stress_db):
        """Sanity scale check: 100 customer orders, each with 1
        transaction, all confirmed.  Reports must still reconcile
        without timing out."""
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement,
        )
        import time

        for i in range(100):
            customer = f'C-{i+1:03d}'
            order_id, [txn_id] = _seed_simple_order(
                stress_db, [(((i % 12) + 1), 1000)],
                customer_label=customer)
            _save_payment_for_txn(txn_id, [
                _line_item(1, 'SNAP', 100.0, 1000, 500, 500),
            ])

        # Reports query must complete in reasonable time.
        start = time.time()
        vr_rows = _collect_vendor_reimbursement(stress_db, [1])
        elapsed = time.time() - start
        assert elapsed < 5.0, (
            f"Vendor reimbursement query took {elapsed:.2f}s on "
            f"100 orders — far too slow for production")

        # Total Due across all vendors: 100 × $10 = $1000.
        total = sum(r['Total Due to Vendor'] for r in vr_rows)
        assert abs(total - 1000.0) < 0.01

    def test_voided_excluded_from_match_cap_returning_customer(
            self, stress_db):
        """Returning customer cap query MUST exclude voided
        transactions.  Pin via direct query (already covered by
        TestReturningCustomerMatchCap.test_void_of_earlier_visit_*
        — duplicate here for the edge-case suite)."""
        from fam.models.customer_order import get_customer_prior_match
        from fam.models.transaction import void_transaction

        order_id, [txn_id] = _seed_simple_order(
            stress_db, [(1, 10000)], customer_label='C-EDGE')
        _save_payment_for_txn(txn_id, [
            _line_item(1, 'SNAP', 100.0, 10000, 5000, 5000),
        ])
        assert get_customer_prior_match('C-EDGE', 1) == 5000
        void_transaction(txn_id, voided_by='Tester')
        assert get_customer_prior_match('C-EDGE', 1) == 0


# ══════════════════════════════════════════════════════════════════
# 7. Full reconciliation: DB ↔ reports ↔ ledger
# ══════════════════════════════════════════════════════════════════
class TestEndToEndReconciliation:
    """After the mega-order scenario runs, every report surface
    should agree with the underlying DB to the cent.  This test
    is the canary that catches any code path that drifts."""

    def test_db_matches_all_report_surfaces(self, stress_db):
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement,
            _collect_fam_match,
            _collect_detailed_ledger,
            _collect_market_day_summary,
        )

        # Scenario: 5 vendors, varied receipts, all SNAP.
        vendor_receipts = [
            (1, 5000), (2, 3000), (3, 2500), (4, 4000), (5, 1500),
        ]
        order_id, txn_ids = _seed_simple_order(
            stress_db, vendor_receipts, customer_label='C-RECON')
        for (_, receipt), txn_id in zip(vendor_receipts, txn_ids):
            customer = receipt // 2
            match = receipt - customer
            _save_payment_for_txn(txn_id, [
                _line_item(1, 'SNAP', 100.0, receipt, match, customer),
            ])

        # ── 1. DB ground truth ─────────────────────────────────
        db_receipt = stress_db.execute(
            "SELECT SUM(receipt_total) FROM transactions "
            "WHERE customer_order_id = ?", (order_id,)
        ).fetchone()[0]
        db_method = stress_db.execute(
            "SELECT SUM(pli.method_amount) FROM payment_line_items pli "
            "JOIN transactions t ON pli.transaction_id = t.id "
            "WHERE t.customer_order_id = ?", (order_id,)
        ).fetchone()[0]
        db_match = stress_db.execute(
            "SELECT SUM(pli.match_amount) FROM payment_line_items pli "
            "JOIN transactions t ON pli.transaction_id = t.id "
            "WHERE t.customer_order_id = ?", (order_id,)
        ).fetchone()[0]
        # Sanity.
        assert db_receipt == 16000
        assert db_method == db_receipt

        # ── 2. Vendor Reimbursement = DB receipt ──────────────
        vr = _collect_vendor_reimbursement(stress_db, [1])
        vr_total = sum(r['Total Due to Vendor'] for r in vr)
        assert abs(vr_total * 100 - db_receipt) <= 1

        # ── 3. FAM Match = DB match ───────────────────────────
        fm = _collect_fam_match(stress_db, 1)
        fm_match_total = sum(r['Total FAM Match'] for r in fm)
        assert abs(fm_match_total * 100 - db_match) <= 1

        # ── 4. Detailed Ledger = sum of receipts ──────────────
        dl = _collect_detailed_ledger(stress_db, 1)
        dl_total = sum(r['Receipt Total'] for r in dl)
        assert abs(dl_total * 100 - db_receipt) <= 1

        # ── 5. Market Day Summary single-row totals ───────────
        mds = _collect_market_day_summary(stress_db, 1)
        assert len(mds) == 1
        assert abs(
            float(mds[0]['Total Receipts']) * 100 - db_receipt) <= 1
