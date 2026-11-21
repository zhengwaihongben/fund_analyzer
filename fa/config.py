# -*- coding: utf-8 -*-
"""Central configuration: defaults, colours, paths, cache policy.

Everything that used to be scattered as module-level constants in
``fund_analyzer.py`` lives here. Callers may still pass a plain ``dict``
config (the public API keeps that contract), but the defaults and the
environment overrides have exactly one home now.
"""
from __future__ import annotations

import os
from pathlib import Path

# 專案根目錄（fa/ 的上一層）。所有相對路徑都以此為準，與進程 cwd 無關。
PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CONFIG = {
    "risk_free":    0.02,
    "trading_days": 252,
    "sma_windows":  [5, 10, 60, 120, 250],
    "boll_n":       20,
    "boll_k":       2,
    "benchmark":    ".NDX",
    "benchmark_market": "auto",
    "start_date":   None,
    # 基金類型：auto = 依基金資料自動判斷；standard / qdii = 強制
    "fund_type":    "auto",
}

COLOR_MAP = {
    "NAV":    "#2b2b2b",
    "SMA5":   "#7fb3d5",
    "SMA10":  "#f5b97f",
    "SMA60":  "#8fc98f",
    "SMA120": "#e88b8b",
    "SMA250": "#b3a3d4",
}
BOLL_LINE_COLOR = "rgba(200, 120, 120, 0.75)"
BOLL_FILL_COLOR = "rgba(200, 120, 120, 0.07)"
BOLL_MID_COLOR  = "#7fb3d5"

# --- 輸出 / 緩存 / 網絡 ----------------------------------------------------
DEFAULT_OUTPUT_DIR = "output"

# 持倉等衍生數據的緩存有效期（天）。0 表示永不過期。
CACHE_TTL_DAYS = int(os.environ.get("FA_CACHE_TTL_DAYS", "7"))
# 單個 HTTP / yfinance 請求的超時（秒）
REQUEST_TIMEOUT = float(os.environ.get("FA_REQUEST_TIMEOUT", "30"))
# 日誌文件；設為空字符串可關閉文件日誌
LOG_FILE = os.environ.get("FA_LOG_FILE", str(PROJECT_ROOT / "fund_analyzer.log"))

# --- V2 價格對齊 ------------------------------------------------------------
# 落後幾個交易日以上開始標記為「過期」。預設 0：只要不是目標日當天就降級並警告；
# 真正的取捨由 EXCLUDE 決定（1..EXCLUDE-1 保留、>=EXCLUDE 排除）。
STALE_SESSIONS_WARN = int(os.environ.get("FA_STALE_SESSIONS_WARN", "0"))
# 落後達到這個交易日數就排除（不計入覆蓋率）；與 WARN 一起構成 V2 的三段政策
# 0 = 正常、1..EXCLUDE-1 = 輕微過期保留、>=EXCLUDE = 排除、>MAX = 嚴重過期。
STALE_SESSIONS_EXCLUDE = int(os.environ.get("FA_STALE_SESSIONS_EXCLUDE", "3"))
# 超過這個交易日數視為嚴重過期（日誌級別更高，同樣排除）。
STALE_SESSIONS_MAX = int(os.environ.get("FA_STALE_SESSIONS_MAX", "10"))

# 現金利率缺失時使用的年度假設利率（0 表示仍按 0%，但一定留下警告）。
CASH_ASSUMPTION_RATE = float(os.environ.get("FA_CASH_ASSUMPTION_RATE", "0.0"))

# --- V2.3.1 修訂帳（FX / 價格） ---------------------------------------------
# 同一個日期、同一個基準，重新下載到的數字相差超過這個絕對容差，才算「舊版本被
# 修正」，並寫入 *_revisions 帳。單純的浮點雜訊（約 1e-15）不會觸發。
PRICE_REVISION_TOLERANCE = float(os.environ.get("FA_PRICE_REVISION_TOLERANCE", "1e-8"))
# 相鄰兩筆價格/匯率變動超過這個比例時接受但標記為 anomaly（不靜默丟棄）。
PRICE_JUMP_WARN = float(os.environ.get("FA_PRICE_JUMP_WARN", "0.50"))

# --- V2.3.1 Stage 2 匯率管線 -------------------------------------------------
# Yahoo 的匯率日線時間戳固定在 Europe/London 的 00:00；BST 期間它是前一個 UTC 日
# 的 23:00Z，所以交易日必須用倫敦日期判定，不能用 UTC 日期。
FX_TIMEZONE = os.environ.get("FA_FX_TIMEZONE", "Europe/London")
# 匯率日線在交易日結束後多久才視為可用（Yahoo 沒有公布時間，只能推算並標記
# availability_source = inferred）。
FX_AVAILABILITY_LAG_MINUTES = int(os.environ.get("FA_FX_AVAILABILITY_LAG_MINUTES", "30"))
# 增量更新時，從資料庫最後一筆再往前多抓幾個交易日的重疊窗口，用來發現 Yahoo
# 對歷史數字的修訂；只補尾巴會永遠看不到舊日期被改。
FX_OVERLAP_SESSIONS = int(os.environ.get("FA_FX_OVERLAP_SESSIONS", "5"))
# 重疊窗口換算成日曆天（含週末）。5 個交易日最長會跨 9 個日曆天，取 12 留餘量。
FX_OVERLAP_CALENDAR_DAYS = int(os.environ.get("FA_FX_OVERLAP_CALENDAR_DAYS", "12"))
# 沒有指定 start 時，預設往回抓幾年的匯率歷史。
FX_DEFAULT_YEARS = int(os.environ.get("FA_FX_DEFAULT_YEARS", "2"))

# --- V2.3.1 Stage 2.5 匯率接入 ------------------------------------------------
# QDII 估算是否改走 FX pipeline（寫入 fx_rates 後用 fx_as_of 讀 PIT 匯率）。
# 設 FA_FX_PIPELINE=0 可回退到舊的 fetch_fx_rates() 路徑。
FX_PIPELINE_ENABLED = os.environ.get("FA_FX_PIPELINE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)


# --- V2.3.2 Stage A 價格 PIT 基礎 ---------------------------------------------
# 日線 bar 的可得時間 = 該交易日收盤 + 此延遲（與匯率一致，避免同一份回測用兩套規則）。
PRICE_AVAILABILITY_LAG_MINUTES = int(
    os.environ.get("FA_PRICE_AVAILABILITY_LAG_MINUTES", "30")
)
# 增量更新時回補的重疊視窗：交易日數與日曆日天數取較早者，用來抓供應商修訂。
PRICE_OVERLAP_SESSIONS = int(os.environ.get("FA_PRICE_OVERLAP_SESSIONS", "5"))
PRICE_OVERLAP_CALENDAR_DAYS = int(os.environ.get("FA_PRICE_OVERLAP_CALENDAR_DAYS", "12"))
# 首次下載的預設年數（`--years` 可覆寫）。
PRICE_DEFAULT_YEARS = int(os.environ.get("FA_PRICE_DEFAULT_YEARS", "2"))


# --- V2.3.2 Stage D 回測執行參數 ---------------------------------------------
# runner.py 在每次回測開始時讀取一次並往下傳；engine.py 不讀環境變數。
BACKTEST_COMMISSION_BPS = float(os.environ.get("FA_BACKTEST_COMMISSION_BPS", "5.0"))
BACKTEST_MIN_COMMISSION = float(os.environ.get("FA_BACKTEST_MIN_COMMISSION", "0.0"))
BACKTEST_SLIPPAGE_BPS = float(os.environ.get("FA_BACKTEST_SLIPPAGE_BPS", "10.0"))
BACKTEST_FX_SPREAD_BPS = float(os.environ.get("FA_BACKTEST_FX_SPREAD_BPS", "20.0"))
BACKTEST_RISK_FREE_RATE = float(os.environ.get("FA_BACKTEST_RISK_FREE_RATE", "0.02"))
BACKTEST_DECISION_LAG_MINUTES = int(os.environ.get("FA_BACKTEST_DECISION_LAG_MINUTES", "30"))
BACKTEST_EXECUTION_LAG_SESSIONS = int(os.environ.get("FA_BACKTEST_EXECUTION_LAG_SESSIONS", "1"))
BACKTEST_FRACTIONAL_SHARES = os.environ.get(
    "FA_BACKTEST_FRACTIONAL_SHARES", "1").strip().lower() not in ("0", "false", "no", "off")


def merge_config(cfg: dict | None = None) -> dict:
    """Return ``DEFAULT_CONFIG`` overlaid with every key present in ``cfg``.

    An explicit ``None`` in ``cfg`` is kept on purpose: ``--benchmark ""``
    reaches the pipeline as ``{"benchmark": None}`` and must disable the
    benchmark, not silently fall back to ``.NDX``.
    """
    merged = dict(DEFAULT_CONFIG)
    merged["sma_windows"] = list(DEFAULT_CONFIG["sma_windows"])
    if cfg:
        merged.update(cfg)
    return merged
