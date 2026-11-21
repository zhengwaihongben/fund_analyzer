# -*- coding: utf-8 -*-
"""Stage 2.5 integration: the estimator reads FX through the pipeline, PIT only.

These tests never touch the network: the pipeline's provider is either patched
out or its result is replaced by rows already written to a temporary database.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pandas as pd

from fa.analysis import nav_estimator
from fa.data.database.connection import DatabaseError
from fa.data.database.repository import Repository
from fa.data.fx_pipeline import FxUpdateOutcome
from fa.models.market_data import AvailabilitySource, FxSeries, PricePoint

UTC = timezone.utc
BEIJING = timezone(timedelta(hours=8))
START = date(2026, 11, 10)
END = date(2026, 11, 17)
# 2026-11-17 00:39 北京時間 = 2026-11-16 16:39Z; the bar of 2026-11-16 is only
# published at 2026-11-17 00:30 London (= 2026-11-17T00:30Z), i.e. after it.
AS_OF = datetime(2026, 11, 17, 0, 39, 20, tzinfo=BEIJING)


def _bar(day: date, rate: float, available_at: datetime) -> PricePoint:
    return PricePoint(
        price_date=day,
        close=rate,
        market_timestamp=datetime.combine(day - timedelta(days=1),
                                          datetime.min.time(), tzinfo=UTC),
        available_at=available_at,
        availability_source=AvailabilitySource.INFERRED,
        availability_note="test",
        fetched_at=datetime(2026, 11, 17, 12, tzinfo=UTC),
        market="FX",
        currency="CNY",
        available_price_type="fx_close",
        used_price_type="fx_close",
        source="test",
    )


class FxPipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        self.repo = Repository(self.tmp / "fx.db")
        self.addCleanup(self.repo.close)

    def _seed(self) -> None:
        """One published bar and one that is still in the future at AS_OF."""
        self.repo.save_fx(FxSeries.from_bars("USD", [
            _bar(date(2026, 11, 13), 7.10, datetime(2026, 11, 14, 0, 30, tzinfo=UTC)),
            _bar(date(2026, 11, 16), 7.20, datetime(2026, 11, 17, 0, 30, tzinfo=UTC)),
        ], source="test", base_currency="CNY"))

    def test_point_in_time_read_excludes_the_unpublished_bar(self):
        self._seed()
        with mock.patch.object(nav_estimator, "update_fx",
                               return_value=FxUpdateOutcome()):
            fx_map, source, warnings = nav_estimator._resolve_fx(
                {"USD"}, self.repo, start=START, end=END, as_of=AS_OF)

        self.assertEqual(source, "pipeline-pit")
        self.assertEqual(list(fx_map), ["USD"])
        days = [point.price_date for point in fx_map["USD"].points]
        self.assertEqual(days, [date(2026, 11, 13)])
        self.assertIn("USD", " ".join(warnings))

    def test_the_same_bar_becomes_visible_after_its_publication_time(self):
        self._seed()
        later = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)  # 17:00 北京
        with mock.patch.object(nav_estimator, "update_fx",
                               return_value=FxUpdateOutcome()):
            fx_map, _, _ = nav_estimator._resolve_fx(
                {"USD"}, self.repo, start=START, end=END, as_of=later)

        days = [point.price_date for point in fx_map["USD"].points]
        self.assertEqual(days, [date(2026, 11, 13), date(2026, 11, 16)])

    def test_pipeline_failure_never_falls_back_to_non_pit_rates(self):
        self._seed()
        with mock.patch.object(nav_estimator, "update_fx",
                               side_effect=RuntimeError("boom")), \
                mock.patch.object(nav_estimator, "fetch_fx_rates",
                                  side_effect=AssertionError("legacy path used")):
            fx_map, source, warnings = nav_estimator._resolve_fx(
                {"USD"}, self.repo, start=START, end=END, as_of=AS_OF)

        self.assertEqual(source, "pipeline-pit")
        self.assertEqual([p.price_date for p in fx_map["USD"].points],
                         [date(2026, 11, 13)])
        self.assertTrue(any("FX pipeline 失敗" in item for item in warnings))

    def test_database_error_falls_back_to_the_legacy_download(self):
        legacy = {"USD": pd.Series([7.00, 7.07], index=pd.to_datetime(
            ["2026-11-13", "2026-11-16"]))}
        with mock.patch.object(nav_estimator, "update_fx",
                               side_effect=DatabaseError("read only")), \
                mock.patch.object(nav_estimator, "fetch_fx_rates",
                                  return_value=legacy) as fetch:
            fx_map, source, warnings = nav_estimator._resolve_fx(
                {"USD"}, self.repo, start=START, end=END, as_of=AS_OF)

        self.assertEqual(source, "legacy")
        fetch.assert_called_once()
        self.assertAlmostEqual(fx_map["USD"].points[-1].value, 7.07)
        self.assertTrue(any("舊下載路徑" in item for item in warnings))

    def test_switch_off_keeps_the_old_download_path(self):
        legacy = {"USD": pd.Series([7.00, 7.07], index=pd.to_datetime(
            ["2026-11-13", "2026-11-16"]))}
        with mock.patch.object(nav_estimator, "FX_PIPELINE_ENABLED", False), \
                mock.patch.object(nav_estimator, "fetch_fx_rates",
                                  return_value=legacy), \
                mock.patch.object(nav_estimator, "update_fx",
                                  side_effect=AssertionError("pipeline used")):
            fx_map, source, _ = nav_estimator._resolve_fx(
                {"USD"}, self.repo, start=START, end=END, as_of=AS_OF)

        self.assertEqual(source, "legacy")
        self.assertAlmostEqual(fx_map["USD"].points[-1].value, 7.07)

    def test_cny_only_needs_no_series(self):
        fx_map, source, warnings = nav_estimator._resolve_fx(
            {"CNY"}, self.repo, start=START, end=END, as_of=AS_OF)
        self.assertEqual(fx_map, {})
        self.assertEqual(source, "none")
        self.assertEqual(warnings, [])

    def test_missing_database_keeps_the_legacy_path(self):
        legacy = {"USD": pd.Series([7.00], index=pd.to_datetime(["2026-11-13"]))}
        with mock.patch.object(nav_estimator, "fetch_fx_rates",
                               return_value=legacy):
            fx_map, source, warnings = nav_estimator._resolve_fx(
                {"USD"}, None, start=START, end=END, as_of=AS_OF)
        self.assertEqual(source, "legacy")
        self.assertTrue(any("沒有資料庫" in item for item in warnings))
        self.assertAlmostEqual(fx_map["USD"].points[-1].value, 7.00)


class EstimatorFxSourceTests(unittest.TestCase):
    """The estimate record says which FX path produced its numbers."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))

    def test_record_carries_the_pipeline_label(self):
        self.assertEqual(nav_estimator._pit_moment(None).tzinfo, UTC)
        self.assertEqual(
            nav_estimator._pit_moment(datetime(2026, 11, 17, 0, 39)),
            datetime(2026, 11, 17, 0, 39, tzinfo=UTC))
        self.assertEqual(nav_estimator._pit_moment(AS_OF), AS_OF)


if __name__ == "__main__":
    unittest.main()
