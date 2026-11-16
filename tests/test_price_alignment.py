# -*- coding: utf-8 -*-
"""V2 price/FX alignment tests (staleness policy B)."""
from __future__ import annotations

import unittest
from datetime import date

from fa.analysis.price_alignment import Alignment, align_fx, align_price, describe_alignment
from fa.models.market_data import FxSeries, PricePoint, PriceSeries
from fa.models.return_result import ReturnStatus


def series(days_values, ticker="LLY", market="US", currency="USD") -> PriceSeries:
    points = [
        PricePoint(price_date=day, close=value, adj_close=value, market=market, currency=currency)
        for day, value in days_values
    ]
    return PriceSeries.from_points(ticker, points, market=market, currency=currency)


class AlignPriceTests(unittest.TestCase):
    def setUp(self) -> None:
        # Fri 2025-06-06 and Thu 2025-06-05, both US sessions.
        self.prices = series([(date(2025, 6, 5), 100.0), (date(2025, 6, 6), 110.0)])

    def test_exact_date(self) -> None:
        result = align_price(self.prices, date(2025, 6, 6))
        self.assertEqual(result.status, ReturnStatus.OK)
        self.assertEqual(result.value, 110.0)
        self.assertEqual(result.actual_date, date(2025, 6, 6))
        self.assertTrue(result.ok)
        self.assertFalse(result.is_stale)

    def test_uses_last_session_on_or_before(self) -> None:
        # Sunday 2025-06-08 -> falls back to Friday's close, one session behind.
        result = align_price(self.prices, date(2025, 6, 8))
        self.assertEqual(result.status, ReturnStatus.OK)
        self.assertEqual(result.actual_date, date(2025, 6, 6))
        self.assertEqual(result.stale_sessions, 0)

    def test_slightly_stale_is_kept_with_status(self) -> None:
        result = align_price(self.prices, date(2025, 6, 9), warn_sessions=0)
        self.assertEqual(result.status, ReturnStatus.STALE_PRICE)
        self.assertTrue(result.is_stale)
        self.assertTrue(result.ok)
        self.assertEqual(result.value, 110.0)
        self.assertEqual(result.stale_sessions, 1)
        self.assertIn("落後 1 個交易日", describe_alignment(result))

    def test_too_stale_is_excluded(self) -> None:
        result = align_price(self.prices, date(2025, 6, 9), max_sessions=0)
        self.assertEqual(result.status, ReturnStatus.MISSING_TARGET_PRICE)
        self.assertIsNone(result.value)
        self.assertFalse(result.ok)

    def test_base_endpoint_uses_base_statuses(self) -> None:
        stale = align_price(self.prices, date(2025, 6, 9), warn_sessions=0, base=True)
        self.assertEqual(stale.status, ReturnStatus.STALE_BASE_PRICE)
        too_old = align_price(self.prices, date(2025, 6, 9), max_sessions=0, base=True)
        self.assertEqual(too_old.status, ReturnStatus.MISSING_BASE_PRICE)

    def test_no_data_before_wanted_date(self) -> None:
        result = align_price(self.prices, date(2025, 1, 2))
        self.assertEqual(result.status, ReturnStatus.MISSING_TARGET_PRICE)
        self.assertIsNone(result.value)

    def test_missing_series(self) -> None:
        result = align_price(None, date(2025, 6, 6))
        self.assertEqual(result.status, ReturnStatus.MISSING_TARGET_PRICE)

    def test_missing_wanted_date(self) -> None:
        result = align_price(self.prices, None)
        self.assertEqual(result.status, ReturnStatus.MISSING_TARGET_PRICE)

    def test_holiday_in_market_calendar_does_not_count(self) -> None:
        # 2025-11-27 is Thanksgiving: Wednesday -> Friday is a single session.
        prices = series([(date(2025, 11, 26), 10.0)])
        result = align_price(prices, date(2025, 11, 28), warn_sessions=0)
        self.assertEqual(result.stale_sessions, 1)
        self.assertEqual(result.status, ReturnStatus.STALE_PRICE)


class AlignFxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = FxSeries.from_pairs(
            "USD",
            [(date(2025, 6, 5), 7.00), (date(2025, 6, 6), 7.07)],
            source="test",
        )

    def test_exact_rate(self) -> None:
        result = align_fx(self.fx, date(2025, 6, 6))
        self.assertEqual(result.status, ReturnStatus.OK)
        self.assertAlmostEqual(result.value, 7.07)

    def test_stale_and_excluded(self) -> None:
        stale = align_fx(self.fx, date(2025, 6, 9), warn_sessions=0)
        self.assertEqual(stale.status, ReturnStatus.STALE_FX)
        excluded = align_fx(self.fx, date(2025, 6, 9), max_sessions=0)
        self.assertEqual(excluded.status, ReturnStatus.MISSING_FX)

    def test_missing_series_is_never_cny(self) -> None:
        result = align_fx(None, date(2025, 6, 6))
        self.assertEqual(result.status, ReturnStatus.MISSING_FX)
        self.assertIsNone(result.value)


class AlignmentModelTests(unittest.TestCase):
    def test_describe_without_value(self) -> None:
        alignment = Alignment(status=ReturnStatus.MISSING_FX, wanted_date=date(2025, 6, 6))
        self.assertIn("missing-fx", describe_alignment(alignment))

    def test_describe_with_value(self) -> None:
        alignment = Alignment(
            status=ReturnStatus.OK,
            wanted_date=date(2025, 6, 6),
            value=110.0,
            actual_date=date(2025, 6, 6),
        )
        self.assertIn("110", describe_alignment(alignment))


if __name__ == "__main__":
    unittest.main()
