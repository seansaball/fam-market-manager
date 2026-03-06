"""Import and export application settings (markets, vendors, payment methods).

File format: human-readable pipe-delimited text with section headers.
Extension: .fam
"""

import logging
import re
from datetime import datetime
from dataclasses import dataclass, field

from fam.database.connection import get_connection

logger = logging.getLogger('fam.settings_io')

FILE_VERSION = 1

# Maximum lengths for imported text fields
MAX_NAME_LEN = 100        # Market names, vendor names
MAX_PM_NAME_LEN = 50      # Payment method names (most constrained — 160px combo)
MAX_ADDRESS_LEN = 200     # Market addresses
MAX_CONTACT_LEN = 200     # Vendor contact info


def _sanitize_text(value: str) -> str:
    """Remove control characters and normalize whitespace."""
    # Remove control chars (U+0000–U+001F) except space (0x20)
    cleaned = re.sub(r'[\x00-\x1f]', '', value)
    # Collapse multiple consecutive spaces into one
    cleaned = re.sub(r' {2,}', ' ', cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Data containers for parsed import data
# ---------------------------------------------------------------------------

@dataclass
class ImportMarket:
    name: str
    address: str
    daily_match_limit: float
    limit_active: bool


@dataclass
class ImportVendor:
    name: str
    contact_info: str


@dataclass
class ImportPaymentMethod:
    name: str
    match_percent: float
    sort_order: int


@dataclass
class ImportAssignment:
    market_name: str
    entity_name: str  # vendor or payment method name


@dataclass
class ImportResult:
    """Parsed import data with new-vs-existing classification."""
    markets: list[ImportMarket] = field(default_factory=list)
    vendors: list[ImportVendor] = field(default_factory=list)
    payment_methods: list[ImportPaymentMethod] = field(default_factory=list)
    vendor_assignments: list[ImportAssignment] = field(default_factory=list)
    pm_assignments: list[ImportAssignment] = field(default_factory=list)

    # Sets of names that already exist in the database
    existing_market_names: set = field(default_factory=set)
    existing_vendor_names: set = field(default_factory=set)
    existing_pm_names: set = field(default_factory=set)

    errors: list[str] = field(default_factory=list)

    @property
    def new_markets(self):
        return [m for m in self.markets if m.name not in self.existing_market_names]

    @property
    def skipped_markets(self):
        return [m for m in self.markets if m.name in self.existing_market_names]

    @property
    def new_vendors(self):
        return [v for v in self.vendors if v.name not in self.existing_vendor_names]

    @property
    def skipped_vendors(self):
        return [v for v in self.vendors if v.name in self.existing_vendor_names]

    @property
    def new_payment_methods(self):
        return [p for p in self.payment_methods if p.name not in self.existing_pm_names]

    @property
    def skipped_payment_methods(self):
        return [p for p in self.payment_methods if p.name in self.existing_pm_names]

    @property
    def has_new_data(self):
        return bool(self.new_markets or self.new_vendors or self.new_payment_methods
                     or self.vendor_assignments or self.pm_assignments)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_settings(filepath: str) -> str:
    """Export all settings to a human-readable .fam file.

    Returns the filepath on success, raises on error.
    """
    conn = get_connection()

    markets = [dict(r) for r in conn.execute(
        "SELECT * FROM markets ORDER BY name"
    ).fetchall()]

    vendors = [dict(r) for r in conn.execute(
        "SELECT * FROM vendors ORDER BY name"
    ).fetchall()]

    methods = [dict(r) for r in conn.execute(
        "SELECT * FROM payment_methods ORDER BY sort_order, name"
    ).fetchall()]

    # Market-vendor assignments
    mv_rows = conn.execute("""
        SELECT m.name AS market_name, v.name AS vendor_name
        FROM market_vendors mv
        JOIN markets m ON m.id = mv.market_id
        JOIN vendors v ON v.id = mv.vendor_id
        ORDER BY m.name, v.name
    """).fetchall()

    # Market-payment method assignments
    mpm_rows = conn.execute("""
        SELECT m.name AS market_name, pm.name AS pm_name
        FROM market_payment_methods mpm
        JOIN markets m ON m.id = mpm.market_id
        JOIN payment_methods pm ON pm.id = mpm.payment_method_id
        ORDER BY m.name, pm.sort_order, pm.name
    """).fetchall()

    from fam.utils.app_settings import get_market_code, get_device_id
    _code = get_market_code() or 'Not Set'
    _device = get_device_id() or 'Unknown'

    lines = []
    lines.append("# FAM Market Manager - Settings Export")
    lines.append(f"# Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"# Version: {FILE_VERSION}")
    lines.append(f"# Market Code: {_code}")
    lines.append(f"# Device ID: {_device}")
    lines.append("#")
    lines.append("# This file can be edited with any text editor.")
    lines.append("# Each section uses pipe (|) separated values.")
    lines.append("# Lines starting with # are comments and are ignored.")
    lines.append("")

    # Markets
    lines.append("=== Markets ===")
    lines.append("Name | Address | Daily Match Limit | Limit Active")
    for m in markets:
        addr = m.get('address') or ''
        limit = m.get('daily_match_limit') or 100.00
        active = "Yes" if m.get('match_limit_active', 1) else "No"
        lines.append(f"{m['name']} | {addr} | {limit:.2f} | {active}")
    lines.append("")

    # Vendors
    lines.append("=== Vendors ===")
    lines.append("Name | Contact Info")
    for v in vendors:
        contact = v.get('contact_info') or ''
        lines.append(f"{v['name']} | {contact}")
    lines.append("")

    # Payment Methods
    lines.append("=== Payment Methods ===")
    lines.append("Name | Match % | Sort Order")
    for pm in methods:
        lines.append(f"{pm['name']} | {pm['match_percent']} | {pm['sort_order']}")
    lines.append("")

    # Market-Vendor assignments
    lines.append("=== Market Vendors ===")
    lines.append("Market | Vendor")
    for r in mv_rows:
        lines.append(f"{r['market_name']} | {r['vendor_name']}")
    lines.append("")

    # Market-Payment Method assignments
    lines.append("=== Market Payment Methods ===")
    lines.append("Market | Payment Method")
    for r in mpm_rows:
        lines.append(f"{r['market_name']} | {r['pm_name']}")
    lines.append("")

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    logger.info("Settings exported to %s (%d markets, %d vendors, %d methods)",
                filepath, len(markets), len(vendors), len(methods))
    return filepath


# ---------------------------------------------------------------------------
# Parse (validate + classify)
# ---------------------------------------------------------------------------

def parse_settings_file(filepath: str) -> ImportResult:
    """Parse a .fam settings file and classify items as new or existing.

    Returns an ImportResult with parsed data and validation errors.
    """
    result = ImportResult()

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        result.errors.append(f"Could not read file: {e}")
        return result

    # Validate it looks like a FAM settings file
    if '=== Markets ===' not in content and '=== Vendors ===' not in content:
        result.errors.append(
            "This does not appear to be a valid FAM settings file.\n"
            "Expected section headers like '=== Markets ===' were not found."
        )
        return result

    # Parse sections
    current_section = None
    header_row = None

    for line_num, raw_line in enumerate(content.split('\n'), 1):
        line = raw_line.strip()

        # Skip empty lines and comments
        if not line or line.startswith('#'):
            continue

        # Section headers
        if line.startswith('===') and line.endswith('==='):
            current_section = line.strip('= ').strip()
            header_row = None
            continue

        # First non-comment, non-header line in a section is the column header
        if current_section and header_row is None:
            header_row = line
            continue

        # Parse data rows
        if current_section and header_row:
            parts = [p.strip() for p in line.split('|')]

            try:
                if current_section == 'Markets':
                    if len(parts) < 1:
                        result.errors.append(f"Line {line_num}: Market row needs at least a name")
                        continue
                    name = _sanitize_text(parts[0])
                    if not name:
                        result.errors.append(f"Line {line_num}: Market name cannot be empty")
                        continue
                    if len(name) > MAX_NAME_LEN:
                        result.errors.append(f"Line {line_num}: Market name truncated to {MAX_NAME_LEN} chars")
                        name = name[:MAX_NAME_LEN].rstrip()
                    address = _sanitize_text(parts[1]) if len(parts) > 1 else ''
                    if len(address) > MAX_ADDRESS_LEN:
                        result.errors.append(f"Line {line_num}: Address truncated to {MAX_ADDRESS_LEN} chars")
                        address = address[:MAX_ADDRESS_LEN].rstrip()
                    try:
                        limit = float(parts[2]) if len(parts) > 2 and parts[2] else 100.00
                    except ValueError:
                        result.errors.append(f"Line {line_num}: Invalid match limit '{parts[2]}'")
                        limit = 100.00
                    limit_active = True
                    if len(parts) > 3:
                        limit_active = parts[3].lower() in ('yes', 'true', '1', 'on')
                    result.markets.append(ImportMarket(name, address, limit, limit_active))

                elif current_section == 'Vendors':
                    if len(parts) < 1:
                        result.errors.append(f"Line {line_num}: Vendor row needs at least a name")
                        continue
                    name = _sanitize_text(parts[0])
                    if not name:
                        result.errors.append(f"Line {line_num}: Vendor name cannot be empty")
                        continue
                    if len(name) > MAX_NAME_LEN:
                        result.errors.append(f"Line {line_num}: Vendor name truncated to {MAX_NAME_LEN} chars")
                        name = name[:MAX_NAME_LEN].rstrip()
                    contact = _sanitize_text(parts[1]) if len(parts) > 1 else ''
                    if len(contact) > MAX_CONTACT_LEN:
                        result.errors.append(f"Line {line_num}: Contact info truncated to {MAX_CONTACT_LEN} chars")
                        contact = contact[:MAX_CONTACT_LEN].rstrip()
                    result.vendors.append(ImportVendor(name, contact))

                elif current_section == 'Payment Methods':
                    if len(parts) < 1:
                        result.errors.append(f"Line {line_num}: Payment method needs at least a name")
                        continue
                    name = _sanitize_text(parts[0])
                    if not name:
                        result.errors.append(f"Line {line_num}: Payment method name cannot be empty")
                        continue
                    if len(name) > MAX_PM_NAME_LEN:
                        result.errors.append(f"Line {line_num}: Payment method name truncated to {MAX_PM_NAME_LEN} chars")
                        name = name[:MAX_PM_NAME_LEN].rstrip()
                    try:
                        match_pct = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
                    except ValueError:
                        result.errors.append(f"Line {line_num}: Invalid match % '{parts[1]}'")
                        match_pct = 0.0
                    if match_pct < 0 or match_pct > 999:
                        result.errors.append(f"Line {line_num}: Match % must be between 0 and 999")
                        match_pct = max(0, min(999, match_pct))
                    try:
                        sort_order = int(parts[2]) if len(parts) > 2 and parts[2] else 0
                    except ValueError:
                        sort_order = 0
                    result.payment_methods.append(ImportPaymentMethod(name, match_pct, sort_order))

                elif current_section == 'Market Vendors':
                    if len(parts) < 2:
                        result.errors.append(f"Line {line_num}: Assignment needs Market | Vendor")
                        continue
                    market_name = _sanitize_text(parts[0])
                    vendor_name = _sanitize_text(parts[1])
                    if market_name and vendor_name:
                        result.vendor_assignments.append(
                            ImportAssignment(market_name, vendor_name)
                        )

                elif current_section == 'Market Payment Methods':
                    if len(parts) < 2:
                        result.errors.append(f"Line {line_num}: Assignment needs Market | Method")
                        continue
                    market_name = _sanitize_text(parts[0])
                    pm_name = _sanitize_text(parts[1])
                    if market_name and pm_name:
                        result.pm_assignments.append(
                            ImportAssignment(market_name, pm_name)
                        )

            except Exception as e:
                result.errors.append(f"Line {line_num}: {e}")

    # Look up existing names in the database
    conn = get_connection()
    existing_markets = {
        r['name'] for r in conn.execute("SELECT name FROM markets").fetchall()
    }
    existing_vendors = {
        r['name'] for r in conn.execute("SELECT name FROM vendors").fetchall()
    }
    existing_pms = {
        r['name'] for r in conn.execute("SELECT name FROM payment_methods").fetchall()
    }
    result.existing_market_names = existing_markets
    result.existing_vendor_names = existing_vendors
    result.existing_pm_names = existing_pms

    logger.info(
        "Parsed settings file: %d markets (%d new), %d vendors (%d new), "
        "%d methods (%d new), %d errors",
        len(result.markets), len(result.new_markets),
        len(result.vendors), len(result.new_vendors),
        len(result.payment_methods), len(result.new_payment_methods),
        len(result.errors),
    )
    return result


# ---------------------------------------------------------------------------
# Apply import
# ---------------------------------------------------------------------------

def apply_import(result: ImportResult) -> dict:
    """Insert new items from a parsed ImportResult into the database.

    Skips items that already exist (matched by name).
    Returns a summary dict with counts.
    """
    conn = get_connection()
    counts = {
        'markets_added': 0,
        'vendors_added': 0,
        'payment_methods_added': 0,
        'vendor_assignments_added': 0,
        'pm_assignments_added': 0,
    }

    # Insert new markets
    for m in result.new_markets:
        try:
            conn.execute(
                "INSERT INTO markets (name, address, daily_match_limit, match_limit_active) "
                "VALUES (?, ?, ?, ?)",
                (m.name, m.address or None, m.daily_match_limit, int(m.limit_active))
            )
            counts['markets_added'] += 1
        except Exception as e:
            logger.warning("Could not insert market '%s': %s", m.name, e)

    # Insert new vendors
    for v in result.new_vendors:
        try:
            conn.execute(
                "INSERT INTO vendors (name, contact_info) VALUES (?, ?)",
                (v.name, v.contact_info or None)
            )
            counts['vendors_added'] += 1
        except Exception as e:
            logger.warning("Could not insert vendor '%s': %s", v.name, e)

    # Insert new payment methods
    for pm in result.new_payment_methods:
        try:
            conn.execute(
                "INSERT INTO payment_methods (name, match_percent, sort_order) VALUES (?, ?, ?)",
                (pm.name, pm.match_percent, pm.sort_order)
            )
            counts['payment_methods_added'] += 1
        except Exception as e:
            logger.warning("Could not insert payment method '%s': %s", pm.name, e)

    conn.commit()

    # Now handle assignments — need fresh name→id lookups since we just inserted
    market_name_to_id = {
        r['name']: r['id']
        for r in conn.execute("SELECT id, name FROM markets").fetchall()
    }
    vendor_name_to_id = {
        r['name']: r['id']
        for r in conn.execute("SELECT id, name FROM vendors").fetchall()
    }
    pm_name_to_id = {
        r['name']: r['id']
        for r in conn.execute("SELECT id, name FROM payment_methods").fetchall()
    }

    # Vendor assignments
    for a in result.vendor_assignments:
        mid = market_name_to_id.get(a.market_name)
        vid = vendor_name_to_id.get(a.entity_name)
        if mid and vid:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO market_vendors (market_id, vendor_id) VALUES (?, ?)",
                    (mid, vid)
                )
                counts['vendor_assignments_added'] += 1
            except Exception as e:
                logger.warning("Could not assign vendor '%s' to market '%s': %s",
                               a.entity_name, a.market_name, e)

    # Payment method assignments
    for a in result.pm_assignments:
        mid = market_name_to_id.get(a.market_name)
        pid = pm_name_to_id.get(a.entity_name)
        if mid and pid:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO market_payment_methods "
                    "(market_id, payment_method_id) VALUES (?, ?)",
                    (mid, pid)
                )
                counts['pm_assignments_added'] += 1
            except Exception as e:
                logger.warning("Could not assign method '%s' to market '%s': %s",
                               a.entity_name, a.market_name, e)

    conn.commit()

    logger.info("Import applied: %s", counts)
    return counts
