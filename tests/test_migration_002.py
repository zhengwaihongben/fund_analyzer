# -*- coding: utf-8 -*-
"""V2.3.1 Stage 1 — migration 002 against a real V2.2.3 database.

Acceptance class C (database integrity):

* the upgrade runs on a **copy** of the delivered V2.2.3 baseline, never on the
  baseline itself;
* old data survives the migration (row counts and values unchanged);
* the four-state look-ahead status is back-filled conservatively — a legacy
  ``look_ahead_safe = 1`` becomes ``unknown`` because the boolean already lost
  the information, only ``0`` becomes ``unsafe``;
* an interrupted migration rolls back completely and can simply be re-run;
* after the upgrade the V2.2 read paths still work.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fa.config import PROJECT_ROOT
from fa.data.database import connection as connection_mod
from fa.data.database.connection import DatabaseError, connect, schema_version
from fa.data.database.schema import SCHEMA_VERSION
from fa.data.database.repository import Repository
from fa.models.market_data import AvailabilitySource, PricePoint, PriceSeries

BASELINE_DB = PROJECT_ROOT / "docs" / "development" / "v2.2.3_baseline" / "database_backup.db"
UTC = timezone.utc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        "funds",
        "assets",
        "holdings_snapshots",
        "holdings",
        "market_prices",
        "fx_rates",
        "official_nav",
        "estimation_runs",
        "estimation_inputs",
        "estimation_results",
        "backtest_results",
    ]
    return {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in tables}


@unittest.skipUnless(BASELINE_DB.exists(), "V2.2.3 baseline database is not available")
class _BaselineCase(unittest.TestCase):
    """Copies the frozen baseline into a scratch directory for each test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scratch = Path(self._tmp.name) / "upgrade.db"
        shutil.copyfile(BASELINE_DB, self.scratch)
        # Read the pristine state before touching the copy.
        raw = sqlite3.connect(self.scratch)
        try:
            self.before_counts = _counts(raw)
            self.before_version = raw.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            self.before_look_ahead = raw.execute(
                "SELECT look_ahead_safe, COUNT(*) FROM estimation_runs GROUP BY look_ahead_safe"
            ).fetchall()
            self.before_nav_rows = raw.execute(
                "SELECT COUNT(*) FROM official_nav WHERE fund_code = '000369'"
            ).fetchone()[0]
        finally:
            raw.close()


class MigrationAgainstBaselineTests(_BaselineCase):
    def test_version_moves_from_1_to_2(self):
        self.assertEqual(self.before_version, 1)
        conn = connect(self.scratch)
        try:
            # Later migrations may raise this further; what this test needs is
            # that 002 landed (the column and ledger assertions below prove it).
            self.assertEqual(schema_version(conn), SCHEMA_VERSION)
            self.assertGreaterEqual(schema_version(conn), 2)
        finally:
            conn.close()

    def test_existing_rows_survive(self):
        conn = connect(self.scratch)
        try:
            self.assertEqual(_counts(conn), self.before_counts)
        finally:
            conn.close()

    def test_new_columns_exist(self):
        conn = connect(self.scratch)
        try:
            prices = _columns(conn, "market_prices")
            for name in (
                "market_timestamp",
                "available_at",
                "availability_source",
                "availability_note",
                "fetched_at",
                "first_fetched_at",
                "dividend",
                "split",
            ):
                self.assertIn(name, prices)
            self.assertIn("look_ahead_status", _columns(conn, "estimation_runs"))
            self.assertIn("base_currency", _columns(conn, "fx_rates"))
        finally:
            conn.close()

    def test_price_revisions_table_exists(self):
        conn = connect(self.scratch)
        try:
            columns = _columns(conn, "price_revisions")
        finally:
            conn.close()
        for name in (
            "revision_id",
            "ticker",
            "price_date",
            "price_type",
            "close",
            "adj_close",
            "market_timestamp",
            "available_at",
            "availability_source",
            "fetched_at",
            "dividend",
            "split",
            "replaced_at",
            "reason",
        ):
            self.assertIn(name, columns)

    def test_look_ahead_backfill_is_conservative(self):
        conn = connect(self.scratch)
        try:
            rows = conn.execute(
                "SELECT look_ahead_safe, look_ahead_status, COUNT(*) FROM estimation_runs "
                "GROUP BY look_ahead_safe, look_ahead_status"
            ).fetchall()
        finally:
            conn.close()
        mapping = {(int(safe), status): count for safe, status, count in rows}
        for safe, count in self.before_look_ahead:
            safe = int(safe)
            expected = "unsafe" if safe == 0 else "unknown"
            self.assertEqual(mapping.get((safe, expected), 0), count,
                             f"look_ahead_safe={safe} 應回填為 {expected}")
        self.assertNotIn("verified-safe", {status for _, status in mapping})
        self.assertNotIn("estimated-disclosure", {status for _, status in mapping})

    def test_legacy_v22_market_rows_get_unknown_availability(self):
        conn = connect(self.scratch)
        try:
            rows = conn.execute(
                "SELECT availability_source, COUNT(*) FROM market_prices GROUP BY availability_source"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(dict(rows), {"unknown": self.before_counts["market_prices"]})

    def test_v22_read_paths_still_work_after_upgrade(self):
        repo = Repository(path=self.scratch)
        try:
            prices = repo.load_prices("LLY")
            self.assertGreen(prices)
            self.assertGreater(len(prices.points), 0)
            self.assertEqual(prices.points[0].availability_source, AvailabilitySource.UNKNOWN)
            self.assertFalse(prices.points[0].is_pit_usable)

            fx = repo.load_fx("USD")
            self.assertEqual(fx.currency, "USD")
            self.assertEqual(fx.base_currency, "CNY")
            self.assertEqual(len(fx.points), self.before_counts["fx_rates"])

            nav = repo.load_official_nav("000369")
            self.assertEqual(len(nav), self.before_nav_rows)

            runs = repo.list_estimations("000369", limit=100)
            self.assertEqual(len(runs), self.before_counts["estimation_runs"])
            detail = repo.load_estimation(runs[0]["run_id"])
            self.assertIsNotNone(detail)
        finally:
            repo.close()

    def assertGreen(self, series):
        self.assertEqual(series.ticker, "LLY")

    def test_new_availability_fields_round_trip_through_the_database(self):
        repo = Repository(path=self.scratch)
        try:
            market = datetime(2026, 11, 13, 21, 0, tzinfo=UTC)
            available = market + timedelta(minutes=30)
            series = PriceSeries(
                ticker="TEST.AV",
                market="US",
                currency="USD",
                source="unit-test",
                points=[
                    PricePoint(
                        price_date=date(2026, 11, 13),
                        close=100.0,
                        adj_close=99.5,
                        market_timestamp=market,
                        available_at=available,
                        availability_source=AvailabilitySource.INFERRED,
                        availability_note="market close + 30 分鐘",
                        dividend=0.25,
                        split=1.0,
                    )
                ],
            )
            repo.save_prices(series)
            back = repo.load_prices("TEST.AV")
        finally:
            repo.close()
        point = back.points[0]
        self.assertEqual(point.market_timestamp, market)
        self.assertEqual(point.available_at, available)
        self.assertIs(point.availability_source, AvailabilitySource.INFERRED)
        self.assertEqual(point.availability_note, "market close + 30 分鐘")
        self.assertEqual(point.dividend, 0.25)
        self.assertEqual(point.split, 1.0)
        self.assertTrue(point.is_pit_usable)


class MigrationFaultInjectionTests(_BaselineCase):
    def test_interrupted_migration_rolls_back_and_can_be_rerun(self):
        conn = sqlite3.connect(self.scratch)
        conn.row_factory = sqlite3.Row
        calls = {"n": 0}

        def hook(_statement: str) -> None:
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("simulated crash")

        try:
            with self.assertRaises(DatabaseError):
                connection_mod.initialise(conn, _statement_hook=hook)
            # Nothing from the failed attempt may survive.
            self.assertEqual(schema_version(conn), 1)
            self.assertNotIn("market_timestamp", _columns(conn, "market_prices"))
            self.assertNotIn("look_ahead_status", _columns(conn, "estimation_runs"))
            self.assertEqual(_counts(conn), self.before_counts)
            self.assertGreaterEqual(calls["n"], 3)

            # A plain retry must succeed.
            version = connection_mod.initialise(conn)
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertIn("market_timestamp", _columns(conn, "market_prices"))
            self.assertEqual(_counts(conn), self.before_counts)
        finally:
            conn.close()

    def test_rerun_is_idempotent(self):
        conn = connect(self.scratch)
        try:
            self.assertEqual(connection_mod.initialise(conn), SCHEMA_VERSION)
            self.assertEqual(connection_mod.initialise(conn), SCHEMA_VERSION)
            self.assertEqual(_counts(conn), self.before_counts)
        finally:
            conn.close()


class FreshDatabaseTests(unittest.TestCase):
    def test_fresh_database_creates_the_latest_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fresh.db"
            conn = connect(path)
            try:
                self.assertEqual(schema_version(conn), SCHEMA_VERSION)
                self.assertIn("available_at", _columns(conn, "market_prices"))
                self.assertIn("price_revisions", {
                    row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                })
            finally:
                conn.close()

    def test_fresh_database_has_no_rows_to_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "fresh.db")
            try:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM estimation_runs").fetchone()[0], 0
                )
            finally:
                conn.close()


class BaselineImmutabilityTests(unittest.TestCase):
    def test_the_frozen_baseline_is_never_modified(self):
        if not BASELINE_DB.exists():
            self.skipTest("baseline missing")
        before = _sha256(BASELINE_DB)
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "copy.db"
            shutil.copyfile(BASELINE_DB, copy)
            conn = connect(copy)
            conn.close()
        self.assertEqual(_sha256(BASELINE_DB), before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
