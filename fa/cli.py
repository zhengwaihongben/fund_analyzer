"""Command-line entry point."""

from pathlib import Path
import argparse
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
    }
    
    analyze_fund(
        fund_code=args.fund_code,
        base_output=Path(args.output),
        cfg=cfg,
        show=args.show,
        use_cache=not args.no_cache,
    )
