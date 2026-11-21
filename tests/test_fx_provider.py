# -*- coding: utf-8 -*-
"""Tests for the Stage 2.3 FX provider (mock HTTP only, no live network)."""
from __future__ import annotations

import inspect
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import pandas as pd
import requests
from zoneinfo import ZoneInfo

from fa.data import fx as fx_facade
from fa.data import fx_provider
from fa.data.fx_provider import (
    FX_SANITY_BANDS,
    FxBar,
    FxFetch,
    _eu_rule_offset,
    _last_sunday,
    _london_midnight_utc,
    bar_trading_date,
    fetch_fx_bars,
    fetch_fx_bars_many,
    infer_availability,
    london_offset,
    london_today,
)


def _epoch(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp).timestamp())


def _payload(bars, currency="CNY"):
    return {
        "chart": {
            "result": [{
                "meta": {"currency": currency},
                "timestamp": [_epoch(stamp) for stamp, _ in bars],
                "indicators": {"quote": [{"close": [value for _, value in bars]}]},
            }],
            "error": None,
        }
    }


class _FakeResponse:
    def __init__(self, payload=None, status=200, body_error=None):
        self._payload = payload
        self.status_code = status
        self._body_error = body_error

    def json(self):
        if self._body_error is not None:
            raise ValueError(self._body_error)
        return self._payload


class BarTradingDateTests(unittest.TestCase):
    def test_winter_stamp_keeps_the_same_date(self):
        self.assertEqual(bar_trading_date(datetime(2026, 1, 15, tzinfo=timezone.utc)),
                         date(2026, 1, 15))

    def test_summer_stamp_at_23z_belongs_to_the_next_london_day(self):
        self.assertEqual(bar_trading_date(datetime(2026, 6, 15, 23, tzinfo=timezone.utc)),
                         date(2026, 6, 16))

    def test_bst_start_and_end_days(self):
        self.assertEqual(bar_trading_date(datetime(2026, 3, 29, 23, tzinfo=timezone.utc)),
                         date(2026, 3, 30))
        self.assertEqual(bar_trading_date(datetime(2026, 10, 25, tzinfo=timezone.utc)),
                         date(2026, 10, 25))

    def test_naive_stamp_is_rejected(self):
        with self.assertRaises(ValueError):
            bar_trading_date(datetime(2026, 11, 17))

    def test_non_datetime_is_rejected(self):
        with self.assertRaises(TypeError):
            bar_trading_date("2026-11-17")


class LondonOffsetTests(unittest.TestCase):
    def test_fallback_matches_zoneinfo_every_day_2025_to_2030(self):
        zone = ZoneInfo("Europe/London")
        day = date(2025, 1, 1)
        while day <= date(2030, 12, 31):
            for hour in (0, 12):
                moment = datetime(day.year, day.month, day.day, hour,
                                  tzinfo=timezone.utc)
                self.assertEqual(
                    _eu_rule_offset(moment),
                    moment.astimezone(zone).utcoffset(),
                    msg=f"{moment.isoformat()}",
                )
            day += timedelta(days=1)

    def test_last_sunday_of_march_and_october(self):
        self.assertEqual(_last_sunday(2026, 3).date(), date(2026, 3, 29))
        self.assertEqual(_last_sunday(2026, 10).date(), date(2026, 10, 25))
        self.assertEqual(_last_sunday(2027, 3).date(), date(2027, 3, 28))
        self.assertEqual(_last_sunday(2027, 10).date(), date(2027, 10, 31))

    def test_london_offset_prefers_zoneinfo(self):
        self.assertEqual(london_offset(datetime(2026, 7, 1, tzinfo=timezone.utc)),
                         timedelta(hours=1))
        self.assertEqual(london_offset(datetime(2026, 1, 1, tzinfo=timezone.utc)),
                         timedelta(0))


class AvailabilityTests(unittest.TestCase):
    def test_available_after_the_london_day_plus_lag(self):
        self.assertEqual(
            infer_availability(date(2026, 11, 17), lag_minutes=30),
            datetime(2026, 11, 18, 0, 30, tzinfo=timezone.utc),
        )

    def test_summer_availability_uses_bst_midnight(self):
        self.assertEqual(
            infer_availability(date(2026, 7, 1), lag_minutes=30),
            datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc),
        )

    def test_datetime_is_rejected(self):
        with self.assertRaises(TypeError):
            infer_availability(datetime(2026, 11, 17))


class ProviderParsingTests(unittest.TestCase):
    def setUp(self):
        self.start = date(2026, 11, 9)
        self.end = date(2026, 11, 20)

    def _run(self, payload, start=None, end=None):
        with mock.patch.object(fx_provider.requests, "get",
                               return_value=_FakeResponse(payload)) as called:
            fetch = fetch_fx_bars("USD", start or self.start, end or self.end)
        return fetch, called

    def test_bars_keep_the_raw_stamp_and_london_date(self):
        payload = _payload([
            ("2026-11-12T00:00:00+00:00", 6.65),
            ("2026-11-13T00:00:00+00:00", 6.66),
        ])
        fetch, _ = self._run(payload)
        self.assertIsNone(fetch.error)
        self.assertEqual([bar.rate_date for bar in fetch.bars],
                         [date(2026, 11, 12), date(2026, 11, 13)])
        self.assertEqual(fetch.bars[0].market_timestamp,
                         datetime(2026, 11, 12, tzinfo=timezone.utc))
        self.assertTrue(fetch.bars[0].market_timestamp.tzinfo is not None)

    def test_summer_bar_is_dated_in_london(self):
        payload = _payload([("2026-07-01T23:00:00+00:00", 6.65)])
        fetch, _ = self._run(payload, date(2026, 6, 25), date(2026, 7, 5))
        self.assertEqual(fetch.bars[0].rate_date, date(2026, 7, 2))

    def test_out_of_window_bars_are_dropped(self):
        payload = _payload([
            ("2026-10-01T00:00:00+00:00", 6.60),
            ("2026-11-12T00:00:00+00:00", 6.65),
        ])
        fetch, _ = self._run(payload)
        self.assertEqual(len(fetch.bars), 1)

    def test_duplicate_dates_keep_the_last_and_count(self):
        payload = _payload([
            ("2026-11-12T00:00:00+00:00", 6.60),
            ("2026-11-12T00:00:00+00:00", 6.70),
        ])
        fetch, _ = self._run(payload)
        self.assertEqual(len(fetch.bars), 1)
        self.assertEqual(fetch.bars[0].rate, 6.70)
        self.assertEqual(fetch.duplicates, 1)
        self.assertIn("duplicate-dates: 1", fetch.warnings)

    def test_bad_closes_are_dropped_without_killing_the_payload(self):
        payload = _payload([
            ("2026-11-10T00:00:00+00:00", None),
            ("2026-11-11T00:00:00+00:00", 0.0),
            ("2026-11-12T00:00:00+00:00", -1.0),
            ("2026-11-13T00:00:00+00:00", 6.65),
        ])
        fetch, _ = self._run(payload)
        self.assertEqual(len(fetch.bars), 1)
        self.assertIn("dropped-bars: 3", fetch.warnings)

    def test_direction_suspect_is_flagged_and_never_reciprocated(self):
        payload = _payload([("2026-11-12T00:00:00+00:00", 0.15)])
        fetch, _ = self._run(payload)
        self.assertTrue(fetch.error.startswith("quote-direction-suspect"))
        self.assertEqual(fetch.bars[0].rate, 0.15)

    def test_non_cny_quote_is_rejected(self):
        payload = _payload([("2026-11-12T00:00:00+00:00", 6.65)], currency="HKD")
        fetch, _ = self._run(payload)
        self.assertTrue(fetch.error.startswith("non-cny-quote"))
        self.assertEqual(fetch.bars, [])

    def test_http_error_is_reported(self):
        with mock.patch.object(fx_provider.requests, "get",
                               return_value=_FakeResponse(None, status=500)):
            fetch = fetch_fx_bars("USD", self.start, self.end)
        self.assertEqual(fetch.error, "http-500")

    def test_timeout_is_reported(self):
        with mock.patch.object(fx_provider.requests, "get",
                               side_effect=requests.Timeout()):
            fetch = fetch_fx_bars("USD", self.start, self.end)
        self.assertEqual(fetch.error, "timeout")

    def test_network_error_is_reported(self):
        with mock.patch.object(fx_provider.requests, "get",
                               side_effect=requests.ConnectionError("boom")):
            fetch = fetch_fx_bars("USD", self.start, self.end)
        self.assertTrue(fetch.error.startswith("network-error"))

    def test_invalid_json_is_reported(self):
        with mock.patch.object(fx_provider.requests, "get",
                               return_value=_FakeResponse(body_error="nope")):
            fetch = fetch_fx_bars("USD", self.start, self.end)
        self.assertEqual(fetch.error, "invalid-json")

    def test_empty_result_is_reported(self):
        payload = {"chart": {"result": [], "error": {"description": "No data"}}}
        fetch, _ = self._run(payload)
        self.assertTrue(fetch.error.startswith("no-result"))

    def test_no_bars_is_reported(self):
        payload = _payload([])
        fetch, _ = self._run(payload)
        self.assertEqual(fetch.error, "no-bars")


class ProviderContractTests(unittest.TestCase):
    def test_cny_is_a_noop_without_download(self):
        with mock.patch.object(fx_provider.requests, "get") as called:
            fetch = fetch_fx_bars("CNY", date(2026, 11, 1), date(2026, 11, 20))
        self.assertEqual(fetch.bars, [])
        self.assertIsNone(fetch.error)
        called.assert_not_called()

    def test_unsupported_currency_is_reported_without_download(self):
        with mock.patch.object(fx_provider.requests, "get") as called:
            fetch = fetch_fx_bars("XXX", date(2026, 11, 1), date(2026, 11, 20))
        self.assertEqual(fetch.error, "unsupported-currency")
        called.assert_not_called()

    def test_empty_currency_is_reported(self):
        self.assertEqual(fetch_fx_bars("", date(2026, 11, 1), date(2026, 11, 2)).error,
                         "missing-currency")

    def test_reversed_window_is_rejected(self):
        with self.assertRaises(ValueError):
            fetch_fx_bars("USD", date(2026, 11, 20), date(2026, 11, 1))

    def test_many_skips_cny_and_duplicates(self):
        with mock.patch.object(fx_provider, "fetch_fx_bars",
                               side_effect=lambda code, start, end, timeout=None:
                               FxFetch(currency=code, symbol="X")) as called:
            out = fetch_fx_bars_many(["usd", "USD", "CNY", "", "hkd"],
                                     date(2026, 11, 1), date(2026, 11, 20))
        self.assertEqual(sorted(out), ["HKD", "USD"])
        self.assertEqual(called.call_count, 2)

    def test_signatures_stay_stable(self):
        params = list(inspect.signature(fetch_fx_bars).parameters)
        self.assertEqual(params, ["currency", "start", "end", "timeout"])
        self.assertEqual(list(inspect.signature(fetch_fx_bars_many).parameters),
                         ["currencies", "start", "end", "timeout"])

    def test_every_band_contains_a_plausible_rate(self):
        for code, (low, high) in FX_SANITY_BANDS.items():
            self.assertLess(low, high, msg=code)


class FacadeTests(unittest.TestCase):
    def setUp(self):
        self.bars = [
            FxBar(rate_date=date(2026, 7, 1), rate=6.65,
                  market_timestamp=datetime(2026, 7, 1, 23, tzinfo=timezone.utc)),
            FxBar(rate_date=date(2026, 7, 2), rate=6.66,
                  market_timestamp=datetime(2026, 7, 2, 23, tzinfo=timezone.utc)),
        ]

    def test_series_is_indexed_by_london_dates(self):
        fetch = FxFetch(currency="USD", symbol="CNY=X", bars=self.bars)
        with mock.patch.object(fx_facade, "fetch_fx_bars", return_value=fetch):
            series = fx_facade.fetch_fx_series("USD", start="2026-06-25",
                                               end="2026-07-05")
        self.assertIsNotNone(series)
        self.assertEqual([ts.date() for ts in series.index],
                         [date(2026, 7, 1), date(2026, 7, 2)])

    def test_series_is_none_for_cny(self):
        self.assertIsNone(fx_facade.fetch_fx_series("CNY"))

    def test_series_is_none_when_download_fails(self):
        fetch = FxFetch(currency="USD", symbol="CNY=X", error="timeout")
        with mock.patch.object(fx_facade, "fetch_fx_bars", return_value=fetch):
            self.assertIsNone(fx_facade.fetch_fx_series("USD"))

    def test_rates_map_skips_cny(self):
        fetch = FxFetch(currency="USD", symbol="CNY=X", bars=self.bars)
        with mock.patch.object(fx_facade, "fetch_fx_bars_many",
                               return_value={"USD": fetch}):
            rates = fx_facade.fetch_fx_rates(["USD", "CNY"])
        self.assertEqual(sorted(rates), ["USD"])

    def test_fetch_map_keeps_time_fields(self):
        fetch = FxFetch(currency="USD", symbol="CNY=X", bars=self.bars)
        series = fx_facade.fx_series_map_from_fetch({"USD": fetch}, lag_minutes=30)
        bars = series["USD"].bars
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].available_at,
                         datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc))
        self.assertEqual(bars[0].availability_source.value, "inferred")
        self.assertIn("inferred", bars[0].availability_note)
        self.assertEqual(bars[0].market_timestamp,
                         datetime(2026, 7, 1, 23, tzinfo=timezone.utc))

    def test_fetch_map_drops_failed_currencies(self):
        failed = FxFetch(currency="CHF", symbol="CHFCNY=X", error="timeout")
        good = FxFetch(currency="USD", symbol="CNY=X", bars=self.bars)
        series = fx_facade.fx_series_map_from_fetch({"CHF": failed, "USD": good})
        self.assertEqual(sorted(series), ["USD"])

    def test_v22_map_still_works(self):
        frame = pd.Series([6.65, 6.66],
                          index=pd.DatetimeIndex(["2026-07-01", "2026-07-02"]))
        mapped = fx_facade.fx_series_map({"USD": frame})
        self.assertEqual(len(mapped["USD"].points), 2)

    def test_facade_keeps_the_old_signature(self):
        self.assertEqual(list(inspect.signature(fx_facade.fetch_fx_series).parameters),
                         ["currency", "range_", "start", "end"])


class RequestWindowTests(unittest.TestCase):
    """The Yahoo window must be expressed in London midnights (review P1)."""

    def _params(self, start, end):
        payload = _payload([])
        with mock.patch.object(fx_provider.requests, "get",
                               return_value=_FakeResponse(payload)) as called:
            fetch_fx_bars("USD", start, end)
        return called.call_args.kwargs["params"]

    def test_winter_window_starts_and_ends_at_utc_midnight(self):
        params = self._params(date(2026, 1, 5), date(2026, 1, 6))
        self.assertEqual(params["period1"], _epoch("2026-01-05T00:00:00+00:00"))
        self.assertEqual(params["period2"], _epoch("2026-01-07T00:00:00+00:00"))

    def test_summer_window_covers_the_london_first_and_last_bar(self):
        params = self._params(date(2026, 7, 1), date(2026, 7, 3))
        # 2026-07-01 00:00 London is 2026-06-30T23:00Z; the bar stamp of the
        # first day would fall outside a UTC-midnight period1.
        self.assertEqual(params["period1"], _epoch("2026-06-30T23:00:00+00:00"))
        self.assertEqual(params["period2"], _epoch("2026-07-03T23:00:00+00:00"))

    def test_first_day_of_bst_uses_the_gmt_midnight(self):
        params = self._params(date(2026, 3, 29), date(2026, 3, 29))
        self.assertEqual(params["period1"], _epoch("2026-03-29T00:00:00+00:00"))
        self.assertEqual(params["period2"], _epoch("2026-03-29T23:00:00+00:00"))

    def test_last_day_of_bst_uses_the_bst_midnight(self):
        params = self._params(date(2026, 10, 25), date(2026, 10, 25))
        # The 25th starts under BST, but the 26th starts under GMT.
        self.assertEqual(params["period1"], _epoch("2026-10-24T23:00:00+00:00"))
        self.assertEqual(params["period2"], _epoch("2026-10-26T00:00:00+00:00"))

    def test_window_is_wide_enough_for_every_day_of_the_year(self):
        day = date(2026, 1, 1)
        while day <= date(2026, 12, 31):
            begin = _london_midnight_utc(day)
            end = _london_midnight_utc(day + timedelta(days=1))
            self.assertLess(begin, end, msg=str(day))
            # A London day is 23, 24 or 25 hours around the DST switches; it
            # must never be shorter than 23.
            self.assertGreaterEqual(end - begin, timedelta(hours=23), msg=str(day))
            day += timedelta(days=1)


class FallbackMidnightTests(unittest.TestCase):
    """Without a tz database the fallback must still find London midnight."""

    def _force_fallback(self):
        patcher_a = mock.patch.object(fx_provider, "_LONDON", None)
        patcher_b = mock.patch.object(fx_provider, "_LONDON_FAILED", True)
        patcher_a.start()
        patcher_b.start()
        self.addCleanup(patcher_a.stop)
        self.addCleanup(patcher_b.stop)

    def test_eu_rule_fallback_agrees_with_zoneinfo_for_a_decade(self):
        self._force_fallback()
        zone = ZoneInfo("Europe/London")
        day = date(2025, 1, 1)
        while day <= date(2030, 12, 31):
            expected = datetime.combine(day, datetime.min.time(),
                                        tzinfo=zone).astimezone(timezone.utc)
            self.assertEqual(_london_midnight_utc(day), expected, msg=str(day))
            day += timedelta(days=1)

    def test_bst_start_day_midnight_is_gmt(self):
        self._force_fallback()
        self.assertEqual(_london_midnight_utc(date(2026, 3, 29)),
                         datetime(2026, 3, 29, tzinfo=timezone.utc))

    def test_bst_end_day_midnight_is_bst(self):
        self._force_fallback()
        self.assertEqual(_london_midnight_utc(date(2026, 10, 25)),
                         datetime(2026, 10, 24, 23, tzinfo=timezone.utc))

    def test_summer_midnight_is_previous_utc_day_23h(self):
        self._force_fallback()
        self.assertEqual(_london_midnight_utc(date(2026, 7, 1)),
                         datetime(2026, 6, 30, 23, tzinfo=timezone.utc))

    def test_availability_on_transition_day_uses_the_correct_offset(self):
        self._force_fallback()
        self.assertEqual(infer_availability(date(2026, 10, 24), 30),
                         datetime(2026, 10, 24, 23, 30, tzinfo=timezone.utc))
        self.assertEqual(infer_availability(date(2026, 3, 28), 30),
                         datetime(2026, 3, 29, 0, 30, tzinfo=timezone.utc))

    def test_london_today_follows_london_not_utc(self):
        self.assertEqual(london_today(),
                         datetime.now(ZoneInfo("Europe/London")).date())

    def test_london_today_falls_back_without_zoneinfo(self):
        self._force_fallback()
        self.assertEqual(london_today(),
                         (datetime.now(timezone.utc)
                          + _eu_rule_offset(datetime.now(timezone.utc))).date())


class FacadeWindowTests(unittest.TestCase):
    def test_default_end_is_today_in_london(self):
        with mock.patch.object(fx_facade, "london_today",
                               return_value=date(2026, 7, 2)):
            begin, stop = fx_facade._window("1mo", None, None)
        self.assertEqual(stop, date(2026, 7, 2))
        self.assertEqual(begin, date(2026, 5, 23))

    def test_explicit_end_wins_over_london_today(self):
        with mock.patch.object(fx_facade, "london_today",
                               return_value=date(2026, 7, 2)):
            _, stop = fx_facade._window("1mo", None, "2026-01-10")
        self.assertEqual(stop, date(2026, 1, 10))

    def test_series_request_uses_the_london_window(self):
        fetch = FxFetch(currency="USD", symbol="CNY=X", bars=[])
        with mock.patch.object(fx_facade, "london_today",
                               return_value=date(2026, 7, 2)), \
                mock.patch.object(fx_facade, "fetch_fx_bars",
                                  return_value=fetch) as called:
            fx_facade.fetch_fx_series("USD")
        called.assert_called_once_with("USD", date(2026, 5, 23), date(2026, 7, 2))


if __name__ == "__main__":
    unittest.main()
