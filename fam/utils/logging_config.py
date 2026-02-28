"""Centralized logging configuration with rotating file handler."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def setup_logging():
    """Set up file-based rotating log next to the database.

    Returns the log file path.  5 MB per file, 3 backups = 20 MB max.
    """
    if getattr(sys, 'frozen', False):
        log_dir = os.path.dirname(sys.executable)
    else:
        log_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

    log_path = os.path.join(log_dir, 'fam_manager.log')

    handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
    )
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    handler.setFormatter(formatter)

    root = logging.getLogger('fam')
    root.setLevel(logging.INFO)
    # Avoid duplicate handlers if setup_logging is called more than once
    if not root.handlers:
        root.addHandler(handler)

    return log_path
