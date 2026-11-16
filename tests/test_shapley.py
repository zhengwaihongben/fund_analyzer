"""Shapley 歸因（V2.2）：本地 + 匯率 == 該資產人民幣貢獻。"""

from __future__ import annotations

import unittest

from fa.analysis.valuation import renormalised_error, value_portfolio
from fa.models.asset import AssetType
from fa.models.holding import HoldingSnapshot
from fa.models.return_result import (
    AssetReturnResult,
    ReturnStatus,
    shapley_contributions,
)


def asset_result(asset_id: str, weight: float, local: float, fx: float):
    return AssetReturnResult(asset_id=asset_id, status=ReturnStatus.OK,
                             weight=weight, local_return=local, fx_return=fx)


def snapshot(*weights: float) -> HoldingSnapshot:
    from datetime import date

    from fa.models.asset import Asset

    assets = [
        Asset(asset_id=f"A{i}", name=f"資產{i}", asset_type=AssetType.EQUITY,
              currency="USD", weight=w)
        for i, w in enumerate(weights)
    ]
    return HoldingSnapshot(fund_code="000369", assets=assets, quarter="2025Q1",
                           holding_date=date(2025, 3, 31),
                           disclosure_date=date(2025, 4, 21))


class ShapleyTests(unittest.TestCase):
    def test_spec_example_10_43_percent(self):
        """w=10.43 %、本地 +10 %、匯率 +1 % → 人民幣 11.1 %，歸因 1.0482/0.1095。"""
        result = asset_result("LLY", 0.1043, 0.10, 0.01)
        self.assertAlmostEqual(result.return_cny, 0.111, places=12)
        self.assertAlmostEqual(result.local_contribution, 0.01048215, places=12)
        self.assertAlmostEqual(result.fx_contribution, 0.00109515, places=12)
        self.assertAlmostEqual(result.contribution, 0.1043 * 0.111, places=12)

    def test_local_plus_fx_equals_cny_contribution(self):
        for weight, local, fx in [
            (0.5, 0.10, 0.01), (0.3, -0.05, 0.02), (0.2, 0.0, -0.03),
            (0.1, 0.08, 0.0), (0.05, -0.02, -0.01),
        ]:
            result = asset_result("X", weight, local, fx)
            self.assertAlmostEqual(
                result.local_contribution + result.fx_contribution,
                weight * result.return_cny,
                places=12,
            )
            c_local, c_fx = shapley_contributions(weight, local, fx)
            self.assertAlmostEqual(c_local, result.local_contribution, places=12)
            self.assertAlmostEqual(c_fx, result.fx_contribution, places=12)

    def test_cny_asset_has_no_fx_component(self):
        """人民幣現金/資產：r_fx = 0 → 全部歸因到本地回報。"""
        result = asset_result("CASH", 0.05, 0.00164514, 0.0)
        self.assertAlmostEqual(result.fx_contribution, 0.0, places=12)
        self.assertAlmostEqual(result.local_contribution,
                               0.05 * 0.00164514, places=12)

    def test_portfolio_sum_of_contributions_equals_return(self):
        portfolio = value_portfolio(
            snapshot(0.5, 0.3, 0.1),
            [asset_result("A0", 0.5, 0.10, 0.01),
             asset_result("A1", 0.3, -0.02, 0.03),
             asset_result("A2", 0.1, 0.04, -0.01)],
        )
        total = portfolio.local_contribution + portfolio.fx_contribution
        self.assertAlmostEqual(total, portfolio.estimated_return, places=12)
        self.assertAlmostEqual(renormalised_error(portfolio), 0.0, places=12)

    def test_mixed_currency_portfolio_keeps_identity(self):
        portfolio = value_portfolio(
            snapshot(0.4, 0.2, 0.2),
            [asset_result("A0", 0.4, 0.02, 0.01),
             asset_result("A1", 0.2, 0.03, -0.02),
             asset_result("A2", 0.2, -0.01, 0.0)],
        )
        self.assertAlmostEqual(portfolio.estimated_return,
                               0.4 * (1.02 * 1.01 - 1)
                               + 0.2 * (1.03 * 0.98 - 1)
                               + 0.2 * (-0.01), places=12)


if __name__ == "__main__":
    unittest.main()
