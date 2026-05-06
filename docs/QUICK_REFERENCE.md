# FAM Market Manager — Quick Reference

> **Print this on one page. Stick it behind the booth.**

---

## Sidebar

| Where | What |
|---|---|
| **Market** | Open / close the market day |
| **Receipt Intake** | Enter customer purchases |
| **Payment** | Process payment + FAM match |
| **Adjustments** | Fix mistakes after Confirm |
| **FMNP Entry** | Record FMNP checks (paper, vendor side) |
| **Reports** | Daily totals + exports |
| **Settings** | Markets / Vendors / Methods / Rewards / Sync |
| **Help** | Articles, troubleshooting, system status |

---

## A market day in 6 steps

1. **Market** → Open Market Day
2. **Receipt Intake** → enter receipts → "Add to Order"
3. **Payment** → enter amounts per method → ⚡ Auto-Distribute → Confirm
4. (repeat 2 & 3 for each customer)
5. **Reports** → check totals → Print or Export
6. **Market** → Close Market Day

---

## What the buttons do

| Button | What it does |
|---|---|
| **Auto-Distribute (⚡)** | Balances payment amounts to match the receipt total |
| **Confirm** | Saves the transaction. From here on, use Adjustments to change it |
| **Void** | Cancels the entire transaction. There is no un-void |
| **Adjust** | Edit a confirmed transaction (changes payment lines, not the receipt total) |
| **Discard** | Throw away an in-progress order before confirming |

---

## Sync chip — what the colors mean

| Color | Meaning | Do |
|---|---|---|
| 🟢 Green | Synced. Last attempt succeeded. | Nothing |
| 🟡 Yellow | Sync hasn't run recently / in progress. | Wait, or click Sync to Cloud |
| 🔴 Red | Last attempt failed. | Hover for the error. Try Sync to Cloud |
| ⚪ Gray "No network" | No internet. | Keep working — it's safe |

---

## Common errors → fast fixes

| Message | Fix |
|---|---|
| "Another instance is already running" | Task Manager → end "FAM Manager.exe" → relaunch. If still stuck: delete `%APPDATA%\FAM Market Manager\.fam_instance.lock` |
| "Update did not complete" | Click OK. Settings → Updates → Check for Updates again |
| "No network" gray chip | Normal offline. Sync resumes when Wi-Fi returns |
| Sync chip red, tooltip says "Network unavailable" | Check Wi-Fi. Once back: Sync to Cloud |
| Sync chip red, tooltip says "permission" | Sheet not shared with the service account. Coordinator action |
| "Stale market day was auto-closed" | Normal safety. Open today's day fresh |

---

## File locations (paste into File Explorer address bar)

| What | Where |
|---|---|
| Data folder | `%APPDATA%\FAM Market Manager\` |
| Database (don't delete!) | `%APPDATA%\FAM Market Manager\fam_data.db` |
| Plain-text ledger backup | `%APPDATA%\FAM Market Manager\fam_ledger_backup.txt` |
| Auto-saved DB backups | `%APPDATA%\FAM Market Manager\backups\` |
| Photos cache | `%APPDATA%\FAM Market Manager\photos\` |
| Updater log | `%APPDATA%\FAM Market Manager\_fam_update.log` |
| App log | `%APPDATA%\FAM Market Manager\fam_manager.log` |
| Rollback files | `%APPDATA%\FAM Market Manager\_update_backup\` |

---

## Sending diagnostic info

Help → **System Status** → **Copy Diagnostic Info** → paste into:

- An email (best)
- A note on your phone
- Notepad → save to Desktop → bring later on a USB stick

---

## When the customer wants to change a payment after Confirm

**Use Adjustments.** Sidebar → Adjustments → search for the
transaction → Edit → change amounts → ⚡ Auto-Distribute → Save.

---

## When you gave reward tokens but the order was voided

You can't get tokens back. Write a sticky note: customer label,
tokens given, void reason. Hand to the coordinator at end-of-day.

---

## When the internet is down

Keep working. The app is fully functional offline. Sync resumes
automatically when Wi-Fi returns. At end-of-day, take the laptop to
Wi-Fi and click Sync to Cloud.

---

## When all else fails

1. Print this page and the **Emergency Runbook** before market day
2. If the app stops working: switch to paper. Note customer / vendor / receipt total / payment amounts. Re-enter later
3. Send the coordinator a diagnostic info paste + photo of any error message
4. Your data is safer than you think — see the Emergency Runbook for restore steps

---

**Help → Browse → search anything in plain English.**
**Help → Troubleshooting → look up a symptom.**
**Help → System Status → see what the app sees.**

---

> Repo: `github.com/seansaball/fam-market-manager`
> Version: 2.0.1
