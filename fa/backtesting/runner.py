# -*- coding: utf-8 -*-
"""Backtest runner: fingerprint, idempotency and persistence (Stage D).

The runner is the only place that knows about the database *and* the strategy
meaning at the same time.  Its jobs, in order:

1. **Resolve the effective configuration once** (spec §9) and pass concrete
   values down; the engine never reads the environment.  The resolved block is
   part of the run identity: changing ``FA_BACKTEST_EXECUTION_LAG_SESSIONS``
   must produce a new run, not silently reuse an old one computed under
   different rules (review P1-3).
2. **Identify the run** by a hash of the specification, the effective runtime
   configuration *and* of a PIT fingerprint of the inputs that could affect the
   run (spec §4.4).  The fingerprint deliberately uses a conservative superset
   for session prices, while momentum windows mirror the engine's actual reads.
   The content hash is the important part: a revised historical
   close leaves row counts, date ranges and ``max(fetched_at)`` untouched, so a
   range summary would let an old result be reused after its inputs changed.
   Dividend and split columns participate because the engine consumes them when
   a series has no adjusted close (review P1-1).
3. **Persist once, atomically** through :class:`Repository`, and return
   ``created=False`` when the identical run is already stored — including its
   anomalies (review P1-2).

The engine's output is unchanged by Stage D: metrics are computed from the
equity curve and trades, never from prices (spec §7).
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fa import config
from fa.backtesting.benchmark import BenchmarkSeries, benchmark_series
from fa.backtesting.data_access import BacktestDataMissingError, DataAccess
from fa.backtesting.engine import EngineResult, is_rebalance_day, run_engine
from fa.backtesting.metrics import compute_metrics
from fa.backtesting.models import (
    COST_MODEL_DEFAULT,
    BacktestMetrics,
    BacktestRun,
    CostModel,
    StrategySpec,
)
from fa.data.database.schema import SCHEMA_VERSION
from fa.models.market_data import iso_utc
from fa.utils.calendars import calendar_for

log = logging.getLogger(__name__)

#: Version stamped on every persisted run; part of ``run_id``.
MODEL_VERSION = "2.3.2"


@dataclass(frozen=True)
class RunConfig:
    """The concrete settings one run was executed under (review P1-3).

    The block is hashed into ``run_id`` and stored with the run, so a stored
    result can always be read as "these rules, on this data".

    Precedence between the two cost sources, documented because it is easy to
    get wrong: an explicit :class:`CostModel` on the spec always wins.  The
    environment block is only the *default* cost model — it applies when the
    spec left ``CostModel()`` untouched, which is what makes
    ``FA_BACKTEST_COMMISSION_BPS`` useful without letting an env var silently
    override a spec that stated its own costs.
    """

    decision_lag_minutes: int
    execution_lag_sessions: int
    fractional_shares: bool
    risk_free_rate: float
    costs: CostModel

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_lag_minutes": self.decision_lag_minutes,
            "execution_lag_sessions": self.execution_lag_sessions,
            "fractional_shares": self.fractional_shares,
            "risk_free_rate": float(self.risk_free_rate),
            "costs": costs_payload(self.costs),
        }


def costs_payload(costs: CostModel) -> dict[str, float]:
    """Canonical ``CostModel`` mapping, shared by ``spec_json`` and the hash."""
    return {
        "commission_rate": float(costs.commission_rate),
        "min_commission": float(costs.min_commission),
        "slippage_bps": float(costs.slippage_bps),
        "fx_spread_bps": float(costs.fx_spread_bps),
    }


def _config_value(name: str, default: Any) -> Any:
    """Read a Stage D setting once, falling back to the documented default."""
    return getattr(config, name, default)


def _costs_from_config() -> CostModel:
    """The default cost model described by the ``FA_BACKTEST_*`` environment."""
    return CostModel(
        commission_rate=float(_config_value("BACKTEST_COMMISSION_BPS", 5.0)) / 10_000.0,
        min_commission=float(_config_value("BACKTEST_MIN_COMMISSION", 0.0)),
        slippage_bps=float(_config_value("BACKTEST_SLIPPAGE_BPS", 10.0)),
        fx_spread_bps=float(_config_value("BACKTEST_FX_SPREAD_BPS", 20.0)),
    )


def resolve_config(spec: StrategySpec) -> RunConfig:
    """Read the effective run settings for ``spec`` (spec §9).

    Cost precedence (review P2): an explicitly supplied ``CostModel`` always
    wins; the ``FA_BACKTEST_*`` values are the default for a spec that left
    ``costs`` at :data:`COST_MODEL_DEFAULT`.  Identity — not equality — decides,
    so an explicit ``CostModel()`` (which equals the default field by value) is
    still honoured as an explicit request for zero-ish default costs.
    """
    explicit_costs = spec.costs is not COST_MODEL_DEFAULT
    return RunConfig(
        decision_lag_minutes=int(_config_value("BACKTEST_DECISION_LAG_MINUTES", 30)),
        execution_lag_sessions=int(_config_value("BACKTEST_EXECUTION_LAG_SESSIONS", 1)),
        fractional_shares=bool(_config_value("BACKTEST_FRACTIONAL_SHARES", True)),
        risk_free_rate=float(_config_value("BACKTEST_RISK_FREE_RATE", 0.02)),
        costs=spec.costs if explicit_costs else _costs_from_config(),
    )


def canonical_spec(spec: StrategySpec, runtime: RunConfig | None = None) -> dict[str, Any]:
    """Serialise a validated spec into the canonical form used by ``run_id``.

    Every field participates, ``initial_capital`` included (spec §4.4): two runs
    that differ only in starting capital are different runs.  ``runtime`` adds
    the effective settings block when one is supplied.
    """
    payload: dict[str, Any] = {
        "spec_id": spec.spec_id,
        "universe": [
            {"ticker": instrument.ticker,
             "market": instrument.market,
             "currency": instrument.currency}
            for instrument in spec.universe
        ],
        "weighting": spec.weighting.value,
        "start": spec.start.isoformat(),
        "end": spec.end.isoformat(),
        "initial_capital": float(spec.initial_capital),
        "rebalance": {
            "frequency": spec.rebalance.frequency.value,
            "anchor": spec.rebalance.anchor,
        },
        "target_weights": {key: float(value)
                           for key, value in sorted(spec.target_weights.items())},
        "momentum_window": spec.momentum_window,
        "drift_threshold": float(spec.drift_threshold),
        "base_currency": spec.base_currency,
        "benchmark": spec.benchmark,
        "costs": costs_payload(spec.costs),
    }
    if runtime is not None:
        payload["runtime"] = runtime.as_dict()
    return payload


def canonical_json(payload: Any) -> str:
    """Stable JSON: sorted keys, compact separators, ``repr`` floats."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def spec_fingerprint(spec: StrategySpec, runtime: RunConfig | None = None) -> str:
    payload = canonical_json(canonical_spec(spec, runtime))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _grid(spec: StrategySpec) -> tuple[date, ...]:
    """Union of the venues' trading days over ``[start, end]`` (spec §5.1)."""
    calendars = {market: calendar_for(market)
                 for market in sorted({instrument.market for instrument in spec.universe})}
    days: set[date] = set()
    cursor = spec.start
    while cursor <= spec.end:
        if any(calendar.is_trading_day(cursor) for calendar in calendars.values()):
            days.add(cursor)
        cursor += timedelta(days=1)
    return tuple(sorted(days))


def decision_instant(
    spec: StrategySpec,
    session: date,
    *,
    lag_minutes: int,
) -> datetime:
    """The moment a decision for ``session`` may be taken (spec §5.2).

    The last venue to close sets the instant: an instrument that has not closed
    yet cannot be traded on that session's information.
    """
    closes: list[datetime] = []
    for market in sorted({instrument.market for instrument in spec.universe}):
        calendar = calendar_for(market)
        if not calendar.is_trading_day(session):
            continue
        closes.append(calendar.session(session).close)
    if not closes:
        raise ValueError(f"{session} 沒有任何市場開市，無法決定決策時刻")
    return max(closes) + timedelta(minutes=lag_minutes)


def _row_payload(point: Any) -> dict[str, Any]:
    """One PIT row as a market input (spec §4.4).

    ``revision_id`` is deliberately absent: it is database bookkeeping, not a
    market input, so a revision published later must not change the fingerprint
    of an earlier instant (review round 4).

    ``fetched_at`` is absent for the same reason (review round 5, P2): a plain
    re-download refreshes it without changing a single market value, so hashing
    it would invalidate runs that nothing invalidated.
    """
    return {
        "date": point.price_date.isoformat(),
        "close": float(point.close),
        "adj_close": None if point.adj_close is None else float(point.adj_close),
        "dividend": None if point.dividend is None else float(point.dividend),
        "split": None if point.split is None else float(point.split),
        "market_timestamp": iso_utc(point.market_timestamp) if point.market_timestamp else None,
        "available_at": iso_utc(point.available_at) if point.available_at else None,
        "availability_source": str(point.availability_source.value),
        "source": point.source,
    }


def fingerprint_session_payload(
    spec: StrategySpec,
    data_access: DataAccess,
    session: date,
    *,
    instant: datetime,
    rebalance_days: Collection[date] = (),
    window: int | None = None,
) -> dict[str, Any]:
    """One session's fingerprint block, exposed so tests can inspect its roles.

    The two price roles have **different** calendar rules (review round 7,
    P1-17):

    * ``session_price`` — the price this session would trade or mark with.  It
      exists only when the instrument's own venue is open, so a US holiday
      inside a Hong Kong session contributes nothing.  This is a *conservative
      superset*: it records the price the engine could use, not proof that the
      engine read it (a holding-free position may never ask).
    * ``momentum_window`` — the last ``window + 1`` observations visible at the
      decision instant.  A rebalance day reads these for **every** instrument,
      open or shut, because ``_Engine._momentum_weights()`` asks every ticker
      for its series regardless of the venue's state that day.  Skipping a shut
      venue here would drop an input the engine actually uses.
    """
    if window is not None:
        width = int(window)
    else:
        width = int(spec.momentum_window) if spec.momentum_window else 0
    prices: list[dict[str, Any]] = []
    for instrument in sorted(spec.universe, key=lambda item: item.ticker):
        if calendar_for(instrument.market).is_trading_day(session):
            try:
                point = data_access.price_at(instrument, instant, session)
            except BacktestDataMissingError as exc:
                prices.append({"ticker": instrument.ticker,
                               "role": "session_price",
                               "session": session.isoformat(),
                               "missing": str(exc)})
            else:
                prices.append({"ticker": instrument.ticker,
                               "role": "session_price",
                               **_row_payload(point)})
        if width <= 0 or session not in rebalance_days:
            continue
        try:
            series = data_access.prices(instrument, instant, end=session)
        except BacktestDataMissingError as exc:
            prices.append({"ticker": instrument.ticker,
                           "role": "momentum_window",
                           "session": session.isoformat(),
                           "missing": str(exc)})
            continue
        for point in series.points[-width - 1:]:
            prices.append({"ticker": instrument.ticker,
                           "role": "momentum_window",
                           **_row_payload(point)})

    fx_rows: list[dict[str, Any]] = []
    for currency in sorted({instrument.currency for instrument in spec.universe
                            if instrument.currency != "CNY"}):
        entry = data_access.fx_rate(currency, instant, session)
        if entry is None or entry[0] is None:
            fx_rows.append({"currency": currency,
                            "session": session.isoformat(),
                            "missing": True})
            continue
        rate, rate_date = entry
        fx_rows.append({"currency": currency,
                        "session": session.isoformat(),
                        "rate": float(rate),
                        "rate_date": rate_date.isoformat()})
    return {
        "session": session.isoformat(),
        "decision_instant": iso_utc(instant),
        "prices": prices,
        "fx": fx_rows,
    }


def data_fingerprint(
    spec: StrategySpec,
    data_access: DataAccess,
    *,
    sessions: Sequence[date],
    lag_minutes: int,
    benchmark: BenchmarkSeries | None = None,
) -> str:
    """Content hash of the PIT inputs seen at *each* decision instant.

    ``F(t) = H(PIT-visible input values at t)``: the hash describes what the run
    could have consumed at every session's own ``close + lag`` moment, not the
    final database state (review round 4, P1-8).  Each session block carries its
    session date and decision instant: price rows use the data visible at that
    instant, and the FX used is recorded for the same moment.

    Prices use two distinct roles:

    * ``session_price`` — a conservative superset of the PIT prices the engine
      could use.  It records the session bar whenever that instrument's venue is
      open; it does not claim that the engine necessarily read the bar.
    * ``momentum_window`` — the exact last ``window + 1`` observations requested
      by the engine for every instrument on each rebalance day, including
      instruments whose venue is closed on that date.

    FIXED and EQUAL strategies therefore hash session-price inputs only.
    MOMENTUM strategies additionally hash the per-instrument momentum windows on
    rebalance days.  Each session block carries its session date and decision
    instant.  Price rows are evaluated using the PIT data visible at that
    instant; they are not restricted to rows that first became visible since the
    preceding session.

    FX goes through :meth:`DataAccess.fx_rate`, the same call the engine makes, and
    the payload records the rate *and* the date it applies to: two databases can
    quote the same number from different observations, and the engine writes that
    date into its trades (review P1-10).  Reading a bounded range here would have
    reported a perfectly usable earlier observation as ``missing``.

    Only :class:`BacktestDataMissingError` becomes a ``missing`` marker; an
    integrity error propagates, because a hash that described contradictory
    data as merely absent would let a broken run look reproducible
    (review P1-4).

    ``benchmark`` contributes the price and FX inputs its curve consumed,
    session by session (review P1-6/P1-7), without the revision bookkeeping.
    """
    ordered = tuple(sessions)
    if not ordered:
        raise ValueError("sessions 不可為空：指紋至少需要一個決策時刻")
    if lag_minutes < 0:
        raise ValueError(f"lag_minutes 不可為負數: {lag_minutes!r}")

    window = int(spec.momentum_window) if spec.momentum_window else 0

    rebalance_days = (
        {day for day in ordered if is_rebalance_day(spec, day, ordered)}
        if window else set()
    )

    lines: list[str] = []
    for session in ordered:
        instant = decision_instant(spec, session, lag_minutes=lag_minutes)
        lines.append(canonical_json(fingerprint_session_payload(
            spec, data_access, session, instant=instant,
            rebalance_days=rebalance_days, window=window)))

    if benchmark is not None:
        for row in benchmark.fingerprint_rows():
            lines.append(canonical_json(row))

    payload = "\n".join(lines)
    return hashlib.sha256(
        f"{MODEL_VERSION}|{SCHEMA_VERSION}|{payload}".encode("utf-8")).hexdigest()


def run_id_for(
    spec: StrategySpec,
    fingerprint: str,
    runtime: RunConfig | None = None,
) -> str:
    """``sha256(canonical_spec + model_version + data_fingerprint)`` (spec §4.4)."""
    payload = (f"{canonical_json(canonical_spec(spec, runtime))}|"
               f"{MODEL_VERSION}|{fingerprint}")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run_from_result(
    spec: StrategySpec,
    *,
    runtime: RunConfig,
    run_id: str,
    fingerprint: str,
    created_at: datetime,
    metrics: BacktestMetrics,
    result: EngineResult,
) -> BacktestRun:
    canonical = canonical_spec(spec, runtime)
    return BacktestRun(
        run_id=run_id,
        spec_id=spec.spec_id,
        model_version=MODEL_VERSION,
        start=spec.start,
        end=spec.end,
        base_currency=spec.base_currency,
        universe_json=canonical_json(canonical["universe"]),
        spec_json=canonical_json(canonical),
        costs_json=canonical_json(costs_payload(runtime.costs)),
        data_fingerprint=fingerprint,
        look_ahead_status="unknown",
        created_at=created_at,
        metrics=metrics,
        trades=result.trades,
        equity_curve=result.equity_curve,
        anomalies=result.anomalies,
    )


def _free_run_id(repository: Any, base: str) -> str:
    """``force=True`` appends ``-N`` until the id is unused (spec §4.4)."""
    candidate = base
    suffix = 0
    while repository.load_backtest_run(candidate) is not None:
        suffix += 1
        candidate = f"{base}-{suffix}"
        if suffix > 1_000:
            raise RuntimeError(f"無法為 {base} 產生新的 run_id")
    return candidate


def _attach_benchmark(
    result: EngineResult,
    benchmark: BenchmarkSeries | None,
) -> EngineResult:
    """Put the benchmark on the equity curve and collect its anomalies."""
    if benchmark is None:
        return result
    curve = tuple(
        replace(point, benchmark_base=benchmark.level_for(point.session))
        for point in result.equity_curve
    )
    return replace(result, equity_curve=curve,
                   anomalies=result.anomalies + benchmark.anomalies)


def run_backtest(
    spec: StrategySpec,
    repository: Any,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> tuple[BacktestRun, bool]:
    """Run ``spec`` once and persist it, or return the identical stored run.

    ``created=False`` means a run with the same specification, the same effective
    settings and the same PIT content is already stored; nothing was written and
    the stored run is returned unchanged.
    """
    if not isinstance(spec, StrategySpec):
        raise TypeError(f"spec 必須是 StrategySpec: {spec!r}")
    if now is not None and (not isinstance(now, datetime) or now.tzinfo is None):
        raise ValueError("now 必須是帶時區的 datetime")
    created_at = now or datetime.now(timezone.utc)

    runtime = resolve_config(spec)
    grid = _grid(spec)
    if not grid:
        raise ValueError(
            f"{spec.start}–{spec.end} 之間沒有任何交易日，無法回測")
    data_access = DataAccess(repository)
    # The benchmark is read session by session, each as of that session's own
    # decision instant (review P1-6), and its price/FX rows join the fingerprint
    # (review P1-7).  It is therefore built before the identity is computed.
    benchmark = benchmark_series(
        spec, data_access, grid, lag_minutes=runtime.decision_lag_minutes)
    fingerprint = data_fingerprint(
        spec, data_access, sessions=grid,
        lag_minutes=runtime.decision_lag_minutes, benchmark=benchmark)
    base_run_id = run_id_for(spec, fingerprint, runtime)

    existing = repository.load_backtest_run(base_run_id)
    if existing is not None and not force:
        log.info("回測 %s 已存在（相同規格、設定與 PIT 內容），直接回傳", base_run_id)
        return existing, False

    effective = replace(spec, costs=runtime.costs)
    result = run_engine(
        effective,
        data_access,
        decision_lag_minutes=runtime.decision_lag_minutes,
        execution_lag_sessions=runtime.execution_lag_sessions,
        fractional_shares=runtime.fractional_shares,
    )
    result = _attach_benchmark(result, benchmark)
    metrics = compute_metrics(result.equity_curve, result.trades,
                              risk_free_rate=runtime.risk_free_rate)

    run_id = base_run_id if not force else _free_run_id(repository, base_run_id)
    run = _run_from_result(spec, runtime=runtime, run_id=run_id,
                           fingerprint=fingerprint, created_at=created_at,
                           metrics=metrics, result=result)
    repository.save_backtest_run(run)
    log.info("回測 %s 已寫入（%s 筆交易、%s 個交易日）",
             run_id, len(result.trades), len(result.equity_curve))
    return run, True


__all__ = [
    "MODEL_VERSION",
    "RunConfig",
    "canonical_json",
    "canonical_spec",
    "costs_payload",
    "data_fingerprint",
    "decision_instant",
    "resolve_config",
    "run_backtest",
    "run_id_for",
    "spec_fingerprint",
]
