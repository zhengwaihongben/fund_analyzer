# -*- coding: utf-8 -*-
"""Stage D — application reporting service (D-04, D-06, D-07, D-08, D-09)."""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from fa.application import reporting
from fa.application import backtesting as backtest_service
from fa.backtesting import run_backtest
from fa.data.database.repository import Repository
from fa.models.estimation import EstimationResult, EstimationRun
from fa.reporting import backtest_report, estimation_report
from tests.fixtures.backtesting import NOW, build_store, make_spec

RUN_DAY = date(2026, 11, 16)
INPUTS = {
    "holdings": {
        "quarter": "2026Q3", "scope": "top10",
        "disclosure_date": "2026-10-26", "disclosure_date_estimated": True,
        "source": "eastmoney-page",
    },
    "assets": [
        {"ticker": "LLY", "name": "禮來", "asset_type": "equity",
         "market": "US", "currency": "USD", "weight": 0.5,
         "base_price_date": "2026-11-13", "target_price_date": "2026-11-16",
         "local_return": 0.01, "fx_return": 0.0, "cny_return": 0.01,
         "used_price_type": "close+adj_close", "status": "ok"},
    ],
}


def _table_fingerprint(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        digest = hashlib.sha256()
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        for table in tables:
            digest.update(table.encode("utf-8"))
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1"):
                digest.update(repr(row).encode("utf-8"))
        return digest.hexdigest()
    finally:
        connection.close()


class ReportingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = self.root / "reporting.db"
        self.reports = self.root / "output" / "reports"
        self.save_dir = self.root / "output" / "000369"
        self.save_dir.mkdir(parents=True)

        repository = Repository(self.db)
        try:
            self.store = build_store(repository, days=40)
            self.spec = make_spec(self.store, sessions=40)
            self.run, _ = run_backtest(self.spec, repository, now=NOW)
        finally:
            repository.close()

        repository = Repository(self.db)
        try:
            run = EstimationRun(fund_code="000369", target_nav_date=RUN_DAY,
                                trade_date=RUN_DAY, run_id="est-run-1")
            result = EstimationResult(
                run_id="est-run-1", base_nav=2.8, estimated_nav=2.82,
                estimated_return=0.005, coverage_ratio=0.9,
                uncovered_weight=0.1, data_quality="high",
                warnings=["w1"], message="")
            repository.save_estimation(run, dict(INPUTS), result)
        finally:
            repository.close()

        patcher = mock.patch.object(reporting, "REPORTS_DIR", self.reports)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_d07_export_backtest_matches_the_renderer(self):
        target = reporting.export_backtest(
            self.run.run_id, repository_path=self.db)
        self.assertEqual(target, self.reports / f"{self.run.run_id}.md")
        stored = Repository(self.db)
        try:
            expected = backtest_report.render(stored.load_backtest_run(self.run.run_id))
        finally:
            stored.close()
        text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertEqual(text, expected)

    def test_d08_export_estimation_matches_the_renderer(self):
        target = reporting.export_estimation(
            "est-run-1", repository_path=self.db, save_dir=self.save_dir)
        self.assertEqual(target, self.reports / "est-run-1_estimate.md")
        text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertEqual(text, reporting.render_estimation(
            "est-run-1", repository_path=self.db, save_dir=self.save_dir))
        self.assertIn("## NAV", text)

    def test_d04_rendering_never_runs_a_backtest_or_the_estimator(self):
        with mock.patch.object(backtest_service, "run_backtest",
                               side_effect=AssertionError("run_backtest called")), \
                mock.patch("fa.analysis.nav_estimator.estimate_qdii_nav",
                           side_effect=AssertionError("estimator called")):
            self.assertIn("# Backtest report", reporting.render_backtest(
                self.run.run_id, repository_path=self.db))
            self.assertIn("# Estimation report", reporting.render_estimation(
                "est-run-1", repository_path=self.db, save_dir=self.save_dir))

    def test_d06_history_lists_and_filters(self):
        rows = reporting.history_backtests(repository_path=self.db)
        self.assertEqual([row["run_id"] for row in rows], [self.run.run_id])
        self.assertEqual(
            reporting.history_backtests(spec_id="other", repository_path=self.db), [])
        estimations = reporting.history_estimations(repository_path=self.db)
        self.assertEqual([row["run_id"] for row in estimations], ["est-run-1"])
        self.assertEqual(reporting.history_estimations(
            "999999", repository_path=self.db), [])

    def test_d09_browsing_and_export_do_not_change_rows(self):
        before = _table_fingerprint(self.db)
        reporting.history_backtests(repository_path=self.db)
        reporting.history_estimations(repository_path=self.db)
        reporting.render_backtest(self.run.run_id, repository_path=self.db)
        reporting.render_estimation(
            "est-run-1", repository_path=self.db, save_dir=self.save_dir)
        reporting.export_backtest(self.run.run_id, repository_path=self.db)
        reporting.export_estimation(
            "est-run-1", repository_path=self.db, save_dir=self.save_dir)
        self.assertEqual(_table_fingerprint(self.db), before)

    def test_unknown_run_raises_without_writing(self):
        before = _table_fingerprint(self.db)
        with self.assertRaises(LookupError):
            reporting.render_backtest("missing", repository_path=self.db)
        self.assertEqual(_table_fingerprint(self.db), before)


if __name__ == "__main__":
    unittest.main()
