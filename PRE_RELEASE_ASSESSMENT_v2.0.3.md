# FAM Market Manager v2.0.3 — Final Pre-Release Audit Synthesis

**Prepared:** 2026-05-05
**Schema:** v34 (unchanged from v2.0.2)
**Codebase:** `C:\Users\seans\Desktop\FAM_Claude\fam-market-manager`
**Test count:** **3,264 passing, 39 skipped, 1 xfailed, 0 failing** (up from 3,241 in v2.0.2)
**Document purpose:** Self-contained synthesis of four post-v2.0.2 verification agents. Any fresh session can pick up tagging from this document alone.

## ⏵ Verdict: GO (with manual staging-laptop validation in §6)

All four agents completed. Findings split into:
- 16 v2.0.2 fixes — **all verified correct** (Agent 1)
- 2 NEW-CRIT scale/lifecycle issues — **landed** (Agent 2)
- 2 CRIT-SEC items — **landed**, including a regression my v2.0.2 B-H5 fix introduced (Agent 3)
- 7 TEST-CRIT regression-test gaps — **closed** (Agent 4)

Plus **23 new regression tests** that fail without each fix.

---

## 1. The four-agent post-fix review

| Agent | Scope | Result |
|------|------|------|
| **Fix verification** | Verify each v2.0.2 fix actually does what its comments / commit message claim, look for new failure modes | All 16 fixes correct. 2 cosmetic concerns (DB-H2 dialog title, B-H8 legacy-install hard-fail) — release-notes call-outs. |
| **Audit gaps** | Find what the previous parallel-agent audit missed: concurrent state machines, scale, error paths, UX dark corners, export integrity, build/packaging | 2 NEW-CRIT, 7 NEW-HIGH, 7 NEW-MED, 7 NEW-LOW |
| **Adversarial security** | Paranoid pass: code execution, SQL injection, path traversal, sensitive data exposure, allow-list bypass attempts, update-channel pivot | 2 CRIT-SEC (one regression from B-H5, one path traversal exfil), 1 HIGH-SEC, 5 MED-SEC, 5 LOW-SEC |
| **Test suite quality** | Flakiness, drift, coverage gaps for the v2.0.2 fixes themselves, fixture hygiene | 7 TEST-CRIT (5 of 12 v2.0.2 H-fixes lacked failure-path coverage), 6 TEST-HIGH, ~10 TEST-MED, ~15 TEST-LOW |

---

## 2. CRIT items landed in v2.0.3

### CRIT-SEC-1: `_update_backup` rollback regression (introduced by v2.0.2 B-H5)
- **Issue:** The B-H5 hotfix added auto-rollback from `_update_backup` to recover failed updates. But `_update_backup` lives in `%APPDATA%` (data dir, attacker-writable in the threat model). An attacker with FS write to data_dir could plant a malicious `FAM Manager.exe` there, then either wait for or force an update failure (e.g. by chmoding the install dir read-only). The auto-rollback would silently restore attacker code into the install dir — escalating data-dir-write to install-dir-RCE.
- **Files:** `fam/update/checker.py:486-628` (rollback_label_block + manifest write)
- **Fix:** SHA-256 manifest written to `app_dir\_update_manifest.sha256` at backup time (install-dir trust boundary). Rollback uses PowerShell `Get-FileHash` to verify the backup's `FAM Manager.exe` hash matches the trusted manifest BEFORE copying. Mismatch → refuse rollback, emit "SECURITY WARNING: Backup hash MISMATCH" with manual-recovery instructions.
- **Sub-fix:** Refactored install-block to use `goto :ROLLBACK_AND_EXIT` label instead of inlining rollback in parenthesised `if (...)` blocks. Batch syntax forbids `goto` labels inside `(...)`. The bug surfaced because the original v2.0.2 inline insertion broke the script's control flow (6 batch-runtime tests failed); the goto-label refactor restored 3,264 passing.

### CRIT-SEC-2: Photo path traversal arbitrary-file exfil
- **Issue:** `fam/utils/photo_storage.py:181-184` had `os.path.join(get_data_dir(), relative_path)` — Python returns the absolute path verbatim if the second arg is absolute. An attacker writing `photo_path='C:\Users\X\.aws\credentials'` to a `payment_line_items` or `fmnp_entries` row would cause the next Drive sync to upload that file to the volunteer's Google Drive folder.
- **Files:** `fam/utils/photo_storage.py:_validate_relative_photo_path` (new); `get_photo_full_path` raises `UnsafePhotoPathError`; `photo_exists` returns False on unsafe; `fam/sync/drive.py:866-880` explicit pre-upload validation.
- **Fix:** Reject absolute paths, drive-letter prefixes, `..` segments, and any path whose normalised form lands outside data_dir. Drive uploader logs `ERROR` and skips unsafe rows.

### NEW-CRIT-1: `closeEvent` orphans the download thread
- **Issue:** `MainWindow.closeEvent` waited up to 10s for `_sync_thread` and 3s for `_update_check_thread` but never touched `settings_screen._update_dl_thread`. Closing the app mid-download (10–60 MB ZIP, 30s–min on conference Wi-Fi) left an orphan QThread that eventually fired `_on_download_finished` against a destroyed parent widget — uncaught C++ exception or zombie process.
- **Files:** `fam/ui/main_window.py:1342-1361`
- **Fix:** closeEvent now walks `settings_screen._update_dl_thread` with quit + 10s wait + terminate fallback, parallel to the existing sync/update-check thread cleanup.

### NEW-CRIT-2: `_load_activity_log` unbounded query
- **Issue:** `ReportsScreen._load_activity_log` did `SELECT … FROM audit_log ORDER BY changed_at DESC` with no LIMIT, then `setRowCount(len(rows))` and 10 setItem calls per row. At year 2-3 scale (500K+ audit rows) this froze the UI thread for 30-60s every time the operator opened Reports → Activity Log. Peer query `_load_transaction_log` already used `limit=500`; this one was the lone unbounded loader.
- **Files:** `fam/ui/reports_screen.py:1141-1170`
- **Fix:** Added `LIMIT 1000` and stable `ORDER BY changed_at DESC, id DESC`. The Transaction Log tab and the cloud-synced Audit Log sheet retain the full history; this one is a "what happened recently" view, not the canonical record.

---

## 3. v2.0.2 fixes verified by Agent 1

All 16 fixes correct as implemented:

| ID | What | Verified |
|----|------|------|
| C1 | gsheets `_retry_on_error` 4xx guard mirrors `drive.py` | ✓ |
| C2 | Fresh-install branch runs `_migrate_v32_to_v33` | ✓ |
| DB-C2 | New v33→v34 migration: dedupe `schema_version` + UNIQUE INDEX | ✓ |
| C3 | "Errors Only" filter includes CRITICAL | ✓ |
| C4 | `update_repo_url` allow-list (save + read + install) | ✓ |
| C5 | `_on_download_finished` TOCTOU re-check | ✓ |
| F-H1 | Penny-rec `customer_total_paid` recompute | ✓ |
| F-H3 | `UNALLOCATED_FUNDS` etc. in `ACTION_LABELS` | ✓ |
| UF-H6 | AdjustmentDialog `_skip_audit=True` | ✓ |
| UF-H10 | Rewards rollback on failure (no inner swallow) | ✓ |
| DB-H2 | Pre-migration backup failure FATAL | ✓ |
| UI-H8 | Match-cap re-check at AdjustmentDialog accept | ✓ |
| UF-H4 / UF-H5 | Filter persistence (Admin + FMNP) | ✓ |
| UF-H1 / UF-H2 | Atomic void+order-status flip | ✓ |
| B-H8 | hostname-XXX fallback rejected by `get_device_id` | ✓ |
| B-H5 | Auto-rollback on xcopy failure | ✓ (now hash-pinned per CRIT-SEC-1) |

Two cosmetic concerns surfaced (do NOT block ship):
- **DB-H2 dialog title** — backup-failure RuntimeError currently surfaces under "Database Error" dialog. Body text is correct but the title and lead sentence are misleading. Logged for v2.1 polish.
- **B-H8 legacy hard-fail** — installs that previously stored `device_id='hostname-XYZ'` will refuse to launch on first v2.0.3 run. Intentional but warrants release-notes call-out.

---

## 4. Test coverage gaps closed (`tests/test_v2_0_3_regression_coverage.py`)

23 new tests covering all 7 TEST-CRIT items + CRIT-SEC-2 + NEW-CRIT-2:

| Test class | Validates |
|-----------|-----------|
| `TestPennyRecCustomerTotalRecompute` | F-H1 — dict-returned `customer_total_paid` consistent with line_items after negative-match-guard mutation, no spurious `is_valid=False` |
| `TestAdjustmentAuditNoDoubleEmit` | UF-H6 — `update_transaction(_skip_audit=True)` emits zero per-field rows; `_skip_audit=False` emits exactly one |
| `TestRewardsRollbackOnFailure` | UF-H10 — caller-driven rollback discards uncommitted PLI rows; source-pin guards against re-introducing inner try/except |
| `TestPreMigrationBackupFailureFatal` | DB-H2 — RuntimeError propagates from `_write_pre_migration_backup` failure |
| `TestRecomputeMatchLimitForTxn` | UI-H8 — helper returns None when cap inactive; correct remaining cap excluding current txn |
| `TestVoidTransactionCommitFalse` | UF-H1/H2 — void rollback undoes status change AND audit row |
| `TestGetDeviceIdRejectsHostnameFallback` | B-H8 — `hostname-XXX` returns None; real GUID returns string |
| `TestPhotoPathTraversalRejected` | CRIT-SEC-2 — absolute Win/Unix paths, drive letter, `..` escape all rejected; legitimate relative path accepted |
| `TestActivityLogLimit` | NEW-CRIT-2 — source-pin verifies `LIMIT` clause |

Test fixture hygiene: all use `tmp_path` per-test isolation via the autouse `fresh_db` fixture.

---

## 5. HIGH and MED items deferred to v2.0.4 / v2.1

These were found by the second-round audit but deemed not ship-blocking:

### Agent 2 (audit gaps) — deferred
- **HIGH-1** gsheets unbounded payload size (chunk `dirty_cells` to ~5K cells/call)
- **HIGH-2** Backup retention sorts globally — low-volume markets get evicted
- **HIGH-3** `generate_customer_label` TOCTOU on same device (no UNIQUE on `customer_orders.customer_label`)
- **HIGH-4** Three exports give three different numbers (ledger backup includes Voided, sync excludes Voided, FMNP-context block uses a third filter)
- **HIGH-5** Backup filename second-resolution collision
- **HIGH-6** `connection.py` missing `synchronous=NORMAL` and `wal_autocheckpoint`
- **HIGH-7** Zero accessibility annotations across UI
- 7 MED, 7 LOW

### Agent 3 (security) — deferred
- **HIGH-SEC-2** Diagnostic clipboard exfiltrates Drive folder ID + Sheet ID
- **MED-SEC-1** Allow-list regex permits `http://` (neutralized at API layer but defense-in-depth gap)
- **MED-SEC-2** System Status `setHtml` only escapes `<>` (not `&`)
- **MED-SEC-3** `_update_temp` not GC'd on crash
- **MED-SEC-4** Sub-frame TOCTOU window between C5 re-check and `subprocess.Popen`
- **MED-SEC-5** Vendor name in log can carry HTML payload to clipboard

### Agent 4 (tests) — deferred
- **TEST-HIGH** parity-matrix `pytest.skip(...)` audit (suspected to mask real engine disagreements)
- **TEST-HIGH** Conftest pin `eastern_now` and `date.today` alongside `eastern_today`
- **TEST-HIGH** IDN/homoglyph + path-traversal tests for update-allowlist
- **TEST-HIGH** Brittle source-pin in `test_update_install_toctou.py`
- ~10 MED, ~15 LOW

---

## 6. Manual staging validation checklist (must pass before tag)

Repeat from v2.0.2's checklist plus the v2.0.3-specific items:

### v2.0.2 carry-overs
- [ ] Fresh install: schema_version = 34, `chk_pli_uf_zero_*` triggers exist, `idx_schema_version_unique` exists
- [ ] C4 allow-list: try saving `https://github.com/attacker/fam-market-manager` → must show "Cannot save" message
- [ ] C5 TOCTOU: open Settings → Check → Install, then Open Market Day during download → must abort with "Market Day Opened During Download"
- [ ] B-H5 rollback (now hash-verified): delete `FAM Manager.exe` from `_update_temp` mid-install → confirm rollback restores from `_update_backup` AND verifies hash
- [ ] UI-H8 multi-laptop cap: simulate concurrent confirm reducing cap during open AdjustmentDialog → save aborts with "Daily Match Cap Reduced"

### v2.0.3 new
- [ ] **CRIT-SEC-1 manifest defense:** corrupt `_update_backup\FAM Manager.exe` (overwrite with garbage) BEFORE triggering an update failure → rollback must refuse with "SECURITY WARNING: Backup hash MISMATCH"
- [ ] **CRIT-SEC-1 manifest absent:** delete `app_dir\_update_manifest.sha256` BEFORE update → rollback must refuse with "No trusted backup manifest found"
- [ ] **CRIT-SEC-2 path traversal:** manually `UPDATE payment_line_items SET photo_path='C:\Windows\System32\drivers\etc\hosts' WHERE id=1` → next sync must log `ERROR refusing unsafe photo path`, NOT upload the file
- [ ] **NEW-CRIT-1 download thread:** Settings → Check → Install → close window mid-download → confirm app closes within 10s with no orphan process in Task Manager
- [ ] **NEW-CRIT-2 Activity Log:** populate audit_log to 100K+ rows (synthetic) → open Reports → Activity Log → confirm <1s render

---

## 7. Bottom line

**Tag-ready as v2.0.3** after manual staging validation. The codebase is materially better than at the start of the v2.0.1 review:
- 5 v2.0.1 critical ship-blockers + 12 HIGH-severity items fixed in v2.0.2
- 4 additional CRITICAL items (2 security regressions caught by adversarial review, 2 scale/lifecycle issues) fixed in v2.0.3
- 23 regression tests added that fail without each v2.0.2 / v2.0.3 fix
- 3,264 tests pass, no regressions

Largest remaining risk surface is HIGH-3 (customer-label same-device race — fix queued for v2.0.4) and HIGH-7 (no a11y annotations — v2.1 release polish). All deferred items are documented above with file:line references.
