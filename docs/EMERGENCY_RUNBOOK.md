# FAM Market Manager — Emergency Runbook

> **Print this and tape it inside the laptop case.**
>
> This is for a volunteer at the booth when something goes wrong
> and the project owner / coordinator cannot be reached. Each
> section is one symptom and the exact recovery steps.
>
> Last updated for v2.0.7 — 2026-05-07.

---

## How to use this page

Look down the **left column** for the symptom that matches what
you see. Follow the steps in the **right column** in order. Stop
when the problem is fixed. If you reach the end of a section
without success, go to the bottom of this page: **"When all
else fails."**

You don't need to understand why — just do the steps.

---

## 1. The app won't open

| What you see | What to do |
|---|---|
| You double-click and nothing happens, or you get a Windows error like "FAM Manager has stopped working." | 1. Wait 30 seconds. Double-click the icon again.<br>2. If still nothing: press **Ctrl + Shift + Esc** to open Task Manager. In the Processes list, look for any line starting with **"FAM Manager"**. If you see one, click it, then click **End task**. Wait 5 seconds. Try the icon again.<br>3. If it's still broken: skip to **Section 2** below — likely a stuck lock file. |

---

## 2. "Another FAM Market Manager instance is already running"

This means the app thinks another copy is running on the same
laptop. Almost always it's a stuck file from the previous time
you closed the app.

| Step | What to do |
|---|---|
| 1 | Look at the Windows taskbar. Is FAM Manager already open in another window? Click that window — you don't need a second copy. |
| 2 | If not open: **Ctrl + Shift + Esc** → Task Manager → Processes tab → look for **FAM Manager.exe** → click it → **End task**. Wait 5 seconds. Try launching again. |
| 3 | Still failing? Open File Explorer. Click in the address bar at the top, paste exactly: `%APPDATA%\FAM Market Manager` then press Enter. |
| 4 | Find the file **`.fam_instance.lock`** (note the dot at the start). |
| 5 | Right-click → **Delete**. (You'll be deleting a tiny lock file, not your data — your data is in `fam_data.db` which you should NOT delete.) |
| 6 | Launch FAM Manager from your shortcut. It'll work. |

---

## 3. "Update did not complete" dialog

This appears at startup after an auto-update went sideways. **Your
data is safe** — this dialog is a safety net, not a sign of damage.

| Step | What to do |
|---|---|
| 1 | Read the dialog. Note both version numbers (the one expected vs. the one actually running). |
| 2 | Click **OK** to dismiss. The app will work normally on whatever version it's on. |
| 3 | Try **Settings → Updates → Check for Updates** once more. Most of the time the second attempt succeeds. |
| 4 | If the second attempt fails the same way: do a **manual update** — see **Section 4** below. |
| 5 | If you need to roll back to the previous version: see **Section 5**. |

---

## 4. Manual update (when the in-app updater fails)

| Step | What to do |
|---|---|
| 1 | Quit FAM Manager. Click the X to close the window. |
| 2 | Open a browser. Go to: `https://github.com/seansaball/fam-market-manager/releases` |
| 3 | Find the most recent release. Click **`FAM_Manager_vX.Y.Z.zip`** to download. |
| 4 | When it finishes downloading: open your **Downloads** folder. Right-click the zip file → **Extract All** → **Extract**. |
| 5 | The extracted folder contains `FAM Manager.exe` and a folder called `_internal`. |
| 6 | Find your installed FAM Manager. Right-click your desktop shortcut → **Open file location**. |
| 7 | A File Explorer window opens showing `FAM Manager.exe`. **Note this folder.** |
| 8 | Go back to your extracted zip folder. Select all files (Ctrl + A), copy (Ctrl + C). |
| 9 | Go to the install folder from step 7. Paste (Ctrl + V). When Windows asks "replace these files?" click **Yes / Replace all**. |
| 10 | Launch FAM Manager from your shortcut. You're now on the new version. |

> Your data folder (`%APPDATA%\FAM Market Manager`) is NEVER touched
> by an update. The transactions, photos, and settings are
> completely separate from the install folder.

---

## 5. Roll back to the previous version

If the new version is broken and you need to revert:

| Step | What to do |
|---|---|
| 1 | Quit FAM Manager. |
| 2 | Open File Explorer, paste: `%APPDATA%\FAM Market Manager\_update_backup` and press Enter. |
| 3 | If this folder exists and contains `FAM Manager.exe`, you have a rollback available. If not, you'll need to download the older version manually. |
| 4 | Select all files in the `_update_backup` folder (Ctrl + A), copy (Ctrl + C). |
| 5 | Go to your install folder (right-click desktop shortcut → Open file location). |
| 6 | Paste, replace all when prompted. |
| 7 | Launch — you're back on the previous version. |

---

## 6. The internet is down (no Wi-Fi at the venue)

**This is not an emergency.** Keep working. The app is fully
functional offline.

| What you'll see | What it means |
|---|---|
| The colored chip in the title bar shows **gray "No network"** | This is normal offline. Your local data is fine. |
| You can't click "Sync to Cloud" | Correct — it's grayed out until internet returns. |
| Auto-sync stops trying | Correct — it'll resume automatically once internet is back. |

**At the booth:** Continue Receipt Intake → Payment → FMNP →
Adjustments as normal. Print receipts (if you have a printer).
The app saves everything to the local database AND a plain-text
ledger file every time you confirm.

**At end-of-day:**
1. Close the market day as normal
2. Take the laptop home (or any Wi-Fi hotspot)
3. Wait a minute — auto-sync runs every 5 minutes once Wi-Fi is back
4. Or click Settings → Cloud Sync → **Sync to Cloud** to push immediately
5. Confirm the chip turns green
6. Done

---

## 7. The sync chip is red / yellow

| What it means | What to do |
|---|---|
| Yellow = sync hasn't happened recently. | Click Settings → Cloud Sync → **Sync to Cloud**. Wait. If it goes green, you're done. |
| Red = the last sync attempt failed. | 1. Hover the chip — read the tooltip. The error message tells you what's wrong.<br>2. Open a browser, go to google.com — does it load? If no, internet is down (see Section 6).<br>3. If internet works: Settings → Cloud Sync → **Sync to Cloud** to retry.<br>4. If it keeps failing: open Help → Browse → search "sync failed" for detailed steps. |

---

## 8. Transactions are missing from the shared Google Sheet

The chip is green, you've synced, but you can't find your rows
on the sheet.

| Step | What to do |
|---|---|
| 1 | Click the **right sheet tab** in your browser. Receipts go to "Detailed Ledger." Vendor totals to "Vendor Reimbursement." FMNP to "FMNP Entries." |
| 2 | Refresh your browser (Ctrl + R). |
| 3 | Each row has columns called **`market_code`** and **`device_id`** at the far left. Filter the sheet by your market_code (the 4-letter code in your title bar). Your rows are tagged with this code. |
| 4 | If your market runs multiple laptops: each laptop has a unique `device_id`. The same customer label (C-005) might appear from two different laptops — they're different customers. |
| 5 | Help → System Status to find your laptop's device_id. |
| 6 | If you still can't find them: Settings → Cloud Sync → **Sync to Cloud** to force a re-push. Wait. Refresh browser. |

**Important:** Your local data is safe regardless of what's on
the sheet. Re-syncing later cannot lose anything.

---

## 9. The app is slow or hanging

| Step | What to do |
|---|---|
| 1 | Wait 30 seconds. Sometimes the app is doing a sync in the background and is briefly busy. |
| 2 | If it's still frozen: close the app (X button). If it won't close, Task Manager → End task. |
| 3 | Reopen. Most slowness clears with a restart. |
| 4 | If it's persistently slow even after restart: Help → System Status → look at "Database" size. Over 100 MB is unusual; mention this in your end-of-day report to the coordinator. |

---

## 10. I voided the wrong transaction

You can't un-void in the same session. Three options:

| Situation | What to do |
|---|---|
| The customer is still in front of you | Re-enter the transaction from scratch. Receipt Intake → Payment → confirm. The new transaction is fully valid; the void stays in the audit log as a correction. |
| The customer is gone but you remember details | Re-enter from scratch using the same customer label, same receipts, same payment amounts. Make a note in the receipt's notes field that this is a re-entry of a wrong-void. |
| You don't remember the details | Look up the original in Reports → Detailed Ledger by date. The voided row shows everything that was voided. Re-enter from that. |

---

## 11. I gave the customer reward tokens but the order was voided

You can't get the tokens back from the customer. The Generated
Rewards report keeps the row as a historical record.

| Step | What to do |
|---|---|
| 1 | Write down on a sticky note: customer label, how many tokens given, the void reason. |
| 2 | Hand the note to the coordinator at end-of-day with the deposit. |
| 3 | The coordinator reconciles tokens-out vs. inventory. A voided order with rewards counts against inventory; that's normal accounting. |

---

## 12. The customer wants to change a payment after I confirmed

Use **Adjustments**, not Void. Adjustments edit a confirmed
transaction; Voids cancel it entirely.

| Step | What to do |
|---|---|
| 1 | Sidebar → **Adjustments**. |
| 2 | Search for the transaction by customer label, vendor name, or receipt total. |
| 3 | Click **Edit**. The adjustment dialog opens with the existing payment lines. |
| 4 | Modify amounts, add lines, remove lines as needed. |
| 5 | If amounts no longer add up: click the **⚡ Auto-Distribute** button to balance everything to the receipt total automatically. **v2.0.7+ note**: Auto-Distribute only fills rows whose per-row ⚡ icon is **green** (Active). Grey ⚡ rows are Locked at the volunteer's typed value and won't be touched. Click a grey ⚡ to release the cap and let Auto-Distribute refill it. |
| 6 | Click **Save**. The original is preserved in the audit log; the new state is what reports show. |

> If the original transaction included a denominated payment method
> (Food Bucks, Food RX, FMNP), v2.0.7+ pops a safety dialog asking
> if you'd rather **Void Instead**.  That's usually the safer path
> for denominated transactions — Void the original and re-enter
> from Receipt Intake fresh.

> If the payment changes mean the customer hands over different
> physical money / FMNP / tokens, do that in person too.

---

## 12b. Hard block on the Payment screen — math doesn't reconcile

If the Payment screen refuses to confirm — a "Payment row mismatch"
warning, a per-vendor over- or under-allocation error, or any other
dialog that explicitly mentions the customer's daily FAM cap — and a
click of **⚡ Auto-Distribute** does not clear it: **break the
receipts into separate orders, one payment method per order.** This
is the cleanest, safest resolution and works for every cap-bound or
denomination-aware scenario you'll see at the booth.

| Step | What to do |
|---|---|
| 1 | Click **Cancel** on the Payment dialog. Do **not** click Confirm. |
| 2 | Return to **Receipt Intake**. Click **Discard** on the in-progress order (or **Pending Orders → Discard** if you saved it as a draft). The receipts you typed are not lost — re-add them in step 3. |
| 3 | Create a new order for the **same customer label** (returning-customer dropdown, or type the label). Add only the receipts that one payment method will cover (e.g. just the Food RX portion). |
| 4 | **Payment** → enter only that one method → ⚡ Auto-Distribute if needed → Confirm. |
| 5 | Repeat from step 3 with the remaining receipts and the next payment method (e.g. SNAP for the rest). |

The customer label being the same on every order means the daily
match cap accounting carries through automatically — the second
order sees the first's match already used.  Reports group by
customer label so the customer's day still rolls up to one row per
category.  Nothing is lost.

If even split orders don't reconcile (very rare): take a phone
photo of the dialog, write the customer's purchases on paper, and
confirm what you can.  The coordinator can reconcile the rest later
via Adjustments.

---

## 13. I need to send diagnostic info but have no internet

| Step | What to do |
|---|---|
| 1 | Help (sidebar) → **System Status** tab → click **"Copy Diagnostic Info"**. |
| 2 | Open Notepad (Start menu → type "notepad"). |
| 3 | Paste (Ctrl + V). |
| 4 | File → Save As → Desktop → name it `FAM diagnostic 2026-MM-DD.txt`. |
| 5 | When you next have internet, attach that file to an email. |
| 6 | Or: take a phone photo of the screen and send the photo when you have signal. |
| 7 | Or: copy the file to a USB stick and physically give it to the coordinator. |

---

## 14. I think the data is gone or corrupted

**Stop what you're doing.** Don't keep clicking. Read this fully
before acting.

| Check | What it means |
|---|---|
| Does the app still open? | Yes → not corrupted; data might just be filtered out. Check report date filters. |
| Does Reports → Detailed Ledger show **anything** for today? | If yes, you have data — the issue is filtering or display. |
| Does Reports show ZERO rows for a day you know happened? | Possible data loss. Stop. See "Restoring from backup" steps below. |

### Restoring from backup (last resort)

1. **Quit FAM Manager** (X to close)
2. Open File Explorer, paste: `%APPDATA%\FAM Market Manager` and press Enter
3. **Make a safety copy first.** Right-click the entire folder → Copy. Paste it on your Desktop. Name it `FAM Backup BEFORE RESTORE 2026-MM-DD`. If anything goes wrong, you can put this back.
4. Inside the original folder, find the `backups` subfolder.
5. Look for files named like `fam_2026-05-01_09-15-00.db`. Pick the most recent one that PRE-DATES your problem.
6. **Rename** the existing `fam_data.db` to `fam_data_BROKEN.db` (don't delete it).
7. **Copy** the chosen backup file from `backups\` up one level into `%APPDATA%\FAM Market Manager\`.
8. **Rename** that copy to exactly `fam_data.db`.
9. Launch FAM Manager. Open Reports — verify the data looks correct as of the backup time you picked.

If the most recent backup is from this morning and your problem
happened this afternoon, you'll need to re-enter transactions
between those times. Open `fam_ledger_backup.txt` in Notepad —
that file lists every confirmed transaction in plain English and
is your reference for what to re-enter.

---

## When all else fails

If none of the above works, and the app is unusable, and you have
to keep running the market:

1. **Switch to paper.** Use the receipt pads and a calculator.
2. Note every transaction: customer, vendor, receipt total, payment amounts.
3. After the market: hand the paper notes to the coordinator. They can either:
   - Re-enter into the next functional copy of the app
   - Add to the Google Sheet manually (the sheet is the merged record across all devices)
4. The shared Google Sheet from previous days is intact — your data is just not yet on it from today. Paper recovery is fully recoverable.

---

## Things that are NOT emergencies

These look scary but are normal:

| Looks like | Actually is |
|---|---|
| Gray "No network" indicator | Just offline. Keep working. |
| "Stale market day was auto-closed" dialog at startup | A safety feature. The previous day was left open; the app closed it. Open today's day normally. |
| FMNP starts "Inactive" after Load Defaults | Intentional. Most markets use FMNP Entry only, not as a payment method. Activate in Settings if you need it on the Payment Screen. |
| "Failed: Detailed Ledger" in tooltip after a sync attempt | One tab failed; others may have succeeded. Click Sync to Cloud to retry. |
| Sync indicator yellow for a few seconds | Normal during sync. Wait — should turn green within 30 seconds. |
| Lots of similar errors in the log around the same time | Probably one network outage logged 6× (one per sheet tab). The v2.0 update collapses these into a single line; older versions don't. |

---

## Where things live (quick reference)

| File / folder | What it is |
|---|---|
| `%APPDATA%\FAM Market Manager\fam_data.db` | The main database. **Source of truth.** |
| `%APPDATA%\FAM Market Manager\fam_ledger_backup.txt` | Plain-text human-readable log of every confirmed transaction. Open in Notepad. |
| `%APPDATA%\FAM Market Manager\backups\` | Auto-saved database backups (every 5 min during market days, plus market_open and market_close). |
| `%APPDATA%\FAM Market Manager\photos\` | Local cache of FMNP check photos. Uploaded to Drive when sync runs. |
| `%APPDATA%\FAM Market Manager\.fam_instance.lock` | Single-instance lock. Delete this if Section 2 says to. |
| `%APPDATA%\FAM Market Manager\_pending_update.json` | Pending-update marker. The app handles this; you don't touch it. |
| `%APPDATA%\FAM Market Manager\_update_backup\` | Previous version's files, kept for rollback. See Section 5. |
| `%APPDATA%\FAM Market Manager\_fam_update.log` | Updater's own log. Useful when an update fails. |
| `%APPDATA%\FAM Market Manager\fam_manager.log` | The application log. Last 30 lines are included in Help → System Status → Copy Diagnostic Info. |

To open `%APPDATA%\FAM Market Manager`: press **Windows + R**,
type or paste `%APPDATA%\FAM Market Manager`, press Enter.

---

## Diagnostic info template

When you email the coordinator, include:

```
Date and time of issue:
Market location:
What I was doing:
What I saw on screen (or attach a phone photo):
What I tried:
Did it work?:

[Paste the System Status diagnostic block here]
```

---

**Print this whole document. Tape it inside the laptop case or in
the volunteer binder. The internet might not be available when you
need it.**
