"""Benchmark index history (US / CN / HK)."""

import akshare as ak
import pandas as pd

from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

def fetch_benchmark(symbol: str, market: str = "auto") -> pd.DataFrame:
    """
    统一获取基准指数数据。
    返回 DataFrame 带两列：Date (datetime)、BenchClose (float)。
    失败时返回空 DataFrame。

    market 参数：
        "auto" — 根据 symbol 格式自动判断
        "us"   — 美股（新浪接口），symbol 以 . 开头，如 .NDX
        "cn"   — A股（东财接口），symbol 形如 sh000300
        "hk"   — 港股（东财接口），symbol 形如 HSI
    """
    if not symbol:
        return pd.DataFrame()

    symbol = symbol.strip()

    # ---------- 自动判断市场 ----------
    if market == "auto":
        if symbol.startswith("."):
            market = "us"
        elif symbol[:2].lower() in ("sh", "sz", "bj"):
            market = "cn"
        else:
            market = "hk"

    log.info(f"获取基准指数: {symbol} (市场: {market})")

    try:
        if market == "us":
            df = ak.index_us_stock_sina(symbol=symbol)
            df = df.rename(columns={"date": "Date", "close": "BenchClose"})
        elif market == "cn":
            df = ak.stock_zh_index_daily_em(symbol=symbol)
            df = df.rename(columns={"date": "Date", "close": "BenchClose"})
        elif market == "hk":
            df = ak.stock_hk_index_daily_em(symbol=symbol)
            df = df.rename(columns={"date": "Date", "close": "BenchClose"})
        else:
            log.warning(f"未知市场类型: {market}")
            return pd.DataFrame()
    except Exception as e:
        log.warning(f"基准 {symbol} 获取失败: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        log.warning(f"基准 {symbol} 返回空数据")
        return pd.DataFrame()

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["BenchClose"] = pd.to_numeric(df["BenchClose"], errors="coerce")
    df = df.dropna(subset=["Date", "BenchClose"]).sort_values("Date").reset_index(drop=True)
    return df[["Date", "BenchClose"]]
