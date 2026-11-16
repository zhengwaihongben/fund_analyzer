# -*- coding: utf-8 -*-
"""US (NYSE / NASDAQ) calendar with a simple DST rule.

Holiday coverage is 2025–2026 only.  A new year must be added deliberately
(and checked against the exchange calendar), because a missing year would
otherwise turn exchange holidays into trading days.
"""
from __future__ import annotations

from datetime import date, time, timedelta, timezone

from fa.utils.calendars.base import HolidayCalendar, _days

HOLIDAYS_US: dict[int, frozenset[date]] = {
    2025: _days(
        2025,
        (1, 1),    # New Year's Day
        (1, 20),   # Martin Luther King Jr. Day
        (2, 17),   # Presidents' Day
        (4, 18),   # Good Friday
        (5, 26),   # Memorial Day
        (6, 19),   # Juneteenth
        (7, 4),    # Independence Day
        (9, 1),    # Labor Day
        (11, 27),  # Thanksgiving
        (12, 25),  # Christmas
    ),
    2026: _days(
        2026,
        (1, 1),
        (1, 19),
        (2, 16),
        (4, 3),    # Good Friday
        (5, 25),
        (6, 19),
        (7, 3),    # Independence Day observed
        (9, 7),
        (11, 26),
        (12, 25),
    ),
}


def _first_sunday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7)


def _nth_sunday(year: int, month: int, n: int) -> date:
    return _first_sunday(year, month) + timedelta(days=7 * (n - 1))


class USCalendar(HolidayCalendar):
    market = "US"
    timezone_name = "America/New_York"
    close_time = time(16, 0)
    utc_offset_hours = -5.0
    holidays = HOLIDAYS_US

    def tz_for(self, day: date) -> timezone:
        """US Eastern time: EDT between the 2nd Sunday of March and 1st of November."""
        start = _nth_sunday(day.year, 3, 2)
        end = _first_sunday(day.year, 11)
        offset = -4.0 if start <= day < end else -5.0
        return timezone(timedelta(hours=offset), "America/New_York")
