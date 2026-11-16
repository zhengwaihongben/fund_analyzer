"""Command-line entry point."""

from pathlib import Path
import argparse
import json
import os

from fa.config import PROJECT_ROOT
from fa.pipeline import analyze_fund
from fa.utils.logging_setup import setup_logging
from fa.utils.net import apply_default_timeout

def main():
    os.chdir(PROJECT_ROOT)

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
    parser.add_argument("--fund-type", default="auto", choices=["auto", "standard", "qdii"],
                        help="基金類型（auto=依基金資料自動判斷，qdii=強制啟用 QDII 淨值估算）")
    parser.add_argument("--qdii-as-of", default=None,
                        help="QDII 估算的基準時間（YYYY-MM-DD[THH:MM]），用於補算歷史日期")
    parser.add_argument("--qdii-backtest", action="store_true",
                        help="對已保存的 QDII 估算記錄做回測，輸出 MAE/RMSE")
    parser.add_argument("--no-cache", action="store_true",
                        help="忽略持仓缓存，强制重新拉取")
    parser.add_argument("--show", action="store_true",
                        help="生成完成后用浏览器打开所有图表")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    setup_logging(args.verbose)
    apply_default_timeout()

    # AUM 由 fetch_aum() 自動獲取，不再由 CLI 傳入（--aum 從未定義過）
    cfg = {
        "risk_free":  args.risk_free,
        "benchmark":  args.benchmark or None,
        "benchmark_market": args.benchmark_market,
        "start_date": args.start_date,
        "fund_type":  args.fund_type,
        "qdii_as_of": args.qdii_as_of,
    }

    summary = analyze_fund(
        fund_code=args.fund_code,
        base_output=Path(args.output),
        cfg=cfg,
        show=args.show,
        use_cache=not args.no_cache,
    )

    estimate = summary.get("qdii_estimate")
    if estimate:
        _print_qdii_summary(args.fund_code, estimate)

    if args.qdii_backtest:
        from fa.analysis.estimation_backtest import run_backtest
        result = run_backtest(args.fund_code, Path(args.output) / args.fund_code)
        print(json.dumps(result, ensure_ascii=False, indent=2))


def _print_qdii_summary(fund_code: str, estimate: dict) -> None:
    """Compact QDII block for CLI users (the GUI shows the same numbers)."""
    status = estimate.get("status")
    if status in ("up-to-date", "insufficient-data", "error", "no-nav"):
        print(f"[QDII] {fund_code} 未產生估算：{estimate.get('message') or status}")
        return
    print(f"\n[QDII] {fund_code} 淨值估算")
    print(f"  目標估值日      : {estimate.get('target_nav_date')}"
          f"（基準 {estimate.get('base_nav_date')}，落後 {estimate.get('nav_lag_days')} 個交易日）")
    print(f"  官方淨值 → 估算 : {estimate.get('base_nav')} → {estimate.get('estimated_nav')}")
    print(f"  估算漲跌        : {estimate.get('estimated_return', 0) * 100:+.2f}%")
    print(f"  持倉覆蓋率      : {estimate.get('coverage_ratio', 0) * 100:.1f}%"
          f"（{estimate.get('holdings_included')} 隻採用，"
          f"{estimate.get('holdings_excluded')} 隻排除；"
          f"未覆蓋 {estimate.get('uncovered_weight', 0) * 100:.1f}% 按 0% 計算）")
    print(f"  貢獻拆解        : 本地 {estimate.get('local_contribution', 0) * 100:+.2f}%"
          f" + 匯率 {estimate.get('fx_contribution', 0) * 100:+.2f}%"
          f" + 交叉項 {estimate.get('interaction_contribution', 0) * 100:+.2f}%")
    disclosure_date = estimate.get("disclosure_date") or "--"
    if estimate.get("disclosure_date_estimated", True):
        disclosure_date += "（推算）"
    print(f"  持倉披露        : {estimate.get('holdings_quarter')}"
          f"（{estimate.get('holdings_date')}，披露日 {disclosure_date}，"
          f"僅用目標日已披露季度）")
    if estimate.get("warnings"):
        for warning in estimate["warnings"]:
            print(f"  ⚠ {warning}")
