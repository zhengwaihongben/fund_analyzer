# -*- coding: utf-8 -*-
"""Benchmark series for a backtest run (Stage D, reviews P1-5, P1-6 and P1-7).

The benchmark follows the same rules as the strategy itself, session by session:

* **every session is read as of that session's own decision instant** — the same
  ``close + lag`` moment the engine uses — so a bar published in February cannot
  enter a January point of the curve (review P1-6; reading the whole window at the
  run's final instant was a look-ahead defect);
* the same rule applies to the FX conversion: a rate published after the session's
  decision instant cannot be used to convert that session;
* its venue and currency come from its own stored rows, so a Hong Kong index is
  read on the Hong Kong calendar — the union grid is the *strategy's* calendar,
  not the benchmark's;
* a closed venue carries the previous level; an open venue whose price or FX is
  not yet published records an anomaly and leaves the point empty, because
  filling it later would be looking ahead;
* it is normalised to ``initial_capital`` on its first usable session, so the
  curve can be plotted against the equity curve directly;
* the price rows and the FX rows actually consumed are returned alongside the
  levels, and join the run's data fingerprint (review P1-7).  A benchmark quoted
  in GBP therefore changes identity when GBP/CNY is revised, even though the
  strategy only ever touched USD.

The module returns plain numbers; the runner attaches them to ``EquityPoint``.
Keeping it separate means the engine never needs to know what a benchmark is.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Sequence

from fa.utils.calendars import calendar_for

from .data_access import (
    BacktestDataError,
    BacktestDataIntegrityError,
    BacktestDataMissingError,
    DataAccess,
)
from .models import StrategySpec

log = logging.getLogger(__name__)

__all__ = ["BenchmarkSeries", "benchmark_levels", "benchmark_rows", "benchmark_series"]


@dataclass(frozen=True)
class BenchmarkSeries:
    """The benchmark curve and the PIT evidence behind it.

    ``levels`` has one entry per session of the run.  ``price_rows`` and
    ``fx_rows`` hold only the bars the curve actually consumed (deduplicated),
    which is what makes the fingerprint describe real inputs rather than the
    whole table.
    """

    ticker: str
    market: str
    currency: str
    sessions: tuple[date, ...]
    levels: tuple[float | None, ...]
    anomalies: tuple[str, ...] = ()
    price_rows: tuple[dict[str, Any], ...] = ()
    fx_rows: tuple[dict[str, Any], ...] = ()

    def level_for(self, session: date) -> float | None:
        """The level aligned with ``session`` (``None`` when not yet public)."""
        try:
            index = self.sessions.index(session)
        except ValueError:
            return None
        return self.levels[index]

    def rows(self) -> list[dict[str, Any]]:
        """Canonical rows for the data fingerprint, in a stable order.

        ``revision_id`` is kept here for debugging; it is *not* a market input,
        so :meth:`fingerprint_rows` strips it before hashing (review round 4).
        """
        rows: list[dict[str, Any]] = [
            {"benchmark": self.ticker,
             "market": self.market,
             "currency": self.currency},
        ]
        rows.extend(self.price_rows)
        rows.extend(self.fx_rows)
        return rows

    def fingerprint_rows(self) -> list[dict[str, Any]]:
        """``rows()`` without the database bookkeeping the digest must ignore."""
        return [
            {key: value for key, value in row.items() if key != "revision_id"}
            for row in self.rows()
        ]


def _default_instant(spec: StrategySpec, lag_minutes: int) -> Callable[[date], datetime]:
    """The runner's decision instant, imported lazily to avoid an import cycle."""
    from .runner import decision_instant

    return lambda session: decision_instant(
        spec, session, lag_minutes=lag_minutes)


def _price_row(
    ticker: str,
    point: Any,
    *,
    price: float,
    price_source: str,
) -> dict[str, Any]:
    """One consumed benchmark bar, including the price actually used (P1-9).

    ``adj_close`` is a total-return series, so the level must use it when it is
    present; recording ``price`` and ``price_source`` is what lets the digest
    say which of the two numbers the curve was built from.
    """
    return {
        "kind": "benchmark_price",
        "benchmark": ticker,
        "date": point.price_date.isoformat(),
        "close": float(point.close),
        "adj_close": None if point.adj_close is None else float(point.adj_close),
        "price": float(price),
        "price_source": price_source,
        "market_timestamp": point.market_timestamp.isoformat()
        if point.market_timestamp else None,
        "available_at": point.available_at.isoformat()
        if point.available_at else None,
        "availability_source": str(point.availability_source.value),
        "source": point.source,
        "revision_id": int(point.revision_id),
    }


def benchmark_series(
    spec: StrategySpec,
    data_access: DataAccess,
    sessions: Sequence[date],
    *,
    lag_minutes: int,
    instant_for: Callable[[date], datetime] | None = None,
) -> BenchmarkSeries | None:
    """Read the benchmark PIT, one decision instant per session.

    Returns ``None`` when the spec declares no benchmark.  A missing price or FX
    rate on an open venue becomes an anomaly and an empty point; an integrity
    error (contradictory venue or currency metadata) propagates, because a
    benchmark built from mixed data would silently mislead the comparison.
    """
    if not isinstance(spec, StrategySpec):
        raise TypeError(f"spec 必須是 StrategySpec: {spec!r}")
    if not isinstance(data_access, DataAccess):
        raise TypeError(f"data_access 必須是 DataAccess: {data_access!r}")
    ordered = tuple(sessions)
    if not spec.benchmark:
        return None

    ticker = spec.benchmark
    try:
        market, currency = data_access.instrument_metadata(ticker)
    except BacktestDataMissingError as exc:
        return BenchmarkSeries(
            ticker=ticker, market="", currency="", sessions=ordered,
            levels=(None,) * len(ordered),
            anomalies=(f"benchmark-unavailable: {exc}",))

    instant = instant_for or _default_instant(spec, lag_minutes)
    calendar = calendar_for(market)
    capital = float(spec.initial_capital)

    levels: list[float | None] = []
    anomalies: list[str] = []
    price_rows: list[dict[str, Any]] = []
    fx_rows: list[dict[str, Any]] = []
    seen_prices: set[tuple[str, int]] = set()
    last_level: float | None = None
    base_level: float | None = None

    for session in ordered:
        if not calendar.is_trading_day(session):
            # The benchmark's own venue is shut: carry, never invent a move.
            levels.append(last_level)
            continue
        as_of = instant(session)
        try:
            series = data_access.series_for(
                ticker, as_of, start=session, end=session)
        except BacktestDataMissingError:
            anomalies.append(
                f"benchmark-missing-price: {ticker} 在 {session.isoformat()} "
                f"開市但沒有已公布價格")
            levels.append(None)
            continue
        point = next(
            (item for item in series.points if item.price_date == session), None)
        if point is None:
            anomalies.append(
                f"benchmark-missing-price: {ticker} 在 {session.isoformat()} "
                f"開市但沒有已公布價格")
            levels.append(None)
            continue

        # P1-9: the level is a total-return approximation, so ``adj_close`` wins
        # whenever the vendor provided it; crediting dividends again on top of an
        # adjusted series would double count them.
        if point.adj_close is not None:
            price, price_source = float(point.adj_close), "adj_close"
        else:
            price, price_source = float(point.close), "close"

        key = (session.isoformat(), int(point.revision_id))
        if key not in seen_prices:
            seen_prices.add(key)
            price_rows.append(_price_row(
                ticker, point, price=price, price_source=price_source))

        rate = 1.0
        if currency != "CNY":
            fx_series = data_access.fx(currency, as_of, end=session)
            if fx_series is None:
                anomalies.append(
                    f"benchmark-missing-fx: {ticker} 在 {session.isoformat()} "
                    f"沒有已公布匯率")
                levels.append(None)
                continue
            entry = fx_series.rate_on_or_before(session)
            if entry is None or entry[0] is None:
                anomalies.append(
                    f"benchmark-missing-fx: {ticker} 在 {session.isoformat()} "
                    f"沒有已公布匯率")
                levels.append(None)
                continue
            rate = float(entry[0])
            # FX joins the fingerprint as the rate actually used at this
            # decision instant.  A row payload would leak a later revision into
            # earlier sessions: the repository keeps no FX version history
            # (decision Q7), so a revision bumps the current row's availability.
            fx_rows.append({
                "kind": "benchmark_fx",
                "currency": currency,
                "session": session.isoformat(),
                "rate": rate,
            })

        cny = price * rate
        if base_level is None:
            base_level = cny
        level = capital * cny / base_level if base_level else cny
        last_level = level
        levels.append(level)

    return BenchmarkSeries(
        ticker=ticker, market=market, currency=currency, sessions=ordered,
        levels=tuple(levels), anomalies=tuple(anomalies),
        price_rows=tuple(price_rows), fx_rows=tuple(fx_rows))


def benchmark_levels(
    spec: StrategySpec,
    data_access: DataAccess,
    sessions: Sequence[date],
    *,
    lag_minutes: int = 30,
    instant_for: Callable[[date], datetime] | None = None,
) -> tuple[list[float | None], list[str]]:
    """``(levels, anomalies)`` aligned with ``sessions`` (no benchmark → ``[]``)."""
    series = benchmark_series(
        spec, data_access, sessions,
        lag_minutes=lag_minutes, instant_for=instant_for)
    if series is None:
        return [], []
    return list(series.levels), list(series.anomalies)


def benchmark_rows(
    spec: StrategySpec,
    data_access: DataAccess,
    sessions: Sequence[date],
    *,
    lag_minutes: int = 30,
    instant_for: Callable[[date], datetime] | None = None,
) -> list[dict[str, Any]]:
    """The benchmark's consumed PIT rows, for the run's data fingerprint."""
    series = benchmark_series(
        spec, data_access, sessions,
        lag_minutes=lag_minutes, instant_for=instant_for)
    return [] if series is None else series.rows()


# ``BacktestDataError`` is re-exported so callers can catch the base class.
_ = BacktestDataError
_ = BacktestDataIntegrityError
