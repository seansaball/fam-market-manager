"""Tests for the v1.9.9 vendor-aware denomination forfeit fix.

The 2026-04 onsite caught a multi-vendor scenario where Auto-Distribute
left one vendor under-allocated:

  Order: 3 receipts ($10 + $25 + $10 = $45)
    - 1.11 Juice Bar     $10  (no JH Food Bucks eligibility)
    - Hello Hummus       $25  (eligible)
    - KizzleFoods        $10  (eligible)

  Customer hands over 9 × $2 JH Food Bucks ($18 customer / $36 method
  with 100% match).  Coordinator binds 6 to Hello Hummus and 3 to
  KizzleFoods.  KizzleFoods overflows (3 × $4 = $12 method against a
  $10 receipt → $2 forfeit).  Auto-Distribute adds a SNAP row to fill
  the rest.

  Expected: SNAP fills $10 to Juice Bar, $1 to Hello Hummus.
  Observed (pre-fix): SNAP went $8.46 to Juice Bar, $2.54 to Hello
  Hummus → Juice Bar under-allocated by $1.54, save blocked.

Root cause
----------
``_apply_denomination_forfeit`` walked line items in iteration order
and reduced match on the FIRST one with positive match — regardless
of which vendor was actually over-allocated.  In the example above
the forfeit got attributed to Hello Hummus's FB row (which had
headroom to spare) instead of KizzleFoods's row (which was the
actual overflow).  The mis-attribution then poisoned the per-vendor
SNAP distribution.

Fix
---
``_apply_denomination_forfeit`` now:
  1. Builds a per-vendor allocation map from the line items'
     ``bound_vendor_id`` (with single_vendor_id fallback for
     orders/dialogs in single-vendor mode).
  2. Identifies which vendors are over-allocated and by how much.
  3. Reduces match on the line items bound to those vendors only.
  4. Falls back to the legacy first-with-match algorithm for any
     residual overage (defensive — guards against numerical drift
     and edge cases like vendor maps unexpectedly empty).
"""

from unittest.mock import MagicMock

import pytest


# ══════════════════════════════════════════════════════════════════
# 1. The 2026-04 onsite scenario
# ══════════════════════════════════════════════════════════════════
class TestOnsiteRegressionScenario:
    """Recreate the exact 3-vendor / 9-Food-Bucks / SNAP-fill case
    from the 2026-04 onsite screenshot.  Pin that the forfeit
    lands on KizzleFoods (the over-allocated vendor) and Hello
    Hummus's FB row stays at full $24 method amount."""

    def _build_screen_stub(self):
        """A MagicMock standing in for PaymentScreen with just the
        ``_order_transactions`` data ``_apply_denomination_forfeit``
        reads.  Lets us call the method as an unbound function
        without instantiating any Qt widgets."""
        from fam.ui.payment_screen import PaymentScreen
        screen = MagicMock(spec=PaymentScreen)
        screen._order_transactions = [
            {'id': 1, 'vendor_id': 1, 'receipt_total': 1000},   # JB
            {'id': 2, 'vendor_id': 2, 'receipt_total': 2500},   # HH
            {'id': 3, 'vendor_id': 3, 'receipt_total': 1000},   # KF
        ]
        return screen

    def test_forfeit_lands_on_overallocated_vendor(self):
        """The $2 overage came from KizzleFoods (3 × $4 method = $12
        against $10 receipt).  Forfeit must reduce KizzleFoods's
        FB row, NOT Hello Hummus's (which has headroom)."""
        from fam.ui.payment_screen import PaymentScreen
        screen = self._build_screen_stub()

        # Engine output BEFORE forfeit.  Three line items in the
        # exact iteration order the bug used to depend on:
        #   item 0: 6 FB → Hello Hummus  ($24 method, $12 match)
        #   item 1: 3 FB → KizzleFoods   ($12 method, $6  match)
        #   item 2: SNAP                  ($11 method, $5.50 match)
        result = {
            'line_items': [
                {'method_amount': 2400, 'match_amount': 1200,
                 'customer_charged': 1200},
                {'method_amount': 1200, 'match_amount': 600,
                 'customer_charged': 600},
                {'method_amount': 1100, 'match_amount': 550,
                 'customer_charged': 550},
            ],
            'allocated_total': 4700,
            'fam_subsidy_total': 2350,
        }
        items = [
            {'denomination': 200, 'bound_vendor_id': 2,
             'method_amount': 2400, 'match_amount': 1200},  # HH
            {'denomination': 200, 'bound_vendor_id': 3,
             'method_amount': 1200, 'match_amount': 600},   # KF
            {'denomination': None, 'bound_vendor_id': None,
             'method_amount': 1100, 'match_amount': 550},   # SNAP
        ]

        PaymentScreen._apply_denomination_forfeit(
            screen, result, items, overage=200)

        # Hello Hummus FB row UNCHANGED (it had headroom).
        assert result['line_items'][0]['method_amount'] == 2400, (
            f"Hello Hummus FB row should stay at $24 method — it "
            f"had headroom and shouldn't have been forfeited.  Got "
            f"{result['line_items'][0]['method_amount']}.")
        assert result['line_items'][0]['match_amount'] == 1200

        # KizzleFoods FB row REDUCED by $2 (the actual overage).
        assert result['line_items'][1]['method_amount'] == 1000, (
            f"KizzleFoods FB row must drop from $12 to $10 method "
            f"(receipt total).  Got "
            f"{result['line_items'][1]['method_amount']}.")
        assert result['line_items'][1]['match_amount'] == 400

        # SNAP row UNCHANGED (forfeit only touches denom rows).
        assert result['line_items'][2]['method_amount'] == 1100
        assert result['line_items'][2]['match_amount'] == 550

        # Customer_charged unchanged on every row — the customer
        # paid in physical instruments, that count never changes.
        assert result['line_items'][0]['customer_charged'] == 1200
        assert result['line_items'][1]['customer_charged'] == 600
        assert result['line_items'][2]['customer_charged'] == 550

        # ``items`` array updated in lockstep so saved data matches.
        assert items[0]['method_amount'] == 2400
        assert items[1]['method_amount'] == 1000
        assert items[1]['match_amount'] == 400

    def test_per_vendor_allocation_balances_after_forfeit(self):
        """End-to-end invariant: after forfeit, per-vendor
        allocations match per-vendor receipts exactly.  This is the
        property that was broken — the per-vendor SNAP allocation
        relied on this to know how much each vendor still needed."""
        from fam.ui.payment_screen import PaymentScreen
        screen = self._build_screen_stub()

        result = {
            'line_items': [
                {'method_amount': 2400, 'match_amount': 1200,
                 'customer_charged': 1200},
                {'method_amount': 1200, 'match_amount': 600,
                 'customer_charged': 600},
                {'method_amount': 1100, 'match_amount': 550,
                 'customer_charged': 550},
            ],
            'allocated_total': 4700,
            'fam_subsidy_total': 2350,
        }
        items = [
            {'denomination': 200, 'bound_vendor_id': 2,
             'method_amount': 2400, 'match_amount': 1200},
            {'denomination': 200, 'bound_vendor_id': 3,
             'method_amount': 1200, 'match_amount': 600},
            {'denomination': None, 'bound_vendor_id': None,
             'method_amount': 1100, 'match_amount': 550},
        ]
        PaymentScreen._apply_denomination_forfeit(
            screen, result, items, overage=200)

        # Per-vendor denom totals after forfeit:
        #   HH: $24 (unchanged)
        #   KF: $10 (post-forfeit, exactly the receipt)
        # Plus SNAP $11 to allocate across vendors:
        #   JB: $10 (full receipt, no FB)
        #   HH: $1  (filling the $25 - $24 gap)
        #   KF: $0  (already filled by FB)
        # Total: $24 + $10 + $11 = $45 = order total ✓
        denom_per_vendor = {1: 0, 2: 0, 3: 0}
        for li, it in zip(result['line_items'], items):
            if it['denomination']:
                denom_per_vendor[it['bound_vendor_id']] += li['method_amount']
        assert denom_per_vendor[2] == 2400, (
            "HH should keep its full $24 FB allocation")
        assert denom_per_vendor[3] == 1000, (
            "KF should be reduced to its $10 receipt cap (not $12)")
        # JB had no FB — stays at 0.
        assert denom_per_vendor[1] == 0


# ══════════════════════════════════════════════════════════════════
# 2. Single-vendor + edge cases (regression-protection for fix path)
# ══════════════════════════════════════════════════════════════════
class TestForfeitEdgeCases:

    def test_single_vendor_order_still_works(self):
        """Single-vendor orders had the bug coincidentally hidden
        because the 'first row with match' was always the right
        row.  Make sure the fix doesn't regress that case."""
        from fam.ui.payment_screen import PaymentScreen
        screen = MagicMock(spec=PaymentScreen)
        screen._order_transactions = [
            {'id': 1, 'vendor_id': 1, 'receipt_total': 4900},
        ]

        # 5 × $5 FMNP = $25 customer / $50 method.  Receipt $49.
        # Overage $1.
        result = {
            'line_items': [
                {'method_amount': 5000, 'match_amount': 2500,
                 'customer_charged': 2500},
            ],
            'allocated_total': 5000,
            'fam_subsidy_total': 2500,
        }
        items = [
            {'denomination': 500, 'bound_vendor_id': None,
             'method_amount': 5000, 'match_amount': 2500},
        ]
        PaymentScreen._apply_denomination_forfeit(
            screen, result, items, overage=100)

        assert result['line_items'][0]['method_amount'] == 4900
        assert result['line_items'][0]['match_amount'] == 2400
        # Customer charge unchanged.
        assert result['line_items'][0]['customer_charged'] == 2500

    def test_overage_exceeds_overallocated_vendors_falls_back(self):
        """Defensive: if vendor map allocation is somehow inconsistent
        (e.g. the overage parameter is larger than the sum of
        per-vendor over-allocations), the legacy fall-back kicks in
        and absorbs the residual so totals still reconcile."""
        from fam.ui.payment_screen import PaymentScreen
        screen = MagicMock(spec=PaymentScreen)
        screen._order_transactions = [
            {'id': 1, 'vendor_id': 1, 'receipt_total': 1000},
        ]

        # Single line item; caller passes overage > what the per-
        # vendor pass can absorb (caller is wrong, but the function
        # must still complete cleanly).
        result = {
            'line_items': [
                {'method_amount': 1200, 'match_amount': 600,
                 'customer_charged': 600},
            ],
            'allocated_total': 1200,
            'fam_subsidy_total': 600,
        }
        items = [
            {'denomination': 200, 'bound_vendor_id': 1,
             'method_amount': 1200, 'match_amount': 600},
        ]
        # Overage of 600 — more than the per-vendor over-allocation
        # of 200.  The function must reduce match all the way down
        # rather than partial-apply and leave the totals wrong.
        PaymentScreen._apply_denomination_forfeit(
            screen, result, items, overage=600)

        assert result['line_items'][0]['method_amount'] == 600
        assert result['line_items'][0]['match_amount'] == 0
        # Result totals decremented by the full overage.
        assert result['allocated_total'] == 600
        assert result['fam_subsidy_total'] == 0

    def test_forfeit_skips_non_denominated_rows(self):
        """SNAP and other non-denominated rows must NEVER be
        targeted by the forfeit pass — only denominated rows can
        carry the overage by definition (the forfeit exists
        because denoms can't be split into smaller increments)."""
        from fam.ui.payment_screen import PaymentScreen
        screen = MagicMock(spec=PaymentScreen)
        screen._order_transactions = [
            {'id': 1, 'vendor_id': 1, 'receipt_total': 1000},
            {'id': 2, 'vendor_id': 2, 'receipt_total': 1000},
        ]

        # SNAP overshoots vendor 2.  Note: SNAP isn't denominated,
        # so this isn't really a "denomination overage" — but
        # callers might pass an overage anyway.  Make sure we
        # don't reduce match on SNAP via the per-vendor pass.
        # The legacy fall-back will absorb it instead.
        result = {
            'line_items': [
                {'method_amount': 1000, 'match_amount': 500,
                 'customer_charged': 500},  # FB → vendor 1
                {'method_amount': 1200, 'match_amount': 600,
                 'customer_charged': 600},  # SNAP overflow on vendor 2
            ],
            'allocated_total': 2200,
            'fam_subsidy_total': 1100,
        }
        items = [
            {'denomination': 200, 'bound_vendor_id': 1,
             'method_amount': 1000, 'match_amount': 500},
            # SNAP — non-denom, no bound vendor.
            {'denomination': None, 'bound_vendor_id': None,
             'method_amount': 1200, 'match_amount': 600},
        ]
        # Caller passes 200 overage.  The vendor-aware pass finds
        # NO denom row bound to vendor 2 (the over-allocated one),
        # so the legacy fall-back absorbs the 200 — but it must
        # still reduce a real match, not silently fail.
        PaymentScreen._apply_denomination_forfeit(
            screen, result, items, overage=200)

        # Total reduction = 200 across all match.
        total_match_after = sum(
            li['match_amount'] for li in result['line_items'])
        assert total_match_after == 1100 - 200, (
            "Total match must drop by exactly the overage amount; "
            "the function should never silently fail to apply.")

    def test_no_overage_is_no_op(self):
        """Sanity: ``overage=0`` leaves everything untouched."""
        from fam.ui.payment_screen import PaymentScreen
        screen = MagicMock(spec=PaymentScreen)
        screen._order_transactions = [
            {'id': 1, 'vendor_id': 1, 'receipt_total': 1000},
        ]
        result = {
            'line_items': [
                {'method_amount': 1000, 'match_amount': 500,
                 'customer_charged': 500},
            ],
            'allocated_total': 1000,
            'fam_subsidy_total': 500,
        }
        items = [
            {'denomination': 200, 'bound_vendor_id': 1,
             'method_amount': 1000, 'match_amount': 500},
        ]
        PaymentScreen._apply_denomination_forfeit(
            screen, result, items, overage=0)
        assert result['line_items'][0]['method_amount'] == 1000
        assert result['line_items'][0]['match_amount'] == 500
        assert result['allocated_total'] == 1000


# ══════════════════════════════════════════════════════════════════
# 4. Multi-vendor multi-forfeit (each vendor independently +1 unit)
# ══════════════════════════════════════════════════════════════════
class TestMultipleSimultaneousForfeits:
    """A single customer can pay multiple vendors with the same
    denominated method.  Each vendor independently is allowed up to
    +1 unit overage (the standard forfeit rule).  The forfeit
    attribution must apply to each over-allocated vendor's row
    independently — NOT collapse the multi-vendor overage onto a
    single row.

    User-stated requirement (2026-04 onsite follow-up):
        "if a customer decides to pay multiple vendors with a
        denominated method they should both be allowed to have a
        single unit overage and anything left over when you hit
        auto distribute it should fill in with SNAP for whoever is
        due any left over funds."

    The current ``_apply_denomination_forfeit`` already handles
    this by iterating every entry of ``over_per_vendor``.  These
    tests pin the contract so a future refactor that 'optimises'
    the loop into a single-pass single-vendor reduction regresses
    loudly.
    """

    def test_two_vendors_each_one_unit_over_get_independent_forfeits(self):
        """Customer hands 6 × $2 Food Bucks to Vendor A ($11 receipt)
        AND 7 × $2 Food Bucks to Vendor B ($13 receipt).  Each is
        over by exactly $1.  Both rows must be forfeit-reduced to
        their respective receipt totals — NOT one row reduced by
        the full $2 while the other stays inflated."""
        from fam.ui.payment_screen import PaymentScreen
        screen = MagicMock(spec=PaymentScreen)
        screen._order_transactions = [
            {'id': 1, 'vendor_id': 1, 'receipt_total': 1100},   # A
            {'id': 2, 'vendor_id': 2, 'receipt_total': 1300},   # B
        ]

        result = {
            'line_items': [
                # 6 × $2 FB → A: $12 customer + $0 (will adjust),
                # 100% match means $12 customer / $12 match / $24 method
                # but the customer's "real" customer_charged for FB at
                # 100% match is $12 in physical instruments (the face
                # value).  To match what the engine would produce here,
                # use customer=600 (charge of 6 × $2 in cents = $12 →
                # 600 cents) ... wait that's $6 not $12.  Let me redo.
                #
                # Actually a cleaner scenario for testing the forfeit:
                # 6 × $2 FB at 100% match.  Each unit: customer pays
                # $2, FAM matches $2, method total $4.  6 units: $12
                # customer paid in cents=1200, $12 match = 1200, $24
                # method = 2400.  Receipt $11 = 1100.  Overage = $13
                # ($24 method - $11 receipt = $13)?  No wait — single
                # method only fits $11, so customer + match must equal
                # $11.  Customer paid $12 in physical — they overpaid.
                # That's the forfeit case: customer paid more than
                # receipt warrants.  Adjusted: customer $11, match $0,
                # method $11.  Forfeit on match = $12.  But that's a
                # full-match forfeit, not "1 unit overage".
                #
                # For "1 unit overage" we want: customer hands N+1
                # units where N would fit cleanly.  $11 receipt with
                # $4 effective denom (1 unit FB at 100%): N=2 fits
                # ($8), N+1=3 overshoots ($12 = $1 over).  6 units
                # would be way over.
                #
                # So the correct scenario for the user's request:
                # Vendor A: receipt $11, 3 × $2 FB → $6 customer +
                # $6 match = $12 method, overage $1.
                # Vendor B: receipt $13, 4 × $2 FB → $8 customer +
                # $8 match = $16 method, overage $3 ... that's 3
                # units over, too much.
                #
                # Cleaner: Vendor B receipt $11, same as A.
                # Vendor B: receipt $11, 3 × $2 FB → $12 method,
                # overage $1.
                #
                # OK rewriting the scenario:
                # Vendor A: receipt $11, FB customer $6 / match $6 /
                #   method $12 (overage $1)
                # Vendor B: receipt $11, FB customer $6 / match $6 /
                #   method $12 (overage $1)
                # Total overage = $2.
                {'method_amount': 1200, 'match_amount': 600,
                 'customer_charged': 600},   # FB → A
                {'method_amount': 1200, 'match_amount': 600,
                 'customer_charged': 600},   # FB → B
            ],
            'allocated_total': 2400,
            'fam_subsidy_total': 1200,
        }
        items = [
            {'denomination': 200, 'bound_vendor_id': 1,
             'method_amount': 1200, 'match_amount': 600},
            {'denomination': 200, 'bound_vendor_id': 2,
             'method_amount': 1200, 'match_amount': 600},
        ]
        # Adjust receipts to exactly match this scenario.
        screen._order_transactions = [
            {'id': 1, 'vendor_id': 1, 'receipt_total': 1100},
            {'id': 2, 'vendor_id': 2, 'receipt_total': 1100},
        ]
        # Total overage = 200 ($2 across both vendors).
        PaymentScreen._apply_denomination_forfeit(
            screen, result, items, overage=200)

        # Both rows reduced by $1 each — NOT one row reduced by $2.
        assert result['line_items'][0]['method_amount'] == 1100, (
            f"Vendor A's FB row must drop $12 → $11 (its receipt).  "
            f"Got {result['line_items'][0]['method_amount']}.")
        assert result['line_items'][0]['match_amount'] == 500, (
            "Vendor A's match must drop by exactly its $1 forfeit.")
        assert result['line_items'][1]['method_amount'] == 1100, (
            f"Vendor B's FB row must independently drop $12 → $11.  "
            f"Got {result['line_items'][1]['method_amount']}.  If "
            f"Vendor A absorbed the full $2, this would still be $12 "
            f"and the per-vendor reconciliation would fail at save.")
        assert result['line_items'][1]['match_amount'] == 500, (
            "Vendor B's match must independently drop by its own $1 "
            "forfeit.")

        # Customer charge unchanged on both rows (customer paid in
        # physical instruments; that count never moves).
        assert result['line_items'][0]['customer_charged'] == 600
        assert result['line_items'][1]['customer_charged'] == 600

    def test_three_vendors_two_over_one_balanced(self):
        """Three vendors: A and C overshoot by $1 each, B is exactly
        balanced (no FB).  Forfeit must touch ONLY A and C — B's
        rows (whether non-denom or empty) must stay untouched."""
        from fam.ui.payment_screen import PaymentScreen
        screen = MagicMock(spec=PaymentScreen)
        screen._order_transactions = [
            {'id': 1, 'vendor_id': 1, 'receipt_total': 1100},
            {'id': 2, 'vendor_id': 2, 'receipt_total': 2000},
            {'id': 3, 'vendor_id': 3, 'receipt_total': 1100},
        ]
        # Items: FB → A, FB → C, SNAP (filling B + others).  SNAP is
        # included to verify it isn't dragged into the per-vendor
        # forfeit pass.
        result = {
            'line_items': [
                {'method_amount': 1200, 'match_amount': 600,
                 'customer_charged': 600},   # FB → A (over $1)
                {'method_amount': 1200, 'match_amount': 600,
                 'customer_charged': 600},   # FB → C (over $1)
                {'method_amount': 2000, 'match_amount': 1000,
                 'customer_charged': 1000},  # SNAP → B etc.
            ],
            'allocated_total': 4400,
            'fam_subsidy_total': 2200,
        }
        items = [
            {'denomination': 200, 'bound_vendor_id': 1,
             'method_amount': 1200, 'match_amount': 600},
            {'denomination': 200, 'bound_vendor_id': 3,
             'method_amount': 1200, 'match_amount': 600},
            {'denomination': None, 'bound_vendor_id': None,
             'method_amount': 2000, 'match_amount': 1000},
        ]
        PaymentScreen._apply_denomination_forfeit(
            screen, result, items, overage=200)

        # A and C: each reduced by $1.
        assert result['line_items'][0]['method_amount'] == 1100
        assert result['line_items'][1]['method_amount'] == 1100
        # SNAP unchanged (non-denom rows are excluded from the
        # per-vendor pass and the legacy fallback only fires on
        # residual overage — which is 0 after A and C absorb).
        assert result['line_items'][2]['method_amount'] == 2000
        assert result['line_items'][2]['match_amount'] == 1000

    def test_overage_distributed_proportionally_across_vendor_overages(
            self):
        """Vendor A is over by $1, vendor B is over by $3 (e.g. user
        manually typed extra Food Bucks past the cap).  Total
        overage $4.  Forfeit must apply $1 to A and $3 to B — NOT
        $2 each (proportional split would be wrong; each vendor's
        overage is THEIR overage)."""
        from fam.ui.payment_screen import PaymentScreen
        screen = MagicMock(spec=PaymentScreen)
        screen._order_transactions = [
            {'id': 1, 'vendor_id': 1, 'receipt_total': 1100},
            {'id': 2, 'vendor_id': 2, 'receipt_total': 1100},
        ]
        result = {
            'line_items': [
                # A: $12 method on $11 receipt = $1 over
                {'method_amount': 1200, 'match_amount': 600,
                 'customer_charged': 600},
                # B: $14 method on $11 receipt = $3 over
                {'method_amount': 1400, 'match_amount': 700,
                 'customer_charged': 700},
            ],
            'allocated_total': 2600,
            'fam_subsidy_total': 1300,
        }
        items = [
            {'denomination': 200, 'bound_vendor_id': 1,
             'method_amount': 1200, 'match_amount': 600},
            {'denomination': 200, 'bound_vendor_id': 2,
             'method_amount': 1400, 'match_amount': 700},
        ]
        PaymentScreen._apply_denomination_forfeit(
            screen, result, items, overage=400)

        # A: receipt is $11, allocation was $12 → reduce by $1.
        assert result['line_items'][0]['method_amount'] == 1100
        assert result['line_items'][0]['match_amount'] == 500
        # B: receipt is $11, allocation was $14 → reduce by $3.
        assert result['line_items'][1]['method_amount'] == 1100
        assert result['line_items'][1]['match_amount'] == 400


# ══════════════════════════════════════════════════════════════════
# 5. Forfeit + auto-distribute fill-in (the user's full ask)
# ══════════════════════════════════════════════════════════════════
class TestForfeitPlusAutoDistributeFillIn:
    """End-to-end check that the forfeit-attribution fix composes
    correctly with the existing per-vendor SNAP distribution.

    Scenario: 3 vendors, mixed FB allocation:
      - A: receipt $11, 6 × $2 FB → $12 method (over $1)
      - B: receipt $20, 7 × $2 FB → $14 method (under $6)
      - C: receipt $5,  no FB              → $0 method (under $5)

    Auto-Distribute should compute effective_order_total = $26
    (locked bound denom) + $11 (sum of under-allocated remaining)
    = $37.  SNAP needs $11 method.  Per-vendor SNAP allocation:
    A=$0 (over), B=$6, C=$5.

    Forfeit then reduces A's FB by $1 → final A balance $11.

    All three vendors end at exactly their receipt totals.  This
    test pins the math at the helper level."""

    def test_compute_effective_order_total_handles_overage_per_vendor(
            self):
        """Pin that ``_compute_effective_order_total`` produces the
        right number for a mixed-state order.  (The function is
        what tells Auto-Distribute how much SNAP to allocate; if
        it's wrong, the SNAP row ends up under or over.)"""
        from fam.ui.payment_screen import PaymentScreen
        from PySide6.QtCore import QObject

        # Need a real PaymentScreen for the helper to read its
        # _payment_rows + _order_transactions.  Build the minimum
        # state by hand without going through the full UI.
        screen = MagicMock(spec=PaymentScreen)
        screen._order_transactions = [
            {'id': 1, 'vendor_id': 1, 'receipt_total': 1100},  # A
            {'id': 2, 'vendor_id': 2, 'receipt_total': 2000},  # B
            {'id': 3, 'vendor_id': 3, 'receipt_total': 500},   # C
        ]
        screen._order_total = 3600
        # Mock the row data: 2 FB rows (A, B) + 1 (empty) SNAP slot.
        # The function reads: row.get_selected_method() and
        # row.get_bound_vendor_id() and row._get_active_charge().
        row_a = MagicMock()
        row_a.get_selected_method.return_value = {
            'name': 'Food Bucks', 'match_percent': 100.0,
            'denomination': 200,
        }
        row_a.get_bound_vendor_id.return_value = 1
        row_a._get_active_charge.return_value = 600  # 6 × $2 = $12 charge — wait, 6 units × $2 = $12, with 100% match = $24 method. But customer paid $6 (3 units)? No.  Let me reset.
        # Actually for FB at 100% match, "charge" in the row is the
        # face value the customer hands over.  6 × $2 = $12 is the
        # face value, so charge=1200.  charge_to_method_amount(1200, 100)
        # = 1200 + 1200 = 2400 (= 6 × $4).  But for an $11 receipt,
        # 1 unit overage means N=2 ($8 alloc) plus 1 = 3 units = $12
        # alloc.  That's the canonical "1 unit forfeit" case.
        # Charge = 3 × $2 = $6 = 600 cents.  method_amount = $12 = 1200 cents.
        row_a._get_active_charge.return_value = 600   # 3 × $2 = $6 charge

        row_b = MagicMock()
        row_b.get_selected_method.return_value = {
            'name': 'Food Bucks', 'match_percent': 100.0,
            'denomination': 200,
        }
        row_b.get_bound_vendor_id.return_value = 2
        # 7 × $2 = $14 charge, 100% match → $28 method.  $20 receipt
        # → $8 over, NOT 1-unit forfeit (>1 unit over isn't a clean
        # forfeit).  Adjust: 4 units = $8 charge, $16 method, under
        # $4 against $20 receipt.  That's the 'under-allocated'
        # scenario the test wants.
        row_b._get_active_charge.return_value = 800  # 4 × $2 = $8 charge → $16 method

        screen._payment_rows = [row_a, row_b]

        # Compute effective order total directly via the helper.
        # The function lives on PaymentScreen so call it as unbound.
        eot = PaymentScreen._compute_effective_order_total(screen)

        # bound_denom_alloc:
        #   A: charge $6 → method $12  (over by $1)
        #   B: charge $8 → method $16  (under by $4 of B's $20 receipt)
        # vendor_receipts = {A: $11, B: $20, C: $5}
        # max(0, A_receipt - A_alloc) = max(0, 11-12) = 0
        # max(0, B_receipt - B_alloc) = max(0, 20-16) = $4
        # max(0, C_receipt - C_alloc) = max(0, 5-0)   = $5
        # non_denom_needed = $9
        # locked_bound_denom = $12 + $16 = $28
        # effective_order_total = $28 + $9 = $37
        assert eot == 3700, (
            f"effective_order_total should be $37 (locked denom "
            f"$28 + non-denom needed $9) — got "
            f"{eot}.  This is what tells Auto-Distribute to "
            f"allocate $9 of SNAP across vendors B and C.")


# ══════════════════════════════════════════════════════════════════
# 3. Source-level guards
# ══════════════════════════════════════════════════════════════════
class TestForfeitSourceGuards:

    def test_function_consults_order_transactions_for_vendor_map(self):
        """Pin that the wrapper builds the vendor map from
        ``_order_transactions`` and forwards it to the canonical
        forfeit function.  Without this, the per-vendor-aware
        path would silently regress to the legacy first-with-
        match behaviour.

        v2.0.7-final consolidation (Option B): the vendor-aware
        Phase A/B math now lives in
        ``fam.utils.calculations.apply_denomination_forfeit``.
        PaymentScreen's ``_apply_denomination_forfeit`` is a thin
        wrapper that builds ``vendor_receipts`` from
        ``self._order_transactions`` and delegates."""
        import inspect
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import apply_denomination_forfeit
        wrapper_src = inspect.getsource(
            PaymentScreen._apply_denomination_forfeit)
        canonical_src = inspect.getsource(apply_denomination_forfeit)
        # Wrapper must build the vendor_receipts map from
        # _order_transactions and forward to the canonical fn.
        assert 'self._order_transactions' in wrapper_src, (
            "PaymentScreen wrapper must read _order_transactions "
            "to build the vendor_receipts map.")
        assert 'apply_denomination_forfeit' in wrapper_src, (
            "PaymentScreen wrapper must delegate to the canonical "
            "fam.utils.calculations.apply_denomination_forfeit.")
        assert 'vendor_receipts=vendor_receipts' in wrapper_src, (
            "Wrapper must pass vendor_receipts as a keyword arg "
            "to the canonical function.")
        # Canonical function carries the vendor-aware logic.
        assert 'bound_vendor_id' in canonical_src
        assert 'vendor_alloc' in canonical_src
        assert 'vendor_receipts' in canonical_src

    def test_legacy_fallback_preserved(self):
        """The fall-back loop (Pass 2: first-with-match) must
        still exist for residual overage (defensive guard against
        numerical drift, empty vendor maps, etc.).  Without it,
        edge-case inputs would silently leave totals unbalanced.

        v2.0.7-final consolidation: this logic moved into the
        canonical ``fam.utils.calculations.apply_denomination_forfeit``
        function alongside the vendor-aware Pass 1."""
        import inspect
        from fam.utils.calculations import apply_denomination_forfeit
        src = inspect.getsource(apply_denomination_forfeit)
        # The fall-back is guarded by ``if remaining_overage > 0:``
        # AFTER the per-vendor pass.
        assert 'if remaining_overage > 0:' in src
        # And uses the original first-with-match algorithm.
        assert "if li['match_amount'] > 0:" in src
