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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from fa.analysis.estimation_backtest import backfill_history, read_history, write_history
from fa.analysis.returns import calculate_asset, series_from_frame
from fa.analysis.valuation import value_portfolio
from fa.analysis.valuation_dates import resolve_valuation_dates
from fa.data.asset_classifier import build_assets
from fa.config import FX_PIPELINE_ENABLED
from fa.data.database.connection import DatabaseError
from fa.data.estimation_storage import (
    persist_estimate,
    write_inputs_snapshot,
)
from fa.data.fx import fetch_fx_rates, fx_series_map
from fa.data.fx_pipeline import FxRequest, update_fx
from fa.data.nav import fetch_nav, load_nav_csv
from fa.data.qdii_holdings import fetch_qdii_holdings, select_point_in_time
from fa.data.qdii_market import fetch_holding_prices
from fa.models.asset import Asset, normalise_currency
from fa.models.estimation import (
    MODEL_VERSION,
    EstimationResult,
    EstimationRun,
    LookAheadStatus,
    new_run_id,
    resolve_look_ahead_status,
)
from fa.models.market_data import FxSeries
from fa.utils.calendar import to_date
from fa.utils.calendars import calendar_for, calendar_is_fallback
from fa.utils.data_quality import assess_quality
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

ESTIMATE_SUFFIX = "_qdii_estimate.json"
HISTORY_SUFFIX = "_qdii_estimates.csv"
INPUTS_SUFFIX = "_qdii_estimate_inputs.json"
MARKET_LOOKBACK = "1mo"
STALE_HOLDINGS_DAYS = 150

# Kept as private aliases for older callers/tests; the implementation lives in
# the data layer so the analysis layer performs no file I/O.
_persist = persist_estimate
_write_inputs_snapshot = write_inputs_snapshot


def load_nav_frame(fund_code: str, save_dir: Path) -> pd.DataFrame:
    """Official NAV series; the CSV read lives in :mod:`fa.data.nav`."""
    return load_nav_csv(fund_code, save_dir)


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


def _fx_series(rates: dict) -> dict[str, FxSeries]:
    """Wrapper kept for callers; the conversion lives in :mod:`fa.data.fx`."""
    return fx_series_map(rates)


def _pit_moment(as_of: datetime | None) -> datetime:
    """The instant whose published data this estimate is allowed to use."""
    if as_of is None:
        return datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        log.warning("估算時間沒有時區，PIT 匯率按 UTC 解讀：%s", as_of)
        return as_of.replace(tzinfo=timezone.utc)
    return as_of


def _resolve_fx(currencies, repository, *, start: date, end: date,
                as_of: datetime | None):
    """FX series for the estimate plus a label saying which path produced them.

    ``pipeline-pit`` writes fresh bars through :func:`update_fx` and then reads
    back only the rates already published at ``as_of``.  The series returned by
    ``update_fx`` itself is deliberately **not** used here: it also carries the
    bars of the London day that is still in progress, and those must stay
    invisible to the estimate.  ``legacy`` is the pre-Stage-2.5 download path
    and is used only when the pipeline is switched off or the database cannot
    accept the write.
    """
    warnings: list[str] = []
    wanted = sorted({normalise_currency(code) for code in currencies if code})
    wanted = [code for code in wanted if code and code != "CNY"]
    if not wanted:
        return {}, "none", warnings
    if not FX_PIPELINE_ENABLED or repository is None:
        if repository is None:
            warnings.append("沒有資料庫可用，FX 改用舊下載路徑（無 PIT 保證）")
        return _fx_series(fetch_fx_rates(wanted, start=start, end=end)), "legacy", warnings
    try:
        outcome = update_fx(
            FxRequest(currencies=wanted, start=start, end=end), repository
        )
    except DatabaseError as exc:
        warnings.append(f"FX pipeline 無法寫入資料庫（{exc}），改用舊下載路徑（無 PIT 保證）")
        return _fx_series(fetch_fx_rates(wanted, start=start, end=end)), "legacy", warnings
    except Exception as exc:  # noqa: BLE001 - pipeline 失敗不應讓估算失敗
        warnings.append(f"FX pipeline 失敗：{exc}")
    else:
        warnings.extend(outcome.warnings)
        for code, reason in sorted(outcome.skipped.items()):
            warnings.append(f"{code}: {reason}")
    moment = _pit_moment(as_of)
    series: dict[str, FxSeries] = {}
    for code in wanted:
        try:
            loaded = repository.fx_as_of(code, moment, start=start, end=end)
        except Exception as exc:  # noqa: BLE001 - 單一貨幣失敗只影響自己
            warnings.append(f"{code}: PIT 匯率讀取失敗（{exc}）")
            continue
        if len(loaded):
            series[code] = loaded
            try:
                stored = repository.load_fx(code)
            except Exception:  # noqa: BLE001 - 只是診斷訊息
                stored = None
            if stored is not None:
                hidden = sum(
                    1 for point in stored.points
                    if point.available_at is not None and point.available_at > moment
                )
                if hidden:
                    warnings.append(
                        f"{code}: {hidden} 筆匯率尚未公布，已按 PIT 排除")
        else:
            warnings.append(
                f"{code}: 截至 {moment.isoformat()} 尚無已公布匯率（PIT 排除）")
    return series, "pipeline-pit", warnings


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
            look_ahead_safe=False,
            look_ahead_status=LookAheadStatus.UNKNOWN.value,
            quarters_available=available,
        )
    holdings = selection["holdings"]

    # One run id per estimate: it is the identity of the run everywhere (SQLite,
    # history CSV, input snapshot), so re-estimating the same target date never
    # destroys the earlier run.
    run_id = new_run_id()
    look_ahead_status = resolve_look_ahead_status(
        disclosure_date=selection.get("disclosure_date"),
        disclosure_date_estimated=bool(selection.get("disclosure_date_estimated", True)),
        target_date=target_date,
    )

    tickers = [str(t) for t in holdings["ticker"].tolist() if str(t).strip()]
    look_start = base_date - timedelta(days=10)
    price_series, price_hits = _market_data(
        tickers, look_start, target_date, repository
    )

    assets = build_assets(holdings, price_hits)

    # The declared currency wins, but a holding whose currency the source did
    # not label still needs its quote currency fetched, otherwise the FX leg
    # would be silently skipped.
    currencies = {asset.currency for asset in assets if asset.currency}
    currencies |= {
        series.currency for series in price_series.values() if series.currency
    }
    fx_map, fx_source, fx_warnings = _resolve_fx(
        currencies, repository, start=look_start, end=target_date, as_of=as_of,
    )

    def _fx_for(asset: Asset) -> FxSeries | None:
        code = asset.currency or (price_series.get(asset.asset_id).currency
                                  if asset.asset_id in price_series else "")
        return fx_map.get(normalise_currency(code))

    # Each holding gets its own market calendar: a QDII fund can hold a US, a
    # Swiss and a Hong Kong position in the same quarter, and one calendar
    # cannot judge all three for staleness.
    calendars = {
        asset.asset_id: calendar_for(asset.market or "", asset.ticker)
        for asset in assets
    }

    results = [
        calculate_asset(
            asset,
            base_date=base_date,
            target_date=target_date,
            prices=price_series.get(asset.asset_id),
            fx=_fx_for(asset),
            calendar=calendars[asset.asset_id],
        )
        for asset in assets
    ]
    for asset, result in zip(assets, results):
        hit = price_hits.get(asset.asset_id) or {}
        cal = calendars[asset.asset_id]
        result.extra.update({
            "name": asset.name,
            "market": asset.market or "",
            "symbol": hit.get("symbol") or asset.ticker,
            "asset_type": asset.asset_type.value,
            "classification": asset.extra.get("classification", ""),
            "calendar": getattr(cal, "market", ""),
            "calendar_fallback": calendar_is_fallback(cal),
        })
    calendar_fallback = any(
        bool(result.extra.get("calendar_fallback")) for result in results
    )
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
        calendar_fallback=calendar_fallback,
    )

    warnings_list: list[str] = list(portfolio.get("warnings") or [])
    warnings_list.extend(fx_warnings)
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
        run_id=run_id,
        look_ahead_status=look_ahead_status,
        inputs=_inputs_snapshot(
            fund_code=fund_code,
            cfg=cfg,
            base_date=base_date,
            target_date=target_date,
            base_nav=base_nav,
            selection=selection,
            portfolio=portfolio,
            assets=assets,
            results=results,
            price_series=price_series,
            fx_map=fx_map,
            calendars=calendars,
        ),
    )
    record["fx_source"] = fx_source
    if isinstance(record.get("inputs"), dict):
        record["inputs"]["fx_source"] = fx_source

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


def _market_data(tickers: list[str], start: date, end: date, repository=None):
    """Prices for the estimation window, database first, network second.

    The window is the valuation interval itself (plus a few days of slack), not
    a fixed lookback: an estimate for a July target date must not be built from
    the last month of November quotes.
    """
    series_map: dict = {}
    hits: dict = {}
    missing: list[str] = []
    for ticker in tickers:
        cached = _db_prices(repository, ticker, start, end)
        if cached is None:
            missing.append(ticker)
            continue
        series_map[ticker] = cached
        hits[ticker] = {
            "market": cached.market,
            "currency": cached.currency,
            "symbol": ticker,
            "source": "database",
        }
    if missing:
        fetched = fetch_holding_prices(missing, start=start, end=end) or {}
        for ticker, hit in fetched.items():
            frame = (hit or {}).get("prices")
            if frame is None or getattr(frame, "empty", True):
                continue
            series = series_from_frame(
                frame,
                ticker=ticker,
                market=str((hit or {}).get("exchange") or ""),
                currency=normalise_currency((hit or {}).get("currency")),
                source="yahoo",
            )
            series_map[ticker] = series
            hits[ticker] = {
                "market": series.market,
                "currency": series.currency,
                "symbol": (hit or {}).get("symbol") or ticker,
                "source": "yahoo",
            }
            _save_prices(repository, series)
    return series_map, hits


def _db_prices(repository, ticker: str, start: date, end: date):
    if repository is None:
        return None
    try:
        series = repository.load_prices(ticker, start=start, end=end)
    except Exception as exc:  # noqa: BLE001 - 資料庫只是快取，不能擋住估算
        log.debug("[prices] 讀取資料庫 %s 失敗: %s", ticker, exc)
        return None
    if series is None or not getattr(series, "points", None):
        return None
    return series


def _save_prices(repository, series) -> None:
    if repository is None or series is None:
        return
    try:
        repository.save_prices(series)
    except Exception as exc:  # noqa: BLE001 - 寫入失敗只損失快取
        log.debug("[prices] 寫入資料庫 %s 失敗: %s", getattr(series, "ticker", ""), exc)


def _price_point(series, day: date):
    if series is None:
        return None
    try:
        return series.point_on_or_before(day)
    except Exception:  # noqa: BLE001 - 快照不該讓估算失敗
        return None


def _fx_point(fx: FxSeries | None, day: date):
    if fx is None:
        return (None, None)
    try:
        return fx.rate_on_or_before(day)
    except Exception:  # noqa: BLE001
        return (None, None)


def _inputs_snapshot(*, fund_code: str, cfg: dict, base_date: date,
                     target_date: date, base_nav: float, selection: dict,
                     portfolio: dict, assets: list[Asset], results,
                     price_series: dict, fx_map: dict, calendars: dict) -> dict:
    """Everything needed to reproduce this estimate without hitting the network.

    Yahoo revises history; a stored return is worthless without the price
    values, FX rates, dates and calendars it was computed from.
    """
    by_id = {asset.asset_id: asset for asset in assets}
    asset_rows = []
    for result in results:
        asset = by_id.get(result.asset_id)
        series = price_series.get(result.asset_id)
        base_point = _price_point(series, base_date)
        target_point = _price_point(series, target_date)
        currency = result.currency or (asset.currency if asset else "")
        fx_rates = fx_map.get(normalise_currency(currency))
        fx_base, fx_base_date = _fx_point(fx_rates, base_date)
        fx_target, fx_target_date = _fx_point(fx_rates, target_date)
        calendar = calendars.get(result.asset_id)
        asset_rows.append({
            "ticker": result.asset_id,
            "name": (asset.name if asset else None),
            "asset_type": (asset.asset_type.value if asset else "unknown"),
            "classification": (asset.extra.get("classification") if asset else ""),
            "weight": result.weight,
            "currency": currency,
            "market": (asset.market if asset else "") or "",
            "calendar": getattr(calendar, "market", ""),
            "calendar_fallback": calendar_is_fallback(calendar) if calendar else False,
            "price_type_used": result.price_type_used,
            "available_price_type": getattr(base_point, "available_price_type", "") or "",
            "used_price_type": (
                result.price_type_used
                or getattr(target_point, "used_price_type", "")
                or getattr(base_point, "used_price_type", "")
            ),
            "base_price_date": _iso(getattr(base_point, "price_date", None)),
            "base_price": getattr(base_point, "value", None),
            "target_price_date": _iso(getattr(target_point, "price_date", None)),
            "target_price": getattr(target_point, "value", None),
            "base_fx_date": _iso(fx_base_date),
            "base_fx": fx_base,
            "target_fx_date": _iso(fx_target_date),
            "target_fx": fx_target,
            "status": result.status.value,
            "local_return": result.local_return,
            "fx_return": result.fx_return,
            "return_cny": result.return_cny,
            "cash_rate": (result.extra or {}).get("annual_rate"),
            "cash_rate_source": (result.extra or {}).get("rate_source"),
            "warnings": list(result.warnings),
        })
    return {
        "fund_code": fund_code,
        "model_version": MODEL_VERSION,
        "config": {k: v for k, v in (cfg or {}).items() if k != "fund_name"},
        "base_nav_date": _iso(base_date),
        "target_nav_date": _iso(target_date),
        "base_nav": base_nav,
        "holdings": {
            "quarter": selection.get("quarter"),
            "holding_date": _iso(selection.get("holding_date")),
            "disclosure_date": _iso(selection.get("disclosure_date")),
            "disclosure_date_estimated": bool(
                selection.get("disclosure_date_estimated", True)),
            "source": portfolio.get("source"),
            "scope": portfolio.get("holdings_scope"),
            "total_weight": float(selection["holdings"]["weight"].sum()),
        },
        "assets": asset_rows,
    }


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
                   warnings_list: list[str], run_id: str = "",
                   look_ahead_status: LookAheadStatus = LookAheadStatus.UNKNOWN,
                   inputs: dict | None = None) -> dict:
    """Serialize the V2 objects into the field names existing consumers expect."""
    rows = [_legacy_row(result) for result in results]
    interaction = (summary.estimated_return or 0.0) - summary.local_contribution - summary.fx_contribution
    return {
        "run_id": run_id,
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
        "look_ahead_safe": look_ahead_status.is_strictly_safe,
        "look_ahead_status": look_ahead_status.value,
        "quarters_available": portfolio.get("quarters") or [],
        "holdings_source": portfolio.get("source"),
        "holdings_scope": portfolio.get("holdings_scope"),
        "history_available": bool(portfolio.get("history_available")),
        "data_quality": quality.grade.value,
        "quality_score": quality.score,
        "quality": quality.to_dict(),
        "holdings": _rounded_rows(rows),
        "warnings": warnings_list,
        "message": "；".join(warnings_list) if warnings_list else None,
        "inputs": inputs or {},
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
        "asset_type": (result.extra or {}).get("asset_type"),
        "classification": (result.extra or {}).get("classification"),
        "price_type_used": result.price_type_used,
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
            run_id=record.get("run_id") or new_run_id(),
            holdings_quarter=record.get("holdings_quarter"),
            holdings_date=to_date(record["holdings_date"]) if record.get("holdings_date") else None,
            disclosure_date=(to_date(record["disclosure_date"])
                             if record.get("disclosure_date") else None),
            disclosure_date_estimated=bool(record.get("disclosure_date_estimated")),
            holdings_source=record.get("holdings_source") or "",
            look_ahead_status=LookAheadStatus.parse(record.get("look_ahead_status")),
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
        repository.save_estimation(run, record.get("inputs") or record, result)
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
