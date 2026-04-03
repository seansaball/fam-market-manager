"""Centralized logging configuration with rotating file handler."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_log_path = None


def setup_logging(data_dir: str | None = None):
    """Set up file-based rotating log in the data directory.

    Parameters
    ----------
    data_dir : str, optional
        Directory for the log file.  When ``None``, falls back to the
        legacy behaviour (next to the executable or project root).

    Returns the log file path.  5 MB per file, 3 backups = 20 MB max.
    """
    global _log_path

    if data_dir:
        log_dir = data_dir
    elif getattr(sys, 'frozen', False):
        log_dir = os.path.dirname(sys.executable)
    else:
        log_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

    _log_path = os.path.join(log_dir, 'fam_manager.log')

    handler = RotatingFileHandler(
        _log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
    )
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    # Force log timestamps to US Eastern regardless of system timezone
    from fam.utils.timezone import eastern_now
    formatter.converter = lambda *_args: eastern_now().timetuple()
    handler.setFormatter(formatter)

    root = logging.getLogger('fam')
    root.setLevel(logging.INFO)
    # Avoid duplicate handlers if setup_logging is called more than once
    if not root.handlers:
        root.addHandler(handler)

    return _log_path


def get_log_path():
    """Return the log file path (available after setup_logging()).

    Falls back to computing the path the same way setup_logging() does
    if called before setup has run.
    """
    if _log_path:
        return _log_path
    if getattr(sys, 'frozen', False):
        log_dir = os.path.dirname(sys.executable)
    else:
        log_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
    return os.path.join(log_dir, 'fam_manager.log')
