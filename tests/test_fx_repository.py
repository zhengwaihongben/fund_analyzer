# -*- coding: utf-8 -*-
"""V2.3.1 Stage 2.2: FX bars, persistence, revision ledger and PIT query.

The FX convention under test is ``FX_t = CNY per 1 unit of foreign currency``
and every observation must be able to answer three separate questions: which
trading date the rate belongs to, when it was observed, and when it became
knowable.  This file pins those three answers against the database's final
state rather than against the return value of a function.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fa.config import PRICE_REVISION_TOLERANCE
from fa.data.database import Repository
from fa.data.database.connection import DatabaseError
from fa.models import AvailabilitySource, FxSeries, MarketDataError, PricePoint

D0 = date(2026, 11, 16)
D1 = date(2026, 11, 17)
D2 = date(2026, 11, 18)
STAMP0 = datetime(2026, 11, 16, 0, 0, tzinfo=timezone.utc)
STAMP1 = datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc)
AVAILABLE0 = datetime(2026, 11, 17, 0, 30, tzinfo=timezone.utc)
AVAILABLE1 = datetime(2026, 11, 18, 0, 30, tzinfo=timezone.utc)
FETCHED0 = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)
FETCHED1 = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def make_bar(
    day: date,
    rate: float,
    *,
    stamp: datetime | None = STAMP0,
    available: datetime | None = AVAILABLE0,
    source: AvailabilitySource = AvailabilitySource.INFERRED,
    note: str = "bar 標記為 Europe/London 當日 00:00；公布時間為推論值",
    fetched: datetime = FETCHED0,
    origin: str = "yahoo",
) -> PricePoint:
    return PricePoint(
        price_date=day,
        close=rate,
        market="FX",
        currency="CNY",
        available_price_type="fx_close",
        used_price_type="fx_close",
        source=origin,
        market_timestamp=stamp,
        available_at=available,
        availability_source=source,
        availability_note=note,
        fetched_at=fetched,
    )


def series_of(*bars: PricePoint, currency: str = "USD") -> FxSeries:
    return FxSeries.from_bars(currency, bars, source="yahoo")


class FxCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Repository(Path(self._tmp.name) / "fx.db")
        self.addCleanup(self.repo.close)

    # -- raw table readers: assertions look at the stored state -------------
    def _rates(self, currency: str = "USD") -> list:
        return self.repo._conn.execute(  # noqa: SLF001 - 測試直接看表
            "SELECT * FROM fx_rates WHERE currency = ? ORDER BY rate_date",
            (currency,),
        ).fetchall()

    def _revisions(self, currency: str = "USD") -> list:
        return self.repo._conn.execute(  # noqa: SLF001 - 測試直接看表
            "SELECT * FROM fx_revisions WHERE currency = ? ORDER BY revision_id",
            (currency,),
        ).fetchall()


class FxBarModelTests(FxCase):
    def test_bars_keep_every_time_field(self) -> None:
        bar = make_bar(D0, 7.10)
        series = series_of(bar)

        self.assertEqual(len(series.bars), 1)
        self.assertIs(series.bars[0], series.points[0])
        self.assertEqual(series.bars[0].price_date, D0)
        self.assertEqual(series.bars[0].value, 7.10)
        self.assertEqual(series.bars[0].market_timestamp, STAMP0)
        self.assertEqual(series.bars[0].available_at, AVAILABLE0)
        self.assertEqual(series.bars[0].availability_source, AvailabilitySource.INFERRED)
        self.assertEqual(series.bars[0].fetched_at, FETCHED0)
        self.assertTrue(series.bars[0].availability_note)
        self.assertEqual(series.quote_convention, "CNY per 1 USD")

    def test_series_without_availability_is_not_pit_usable(self) -> None:
        bar = make_bar(D0, 7.10, stamp=None, available=None,
                       source=AvailabilitySource.UNKNOWN, note="")
        series = series_of(bar)
        self.assertEqual(len(series.bars), 1)
        self.assertEqual(series.pit_bars, [])

    def test_from_bars_rejects_ambiguous_direction(self) -> None:
        bar = make_bar(D0, 7.10)
        with self.assertRaises(MarketDataError):
            FxSeries.from_bars("USD", [bar], base_currency="")
        with self.assertRaises(MarketDataError):
            FxSeries.from_bars("USD", [bar], base_currency="EUR")

    def test_from_bars_rejects_non_positive_rate(self) -> None:
        bar = make_bar(D0, 7.10)
        bar.close = 0.0  # PricePoint 建構時已擋一次；這裡驗證序列層也擋
        with self.assertRaises(MarketDataError):
            FxSeries.from_bars("USD", [bar])

    def test_from_pairs_is_unchanged_for_legacy_callers(self) -> None:
        series = FxSeries.from_pairs("USD", [(D0, 7.10)], source="test")
        self.assertEqual(series.base_currency, "CNY")
        self.assertEqual(series.points[0].available_at, None)
        self.assertEqual(series.points[0].availability_source, AvailabilitySource.UNKNOWN)


class FxPersistenceTests(FxCase):
    def test_save_and_load_round_trip_keeps_full_provenance(self) -> None:
        written = self.repo.save_fx(series_of(make_bar(D0, 7.10), make_bar(D1, 7.11)))
        self.assertEqual(written, 2)

        loaded = self.repo.load_fx("USD")
        self.assertEqual(loaded.base_currency, "CNY")
        self.assertEqual(loaded.source, "database")
        self.assertEqual([point.value for point in loaded.bars], [7.10, 7.11])
        self.assertEqual(loaded.bars[0].market_timestamp, STAMP0)
        self.assertEqual(loaded.bars[0].available_at, AVAILABLE0)
        self.assertEqual(loaded.bars[0].availability_source, AvailabilitySource.INFERRED)
        self.assertEqual(loaded.bars[0].fetched_at, FETCHED0)

    def test_new_rows_do_not_create_revisions(self) -> None:
        self.repo.save_fx(series_of(make_bar(D0, 7.10), make_bar(D1, 7.11)))
        self.assertEqual(self._revisions(), [])
        self.assertEqual(self.repo.count_fx_revisions("USD"), 0)

    def test_identical_rate_only_refreshes_the_fetch_time(self) -> None:
        self.repo.save_fx(series_of(make_bar(D0, 7.10, fetched=FETCHED0)))
        first = self._rates()[0]["fetched_at"]

        self.repo.save_fx(series_of(make_bar(D0, 7.10, fetched=FETCHED1)))
        rows = self._rates()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rate"], 7.10)
        self.assertNotEqual(rows[0]["fetched_at"], first)
        self.assertEqual(rows[0]["fetched_at"], FETCHED1.isoformat().replace("+00:00", "Z"))
        self.assertEqual(self._revisions(), [])

    def test_changed_rate_is_recorded_as_a_revision(self) -> None:
        self.repo.save_fx(series_of(make_bar(D0, 7.10)))
        self.repo.save_fx(series_of(make_bar(D0, 7.11, fetched=FETCHED1)))

        rows = self._rates()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rate"], 7.11)

        revisions = self._revisions()
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["rate"], 7.10)
        self.assertEqual(revisions[0]["rate_date"], D0.isoformat())
        self.assertEqual(revisions[0]["reason"], "fx revision replaced rate")
        self.assertEqual(revisions[0]["base_currency"], "CNY")
        self.assertEqual(self.repo.count_fx_revisions("USD"), 1)

    def test_revision_preserves_the_stored_rows_own_times(self) -> None:
        self.repo.save_fx(series_of(make_bar(
            D0, 7.10,
            stamp=STAMP0,
            available=AVAILABLE0,
            source=AvailabilitySource.INFERRED,
            note="舊版本的說明",
            fetched=FETCHED0,
            origin="yahoo-old",
        )))
        self.repo.save_fx(series_of(make_bar(
            D0, 7.12,
            stamp=STAMP1,
            available=AVAILABLE1,
            source=AvailabilitySource.OBSERVED,
            note="新版本的說明",
            fetched=FETCHED1,
            origin="yahoo-new",
        )))

        revision = self._revisions()[0]
        # 帳本保存的是「舊列自己」的時間與出處，而不是這次進來的值。
        self.assertEqual(revision["rate"], 7.10)
        self.assertEqual(revision["market_timestamp"], "2026-11-16T00:00:00Z")
        self.assertEqual(revision["available_at"], "2026-11-17T00:30:00Z")
        self.assertEqual(revision["availability_source"], "inferred")
        self.assertEqual(revision["source"], "yahoo-old")
        self.assertEqual(revision["fetched_at"], "2026-11-17T08:00:00Z")
        self.assertTrue(revision["replaced_at"])
        self.assertEqual(revision["reason"], "fx revision replaced rate, market_timestamp")

        current = self._rates()[0]
        self.assertEqual(current["rate"], 7.12)
        self.assertEqual(current["availability_source"], "observed")
        self.assertEqual(current["available_at"], "2026-11-18T00:30:00Z")

    def test_change_within_tolerance_is_not_a_revision(self) -> None:
        self.repo.save_fx(series_of(make_bar(D0, 7.10)))
        self.repo.save_fx(series_of(make_bar(D0, 7.10 + PRICE_REVISION_TOLERANCE / 2)))
        self.assertEqual(self._revisions(), [])
        self.assertEqual(self.repo.count_fx_revisions("USD"), 0)

    def test_change_above_tolerance_is_a_revision(self) -> None:
        self.repo.save_fx(series_of(make_bar(D0, 7.10)))
        self.repo.save_fx(series_of(make_bar(D0, 7.10 + PRICE_REVISION_TOLERANCE * 2)))
        self.assertEqual(len(self._revisions()), 1)

    def test_vendor_missing_date_never_deletes_the_stored_row(self) -> None:
        self.repo.save_fx(series_of(make_bar(D0, 7.10), make_bar(D1, 7.11)))
        self.repo.save_fx(series_of(make_bar(D0, 7.12)))

        rows = self._rates()
        self.assertEqual([row["rate_date"] for row in rows],
                         [D0.isoformat(), D1.isoformat()])
        self.assertEqual(rows[1]["rate"], 7.11)
        self.assertEqual(self.repo.last_fx_date("USD"), D1)

    def test_bar_without_time_is_stored_without_inventing_one(self) -> None:
        self.repo.save_fx(series_of(make_bar(
            D0, 7.10, stamp=None, available=None,
            source=AvailabilitySource.UNKNOWN, note="")))

        row = self._rates()[0]
        self.assertIsNone(row["market_timestamp"])
        self.assertIsNone(row["available_at"])
        self.assertEqual(row["availability_source"], "unknown")

        loaded = self.repo.load_fx("USD")
        self.assertEqual(len(loaded.bars), 1)
        self.assertFalse(loaded.bars[0].is_pit_usable)

    def test_enrichment_after_the_fact_is_not_a_revision(self) -> None:
        self.repo.save_fx(series_of(make_bar(
            D0, 7.10, stamp=None, available=None,
            source=AvailabilitySource.UNKNOWN, note="")))
        self.repo.save_fx(series_of(make_bar(
            D0, 7.10,
            stamp=STAMP0,
            available=AVAILABLE0,
            source=AvailabilitySource.INFERRED,
            fetched=FETCHED1,
        )))

        self.assertEqual(self._revisions(), [])
        row = self._rates()[0]
        self.assertEqual(row["market_timestamp"], "2026-11-16T00:00:00Z")
        self.assertEqual(row["available_at"], "2026-11-17T00:30:00Z")
        self.assertEqual(row["availability_source"], "inferred")
        self.assertEqual(row["rate"], 7.10)

    def test_save_fx_rejects_ambiguous_base_and_writes_nothing(self) -> None:
        series = series_of(make_bar(D0, 7.10))
        series.base_currency = ""
        with self.assertRaises(DatabaseError):
            self.repo.save_fx(series)
        series.base_currency = "EUR"
        with self.assertRaises(DatabaseError):
            self.repo.save_fx(series)
        self.assertEqual(self._rates(), [])

    def test_empty_series_is_a_no_op(self) -> None:
        self.assertEqual(self.repo.save_fx(FxSeries.from_bars("USD", [])), 0)
        self.assertEqual(self._rates(), [])
        self.assertIsNone(self.repo.last_fx_date("USD"))


class FxPointInTimeTests(FxCase):
    def _seed(self) -> None:
        self.repo.save_fx(series_of(
            make_bar(D0, 7.10, stamp=STAMP0, available=AVAILABLE0),
            make_bar(D1, 7.11, stamp=STAMP1, available=AVAILABLE1, fetched=FETCHED1),
        ))

    def test_before_the_first_publication_nothing_is_visible(self) -> None:
        self._seed()
        series = self.repo.fx_as_of("USD", AVAILABLE0 - timedelta(minutes=1))
        self.assertEqual(series.bars, [])

    def test_exactly_at_the_publication_instant_it_is_visible(self) -> None:
        self._seed()
        series = self.repo.fx_as_of("USD", AVAILABLE0)
        self.assertEqual([bar.price_date for bar in series.bars], [D0])

    def test_between_publications_only_the_earlier_bar_is_visible(self) -> None:
        self._seed()
        series = self.repo.fx_as_of("USD", AVAILABLE1 - timedelta(minutes=1))
        self.assertEqual([bar.price_date for bar in series.bars], [D0])

    def test_after_both_publications_every_bar_is_visible(self) -> None:
        self._seed()
        series = self.repo.fx_as_of("USD", AVAILABLE1 + timedelta(minutes=1))
        self.assertEqual([bar.price_date for bar in series.bars], [D0, D1])
        self.assertEqual(series.base_currency, "CNY")

    def test_rows_without_available_at_are_invisible_to_pit(self) -> None:
        self._seed()
        self.repo.save_fx(series_of(make_bar(
            D2, 7.12, stamp=None, available=None, source=AvailabilitySource.UNKNOWN)))

        series = self.repo.fx_as_of("USD", AVAILABLE1 + timedelta(days=10))
        self.assertEqual([bar.price_date for bar in series.bars], [D0, D1])
        # 但 V2.2 的 load_fx 仍然看得到它。
        self.assertEqual(len(self.repo.load_fx("USD").bars), 3)

    def test_inferred_bar_becomes_visible_only_after_its_available_at(self) -> None:
        """D-1: the London-dated bar is public from the next London midnight."""
        self.repo.save_fx(series_of(make_bar(
            D1, 7.09,
            stamp=datetime(2026, 11, 16, 0, 0, tzinfo=timezone.utc),
            available=datetime(2026, 11, 17, 0, 30, tzinfo=timezone.utc),
            source=AvailabilitySource.INFERRED,
        )))

        before = self.repo.fx_as_of("USD", datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(before.bars, [])

        after = self.repo.fx_as_of("USD", datetime(2026, 11, 17, 0, 31, tzinfo=timezone.utc))
        self.assertEqual(len(after.bars), 1)
        self.assertEqual(after.bars[0].availability_source, AvailabilitySource.INFERRED)
        self.assertTrue(after.bars[0].availability_note)

    def test_pit_requires_an_aware_instant(self) -> None:
        self._seed()
        with self.assertRaises(DatabaseError):
            self.repo.fx_as_of("USD", datetime(2026, 11, 17, 0, 30))  # naive
        with self.assertRaises(DatabaseError):
            self.repo.fx_as_of("", AVAILABLE0)
        with self.assertRaises(DatabaseError):
            self.repo.fx_as_of("USD", D0)  # 只給日期

    def test_pit_honours_the_date_window(self) -> None:
        self._seed()
        series = self.repo.fx_as_of("USD", AVAILABLE1 + timedelta(minutes=1), start=D1)
        self.assertEqual([bar.price_date for bar in series.bars], [D1])
        series = self.repo.fx_as_of("USD", AVAILABLE1 + timedelta(minutes=1), end=D0)
        self.assertEqual([bar.price_date for bar in series.bars], [D0])


class FxPointInTimeTimezoneTests(FxCase):
    """PIT visibility must depend on the instant, never on how it is written."""

    def _seed(self) -> None:
        self.repo.save_fx(series_of(
            make_bar(D0, 7.10, stamp=STAMP0, available=AVAILABLE0),
            make_bar(D1, 7.11, stamp=STAMP1, available=AVAILABLE1, fetched=FETCHED1),
        ))

    def _visible(self, moment: datetime) -> list[str]:
        return [bar.price_date.isoformat()
                for bar in self.repo.fx_as_of("USD", moment).bars]

    def _insert_raw(self, day: date, rate: float, source_value: str,
                    available: datetime) -> None:
        """Write a row the model would not produce (illegal/unknown source)."""
        self.repo._conn.execute(  # noqa: SLF001 - 測試刻意繞過模型
            "INSERT INTO fx_rates (currency, rate_date, rate, source, retrieved_at,"
            " base_currency, market_timestamp, available_at, availability_source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("USD", day.isoformat(), rate, "hand-edit", "2026-11-18T00:00:00Z",
             "CNY", "2026-11-17T00:00:00Z", available.isoformat().replace("+00:00", "Z"),
             source_value),
        )
        self.repo._conn.commit()

    def test_same_instant_in_every_timezone_gives_the_same_result(self) -> None:
        self._seed()
        instant = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
        expected = self._visible(instant)
        self.assertEqual(expected, [D0.isoformat(), D1.isoformat()])
        for zone in ("UTC", "Asia/Shanghai", "America/Los_Angeles", "Europe/London"):
            with self.subTest(zone=zone):
                self.assertEqual(self._visible(instant.astimezone(ZoneInfo(zone))), expected)

    def test_timezone_equivalence_at_every_boundary(self) -> None:
        self._seed()
        for moment, expected in (
            (AVAILABLE0 - timedelta(minutes=1), []),
            (AVAILABLE0, [D0.isoformat()]),
            (AVAILABLE1 - timedelta(minutes=1), [D0.isoformat()]),
            (AVAILABLE1, [D0.isoformat(), D1.isoformat()]),
        ):
            for zone in ("UTC", "Asia/Shanghai", "America/Los_Angeles"):
                with self.subTest(moment=moment.isoformat(), zone=zone):
                    self.assertEqual(
                        self._visible(moment.astimezone(ZoneInfo(zone))), expected)

    def test_caller_local_date_behind_london_does_not_hide_public_data(self) -> None:
        """`2026-11-16T23:30-08:00` == `2026-11-17T07:30Z`, which is after the
        `2026-11-17` bar became available at `00:30Z`.  Its local date is still
        the 16th, so the removed `rate_date <= as_of.date()` filter hid a bar
        that was already public.
        """
        self.repo.save_fx(series_of(make_bar(
            D1, 7.09, stamp=STAMP1,
            available=datetime(2026, 11, 17, 0, 30, tzinfo=timezone.utc),
        )))
        moment = datetime(2026, 11, 16, 23, 30, tzinfo=timezone(timedelta(hours=-8)))
        self.assertEqual(self._visible(moment), [D1.isoformat()])
        # 同一個瞬間寫成 UTC 也是同一個答案。
        self.assertEqual(self._visible(moment.astimezone(timezone.utc)),
                         [D1.isoformat()])

    def test_unknown_source_with_an_available_at_is_excluded(self) -> None:
        self._insert_raw(D0, 7.10, "unknown", AVAILABLE0)
        self.assertEqual(self._visible(AVAILABLE1 + timedelta(days=1)), [])

    def test_illegal_source_is_excluded(self) -> None:
        self._insert_raw(D0, 7.10, "guessed", AVAILABLE0)
        self.assertEqual(self._visible(AVAILABLE1 + timedelta(days=1)), [])

    def test_whitelist_keeps_every_usable_source(self) -> None:
        for offset, source in enumerate(
                ("observed", "vendor_timestamp", "inferred")):
            self._insert_raw(
                date(2026, 11, 10 + offset), 7.10, source,
                datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc))
        visible = self._visible(datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc))
        self.assertEqual(visible, [
            date(2026, 11, 10).isoformat(),
            date(2026, 11, 11).isoformat(),
            date(2026, 11, 12).isoformat(),
        ])


if __name__ == "__main__":
    unittest.main()
