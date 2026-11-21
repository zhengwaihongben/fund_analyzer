# -*- coding: utf-8 -*-
"""Stage B-1：應用服務層測試（真實暫存 SQLite，無網路）。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from fa.application import estimation as service
from fa.data.database import connection as connection_mod
from fa.data.database.repository import Repository
from fa.models.estimation import EstimationResult, EstimationRun

RUN_DAY = date(2026, 11, 16)
INPUTS = {
    "assets": [
        {
            "ticker": "LLY",
            "name": "禮來",
            "asset_type": "equity",
            "market": "US",
            "currency": "USD",
            "weight": 0.5,
            "base_price_date": "2026-11-13",
            "target_price_date": "2026-11-16",
            "local_return": 0.01,
            "fx_return": 0.0,
            "cny_return": 0.01,
            "status": "ok",
        },
        {
            "ticker": "AZN",
            "name": "阿斯利康",
            "asset_type": "equity",
            "market": "GB",
            "currency": "GBP",
            "weight": 0.5,
            "base_price_date": "2026-11-13",
            "target_price_date": "2026-11-16",
            "local_return": None,
            "fx_return": None,
            "cny_return": None,
            "status": "missing-fx",
        },
    ]
}


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.db = root / "test.db"
        self.save_dir = root / "output" / "000369"
        self.save_dir.mkdir(parents=True)

    # -- fixtures ---------------------------------------------------------
    def store_run(self, run_id="run-1", fund="000369", inputs=None, warnings=None,
                  estimated_nav=2.81):
        repository = Repository(self.db)
        try:
            run = EstimationRun(fund_code=fund, target_nav_date=RUN_DAY, trade_date=RUN_DAY,
                                run_id=run_id)
            result = EstimationResult(run_id=run_id, base_nav=2.80, estimated_nav=estimated_nav,
                                      estimated_return=0.0035, coverage_ratio=0.95,
                                      uncovered_weight=0.05, data_quality="high",
                                      warnings=list(warnings or []), message="")
            repository.save_estimation(run, dict(inputs if inputs is not None else INPUTS), result)
        finally:
            repository.close()
        return run_id

    def write_snapshot(self, run_id, holdings):
        path = self.save_dir / f"000369{service.INPUTS_SUFFIX}"
        payload = {"fund_code": "000369", "updated_at": "now",
                   "snapshots": {run_id: {"run_id": run_id, "holdings": holdings}}}
        path.write_text(json.dumps(payload), encoding="utf-8")

    def write_latest(self, run_id, holdings):
        path = self.save_dir / f"000369{service.ESTIMATE_SUFFIX}"
        path.write_text(json.dumps({"run_id": run_id, "holdings": holdings}), encoding="utf-8")

    def make_estimator(self, run_id, *, save=True, closing_run=None, closing_inputs=None,
                       holdings=None, warnings=None):
        def estimator(fund_code, save_dir, *, as_of=None, repository=None):
            self.assertIsInstance(repository, Repository)
            if save:
                run = EstimationRun(fund_code=fund_code, target_nav_date=RUN_DAY,
                                    trade_date=RUN_DAY, run_id=run_id)
                result = EstimationResult(run_id=run_id, base_nav=2.80, estimated_nav=2.82,
                                          estimated_return=0.005, coverage_ratio=0.9,
                                          uncovered_weight=0.1, data_quality="high",
                                          warnings=list(warnings or []), message="")
                repository.save_estimation(run, dict(closing_inputs or INPUTS), result)
            return {"run_id": run_id, "warnings": list(warnings or []),
                    "holdings": list(holdings if holdings is not None else [])}
        return estimator


class LiveEstimateTests(ServiceTestCase):
    def test_live_uses_a_repository_and_writes_the_run(self):
        view = service.run_estimate("000369", self.save_dir, db_path=self.db,
                                    estimator=self.make_estimator("run-live"))
        self.assertTrue(view.persisted)
        self.assertEqual(view.run_id, "run-live")
        self.assertEqual(view.mode, "live")
        self.assertEqual(view.message, "")
        repository = Repository(self.db)
        try:
            self.assertIsNotNone(repository.load_estimation("run-live"))
        finally:
            repository.close()

    def test_live_assets_come_from_the_sqlite_inputs_snapshot(self):
        view = service.run_estimate("000369", self.save_dir, db_path=self.db,
                                    estimator=self.make_estimator("run-live"))
        self.assertEqual(len(view.asset_rows), 2)
        self.assertEqual(view.asset_source, service.SOURCE_SQLITE_INPUTS)
        self.assertIsNone(view.asset_rows[0]["contribution"])
        self.assertIsNone(view.asset_rows[1]["contribution"])

    def test_live_view_exposes_the_inputs_snapshot(self):
        inputs = {"holdings_scope": "top10", "price_source": "yahoo", "assets": []}
        view = service.run_estimate(
            "000369", self.save_dir, db_path=self.db,
            estimator=self.make_estimator("run-live", closing_inputs=inputs),
        )
        self.assertEqual(view.inputs["holdings_scope"], "top10")
        self.assertEqual(view.inputs["price_source"], "yahoo")
        self.assertEqual(view.as_dict()["inputs"]["holdings_scope"], "top10")

    def test_live_view_without_persisted_row_has_no_inputs(self):
        view = service.run_estimate(
            "000369", self.save_dir, db_path=self.db,
            estimator=self.make_estimator("run-missing", save=False),
        )
        self.assertFalse(view.persisted)
        self.assertEqual(view.inputs, {})

    def test_persistence_failure_is_reported_not_hidden(self):
        view = service.run_estimate("000369", self.save_dir, db_path=self.db,
                                    estimator=self.make_estimator("run-missing", save=False,
                                                                  holdings=[{"ticker": "LLY",
                                                                             "weight": 1.0}]))
        self.assertFalse(view.persisted)
        self.assertEqual(view.message, "未寫入資料庫")
        self.assertEqual(view.asset_source, service.SOURCE_LIVE_RECORD)
        self.assertEqual(len(view.asset_rows), 1)
        self.assertTrue(any("未寫入資料庫" in item for item in view.warnings))

    def test_each_live_run_gets_its_own_id(self):
        service.run_estimate("000369", self.save_dir, db_path=self.db,
                             estimator=self.make_estimator("run-a"))
        service.run_estimate("000369", self.save_dir, db_path=self.db,
                             estimator=self.make_estimator("run-b"))
        rows = service.list_estimates(db_path=self.db)
        self.assertEqual(sorted(row["run_id"] for row in rows), ["run-a", "run-b"])

    def test_repository_is_closed_when_the_estimator_raises(self):
        created = []

        class TrackingRepository:
            def __init__(self, path):
                self._inner = Repository(path)
                self.closed = False
                created.append(self)

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def close(self):
                self.closed = True
                self._inner.close()

        def broken(fund_code, save_dir, *, as_of=None, repository=None):
            raise RuntimeError("boom")

        with mock.patch.object(service, "Repository", TrackingRepository):
            with self.assertRaises(RuntimeError):
                service.run_estimate("000369", self.save_dir, db_path=self.db,
                                     estimator=broken)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)

    def test_live_without_a_run_id_is_never_reported_as_persisted(self):
        def no_run_id(fund_code, save_dir, *, as_of=None, repository=None):
            return {"warnings": [], "holdings": [{"ticker": "LLY", "weight": 0.5}]}

        view = service.run_estimate("000369", self.save_dir, db_path=self.db,
                                    estimator=no_run_id)
        self.assertFalse(view.persisted)
        self.assertIsNone(view.run_id)
        self.assertEqual(view.message, "未寫入資料庫")
        self.assertEqual(len(view.asset_rows), 1)


class StoredEstimateTests(ServiceTestCase):
    def test_stored_service_never_calls_the_estimator_or_writes(self):
        self.store_run("run-1")
        with mock.patch.object(service, "estimate_qdii_nav", side_effect=AssertionError("no")), \
                mock.patch.object(Repository, "save_estimation", side_effect=AssertionError("no")):
            view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(view.mode, "stored")
        self.assertTrue(view.persisted)
        self.assertEqual(view.run_id, "run-1")
        self.assertEqual(view.record["estimated_nav"], 2.81)

    def test_inputs_snapshot_is_exposed_read_only(self):
        self.store_run(
            "run-1",
            inputs={"holdings_scope": "full_portfolio", "price_source": "eastmoney",
                    "assets": []},
        )
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(view.inputs["holdings_scope"], "full_portfolio")
        self.assertEqual(view.inputs["price_source"], "eastmoney")
        self.assertEqual(view.as_dict()["inputs"]["price_source"], "eastmoney")

    def test_unknown_run_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            service.load_estimate("nope", db_path=self.db, save_dir=self.save_dir)

    def test_contribution_comes_from_the_per_run_snapshot(self):
        self.store_run("run-1")
        self.write_snapshot("run-1", [{"ticker": "LLY", "contribution": 0.004}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertIn(service.SOURCE_RUN_SNAPSHOT, view.asset_source)
        self.assertEqual(view.asset_rows[0]["contribution"], 0.004)
        self.assertIsNone(view.asset_rows[1]["contribution"])

    def test_latest_json_of_another_run_is_ignored(self):
        self.store_run("run-1")
        self.write_latest("run-other", [{"ticker": "LLY", "contribution": 0.9}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertNotIn(service.SOURCE_LATEST_JSON, view.asset_source)
        self.assertIsNone(view.asset_rows[0]["contribution"])

    def test_latest_json_with_the_same_run_id_is_used(self):
        self.store_run("run-1")
        self.write_latest("run-1", [{"ticker": "LLY", "contribution": 0.002}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertIn(service.SOURCE_LATEST_JSON, view.asset_source)
        self.assertEqual(view.asset_rows[0]["contribution"], 0.002)

    def test_missing_asset_detail_is_announced(self):
        self.store_run("run-empty", inputs={})
        view = service.load_estimate("run-empty", db_path=self.db, save_dir=self.save_dir)
        self.assertFalse(view.has_assets)
        self.assertEqual(view.message, service.NO_ASSET_SOURCE_TEXT)

    def test_cny_return_is_resolved_from_sqlite_return_cny(self):
        self.store_run(
            "run-cny",
            inputs={
                "assets": [
                    {
                        "ticker": "LLY",
                        "weight": 0.5,
                        "local_return": 0.01,
                        "fx_return": 0.002,
                        "return_cny": 0.012,
                        "status": "ok",
                    }
                ]
            },
        )
        view = service.load_estimate("run-cny", db_path=self.db, save_dir=self.save_dir)
        self.assertAlmostEqual(view.asset_rows[0]["cny_return"], 0.012)

    def test_canonical_cny_return_wins_over_the_legacy_name(self):
        self.store_run(
            "run-canonical",
            inputs={
                "assets": [
                    {"ticker": "LLY", "cny_return": 0.02, "return_cny": 0.99, "status": "ok"}
                ]
            },
        )
        view = service.load_estimate("run-canonical", db_path=self.db, save_dir=self.save_dir)
        self.assertAlmostEqual(view.asset_rows[0]["cny_return"], 0.02)

    def test_unknown_weight_and_contribution_are_not_zero(self):
        self.store_run("run-1", inputs={"assets": [{"ticker": "LLY", "weight": None,
                                                   "status": "insufficient-data"}]})
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertIsNone(view.asset_rows[0]["weight"])
        self.assertIsNone(view.asset_rows[0]["contribution"])
        self.assertEqual(view.asset_rows[0]["status"], "insufficient-data")


class AssetSourcePriorityTests(ServiceTestCase):
    """B-2.1：SQLite → per-run snapshot → latest JSON 的優先級與合併規則。"""

    @staticmethod
    def _asset(ticker="LLY", **extra):
        base = {"ticker": ticker, "weight": 0.5, "local_return": 0.01,
                "fx_return": 0.002, "return_cny": 0.012, "status": "ok"}
        base.update(extra)
        return base

    def test_b201_sqlite_values_win_over_the_snapshot(self):
        self.store_run("run-1", inputs={"assets": [self._asset(contribution=0.025)]})
        self.write_snapshot("run-1", [{"ticker": "LLY", "weight": 0.8, "contribution": 0.05}])
        row = service.load_estimate("run-1", db_path=self.db,
                                    save_dir=self.save_dir).asset_rows[0]
        self.assertEqual(row["weight"], 0.5)
        self.assertEqual(row["contribution"], 0.025)

    def test_b202_snapshot_supplies_a_missing_contribution(self):
        self.store_run("run-1", inputs={"assets": [self._asset()]})
        self.write_snapshot("run-1", [{"ticker": "LLY", "contribution": 0.025}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(view.asset_rows[0]["contribution"], 0.025)
        self.assertIn(service.SOURCE_RUN_SNAPSHOT, view.asset_source)

    def test_b203_latest_json_supplies_a_missing_contribution(self):
        self.store_run("run-1", inputs={"assets": [self._asset()]})
        self.write_latest("run-1", [{"ticker": "LLY", "contribution": 0.03}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(view.asset_rows[0]["contribution"], 0.03)
        self.assertIn(service.SOURCE_LATEST_JSON, view.asset_source)

    def test_b204_latest_json_of_another_run_is_ignored(self):
        self.store_run("run-1", inputs={"assets": [self._asset()]})
        self.write_latest("run-other", [{"ticker": "LLY", "contribution": 0.9}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertIsNone(view.asset_rows[0]["contribution"])
        self.assertNotIn(service.SOURCE_LATEST_JSON, view.asset_source)

    def test_b205_latest_json_works_when_the_snapshot_is_absent(self):
        self.store_run("run-1", inputs={"assets": [self._asset()]})
        self.write_latest("run-1", [{"ticker": "LLY", "contribution": 0.022}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(view.asset_rows[0]["contribution"], 0.022)
        self.assertNotIn(service.SOURCE_RUN_SNAPSHOT, view.asset_source)

    def test_b206_snapshot_builds_rows_when_sqlite_has_no_assets(self):
        self.store_run("run-1", inputs={})
        self.write_snapshot("run-1", [{"ticker": "LLY", "weight": 1.0,
                                       "contribution": 0.01}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(len(view.asset_rows), 1)
        self.assertEqual(view.asset_rows[0]["ticker"], "LLY")
        self.assertEqual(view.asset_rows[0]["contribution"], 0.01)
        self.assertEqual(view.asset_source, service.SOURCE_RUN_SNAPSHOT)

    def test_b207_only_matching_tickers_are_filled(self):
        self.store_run("run-1", inputs={"assets": [self._asset("LLY"),
                                                   self._asset("AZN", weight=0.5)]})
        self.write_snapshot("run-1", [{"ticker": "AZN", "contribution": 0.004}])
        rows = service.load_estimate("run-1", db_path=self.db,
                                     save_dir=self.save_dir).asset_rows
        by_ticker = {row["ticker"]: row for row in rows}
        self.assertIsNone(by_ticker["LLY"]["contribution"])
        self.assertEqual(by_ticker["AZN"]["contribution"], 0.004)

    def test_b208_ticker_matching_ignores_case_and_surrounding_spaces(self):
        self.store_run("run-1", inputs={"assets": [self._asset("LLY")]})
        self.write_snapshot("run-1", [{"ticker": "  lly ", "contribution": 0.007}])
        row = service.load_estimate("run-1", db_path=self.db,
                                    save_dir=self.save_dir).asset_rows[0]
        self.assertEqual(row["contribution"], 0.007)

    def test_b209_an_existing_contribution_is_never_overwritten(self):
        self.store_run("run-1", inputs={"assets": [self._asset(contribution=0.001)]})
        self.write_snapshot("run-1", [{"ticker": "LLY", "contribution": 0.9}])
        self.write_latest("run-1", [{"ticker": "LLY", "contribution": 0.8}])
        row = service.load_estimate("run-1", db_path=self.db,
                                    save_dir=self.save_dir).asset_rows[0]
        self.assertEqual(row["contribution"], 0.001)

    def test_b210_unmatched_snapshot_tickers_do_not_add_assets(self):
        self.store_run("run-1", inputs={"assets": [self._asset("LLY")]})
        self.write_snapshot("run-1", [{"ticker": "ZZZ", "contribution": 0.5}])
        rows = service.load_estimate("run-1", db_path=self.db,
                                     save_dir=self.save_dir).asset_rows
        self.assertEqual([row["ticker"] for row in rows], ["LLY"])

    def test_b2_1_13_unmatched_snapshot_is_not_claimed_as_a_source(self):
        self.store_run("run-1", inputs={"assets": [self._asset("LLY")]})
        self.write_snapshot("run-1", [{"ticker": "ZZZ", "contribution": 0.5}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertNotIn(service.SOURCE_RUN_SNAPSHOT, view.asset_source)
        self.assertIn(service.SOURCE_SQLITE_INPUTS, view.asset_source)

    def test_b2_1_14_unmatched_latest_json_is_not_claimed_as_a_source(self):
        self.store_run("run-1", inputs={"assets": [self._asset("LLY")]})
        self.write_latest("run-1", [{"ticker": "ZZZ", "contribution": 0.5}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertNotIn(service.SOURCE_LATEST_JSON, view.asset_source)
        self.assertIn(service.SOURCE_SQLITE_INPUTS, view.asset_source)

    def test_b2_1_15_matched_but_useless_snapshot_is_not_claimed(self):
        self.store_run("run-1", inputs={"assets": [self._asset("LLY")]})
        self.write_snapshot("run-1", [{"ticker": "LLY", "weight": 0.5}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertNotIn(service.SOURCE_RUN_SNAPSHOT, view.asset_source)
        self.assertIsNone(view.asset_rows[0]["contribution"])

    def test_b2_1_16_a_none_status_can_be_filled_by_the_snapshot(self):
        self.store_run("run-1", inputs={"assets": [self._asset("AZN", status=None)]})
        self.write_snapshot("run-1", [{"ticker": "AZN", "status": "estimated",
                                       "contribution": 0.002}])
        row = service.load_estimate("run-1", db_path=self.db,
                                    save_dir=self.save_dir).asset_rows[0]
        self.assertEqual(row["status"], "estimated")

    def test_b2_1_17_an_ok_status_can_be_filled_by_the_snapshot(self):
        self.store_run("run-1", inputs={"assets": [self._asset("AZN", status="ok")]})
        self.write_snapshot("run-1", [{"ticker": "AZN", "status": "partial",
                                       "contribution": 0.002}])
        row = service.load_estimate("run-1", db_path=self.db,
                                    save_dir=self.save_dir).asset_rows[0]
        self.assertEqual(row["status"], "partial")

    def test_b2_1_18_status_is_merged_independently_of_contribution(self):
        self.store_run("run-1", inputs={"assets": [self._asset("LLY", status=None,
                                                               contribution=0.001)]})
        self.write_snapshot("run-1", [{"ticker": "LLY", "status": "estimated",
                                       "contribution": 0.9}])
        row = service.load_estimate("run-1", db_path=self.db,
                                    save_dir=self.save_dir).asset_rows[0]
        self.assertEqual(row["contribution"], 0.001)
        self.assertEqual(row["status"], "estimated")

    def test_status_outside_ok_is_never_overwritten_by_the_snapshot(self):
        self.store_run("run-1", inputs={"assets": [self._asset("AZN", status="missing-fx")]})
        self.write_snapshot("run-1", [{"ticker": "AZN", "status": "ok",
                                       "contribution": 0.002}])
        row = service.load_estimate("run-1", db_path=self.db,
                                    save_dir=self.save_dir).asset_rows[0]
        self.assertEqual(row["status"], "missing-fx")

    def test_ok_status_may_be_supplied_by_the_snapshot(self):
        self.store_run("run-1", inputs={"assets": [self._asset("AZN", status="")]})
        self.write_snapshot("run-1", [{"ticker": "AZN", "status": "estimated",
                                       "contribution": 0.002}])
        row = service.load_estimate("run-1", db_path=self.db,
                                    save_dir=self.save_dir).asset_rows[0]
        self.assertEqual(row["status"], "estimated")


class HistoryTests(ServiceTestCase):
    def test_history_filters_by_fund_and_respects_the_limit(self):
        self.store_run("run-1", fund="000369")
        self.store_run("run-2", fund="000369")
        self.store_run("run-3", fund="000001")
        self.assertEqual(len(service.list_estimates(db_path=self.db)), 3)
        self.assertEqual(len(service.list_estimates("000369", db_path=self.db)), 2)
        self.assertEqual(len(service.list_estimates(limit=1, db_path=self.db)), 1)

    def test_limit_is_clamped(self):
        self.store_run("run-1")
        original = Repository.list_estimations
        with mock.patch.object(
            Repository,
            "list_estimations",
            autospec=True,
            side_effect=lambda repo, **kwargs: original(repo, **kwargs),
        ) as spy:
            # Lower bound: 0 and negatives become 1.
            self.assertEqual(len(service.list_estimates(limit=0, db_path=self.db)), 1)
            self.assertEqual(spy.call_args.kwargs["limit"], 1)
            self.assertEqual(len(service.list_estimates(limit=-5, db_path=self.db)), 1)
            self.assertEqual(spy.call_args.kwargs["limit"], 1)
            # Upper bound: anything above 200 is clamped, so the SQL never asks
            # for an unbounded page.  One stored run is enough to prove the
            # argument that reached the repository.
            self.assertEqual(len(service.list_estimates(limit=9999, db_path=self.db)), 1)
            self.assertEqual(spy.call_args.kwargs["limit"], service.MAX_HISTORY_LIMIT)
            self.assertEqual(spy.call_args.kwargs["limit"], 200)

    def test_history_rows_are_plain_dictionaries(self):
        self.store_run("run-1", warnings=["w1"])
        rows = service.list_estimates(db_path=self.db)
        self.assertIsInstance(rows[0], dict)
        self.assertIn("run_id", rows[0])
        self.assertIn("target_nav_date", rows[0])
        self.assertIn("coverage_ratio", rows[0])

    def test_a_listed_run_id_loads_that_runs_own_detail(self):
        self.store_run("run-1", fund="000369")
        self.store_run("run-2", fund="000001")
        listed = {row["run_id"] for row in service.list_estimates(db_path=self.db)}
        self.assertEqual(listed, {"run-1", "run-2"})
        for run_id, fund in (("run-1", "000369"), ("run-2", "000001")):
            view = service.load_estimate(run_id, db_path=self.db,
                                         save_dir=self.save_dir)
            self.assertEqual(view.run_id, run_id)
            self.assertEqual(view.record["fund_code"], fund)


class JsonInputToleranceTests(ServiceTestCase):
    """B-2.2：JSON 缺失/損壞/結構錯誤時，stored mode 仍以 SQLite 摘要為主。"""

    def _inputs_path(self):
        return self.save_dir / f"000369{service.INPUTS_SUFFIX}"

    def _latest_path(self):
        return self.save_dir / f"000369{service.ESTIMATE_SUFFIX}"

    def _write(self, path, text):
        path.write_text(text, encoding="utf-8")

    def _stored(self):
        return service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)

    def _assert_summary_only(self, view):
        self.assertEqual(view.mode, "stored")
        self.assertTrue(view.persisted)
        self.assertEqual(view.run_id, "run-1")
        self.assertEqual(view.record["estimated_nav"], 2.81)
        self.assertEqual(view.asset_rows, [])
        self.assertEqual(view.asset_source, "")
        self.assertEqual(view.message, service.NO_ASSET_SOURCE_TEXT)

    def _summary(self):
        self.store_run("run-1", inputs={})

    # -- inputs JSON ------------------------------------------------------
    def test_b2_2_01_inputs_json_missing(self):
        self._summary()
        self._assert_summary_only(self._stored())

    def test_b2_2_02_inputs_json_is_empty(self):
        self._summary()
        self._write(self._inputs_path(), "")
        self._assert_summary_only(self._stored())

    def test_b2_2_03_inputs_json_is_invalid(self):
        self._summary()
        self._write(self._inputs_path(), "{not json")
        self._assert_summary_only(self._stored())

    def test_b2_2_04_inputs_root_is_a_list(self):
        self._summary()
        self._write(self._inputs_path(), json.dumps([{"run-1": {"holdings": []}}]))
        self._assert_summary_only(self._stored())

    def test_b2_2_05_snapshots_key_missing(self):
        self._summary()
        self._write(self._inputs_path(), json.dumps({"fund_code": "000369"}))
        self._assert_summary_only(self._stored())

    def test_b2_2_06_snapshots_is_not_a_dict(self):
        self._summary()
        self._write(self._inputs_path(), json.dumps({"snapshots": []}))
        self._assert_summary_only(self._stored())
        self._write(self._inputs_path(), json.dumps({"snapshots": "run-1"}))
        self._assert_summary_only(self._stored())

    def test_b2_2_07_other_run_id_is_not_used(self):
        self._summary()
        self.write_snapshot("other-run", [{"ticker": "LLY", "contribution": 0.5}])
        self._assert_summary_only(self._stored())

    def test_b2_2_08_snapshot_is_not_a_dict(self):
        self._summary()
        self._write(self._inputs_path(),
                    json.dumps({"snapshots": {"run-1": None}}))
        self._assert_summary_only(self._stored())
        self._write(self._inputs_path(),
                    json.dumps({"snapshots": {"run-1": "bad"}}))
        self._assert_summary_only(self._stored())

    def test_b2_2_09_snapshot_holdings_missing(self):
        self._summary()
        self._write(self._inputs_path(),
                    json.dumps({"snapshots": {"run-1": {"run_id": "run-1"}}}))
        self._assert_summary_only(self._stored())

    def test_b2_2_10_snapshot_holdings_not_a_list(self):
        self._summary()
        self._write(self._inputs_path(), json.dumps(
            {"snapshots": {"run-1": {"holdings": {"ticker": "LLY"}}}}))
        self._assert_summary_only(self._stored())

    def test_b2_2_11_non_dict_holdings_are_ignored(self):
        self._summary()
        self.write_snapshot("run-1", [None, "bad", {"ticker": "LLY",
                                                    "contribution": 0.01,
                                                    "status": "estimated"}])
        view = self._stored()
        self.assertEqual(len(view.asset_rows), 1)
        self.assertEqual(view.asset_rows[0]["ticker"], "LLY")
        self.assertEqual(view.asset_rows[0]["contribution"], 0.01)
        self.assertIn(service.SOURCE_RUN_SNAPSHOT, view.asset_source)

    def test_b2_2_12_all_invalid_holdings_claim_no_source(self):
        self._summary()
        self.write_snapshot("run-1", [None, "bad", 42])
        self._assert_summary_only(self._stored())

    # -- latest JSON ------------------------------------------------------
    def test_b2_2_13_latest_json_missing(self):
        self._summary()
        self._assert_summary_only(self._stored())

    def test_b2_2_14_latest_json_is_empty(self):
        self._summary()
        self._write(self._latest_path(), "")
        self._assert_summary_only(self._stored())

    def test_b2_2_15_latest_json_is_invalid(self):
        self._summary()
        self._write(self._latest_path(), "{oops")
        self._assert_summary_only(self._stored())

    def test_b2_2_16_latest_root_is_not_a_dict(self):
        self._summary()
        self._write(self._latest_path(), json.dumps([{"run_id": "run-1"}]))
        self._assert_summary_only(self._stored())

    def test_b2_2_17_latest_without_a_run_id(self):
        self._summary()
        self._write(self._latest_path(),
                    json.dumps({"holdings": [{"ticker": "LLY", "contribution": 0.1}]}))
        self._assert_summary_only(self._stored())

    def test_b2_2_18_latest_with_another_run_id_is_ignored(self):
        self._summary()
        self._write(self._latest_path(),
                    json.dumps({"run_id": "other-run",
                                "holdings": [{"ticker": "LLY", "contribution": 0.1}]}))
        self._assert_summary_only(self._stored())

    def test_b2_2_19_latest_holdings_not_a_list(self):
        self._summary()
        self._write(self._latest_path(),
                    json.dumps({"run_id": "run-1", "holdings": "LLY"}))
        self._assert_summary_only(self._stored())

    def test_b2_2_20_latest_non_dict_holdings_are_ignored(self):
        self._summary()
        self._write(self._latest_path(), json.dumps(
            {"run_id": "run-1", "holdings": [None, "bad", {"ticker": "LLY",
                                                           "contribution": 0.02}]}))
        view = self._stored()
        self.assertEqual(len(view.asset_rows), 1)
        self.assertEqual(view.asset_rows[0]["ticker"], "LLY")
        self.assertIn(service.SOURCE_LATEST_JSON, view.asset_source)


WRITE_METHODS = (
    "upsert_fund",
    "save_snapshot",
    "save_prices",
    "save_fx",
    "save_official_nav",
    "save_estimation",
    "save_backtest_results",
    "save_backtest_run",
)


class StoredModeReadOnlyTests(ServiceTestCase):
    """B-2.4：stored mode 不估值、不寫資料庫、不動 JSON，且一定關閉 Repository。"""

    def _files(self):
        return [
            self.save_dir / f"000369{service.INPUTS_SUFFIX}",
            self.save_dir / f"000369{service.ESTIMATE_SUFFIX}",
        ]

    def _snapshot(self, path):
        if not path.exists():
            return None
        stat = path.stat()
        return path.read_bytes(), stat.st_mtime_ns

    def _seed_full(self):
        self.store_run("run-1")
        self.write_snapshot("run-1", [{"ticker": "LLY", "contribution": 0.01,
                                       "status": "estimated"}])
        self.write_latest("run-1", [{"ticker": "LLY", "contribution": 0.01}])

    def _spy_writes(self):
        spies = {}
        for name in WRITE_METHODS:
            patcher = mock.patch.object(Repository, name, wraps=getattr(Repository, name))
            spies[name] = patcher.start()
            self.addCleanup(patcher.stop)
        return spies

    def _assert_no_writes(self, spies):
        for name, spy in spies.items():
            spy.assert_not_called()

    def _record_closes(self):
        seen = []
        original = Repository.close

        def recorder(repository):
            seen.append(repository)
            original(repository)

        patcher = mock.patch.object(Repository, "close", recorder)
        patcher.start()
        self.addCleanup(patcher.stop)
        return seen

    def _assert_connection_closed(self, repository):
        with self.assertRaises(sqlite3.ProgrammingError):
            repository._conn.execute("SELECT 1")

    # -- B2.4-01 ----------------------------------------------------------
    def test_b2_4_01_estimator_is_never_called(self):
        self._seed_full()
        with mock.patch.object(service, "estimate_qdii_nav") as engine:
            service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
            for path in self._files():
                path.write_text("{broken", encoding="utf-8")
            service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
            missing = Path(self._tmp.name) / "empty"
            service.load_estimate("run-1", db_path=self.db, save_dir=missing)
            with self.assertRaises(LookupError):
                service.load_estimate("nope", db_path=self.db, save_dir=self.save_dir)
        engine.assert_not_called()

    # -- B2.4-02 ----------------------------------------------------------
    def test_b2_4_02_no_repository_write_is_called(self):
        self._seed_full()
        spies = self._spy_writes()
        service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self._assert_no_writes(spies)

    def test_b2_4_02_no_write_on_corrupt_json_or_unknown_run(self):
        self._seed_full()
        for path in self._files():
            path.write_text("{broken", encoding="utf-8")
        spies = self._spy_writes()
        service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        with self.assertRaises(LookupError):
            service.load_estimate("nope", db_path=self.db, save_dir=self.save_dir)
        self._assert_no_writes(spies)

    # -- B2.4-03 ----------------------------------------------------------
    def test_b2_4_03_json_files_are_not_modified(self):
        self._seed_full()
        before = {path: self._snapshot(path) for path in self._files()}
        service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        for path, snapshot in before.items():
            self.assertIsNotNone(snapshot)
            self.assertEqual(self._snapshot(path), snapshot, path.name)

    def test_b2_4_03_corrupt_json_is_not_rewritten(self):
        self._seed_full()
        for path in self._files():
            path.write_text("{broken", encoding="utf-8")
        before = {path: self._snapshot(path) for path in self._files()}
        service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        for path, snapshot in before.items():
            self.assertEqual(self._snapshot(path), snapshot, path.name)

    def test_b2_4_03_missing_json_files_are_not_created(self):
        self.store_run("run-1", inputs={})
        service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        for path in self._files():
            self.assertFalse(path.exists(), path.name)
        self.assertEqual(list(self.save_dir.iterdir()), [])

    # -- B2.4-04 ----------------------------------------------------------
    def test_b2_4_04_repository_is_closed_after_a_successful_read(self):
        self._seed_full()
        closes = self._record_closes()
        service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(len(closes), 1)
        self._assert_connection_closed(closes[0])

    # -- B2.4-05 ----------------------------------------------------------
    def test_b2_4_05_repository_is_closed_when_the_run_is_unknown(self):
        self.store_run("run-1")
        closes = self._record_closes()
        with self.assertRaises(LookupError):
            service.load_estimate("missing-run", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(len(closes), 1)
        self._assert_connection_closed(closes[0])

    # -- B2.4-06 ----------------------------------------------------------
    def test_b2_4_06_list_estimates_closes_and_does_not_write(self):
        self.store_run("run-1")
        closes = self._record_closes()
        spies = self._spy_writes()
        rows = service.list_estimates(db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(closes), 1)
        self._assert_connection_closed(closes[0])
        self._assert_no_writes(spies)

    # -- B2.4-07 ----------------------------------------------------------
    def test_b2_4_07_unexpected_json_error_propagates_and_closes(self):
        self._seed_full()
        closes = self._record_closes()
        spies = self._spy_writes()
        with mock.patch.object(service, "_read_json", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(len(closes), 1)
        self._assert_connection_closed(closes[0])
        self._assert_no_writes(spies)


    # -- B2.4-08 ----------------------------------------------------------
    def _db_files(self):
        return {p.name: p.stat().st_size for p in self.db.parent.glob(self.db.name + "*")}

    def _db_fingerprint(self):
        con = sqlite3.connect(self.db)
        try:
            schema = [tuple(row) for row in con.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name")]
            tables = [row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            versions = [tuple(row) for row in con.execute(
                "SELECT version, applied_at FROM schema_version ORDER BY version")]
            counts = {name: con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                      for name in tables}
            data = {}
            for name in ("estimation_runs", "estimation_results", "estimation_inputs"):
                if name in tables:
                    data[name] = [tuple(row) for row in con.execute(
                        f"SELECT * FROM {name} ORDER BY 1")]
            return {"schema": schema, "versions": versions, "counts": counts, "data": data}
        finally:
            con.close()

    def test_b2_4_08_initialise_runs_when_the_repository_opens(self):
        self.store_run("run-1")
        original = connection_mod.initialise
        with mock.patch.object(connection_mod, "initialise", wraps=original) as spy:
            service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(spy.call_count, 1)
        with mock.patch.object(connection_mod, "initialise", wraps=original) as spy:
            service.list_estimates(db_path=self.db)
        self.assertEqual(spy.call_count, 1)

    def test_b2_4_08_initialise_leaves_schema_rows_and_versions_unchanged(self):
        self.store_run("run-1")
        self.write_snapshot("run-1", [{"ticker": "LLY", "contribution": 0.01}])
        before = self._db_fingerprint()
        wal_before = self._db_files()
        service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        service.list_estimates(db_path=self.db)
        after = self._db_fingerprint()
        wal_after = self._db_files()
        # Main-file and WAL sizes are recorded for the record, not asserted:
        # WAL checkpointing can move bytes between the two files without any
        # logical change, so only the logical fingerprint is authoritative.
        self.assertEqual(after["schema"], before["schema"], (wal_before, wal_after))
        self.assertEqual(after["versions"], before["versions"])
        self.assertEqual(after["counts"], before["counts"])
        self.assertEqual(after["data"], before["data"])

    def test_b2_4_08_no_migration_is_applied_and_no_table_is_created(self):
        self.store_run("run-1")
        with sqlite3.connect(self.db) as con:
            version_before = con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            tables_before = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        with sqlite3.connect(self.db) as con:
            version_after = con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            tables_after = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(version_after, version_before)
        self.assertEqual(tables_after, tables_before)

    def test_b2_4_08_a_read_opens_a_writable_connection_without_writing(self):
        """Documents the honest boundary: stored mode is not a read-only handle.

        ``Repository`` calls ``connect()``, which always runs ``initialise()``
        and therefore opens a write transaction (``BEGIN IMMEDIATE``) even when
        nothing is pending.  On an up-to-date database no row, table or version
        changes (asserted above); on a stale database the same call *would*
        migrate it — that is ``connect()``'s documented contract, not a write
        performed by ``load_estimate()``.  A strictly read-only handle would
        need a Repository change, which this batch may not make.
        """
        self.store_run("run-1")
        before = self._db_fingerprint()
        seen = []
        original = connection_mod.initialise

        def spy(conn, **kwargs):
            seen.append(conn.execute("PRAGMA query_only").fetchone()[0])
            return original(conn, **kwargs)

        with mock.patch.object(connection_mod, "initialise", spy):
            service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        # `query_only = 0` means SQLite itself would accept a write on this
        # handle: the connection is write-capable, not merely unused for writes.
        self.assertEqual(seen, [0])
        self.assertEqual(self._db_fingerprint(), before)


class NumericNormalisationTests(ServiceTestCase):
    """B-2.5-01：數值正規化——缺失維持 None，無效值不得變成有效財務數字。"""

    def setUp(self):
        super().setUp()
        self.store_run("run-1", inputs={})

    def _row(self, holding):
        self.write_snapshot("run-1", [holding])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(len(view.asset_rows), 1)
        return view.asset_rows[0]

    def test_none_stays_none(self):
        row = self._row({"ticker": "LLY", "weight": None, "local_return": None,
                         "fx_return": None, "cny_return": None, "contribution": None})
        for key in ("weight", "local_return", "fx_return", "cny_return", "contribution"):
            self.assertIsNone(row[key], key)

    def test_empty_string_is_not_a_number(self):
        row = self._row({"ticker": "LLY", "weight": "", "cny_return": "",
                         "contribution": ""})
        for key in ("weight", "cny_return", "contribution"):
            self.assertIsNone(row[key], key)

    def test_zero_is_kept(self):
        row = self._row({"ticker": "LLY", "weight": 0, "cny_return": 0,
                         "contribution": 0})
        self.assertEqual(row["weight"], 0.0)
        self.assertEqual(row["cny_return"], 0.0)
        self.assertEqual(row["contribution"], 0.0)

    def test_numeric_string_is_parsed(self):
        row = self._row({"ticker": "LLY", "weight": "0.125", "cny_return": "-0.03"})
        self.assertEqual(row["weight"], 0.125)
        self.assertEqual(row["cny_return"], -0.03)

    def test_negative_keeps_sign(self):
        row = self._row({"ticker": "LLY", "weight": -0.25, "fx_return": -0.01})
        self.assertEqual(row["weight"], -0.25)
        self.assertEqual(row["fx_return"], -0.01)

    def test_garbage_does_not_become_zero(self):
        row = self._row({"ticker": "LLY", "weight": "abc", "local_return": "n/a",
                         "contribution": "unknown"})
        for key in ("weight", "local_return", "contribution"):
            self.assertIsNone(row[key], key)

    def test_booleans_are_not_numbers(self):
        row = self._row({"ticker": "LLY", "weight": True, "contribution": False})
        self.assertIsNone(row["weight"])
        self.assertIsNone(row["contribution"])

    def test_nan_is_not_financial(self):
        row = self._row({"ticker": "LLY", "weight": float("nan")})
        self.assertIsNone(row["weight"])

    def test_positive_infinity_is_not_financial(self):
        row = self._row({"ticker": "LLY", "weight": float("inf")})
        self.assertIsNone(row["weight"])

    def test_negative_infinity_is_not_financial(self):
        row = self._row({"ticker": "LLY", "weight": float("-inf")})
        self.assertIsNone(row["weight"])

    def test_oversized_positive_integer_is_not_financial(self):
        row = self._row({"ticker": "LLY", "weight": 10 ** 1000})
        self.assertIsNone(row["weight"])

    def test_oversized_negative_integer_is_not_financial(self):
        row = self._row({"ticker": "LLY", "weight": -(10 ** 1000)})
        self.assertIsNone(row["weight"])

    def test_oversized_integer_in_json_holdings_does_not_abort_history_read(self):
        row = self._row({"ticker": "LLY", "weight": 10 ** 1000,
                         "contribution": -(10 ** 1000)})
        self.assertEqual(row["ticker"], "LLY")
        self.assertIsNone(row["weight"])
        self.assertIsNone(row["contribution"])

    def test_every_numeric_field_is_guarded(self):
        row = self._row({"ticker": "LLY", "weight": float("inf"),
                         "local_return": float("-inf"), "fx_return": float("nan"),
                         "cny_return": float("inf"), "contribution": float("-inf")})
        for key in ("weight", "local_return", "fx_return", "cny_return", "contribution"):
            self.assertIsNone(row[key], key)

    def test_price_fields_are_not_part_of_the_presentation_contract(self):
        row = self._row({"ticker": "LLY", "base_price": 100.0, "target_price": 101.0})
        # EstimationView exposes price *dates*, not price levels (§6.3); a
        # holding that carries extra fields must not leak them into the row.
        self.assertNotIn("base_price", row)
        self.assertNotIn("target_price", row)


class SourcePriorityMergeTests(ServiceTestCase):
    """B-2.5-02：優先級與欄位補充不得倒置或互相阻擋。"""

    def _asset(self, ticker="LLY", contribution=None, status="ok"):
        return {"ticker": ticker, "weight": 0.5, "contribution": contribution,
                "status": status}

    def test_sqlite_contribution_is_not_overwritten_by_lower_sources(self):
        self.store_run("run-1", inputs={"assets": [self._asset(contribution=0.01)]})
        self.write_snapshot("run-1", [{"ticker": "LLY", "contribution": 0.5}])
        self.write_latest("run-1", [{"ticker": "LLY", "contribution": 0.9}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(view.asset_rows[0]["contribution"], 0.01)
        self.assertEqual(view.asset_source, service.SOURCE_SQLITE_INPUTS)

    def test_snapshot_wins_over_latest_json(self):
        self.store_run("run-1", inputs={"assets": [self._asset()]})
        self.write_snapshot("run-1", [{"ticker": "LLY", "contribution": 0.5}])
        self.write_latest("run-1", [{"ticker": "LLY", "contribution": 0.9}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(view.asset_rows[0]["contribution"], 0.5)
        self.assertIn(service.SOURCE_RUN_SNAPSHOT, view.asset_source)
        self.assertNotIn(service.SOURCE_LATEST_JSON, view.asset_source)

    def test_status_is_filled_even_when_contribution_is_present(self):
        self.store_run("run-1", inputs={"assets": [self._asset(contribution=0.02,
                                                               status="ok")]})
        self.write_snapshot("run-1", [{"ticker": "LLY", "status": "estimated"}])
        row = service.load_estimate("run-1", db_path=self.db,
                                    save_dir=self.save_dir).asset_rows[0]
        self.assertEqual(row["contribution"], 0.02)
        self.assertEqual(row["status"], "estimated")

    def test_ticker_is_normalised_for_matching(self):
        self.store_run("run-1", inputs={"assets": [self._asset()]})
        self.write_snapshot("run-1", [{"ticker": "  lly ", "contribution": 0.03}])
        row = service.load_estimate("run-1", db_path=self.db,
                                    save_dir=self.save_dir).asset_rows[0]
        self.assertEqual(row["contribution"], 0.03)

    def test_unmatched_ticker_contributes_nothing(self):
        self.store_run("run-1", inputs={"assets": [self._asset()]})
        self.write_snapshot("run-1", [{"ticker": "AZN", "contribution": 0.03}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertIsNone(view.asset_rows[0]["contribution"])
        self.assertEqual(view.asset_source, service.SOURCE_SQLITE_INPUTS)

    def test_latest_json_with_another_run_id_contributes_nothing(self):
        self.store_run("run-1", inputs={"assets": [self._asset()]})
        self.write_latest("other-run", [{"ticker": "LLY", "contribution": 0.03}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertIsNone(view.asset_rows[0]["contribution"])
        self.assertNotIn(service.SOURCE_LATEST_JSON, view.asset_source)


class LegacyHistoryTests(ServiceTestCase):
    """B-2.5-03：舊格式與缺失欄位的歷史紀錄仍可讀。"""

    def test_a_run_without_input_assets_falls_back_to_the_snapshot(self):
        self.store_run("run-1", inputs={})
        self.write_snapshot("run-1", [{"ticker": "LLY", "contribution": 0.01}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(len(view.asset_rows), 1)
        self.assertIsNone(view.asset_rows[0]["weight"])
        self.assertIsNotNone(view.asset_rows[0]["contribution"])

    def test_legacy_cny_return_field_is_read(self):
        self.store_run("run-1", inputs={})
        self.write_snapshot("run-1", [{"ticker": "LLY", "cny_return": 0.02}])
        row = service.load_estimate("run-1", db_path=self.db,
                                    save_dir=self.save_dir).asset_rows[0]
        self.assertEqual(row["cny_return"], 0.02)

    def test_new_return_cny_field_is_read(self):
        self.store_run("run-1", inputs={})
        self.write_snapshot("run-1", [{"ticker": "LLY", "return_cny": 0.03}])
        row = service.load_estimate("run-1", db_path=self.db,
                                    save_dir=self.save_dir).asset_rows[0]
        self.assertEqual(row["cny_return"], 0.03)

    def test_a_holding_without_a_ticker_does_not_abort_the_read(self):
        self.store_run("run-1", inputs={})
        self.write_snapshot("run-1", [{"weight": 0.5, "contribution": 0.01}])
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(len(view.asset_rows), 1)
        self.assertIsNone(view.asset_rows[0]["ticker"])

    def test_partially_valid_rows_keep_their_valid_fields(self):
        self.store_run("run-1", inputs={})
        self.write_snapshot("run-1", [
            {"ticker": "LLY", "weight": 0.4, "contribution": 0.01},
            {"ticker": "AZN", "weight": None, "cny_return": None, "contribution": None},
        ])
        rows = service.load_estimate("run-1", db_path=self.db,
                                     save_dir=self.save_dir).asset_rows
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["weight"], 0.4)
        self.assertIsNone(rows[1]["weight"])

    def test_store_run_without_inputs_row_still_returns_the_summary(self):
        self.store_run("run-1", inputs={})
        view = service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        self.assertEqual(view.record["estimated_nav"], 2.81)
        self.assertEqual(view.asset_rows, [])
        self.assertEqual(view.message, service.NO_ASSET_SOURCE_TEXT)


class AbnormalValuePresentationTests(ServiceTestCase):
    """B-2.5-04：呈現層忠實反映資料，不自行加限制或重算。"""

    def _rows(self, holdings):
        self.store_run("run-1", inputs={})
        self.write_snapshot("run-1", holdings)
        return service.load_estimate("run-1", db_path=self.db,
                                     save_dir=self.save_dir).asset_rows

    def test_zero_weight_is_kept(self):
        self.assertEqual(self._rows([{"ticker": "LLY", "weight": 0}])[0]["weight"], 0.0)

    def test_negative_weight_is_kept(self):
        self.assertEqual(self._rows([{"ticker": "LLY", "weight": -0.1}])[0]["weight"], -0.1)

    def test_tiny_weight_is_kept(self):
        self.assertEqual(self._rows([{"ticker": "LLY", "weight": 1e-12}])[0]["weight"], 1e-12)

    def test_weight_above_one_is_not_clamped(self):
        self.assertEqual(self._rows([{"ticker": "LLY", "weight": 1.5}])[0]["weight"], 1.5)

    def test_extreme_magnitudes_are_kept_when_finite(self):
        row = self._rows([{"ticker": "LLY", "weight": 1e308, "cny_return": 5e-324}])[0]
        self.assertEqual(row["weight"], 1e308)
        self.assertEqual(row["cny_return"], 5e-324)

    def test_empty_holdings_produce_no_rows(self):
        self.assertEqual(self._rows([]), [])

    def test_duplicate_tickers_are_preserved_not_merged(self):
        rows = self._rows([{"ticker": "LLY", "weight": 0.3},
                           {"ticker": "LLY", "weight": 0.4}])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["weight"], 0.3)

    def test_invalid_date_strings_are_presented_verbatim(self):
        row = self._rows([{"ticker": "LLY", "base_price_date": "not-a-date",
                           "target_price_date": ""}])[0]
        self.assertEqual(row["base_price_date"], "not-a-date")
        self.assertEqual(row["target_price_date"], "")

    def test_mixed_missing_and_invalid_values_do_not_raise(self):
        rows = self._rows([{"ticker": "LLY", "weight": "abc", "cny_return": None,
                            "status": ""},
                           {"ticker": "AZN", "weight": "0.2", "status": None}])
        self.assertIsNone(rows[0]["weight"])
        self.assertEqual(rows[1]["weight"], 0.2)


class HistoryReadOnlyRegressionTests(ServiceTestCase):
    """B-2.5-05：歷史讀取仍是唯讀，且不觸發估值。"""

    def test_reading_history_leaves_sqlite_and_json_untouched(self):
        self.store_run("run-1", inputs={})
        self.write_snapshot("run-1", [{"ticker": "LLY", "contribution": 0.01}])
        before_rows = self._estimation_rows()
        json_path = self.save_dir / f"000369{service.INPUTS_SUFFIX}"
        before_bytes = json_path.read_bytes()
        with mock.patch.object(service, "estimate_qdii_nav") as engine:
            service.load_estimate("run-1", db_path=self.db, save_dir=self.save_dir)
        engine.assert_not_called()
        self.assertEqual(self._estimation_rows(), before_rows)
        self.assertEqual(json_path.read_bytes(), before_bytes)

    def _estimation_rows(self):
        with sqlite3.connect(self.db) as con:
            runs = [tuple(row) for row in con.execute(
                "SELECT * FROM estimation_runs ORDER BY 1")]
            results = [tuple(row) for row in con.execute(
                "SELECT * FROM estimation_results ORDER BY 1")]
        return runs, results


if __name__ == "__main__":
    unittest.main()
