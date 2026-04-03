"""Centralised Eastern-time helpers.

Every timestamp the application creates — database records, backup files,
Google Sheets entries, UI display — should use US Eastern time.  Import
``eastern_now`` or ``eastern_today`` instead of ``datetime.now()`` /
``date.today()`` so the behaviour is consistent regardless of which
machine the app runs on.

``America/New_York`` automatically handles the EST ↔ EDT switch.
"""

from datetime import datetime, date
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def eastern_now() -> datetime:
    """Return the current datetime in US Eastern time (EST/EDT)."""
    return datetime.now(EASTERN)


def eastern_today() -> date:
    """Return today's date in US Eastern time."""
    return datetime.now(EASTERN).date()


def eastern_timestamp() -> str:
    """Return the current Eastern time as a human-readable string.

    Format: ``2026-04-02 20:16:27``  (no microseconds, no offset).
    Use this for all stored timestamps — database records, ledger
    backups, sync settings, etc.
    """
    return eastern_now().strftime("%Y-%m-%d %H:%M:%S")
