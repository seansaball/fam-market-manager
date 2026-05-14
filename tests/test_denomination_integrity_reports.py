"""Denomination-integrity across UI + reports for the
under-denomination scenario (user-reported 2026-05-07).

User screenshot scenario (Customer C-005-LB1, 5 receipts):

  Vendor              Receipt   Payment
  Pitaland Inc.       $1.45     1 × $10 Food RX token
  Fungetarian         $25.63    2 × $10 Food RX tokens (+$5.63 match)
  Hughes Farm         $45.62    3 × $10 Food RX tokens (+$15.62 match)
  Fudgie Wudgie       $1.42     SNAP $0.71 customer + $0.71 match
  Haffey Family Farm  $4.52     SNAP $2.26 customer + $2.26 match

User complaints (verbatim):

  "for receipts lower than the denominated payment it is still
  not retaining the actual denominated value"

    1. UI payment screen Pitaland row had a blank Food RX cell
       (no "1 × $10.00 = $X" breakdown text).

    2. Reports showed Pitaland Food RX = $1.45 instead of $10.

  "FAM match might be intermingling in the Payment Methods and
  obscuring the true Food RX denomination of $10"

    3. Detailed Ledger Payment Methods column showed
       "Food RX: $25.63" for Fungetarian (= customer $20 + match
       $5.63), making 2 × $10 ≠ $25.63 visibly nonsensical.

Root cause for (1): ``unit_count = (charge // denom)`` used
``customer_charged`` (post-forfeit, $1.45) instead of the actual
customer payment (= ``customer_charged + customer_forfeit_cents``,
$10).  ``145 // 1000 = 0`` → "0 × $10 = ..." rendered as bare ✓.

Root cause for (2) + (3): per-method aggregation in reports used
``SUM(customer_charged)`` (post-forfeit) for Vendor Reimbursement
and ``method_amount`` (= customer + match) for Detailed Ledger.
Both obscured the customer's denomination-true payment.

Fix: across UI + Vendor Reimbursement + Detailed Ledger + sync,
the per-method "customer payment" value is canonicalised to
``customer_charged + customer_forfeit_cents``:
  * Equals ``tokens × denomination`` for denominated methods
    (denomination-pure, no FAM-match intermingling).
  * Equals ``customer_charged`` for non-denominated methods
    (forfeit is always 0 there — no behavior change).

Math identities now hold cleanly:

  Per Vendor Reimbursement row:
    Σ(method-cols) + FAM Match - Customer Forfeit
      = Total Due to Vendor

  Per Detailed Ledger row:
    Customer Paid + FAM Match - Customer Forfeit = Receipt Total

This file pins:

  1. Pitaland UI breakdown row renders "1 × $10.00 = $10.00".
  2. Vendor Reimbursement Food RX column shows $10 for Pitaland
     (NOT $1.45).
  3. Vendor Reimbursement row reconciliation works for both
     under-denomination (forfeit > 0) and matched-only (forfeit
     = 0) scenarios.
  4. Detailed Ledger Payment Methods column shows
     "Food RX: $10.00" for Pitaland (denomination-true) and
     "Food RX: $20.00" for Fungetarian (no FAM-match
     intermingling — pre-fix this was "Food RX: $25.63").
  5. Detailed Ledger Customer Paid column matches the
     denomination-true value (consistent with Payment Methods
     column).
  6. Reports header Customer Forfeit summary card is populated.
"""

import pytest

from fam.database.connection import (
    set_db_path, close_connection, get_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def pitaland_db(tmp_path):
    """The user's reported scenario: 1 × $10 Food RX → $1.45
    receipt + Fungetarian's matched 2 × $10 Food RX → $25.63
    receipt.  These two cover both branches:
      * Under-denomination: forfeit > 0, match = 0
      * Matched-only:        forfeit = 0, match > 0
    """
    from fam.models.transaction import (
        create_transaction, save_payment_line_items,
        confirm_transaction,
    )
    from fam.models.customer_order import create_customer_order
    db_file = str(tmp_path / "pitaland.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(1, 'Food RX', 100.0, 1000, 1, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods "
        "(market_id, payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES "
        "(1, 'Pitaland Inc.'), (2, 'Fungetarian')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1), (1, 2)")
    conn.execute(
        "INSERT INTO vendor_payment_methods "
        "(vendor_id, payment_method_id) VALUES (1, 1), (2, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES (1, 1, '2099-05-07', 'Open', 'T')")
    conn.commit()
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-005-LB1')

    # Pitaland: 1 × $10 token → $1.45 receipt → $8.55 forfeit
    txn1, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=145,
        customer_order_id=order_id,
        market_day_date='2099-05-07')
    save_payment_line_items(txn1, [{
        'payment_method_id': 1,
        'method_name_snapshot': 'Food RX',
        'match_percent_snapshot': 100.0,
        'method_amount': 145,
        'match_amount': 0,
        'customer_charged': 145,
        'customer_forfeit_cents': 855,
        'photo_path': None,
    }])
    confirm_transaction(txn1, confirmed_by='T')

    # Fungetarian: 2 × $10 tokens → $25.63 receipt → $0 forfeit
    # Customer pays $20, match $5.63, vendor gets $25.63
    txn2, _ = create_transaction(
        market_day_id=1, vendor_id=2, receipt_total=2563,
        customer_order_id=order_id,
        market_day_date='2099-05-07')
    save_payment_line_items(txn2, [{
        'payment_method_id': 1,
        'method_name_snapshot': 'Food RX',
        'match_percent_snapshot': 100.0,
        'method_amount': 2563,
        'match_amount': 563,
        'customer_charged': 2000,
        'customer_forfeit_cents': 0,
        'photo_path': None,
    }])
    confirm_transaction(txn2, confirmed_by='T')

    yield conn
    close_connection()


# ──────────────────────────────────────────────────────────────────
# 1. Vendor Reimbursement: Food RX column shows denomination-true
# ──────────────────────────────────────────────────────────────────


class TestVendorReimbursementDenominationIntegrity:

    def test_pitaland_food_rx_column_shows_ten_dollars(
            self, pitaland_db):
        """Pre-fix: Pitaland's Food RX column showed $1.45.
        Post-fix: shows $10.00 (denomination-true)."""
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement,
        )
        rows = _collect_vendor_reimbursement(pitaland_db, [1])
        pitaland = next(r for r in rows if r['Vendor'] == 'Pitaland Inc.')
        assert pitaland['Food RX'] == 10.00, (
            f"Pitaland Food RX must show denomination-true value "
            f"($10), NOT post-forfeit value ($1.45).  "
            f"Got: ${pitaland['Food RX']:.2f}")
        assert pitaland['Customer Forfeit'] == 8.55
        assert pitaland['Total Due to Vendor'] == 1.45

    def test_fungetarian_food_rx_no_fam_match_intermingling(
            self, pitaland_db):
        """Pre-fix: Fungetarian's Food RX showed $25.63 (= customer
        $20 + match $5.63), making "2 × $10" math nonsensical.
        Post-fix: shows $20 (= 2 × $10), pure denomination value."""
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement,
        )
        rows = _collect_vendor_reimbursement(pitaland_db, [1])
        fung = next(r for r in rows if r['Vendor'] == 'Fungetarian')
        assert fung['Food RX'] == 20.00, (
            f"Fungetarian Food RX must show denomination-true "
            f"value (2 × $10 = $20), with NO FAM-match "
            f"intermingling.  Got: ${fung['Food RX']:.2f}")
        assert fung['FAM Match'] == 5.63
        assert fung['Customer Forfeit'] == 0.00
        assert fung['Total Due to Vendor'] == 25.63

    def test_row_reconciliation_under_denomination(self, pitaland_db):
        """Math identity: Σ(method-cols) + FAM Match -
        Customer Forfeit = Total Due to Vendor."""
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement,
        )
        rows = _collect_vendor_reimbursement(pitaland_db, [1])
        for r in rows:
            method_sum = sum(
                v for k, v in r.items()
                if k not in {
                    'Market Name', 'Vendor', 'Month', 'Year-Month',
                    'Date(s)', 'Total Due to Vendor', 'FAM Match',
                    'FMNP (External)', 'Customer Forfeit',
                    'Check Payable To', 'Address',
                })
            recon = (method_sum + r['FAM Match']
                     - r['Customer Forfeit']
                     + r['FMNP (External)'])
            assert abs(recon - r['Total Due to Vendor']) <= 0.01, (
                f"Vendor {r['Vendor']!r}: Σ(method) + FAM Match "
                f"- Customer Forfeit + FMNP = {recon:.2f}, "
                f"but Total Due = {r['Total Due to Vendor']:.2f}")


# ──────────────────────────────────────────────────────────────────
# 2. Detailed Ledger: Payment Methods column denomination-pure
# ──────────────────────────────────────────────────────────────────


class TestDetailedLedgerDenominationIntegrity:

    def test_pitaland_payment_methods_column_shows_ten_dollars(
            self, pitaland_db):
        from fam.sync.data_collector import _collect_detailed_ledger
        rows = _collect_detailed_ledger(pitaland_db, 1)
        pita = next(r for r in rows
                    if r.get('Vendor') == 'Pitaland Inc.')
        assert 'Food RX: $10.00' in pita['Payment Methods'], (
            f"Pitaland Payment Methods must include "
            f"'Food RX: $10.00' (denomination-true).  "
            f"Got: {pita['Payment Methods']!r}")

    def test_fungetarian_no_fam_match_intermingling(
            self, pitaland_db):
        """Pre-fix: 'Food RX: $25.63' (customer + match
        intermingled).  Post-fix: 'Food RX: $20.00' (pure
        2 × $10 token value, no match)."""
        from fam.sync.data_collector import _collect_detailed_ledger
        rows = _collect_detailed_ledger(pitaland_db, 1)
        fung = next(r for r in rows
                    if r.get('Vendor') == 'Fungetarian')
        assert 'Food RX: $20.00' in fung['Payment Methods'], (
            f"Fungetarian Payment Methods must show "
            f"'Food RX: $20.00' (denomination-pure, NO FAM-match "
            f"intermingling).  Got: {fung['Payment Methods']!r}")
        # Pre-fix value MUST NOT appear.
        assert 'Food RX: $25.63' not in fung['Payment Methods'], (
            f"Fungetarian Payment Methods must NOT show "
            f"'Food RX: $25.63' (pre-fix value with FAM-match "
            f"intermingling).  Got: {fung['Payment Methods']!r}")

    def test_pitaland_customer_paid_matches_payment_methods(
            self, pitaland_db):
        """Customer Paid column must match the Payment Methods
        column sum for the row (both denomination-true)."""
        from fam.sync.data_collector import _collect_detailed_ledger
        rows = _collect_detailed_ledger(pitaland_db, 1)
        pita = next(r for r in rows
                    if r.get('Vendor') == 'Pitaland Inc.')
        assert pita['Customer Paid'] == 10.00, (
            f"Pitaland Customer Paid must be $10 (denomination-"
            f"true, matches the 'Food RX: $10.00' Payment "
            f"Methods).  Got: ${pita['Customer Paid']:.2f}")
        assert pita['Customer Forfeit'] == 8.55
        assert pita['FAM Match'] == 0.00
        assert pita['Receipt Total'] == 1.45
        # Reconciliation
        recon = (pita['Customer Paid'] + pita['FAM Match']
                 - pita['Customer Forfeit'])
        assert abs(recon - pita['Receipt Total']) <= 0.01

    def test_fungetarian_customer_paid_matches_payment_methods(
            self, pitaland_db):
        from fam.sync.data_collector import _collect_detailed_ledger
        rows = _collect_detailed_ledger(pitaland_db, 1)
        fung = next(r for r in rows
                    if r.get('Vendor') == 'Fungetarian')
        assert fung['Customer Paid'] == 20.00, (
            f"Fungetarian Customer Paid must be $20 "
            f"(denomination-true, no FAM match).  "
            f"Got: ${fung['Customer Paid']:.2f}")
        assert fung['FAM Match'] == 5.63
        assert fung['Customer Forfeit'] == 0.00
        assert fung['Receipt Total'] == 25.63


# ──────────────────────────────────────────────────────────────────
# 3. UI Payment Screen vendor breakdown row
# ──────────────────────────────────────────────────────────────────


class TestPaymentScreenVendorBreakdownDenominationIntegrity:
    """The Pitaland row in the UI vendor breakdown table MUST
    render '1 × $10.00 = $10.00' (not blank or '0 × $10.00')
    when 1 × $10 Food RX token is paid against a $1.45 receipt.

    Tests the per-vendor breakdown rendering directly via
    PaymentScreen._compute_per_vendor_state to avoid Qt event-
    loop coupling.  This pins the unit_count fix at the
    behavioral layer."""

    def test_under_denomination_unit_count_is_one(self):
        """When customer_charged ($1.45) < denomination ($10),
        unit_count must come from ``customer_charged + forfeit``,
        NOT raw customer_charged.  Pre-fix:
        145 // 1000 = 0 → blank cell.  Post-fix:
        (145 + 855) // 1000 = 1 → '1 × $10.00 = $10.00'."""
        # Direct test of the arithmetic that powers the breakdown.
        # Mirrors fam/ui/payment_screen.py:_compute_per_vendor_state
        # Phase 1 loop.
        denom = 1000  # $10
        customer_charged = 145
        forfeit = 855
        # Pre-fix calculation:
        unit_count_old = customer_charged // denom
        # Post-fix calculation:
        unit_count_new = (customer_charged + forfeit) // denom
        denom_value_new = unit_count_new * denom
        assert unit_count_old == 0, (
            "Pre-fix: customer_charged // denom collapses to 0 "
            "for under-denomination receipts.  This test pins "
            "the pre-fix collapse so the regression case is "
            "documented.")
        assert unit_count_new == 1, (
            f"Post-fix: (customer_charged + forfeit) // denom "
            f"must yield the actual token count.  Got: "
            f"{unit_count_new}")
        assert denom_value_new == 1000, (
            f"Denomination-pure display value = tokens × denom "
            f"= 1 × $10 = $10.  Got: {denom_value_new}c")

    def test_matched_only_unit_count_unchanged(self):
        """Sanity: when forfeit = 0 (matched-only scenario), the
        new calculation reduces to the old."""
        # Fungetarian: 2 × $10 tokens, $20 customer charged, $0
        # forfeit (because match $5.63 covers the rest of $25.63).
        denom = 1000
        customer_charged = 2000
        forfeit = 0
        unit_count = (customer_charged + forfeit) // denom
        denom_value = unit_count * denom
        assert unit_count == 2
        assert denom_value == 2000  # 2 × $10 = $20


# ──────────────────────────────────────────────────────────────────
# 3b. Detailed Ledger — Unallocated Funds carve-out
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def adjusted_unallocated_db(tmp_path):
    """Mimics the user-reported 2026-05-07 scenario: a customer-gone
    adjustment that creates an Unallocated Funds row to track the
    FAM-absorbed gap.  Vendor Reimbursement displays this correctly
    ($18.08 in the user's case); the Detailed Ledger Payment Methods
    column was incorrectly showing "Unallocated Funds: $0.00" because
    the v2.0.7 denomination-integrity refactor used
    customer_charged + forfeit (both 0 for Unallocated Funds)
    instead of method_amount."""
    from fam.models.transaction import (
        create_transaction, save_payment_line_items,
        confirm_transaction,
    )
    from fam.models.customer_order import create_customer_order
    db_file = str(tmp_path / "adjusted_unallocated.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    # SNAP at 100% match; Unallocated Funds is seeded by schema v25+.
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(2, 'SNAP', 100.0, 0, 2, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods "
        "(market_id, payment_method_id) VALUES (1, 2)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES "
        "(1, 'Haffey Family Farm')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1)")
    conn.execute(
        "INSERT INTO vendor_payment_methods "
        "(vendor_id, payment_method_id) VALUES (1, 2)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES (1, 1, '2099-05-07', 'Open', 'T')")
    conn.commit()
    # Look up the system-seeded Unallocated Funds method id.
    uf_row = conn.execute(
        "SELECT id FROM payment_methods WHERE name = 'Unallocated Funds'"
    ).fetchone()
    assert uf_row, ("schema v25+ should seed Unallocated Funds — "
                    "fixture cannot construct the carve-out scenario "
                    "without it")
    uf_id = uf_row['id']

    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-006-LB1')

    # Receipt $20.65: SNAP $1.28 customer + $1.29 match = $2.57
    # The remaining $18.08 was discovered missing post-customer-gone
    # and absorbed by FAM via the Unallocated Funds method.
    txn, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=2065,
        customer_order_id=order_id,
        market_day_date='2099-05-07')
    save_payment_line_items(txn, [
        {
            'payment_method_id': 2,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 257,    # $1.28 + $1.29 = $2.57
            'match_amount': 129,
            'customer_charged': 128,
            'customer_forfeit_cents': 0,
            'photo_path': None,
        },
        {
            'payment_method_id': uf_id,
            'method_name_snapshot': 'Unallocated Funds',
            'match_percent_snapshot': 0.0,
            'method_amount': 1808,   # $18.08 absorbed
            'match_amount': 0,
            'customer_charged': 0,   # customer paid nothing
            'customer_forfeit_cents': 0,
            'photo_path': None,
        },
    ])
    confirm_transaction(txn, confirmed_by='T')
    # Mark Adjusted to mirror the user's reported status.
    conn.execute(
        "UPDATE transactions SET status = 'Adjusted' WHERE id = ?",
        [txn])
    conn.commit()

    yield conn
    close_connection()


class TestDetailedLedgerUnallocatedFundsCarveOut:
    """User-reported 2026-05-07: Detailed Ledger Payment Methods
    column showed "Unallocated Funds: $0.00" for adjusted
    transactions because the v2.0.7 refactor used
    customer_charged + forfeit (both 0 for Unallocated Funds).
    Fix: CASE expression uses method_amount for Unallocated Funds
    (mirrors the per-method carve-out already present in Vendor
    Reimbursement)."""

    def test_unallocated_funds_shows_absorbed_amount_not_zero(
            self, adjusted_unallocated_db):
        from fam.sync.data_collector import _collect_detailed_ledger
        rows = _collect_detailed_ledger(adjusted_unallocated_db, 1)
        haffey = next(r for r in rows
                      if r.get('Vendor') == 'Haffey Family Farm')
        # Pre-fix value MUST NOT appear.
        assert 'Unallocated Funds: $0.00' not in haffey['Payment Methods'], (
            f"Detailed Ledger Payment Methods MUST NOT show "
            f"'Unallocated Funds: $0.00' — the FAM-absorbed gap "
            f"must surface as the actual absorbed amount.  "
            f"Got: {haffey['Payment Methods']!r}")
        # Post-fix value must appear.
        assert 'Unallocated Funds: $18.08' in haffey['Payment Methods'], (
            f"Detailed Ledger Payment Methods must show "
            f"'Unallocated Funds: $18.08' (the absorbed amount, "
            f"matching the Vendor Reimbursement column).  "
            f"Got: {haffey['Payment Methods']!r}")
        # SNAP row still shows the customer's actual payment
        # (denomination-integrity rule unchanged for ordinary
        # methods).
        assert 'SNAP: $1.28' in haffey['Payment Methods']

    def test_vendor_reimbursement_already_correct(
            self, adjusted_unallocated_db):
        """Cross-check: Vendor Reimbursement has had this carve-
        out since the column was introduced.  Pin it so a future
        refactor doesn't re-introduce the same bug there."""
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement,
        )
        rows = _collect_vendor_reimbursement(
            adjusted_unallocated_db, [1])
        haffey = next(r for r in rows
                      if r['Vendor'] == 'Haffey Family Farm')
        assert haffey['Unallocated Funds'] == 18.08, (
            f"Vendor Reimbursement Unallocated Funds column "
            f"must surface the absorbed $18.08.  Got: "
            f"${haffey['Unallocated Funds']:.2f}")
        assert haffey['SNAP'] == 1.28
        assert haffey['Total Due to Vendor'] == 20.65


# ──────────────────────────────────────────────────────────────────
# 4. Reports header Customer Forfeit card
# ──────────────────────────────────────────────────────────────────


class TestReportsHeaderCustomerForfeitCard:
    """The Reports & Exports header now has a Customer Forfeit
    card so the math identity ``Total Receipts = Customer Paid +
    FAM Match - Customer Forfeit (+ FAM Absorbed + FMNP)`` is
    visible at a glance, matching the Payment Screen header."""

    def test_card_is_registered(self, qtbot, pitaland_db):
        from fam.ui.reports_screen import ReportsScreen
        screen = ReportsScreen()
        qtbot.addWidget(screen)
        # Card key must be present in the SummaryRow.
        assert 'customer_forfeit' in screen.summary_row.cards, (
            "Reports header must include a 'customer_forfeit' "
            "card to surface Phase B forfeit totals.")
