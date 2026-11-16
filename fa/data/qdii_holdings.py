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

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from fa.data.holdings import _clean_holdings, _fetch_year_holdings, fetch_holdings_page
from fa.data.qdii_market import SYMBOL_OVERRIDES
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
]

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
    """把既有快照讀成回傳結構；讀不到或為空回傳 ``None``。"""
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
    if note:
        log.warning(f"[{fund_code}] {note}")
    latest = _latest_quarter(cached)
    if latest and "quarter" in cached.columns:
        newest = cached[cached["quarter"].astype(str) == latest]
    else:
        newest = cached
    return {
        "holdings": cached.reset_index(drop=True),
        "quarters": _quarters(cached),
        "quarter": latest,
        "holding_date": _first(newest, "holding_date"),
        "disclosure_date": _first(newest, "disclosure_date"),
        "source": source,
        "cache_hit": True,
    }


def fetch_qdii_holdings(fund_code: str, save_dir: Path,
                        use_cache: bool = True, keep_all: bool = False) -> dict:
    """Disclosed QDII portfolio for ``fund_code``.

    Returns ``{"holdings": DataFrame, "quarters", "quarter", "holding_date",
    "disclosure_date", "source", "cache_hit"}``.  An empty frame means the
    portfolio could not be obtained; the estimator turns that into a
    ``status="insufficient-data"`` result instead of inventing weights.

    Fallback order: fresh cache → fund home page (latest quarter, top 10) →
    archives API → any older snapshot on disk.  With ``keep_all=True`` every
    disclosed quarter is kept and the archives are tried first, because an
    older target date needs quarters the home page does not show.
    """
    cache_path = Path(save_dir) / f"{fund_code}{QDII_HOLDINGS_SUFFIX}"
    empty = {
        "holdings": pd.DataFrame(columns=COLUMNS),
        "quarters": [],
        "quarter": None, "holding_date": None, "disclosure_date": None,
        "source": None, "cache_hit": False,
    }

    if use_cache and cache_path.exists() and is_cache_fresh(cache_path):
        cached = _cached_holdings(cache_path, fund_code, "cache")
        if cached is not None and (not keep_all or cached["quarters"]):
            log.info(f"[{fund_code}] QDII 持倉來自緩存: {cache_path.name}")
            return cached

    holdings = pd.DataFrame(columns=COLUMNS)
    source = None

    if keep_all:
        holdings, source = _load_quarters(fund_code)
        if holdings.empty:
            page_raw = fetch_holdings_page(fund_code)
            if page_raw is not None and not page_raw.empty:
                holdings = build_qdii_holdings(page_raw)
                if not holdings.empty:
                    source = "eastmoney-page"
    else:
        # 只需要最新季度：首頁一次請求即可，先走快路徑
        page_raw = fetch_holdings_page(fund_code)
        if page_raw is not None and not page_raw.empty:
            holdings = build_qdii_holdings(page_raw)
            if not holdings.empty:
                source = "eastmoney-page"
        if holdings.empty:
            holdings, source = _load_quarters(fund_code)

    if holdings.empty:
        stale = _cached_holdings(
            cache_path, fund_code, "cache-stale",
            note=f"QDII 持倉無法更新，改用較舊快照: {cache_path.name}")
        if stale is not None:
            return stale
        log.warning(f"[{fund_code}] 無法取得 QDII 持倉")
        return empty

    if keep_all:
        holdings = _merge_quarters(holdings, _cached_holdings(cache_path, fund_code, "cache"))
    write_cache_df(holdings, cache_path)
    latest = _latest_quarter(holdings)
    newest = holdings[holdings["quarter"].astype(str) == latest] if latest else holdings
    log.info(f"[{fund_code}] QDII 持倉 {len(holdings)} 條／{len(_quarters(holdings))} 個季度"
             f"（最新 {latest}）← {source} → {cache_path.name}")
    return {
        "holdings": holdings,
        "quarters": _quarters(holdings),
        "quarter": latest,
        "holding_date": _first(newest, "holding_date"),
        "disclosure_date": _first(newest, "disclosure_date"),
        "source": source,
        "cache_hit": False,
    }


def _load_quarters(fund_code: str) -> tuple[pd.DataFrame, str | None]:
    """Fetch every archived quarter (slow path, one request per year)."""
    from datetime import datetime
    years = [str(y) for y in range(datetime.now().year - 1, datetime.now().year + 1)]
    frames = []
    for year in years:
        frame = _fetch_year_holdings(fund_code, year)
        if frame is not None and not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=COLUMNS), None
    return build_qdii_holdings(pd.concat(frames, ignore_index=True), keep_all=True), \
        "eastmoney-archives"


def _merge_quarters(fetched: pd.DataFrame, cached: dict | None) -> pd.DataFrame:
    """Union of the fetched quarters and the cached ones (fetched wins)."""
    if not cached or cached.get("holdings") is None or cached["holdings"].empty:
        return fetched
    merged = pd.concat([cached["holdings"], fetched], ignore_index=True)
    merged = merged.drop_duplicates(subset=["quarter", "ticker"], keep="last")
    merged = merged.sort_values(["quarter", "weight"], ascending=[True, False])
    return merged.reset_index(drop=True)


def _quarters(frame: pd.DataFrame) -> list[str]:
    if frame is None or frame.empty or "quarter" not in frame.columns:
        return []
    return sorted({str(q) for q in frame["quarter"] if str(q).strip()})


def _latest_quarter(frame: pd.DataFrame) -> str | None:
    quarters = _quarters(frame)
    return quarters[-1] if quarters else None


def _first(frame: pd.DataFrame, column: str):
    if column not in frame.columns or frame.empty:
        return None
    value = frame.iloc[0][column]
    return None if pd.isna(value) else str(value)
