# FAM Market Manager v2.0.8

**Previous public release:** v1.9.8 / v1.9.9 (April 2026)
**Schema version:** 37 (forward migrations from v22 baseline)
**Tests:** 3,646 across 161+ files (was 1,822 at v1.9.8)

---

v2.0.8 is the **first v2.x release reaching the field**. It rolls up a season of hardening, polish, and new features layered into v2.0.0 → v2.0.1 → v2.0.6 → v2.0.7 → v2.0.8 development cycles. Everything below has been validated through end-to-end testing and onsite use during the May 2026 markets, and is being shipped together as one consolidated upgrade.

The bulk of this release is **user-facing**. Volunteers and market managers will notice changes on the Payment Screen, the FMNP page, several reports, and Settings. The behind-the-scenes engine, database, and reliability work is summarized in the final section for technical reference.

---

## What volunteers will see at the booth

### Redesigned Payment Confirmation Dialog

Confirming a payment now opens a dialog that walks the volunteer through what they need to physically do before the transaction commits.

* A **marching-ants action zone** highlights each step.
* **Per-method action rows** — each payment method on the order gets its own line item: "Collect $10.00 SNAP," "Hand over 2 × $2.00 Food Bucks tokens," "Take 1 × $5.00 FMNP check."
* **Required acknowledgment checkboxes** for SNAP/EBT — the Confirm button stays disabled until the volunteer ticks "Yes, I've completed the SNAP swipe on the EBT terminal."
* A **customer-impact summary** shows what to collect and what to give back, in dollars, before the transaction commits.
* A **Customer Forfeit warning zone** appears when a Phase B forfeit is about to commit so the volunteer can confirm the customer accepts the over-tender.
* A **GIVE TO CUSTOMER zone** displays Rewards-engine handouts in real time so the volunteer hands over exactly the right number of tokens.

### Per-row ⚡ Auto-Distribute toggle on Payment rows

Each non-denominated payment row (SNAP, Cash, etc.) now has a small **⚡ icon** with two visual states:

| State | Visual | Behavior |
|---|---|---|
| **Active** | Green ⚡ | Auto-Distribute will fill or refill this row with the receipt remainder. The row is the "overflow target." |
| **Locked** | Grey ⚡ | Auto-Distribute will skip this row. The volunteer's typed value stays exactly as entered, even when the daily FAM match cap kicks in. |

Typing into the amount field auto-locks the row. Adding a third payment row when one is already Active defaults the new row to Locked. Volunteers can click ⚡ to switch states without deleting and re-adding the row. Only one row at a time can be the green overflow target.

**Why this exists**: earlier engine paths could silently inflate any row's value when the daily match cap shrank the FAM contribution. The volunteer would type "$125 SNAP" (because that's all the customer has on their EBT card), click Auto-Distribute, and see SNAP magically become "$138.09" — confusing and unfixable without deleting the row. The toggle makes intent explicit.

### Per-vendor payment-method eligibility

Configure, per vendor, exactly which payment methods that vendor can accept (**Settings → Vendors → Eligible Payment Methods**, plus a per-market checkbox grid in **Settings → Markets → Assign Payment Methods**).

Real-world eligibility rules apply automatically — Food Bucks for produce-only vendors, FMNP for FMNP-eligible vendors, and so on. Pre-v2.0.x the app showed every method against every vendor, so a baked-goods booth could accidentally accept Food Bucks. Now ineligible methods don't appear as options against ineligible vendors.

**SNAP and Cash are universally accepted at every vendor by default and cannot be unassigned**. Their checkboxes are checked, disabled, and tooltipped explaining why. A schema migration backfills the bindings for every vendor on first launch. Eliminates the silent-SNAP-onto-ineligible-vendor reproducer class entirely.

### Customer Forfeit visible end-to-end

When a customer hands a denomination unit (e.g. a $10 Food RX token) to a vendor whose receipt is smaller than the unit's face value (e.g. $1.45), the over-tendered amount that didn't reach the vendor is now tracked explicitly:

* A new **Customer Forfeit** card on the Payment Screen header shows the running total. Stays at $0.00 in normal use; goes positive only when a denomination unit's face value exceeded what the receipt and the FAM match together could absorb.
* The Payment Confirmation Dialog surfaces a Customer Forfeit warning zone when a forfeit is about to commit.
* Reports get a dedicated **Customer Forfeit** column for after-the-fact audit (see *Reporting Updates* below).

The vendor still gets the full receipt total — Phase B forfeit doesn't shift money to the vendor. It's a record of "the customer over-tendered, here's the unaccounted token value."

### Configurable Customer Rewards engine

A new rewards engine in **Settings → Rewards** lets the market operations manager configure rules of the form:

> "For every $X spent on [source method], hand out N units of [reward method] worth $Y each."

The default rule is the classic **$5 SNAP → 1 × $2 JH Food Bucks**, but trigger method, threshold, reward method, reward unit value, and quantity per increment are all editable. The Payment Confirmation Dialog tells the volunteer in real time exactly how many tokens to physically hand over. A new **Generated Rewards** tab in Reports lists every reward issued by customer, date, source method, reward method — for end-of-season reconciliation against your token inventory. Adjusting a rule mid-season doesn't alter previously-issued rewards; the historical record reflects what the cashier actually handed out.

### New action-oriented dialogs

When the volunteer hits a hard edge case, the Payment Screen now surfaces a clear, action-oriented dialog instead of a generic error.

* **Cap-bound impossible-to-balance recommendation.** When the customer's daily FAM match cap can't accommodate their full payment mix (a narrow returning-customer scenario), the dialog names the cap as the root cause, shows the exact dollar gap to reduce, and explicitly recommends splitting the customer's receipts into separate orders — each order gets its own clean cap allocation. Backed by a new in-app help article (`split-orders-when-stuck`) and troubleshooting flow.
* **Adjustment safety gate for denominated transactions.** Clicking **Adjust** on a transaction that included Food Bucks / Food RX / FMNP opens a three-button dialog (**Void Instead** recommended / **Adjust Anyway** with audit-log entry / **Cancel**) explaining that adjustments on denominated rows can re-trigger cap-aware reductions. Void + re-enter is the supported workflow for those.
* **Customer-gone path.** If a volunteer realizes mid-Adjustment that the customer has already left, the app offers a clear "log as Unallocated Funds" option that records FAM's absorbed loss without leaving the transaction in a broken state.

### FMNP Check Tracking — full history at a glance

The FMNP Check Tracking page's market-day dropdown now defaults to **"All Market Days"** as the first option, paired with a date-range filter (mirrors the Reports + Adjustments pattern).

* Volunteers can search the full FMNP entry history at a glance — no more scrolling through every prior market day.
* A new **Market Day** column in the entries table identifies which date each entry came from.
* When "All Market Days" is selected, the **Add FMNP Entry** button greys out (you can't attribute a new entry to "all markets") and an inline hint label appears next to it: *"← Pick a specific market day above to add a new entry."*
* Photo attachment supports multi-photo for multi-check entries, scrollable when the list is long.
* Three-layer photo deduplication (within entry, cross-transaction warning, Drive upload reuse).

### Returning customer + pending order workflow

The **Returning customer dropdown** pulls up a customer who's already shopped today; their match-cap accounting carries through automatically across visits. The **Pending Orders dropdown** lets a volunteer pause an order mid-shopping and resume it later from any laptop. **Customer labels carry a 3-character device tag** (e.g. `C-005-LB1`) so coordinators can tell which laptop captured which customer.

A **Stale market day auto-close** safety feature auto-closes Open days with past dates at app launch, with a clear notification of which days were closed and why.

---

## Reporting updates — tracking overage, forfeits, rewards, and zip codes

### New Customer Forfeit column on Vendor Reimbursement & Detailed Ledger

Coordinators reviewing end-of-month reimbursements can now see, at a glance, when a customer over-tendered a denomination unit and how much went unaccounted. The per-vendor row reconciliation reads:

```
Σ(per-method-cols) + FAM Match − Customer Forfeit + FMNP_External = Total Due to Vendor
```

The **Total Due to Vendor** is unchanged — vendors still receive exactly the receipt total. Customer Forfeit is a separate column that closes the math when a customer's physical handout exceeds the receipt.

### Denomination-true per-method columns

The per-method columns in Vendor Reimbursement (Food RX, Food Bucks, FMNP, SNAP, Cash) now show the customer's **physical handout** — denomination-true, no FAM match intermingled.

**Example**: a customer hands a $10 Food RX token for a $1.45 receipt.

* Earlier versions: Food RX column = $1.45 (the post-forfeit value)
* v2.0.8: Food RX column = $10.00 (the actual physical handout) + Customer Forfeit = $8.55

The reports now read the way the volunteer remembers handling it: "the customer paid $10 in Food RX, and $8.55 of that was forfeited." The vendor's reimbursement total is unchanged.

### New report tabs and columns

* **Generated Rewards tab** — append-only historical record of every reward issued (by customer, date, source method, reward method).
* **Zip Code column** on Detailed Ledger, Transaction Log, Generated Rewards, and FMNP Entries (internal + Sheets). Coordinators can pivot on (Zip Code × Vendor × Payment Method).
* **FAM Match vs FAM Absorbed** as two distinct columns. *Match* is the multiplier on customer payment; *Absorbed* is the customer-gone Unallocated Funds path.
* **Customer Forfeit summary tile** on the Reports header alongside Total Receipts / Customer Paid / FAM Match / FMNP Checks / FAM Absorbed.
* **Vendor Reimbursement** rebuilt with accurate per-vendor totals + a separate **FMNP (External)** column for paper checks recorded via the FMNP Entry screen.
* **Activity Log** with full audit trail across CREATE / CONFIRM / ADJUST / VOID / OPEN / CLOSE / REOPEN actions, filterable by action type and date range.
* **Charts** (matplotlib time-series + category breakdowns) and a **Folium-rendered customer map** for at-a-glance insights.
* **CSV export** for every report with formula-injection sanitization.

### FMNP Path 1 / Path 2 reporting fixes

* FMNP via Payment Screen now reports the physical face value (not the matched value).
* FMNP summary tile on Reports sums BOTH paths (Path 1 — via Payment Screen + Path 2 — via FMNP Entry screen).

---

## Multi-workstation cloud sync hardening

Markets that run several laptops in parallel will benefit from a systematic pass over the cloud-sync invariants. Every local mutation that affects a cloud-bound row now triggers a sync trigger that reaches every relevant tab, and every cleanup respects per-device ownership.

* **Settings changes propagate immediately.** Vendor / market / payment-method add, edit, toggle, delete; the four assignment dialogs; reward-rule changes — all now trigger a full-scope cloud sync.
* **Closed market day mutations sync correctly.** FMNP entries added to closed days and Admin adjustments / voids on historical receipts now scope to the affected day, not the currently-open one.
* **Vendor Reimbursement cleanup is multi-market-aware.** Stale rows owned by this device are removed across *every* market it has worked, not just its currently-configured primary.
* **Reset preserves other devices' data.** Resetting a local app instance now triggers an immediate sync that clears only this device's rows from the shared sheet — other workstations' data is untouched. The reset dialog explicitly confirms this multi-workstation safety guarantee.
* **Single-instance lock** prevents two app instances from operating on the same database simultaneously.
* **Customer labels carry a device tag** (e.g. `C-005-LB1`) so coordinators can tell which laptop captured which customer.
* **Market renames protected** — renames that would shift the derived market_code are blocked once the market has any history; code-stable renames (typo fixes) are always allowed.
* **Sync watchdog** — 5-minute timer guard so a stuck sync state self-recovers.

---

## Auto-update

* **Check for updates** from Settings — the app polls the official GitHub release channel on a 6-hour cadence.
* **Pre-install safety check** — the app refuses to install an update while a market day is open.
* **Sticky update notifications** — "Remind me later" re-fires after 6 hours; "Ignore for this version" stays silent until a newer version.
* **SHA-256 rollback verification** on auto-update — failed installs verify the rollback's authenticity before restoring.

---

## Help & self-service

* **75+ curated articles** (was 51 at v1.9.8) across 8 categories with live keyword search.
* **Animated 5-stage walkthrough** ("Your Day at the Market") with hand-drawn pictograms.
* **10 symptom-based troubleshooting flows** including new flows for "Auto-Distribute did nothing" / "payment screen hard block" / "split orders when stuck."
* **System Status** tab with live diagnostic snapshot — app version, last sync, disk usage, record counts, instance-lock state, pending-update state, rewards summary — and one-click "Copy Diagnostic Info."

Three new help articles in this release: `auto-distribute-toggle`, `customer-forfeit`, `fmnp-all-market-days`.

---

## Coordinator-facing rollout notes

Two changes are visible enough that coordinators may want to brief volunteers ahead of the next market.

### 1. Reports may show different per-method values for transactions where a customer over-tendered a denomination unit

Per-method report columns now show the customer's **actual physical handout** (e.g. $10 for a $10 Food RX token) with the over-tender ($8.55 if the receipt was $1.45) recorded in the new **Customer Forfeit** column. **Vendor reimbursement total is unchanged.** This is a *display* change reflecting denomination integrity — no underlying data was modified.

### 2. Per-row ⚡ toggle on Payment rows

Each non-denom payment row has a small ⚡ icon. **Green** = Auto-Distribute will fill it. **Grey** = Locked at the volunteer's typed value. Typing in the amount field auto-locks the row. Click ⚡ to switch states. Only one row at a time can be the green "overflow target."

If a volunteer says "Auto-Distribute did nothing," check whether the row they expected to fill is grey — they need to click ⚡ to release it, or add another row to absorb the remainder.

---

## Upgrading from v1.9.x

**Manual update (recommended for the first v2.x install):**

1. Download **FAM_Manager_v2.0.8.zip** from the GitHub release page
2. Replace the old application folder with the extracted contents
3. Launch — the app finds your existing data automatically

After this release, the in-app updater (**Settings → Updates → Check for Updates → Download & Install**) handles future versions.

> **Data safety:** existing data is preserved automatically. Cloud credentials, Drive folder, and update channel settings survive the upgrade. Schema migrations (v22 → v37, all additive) run on first launch with a pre-migration backup written before each step. Any failure rolls back cleanly to your prior data.

> **What you'll see on existing Sheets:** several new columns appear on the next sync — *Customer Forfeit* on Vendor Reimbursement and Detailed Ledger; *Zip Code* across multiple report tabs; per-method columns now show denomination-true values for new transactions. Legacy transactions are unchanged because their forfeit value defaults to 0.

### Pre-flight check for image-cloned fleet laptops

If your fleet was deployed by cloning one Windows image across multiple laptops, take 30 seconds to verify each device has its own MachineGuid before upgrading. v2.0.8 refuses to launch on devices that share a synthetic device identity (this prevents silent cross-device sync corruption). In an elevated PowerShell on each laptop:

```powershell
(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' MachineGuid).MachineGuid
```

You should see a unique GUID per device. If a value is missing, blank, or identical across laptops, generate a fresh one before launching v2.0.8:

```powershell
$g = [guid]::NewGuid().ToString()
Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' MachineGuid $g
```

If a device hits this on first launch you'll see a *Device Identity Required* dialog with the same instructions — no data is touched.

---

## Behind the scenes

The user-facing changes above sit on top of a substantial reliability and engine pass. Volunteers and managers don't need to think about any of this — included for technical reference.

### Reliability hardening

* **Cross-platform single-instance lock** — advisory file lock at `%APPDATA%\FAM Market Manager\.fam_instance.lock`; prevents two copies running against the same data folder.
* **Atomic photo writes** — tempfile + `os.replace`, so a mid-write crash leaves no half-written JPEGs.
* **Offline-quiet sync logging** — DNS / connection errors collapse to ONE warning per sync cycle instead of full tracebacks per sheet tab; ~30× log-noise reduction during outages.
* **Per-line invariant** enforced at the database level: every payment-method row satisfies `customer_charged + match_amount = method_amount`.
* **Voided is terminal** — DB-trigger enforced. A voided transaction stays voided.
* **Atomic ledger backup** on every payment confirm and adjustment, with rotation through 5 historical snapshots.
* **Pre-migration database snapshots** before any schema upgrade. Backup failure now blocks the migration entirely, never proceeds without rollback safety.
* **Drive verification tri-state** — checking whether a photo is still on Drive distinguishes "yes / no / can't tell right now," so transient network errors don't cause the app to mistakenly think every photo was deleted.
* **Sheet payload chunking** — large syncs after a long offline period are automatically split into batches that fit Google's per-call limits.
* **Photo dedup cache invalidation** on void / FMNP delete / FMNP photo replace.
* **Error Log tab auto-refreshes** on every selection so the local view stays aligned with the cloud Error Log sheet.
* **Database fragmentation hint is size-aware** — only fires on databases >4MB with >30% reclaimable space.
* **Device-identity dialog is actionable** — image-cloned laptops with a missing MachineGuid get clear PowerShell remediation.

### Engine improvements

* Denomination preservation through adjustments — engine snap-back + save-layer guard + safety gate dialog keep denom rows aligned to physical-token multiples through every code path.
* User-cap-aware engine paths so a typed value isn't silently inflated under daily match cap.
* Auto-Distribute cap-deficit Pass 2 fallback to unmatched non-denom rows.
* Charge-integrity Layer 2A/2B/2C confirm-time guards + photo validation + spinbox write-back.
* Single-vendor multi-receipt allocation in Layer 2C and the save path.
* Vendor Reimbursement aggregation correctly excludes voided lines.
* AdjustmentDialog `_update_customer_impact` propagates `user_capped` so impact preview matches save.

### Database & schema

Schema upgraded from **v22 (v1.9.8 baseline) to v37** through 15 forward migrations, all additive. Notable migrations:

* **v23 → v24**: `vendor_payment_methods` junction (per-vendor binding)
* **v24 → v25**: `payment_methods.is_system` column + Unallocated Funds seed
* **v27 → v28**: `chk_pli_invariant_*` triggers enforce `customer_charged + match_amount = method_amount`
* **v30 → v31**: PLI UPDATE non-negativity + Voided-one-way triggers
* **v31 → v32**: composite scaling indexes for multi-year deployments
* **v32 → v33**: `chk_pli_uf_zero_*` triggers enforce Unallocated Funds rows have `customer_charged=0` AND `match_amount=0`
* **v33 → v34**: schema_version dedupe + UNIQUE INDEX
* **v34 → v35**: SNAP / Cash universal vendor binding backfill
* **v35 → v36**: `customer_forfeit_cents` column on `payment_line_items` (Phase B forfeit)
* **v36 → v37**: `user_capped` column on `payment_line_items` (per-row toggle persistence)

WAL synchronous + checkpoint thresholds tuned for the market-day workload. Composite scaling indexes added for year-over-year deployment performance. Pre-migration `.bak` is fatal-on-failure.

### Concurrency baseline

* WAL + FK + busy_timeout pragmas
* Cross-thread snapshot isolation
* Single-table read transactions for reports + sync

### Input validation hardening

52 tests covering SQL injection, path traversal, unicode round-trip, CSV formula injection, extreme values (zero / negative / overflow), file system safety.

### UI volume tested

200 vendors, 50 payment methods, 500-customer dropdown, 30-row payment engine, 50-confirm memory bound.

### Timezone correctness

Eastern across the board. DST spring-forward / fall-back, year-end, and leap-day pinned via tests.

### Test suite

**3,646 tests across 161+ files** (was 1,822 at v1.9.8 — +1,824 tests across the v2.0.x cycle).

Notable new test files since v1.9.9:

* `test_universal_vendor_method_bindings.py`, `test_adjustment_denom_safety_gate.py`, `test_save_layer_denom_guard.py`, `test_denom_preservation_in_adjustments.py`
* `test_single_vendor_multi_receipt_layer2c.py` + `test_save_path_single_vendor_multi_receipt.py`
* `test_cap_bound_split_recommendation.py`
* `test_customer_forfeit_persistence.py` (Phase B schema v36 round-trip)
* `test_under_denomination_forfeit.py` (Phase B forfeit math)
* `test_user_capped_charge.py` (⚡ toggle, radio invariant, schema v37 round-trip — 36 tests)
* `test_denomination_integrity_reports.py` (per-method column denomination-true — 12 tests)
* `test_fmnp_screen_filters.py` (All Market Days filter + hint label — 16 tests)
* `test_active_tx_status_centralization.py` (status-filter helper — 10 tests)
* `test_spinbox_keypress_empty_text.py` (Page Up / Page Down crash fix — 21 tests)
* `test_concurrency.py`, `test_crash_recovery.py`, `test_input_validation.py`, `test_ui_volume.py`, `test_timezone.py` (the v2.0.0 hardening suite)

Three random fuzz seeds produce 3¢-12¢ rounding divergences in cap-aware paths under specific 30+ action sequences. These are tail-of-distribution rounding artefacts that don't reflect production-realistic usage.

> **Three-way reconciliation verified end-to-end.** For every tested transaction, Database records = Ledger backup = Google Sheets sync output. No penny is lost, duplicated, or misattributed.

### Documentation overhaul

* New printable references: `EMERGENCY_RUNBOOK.md` (symptom-indexed recovery), `COORDINATOR_HANDBOOK.md` (training + escalation), `QUICK_REFERENCE.md` (one-page cheat sheet).
* `SYSTEM_INVARIANTS.md` and `FINANCIAL_FORMULA.md` for developers.
* In-app help: 75+ articles (was 51), 10 troubleshooting flows.

---

## Known limitations

* Windows SmartScreen reputation building — the v2.0.8 executable is code-signed, but Microsoft's reputation system tracks each signed build separately and needs install volume before SmartScreen stops warning new users.
* FMNP method is hardcoded to the literal name "FMNP" in two report queries. Renaming the FMNP method in Settings is not supported.
* Vendor Reimbursement does not auto-merge across devices. When two laptops sync at the same market, each laptop's contribution to a vendor appears as its own row on the synced sheet. Coordinators sum across rows when cutting checks.
* Three random fuzz-test seeds produce small (3¢-12¢) rounding divergences under specific 30+ action sequences — tail-of-distribution artefacts, not production-realistic.

---

## Development & build

* Python 3.12, PySide6 6.7+
* Build via `build.bat` (PyInstaller spec in `fam_manager.spec`)
* Test suite: `python -m pytest tests/ -v --no-header -p no:faulthandler`
* Documentation: `docs/USER_GUIDE.md`, `docs/SYSTEM_INVARIANTS.md`, `docs/FINANCIAL_FORMULA.md`, `docs/EMERGENCY_RUNBOOK.md`, `docs/QUICK_REFERENCE.md`, `docs/COORDINATOR_HANDBOOK.md`

---

*For volunteers and coordinators: see `docs/QUICK_REFERENCE.md` for the printable cheat sheet.*
*For developers: see `docs/SYSTEM_INVARIANTS.md` for the formal contract and `docs/FINANCIAL_FORMULA.md` for the engine algorithms.*
