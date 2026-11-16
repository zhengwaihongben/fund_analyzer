# -*- coding: utf-8 -*-
"""QDII NAV estimator (V1: weighted holdings model).

For a QDII fund the official NAV lags the overseas close by one trading day,
so at day ``T`` we estimate the NAV of ``T-1`` from the moves of the disclosed
portfolio plus the currency moves::

    NAV_T-1 = NAV_base * (1 + Σ w_i * r_i,CNY)

Only holdings with a usable price **and** FX rate enter the sum; everything
else is reported as uncovered (``coverage_ratio``).  The estimated NAV is
never written over the official NAV -- it goes to
``<code>_qdii_estimate.json`` (latest) and ``<code>_qdii_estimates.csv``
(history, later reconciled with the official NAV by
:mod:`fa.analysis.estimation_backtest`).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from fa.analysis.estimation_backtest import backfill_history, read_history, write_history
from fa.analysis.portfolio_return import aggregate, holding_return
from fa.analysis.valuation_dates import resolve_valuation_dates
from fa.data.fx import fetch_fx_rates
from fa.data.nav import fetch_nav
from fa.data.qdii_holdings import fetch_qdii_holdings, select_point_in_time
from fa.data.qdii_market import fetch_holding_prices
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

ESTIMATE_SUFFIX = "_qdii_estimate.json"
HISTORY_SUFFIX = "_qdii_estimates.csv"
INPUTS_SUFFIX = "_qdii_estimate_inputs.json"
MARKET_LOOKBACK = "1mo"
STALE_HOLDINGS_DAYS = 150


def load_nav_frame(fund_code: str, save_dir: Path) -> pd.DataFrame:
    """Official NAV series; falls back to a fresh download when missing."""
    path = Path(save_dir) / f"{fund_code}_daily_nav.csv"
    if path.exists():
        try:
            frame = pd.read_csv(path)
            frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
            frame["NAV"] = pd.to_numeric(frame["NAV"], errors="coerce")
            frame = frame.dropna(subset=["Date", "NAV"]).sort_values("Date")
            if not frame.empty:
                return frame[["Date", "NAV"]].reset_index(drop=True)
        except Exception as exc:  # noqa: BLE001 - 壞檔就重抓
            log.warning("淨值緩存不可讀（%s），重新下載", exc)

    frame = fetch_nav(fund_code, Path(save_dir))
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["Date", "NAV"])
    frame = frame.copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame["NAV"] = pd.to_numeric(frame["NAV"], errors="coerce")
    return frame.dropna(subset=["Date", "NAV"]).sort_values("Date").reset_index(drop=True)


def _nav_on(frame: pd.DataFrame, day) -> float | None:
    if frame.empty:
        return None
    target = pd.Timestamp(day).normalize()
    subset = frame[frame["Date"] <= target]
    if subset.empty:
        return None
    return float(subset.iloc[-1]["NAV"])


def estimate_qdii_nav(fund_code: str, save_dir: Path, cfg: dict | None = None,
                      as_of: datetime | None = None, target_nav_date=None,
                      use_cache: bool = True, write: bool = True) -> dict:
    """Estimate the latest unpublished fund NAV.  Never raises on data gaps."""
    cfg = cfg or {}
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(as_of, str):
        try:
            as_of = datetime.fromisoformat(as_of)
        except ValueError:
            log.warning("無法解析估算時間 %r，改用現在時間", as_of)
            as_of = None
    computed_at = (as_of or datetime.now()).isoformat(timespec="seconds")

    nav_df = load_nav_frame(fund_code, save_dir)
    if nav_df.empty:
        return {
            "status": "insufficient-data",
            "fund_code": fund_code,
            "computed_at": computed_at,
            "message": "沒有官方淨值，無法估算",
            "holdings": [],
        }

    dates = resolve_valuation_dates(
        nav_df["Date"], as_of=as_of, target_nav_date=target_nav_date)
    base_nav = _nav_on(nav_df, dates["base_nav_date"]) if dates.get("base_nav_date") else None
    if dates["status"] != "ok" or base_nav is None:
        return {
            "status": dates["status"] if dates["status"] != "ok" else "insufficient-data",
            "fund_code": fund_code,
            "computed_at": computed_at,
            "message": dates.get("note") or "缺少估算所需的官方淨值",
            **{k: dates.get(k) for k in ("trade_date", "target_nav_date", "base_nav_date")},
            "nav_lag_days": dates.get("nav_lag_days"),
            "holdings": [],
        }

    target_date = dates["target_nav_date"]
    base_date = dates["base_nav_date"]

    # 只使用「目標日當天已經公開」的季度持倉，否則就是看得到未來的回測
    portfolio = fetch_qdii_holdings(fund_code, save_dir, use_cache=use_cache,
                                    keep_all=True)
    selection = select_point_in_time(portfolio.get("holdings"), target_date)
    if selection is None:
        # 手上的快照可能比目標日新（或目標日很早）：改抓歷史季報再試一次
        refreshed = fetch_qdii_holdings(fund_code, save_dir, use_cache=False,
                                        keep_all=True)
        selection = select_point_in_time(refreshed.get("holdings"), target_date)
        if selection is not None:
            portfolio = refreshed

    if selection is None or selection["holdings"].empty:
        available = portfolio.get("quarters") or []
        detail = f"（現有季度：{', '.join(available)}）" if available else ""
        return {
            "status": "insufficient-data",
            "fund_code": fund_code,
            "computed_at": computed_at,
            "message": f"目標日 {target_date} 之前尚無已披露的 QDII 持倉{detail}",
            **{k: dates.get(k) for k in ("trade_date", "target_nav_date",
                                         "base_nav_date", "timezone", "cutoff",
                                         "as_of_beijing")},
            "base_nav": base_nav,
            "look_ahead_safe": True,
            "quarters_available": available,
            "holdings": [],
        }
    holdings = selection["holdings"]

    tickers = [str(t) for t in holdings["ticker"].tolist() if str(t).strip()]
    price_hits = fetch_holding_prices(tickers, range_=MARKET_LOOKBACK)

    currencies: set[str] = set()
    for item in holdings.to_dict("records"):
        hit = price_hits.get(str(item["ticker"])) or {}
        currency = hit.get("currency") or item.get("currency_hint")
        if currency:
            currencies.add(str(currency).upper())
    fx_rates = fetch_fx_rates(currencies, range_=MARKET_LOOKBACK)

    rows: list[dict] = []
    for row in holdings.to_dict("records"):
        ticker = str(row["ticker"])
        hit = price_hits.get(ticker) or {}
        currency = hit.get("currency") or row.get("currency_hint")
        detail = holding_return(
            hit.get("prices"),
            fx_rates.get(str(currency or "").upper()),
            currency,
            base_date,
            target_date,
        )
        rows.append({
            "ticker": ticker,
            "name": row.get("name"),
            "weight": float(row.get("weight") or 0.0),
            "symbol": hit.get("symbol"),
            "currency": currency,
            "market": hit.get("exchange") or row.get("market"),
            "status": detail["status"],
            "local_return": detail["local_return"],
            "fx_return": detail["fx_return"],
            "cny_return": detail["cny_return"],
            "contribution": (float(row.get("weight") or 0.0) * detail["cny_return"]
                             if detail["cny_return"] is not None else None),
            "price_date": detail["price_date"],
            "base_price_date": detail["base_price_date"],
            "fx_date": detail["fx_date"],
            "base_fx_date": detail["base_fx_date"],
            "notes": detail["notes"],
        })

    summary = aggregate(rows)
    estimated_return = summary["estimated_return"]
    estimated_nav = base_nav * (1.0 + estimated_return)
    market_dates = [r["price_date"] for r in rows if r.get("price_date")]
    market_data_date = max(market_dates) if market_dates else None

    excluded = [r for r in rows if r["status"] != "ok"]
    uncovered = summary["uncovered_weight"]
    warnings_list: list[str] = []
    if excluded:
        warnings_list.append(f"{len(excluded)} 隻持倉缺少可用價格、匯率或幣種，已排除在覆蓋率之外")
    if uncovered > 0.001:
        warnings_list.append(
            f"未覆蓋 {uncovered * 100:.1f}% 的資產按 0% 回報處理（未知，非已知事實）")
    if summary["covered_weight"] <= 0:
        status = "insufficient-data"
        warnings_list.append("沒有任何持倉可估值")
    elif uncovered > 0.001 or excluded:
        # 覆蓋率不到 100% 時不能宣稱「已完整估算」
        status = "partial"
    else:
        status = "estimated"

    if selection.get("holding_date"):
        try:
            age = (pd.Timestamp(target_date) - pd.Timestamp(selection["holding_date"])).days
            if age > STALE_HOLDINGS_DAYS:
                warnings_list.append(f"持倉披露已 {age} 天未更新，權重可能已偏離實際")
        except (TypeError, ValueError):
            pass

    record = {
        "status": status,
        "fund_code": fund_code,
        "fund_name": cfg.get("fund_name"),
        "mode": "qdii",
        "computed_at": computed_at,
        "trade_date": dates["trade_date"],
        "target_nav_date": target_date,
        "base_nav_date": base_date,
        "nav_lag_days": dates["nav_lag_days"],
        "timezone": dates.get("timezone"),
        "cutoff": dates.get("cutoff"),
        "as_of_beijing": dates.get("as_of_beijing"),
        "market_data_date": market_data_date,
        "base_nav": round(base_nav, 4),
        "estimated_nav": round(estimated_nav, 4),
        "estimated_return": estimated_return,
        "coverage_ratio": summary["covered_weight"],
        "coverage_of_known": summary["coverage_fraction"],
        "covered_weight": summary["covered_weight"],
        "total_weight": summary["total_weight"],
        "uncovered_weight": summary["uncovered_weight"],
        "fx_contribution": summary["fx_contribution"],
        "local_contribution": summary["local_contribution"],
        "interaction_contribution": summary["interaction_contribution"],
        "holdings_included": summary["holdings_included"],
        "holdings_excluded": summary["holdings_excluded"],
        "holdings_quarter": selection.get("quarter") or portfolio.get("quarter"),
        "holdings_date": selection.get("holding_date"),
        "disclosure_date": selection.get("disclosure_date"),
        "disclosure_date_estimated": selection.get("disclosure_date_estimated", True),
        "look_ahead_safe": True,
        "quarters_available": portfolio.get("quarters") or [],
        "holdings_source": portfolio.get("source"),
        "holdings": _rounded_rows(rows),
        "warnings": warnings_list,
        "message": "；".join(warnings_list) if warnings_list else None,
    }

    log.info(f"[{fund_code}] QDII 估算 {target_date}: "
             f"NAV {base_nav:.4f} → {estimated_nav:.4f} "
             f"({estimated_return * 100:+.2f}%)，覆蓋 {summary['covered_weight'] * 100:.1f}%")
    if write:
        _persist(record, save_dir, nav_df)
    return record


def _rounded_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        out.append({
            **{k: v for k, v in row.items() if not isinstance(v, float)},
            **{k: round(v, 6) for k, v in row.items() if isinstance(v, float)},
        })
    return out


def _persist(record: dict, save_dir: Path, nav_df: pd.DataFrame) -> None:
    """Write the latest estimate plus one history row (idempotent per target)."""
    json_path = save_dir / f"{record['fund_code']}{ESTIMATE_SUFFIX}"
    try:
        json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    except OSError as exc:
        log.warning("QDII 估算結果寫入失敗: %s", exc)

    history_path = save_dir / f"{record['fund_code']}{HISTORY_SUFFIX}"
    history = read_history(history_path)
    row = {
        "computed_at": record["computed_at"],
        "target_nav_date": record["target_nav_date"],
        "base_nav_date": record["base_nav_date"],
        "base_nav": record["base_nav"],
        "estimated_nav": record["estimated_nav"],
        "estimated_return": record["estimated_return"],
        "coverage_ratio": record["coverage_ratio"],
        "uncovered_weight": record.get("uncovered_weight"),
        "holdings_quarter": record.get("holdings_quarter"),
        "holdings_date": record.get("holdings_date"),
        "disclosure_date": record.get("disclosure_date"),
        "disclosure_date_estimated": record.get("disclosure_date_estimated"),
        "look_ahead_safe": record.get("look_ahead_safe"),
        "holdings_source": record.get("holdings_source"),
        "status": record["status"],
        "official_nav": None,
        "error": None,
        "abs_error": None,
    }
    history = pd.concat([history, pd.DataFrame([row])], ignore_index=True) \
        if not history.empty else pd.DataFrame([row])
    history = history.drop_duplicates(subset=["target_nav_date"], keep="last")
    history = backfill_history(history, nav_df)
    write_history(history, history_path)
    _write_inputs_snapshot(record, save_dir)


def _write_inputs_snapshot(record: dict, save_dir: Path) -> None:
    """Keep the complete input snapshot of every estimate, per target date.

    The history CSV only carries the summary line; without the per-holding
    prices, FX rates and the dates they were quoted on, an old estimate cannot
    be reproduced or its error traced back to one holding.  This file keeps
    the full record keyed by ``target_nav_date`` so a later run never
    overwrites the inputs of an earlier one.
    """
    path = Path(save_dir) / f"{record.get('fund_code')}{INPUTS_SUFFIX}"
    snapshots: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(existing, dict) and isinstance(existing.get("snapshots"), dict):
                snapshots = existing["snapshots"]
        except (OSError, ValueError) as exc:
            log.warning("QDII 估算輸入快照不可讀（%s），重新建立", exc)
    snapshots[str(record.get("target_nav_date"))] = record
    payload = {
        "fund_code": record.get("fund_code"),
        "updated_at": record.get("computed_at"),
        "snapshots": snapshots,
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError as exc:
        log.warning("QDII 估算輸入快照寫入失敗: %s", exc)
