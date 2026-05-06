# FAM Data Flow Inventory (v1.9.10, 2026-05-01)

> Comprehensive map of every financial input → DB → output replication
> point. Each replication arrow is pinned by at least one regression
> test (cited in the right column).

## Inputs (where money values originate)

| Surface | Field | Type | DB destination |
|---|---|---|---|
| Receipt Intake | receipt_total | int cents | `transactions.receipt_total` |
| Receipt Intake | vendor | int (FK) | `transactions.vendor_id` |
| Receipt Intake | customer_label | text | `customer_orders.customer_label` |
| Receipt Intake | zip_code | text | `customer_orders.zip_code` |
| Receipt Intake | notes | text | `transactions.notes` |
| Payment screen | charge per row | int cents | `payment_line_items.customer_charged` (via engine) |
| Payment screen | method | int (FK) | `payment_line_items.payment_method_id` |
| Payment screen | bound vendor (denom) | int | resolved into `transactions.vendor_id` of the saved row's transaction |
| Payment screen | photo source path(s) | str/list | `payment_line_items.photo_path` |
| FMNP entry | amount | int cents | `fmnp_entries.amount` |
| FMNP entry | vendor | int (FK) | `fmnp_entries.vendor_id` |
| FMNP entry | check #s + photos | text | `fmnp_entries.notes`, `fmnp_entries.photo_path` |
| Adjustment | new receipt_total | int cents | `transactions.receipt_total` (UPDATE) |
| Adjustment | new vendor | int | `transactions.vendor_id` (UPDATE) |
| Adjustment | new payment rows | various | `payment_line_items` (DELETE+INSERT) |
| Adjustment | "customer-gone" gap | int cents | new PLI row, method='Unallocated Funds' |
| Settings → Markets | name, address, daily_match_limit, match_limit_active, is_active | various | `markets.*` |
| Settings → Vendors | name, check_payable_to, address, is_active | various | `vendors.*` |
| Settings → Methods | name, match_percent, denomination, is_active | various | `payment_methods.*` |
| Settings → Reward Rules | source/threshold/reward/unit/active | int | `reward_rules.*` |
| Settings → Sync tabs | each tab on/off | bool | `app_settings.sync_tab_<name>` |
| Settings → Rewards toggle | on/off | bool | `app_settings.rewards_enabled` |
| Market Day | open/close | timestamp | `market_days.status, opened_at, closed_at` |

## Storage layer (DB columns by table)

| Table | Money columns | Status/audit columns | FK refs |
|---|---|---|---|
| `transactions` | `receipt_total` | `status`, `confirmed_by`, `confirmed_at` | market_day_id, vendor_id, customer_order_id |
| `payment_line_items` | `method_amount`, `match_amount`, `customer_charged` | `created_at`, `photo_path`, `photo_drive_url` | transaction_id, payment_method_id |
| `customer_orders` | — | `status`, `customer_label`, `zip_code`, `created_at` | market_day_id |
| `fmnp_entries` | `amount` | `status`, `entered_by`, `entered_at`, `photo_path`, `photo_drive_url` | market_day_id, vendor_id |
| `audit_log` | — | `action`, `field_name`, `old_value`, `new_value`, `changed_by`, `device_id`, `app_version`, `changed_at` | (record_id is loose) |
| `generated_rewards` | `source_total_cents`, `threshold_cents`, `reward_unit_cents`, `reward_amount_cents` | `generated_at`, `generated_by` | customer_order_id, market_day_id |
| `reward_rules` | `threshold_cents`, `reward_unit_cents` | `is_active`, `created_at`, `updated_at` | source_method_id, reward_method_id |
| `markets` | `daily_match_limit` | `match_limit_active`, `is_active` | — |

## Output replication points

### In-app Reports screen

| Tab/card | Reads from | Pinned by |
|---|---|---|
| Total Receipts card | SUM `transactions.receipt_total` | `test_v1_9_10_audit_fixes::TestPostConfirmSumEqualsReceipt` |
| Customer Paid card | SUM `payment_line_items.customer_charged` (excl. UF) | `test_data_flow_inventory::TestSummaryCards` (new) |
| FAM Match card | SUM `payment_line_items.match_amount` | same |
| FMNP Checks card | SUM `fmnp_entries.amount` | same |
| FAM Absorbed card | SUM `payment_line_items.method_amount` where method='Unallocated Funds' | `test_unallocated_funds`, `test_uf_in_vendor_reimbursement` |
| Vendor Reimbursement tab | per-vendor query joining receipts + per-method customer/match + UF method_amount + FMNP-external | `test_uf_in_vendor_reimbursement`, `test_v1_9_10_audit_fixes::TestVendorReimbursementCentsAccumulation`, `test_multi_receipt_same_vendor` |
| FAM Match Report tab | per-method SUM | `test_unallocated_funds::TestFAMMatchReportSurfacesAbsorbed` |
| Detailed Ledger tab | per-txn full breakdown (incl. Voided) | `test_data_flow_inventory::TestDetailedLedger` (new) |
| Transaction Log tab | each transaction row | `test_data_flow_inventory::TestTransactionLog` (new) |
| Activity Log tab | audit_log rows scoped to market day date | `test_v1_9_10_audit_fixes::TestActivityLogDeviceIdPreserved` |
| Geolocation tab | per-zip aggregation | `test_data_flow_inventory::TestGeolocation` (new) |
| FMNP Entries tab | fmnp_entries rows + payment_line_items FMNP-method rows | `test_data_flow_inventory::TestFMNPEntries` (new) |
| Generated Rewards tab | generated_rewards rows | `test_generated_rewards_report` |
| Charts tab | aggregations of the above | (covered indirectly) |
| Error Log tab | log file tail | `test_error_log_versioning` |

### Cloud sync (Google Sheets, 10 tabs)

Each in-app tab has a sync-side mirror collector in `fam/sync/data_collector.py`:

| Sheet tab | Collector | Pinned by |
|---|---|---|
| Vendor Reimbursement | `_collect_vendor_reimbursement` | `test_uf_in_vendor_reimbursement::TestCollectorShows*`, `test_v1_9_10_audit_fixes::TestVendorReimbursement*` |
| FAM Match Report | `_collect_fam_match` | `test_unallocated_funds::TestFAMMatchReportSurfacesAbsorbed` |
| Detailed Ledger | `_collect_detailed_ledger` | `test_data_flow_inventory::TestSyncDetailedLedger` (new) |
| Transaction Log | `_collect_transaction_log` | `test_data_flow_inventory::TestSyncTransactionLog` (new) |
| Activity Log | `_collect_activity_log` | `test_v1_9_10_audit_fixes::TestActivityLogDeviceIdPreserved` |
| Geolocation | `_collect_geolocation` | `test_data_flow_inventory::TestSyncGeolocation` (new) |
| FMNP Entries | `_collect_fmnp_entries` | `test_data_flow_inventory::TestSyncFMNPEntries` (new) |
| Market Day Summary | `_collect_market_day_summary` | `test_data_flow_inventory::TestSyncMarketDaySummary` (new) |
| Generated Rewards | `_collect_generated_rewards` | `test_generated_rewards_persistence` |
| Error Log | `_collect_error_log` | (covered) |

### Other outputs

| Output | Source | Pinned by |
|---|---|---|
| Printed receipt | `_build_receipt_data` in payment_screen | `test_data_flow_inventory::TestReceiptData` (new) |
| CSV exports | `fam/utils/export.py::export_*` | `test_export_reconciliation` + `test_data_flow_inventory::TestCSVExports` (new) |
| Text ledger | `fam/utils/export.py::write_ledger_backup` | `test_v1_9_10_audit_fixes::TestLedgerRotation` |
| Binary backup | `fam/database/backup.py::create_backup` | `test_v1_9_10_audit_fixes::TestBinaryBackupRestore` |
| Audit log (admin tab) | `get_audit_log` | `test_data_flow_inventory::TestAdminAuditLogDisplay` (new) |
| Match cap warnings | engine `result.match_was_capped` | (covered by admin fuzz cap-active tests) |
| Denom forfeit warning | `denom_overage_amt > 0` in `_update_summary` | (covered by `test_multi_denom_overage_two_vendors`) |

## Cross-replication invariants

| ID | Invariant | Enforced where | Test |
|---|---|---|---|
| **E3** | per-PLI: customer + match = method | DB trigger `chk_pli_invariant_*` (v28) | `test_app_restart_persistence` |
| **G1** | PLI INSERT/UPDATE: method/match/customer ≥ 0 | DB triggers `chk_payment_amount_*` (v4 INSERT, v31 UPDATE) | `test_v1_9_10_audit_fixes::TestDBTriggers` |
| **G2** | per-txn: SUM(method_amount) = receipt_total ± 1¢ | application layer | `test_v1_9_10_audit_fixes::TestPostConfirmSumEqualsReceipt` |
| **G3** | Voided is terminal | DB trigger `chk_transactions_voided_one_way` (v31) + python | `test_v1_9_10_audit_fixes::TestDBTriggers` |
| **L1** | per-vendor receipt = SUM PLI method on that vendor's transactions | save algorithm | `test_multi_receipt_same_vendor` |
| **R1** | row identity: Σ method-cols + FAM Match + FMNP_External = Total Due | report computation | `test_uf_in_vendor_reimbursement::TestCollectorShows*::test_row_identity_holds_after_uf_fix` |
| **D1** | DB ↔ in-app reports: same query, same result | rendered same way | `test_data_flow_inventory::TestInAppMirrorsSyncCollector` (new) |
| **A1** | every financial mutation → audit_log row | per-model calls | `test_audit_coverage_gaps`, `test_v1_9_10_audit_fixes::TestUpdateTransactionAuditsItself`, `TestFinancialSettingsAuditTrail` |
| **C1** | photo_drive_url survives re-save | `save_payment_line_items` | `test_v1_9_10_audit_fixes::TestPhotoDriveUrlPreservation` |
| **C2** | reactive UI refresh on every mutation | main_window signal wiring | `test_reports_refresh_after_adjust` |
| **F1** | denom forfeit: Σ_li method = receipt | engine + forfeit | covered by admin-fuzz |
| **W1** | reward rows never created retroactively | model contract | `test_generated_rewards_persistence`, `test_rewards_engine` |

## Coverage gaps to close (this session)

- [ ] Master end-to-end test exercising one txn through every output
- [ ] Receipt-data field parity (every column)
- [ ] CSV export field parity per tab
- [ ] In-app vs sync collector parity (same DB, both render the same numbers)
- [ ] Detailed ledger field parity
- [ ] Geolocation field parity
- [ ] FMNP entries field parity (both internal PLI and external)
- [ ] Market Day Summary field parity
- [ ] Transaction Log field parity
- [ ] Admin "Recent Audit Log" panel renders newest mutations
