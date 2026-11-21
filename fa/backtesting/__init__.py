# -*- coding: utf-8 -*-
"""V2.3.2 strategy backtesting (Stages B, C and D).

The package is intentionally split so the engine stays pure:

``models.py``      frozen dataclasses, validation, no I/O
``data_access.py`` PIT-only reads through a Repository
``costs.py``       commission / slippage / FX-spread arithmetic, no state
``engine.py``      the simulation loop, reads only through ``DataAccess``
``metrics.py``     equity curve + trades → metrics, no prices, no database
``runner.py``      config, fingerprint, orchestration and persistence

Importing this package must not pull in ``requests`` / ``yfinance`` /
``akshare``; ``fa/__init__.py`` resolves its public names lazily to keep that
promise.
"""
from .costs import TradeCosts
from .data_access import BacktestDataError, DataAccess, utc_now
from .engine import EngineResult, run_engine
from .metrics import compute_metrics
from .models import (
    BacktestMetrics,
    BacktestRun,
    BacktestSpecError,
    CostModel,
    EquityPoint,
    Instrument,
    Position,
    RebalanceFrequency,
    RebalanceRule,
    StrategySpec,
    Trade,
    Weighting,
    with_initial_capital,
)
from .runner import MODEL_VERSION, canonical_spec, run_backtest

__all__ = [
    "BacktestDataError",
    "BacktestMetrics",
    "BacktestRun",
    "BacktestSpecError",
    "CostModel",
    "DataAccess",
    "EngineResult",
    "EquityPoint",
    "Instrument",
    "MODEL_VERSION",
    "Position",
    "RebalanceFrequency",
    "RebalanceRule",
    "StrategySpec",
    "Trade",
    "TradeCosts",
    "Weighting",
    "canonical_spec",
    "compute_metrics",
    "run_backtest",
    "run_engine",
    "utc_now",
    "with_initial_capital",
]
