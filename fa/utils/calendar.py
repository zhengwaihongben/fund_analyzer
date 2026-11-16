# -*- coding: utf-8 -*-
"""Trading-calendar helpers for the QDII valuation logic.

The fund and the overseas markets do not share a calendar, so every date used
in an estimate has to come from one of these helpers instead of
``today - 1 day``.  Two calendars are kept apart on purpose:

* the **China** calendar (:func:`is_cn_trading_day`, :func:`previous_cn_trading_day`,
  ...) knows about weekends **and** the public-holiday table below, and is
  always evaluated in Beijing time (:func:`beijing_now`);
* the **overseas** calendars are never guessed at all -- the actual trading
  dates are read from the downloaded price series, which is the only reliable
  source (see :mod:`fa.data.qdii_market`).

``CN_HOLIDAYS`` is maintained data, not derived data: it must be extended every
year (the State Council publishes the next year's schedule in November).  For a
year that is not in the table the module degrades to weekends-only and logs the
gap once instead of silently pretending the holidays do not exist.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd

from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

_WEEKEND = (5, 6)

# 中國沒有夏令時，固定 +08:00；Windows 上沒有系統 tzdata，因此不依賴 ZoneInfo
CN_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

# 上海／深圳交易所休市日（僅列平日；週末由 _WEEKEND 處理）。
# 2025 為已公布日程；2026 為已公布日程的維護快照，需按年覆核。
CN_HOLIDAYS = frozenset({
    # 2025
    date(2025, 1, 1),                                        # 元旦
    date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30),
    date(2025, 1, 31), date(2025, 2, 3), date(2025, 2, 4),   # 春節
    date(2025, 4, 4),                                        # 清明
    date(2025, 5, 1), date(2025, 5, 2), date(2025, 5, 5),    # 勞動節
    date(2025, 6, 2),                                        # 端午
    date(2025, 10, 1), date(2025, 10, 2), date(2025, 10, 3),
    date(2025, 10, 6), date(2025, 10, 7), date(2025, 10, 8), # 國慶 + 中秋
    # 2026
    date(2026, 1, 1), date(2026, 1, 2),                      # 元旦
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 2, 19), date(2026, 2, 20),                    # 春節
    date(2026, 4, 6),                                        # 清明
    date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5),    # 勞動節
    date(2026, 6, 19),                                       # 端午
    date(2026, 9, 25),                                       # 中秋
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5),
    date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8), # 國慶
})

_HOLIDAY_YEARS = frozenset(d.year for d in CN_HOLIDAYS)
_warned_years: set[int] = set()


def to_date(value) -> date:
    """Coerce ``date`` / ``datetime`` / ``Timestamp`` / ISO string to ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def beijing_now() -> datetime:
    """Current time in Beijing, regardless of the host machine's time zone."""
    return datetime.now(CN_TZ)


def to_beijing(value=None) -> datetime:
    """Coerce to an aware Beijing ``datetime``.

    A naive ``datetime`` is interpreted as Beijing time (the only time zone
    this project reasons about); an aware one is converted.
    """
    if value is None:
        return beijing_now()
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=CN_TZ)
    else:
        moment = pd.Timestamp(value).to_pydatetime()
    if moment.tzinfo is None:
        return moment.replace(tzinfo=CN_TZ)
    return moment.astimezone(CN_TZ)


def is_business_day(day) -> bool:
    """Weekday check only -- no holiday knowledge, for non-China calendars."""
    return to_date(day).weekday() not in _WEEKEND


def is_cn_trading_day(day) -> bool:
    """China trading day: not a weekend and not in :data:`CN_HOLIDAYS`."""
    value = to_date(day)
    if value.weekday() in _WEEKEND:
        return False
    if value.year not in _HOLIDAY_YEARS and value.year not in _warned_years:
        _warned_years.add(value.year)
        log.warning("中國假期表未收錄 %s 年，該年度僅按週末判斷交易日", value.year)
    return value not in CN_HOLIDAYS


def previous_business_day(day) -> date:
    """Last weekday strictly before ``day``."""
    value = to_date(day) - timedelta(days=1)
    while value.weekday() in _WEEKEND:
        value -= timedelta(days=1)
    return value


def next_business_day(day) -> date:
    """First weekday strictly after ``day``."""
    value = to_date(day) + timedelta(days=1)
    while value.weekday() in _WEEKEND:
        value += timedelta(days=1)
    return value


def previous_cn_trading_day(day) -> date:
    """Last China trading day strictly before ``day``."""
    value = to_date(day) - timedelta(days=1)
    for _ in range(30):
        if is_cn_trading_day(value):
            return value
        value -= timedelta(days=1)
    return to_date(day) - timedelta(days=1)


def next_cn_trading_day(day) -> date:
    """First China trading day strictly after ``day``."""
    value = to_date(day) + timedelta(days=1)
    for _ in range(30):
        if is_cn_trading_day(value):
            return value
        value += timedelta(days=1)
    return to_date(day) + timedelta(days=1)


def last_index_on_or_before(index, day):
    """Last value of a sorted ``DatetimeIndex`` that is ``<= day`` (or None)."""
    if index is None or len(index) == 0:
        return None
    ts = pd.Timestamp(day).normalize()
    pos = index.searchsorted(ts, side="right") - 1
    if pos < 0:
        return None
    return index[pos]


def business_days_between(start, end) -> int:
    """Number of weekdays in ``(start, end]``; 0 when ``end <= start``."""
    start, end = to_date(start), to_date(end)
    if end <= start:
        return 0
    return len(pd.bdate_range(start, end)) - 1


def cn_trading_days_between(start, end) -> int:
    """Number of China trading days in ``(start, end]``; 0 when ``end <= start``."""
    start, end = to_date(start), to_date(end)
    if end <= start:
        return 0
    days = pd.bdate_range(start + timedelta(days=1), end)
    return sum(1 for stamp in days if stamp.date() not in CN_HOLIDAYS)
