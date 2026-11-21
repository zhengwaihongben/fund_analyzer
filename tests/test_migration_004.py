# -*- coding: utf-8 -*-
"""Stage D: Migration 004 is additive, idempotent and rolls back cleanly."""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fa.data.database import connection as connection_mod
from fa.data.database.connection import DatabaseError, initialise
from fa.data.database.schema import MIGRATION_004, MIGRATIONS, SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent
BASELINE_DB = ROOT / "docs" / "development" / "v2.2.3_baseline" / "database_backup.db"
NEW_TABLES = (
    "strategy_backtest_runs",
    "strategy_backtest_metrics",
    "strategy_backtest_trades",
    "strategy_backtest_equity",
)


def _upgrade_to(path: Path, version: int) -> None:
    """Apply only the migrations up to ``version`` (test seam)."""
    original = list(connection_mod.MIGRATIONS)
    try:
        connection_mod.MIGRATIONS[:] = [(v, sql) for v, sql in original if v <= version]
        conn = sqlite3.connect(str(path))
        try:
            conn.row_factory = sqlite3.Row
            initialise(conn)
        finally:
            conn.close()
    finally:
        connection_mod.MIGRATIONS[:] = original


def _version(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return int(row[0])
    finally:
        conn.close()


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def _columns(path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def _counts(path: Path) -> dict[str, int]:
    names = ("market_prices", "fx_rates", "official_nav", "holdings_snapshots",
             "estimation_runs", "estimation_results", "price_revisions", "fx_revisions")
    conn = sqlite3.connect(str(path))
    try:
        out = {}
        for name in names:
            try:
                out[name] = int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            except sqlite3.Error:
                out[name] = -1
        return out
    finally:
        conn.close()


@unittest.skipUnless(BASELINE_DB.exists(), "frozen baseline database is missing")
class Migration004OnBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "baseline.db"
        shutil.copy2(BASELINE_DB, self.path)
        _upgrade_to(self.path, 3)
        self.before_counts = _counts(self.path)
        self.before_tables = _tables(self.path)
        self.before_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.baseline_hash = hashlib.sha256(BASELINE_DB.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.assertEqual(hashlib.sha256(BASELINE_DB.read_bytes()).hexdigest(),
                         self.baseline_hash, "frozen baseline 被修改了")

    def test_version_goes_from_three_to_four(self) -> None:
        self.assertEqual(_version(self.path), 3)
        self.baseline_hash = hashlib.sha256(BASELINE_DB.read_bytes()).hexdigest()
        _upgrade_to(self.path, SCHEMA_VERSION)
        self.assertEqual(_version(self.path), 4)

    def test_new_tables_have_the_documented_columns(self) -> None:
        self.baseline_hash = hashlib.sha256(BASELINE_DB.read_bytes()).hexdigest()
        _upgrade_to(self.path, 4)
        self.assertEqual(
            _columns(self.path, "strategy_backtest_runs"),
            ["run_id", "spec_id", "model_version", "start_date", "end_date",
             "base_currency", "universe_json", "spec_json", "costs_json",
             "data_fingerprint", "look_ahead_status", "created_at",
             "anomalies_json"])
        self.assertEqual(
            _columns(self.path, "strategy_backtest_metrics"),
            ["run_id", "sessions", "cagr", "annualised_vol", "sharpe", "sortino",
             "max_drawdown", "calmar", "turnover", "hit_rate", "exposure",
             "cost_drag", "trades", "total_costs_base"])
        self.assertEqual(
            _columns(self.path, "strategy_backtest_trades"),
            ["run_id", "seq", "session", "ticker", "side", "quantity",
             "price_local", "price_source", "fx_rate", "fx_source",
             "gross_base", "commission_base", "slippage_base",
             "fx_spread_base", "net_base", "reason"])
        self.assertEqual(
            _columns(self.path, "strategy_backtest_equity"),
            ["run_id", "session", "cash_base", "holdings_base", "total_base",
             "exposure", "benchmark_base", "drawdown"])

    def test_existing_data_is_untouched(self) -> None:
        self.baseline_hash = hashlib.sha256(BASELINE_DB.read_bytes()).hexdigest()
        _upgrade_to(self.path, 4)
        self.assertEqual(_counts(self.path), self.before_counts)
        for name in NEW_TABLES:
            self.assertNotIn(name, self.before_tables)
            self.assertIn(name, _tables(self.path))

    def test_rerun_is_idempotent(self) -> None:
        self.baseline_hash = hashlib.sha256(BASELINE_DB.read_bytes()).hexdigest()
        _upgrade_to(self.path, 4)
        first = _counts(self.path)
        _upgrade_to(self.path, 4)
        self.assertEqual(_version(self.path), 4)
        self.assertEqual(_counts(self.path), first)

    def test_failure_rolls_back_and_a_retry_lands(self) -> None:
        self.baseline_hash = hashlib.sha256(BASELINE_DB.read_bytes()).hexdigest()
        original = list(connection_mod.MIGRATIONS)

        def boom(_statement: str) -> None:
            raise RuntimeError("injected failure")

        try:
            connection_mod.MIGRATIONS[:] = [(v, sql) for v, sql in original if v <= 4]
            conn = sqlite3.connect(str(self.path))
            try:
                conn.row_factory = sqlite3.Row
                with self.assertRaises(DatabaseError):
                    initialise(conn, _statement_hook=boom)
            finally:
                conn.close()
        finally:
            connection_mod.MIGRATIONS[:] = original
        self.assertEqual(_version(self.path), 3)
        self.assertNotIn("strategy_backtest_runs", _tables(self.path))

        _upgrade_to(self.path, 4)
        self.assertEqual(_version(self.path), 4)
        self.assertIn("strategy_backtest_runs", _tables(self.path))

    def test_fresh_database_reaches_four(self) -> None:
        self.baseline_hash = hashlib.sha256(BASELINE_DB.read_bytes()).hexdigest()
        fresh = Path(self._tmp.name) / "fresh.db"
        conn = sqlite3.connect(str(fresh))
        try:
            conn.row_factory = sqlite3.Row
            initialise(conn)
        finally:
            conn.close()
        self.assertEqual(_version(fresh), 4)
        for name in NEW_TABLES:
            self.assertIn(name, _tables(fresh))


class Migration004SelfHealTests(unittest.TestCase):
    """A database created by the pre-review build reports v4 without the column."""

    def test_a_missing_review_column_is_added_without_rerunning_the_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "early_v4.db"
            conn = sqlite3.connect(str(path))
            try:
                conn.row_factory = sqlite3.Row
                initialise(conn)
                conn.execute(
                    "ALTER TABLE strategy_backtest_runs DROP COLUMN anomalies_json"
                )
                conn.commit()
            finally:
                conn.close()
            conn = sqlite3.connect(str(path))
            try:
                conn.row_factory = sqlite3.Row
                self.assertEqual(initialise(conn), 4)
            finally:
                conn.close()
            columns = {
                row[1] for row in sqlite3.connect(str(path)).execute(
                    "PRAGMA table_info(strategy_backtest_runs)"
                )
            }
            self.assertIn("anomalies_json", columns)
            self.assertEqual(_version(path), 4)

    def test_the_repair_is_scoped_to_the_review_column(self) -> None:
        # A Migration 002 column dropped from a v4 database must stay dropped:
        # re-adding it here would leave its backfill SQL unrun.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "early_v4_half.db"
            conn = sqlite3.connect(str(path))
            try:
                conn.row_factory = sqlite3.Row
                initialise(conn)
                conn.execute(
                    "ALTER TABLE strategy_backtest_runs DROP COLUMN anomalies_json"
                )
                conn.execute("ALTER TABLE market_prices DROP COLUMN dividend")
                conn.commit()
            finally:
                conn.close()
            conn = sqlite3.connect(str(path))
            try:
                conn.row_factory = sqlite3.Row
                self.assertEqual(initialise(conn), 4)
            finally:
                conn.close()
            conn = sqlite3.connect(str(path))
            try:
                repaired = {
                    row[1] for row in conn.execute(
                        "PRAGMA table_info(strategy_backtest_runs)"
                    )
                }
                untouched = {
                    row[1] for row in conn.execute("PRAGMA table_info(market_prices)")
                }
            finally:
                conn.close()
            self.assertIn("anomalies_json", repaired)
            self.assertNotIn("dividend", untouched)


class Migration004StatementTests(unittest.TestCase):
    def test_migration_is_registered_last(self) -> None:
        self.assertEqual(MIGRATIONS[-1][0], 4)
        self.assertEqual(MIGRATIONS[-1][1], MIGRATION_004)

    def test_migration_only_adds(self) -> None:
        lowered = MIGRATION_004.lower()
        self.assertNotIn("drop table", lowered)
        self.assertNotIn("alter table", lowered)
        self.assertNotIn("delete from", lowered)
        for name in NEW_TABLES:
            self.assertIn(f"create table if not exists {name}", lowered)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
