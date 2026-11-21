# -*- coding: utf-8 -*-
"""Benchmark curve tests (Stage D, reviews P1-5, P1-6 and P1-7).

The benchmark is a price series read through the same PIT rules as the
strategy, session by session, and its consumed bars join the run fingerprint.
These tests pin the two defects that review found after the first Stage D
delivery:

* the curve must not look ahead — a bar (or an FX rate) published after a
  session's decision instant cannot fill that session;
* the benchmark's FX values and their version identity must be part of the
  fingerprint, so revising GBP/CNY changes the identity of a run whose strategy
  never touched GBP.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fa.backtesting.benchmark import (
    benchmark_levels,
    benchmark_rows,
    benchmark_series,
)
from fa.backtesting.data_access import DataAccess
from fa.backtesting.models import CostModel, Instrument, StrategySpec
from fa.backtesting.runner import data_fingerprint, run_backtest
from fa.data.database.repository import Repository
from fa.data.price_pipeline import infer_availability
from fa.models.market_data import AvailabilitySource, FxSeries, PriceSeries
from tests.fixtures.backtesting import (
    NOW,
    build_store,
    make_spec,
    price_point,
    trading_days,
)

UTC = timezone.utc
LAG = 30


def _series(ticker: str, market: str, currency: str, points) -> PriceSeries:
    return PriceSeries.from_points(
        ticker, list(points), market=market, currency=currency, source="fixture")


def _fx(currency: str, points) -> FxSeries:
    return FxSeries.from_bars(currency, list(points), source="fixture", base_currency="CNY")


class BenchmarkTestCase(unittest.TestCase):
    """A repository with the standard fixtures plus a benchmark series."""

    days: list[date]

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "bench.db"
        self.repository = Repository(self.path)
        self.addCleanup(self.repository.close)
        self.store = build_store(self.repository, days=20)
        self.days = trading_days(6)
        self.access = DataAccess(self.repository)
        self.spec = make_spec(benchmark="SPY", sessions=6)

    # -- helpers ---------------------------------------------------------
    def save_prices(self, ticker: str, market: str, currency: str,
                    closes: list[float], *, published=None) -> None:
        points = []
        for index, day in enumerate(self.days):
            when = None
            if published is not None:
                when = published.get(index)
            points.append(
                price_point(day, closes[index], ticker=ticker, market=market,
                            currency=currency, available_at=when))
        self.repository.save_prices(_series(ticker, market, currency, points))

    def save_fx(self, currency: str, rates: dict[date, float],
                *, published=None) -> None:
        points = []
        for day, rate in rates.items():
            when = None
            if published is not None:
                when = published.get(day)
            points.append(price_point(day, rate, ticker=currency, market="FX",
                                      currency="CNY", available_at=when))
        self.repository.save_fx(_fx(currency, points))

    def curve(self, spec: StrategySpec | None = None, sessions=None):
        return benchmark_series(
            spec or self.spec, self.access, sessions or self.days, lag_minutes=LAG)

    def instant(self, day: date) -> datetime:
        return infer_availability(day, "US")[0]


class BenchmarkPitTests(BenchmarkTestCase):
    """Review P1-6: the curve never borrows a bar from the future."""

    def test_a_price_published_after_its_session_is_not_used(self) -> None:
        late = infer_availability(self.days[4], "US")[0]
        self.save_prices("SPY", "US", "USD", [100.0] * 6, published={2: late})
        series = self.curve()
        self.assertIsNotNone(series)
        self.assertIsNotNone(series.levels[1])
        self.assertIsNone(series.levels[2])
        self.assertTrue(any(item.startswith("benchmark-missing-price")
                            for item in series.anomalies))

    def test_a_price_published_before_the_decision_instant_is_used(self) -> None:
        on_time = infer_availability(self.days[2], "US")[0]
        self.save_prices("SPY", "US", "USD", [100.0] * 6, published={2: on_time})
        series = self.curve()
        self.assertIsNotNone(series.levels[2])
        self.assertFalse(any(item.startswith("benchmark-missing-price")
                             for item in series.anomalies))

    def test_a_fx_rate_published_later_cannot_convert_the_session(self) -> None:
        self.spec = make_spec(benchmark="SPX", sessions=6)
        self.save_prices("SPX", "US", "GBP", [100.0] * 6)
        late = infer_availability(self.days[4], "FX")[0]
        self.save_fx("GBP", {self.days[2]: 8.2}, published={self.days[2]: late})
        series = self.curve()
        self.assertIsNone(series.levels[2])
        self.assertTrue(any(item.startswith("benchmark-missing-fx")
                            for item in series.anomalies))

    def test_a_closed_venue_carries_the_previous_level(self) -> None:
        sessions = [date(2025, 1, 16), date(2025, 1, 17), date(2025, 1, 20),
                    date(2025, 1, 21)]
        points = [
            price_point(sessions[0], 100.0, ticker="SPY"),
            price_point(sessions[1], 101.0, ticker="SPY"),
            price_point(sessions[3], 103.0, ticker="SPY"),
        ]
        self.repository.save_prices(_series("SPY", "US", "USD", points))
        series = benchmark_series(self.spec, self.access, sessions, lag_minutes=LAG)
        self.assertEqual(series.levels[2], series.levels[1])
        self.assertFalse(any("2025-01-20" in item for item in series.anomalies))

    def test_an_open_venue_without_a_price_never_forward_fills(self) -> None:
        self.repository.save_prices(
            _series("SPY", "US", "USD",
                    [price_point(self.days[0], 100.0, ticker="SPY"),
                     price_point(self.days[1], 101.0, ticker="SPY")]))
        series = self.curve()
        self.assertIsNone(series.levels[2])
        self.assertTrue(any("SPY" in item and self.days[2].isoformat() in item
                            for item in series.anomalies))


class BenchmarkFingerprintTests(BenchmarkTestCase):
    """Review P1-7/P1-9: the benchmark's prices *and* FX join the fingerprint."""

    def test_a_revised_benchmark_price_changes_the_digest(self) -> None:
        future = next_session_after(datetime.now(UTC))
        sessions = [*self.days, future]
        self.save_point("SPY", "US", "USD", future, 100.0)
        before = self._fingerprint(sessions)
        self.save_point("SPY", "US", "USD", future, 200.0)
        # The correction is published before the future session's decision
        # instant, so that session sees the new value while the past does not.
        self.assertNotEqual(self._fingerprint(sessions), before)
        self.assertEqual(self._fingerprint(self.days),
                         self._fingerprint(self.days))

    def test_a_revised_benchmark_fx_rate_changes_the_digest(self) -> None:
        future = next_session_after(datetime.now(UTC))
        sessions = [*self.days, future]
        self.spec = make_spec(benchmark="SPX", sessions=6)
        self.save_point("SPX", "US", "GBP", future, 100.0)
        self.save_fx("GBP", {future: 8.0})
        before = self._fingerprint(sessions)
        self.save_fx("GBP", {future: 8.8})
        self.assertNotEqual(self._fingerprint(sessions), before)

    def test_the_benchmark_fx_rates_are_in_the_digest(self) -> None:
        self.spec = make_spec(benchmark="SPX", sessions=6)
        self.save_prices("SPX", "US", "GBP", [100.0] * 6)
        self.save_fx("GBP", {day: 8.0 for day in self.days})
        series = self.curve()
        self.assertEqual(len(series.fx_rows), len(self.days))
        fx_rows = [row for row in series.rows() if row.get("kind") == "benchmark_fx"]
        self.assertEqual(len(fx_rows), len(self.days))
        self.assertTrue(all(row["rate"] == 8.0 for row in fx_rows))
        with_benchmark = self._fingerprint(self.days)
        without = data_fingerprint(self.spec, self.access, sessions=tuple(self.days),
                                   lag_minutes=LAG)
        self.assertNotEqual(with_benchmark, without)

    def test_a_cny_benchmark_adds_no_fx_rows(self) -> None:
        self.spec = make_spec(benchmark="000300", sessions=6)
        self.save_prices("000300", "CN", "CNY", [100.0] * 6)
        series = self.curve()
        self.assertEqual(series.fx_rows, ())
        self.assertFalse(any(row.get("kind") == "benchmark_fx"
                             for row in series.rows()))

    def test_a_shared_currency_keeps_every_consumed_rate(self) -> None:
        self.save_prices("SPY", "US", "USD", [100.0] * 6)
        series = self.curve()
        self.assertEqual(len(series.fx_rows), len(self.days))
        for row in series.fx_rows:
            self.assertEqual(row["currency"], "USD")
            self.assertIsNotNone(row["rate"])
            self.assertNotIn("revision_id", row)

    def test_rows_carry_the_price_actually_used(self) -> None:
        import dataclasses
        points = [
            dataclasses.replace(
                price_point(day, 100.0, ticker="SPY", market="US", currency="USD"),
                adj_close=None)
            for day in self.days
        ]
        self.repository.save_prices(_series("SPY", "US", "USD", points))
        series = self.curve()
        rows = series.rows()
        self.assertEqual(rows[0], {"benchmark": "SPY", "market": "US",
                                   "currency": "USD"})
        prices = [row for row in rows if row.get("kind") == "benchmark_price"]
        self.assertEqual(len(prices), len(self.days))
        for row in prices:
            self.assertEqual(row["price_source"], "close")
            self.assertEqual(row["price"], row["close"])
            self.assertNotIn("revision_id", _fingerprint_row(row))
            self.assertEqual(row["availability_source"], "inferred")

    def test_an_adjusted_close_is_preferred_for_the_level(self) -> None:
        points = [
            price_point(day, 100.0, ticker="SPY", market="US", currency="USD")
            for day in self.days
        ]
        points = [_with_adj_close(point, 95.0) for point in points]
        self.repository.save_prices(_series("SPY", "US", "USD", points))
        series = self.curve()
        prices = [row for row in series.rows() if row.get("kind") == "benchmark_price"]
        self.assertTrue(all(row["price_source"] == "adj_close" for row in prices))
        self.assertTrue(all(row["price"] == 95.0 for row in prices))
        self.assertAlmostEqual(series.levels[-1], self.spec.initial_capital)

    # -- helpers ---------------------------------------------------------
    def save_point(self, ticker: str, market: str, currency: str,
                   day: date, close: float) -> None:
        self.repository.save_prices(_series(
            ticker, market, currency,
            [price_point(day, close, ticker=ticker, market=market,
                         currency=currency)]))

    def _fingerprint(self, sessions) -> str:
        series = self.curve(sessions=sessions)
        return data_fingerprint(self.spec, self.access, sessions=tuple(sessions),
                                lag_minutes=LAG, benchmark=series)

    def _closes(self, as_of: datetime) -> list[float]:
        series = self.access.series_for("SPY", as_of, start=self.days[0],
                                        end=self.days[-1])
        return [float(point.close) for point in series.points]


def _with_adj_close(point, value: float):
    import dataclasses
    return dataclasses.replace(point, adj_close=value)


def _fingerprint_row(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "revision_id"}


def next_session_after(after: datetime) -> date:
    from fa.utils.calendars import calendar_for
    day = after.date() + timedelta(days=1)
    calendar = calendar_for("US")
    while not calendar.is_trading_day(day):
        day = day + timedelta(days=1)
    return day


class BenchmarkCompatibilityTests(BenchmarkTestCase):
    """The wrapper helpers keep the simple call shape used elsewhere."""

    def test_no_benchmark_yields_none_and_empty_lists(self) -> None:
        spec = make_spec(sessions=6)
        self.assertIsNone(benchmark_series(spec, self.access, self.days, lag_minutes=LAG))
        self.assertEqual(benchmark_levels(spec, self.access, self.days), ([], []))
        self.assertEqual(benchmark_rows(spec, self.access, self.days), [])

    def test_levels_and_rows_agree_with_the_series(self) -> None:
        self.save_prices("SPY", "US", "USD", [100.0] * 6)
        series = self.curve()
        levels, anomalies = benchmark_levels(
            self.spec, self.access, self.days, lag_minutes=LAG)
        self.assertEqual(levels, list(series.levels))
        self.assertEqual(anomalies, list(series.anomalies))
        self.assertEqual(benchmark_rows(self.spec, self.access, self.days,
                                        lag_minutes=LAG), series.rows())


class BenchmarkEquityCurveTests(BenchmarkTestCase):
    """``run_backtest`` puts the curve on the equity points."""

    def test_the_equity_curve_carries_the_benchmark(self) -> None:
        sessions = trading_days(20)
        points = [price_point(day, 100.0 + 10.0 * index, ticker="SPY")
                  for index, day in enumerate(sessions)]
        self.repository.save_prices(_series("SPY", "US", "USD", points))
        spec = make_spec(benchmark="SPY", sessions=20)
        run, created = run_backtest(spec, self.repository, now=NOW)
        self.assertTrue(created)
        levels = [point.benchmark_base for point in run.equity_curve]
        self.assertIsNotNone(levels[0])
        self.assertAlmostEqual(levels[0], spec.initial_capital, places=6)
        self.assertTrue(all(level is not None for level in levels))
        self.assertFalse(any(item.startswith("benchmark-") for item in run.anomalies))

    def test_without_a_benchmark_the_curve_stays_empty(self) -> None:
        spec = make_spec(sessions=20)
        run, created = run_backtest(spec, self.repository, now=NOW)
        self.assertTrue(created)
        self.assertTrue(all(point.benchmark_base is None for point in run.equity_curve))
        self.assertFalse(any(item.startswith("benchmark-") for item in run.anomalies))


if __name__ == "__main__":
    unittest.main()
