# FAM Market Manager v2.0.9

**Previous public release:** v2.0.8 (May 2026)
**Schema version:** 37 (unchanged from v2.0.8 — no migrations required)

---

v2.0.9 is a small, focused follow-up to v2.0.8. It changes the **Vendor Reimbursement** Google Sheet so each calendar month becomes its own row instead of one ever-growing all-time-cumulative row per vendor. Coordinators can now reconcile vendor reimbursements **month-over-month** at a glance.

There are no schema changes, no migration steps, and no behavior changes for volunteers at the booth. The change is entirely on the cloud-sync side.

---

## What's new

### Vendor Reimbursement: one row per (market × vendor × month)

**Before (v2.0.8):**

```
Vendor X | April     | $2,810.00
```

…where the "Month" column always showed "April" once the year started and the totals kept growing as the months rolled over.

**After (v2.0.9):**

```
Vendor X | April 2026 | $1,250.00
Vendor X | May 2026   | $1,480.00
Vendor X | June 2026  | $    80.00
```

Each month is its own row. The **Month** column now reads `"April 2026"` (human-readable), and a new **Year-Month** column carries the sortable `"2026-04"` form used as part of the row identity for upsert.

The math identity still holds within every monthly row:

```
Σ(per-method-cols) + FAM Match − Customer Forfeit + FMNP (External) = Total Due to Vendor
```

### What this affects

- **Vendor Reimbursement sheet only.** Detailed Ledger, Transaction Log, FAM Match, FMNP Entries, Activity Log, Generated Rewards, and Market Day Summary already filter by date on the sheet side and are unchanged.
- **On-screen Reports tab is unchanged** — this is a sync-layer-only change. Same data, same totals, same UI.
- **Closed-day mutations** (admin adjustments / voids on prior months) correctly update the affected month's row, not the current month's. The Vendor Reimbursement collector still runs against the whole dataset on every sync, so every month is re-emitted and a corrected April surfaces on the April row.

---

## ⚠ One-time cleanup after upgrade

The first v2.0.9 sync against an existing shared sheet will leave the **old all-time-cumulative rows in place as orphans**. The new code emits per-month rows under a different composite key (one that includes `Year-Month`), so the upsert path sees no match for the new keys and appends them — it doesn't know that the old cumulative row is the same vendor.

This is by design — automatic deletion of pre-v2.0.9 rows would be irreversible if the upgrade revealed an issue. Coordinators should perform a one-time manual cleanup once they're satisfied with the new monthly layout:

1. Open the shared Google Sheet → **Vendor Reimbursement** tab.
2. Filter the **Year-Month** column for blank cells.
3. Verify those rows are the old cumulative ones (they will have a "Month" value of just "April" / "May" / etc. with no year — the new rows always include the year).
4. Delete those rows.

The new per-month rows will continue to be maintained automatically on every sync.

---

## Technical notes (for the operator / maintainer)

- **Collector change**: `fam/sync/data_collector.py::_collect_vendor_reimbursement` now adds `strftime('%Y-%m', md.date) AS year_month` to its three queries (vendor rows, method rows, external FMNP rows) and groups by it. In-memory accumulators are keyed by `(market_name, vendor, year_month)`.
- **Upsert key change**: `SyncManager.SHEET_KEYS['Vendor Reimbursement']` now includes `Year-Month`. Without that, May would silently overwrite April on every sync.
- **Tests**: 8 new test classes in `tests/test_vendor_reimbursement_monthly.py` covering two-month split, math identity per month, prior-month void, cross-device same month, FMNP-only multi-month, txn + FMNP same-month merge, void decreases month row, and upsert keying.
- **No schema migration.** SQLite already stores `md.date` in ISO format, so `strftime('%Y-%m', md.date)` works against existing data without changes.
- **FMNP-only vendors** with entries in multiple months now produce one row per month (matched the transaction-row behavior). FMNP-only vendors with entries in a single month still produce one row.
- **Vendors with both transactions and FMNP entries in the same month** continue to merge into one row — the year-month grouping is the unit, not the source.

---

## Where this came from

The need surfaced during v2.0.8 release prep. A coordinator asked: *"When the month changes, will the app make a new row for the vendor to separate out the new months?"* Reading the code, the answer was no — the original cumulative-row design favored single-line readability over month-over-month reconciliation. After a season of use, the trade-off is wrong. Monthly rows make end-of-month reconciliation against bank deposits, vendor payment runs, and FMNP redemption batches dramatically faster.

---

## Upgrade

Standard installer. No schema migration, no laptop-specific actions, no data loss.

Run a manual full sync from any laptop after upgrading so the shared sheet picks up the new per-month rows immediately, then perform the one-time orphan-row cleanup above.
