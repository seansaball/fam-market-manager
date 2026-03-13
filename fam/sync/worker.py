"""Background worker for non-blocking sync operations."""

import logging

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger('fam.sync.worker')


class SyncWorker(QObject):
    """Runs sync in a background thread.  Emits signals on completion."""

    finished = Signal(dict)   # {sheet_name: SyncResult}
    error = Signal(str)       # error message
    progress = Signal(str)    # status text for UI updates

    def __init__(self, sync_manager, report_data: dict = None):
        super().__init__()
        self._manager = sync_manager
        self._data = report_data
        self.photo_stats = None  # Set after photo upload step

    def run(self):
        """Execute the sync.  Called from the background QThread."""
        try:
            # Step 0: Collect sync data on the worker thread (off the UI thread)
            if self._data is None:
                self.progress.emit("Collecting sync data...")
                try:
                    from fam.sync.data_collector import collect_sync_data
                    self._data = collect_sync_data()
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

                    # Re-collect sync data so fresh Drive URLs appear in the sheet
                    from fam.sync.data_collector import collect_sync_data
                    fresh_data = collect_sync_data()
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

            # Step 2: Sync data to Google Sheets
            self.progress.emit("Syncing data to Google Sheets...")
            results = self._manager.sync_all(self._data)
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
                pass
