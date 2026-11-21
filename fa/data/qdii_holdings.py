# -*- coding: utf-8 -*-
"""QDII holdings: the *disclosed* portfolios, with real dates.

A QDII fund reports its portfolio quarterly, so the weights used for an
estimate may describe a date months in the past.  Every record therefore
carries both ``holding_date`` (the quarter end the weights describe) and
``disclosure_date`` (the day the market could first know them), which is what
makes a look-ahead-free estimate possible: :func:`select_point_in_time` only
returns quarters that were already public on the target NAV date.

``disclosure_date`` is derived today (quarter end + 21 calendar days) because
no source we can reach publishes the real filing date; such rows carry
``disclosure_date_estimated=True`` so the value is never mistaken for a filing
record.

The upstream vendor is the same (Eastmoney), but everything here is
QDII-specific: a separate cache file, weight fractions, per-holding currency
hints and the disclosure-date logic.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from fa.data.holdings import _clean_holdings, fetch_holdings_page
from fa.data.qdii_market import SYMBOL_OVERRIDES
from fa.models.asset import Asset, AssetType
from fa.models.holding import HoldingSnapshot, snapshot_from_rows
from fa.utils.cache import is_cache_fresh, read_cache_df, write_cache_df
from fa.utils.calendar import to_date
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

QDII_HOLDINGS_SUFFIX = "_qdii_holdings.csv"
# 季報須於季度結束後 15 個工作日內披露；用 21 個日曆日近似（非真實披露日）
QDII_DISCLOSURE_LAG_DAYS = 21

COLUMNS = [
    "ticker", "name", "weight", "currency_hint", "market",
    "quarter", "holding_date", "disclosure_date", "disclosure_date_estimated",
    "asset_type",
]

#: The Eastmoney table this data comes from is literally 「股票投资明细」 (equity
#: holdings detail).  That label is a *source-declared* asset type — it is used
#: instead of guessing from the ticker's shape, which proves nothing.
EQUITY_TABLE_MARKER = "股票投资明细"

_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}

_MARKETS = {
    ".SW": ("SIX", "CHF"), ".L": ("LSE", "GBP"), ".DE": ("XETRA", "EUR"),
    ".PA": ("EPA", "EUR"), ".AS": ("AMS", "EUR"), ".T": ("TSE", "JPY"),
    ".HK": ("HKEX", "HKD"), ".TO": ("TSX", "CAD"), ".AX": ("ASX", "AUD"),
}

_OVERRIDE_MARKET = {
    "ROP.SW": ("SIX", "CHF"),
    "NOVN.SW": ("SIX", "CHF"),
    "AZN.L": ("LSE", "GBP"),
}


def save_quarters_to_db(repository, fund_code: str, frame: pd.DataFrame,
                        source: str = "eastmoney-archives") -> int:
    """把每個季度的持倉寫進 SQLite（V2.1 的資料層），失敗只記錄不拋出。

    ``source`` 必須是資料真正的來源（``eastmoney-archives`` / ``eastmoney-page``），
    否則日後追查「這季資料從哪來」會得到錯誤答案。
    """
    if repository is None or frame is None or frame.empty:
        return 0
    source = source or "unknown"
    saved = 0
    for quarter, chunk in frame.groupby("quarter", sort=True, observed=True):
        rows = []
        for row in chunk.to_dict("records"):
            rows.append({
                "ticker": row.get("ticker"),
                "name": row.get("name"),
                "weight": row.get("weight", 0.0),
                "market": row.get("market"),
                "currency": row.get("currency_hint"),
                "holding_date": row.get("holding_date"),
                "disclosure_date": row.get("disclosure_date"),
                # 保留來源聲明的資產類型；沒有聲明就是 unknown，不能在這裡重新假設。
                "asset_type": AssetType.parse(row.get("asset_type")),
            })
        try:
            snapshot = snapshot_from_rows(
                fund_code, rows,
                quarter=str(quarter),
                holding_date=to_date(chunk["holding_date"].iloc[0]) if "holding_date" in chunk else None,
                disclosure_date=to_date(chunk["disclosure_date"].iloc[0]) if "disclosure_date" in chunk else None,
                disclosure_date_estimated=True,
                source=source,
            )
            repository.save_snapshot(snapshot)
            saved += 1
        except Exception as exc:  # noqa: BLE001 - 資料庫不可用不該中斷估值
            log.debug(f"[{fund_code}] 持倉寫入資料庫失敗（{quarter}）: {exc}")
            break
    if saved:
        log.info(f"[{fund_code}] {saved} 個季度持倉已寫入資料庫（來源 {source}）")
    return saved


def load_quarters_from_db(repository, fund_code: str) -> pd.DataFrame:
    """從 SQLite 讀回歷史持倉（網絡不可用時的歷史來源）。"""
    if repository is None:
        return pd.DataFrame(columns=COLUMNS)
    rows = []
    try:
        snapshots = repository.load_snapshots(fund_code)
    except Exception as exc:  # noqa: BLE001 - 資料庫不可用視同沒有歷史
        log.debug(f"[{fund_code}] 讀取資料庫持倉失敗: {exc}")
        return pd.DataFrame(columns=COLUMNS)
    for snapshot in snapshots or []:
        for asset in snapshot.assets:
            rows.append({
                "ticker": asset.ticker or asset.asset_id,
                "name": asset.name,
                "weight": asset.weight,
                "currency_hint": asset.currency,
                "market": asset.market,
                # 資料庫存的是「來源聲明的類型」，讀回來時必須一起帶上，
                # 否則離線 fallback 會把每隻持倉都變成 unknown（無法估值）。
                "asset_type": asset.asset_type.value,
                "quarter": snapshot.quarter,
                "holding_date": snapshot.holding_date.isoformat() if snapshot.holding_date else "",
                "disclosure_date": snapshot.disclosure_date.isoformat() if snapshot.disclosure_date else "",
                "disclosure_date_estimated": snapshot.disclosure_date_estimated,
            })
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.DataFrame(rows, columns=COLUMNS)
    frame = frame.sort_values(["quarter", "weight"], ascending=[True, False])
    return frame.reset_index(drop=True)


def quarter_end(label: str) -> date | None:
    """``"2025Q1"`` -> ``date(2025, 3, 31)``; ``None`` when unparsable."""
    text = str(label or "").strip().upper()
    if "Q" not in text:
        return None
    year_part, _, quarter_part = text.partition("Q")
    try:
        year, quarter = int(year_part), int(quarter_part)
    except ValueError:
        return None
    if quarter not in _QUARTER_END:
        return None
    month, day = _QUARTER_END[quarter]
    return date(year, month, day)


def market_and_currency(ticker: str) -> tuple[str | None, str | None]:
    """Best-effort exchange/currency hint; the price API confirms it later."""
    raw = str(ticker or "").strip().upper()
    if not raw:
        return None, None
    override = SYMBOL_OVERRIDES.get(raw) or SYMBOL_OVERRIDES.get(raw.replace(" ", ""))
    if override:
        return _OVERRIDE_MARKET.get(override, (None, None))
    compact = raw.replace(" ", "")
    if compact.isdigit():
        return "HKEX", "HKD"
    for suffix, (market, currency) in _MARKETS.items():
        if compact.endswith(suffix):
            return market, currency
    if compact.isalpha():
        return "US", "USD"
    return None, None


def _source_asset_type(row) -> str:
    """Asset type declared by the source table, never inferred from the ticker.

    Eastmoney's 「股票投资明细」 table is the equity-holdings table; its label is
    trustworthy evidence.  Anything else returns ``""`` so the classifier keeps
    the holding as ``unknown`` instead of guessing.
    """
    label = str(row.get("Quarter") or "") or str(row.get("_quarter") or "")
    return "equity" if EQUITY_TABLE_MARKER in label else ""


def build_qdii_holdings(raw: pd.DataFrame, quarter: str | None = None,
                        keep_all: bool = False) -> pd.DataFrame:
    """Normalise a raw Eastmoney frame into the QDII record layout.

    ``keep_all=True`` keeps every disclosed quarter (needed for a
    point-in-time estimate of an older target date); the default keeps only the
    latest one, which is all the "latest NAV" view needs.
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=COLUMNS)

    cleaned = _clean_holdings(raw.copy()).copy()
    if "QuarterLabel" in cleaned.columns:
        cleaned["_quarter"] = cleaned["QuarterLabel"].astype(str)
    else:
        cleaned["_quarter"] = str(quarter or "")

    available = sorted({q for q in cleaned["_quarter"] if q})
    if quarter is not None:
        cleaned = cleaned[cleaned["_quarter"] == str(quarter)]
    elif not keep_all and available:
        cleaned = cleaned[cleaned["_quarter"] == available[-1]]

    cleaned["weight"] = pd.to_numeric(cleaned.get("Weight"), errors="coerce") / 100.0
    cleaned = cleaned.dropna(subset=["weight"])
    if cleaned.empty:
        return pd.DataFrame(columns=COLUMNS)

    rows = []
    for _, row in cleaned.iterrows():
        ticker = str(row.get("StockCode", "") or "").strip()
        market, currency = market_and_currency(ticker)
        label = str(row.get("_quarter") or "")
        end = quarter_end(label) if label else None
        # 真實披露日無法取得 → 季末 + 21 天推算，並標記為 estimated
        disclosed = end + timedelta(days=QDII_DISCLOSURE_LAG_DAYS) if end else None
        rows.append({
            "ticker": ticker,
            "name": str(row.get("Stock", "") or "").strip(),
            "weight": float(row["weight"]),
            "currency_hint": currency,
            "market": market,
            "quarter": label,
            "holding_date": end.isoformat() if end else "",
            "disclosure_date": disclosed.isoformat() if disclosed else "",
            "disclosure_date_estimated": True,
            "asset_type": _source_asset_type(row),
        })

    out = pd.DataFrame(rows, columns=COLUMNS)
    out = out[out["weight"] > 0]
    out = out.sort_values(["quarter", "weight"], ascending=[True, False])
    return out.reset_index(drop=True)


def select_point_in_time(holdings: pd.DataFrame, target_date) -> dict | None:
    """The newest portfolio that was **public** on ``target_date``.

    A quarter enters the estimate only when both its ``holding_date`` and its
    ``disclosure_date`` are on or before the target NAV date; otherwise the
    estimate would use information the market did not have yet (look-ahead
    bias).  Returns ``None`` when no disclosed quarter qualifies.
    """
    if holdings is None or holdings.empty or "disclosure_date" not in holdings.columns:
        return None
    frame = holdings.copy()
    frame["_disc"] = pd.to_datetime(frame["disclosure_date"], errors="coerce")
    if "holding_date" in frame.columns:
        frame["_hold"] = pd.to_datetime(frame["holding_date"], errors="coerce")
    else:
        frame["_hold"] = frame["_disc"]

    limit = pd.Timestamp(to_date(target_date))
    usable = frame[
        frame["_disc"].notna() & (frame["_disc"] <= limit)
        & frame["_hold"].notna() & (frame["_hold"] <= limit)
    ]
    if usable.empty:
        return None

    latest = usable["_hold"].max()
    picked = usable[usable["_hold"] == latest].drop(columns=["_disc", "_hold"])
    picked = picked.reset_index(drop=True)
    estimated = True
    if "disclosure_date_estimated" in picked.columns:
        flags = picked["disclosure_date_estimated"].astype(str).str.strip().str.lower()
        estimated = bool(flags.isin(["true", "1", "yes"]).any())
    return {
        "holdings": picked,
        "quarter": _first(picked, "quarter"),
        "holding_date": _first(picked, "holding_date"),
        "disclosure_date": _first(picked, "disclosure_date"),
        "disclosure_date_estimated": estimated,
        "look_ahead_safe": True,
    }


def _cached_holdings(cache_path: Path, fund_code: str, source: str,
                     note: str | None = None) -> dict | None:
    """Read an existing snapshot; ``None`` when missing, unreadable or empty.

    The frame may hold several quarters (imported history, the SQLite layer or
    an earlier run); the newest one is what the estimate uses by default, while
    the older ones stay available to :func:`select_point_in_time`.
    """
    path = Path(cache_path)
    if not path.exists():
        return None
    try:
        cached = read_cache_df(path)
    except Exception as exc:  # noqa: BLE001 - 壞檔就交回 None 讓上層重抓
        log.warning(f"[{fund_code}] QDII 持倉緩存不可用: {exc}")
        return None
    cached["weight"] = pd.to_numeric(cached["weight"], errors="coerce")
    cached = cached.dropna(subset=["weight"])
    if cached.empty:
        return None
    if "asset_type" not in cached.columns:
        # 舊版快取沒有來源聲明的資產類型；補空欄位並提示重抓，而不是替它猜類型。
        cached["asset_type"] = ""
        log.warning(
            f"[{fund_code}] 持倉快取缺少 asset_type 欄位（來源聲明的資產類型），"
            "請以 --no-cache 重新抓取；本次這些持倉會被視為 unknown"
        )
    if note:
        log.warning(f"[{fund_code}] {note}")
    return _assemble(fund_code, cached, source, cache_hit=True)


def _assemble(fund_code: str, holdings: pd.DataFrame, source: str | None,
              *, cache_hit: bool, warnings: list[str] | None = None) -> dict:
    """Package a holdings frame for the estimator / GUI.

    ``holdings_scope`` says how much of the fund the numbers cover (Eastmoney
    only publishes the top ten, so ``top10`` is the normal case), and
    ``history_available`` says whether anything older than the newest quarter is
    on hand -- the two facts a caller needs instead of a vague "complete" flag.
    """
    quarters = _quarters(holdings)
    latest = quarters[-1] if quarters else None
    if latest and "quarter" in holdings.columns:
        newest = holdings[holdings["quarter"].astype(str) == latest]
    else:
        newest = holdings
    scope = "top10" if len(newest) <= 10 else "full_portfolio"
    return {
        "holdings": holdings.reset_index(drop=True),
        "quarters": quarters,
        "quarter": latest,
        "holding_date": _first(newest, "holding_date"),
        "disclosure_date": _first(newest, "disclosure_date"),
        "source": source,
        "cache_hit": cache_hit,
        "holdings_scope": scope,
        "history_available": len(quarters) > 1,
        "warnings": list(warnings or []),
    }


def _empty_result() -> dict:
    return {
        "holdings": pd.DataFrame(columns=COLUMNS),
        "quarters": [],
        "quarter": None, "holding_date": None, "disclosure_date": None,
        "source": None, "cache_hit": False,
        "holdings_scope": None, "history_available": False,
        "warnings": [],
    }


def _store_page(fund_code: str, fetched: pd.DataFrame, cache_path: Path,
                repository) -> dict:
    """Merge the freshly fetched quarter with whatever history is on hand.

    The new quarter must not erase older snapshots that were imported earlier:
    they are the only material a point-in-time estimate has to work with.
    """
    holdings = fetched
    known = _cached_holdings(cache_path, fund_code, "cache")
    if known is not None and not known["holdings"].empty:
        merged = pd.concat([known["holdings"], fetched], ignore_index=True)
        merged = merged.drop_duplicates(subset=["quarter", "ticker"], keep="last")
        holdings = merged.sort_values(
            ["quarter", "weight"], ascending=[True, False]).reset_index(drop=True)
    write_cache_df(holdings, cache_path)
    save_quarters_to_db(repository, fund_code, holdings, source="eastmoney-page")
    result = _assemble(fund_code, holdings, "eastmoney-page", cache_hit=False)
    log.info(f"[{fund_code}] QDII 持倉 {len(holdings)} 條／{len(result['quarters'])} 個季度"
             f"（最新 {result['quarter']}，範圍 {result['holdings_scope']}）"
             f"← eastmoney-page → {cache_path.name}")
    return result


def fetch_qdii_holdings(fund_code: str, save_dir: Path,
                        use_cache: bool = True, repository=None) -> dict:
    """Latest disclosed QDII portfolio for ``fund_code``.

    Latest-only by design.  The Eastmoney archive endpoint that serves older
    quarters is unreachable from many networks, and waiting for it turned one
    bad afternoon into a permanent "history missing" state; the estimator only
    needs the newest public portfolio.  Anything older must come from history
    that was imported or saved earlier (SQLite), and :func:`select_point_in_time`
    uses it when a date-specific estimate asks for it.

    Order: fresh cache → fund home page (one request) → SQLite snapshot →
    older cache.  An empty frame means no portfolio could be obtained.

    Returns ``{"holdings", "quarters", "quarter", "holding_date",
    "disclosure_date", "source", "cache_hit", "holdings_scope",
    "history_available", "warnings"}``.
    """
    cache_path = Path(save_dir) / f"{fund_code}{QDII_HOLDINGS_SUFFIX}"

    if use_cache and cache_path.exists() and is_cache_fresh(cache_path):
        cached = _cached_holdings(cache_path, fund_code, "cache")
        if cached is not None:
            log.info(f"[{fund_code}] QDII 持倉來自緩存: {cache_path.name}")
            return cached

    page_raw = fetch_holdings_page(fund_code)
    if page_raw is not None and not page_raw.empty:
        fetched = build_qdii_holdings(page_raw)
        if not fetched.empty:
            return _store_page(fund_code, fetched, cache_path, repository)

    db_frame = load_quarters_from_db(repository, fund_code)
    if not db_frame.empty:
        log.info(f"[{fund_code}] 網絡不可用，改用資料庫中的 QDII 持倉快照")
        return _assemble(fund_code, db_frame, "database", cache_hit=False)

    stale = _cached_holdings(cache_path, fund_code, "cache-stale",
                             note="QDII 持倉無法更新，改用本機較舊快照")
    if stale is not None:
        return stale

    log.warning(f"[{fund_code}] 無法取得 QDII 持倉")
    return _empty_result()


def _quarters(frame: pd.DataFrame) -> list[str]:
    if frame is None or frame.empty or "quarter" not in frame.columns:
        return []
    return sorted({str(q) for q in frame["quarter"] if str(q).strip()})


def _first(frame: pd.DataFrame, column: str):
    if column not in frame.columns or frame.empty:
        return None
    value = frame.iloc[0][column]
    return None if pd.isna(value) else str(value)
