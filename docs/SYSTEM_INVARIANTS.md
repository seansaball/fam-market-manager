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

## Layer 2 — Forfeit pass (post `_apply_denomination_forfeit`)

| ID | Invariant |
|---|---|
| **F1** | After forfeit, `Σ_li method_amount == receipt_total`. |
| **F2** | After forfeit, every per-vendor allocation `≤ vendor_receipt + 1¢`. |
| **F3** | `customer_forfeit_cents >= 0` for every line item. |
| **F4** | `customer_forfeit_cents > 0` only on denominated rows. |
| **F5** | When forfeit was applied: `(customer_charged + customer_forfeit_cents)` equals the row's pre-forfeit customer (= unit_count × denomination). |
| **F6** | Per-line invariant `customer_charged + match_amount == method_amount` survives forfeit (Phase A reduces match+method together; Phase B reduces customer+method together). |

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
| **U5** | `summary_card['fam_match'] == result.fam_subsidy_total`. |
| **U6** | `summary_card['customer_pays'] == result.customer_total_paid`. |
| **U7** | `summary_card['allocated'] == result.allocated_total` (pre-forfeit when overage active; post-forfeit when not). |
| **U8** | For each row in `vendor_breakdown_table`: `Remaining = receipt - allocated_for_this_vendor`. |
| **U9** | `Σ vendor_breakdown.allocated == result.allocated_total` (within ±1¢). |
| **U10** | The "Collect from Customer" panel rows sum to `result.customer_total_paid`. |
| **U11** | The denomination-overage warning is visible iff `denom_overage > 0`. |
| **U12** | The Confirm button is disabled iff there's a hard error or required checkbox unchecked. |

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
| **R3** | Per-method column `c[name] == Σ payment_line_items.customer_charged` for that method on that vendor. |
| **R4** | `c['FAM Match'] == Σ payment_line_items.match_amount` across all methods on that vendor. |
| **R5** | `Σ per-method-cols + FAM Match + FMNP_External == Total Due to Vendor` (within ±1¢). |
| **R6** | Voided transactions excluded from R1-R5. |
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
