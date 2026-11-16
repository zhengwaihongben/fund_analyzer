# -*- coding: utf-8 -*-
"""Market price and FX series models.

A price is only meaningful with three extra pieces of context: which market it
came from, which currency it is quoted in, and *when* it was actually formed and
retrieved.  V2 keeps all of them on every point so that an estimate can be
reproduced later, and so that a stale price is a visible fact rather than a
silent assumption.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable


class MarketDataError(ValueError):
    """Raised when a price/FX series is malformed."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PricePoint:
    """A single closing price observation."""

    price_date: date
    close: float
    adj_close: float | None = None
    price_timestamp: datetime | None = None
    retrieved_at: datetime | None = None
    market: str = ""
    currency: str = ""
    price_type: str = "adjusted_close"
    source: str = ""

    def __post_init__(self) -> None:
        if self.close is None:
            raise MarketDataError("PricePoint.close 不可為空")
        self.close = float(self.close)
        if self.adj_close is not None:
            self.adj_close = float(self.adj_close)
        if self.retrieved_at is None:
            self.retrieved_at = _utcnow()

    @property
    def value(self) -> float:
        """The price used for returns: adjusted close when available."""
        return self.adj_close if self.adj_close is not None else self.close

    def to_dict(self) -> dict[str, Any]:
        return {
            "price_date": self.price_date.isoformat(),
            "close": self.close,
            "adj_close": self.adj_close,
            "price_timestamp": self.price_timestamp.isoformat() if self.price_timestamp else None,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "market": self.market,
            "currency": self.currency,
            "price_type": self.price_type,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PricePoint":
        def _dt(value: Any) -> datetime | None:
            if value in (None, ""):
                return None
            if isinstance(value, datetime):
                return value
            return datetime.fromisoformat(str(value))

        price_date = data.get("price_date")
        if isinstance(price_date, str):
            price_date = date.fromisoformat(price_date[:10])
        return cls(
            price_date=price_date,
            close=data.get("close"),
            adj_close=data.get("adj_close"),
            price_timestamp=_dt(data.get("price_timestamp")),
            retrieved_at=_dt(data.get("retrieved_at")),
            market=data.get("market", ""),
            currency=data.get("currency", ""),
            price_type=data.get("price_type", "adjusted_close"),
            source=data.get("source", ""),
        )


@dataclass
class PriceSeries:
    """Ascending series of :class:`PricePoint` for one instrument."""

    ticker: str
    market: str = ""
    currency: str = ""
    source: str = ""
    points: list[PricePoint] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ticker = str(self.ticker or "").strip()
        if not self.ticker:
            raise MarketDataError("PriceSeries.ticker 不可為空")
        self.points = sorted(self.points, key=lambda point: point.price_date)

    def __len__(self) -> int:
        return len(self.points)

    @property
    def first_date(self) -> date | None:
        return self.points[0].price_date if self.points else None

    @property
    def last_date(self) -> date | None:
        return self.points[-1].price_date if self.points else None

    def point_on_or_before(self, day: date) -> PricePoint | None:
        """Last observation whose price date is ``<= day`` (no look-ahead)."""
        found: PricePoint | None = None
        for point in self.points:
            if point.price_date <= day:
                found = point
            else:
                break
        return found

    def price_on_or_before(self, day: date) -> tuple[float | None, date | None]:
        point = self.point_on_or_before(day)
        if point is None:
            return None, None
        return point.value, point.price_date

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "market": self.market,
            "currency": self.currency,
            "source": self.source,
            "points": [point.to_dict() for point in self.points],
        }

    @classmethod
    def from_points(
        cls,
        ticker: str,
        points: Iterable[PricePoint],
        *,
        market: str = "",
        currency: str = "",
        source: str = "",
    ) -> "PriceSeries":
        return cls(
            ticker=ticker,
            market=market,
            currency=currency,
            source=source,
            points=list(points),
        )


@dataclass
class FxSeries:
    """CNY-per-unit-of-foreign-currency series (the V2 FX convention)."""

    currency: str
    source: str = ""
    points: list[PricePoint] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.currency = str(self.currency or "").strip().upper()
        self.points = sorted(self.points, key=lambda point: point.price_date)

    def __len__(self) -> int:
        return len(self.points)

    def rate_on_or_before(self, day: date) -> tuple[float | None, date | None]:
        found: PricePoint | None = None
        for point in self.points:
            if point.price_date <= day:
                found = point
            else:
                break
        if found is None:
            return None, None
        return found.value, found.price_date

    @classmethod
    def from_pairs(
        cls,
        currency: str,
        pairs: Iterable[tuple[date, float]],
        *,
        source: str = "",
        market: str = "FX",
    ) -> "FxSeries":
        points = [
            PricePoint(
                price_date=day,
                close=rate,
                adj_close=None,
                market=market,
                currency="CNY",
                price_type="fx_close",
                source=source,
            )
            for day, rate in pairs
        ]
        return cls(currency=currency, source=source, points=points)
