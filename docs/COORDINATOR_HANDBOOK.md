# FAM Market Manager — Coordinator Handbook

> **For the person responsible for keeping the system running** —
> typically one or two operations leads at the umbrella organization.
> If you've inherited responsibility from the project owner, this is
> what you need to know.
>
> Last updated for v2.0.6 — May 2026.

---

## Your role

You are the bridge between the volunteers at the booth and the
technical infrastructure. Volunteers handle market-day workflows.
The system handles money math, reports, and cloud sync. **You
handle:**

1. **Setup of new laptops** before they go to a market
2. **Credentials & access** for Google Sheets and Drive
3. **Multi-laptop deployments** (one market with two or more devices)
4. **Updates** rolled out to all laptops
5. **Reconciliation issues** at end-of-day or month
6. **Triage of escalations** from volunteers when the runbook isn't enough

You don't need to write code. You need to know where things live,
how to read a diagnostic, and when to escalate to the project owner.

---

## What every volunteer should have

Before market day:

- [ ] A working laptop with FAM Manager installed
- [ ] A printed copy of `EMERGENCY_RUNBOOK.md` in the laptop case
- [ ] The market_code preset in Settings → Markets (e.g., `BPFM`)
- [ ] A unique device tag in Settings → Preferences (e.g., `LB1`)
- [ ] The Google credentials file already loaded
- [ ] The Spreadsheet ID configured
- [ ] At least one successful test sync after install

If any of these are missing, do them BEFORE the laptop leaves your
hands.

---

## The first-time deployment of a new laptop

### Step 1: Install the app

Use the latest release zip from
`https://github.com/seansaball/fam-market-manager/releases`.

1. Download `FAM_Manager_vX.Y.Z.zip`
2. Right-click → Extract All
3. The extracted folder contains everything; copy it to `C:\Program Files\FAM Manager\` (or any folder)
4. Right-click `FAM Manager.exe` → Send to → Desktop (create shortcut)
5. Launch — Windows SmartScreen will warn on first run; click "More info" → "Run anyway"

> **Pre-flight check for image-cloned laptops (v2.0.6+).** If your fleet was deployed by cloning one Windows image across multiple laptops, verify each device has its own MachineGuid registry value before launching the app for the first time. Cloned images can share the same MachineGuid, which v2.0.6 refuses to launch with — it would silently corrupt cross-device cloud sync (every device's rows would collide on the same composite key).
>
> In an elevated PowerShell on each laptop:
> ```powershell
> (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' MachineGuid).MachineGuid
> ```
> You should see a unique GUID per device. If a value is missing, blank, or identical across two laptops, generate a fresh one before launching:
> ```powershell
> $g = [guid]::NewGuid().ToString()
> Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' MachineGuid $g
> ```
> If a device hits this on first launch, you'll see a "Device Identity Required" dialog with the same instructions — no data is touched, and the device runs normally once the registry value is set.

### Step 2: Initial settings

On first launch, the tutorial overlay walks through the basics. At
the end, choose **"Yes — Load Default Data"** to seed the app, or
**"No Thanks — Start Blank"** if you have a `.fam` settings file.

### Step 3: Configure identity

Settings → Markets:
- Make sure your market is in the list with the correct `market_code` (4-letter code)
- Or add a new one if needed

Settings → Preferences → Device Identity:
- Set a 1–4 character **Device Tag** unique to this laptop
- Suggestion: `LB1`, `LB2` for "Laptop 1", "Laptop 2"
- Or `BP1`, `BV2` to combine market and laptop number

### Step 4: Cloud sync

Settings → Cloud Sync:
1. Click **Load Credentials** → pick the Google service-account JSON file
2. Paste the **Spreadsheet ID** (from the URL of the shared sheet — between `/d/` and `/edit`)
3. Click **Save Sync Settings**
4. Click **Sync to Cloud** to test
5. Verify a row appears on the shared sheet

If the test sync fails with a permission error, the service account
email isn't shared on the sheet yet. See **Adding the service
account to the sheet** below.

### Step 5: Updates configuration

Settings → Updates:
- The repository URL is pre-filled with the official one
- Auto-check is on by default — leave it on
- Click **Check for Updates** once to verify the connection works

### Step 6: Verification checklist

Before the laptop goes to a market:

- [ ] Open and close a test market day successfully
- [ ] Enter a fake transaction; confirm it; void it
- [ ] Sync to Cloud — chip turns green, row appears on the shared sheet
- [ ] Help → System Status — verify everything is populated
- [ ] Click **Copy Diagnostic Info**, paste somewhere, sanity-check the values

---

## Google Sheets / Drive setup

### What the system uses

The app uses a Google **service account** — a non-human Google identity
that authenticates with a JSON file instead of a password. The
service account has its own email address (looks like
`fam-sync@your-project.iam.gserviceaccount.com`).

### Adding the service account to the sheet

1. Open the shared Google Sheet in a browser
2. Click **Share** (top right)
3. Paste the service account email
4. Set permission to **Editor**
5. **Uncheck** "Notify people" (the service account has no inbox)
6. Click **Share**

The same identity handles Drive automatically — when the app needs
to upload a photo, it'll create a folder if one doesn't exist.

### Generating a new credentials file

If you need to rotate or replace the credentials:

1. Open Google Cloud Console: https://console.cloud.google.com
2. Select the project that owns the existing service account
3. **IAM & Admin** → **Service Accounts**
4. Click the existing service account (or create a new one)
5. **Keys** tab → **Add Key** → **Create new key** → **JSON**
6. The browser downloads a `.json` file
7. Distribute this file to each laptop (USB stick, secure email, etc.)
8. On each laptop: Settings → Cloud Sync → Load Credentials → pick the new file → Save Sync Settings

The old credentials remain valid until you revoke them in Google
Cloud Console. Coordinate the rollover so no laptop is using a
revoked key.

---

## Multi-laptop deployments

When two or more laptops cover the same market:

### What works automatically

- Each laptop syncs independently to the same shared sheet
- Rows are tagged with `market_code` + `device_id`, so they don't
  overwrite each other
- Customer labels can repeat across laptops (C-005 on laptop A and
  C-005 on laptop B are different customers)
- Reports on the shared sheet merge everything; filter by `device_id`
  to see one laptop's contributions in isolation

### What you must set up

- Different `device_tag` on each laptop (Settings → Preferences)
- Same `market_code` for the same market
- Same credentials file
- Same Spreadsheet ID

### What NOT to do

- **Don't copy `fam_data.db` between laptops.** That clones identity
  and breaks the merge.
- **Don't change device_tag mid-day.** The shared sheet will see two
  "different" devices and rows will appear duplicated.
- **Don't run two copies on one laptop.** The instance lock prevents
  this; if it gets bypassed, the database can corrupt.

### End-of-day across laptops

Each laptop runs its own end-of-day. The shared sheet merges
everything. Pull totals from the sheet, not from individual laptops.

---

## Updates

### Recommended cadence

- **Test new releases on one laptop first.** Run a fake market day
  end-to-end before deploying.
- **Roll out between markets, not during.** A bad update during a
  market day is a recoverable but stressful event.
- Subscribe to GitHub release notifications so you know when new
  versions ship.

### How auto-update works

The app checks GitHub Releases once per day on launch. If there's a
new version:

1. Volunteers see a banner offering to update
2. If they click "Download & Install," the app:
   - Downloads the release zip
   - Verifies the file size
   - Writes a `_pending_update.json` marker
   - Quits and runs an installer batch script
   - The script copies new files over the old, then relaunches
3. The relaunched app reads the marker and confirms the version
4. If the version doesn't match, a "did not complete" dialog fires

### Disabling auto-update

If you'd rather control updates manually:

Settings → Updates → uncheck **"Auto-check for updates on launch"**.
Update each laptop manually using the steps in the Emergency Runbook.

### Pinning all laptops to the same version

You can do this with config management or simply by visiting each
laptop and not clicking "Download & Install" until you're ready.
There's no central control plane — but for ~5–20 laptops, manual
sequencing is fine.

### Rolling back

The app keeps the previous version's binaries at
`%APPDATA%\FAM Market Manager\_update_backup\`. If a new version is
broken:

1. Quit the app
2. Copy everything from `_update_backup` over the install folder
3. Launch — you're back on the previous version

The data folder is never touched, so you don't lose any market data
in the rollback.

---

## Reading a diagnostic

When a volunteer sends you their `Copy Diagnostic Info` paste, here's
what to look at:

```
App version       : 2.0.0         ← which version are they on?
Data directory    : C:\Users\...   ← any chance multiple users are conflicting?
Market code       : BPFM           ← matches their assignment?
Device ID         : LB1-abc123     ← is this their tag?
Open market day   : Bethel Park...
Instance lock     : held by pid... ← if "STALE", direct them to delete it
```

```
-- Sync ----
Last sync         : 2026-05-01... ← how recent? '(never)' is a red flag
Last sync error   : Network unav.. ← any error here is the smoking gun
Sheet configured  : yes            ← if 'no', the spreadsheet ID isn't saved
Credentials loaded: yes            ← if 'no', they didn't load the JSON
```

```
-- Updates ----
Last update check : ...
Update source     : github.com/... ← non-default URL is a misconfiguration
Pending update    : YES — target was 2.0.0   ← stuck mid-update
```

```
-- Rewards ----
Rewards           : enabled, 2 rules, 47 today
                                   ← matches their market's expectations?
```

```
-- Records ----
Confirmed txns    : 12             ← do these counts make sense?
Voided txns       : 0
Active FMNP rows  : 3
Market days total : 87
Audit log rows    : 2,401
```

```
-- Disk usage ----
Database          : 2.4 MB         ← anything over 100 MB is unusual
Backups folder    : 18.7 MB (24 files)  ← 0 files = backups disabled?
Oldest backup     : fam_2026-04-01... ← how far back can they recover?
```

```
-- Log tail ----
[last 30 lines of fam_manager.log]
                                   ← raw error context, look for traceback
```

### Common diagnostic patterns

| What you see | What it means |
|---|---|
| `Last sync error: Network unavailable` | A blip — usually clears on its own. If repeated for hours, the laptop's Wi-Fi or DNS is broken. |
| `Last sync: (never)` + credentials loaded | Spreadsheet ID is wrong, or service account isn't shared on the sheet. |
| `Pending update: YES` | The user has been stuck in a half-updated state. Walk them through manual update or rollback. |
| `Instance lock: STALE` | A previous crash left the lock file. Delete it (Section 2 in Emergency Runbook). |
| Database > 500 MB | Something is wrong — investigate. Most installs are under 50 MB. |
| Backups folder is 0 bytes / 0 files | Backup mechanism not running. The app should be auto-creating backups during market days; this is a real problem. |
| Audit log rows growing past 1M | Old data should be archived. Not urgent but worth flagging. |

### When to escalate

If a volunteer reports any of the following, escalate to the project
owner (or the technical contact):

- The app crashes on launch and Section 2 of the runbook doesn't fix it
- A confirmed transaction's totals don't match the receipt photo
- Reports show negative match dollars
- The audit log has gaps (missing entries between known events)
- The shared sheet shows rows from a `device_id` you don't recognize
- Anything involving money math being wrong

For everything else, follow this handbook + the Emergency Runbook
and the volunteer's diagnostic info.

---

## Monthly reconciliation

### Pull these reports

From the shared Google Sheet (best — merged across all laptops):

1. **Vendor Reimbursement** filtered by month → vendor totals for payment
2. **FAM Match Report** filtered by month → total match dollars by payment method
3. **FMNP Entries** filtered by month → FMNP checks taken to vendors
4. **Generated Rewards** filtered by month → tokens given to customers (if your market does rewards)

### Reconcile against

- Physical vendor payment records (your bookkeeping)
- FMNP check inventory before/after
- Reward token inventory before/after
- Bank deposits / cash counts

### Common discrepancies

| Symptom | Likely cause |
|---|---|
| Vendor total too low | A laptop didn't sync — check Last sync timestamps |
| FMNP count off by exact amount of one entry | A FMNP entry was voided after coordinator reviewed |
| Reward inventory short by a few tokens | A voided order with rewards — reward row stays as historical record by design |
| Match dollars exceed cap | Cap was raised mid-day. Check Settings → Markets audit log |

The audit log (Reports → Activity Log) records every change with
timestamp and operator. Use it as the authoritative history when
two records disagree.

---

## End-of-month checklist

- [ ] All laptops have synced their final market days
- [ ] Pull the four reports from the shared sheet
- [ ] Reconcile against physical inventory and bank records
- [ ] Archive the previous month's photos from Drive (move out of the active folder if needed)
- [ ] Review any voided orders for patterns (training opportunity?)
- [ ] Review any "Pending update" markers — should be cleared after a clean update
- [ ] Apply any pending app updates to the staged laptop, test, then roll out
- [ ] Confirm backup volumes — every laptop should have backup files in their `backups/` folder

---

## Decommissioning a laptop

When a laptop is being retired:

1. Sync one final time — confirm green chip
2. Make a copy of the entire `%APPDATA%\FAM Market Manager\` folder onto a USB stick
3. Verify the data exists on the shared Google Sheet
4. The laptop can now be wiped — the data is preserved in the sheet and the USB backup
5. Remove the laptop's `device_id` from any documentation; assign it to a new device only after wiping

---

## Onboarding a new volunteer

Day-of:

1. Show them the laptop case with the printed Emergency Runbook
2. Show them the sidebar — what each section does (Market, Receipt Intake, Payment, Adjustments, FMNP Entry, Reports, Settings, Help)
3. Walk through opening a market day, entering one fake transaction, confirming it, voiding it
4. Show them the sync indicator and what colors mean
5. Show them Help → Browse and how to search for a topic
6. Show them Help → System Status → "Copy Diagnostic Info" so they know how to send you info if something goes wrong
7. **Tell them: when in doubt, look at the printed runbook first, then send a diagnostic.**

---

## Common training points

- **The app saves before it syncs.** A red sync chip never means data
  is at risk locally.
- **Voids are permanent in the same session.** If you void wrongly,
  re-enter the transaction.
- **Adjustments edit a confirmed transaction.** Use this for "wrong
  payment method" situations.
- **The customer label is a tag, not a name.** It's how the app links
  multiple receipts from one customer in the same day.
- **Match math is automatic.** Don't try to override it — if the math
  looks wrong, something else is wrong (cap reached? FMNP not active?).

---

## Project-owner contact information

> **Fill this in for your organization** before printing this
> handbook:

- **Project owner name:**
- **Email:**
- **Phone:**
- **Best hours to reach:**
- **GitHub repo:** https://github.com/seansaball/fam-market-manager
- **Issue tracker:** https://github.com/seansaball/fam-market-manager/issues

---

## When to escalate (recap)

| Situation | Action |
|---|---|
| Volunteer needs help during market day | Refer to printed Emergency Runbook |
| Volunteer needs help between markets | Use this handbook + their diagnostic info |
| Money math looks wrong | Pull the audit log first; if it confirms the discrepancy, escalate to project owner |
| Database file appears corrupt | Make a safety copy → try restore-from-backup steps → if that fails, escalate |
| Multiple laptops report the same issue at the same time | Likely a Google API outage; check status.cloud.google.com — wait 30 min before escalating |
| Anything you've never seen before | Send the volunteer's diagnostic + your guess to the project owner |

---

## Versioning notes

This handbook is for **v2.0.6**. Major changes recently:

- **v2.0.6**: Production season release.  Per-vendor payment-method eligibility (Settings → Vendors), configurable rewards engine (Settings → Rewards), redesigned Payment Confirmation Dialog.  Multi-workstation cloud sync hardened end-to-end — settings changes propagate to the shared sheet, closed-day mutations sync correctly, Vendor Reimbursement cleanup is multi-market-aware, reset preserves other devices' rows, market renames protected against code-shift orphaning.  Photo dedup cache cleaned on void/delete/replace.  Schema v33 → v34 (additive).  3,387 tests.
- v2.0.1: Pre-deployment hardening pass — InstanceLock wired into app.py, narrow-scope auto-sync no longer deletes historical rows, FMNP face-value reporting, three-gate reset, defensive UF triggers
- **v2.0.0**: First production release.  Brings the comprehensive hardening + documentation pass: cross-platform single-instance lock, atomic photo writes, offline-quiet sync logging, customer rewards add-on, 5 hardening sessions (+277 new tests), 11 new in-app help articles, 6 new troubleshooting flows, and three new printable docs (Emergency Runbook, Coordinator Handbook, Quick Reference).  Skipped the v1.9.10 internal designation and went straight from v1.9.9 to v2.0 to signal the production milestone.
- v1.9.9: Composite-key sync, multi-device support, audited vendor/method CRUD
- v1.9.8: FMNP-as-payment improvements, Adjustments overhaul
- v1.9.7: Drive photo verification (tri-state)
- v1.9.6: TLS / certifi fix for frozen builds
- v1.9.5: Pending-update marker introduced
- v1.9.3: Auto-update zip layout fix

Older versions may behave slightly differently. If a volunteer is on
an older version, check the version chip in Help → System Status and
prioritize their update.

---

## Need to update this document?

The canonical copy lives at `docs/COORDINATOR_HANDBOOK.md` in the
GitHub repo. Open a pull request or email the project owner with
suggested changes.
