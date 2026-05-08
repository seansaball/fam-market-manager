"""When a customer hands a denomination unit larger than the
receipt remaining, Phase B forfeit MUST preserve the actual
customer-paid amount in saved data + reports.

User-reported scenario (2026-05-07):

  * Customer hands 1 Food RX token ($10 face, 100% match) to a
    single-vendor receipt totalling $6.52.
  * Engine's denomination-forfeit math correctly produces:
      customer_charged   = $6.52  (effective contribution)
      match_amount       = $0.00  (FAM match fully forfeited)
      method_amount      = $6.52  (vendor reimbursement)
      customer_forfeit   = $3.48  (token value not absorbed)
  * BUT the v2.0.7 ``resolve_payment_state`` snap-back loop
    (which rounds denom rows' customer_charged DOWN to the
    nearest denomination multiple) over-corrected $6.52 → $0
    and dumped $6.52 into ``match_amount``.
  * Reports then showed:
      Vendor Reimbursement Food RX column: $0.00 (wrong; was $6.52)
      Vendor Reimbursement FAM Match:      $6.52 (wrong; was $0.00)
      Detailed Ledger Customer Paid:       $0.00 (wrong)
      Detailed Ledger FAM Match:           $6.52 (wrong)
    — falsely implying FAM funded the entire transaction and
    the customer paid nothing.

Fix: snap-back skips rows with ``customer_forfeit_cents > 0``.
That field is set ONLY by Phase B forfeit, so it's an
unambiguous "this sub-denomination customer_charged is
intentional" signal that distinguishes legitimate forfeit
from drift / legacy-bad-data needing self-heal.

This file pins:

  1. **Phase B forfeit is preserved through snap-back.** The
     user-reported scenario produces customer_charged=$6.52,
     match=$0, forfeit=$3.48 in the saved state.
  2. **Forfeit-row invariant.** Even though customer_charged is
     not a multiple of denomination, ``customer_charged +
     customer_forfeit_cents`` IS — i.e. the customer's physical
     token count is recoverable.
  3. **Self-heal of legacy bad data still works.** Rows with
     no forfeit_cents flag and sub-denomination cc still snap
     to the nearest multiple (regression: don't break the
     v2.0.7 self-heal path).
  4. **Smaller token examples.**  $2 Food Bucks, $5 FMNP — same
     forfeit semantics across denominations.
"""

from fam.utils.calculations import (
    resolve_payment_state, charge_to_method_amount,
)


def _vendor_aware_forfeit_for_test(result, items, overage):
    """Test stub mimicking PaymentScreen._apply_denomination_forfeit
    for a single-vendor scenario (Phase A reduces match, Phase B
    reduces customer_charged + tags forfeit_cents)."""
    v_remain = overage
    for i, li in enumerate(result['line_items']):
        if v_remain <= 0:
            break
        if not items[i].get('denomination'):
            continue
        # Phase A
        if li['match_amount'] > 0:
            match_red = min(v_remain, li['match_amount'])
            li['match_amount'] -= match_red
            li['method_amount'] -= match_red
            items[i]['method_amount'] = li['method_amount']
            items[i]['match_amount'] = li['match_amount']
            v_remain -= match_red
        # Phase B
        if v_remain > 0 and li['customer_charged'] > 0:
            cust_red = min(v_remain, li['customer_charged'])
            li['customer_charged'] -= cust_red
            li['method_amount'] -= cust_red
            li['customer_forfeit_cents'] = (
                li.get('customer_forfeit_cents', 0) + cust_red)
            items[i]['method_amount'] = li['method_amount']
            items[i]['customer_charged'] = li['customer_charged']
            items[i]['customer_forfeit_cents'] = (
                items[i].get('customer_forfeit_cents', 0) + cust_red)
            v_remain -= cust_red


class TestPhaseBForfeitPreservedThroughSnapBack:
    """The user-reported v2.0.7 incident: Food RX $10 token to
    $6.52 receipt.  Saved data must show customer_charged=$6.52,
    NOT $0."""

    def test_food_rx_one_token_under_receipt(self):
        items = [{
            'method_amount': charge_to_method_amount(1000, 100),  # $20
            'match_percent': 100,
            'match_amount': 1000,
            'customer_charged': 1000,
            'denomination': 1000,
            'method_name_snapshot': 'Food RX',
        }]
        resolve_payment_state(
            652, items,
            apply_denomination_forfeit_fn=_vendor_aware_forfeit_for_test)
        assert items[0]['customer_charged'] == 652, (
            f"Phase B forfeit must preserve customer_charged at "
            f"$6.52 (the effective customer contribution).  "
            f"Got: ${items[0]['customer_charged']/100:.2f}.  If "
            f"this is $0, the snap-back is over-correcting and "
            f"the user-reported v2.0.7 incident has regressed.")
        assert items[0]['match_amount'] == 0, (
            f"FAM match must be $0 (fully forfeited).  "
            f"Got: ${items[0]['match_amount']/100:.2f}.  If this "
            f"is $6.52, the snap-back dumped the customer's "
            f"contribution into match.")
        assert items[0]['method_amount'] == 652, (
            "method_amount (vendor reimbursement) must equal "
            "receipt total.")
        assert items[0]['customer_forfeit_cents'] == 348, (
            "customer_forfeit_cents must record the unaccounted "
            "$3.48 of token value the customer handed over but "
            "didn't translate to vendor reimbursement.")

    def test_phase_b_invariant_holds(self):
        """Even though customer_charged ($6.52) isn't aligned to
        the $10 denomination, ``customer_charged +
        customer_forfeit_cents`` ($10) IS.  This invariant lets
        downstream consumers reconstruct the physical token count
        from the saved row + forfeit field."""
        items = [{
            'method_amount': charge_to_method_amount(1000, 100),
            'match_percent': 100,
            'match_amount': 1000,
            'customer_charged': 1000,
            'denomination': 1000,
        }]
        resolve_payment_state(
            652, items,
            apply_denomination_forfeit_fn=_vendor_aware_forfeit_for_test)
        cc = items[0]['customer_charged']
        forfeit = items[0]['customer_forfeit_cents']
        denom = items[0]['denomination']
        assert (cc + forfeit) % denom == 0, (
            f"Phase B forfeit invariant broken: customer_charged "
            f"({cc}) + forfeit ({forfeit}) must be a multiple of "
            f"denomination ({denom}).  Got: {cc + forfeit} "
            f"(remainder {(cc + forfeit) % denom}).")
        assert (cc + forfeit) == 1000, (
            "Customer's physical handout (1 token × $10) must be "
            "exactly recoverable from cc + forfeit.")

    def test_food_bucks_two_dollar_token_under_receipt(self):
        """$2 Food Bucks on a $0.47 receipt — extreme case."""
        items = [{
            'method_amount': charge_to_method_amount(200, 100),  # $4
            'match_percent': 100,
            'match_amount': 200,
            'customer_charged': 200,
            'denomination': 200,
            'method_name_snapshot': 'JH Food Bucks',
        }]
        resolve_payment_state(
            47, items,
            apply_denomination_forfeit_fn=_vendor_aware_forfeit_for_test)
        assert items[0]['customer_charged'] == 47
        assert items[0]['match_amount'] == 0
        assert items[0]['customer_forfeit_cents'] == 153, (
            "Customer handed $2.00 token, receipt was $0.47, "
            "match forfeited entirely ($2 of forfeit absorbed by "
            "match), $1.53 of customer contribution forfeited.")
        assert (items[0]['customer_charged']
                + items[0]['customer_forfeit_cents']) == 200

    def test_fmnp_five_dollar_check_under_receipt(self):
        """$5 FMNP check on a $3 receipt — common FMNP scenario."""
        items = [{
            'method_amount': charge_to_method_amount(500, 100),  # $10
            'match_percent': 100,
            'match_amount': 500,
            'customer_charged': 500,
            'denomination': 500,
            'method_name_snapshot': 'FMNP',
        }]
        resolve_payment_state(
            300, items,
            apply_denomination_forfeit_fn=_vendor_aware_forfeit_for_test)
        assert items[0]['customer_charged'] == 300
        assert items[0]['match_amount'] == 0
        assert items[0]['customer_forfeit_cents'] == 200


class TestSelfHealStillWorks:
    """The v2.0.7 self-heal of legacy bad data must still
    fire on rows that LACK ``customer_forfeit_cents``.  Snap-back
    skipping is gated on forfeit_cents > 0, so legacy rows
    (no forfeit field) still self-heal as before."""

    def test_legacy_misaligned_row_self_heals(self):
        """A row with customer_charged=47, no forfeit field,
        denom=200 — represents legacy bad data from a buggy save.
        Snap-back must round customer_charged DOWN to 0 and
        absorb $0.47 into match."""
        items = [{
            'method_amount': 71,
            'match_percent': 100.0,
            'denomination': 200,
            'payment_method_id': 1,
            # No customer_forfeit_cents key — legacy data.
        }]
        resolve_payment_state(71, items)
        cc = items[0]['customer_charged']
        assert cc % 200 == 0, (
            f"Self-heal must round legacy misaligned cc to a "
            f"multiple of denom.  Got: {cc}.")
        # Sum invariant preserved.
        assert (items[0]['customer_charged']
                + items[0]['match_amount']
                == items[0]['method_amount']), (
            "Sum invariant broken by self-heal.")

    def test_explicit_zero_forfeit_still_self_heals(self):
        """A row with ``customer_forfeit_cents = 0`` (explicitly
        set, not just absent) is treated as no-forfeit — snap-back
        applies normally."""
        items = [{
            'method_amount': 71,
            'match_percent': 100.0,
            'customer_charged': 47,
            'match_amount': 24,
            'denomination': 200,
            'customer_forfeit_cents': 0,
        }]
        resolve_payment_state(71, items)
        cc = items[0]['customer_charged']
        assert cc % 200 == 0, (
            f"forfeit_cents=0 must NOT prevent self-heal.  "
            f"Got cc={cc}, expected multiple of 200.")


class TestSnapBackBeltAndSuspendersAssertion:
    """The snap-back's defensive assertion fires only if Phase B
    produced a corrupted state (cc + forfeit not a multiple of
    denom) — it should never fire under normal engine operation."""

    def test_assertion_does_not_fire_on_valid_phase_b_state(self):
        """Sanity: the user's exact scenario should NOT trip the
        defensive assertion."""
        items = [{
            'method_amount': charge_to_method_amount(1000, 100),
            'match_percent': 100,
            'match_amount': 1000,
            'customer_charged': 1000,
            'denomination': 1000,
        }]
        # Should not raise.
        resolve_payment_state(
            652, items,
            apply_denomination_forfeit_fn=_vendor_aware_forfeit_for_test)
        # Reaching here without AssertionError is the test passing.


class TestLiveSummaryNeverShowsNegativeRemainingForDenomOverage:
    """The PaymentScreen live summary must show $0 Remaining (not
    a negative phantom value) when a denomination overage triggers
    the engine's Phase A reduction.

    User-reported design feedback (2026-05-07): "we shouldn't see
    the order total go negative due to forfeited FAM match funds
    that never applied in the first place.  It should just stop
    the match logic once it meets the total and not exceed it
    with non applicable FAM match funds."

    Pre-fix: ``_update_summary_impl`` ran the engine, got back a
    pre-forfeit state with allocated > receipt, displayed
    "Remaining: -$X" briefly, then ran ``_apply_denomination_
    forfeit`` and updated ONLY the ``fam_match`` and
    ``customer_pays`` cards.  The ``allocated`` and ``remaining``
    cards stayed at the pre-forfeit phantom values.

    Fix: after forfeit, all four cards are re-written with the
    post-forfeit balanced totals.  Remaining shows $0.
    """

    def test_phase_a_only_summary_remaining_is_zero(
            self, qtbot, monkeypatch):
        """1 FMNP token ($5 face, 100% match) on a $9 receipt —
        Phase A reduces match by $1, Phase B does NOT fire (token
        face value still reaches the vendor in full).  Live
        summary must show Remaining $0, no warning, no phantom
        negative number."""
        from fam.database.connection import (
            set_db_path, close_connection, get_connection,
        )
        from fam.database.schema import initialize_database
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        from fam.ui.payment_screen import PaymentScreen
        import tempfile
        tmpdir = tempfile.mkdtemp()
        db_file = f"{tmpdir}/live_summary_phase_a.db"
        close_connection()
        set_db_path(db_file)
        initialize_database()
        conn = get_connection()
        conn.execute("INSERT INTO markets (id, name, "
                     "daily_match_limit, match_limit_active) VALUES "
                     "(1, 'M', 100000, 1)")
        conn.execute("INSERT INTO payment_methods (id, name, "
                     "match_percent, denomination, sort_order, "
                     "is_active) VALUES "
                     "(1, 'FMNP', 100.0, 500, 1, 1)")
        conn.execute("INSERT INTO market_payment_methods "
                     "(market_id, payment_method_id) VALUES (1, 1)")
        conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
        conn.execute("INSERT INTO market_vendors (market_id, "
                     "vendor_id) VALUES (1, 1)")
        conn.execute("INSERT INTO vendor_payment_methods "
                     "(vendor_id, payment_method_id) VALUES (1, 1)")
        conn.execute("INSERT INTO market_days (id, market_id, "
                     "date, status, opened_by) VALUES "
                     "(1, 1, '2099-05-01', 'Open', 'T')")
        conn.commit()
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-001-LB1')
        create_transaction(market_day_id=1, vendor_id=1,
                            receipt_total=900,
                            customer_order_id=order_id,
                            market_day_date='2099-05-01')

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        # Pick the FMNP method on row 0 and stepper-set 1 token.
        row = screen._payment_rows[0]
        for i in range(row.method_combo.count()):
            data = row.method_combo.itemData(i)
            if data and data.get('name') == 'FMNP':
                row.method_combo.setCurrentIndex(i)
                break
        row._stepper.setValue(500)  # 1 token = $5
        screen._update_summary()

        remaining_text = screen.summary_row.cards['remaining'].value_label.text()
        # Post-fix: remaining shows $0.00 (post-forfeit balanced)
        assert remaining_text in ('$0.00', '0.00'), (
            f"Phase A only — Remaining card must show $0 "
            f"(forfeit already balanced the math).  "
            f"Got: {remaining_text!r}")
        # Warning must NOT show (Phase A is silent per policy)
        assert not screen.denom_overage_warning.isVisible(), (
            "Phase A only — inline warning must be hidden "
            "(Phase A reductions are silent per the v2.0.7 final "
            "policy).")
        close_connection()

    def test_phase_b_summary_remaining_is_zero_and_warning_shows(
            self, qtbot, monkeypatch):
        """1 Food RX ($10 face, 100% match) on a $6.52 receipt —
        Phase A consumes all match ($10), Phase B forfeits
        $3.48 of customer token value.  Live summary must show
        Remaining $0 (balanced) AND the warning must surface
        the $3.48 customer forfeit."""
        from fam.database.connection import (
            set_db_path, close_connection, get_connection,
        )
        from fam.database.schema import initialize_database
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        from fam.ui.payment_screen import PaymentScreen
        import tempfile
        tmpdir = tempfile.mkdtemp()
        db_file = f"{tmpdir}/live_summary_phase_b.db"
        close_connection()
        set_db_path(db_file)
        initialize_database()
        conn = get_connection()
        conn.execute("INSERT INTO markets (id, name, "
                     "daily_match_limit, match_limit_active) VALUES "
                     "(1, 'M', 100000, 1)")
        conn.execute("INSERT INTO payment_methods (id, name, "
                     "match_percent, denomination, sort_order, "
                     "is_active) VALUES "
                     "(1, 'Food RX', 100.0, 1000, 1, 1)")
        conn.execute("INSERT INTO market_payment_methods "
                     "(market_id, payment_method_id) VALUES (1, 1)")
        conn.execute("INSERT INTO vendors (id, name) VALUES "
                     "(1, 'Fungetarian')")
        conn.execute("INSERT INTO market_vendors (market_id, "
                     "vendor_id) VALUES (1, 1)")
        conn.execute("INSERT INTO vendor_payment_methods "
                     "(vendor_id, payment_method_id) VALUES (1, 1)")
        conn.execute("INSERT INTO market_days (id, market_id, "
                     "date, status, opened_by) VALUES "
                     "(1, 1, '2099-05-01', 'Open', 'T')")
        conn.commit()
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-002-LB1')
        create_transaction(market_day_id=1, vendor_id=1,
                            receipt_total=652,
                            customer_order_id=order_id,
                            market_day_date='2099-05-01')

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        row = screen._payment_rows[0]
        for i in range(row.method_combo.count()):
            data = row.method_combo.itemData(i)
            if data and data.get('name') == 'Food RX':
                row.method_combo.setCurrentIndex(i)
                break
        row._stepper.setValue(1000)  # 1 token = $10
        screen._update_summary()

        remaining_text = screen.summary_row.cards['remaining'].value_label.text()
        assert remaining_text in ('$0.00', '0.00'), (
            f"Phase B engaged — Remaining card must show $0 "
            f"(post-forfeit balanced).  Got: {remaining_text!r}")
        # v2.0.7-final (Option B, schema v36): the legacy
        # ``denom_overage_warning`` label is gone.  The Customer
        # Forfeit summary card replaces it as the single source
        # of truth for Phase B token-value loss.  Verify the new
        # card shows the expected $3.48.
        forfeit_card_text = (
            screen.summary_row.cards['customer_forfeit']
            .value_label.text())
        assert forfeit_card_text == '$3.48', (
            f"Customer Forfeit card must show $3.48 for Phase B "
            f"scenario.  Got: {forfeit_card_text!r}")
        # Legacy warning must stay hidden — the card replaces it.
        assert screen.denom_overage_warning.isHidden() or \
               screen.denom_overage_warning.text() == '', (
            "Legacy denom_overage_warning must be hidden / "
            "empty — the Customer Forfeit card subsumes it.")
        # The card label itself says "Customer Forfeit" — that's
        # the volunteer's signal.  And the value shows the exact
        # $3.48 of Phase B forfeit (asserted above).
        close_connection()


class TestPaymentConfirmationDialogForfeitPolicy:
    """v2.0.7 final policy (user-reported 2026-05-07): the
    warning zone fires ONLY for Phase B forfeit (true customer-
    side token-value loss).  Phase A (FAM match reduction without
    token-value loss) is NOT surfaced as a forfeit anywhere — the
    customer never had the FAM match money to lose; FAM just
    contributes less when the receipt has no headroom.

    This pins the policy:

      1. Phase A only → NO warning zone (silent).
      2. Phase B engaged → warning zone with pure customer-forfeit
         language (no "FAM match forfeit" terminology, no Phase A
         breakdown leaked in).
    """

    def test_phase_a_only_does_not_show_warning_zone(self, qtbot):
        """Scenario: $5 FMNP check ($10 method @ 100% match) on a
        $9 receipt — receipt has headroom for some FAM match,
        $1 of match is reduced (Phase A only).  Customer's $5
        token face value reaches the vendor in full.  Per the
        final policy, this produces NO warning zone — Phase A
        reduction is silent."""
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        line_items = [{
            'method_amount': 900,    # post-Phase-A
            'match_amount': 400,     # was 500, reduced by 100
            'customer_charged': 500, # token face value preserved
            'customer_forfeit_cents': 0,  # no Phase B
        }]
        items = [{
            'method_name_snapshot': 'FMNP',
            'denomination': 500,
        }]
        dlg = PaymentConfirmationDialog(
            line_items, items, receipt_total=900,
            denom_overage=100, receipt_count=1)
        qtbot.addWidget(dlg)

        from PySide6.QtWidgets import QFrame
        warn = dlg.findChild(QFrame, 'denomOverageWarning')
        assert warn is None, (
            "Phase-A-only scenarios must NOT show the denom-overage "
            "warning zone — Phase A is a math-balancing reduction "
            "of FAM match, not a customer-side forfeit.  Got a "
            "warning frame anyway, which means the suppression "
            "gate is broken.")

    def test_phase_b_warning_says_customer_token_value(self, qtbot):
        """The user-reported v2.0.7 incident scenario: $10 Food RX
        token to $6.52 receipt.  Phase B fires (customer
        forfeit_cents = $3.48).  Warning must explicitly say the
        customer is losing token face value."""
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        line_items = [{
            'method_amount': 652,
            'match_amount': 0,
            'customer_charged': 652,
            'customer_forfeit_cents': 348,  # Phase B engaged
        }]
        items = [{
            'method_name_snapshot': 'Food RX',
            'denomination': 1000,
        }]
        dlg = PaymentConfirmationDialog(
            line_items, items, receipt_total=652,
            denom_overage=1348, receipt_count=1)
        qtbot.addWidget(dlg)

        from PySide6.QtWidgets import QFrame, QLabel
        warn = dlg.findChild(QFrame, 'denomOverageWarning')
        assert warn is not None, (
            "Phase B engaged — warning zone must fire.")
        labels = [w.text() for w in warn.findChildren(QLabel)]
        joined = ' '.join(labels).lower()
        # Title must call out customer forfeit (not FAM match)
        assert 'customer forfeit' in joined or \
               ('customer' in joined and 'denomination' in joined), (
            f"Phase-B warning must explicitly call out customer "
            f"forfeit / customer's denomination loss.  Got: "
            f"{labels}")
        # The exact $3.48 amount must be visible
        assert '3.48' in joined, (
            f"Phase-B warning must surface the exact customer-side "
            f"forfeit amount ($3.48 for the user's scenario).  "
            f"Got: {labels}")
        # NEGATIVE pin: the message must NOT include "FAM match
        # forfeit" terminology — per the final policy, Phase A
        # reduction is not a forfeit, even when it accompanies
        # a Phase B event.
        assert 'fam match forfeit' not in joined, (
            f"Phase B warning must NOT include 'FAM match forfeit' "
            f"language — Phase A is not a forfeit per the v2.0.7 "
            f"final policy.  Got: {labels}")
