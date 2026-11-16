# -*- coding: utf-8 -*-
"""Asset return calculators (V2.2).

One calculator per supported asset class, all returning the same
:class:`~fa.models.return_result.AssetReturnResult`:

===========  ==========================================================
Equity       ``P1 / P0 - 1`` on adjusted close (raw close only as fallback)
ETF          equity maths + exchange/currency identification
Bond ETF     ``TR1 / TR0 - 1``; a raw price is rejected, not understated
Cash         fixed-rate compounding over calendar days, then FX to CNY
===========  ==========================================================

``AssetType.UNKNOWN`` is preserved and *excluded*: it is reported as
``unsupported-asset`` rather than guessed into one of the classes above.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fa.analysis.returns.base import (
    FxOutcome,
    ReturnCalculator,
    ReturnCalculatorError,
    cny_return,
    convert_to_cny,
    priced_return,
    series_from_frame,
)
from fa.analysis.returns.bond_etf import BondEtfReturnCalculator
from fa.analysis.returns.cash import CashReturnCalculator, cash_return
from fa.analysis.returns.equity import EquityReturnCalculator
from fa.analysis.returns.etf import EtfReturnCalculator
from fa.analysis.returns.fx import compute_fx_return, describe_fx
from fa.models.asset import Asset, AssetType
from fa.models.market_data import FxSeries, PriceSeries
from fa.models.return_result import AssetReturnResult, ReturnStatus

_CALCULATORS: dict[AssetType, ReturnCalculator] = {
    AssetType.EQUITY: EquityReturnCalculator(),
    AssetType.ETF: EtfReturnCalculator(),
    AssetType.BOND_ETF: BondEtfReturnCalculator(),
    AssetType.CASH: CashReturnCalculator(),
}


def supported_asset_types() -> frozenset[AssetType]:
    """Asset classes that have a return calculator in this build."""
    return frozenset(_CALCULATORS)


def get_calculator(asset_type: AssetType | str) -> ReturnCalculator:
    """Look up the calculator for ``asset_type`` (``KeyError`` when unknown)."""
    try:
        parsed = asset_type if isinstance(asset_type, AssetType) else AssetType.parse(asset_type)
    except ValueError as exc:  # pragma: no cover - parse is permissive
        raise ReturnCalculatorError(f"未知資產類型: {asset_type!r}") from exc
    try:
        return _CALCULATORS[parsed]
    except KeyError as exc:
        raise ReturnCalculatorError(f"沒有 {parsed.value} 的報酬計算器") from exc


def calculate_asset(
    asset: Asset,
    *,
    base_date: date,
    target_date: date,
    prices: PriceSeries | None = None,
    fx: FxSeries | None = None,
    calendar: Any = None,
) -> AssetReturnResult:
    """Calculate one asset's CNY return, excluding unknown asset types."""
    if not isinstance(asset, Asset):
        raise ReturnCalculatorError("calculate_asset() 需要 Asset 物件")
    calculator = _CALCULATORS.get(asset.asset_type)
    if calculator is None:
        return AssetReturnResult(
            asset_id=asset.asset_id,
            status=ReturnStatus.UNSUPPORTED_ASSET,
            weight=asset.weight,
            currency=asset.currency,
            warnings=[f"未知資產類型 {asset.asset_type.value}，保留但不估值"],
        )
    return calculator.calculate(
        asset,
        base_date=base_date,
        target_date=target_date,
        prices=prices,
        fx=fx,
        calendar=calendar,
    )


__all__ = [
    "BondEtfReturnCalculator",
    "CashReturnCalculator",
    "EquityReturnCalculator",
    "EtfReturnCalculator",
    "FxOutcome",
    "ReturnCalculator",
    "ReturnCalculatorError",
    "calculate_asset",
    "cash_return",
    "cny_return",
    "compute_fx_return",
    "convert_to_cny",
    "describe_fx",
    "get_calculator",
    "priced_return",
    "series_from_frame",
    "supported_asset_types",
]
