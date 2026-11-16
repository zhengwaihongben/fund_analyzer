# -*- coding: utf-8 -*-
"""Equity return calculator: adjusted close, base and target aligned."""
from __future__ import annotations

from datetime import date
from typing import Any

from fa.analysis.returns.base import ReturnCalculatorError, priced_return
from fa.models.asset import Asset, AssetType
from fa.models.market_data import FxSeries, PriceSeries
from fa.models.return_result import AssetReturnResult


class EquityReturnCalculator:
    """``r_local = P1 / P0 - 1`` on adjusted close (raw close only as fallback)."""

    asset_types = frozenset({AssetType.EQUITY})

    def calculate(
        self,
        asset: Asset,
        *,
        base_date: date,
        target_date: date,
        prices: PriceSeries | None,
        fx: FxSeries | None,
        calendar: Any = None,
    ) -> AssetReturnResult:
        if not isinstance(asset, Asset):
            raise ReturnCalculatorError("calculate() 需要 Asset 物件")
        return priced_return(
            asset,
            base_date=base_date,
            target_date=target_date,
            prices=prices,
            fx=fx,
            calendar=calendar,
            allow_raw_close=True,
            require_total_return=False,
        )


__all__ = ["EquityReturnCalculator"]
