# FAM Market Manager — Technical Overview

> **Version:** 1.9.6
> **Last Updated:** April 2026
> **Audience:** Developers, administrators, and stakeholders

---

## 1. System Purpose

FAM Market Manager is a desktop point-of-sale and back-office application for farmers markets participating in the **Food Assistance Match (FAM)** program. It enables volunteers to:

- Open and close market days
- Record customer receipts by vendor
- Calculate FAM matching subsidies per payment method
- Process multi-method payments with daily match caps
- Track FMNP (Farmers Market Nutrition Program) check entries with multi-photo attachments
- Print customer receipts
- Generate reports, charts, and data exports
- Adjust or void transactions with a full audit trail
- Manage markets, vendors, and payment method configuration (including denomination constraints and photo requirements)
- Import/export settings across devices via `.fam` files
- One-way sync of reports to Google Sheets for remote viewing
- Upload FMNP and payment photos to Google Drive with content-based deduplication
- Check for and install application updates from GitHub Releases

The application runs as a standalone Windows desktop executable with local SQLite storage. Internet connectivity is optional — required only for cloud sync, photo upload, and auto-update features.

---

## 2. High-Level Architecture

```
┌──────────────────────────────────────────────────┐
│                   run.py                         │
│              (Console Entry Point)               │
└────────────────────┬─────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────┐
│                  fam/app.py                      │
│   - Resolves data directory (%APPDATA%)          │
│   - Migrates legacy data from exe directory      │
│   - Initializes logging + database               │
│   - Captures device ID                           │
│   - Creates QApplication + MainWindow            │
│   - Global exception handler                     │
└────────────────────┬─────────────────────────────┘
                     │
        ┌────────────┼─────────────┐
        ▼            ▼             ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│ database │  │  models   │  │   utils  │
│ layer    │  │  layer    │  │  layer   │
├──────────┤  ├──────────┤  ├──────────┤
│connection│  │ vendor   │  │calculat. │
│ schema   │  │market_day│  │ export   │
│ seed     │  │payment_m.│  │ logging  │
│ backup   │  │transact. │  │app_sett. │
│          │  │cust_order│  │photo_stor│
│          │  │ fmnp     │  │photo_path│
│          │  │ audit    │  │          │
│          │  │photo_hash│  │          │
└──────────┘  └──────────┘  └──────────┘
        ▲            ▲             ▲
        └────────────┼─────────────┘
                     │
        ┌────────────┼─────────────┐
        ▼            │             ▼
┌──────────┐         │      ┌──────────┐
│   sync   │         │      │  update  │
├──────────┤         │      ├──────────┤
│ gsheets  │         │      │ checker  │
│ manager  │         │      │ worker   │
│data_coll.│         │      └──────────┘
│ worker   │         │
│ drive    │         │
│ base     │         │
└──────────┘         │
                     ▼
┌──────────────────────────────────────────────────┐
│                   fam/ui/                        │
│                                                  │
│   MainWindow (QMainWindow)                       │
│   ├── _PatternSidebar (240px, 7 nav buttons)    │
│   └── Content Area                               │
│       ├── Header Bar (Start Tutorial button)     │
│       └── QStackedWidget (7 screens)             │
│           ├── 0: MarketDayScreen                 │
│           ├── 1: ReceiptIntakeScreen             │
│           ├── 2: PaymentScreen                   │
│           ├── 3: FMNPScreen                      │
│           ├── 4: AdminScreen                     │
│           ├── 5: ReportsScreen                   │
│           └── 6: SettingsScreen                  │
│                                                  │
│   Shared: styles.py, helpers.py, widgets/        │
│   Tutorial: tutorial_overlay.py                  │
│   Settings I/O: settings_io.py                   │
└──────────────────────────────────────────────────┘
```

---

## 3. Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Language | Python 3.12 | Application runtime |
| GUI Framework | PySide6 (Qt6) | Desktop user interface |
| Database | SQLite (WAL mode) | Local data persistence |
| Charts | Matplotlib (QtAgg backend) | Report visualizations |
| Data Export | Pandas | CSV file generation |
| Geolocation | Folium + pgeocode | Zip code heat maps |
| Cloud Sync | gspread + google-auth | Google Sheets integration |
| Photo Upload | google-auth (AuthorizedSession) | Google Drive REST API |
| Auto-Update | urllib.request (stdlib) | GitHub Releases API |
| Packaging | PyInstaller | Standalone Windows executable |
| Testing | pytest + pytest-qt | Unit, integration, and automated UI tests (1547 tests) |

**Runtime Dependencies** (`requirements.txt`):
- `PySide6 >= 6.5.0`
- `pandas >= 2.0.0`
- `matplotlib >= 3.7.0`
- `folium >= 0.14.0`
- `pgeocode >= 0.4.0`
- `gspread >= 6.0.0`
- `google-auth >= 2.20.0`

---

## 4. Repository Layout

```
fam-market-manager/
├── run.py                      # Console entry point
├── fam/
│   ├── __init__.py             # Package init, __version__
│   ├── app.py                  # QApplication init, data dir, exception handler
│   ├── settings_io.py          # .fam file import/export
│   ├── database/
│   │   ├── connection.py       # Thread-local SQLite connections
│   │   ├── schema.py           # Table creation + migrations (v1–v22)
│   │   ├── seed.py             # Sample data (opt-in via tutorial)
│   │   └── backup.py           # SQLite backup API + retention
│   ├── models/
│   │   ├── vendor.py           # Vendor CRUD + market assignments + registration fields
│   │   ├── market_day.py       # Market day open/close/reopen
│   │   ├── payment_method.py   # Payment method CRUD + market assignments + denomination/photo config
│   │   ├── transaction.py      # Transaction lifecycle + payment line items + payment photo queries
│   │   ├── customer_order.py   # Customer order grouping + returning customers
│   │   ├── fmnp.py             # FMNP check entry CRUD + photo queries
│   │   ├── audit.py            # Append-only audit log (app_version + device_id)
│   │   └── photo_hash.py       # Content hash lookups: Drive dedup (photo_hashes) + local dedup (local_photo_hashes)
│   ├── ui/
│   │   ├── main_window.py      # MainWindow + sidebar + tutorial + backup timer + auto-update check
│   │   ├── market_day_screen.py
│   │   ├── receipt_intake_screen.py
│   │   ├── payment_screen.py   # Includes receipt printing + charge-based denomination input + receipt photos
│   │   ├── fmnp_screen.py      # Multi-photo FMNP with dynamic slots and scrollable container
│   │   ├── admin_screen.py
│   │   ├── reports_screen.py   # Resizable report table columns (auto-fit to content)
│   │   ├── settings_screen.py  # Includes ImportPreviewDialog, Cloud Sync, Updates tabs
│   │   ├── tutorial_overlay.py # Guided tutorial + auto-configure prompt
│   │   ├── styles.py           # Color palette + global stylesheet
│   │   ├── helpers.py          # Shared widgets + table utilities
│   │   └── widgets/
│   │       ├── payment_row.py  # Payment method entry widget + denomination validation
│   │       └── summary_card.py # Metric display cards
│   ├── sync/
│   │   ├── base.py             # SyncResult dataclass + SyncBackend ABC
│   │   ├── manager.py          # SyncManager orchestration + Agent Tracker
│   │   ├── gsheets.py          # Google Sheets backend using gspread with service account auth
│   │   ├── data_collector.py   # Collects report data for sync (worker thread); FMNP from both tables
│   │   ├── drive.py            # Google Drive photo upload via REST API (retry, folder hierarchy, dead URL detection)
│   │   └── worker.py           # SyncWorker(QObject) — data collection + photo upload + sheet sync in background QThread
│   ├── update/
│   │   ├── checker.py          # GitHub API, version comparison, download, batch script
│   │   └── worker.py           # QThread workers for check + download
│   └── utils/
│       ├── app_settings.py     # Market code, device ID, sync/update settings, key-value store
│       ├── calculations.py     # Match formula + payment breakdown + penny reconciliation
│       ├── money.py            # Integer-cents helpers: dollars_to_cents, cents_to_dollars, format_dollars
│       ├── export.py           # CSV export + ledger backup
│       ├── logging_config.py   # Rotating file logger
│       ├── log_reader.py       # Log file parser for Error Log sync tab
│       ├── photo_storage.py    # Photo storage in {data_dir}/photos/ with SHA-256 hashing + resize
│       └── photo_paths.py      # JSON encode/decode for multi-photo pipe-separated paths
├── tests/
│   ├── test_match_formula.py       # 98 tests — core formula verification
│   ├── test_match_limit.py         # 28 tests — daily cap logic
│   ├── test_returning_customer.py  # 23 tests + DB integration
│   ├── test_adjustments.py         # 71 tests — adjustments, voids, ledger
│   ├── test_fmnp_reports.py        # 38 tests — FMNP entries and reports
│   ├── test_models.py              # 130 tests — model CRUD operations, photo queries
│   ├── test_market_code.py         # 44 tests — market code, device ID, exports
│   ├── test_backup.py              # 21 tests — backup creation + retention
│   ├── test_schema.py              # 40 tests — migrations v1–v21, triggers, indexes
│   ├── test_settings_io.py         # 54 tests — import/export round-trip
│   ├── test_sync.py                # 124 tests — cloud sync, data collection, Google Sheets
│   ├── test_update.py              # 122 tests — URL parsing, version comparison, update flow, zip probe, runtime script execution, path safety, PowerShell escaping, pending-update verification
│   ├── test_denomination.py        # 43 tests — denomination constraints, charge conversion
│   ├── test_charge_conversion.py   # 52 tests — charge-to-amount conversion edge cases
│   ├── test_auto_distribute.py     # 71 tests — multi-receipt payment distribution, max-cap math
│   ├── test_multi_photo.py         # 112 tests — multi-photo storage, dedup, Drive upload
│   ├── test_cloud_sync_ux.py       # 151 tests — sync UX flows, Drive integration, Agent Tracker
│   ├── test_money_boundaries.py    # 63 tests — integer-cents boundaries, float accumulation, FMNP check splitting, penny reconciliation
│   ├── test_reconciliation.py      # 25 tests — three-way reconciliation (DB == Ledger == Sheets)
│   ├── test_ui_payment.py          # 37 tests — automated UI: PaymentScreen widget behavior
│   ├── test_ui_workflows.py        # 31 tests — end-to-end market day simulation, cap workflows
│   ├── test_ui_guards.py           # 66 tests — max-cap clamping, lifecycle guards, match-cap-aware charge
│   ├── test_ui_expanded.py         # 51 tests — production readiness E2E: payment pipelines, void exclusion, reconciliation
│   ├── test_payment_method_safety.py # 23 tests — payment method CRUD safety, deactivation guards
│   └── conftest.py / __init__.py
├── releases/
│   └── FAM_Manager_v1.9.6.zip # Distribution package
├── fam_manager.spec            # PyInstaller build configuration
├── build.bat                   # Windows build script
├── requirements.txt
└── README.md
```

---

## 5. Database Design

### 5.1 Connection Management

SQLite connections are **thread-local** via `threading.local()`. Each thread lazily initializes its own connection with:

- `row_factory = sqlite3.Row` (column-name access)
- `PRAGMA journal_mode=WAL` (concurrent reads during writes)
- `PRAGMA foreign_keys=ON` (referential integrity enforced)

The database file (`fam_data.db`) is stored in `%APPDATA%\FAM Market Manager\` in production, or in the project root during development. This separation ensures application upgrades never affect user data.

### 5.2 Schema (Version 22)

**Core Tables:**

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `markets` | Market locations | name, address, daily_match_limit, match_limit_active |
| `vendors` | Vendor businesses | name, contact_info, is_active, check_payable_to, street, city, state, zip_code, ach_enabled |
| `payment_methods` | Payment types with match rates | name, match_percent (0–999), sort_order, denomination, photo_required |
| `market_days` | Daily market sessions | market_id, date, status (Open/Closed), opened_by, closed_by |
| `customer_orders` | Groups receipts per customer visit | market_day_id, customer_label (C-001), zip_code, status |
| `transactions` | Individual vendor receipts | fam_transaction_id, vendor_id, receipt_total, customer_order_id, status |
| `payment_line_items` | Payment breakdown per receipt | transaction_id, method_amount, match_amount, customer_charged, photo_path, photo_drive_url |
| `fmnp_entries` | FMNP check records | market_day_id, vendor_id, amount, check_count, status, photo_path, photo_drive_url |
| `audit_log` | Append-only change history | table_name, record_id, action, old_value, new_value, changed_by, app_version, device_id |
| `app_settings` | Key-value configuration store | key, value (market_code, device_id, tutorial_shown, sync_*, update_*, drive_*, etc.) |
| `photo_hashes` | Content hash to Drive URL mapping | content_hash (PK), drive_url, created_at |
| `local_photo_hashes` | Content hash to local path mapping | content_hash (PK), relative_path, created_at |

**Junction Tables:**

| Table | Relationship |
|-------|-------------|
| `market_vendors` | Which vendors serve which markets |
| `market_payment_methods` | Which payment methods each market accepts |

### 5.3 Key Design Decisions

**Snapshot columns:** `payment_line_items` stores `method_name_snapshot` and `match_percent_snapshot` at the time of payment confirmation. This ensures historical records remain accurate even if payment method settings are later changed.

**Soft deletes:** Transactions, customer orders, and FMNP entries use a `status` field rather than physical deletion. Voided/inactive records are preserved for audit purposes.

**Transaction IDs:** Human-readable format `FAM-{CODE}-YYYYMMDD-NNNN` with market code and sequential numbering per date. Example: `FAM-BPFM-20260306-0005`. Backward compatible with older `FAM-YYYYMMDD-NNNN` format.

**Customer labels:** Sequential per market day (`C-001`, `C-002`, ...) designed to match paper receipt numbering. Returning customers reuse their original label for additional orders within the same market day.

**Denomination constraints:** Payment methods can define a `denomination` value (e.g., $5.00 for FMNP checks). When set, the UI enforces that payment amounts are exact multiples of the denomination.

**Photo paths:** Multi-photo support uses JSON-encoded arrays in TEXT columns (e.g., `["photos/a.jpg", "photos/b.jpg"]`). Legacy single-path strings are handled transparently by `photo_paths.py`.

### 5.4 Migrations

Schema migrations run automatically on startup. Each migration is guarded by a try/except. The `schema_version` table tracks the current version. A pre-migration backup (`.pre-migration.bak`) is created before any structural changes.

| Version | Change |
|---------|--------|
| v1-v2 | Added customer_orders table + customer_order_id to transactions |
| v2-v3 | Added market_vendors junction table |
| v3-v4 | Added validation triggers + performance indexes |
| v4-v5 | Added daily_match_limit columns to markets |
| v5-v6 | Renamed discount columns to match columns; expanded range to 0-999 |
| v6-v7 | Added zip_code to customer_orders |
| v7-v8 | Added FMNP payment method (100% match) |
| v8-v9 | Added market_payment_methods junction table |
| v9-v10 | Added app_settings key-value table |
| v10-v11 | Added status column to fmnp_entries for soft-delete |
| v11-v12 | Added denomination column to payment_methods |
| v12-v13 | Added photo_path + photo_drive_url to fmnp_entries |
| v13-v14 | Added photo_required to payment_methods + photo_path to payment_line_items |
| v14-v15 | Added photo_drive_url to payment_line_items |
| v15-v16 | Added app_version + device_id to audit_log |
| v16-v17 | Added photo_hashes table (content hash to Drive URL dedup) |
| v17-v18 | Added local_photo_hashes table + backfill of existing photos |
| v18-v19 | Added vendor registration fields (check_payable_to, address, ach_enabled) |
| v19-v20 | Added FK indexes (transactions.vendor_id, customer_orders.market_day_id, fmnp_entries.vendor_id, payment_line_items.payment_method_id) |
| v20-v21 | Added indexes: transactions(customer_order_id), market_days(market_id, date), audit_log(table_name, record_id) |
| v21-v22 | Converted all monetary REAL columns to INTEGER cents (markets.daily_match_limit, payment_methods.denomination, transactions.receipt_total, payment_line_items.method_amount/match_amount/customer_charged, fmnp_entries.amount) |

### 5.5 Database Triggers

Check constraints enforced via `BEFORE INSERT` and `BEFORE UPDATE` triggers:

- `transactions.receipt_total > 0`
- `payment_line_items.method_amount >= 0`
- `payment_line_items.match_amount >= 0`
- `fmnp_entries.amount > 0`
- `payment_methods.match_percent BETWEEN 0 AND 999`

### 5.6 Indexes

Performance indexes on frequently queried columns:

- `idx_transactions_market_day` -- transactions by market day
- `idx_transactions_status` -- transactions by status
- `idx_transactions_fam_id` -- transaction ID lookups
- `idx_transactions_vendor` -- transactions by vendor (v20)
- `idx_transactions_customer_order` -- transactions by customer order (v21)
- `idx_payment_items_txn` -- payment items by transaction
- `idx_payment_items_method` -- payment items by payment method (v20)
- `idx_fmnp_market_day` -- FMNP entries by market day
- `idx_fmnp_entries_vendor` -- FMNP entries by vendor (v20)
- `idx_customer_orders_market_day` -- customer orders by market day (v20)
- `idx_market_days_market_date` -- market days by (market_id, date) composite (v21)
- `idx_audit_log_changed_at` -- audit log chronological queries
- `idx_audit_log_table_record` -- audit log by (table_name, record_id) composite (v21)

---

## 6. Multi-Market Device Identity

### 6.1 Market Code

A 1-4 character uppercase code auto-derived from the market name when a market day is opened:
- Multi-word: first letter of each word (e.g., "Bethel Park Farmers Market" -> `BPFM`)
- Single word: first 2 alpha characters

The code is embedded in transaction IDs, CSV export filenames, ledger headers, receipt printouts, and the title bar.

### 6.2 Device ID

The Windows `MachineGuid` is captured from `HKLM\SOFTWARE\Microsoft\Cryptography` on first launch and stored in `app_settings`. Falls back to `hostname-{platform.node()}` if registry access fails. Appears in CSV exports, ledger headers, audit log entries, and the Agent Tracker sync tab.

### 6.3 CSV Export Identity Columns

All CSV exports inject `market_code` and `device_id` as the first two columns, allowing the finance team to consolidate reports from multiple markets/devices.

---

## 7. Backup System

### 7.1 Database Backups

- **Method:** SQLite backup API (`sqlite3.backup()`) for consistent hot copies
- **Storage:** `{data_dir}/backups/` subdirectory
- **Naming:** `fam_{code}_backup_{YYYYMMDD_HHMMSS}_{reason}.db`
- **Triggers:** Market open, market close, every 5 minutes during active market day
- **Retention:** 20 most recent backups; older files auto-deleted
- **Safety:** Never raises exceptions -- all errors logged silently

### 7.2 Ledger Backup

- **File:** `fam_ledger_backup.txt` (single file, always overwritten)
- **Content:** Human-readable summary of ALL transactions from the entire database
- **Scope:** All market days, grouped by market -> date -> transaction
- **Triggers:** After every payment confirmation, adjustment, void, and market-day close
- **Write method:** Atomic (tempfile + os.replace) to prevent corruption
- **Fallback:** Timestamped file if the primary file is locked (e.g., open in Notepad)

---

## 8. Cloud Sync (Google Sheets) and Photo Upload (Google Drive)

### 8.1 Architecture

The `fam/sync/` package provides optional one-way sync from local SQLite to Google Sheets, plus photo upload to Google Drive.

```
Settings -> Load Credentials -> sync_credentials.json stored in AppData
Settings -> Save Spreadsheet ID -> app_settings table
Settings -> Save Drive Folder ID -> app_settings table

User clicks "Sync Now" -> SyncWorker (QThread)
    -> Step 0: DataCollector gathers report data from SQLite (worker thread)
    -> Step 1: drive.py uploads pending photos to Google Drive
         -> Verify existing Drive URLs (dead URL detection)
         -> Rename voided/deleted photos to VOID_ prefix
         -> Upload new photos with 3-layer dedup + retry
         -> Re-collect sync data so fresh Drive URLs appear in sheets
    -> Step 2: SyncManager orchestrates sheet-by-sheet upload
         -> GSheetsSyncBackend writes via gspread API
         -> Agent Tracker row updated with device metadata
    -> SyncResult per sheet returned to UI
```

### 8.2 Components

| Module | Purpose |
|--------|---------|
| `sync/base.py` | `SyncResult` dataclass (rows_synced, status, error) + `SyncBackend` ABC |
| `sync/data_collector.py` | Queries database for all sync tabs; FMNP entries collected from both `fmnp_entries` table and `payment_line_items` (FMNP method), expanded to per-check rows with individual photo links |
| `sync/gsheets.py` | Google Sheets backend using `gspread` with service account auth |
| `sync/drive.py` | Google Drive photo upload via REST API (AuthorizedSession); retry with exponential backoff (429/500/502/503); organized folder hierarchy (Root > Market > Payment Type); dead URL detection and clearance; inherits parent folder permissions |
| `sync/manager.py` | `SyncManager` -- orchestrates data collection + backend calls + Agent Tracker per-device metadata row |
| `sync/worker.py` | `SyncWorker(QObject)` -- runs data collection, photo upload, and sheet sync in background QThread |

### 8.3 Credentials and Configuration

- **Service account JSON** stored at `{data_dir}/sync_credentials.json`
- **Spreadsheet ID** stored in `app_settings` (key: `sync_spreadsheet_id`)
- **Drive photos folder ID** stored in `app_settings` (key: `drive_photos_folder_id`)
- **Credentials loaded flag** in `app_settings` (key: `sync_credentials_loaded`)
- **Last sync timestamp** in `app_settings` (key: `last_sync_at`)
- **Last sync error** in `app_settings` (key: `last_sync_error`)

### 8.4 Data Sheets

The sync writes multiple sheets to the target spreadsheet:

| Sheet | Content |
|-------|---------|
| Vendor Reimbursement | Per-vendor receipt totals, FAM subsidy breakdown, FMNP external, check payable info, address |
| FAM Match Report | Per-method totals and match amounts per market day |
| Detailed Ledger | Full transaction ledger with payment line items and photo URLs |
| Transaction Log | Audit trail of transaction-level actions with app version |
| Activity Log | Full audit log scoped by market day date range |
| Geolocation | Customer zip code distribution per market day |
| FMNP Entries | Per-check FMNP rows from both fmnp_entries and payment flow, with individual photo links |
| Market Day Summary | Aggregate totals per market day (transactions, receipts, customer paid, FAM match) |
| Error Log | Application errors and warnings parsed from the log file |
| Agent Tracker | One row per device with metadata (app version, hostname, OS, sync status, row counts) |

### 8.5 Photo Upload (Google Drive)

Photos are uploaded to Google Drive via the REST API v3, reusing the same service account credentials as Google Sheets sync. No additional dependencies are required beyond `google-auth`.

**Folder hierarchy:** Photos are organized as `Root Folder > Market Name > Payment Type` (e.g., `FAM Market Manager Photos > Bethel Park Farmers Market > FMNP`).

**Upload flow:**
1. Verify existing Drive URLs; clear any pointing to deleted or trashed files (dead URL detection)
2. Rename Drive files for voided/deleted entries to `VOID_` prefix
3. Upload pending photos with resumable upload and retry (exponential backoff for 429/500/502/503)
4. Post-upload verification confirms each file exists in Drive

**3-layer deduplication:**
1. **Within-entry (hard block):** Same photo cannot be attached to the same entry twice
2. **Cross-transaction (warning):** Local content hash registry (`local_photo_hashes`) detects reuse of the same image across different transactions at attachment time
3. **Drive upload (silent reuse):** Content hash to Drive URL cache (`photo_hashes`) allows the sync to skip uploading identical content, reusing the existing Drive URL

**Permissions:** Uploaded files inherit the parent folder's sharing permissions. No public sharing links are created.

---

## 9. Photo System

### 9.1 Local Storage

Photos are stored in `{data_dir}/photos/` with filenames following the pattern `{prefix}_{entry_id}_{timestamp}.{ext}`. The prefix distinguishes photo types: `fmnp` for FMNP check photos, `pay` for payment receipt photos.

Large images are automatically resized to a maximum of 1920px on the longest side using `QPixmap`, with JPEG quality at 85%.

### 9.2 Content Hashing

SHA-256 hashes are computed for every photo at storage time (reading in 64 KB chunks). The hash of the original source file is recorded in `local_photo_hashes` so that duplicate detection works against the user-selected file, not the potentially resized stored copy.

### 9.3 Path Encoding

`photo_paths.py` handles JSON encode/decode for multi-photo storage. New entries store JSON arrays (e.g., `["photos/a.jpg", "photos/b.jpg"]`). Legacy single-path strings are parsed transparently as one-element lists.

---

## 10. Auto-Update System

### 10.1 Architecture

The `fam/update/` package provides self-update capability via GitHub Releases.

```
Launch -> 5s timer -> _auto_check_for_updates()
    -> Rate limit check (once per 24h)
    -> UpdateCheckWorker -> GET /repos/{owner}/{repo}/releases/latest
    -> If update available + not dismissed -> notification dialog

Settings -> Updates tab -> "Check for Updates"
    -> UpdateCheckWorker -> GitHub API -> UI shows version info

Settings -> "Download & Install"
    -> Safety checks (frozen mode, market day not open)
    -> UpdateDownloadWorker -> downloads .zip to AppData
    -> verify_download() -> file size check
    -> generate_update_script() -> writes _fam_update.bat
    -> subprocess.Popen(.bat) -> QApplication.quit()

Batch script:
    -> Waits for exe to exit (30s timeout)
    -> Backs up current app dir to AppData\_update_backup\
    -> PowerShell Expand-Archive -> copies over app dir
    -> Relaunches FAM Manager.exe -> self-deletes
```

### 10.2 Components

| Module | Purpose |
|--------|---------|
| `update/checker.py` | URL parsing, version comparison, GitHub API, download, script generation |
| `update/worker.py` | `UpdateCheckWorker` + `UpdateDownloadWorker` (QThread workers) |

### 10.3 Key Functions in `checker.py`

| Function | Purpose |
|----------|---------|
| `parse_github_repo_url(url)` | Validates GitHub URL, extracts (owner, repo) |
| `compare_versions(current, remote)` | Semantic version comparison (-1/0/1) |
| `check_for_update(owner, repo, version)` | Calls GitHub API, finds .zip asset, returns release info |
| `download_update(url, dest, callback)` | Downloads in 64KB chunks with progress |
| `verify_download(path, expected_size)` | File size verification |
| `generate_update_script(app_dir, zip)` | Writes batch script to AppData |

### 10.4 Settings

| Key | Purpose |
|-----|---------|
| `update_repo_url` | GitHub repository URL |
| `update_auto_check` | Enable/disable auto-check on launch (default: enabled) |
| `update_last_check` | ISO timestamp of last check (rate limit) |
| `update_last_version` | Latest version found |
| `update_dismissed_version` | Version the user clicked "Skip" on |

### 10.5 Safety Features

| Concern | Solution |
|---------|----------|
| Running exe can't replace itself | Batch script waits for app to exit first |
| Corrupt download | File size verified against GitHub API |
| Bad update breaks app | Full backup at `AppData\_update_backup\` |
| Market day in progress | Download & Install blocked while market day is open |
| No internet | Silent skip on auto-check; clear error on manual check |
| Dev mode (not frozen) | Install button disabled with explanatory message |
| API rate limiting | Auto-check max once per 24 hours |

---

## 11. Core Business Logic

### 11.1 The FAM Match Formula

```
match_amount = method_amount * (match_percent / (100 + match_percent))
customer_charged = method_amount - match_amount
```

**Key property:** `match_amount + customer_charged == method_amount` (always holds)

All monetary values are **integer cents** (e.g. $89.99 = 8999). See Section 15 for the full Money Handling Contract.

**Formula locations** (must remain synchronized):

1. `fam/utils/calculations.py` -> `calculate_payment_breakdown()` -- canonical implementation
2. `fam/ui/widgets/payment_row.py` -> `_recompute()` -- live UI preview
3. `fam/ui/widgets/payment_row.py` -> `get_data()` -- data collection
4. `fam/ui/payment_screen.py` -> `_distribute_and_save_payments()` -- multi-receipt distribution

### 11.2 Daily Match Limit (Cap)

Each market can set a per-customer daily FAM match cap. When exceeded:
1. Compute `ratio = match_limit / uncapped_total`
2. Scale each line item's `match_amount` proportionally
3. Apply penny adjustment to the largest line item for rounding

### 11.3 Multi-Receipt Payment Distribution

When a customer order contains multiple receipts, payments are distributed proportionally across receipts based on receipt total. Rounding remainder applied to the last receipt.

### 11.4 Denomination Validation

Payment methods with a `denomination` value (e.g., $5.00 for FMNP checks) enforce that amounts entered in the UI are exact multiples of that denomination. The UI uses charge-based input: the user enters the total charge and the system calculates individual check amounts.

### 11.5 Penny Reconciliation

When 100% match methods split an odd-cent total (e.g., $56.77), exact halving is impossible. `calculate_payment_breakdown()` detects a ±1¢ gap between `allocated_total` and `receipt_total` and absorbs it into the FAM match of the largest matched line item. Customer charge stays unchanged — only the FAM subsidy absorbs the rounding artifact.

---

## 12. Application Lifecycle

### 12.1 Startup Sequence

1. `run.py` adds project root to `sys.path`, calls `fam.app.run()`
2. `app.py` detects frozen (PyInstaller) vs. development mode
3. Data directory resolved to `%APPDATA%\FAM Market Manager\` (production) or project root (development)
4. One-time migration: legacy data files moved from exe directory to AppData
5. Rotating file logger initialized in data directory
6. Database schema created/migrated via `initialize_database()`
7. Device ID captured via `capture_device_id()`
8. `QApplication` created with global stylesheet and exception handler
9. `MainWindow` instantiated and displayed
10. First-run tutorial auto-launches if `tutorial_shown` not set
11. Auto-update check scheduled via `QTimer.singleShot(5000, ...)` (rate-limited to once per 24h)
12. Qt event loop starts

### 12.2 First Run Experience

1. Tutorial overlay guides user through all 11 steps
2. Final step offers "Quick Setup" -- one-click auto-configure
3. "Yes" calls `seed_sample_data()` (3 markets, 23 vendors, 6 payment methods)
4. "No" leaves database empty for manual configuration
5. `tutorial_shown` flag set in `app_settings` to prevent re-launch

---

## 13. Testing

**1547 tests** across 24 test files:

| File | Tests | Coverage |
|------|-------|----------|
| `test_match_formula.py` | 98 | Core formula, reconciliation, edge cases, real-world scenarios |
| `test_match_limit.py` | 28 | Daily cap logic, proportional reduction, high percentages, penny reconciliation under cap, cap=0/1¢ boundaries |
| `test_returning_customer.py` | 23 | DB integration, prior match tracking, effective remaining limit |
| `test_adjustments.py` | 71 | Adjustments, voids, voided ledger exclusion, multi-method |
| `test_fmnp_reports.py` | 38 | FMNP entries, soft-delete, reporting |
| `test_models.py` | 130 | Model CRUD operations, transaction lifecycle, photo queries |
| `test_market_code.py` | 44 | Market code derivation, device ID, export filenames, CSV columns |
| `test_backup.py` | 21 | Backup creation, retention enforcement |
| `test_schema.py` | 40 | Migrations v1-v21, triggers, indexes, defaults |
| `test_settings_io.py` | 54 | Import/export parsing, round-trip, sanitization |
| `test_sync.py` | 124 | Cloud sync, data collection, Google Sheets mocking |
| `test_update.py` | 122 | URL parsing, version comparison, GitHub API, update flow, nested-zip exe probe, runtime batch execution against synthetic installs, path-traversal guard, PowerShell escaping, pending-update marker |
| `test_denomination.py` | 43 | Denomination constraints, charge conversion, validation |
| `test_charge_conversion.py` | 52 | Charge-to-amount conversion edge cases |
| `test_auto_distribute.py` | 71 | Multi-receipt payment distribution, max-cap math, cap reconciliation |
| `test_multi_photo.py` | 112 | Multi-photo storage, dedup (3-layer), Drive upload, hash cache |
| `test_cloud_sync_ux.py` | 151 | Sync UX flows, Drive integration, dead URL detection, Agent Tracker |
| `test_money_boundaries.py` | 63 | Integer-cents boundaries, float accumulation, FMNP check splitting, penny reconciliation |
| `test_reconciliation.py` | 25 | Three-way reconciliation (DB == Ledger == Sheets) |
| `test_ui_payment.py` | 37 | Automated UI tests: PaymentScreen widget behavior, row management, summary cards |
| `test_ui_workflows.py` | 31 | End-to-end market day simulation, returning customer cap workflows, void exclusion |
| `test_ui_guards.py` | 66 | Max-cap clamping, market day lifecycle guards, adjustment edge cases, match-cap-aware charge input |
| `test_ui_expanded.py` | 51 | Production readiness: payment confirm E2E (DB/sync/ledger), draft save/resume, returning customer match limits, void-after-confirm exclusion, adjustment propagation, multi-receipt mixed vendors, denomination overage/forfeit, odd-cent pipeline, high-volume reconciliation (30 txns), report state changes |
| `test_payment_method_safety.py` | 23 | Payment method CRUD safety, market assignment, deactivation guards, Reports FMNP separation |

**Run:** `python -m pytest tests/ -v`

---

## 14. Build and Deployment

### 14.1 Development

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

### 14.2 Windows Executable

```bash
build.bat
```

**Output:** `dist\FAM Manager\FAM Manager.exe`

The PyInstaller spec bundles all Python dependencies, UI assets, and hidden imports. Hidden imports include Google Sheets sync modules (`gspread`, `gspread.utils`, `gspread.exceptions`), Drive/photo modules (`google.auth.transport.requests`, `google.auth.transport._http_client`, `google.oauth2.service_account`), and their transitive dependencies (`cachetools`, `pyasn1`, `pyasn1_modules`, `rsa`). Excludes unused backends and test frameworks.

### 14.3 Distribution

Zip the `dist\FAM Manager` folder (include `FAM_Default_Settings.fam` for manual import). End users extract the zip and double-click the executable. No Python installation required. Works on Windows 10/11 (64-bit).

> **Windows SmartScreen:** Unsigned executables trigger a SmartScreen warning on first run. Users click "More info" -> "Run anyway." Code signing certificate is a planned future enhancement.

### 14.4 Data Persistence

All persistent data is stored in `%APPDATA%\FAM Market Manager\`:

| File/Folder | Purpose |
|-------------|---------|
| `fam_data.db` | SQLite database -- all application data |
| `fam_ledger_backup.txt` | Auto-generated human-readable ledger backup |
| `fam_manager.log` | Rotating log file (5 MB x 3 backups) |
| `sync_credentials.json` | Google service account credentials (if configured) |
| `photos/` | FMNP check and payment receipt photos (SHA-256 indexed) |
| `backups/` | Automatic database backups (20 most recent) |
| `_update_backup/` | Previous app version backup (created during auto-update) |

**Upgrades are seamless:** replace the application folder and launch. Schema migrations run automatically. Legacy data (v1.5.1 and earlier) is auto-migrated from the exe directory to AppData on first launch.

---

## 15. Money Handling Contract (Integer Cents)

All monetary values throughout the application are stored, computed, and transmitted as **integer cents** (e.g. `$89.99` = `8999`). This eliminates IEEE 754 float precision drift that accumulates when adding many dollar values.

### 15.1 Representation by Layer

| Layer | Representation | Conversion |
|-------|---------------|------------|
| Database (schema v22+) | INTEGER cents | Migration v21→v22 converted all REAL dollar columns |
| Python business logic | `int` cents | All calculations, models, sync, export |
| UI input (QDoubleSpinBox) | Dollar float | `dollars_to_cents()` on read from widget |
| UI display | Dollar string | `format_dollars(cents)` or `cents_to_dollars(cents)` |
| CSV/Ledger export | Dollar float | `cents_to_dollars()` at write boundary |
| Google Sheets sync | Dollar float | `cents_to_dollars()` in `data_collector.py` |

### 15.2 Boundary Helpers (`fam/utils/money.py`)

| Function | Direction | Example |
|----------|-----------|---------|
| `dollars_to_cents(89.99)` | UI → internal | `8999` |
| `cents_to_dollars(8999)` | internal → display | `89.99` |
| `format_dollars(8999)` | internal → string | `"$89.99"` |
| `format_dollars_comma(123456)` | internal → string | `"$1,234.56"` |

### 15.3 Anti-Patterns

- **Float accumulation**: Never sum `cents_to_dollars()` results across multiple rows. Accumulate in integer cents, convert once.
- **Dollar arithmetic**: Never do `receipt_total / 2` in dollar space. Work in cents.
- **Mixed types**: A variable is either cents (int) or dollars (float), never ambiguous.

### 15.4 Known Dollar Island

`large_receipt_threshold` in `app_settings` is stored and compared as a dollar value (float). It is compared only against QDoubleSpinBox dollar values in the UI — it never enters the cents pipeline.

---

## 16. Data Integrity & Reconciliation

### 16.1 Three-Way Invariant

For every completed transaction: **DB == Ledger == Sheets**

- **DB**: `transactions` + `payment_line_items` tables (integer cents)
- **Ledger**: `fam_ledger_backup.txt` (dollar strings, converted from cents at write time)
- **Sheets**: Google Sheets sync payload (dollar floats, converted from cents in `data_collector.py`)

### 16.2 Automated Tests (`test_reconciliation.py`)

25 end-to-end tests verify the three-way invariant across single transactions, multi-transaction aggregates, FMNP entries, edits, voids, persistence round-trips, edge-case amounts, and high-volume scenarios (50 transactions).

### 16.3 FMNP Check Splitting

When an FMNP entry covers multiple checks, the total is split using integer division with remainder distribution, guaranteeing `sum(all checks) == total` exactly.

### 16.4 FMNP Treatment Across Layers

FMNP data intentionally appears differently depending on the layer. This is by design, not a bug:

| Layer | What It Includes | Why |
|-------|-----------------|-----|
| **DB (`transactions` + `payment_line_items`)** | Regular transactions only; FMNP entries tracked separately in `fmnp_entries` table | Clean separation of payment-flow transactions from vendor-reported FMNP checks |
| **Sync — Market Day Summary** | Transactions only (`receipt_cents`, `customer_paid_cents`, `fam_match_cents`) | Matches DB scope; FMNP tracked via separate `fmnp_cents` field |
| **Sync — FMNP Entries tab** | Per-check rows from both `fmnp_entries` and FMNP `payment_line_items` | Full FMNP picture from both data sources |
| **Ledger (`fam_ledger_backup.txt`)** | Combines regular transactions + FMNP in the FAM Match total | Human-readable "everything in one place" for paper audit |
| **Reports Screen** | Separate cards: FAM Match card (transactions only) + FMNP Match card (FMNP only) | Clear visual separation for coordinators |

The three-way reconciliation tests (`test_reconciliation.py`) verify that DB == Ledger == Sheets for transaction data, with FMNP tracked separately in each layer's appropriate location.

### 16.5 Atomic Financial Operations

All financial state changes use explicit transaction boundaries with rollback on failure:

| Operation | Location | Pattern |
|-----------|----------|---------|
| `confirm_transaction()` | `transaction.py` | `try/commit=False/except/rollback` — writes line items + updates status atomically |
| `void_transaction()` | `transaction.py` | `try/commit=False/except/rollback` — sets Voided status + audit log atomically |
| `save_payment_line_items()` | `transaction.py` | `try/commit=False/except/rollback` — replaces all line items in one transaction |
| Admin adjustment | `admin_screen.py` | `try/except/rollback` — updates transaction + audit log atomically |

**Non-atomic by design:** `create_transaction()` and `create_customer_order()` do NOT use explicit transaction wrapping. These create Draft-status records with no financial impact — a partially-created draft is harmless and will be cleaned up or completed by the user.

### 16.6 Market Day Lifecycle Guards

Transactions can only be created on an open market day. This is enforced at the **model level** in `create_transaction()`:

```python
row = conn.execute("SELECT date, status FROM market_days WHERE id=?", (market_day_id,)).fetchone()
if row is None:
    raise ValueError(f"Market day {market_day_id} not found")
if row['status'] != 'Open':
    raise ValueError(f"Market day {market_day_id} is '{row['status']}' — transactions can only be created on an open market day")
```

This guard is authoritative — it fires regardless of which UI path reaches it. The Receipt Intake screen also has a UI-level guard (disabling the Add button when no open market day exists), but the model-level guard is the safety net.

### 16.7 Max-Cap Clamping (UI Guard)

The Payment Screen prevents users from exceeding the remaining order balance at input time:

1. **`_push_row_limits()`** in `payment_screen.py` — after every row change, recalculates each row's maximum allowed charge based on `order_total - sum(other rows' method_amounts)`
2. **`set_max_charge()`** in `PaymentRow` — receives the max and applies it to the active input widget
3. **`setMaxCharge()`** in `DenominationStepper` — converts remaining balance to max unit count via `floor(remaining / denomination)`, disables the + button at max

For denominated methods (e.g., FMNP $5 checks), the max is in charge space — the match flexes to fit. For non-denominated methods, the max charge accounts for match percentage: `remaining / (1 + match_pct / 100)`.

### 16.8 Snapshot Architecture

`payment_line_items` stores `method_name_snapshot` and `match_percent_snapshot` at the time of payment confirmation. This ensures historical records remain accurate even if payment method settings are later changed. Reports, ledger, and sync all read from these snapshot columns rather than joining to the current `payment_methods` table.

---

## 17. Developer Guardrails & Known Limitations

### 17.1 Code-Enforced Guardrails

These are automatically enforced by the codebase — no developer discipline required:

| Guardrail | Enforcement |
|-----------|------------|
| Market day must be Open to create transactions | `create_transaction()` raises `ValueError` |
| Receipt total must be positive | SQLite `BEFORE INSERT` trigger |
| Match percent must be 0-999 | SQLite `BEFORE INSERT/UPDATE` trigger |
| Payment line item amounts must be non-negative | SQLite `BEFORE INSERT` trigger |
| FMNP amount must be positive | SQLite `BEFORE INSERT` trigger |
| Foreign keys enforced | `PRAGMA foreign_keys=ON` on every connection |
| Schema version tracked | `schema_version` table, auto-migration on startup |
| Pre-migration backup | `.pre-migration.bak` created before any schema change |
| Snapshot columns frozen at confirmation | `confirm_transaction()` writes `method_name_snapshot` and `match_percent_snapshot` |
| Penny reconciliation | `calculate_payment_breakdown()` absorbs ±1¢ gaps into FAM match |
| Max-cap clamping | `_push_row_limits()` prevents UI input beyond remaining balance |
| Photo dedup (within-entry) | Hard block on duplicate SHA-256 hash within same entry |

### 17.2 Developer-Discipline Guardrails

These require developers to follow conventions — no automated enforcement:

| Convention | Rationale |
|------------|-----------|
| All new monetary columns must be INTEGER cents | Prevents float drift; `dollars_to_cents()` at UI boundary only |
| Never sum `cents_to_dollars()` results | Accumulate in int cents, convert once at display/export |
| Formula changes must update all 4 locations | `calculations.py`, `payment_row._recompute()`, `payment_row.get_data()`, `payment_screen._distribute_and_save_payments()` |
| New financial operations must use try/except/rollback | Follow pattern in `confirm_transaction()` |
| Audit log entries must include `app_version` and `device_id` | Required for multi-device traceability |

### 17.3 Known Non-Blocking Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| `QDoubleSpinBox` IEEE 754 float at UI boundary | Could theoretically produce ±0.001¢ error on exotic inputs | `dollars_to_cents()` uses `int(round(dollars * 100))` — rounds to nearest cent |
| `large_receipt_threshold` stored as dollar float | Only compared against UI dollar values, never enters cents pipeline | Isolated "dollar island" — no accumulation risk |
| `update_transaction` uses f-string SQL for field names | Field names come from hardcoded `allowed` set; values are parameterized | Safe by construction — no user input reaches field names |
| No server-side validation for cloud sync | Google Sheets is a display-only destination | One-way sync by design; spreadsheet changes are overwritten on next sync |
