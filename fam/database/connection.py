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
    """Get a thread-local SQLite connection."""
    if not hasattr(_local, 'connection') or _local.connection is None:
        db_path = get_db_path()
        _local.connection = sqlite3.connect(db_path)
        _local.connection.row_factory = sqlite3.Row
        _local.connection.execute("PRAGMA journal_mode=WAL")
        _local.connection.execute("PRAGMA foreign_keys=ON")
        _local.connection.execute("PRAGMA busy_timeout=5000")
    return _local.connection


def close_connection():
    """Close the thread-local connection if open."""
    if hasattr(_local, 'connection') and _local.connection is not None:
        _local.connection.close()
        _local.connection = None
