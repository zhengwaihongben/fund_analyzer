# -*- coding: utf-8 -*-
"""技術指標與風險指標的單元測試（全部使用合成數據，不訪問網絡）。"""
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from fa.analysis.risk import compute_risk_metrics
from fa.analysis.technical import compute_technical
from fa.config import merge_config
from fa.viz.charts import plot_holdings


def make_nav(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-02", periods=n)
    nav = np.cumprod(1 + rng.normal(4e-4, 0.012, n))
    return pd.DataFrame({"Date": idx, "NAV": nav})


class TechnicalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = merge_config()
        self.df = make_nav()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_indicator_columns_are_renamed_to_nav_proxies(self):
        out = compute_technical(self.df, self.cfg, self.tmp, "017436")
        for col in ("SMA5", "SMA60", "SMA250", "BOLL_Mid", "BOLL_Upper",
                    "RSI", "MACD_DIF", "KDJ_K", "NAV_VOL", "NAV_DX",
                    "NAV_DI_Plus", "NAV_DI_Minus"):
            self.assertIn(col, out.columns)
        # 基金只有 NAV，沒有 High/Low/Close，標準 ATR/ADX 不可能成立
        self.assertNotIn("ATR", out.columns)
        self.assertNotIn("ADX", out.columns)

    def test_warmup_uses_full_window(self):
        out = compute_technical(self.df, self.cfg, self.tmp, "017436")
        self.assertTrue(out["SMA60"].iloc[:59].isna().all())
        self.assertFalse(np.isnan(out["SMA60"].iloc[59]))
        self.assertTrue(out["BOLL_Mid"].iloc[:19].isna().all())
        self.assertFalse(np.isnan(out["BOLL_Mid"].iloc[19]))

    def test_writes_technical_csv(self):
        compute_technical(self.df, self.cfg, self.tmp, "017436")
        path = self.tmp / "017436_daily_nav_sma_boll.csv"
        self.assertTrue(path.exists())
        self.assertIn("NAV_DX", pd.read_csv(path).columns)


class RiskMetricTests(unittest.TestCase):
    def setUp(self):
        self.cfg = merge_config()
        self.df = make_nav()

    def _expected_downside_deviation(self) -> float:
        td = self.cfg["trading_days"]
        rf = self.cfg["risk_free"]
        ret = self.df["NAV"].pct_change().dropna()
        diff = np.minimum(ret - rf / td, 0.0)
        return float(np.sqrt(np.mean(diff ** 2)) * np.sqrt(td))

    def test_downside_deviation_uses_all_observations(self):
        m = compute_risk_metrics(self.df, self.cfg, "017436")
        self.assertAlmostEqual(
            m["downside_deviation"],
            round(self._expected_downside_deviation(), 4),
            places=4,
        )
        self.assertNotIn("downside_vol", m)

    def test_sortino_divides_by_downside_deviation(self):
        m = compute_risk_metrics(self.df, self.cfg, "017436")
        td = self.cfg["trading_days"]
        ret = self.df["NAV"].pct_change().dropna()
        ann_ret = (1 + ret).prod() ** (td / len(ret)) - 1
        expected = (ann_ret - self.cfg["risk_free"]) / self._expected_downside_deviation()
        self.assertAlmostEqual(m["sortino"], round(expected, 3), places=3)

    def test_metric_keys_present(self):
        m = compute_risk_metrics(self.df, self.cfg, "017436")
        for key in ("ann_return", "ann_vol", "sharpe", "sortino", "calmar",
                    "max_drawdown", "downside_deviation", "tracking_error",
                    "info_ratio", "ir_benchmark", "period", "years"):
            self.assertIn(key, m)

    def test_short_history_returns_empty(self):
        self.assertEqual(compute_risk_metrics(make_nav(10), self.cfg, "017436"), {})

    def test_info_ratio_aligns_on_common_trading_days(self):
        bench = self.df[["Date"]].copy()
        bench["BenchClose"] = np.linspace(100.0, 130.0, len(bench))
        bench = bench.iloc[5:-5].reset_index(drop=True)   # 缺口：只有 290 個共同交易日

        with mock.patch("fa.analysis.risk.fetch_benchmark", return_value=bench):
            m = compute_risk_metrics(self.df, self.cfg, "017436", benchmark=".NDX")

        self.assertIsNotNone(m["tracking_error"])
        self.assertIsNotNone(m["info_ratio"])
        self.assertEqual(m["ir_benchmark"], ".NDX")

        # 價格先對齊再算收益：共同交易日 290 天 → 289 個日收益
        px = self.df.merge(bench, on="Date").sort_values("Date").reset_index(drop=True)
        fund_ret = px["NAV"].pct_change()
        bench_ret = px["BenchClose"].pct_change()
        expected_te = (fund_ret - bench_ret).dropna().std() * np.sqrt(self.cfg["trading_days"])
        self.assertAlmostEqual(m["tracking_error"], round(float(expected_te), 4), places=4)

    def test_benchmark_failure_is_logged_not_fatal(self):
        with mock.patch("fa.analysis.risk.fetch_benchmark", side_effect=RuntimeError("boom")):
            m = compute_risk_metrics(self.df, self.cfg, "017436", benchmark=".NDX")
        self.assertIsNone(m["info_ratio"])
        self.assertIsNotNone(m["sharpe"])


class HoldingsChartTests(unittest.TestCase):
    """持倉只輸出最新一季，並以餅圖呈現（不再保留歷史季度）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.raw = pd.DataFrame({
            "股票代码": ["LLY", "JNJ", "LLY"],
            "股票名称": ["礼来", "强生", "礼来"],
            "占净值比例": [10.43, 7.33, 9.0],
            "季度": ["2026年3季度股票投资明细", "2026年3季度股票投资明细",
                     "2026年2季度股票投资明细"],
        })

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_latest_quarter_pie_and_clean_csv(self):
        out = plot_holdings(self.raw, "000369", self.tmp)

        self.assertIsNotNone(out)
        self.assertEqual(out.name, "000369_holdings_pie.html")
        html = out.read_text(encoding="utf-8")
        # Plotly 依版本決定把圖表腳本裡的中文寫成原字或 \uXXXX 轉義，兩種都算通過。
        visible = re.sub(r"\\u([0-9a-fA-F]{4})",
                         lambda m: chr(int(m.group(1), 16)), html)
        self.assertIn("最新季度持仓", visible)
        self.assertIn("其他（未披露）", visible)   # 未披露部分要畫成一片，不能藏起來

        clean = pd.read_csv(self.tmp / "000369_holdings_clean.csv", encoding="utf-8-sig")
        self.assertEqual(list(clean["QuarterLabel"].unique()), ["2026Q3"])
        self.assertEqual(len(clean), 2)         # 舊季度已被剔除
        self.assertFalse((self.tmp / "000369_holdings_area.html").exists())

    def test_empty_holdings_returns_none(self):
        self.assertIsNone(plot_holdings(pd.DataFrame(), "000369", self.tmp))


if __name__ == "__main__":
    unittest.main()
