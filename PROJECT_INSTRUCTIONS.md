# FAM Market Manager — Project Instructions & Specification

> **Purpose:** This file is the single source of truth for the FAM Market Manager
> application. It is written for an AI coding assistant or a new developer who
> needs to understand, maintain, or extend the project **without** access to
> previous conversation history. Keep this file up to date with every commit.
>
> **Last updated:** 2026-02-27 — v2 (commit 5b03751)

---

## 1. Project Overview

**FAM Market Manager** is a desktop POS/back-office application used at farmers
markets to track customer transactions, calculate Food Assistance Match (FAM)
subsidies, process payments, record FMNP checks, and generate reports for
vendor reimbursement.

| Stack         | Technology                          |
|---------------|-------------------------------------|
| Language      | Python 3.12+                        |
| GUI framework | PySide6 (Qt 6)                      |
| Database      | SQLite (WAL mode, foreign keys on)  |
| Charts        | matplotlib                          |
| Geolocation   | folium + pgeocode                   |
| Data export   | pandas                              |
| Packaging     | PyInstaller (Windows .exe)          |
| Tests         | pytest                              |

---

## 2. Repository Layout

```
fam-market-manager/
├── fam/                          # Application package
│   ├── app.py                    # Qt application entry, DB init
│   ├── run.py                    # Console entry point
│   ├── database/
│   │   ├── connection.py         # Thread-local SQLite connection
│   │   ├── schema.py             # Table DDL + migrations (v1–v7)
│   │   └── seed.py               # First-run sample data
│   ├── models/
│   │   ├── vendor.py             # Vendor CRUD
│   │   ├── market_day.py         # Market day lifecycle
│   │   ├── payment_method.py     # Payment method CRUD
│   │   ├── transaction.py        # Receipts + payment line items
│   │   ├── customer_order.py     # Multi-receipt customer orders
│   │   ├── fmnp.py               # FMNP check entries
│   │   └── audit.py              # Append-only audit log
│   ├── ui/
│   │   ├── main_window.py        # Sidebar nav + screen stack
│   │   ├── market_day_screen.py  # Screen 0 — Open/close market day
│   │   ├── receipt_intake_screen.py  # Screen 1 — Add receipts
│   │   ├── payment_screen.py     # Screen 2 — Allocate payments
│   │   ├── fmnp_screen.py        # Screen 3 — FMNP entry
│   │   ├── admin_screen.py       # Screen 4 — Adjustments & voids
│   │   ├── reports_screen.py     # Screen 5 — Reports & exports
│   │   ├── settings_screen.py    # Screen 6 — Config management
│   │   ├── styles.py             # Global QSS + brand colours
│   │   ├── helpers.py            # Reusable widgets & helpers
│   │   └── widgets/
│   │       ├── payment_row.py    # Payment method entry row
│   │       └── summary_card.py   # Summary display cards
│   └── utils/
│       ├── calculations.py       # Core financial math
│       ├── export.py             # CSV export functions
│       └── logging_config.py     # Rotating file logger
├── tests/
│   ├── test_match_formula.py     # 68 tests — formula validation
│   ├── test_match_limit.py       # 18 tests — daily cap logic
│   └── test_returning_customer.py # 21 tests — multi-visit tracking
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

- `match_limit` is passed to `calculate_payment_breakdown()`
- Returning customers: prior match usage is subtracted from the daily limit
  before the current order's calculation
- Cap logic: `if uncapped_total > match_limit: ratio = limit / uncapped_total`

---

## 4. Database Schema (v7)

### Tables

**markets**
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto-increment |
| name | TEXT UNIQUE NOT NULL | |
| address | TEXT | |
| is_active | INTEGER | Default 1 |
| daily_match_limit | REAL | Default 100.00 |
| match_limit_active | INTEGER | Default 0 (off) |

**vendors**
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| name | TEXT NOT NULL | |
| contact_info | TEXT | |
| is_active | INTEGER | Default 1 |

**market_vendors** — junction table
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| market_id | INTEGER FK | → markets |
| vendor_id | INTEGER FK | → vendors |
| | UNIQUE | (market_id, vendor_id) |

**payment_methods**
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| name | TEXT UNIQUE NOT NULL | |
| match_percent | REAL NOT NULL | 0–999, CHECK constraint |
| is_active | INTEGER | Default 1 |
| sort_order | INTEGER | Default 0 |

**market_days**
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| market_id | INTEGER FK | → markets |
| date | TEXT NOT NULL | YYYY-MM-DD |
| status | TEXT | 'Open' or 'Closed' |
| opened_by | TEXT | |
| closed_by | TEXT | |
| closed_at | TEXT | ISO timestamp |
| created_at | TEXT | Auto |

**customer_orders**
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| market_day_id | INTEGER FK | → market_days |
| customer_label | TEXT NOT NULL | Sequential: C-001, C-002… |
| zip_code | TEXT | Optional, for geolocation |
| status | TEXT | Draft / Confirmed / Voided |
| created_at | TEXT | Auto |

**transactions** — individual receipts
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| fam_transaction_id | TEXT UNIQUE | FAM-YYYYMMDD-NNNN |
| market_day_id | INTEGER FK | → market_days |
| vendor_id | INTEGER FK | → vendors |
| receipt_total | REAL | CHECK > 0 |
| receipt_number | TEXT | Optional paper receipt # |
| status | TEXT | Draft / Confirmed / Voided |
| snap_reference_code | TEXT | Optional SNAP approval code |
| confirmed_by | TEXT | |
| confirmed_at | TEXT | |
| notes | TEXT | |
| customer_order_id | INTEGER FK | → customer_orders |
| created_at | TEXT | Auto |

**payment_line_items** — payment breakdown per receipt
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| transaction_id | INTEGER FK | → transactions |
| payment_method_id | INTEGER FK | → payment_methods |
| method_name_snapshot | TEXT | Frozen name at time of save |
| match_percent_snapshot | REAL | Frozen % at time of save |
| method_amount | REAL | CHECK >= 0 |
| match_amount | REAL | CHECK >= 0 |
| customer_charged | REAL | Can be 0, never negative |
| created_at | TEXT | Auto |

**fmnp_entries** — Farmers Market Nutrition Program checks
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| market_day_id | INTEGER FK | → market_days |
| vendor_id | INTEGER FK | → vendors |
| amount | REAL | CHECK > 0 |
| check_count | INTEGER | |
| notes | TEXT | |
| entered_by | TEXT | |
| created_at | TEXT | Auto |
| updated_at | TEXT | Auto |

**audit_log** — append-only change history
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| table_name | TEXT | |
| record_id | INTEGER | |
| action | TEXT | INSERT / UPDATE / DELETE |
| field_name | TEXT | Column that changed |
| old_value | TEXT | |
| new_value | TEXT | |
| reason_code | TEXT | See admin adjustment codes |
| notes | TEXT | |
| changed_by | TEXT | |
| changed_at | TEXT | Auto |

**schema_version**
| Column | Type | Notes |
|--------|------|-------|
| version | INTEGER | Current: 7 |
| applied_at | TEXT | ISO timestamp |

### Migration History

| Version | Change |
|---------|--------|
| v1 | Added customer_orders table |
| v2 | Added market_vendors junction table |
| v3 | Added validation triggers + performance indexes |
| v4 | Added daily_match_limit columns to markets |
| v5 | Renamed discount_percent/discount_amount → match_percent/match_amount |
| v6 | Expanded match_percent CHECK to 0–999 |
| v7 | Added zip_code column to customer_orders |

### Indexes
- `idx_transactions_market_day` on transactions(market_day_id)
- `idx_transactions_status` on transactions(status)
- `idx_transactions_fam_id` on transactions(fam_transaction_id)
- `idx_pli_transaction` on payment_line_items(transaction_id)
- `idx_fmnp_market_day` on fmnp_entries(market_day_id)
- `idx_audit_changed_at` on audit_log(changed_at)

### Triggers
- Validate `receipt_total > 0` on INSERT/UPDATE to transactions
- Validate `method_amount >= 0` on INSERT/UPDATE to payment_line_items
- Validate `match_amount >= 0` on INSERT/UPDATE to payment_line_items
- Validate `amount > 0` on INSERT/UPDATE to fmnp_entries
- Validate `match_percent BETWEEN 0 AND 999` on INSERT/UPDATE to payment_methods

---

## 5. Seed Data (First Run)

When the database is created for the first time, `seed.py` populates:

**Markets:** Downtown Saturday Market, Riverside Wednesday Market

**Vendors:** Green Valley Farm, Sunny Acres Produce, Mountain Herb Co., Baker's Delight

**Payment Methods:**
| Name       | Match % | Sort |
|------------|---------|------|
| SNAP       | 50%     | 1    |
| Cash       | 0%      | 2    |
| Tokens     | 25%     | 3    |
| Food Bucks | 100%    | 4    |
| Food RX    | 75%     | 5    |

---

## 6. Application Screens

### Screen 0 — Market Day Setup (`market_day_screen.py`)
- Select market from dropdown, enter volunteer name
- Open market day for today's date
- View market day history table (sortable)
- Close / reopen market days
- Signal: `market_day_changed`

### Screen 1 — Receipt Intake (`receipt_intake_screen.py`)
- Create new customer → generates sequential label (C-001, C-002…)
- Returning customer dropdown (reuses label, tracks across visits)
- Optional zip code field (saved on blur, feeds geolocation report)
- Select vendor, enter receipt total, add notes
- Add multiple receipts to same customer order
- Receipts table with per-row Remove button
- "Confirm All Receipts" → navigates to Payment screen
- Void individual receipts or entire order
- Signal: `customer_order_ready(int)`

### Screen 2 — Payment Processing (`payment_screen.py`)
- Loads customer order (all receipts)
- Dynamic payment method rows (add/remove)
- Each row: method combo → amount input → live FAM match + customer pays
- Summary cards: Total Allocated, Remaining, Customer Pays, FAM Match, Vendor Reimbursement
- Daily match limit display with prior-usage tracking
- Match cap warning banner when limit is hit
- Vendor breakdown table
- Collection checklist (what to collect from customer)
- Save Draft or Confirm Payment (with confirmation dialog)
- Distributes payments proportionally across multi-receipt orders
- Signals: `payment_confirmed()`, `draft_saved()`

### Screen 3 — FMNP Entry (`fmnp_screen.py`)
- Select market day and vendor
- Enter amount, check count, notes
- FMNP entries table (sortable) with edit/delete
- Independent of the transaction/payment flow

### Screen 4 — Admin Adjustments (`admin_screen.py`)
- Search transactions by market day, vendor, status, FAM ID
- View transaction detail
- Edit receipt amount or vendor with reason code
- Void transactions
- Reason codes: `data_entry_error`, `vendor_correction`, `admin_adjustment`, `customer_dispute`, `other`
- All changes logged to audit_log table

### Screen 5 — Reports (`reports_screen.py`)
- Four-filter bar: Market, Vendor, Status, Date Range (all multi-select)
- Tab 1 — Data Tables:
  - Vendor Reimbursement
  - FAM Match Breakdown
  - Detailed Ledger
  - Activity Log / Audit Trail
  - Geolocation (zip code analysis)
- Tab 2 — Charts:
  - Pie: Payment method distribution
  - Line: Match usage trends
  - Bar: FMNP vendor totals
  - Bar: Top 15 zip codes by customer count
- Geolocation heat map button (generates folium HTML, opens in browser)
- Export any report to CSV (timestamped filenames)

### Screen 6 — Settings (`settings_screen.py`)
- Tab: Markets — add/edit markets, set daily match limit, assign vendors
- Tab: Vendors — add/edit vendors, activate/deactivate
- Tab: Payment Methods — add/edit methods, set match %, sort order, activate/deactivate

---

## 7. UI Architecture & Styling

### Framework
- `QMainWindow` with sidebar (`_PatternSidebar`) + `QStackedWidget`
- Sidebar: tileable background image over dark green, white text buttons
- All screens are `QWidget` subclasses swapped via stacked widget index

### Brand Colours (in `styles.py`)
| Constant | Hex | Usage |
|----------|-----|-------|
| `PRIMARY_GREEN` | #2b493b | Sidebar, headers, primary accent |
| `ACCENT_GREEN` | #469a45 | Success states, positive values |
| `HARVEST_GOLD` | #e68a3e | Highlights, primary buttons |
| `BACKGROUND` | #F7F6F2 | Page background (light cream) |
| `WHITE` | #FFFFFF | Cards, inputs |
| `LIGHT_GRAY` | #E0E0E0 | Borders |
| `MEDIUM_GRAY` | #9E9E9E | Disabled/muted text |
| `SUBTITLE_GRAY` | #757575 | Subtitles |
| `ERROR_COLOR` | #D32F2F | Errors, void/remove buttons |
| `WARNING_COLOR` | #f79841 | Warnings |
| `FIELD_LABEL_BG` | #ECE8DE | Field label background |

### Key Reusable Widgets
- **`CheckableComboBox`** — Multi-select dropdown with "Select All", stays open
- **`DateRangeWidget`** — Clickable date-range picker with dialog
- **`PaymentRow`** — Payment method selector + amount + computed fields
- **`SummaryCard` / `SummaryRow`** — Metric display cards with dynamic colours
- **`make_field_label()`** — Height-matched field labels
- **`configure_table()`** — Standard table setup (sort, stretch, alternating rows)

### Button Heights
Primary and secondary buttons are height-matched to input fields for visual
consistency. Primary buttons use `HARVEST_GOLD` (dark orange), secondary
buttons use white with green border.

---

## 8. Application Workflows

### Workflow A: Full Market Day

1. **Open market** — Screen 0: Select market, enter volunteer name, click Open
2. **Intake receipts** — Screen 1: New customer → add receipts → Confirm
3. **Process payment** — Screen 2: Allocate methods → Confirm Payment
4. **Repeat 2–3** for each customer
5. **FMNP** — Screen 3: Enter FMNP checks received by vendors
6. **Reports** — Screen 5: Export Vendor Reimbursement, FAM Match, Ledger
7. **Close market** — Screen 0: Close market day

### Workflow B: Returning Customer

1. Customer C-001 checked out earlier with $60 order, $30 FAM match
2. C-001 returns with more items
3. Volunteer selects "C-001" from Returning Customer dropdown
4. System creates new order under same label
5. Payment screen shows: "Previously redeemed: $30 | Remaining: $70"
6. New order's match is capped by the remaining daily limit

### Workflow C: Admin Correction

1. Screen 4: Search for the transaction
2. Click Edit → AdjustmentDialog opens
3. Change receipt amount, select reason code, add notes
4. Confirm → old/new values logged to audit_log
5. If payment was confirmed, it moves to "Adjusted" status

---

## 9. Test Suite

**Run:** `python -m pytest tests/ -v` from project root

**107 total tests across 3 files:**

### test_match_formula.py (68 tests)
- `TestCoreFormula` — Spot-checks at 0%, 10%, 25%, 50%, 75%, 100%, 200%, 500%
- `TestReconciliation` — Parametrized proof that match + customer == receipt
  for 16 amount/percent combinations + multi-method scenarios
- `TestCustomerNeverNegative` — Customer charged >= 0 for all percentages 0–999
- `TestBoundaryConditions` — $0 receipt, $0.01 penny, $10k, 999%, fractional
  percentages, empty entries, negative inputs
- `TestCapEdgeCases` — Cap at/below/above match, zero cap, three-method proportional
- `TestRealWorldScenarios` — SNAP purchases, mixed payment, daily cap, returning
  customer exhausted limit

### test_match_limit.py (18 tests)
- `TestNoMatchLimit` — Unlimited: 0%, 50%, 100% match
- `TestMatchLimitAboveTotal` — Cap above actual match (no capping)
- `TestMatchLimitCapping` — Single/multi method capping, proportional reduction
- `TestMatchLimitEdgeCases` — Zero cap, None limit, reconciliation after cap
- `TestHighMatchPercent` — 150%, 200%, 300% match with and without cap

### test_returning_customer.py (21 tests)
- Uses temp SQLite DB fixture with seeded market/vendor/payment method
- `TestGetConfirmedCustomers` — Query confirmed customer list
- `TestGetCustomerPriorMatch` — Track match usage, exclusion, isolation
- `TestReturningCustomerOrder` — Label generation, reuse, market fields
- `TestEffectiveRemainingLimit` — Cumulative limit tracking across 1–3 visits

---

## 10. Build & Deployment

### Development

```bash
pip install -r requirements.txt
python fam/run.py
```

### Windows Executable

```bash
build.bat
# Output: dist/FAM Manager/FAM Manager.exe
```

The `.exe` creates `fam_data.db` alongside itself on first run. The database
file is the only persistent state — back it up to preserve all data.

### PyInstaller Notes (`fam_manager.spec`)
- Entry: `fam/run.py` (windowed, no console)
- Assets bundled: `_dropdown_arrow.png`, `_fam_logo.png`, `_fam_background.jpg`
- Hidden imports: matplotlib backends, PySide6 modules, folium, pgeocode, branca, xyzservices
- Excludes: tkinter, pytest, unused matplotlib backends

---

## 11. Dependencies

```
PySide6>=6.5.0       # Qt GUI framework
pandas>=2.0.0        # Data export to CSV
matplotlib>=3.7.0    # Charts in reports
folium>=0.14.0       # Interactive heat map (geolocation report)
pgeocode>=0.4.0      # Offline zip code → lat/lon geocoding
```

Dev/test only: `pytest`

---

## 12. Conventions & Patterns

### Code Style
- Python 3.12+, type hints where practical
- Models return `dict` rows (sqlite3.Row) or lists of dicts
- All monetary values are `float`, rounded to 2 decimal places
- Database writes default to `commit=True`; pass `commit=False` for atomic batches

### Naming
- "Match" (not "discount") everywhere — renamed in v2
- `match_percent` = the configured percentage
- `match_amount` = the dollar amount FAM pays
- `customer_charged` = the dollar amount customer pays
- `fam_subsidy_total` = sum of all match amounts
- `receipt_total` = the full receipt amount (match + customer = receipt)
- `method_amount` = portion of receipt allocated to one payment method

### Snapshots
`payment_line_items` stores `method_name_snapshot` and `match_percent_snapshot`
to freeze the values at time of payment. If a payment method's name or
percentage changes later, historical records remain accurate.

### Signals
PySide6 signals are used for cross-screen communication:
- `market_day_changed` — Market day opened/closed
- `customer_order_ready(int)` — Receipt intake → Payment screen
- `payment_confirmed()` — Payment confirmed → return to intake
- `draft_saved()` — Draft saved → return to intake

### Error Handling
- Database errors: caught at the screen level, shown via error labels
- Validation: `calculate_payment_breakdown()` returns `errors` list and `is_valid` flag
- Atomic operations: use `commit=False` + explicit `conn.commit()` / `conn.rollback()`

---

## 13. Known Design Decisions

1. **SQLite, not Postgres** — Single-user desktop app, no server needed.
   Database file lives alongside the .exe for easy backup/portability.

2. **No ORM** — Direct SQL via sqlite3 module. Models are thin wrappers.
   Keeps the codebase simple and the SQL visible.

3. **Snapshot columns** — Payment line items freeze method name and percentage
   so historical data survives settings changes.

4. **Sequential customer labels** — C-001, C-002 per market day. Simple,
   human-friendly, works for the paper-receipt workflow at markets.

5. **Proportional cap reduction** — When daily match limit is hit, all
   payment methods are scaled down by the same ratio rather than capping
   first-come-first-served. This is fairer for mixed-method payments.

6. **Folium for heat maps** — Generates an HTML file opened in the system
   browser rather than embedding a map in the Qt window. Simpler, more
   interactive, avoids QtWebEngine dependency.

---

## 14. Version History

| Version | Date       | Commit   | Summary |
|---------|------------|----------|---------|
| v1      | 2026-02-26 | d1f7253  | Initial commit — core app structure |
| v2      | 2026-02-27 | 5b03751  | Fix match formula (1:1 semantics), add zip code geolocation report, rename discount→match, add 107 tests |

---

## 15. How to Continue Development

If you are an AI assistant or developer picking up this project:

1. **Read this file first** — it covers architecture, business logic, and conventions
2. **Run the tests** — `python -m pytest tests/ -v` — all 107 must pass
3. **Understand the match formula** — Section 3 is the most critical business logic
4. **Check the schema version** — `CURRENT_SCHEMA_VERSION` in `schema.py` tells you the current DB version; new migrations increment it
5. **Follow the naming conventions** — "match" not "discount", snapshot columns for historical accuracy
6. **Update this file** — When you add features, fix bugs, or change schema, update the relevant sections and bump the version history
7. **Keep tests passing** — Add tests for new calculations; the formula tests in `test_match_formula.py` are the gold standard for validating financial math

### Adding a New Feature Checklist
- [ ] Model functions in `fam/models/` (CRUD, queries)
- [ ] If schema change: new migration in `schema.py`, bump `CURRENT_SCHEMA_VERSION`
- [ ] UI screen or widget updates in `fam/ui/`
- [ ] If new calculation logic: add to `calculations.py` + tests
- [ ] If new export: add to `export.py`
- [ ] Update this `PROJECT_INSTRUCTIONS.md`
- [ ] Run full test suite
- [ ] Commit with descriptive message
