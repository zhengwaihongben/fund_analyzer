# -*- coding: utf-8 -*-
"""Stage A: notebook areas, window registry and close-out lifecycle (A-01…A-05, A-07, A-09)."""
from __future__ import annotations

import gc
import shutil
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from fa.gui.navigation import AREA_TITLES, WindowRegistry


# Plain-data blockers for the two legacy windows.  Keyed by fund code so the
# callable itself stays module level and captures no window.
_RELEASES: dict[str, threading.Event] = {}


def _qdii_blocker(fund_code, save_dir, result_queue):
    _RELEASES.setdefault(fund_code, threading.Event()).wait(5.0)
    result_queue.put(("ok", {}))


def _bridge_blocker(reporter, fund_code, save_dir):
    """WorkerBridge work item that blocks without touching any GUI object."""
    _RELEASES.setdefault(fund_code, threading.Event()).wait(5.0)
    return {"fund_code": fund_code}


def _post_market_blocker(fund_code, save_dir, result_queue):
    _RELEASES.setdefault(fund_code, threading.Event()).wait(5.0)
    result_queue.put(("ok", {}))


def _tk_available() -> bool:
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


TK_AVAILABLE = _tk_available()


class FakeWindow:
    def __init__(self, parent=None, **kwargs):
        self.parent = parent
        self.options = kwargs
        self.lifted = 0
        self.closed = 0
        self.exists = True

    def winfo_exists(self):
        return self.exists

    def lift(self):
        self.lifted += 1

    def focus_force(self):
        pass

    def close(self):
        self.closed += 1
        self.exists = False


class WindowRegistryTests(unittest.TestCase):
    def test_open_creates_once_and_reuses(self):
        registry = WindowRegistry(master=None)
        registry.register("demo", FakeWindow)
        first = registry.open("demo", value=1)
        second = registry.open("demo")
        self.assertIs(first, second)
        self.assertGreaterEqual(first.lifted, 1)

    def test_closed_window_is_recreated(self):
        registry = WindowRegistry(master=None)
        registry.register("demo", FakeWindow)
        first = registry.open("demo")
        first.exists = False
        second = registry.open("demo")
        self.assertIsNot(first, second)

    def test_close_all_closes_every_window(self):
        registry = WindowRegistry(master=None)
        registry.register("a", FakeWindow)
        registry.register("b", FakeWindow)
        first, second = registry.open("a"), registry.open("b")
        registry.close_all()
        self.assertEqual((first.closed, second.closed), (1, 1))
        with self.assertRaises(RuntimeError):
            registry.open("a")

    def test_unregistered_key_is_rejected(self):
        registry = WindowRegistry(master=None)
        with self.assertRaises(KeyError):
            registry.open("missing")


@unittest.skipUnless(TK_AVAILABLE, "需要 Tk 顯示環境")
class MainWindowStageATests(unittest.TestCase):
    def setUp(self):
        from fa.gui.app import FundAnalyzerGUI

        # Collect dead Tk objects on the main thread before a new interpreter
        # starts: a later collection inside a worker thread would delete Tcl
        # async handlers from the wrong thread (Tcl_AsyncDelete).
        gc.collect()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = FundAnalyzerGUI(self.root)

    def tearDown(self):
        if not self.app._closed:
            self.app.shutdown()
        gc.collect()

    # -- A-01 -----------------------------------------------------------
    def test_main_window_opens_and_closes(self):
        self.assertEqual(self.root.title(), "Fund Analyzer — 基金分析工具")
        self.assertEqual(self.root.minsize(), (720, 560))

    # -- A-02 -----------------------------------------------------------
    def test_notebook_has_four_areas(self):
        self.assertEqual(self.app.notebook.area_keys(),
                         ["analysis", "estimation", "backtest", "reports"])
        titles = [self.app.notebook.tab(tab, "text")
                  for tab in self.app.notebook.tabs()]
        self.assertEqual(titles, list(AREA_TITLES.values()))

    # -- A-03 -----------------------------------------------------------
    def test_tabs_switch(self):
        for key in ("analysis", "estimation", "backtest", "reports", "analysis"):
            self.app.notebook.select(self.app.notebook.frame_for(key))
            self.root.update_idletasks()
            self.assertEqual(self.app.notebook.index("current"),
                             self.app.notebook.index(self.app.notebook.frame_for(key)))

    # -- A-09 -----------------------------------------------------------
    def test_analysis_tab_intact(self):
        for name in ("code_var", "rf_var", "bench_var", "output_var",
                     "start_date_var", "bench_market_var", "ignore_cache_var"):
            self.assertTrue(hasattr(self.app, name), name)
        self.assertEqual(str(self.app.run_btn.cget("state")), "normal")
        self.assertIsNotNone(self.app.result_listbox)
        self.assertIsNotNone(self.app.log_text)
        self.assertEqual(self.app.code_var.get(), "")
        self.assertEqual(self.app.rf_var.get(), "0.02")

    # -- A-04 -----------------------------------------------------------
    def test_close_cancels_timers(self):
        self.app.shutdown()
        self.assertTrue(self.app._closed)
        self.assertIsNone(self.app._log_poll_id)
        self.assertIsNone(self.app._result_poll_id)
        with self.assertRaises(tk.TclError):
            self.root.winfo_exists()
        # Destroyed windows must not reschedule polling.
        self.app._poll_log_queue()
        self.app._poll_result_queue()

    # -- A-05 -----------------------------------------------------------
    def test_shutdown_closes_registered_child_windows(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        payload = {"status": "ok", "estimate_return": 0.0,
                   "yesterday_official_return": None, "nq_futures_return": None,
                   "coverage_ratio": None, "covered_weighted_return": None,
                   "holdings": [], "message": ""}
        with mock.patch("fa.gui.post_market.fetch_post_market_estimate",
                        return_value=payload):
            self.app.code_var.set("017436")
            self.app._output_dir = tmp
            self.app.open_post_market_window()
            window = self.app._post_market_window
            self.assertIsNotNone(window)
            self.app.open_post_market_window()
            self.assertIs(self.app._post_market_window, window)
            self.app.shutdown()
        self.assertTrue(window._closed)
        self.assertIsNone(self.app._post_market_window)


@unittest.skipUnless(TK_AVAILABLE, "需要 Tk 顯示環境")
class PostMarketSchedulerTests(unittest.TestCase):
    """A-07: the 60 s refresh and 1 s poll are scheduled once and cancelled."""

    def setUp(self):
        from fa.gui.post_market import PostMarketWindow

        gc.collect()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = tk.Tk()
        self.root.withdraw()
        self.scheduled = []
        self.cancelled = []
        self.counter = 0

        def fake_after(_self, ms, func=None, *args):
            self.counter += 1
            self.scheduled.append((ms, func))
            return f"fake-{self.counter}"

        self._patchers = [
            mock.patch.object(PostMarketWindow, "after", fake_after, create=True),
            mock.patch.object(PostMarketWindow, "after_cancel",
                             lambda _self, identifier: self.cancelled.append(identifier),
                             create=True),
            mock.patch("fa.gui.post_market.fetch_post_market_estimate",
                       return_value={"status": "ok", "holdings": [], "message": ""}),
        ]
        for patcher in self._patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = PostMarketWindow(self.root, "017436", self.tmp)

    def tearDown(self):
        try:
            self.window.close()
        finally:
            try:
                self.root.destroy()
            except tk.TclError:
                pass
            gc.collect()

    def _poll_once(self):
        """Run one main-thread drain so the 60 s refresh timer is scheduled."""
        thread = getattr(self.window, "_fetch_thread", None)
        if thread is not None:
            thread.join(2.0)
        self.window._poll_result_queue()

    def test_post_market_poll_is_scheduled_and_cancelled(self):
        self._poll_once()
        delays = [delay for delay, _ in self.scheduled]
        self.assertIn(60000, delays)
        self.assertIn(200, delays)
        self.assertIsNotNone(self.window._refresh_id)
        self.window.close()
        self.assertTrue(self.cancelled)
        self.assertIsNone(self.window._refresh_id)
        self.assertIsNone(self.window._poll_id)

    def test_close_cancels_both_timers(self):
        self._poll_once()
        self.assertIsNotNone(self.window._refresh_id)
        self.window.close()
        self.assertTrue(self.cancelled)
        self.assertIsNone(self.window._refresh_id)
        self.assertIsNone(self.window._poll_id)

    def test_no_new_schedule_after_close(self):
        self._poll_once()
        before = len(self.scheduled)
        self.window.close()
        self.window.refresh_data()
        self.assertEqual(len(self.scheduled), before)


class _BudgetWindow:
    """Child window whose close records the wait budget it was given."""

    def __init__(self):
        self.received = []
        self.exists = True

    def winfo_exists(self):
        return self.exists

    def close(self, *, timeout=2.0):
        self.received.append(timeout)
        time.sleep(0.25)
        self.exists = False


class RegistryBudgetTests(unittest.TestCase):
    """A-06/T-7: close_all shares one wait budget across every child."""

    def test_registry_close_all_uses_shared_deadline(self):
        registry = WindowRegistry(master=None)
        first, second = _BudgetWindow(), _BudgetWindow()
        registry.register("a", lambda parent=None: first)
        registry.register("b", lambda parent=None: second)
        registry.open("a")
        registry.open("b")
        registry.close_all(timeout=2.0)
        self.assertEqual(len(first.received), 1)
        self.assertEqual(len(second.received), 1)
        self.assertLessEqual(first.received[0], 2.0)
        self.assertLess(second.received[0], first.received[0])
        self.assertLessEqual(second.received[0], 2.0)

    def test_registry_clamps_an_oversized_timeout(self):
        registry = WindowRegistry(master=None)
        window = _BudgetWindow()
        registry.register("a", lambda parent=None: window)
        registry.open("a")
        registry.close_all(timeout=30.0)
        self.assertLessEqual(window.received[0], 2.0)


@unittest.skipUnless(TK_AVAILABLE, "需要 Tk 顯示環境")
class LegacyWorkerIsolationTests(unittest.TestCase):
    """A-06: the QDII and post-market threads hold no window reference."""

    def setUp(self):
        gc.collect()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = tk.Tk()
        self.root.withdraw()
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        for event in _RELEASES.values():
            event.set()
        _RELEASES.clear()
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        gc.collect()

    def _assert_isolated(self, thread, expected_callable, forbidden, expected_args):
        self.assertIsNotNone(thread)
        self.assertIs(thread._target, expected_callable)
        self.assertIsNone(getattr(thread._target, "__self__", None))
        self.assertIsNone(getattr(thread._target, "__closure__", None))
        self.assertEqual(thread._args, expected_args)
        for argument in thread._args:
            self.assertIsNot(argument, forbidden)
            self.assertNotIsInstance(argument, tk.Misc)
            self.assertNotIsInstance(argument, tk.Toplevel)

    def test_qdii_worker_does_not_retain_window(self):
        from fa.gui import qdii_estimate, worker as worker_module
        from fa.gui.qdii_estimate import QdiiEstimateWindow

        with mock.patch.object(qdii_estimate, "_live_estimate_job",
                               _bridge_blocker):
            window = QdiiEstimateWindow(self.root, "017436", self.tmp)
            thread = window.worker.thread
            self.assertIsNotNone(thread)
            self.assertIs(thread._target, worker_module._execute_worker)
            self.assertIsNone(getattr(thread._target, "__self__", None))
            self.assertIsNone(getattr(thread._target, "__closure__", None))
            work, args, kwargs, queue, closed_event, reporter = thread._args
            self.assertIs(work, _bridge_blocker)
            self.assertIsNone(getattr(work, "__self__", None))
            self.assertIsNone(getattr(work, "__closure__", None))
            self.assertEqual(args, ("017436", self.tmp))
            self.assertEqual(kwargs, {})
            self.assertIsNot(args[0], window)
            self.assertEqual(
                worker_module.ProgressReporter.__slots__,
                ("_queue", "_closed_event"),
            )
            self.assertFalse(hasattr(reporter, "_widget"))
            self.assertFalse(hasattr(reporter, "_bridge"))
            self.assertFalse(hasattr(reporter, "__dict__"))
            for value in (work, args, kwargs, queue, closed_event, reporter):
                self.assertIsNot(value, window)
                self.assertNotIsInstance(value, tk.Misc)
                self.assertNotIsInstance(value, tk.Toplevel)
        window.close()

    def test_post_market_worker_does_not_retain_window(self):
        from fa.gui import post_market
        from fa.gui.post_market import PostMarketWindow

        _RELEASES.setdefault("017436", self.release)
        with mock.patch.object(post_market, "_fetch_post_market_task",
                               _post_market_blocker):
            window = PostMarketWindow(self.root, "017436", self.tmp)
            self._assert_isolated(
                window._fetch_thread,
                _post_market_blocker,
                window,
                ("017436", self.tmp, window.result_queue),
            )
        window.close()


@unittest.skipUnless(TK_AVAILABLE, "需要 Tk 顯示環境")
class EstimationTabWiringTests(unittest.TestCase):
    """B-3 review D5/F1/F2: the app wiring, not only the window class."""

    def setUp(self):
        from fa.gui.app import FundAnalyzerGUI

        gc.collect()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = FundAnalyzerGUI(self.root)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def tearDown(self):
        if not self.app._closed:
            self.app.shutdown()
        gc.collect()

    def test_live_entry_passes_no_estimate_and_the_history_refresh(self):
        self.app.code_var.set("000369")
        self.app._output_dir = self.tmp
        fake = mock.Mock()
        with mock.patch.object(self.app.windows, "open", return_value=fake) as opener:
            self.app.open_qdii_window()
        kwargs = opener.call_args.kwargs
        self.assertIsNone(kwargs["estimate"])
        self.assertEqual(kwargs["mode"], "live")
        self.assertEqual(kwargs["on_saved"], self.app.refresh_estimation_history)
        self.assertIs(self.app._qdii_window, fake)

    def test_handover_entry_uses_the_analysis_record(self):
        record = {"fund_code": "000369", "estimated_nav": 2.82, "holdings": []}
        self.app.code_var.set("000369")
        self.app._output_dir = self.tmp
        self.app._qdii_estimate = record
        fake = mock.Mock()
        with mock.patch.object(self.app.windows, "open", return_value=fake) as opener:
            self.app.open_qdii_handover()
        kwargs = opener.call_args.kwargs
        self.assertIs(kwargs["estimate"], record)
        self.assertEqual(kwargs["mode"], "handover")

    def test_existing_window_is_switched_back_to_live(self):
        existing = mock.Mock()
        existing.start_live = mock.Mock()
        self.app.code_var.set("000369")
        self.app._output_dir = self.tmp
        with mock.patch.object(self.app.windows, "get", return_value=existing), \
                mock.patch.object(self.app.windows, "open") as opener:
            self.app.open_qdii_window()
        existing.start_live.assert_called_once()
        existing.lift.assert_called_once()
        opener.assert_not_called()

    def test_the_qdii_button_opens_the_handover_view(self):
        record = {"fund_code": "000369", "estimated_nav": 2.82, "holdings": []}
        self.app.code_var.set("000369")
        self.app._output_dir = self.tmp
        self.app._qdii_estimate = record
        self.assertIn("open_qdii_handover", str(self.app.qdii_btn.cget("command")))
        self.app.qdii_btn.configure(state="normal")
        fake = mock.Mock()
        with mock.patch.object(self.app.windows, "open", return_value=fake) as opener:
            self.app.qdii_btn.invoke()
        kwargs = opener.call_args.kwargs
        self.assertEqual(kwargs["mode"], "handover")
        self.assertIs(kwargs["estimate"], record)

    def test_open_handover_switches_an_existing_window(self):
        record = {"fund_code": "000369", "estimated_nav": 2.82, "holdings": []}
        self.app.code_var.set("000369")
        self.app._output_dir = self.tmp
        self.app._qdii_estimate = record
        existing = mock.Mock()
        existing.show_handover = mock.Mock()
        with mock.patch.object(self.app.windows, "get", return_value=existing), \
                mock.patch.object(self.app.windows, "open") as opener:
            self.app.open_qdii_handover()
        existing.show_handover.assert_called_once_with(record)
        existing.lift.assert_called_once()
        opener.assert_not_called()

    def test_estimation_tab_loads_history_once_the_result_arrives(self):
        with mock.patch.object(self.app.history_worker, "submit") as submit:
            self.app.notebook.select(self.app.notebook.frame_for("estimation"))
            self.root.update_idletasks()
            self.app.notebook.event_generate("<<NotebookTabChanged>>")
            self.root.update()
            self.assertGreaterEqual(submit.call_count, 1)
            self.app._on_history_result(True, [])
            submit.reset_mock()
            for key in ("analysis", "estimation", "analysis", "estimation"):
                self.app.notebook.select(self.app.notebook.frame_for(key))
                self.root.update_idletasks()
                self.app.notebook.event_generate("<<NotebookTabChanged>>")
                self.root.update()
            self.assertEqual(submit.call_count, 0)


if __name__ == "__main__":
    unittest.main()

class BacktestCloseBudgetTests(unittest.TestCase):
    """C-15: closing the backtest window during a run waits at most 2 s."""

    def test_gui_close_during_run_bounded_wait(self):
        import time
        from fa.gui import backtest_window as backtest_module
        from fa.gui.backtest_window import BacktestWindow

        root = tk.Tk()
        root.withdraw()
        window = BacktestWindow(root)
        started = threading.Event()

        def slow(reporter, values, repository_path):
            started.set()
            threading.Event().wait(3.0)
            return None

        with mock.patch.object(backtest_module, "_run_job", slow):
            window.submit()
            started.wait(2.0)
            begin = time.monotonic()
            window.close(timeout=2.0)
            elapsed = time.monotonic() - begin
        self.assertLess(elapsed, 2.6)
        self.assertFalse(window.winfo_exists())
        root.destroy()
