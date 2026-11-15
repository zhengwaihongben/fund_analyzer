# -*- coding: utf-8 -*-
"""analyze_fund 的整合測試：只 stub 網絡抓取，其餘（指標 / 圖表 / 匯總）真跑。"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from fa.pipeline import analyze_fund


def make_nav(n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame({"Date": idx, "NAV": np.cumprod(1 + rng.normal(4e-4, 0.012, n))})


class AnalyzeFundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out_dir = self.tmp / "out"
        self.save = self.out_dir / "017436"
        self.save.mkdir(parents=True)
        self.nav = make_nav()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, holdings=None, use_cache=True):
        nav = self.nav

        def fake_fetch_nav(fund_code, save_dir, start_date=None):
            # 真實的 fetch_nav 會把原始淨值寫成緩存文件
            dst = Path(save_dir) / f"{fund_code}_daily_nav.csv"
            nav.to_csv(dst, index=False)
            return nav

        patches = mock.patch.multiple(
            "fa.pipeline",
            fetch_nav=mock.DEFAULT,
            fetch_aum=mock.DEFAULT,
            fetch_holdings=mock.DEFAULT,
        )
        with patches as m:
            m["fetch_nav"].side_effect = fake_fetch_nav
            m["fetch_aum"].return_value = (None, None)
            m["fetch_holdings"].return_value = (
                holdings if holdings is not None else pd.DataFrame())
            return analyze_fund("017436", base_output=self.out_dir,
                                cfg={"benchmark": None}, use_cache=use_cache)

    def test_success_summary_and_files(self):
        summary = self._run()

        self.assertTrue(summary["success"])
        self.assertIsNone(summary["error"])

        summary_path = self.save / "017436_summary.json"
        self.assertTrue(summary_path.exists())
        on_disk = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertTrue(on_disk["success"])

        for name in on_disk["files"].values():
            if name:
                self.assertTrue((self.save / name).exists(), name)

        self.assertTrue((self.save / "017436_daily_nav.csv").exists())

    def test_holdings_cache_survives_the_run(self):
        raw = self.save / "017436_holdings_raw.csv"
        raw.write_text("StockCode,Weight\n0700,5.0\n", encoding="utf-8")
        stale = self.save / "017436_stale_report.html"
        stale.write_text("old", encoding="utf-8")

        self._run()

        self.assertTrue(raw.exists(), "持倉緩存不應被清空")
        self.assertFalse(stale.exists(), "上次遺留的派生報告應被清理")

    def test_holdings_empty_removes_stale_clean_csv(self):
        clean = self.save / "017436_holdings_clean.csv"
        clean.write_text("StockCode,Weight\n0700,5.0\n", encoding="utf-8")

        self._run(holdings=pd.DataFrame())

        self.assertFalse(clean.exists())
        self.assertIsNone(
            json.loads((self.save / "017436_summary.json").read_text(encoding="utf-8"))
            ["files"]["holdings_clean"])

    def test_risk_metrics_use_official_nav_only(self):
        summary = self._run()
        metrics = summary["metrics"]
        # 300 個真實淨值 → pct_change 之後 299 個日收益
        self.assertEqual(metrics["trading_days"], 299)

    def test_no_estimation_section_in_summary(self):
        summary = self._run()
        self.assertNotIn("estimation", summary)
        on_disk = json.loads(
            (self.save / "017436_summary.json").read_text(encoding="utf-8"))
        self.assertNotIn("estimation", on_disk)

    def test_empty_nav_reports_error_without_raising(self):
        with mock.patch.multiple(
            "fa.pipeline",
            fetch_nav=mock.DEFAULT,
            fetch_aum=mock.DEFAULT,
        ) as m:
            m["fetch_nav"].return_value = pd.DataFrame()
            m["fetch_aum"].return_value = (None, None)
            summary = analyze_fund("017436", base_output=self.out_dir,
                                   cfg={"benchmark": None})

        self.assertFalse(summary["success"])
        self.assertIn("净值数据为空", summary["error"])


if __name__ == "__main__":
    unittest.main()
