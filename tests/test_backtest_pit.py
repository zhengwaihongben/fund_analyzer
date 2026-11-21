# -*- coding: utf-8 -*-
"""PIT-01…PIT-07: the Stage C engine may only act on what was public.

The tests use the real repository and the real engine; nothing is mocked.  The
only trick is that publication instants are chosen so a value is *just*
invisible (one microsecond after the decision instant) or *just* visible (exactly
at it), which is the boundary the PIT queries claim to honour.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fa.backtesting.data_access import DataAccess
from fa.backtesting.engine import run_engine
from fa.backtesting.models import (
    CostModel,
    Instrument,
    RebalanceRule,
    StrategySpec,
    Weighting,
)
from fa.data.database.repository import Repository
from fa.data.price_pipeline import infer_availability
from fa.models.market_data import AvailabilitySource, PricePoint, PriceSeries
from tests.fixtures.backtesting import (
    NOW,
    fx_series,
    price_point,
    trading_days,
    us_instrument,
)

UTC = timezone.utc
FREE = CostModel(commission_rate=0.0, min_commission=0.0, slippage_bps=0.0, fx_spread_bps=0.0)
DAYS = trading_days(6)


def _spec(days: list[date], *, capital: float = 100_000.0) -> StrategySpec:
    return StrategySpec(
        spec_id="pit",
        universe=(us_instrument("AAA"),),
        weighting=Weighting.FIXED,
        start=days[0],
        end=days[-1],
        initial_capital=capital,
        rebalance=RebalanceRule(frequency="monthly", anchor=1),
        target_weights={"AAA": 1.0},
        drift_threshold=0.0,
        costs=FREE,
    )


def _series(
    days: list[date],
    closes: dict[date, float],
    *,
    publish: dict[date, datetime] | None = None,
    legacy: bool = False,
) -> PriceSeries:
    points: list[PricePoint] = []
    for day in days:
        if day not in closes:
            continue
        if legacy:
            point = PricePoint(
                price_date=day,
                close=closes[day],
                adj_close=closes[day],
                market_timestamp=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
                available_at=None,
                availability_source=AvailabilitySource.UNKNOWN,
                fetched_at=NOW,
                market="US",
                currency="USD",
                available_price_type="close+adj_close",
                used_price_type="close+adj_close",
                source="legacy",
            )
        else:
            point = price_point(
                day,
                closes[day],
                ticker="AAA",
                available_at=(publish or {}).get(day),
            )
        points.append(point)
    return PriceSeries.from_points(
        "AAA", points, market="US", currency="USD", source="fixture"
    )


class _PitCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "pit.db")
        self.addCleanup(self.repository.close)
        self.data = DataAccess(self.repository)
        self.closes = {day: 100.0 for day in DAYS}
        self.repository.save_fx(fx_series("USD", DAYS, {day: 7.0 for day in DAYS}))

    def _seed(self, **kwargs) -> None:
        self.repository.save_prices(_series(DAYS, self.closes, **kwargs))

    def _decision(self, day: date) -> datetime:
        return infer_availability(day, "US")[0]


class DecisionInstantTests(_PitCase):
    def test_pit_01_price_published_exactly_at_the_decision_instant_is_usable(self):
        self._seed()
        result = run_engine(_spec(DAYS), self.data)
        self.assertTrue(
            not any("stale-valuation" in note and DAYS[1].isoformat() in note
                    for note in result.anomalies),
            result.anomalies,
        )

    def test_pit_02_price_published_one_microsecond_later_is_not_usable(self):
        instant = self._decision(DAYS[1])
        publish = {DAYS[1]: instant + timedelta(microseconds=1)}
        self._seed(publish=publish)
        window = (DAYS[1], DAYS[1])
        self.assertEqual(
            self.repository.prices_as_of("AAA", instant, start=window[0], end=window[1]).points,
            [],
        )
        self.assertEqual(
            len(self.repository.prices_as_of(
                "AAA", instant + timedelta(microseconds=1), start=window[0], end=window[1]).points),
            1,
        )
        result = run_engine(_spec(DAYS), self.data)
        # The only session that could trade is DAYS[1], whose price is not yet
        # public at that session's decision instant: no fill, no stale fill.
        self.assertEqual(result.trades, ())


class ExecutionInstantTests(_PitCase):
    def test_pit_03_execution_uses_the_price_public_at_the_execution_close(self):
        closes = dict(self.closes)
        closes[DAYS[1]] = 200.0
        publish = {DAYS[1]: self._decision(DAYS[1])}
        self.repository.save_prices(_series(DAYS, closes, publish=publish))
        result = run_engine(_spec(DAYS), self.data)
        buy = result.trades[0]
        self.assertEqual(buy.session, DAYS[1])
        self.assertAlmostEqual(buy.price_local, 200.0, delta=1e-9)

    def test_pit_04_execution_price_published_later_is_not_substituted(self):
        closes = dict(self.closes)
        closes[DAYS[1]] = 200.0
        publish = {DAYS[1]: self._decision(DAYS[1]) + timedelta(days=30)}
        self.repository.save_prices(_series(DAYS, closes, publish=publish))
        result = run_engine(_spec(DAYS), self.data)
        # The order cannot be priced, so it is skipped — never filled at 100.
        self.assertEqual(result.trades, ())
        self.assertTrue(any("missing-price" in note for note in result.anomalies))


class UnpublishedIsolationTests(_PitCase):
    """PIT-05: two databases that differ only in an *unpublished* value agree."""

    def _run_with_future_value(self, value: float):
        other = Repository(Path(self._tmp.name) / f"future_{value}.db")
        self.addCleanup(other.close)
        other.save_fx(fx_series("USD", DAYS, {day: 7.0 for day in DAYS}))
        closes = dict(self.closes)
        closes[DAYS[2]] = value
        publish = {DAYS[2]: datetime(2027, 1, 1, tzinfo=UTC)}
        other.save_prices(_series(DAYS, closes, publish=publish))
        return run_engine(_spec(DAYS), DataAccess(other))

    def test_pit_05_an_unpublished_value_cannot_change_any_decision(self):
        first = self._run_with_future_value(300.0)
        second = self._run_with_future_value(999.0)
        self.assertEqual(first.trades, second.trades)
        self.assertEqual(first.equity_curve, second.equity_curve)

    def test_pit_06_a_published_value_does_change_the_run(self):
        self._seed()
        published = Repository(Path(self._tmp.name) / "published.db")
        self.addCleanup(published.close)
        published.save_fx(fx_series("USD", DAYS, {day: 7.0 for day in DAYS}))
        closes = dict(self.closes)
        closes[DAYS[2]] = 300.0
        published.save_prices(_series(DAYS, closes))
        base = run_engine(_spec(DAYS), self.data)
        changed = run_engine(_spec(DAYS), DataAccess(published))
        self.assertNotEqual(base.equity_curve, changed.equity_curve)


class RevisionVisibilityTests(_PitCase):
    def test_pit_07_a_revision_does_not_change_decisions_made_before_it(self):
        self._seed()
        before = run_engine(_spec(DAYS), self.data)
        closes = dict(self.closes)
        closes[DAYS[1]] = 150.0
        closes[DAYS[2]] = 150.0
        self.repository.save_prices(_series(DAYS, closes))  # records revisions
        after = run_engine(_spec(DAYS), self.data)
        self.assertEqual(before.trades, after.trades)
        self.assertEqual(before.equity_curve, after.equity_curve)
        instant = self._decision(DAYS[3])
        old = self.repository.prices_as_of("AAA", instant)
        self.assertEqual(sorted(point.close for point in old.points[:2]), [100.0, 100.0])


class LegacyRowsTests(_PitCase):
    def test_pit_08_legacy_rows_without_availability_are_never_used(self):
        self._seed(legacy=True)
        result = run_engine(_spec(DAYS), self.data)
        self.assertEqual(result.trades, ())
        for point in result.equity_curve:
            self.assertAlmostEqual(point.cash_base, 100_000.0, delta=1e-9)
            self.assertAlmostEqual(point.holdings_base, 0.0, delta=1e-9)
        self.assertEqual(self.repository.prices_as_of("AAA", self._decision(DAYS[-1])).points, [])


if __name__ == "__main__":
    unittest.main()
