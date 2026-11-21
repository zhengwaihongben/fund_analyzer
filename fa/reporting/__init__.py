# -*- coding: utf-8 -*-
"""Report renderers (V2.4 Stage D): pure text from persisted records."""
from __future__ import annotations

from fa.reporting.backtest_report import render as render_backtest
from fa.reporting.estimation_report import render as render_estimation

__all__ = ["render_backtest", "render_estimation"]
