# -*- coding: utf-8 -*-
"""The return calculators must reject non-``Asset`` input.

Each concrete calculator re-checks the type even though ``calculate_asset``
already does, because the classes are public: calling one directly with a ticker
string would otherwise reach ``priced_return`` and fail somewhere less obvious.
"""

from __future__ import annotations

import unittest
from datetime import date

from fa.analysis.returns.base import ReturnCalculatorError
from fa.analysis.returns.bond_etf import BondEtfReturnCalculator
from fa.analysis.returns.equity import EquityReturnCalculator
from fa.analysis.returns.etf import EtfReturnCalculator


class CalculatorTypeGuardTests(unittest.TestCase):
    def _call(self, calculator) -> None:  # noqa: ANN001
        with self.assertRaises(ReturnCalculatorError):
            calculator.calculate(
                "LLY",  # type: ignore[arg-type]
                base_date=date(2025, 6, 6),
                target_date=date(2025, 6, 9),
                prices=None,
                fx=None,
            )

    def test_equity_rejects_a_non_asset(self) -> None:
        self._call(EquityReturnCalculator())

    def test_etf_rejects_a_non_asset(self) -> None:
        self._call(EtfReturnCalculator())

    def test_bond_etf_rejects_a_non_asset(self) -> None:
        self._call(BondEtfReturnCalculator())


if __name__ == "__main__":
    unittest.main()
