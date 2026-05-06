"""Abstract sync backend interface — swappable for Supabase later."""

from abc import ABC, abstractmethod
from typing import Optional


class SyncResult:
    """Outcome of a sync operation.

    The ``offline`` flag distinguishes a transient network outage
    (DNS failure, connection refused, host unreachable) from a real
    code/data error.  Callers use it to:

      * Skip subsequent sheets in the same cycle once we've proven
        the network is down (no point in re-failing 5 more times)
      * Coalesce per-tab failures into a single summary log line so
        a 5-minute internet blip doesn't dump 30 stack traces
    """

    def __init__(self, success: bool, rows_synced: int = 0,
                 error: Optional[str] = None,
                 offline: bool = False):
        self.success = success
        self.rows_synced = rows_synced
        self.error = error
        self.offline = offline

    def __repr__(self):
        return (f"SyncResult(success={self.success}, "
                f"rows={self.rows_synced}, error={self.error}, "
                f"offline={self.offline})")


class SyncBackend(ABC):
    """Abstract interface for cloud sync backends.

    Implementations must handle authentication, writing rows to named
    sheets/tables, upserting by composite key, and deleting by identity.
    """

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if credentials are present and loadable."""

    @abstractmethod
    def validate_connection(self) -> SyncResult:
        """Test the connection. Returns SyncResult with success/error."""

    @abstractmethod
    def upsert_rows(self, sheet_name: str, rows: list[dict],
                    key_columns: list[str],
                    delete_stale: bool = True) -> SyncResult:
        """Insert or update rows.

        *key_columns* define the composite key for duplicate detection.
        Rows matching the key are updated; new rows are appended.

        *delete_stale*:  when True (default), rows on the destination
        that belong to this device but are NOT in *rows* are deleted
        as "no longer in local data."  Set False for narrow-scope
        auto-syncs that only carry a subset of the device's data —
        otherwise historical rows get silently removed every time the
        scope shrinks.
        """

    @abstractmethod
    def delete_rows(self, sheet_name: str,
                    market_code: str, device_id: str) -> SyncResult:
        """Delete all rows matching market_code + device_id."""

    @abstractmethod
    def read_rows(self, sheet_name: str,
                  market_code: Optional[str] = None,
                  device_id: Optional[str] = None) -> list[dict]:
        """Read rows, optionally filtered by market_code/device_id."""
