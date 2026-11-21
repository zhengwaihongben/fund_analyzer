# -*- coding: utf-8 -*-
"""Application reporting service (V2.4 Stage D, D-04/D-06/D-07/D-08).

Read-only facade over the persisted records:

* ``render_backtest`` / ``render_estimation`` load a stored run through the
  accepted application services and hand it to the pure renderers;
* ``export_*`` write the same text under ``output/reports``;
* ``history_*`` return the summary rows the GUI lists.

Nothing here calls ``run_backtest`` or the estimator, and every repository
is closed in ``finally`` — importing this module must not start a backtest.
"""
from __future__ import annotations

from pathlib import Path

from fa.application import backtesting, estimation
from fa.config import PROJECT_ROOT
from fa.reporting import backtest_report, estimation_report

REPORTS_DIR = PROJECT_ROOT / "output" / "reports"
BACKTEST_SUFFIX = ".md"
ESTIMATION_SUFFIX = "_estimate.md"


def history_estimations(fund_code: str | None = None, limit: int = 50,
                        *, repository_path: str | Path | None = None) -> list[dict]:
    return estimation.list_estimates(
        fund_code, limit, db_path=repository_path)


def history_backtests(spec_id: str | None = None, limit: int = 50,
                      *, repository_path: str | Path | None = None) -> list[dict]:
    """Summary rows from the one new read-only repository method."""
    from fa.data.database.repository import Repository

    path = Path(repository_path) if repository_path is not None else backtesting.DEFAULT_DATABASE
    repository = Repository(path)
    try:
        return repository.list_backtest_runs(spec_id, limit)
    finally:
        repository.close()


def render_backtest(run_id: str, *, repository_path: str | Path | None = None) -> str:
    return backtest_report.render(
        backtesting.load_run(run_id, repository_path=repository_path))


def render_estimation(run_id: str, *, repository_path: str | Path | None = None,
                      save_dir: str | Path | None = None) -> str:
    view = estimation.load_estimate(
        run_id, db_path=repository_path, save_dir=save_dir)
    return estimation_report.render(view)


def export_backtest(run_id: str, *, repository_path: str | Path | None = None,
                    target_dir: str | Path | None = None) -> Path:
    text = render_backtest(run_id, repository_path=repository_path)
    return _write(text, run_id, BACKTEST_SUFFIX, target_dir)


def export_estimation(run_id: str, *, repository_path: str | Path | None = None,
                      save_dir: str | Path | None = None,
                      target_dir: str | Path | None = None) -> Path:
    text = render_estimation(
        run_id, repository_path=repository_path, save_dir=save_dir)
    return _write(text, run_id, ESTIMATION_SUFFIX, target_dir)


def _write(text: str, run_id: str, suffix: str,
           target_dir: str | Path | None) -> Path:
    directory = Path(target_dir) if target_dir is not None else REPORTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{run_id}{suffix}"
    target.write_text(text, encoding="utf-8", newline="\r\n")
    return target


__all__ = [
    "BACKTEST_SUFFIX",
    "ESTIMATION_SUFFIX",
    "REPORTS_DIR",
    "export_backtest",
    "export_estimation",
    "history_backtests",
    "history_estimations",
    "render_backtest",
    "render_estimation",
]
