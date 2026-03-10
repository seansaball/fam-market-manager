"""Background worker for non-blocking sync operations."""

import logging

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger('fam.sync.worker')


class SyncWorker(QObject):
    """Runs sync in a background thread.  Emits signals on completion."""

    finished = Signal(dict)   # {sheet_name: SyncResult}
    error = Signal(str)       # error message
    progress = Signal(str)    # status text for UI updates

    def __init__(self, sync_manager, report_data: dict):
        super().__init__()
        self._manager = sync_manager
        self._data = report_data

    def run(self):
        """Execute the sync.  Called from the background QThread."""
        try:
            self.progress.emit("Syncing to Google Sheets...")
            results = self._manager.sync_all(self._data)
            self.finished.emit(results)
        except Exception as e:
            logger.exception("Sync worker failed")
            self.error.emit(str(e))
