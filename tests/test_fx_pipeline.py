# -*- coding: utf-8 -*-
"""Stage 2.4 — FX pipeline tests (fake provider, temporary SQLite only)."""
from __future__ import annotations

import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from fa.data import fx_pipeline
from fa.data.database.repository import Repository
from fa.data.fx_pipeline import (
    FxRequest,
    next_fx_session,
    previous_fx_session,
    update_fx,
)
from fa.data.database.connection import DatabaseError
from fa.data.fx_provider import FxBar, FxFetch, infer_availability
from fa.models.market_data import (
    AvailabilitySource,
    FxSeries,
    MarketDataError,
    PricePoint,
)

UTC = timezone.utc
START = date(2026, 1, 5)      # Monday
END = date(2026, 2, 27)       # Friday
NOW = datetime(2026, 2, 27, 12, 0, tzinfo=UTC)


def _stamp(day: date) -> datetime:
    """A plausible Yahoo stamp: 00:00 London == 23:00Z of the previous day."""
    return datetime.combine(day - timedelta(days=1), time(23, 0), tzinfo=UTC)


def _bar(day: date, rate: float, *, offset_hours: int = 0) -> FxBar:
    return FxBar(
        rate_date=day,
        rate=rate,
        market_timestamp=_stamp(day) + timedelta(hours=offset_hours),
        source="yahoo",
    )


def _points(pairs, *, fetched_at: datetime = NOW, source: str = "seed"):
    points = []
    for day, rate in pairs:
        points.append(
            PricePoint(
                price_date=day,
                close=rate,
                market_timestamp=_stamp(day),
                available_at=infer_availability(day),
                availability_source=AvailabilitySource.INFERRED,
                availability_note="seed",
                fetched_at=fetched_at,
                market="FX",
                currency="CNY",
                available_price_type="fx_close",
                used_price_type="fx_close",
                source=source,
            )
        )
    return points


def _series(currency: str, pairs, **kwargs) -> FxSeries:
    return FxSeries.from_bars(
        currency, _points(pairs, **kwargs), source="seed", base_currency="CNY"
    )


def _legacy_points(pairs, *, fetched_at: datetime = NOW):
    """Rows as they were written before the availability model existed."""
    return [
        PricePoint(
            price_date=day,
            close=rate,
            market_timestamp=_stamp(day),
            available_at=None,
            availability_source=AvailabilitySource.UNKNOWN,
            availability_note="",
            fetched_at=fetched_at,
            market="FX",
            currency="CNY",
            available_price_type="fx_close",
            used_price_type="fx_close",
            source="legacy",
        )
        for day, rate in pairs
    ]


def _legacy_series(currency: str, pairs) -> FxSeries:
    return FxSeries.from_bars(
        currency, _legacy_points(pairs), source="legacy", base_currency="CNY"
    )


class FakeProvider:
    """Records every call and returns whatever the test queued."""

    def __init__(self, results):
        self.results = results
        self.calls: list[tuple[str, date, date]] = []

    def __call__(self, currency: str, start: date, end: date) -> FxFetch:
        self.calls.append((currency, start, end))
        result = self.results.get(currency, FxFetch(currency=currency, symbol="X"))
        if callable(result):
            return result(currency, start, end)
        return result


class PipelineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Repository(Path(self._tmp.name) / "test.db")
        self.addCleanup(self.repo.close)

    def run_update(self, request, provider, now=NOW):
        return update_fx(request, self.repo, provider=provider, now=now)

    def stored_days(self, currency: str = "USD"):
        series = self.repo.load_fx(currency)
        return [point.price_date for point in series.points]


# --- 10.1 validation ---------------------------------------------------------


class FxPipelineValidationTests(PipelineTestCase):
    def test_empty_currency_list_does_nothing(self):
        provider = FakeProvider({})
        outcome = self.run_update(FxRequest([], START, END), provider)
        self.assertEqual(provider.calls, [])
        self.assertEqual(outcome.series, {})
        self.assertEqual(outcome.downloads, 0)

    def test_duplicate_currencies_are_downloaded_once(self):
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=[_bar(START, 7.0)])})
        outcome = self.run_update(FxRequest(["usd", "USD", " usd "], START, END), provider)
        self.assertEqual(len(provider.calls), 1)
        self.assertIn("USD", outcome.series)

    def test_cny_is_skipped_as_base_currency(self):
        provider = FakeProvider({})
        outcome = self.run_update(FxRequest(["CNY"], START, END), provider)
        self.assertEqual(provider.calls, [])
        self.assertEqual(outcome.skipped["CNY"], "base-currency")

    def test_unsupported_currency_is_skipped(self):
        provider = FakeProvider({})
        outcome = self.run_update(FxRequest(["XYZ"], START, END), provider)
        self.assertEqual(provider.calls, [])
        self.assertEqual(outcome.skipped["XYZ"], "unsupported-currency")

    def test_invalid_rate_drops_only_that_bar(self):
        bars = [_bar(START, 7.0), _bar(START + timedelta(days=1), 0.0),
                _bar(START + timedelta(days=2), 7.1)]
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=bars)})
        outcome = self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(self.stored_days(), [START, START + timedelta(days=2)])
        self.assertTrue(any(a["kind"] == "invalid-rate" for a in outcome.anomalies))

    def test_out_of_range_date_is_dropped(self):
        bars = [_bar(START - timedelta(days=5), 7.0), _bar(START, 7.0)]
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=bars)})
        outcome = self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(self.stored_days(), [START])
        self.assertTrue(any(a["kind"] == "out-of-range" for a in outcome.anomalies))

    def test_duplicate_date_keeps_the_later_stamp(self):
        bars = [
            _bar(START, 7.0, offset_hours=0),
            _bar(START, 7.2, offset_hours=3),
        ]
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=bars)})
        outcome = self.run_update(FxRequest(["USD"], START, END), provider)
        stored = self.repo.load_fx("USD")
        self.assertEqual([point.value for point in stored.points], [7.2])
        self.assertTrue(any(a["kind"] == "duplicate-date" for a in outcome.anomalies))

    def test_jump_is_kept_and_flagged(self):
        day2 = START + timedelta(days=1)
        bars = [_bar(START, 6.0), _bar(day2, 9.4)]
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=bars)})
        outcome = self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(self.stored_days(), [START, day2])
        jump = [a for a in outcome.anomalies if a["kind"] == "jump"]
        self.assertEqual(len(jump), 1)
        self.assertAlmostEqual(jump[0]["change"], 9.4 / 6.0 - 1.0)

    def test_implausible_rate_is_kept_never_reciprocated(self):
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=[_bar(START, 30.0)])})
        outcome = self.run_update(FxRequest(["USD"], START, END), provider)
        stored = self.repo.load_fx("USD")
        self.assertEqual([point.value for point in stored.points], [30.0])
        self.assertTrue(any(a["kind"] == "implausible" for a in outcome.anomalies))
        self.assertTrue(any("quote-direction-suspect" in w for w in outcome.warnings))

    def test_inverted_quote_rejects_the_whole_series(self):
        bars = [_bar(START, 0.15), _bar(START + timedelta(days=1), 0.151)]
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=bars)})
        outcome = self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(outcome.skipped["USD"], "inverted-quote")
        self.assertEqual(self.stored_days(), [])

    def test_provider_error_skips_without_writing(self):
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", error="timeout")})
        outcome = self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(outcome.skipped["USD"], "timeout")
        self.assertEqual(self.stored_days(), [])


# --- 10.2 availability -------------------------------------------------------


class FxPipelineAvailabilityTests(PipelineTestCase):
    def setUp(self) -> None:
        super().setUp()
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=[_bar(START, 7.0)])})
        self.outcome = self.run_update(FxRequest(["USD"], START, END), provider)
        self.point = self.repo.load_fx("USD").points[0]

    def test_market_timestamp_is_the_provider_stamp(self):
        self.assertEqual(self.point.market_timestamp, _stamp(START))

    def test_available_at_is_next_london_midnight_plus_lag(self):
        self.assertEqual(self.point.available_at, infer_availability(START))

    def test_availability_source_is_inferred(self):
        self.assertIs(self.point.availability_source, AvailabilitySource.INFERRED)

    def test_availability_note_is_preserved(self):
        self.assertIn("Europe/London", self.point.availability_note)
        self.assertIn("inferred", self.point.availability_note)

    def test_fetched_at_uses_the_injected_now(self):
        self.assertEqual(self.point.fetched_at, NOW)

    def test_base_currency_is_cny(self):
        self.assertEqual(self.repo.load_fx("USD").base_currency, "CNY")

    def test_series_is_returned(self):
        self.assertIn("USD", self.outcome.series)


# --- 10.3 cache --------------------------------------------------------------


class FxPipelineCacheTests(PipelineTestCase):
    def _seed(self, pairs):
        self.repo.save_fx(_series("USD", pairs))

    def test_empty_database_downloads_the_full_window(self):
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=[_bar(START, 7.0)])})
        self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(provider.calls, [("USD", START, END)])

    def test_complete_cache_makes_no_download(self):
        self._seed([(START, 7.0), (END, 7.1)])
        provider = FakeProvider({})
        outcome = self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(provider.calls, [])
        self.assertEqual(outcome.downloads, 0)
        self.assertIn("USD", outcome.series)

    def test_partial_history_downloads_from_request_start(self):
        self._seed([(date(2026, 2, 1), 7.0), (END, 7.1)])
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=[])})
        self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(provider.calls[0][1], START)

    def test_recent_data_still_downloads_the_overlap(self):
        last = END - timedelta(days=10)
        self._seed([(START, 7.0), (last, 7.1)])
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=[])})
        self.run_update(FxRequest(["USD"], START, END), provider)
        expected = max(START, last - timedelta(days=12))
        self.assertEqual(provider.calls[0][1], expected)

    def test_prefer_cache_false_forces_a_download(self):
        self._seed([(START, 7.0), (END, 7.1)])
        provider = FakeProvider({})
        self.run_update(FxRequest(["USD"], START, END, prefer_cache=False), provider)
        self.assertEqual(provider.calls, [("USD", START, END)])

    def test_weekend_end_uses_the_previous_session(self):
        saturday = date(2026, 2, 28)
        self._seed([(START, 7.0), (previous_fx_session(saturday), 7.1)])
        provider = FakeProvider({})
        outcome = self.run_update(FxRequest(["USD"], START, saturday), provider)
        self.assertEqual(provider.calls, [])
        self.assertIn("USD", outcome.series)

    def test_missing_historical_window_is_not_a_cache_hit(self):
        self._seed([(date(2026, 2, 2), 7.0), (END, 7.1)])
        provider = FakeProvider({})
        outcome = self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0][1], START)
        self.assertEqual(outcome.downloads, 1)


# --- 10.4 overlap ------------------------------------------------------------


class FxPipelineOverlapTests(PipelineTestCase):
    def test_overlap_window_is_twelve_calendar_days(self):
        last = END - timedelta(days=3)
        self.repo.save_fx(_series("USD", [(START, 7.0), (last, 7.1)]))
        provider = FakeProvider({})
        self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(provider.calls[0][1], last - timedelta(days=12))
        self.assertEqual(provider.calls[0][2], END)

    def test_overlap_never_starts_before_the_request(self):
        self.repo.save_fx(_series("USD", [(START, 7.0), (START + timedelta(days=1), 7.1)]))
        provider = FakeProvider({})
        self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(provider.calls[0][1], START)

    def test_overlap_sessions_can_extend_the_window(self):
        last = END - timedelta(days=3)
        self.repo.save_fx(_series("USD", [(START, 7.0), (last, 7.1)]))
        provider = FakeProvider({})
        original = fx_pipeline.FX_OVERLAP_SESSIONS
        self.addCleanup(setattr, fx_pipeline, "FX_OVERLAP_SESSIONS", original)
        fx_pipeline.FX_OVERLAP_SESSIONS = 20
        self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertLess(provider.calls[0][1], last - timedelta(days=12))
        self.assertGreaterEqual((last - provider.calls[0][1]).days, 12)

    def test_overlap_calendar_days_still_set_the_floor(self):
        last = END - timedelta(days=3)
        self.repo.save_fx(_series("USD", [(START, 7.0), (last, 7.1)]))
        provider = FakeProvider({})
        self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(provider.calls[0][1], last - timedelta(days=12))

    def test_vendor_revision_creates_a_ledger_row(self):
        self.repo.save_fx(_series("USD", [(START, 7.0), (END - timedelta(days=1), 7.1)]))
        provider = FakeProvider({"USD": FxFetch(
            "USD", "CNY=X", bars=[_bar(START, 7.05), _bar(END, 7.2)])})
        outcome = self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(outcome.revisions, 1)
        self.assertEqual(self.repo.count_fx_revisions("USD"), 1)

    def test_vendor_dropped_date_keeps_the_stored_row(self):
        dropped = END - timedelta(days=1)
        self.repo.save_fx(_series("USD", [(START, 7.0), (dropped, 7.1), (END, 7.2)]))
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=[_bar(START, 7.0)])})
        self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertIn(dropped, self.stored_days())


# --- 10.5 persistence --------------------------------------------------------


class FxPipelinePersistenceTests(PipelineTestCase):
    def _fetch(self, rate=7.0):
        return FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=[_bar(START, rate)])})

    def test_first_update_inserts_rows(self):
        outcome = self.run_update(FxRequest(["USD"], START, END), self._fetch())
        self.assertEqual(outcome.inserted, 1)
        self.assertEqual(outcome.updated, 0)
        self.assertEqual(outcome.revisions, 0)

    def test_identical_second_update_adds_no_revision(self):
        self.run_update(FxRequest(["USD"], START, END), self._fetch())
        outcome = self.run_update(FxRequest(["USD"], START, END, prefer_cache=False),
                                  self._fetch())
        self.assertEqual(outcome.revisions, 0)
        self.assertEqual(self.repo.count_fx_revisions("USD"), 0)

    def test_changed_rate_lands_in_the_ledger(self):
        self.run_update(FxRequest(["USD"], START, END), self._fetch(7.0))
        outcome = self.run_update(FxRequest(["USD"], START, END, prefer_cache=False),
                                  self._fetch(7.5))
        self.assertEqual(outcome.revisions, 1)
        stored = self.repo.load_fx("USD").points[0]
        self.assertEqual(stored.value, 7.5)

    def test_repeated_identical_request_keeps_the_row_count(self):
        self.run_update(FxRequest(["USD"], START, END), self._fetch())
        before = len(self.stored_days())
        self.run_update(FxRequest(["USD"], START, END, prefer_cache=False), self._fetch())
        self.assertEqual(len(self.stored_days()), before)

    def test_every_stored_bar_has_an_availability_time(self):
        self.run_update(FxRequest(["USD"], START, END), self._fetch())
        for point in self.repo.load_fx("USD").points:
            self.assertIsNotNone(point.available_at)
            self.assertIs(point.availability_source, AvailabilitySource.INFERRED)

    def test_save_failure_does_not_affect_other_currencies(self):
        class Exploding(Repository):
            def save_fx(self, series):
                if series.currency == "EUR":
                    raise DatabaseError("boom")
                return super().save_fx(series)

        repo = Exploding(Path(self._tmp.name) / "other.db")
        self.addCleanup(repo.close)
        provider = FakeProvider({
            "USD": FxFetch("USD", "CNY=X", bars=[_bar(START, 7.0)]),
            "EUR": FxFetch("EUR", "EURCNY=X", bars=[_bar(START, 7.4)]),
        })
        outcome = update_fx(FxRequest(["USD", "EUR"], START, END), repo,
                            provider=provider, now=NOW)
        self.assertIn("USD", outcome.series)
        self.assertTrue(outcome.skipped["EUR"].startswith("database-error"))
        self.assertEqual(len(repo.load_fx("USD").points), 1)

    def test_default_provider_is_resolved_at_call_time(self):
        original = fx_pipeline.fetch_fx_bars
        self.addCleanup(setattr, fx_pipeline, "fetch_fx_bars", original)
        fx_pipeline.fetch_fx_bars = lambda code, start, end: FxFetch(
            code, "CNY=X", bars=[_bar(START, 7.0)])
        outcome = update_fx(FxRequest(["USD"], START, END), self.repo, now=NOW)
        self.assertIn("USD", outcome.series)


# --- 10.6 multi-currency -----------------------------------------------------


class FxPipelineMultiCurrencyTests(PipelineTestCase):
    def test_all_currencies_succeed(self):
        provider = FakeProvider({
            "USD": FxFetch("USD", "CNY=X", bars=[_bar(START, 7.0)]),
            "JPY": FxFetch("JPY", "JPYCNY=X", bars=[_bar(START, 0.045)]),
        })
        outcome = self.run_update(FxRequest(["USD", "JPY"], START, END), provider)
        self.assertEqual(set(outcome.series), {"USD", "JPY"})
        self.assertEqual(outcome.skipped, {})

    def test_one_timeout_does_not_block_the_others(self):
        provider = FakeProvider({
            "USD": FxFetch("USD", "CNY=X", bars=[_bar(START, 7.0)]),
            "EUR": FxFetch("EUR", "EURCNY=X", error="timeout"),
            "JPY": FxFetch("JPY", "JPYCNY=X", bars=[_bar(START, 0.045)]),
        })
        outcome = self.run_update(FxRequest(["USD", "EUR", "JPY"], START, END), provider)
        self.assertEqual(set(outcome.series), {"USD", "JPY"})
        self.assertEqual(outcome.skipped, {"EUR": "timeout"})

    def test_unsupported_between_two_working_currencies(self):
        provider = FakeProvider({
            "USD": FxFetch("USD", "CNY=X", bars=[_bar(START, 7.0)]),
            "JPY": FxFetch("JPY", "JPYCNY=X", bars=[_bar(START, 0.045)]),
        })
        outcome = self.run_update(FxRequest(["USD", "XYZ", "JPY"], START, END), provider)
        self.assertEqual(set(outcome.series), {"USD", "JPY"})
        self.assertEqual(outcome.skipped["XYZ"], "unsupported-currency")

    def test_inverted_quote_isolated_to_one_currency(self):
        provider = FakeProvider({
            "USD": FxFetch("USD", "CNY=X", bars=[_bar(START, 7.0)]),
            "CHF": FxFetch("CHF", "CHFCNY=X", bars=[_bar(START, 0.126)]),
        })
        outcome = self.run_update(FxRequest(["USD", "CHF"], START, END), provider)
        self.assertEqual(set(outcome.series), {"USD"})
        self.assertEqual(outcome.skipped["CHF"], "inverted-quote")

    def test_all_failures_are_reported(self):
        provider = FakeProvider({
            "USD": FxFetch("USD", "CNY=X", error="network-error: down"),
            "EUR": FxFetch("EUR", "EURCNY=X", error="http-500"),
        })
        outcome = self.run_update(FxRequest(["USD", "EUR"], START, END), provider)
        self.assertEqual(outcome.series, {})
        self.assertEqual(outcome.skipped, {"USD": "network-error", "EUR": "http-500"})

    def test_provider_raising_is_contained(self):
        def boom(currency, start, end):
            raise RuntimeError("kaboom")

        provider = FakeProvider({
            "USD": boom,
            "JPY": FxFetch("JPY", "JPYCNY=X", bars=[_bar(START, 0.045)]),
        })
        outcome = self.run_update(FxRequest(["USD", "JPY"], START, END), provider)
        self.assertIn("USD", outcome.skipped)
        self.assertIn("JPY", outcome.series)


# --- request/contract --------------------------------------------------------


class FxRequestTests(unittest.TestCase):
    def test_currencies_are_normalised(self):
        request = FxRequest([" usd ", "USD", "", "hkd"], START, END)
        self.assertEqual(request.currencies, ["USD", "HKD"])

    def test_datetime_is_rejected(self):
        with self.assertRaises(TypeError):
            FxRequest(["USD"], datetime(2026, 1, 5, tzinfo=UTC), END)

    def test_start_after_end_is_rejected(self):
        with self.assertRaises(ValueError):
            FxRequest(["USD"], END, START)

    def test_naive_now_is_rejected(self):
        with TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "x.db")
            try:
                with self.assertRaises(ValueError):
                    update_fx(FxRequest(["USD"], START, END), repo,
                              provider=FakeProvider({}),
                              now=datetime(2026, 2, 27, 12, 0))
            finally:
                repo.close()

    def test_session_helpers(self):
        self.assertEqual(previous_fx_session(date(2026, 2, 28)), date(2026, 2, 27))
        self.assertEqual(next_fx_session(date(2026, 2, 28)), date(2026, 3, 2))


# --- 10.7 legacy metadata enrichment ----------------------------------------


class FxPipelineLegacyMetadataTests(PipelineTestCase):
    """Date coverage alone must not hide rows that never reach ``fx_as_of()``."""

    def _seed_legacy(self, pairs):
        self.repo.save_fx(_legacy_series("USD", pairs))

    def test_date_complete_but_metadata_missing_is_not_a_cache_hit(self):
        self._seed_legacy([(START, 7.0), (END, 7.1)])
        provider = FakeProvider({})
        outcome = self.run_update(FxRequest(["USD"], START, END), provider)

        # No download: the dates are known, only the publication times are not.
        self.assertEqual(provider.calls, [])
        self.assertEqual(outcome.downloads, 0)
        self.assertIn("USD", outcome.series)

        stored = self.repo.load_fx("USD")
        self.assertEqual([point.value for point in stored.points], [7.0, 7.1])
        for point in stored.points:
            self.assertIsNotNone(point.available_at)
            self.assertIs(point.availability_source, AvailabilitySource.INFERRED)
        self.assertEqual(outcome.updated, 2)
        self.assertEqual(outcome.revisions, 0)
        self.assertEqual(self.repo.count_fx_revisions("USD"), 0)
        self.assertTrue(any("metadata-enriched" in text for text in outcome.warnings))

        pit = self.repo.fx_as_of("USD", datetime(2026, 3, 2, tzinfo=UTC))
        self.assertEqual(len(pit.points), 2)

    def test_partial_metadata_is_completed(self):
        self._seed_legacy([(START, 7.0)])
        self.repo.save_fx(_series("USD", [(START + timedelta(days=1), 7.05), (END, 7.1)]))
        provider = FakeProvider({})
        outcome = self.run_update(FxRequest(["USD"], START, END), provider)

        self.assertEqual(provider.calls, [])
        self.assertEqual(outcome.updated, 1)   # only the legacy bar was touched
        for point in self.repo.load_fx("USD").points:
            self.assertIsNotNone(point.available_at)
        self.assertEqual(self.repo.count_fx_revisions("USD"), 0)

    def test_enrichment_never_changes_a_rate(self):
        pairs = [(START, 7.0), (START + timedelta(days=1), 7.05), (END, 7.1)]
        self._seed_legacy(pairs)
        self.run_update(FxRequest(["USD"], START, END), FakeProvider({}))
        stored = self.repo.load_fx("USD")
        self.assertEqual([point.value for point in stored.points], [7.0, 7.05, 7.1])

    def test_second_update_hits_the_cache(self):
        self._seed_legacy([(START, 7.0), (END, 7.1)])
        self.run_update(FxRequest(["USD"], START, END), FakeProvider({}))
        provider = FakeProvider({})
        outcome = self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertEqual(provider.calls, [])
        self.assertEqual(outcome.updated, 0)
        self.assertEqual(outcome.revisions, 0)
        self.assertFalse(any("metadata-enriched" in text for text in outcome.warnings))


# --- 10.8 failure reporting --------------------------------------------------


class FxPipelineFailureReportingTests(PipelineTestCase):
    def _bars(self):
        return [_bar(START, 7.0), _bar(END, 7.1)]

    def test_save_failure_reports_no_inserts(self):
        provider = FakeProvider({"USD": FxFetch("USD", "CNY=X", bars=self._bars())})
        with mock.patch.object(self.repo, "save_fx", side_effect=DatabaseError("disk")):
            outcome = self.run_update(FxRequest(["USD"], START, END), provider)
        self.assertTrue(outcome.skipped["USD"].startswith("database-error"))
        self.assertEqual(outcome.inserted, 0)
        self.assertEqual(outcome.updated, 0)
        self.assertEqual(outcome.revisions, 0)
        self.assertNotIn("USD", outcome.series)
        self.assertEqual(len(self.repo.load_fx("USD").points), 0)

    def test_revision_count_failure_is_isolated(self):
        class FlakyCount(Repository):
            def count_fx_revisions(self, currency, start=None, end=None):
                if currency == "USD":
                    raise DatabaseError("count failed")
                return super().count_fx_revisions(currency, start, end)

        repo = FlakyCount(Path(self._tmp.name) / "count.db")
        self.addCleanup(repo.close)
        provider = FakeProvider({
            "USD": FxFetch("USD", "CNY=X", bars=[_bar(START, 7.0)]),
            "JPY": FxFetch("JPY", "JPYCNY=X", bars=[_bar(START, 0.045)]),
        })
        outcome = update_fx(FxRequest(["USD", "JPY"], START, END), repo,
                            provider=provider, now=NOW)
        self.assertTrue(outcome.skipped["USD"].startswith("database-error"))
        self.assertIn("JPY", outcome.series)
        self.assertEqual(len(repo.load_fx("JPY").points), 1)
        self.assertEqual(outcome.inserted, 1)
        self.assertEqual(outcome.updated, 0)

    def test_downloads_counts_the_call_even_when_the_provider_raises(self):
        def boom(currency, start, end):
            raise RuntimeError("down")

        outcome = self.run_update(FxRequest(["USD"], START, END), FakeProvider({"USD": boom}))
        self.assertEqual(outcome.downloads, 1)
        self.assertEqual(outcome.skipped["USD"], "provider-error: down")

    def test_provider_warnings_and_duplicates_reach_the_outcome(self):
        fetch = FxFetch(
            "USD", "CNY=X", bars=[_bar(START, 7.0)],
            warnings=["dropped-bars: 2"], duplicates=3,
        )
        outcome = self.run_update(FxRequest(["USD"], START, END), FakeProvider({"USD": fetch}))

        self.assertIn("USD: dropped-bars: 2", outcome.warnings)
        self.assertIn("USD: provider-duplicate-dates: 3", outcome.warnings)
        kinds = [entry["kind"] for entry in outcome.anomalies]
        self.assertIn("provider-duplicate-date", kinds)
        entry = next(
            item for item in outcome.anomalies
            if item["kind"] == "provider-duplicate-date"
        )
        self.assertEqual(entry["count"], 3)
        self.assertIsNone(entry["rate_date"])

    def test_pipeline_duplicates_keep_their_own_kind(self):
        bars = [_bar(START, 7.0), _bar(START, 7.02, offset_hours=1)]
        fetch = FxFetch("USD", "CNY=X", bars=bars)
        outcome = self.run_update(FxRequest(["USD"], START, END), FakeProvider({"USD": fetch}))

        kinds = [entry["kind"] for entry in outcome.anomalies]
        self.assertIn("duplicate-date", kinds)
        self.assertNotIn("provider-duplicate-date", kinds)


if __name__ == "__main__":
    unittest.main()
