# -*- coding: utf-8 -*-
"""Coverage-focused tests for :mod:`fa.analysis.nav_estimator`.

``scripts/coverage_core.py`` reported the module at 86.6 %.  The uncovered
statements are the *diagnostic* paths of a NAV estimate: what the module does
when the database, the FX pipeline or the holdings snapshot misbehave.  Those
paths matter financially — a silent failure would turn "we could not check" into
"the number is fine" — so they are tested here with stubs instead of a network.

The full-estimate helpers mirror ``tests/test_nav_estimator_v2.py``; the unit
tests below them call the private helpers directly.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pandas as pd

from fa.analysis import nav_estimator
from fa.analysis.nav_estimator import (
    _base_nav_is_fresh,
    _db_prices,
    _fx_point,
    _fx_series,
    _iso,
    _market_data,
    _nav_on,
    _price_point,
    _resolve_fx,
    _save_prices,
    _save_to_database,
    estimate_qdii_nav,
    load_nav_frame,
)
from fa.data.fx_pipeline import FxUpdateOutcome
from fa.analysis.valuation_dates import order_trade_date
from fa.utils.calendar import previous_cn_trading_day
from fa.models.market_data import (
    AvailabilitySource,
    FxSeries,
    PricePoint,
    PriceSeries,
)

BASE_NAV = 1.2500
NAV_DATES = ["2025-06-05", "2025-06-06", "2025-06-09"]
# The CSV stops one session short on purpose: the estimate is only interesting
# when the target date has not been published yet.
CSV_DATES = ["2025-06-05", "2025-06-06"]


def _fx_point_row(day: date, rate: float) -> PricePoint:
    return PricePoint(
        price_date=day,
        close=rate,
        market_timestamp=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
        available_at=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        + timedelta(days=1),
        availability_source=AvailabilitySource.INFERRED,
        availability_note="test",
        fetched_at=datetime(2025, 6, 10, tzinfo=timezone.utc),
        market="FX",
        currency="CNY",
        available_price_type="fx_close",
        used_price_type="fx_close",
        source="test",
    )


def _usd_series() -> FxSeries:
    return FxSeries.from_bars(
        "USD",
        [_fx_point_row(date(2025, 6, 6), 7.00), _fx_point_row(date(2025, 6, 9), 7.07)],
        source="test",
        base_currency="CNY",
    )


class StubRepository:
    """The handful of Repository methods :func:`_resolve_fx` touches."""

    def __init__(self, *, series=None, fx_error=None, load_error=None) -> None:
        self.series = series if series is not None else _usd_series()
        self.fx_error = fx_error
        self.load_error = load_error

    def fx_as_of(self, code, moment, start=None, end=None):  # noqa: ANN001
        if self.fx_error is not None:
            raise self.fx_error
        return self.series

    def load_fx(self, code):  # noqa: ANN001
        if self.load_error is not None:
            raise self.load_error
        return self.series


class NavHelperTests(unittest.TestCase):
    def test_load_nav_frame_delegates_to_the_data_layer(self) -> None:
        sentinel = pd.DataFrame({"Date": [], "NAV": []})
        with mock.patch.object(nav_estimator, "load_nav_csv", return_value=sentinel) as loader:
            self.assertIs(load_nav_frame("000369", Path("x")), sentinel)
        loader.assert_called_once_with("000369", Path("x"))

    def test_nav_on_returns_none_for_an_empty_frame(self) -> None:
        self.assertIsNone(_nav_on(pd.DataFrame(columns=["Date", "NAV"]), date(2025, 6, 9)))

    def test_nav_on_returns_none_before_the_first_date(self) -> None:
        frame = pd.DataFrame({"Date": pd.to_datetime(["2025-06-05"]), "NAV": [1.2]})
        self.assertIsNone(_nav_on(frame, date(2025, 6, 1)))

    def test_nav_on_returns_the_last_value_on_or_before_the_day(self) -> None:
        frame = pd.DataFrame({"Date": pd.to_datetime(NAV_DATES), "NAV": [1.1, 1.2, BASE_NAV]})
        self.assertEqual(_nav_on(frame, date(2025, 6, 8)), 1.2)

    def test_fx_series_converts_pandas_series(self) -> None:
        rates = {"USD": pd.Series([7.0, 7.07],
                                  index=pd.to_datetime(["2025-06-06", "2025-06-09"]))}
        mapped = _fx_series(rates)
        self.assertIn("USD", mapped)
        self.assertEqual(len(mapped["USD"].points), 2)

    def test_iso_handles_none_dates_and_strings(self) -> None:
        self.assertIsNone(_iso(None))
        self.assertEqual(_iso(date(2025, 6, 9)), "2025-06-09")
        self.assertEqual(_iso(datetime(2025, 6, 9, 12, 0)), "2025-06-09")
        self.assertEqual(_iso("2025-06-09T00:00:00"), "2025-06-09")

    def test_base_nav_is_not_fresh_without_rows(self) -> None:
        self.assertFalse(_base_nav_is_fresh(pd.DataFrame(columns=["Date", "NAV"]),
                                            date(2025, 6, 6), date(2025, 6, 9)))

    def test_base_nav_is_not_fresh_when_nothing_precedes_the_target(self) -> None:
        frame = pd.DataFrame({"Date": pd.to_datetime(["2025-07-01"]), "NAV": [1.2]})
        self.assertFalse(_base_nav_is_fresh(frame, date(2025, 6, 6), date(2025, 6, 9)))


class ResolveFxTests(unittest.TestCase):
    def test_no_currency_needs_no_series(self) -> None:
        series, source, warnings = _resolve_fx(
            ["CNY", ""], StubRepository(), start=date(2025, 6, 1),
            end=date(2025, 6, 9), as_of=None,
        )
        self.assertEqual((series, source, warnings), ({}, "none", []))

    def test_skipped_currencies_are_reported(self) -> None:
        outcome = FxUpdateOutcome(skipped={"USD": "timeout"})
        with mock.patch.object(nav_estimator, "update_fx", return_value=outcome):
            _, source, warnings = _resolve_fx(
                ["USD"], StubRepository(), start=date(2025, 6, 1),
                end=date(2025, 6, 9), as_of=datetime(2025, 6, 10, tzinfo=timezone.utc),
            )
        self.assertEqual(source, "pipeline-pit")
        self.assertIn("USD: timeout", warnings)

    def test_a_pit_read_failure_only_drops_that_currency(self) -> None:
        repo = StubRepository(fx_error=RuntimeError("boom"))
        with mock.patch.object(nav_estimator, "update_fx", return_value=FxUpdateOutcome()):
            series, source, warnings = _resolve_fx(
                ["USD"], repo, start=date(2025, 6, 1), end=date(2025, 6, 9),
                as_of=datetime(2025, 6, 10, tzinfo=timezone.utc),
            )
        self.assertEqual(series, {})
        self.assertEqual(source, "pipeline-pit")
        self.assertTrue(any("PIT 匯率讀取失敗" in warning for warning in warnings))

    def test_a_diagnostic_read_failure_is_not_fatal(self) -> None:
        repo = StubRepository(load_error=RuntimeError("read failed"))
        with mock.patch.object(nav_estimator, "update_fx", return_value=FxUpdateOutcome()):
            series, _, _ = _resolve_fx(
                ["USD"], repo, start=date(2025, 6, 1), end=date(2025, 6, 9),
                as_of=datetime(2025, 6, 10, tzinfo=timezone.utc),
            )
        self.assertIn("USD", series)

    def test_an_unpublished_currency_is_reported(self) -> None:
        repo = StubRepository(series=FxSeries(currency="USD", points=[]))
        with mock.patch.object(nav_estimator, "update_fx", return_value=FxUpdateOutcome()):
            series, _, warnings = _resolve_fx(
                ["USD"], repo, start=date(2025, 6, 1), end=date(2025, 6, 9),
                as_of=datetime(2025, 6, 10, tzinfo=timezone.utc),
            )
        self.assertEqual(series, {})
        self.assertTrue(any("尚無已公布匯率" in warning for warning in warnings))

    def test_a_database_write_failure_falls_back_to_the_legacy_download(self) -> None:
        from fa.data.database.connection import DatabaseError

        legacy = {"USD": pd.Series([7.0], index=pd.to_datetime(["2025-06-06"]))}
        with mock.patch.object(nav_estimator, "update_fx", side_effect=DatabaseError("locked")):
            with mock.patch.object(nav_estimator, "fetch_fx_rates", return_value=legacy):
                _, source, warnings = _resolve_fx(
                    ["USD"], StubRepository(), start=date(2025, 6, 1),
                    end=date(2025, 6, 9), as_of=None,
                )
        self.assertEqual(source, "legacy")
        self.assertTrue(any("改用舊下載路徑" in warning for warning in warnings))

    def test_an_unexpected_pipeline_failure_is_warned_and_pit_still_used(self) -> None:
        with mock.patch.object(nav_estimator, "update_fx", side_effect=RuntimeError("kaboom")):
            series, source, warnings = _resolve_fx(
                ["USD"], StubRepository(), start=date(2025, 6, 1),
                end=date(2025, 6, 9), as_of=datetime(2025, 6, 10, tzinfo=timezone.utc),
            )
        self.assertEqual(source, "pipeline-pit")
        self.assertIn("USD", series)
        self.assertTrue(any("FX pipeline 失敗" in warning for warning in warnings))


class PriceHelperTests(unittest.TestCase):
    def test_db_prices_returns_none_without_a_repository(self) -> None:
        self.assertIsNone(_db_prices(None, "LLY", date(2025, 6, 1), date(2025, 6, 9)))

    def test_db_prices_swallows_a_database_error(self) -> None:
        class Boom:
            def load_prices(self, ticker, start=None, end=None):  # noqa: ANN001
                raise RuntimeError("db down")

        self.assertIsNone(
            _db_prices(Boom(), "LLY", date(2025, 6, 1), date(2025, 6, 9)))

    def test_db_prices_treats_an_empty_series_as_absent(self) -> None:
        class Empty:
            def load_prices(self, ticker, start=None, end=None):  # noqa: ANN001
                return PriceSeries.from_points("LLY", [])

        self.assertIsNone(
            _db_prices(Empty(), "LLY", date(2025, 6, 1), date(2025, 6, 9)))

    def test_save_prices_swallows_a_database_error(self) -> None:
        class Boom:
            def save_prices(self, series):  # noqa: ANN001
                raise RuntimeError("db down")

        _save_prices(Boom(), PriceSeries.from_points("LLY", []))
        _save_prices(None, None)

    def test_price_point_returns_none_for_a_missing_or_broken_series(self) -> None:
        self.assertIsNone(_price_point(None, date(2025, 6, 9)))

        class Boom:
            def point_on_or_before(self, day):  # noqa: ANN001
                raise RuntimeError("broken")

        self.assertIsNone(_price_point(Boom(), date(2025, 6, 9)))

    def test_market_data_prefers_a_database_cache(self) -> None:
        point = PricePoint(
            price_date=date(2025, 6, 6), close=100.0, market="US", currency="USD",
            available_price_type="close+adj_close", used_price_type="close+adj_close",
        )

        class Cached:
            def load_prices(self, ticker, start=None, end=None):  # noqa: ANN001
                return PriceSeries.from_points(ticker, [point], market="US", currency="USD")

        series_map, hits = _market_data(
            ["LLY"], date(2025, 6, 1), date(2025, 6, 9), Cached())
        self.assertEqual(series_map["LLY"].currency, "USD")
        self.assertEqual(hits["LLY"]["market"], "US")

    def test_fx_point_returns_empty_pairs(self) -> None:
        self.assertEqual(_fx_point(None, date(2025, 6, 9)), (None, None))

        class Boom:
            def rate_on_or_before(self, day):  # noqa: ANN001
                raise RuntimeError("broken")

        self.assertEqual(_fx_point(Boom(), date(2025, 6, 9)), (None, None))


class FullEstimateCoverageTests(unittest.TestCase):
    """Full estimates with stubbed data sources; no network or real database."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.save = self.tmp / "000369"
        self.save.mkdir(parents=True)
        pd.DataFrame({
            "Date": pd.to_datetime(CSV_DATES),
            "NAV": [1.2400, BASE_NAV],
        }).to_csv(self.save / "000369_daily_nav.csv", index=False)
        self.holdings = pd.DataFrame([
            {"ticker": "LLY", "name": "礼来", "weight": 0.5, "currency_hint": "USD",
             "market": "US", "quarter": "2025Q1", "holding_date": "2025-03-31",
             "disclosure_date": "2025-04-21", "asset_type": "equity"},
        ])
        self.prices = {
            "LLY": {
                "status": "ok", "symbol": "LLY", "currency": "USD",
                "exchange": "NYQ",
                "prices": pd.DataFrame({
                    "Date": pd.to_datetime(["2025-06-06", "2025-06-09"]),
                    "Price": [100.0, 110.0],
                }),
                "tried": ["LLY"], "error": None,
            },
        }

    def _estimate(self, *, portfolio=None, as_of="default", selection=None):
        base = {
            "holdings": self.holdings, "quarters": ["2025Q1"], "quarter": "2025Q1",
            "holding_date": "2025-03-31", "disclosure_date": "2025-04-21",
            "source": "test", "cache_hit": False, "holdings_scope": "top10",
            "history_available": False,
        }
        if portfolio:
            base.update(portfolio)
        with mock.patch.multiple(
            "fa.analysis.nav_estimator",
            fetch_qdii_holdings=mock.DEFAULT,
            fetch_holding_prices=mock.DEFAULT,
            fetch_fx_rates=mock.DEFAULT,
            update_fx=mock.DEFAULT,
        ) as patched:
            patched["fetch_qdii_holdings"].return_value = base
            patched["fetch_holding_prices"].return_value = self.prices
            patched["fetch_fx_rates"].return_value = {
                "USD": pd.Series([7.0, 7.07],
                                 index=pd.to_datetime(["2025-06-06", "2025-06-09"]))}
            patched["update_fx"].return_value = FxUpdateOutcome()
            if selection is not None:
                patched_selector = mock.patch.object(
                    nav_estimator, "select_point_in_time", return_value=selection)
                patched_selector.start()
                self.addCleanup(patched_selector.stop)
            kwargs = {}
            if as_of == "default":
                kwargs["as_of"] = datetime(2025, 6, 10, 2, 0, tzinfo=timezone.utc)
            elif as_of is not None:
                kwargs["as_of"] = as_of
            return estimate_qdii_nav("000369", self.save, **kwargs)

    def test_a_stale_snapshot_is_named_in_the_warnings(self) -> None:
        record = self._estimate(portfolio={"source": "cache-stale"})
        self.assertTrue(any("較舊快照" in warning for warning in record["warnings"]))

    def test_an_old_disclosure_is_flagged(self) -> None:
        stale = self.holdings.copy()
        stale["holding_date"] = "2022-03-31"
        stale["disclosure_date"] = "2022-04-21"
        record = self._estimate(portfolio={"holdings": stale})
        self.assertTrue(any("天未更新" in warning for warning in record["warnings"]))

    def test_an_iso_string_as_of_is_accepted(self) -> None:
        record = self._estimate(as_of="2025-06-10T02:00:00+00:00")
        self.assertEqual(record["target_nav_date"], "2025-06-09")

    def test_an_unparsable_as_of_falls_back_to_now(self) -> None:
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ANN001, ANN206
                return datetime(2025, 6, 10, 2, 0, tzinfo=tz)

        with mock.patch.object(nav_estimator, "datetime", FixedDatetime):
            record = self._estimate(as_of="definitely-not-a-date")
        trade_day, _ = order_trade_date(None)
        self.assertEqual(record["target_nav_date"],
                         previous_cn_trading_day(trade_day).isoformat())

    def test_save_to_database_returns_none_without_repository_or_target(self) -> None:
        self.assertIsNone(_save_to_database(None, {}, pd.DataFrame()))
        self.assertIsNone(_save_to_database(object(), {}, pd.DataFrame()))

    def test_save_to_database_swallows_a_repository_failure(self) -> None:
        class Boom:
            def save_official_nav(self, *args, **kwargs):  # noqa: ANN002, ANN003
                raise RuntimeError("db down")

            def save_estimation(self, *args, **kwargs):  # noqa: ANN002, ANN003
                raise RuntimeError("db down")

        record = {
            "fund_code": "000369", "target_nav_date": "2025-06-09",
            "trade_date": "2025-06-10", "base_nav_date": "2025-06-06",
            "as_of_beijing": "not-a-date", "status": "partial",
        }
        self.assertIsNone(_save_to_database(Boom(), record, pd.DataFrame({"Date": [], "NAV": []})))


if __name__ == "__main__":
    unittest.main()
