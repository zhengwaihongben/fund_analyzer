# -*- coding: utf-8 -*-
"""Market calendars for the V2 valuation engine.

``get_calendar("US")`` returns a cached calendar instance; ``calendar_for(ticker)``
maps a holding's market/ticker to one.  Markets with no calendar fall back to a
weekend-only calendar, which is reported by ``is_fallback=True`` so callers can
lower the data-quality grade instead of pretending precision.
"""
from __future__ import annotations

from datetime import date

from fa.utils.calendars.base import (
    CalendarError,
    HolidayCalendar,
    Session,
    WEEKEND,
)
from fa.utils.calendars.china import ChinaCalendar
from fa.utils.calendars.hong_kong import HongKongCalendar
from fa.utils.calendars.japan import JapanCalendar
from fa.utils.calendars.us import USCalendar

_CALENDARS: dict[str, HolidayCalendar] = {}

_ALIASES = {
    "US": "US",
    "NYSE": "US",
    "NASDAQ": "US",
    "USA": "US",
    "AMEX": "US",
    "HK": "HK",
    "HKEX": "HK",
    "HKG": "HK",
    "CN": "CN",
    "SH": "CN",
    "SZ": "CN",
    "SSE": "CN",
    "SZSE": "CN",
    "JP": "JP",
    "TSE": "JP",
    "TYO": "JP",
    "JPX": "JP",
}


class FallbackCalendar(HolidayCalendar):
    """Weekend-only calendar used when a market has no holiday table."""

    def __init__(self, market: str = "UNKNOWN") -> None:
        super().__init__()
        self.market = market
        self.timezone_name = "UTC"
        self.utc_offset_hours = 0.0

    @property
    def is_fallback(self) -> bool:
        return True

    def _holiday_set(self, year: int) -> frozenset[date]:
        # No table by design: weekends only, and no "missing year" warning noise.
        return frozenset()


def normalise_market(market: str | None) -> str:
    if not market:
        return ""
    return _ALIASES.get(str(market).strip().upper(), str(market).strip().upper())


def get_calendar(market: str | None) -> HolidayCalendar:
    """Calendar for ``market`` (alias-tolerant), cached."""
    key = normalise_market(market)
    if key in _CALENDARS:
        return _CALENDARS[key]
    if key == "US":
        calendar: HolidayCalendar = USCalendar()
    elif key == "HK":
        calendar = HongKongCalendar()
    elif key == "CN":
        calendar = ChinaCalendar()
    elif key == "JP":
        calendar = JapanCalendar()
    else:
        calendar = FallbackCalendar(key or "UNKNOWN")
    _CALENDARS[key] = calendar
    return calendar


def calendar_for(market: str | None = None, ticker: str | None = None) -> HolidayCalendar:
    """Best-effort calendar from a market code or a ticker suffix."""
    if market:
        return get_calendar(market)
    if ticker and "." in ticker:
        suffix = ticker.rsplit(".", 1)[1].upper()
        suffix_map = {
            "SW": "CH",
            "L": "GB",
            "DE": "DE",
            "PA": "FR",
            "AS": "NL",
            "T": "JP",
            "HK": "HK",
            "TO": "CA",
            "AX": "AU",
        }
        return get_calendar(suffix_map.get(suffix, suffix))
    return get_calendar(market)


def available_markets() -> list[str]:
    return ["CN", "US", "HK", "JP"]


__all__ = [
    "CalendarError",
    "HolidayCalendar",
    "Session",
    "WEEKEND",
    "ChinaCalendar",
    "HongKongCalendar",
    "JapanCalendar",
    "USCalendar",
    "FallbackCalendar",
    "get_calendar",
    "calendar_for",
    "normalise_market",
    "available_markets",
]
