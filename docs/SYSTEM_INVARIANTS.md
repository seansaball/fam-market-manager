# FAM Market Manager — System Invariants

> **Purpose:** This document is the canonical "mathematical contract"
> the application must obey at all times during all valid user flows.
> Every invariant has a unique ID and is enforced by code in
> `tests/_coherence.py::audit_screen`.  If a bug ever escapes our test
> suite again, the question to ask is: *"which invariant in this
> document was missing?"* — then add it here, add the check to the
> auditor, and add a regression test.
>
> **Drift policy:** any change to engine, save path, UI write-back,
> or report query MUST be accompanied by an audit-pass run before
> merge.  No exceptions.

---

## Notation

* All monetary values are **integer cents**.
* `Σ` over rows means sum across visible PaymentRow widgets.
* `Σ_li` over engine line items means sum across `result['line_items']`.
* `Σ_db` means sum across `payment_line_items` rows for confirmed/
  adjusted transactions.
* `±1¢` means within 1 cent (penny-reconciliation tolerance).
* `forfeit` = `customer_forfeit_cents` on a line item (post-Phase-B
  customer-side denom forfeit, see FINANCIAL_FORMULA.md §6).

---

## Layer 1 — Engine purity

| ID | Invariant |
|---|---|
| **E1** | `calculate_payment_breakdown(receipt, entries, cap)` is deterministic — same inputs always produce the same `result`. |
| **E2** | When `result.is_valid` is True, `Σ_li method_amount == receipt`. |
| **E3** | For every line item: `customer_charged + match_amount == method_amount`. |
| **E4** | When `match_was_capped` is True, `Σ_li match_amount <= match_limit`. |
| **E5** | `result.allocated_total == Σ_li method_amount`. |
| **E6** | `result.fam_subsidy_total == Σ_li match_amount`. |
| **E7** | `result.customer_total_paid == Σ_li customer_charged`. |

## Layer 2 — Forfeit pass (post `apply_denomination_forfeit`)

The canonical forfeit function lives in `fam.utils.calculations`
(v2.0.7-final consolidation, schema v36). Two phases:

* **Phase A** — FAM match reduction. Silent in the UI; no
  customer-side loss. The customer never had the FAM match money
  to lose; FAM is just contributing less because the receipt has
  no headroom. NOT counted in `customer_forfeit_cents`.
* **Phase B** — customer-side token-value forfeit. Real customer
  loss. When the denomination unit overshoots the receipt even
  after Phase A consumes all match, the excess portion of the
  customer's physical token doesn't reach the vendor. Tracked in
  `customer_forfeit_cents` on the line item AND surfaced in the
  Customer Forfeit summary card AND the Customer Forfeit
  reports column.

| ID | Invariant |
|---|---|
| **F1** | After forfeit, `Σ_li method_amount == receipt_total`. |
| **F2** | After forfeit, every per-vendor allocation `≤ vendor_receipt + 1¢`. |
| **F3** | `customer_forfeit_cents >= 0` for every line item. |
| **F4** | `customer_forfeit_cents > 0` only on denominated rows (Phase B is denom-only). |
| **F5** | **Phase B invariant:** when `customer_forfeit_cents > 0`, `(customer_charged + customer_forfeit_cents)` is an integer multiple of `denomination` (= unit_count × face value). The customer's physical token count is recoverable from the saved row. |
| **F6** | Per-line invariant `customer_charged + match_amount == method_amount` survives forfeit. **Phase A:** reduces `match_amount` + `method_amount` together (customer untouched). **Phase B:** reduces `customer_charged` + `method_amount` together (match was already 0). |

## Layer 3 — Engine ↔ DB

| ID | Invariant |
|---|---|
| **D1** | For every confirmed/adjusted transaction T: `Σ_db payment_line_items.method_amount == T.receipt_total`. |
| **D2** | For every saved row: `customer_charged + match_amount == method_amount` (enforced by SQL CHECK trigger `chk_pli_invariant_*`, schema v28+). |
| **D3** | Saved `customer_charged` equals engine's post-forfeit `customer_charged` for that line. |
| **D4** | `payment_method_id` on saved row matches the row's `method_name_snapshot`. |
| **D5** | Saved row `transaction_id` corresponds to the row's `bound_vendor_id` for denominated rows. |

## Layer 4 — Engine ↔ UI

These hold AFTER `_update_summary` completes (i.e. when the screen
is in a stable state, not mid-transition).

| ID | Invariant |
|---|---|
| **U1** | `row.spinbox_value == engine_li.customer_charged + engine_li.customer_forfeit_cents` (Layer 2A loosened — forfeit-aware equality). |
| **U2** | `row.match_label_cents + row.charge_cents == row.total_label_cents` (V5 — per-row visible math). |
| **U3** | When `forfeit_cents == 0`: `row.match_label == engine_li.match_amount` AND `row.total_label == engine_li.method_amount`. |
| **U4** | When `forfeit_cents > 0`: row labels show pre-forfeit (= `charge × pct`); the post-forfeit reduction surfaces only in summary cards / Collect panel. |
| **U5** | **Phase A/B post-forfeit:** `summary_card['fam_match'] == result.fam_subsidy_total` (after the forfeit pass has run; Phase A reduces this; Phase B doesn't). |
| **U6** | **Phase A/B post-forfeit:** `summary_card['customer_pays'] == result.customer_total_paid` (after the forfeit pass has run; Phase A doesn't touch this; Phase B reduces it). |
| **U7** | **v2.0.7-final unconditional:** `summary_card['allocated'] == result.allocated_total` (always post-forfeit). The card NEVER displays a phantom-negative remaining due to about-to-be-forfeited FAM match. The forfeit pass runs unconditionally in `_update_summary_impl` before the card is written. See FINANCIAL_FORMULA.md §6.6. |
| **U8** | For each row in `vendor_breakdown_table`: `Remaining = receipt - allocated_for_this_vendor`. |
| **U9** | `Σ vendor_breakdown.allocated == result.allocated_total` (within ±1¢). |
| **U10** | The "Collect from Customer" panel rows sum to `result.customer_total_paid`. |
| **U11** | **v2.0.7-final (Option B):** The legacy `denom_overage_warning` label is permanently hidden. Customer Forfeit information lives exclusively in the Customer Forfeit summary card (U13) and the PaymentConfirmationDialog warning zone. |
| **U12** | The Confirm button is disabled iff there's a hard error or required checkbox unchecked. |
| **U13** | **Customer Forfeit card (v2.0.7-final, Option B):** `summary_card['customer_forfeit'] == Σ_li customer_forfeit_cents`. Always visible; shows $0.00 when no Phase B forfeit. Phase A is NEVER counted here — only Phase B token-value loss. |
| **U14** | **User-cap engine respect (v2.0.7+, schema v37):** A non-denom row marked `user_capped=True` MUST have `engine_li.customer_charged == row.spinbox_value` regardless of cap state. The engine never inflates customer_charged on a user-capped row; cap-shrinkage surfaces as `allocation_remaining > 0` (Confirm blocked by `is_valid=False`). Enforced at every entries-build site (`_update_summary_impl`, `_confirm_payment`, `resolve_payment_state`, AdjustmentDialog `_update_customer_impact`). |
| **U15** | **User-cap UI lifecycle (v2.0.7+):** `row._user_capped` flips True when `amount_spin.valueChanged` fires (programmatic writes via `_set_active_charge` block signals on amount_spin so this handler fires only on genuine user typing). The flag persists across method changes, programmatic writes, engine round-trips, and DB save/restore (schema v37). Released only by `clear_user_cap()`, by clicking the ⚡ toggle Locked → Active, or by removing the row. |
| **U16** | **Radio invariant for overflow target (v2.0.7+):** AT MOST ONE non-denom row has `user_capped=False` (= Active, green ⚡) at any time. Enforced at row-add (defaults new rows to Locked when an Active exists) and on explicit ⚡ click (Locked → Active locks all OTHER non-denom rows via `_enforce_single_active_overflow_target`). Auto-Distribute targets the single Active row. |
| **U17** | **Set-max-charge floor (v2.0.7+):** `PaymentRow.set_max_charge(N)` on a non-denom row where `_user_capped=True` MUST NOT clamp the spinbox value below its current charge. Floor = `max(N, current_charge)`. Lowest-layer defence: protects against any caller (`_push_row_limits`, `AdjustmentDialog._update_row_caps`, future code) that might compute a sub-current max for a user-capped row. |

## Layer 5 — Per-vendor reconciliation (Layer 2C)

| ID | Invariant |
|---|---|
| **L1** | For every confirmable order, predicted per-vendor allocation == receipt within ±1¢. |
| **L2** | When this fails, the screen MUST be in an explicit error state (error label visible, confirm disabled) — NEVER allow confirm with per-vendor mismatch. |
| **L3** | After confirm: actual saved per-txn `Σ method == T.receipt_total` (within ±1¢ for penny rounding, exactly 0¢ in steady state). |

## Layer 6 — Vendor reimbursement contract

This is the financial promise to vendors. Penny drift here is
**unacceptable** — it's real money mistakes.

| ID | Invariant |
|---|---|
| **R1** | `_collect_vendor_reimbursement(conn, [md_ids])` per-vendor `Total Due to Vendor == Σ T.receipt_total` for that vendor's confirmed/adjusted transactions. |
| **R2** | `Σ Total Due to Vendor == Σ T.receipt_total` across the market day(s). |
| **R3** | **Denomination-integrity (v2.0.7+):** Per-method column `c[name] == Σ payment_line_items.(customer_charged + customer_forfeit_cents)` for that method on that vendor. For non-denom methods this equals `Σ customer_charged` (forfeit is always 0); for denom methods it equals `tokens × denomination` (the customer's true physical handout). EXCEPTION: the system-managed `Unallocated Funds` method uses `Σ method_amount` instead, since `customer_charged = 0` (FAM absorbs the gap). |
| **R4** | `c['FAM Match'] == Σ payment_line_items.match_amount` across all methods on that vendor. |
| **R5** | **Reconciliation (v2.0.7+):** `Σ per-method-cols + FAM Match - Customer Forfeit + FMNP_External == Total Due to Vendor` (within ±1¢). The Customer Forfeit subtraction is the closure: per-method columns show denomination-true customer payment, forfeit is the over-tendered portion that didn't reach the vendor. |
| **R6** | Voided transactions excluded from R1-R5. |
| **R7** | **Customer Forfeit column (v2.0.7+):** `c['Customer Forfeit'] == Σ payment_line_items.customer_forfeit_cents` for that vendor (Phase B only — Phase A FAM-match reduction is NEVER reported here). |
| **R7** | Adding/voiding a transaction immediately reflects in R1 on next read (no caching). |

## Layer 7 — State preservation (drafts)

| ID | Invariant |
|---|---|
| **S1** | `save_draft` writes the current row state to DB; no engine recomputation between Save and Resume. |
| **S2** | `resume_draft` restores rows to byte-identical spinbox values, methods, and bound vendors. |
| **S3** | `auto_distribute` is idempotent: running it twice in a row produces the same final row state (modulo no other operations between). |
| **S4** | `add_row + delete_row` (same row, no other operations) returns the screen to its prior state. |
| **S5** | An empty row (no method selected) is never saved to DB. |
| **S6** | Resuming a draft, then immediately re-saving it, produces the same DB rows. |

## Layer 8 — Convergence

| ID | Invariant |
|---|---|
| **C1** | After ANY sequence of UI operations, the screen is either: (a) in a confirmable state where every invariant above holds, OR (b) in an explicit error state with the confirm button disabled. There is NEVER a third state where the screen looks confirmable but the math is wrong. |
| **C2** | Auto-distribute always produces a state from set (a) above OR an explicit error state (a). It never produces hidden inconsistency. |
| **C3** | If invariants in set (a) hold and the user clicks Confirm, the save MUST succeed. (Layer 2A/B/C hard-stops are acceptable; silent corruption is not.) |

## Layer 9 — Rewards (informational only, see FINANCIAL_FORMULA.md §11)

| ID | Invariant |
|---|---|
| **W1** | Reward rows are NEVER created retroactively for pre-feature transactions. |
| **W2** | `record_generated_rewards` is the only writer for `generated_rewards`. |
| **W3** | Reward row count for an order doesn't change after the first confirmation (idempotent re-fire is a no-op). |
| **W4** | Disabling the rewards feature does NOT delete or hide existing rows. |
| **W5** | Editing/deleting a `reward_rules` row does NOT modify any `generated_rewards` row. |

---

## How to use this document

* When fixing a bug: identify which invariant was violated, add the
  check to `tests/_coherence.py` if missing, then fix the root cause.
* When adding a feature: every new feature must list which invariants
  it preserves AND which (if any) it adds.
* Before release: full `audit_screen` fuzz run must show ZERO failures.
* If `audit_screen` flags an invariant that isn't in this doc, add
  the doc entry first — every check has a named contract.

**The bar:** every UI surface, DB row, engine output, and report
query must AGREE on every value at every moment. No exceptions.
"Mathematical soundness" means each invariant is independently
verifiable and cross-layer equalities are explicit.
