# FAM Market Manager — Technical Overview

> **Version:** 1.6.1
> **Last Updated:** March 2026
> **Audience:** Developers, administrators, and stakeholders

---

## 1. System Purpose

FAM Market Manager is a desktop point-of-sale and back-office application for farmers markets participating in the **Food Assistance Match (FAM)** program. It enables volunteers to:

- Open and close market days
- Record customer receipts by vendor
- Calculate FAM matching subsidies per payment method
- Process multi-method payments with daily match caps
- Track FMNP (Farmers Market Nutrition Program) check entries
- Print customer receipts
- Generate reports, charts, and data exports
- Adjust or void transactions with a full audit trail
- Manage markets, vendors, and payment method configuration
- Import/export settings across devices via `.fam` files

The application runs as a standalone Windows desktop executable with no server, no internet requirement, and no external database — all data is stored locally in a single SQLite file.

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
│          │  │cust_order│  │          │
│          │  │ fmnp     │  │          │
│          │  │ audit    │  │          │
└──────────┘  └──────────┘  └──────────┘
        ▲            ▲             ▲
        └────────────┼─────────────┘
                     │
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
| Packaging | PyInstaller | Standalone Windows executable |
| Testing | pytest | Unit and integration tests (479 tests) |

**Runtime Dependencies** (`requirements.txt`):
- `PySide6 >= 6.5.0`
- `pandas >= 2.0.0`
- `matplotlib >= 3.7.0`
- `folium >= 0.14.0`
- `pgeocode >= 0.4.0`

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
│   │   ├── schema.py           # Table creation + migrations (v1–v11)
│   │   ├── seed.py             # Sample data (opt-in via tutorial)
│   │   └── backup.py           # SQLite backup API + retention
│   ├── models/
│   │   ├── vendor.py           # Vendor CRUD + market assignments
│   │   ├── market_day.py       # Market day open/close/reopen
│   │   ├── payment_method.py   # Payment method CRUD + market assignments
│   │   ├── transaction.py      # Transaction lifecycle + payment line items
│   │   ├── customer_order.py   # Customer order grouping + returning customers
│   │   ├── fmnp.py             # FMNP check entry CRUD
│   │   └── audit.py            # Append-only audit log
│   ├── ui/
│   │   ├── main_window.py      # MainWindow + sidebar + tutorial + backup timer
│   │   ├── market_day_screen.py
│   │   ├── receipt_intake_screen.py
│   │   ├── payment_screen.py   # Includes receipt printing
│   │   ├── fmnp_screen.py
│   │   ├── admin_screen.py
│   │   ├── reports_screen.py
│   │   ├── settings_screen.py  # Includes ImportPreviewDialog
│   │   ├── tutorial_overlay.py # Guided tutorial + auto-configure prompt
│   │   ├── styles.py           # Color palette + global stylesheet
│   │   ├── helpers.py          # Shared widgets + table utilities
│   │   └── widgets/
│   │       ├── payment_row.py  # Payment method entry widget
│   │       └── summary_card.py # Metric display cards
│   └── utils/
│       ├── app_settings.py     # Market code, device ID, key-value settings
│       ├── calculations.py     # Match formula + payment breakdown
│       ├── export.py           # CSV export + ledger backup
│       └── logging_config.py   # Rotating file logger
├── tests/
│   ├── test_match_formula.py   # 68 tests — core formula verification
│   ├── test_match_limit.py     # 18 tests — daily cap logic
│   ├── test_returning_customer.py  # 21 tests + DB integration
│   ├── test_adjustments.py     # 105 tests — adjustments, voids, ledger
│   ├── test_fmnp_reports.py    # 42 tests — FMNP entries and reports
│   ├── test_models.py          # 37 tests — model CRUD operations
│   ├── test_market_code.py     # 44 tests — market code, device ID, exports
│   ├── test_backup.py          # 12 tests — backup creation + retention
│   ├── test_schema.py          # 30 tests — migrations, triggers, indexes
│   └── test_settings_io.py     # 102 tests — import/export round-trip
├── releases/
│   └── FAM_Manager_v1.6.1.zip # Distribution package
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

### 5.2 Schema (Version 11)

**Core Tables:**

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `markets` | Market locations | name, address, daily_match_limit, match_limit_active |
| `vendors` | Vendor businesses | name, contact_info, is_active |
| `payment_methods` | Payment types with match rates | name, match_percent (0–999), sort_order |
| `market_days` | Daily market sessions | market_id, date, status (Open/Closed), opened_by, closed_by |
| `customer_orders` | Groups receipts per customer visit | market_day_id, customer_label (C-001), zip_code, status |
| `transactions` | Individual vendor receipts | fam_transaction_id, vendor_id, receipt_total, customer_order_id, status |
| `payment_line_items` | Payment breakdown per receipt | transaction_id, method_amount, match_amount, customer_charged |
| `fmnp_entries` | FMNP check records | market_day_id, vendor_id, amount, check_count, status |
| `audit_log` | Append-only change history | table_name, record_id, action, old_value, new_value, changed_by |
| `app_settings` | Key-value configuration store | key, value (market_code, device_id, tutorial_shown, etc.) |

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

### 5.4 Migrations

Schema migrations run automatically on startup. Each migration is guarded by a try/except. The `schema_version` table tracks the current version. A pre-migration backup (`.pre-migration.bak`) is created before any structural changes.

| Version | Change |
|---------|--------|
| v1→v2 | Added customer_orders table + customer_order_id to transactions |
| v2→v3 | Added market_vendors junction table |
| v3→v4 | Added validation triggers + performance indexes |
| v4→v5 | Added daily_match_limit columns to markets |
| v5→v6 | Renamed discount columns → match columns; expanded range to 0–999 |
| v6→v7 | Added zip_code to customer_orders |
| v7→v8 | Added FMNP payment method (100% match) |
| v8→v9 | Added market_payment_methods junction table |
| v9→v10 | Added app_settings key-value table |
| v10→v11 | Added status column to fmnp_entries for soft-delete |

### 5.5 Database Triggers

Check constraints enforced via `BEFORE INSERT` and `BEFORE UPDATE` triggers:

- `transactions.receipt_total > 0`
- `payment_line_items.method_amount >= 0`
- `payment_line_items.match_amount >= 0`
- `fmnp_entries.amount > 0`
- `payment_methods.match_percent BETWEEN 0 AND 999`

### 5.6 Indexes

Performance indexes on frequently queried columns:

- `idx_transactions_market_day` — transactions by market day
- `idx_transactions_status` — transactions by status
- `idx_transactions_fam_id` — transaction ID lookups
- `idx_payment_items_txn` — payment items by transaction
- `idx_fmnp_market_day` — FMNP entries by market day
- `idx_audit_log_changed_at` — audit log chronological queries

---

## 6. Multi-Market Device Identity

### 6.1 Market Code

A 1–4 character uppercase code auto-derived from the market name when a market day is opened:
- Multi-word: first letter of each word (e.g., "Bethel Park Farmers Market" → `BPFM`)
- Single word: first 2 alpha characters

The code is embedded in transaction IDs, CSV export filenames, ledger headers, receipt printouts, and the title bar.

### 6.2 Device ID

The Windows `MachineGuid` is captured from `HKLM\SOFTWARE\Microsoft\Cryptography` on first launch and stored in `app_settings`. Falls back to `hostname-{platform.node()}` if registry access fails. Appears in CSV exports and ledger headers as a supplemental identifier for the finance team.

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
- **Safety:** Never raises exceptions — all errors logged silently

### 7.2 Ledger Backup

- **File:** `fam_ledger_backup.txt` (single file, always overwritten)
- **Content:** Human-readable summary of ALL transactions from the entire database
- **Scope:** All market days, grouped by market → date → transaction
- **Triggers:** After every payment confirmation, adjustment, void, and market-day close
- **Write method:** Atomic (tempfile + os.replace) to prevent corruption
- **Fallback:** Timestamped file if the primary file is locked (e.g., open in Notepad)

---

## 8. Core Business Logic

### 8.1 The FAM Match Formula

```
match_amount = method_amount × (match_percent / (100 + match_percent))
customer_charged = method_amount − match_amount
```

**Key property:** `match_amount + customer_charged == method_amount` (always holds)

**Formula locations** (must remain synchronized):

1. `fam/utils/calculations.py` → `calculate_payment_breakdown()` — canonical implementation
2. `fam/ui/widgets/payment_row.py` → `_recompute()` — live UI preview
3. `fam/ui/widgets/payment_row.py` → `get_data()` — data collection
4. `fam/ui/payment_screen.py` → `_distribute_and_save_payments()` — multi-receipt distribution

### 8.2 Daily Match Limit (Cap)

Each market can set a per-customer daily FAM match cap. When exceeded:
1. Compute `ratio = match_limit / uncapped_total`
2. Scale each line item's `match_amount` proportionally
3. Apply penny adjustment to the largest line item for rounding

### 8.3 Multi-Receipt Payment Distribution

When a customer order contains multiple receipts, payments are distributed proportionally across receipts based on receipt total. Rounding remainder applied to the last receipt.

---

## 9. Application Lifecycle

### 9.1 Startup Sequence

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
11. Qt event loop starts

### 9.2 First Run Experience

1. Tutorial overlay guides user through all 11 steps
2. Final step offers "Quick Setup" — one-click auto-configure
3. "Yes" calls `seed_sample_data()` (3 markets, 8 vendors, 6 payment methods)
4. "No" leaves database empty for manual configuration
5. `tutorial_shown` flag set in `app_settings` to prevent re-launch

---

## 10. Testing

**479 tests** across 10 test files:

| File | Tests | Coverage |
|------|-------|----------|
| `test_match_formula.py` | 68 | Core formula, reconciliation, edge cases, real-world scenarios |
| `test_match_limit.py` | 18 | Daily cap logic, proportional reduction, high percentages |
| `test_returning_customer.py` | 21 | DB integration, prior match tracking, effective remaining limit |
| `test_adjustments.py` | 105 | Adjustments, voids, voided ledger exclusion, multi-method |
| `test_fmnp_reports.py` | 42 | FMNP entries, soft-delete, reporting |
| `test_models.py` | 37 | Model CRUD operations, transaction lifecycle |
| `test_market_code.py` | 44 | Market code derivation, device ID, export filenames, CSV columns |
| `test_backup.py` | 12 | Backup creation, retention enforcement |
| `test_schema.py` | 30 | Migrations, triggers, indexes, defaults |
| `test_settings_io.py` | 102 | Import/export parsing, round-trip, sanitization |

**Run:** `python -m pytest tests/ -v`

---

## 11. Build and Deployment

### 11.1 Development

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

### 11.2 Windows Executable

```bash
build.bat
```

**Output:** `dist\FAM Manager\FAM Manager.exe`

The PyInstaller spec bundles all Python dependencies, UI assets, and hidden imports. Excludes unused backends and test frameworks.

### 11.3 Distribution

Zip the `dist\FAM Manager` folder (include `FAM_Default_Settings.fam` for manual import). End users extract the zip and double-click the executable. No Python installation required. Works on Windows 10/11 (64-bit).

> **Windows SmartScreen:** Unsigned executables trigger a SmartScreen warning on first run. Users click "More info" → "Run anyway." Code signing certificate is a planned future enhancement.

### 11.4 Data Persistence

All persistent data is stored in `%APPDATA%\FAM Market Manager\`:

| File/Folder | Purpose |
|-------------|---------|
| `fam_data.db` | SQLite database — all application data |
| `fam_ledger_backup.txt` | Auto-generated human-readable ledger backup |
| `fam_manager.log` | Rotating log file (5 MB × 3 backups) |
| `backups/` | Automatic database backups (20 most recent) |

**Upgrades are seamless:** replace the application folder and launch. Schema migrations run automatically. Legacy data (v1.5.1 and earlier) is auto-migrated from the exe directory to AppData on first launch.
