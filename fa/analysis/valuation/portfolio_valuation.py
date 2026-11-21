# -*- coding: utf-8 -*-
"""Portfolio valuation: weights, coverage and Shapley attribution.

The fund return is the weighted sum of the *CNY* returns of the assets that
could be valued::

    R_fund = Σ_{i ∈ C} w_i · r_i,CNY

with ``C`` the set of assets whose calculator returned ``ok``.  Two rules are
structural, not stylistic:

* **weights are never renormalised** — a portfolio with 46 % coverage must not
  report a return that silently assumes the other 54 % moved like the known
  part;
* **coverage and uncovered weight are separate facts** — ``coverage_ratio`` is
  the share of the fund, ``coverage_of_known`` the share of the disclosed
  holdings.

This module performs no I/O: it is handed results and returns the aggregate.
"""
from __future__ import annotations

from typing import Iterable

from fa.models.holding import HoldingSnapshot
from fa.models.return_result import (
    AssetReturnResult,
    PortfolioReturnResult,
    PortfolioStatus,
)


def value_portfolio(
    snapshot: HoldingSnapshot | None,
    results: Iterable[AssetReturnResult],
) -> PortfolioReturnResult:
    """Aggregate per-asset results into the fund-level CNY return.

    ``snapshot`` supplies the disclosed weight (the denominator of
    ``coverage_of_known``); it may be ``None`` when only results are available.
    """
    results = list(results)
    disclosed = float(snapshot.total_weight) if snapshot is not None else 0.0

    covered = 0.0
    estimated = 0.0
    local = 0.0
    fx = 0.0
    included = 0
    excluded = 0
    warnings: list[str] = []

    for result in results:
        if result.is_usable:
            covered += result.weight
            estimated += result.contribution
            local += result.local_contribution or 0.0
            fx += result.fx_contribution or 0.0
            included += 1
        else:
            excluded += 1

    uncovered = max(0.0, 1.0 - covered)
    if covered <= 0.0:
        status = PortfolioStatus.INSUFFICIENT_DATA
        message = "沒有任何資產可估值"
        warnings.append(message)
    elif uncovered > 0.001 or excluded:
        status = PortfolioStatus.PARTIAL
        warnings.append(
            f"未覆蓋 {uncovered * 100:.1f}% 的資產按 0% 回報處理（未知，非已知事實）"
        )
    else:
        status = PortfolioStatus.ESTIMATED

    for result in results:
        for warning in result.warnings:
            text = f"{result.asset_id}: {warning}"
            if text not in warnings:
                warnings.append(text)

    portfolio = PortfolioReturnResult(
        status=status,
        estimated_return=estimated if included else None,
        covered_weight=covered,
        uncovered_weight=uncovered,
        coverage_ratio=covered,
        local_contribution=local,
        fx_contribution=fx,
        holdings_included=included,
        holdings_excluded=excluded,
        assets=results,
        warnings=warnings,
        disclosed_weight=disclosed,
    )
    return portfolio


def renormalised_error(portfolio: PortfolioReturnResult) -> float:
    """Difference between the Shapley total and ``estimated_return`` (should be ~0).

    Exposed so tests and the backtest can assert the attribution identity
    ``Σ C_local + Σ C_fx == R_fund`` instead of trusting it.
    """
    if portfolio.estimated_return is None:
        return 0.0
    return portfolio.total_contribution - portfolio.estimated_return


__all__ = ["renormalised_error", "value_portfolio"]
