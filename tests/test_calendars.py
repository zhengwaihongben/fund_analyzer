# -*- coding: utf-8 -*-
"""V2 market calendar tests (CN / US / HK / JP, plus fallbacks)."""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from fa.utils.calendars import (
    FallbackCalendar,
    available_markets,
    calendar_for,
    get_calendar,
    normalise_market,
)


class ChinaCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = get_calendar("CN")

    def test_weekend_and_holiday(self) -> None:
        self.assertFalse(self.cal.is_trading_day(date(2025, 6, 7)))    # Saturday
        self.assertFalse(self.cal.is_trading_day(date(2025, 10, 1)))   # National Day
        self.assertFalse(self.cal.is_trading_day(date(2025, 6, 2)))    # Dragon Boat
        self.assertTrue(self.cal.is_trading_day(date(2025, 10, 9)))

    def test_previous_and_next_across_holiday_week(self) -> None:
        self.assertEqual(self.cal.previous_trading_day(date(2025, 10, 9)), date(2025, 9, 30))
        self.assertEqual(self.cal.next_trading_day(date(2025, 9, 30)), date(2025, 10, 9))

    def test_sessions_between(self) -> None:
        # 2025-09-30 -> 2025-10-09 has exactly one session before 10-09.
        self.assertEqual(self.cal.trading_days_between(date(2025, 9, 30), date(2025, 10, 9)), 1)
        self.assertEqual(self.cal.sessions_stale(date(2025, 9, 30), date(2025, 10, 9)), 1)
        self.assertEqual(self.cal.sessions_stale(date(2025, 10, 9), date(2025, 10, 9)), 0)

    def test_close_datetime_is_beijing_time(self) -> None:
        close = self.cal.market_close_datetime(date(2025, 6, 6))
        self.assertEqual(close.hour, 15)
        self.assertEqual(close.utcoffset(), timedelta(hours=8))
        self.assertEqual(close.date(), date(2025, 6, 6))

    def test_session_is_none_when_closed(self) -> None:
        self.assertIsNone(self.cal.session(date(2025, 10, 1)))
        session = self.cal.session(date(2025, 6, 6))
        self.assertIsNotNone(session)
        self.assertEqual(session.day, date(2025, 6, 6))


class USCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = get_calendar("US")

    def test_holidays(self) -> None:
        self.assertFalse(self.cal.is_trading_day(date(2025, 7, 4)))
        self.assertFalse(self.cal.is_trading_day(date(2025, 11, 27)))
        self.assertFalse(self.cal.is_trading_day(date(2025, 4, 18)))
        self.assertTrue(self.cal.is_trading_day(date(2025, 7, 7)))

    def test_dst_offsets(self) -> None:
        summer = self.cal.market_close_datetime(date(2025, 7, 7))
        winter = self.cal.market_close_datetime(date(2025, 12, 15))
        self.assertEqual(summer.utcoffset(), timedelta(hours=-4))
        self.assertEqual(winter.utcoffset(), timedelta(hours=-5))
        self.assertEqual(summer.hour, 16)

    def test_dst_boundaries(self) -> None:
        # 2nd Sunday of March 2025 = 03-09; 1st Sunday of November 2025 = 11-02.
        self.assertEqual(
            self.cal.market_close_datetime(date(2025, 3, 10)).utcoffset(),
            timedelta(hours=-4),
        )
        self.assertEqual(
            self.cal.market_close_datetime(date(2025, 10, 31)).utcoffset(),
            timedelta(hours=-4),
        )
        self.assertEqual(
            self.cal.market_close_datetime(date(2025, 11, 3)).utcoffset(),
            timedelta(hours=-5),
        )


class HongKongCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = get_calendar("HK")

    def test_holidays(self) -> None:
        self.assertFalse(self.cal.is_trading_day(date(2025, 1, 29)))
        self.assertFalse(self.cal.is_trading_day(date(2025, 10, 1)))
        self.assertTrue(self.cal.is_trading_day(date(2025, 10, 8)))

    def test_close_time(self) -> None:
        close = self.cal.market_close_datetime(date(2025, 6, 6))
        self.assertEqual((close.hour, close.minute), (16, 0))
        self.assertEqual(close.utcoffset(), timedelta(hours=8))


class JapanCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = get_calendar("JP")

    def test_holidays(self) -> None:
        self.assertFalse(self.cal.is_trading_day(date(2025, 5, 6)))
        self.assertFalse(self.cal.is_trading_day(date(2025, 12, 31)))
        self.assertTrue(self.cal.is_trading_day(date(2025, 5, 7)))

    def test_close_at_1530_tokyo(self) -> None:
        close = self.cal.market_close_datetime(date(2025, 6, 6))
        self.assertEqual((close.hour, close.minute), (15, 30))
        self.assertEqual(close.utcoffset(), timedelta(hours=9))


class RegistryTests(unittest.TestCase):
    def test_aliases_share_one_instance(self) -> None:
        self.assertIs(get_calendar("NYSE"), get_calendar("US"))
        self.assertEqual(normalise_market("nasdaq"), "US")
        self.assertEqual(get_calendar("HKEX").market, "HK")
        self.assertEqual(get_calendar("SSE").market, "CN")
        self.assertEqual(get_calendar("TSE").market, "JP")

    def test_calendar_from_ticker_suffix(self) -> None:
        self.assertEqual(calendar_for(ticker="0700.HK").market, "HK")
        self.assertEqual(calendar_for(ticker="AZN.L").market, "GB")
        self.assertEqual(calendar_for(market="US").market, "US")

    def test_unknown_market_falls_back_to_weekends(self) -> None:
        cal = get_calendar("CH")
        self.assertIsInstance(cal, FallbackCalendar)
        self.assertTrue(cal.is_fallback)
        self.assertFalse(cal.is_trading_day(date(2024, 12, 28)))  # Saturday
        self.assertTrue(cal.is_trading_day(date(2024, 12, 27)))   # Friday

    def test_available_markets(self) -> None:
        self.assertEqual(available_markets(), ["CN", "US", "HK", "JP"])

    def test_unknown_year_warns_once(self) -> None:
        cal = get_calendar("US")
        with self.assertLogs("fa.utils.calendars.base", level="WARNING") as ctx:
            cal.is_trading_day(date(2030, 1, 1))
            cal.is_trading_day(date(2030, 1, 2))
        self.assertEqual(len(ctx.records), 1)
        self.assertIn("2030", ctx.records[0].getMessage())


if __name__ == "__main__":
    unittest.main()
