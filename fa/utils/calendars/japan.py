# -*- coding: utf-8 -*-
"""Tokyo Stock Exchange (JP) calendar.

Holiday coverage is 2025–2026 only.  TSE's regular session closes at 15:30
local time (extended from 15:00 in November 2024).
"""
from __future__ import annotations

from datetime import date, time, timedelta, timezone

from fa.utils.calendars.base import HolidayCalendar, _days

HOLIDAYS_JP: dict[int, frozenset[date]] = {
    2025: _days(
        2025,
        (1, 1),    # New Year
        (1, 2),
        (1, 3),
        (1, 13),   # Coming of Age Day
        (2, 11),   # National Foundation Day
        (2, 24),   # Emperor's Birthday (observed)
        (3, 20),   # Vernal Equinox
        (4, 29),   # Showa Day
        (5, 5),    # Children's Day
        (5, 6),    # Greenery Day (observed)
        (7, 21),   # Marine Day
        (8, 11),   # Mountain Day
        (9, 15),   # Respect for the Aged Day
        (9, 23),   # Autumnal Equinox
        (10, 13),  # Sports Day
        (11, 3),   # Culture Day
        (11, 24),  # Labour Thanksgiving (observed)
        (12, 31),  # Year-end close
    ),
    2026: _days(
        2026,
        (1, 1),
        (1, 2),
        (1, 3),
        (1, 12),
        (2, 11),
        (2, 23),
        (3, 20),
        (4, 29),
        (5, 5),
        (5, 6),
        (7, 20),
        (8, 11),
        (9, 21),
        (9, 22),   # Autumnal Equinox (observed)
        (10, 12),
        (11, 3),
        (11, 23),
        (12, 31),
    ),
}


class JapanCalendar(HolidayCalendar):
    market = "JP"
    timezone_name = "Asia/Tokyo"
    close_time = time(15, 30)
    utc_offset_hours = 9.0
    holidays = HOLIDAYS_JP

    def tz_for(self, day: date) -> timezone:
        return timezone(timedelta(hours=9), "Asia/Tokyo")
