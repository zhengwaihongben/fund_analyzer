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
    "HKSE": "HK",
    "CN": "CN",
    "SH": "CN",
    "SZ": "CN",
    "SSE": "CN",
    "SZSE": "CN",
    "JP": "JP",
    "TSE": "JP",
    "TYO": "JP",
    "JPX": "JP",
    "CH": "CH",
    "SIX": "CH",
    "SWX": "CH",
    "GB": "GB",
    "LSE": "GB",
    "UK": "GB",
    "DE": "DE",
    "XETRA": "DE",
    "GER": "DE",
    "FRA": "DE",
    "FR": "FR",
    "PAR": "FR",
    "NL": "NL",
    "AMS": "NL",
    "CA": "CA",
    "TO": "CA",
    "AU": "AU",
    "AX": "AU",
}

#: Yahoo's ``exchangeName`` values (and their variants) mapped onto market codes.
_EXCHANGE_NAMES = {
    "NASDAQS": "US",
    "NASDAQGS": "US",
    "NASDAQGM": "US",
    "NASDAQCM": "US",
    "NMS": "US",
    "NGM": "US",
    "NCM": "US",
    "NYQ": "US",
    "NYE": "US",
    "PCX": "US",
    "ASE": "US",
    "BATS": "US",
    "SIX SWISS EXCHANGE": "CH",
    "SIX": "CH",
    "LONDON STOCK EXCHANGE": "GB",
    "LSE": "GB",
    "LSE AIM": "GB",
    "XETRA": "DE",
    "GERMANY": "DE",
    "EURONEXT PARIS": "FR",
    "EURONEXT AMSTERDAM": "NL",
    "HONG KONG STOCK EXCHANGE": "HK",
    "HKSE": "HK",
    "TOKYO STOCK EXCHANGE": "JP",
    "JPX": "JP",
    "TORONTO STOCK EXCHANGE": "CA",
    "ASX": "AU",
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


def normalise_exchange(name: str | None) -> str:
    """Map an exchange name/market alias onto a canonical market code.

    ``"NasdaqGS"``, ``"NMS"`` and ``"NYQ"`` all mean the US calendar; leaving
    them as-is silently dropped the entire US holiday table.
    """
    if not name:
        return ""
    key = str(name).strip().upper()
    if key in _ALIASES:
        return _ALIASES[key]
    if key in _EXCHANGE_NAMES:
        return _EXCHANGE_NAMES[key]
    for prefix, code in _EXCHANGE_NAMES.items():
        if key.startswith(prefix):
            return code
    return key


def normalise_market(market: str | None) -> str:
    return normalise_exchange(market)


def calendar_is_fallback(calendar: object) -> bool:
    """True when ``calendar`` has no holiday table (weekends only)."""
    return bool(getattr(calendar, "is_fallback", False))


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
    """Best-effort calendar from an exchange name, market code or ticker suffix."""
    code = normalise_exchange(market)
    if code in ("US", "HK", "CN", "JP"):
        return get_calendar(code)
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
    return get_calendar(code)


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
    "calendar_is_fallback",
    "normalise_exchange",
    "normalise_market",
    "available_markets",
]
