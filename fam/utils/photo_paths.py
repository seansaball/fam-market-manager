"""Parse and encode multi-photo paths stored as JSON arrays in TEXT columns.

Provides backward compatibility: old single-path strings (e.g.
'photos/fmnp_42_123.jpg') are treated as one-element lists, while
new entries store JSON arrays (e.g. '["photos/a.jpg", "photos/b.jpg"]').
"""

import json
from typing import Optional


def parse_photo_paths(raw: Optional[str]) -> list[str]:
    """Return a list of photo paths from a DB value.

    Handles:
      - None / '' → []
      - '["photos/a.jpg", "photos/b.jpg"]' (JSON array) → list
      - 'photos/old_single.jpg' (legacy single path) → [path]
    """
    if not raw:
        return []
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith('['):
        try:
            paths = json.loads(raw)
            if isinstance(paths, list):
                return [p for p in paths if p]
        except (json.JSONDecodeError, TypeError):
            pass
    # Legacy single-path format
    return [raw]


def encode_photo_paths(paths: list[str]) -> Optional[str]:
    """Encode a list of photo paths as a JSON array string.

    Returns None if the list is empty (clears the DB column).
    """
    filtered = [p for p in paths if p]
    if not filtered:
        return None
    return json.dumps(filtered)
