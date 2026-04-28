"""Seed data for first-run initialization.

As of v1.6, first run starts with a clean slate — no pre-loaded markets,
vendors, or payment methods. Users configure their own data via the Settings
screen or by importing a .fam settings file.

The seed_if_empty() function is retained for backward compatibility with
the Reset feature and test infrastructure, but it no longer auto-runs
sample data on first launch.
"""

from .connection import get_connection


def seed_if_empty():
    """No-op on first run — the app starts with a clean slate.

    Returns False to indicate no data was seeded.
    """
    return False


def seed_sample_data():
    """Populate the database with sample data for testing or reset purposes.

    This is called by the Reset feature and test infrastructure, not on
    first launch.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Check if markets already have data
    cursor.execute("SELECT COUNT(*) FROM markets")
    if cursor.fetchone()[0] > 0:
        return False  # Already has data

    # Markets.  daily_match_limit is set explicitly to 10000 (cents = $100)
    # rather than relying on the column default — on databases that migrated
    # forward from v4, the column default is still the pre-v22 REAL `100.00`
    # (which the integer-cents engine reads as 100 cents → $1.00).  Always
    # passing the value explicitly makes Reset to Defaults produce the same
    # $100 cap on fresh installs and migrated installs alike.
    markets = [
        ("Bethel Park Farmers Market",
         "30 Corrigan Dr, Bethel Park, PA 15102", 10000, 1),
        ("Bellevue Farmers Market",
         "34 North Balph Ave., Bellevue, PA 15202", 10000, 1),
        ("Test Market",
         "123 Test Lane, Test Township, PA 15000", 10000, 1),
    ]
    cursor.executemany(
        "INSERT INTO markets (name, address, daily_match_limit,"
        " match_limit_active) VALUES (?, ?, ?, ?)",
        markets,
    )

    # Vendors
    vendors = [
        ("1.11 Juice Bar", None),
        ("412 BBQ", None),
        ("Elfinwild Farms", None),
        ("Fudgie Wudgie", None),
        ("Fungetarian", None),
        ("Healthy Heartbeets", None),
        ("Haffey Family Farm", None),
        ("Hello Hummus", None),
        ("Hughes Farm & Apiary", None),
        ("Jill's gourmet dips", None),
        ("KizzleFoods", None),
        ("Loafers Bread Co.", None),
        ("Machacha Foods", None),
        ("Old School Meats", None),
        ("Olive & Marlowe", None),
        ("Pgh Dumplingz", None),
        ("Pitaland Inc.", None),
        ("Pleasant Lane Farms", None),
        ("Pond Hill Farm LLC", None),
        ("Rockin' Cat Organic Coffee & Tea", None),
        ("Saucy African", None),
        ("Sturges Orchards", None),
        ("The Cakery", None),
    ]
    cursor.executemany("INSERT INTO vendors (name, contact_info) VALUES (?, ?)", vendors)

    # Payment Methods.  FMNP defaults to is_active=0 so it does NOT appear
    # in Receipt Intake / Payment Screen on a fresh "Load Defaults" run.
    # The dedicated FMNP Entry screen continues to work regardless of
    # is_active (it looks up the payment method by name without filtering).
    # Coordinators who want FMNP available as a payment-row option simply
    # toggle it on from Settings → Payment Methods.
    payment_methods = [
        ("SNAP", 100.0, 1, 1, None, None),
        ("FMNP", 100.0, 0, 2, 500, 'Optional'),
        ("Food RX", 100.0, 1, 3, None, None),
        ("JH Food Bucks", 100.0, 1, 4, None, None),
        ("JH Tokens", 100.0, 1, 5, None, None),
        ("Cash", 0.0, 1, 6, None, None),
    ]
    cursor.executemany(
        "INSERT INTO payment_methods (name, match_percent, is_active, sort_order,"
        " denomination, photo_required) VALUES (?, ?, ?, ?, ?, ?)",
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
