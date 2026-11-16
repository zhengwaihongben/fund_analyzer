# -*- coding: utf-8 -*-
"""Fund Analyzer package.

Phase 3 result of the code review: the former 1960-line single module is
split by responsibility (config / data / analysis / viz / gui). The public
API is unchanged — ``import fund_analyzer`` re-exports everything listed in
``__all__`` below, so existing callers keep working.
"""
from fa.config import (  # noqa: F401
    BOLL_FILL_COLOR,
    BOLL_LINE_COLOR,
    BOLL_MID_COLOR,
    CACHE_TTL_DAYS,
    COLOR_MAP,
    DEFAULT_CONFIG,
    DEFAULT_OUTPUT_DIR,
    PROJECT_ROOT,
    REQUEST_TIMEOUT,
    merge_config,
)
from fa.errors import (  # noqa: F401
    AnalysisError,
    CacheError,
    ConfigurationError,
    DataFetchError,
    FundAnalyzerError,
)
from fa.utils.logging_setup import get_logger, setup_logging  # noqa: F401
from fa.utils.dates import latest_quarter_label  # noqa: F401
from fa.utils.tickers import to_yf_ticker  # noqa: F401
from fa.utils.cache import (  # noqa: F401
    DATA_FILE_SUFFIXES,
    _prune_stale_outputs,
)
from fa.data.aum import fetch_aum  # noqa: F401
from fa.data.nav import fetch_nav  # noqa: F401
from fa.data.benchmark import fetch_benchmark  # noqa: F401
from fa.data.holdings import (  # noqa: F401
    _clean_holdings,
    fetch_holdings,
    top_holdings,
)
from fa.data.market import (  # noqa: F401
    _yf_download_df,
    _yf_run,
    _yf_ticker_history,
)
from fa.data.qdii_holdings import (  # noqa: F401
    fetch_qdii_holdings,
    load_quarters_from_db,
    save_quarters_to_db,
    select_point_in_time,
)
from fa.data.qdii_market import fetch_holding_price, fetch_holding_prices  # noqa: F401
from fa.data.fx import fetch_fx_rates, fetch_fx_series  # noqa: F401
from fa.data.fund_info import fetch_fund_profile, resolve_fund_mode  # noqa: F401
from fa.analysis.technical import compute_technical  # noqa: F401
from fa.analysis.risk import compute_risk_metrics  # noqa: F401
from fa.analysis.post_market import fetch_post_market_estimate  # noqa: F401
from fa.analysis.nav_estimator import estimate_qdii_nav  # noqa: F401
from fa.analysis.estimation_backtest import run_backtest  # noqa: F401
from fa.analysis.valuation_dates import order_trade_date, resolve_valuation_dates  # noqa: F401
from fa.analysis.returns import calculate_asset, get_calculator  # noqa: F401
from fa.analysis.valuation import value_portfolio  # noqa: F401
from fa.viz.charts import (  # noqa: F401
    plot_excess_return,
    plot_holdings,
    plot_risk_metrics,
    plot_technical,
    plot_trend,
)
from fa.viz.master import generate_master_report  # noqa: F401
from fa.pipeline import analyze_fund  # noqa: F401
from fa.cli import main  # noqa: F401

__all__ = [
    "DEFAULT_CONFIG", "COLOR_MAP", "BOLL_LINE_COLOR", "BOLL_FILL_COLOR",
    "BOLL_MID_COLOR", "PROJECT_ROOT", "CACHE_TTL_DAYS", "REQUEST_TIMEOUT",
    "DEFAULT_OUTPUT_DIR", "merge_config",
    "FundAnalyzerError", "ConfigurationError", "DataFetchError",
    "CacheError", "AnalysisError",
    "setup_logging", "get_logger",
    "latest_quarter_label", "to_yf_ticker",
    "DATA_FILE_SUFFIXES", "_prune_stale_outputs",
    "fetch_aum", "fetch_nav", "fetch_benchmark", "fetch_holdings",
    "_clean_holdings", "top_holdings",
    "_yf_run", "_yf_download_df", "_yf_ticker_history",
    "fetch_qdii_holdings", "select_point_in_time",
    "load_quarters_from_db", "save_quarters_to_db",
    "fetch_holding_price", "fetch_holding_prices",
    "fetch_fx_rates", "fetch_fx_series",
    "fetch_fund_profile", "resolve_fund_mode",
    "compute_technical", "compute_risk_metrics",
    "fetch_post_market_estimate", "estimate_qdii_nav", "run_backtest",
    "resolve_valuation_dates", "order_trade_date",
    "calculate_asset", "get_calculator", "value_portfolio",
    "plot_trend", "plot_holdings", "plot_technical", "plot_risk_metrics",
    "plot_excess_return",
    "generate_master_report", "analyze_fund", "main",
]
