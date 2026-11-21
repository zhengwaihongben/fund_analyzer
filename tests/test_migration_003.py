# -*- coding: utf-8 -*-
"""V2.3.1 Stage 2.1 — migration 003 against a real V2.2.3 database.

Acceptance (Stage 2.1 only; the FX pipeline itself belongs to 2.2+):

* schema version moves 2 → 3;
* ``fx_revisions`` and its two indexes exist, and the ledger starts empty;
* every pre-existing table keeps its row count; price and estimation data are
  untouched;
* Migration 003 is purely additive — no column of an existing table changes;
* re-running the migration is a no-op, and an interrupted run rolls back
  completely (version stays at 2, no ``fx_revisions`` table);
* the frozen V2.2.3 baseline itself stays byte-identical: the upgrade only ever
  touches a copy.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fa.config import PROJECT_ROOT
from fa.data.database import connection as connection_mod
from fa.data.database.connection import DatabaseError, schema_version

BASELINE_DB = PROJECT_ROOT / "docs" / "development" / "v2.2.3_baseline" / "database_backup.db"

#: Tables expected after migration 002, i.e. the state 003 starts from.
LEGACY_TABLES = [
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
    "price_revisions",
]

NEW_TABLE = "fx_revisions"

REVISION_COLUMNS = [
    "revision_id",
    "currency",
    "rate_date",
    "rate",
    "base_currency",
    "market_timestamp",
    "available_at",
    "availability_source",
    "source",
    "fetched_at",
    "replaced_at",
    "reason",
]

NEW_INDEXES = {"idx_fx_revisions_currency_date", "idx_fx_revisions_replaced_at"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(conn: sqlite3.Connection, sql: str):
    return tuple(conn.execute(sql).fetchone())


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _indexes(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    present = _tables(conn)
    return {
        name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        for name in LEGACY_TABLES
        if name in present
    }


def upgrade_to(path: Path, version: int) -> int:
    """Apply only the migrations up to ``version`` (test fixture helper)."""
    original = connection_mod.MIGRATIONS
    connection_mod.MIGRATIONS = [(v, sql) for v, sql in original if v <= version]
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        return connection_mod.initialise(conn)
    finally:
        conn.close()
        connection_mod.MIGRATIONS = original


@unittest.skipUnless(BASELINE_DB.exists(), "V2.2.3 baseline database is not available")
class _BaselineCase(unittest.TestCase):
    """Copies the frozen baseline and upgrades the copy to version 2."""

    def setUp(self):
        self.baseline_sha_before = _sha256(BASELINE_DB)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scratch = Path(self._tmp.name) / "upgrade.db"
        shutil.copyfile(BASELINE_DB, self.scratch)
        self.assertEqual(upgrade_to(self.scratch, 2), 2)

        raw = sqlite3.connect(self.scratch)
        raw.row_factory = sqlite3.Row
        try:
            self.before_version = raw.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
            self.before_counts = _counts(raw)
            self.before_tables = _tables(raw)
            self.before_price_columns = _columns(raw, "market_prices")
            self.before_fx_columns = _columns(raw, "fx_rates")
            self.before_indexes = _indexes(raw)
            self.before_price_fingerprint = _rows(
                raw,
                "SELECT COUNT(*), MIN(price_date), MAX(price_date), "
                "ROUND(SUM(close), 6) FROM market_prices",
            )
            self.before_estimation_fingerprint = _rows(
                raw, "SELECT COUNT(*), COUNT(DISTINCT fund_code) FROM estimation_runs"
            )
            self.before_nav_fingerprint = _rows(
                raw, "SELECT COUNT(*), ROUND(SUM(nav), 6) FROM official_nav"
            )
        finally:
            raw.close()

    def tearDown(self):
        # The upgrade must never write to the delivered baseline.
        self.assertEqual(_sha256(BASELINE_DB), self.baseline_sha_before)

    def upgraded(self):
        conn = sqlite3.connect(self.scratch)
        conn.row_factory = sqlite3.Row
        original = connection_mod.MIGRATIONS
        try:
            # Scope the upgrade to Migration 003 itself: later migrations (004)
            # add their own tables and indexes, which this file must not count.
            connection_mod.MIGRATIONS = [(v, sql) for v, sql in original if v <= 3]
            try:
                connection_mod.initialise(conn)
            finally:
                connection_mod.MIGRATIONS = original
            return conn
        except Exception:
            conn.close()
            raise


class Migration003AgainstBaselineTests(_BaselineCase):
    def test_version_moves_from_2_to_3(self):
        self.assertEqual(self.before_version, 2)
        conn = self.upgraded()
        try:
            self.assertEqual(schema_version(conn), 3)
        finally:
            conn.close()

    def test_fx_revisions_table_has_the_documented_columns(self):
        conn = self.upgraded()
        try:
            self.assertIn(NEW_TABLE, _tables(conn))
            self.assertEqual(_columns(conn, NEW_TABLE), REVISION_COLUMNS)
        finally:
            conn.close()

    def test_both_indexes_exist(self):
        conn = self.upgraded()
        try:
            self.assertTrue(NEW_INDEXES <= _indexes(conn))
            # Nothing but the two new indexes was added.
            self.assertEqual(_indexes(conn) - self.before_indexes, NEW_INDEXES)
        finally:
            conn.close()

    def test_ledger_starts_empty(self):
        conn = self.upgraded()
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM fx_revisions").fetchone()[0], 0
            )
        finally:
            conn.close()

    def test_every_legacy_row_count_is_unchanged(self):
        conn = self.upgraded()
        try:
            self.assertEqual(_counts(conn), self.before_counts)
        finally:
            conn.close()

    def test_price_estimation_and_nav_data_are_unchanged(self):
        conn = self.upgraded()
        try:
            self.assertEqual(
                _rows(
                    conn,
                    "SELECT COUNT(*), MIN(price_date), MAX(price_date), "
                    "ROUND(SUM(close), 6) FROM market_prices",
                ),
                self.before_price_fingerprint,
            )
            self.assertEqual(
                _rows(
                    conn,
                    "SELECT COUNT(*), COUNT(DISTINCT fund_code) FROM estimation_runs",
                ),
                self.before_estimation_fingerprint,
            )
            self.assertEqual(
                _rows(conn, "SELECT COUNT(*), ROUND(SUM(nav), 6) FROM official_nav"),
                self.before_nav_fingerprint,
            )
        finally:
            conn.close()

    def test_no_existing_column_or_table_changes(self):
        conn = self.upgraded()
        try:
            self.assertEqual(_columns(conn, "market_prices"), self.before_price_columns)
            self.assertEqual(_columns(conn, "fx_rates"), self.before_fx_columns)
            after = _tables(conn)
            self.assertTrue(self.before_tables <= after)
            self.assertEqual(after - self.before_tables, {NEW_TABLE})
        finally:
            conn.close()

    def test_rerun_is_idempotent(self):
        conn = self.upgraded()
        original = connection_mod.MIGRATIONS
        try:
            connection_mod.MIGRATIONS = [
                (v, sql) for v, sql in original if v <= 3
            ]
            self.assertEqual(connection_mod.initialise(conn), 3)
            self.assertEqual(connection_mod.initialise(conn), 3)
            self.assertEqual(_counts(conn), self.before_counts)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM fx_revisions").fetchone()[0], 0
            )
        finally:
            connection_mod.MIGRATIONS = original
            conn.close()


class Migration003FaultInjectionTests(_BaselineCase):
    def test_rollback_keeps_version_two_and_no_table(self):
        from fa.data.database.schema import MIGRATION_003, MIGRATIONS

        # Guard: 003 is registered (migration 004 is out of scope for this file)
        # and its first statement creates the table.
        self.assertIn((3, MIGRATION_003), MIGRATIONS)
        self.assertIn(NEW_TABLE, MIGRATION_003)

        conn = sqlite3.connect(self.scratch)
        conn.row_factory = sqlite3.Row
        original = connection_mod.MIGRATIONS
        try:
            connection_mod.MIGRATIONS = [
                (v, sql) for v, sql in original if v <= 3
            ]

            def hook(statement: str) -> None:
                if NEW_TABLE in statement:
                    raise RuntimeError("injected failure")

            with self.assertRaises(DatabaseError):
                connection_mod.initialise(conn, _statement_hook=hook)
            # Nothing from the failed attempt may survive.
            self.assertEqual(schema_version(conn), 2)
            self.assertNotIn(NEW_TABLE, _tables(conn))
            self.assertEqual(_counts(conn), self.before_counts)

            # A plain retry must land the table.
            self.assertEqual(connection_mod.initialise(conn), 3)
            self.assertIn(NEW_TABLE, _tables(conn))
            self.assertEqual(_counts(conn), self.before_counts)
        finally:
            connection_mod.MIGRATIONS = original
            conn.close()


class FreshDatabaseTests(unittest.TestCase):
    def test_fresh_database_lands_on_version_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fresh.db"
            original = connection_mod.MIGRATIONS
            connection_mod.MIGRATIONS = [
                (v, sql) for v, sql in original if v <= 3
            ]
            try:
                conn = connection_mod.connect(path)
            finally:
                connection_mod.MIGRATIONS = original
            try:
                self.assertEqual(schema_version(conn), 3)
                self.assertIn(NEW_TABLE, _tables(conn))
                self.assertTrue(NEW_INDEXES <= _indexes(conn))
                self.assertEqual(_columns(conn, NEW_TABLE), REVISION_COLUMNS)
            finally:
                conn.close()

    def test_a_revision_row_round_trips_with_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fresh.db"
            conn = connection_mod.connect(path)
            try:
                conn.execute(
                    "INSERT INTO fx_revisions "
                    "(currency, rate_date, rate, market_timestamp, replaced_at, reason) "
                    "VALUES ('USD', '2026-11-13', 7.10, "
                    "'2026-11-13T00:00:00Z', '2026-11-17T02:00:00Z', 'vendor revision')"
                )
                conn.commit()
                row = conn.execute("SELECT * FROM fx_revisions").fetchone()
                self.assertEqual(row["currency"], "USD")
                self.assertEqual(row["base_currency"], "CNY")
                self.assertEqual(row["availability_source"], "unknown")
                self.assertEqual(row["revision_id"], 1)
            finally:
                conn.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
