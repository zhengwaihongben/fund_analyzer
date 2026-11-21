# -*- coding: utf-8 -*-
"""Price provider — Yahoo daily bars with the vendor's own timestamp kept.

Stage A boundary: this module downloads and converts.  It never opens SQLite and
never decides whether a bar should be stored; ``fa/data/price_pipeline.py`` owns
the cache decision, the validation and the persistence.

Why a new module instead of ``qdii_market.py``: that module normalises every
stamp to UTC midnight because the valuation engine only needs a date.  A PIT
foundation needs the opposite — the raw stamp is the evidence of *when* the
vendor observed the bar, and rewriting it would erase that evidence.  Both
readers now share the same Yahoo endpoint but keep their own conversion, which is
why the NAV estimator's behaviour is unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from fa.config import REQUEST_TIMEOUT
from fa.data.qdii_market import candidate_symbols, normalise_currency
from fa.utils.calendars import normalise_exchange

log = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

#: Quote-currency fields we accept; GBp is normalised by ``normalise_currency``.
_PRICE_TYPE_BOTH = "close+adj_close"
_PRICE_TYPE_CLOSE = "close"


@dataclass(frozen=True)
class PriceBar:
    """One daily observation, as the vendor reported it."""

    trading_date: date
    close: float
    market_timestamp: datetime
    adj_close: float | None = None
    currency: str = ""
    market: str = ""
    source: str = "yahoo"


@dataclass
class PriceFetch:
    """Result of one ticker download (never raises for data problems)."""

    ticker: str
    symbol: str = ""
    market: str = ""
    currency: str = ""
    bars: list[PriceBar] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    dropped: int = 0
    duplicates: int = 0


def bar_trading_date(stamp: datetime, exchange_timezone: str | None = None) -> date:
    """Exchange-local date of a bar stamp.

    Yahoo stamps a US daily bar at the opening bell (``14:30Z`` in EST, ``13:30Z``
    in EDT), so the UTC date and the session date happen to agree for US venues;
    they need not for a venue east of UTC.  The exchange timezone from ``meta`` is
    therefore authoritative, with UTC only as a last resort.
    """
    if stamp.tzinfo is None:
        raise ValueError("price bar stamp 必須帶時區")
    if not exchange_timezone:
        return stamp.astimezone(timezone.utc).date()
    try:
        zone = ZoneInfo(exchange_timezone)
    except Exception:  # noqa: BLE001 - 缺 tzdata 時退回 UTC，但不假裝精確
        log.debug("未知時區 %s，改用 UTC 判定交易日", exchange_timezone)
        return stamp.astimezone(timezone.utc).date()
    return stamp.astimezone(zone).date()


def _utc_stamp(value: int | float) -> datetime:
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _request_window(start: date, end: date) -> tuple[int, int]:
    """Unix window for the chart endpoint, padded by one day on each side.

    Equity bars are stamped at the session open, so a UTC-midnight boundary is
    already safe; the padding absorbs venues east of UTC and the odd late bar.
    """
    first = datetime.combine(start - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    last = datetime.combine(end + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc)
    return int(first.timestamp()), int(last.timestamp())


def _http_chart(symbol: str, start: date, end: date, timeout: float | None) -> tuple[dict | None, str | None]:
    period1, period2 = _request_window(start, end)
    params = {"interval": "1d", "period1": period1, "period2": period2, "events": "div,splits"}
    try:
        response = requests.get(
            CHART_URL.format(symbol=requests.utils.quote(symbol)),
            headers=HEADERS,
            params=params,
            timeout=timeout or REQUEST_TIMEOUT,
        )
    except requests.Timeout:
        return None, "timeout"
    except Exception as exc:  # noqa: BLE001 - 網路層一律降級
        return None, f"network-error: {exc}"
    if response.status_code != 200:
        return None, f"http-{response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return None, "invalid-json"
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        error = (payload.get("chart") or {}).get("error") or {}
        detail = error.get("description") or error.get("code") or "no-result"
        return None, f"no-result: {detail}"
    return results[0], None


def _parse_bars(result: dict, start: date, end: date) -> tuple[list[PriceBar], dict]:
    """Convert one ``chart.result[0]`` payload into bars inside the window."""
    meta = result.get("meta") or {}
    tz_name = meta.get("exchangeTimezoneName")
    market = normalise_exchange(meta.get("exchangeName"))
    currency = normalise_currency(meta.get("currency"), meta.get("symbol") or "") or ""
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    adj = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
    adj = adj if adj is not None and len(adj) == len(timestamps) else [None] * len(timestamps)

    bars: list[PriceBar] = []
    dropped = 0
    seen: dict[date, PriceBar] = {}
    duplicates = 0
    for index, stamp in enumerate(timestamps):
        close = closes[index] if index < len(closes) else None
        adj_close = adj[index] if index < len(adj) else None
        if close is None:
            dropped += 1
            continue
        try:
            close_value = float(close)
            adj_value = None if adj_close is None else float(adj_close)
        except (TypeError, ValueError):
            dropped += 1
            continue
        if close_value != close_value or close_value in (float("inf"), float("-inf")):
            dropped += 1
            continue
        if adj_value is not None and (adj_value != adj_value or adj_value in (float("inf"), float("-inf"))):
            adj_value = None
        moment = _utc_stamp(stamp)
        session = bar_trading_date(moment, tz_name)
        if session < start or session > end:
            continue
        bar = PriceBar(
            trading_date=session,
            close=close_value,
            adj_close=adj_value,
            market_timestamp=moment,
            currency=currency,
            market=market,
        )
        previous = seen.get(session)
        if previous is not None:
            duplicates += 1
            if previous.market_timestamp and bar.market_timestamp < previous.market_timestamp:
                continue
        seen[session] = bar

    bars = sorted(seen.values(), key=lambda item: item.trading_date)
    return bars, {
        "market": market,
        "currency": currency,
        "dropped": dropped,
        "duplicates": duplicates,
        "symbol": meta.get("symbol") or "",
    }


def fetch_price_bars(
    ticker: str,
    start: date,
    end: date,
    *,
    timeout: float | None = None,
) -> PriceFetch:
    """Download one ticker's daily bars for ``[start, end]``.

    Candidates come from ``qdii_market.candidate_symbols`` (existing overrides and
    exchange suffixes), so a fund ticker such as ``ROP SW`` resolves exactly as it
    does in the valuation engine.  Data problems are reported through
    :class:`PriceFetch`; only programming errors raise.
    """
    code = str(ticker or "").strip()
    if not code:
        return PriceFetch(ticker=code, error="missing-ticker")
    if end < start:
        return PriceFetch(ticker=code, error="invalid-window")
    candidates = candidate_symbols(code)
    if not candidates:
        return PriceFetch(ticker=code, error="missing-ticker")

    last_error = "no-result"
    for symbol in candidates[:4]:
        result, error = _http_chart(symbol, start, end, timeout)
        if error is not None or result is None:
            last_error = error or "no-result"
            # A transient/transport failure says nothing about which listing is
            # right; falling back to another venue (e.g. LLY -> LLY.SW) would
            # silently swap the currency and the price level.
            if last_error.startswith(("timeout", "network-error", "http-5")):
                return PriceFetch(ticker=code, error=last_error)
            continue
        bars, info = _parse_bars(result, start, end)
        if not bars:
            last_error = "no-bars"
            continue
        warnings: list[str] = []
        if info["dropped"]:
            warnings.append(f"dropped-bars: {info['dropped']}")
        fetch = PriceFetch(
            ticker=code,
            symbol=symbol,
            market=info["market"],
            currency=info["currency"],
            bars=bars,
            warnings=warnings,
            dropped=info["dropped"],
            duplicates=info["duplicates"],
        )
        return fetch
    return PriceFetch(ticker=code, error=last_error)


__all__ = [
    "PriceBar",
    "PriceFetch",
    "bar_trading_date",
    "fetch_price_bars",
]
