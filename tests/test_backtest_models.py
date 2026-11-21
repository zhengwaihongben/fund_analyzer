# -*- coding: utf-8 -*-
"""Stage B: ``StrategySpec`` validation and the simulation dataclasses."""
from __future__ import annotations

import unittest
from datetime import date, timedelta
from types import MappingProxyType

from fa.backtesting.models import (
    BacktestMetrics,
    BacktestSpecError,
    CostModel,
    EquityPoint,
    Instrument,
    MIN_MOMENTUM_WINDOW,
    Position,
    RebalanceFrequency,
    RebalanceRule,
    StrategySpec,
    Trade,
    Weighting,
    with_initial_capital,
)

US = Instrument(ticker="LLY", market="US", currency="USD")
HK = Instrument(ticker="0700", market="HK", currency="HKD")
START = date(2025, 1, 6)
END = date(2025, 6, 30)


def _spec(**kwargs) -> StrategySpec:
    values = dict(
        spec_id="unit",
        universe=(US, HK),
        weighting=Weighting.EQUAL,
        start=START,
        end=END,
        initial_capital=1_000_000.0,
    )
    values.update(kwargs)
    return StrategySpec(**values)


class InstrumentTests(unittest.TestCase):
    def test_ticker_and_currency_are_normalised(self) -> None:
        item = Instrument(ticker=" lly ", market="us", currency=" usd ")
        self.assertEqual((item.ticker, item.market, item.currency), ("LLY", "US", "USD"))
        self.assertTrue(item.needs_fx)

    def test_cny_is_not_an_instrument(self) -> None:
        with self.assertRaises(BacktestSpecError):
            Instrument(ticker="600519", market="CN", currency="CNY")

    def test_currency_is_required(self) -> None:
        with self.assertRaises(BacktestSpecError):
            Instrument(ticker="LLY", market="US", currency="")
        with self.assertRaises(BacktestSpecError):
            Instrument(ticker="", market="US", currency="USD")


class RebalanceRuleTests(unittest.TestCase):
    def test_weekly_anchor_must_be_a_weekday(self) -> None:
        self.assertEqual(RebalanceRule(frequency="weekly", anchor=4).anchor, 4)
        with self.assertRaises(BacktestSpecError):
            RebalanceRule(frequency="weekly", anchor=5)
        with self.assertRaises(BacktestSpecError):
            RebalanceRule(frequency="weekly", anchor=-1)

    def test_monthly_anchor_range(self) -> None:
        self.assertEqual(RebalanceRule(frequency="monthly", anchor=31).anchor, 31)
        with self.assertRaises(BacktestSpecError):
            RebalanceRule(frequency="monthly", anchor=0)
        with self.assertRaises(BacktestSpecError):
            RebalanceRule(frequency="monthly", anchor=32)

    def test_daily_has_no_anchor(self) -> None:
        self.assertIsNone(RebalanceRule(frequency="daily", anchor=3).anchor)

    def test_missing_anchor_is_rejected(self) -> None:
        with self.assertRaises(BacktestSpecError):
            RebalanceRule(frequency="quarterly", anchor=None)


class CostModelTests(unittest.TestCase):
    def test_negative_cost_is_rejected(self) -> None:
        with self.assertRaises(BacktestSpecError):
            CostModel(commission_rate=-0.001)
        with self.assertRaises(BacktestSpecError):
            CostModel(slippage_bps=-1.0)

    def test_absurd_cost_is_rejected(self) -> None:
        with self.assertRaises(BacktestSpecError):
            CostModel(commission_rate=0.5)
        with self.assertRaises(BacktestSpecError):
            CostModel(fx_spread_bps=2_000.0)


class StrategySpecTests(unittest.TestCase):
    def test_minimal_valid_spec(self) -> None:
        spec = _spec()
        self.assertEqual(spec.weighting, Weighting.EQUAL)
        self.assertEqual(spec.base_currency, "CNY")
        self.assertEqual(dict(spec.target_weights), {})

    def test_universe_must_not_be_empty(self) -> None:
        with self.assertRaises(BacktestSpecError):
            _spec(universe=())

    def test_duplicate_ticker_is_rejected(self) -> None:
        with self.assertRaises(BacktestSpecError) as context:
            _spec(universe=(US, US))
        self.assertIn("重複", str(context.exception))

    def test_initial_capital_is_required_and_positive(self) -> None:
        with self.assertRaises(TypeError):
            StrategySpec(  # type: ignore[call-arg]
                spec_id="unit", universe=(US,), weighting=Weighting.EQUAL,
                start=START, end=END,
            )
        with self.assertRaises(BacktestSpecError):
            _spec(initial_capital=0.0)
        with self.assertRaises(BacktestSpecError):
            _spec(initial_capital=-1.0)

    def test_base_currency_is_cny_only(self) -> None:
        with self.assertRaises(BacktestSpecError):
            _spec(base_currency="USD")

    def test_start_after_end_is_rejected(self) -> None:
        with self.assertRaises(BacktestSpecError):
            _spec(start=END, end=START)

    def test_drift_threshold_range(self) -> None:
        self.assertEqual(_spec(drift_threshold=0.0).drift_threshold, 0.0)
        with self.assertRaises(BacktestSpecError):
            _spec(drift_threshold=1.5)

    def test_fixed_weights_must_sum_to_one(self) -> None:
        spec = _spec(
            weighting=Weighting.FIXED,
            target_weights={"LLY": 0.6, "0700": 0.4},
        )
        self.assertEqual(dict(spec.target_weights), {"LLY": 0.6, "0700": 0.4})
        self.assertIsInstance(spec.target_weights, MappingProxyType)
        for weights in ({"LLY": 0.6, "0700": 0.3}, {"LLY": 0.5, "0700": 0.5, "X": 0.0}):
            with self.assertRaises(BacktestSpecError):
                _spec(weighting=Weighting.FIXED, target_weights=weights)

    def test_fixed_weights_need_every_ticker(self) -> None:
        with self.assertRaises(BacktestSpecError) as context:
            _spec(weighting=Weighting.FIXED, target_weights={"LLY": 1.0})
        self.assertIn("缺少", str(context.exception))

    def test_rule_weights_reject_target_weights(self) -> None:
        with self.assertRaises(BacktestSpecError):
            _spec(target_weights={"LLY": 1.0})

    def test_momentum_needs_a_long_enough_window(self) -> None:
        with self.assertRaises(BacktestSpecError):
            _spec(weighting=Weighting.MOMENTUM)
        with self.assertRaises(BacktestSpecError):
            _spec(weighting=Weighting.MOMENTUM, momentum_window=MIN_MOMENTUM_WINDOW - 1)
        spec = _spec(weighting=Weighting.MOMENTUM, momentum_window=60)
        self.assertEqual(spec.momentum_window, 60)

    def test_weights_are_immutable(self) -> None:
        spec = _spec(weighting=Weighting.FIXED, target_weights={"LLY": 0.5, "0700": 0.5})
        with self.assertRaises(TypeError):
            spec.target_weights["LLY"] = 0.9  # type: ignore[index]

    def test_short_window_is_a_warning_not_an_error(self) -> None:
        spec = _spec(start=date(2025, 1, 6), end=date(2025, 6, 30))
        self.assertTrue(any("不足兩年" in warning for warning in spec.warnings()))
        long_spec = _spec(start=date(2023, 1, 2), end=date(2025, 6, 30))
        self.assertEqual(long_spec.warnings(), [])

    def test_instruments_by_ticker(self) -> None:
        mapping = _spec().instruments_by_ticker()
        self.assertEqual(set(mapping), {"LLY", "0700"})
        self.assertIs(mapping["LLY"], US)

    def test_with_initial_capital_revalidates(self) -> None:
        spec = _spec()
        changed = with_initial_capital(spec, 500_000.0)
        self.assertEqual(changed.initial_capital, 500_000.0)
        self.assertIsNot(changed, spec)
        with self.assertRaises(BacktestSpecError):
            with_initial_capital(spec, 0.0)

    def test_cost_mapping_is_converted(self) -> None:
        spec = _spec(costs={"commission_rate": 0.001})
        self.assertIsInstance(spec.costs, CostModel)
        self.assertEqual(spec.costs.commission_rate, 0.001)


class SimulationObjectTests(unittest.TestCase):
    def test_trade_side_and_quantity(self) -> None:
        trade = Trade(
            session=START, ticker="LLY", side="BUY", quantity=10.0,
            price_local=100.0, price_source="pit", fx_rate=7.1, fx_source="pit",
            gross_base=7100.0, commission_base=5.0, slippage_base=3.55,
            fx_spread_base=7.1, net_base=7115.65,
        )
        self.assertEqual(trade.side, "buy")
        self.assertAlmostEqual(trade.total_costs_base, 15.65)
        with self.assertRaises(BacktestSpecError):
            Trade(session=START, ticker="LLY", side="hold", quantity=1.0,
                  price_local=1.0, price_source="", fx_rate=None, fx_source=None,
                  gross_base=1.0, commission_base=0.0, slippage_base=0.0,
                  fx_spread_base=0.0, net_base=1.0)
        with self.assertRaises(BacktestSpecError):
            Trade(session=START, ticker="LLY", side="buy", quantity=0.0,
                  price_local=1.0, price_source="", fx_rate=None, fx_source=None,
                  gross_base=1.0, commission_base=0.0, slippage_base=0.0,
                  fx_spread_base=0.0, net_base=1.0)

    def test_position_rejects_negative_quantity(self) -> None:
        with self.assertRaises(BacktestSpecError):
            Position(ticker="LLY", quantity=-1.0, price=100.0, market_value_base=0.0)

    def test_equity_point_rejects_negative_cash(self) -> None:
        with self.assertRaises(BacktestSpecError):
            EquityPoint(session=START, cash_base=-1.0, holdings_base=10.0,
                        total_base=9.0, exposure=1.1)

    def test_metrics_report_none_instead_of_zero(self) -> None:
        metrics = BacktestMetrics(sessions=10, trades=0)
        payload = metrics.as_dict()
        self.assertIsNone(payload["sortino"])
        self.assertEqual(payload["sessions"], 10)
        self.assertIsNone(payload["cagr"])


class FiniteNumberTests(unittest.TestCase):
    """NaN 與 ±Infinity 都會穿過「大於零」檢查，必須另外擋掉。"""

    BAD = (float("nan"), float("inf"), float("-inf"))

    def test_spec_numbers_must_be_finite(self) -> None:
        for value in self.BAD:
            with self.subTest(field="initial_capital", value=value):
                with self.assertRaises(BacktestSpecError):
                    _spec(initial_capital=value)
            with self.subTest(field="drift_threshold", value=value):
                with self.assertRaises(BacktestSpecError):
                    _spec(drift_threshold=value)

    def test_cost_model_numbers_must_be_finite(self) -> None:
        for name in ("commission_rate", "min_commission", "slippage_bps", "fx_spread_bps"):
            for value in self.BAD:
                with self.subTest(field=name, value=value):
                    with self.assertRaises(BacktestSpecError):
                        CostModel(**{name: value})

    def test_position_numbers_must_be_finite(self) -> None:
        for name in ("quantity", "price", "market_value_base"):
            for value in self.BAD:
                payload = dict(ticker="LLY", quantity=10.0, price=100.0,
                               market_value_base=1000.0)
                payload[name] = value
                with self.subTest(field=name, value=value):
                    with self.assertRaises(BacktestSpecError):
                        Position(**payload)

    def test_trade_numbers_must_be_finite(self) -> None:
        base = dict(
            session=START, ticker="LLY", side="buy", quantity=10.0,
            price_local=100.0, price_source="pit", fx_rate=7.1, fx_source="pit",
            gross_base=7100.0, commission_base=5.0, slippage_base=3.55,
            fx_spread_base=7.1, net_base=7115.65,
        )
        for name in ("quantity", "price_local", "fx_rate", "gross_base",
                     "commission_base", "slippage_base", "fx_spread_base", "net_base"):
            for value in self.BAD:
                payload = dict(base)
                payload[name] = value
                with self.subTest(field=name, value=value):
                    with self.assertRaises(BacktestSpecError):
                        Trade(**payload)

    def test_equity_point_numbers_must_be_finite(self) -> None:
        base = dict(session=START, cash_base=1000.0, holdings_base=5000.0,
                    total_base=6000.0, exposure=0.83)
        for name in ("cash_base", "holdings_base", "total_base", "exposure",
                     "benchmark_base", "drawdown"):
            for value in self.BAD:
                payload = dict(base)
                payload[name] = value
                with self.subTest(field=name, value=value):
                    with self.assertRaises(BacktestSpecError):
                        EquityPoint(**payload)

    def test_zero_is_still_allowed_where_it_was_allowed(self) -> None:
        self.assertEqual(CostModel(commission_rate=0.0).commission_rate, 0.0)
        self.assertEqual(Position(ticker="LLY", quantity=0.0, price=0.0,
                                  market_value_base=0.0).quantity, 0.0)
        self.assertEqual(EquityPoint(session=START, cash_base=0.0, holdings_base=0.0,
                                     total_base=0.0, exposure=0.0).total_base, 0.0)
        self.assertEqual(_spec(drift_threshold=0.0).drift_threshold, 0.0)


if __name__ == "__main__":
    unittest.main()
