"""Background worker for non-blocking sync operations."""

import logging

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger('fam.sync.worker')


class SyncWorker(QObject):
    """Runs sync in a background thread.  Emits signals on completion."""

    finished = Signal(dict)   # {sheet_name: SyncResult}
    error = Signal(str)       # error message
    progress = Signal(str)    # status text for UI updates

    def __init__(self, sync_manager, report_data: dict = None,
                 market_day_id: int | None = None):
        super().__init__()
        self._manager = sync_manager
        self._data = report_data
        # Persist the original scope so the photo re-collection
        # step (after Drive uploads succeed) re-runs with the SAME
        # market-day filter the caller initially asked for.
        # v1.9.10 follow-up (2026-05-01): without this, an
        # auto-sync triggered for a single market day silently
        # widened to ALL market days when photos uploaded —
        # multiplying API calls and shipping unintended scope to
        # Google Sheets.
        self._market_day_id = market_day_id
        self.photo_stats = None  # Set after photo upload step

    def run(self):
        """Execute the sync.  Called from the background QThread."""
        try:
            # Step 0: Collect sync data on the worker thread (off the UI thread)
            if self._data is None:
                self.progress.emit("Collecting sync data...")
                try:
                    from fam.sync.data_collector import collect_sync_data
                    self._data = collect_sync_data(
                        market_day_id=self._market_day_id)
                except Exception:
                    logger.exception("Failed to collect sync data")
                    self.error.emit("Failed to collect sync data")
                    return
                if not self._data:
                    self.error.emit("No data to sync")
                    return

            # Step 1: Upload pending photos to Google Drive
            # (so Drive URLs are available for the sheet sync)
            try:
                from fam.sync.drive import upload_pending_photos
                self.progress.emit("Uploading photos to Google Drive...")
                stats = upload_pending_photos()
                self.photo_stats = stats

                total_uploaded = stats.get('uploaded', 0)
                total_failed = stats.get('failed', 0)

                # Check for folder access error first
                if stats.get('error'):
                    self.progress.emit(
                        f"Photo error: {stats['error']}")
                elif total_uploaded > 0:
                    # Build descriptive message
                    parts = []
                    if stats.get('fmnp_uploaded'):
                        parts.append(f"{stats['fmnp_uploaded']} FMNP")
                    if stats.get('payment_uploaded'):
                        parts.append(f"{stats['payment_uploaded']} payment")
                    detail = " + ".join(parts) if parts else str(total_uploaded)
                    msg = f"Uploaded {detail} photo(s) to Drive"
                    if total_failed:
                        msg += f" ({total_failed} failed)"
                    self.progress.emit(msg)

                    # Re-collect sync data so fresh Drive URLs appear
                    # in the sheet.  Re-use the original scope; see the
                    # ``market_day_id`` comment in __init__.
                    from fam.sync.data_collector import collect_sync_data
                    fresh_data = collect_sync_data(
                        market_day_id=self._market_day_id)
                    if fresh_data:
                        self._data = fresh_data
                    logger.info("Uploaded %d photo(s), re-collected sync data",
                                total_uploaded)
                elif total_failed > 0:
                    self.progress.emit(
                        f"Photo upload: {total_failed} failed — syncing sheets...")
                else:
                    self.progress.emit("No new photos to upload")
            except Exception as exc:
                logger.warning("Photo upload step failed — continuing with sheet sync",
                               exc_info=True)
                self.progress.emit(f"Photo upload error: {exc} — syncing sheets...")
                self.photo_stats = {'uploaded': 0, 'failed': 0,
                                    'error': str(exc)}

            # Step 2: Sync data to Google Sheets.
            #
            # v2.0.1: when the worker was given a narrow scope
            # (a single open market day), tell the manager NOT to
            # delete stale rows on the per-md tabs.  A narrow-scope
            # collection legitimately omits other market days'
            # rows; deleting them on the sheet would silently
            # destroy historical data on every auto-sync.  Manual
            # full syncs (market_day_id is None) still prune.
            delete_stale = self._market_day_id is None
            self.progress.emit("Syncing data to Google Sheets...")
            results = self._manager.sync_all(
                self._data, delete_stale=delete_stale)
            self.finished.emit(results)
        except Exception as e:
            logger.exception("Sync worker failed")
            self.error.emit(str(e))
        finally:
            # Close this thread's database connection to prevent leaks.
            # Each sync cycle creates a thread-local SQLite connection
            # via get_connection(); without this it stays open until GC.
            try:
                from fam.database.connection import close_connection
                close_connection()
            except Exception:
                # v2.0.1: was a silent ``pass`` — close_connection
                # failures leaked SQLite connections + made "database
                # is locked" symptom-to-cause distance enormous.
                # logger.warning so the entry reaches the rotating
                # log + the in-app Error Log report.
                logger.warning(
                    "Sync worker: close_connection failed in finally",
                    exc_info=True)
