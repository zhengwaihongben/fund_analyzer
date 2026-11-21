# -*- coding: utf-8 -*-
"""Coverage-focused tests for the return calculators.

``scripts/coverage_core.py`` reported ``fa/analysis/returns/base.py`` at 85.5 %
and ``fa/analysis/returns/fx.py`` at 66.7 %.  The paths below were the missing
ones, and each has a financial meaning:

* ``series_from_frame`` is the only producer of :class:`PriceSeries` from the
  vendor's ``DataFrame``; a row it silently accepts or rejects changes every
  downstream return, so the skip branches matter.
* ``convert_to_cny`` must distinguish *missing* FX from *excluded* FX and must
  never divide by a zero base rate.
* ``cny_return`` is the composition identity ``1 + r_CNY = (1 + r_local)(1 + r_fx)``.
* ``describe_fx`` writes the log/GUI text for the FX leg.

Nothing here changes production behaviour: the tests call the public functions
with inputs the existing suite did not exercise.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import pandas as pd

from fa.analysis.returns.base import (
    FxOutcome,
    _available_price_type,
    cny_return,
    convert_to_cny,
    priced_return,
    series_from_frame,
)
from fa.analysis.returns.fx import compute_fx_return, describe_fx
from fa.models.asset import Asset, AssetType
from fa.models.market_data import AvailabilitySource, FxSeries, PricePoint, PriceSeries
from fa.utils.calendars import calendar_for

US = calendar_for("US")


def _day(offset: int) -> date:
    return date(2026, 1, 5) + timedelta(days=offset)


def _us_session(offset: int) -> date:
    """A weekday, so the alignment calendar counts it as a session."""
    day = _day(offset)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return day


def _point(day: date, close: float, adjusted: float | None = None) -> PricePoint:
    return PricePoint(
        price_date=day,
        close=close,
        adj_close=adjusted,
        market="US",
        currency="USD",
        available_price_type="close+adj_close" if adjusted is not None else "close",
    )


class AvailablePriceTypeTests(unittest.TestCase):
    def test_close_only_frame_reports_close(self) -> None:
        frame = pd.DataFrame({"Date": [_day(0)], "Close": [100.0]})
        self.assertEqual(_available_price_type(frame), "close")

    def test_adjusted_only_frame_reports_adjusted_close(self) -> None:
        frame = pd.DataFrame({"Date": [_day(0)], "AdjClose": [100.0]})
        self.assertEqual(_available_price_type(frame), "adjusted_close")

    def test_both_columns_report_the_pair(self) -> None:
        frame = pd.DataFrame({"Date": [_day(0)], "Close": [100.0], "AdjClose": [99.0]})
        self.assertEqual(_available_price_type(frame), "close+adj_close")

    def test_a_frame_without_price_columns_reports_empty(self) -> None:
        frame = pd.DataFrame({"Date": [_day(0)], "Volume": [10]})
        self.assertEqual(_available_price_type(frame), "")


class SeriesFromFrameTests(unittest.TestCase):
    def test_an_empty_frame_produces_an_empty_series(self) -> None:
        series = series_from_frame(
            pd.DataFrame(columns=["Date", "Close"]), ticker="LLY", market="US",
            currency="USD",
        )
        self.assertEqual(len(series.points), 0)
        self.assertEqual(series.ticker, "LLY")

    def test_a_columnless_frame_produces_an_empty_series(self) -> None:
        series = series_from_frame(pd.DataFrame(), ticker="LLY")
        self.assertEqual(len(series.points), 0)

    def test_a_plain_row_list_is_accepted(self) -> None:
        rows = [
            {"Date": _day(0), "Close": 100.0, "AdjClose": 99.5},
            {"Date": _day(1), "Close": 101.0},
        ]
        series = series_from_frame(rows, ticker="LLY", currency="USD")
        self.assertEqual([point.close for point in series.points], [100.0, 101.0])
        # A row list carries no column metadata, so the series type is empty and
        # PriceSeries derives it per point from the adj_close presence.
        self.assertEqual(
            [point.available_price_type for point in series.points],
            ["close+adj_close", "close"],
        )

    def test_rows_without_a_date_are_skipped(self) -> None:
        rows = [{"Close": 100.0}, {"Date": _day(0), "Close": 101.0}]
        series = series_from_frame(rows, ticker="LLY")
        self.assertEqual([point.close for point in series.points], [101.0])

    def test_rows_with_an_unparsable_date_are_skipped(self) -> None:
        rows = [{"Date": "not-a-date", "Close": 100.0}, {"Date": _day(0), "Close": 101.0}]
        series = series_from_frame(rows, ticker="LLY")
        self.assertEqual([point.close for point in series.points], [101.0])

    def test_a_row_without_any_price_is_skipped(self) -> None:
        rows = [{"Date": _day(0)}, {"Date": _day(1), "Close": 101.0}]
        series = series_from_frame(rows, ticker="LLY")
        self.assertEqual([point.close for point in series.points], [101.0])

    def test_a_nan_close_falls_back_to_the_adjusted_price(self) -> None:
        frame = pd.DataFrame(
            {"Date": [_day(0)], "Close": [float("nan")], "AdjClose": [98.0]}
        )
        series = series_from_frame(frame, ticker="LLY", currency="USD")
        self.assertEqual([point.close for point in series.points], [98.0])
        self.assertEqual([point.adj_close for point in series.points], [98.0])

    def test_an_explicit_price_type_overrides_the_derived_one(self) -> None:
        frame = pd.DataFrame({"Date": [_day(0)], "Close": [100.0]})
        series = series_from_frame(frame, ticker="LLY", price_type="adjusted_close")
        self.assertEqual(series.points[0].available_price_type, "adjusted_close")


class FxOutcomeTests(unittest.TestCase):
    def test_ok_is_true_only_for_usable_statuses(self) -> None:
        from fa.analysis.returns.base import ReturnStatus

        self.assertTrue(FxOutcome(fx_return=0.01).ok)
        self.assertFalse(FxOutcome(status=ReturnStatus.MISSING_FX).ok)

    def test_cny_return_composes_the_two_legs(self) -> None:
        self.assertAlmostEqual(cny_return(0.10, 0.05), 0.155, places=12)

    def test_describe_fx_reports_the_status_when_there_is_no_number(self) -> None:
        from fa.analysis.returns.base import ReturnStatus

        self.assertEqual(
            describe_fx(FxOutcome(status=ReturnStatus.MISSING_FX)), "missing-fx"
        )

    def test_describe_fx_prints_the_percentage(self) -> None:
        self.assertEqual(describe_fx(FxOutcome(fx_return=0.0123)), "+1.23%")


class ConvertToCnyPathTests(unittest.TestCase):
    def _series(self, pairs):
        return FxSeries.from_pairs("USD", pairs, source="unit-test")

    def test_a_missing_base_rate_is_reported_without_inventing_one(self) -> None:
        fx = self._series([(_us_session(5), 7.0)])
        outcome = convert_to_cny("USD", fx, _us_session(0), _us_session(6), calendar=US)
        from fa.analysis.returns.base import ReturnStatus

        self.assertEqual(outcome.status, ReturnStatus.MISSING_FX)
        self.assertIsNone(outcome.fx_return)

    def test_a_stale_target_rate_is_excluded_rather_than_carried(self) -> None:
        fx = self._series([(_us_session(0), 7.0)])
        outcome = convert_to_cny("USD", fx, _us_session(0), _us_session(5), calendar=US)
        from fa.analysis.returns.base import ReturnStatus

        self.assertEqual(outcome.status, ReturnStatus.MISSING_FX)
        self.assertFalse(outcome.ok)

    def test_a_too_stale_rate_is_excluded(self) -> None:
        fx = self._series([(_us_session(0), 7.0)])
        outcome = convert_to_cny("USD", fx, _us_session(6), _us_session(7), calendar=US)
        from fa.analysis.returns.base import ReturnStatus

        self.assertEqual(outcome.status, ReturnStatus.MISSING_FX)
        self.assertTrue(any("過期" in warning for warning in outcome.warnings))

    def test_a_slightly_stale_base_rate_is_flagged_not_dropped(self) -> None:
        fx = self._series([(_us_session(0), 7.0), (_us_session(3), 7.2)])
        outcome = convert_to_cny("USD", fx, _us_session(1), _us_session(3), calendar=US)
        from fa.analysis.returns.base import ReturnStatus

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.status, ReturnStatus.STALE_BASE_FX)
        self.assertAlmostEqual(outcome.fx_return, 7.2 / 7.0 - 1.0, places=12)


class PricedReturnPathTests(unittest.TestCase):
    def _asset(self) -> Asset:
        return Asset(
            asset_id="LLY", name="Eli Lilly", asset_type=AssetType.EQUITY,
            weight=1.0, currency="USD", market="US",
        )

    def _series(self, points) -> PriceSeries:
        return PriceSeries.from_points("LLY", points, market="US", currency="USD")

    def test_a_missing_target_price_is_reported(self) -> None:
        # The only observation is later than the target date, so no price is
        # available for the target at all.
        prices = self._series([_point(_us_session(5), 100.0, 100.0)])
        result = priced_return(
            self._asset(), base_date=_us_session(5), target_date=_us_session(0),
            prices=prices, fx=None, calendar=US,
        )
        from fa.analysis.returns.base import ReturnStatus

        self.assertEqual(result.status, ReturnStatus.MISSING_TARGET_PRICE)

    def test_a_stale_base_price_is_flagged(self) -> None:
        prices = self._series([
            _point(_us_session(0), 100.0, 100.0),
            _point(_us_session(3), 110.0, 110.0),
        ])
        fx = FxSeries.from_pairs(
            "USD",
            [(_us_session(0), 7.0), (_us_session(3), 7.0)],
            source="unit-test",
        )
        result = priced_return(
            self._asset(), base_date=_us_session(1), target_date=_us_session(3),
            prices=prices, fx=fx, calendar=US,
        )
        from fa.analysis.returns.base import ReturnStatus

        self.assertEqual(result.status, ReturnStatus.STALE_BASE_PRICE)
        self.assertTrue(result.status.is_usable)

    def test_a_raw_close_can_be_refused(self) -> None:
        prices = self._series([
            _point(_us_session(0), 100.0),
            _point(_us_session(1), 101.0),
        ])
        result = priced_return(
            self._asset(), base_date=_us_session(0), target_date=_us_session(1),
            prices=prices, fx=None, calendar=US, allow_raw_close=False,
        )
        from fa.analysis.returns.base import ReturnStatus

        self.assertEqual(result.status, ReturnStatus.UNSUPPORTED_ASSET)

    def test_a_zero_base_value_is_refused_rather_than_dividing(self) -> None:
        class ZeroPoint:
            close = 0.0
            adj_close = None
            value = 0.0
            price_date = _us_session(0)
            used_price_type = ""

        class ZeroSeries:
            ticker = "LLY"
            market = "US"
            currency = "USD"

            def price_on_or_before(self, day):  # noqa: ANN001 - alignment lookup
                return (0.0, _us_session(0))

            def point_on_or_before(self, day):  # noqa: ANN001 - point lookup
                return ZeroPoint()

            def __len__(self) -> int:
                return 2

            def __iter__(self):
                return iter([ZeroPoint(), ZeroPoint()])

        result = priced_return(
            self._asset(), base_date=_us_session(0), target_date=_us_session(1),
            prices=ZeroSeries(), fx=None, calendar=US,
        )
        from fa.analysis.returns.base import ReturnStatus

        self.assertEqual(result.status, ReturnStatus.MISSING_BASE_PRICE)


if __name__ == "__main__":
    unittest.main()
