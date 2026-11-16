# -*- coding: utf-8 -*-
"""V2 data models: assets, holdings, market data, return results, estimations.

These dataclasses are the contract between the data layer, the valuation
engine, the database and the GUI.  Nothing in this package touches the
network, the filesystem or Tk: it is pure data plus validation.
"""
from __future__ import annotations

from fa.models.asset import (
    BASE_CURRENCY,
    PRICED_ASSET_TYPES,
    SUPPORTED_ASSET_TYPES,
    Asset,
    AssetType,
    AssetValidationError,
    normalise_currency,
)
from fa.models.estimation import (
    MODEL_VERSION,
    EstimationResult,
    EstimationRun,
    new_run_id,
)
from fa.models.holding import HoldingSnapshot, HoldingValidationError, snapshot_from_rows
from fa.models.market_data import (
    FxSeries,
    MarketDataError,
    PricePoint,
    PriceSeries,
)
from fa.models.return_result import (
    AssetReturnResult,
    PortfolioReturnResult,
    PortfolioStatus,
    ReturnStatus,
    shapley_contributions,
)

__all__ = [
    "BASE_CURRENCY",
    "PRICED_ASSET_TYPES",
    "SUPPORTED_ASSET_TYPES",
    "Asset",
    "AssetType",
    "AssetValidationError",
    "normalise_currency",
    "MODEL_VERSION",
    "EstimationResult",
    "EstimationRun",
    "new_run_id",
    "HoldingSnapshot",
    "HoldingValidationError",
    "snapshot_from_rows",
    "FxSeries",
    "MarketDataError",
    "PricePoint",
    "PriceSeries",
    "AssetReturnResult",
    "PortfolioReturnResult",
    "PortfolioStatus",
    "ReturnStatus",
    "shapley_contributions",
]
