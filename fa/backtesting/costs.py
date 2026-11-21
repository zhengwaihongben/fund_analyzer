# -*- coding: utf-8 -*-
"""Trade costs for the V2.3.2 strategy backtester (spec §6).

``costs.py`` is pure arithmetic: it receives a notional, a
:class:`~fa.backtesting.models.CostModel` and whether a currency conversion
happens, and returns the three cost components.  It knows nothing about
strategies, portfolios, calendars or the database — that is what keeps the
engine a pure simulation.

Every amount is in the base currency (CNY).  The four components are:

``commission``
    ``gross * commission_rate``, floored by ``min_commission`` **per order**
    (not per day, not per pair).
``slippage``
    ``gross * slippage_bps / 10 000``.  The sign is the engine's business: a
    buy pays more than the market price, a sell receives less, so the engine
    always *subtracts* this cost from cash.  Recording it as a cost instead of
    bending ``Trade.price_local`` keeps the market price on the trade row and
    prevents the same bps from being charged twice.
``fx_spread``
    ``conversion_notional * fx_spread_bps / 10 000`` on each conversion leg,
    zero for CNY instruments (holding cash is not a conversion).

Rounding (spec §6): cash and cost amounts to 2 decimals, quantities to 6
decimals, and the recorded price is never rounded by the cost model.  The
functions below return **unrounded** values; the engine rounds once per order
with :func:`round_money` / :func:`round_quantity`.  ``Decimal`` with
``ROUND_HALF_EVEN`` is used so ties are documented rather than left to binary
floating point (``round(2.675, 2) == 2.67`` would surprise an accountant).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from math import isfinite

from fa.backtesting.models import CostModel

#: One basis point is 1/10 000.
BPS_DENOMINATOR = 10_000.0

_CENT = Decimal("0.01")
_MICRO = Decimal("0.000001")


def round_money(value: float) -> float:
    """Round a base-currency amount to 2 decimals, ties to even."""
    return float(Decimal(str(float(value))).quantize(_CENT, rounding=ROUND_HALF_EVEN))


def round_quantity(value: float) -> float:
    """Round a share quantity to 6 decimals, ties to even."""
    return float(Decimal(str(float(value))).quantize(_MICRO, rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True)
class TradeCosts:
    """The three cost components of one order, unrounded, in CNY."""

    commission_base: float
    slippage_base: float
    fx_spread_base: float

    @property
    def total_base(self) -> float:
        return self.commission_base + self.slippage_base + self.fx_spread_base

    def rounded(self) -> "TradeCosts":
        return TradeCosts(
            commission_base=round_money(self.commission_base),
            slippage_base=round_money(self.slippage_base),
            fx_spread_base=round_money(self.fx_spread_base),
        )

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.commission_base, self.slippage_base, self.fx_spread_base)


def _require_notional(value: float, label: str) -> float:
    amount = float(value)
    if not isfinite(amount):
        raise ValueError(f"{label} 必須是有限數值: {value!r}")
    if amount < 0.0:
        raise ValueError(f"{label} 不可為負數: {value!r}")
    return amount


def calculate_commission(gross_notional_base: float, cost_model: CostModel) -> float:
    """Per-order commission: ``max(gross * rate, min_commission)``."""
    gross = _require_notional(gross_notional_base, "gross_notional_base")
    if gross == 0.0:
        return 0.0
    commission = gross * cost_model.commission_rate
    if cost_model.min_commission > 0.0:
        commission = max(commission, cost_model.min_commission)
    return commission


def calculate_slippage(gross_notional_base: float, cost_model: CostModel) -> float:
    """Adverse-direction cost, always a positive amount."""
    gross = _require_notional(gross_notional_base, "gross_notional_base")
    return gross * cost_model.slippage_bps / BPS_DENOMINATOR


def calculate_fx_spread(
    fx_notional_base: float,
    is_foreign_currency: bool,
    cost_model: CostModel,
) -> float:
    """Conversion cost; zero for CNY because nothing is converted."""
    notional = _require_notional(fx_notional_base, "fx_notional_base")
    if not is_foreign_currency:
        return 0.0
    return notional * cost_model.fx_spread_bps / BPS_DENOMINATOR


def calculate_trade_costs(
    gross_notional_base: float,
    is_foreign_currency: bool,
    cost_model: CostModel,
) -> TradeCosts:
    """All three components for one order, unrounded."""
    gross = _require_notional(gross_notional_base, "gross_notional_base")
    return TradeCosts(
        commission_base=calculate_commission(gross, cost_model),
        slippage_base=calculate_slippage(gross, cost_model),
        fx_spread_base=calculate_fx_spread(gross, is_foreign_currency, cost_model),
    )


__all__ = [
    "BPS_DENOMINATOR",
    "TradeCosts",
    "calculate_commission",
    "calculate_fx_spread",
    "calculate_slippage",
    "calculate_trade_costs",
    "round_money",
    "round_quantity",
]
