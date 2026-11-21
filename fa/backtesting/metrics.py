# -*- coding: utf-8 -*-
"""Backtest performance metrics (V2.3.2 Stage D).

The input is a finished ``EngineResult``: the equity curve and the trade list.
Nothing here re-reads prices, hits the database or knows about a strategy — the
definitions in spec §7 are applied to the stored series, which is also what a
reader of the persisted run can reproduce.

A metric that cannot be computed is reported as ``None`` (spec §4.2), never as a
fake zero: a run with no negative excess has no Sortino, and a run with no
drawdown has no Calmar.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

from fa.backtesting.models import BacktestMetrics, EquityPoint, Trade

#: Trading days used to annualise a daily statistic.
TRADING_DAYS = 252
#: Days used by the CAGR exponent (a calendar year, not 252 sessions).
YEAR_DAYS = 365.25


def _simple_returns(totals: Sequence[float]) -> list[float]:
    out: list[float] = []
    for previous, current in zip(totals, totals[1:]):
        if previous <= 0.0:
            out.append(0.0)
            continue
        out.append(current / previous - 1.0)
    return out


def _log_returns(totals: Sequence[float]) -> list[float]:
    out: list[float] = []
    for previous, current in zip(totals, totals[1:]):
        if previous <= 0.0 or current <= 0.0:
            continue
        out.append(math.log(current / previous))
    return out


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _stdev(values: Sequence[float], *, ddof: int = 1) -> float | None:
    if len(values) - ddof <= 0:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - ddof)
    return math.sqrt(variance)


def _daily_risk_free(annual: float) -> float:
    """Convert an annual simple rate to its daily equivalent."""
    if annual <= -1.0:
        return 0.0
    return (1.0 + annual) ** (1.0 / TRADING_DAYS) - 1.0


def _years(first: EquityPoint, last: EquityPoint) -> float | None:
    days = (last.session - first.session).days
    if days <= 0:
        return None
    return days / YEAR_DAYS


def _max_drawdown(totals: Sequence[float]) -> float | None:
    peak = -math.inf
    worst = 0.0
    for total in totals:
        peak = max(peak, total)
        if peak > 0.0:
            worst = min(worst, total / peak - 1.0)
    return worst if math.isfinite(peak) else None


def _fifo_hit_rate(trades: Sequence[Trade]) -> float | None:
    """Share of closed round trips with a positive net PnL (FIFO)."""
    lots: dict[str, list[list[float]]] = {}
    closed = 0
    winners = 0
    for trade in trades:
        book = lots.setdefault(trade.ticker, [])
        if trade.side == "buy":
            book.append([trade.quantity, trade.net_base / trade.quantity])
            continue
        remaining = trade.quantity
        sell_price = trade.net_base / trade.quantity
        while remaining > 0.0 and book:
            lot = book[0]
            take = min(remaining, lot[0])
            closed += 1
            if sell_price > lot[1]:
                winners += 1
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-12:
                book.pop(0)
    return winners / closed if closed else None


def compute_metrics(
    equity_curve: Iterable[EquityPoint],
    trades: Iterable[Trade],
    *,
    risk_free_rate: float = 0.02,
) -> BacktestMetrics:
    """Apply the spec §7 definitions to a finished run."""
    curve = list(equity_curve)
    trade_list = list(trades)
    if not curve:
        raise ValueError("equity_curve 不可為空，沒有權益曲線就沒有指標")

    totals = [point.total_base for point in curve]
    first, last = curve[0], curve[-1]
    years = _years(first, last)
    sessions = len(curve)

    cagr = None
    if years is not None and first.total_base > 0.0:
        cagr = (last.total_base / first.total_base) ** (1.0 / years) - 1.0

    log_returns = _log_returns(totals)
    vol = _stdev(log_returns)
    annualised_vol = vol * math.sqrt(TRADING_DAYS) if vol is not None else None

    daily_rf = _daily_risk_free(risk_free_rate)
    excess = [value - daily_rf for value in _simple_returns(totals)]
    mean_excess = _mean(excess)
    excess_std = _stdev(excess)
    sharpe = None
    if mean_excess is not None and excess_std is not None and excess_std > 0.0:
        sharpe = mean_excess / excess_std * math.sqrt(TRADING_DAYS)

    downside = [min(value, 0.0) for value in excess]
    downside_std = _stdev(downside)
    sortino = None
    if (mean_excess is not None and downside_std is not None
            and downside_std > 0.0 and any(value < 0.0 for value in excess)):
        sortino = mean_excess * TRADING_DAYS / (downside_std * math.sqrt(TRADING_DAYS))

    max_drawdown = _max_drawdown(totals)
    calmar = None
    if cagr is not None and max_drawdown is not None and max_drawdown < 0.0:
        calmar = cagr / abs(max_drawdown)

    mean_total = _mean(totals)
    turnover = None
    cost_drag = None
    if mean_total is not None and mean_total > 0.0 and years is not None:
        turnover = sum(abs(trade.net_base) for trade in trade_list) / (2.0 * mean_total) / years
        total_costs = sum(trade.total_costs_base for trade in trade_list)
        cost_drag = total_costs / mean_total / years

    exposure = None
    if mean_total is not None and mean_total > 0.0:
        exposure = sum(
            point.holdings_base / point.total_base if point.total_base > 0.0 else 0.0
            for point in curve
        ) / sessions

    return BacktestMetrics(
        sessions=sessions,
        trades=len(trade_list),
        cagr=cagr,
        annualised_vol=annualised_vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        calmar=calmar,
        turnover=turnover,
        hit_rate=_fifo_hit_rate(trade_list),
        exposure=exposure,
        cost_drag=cost_drag,
        total_costs_base=sum(trade.total_costs_base for trade in trade_list),
    )


__all__ = ["TRADING_DAYS", "YEAR_DAYS", "compute_metrics"]
