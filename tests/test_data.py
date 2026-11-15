"""Data layer tests: pingzhongdata NAV parsing and holdings retries."""

import unittest
from unittest import mock

import pandas as pd

from fa.data.holdings import _fetch_year_holdings
from fa.data.nav import _parse_net_worth_js

_SAMPLE_JS = """
var ishb=false;
var Data_netWorthTrend = [{"x":1386633600000,"y":1.0,"equityReturn":0,"unitMoney":""},
{x:1386720000000,y:1.034,equityReturn:3.4,unitMoney:""},{x:1386806400000,y:0.99,equityReturn:-4.25,unitMoney:""}];
var Data_ACWorthTrend = [[1386633600000,1.0]];
"""


class PingzhongNavTests(unittest.TestCase):
    def test_parse_net_worth_js(self):
        df = _parse_net_worth_js(_SAMPLE_JS)
        self.assertEqual(len(df), 3)
        self.assertEqual(list(df.columns), ["净值日期", "单位净值", "日增长率"])
        self.assertAlmostEqual(df["单位净值"].iloc[1], 1.034)
        self.assertAlmostEqual(df["日增长率"].iloc[2], -4.25)
        # 毫秒时间戳按 Asia/Shanghai 转成日期
        self.assertEqual(df["净值日期"].iloc[0], pd.Timestamp("2013-12-10").date())

    def test_parse_net_worth_js_missing_block(self):
        with self.assertRaises(ValueError):
            _parse_net_worth_js("var somethingElse = 1;")

    def test_parse_net_worth_js_empty_list(self):
        df = _parse_net_worth_js("var Data_netWorthTrend = [];")
        self.assertTrue(df.empty)


class HoldingsRetryTests(unittest.TestCase):
    def _df(self):
        return pd.DataFrame({"股票代码": ["600519"], "季度": ["2025年1季度"]})

    def test_retries_then_succeeds(self):
        calls = []
        boom = RuntimeError("SSL EOF")

        def fake(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise boom
            return self._df()

        with mock.patch("fa.data.holdings.ak.fund_portfolio_hold_em", side_effect=fake), \
                mock.patch("fa.data.holdings.time.sleep") as sleep:
            df = _fetch_year_holdings("000369", "2025")

        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(2.0)
        self.assertEqual(len(df), 1)

    def test_gives_up_after_attempts(self):
        with mock.patch("fa.data.holdings.ak.fund_portfolio_hold_em",
                        side_effect=RuntimeError("boom")) as fetch, \
                mock.patch("fa.data.holdings.time.sleep"):
            self.assertIsNone(_fetch_year_holdings("000369", "2025", attempts=3))
        self.assertEqual(fetch.call_count, 3)

    def test_empty_result_is_retried(self):
        calls = []

        def fake(**kwargs):
            calls.append(kwargs)
            return pd.DataFrame()

        with mock.patch("fa.data.holdings.ak.fund_portfolio_hold_em", side_effect=fake), \
                mock.patch("fa.data.holdings.time.sleep"):
            self.assertIsNone(_fetch_year_holdings("000369", "2025", attempts=2))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
