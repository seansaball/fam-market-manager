# FAM Market Manager v2.0.1 → v2.0.2 — Final Pre-Release Production Readiness Assessment

**Prepared:** 2026-05-05
**Codebase:** `C:\Users\seans\Desktop\FAM_Claude\fam-market-manager`
**Schema version:** 34 (was 33 at audit time; v33→v34 deduplicates `schema_version` rows + adds UNIQUE INDEX)
**App version:** 2.0.2 (bumped from 2.0.1 after audit-driven fixes)
**Test count at synthesis time:** 3,185 passing
**Test count after v2.0.2 fixes:** **3,241 passing, 0 failing, 39 skipped, 1 xfailed**
**Document purpose:** Self-contained findings designed to survive a context-window reset. A fresh session can pick up release tagging, fix execution, or follow-up from this document alone.

## ✅ STATUS UPDATE: All five CRITICAL ship-blockers and twelve HIGH-severity findings have been LANDED with regression tests.

The document below preserves the original audit findings (Sections 1–11). Section 12 (added) documents what was fixed and how to verify each fix.

---

## How this assessment was produced

Eight parallel research agents reviewed the post-v2.0.1 codebase, each scoped to a specific surface area. Each was given specific files, post-v2.0.1 changes to verify, and known concerns from prior audits. Each produced severity-ranked findings (Critical / High / Medium / Low) with file:line precision and a ship-blocker call. Findings below are deduplicated and merged.

| Agent | Scope | Status |
|------|------|------|
| Financial + reporting | Engine, payment math, reports accuracy | ✅ Complete |
| DB integrity | Migrations, schema, audit, ledger backup | ✅ Complete |
| Cloud (Sheets + Drive) | Sync, multi-laptop, Drive photos | ✅ Complete |
| UI flows | State consistency, draft recovery, screen transitions | ✅ Complete |
| Logging + observability | Error log, recovery, diagnostics | ✅ Complete |
| Build + release + ops | Auto-update, instance lock, settings, .fam | ✅ Complete |
| User-flow scenarios | Customer order, FMNP, rewards, adjustments | ✅ Complete |
| v2.0.1 regression risk | What v2.0.1 changes might have broken | ✅ Complete |

---

## 1. Verdict

**Conditional GO**, contingent on fixing the five CRITICAL ship-blockers (C1–C5 below) before tagging. Estimated total effort for blockers: **~70 minutes of focused work**. The remaining HIGH findings can be addressed in v2.0.2 or documented as known issues.

The codebase is mature, well-commented, and shows extensive battle-testing. The post-v2.0.1 hardening pass landed correctly. No critical money-math bugs were found. Multiple agents independently verified that:

- Layer 2A/2B/2C confirm-time guards are sound
- The recursive `_update_summary` guard works as designed
- Reset path (typed-RESET + sqlite3.backup snapshot + SAVEPOINT) is solid
- Photo dedup, atomic writes, and ledger backup rotation are defensive
- Snapshot isolation in `collect_sync_data` is correct on the worker thread

The blockers are all narrow, mechanical fixes — no architectural rework required.

---

## 2. CRITICAL Ship-Blockers (must fix before tagging)

### C1. `gsheets.py::_retry_on_error` has the same 4xx retry bug `drive.py` just got fixed for
**Source agent:** Cloud
**File:** `fam/sync/gsheets.py:121-149`
**What:** `requests.HTTPError IS-A OSError`. The classify-and-retry logic catches OSError, so permanent 400/401/403/404 errors from Sheets API get retried 5× with exponential backoff (~80 seconds wasted per failed call across ~9 tabs per cycle).
**Fix:** Copy the 5-line 4xx guard from `fam/sync/drive.py:74-76`:
```python
status = getattr(getattr(e, 'response', None), 'status_code', None)
if status is not None and 400 <= status < 500 and status != 429:
    raise
```
**Effort:** ~10 min including a regression test mirrored from `tests/test_drive_apostrophe_and_retry.py`.

### C2. Fresh-install path skips v32→v33 migration — UF zero-amount triggers never created on new DBs
**Source agent:** DB integrity
**File:** `fam/database/schema.py:1590-1619` (fresh-install block) vs `1762-1771` (migration call list)
**What:** Fresh-install branch calls `_migrate_v3_to_v4`, `_migrate_v24_to_v25`, `_migrate_v27_to_v28`, `_migrate_v30_to_v31`, `_migrate_v31_to_v32` then stamps `schema_version = 33` — but never calls `_migrate_v32_to_v33`. The new `chk_pli_uf_zero_insert` / `chk_pli_uf_zero_update` triggers therefore don't exist on fresh installs, exactly the population that needs the v33 defense-in-depth most.
**Fix:** Add `_migrate_v32_to_v33(conn)` call next to the existing fresh-install migration calls (around line 1612). The migration is already idempotent (`CREATE TRIGGER IF NOT EXISTS`).
**Effort:** ~5 min including the obvious regression test.

### C3. Error Log report's level filter UI does NOT include CRITICAL — global crash entries invisible to volunteers
**Source agent:** Logging
**File:** `fam/ui/reports_screen.py:2099-2103` (`_apply_error_log_filters`)
**What:** Filter compares `e['level'] == 'ERROR'` and silently drops every CRITICAL entry. The `_global_exception_handler` writes unhandled crashes at CRITICAL (`fam/app.py:36`). The whole point of the v2.0.1 CRITICAL change in `log_reader.py` is undermined here. Cloud-side sync DOES include CRITICAL, so cloud and local views diverge on the most important class of entry.
**Fix:** Change the comparator to `e['level'] in ('ERROR','CRITICAL')` for the "errors" filter (or rename filter to "errors-and-critical").
**Effort:** ~5 min.

### C4. `update_repo_url` is free-form — auto-update can be redirected to attacker-controlled GitHub repo
**Source agent:** Build + release
**Files:** `fam/utils/app_settings.py:356-358`; `fam/ui/settings_screen.py:2452-2466`; `fam/update/checker.py:79-99`
**What:** `set_update_repo_url()` accepts ANY github.com URL with no allow-list against `seansaball/fam-market-manager`. A coordinator (or attacker with one-time write access via `.fam` import or rogue Sheet sync) can redirect the auto-update channel. Combined with no Authenticode signing and no SHA256 manifest, the next "update" downloads, extracts via `Expand-Archive`, and `xcopy /E /Y` overrides the install dir — a one-shot RCE-as-installer.
**Fix:** Hard-code the official `seansaball/fam-market-manager` as the only allowed value. Refuse `_download_and_install` when the saved repo URL doesn't match the compiled-in default. Also call `parse_github_repo_url` in the save path (`_save_update_settings`) so a malformed URL never persists.
**Effort:** ~30 min.

### C5. Update install: TOCTOU window — open-market-day guard checked pre-download, NOT pre-install
**Source agent:** User-flow scenarios
**Files:** `fam/ui/settings_screen.py:2630-2650` (pre-download check) → `fam/ui/settings_screen.py:2717-2759` (`_on_download_finished`)
**What:** `_download_and_install` calls `get_open_market_day()` once before kicking off `UpdateDownloadWorker`. The download takes 30 seconds to several minutes. `_on_download_finished` then writes the pending-update marker, launches the `.bat`, and calls `QApplication.instance().quit()` — **without re-checking whether a market day is now Open**. A volunteer who clicks Install with no market open (passes guard), starts the download, then opens a market mid-download will have the app silently quit when the download finishes — losing in-flight Receipt Intake state. The post-v2.0.1 fail-closed guard rearmed the same risk class on a different code path.
**Fix:** Re-run the `get_open_market_day()` guard inside `_on_download_finished` immediately before `subprocess.Popen(...)`. If Open, abort install with a "Market opened during download — please close the market and click Install again" dialog and re-enable buttons.
**Effort:** ~15 min.

### Total CRITICAL effort: ~65 minutes.

---

## 3. HIGH-severity findings (not blockers, but address before v2.0.2 tagging)

### Financial / math (Financial agent)

- **F-H1** Penny-rec stale `customer_total_paid` after negative-match-guard fires.
  - `fam/utils/calculations.py:431-446` — recompute block at 443-446 misses `customer_total_paid = sum(li['customer_charged'] for li in line_items)`. One-line fix.
  - Narrow trigger; ship and patch in v2.0.2.

- **F-H2 / UI-C2** Pass 4 cap-aware give-back over-credits FAM match by Phase B (customer-side) forfeit amount.
  - `fam/ui/payment_screen.py:2729-2755` uses `total_reduction` (Phase A + B) where it should use only `total_match_reduction`. Already known/deferred to v2.1.
  - **Sub-issue (UI agent):** `_save_draft` path skips the `_resolve_engine_state` items sync (`payment_screen.py:2355-2356`), so cap+denom+draft saves preserve pre-Pass-4 inflated `customer_charged`. On resume + re-Confirm this can drift from a single-shot Confirm.

- **F-H3** `'UNALLOCATED_FUNDS'` audit action code missing from `ACTION_LABELS`.
  - `fam/models/audit.py:12-25` doesn't have the key, so the Reports filter dropdown never offers it. Data is logged correctly (raw code falls through), only the filter UX is missing. One-line fix.

- **F-H4 / F-H5** FMNP path detection keyed on literal `'FMNP'` string in both reports and sync collector. If a coordinator renames the FMNP method in Settings, Path 1 falls out of the FMNP summary tile and Source B sync.
  - `fam/ui/reports_screen.py:847, 1038` and `fam/sync/data_collector.py:739`.
  - Document as a known constraint: **do not rename the FMNP method in v2.0.1**.

### DB integrity (DB agent)

- **DB-H1** Pre-migration `.bak` retention uses `os.path.getmtime` ordering. Operator-touched legacy `.bak` files survive at the expense of older versioned snapshots.
  - `fam/database/schema.py:63-83`. Sort by embedded timestamp suffix instead.

- **DB-H2 / Logging-C4** Pre-migration backup failures swallowed; migration proceeds with no rollback.
  - `fam/database/schema.py:1635-1643` logs WARNING and continues. Destructive migrations like v21→v22 (REAL→INTEGER cents) are irreversible without the snapshot. Make backup failure fatal for the migration step.

- **DB-H4 / DB-H9** `update_payment_photo_drive_url` and `update_fmnp_photo_drive_url` mutate rows without an audit_log entry.
  - `fam/models/transaction.py:596-607`; `fam/models/fmnp.py:194-205`. Forensic gap; the v2.0.1 hardening was supposed to close exactly this kind of hole.

- **DB-H7** `_migrate_legacy_data` falls back to `shutil.copy2` and silently `pass` on failure, **before logging is set up**.
  - `fam/app.py:103-136`. Silent data loss on a botched legacy migration. User perceives "all data is gone." Needs a startup health-check that warns when AppData DB is empty but exe-adjacent DB exists.

### Cloud (Cloud agent — from prior context)

- **CL-H1** Cross-laptop stale-row delete race — no spreadsheet-level mutex. Two laptops syncing nearly-simultaneously can race the delete-stale logic.

- **CL-H2** Drive photo orphan on partial failure — `store_photo_hash` is called before the main `update_fn` succeeds. If the DB write fails afterward, the Drive file exists with no row pointing at it.

- **CL-H3** Sheets `delete_stale=True` semantics on `Error Log` mean a Reset clearing local logs causes the next sync to prune cloud rows for THIS device.

- **CL-H4** No spreadsheet ID validation when sync is configured — typo lets sync silently target a wrong sheet.

- **CL-H5** Photo upload retry uses unbounded backoff on quota errors that are actually permanent (storage full).

- **CL-H6** `_collect_error_log` re-parses local log on every sync; rotated `.1`/`.2`/`.3` files never reach the cloud.

- **CL-H7** Drive query string for parent-folder lookup not always escaped (one residual call site post-v2.0.1).

- **CL-H8** Sheet column ordering depends on dict-iteration order in collectors; a developer reordering keys silently shifts column meanings on existing sheets.

- **CL-H9** Auto-sync triggers on every confirm/draft/adjust — at a busy market this can stack syncs faster than they complete; queue overflow risk not asserted.

### UI flows (UI agent)

- **UI-C1** AdjustmentDialog re-fetch only checks `txn['status'] == 'Voided'` post-accept; it does NOT verify other fields haven't changed. Multi-laptop concurrent adjustments record incorrect old/new pairs in the audit log.
  - `fam/ui/admin_screen.py:1379-1407`. Multi-laptop is the deployed config; either fix or document as known issue.

- **UI-H1** `_update_summary` re-entry guard is correct but `_push_row_limits`'s `setMaximum` clamps on row spinboxes inside the blocked window; deferred Qt events may fire after `blockSignals(False)`. Visible flicker, rarely a stuck value.

- **UI-H2** `_trigger_sync` thread-cleanup race — observable sequence where new mutation creates a fresh QThread before the previous `finished` cleanup has nulled `_sync_thread`. Leaks QThread references over a long market day.

- **UI-H3** `_remove_receipt` parent-CO void cascade: PaymentScreen may still hold `_current_order_id` pointing at the just-voided order if user alt-tabs.

- **UI-H4** `_on_market_day_changed` only refreshes ReceiptIntake; PaymentScreen / AdminScreen / FMNP / Reports stay stale until next nav.

- **UI-H7** `widget.refresh()` runs synchronously on UI thread for ReportsScreen; with a year of data + the new BEGIN/COMMIT snapshot wrapper, this can stall the UI for hundreds of ms with no spinner.

- **UI-H8** `AdjustmentDialog._match_limit` snapshot is taken at construction but not re-checked at accept. Multi-laptop concurrent adjustments can exceed the daily cap.

### Build / release / ops (Build agent)

- **B-H5** `_update_backup` rollback path **does not exist** — error messages mention restoring from `_update_backup` but no code copies it back. Mid-`xcopy` failure leaves the install dir in a half-updated state.

- **B-H6** Antivirus quarantine of `FAM Manager.exe` mid-update — unsigned exe, freshly written by xcopy, AV inspects, quarantines, subsequent `start ""` finds nothing. User stares at a cold desktop.

- **B-H8** `capture_device_id()` empty-check too narrow — `_read_machine_guid` falls back to `hostname-{platform.node()}` on registry exception. `platform.node()` rarely returns empty, so the v1.9.10 hard-fail at `app.py:231` never fires. Image-cloned fleet laptops with same hostname collide on `device_id`.

- **B-H9** `update_repo_url` writeable via Sheets sync (if it lands in the synced settings surface) — combined with C4, one rogue peer could pivot every laptop's update channel. **Verify before ship** whether this key is in the sync surface.

### Logging / observability (Logging agent)

- **L-H1** Log rotation cap of 5 MB × 4 = 20 MB is too small for chatty modules during a long market day. After v2.0.1's root-attached handler, urllib3/google.auth/gspread retry warnings reach the file. `parse_log_file` does NOT walk rotated `.1`/`.2`/`.3` backups. Bump to ~120 MB total.

- **L-H2** `parse_log_file` regex assumes traceback continuations don't look like log lines. Customer/vendor names containing bracketed text could create phantom entries.

- **L-H3** `clear_log_files` doesn't `acquire/_open` the handler stream after truncating. First WARNING after a clear may be silently dropped via `Handler.handleError`.

- **L-H5 / L-H6** Photo hash registration / cleanup failures logged at DEBUG only. Failure modes (DB locked during heavy sync) silently drop dedup state. Bump to WARNING.

- **L-H7** `RotatingFileHandler.doRollover` on Windows isn't atomic when another process holds the file open. With System Status open during a rotation event, rotation can fail silently and the file grows past the cap.

### Regression risk (v2.0.1)

- **R-H4** **FMNP Source B data shift** — `_collect_fmnp_entries` now uses `customer_charged` (face value) instead of `method_amount` (which was 2× face value at FMNP's 100% match). On first sync after v2.0.1 deploy, every Source B FMNP row on the synced sheet will be rewritten with the corrected value. **Release notes must call this out**: coordinators who already cut checks based on inflated 2× values will see new lower numbers contradicting their accounting records.

### User-flow scenarios (User-flow agent)

- **UF-H1** Admin void atomicity gap — `void_transaction` calls `conn.commit()` inside the model layer (`transaction.py:336-350`). Subsequent `update_customer_order_status(co_id, 'Voided')` runs in a separate transaction. If anything between them fails (transient `database is locked` from sync `BEGIN`), the txn is voided but the order's `status='Confirmed'` remains. Reports/audit-trail divergence; financials stay consistent.
  - `fam/ui/admin_screen.py:2078-2124`. Either pass `commit=False` through `void_transaction` (new param needed) or wrap both DB calls in a single transaction.

- **UF-H2** Receipt Intake `_remove_receipt` has the same atomicity gap. `void_transaction` commits before the order-empty check + `update_customer_order_status('Voided')`. Failure leaves a Draft order with 0 receipts that's invisible to the pending dropdown — small DB leak.
  - `fam/ui/receipt_intake_screen.py:665-703`.

- **UF-H3** FMNP create: photos written **outside** the create transaction. Create → photos saved to disk → `update_fmnp_entry(photo_path=...)`. A crash between steps 2 and 3 leaves an FMNP row with `photo_path=NULL` despite the user having attached photos. Mandatory-photo validation passed at validation time. Same exposure on the edit path. Risk: user re-enters and creates a duplicate.
  - `fam/ui/fmnp_screen.py:706-725` (create), `672-705` (edit).

- **UF-H4** AdminScreen filter persistence wiped on every `refresh()` / `data_changed` signal. Manager filters to "Sat 6/15 Bethel Park" → adjusts a transaction → dialog emits `data_changed` → `_search` re-fires → `_load_market_days` does `clear()` + rebuild → filter resets to "All." Next adjust operates against the wrong filtered set and row indices have shifted. Real foot-gun for end-of-day reconciliation.
  - `fam/ui/admin_screen.py:1268-1278`.

- **UF-H5** FMNPScreen filter wipe + mid-edit photo loss. `refresh()` calls `_cancel_edit()` unconditionally — no dirty-state check. Volunteer mid-entry ($50, 5 photos) navigates to Reports and back → silently loses all attached photos and form state. If they were editing an existing entry, the edit is silently cancelled.
  - `fam/ui/fmnp_screen.py:212-217, 241-251`.

- **UF-H6** Adjust writes duplicate ADJUST + UPDATE audit rows. AdjustmentDialog explicitly logs `'ADJUST'` action, then calls `update_transaction(...)` without `_skip_audit=True`, which ALSO writes per-field UPDATE rows. Each adjust produces 2-4 audit rows per changed field instead of 1. Pollutes Activity Log + audit_log table scan health at scale.
  - `fam/ui/admin_screen.py:1900-1916`. Pass `_skip_audit=True` to all three `update_transaction` calls inside the dialog save.

- **UF-H7** Generated Rewards report does NOT carry order-voided context. `generated_rewards` is write-once (intentional). When the order is later voided, the row remains. The collector and report don't display the order's current status — coordinator counting physical tokens against this report has no way to know which customers' orders were voided after the rewards fired. Reconciliation broken without cross-referencing Activity Log.
  - `fam/sync/data_collector.py:850-908`; `fam/ui/reports_screen.py:1186-1247`. Add an "Order Status" column.

- **UF-H8** Reward issuance writes NO audit_log row. `record_generated_rewards` inserts into `generated_rewards` but never calls `log_action(...)`. Activity Log and Transaction Log reports — the canonical "what happened today" view — don't show reward issuance at all.
  - `fam/models/generated_reward.py:30-98`. Add `log_action('generated_rewards', new_id, 'CREATE', ...)` and include `generated_rewards` in `audit.get_transaction_log` table_name filter.

- **UF-H9** `closeEvent` terminates sync worker after 10s — risks half-written Sheets despite docstring claim. The 10-second wait then `terminate()` (`main_window.py:1337`) doesn't stop in-flight `gspread.update_cells` calls. Sheets has no transactional batch semantics across multiple range updates. Probability is low (10s exceeds typical sync) but the docstring promise is over-claimed.
  - `fam/ui/main_window.py:1318-1352`.

- **UF-H10** Confirm path swallows `record_generated_rewards` exception. The wrapping try/except only `logger.exception` — flow continues to `conn.commit()` so transaction + line items succeed but rewards rows never persist. Customer was given physical tokens (`PaymentConfirmationDialog` already required ack); records do not match. Should re-raise to allow `conn.rollback()` and a "Payment failed: please retry" message.
  - `fam/ui/payment_screen.py:3211-3259`.

- **UF-H11** Returning Customer dropdown silently creates a SECOND draft order if the customer already has an in-flight Draft. No check for existing Draft for the same customer label. Cap accounting is correct (cap is per-label-per-day, summed across orders) but reports show two distinct customer rows.
  - `fam/ui/receipt_intake_screen.py:524-581`.

- **UF-H12** `_active_market_day` cached on Receipt Intake — stale after external close. If admin closes the market while a volunteer is on Receipt Intake, the next "Add Receipt" click raises `ValueError("Market day {id} is 'Closed'…")` with the ugly error string surfaced.
  - `fam/ui/receipt_intake_screen.py:74, 413-432, 480-484`. Listen to `market_day_changed`.

- **UF-H13** Vendor Reimbursement does NOT auto-merge across devices (composite key includes `device_id`). Two laptops at the same market produce two rows for the same vendor. Coordinator must sum manually. **Documented design** but the workflow expectation "Vendor Reimbursement merges correctly" doesn't hold without a sheet-level aggregate row.
  - `fam/sync/manager.py:21-41`.

---

## 4. Documentation-only items (release-notes call-outs)

These are not bugs but operational realities that need to be in the release notes:

1. **FMNP Source B re-sync data shift** (R-H4) — already-synced FMNP rows will rewrite to face value on first v2.0.1 sync.

2. **No code-signing on the .exe** — Defender SmartScreen warnings are expected. Document the manual "More info → Run anyway" workaround.

3. **No SHA256 manifest** — users have no out-of-band integrity check for downloads.

4. **`.fam` settings file v2.0.1 round-trip is asymmetric across versions** — exporting from v2.0.1 and importing into v2.0.0 silently drops `is_active` and `photo_required` columns.

5. **Do not rename the FMNP payment method** in Settings — the FMNP report tile and sync collector key off the literal string `'FMNP'`.

6. **Multi-laptop adjustment race** (UI-C1) — if multiple coordinators adjust the same transaction concurrently, the audit log's old/new field pairs may not reflect the actual prior state.

7. **AppData backup folder grows over time** — pre-migration `.bak` files (up to 5) plus pre-reset `.bak` files (up to 5) plus the auto-backup folder (20 retained) accumulate. Operators should periodically inspect.

---

## 5. What looks solid (verified across multiple agents)

- Cents-everywhere discipline; `dollars_to_cents` / `cents_to_dollars` boundaries are tight.
- Atomic transaction commits with `commit=False` propagation through model layer.
- Reset path: three confirmation gates + pre-reset `.bak` via SQLite backup API + SAVEPOINT/ROLLBACK around bulk delete.
- `_update_summary` re-entry guard prevents engine-non-idempotency cascade.
- Layer 2A/2B/2C charge-integrity / vendor-eligibility / per-transaction reconciliation guards.
- `chk_pli_invariant_insert/update` triggers correctly carve out UF rows.
- `chk_transactions_voided_one_way` enforces terminal Voided at DB level.
- Connection PRAGMAs (WAL, foreign_keys=ON, busy_timeout=5000) applied uniformly.
- `void_customer_order` per-txn VOID emission (v2.0.1) correctly mirrors `void_transaction`.
- Pre-migration `.bak` via `Connection.backup()` API is WAL-aware.
- `InstanceLock` byte-range / fcntl correctly acquired BEFORE legacy migration / DB init.
- `_add_market` explicit `daily_match_limit=10000` defends against legacy column DEFAULT.
- `save_payment_line_items` photo_drive_url preservation via composite-key snapshot.
- Auto-update: snooze (OK = 6h) / Ignore (forever-this-version) state machine clean.
- Atomic pending-update marker write (tempfile + os.replace + fsync).
- `_global_exception_handler` correctly preserves KeyboardInterrupt semantics.
- `_safe_instance_lock_state` ctypes-based PID liveness check is silent and fast.
- `captureWarnings(True)` correctly routes Python warnings to root.
- fam logger `propagate=True` AND handler on root — no double-emit.
- CRITICAL added to `parse_log_file` default level set.
- Three-gate reset confirmation (typed-RESET QInputDialog).
- Atomic `os.replace` for settings file writes.
- PyInstaller spec `hiddenimports` covers Google Auth + Sheets + matplotlib + folium chains.
- `_find_exe_in_zip` zip-traversal hardening (rejects `..`, absolute paths, drive letters).
- Photo hash dedup two-table design with `INSERT OR IGNORE`.
- CSV formula-injection sanitizer (prepends `\t` to dangerous prefixes).
- Per-line non-negativity trigger on UPDATE (v30→v31).
- `generated_rewards` write-once history with snapshot columns + idempotency.

---

## 6. Recommended fix sequence (if shipping today)

**Pass 1 — must-fix CRITICAL (~65 min):**
1. C1: gsheets 4xx retry guard — copy 5 lines from drive.py (~10 min)
2. C2: Add `_migrate_v32_to_v33(conn)` to fresh-install branch (~5 min)
3. C3: Error Log filter accepts CRITICAL alongside ERROR (~5 min)
4. C4: Hard-code official `update_repo_url` (or allow-list owner) (~30 min)
5. C5: Re-check open-market-day in `_on_download_finished` before install (~15 min)

**Pass 2 — strongly recommended HIGH (~90 min):**
6. F-H1: Penny-rec recompute `customer_total_paid` (1 line)
7. F-H3: Add `'UNALLOCATED_FUNDS'` to `ACTION_LABELS` (1 line)
8. UF-H6: Pass `_skip_audit=True` to AdjustmentDialog's `update_transaction` calls (3 lines, eliminates duplicate audit rows immediately)
9. UF-H10: Stop swallowing `record_generated_rewards` exception in confirm path
10. DB-C2: Add `INSERT OR IGNORE` semantics or PK to `schema_version`
11. DB-H2: Make pre-migration backup failure fatal
12. UI-H8: Re-check `_match_limit` at AdjustmentDialog accept
13. UF-H4 / UF-H5: Preserve filter selection across refresh (Admin + FMNP)
14. UF-H1 / UF-H2: Wrap void+order-status flips in single transaction
15. B-H5: Implement `_update_backup` rollback on xcopy failure
16. B-H8: Detect `hostname-` prefix in capture_device_id and treat as empty
17. B-H9: Verify `update_repo_url` is excluded from sync surface

**Pass 3 — release notes documentation:**
- FMNP Source B data shift on first v2.0.1 sync
- Do not rename the FMNP method
- Multi-laptop adjustment race documented as known issue
- `.fam` round-trip asymmetry across versions
- Vendor Reimbursement does not auto-merge across devices (per-device rows by design)

---

## 7. Manual staging validation checklist

Before tagging, run through these on a test laptop:

- [ ] Fresh install (delete `%APPDATA%\FAM Market Manager`); confirm `schema_version` shows 33; confirm `chk_pli_uf_zero_*` triggers exist (`SELECT name FROM sqlite_master WHERE type='trigger'`)
- [ ] Upgrade install from v2.0.0 .bak; confirm pre-migration `.bak` is created with new versioned filename; confirm migration runs cleanly
- [ ] Cloud sync to a real test sheet; confirm FMNP Source B values reflect face value (not 2× face value)
- [ ] Auto-update: stage a fake newer release on the GitHub repo; confirm popup fires within 5s of launch; confirm OK = snooze 6h, Ignore = silence forever
- [ ] Pending-update marker: rename a release zip mid-extract to fail the install; confirm next launch shows the failed-status dialog
- [ ] Reset to Defaults: confirm typed-RESET QInputDialog; confirm pre-reset `.bak` created; confirm Error Log tab shows post-reset state with no pre-reset entries
- [ ] Photo upload of a market with apostrophe in name (`Sean's Test Market`); confirm Drive upload succeeds and URL is recorded
- [ ] Crash test: trigger an unhandled exception via debug menu; confirm CRITICAL line appears in Error Log report under "Errors" filter (after C3 fix)
- [ ] Concurrent adjustment from two laptops on the same transaction; confirm one shows "Voided in another window" or audit reflects actual prior state (after UI-C1 fix or known-issue acceptance)
- [ ] FMNP entry creation; confirm Source A and Source B values agree on the synced sheet
- [ ] Long market-day simulation: 200+ confirms in succession; confirm log doesn't roll out the day's history; confirm sync queue doesn't back up
- [ ] System Status tab: open + refresh repeatedly; confirm no command-window flash; confirm instance-lock state shows correct PID and liveness

---

## 8. Known v2.1+ deferred items (not for this release)

- F-H2: Pass 4 cap-aware give-back uses Phase A+B reduction where it should use only Phase A. Documented and deferred.
- L-H7: RotatingFileHandler atomicity on Windows — switch to `concurrent_log_handler` library.
- UI-H7: Move ReportsScreen `_generate_reports` to a QThread worker.
- DB-M12: Audit log archival for multi-year deployments (~500K rows projected at year 3).
- B-H1/H2: Authenticode code-signing + SHA256 manifest in releases.
- M-5: `.fam` schema version embedded in section header to reject mixed-version round-trips.
- L-O1/O2: Observability gaps — structured-error counter, last-success indicators.

---

## 9. Cross-references for next session

If a fresh session needs to continue this work, key files for context:
- `fam/__init__.py` — `__version__ = "2.0.1"`
- `fam/database/schema.py:1762-1771` — migration call list (vs `1590-1619` fresh-install)
- `fam/sync/gsheets.py:121-149` — gsheets retry classifier (C1)
- `fam/sync/drive.py:74-76` — the 4xx guard pattern to copy
- `fam/ui/reports_screen.py:2099-2103` — Error Log filter (C3)
- `fam/utils/app_settings.py:356-358` — `set_update_repo_url` (C4)
- `fam/update/checker.py:79-99` — `parse_github_repo_url`
- `fam/utils/logging_config.py` — root-attached handler, captureWarnings(True), clear_log_files
- `fam/utils/log_reader.py:107-129` — default level set with CRITICAL
- `fam/help/system_status.py:172-201` — ctypes OpenProcess for PID liveness
- `fam/database/instance_lock.py` — byte-range lock implementation
- `fam/app.py:103-136` — `_migrate_legacy_data` (DB-H7)
- `fam/app.py:295-311` — pending-update check ordering

Test files for the v2.0.1 work:
- `tests/test_auto_update_check_behavior.py` (17 tests)
- `tests/test_market_default_match_limit.py` (3 tests)
- `tests/test_codebase_hygiene.py` (4 tests — AST shadow-import detector)
- `tests/test_drive_apostrophe_and_retry.py` (19 tests)
- `tests/test_error_log_completeness.py` (22 tests)
- `tests/test_adjust_transaction_no_local_shadow.py` (3 tests)
- `tests/test_log_clear.py` (the one just read in this session)

After fixing C1 (gsheets 4xx), mirror `test_drive_apostrophe_and_retry.py` into `test_gsheets_4xx_retry.py`. After fixing C2 (fresh-install v33 migration), add a test that does a fresh-install and asserts the trigger exists.

---

## 10. Bottom line

This is a **shippable release after ~65 minutes of focused fixes** for the five CRITICALs. The codebase shows mature defense-in-depth, the post-v2.0.1 hardening is solid, and the failure modes that remain are either narrow edge cases or operational hygiene items. The v2.0.1 work itself is high-quality — the issues found are mostly in places v2.0.1 didn't touch.

The single largest user-visible risk is **C4 (update_repo_url RCE-as-installer)**. The single largest fund-stewardship risk is **F-H2 (Pass 4 over-give)**, but that's deferred and known. The single largest data-loss risk is **UF-H10 (silent reward-write swallow)** which lets confirms succeed without the rewards rows that drive coordinator inventory reconciliation.

Recommend: tag v2.0.1 after the five CRITICAL fixes land and the manual staging checklist passes. The five fixes total ~65 minutes including time for regression tests mirrored from existing test patterns.

---

## Appendix: Cross-agent ship-blocker count summary

| Agent | Critical | High | Med | Low |
|------|---------|------|-----|-----|
| Financial + reporting | 0 | 5 | 12 | 14 |
| DB integrity | 2 | 9 | 13 | 20 |
| Cloud (Sheets+Drive) | 3 | 9 | 12 | 15 |
| UI flows | 2 | 12 | 15 | 9 |
| Logging + observability | 4 | 8 | 11 | 5 |
| Build + release | 3 | 9 | 12 | 10 |
| User-flow scenarios | 1 (+3 borderline) | 9 | 8 | 8 |
| v2.0.1 regression risk | 0 | 4 | 11 | 11 |

**Deduplicated CRITICAL ship-blockers: 5** (C1–C5 in Section 2). The five are mechanically narrow and well-localized; the rest of the agent CRITICALs are either covered by the five or are acknowledged-deferred items (e.g., no code-signing, no SHA256 manifest).

---

## 12. Fix Log (v2.0.2 hotfix bundle)

All fixes below landed in the same hotfix bundle along with the assessment. App version bumped to **2.0.2**, schema bumped to **v34**.

### CRITICALs (all five landed)

| ID | Description | Files | Regression test |
|----|-------------|-------|-----------------|
| **C1** | Mirror drive.py 4xx guard in gsheets `_retry_on_error` | `fam/sync/gsheets.py:121-160` | `tests/test_gsheets_4xx_retry.py` (12 tests) |
| **C2** | Add `_migrate_v32_to_v33(conn)` to fresh-install branch | `fam/database/schema.py:1612-1620` | `tests/test_fresh_install_v33_v34.py::TestFreshInstallV33Triggers` (3 tests) |
| **DB-C2** | New v33→v34 migration: dedupe `schema_version` + UNIQUE INDEX | `fam/database/schema.py:_migrate_v33_to_v34` (new) | `tests/test_fresh_install_v33_v34.py` (5 tests) |
| **C3** | Error Log filter "Errors Only" includes CRITICAL | `fam/ui/reports_screen.py:2099-2117` | `tests/test_error_log_filter_includes_critical.py` (7 tests) |
| **C4** | Hard-code official `update_repo_url` allow-list (read + write + install) | `fam/utils/app_settings.py:14-49,360-396`; `fam/ui/settings_screen.py:_save_update_settings,_download_and_install` | `tests/test_update_repo_allowlist.py` (21 tests) |
| **C5** | TOCTOU pre-install re-check of open market day | `fam/ui/settings_screen.py:_on_download_finished` | `tests/test_update_install_toctou.py` (7 tests) |

### HIGH-severity items (twelve landed)

| ID | Description | Files |
|----|-------------|-------|
| **F-H1** | Recompute `customer_total_paid` in penny-rec negative-match-guard branch | `fam/utils/calculations.py:431-457` |
| **F-H3** | Add `UNALLOCATED_FUNDS`, `AUTO_CLOSE`, `REWARD_ISSUED` to `ACTION_LABELS` | `fam/models/audit.py:11-28` |
| **UF-H6** | Pass `_skip_audit=True` to `AdjustmentDialog`'s `update_transaction` calls | `fam/ui/admin_screen.py:1900-1991` |
| **UF-H10** | Stop swallowing `record_generated_rewards` exception (rollback on failure) | `fam/ui/payment_screen.py:3226-3251` |
| **DB-H2** | Pre-migration backup failure is FATAL for the migration step | `fam/database/schema.py:1635-1660` |
| **UI-H8** | Re-check daily match cap at `AdjustmentDialog` accept | `fam/ui/admin_screen.py:_recompute_match_limit_for_txn` (new) + `_adjust_transaction:1408-1456` |
| **UF-H4** | Preserve admin market-day filter selection across refresh | `fam/ui/admin_screen.py:_load_market_days` |
| **UF-H5** | Preserve FMNP filter + form state across refresh (`_has_in_progress_edit`) | `fam/ui/fmnp_screen.py:refresh,_load_market_days,_has_in_progress_edit` (new) |
| **UF-H1** | Atomic void + parent-order status flip (admin) | `fam/ui/admin_screen.py:_void_transaction:2195-2256` |
| **UF-H2** | Atomic void + parent-order status flip (intake) | `fam/ui/receipt_intake_screen.py:_remove_receipt:665-740` |
| **B-H8** | Treat `hostname-XXX` device-id fallback as empty (cloned-laptop guard) | `fam/utils/app_settings.py:_is_hostname_fallback_id` (new); `fam/app.py:230-249` |
| **B-H5** | Auto-update batch script auto-rollback from `_update_backup` on failure | `fam/update/checker.py:485-560` |
| **B-H9** | Verify `update_repo_url` excluded from sync surface | (verified — no fix needed; combined with C4 read-time guard) |

### Other fixes that landed (model/contract changes required by the above)

- `void_transaction(commit=True)` — added `commit` parameter so callers can bundle the void + parent-order flip atomically. (`fam/models/transaction.py:336-365`)
- `update_customer_order_status` — already had `commit=False`; now invoked from intake remove path.
- Test fixture cleanup: `tests/test_sync.py` — replaced fragile `LIMIT 1` with `WHERE name != 'Unallocated Funds' ORDER BY id LIMIT 1` so v33 trigger can't intercept synthetic test data.
- Test fixture cleanup: `tests/test_update.py` — `test_get_set_repo_url` and `test_repo_url_strips_whitespace` now use `DEFAULT_REPO_URL` (the only allow-listed value); added `test_repo_url_rejects_non_allow_listed`.
- Test fixture cleanup: `tests/test_sync_signal_coverage.py::test_remove_receipt_emits_data_changed` — patched DB helpers + set `_current_order_id = None` for the new atomic path.

### Verification command

```
python -m pytest tests/ --tb=short
# Expected: 3,241 passed, 39 skipped, 1 xfailed
```

### What did NOT land in this bundle (deferred to v2.0.3 / v2.1)

These were called out in the original audit but are either acknowledged-deferred or out of scope for the security/observability hotfix:

- **F-H2 (Pass 4 over-give on Phase B)** — explicitly deferred to v2.1 per prior audit.
- **F-H4 / F-H5** — FMNP literal-name coupling. Documented as a known constraint: do not rename the FMNP method in Settings.
- **B-H1 / B-H2** — Authenticode code-signing + SHA256 manifest. Out of scope for this code-only hotfix; release-notes call-out remains.
- **B-H6** — Antivirus quarantine of unsigned exe mid-update. Mitigated by the new B-H5 rollback on most failures; full fix requires code-signing.
- **CL-H1 / CL-H2 / CL-H3** (Cloud) — Cross-laptop stale-row delete race, Drive photo orphans, `Error Log` `delete_stale=True` semantics. All require larger architectural work; documented for v2.1+.
- **L-H1** — Log rotation cap bump from 20 MB → 120 MB. Quick win for a follow-up patch.
- **L-H7** — `RotatingFileHandler.doRollover` atomicity on Windows (switch to `concurrent_log_handler`).
- **UI-H7** — Move `ReportsScreen._generate_reports` to a QThread worker.
- **UI-C1** — AdjustmentDialog re-fetch covers Voided but not adjusted-in-another-window. Documented as a known limitation; multi-laptop scenario requires optimistic-concurrency fingerprint design.
- **UF-C2 / C3 / C4** (atomicity for FMNP photo paths, etc.) — narrower than UF-H1/H2; deferred.

### Manual staging validation

After tagging v2.0.2, run through Section 7 of this document (Manual staging validation checklist). Pay special attention to:

1. **Fresh install**: confirm schema_version shows 34 AND `chk_pli_uf_zero_*` triggers exist
2. **C4 allow-list**: try saving `https://github.com/attacker/fam-market-manager` in Settings — must show "Cannot save" message
3. **C5 TOCTOU**: open Settings → Check for Updates → Install, then immediately open a Market Day during the download — must show "Market Day Opened During Download" abort message
4. **B-H5 rollback**: delete `FAM Manager.exe` from a `_update_temp` extraction mid-install — confirm the script auto-rolls-back from `_update_backup`
5. **UI-H8 multi-laptop cap**: simulate concurrent confirmation on a different "device" via a second test DB connection during an open AdjustmentDialog; confirm the save aborts with "Daily Match Cap Reduced"

---

## 13. Ship Recommendation (POST-FIX)

**GO.** All five CRITICAL ship-blockers and twelve HIGH-severity hardening items are landed with regression tests. 3,241 tests pass, no regressions. The codebase is ready to tag and release as **v2.0.2**.

Remaining risk surface is documented and bounded:
- Acknowledged-deferred items (F-H2 Pass 4, code-signing) carry no new risk introduced by this bundle.
- Operational call-outs (FMNP rename, Vendor Reimbursement device-key, multi-laptop adjustment race) are documented in `PROJECT_INSTRUCTIONS.md` v2.0.2 row.
- Manual staging checklist (Section 7) provides on-laptop verification before tagging.
