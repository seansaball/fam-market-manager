"""Background workers for non-blocking update operations."""

import logging

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger('fam.update.worker')


class UpdateCheckWorker(QObject):
    """Checks GitHub Releases for a newer version in a background thread."""

    finished = Signal(dict)   # update info dict (empty if no update)
    error = Signal(str)       # error message

    def __init__(self, owner: str, repo: str, current_version: str):
        super().__init__()
        self._owner = owner
        self._repo = repo
        self._version = current_version

    def run(self):
        """Execute the version check.  Called from the background QThread."""
        try:
            from fam.update.checker import check_for_update
            result = check_for_update(
                self._owner, self._repo, self._version)
            self.finished.emit(result or {})
        except Exception as e:
            logger.exception("Update check worker failed")
            self.error.emit(str(e))


class UpdateDownloadWorker(QObject):
    """Downloads a release asset in a background thread."""

    finished = Signal(str)       # path to downloaded zip
    error = Signal(str)          # error message
    progress = Signal(int, int)  # (bytes_downloaded, total_bytes)

    def __init__(self, asset_url: str, asset_size: int, dest_path: str):
        super().__init__()
        self._url = asset_url
        self._size = asset_size
        self._dest = dest_path

    def run(self):
        """Execute the download.  Called from the background QThread."""
        try:
            from fam.update.checker import download_update, verify_download

            success = download_update(
                self._url, self._dest,
                progress_callback=lambda dl, total: self.progress.emit(
                    dl, total),
            )
            if not success:
                self.error.emit("Download failed")
                return

            if not verify_download(self._dest, self._size):
                self.error.emit(
                    "Downloaded file size does not match expected size")
                return

            self.finished.emit(self._dest)

        except Exception as e:
            logger.exception("Update download worker failed")
            self.error.emit(str(e))
