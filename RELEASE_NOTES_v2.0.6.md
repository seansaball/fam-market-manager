# FAM Market Manager v2.0.6

**Previous release:** v1.9.8 (April 27, 2026)
**Schema version:** 34
**Tests:** 3,387 across 60+ files

---

This release covers everything since v1.9.8. The headline additions are per-vendor payment-method eligibility, a configurable rewards engine, and a redesigned Payment Confirmation Dialog. A number of behind-the-scenes improvements to reliability, reporting, and cloud sync are included as well.

---

## What's New

### Per-Vendor Payment-Method Eligibility

Market operations managers can now configure, per vendor, exactly which payment methods that vendor is eligible to receive. The configuration lives in **Settings → Vendors → "Eligible Payment Methods"**, plus a per-market checkbox grid in **Settings → Markets → "Assign Payment Methods."**

FAM-supported payment programs have real-world eligibility rules — Food Bucks are produce-only, FMNP is for FMNP-eligible vendors, SNAP has its own scope. Previously the app showed every payment method against every vendor, so a volunteer at a baked-goods booth could accidentally accept Food Bucks. Now ineligible methods don't appear as options against ineligible vendors.

- If a vendor's eligibility changes mid-season, the manager updates one checkbox; every laptop gets the new rule on next sync.
- Multi-vendor receipts work cleanly: each vendor's portion is recorded as its own transaction under one customer order, so a $5 FMNP check split across two vendors lands as two denominated rows on the correct vendors.
- The Vendor Reimbursement report no longer attributes phantom payments to vendors who never accepted those methods.

### Configurable Rewards Engine

A new rewards engine in **Settings → Rewards** lets the market operations manager configure rules of the form:

> "For every $X spent on [source method], hand out N units of [reward method] worth $Y each."

The default rule is the classic **$5 SNAP → 1 × $2 JH Food Bucks**, but trigger method, threshold, reward method, reward unit value, and quantity per increment are all editable. The Payment Confirmation Dialog tells the volunteer in real time exactly how many tokens to physically hand over.

- A new **Generated Rewards** tab in Reports lists every reward issued — by customer, date, source method, reward method — for end-of-season reconciliation against your token inventory.
- Adjusting a rule mid-season doesn't alter previously-issued rewards. The historical record reflects what the cashier actually handed out, not a recomputation.
- Rules can be disabled without losing history.

### Payment Confirmation Dialog

The confirm flow has been redesigned to walk the volunteer through what they need to physically do before the transaction commits.

- A **marching-ants action zone** highlights the steps the volunteer needs to complete.
- **Per-method action rows** — each payment method on the order gets its own line item: "Collect $10.00 SNAP," "Hand over 2 × $2.00 Food Bucks tokens," "Take 1 × $5.00 FMNP check."
- **Required acknowledgment checkboxes** for SNAP/EBT — the Confirm button stays disabled until the volunteer ticks "Yes, I've completed the SNAP swipe on the EBT terminal."
- A **customer impact summary** shows what to collect and what to give back, in dollars, before the transaction commits.

### Other UI Improvements

- **Per-vendor "Remaining" column** on multi-vendor orders so the volunteer sees at a glance how much is left to allocate against each vendor.
- **✓ / ✗ eligibility indicators** next to each payment method on a vendor row.
- **Smart Auto-Distribute** button fills in a payment breakdown that respects vendor eligibility, denomination rules, and the customer's daily match cap.
- **Customer-gone path:** if a volunteer realizes mid-Adjustment that the customer has already left, the app offers a clear "log as Unallocated Funds" option that records FAM's absorbed loss without leaving the transaction in a broken state.
- **Returning customer dropdown** — pull up a customer who's already shopped today; their match-cap accounting carries through automatically.
- **Pending Orders dropdown** — pause an order mid-shopping and resume it later from any laptop.
- **Sync indicator** in the header shows the live state: "Last sync OK," "Syncing now," or "No network — data safe locally."

---

## Reports & Analytics

- **Zip Code column** added to the Detailed Ledger, Transaction Log, Generated Rewards, and FMNP Entries reports — both internal and synced to Google Sheets. Coordinators can pivot on (Zip Code × Vendor × Payment Method) to see which customer demographics use which programs at which vendors.
- **Vendor Reimbursement** rebuilt with accurate per-vendor totals, with FMNP External (manually-entered checks) as a separate column so the reimbursement check matches reality.
- **FAM Match vs FAM Absorbed** — two distinct columns. "Match" is the multiplier on customer payment; "Absorbed" is the customer-gone Unallocated Funds path.
- **Detailed Ledger** shows per-transaction granularity — each vendor's slice of a multi-vendor receipt is its own row.
- **Activity Log** with audit trail of every CREATE / CONFIRM / ADJUST / VOID / OPEN / CLOSE / REOPEN action across the app, filterable by action type and date range.
- **Charts** (matplotlib) and a **Folium-rendered customer map** for at-a-glance insights.
- **CSV export** for every report with formula-injection sanitization.

## Customer Orders & Daily Match Cap

- **Per-customer-per-day match cap** is enforced automatically. A customer who shops at multiple vendors throughout the day cannot accumulate more than the configured daily limit (default $100, set per-market in Settings).
- **Multi-laptop awareness.** Customer labels carry a 3-character device tag (e.g. `C-005-LB1`) so coordinators can tell which laptop captured which customer.
- **Stale market day auto-close.** A market day left open from a prior date auto-closes on launch with a clear notification of which days were closed and why.

## FMNP Support

- **Dedicated FMNP Entry screen** (separate from the Payment screen) for batch entering manual FMNP checks. Photo-friendly, multi-check, vendor selection, notes.
- **Activation safeguards.** Activating FMNP for the Payment Screen — either globally or per market — surfaces a confirmation dialog explaining that FAM does not currently accept or cash physical FMNP checks, with guidance to leave it inactive unless explicitly instructed.
- **FMNP face-value reporting.** The synced FMNP Entries sheet shows the face value of physical scrip handed over so vendors redeeming checks see numbers that match what they're cashing.
- **Photo deduplication** within an FMNP entry (refuses to attach the same image twice) and across entries (warns if the same content was uploaded earlier).

## Photo Receipts

- **Photo capture** on Payment Screen and FMNP Entry, with optional / mandatory enforcement per payment method (set in Settings → Payment Methods).
- **Three-layer deduplication** — by stored path, by content hash, and by Drive URL.
- **Atomic writes** — photo files are saved to a temporary path and atomically renamed on success.
- **Drive sync with retry resilience** — transient network errors don't cause re-upload storms.

## Cloud Sync (Google Sheets + Google Drive)

- **One-way sync to a shared Google Spreadsheet** with one tab per report.
- **Photo upload to Google Drive** with folder hierarchy: `Root > Market Name > Payment Type`.
- **Automatic re-upload of trashed photos** — if someone accidentally deletes a Drive photo, the app detects it and re-uploads on next sync.
- **Offline tolerance** — sync attempts during an outage log a single warning and the app continues working normally.
- **Visible sync indicator** in the app's header.

## Multi-Workstation Cloud Sync Invariant

Pre-release end-to-end testing surfaced a class of cases where local data mutations didn't always reach the shared Google Sheet — or reached it in ways that conflicted with other workstations syncing to the same sheet. v2.0.6 closes those gaps systematically. Every local mutation that affects a cloud-bound row now triggers a sync trigger that reaches every relevant tab, and every cleanup respects per-device ownership.

- **Settings changes propagate to the cloud.** Vendor / market / payment-method add, edit, toggle, and delete actions, plus the four assignment dialogs (vendor↔market, market↔method, vendor↔method) and reward-rule add / toggle / delete, now trigger a full-scope cloud sync. Pre-fix, settings-only changes such as renaming a vendor only reached the cloud when an unrelated mutation happened to fire a sync — sometimes hours later, with stale data on the shared sheet in the meantime.
- **Closed market day mutations sync correctly.** FMNP entries added to closed market days (paper checks delivered later, end-of-month batch entry) and Admin adjustments / voids on historical receipts now scope the sync to the affected day rather than the currently-open day. Pre-fix the auto-sync narrowed to the open day and silently skipped closed-day mutations.
- **Vendor Reimbursement cleanup is multi-market-aware.** The whole-dataset cleanup in the cloud-sheet upsert path now correctly removes stale rows owned by this device across **all** markets. Pre-fix the cleanup gate over-restricted to the device's currently-configured primary market, leaving stale rows from other markets stranded on the shared sheet indefinitely.
- **Reset preserves other devices' data.** Resetting a local app instance now triggers an immediate full-scope cloud sync that drops only this device's rows from the shared sheet. Other workstations' data — Vendor Reimbursement entries from a co-located laptop at the same market, FAM Match rows from another device — is preserved untouched. The reset success dialog explicitly confirms this multi-workstation safety guarantee.
- **Market renames protected.** Renaming a market in a way that would change its derived market_code (e.g. "Bethel Park" → "Pittsburgh South") is now **blocked** once the market has any market_days on record. Code changes orphan existing rows on the shared sheet under the old code, with no automated cleanup possible for per-day report tabs. Code-stable renames such as typo fixes and casing changes ("Bethal Park" → "Bethel Park") are always allowed since the cloud identity doesn't move. Brand-new markets with no recorded days can still be freely renamed with an informational confirmation dialog.

## Receipt Photos & Drive Sync (Edit Hardening)

- **Photo dedup cache cleaned on void / delete / replace.** Voiding a transaction, soft-deleting an FMNP entry, or replacing an FMNP entry's image now drops the corresponding rows from the local photo-hash cache when no other active record references them. Re-uploading the same image content to a fresh transaction or entry now triggers a fresh Drive upload instead of short-circuiting to the now-VOID-renamed file. Photos genuinely shared by another active record stay cached so dedup keeps working for them.
- **FMNP photo replace re-evaluates the Drive URL.** Editing an FMNP entry to clear and re-attach a photo now clears `photo_drive_url` as part of the same update, so the next sync correctly re-evaluates the upload. Pre-fix the upload pipeline was never even triggered for single-photo replacements because the URL count matched the path count, leaving the entry stranded against the old Drive file.

## Settings & Configuration

- **Settings import/export** via `.fam` files — coordinators can prepare a configuration on one laptop and distribute it to all season laptops in seconds.
- **Reset to Defaults** with three-gate confirmation (warning + warning + typed RESET) and a pre-reset database backup.
- **New markets auto-populate** with the operator's full set of vendors and payment methods checked. Operators untick what doesn't apply rather than starting from a blank slate.

## Help & Self-Service

- **51 curated articles** across 8 categories with live keyword search.
- **Animated 5-stage walkthrough** ("Your Day at the Market") with hand-drawn pictograms.
- **10 symptom-based troubleshooting flows.**
- **System Status** tab with live diagnostic snapshot and one-click "Copy Diagnostic Info."

## Auto-Update

- **Check for updates** from Settings — the app polls the official GitHub release channel on a 6-hour cadence.
- **Pre-install safety check** — the app refuses to install an update while a market day is open.
- **Sticky update notifications** — "Remind me later" re-fires the popup after 6 hours; "Ignore for this version" stays silent until a newer version is released.

---

## Reliability & Data Integrity

A number of changes since v1.9.8 strengthen the foundation the app runs on. Most are invisible during normal use but show up when something goes wrong.

- **Single-instance lock** prevents two app instances from operating on the same database simultaneously — useful at multi-laptop markets where laptops sometimes share a network folder.
- **Per-line invariant** enforced at the database level: every payment-method row satisfies `customer_charged + match_amount = method_amount`.
- **Voided is terminal** — database-trigger enforced. A voided transaction stays voided.
- **Atomic ledger backup** on every payment confirm and adjustment, with rotation through 5 historical snapshots.
- **Pre-migration database snapshots** before any schema upgrade. Backup failure now blocks the migration entirely, never proceeds without rollback safety.
- **Auto-backup** on market open, market close, and on a periodic timer. Retention is per-market so a low-volume market's history can't be evicted by a high-volume market's activity.
- **Auto-update channel pinned** to the official GitHub repository; failed installs verify the rollback's authenticity (SHA-256 hash check) before restoring.
- **Drive verification tri-state** — checking whether a photo is still on Drive distinguishes "yes / no / can't tell right now," so transient network errors don't cause the app to mistakenly think every photo was deleted.
- **Sheet payload chunking** — large syncs after a long offline period are automatically split into batches that fit Google's per-call limits.
- **Offline tolerance** — when the network is down, the app logs one warning per sync cycle (not one per tab); when connectivity returns, the next sync catches up automatically.
- **Error Log tab auto-refreshes on every selection.** New log entries appended during a sync cycle (warnings, recoveries, transient errors) now appear in the local UI without a manual refresh click — keeps the local view aligned with the cloud Error Log sheet.
- **Database fragmentation hint is size-aware.** The "consider running VACUUM" advisory only fires on databases that have grown past ~4MB and accumulated more than 30% reclaimable space — meaningful at production scale, silent on freshly-reset or test installs where the absolute number of unreclaimed bytes is negligible.
- **Device-identity dialog is actionable.** When a workstation cannot launch because its Windows MachineGuid registry value is missing or shared (image-cloned laptops), the dialog now points at the actual cause and provides copy-paste PowerShell remediation, instead of the generic "Database Error" message.

---

## Database & Performance

- Schema upgraded from v22 to **v34** through 12 forward migrations. First launch on v2.0.6 runs migrations forward automatically; a pre-migration backup is written before any change.
- Composite scaling indexes added for year-over-year deployment performance.
- WAL synchronous + checkpoint thresholds tuned for the market-day workload.
- Existing markets that were created on a previous version with empty vendor / payment-method assignments get back-filled automatically on first launch.

---

## Test Suite

The test suite has grown from **1,822 tests** (v1.9.8) to **3,387 tests** across 60+ files. Coverage includes per-vendor binding scenarios, the rewards engine, the redesigned Payment Confirmation Dialog, customer-gone Unallocated Funds flow, daily match-cap enforcement across multi-laptop deployments, multi-vendor receipt distribution, and the cloud-sync resilience paths.

> **Three-way reconciliation verified end-to-end.** For every tested transaction, the system confirms that Database records = Ledger backup = Google Sheets sync output. No penny is lost, duplicated, or misattributed.

---

## Installation (New PC)

1. Download **FAM_Manager_v2.0.6.zip** from the GitHub release page
2. Extract to any folder
3. Double-click **FAM Manager.exe**
4. Follow the tutorial → configure markets, vendors, and payment methods in **Settings**

Windows SmartScreen may prompt on first run. Click "More info" then "Run anyway."

## Upgrading (Existing PC)

**Option A — In-App Update (recommended):**
1. Open the app and go to **Settings → Updates**
2. Click **"Check for Updates"**
3. Click **"Download & Install"** — the app handles the rest

**Option B — Manual:**
1. Download the new zip from the GitHub release page
2. Replace the old application folder with the extracted contents
3. Launch — the app finds your existing data automatically

> **Data safety:** Your existing data is preserved automatically. Cloud credentials, Drive folder, and update channel settings survive the upgrade. The database upgrade runs on first launch, and a safety backup is created before any migration starts.

> **What you'll see on existing Sheets:** Synced Sheets get a new **Zip Code** column appended automatically on the next sync. Historical rows show the column empty (the data was never collected for those rows); new rows from v2.0.6 onward populate it from each customer's order.

### Pre-Flight Check for Image-Cloned Laptops

If your fleet was deployed by cloning one Windows image across multiple laptops, take 30 seconds to verify each device has its own MachineGuid before upgrading. v2.0.6 refuses to launch on devices that share a synthetic device identity, because shared identities silently corrupt cross-device cloud sync.

In an elevated PowerShell, on each device:

```powershell
(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' MachineGuid).MachineGuid
```

You should see a unique GUID per device. If the value is missing, blank, or identical across laptops, generate a fresh one before launching v2.0.6:

```powershell
$g = [guid]::NewGuid().ToString()
Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' MachineGuid $g
```

If a device hits this on first launch of v2.0.6, you'll see a "Device Identity Required" dialog with the same instructions — no data is touched, and the device runs normally once the registry value is set.

---

## Known Limitations

- No code-signing yet — Windows SmartScreen may show a "publisher unknown" warning on first install.
- FMNP method is hardcoded to the literal name "FMNP" in two report queries. Renaming the FMNP method in Settings is not supported — the activation warning explicitly asks operators not to.
- Vendor Reimbursement does not auto-merge across devices. When two laptops sync at the same market, each laptop's contribution to a vendor appears as its own row on the synced sheet. Coordinators sum across rows when cutting checks.
