"""Sync manager — orchestrates data collection and backend sync."""

import logging
from datetime import datetime

from fam.sync.base import SyncBackend, SyncResult
from fam.utils.app_settings import (
    get_market_code, get_device_id, set_setting, get_setting,
)

logger = logging.getLogger('fam.sync.manager')


class SyncManager:
    """Coordinates data collection and sync to the configured backend."""

    # Maps sheet tab names to their composite key columns for upsert.
    # market_code + device_id are always the first two key columns.
    SHEET_KEYS: dict[str, list[str]] = {
        'Vendor Reimbursement': ['market_code', 'device_id', 'Vendor'],
        'FAM Match Report':     ['market_code', 'device_id', 'Payment Method'],
        'Detailed Ledger':      ['market_code', 'device_id', 'Transaction ID'],
        'Transaction Log':      ['market_code', 'device_id', 'Time', 'Transaction'],
        'Activity Log':         ['market_code', 'device_id', 'Timestamp',
                                 'Record ID', 'Action'],
        'Geolocation':          ['market_code', 'device_id', 'Zip Code'],
        'FMNP Entries':         ['market_code', 'device_id', 'Entry ID'],
        'Market Day Summary':   ['market_code', 'device_id', 'Date'],
    }

    def __init__(self, backend: SyncBackend):
        self._backend = backend

    def is_available(self) -> bool:
        """Return True if the backend is configured."""
        return self._backend.is_configured()

    def sync_all(self, report_data: dict[str, list[dict]]
                 ) -> dict[str, SyncResult]:
        """Sync all provided sheet tabs.

        *report_data* maps sheet name → list[dict] (with identity
        columns already prepended by data_collector).

        Returns ``{sheet_name: SyncResult}``.  Never raises.
        """
        results: dict[str, SyncResult] = {}

        for sheet_name, rows in report_data.items():
            key_cols = self.SHEET_KEYS.get(
                sheet_name,
                ['market_code', 'device_id'],
            )
            try:
                result = self._backend.upsert_rows(
                    sheet_name, rows, key_cols)
                results[sheet_name] = result
            except Exception as e:
                logger.exception("sync_all: %s failed", sheet_name)
                results[sheet_name] = SyncResult(
                    success=False, error=str(e))

        # Record outcome in app_settings
        failed = [n for n, r in results.items() if not r.success]
        set_setting('last_sync_at', datetime.now().isoformat())
        if failed:
            set_setting('last_sync_error',
                        f"Failed: {', '.join(failed)}")
        else:
            set_setting('last_sync_error', '')

        total_rows = sum(r.rows_synced for r in results.values()
                         if r.success)
        logger.info("Sync complete: %d tabs, %d rows, %d failures",
                    len(results), total_rows, len(failed))

        return results

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
