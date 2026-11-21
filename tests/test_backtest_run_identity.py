# -*- coding: utf-8 -*-
"""Stage D review P1-1/P1-2/P1-3: run identity and persistence defects.

* P1-1: ``dividend`` and ``split`` participate in the fingerprint, because the
  engine consumes them when a series has no adjusted close.
* P1-2: ``anomalies`` are stored and returned on a cache hit.
* P1-3: the effective settings block (execution lag, fractional shares, risk-free
  rate, resolved cost model) is inside ``run_id`` and actually used.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fa import config
from fa.backtesting import CostModel, DataAccess, run_backtest
from fa.backtesting.models import RebalanceRule
from fa.backtesting.runner import _grid, data_fingerprint
from fa.data.database.repository import Repository
from fa.models.market_data import PriceSeries
from tests.fixtures.backtesting import (
    NOW,
    build_store,
    fx_series,
    make_spec,
    raw_price_point,
    us_instrument,
)

UTC = timezone.utc


class IdentityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "identity.db")
        self.addCleanup(self.repository.close)
        self.store = build_store(self.repository, days=30)
        self.access = DataAccess(self.repository)

    def save_raw(self, closes: list[float], **kwargs) -> None:
        points = [
            raw_price_point(day, close, ticker="LLY", market="US",
                            currency="USD", **kwargs)
            for day, close in zip(self.store.days, closes)
        ]
        self.repository.save_prices(PriceSeries.from_points(
            "LLY", points, market="US", currency="USD", source="fixture"))

    @staticmethod
    def later() -> datetime:
        return datetime.now(UTC) + timedelta(seconds=5)

    def single_asset_spec(self):
        return make_spec(
            self.store, sessions=30,
            universe=(us_instrument(),),
            weighting="fixed",
            target_weights={"LLY": 1.0},
            rebalance=RebalanceRule(frequency="monthly", anchor=1),
            drift_threshold=0.0,
        )


class FingerprintCorporateActionTests(IdentityTestCase):
    def _digests(self, *, dividend=None, split=None) -> tuple[str, str]:
        """(past digest, extended digest) for one corporate-action variant."""
        spec = make_spec(self.store, sessions=30)
        past = _grid(spec)
        future = _next_session()
        with tempfile.TemporaryDirectory() as tmp:
            repository = Repository(Path(tmp) / "actions.db")
            try:
                points = [
                    raw_price_point(day, 100.0, ticker="LLY", market="US",
                                    currency="USD")
                    for day in self.store.days
                ]
                points.append(raw_price_point(
                    future, 100.0, ticker="LLY", market="US", currency="USD",
                    dividend=dividend, split=split))
                repository.save_prices(PriceSeries.from_points(
                    "LLY", points, market="US", currency="USD",
                    source="fixture"))
                repository.save_fx(fx_series(
                    "USD", self.store.days,
                    {day: 7.10 for day in self.store.days}))
                access = DataAccess(repository)
                return (
                    data_fingerprint(spec, access, sessions=past, lag_minutes=30),
                    data_fingerprint(spec, access, sessions=(*past, future),
                                     lag_minutes=30),
                )
            finally:
                repository.close()

    def test_a_dividend_only_change_moves_the_digest(self) -> None:
        past_before, before = self._digests()
        past_after, after = self._digests(dividend=0.9)
        self.assertEqual(past_before, past_after,
                         "未來那一根還不存在時的指紋不應改變")
        self.assertNotEqual(before, after,
                            "股息是引擎會消費的輸入，必須進入指紋")

    def test_a_split_only_change_moves_the_digest(self) -> None:
        _, before = self._digests()
        _, after = self._digests(split=2.0)
        self.assertNotEqual(before, after,
                            "拆股倍率是引擎會消費的輸入，必須進入指紋")


def _next_session():
    from datetime import date, timezone as _tz

    from fa.utils.calendars import calendar_for
    day = datetime.now(_tz.utc).date() + timedelta(days=1)
    calendar = calendar_for("US")
    while not calendar.is_trading_day(day):
        day = day + timedelta(days=1)
    return day


class AnomalyPersistenceTests(IdentityTestCase):
    def _break_one_session(self) -> None:
        self.repository._conn.execute(
            "DELETE FROM market_prices WHERE ticker = 'LLY' AND price_date = ?",
            (self.store.days[1].isoformat(),),
        )
        self.repository._conn.commit()

    def test_anomalies_are_stored_and_returned_on_a_cache_hit(self) -> None:
        self._break_one_session()
        spec = self.single_asset_spec()
        run, created = run_backtest(spec, self.repository, now=NOW)
        self.assertTrue(created)
        self.assertTrue(run.anomalies, "預期缺價會產生 anomaly")
        again, created_again = run_backtest(spec, self.repository, now=NOW)
        self.assertFalse(created_again)
        self.assertEqual(again.anomalies, run.anomalies)
        stored = self.repository.load_backtest_run(run.run_id)
        self.assertEqual(stored.anomalies, run.anomalies)

    def test_anomalies_column_defaults_to_an_empty_list(self) -> None:
        spec = make_spec(self.store, sessions=30)
        run, _ = run_backtest(spec, self.repository, now=NOW)
        blank = replace(run, run_id="f" * 64, anomalies=())
        self.repository.save_backtest_run(blank)
        stored = self.repository.load_backtest_run(blank.run_id)
        self.assertEqual(stored.anomalies, ())


class ConfigIdentityTests(IdentityTestCase):
    def test_execution_lag_is_part_of_the_run_identity(self) -> None:
        spec = make_spec(self.store, sessions=30)
        first, created = run_backtest(spec, self.repository, now=NOW)
        self.assertTrue(created)
        with mock.patch.object(config, "BACKTEST_EXECUTION_LAG_SESSIONS", 2):
            second, created_again = run_backtest(
                spec, self.repository, now=NOW)
        self.assertTrue(created_again)
        self.assertNotEqual(second.run_id, first.run_id)
        self.assertEqual(self.repository.count_backtest_runs(), 2)

    def test_risk_free_rate_is_part_of_the_run_identity(self) -> None:
        spec = make_spec(self.store, sessions=30)
        first, _ = run_backtest(spec, self.repository, now=NOW)
        with mock.patch.object(config, "BACKTEST_RISK_FREE_RATE", 0.20):
            second, created = run_backtest(spec, self.repository, now=NOW)
        self.assertTrue(created)
        self.assertNotEqual(second.run_id, first.run_id)

    def test_fractional_shares_switch_is_part_of_the_run_identity(self) -> None:
        spec = make_spec(self.store, sessions=30)
        first, _ = run_backtest(spec, self.repository, now=NOW)
        with mock.patch.object(config, "BACKTEST_FRACTIONAL_SHARES", False):
            second, created = run_backtest(spec, self.repository, now=NOW)
        self.assertTrue(created)
        self.assertNotEqual(second.run_id, first.run_id)

    def test_the_effective_settings_are_persisted_with_the_run(self) -> None:
        spec = make_spec(self.store, sessions=30)
        with mock.patch.object(config, "BACKTEST_EXECUTION_LAG_SESSIONS", 2):
            run, _ = run_backtest(spec, self.repository, now=NOW)
        payload = json.loads(run.spec_json)
        self.assertEqual(payload["runtime"]["execution_lag_sessions"], 2)
        self.assertIn("costs", payload["runtime"])


class CostConfigTests(IdentityTestCase):
    @staticmethod
    def _commission_total(run) -> float:
        return sum(trade.commission_base for trade in run.trades)

    def test_the_env_commission_rate_reaches_the_cost_model(self) -> None:
        spec = make_spec(self.store, sessions=30)
        cheap, _ = run_backtest(spec, self.repository, now=NOW)
        with mock.patch.object(config, "BACKTEST_COMMISSION_BPS", 50.0):
            dear, created = run_backtest(spec, self.repository, now=NOW)
        self.assertTrue(created)
        cheap_total = self._commission_total(cheap)
        dear_total = self._commission_total(dear)
        self.assertGreater(cheap_total, 0.0)
        self.assertAlmostEqual(dear_total, cheap_total * 10.0,
                               delta=max(0.01, cheap_total * 0.01))

    def test_an_explicit_cost_model_wins_over_the_environment(self) -> None:
        spec = make_spec(
            self.store, sessions=30,
            costs=CostModel(commission_rate=0.0, min_commission=0.0,
                            slippage_bps=0.0, fx_spread_bps=0.0),
        )
        with mock.patch.object(config, "BACKTEST_COMMISSION_BPS", 50.0):
            run, _ = run_backtest(spec, self.repository, now=NOW)
        self.assertEqual(self._commission_total(run), 0.0)
        self.assertTrue(all(trade.slippage_base == 0.0 for trade in run.trades))

    def test_an_explicit_default_cost_model_is_still_explicit(self) -> None:
        """Review P2: ``CostModel()`` is a choice, not an omitted field."""
        with mock.patch.object(config, "BACKTEST_COMMISSION_BPS", 50.0):
            implicit, _ = run_backtest(
                make_spec(self.store, sessions=30), self.repository, now=NOW)
            explicit, created = run_backtest(
                make_spec(self.store, sessions=30, costs=CostModel()),
                self.repository, now=NOW)
        self.assertTrue(created)
        self.assertGreater(self._commission_total(implicit), 0.0)
        self.assertGreater(self._commission_total(explicit), 0.0)
        self.assertNotEqual(implicit.run_id, explicit.run_id)
        # 50 bps against the 5 bps default — the environment really was bypassed.
        self.assertAlmostEqual(self._commission_total(implicit),
                               self._commission_total(explicit) * 10.0,
                               delta=max(0.01, self._commission_total(explicit) * 0.02))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
