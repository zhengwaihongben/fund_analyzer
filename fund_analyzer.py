#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fund Analyzer — 輸入基金代碼，一鍵輸出淨值、持倉、技術指標、風險指標

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
    "aum":          None,
    "aum_date":     None,
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
# 1. 淨值數據
# ============================================================
def fetch_nav(fund_code: str, save_dir: Path) -> pd.DataFrame:
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
        # 兜底：第一列当日期
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
        # 兜底：排除 Date 后，取第一个数值型列
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

    out = save_dir / f"{fund_code}_daily_nav.csv"
    raw.to_csv(out, index=False, encoding="utf-8-sig")
    log.info(f"[{fund_code}] 净值 {len(raw)} 条 → {out.name}")
    return raw


def plot_trend(df: pd.DataFrame, fund_code: str, save_dir: Path) -> Path:
    log.info(f"[{fund_code}] 生成淨值趨勢圖 ...")
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
    log.info(f"[{fund_code}] 趨勢圖 → {out.name}")
    return out


# ============================================================
# 2. 持倉
# ============================================================
def fetch_holdings(fund_code: str, save_dir: Path, use_cache=True) -> pd.DataFrame:
    cache_path = save_dir / f"{fund_code}_holdings_raw.csv"
    if use_cache and cache_path.exists():
        log.info(f"[{fund_code}] 從緩存讀取持倉: {cache_path.name}")
        return pd.read_csv(cache_path, dtype=str)

    log.info(f"[{fund_code}] 獲取歷史持倉 ...")
    current_year = datetime.now().year
    years = [str(y) for y in range(current_year - 3, current_year + 1)]

    all_holdings = []
    for year in years:
        try:
            df = ak.fund_portfolio_hold_em(symbol=fund_code, date=year)
            if df is not None and not df.empty:
                all_holdings.append(df)
                log.info(f"[{fund_code}] {year} 年持倉: {len(df)} 條")
        except Exception as e:
            log.warning(f"[{fund_code}] {year} 年持倉獲取失敗: {e}")
        time.sleep(1.5)   # 每年之間停 1.5 秒

    if not all_holdings:
        log.warning(f"[{fund_code}] 無持倉數據")
        return pd.DataFrame()

    holdings = pd.concat(all_holdings, ignore_index=True)
    holdings.to_csv(cache_path, index=False, encoding="utf-8-sig")
    log.info(f"[{fund_code}] 持倉 {len(holdings)} 條 → {cache_path.name}")
    return holdings


def _clean_holdings(holdings: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "股票代碼": "StockCode", "股票名稱": "Stock",
        "佔淨值比例": "Weight", "持股數": "Shares",
        "持倉市值": "MarketValue", "季度": "Quarter",
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


def plot_holdings(holdings: pd.DataFrame, fund_code: str, save_dir: Path):
    if holdings.empty:
        return None
    log.info(f"[{fund_code}] 生成持倉堆疊面積圖 ...")

    holdings = _clean_holdings(holdings)
    if "Weight" not in holdings.columns or "QuarterLabel" not in holdings.columns:
        log.warning(f"[{fund_code}] 持倉數據字段不足，跳過繪圖")
        return None

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
    log.info(f"[{fund_code}] 持倉圖 → {out.name}")
    return out


# ============================================================
# 3. 技術指標
# ============================================================
def compute_technical(df: pd.DataFrame, cfg: dict,
                      save_dir: Path, fund_code: str) -> pd.DataFrame:
    log.info(f"[{fund_code}] 計算 SMA / BOLL ...")
    for w in cfg["sma_windows"]:
        df[f"SMA{w}"] = df["NAV"].rolling(window=w, min_periods=1).mean()

    n, k = cfg["boll_n"], cfg["boll_k"]
    df["BOLL_Mid"]   = df["NAV"].rolling(window=n, min_periods=1).mean()
    df["BOLL_Std"]   = df["NAV"].rolling(window=n, min_periods=1).std()
    df["BOLL_Upper"] = df["BOLL_Mid"] + k * df["BOLL_Std"]
    df["BOLL_Lower"] = df["BOLL_Mid"] - k * df["BOLL_Std"]

    # 預先構造價格文字標籤（4 位小數），供技術圖使用
    df["NAV_Label"] = df["NAV"].round(4).astype(str)

    out = save_dir / f"{fund_code}_daily_nav_sma_boll.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")

    out = save_dir / f"{fund_code}_daily_nav_sma_boll.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    log.info(f"[{fund_code}] 技術指標 CSV → {out.name}")
    return df


def plot_technical(df: pd.DataFrame, cfg: dict,
                   fund_code: str, save_dir: Path) -> Path:
    log.info(f"[{fund_code}] 生成 SMA/BOLL 交互圖 ...")
    windows = cfg["sma_windows"]
    fig = go.Figure()

    # --- trace 0: NAV（lines + markers + text，直接顯示價格）---
    fig.add_trace(go.Scatter(
        x=df["Date"], y=df["NAV"],
        mode="lines+markers+text",
        name="NAV (Unit Net Value)",
        line=dict(color=COLOR_MAP["NAV"], width=1.2),
        marker=dict(size=5, color=COLOR_MAP["NAV"]),
        text=df["NAV_Label"],
        textposition="top center",
        textfont=dict(size=8, color="#444444"),
        hovertemplate="%{x|%Y-%m-%d}<br>NAV: %{y:.4f}<extra></extra>",
        visible=True,
    ))

    # --- trace 1~N: SMA（lines + markers）---
    for w in windows:
        key = f"SMA{w}"
        fig.add_trace(go.Scatter(
            x=df["Date"], y=df[key],
            mode="lines+markers",
            name=f"SMA {w}",
            line=dict(color=COLOR_MAP[key], width=1.6),
            marker=dict(size=3, color=COLOR_MAP[key]),
            hovertemplate=f"%{{x|%Y-%m-%d}}<br>SMA {w}: %{{y:.4f}}<extra></extra>",
            visible=True,
        ))

    # --- BOLL Upper ---
    fig.add_trace(go.Scatter(
        x=df["Date"], y=df["BOLL_Upper"],
        mode="lines+markers",
        name="BOLL Upper",
        line=dict(color="rgba(214,39,40,0.9)", width=1.4),
        marker=dict(size=3, color="rgba(214,39,40,0.9)"),
        hovertemplate="%{x|%Y-%m-%d}<br>Upper: %{y:.4f}<extra></extra>",
        visible=False,
    ))

    # --- BOLL Lower（fill 到 Upper 之間）---
    fig.add_trace(go.Scatter(
        x=df["Date"], y=df["BOLL_Lower"],
        mode="lines+markers",
        name="BOLL Lower",
        line=dict(color="rgba(214,39,40,0.9)", width=1.4),
        marker=dict(size=3, color="rgba(214,39,40,0.9)"),
        fill="tonexty",
        fillcolor="rgba(214,39,40,0.10)",
        hovertemplate="%{x|%Y-%m-%d}<br>Lower: %{y:.4f}<extra></extra>",
        visible=False,
    ))

    # --- BOLL Mid ---
    fig.add_trace(go.Scatter(
        x=df["Date"], y=df["BOLL_Mid"],
        mode="lines+markers",
        name="BOLL Mid (SMA 20)",
        line=dict(color="#1f77b4", width=1.8),
        marker=dict(size=3, color="#1f77b4"),
        hovertemplate="%{x|%Y-%m-%d}<br>Mid: %{y:.4f}<extra></extra>",
        visible=False,
    ))

    # --- visible 數組 ---
    sma_visible  = [True] * (1 + len(windows)) + [False, False, False]
    boll_visible = [True] + [False] * len(windows) + [True, True, True]

    # --- 按鈕切換 ---
    fig.update_layout(
        updatemenus=[dict(
            type="buttons", direction="right",
            x=0.0, y=1.12, xanchor="left", yanchor="top",
            showactive=True, active=0,
            bgcolor="#f0f0f0", bordercolor="#cccccc", font=dict(size=13),
            buttons=[
                dict(
                    label="SMA",
                    method="update",
                    args=[
                        {"visible": sma_visible},
                        {"title.text": (
                            f"{fund_code}"
                            f"<br><sup>NAV with SMA 5 / 10 / 60 / 120 / 250</sup>"
                        )},
                    ],
                ),
                dict(
                    label="BOLL",
                    method="update",
                    args=[
                        {"visible": boll_visible},
                        {"title.text": (
                            f"{fund_code}"
                            f"<br><sup>NAV with Bollinger Bands (20, 2σ)</sup>"
                        )},
                    ],
                ),
            ],
        )],
        title=dict(
            text=(
                f"{fund_code}"
                f"<br><sup>NAV with SMA 5 / 10 / 60 / 120 / 250</sup>"
            ),
            font=dict(size=18),
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
        yaxis=dict(
            title="Unit NAV (CNY)",
            autorange=True,
            fixedrange=False,
        ),
        hovermode="x unified",
        template="plotly_white",
        height=700,
        margin=dict(l=60, r=40, t=150, b=60),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="#cccccc", borderwidth=1,
        ),
    )
    out = save_dir / f"{fund_code}_nav_sma_boll.html"
    fig.write_html(out, include_plotlyjs="cdn")
    log.info(f"[{fund_code}] SMA/BOLL 圖 → {out.name}")
    return out


# ============================================================
# 4. 風險指標
# ============================================================
def compute_risk_metrics(df: pd.DataFrame, cfg: dict, fund_code: str,
                         benchmark: str = None, aum: float = None) -> dict:
    log.info(f"[{fund_code}] 計算風險指標 ...")
    rf = cfg["risk_free"]
    td = cfg["trading_days"]

    df = df.copy()
    df["DailyRet"] = df["NAV"].pct_change()
    ret = df["DailyRet"].dropna()

    if len(ret) < 30:
        log.warning(f"[{fund_code}] 有效收益率不足 30 條，跳過指標計算")
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
            bench = ak.index_us_stock_sina(symbol=benchmark)
            bench["date"] = pd.to_datetime(bench["date"])
            bench = bench.rename(columns={"date": "Date", "close": "BenchClose"})
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
            log.warning(f"[{fund_code}] 基準 {benchmark} 獲取失敗: {e}")

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
    log.info(f"[{fund_code}] 生成風險指標圖 ...")
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

    # 指標卡片
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

    # 副標題帶 AUM（可選）
    if aum is not None:
        sub = f"Risk-Adjusted Performance Metrics | AUM: {aum} 億元"
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
    log.info(f"[{fund_code}] 風險指標圖 → {out.name}")
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
    log.info(f"輸出目錄: {save_dir.resolve()}")

    summary = {"fund_code": fund_code, "output_dir": str(save_dir.resolve()),
               "generated_at": datetime.now().isoformat(timespec="seconds")}

    # 1. 淨值（硬依賴）
    nav_df = fetch_nav(fund_code, save_dir)
    if nav_df.empty:
        log.error(f"[{fund_code}] 無淨值數據，終止")
        return summary

    trend_html = plot_trend(nav_df, fund_code, save_dir)

    # 2. 持倉（軟依賴）
    holdings = fetch_holdings(fund_code, save_dir, use_cache=use_cache)
    holdings_html = plot_holdings(holdings, fund_code, save_dir) if not holdings.empty else None

    # 3. 技術指標
    tech_df = compute_technical(nav_df, cfg, save_dir, fund_code)
    tech_html = plot_technical(tech_df, cfg, fund_code, save_dir)

    # 4. 風險指標
    metrics = compute_risk_metrics(tech_df, cfg, fund_code,
                                   benchmark=cfg.get("benchmark"),
                                   aum=cfg.get("aum"))
    summary["metrics"] = metrics
    risk_html = plot_risk_metrics(
        tech_df, metrics, fund_code, save_dir,
        aum=cfg.get("aum"),
        aum_date=cfg.get("aum_date"),
    ) if metrics else None
    
    # 保存匯總
    summary["files"] = {
        "nav_csv":        f"{fund_code}_daily_nav.csv",
        "trend_html":     trend_html.name if trend_html else None,
        "holdings_raw":   f"{fund_code}_holdings_raw.csv" if not holdings.empty else None,
        "holdings_html":  holdings_html.name if holdings_html else None,
        "tech_csv":       f"{fund_code}_daily_nav_sma_boll.csv",
        "tech_html":      tech_html.name if tech_html else None,
        "risk_html":      risk_html.name if risk_html else None,
    }
    summary_path = save_dir / f"{fund_code}_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log.info(f"[{fund_code}] 匯總 → {summary_path.name}")

    if show:
        for h in [trend_html, holdings_html, tech_html, risk_html]:
            if h and h.exists():
                webbrowser.open(h.as_uri())

    log.info(f"===== {fund_code} 分析完成 =====")
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Fund Analyzer — 輸入基金代碼，輸出淨值、持倉、技術指標、風險指標")
    parser.add_argument("fund_code", help="基金代碼（如 017436）")
    parser.add_argument("--output", default="output", help="輸出根目錄（默認 ./output）")
    parser.add_argument("--benchmark", default=".NDX",
                        help="基準指數代碼（默認 .NDX，傳空字符串跳過 IR）")
    parser.add_argument("--risk-free", type=float, default=0.02,
                        help="年化無風險利率（默認 0.02）")
    parser.add_argument("--aum", type=float, default=None,
                        help="基金規模（億元），可選，手動傳入")
    parser.add_argument("--no-cache", action="store_true",
                        help="忽略持倉緩存，強製重新拉取")
    parser.add_argument("--show", action="store_true",
                        help="生成完成後用瀏覽器打開所有圖表")
    parser.add_argument("-v", "--verbose", action="store_true", help="詳細日誌")
    args = parser.parse_args()

    setup_logging(args.verbose)

    cfg = {
        "risk_free": args.risk_free,
        "benchmark": args.benchmark or None,
        "aum":       args.aum,
        "aum_date":  datetime.now().strftime("%Y-%m-%d"),
    }

    analyze_fund(
        fund_code=args.fund_code,
        base_output=Path(args.output),
        cfg=cfg,
        show=args.show,
        use_cache=not args.no_cache,
    )


if __name__ == "__main__":
    main()