#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fund Analyzer — 输入基金代码，一键输出净值、持仓、技术指标、风险指标

Usage:
    python fund_analyzer.py 017436
    python fund_analyzer.py 017436 --benchmark .NDX --risk-free 0.02
    python fund_analyzer.py 017436 --aum 47.06 --show -v
"""
import argparse
import json
import logging
import re
import webbrowser
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import akshare as ak
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import time
import statsmodels.api as sm
import tkinter as tk

# ============================================================
# 配置
# ============================================================
DEFAULT_CONFIG = {
    "risk_free":    0.02,
    "trading_days": 252,
    "sma_windows":  [5, 10, 60, 120, 250],
    "boll_n":       20,
    "boll_k":       2,
    "benchmark":    ".NDX",
    "benchmark_market": "auto",
    "start_date":   None,
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

log = logging.getLogger("fund_analyzer")


def setup_logging(verbose=False):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


# ============================================================
# 1. 净值数据
# ============================================================
def fetch_aum(fund_code: str) -> tuple:
    """从雪球接口自动获取基金最新规模。返回 (规模亿元, 日期)。失败返回 (None, None)。"""
    try:
        info = ak.fund_individual_basic_info_xq(symbol=fund_code)
        # 提取最新规模
        aum_series = info.loc[info['item'] == '最新规模', 'value']
        if not aum_series.empty:
            aum_str = aum_series.values[0]
            # 提取数字（部分接口返回如 "47.06亿" 的字符串）
            import re
            match = re.search(r"[\d\.]+", str(aum_str))
            if match:
                aum = float(match.group())
                log.info(f"[{fund_code}] 自动获取最新规模: {aum} 亿元")
                return aum, datetime.now().strftime("%Y-%m-%d")
    except Exception as e:
        log.warning(f"[{fund_code}] 自动获取规模失败: {e}")
    return None, None

def fetch_nav(fund_code: str, save_dir: Path,
              start_date: str = None) -> pd.DataFrame:
    log.info(f"[{fund_code}] 获取历史净值 ...")
    try:
        raw = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
    except Exception as e:
        log.error(f"[{fund_code}] 净值获取失败: {e}")
        return pd.DataFrame()

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

    # ---------- 按起始日期筛选（新增） ----------
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
            log.warning(f"[{fund_code}] 起始日期解析失败 ''''{start_date}'''': {e}，使用全部数据")

    out = save_dir / f"{fund_code}_daily_nav.csv"
    raw.to_csv(out, index=False, encoding="utf-8-sig")
    log.info(f"[{fund_code}] 净值 {len(raw)} 条 → {out.name}")
    return raw

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

def plot_trend(df: pd.DataFrame, fund_code: str, save_dir: Path) -> Path:
    log.info(f"[{fund_code}] 生成净值趋势图 ...")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["Date"], y=df["NAV"],
        mode="lines", name="Unit NAV",
        line=dict(color="#1f77b4", width=1.8),
        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>NAV: %{y:.4f}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(
            text=f"{fund_code}",
            font=dict(size=18),
        ),
        xaxis=dict(
            title="Date",
            rangeslider=dict(visible=True),
            type="date",
        ),
        yaxis=dict(
            title="Unit NAV (CNY)",
            autorange=True,
            fixedrange=False,
        ),
        hovermode="x unified",
        template="plotly_white",
        height=600,
        margin=dict(l=60, r=40, t=80, b=60),
    )
    out = save_dir / f"{fund_code}_trend.html"
    fig.write_html(out, include_plotlyjs="cdn")
    log.info(f"[{fund_code}] 趋势图 → {out.name}")
    return out


# ============================================================
# 2. 持仓
# ============================================================
def fetch_holdings(fund_code: str, save_dir: Path, use_cache=True, start_date=None) -> pd.DataFrame:
    cache_path = save_dir / f"{fund_code}_holdings_raw.csv"
    if use_cache and cache_path.exists():
        log.info(f"[{fund_code}] 从缓存读取持仓: {cache_path.name}")
        return pd.read_csv(cache_path, dtype=str)

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
        try:
            df = ak.fund_portfolio_hold_em(symbol=fund_code, date=year)
            if df is not None and not df.empty:
                all_holdings.append(df)
                log.info(f"[{fund_code}] {year} 年持仓: {len(df)} 条")
        except Exception as e:
            log.warning(f"[{fund_code}] {year} 年持仓获取失败: {e}")

    if not all_holdings:
        log.warning(f"[{fund_code}] 无持仓数据")
        return pd.DataFrame()

    holdings = pd.concat(all_holdings, ignore_index=True)
    holdings.to_csv(cache_path, index=False, encoding="utf-8-sig")
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


def plot_holdings(holdings: pd.DataFrame, fund_code: str, save_dir: Path, start_date: str = None):
    if holdings.empty:
        return None
    log.info(f"[{fund_code}] 生成持仓堆叠面积图 ...")

    holdings = _clean_holdings(holdings)
    if "Weight" not in holdings.columns or "QuarterLabel" not in holdings.columns:
        log.warning(f"[{fund_code}] 持仓数据字段不足，跳过绘图")
        return None

    # 新增：按 start_date 过滤季度数据
    if start_date:
        try:
            start_year = pd.to_datetime(start_date).year
            # 提取 QuarterLabel 的年份部分并过滤
            holdings = holdings[
                holdings["QuarterLabel"].astype(str).str[:4].astype(int) >= start_year
            ]
            if holdings.empty:
                log.warning(f"[{fund_code}] 按起始年份 {start_year} 过滤后无持仓数据，跳过绘图")
                return None
        except Exception as e:
            log.warning(f"[{fund_code}] 过滤持仓年份失败: {e}")

    clean_path = save_dir / f"{fund_code}_holdings_clean.csv"
    holdings.to_csv(clean_path, index=False, encoding="utf-8-sig")

    qorder = list(holdings["QuarterLabel"].cat.categories)
    latest_q = qorder[-1]
    latest = (holdings[holdings["QuarterLabel"] == latest_q]
            .nlargest(10, "Weight")[["StockCode", "Stock", "Weight"]]
            .reset_index(drop=True))
    top_stocks = latest["Stock"].tolist()

    log.info(f"[{fund_code}] 最新季度 {latest_q} 前十大重仓股:")
    for i, row in latest.iterrows():
        code   = row["StockCode"]
        name   = row["Stock"]
        weight = row["Weight"]
        log.info(f"  {i+1:>2}. {code:<10}{name:<26}{weight:>8.2f}%")
    total = latest["Weight"].sum()
    log.info(f"      Top 10 Total: {total:.2f}%")

    full_idx = pd.MultiIndex.from_product(
        [qorder, top_stocks], names=["QuarterLabel", "Stock"]
    )
    plot_df = (holdings[holdings["Stock"].isin(top_stocks)]
               .groupby(["QuarterLabel", "Stock"])["Weight"]
               .sum().reindex(full_idx, fill_value=0).reset_index())

    fig = px.area(
        plot_df, x="QuarterLabel", y="Weight", color="Stock",
        title=(
            f"{fund_code}"
            f"<br><sup>Top 10 Holdings Weight Over Time</sup>"
        ),
        labels={"Weight": "Weight (% of NAV)",
                "QuarterLabel": "Quarter", "Stock": "Stock"},
        template="plotly_white", height=620,
    )
    fig.update_layout(
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="right", x=1),
        margin=dict(l=60, r=40, t=110, b=60),
        xaxis=dict(type="category", title="Quarter"),
        yaxis=dict(title="Weight (% of NAV)"),
    )
    out = save_dir / f"{fund_code}_holdings_area.html"
    fig.write_html(out, include_plotlyjs="cdn")
    log.info(f"[{fund_code}] 持仓图 → {out.name}")
    return out


# ============================================================
# 3. 技术指标
# ============================================================
def compute_technical(df: pd.DataFrame, cfg: dict,
                      save_dir: Path, fund_code: str) -> pd.DataFrame:
    log.info(f"[{fund_code}] 计算 SMA / BOLL / RSI / MACD ...")
    for w in cfg["sma_windows"]:
        df[f"SMA{w}"] = df["NAV"].rolling(window=w, min_periods=1).mean()

    n, k = cfg["boll_n"], cfg["boll_k"]
    df["BOLL_Mid"]   = df["NAV"].rolling(window=n, min_periods=1).mean()
    df["BOLL_Std"]   = df["NAV"].rolling(window=n, min_periods=1).std()
    df["BOLL_Upper"] = df["BOLL_Mid"] + k * df["BOLL_Std"]
    df["BOLL_Lower"] = df["BOLL_Mid"] - k * df["BOLL_Std"]

    # 计算 RSI (默认周期 14)
    delta = df["NAV"].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))
    df["RSI"] = df["RSI"].fillna(50)

    # 新增：计算 MACD (12, 26, 9)
    exp12 = df["NAV"].ewm(span=12, adjust=False).mean()
    exp26 = df["NAV"].ewm(span=26, adjust=False).mean()
    df["MACD_DIF"] = exp12 - exp26
    df["MACD_DEA"] = df["MACD_DIF"].ewm(span=9, adjust=False).mean()
    df["MACD_Hist"] = (df["MACD_DIF"] - df["MACD_DEA"]) * 2  # A股习惯乘2，更直观

    # ================= 新增：計算 KDJ (9, 3, 3) =================
    low_min = df["NAV"].rolling(window=9, min_periods=1).min()
    high_max = df["NAV"].rolling(window=9, min_periods=1).max()
    rsv = (df["NAV"] - low_min) / (high_max - low_min + 1e-12) * 100
    df["KDJ_K"] = rsv.ewm(com=2, adjust=False).mean()
    df["KDJ_D"] = df["KDJ_K"].ewm(com=2, adjust=False).mean()
    df["KDJ_J"] = 3 * df["KDJ_K"] - 2 * df["KDJ_D"]

    # ================= 新增：計算 ATR (14) 與 ADX (14) =================
    # 因為基金沒有高低價，真實波幅 TR 退化為淨值變動的絕對值
    tr = df["NAV"].diff().abs()
    df["ATR"] = tr.ewm(alpha=1/14, adjust=False).mean()

    # 計算 ADX 的 +DI, -DI 和 ADX
    up_move = df["NAV"].diff()
    down_move = -df["NAV"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr_14 = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean()
    plus_di = 100 * (pd.Series(plus_dm).ewm(alpha=1/14, adjust=False).mean() / (atr_14 + 1e-12))
    minus_di = 100 * (pd.Series(minus_dm).ewm(alpha=1/14, adjust=False).mean() / (atr_14 + 1e-12))
    
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-12)
    df["ADX_PlusDI"] = plus_di
    df["ADX_MinusDI"] = minus_di
    df["ADX"] = dx.ewm(alpha=1/14, adjust=False).mean()

    out = save_dir / f"{fund_code}_daily_nav_sma_boll.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")

    log.info(f"[{fund_code}] 技术指标 CSV → {out.name}")
    return df


def plot_technical(df: pd.DataFrame, cfg: dict,
                   fund_code: str, save_dir: Path) -> Path:
    log.info(f"[{fund_code}] 生成 SMA/BOLL/RSI/MACD/KDJ/ATR/ADX 交互图 ...")
    windows = cfg["sma_windows"]
    
    if "IsEstimated" not in df.columns:
        df["IsEstimated"] = False

    real_df = df[df["IsEstimated"] == False]
    est_df = df[df["IsEstimated"] == True]

    if not est_df.empty and not real_df.empty:
        last_real = real_df.iloc[[-1]]
        est_line_df = pd.concat([last_real, est_df]).copy()
        est_line_df["IsEstimated"] = True
    else:
        est_line_df = pd.DataFrame()

    # 建立 5 個子圖
    fig = make_subplots(
        rows=5, cols=1, 
        shared_xaxes=True, 
        vertical_spacing=0.03,
        row_heights=[0.4, 0.15, 0.15, 0.15, 0.15], # 主圖 40%，其他各 15%
        subplot_titles=(
            f"{fund_code} · NAV / SMA / BOLL", 
            "MACD (12, 26, 9)", 
            "RSI (14) & KDJ (9,3,3)", 
            "ADX (14)", 
            "ATR (14) - 波動率"
        )
    )

    # ================= 行 1: 主圖 =================
    fig.add_trace(go.Scatter(x=real_df["Date"], y=real_df["NAV"], mode="lines", name="NAV",
        line=dict(color=COLOR_MAP["NAV"], width=1.2),
        hovertemplate="%{x|%Y-%m-%d}<br>NAV: %{y:.4f}<extra></extra>"), row=1, col=1)

    if not est_line_df.empty:
        fig.add_trace(go.Scatter(x=est_line_df["Date"], y=est_line_df["NAV"], mode="lines+markers", name="Estimated NAV",
            line=dict(color="#d62728", width=2, dash="dash"),
            marker=dict(size=8, color="#d62728", symbol="circle-open", line=dict(width=2)),
            hovertemplate="<b>Estimated</b><br>%{x|%Y-%m-%d}<br>NAV: %{y:.4f}<extra></extra>"), row=1, col=1)

    idx_sma = []
    for w in windows:
        idx_sma.append(len(fig.data))
        key = f"SMA{w}"
        fig.add_trace(go.Scatter(x=df["Date"], y=df[key], mode="lines", name=f"SMA {w}",
            line=dict(color=COLOR_MAP[key], width=1.6),
            hovertemplate=f"%{{x|%Y-%m-%d}}<br>SMA {w}: %{{y:.4f}}<extra></extra>"), row=1, col=1)

    idx_boll_upper = len(fig.data)
    fig.add_trace(go.Scatter(x=df["Date"], y=df["BOLL_Upper"], mode="lines", name="BOLL Upper", line=dict(color="rgba(214,39,40,0.9)", width=1.4), visible=False), row=1, col=1)
    idx_boll_lower = len(fig.data)
    fig.add_trace(go.Scatter(x=df["Date"], y=df["BOLL_Lower"], mode="lines", name="BOLL Lower", line=dict(color="rgba(214,39,40,0.9)", width=1.4), fill="tonexty", fillcolor="rgba(214,39,40,0.10)", visible=False), row=1, col=1)
    idx_boll_mid = len(fig.data)
    fig.add_trace(go.Scatter(x=df["Date"], y=df["BOLL_Mid"], mode="lines", name="BOLL Mid", line=dict(color="#1f77b4", width=1.8, dash="dash"), visible=False), row=1, col=1)

    # ================= 行 2: MACD =================
    colors = ['#ef5350' if val > 0 else '#26a69a' for val in df["MACD_Hist"]]
    fig.add_trace(go.Bar(x=df["Date"], y=df["MACD_Hist"], name="MACD Hist", marker_color=colors, opacity=0.7), row=2, col=1)
    fig.add_trace(go.Scatter(x=df["Date"], y=df["MACD_DIF"], mode="lines", name="DIF", line=dict(color="#1f77b4", width=1.2)), row=2, col=1)
    fig.add_trace(go.Scatter(x=df["Date"], y=df["MACD_DEA"], mode="lines", name="DEA", line=dict(color="#ff9800", width=1.2)), row=2, col=1)

    # ================= 行 3: RSI & KDJ =================
    fig.add_trace(go.Scatter(x=df["Date"], y=df["RSI"], mode="lines", name="RSI", line=dict(color="#8e44ad", width=1.4)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df["Date"], y=df["KDJ_K"], mode="lines", name="KDJ K", line=dict(color="#ff9800", width=1.2)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df["Date"], y=df["KDJ_D"], mode="lines", name="KDJ D", line=dict(color="#1f77b4", width=1.2)), row=3, col=1)
    fig.add_hline(y=70, line_dash="dash", line_color="red", opacity=0.3, row=3, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color="green", opacity=0.3, row=3, col=1)

    # ================= 行 4: ADX =================
    fig.add_trace(go.Scatter(x=df["Date"], y=df["ADX"], mode="lines", name="ADX", line=dict(color="#000000", width=1.5)), row=4, col=1)
    fig.add_trace(go.Scatter(x=df["Date"], y=df["ADX_PlusDI"], mode="lines", name="+DI", line=dict(color="#26a69a", width=1, dash="dot")), row=4, col=1)
    fig.add_trace(go.Scatter(x=df["Date"], y=df["ADX_MinusDI"], mode="lines", name="-DI", line=dict(color="#ef5350", width=1, dash="dot")), row=4, col=1)
    fig.add_hline(y=25, line_dash="dash", line_color="gray", opacity=0.5, row=4, col=1)

    # ================= 行 5: ATR =================
    fig.add_trace(go.Scatter(x=df["Date"], y=df["ATR"], mode="lines", name="ATR", line=dict(color="#17becf", width=1.5), fill="tozeroy", fillcolor="rgba(23,190,207,0.1)"), row=5, col=1)

    # ================= 突出顯示預估日的各項指標 =================
    if not est_df.empty:
        est_date = est_df["Date"].iloc[0]
        # 主圖 SMA
        for w in windows:
            if pd.notna(est_df[f"SMA{w}"].iloc[0]):
                fig.add_trace(go.Scatter(x=[est_date], y=[est_df[f"SMA{w}"].iloc[0]], mode="markers", marker=dict(color=COLOR_MAP[f"SMA{w}"], size=8, symbol="star"), showlegend=False, hoverinfo="skip"), row=1, col=1)
        
        # MACD
        if pd.notna(est_df["MACD_DIF"].iloc[0]):
            fig.add_trace(go.Scatter(x=[est_date], y=[est_df["MACD_DIF"].iloc[0]], mode="markers", marker=dict(color="#1f77b4", size=8, symbol="star"), showlegend=False, hoverinfo="skip"), row=2, col=1)
            fig.add_trace(go.Scatter(x=[est_date], y=[est_df["MACD_DEA"].iloc[0]], mode="markers", marker=dict(color="#ff9800", size=8, symbol="star"), showlegend=False, hoverinfo="skip"), row=2, col=1)
        
        # RSI & KDJ
        if pd.notna(est_df["RSI"].iloc[0]):
            fig.add_trace(go.Scatter(x=[est_date], y=[est_df["RSI"].iloc[0]], mode="markers", marker=dict(color="#8e44ad", size=8, symbol="star"), showlegend=False, hoverinfo="skip"), row=3, col=1)
        if pd.notna(est_df["KDJ_K"].iloc[0]):
            fig.add_trace(go.Scatter(x=[est_date], y=[est_df["KDJ_K"].iloc[0]], mode="markers", marker=dict(color="#ff9800", size=8, symbol="star"), showlegend=False, hoverinfo="skip"), row=3, col=1)
            fig.add_trace(go.Scatter(x=[est_date], y=[est_df["KDJ_D"].iloc[0]], mode="markers", marker=dict(color="#1f77b4", size=8, symbol="star"), showlegend=False, hoverinfo="skip"), row=3, col=1)
            
        # ADX
        if pd.notna(est_df["ADX"].iloc[0]):
            fig.add_trace(go.Scatter(x=[est_date], y=[est_df["ADX"].iloc[0]], mode="markers", marker=dict(color="#000000", size=8, symbol="star"), showlegend=False, hoverinfo="skip"), row=4, col=1)
            
        # ATR
        if pd.notna(est_df["ATR"].iloc[0]):
            fig.add_trace(go.Scatter(x=[est_date], y=[est_df["ATR"].iloc[0]], mode="markers", marker=dict(color="#17becf", size=8, symbol="star"), showlegend=False, hoverinfo="skip"), row=5, col=1)

    # ================= 按鈕顯隱邏輯 =================
    total_traces = len(fig.data)
    visible_sma = [True] * total_traces
    visible_sma[idx_boll_upper] = False
    visible_sma[idx_boll_lower] = False
    visible_sma[idx_boll_mid] = False

    visible_boll = [True] * total_traces
    for idx in idx_sma:
        visible_boll[idx] = False

    fig.update_layout(
        updatemenus=[dict(
            type="buttons", direction="right", x=0.0, y=1.05, xanchor="left", yanchor="top",
            showactive=True, active=0, bgcolor="#f0f0f0", bordercolor="#cccccc", font=dict(size=13),
            buttons=[
                dict(label="SMA", method="update", args=[{"visible": visible_sma}]),
                dict(label="BOLL", method="update", args=[{"visible": visible_boll}]),
            ],
        )],
        title=dict(text=f"{fund_code} · 綜合技術指標", font=dict(size=17)),
        height=1300, # 因為子圖變多，高度增加到 1300
        hovermode="x unified",
        template="plotly_white",
        margin=dict(l=60, r=40, t=100, b=60),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, bgcolor="rgba(255,255,255,0.8)", bordercolor="#cccccc", borderwidth=1),
    )
    
    # 配置 X 軸（只有最下面的 5 號圖顯示滑塊和日期）
    for i in range(1, 5):
        fig.update_xaxes(rangeslider=dict(visible=False), type="date", row=i, col=1)
    
    fig.update_xaxes(
        rangeslider=dict(visible=True), type="date", row=5, col=1,
        tickformatstops=[
            dict(dtickrange=[None, 604800000], value="%Y-%m-%d"),
            dict(dtickrange=[604800000, "M1"], value="%Y-%m-%d"),
            dict(dtickrange=["M1", "M12"],     value="%Y-%m"),
            dict(dtickrange=["M12", None],     value="%Y"),
        ],
        hoverformat="%Y-%m-%d"
    )
    
    # 配置 Y 軸標題
    fig.update_yaxes(title_text="Unit NAV", row=1, col=1)
    fig.update_yaxes(title_text="MACD", row=2, col=1)
    fig.update_yaxes(title_text="震盪指標", range=[-20, 120], row=3, col=1) # KDJ 的 J 線可能超過 100 或低於 0，所以放寬
    fig.update_yaxes(title_text="趨勢強度", range=[0, 100], row=4, col=1)
    fig.update_yaxes(title_text="波幅", row=5, col=1)

    out = save_dir / f"{fund_code}_nav_sma_boll.html"
    fig.write_html(out, include_plotlyjs="cdn")
    log.info(f"[{fund_code}] 綜合技術指標圖 → {out.name}")
    return out

# ============================================================
# 4. 风险指标
# ============================================================
def compute_risk_metrics(df: pd.DataFrame, cfg: dict, fund_code: str,
                         benchmark: str = None, aum: float = None) -> dict:
    log.info(f"[{fund_code}] 计算风险指标 ...")
    rf = cfg["risk_free"]
    td = cfg["trading_days"]

    df = df.copy()
    df["DailyRet"] = df["NAV"].pct_change()
    ret = df["DailyRet"].dropna()

    if len(ret) < 30:
        log.warning(f"[{fund_code}] 有效收益率不足 30 条，跳过指标计算")
        return {}

    cum = (1 + ret).prod()
    n_years = len(ret) / td
    ann_ret = cum ** (1 / n_years) - 1 if n_years > 0 else np.nan
    ann_vol = ret.std() * np.sqrt(td)

    sharpe = (ann_ret - rf) / ann_vol if ann_vol > 0 else np.nan

    downside = ret[ret < 0]
    dvol = downside.std() * np.sqrt(td) if len(downside) > 1 else np.nan
    sortino = (ann_ret - rf) / dvol if dvol and dvol > 0 else np.nan

    nav = df["NAV"]
    dd = (nav - nav.cummax()) / nav.cummax()
    max_dd = dd.min()
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else np.nan

    info_ratio, te, ir_source = np.nan, np.nan, "N/A"
    if benchmark:
        try:
            bench = fetch_benchmark(benchmark, market=cfg.get("benchmark_market", "auto"))
            if not bench.empty:
                bench["BenchRet"] = bench["BenchClose"].pct_change()
                merged = df[["Date", "DailyRet"]].merge(
                    bench[["Date", "BenchRet"]], on="Date", how="inner"
                ).dropna()
                active = merged["DailyRet"] - merged["BenchRet"]
                te = active.std() * np.sqrt(td)
                ann_active = active.mean() * td
                info_ratio = ann_active / te if te > 0 else np.nan
                ir_source = benchmark
        except Exception as e:
            log.warning(f"[{fund_code}] 基准 {benchmark} 获取失败: {e}")

    def r(x, n=4):
        return round(float(x), n) if x is not None and not np.isnan(x) else None

    start_date = df["Date"].min().date()
    end_date   = df["Date"].max().date()

    metrics = {
        "fund_code":       fund_code,
        "period":          f"{start_date} ~ {end_date}",
        "trading_days":    int(len(ret)),
        "years":           round(n_years, 2),
        "ann_return":      r(ann_ret),
        "ann_vol":         r(ann_vol),
        "sharpe":          r(sharpe, 3),
        "sortino":         r(sortino, 3),
        "calmar":          r(calmar, 3),
        "info_ratio":      r(info_ratio, 3),
        "max_drawdown":    r(max_dd),
        "downside_vol":    r(dvol),
        "tracking_error":  r(te),
        "ir_benchmark":    ir_source,
        "aum":             aum,
    }

    sharpe_v  = metrics.get("sharpe")
    sortino_v = metrics.get("sortino")
    calmar_v  = metrics.get("calmar")
    ir_v      = metrics.get("info_ratio")
    maxdd_v   = metrics.get("max_drawdown")

    log.info(
        f"[{fund_code}] Sharpe={sharpe_v}  Sortino={sortino_v}"
        f"  Calmar={calmar_v}  IR={ir_v}  MaxDD={maxdd_v}"
    )
    return metrics


def plot_risk_metrics(df: pd.DataFrame, metrics: dict,
                      fund_code: str, save_dir: Path,
                      aum: float = None, aum_date: str = None) -> Path:
    log.info(f"[{fund_code}] 生成风险指标图 ...")
    df = df.copy()
    nav = df["NAV"]
    dd = ((nav - nav.cummax()) / nav.cummax()) * 100

    fig = make_subplots(
        rows=2, cols=1,
        row_heights=[0.65, 0.35],
        vertical_spacing=0.08,
        subplot_titles=("NAV & Drawdown", "Drawdown Profile"),
    )
    fig.add_trace(go.Scatter(
        x=df["Date"], y=nav,
        mode="lines", name="NAV",
        line=dict(color="#2b2b2b", width=1.4),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df["Date"], y=dd,
        mode="lines", name="Drawdown (%)",
        line=dict(color="rgba(200,120,120,0.8)", width=1),
        fill="tozeroy", fillcolor="rgba(200,120,120,0.12)",
    ), row=2, col=1)

    # 指标卡片
    sh = metrics.get("sharpe")
    so = metrics.get("sortino")
    ca = metrics.get("calmar")
    ir = metrics.get("info_ratio")
    metric_text = (
        f"Sharpe: {sh}   |   Sortino: {so}   |   "
        f"Calmar: {ca}   |   IR: {ir}"
    )
    fig.add_annotation(
        xref="paper", yref="paper",
        x=0.5, y=1.10,
        text=metric_text,
        showarrow=False,
        font=dict(size=13, color="#333333"),
        bgcolor="rgba(245,245,245,0.9)",
        bordercolor="#cccccc", borderwidth=1,
    )

    # 副标题带 AUM（可选）
    if aum is not None:
        sub = f"Risk-Adjusted Performance Metrics | AUM: {aum} 亿元"
        if aum_date:
            sub += f" ({aum_date})"
    else:
        sub = "Risk-Adjusted Performance Metrics"

    fig.update_layout(
        title=dict(
            text=f"{fund_code}<br><sup>{sub}</sup>",
            font=dict(size=17),
        ),
        height=700,
        template="plotly_white",
        hovermode="x unified",
        showlegend=False,
        margin=dict(l=60, r=40, t=140, b=50),
    )
    fig.update_xaxes(title_text="Date", row=2, col=1)
    fig.update_yaxes(title_text="NAV (CNY)", row=1, col=1)
    fig.update_yaxes(title_text="Drawdown (%)", row=2, col=1)

    out = save_dir / f"{fund_code}_risk_metrics.html"
    fig.write_html(out, include_plotlyjs="cdn")
    log.info(f"[{fund_code}] 风险指标图 → {out.name}")
    return out

def plot_excess_return(df: pd.DataFrame, fund_code: str, save_dir: Path,
                       benchmark: str, benchmark_market: str = "auto") -> Path:
    """
    绘制基金相对基准的累计超额收益曲线。

    计算方法（几何累加）：
        基金累计净值   = (1 + 基金日收益).cumprod()
        基准累计净值   = (1 + 基准日收益).cumprod()
        累计超额收益   = 基金累计净值 / 基准累计净值 - 1
    """
    log.info(f"[{fund_code}] 生成累计超额收益曲线 (基准: {benchmark}) ...")

    bench = fetch_benchmark(benchmark, market=benchmark_market)
    if bench.empty:
        log.warning(f"[{fund_code}] 基准数据获取失败，跳过超额收益图")
        return None

    # 对齐日期
    df = df.copy()
    df["DailyRet"] = df["NAV"].pct_change()
    merged = df[["Date", "DailyRet"]].merge(bench, on="Date", how="inner").dropna()
    if len(merged) < 30:
        log.warning(f"[{fund_code}] 基金与基准重叠日期不足 30 天，跳过超额收益图")
        return None

    merged = merged.reset_index(drop=True)
    merged["BenchRet"] = merged["BenchClose"].pct_change()
    merged = merged.dropna().reset_index(drop=True)
    merged["FundCum"]  = (1 + merged["DailyRet"]).cumprod()
    merged["BenchCum"] = (1 + merged["BenchRet"]).cumprod()
    merged["Excess"]   = (merged["FundCum"] / merged["BenchCum"] - 1) * 100

    # 统计
    final_excess = merged["Excess"].iloc[-1]
    max_excess   = merged["Excess"].max()
    min_excess   = merged["Excess"].min()
    log.info(f"[{fund_code}] 累计超额收益: 期末 {final_excess:+.2f}%  "
             f"最高 {max_excess:+.2f}%  最低 {min_excess:+.2f}%")

    # 画图
    fig = go.Figure()

    # 主线
    fig.add_trace(go.Scatter(
        x=merged["Date"], y=merged["Excess"],
        mode="lines",
        name=f"Excess vs {benchmark}",
        line=dict(color="#d62728", width=1.8),
        fill="tozeroy", fillcolor="rgba(214,39,40,0.10)",
        hovertemplate="%{x|%Y-%m-%d}<br>Excess: %{y:+.2f}%<extra></extra>",
    ))

    # 零线
    fig.add_hline(y=0, line=dict(color="#666666", width=1, dash="dash"))

    # 最高点 / 最低点标注
    if max_excess > 0:
        idx_max = merged["Excess"].idxmax()
        fig.add_annotation(
            x=merged.loc[idx_max, "Date"],
            y=merged.loc[idx_max, "Excess"],
            text=f"Peak: {max_excess:+.2f}%",
            showarrow=True, arrowhead=2, ax=0, ay=-30,
            font=dict(size=10, color="#2ca02c"),
            bgcolor="rgba(255,255,255,0.85)",
        )
    if min_excess < 0:
        idx_min = merged["Excess"].idxmin()
        fig.add_annotation(
            x=merged.loc[idx_min, "Date"],
            y=merged.loc[idx_min, "Excess"],
            text=f"Trough: {min_excess:+.2f}%",
            showarrow=True, arrowhead=2, ax=0, ay=30,
            font=dict(size=10, color="#d62728"),
            bgcolor="rgba(255,255,255,0.85)",
        )

    # 顶部信息卡
    stat_text = (
        f"Final: {final_excess:+.2f}%   |   "
        f"Peak: {max_excess:+.2f}%   |   "
        f"Trough: {min_excess:+.2f}%"
    )
    fig.add_annotation(
        xref="paper", yref="paper",
        x=0.5, y=1.10,
        text=stat_text,
        showarrow=False,
        font=dict(size=13, color="#333333"),
        bgcolor="rgba(245,245,245,0.9)",
        bordercolor="#cccccc", borderwidth=1,
    )

    fig.update_layout(
        title=dict(
            text=(
                f"{fund_code}"
                f"<br><sup>Cumulative Excess Return vs {benchmark}</sup>"
            ),
            font=dict(size=17),
        ),
        xaxis=dict(
            title="Date",
            rangeslider=dict(visible=True),
            type="date",
            tickformatstops=[
                dict(dtickrange=[None, 604800000], value="%Y-%m-%d"),
                dict(dtickrange=[604800000, "M1"], value="%Y-%m-%d"),
                dict(dtickrange=["M1", "M12"],     value="%Y-%m"),
                dict(dtickrange=["M12", None],     value="%Y"),
            ],
            hoverformat="%Y-%m-%d",
        ),
        yaxis=dict(title="Cumulative Excess Return (%)", zeroline=True),
        hovermode="x unified",
        template="plotly_white",
        height=600,
        margin=dict(l=60, r=40, t=130, b=60),
        showlegend=False,
    )

    out = save_dir / f"{fund_code}_excess_return.html"
    fig.write_html(out, include_plotlyjs="cdn")
    log.info(f"[{fund_code}] 超额收益图 → {out.name}")
    return out


# ============================================================
# 主流程
# ============================================================
def analyze_fund(fund_code: str, base_output: Path = Path("output"),
                 cfg: dict = None, show: bool = False,
                 use_cache: bool = True) -> dict:
    cfg = {**DEFAULT_CONFIG, **(cfg or {})}
    save_dir = base_output / fund_code
    save_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"===== 分析基金 {fund_code} =====")
    log.info(f"输出目录: {save_dir.resolve()}")

    # 清理上一次运行残留的旧文件，避免失败时误读旧结果
    for old in save_dir.iterdir():
        if old.is_file():
            try:
                old.unlink()
            except Exception:
                pass

    summary = {
        "fund_code":    fund_code,
        "output_dir":   str(save_dir.resolve()),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "success":      False,
        "error":        None,
    }

    # 0. 自动获取 AUM（可选）
    aum_val, aum_date = fetch_aum(fund_code)

    # 1. 净值（硬依赖）
    start_date = cfg.get("start_date")
    if start_date:
        log.info(f"[{fund_code}] 起始追蹤日期: {start_date}")
    nav_df = fetch_nav(fund_code, save_dir, start_date=start_date)
    if nav_df.empty:
        log.error(f"[{fund_code}] 无净值数据，终止")
        summary["error"] = "净值数据为空（可能基金代码错误、数据源异常，或起始日期晚于最新净值）"
        return summary
    trend_html = plot_trend(nav_df, fund_code, save_dir)

    # 2. 持仓
    holdings = fetch_holdings(fund_code, save_dir, use_cache=use_cache, start_date=start_date)
    holdings_html = plot_holdings(holdings, fund_code, save_dir, start_date=start_date) if not holdings.empty else None

    # 3. 估算最新净值
    estimated_df = estimate_latest_nav(fund_code, save_dir)
    
    # 4. 技术指标（将估算点传给 compute_technical，让所有指标自动计算到最后一天）
    if not estimated_df.empty:
        tech_df = compute_technical(estimated_df, cfg, save_dir, fund_code)
    else:
        tech_df = compute_technical(nav_df, cfg, save_dir, fund_code)
        
    tech_html = plot_technical(tech_df, cfg, fund_code, save_dir)

    # 5. 风险指标
    metrics = compute_risk_metrics(tech_df, cfg, fund_code,
                                   benchmark=cfg.get("benchmark"),
                                   aum=aum_val)
    summary["metrics"] = metrics
    risk_html = plot_risk_metrics(
        tech_df, metrics, fund_code, save_dir,
        aum=aum_val,
        aum_date=aum_date,
    ) if metrics else None

    # 6. 累计超额收益曲线（需要基准）
    excess_html = None
    if cfg.get("benchmark"):
        excess_html = plot_excess_return(
            tech_df, fund_code, save_dir,
            benchmark=cfg["benchmark"],
            benchmark_market=cfg.get("benchmark_market", "auto"),
        )
    
    # 保存汇总
    summary["files"] = {
        "nav_csv":        f"{fund_code}_daily_nav.csv",
        "trend_html":     trend_html.name if trend_html else None,
        "holdings_raw":   f"{fund_code}_holdings_raw.csv" if not holdings.empty else None,
        "holdings_html":  holdings_html.name if holdings_html else None,
        "tech_csv":       f"{fund_code}_daily_nav_sma_boll.csv",
        "tech_html":      tech_html.name if tech_html else None,
        "risk_html":      risk_html.name if risk_html else None,
        "excess_html":    excess_html.name if excess_html else None,
    }
    summary_path = save_dir / f"{fund_code}_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log.info(f"[{fund_code}] 汇总 → {summary_path.name}")

    if show:
        for h in [trend_html, holdings_html, tech_html, risk_html, excess_html]:
            if h and h.exists():
                webbrowser.open(h.as_uri())

    summary["success"] = True
    log.info(f"===== {fund_code} 分析完成 =====")
    return summary


def main():
    import os
    os.chdir(Path(__file__).parent)
    
    root = tk.Tk()

    parser = argparse.ArgumentParser(
        description="Fund Analyzer — 输入基金代码，输出净值、持仓、技术指标、风险指标")
    parser.add_argument("fund_code", help="基金代码（如 017436）")
    parser.add_argument("--output", default="output", help="输出根目录（默认 ./output）")
    parser.add_argument("--benchmark", default=".NDX",
                        help="基准指数代码（默认 .NDX，传空字符串跳过 IR）")
    parser.add_argument("--benchmark-market", default="auto",
                        choices=["auto", "us", "cn", "hk"],
                        help="基准市场（auto=自动判断，us=美股，cn=A股，hk=港股）")
    parser.add_argument("--risk-free", type=float, default=0.02,
                        help="年化无风险利率（默认 0.02）")
    parser.add_argument("--start-date", default=None,
                        help="起始追蹤日期（YYYY-MM-DD），留空则从成立日开始")
    parser.add_argument("--no-cache", action="store_true",
                        help="忽略持仓缓存，强制重新拉取")
    parser.add_argument("--show", action="store_true",
                        help="生成完成后用浏览器打开所有图表")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    setup_logging(args.verbose)

    cfg = {
        "risk_free":  args.risk_free,
        "benchmark":  args.benchmark or None,
        "benchmark_market": args.benchmark_market,
        "aum":        args.aum,
        "aum_date":   datetime.now().strftime("%Y-%m-%d"),
        "start_date": args.start_date,
    }
    
    analyze_fund(
        fund_code=args.fund_code,
        base_output=Path(args.output),
        cfg=cfg,
        show=args.show,
        use_cache=not args.no_cache,
    )

def generate_master_report(fund_code: str, save_dir: Path) -> Path:
        """生成一个包含核心图表的综合 HTML 报告。"""
        charts = [
            f"{fund_code}_holdings_area.html",
            f"{fund_code}_nav_sma_boll.html",
            f"{fund_code}_risk_metrics.html"
        ]
        
        # 过滤出实际存在的文件，生成 iframe 代码
        iframes = ""
        for chart in charts:
            if (save_dir / chart).exists():
                iframes += f'<iframe src="{chart}" style="width:100%; height:750px; border:none; margin-bottom:20px; box-shadow:0 4px 12px rgba(0,0,0,0.1); border-radius:8px; background:white;"></iframe>\n'

        html_content = f"""<!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>{fund_code} · 综合分析报告</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                    background-color: #f0f2f5;
                    margin: 0;
                    padding: 20px;
                }}
                .header {{
                    text-align: center;
                    margin-bottom: 30px;
                    color: #333;
                }}
                .container {{
                    max-width: 1200px;
                    margin: 0 auto;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{fund_code} · 基金分析报告</h1>
                </div>
                {iframes}
            </div>
        </body>
        </html>"""

        out_path = save_dir / f"{fund_code}_master_report.html"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        return out_path

# ============================================================
# 5. 盘后/盘前交易监控（解决 QDII 时差盲盒）
# ============================================================
# ============================================================
# yfinance 隔離調用（避免 curl_cffi 在多線程下 segfault）
# ============================================================
import subprocess
import sys as _sys
import json as _json
from pathlib import Path as _Path

_YF_HELPER = _Path(__file__).resolve().parent / "yf_helper.py"


def _yf_run(payload: dict, timeout: int = 90) -> dict:
    """在子進程中調用 yfinance，返回 dict。崩潰不影響主進程。"""
    if not _YF_HELPER.exists():
        return {"error": "yf_helper.py not found"}
    
    # 【修復】如果是 pythonw.exe，替換為 python.exe 啟動子進程，防止管道死鎖
    exe = _sys.executable
    if exe.endswith("pythonw.exe"):
        exe_py = exe.replace("pythonw.exe", "python.exe")
        if _Path(exe_py).exists():
            exe = exe_py

    creationflags = 0
    if _sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
        
    try:
        proc = subprocess.run(
            [exe, str(_YF_HELPER)],
            input=_json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": f"subprocess failed: {e}"}

    if proc.returncode != 0:
        return {"error": f"subprocess exit {proc.returncode}: {(proc.stderr or '')[-500:]}"}

    out = (proc.stdout or "").strip()
    try:
        return _json.loads(out)
    except _json.JSONDecodeError:
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return _json.loads(line)
                except _json.JSONDecodeError:
                    continue
        return {"error": f"invalid JSON from helper: {out[-500:]}"}


def _yf_download_df(tickers, period="1y", interval="1d",
                    prepost=False, group_by="column"):
    """yf.download 的隔離版本。返回 DataFrame 或 None。"""
    res = _yf_run({
        "action": "download",
        "tickers": tickers if isinstance(tickers, list) else [tickers],
        "period": period,
        "interval": interval,
        "prepost": prepost,
        "group_by": group_by,
    })
    if "error" in res or res.get("empty", True):
        log.warning(f"yf_download 失敗: {res.get('error', 'empty')}")
        return None

    index = pd.to_datetime(res["index"])
    cols = res["columns"]
    if cols and isinstance(cols[0], list):
        columns = pd.MultiIndex.from_tuples([tuple(c) for c in cols])
    else:
        columns = cols
    df = pd.DataFrame(res["data"], index=index, columns=columns)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def _yf_ticker_history(ticker, period="5d"):
    """yf.Ticker(t).history() 的隔離版本。返回 DataFrame 或 None。"""
    res = _yf_run({"action": "history", "ticker": ticker, "period": period})
    if "error" in res or res.get("empty", True):
        log.warning(f"yf_history({ticker}) 失敗: {res.get('error', 'empty')}")
        return None
    df = pd.DataFrame(
        res["data"],
        index=pd.to_datetime(res["index"]),
        columns=res["columns"],
    )
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df

def fetch_post_market_estimate(fund_code: str, save_dir: Path) -> dict:
    """
    读取最新持仓，获取盘后/盘前数据，计算预估涨跌幅（批量请求优化版）。
    """
    result = {
        "status": "success",
        "fund_code": fund_code,
        "yesterday_official_return": None, 
        "estimate_return": None,           
        "nq_futures_return": None,         
        "holdings": [],                    
        "message": ""
    }

    # 1. 获取昨日官方净值涨跌幅
    nav_csv = save_dir / f"{fund_code}_daily_nav.csv"
    if nav_csv.exists():
        try:
            df_nav = pd.read_csv(nav_csv)
            if len(df_nav) >= 2:
                last_nav = df_nav["NAV"].iloc[-1]
                prev_nav = df_nav["NAV"].iloc[-2]
                result["yesterday_official_return"] = (last_nav / prev_nav - 1) * 100
        except Exception:
            pass

    # 2. 获取纳指期货数据
    try:
        nq_hist = _yf_ticker_history("NQ=F", period="5d")
        if nq_hist is not None and len(nq_hist) >= 2:
            nq_last = nq_hist['Close'].iloc[-1]
            nq_prev = nq_hist['Close'].iloc[-2]
            result["nq_futures_return"] = (nq_last / nq_prev - 1) * 100
    except Exception as e:
        log.warning(f"[{fund_code}] 纳指期货获取失败: {e}")

    # 3. 获取最新持仓
    clean_csv = save_dir / f"{fund_code}_holdings_clean.csv"
    if not clean_csv.exists():
        result["status"] = "error"
        result["message"] = "找不到持仓数据，请先在主界面运行一次分析。"
        return result

    try:
        df_holdings = pd.read_csv(clean_csv)
        latest_q = df_holdings["QuarterLabel"].iloc[0]
        latest = df_holdings[df_holdings["QuarterLabel"] == latest_q].head(10)
    except Exception as e:
        result["status"] = "error"
        result["message"] = f"读取持仓失败: {e}"
        return result

    # 收集所有需要查询的 Ticker（过滤掉非美股代码）
    ticker_map = {}  # 映射关系：原始代码 -> 清洗后的yf代码
    for _, row in latest.iterrows():
        raw_ticker = str(row["StockCode"]).strip()
        # 常见的美股中概股/港股会带数字或点号，yfinance 需要转换
        # 这里简单判断：纯字母才去请求
        if raw_ticker.isalpha():
            ticker_map[raw_ticker] = raw_ticker
            
    tickers_list = list(ticker_map.values())
    if not tickers_list:
         result["status"] = "error"
         result["message"] = "持仓中无美股代码。"
         return result

    # 4. 批量获取日线数据（获取昨收价）
    daily_close = {}
    df_daily_all = _yf_download_df(tickers_list, period="5d", group_by="column")
    if df_daily_all is not None:
        try:
            if isinstance(df_daily_all.columns, pd.MultiIndex):
                for t in tickers_list:
                    try:
                        col = df_daily_all[("Close", t)].dropna()
                        if not col.empty:
                            daily_close[t] = col.iloc[-1]
                    except KeyError:
                        pass
            else:
                col = df_daily_all["Close"].dropna()
                if not col.empty:
                    daily_close[tickers_list[0]] = col.iloc[-1]
        except Exception as e:
            log.warning(f"[{fund_code}] 解析日线失败: {e}")

    # 5. 批量获取分钟线数据（包含盘前/盘后）
    latest_price = {}
    df_intra_all = _yf_download_df(tickers_list, period="5d", interval="1m",
                                    prepost=True, group_by="column")
    if df_intra_all is not None:
        try:
            if isinstance(df_intra_all.columns, pd.MultiIndex):
                for t in tickers_list:
                    try:
                        col = df_intra_all[("Close", t)].dropna()
                        if not col.empty:
                            latest_price[t] = col.iloc[-1]
                    except KeyError:
                        pass
            else:
                col = df_intra_all["Close"].dropna()
                if not col.empty:
                    latest_price[tickers_list[0]] = col.iloc[-1]
        except Exception as e:
            log.warning(f"[{fund_code}] 解析分钟线失败: {e}")

    # 6. 组装结果并计算
    total_weight_valid = 0
    weighted_return = 0
    missing_data = False

    for _, row in latest.iterrows():
        raw_ticker = str(row["StockCode"]).strip()
        name = row["Stock"]
        weight = float(row["Weight"]) / 100.0 
        
        stock_info = {
            "ticker": raw_ticker,
            "name": name,
            "weight": weight * 100,
            "regular_price": None,
            "post_price": None,
            "change_pct": None,
            "status": ""
        }

        if raw_ticker not in ticker_map:
            stock_info["status"] = "非美股代码"
            result["holdings"].append(stock_info)
            missing_data = True
            continue

        t = ticker_map[raw_ticker]
        reg_p = daily_close.get(t)
        latest_p = latest_price.get(t)

        if reg_p is not None:
            stock_info["regular_price"] = reg_p
        if latest_p is not None:
            stock_info["post_price"] = latest_p

        if reg_p and latest_p and reg_p > 0:
            change = (latest_p / reg_p - 1) * 100
            # 如果变化极小，可能是非交易时段盘前价格未动，归零处理
            if abs(change) < 0.001:
                change = 0.0
                
            stock_info["change_pct"] = change
            weighted_return += change * weight
            total_weight_valid += weight
        else:
            stock_info["status"] = "数据缺失"
            missing_data = True

        result["holdings"].append(stock_info)

    # 7. 计算综合预估
    if total_weight_valid > 0:
        raw_estimate = weighted_return / total_weight_valid
        result["estimate_return"] = raw_estimate
        if missing_data:
            result["message"] = "⚠️ 部分股票缺失盘后数据，预估仅基于有数据的股票推算"
    else:
        result["message"] = "❌ 所有股票均无法获取盘后数据，无法预估"

    return result

def estimate_latest_nav(fund_code: str, save_dir: Path) -> pd.DataFrame:
    """
    结合持仓权重、纳指和汇率，利用多元线性回归估算 T-1 日的基金净值。
    返回带有 'IsEstimated' 标记的 DataFrame。
    """
    log.info(f"[{fund_code}] 正在使用回归模型估算最新净值 ...")
    
    # 1. 读取原始净值数据
    nav_csv = save_dir / f"{fund_code}_daily_nav.csv"
    if not nav_csv.exists():
        return pd.DataFrame()
    
    df_nav = pd.read_csv(nav_csv)
    # 【修复】标准化日期并去重，防止 join 产生笛卡尔积撑爆内存
    df_nav["Date"] = pd.to_datetime(df_nav["Date"]).dt.normalize()
    df_nav = df_nav.drop_duplicates(subset=["Date"], keep="last")
    df_nav = df_nav.sort_values("Date").set_index("Date")
    df_nav["DailyRet"] = df_nav["NAV"].pct_change()
    
    # 默认返回的保底数据
    fallback_df = df_nav.reset_index()
    fallback_df["IsEstimated"] = False
    
    # 2. 获取纳斯达克指数和汇率数据
    try:
        ndx_data = _yf_download_df("^NDX", period="1y")
        cny_data = _yf_download_df("CNY=X", period="1y")
        if ndx_data is None or cny_data is None:
            log.warning(f"[{fund_code}] 宏觀因子下載失敗，跳過估算")
            return fallback_df

        def _pick_close(df):
            c = df["Close"]
            return c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c

        macro_data = pd.DataFrame({
            "^NDX": _pick_close(ndx_data),
            "CNY=X": _pick_close(cny_data),
        })
        # 【修复】标准化日期并去重
        macro_data.index = pd.to_datetime(macro_data.index).dt.normalize()
        macro_data = macro_data.drop_duplicates(keep="last").dropna()
        macro_ret = macro_data.pct_change().rename(
            columns={"^NDX": "NDX_Ret", "CNY=X": "FX_Ret"}
        )
    except Exception as e:
        log.warning(f"[{fund_code}] 获取宏观因子失败: {e}")
        return fallback_df

    # 3. 计算前十大持仓组合的收益率
    clean_csv = save_dir / f"{fund_code}_holdings_clean.csv"
    port_ret_series = pd.Series(dtype=float)
    
    if clean_csv.exists():
        try:
            df_holdings = pd.read_csv(clean_csv)
            latest_q = df_holdings["QuarterLabel"].iloc[0]
            latest_10 = df_holdings[df_holdings["QuarterLabel"] == latest_q].head(10)
            
            tickers_10 = [str(t).strip() for t in latest_10["StockCode"] if str(t).strip().isalpha()]
            weights_10 = (latest_10["Weight"] / 100).values
            
            if tickers_10:
                df_all = _yf_download_df(tickers_10, period="1y", group_by="column")
                if df_all is None:
                    raise RuntimeError("股票数据下载失败")
                closes = df_all["Close"]
                if isinstance(closes, pd.Series):
                    closes = closes.to_frame(name=tickers_10[0])
                stock_ret = closes.pct_change()

                w = weights_10[:len(stock_ret.columns)]
                port_ret_series = (stock_ret * w).sum(axis=1)
                port_ret_series.name = "Portfolio_Ret"
                port_ret_series.index = pd.to_datetime(port_ret_series.index).dt.normalize()
                port_ret_series = port_ret_series.drop_duplicates(keep="last")
        except Exception as e:
            log.warning(f"[{fund_code}] 计算持仓组合收益失败: {e}")

    # 4. 合并数据（全部改用 pd.merge 防爆炸）
    merged = pd.merge(df_nav[["DailyRet"]], macro_ret, left_index=True, right_index=True, how="inner")
    if not port_ret_series.empty:
        merged = pd.merge(merged, port_ret_series, left_index=True, right_index=True, how="inner")
    
    merged = merged.replace([np.inf, -np.inf], np.nan).dropna()
    
    if len(merged) < 20:
        log.warning(f"[{fund_code}] 有效重叠数据不足 20 天，无法回归。")
        return fallback_df

    # 5. 多元线性回归（纯 numpy）
    X_cols = ["NDX_Ret", "FX_Ret"]
    if not port_ret_series.empty:
        X_cols.append("Portfolio_Ret")
        
    # 提取 numpy 数组，强制转为 float 并添加常数项 1.0
    X_raw = merged[X_cols].astype(float).values
    X_np = np.column_stack((np.ones(len(X_raw)), X_raw))
    y_np = merged["DailyRet"].astype(float).values
    
    try:
        coeffs, residuals, rank, s = np.linalg.lstsq(X_np, y_np, rcond=None)
        y_pred = X_np.dot(coeffs)
        ss_res = np.sum((y_np - y_pred) ** 2)
        ss_tot = np.sum((y_np - np.mean(y_np)) ** 2)
        r_squared = 1 - (ss_res / (ss_tot + 1e-12))
        log.info(f"[{fund_code}] 回归拟合成功，R-squared = {r_squared:.4f}")
    except Exception as e:
        log.warning(f"[{fund_code}] 回归失败: {e}")
        return fallback_df

    # 6. 获取最新的宏观因子数据 (T-1日)
    last_nav_date = merged.index.max()
    latest_macro = macro_ret[macro_ret.index >= last_nav_date]
    
    if latest_macro.empty:
        log.info(f"[{fund_code}] 暂无最新的美股交易数据可供预测。")
        return fallback_df

    latest_row = latest_macro.iloc[-1]
    latest_date = latest_macro.index[-1]
    
    # 构建预测输入的 X
    predict_vals = [1.0, latest_row["NDX_Ret"], latest_row["FX_Ret"]]
    if not port_ret_series.empty:
        latest_port_ret = port_ret_series.get(latest_date, 0.0)
        if pd.isna(latest_port_ret):
            latest_port_ret = 0.0
        predict_vals.append(latest_port_ret)

    # 7. 预测最新收益率并推算净值
    try:
        est_return = np.dot(np.array(predict_vals, dtype=float), coeffs)
        est_return = max(min(est_return, 0.15), -0.15)
        last_real_nav = df_nav["NAV"].iloc[-1]
        est_nav = last_real_nav * (1 + est_return)
        log.info(f"[{fund_code}] 预测日期: {latest_date.date()} | 预估收益率: {est_return*100:+.2f}% | 预估净值: {est_nav:.4f}")
    except Exception as e:
        log.warning(f"[{fund_code}] 预测失败: {e}")
        return fallback_df

    # 8. 将预估点拼接到原 DataFrame
    new_row = pd.DataFrame({
        "Date": [latest_date],
        "NAV": [est_nav],
        "IsEstimated": [True]
    })
    
    final_df = pd.concat([fallback_df, new_row], ignore_index=True)
    final_df = final_df.sort_values("Date").reset_index(drop=True)
    
    return final_df


def plot_estimated_trend(df: pd.DataFrame, fund_code: str, save_dir: Path) -> Path:
    """绘制带有 T-1 日预估点的净值走势图"""
    log.info(f"[{fund_code}] 生成最新净值预估趋势图 ...")
    
    # 防御性检查
    if "IsEstimated" not in df.columns:
        df["IsEstimated"] = False
        
    # 分离真实数据和预估数据
    real_df = df[df["IsEstimated"] == False]
    est_df = df[df["IsEstimated"] == True]
    
    # 为了让预估点和实线连接，把真实数据的最后一个点也加进预估序列
    if not est_df.empty and not real_df.empty:
        last_real = real_df.iloc[[-1]]
        est_line_df = pd.concat([last_real, est_df]).copy()
        est_line_df["IsEstimated"] = True
    else:
        est_line_df = pd.DataFrame()

    fig = go.Figure()

    # 1. 真实净值实线
    fig.add_trace(go.Scatter(
        x=real_df["Date"], y=real_df["NAV"],
        mode="lines", name="Official NAV",
        line=dict(color="#1f77b4", width=1.8),
        hovertemplate="%{x|%Y-%m-%d}<br>NAV: %{y:.4f}<extra></extra>",
    ))

    # 2. 预估净值虚线与空心点
    if not est_line_df.empty:
        fig.add_trace(go.Scatter(
            x=est_line_df["Date"], y=est_line_df["NAV"],
            mode="lines+markers", name="Estimated T-1 NAV",
            line=dict(color="#d62728", width=1.8, dash="dash"),
            marker=dict(size=8, color="#d62728", symbol="circle-open", line=dict(width=2)),
            hovertemplate="<b>Estimated</b><br>%{x|%Y-%m-%d}<br>NAV: %{y:.4f}<extra></extra>",
        ))
        
        # 添加文本标注
        latest_est = est_line_df.iloc[-1]
        fig.add_annotation(
            x=latest_est["Date"], y=latest_est["NAV"],
            text=f"Est: {latest_est['NAV']:.4f}",
            showarrow=True, arrowhead=2, ax=0, ay=-30,
            font=dict(size=11, color="#d62728", weight="bold"),
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="#d62728", borderwidth=1,
        )

    fig.update_layout(
        title=dict(
            text=f"{fund_code}<br><sup>Official NAV vs Estimated Latest NAV (T-1)</sup>",
            font=dict(size=17),
        ),
        xaxis=dict(
            title="Date",
            rangeslider=dict(visible=True),
            type="date",
        ),
        yaxis=dict(title="Unit NAV (CNY)", autorange=True, fixedrange=False),
        hovermode="x unified",
        template="plotly_white",
        height=600,
        margin=dict(l=60, r=40, t=100, b=60),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
        ),
    )
    
    out = save_dir / f"{fund_code}_estimated_nav.html"
    fig.write_html(out, include_plotlyjs="cdn")
    log.info(f"[{fund_code}] 预估净值图 → {out.name}")
    return out

if __name__ == "__main__":
    main()