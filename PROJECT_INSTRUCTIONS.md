# FAM Market Manager — Project Instructions & Specification

> **Purpose:** This file is the single source of truth for the FAM Market Manager
> application. It is written for an AI coding assistant or a new developer who
> needs to understand, maintain, or extend the project **without** access to
> previous conversation history. Keep this file up to date with every commit.
>
> **Last updated:** 2026-03-09 — v1.7.0

---

## 1. Project Overview

**FAM Market Manager** is a desktop POS/back-office application used at farmers
markets to track customer transactions, calculate Food Assistance Match (FAM)
subsidies, process payments, record FMNP checks, and generate reports for
vendor reimbursement. Deployed across multiple market locations, each on a
dedicated Windows PC.

| Stack         | Technology                          |
|---------------|-------------------------------------|
| Language      | Python 3.12+                        |
| GUI framework | PySide6 (Qt 6)                      |
| Database      | SQLite (WAL mode, foreign keys on)  |
| Charts        | matplotlib                          |
| Geolocation   | folium + pgeocode                   |
| Data export   | pandas                              |
| Packaging     | PyInstaller (Windows .exe)          |
| Cloud Sync    | gspread + google-auth               |
| Auto-Update   | urllib.request (stdlib)              |
| Tests         | pytest (618 tests)                  |

---

## 2. Repository Layout

```
fam-market-manager/
├── fam/                          # Application package
│   ├── __init__.py               # __version__ = "1.7.0"
│   ├── app.py                    # Qt app entry, data dir, exception handler
│   ├── settings_io.py            # .fam file import/export
│   ├── database/
│   │   ├── connection.py         # Thread-local SQLite connection
│   │   ├── schema.py             # Table DDL + migrations (v1–v11)
│   │   ├── seed.py               # Opt-in sample data (via tutorial)
│   │   └── backup.py             # SQLite backup API + retention
│   ├── models/
│   │   ├── vendor.py             # Vendor CRUD
│   │   ├── market_day.py         # Market day lifecycle
│   │   ├── payment_method.py     # Payment method CRUD
│   │   ├── transaction.py        # Receipts + payment line items
│   │   ├── customer_order.py     # Multi-receipt customer orders
│   │   ├── fmnp.py               # FMNP check entries
│   │   └── audit.py              # Append-only audit log
│   ├── ui/
│   │   ├── main_window.py        # Sidebar nav + screen stack + backup timer + auto-update check
│   │   ├── market_day_screen.py  # Screen 0 — Open/close market day
│   │   ├── receipt_intake_screen.py  # Screen 1 — Add receipts
│   │   ├── payment_screen.py     # Screen 2 — Allocate payments + receipt printing
│   │   ├── fmnp_screen.py        # Screen 3 — FMNP entry
│   │   ├── admin_screen.py       # Screen 4 — Adjustments & voids
│   │   ├── reports_screen.py     # Screen 5 — Reports & exports
│   │   ├── settings_screen.py    # Screen 6 — Config + import/export + cloud sync + updates
│   │   ├── tutorial_overlay.py   # Guided tutorial + auto-configure
│   │   ├── styles.py             # Global QSS + brand colours
│   │   ├── helpers.py            # Reusable widgets & helpers
│   │   └── widgets/
│   │       ├── payment_row.py    # Payment method entry row
│   │       └── summary_card.py   # Summary display cards
│   ├── sync/
│   │   ├── base.py               # SyncResult dataclass
│   │   ├── manager.py            # SyncManager orchestration
│   │   ├── gsheets.py            # Google Sheets backend via gspread
│   │   ├── data_collector.py     # Collects report data for sync
│   │   └── worker.py             # QThread worker for background sync
│   ├── update/
│   │   ├── checker.py            # GitHub API, version comparison, download, batch script
│   │   └── worker.py             # QThread workers for check + download
│   └── utils/
│       ├── app_settings.py       # Market code, device ID, sync/update settings, key-value store
│       ├── calculations.py       # Core financial math
│       ├── export.py             # CSV export + ledger backup
│       └── logging_config.py     # Rotating file logger
├── tests/
│   ├── test_match_formula.py     # 68 tests — formula validation
│   ├── test_match_limit.py       # 18 tests — daily cap logic
│   ├── test_returning_customer.py # 21 tests — multi-visit tracking
│   ├── test_adjustments.py       # 105 tests — adjustments, voids, ledger
│   ├── test_fmnp_reports.py      # 42 tests — FMNP entries and reports
│   ├── test_models.py            # 37 tests — model CRUD operations
│   ├── test_market_code.py       # 44 tests — market code, device ID
│   ├── test_backup.py            # 12 tests — backup creation + retention
│   ├── test_schema.py            # 30 tests — migrations, triggers, indexes
│   ├── test_settings_io.py       # 102 tests — import/export round-trip
│   ├── test_sync.py              # 90 tests — cloud sync, data collection, Google Sheets
│   └── test_update.py            # 77 tests — URL parsing, version comparison, update flow
├── releases/
│   └── FAM_Manager_v1.7.0.zip   # Distribution package
├── requirements.txt
├── fam_manager.spec              # PyInstaller config
├── build.bat                     # Windows build script
└── PROJECT_INSTRUCTIONS.md       # ← This file
```

---

## 3. Critical Business Logic — Match Formula

The single most important calculation in the application. **Get this wrong and
every dollar amount in the system is incorrect.**

### Formula

```
match_amount = method_amount × match_percent / (100 + match_percent)
customer_charged = method_amount − match_amount
```

### Semantics

| Match % | Meaning     | $100 order: FAM pays | $100 order: Customer pays |
|---------|-------------|----------------------|---------------------------|
| 0%      | No match    | $0.00                | $100.00                   |
| 25%     | 1:4         | $20.00               | $80.00                    |
| 50%     | 1:2         | $33.33               | $66.67                    |
| 100%    | 1:1         | $50.00               | $50.00                    |
| 200%    | 2:1         | $66.67               | $33.33                    |
| 300%    | 3:1         | $75.00               | $25.00                    |

**Key properties:**
- `match_amount + customer_charged == method_amount` (always, per line item)
- `customer_total_paid + fam_subsidy_total == receipt_total` (when fully allocated)
- `customer_charged >= 0` for all valid inputs (match never exceeds receipt)
- Rounding: Python `round(x, 2)` — banker's rounding to the penny

### Where the formula lives (4 locations — must stay in sync)

| File                           | Function / Line                  | Purpose                    |
|--------------------------------|----------------------------------|----------------------------|
| `fam/utils/calculations.py`    | `calculate_payment_breakdown()`  | Canonical calculation      |
| `fam/ui/widgets/payment_row.py`| `_recompute()`                   | Live UI preview            |
| `fam/ui/widgets/payment_row.py`| `get_data()`                     | Data collection for save   |
| `fam/ui/payment_screen.py`     | `_distribute_and_save_payments()`| Multi-receipt distribution |

### Daily match limit (cap)

Markets can set a per-customer daily FAM match cap (e.g. $100/day).
When a customer's total match exceeds the cap, all match amounts are
**proportionally reduced** so the total equals the cap exactly.

---

## 4. Database Schema (v11)

### Tables

**markets** — name, address, daily_match_limit, match_limit_active, is_active

**vendors** — name, contact_info, is_active

**market_vendors** — junction table (market_id, vendor_id)

**payment_methods** — name, match_percent (0–999), sort_order, is_active

**market_payment_methods** — junction table (market_id, payment_method_id)

**market_days** — market_id, date, status (Open/Closed), opened_by, closed_by

**customer_orders** — market_day_id, customer_label, zip_code, status

**transactions** — fam_transaction_id (FAM-{CODE}-YYYYMMDD-NNNN), market_day_id, vendor_id, receipt_total, status, customer_order_id

**payment_line_items** — transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot, method_amount, match_amount, customer_charged

**fmnp_entries** — market_day_id, vendor_id, amount, check_count, status (Active/Inactive)

**audit_log** — table_name, record_id, action, field_name, old_value, new_value, reason_code, notes, changed_by

**app_settings** — key-value store (market_code, device_id, tutorial_shown, large_receipt_threshold, sync_credentials_loaded, sync_spreadsheet_id, last_sync_at, last_sync_error, update_repo_url, update_auto_check, update_last_check, update_last_version, update_dismissed_version)

**schema_version** — version (current: 11), applied_at

### Migration History

| Version | Change |
|---------|--------|
| v1→v2 | Added customer_orders table |
| v2→v3 | Added market_vendors junction table |
| v3→v4 | Added validation triggers + performance indexes |
| v4→v5 | Added daily_match_limit columns to markets |
| v5→v6 | Renamed discount → match; expanded match_percent to 0–999 |
| v6→v7 | Added zip_code to customer_orders |
| v7→v8 | Added FMNP payment method (100% match) |
| v8→v9 | Added market_payment_methods junction table |
| v9→v10 | Added app_settings key-value table |
| v10→v11 | Added status column to fmnp_entries for soft-delete |

---

## 5. Multi-Market Device Identity

### Market Code
- 1–4 uppercase alpha chars, auto-derived from market name on open/reopen
- Multi-word names: first letter of each word (e.g., "Bethel Park Farmers Market" → `BPFM`)
- Single-word names: first 2 alpha characters
- Stored in `app_settings` table (key: `market_code`)
- Embedded in: transaction IDs, CSV filenames, ledger header, receipt printouts, title bar

### Device ID
- Windows `MachineGuid` from `HKLM\SOFTWARE\Microsoft\Cryptography`
- Fallback: `hostname-{platform.node()}`
- Captured on every app launch, stored in `app_settings` (key: `device_id`)
- Appears in: CSV export columns, ledger header, settings export header

### CSV Identity Columns
All CSV exports inject `market_code` and `device_id` as the first two columns.

---

## 6. Backup System

### Database Backups (`fam/database/backup.py`)
- SQLite backup API for consistent hot copies (works with WAL mode)
- Stored in `{data_dir}/backups/`
- Retention: 20 most recent files, older auto-deleted
- Created on: market open, market close, every 5 minutes (auto timer)
- Never raises — all errors logged silently

### Ledger Backup (`fam/utils/export.py`)
- Single file: `fam_ledger_backup.txt`
- Contains all transactions from entire database
- Atomic write: tempfile + os.replace
- Updated after: payment confirmation, adjustment, void, market-day close

---

## 7. First-Run Experience

1. App starts with clean slate (no pre-loaded data)
2. Tutorial auto-launches on first run (11 steps)
3. Final step: "Quick Setup" with Yes/No buttons
4. "Yes" calls `seed_sample_data()` → loads 3 markets, 8 vendors, 6 payment methods
5. "No" → blank app, user configures manually or imports `.fam` file
6. `tutorial_shown` flag persisted in `app_settings` to prevent re-launch
7. Tutorial can be re-run anytime via "Start Tutorial" button

---

## 8. Application Screens

### Screen 0 — Market Day Setup
- Select market, enter volunteer name, open/close/reopen market day
- Auto-derives market code from market name on open
- Signal: `market_day_changed`

### Screen 1 — Receipt Intake
- New/returning customer, vendor dropdown, receipt total, zip code
- Multiple receipts per customer, pending orders
- Signal: `customer_order_ready(int)`

### Screen 2 — Payment Processing
- Summary cards, dynamic payment rows, daily match limit display
- Confirm payment → generates transaction IDs (FAM-{CODE}-YYYYMMDD-NNNN)
- Print receipt after confirmation
- Double-click protection on confirm button
- Signals: `payment_confirmed()`, `draft_saved()`

### Screen 3 — FMNP Entry
- Market day + vendor + amount + check count
- Edit/delete with soft-delete (status: Active/Inactive)

### Screen 4 — Admin Adjustments
- Search/filter transactions, adjust amounts/vendors, void
- Audit log with reason codes

### Screen 5 — Reports
- Summary, Detailed Ledger, Vendor Reimbursement, FAM Match, Geolocation, Activity Log, Error Log
- CSV export with market code in filenames and identity columns

### Screen 6 — Settings
- Markets, Vendors, Payment Methods, Preferences tabs
- Import/Export `.fam` settings files with preview dialog
- Device Identity display (read-only market code + device ID)
- Cloud Sync tab — One-way sync to Google Sheets (credentials, spreadsheet ID, sync now)
- Updates tab — GitHub repo URL, check for updates, download & install, auto-check toggle

---

## 9. Test Suite

**Run:** `python -m pytest tests/ -v` from project root

**618 total tests across 13 files** — all must pass before committing.

---

## 10. Build & Deployment

### Development
```bash
pip install -r requirements.txt
python run.py
```

### Windows Executable
```bash
build.bat
# Output: dist/FAM Manager/FAM Manager.exe
```

### Data Persistence
All data stored in `%APPDATA%\FAM Market Manager\` (separate from exe):
- `fam_data.db` — SQLite database (all app data)
- `fam_ledger_backup.txt` — human-readable ledger backup
- `fam_manager.log` — rotating log file
- `sync_credentials.json` — Google Sheets credentials (if configured)
- `backups/` — automatic database backups (20 most recent)
- `_update_backup/` — previous app version (created during auto-update)

Upgrading: use in-app auto-update or manually replace app folder. Schema migrations run automatically.
Legacy data (v1.5.1 and earlier) auto-migrated from exe directory on first launch.

---

## 11. Version History

| Version | Date       | Summary |
|---------|------------|---------|
| v1.7.0  | 2026-03-09 | Google Sheets cloud sync, auto-update from GitHub Releases, sync/update packages, 618 tests |
| v1.6.1  | 2026-03-06 | Tutorial auto-configure, market code/device ID, receipt printing, settings import/export, database backups, ledger backup, data dir migration, global exception handler, 479 tests |
| v1.5.1  | 2026-03-04 | First-run tutorial, single-instance prevention, PyInstaller fix |
| v1.5.0  | 2026-03-03 | Interactive tutorial overlay, production-readiness improvements |
| v1.4.1  | 2026-03-02 | Custom FAM logo and window icon |
| v1.4.0  | 2026-03-01 | Reports & charts, ledger backup, real seed data |
| v1.3.0  | 2026-02-28 | FMNP payment integration, UI density optimization |
| v1.2.0  | 2026-02-28 | UI polish — row heights, button styles, chart scaling |
| v1.0    | 2026-02-26 | Initial release |

---

## 12. Cloud Sync (Google Sheets)

### Architecture
Optional one-way sync from local SQLite to a shared Google Spreadsheet.

| Module | Purpose |
|--------|---------|
| `sync/base.py` | `SyncResult` dataclass |
| `sync/data_collector.py` | Queries DB for summary, vendor, payment, transaction data |
| `sync/gsheets.py` | Google Sheets backend via `gspread` (service account auth) |
| `sync/manager.py` | `SyncManager` — orchestrates data collection + backend calls |
| `sync/worker.py` | `SyncWorker(QObject)` — runs sync in background QThread |

### Credentials
- Service account JSON stored at `{data_dir}/sync_credentials.json`
- Spreadsheet ID in `app_settings` (key: `sync_spreadsheet_id`)
- Credentials loaded flag in `app_settings` (key: `sync_credentials_loaded`)

---

## 13. Auto-Update (GitHub Releases)

### Architecture
Self-update via GitHub Releases API. Uses `urllib.request` (stdlib — no new dependencies).

| Module | Purpose |
|--------|---------|
| `update/checker.py` | URL parsing, version comparison, GitHub API, download, batch script generation |
| `update/worker.py` | `UpdateCheckWorker` + `UpdateDownloadWorker` (QThread workers) |

### Key functions in `checker.py`
- `parse_github_repo_url(url)` — validates GitHub URL, extracts (owner, repo)
- `compare_versions(current, remote)` — semantic version comparison (-1/0/1)
- `check_for_update(owner, repo, version)` — calls GitHub API, returns release info
- `download_update(url, dest, callback)` — downloads in 64KB chunks with progress
- `verify_download(path, expected_size)` — file size verification
- `generate_update_script(app_dir, zip)` — writes `_fam_update.bat` to AppData

### Update flow
1. App checks GitHub on launch (rate-limited to once/24h) or user clicks "Check for Updates"
2. If update found, user clicks "Download & Install" (blocked while market day open)
3. Download verified against GitHub-reported file size
4. Batch script generated: waits for exe exit → backs up app dir → extracts zip → copies → relaunches
5. Previous version backed up at `{data_dir}/_update_backup/`

### Safety
- Running exe can't replace itself → batch script waits for exit
- Download size verified against GitHub API
- Full backup of previous version before overwrite
- Install blocked while market day is open
- Dev mode: install button disabled with message
- Default repo URL: `DEFAULT_REPO_URL` constant in `app_settings.py`

---

## 14. Future Milestones

- **Code signing certificate** — Eliminate Windows SmartScreen warning
- **Multi-language support** — Spanish/English toggle for volunteers
