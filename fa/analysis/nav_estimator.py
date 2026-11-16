# -*- coding: utf-8 -*-
"""QDII NAV estimator (V2.2: orchestrator only).

For a QDII fund the official NAV lags the overseas close by one trading day,
so at day ``T`` we estimate the NAV of ``T-1``::

    NAV_T-1 = NAV_base * (1 + R_fund)
    R_fund  = Σ_{i ∈ C} w_i * r_i,CNY          (weights never renormalised)

This module **coordinates**; it does not do the maths itself:

1. valuation dates          :mod:`fa.analysis.valuation_dates`
2. official NAV             :mod:`fa.data.nav`
3. point-in-time holdings   :mod:`fa.data.qdii_holdings`
4. market prices / FX       :mod:`fa.data.qdii_market`, :mod:`fa.data.fx`
5. per-asset CNY returns    :mod:`fa.analysis.returns`
6. portfolio aggregation    :mod:`fa.analysis.valuation`
7. data-quality grading     :mod:`fa.utils.data_quality`
8. immutable run + result   :mod:`fa.data.database` (when a repository is given)
9. file output              : legacy JSON/CSV via the serializer below

The estimated NAV is never written over the official NAV.  The legacy JSON
field names are produced by :func:`_legacy_record` so existing reports keep
working while the internals moved to the V2 model.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from fa.analysis.estimation_backtest import backfill_history, read_history, write_history
from fa.analysis.returns import calculate_asset, series_from_frame
from fa.analysis.valuation import value_portfolio
from fa.analysis.valuation_dates import resolve_valuation_dates
from fa.data.fx import fetch_fx_rates
from fa.data.nav import fetch_nav
from fa.data.qdii_holdings import fetch_qdii_holdings, select_point_in_time
from fa.data.qdii_market import fetch_holding_prices
from fa.models.asset import Asset, AssetType, normalise_currency
from fa.models.estimation import EstimationResult, EstimationRun
from fa.models.market_data import FxSeries
from fa.utils.calendar import to_date
from fa.utils.data_quality import assess_quality
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


def _empty_record(fund_code: str, computed_at: str, status: str, message: str,
                  **extra) -> dict:
    record = {
        "status": status,
        "fund_code": fund_code,
        "computed_at": computed_at,
        "message": message,
        "holdings": [],
        "warnings": [message] if message else [],
        "model_version": None,
    }
    record.update(extra)
    return record


def _build_assets(holdings: pd.DataFrame, price_hits: dict) -> list[Asset]:
    """Convert one holdings quarter into :class:`Asset` objects."""
    assets: list[Asset] = []
    for row in holdings.to_dict("records"):
        ticker = str(row.get("ticker") or "").strip()
        hit = price_hits.get(ticker) or {}
        currency = normalise_currency(hit.get("currency") or row.get("currency_hint"))
        market = hit.get("exchange") or row.get("market")
        assets.append(
            Asset(
                asset_id=ticker,
                name=str(row.get("name") or ticker),
                asset_type=AssetType.EQUITY,
                ticker=ticker,
                market=market or None,
                currency=currency,
                weight=float(row.get("weight") or 0.0),
                holding_date=to_date(row.get("holding_date")) if row.get("holding_date") else None,
                source=str(row.get("source") or ""),
            )
        )
    return assets


def _fx_series(rates: dict) -> dict[str, FxSeries]:
    """Wrap raw FX pandas series into the V2 :class:`FxSeries` model."""
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
            out[str(currency).upper()] = FxSeries.from_pairs(
                str(currency).upper(), pairs, source="yahoo"
            )
    return out


def estimate_qdii_nav(fund_code: str, save_dir: Path, cfg: dict | None = None,
                      as_of: datetime | None = None, target_nav_date=None,
                      use_cache: bool = True, write: bool = True,
                      repository=None) -> dict:
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
        return _empty_record(fund_code, computed_at, "insufficient-data",
                             "沒有官方淨值，無法估算")

    dates = resolve_valuation_dates(
        nav_df["Date"], as_of=as_of, target_nav_date=target_nav_date)
    base_nav = _nav_on(nav_df, dates["base_nav_date"]) if dates.get("base_nav_date") else None
    if dates["status"] != "ok" or base_nav is None:
        status = dates["status"] if dates["status"] != "ok" else "insufficient-data"
        return _empty_record(
            fund_code, computed_at, status,
            dates.get("note") or "缺少估算所需的官方淨值",
            **{k: dates.get(k) for k in ("trade_date", "target_nav_date", "base_nav_date")},
            nav_lag_days=dates.get("nav_lag_days"),
        )

    target_date = to_date(dates["target_nav_date"])
    base_date = to_date(dates["base_nav_date"])

    # 只使用「目標日當天已經公開」的季度持倉，否則就是看得到未來的回測。
    # 只取「最新可得」的持倉：歷史季度不再自動下載（上游檔案接口不可達），
    # 需要更早日期時只能靠已匯入／已存進資料庫的歷史快照。
    portfolio = fetch_qdii_holdings(fund_code, save_dir, use_cache=use_cache,
                                    repository=repository)
    selection = select_point_in_time(portfolio.get("holdings"), target_date)

    if selection is None or selection["holdings"].empty:
        available = portfolio.get("quarters") or []
        detail = f"（本機季度：{', '.join(available)}）" if available else ""
        return _empty_record(
            fund_code, computed_at, "insufficient-data",
            (f"目標日 {target_date} 之前沒有已披露的 QDII 持倉{detail}；"
             f"需要更早的季度持倉，請先匯入歷史快照"),
            **{k: dates.get(k) for k in ("trade_date", "target_nav_date",
                                         "base_nav_date", "timezone", "cutoff",
                                         "as_of_beijing")},
            base_nav=base_nav,
            look_ahead_safe=True,
            quarters_available=available,
        )
    holdings = selection["holdings"]

    tickers = [str(t) for t in holdings["ticker"].tolist() if str(t).strip()]
    price_hits = fetch_holding_prices(tickers, range_=MARKET_LOOKBACK)

    assets = _build_assets(holdings, price_hits)
    currencies = {asset.currency for asset in assets if asset.currency}
    fx_map = _fx_series(fetch_fx_rates(currencies, range_=MARKET_LOOKBACK))

    price_series = {}
    for ticker in tickers:
        hit = price_hits.get(ticker) or {}
        frame = hit.get("prices")
        if frame is None:
            continue
        price_series[ticker] = series_from_frame(
            frame,
            ticker=ticker,
            market=str(hit.get("exchange") or ""),
            currency=normalise_currency(hit.get("currency")),
            source="yahoo",
        )

    results = [
        calculate_asset(
            asset,
            base_date=base_date,
            target_date=target_date,
            prices=price_series.get(asset.asset_id),
            fx=fx_map.get(asset.currency),
        )
        for asset in assets
    ]
    for asset, result in zip(assets, results):
        hit = price_hits.get(asset.asset_id) or {}
        result.extra.update({
            "name": asset.name,
            "market": asset.market or "",
            "symbol": hit.get("symbol") or asset.ticker,
        })
    summary = value_portfolio(
        _snapshot_view(fund_code, selection, assets), results
    )

    if summary.estimated_return is None:
        estimated_return = None
        estimated_nav = None
    else:
        estimated_return = summary.estimated_return
        estimated_nav = base_nav * (1.0 + estimated_return)
    market_dates = [r.target_price_date for r in results if r.target_price_date]
    market_data_date = max(market_dates) if market_dates else None

    quality = assess_quality(
        results,
        disclosure_date_estimated=bool(selection.get("disclosure_date_estimated", True)),
        base_nav_fresh=_base_nav_is_fresh(nav_df, base_date, target_date),
    )

    warnings_list: list[str] = list(portfolio.get("warnings") or [])
    if portfolio.get("source") == "cache-stale":
        warnings_list.append("持倉無法更新，改用本機較舊快照，權重可能已偏離實際")
    if portfolio.get("holdings_scope") == "top10":
        warnings_list.append("持倉僅含前十大（揭露口徑），其餘資產按未覆蓋處理")
    excluded = summary.excluded()
    if excluded:
        warnings_list.append(
            f"{len(excluded)} 隻持倉缺少可用價格、匯率或幣種，已排除在覆蓋率之外")
    if summary.uncovered_weight > 0.001:
        warnings_list.append(
            f"未覆蓋 {summary.uncovered_weight * 100:.1f}% 的資產按 0% 回報處理"
            f"（未知，非已知事實）")
    warnings_list.extend(w for w in summary.warnings if w not in warnings_list)
    if quality.reasons:
        warnings_list.extend(r for r in quality.reasons if r not in warnings_list)

    if summary.status.value == "insufficient-data":
        warnings_list.append("沒有任何持倉可估值")

    if selection.get("holding_date"):
        try:
            age = (pd.Timestamp(target_date) - pd.Timestamp(selection["holding_date"])).days
            if age > STALE_HOLDINGS_DAYS:
                warnings_list.append(f"持倉披露已 {age} 天未更新，權重可能已偏離實際")
        except (TypeError, ValueError):
            pass

    record = _legacy_record(
        fund_code=fund_code,
        cfg=cfg,
        computed_at=computed_at,
        dates=dates,
        target_date=target_date,
        base_date=base_date,
        base_nav=base_nav,
        estimated_nav=estimated_nav,
        summary=summary,
        results=results,
        selection=selection,
        portfolio=portfolio,
        quality=quality,
        market_data_date=market_data_date,
        warnings_list=warnings_list,
    )

    if estimated_nav is None:
        log.warning(f"[{fund_code}] QDII 估算 {target_date}: 沒有可估值的持倉"
                    f"（品質 {quality.grade.value}），未產生估算值")
    else:
        log.info(f"[{fund_code}] QDII 估算 {target_date}: "
                 f"NAV {base_nav:.4f} → {estimated_nav:.4f} "
                 f"({estimated_return * 100:+.2f}%)，覆蓋 {summary.coverage_ratio * 100:.1f}%"
                 f"（品質 {quality.grade.value}）")
    if write:
        _persist(record, save_dir, nav_df)
        _save_to_database(repository, record, nav_df, results)
    return record


def _snapshot_view(fund_code: str, selection: dict, assets: list[Asset]):
    """Minimal snapshot exposing ``total_weight`` for :func:`value_portfolio`."""
    from fa.models.holding import HoldingSnapshot

    return HoldingSnapshot(
        fund_code=str(fund_code or ""),
        assets=assets,
        quarter=selection.get("quarter"),
        holding_date=to_date(selection["holding_date"]) if selection.get("holding_date") else None,
        disclosure_date=(to_date(selection["disclosure_date"])
                         if selection.get("disclosure_date") else None),
        disclosure_date_estimated=bool(selection.get("disclosure_date_estimated", True)),
        source=str(selection.get("source") or ""),
    )


def _base_nav_is_fresh(nav_df: pd.DataFrame, base_date: date, target_date: date) -> bool:
    """True when the base NAV is the last published NAV before the target date."""
    if nav_df.empty:
        return False
    published = pd.to_datetime(nav_df["Date"], errors="coerce").dropna()
    before = published[published.dt.date <= target_date]
    if before.empty:
        return False
    return before.iloc[-1].date() == base_date


def _iso(value):
    """JSON-safe ISO date string (``None`` stays ``None``)."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)[:10]


def _legacy_record(*, fund_code: str, cfg: dict, computed_at: str, dates: dict,
                   target_date: date, base_date: date, base_nav: float,
                   estimated_nav: float, summary, results, selection: dict,
                   portfolio: dict, quality, market_data_date,
                   warnings_list: list[str]) -> dict:
    """Serialize the V2 objects into the field names existing consumers expect."""
    rows = [_legacy_row(result) for result in results]
    interaction = (summary.estimated_return or 0.0) - summary.local_contribution - summary.fx_contribution
    return {
        "status": summary.status.value,
        "fund_code": fund_code,
        "fund_name": cfg.get("fund_name"),
        "mode": "qdii",
        "model_version": "2.2.0",
        "computed_at": computed_at,
        "trade_date": dates.get("trade_date"),
        "target_nav_date": dates.get("target_nav_date"),
        "base_nav_date": dates.get("base_nav_date"),
        "nav_lag_days": dates["nav_lag_days"],
        "timezone": dates.get("timezone"),
        "cutoff": dates.get("cutoff"),
        "as_of_beijing": dates.get("as_of_beijing"),
        "market_data_date": _iso(market_data_date),
        "base_nav": round(float(base_nav), 4),
        "estimated_nav": (round(float(estimated_nav), 4)
                          if estimated_nav is not None else None),
        "estimated_return": summary.estimated_return,
        "coverage_ratio": summary.coverage_ratio,
        "coverage_of_known": summary.coverage_of_known,
        "covered_weight": summary.covered_weight,
        "total_weight": summary.disclosed_weight,
        "uncovered_weight": summary.uncovered_weight,
        "fx_contribution": summary.fx_contribution,
        "local_contribution": summary.local_contribution,
        # legacy consumers read this field; V2 splits it away with Shapley, so
        # re-emit the (now empty by construction) interaction term for them.
        "interaction_contribution": interaction,
        "holdings_included": summary.holdings_included,
        "holdings_excluded": summary.holdings_excluded,
        "holdings_quarter": selection.get("quarter") or portfolio.get("quarter"),
        "holdings_date": _iso(selection.get("holding_date")),
        "disclosure_date": _iso(selection.get("disclosure_date")),
        "disclosure_date_estimated": selection.get("disclosure_date_estimated", True),
        "look_ahead_safe": True,
        "quarters_available": portfolio.get("quarters") or [],
        "holdings_source": portfolio.get("source"),
        "holdings_scope": portfolio.get("holdings_scope"),
        "history_available": bool(portfolio.get("history_available")),
        "data_quality": quality.grade.value,
        "quality_score": quality.score,
        "holdings": _rounded_rows(rows),
        "warnings": warnings_list,
        "message": "；".join(warnings_list) if warnings_list else None,
    }


def _legacy_row(result) -> dict:
    """One per-holding row in the legacy JSON shape."""
    return {
        "ticker": result.asset_id,
        "name": result.extra.get("name") if result.extra else None,
        "weight": result.weight,
        "symbol": result.extra.get("symbol") if result.extra else None,
        "currency": result.currency,
        "market": result.extra.get("market") if result.extra else None,
        "status": result.status.value,
        "local_return": result.local_return,
        "fx_return": result.fx_return,
        "cny_return": result.return_cny,
        "contribution": result.contribution,
        "price_date": result.target_price_date.isoformat() if result.target_price_date else None,
        "base_price_date": result.base_price_date.isoformat() if result.base_price_date else None,
        "fx_date": result.target_fx_date.isoformat() if result.target_fx_date else None,
        "base_fx_date": result.base_fx_date.isoformat() if result.base_fx_date else None,
        "notes": list(result.warnings),
    }


def _save_to_database(repository, record: dict, nav_df: pd.DataFrame,
                      results=None) -> str | None:
    """Persist one immutable estimation run (V2.1 database layer)."""
    if repository is None or not record.get("target_nav_date"):
        return None
    try:
        as_of_value = record.get("as_of_beijing")
        if isinstance(as_of_value, str):
            try:
                as_of_value = datetime.fromisoformat(as_of_value)
            except ValueError:
                as_of_value = None
        run = EstimationRun(
            fund_code=record["fund_code"],
            target_nav_date=to_date(record["target_nav_date"]),
            trade_date=to_date(record["trade_date"]),
            base_nav_date=to_date(record["base_nav_date"]) if record.get("base_nav_date") else None,
            as_of=as_of_value,
            mode="qdii",
            holdings_quarter=record.get("holdings_quarter"),
            holdings_date=to_date(record["holdings_date"]) if record.get("holdings_date") else None,
            disclosure_date=(to_date(record["disclosure_date"])
                             if record.get("disclosure_date") else None),
            disclosure_date_estimated=bool(record.get("disclosure_date_estimated")),
            holdings_source=record.get("holdings_source") or "",
            look_ahead_safe=bool(record.get("look_ahead_safe", True)),
        )
        result = EstimationResult(
            run_id=run.run_id,
            status=record["status"],
            base_nav=record.get("base_nav"),
            estimated_nav=record.get("estimated_nav"),
            estimated_return=record.get("estimated_return"),
            coverage_ratio=record.get("coverage_ratio") or 0.0,
            uncovered_weight=record.get("uncovered_weight") or 0.0,
            local_contribution=record.get("local_contribution") or 0.0,
            fx_contribution=record.get("fx_contribution") or 0.0,
            data_quality=record.get("data_quality") or "unknown",
            quality_score=record.get("quality_score"),
            warnings=list(record.get("warnings") or []),
            message=record.get("message") or "",
        )
        if "Date" in nav_df.columns and "NAV" in nav_df.columns:
            repository.save_official_nav(
                record["fund_code"],
                [(row["Date"], row["NAV"]) for row in nav_df.to_dict("records")],
            )
        repository.save_estimation(run, record, result)
        log.info(f"[{record['fund_code']}] 估算已寫入資料庫 run={run.run_id[:8]}")
        return run.run_id
    except Exception as exc:  # noqa: BLE001 - 資料庫失敗不該讓估算消失
        log.warning(f"[{record.get('fund_code')}] 估算寫入資料庫失敗: {exc}")
        return None


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
