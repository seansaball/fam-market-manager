"""Abstract sync backend interface — swappable for Supabase later."""

from abc import ABC, abstractmethod
from typing import Optional


class SyncResult:
    """Outcome of a sync operation."""

    def __init__(self, success: bool, rows_synced: int = 0,
                 error: Optional[str] = None):
        self.success = success
        self.rows_synced = rows_synced
        self.error = error

    def __repr__(self):
        return (f"SyncResult(success={self.success}, "
                f"rows={self.rows_synced}, error={self.error})")


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
                    key_columns: list[str]) -> SyncResult:
        """Insert or update rows.

        *key_columns* define the composite key for duplicate detection.
        Rows matching the key are updated; new rows are appended.
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
