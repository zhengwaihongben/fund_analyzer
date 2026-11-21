# -*- coding: utf-8 -*-
"""FX provider: fetch raw Yahoo FX bars and convert them into trading dates.

Stage 2.3 boundary: this module **only** fetches and converts.  It never
touches SQLite, never retries, never decides when a rate becomes usable — the
pipeline (Stage 2.4) owns persistence and availability bookkeeping.

Two facts measured in Stage 2.0 drive the conversion:

* Yahoo stamps each FX daily bar at ``00:00 Europe/London``, not at a market
  close.  In BST that instant is ``23:00Z`` of the *previous* UTC day, so the
  trading date must be taken in London (``bar_trading_date``), never in UTC.
* The running day's bar is live before the day is over, so ``available_at`` is
  inferred as the end of the trading day plus a lag (``infer_availability``),
  which keeps today's bar out of point-in-time queries (decision D-1).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from fa.config import FX_AVAILABILITY_LAG_MINUTES, FX_TIMEZONE, REQUEST_TIMEOUT
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept": "application/json,text/plain,*/*",
}

# Audited symbols.  Every one of them quotes **CNY per 1 unit of the foreign
# currency**; the irregular ``USD -> CNY=X`` was verified against the HKD peg.
# A symbol that is not in this table is refused, never assumed.
FX_QUOTE_CONVENTIONS: dict[str, str] = {
    "USD": "CNY=X",
    "HKD": "HKDCNY=X",
    "CHF": "CHFCNY=X",
    "GBP": "GBPCNY=X",
    "EUR": "EURCNY=X",
    "JPY": "JPYCNY=X",
    "CAD": "CADCNY=X",
    "AUD": "AUDCNY=X",
    "SGD": "SGDCNY=X",
    "TWD": "TWDCNY=X",
}

# Wide enough for real moves, narrow enough that a reciprocal cannot pass:
# 1/6.66 = 0.15 is far outside the USD band, 1/0.849 = 1.18 is outside HKD.
FX_SANITY_BANDS: dict[str, tuple[float, float]] = {
    "USD": (5.0, 9.5),
    "HKD": (0.65, 1.15),
    "CHF": (6.0, 11.0),
    "GBP": (7.0, 12.0),
    "EUR": (6.5, 10.0),
    "JPY": (0.02, 0.09),
    "CAD": (3.5, 7.0),
    "AUD": (3.0, 6.5),
    "SGD": (4.0, 7.0),
    "TWD": (0.14, 0.40),
}


@dataclass(frozen=True)
class FxBar:
    """One daily FX observation, converted but not yet persisted."""

    rate_date: date
    rate: float
    market_timestamp: datetime      # aware, UTC (Yahoo bar stamp)
    source: str = "yahoo"


@dataclass
class FxFetch:
    """Result of one currency download.

    ``error`` describes the *series* (HTTP failure, unsupported currency,
    direction suspect).  ``warnings`` and ``duplicates`` are additive
    diagnostics for the pipeline; they never change ``bars``.
    """

    currency: str
    symbol: str
    bars: list[FxBar] = field(default_factory=list)
    currency_raw: str | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    duplicates: int = 0


# --- London time -------------------------------------------------------------

_LONDON = None
_LONDON_FAILED = False


def _london_zone():
    """``ZoneInfo("Europe/London")`` or ``None`` when the tz database is absent."""
    global _LONDON, _LONDON_FAILED
    if _LONDON is not None or _LONDON_FAILED:
        return _LONDON
    try:
        _LONDON = ZoneInfo(FX_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):  # pragma: no cover - Windows w/o tzdata
        _LONDON_FAILED = True
        _LONDON = None
        log.warning("系統沒有 %s 的時區資料，改用 EU 夏令時間規則", FX_TIMEZONE)
    return _LONDON


def london_offset(moment: datetime) -> timedelta:
    """UTC offset of Europe/London at ``moment`` (BST from the last Sunday of
    March 01:00 UTC to the last Sunday of October 01:00 UTC).

    ``ZoneInfo`` is preferred; the rule is only used when the tz database is
    missing, and a test asserts both agree for every day 2025–2030.
    """
    zone = _london_zone()
    if zone is not None:
        return moment.astimezone(zone).utcoffset() or timedelta(0)
    return _eu_rule_offset(moment)


def _eu_rule_offset(moment: datetime) -> timedelta:
    utc = moment.astimezone(timezone.utc)
    year = utc.year
    start = _last_sunday(year, 3).replace(hour=1, tzinfo=timezone.utc)
    stop = _last_sunday(year, 10).replace(hour=1, tzinfo=timezone.utc)
    return timedelta(hours=1) if start <= utc < stop else timedelta(0)


def _last_sunday(year: int, month: int) -> datetime:
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def bar_trading_date(stamp: datetime, tz: str = FX_TIMEZONE) -> date:
    """Trading date of a bar stamp, taken in London.

    A naive stamp is rejected: the whole point is that the timezone determines
    the date.
    """
    if not isinstance(stamp, datetime):
        raise TypeError(f"bar_trading_date 需要 datetime，收到 {type(stamp).__name__}")
    if stamp.tzinfo is None:
        raise ValueError("bar_trading_date 需要帶時區的 datetime")
    zone = _london_zone() if tz == FX_TIMEZONE else _zone_for(tz)
    if zone is not None:
        return stamp.astimezone(zone).date()
    return (stamp.astimezone(timezone.utc) + _eu_rule_offset(stamp)).date()


def _zone_for(tz: str):
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _london_midnight_utc(day: date) -> datetime:
    """The UTC instant at which ``day`` starts in London.

    The fallback must use the offset that applies **at London midnight**, not
    the offset at noon: on the two DST switch days the two differ by an hour
    (2026-03-29 00:00 London = 00:00Z, not 23:00Z; 2026-10-25 00:00 London =
    2026-10-24 23:00Z, not 00:00Z).  It therefore solves for a fixed point
    ``midnight_utc = 00:00Z - offset(midnight_utc)``.
    """
    zone = _london_zone()
    if zone is not None:
        local = datetime.combine(day, time(0), tzinfo=zone)
        return local.astimezone(timezone.utc)
    base = datetime.combine(day, time(0), tzinfo=timezone.utc)
    offset = _eu_rule_offset(base)
    candidate = base - offset
    settled = _eu_rule_offset(candidate)
    if settled != offset:
        candidate = base - settled
    return candidate


def london_today() -> date:
    """Today's date in London (never the UTC date).

    Between 23:00Z and midnight London is already on the next date, so a UTC
    date would ask for a window one day short whenever the caller builds its
    default range just after the UTC evening.
    """
    now = datetime.now(timezone.utc)
    zone = _london_zone()
    if zone is not None:
        return now.astimezone(zone).date()
    return (now + _eu_rule_offset(now)).date()


def infer_availability(rate_date: date,
                       lag_minutes: int = FX_AVAILABILITY_LAG_MINUTES) -> datetime:
    """When a bar can first be used: the end of its London trading day + lag.

    The value is *inferred*, never observed: Yahoo does not publish a
    publication timestamp, so the pipeline must record this provenance.
    """
    if not isinstance(rate_date, date) or isinstance(rate_date, datetime):
        raise TypeError("infer_availability 需要 date（交易日期）")
    return _london_midnight_utc(rate_date + timedelta(days=1)) + timedelta(
        minutes=lag_minutes)


# --- download ----------------------------------------------------------------


def _http_chart(symbol: str, start: date, end: date,
                timeout: float | None) -> tuple[dict | None, str | None]:
    """One GET, no retries; ``(payload, error)``."""
    url = CHART_URL.format(symbol=requests.utils.quote(symbol))
    # The window is expressed in *London* trading dates: in BST the bar of day D
    # is stamped 23:00Z of D-1, so a UTC-midnight period1 silently dropped the
    # first bar of the window (and period2 cut the last one off).
    params = {
        "interval": "1d",
        "period1": int(_london_midnight_utc(start).timestamp()),
        # +1 day: period2 is exclusive, and the bar belongs to a London date.
        "period2": int(_london_midnight_utc(end + timedelta(days=1)).timestamp()),
    }
    try:
        response = requests.get(url, headers=HEADERS, params=params,
                                timeout=REQUEST_TIMEOUT if timeout is None else timeout)
    except requests.Timeout:
        return None, "timeout"
    except requests.RequestException as exc:
        return None, f"network-error: {exc}"
    except Exception as exc:  # noqa: BLE001 - 網路層一律回報，不讓例外穿出
        return None, f"network-error: {exc}"

    if response.status_code != 200:
        return None, f"http-{response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return None, "invalid-json"
    return payload, None


def _parse_bars(result: dict, start: date, end: date,
                currency: str) -> tuple[list[FxBar], int, list[str]]:
    """Convert one ``chart.result[0]`` payload into validated bars."""
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    bars: list[FxBar] = []
    warnings: list[str] = []
    dropped = 0

    if len(closes) != len(timestamps):
        warnings.append("close-timestamp-mismatch")

    for index, stamp in enumerate(timestamps):
        close = closes[index] if index < len(closes) else None
        try:
            value = float(close)
            moment = datetime.fromtimestamp(float(stamp), tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            dropped += 1
            continue
        if not math.isfinite(value) or value <= 0:
            dropped += 1
            continue
        day = bar_trading_date(moment)
        if day < start or day > end:
            continue
        bars.append(FxBar(rate_date=day, rate=value, market_timestamp=moment))

    if dropped:
        warnings.append(f"dropped-bars: {dropped}")

    bars.sort(key=lambda bar: bar.rate_date)
    duplicates = 0
    deduped: list[FxBar] = []
    for bar in bars:
        if deduped and deduped[-1].rate_date == bar.rate_date:
            duplicates += 1
            deduped[-1] = bar          # keep the last observation of the day
        else:
            deduped.append(bar)
    if duplicates:
        warnings.append(f"duplicate-dates: {duplicates}")
    return deduped, duplicates, warnings


def _band_error(currency: str, bars: list[FxBar]) -> str | None:
    low, high = FX_SANITY_BANDS.get(currency, (0.0, math.inf))
    outside = [bar for bar in bars if not low <= bar.rate <= high]
    if not outside:
        return None
    sample = outside[-1]
    return (f"quote-direction-suspect: {len(outside)} bar(s) outside "
            f"[{low}, {high}], last {sample.rate_date} {sample.rate}")


def fetch_fx_bars(currency: str, start: date, end: date,
                  *, timeout: float | None = None) -> FxFetch:
    """Download one currency's CNY-per-unit bars for ``[start, end]``."""
    code = str(currency or "").strip().upper()
    if not code:
        return FxFetch(currency=code, symbol="", error="missing-currency")
    if not isinstance(start, date) or not isinstance(end, date):
        raise TypeError("fetch_fx_bars 需要 date 型別的 start/end")
    if start > end:
        raise ValueError("fetch_fx_bars 的 start 不可晚於 end")
    if code == "CNY":
        return FxFetch(currency="CNY", symbol="")          # explicit no-op
    symbol = FX_QUOTE_CONVENTIONS.get(code)
    if symbol is None:
        return FxFetch(currency=code, symbol="", error="unsupported-currency")

    payload, error = _http_chart(symbol, start, end, timeout)
    if error is not None:
        log.warning("匯率下載失敗 %s (%s): %s", code, symbol, error)
        return FxFetch(currency=code, symbol=symbol, error=error)

    results = ((payload or {}).get("chart") or {}).get("result") or []
    if not results:
        detail = ((payload or {}).get("chart") or {}).get("error") or {}
        return FxFetch(currency=code, symbol=symbol,
                       error=f"no-result: {detail.get('description') or 'empty'}")

    result = results[0]
    meta = result.get("meta") or {}
    currency_raw = meta.get("currency")
    if str(currency_raw or "").strip().upper() != "CNY":
        return FxFetch(currency=code, symbol=symbol, currency_raw=currency_raw,
                       error=f"non-cny-quote: {currency_raw!r}")

    bars, duplicates, warnings = _parse_bars(result, start, end, code)
    if not bars:
        return FxFetch(currency=code, symbol=symbol, currency_raw=currency_raw,
                       error="no-bars", duplicates=duplicates)

    return FxFetch(currency=code, symbol=symbol, bars=bars,
                   currency_raw=currency_raw, error=_band_error(code, bars),
                   warnings=warnings, duplicates=duplicates)


def fetch_fx_bars_many(currencies: Iterable[str], start: date, end: date,
                       *, timeout: float | None = None) -> dict[str, FxFetch]:
    """Fetch several currencies once each; CNY is skipped (no 1.0 series)."""
    wanted = []
    for currency in currencies or []:
        code = str(currency or "").strip().upper()
        if code and code != "CNY" and code not in wanted:
            wanted.append(code)
    out: dict[str, FxFetch] = {}
    for code in wanted:
        out[code] = fetch_fx_bars(code, start, end, timeout=timeout)
    return out
