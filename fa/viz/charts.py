"""Plotly chart builders."""

from pathlib import Path

from plotly.subplots import make_subplots
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from fa.config import COLOR_MAP
from fa.data.benchmark import fetch_benchmark
from fa.data.holdings import _clean_holdings, top_holdings
from fa.utils.dates import latest_quarter_label
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

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
    latest_q = latest_quarter_label(holdings)
    latest = top_holdings(holdings, 10)[["StockCode", "Stock", "Weight"]]
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


def plot_technical(df: pd.DataFrame, cfg: dict,
                   fund_code: str, save_dir: Path) -> Path:
    log.info(f"[{fund_code}] 生成 SMA/BOLL/RSI/MACD/KDJ(proxy)/NAV_VOL/NAV_DX 交互图 ...")
    windows = cfg["sma_windows"]

    # 建立 5 個子圖
    fig = make_subplots(
        rows=5, cols=1, 
        shared_xaxes=True, 
        vertical_spacing=0.03,
        row_heights=[0.4, 0.15, 0.15, 0.15, 0.15], # 主圖 40%，其他各 15%
        subplot_titles=(
            f"{fund_code} · NAV / SMA / BOLL",
            "MACD (12, 26, 9)",
            "RSI (14) & KDJ (9,3,3, NAV proxy)",
            "NAV 方向強度 (proxy: 非標準 ADX)",
            "NAV 波動度 (proxy: 非標準 ATR)"
        )
    )

    # ================= 行 1: 主圖 =================
    fig.add_trace(go.Scatter(x=df["Date"], y=df["NAV"], mode="lines", name="NAV",
        line=dict(color=COLOR_MAP["NAV"], width=1.2),
        hovertemplate="%{x|%Y-%m-%d}<br>NAV: %{y:.4f}<extra></extra>"), row=1, col=1)

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

    # ================= 行 3: RSI & KDJ (NAV proxy) =================
    fig.add_trace(go.Scatter(x=df["Date"], y=df["RSI"], mode="lines", name="RSI", line=dict(color="#8e44ad", width=1.4)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df["Date"], y=df["KDJ_K"], mode="lines", name="KDJ K (NAV proxy)", line=dict(color="#ff9800", width=1.2)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df["Date"], y=df["KDJ_D"], mode="lines", name="KDJ D (NAV proxy)", line=dict(color="#1f77b4", width=1.2)), row=3, col=1)
    fig.add_hline(y=70, line_dash="dash", line_color="red", opacity=0.3, row=3, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color="green", opacity=0.3, row=3, col=1)

    # ================= 行 4: NAV 方向強度 (proxy) =================
    fig.add_trace(go.Scatter(x=df["Date"], y=df["NAV_DX"], mode="lines", name="NAV_DX (proxy)", line=dict(color="#000000", width=1.5)), row=4, col=1)
    fig.add_trace(go.Scatter(x=df["Date"], y=df["NAV_DI_Plus"], mode="lines", name="+DI (proxy)", line=dict(color="#26a69a", width=1, dash="dot")), row=4, col=1)
    fig.add_trace(go.Scatter(x=df["Date"], y=df["NAV_DI_Minus"], mode="lines", name="-DI (proxy)", line=dict(color="#ef5350", width=1, dash="dot")), row=4, col=1)
    fig.add_hline(y=25, line_dash="dash", line_color="gray", opacity=0.5, row=4, col=1)

    # ================= 行 5: NAV 波動度 (proxy) =================
    fig.add_trace(go.Scatter(x=df["Date"], y=df["NAV_VOL"], mode="lines", name="NAV_VOL (proxy)", line=dict(color="#17becf", width=1.5), fill="tozeroy", fillcolor="rgba(23,190,207,0.1)"), row=5, col=1)

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
    fig.update_yaxes(title_text="震盪指標（KDJ 為 NAV proxy）", range=[-20, 120], row=3, col=1)
    fig.update_yaxes(title_text="NAV 方向強度 (proxy)", range=[0, 100], row=4, col=1)
    fig.update_yaxes(title_text="NAV 波動度 (proxy)", row=5, col=1)

    out = save_dir / f"{fund_code}_nav_sma_boll.html"
    fig.write_html(out, include_plotlyjs="cdn")
    log.info(f"[{fund_code}] 綜合技術指標圖 → {out.name}")
    return out


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

    # 对齐日期：先取共同交易日的「价格」，再各自算收益率。
    # 【修正】原做法兩邊各自 pct_change 後才內連接，任一方休市就會錯配基準日。
    merged = df[["Date", "NAV"]].merge(
        bench[["Date", "BenchClose"]], on="Date", how="inner"
    ).sort_values("Date").dropna().reset_index(drop=True)
    if len(merged) < 31:
        log.warning(f"[{fund_code}] 基金与基准重叠日期不足 31 天，跳过超额收益图")
        return None

    merged["DailyRet"] = merged["NAV"].pct_change()
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

