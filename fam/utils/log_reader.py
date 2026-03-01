"""Parse the fam_manager.log file for display in the Error Log UI.

Reads the rotating log file produced by logging_config.setup_logging(),
extracts structured entries, groups multi-line tracebacks with their
parent ERROR line, and translates technical messages into friendly
descriptions where possible.
"""

import os
import re

# Matches: "2026-02-28 14:07:03 [ERROR] fam.ui.payment_screen: message text"
LOG_LINE_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+'
    r'\[(\w+)\]\s+'
    r'([\w.]+):\s+'
    r'(.*)$'
)

# Maps technical logger names to friendly module names
MODULE_LABELS = {
    'fam':                          'Application',
    'fam.app':                      'Application Startup',
    'fam.database.schema':          'Database Setup',
    'fam.database.connection':      'Database Connection',
    'fam.models.audit':             'Audit Log',
    'fam.models.transaction':       'Transactions',
    'fam.models.market_day':        'Market Day',
    'fam.models.vendor':            'Vendors',
    'fam.models.payment_method':    'Payment Methods',
    'fam.models.fmnp':              'FMNP',
    'fam.models.customer_order':    'Customer Orders',
    'fam.ui.payment_screen':        'Payment Screen',
    'fam.ui.receipt_intake_screen': 'Receipt Intake',
    'fam.ui.admin_screen':          'Adjustments',
    'fam.ui.settings_screen':       'Settings',
    'fam.ui.fmnp_screen':           'FMNP Screen',
    'fam.ui.market_day_screen':     'Market Day Screen',
    'fam.ui.reports_screen':        'Reports',
    'fam.ui.main_window':           'Main Window',
}

# Known error patterns → plain-English descriptions
_FRIENDLY_PATTERNS = [
    (re.compile(r'Failed to save draft', re.I),
     'A draft transaction could not be saved.'),
    (re.compile(r'Payment.*(?:fail|error)', re.I),
     'Payment processing encountered an error.'),
    (re.compile(r'Failed to adjust transaction', re.I),
     'A transaction adjustment could not be completed.'),
    (re.compile(r'Failed to void transaction', re.I),
     'A transaction void operation failed.'),
    (re.compile(r'Failed to (?:save|add|create) FMNP', re.I),
     'An FMNP entry could not be saved.'),
    (re.compile(r'Failed to delete FMNP', re.I),
     'An FMNP entry could not be deleted.'),
    (re.compile(r'Failed to (?:add|create) market', re.I),
     'A new market could not be created.'),
    (re.compile(r'Failed to edit market', re.I),
     'A market update could not be saved.'),
    (re.compile(r'Failed to (?:add|create) vendor', re.I),
     'A new vendor could not be created.'),
    (re.compile(r'Failed to edit vendor', re.I),
     'A vendor update could not be saved.'),
    (re.compile(r'Failed to (?:add|create) payment method', re.I),
     'A new payment method could not be created.'),
    (re.compile(r'Failed to update match limit', re.I),
     'A market match limit change could not be saved.'),
    (re.compile(r'database.*locked', re.I),
     'The database was temporarily locked by another operation.'),
    (re.compile(r'OperationalError', re.I),
     'A database operation failed unexpectedly.'),
    (re.compile(r'IntegrityError', re.I),
     'A data integrity constraint was violated.'),
]


def get_friendly_module(logger_name):
    """Translate 'fam.ui.payment_screen' to 'Payment Screen'."""
    if logger_name in MODULE_LABELS:
        return MODULE_LABELS[logger_name]
    # Try progressively shorter prefixes
    parts = logger_name.split('.')
    while parts:
        candidate = '.'.join(parts)
        if candidate in MODULE_LABELS:
            return MODULE_LABELS[candidate]
        parts.pop()
    return logger_name


def get_friendly_message(raw_message):
    """Match known error patterns to a user-friendly description."""
    for pattern, friendly in _FRIENDLY_PATTERNS:
        if pattern.search(raw_message):
            return friendly
    return raw_message


def parse_log_file(log_path, levels=None, date_from=None, date_to=None,
                   limit=500):
    """Parse the log file and return structured entries.

    Args:
        log_path: Absolute path to fam_manager.log.
        levels: Set of level strings to include, e.g. {'ERROR', 'WARNING'}.
                If None, includes ERROR and WARNING.
        date_from: Include entries from this date (YYYY-MM-DD), inclusive.
        date_to: Include entries to this date (YYYY-MM-DD), inclusive.
        limit: Maximum entries to return.

    Returns:
        List of dicts (newest first) with keys: timestamp, level, module,
        module_label, message, friendly_message, traceback
    """
    if levels is None:
        levels = {'ERROR', 'WARNING'}

    if not os.path.exists(log_path):
        return []

    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            raw_lines = f.readlines()
    except OSError:
        return []

    # Parse all lines, grouping tracebacks with their parent entry
    entries = []
    current_entry = None

    for line in raw_lines:
        match = LOG_LINE_RE.match(line.rstrip())
        if match:
            # Flush previous entry
            if current_entry is not None:
                entries.append(current_entry)
            timestamp, level, module, message = match.groups()
            current_entry = {
                'timestamp': timestamp,
                'level': level,
                'module': module,
                'module_label': get_friendly_module(module),
                'message': message,
                'friendly_message': get_friendly_message(message),
                'traceback': '',
            }
        elif current_entry is not None:
            # Continuation line (traceback, multi-line message)
            current_entry['traceback'] += line

    # Don't forget the last entry
    if current_entry is not None:
        entries.append(current_entry)

    # Filter by level
    entries = [e for e in entries if e['level'] in levels]

    # Filter by date range
    if date_from:
        entries = [e for e in entries if e['timestamp'][:10] >= date_from]
    if date_to:
        entries = [e for e in entries if e['timestamp'][:10] <= date_to]

    # Newest first, limited
    entries.reverse()
    return entries[:limit]
