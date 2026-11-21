# -*- coding: utf-8 -*-
"""Backtesting for the QDII NAV estimator.

Every estimate is stored with the dates and inputs it used.  Once the fund
publishes the official NAV for a target date, that value is attached to the
history row and the error becomes measurable::

    error_t = (estimated_nav_t - official_nav_t) / official_nav_t

Reported metrics are MAE, RMSE, Mean Error (systematic bias) and Maximum
Absolute Error.  Because the history keeps one row per target date and only
uses the portfolio disclosed at that time, the comparison is look-ahead free.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

HISTORY_COLUMNS = [
    "run_id", "computed_at", "target_nav_date", "base_nav_date", "base_nav",
    "estimated_nav", "estimated_return", "coverage_ratio", "uncovered_weight",
    "holdings_quarter", "holdings_date", "disclosure_date",
    "disclosure_date_estimated", "look_ahead_safe", "look_ahead_status",
    "holdings_source", "data_quality", "status",
    "official_nav", "error", "abs_error",
]

_NUMERIC = ("base_nav", "estimated_nav", "estimated_return", "coverage_ratio",
            "uncovered_weight", "official_nav", "error", "abs_error")


def read_history(path: Path) -> pd.DataFrame:
    """Load the estimate history; a missing/corrupt file yields an empty frame."""
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    try:
        history = pd.read_csv(path, dtype={"holdings_quarter": str})
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        log.warning("QDII 估算歷史不可讀（%s），重新建立", exc)
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    for column in _NUMERIC:
        if column in history.columns:
            history[column] = pd.to_numeric(history[column], errors="coerce")
    return history


def write_history(history: pd.DataFrame, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = history.copy()
    for column in HISTORY_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame[HISTORY_COLUMNS].sort_values("target_nav_date").to_csv(
        path, index=False, encoding="utf-8-sig")
    return path


def backfill_history(history: pd.DataFrame, nav_df: pd.DataFrame) -> pd.DataFrame:
    """Attach the official NAV (and the resulting error) where it now exists."""
    if history is None or history.empty or nav_df is None or nav_df.empty:
        return history

    nav = nav_df.copy()
    nav["Date"] = pd.to_datetime(nav["Date"], errors="coerce").dt.normalize()
    nav["NAV"] = pd.to_numeric(nav["NAV"], errors="coerce")
    official = (
        nav.dropna(subset=["Date", "NAV"])
           .drop_duplicates(subset=["Date"], keep="last")
           .set_index("Date")["NAV"]
    )

    frame = history.copy()
    frame["target_nav_date"] = pd.to_datetime(frame["target_nav_date"], errors="coerce")
    frame["official_nav"] = frame["target_nav_date"].map(official)
    if "estimated_nav" in frame.columns:
        frame["estimated_nav"] = pd.to_numeric(frame["estimated_nav"], errors="coerce")
    else:
        frame["estimated_nav"] = float("nan")

    known = frame["official_nav"].notna() & frame["estimated_nav"].notna()
    frame.loc[known, "error"] = (
        frame.loc[known, "estimated_nav"] - frame.loc[known, "official_nav"]
    ) / frame.loc[known, "official_nav"]
    # error 欄在新鮮的歷史列裡是 None（object dtype），先轉數值再取絕對值
    frame["error"] = pd.to_numeric(frame["error"], errors="coerce")
    frame["abs_error"] = frame["error"].abs()
    frame["target_nav_date"] = frame["target_nav_date"].dt.strftime("%Y-%m-%d")
    return frame


def compute_metrics(history: pd.DataFrame) -> dict:
    """MAE / RMSE / mean error / max abs error over reconciled rows."""
    if history is None or history.empty or "error" not in history.columns:
        return {"n": 0}

    errors = pd.to_numeric(history["error"], errors="coerce").dropna()
    if errors.empty:
        return {"n": 0}

    covered = (pd.to_numeric(history["coverage_ratio"], errors="coerce")
               if "coverage_ratio" in history.columns else None)
    return {
        "n": int(len(errors)),
        "mae": float(errors.abs().mean()),
        "rmse": float((errors ** 2).mean() ** 0.5),
        "mean_error": float(errors.mean()),
        "max_abs_error": float(errors.abs().max()),
        "best": float(errors.abs().min()),
        "mean_coverage": float(covered.reindex(errors.index).mean())
        if covered is not None else None,
        "first_date": str(history.loc[errors.index, "target_nav_date"].min()),
        "last_date": str(history.loc[errors.index, "target_nav_date"].max()),
    }


def run_backtest(fund_code: str, save_dir: Path, nav_df: pd.DataFrame | None = None,
                 history_name: str | None = None) -> dict:
    """Reconcile the stored estimates with the official NAV and score them."""
    from fa.analysis.nav_estimator import HISTORY_SUFFIX, load_nav_frame

    save_dir = Path(save_dir)
    path = save_dir / (history_name or f"{fund_code}{HISTORY_SUFFIX}")
    history = read_history(path)
    if history.empty:
        return {"status": "no-history", "fund_code": fund_code, "n": 0,
                "message": "尚未有估算記錄"}

    nav_df = nav_df if nav_df is not None else load_nav_frame(fund_code, save_dir)
    history = backfill_history(history, nav_df)
    write_history(history, path)

    metrics = compute_metrics(history)
    pending = int(len(history) - metrics.get("n", 0))
    if metrics.get("n"):
        log.info(f"[{fund_code}] 回測: n={metrics['n']} "
                 f"MAE={metrics['mae']:.6f} RMSE={metrics['rmse']:.6f}")
    else:
        log.info(f"[{fund_code}] 回測: 尚無可對照的官方淨值（pending={pending}）")
    result = {
        "status": "ok" if metrics.get("n") else "pending",
        "fund_code": fund_code,
        "history": path.name,
        "pending": pending,
        **metrics,
    }
    if not metrics.get("n"):
        result["message"] = ("估算記錄尚未對應到已公佈的官方淨值；"
                             "等目標日淨值公佈後重跑即可得到 MAE/RMSE")
    return result
