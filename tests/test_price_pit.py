# -*- coding: utf-8 -*-
"""V2.3.2 Stage A — price pipeline, revision ledger and PIT reads.

Offline only: a fake provider plus a temporary SQLite database.  No test here
reaches Yahoo, and none writes to ``data/database/fund_analyzer.db``.
"""
from __future__ import annotations

import math
import unittest
from datetime import date, datetime, time, timedelta, timezone
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import mock

from fa.data import price_provider
from fa.data.database.repository import Repository
from fa.data.price_pipeline import (
    PriceRequest,
    _first_incomplete_date,
    _plan_download,
    infer_availability,
    update_prices,
)
from fa.data.price_provider import PriceBar, PriceFetch, bar_trading_date, fetch_price_bars
from fa.models.market_data import AvailabilitySource, PricePoint, PriceSeries

UTC = timezone.utc
START = date(2026, 1, 5)   # Monday
END = date(2026, 2, 27)    # Friday
NOW = datetime(2026, 2, 27, 22, 0, tzinfo=UTC)


def _stamp(day: date) -> datetime:
    """A plausible US stock stamp: the 09:30 ET opening bell, 14:30Z in winter."""
    return datetime.combine(day, time(14, 30), tzinfo=UTC)


def _bar(day: date, close: float, *, adj: float | None = None, market: str = "US") -> PriceBar:
    return PriceBar(
        trading_date=day,
        close=close,
        adj_close=adj,
        market_timestamp=_stamp(day),
        currency="USD",
        market=market,
        source="yahoo",
    )


def _point(day: date, close: float, *, available: datetime | None = None,
           source: AvailabilitySource | None = None, **kwargs) -> PricePoint:
    when = available if available is not None else infer_availability(day, "US")[0]
    return PricePoint(
        price_date=day,
        close=close,
        adj_close=kwargs.pop("adj_close", None),
        market_timestamp=_stamp(day),
        available_at=when,
        availability_source=source or (
            AvailabilitySource.INFERRED if available is not None or source is None
            else AvailabilitySource.UNKNOWN
        ),
        availability_note="seed",
        fetched_at=NOW,
        market="US",
        currency="USD",
        available_price_type="close+adj_close",
        used_price_type="close+adj_close",
        source="seed",
        **kwargs,
    )


def _legacy_point(day: date, close: float) -> PricePoint:
    """A V2.2 row: readable, but invisible to PIT."""
    return PricePoint(
        price_date=day,
        close=close,
        market_timestamp=_stamp(day),
        available_at=None,
        availability_source=AvailabilitySource.UNKNOWN,
        fetched_at=NOW,
        market="US",
        currency="USD",
        available_price_type="close+adj_close",
        used_price_type="close+adj_close",
        source="legacy",
    )


def _typed_point(day: date, close: float, price_type: str, *,
                 usable: bool = True) -> PricePoint:
    """A row with a chosen ``price_type`` and a chosen PIT visibility."""
    return PricePoint(
        price_date=day,
        close=close,
        adj_close=close if price_type == "close+adj_close" else None,
        market_timestamp=_stamp(day),
        available_at=infer_availability(day, "US")[0] if usable else None,
        availability_source=(
            AvailabilitySource.INFERRED if usable else AvailabilitySource.UNKNOWN
        ),
        availability_note="seed" if usable else "",
        fetched_at=NOW,
        market="US",
        currency="USD",
        available_price_type=price_type,
        used_price_type=price_type,
        source="seed" if usable else "legacy",
    )


def _series(ticker: str, points: list[PricePoint]) -> PriceSeries:
    return PriceSeries.from_points(ticker, points, market="US", currency="USD", source="seed")


def _trading_days(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


class FakeProvider:
    """Records calls and returns queued fetches."""

    def __init__(self, *results: PriceFetch) -> None:
        self.calls: list[tuple[str, date, date]] = []
        self._results = list(results)
        self.error: Exception | None = None

    def __call__(self, ticker: str, start: date, end: date) -> PriceFetch:
        self.calls.append((ticker, start, end))
        if self.error is not None:
            raise self.error
        if not self._results:
            return PriceFetch(ticker=ticker, market="US", currency="USD", bars=[])
        return self._results.pop(0)


class PriceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Repository(Path(self._tmp.name) / "test.db")
        self.addCleanup(self.repo.close)

    def run_update(self, request: PriceRequest, provider) -> object:
        return update_prices(request, self.repo, provider=provider, now=NOW)

    def stored_days(self, ticker: str = "LLY") -> list[str]:
        return [row["price_date"] for row in self.repo._conn.execute(
            "SELECT price_date FROM market_prices WHERE ticker = ? ORDER BY price_date",
            (ticker,),
        ).fetchall()]


class PriceProviderTests(unittest.TestCase):
    def test_naive_stamp_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            bar_trading_date(datetime(2026, 11, 13, 14, 30), "America/New_York")

    def test_us_open_bell_stamp_maps_to_the_same_session(self) -> None:
        stamp = datetime(2026, 11, 13, 14, 30, tzinfo=UTC)
        self.assertEqual(bar_trading_date(stamp, "America/New_York"), date(2026, 11, 13))

    def test_venue_east_of_utc_maps_to_its_local_date(self) -> None:
        stamp = datetime(2026, 11, 16, 23, 30, tzinfo=UTC)
        self.assertEqual(bar_trading_date(stamp, "Asia/Tokyo"), date(2026, 11, 17))

    def test_unknown_timezone_falls_back_to_utc(self) -> None:
        stamp = datetime(2026, 11, 16, 23, 30, tzinfo=UTC)
        self.assertEqual(bar_trading_date(stamp, "Not/AZone"), date(2026, 11, 16))

    def test_parse_bars_keeps_stamps_filters_window_and_counts_drops(self) -> None:
        payload = {
            "meta": {
                "symbol": "LLY",
                "exchangeName": "NYQ",
                "currency": "USD",
                "exchangeTimezoneName": "America/New_York",
            },
            "timestamp": [
                int(datetime(2026, 11, 12, 14, 30, tzinfo=UTC).timestamp()),
                int(datetime(2026, 11, 13, 14, 30, tzinfo=UTC).timestamp()),
                int(datetime(2026, 10, 1, 14, 30, tzinfo=UTC).timestamp()),  # before window
            ],
            "indicators": {
                "quote": [{"close": [100.0, 101.0, 99.0]}],
                "adjclose": [{"adjclose": [100.0, 100.5, 99.0]}],
            },
        }
        with mock.patch.object(price_provider, "candidate_symbols", return_value=["LLY"]):
            with mock.patch.object(price_provider, "_http_chart", return_value=(payload, None)):
                fetch = fetch_price_bars("LLY", date(2026, 11, 10), date(2026, 11, 13))
        self.assertIsNone(fetch.error)
        self.assertEqual([bar.trading_date for bar in fetch.bars], [date(2026, 11, 12), date(2026, 11, 13)])
        self.assertEqual(fetch.market, "US")
        self.assertEqual(fetch.currency, "USD")
        self.assertEqual(fetch.bars[1].adj_close, 100.5)
        self.assertEqual(fetch.bars[0].market_timestamp, datetime(2026, 11, 12, 14, 30, tzinfo=UTC))

    def test_parse_bars_drops_unusable_closes(self) -> None:
        payload = {
            "meta": {"exchangeName": "NYQ", "currency": "USD", "exchangeTimezoneName": "America/New_York"},
            "timestamp": [
                int(datetime(2026, 11, 12, 14, 30, tzinfo=UTC).timestamp()),
                int(datetime(2026, 11, 13, 14, 30, tzinfo=UTC).timestamp()),
            ],
            "indicators": {"quote": [{"close": [None, 101.0]}], "adjclose": [{"adjclose": []}]},
        }
        with mock.patch.object(price_provider, "candidate_symbols", return_value=["LLY"]):
            with mock.patch.object(price_provider, "_http_chart", return_value=(payload, None)):
                fetch = fetch_price_bars("LLY", date(2026, 11, 10), date(2026, 11, 13))
        self.assertEqual(len(fetch.bars), 1)
        self.assertEqual(fetch.dropped, 1)
        self.assertIn("dropped-bars: 1", fetch.warnings)

    def test_http_error_is_reported_not_raised(self) -> None:
        with mock.patch.object(price_provider, "candidate_symbols", return_value=["LLY"]):
            with mock.patch.object(price_provider, "_http_chart", return_value=(None, "http-500")):
                fetch = fetch_price_bars("LLY", START, END)
        self.assertEqual(fetch.error, "http-500")
        self.assertEqual(fetch.bars, [])

    def test_transient_error_does_not_fall_back_to_another_venue(self) -> None:
        payload = {"meta": {"exchangeName": "NYQ", "currency": "USD"}, "timestamp": [],
                   "indicators": {"quote": [{"close": []}]}}
        calls: list[str] = []

        def fake_chart(symbol, start, end, timeout):
            calls.append(symbol)
            if symbol == "LLY":
                return None, "network-error: boom"
            return payload, None

        with mock.patch.object(price_provider, "candidate_symbols", return_value=["LLY", "LLY.SW"]):
            with mock.patch.object(price_provider, "_http_chart", side_effect=fake_chart):
                fetch = fetch_price_bars("LLY", START, END)
        self.assertEqual(calls, ["LLY"])
        self.assertTrue(fetch.error.startswith("network-error"))


class PriceCacheTests(PriceTestCase):
    def test_first_update_downloads_and_stamps_availability(self) -> None:
        days = _trading_days(START, START + timedelta(days=4))
        provider = FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD",
                                           bars=[_bar(day, 100.0 + i) for i, day in enumerate(days)]))
        outcome = self.run_update(PriceRequest(["LLY"], START, END), provider)
        self.assertEqual(outcome.downloads, 1)
        self.assertEqual(outcome.inserted, len(days))
        self.assertEqual(outcome.revisions, 0)
        self.assertEqual(len(self.stored_days()), len(days))
        row = self.repo._conn.execute(
            "SELECT available_at, availability_source FROM market_prices WHERE ticker='LLY'"
        ).fetchone()
        self.assertEqual(row["availability_source"], "inferred")
        self.assertTrue(row["available_at"].endswith("Z"))
        self.assertIn("21:30:00", row["available_at"])

    def test_complete_cache_needs_no_download(self) -> None:
        days = _trading_days(START, END)
        self.repo.save_prices(_series("LLY", [_point(day, 100.0 + i) for i, day in enumerate(days)]))
        provider = FakeProvider()
        outcome = self.run_update(PriceRequest(["LLY"], START, END), provider)
        self.assertEqual(outcome.downloads, 0)
        self.assertEqual(provider.calls, [])
        self.assertEqual(len(outcome.series["LLY"].points), len(days))
        self.assertEqual(outcome.revisions, 0)

    def test_prefer_cache_false_forces_a_download(self) -> None:
        days = _trading_days(START, START + timedelta(days=9))
        self.repo.save_prices(_series("LLY", [_point(day, 100.0) for day in days]))
        provider = FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD",
                                           bars=[_bar(day, 100.0) for day in days]))
        outcome = self.run_update(PriceRequest(["LLY"], START, END, prefer_cache=False), provider)
        self.assertEqual(outcome.downloads, 1)
        self.assertEqual(outcome.updated, len(days))

    def test_tail_gap_re_downloads_the_overlap(self) -> None:
        seeded = _trading_days(START, END - timedelta(days=14))
        self.repo.save_prices(_series("LLY", [_point(day, 100.0) for day in seeded]))
        provider = FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD",
                                           bars=[_bar(day, 100.0) for day in seeded[-3:]]))
        self.run_update(PriceRequest(["LLY"], START, END), provider)
        self.assertTrue(provider.calls)
        _, fetch_start, _ = provider.calls[0]
        self.assertLessEqual(fetch_start, seeded[-1] - timedelta(days=5))
        self.assertGreaterEqual(fetch_start, START)

    def test_missing_history_before_the_stored_window_is_not_a_cache_hit(self) -> None:
        recent = _trading_days(END - timedelta(days=9), END)
        self.repo.save_prices(_series("LLY", [_point(day, 100.0) for day in recent]))
        provider = FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD",
                                           bars=[_bar(day, 100.0) for day in recent]))
        self.run_update(PriceRequest(["LLY"], START, END), provider)
        self.assertEqual(provider.calls[0][1], START)

    def test_during_a_session_yesterday_is_a_complete_window(self) -> None:
        # 2026-11-17 (Tue) 10:25Z: the 11-17 US session has not closed, so the
        # published tail edge is 11-16 and a series ending 11-16 is complete.
        days = _trading_days(START, date(2026, 11, 16))
        self.repo.save_prices(_series("LLY", [_point(day, 100.0) for day in days]))
        moment = datetime(2026, 11, 17, 10, 25, tzinfo=UTC)
        provider = FakeProvider()
        outcome = update_prices(
            PriceRequest(["LLY"], START, date(2026, 11, 17)), self.repo,
            provider=provider, now=moment,
        )
        self.assertEqual(provider.calls, [])
        self.assertEqual(outcome.downloads, 0)

    def test_market_mismatch_is_rejected(self) -> None:
        days = _trading_days(START, START + timedelta(days=2))
        self.repo.save_prices(_series("LLY", [_point(day, 100.0) for day in days]))
        swiss = PriceFetch(
            ticker="LLY", symbol="LLY.SW", market="EBS", currency="CHF",
            bars=[_bar(day, 120.0, market="EBS") for day in days],
        )
        provider = FakeProvider(swiss)
        outcome = self.run_update(PriceRequest(["LLY"], START, END, prefer_cache=False), provider)
        self.assertIn("market-mismatch", outcome.skipped["LLY"])
        closes = {row["close"] for row in self.repo._conn.execute(
            "SELECT close FROM market_prices WHERE ticker='LLY'").fetchall()}
        self.assertEqual(closes, {100.0})

    def test_legacy_rows_without_availability_are_re_downloaded(self) -> None:
        days = _trading_days(START, END)
        self.repo.save_prices(
            _series("LLY", [_legacy_point(day, 100.0 + i) for i, day in enumerate(days)])
        )
        same = PriceFetch(
            ticker="LLY", market="US", currency="USD",
            bars=[_bar(day, 100.0 + i) for i, day in enumerate(days)],
        )
        provider = FakeProvider(same)
        outcome = self.run_update(PriceRequest(["LLY"], START, END), provider)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0][1], days[0])
        self.assertEqual(outcome.downloads, 1)
        self.assertEqual(outcome.updated, len(days))
        self.assertEqual(outcome.revisions, 0)  # same numbers: evidence, not a revision
        sources = {row["availability_source"] for row in self.repo._conn.execute(
            "SELECT availability_source FROM market_prices WHERE ticker='LLY'").fetchall()}
        self.assertEqual(sources, {"inferred"})
        again = self.run_update(PriceRequest(["LLY"], START, END), FakeProvider())
        self.assertEqual(again.downloads, 0)
        self.assertEqual(again.updated, 0)

    def test_partial_legacy_metadata_starts_the_download_at_the_first_gap(self) -> None:
        days = _trading_days(START, END)
        split = len(days) // 2
        points = [_point(day, 100.0 + i) for i, day in enumerate(days[:split])]
        points += [_legacy_point(day, 100.0 + i) for i, day in enumerate(days[split:], start=split)]
        self.repo.save_prices(_series("LLY", points))
        bars = [_bar(day, 100.0 + i) for i, day in enumerate(days[split:], start=split)]
        provider = FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD", bars=bars))
        outcome = self.run_update(PriceRequest(["LLY"], START, END), provider)
        self.assertEqual(provider.calls[0][1], days[split])
        self.assertEqual(outcome.updated, len(bars))
        sources = {row["availability_source"] for row in self.repo._conn.execute(
            "SELECT availability_source FROM market_prices WHERE ticker='LLY' AND price_date >= ?",
            (days[split].isoformat(),),
        ).fetchall()}
        self.assertEqual(sources, {"inferred"})

    def test_legacy_price_type_is_updated_in_place_not_duplicated(self) -> None:
        days = _trading_days(START, END)
        legacy = [
            PricePoint(
                price_date=day,
                close=100.0 + i,
                adj_close=100.0 + i,
                market_timestamp=_stamp(day),
                available_at=None,
                availability_source=AvailabilitySource.UNKNOWN,
                fetched_at=NOW,
                market="US",
                currency="USD",
                available_price_type="adjusted_close",
                used_price_type="adjusted_close",
                source="legacy",
            )
            for i, day in enumerate(days)
        ]
        self.repo.save_prices(_series("LLY", legacy))
        bars = [_bar(day, 100.0 + i) for i, day in enumerate(days)]
        outcome = self.run_update(
            PriceRequest(["LLY"], START, END),
            FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD", bars=bars)),
        )
        self.assertEqual(outcome.updated, len(days))
        rows = self.repo._conn.execute(
            "SELECT price_type, availability_source FROM market_prices WHERE ticker='LLY'"
        ).fetchall()
        self.assertEqual(len(rows), len(days))  # one row per session, no second key
        self.assertEqual({row["price_type"] for row in rows}, {"adjusted_close"})
        self.assertEqual({row["availability_source"] for row in rows}, {"inferred"})

    def test_failed_re_download_leaves_legacy_rows_pit_invisible(self) -> None:
        days = _trading_days(START, END)
        self.repo.save_prices(_series("LLY", [_legacy_point(day, 100.0) for day in days]))
        failing = PriceFetch(
            ticker="LLY", market="US", currency="USD", bars=[], error="timeout",
        )
        provider = FakeProvider(failing)
        outcome = self.run_update(PriceRequest(["LLY"], START, END), provider)
        self.assertEqual(outcome.skipped.get("LLY"), "timeout")
        self.assertEqual(outcome.downloads, 1)
        self.assertEqual(outcome.updated, 0)
        sources = {row["availability_source"] for row in self.repo._conn.execute(
            "SELECT availability_source FROM market_prices WHERE ticker='LLY'").fetchall()}
        self.assertEqual(sources, {"unknown"})
        self.assertEqual(self.repo.prices_as_of("LLY", NOW).points, [])


class PriceValidationTests(PriceTestCase):
    def test_invalid_rate_is_dropped_but_other_bars_are_kept(self) -> None:
        good = _trading_days(START, START + timedelta(days=2))
        bad = _bar(good[1], 0.0)
        nan_bar = _bar(good[2], math.nan)
        provider = FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD",
                                           bars=[_bar(good[0], 100.0), bad, nan_bar]))
        outcome = self.run_update(PriceRequest(["LLY"], START, END), provider)
        self.assertEqual(outcome.inserted, 1)
        kinds = {item["kind"] for item in outcome.anomalies}
        self.assertIn("invalid-rate", kinds)
        self.assertEqual(self.stored_days(), [good[0].isoformat()])

    def test_out_of_range_bar_is_never_written(self) -> None:
        outside = _bar(START - timedelta(days=10), 50.0)
        inside = _bar(START, 100.0)
        provider = FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD",
                                           bars=[outside, inside]))
        outcome = self.run_update(PriceRequest(["LLY"], START, END), provider)
        self.assertIn("out-of-range", {item["kind"] for item in outcome.anomalies})
        self.assertEqual(self.stored_days(), [START.isoformat()])

    def test_duplicate_dates_keep_the_latest_stamp(self) -> None:
        first = _bar(START, 100.0)
        later = PriceBar(
            trading_date=START, close=101.0, market_timestamp=_stamp(START) + timedelta(hours=1),
            currency="USD", market="US", source="yahoo",
        )
        provider = FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD",
                                           bars=[first, later]))
        outcome = self.run_update(PriceRequest(["LLY"], START, END), provider)
        self.assertIn("duplicate-date", {item["kind"] for item in outcome.anomalies})
        row = self.repo._conn.execute(
            "SELECT close FROM market_prices WHERE ticker='LLY' AND price_date=?",
            (START.isoformat(),),
        ).fetchone()
        self.assertEqual(row["close"], 101.0)

    def test_jump_is_recorded_and_kept(self) -> None:
        days = _trading_days(START, START + timedelta(days=1))
        provider = FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD",
                                           bars=[_bar(days[0], 100.0), _bar(days[1], 200.0)]))
        outcome = self.run_update(PriceRequest(["LLY"], START, END), provider)
        jumps = [item for item in outcome.anomalies if item["kind"] == "jump"]
        self.assertEqual(len(jumps), 1)
        self.assertAlmostEqual(jumps[0]["value"], 1.0)
        self.assertEqual(len(self.stored_days()), 2)

    def test_provider_error_is_isolated(self) -> None:
        provider = FakeProvider()
        provider.error = RuntimeError("boom")
        outcome = self.run_update(PriceRequest(["LLY"], START, END), provider)
        self.assertIn("provider-error", outcome.skipped["LLY"])
        self.assertEqual(self.stored_days(), [])


class PriceRevisionTests(PriceTestCase):
    def _seed(self, close: float) -> None:
        day = START
        self.repo.save_prices(_series("LLY", [_point(day, close)]))

    def test_changed_close_is_ledgered_and_bumped(self) -> None:
        self._seed(100.0)
        provider = FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD",
                                           bars=[_bar(START, 105.0)]))
        outcome = self.run_update(PriceRequest(["LLY"], START, END), provider)
        self.assertEqual(outcome.revisions, 1)
        revision = self.repo._conn.execute("SELECT * FROM price_revisions").fetchone()
        self.assertEqual(revision["close"], 100.0)
        current = self.repo._conn.execute(
            "SELECT * FROM market_prices WHERE ticker='LLY'").fetchone()
        self.assertEqual(current["close"], 105.0)
        # 修訂值只能在修訂發生之後才可見：available_at 被推到 replaced_at，
        # 與舊版「正好在 replaced_at 起不再可見」互補，任何時刻都只看到一個版本。
        self.assertEqual(current["available_at"], revision["replaced_at"])

    def test_identical_re_download_creates_no_revision(self) -> None:
        self._seed(100.0)
        provider = FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD",
                                           bars=[_bar(START, 100.0)]))
        outcome = self.run_update(PriceRequest(["LLY"], START, END), provider)
        self.assertEqual(outcome.revisions, 0)
        self.assertEqual(self.repo._conn.execute(
            "SELECT COUNT(*) AS n FROM price_revisions").fetchone()["n"], 0)

    def test_a_revised_value_is_not_visible_before_the_revision(self) -> None:
        original_available = infer_availability(START, "US")[0]
        self._seed(100.0)
        provider = FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD",
                                           bars=[_bar(START, 105.0)]))
        self.run_update(PriceRequest(["LLY"], START, END), provider)
        revision = self.repo._conn.execute("SELECT replaced_at FROM price_revisions").fetchone()
        replaced_at = datetime.fromisoformat(revision["replaced_at"].replace("Z", "+00:00"))
        before = self.repo.prices_as_of("LLY", original_available + timedelta(minutes=1))
        self.assertEqual([point.close for point in before.points], [100.0])
        after = self.repo.prices_as_of("LLY", replaced_at + timedelta(seconds=1))
        self.assertEqual([point.close for point in after.points], [105.0])

    def _revised(self) -> datetime:
        self._seed(100.0)
        provider = FakeProvider(PriceFetch(ticker="LLY", market="US", currency="USD",
                                           bars=[_bar(START, 105.0)]))
        self.run_update(PriceRequest(["LLY"], START, END), provider)
        row = self.repo._conn.execute(
            "SELECT replaced_at FROM price_revisions").fetchone()
        return datetime.fromisoformat(row["replaced_at"].replace("Z", "+00:00"))

    def closes_at(self, moment: datetime) -> list[float]:
        return [point.close for point in self.repo.prices_as_of("LLY", moment).points]

    def test_revision_boundary_one_microsecond_before_keeps_the_old_version(self) -> None:
        replaced_at = self._revised()
        self.assertEqual(self.closes_at(replaced_at - timedelta(microseconds=1)), [100.0])

    def test_revision_boundary_at_the_instant_shows_only_the_new_version(self) -> None:
        replaced_at = self._revised()
        self.assertEqual(self.closes_at(replaced_at), [105.0])

    def test_revision_boundary_one_microsecond_after_shows_only_the_new_version(self) -> None:
        replaced_at = self._revised()
        self.assertEqual(self.closes_at(replaced_at + timedelta(microseconds=1)), [105.0])


class PricePointInTimeTests(PriceTestCase):
    def test_unknown_availability_is_excluded_but_still_readable(self) -> None:
        self.repo.save_prices(_series("LLY", [_legacy_point(START, 100.0)]))
        self.assertEqual(self.repo.prices_as_of("LLY", NOW + timedelta(days=400)).points, [])
        self.assertEqual(len(self.repo.load_prices("LLY").points), 1)

    def test_availability_boundary_is_inclusive_at_the_instant(self) -> None:
        available = infer_availability(START, "US")[0]
        self.repo.save_prices(_series("LLY", [_point(START, 100.0, available=available)]))
        self.assertEqual(self.repo.prices_as_of("LLY", available - timedelta(microseconds=1)).points, [])
        self.assertEqual(len(self.repo.prices_as_of("LLY", available).points), 1)

    def test_availability_one_microsecond_later_is_visible(self) -> None:
        available = infer_availability(START, "US")[0]
        self.repo.save_prices(_series("LLY", [_point(START, 100.0, available=available)]))
        self.assertEqual(
            len(self.repo.prices_as_of("LLY", available + timedelta(microseconds=1)).points), 1
        )

    def test_same_instant_in_any_timezone_gives_the_same_result(self) -> None:
        available = infer_availability(START, "US")[0]
        self.repo.save_prices(_series("LLY", [_point(START, 100.0, available=available)]))
        moment = available + timedelta(hours=1)
        shanghai = moment.astimezone(timezone(timedelta(hours=8)))
        self.assertEqual(
            len(self.repo.prices_as_of("LLY", moment).points),
            len(self.repo.prices_as_of("LLY", shanghai).points),
        )

    def test_naive_as_of_is_rejected(self) -> None:
        from fa.data.database.connection import DatabaseError

        with self.assertRaises(DatabaseError):
            self.repo.prices_as_of("LLY", datetime(2026, 1, 5))

    def test_execution_instant_after_the_close_sees_the_bar(self) -> None:
        day = date(2026, 7, 1)  # US summer: close 20:00Z, available 20:30Z
        available, _ = infer_availability(day, "US")
        self.assertEqual(available, datetime(2026, 7, 1, 20, 30, tzinfo=UTC))
        self.repo.save_prices(_series("LLY", [_point(day, 100.0, available=available)]))
        self.assertEqual(self.repo.prices_as_of("LLY", datetime(2026, 7, 1, 20, 0, tzinfo=UTC)).points, [])
        self.assertEqual(len(self.repo.prices_as_of("LLY", available).points), 1)

    def test_winter_close_is_stamped_at_21_30_utc(self) -> None:
        available, _ = infer_availability(date(2026, 1, 6), "US")
        self.assertEqual(available, datetime(2026, 1, 6, 21, 30, tzinfo=UTC))


class PriceRequestTests(unittest.TestCase):
    def test_tickers_are_normalised_and_de_duplicated(self) -> None:
        request = PriceRequest([" lly ", "", "LLY", "abbv"], START, END)
        self.assertEqual(request.tickers, ["LLY", "ABBV"])

    def test_datetime_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            PriceRequest(["LLY"], datetime(2026, 1, 5), END)

    def test_reversed_window_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PriceRequest(["LLY"], END, START)

    def test_naive_now_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            update_prices(PriceRequest(["LLY"], START, END), None, provider=FakeProvider(),
                          now=datetime(2026, 1, 5))


class PriceDownloadPlanTests(unittest.TestCase):
    """The cache decision must not depend on the tail edge.

    BT-PIT-01…06 (review m08830): a short tail, or ``prefer_cache=False``, must
    never hide an earlier session that has no publication evidence.
    """

    WINDOW_START = date(2026, 1, 5)
    WINDOW_END = date(2026, 2, 6)
    PLAN_NOW = datetime(2026, 2, 6, 23, 0, tzinfo=UTC)

    def plan(self, points: list[PricePoint], *, prefer_cache: bool = True):
        request = PriceRequest(["LLY"], self.WINDOW_START, self.WINDOW_END,
                               prefer_cache=prefer_cache)
        return _plan_download(request, _series("LLY", points), "US", self.PLAN_NOW)

    def test_complete_window_and_metadata_is_a_cache_hit(self) -> None:
        days = _trading_days(self.WINDOW_START, self.WINDOW_END)
        self.assertEqual(self.plan([_point(day, 100.0) for day in days]), (True, None))

    def test_first_session_without_metadata_is_the_download_start(self) -> None:
        days = _trading_days(self.WINDOW_START, self.WINDOW_END)
        points = [_typed_point(day, 100.0, "close+adj_close", usable=index > 0)
                  for index, day in enumerate(days)]
        cache_hit, fetch_start = self.plan(points)
        self.assertFalse(cache_hit)
        self.assertEqual(fetch_start, days[0])

    def test_tail_gap_does_not_hide_an_earlier_metadata_gap(self) -> None:
        days = _trading_days(self.WINDOW_START, self.WINDOW_END)
        gap = days[10]
        points = [
            _typed_point(day, 100.0, "close+adj_close", usable=day != gap)
            for day in days[:-2]
        ]
        cache_hit, fetch_start = self.plan(points)
        self.assertFalse(cache_hit)
        self.assertEqual(fetch_start, gap)
        self.assertLess(fetch_start, days[-3])

    def test_interior_metadata_gap_in_a_complete_window_starts_at_the_gap(self) -> None:
        days = _trading_days(self.WINDOW_START, self.WINDOW_END)
        gap = days[15]
        points = [_typed_point(day, 100.0, "close+adj_close", usable=day != gap)
                  for day in days]
        cache_hit, fetch_start = self.plan(points)
        self.assertFalse(cache_hit)
        # The overlap may reach back further than the gap; what matters is that
        # the gap itself is inside the download window.
        self.assertLessEqual(fetch_start, gap)
        self.assertGreaterEqual(fetch_start, self.WINDOW_START)

    def test_forced_refresh_still_covers_the_metadata_gap(self) -> None:
        days = _trading_days(self.WINDOW_START, self.WINDOW_END)
        gap = days[15]
        points = [_typed_point(day, 100.0, "close+adj_close", usable=day != gap)
                  for day in days]
        cache_hit, fetch_start = self.plan(points, prefer_cache=False)
        self.assertFalse(cache_hit)
        self.assertLessEqual(fetch_start, gap)

    def test_empty_store_starts_at_the_request(self) -> None:
        self.assertEqual(self.plan([]), (False, self.WINDOW_START))

    def test_history_gap_starts_at_the_request_start(self) -> None:
        days = _trading_days(self.WINDOW_START, self.WINDOW_END)[20:]
        self.assertEqual(self.plan([_point(day, 100.0) for day in days]),
                         (False, self.WINDOW_START))


class PriceDuplicateKeyTests(PriceTestCase):
    """A superseded legacy key must not make a session look incomplete.

    BT-PIT-07…12 (review m08830).  The database may hold an old
    ``adjusted_close`` row next to a re-downloaded ``close+adj_close`` row for
    the same session; completeness is a property of the session, not of every
    row that happens to share its date.
    """

    def seed(self, ticker: str, points: list[PricePoint]) -> None:
        self.repo.save_prices(_series(ticker, points))

    def rows(self, ticker: str = "LLY") -> list[tuple[str, str, str]]:
        return [
            (row["price_date"], row["price_type"], row["availability_source"])
            for row in self.repo._conn.execute(
                "SELECT price_date, price_type, availability_source FROM market_prices "
                "WHERE ticker = ? ORDER BY price_date, price_type",
                (ticker,),
            ).fetchall()
        ]

    def test_superseded_unknown_key_does_not_force_a_redownload(self) -> None:
        days = _trading_days(START, START + timedelta(days=20))[:5]
        for day in days:
            self.seed("LLY", [_typed_point(day, 100.0, "adjusted_close", usable=False)])
            self.seed("LLY", [_typed_point(day, 100.0, "close+adj_close")])
        provider = FakeProvider()
        provider.error = AssertionError("cache hit expected, provider was called")
        outcome = self.run_update(PriceRequest(["LLY"], days[0], days[-1]), provider)
        self.assertEqual(outcome.downloads, 0)
        self.assertIsNone(_first_incomplete_date(self.repo.load_prices("LLY")))
        self.assertEqual(len(self.repo.prices_as_of("LLY", NOW).points), len(days))

    def test_session_with_only_unknown_keys_is_still_downloaded(self) -> None:
        days = _trading_days(START, START + timedelta(days=20))[:4]
        for day in days:
            self.seed("LLY", [_typed_point(day, 100.0, "adjusted_close", usable=False)])
        provider = FakeProvider(PriceFetch(
            ticker="LLY", market="US", currency="USD",
            bars=[_bar(day, 100.0 + index, adj=100.0 + index) for index, day in enumerate(days)],
        ))
        outcome = self.run_update(PriceRequest(["LLY"], days[0], days[-1]), provider)
        self.assertEqual(outcome.downloads, 1)
        self.assertEqual(provider.calls[0][1], days[0])
        self.assertEqual({row[2] for row in self.rows()}, {"inferred"})

    def test_redownload_updates_the_canonical_key_not_a_third_key(self) -> None:
        day = _trading_days(START, START + timedelta(days=10))[0]
        self.seed("LLY", [_typed_point(day, 100.0, "adjusted_close", usable=False)])
        provider = FakeProvider(PriceFetch(
            ticker="LLY", market="US", currency="USD", bars=[_bar(day, 101.0, adj=101.0)],
        ))
        outcome = self.run_update(PriceRequest(["LLY"], day, day), provider)
        self.assertEqual(outcome.downloads, 1)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "adjusted_close")
        self.assertEqual(rows[0][2], "inferred")
        second = self.run_update(PriceRequest(["LLY"], day, day), FakeProvider())
        self.assertEqual(second.downloads, 0)

    def test_seventy_three_legacy_duplicates_reach_a_cache_hit(self) -> None:
        days = _trading_days(START, START + timedelta(days=110))[:73]
        for day in days:
            self.seed("LLY", [_typed_point(day, 100.0, "adjusted_close", usable=False)])
            self.seed("LLY", [_typed_point(day, 100.0, "close+adj_close")])
        self.assertEqual(len(self.rows()), 146)
        provider = FakeProvider()
        provider.error = AssertionError("73 legacy duplicates caused a re-download")
        request = PriceRequest(["LLY"], days[0], days[-1])
        outcome = self.run_update(request, provider)
        self.assertEqual(outcome.downloads, 0)
        self.assertEqual(len(self.rows()), 146)
        self.assertEqual(
            {row[1] for row in self.rows()}, {"adjusted_close", "close+adj_close"}
        )
        self.assertEqual(len(self.repo.prices_as_of("LLY", datetime(2027, 1, 1, tzinfo=UTC)).points), 73)
        again = self.run_update(request, FakeProvider())
        self.assertEqual(again.downloads, 0)
        self.assertEqual(again.warnings, [])


class PriceDateWindowTests(PriceTestCase):
    """DATE-01…DATE-08: ``start``/``end`` are pushed into both UNION branches.

    The window is a trading-date filter, so SQL must apply it to
    ``market_prices`` and ``price_revisions`` alike; leaving it to Python would
    read every stored year just to discard it, and applying it to only one
    branch would let the other leak dates outside the requested range.
    """

    DAYS = _trading_days(START, START + timedelta(days=13))

    def seed(self, days: list[date], *, base: float = 100.0) -> None:
        self.repo.save_prices(_series("LLY", [
            _point(day, base + index) for index, day in enumerate(days)
        ]))

    def dates(self, **kwargs) -> list[date]:
        return [
            point.price_date
            for point in self.repo.prices_as_of("LLY", NOW, **kwargs).points
        ]

    def closes(self, **kwargs) -> list[float]:
        return [
            point.close
            for point in self.repo.prices_as_of("LLY", NOW, **kwargs).points
        ]

    def test_no_range_returns_every_visible_date(self) -> None:
        window = self.DAYS[:6]
        self.seed(window)
        self.assertEqual(self.dates(), window)

    def test_start_only(self) -> None:
        window = self.DAYS[:6]
        self.seed(window)
        self.assertEqual(self.dates(start=self.DAYS[2]), window[2:])

    def test_end_only(self) -> None:
        window = self.DAYS[:6]
        self.seed(window)
        self.assertEqual(self.dates(end=self.DAYS[3]), window[:4])

    def test_both_bounds(self) -> None:
        window = self.DAYS[:6]
        self.seed(window)
        self.assertEqual(
            self.dates(start=self.DAYS[1], end=self.DAYS[4]), window[1:5]
        )

    def test_start_edge_is_inclusive(self) -> None:
        window = self.DAYS[:5]
        self.seed(window)
        self.assertIn(self.DAYS[2], self.dates(start=self.DAYS[2], end=self.DAYS[4]))

    def test_end_edge_is_inclusive(self) -> None:
        window = self.DAYS[:5]
        self.seed(window)
        self.assertIn(self.DAYS[3], self.dates(start=self.DAYS[1], end=self.DAYS[3]))

    def test_window_keeps_the_version_visible_at_the_instant(self) -> None:
        day = self.DAYS[2]
        self.repo.save_prices(_series("LLY", [_point(day, 100.0)]))
        self.repo.save_prices(_series("LLY", [_point(day, 105.0)]))
        before = self.repo.prices_as_of("LLY", NOW, start=day, end=day).points
        self.assertEqual([point.close for point in before], [100.0])
        later = datetime.now(UTC) + timedelta(seconds=5)
        after = self.repo.prices_as_of("LLY", later, start=day, end=day).points
        self.assertEqual([point.close for point in after], [105.0])

    def test_revision_outside_the_window_does_not_leak(self) -> None:
        window = self.DAYS[:5]
        self.seed(window)
        self.repo.save_prices(_series("LLY", [_point(self.DAYS[0], 999.0)]))
        later = datetime.now(UTC) + timedelta(seconds=5)
        later_dates = [
            point.price_date
            for point in self.repo.prices_as_of(
                "LLY", later, start=window[2], end=window[4]
            ).points
        ]
        self.assertEqual(later_dates, window[2:5])
        visible_now = self.dates(start=window[2], end=window[4])
        self.assertEqual(visible_now, window[2:5])


if __name__ == "__main__":
    unittest.main()
