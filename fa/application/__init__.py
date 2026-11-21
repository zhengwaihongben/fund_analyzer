# -*- coding: utf-8 -*-
"""應用服務層：GUI 與估算／回測子系統之間的協調者。

GUI 只負責顯示，Repository 只在 worker thread 內建立與關閉；
Putting the orchestration here keeps the windows free of database
bookkeeping and keeps the estimation maths out of the widgets.
"""
from __future__ import annotations

from fa.application.estimation import (
    DEFAULT_DATABASE,
    EstimationView,
    list_estimates,
    load_estimate,
    run_estimate,
)

__all__ = [
    "DEFAULT_DATABASE",
    "EstimationView",
    "list_estimates",
    "load_estimate",
    "run_estimate",
]
