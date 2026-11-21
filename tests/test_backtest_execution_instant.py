# -*- coding: utf-8 -*-
"""Stage D: the execution instant is the close, the fill price is that close (BT-13)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fa.backtesting import DataAccess, run_engine
from fa.data.database.repository import Repository
from fa.utils.calendars import calendar_for
from tests.fixtures.backtesting import NOW, build_store, make_spec, us_instrument

UTC = NOW.tzinfo


class ExecutionInstantTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "instant.db")
        self.addCleanup(self.repository.close)
        self.store = build_store(self.repository, days=40)
        self.spec = make_spec(self.store, sessions=40)
        self.access = DataAccess(self.repository)
        self.instrument = us_instrument()
        self.calendar = calendar_for("US")
        self.day = next(
            day for day in self.store.days if self.calendar.is_trading_day(day))

    def _close(self, day):
        return self.calendar.session(day).close

    def test_the_bar_is_invisible_at_the_close_itself(self) -> None:
        with self.assertRaises(Exception):
            self.access.price_at(self.instrument, self._close(self.day), self.day)

    def test_the_bar_is_visible_thirty_minutes_later(self) -> None:
        from datetime import timedelta
        point = self.access.price_at(self.instrument,
                                     self._close(self.day) + timedelta(minutes=30),
                                     self.day)
        self.assertIsNotNone(point)
        self.assertEqual(point.price_date, self.day)

    def test_the_recorded_fill_price_is_the_execution_session_close(self) -> None:
        result = run_engine(self.spec, self.access)
        self.assertTrue(result.trades)
        closes = {
            day: 100.0 + index for index, day in enumerate(self.store.days)
        }
        for trade in result.trades:
            if trade.ticker == "LLY":
                self.assertAlmostEqual(trade.price_local, closes[trade.session], places=9,
                                       msg=f"成交價必須是 {trade.session} 的收盤價")
                self.assertEqual(trade.price_source, "close")

    def test_a_late_publication_is_not_substituted(self) -> None:
        from datetime import datetime, timedelta

        from fa.data.database.repository import Repository as Repo
        from fa.models.market_data import AvailabilitySource, PricePoint, PriceSeries

        day = self.store.days[12]
        series = self.repository.load_prices("LLY")
        points = list(series.points)
        late = day
        updated = [
            point for point in points if point.price_date != late
        ]
        updated.append(PricePoint(
            price_date=late,
            close=999.0,
            adj_close=999.0,
            market_timestamp=datetime.combine(late, datetime.min.time(), tzinfo=UTC),
            available_at=datetime(2030, 1, 1, tzinfo=UTC),
            availability_source=AvailabilitySource.INFERRED,
            fetched_at=NOW,
            market="US",
            currency="USD",
            available_price_type="close+adj_close",
            used_price_type="close+adj_close",
            source="fixture",
        ))
        self.repository.save_prices(PriceSeries.from_points(
            "LLY", updated, market="US", currency="USD", source="fixture"))
        point = self.access.price_at(self.instrument, NOW, late)
        self.assertIsNotNone(point)
        self.assertNotEqual(point.close, 999.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
