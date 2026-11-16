"""End-to-end analysis pipeline for one fund."""

from datetime import datetime
from pathlib import Path
import json
import webbrowser

from fa.analysis.nav_estimator import estimate_qdii_nav
from fa.analysis.risk import compute_risk_metrics
from fa.analysis.technical import compute_technical
from fa.config import merge_config
from fa.data.aum import fetch_aum
from fa.data.fund_info import resolve_fund_mode
from fa.data.holdings import fetch_holdings
from fa.data.nav import fetch_nav
from fa.utils.cache import _prune_stale_outputs
from fa.utils.logging_setup import get_logger
from fa.viz.charts import plot_excess_return, plot_holdings, plot_risk_metrics, plot_technical, plot_trend


log = get_logger(__name__)

def _open_repository(cfg: dict):
    """Best-effort SQLite handle; estimation works without a database."""
    if not cfg.get("store_db", True):
        return None
    try:
        from fa.data.database import Repository

        return Repository(cfg.get("db_path"))
    except Exception as exc:  # noqa: BLE001 - 資料庫不可用時退回純檔案流程
        log.warning(f"資料庫不可用，改用純檔案模式: {exc}")
        return None


def analyze_fund(fund_code: str, base_output: Path = Path("output"),
                 cfg: dict = None, show: bool = False,
                 use_cache: bool = True) -> dict:
    cfg = merge_config(cfg)
    save_dir = base_output / fund_code
    save_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"===== 分析基金 {fund_code} =====")
    log.info(f"输出目录: {save_dir.resolve()}")

    # 注意：不要在分析開始時清空整個輸出目錄——那會把持倉緩存一併刪掉，
    # 令 use_cache / --no-cache 與 GUI 的「忽略緩存」選項全部失效。
    # 派生報告統一在分析成功結束後由 _prune_stale_outputs() 清理；
    # 分析中途失敗時，上一次的成功結果保持原樣。

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
    holdings_html = plot_holdings(holdings, fund_code, save_dir) if not holdings.empty else None
    if holdings.empty:
        # 避免下游模組讀到上一次分析遺留的舊持倉
        (save_dir / f"{fund_code}_holdings_clean.csv").unlink(missing_ok=True)

    # 3. 技术指标
    tech_df = compute_technical(nav_df, cfg, save_dir, fund_code)

    tech_html = plot_technical(tech_df, cfg, fund_code, save_dir)

    # 4. 风险指标
    metrics = compute_risk_metrics(nav_df, cfg, fund_code,
                                   benchmark=cfg.get("benchmark"),
                                   aum=aum_val)
    summary["metrics"] = metrics
    risk_html = plot_risk_metrics(
        nav_df, metrics, fund_code, save_dir,
        aum=aum_val,
        aum_date=aum_date,
    ) if metrics else None

    # 5. 累计超额收益曲线（需要基准）
    excess_html = None
    if cfg.get("benchmark"):
        excess_html = plot_excess_return(
            nav_df, fund_code, save_dir,
            benchmark=cfg["benchmark"],
            benchmark_market=cfg.get("benchmark_market", "auto"),
        )
    
    # 5. QDII 淨值估算（自動識別基金類型，可用 cfg["fund_type"] 覆蓋）
    qdii_estimate = None
    fund_mode, fund_profile = resolve_fund_mode(fund_code, cfg.get("fund_type", "auto"))
    summary["fund_profile"] = fund_profile
    summary["analysis_mode"] = fund_mode
    if fund_mode == "qdii":
        repository = _open_repository(cfg)
        try:
            qdii_estimate = estimate_qdii_nav(
                fund_code, save_dir,
                cfg={**cfg, "fund_name": fund_profile.get("name")},
                as_of=cfg.get("qdii_as_of"),
                use_cache=use_cache,
                repository=repository,
            )
        except Exception as exc:  # noqa: BLE001 - 估算失敗不應讓整份分析失敗
            log.exception(f"[{fund_code}] QDII 淨值估算失敗")
            qdii_estimate = {"status": "error", "fund_code": fund_code,
                             "message": str(exc), "holdings": []}
        finally:
            if repository is not None:
                repository.close()
    summary["qdii_estimate"] = qdii_estimate

    # 保存汇总
    qdii_files = {
        name: f"{fund_code}_{name}"
        for name in ("qdii_holdings.csv", "qdii_estimate.json", "qdii_estimates.csv",
                     "qdii_estimate_inputs.json")
        if (save_dir / f"{fund_code}_{name}").exists()
    }
    summary["files"] = {
        "nav_csv":        f"{fund_code}_daily_nav.csv",
        "trend_html":     trend_html.name if trend_html else None,
        "holdings_raw":   f"{fund_code}_holdings_raw.csv" if not holdings.empty else None,
        "holdings_clean": f"{fund_code}_holdings_clean.csv" if not holdings.empty else None,
        "holdings_html":  holdings_html.name if holdings_html else None,
        "tech_csv":       f"{fund_code}_daily_nav_sma_boll.csv",
        "tech_html":      tech_html.name if tech_html else None,
        "risk_html":      risk_html.name if risk_html else None,
        "excess_html":    excess_html.name if excess_html else None,
        "qdii_holdings_csv": qdii_files.get("qdii_holdings.csv"),
        "qdii_estimate_json": qdii_files.get("qdii_estimate.json"),
        "qdii_estimates_csv": qdii_files.get("qdii_estimates.csv"),
        "qdii_estimate_inputs_json": qdii_files.get("qdii_estimate_inputs.json"),
    }
    summary_path = save_dir / f"{fund_code}_summary.json"

    # 先確認成功，再寫 JSON：否則 JSON 裡會一直是初始化時的 success=false
    summary["success"] = True

    # 清理本次未重新生成、上一次遺留的派生報告（緩存與原始數據保留）
    keep = {name for name in summary["files"].values() if name}
    keep.add(summary_path.name)
    keep.add(f"{fund_code}_master_report.html")
    _prune_stale_outputs(save_dir, fund_code, keep)

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log.info(f"[{fund_code}] 汇总 → {summary_path.name}")

    if show:
        for h in [trend_html, holdings_html, tech_html, risk_html, excess_html]:
            if h and h.exists():
                webbrowser.open(h.as_uri())

    log.info(f"===== {fund_code} 分析完成 =====")
    return summary
