"""組合估值（V2.2 第 3 步）：權重不重標準化、覆蓋率與狀態。"""

from __future__ import annotations

import unittest
from datetime import date

from fa.analysis.valuation import renormalised_error, value_portfolio
from fa.models.asset import Asset, AssetType
from fa.models.holding import HoldingSnapshot
from fa.models.return_result import (
    AssetReturnResult,
    PortfolioStatus,
    ReturnStatus,
)

BASE = date(2025, 6, 6)
TARGET = date(2025, 6, 9)


def snapshot(*weights: float) -> HoldingSnapshot:
    assets = [
        Asset(asset_id=f"A{i}", name=f"資產{i}", asset_type=AssetType.EQUITY,
              currency="USD", weight=w)
        for i, w in enumerate(weights)
    ]
    return HoldingSnapshot(fund_code="000369", assets=assets, quarter="2025Q1",
                           holding_date=date(2025, 3, 31),
                           disclosure_date=date(2025, 4, 21))


def result(asset_id: str, weight: float, local: float | None,
           fx: float = 0.0, status: ReturnStatus = ReturnStatus.OK):
    if status is not ReturnStatus.OK or local is None:
        return AssetReturnResult(asset_id=asset_id, status=status, weight=weight)
    return AssetReturnResult(asset_id=asset_id, status=ReturnStatus.OK,
                             weight=weight, local_return=local, fx_return=fx,
                             base_price_date=BASE, target_price_date=TARGET)


class PortfolioValuationTests(unittest.TestCase):
    def test_case_e_matches_spec(self):
        """20 % / 30 % / 10 % 權重的 10 % / 5 % / -2 % → 3.3 %，覆蓋 60 %。"""
        portfolio = value_portfolio(
            snapshot(0.20, 0.30, 0.10),
            [result("A0", 0.20, 0.10), result("A1", 0.30, 0.05),
             result("A2", 0.10, -0.02)],
        )
        self.assertAlmostEqual(portfolio.estimated_return, 0.033, places=10)
        self.assertAlmostEqual(portfolio.covered_weight, 0.60, places=10)
        self.assertAlmostEqual(portfolio.uncovered_weight, 0.40, places=10)
        self.assertAlmostEqual(portfolio.coverage_ratio, 0.60, places=10)
        self.assertEqual(portfolio.status, PortfolioStatus.PARTIAL)
        self.assertEqual(portfolio.holdings_included, 3)
        self.assertEqual(portfolio.holdings_excluded, 0)

    def test_weights_are_never_renormalised(self):
        """缺一隻 10 % 的持倉時回報是 3.5 %，不是重標準化後的 5.83 %。"""
        portfolio = value_portfolio(
            snapshot(0.20, 0.30, 0.10),
            [result("A0", 0.20, 0.10), result("A1", 0.30, 0.05),
             result("A2", 0.10, None, status=ReturnStatus.MISSING_PRICE)],
        )
        self.assertAlmostEqual(portfolio.estimated_return, 0.035, places=10)
        self.assertAlmostEqual(portfolio.covered_weight, 0.50, places=10)
        self.assertAlmostEqual(portfolio.uncovered_weight, 0.50, places=10)
        self.assertEqual(portfolio.holdings_excluded, 1)

    def test_all_covered_is_estimated(self):
        portfolio = value_portfolio(snapshot(0.6, 0.4),
                                    [result("A0", 0.6, 0.01),
                                     result("A1", 0.4, 0.02)])
        self.assertEqual(portfolio.status, PortfolioStatus.ESTIMATED)
        self.assertAlmostEqual(portfolio.uncovered_weight, 0.0, places=12)
        self.assertAlmostEqual(portfolio.coverage_of_known, 1.0, places=12)

    def test_nothing_usable_is_insufficient_data(self):
        portfolio = value_portfolio(
            snapshot(0.5, 0.5),
            [result("A0", 0.5, None, status=ReturnStatus.MISSING_FX),
             result("A1", 0.5, None, status=ReturnStatus.MISSING_PRICE)],
        )
        self.assertEqual(portfolio.status, PortfolioStatus.INSUFFICIENT_DATA)
        self.assertAlmostEqual(portfolio.covered_weight, 0.0, places=12)
        self.assertAlmostEqual(portfolio.uncovered_weight, 1.0, places=12)
        self.assertIsNone(portfolio.estimated_return)

    def test_top10_snapshot_reports_two_coverage_metrics(self):
        """前十大合計 46 % 全部有價 → 覆蓋率 46 %、已揭露覆蓋 100 %。"""
        portfolio = value_portfolio(
            snapshot(0.30, 0.16),
            [result("A0", 0.30, 0.01), result("A1", 0.16, 0.01)],
        )
        self.assertAlmostEqual(portfolio.coverage_ratio, 0.46, places=10)
        self.assertAlmostEqual(portfolio.coverage_of_known, 1.0, places=10)
        self.assertLess(portfolio.coverage_ratio, 1.0)
        self.assertEqual(portfolio.status, PortfolioStatus.PARTIAL)

    def test_warns_about_uncovered_weight(self):
        portfolio = value_portfolio(snapshot(0.2), [result("A0", 0.2, 0.01)])
        self.assertTrue(any("未覆蓋" in w for w in portfolio.warnings))

    def test_missing_snapshot_leaves_everything_uncovered(self):
        portfolio = value_portfolio(None, [result("A0", 0.3, 0.01)])
        self.assertAlmostEqual(portfolio.uncovered_weight, 0.70, places=10)

    def test_shapley_identity_holds_for_the_aggregate(self):
        portfolio = value_portfolio(
            snapshot(0.5, 0.3),
            [result("A0", 0.5, 0.10, fx=0.01), result("A1", 0.3, -0.02, fx=-0.03)],
        )
        self.assertAlmostEqual(renormalised_error(portfolio), 0.0, places=12)
        self.assertAlmostEqual(portfolio.total_contribution,
                               portfolio.estimated_return, places=12)


if __name__ == "__main__":
    unittest.main()
