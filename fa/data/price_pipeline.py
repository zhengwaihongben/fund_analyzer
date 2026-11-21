# -*- coding: utf-8 -*-
"""Price pipeline (V2.3.2 Stage A) — download, validate, persist, serve PIT.

Three layers, deliberately separate:

* ``fa/data/price_provider.py`` — fetches Yahoo bars and keeps the vendor stamp.
* this module — decides *when* to download, validates and stamps availability.
* ``Repository`` — stores rows, ledgers revisions, answers ``prices_as_of()``.

Why the pipeline does not simply trust the cache: a date-complete series can
still be unusable for PIT work.  V2.2 rows may have no ``available_at`` and
``availability_source='unknown'``, so they are invisible to ``prices_as_of()``.
Such rows are never assigned an availability instant merely from their stored
trading date.  The pipeline re-downloads them from the provider and stamps
availability only on bars actually returned by the provider; if the provider
cannot return a legacy bar, that row stays PIT-invisible.

Cache completeness is evaluated per trading date using the canonical price
version for that date (``fa/data/price_canonical.py``).  A stale, non-PIT-visible
duplicate key does not invalidate a date when a valid canonical PIT version
already exists — otherwise the duplicate would cause a permanent cache miss.

The overlap exists because vendors restate history: ``Start_fetch`` walks back
``PRICE_OVERLAP_SESSIONS`` trading days (and at most
``PRICE_OVERLAP_CALENDAR_DAYS`` calendar days), so a corrected close for a day we
already hold still reaches the ledger.  A metadata gap takes priority over that
overlap: the download must start at the earliest session without evidence even
when the tail is also short.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from fa.config import (
    PRICE_AVAILABILITY_LAG_MINUTES,
    PRICE_OVERLAP_CALENDAR_DAYS,
    PRICE_OVERLAP_SESSIONS,
)
from fa.data.price_canonical import canonical_points_by_day, point_is_pit_usable
from fa.data.price_provider import PriceBar, PriceFetch, fetch_price_bars
from fa.models.market_data import (
    AvailabilitySource,
    PricePoint,
    PriceSeries,
)
from fa.utils.calendars import calendar_for, calendar_is_fallback, normalise_exchange

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceRequest:
    """What the caller wants, independent of how it is obtained."""

    tickers: list[str]
    start: date
    end: date
    prefer_cache: bool = True

    def __post_init__(self) -> None:
        for value, name in ((self.start, "start"), (self.end, "end")):
            if isinstance(value, datetime) or not isinstance(value, date):
                raise TypeError(f"PriceRequest.{name} 必須是 date（不含時間）")
        if self.end < self.start:
            raise ValueError("PriceRequest.end 不可早於 start")
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in self.tickers or []:
            code = str(raw or "").strip().upper()
            if not code or code in seen:
                continue
            seen.add(code)
            cleaned.append(code)
        object.__setattr__(self, "tickers", cleaned)


@dataclass
class PriceUpdateOutcome:
    """What one ``update_prices()`` call did, per ticker and in total."""

    series: dict[str, PriceSeries] = field(default_factory=dict)
    inserted: int = 0
    updated: int = 0
    revisions: int = 0
    downloads: int = 0
    skipped: dict[str, str] = field(default_factory=dict)
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def availability_note(market: str, lag_minutes: int | None = None) -> str:
    lag = PRICE_AVAILABILITY_LAG_MINUTES if lag_minutes is None else lag_minutes
    venue = market or "未知市場"
    return (
        f"Yahoo 日線 bar 記在開盤時刻；本專案視為 {venue} 當日收盤成交，"
        f"可得時間 = 收盤 + {lag} 分鐘（inferred，非供應商公布時間）"
    )


def _session_floor(day: date, market: str) -> date:
    """Latest trading session on or before ``day``."""
    calendar = calendar_for(market)
    current = day
    for _ in range(14):
        if calendar.is_trading_day(current):
            return current
        current = calendar.previous_trading_day(current)
    return day


def _session_ceiling(day: date, market: str) -> date:
    """Earliest trading session on or after ``day``."""
    calendar = calendar_for(market)
    current = day
    for _ in range(14):
        if calendar.is_trading_day(current):
            return current
        current = calendar.next_trading_day(current)
    return day


def _sessions_back(day: date, sessions: int, market: str) -> date:
    calendar = calendar_for(market)
    current = day
    for _ in range(max(0, sessions)):
        current = calendar.previous_trading_day(current)
    return current


def infer_availability(
    day: date, market: str, lag_minutes: int | None = None
) -> tuple[datetime, list[str]]:
    """Publication instant for a daily bar: session close + lag, UTC.

    The bar is stamped at the session *open*, so treating it as known at the
    close is the earliest defensible choice; the lag stops an estimator that
    runs exactly at the close from consuming the bar it is trying to explain.
    A market whose calendar falls back to the weekend-only rule gets the
    placeholder close of 16:00 UTC and a warning, never a silent exactness.
    """
    lag = PRICE_AVAILABILITY_LAG_MINUTES if lag_minutes is None else lag_minutes
    calendar = calendar_for(market)
    warnings: list[str] = []
    if calendar_is_fallback(calendar):
        warnings.append(
            f"calendar-fallback: {market or 'unknown'} 只有週末規則，收盤時間以 16:00 UTC 近似"
        )
    if not calendar.is_trading_day(day):
        warnings.append(f"non-trading-day: {day.isoformat()} 不在 {market or 'unknown'} 行事曆")
    close = calendar.market_close_datetime(day)
    if close.tzinfo is None:
        close = close.replace(tzinfo=timezone.utc)
    return close.astimezone(timezone.utc) + timedelta(minutes=lag), warnings


def _last_completed_session(day: date, market: str, now: datetime) -> date:
    """Latest session whose bar is already published at ``now``.

    Used for the tail edge of the cache test: during a live session the vendor
    already returns a running bar, but that bar is not yet public by our own
    availability rule, so a series ending yesterday is complete, not short.
    """
    calendar = calendar_for(market)
    current = day
    for _ in range(21):
        if calendar.is_trading_day(current):
            available, _ = infer_availability(current, market)
            if available <= now:
                return current
        current = calendar.previous_trading_day(current)
    return _session_floor(day, market)


def _complete_window(
    request: PriceRequest, stored: PriceSeries, market: str, now: datetime
) -> bool:
    """Both window edges are covered by stored sessions."""
    if not stored.points:
        return False
    first = stored.points[0].price_date
    last = stored.points[-1].price_date
    return first <= _session_ceiling(request.start, market) and last >= _last_completed_session(
        request.end, market, now
    )


def _first_incomplete_date(series: PriceSeries) -> date | None:
    """Earliest stored session whose *canonical* version has no evidence.

    Q1 (confirmed 2026-11-18): a session whose only rows predate the availability
    model is **re-downloaded** through the provider.  Inferring an instant from
    the row's own trading date would assert that the stored number was public at
    a moment nobody observed.

    The check is per trading date, not per row: a session that already holds a
    usable version is complete even when a superseded legacy key with
    ``availability_source='unknown'`` still sits in the database.  Judging per
    row would make such a session look incomplete forever and re-download it on
    every request.
    """
    for day, point in canonical_points_by_day(series.points).items():
        if not point_is_pit_usable(point):
            return day
    return None


def _plan_download(
    request: PriceRequest, stored: PriceSeries, market: str, now: datetime
) -> tuple[bool, date | None]:
    """Decide between a cache hit and a download window.

    Returns ``(cache_hit, fetch_start)``; ``fetch_start`` is ``None`` when no
    download is needed.  The publication-metadata check runs **before** the
    coverage rules, so neither a short tail nor ``prefer_cache=False`` can hide
    an earlier evidence gap.  A cache hit requires all three: the caller accepts
    the cache, both window edges are covered, and every canonical session has
    publication evidence.
    """
    if not stored.points:
        return False, request.start

    first_incomplete = _first_incomplete_date(stored)
    covered = _complete_window(request, stored, market, now)

    if request.prefer_cache and covered and first_incomplete is None:
        return True, None

    last = stored.points[-1].price_date
    overlap = _sessions_back(last, PRICE_OVERLAP_SESSIONS, market)
    calendar_floor = last - timedelta(days=PRICE_OVERLAP_CALENDAR_DAYS)
    overlap_start = max(request.start, min(overlap, calendar_floor))

    if stored.points[0].price_date > _session_ceiling(request.start, market):
        # A genuine history gap: refetch the requested window from its start.
        base_start = request.start
    else:
        base_start = overlap_start

    if first_incomplete is not None:
        base_start = min(base_start, first_incomplete)
    return False, max(request.start, base_start)


def _jump_value(
    day: date, close: float, known: dict[date, float]
) -> float | None:
    previous_days = [item for item in known if item < day]
    if not previous_days:
        return None
    previous = known[max(previous_days)]
    if previous <= 0:
        return None
    return close / previous - 1.0


def _validate_bars(
    ticker: str,
    fetch: PriceFetch,
    request: PriceRequest,
    stored: PriceSeries,
    *,
    now: datetime,
    market: str,
    lag_minutes: int | None,
) -> tuple[list[PricePoint], list[dict[str, Any]], list[str], str | None]:
    """Turn provider bars into storable points, dropping only invalid bars.

    The pipeline keeps its own guards instead of trusting the provider: a
    duplicate date is de-duplicated here too, and a jump is recorded rather than
    smoothed, deleted or (worst of all) inverted.
    """
    anomalies: list[dict[str, Any]] = []
    warnings: list[str] = []
    known: dict[date, float] = {point.price_date: point.close for point in stored.points}
    # One row per session: the conflict key is (ticker, price_date, price_type),
    # so flipping between "close" and "close+adj_close" would create a second row
    # for the same session and split the PIT history in two.  The type is taken
    # from the canonical version of that date (the same rule the repository uses),
    # so a V2.2 "adjusted_close" row is updated in place while a date that already
    # holds a usable canonical row keeps that row's type.
    canonical = canonical_points_by_day(stored.points)
    stored_type_by_day = {
        day: point.available_price_type
        for day, point in canonical.items()
        if point.available_price_type
    }
    stored_types = set(stored_type_by_day.values())
    if "close+adj_close" in stored_types:
        default_type = "close+adj_close"
    elif "close" in stored_types:
        default_type = "close"
    elif len(stored_types) == 1:
        default_type = next(iter(stored_types))
    else:
        default_type = None
    by_day: dict[date, PriceBar] = {}
    for bar in fetch.bars:
        if bar.trading_date < request.start or bar.trading_date > request.end:
            anomalies.append(
                {"ticker": ticker, "price_date": bar.trading_date.isoformat(), "kind": "out-of-range"}
            )
            continue
        if not math.isfinite(float(bar.close)) or float(bar.close) <= 0:
            anomalies.append(
                {"ticker": ticker, "price_date": bar.trading_date.isoformat(), "kind": "invalid-rate"}
            )
            continue
        previous = by_day.get(bar.trading_date)
        if previous is not None:
            anomalies.append(
                {"ticker": ticker, "price_date": bar.trading_date.isoformat(), "kind": "duplicate-date"}
            )
            if previous.market_timestamp and bar.market_timestamp < previous.market_timestamp:
                continue
        by_day[bar.trading_date] = bar

    points: list[PricePoint] = []
    for day in sorted(by_day):
        bar = by_day[day]
        close = float(bar.close)
        adj_close = bar.adj_close
        if adj_close is not None and (not math.isfinite(float(adj_close)) or float(adj_close) <= 0):
            warnings.append(f"{ticker}: {day.isoformat()} adj_close 非正數，保留 close 並標記 fallback")
            adj_close = None
        jump = _jump_value(day, close, known)
        if jump is not None and abs(jump) > 0.50:
            anomalies.append(
                {"ticker": ticker, "price_date": day.isoformat(), "kind": "jump", "value": jump}
            )
        available, cal_warnings = infer_availability(day, market, lag_minutes=lag_minutes)
        warnings.extend(cal_warnings)
        price_type = (
            stored_type_by_day.get(day)
            or default_type
            or ("close+adj_close" if adj_close is not None else "close")
        )
        points.append(
            PricePoint(
                price_date=day,
                close=close,
                adj_close=adj_close,
                market_timestamp=bar.market_timestamp,
                available_at=available,
                availability_source=AvailabilitySource.INFERRED,
                availability_note=availability_note(market, lag_minutes),
                fetched_at=now,
                market=bar.market or market,
                currency=bar.currency or stored.currency,
                available_price_type=price_type,
                used_price_type=price_type,
                source=bar.source or "yahoo",
            )
        )
        known[day] = close
    if fetch.dropped:
        warnings.append(f"{ticker}: provider 丟棄 {fetch.dropped} 根無法解析的 bar")
    if fetch.duplicates:
        anomalies.append(
            {
                "ticker": ticker,
                "price_date": None,
                "kind": "provider-duplicate-date",
                "count": fetch.duplicates,
            }
        )
    return points, anomalies, warnings, None


def update_prices(
    request: PriceRequest,
    repository: Any,
    *,
    provider: Callable[[str, date, date], PriceFetch] | None = None,
    now: datetime | None = None,
) -> PriceUpdateOutcome:
    """Ensure the requested window is stored and PIT-visible for each ticker.

    A per-ticker failure never aborts the others: the outcome records it in
    ``skipped`` and the remaining tickers still update.  Counters are applied
    only after the database write succeeded, so a failed write reports zero
    inserted rows rather than rows that were merely planned.
    """
    if not isinstance(request, PriceRequest):
        raise TypeError("update_prices() 需要 PriceRequest")
    moment = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise ValueError("update_prices(now=…) 必須是帶時區的 datetime")
    moment = moment.astimezone(timezone.utc)
    fetch = provider or fetch_price_bars
    outcome = PriceUpdateOutcome()

    for ticker in request.tickers:
        try:
            stored = repository.load_prices(ticker, request.start, request.end)
        except Exception as exc:  # noqa: BLE001 - 單一 ticker 失敗不可擴散
            outcome.skipped[ticker] = f"database-error: {exc}"
            continue
        market = stored.market if stored.points else ""
        cache_hit, fetch_start = _plan_download(request, stored, market, moment)
        if cache_hit:
            outcome.series[ticker] = stored
            continue

        outcome.downloads += 1
        try:
            fetch_result = fetch(ticker, fetch_start or request.start, request.end)
        except Exception as exc:  # noqa: BLE001 - provider 契約外的例外
            outcome.skipped[ticker] = f"provider-error: {exc}"
            continue
        if not isinstance(fetch_result, PriceFetch):
            outcome.skipped[ticker] = "provider-contract: 回傳型別不是 PriceFetch"
            continue
        if fetch_result.error:
            outcome.skipped[ticker] = fetch_result.error
            continue
        if (
            stored.points
            and stored.market
            and fetch_result.market
            and normalise_exchange(stored.market) != normalise_exchange(fetch_result.market)
        ):
            # Mixing venues under one ticker silently swaps currency and price
            # level (LLY on NYSE vs LLY.SW in CHF); refuse rather than corrupt.
            outcome.skipped[ticker] = (
                f"market-mismatch: stored {stored.market} vs vendor {fetch_result.market}"
            )
            outcome.warnings.append(
                f"{ticker}: 供應商回傳的交易所 {fetch_result.market} 與已存 {stored.market} 不同，已拒絕寫入"
            )
            continue
        for warning in fetch_result.warnings:
            outcome.warnings.append(f"{ticker}: {warning}")
        if fetch_result.duplicates:
            outcome.anomalies.append(
                {
                    "ticker": ticker,
                    "price_date": None,
                    "kind": "provider-duplicate-date",
                    "count": fetch_result.duplicates,
                }
            )
        market = fetch_result.market or market
        points, anomalies, warnings, skip_reason = _validate_bars(
            ticker, fetch_result, request, stored,
            now=moment, market=market, lag_minutes=None,
        )
        outcome.anomalies.extend(anomalies)
        outcome.warnings.extend(f"{ticker}: {item}" for item in warnings)
        if skip_reason:
            outcome.skipped[ticker] = skip_reason
            continue
        if not points:
            outcome.skipped[ticker] = "no-usable-bars"
            continue
        series = PriceSeries.from_points(
            ticker, points, market=market,
            currency=fetch_result.currency or stored.currency,
            source="yahoo",
        )
        stored_dates = {point.price_date for point in stored.points}
        inserted = sum(1 for point in points if point.price_date not in stored_dates)
        updated = len(points) - inserted
        try:
            before = repository.count_price_revisions(ticker)
            repository.save_prices(series)
            after = repository.count_price_revisions(ticker)
        except Exception as exc:  # noqa: BLE001
            outcome.skipped[ticker] = f"database-error: {exc}"
            continue
        outcome.inserted += inserted
        outcome.updated += updated
        outcome.revisions += max(0, after - before)
        outcome.series[ticker] = series
        log.info(
            "%s 下載 %d 根 bar：新增 %d、更新 %d、修訂 %d",
            ticker, len(points), inserted, updated, max(0, after - before),
        )
    return outcome


__all__ = [
    "PriceRequest",
    "PriceUpdateOutcome",
    "availability_note",
    "infer_availability",
    "update_prices",
]
