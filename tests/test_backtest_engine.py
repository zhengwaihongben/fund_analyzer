# -*- coding: utf-8 -*-
"""ENG-*: the Stage C engine (V2.3.2, spec §5).

The acceptance case (C-G2) is built twice: once with zero costs, where the
expected cash, quantities and equity curve are exact to the cent and compared
with a 1e-9 tolerance, and once with the specification's cost defaults, where
the scaled buy exercises the cash constraint and the accounting identity
``cash + net cash flows == initial capital`` is checked instead of a rounded
ideal (a quantity is persisted at 6 decimals, so comparing below that
quantisation would test float noise, not the engine).
"""
from __future__ import annotations

import ast
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fa.backtesting.data_access import DataAccess
from fa.backtesting.engine import EngineResult, run_engine
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
    START,
    build_store,
    fx_series,
    hk_instrument,
    make_spec,
    price_point,
    price_series,
    raw_price_point,
    trading_days,
    us_instrument,
)

FREE = CostModel(commission_rate=0.0, min_commission=0.0, slippage_bps=0.0, fx_spread_bps=0.0)
SPEC = CostModel(commission_rate=0.0005, min_commission=0.0, slippage_bps=10.0, fx_spread_bps=20.0)


def _hand_store(repository: Repository) -> tuple[date, date, date]:
    """Two USD assets, flat prices, three sessions, FX at 7.0."""
    days = [date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8)]
    a = us_instrument("AAA")
    b = us_instrument("BBB")
    repository.save_prices(PriceSeries.from_points(
        "AAA", [price_point(day, 100.0, ticker="AAA") for day in days],
        market="US", currency="USD", source="fixture"))
    repository.save_prices(PriceSeries.from_points(
        "BBB", [price_point(day, 40.0, ticker="BBB") for day in days],
        market="US", currency="USD", source="fixture"))
    repository.save_fx(fx_series("USD", days, {day: 7.0 for day in days}))
    return days[0], days[1], days[2]


def _hand_spec(days, *, costs: CostModel) -> StrategySpec:
    return StrategySpec(
        spec_id="hand",
        universe=(us_instrument("AAA"), us_instrument("BBB")),
        weighting=Weighting.FIXED,
        start=days[0],
        end=days[2],
        initial_capital=700_000.0,
        rebalance=RebalanceRule(frequency="monthly", anchor=1),
        target_weights={"AAA": 0.6, "BBB": 0.4},
        drift_threshold=0.0,
        costs=costs,
    )


class HandComputedTests(unittest.TestCase):
    """C-G2: two assets, three sessions, hand-computed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "hand.db")
        self.addCleanup(self.repository.close)
        self.days = _hand_store(self.repository)

    def _run(self, costs: CostModel) -> EngineResult:
        return run_engine(_hand_spec(self.days, costs=costs), DataAccess(self.repository))

    def test_zero_cost_hand_case_matches_hand_calculation_exactly(self):
        result = self._run(FREE)
        self.assertEqual(len(result.trades), 2)
        buys = {trade.ticker: trade for trade in result.trades}
        # 700 000 * 0.6 = 420 000 CNY / (100 USD * 7.0) = 600 shares
        self.assertAlmostEqual(buys["AAA"].quantity, 600.0, delta=1e-9)
        # 700 000 * 0.4 = 280 000 CNY / (40 USD * 7.0) = 1 000 shares
        self.assertAlmostEqual(buys["BBB"].quantity, 1_000.0, delta=1e-9)
        for trade in result.trades:
            self.assertEqual(trade.session, self.days[1])
            self.assertAlmostEqual(trade.total_costs_base, 0.0, delta=1e-9)
        self.assertEqual([len(result.equity_curve)], [3])
        first, second, third = result.equity_curve
        self.assertAlmostEqual(first.cash_base, 700_000.0, delta=1e-9)
        self.assertAlmostEqual(first.total_base, 700_000.0, delta=1e-9)
        self.assertAlmostEqual(first.exposure, 0.0, delta=1e-9)
        self.assertAlmostEqual(second.cash_base, 0.0, delta=1e-9)
        self.assertAlmostEqual(second.holdings_base, 700_000.0, delta=1e-9)
        self.assertAlmostEqual(second.total_base, 700_000.0, delta=1e-9)
        self.assertAlmostEqual(second.exposure, 1.0, delta=1e-9)
        self.assertAlmostEqual(third.total_base, 700_000.0, delta=1e-9)
        # No drift, no orders on the third session.
        self.assertEqual(len(result.trades), 2)

    def test_cost_bearing_hand_case_keeps_the_accounting_identity(self):
        result = self._run(SPEC)
        self.assertEqual(len(result.trades), 2)
        aaa = next(t for t in result.trades if t.ticker == "AAA")
        # 420 000 gross: commission 210.00, slippage 420.00, FX spread 840.00
        self.assertAlmostEqual(aaa.gross_base, 420_000.0, delta=1e-9)
        self.assertAlmostEqual(aaa.commission_base, 210.0, delta=1e-9)
        self.assertAlmostEqual(aaa.slippage_base, 420.0, delta=1e-9)
        self.assertAlmostEqual(aaa.fx_spread_base, 840.0, delta=1e-9)
        self.assertAlmostEqual(aaa.net_base, 421_470.0, delta=1e-9)
        second = result.equity_curve[1]
        # The second buy is capped by cash, so it spends the remaining 278 530.
        self.assertLessEqual(second.cash_base, 0.01)
        self.assertGreaterEqual(second.cash_base, 0.0)
        total_costs = sum(trade.total_costs_base for trade in result.trades)
        self.assertAlmostEqual(
            result.equity_curve[1].total_base + total_costs, 700_000.0, delta=0.01)
        self.assertAlmostEqual(
            result.equity_curve[1].cash_base
            + sum(trade.net_base for trade in result.trades),
            700_000.0, delta=0.01)


class GridAndValuationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "grid.db")
        self.addCleanup(self.repository.close)
        self.store = build_store(self.repository, days=30)
        self.data = DataAccess(self.repository)

    def test_eng_01_session_grid_covers_the_requested_window(self):
        spec = make_spec(sessions=20)
        result = run_engine(spec, self.data)
        sessions = [point.session for point in result.equity_curve]
        self.assertEqual(sessions, trading_days(20))
        self.assertEqual(len(result.equity_curve), 20)

    def test_eng_18_equity_curve_is_cash_plus_holdings(self):
        result = run_engine(make_spec(sessions=30), self.data)
        for point in result.equity_curve:
            self.assertAlmostEqual(
                point.total_base, point.cash_base + point.holdings_base, delta=1e-6)

    def test_eng_19_drawdown_is_measured_against_the_running_peak(self):
        result = run_engine(make_spec(sessions=30), self.data)
        peak = 0.0
        for point in result.equity_curve:
            peak = max(peak, point.total_base)
            expected = point.total_base / peak - 1.0
            self.assertAlmostEqual(point.drawdown, expected, delta=1e-12)
            self.assertLessEqual(point.drawdown, 0.0)

    def test_no_trades_before_the_second_session(self):
        result = run_engine(make_spec(sessions=30), self.data)
        self.assertTrue(result.trades)
        first = result.equity_curve[1].session
        self.assertTrue(all(trade.session >= first for trade in result.trades))


class WeightingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "weights.db")
        self.addCleanup(self.repository.close)
        self.store = build_store(self.repository, days=60)
        self.data = DataAccess(self.repository)

    def test_eng_02_equal_weight_allocates_half_to_each(self):
        result = run_engine(make_spec(sessions=40), self.data)
        execution = result.equity_curve[1].session
        buys = [t for t in result.trades if t.side == "buy" and t.session == execution]
        self.assertEqual(len(buys), 2)
        us = next(t for t in buys if t.ticker == "LLY")
        hk = next(t for t in buys if t.ticker == "0700")
        # Equal weights: the second buy is slightly smaller because the first
        # order already paid its costs, so "equal" is symmetric to within them.
        self.assertLess(abs(us.gross_base - hk.gross_base) / us.gross_base, 0.02)
        self.assertAlmostEqual(
            result.equity_curve[1].holdings_base
            / (us.gross_base + hk.gross_base), 1.0, delta=1e-3)

    def test_eng_03_fixed_weight_respects_the_targets(self):
        spec = make_spec(sessions=40, weighting=Weighting.FIXED,
                         target_weights={"LLY": 0.75, "0700": 0.25})
        result = run_engine(spec, self.data)
        buys = {t.ticker: t for t in result.trades if t.side == "buy"}
        self.assertAlmostEqual(
            buys["LLY"].gross_base / buys["0700"].gross_base, 3.0, delta=0.05)

    def test_eng_04_momentum_uses_the_positive_momentum_normalisation(self):
        days = trading_days(60)
        spec = make_spec(sessions=60, weighting=Weighting.MOMENTUM, momentum_window=20,
                         start=days[25], end=days[-1])
        result = run_engine(spec, self.data)
        buys = {t.ticker: t for t in result.trades if t.side == "buy"}
        self.assertIn("LLY", buys)
        self.assertIn("0700", buys)
        self.assertGreater(buys["LLY"].gross_base, buys["0700"].gross_base)

    def test_momentum_without_enough_history_holds_cash(self):
        spec = make_spec(sessions=60, weighting=Weighting.MOMENTUM, momentum_window=20,
                         start=START, end=START + timedelta(days=10))
        result = run_engine(spec, self.data)
        self.assertEqual(result.trades, ())
        self.assertTrue(any("momentum-excluded" in note for note in result.anomalies))
        self.assertAlmostEqual(result.equity_curve[-1].exposure, 0.0, delta=1e-12)


class RebalanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "rebalance.db")
        self.addCleanup(self.repository.close)
        self.store = build_store(self.repository, days=60)
        self.data = DataAccess(self.repository)

    def test_eng_05_daily_rebalance_trades_on_each_session(self):
        spec = make_spec(sessions=30, rebalance=RebalanceRule(frequency="daily"),
                         drift_threshold=0.0)
        result = run_engine(spec, self.data)
        sessions = {trade.session for trade in result.trades}
        self.assertGreaterEqual(len(sessions), 5)

    def test_eng_06_weekly_rebalance_only_trades_after_the_anchor(self):
        spec = make_spec(sessions=45, rebalance=RebalanceRule(frequency="weekly", anchor=0))
        result = run_engine(spec, self.data)
        weeks = {trade.session.isocalendar()[:2] for trade in result.trades}
        self.assertGreaterEqual(len(weeks), 2)
        for trade in result.trades:
            self.assertLessEqual(trade.session.weekday(), 1)

    def test_eng_07_monthly_rebalance_only_trades_in_a_new_month(self):
        spec = make_spec(sessions=60, rebalance=RebalanceRule(frequency="monthly", anchor=1))
        result = run_engine(spec, self.data)
        months = {(trade.session.year, trade.session.month) for trade in result.trades}
        self.assertGreaterEqual(len(months), 2)
        self.assertLess(len(months), len({(t.session.year, t.session.month)
                                         for t in result.equity_curve}))

    def test_eng_08_drift_threshold_can_skip_a_rebalance(self):
        loose = make_spec(sessions=45, rebalance=RebalanceRule(frequency="weekly", anchor=0),
                          drift_threshold=1.0)
        tight = make_spec(sessions=45, rebalance=RebalanceRule(frequency="weekly", anchor=0),
                          drift_threshold=0.0)
        loose_result = run_engine(loose, self.data)
        tight_result = run_engine(tight, self.data)
        self.assertLess(len(loose_result.trades), len(tight_result.trades))

    def test_eng_20_same_inputs_produce_the_same_run(self):
        spec = make_spec(sessions=45, rebalance=RebalanceRule(frequency="weekly", anchor=0))
        first = run_engine(spec, self.data)
        second = run_engine(spec, self.data)
        self.assertEqual(first.trades, second.trades)
        self.assertEqual(first.equity_curve, second.equity_curve)
        self.assertEqual(first.anomalies, second.anomalies)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "execution.db")
        self.addCleanup(self.repository.close)
        self.store = build_store(self.repository, days=30)
        self.data = DataAccess(self.repository)

    def test_eng_12_cash_never_goes_negative_and_buys_scale_down(self):
        spec = make_spec(sessions=30, weighting=Weighting.FIXED,
                         target_weights={"LLY": 1.0, "0700": 0.0},
                         rebalance=RebalanceRule(frequency="daily"))
        result = run_engine(spec, self.data)
        for point in result.equity_curve:
            self.assertGreaterEqual(point.cash_base, 0.0)
        self.assertTrue(any("cash-limited" in note for note in result.anomalies))

    def test_eng_13_whole_shares_when_fractional_is_disabled(self):
        spec = make_spec(sessions=30)
        result = run_engine(spec, self.data, fractional_shares=False)
        for trade in result.trades:
            self.assertEqual(trade.quantity, float(int(trade.quantity)))

    def test_eng_15_missing_fx_skips_the_orders(self):
        repository = Repository(Path(self._tmp.name) / "no_fx.db")
        self.addCleanup(repository.close)
        days = trading_days(10)
        repository.save_prices(price_series(us_instrument(), days))
        spec = make_spec(sessions=10)
        result = run_engine(spec, DataAccess(repository))
        self.assertEqual(result.trades, ())
        self.assertTrue(any("missing-fx" in note for note in result.anomalies))

    def test_eng_14_missing_price_skips_that_order_only(self):
        repository = Repository(Path(self._tmp.name) / "gap.db")
        self.addCleanup(repository.close)
        days = trading_days(6)
        repository.save_prices(price_series(us_instrument(), days))
        hk_days = [day for day in days if day != days[1]]
        repository.save_prices(price_series(hk_instrument(), hk_days, base=400.0, step=2.0))
        repository.save_fx(fx_series("USD", days, {day: 7.10 for day in days}))
        repository.save_fx(fx_series("HKD", days, {day: 0.91 for day in days}))
        spec = make_spec(sessions=6, rebalance=RebalanceRule(frequency="daily"),
                         drift_threshold=0.0)
        result = run_engine(spec, DataAccess(repository))
        self.assertTrue(result.trades)
        self.assertTrue(any("missing-price" in note for note in result.anomalies))


class CorporateActionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "actions.db")
        self.addCleanup(self.repository.close)

    def _raw_store(self):
        days = trading_days(6)
        a = us_instrument("AAA")
        points = [
            raw_price_point(day, 100.0, ticker="AAA",
                            dividend=(1.0 if day == days[2] else None),
                            split=(2.0 if day == days[3] else None))
            for day in days
        ]
        self.repository.save_prices(PriceSeries.from_points(
            "AAA", points, market="US", currency="USD", source="fixture"))
        self.repository.save_fx(fx_series("USD", days, {day: 7.0 for day in days}))
        return days

    def _spec(self, days):
        return StrategySpec(
            spec_id="raw",
            universe=(us_instrument("AAA"),),
            weighting=Weighting.FIXED,
            start=days[0],
            end=days[-1],
            initial_capital=100_000.0,
            rebalance=RebalanceRule(frequency="monthly", anchor=1),
            target_weights={"AAA": 1.0},
            drift_threshold=0.0,
            costs=FREE,
        )

    def test_eng_16_dividend_is_credited_on_a_raw_close_series(self):
        days = self._raw_store()
        result = run_engine(self._spec(days), DataAccess(self.repository))
        self.assertTrue(any("raw-price-fallback" in note for note in result.anomalies))
        buy = result.trades[0]
        # 100 000 CNY / (100 * 7) = 142.857143 shares
        self.assertAlmostEqual(buy.quantity, 142.857143, delta=1e-6)
        curve = {point.session: point for point in result.equity_curve}
        before = curve[days[1]]
        after = curve[days[2]]
        self.assertAlmostEqual(after.cash_base - before.cash_base, 142.857143 * 1.0 * 7.0,
                               delta=1e-4)

    def test_eng_17_split_scales_the_held_quantity(self):
        days = self._raw_store()
        result = run_engine(self._spec(days), DataAccess(self.repository))
        final = result.final_positions[0]
        self.assertAlmostEqual(final.quantity, 285.714286, delta=1e-6)


class ExecutionPriceTests(unittest.TestCase):
    """P1-1: a fill uses the official close, valuation uses the adjusted close."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "price.db")
        self.addCleanup(self.repository.close)
        days = trading_days(4)
        self.days = days
        # close 100, adjusted close 95: a trade worth 100 per share, a position
        # worth 95 per share once dividends are folded in.
        self.repository.save_prices(PriceSeries.from_points(
            "AAA", [self._point(day, 100.0, 95.0) for day in days],
            market="US", currency="USD", source="fixture"))
        self.repository.save_fx(fx_series("USD", days, {day: 7.0 for day in days}))

    @staticmethod
    def _point(day: date, close: float, adj_close: float) -> PricePoint:
        return PricePoint(
            price_date=day,
            close=close,
            adj_close=adj_close,
            market_timestamp=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
            available_at=infer_availability(day, "US")[0],
            availability_source=AvailabilitySource.INFERRED,
            fetched_at=NOW,
            market="US",
            currency="USD",
            available_price_type="close+adj_close",
            used_price_type="close+adj_close",
            source="fixture",
        )

    def _run(self, repository=None):
        spec = StrategySpec(
            spec_id="price",
            universe=(us_instrument("AAA"),),
            weighting=Weighting.FIXED,
            start=self.days[0],
            end=self.days[-1],
            initial_capital=100_000.0,
            rebalance=RebalanceRule(frequency="monthly", anchor=1),
            target_weights={"AAA": 1.0},
            drift_threshold=0.0,
            costs=FREE,
        )
        return run_engine(spec, DataAccess(repository or self.repository))

    def test_fill_price_is_the_official_close_not_the_adjusted_close(self):
        result = self._run()
        buy = result.trades[0]
        self.assertAlmostEqual(buy.price_local, 100.0, delta=1e-9)
        self.assertEqual(buy.price_source, "close")
        # 100 000 CNY / (100 * 7.0) = 142.857143 shares, not 100 000 / (95 * 7).
        self.assertAlmostEqual(buy.quantity, 100_000.0 / 700.0, delta=1e-6)
        self.assertAlmostEqual(buy.gross_base, 100_000.0, delta=1e-2)

    def test_valuation_still_marks_the_position_at_the_adjusted_close(self):
        result = self._run()
        buy = result.trades[0]
        after = result.equity_curve[1]
        self.assertAlmostEqual(
            after.holdings_base, buy.quantity * 95.0 * 7.0, delta=1e-2)
        self.assertAlmostEqual(after.holdings_base, 95_000.0, delta=1e-2)

    def test_valuation_falls_back_to_the_close_without_an_adjusted_close(self):
        repository = Repository(Path(self._tmp.name) / "raw.db")
        self.addCleanup(repository.close)
        days = trading_days(4)
        repository.save_prices(PriceSeries.from_points(
            "AAA", [raw_price_point(day, 100.0, ticker="AAA") for day in days],
            market="US", currency="USD", source="fixture"))
        repository.save_fx(fx_series("USD", days, {day: 7.0 for day in days}))
        result = self._run(repository)
        buy = result.trades[0]
        self.assertAlmostEqual(buy.price_local, 100.0, delta=1e-9)
        self.assertEqual(buy.price_source, "close")
        self.assertAlmostEqual(result.equity_curve[1].holdings_base, 100_000.0, delta=1e-2)


class _FxDropout(DataAccess):
    """A data access whose FX lookup stops answering from ``drop_from`` on."""

    def __init__(self, repository: Repository, *, drop_from: date):
        super().__init__(repository)
        self.drop_from = drop_from

    def fx_rate(self, currency, as_of, session):
        if session >= self.drop_from:
            return None
        return super().fx_rate(currency, as_of, session)


class DividendFxTests(unittest.TestCase):
    """P1-2: a foreign dividend is only credited when its rate is published."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "dividend.db")
        self.addCleanup(self.repository.close)
        self.days = trading_days(5)
        self.repository.save_prices(PriceSeries.from_points(
            "AAA",
            [
                raw_price_point(
                    day, 100.0, ticker="AAA",
                    dividend=(1.0 if day == self.days[2] else None))
                for day in self.days
            ],
            market="US", currency="USD", source="fixture"))

    def _spec(self) -> StrategySpec:
        return StrategySpec(
            spec_id="dividend",
            universe=(us_instrument("AAA"),),
            weighting=Weighting.FIXED,
            start=self.days[0],
            end=self.days[-1],
            initial_capital=100_000.0,
            rebalance=RebalanceRule(frequency="monthly", anchor=1),
            target_weights={"AAA": 1.0},
            drift_threshold=0.0,
            costs=FREE,
        )

    def test_foreign_dividend_is_converted_when_the_rate_exists(self):
        self.repository.save_fx(
            fx_series("USD", self.days, {day: 7.0 for day in self.days}))
        result = run_engine(self._spec(), DataAccess(self.repository))
        curve = {point.session: point for point in result.equity_curve}
        quantity = result.trades[0].quantity
        self.assertAlmostEqual(
            curve[self.days[2]].cash_base - curve[self.days[1]].cash_base,
            quantity * 1.0 * 7.0, delta=1e-4)
        self.assertFalse(any("missing-fx-dividend" in note for note in result.anomalies))

    def test_foreign_dividend_without_a_rate_is_not_credited(self):
        # The buy on day 1 sees a rate; from the ex-date on there is none.
        self.repository.save_fx(
            fx_series("USD", self.days[:3], {day: 7.0 for day in self.days[:3]}))
        data = _FxDropout(self.repository, drop_from=self.days[2])
        result = run_engine(self._spec(), data)
        curve = {point.session: point for point in result.equity_curve}
        # No 1:1 fallback: the dividend must not arrive as 100 CNY.
        self.assertAlmostEqual(
            curve[self.days[2]].cash_base - curve[self.days[1]].cash_base, 0.0, delta=1e-9)
        self.assertTrue(any("missing-fx-dividend" in note for note in result.anomalies))


class ClosedMarketExecutionTests(unittest.TestCase):
    """P1-3: a closed venue is not a missing price."""

    HOLIDAY = date(2025, 1, 20)  # US holiday, Hong Kong trading day

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "holiday.db")
        self.addCleanup(self.repository.close)
        self.days = trading_days(14)
        self.assertIn(self.HOLIDAY, self.days)
        us_days = [day for day in self.days if day != self.HOLIDAY]
        self.repository.save_prices(price_series(us_instrument(), us_days))
        self.repository.save_prices(
            price_series(hk_instrument(), self.days, base=400.0, step=2.0))
        self.repository.save_fx(
            fx_series("USD", self.days, {day: 7.10 for day in self.days}))
        self.repository.save_fx(
            fx_series("HKD", self.days, {day: 0.91 for day in self.days}))

    def _run(self):
        spec = make_spec(
            sessions=14,
            weighting=Weighting.FIXED,
            target_weights={"LLY": 0.5, "0700": 0.5},
            rebalance=RebalanceRule(frequency="daily"),
            drift_threshold=0.0,
        )
        return run_engine(spec, DataAccess(self.repository))

    def test_a_closed_venue_skips_its_orders_without_a_missing_price_anomaly(self):
        result = self._run()
        with self.subTest(check="no missing price"):
            self.assertFalse(
                any("missing-price" in note for note in result.anomalies),
                result.anomalies)
        with self.subTest(check="the open venue keeps trading"):
            self.assertTrue(any(trade.ticker == "0700" for trade in result.trades))
        with self.subTest(check="the closed venue never trades that session"):
            self.assertFalse(
                any(trade.ticker == "LLY" and trade.session == self.HOLIDAY
                    for trade in result.trades))

    def test_the_closed_position_is_still_valued_while_the_market_is_shut(self):
        result = self._run()
        curve = {point.session: point for point in result.equity_curve}
        self.assertIn(self.HOLIDAY, curve)
        self.assertGreater(curve[self.HOLIDAY].holdings_base, 0.0)
        self.assertFalse(any("stale-valuation" in note for note in result.anomalies))

    def test_a_missing_price_on_a_trading_day_is_still_an_anomaly(self):
        repository = Repository(Path(self._tmp.name) / "gap_us.db")
        self.addCleanup(repository.close)
        days = trading_days(6)
        gap = days[2]  # a US trading day
        repository.save_prices(price_series(
            us_instrument(), [day for day in days if day != gap]))
        repository.save_prices(
            price_series(hk_instrument(), days, base=400.0, step=2.0))
        repository.save_fx(fx_series("USD", days, {day: 7.10 for day in days}))
        repository.save_fx(fx_series("HKD", days, {day: 0.91 for day in days}))
        spec = make_spec(
            sessions=6,
            weighting=Weighting.FIXED,
            target_weights={"LLY": 0.5, "0700": 0.5},
            rebalance=RebalanceRule(frequency="daily"),
            drift_threshold=0.0,
        )
        result = run_engine(spec, DataAccess(repository))
        self.assertTrue(any("missing-price: LLY" in note for note in result.anomalies))


class IsolationTests(unittest.TestCase):
    def test_engine_source_never_reads_prices_or_sql(self):
        # AST rather than text: the module docstring is allowed to *describe*
        # ``load_prices()`` and SQL, it just must not call them.
        path = Path(__file__).resolve().parents[1] / "fa" / "backtesting" / "engine.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        forbidden_modules = {"sqlite3", "requests", "yfinance", "akshare", "random", "os"}
        forbidden_calls = {
            "load_prices",
            "execute",
            "executemany",
            "executescript",
            "now",
            "today",
            "time",
        }
        imported: set[str] = set()
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    calls.add(func.id)
                elif isinstance(func, ast.Attribute):
                    calls.add(func.attr)
        with self.subTest(check="imports"):
            self.assertEqual(imported & forbidden_modules, set())
        with self.subTest(check="calls"):
            self.assertEqual(calls & forbidden_calls, set())


if __name__ == "__main__":
    unittest.main()
