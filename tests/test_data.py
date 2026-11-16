"""Data layer tests: pingzhongdata NAV parsing, holdings retries and page fallback."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from fa.data import holdings as holdings_mod
from fa.data.holdings import (
    _fetch_year_holdings,
    fetch_holdings,
    fetch_holdings_page,
    read_stale_cache,
)
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
    def setUp(self):
        holdings_mod._archives_down_until = 0.0

    def tearDown(self):
        holdings_mod._archives_down_until = 0.0

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


_PAGE_HTML = """
<table class="ui-table-hover" width="100%" border="0" cellspacing="0" cellpadding="0"><tr>
   <th class="alignLeft">股票名称</th>   <th class="alignRight">持仓占比</th>  </tr><tr>
   <td class="alignLeft">   <a href="//quote.eastmoney.com/unify/r/106.LLY"
   title="礼来">礼来</a>  </td>   <td class="alignRight bold">10.43%</td>
   <td class="alignRight bold" stockcode="stock_LLY"><span class="">--</span></td>  </tr><tr>
   <td class="alignLeft">   <div title="罗氏控股股份公司">罗氏控股股</div>  </td>
   <td class="alignRight bold">3.39%</td>
   <td class="alignRight bold" stockcode="stock_ROP SW"><span class="">--</span></td>  </tr></table>
<p class='sum'><span class="sum-num">46.09%</span></p>
<div class='poptableWrap_footer'><span class='end_date'>持仓截止日期: 2026-09-30</span></div>
"""


class _FakeResponse:
    def __init__(self, text):
        self.content = text.encode("utf-8")

    def raise_for_status(self):
        return None


class HoldingsPageTests(unittest.TestCase):
    def setUp(self):
        holdings_mod._archives_down_until = 0.0

    def tearDown(self):
        holdings_mod._archives_down_until = 0.0

    def test_parses_page_table(self):
        with mock.patch("fa.data.holdings.requests.get",
                        return_value=_FakeResponse(_PAGE_HTML)):
            df = fetch_holdings_page("000369")
        self.assertEqual(len(df), 2)
        self.assertEqual(list(df["股票代码"]), ["LLY", "ROP SW"])
        self.assertAlmostEqual(df["占净值比例"].iloc[0], 10.43)
        self.assertEqual(df["季度"].iloc[0], "2026年3季度股票投资明细")

    def test_missing_table_returns_empty(self):
        with mock.patch("fa.data.holdings.requests.get",
                        return_value=_FakeResponse("<html>no table</html>")):
            self.assertTrue(fetch_holdings_page("000369").empty)

    def test_request_error_returns_empty(self):
        with mock.patch("fa.data.holdings.requests.get",
                        side_effect=RuntimeError("boom")):
            self.assertTrue(fetch_holdings_page("000369").empty)

    def test_fetch_holdings_prefers_page(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("fa.data.holdings.requests.get",
                           return_value=_FakeResponse(_PAGE_HTML)), \
                mock.patch("fa.data.holdings.ak.fund_portfolio_hold_em") as archive:
            df = fetch_holdings("000369", Path(tmp), use_cache=False)
            cached = (Path(tmp) / "000369_holdings_raw.csv").exists()

        self.assertEqual(len(df), 2)
        self.assertTrue(cached)
        archive.assert_not_called()  # 只需要最新季度，首頁一次請求就夠

    def test_fetch_holdings_falls_back_to_archives(self):
        raw = pd.DataFrame({
            "股票代码": ["MSFT"], "股票名称": ["微软"],
            "占净值比例": [5.0], "季度": ["2026年2季度股票投资明细"],
        })
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("fa.data.holdings.requests.get",
                           return_value=_FakeResponse("<html></html>")), \
                mock.patch("fa.data.holdings.ak.fund_portfolio_hold_em",
                           return_value=raw) as archive:
            df = fetch_holdings("000369", Path(tmp), use_cache=False)

        self.assertEqual(df["股票代码"].iloc[0], "MSFT")
        archive.assert_called()

    def test_fetch_holdings_falls_back_to_stale_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "000369_holdings_raw.csv"
            pd.DataFrame({"股票代码": ["LLY"], "季度": ["2025年1季度"]}).to_csv(
                target, index=False, encoding="utf-8-sig")
            with mock.patch("fa.data.holdings.ak.fund_portfolio_hold_em",
                            side_effect=RuntimeError("SSL EOF")), \
                    mock.patch("fa.data.holdings.requests.get",
                               side_effect=RuntimeError("down")), \
                    mock.patch("fa.data.holdings.time.sleep"):
                df = fetch_holdings("000369", Path(tmp), use_cache=False)

        self.assertEqual(len(df), 1)
        self.assertEqual(df["股票代码"].iloc[0], "LLY")

    def test_read_stale_cache_missing_file(self):
        self.assertIsNone(read_stale_cache(Path("nope") / "x.csv", "000369"))



if __name__ == "__main__":
    unittest.main()
