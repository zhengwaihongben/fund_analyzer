# -*- coding: utf-8 -*-
"""Stage D review P1-4: missing data vs contradictory data.

A hole in otherwise valid data is recoverable: the simulation records an
anomaly.  Data that contradicts the strategy (venue, currency, FX base) must
abort the run — a backtest built from data nobody vouched for is worse than no
backtest at all.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fa.backtesting import DataAccess
from fa.backtesting.data_access import (
    BacktestDataIntegrityError,
    BacktestDataMissingError,
)
from fa.backtesting.engine import run_engine
from fa.backtesting.runner import _grid, data_fingerprint
from fa.data.database.repository import Repository
from fa.models.market_data import AvailabilitySource, FxSeries, PricePoint, PriceSeries
from tests.fixtures.backtesting import NOW, build_store, make_spec, us_instrument

UTC = timezone.utc


class IntegrityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "integrity.db")
        self.addCleanup(self.repository.close)
        self.store = build_store(self.repository, days=20)
        self.access = DataAccess(self.repository)


class PriceIntegrityTests(IntegrityTestCase):
    def test_a_foreign_venue_under_the_same_ticker_aborts(self) -> None:
        # Flip the venue in place for the last sessions: one version only, so
        # every reader sees the contradictory row.
        self.repository._conn.execute(
            "UPDATE market_prices SET market = 'SIX' "
            "WHERE ticker = 'LLY' AND price_date >= ?",
            (self.store.days[-3].isoformat(),),
        )
        self.repository._conn.commit()
        with self.assertRaises(BacktestDataIntegrityError):
            self.access.prices(us_instrument(), NOW)
        with self.assertRaises(BacktestDataIntegrityError):
            run_engine(make_spec(self.store, sessions=20), self.access)
        with self.assertRaises(BacktestDataIntegrityError):
            spec = make_spec(self.store, sessions=20)
            data_fingerprint(spec, self.access, sessions=_grid(spec),
                             lag_minutes=30)

    def test_a_price_without_a_venue_aborts(self) -> None:
        self.repository.save_prices(PriceSeries.from_points(
            "LLY",
            [PricePoint(
                price_date=self.store.days[3],
                close=100.0,
                market_timestamp=datetime.combine(
                    self.store.days[3], datetime.min.time(), tzinfo=UTC),
                available_at=NOW,
                availability_source=AvailabilitySource.INFERRED,
                fetched_at=NOW,
                market="",
                currency="USD",
                available_price_type="close",
                used_price_type="close",
                source="fixture",
            )],
            market="", currency="USD", source="fixture"))
        with self.assertRaises(BacktestDataIntegrityError):
            self.access.prices(us_instrument(), NOW)

    def test_a_currency_switch_aborts(self) -> None:
        self.repository.save_prices(PriceSeries.from_points(
            "LLY",
            [PricePoint(
                price_date=self.store.days[4],
                close=100.0,
                market_timestamp=datetime.combine(
                    self.store.days[4], datetime.min.time(), tzinfo=UTC),
                available_at=NOW,
                availability_source=AvailabilitySource.INFERRED,
                fetched_at=NOW,
                market="US",
                currency="CAD",
                available_price_type="close",
                used_price_type="close",
                source="fixture",
            )],
            market="US", currency="CAD", source="fixture"))
        with self.assertRaises(BacktestDataIntegrityError):
            self.access.prices(us_instrument(), NOW)

    def test_an_absent_ticker_is_missing_not_integrity(self) -> None:
        with self.assertRaises(BacktestDataMissingError):
            self.access.prices(us_instrument("NOPE"), NOW)


class FxIntegrityTests(IntegrityTestCase):
    def test_a_non_cny_fx_base_aborts(self) -> None:
        class UsdBaseRepository:
            def fx_as_of(self, code, as_of, start=None, end=None):
                return FxSeries.from_pairs(
                    "USD", [(self._day, 7.1)], source="stub",
                    base_currency="USD")

            _day = datetime(2025, 1, 6, tzinfo=UTC).date()

        access = DataAccess(UsdBaseRepository())
        with self.assertRaises(BacktestDataIntegrityError):
            access.fx("USD", NOW)

    def test_an_empty_currency_aborts(self) -> None:
        with self.assertRaises(BacktestDataIntegrityError):
            self.access.fx("", NOW)
        with self.assertRaises(BacktestDataIntegrityError):
            self.access.fx_rate("", NOW, self.store.days[0])

    def test_cny_needs_no_series(self) -> None:
        self.assertIsNone(self.access.fx("CNY", NOW))
        self.assertEqual(self.access.fx_rate("CNY", NOW, self.store.days[2]),
                         (1.0, self.store.days[2]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
