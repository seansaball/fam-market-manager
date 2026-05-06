"""Sync manager — orchestrates data collection and backend sync."""

import logging
import platform
import time
from fam.sync.base import SyncBackend, SyncResult
from fam.utils.timezone import eastern_timestamp
from fam.utils.app_settings import (
    get_market_code, get_device_id, set_setting, get_setting,
)

logger = logging.getLogger('fam.sync.manager')


class SyncManager:
    """Coordinates data collection and sync to the configured backend."""

    # Maps sheet tab names to their composite key columns for upsert.
    # market_code + device_id are always the first two key columns
    # (except Agent Tracker which is keyed by device_id alone).
    SHEET_KEYS: dict[str, list[str]] = {
        'Vendor Reimbursement': ['market_code', 'device_id', 'Market Name', 'Vendor'],
        'FAM Match Report':     ['market_code', 'device_id', 'Payment Method', 'Date'],
        'Detailed Ledger':      ['market_code', 'device_id', 'Transaction ID'],
        'Transaction Log':      ['market_code', 'device_id', 'Time', 'Transaction',
                                 'Action'],
        'Activity Log':         ['market_code', 'device_id', 'Timestamp',
                                 'Record ID', 'Action'],
        'Geolocation':          ['market_code', 'device_id', 'Zip Code', 'Date'],
        'FMNP Entries':         ['market_code', 'device_id', 'Entry ID'],
        'Market Day Summary':   ['market_code', 'device_id', 'Date'],
        # v2.0.5: Error Log key drops Message because Message now
        # contains the FULL multi-line traceback (was just the first
        # line pre-fix).  A multi-KB key cell is wasteful and brittle
        # against newline / whitespace normalisation.  Timestamp +
        # Module + Level is sufficient: two errors landing in the same
        # second from the same module at the same level are the same
        # event by every operational definition.  Existing Sheets
        # with the old key get their rows refreshed on the next sync
        # via the WHOLE_DATASET_TABS delete_stale=True semantics.
        'Error Log':            ['market_code', 'device_id', 'Timestamp', 'Module', 'Level'],
        # v1.9.10: customer-facing rewards add-on (informational
        # only — NOT financial).  Key combo (market_code, device_id,
        # Date, Customer, Source Method, Reward Method) — one row
        # per (order × rule that fired).
        'Generated Rewards':    ['market_code', 'device_id', 'Date',
                                 'Customer', 'Source Method',
                                 'Reward Method'],
        'Agent Tracker':        ['device_id'],
    }

    def __init__(self, backend: SyncBackend, throttle_writes: bool = True):
        self._backend = backend
        self._throttle_writes = throttle_writes

    def is_available(self) -> bool:
        """Return True if the backend is configured."""
        return self._backend.is_configured()

    def sync_all(self, report_data: dict[str, list[dict]],
                 delete_stale: bool = True
                 ) -> dict[str, SyncResult]:
        """Sync all provided sheet tabs.

        *report_data* maps sheet name → list[dict] (with identity
        columns already prepended by data_collector).

        *delete_stale*: when False, the backend's stale-row cleanup is
        skipped on the per-md tabs so a narrow-scope auto-sync cannot
        delete historical rows.  Vendor Reimbursement (whole-dataset)
        always honours delete_stale=True since the collector always
        runs across all market days.

        Returns ``{sheet_name: SyncResult}``.  Never raises.
        """
        # Per-md tabs honour the caller's delete_stale.  Vendor
        # Reimbursement is whole-dataset by design and always wants
        # stale rows pruned.
        WHOLE_DATASET_TABS = frozenset({'Vendor Reimbursement', 'Error Log'})
        results: dict[str, SyncResult] = {}

        # Reset the backend's per-cycle offline short-circuit so
        # one bad cycle doesn't permanently mute the next.  The
        # backend uses this to skip retrying after the first
        # offline failure within a cycle (saves 5×wait-and-fail
        # iterations during an outage).
        reset = getattr(self._backend, 'reset_offline_state', None)
        if callable(reset):
            reset()

        for idx, (sheet_name, rows) in enumerate(report_data.items()):
            key_cols = self.SHEET_KEYS.get(
                sheet_name,
                ['market_code', 'device_id'],
            )
            tab_delete_stale = (
                True if sheet_name in WHOLE_DATASET_TABS else delete_stale
            )
            try:
                result = self._backend.upsert_rows(
                    sheet_name, rows, key_cols,
                    delete_stale=tab_delete_stale)
                results[sheet_name] = result
            except Exception as e:
                logger.exception("sync_all: %s failed", sheet_name)
                results[sheet_name] = SyncResult(
                    success=False, error=str(e))

            # Throttle between tabs to stay within Google Sheets
            # write-request quota (60 writes/min/user).  Skip the
            # throttle when we've gone offline — every remaining
            # tab will short-circuit instantly anyway, and a
            # 6-second pause during an outage just delays the
            # eventual error UI.
            if (idx < len(report_data) - 1
                    and self._throttle_writes
                    and not getattr(results[sheet_name], 'offline', False)):
                time.sleep(1.0)

        # Record outcome in app_settings.
        #
        # v1.9.10 follow-up (2026-05-01): only update ``last_sync_at``
        # on a fully-clean run.  When a tab fails, the UI's "Last
        # sync: HH:MM" indicator was misleading — it advanced to
        # "now" even though some tabs hadn't shipped, hiding the
        # partial-failure state from the operator.  ``last_sync_at``
        # is the canonical "all data is on Sheets through this
        # timestamp" anchor; partial failures don't satisfy that.
        # ``last_sync_error`` always reflects the latest run.
        failed = [n for n, r in results.items() if not r.success]
        if not failed:
            set_setting('last_sync_at', eastern_timestamp())
            set_setting('last_sync_error', '')
        else:
            set_setting('last_sync_error',
                        f"Failed: {', '.join(failed)}")

        total_rows = sum(r.rows_synced for r in results.values()
                         if r.success)

        # Coalesce per-tab offline failures into a single summary
        # line.  Without this, a 5-minute internet blip during a
        # multi-tab sync produces ~6× duplicate "Sync complete:
        # 0 rows, 6 failures" entries — useless noise.  When
        # *every* failure is offline-class, log ONE warning that
        # captures the situation; otherwise log the per-tab info
        # line as before.
        offline_failures = [
            n for n, r in results.items()
            if not r.success and getattr(r, 'offline', False)
        ]
        if offline_failures and len(offline_failures) == len(failed):
            logger.warning(
                "Sync skipped — network unavailable (%d tab(s) "
                "deferred; will retry on next sync)",
                len(offline_failures))
        else:
            logger.info("Sync complete: %d tabs, %d rows, %d failures",
                        len(results), total_rows, len(failed))

        # Sync agent tracker as the final step — reports this sync's outcome
        try:
            tracker_result = self._sync_agent_tracker(results, report_data)
            results['Agent Tracker'] = tracker_result
        except Exception as e:
            logger.exception("Agent tracker sync failed")
            results['Agent Tracker'] = SyncResult(
                success=False, error=str(e))

        return results

    def _sync_agent_tracker(self, data_results: dict[str, SyncResult],
                             report_data: dict[str, list[dict]]
                             ) -> SyncResult:
        """Sync a single 'Agent Tracker' row with this device's metadata.

        Called after data tabs finish so the row reflects current sync
        results.  One row per device_id, updated on every sync.

        market_code and Market Name are intentionally omitted — a single
        device can serve multiple markets on different days, so there is
        no 1:1 correlation.
        """
        from fam import __version__

        did = get_device_id() or ''

        # Summarize data sync results
        success_count = sum(1 for r in data_results.values() if r.success)
        total_tabs = len(data_results)
        failed = [n for n, r in data_results.items() if not r.success]
        status = 'OK' if not failed else 'Error'

        # Total Rows = total data rows managed across all tabs (not just
        # the rows that changed).  The dirty-check optimisation means
        # rows_synced can be 0 when nothing changed, but the operator
        # still wants to see how much data is being tracked.
        total_rows = sum(len(rows) for rows in report_data.values())

        row = {
            'device_id': did,
            'App Version': __version__,
            'Last Sync': eastern_timestamp(),
            'Hostname': platform.node(),
            'OS': platform.platform(),
            'Status': status,
            'Sheets Synced': f"{success_count}/{total_tabs}",
            'Total Rows': total_rows,
            'Errors': ', '.join(failed) if failed else '',
        }

        return self._backend.upsert_rows(
            'Agent Tracker', [row], ['device_id'])

    def clear_market_data(self) -> dict[str, SyncResult]:
        """Delete all rows for this market's identity across all tabs."""
        mc = get_market_code() or ''
        did = get_device_id() or ''
        results: dict[str, SyncResult] = {}

        for sheet_name in self.SHEET_KEYS:
            try:
                results[sheet_name] = self._backend.delete_rows(
                    sheet_name, mc, did)
            except Exception as e:
                logger.exception("clear_market_data: %s failed",
                                 sheet_name)
                results[sheet_name] = SyncResult(
                    success=False, error=str(e))

        return results
