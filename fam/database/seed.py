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
        ("Downtown Saturday Market", "123 Main St"),
        ("Riverside Wednesday Market", "456 River Rd"),
    ]
    cursor.executemany("INSERT INTO markets (name, address) VALUES (?, ?)", markets)

    # Vendors
    vendors = [
        ("Green Valley Farm", None),
        ("Sunny Acres Produce", None),
        ("Mountain Herb Co.", None),
        ("Baker's Delight", None),
    ]
    cursor.executemany("INSERT INTO vendors (name, contact_info) VALUES (?, ?)", vendors)

    # Payment Methods
    payment_methods = [
        ("SNAP", 50.0, 1, 1),
        ("Cash", 0.0, 1, 2),
        ("Tokens", 25.0, 1, 3),
        ("Food Bucks", 100.0, 1, 4),
        ("Food RX", 75.0, 1, 5),
        ("FMNP", 100.0, 1, 6),
    ]
    cursor.executemany(
        "INSERT INTO payment_methods (name, match_percent, is_active, sort_order) VALUES (?, ?, ?, ?)",
        payment_methods
    )

    # Assign all payment methods to all markets by default
    cursor.execute("SELECT id FROM markets")
    market_ids = [r[0] for r in cursor.fetchall()]
    cursor.execute("SELECT id FROM payment_methods")
    pm_ids = [r[0] for r in cursor.fetchall()]
    for mid in market_ids:
        for pid in pm_ids:
            cursor.execute(
                "INSERT OR IGNORE INTO market_payment_methods"
                " (market_id, payment_method_id) VALUES (?, ?)",
                (mid, pid)
            )

    conn.commit()
    return True
