# -*- coding: utf-8 -*-
"""Edge paths that keep the documented core modules above the 90 % gate.

Everything here is a *behaviour* check with a hand-written expectation, not a
line-chasing exercise: each test names the situation a caller can actually hit
(a cash holding whose FX is missing, an unknown return status, a benchmark NAV
that is not the last session, a calendar without a holiday table).
"""

from __future__ import annotations

import unittest
from datetime import date

from fa.analysis.returns.base import convert_to_cny
from fa.analysis.returns.cash import CashReturnCalculator
from fa.analysis.valuation.portfolio_valuation import renormalised_error
from fa.analysis.valuation_dates import _nav_dates
from fa.models import Asset, AssetType, PortfolioReturnResult, ReturnStatus
from fa.models.market_data import (
    AvailabilitySource,
    FxSeries,
    PricePoint,
)
from fa.models.return_result import AssetReturnResult
from fa.utils.data_quality import assess_quality


def _fx_point(day: date, rate: float) -> PricePoint:
    return PricePoint(
        price_date=day,
        close=rate,
        market="FX",
        currency="CNY",
        available_price_type="fx_close",
        used_price_type="fx_close",
    )


class CashGuardTests(unittest.TestCase):
    def test_cash_calculator_rejects_a_non_asset(self) -> None:
        with self.assertRaises(Exception):
            CashReturnCalculator().calculate(
                "USD",  # type: ignore[arg-type]
                base_date=date(2025, 6, 6),
                target_date=date(2025, 6, 9),
                prices=None,
                fx=None,
            )

    def test_a_cash_holding_without_fx_is_not_valued(self) -> None:
        asset = Asset("cash", "Cash USD", asset_type=AssetType.CASH,
                      currency="USD", weight=1.0)
        result = CashReturnCalculator().calculate(
            asset,
            base_date=date(2025, 6, 6),
            target_date=date(2025, 6, 9),
            prices=None,
            fx=None,
        )
        self.assertFalse(result.is_usable)
        self.assertEqual(result.status, ReturnStatus.MISSING_FX)


class FxAlignmentTests(unittest.TestCase):
    def test_a_target_rate_that_is_too_old_is_not_used(self) -> None:
        series = FxSeries.from_bars(
            "USD", [_fx_point(date(2025, 5, 1), 7.0)], source="test",
            base_currency="CNY",
        )
        outcome = convert_to_cny(
            "USD", series, date(2025, 5, 1), date(2025, 6, 9), calendar=None,
        )
        self.assertEqual(outcome.status, ReturnStatus.MISSING_FX)


class ValuationEdgeTests(unittest.TestCase):
    def test_renormalised_error_is_zero_without_an_estimate(self) -> None:
        portfolio = PortfolioReturnResult(status=None, estimated_return=None)  # type: ignore[arg-type]
        self.assertEqual(renormalised_error(portfolio), 0.0)

    def test_valuation_dates_accepts_no_published_dates(self) -> None:
        self.assertEqual(_nav_dates(None), [])
        self.assertEqual(_nav_dates([]), [])


class QualityPenaltyTests(unittest.TestCase):
    def test_an_unknown_status_is_treated_as_unsupported(self) -> None:
        report = assess_quality([{"status": "bogus", "weight": 0.5}])
        self.assertLess(report.score, 100.0)

    def test_a_stale_base_nav_costs_ten_points(self) -> None:
        fresh = assess_quality([{"status": "ok", "weight": 1.0}])
        stale = assess_quality([{"status": "ok", "weight": 1.0}], base_nav_fresh=False)
        self.assertAlmostEqual(fresh.score - stale.score, 10.0)

    def test_a_fallback_calendar_costs_five_points(self) -> None:
        normal = assess_quality([{"status": "ok", "weight": 1.0}])
        fallback = assess_quality([{"status": "ok", "weight": 1.0}],
                                  calendar_fallback=True)
        self.assertAlmostEqual(normal.score - fallback.score, 5.0)


if __name__ == "__main__":
    unittest.main()
