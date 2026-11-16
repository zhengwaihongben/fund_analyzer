# -*- coding: utf-8 -*-
"""Cash return calculator.

``1 + r_cash = (1 + y / 365) ** Δd`` — a fixed annual rate compounded over the
actual number of calendar days between the two valuation dates (V2.2 does not
model a daily rate curve).

The rate must come from somewhere explicit: ``policy_rate``, ``market_rate`` or
``manual``.  When none is available the configured assumption is used, recorded
as ``source="assumption"`` and flagged with a warning — a missing rate is never
silently turned into 0 %.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fa.analysis.returns.base import (
    ReturnCalculatorError,
    convert_to_cny,
    cny_return,
)
from fa.config import CASH_ASSUMPTION_RATE
from fa.models.asset import Asset, AssetType, normalise_currency
from fa.models.market_data import FxSeries, PriceSeries
from fa.models.return_result import AssetReturnResult, ReturnStatus

RATE_SOURCES = ("policy_rate", "market_rate", "manual", "assumption")


def cash_return(annual_rate: float, days: int) -> float:
    """Compound a fixed annual rate over ``days`` calendar days."""
    if days <= 0:
        return 0.0
    return (1.0 + annual_rate / 365.0) ** days - 1.0


class CashReturnCalculator:
    """Fixed-rate cash accrual, converted to CNY when the cash is foreign."""

    asset_types = frozenset({AssetType.CASH})

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

        currency = normalise_currency(asset.currency)
        warnings: list[str] = []
        rate = asset.interest_rate
        source = (asset.interest_rate_source or "").strip().lower()
        if rate is None:
            rate = CASH_ASSUMPTION_RATE
            source = "assumption"
            warnings.append(
                f"現金缺少利率來源，改用假設年利率 {rate * 100:.2f}%（assumption）"
            )
        elif source not in RATE_SOURCES:
            source = "manual" if source else "manual"
        rate = float(rate)

        days = (target_date - base_date).days
        local = cash_return(rate, days)

        fx_outcome = convert_to_cny(currency, fx, base_date, target_date, calendar=calendar)
        warnings.extend(fx_outcome.warnings)
        if not fx_outcome.ok:
            return AssetReturnResult(
                asset_id=asset.asset_id,
                status=fx_outcome.status,
                weight=asset.weight,
                currency=currency,
                local_return=local,
                base_fx_date=fx_outcome.base_fx_date,
                target_fx_date=fx_outcome.target_fx_date,
                warnings=warnings,
                extra={"annual_rate": rate, "rate_source": source, "days": days},
            )

        return AssetReturnResult(
            asset_id=asset.asset_id,
            status=ReturnStatus.OK,
            weight=asset.weight,
            currency=currency,
            local_return=local,
            fx_return=fx_outcome.fx_return,
            return_cny=cny_return(local, fx_outcome.fx_return or 0.0),
            base_fx_date=fx_outcome.base_fx_date,
            target_fx_date=fx_outcome.target_fx_date,
            warnings=warnings,
            extra={"annual_rate": rate, "rate_source": source, "days": days},
        )


__all__ = ["CashReturnCalculator", "cash_return", "RATE_SOURCES"]
