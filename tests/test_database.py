# -*- coding: utf-8 -*-
"""V2 SQLite layer tests: schema, migrations, repository round-trips."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fa.data.database import (
    SCHEMA_VERSION,
    DuplicateRunError,
    Repository,
    connect,
    schema_version,
)
from fa.data.database.connection import DatabaseError
from fa.models import (
    Asset,
    AssetType,
    AvailabilitySource,
    EstimationResult,
    EstimationRun,
    FxSeries,
    HoldingSnapshot,
    PortfolioStatus,
    PricePoint,
    PriceSeries,
)


def make_snapshot(
    quarter: str = "2026Q3",
    disclosure=(2026, 10, 21),
    holding_day=(2026, 9, 30),
) -> HoldingSnapshot:
    holding_date = date(*holding_day)
    return HoldingSnapshot(
        fund_code="000369",
        quarter=quarter,
        holding_date=holding_date,
        disclosure_date=date(*disclosure),
        source="unit-test",
        assets=[
            Asset(
                asset_id="LLY",
                name="礼来",
                asset_type=AssetType.EQUITY,
                ticker="LLY",
                market="US",
                currency="USD",
                weight=0.4,
                holding_date=holding_date,
            ),
            Asset(
                asset_id="ROP.SW",
                name="罗氏控股",
                asset_type=AssetType.EQUITY,
                ticker="ROP.SW",
                market="CH",
                currency="CHF",
                weight=0.2,
                holding_date=holding_date,
            ),
        ],
    )


def make_prices(ticker: str = "LLY") -> PriceSeries:
    points = [
        PricePoint(price_date=date(2026, 11, 12), close=800.0, adj_close=800.0, market="US", currency="USD"),
        PricePoint(price_date=date(2026, 11, 13), close=810.0, adj_close=810.0, market="US", currency="USD"),
    ]
    return PriceSeries.from_points(ticker, points, market="US", currency="USD", source="unit-test")


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "test.db"
        self.repo = Repository(self.path)
        self.addCleanup(self.repo.close)


class SchemaTests(unittest.TestCase):
    def test_fresh_database_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fresh.db"
            conn = connect(path)
            try:
                self.assertEqual(schema_version(conn), SCHEMA_VERSION)
                names = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                for table in (
                    "funds",
                    "assets",
                    "holdings_snapshots",
                    "holdings",
                    "market_prices",
                    "fx_rates",
                    "official_nav",
                    "estimation_runs",
                    "estimation_inputs",
                    "estimation_results",
                    "backtest_results",
                    "schema_version",
                ):
                    self.assertIn(table, names)
            finally:
                conn.close()

    def test_reconnect_does_not_reapply_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reconnect.db"
            first = connect(path)
            try:
                self.assertEqual(schema_version(first), SCHEMA_VERSION)
            finally:
                first.close()
            with self.assertNoLogs("fa.data.database.connection", level="INFO"):
                second = connect(path)
            try:
                self.assertEqual(schema_version(second), SCHEMA_VERSION)
            finally:
                second.close()


class FundAndSnapshotTests(DatabaseTestCase):
    def test_upsert_fund(self) -> None:
        self.repo.upsert_fund("000369", name="广发全球医疗", fund_type="QDII", is_qdii=True, source="test")
        fund = self.repo.load_fund("000369")
        self.assertEqual(fund["name"], "广发全球医疗")
        self.assertEqual(fund["is_qdii"], 1)
        self.repo.upsert_fund("000369", name="改名")
        self.assertEqual(self.repo.load_fund("000369")["name"], "改名")
        self.assertIsNone(self.repo.load_fund("999999"))

    def test_save_and_load_snapshot(self) -> None:
        snapshot_id = self.repo.save_snapshot(make_snapshot())
        self.assertTrue(snapshot_id)

        loaded = self.repo.load_snapshots("000369")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].quarter, "2026Q3")
        self.assertEqual(loaded[0].disclosure_date, date(2026, 10, 21))
        self.assertEqual(len(loaded[0].assets), 2)
        self.assertAlmostEqual(loaded[0].total_weight, 0.6)
        tickers = {asset.ticker for asset in loaded[0].assets}
        self.assertEqual(tickers, {"LLY", "ROP.SW"})

    def test_snapshot_as_of_is_point_in_time(self) -> None:
        self.repo.save_snapshot(
            make_snapshot("2026Q2", disclosure=(2026, 7, 21), holding_day=(2026, 6, 30))
        )
        self.repo.save_snapshot(make_snapshot("2026Q3", disclosure=(2026, 10, 21)))

        # Before the Q3 disclosure only Q2 is public.
        as_of = self.repo.snapshot_as_of("000369", date(2026, 10, 20))
        self.assertEqual(as_of.quarter, "2026Q2")

        as_of = self.repo.snapshot_as_of("000369", date(2026, 10, 21))
        self.assertEqual(as_of.quarter, "2026Q3")

        self.assertIsNone(self.repo.snapshot_as_of("000369", date(2026, 1, 1)))

    def test_snapshot_validation_is_enforced(self) -> None:
        snapshot = make_snapshot()
        snapshot.assets.append(snapshot.assets[0])
        with self.assertRaises(Exception):
            self.repo.save_snapshot(snapshot)


class MarketDataTests(DatabaseTestCase):
    def test_prices_round_trip(self) -> None:
        written = self.repo.save_prices(make_prices())
        self.assertEqual(written, 2)

        loaded = self.repo.load_prices("LLY")
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded.last_date, date(2026, 11, 13))
        self.assertEqual(loaded.market, "US")
        self.assertEqual(loaded.currency, "USD")
        value, day = loaded.price_on_or_before(date(2026, 11, 13))
        self.assertEqual(day, date(2026, 11, 13))
        self.assertAlmostEqual(value, 810.0)

    def test_prices_upsert_on_same_key(self) -> None:
        self.repo.save_prices(make_prices())
        updated = make_prices()
        updated.points[-1].adj_close = 811.5
        self.repo.save_prices(updated)
        loaded = self.repo.load_prices("LLY")
        self.assertEqual(len(loaded), 2)
        self.assertAlmostEqual(loaded.points[-1].value, 811.5)

    def test_price_range_filter(self) -> None:
        self.repo.save_prices(make_prices())
        loaded = self.repo.load_prices("LLY", start=date(2026, 11, 13))
        self.assertEqual(len(loaded), 1)

    def test_used_price_type_does_not_rewrite_the_stored_available_type(self) -> None:
        # market_prices.price_type 記錄來源提供什麼；計算過後 used_price_type
        # 可能被填成 close，不能因此把資料庫的 key 改掉。
        prices = make_prices()
        for point in prices.points:
            point.used_price_type = "close"
        self.repo.save_prices(prices)
        self.repo.save_prices(prices)
        with self.repo._conn:  # noqa: SLF001 - 測試直接看表
            rows = self.repo._conn.execute(  # noqa: SLF001
                "SELECT price_type FROM market_prices ORDER BY price_date"
            ).fetchall()
        self.assertEqual([row[0] for row in rows], ["close+adj_close", "close+adj_close"])

    def test_upsert_without_timestamps_keeps_the_legacy_columns(self) -> None:
        # 舊讀取路徑只認 price_timestamp / retrieved_at。新點沒有帶時間時，
        # upsert 不可以把它們清成 NULL（COALESCE，不是直接覆蓋）。
        moment = datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc)
        stamped = make_prices()
        for point in stamped.points:
            point.market_timestamp = moment
        self.repo.save_prices(stamped)

        bare = make_prices()  # 沒有 market_timestamp / fetched_at
        self.repo.save_prices(bare)

        rows = self.repo._conn.execute(  # noqa: SLF001 - 測試直接看表
            "SELECT price_timestamp, retrieved_at, market_timestamp FROM market_prices"
        ).fetchall()
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["price_timestamp"], "2026-11-13T21:00:00Z")
            self.assertIsNotNone(row["retrieved_at"])
            self.assertEqual(row["market_timestamp"], "2026-11-13T21:00:00Z")

    def test_fx_without_base_currency_is_rejected(self) -> None:
        fx = FxSeries.from_pairs("USD", [(date(2026, 11, 12), 7.10)])
        fx.base_currency = ""
        with self.assertRaises(DatabaseError):
            self.repo.save_fx(fx)
        rows = self.repo._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) AS n FROM fx_rates"
        ).fetchone()
        self.assertEqual(rows["n"], 0)

    def test_fx_with_non_cny_base_is_rejected(self) -> None:
        fx = FxSeries.from_pairs("USD", [(date(2026, 11, 12), 7.10)], base_currency="EUR")
        with self.assertRaises(DatabaseError):
            self.repo.save_fx(fx)
        rows = self.repo._conn.execute(  # noqa: SLF001 - 測試直接看表
            "SELECT COUNT(*) AS n FROM fx_rates"
        ).fetchone()
        self.assertEqual(rows["n"], 0)

    def _stored(self, column: str) -> list:
        rows = self.repo._conn.execute(  # noqa: SLF001 - 測試直接看最終狀態
            f"SELECT {column} FROM market_prices ORDER BY price_date"
        ).fetchall()
        return [row[0] for row in rows]

    def _stored_dividends(self) -> list:
        return self._stored("dividend")

    def _stored_splits(self) -> list:
        return self._stored("split")

    def _revision_rows(self) -> list:
        return self.repo._conn.execute(  # noqa: SLF001 - 測試直接看表
            "SELECT close, dividend, split, reason FROM price_revisions ORDER BY revision_id"
        ).fetchall()

    def test_dividend_only_change_is_recorded_as_a_revision(self) -> None:
        prices = make_prices()
        for point in prices.points:
            point.dividend = 0.2
        self.repo.save_prices(prices)

        updated = make_prices()  # 價格完全相同，只有股利改變
        for point in updated.points:
            point.dividend = 0.5
        self.repo.save_prices(updated)

        revisions = self._revision_rows()
        self.assertEqual(len(revisions), 2)
        self.assertEqual(revisions[0]["dividend"], 0.2)
        self.assertEqual(sorted(r["close"] for r in revisions), [800.0, 810.0])  # 舊值保留
        self.assertIn("dividend", revisions[0]["reason"])
        self.assertNotIn("close", revisions[0]["reason"])
        self.assertEqual(self._stored_dividends(), [0.5, 0.5])  # 現值已更新

    def test_split_only_change_is_recorded_as_a_revision(self) -> None:
        prices = make_prices()
        for point in prices.points:
            point.split = 1.0
        self.repo.save_prices(prices)

        updated = make_prices()
        for point in updated.points:
            point.split = 2.0
        self.repo.save_prices(updated)

        revisions = self._revision_rows()
        self.assertEqual(len(revisions), 2)
        self.assertEqual(revisions[0]["split"], 1.0)
        self.assertEqual(sorted(r["close"] for r in revisions), [800.0, 810.0])  # 舊值保留
        self.assertIn("split", revisions[0]["reason"])
        self.assertNotIn("close", revisions[0]["reason"])
        self.assertEqual(self._stored_splits(), [2.0, 2.0])  # 現值已更新

    def test_availability_enrichment_is_not_a_revision(self) -> None:
        # 補上 available_at / note 是「補充」，不是「改寫」，不該塞滿 revision。
        self.repo.save_prices(make_prices())
        enriched = make_prices()
        for point in enriched.points:
            point.available_at = datetime(2026, 11, 13, 21, 30, tzinfo=timezone.utc)
            point.availability_source = AvailabilitySource.INFERRED
            point.availability_note = "market close + 30 分鐘"
        self.repo.save_prices(enriched)
        self.assertEqual(self._revision_rows(), [])
        loaded = self.repo.load_prices("LLY")
        self.assertEqual(loaded.points[-1].availability_source, AvailabilitySource.INFERRED)

    def test_dividend_not_provided_is_not_a_revision(self) -> None:
        # dividend 的 upsert 是 COALESCE：新下載沒帶 dividend（None）不會清掉舊值，
        # 資料庫最終仍是 0.2，所以不該記一筆沒發生的修訂。
        prices = make_prices()
        for point in prices.points:
            point.dividend = 0.2
        self.repo.save_prices(prices)

        bare = make_prices()  # dividend 未提供（None）
        self.repo.save_prices(bare)

        self.assertEqual(self._revision_rows(), [])
        self.assertEqual(self._stored_dividends(), [0.2, 0.2])

    def test_split_not_provided_is_not_a_revision(self) -> None:
        prices = make_prices()
        for point in prices.points:
            point.split = 2.0
        self.repo.save_prices(prices)

        bare = make_prices()
        self.repo.save_prices(bare)

        self.assertEqual(self._revision_rows(), [])
        self.assertEqual(self._stored_splits(), [2.0, 2.0])

    def test_upsert_without_market_currency_source_keeps_the_stored_values(self) -> None:
        # market / currency / source 是 enrichment：新資料沒帶（空字串）時不能把
        # 舊值蓋成空白，否則舊列會失去市場與幣別資訊。
        self.repo.save_prices(make_prices())
        bare = PriceSeries(
            ticker="LLY",
            points=[
                PricePoint(price_date=date(2026, 11, 12), close=800.0, adj_close=800.0),
                PricePoint(price_date=date(2026, 11, 13), close=810.0, adj_close=810.0),
            ],
        )
        self.repo.save_prices(bare)

        rows = self.repo._conn.execute(  # noqa: SLF001 - 測試直接看最終狀態
            "SELECT market, currency, source FROM market_prices ORDER BY price_date"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["market"], "US")
            self.assertEqual(row["currency"], "USD")
            self.assertEqual(row["source"], "unit-test")

    def test_timestamps_are_stored_as_utc_with_z(self) -> None:
        shanghai = timezone(timedelta(hours=8))
        prices = make_prices()
        for point in prices.points:
            point.market_timestamp = datetime(2026, 11, 14, 5, 0, tzinfo=shanghai)
        self.repo.save_prices(prices)
        rows = self.repo._conn.execute(  # noqa: SLF001
            "SELECT market_timestamp, price_timestamp FROM market_prices"
        ).fetchall()
        for row in rows:
            self.assertEqual(row["market_timestamp"], "2026-11-13T21:00:00Z")
            self.assertEqual(row["price_timestamp"], "2026-11-13T21:00:00Z")
        loaded = self.repo.load_prices("LLY")
        self.assertEqual(loaded.points[-1].market_timestamp,
                         datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc))

    def test_fx_round_trip(self) -> None:
        fx = FxSeries.from_pairs(
            "USD", [(date(2026, 11, 12), 7.10), (date(2026, 11, 13), 7.12)], source="unit-test"
        )
        self.assertEqual(self.repo.save_fx(fx), 2)
        loaded = self.repo.load_fx("usd")
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded.currency, "USD")
        self.assertAlmostEqual(loaded.rate_on_or_before(date(2026, 11, 13))[0], 7.12)

    def test_official_nav_round_trip(self) -> None:
        self.repo.save_official_nav(
            "000369",
            [(date(2026, 11, 11), 2.825), (date(2026, 11, 12), 2.815)],
        )
        nav = self.repo.load_official_nav("000369")
        self.assertEqual(len(nav), 2)
        self.assertEqual(self.repo.nav_on_or_before("000369", date(2026, 11, 12)), (date(2026, 11, 12), 2.815))
        self.assertIsNone(self.repo.nav_on_or_before("000369", date(2026, 11, 10)))


class EstimationTests(DatabaseTestCase):
    def _run_and_result(self) -> tuple[EstimationRun, EstimationResult]:
        run = EstimationRun(
            fund_code="000369",
            target_nav_date=date(2026, 11, 13),
            trade_date=date(2026, 11, 15),
            base_nav_date=date(2026, 11, 12),
            as_of=datetime(2026, 11, 15, 20, 56),
            holdings_quarter="2026Q3",
            holdings_date=date(2026, 9, 30),
            disclosure_date=date(2026, 10, 21),
            disclosure_date_estimated=True,
            holdings_source="eastmoney-page",
        )
        result = EstimationResult(
            run_id=run.run_id,
            status=PortfolioStatus.PARTIAL,
            base_nav=2.815,
            estimated_nav=2.8205,
            estimated_return=0.0019449,
            coverage_ratio=0.4609,
            uncovered_weight=0.5391,
            local_contribution=0.0023235,
            fx_contribution=-0.0003791,
            data_quality="medium",
            quality_score=74.0,
            warnings=["未覆蓋 53.9% 的資產按 0% 回報處理"],
            message="",
        )
        return run, result

    def test_save_and_load_estimation(self) -> None:
        run, result = self._run_and_result()
        inputs = {"holdings": [{"ticker": "LLY", "weight": 0.1043}], "prices": {"LLY": 810.0}}
        run_id = self.repo.save_estimation(run, inputs, result)
        self.assertEqual(run_id, run.run_id)

        loaded = self.repo.load_estimation(run_id)
        self.assertEqual(loaded["fund_code"], "000369")
        self.assertEqual(loaded["status"], "partial")
        self.assertAlmostEqual(loaded["estimated_nav"], 2.8205)
        self.assertEqual(loaded["warnings"], ["未覆蓋 53.9% 的資產按 0% 回報處理"])

        replay = self.repo.estimation_inputs(run_id)
        self.assertEqual(replay["prices"]["LLY"], 810.0)
        self.assertEqual(replay["holdings"][0]["weight"], 0.1043)

    def test_duplicate_run_is_rejected(self) -> None:
        run, result = self._run_and_result()
        self.repo.save_estimation(run, {}, result)
        with self.assertRaises(DuplicateRunError):
            self.repo.save_estimation(run, {}, result)

    def test_result_run_id_must_match(self) -> None:
        run, result = self._run_and_result()
        result.run_id = "other"
        with self.assertRaises(Exception):
            self.repo.save_estimation(run, {}, result)

    def test_list_estimations(self) -> None:
        for offset in range(3):
            run = EstimationRun(
                fund_code="000369",
                target_nav_date=date(2026, 11, 10 + offset),
                trade_date=date(2026, 11, 15),
            )
            result = EstimationResult(run_id=run.run_id, estimated_nav=2.8, status=PortfolioStatus.PARTIAL)
            self.repo.save_estimation(run, {}, result)

        rows = self.repo.list_estimations("000369")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["target_nav_date"], "2026-11-12")
        self.assertEqual(len(self.repo.list_estimations("999999")), 0)
        self.assertEqual(len(self.repo.list_estimations(limit=2)), 2)

    def test_backtest_results(self) -> None:
        run, result = self._run_and_result()
        self.repo.save_estimation(run, {}, result)
        self.repo.save_backtest_results(
            run.run_id,
            {
                "overall": {"n": 10, "mae": 0.0021, "rmse": 0.003, "mean_error": 0.0004, "max_abs_error": 0.006},
                "coverage>=0.9": {"n": 4, "mae": 0.0019},
            },
        )
        rows = self.repo.load_backtest_results(run.run_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["group_key"], "coverage>=0.9")
        self.assertAlmostEqual(rows[1]["mae"], 0.0021)


if __name__ == "__main__":
    unittest.main()
