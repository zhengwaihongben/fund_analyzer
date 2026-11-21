# -*- coding: utf-8 -*-
"""Stage C view tests (C-06…C-13, C-19) and window behaviour.

Every expectation is read from a **real persisted run** produced by
``run_backtest()`` on a temporary database, so a GUI that reformats or
recomputes numbers fails here.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import tkinter as tk

from fa.application import backtesting as service
from fa.backtesting import run_backtest
from fa.backtesting.models import BacktestSpecError
from fa.data.database.repository import Repository
from fa.gui import backtest_window as window_module
from fa.gui.backtest_window import (
    BacktestForm,
    BacktestWindow,
    anomaly_lines,
    cost_source_rows,
    equity_rows,
    equity_series,
    metrics_rows,
    runtime_rows,
    summary_rows,
    trade_rows,
)
from fa.gui.formatting import MISSING, date_text, money, pct
from tests.fixtures.backtesting import build_store, make_spec


class _RunCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="c-view-"))
        self.db_path = self.tmpdir / "fund_analyzer.db"
        self.repository = Repository(self.db_path)
        build_store(self.repository, days=40)
        spec = make_spec(sessions=40)
        self.run, self.created = run_backtest(spec, self.repository)

    def tearDown(self):
        try:
            self.repository.close()
        finally:
            shutil.rmtree(self.tmpdir, ignore_errors=True)


class SummaryTests(_RunCase):
    def test_summary_reflects_the_run_and_created_flag(self):
        rows = dict(summary_rows(self.run, created=True,
                                 request_cost_source=service.COST_SOURCE_DEFAULT))
        self.assertEqual(rows["Run ID"], self.run.run_id)
        self.assertEqual(rows["策略 ID"], "fixture")
        self.assertIn(date_text(self.run.start), rows["期間"])
        self.assertEqual(rows["初始資金（CNY）"], money(1_000_000.0))
        self.assertEqual(rows["資料指紋"], f"{self.run.data_fingerprint[:12]}…")
        self.assertEqual(rows["異常筆數"], str(len(self.run.anomalies)))
        self.assertEqual(rows["執行狀態"], "Completed")
        self.assertIn("Created", rows["重用狀態"])

    def test_reused_run_says_so(self):
        rows = dict(summary_rows(self.run, created=False,
                                 request_cost_source=service.COST_SOURCE_DEFAULT))
        self.assertIn("Existing Run Reused", rows["重用狀態"])

    def test_c11_anomalies_keep_their_order_and_have_an_empty_state(self):
        if self.run.anomalies:
            self.assertEqual(anomaly_lines(self.run),
                             [str(item) for item in self.run.anomalies])
        empty = replace(self.run, anomalies=())
        self.assertEqual(anomaly_lines(empty), ["無異常"])


class RuntimePanelTests(_RunCase):
    def test_c19_runtime_panel_reads_the_stored_runtime_block(self):
        rows = dict(runtime_rows(self.run))
        self.assertEqual(rows["決策延遲（分鐘）"], "30")
        self.assertEqual(rows["執行延遲（交易日）"], "1")
        self.assertEqual(rows["零股（fractional_shares）"], "是")
        self.assertEqual(rows["無風險利率"], pct(0.02))
        self.assertEqual(rows["佣金率"], "0.0005")
        self.assertEqual(rows["最低佣金（CNY）"], money(0.0))

    def test_c19_runtime_panel_has_no_widgets_and_stays_read_only(self):
        rows = runtime_rows(self.run)
        self.assertTrue(all(isinstance(row, tuple) and len(row) == 2 for row in rows))
        self.assertFalse(hasattr(self.run, "set_fractional_shares"))

    def test_c19_created_run_shows_the_request_mode(self):
        rows = dict(cost_source_rows(
            self.run, created=True,
            request_cost_source=service.COST_SOURCE_CUSTOM))
        self.assertIn("本次請求模式", rows["成本來源"])
        self.assertIn("自訂", rows["成本來源"])

    def test_c19_reused_run_reports_the_unknown_source_instead_of_guessing(self):
        rows = dict(cost_source_rows(
            self.run, created=False,
            request_cost_source=service.COST_SOURCE_CUSTOM))
        self.assertEqual(rows["成本來源"], service.UNKNOWN_COST_SOURCE_TEXT)
        self.assertNotIn("自訂", rows["成本來源"])


class MetricsTests(_RunCase):
    def test_c07_metrics_match_the_stored_row(self):
        loaded = self.repository.load_backtest_run(self.run.run_id)
        rows = {label: (value, kind) for label, value, kind in metrics_rows(loaded)}
        self.assertEqual(rows["交易日"][0], loaded.metrics.sessions)
        self.assertEqual(rows["交易筆數"][0], loaded.metrics.trades)
        self.assertEqual(rows["Sharpe"][0], loaded.metrics.sharpe)
        self.assertEqual(rows["總成本（CNY）"][0], loaded.metrics.total_costs_base)
        self.assertAlmostEqual(loaded.metrics.cagr, self.run.metrics.cagr)

    def test_c06_metrics_render_from_the_same_run_the_service_returns(self):
        result = service.run_strategy(
            BacktestForm(spec_id="view-check", start="2025-01-06",
                         end="2025-02-28", initial_capital="1000000",
                         universe=[{"ticker": "LLY", "market": "US",
                                    "currency": "USD"}]).to_spec(),
            self.db_path,
        )
        rendered = {label: value for label, value, _kind in metrics_rows(result.run)}
        stored = self.repository.load_backtest_run(result.run.run_id)
        self.assertEqual(result.run.run_id, stored.run_id)
        self.assertEqual(rendered["交易日"], stored.metrics.sessions)


class TableTests(_RunCase):
    def test_c09_equity_rows_have_seven_columns_including_drawdown(self):
        rows = equity_rows(self.run)
        self.assertEqual(len(rows), len(self.run.equity_curve))
        first = rows[0]
        self.assertEqual(len(first), 7)
        point = self.run.equity_curve[0]
        self.assertEqual(first[0], date_text(point.session))
        self.assertEqual(first[3], money(point.total_base))
        self.assertEqual(first[4], pct(point.exposure, signed=False))
        loaded = self.repository.load_backtest_run(self.run.run_id)
        stored = loaded.equity_curve[0]
        expected = pct(stored.drawdown) if stored.drawdown is not None else MISSING
        self.assertEqual(first[6], expected)
        if stored.drawdown is not None:
            self.assertIn("%", first[6])

    def test_c09_trade_rows_carry_every_cost_leg(self):
        rows = trade_rows(self.run)
        self.assertEqual(len(rows), len(self.run.trades))
        trade = self.run.trades[0]
        row = rows[0]
        self.assertEqual(len(row), 14)
        self.assertEqual(row[1], trade.ticker)
        self.assertEqual(row[2], "買入" if trade.side == "buy" else "賣出")
        self.assertEqual(row[5], trade.price_source)
        self.assertEqual(row[9], money(trade.commission_base))
        self.assertEqual(row[10], money(trade.slippage_base))
        self.assertEqual(row[11], money(trade.fx_spread_base))
        self.assertEqual(row[12], money(trade.net_base))

    def test_c08_equity_chart_series_matches_the_curve(self):
        totals, benchmarks = equity_series(self.run)
        self.assertEqual(len(totals), len(self.run.equity_curve))
        self.assertEqual(len(benchmarks), len(self.run.equity_curve))


class _TkCase(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = BacktestWindow(self.root)
        for key, value in (("start", "2025-01-06"), ("end", "2025-02-28")):
            entry = self.window.entries[key]
            entry.delete(0, "end")
            entry.insert(0, value)

    def tearDown(self):
        if self.window is not None and not self.window._closed:
            self.window.close(timeout=1.0)
        self.root.destroy()


class WindowTests(_TkCase):
    def test_window_exposes_the_form_and_is_idle(self):
        form = self.window.form_values()
        self.assertIsInstance(form, BacktestForm)
        self.assertEqual(self.window.state, "idle")
        self.assertEqual(self.window.status_var.get(), "Idle")

    def test_created_run_renders_every_panel(self):
        result = service.BacktestRunResult(
            run=self._real_run(), created=True,
            request_cost_source=service.COST_SOURCE_DEFAULT)
        with mock.patch.object(window_module, "_run_job",
                               return_value=window_module.RunOutcome(result=result)):
            self.window.submit()
            self.window.worker._thread.join(5.0)
            self.window.worker._poll()
        self.assertEqual(self.window.state, "completed")
        text = self.window.summary_text.get("1.0", "end")
        self.assertIn(result.run.run_id, text)
        self.assertIn("Created", text)
        self.assertTrue(self.window.metrics_tree.get_children())
        self.assertTrue(self.window.equity_tree.get_children())

    def test_error_is_shown_with_its_class_name(self):
        with mock.patch.object(window_module, "_run_job",
                               side_effect=RuntimeError("boom")):
            self.window.submit()
            self.window.worker._thread.join(5.0)
            self.window.worker._poll()
        self.assertIn("Failed", self.window.status_var.get())
        self.assertIn("RuntimeError", self.window.status_var.get())

    def test_second_submit_is_refused_while_running(self):
        with mock.patch.object(window_module, "_run_job",
                               return_value=window_module.RunOutcome(
                                   result=service.BacktestRunResult(
                                       run=self._real_run(), created=True,
                                       request_cost_source="default"))):
            self.window.state = "running"
            self.window.submit()
        self.assertEqual(self.window.state, "running")

    def _real_run(self):
        tmpdir = Path(tempfile.mkdtemp(prefix="c-window-"))
        self.addCleanup(shutil.rmtree, tmpdir, True)
        repository = Repository(tmpdir / "fund_analyzer.db")
        self.addCleanup(repository.close)
        build_store(repository, days=30)
        run, _created = run_backtest(make_spec(sessions=30), repository)
        return run


class WindowValidationTests(_TkCase):
    def test_weighting_switch_enables_the_right_fields(self):
        self.assertEqual(str(self.window.entries["target_weights"].cget("state")),
                         "disabled")
        self.window.vars["weighting"].set("fixed")
        self.window.update_idletasks()
        self.assertEqual(str(self.window.entries["target_weights"].cget("state")),
                         "normal")
        self.window.vars["weighting"].set("momentum")
        self.window.update_idletasks()
        self.assertEqual(str(self.window.entries["momentum_window"].cget("state")),
                         "normal")
        self.assertEqual(str(self.window.entries["target_weights"].cget("state")),
                         "disabled")

    def test_cost_mode_switch_enables_the_cost_entries(self):
        self.assertEqual(str(self.window.entries["commission_rate"].cget("state")),
                         "disabled")
        self.window.vars["cost_mode"].set("custom")
        self.window.update_idletasks()
        self.assertEqual(str(self.window.entries["commission_rate"].cget("state")),
                         "normal")

    def test_invalid_spec_creates_no_run(self):
        self.window.entries["start"].delete(0, "end")
        self.window.entries["start"].insert(0, "not-a-date")
        self.window.submit()
        self.assertEqual(self.window.state, "idle")
        self.assertIn("設定錯誤", self.window.status_var.get())
        self.assertIsNone(self.window.worker._thread)

    def test_c12_all_four_error_classes_are_shown(self):
        from fa.backtesting.data_access import (BacktestDataIntegrityError,
                                                BacktestDataMissingError)
        from fa.data.database.connection import DatabaseError
        for error_type in (BacktestSpecError, BacktestDataMissingError,
                           BacktestDataIntegrityError, DatabaseError):
            with self.subTest(error=error_type.__name__):
                with mock.patch.object(window_module, "_run_job",
                                       side_effect=error_type("boom")):
                    self.window.submit()
                    self.window.worker._thread.join(5.0)
                    self.window.worker._poll()
                self.assertIn(error_type.__name__,
                              self.window.status_var.get())

if __name__ == "__main__":
    unittest.main()
