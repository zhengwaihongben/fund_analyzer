# -*- coding: utf-8 -*-
"""Bond ETF return calculator.

``r_local = TR1 / TR0 - 1`` where ``TR`` is a **total return** series.  A bond
ETF's price alone is not its return — coupons are paid out and the price drops
by the distribution, so using a raw close systematically understates the
return.  V2 takes the conservative rule: only a series that is known to be
total return (a total-return feed, or an adjusted close that already
reinvests distributions) may be used; a raw price is rejected as
``unsupported-asset`` rather than silently treated as total return.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fa.analysis.returns.base import ReturnCalculatorError, priced_return
from fa.models.asset import Asset, AssetType
from fa.models.market_data import FxSeries, PriceSeries
from fa.models.return_result import AssetReturnResult


class BondEtfReturnCalculator:
    """``r_local = TR1 / TR0 - 1``; rejects price-only series."""

    asset_types = frozenset({AssetType.BOND_ETF})

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
            allow_raw_close=False,
            require_total_return=True,
        )


__all__ = ["BondEtfReturnCalculator"]
