# FAM Market Manager — Financial Formula Reference (v1.9.9)

> Authoritative reference for every monetary calculation in the
> application.  All citations are `file:line` against the v1.9.9
> codebase.  All values are **integer cents** unless explicitly
> noted otherwise.

---

## 1. Money primitives

`fam/utils/money.py`

| Function | Formula | Purpose |
|----------|---------|---------|
| `dollars_to_cents(d)` (L9–17) | `round(d * 100)` | Convert UI float to engine integer |
| `cents_to_dollars(c)` (L20–26) | `c / 100.0` | Convert engine integer to display float |
| `format_dollars(c)` (L29–39) | `"${0:,.2f}"` | Render for UI / CSV / receipt |

**Engine invariant:** every monetary value stored in the database
is `INTEGER cents`.  Floats appear only at UI/export boundaries.

---

## 2. Per-row math

`fam/utils/calculations.py`

### 2a. Customer charge → method allocation

```
method_amount = round( charge × (1 + match_percent / 100) )
```

`charge_to_method_amount` (L10–16).  `method_amount` is the
**total dollars allocated** to a payment method (customer pays
`charge`, FAM pays `method_amount − charge`).

### 2b. Method allocation → customer charge

```
charge = round( method_amount / (1 + match_percent / 100) )
```

`method_amount_to_charge` (L19–26).  Inverse of 2a; used when
restoring drafts and computing caps.

### 2c. Per-line invariant

```
customer_charged + match_amount = method_amount
```

Enforced in `calculate_payment_breakdown` (L235):

```python
match_amount = round(method_amount × (match_percent / (100 + match_percent)))
customer_charged = method_amount − match_amount
```

This formula guarantees the invariant holds *exactly*, with no
floating-point drift.

---

## 3. Multi-row breakdown (`calculate_payment_breakdown`)

`fam/utils/calculations.py:177–331`

```
calculate_payment_breakdown(
    receipt_total: int,            # cents
    payment_entries: list[dict],   # each {method_amount, match_percent}
    match_limit: int | None,       # daily cap in cents (None = no cap)
) -> dict
```

### 3a. Allocation invariant

```
sum(line.method_amount for line in line_items) == receipt_total ± 1¢
```

Enforced via *penny reconciliation* (see 3c).

### 3b. Daily match cap (proportional)

When `match_limit` is set and uncapped match would exceed it:

```
cap_ratio          = match_limit / uncapped_match_total
line.match_amount  = round(line.uncapped_match × cap_ratio)
line.customer_paid = line.method_amount − line.match_amount
```

Applied to **every row proportionally**; the row with the largest
match absorbs any sub-cent residue (L257–270) so the capped total
equals `match_limit` exactly.

The breakdown returns `match_was_capped=True` and
`uncapped_fam_subsidy_total=<original>` so callers can show the
volunteer that the cap was hit.

### 3c. Penny reconciliation

After all per-row math, if `|allocated_total − receipt_total| ≤ 1`
(the only acceptable drift after `round()`), the residue is
absorbed into the **largest-match line**:

```python
target = max(matched_lines, key=lambda l: l.method_amount)
target.method_amount += residue   # +1 or -1
target.match_amount  += residue
# customer_charged stays unchanged → FAM absorbs the rounding penny
```

(L278–300, with a guard at L294 that pushes the residue into
`customer_charged` if doing so would push `match_amount` below 0.)

**Net effect:** every receipt ties out to the cent.  The customer
never pays an extra penny because of rounding; FAM's subsidy
absorbs it.

### 3d. Returned dict (L321–331)

| Key | Meaning |
|-----|---------|
| `line_items` | Per-row dicts: `method_amount`, `match_amount`, `customer_charged` |
| `customer_total_paid` | `Σ customer_charged` (cents) |
| `fam_subsidy_total` | `Σ match_amount` (cents) |
| `allocated_total` | `Σ method_amount` (cents) |
| `allocation_remaining` | `receipt_total − allocated_total` |
| `is_valid` | `True` iff no errors AND \|remaining\| ≤ 1¢ |
| `errors` | Validation messages (negative amounts, etc.) |
| `match_was_capped` | True if `match_limit` was applied |
| `uncapped_fam_subsidy_total` | Original match before cap |

---

## 4. Auto-distribute (`smart_auto_distribute`)

`fam/utils/calculations.py:29–174`

Three-pass algorithm:

1. **Seed** each denominated row with 1 unit (if affordable).
2. **Fill remaining**: denominated rows get whole-unit additions;
   the *best absorber* (highest match%, ties by sort_order) takes
   the floor of the remainder so the customer never pays a
   rounding penny.
3. **Forfeit** (denominated only): if all auto rows are
   denominated and remainder > 1¢ but < denomination, allow +1
   unit on the best-fit denom row.  FAM match flexes down to
   absorb the overage.

Returns: `[{index, charge}, …]` — only rows whose charge changed.
Locked rows (user-entered > 0) are never modified.

---

## 5. Per-vendor charge limits (`PaymentScreen._push_row_limits`)

`fam/ui/payment_screen.py:1533–1761`

For each row, the maximum charge the input widget will accept is
the tighter of:

```
per_vendor_remaining = vendor_receipt − Σ(other denom rows on same vendor)
order_remaining      = effective_order_total − Σ(other rows)
max_charge = min(per_vendor_remaining, order_remaining)
```

`effective_order_total` accounts for denomination over-allocation
(see L1583–1620): when a denom row over-fills its bound vendor,
non-denom rows must over-fill *other* vendors to keep the order
total intact.  No-overage cases collapse to `self._order_total`.

When the daily match cap is active and active row is non-denom,
`max_charge` is inflated so the customer can pay enough to cover
the un-matched portion (L1730–1741).

---

## 6. Vendor reimbursement

`fam/sync/data_collector.py:_collect_vendor_reimbursement` (L132–255)
and the mirror in `fam/ui/reports_screen.py`.

### 6.1  Total Due to Vendor (the contract)

Per `(market, vendor)` across all included market days:

```sql
SELECT SUM(t.receipt_total) AS reimburse_amount
FROM transactions t
WHERE t.market_day_id IN (?)
  AND t.vendor_id = ?
  AND t.status IN ('Confirmed', 'Adjusted')
```

**Voided transactions are excluded.**  External FMNP entries from
`fmnp_entries` (where `status='Active'`) are added to
`Total Due to Vendor` (in addition to the in-app receipt total)
because FAM is also paying the vendor for those off-book checks.

### 6.2  Per-method columns (v1.9.10+ semantics)

Each distinct `payment_method_name_snapshot` becomes its own
column in the report.  The cell shows the **physical-instrument
total** the customer handed to the vendor — `SUM(customer_charged)`
— **NOT** `method_amount` (which would conflate the customer's
contribution with FAM's match).

```sql
SELECT pl.method_name_snapshot AS method,
       SUM(pl.customer_charged) AS customer_total,
       SUM(pl.match_amount)     AS match_total
FROM payment_line_items pl
JOIN transactions t ON pl.transaction_id = t.id
WHERE t.market_day_id IN (?)
  AND t.status IN ('Confirmed', 'Adjusted')
GROUP BY t.vendor_id, pl.method_name_snapshot
```

For the canonical $3-receipt / $2-FB-token / $1-match scenario:

| Column                 | Value | Why                              |
|------------------------|------:|----------------------------------|
| Total Due to Vendor    | $3.00 | = receipt_total                   |
| `JH Food Bucks`        | $2.00 | = customer_charged (physical $2)  |
| `FAM Match`            | $1.00 | = post-forfeit match contribution |
| **Identity check**     | $3.00 | $2 + $1 = $3 ✓                    |

### 6.3  FAM Match column

A dedicated column aggregates `SUM(match_amount)` across **all**
methods for the vendor (post-forfeit, post-cap).  This lets a
market manager:

1. See "how many $2 Food Bucks does this vendor need to redeem?"
   — directly from the `JH Food Bucks` column (= count × face).
2. See "how much does FAM owe this vendor for match?" — directly
   from the `FAM Match` column.

Pre-v1.9.10 the per-method columns showed `method_amount` (=
customer + match), which inflated the count of physical
instruments and hid the FAM Match contribution inside each
method-specific column.

### 6.4  Per-vendor identity

```
Σ(per-method columns) + FAM Match + FMNP (External) = Total Due to Vendor
                       (within penny-rec tolerance)
```

This identity is verified by
`tests/test_vendor_reimbursement_denom_overage.py` and by the
multi-method tests in
`tests/test_sync.py::TestEnhancedVendorReimbursement`.

### 6.5  Column order

```
Market Name | Vendor | Month | Date(s) | Total Due to Vendor |
FAM Match | <method-1> | <method-2> | … | FMNP (External) |
Check Payable To | Address
```

`FAM Match` sits between `Total Due to Vendor` and the
per-method columns so the reader can see the vendor-level total
+ FAM responsibility before scanning method-by-method.

---

## 7. Returning customer prior match

`fam/models/customer_order.py:get_customer_prior_match` (L216–238)

```sql
SELECT COALESCE(SUM(pli.match_amount), 0)
FROM customer_orders co
JOIN transactions t ON t.customer_order_id = co.id
JOIN payment_line_items pli ON pli.transaction_id = t.id
WHERE co.market_day_id = ?
  AND co.customer_label = ?
  AND co.status IN ('Confirmed', 'Adjusted')
  AND t.status IN ('Confirmed', 'Adjusted')
  [AND co.id != ?  -- exclude_order_id for current edit]
```

**Both** the order-level and txn-level statuses must be
non-voided.  This is what lets a void of an earlier visit free up
match cap for a later visit.

---

## 8. Schema-level invariants

`fam/database/schema.py`

| Trigger | What it blocks | Lines |
|---------|----------------|-------|
| `chk_transaction_amount_*` | `receipt_total ≤ 0` | 239–249 |
| `chk_payment_amount_insert` | `method_amount < 0` or `match_amount < 0` | 252–259 |
| `chk_fmnp_amount_*` | `fmnp_entries.amount ≤ 0` | 262–274 |
| `chk_payment_method_match_*` | `match_percent < 0` or `> 999` | 277–289 |

**Gap (acceptable):** there is no DB-level trigger enforcing
`customer_charged + match_amount = method_amount`.  This invariant
is maintained by the application engine
(`calculate_payment_breakdown`).  `tests/test_export_reconciliation
.py::TestPerLineInvariant` and the per-transaction reconciliation
in both simulations re-prove it after every save.

---

## 9. Status & lifecycle

```
            create_transaction (CREATE)
                       │
                       ▼
                    Draft
                       │
      save_payment_line_items (PAYMENT_SAVED)
                       │
                       ▼
              confirm_transaction (CONFIRM)
                       │
                       ▼
                  Confirmed ───── update_transaction ─────► Adjusted
                       │                                       │
                       └────── void_transaction (VOID) ────────┤
                                                               ▼
                                                            Voided
```

Voided transactions:

* Keep their `payment_line_items` rows (audit trail).
* Are **excluded** from financial reports (Vendor Reimbursement,
  FAM Match, Detailed Ledger UI, Market Day Summary, Geolocation,
  cloud sync versions of those tabs).
* Are **included** in audit surfaces (Activity Log, Transaction
  Log, Detailed Ledger sync export, ledger backup file — marked
  with `Status='Voided'` and excluded from totals).

---

## 10. Reconciliation contract (the "zero tolerance" line)

For every confirmed/adjusted transaction `T`:

```
T.receipt_total = Σ T.payment_line_items.method_amount
                = Σ T.payment_line_items.customer_charged
                + Σ T.payment_line_items.match_amount
```

For every market day `D`:

```
Σ T.receipt_total                 (DB)
  = Σ Vendor Reimbursement row    (UI report + CSV export)
  = Σ FAM Match "Total Allocated" (UI report + CSV export)
  = Σ Detailed Ledger non-voided  (UI report + CSV export)
```

These equalities are tested in:

* `tests/test_production_stress.py::TestEndToEndReconciliation`
* `tests/test_export_reconciliation.py::TestVendorReimbursementExport`
* `scripts/v1_9_9_stress_sim.py` Phase 7
* `scripts/production_sim.py` Phase 3

A failure in any of those tests is a **financial-integrity
regression**, not a cosmetic bug.

---

## 11. Rewards (informational add-on, **NOT financial**)

`fam/utils/rewards.py`, `fam/models/reward_rule.py`, schema v29
table `reward_rules`.

The rewards program is a customer-facing marketing/loyalty feature
introduced in v1.9.10 (2026-04-30).  It exists **entirely outside
the financial pipeline** documented above.  The carve-out here is
deliberate: future readers must not confuse rewards data with any
financial reconciliation.

### What rewards do

For every configured `(source_method × threshold × reward_method ×
reward_unit)` rule, the FAM rep hands the customer physical scrip
tokens at confirmation time when the order's `customer_charged` for
the source method crosses each whole-increment threshold.

Default rule (seeded on fresh install):

```
For every $5.00 of SNAP customer_charged in a confirmed order
  → hand one $2.00 JH Food Bucks token to the customer.
```

Whole-increment math (NOT pro-rated):

```
n_units      = floor(source_total_cents / threshold_cents)
reward_cents = n_units × reward_unit_cents
```

* $4.99 SNAP → 0 units → $0 reward
* $5.00 SNAP → 1 unit  → $2 reward
* $7.00 SNAP → 1 unit  → $2 reward (still 1 full $5 increment)
* $10.00 SNAP → 2 units → $4 reward

### What rewards explicitly do **NOT** affect

* `payment_line_items` — no new columns; per-line invariant
  unchanged.
* `transactions` — no new columns.
* Vendor reimbursement — vendors don't see/redeem these tokens.
* FAM match — the FAM-side cap math is independent.
* Daily match limit — rewards don't consume cap.
* Receipt-total reconciliation — `Σ method_amount = receipt_total`
  remains the financial contract; rewards are not part of it.

### Storage policy (v30 update — write-once snapshot)

**Reward amounts are stored as a write-once snapshot history** in
the `generated_rewards` table (added in schema v30).  Rows are
written atomically with payment confirmation and **never modified
after**.  Specifically:

* The rule's threshold, reward unit, and source/reward method
  *names* are snapshotted into the row at write time — later
  edits or deletions of the rule do not retro-apply.
* Voiding or adjusting a transaction does NOT modify reward rows.
  The cashier already handed the tokens; the snapshot is the
  receipt-of-record.
* Disabling the rewards feature does NOT remove rows from the
  Generated Rewards report — the historical record persists.
* Pre-feature transactions (confirmations that happened before the
  rewards feature was on) have **no rows** in this table.  Adding
  a rule later does not retroactively populate them.

Earlier (in v29) rewards were derived on demand from a JOIN of
`payment_line_items` and `transactions`.  This was changed because
adding a rule retroactively surfaced rewards for prior transactions
the cashier had not actually given the customer.  The v30 design
puts the rewards on the same write-once footing as the receipt
itself.

The pure-function math (`compute_rewards_for_order` in
`fam/utils/rewards.py`) is still used at confirmation time to
compute *what* to write — but the WRITE is the source of truth
from that moment on.  The Generated Rewards report and cloud sync
read the stored rows; they do not recompute.

There are no foreign-key cascades from `generated_rewards` to
`reward_rules` or `payment_methods` — the snapshot columns are
text + integers, so deleting a rule or renaming a payment method
leaves the historical record intact.

### Where rewards surface

| Surface | Trigger | Source (v30) |
|---|---|---|
| Payment-confirmation dialog (rewards zone) | feature flag on + rule fires | computed pre-commit from `items` + engine `line_items` (pure function — no DB row exists yet) |
| Persistent write at confirmation | feature flag on + rule fires + Confirm clicked | `record_generated_rewards` inside the same transaction as the payment commit |
| Printed customer receipt (rewards section) | reward rows exist for the order | reads `generated_rewards` for the order via `get_generated_rewards_for_order` |
| Reports screen → Generated Rewards tab | always; never wiped by feature toggle | `_collect_generated_rewards` reads stored rows |
| Cloud sync → "Generated Rewards" sheet | Required tab, on by default | `_collect_generated_rewards` reads stored rows |

### Reward-method validation

`reward_method` MUST be a payment method with `denomination > 0`
(physical scrip the FAM rep can hand out — Food Bucks, Food RX, JH
Tokens, etc.).  Non-denominated methods (SNAP, Cash, FMNP) are
forbidden as reward targets.  Enforced at the model layer in
`create_reward_rule` / `update_reward_rule`; the schema also
enforces `source_method_id != reward_method_id` via CHECK.

### Feature toggle

`app_settings.is_rewards_enabled()` (default `True` on fresh
installs).  When `False`, **future** confirmations skip
generation:

* Confirmation dialog rewards zone is suppressed (no lines
  computed for the in-progress payment).
* No new rows are written to `generated_rewards`.

The toggle does **NOT** remove or hide history:

* Existing rows in `generated_rewards` continue to appear in the
  Reports tab and the cloud-synced sheet.
* Receipts re-printed for past orders continue to show their
  reward sections.
* Rule config in `reward_rules` is preserved.

Re-enabling the feature simply resumes generation for the next
confirmation; no backfill happens for the orders that were
confirmed while the feature was off.
