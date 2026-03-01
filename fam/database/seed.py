"""Seed data for first-run initialization."""

from .connection import get_connection


def seed_if_empty():
    """Populate the database with initial test data if tables are empty."""
    conn = get_connection()
    cursor = conn.cursor()

    # Check if markets already have data
    cursor.execute("SELECT COUNT(*) FROM markets")
    if cursor.fetchone()[0] > 0:
        return False  # Already seeded

    # Markets
    markets = [
        ("Bethel Park Farmers Market", "30 Corrigan Dr, Bethel Park, PA 15102"),
        ("Bellevue Farmers Market", "34 North Balph Ave., Bellevue, PA 15202"),
        ("Cranberry Farmers Market", "2525 Rochester Road, Cranberry Township, PA 16066"),
    ]
    cursor.executemany("INSERT INTO markets (name, address) VALUES (?, ?)", markets)

    # Vendors
    vendors = [
        ("Evelyn's Farm", None),
        ("Forever Green Family Farm", None),
        ("Goose Run Farms", None),
        ("Hello Hummus", None),
        ("Loafers Bread Co", None),
        ("Logan Family Farm", None),
        ("Rockin' Cat Organic Coffee and Tea", None),
        ("Two Acre Farm", None),
    ]
    cursor.executemany("INSERT INTO vendors (name, contact_info) VALUES (?, ?)", vendors)

    # Payment Methods
    payment_methods = [
        ("SNAP", 100.0, 1, 1),
        ("FMNP", 100.0, 1, 2),
        ("Food RX", 100.0, 1, 3),
        ("JH Food Bucks", 100.0, 1, 4),
        ("JH Tokens", 100.0, 1, 5),
        ("Cash", 0.0, 1, 6),
    ]
    cursor.executemany(
        "INSERT INTO payment_methods (name, match_percent, is_active, sort_order) VALUES (?, ?, ?, ?)",
        payment_methods
    )

    # Assign all vendors and payment methods to all markets by default
    cursor.execute("SELECT id FROM markets")
    market_ids = [r[0] for r in cursor.fetchall()]
    cursor.execute("SELECT id FROM vendors")
    vendor_ids = [r[0] for r in cursor.fetchall()]
    cursor.execute("SELECT id FROM payment_methods")
    pm_ids = [r[0] for r in cursor.fetchall()]
    for mid in market_ids:
        for vid in vendor_ids:
            cursor.execute(
                "INSERT OR IGNORE INTO market_vendors"
                " (market_id, vendor_id) VALUES (?, ?)",
                (mid, vid)
            )
        for pid in pm_ids:
            cursor.execute(
                "INSERT OR IGNORE INTO market_payment_methods"
                " (market_id, payment_method_id) VALUES (?, ?)",
                (mid, pid)
            )

    conn.commit()
    return True
