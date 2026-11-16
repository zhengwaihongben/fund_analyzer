# -*- coding: utf-8 -*-
"""FX return calculator (the return leg, not the downloader).

Distinct from :mod:`fa.data.fx`, which fetches ``<CUR>CNY`` series.  This module
turns two aligned rates into a return using the project-wide convention
``FX_t = CNY per one unit of foreign currency``::

    r_fx = FX_target / FX_base - 1

A CNY asset has ``r_fx = 0`` by definition and needs no series; an unknown
currency is **not** CNY and a missing rate is never invented.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fa.analysis.returns.base import FxOutcome, convert_to_cny
from fa.models.market_data import FxSeries


def compute_fx_return(
    currency: str | None,
    fx: FxSeries | None,
    base_date: date,
    target_date: date,
    calendar: Any = None,
) -> FxOutcome:
    """Return the FX leg for ``currency`` between the two valuation dates."""
    return convert_to_cny(currency, fx, base_date, target_date, calendar=calendar)


def describe_fx(outcome: FxOutcome) -> str:
    """Short human-readable summary for logs and GUI text."""
    if outcome.fx_return is None:
        return outcome.status.value
    return f"{outcome.fx_return * 100:+.2f}%"


__all__ = ["FxOutcome", "compute_fx_return", "describe_fx"]
