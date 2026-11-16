# -*- coding: utf-8 -*-
"""ETF return calculator.

An ETF behaves like an equity for the return maths, but it carries two extra
facts that must not be ignored: **which exchange** the quote comes from (that
decides the trading calendar and therefore the staleness) and **which currency**
it is quoted in (a QDII fund can hold a USD ETF, a HKD ETF and a JPY ETF in the
same portfolio, so a single page-wide currency would be wrong).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fa.analysis.returns.base import ReturnCalculatorError, priced_return
from fa.models.asset import Asset, AssetType, normalise_currency
from fa.models.market_data import FxSeries, PriceSeries
from fa.models.return_result import AssetReturnResult


class EtfReturnCalculator:
    """``r_local = P1 / P0 - 1`` with exchange/currency identification."""

    asset_types = frozenset({AssetType.ETF})

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

        result = priced_return(
            asset,
            base_date=base_date,
            target_date=target_date,
            prices=prices,
            fx=fx,
            calendar=calendar,
            allow_raw_close=True,
            require_total_return=False,
        )

        declared = normalise_currency(asset.currency)
        quoted = normalise_currency(prices.currency) if prices is not None else ""
        if declared and quoted and declared != quoted:
            result.warnings.append(
                f"ETF 宣告幣種 {declared} 與行情幣種 {quoted} 不一致，以行情為準"
            )
        if not declared and quoted:
            result.currency = quoted
        exchange = asset.exchange or (prices.market if prices is not None else "")
        if not exchange:
            result.warnings.append("未識別 ETF 交易所，交易日曆退回週末規則")
        return result


__all__ = ["EtfReturnCalculator"]
