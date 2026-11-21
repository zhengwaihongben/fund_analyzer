# -*- coding: utf-8 -*-
"""Stage D — history queries (D-11), renderer move (D-01/D-02) and GUI (D-03/D-07)."""
from __future__ import annotations

import gc
import time
import tkinter as tk
import unittest
from unittest import mock
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from fa.application import reporting
from fa.backtesting import run_backtest
from fa.data.database.repository import DatabaseError, Repository
from fa.models.estimation import EstimationResult, EstimationRun
from fa.gui import history_window as history_mod
from fa.gui.history_window import (HistoryWindow, backtest_history_rows,
                                   estimation_history_rows)
from tests.fixtures.backtesting import NOW, build_store, make_spec


def _slow_job(reporter, spec_id, repository_path):
    """Module-level blocking job: the close budget must not wait for it."""
    import time as _time
    _time.sleep(5.0)
    return []


def _slow_estimation_job(reporter, fund_code, repository_path):
    """Same, for the estimation pane."""
    import time as _time
    _time.sleep(5.0)
    return []


class HistoryQueryTests(unittest.TestCase):
    """D-11: the only new repository method is a read-only summary query."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "history.db")
        self.addCleanup(self.repository.close)
        self.store = build_store(self.repository, days=40)
        self.spec = make_spec(self.store, sessions=40)
        self.first, _ = run_backtest(self.spec, self.repository, now=NOW)
        self.second, _ = run_backtest(
            self.spec, self.repository, now=NOW, force=True)

    def test_d11_columns_match_the_stored_run(self):
        rows = self.repository.list_backtest_runs()
        self.assertEqual(len(rows), 2)
        row = next(item for item in rows if item["run_id"] == self.first.run_id)
        self.assertEqual(
            set(row),
            {"run_id", "spec_id", "model_version", "start", "end",
             "base_currency", "created_at", "look_ahead_status",
             "data_fingerprint", "sessions", "trades", "cagr",
             "max_drawdown", "total_costs_base"},
        )
        loaded = self.repository.load_backtest_run(self.first.run_id)
        self.assertEqual(row["spec_id"], loaded.spec_id)
        self.assertEqual(row["model_version"], loaded.model_version)
        self.assertEqual(row["start"], loaded.start)
        self.assertEqual(row["end"], loaded.end)
        self.assertEqual(row["base_currency"], loaded.base_currency)
        self.assertEqual(row["created_at"], loaded.created_at)
        self.assertEqual(row["look_ahead_status"], loaded.look_ahead_status)
        self.assertEqual(row["data_fingerprint"], loaded.data_fingerprint)
        self.assertEqual(row["sessions"], loaded.metrics.sessions)
        self.assertEqual(row["trades"], loaded.metrics.trades)
        self.assertEqual(row["cagr"], loaded.metrics.cagr)
        self.assertEqual(row["max_drawdown"], loaded.metrics.max_drawdown)
        self.assertEqual(
            row["total_costs_base"], loaded.metrics.total_costs_base)

    def test_d11_newest_run_sorts_first(self):
        rows = self.repository.list_backtest_runs()
        stamps = [item["created_at"] for item in rows]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        self.assertEqual({item["run_id"] for item in rows},
                         {self.first.run_id, self.second.run_id})

    def test_d11_limit_is_clamped_and_bounded(self):
        seen: list[str] = []
        self.repository._conn.set_trace_callback(seen.append)
        try:
            self.assertEqual(len(self.repository.list_backtest_runs(limit=0)), 1)
            self.assertEqual(len(self.repository.list_backtest_runs(limit=1)), 1)
            self.assertEqual(len(self.repository.list_backtest_runs(limit=9999)), 2)
        finally:
            self.repository._conn.set_trace_callback(None)
        limits = [line for line in seen if "LIMIT" in line]
        self.assertEqual(len(limits), 3)
        self.assertTrue(limits[0].rstrip().endswith("LIMIT 1"), limits[0])
        self.assertTrue(limits[1].rstrip().endswith("LIMIT 1"), limits[1])
        self.assertTrue(limits[2].rstrip().endswith("LIMIT 200"), limits[2])
        with self.assertRaises(DatabaseError):
            self.repository.list_backtest_runs(limit="many")

    def test_d11_spec_filter(self):
        ours = self.repository.list_backtest_runs(spec_id=self.spec.spec_id)
        self.assertEqual(len(ours), 2)
        self.assertEqual(self.repository.list_backtest_runs(spec_id="other"), [])
        self.assertEqual(self.repository.list_backtest_runs(spec_id="  "), ours)

    def test_d11_query_does_not_write(self):
        before = self.repository.count_backtest_runs()
        self.repository.list_backtest_runs()
        self.assertEqual(self.repository.count_backtest_runs(), before)


class HistoryRowMappingTests(unittest.TestCase):
    """D-06 display mapping: order preserved, missing values dash."""

    def test_estimation_rows_keep_service_order(self):
        rows = estimation_history_rows([
            {"run_id": "b", "fund_code": "000369",
             "target_nav_date": "2026-11-16", "status": "part",
             "estimated_nav": 2.8, "coverage_ratio": 0.461,
             "data_quality": "low", "created_at": "2026-11-16T13:00:00"},
            {"run_id": "a", "fund_code": "000369",
             "target_nav_date": "2026-11-15", "status": "ok",
             "estimated_nav": None, "coverage_ratio": None,
             "data_quality": "high", "created_at": "2026-11-15T13:00:00"},
        ])
        self.assertEqual([row[0] for row in rows], ["b", "a"])
        self.assertEqual(rows[1][4], "—")
        self.assertEqual(rows[1][5], "—")
        self.assertEqual(rows[0][5], "46.1%")

    def test_backtest_rows_period_and_numbers(self):
        rows = backtest_history_rows([{
            "run_id": "r1", "spec_id": "s1", "start": date(2025, 1, 6),
            "end": date(2025, 3, 31), "sessions": 60, "trades": 4,
            "cagr": 0.12, "max_drawdown": -0.08,
            "created_at": "2026-11-16T13:00:00",
        }])
        self.assertEqual(rows[0][2], "2025-01-06 … 2025-03-31")
        self.assertEqual(rows[0][3], "60")
        self.assertEqual(rows[0][5], "+12.00%")
        self.assertEqual(rows[0][6], "-8.00%")


class HistoryWindowTests(unittest.TestCase):
    """D-03/D-07 on the real window, a real database and the real worker."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "gui.db"
        self.root = tk.Tk()
        self.root.withdraw()
        repository = Repository(self.db)
        try:
            store = build_store(repository, days=40)
            spec = make_spec(store, sessions=40)
            self.run, _ = run_backtest(spec, repository, now=NOW)
            self.estimation_run = EstimationRun(
                fund_code='000369', target_nav_date=date(2026, 11, 16),
                trade_date=date(2026, 11, 16), base_nav_date=date(2026, 11, 13),
                holdings_quarter='2026Q3', holdings_date=date(2026, 9, 30),
                disclosure_date=date(2026, 10, 26), holdings_source='eastmoney-page',
                run_id='est-run-1')
            repository.save_estimation(
                self.estimation_run,
                {'holdings': {'quarter': '2026Q3', 'scope': 'top10',
                              'disclosure_date': '2026-10-26',
                              'disclosure_date_estimated': True,
                              'source': 'eastmoney-page'},
                 'assets': [{'ticker': 'LLY', 'used_price_type': 'close+adj_close'}]},
                EstimationResult(run_id='est-run-1', status='partial',
                                 base_nav=2.815, estimated_nav=2.8205,
                                 estimated_return=0.00195, coverage_ratio=0.461,
                                 uncovered_weight=0.539, data_quality='low',
                                 warnings=['未覆蓋權重 53.9% 未計入估算']))
        finally:
            repository.close()

    def tearDown(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        gc.collect()

    def pump(self, predicate, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def _window(self):
        window = HistoryWindow(
            self.root, repository_path=str(self.db),
            save_dir=str(Path(self._tmp.name) / "out"))
        self.addCleanup(window.close)
        return window

    def test_d03_window_lists_and_renders_the_stored_run(self):
        window = self._window()
        pane = window.backtest_pane
        self.assertTrue(self.pump(
            lambda: len(pane.tree.get_children()) == 1))
        pane.tree.selection_set("0")
        self.assertTrue(self.pump(
            lambda: pane.text.get("1.0", "end").strip() != ""))
        shown = pane.text.get("1.0", "end").replace("\r\n", "\n").strip()
        expected = reporting.render_backtest(
            self.run.run_id, repository_path=self.db).strip()
        self.assertEqual(shown, expected)

    def test_close_while_a_job_runs_is_bounded(self):
        with mock.patch.object(history_mod, "_backtests_job", _slow_job):
            window = HistoryWindow(
                self.root, repository_path=str(self.db),
                save_dir=str(Path(self._tmp.name) / "out"))
            self.assertTrue(self.pump(
                lambda: window.backtest_pane.bridge.is_running))
            started = time.monotonic()
            window.close()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 2.5)
            self.assertTrue(window.closed)

    def test_close_with_two_running_panes_is_bounded(self):
        """F1: both panes load on open, so closing must not cost 2 × 2 s."""
        with mock.patch.object(history_mod, "_estimations_job", _slow_estimation_job), \
                mock.patch.object(history_mod, "_backtests_job", _slow_job):
            window = HistoryWindow(
                self.root, repository_path=str(self.db),
                save_dir=str(Path(self._tmp.name) / "out"))
            self.assertTrue(self.pump(
                lambda: window.estimation_pane.bridge.is_running
                and window.backtest_pane.bridge.is_running))
            started = time.monotonic()
            window.close()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 2.2)
            self.assertTrue(window.closed)

    def test_d05_estimation_pane_renders_the_stored_run(self):
        window = self._window()
        pane = window.estimation_pane
        self.assertTrue(self.pump(
            lambda: len(pane.tree.get_children()) == 1))
        pane.tree.selection_set("0")
        self.assertTrue(self.pump(
            lambda: pane.text.get("1.0", "end").strip() != ""))
        shown = pane.text.get("1.0", "end").replace("\r\n", "\n").strip()
        expected = reporting.render_estimation(
            "est-run-1", repository_path=self.db,
            save_dir=Path(self._tmp.name) / "out").strip()
        self.assertEqual(shown, expected)
        self.assertIn("2026Q3", shown)

    def test_d08_estimation_pane_export_matches_the_renderer(self):
        window = self._window()
        pane = window.estimation_pane
        self.assertTrue(self.pump(
            lambda: len(pane.tree.get_children()) == 1))
        pane.tree.selection_set("0")
        self.assertTrue(self.pump(
            lambda: str(pane.export_button["state"]) == "normal"))
        pane.export()
        self.assertTrue(self.pump(lambda: pane.path_var.get() != ""))
        written = Path(pane.path_var.get()).read_text(encoding="utf-8")
        self.assertEqual(
            written.replace("\r\n", "\n").strip(),
            reporting.render_estimation(
                "est-run-1", repository_path=self.db,
                save_dir=Path(self._tmp.name) / "out").strip())

    def test_d07_export_button_writes_the_same_text(self):
        window = self._window()
        pane = window.backtest_pane
        self.assertTrue(self.pump(
            lambda: len(pane.tree.get_children()) == 1))
        pane.tree.selection_set("0")
        self.assertTrue(self.pump(
            lambda: str(pane.export_button["state"]) == "normal"))
        pane.export()
        self.assertTrue(self.pump(lambda: pane.path_var.get() != ""))
        written = Path(pane.path_var.get())
        self.assertTrue(written.exists())
        self.assertEqual(
            written.read_text(encoding="utf-8").replace("\r\n", "\n").strip(),
            reporting.render_backtest(
                self.run.run_id, repository_path=self.db).strip())


if __name__ == "__main__":
    unittest.main()
