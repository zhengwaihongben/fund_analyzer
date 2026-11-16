# -*- coding: utf-8 -*-
"""Market calendar base class.

V2 needs to know, for each exchange, which dates are trading days and when the
session closes.  The implementation is rule-based (weekends plus a published
holiday table per year) so the project carries no hard dependency on a calendar
package; :meth:`HolidayCalendar._warn_missing_year` makes an unverified year
loud instead of silently treating holidays as trading days.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

log = logging.getLogger(__name__)

WEEKEND = (5, 6)


class CalendarError(ValueError):
    """Raised when an unsupported market or malformed calendar is requested."""


def _fixed_tz(hours: float, name: str) -> timezone:
    return timezone(timedelta(hours=hours), name)


@dataclass(frozen=True)
class Session:
    """One trading session: the date and the local close instant."""

    day: date
    close: datetime


class HolidayCalendar:
    """Weekend + holiday-table calendar for one market."""

    market: str = ""
    timezone_name: str = ""
    #: Local time of the regular close.
    close_time: time = time(16, 0)
    #: UTC offset in hours during standard time (used for the tz object).
    utc_offset_hours: float = 0.0
    #: Holiday dates, per year.
    holidays: dict[int, frozenset[date]] = {}

    def __init__(self) -> None:
        self._warned_years: set[int] = set()

    # -- construction helpers --------------------------------------------------
    @property
    def supported_years(self) -> frozenset[int]:
        return frozenset(self.holidays)

    def _holiday_set(self, year: int) -> frozenset[date]:
        found = self.holidays.get(year)
        if found is not None:
            return found
        self._warn_missing_year(year)
        return frozenset()

    def _warn_missing_year(self, year: int) -> None:
        if year in self._warned_years:
            return
        self._warned_years.add(year)
        log.warning(
            "市場日曆未收錄 %s 年，該年度僅按週末判斷交易日 (market=%s)", year, self.market
        )

    # -- calendar queries ------------------------------------------------------
    def is_trading_day(self, day: date) -> bool:
        if day.weekday() in WEEKEND:
            return False
        return day not in self._holiday_set(day.year)

    def previous_trading_day(self, day: date) -> date:
        cursor = day - timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(cursor):
                return cursor
            cursor -= timedelta(days=1)
        raise CalendarError(f"{self.market}: 找不到 {day} 之前的交易日")

    def next_trading_day(self, day: date) -> date:
        cursor = day + timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(cursor):
                return cursor
            cursor += timedelta(days=1)
        raise CalendarError(f"{self.market}: 找不到 {day} 之後的交易日")

    def trading_days_between(self, start: date, end: date) -> int:
        """Number of trading days strictly after ``start`` up to ``end``."""
        if end <= start:
            return 0
        count = 0
        cursor = start + timedelta(days=1)
        while cursor <= end:
            if self.is_trading_day(cursor):
                count += 1
            cursor += timedelta(days=1)
        return count

    def sessions_stale(self, price_date: date, wanted_date: date) -> int:
        """How many sessions a price is behind ``wanted_date`` (0 = current)."""
        if price_date >= wanted_date:
            return 0
        return self.trading_days_between(price_date, wanted_date)

    def market_close_datetime(self, day: date) -> datetime:
        """Timezone-aware close instant for ``day`` (exchange local time)."""
        return datetime.combine(day, self.close_time, tzinfo=self.tz_for(day))

    def tz(self) -> timezone:
        return _fixed_tz(self.utc_offset_hours, self.timezone_name or self.market)

    def tz_for(self, day: date) -> timezone:
        """Timezone in force on ``day`` (overridden where DST applies)."""
        return self.tz()

    def session(self, day: date) -> Session | None:
        """The session for ``day``, or ``None`` when the market is closed."""
        if not self.is_trading_day(day):
            return None
        return Session(day=day, close=self.market_close_datetime(day))


def _days(year: int, *month_days: tuple[int, int]) -> frozenset[date]:
    return frozenset(date(year, month, day) for month, day in month_days)
