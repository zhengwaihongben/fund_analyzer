# -*- coding: utf-8 -*-
"""Stage B: PIT-only data access and the no-network-import guarantee."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fa.backtesting import (
    BacktestDataError,
    BacktestSpecError,
    DataAccess,
    Instrument,
)
from fa.data.database.repository import Repository
from fa.data.price_pipeline import infer_availability
from fa.models.market_data import (
    AvailabilitySource,
    FxSeries,
    PricePoint,
    PriceSeries,
)
from tests.fixtures.backtesting import (
    NOW,
    build_store,
    hk_instrument,
    price_point,
    price_series,
    trading_days,
    us_instrument,
)

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "fa" / "backtesting"
UTC = timezone.utc


class DataAccessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "backtest.db")
        self.addCleanup(self.repository.close)
        self.store = build_store(self.repository, days=40)
        self.access = DataAccess(self.repository)


class PriceAccessTests(DataAccessTestCase):
    def test_returns_the_visible_series(self) -> None:
        series = self.access.prices(self.store.us, NOW)
        self.assertEqual(len(series.points), len(self.store.days))
        self.assertEqual(series.market, "US")
        self.assertEqual(series.currency, "USD")

    def test_rows_without_publication_time_are_invisible(self) -> None:
        legacy = Instrument(ticker="OLD", market="US", currency="USD")
        self.repository.save_prices(PriceSeries.from_points(
            "OLD",
            [price_point(self.store.days[0], 50.0, ticker="OLD",
                         available_at=None, source=AvailabilitySource.UNKNOWN)],
            market="US", currency="USD", source="legacy",
        ))
        with self.assertRaises(BacktestDataError):
            self.access.prices(legacy, NOW)

    def test_price_at_returns_one_session(self) -> None:
        session = self.store.days[5]
        point = self.access.price_at(self.store.hk, NOW, session)
        self.assertEqual(point.price_date, session)
        self.assertEqual(point.close, 410.0)

    def test_price_at_missing_session_raises(self) -> None:
        with self.assertRaises(BacktestDataError):
            self.access.price_at(self.store.us, NOW, self.store.days[-1] + timedelta(days=3))

    def test_price_before_publication_is_not_usable(self) -> None:
        early = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)
        with self.assertRaises(BacktestDataError):
            self.access.prices(self.store.us, early)

    def test_market_mismatch_is_rejected(self) -> None:
        ticker = "ROP SW"
        rows = [
            price_point(day, 250.0, ticker=ticker, market="SIX", currency="CHF")
            for day in self.store.days[:5]
        ]
        self.repository.save_prices(PriceSeries.from_points(
            ticker, rows, market="SIX", currency="CHF", source="fixture",
        ))
        with self.assertRaises(BacktestDataError) as context:
            self.access.prices(Instrument(ticker, market="US", currency="CHF"), NOW)
        self.assertIn("市場", str(context.exception))

    def test_currency_mismatch_is_rejected(self) -> None:
        with self.assertRaises(BacktestDataError) as context:
            self.access.prices(Instrument("LLY", market="US", currency="CHF"), NOW)
        self.assertIn("幣別", str(context.exception))

    def test_non_pit_reader_is_never_used(self) -> None:
        def forbidden(*args, **kwargs):
            raise AssertionError("DataAccess 不可呼叫 load_prices()")

        self.repository.load_prices = forbidden  # type: ignore[assignment]
        self.assertEqual(len(self.access.prices(self.store.us, NOW).points),
                         len(self.store.days))


class FxAccessTests(DataAccessTestCase):
    def test_cny_needs_no_series(self) -> None:
        self.assertIsNone(self.access.fx("CNY", NOW))
        self.assertEqual(self.access.fx_rate("CNY", NOW, self.store.days[0]),
                         (1.0, self.store.days[0]))

    def test_foreign_series_is_visible(self) -> None:
        series = self.access.fx("USD", NOW)
        self.assertIsNotNone(series)
        self.assertEqual(series.base_currency, "CNY")
        rate, day = self.access.fx_rate("USD", NOW, self.store.days[3])
        self.assertAlmostEqual(rate, 7.10)
        self.assertEqual(day, self.store.days[3])

    def test_hkd_series_is_separate(self) -> None:
        rate, _ = self.access.fx_rate("HKD", NOW, self.store.days[0])
        self.assertAlmostEqual(rate, 0.91)


class OfficialNavTests(DataAccessTestCase):
    def test_history_is_returned_for_reporting(self) -> None:
        rows = self.access.official_nav_history("000369")
        self.assertEqual(len(rows), len(self.store.days))
        self.assertIsInstance(rows[0][0], type(self.store.days[0]))
        self.assertIsInstance(rows[0][1], float)

    def test_empty_code_is_rejected(self) -> None:
        with self.assertRaises(BacktestDataError):
            self.access.official_nav_history("")


class RevisionIdentityTests(DataAccessTestCase):
    def test_pit_row_carries_the_revision_that_produced_it(self) -> None:
        session = self.store.days[2]
        original = self.access.price_at(self.store.us, NOW, session)
        self.assertEqual(original.revision_id, 0)

        revised = [
            price_point(day, 100.0 + index * 1.0 + 5.0, ticker="LLY")
            for index, day in enumerate(self.store.days)
        ]
        self.repository.save_prices(PriceSeries.from_points(
            "LLY", revised, market="US", currency="USD", source="fixture",
        ))

        before = self.access.price_at(self.store.us, NOW, session)
        self.assertAlmostEqual(before.close, 102.0)
        self.assertGreater(before.revision_id, 0)

        after = self.access.price_at(
            self.store.us, datetime.now(UTC) + timedelta(seconds=5), session)
        self.assertAlmostEqual(after.close, 107.0)
        self.assertEqual(after.revision_id, 0)

    def test_revision_rows_helper(self) -> None:
        series = self.access.prices(self.store.us, NOW)
        rows = DataAccess.revision_rows(series)
        self.assertEqual(len(rows), len(series.points))
        self.assertTrue(all(row[2] == 0 for row in rows))


class IsolationTests(unittest.TestCase):
    def test_package_does_not_import_the_network_stack(self) -> None:
        code = (
            "import sys; import fa.backtesting; "
            "print(sorted(m for m in ('requests','yfinance','akshare') if m in sys.modules))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=str(ROOT),
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]", result.stdout)

    def test_sources_contain_no_sql_and_no_non_pit_reader(self) -> None:
        # 以 AST 檢查「真的匯入/呼叫」什麼，而不是掃字串：docstring 提到
        # requests 是為了說明「不可以匯入它」，不該被當成違規。
        import ast

        banned_modules = {"requests", "yfinance", "akshare", "sqlite3", "urllib3"}
        banned_calls = {"execute", "executemany", "executescript", "load_prices"}
        for path in sorted(PACKAGE.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        self.assertNotIn(root, banned_modules,
                                         f"{path.name} 匯入了 {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".")[0]
                    self.assertNotIn(root, banned_modules,
                                     f"{path.name} 匯入了 {node.module}")
                elif isinstance(node, ast.Call):
                    name = ""
                    if isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    elif isinstance(node.func, ast.Name):
                        name = node.func.id
                    self.assertNotIn(name, banned_calls,
                                     f"{path.name} 呼叫了 {name}()")

    def test_public_names_survive_lazy_imports(self) -> None:
        import fa

        for name in ("analyze_fund", "estimate_qdii_nav", "calculate_asset"):
            self.assertIn(name, fa.__all__)
            self.assertTrue(callable(getattr(fa, name)))
        self.assertIn("to_yf_ticker", dir(fa))


class FxFallbackTestCase(DataAccessTestCase):
    """FX-01…FX-05: a missing FX session must fall back to the latest earlier rate."""

    def _fx_point(self, day, rate, *, available_at=None):
        when = available_at
        if when is None:
            when = infer_availability(day, "FX")[0]
        return PricePoint(
            price_date=day, close=rate,
            market_timestamp=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
            available_at=when, availability_source=AvailabilitySource.INFERRED,
            fetched_at=NOW, market="FX", currency="CNY",
            available_price_type="fx_close", used_price_type="fx_close",
            source="fixture",
        )

    def _save(self, currency, points):
        self.repository.save_fx(FxSeries.from_bars(
            currency, points, source="fixture", base_currency="CNY"))

    def test_fx_01_uses_the_rate_of_the_same_session(self) -> None:
        monday = self.store.days[0]
        self._save("EUR", [self._fx_point(monday, 7.90)])
        self.assertEqual(self.access.fx_rate("EUR", NOW, monday), (7.90, monday))

    def test_fx_02_missing_session_falls_back_to_the_previous_rate(self) -> None:
        monday, tuesday = self.store.days[0], self.store.days[1]
        self._save("EUR", [self._fx_point(monday, 7.90)])
        self.assertEqual(self.access.fx_rate("EUR", NOW, tuesday), (7.90, monday))

    def test_fx_03_gap_between_two_sessions_uses_the_earlier_one(self) -> None:
        monday, tuesday, wednesday = self.store.days[0], self.store.days[1], self.store.days[2]
        self._save("CHF", [self._fx_point(monday, 8.00), self._fx_point(wednesday, 8.05)])
        self.assertEqual(self.access.fx_rate("CHF", NOW, tuesday), (8.00, monday))
        self.assertEqual(self.access.fx_rate("CHF", NOW, wednesday), (8.05, wednesday))

    def test_fx_04_unpublished_rate_is_never_used_as_a_fallback(self) -> None:
        monday, tuesday = self.store.days[0], self.store.days[1]
        future = NOW + timedelta(hours=1)
        self._save("JPY", [self._fx_point(tuesday, 0.048, available_at=future)])
        self.assertIsNone(self.access.fx_rate("JPY", NOW, tuesday))
        # 公開之後同一筆就能用，證明被擋下的是 publication gate 而不是資料本身。
        self.assertEqual(self.access.fx_rate("JPY", future, tuesday), (0.048, tuesday))

    def test_fx_05_no_earlier_rate_returns_none(self) -> None:
        wednesday = self.store.days[2]
        self._save("CAD", [self._fx_point(wednesday, 4.60)])
        self.assertIsNone(self.access.fx_rate("CAD", NOW, self.store.days[0]))


class MarketMetadataTests(DataAccessTestCase):
    def _save_series(self, ticker, rows, *, market, currency):
        points = [
            price_point(day, close, ticker=ticker, market=market, currency=currency)
            for day, close in rows
        ]
        self.repository.save_prices(PriceSeries.from_points(
            ticker, points, market=market, currency=currency, source="fixture"))

    def test_market_01_instrument_requires_a_market(self) -> None:
        with self.assertRaises(BacktestSpecError):
            Instrument(ticker="LLY", market="", currency="USD")

    def test_market_02_point_without_market_is_rejected(self) -> None:
        self._save_series("NOMKT", [(self.store.days[0], 10.0)], market="", currency="USD")
        with self.assertRaises(BacktestDataError) as context:
            self.access.prices(Instrument("NOMKT", market="US", currency="USD"), NOW)
        self.assertIn("沒有標示", str(context.exception))

    def test_market_03_mixed_venues_are_rejected(self) -> None:
        ticker = "MIXED"
        self._save_series(ticker, [(self.store.days[0], 10.0)], market="US", currency="USD")
        self._save_series(ticker, [(self.store.days[1], 11.0)], market="SIX", currency="USD")
        with self.assertRaises(BacktestDataError) as context:
            self.access.prices(Instrument(ticker, market="US", currency="USD"), NOW)
        self.assertIn("不一致", str(context.exception))

    def test_normalised_market_codes_still_compare_equal(self) -> None:
        ticker = "ALIAS"
        self._save_series(ticker, [(self.store.days[0], 10.0)], market="NYQ", currency="USD")
        series = self.access.prices(Instrument(ticker, market="US", currency="USD"), NOW)
        self.assertEqual(len(series.points), 1)


class CurrencyMetadataTests(DataAccessTestCase):
    def _save_series(self, ticker, rows, *, currency, market="US"):
        points = [
            price_point(day, close, ticker=ticker, market=market, currency=currency)
            for day, close in rows
        ]
        self.repository.save_prices(PriceSeries.from_points(
            ticker, points, market=market, currency=currency, source="fixture"))

    def test_currency_01_point_without_currency_is_rejected(self) -> None:
        self._save_series("NOCUR", [(self.store.days[0], 10.0)], currency="")
        with self.assertRaises(BacktestDataError) as context:
            self.access.prices(Instrument("NOCUR", market="US", currency="USD"), NOW)
        self.assertIn("計價幣別", str(context.exception))

    def test_currency_02_second_point_in_another_currency_is_rejected(self) -> None:
        ticker = "DRIFT"
        self._save_series(ticker, [(self.store.days[0], 10.0)], currency="USD")
        self._save_series(ticker, [(self.store.days[1], 11.0)], currency="HKD")
        with self.assertRaises(BacktestDataError) as context:
            self.access.prices(Instrument(ticker, market="US", currency="USD"), NOW)
        self.assertIn("計價幣別", str(context.exception))

    def test_currency_03_consistent_series_passes(self) -> None:
        series = self.access.prices(self.store.us, NOW)
        self.assertTrue(all(point.currency == "USD" for point in series.points))


if __name__ == "__main__":
    unittest.main()
