# FAM Market Manager v2.1.1

**Previous release:** v2.1.0 (June 12, 2026)
**Release date:** July 30, 2026
**Schema version:** 41 (unchanged — no migration)

A small point release with a single fix. No action needed beyond the update.

---

## Fix — payment methods can now use sub-dollar denominations

A payment method's denomination could not be set below **$1.00** — typing a
smaller value (e.g. **$0.50** for JH Tokens) silently snapped back to $1.00.
Sub-dollar scrip is real, so the minimum is now **$0.01**, and any value in
$0.01 steps is accepted (JH Tokens at $0.50, $0.25 program coupons, etc.).

- Set it under **Settings → Payment Methods → (edit method) → Denomination**.
- Reported from the field (JH Tokens in $0.50 increments).
- No other behavior changed. All denomination math (token counts, whole-multiple
  entry, reimbursement) was already exact to the cent, so smaller denominations
  flow through cleanly.

## Upgrade

Use the in-app updater: **Settings → Updates → Check for Updates → Download &
Install**. No schema migration runs. Existing data, settings, and cloud
configuration are unchanged. Manual zip replacement from the GitHub release
page also works if needed.
