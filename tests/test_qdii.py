# -*- coding: utf-8 -*-
"""QDII 淨值估算（V1）的單元測試。

重點不是「能不能跑」，而是把計算規則釘死：
* 日期由日曆模組決定，不用 today-1；
* 缺價格 / 缺匯率的持倉被排除並計入未覆蓋，絕不當成 0% 收益；
* 覆蓋率、匯率貢獻、估算淨值可手算核對；
* 估算值與官方淨值分開存放，回測用官方淨值對帳。
"""
import json
import shutil
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import pandas as pd

from fa.analysis.estimation_backtest import (
    backfill_history,
    compute_metrics,
    read_history,
    run_backtest,
    write_history,
)
from fa.analysis.nav_estimator import estimate_qdii_nav
from fa.analysis import nav_estimator as nav_estimator_mod
from fa.analysis.portfolio_return import aggregate, holding_return
from fa.analysis.valuation_dates import (
    order_trade_date as cal_order,
    resolve_valuation_dates,
)
from fa.data.fund_info import resolve_fund_mode
from fa.data.fx import rate_on
from fa.data import qdii_holdings as qdii_holdings_mod
from fa.data.qdii_holdings import (
    COLUMNS as HOLDING_COLUMNS,
    build_qdii_holdings,
    fetch_qdii_holdings,
    market_and_currency,
    quarter_end,
    select_point_in_time,
)
from fa.utils.cache import write_cache_df
from fa.data.qdii_market import (
    _frame_from_chart,
    candidate_symbols,
    normalise_currency,
)
from fa.utils import calendar as cal


class CalendarTests(unittest.TestCase):
    def test_weekend_and_business_day(self):
        saturday = date(2025, 6, 14)
        sunday = date(2025, 6, 15)
        monday = date(2025, 6, 16)
        self.assertFalse(cal.is_business_day(saturday))
        self.assertFalse(cal.is_business_day(sunday))
        self.assertTrue(cal.is_business_day(monday))

    def test_previous_business_day_skips_weekend(self):
        self.assertEqual(cal.previous_business_day(date(2025, 6, 16)), date(2025, 6, 13))
        self.assertEqual(cal.previous_business_day(date(2025, 6, 10)), date(2025, 6, 9))

    def test_business_days_between(self):
        # 6/6（五）→ 6/10（二）：6/9、6/10 兩個交易日
        self.assertEqual(cal.business_days_between(date(2025, 6, 6), date(2025, 6, 10)), 2)


class ValuationDateTests(unittest.TestCase):
    def test_target_is_previous_business_day(self):
        nav_dates = pd.to_datetime(["2025-06-04", "2025-06-05", "2025-06-06"])
        result = resolve_valuation_dates(nav_dates, as_of=datetime(2025, 6, 10, 10, 0))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["trade_date"], "2025-06-10")
        self.assertEqual(result["target_nav_date"], "2025-06-09")
        self.assertEqual(result["base_nav_date"], "2025-06-06")
        self.assertEqual(result["nav_lag_days"], 1)

    def test_no_nav_published(self):
        result = resolve_valuation_dates([], as_of=datetime(2025, 6, 10, 10, 0))
        self.assertEqual(result["status"], "no-nav")

    def test_up_to_date_when_target_already_published(self):
        nav_dates = pd.to_datetime(["2025-06-06", "2025-06-09"])
        result = resolve_valuation_dates(nav_dates, as_of=datetime(2025, 6, 10, 10, 0))
        self.assertEqual(result["status"], "up-to-date")
        self.assertEqual(result["base_nav_date"], "2025-06-09")

    def test_forced_target_must_be_before_trade_date(self):
        result = resolve_valuation_dates(
            pd.to_datetime(["2025-06-09"]),
            as_of=datetime(2025, 6, 10), target_nav_date="2025-06-10")
        self.assertEqual(result["status"], "invalid-target")


class HoldingReturnTests(unittest.TestCase):
    def _prices(self, rows):
        return pd.DataFrame(rows, columns=["Date", "Price"])

    def test_local_and_fx_return_compound(self):
        prices = self._prices([
            (pd.Timestamp("2025-06-09"), 100.0),
            (pd.Timestamp("2025-06-10"), 110.0),
        ])
        fx = pd.Series([7.00, 7.07], index=pd.to_datetime(["2025-06-09", "2025-06-10"]))
        detail = holding_return(prices, fx, "USD", date(2025, 6, 9), date(2025, 6, 10))
        self.assertEqual(detail["status"], "ok")
        self.assertAlmostEqual(detail["local_return"], 0.10, places=10)
        self.assertAlmostEqual(detail["fx_return"], 0.01, places=10)
        self.assertAlmostEqual(detail["cny_return"], 1.1 * 1.01 - 1, places=10)

    def test_missing_price_is_not_zero_return(self):
        detail = holding_return(self._prices([]), None, "USD",
                                date(2025, 6, 9), date(2025, 6, 10))
        self.assertEqual(detail["status"], "missing-price")
        self.assertIsNone(detail["cny_return"])

    def test_missing_fx_is_flagged(self):
        prices = self._prices([
            (pd.Timestamp("2025-06-09"), 100.0),
            (pd.Timestamp("2025-06-10"), 101.0),
        ])
        detail = holding_return(prices, None, "USD", date(2025, 6, 9), date(2025, 6, 10))
        self.assertEqual(detail["status"], "missing-fx")
        self.assertIsNone(detail["cny_return"])

    def test_stale_price_is_excluded(self):
        prices = self._prices([(pd.Timestamp("2025-05-01"), 100.0)])
        detail = holding_return(prices, None, "CNY",
                                date(2025, 6, 9), date(2025, 6, 10))
        self.assertEqual(detail["status"], "stale-price")

    def test_cny_holding_needs_no_fx(self):
        prices = self._prices([
            (pd.Timestamp("2025-06-09"), 10.0),
            (pd.Timestamp("2025-06-10"), 11.0),
        ])
        detail = holding_return(prices, None, "CNY", date(2025, 6, 9), date(2025, 6, 10))
        self.assertEqual(detail["status"], "ok")
        self.assertEqual(detail["fx_return"], 0.0)
        self.assertAlmostEqual(detail["cny_return"], 0.10, places=10)


class AggregateTests(unittest.TestCase):
    def test_excluded_rows_stay_out_of_return_and_coverage(self):
        rows = [
            {"ticker": "A", "weight": 0.5, "status": "ok", "cny_return": 0.10,
             "fx_return": 0.01, "local_return": 0.09},
            {"ticker": "B", "weight": 0.3, "status": "missing-price",
             "cny_return": None, "fx_return": None, "local_return": None},
            {"ticker": "C", "weight": 0.2, "status": "ok", "cny_return": -0.05,
             "fx_return": 0.0, "local_return": -0.05},
        ]
        summary = aggregate(rows)
        # 覆蓋率是「佔基金的比例」，不是重算成 100%
        self.assertAlmostEqual(summary["covered_weight"], 0.7, places=10)
        self.assertAlmostEqual(summary["coverage_fraction"], 0.7, places=10)
        self.assertAlmostEqual(summary["estimated_return"], 0.5 * 0.10 + 0.2 * -0.05,
                               places=10)
        self.assertAlmostEqual(summary["fx_contribution"], 0.5 * 0.01, places=10)
        self.assertEqual((summary["holdings_included"], summary["holdings_excluded"]), (2, 1))

    def test_empty_weights_give_zero_coverage(self):
        summary = aggregate([])
        self.assertEqual(summary["covered_weight"], 0.0)
        self.assertEqual(summary["coverage_fraction"], 0.0)


class QdiiMarketTests(unittest.TestCase):
    def test_pence_is_normalised_to_pounds(self):
        self.assertEqual(normalise_currency("GBp"), "GBP")
        self.assertEqual(normalise_currency("GBX"), "GBP")
        self.assertEqual(normalise_currency("chf"), "CHF")
        self.assertEqual(normalise_currency(None, "AZN.L"), "GBP")
        self.assertIsNone(normalise_currency(None, "LLY"))

    def test_symbol_candidates_prefer_overrides(self):
        self.assertEqual(candidate_symbols("ROPSW")[0], "ROP.SW")
        self.assertIn("AZN.L", candidate_symbols("AZN"))
        self.assertEqual(candidate_symbols("llY")[0], "LLY")
        self.assertEqual(candidate_symbols(""), [])

    def test_chart_payload_becomes_price_frame(self):
        payload = {
            "timestamp": [1749513600, 1749600000],
            "indicators": {
                "quote": [{"close": [101.0, 102.0]}],
                "adjclose": [{"adjclose": [100.5, 101.5]}],
            },
        }
        frame = _frame_from_chart(payload)
        self.assertIsNotNone(frame)
        self.assertEqual(list(frame.columns), ["Date", "Close", "AdjClose", "Price"])
        self.assertAlmostEqual(frame["Price"].iloc[-1], 101.5)

    def test_chart_payload_without_timestamps(self):
        self.assertIsNone(_frame_from_chart({"timestamp": []}))


class FxTests(unittest.TestCase):
    def test_rate_on_reports_staleness(self):
        series = pd.Series([7.0], index=pd.to_datetime(["2025-06-02"]))
        value, actual, stale = rate_on(series, date(2025, 6, 4))
        self.assertEqual(value, 7.0)
        self.assertEqual(actual.date(), date(2025, 6, 2))
        self.assertEqual(stale, 2)

    def test_rate_on_rejects_stale_data(self):
        series = pd.Series([7.0], index=pd.to_datetime(["2025-01-02"]))
        value, _, stale = rate_on(series, date(2025, 6, 4), max_stale_days=7)
        self.assertIsNone(value)
        self.assertGreater(stale, 7)

    def test_rate_on_without_series(self):
        self.assertEqual(rate_on(None, date(2025, 6, 4)), (None, None, None))


class QdiiHoldingsTests(unittest.TestCase):
    def test_quarter_end(self):
        self.assertEqual(quarter_end("2025Q1"), date(2025, 3, 31))
        self.assertEqual(quarter_end("2025Q4"), date(2025, 12, 31))
        self.assertIsNone(quarter_end("2025"))
        self.assertIsNone(quarter_end("2025Q9"))

    def test_market_and_currency_hints(self):
        self.assertEqual(market_and_currency("0700"), ("HKEX", "HKD"))
        self.assertEqual(market_and_currency("ROPSW"), ("SIX", "CHF"))
        self.assertEqual(market_and_currency("AZN"), ("LSE", "GBP"))
        self.assertEqual(market_and_currency("LLY"), ("US", "USD"))
        self.assertEqual(market_and_currency(""), (None, None))

    def test_build_holdings_normalises_weights_and_dates(self):
        raw = pd.DataFrame({
            "股票代码": ["LLY", "0700", "ROPSW"],
            "股票名称": ["礼来", "腾讯控股", "罗氏"],
            "占净值比例": ["10.43", "5.00", "3.39"],
            "季度": ["2025年1季度股票投资明细"] * 3,
        })
        holdings = build_qdii_holdings(raw)

        self.assertEqual(list(holdings["ticker"]), ["LLY", "0700", "ROPSW"])
        self.assertAlmostEqual(holdings["weight"].iloc[0], 0.1043, places=10)
        self.assertEqual(holdings.iloc[0]["quarter"], "2025Q1")
        self.assertEqual(holdings.iloc[0]["holding_date"], "2025-03-31")
        # 披露日 = 季末 + 21 天
        self.assertEqual(holdings.iloc[0]["disclosure_date"], "2025-04-21")
        self.assertEqual(holdings.iloc[0]["currency_hint"], "USD")

    def test_build_holdings_keeps_only_latest_quarter(self):
        raw = pd.DataFrame({
            "股票代码": ["LLY", "LLY"],
            "股票名称": ["礼来", "礼来"],
            "占净值比例": ["1.00", "10.00"],
            "季度": ["2024年4季度股票投资明细", "2025年1季度股票投资明细"],
        })
        holdings = build_qdii_holdings(raw)
        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings.iloc[0]["quarter"], "2025Q1")
        self.assertAlmostEqual(holdings.iloc[0]["weight"], 0.10, places=10)

    def test_build_holdings_empty_input(self):
        self.assertTrue(build_qdii_holdings(pd.DataFrame()).empty)


class QdiiHoldingsFallbackTests(unittest.TestCase):
    """基金檔案接口（fundf10）故障時，估算不能因此失去持倉。"""

    _RAW = pd.DataFrame({
        "股票代码": ["LLY", "JNJ"],
        "股票名称": ["礼来", "强生"],
        "占净值比例": [10.43, 7.33],
        "季度": ["2026年3季度股票投资明细"] * 2,
    })

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(setattr, qdii_holdings_mod, "_archives_down_until", 0.0)
        qdii_holdings_mod._archives_down_until = 0.0

    def test_page_is_used_when_archives_fail(self):
        with mock.patch("fa.data.qdii_holdings._fetch_year_holdings", return_value=None), \
                mock.patch("fa.data.qdii_holdings.fetch_holdings_page",
                           return_value=self._RAW):
            portfolio = fetch_qdii_holdings("000369", self.tmp, use_cache=False)

        self.assertEqual(portfolio["source"], "eastmoney-page")
        self.assertEqual(len(portfolio["holdings"]), 2)
        self.assertAlmostEqual(portfolio["holdings"]["weight"].iloc[0], 0.1043)
        self.assertTrue((self.tmp / "000369_qdii_holdings.csv").exists())

    def test_stale_cache_is_used_when_everything_fails(self):
        write_cache_df(build_qdii_holdings(self._RAW),
                       self.tmp / "000369_qdii_holdings.csv")
        with mock.patch("fa.data.qdii_holdings._fetch_year_holdings", return_value=None), \
                mock.patch("fa.data.qdii_holdings.fetch_holdings_page",
                           return_value=pd.DataFrame()):
            portfolio = fetch_qdii_holdings("000369", self.tmp, use_cache=False)

        self.assertEqual(portfolio["source"], "cache-stale")
        self.assertEqual(len(portfolio["holdings"]), 2)

    def test_no_source_gives_empty_holdings(self):
        with mock.patch("fa.data.qdii_holdings._fetch_year_holdings", return_value=None), \
                mock.patch("fa.data.qdii_holdings.fetch_holdings_page",
                           return_value=pd.DataFrame()):
            portfolio = fetch_qdii_holdings("000369", self.tmp, use_cache=False)

        self.assertIsNone(portfolio["source"])
        self.assertTrue(portfolio["holdings"].empty)
        self.assertEqual(list(portfolio["holdings"].columns), HOLDING_COLUMNS)


class FundModeTests(unittest.TestCase):
    def test_manual_override_wins(self):
        with mock.patch("fa.data.fund_info.fetch_fund_profile",
                        return_value={"is_qdii": False, "source": "name"}):
            mode, profile = resolve_fund_mode("000001", "qdii")
        self.assertEqual(mode, "qdii")
        self.assertEqual(profile["mode_source"], "manual")

    def test_manual_standard_override(self):
        with mock.patch("fa.data.fund_info.fetch_fund_profile",
                        return_value={"is_qdii": True, "source": "eastmoney-jbgk"}):
            mode, _ = resolve_fund_mode("000369", "standard")
        self.assertEqual(mode, "standard")

    def test_auto_follows_disclosed_type(self):
        with mock.patch("fa.data.fund_info.fetch_fund_profile",
                        return_value={"is_qdii": True, "source": "eastmoney-mobile"}):
            mode, profile = resolve_fund_mode("000369", "auto")
        self.assertEqual(mode, "qdii")
        self.assertEqual(profile["mode_source"], "auto")

    def test_unknown_falls_back_to_standard(self):
        with mock.patch("fa.data.fund_info.fetch_fund_profile",
                        return_value={"is_qdii": None, "source": "unknown"}):
            mode, profile = resolve_fund_mode("000369", "auto")
        self.assertEqual(mode, "standard")
        self.assertEqual(profile["mode_source"], "fallback")

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            resolve_fund_mode("000369", "hedge")


class EstimateQdiiNavTests(unittest.TestCase):
    """端到端：只 stub 網絡，日期 / 計算 / 落盤全部真跑。"""

    BASE_NAV = 1.2500
    # 官方淨值只到 6/6（五）；6/9（一）尚未公布 → 估算 6/9
    NAV_DATES = ["2025-06-04", "2025-06-05", "2025-06-06"]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.save = self.tmp / "000369"
        self.save.mkdir(parents=True)
        nav = pd.DataFrame({
            "Date": pd.to_datetime(self.NAV_DATES),
            "NAV": [1.2400, 1.2450, self.BASE_NAV],
        })
        nav.to_csv(self.save / "000369_daily_nav.csv", index=False)

        self.holdings = pd.DataFrame([
            {"ticker": "LLY", "name": "礼来", "weight": 0.5, "currency_hint": "USD",
             "market": "US", "quarter": "2025Q1", "holding_date": "2025-03-31",
             "disclosure_date": "2025-04-21"},
            {"ticker": "MISSING", "name": "無行情", "weight": 0.3,
             "currency_hint": "USD", "market": "US", "quarter": "2025Q1",
             "holding_date": "2025-03-31", "disclosure_date": "2025-04-21"},
        ])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, as_of=datetime(2025, 6, 10, 10, 0), holdings=None):
        prices = {
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
        fx = {"USD": pd.Series([7.00, 7.07],
                               index=pd.to_datetime(["2025-06-06", "2025-06-09"]))}
        portfolio = {
            "holdings": self.holdings if holdings is None else holdings,
            "quarter": "2025Q1", "holding_date": "2025-03-31",
            "disclosure_date": "2025-04-21", "source": "test", "cache_hit": False,
        }
        with mock.patch.multiple(
            "fa.analysis.nav_estimator",
            fetch_qdii_holdings=mock.DEFAULT,
            fetch_holding_prices=mock.DEFAULT,
            fetch_fx_rates=mock.DEFAULT,
        ) as m:
            m["fetch_qdii_holdings"].return_value = portfolio
            m["fetch_holding_prices"].return_value = prices
            m["fetch_fx_rates"].return_value = fx
            return estimate_qdii_nav("000369", self.save, as_of=as_of)

    def test_estimated_nav_matches_hand_calculation(self):
        record = self._run()

        self.assertEqual(record["status"], "partial")   # 有一隻持倉缺價格
        self.assertEqual(record["target_nav_date"], "2025-06-09")
        self.assertEqual(record["base_nav_date"], "2025-06-06")
        # 從 6/6 收盤到 6/9 收盤一個交易日
        self.assertAlmostEqual(record["estimated_return"], 0.5 * (1.1 * 1.01 - 1),
                               places=6)
        self.assertAlmostEqual(record["estimated_nav"],
                               round(self.BASE_NAV * (1 + 0.5 * (1.1 * 1.01 - 1)), 4),
                               places=4)
        self.assertAlmostEqual(record["coverage_ratio"], 0.5, places=10)
        self.assertEqual(record["holdings_included"], 1)
        self.assertEqual(record["holdings_excluded"], 1)

    def test_estimate_is_persisted_next_to_official_nav(self):
        self._run()

        json_path = self.save / "000369_qdii_estimate.json"
        csv_path = self.save / "000369_qdii_estimates.csv"
        self.assertTrue(json_path.exists())
        self.assertTrue(csv_path.exists())

        record = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "partial")
        self.assertEqual(record["holdings_quarter"], "2025Q1")
        # 官方淨值文件沒有被改寫
        nav = pd.read_csv(self.save / "000369_daily_nav.csv")
        self.assertEqual(len(nav), len(self.NAV_DATES))

        history = read_history(csv_path)
        self.assertEqual(len(history), 1)
        self.assertEqual(history.iloc[0]["target_nav_date"], "2025-06-09")

    def test_repeat_run_for_same_target_replaces_history_row(self):
        self._run()
        self._run()

        history = read_history(self.save / "000369_qdii_estimates.csv")
        self.assertEqual(len(history), 1, "同一估值日只保留一行")

    def test_up_to_date_when_official_nav_already_published(self):
        as_of = datetime(2025, 6, 10, 10, 0)
        nav = pd.DataFrame({"Date": pd.to_datetime(self.NAV_DATES + ["2025-06-09"]),
                            "NAV": [1.24, 1.245, 1.25, 1.26]})
        nav.to_csv(self.save / "000369_daily_nav.csv", index=False)

        record = self._run(as_of=as_of)
        self.assertEqual(record["status"], "up-to-date")
        self.assertEqual(record["holdings"], [])

    def test_no_holdings_is_insufficient_data(self):
        record = self._run(holdings=pd.DataFrame(
            columns=["ticker", "name", "weight", "currency_hint", "market",
                     "quarter", "holding_date", "disclosure_date"]))
        self.assertEqual(record["status"], "insufficient-data")
        self.assertIsNone(record.get("estimated_nav"))

    def test_missing_nav_file_and_no_download(self):
        (self.save / "000369_daily_nav.csv").unlink()
        with mock.patch("fa.analysis.nav_estimator.fetch_nav",
                        return_value=pd.DataFrame()):
            record = estimate_qdii_nav("000369", self.save)
        self.assertEqual(record["status"], "insufficient-data")
        self.assertIn("官方淨值", record["message"])


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "000369_qdii_estimates.csv"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _history(self):
        return pd.DataFrame([
            {"computed_at": "2025-06-10T16:00:00", "target_nav_date": "2025-06-09",
             "base_nav_date": "2025-06-06", "base_nav": 1.25,
             "estimated_nav": 1.2625, "estimated_return": 0.01,
             "coverage_ratio": 0.5, "status": "partial",
             "holdings_quarter": "2025Q1"},
            {"computed_at": "2025-06-11T16:00:00", "target_nav_date": "2025-06-10",
             "base_nav_date": "2025-06-09", "base_nav": 1.25,
             "estimated_nav": 1.20, "estimated_return": -0.04,
             "coverage_ratio": 0.5, "status": "partial",
             "holdings_quarter": "2025Q1"},
        ])

    def test_backfill_attaches_official_nav_and_error(self):
        nav = pd.DataFrame({
            "Date": pd.to_datetime(["2025-06-09", "2025-06-10"]),
            "NAV": [1.25, 1.20],
        })
        filled = backfill_history(self._history(), nav)
        self.assertAlmostEqual(filled.iloc[0]["official_nav"], 1.25)
        self.assertAlmostEqual(filled.iloc[0]["error"], 0.01, places=10)
        self.assertAlmostEqual(filled.iloc[1]["error"], 0.0, places=10)
        self.assertTrue(filled["abs_error"].notna().all())

    def test_metrics_match_manual_computation(self):
        history = pd.DataFrame({
            "target_nav_date": ["2025-06-09", "2025-06-10", "2025-06-11"],
            "error": [0.01, -0.02, 0.03],
            "coverage_ratio": [0.5, 0.5, 0.5],
        })
        metrics = compute_metrics(history)
        self.assertEqual(metrics["n"], 3)
        self.assertAlmostEqual(metrics["mae"], (0.01 + 0.02 + 0.03) / 3, places=10)
        self.assertAlmostEqual(metrics["rmse"], (0.0014 / 3) ** 0.5, places=10)
        self.assertAlmostEqual(metrics["mean_error"], 0.02 / 3, places=10)
        self.assertAlmostEqual(metrics["max_abs_error"], 0.03, places=10)

    def test_pending_rows_are_not_scored(self):
        write_history(self._history(), self.path)
        nav = pd.DataFrame({"Date": pd.to_datetime(["2025-06-09"]), "NAV": [1.25]})
        result = run_backtest("000369", self.tmp, nav_df=nav)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["n"], 1)
        self.assertEqual(result["pending"], 1)

    def test_no_history(self):
        result = run_backtest("000369", self.tmp)
        self.assertEqual(result["status"], "no-history")

    def test_write_history_is_sorted_and_complete(self):
        write_history(self._history(), self.path)
        frame = read_history(self.path)
        self.assertEqual(list(frame["target_nav_date"]),
                         ["2025-06-09", "2025-06-10"])
        self.assertIn("official_nav", frame.columns)


class V1CorrectionTests(unittest.TestCase):
    """V1 修正意見：前視偏差、時區/15:00、基準日過期、未知幣種、歸因。"""

    def _raw(self):
        return pd.DataFrame([
            {"股票代码": "AAA", "股票名称": "A 公司", "占净值比例": 30,
             "季度": "2024年4季度股票投资明细"},
            {"股票代码": "BBB", "股票名称": "B 公司", "占净值比例": 70,
             "季度": "2024年4季度股票投资明细"},
            {"股票代码": "AAA", "股票名称": "A 公司", "占净值比例": 40,
             "季度": "2025年1季度股票投资明细"},
            {"股票代码": "CCC", "股票名称": "C 公司", "占净值比例": 60,
             "季度": "2025年1季度股票投资明细"},
        ])

    def test_point_in_time_uses_only_disclosed_quarter(self):
        holdings = build_qdii_holdings(self._raw(), keep_all=True)
        # 2025Q1 要到 2025-04-21（推算）才算披露
        selection = select_point_in_time(holdings, date(2025, 2, 10))
        self.assertIsNotNone(selection)
        self.assertEqual(selection["quarter"], "2024Q4")
        self.assertEqual(set(selection["holdings"]["ticker"]), {"AAA", "BBB"})
        self.assertTrue(selection["disclosure_date_estimated"])
        self.assertTrue(selection["look_ahead_safe"])

        # 目標日推到 5 月，才輪到 2025Q1
        later = select_point_in_time(holdings, date(2025, 5, 10))
        self.assertEqual(later["quarter"], "2025Q1")

    def test_point_in_time_returns_none_before_first_disclosure(self):
        holdings = build_qdii_holdings(self._raw(), keep_all=True)
        self.assertIsNone(select_point_in_time(holdings, date(2024, 12, 1)))

    def test_order_trade_date_respects_cutoff(self):
        before, _ = cal_order(datetime(2025, 6, 10, 14, 0))
        after, _ = cal_order(datetime(2025, 6, 10, 15, 30))
        friday_late, _ = cal_order(datetime(2025, 6, 13, 16, 0))
        self.assertEqual(before, date(2025, 6, 10))
        self.assertEqual(after, date(2025, 6, 11))
        self.assertEqual(friday_late, date(2025, 6, 16))

    def test_cn_holidays_are_not_trading_days(self):
        self.assertFalse(cal.is_cn_trading_day(date(2025, 10, 1)))
        self.assertTrue(cal.is_cn_trading_day(date(2025, 9, 30)))
        self.assertEqual(cal.previous_cn_trading_day(date(2025, 10, 9)),
                         date(2025, 9, 30))

    def test_stale_base_price_is_flagged(self):
        prices = pd.DataFrame({
            "Date": pd.to_datetime(["2025-05-01", "2025-06-10"]),
            "Price": [100.0, 110.0],
        })
        detail = holding_return(prices, None, "CNY",
                                date(2025, 6, 9), date(2025, 6, 10))
        self.assertEqual(detail["status"], "stale-base-price")
        self.assertIsNone(detail["cny_return"])

    def test_unknown_currency_is_not_assumed_cny(self):
        prices = pd.DataFrame({
            "Date": pd.to_datetime(["2025-06-09", "2025-06-10"]),
            "Price": [100.0, 101.0],
        })
        detail = holding_return(prices, None, "", date(2025, 6, 9), date(2025, 6, 10))
        self.assertEqual(detail["status"], "missing-currency")

    def test_attribution_components_sum_to_total(self):
        rows = [
            {"ticker": "A", "weight": 0.5, "status": "ok",
             "local_return": 0.09, "fx_return": 0.009,
             "cny_return": 1.09 * 1.009 - 1},
            {"ticker": "B", "weight": 0.5, "status": "ok",
             "local_return": -0.03, "fx_return": 0.01,
             "cny_return": 0.97 * 1.01 - 1},
        ]
        summary = aggregate(rows)
        total = (summary["local_contribution"] + summary["fx_contribution"]
                 + summary["interaction_contribution"])
        self.assertAlmostEqual(total, summary["estimated_return"], places=12)
        self.assertAlmostEqual(summary["uncovered_weight"], 0.0, places=12)

    def test_inputs_snapshot_keeps_every_target_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            save = Path(tmp)
            record = {"fund_code": "000369", "target_nav_date": "2025-06-09",
                      "computed_at": "2025-06-10T16:00:00", "holdings": [],
                      "estimated_nav": 1.1}
            nav_estimator_mod._write_inputs_snapshot(record, save)
            other = dict(record, target_nav_date="2025-06-10", estimated_nav=1.2)
            nav_estimator_mod._write_inputs_snapshot(other, save)

            path = save / "000369_qdii_estimate_inputs.json"
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            self.assertEqual(set(payload["snapshots"]), {"2025-06-09", "2025-06-10"})
            self.assertAlmostEqual(
                payload["snapshots"]["2025-06-10"]["estimated_nav"], 1.2)


if __name__ == "__main__":
    unittest.main()
