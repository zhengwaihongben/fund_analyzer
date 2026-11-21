# -*- coding: utf-8 -*-
"""Fixtures for the V2.3.2 backtesting tests.

The fixtures build a temporary SQLite repository with synthetic data only: two
instruments (a US/USD line and a Hong Kong/HKD line), the CNY-per-unit FX
series they need, and an official NAV history.  No network, and the production
database is never opened.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from fa.backtesting.models import CostModel, Instrument, RebalanceRule, StrategySpec, Weighting
from fa.data.database.repository import Repository
from fa.data.price_pipeline import infer_availability
from fa.models.market_data import AvailabilitySource, FxSeries, PricePoint, PriceSeries

UTC = timezone.utc

#: First synthetic session (a Monday).
START = date(2025, 1, 6)
#: Default ``as_of`` — after every synthetic row has become public.
NOW = datetime(2026, 1, 5, 22, 0, tzinfo=UTC)


def us_instrument(ticker: str = "LLY") -> Instrument:
    return Instrument(ticker=ticker, market="US", currency="USD")


def hk_instrument(ticker: str = "0700") -> Instrument:
    return Instrument(ticker=ticker, market="HK", currency="HKD")


def trading_days(count: int, start: date = START) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def price_point(
    day: date,
    close: float,
    *,
    ticker: str = "LLY",
    market: str = "US",
    currency: str = "USD",
    available_at: datetime | None = None,
    source: AvailabilitySource | str = AvailabilitySource.INFERRED,
) -> PricePoint:
    """A PIT-visible point; pass ``available_at=None`` + ``UNKNOWN`` for legacy."""
    when = available_at
    if when is None and AvailabilitySource.parse(source).is_pit_usable:
        when = infer_availability(day, market)[0]
    return PricePoint(
        price_date=day,
        close=close,
        adj_close=close,
        market_timestamp=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
        available_at=when,
        availability_source=source,
        fetched_at=NOW,
        market=market,
        currency=currency,
        available_price_type="close+adj_close",
        used_price_type="close+adj_close",
        source="fixture",
    )


def raw_price_point(
    day: date,
    close: float,
    *,
    dividend: float | None = None,
    split: float | None = None,
    ticker: str = "LLY",
    market: str = "US",
    currency: str = "USD",
) -> PricePoint:
    """A point with no ``adj_close``: the engine must then handle dividends/splits."""
    return PricePoint(
        price_date=day,
        close=close,
        adj_close=None,
        market_timestamp=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
        available_at=infer_availability(day, market)[0],
        availability_source=AvailabilitySource.INFERRED,
        fetched_at=NOW,
        market=market,
        currency=currency,
        available_price_type="close",
        used_price_type="close",
        dividend=dividend,
        split=split,
        source="fixture",
    )


def price_series(
    instrument: Instrument,
    days: list[date],
    *,
    base: float = 100.0,
    step: float = 1.0,
) -> PriceSeries:
    points = [
        price_point(
            day,
            base + index * step,
            ticker=instrument.ticker,
            market=instrument.market,
            currency=instrument.currency,
        )
        for index, day in enumerate(days)
    ]
    return PriceSeries.from_points(
        instrument.ticker,
        points,
        market=instrument.market,
        currency=instrument.currency,
        source="fixture",
    )


def fx_series(currency: str, days: list[date], rates: dict[date, float]) -> FxSeries:
    points = [
        PricePoint(
            price_date=day,
            close=rates[day],
            market_timestamp=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
            available_at=infer_availability(day, "FX")[0],
            availability_source=AvailabilitySource.INFERRED,
            fetched_at=NOW,
            market="FX",
            currency="CNY",
            available_price_type="fx_close",
            used_price_type="fx_close",
            source="fixture",
        )
        for day in days
    ]
    return FxSeries.from_bars(currency, points, source="fixture", base_currency="CNY")


@dataclass
class SyntheticStore:
    """A temporary repository plus the series that live in it."""

    repository: Repository
    days: list[date]
    us: Instrument
    hk: Instrument

    def series(self, instrument: Instrument, *, base: float = 100.0) -> PriceSeries:
        return price_series(instrument, self.days, base=base)


def build_store(repository: Repository, *, days: int = 60) -> SyntheticStore:
    """Populate ``repository`` with two price series, FX and one NAV history."""
    sessions = trading_days(days)
    us = us_instrument()
    hk = hk_instrument()
    repository.save_prices(price_series(us, sessions, base=100.0))
    repository.save_prices(price_series(hk, sessions, base=400.0, step=2.0))
    repository.save_fx(fx_series("USD", sessions, {day: 7.10 for day in sessions}))
    repository.save_fx(fx_series("HKD", sessions, {day: 0.91 for day in sessions}))
    repository.save_official_nav(
        "000369", [(day, 2.8 + index * 0.001) for index, day in enumerate(sessions)]
    )
    return SyntheticStore(repository=repository, days=sessions, us=us, hk=hk)


def make_spec(
    store: SyntheticStore | None = None,
    *,
    weighting: Weighting = Weighting.EQUAL,
    sessions: int = 60,
    **kwargs,
):
    """A valid two-instrument spec over the synthetic sessions.

    ``costs`` is deliberately left unset unless the caller supplies it: the
    runner then reads ``FA_BACKTEST_*`` (review P2), while an explicit
    ``CostModel()`` — even one equal to the defaults — is honoured as a choice.
    """
    days = trading_days(sessions)
    universe = (us_instrument(), hk_instrument())
    defaults = dict(
        spec_id="fixture",
        universe=universe,
        weighting=weighting,
        start=days[0],
        end=days[-1],
        initial_capital=1_000_000.0,
        rebalance=RebalanceRule(frequency="monthly", anchor=1),
    )
    defaults.update(kwargs)
    return StrategySpec(**defaults)


__all__ = [
    "NOW",
    "START",
    "SyntheticStore",
    "build_store",
    "fx_series",
    "hk_instrument",
    "make_spec",
    "price_point",
    "price_series",
    "raw_price_point",
    "trading_days",
    "us_instrument",
]
