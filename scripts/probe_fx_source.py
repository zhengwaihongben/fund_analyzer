# -*- coding: utf-8 -*-
"""Stage 2.0 audit probe: what Yahoo actually returns for the FX pairs.

Read-only, network-only; it never touches the database.  The questions it
answers are the two the Stage 2 plan asks the audit to settle before any code:

1. **What is the timestamp of a daily FX bar?**  ``fa.data.qdii_market``
   normalises every bar to midnight UTC, which destroys the information needed
   to define ``market_timestamp`` / ``available_at`` honestly.  Here the raw
   epoch values are printed, together with the exchange timezone Yahoo reports
   and the regular trading period of the current session.
2. **What is the quote direction of each ``FX_TICKERS`` pair?**  The project
   convention is ``CNY per 1 unit of foreign currency``.  A vendor that quotes
   ``USD per 1 CNY`` for one pair and ``CNY per 1 USD`` for another cannot be
   detected from the ticker alone, so every pair is compared with an
   independent expected band.

Run with the project interpreter::

    python scripts/probe_fx_source.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fa.data.fx import FX_TICKERS  # noqa: E402

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = "Mozilla/5.0 (compatible; fund-analyzer-audit/2.3.1)"
TIMEOUT = 25

# Independent sanity bands (CNY per 1 foreign unit), wide enough to survive
# real market moves but narrow enough that a reciprocal quote cannot pass.
EXPECTED_BANDS = {
    "USD": (6.0, 8.5),
    "HKD": (0.75, 1.05),
    "CHF": (7.0, 10.0),
    "GBP": (7.5, 11.0),
    "EUR": (7.0, 9.5),
    "JPY": (0.03, 0.08),
    "CAD": (4.0, 6.5),
    "AUD": (3.5, 6.0),
    "SGD": (4.0, 6.5),
    "TWD": (0.15, 0.35),
}


def _iso(epoch: int | float | None, tz=None) -> str:
    if epoch is None:
        return "-"
    moment = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
    if tz is None:
        return moment.isoformat().replace("+00:00", "Z")
    return f"{moment.isoformat()} ({moment.astimezone(tz).isoformat()})"


def _fetch(symbol: str, range_: str = "5d", interval: str = "1d") -> dict:
    url = CHART_URL.format(symbol=symbol)
    query = f"?range={range_}&interval={interval}"
    request = urllib.request.Request(url + query, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8"))
    chart = payload["chart"]
    if chart.get("error"):
        raise RuntimeError(chart["error"])
    return chart["result"][0]


def _describe(currency: str, symbol: str) -> str:
    result = _fetch(symbol)
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    tz_name = meta.get("exchangeTimezoneName") or ""
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name) if tz_name else None
    except Exception:  # pragma: no cover - tz database missing
        tz = None

    lines = [f"== {currency} / {symbol} =="]
    lines.append(
        f"  meta: currency={meta.get('currency')!r} exchange={meta.get('exchangeName')!r} "
        f"type={meta.get('instrumentType')!r}"
    )
    lines.append(
        f"  tz: exchangeTimezoneName={tz_name!r} gmtoffset={meta.get('gmtoffset')} "
        f"exchangeDataDelayedBy={meta.get('exchangeDataDelayedBy')}"
    )
    lines.append(
        f"  regularMarketTime={_iso(meta.get('regularMarketTime'), tz)} "
        f"price={meta.get('regularMarketPrice')}"
    )
    period = ((meta.get("currentTradingPeriod") or {}).get("regular") or {})
    if period:
        lines.append(
            f"  currentTradingPeriod.regular: start={_iso(period.get('start'), tz)} "
            f"end={_iso(period.get('end'), tz)}"
        )
    lines.append(f"  bars={len(stamps)}")
    for stamp, close in list(zip(stamps, closes))[-4:]:
        lines.append(f"    {_iso(stamp, tz)} -> close={close}")

    if closes and closes[-1] is not None:
        value = float(closes[-1])
        low, high = EXPECTED_BANDS.get(currency, (float("-inf"), float("inf")))
        verdict = "CNY-per-unit OK" if low <= value <= high else "OUTSIDE BAND"
        reciprocal = 1.0 / value if value else float("inf")
        lines.append(
            f"  direction: close={value:.6f} expected={low}..{high} -> {verdict}"
            f" (reciprocal would be {reciprocal:.6f})"
        )
    return "\n".join(lines)


def _coverage(currency: str, symbol: str, range_: str = "1y") -> str:
    """Date completeness of a daily FX series: weekdays, weekends, gaps."""
    result = _fetch(symbol, range_=range_)
    stamps = result.get("timestamp") or []
    days = [datetime.fromtimestamp(float(s), tz=timezone.utc).date() for s in stamps]
    if not days:
        return f"== {currency} / {symbol} ({range_}) ==\n  no bars"
    weekdays = [0] * 7
    for day in days:
        weekdays[day.weekday()] += 1
    weekend = weekdays[5] + weekdays[6]
    gaps = [
        (left, right, (right - left).days)
        for left, right in zip(days, days[1:])
        if (right - left).days > 4
    ]
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    hist = ", ".join(f"{names[i]}={weekdays[i]}" for i in range(7))
    gaps_text = "; ".join(f"{a} -> {b} ({n}d)" for a, b, n in gaps) or "none"
    return (
        f"== {currency} / {symbol} ({range_}) ==\n"
        f"  bars={len(days)} first={days[0]} last={days[-1]} weekends={weekend}\n"
        f"  {hist}\n  gaps>4d: {gaps_text}"
    )


def _timeline(currency: str, symbol: str) -> str:
    """Hourly + daily stamps for the last sessions (session-boundary evidence)."""
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    daily = _fetch(symbol, range_="1mo")
    daily_days = [
        datetime.fromtimestamp(float(s), tz=timezone.utc)
        for s in (daily.get("timestamp") or [])
    ]
    hourly = _fetch(symbol, range_="3d", interval="60m")
    hourly_stamps = [
        datetime.fromtimestamp(float(s), tz=timezone.utc)
        for s in (hourly.get("timestamp") or [])
    ]
    lines = [f"== {currency} / {symbol}: timeline =="]
    lines.append("  daily bars (UTC):")
    for moment in daily_days[-14:]:
        lines.append(f"    {moment.strftime('%Y-%m-%d %a %H:%M')}")
    lines.append(f"  hourly bars (UTC): {len(hourly_stamps)}")
    for moment in hourly_stamps:
        lines.append(f"    {moment.strftime('%Y-%m-%d %a %H:%M')}")
    if len(hourly_stamps) >= 2:
        deltas = [
            (right - left).total_seconds() / 3600.0
            for left, right in zip(hourly_stamps, hourly_stamps[1:])
        ]
        lines.append(f"  hourly spacing (h): min={min(deltas)} max={max(deltas)}")
    if daily_days:
        lines.append(f"  last daily bar weekday: {names[daily_days[-1].weekday()]}")
    return "\n".join(lines)


def _weekends(currency: str, symbol: str) -> str:
    """Show every weekend-stamped daily bar and its neighbours, with closes."""
    result = _fetch(symbol, range_="1y")
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    rows = [
        (datetime.fromtimestamp(float(s), tz=timezone.utc), close)
        for s, close in zip(stamps, closes)
    ]
    lines = [f"== {currency} / {symbol}: weekend-stamped bars =="]
    hit = 0
    for index, (moment, close) in enumerate(rows):
        if moment.weekday() < 5:
            continue
        hit += 1
        window = rows[max(0, index - 2): index + 3]
        rendered = " | ".join(
            f"{m.strftime('%Y-%m-%d %a')}={c}" for m, c in window
        )
        lines.append(f"  #{index} {moment.strftime('%Y-%m-%d %a %H:%M')} close={close}\n    {rendered}")
    lines.append(f"  weekend bars={hit} of {len(rows)}")
    return "\n".join(lines)


def main() -> int:
    if "--weekends" in sys.argv:
        for currency in ("USD", "HKD", "CHF"):
            symbol = FX_TICKERS[currency]
            try:
                print(_weekends(currency, symbol))
            except Exception as exc:  # noqa: BLE001
                print(f"== {currency} / {symbol} ==\n  FAILED: {type(exc).__name__}: {exc}")
            print()
        return 0

    if "--timeline" in sys.argv:
        for currency in ("USD", "HKD"):
            symbol = FX_TICKERS[currency]
            try:
                print(_timeline(currency, symbol))
            except Exception as exc:  # noqa: BLE001
                print(f"== {currency} / {symbol} ==\n  FAILED: {type(exc).__name__}: {exc}")
            print()
        return 0

    if "--coverage" in sys.argv:
        failures = 0
        for currency, symbol in FX_TICKERS.items():
            try:
                print(_coverage(currency, symbol))
            except Exception as exc:  # noqa: BLE001 - audit probe reports, never raises
                failures += 1
                print(f"== {currency} / {symbol} ==\n  FAILED: {type(exc).__name__}: {exc}")
            print()
        print(f"pairs={len(FX_TICKERS)} failures={failures}")
        return 0

    failures = 0
    for currency, symbol in FX_TICKERS.items():
        try:
            print(_describe(currency, symbol))
        except Exception as exc:  # noqa: BLE001 - audit probe reports, never raises
            failures += 1
            print(f"== {currency} / {symbol} ==\n  FAILED: {type(exc).__name__}: {exc}")
        print()
    print(f"pairs={len(FX_TICKERS)} failures={failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
