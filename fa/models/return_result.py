# -*- coding: utf-8 -*-
"""Return result models shared by every V2 return calculator.

Each calculator (equity, ETF, bond ETF, cash, FX) returns the same
:class:`AssetReturnResult` shape, so portfolio valuation never needs to know
how a particular asset's return was produced.  The only place that differs is
:func:`shapley_contributions`, which converts a local/FX pair into attribution
contributions that add up exactly to the CNY return.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class ReturnStatus(str, Enum):
    """Why an asset did or did not contribute to the estimate."""

    OK = "ok"
    MISSING_PRICE = "missing-price"
    MISSING_TARGET_PRICE = "missing-target-price"
    MISSING_BASE_PRICE = "missing-base-price"
    STALE_PRICE = "stale-price"
    STALE_BASE_PRICE = "stale-base-price"
    MISSING_CURRENCY = "missing-currency"
    MISSING_FX = "missing-fx"
    STALE_FX = "stale-fx"
    STALE_BASE_FX = "stale-base-fx"
    UNSUPPORTED_ASSET = "unsupported-asset"

    @property
    def is_ok(self) -> bool:
        return self is ReturnStatus.OK


class PortfolioStatus(str, Enum):
    """Overall status of a portfolio estimate."""

    ESTIMATED = "estimated"
    PARTIAL = "partial"
    INSUFFICIENT_DATA = "insufficient-data"

    @classmethod
    def parse(cls, value: Any) -> "PortfolioStatus":
        if isinstance(value, cls):
            return value
        return cls(str(value or "").strip().lower() or cls.INSUFFICIENT_DATA.value)


def shapley_contributions(
    weight: float, local_return: float, fx_return: float
) -> tuple[float, float]:
    """Split ``weight * ((1+local)(1+fx) - 1)`` into local and FX contributions.

    Symmetric (Shapley) split, as specified for V2::

        C_local = w * r_local * (1 + r_fx / 2)
        C_fx    = w * r_fx    * (1 + r_local / 2)

    which satisfies ``C_local + C_fx == w * r_cny`` exactly.
    """
    c_local = weight * local_return * (1.0 + fx_return / 2.0)
    c_fx = weight * fx_return * (1.0 + local_return / 2.0)
    return c_local, c_fx


@dataclass
class AssetReturnResult:
    """Return of one asset between a base date and a target date."""

    asset_id: str
    status: ReturnStatus = ReturnStatus.OK
    local_return: float | None = None
    fx_return: float | None = None
    return_cny: float | None = None
    weight: float = 0.0
    base_price_date: date | None = None
    target_price_date: date | None = None
    base_fx_date: date | None = None
    target_fx_date: date | None = None
    currency: str = ""
    local_contribution: float | None = None
    fx_contribution: float | None = None
    warnings: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.asset_id = str(self.asset_id or "").strip()
        if not self.asset_id:
            raise ValueError("AssetReturnResult.asset_id 不可為空")
        if not isinstance(self.status, ReturnStatus):
            try:
                self.status = ReturnStatus(str(self.status))
            except ValueError:
                self.status = ReturnStatus.OK if not self.status else ReturnStatus.UNSUPPORTED_ASSET
        self.weight = float(self.weight or 0.0)
        if (
            self.return_cny is None
            and self.local_return is not None
            and self.fx_return is not None
        ):
            self.return_cny = (1.0 + self.local_return) * (1.0 + self.fx_return) - 1.0
        if self.local_contribution is None and self.fx_contribution is None:
            if (
                self.status.is_ok
                and self.local_return is not None
                and self.fx_return is not None
            ):
                self.local_contribution, self.fx_contribution = shapley_contributions(
                    self.weight, self.local_return, self.fx_return
                )

    @property
    def is_ok(self) -> bool:
        return self.status.is_ok

    @property
    def contribution(self) -> float:
        """Contribution to the fund return (0 when the asset is excluded)."""
        if not self.is_ok:
            return 0.0
        if self.local_contribution is None or self.fx_contribution is None:
            return 0.0
        return self.local_contribution + self.fx_contribution

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "status": self.status.value,
            "local_return": self.local_return,
            "fx_return": self.fx_return,
            "return_cny": self.return_cny,
            "weight": self.weight,
            "local_contribution": self.local_contribution,
            "fx_contribution": self.fx_contribution,
            "contribution": self.contribution,
            "base_price_date": self.base_price_date.isoformat() if self.base_price_date else None,
            "target_price_date": (
                self.target_price_date.isoformat() if self.target_price_date else None
            ),
            "base_fx_date": self.base_fx_date.isoformat() if self.base_fx_date else None,
            "target_fx_date": self.target_fx_date.isoformat() if self.target_fx_date else None,
            "currency": self.currency,
            "warnings": list(self.warnings),
            "extra": dict(self.extra),
        }


@dataclass
class PortfolioReturnResult:
    """Weighted CNY return of a holdings snapshot plus its coverage metadata."""

    status: PortfolioStatus = PortfolioStatus.INSUFFICIENT_DATA
    estimated_return: float | None = None
    covered_weight: float = 0.0
    uncovered_weight: float = 1.0
    coverage_ratio: float = 0.0
    local_contribution: float = 0.0
    fx_contribution: float = 0.0
    holdings_included: int = 0
    holdings_excluded: int = 0
    assets: list[AssetReturnResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    disclosed_weight: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.status, PortfolioStatus):
            self.status = PortfolioStatus.parse(self.status)
        self.covered_weight = float(self.covered_weight or 0.0)
        self.uncovered_weight = float(self.uncovered_weight or 0.0)
        self.disclosed_weight = float(self.disclosed_weight or 0.0)

    @property
    def total_contribution(self) -> float:
        """Local + FX contributions; equals :attr:`estimated_return` when set."""
        return self.local_contribution + self.fx_contribution

    @property
    def coverage_of_known(self) -> float:
        """Covered share *of the disclosed holdings*, not of the whole fund.

        ``coverage_ratio`` answers "how much of the fund did we value";
        this answers "how much of what we know about did we value".  The two
        must never be conflated (a fund disclosing only the top 10 can have
        ``coverage_ratio`` 46 % and ``coverage_of_known`` 100 %).
        """
        if self.disclosed_weight <= 0:
            return 0.0
        return min(1.0, self.covered_weight / self.disclosed_weight)

    def excluded(self) -> list[AssetReturnResult]:
        return [asset for asset in self.assets if not asset.is_ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "estimated_return": self.estimated_return,
            "covered_weight": self.covered_weight,
            "uncovered_weight": self.uncovered_weight,
            "coverage_ratio": self.coverage_ratio,
            "coverage_of_known": self.coverage_of_known,
            "disclosed_weight": self.disclosed_weight,
            "local_contribution": self.local_contribution,
            "fx_contribution": self.fx_contribution,
            "total_contribution": self.total_contribution,
            "holdings_included": self.holdings_included,
            "holdings_excluded": self.holdings_excluded,
            "warnings": list(self.warnings),
            "assets": [asset.to_dict() for asset in self.assets],
        }
