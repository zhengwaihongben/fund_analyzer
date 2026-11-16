# -*- coding: utf-8 -*-
"""Shared plumbing for the V2 asset return calculators.

Every calculator answers the same question — "what did one unit of this asset
return in CNY between ``base_date`` and ``target_date``" — and returns the same
:class:`~fa.models.return_result.AssetReturnResult`, so portfolio valuation never
has to know how the number was produced.

The functions here implement the parts that are genuinely common: aligning a
price/FX series (delegated to :mod:`fa.analysis.price_alignment`), converting a
local return into CNY, and turning the outcome into a result object.  The
asset-specific rules (which price column counts, whether a total return series
is required, how cash accrues) live in the individual calculators.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from fa.analysis.price_alignment import Alignment, align_fx, align_price
from fa.models.asset import Asset, AssetType, normalise_currency
from fa.models.market_data import FxSeries, PricePoint, PriceSeries
from fa.models.return_result import AssetReturnResult, ReturnStatus


class ReturnCalculatorError(ValueError):
    """Raised when a calculator is asked something it cannot answer."""


class ReturnCalculator(Protocol):
    """Uniform interface implemented by every asset return calculator."""

    asset_types: frozenset[AssetType]

    def calculate(
        self,
        asset: Asset,
        *,
        base_date: date,
        target_date: date,
        prices: PriceSeries | None,
        fx: FxSeries | None,
        calendar: Any = None,
    ) -> AssetReturnResult: ...


def series_from_frame(
    frame,
    *,
    ticker: str,
    market: str = "",
    currency: str = "",
    source: str = "",
    price_type: str = "adjusted_close",
) -> PriceSeries:
    """Build a :class:`PriceSeries` from a quotes DataFrame.

    Accepts the columns produced by the data layer (``Date`` plus ``Close`` /
    ``AdjClose`` / ``Price``); rows without a usable date or close are skipped
    rather than guessed.
    """
    if frame is None or len(frame) == 0:
        return PriceSeries(ticker=ticker, market=market, currency=currency, source=source)

    points: list[PricePoint] = []
    if hasattr(frame, "to_dict"):
        rows = frame.to_dict("records")
    else:  # pragma: no cover - defensive, callers pass a DataFrame
        rows = list(frame)
    for row in rows:
        day = row.get("Date") or row.get("date")
        if day is None:
            continue
        try:
            day = day.date() if hasattr(day, "date") else date.fromisoformat(str(day)[:10])
        except (TypeError, ValueError):
            continue
        close = row.get("Close", row.get("close"))
        adjusted = row.get("AdjClose", row.get("adj_close", row.get("Price")))
        if close is None or (isinstance(close, float) and close != close):  # NaN
            close = adjusted
        if adjusted is None or (isinstance(adjusted, float) and adjusted != adjusted):
            adjusted = None
        if close is None or (isinstance(close, float) and close != close):
            continue
        points.append(
            PricePoint(
                price_date=day,
                close=float(close),
                adj_close=float(adjusted) if adjusted is not None else None,
                market=market,
                currency=currency,
                price_type=price_type,
                source=source,
            )
        )
    return PriceSeries.from_points(
        ticker, points, market=market, currency=currency, source=source
    )


@dataclass
class FxOutcome:
    """Result of converting a local return into CNY."""

    status: ReturnStatus = ReturnStatus.OK
    fx_return: float | None = None
    base_fx_date: date | None = None
    target_fx_date: date | None = None
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status.is_ok


def cny_return(local_return: float, fx_return: float) -> float:
    """``1 + r_CNY = (1 + r_local) * (1 + r_fx)``."""
    return (1.0 + local_return) * (1.0 + fx_return) - 1.0


def convert_to_cny(
    currency: str | None,
    fx: FxSeries | None,
    base_date: date,
    target_date: date,
    calendar: Any = None,
) -> FxOutcome:
    """FX leg of an asset return, in the project-wide ``CNY per unit`` direction.

    A CNY asset needs no FX data; an unknown currency is *not* CNY and an empty
    currency is never silently treated as CNY either.
    """
    code = normalise_currency(currency)
    if not code:
        return FxOutcome(status=ReturnStatus.MISSING_CURRENCY)
    if code in ("CNY", "RMB"):
        return FxOutcome(fx_return=0.0)
    if fx is None:
        return FxOutcome(status=ReturnStatus.MISSING_FX)

    base_align = align_fx(fx, base_date, calendar=calendar, base=True)
    target_align = align_fx(fx, target_date, calendar=calendar)
    warnings: list[str] = []
    for alignment, label in ((base_align, "基準日"), (target_align, "目標日")):
        if alignment.excluded:
            return FxOutcome(
                status=ReturnStatus.MISSING_FX,
                base_fx_date=base_align.actual_date,
                target_fx_date=target_align.actual_date,
                warnings=(f"{code} 匯率在{label}過期不可用",),
            )
        if alignment.is_stale:
            warnings.append(
                f"{code} 匯率在{label}落後 {alignment.stale_sessions} 個交易日"
            )
    if not base_align.ok:
        return FxOutcome(
            status=ReturnStatus.MISSING_FX,
            base_fx_date=base_align.actual_date,
            target_fx_date=target_align.actual_date,
            warnings=tuple(warnings),
        )
    if not target_align.ok:
        return FxOutcome(
            status=ReturnStatus.MISSING_FX,
            base_fx_date=base_align.actual_date,
            target_fx_date=target_align.actual_date,
            warnings=tuple(warnings),
        )

    rate_base = float(base_align.value)
    rate_target = float(target_align.value)
    if rate_base == 0:
        return FxOutcome(
            status=ReturnStatus.MISSING_FX,
            base_fx_date=base_align.actual_date,
            target_fx_date=target_align.actual_date,
            warnings=tuple(warnings) + (f"{code} 基準日匯率為 0，無法換算",),
        )
    return FxOutcome(
        fx_return=rate_target / rate_base - 1.0,
        base_fx_date=base_align.actual_date,
        target_fx_date=target_align.actual_date,
        warnings=tuple(warnings),
    )


def _point_value(point: PricePoint, *, allow_adjusted: bool = True) -> tuple[float, bool]:
    """Return ``(value, used_adjusted_close)`` for one observation."""
    if allow_adjusted and point.adj_close is not None:
        return float(point.adj_close), True
    return float(point.close), False


def priced_return(
    asset: Asset,
    *,
    base_date: date,
    target_date: date,
    prices: PriceSeries | None,
    fx: FxSeries | None,
    calendar: Any = None,
    allow_raw_close: bool = True,
    require_total_return: bool = False,
) -> AssetReturnResult:
    """Return of a priced asset (equity / ETF / bond ETF).

    ``require_total_return`` is used by the bond ETF calculator: a raw close is
    a price move, not a total return, so it is rejected instead of silently
    understating the return.
    """
    warnings: list[str] = []
    currency = normalise_currency(asset.currency or (prices.currency if prices else ""))

    if prices is None or len(prices) == 0:
        return AssetReturnResult(
            asset_id=asset.asset_id,
            status=ReturnStatus.MISSING_PRICE,
            weight=asset.weight,
            currency=currency,
            warnings=["沒有價格資料"],
        )

    base_align = align_price(prices, base_date, calendar=calendar, base=True)
    target_align = align_price(prices, target_date, calendar=calendar)
    base_point = prices.point_on_or_before(base_date) if base_date else None
    target_point = prices.point_on_or_before(target_date) if target_date else None

    result_kwargs = {
        "asset_id": asset.asset_id,
        "weight": asset.weight,
        "currency": currency,
        "base_price_date": base_align.actual_date,
        "target_price_date": target_align.actual_date,
    }

    if base_point is None:
        return AssetReturnResult(status=ReturnStatus.MISSING_BASE_PRICE, warnings=["基準日沒有可用價格"], **result_kwargs)
    if target_point is None:
        return AssetReturnResult(status=ReturnStatus.MISSING_TARGET_PRICE, warnings=["目標日沒有可用價格"], **result_kwargs)
    if base_align.excluded or target_align.excluded:
        return AssetReturnResult(
            status=(ReturnStatus.STALE_BASE_PRICE if base_align.excluded else ReturnStatus.STALE_PRICE),
            warnings=[
                f"價格過期已排除（基準日 {base_align.stale_sessions}、目標日 "
                f"{target_align.stale_sessions} 個交易日）"
            ],
            **result_kwargs,
        )
    for alignment, label in ((base_align, "基準日"), (target_align, "目標日")):
        if alignment.is_stale:
            warnings.append(f"價格在{label}落後 {alignment.stale_sessions} 個交易日")

    base_value, base_adjusted = _point_value(base_point)
    target_value, target_adjusted = _point_value(target_point)

    if require_total_return and not (base_adjusted and target_adjusted):
        return AssetReturnResult(
            status=ReturnStatus.UNSUPPORTED_ASSET,
            warnings=["債券 ETF 需要總報酬（或調整後）價格，原始收盤價不足以代表總報酬"],
            **result_kwargs,
        )
    if not (base_adjusted and target_adjusted):
        if not allow_raw_close:
            return AssetReturnResult(
                status=ReturnStatus.UNSUPPORTED_ASSET,
                warnings=["缺少調整後收盤價，且此資產類型不接受原始收盤價"],
                **result_kwargs,
            )
        warnings.append("缺少調整後收盤價，改用原始收盤價（僅為近似）")

    if base_value == 0:
        return AssetReturnResult(
            status=ReturnStatus.MISSING_BASE_PRICE,
            warnings=["基準日價格為 0，無法計算報酬"],
            **result_kwargs,
        )

    local_return = target_value / base_value - 1.0
    fx_outcome = convert_to_cny(currency, fx, base_date, target_date, calendar=calendar)
    warnings.extend(fx_outcome.warnings)
    if not fx_outcome.ok:
        return AssetReturnResult(
            status=fx_outcome.status,
            local_return=local_return,
            base_fx_date=fx_outcome.base_fx_date,
            target_fx_date=fx_outcome.target_fx_date,
            warnings=warnings,
            **result_kwargs,
        )

    return AssetReturnResult(
        status=ReturnStatus.OK,
        local_return=local_return,
        fx_return=fx_outcome.fx_return,
        return_cny=cny_return(local_return, fx_outcome.fx_return or 0.0),
        base_fx_date=fx_outcome.base_fx_date,
        target_fx_date=fx_outcome.target_fx_date,
        warnings=warnings,
        **result_kwargs,
    )


__all__ = [
    "FxOutcome",
    "ReturnCalculator",
    "ReturnCalculatorError",
    "cny_return",
    "convert_to_cny",
    "priced_return",
    "series_from_frame",
]
