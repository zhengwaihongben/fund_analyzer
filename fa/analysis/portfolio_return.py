# -*- coding: utf-8 -*-
"""Per-holding return maths for the QDII NAV estimator.

Pure functions only: dates and series go in, numbers come out, so the
arithmetic is unit-testable without touching the network.

For one holding ``i`` with weight ``w_i``:

``r_local = P_i(target) / P_i(base) - 1``
``r_fx    = FX_ccy→CNY(target) / FX_ccy→CNY(base) - 1``
``r_cny   = (1 + r_local) * (1 + r_fx) - 1``
``contribution_i = w_i * r_cny``

A holding whose price, currency or FX rate is unavailable is **excluded**
(status ``missing-*`` / ``stale-*``) instead of being treated as a 0% return;
the caller turns the excluded weight into the reported coverage ratio.

Both ends of the interval are checked for staleness: a price quoted on the base
date must be as close to the base NAV date as the target price is to the target
NAV date, otherwise the two returns would cover different intervals and the
estimate would silently compare apples with oranges.  An unknown currency is
*not* assumed to be CNY -- it is excluded as ``missing-currency``.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from fa.utils.calendar import to_date


# 價格資料落後目標日超過這個天數就不再採用（休市 1-3 天屬正常）
MAX_STALE_DAYS = 7

_CNY_ALIASES = ("CNY", "RMB")
_UNKNOWN_ALIASES = ("", "NAN", "NONE", "NULL", "UNKNOWN", "?", "-")


def _last_on_or_before(series: pd.Series, day) -> tuple[float | None, date | None]:
    """Last value at or before ``day`` plus the date it belongs to."""
    if series is None or len(series) == 0:
        return None, None
    index = pd.DatetimeIndex(series.index)
    target = pd.Timestamp(day).normalize()
    pos = index.searchsorted(target, side="right") - 1
    if pos < 0:
        return None, None
    value = float(series.iloc[pos])
    return value, to_date(index[pos])


def _price_series(prices: pd.DataFrame, column: str = "Price") -> pd.Series | None:
    if prices is None or prices.empty or column not in prices.columns:
        return None
    frame = prices[["Date", column]].dropna()
    if frame.empty:
        return None
    series = pd.Series(
        frame[column].astype(float).values,
        index=pd.DatetimeIndex(pd.to_datetime(frame["Date"])),
    ).sort_index()
    return series[series > 0]


def normalise_currency(currency) -> str:
    """Upper-case a currency code; empty/invalid markers become ``""``."""
    text = str(currency or "").strip().upper()
    if text in _UNKNOWN_ALIASES:
        return ""
    return text


def holding_return(prices: pd.DataFrame, fx_series: pd.Series | None,
                   currency: str | None, base_date, target_date,
                   max_stale_days: int = MAX_STALE_DAYS) -> dict:
    """Local / FX / CNY return of one holding between two valuation dates."""
    result = {
        "status": "ok",
        "local_return": None,
        "fx_return": None,
        "cny_return": None,
        "price_date": None,
        "base_price_date": None,
        "fx_date": None,
        "base_fx_date": None,
        "notes": [],
    }

    series = _price_series(prices)
    if series is None:
        result["status"] = "missing-price"
        return result

    p_target, d_target = _last_on_or_before(series, target_date)
    if p_target is None:
        result["status"] = "missing-target-price"
        return result
    p_base, d_base = _last_on_or_before(series, base_date)
    if p_base is None:
        result["status"] = "missing-base-price"
        return result

    result["price_date"] = d_target.isoformat()
    result["base_price_date"] = d_base.isoformat()

    stale_days = (to_date(target_date) - d_target).days
    if stale_days > max_stale_days:
        result["status"] = "stale-price"
        result["notes"].append(f"價格落後目標日 {stale_days} 天")
        return result

    stale_base = (to_date(base_date) - d_base).days
    if stale_base > max_stale_days:
        result["status"] = "stale-base-price"
        result["notes"].append(f"基準日價格落後 {stale_base} 天，與 NAV 比較區間不一致")
        return result

    currency = normalise_currency(currency)
    local = p_target / p_base - 1.0
    if currency in _CNY_ALIASES:
        fx_return = 0.0
    elif not currency:
        # 「未知貨幣」不等於人民幣：不能當成 0% 匯率貢獻默默算進去
        result["status"] = "missing-currency"
        result["notes"].append("未知計價貨幣，無法換算人民幣")
        return result
    else:
        fx_target, fx_d_target = _last_on_or_before(fx_series, target_date)
        if fx_target is None:
            result["status"] = "missing-fx"
            result["notes"].append(f"缺少 {currency}/CNY 匯率")
            return result
        fx_base, fx_d_base = _last_on_or_before(fx_series, base_date)
        if fx_base is None:
            result["status"] = "missing-fx"
            result["notes"].append("缺少基準日匯率")
            return result
        if (to_date(target_date) - fx_d_target).days > max_stale_days:
            result["status"] = "stale-fx"
            result["notes"].append("匯率資料過期")
            return result
        if (to_date(base_date) - fx_d_base).days > max_stale_days:
            result["status"] = "stale-base-fx"
            result["notes"].append("基準日匯率過期，與 NAV 比較區間不一致")
            return result
        fx_return = fx_target / fx_base - 1.0
        result["fx_date"] = fx_d_target.isoformat()
        result["base_fx_date"] = fx_d_base.isoformat()

    result["local_return"] = local
    result["fx_return"] = fx_return
    result["cny_return"] = (1.0 + local) * (1.0 + fx_return) - 1.0
    return result


def aggregate(rows: list[dict]) -> dict:
    """Weighted fund return, coverage ratio and attribution.

    ``rows`` carry ``weight`` (fraction), ``status``, ``cny_return``,
    ``local_return`` and ``fx_return``.  Rows whose status is not ``ok`` are
    excluded from both the return and the coverage sum -- they are never
    silently treated as 0%.

    The attribution is exact::

        r_cny          = r_local + r_fx + r_local * r_fx
        contribution   = w * r_cny
                       = w*r_local + w*r_fx + w*r_local*r_fx
                       = local + fx + interaction

    so ``local_contribution + fx_contribution + interaction_contribution``
    equals ``estimated_return`` (no missing cross term).
    """
    covered_weight = 0.0
    total_weight = 0.0
    fund_return = 0.0
    fx_contribution = 0.0
    local_contribution = 0.0
    interaction_contribution = 0.0
    included = excluded = 0

    for row in rows:
        weight = float(row.get("weight") or 0.0)
        total_weight += weight
        if row.get("status") != "ok" or row.get("cny_return") is None:
            excluded += 1
            continue
        included += 1
        local = float(row.get("local_return") or 0.0)
        fx = float(row.get("fx_return") or 0.0)
        covered_weight += weight
        fund_return += weight * float(row["cny_return"])
        fx_contribution += weight * fx
        local_contribution += weight * local
        interaction_contribution += weight * local * fx

    coverage = (covered_weight / total_weight) if total_weight > 0 else 0.0
    return {
        "estimated_return": fund_return,
        "coverage_ratio": covered_weight,
        "coverage_fraction": coverage,
        "covered_weight": covered_weight,
        "total_weight": total_weight,
        "uncovered_weight": max(0.0, 1.0 - covered_weight),
        "fx_contribution": fx_contribution,
        "local_contribution": local_contribution,
        "interaction_contribution": interaction_contribution,
        "holdings_included": included,
        "holdings_excluded": excluded,
    }
