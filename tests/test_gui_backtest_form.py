# -*- coding: utf-8 -*-
"""Stage C form tests (C-01…C-05, C-19): conversion, sentinel, JSON round trip."""
from __future__ import annotations

import unittest
from datetime import date

from fa.application import backtesting as service
from fa.backtesting.models import (
    COST_MODEL_DEFAULT,
    BacktestSpecError,
    CostModel,
    Weighting,
)
from fa.gui.backtest_window import BacktestForm


def _form(**overrides) -> BacktestForm:
    values = dict(
        spec_id="form-test",
        universe=[{"ticker": "LLY", "market": "US", "currency": "USD"}],
        start="2025-01-06",
        end="2025-03-03",
        initial_capital="700000",
        weighting="equal",
    )
    values.update(overrides)
    return BacktestForm(**values)


class FormMappingTests(unittest.TestCase):
    def test_every_spec_field_is_mapped(self):
        spec = _form(
            weighting="fixed", target_weights="LLY=1.0", benchmark=".NDX",
            drift_threshold="0.05", rebalance_frequency="weekly",
            rebalance_anchor="2",
        ).to_spec()
        self.assertEqual(spec.spec_id, "form-test")
        self.assertEqual(spec.universe[0].ticker, "LLY")
        self.assertEqual(spec.start, date(2025, 1, 6))
        self.assertEqual(spec.end, date(2025, 3, 3))
        self.assertEqual(spec.initial_capital, 700_000.0)
        self.assertEqual(spec.weighting, Weighting.FIXED)
        self.assertEqual(dict(spec.target_weights), {"LLY": 1.0})
        self.assertEqual(spec.benchmark, ".NDX")
        self.assertEqual(spec.rebalance.frequency.value, "weekly")
        self.assertEqual(spec.rebalance.anchor, 2)
        self.assertAlmostEqual(spec.drift_threshold, 0.05)

    def test_runtime_settings_are_not_form_fields(self):
        names = set(BacktestForm().__dataclass_fields__)
        for forbidden in ("fractional_shares", "decision_lag_minutes",
                          "execution_lag_sessions", "risk_free_rate"):
            self.assertNotIn(forbidden, names)

    def test_blank_universe_rows_are_ignored(self):
        spec = _form(universe=[
            {"ticker": "", "market": "US", "currency": "USD"},
            {"ticker": "ABBV", "market": "US", "currency": "USD"},
        ]).to_spec()
        self.assertEqual([item.ticker for item in spec.universe], ["ABBV"])

    def test_from_spec_round_trips_the_visible_values(self):
        spec = _form(weighting="momentum", momentum_window="30").to_spec()
        restored = BacktestForm.from_spec(spec).to_spec()
        self.assertEqual(restored.weighting, Weighting.MOMENTUM)
        self.assertEqual(restored.momentum_window, 30)
        self.assertEqual(restored.universe, spec.universe)


class SentinelTests(unittest.TestCase):
    def test_default_cost_mode_keeps_the_sentinel(self):
        spec = _form(cost_mode="default").to_spec()
        self.assertIs(spec.costs, COST_MODEL_DEFAULT)

    def test_custom_cost_mode_is_explicit(self):
        spec = _form(cost_mode="custom", commission_rate="0.0012",
                     min_commission="1.5", slippage_bps="7",
                     fx_spread_bps="11").to_spec()
        self.assertIsNot(spec.costs, COST_MODEL_DEFAULT)
        self.assertEqual(spec.costs, CostModel(0.0012, 1.5, 7.0, 11.0))

    def test_from_spec_recovers_the_cost_mode(self):
        explicit = _form(cost_mode="custom", commission_rate="0.002").to_spec()
        self.assertEqual(BacktestForm.from_spec(explicit).cost_mode, "custom")
        default = _form().to_spec()
        self.assertEqual(BacktestForm.from_spec(default).cost_mode, "default")


class InvalidInputTests(unittest.TestCase):
    def test_invalid_date_is_a_spec_error(self):
        with self.assertRaises(BacktestSpecError):
            _form(start="2025/01/06").to_spec()

    def test_invalid_number_is_a_spec_error(self):
        with self.assertRaises(BacktestSpecError):
            _form(initial_capital="lots").to_spec()

    def test_weights_need_ticker_equals_value(self):
        with self.assertRaises(BacktestSpecError):
            _form(weighting="fixed", target_weights="LLY 1.0").to_spec()

    def test_fixed_weights_must_cover_the_universe(self):
        with self.assertRaises(BacktestSpecError):
            _form(weighting="fixed", target_weights="ABBV=1.0").to_spec()

    def test_engine_validation_is_not_duplicated(self):
        # The GUI must surface the engine's own reason, not a lookalike.
        with self.assertRaises(BacktestSpecError) as caught:
            _form(universe=[]).to_spec()
        self.assertIn("universe", str(caught.exception))


class JsonRoundTripTests(unittest.TestCase):
    def test_form_to_json_and_back_preserves_the_sentinel(self):
        spec = _form().to_spec()
        payload = service.spec_to_json(spec)
        self.assertIsNone(payload["costs"])
        restored = service.spec_from_json(payload)
        self.assertIs(restored.costs, COST_MODEL_DEFAULT)
        self.assertEqual(restored.start, spec.start)

    def test_explicit_cost_model_round_trips(self):
        spec = _form(cost_mode="custom", commission_rate="0.003").to_spec()
        restored = service.spec_from_json(service.spec_to_json(spec))
        self.assertEqual(restored.costs, spec.costs)


if __name__ == "__main__":
    unittest.main()
