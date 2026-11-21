# -*- coding: utf-8 -*-
"""Market prices for QDII holdings.

QDII funds hold securities on several exchanges (US, London, SIX, HK, ...),
so a single currency cannot be assumed.  For every disclosed holding we

* resolve the Yahoo symbol (with fallbacks, because the fund data source and
  Yahoo disagree on suffixes -- Roche's ``ROPSW`` is ``ROP.SW`` on Yahoo and
  ``ROG.SW`` no longer resolves),
* read the **currency from the response metadata** instead of guessing it
  from the ticker, and
* keep ``AdjClose`` when the API provides it (splits/dividends).

The direct chart endpoint is used rather than ``yfinance`` because it returns
the same data in one request and does not spawn a JS engine; the existing
``yfinance`` subprocess bridge in :mod:`fa.data.market` stays available for
other callers.
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd
import requests

from fa.config import REQUEST_TIMEOUT
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept": "application/json,text/plain,*/*",
}

# 基金資料源與 Yahoo 的寫法不一致時的人工對照（只放確實必要者）
SYMBOL_OVERRIDES = {
    "ROPSW": "ROP.SW",   # Roche：Yahoo 的 ROG.SW 已下線，ROP.SW 可用
    "ROP SW": "ROP.SW",
    "ROG": "ROP.SW",
    "NOVN": "NOVN.SW",
    "AZN": "AZN.L",      # 倫敦掛牌（GBp 計價，見 _normalise_currency）
}

# 裸代碼解析失敗時依序嘗試的交易所後綴
FALLBACK_SUFFIXES = (".SW", ".L", ".DE", ".PA", ".AS", ".T", ".HK", ".TO", ".AX")

# 僅在 Yahoo 沒回報貨幣時使用的最後手段
CURRENCY_FOR_SUFFIX = {
    ".SW": "CHF", ".L": "GBP", ".DE": "EUR", ".PA": "EUR", ".AS": "EUR",
    ".T": "JPY", ".HK": "HKD", ".TO": "CAD", ".AX": "AUD",
}

# 以「分」報價的市場：GBp / GBX 都是便士，與 GBP 差 100 倍，
# 但我們只用價格比率（P1/P0），倍數會自動抵消，因此統一歸為 GBP。
_PENCE_LABELS = {"GBp", "GBX", "GBX."}


def normalise_currency(raw: str | None, symbol: str = "") -> str | None:
    """Map a Yahoo currency label onto an ISO code we have an FX pair for."""
    if not raw:
        for suffix, cur in CURRENCY_FOR_SUFFIX.items():
            if symbol.upper().endswith(suffix):
                return cur
        return None
    label = str(raw).strip()
    if label in _PENCE_LABELS:
        return "GBP"
    return label.upper()


def candidate_symbols(code: str) -> list[str]:
    """Symbols to try, most likely first."""
    raw = str(code or "").strip().upper()
    if not raw:
        return []
    out: list[str] = []

    def add(sym: str) -> None:
        if sym and sym not in out:
            out.append(sym)

    add(SYMBOL_OVERRIDES.get(raw, ""))
    compact = raw.replace(" ", "")
    add(compact)
    for suffix in FALLBACK_SUFFIXES:
        add(compact + suffix)
    return out


def _frame_from_chart(result: dict) -> pd.DataFrame | None:
    """Turn one ``chart.result[0]`` payload into a Date/Close/AdjClose frame."""
    timestamps = result.get("timestamp") or []
    if not timestamps:
        return None
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    adj = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")

    frame = pd.DataFrame({
        "Date": pd.DatetimeIndex(pd.to_datetime(timestamps, unit="s", utc=True))
                  .tz_convert("UTC").tz_localize(None).normalize(),
        "Close": closes if len(closes) == len(timestamps) else [None] * len(timestamps),
        "AdjClose": adj if adj is not None and len(adj) == len(timestamps)
        else [None] * len(timestamps),
    })
    frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
    frame["AdjClose"] = pd.to_numeric(frame["AdjClose"], errors="coerce")
    frame["Price"] = frame["AdjClose"].fillna(frame["Close"])
    frame = frame.dropna(subset=["Price"]).sort_values("Date").reset_index(drop=True)
    if frame.empty:
        return None
    return frame


def fetch_yahoo_chart(symbol: str, range_: str = "1mo",
                      interval: str = "1d", start=None, end=None) -> dict | None:
    """Download one symbol's daily history; ``None`` when unavailable.

    ``start``/``end`` (dates) fetch an explicit window instead of a trailing
    ``range_``: a valuation for July cannot be served by "the last month" in
    November, and the caller must not silently get the wrong window.
    """
    url = CHART_URL.format(symbol=requests.utils.quote(symbol))
    params: dict[str, object] = {"interval": interval}
    if start is not None and end is not None:
        params["period1"] = int(pd.Timestamp(start).timestamp())
        params["period2"] = int((pd.Timestamp(end) + pd.Timedelta(days=1)).timestamp())
    else:
        params["range"] = range_
    try:
        resp = requests.get(url, headers=_BROWSER_HEADERS,
                            params=params,
                            timeout=REQUEST_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - 網路層一律降級處理
        log.debug("行情下載失敗 %s: %s", symbol, exc)
        return None

    if resp.status_code != 200:
        log.debug("行情下載失敗 %s: HTTP %s", symbol, resp.status_code)
        return None

    try:
        payload = resp.json()
    except ValueError:
        log.debug("行情回傳非 JSON: %s", symbol)
        return None

    chart = payload.get("chart") or {}
    results = chart.get("result") or []
    if not results:
        return None

    result = results[0]
    frame = _frame_from_chart(result)
    if frame is None:
        return None

    meta = result.get("meta") or {}
    currency_raw = meta.get("currency")
    return {
        "symbol": meta.get("symbol") or symbol,
        "currency_raw": currency_raw,
        "currency": normalise_currency(currency_raw, meta.get("symbol") or symbol),
        "exchange": meta.get("exchangeName"),
        "prices": frame,
    }


def fetch_holding_price(code: str, range_: str = "1mo", start=None,
                        end=None) -> dict:
    """Resolve and download one holding's price history.

    Returns ``{"status": "ok"|"missing", "symbol", "currency", "prices",
    "tried", "error"}``.  A holding whose price cannot be found is reported
    as ``missing`` -- it is never silently treated as a 0% return.
    """
    tried: list[str] = []
    for symbol in candidate_symbols(code):
        tried.append(symbol)
        hit = fetch_yahoo_chart(symbol, range_=range_, start=start, end=end)
        if hit is not None:
            return {"status": "ok", **hit, "tried": tried, "error": None}
    return {
        "status": "missing",
        "symbol": None,
        "currency": None,
        "currency_raw": None,
        "exchange": None,
        "prices": None,
        "tried": tried,
        "error": "no symbol resolved",
    }


def fetch_holding_prices(codes: Iterable[str], range_: str = "1mo", start=None,
                         end=None) -> dict:
    """Download several holdings; keys are the original holding codes."""
    out: dict[str, dict] = {}
    for code in codes:
        key = str(code or "").strip()
        if not key or key in out:
            continue
        out[key] = fetch_holding_price(key, range_=range_, start=start, end=end)
    return out
