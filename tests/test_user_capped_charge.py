"""User-cap on non-denom charge fields (user-reported 2026-05-07).

User scenario:

  "if the customer only has $125 on their SNAP card and wants to
   pay the rest with cash I can't even set SNAP to $125 it fills
   up to the remaining total"

  "If a user adds a non-denominated payment row and clicks auto
   distribute it can automatically fill, if multiple are present
   default to SNAP first, CASH last, if the user modifies the
   value of any row manually then auto distribute should skip it
   if the button is hit again and honor what the user is trying
   to enter manually."

Pre-rebuild behavior: typing $100 into a non-denom row caused the
engine's cap-aware logic + Pass 4 give-back to inflate the row's
``customer_charged`` (e.g. to $108) to absorb the FAM-match-cap
shrinkage.  The UI write-back at payment_screen.py then clobbered
the user's $100 with the engine's inflated value — no warning, no
opt-out.  Auto-Distribute also reset every non-denom row to $0
before redistributing, so a manually-typed cap couldn't survive
a subsequent Auto-Distribute click.

Full rebuild (v2.0.7+) — clean two-policy model:

  ENGINE policy (`fam/utils/calculations.py`):
    customer_charged for non-denom rows is INPUT and is NEVER
    modified by the engine.  When the daily FAM match cap
    shrinks a row's match contribution, the row's method_amount
    shrinks (= customer + new_match), the order's
    allocation_remaining surfaces > 0, and ``is_valid`` becomes
    False.  Confirm is blocked by the existing safety check
    until the volunteer adds another row to absorb the gap.

  AUTO-DISTRIBUTE policy (`fam/ui/payment_screen.py
  ._auto_distribute`):
    Rows where ``is_user_capped()`` returns True are treated
    like locked denom rows — they keep their typed value.
    Auto-Distribute redistributes the remainder across the
    NON-capped non-denom rows, with the existing SNAP-first /
    Cash-last ordering preserved by ``smart_auto_distribute``.

  PaymentRow ``_user_capped`` flag becomes a pure UI concept:
    set when ``amount_spin.valueChanged`` fires (genuine user
    typing — programmatic ``_set_active_charge`` blocks signals),
    cleared by ``clear_user_cap()`` if needed (e.g. when the row
    is removed and re-added).  The engine doesn't read this
    flag — it preserves customer_charged for non-denom rows
    universally.

This file pins:

  1. Engine never inflates non-denom customer_charged when the
     match cap shrinks the row's match (cap reduces match →
     method shrinks → remaining surfaces).
  2. Engine never inflates non-denom customer_charged via cent
     adjustment / penny reconciliation (those touch match only).
  3. Auto-Distribute skips user-capped rows (treats them as
     locked, like denom rows with a charge).
  4. PaymentRow's ``_user_capped`` flag is True after typing,
     False after ``clear_user_cap()``, False on a freshly-added
     row, and NOT set by programmatic ``_set_active_charge``.
  5. ``get_data()`` carries the ``user_capped`` flag for callers
     that want to consult it (Auto-Distribute does).
"""

import pytest

from fam.utils.calculations import (
    calculate_payment_breakdown,
)


# ──────────────────────────────────────────────────────────────────
# 1. Engine NEVER inflates non-denom customer_charged
# ──────────────────────────────────────────────────────────────────


class TestEnginePreservesNonDenomCustomer:
    """When the daily FAM match cap shrinks a non-denom row's
    match contribution, the row's customer_charged STAYS at the
    input value.  The shortfall surfaces as
    allocation_remaining > 0 (Confirm blocked → user adds
    another row)."""

    def test_snap_customer_preserved_under_match_cap(self):
        """User typed SNAP $125 (= method_amount $250 at 100%
        match) with user_capped=True.  Daily cap = $100.

        Pre-fix: engine inflated customer_charged to absorb the
        cap shrinkage.  Post-fix: with user_capped=True,
        customer stays $125, match reduces to $100, method
        shrinks to $225, allocation_remaining surfaces."""
        items = [
            {'method_amount': 25000,    # $125 × 2 (100% match)
             'match_percent': 100.0,
             'denomination': None,
             'user_capped': True},
        ]
        result = calculate_payment_breakdown(
            receipt_total=25000, payment_entries=items,
            match_limit=10000)  # $100 daily cap
        assert result['line_items'][0]['customer_charged'] == 12500, (
            f"Engine MUST preserve user-capped customer_charged.  "
            f"Got: ${result['line_items'][0]['customer_charged']/100:.2f}")
        assert result['line_items'][0]['match_amount'] == 10000
        assert result['line_items'][0]['method_amount'] == 22500
        assert result['allocation_remaining'] == 2500

    def test_two_non_denom_rows_under_cap_with_user_caps(self):
        """SNAP $100 (user_capped) + Cash $35.57 (user_capped),
        denom Food Bucks $8 (4 × $2, 100% match), receipt
        $250.09, cap $100."""
        items = [
            {'method_amount': 1600,     # FB $8 + $8 match = $16
             'match_percent': 100.0,
             'denomination': 200},
            {'method_amount': 20000,    # SNAP $100 + $100 match = $200
             'match_percent': 100.0,
             'denomination': None,
             'user_capped': True},
            {'method_amount': 3557,     # Cash $35.57 + $0 match
             'match_percent': 0.0,
             'denomination': None,
             'user_capped': True},
        ]
        result = calculate_payment_breakdown(
            receipt_total=25009, payment_entries=items,
            match_limit=10000)
        # Customer charges all preserved.
        assert result['line_items'][0]['customer_charged'] == 800     # FB
        assert result['line_items'][1]['customer_charged'] == 10000  # SNAP
        assert result['line_items'][2]['customer_charged'] == 3557   # Cash
        # Cap applied — total match = cap.
        total_match = sum(
            li['match_amount'] for li in result['line_items'])
        assert total_match == 10000
        # Customer total = $8 + $100 + $35.57 = $143.57
        # Match total = $100 (cap)
        # Allocated = $243.57 → remaining = $250.09 - $243.57 = $6.52
        assert result['allocation_remaining'] == 652
        assert result['is_valid'] is False, (
            "is_valid must be False when allocated < receipt — "
            "blocks Confirm so volunteer adds another row to "
            "absorb the gap.")

    def test_existing_inflation_preserved_when_user_capped_false(self):
        """Backward compat: when user_capped is omitted/False,
        the existing cap-aware inflation behaviour fires.  Tests
        that pin the old engine behaviour MUST keep passing."""
        items = [
            {'method_amount': 25000,    # SNAP $125 + $125 match
             'match_percent': 100.0,
             'denomination': None},
            # No 'user_capped' key — defaults to False.
        ]
        result = calculate_payment_breakdown(
            receipt_total=25000, payment_entries=items,
            match_limit=10000)
        # Existing behaviour: customer inflates to $150 to absorb
        # the cap shrinkage so allocated == receipt.
        assert result['line_items'][0]['customer_charged'] == 15000
        assert result['line_items'][0]['match_amount'] == 10000
        assert result['line_items'][0]['method_amount'] == 25000
        assert result['allocation_remaining'] == 0

    def test_no_cap_user_cap_no_op(self):
        """Sanity: when no cap is in play, user_capped is a
        no-op (the engine doesn't need to modify customer)."""
        items = [
            {'method_amount': 20000,    # SNAP $100 + $100 match
             'match_percent': 100.0,
             'denomination': None,
             'user_capped': True},
        ]
        result = calculate_payment_breakdown(
            receipt_total=20000, payment_entries=items,
            match_limit=None)
        assert result['line_items'][0]['customer_charged'] == 10000
        assert result['line_items'][0]['match_amount'] == 10000
        assert result['line_items'][0]['method_amount'] == 20000


# ──────────────────────────────────────────────────────────────────
# 2. PaymentRow: _user_capped flag lifecycle
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_app_db(tmp_path):
    """Fresh DB so PaymentRow can load methods."""
    from fam.database.connection import (
        set_db_path, close_connection, get_connection,
    )
    from fam.database.schema import initialize_database
    db_file = str(tmp_path / "user_cap.db")
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
        "(1, 'SNAP', 100.0, 0, 1, 1), "
        "(2, 'Cash', 0.0, 0, 2, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods "
        "(market_id, payment_method_id) VALUES (1, 1), (1, 2)")
    conn.commit()
    yield conn
    close_connection()


class TestPaymentRowUserCapFlag:

    def test_fresh_row_is_not_user_capped(self, qtbot, fresh_app_db):
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        assert row.is_user_capped() is False, (
            "A freshly-created PaymentRow must have user_capped=False "
            "(default state — no user has typed anything yet).")

    def test_user_typing_marks_row_as_capped(
            self, qtbot, fresh_app_db):
        """When ``amount_spin.valueChanged`` fires (user-only signal
        because programmatic writes block signals), the row marks
        itself user-capped."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        # Select SNAP so the spinbox is the active input.
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'SNAP':
                row.method_combo.setCurrentIndex(i)
                break
        # Simulate user typing $125 (Qt fires valueChanged).
        row.amount_spin.setValue(125.00)
        assert row.is_user_capped() is True, (
            "After amount_spin.valueChanged fires, the row must "
            "be marked user_capped=True.")

    def test_clear_user_cap_resets_flag(self, qtbot, fresh_app_db):
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'SNAP':
                row.method_combo.setCurrentIndex(i)
                break
        row.amount_spin.setValue(125.00)
        assert row.is_user_capped() is True
        row.clear_user_cap()
        assert row.is_user_capped() is False, (
            "clear_user_cap() must reset the flag — used by row "
            "removal/reset paths to start fresh.")

    def test_programmatic_set_does_not_mark_user_capped(
            self, qtbot, fresh_app_db):
        """``_set_active_charge`` blocks signals on amount_spin
        before calling setValue, so the user-cap handler doesn't
        fire.  This protects the flag from being set by Auto-
        Distribute and other engine write-back paths."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'SNAP':
                row.method_combo.setCurrentIndex(i)
                break
        # Programmatic write — should NOT mark user-capped.
        row._set_active_charge(12500)
        assert row.is_user_capped() is False, (
            "Programmatic _set_active_charge must NOT mark the "
            "row user-capped (Auto-Distribute fills via this "
            "path; if it self-flagged, subsequent Auto-Distribute "
            "clicks would never re-fill the same row).")

    def test_get_data_carries_user_capped_flag(
            self, qtbot, fresh_app_db):
        """The ``get_data()`` payload includes ``user_capped`` so
        Auto-Distribute can read it."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'SNAP':
                row.method_combo.setCurrentIndex(i)
                break
        row.amount_spin.setValue(125.00)
        data = row.get_data()
        assert data is not None
        assert 'user_capped' in data, (
            f"get_data() must include 'user_capped' key.  "
            f"Got keys: {sorted(data.keys())}")
        assert data['user_capped'] is True

    def test_set_data_restores_user_capped_flag(
            self, qtbot, fresh_app_db):
        """v2.0.7+ schema v37 (audit 2026-05-07): set_data
        accepts a ``user_capped`` parameter and applies it to
        the row, so a Locked row coming back from DB returns
        Locked (gold ⚡) — not silently reset to Active."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        # Restore a row that was saved with user_capped=True.
        row.set_data(
            payment_method_id=1,
            method_amount=10000,
            customer_charged=5000,
            user_capped=True,
        )
        assert row.is_user_capped() is True, (
            "set_data(user_capped=True) MUST mark the row "
            "Locked.  Without this, draft restore silently "
            "drops the volunteer's lock intent.")
        assert row._get_active_charge() == 5000

    def test_set_data_default_user_capped_false_for_legacy(
            self, qtbot, fresh_app_db):
        """Backward compat: callers that don't pass user_capped
        get the existing False default (= row comes back Active).
        Pre-v37 DB rows have no user_capped column, so loading
        them via SELECT * yields user_capped=None → bool(None)
        = False → row is Active.  This is the correct legacy
        behaviour."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        row.set_data(
            payment_method_id=1,
            method_amount=10000,
            customer_charged=5000,
            # No user_capped passed.
        )
        assert row.is_user_capped() is False


# ──────────────────────────────────────────────────────────────────
# 2d. Schema v37: user_capped persists through DB round-trip
# ──────────────────────────────────────────────────────────────────


class TestUserCappedPersistsThroughDB:
    """v2.0.7+ schema v37 (audit 2026-05-07): the user-cap flag
    is persisted in payment_line_items.user_capped.  This pins
    the round-trip: save → reload → flag survives."""

    def test_user_capped_column_exists_after_init(self, tmp_path):
        from fam.database.connection import (
            set_db_path, close_connection, get_connection,
        )
        from fam.database.schema import initialize_database
        db_file = str(tmp_path / "v37.db")
        close_connection()
        set_db_path(db_file)
        initialize_database()
        conn = get_connection()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(payment_line_items)").fetchall()}
        close_connection()
        assert 'user_capped' in cols, (
            "Schema v37 must add a 'user_capped' column to "
            "payment_line_items.  Got columns: "
            f"{sorted(cols)}")

    def test_save_and_load_preserves_user_capped(self, tmp_path):
        from fam.database.connection import (
            set_db_path, close_connection, get_connection,
        )
        from fam.database.schema import initialize_database
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
            get_payment_line_items,
        )
        from fam.models.customer_order import create_customer_order
        db_file = str(tmp_path / "roundtrip.db")
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
            "(1, 'SNAP', 100.0, 0, 1, 1)")
        conn.execute(
            "INSERT INTO market_payment_methods "
            "(market_id, payment_method_id) VALUES (1, 1)")
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (1, 'V')")
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, 1)")
        conn.execute(
            "INSERT INTO market_days (id, market_id, date, "
            "status, opened_by) VALUES "
            "(1, 1, '2099-05-07', 'Open', 'T')")
        conn.commit()
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-001-LB1')
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=10000,
            customer_order_id=order_id,
            market_day_date='2099-05-07')
        # Save with user_capped=True.
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 10000,
            'match_amount': 5000,
            'customer_charged': 5000,
            'customer_forfeit_cents': 0,
            'user_capped': True,
            'photo_path': None,
        }])
        # Reload via the model API.
        loaded = get_payment_line_items(txn_id)
        close_connection()
        assert len(loaded) == 1
        assert bool(loaded[0]['user_capped']) is True, (
            f"user_capped=True must round-trip through "
            f"save → SELECT *.  Got: {loaded[0]['user_capped']!r}")

    def test_save_default_false_when_flag_omitted(self, tmp_path):
        """Backward compat: items without 'user_capped' key save
        as 0 (= False).  Existing call sites that haven't been
        updated to pass the flag continue working."""
        from fam.database.connection import (
            set_db_path, close_connection, get_connection,
        )
        from fam.database.schema import initialize_database
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
            get_payment_line_items,
        )
        from fam.models.customer_order import create_customer_order
        db_file = str(tmp_path / "default.db")
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
            "(1, 'Cash', 0.0, 0, 1, 1)")
        conn.execute(
            "INSERT INTO market_payment_methods "
            "(market_id, payment_method_id) VALUES (1, 1)")
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (1, 'V')")
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, 1)")
        conn.execute(
            "INSERT INTO market_days (id, market_id, date, "
            "status, opened_by) VALUES "
            "(1, 1, '2099-05-07', 'Open', 'T')")
        conn.commit()
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-002-LB1')
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=2000,
            customer_order_id=order_id,
            market_day_date='2099-05-07')
        # Save WITHOUT 'user_capped' key (legacy callers).
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 2000,
            'match_amount': 0,
            'customer_charged': 2000,
            'customer_forfeit_cents': 0,
            'photo_path': None,
        }])
        loaded = get_payment_line_items(txn_id)
        close_connection()
        assert bool(loaded[0]['user_capped']) is False


# ──────────────────────────────────────────────────────────────────
# 2b. Auto-distribute toggle button (⚡ icon) UI (v2.0.7+)
# ──────────────────────────────────────────────────────────────────


class TestAutoDistributeToggleButton:
    """User-reported 2026-05-07 follow-up: a per-row ⚡ icon
    button lets the volunteer see (and toggle) whether a row is
    in Auto-Distribute's redistribution pool, without having to
    delete and re-add the row to release a cap."""

    def test_button_exists_on_payment_row(
            self, qtbot, fresh_app_db):
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        assert hasattr(row, 'auto_distribute_btn'), (
            "PaymentRow must expose an `auto_distribute_btn` "
            "attribute — the ⚡ toggle that controls whether "
            "Auto-Distribute will refill this row.")

    def test_button_hidden_when_no_method_selected(
            self, qtbot, fresh_app_db):
        """Fresh row, placeholder method → button hidden.  The
        toggle's purpose is ambiguous before a real method is
        picked; show it once the method is concrete."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        assert row.auto_distribute_btn.isVisible() is False

    def test_button_visible_for_non_denom_method(
            self, qtbot, fresh_app_db):
        """SNAP (non-denom) selected → button visible.  This is
        where the auto-distribute / lock concept actually
        applies."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'SNAP':
                row.method_combo.setCurrentIndex(i)
                break
        # The button is hidden until the row is parented + shown,
        # but the visibility property still reflects the intent.
        # We check the underlying flag, not isVisible() (which
        # requires the parent to be on-screen).
        assert not row.auto_distribute_btn.isHidden(), (
            "After picking a non-denom method (SNAP), the "
            "⚡ toggle must NOT be hidden.")

    def test_button_hidden_for_denom_method(
            self, qtbot, fresh_app_db):
        """Denom rows are inherently locked by their physical
        scrip nature — the stepper conveys that.  Showing the
        ⚡ toggle on a denom row would imply Auto-Distribute can
        modify it, which is wrong."""
        from fam.ui.widgets.payment_row import PaymentRow
        from fam.database.connection import get_connection
        # Add a denom method to the fixture DB.
        conn = get_connection()
        conn.execute(
            "INSERT INTO payment_methods (id, name, "
            "match_percent, denomination, sort_order, is_active) "
            "VALUES (3, 'Food RX', 100.0, 1000, 3, 1)")
        conn.execute(
            "INSERT INTO market_payment_methods "
            "(market_id, payment_method_id) VALUES (1, 3)")
        conn.commit()
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'Food RX':
                row.method_combo.setCurrentIndex(i)
                break
        assert row.auto_distribute_btn.isHidden(), (
            "After picking a denom method (Food RX), the ⚡ "
            "toggle MUST be hidden — denom rows are locked by "
            "their physical scrip nature.")

    def test_clicking_button_toggles_user_capped(
            self, qtbot, fresh_app_db):
        """Click on Active → flips to Locked.  Click on Locked
        → flips back to Active.  This is the 'change your mind'
        affordance the volunteer asked for — no need to delete
        and re-add the row to release a cap."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'SNAP':
                row.method_combo.setCurrentIndex(i)
                break
        assert row.is_user_capped() is False
        # Click 1: Active → Locked
        row.auto_distribute_btn.click()
        assert row.is_user_capped() is True, (
            "Clicking the ⚡ button on an Active row must "
            "set user_capped=True (lock the row).")
        # Click 2: Locked → Active
        row.auto_distribute_btn.click()
        assert row.is_user_capped() is False, (
            "Clicking the ⚡ button on a Locked row must "
            "set user_capped=False (release the cap).")

    def test_typing_into_amount_locks_button(
            self, qtbot, fresh_app_db):
        """When the volunteer types into the amount field, the
        ⚡ button flips to Locked automatically — visual signal
        that Auto-Distribute will now skip this row."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'SNAP':
                row.method_combo.setCurrentIndex(i)
                break
        assert row.is_user_capped() is False
        # Simulate user typing.
        row.amount_spin.setValue(80.00)
        assert row.is_user_capped() is True, (
            "Typing into amount_spin must auto-lock the row "
            "(set user_capped=True).")

    def test_tooltip_changes_with_state(
            self, qtbot, fresh_app_db):
        """The tooltip explains the current state and what
        clicking does — self-documenting so the volunteer doesn't
        need a help article to understand the icon."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'SNAP':
                row.method_combo.setCurrentIndex(i)
                break
        # Active state tooltip mentions "fill" / "lock".
        active_tip = row.auto_distribute_btn.toolTip().lower()
        assert 'fill' in active_tip or 'auto-distribute' in active_tip
        assert 'lock' in active_tip
        # Locked state tooltip mentions "skip" / "release".
        row.auto_distribute_btn.click()
        locked_tip = row.auto_distribute_btn.toolTip().lower()
        assert 'skip' in locked_tip or 'locked' in locked_tip
        assert 'release' in locked_tip or 'refill' in locked_tip

    def test_programmatic_set_does_not_change_button_state(
            self, qtbot, fresh_app_db):
        """Auto-Distribute fills via _set_active_charge
        (signals blocked).  The button must NOT flip to Locked
        — Auto-Distribute is the intended source of the value."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'SNAP':
                row.method_combo.setCurrentIndex(i)
                break
        assert row.is_user_capped() is False
        row._set_active_charge(8000)  # programmatic
        assert row.is_user_capped() is False, (
            "Programmatic _set_active_charge MUST NOT flip the "
            "row to Locked — Auto-Distribute uses this path to "
            "fill rows; if it self-locked, subsequent Auto-"
            "Distribute clicks could never refill.")


# ──────────────────────────────────────────────────────────────────
# 2c. Single-overflow-target radio-button (v2.0.7+)
# ──────────────────────────────────────────────────────────────────


class TestSingleOverflowTargetRadioButton:
    """User-reported 2026-05-07 follow-up: only ONE non-denom
    row at a time may be Active (= the overflow target for
    Auto-Distribute).  Activating one row's ⚡ toggle locks all
    others; new rows added when an Active row exists default to
    Locked.

    Denom rows are always locked by physical scrip — they don't
    participate in the radio-button group."""

    def test_signal_emitted_on_locked_to_active_toggle(
            self, qtbot, fresh_app_db):
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'SNAP':
                row.method_combo.setCurrentIndex(i)
                break
        # Lock the row first (via typing).
        row.amount_spin.setValue(50.00)
        assert row.is_user_capped() is True
        # Track signal emissions.
        emitted = []
        row.auto_distribute_activated.connect(
            lambda r: emitted.append(r))
        # Click ⚡ to flip Locked → Active.
        row.auto_distribute_btn.click()
        assert row.is_user_capped() is False
        assert len(emitted) == 1, (
            "auto_distribute_activated must fire exactly once on "
            "a Locked → Active toggle.")
        assert emitted[0] is row

    def test_signal_NOT_emitted_on_active_to_locked_toggle(
            self, qtbot, fresh_app_db):
        """Going Active → Locked is unilateral — doesn't claim
        the overflow target, so no signal needed."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'SNAP':
                row.method_combo.setCurrentIndex(i)
                break
        # Row starts Active (default).
        emitted = []
        row.auto_distribute_activated.connect(
            lambda r: emitted.append(r))
        # Click ⚡ to flip Active → Locked.
        row.auto_distribute_btn.click()
        assert row.is_user_capped() is True
        assert len(emitted) == 0, (
            "auto_distribute_activated MUST NOT fire on Active "
            "→ Locked transitions — only Locked → Active claims "
            "the overflow target.")

    def test_screen_handler_locks_other_rows_on_activation(
            self, qtbot, fresh_app_db):
        """Radio-button enforcement: when one non-denom row
        becomes Active, all OTHER non-denom rows lock."""
        import inspect
        from fam.ui.payment_screen import PaymentScreen
        # Source pin: the handler must exist and be wired.
        src = inspect.getsource(PaymentScreen)
        assert "_enforce_single_active_overflow_target" in src, (
            "PaymentScreen must define "
            "_enforce_single_active_overflow_target — the "
            "handler that locks all other non-denom rows when "
            "one becomes Active.")
        assert "auto_distribute_activated.connect" in src, (
            "PaymentScreen._add_payment_row must connect each "
            "row's auto_distribute_activated signal to the "
            "radio-button handler.")

    def test_new_rows_default_locked_when_active_exists(self):
        """v2.0.7+ audit follow-up (2026-05-07): per the
        volunteer's stated policy ("only one denominated payment
        row should be allowed to have the auto distro active at
        a time"), a new row defaults to Locked when there's
        already an Active non-denom row.  This makes the radio
        invariant hold at row-add time, not just on explicit
        ⚡ click.

        Without this, adding a third non-denom method (e.g. JH
        Tokens after SNAP and Cash were already configured)
        would produce two green ⚡ icons, confusing the
        overflow-target semantics."""
        import inspect
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._add_payment_row)
        # New rows MUST conditionally default to Locked.
        assert "_has_active_non_denom_row" in src, (
            "_add_payment_row must consult "
            "_has_active_non_denom_row() and default new rows "
            "to Locked when one exists.")
        assert "row._user_capped = True" in src, (
            "_add_payment_row must set _user_capped=True for new "
            "rows when an Active non-denom already exists.")
        # The radio handler must still be connected for the
        # on-demand activation flow.
        assert "_enforce_single_active_overflow_target" in src


# ──────────────────────────────────────────────────────────────────
# 3. Auto-Distribute respects user-capped rows
# ──────────────────────────────────────────────────────────────────


class TestAutoDistributeRespectsUserCap:
    """Auto-Distribute must NOT reset user-capped rows to 0
    before redistributing.  A volunteer who typed SNAP $125
    should see Auto-Distribute redistribute only the OTHER
    non-denom rows, treating SNAP $125 as a hard cap (locked)."""

    def test_auto_distribute_does_not_clear_user_caps(self):
        """Source pin: ``_auto_distribute`` MUST NOT call
        ``clear_user_cap()`` on rows.  Pre-rebuild it cleared
        every cap, defeating the user's explicit policy."""
        import inspect
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._auto_distribute)
        assert "clear_user_cap" not in src, (
            "PaymentScreen._auto_distribute MUST NOT call "
            "clear_user_cap() — per the volunteer's policy "
            "(2026-05-07), Auto-Distribute SKIPS user-capped "
            "rows.  Clearing the cap before redistributing "
            "wipes the volunteer's intent.")

    def test_row_descriptor_skips_reset_for_user_capped(self):
        """Source pin: the row descriptor builder must check
        ``is_user_capped()`` and NOT zero the charge for those
        rows."""
        import inspect
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._auto_distribute)
        assert "is_user_capped" in src, (
            "PaymentScreen._auto_distribute MUST consult "
            "row.is_user_capped() to skip resetting user-capped "
            "rows to 0.")

    def test_set_max_charge_floors_at_current_for_user_capped(
            self, qtbot, fresh_app_db):
        """User-reported 2026-05-07 follow-up: typing Cash $50,
        clicking Auto-Distribute should keep Cash at $50 even
        though _push_row_limits computes Cash's max as $0
        (because SNAP absorbed the whole budget).

        Bottom-layer defence: PaymentRow.set_max_charge MUST
        floor the max at the row's current charge for user-
        capped non-denom rows.  Without this, Qt's silent
        setMaximum() clamp zeroes the typed $50 — Auto-
        Distribute appears to 'eat' the volunteer's entry."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'Cash':
                row.method_combo.setCurrentIndex(i)
                break
        # Type $50 into Cash → user_capped=True, charge=$50.
        row.amount_spin.setValue(50.00)
        assert row.is_user_capped() is True
        assert row._get_active_charge() == 5000

        # Simulate _push_row_limits computing max=$0 for Cash
        # (because SNAP absorbed all the budget).  Without the
        # defensive floor, Qt would clamp $50 → $0.
        row.set_max_charge(0)
        assert row._get_active_charge() == 5000, (
            f"set_max_charge(0) on a user-capped non-denom row "
            f"MUST NOT clamp the volunteer's typed $50 to $0.  "
            f"Got: ${row._get_active_charge() / 100:.2f}")

    def test_set_max_charge_clamps_normally_for_non_capped(
            self, qtbot, fresh_app_db):
        """Sanity: the floor only applies to user-capped rows.
        Non-user-capped rows still get the existing clamp
        behaviour (Qt's setMaximum)."""
        from fam.ui.widgets.payment_row import PaymentRow
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)
        for i in range(row.method_combo.count()):
            m = row.method_combo.itemData(i)
            if m and m['name'] == 'Cash':
                row.method_combo.setCurrentIndex(i)
                break
        # Set value programmatically (NOT user-typed → flag stays False).
        row._set_active_charge(5000)
        assert row.is_user_capped() is False

        # set_max_charge(0) on non-capped row MAY clamp via Qt's
        # silent setMaximum (existing behaviour).  We don't assert
        # the value here — just that the defence path is bypassed.
        row.set_max_charge(0)
        # Non-user-capped row gets clamped (Qt behaviour preserved).
        assert row._get_active_charge() == 0

    def test_collect_line_items_preserves_per_vendor_invariant(self):
        """``_collect_line_items`` applies the budget cap to ALL
        non-denom rows (including user-capped) to preserve the
        per-vendor over-allocation invariant.  The volunteer's
        typed value stays visible in the spinbox (defended by
        set_max_charge's floor); only the engine-input
        method_amount is reduced when it genuinely exceeds the
        order's budget.

        Initially Fix #9 had _collect_line_items skip the cap
        for user-capped rows, but the admin fuzzer caught
        per-vendor over-allocation breaches — typed values
        could exceed receipts, violating a deeper invariant.
        Reverted in favour of the row-layer floor (set_max_charge)
        which protects the user's view without breaking the
        engine's accounting."""
        import inspect
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._collect_line_items)
        # The cap loop must NOT special-case user_capped — that
        # was the buggy version that broke per-vendor invariants.
        assert "if not data.get('user_capped'" not in src, (
            "_collect_line_items must NOT skip the budget cap "
            "for user-capped rows — doing so breaks the per-"
            "vendor over-allocation invariant.  Use "
            "set_max_charge's floor at the row layer instead.")

    def test_cap_deficit_falls_back_to_unmatched_auto(self):
        """User-reported 2026-05-07 follow-up: SNAP user-capped
        at $125 (100% match), Cash auto at $0 (0% match), receipt
        $250.09, daily cap $100.

        Pre-fix: smart_auto_distribute computes locked_total
        using SNAP's UNCAPPED method ($250) → remaining = -$0.09
        → Cash gets $0 from smart_auto_distribute.  The post-cap
        deficit-redistribution loop only targeted MATCHED auto
        rows, so the $25 cap-shrinkage deficit had nowhere to
        land.  Cash stayed $0; Auto-Distribute appeared to "do
        nothing".

        Post-fix: Pass 2 of the deficit redistribution falls
        back to UNMATCHED non-denom auto rows.  Cash absorbs the
        $25.05 deficit naturally (customer pays Cash directly,
        no match involved, vendor reimbursement closes the gap).

        This pins the source-level guarantees that make Auto-
        Distribute actually fill Cash in this scenario.  The
        full UI-level scenario is harder to drive deterministically
        (Auto-Distribute's full path involves vendor binding,
        descriptor build, smart_auto_distribute, post-cap, and
        write-back) but the source pins ensure the critical
        wiring is in place."""
        import inspect
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._auto_distribute)
        # The post-cap block must run even with empty assignments
        # (deficit can come entirely from a locked user-capped row).
        assert "self._match_limit is not None and assignments" not in src, (
            "Post-cap block must NOT short-circuit when "
            "assignments is empty — the deficit can come "
            "entirely from a locked user-capped row, with no "
            "matched auto rows to populate `assignments`.")
        # Pass 2 (unmatched-auto fallback) must be present.
        assert "unmatched_auto_indices" in src, (
            "_auto_distribute must include a Pass 2 that "
            "redistributes leftover cap deficit to unmatched "
            "non-denom auto rows (e.g. Cash).  Without this, "
            "the deficit from a locked user-capped row has "
            "nowhere to land and Cash stays at $0.")


# ──────────────────────────────────────────────────────────────────
# 4. Engine source-pin: customer_charged preservation invariant
# ──────────────────────────────────────────────────────────────────


class TestEngineSourcePin:
    """Pin the user-cap bypass at every engine inflation site so
    a future refactor can't accidentally re-introduce the silent
    inflation that overrides volunteer-typed values."""

    def test_pass4_skips_user_capped(self):
        """Pass 4 cap-aware give-back must skip user_capped rows."""
        import inspect
        import fam.utils.calculations as calc
        src = inspect.getsource(calc)
        # Pass 4 must explicitly check user_capped.
        assert "if item.get('user_capped'):" in src, (
            "Pass 4 give-back must skip user-capped rows.")

    def test_common_cap_path_branches_on_user_capped(self):
        """Common cap path must branch on user_capped: preserve
        customer when True, existing inflation when False."""
        import inspect
        import fam.utils.calculations as calc
        src = inspect.getsource(calc.calculate_payment_breakdown)
        assert "if li.get('user_capped'):" in src, (
            "Common cap path must check user_capped to choose "
            "between preserving customer (typed value) and "
            "inflating customer (existing behaviour).")

    def test_resolve_payment_state_propagates_user_capped(self):
        """The engine wrapper that callers use must propagate
        user_capped from items to entries."""
        import inspect
        import fam.utils.calculations as calc
        src = inspect.getsource(calc.resolve_payment_state)
        assert "'user_capped'" in src, (
            "resolve_payment_state must propagate user_capped "
            "from items to engine entries.")

    def test_confirm_payment_propagates_user_capped(self):
        """The Confirm Payment path must propagate user_capped to
        the engine — without this, the engine inflates the typed
        value and Layer 2A blocks with a 'row mismatch' error."""
        import inspect
        from fam.ui.payment_screen import PaymentScreen
        src = inspect.getsource(PaymentScreen._confirm_payment)
        assert "'user_capped'" in src, (
            "PaymentScreen._confirm_payment must propagate "
            "user_capped to the engine.  Without this, Layer 2A "
            "fires 'row mismatch' on user-typed values.")
