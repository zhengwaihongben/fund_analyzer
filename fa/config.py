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
