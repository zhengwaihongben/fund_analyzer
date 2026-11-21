# -*- coding: utf-8 -*-
"""Persistence of QDII estimates (JSON, history CSV, input snapshots).

The analysis layer must not write files: it hands the finished record to this
module.  Three artefacts are kept, each with a different question in mind:

* ``_qdii_estimate.json`` — the latest result, for the GUI and the reports;
* ``_qdii_estimates.csv`` — one row per **run** (``run_id`` is the identity),
  which the backtest reads;
* ``_qdii_estimate_inputs.json`` — the complete inputs of every run, keyed by
  ``run_id``, so an old estimate can be reproduced after Yahoo revises history.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from fa.analysis.estimation_backtest import backfill_history, read_history, write_history
from fa.utils.logging_setup import get_logger

log = get_logger(__name__)

ESTIMATE_SUFFIX = "_qdii_estimate.json"
HISTORY_SUFFIX = "_qdii_estimates.csv"
INPUTS_SUFFIX = "_qdii_estimate_inputs.json"


def persist_estimate(record: dict, save_dir: Path, nav_df: pd.DataFrame) -> None:
    """Write the latest estimate, append one history row, keep the inputs."""
    save_dir = Path(save_dir)
    json_path = save_dir / f"{record['fund_code']}{ESTIMATE_SUFFIX}"
    try:
        json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    except OSError as exc:
        log.warning("QDII 估算結果寫入失敗: %s", exc)

    history_path = save_dir / f"{record['fund_code']}{HISTORY_SUFFIX}"
    history = read_history(history_path)
    row = {
        "run_id": record.get("run_id"),
        "computed_at": record["computed_at"],
        "target_nav_date": record["target_nav_date"],
        "base_nav_date": record["base_nav_date"],
        "base_nav": record["base_nav"],
        "estimated_nav": record["estimated_nav"],
        "estimated_return": record["estimated_return"],
        "coverage_ratio": record["coverage_ratio"],
        "uncovered_weight": record.get("uncovered_weight"),
        "holdings_quarter": record.get("holdings_quarter"),
        "holdings_date": record.get("holdings_date"),
        "disclosure_date": record.get("disclosure_date"),
        "disclosure_date_estimated": record.get("disclosure_date_estimated"),
        "look_ahead_safe": record.get("look_ahead_safe"),
        "look_ahead_status": record.get("look_ahead_status"),
        "holdings_source": record.get("holdings_source"),
        "data_quality": record.get("data_quality"),
        "status": record["status"],
        "official_nav": None,
        "error": None,
        "abs_error": None,
    }
    history = pd.concat([history, pd.DataFrame([row])], ignore_index=True) \
        if not history.empty else pd.DataFrame([row])
    # ``run_id`` is the identity of a run: two runs for the same target date are
    # two different estimates and both must survive.
    identity = "run_id" if "run_id" in history.columns else "target_nav_date"
    history = history.drop_duplicates(subset=[identity], keep="last")
    history = backfill_history(history, nav_df)
    write_history(history, history_path)
    write_inputs_snapshot(record, save_dir)


def write_inputs_snapshot(record: dict, save_dir: Path) -> None:
    """Keep the complete input snapshot of every estimate, per ``run_id``.

    The history CSV only carries the summary line; without the per-holding
    prices, FX rates and the dates they were quoted on, an old estimate cannot
    be reproduced or its error traced back to one holding.
    """
    path = Path(save_dir) / f"{record.get('fund_code')}{INPUTS_SUFFIX}"
    snapshots: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(existing, dict) and isinstance(existing.get("snapshots"), dict):
                snapshots = existing["snapshots"]
        except (OSError, ValueError) as exc:
            log.warning("QDII 估算輸入快照不可讀（%s），重新建立", exc)
    snapshots[str(record.get("run_id") or record.get("target_nav_date"))] = record
    payload = {
        "fund_code": record.get("fund_code"),
        "updated_at": record.get("computed_at"),
        "snapshots": snapshots,
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError as exc:
        log.warning("QDII 估算輸入快照寫入失敗: %s", exc)


__all__ = [
    "ESTIMATE_SUFFIX",
    "HISTORY_SUFFIX",
    "INPUTS_SUFFIX",
    "persist_estimate",
    "write_inputs_snapshot",
]
