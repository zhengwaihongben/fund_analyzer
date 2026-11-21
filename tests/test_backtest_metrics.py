# -*- coding: utf-8 -*-
"""Stage D: metrics against hand-computed numbers (spec §7)."""
from __future__ import annotations

import math
import unittest
from datetime import date, timedelta

from fa.backtesting import EquityPoint, Trade
from fa.backtesting.metrics import TRADING_DAYS, YEAR_DAYS, compute_metrics

DAY0 = date(2025, 1, 6)


def _equity(totals: list[float], *, holdings: list[float] | None = None) -> list[EquityPoint]:
    points: list[EquityPoint] = []
    for index, total in enumerate(totals):
        held = holdings[index] if holdings is not None else total
        points.append(EquityPoint(
            session=DAY0 + timedelta(days=index),
            cash_base=max(total - held, 0.0),
            holdings_base=held,
            total_base=total,
            exposure=held / total if total else 0.0,
        ))
    return points


def _trade(side: str, quantity: float, price: float, *, net: float | None = None,
           costs: float = 0.0) -> Trade:
    gross = quantity * price
    return Trade(
        session=DAY0,
        ticker="AAA",
        side=side,
        quantity=quantity,
        price_local=price,
        price_source="close",
        fx_rate=None,
        fx_source=None,
        gross_base=gross,
        commission_base=costs,
        slippage_base=0.0,
        fx_spread_base=0.0,
        net_base=gross + costs if net is None and side == "buy" else (
            gross - costs if net is None else net),
    )


class ReturnMetricTests(unittest.TestCase):
    def test_cagr_uses_calendar_days(self) -> None:
        totals = [1_000_000.0, 1_010_000.0, 1_020_000.0]
        metrics = compute_metrics(_equity(totals), [])
        years = 2 / YEAR_DAYS
        expected = (1.02) ** (1 / years) - 1
        self.assertAlmostEqual(metrics.cagr, expected, places=12)

    def test_annualised_volatility_uses_log_returns(self) -> None:
        totals = [1_000_000.0, 1_010_000.0, 1_005_000.0, 1_020_000.0]
        metrics = compute_metrics(_equity(totals), [])
        logs = [math.log(b / a) for a, b in zip(totals, totals[1:])]
        mean = sum(logs) / len(logs)
        std = math.sqrt(sum((value - mean) ** 2 for value in logs) / (len(logs) - 1))
        self.assertAlmostEqual(metrics.annualised_vol, std * math.sqrt(TRADING_DAYS), places=12)

    def test_sharpe_uses_daily_excess_returns(self) -> None:
        totals = [1_000_000.0, 1_010_000.0, 1_005_000.0, 1_025_000.0]
        metrics = compute_metrics(_equity(totals), [], risk_free_rate=0.02)
        daily_rf = 1.02 ** (1 / TRADING_DAYS) - 1
        excess = [b / a - 1 - daily_rf for a, b in zip(totals, totals[1:])]
        mean = sum(excess) / len(excess)
        std = math.sqrt(sum((value - mean) ** 2 for value in excess) / (len(excess) - 1))
        self.assertAlmostEqual(metrics.sharpe, mean / std * math.sqrt(TRADING_DAYS), places=12)

    def test_sortino_is_none_without_negative_excess(self) -> None:
        metrics = compute_metrics(_equity([1_000.0, 1_100.0, 1_200.0]), [], risk_free_rate=0.0)
        self.assertIsNone(metrics.sortino)

    def test_sortino_matches_the_downside_formula(self) -> None:
        totals = [1_000.0, 1_100.0, 990.0, 1_050.0]
        metrics = compute_metrics(_equity(totals), [], risk_free_rate=0.0)
        excess = [b / a - 1 for a, b in zip(totals, totals[1:])]
        mean = sum(excess) / len(excess)
        downside = [min(value, 0.0) for value in excess]
        dmean = sum(downside) / len(downside)
        dstd = math.sqrt(sum((value - dmean) ** 2 for value in downside) / (len(downside) - 1))
        expected = mean * TRADING_DAYS / (dstd * math.sqrt(TRADING_DAYS))
        self.assertAlmostEqual(metrics.sortino, expected, places=12)


class DrawdownMetricTests(unittest.TestCase):
    def test_max_drawdown_is_a_negative_fraction(self) -> None:
        metrics = compute_metrics(_equity([1_000.0, 1_100.0, 990.0, 1_050.0]), [])
        self.assertAlmostEqual(metrics.max_drawdown, -0.10, places=12)

    def test_calmar_is_none_without_a_drawdown(self) -> None:
        metrics = compute_metrics(_equity([1_000.0, 1_100.0, 1_200.0]), [])
        self.assertEqual(metrics.max_drawdown, 0.0)
        self.assertIsNone(metrics.calmar)

    def test_calmar_divides_cagr_by_absolute_drawdown(self) -> None:
        metrics = compute_metrics(_equity([1_000.0, 900.0, 950.0]), [])
        self.assertIsNotNone(metrics.cagr)
        self.assertAlmostEqual(metrics.calmar, metrics.cagr / 0.10, places=12)


class TradeMetricTests(unittest.TestCase):
    def test_turnover_and_cost_drag_use_trade_notional_and_total_costs(self) -> None:
        trades = [_trade("buy", 10.0, 100.0, costs=5.0), _trade("sell", 5.0, 110.0, costs=3.0)]
        totals = [1_000.0, 1_050.0, 1_100.0]
        metrics = compute_metrics(_equity(totals), trades)
        mean_total = sum(totals) / 3
        years = 2 / YEAR_DAYS
        notional = sum(abs(trade.net_base) for trade in trades)
        self.assertAlmostEqual(metrics.turnover, notional / (2 * mean_total) / years, places=12)
        self.assertAlmostEqual(metrics.cost_drag, 8.0 / mean_total / years, places=12)
        self.assertEqual(metrics.total_costs_base, 8.0)

    def test_hit_rate_counts_closed_round_trips_fifo(self) -> None:
        trades = [
            _trade("buy", 10.0, 100.0),
            _trade("sell", 5.0, 110.0),
            _trade("sell", 5.0, 90.0),
        ]
        metrics = compute_metrics(_equity([1_000.0, 1_000.0]), trades)
        self.assertAlmostEqual(metrics.hit_rate, 0.5, places=12)

    def test_hit_rate_is_none_without_round_trips(self) -> None:
        metrics = compute_metrics(_equity([1_000.0, 1_000.0]), [_trade("buy", 10.0, 100.0)])
        self.assertIsNone(metrics.hit_rate)

    def test_exposure_is_the_mean_share_in_holdings(self) -> None:
        metrics = compute_metrics(_equity([1_000.0, 1_000.0], holdings=[500.0, 1_000.0]), [])
        self.assertAlmostEqual(metrics.exposure, 0.75, places=12)

    def test_trade_count_and_sessions_are_reported(self) -> None:
        metrics = compute_metrics(_equity([1_000.0] * 4), [_trade("buy", 1.0, 10.0)])
        self.assertEqual(metrics.sessions, 4)
        self.assertEqual(metrics.trades, 1)

    def test_empty_curve_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            compute_metrics([], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
