"""Regression tests for the integer-cents money boundary.

Validates that dollars↔cents conversion is correct at every boundary:
persistence round-trips, display formatting, calculation pipelines,
and edge-case inputs.
"""

import pytest
from fam.utils.money import dollars_to_cents, cents_to_dollars, format_dollars, format_dollars_comma
from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Create a fresh database for each test."""
    db_file = str(tmp_path / "test_money_boundaries.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    yield conn
    close_connection()


# ══════════════════════════════════════════════════════════════════
# 1. dollars_to_cents conversion
# ══════════════════════════════════════════════════════════════════

class TestDollarsToCents:

    def test_exact_dollar(self):
        assert dollars_to_cents(1.00) == 100

    def test_exact_cents(self):
        assert dollars_to_cents(89.99) == 8999

    def test_zero(self):
        assert dollars_to_cents(0.0) == 0

    def test_large_amount(self):
        assert dollars_to_cents(99999.99) == 9999999

    def test_one_cent(self):
        assert dollars_to_cents(0.01) == 1

    def test_half_cent_rounds_to_nearest(self):
        # 0.005 → 0.5 cents → rounds to 0 (banker's rounding)
        # or 1 depending on implementation
        result = dollars_to_cents(0.005)
        assert result in (0, 1)  # either is acceptable

    def test_float_precision_issue(self):
        """19.99 * 100 = 1998.9999... in IEEE 754 — must still produce 1999."""
        assert dollars_to_cents(19.99) == 1999

    def test_33_cents(self):
        """0.33 * 100 = 33.0000...04 in IEEE 754 — must produce 33."""
        assert dollars_to_cents(0.33) == 33

    def test_negative_not_expected_but_safe(self):
        """Negative values shouldn't appear but should convert correctly."""
        assert dollars_to_cents(-5.00) == -500


# ══════════════════════════════════════════════════════════════════
# 2. cents_to_dollars conversion
# ══════════════════════════════════════════════════════════════════

class TestCentsToDollars:

    def test_exact(self):
        assert cents_to_dollars(8999) == 89.99

    def test_zero(self):
        assert cents_to_dollars(0) == 0.0

    def test_one_cent(self):
        assert cents_to_dollars(1) == 0.01

    def test_large(self):
        assert cents_to_dollars(9999999) == 99999.99


# ══════════════════════════════════════════════════════════════════
# 3. format_dollars
# ══════════════════════════════════════════════════════════════════

class TestFormatDollars:

    def test_zero(self):
        assert format_dollars(0) == "$0.00"

    def test_one_cent(self):
        assert format_dollars(1) == "$0.01"

    def test_one_dollar(self):
        assert format_dollars(100) == "$1.00"

    def test_typical(self):
        assert format_dollars(8999) == "$89.99"

    def test_large_no_comma(self):
        # format_dollars does NOT add commas
        assert format_dollars(123456) == "$1234.56"

    def test_format_dollars_comma(self):
        assert format_dollars_comma(123456) == "$1,234.56"

    def test_format_dollars_comma_small(self):
        assert format_dollars_comma(999) == "$9.99"


# ══════════════════════════════════════════════════════════════════
# 4. Round-trip: dollars → cents → dollars
# ══════════════════════════════════════════════════════════════════

class TestRoundTrip:

    @pytest.mark.parametrize("dollars", [
        0.00, 0.01, 0.99, 1.00, 9.99, 10.00, 49.99, 89.99,
        100.00, 199.95, 999.99, 12345.67,
    ])
    def test_round_trip(self, dollars):
        """dollars → cents → dollars should be lossless for valid inputs."""
        cents = dollars_to_cents(dollars)
        assert isinstance(cents, int)
        result = cents_to_dollars(cents)
        assert result == dollars

    @pytest.mark.parametrize("dollars", [
        0.00, 0.01, 0.99, 1.00, 9.99, 10.00, 49.99, 89.99,
    ])
    def test_format_round_trip(self, dollars):
        """dollars → cents → format_dollars should match f'${dollars:.2f}'."""
        cents = dollars_to_cents(dollars)
        assert format_dollars(cents) == f"${dollars:.2f}"


# ══════════════════════════════════════════════════════════════════
# 5. DB persistence round-trip
# ══════════════════════════════════════════════════════════════════

class TestDBRoundTrip:

    def test_receipt_total_round_trip(self, fresh_db):
        """Receipt total survives: spinbox dollars → cents → DB → cents → display dollars."""
        conn = fresh_db
        conn.execute("INSERT INTO markets (id, name) VALUES (1, 'M')")
        conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
        conn.execute(
            "INSERT INTO market_days (id, market_id, date, status) "
            "VALUES (1, 1, '2026-01-01', 'Open')")

        # Simulate: user types $89.99 in spinbox
        user_input = 89.99
        cents = dollars_to_cents(user_input)
        assert cents == 8999

        conn.execute(
            "INSERT INTO transactions (id, market_day_id, vendor_id, "
            "receipt_total, status, fam_transaction_id) "
            "VALUES (1, 1, 1, ?, 'Draft', 'FAM-001')", (cents,))
        conn.commit()

        # Read back
        row = conn.execute("SELECT receipt_total FROM transactions WHERE id=1").fetchone()
        stored = row['receipt_total']
        assert stored == 8999
        assert isinstance(stored, int)

        # Display
        assert format_dollars(stored) == "$89.99"

    def test_denomination_round_trip(self, fresh_db):
        """Denomination survives: settings dollars → cents → DB → cents → display."""
        conn = fresh_db

        denom_dollars = 5.00
        denom_cents = dollars_to_cents(denom_dollars)
        assert denom_cents == 500

        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent, sort_order, denomination) "
            "VALUES (1, 'FMNP', 100.0, 1, ?)", (denom_cents,))
        conn.commit()

        row = conn.execute(
            "SELECT denomination FROM payment_methods WHERE id=1").fetchone()
        assert row['denomination'] == 500
        assert isinstance(row['denomination'], int)

    def test_daily_match_limit_round_trip(self, fresh_db):
        """Daily match limit round-trip through DB."""
        conn = fresh_db

        limit_dollars = 100.00
        limit_cents = dollars_to_cents(limit_dollars)
        conn.execute(
            "INSERT INTO markets (id, name, daily_match_limit) VALUES (1, 'M', ?)",
            (limit_cents,))
        conn.commit()

        row = conn.execute("SELECT daily_match_limit FROM markets WHERE id=1").fetchone()
        assert row['daily_match_limit'] == 10000
        assert isinstance(row['daily_match_limit'], int)


# ══════════════════════════════════════════════════════════════════
# 6. Calculation pipeline: end-to-end cents integrity
# ══════════════════════════════════════════════════════════════════

class TestCalculationPipeline:

    def test_full_payment_pipeline(self):
        """Simulate: $89.99 order → SNAP 100% match → breakdown → all cents."""
        from fam.utils.calculations import (
            charge_to_method_amount, calculate_payment_breakdown
        )

        order_cents = dollars_to_cents(89.99)
        assert order_cents == 8999

        # Customer charges $44.99 (charge = what customer pays)
        charge = 4500  # $45.00 in cents
        method_amount = charge_to_method_amount(charge, 100.0)
        assert method_amount == 9000  # $90.00 allocated

        result = calculate_payment_breakdown(
            order_cents,
            [{'method_amount': method_amount, 'match_percent': 100.0}],
            match_limit=None,
        )

        # All values must be integers
        assert isinstance(result['allocated_total'], int)
        assert isinstance(result['fam_subsidy_total'], int)
        assert isinstance(result['customer_total_paid'], int)
        for li in result['line_items']:
            assert isinstance(li['method_amount'], int)
            assert isinstance(li['match_amount'], int)
            assert isinstance(li['customer_charged'], int)

    def test_match_limit_pipeline(self):
        """Match limit cap preserves integer types."""
        from fam.utils.calculations import calculate_payment_breakdown

        result = calculate_payment_breakdown(
            10000,  # $100 order
            [{'method_amount': 10000, 'match_percent': 100.0}],
            match_limit=2500,  # $25 cap
        )

        assert result['fam_subsidy_total'] == 2500
        assert result['match_was_capped'] is True
        for li in result['line_items']:
            assert isinstance(li['match_amount'], int)
            assert isinstance(li['customer_charged'], int)
            assert li['match_amount'] + li['customer_charged'] == li['method_amount']

    def test_penny_reconciliation_odd_total_with_match(self):
        """Odd-cent order with 100% match: allocated must equal receipt exactly.

        Regression for: $56.77 order with SNAP ($36.76) + FMNP ($20.00)
        produced allocated=$56.76, leaving 1¢ unaccounted.  The fix absorbs
        the rounding penny into the FAM match so allocated == receipt.
        """
        from fam.utils.calculations import calculate_payment_breakdown

        # User's exact scenario
        result = calculate_payment_breakdown(
            5677,
            [
                {'method_amount': 3676, 'match_percent': 100.0},  # SNAP
                {'method_amount': 2000, 'match_percent': 100.0},  # FMNP
            ],
        )
        assert result['allocated_total'] == 5677
        assert result['allocation_remaining'] == 0
        assert result['is_valid'] is True
        # Customer charge unchanged
        assert result['customer_total_paid'] == 2838
        # FAM absorbs the penny
        assert result['fam_subsidy_total'] == 2839
        # Sum reconciles
        assert result['customer_total_paid'] + result['fam_subsidy_total'] == 5677

    def test_penny_reconciliation_over_allocation(self):
        """1¢ over-allocation: match reduced by 1¢."""
        from fam.utils.calculations import calculate_payment_breakdown

        result = calculate_payment_breakdown(
            4999,
            [{'method_amount': 5000, 'match_percent': 100.0}],
        )
        assert result['allocated_total'] == 4999
        assert result['allocation_remaining'] == 0
        assert result['line_items'][0]['method_amount'] == 4999
        assert result['line_items'][0]['match_amount'] == 2499
        assert result['line_items'][0]['customer_charged'] == 2500

    def test_penny_reconciliation_no_match_methods(self):
        """Cash-only 1¢ gap: no matched items to absorb, stays as tolerance."""
        from fam.utils.calculations import calculate_payment_breakdown

        result = calculate_payment_breakdown(
            5001,
            [{'method_amount': 5000, 'match_percent': 0.0}],
        )
        assert result['allocation_remaining'] == 1
        assert result['is_valid'] is True  # ±1 tolerance

    def test_penny_reconciliation_exact_amount(self):
        """No gap: nothing to absorb."""
        from fam.utils.calculations import calculate_payment_breakdown

        result = calculate_payment_breakdown(
            5000,
            [{'method_amount': 5000, 'match_percent': 100.0}],
        )
        assert result['allocated_total'] == 5000
        assert result['allocation_remaining'] == 0


# ══════════════════════════════════════════════════════════════════
# 7. Settings I/O boundary
# ══════════════════════════════════════════════════════════════════

class TestSettingsIOBoundary:

    def test_export_import_denomination_round_trip(self, fresh_db, tmp_path):
        """Denomination survives: DB cents → export dollars → import dollars → DB cents."""
        from fam.settings_io import export_settings, parse_settings_file, apply_import
        from fam.models.payment_method import create_payment_method

        # Create with cents
        create_payment_method('TestPM', 100.0, sort_order=1, denomination=500)

        # Export (should contain "5.0" in the file)
        filepath = str(tmp_path / "test.fam")
        export_settings(filepath)

        with open(filepath) as f:
            content = f.read()
        assert '5.0' in content  # denomination exported as dollars

        # Clear and re-import
        fresh_db.execute("DELETE FROM payment_methods")
        fresh_db.commit()

        result = parse_settings_file(filepath)
        assert len(result.payment_methods) >= 1
        pm = next(p for p in result.payment_methods if p.name == 'TestPM')
        assert pm.denomination == 5.0  # parsed as dollars

        apply_import(result)

        # Verify DB has cents
        row = fresh_db.execute(
            "SELECT denomination FROM payment_methods WHERE name='TestPM'"
        ).fetchone()
        assert row['denomination'] == 500

    def test_export_import_match_limit_round_trip(self, fresh_db, tmp_path):
        """Daily match limit survives export → import round trip."""
        from fam.settings_io import export_settings, parse_settings_file, apply_import

        # Ensure a market exists, then set limit in cents
        fresh_db.execute(
            "INSERT OR IGNORE INTO markets (id, name, daily_match_limit, match_limit_active)"
            " VALUES (1, 'Test Market', 7500, 1)")
        fresh_db.execute(
            "UPDATE markets SET daily_match_limit = 7500 WHERE id = 1")
        fresh_db.commit()

        filepath = str(tmp_path / "test.fam")
        export_settings(filepath)

        with open(filepath) as f:
            content = f.read()
        assert '75.00' in content  # exported as dollars

        # Clear and re-import
        fresh_db.execute("DELETE FROM markets")
        fresh_db.commit()

        result = parse_settings_file(filepath)
        apply_import(result)

        row = fresh_db.execute(
            "SELECT daily_match_limit FROM markets LIMIT 1"
        ).fetchone()
        assert row['daily_match_limit'] == 7500


# ══════════════════════════════════════════════════════════════════
# 8. FMNP check-splitting: remainder distribution
# ══════════════════════════════════════════════════════════════════

class TestFMNPCheckSplitting:
    """Verify FMNP check amounts sum exactly to the total (no lost pennies)."""

    def test_even_split(self):
        """1500 cents / 3 checks = 500 each, no remainder."""
        total = 1500
        num = 3
        base = total // num
        rem = total % num
        checks = [base + (1 if i < rem else 0) for i in range(num)]
        assert sum(checks) == total
        assert checks == [500, 500, 500]

    def test_uneven_split_remainder_distributed(self):
        """1000 cents / 3 checks → 334, 333, 333 (first gets extra penny)."""
        total = 1000
        num = 3
        base = total // num
        rem = total % num
        checks = [base + (1 if i < rem else 0) for i in range(num)]
        assert sum(checks) == total
        assert checks[0] == 334  # remainder penny goes to first check
        assert checks[1] == 333
        assert checks[2] == 333

    def test_single_check(self):
        """Single check gets the full amount."""
        total = 1999
        num = 1
        base = total // num
        rem = total % num
        checks = [base + (1 if i < rem else 0) for i in range(num)]
        assert sum(checks) == total
        assert checks == [1999]

    def test_two_checks_odd_cent(self):
        """999 cents / 2 → 500 + 499."""
        total = 999
        num = 2
        base = total // num
        rem = total % num
        checks = [base + (1 if i < rem else 0) for i in range(num)]
        assert sum(checks) == total
        assert checks == [500, 499]

    def test_many_checks(self):
        """100 cents / 7 checks: sum must be exact."""
        total = 100
        num = 7
        base = total // num
        rem = total % num
        checks = [base + (1 if i < rem else 0) for i in range(num)]
        assert sum(checks) == total
        assert len(checks) == 7


# ══════════════════════════════════════════════════════════════════
# 9. Accumulate-then-convert vs convert-then-accumulate
# ══════════════════════════════════════════════════════════════════

class TestAccumulationPrecision:
    """Verify that accumulating cents then converting is lossless,
    while accumulating dollar floats can drift."""

    def test_accumulate_cents_then_convert(self):
        """Accumulating in cents and converting once is always exact."""
        values_cents = [3333, 3334, 3333]  # $33.33 + $33.34 + $33.33
        total_cents = sum(values_cents)
        assert total_cents == 10000
        assert cents_to_dollars(total_cents) == 100.0

    def test_many_small_values(self):
        """Summing 1000 x 1 cent in cents then converting is exact."""
        total_cents = sum([1] * 1000)
        assert total_cents == 1000
        assert cents_to_dollars(total_cents) == 10.0

    @pytest.mark.parametrize("values_cents", [
        [3333, 3334, 3333],
        [1] * 100,
        [4999, 5001],
        [33, 33, 34],
        [7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 9],
    ])
    def test_sum_invariant(self, values_cents):
        """Sum of individual dollar conversions may drift;
        converting the cent sum never does."""
        total_cents = sum(values_cents)
        assert cents_to_dollars(total_cents) == total_cents / 100
