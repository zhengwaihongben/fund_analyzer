"""Daily NAV history for a fund.

Primary source: the ``pingzhongdata`` JS blob served by fund.eastmoney.com.
It is parsed directly with a regular expression + ``json`` instead of going
through ``akshare``/``py_mini_racer``.  Two reasons:

* the V8 isolate created by ``py_mini_racer`` is not thread safe and has been
  observed to abort the whole process (0xC06D007F) when it is finalized by the
  garbage collector on a different thread than the one that created it -- which
  is exactly what happens inside the GUI worker thread;
* it is much faster (one HTTP request, no JS engine).

``akshare`` remains as a fallback in case the blob format ever changes.
"""

from pathlib import Path
import json
import re

import akshare as ak
import pandas as pd
import requests

from fa.config import REQUEST_TIMEOUT
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

_PINGZHONG_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
_BROWSER_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://fund.eastmoney.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
}
_NET_WORTH_RE = re.compile(
    r"var\s+Data_netWorthTrend\s*=\s*(\[.*?\])\s*;", re.DOTALL
)
_JS_KEY_RE = re.compile(r"([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:")


def _parse_net_worth_js(text: str) -> pd.DataFrame:
    """Parse ``Data_netWorthTrend`` out of a pingzhongdata JS blob."""
    match = _NET_WORTH_RE.search(text)
    if not match:
        raise ValueError("pingzhongdata 中找不到 Data_netWorthTrend")

    literal = match.group(1)
    # {x:1746000000000,y:1.2345,...} -> {"x":1746000000000,"y":1.2345,...}
    literal = _JS_KEY_RE.sub(r'\1"\2":', literal)
    records = json.loads(literal)
    if not records:
        return pd.DataFrame()

    dates = pd.to_datetime(
        [rec.get("x") for rec in records], unit="ms", utc=True
    ).tz_convert("Asia/Shanghai").date
    df = pd.DataFrame({
        "净值日期": dates,
        "单位净值": [rec.get("y") for rec in records],
        "日增长率": [rec.get("equityReturn") for rec in records],
    })
    return df


def _fetch_nav_direct(fund_code: str) -> pd.DataFrame:
    """Fetch the NAV history without akshare/py_mini_racer."""
    resp = requests.get(
        _PINGZHONG_URL.format(code=fund_code),
        headers=_BROWSER_HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return _parse_net_worth_js(resp.text)


def _fetch_nav_akshare(fund_code: str) -> pd.DataFrame:
    return ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")


def _load_raw_nav(fund_code: str) -> pd.DataFrame:
    try:
        raw = _fetch_nav_direct(fund_code)
        log.debug(f"[{fund_code}] 直接解析 pingzhongdata 成功")
        return raw
    except Exception as e:  # noqa: BLE001 - fall back to akshare
        log.warning(f"[{fund_code}] 直接解析净值失败（{e}），改用 akshare")

    try:
        return _fetch_nav_akshare(fund_code)
    except Exception as e:
        log.error(f"[{fund_code}] 净值获取失败: {e}")
        return pd.DataFrame()


def fetch_nav(fund_code: str, save_dir: Path,
              start_date: str = None) -> pd.DataFrame:
    log.info(f"[{fund_code}] 获取历史净值 ...")
    raw = _load_raw_nav(fund_code)

    if raw is None or raw.empty:
        log.warning(f"[{fund_code}] 净值数据为空")
        return pd.DataFrame()

    # ---------- 清理列名：去 BOM、去首尾空格 ----------
    raw.columns = [str(c).replace("\ufeff", "").strip() for c in raw.columns]
    log.debug(f"[{fund_code}] 原始列名: {raw.columns.tolist()}")

    # ---------- 日期列 ----------
    date_col = None
    for cand in ["净值日期", "日期", "date", "Date"]:
        if cand in raw.columns:
            date_col = cand
            break
    if date_col is None:
        date_col = raw.columns[0]
        log.warning(f"[{fund_code}] 未找到日期列，使用第一列: {date_col}")

    raw = raw.rename(columns={date_col: "Date"})

    # ---------- 净值列：按优先级匹配 ----------
    nav_candidates = ["单位净值", "单位净值走势", "净值", "NAV", "nav"]
    nav_col = None
    for cand in nav_candidates:
        if cand in raw.columns:
            nav_col = cand
            break
    if nav_col is None:
        numeric_cols = [c for c in raw.columns
                        if c != "Date" and pd.api.types.is_numeric_dtype(raw[c])]
        if numeric_cols:
            nav_col = numeric_cols[0]
            log.warning(f"[{fund_code}] 未匹配到净值列名，使用: {nav_col}")
        else:
            log.error(f"[{fund_code}] 无法识别净值列，实际列名: {raw.columns.tolist()}")
            return pd.DataFrame()

    raw = raw.rename(columns={nav_col: "NAV"})

    # ---------- 日增长率（可选） ----------
    for cand in ["日增长率", "增长率", "DailyReturn"]:
        if cand in raw.columns:
            raw = raw.rename(columns={cand: "DailyReturn"})
            break

    # ---------- 类型转换 ----------
    raw["Date"] = pd.to_datetime(raw["Date"], errors="coerce")
    raw["NAV"] = pd.to_numeric(raw["NAV"], errors="coerce")
    raw = raw.dropna(subset=["Date", "NAV"]).sort_values("Date").reset_index(drop=True)

    # ---------- 按起始日期筛选 ----------
    if start_date:
        try:
            sd = pd.to_datetime(start_date)
            before = len(raw)
            raw = raw[raw["Date"] >= sd].reset_index(drop=True)
            log.info(f"[{fund_code}] 按起始日期 {start_date} 筛选: {before} → {len(raw)} 条")
            if raw.empty:
                log.error(f"[{fund_code}] 起始日期 {start_date} 之后无数据，"
                          f"请检查日期是否晚于基金成立日")
                return pd.DataFrame()
        except Exception as e:
            log.warning(f"[{fund_code}] 起始日期解析失败 '{start_date}': {e}，使用全部数据")

    out = save_dir / f"{fund_code}_daily_nav.csv"
    raw.to_csv(out, index=False, encoding="utf-8-sig")
    log.info(f"[{fund_code}] 净值 {len(raw)} 条 → {out.name}")
    return raw


def load_nav_csv(fund_code: str, save_dir) -> pd.DataFrame:
    """Official NAV series (Date/NAV); falls back to a fresh download."""
    save_dir = Path(save_dir)
    path = save_dir / f"{fund_code}_daily_nav.csv"
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

    frame = fetch_nav(fund_code, save_dir)
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["Date", "NAV"])
    frame = frame.copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame["NAV"] = pd.to_numeric(frame["NAV"], errors="coerce")
    return frame.dropna(subset=["Date", "NAV"]).sort_values("Date").reset_index(drop=True)
