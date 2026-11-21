# -*- coding: utf-8 -*-
"""Stage C service layer (C-03/C-05/C-12/C-19): repository scope and contracts."""
from __future__ import annotations

import unittest
from datetime import date

from fa.application import backtesting as service
from fa.backtesting.models import (
    COST_MODEL_DEFAULT,
    BacktestSpecError,
    CostModel,
    Instrument,
    StrategySpec,
    Weighting,
)


def _spec(**overrides) -> StrategySpec:
    values = dict(
        spec_id="stage-c",
        universe=(Instrument("LLY", "US", "USD"),),
        weighting=Weighting.EQUAL,
        start=date(2025, 1, 6),
        end=date(2025, 2, 3),
        initial_capital=100_000.0,
    )
    values.update(overrides)
    return StrategySpec(**values)


class FakeRepository:
    instances = []

    def __init__(self, path):
        self.path = path
        self.closed = False
        FakeRepository.instances.append(self)

    def close(self):
        self.closed = True

    def load_backtest_run(self, run_id):
        return None

    def count_backtest_runs(self):
        return 0


class RunStrategyTests(unittest.TestCase):
    def setUp(self):
        FakeRepository.instances.clear()

    def test_repository_is_opened_with_the_requested_path_and_closed(self):
        seen = {}

        def runner(spec, repository, **kwargs):
            seen["repository"] = repository
            seen["kwargs"] = kwargs
            return object(), True

        result = service.run_strategy(
            _spec(), "C:/tmp/custom.db",
            repository_factory=FakeRepository, runner=runner,
        )
        self.assertEqual(seen["repository"].path.as_posix(), "C:/tmp/custom.db")
        self.assertTrue(seen["repository"].closed)
        self.assertTrue(result.created)
        self.assertFalse(result.reused)

    def test_repository_is_closed_even_when_the_runner_raises(self):
        def runner(spec, repository, **kwargs):
            raise BacktestSpecError("bad spec")

        with self.assertRaises(BacktestSpecError):
            service.run_strategy(_spec(), repository_factory=FakeRepository, runner=runner)
        self.assertTrue(FakeRepository.instances[0].closed)

    def test_created_false_is_passed_through(self):
        result = service.run_strategy(
            _spec(), repository_factory=FakeRepository,
            runner=lambda spec, repository, **kwargs: (object(), False),
        )
        self.assertFalse(result.created)
        self.assertTrue(result.reused)

    def test_force_and_now_are_forwarded(self):
        seen = {}

        def runner(spec, repository, **kwargs):
            seen.update(kwargs)
            return object(), True

        service.run_strategy(_spec(), force=True, now=None,
                             repository_factory=FakeRepository, runner=runner)
        self.assertTrue(seen["force"])
        self.assertIsNone(seen["now"])

    def test_request_cost_source_describes_the_request_only(self):
        default_result = service.run_strategy(
            _spec(), repository_factory=FakeRepository,
            runner=lambda spec, repository, **kwargs: (object(), True),
        )
        self.assertEqual(default_result.request_cost_source, service.COST_SOURCE_DEFAULT)

        custom = CostModel(commission_rate=0.001)
        custom_result = service.run_strategy(
            _spec(costs=custom), repository_factory=FakeRepository,
            runner=lambda spec, repository, **kwargs: (object(), True),
        )
        self.assertEqual(custom_result.request_cost_source, service.COST_SOURCE_CUSTOM)


class LoadAndCountTests(unittest.TestCase):
    def setUp(self):
        FakeRepository.instances.clear()

    def test_load_run_returns_the_persisted_run_and_closes(self):
        sentinel = object()
        repo = FakeRepository("x")
        repo.load_backtest_run = lambda run_id: sentinel
        result = service.load_run("run-1", repository_factory=lambda path: repo)
        self.assertIs(result, sentinel)
        self.assertTrue(repo.closed)

    def test_unknown_run_raises_lookup_error_and_closes(self):
        repo = FakeRepository("x")
        with self.assertRaises(LookupError):
            service.load_run("missing", repository_factory=lambda path: repo)
        self.assertTrue(repo.closed)

    def test_empty_run_id_is_rejected_before_opening_a_repository(self):
        with self.assertRaises(LookupError):
            service.load_run("  ", repository_factory=FakeRepository)
        self.assertEqual(FakeRepository.instances, [])

    def test_count_runs_uses_the_repository_and_closes(self):
        repo = FakeRepository("x")
        repo.count_backtest_runs = lambda: 7
        self.assertEqual(service.count_runs(repository_factory=lambda path: repo), 7)
        self.assertTrue(repo.closed)


class CostPreservingJsonTests(unittest.TestCase):
    def test_default_cost_mode_serialises_as_null_and_round_trips(self):
        spec = _spec()
        payload = service.spec_to_json(spec)
        self.assertIsNone(payload["costs"])
        restored = service.spec_from_json(payload)
        self.assertIs(restored.costs, COST_MODEL_DEFAULT)
        self.assertEqual(restored.spec_id, spec.spec_id)
        self.assertEqual(restored.start, spec.start)
        self.assertEqual(restored.universe, spec.universe)

    def test_missing_costs_key_means_default(self):
        payload = service.spec_to_json(_spec())
        payload.pop("costs")
        self.assertIs(service.spec_from_json(payload).costs, COST_MODEL_DEFAULT)

    def test_custom_cost_mode_is_explicit(self):
        custom = CostModel(commission_rate=0.0012, min_commission=1.5,
                           slippage_bps=7.0, fx_spread_bps=11.0)
        payload = service.spec_to_json(_spec(costs=custom))
        self.assertEqual(payload["costs"], {
            "commission_rate": 0.0012, "min_commission": 1.5,
            "slippage_bps": 7.0, "fx_spread_bps": 11.0,
        })
        restored = service.spec_from_json(payload)
        self.assertIsNot(restored.costs, COST_MODEL_DEFAULT)
        self.assertEqual(restored.costs, custom)

    def test_weighting_rebalance_and_optional_fields_round_trip(self):
        spec = _spec(weighting=Weighting.FIXED, target_weights={"LLY": 1.0},
                     momentum_window=None, benchmark=".NDX", drift_threshold=0.05)
        restored = service.spec_from_json(service.spec_to_json(spec))
        self.assertEqual(restored.weighting, Weighting.FIXED)
        self.assertEqual(dict(restored.target_weights), {"LLY": 1.0})
        self.assertEqual(restored.benchmark, ".NDX")
        self.assertAlmostEqual(restored.drift_threshold, 0.05)

    def test_unknown_cost_keys_are_rejected(self):
        payload = service.spec_to_json(_spec())
        payload["costs"] = {"commission_rate": 0.01, "mystery": 1}
        with self.assertRaises(BacktestSpecError):
            service.spec_from_json(payload)

    def test_invalid_dates_are_reported_as_spec_errors(self):
        payload = service.spec_to_json(_spec())
        payload["start"] = "not-a-date"
        with self.assertRaises(BacktestSpecError):
            service.spec_from_json(payload)


if __name__ == "__main__":
    unittest.main()
