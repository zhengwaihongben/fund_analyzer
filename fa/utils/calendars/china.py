# -*- coding: utf-8 -*-
"""China (Shanghai / Shenzhen) exchange calendar.

Wraps the holiday table already maintained by :mod:`fa.utils.calendar`, which is
shared with the V1 QDII estimator, so both code paths agree on what "yesterday"
means for the fund itself.
"""
from __future__ import annotations

from datetime import date, time

from fa.utils.calendar import CN_HOLIDAYS, CN_TZ
from fa.utils.calendars.base import HolidayCalendar


class ChinaCalendar(HolidayCalendar):
    market = "CN"
    timezone_name = "Asia/Shanghai"
    close_time = time(15, 0)
    utc_offset_hours = 8.0

    def __init__(self) -> None:
        super().__init__()
        years: dict[int, set[date]] = {}
        for day in CN_HOLIDAYS:
            years.setdefault(day.year, set()).add(day)
        self.holidays = {year: frozenset(days) for year, days in years.items()}

    def tz(self):
        return CN_TZ
