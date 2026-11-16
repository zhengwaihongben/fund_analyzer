# -*- coding: utf-8 -*-
"""FX rates with CNY as the base currency.

Every overseas asset is converted with ``FX_t/FX_0 - 1`` where ``FX`` is the
value of one unit of the asset currency expressed in CNY.  The fund is
denominated in CNY, so the base currency is configurable but defaults to CNY.
"""
from __future__ import annotations

import pandas as pd

from fa.data.qdii_market import fetch_yahoo_chart
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

BASE_CURRENCY = "CNY"

# 1 unit of <currency> expressed in CNY
FX_TICKERS = {
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


def supported_currencies() -> list[str]:
    return sorted(FX_TICKERS)


def fetch_fx_series(currency: str, range_: str = "1mo") -> pd.Series | None:
    """Daily CNY-per-unit series for ``currency``; ``None`` when unavailable."""
    code = str(currency or "").strip().upper()
    if code == BASE_CURRENCY:
        return None  # 由調用方視為匯率恆等於 1
    ticker = FX_TICKERS.get(code)
    if not ticker:
        log.warning("沒有 %s 的匯率資料來源", code)
        return None

    hit = fetch_yahoo_chart(ticker, range_=range_)
    if hit is None:
        log.warning("匯率 %s 下載失敗（%s）", code, ticker)
        return None

    frame = hit["prices"]
    series = pd.Series(
        frame["Price"].astype(float).values,
        index=pd.DatetimeIndex(frame["Date"]),
        name=code,
    ).sort_index()
    series = series[series > 0]
    return series if not series.empty else None


def fetch_fx_rates(currencies, range_: str = "1mo") -> dict[str, pd.Series]:
    """Download every needed currency once (CNY itself is not downloaded)."""
    out: dict[str, pd.Series] = {}
    for currency in sorted({str(c or "").strip().upper() for c in currencies}):
        if not currency or currency in out:
            continue
        series = fetch_fx_series(currency, range_=range_)
        if series is not None:
            out[currency] = series
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
