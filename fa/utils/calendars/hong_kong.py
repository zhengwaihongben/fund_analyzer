# -*- coding: utf-8 -*-
"""Hong Kong (HKEX) calendar.

Holiday coverage is 2025–2026 only; the lunar-calendar dates must be verified
against HKEX when a year is added.
"""
from __future__ import annotations

from datetime import date, time, timedelta, timezone

from fa.utils.calendars.base import HolidayCalendar, _days

HOLIDAYS_HK: dict[int, frozenset[date]] = {
    2025: _days(
        2025,
        (1, 1),    # New Year's Day
        (1, 29),   # Lunar New Year
        (1, 30),
        (1, 31),
        (4, 4),    # Ching Ming
        (4, 18),   # Good Friday
        (4, 21),   # Easter Monday
        (5, 1),    # Labour Day
        (5, 5),    # Buddha's Birthday
        (7, 1),    # HKSAR Establishment Day
        (10, 1),   # National Day
        (10, 7),   # Day after Mid-Autumn
        (10, 29),  # Chung Yeung
        (12, 25),  # Christmas
        (12, 26),  # Boxing Day
    ),
    2026: _days(
        2026,
        (1, 1),
        (2, 17),   # Lunar New Year
        (2, 18),
        (2, 19),
        (4, 3),    # Good Friday
        (4, 6),    # Easter Monday / Ching Ming observed
        (5, 1),
        (5, 25),   # Buddha's Birthday
        (6, 19),   # Tuen Ng
        (7, 1),
        (9, 26),   # Day after Mid-Autumn
        (10, 1),
        (10, 19),  # Chung Yeung
        (12, 25),
        (12, 26),
    ),
}


class HongKongCalendar(HolidayCalendar):
    market = "HK"
    timezone_name = "Asia/Hong_Kong"
    close_time = time(16, 0)
    utc_offset_hours = 8.0
    holidays = HOLIDAYS_HK

    def tz_for(self, day: date) -> timezone:
        return timezone(timedelta(hours=8), "Asia/Hong_Kong")
