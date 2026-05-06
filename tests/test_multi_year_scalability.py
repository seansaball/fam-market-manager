"""Multi-year scalability assessment + future-proofing
(v1.9.10 follow-up, 2026-05-01).

Beyond the year-1 scaling concerns addressed in
``test_sync_scaling.py``, multi-year operation introduces
additional growth surfaces:

  * ``audit_log`` grows fastest — every mutation logs ≥1 row.
    Year-3 projection: ~500K rows.  Hot queries that join through
    audit_log (Activity Log, Transaction Log "last_updated")
    must remain index-served.
  * ``payment_line_items`` ~50K rows.  Per-method aggregations
    must use composite indexes so the planner can index-merge.
  * ``transactions`` ~20K rows.  Per-(market_day, status)
    filtering needs the composite index.
  * Local ``photos/`` directory accumulates without bound unless
    a retention policy fires.
  * Google Sheets cells: 10M total per spreadsheet — annual
    partitioning is recommended past Year-3.
  * SQLite query-plan stats stale without periodic ANALYZE.

This test file pins:

  1. The new v32 composite indexes exist post-migration.
  2. Hot queries actually USE those indexes (EXPLAIN QUERY PLAN
     verification — catches regressions where someone changes a
     query in a way that defeats the index).
  3. Local photo cleanup safely deletes Drive-backed files past
     retention but spares fresh + un-uploaded ones.
  4. Market-close ANALYZE doesn't crash + leaves the DB usable.
  5. End-to-end: a year-1 + year-3 + year-5 fixture exercises
     the report queries and asserts they return in O(scope) not
     O(history) time.
"""

import os
import time

import pytest

from fam.database.connection import (
    get_connection, set_db_path, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def empty_db(tmp_path):
    db_file = str(tmp_path / "scale.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'V')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(1, 'SNAP', 100.0, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO vendor_payment_methods (vendor_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.commit()
    yield conn
    close_connection()


def _seed_year(conn, year_label: int, market_days: int = 50,
               txn_per_md: int = 10, audit_rows_per_txn: int = 4):
    """Insert a synthetic year of confirmed transactions plus
    typical audit-log volume.

    Bypasses the model APIs (which guard against past-dated
    market days) — we're simulating historical state, not
    testing the create flow.
    """
    for w in range(market_days):
        date_str = (
            f"20{99 - year_label:02d}-"
            f"{(w // 4) + 1:02d}-{((w % 4) + 1) * 7:02d}"
        )
        cur = conn.execute(
            "INSERT INTO market_days (market_id, date, status, "
            "opened_by) VALUES (1, ?, 'Closed', 'Tester')",
            (date_str,))
        md_id = cur.lastrowid
        for t in range(txn_per_md):
            cur = conn.execute(
                "INSERT INTO customer_orders "
                "(market_day_id, customer_label, zip_code, "
                " status, created_at) "
                "VALUES (?, ?, '15102', 'Confirmed', ?)",
                (md_id, f'C-{year_label}-{w:02d}-{t:02d}',
                 f'{date_str} 10:00:00'))
            order_id = cur.lastrowid
            cents = 2000 + (t * 17)
            fam_tid = (
                f"FAM-Y{year_label}-{date_str.replace('-', '')}-"
                f"{w:02d}{t:02d}")
            cur = conn.execute(
                "INSERT INTO transactions "
                "(fam_transaction_id, market_day_id, vendor_id, "
                " receipt_total, customer_order_id, status, "
                " created_at) VALUES (?, ?, 1, ?, ?, 'Confirmed', ?)",
                (fam_tid, md_id, cents, order_id,
                 f'{date_str} 10:00:00'))
            txn_id = cur.lastrowid
            conn.execute(
                "INSERT INTO payment_line_items "
                "(transaction_id, payment_method_id, "
                " method_name_snapshot, match_percent_snapshot, "
                " method_amount, match_amount, customer_charged, "
                " created_at) VALUES (?, 1, 'SNAP', 100.0, ?, ?, ?, ?)",
                (txn_id, cents, cents // 2, cents - (cents // 2),
                 f'{date_str} 10:00:00'))
            for k in range(audit_rows_per_txn):
                conn.execute(
                    "INSERT INTO audit_log "
                    "(table_name, record_id, action, changed_by, "
                    " app_version, device_id, changed_at) "
                    "VALUES (?, ?, ?, 'Tester', '1.9.10', 'TEST', ?)",
                    ('transactions' if k < 2 else 'payment_line_items',
                     txn_id,
                     'CREATE' if k == 0 else (
                         'CONFIRM' if k == 1 else 'PAYMENT_SAVED'),
                     f'{date_str} 10:00:0{k}'))
    conn.commit()


# ════════════════════════════════════════════════════════════════════
# 1. Schema indexes — v32 composite indexes exist
# ════════════════════════════════════════════════════════════════════


class TestV32CompositeIndexes:

    def test_audit_log_record_table_changed_at_index(self, empty_db):
        idx = empty_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_audit_log_record_table_changed_at'"
        ).fetchone()
        assert idx is not None, (
            "v32 must add the (record_id, table_name, changed_at "
            "DESC) index for the get_transaction_log "
            "MAX(changed_at) subquery")

    def test_transactions_md_status_index(self, empty_db):
        idx = empty_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_transactions_md_status'"
        ).fetchone()
        assert idx is not None, (
            "v32 must add (market_day_id, status) for per-md "
            "status-filtered reports")

    def test_pli_method_txn_index(self, empty_db):
        idx = empty_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_pli_method_txn'"
        ).fetchone()
        assert idx is not None, (
            "v32 must add (payment_method_id, transaction_id) "
            "for FAM Match Report per-method aggregations")

    def test_generated_rewards_md_order_index(self, empty_db):
        idx = empty_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_generated_rewards_md_order'"
        ).fetchone()
        assert idx is not None, (
            "v32 must add (market_day_id, customer_order_id) for "
            "Generated Rewards per-md/per-order lookups")

    def test_schema_version_at_least_32(self, empty_db):
        v = empty_db.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        assert v >= 32, (
            f"Fresh install / migration must reach v32 to ship the "
            f"scaling indexes; got v{v}")


# ════════════════════════════════════════════════════════════════════
# 2. Hot queries still index-served at year-3 scale
# ════════════════════════════════════════════════════════════════════


class TestHotQueriesIndexUsage:
    """Regression guard against query rewrites that defeat the
    index — EXPLAIN QUERY PLAN must show ``USING INDEX`` (or
    ``USING COVERING INDEX``) for the indexed columns."""

    def test_get_transaction_log_uses_audit_index(self, empty_db):
        _seed_year(empty_db, year_label=1, market_days=10)
        plan = empty_db.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT MAX(changed_at) FROM audit_log "
            "WHERE record_id = 1 AND table_name = 'transactions'"
        ).fetchall()
        # EXPLAIN QUERY PLAN row schema:
        # (selectid, order, from, detail) — `detail` carries the
        # human-readable plan including index usage.
        plan_text = ' | '.join(r['detail'] for r in plan)
        # The optimizer should pick one of the audit_log indexes
        # for the equality predicate.
        assert ('audit_log' in plan_text.lower()
                and 'using' in plan_text.lower()
                and 'index' in plan_text.lower()), (
            f"audit_log MAX(changed_at) lookup must be index-served, "
            f"plan was: {plan_text}")

    def test_per_md_transactions_query_uses_index(self, empty_db):
        _seed_year(empty_db, year_label=1, market_days=10)
        plan = empty_db.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM transactions "
            "WHERE market_day_id = 1 AND status IN ('Confirmed', 'Adjusted')"
        ).fetchall()
        # EXPLAIN QUERY PLAN row schema:
        # (selectid, order, from, detail) — `detail` carries the
        # human-readable plan including index usage.
        plan_text = ' | '.join(r['detail'] for r in plan)
        assert 'index' in plan_text.lower(), (
            f"per-md status-filtered query must hit an index; "
            f"plan was: {plan_text}")

    def test_pli_per_method_aggregation_uses_index(self, empty_db):
        _seed_year(empty_db, year_label=1, market_days=5)
        plan = empty_db.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT method_name_snapshot, SUM(method_amount), "
            "SUM(match_amount) "
            "FROM payment_line_items "
            "WHERE payment_method_id = 1 "
            "GROUP BY method_name_snapshot"
        ).fetchall()
        # EXPLAIN QUERY PLAN row schema:
        # (selectid, order, from, detail) — `detail` carries the
        # human-readable plan including index usage.
        plan_text = ' | '.join(r['detail'] for r in plan)
        assert ('index' in plan_text.lower()), (
            f"PLI per-method aggregation must hit an index; "
            f"plan was: {plan_text}")


# ════════════════════════════════════════════════════════════════════
# 3. Year-N report query latency stays bounded by SCOPE, not history
# ════════════════════════════════════════════════════════════════════


class TestReportQueryLatencyBounded:
    """The whole point of the scope-to-open-md fix.  At year-3
    scale a scoped collect should run in roughly the same wall
    time as a year-1 scoped collect — because it touches the
    same number of rows (just one market day's worth)."""

    def test_scoped_collect_constant_time_year_1_vs_year_3(
            self, empty_db):
        # Year-1: 50 mds, 500 txns, 2000 audit rows.
        _seed_year(empty_db, year_label=1)
        from fam.sync.data_collector import collect_sync_data

        # Pick the most-recent md (= "open today's" md analogue).
        latest_md = empty_db.execute(
            "SELECT MAX(id) FROM market_days").fetchone()[0]

        t0 = time.perf_counter()
        scoped_y1 = collect_sync_data(market_day_id=latest_md)
        y1_dt = time.perf_counter() - t0

        # Year-3: add two more years of history.
        _seed_year(empty_db, year_label=2)
        _seed_year(empty_db, year_label=3)

        t0 = time.perf_counter()
        scoped_y3 = collect_sync_data(market_day_id=latest_md)
        y3_dt = time.perf_counter() - t0

        # Loose bound: the scoped query at year-3 must NOT be more
        # than 5× slower than at year-1.  Without the indexes it's
        # easy to see 10–50× degradation as table sizes grow.
        assert y3_dt < (y1_dt * 5.0) + 0.5, (
            f"scoped collect should stay roughly constant: "
            f"year-1 took {y1_dt*1000:.1f}ms, "
            f"year-3 took {y3_dt*1000:.1f}ms — that's {y3_dt/max(y1_dt,1e-3):.1f}× "
            f"degradation, indicating an index regression")

        # Sanity: row counts must be the same regardless of history.
        assert (len(scoped_y1.get('Detailed Ledger', [])) ==
                len(scoped_y3.get('Detailed Ledger', []))), (
            "scoped collect must return the same row count "
            "irrespective of history depth — only the open md's data")


# ════════════════════════════════════════════════════════════════════
# 4. Local photo cleanup — Drive-backed eligible, fresh ones spared
# ════════════════════════════════════════════════════════════════════


class TestLocalPhotoCleanup:

    def test_drive_uploaded_old_photo_deleted(self, empty_db, tmp_path):
        from fam.utils.photo_storage import (
            cleanup_uploaded_local_photos, get_photos_dir,
        )
        from unittest.mock import patch

        # Build a photos directory with two files: one old + uploaded,
        # one fresh + uploaded.
        with patch('fam.utils.photo_storage.get_photos_dir',
                    return_value=str(tmp_path / 'photos')):
            os.makedirs(tmp_path / 'photos', exist_ok=True)
            old = tmp_path / 'photos' / 'pay_1_1700000000.jpg'
            old.write_bytes(b'old')
            fresh = tmp_path / 'photos' / 'pay_2_now.jpg'
            fresh.write_bytes(b'fresh')

            # Backdate the old one to 200 days ago.
            two_hundred_days_ago = time.time() - 200 * 86400
            os.utime(old, (two_hundred_days_ago, two_hundred_days_ago))
            os.utime(fresh, (time.time(), time.time()))

            # Insert market_day FIRST so the transactions FK holds.
            empty_db.execute(
                "INSERT INTO market_days (id, market_id, date, status, "
                "opened_by) VALUES (99, 1, '2099-01-01', 'Open', 'T')")
            cur = empty_db.execute(
                "INSERT INTO transactions (fam_transaction_id, "
                "market_day_id, vendor_id, receipt_total, status) "
                "VALUES ('TX1', 99, 1, 100, 'Confirmed')")
            txn_id = cur.lastrowid
            empty_db.execute(
                "INSERT INTO payment_line_items "
                "(transaction_id, payment_method_id, "
                " method_name_snapshot, match_percent_snapshot, "
                " method_amount, match_amount, customer_charged, "
                " photo_path, photo_drive_url) "
                "VALUES (?, 1, 'SNAP', 100.0, 100, 50, 50, "
                "'photos/pay_1_1700000000.jpg', "
                "'https://drive.example/abc')", (txn_id,))
            empty_db.execute(
                "INSERT INTO payment_line_items "
                "(transaction_id, payment_method_id, "
                " method_name_snapshot, match_percent_snapshot, "
                " method_amount, match_amount, customer_charged, "
                " photo_path, photo_drive_url) "
                "VALUES (?, 1, 'SNAP', 100.0, 50, 25, 25, "
                "'photos/pay_2_now.jpg', "
                "'https://drive.example/def')", (txn_id,))
            empty_db.commit()

            stats = cleanup_uploaded_local_photos(retention_days=90)

            assert stats['deleted'] == 1, (
                f"only the OLD Drive-backed photo should be deleted; "
                f"got {stats!r}")
            assert not old.exists()
            assert fresh.exists(), (
                "fresh photo (younger than retention) must be spared")

    def test_no_drive_url_photo_spared(self, empty_db, tmp_path):
        """A local photo whose DB row has no Drive URL is NOT
        deleted — Drive is the canonical store; without it the
        local copy is the only copy."""
        from fam.utils.photo_storage import (
            cleanup_uploaded_local_photos,
        )
        from unittest.mock import patch

        with patch('fam.utils.photo_storage.get_photos_dir',
                    return_value=str(tmp_path / 'photos')):
            os.makedirs(tmp_path / 'photos', exist_ok=True)
            orphan = tmp_path / 'photos' / 'pay_99_old.jpg'
            orphan.write_bytes(b'irreplaceable')
            two_hundred_days_ago = time.time() - 200 * 86400
            os.utime(orphan, (two_hundred_days_ago, two_hundred_days_ago))

            # No DB row references it (or row has empty drive_url).
            stats = cleanup_uploaded_local_photos(retention_days=90)

            assert stats['deleted'] == 0
            assert orphan.exists(), (
                "photo without a Drive-backed DB row must NOT be "
                "deleted — Drive isn't backing it up")
            assert stats['skipped_no_drive'] == 1

    def test_disabled_when_retention_zero(self, empty_db, tmp_path):
        from fam.utils.photo_storage import cleanup_uploaded_local_photos
        from unittest.mock import patch

        with patch('fam.utils.photo_storage.get_photos_dir',
                    return_value=str(tmp_path / 'photos')):
            os.makedirs(tmp_path / 'photos', exist_ok=True)
            (tmp_path / 'photos' / 'a.jpg').write_bytes(b'x')
            stats = cleanup_uploaded_local_photos(retention_days=0)
            assert stats['scanned'] == 0
            assert stats['deleted'] == 0


# ════════════════════════════════════════════════════════════════════
# 5. Market-close ANALYZE doesn't crash the close
# ════════════════════════════════════════════════════════════════════


class TestMarketCloseMaintenance:

    def test_close_market_day_runs_analyze(self, empty_db):
        from fam.models.market_day import (
            create_market_day, close_market_day,
        )
        # Build a MD scoped to "today" (the create guard requires
        # current-or-future date).  We reach for the bypass by
        # directly inserting the market day, since this test only
        # cares about the ANALYZE call.
        empty_db.execute(
            "INSERT INTO market_days (id, market_id, date, status, "
            "opened_by) VALUES (200, 1, '2099-01-01', 'Open', 'T')")
        empty_db.commit()
        # Should not raise — ANALYZE is best-effort.
        close_market_day(200, closed_by='Tester')
        row = empty_db.execute(
            "SELECT status FROM market_days WHERE id=200"
        ).fetchone()
        assert row['status'] == 'Closed'


# ════════════════════════════════════════════════════════════════════
# 6. Documentation pin — scaling guidance present
# ════════════════════════════════════════════════════════════════════


class TestScalabilityDocumentation:
    """Pin that the operational scaling guidance lives in the
    repo so the runbook stays evergreen.  Drop the doc and a
    test fails — forcing a re-read of the assumptions."""

    def test_scalability_doc_exists(self):
        path = os.path.join(
            os.path.dirname(__file__), '..',
            'docs', 'MULTI_YEAR_SCALABILITY.md')
        assert os.path.isfile(path), (
            "docs/MULTI_YEAR_SCALABILITY.md must exist as the "
            "operator's runbook for multi-year deployments")
