"""SQLite connection manager."""

import sqlite3
import os
import threading

_DB_PATH = None
_local = threading.local()


def set_db_path(path: str):
    """Set the database file path."""
    global _DB_PATH
    _DB_PATH = path


def get_db_path() -> str:
    """Get the database file path, defaulting to fam_data.db in current dir."""
    if _DB_PATH:
        return _DB_PATH
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'fam_data.db')


def get_connection() -> sqlite3.Connection:
    """Get a thread-local SQLite connection.

    PRAGMAs:
        journal_mode=WAL — concurrent reader/writer support
        foreign_keys=ON — schema-level FK enforcement
        busy_timeout=5000 — 5s wait on contended writes

    v2.0.3 PRAGMAs (HIGH-6):
        synchronous=NORMAL — the standard WAL-mode recommendation.
            Default is FULL which fsyncs after every commit.
            NORMAL is durable across crash and ~4× faster on Windows.
            See https://www.sqlite.org/pragma.html#pragma_synchronous —
            "When synchronous is NORMAL, the SQLite database engine
            will still sync at the most critical moments, but less
            often than in FULL mode."
        wal_autocheckpoint=500 — checkpoint when -wal grows past
            500 frames (~2 MB).  Default is 1000.  Smaller cap
            keeps the WAL bounded so restart recovery is fast and
            ``Connection.backup()`` snapshots a smaller WAL tail.
    """
    if not hasattr(_local, 'connection') or _local.connection is None:
        db_path = get_db_path()
        _local.connection = sqlite3.connect(db_path)
        _local.connection.row_factory = sqlite3.Row
        _local.connection.execute("PRAGMA journal_mode=WAL")
        _local.connection.execute("PRAGMA foreign_keys=ON")
        _local.connection.execute("PRAGMA busy_timeout=5000")
        _local.connection.execute("PRAGMA synchronous=NORMAL")
        _local.connection.execute("PRAGMA wal_autocheckpoint=500")
    return _local.connection


def close_connection():
    """Close the thread-local connection if open."""
    if hasattr(_local, 'connection') and _local.connection is not None:
        _local.connection.close()
        _local.connection = None
