# Multi-Year Scalability Assessment & Operational Runbook

> **Audience**: technical operator running FAM Market Manager
> for multiple years on a single instance.  This document
> describes growth profiles, the architectural mitigations in
> place, and the manual operations required at year-3+ to keep
> the app fast.

## TL;DR

The app is engineered to run in O(active-market-day) for the hot
path, regardless of how much history is stored locally.  Three
maintenance operations are recommended past Year-3:

1. **VACUUM the SQLite DB** during quarterly downtime (5 min,
   reclaims free pages, halves the DB file size after lots of
   adjustments/voids).
2. **Run the local photo cleanup pass** (auto-runs on market
   close past 90-day retention; configurable via
   `photo_local_retention_days` setting).
3. **Archive the prior year's Google Sheet** to a year-stamped
   copy (manual once a year, takes ~5 min) — see "Sheet
   partitioning" below.

If those run on schedule, the app stays at Year-1 performance
indefinitely.

---

## Growth profile

| Surface | Year-1 | Year-3 | Year-5 | Auto-managed? |
|---|---|---|---|---|
| `transactions` rows | ~3,000 | ~9,000 | ~15,000 | yes (indexed) |
| `payment_line_items` rows | ~6,000 | ~18,000 | ~30,000 | yes (indexed, composite) |
| `audit_log` rows | ~30,000 | ~100,000 | ~200,000 | yes (composite indexes v32) |
| `generated_rewards` rows | ~3,000 | ~9,000 | ~15,000 | yes (indexed) |
| `customer_orders` rows | ~3,000 | ~9,000 | ~15,000 | yes (indexed) |
| Local DB file size | ~50 MB | ~150 MB | ~250 MB | partial — VACUUM recommended quarterly |
| Local photos directory | ~500 MB | ~5 GB | ~15 GB | yes (post-Drive-upload cleanup, 90-day retention) |
| Local rotated logs | ≤15 MB | ≤15 MB | ≤15 MB | yes (5MB × 3 backupCount) |
| Binary backups (`backups/`) | ~100 MB | ~100 MB | ~100 MB | yes (20 file rotation) |
| Text ledger (`fam_ledger_backup.txt` + .prev1..5) | ~5 MB | ~15 MB | ~25 MB | yes (rotation v1.9.10+) |
| Google Sheets cells per tab | ~150K | ~450K | ~750K | partial — annual sheet archive recommended |

The cell-budget on a single Google Sheet workbook is **10 million
cells total across all tabs**.  At year-5, the FAM Market Manager
data tabs will consume around 5–7 million cells.  Operating past
year-5 without partitioning will eventually fail with a Sheets
API quota error on append.

## Architectural mitigations (already shipped)

### Sync layer — diff-based upsert + scope-aware auto-sync

* **Wire writes are bounded by the diff** (`upsert_rows` reads
  the existing sheet, builds a composite-key index, writes only
  changed cells / new rows / stale-row deletions).
* **Auto-triggered syncs scope to the open market day only**
  (v1.9.10+).  A `Confirm` on Year-5 day-1234 triggers a sync of
  only that day's data — not the prior 1,233 days.
* **Manual `Sync to Cloud` button** does the full sweep.  Same
  for the market-close auto-sync.
* **60-second cooldown between auto-syncs** prevents many rapid
  mutations from flooding the API.
* **Exponential backoff retry on 429** rides out brief throttle
  bursts (up to ~80 s of cumulative wait before giving up).
* **1 s sleep between tabs** keeps the per-minute write quota
  comfortable.

### DB layer — indexes added for multi-year scale (v32)

Schema migration v31 → v32 adds four composite indexes that
specifically address the queries that scale poorly with history:

| Index | Covers query | Why it matters past Year-1 |
|---|---|---|
| `idx_audit_log_record_table_changed_at` on `audit_log(record_id, table_name, changed_at DESC)` | `get_transaction_log` last-updated subquery | Without it, `MAX(changed_at)` for each txn forced sorts at year-3 scale |
| `idx_transactions_md_status` on `transactions(market_day_id, status)` | per-md status-filtered reports | Without it, status filter still scans all txns for the md |
| `idx_pli_method_txn` on `payment_line_items(payment_method_id, transaction_id)` | FAM Match Report per-method aggregation | Avoids full PLI scan when filtering by method |
| `idx_generated_rewards_md_order` on `generated_rewards(market_day_id, customer_order_id)` | Rewards lookups by md + order | Speeds the "rewards for this order" lookup the receipt printer uses |

`tests/test_multi_year_scalability.py::TestHotQueriesIndexUsage`
runs `EXPLAIN QUERY PLAN` against these patterns and asserts the
plan uses an index — so a refactor that defeats the index fails
loud.

### DB hygiene — automatic ANALYZE on market close

Every `close_market_day` call now runs `ANALYZE` after the close
commits.  This refreshes SQLite's query-plan statistics so the
optimizer can pick the right index as tables grow.  ANALYZE is
fast (~hundreds of ms even on 500K-row tables — it samples).

`close_market_day` also reads `PRAGMA freelist_count` /
`page_count` and emits a WARNING log line when fragmentation
exceeds 30 % so the operator gets a nudge to run VACUUM in the
next maintenance window.

### Local resources — bounded by retention policy

* **Logs**: `RotatingFileHandler` with 5 MB / 3 backups = 15 MB
  hard cap.
* **Binary backups**: 20-file rotation (~100 MB cap).
* **Text ledger**: `.prev1` … `.prev5` rotation (~6 snapshots,
  ~25 MB cap).
* **Photos**: `cleanup_uploaded_local_photos(retention_days=90)`
  in `fam/utils/photo_storage.py` deletes local files that meet
  ALL of:
    1. Their DB row has a non-empty `photo_drive_url` (Drive is
       the canonical store).
    2. The file's mtime is older than 90 days (operator buffer
       for offline review).
  Default retention is 90 days; configurable via the
  `photo_local_retention_days` app_settings key (set to 0 to
  disable cleanup entirely).

## Manual operations at year-3+

### 1. Quarterly VACUUM (~5 min downtime)

The market-close path emits a warning when DB fragmentation
exceeds 30 %.  When you see that warning:

1. Close all market days, exit the app.
2. From a SQLite shell against `fam.db`:
   ```sql
   VACUUM;
   ANALYZE;
   ```
3. Restart the app.  The `.db` file should be roughly half the
   size and queries that were getting slower should pop back to
   Year-1 speed.

VACUUM rewrites the entire DB in one pass (5–30 seconds at
year-5 scale); the app must be closed because VACUUM acquires an
exclusive lock.

### 2. Annual Google Sheet archive (~5 min, once a year)

Recommendation: at the start of each calendar year, archive the
prior year's data to a year-stamped sheet copy and clear the
working sheet.

Procedure:

1. Open the FAM Market Manager Google Sheet in your browser.
2. **File → Make a copy** → name it
   `FAM Market Manager — Archive 2026` (or whatever year just
   ended).  Save it in the same Drive folder.
3. In the original (working) sheet, on each tab where rows
   relate to a market day:
    * Sort by `Date` (or `Month`) descending.
    * Select the rows for the prior year (everything below the
      first row of the new year).
    * Right-click → **Delete rows**.
4. The next manual `Sync to Cloud` will see no historical rows
   on the sheet and will only push new-year data + new
   transactions.  Composite-key upserts will not duplicate.

The archived copy stays accessible forever in your Drive.  The
working sheet stays under the cell-budget.

**Why not automate this?** Google Sheets archival is a
high-stakes, irreversible operation — automating it would
require Drive write permissions on the entire account.  The
manual procedure takes 5 minutes once a year and lets the
operator visually verify the archive before deleting from the
working sheet.

### 3. Photos cleanup configuration

Default 90-day retention works for most operators.  To adjust:

```python
# In a Python shell with the app's connection live:
from fam.utils.app_settings import set_setting
set_setting('photo_local_retention_days', '180')   # keep 6 mo
# Or '0' to disable cleanup entirely.
```

The setting is read by the next `cleanup_uploaded_local_photos`
call.  Change takes effect immediately; no restart needed.

## What scaling tests pin

`tests/test_multi_year_scalability.py` runs:

1. **Schema-version check** — the new v32 indexes exist after
   migration.
2. **EXPLAIN QUERY PLAN checks** — three hot queries
   (transaction-log last-updated, per-md status filter, PLI
   per-method aggregation) actually USE indexes.  Drops a flag
   if a future query rewrite defeats the index.
3. **Year-1 vs Year-3 latency** — scoped collect of one market
   day's data must NOT slow down by more than 5× as history
   grows from 50 to 150 market days.  Fails if an unindexed
   scan creeps in.
4. **Local photo cleanup** — Drive-uploaded files past
   retention are deleted; fresh files spared; orphans
   (no Drive URL) spared even when ancient.
5. **Market-close ANALYZE** — close runs without crashing the
   close path.
6. **Documentation pin** — this very document must exist (drop
   it and the test fails).

## Year-by-year operator checklist

| Year | Recommended action | Expected duration |
|---|---|---|
| 1 | None — defaults work | n/a |
| 2 | Verify `backups/` directory has 20 files; binary backup still works | 2 min |
| 3 | Run `VACUUM` once during off-season | 30 s |
| 4 | Run `VACUUM`; run a `cleanup_uploaded_local_photos(dry_run=True)` to preview reclaim | 3 min |
| 5 | Run `VACUUM`; archive prior year's Google Sheet to a year-stamped copy | 10 min |
| 6+ | Repeat year-5 schedule annually | 10 min/year |

## Failure modes and recovery

| Failure | Detection | Recovery |
|---|---|---|
| DB query suddenly slow | Operator notices reports take >5 s | Run `ANALYZE` from SQLite shell; if no improvement, check `EXPLAIN QUERY PLAN` for an unindexed scan and add the index |
| Sync repeatedly hits 429 | `last_sync_error` populated; retry burns its 5 attempts | Rate is currently 60 writes/min/user — a burst of >60 mutations in <1 min would do this.  Cooldown should prevent it; if it persists, check that `_sync_cooldown.setInterval(60_000)` is intact |
| Sheet hits 10M-cell limit | Sync fails with "exceeded grid limits" | Run the annual archive procedure (above) |
| Photos directory full | OS write fails, FMNP/payment photos rejected | Lower `photo_local_retention_days` and run `cleanup_uploaded_local_photos()` |
| Backup directory full | Disk write error during market open | 20-file rotation should self-limit; if disk is otherwise full, free space and the next backup succeeds |
| Audit log >1M rows | DB file grows fast; Activity Log query slows | Audit log is intentionally append-only; if pruning is needed, do it manually with `DELETE FROM audit_log WHERE changed_at < date('now', '-3 years')` after a backup; the post-DELETE VACUUM reclaims space |

## Future enhancements (not blocking go-live)

These are deferred to a post-go-live release.  None of them are
required for multi-year operation if the manual operations above
run on schedule.

* **Auto-archive Google Sheets**: Drive API integration to copy
  the working sheet annually and trim the original.  Held back
  by the Drive permissions trade-off.
* **Background ANALYZE**: run on a 24-hour timer instead of
  market close.
* **Auto-VACUUM trigger**: when fragmentation > 50 %, prompt
  the operator on next launch.
* **Audit log archival**: separate `audit_log_archive` table
  populated by a 1-year-old age cutoff so the live `audit_log`
  stays small.
* **Per-customer reward rule capping**: prevents pathological
  growth in `generated_rewards` if a single customer triggers
  many rules.
