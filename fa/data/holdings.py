"""Quarterly holdings download, cleaning and top-N selection."""

from datetime import datetime
from pathlib import Path
import re
import time

import akshare as ak
import pandas as pd

from fa.config import CACHE_TTL_DAYS
from fa.errors import CacheError
from fa.utils.cache import cache_age_days, is_cache_fresh, read_cache_df, write_cache_df
from fa.utils.dates import latest_quarter_label
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

_HOLDINGS_ATTEMPTS = 3       # 每一年最多嘗試次數
_HOLDINGS_BACKOFF = 2.0      # 第一次重試前等待秒數（之後翻倍）


def _fetch_year_holdings(fund_code: str, year: str,
                         attempts: int = _HOLDINGS_ATTEMPTS):
    """抓取單一年度持倉，遇到暫時性網絡錯誤時退避重試。

    eastmoney 的 CDN 偶爾會在 TLS 握手階段直接斷開（SSLEOFError），
    這類錯誤通常幾秒後就恢復，因此重試比直接放棄更有意義。
    """
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            df = ak.fund_portfolio_hold_em(symbol=fund_code, date=year)
        except Exception as e:  # noqa: BLE001 - 交給重試處理
            last_error = e
            if attempt < attempts:
                wait = _HOLDINGS_BACKOFF * (2 ** (attempt - 1))
                log.warning(f"[{fund_code}] {year} 年持仓获取失败"
                            f"（第 {attempt}/{attempts} 次，{wait:.0f}s 後重試）: {e}")
                time.sleep(wait)
            continue

        if df is not None and not df.empty:
            log.info(f"[{fund_code}] {year} 年持仓: {len(df)} 条")
            return df
        last_error = "接口返回空数据"
        if attempt < attempts:
            wait = _HOLDINGS_BACKOFF * (2 ** (attempt - 1))
            log.warning(f"[{fund_code}] {year} 年持仓为空"
                        f"（第 {attempt}/{attempts} 次，{wait:.0f}s 後重試）")
            time.sleep(wait)

    log.warning(f"[{fund_code}] {year} 年持仓获取失败（已重試 {attempts} 次）: {last_error}")
    return None


def top_holdings(df_holdings: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """取最新季度、按 Weight 降序的前 n 大持倉。"""
    if df_holdings is None or df_holdings.empty:
        return pd.DataFrame()

    latest_q = latest_quarter_label(df_holdings)
    if latest_q is None:
        return df_holdings.head(n).reset_index(drop=True)

    sub = df_holdings[df_holdings["QuarterLabel"].astype(str) == latest_q].copy()
    if "Weight" in sub.columns:
        sub["Weight"] = pd.to_numeric(sub["Weight"], errors="coerce")
        sub = sub.dropna(subset=["Weight"]).nlargest(n, "Weight")
    else:
        sub = sub.head(n)
    return sub.reset_index(drop=True)


def fetch_holdings(fund_code: str, save_dir: Path, use_cache=True, start_date=None) -> pd.DataFrame:
    cache_path = save_dir / f"{fund_code}_holdings_raw.csv"
    if use_cache and cache_path.exists():
        if is_cache_fresh(cache_path):
            try:
                cached = read_cache_df(cache_path)
                log.info(f"[{fund_code}] 從緩存讀取持倉"
                         f"（{cache_age_days(cache_path):.1f} 天）: {cache_path.name}")
                return cached
            except CacheError as exc:
                log.warning(f"[{fund_code}] 持倉緩存不可用，重新拉取: {exc}")
        else:
            log.info(f"[{fund_code}] 持倉緩存已超過 {CACHE_TTL_DAYS} 天，重新拉取")

    log.info(f"[{fund_code}] 获取历史持仓 ...")
    current_year = datetime.now().year
    
    # 根据 start_date 决定要抓取哪几年
    if start_date:
        try:
            start_year = pd.to_datetime(start_date).year
            start_year = max(start_year, current_year - 3)  # 最多往前推3年防接口抽风
            years = [str(y) for y in range(start_year, current_year + 1)]
        except Exception as e:
            log.warning(f"[{fund_code}] 起始日期解析失败: {e}，默认拉取近4年持仓")
            years = [str(y) for y in range(current_year - 3, current_year + 1)]
    else:
        years = [str(y) for y in range(current_year - 3, current_year + 1)]

    log.info(f"[{fund_code}] 需抓取年份: {years}")

    all_holdings = []
    for year in years:
        df = _fetch_year_holdings(fund_code, year)
        if df is not None and not df.empty:
            all_holdings.append(df)

    if not all_holdings:
        log.warning(f"[{fund_code}] 无持仓数据")
        return pd.DataFrame()

    holdings = pd.concat(all_holdings, ignore_index=True)
    write_cache_df(holdings, cache_path)
    log.info(f"[{fund_code}] 持仓 {len(holdings)} 条 → {cache_path.name}")
    return holdings


def _clean_holdings(holdings: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "股票代码": "StockCode", "股票名称": "Stock",
        "占净值比例": "Weight", "持股数": "Shares",
        "持仓市值": "MarketValue", "季度": "Quarter",
    }
    holdings = holdings.rename(
        columns={k: v for k, v in rename_map.items() if k in holdings.columns}
    )
    if "Weight" in holdings.columns:
        holdings["Weight"] = holdings["Weight"].astype(str).str.replace("%", "", regex=False)
        holdings["Weight"] = pd.to_numeric(holdings["Weight"], errors="coerce")

    def to_quarter_label(s):
        m = re.search(r"(\d{4})\D*?(\d)\s*季度", str(s))
        return f"{m.group(1)}Q{m.group(2)}" if m else str(s)

    if "Quarter" in holdings.columns:
        holdings["QuarterLabel"] = holdings["Quarter"].apply(to_quarter_label)
        qorder = sorted(holdings["QuarterLabel"].unique())
        holdings["QuarterLabel"] = pd.Categorical(
            holdings["QuarterLabel"], categories=qorder, ordered=True
        )
    return holdings
