# -*- coding: utf-8 -*-
"""Stage D: the runner persists once, identifies by content and stays idempotent."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fa.backtesting import (
    BacktestMetrics,
    BacktestRun,
    canonical_spec,
    run_backtest,
    with_initial_capital,
)
from fa.backtesting.runner import MODEL_VERSION, _grid, data_fingerprint, run_id_for
from fa.data.database.repository import Repository
from tests.fixtures.backtesting import NOW, build_store, make_spec

UTC = timezone.utc


class RunnerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "runner.db")
        self.addCleanup(self.repository.close)
        self.store = build_store(self.repository, days=40)
        self.spec = make_spec(self.store, sessions=40)


class PersistenceTests(RunnerTestCase):
    def test_run_is_persisted_and_round_trips(self) -> None:
        run, created = run_backtest(self.spec, self.repository, now=NOW)
        self.assertTrue(created)
        self.assertIsInstance(run, BacktestRun)
        self.assertEqual(run.model_version, MODEL_VERSION)
        self.assertEqual(self.repository.count_backtest_runs(), 1)

        stored = self.repository.load_backtest_run(run.run_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.run_id, run.run_id)
        self.assertEqual(stored.spec_id, "fixture")
        self.assertEqual(stored.start, self.spec.start)
        self.assertEqual(stored.end, self.spec.end)
        self.assertEqual(stored.data_fingerprint, run.data_fingerprint)
        self.assertEqual(stored.metrics.as_dict(), run.metrics.as_dict())
        self.assertEqual(len(stored.trades), len(run.trades))
        self.assertEqual(len(stored.equity_curve), len(run.equity_curve))
        self.assertEqual(stored.equity_curve[0].session, run.equity_curve[0].session)
        self.assertEqual(stored.metrics.sessions, len(run.equity_curve))

    def test_spec_json_keeps_the_initial_capital(self) -> None:
        run, _ = run_backtest(self.spec, self.repository, now=NOW)
        import json
        payload = json.loads(run.spec_json)
        self.assertEqual(payload["initial_capital"], 1_000_000.0)
        self.assertEqual(payload["spec_id"], "fixture")
        self.assertEqual(payload["universe"][0]["market"], "US")

    def test_second_identical_request_returns_the_stored_run(self) -> None:
        first, created = run_backtest(self.spec, self.repository, now=NOW)
        self.assertTrue(created)
        second, created_again = run_backtest(self.spec, self.repository, now=NOW)
        self.assertFalse(created_again)
        self.assertEqual(second.run_id, first.run_id)
        self.assertEqual(second.created_at, first.created_at)
        self.assertEqual(self.repository.count_backtest_runs(), 1)

    def test_force_true_writes_a_new_run(self) -> None:
        first, _ = run_backtest(self.spec, self.repository, now=NOW)
        forced, created = run_backtest(self.spec, self.repository, now=NOW, force=True)
        self.assertTrue(created)
        self.assertNotEqual(forced.run_id, first.run_id)
        self.assertTrue(forced.run_id.startswith(first.run_id))
        self.assertEqual(self.repository.count_backtest_runs(), 2)

    def test_initial_capital_changes_the_run_id(self) -> None:
        first, _ = run_backtest(self.spec, self.repository, now=NOW)
        richer = with_initial_capital(self.spec, 2_000_000.0)
        second, created = run_backtest(richer, self.repository, now=NOW)
        self.assertTrue(created)
        self.assertNotEqual(second.run_id, first.run_id)
        self.assertEqual(self.repository.count_backtest_runs(), 2)

    def test_naive_now_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run_backtest(self.spec, self.repository, now=datetime(2026, 1, 1, 12, 0))


class RollbackTests(RunnerTestCase):
    def test_a_failing_write_leaves_no_partial_run(self) -> None:
        self.repository._conn.execute(
            """
            CREATE TRIGGER boom BEFORE INSERT ON strategy_backtest_trades
            BEGIN
                SELECT RAISE(ABORT, 'injected failure');
            END
            """
        )
        with self.assertRaises(Exception):
            run_backtest(self.spec, self.repository, now=NOW)
        self.assertEqual(self.repository.count_backtest_runs(), 0)
        runs = self.repository._conn.execute(
            "SELECT COUNT(*) AS n FROM strategy_backtest_runs").fetchone()
        self.assertEqual(runs["n"], 0)

    def test_an_invalid_run_object_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            self.repository.save_backtest_run("not-a-run")


class FingerprintStabilityTests(RunnerTestCase):
    def test_same_data_gives_the_same_fingerprint(self) -> None:
        from fa.backtesting import DataAccess
        access = DataAccess(self.repository)
        first = data_fingerprint(self.spec, access, sessions=_grid(self.spec),
                                 lag_minutes=30)
        second = data_fingerprint(self.spec, access, sessions=_grid(self.spec),
                                  lag_minutes=30)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_run_id_combines_spec_and_fingerprint(self) -> None:
        from fa.backtesting import DataAccess
        access = DataAccess(self.repository)
        fingerprint = data_fingerprint(self.spec, access,
                                       sessions=_grid(self.spec), lag_minutes=30)
        self.assertEqual(run_id_for(self.spec, fingerprint),
                         run_id_for(self.spec, fingerprint))
        self.assertNotEqual(run_id_for(self.spec, fingerprint),
                            run_id_for(self.spec, "0" * 64))

    def test_canonical_spec_is_stable_and_sorted(self) -> None:
        payload = canonical_spec(self.spec)
        self.assertEqual(list(payload["target_weights"]), sorted(payload["target_weights"]))
        self.assertEqual(payload["start"], self.spec.start.isoformat())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
