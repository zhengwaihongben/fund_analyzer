# -*- coding: utf-8 -*-
"""Holdings snapshot model (one disclosed quarter of one fund)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from fa.models.asset import Asset, AssetType, _coerce_date


class HoldingValidationError(ValueError):
    """Raised when a holdings snapshot is internally inconsistent."""


@dataclass
class HoldingSnapshot:
    """The portfolio of one fund as disclosed for one quarter.

    ``holding_date`` is the quarter end the weights describe; ``disclosure_date``
    is when those weights became public.  The two are deliberately separate:
    an estimate computed on an earlier date must not see this snapshot, which
    is exactly the look-ahead bias this model exists to prevent.
    """

    fund_code: str
    assets: list[Asset] = field(default_factory=list)
    quarter: str | None = None
    holding_date: date | None = None
    disclosure_date: date | None = None
    disclosure_date_estimated: bool = False
    source: str = ""
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.fund_code = str(self.fund_code or "").strip()
        if not self.fund_code:
            raise HoldingValidationError("fund_code 不可為空")
        self.quarter = (str(self.quarter).strip() or None) if self.quarter else None
        self.holding_date = _coerce_date(self.holding_date, "holding_date")
        self.disclosure_date = _coerce_date(self.disclosure_date, "disclosure_date")
        self.source = str(self.source or "").strip()
        self.assets = list(self.assets or [])
        for asset in self.assets:
            if not isinstance(asset, Asset):  # pragma: no cover - defensive
                raise HoldingValidationError(f"assets 內含非 Asset 物件: {type(asset)!r}")
        if self.disclosure_date and self.holding_date and self.disclosure_date < self.holding_date:
            raise HoldingValidationError(
                f"{self.fund_code}: 披露日 {self.disclosure_date} 早於持倉日 {self.holding_date}"
            )

    # -- aggregate questions ---------------------------------------------------
    @property
    def total_weight(self) -> float:
        return sum(asset.weight for asset in self.assets)

    @property
    def uncovered_weight(self) -> float:
        return max(0.0, 1.0 - self.total_weight)

    @property
    def asset_ids(self) -> list[str]:
        return [asset.asset_id for asset in self.assets]

    def unique_asset_ids(self) -> list[str]:
        seen: dict[str, int] = {}
        for asset_id in self.asset_ids:
            seen[asset_id] = seen.get(asset_id, 0) + 1
        return [asset_id for asset_id, count in seen.items() if count > 1]

    def is_disclosed_by(self, day: date | None) -> bool:
        """True when this snapshot was public information on ``day``."""
        if day is None or not self.disclosure_date:
            return False
        return self.disclosure_date <= day

    def assets_of(self, *types: AssetType) -> list[Asset]:
        wanted = set(types)
        return [asset for asset in self.assets if asset.asset_type in wanted]

    def by_type(self) -> dict[str, float]:
        """Weight per asset type, useful for diagnostics and reports."""
        totals: dict[str, float] = {}
        for asset in self.assets:
            key = asset.asset_type.value
            totals[key] = totals.get(key, 0.0) + asset.weight
        return totals

    def validate(self) -> None:
        """Raise when the snapshot cannot be used for valuation."""
        duplicates = self.unique_asset_ids()
        if duplicates:
            raise HoldingValidationError(f"{self.fund_code}: 重複 asset_id {duplicates}")
        if self.holding_date is None:
            raise HoldingValidationError(f"{self.fund_code}: 缺少 holding_date")
        if self.disclosure_date is None:
            raise HoldingValidationError(f"{self.fund_code}: 缺少 disclosure_date")
        if self.total_weight > 1.0 + 1e-6:
            raise HoldingValidationError(
                f"{self.fund_code}: 權重合計 {self.total_weight:.4f} 超過 1.0"
            )

    # -- serialisation ---------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_code": self.fund_code,
            "quarter": self.quarter,
            "holding_date": self.holding_date.isoformat() if self.holding_date else None,
            "disclosure_date": self.disclosure_date.isoformat() if self.disclosure_date else None,
            "disclosure_date_estimated": self.disclosure_date_estimated,
            "source": self.source,
            "warnings": list(self.warnings),
            "assets": [asset.to_dict() for asset in self.assets],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HoldingSnapshot":
        return cls(
            fund_code=data.get("fund_code"),
            assets=[Asset.from_dict(item) for item in data.get("assets") or []],
            quarter=data.get("quarter"),
            holding_date=data.get("holding_date"),
            disclosure_date=data.get("disclosure_date"),
            disclosure_date_estimated=bool(data.get("disclosure_date_estimated")),
            source=data.get("source", ""),
            warnings=list(data.get("warnings") or []),
        )


def snapshot_from_rows(
    fund_code: str,
    rows: Iterable[dict[str, Any]],
    *,
    quarter: str | None = None,
    holding_date: date | None = None,
    disclosure_date: date | None = None,
    disclosure_date_estimated: bool = False,
    source: str = "",
) -> HoldingSnapshot:
    """Build a snapshot from loose dict rows (as produced by the data layer)."""
    assets: list[Asset] = []
    for row in rows:
        assets.append(
            Asset(
                asset_id=row.get("asset_id") or row.get("ticker") or row.get("name") or "",
                name=row.get("name") or "",
                asset_type=row.get("asset_type"),
                ticker=row.get("ticker"),
                market=row.get("market"),
                currency=row.get("currency"),
                weight=row.get("weight", 0.0),
                holding_date=row.get("holding_date") or holding_date,
                source=row.get("source") or source,
                price_type=row.get("price_type"),
                exchange=row.get("exchange"),
                benchmark=row.get("benchmark"),
                interest_rate_source=row.get("interest_rate_source"),
                interest_rate=row.get("interest_rate"),
                extra=row.get("extra") or {},
            )
        )
    return HoldingSnapshot(
        fund_code=fund_code,
        assets=assets,
        quarter=quarter,
        holding_date=holding_date,
        disclosure_date=disclosure_date,
        disclosure_date_estimated=disclosure_date_estimated,
        source=source,
    )
