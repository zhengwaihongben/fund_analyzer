"""V2.2 NAV 估算整合測試：計算器 + 組合估值 + 資料品質 + SQLite，全程不連網。"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import pandas as pd

from fa.analysis.nav_estimator import estimate_qdii_nav
from fa.data.database.repository import Repository

NAV_DATES = ["2025-06-04", "2025-06-05", "2025-06-06"]
BASE_NAV = 1.2500


class NavEstimatorV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.save = self.tmp / "000369"
        self.save.mkdir(parents=True)
        pd.DataFrame({
            "Date": pd.to_datetime(NAV_DATES),
            "NAV": [1.2400, 1.2450, BASE_NAV],
        }).to_csv(self.save / "000369_daily_nav.csv", index=False)
        self.holdings = pd.DataFrame([
            {"ticker": "LLY", "name": "礼来", "weight": 0.5, "currency_hint": "USD",
             "market": "US", "quarter": "2025Q1", "holding_date": "2025-03-31",
             "disclosure_date": "2025-04-21"},
            {"ticker": "MISSING", "name": "無行情", "weight": 0.3,
             "currency_hint": "USD", "market": "US", "quarter": "2025Q1",
             "holding_date": "2025-03-31", "disclosure_date": "2025-04-21"},
        ])

    def _prices(self, lly_status="ok"):
        if lly_status != "ok":
            return {"LLY": {"status": "missing", "symbol": None, "currency": None,
                            "exchange": None, "prices": None, "tried": ["LLY"],
                            "error": "no symbol resolved"}}
        return {
            "LLY": {
                "status": "ok", "symbol": "LLY", "currency": "USD",
                "exchange": "NYQ",
                "prices": pd.DataFrame({
                    "Date": pd.to_datetime(["2025-06-06", "2025-06-09"]),
                    "Price": [100.0, 110.0],
                }),
                "tried": ["LLY"], "error": None,
            },
            "MISSING": {
                "status": "missing", "symbol": None, "currency": None,
                "exchange": None, "prices": None, "tried": ["MISSING"],
                "error": "no symbol resolved",
            },
        }

    def _run(self, holdings=None, prices=None, repository=None):
        frame = self.holdings if holdings is None else holdings
        quarters = (sorted({str(q) for q in frame["quarter"] if str(q).strip()})
                    if "quarter" in frame.columns else [])
        portfolio = {
            "holdings": frame, "quarters": quarters, "quarter": "2025Q1",
            "holding_date": "2025-03-31", "disclosure_date": "2025-04-21",
            "source": "test", "cache_hit": False, "holdings_scope": "top10",
            "history_available": False,
        }
        fx = {"USD": pd.Series([7.00, 7.07],
                               index=pd.to_datetime(["2025-06-06", "2025-06-09"]))}
        with mock.patch.multiple(
            "fa.analysis.nav_estimator",
            fetch_qdii_holdings=mock.DEFAULT,
            fetch_holding_prices=mock.DEFAULT,
            fetch_fx_rates=mock.DEFAULT,
        ) as m:
            m["fetch_qdii_holdings"].return_value = portfolio
            m["fetch_holding_prices"].return_value = (
                self._prices() if prices is None else prices)
            m["fetch_fx_rates"].return_value = fx
            return estimate_qdii_nav("000369", self.save,
                                     as_of=datetime(2025, 6, 10, 10, 0),
                                     repository=repository)

    # -- full flow ---------------------------------------------------------
    def test_full_flow_produces_v2_2_record(self):
        record = self._run()
        self.assertEqual(record["model_version"], "2.2.0")
        self.assertEqual(record["status"], "partial")
        self.assertEqual(record["target_nav_date"], "2025-06-09")
        self.assertEqual(record["base_nav_date"], "2025-06-06")
        self.assertAlmostEqual(record["estimated_return"], 0.5 * (1.1 * 1.01 - 1),
                               places=6)
        self.assertIn(record["data_quality"], {"high", "medium", "low"})
        self.assertIsNotNone(record["quality_score"])

    def test_legacy_json_contract_is_kept(self):
        record = self._run()
        for key in ("estimated_nav", "estimated_return", "coverage_ratio",
                    "coverage_of_known", "covered_weight", "uncovered_weight",
                    "total_weight", "local_contribution", "fx_contribution",
                    "interaction_contribution", "holdings", "holdings_quarter",
                    "look_ahead_safe"):
            self.assertIn(key, record)
        self.assertAlmostEqual(record["coverage_ratio"], 0.5, places=10)
        self.assertAlmostEqual(record["coverage_of_known"], 0.5 / 0.8, places=10)
        self.assertAlmostEqual(record["uncovered_weight"], 0.5, places=10)
        # JSON 必須可序列化：日期欄位是字串
        path = self.save / "000369_qdii_estimate.json"
        json.loads(path.read_text(encoding="utf-8-sig"))

    def test_holdings_rows_carry_shapley_split(self):
        record = self._run()
        rows = record["holdings"]
        self.assertEqual(len(rows), 2)
        included = [r for r in rows if r["status"] == "ok"]
        self.assertEqual(len(included), 1)
        row = included[0]
        self.assertAlmostEqual(row["local_return"], 0.10, places=10)
        self.assertAlmostEqual(row["fx_return"], 0.01, places=10)
        self.assertAlmostEqual(row["cny_return"], 0.111, places=10)
        total = sum(r["contribution"] for r in rows)
        self.assertAlmostEqual(total, record["estimated_return"], places=12)
        self.assertAlmostEqual(record["interaction_contribution"], 0.0, places=12)

    def test_insufficient_data_when_no_price_is_usable(self):
        record = self._run(prices=self._prices("missing"))
        self.assertEqual(record["status"], "insufficient-data")
        self.assertIsNone(record["estimated_nav"])
        self.assertAlmostEqual(record["covered_weight"], 0.0, places=12)

    # -- database ----------------------------------------------------------
    def test_estimation_is_persisted_with_quality(self):
        repo = Repository(self.tmp / "test.db")
        with repo:
            record = self._run(repository=repo)
            rows = repo.list_estimations("000369")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["model_version"], "2.2.0")
            self.assertEqual(rows[0]["data_quality"], record["data_quality"])
            loaded = repo.load_estimation(rows[0]["run_id"])
            self.assertAlmostEqual(loaded["estimated_return"],
                                   record["estimated_return"], places=12)
            self.assertEqual(loaded["status"], "partial")


if __name__ == "__main__":
    unittest.main()
