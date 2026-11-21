# -*- coding: utf-8 -*-
"""FX rates with CNY as the base currency.

Every overseas asset is converted with ``FX_t/FX_0 - 1`` where ``FX`` is the
value of one unit of the asset currency expressed in CNY.  The fund is
denominated in CNY, so the base currency is configurable but defaults to CNY.

Since V2.3.1 Stage 2.3 this module is the **V2.2 facade**: it keeps the public
signatures the rest of the codebase already uses, and delegates the actual
download and date conversion to :mod:`fa.data.fx_provider`.  The date index of
``fetch_fx_series`` is now the **London** trading date of each bar, which fixes
a confirmed defect (F-FX-01): in BST the old code labelled the bar with the
previous UTC day, so summer returns were shifted by one day.

The provider carries full time information; callers that need it should use
:func:`fx_series_map_from_fetch` rather than the pandas facade.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd

from fa.data.fx_provider import (
    FX_QUOTE_CONVENTIONS,
    FxBar,
    FxFetch,
    fetch_fx_bars,
    fetch_fx_bars_many,
    infer_availability,
    london_today,
)
from fa.models.market_data import AvailabilitySource, FxSeries, PricePoint
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

BASE_CURRENCY = "CNY"

# 1 unit of <currency> expressed in CNY
FX_TICKERS = dict(FX_QUOTE_CONVENTIONS)

# Range strings the old callers pass; each maps to a lookback in calendar days.
_RANGE_DAYS = {
    "5d": 8,
    "1mo": 40,
    "3mo": 100,
    "6mo": 200,
    "1y": 370,
    "2y": 740,
    "5y": 1830,
    "10y": 3660,
    "ytd": 370,
    "max": 7300,
}

_FX_NOTE = ("bar stamp is 00:00 {tz}; the close time is not published, so "
            "available_at = next {tz} midnight + {lag} min (inferred)")


def supported_currencies() -> list[str]:
    return sorted(FX_TICKERS)


def _window(range_: str | None, start, end,
            today: date | None = None) -> tuple[date, date]:
    """Resolve the (start, end) window the old API expressed as a range string.

    ``end`` defaults to **today in London**, not the UTC date: between 23:00Z
    and midnight London is already on the next date, and a UTC default would
    ask for a window that is one day short.
    """
    stop = pd.Timestamp(end).date() if end is not None else (today or london_today())
    if start is not None:
        begin = pd.Timestamp(start).date()
    else:
        begin = stop - timedelta(days=_RANGE_DAYS.get(str(range_ or "1mo"), 40))
    return begin, stop


def fetch_fx_series(currency: str, range_: str = "1mo", start=None,
                    end=None) -> pd.Series | None:
    """Daily CNY-per-unit series for ``currency``; ``None`` when unavailable.

    The index holds the **London trading dates** (see module docstring).
    """
    code = str(currency or "").strip().upper()
    if code == BASE_CURRENCY:
        return None  # 由調用方視為匯率恆等於 1
    if code not in FX_TICKERS:
        log.warning("沒有 %s 的匯率資料來源", code)
        return None

    begin, stop = _window(range_, start, end)
    fetched = fetch_fx_bars(code, begin, stop)
    if fetched.error or not fetched.bars:
        log.warning("匯率 %s 下載失敗（%s）: %s", code, fetched.symbol,
                    fetched.error or "no-bars")
        return None

    series = pd.Series(
        [bar.rate for bar in fetched.bars],
        index=pd.DatetimeIndex([bar.rate_date for bar in fetched.bars]),
        name=code,
    ).sort_index()
    series = series[series > 0]
    return series if not series.empty else None


def fetch_fx_rates(currencies, range_: str = "1mo", start=None,
                   end=None) -> dict[str, pd.Series]:
    """Download every needed currency once (CNY itself is not downloaded)."""
    begin, stop = _window(range_, start, end)
    codes = sorted({str(c or "").strip().upper() for c in currencies or []})
    codes = [code for code in codes if code and code != BASE_CURRENCY]
    out: dict[str, pd.Series] = {}
    for code, fetched in fetch_fx_bars_many(codes, begin, stop).items():
        if fetched.error or not fetched.bars:
            log.warning("匯率 %s 下載失敗（%s）: %s", code, fetched.symbol,
                        fetched.error or "no-bars")
            continue
        series = pd.Series(
            [bar.rate for bar in fetched.bars],
            index=pd.DatetimeIndex([bar.rate_date for bar in fetched.bars]),
            name=code,
        ).sort_index()
        series = series[series > 0]
        if not series.empty:
            out[code] = series
    return out


def rate_on(series: pd.Series | None, day, max_stale_days: int = 7):
    """Rate on (or the last available before) ``day``.

    Returns ``(value, actual_date, stale_days)`` or ``(None, None, None)``.
    Staleness is reported instead of hidden: the caller decides whether the
    estimate may still use the value.
    """
    if series is None or len(series) == 0:
        return None, None, None
    target = pd.Timestamp(day).normalize()
    pos = series.index.searchsorted(target, side="right") - 1
    if pos < 0:
        return None, None, None
    actual = series.index[pos]
    stale = (target - actual).days
    if stale > max_stale_days:
        return None, actual, stale
    return float(series.iloc[pos]), actual, stale


def fx_series_map(rates) -> dict[str, FxSeries]:
    """Wrap raw FX pandas series into the V2 :class:`FxSeries` model.

    Kept for V2.2 callers; the bars carry no availability information here
    because a pandas series has already dropped it.  Use
    :func:`fx_series_map_from_fetch` when the provenance matters.
    """
    out: dict[str, FxSeries] = {}
    for currency, series in (rates or {}).items():
        if series is None or len(series) == 0:
            continue
        pairs = [
            (pd.Timestamp(index).date(), float(value))
            for index, value in series.items()
            if float(value) == float(value)
        ]
        if pairs:
            code = str(currency).upper()
            out[code] = FxSeries.from_pairs(code, pairs, source="yahoo")
    return out


def fx_series_map_from_fetch(fetches,
                             lag_minutes: int | None = None) -> dict[str, FxSeries]:
    """Build :class:`FxSeries` from provider results, keeping the time fields.

    Each bar keeps its raw Yahoo stamp, its London trading date and an
    ``available_at`` inferred per decision D-1 (end of the trading day + lag),
    marked ``AvailabilitySource.INFERRED``.  Currencies that failed to download
    are omitted; the caller sees the gap rather than a silent 1.0.
    """
    from fa.config import FX_AVAILABILITY_LAG_MINUTES, FX_TIMEZONE

    lag = FX_AVAILABILITY_LAG_MINUTES if lag_minutes is None else lag_minutes
    note = _FX_NOTE.format(tz=FX_TIMEZONE, lag=lag)
    fetched_at = datetime.now(timezone.utc)
    out: dict[str, FxSeries] = {}
    for code, fetch in (fetches or {}).items():
        if not isinstance(fetch, FxFetch) or fetch.error or not fetch.bars:
            continue
        points = [
            PricePoint(
                price_date=bar.rate_date,
                close=bar.rate,
                market="FX",
                currency=BASE_CURRENCY,
                market_timestamp=bar.market_timestamp,
                available_at=infer_availability(bar.rate_date, lag),
                availability_source=AvailabilitySource.INFERRED,
                availability_note=note,
                fetched_at=fetched_at,
                source=bar.source,
                available_price_type="fx_close",
                used_price_type="fx_close",
            )
            for bar in fetch.bars
        ]
        out[str(code).upper()] = FxSeries.from_bars(
            str(code).upper(), points, source="yahoo")
    return out
