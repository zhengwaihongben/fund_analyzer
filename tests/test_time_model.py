# -*- coding: utf-8 -*-
"""V2.3.1 Stage 1 — the time model.

Acceptance class A (time integrity):

* every timestamp that enters a model must be timezone-aware;
* ``market_timestamp`` / ``available_at`` / ``fetched_at`` are three different
  questions and must not be collapsed into one field;
* a price can only be used at a historical instant when its ``available_at``
  is known and marked with a provenance other than ``unknown``;
* the V2.2 fields (``price_timestamp`` / ``retrieved_at``) stay readable so old
  call sites and old snapshots keep working.
"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from fa.models.market_data import (
    AvailabilitySource,
    FxSeries,
    MarketDataError,
    PricePoint,
    PriceSeries,
)
from fa.utils.calendars import calendar_for

UTC = timezone.utc
SHANGHAI = timezone(timedelta(hours=8))


def _p(**kwargs) -> PricePoint:
    base = {"price_date": date(2026, 11, 13), "close": 100.0}
    base.update(kwargs)
    return PricePoint(**base)


class AvailabilitySourceTests(unittest.TestCase):
    def test_parse_accepts_enum_and_text(self):
        self.assertIs(AvailabilitySource.parse(AvailabilitySource.INFERRED),
                      AvailabilitySource.INFERRED)
        self.assertIs(AvailabilitySource.parse("vendor_timestamp"),
                      AvailabilitySource.VENDOR_TIMESTAMP)
        self.assertIs(AvailabilitySource.parse(" Vendor-Timestamp "),
                      AvailabilitySource.VENDOR_TIMESTAMP)
        self.assertIs(AvailabilitySource.parse("INFERRED"), AvailabilitySource.INFERRED)

    def test_unknown_is_the_default_and_parse_fallback(self):
        self.assertIs(AvailabilitySource.parse(None), AvailabilitySource.UNKNOWN)
        self.assertIs(AvailabilitySource.parse("not-a-source"), AvailabilitySource.UNKNOWN)
        self.assertIs(_p().availability_source, AvailabilitySource.UNKNOWN)

    def test_pit_usability_requires_known_availability(self):
        self.assertFalse(AvailabilitySource.UNKNOWN.is_pit_usable)
        self.assertTrue(AvailabilitySource.OBSERVED.is_pit_usable)
        # A known provenance is necessary but not sufficient: without an actual
        # ``available_at`` instant the point still cannot be used for PIT.
        point = _p(
            available_at=datetime(2026, 11, 14, 1, 0, tzinfo=UTC),
            availability_source=AvailabilitySource.INFERRED,
        )
        self.assertTrue(point.is_pit_usable)
        self.assertFalse(_p().is_pit_usable)

    def test_available_at_without_provenance_is_rejected(self):
        with self.assertRaises(MarketDataError):
            _p(available_at=datetime(2026, 11, 14, 1, 0, tzinfo=UTC))

    def test_available_at_accepts_the_unknown_source_only_when_absent(self):
        point = _p(availability_source=AvailabilitySource.UNKNOWN)
        self.assertIsNone(point.available_at)
        self.assertFalse(point.is_pit_usable)

    def test_source_without_available_at_is_rejected(self):
        # 反向不變條件：沒有可用時間，就沒有可宣稱的來源。
        for source in (
            AvailabilitySource.OBSERVED,
            AvailabilitySource.VENDOR_TIMESTAMP,
            AvailabilitySource.INFERRED,
        ):
            with self.subTest(source=source):
                with self.assertRaises(MarketDataError):
                    _p(availability_source=source)


class TimezoneTests(unittest.TestCase):
    def test_naive_market_timestamp_is_rejected(self):
        with self.assertRaises(MarketDataError):
            _p(market_timestamp=datetime(2026, 11, 13, 21, 0))

    def test_naive_available_at_is_rejected(self):
        with self.assertRaises(MarketDataError):
            _p(
                available_at=datetime(2026, 11, 14, 1, 0),
                availability_source=AvailabilitySource.INFERRED,
            )

    def test_naive_fetched_at_is_rejected(self):
        with self.assertRaises(MarketDataError):
            _p(fetched_at=datetime(2026, 11, 14, 1, 0))

    def test_aware_timestamps_are_normalised_to_utc(self):
        moment = datetime(2026, 11, 14, 9, 0, tzinfo=SHANGHAI)
        point = _p(market_timestamp=moment, fetched_at=moment)
        # 同一個時刻，但儲存一律是 UTC。
        self.assertEqual(point.market_timestamp, moment)
        self.assertEqual(point.market_timestamp.utcoffset(), timedelta(0))
        self.assertEqual(point.fetched_at.utcoffset(), timedelta(0))

    def test_utc_instants_are_equivalent_across_offsets(self):
        shanghai = datetime(2026, 11, 14, 9, 0, tzinfo=SHANGHAI)
        utc = datetime(2026, 11, 14, 1, 0, tzinfo=UTC)
        self.assertEqual(shanghai, utc)


class LegacyAliasTests(unittest.TestCase):
    def test_price_timestamp_is_an_alias_of_market_timestamp(self):
        moment = datetime(2026, 11, 13, 21, 0, tzinfo=UTC)
        point = _p(market_timestamp=moment)
        self.assertEqual(point.price_timestamp, moment)
        self.assertIs(point.price_timestamp, point.market_timestamp)
        point.price_timestamp = None
        self.assertIsNone(point.market_timestamp)

    def test_retrieved_at_is_an_alias_of_fetched_at(self):
        moment = datetime(2026, 11, 14, 2, 0, tzinfo=UTC)
        point = _p(fetched_at=moment)
        self.assertEqual(point.retrieved_at, moment)
        self.assertIs(point.retrieved_at, point.fetched_at)
        point.retrieved_at = None
        self.assertIsNone(point.fetched_at)

    def test_fetched_at_defaults_to_now(self):
        point = _p()
        self.assertIsNotNone(point.fetched_at)
        self.assertIsNotNone(point.fetched_at.tzinfo)

    def test_dict_round_trip_keeps_both_names(self):
        moment = datetime(2026, 11, 13, 21, 0, tzinfo=UTC)
        available = datetime(2026, 11, 14, 1, 30, tzinfo=UTC)
        point = _p(
            market_timestamp=moment,
            available_at=available,
            availability_source=AvailabilitySource.INFERRED,
            availability_note="market close + 30 分鐘",
            dividend=0.25,
            split=1.0,
        )
        payload = point.to_dict()
        self.assertEqual(payload["market_timestamp"], "2026-11-13T21:00:00Z")
        self.assertEqual(payload["price_timestamp"], "2026-11-13T21:00:00Z")
        self.assertEqual(payload["availability_source"], "inferred")
        back = PricePoint.from_dict(payload)
        self.assertEqual(back.market_timestamp, moment)
        self.assertEqual(back.available_at, available)
        self.assertEqual(back.availability_note, "market close + 30 分鐘")
        self.assertEqual(back.dividend, 0.25)
        self.assertEqual(back.split, 1.0)

    def test_from_dict_reads_the_v22_field_names(self):
        payload = {
            "price_date": "2026-11-13",
            "close": 100.0,
            "price_timestamp": "2026-11-13T21:00:00+00:00",
            "retrieved_at": "2026-11-14T02:00:00+00:00",
        }
        point = PricePoint.from_dict(payload)
        self.assertEqual(point.market_timestamp, datetime(2026, 11, 13, 21, 0, tzinfo=UTC))
        self.assertEqual(point.fetched_at, datetime(2026, 11, 14, 2, 0, tzinfo=UTC))

    def test_availability_source_survives_the_dict_round_trip(self):
        point = _p(
            available_at=datetime(2026, 11, 14, 1, 0, tzinfo=UTC),
            availability_source="vendor_timestamp",
        )
        self.assertIs(PricePoint.from_dict(point.to_dict()).availability_source,
                      AvailabilitySource.VENDOR_TIMESTAMP)

    def test_legacy_keyword_names_still_construct_a_point(self):
        # V2.2 的呼叫端用 price_timestamp= / retrieved_at= 建構 PricePoint；
        # 這是公開介面，不能默默消失。
        market = datetime(2026, 11, 13, 21, 0, tzinfo=UTC)
        fetched = datetime(2026, 11, 14, 2, 0, tzinfo=UTC)
        point = PricePoint(
            price_date=date(2026, 11, 13),
            close=100.0,
            price_timestamp=market,
            retrieved_at=fetched,
        )
        self.assertEqual(point.market_timestamp, market)
        self.assertEqual(point.fetched_at, fetched)

    def test_new_and_legacy_names_together_are_rejected(self):
        moment = datetime(2026, 11, 13, 21, 0, tzinfo=UTC)
        with self.assertRaises(MarketDataError):
            PricePoint(
                price_date=date(2026, 11, 13),
                close=100.0,
                market_timestamp=moment,
                price_timestamp=moment,
            )
        with self.assertRaises(MarketDataError):
            PricePoint(
                price_date=date(2026, 11, 13),
                close=100.0,
                fetched_at=moment,
                retrieved_at=moment,
            )

    def test_legacy_setter_cannot_bypass_timezone_validation(self):
        point = _p()
        with self.assertRaises(MarketDataError):
            point.price_timestamp = datetime(2026, 11, 13, 21, 0)
        with self.assertRaises(MarketDataError):
            point.retrieved_at = datetime(2026, 11, 13, 21, 0)


    def test_with_used_price_type_preserves_every_field(self):
        # dataclasses.replace() 會重新呼叫自訂 __init__，舊別名與時間欄位都不能掉。
        moment = datetime(2026, 11, 13, 21, 0, tzinfo=UTC)
        available = datetime(2026, 11, 14, 1, 30, tzinfo=UTC)
        point = _p(
            market_timestamp=moment,
            available_at=available,
            availability_source=AvailabilitySource.INFERRED,
            availability_note="market close + 30 分鐘",
            dividend=0.2,
            split=2.0,
            market="US",
            currency="USD",
            available_price_type="adj_close",
            source="yahoo",
        )
        updated = point.with_used_price_type("adj_close")
        self.assertEqual(updated.market_timestamp, point.market_timestamp)
        self.assertEqual(updated.available_at, point.available_at)
        self.assertEqual(updated.fetched_at, point.fetched_at)
        self.assertEqual(updated.availability_source, AvailabilitySource.INFERRED)
        self.assertEqual(updated.availability_note, point.availability_note)
        self.assertEqual(updated.dividend, point.dividend)
        self.assertEqual(updated.split, point.split)
        self.assertEqual(updated.market, point.market)
        self.assertEqual(updated.currency, point.currency)
        self.assertEqual(updated.source, point.source)
        self.assertEqual(updated.used_price_type, "adj_close")
        # 舊別名也要看得到同一個時刻。
        self.assertEqual(updated.price_timestamp, point.market_timestamp)
        self.assertEqual(updated.retrieved_at, point.fetched_at)


class UtcSerialisationTests(unittest.TestCase):
    """儲存一律 UTC，序列化一律以 ``Z`` 結尾（V2.3.1 §2）。"""

    def test_offsets_collapse_to_the_same_utc_instant(self):
        east = datetime(2026, 11, 14, 5, 0, tzinfo=SHANGHAI)
        west = datetime(2026, 11, 13, 16, 0, tzinfo=timezone(timedelta(hours=-5)))
        first = _p(market_timestamp=east)
        second = _p(market_timestamp=west)
        self.assertEqual(first.market_timestamp, second.market_timestamp)
        self.assertEqual(first.market_timestamp.utcoffset(), timedelta(0))

    def test_to_dict_serialises_with_a_z_suffix(self):
        point = _p(
            market_timestamp=datetime(2026, 11, 14, 5, 0, tzinfo=SHANGHAI),
            available_at=datetime(2026, 11, 14, 1, 30, tzinfo=UTC),
            availability_source=AvailabilitySource.INFERRED,
        )
        payload = point.to_dict()
        self.assertTrue(payload["market_timestamp"].endswith("Z"))
        self.assertTrue(payload["available_at"].endswith("Z"))
        self.assertTrue(payload["fetched_at"].endswith("Z"))
        self.assertEqual(payload["market_timestamp"], "2026-11-13T21:00:00Z")


class CorporateActionFieldTests(unittest.TestCase):
    def test_dividend_must_be_finite_and_non_negative(self):
        self.assertEqual(_p(dividend=0.0).dividend, 0.0)
        with self.assertRaises(MarketDataError):
            _p(dividend=-1.0)
        with self.assertRaises(MarketDataError):
            _p(dividend=float("nan"))

    def test_split_must_be_positive(self):
        self.assertEqual(_p(split=2.0).split, 2.0)
        with self.assertRaises(MarketDataError):
            _p(split=0.0)
        with self.assertRaises(MarketDataError):
            _p(split=float("inf"))


class DstTests(unittest.TestCase):
    """The New York close must follow DST, otherwise staleness is wrong."""

    def setUp(self):
        self.calendar = calendar_for("US")

    def test_standard_time_close_is_21_utc(self):
        close = self.calendar.market_close_datetime(date(2026, 3, 6))
        self.assertEqual(close.astimezone(UTC).hour, 21)

    def test_daylight_saving_close_is_20_utc(self):
        close = self.calendar.market_close_datetime(date(2026, 3, 9))
        self.assertEqual(close.astimezone(UTC).hour, 20)

    def test_a_session_gap_never_looks_like_a_dst_shift(self):
        # Friday 2026-03-06 → Monday 2026-03-09 is one session, even though the
        # UTC close moves by an hour.
        friday = self.calendar.market_close_datetime(date(2026, 3, 6))
        monday = self.calendar.market_close_datetime(date(2026, 3, 9))
        self.assertEqual(monday.astimezone(UTC).hour - friday.astimezone(UTC).hour, -1)
        self.assertEqual(self.calendar.sessions_stale(date(2026, 3, 6), date(2026, 3, 9)), 1)


class FxAndSeriesTimeTests(unittest.TestCase):
    def test_fx_points_carry_availability(self):
        series = FxSeries.from_pairs(
            "USD",
            [(date(2026, 11, 12), 7.10), (date(2026, 11, 13), 7.07)],
            source="yahoo",
        )
        self.assertEqual(series.base_currency, "CNY")
        for point in series.points:
            self.assertIsNotNone(point.fetched_at)
            self.assertIs(point.availability_source, AvailabilitySource.UNKNOWN)
            self.assertFalse(point.is_pit_usable)

    def test_series_keeps_each_point_timestamp(self):
        moment = datetime(2026, 11, 13, 21, 0, tzinfo=UTC)
        series = PriceSeries(
            ticker="LLY",
            points=[_p(market_timestamp=moment)],
        )
        self.assertEqual(series.points[0].market_timestamp, moment)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
