"""Risk / performance metrics and benchmark-relative statistics."""

import numpy as np
import pandas as pd

from fa.data.benchmark import fetch_benchmark
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

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

    # 【修正】Downside deviation 應對「所有 N 筆」觀測取 min(r_t - T, 0) 的均方根，
    # T = 每日無風險利率；原做法只拿負收益樣本再算標準差（等於用樣本均值當目標），
    # 會系統性低估分母、令 Sortino 虛高。
    target_daily = rf / td
    downside_diff = np.minimum(ret - target_daily, 0.0)
    dd_dev = float(np.sqrt(np.mean(downside_diff ** 2)) * np.sqrt(td))
    sortino = (ann_ret - rf) / dd_dev if dd_dev > 0 else np.nan

    nav = df["NAV"]
    dd = (nav - nav.cummax()) / nav.cummax()
    max_dd = dd.min()
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else np.nan

    info_ratio, te, ir_source = np.nan, np.nan, "N/A"
    if benchmark:
        try:
            bench = fetch_benchmark(benchmark, market=cfg.get("benchmark_market", "auto"))
            if not bench.empty:
                # 【修正】先按「共同交易日」對齊價格，再各自算日收益；
                # 原做法是兩邊各自 pct_change 之後才 merge，遇到任一方休市就會
                # 拿不同基準日相減，令 TE / IR 失真。
                px = df[["Date", "NAV"]].merge(
                    bench[["Date", "BenchClose"]], on="Date", how="inner"
                ).sort_values("Date").dropna()
                if len(px) >= 31:
                    px["FundRet"] = px["NAV"].pct_change()
                    px["BenchRet"] = px["BenchClose"].pct_change()
                    px = px.dropna()
                    active = px["FundRet"] - px["BenchRet"]
                    te = active.std() * np.sqrt(td)
                    ann_active = active.mean() * td
                    info_ratio = ann_active / te if te > 0 else np.nan
                    ir_source = benchmark
                    if "NDX" in str(benchmark).upper():
                        log.info(
                            f"[{fund_code}] IR/TE 以 {len(px)} 個共同交易日計算；"
                            "基金為人民幣計價、基準為美元指數，未做匯率調整"
                        )
                else:
                    log.warning(f"[{fund_code}] 基金與基準共同交易日不足 31 天，跳過 IR")
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
        "downside_deviation": r(dd_dev),
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
