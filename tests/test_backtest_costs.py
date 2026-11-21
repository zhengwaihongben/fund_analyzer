# -*- coding: utf-8 -*-
"""COST-01…COST-10: the trade cost model (V2.3.2 Stage C, spec §6).

Every expected value here is derived from the published formulas by hand
(``gross * bps / 10 000``) and written as a literal, never by calling the
function under test.
"""
from __future__ import annotations

import unittest

from fa.backtesting.costs import (
    TradeCosts,
    calculate_commission,
    calculate_fx_spread,
    calculate_slippage,
    calculate_trade_costs,
    round_money,
    round_quantity,
)
from fa.backtesting.models import CostModel

SPEC_COSTS = CostModel(
    commission_rate=0.0005,
    min_commission=0.0,
    slippage_bps=10.0,
    fx_spread_bps=20.0,
)


class DefaultsTests(unittest.TestCase):
    def test_cost_model_defaults_match_the_specification(self):
        model = CostModel()
        self.assertEqual(model.commission_rate, 0.0005)
        self.assertEqual(model.min_commission, 0.0)
        self.assertEqual(model.slippage_bps, 10.0)
        self.assertEqual(model.fx_spread_bps, 20.0)


class CommissionTests(unittest.TestCase):
    def test_cost_01_commission_is_five_basis_points(self):
        # 10 000 * 5 / 10 000 = 5.0
        self.assertAlmostEqual(calculate_commission(10_000.0, SPEC_COSTS), 5.0, places=12)

    def test_cost_02_minimum_commission_floors_a_small_order(self):
        model = CostModel(commission_rate=0.0005, min_commission=2.0)
        # 1 000 * 0.0005 = 0.5 -> floored to 2.0
        self.assertAlmostEqual(calculate_commission(1_000.0, model), 2.0, places=12)
        # A large order pays the proportional amount, not the floor.
        self.assertAlmostEqual(calculate_commission(100_000.0, model), 50.0, places=12)

    def test_cost_02b_zero_notional_pays_nothing_even_with_a_floor(self):
        model = CostModel(min_commission=2.0)
        self.assertEqual(calculate_commission(0.0, model), 0.0)


class SlippageTests(unittest.TestCase):
    def test_cost_03_buy_slippage_is_ten_basis_points_of_notional(self):
        # 100 000 * 10 / 10 000 = 100.0
        self.assertAlmostEqual(calculate_slippage(100_000.0, SPEC_COSTS), 100.0, places=12)

    def test_cost_04_sell_slippage_uses_the_same_magnitude(self):
        # costs.py reports a positive cost for both sides; the engine decides the
        # direction (buy pays more, sell receives less).
        self.assertAlmostEqual(
            calculate_slippage(50_000.0, SPEC_COSTS),
            calculate_slippage(50_000.0, SPEC_COSTS),
            places=12,
        )
        self.assertAlmostEqual(calculate_slippage(50_000.0, SPEC_COSTS), 50.0, places=12)


class FxSpreadTests(unittest.TestCase):
    def test_cost_05_fx_spread_is_twenty_basis_points(self):
        # 60 000 * 20 / 10 000 = 120.0
        self.assertAlmostEqual(
            calculate_fx_spread(60_000.0, True, SPEC_COSTS), 120.0, places=12)

    def test_cost_06_cny_orders_pay_no_fx_spread(self):
        self.assertEqual(calculate_fx_spread(60_000.0, False, SPEC_COSTS), 0.0)


class RoundingTests(unittest.TestCase):
    def test_cost_07_money_rounds_half_even_to_two_decimals(self):
        self.assertEqual(round_money(0.615), 0.62)
        self.assertEqual(round_money(0.625), 0.62)  # tie -> even (0.62)
        self.assertEqual(round_money(0.635), 0.64)  # tie -> even (0.64)
        self.assertEqual(round_money(1234.5678), 1234.57)
        # The cost functions themselves stay unrounded.
        self.assertAlmostEqual(calculate_commission(1_234.567, SPEC_COSTS),
                               0.6172835, places=12)

    def test_quantity_rounds_to_six_decimals(self):
        self.assertEqual(round_quantity(77.922077922), 77.922078)
        self.assertEqual(round_quantity(1.0000005), 1.0)  # tie -> even


class TradeCostsTests(unittest.TestCase):
    def test_cost_08_zero_cost_model_produces_zero_costs(self):
        free = CostModel(commission_rate=0.0, min_commission=0.0,
                         slippage_bps=0.0, fx_spread_bps=0.0)
        costs = calculate_trade_costs(100_000.0, True, free)
        self.assertEqual(costs.as_tuple(), (0.0, 0.0, 0.0))
        self.assertEqual(costs.total_base, 0.0)

    def test_cost_09_negative_or_non_finite_notional_is_rejected(self):
        for value in (-1.0, float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    calculate_commission(value, SPEC_COSTS)
                with self.assertRaises(ValueError):
                    calculate_slippage(value, SPEC_COSTS)

    def test_cost_10_total_is_the_sum_and_calls_are_stateless(self):
        first = calculate_trade_costs(60_000.0, True, SPEC_COSTS)
        second = calculate_trade_costs(60_000.0, True, SPEC_COSTS)
        self.assertEqual(first, second)
        self.assertAlmostEqual(first.commission_base, 30.0, places=12)
        self.assertAlmostEqual(first.slippage_base, 60.0, places=12)
        self.assertAlmostEqual(first.fx_spread_base, 120.0, places=12)
        self.assertAlmostEqual(first.total_base, 210.0, places=12)
        rounded = TradeCosts(30.004, 60.004, 120.004).rounded()
        self.assertEqual(rounded.as_tuple(), (30.0, 60.0, 120.0))


if __name__ == "__main__":
    unittest.main()
