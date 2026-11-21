# -*- coding: utf-8 -*-
"""Market price and FX series models.

A price is only meaningful with three extra pieces of context: which market it
came from, which currency it is quoted in, and *when* it was actually formed,
when it became available, and when we fetched it.  V2.3.1 keeps all of them on
every point so that an estimate can be reproduced later, and so that a stale or
not-yet-published price is a visible fact rather than a silent assumption.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Iterable


class MarketDataError(ValueError):
    """Raised when a price/FX series is malformed."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AvailabilitySource(str, Enum):
    """How the ``available_at`` instant of a price was established.

    The distinction matters for point-in-time queries: only timestamps that we
    actually know about may be treated as verified.  ``INFERRED`` means "we
    computed it from an exchange-close rule" and must never be presented as the
    vendor's publication time.
    """

    OBSERVED = "observed"
    VENDOR_TIMESTAMP = "vendor_timestamp"
    INFERRED = "inferred"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: Any) -> "AvailabilitySource":
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        for member in cls:
            if text in (member.value, member.name.lower()):
                return member
        return cls.UNKNOWN

    @property
    def is_pit_usable(self) -> bool:
        """Whether a timestamp with this provenance may be trusted for PIT."""
        return self is not AvailabilitySource.UNKNOWN


def _parse_iso(text: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting a trailing ``Z``."""
    cleaned = str(text).strip()
    if cleaned.endswith(("Z", "z")):
        cleaned = cleaned[:-1] + "+00:00"
    return datetime.fromisoformat(cleaned)


def iso_utc(value: datetime | None) -> str | None:
    """Serialise an instant as ISO-8601 UTC with a ``Z`` suffix.

    Storage is UTC by contract (V2.3.1 §2): the offset of the input is only a
    convenience for the caller, never part of the stored value.
    """
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise MarketDataError(f"iso_utc 需要 datetime: {value!r}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataError(f"iso_utc 收到 naive datetime: {value!r}")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_utc(value: datetime | str | None, field_name: str) -> datetime | None:
    """Validate an instant and normalise it to UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        value = _parse_iso(value)
    if not isinstance(value, datetime):
        raise MarketDataError(f"PricePoint.{field_name} 必須是 datetime: {value!r}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataError(
            f"PricePoint.{field_name} 必須帶時區（不可用 naive datetime）: {value!r}")
    return value.astimezone(timezone.utc)


@dataclass(init=False)
class PricePoint:
    """A single closing price observation."""

    price_date: date
    close: float
    adj_close: float | None = None
    #: When the market produced this observation (exchange close, tz-aware).
    market_timestamp: datetime | None = None
    #: When the observation became obtainable by us (tz-aware).  ``None`` when
    #: the provenance is :attr:`AvailabilitySource.UNKNOWN`.
    available_at: datetime | None = None
    availability_source: AvailabilitySource = AvailabilitySource.UNKNOWN
    availability_note: str = ""
    #: When *we* fetched the row (tz-aware, defaults to now).
    fetched_at: datetime | None = None
    market: str = ""
    currency: str = ""
    #: What the source actually offered for this date (``close`` /
    #: ``close+adj_close`` / ``total_return``).  Filled at the data-acquisition
    #: layer so the provenance survives even when the calculator later decides
    #: to use a different column.
    available_price_type: str = ""
    #: What the return calculation actually used.  Empty until a calculator
    #: reads the point; the selected value is recorded on
    #: :attr:`~fa.models.return_result.AssetReturnResult.price_type_used` too.
    used_price_type: str = ""
    source: str = ""
    #: Corporate-action columns: recorded when the vendor provides them, never
    #: derived from price jumps.  Unpopulated in V2.3.1 (returns still use
    #: adjusted close) but carried through so the schema does not change again.
    dividend: float | None = None
    split: float | None = None
    #: Which stored version produced this value: ``0`` for the current
    #: ``market_prices`` row, otherwise the ``price_revisions.revision_id``
    #: the point was reconstructed from.  Metadata only — it never changes a
    #: price, but a backtest fingerprint must include it so a revised history
    #: cannot be mistaken for the original one.
    revision_id: int = 0

    def __init__(
        self,
        price_date: date,
        close: float,
        adj_close: float | None = None,
        market_timestamp: datetime | None = None,
        available_at: datetime | None = None,
        availability_source: AvailabilitySource | str = AvailabilitySource.UNKNOWN,
        availability_note: str = "",
        fetched_at: datetime | None = None,
        market: str = "",
        currency: str = "",
        available_price_type: str = "",
        used_price_type: str = "",
        source: str = "",
        dividend: float | None = None,
        split: float | None = None,
        revision_id: int = 0,
        **legacy: Any,
    ) -> None:
        """Build a point, accepting the V2.2 keyword names as aliases.

        ``price_timestamp`` and ``retrieved_at`` are still part of the public
        interface, so they are accepted here instead of silently disappearing.
        Providing a new *and* a legacy name for the same instant is a caller
        mistake (there would be no defined winner) and raises.
        """
        unexpected = set(legacy) - {"price_timestamp", "retrieved_at"}
        if unexpected:
            raise TypeError(
                "PricePoint 不支援參數: " + ", ".join(sorted(unexpected)))
        legacy_market = legacy.get("price_timestamp")
        legacy_fetched = legacy.get("retrieved_at")
        if legacy_market is not None and market_timestamp is not None:
            raise MarketDataError(
                "PricePoint 同時收到 market_timestamp 與 price_timestamp（V2.2 名稱），"
                "兩者指向同一時刻，請只提供其中一個")
        if legacy_fetched is not None and fetched_at is not None:
            raise MarketDataError(
                "PricePoint 同時收到 fetched_at 與 retrieved_at（V2.2 名稱），"
                "兩者指向同一時刻，請只提供其中一個")
        self.price_date = price_date
        self.close = close
        self.adj_close = adj_close
        self.market_timestamp = (
            legacy_market if legacy_market is not None else market_timestamp)
        self.available_at = available_at
        self.availability_source = availability_source
        self.availability_note = availability_note
        self.fetched_at = legacy_fetched if legacy_fetched is not None else fetched_at
        self.market = market
        self.currency = currency
        self.available_price_type = available_price_type
        self.used_price_type = used_price_type
        self.source = source
        self.dividend = dividend
        self.split = split
        self.revision_id = revision_id
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.close is None:
            raise MarketDataError("PricePoint.close 不可為空")
        self.close = float(self.close)
        if not math.isfinite(self.close) or self.close <= 0:
            raise MarketDataError(f"PricePoint.close 必須為有限正數: {self.close!r}")
        if self.adj_close is not None:
            self.adj_close = float(self.adj_close)
            if not math.isfinite(self.adj_close) or self.adj_close <= 0:
                raise MarketDataError(
                    f"PricePoint.adj_close 必須為有限正數: {self.adj_close!r}")
        self.market_timestamp = _to_utc(self.market_timestamp, "market_timestamp")
        self.available_at = _to_utc(self.available_at, "available_at")
        self.fetched_at = _to_utc(self.fetched_at, "fetched_at")
        if self.fetched_at is None:
            self.fetched_at = _utcnow()
        self.availability_source = AvailabilitySource.parse(self.availability_source)
        self.availability_note = str(self.availability_note or "")
        if self.available_at is not None and not self.availability_source.is_pit_usable:
            raise MarketDataError(
                "PricePoint.available_at 需要明確的 availability_source"
                "（observed / vendor_timestamp / inferred），不可標為 unknown")
        if self.available_at is None and self.availability_source is not AvailabilitySource.UNKNOWN:
            raise MarketDataError(
                "PricePoint.available_at 為空時，availability_source 必須為 unknown"
                "（沒有可用時間就沒有可宣稱的來源）")
        if self.dividend is not None:
            self.dividend = float(self.dividend)
            if not math.isfinite(self.dividend) or self.dividend < 0:
                raise MarketDataError(f"PricePoint.dividend 必須為有限非負數: {self.dividend!r}")
        if self.split is not None:
            self.split = float(self.split)
            if not math.isfinite(self.split) or self.split <= 0:
                raise MarketDataError(f"PricePoint.split 必須為有限正數: {self.split!r}")
        self.available_price_type = self.available_price_type or (
            "close+adj_close" if self.adj_close is not None else "close"
        )
        self.revision_id = int(self.revision_id or 0)
        if self.revision_id < 0:
            raise MarketDataError(
                f"PricePoint.revision_id 不可為負數: {self.revision_id!r}")

    # -- V2.2 aliases ---------------------------------------------------------
    @property
    def price_timestamp(self) -> datetime | None:
        """V2.2 name for :attr:`market_timestamp`."""
        return self.market_timestamp

    @price_timestamp.setter
    def price_timestamp(self, value: datetime | None) -> None:
        self.market_timestamp = _to_utc(value, "market_timestamp")

    @property
    def retrieved_at(self) -> datetime | None:
        """V2.2 name for :attr:`fetched_at`."""
        return self.fetched_at

    @retrieved_at.setter
    def retrieved_at(self, value: datetime | None) -> None:
        self.fetched_at = _to_utc(value, "fetched_at")

    @property
    def is_pit_usable(self) -> bool:
        """Whether this observation may be used for a point-in-time query."""
        return self.available_at is not None and self.availability_source.is_pit_usable

    @property
    def value(self) -> float:
        """The price used for returns: adjusted close when available."""
        return self.adj_close if self.adj_close is not None else self.close

    @property
    def price_type(self) -> str:
        """Backwards-compatible alias: the used type, else what was available."""
        return self.used_price_type or self.available_price_type

    def with_used_price_type(self, used: str) -> "PricePoint":
        """Return a copy recording which column the calculator actually used."""
        from dataclasses import replace

        return replace(self, used_price_type=str(used or ""))

    def to_dict(self) -> dict[str, Any]:
        market_ts = iso_utc(self.market_timestamp)
        fetched = iso_utc(self.fetched_at)
        return {
            "price_date": self.price_date.isoformat(),
            "close": self.close,
            "adj_close": self.adj_close,
            "market_timestamp": market_ts,
            "available_at": iso_utc(self.available_at),
            "availability_source": self.availability_source.value,
            "availability_note": self.availability_note,
            "fetched_at": fetched,
            # V2.2 keys stay readable for old snapshots and old readers.
            "price_timestamp": market_ts,
            "retrieved_at": fetched,
            "market": self.market,
            "currency": self.currency,
            "available_price_type": self.available_price_type,
            "used_price_type": self.used_price_type,
            "source": self.source,
            "dividend": self.dividend,
            "split": self.split,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PricePoint":
        def _dt(value: Any) -> datetime | None:
            if value in (None, ""):
                return None
            if isinstance(value, datetime):
                return value
            return _parse_iso(str(value))

        price_date = data.get("price_date")
        if isinstance(price_date, str):
            price_date = date.fromisoformat(price_date[:10])
        return cls(
            price_date=price_date,
            close=data.get("close"),
            adj_close=data.get("adj_close"),
            market_timestamp=_dt(data.get("market_timestamp", data.get("price_timestamp"))),
            available_at=_dt(data.get("available_at")),
            availability_source=data.get("availability_source", AvailabilitySource.UNKNOWN),
            availability_note=data.get("availability_note", ""),
            fetched_at=_dt(data.get("fetched_at", data.get("retrieved_at"))),
            market=data.get("market", ""),
            currency=data.get("currency", ""),
            available_price_type=data.get(
                "available_price_type", data.get("price_type", "")
            ),
            used_price_type=data.get("used_price_type", ""),
            source=data.get("source", ""),
            dividend=data.get("dividend"),
            split=data.get("split"),
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
    # 故意沒有預設值：外部來源必須明確聲明基準貨幣，否則無法確認是 CNY per unit。
    # 內部可信來源用 ``from_pairs()``（預設 CNY）或明確傳入 ``base_currency="CNY"``。
    base_currency: str = ""

    def __post_init__(self) -> None:
        self.currency = str(self.currency or "").strip().upper()
        self.base_currency = str(self.base_currency or "").strip().upper()
        self.points = sorted(self.points, key=lambda point: point.price_date)

    @property
    def quote_currency(self) -> str:
        """The quote currency of the pair (``base_currency`` per unit of this)."""
        return self.base_currency

    @property
    def quote_convention(self) -> str:
        base = self.base_currency or "?"
        return f"{base} per 1 {self.currency}"

    @property
    def bars(self) -> list[PricePoint]:
        """每一筆觀測，連同它自己的時間與來源資訊。

        匯率的「bar」與股票的 bar 是同一件事：``close`` 是匯率、``price_date``
        是這個匯率所屬的交易日，而 ``PricePoint`` 另外帶著三個時間 ——
        ``market_timestamp``（這個數字描述哪一瞬間）、``available_at`` 與
        ``availability_source``（它在什麼時候、憑什麼可以公開）、
        ``fetched_at``（這份副本是什麼時候抓的）。

        這裡刻意只是 ``points`` 的別名：FX 管線與儲存層要談「bar」時不必換名字，
        但資料本身仍只有一份，不會出現「bars 與 points 各說各話」。
        """
        return self.points

    @property
    def pit_bars(self) -> list[PricePoint]:
        """只保留具備可用時間的 bar（可在 point-in-time 查詢中使用）。"""
        return [point for point in self.points if point.is_pit_usable]

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
        base_currency: str = "CNY",
        availability_source: AvailabilitySource = AvailabilitySource.UNKNOWN,
        availability_note: str = "",
    ) -> "FxSeries":
        points = [
            PricePoint(
                price_date=day,
                close=rate,
                adj_close=None,
                market=market,
                currency="CNY",
                available_price_type="fx_close",
                used_price_type="fx_close",
                source=source,
                availability_source=availability_source,
                availability_note=availability_note,
                # An FX row is publishable only once we know *when*; the
                # inferred rule is filled by the pipeline that knows the lag.
                available_at=None,
            )
            for day, rate in pairs
        ]
        return cls(currency=currency, source=source, points=points,
                   base_currency=base_currency)

    @classmethod
    def from_bars(
        cls,
        currency: str,
        bars: Iterable[PricePoint],
        *,
        source: str = "",
        base_currency: str = "CNY",
    ) -> "FxSeries":
        """Build a series from observations that already carry their own time.

        Unlike :meth:`from_pairs` (a convenience for tests and legacy callers,
        which cannot know when a rate became public), this constructor keeps
        each bar exactly as handed over — no ``available_at`` is invented — and
        refuses anything that would make the direction ambiguous: an empty or
        non-CNY ``base_currency``, or a non-positive rate.
        """
        base = str(base_currency or "").strip().upper()
        if not base:
            raise MarketDataError(
                f"FX 序列 {currency} 未聲明 base_currency；"
                "匯率方向必須由來源明確聲明（本專案只接受 CNY 基準），不可預設")
        if base != "CNY":
            raise MarketDataError(
                f"FX 序列 {currency} 的 base_currency 為 {base}；"
                "本專案只接受 CNY 基準（FX = CNY per 1 單位外幣），"
                "其他方向會被讀成反向報酬")
        points = list(bars)
        for point in points:
            if point.value is None or point.value <= 0:
                raise MarketDataError(
                    f"FX 序列 {currency} 的 {point.price_date} 匯率不是正數："
                    f"{point.value!r}")
        return cls(currency=currency, source=source, points=points,
                   base_currency=base)
