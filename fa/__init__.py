# -*- coding: utf-8 -*-
"""Fund Analyzer package.

Phase 3 result of the code review: the former 1960-line single module is
split by responsibility (config / data / analysis / viz / gui). The public
API is unchanged — ``from fa import analyze_fund`` keeps working.

Exports are resolved lazily: a plain ``import fa.backtesting`` must not pull in
the network stack (``requests`` / ``yfinance`` / ``akshare``) that the fetch
modules import at module scope, because V2.3.2 requires the backtesting package
to be importable without any networking dependency.  ``fa.__all__`` still lists
every public name, and ``from fa import <name>`` triggers the same import the
eager version performed.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    # config
    "BOLL_FILL_COLOR": ("fa.config", "BOLL_FILL_COLOR"),
    "BOLL_LINE_COLOR": ("fa.config", "BOLL_LINE_COLOR"),
    "BOLL_MID_COLOR": ("fa.config", "BOLL_MID_COLOR"),
    "CACHE_TTL_DAYS": ("fa.config", "CACHE_TTL_DAYS"),
    "COLOR_MAP": ("fa.config", "COLOR_MAP"),
    "DEFAULT_CONFIG": ("fa.config", "DEFAULT_CONFIG"),
    "DEFAULT_OUTPUT_DIR": ("fa.config", "DEFAULT_OUTPUT_DIR"),
    "PROJECT_ROOT": ("fa.config", "PROJECT_ROOT"),
    "REQUEST_TIMEOUT": ("fa.config", "REQUEST_TIMEOUT"),
    "merge_config": ("fa.config", "merge_config"),
    # errors
    "AnalysisError": ("fa.errors", "AnalysisError"),
    "CacheError": ("fa.errors", "CacheError"),
    "ConfigurationError": ("fa.errors", "ConfigurationError"),
    "DataFetchError": ("fa.errors", "DataFetchError"),
    "FundAnalyzerError": ("fa.errors", "FundAnalyzerError"),
    # utils
    "get_logger": ("fa.utils.logging_setup", "get_logger"),
    "setup_logging": ("fa.utils.logging_setup", "setup_logging"),
    "latest_quarter_label": ("fa.utils.dates", "latest_quarter_label"),
    "to_yf_ticker": ("fa.utils.tickers", "to_yf_ticker"),
    "DATA_FILE_SUFFIXES": ("fa.utils.cache", "DATA_FILE_SUFFIXES"),
    "_prune_stale_outputs": ("fa.utils.cache", "_prune_stale_outputs"),
    # data
    "fetch_aum": ("fa.data.aum", "fetch_aum"),
    "fetch_nav": ("fa.data.nav", "fetch_nav"),
    "fetch_benchmark": ("fa.data.benchmark", "fetch_benchmark"),
    "fetch_holdings": ("fa.data.holdings", "fetch_holdings"),
    "_clean_holdings": ("fa.data.holdings", "_clean_holdings"),
    "top_holdings": ("fa.data.holdings", "top_holdings"),
    "_yf_download_df": ("fa.data.market", "_yf_download_df"),
    "_yf_run": ("fa.data.market", "_yf_run"),
    "_yf_ticker_history": ("fa.data.market", "_yf_ticker_history"),
    "fetch_qdii_holdings": ("fa.data.qdii_holdings", "fetch_qdii_holdings"),
    "load_quarters_from_db": ("fa.data.qdii_holdings", "load_quarters_from_db"),
    "save_quarters_to_db": ("fa.data.qdii_holdings", "save_quarters_to_db"),
    "select_point_in_time": ("fa.data.qdii_holdings", "select_point_in_time"),
    "fetch_holding_price": ("fa.data.qdii_market", "fetch_holding_price"),
    "fetch_holding_prices": ("fa.data.qdii_market", "fetch_holding_prices"),
    "fetch_fx_rates": ("fa.data.fx", "fetch_fx_rates"),
    "fetch_fx_series": ("fa.data.fx", "fetch_fx_series"),
    "fetch_fund_profile": ("fa.data.fund_info", "fetch_fund_profile"),
    "resolve_fund_mode": ("fa.data.fund_info", "resolve_fund_mode"),
    # analysis
    "compute_technical": ("fa.analysis.technical", "compute_technical"),
    "compute_risk_metrics": ("fa.analysis.risk", "compute_risk_metrics"),
    "fetch_post_market_estimate": ("fa.analysis.post_market", "fetch_post_market_estimate"),
    "estimate_qdii_nav": ("fa.analysis.nav_estimator", "estimate_qdii_nav"),
    "run_backtest": ("fa.analysis.estimation_backtest", "run_backtest"),
    "order_trade_date": ("fa.analysis.valuation_dates", "order_trade_date"),
    "resolve_valuation_dates": ("fa.analysis.valuation_dates", "resolve_valuation_dates"),
    "calculate_asset": ("fa.analysis.returns", "calculate_asset"),
    "get_calculator": ("fa.analysis.returns", "get_calculator"),
    "value_portfolio": ("fa.analysis.valuation", "value_portfolio"),
    # viz
    "plot_excess_return": ("fa.viz.charts", "plot_excess_return"),
    "plot_holdings": ("fa.viz.charts", "plot_holdings"),
    "plot_risk_metrics": ("fa.viz.charts", "plot_risk_metrics"),
    "plot_technical": ("fa.viz.charts", "plot_technical"),
    "plot_trend": ("fa.viz.charts", "plot_trend"),
    "generate_master_report": ("fa.viz.master", "generate_master_report"),
    # pipeline / cli
    "analyze_fund": ("fa.pipeline", "analyze_fund"),
    "main": ("fa.cli", "main"),
}

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


def __getattr__(name: str) -> Any:
    """Import a public name on first access (PEP 562)."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
