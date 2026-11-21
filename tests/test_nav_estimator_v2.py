"""V2.2 NAV 估算整合測試：計算器 + 組合估值 + 資料品質 + SQLite，全程不連網。"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pandas as pd

from fa.analysis.nav_estimator import estimate_qdii_nav
from fa.data.database.repository import Repository
from fa.data.fx_pipeline import FxUpdateOutcome
from fa.models.market_data import AvailabilitySource, FxSeries, PricePoint

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
             "disclosure_date": "2025-04-21", "asset_type": "equity"},
            {"ticker": "MISSING", "name": "無行情", "weight": 0.3,
             "currency_hint": "USD", "market": "US", "quarter": "2025Q1",
             "holding_date": "2025-03-31", "disclosure_date": "2025-04-21",
             "asset_type": "equity"},
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

    def _seed_fx(self, repository):
        """FX rows published before the estimate instant; no network involved."""
        points = []
        for day, rate in ((date(2025, 6, 6), 7.00), (date(2025, 6, 9), 7.07)):
            points.append(PricePoint(
                price_date=day, close=rate,
                market_timestamp=datetime.combine(day - timedelta(days=1),
                                                  datetime.min.time(), tzinfo=timezone.utc),
                available_at=datetime(day.year, day.month, day.day, 0, 30,
                                      tzinfo=timezone.utc) + timedelta(days=1),
                availability_source=AvailabilitySource.INFERRED,
                availability_note="test", fetched_at=datetime(2025, 6, 10, tzinfo=timezone.utc),
                market="FX", currency="CNY", available_price_type="fx_close",
                used_price_type="fx_close", source="test",
            ))
        repository.save_fx(FxSeries.from_bars("USD", points, source="test",
                                              base_currency="CNY"))

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
        if repository is not None:
            self._seed_fx(repository)
        with mock.patch.multiple(
            "fa.analysis.nav_estimator",
            fetch_qdii_holdings=mock.DEFAULT,
            fetch_holding_prices=mock.DEFAULT,
            fetch_fx_rates=mock.DEFAULT,
            update_fx=mock.DEFAULT,
        ) as m:
            m["fetch_qdii_holdings"].return_value = portfolio
            m["fetch_holding_prices"].return_value = (
                self._prices() if prices is None else prices)
            m["fetch_fx_rates"].return_value = fx
            m["update_fx"].return_value = FxUpdateOutcome()
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

    # -- V2.2.1 review: reproducibility and the data window ----------------
    def test_input_snapshot_records_prices_fx_and_dates(self):
        record = self._run()
        inputs = record["inputs"]
        self.assertEqual(inputs["model_version"], "2.2.0")
        self.assertEqual(inputs["base_nav_date"], "2025-06-06")
        self.assertEqual(inputs["target_nav_date"], "2025-06-09")
        lly = next(a for a in inputs["assets"] if a["ticker"] == "LLY")
        self.assertAlmostEqual(lly["base_price"], 100.0)
        self.assertAlmostEqual(lly["target_price"], 110.0)
        self.assertAlmostEqual(lly["base_fx"], 7.00)
        self.assertAlmostEqual(lly["target_fx"], 7.07)
        # The fixture's `Price` column is Yahoo's AdjClose-or-Close, so the
        # recorded type must say adjusted_close (that is what was used).
        self.assertEqual(lly["price_type_used"], "adjusted_close")
        # Provenance: what the source offered vs what the calculator used.
        self.assertEqual(lly["available_price_type"], "adjusted_close")
        self.assertEqual(lly["used_price_type"], "adjusted_close")
        self.assertTrue(lly["calendar"])
        self.assertIn("holdings", inputs)

    def test_input_snapshot_is_kept_per_run(self):
        first = self._run()
        second = self._run()
        path = self.save / "000369_qdii_estimate_inputs.json"
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        self.assertIn(first["run_id"], payload["snapshots"])
        self.assertIn(second["run_id"], payload["snapshots"])
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_market_data_uses_the_valuation_window(self):
        """Older target dates must be fetched by date range, not last month."""
        self._run()
        # the patched fetcher is called through mock.patch.multiple; run once
        # more with a spy to inspect the window it was given.
        with mock.patch.multiple(
            "fa.analysis.nav_estimator",
            fetch_qdii_holdings=mock.DEFAULT,
            fetch_holding_prices=mock.DEFAULT,
            fetch_fx_rates=mock.DEFAULT,
        ) as m:
            m["fetch_qdii_holdings"].return_value = {
                "holdings": self.holdings, "quarters": ["2025Q1"],
                "quarter": "2025Q1", "holding_date": "2025-03-31",
                "disclosure_date": "2025-04-21", "source": "test",
                "cache_hit": False, "holdings_scope": "top10",
                "history_available": False,
            }
            m["fetch_holding_prices"].return_value = self._prices()
            m["fetch_fx_rates"].return_value = {
                "USD": pd.Series([7.00, 7.07],
                                 index=pd.to_datetime(["2025-06-06", "2025-06-09"]))
            }
            estimate_qdii_nav("000369", self.save,
                              as_of=datetime(2025, 6, 10, 10, 0))
            _, kwargs = m["fetch_holding_prices"].call_args
            self.assertLessEqual(kwargs["start"], date(2025, 6, 6))
            self.assertEqual(kwargs["end"], date(2025, 6, 9))
            _, fx_kwargs = m["fetch_fx_rates"].call_args
            self.assertEqual(fx_kwargs["end"], date(2025, 6, 9))

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
