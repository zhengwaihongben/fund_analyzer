# -*- coding: utf-8 -*-
"""Backtest report renderer, shared by the CLI and the GUI.

This is the renderer that used to live in ``scripts/backtest_report.py``;
it was moved here verbatim (D-01 of the V2.4 Stage D batch) so that the GUI
and the CLI print exactly the same text for the same persisted run.  The
function is pure: it receives a ``BacktestRun`` and never opens the
database, never re-reads a price and never re-runs the engine.
"""
from __future__ import annotations

import json


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.6%}"


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:.6f}"


def render(run) -> str:
    spec_json = json.loads(run.spec_json or "{}")
    runtime = spec_json.get("runtime") or {}
    costs = json.loads(run.costs_json or "{}")
    first = run.equity_curve[0]
    last = run.equity_curve[-1]
    metrics = run.metrics
    lines = [
        f"# Backtest report — `{run.run_id}`",
        "",
        f"- Specification: `{run.spec_id}`",
        f"- Period: {run.start} … {run.end} (base currency {run.base_currency})",
        f"- Created: {run.created_at.isoformat()}",
        f"- Model version: {run.model_version}",
        f"- Data fingerprint: `{run.data_fingerprint}`",
        f"- Look-ahead status: `{run.look_ahead_status}`",
        "",
        "## Identity and configuration",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Weighting | {spec_json.get('weighting', '—')} |",
        f"| Rebalance | {spec_json.get('rebalance', {})} |",
        f"| Initial capital | {spec_json.get('initial_capital', '—')} CNY |",
        f"| Decision lag (min) | {runtime.get('decision_lag_minutes', '—')} |",
        f"| Execution lag (sessions) | {runtime.get('execution_lag_sessions', '—')} |",
        f"| Fractional shares | {runtime.get('fractional_shares', '—')} |",
        f"| Risk-free rate | {runtime.get('risk_free_rate', '—')} |",
        f"| Commission rate | {costs.get('commission_rate', '—')} |",
        f"| Minimum commission | {costs.get('min_commission', '—')} |",
        f"| Slippage (bps) | {costs.get('slippage_bps', '—')} |",
        f"| FX spread (bps) | {costs.get('fx_spread_bps', '—')} |",
        "",
        "## Performance",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Sessions | {metrics.sessions} |",
        f"| Trades | {metrics.trades} |",
        f"| CAGR | {_pct(metrics.cagr)} |",
        f"| Annualised volatility | {_pct(metrics.annualised_vol)} |",
        f"| Sharpe | {_number(metrics.sharpe)} |",
        f"| Sortino | {_number(metrics.sortino)} |",
        f"| Max drawdown | {_pct(metrics.max_drawdown)} |",
        f"| Calmar | {_number(metrics.calmar)} |",
        f"| Turnover | {_number(metrics.turnover)} |",
        f"| Hit rate | {_pct(metrics.hit_rate)} |",
        f"| Exposure | {_pct(metrics.exposure)} |",
        f"| Cost drag (annual) | {_pct(metrics.cost_drag)} |",
        f"| Total costs | {metrics.total_costs_base:.2f} CNY |",
        "",
        "## Costs by side",
        "",
        "| Side | Trades | Commission | Slippage | FX spread | Total |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for side in ("buy", "sell"):
        rows = [trade for trade in run.trades if trade.side == side]
        if not rows:
            lines.append(f"| {side} | 0 | 0.00 | 0.00 | 0.00 | 0.00 |")
            continue
        lines.append(
            f"| {side} | {len(rows)} | "
            f"{sum(t.commission_base for t in rows):.2f} | "
            f"{sum(t.slippage_base for t in rows):.2f} | "
            f"{sum(t.fx_spread_base for t in rows):.2f} | "
            f"{sum(t.commission_base + t.slippage_base + t.fx_spread_base for t in rows):.2f} |"
        )

    lines += [
        "",
        "## Equity curve",
        "",
        f"- Start: {first.session} → {first.total_base:.2f} CNY",
        f"- End: {last.session} → {last.total_base:.2f} CNY",
        f"- Minimum equity: {min(point.total_base for point in run.equity_curve):.2f} CNY",
        "",
        "| Session | Cash | Holdings | Total | Exposure | Benchmark | Drawdown |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    step = max(1, len(run.equity_curve) // 25)
    for point in run.equity_curve[::step]:
        benchmark = "—" if point.benchmark_base is None else f"{point.benchmark_base:.2f}"
        lines.append(
            f"| {point.session} | {point.cash_base:.2f} | {point.holdings_base:.2f} | "
            f"{point.total_base:.2f} | {_pct(point.exposure)} | {benchmark} | "
            f"{_pct(point.drawdown)} |"
        )
    if (len(run.equity_curve) - 1) % step:
        point = run.equity_curve[-1]
        benchmark = "—" if point.benchmark_base is None else f"{point.benchmark_base:.2f}"
        lines.append(
            f"| {point.session} | {point.cash_base:.2f} | {point.holdings_base:.2f} | "
            f"{point.total_base:.2f} | {_pct(point.exposure)} | {benchmark} | "
            f"{_pct(point.drawdown)} |"
        )

    lines += [
        "",
        "## Trades",
        "",
        "| Session | Ticker | Side | Quantity | Price | Source | FX | FX source | Net (CNY) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for trade in run.trades:
        lines.append(
            f"| {trade.session} | `{trade.ticker}` | {trade.side} | {trade.quantity:.6f} | "
            f"{trade.price_local:.4f} | {trade.price_source} | {trade.fx_rate:.6f} | "
            f"{trade.fx_source} | {trade.net_base:.2f} |"
        )
    if run.anomalies:
        lines += ["", "## Anomalies", ""]
        lines += [f"- {item}" for item in run.anomalies]
    lines += [
        "",
        "## Provenance",
        "",
        "All numbers above come from the persisted run "
        f"`{run.run_id}` (tables `strategy_backtest_runs`, `strategy_backtest_metrics`,",
        "`strategy_backtest_trades`, `strategy_backtest_equity`). The benchmark column",
        "reuses the equity curve's own `benchmark_base`, which was computed through the",
        "same PIT `DataAccess` read paths as the portfolio; no price is re-read here.",
        "",
    ]
    return "\n".join(lines)


__all__ = ["render"]
