# -*- coding: utf-8 -*-
"""GUI 生命週期測試：錯誤處理、隊列輪詢定時器、監控窗口的關閉流程。

需要可用的 Tk 顯示環境；沒有時自動跳過。
"""
import logging
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tkinter as tk

from fa.gui import crash
from fa.gui.app import QueueHandler


def _tk_available() -> bool:
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


TK_AVAILABLE = _tk_available()


class QueueHandlerTests(unittest.TestCase):
    def test_emit_formats_record_into_queue(self):
        import queue
        q = queue.Queue()
        handler = QueueHandler(q)
        handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
        record = logging.LogRecord("fa.test", logging.WARNING, __file__, 1,
                                   "hello", None, None)
        handler.emit(record)
        self.assertEqual(q.get_nowait(), "WARNING:hello")


class CrashLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.crash_log = self.tmp / "crash.log"
        patcher = mock.patch.object(crash, "CRASH_LOG", self.crash_log)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_crash_log_appends_with_source(self):
        crash.write_crash_log("boom", source="unit-test")
        crash.write_crash_log("boom2")

        text = self.crash_log.read_text(encoding="utf-8")
        self.assertIn("[unit-test]", text)
        self.assertIn("boom", text)
        self.assertIn("boom2", text)

    def test_report_crash_survives_dialog_failure(self):
        with mock.patch.object(crash.messagebox, "showerror",
                               side_effect=RuntimeError("no display")):
            crash.report_crash("traceback text", source="unit-test")
        self.assertIn("traceback text", self.crash_log.read_text(encoding="utf-8"))


@unittest.skipUnless(TK_AVAILABLE, "需要 Tk 顯示環境")
class MainWindowLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        from fa.gui.app import FundAnalyzerGUI
        self.app = FundAnalyzerGUI(self.root)

    def test_poll_timers_are_scheduled(self):
        self.assertIsNotNone(self.app._log_poll_id)
        self.assertIsNotNone(self.app._result_poll_id)

    def test_shutdown_cancels_timers_and_destroys_root(self):
        self.app.shutdown()

        self.assertTrue(self.app._closed)
        self.assertIsNone(self.app._log_poll_id)
        self.assertIsNone(self.app._result_poll_id)
        with self.assertRaises(tk.TclError):
            self.root.winfo_exists()

        # 關閉後再被調用不應重新排隊
        self.app._poll_log_queue()
        self.app._poll_result_queue()

    def test_analysis_result_is_marshalled_to_main_thread(self):
        received = {}
        self.app._on_analysis_done = lambda summary, error: received.update(
            summary=summary, error=error)
        self.app.result_queue.put(("success", {"fund_code": "017436"}))
        self.app._poll_result_queue()
        self.assertEqual(received["summary"]["fund_code"], "017436")
        self.assertIsNone(received["error"])
        self.app.shutdown()

    def test_open_master_report_before_analysis_warns(self):
        # _master_report_path 初始為 None：不能對 None 調 .exists()
        with mock.patch("fa.gui.app.messagebox.showwarning") as warn:
            self.app.open_master_report()
        warn.assert_called_once()
        self.app.shutdown()

    def test_post_market_window_is_reused_and_closed_with_main_window(self):
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
            first = self.app._post_market_window
            self.assertIsNotNone(first)

            # 重複點擊必須複用同一窗口，不能再開第二個 60 秒輪詢線程
            self.app.open_post_market_window()
            self.assertIs(self.app._post_market_window, first)

            self.app.shutdown()

        self.assertIsNone(self.app._post_market_window)
        self.assertTrue(first._closed)


@unittest.skipUnless(TK_AVAILABLE, "需要 Tk 顯示環境")
class PostMarketWindowLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = tk.Tk()
        self.root.withdraw()
        self.payload = {
            "status": "ok",
            "estimate_return": 0.5,
            "yesterday_official_return": 0.1,
            "nq_futures_return": 0.2,
            "coverage_ratio": 0.9,
            "covered_weighted_return": 0.45,
            "holdings": [{
                "ticker": "MSFT", "name": "Microsoft", "weight": 5.0,
                "regular_price": 400.0, "post_price": 402.0, "change_pct": 0.5,
            }],
            "message": "",
        }
        patcher = mock.patch("fa.gui.post_market.fetch_post_market_estimate",
                             return_value=self.payload)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_window(self, on_close=None):
        from fa.gui.post_market import PostMarketWindow
        return PostMarketWindow(self.root, "017436", self.tmp, on_close=on_close)

    def test_close_cancels_timers_and_notifies_parent(self):
        closed = []
        win = self._make_window(on_close=lambda: closed.append(True))

        # 讓輪詢跑一輪，順便驗證 UI 更新沒有異常
        win._poll_result_queue()

        win.close()
        self.assertTrue(win._closed)
        self.assertEqual(closed, [True])
        self.assertFalse(win.winfo_exists())

        # 重複關閉是冪等的，回調只觸發一次
        win.close()
        self.assertEqual(closed, [True])

    def test_refresh_not_scheduled_after_close(self):
        win = self._make_window()
        win.close()
        win.refresh_data()
        self.assertIsNone(win._refresh_id)

    def test_coverage_is_shown_in_detail_label(self):
        win = self._make_window()
        win._poll_result_queue()
        self.root.update_idletasks()
        detail = win.lbl_detail.cget("text")
        self.assertIn("持仓覆盖", detail)
        self.assertIn("90.0%", detail)
        self.assertIn("已覆盖部分对净值贡献", detail)
        win.close()


if __name__ == "__main__":
    unittest.main()
