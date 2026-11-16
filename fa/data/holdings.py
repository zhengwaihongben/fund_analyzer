"""Quarterly holdings download, cleaning and top-N selection."""

from datetime import datetime
from pathlib import Path
import re
import time

import akshare as ak
import pandas as pd
import requests

from fa.config import CACHE_TTL_DAYS, REQUEST_TIMEOUT
from fa.errors import CacheError
from fa.utils.cache import cache_age_days, is_cache_fresh, read_cache_df, write_cache_df
from fa.utils.dates import latest_quarter_label
from fa.utils.logging_setup import get_logger
from fa.utils.net import describe_network_error


log = get_logger(__name__)

_HOLDINGS_ATTEMPTS = 3       # 每一年最多嘗試次數
_HOLDINGS_BACKOFF = 2.0      # 第一次重試前等待秒數（之後翻倍）

# 基金檔案 CDN（fundf10）故障時，首頁仍可讀到最新季度前十大持倉
_PAGE_URL = "https://fund.eastmoney.com/{code}.html"
_PAGE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Referer": "https://fund.eastmoney.com/",
}
_PAGE_END_RE = re.compile(r"持仓截止日期:\s*(\d{4})-(\d{2})-(\d{2})")

# 檔案接口（某一年）連續失敗後，只在冷卻期內跳過「該年度」，
# 不能因為 2025 年失敗就跳過同一輪的 2026 年。
_ARCHIVES_COOLDOWN = 600.0
_archives_down_until: dict[int, float] = {}


def reset_archives_cooldown() -> None:
    """Clear the per-year archive cooldown (used by tests and by ``--no-cache``)."""
    _archives_down_until.clear()


def _archive_cooling_down(fund_code: str, year: str) -> bool:
    until = _archives_down_until.get(int(year), 0.0)
    if time.monotonic() < until:
        log.debug(f"[{fund_code}] 檔案接口冷卻中，跳過 {year} 年持倉")
        return True
    return False


def _fetch_year_holdings(fund_code: str, year: str,
                         attempts: int = _HOLDINGS_ATTEMPTS):
    """抓取單一年度持倉，遇到暫時性網絡錯誤時退避重試。

    eastmoney 的 CDN 偶爾會在 TLS 握手階段直接斷開（SSLEOFError），
    這類錯誤通常幾秒後就恢復，因此重試比直接放棄更有意義。
    """
    if _archive_cooling_down(fund_code, year):
        return None

    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            df = ak.fund_portfolio_hold_em(symbol=fund_code, date=year)
        except Exception as e:  # noqa: BLE001 - 交給重試處理
            last_error = e
            if attempt < attempts:
                wait = _HOLDINGS_BACKOFF * (2 ** (attempt - 1))
                log.debug(f"[{fund_code}] {year} 年持仓获取失败"
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

    reason = (describe_network_error(last_error)
              if isinstance(last_error, BaseException) else str(last_error))
    log.warning(f"[{fund_code}] {year} 年持仓获取失败（已重試 {attempts} 次）: {reason}")
    if isinstance(last_error, BaseException):
        log.debug(f"[{fund_code}] {year} 年持仓原始錯誤: {last_error}")
    _archives_down_until[int(year)] = time.monotonic() + _ARCHIVES_COOLDOWN
    return None


def _page_quarter_label(text: str) -> str:
    """從首頁的「持仓截止日期」推導季度標籤。"""
    match = _PAGE_END_RE.search(text)
    if not match:
        return "未註明季度"
    year, month, _day = (int(part) for part in match.groups())
    quarter = (month - 1) // 3 + 1
    return f"{year}年{quarter}季度股票投资明细"


def fetch_holdings_page(fund_code: str) -> pd.DataFrame:
    """最新季度前十大持倉（基金首頁備援）。

    檔案接口（fundf10）不可用時，``fund.eastmoney.com/<code>.html`` 仍會
    直接輸出最新披露季度的前十大持倉表格，可用來維持分析與 QDII 估算。
    回傳欄位與 akshare 原始資料一致（股票代码/股票名称/占净值比例/季度）。
    """
    url = _PAGE_URL.format(code=fund_code)
    try:
        resp = requests.get(url, headers=_PAGE_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - 備援路徑失敗就交回空資料
        log.warning(f"[{fund_code}] 基金首頁持倉讀取失敗: {exc}")
        return pd.DataFrame()

    text = resp.content.decode("utf-8", "replace")
    quarter = _page_quarter_label(text)
    rows = []
    for chunk in text.split("<tr")[1:]:
        chunk = chunk.split("</tr>")[0]
        code_match = re.search(r'stockcode="stock_([^"]+)"', chunk)
        weight_match = re.search(r'<td class="alignRight bold">([\d.]+)%</td>', chunk)
        if not (code_match and weight_match):
            continue
        name_match = re.search(r'title="([^"]+)"', chunk)
        rows.append({
            "股票代码": code_match.group(1).strip(),
            "股票名称": name_match.group(1).strip() if name_match else "",
            "占净值比例": float(weight_match.group(1)),
            "季度": quarter,
        })

    if not rows:
        log.warning(f"[{fund_code}] 基金首頁沒有持倉表格")
        return pd.DataFrame()

    frame = pd.DataFrame(rows)
    log.info(f"[{fund_code}] 改由基金首頁取得持倉 {len(frame)} 條（{quarter}）")
    return frame


def read_stale_cache(cache_path: Path, fund_code: str) -> pd.DataFrame | None:
    """讀取任何存在（即使過期）的持倉快照；讀不到回傳 None。"""
    path = Path(cache_path)
    if not path.exists():
        return None
    try:
        frame = read_cache_df(path)
    except CacheError as exc:
        log.warning(f"[{fund_code}] 舊持倉快照不可讀: {exc}")
        return None
    if frame is None or frame.empty:
        return None
    log.warning(f"[{fund_code}] 網絡不可用，改用 {cache_age_days(path):.1f} 天前的持倉快照: "
                f"{path.name}")
    return frame



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

    log.info(f"[{fund_code}] 获取最新季度持仓 ...")

    # 只輸出最新季度持倉：基金首頁一次請求就能拿到最新季度前十大，先走這條快路徑
    page_df = fetch_holdings_page(fund_code)
    if page_df is not None and not page_df.empty:
        write_cache_df(page_df, cache_path)
        log.info(f"[{fund_code}] 持仓來自主頁（最新季度前十大）→ {cache_path.name}")
        return page_df

    log.info(f"[{fund_code}] 首頁持倉不可用，改試檔案接口 ...")
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
        stale = read_stale_cache(cache_path, fund_code)
        if stale is not None:
            return stale

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
