# -*- coding: utf-8 -*-
"""Unified asset model for the V2 valuation engine.

Every holding, whatever its instrument type, is normalised into one
:class:`Asset` so that the valuation pipeline never has to ask "what kind of
thing is this?".  Type-specific attributes (``price_type`` for equities,
``exchange`` for ETFs, ``interest_rate`` for cash, ...) live in dedicated
optional fields, and anything unrecognised is kept as
:class:`AssetType.UNKNOWN` instead of being silently treated as an equity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class AssetType(str, Enum):
    """Asset classes understood by V2.

    ``UNKNOWN`` is a real, first-class value: an unrecognised holding is
    reported and excluded from the estimate, never guessed into EQUITY.
    """

    EQUITY = "equity"
    ETF = "etf"
    BOND_ETF = "bond_etf"
    CASH = "cash"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: Any) -> "AssetType":
        """Coerce ``value`` (enum, string or None) into an :class:`AssetType`."""
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.UNKNOWN
        text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "": cls.UNKNOWN,
            "unknown": cls.UNKNOWN,
            "stock": cls.EQUITY,
            "share": cls.EQUITY,
            "equity": cls.EQUITY,
            "etf": cls.ETF,
            "fund": cls.ETF,
            "bond_etf": cls.BOND_ETF,
            "bondetf": cls.BOND_ETF,
            "bond": cls.BOND_ETF,
            "cash": cls.CASH,
            "money": cls.CASH,
            "deposit": cls.CASH,
        }
        return aliases.get(text, cls.UNKNOWN)


#: Asset types V2 can value with real market data.
PRICED_ASSET_TYPES = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.BOND_ETF})

#: Asset types the estimator is allowed to include in a portfolio return.
SUPPORTED_ASSET_TYPES = frozenset(
    {AssetType.EQUITY, AssetType.ETF, AssetType.BOND_ETF, AssetType.CASH}
)

#: Currencies that need no FX conversion.
BASE_CURRENCY = "CNY"
_CURRENCY_ALIASES = {
    "": "",
    "NAN": "",
    "NONE": "",
    "NULL": "",
    "UNKNOWN": "",
    "?": "",
    "-": "",
    "RMB": BASE_CURRENCY,
    "CNH": BASE_CURRENCY,
    "YUAN": BASE_CURRENCY,
}


def normalise_currency(value: Any) -> str:
    """Return an upper-case ISO currency code, or ``""`` when unknown."""
    if value is None:
        return ""
    text = str(value).strip().upper()
    if text != text.strip():  # pragma: no cover - defensive
        text = text.strip()
    return _CURRENCY_ALIASES.get(text, text)


def _coerce_date(value: Any, what: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError as exc:  # pragma: no cover - defensive
        raise AssetValidationError(f"{what} 不是有效日期: {value!r}") from exc


class AssetValidationError(ValueError):
    """Raised when an :class:`Asset` cannot be constructed from its inputs."""


@dataclass
class Asset:
    """One fund holding, normalised.

    ``weight`` is a fraction of the fund's NAV (``0.1043`` is 10.43 %).  All
    dates are plain :class:`datetime.date` values in the asset's own market;
    converting them to a valuation date is the calendar module's job.
    """

    asset_id: str
    name: str
    asset_type: AssetType = AssetType.UNKNOWN
    ticker: str | None = None
    market: str | None = None
    currency: str = ""
    weight: float = 0.0
    holding_date: date | None = None
    source: str = ""
    # Type-specific attributes -------------------------------------------------
    price_type: str | None = None
    exchange: str | None = None
    benchmark: str | None = None
    interest_rate_source: str | None = None
    interest_rate: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.asset_type = AssetType.parse(self.asset_type)
        self.asset_id = str(self.asset_id or "").strip()
        self.name = str(self.name or "").strip()
        self.ticker = (str(self.ticker).strip() or None) if self.ticker else None
        self.market = (str(self.market).strip() or None) if self.market else None
        self.currency = normalise_currency(self.currency)
        self.holding_date = _coerce_date(self.holding_date, "holding_date")
        self.source = str(self.source or "").strip()
        if not self.asset_id:
            raise AssetValidationError("asset_id 不可為空")
        if not self.name:
            raise AssetValidationError(f"{self.asset_id}: name 不可為空")
        try:
            self.weight = float(self.weight)
        except (TypeError, ValueError) as exc:
            raise AssetValidationError(f"{self.asset_id}: weight 不是數字: {self.weight!r}") from exc
        if not math.isfinite(self.weight):
            raise AssetValidationError(f"{self.asset_id}: weight 不是有限數: {self.weight!r}")
        if self.weight < 0:
            raise AssetValidationError(f"{self.asset_id}: weight 不可為負: {self.weight}")
        if self.weight > 1.0 + 1e-9:
            raise AssetValidationError(
                f"{self.asset_id}: weight 應為基金淨值比例（0–1），收到 {self.weight}"
            )
        if self.interest_rate is not None:
            self.interest_rate = float(self.interest_rate)

    # -- capability questions --------------------------------------------------
    @property
    def is_supported(self) -> bool:
        """True when the estimator knows how to value this asset class."""
        return self.asset_type in SUPPORTED_ASSET_TYPES

    @property
    def needs_price(self) -> bool:
        """True when the asset return comes from market prices."""
        return self.asset_type in PRICED_ASSET_TYPES

    @property
    def needs_interest_rate(self) -> bool:
        """True when the asset return comes from an interest-rate model."""
        return self.asset_type is AssetType.CASH

    @property
    def uses_fx(self) -> bool:
        """True when the asset is quoted outside CNY and needs conversion."""
        return bool(self.currency) and self.currency != BASE_CURRENCY

    # -- serialisation ---------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "ticker": self.ticker,
            "name": self.name,
            "asset_type": self.asset_type.value,
            "market": self.market,
            "currency": self.currency,
            "weight": self.weight,
            "holding_date": self.holding_date.isoformat() if self.holding_date else None,
            "source": self.source,
            "price_type": self.price_type,
            "exchange": self.exchange,
            "benchmark": self.benchmark,
            "interest_rate_source": self.interest_rate_source,
            "interest_rate": self.interest_rate,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Asset":
        payload = {
            "asset_id": data.get("asset_id") or data.get("ticker") or data.get("name"),
            "name": data.get("name"),
            "asset_type": data.get("asset_type"),
            "ticker": data.get("ticker"),
            "market": data.get("market"),
            "currency": data.get("currency"),
            "weight": data.get("weight", 0.0),
            "holding_date": data.get("holding_date"),
            "source": data.get("source", ""),
            "price_type": data.get("price_type"),
            "exchange": data.get("exchange"),
            "benchmark": data.get("benchmark"),
            "interest_rate_source": data.get("interest_rate_source"),
            "interest_rate": data.get("interest_rate"),
            "extra": data.get("extra") or {},
        }
        return cls(**payload)
