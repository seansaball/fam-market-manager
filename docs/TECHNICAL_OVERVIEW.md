# FAM Market Manager — Technical Overview

> **Version:** 1.4.1
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
- Generate reports, charts, and data exports
- Adjust or void transactions with a full audit trail
- Manage markets, vendors, and payment method configuration

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
│   - Sets DB path                                 │
│   - Initializes logging                          │
│   - Initializes database schema + seed           │
│   - Creates QApplication + MainWindow            │
│   - Applies global stylesheet                    │
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
│          │  │transact. │  │          │
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
| Testing | pytest | Unit and integration tests (160 tests) |

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
│   ├── __init__.py
│   ├── app.py                  # QApplication initialization
│   ├── database/
│   │   ├── connection.py       # Thread-local SQLite connections
│   │   ├── schema.py           # Table creation + migrations (v1–v9)
│   │   └── seed.py             # Default data population
│   ├── models/
│   │   ├── vendor.py           # Vendor CRUD + market assignments
│   │   ├── market_day.py       # Market day open/close/reopen
│   │   ├── payment_method.py   # Payment method CRUD + market assignments
│   │   ├── transaction.py      # Transaction lifecycle + payment line items
│   │   ├── customer_order.py   # Customer order grouping + returning customers
│   │   ├── fmnp.py             # FMNP check entry CRUD
│   │   └── audit.py            # Append-only audit log
│   ├── ui/
│   │   ├── main_window.py      # MainWindow + sidebar + tutorial integration
│   │   ├── market_day_screen.py
│   │   ├── receipt_intake_screen.py
│   │   ├── payment_screen.py
│   │   ├── fmnp_screen.py
│   │   ├── admin_screen.py
│   │   ├── reports_screen.py
│   │   ├── settings_screen.py
│   │   ├── tutorial_overlay.py # Guided tutorial system
│   │   ├── styles.py           # Color palette + global stylesheet
│   │   ├── helpers.py          # Shared widgets + table utilities
│   │   └── widgets/
│   │       ├── payment_row.py  # Payment method entry widget
│   │       └── summary_card.py # Metric display cards
│   └── utils/
│       ├── calculations.py     # Match formula + payment breakdown
│       ├── export.py           # CSV export + ledger backup
│       └── logging_config.py   # Rotating file logger
├── tests/
│   ├── test_match_formula.py   # 68 tests — core formula verification
│   ├── test_match_limit.py     # 18 tests — daily cap logic
│   └── test_returning_customer.py  # 21 tests + DB integration
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

The database file (`fam_data.db`) is created alongside the executable in production, or in the project root during development.

### 5.2 Schema (Version 9)

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
| `fmnp_entries` | FMNP check records | market_day_id, vendor_id, amount, check_count |
| `audit_log` | Append-only change history | table_name, record_id, action, old_value, new_value, changed_by |

**Junction Tables:**

| Table | Relationship |
|-------|-------------|
| `market_vendors` | Which vendors serve which markets |
| `market_payment_methods` | Which payment methods each market accepts |

### 5.3 Key Design Decisions

**Snapshot columns:** `payment_line_items` stores `method_name_snapshot` and `match_percent_snapshot` at the time of payment confirmation. This ensures historical records remain accurate even if payment method settings are later changed.

**Soft deletes:** Transactions and customer orders use a `status` field (`Draft` / `Confirmed` / `Voided`) rather than physical deletion. Voided records are preserved for audit purposes.

**Transaction IDs:** Human-readable format `FAM-YYYYMMDD-NNNN` with sequential numbering per date. Example: `FAM-20260301-0005`.

**Customer labels:** Sequential per market day (`C-001`, `C-002`, ...) designed to match paper receipt numbering. Returning customers reuse their original label for additional orders within the same market day.

### 5.4 Migrations

Schema migrations run automatically on startup. Each migration is guarded by a try/except on `sqlite3.OperationalError` (for `ALTER TABLE` of existing columns) or `sqlite3.IntegrityError` (for duplicate inserts). The `schema_version` table tracks the current version.

### 5.5 Database Triggers

Check constraints are enforced via `BEFORE INSERT` and `BEFORE UPDATE` triggers:

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

## 6. Core Business Logic

### 6.1 The FAM Match Formula

This is the central calculation of the entire system. The formula determines how much FAM pays versus how much the customer pays for each payment method:

```
match_amount = method_amount × (match_percent / (100 + match_percent))
customer_charged = method_amount − match_amount
```

**Key property:** `match_amount + customer_charged == method_amount` (always holds)

**Example values for a $100 receipt:**

| Match % | Meaning | FAM Pays | Customer Pays |
|---------|---------|----------|---------------|
| 0% | No match | $0.00 | $100.00 |
| 25% | 1:4 ratio | $20.00 | $80.00 |
| 50% | 1:2 ratio | $33.33 | $66.67 |
| 100% | 1:1 dollar-for-dollar | $50.00 | $50.00 |
| 200% | 2:1 ratio | $66.67 | $33.33 |
| 300% | 3:1 ratio | $75.00 | $25.00 |

**Formula locations** (must remain synchronized):

1. `fam/utils/calculations.py` → `calculate_payment_breakdown()` — canonical implementation
2. `fam/ui/widgets/payment_row.py` → `_recompute()` — live UI preview
3. `fam/ui/widgets/payment_row.py` → `get_data()` — data collection
4. `fam/ui/payment_screen.py` → `_distribute_and_save_payments()` — multi-receipt distribution

### 6.2 Daily Match Limit (Cap)

Each market can set a per-customer daily FAM match cap (e.g., $100/day). When the total uncapped match exceeds this limit:

1. Compute `ratio = match_limit / uncapped_total`
2. Multiply each line item's `match_amount` by the ratio
3. Recalculate `customer_charged = method_amount - match_amount`
4. Apply penny adjustment to the largest line item to correct rounding drift

**Returning customers:** The system tracks how much match a customer has already redeemed within the market day. The effective remaining limit is `daily_limit - prior_match_total`, and this reduced limit is applied to the current order.

### 6.3 Multi-Receipt Payment Distribution

When a customer order contains multiple receipts (transactions), the payment is distributed across them:

1. User allocates payment at the order level (e.g., $30 SNAP, $20 Cash)
2. System distributes each payment method proportionally across receipts based on receipt total
3. Each receipt gets its own `payment_line_items` with method-level amounts
4. Rounding remainder is applied to the last receipt to ensure exact totals

### 6.4 Transaction Lifecycle

```
Draft ──→ Confirmed
  │            │
  └──→ Voided  └──→ Adjusted ──→ (still Confirmed)
                        │
                        └──→ Voided
```

- **Draft:** Created when a receipt is entered. Can be modified or voided.
- **Confirmed:** Payment has been processed. Receipt amounts are locked. Can be adjusted or voided.
- **Adjusted:** An admin correction has been applied. The transaction remains in a confirmed state with the adjusted values. The audit log records old and new values.
- **Voided:** Soft-deleted. Cannot be further modified. All associated payment line items are also voided.

### 6.5 Customer Order Lifecycle

```
Draft ──→ Confirmed (when payment is confirmed)
  │
  └──→ Voided (voids all child transactions)
```

---

## 7. Application Lifecycle

### 7.1 Startup Sequence

1. `run.py` adds project root to `sys.path`, calls `fam.app.run()`
2. `app.py` detects frozen (PyInstaller) vs. development mode
3. Database path set to `fam_data.db` alongside executable or project root
4. Rotating file logger initialized (`fam_manager.log`, 5 MB max, 3 backups)
5. Database schema created/migrated via `initialize_database()`
6. Default data seeded if tables are empty via `seed_if_empty()`
7. `QApplication` created with global stylesheet applied
8. `MainWindow` instantiated and displayed
9. Qt event loop starts

### 7.2 Error Handling at Startup

If database initialization fails, a `QMessageBox.critical` dialog is shown with:
- Database file path
- Error details
- Log file path for troubleshooting

The application exits with code 1.

### 7.3 Shutdown

Application exits when the main window is closed. The exit code is logged.

---

## 8. UI Architecture

### 8.1 Window Structure

`MainWindow` (QMainWindow) contains:
- **Sidebar** (`_PatternSidebar`, 240px fixed): Logo, subtitle, 7 navigation buttons in a `QButtonGroup`, version label
- **Content Area** (QFrame):
  - **Header Bar** (40px): Right-aligned "Start Tutorial" button
  - **QStackedWidget**: 7 screens switched by sidebar navigation

### 8.2 Screen Communication

Screens communicate via Qt signals routed through `MainWindow`:

| Signal | Source | Target | Purpose |
|--------|--------|--------|---------|
| `market_day_changed` | MarketDayScreen | ReceiptIntakeScreen | Refresh after market open/close |
| `customer_order_ready(int)` | ReceiptIntakeScreen | PaymentScreen | Navigate to payment with order ID |
| `payment_confirmed()` | PaymentScreen | ReceiptIntakeScreen | Return to intake after payment |
| `draft_saved()` | PaymentScreen | ReceiptIntakeScreen | Return to intake after draft save |

### 8.3 Brand Colors

| Name | Hex | Usage |
|------|-----|-------|
| PRIMARY_GREEN | `#2b493b` | Sidebar, headers, accents |
| ACCENT_GREEN | `#469a45` | Success states, positive values |
| HARVEST_GOLD | `#e68a3e` | Primary buttons, highlights, totals |
| BACKGROUND | `#F7F6F2` | Page background |
| WHITE | `#FFFFFF` | Cards, inputs |
| ERROR_COLOR | `#D32F2F` | Errors, void actions |
| WARNING_COLOR | `#f79841` | Warnings, caution states |

### 8.4 Reusable Widgets

| Widget | File | Purpose |
|--------|------|---------|
| `PaymentRow` | `widgets/payment_row.py` | Payment method + amount + computed match/charge |
| `SummaryCard` / `SummaryRow` | `widgets/summary_card.py` | Metric display cards with dynamic colors |
| `CheckableComboBox` | `helpers.py` | Multi-select dropdown with checkboxes |
| `DateRangeWidget` | `helpers.py` | Date range picker with month/day/year selectors |
| `NoScrollComboBox/SpinBox` | `helpers.py` | Inputs that ignore mouse wheel scroll |
| `TutorialOverlay` | `tutorial_overlay.py` | Step-by-step guided walkthrough overlay |

---

## 9. Logging System

### 9.1 File Logging

- **File:** `fam_manager.log` (alongside database)
- **Handler:** `RotatingFileHandler`
- **Max size:** 5 MB per file, 3 backups (20 MB total)
- **Format:** `2026-03-01 14:30:00 [INFO] fam.models.transaction: Transaction created: FAM-20260301-0001 id=1 total=$25.00`
- **Level:** INFO and above

### 9.2 Audit Log (Database)

The `audit_log` table provides an append-only record of all significant operations:

| Action | Logged When |
|--------|-------------|
| `CREATE` | Transaction or customer order created |
| `CONFIRM` | Payment confirmed |
| `ADJUST` | Transaction amount or vendor changed |
| `VOID` | Transaction or order voided |
| `PAYMENT_SAVED` | Payment line items saved |
| `OPEN` | Market day opened |
| `CLOSE` | Market day closed |
| `REOPEN` | Market day reopened |

Each entry records: table name, record ID, action, field changed, old value, new value, reason code, notes, who made the change, and timestamp.

### 9.3 Ledger Backup

After payment confirmations, adjustments, voids, and market-day close, `write_ledger_backup()` generates a human-readable text file (`fam_ledger_backup.txt`) with the current market day's complete transaction summary. This provides a readable fallback if the application or database becomes inaccessible.

---

## 10. Data Export

The Reports screen (Screen 5) supports CSV export for all report types:

| Report | Function | Data Included |
|--------|----------|---------------|
| Detailed Ledger | `export_detailed_ledger()` | All transactions with payment breakdowns |
| Vendor Reimbursement | `export_vendor_reimbursement()` | Per-vendor totals, FAM subsidy, customer paid |
| FAM Match Report | `export_fam_match_report()` | Per-customer match amounts |
| Activity Log | `export_activity_log()` | Audit trail entries |
| Geolocation | `export_geolocation_report()` | Zip code aggregations |
| Error Log | `export_error_log()` | System error entries |

File names are auto-generated with timestamps: `fam_{report_name}_{YYYYMMDD_HHMMSS}.csv`

---

## 11. Testing

**160 tests** across 3 test files:

| File | Tests | Coverage |
|------|-------|----------|
| `test_match_formula.py` | 68 | Core formula, reconciliation, edge cases, real-world scenarios |
| `test_match_limit.py` | 18 | Daily cap logic, proportional reduction, high percentages |
| `test_returning_customer.py` | 21 | DB integration, prior match tracking, effective remaining limit |

**Run:** `python -m pytest tests/ -v`

---

## 12. Build and Deployment

### 12.1 Development

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

### 12.2 Windows Executable

```bash
build.bat
```

**Output:** `dist\FAM Manager\FAM Manager.exe`

The PyInstaller spec bundles:
- All Python dependencies
- UI assets (logo, background, dropdown arrow, icon)
- Hidden imports for matplotlib, PySide6 SVG, and geolocation libraries
- Excludes unused backends (Tk, GTK, Cairo) and test frameworks

### 12.3 Distribution

Zip the `dist\FAM Manager` folder. End users extract the zip and double-click the executable. No Python installation required. Works on Windows 10/11 (64-bit).

### 12.4 Data Persistence

The single `fam_data.db` file is created on first launch alongside the executable. This file contains all application data and must be backed up to preserve historical records. To migrate to another machine, copy the entire application folder including the database file.

---

## 13. Seed Data (First Launch)

On first launch with an empty database, the system populates:

**3 Markets:** Bethel Park Farmers Market, Bellevue Farmers Market, Cranberry Farmers Market

**8 Vendors:** Evelyn's Farm, Forever Green Family Farm, Goose Run Farms, Hello Hummus, Loafers Bread Co, Logan Family Farm, Rockin' Cat Organic Coffee and Tea, Two Acre Farm

**6 Payment Methods:**

| Name | Match % | Sort Order |
|------|---------|-----------|
| SNAP | 100% | 1 |
| FMNP | 100% | 2 |
| Food RX | 100% | 3 |
| JH Food Bucks | 100% | 4 |
| JH Tokens | 100% | 5 |
| Cash | 0% | 6 |

All vendors and payment methods are assigned to all markets by default.

---

## 14. Extensibility

### Adding a New Screen

1. Create `fam/ui/new_screen.py` with a QWidget subclass
2. Add to `MainWindow.__init__()` — instantiate and add to `self.stack`
3. Add navigation entry in the `nav_items` list
4. Connect any inter-screen signals through MainWindow

### Adding a New Payment Method

Use the Settings screen (no code changes needed). Payment methods support match percentages from 0% to 999%.

### Adding a New Report Tab

Add a new tab to the `QTabWidget` in `reports_screen.py`. Follow the existing pattern of filter → query → table/chart → export.

### Schema Changes

1. Increment `CURRENT_SCHEMA_VERSION` in `schema.py`
2. Add migration logic in the migrations section (guarded by try/except)
3. Update model functions to use new columns
4. Update relevant UI screens

### Tutorial Steps

Tutorial content is defined as a data list (`TUTORIAL_STEPS`) in `tutorial_overlay.py`. Each step is a `TutorialStep` dataclass specifying the title, description, widget to highlight, card position, and which screen to display. Steps can be added, removed, or reordered by editing the list.
