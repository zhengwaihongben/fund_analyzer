# -*- coding: utf-8 -*-
"""Stage B-3：QDII 估算視圖的呈現層與視窗行為測試。

純函式測試不建立 Tk root；需要 widget 的測試用 withdrawn root，並在主執行緒
回收 Tk 變數（`gc.collect()`），避免 Tcl async handler 在別處被回收。
"""
from __future__ import annotations

import gc
import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import tkinter as tk
from tkinter import ttk

from fa.application import estimation as service
from fa.application.estimation import EstimationView
from fa.data.database.repository import Repository
from fa.gui import qdii_estimate as view_module
from fa.gui.formatting import MISSING
from fa.gui.qdii_estimate import (
    HISTORY_COLUMNS,
    QdiiEstimateWindow,
    asset_table_rows,
    history_rows,
    quality_lines,
    summary_fields,
)
from fa.models.estimation import EstimationResult, EstimationRun


def _record(**overrides):
    record = {
        "fund_code": "000369",
        "base_nav": 2.8150,
        "estimated_nav": 2.8205,
        "estimated_return": 0.0019,
        "target_nav_date": "2026-11-13",
        "base_nav_date": "2026-11-12",
        "coverage_ratio": 0.953,
        "uncovered_weight": 0.047,
        "data_quality": "partial",
        "look_ahead_status": "estimated-disclosure",
        "look_ahead_safe": True,
        "model_version": "2.2.0",
        "status": "partial",
        "holdings_quarter": "2026Q3",
        "holdings_scope": "top10",
        "disclosure_date": "2026-10-21",
        "disclosure_date_estimated": True,
        "market_data_date": "2026-11-13",
        "fx_source": "pipeline-pit",
        "warnings": ["持倉僅前十大，權重為披露值"],
    }
    record.update(overrides)
    return record


def _view(**overrides):
    values = {
        "record": _record(),
        "mode": "live",
        "run_id": "abc123",
        "persisted": True,
        "asset_rows": [
            {
                "ticker": "LLY",
                "name": "礼来",
                "asset_type": "equity",
                "market": "US",
                "currency": "USD",
                "weight": 0.1043,
                "base_price_date": "2026-11-12",
                "target_price_date": "2026-11-13",
                "local_return": 0.0126,
                "fx_return": 0.0,
                "cny_return": 0.0126,
                "contribution": 0.0013,
                "status": "ok",
            },
            {
                "ticker": "ROP SW",
                "name": "罗氏控股",
                "asset_type": "equity",
                "market": "EBS",
                "currency": "CHF",
                "weight": None,
                "base_price_date": None,
                "target_price_date": None,
                "local_return": None,
                "fx_return": None,
                "cny_return": None,
                "contribution": None,
                "status": "missing-fx",
            },
        ],
        "asset_source": "SQLite 輸入快照",
        "warnings": ["持倉僅前十大，權重為披露值"],
        "message": "",
        "inputs": {"price_source": "yahoo", "holdings_scope": "top10"},
    }
    values.update(overrides)
    return EstimationView(**values)


class SummaryFieldTests(unittest.TestCase):
    def test_summary_fields_cover_the_spec(self):
        labels = [label for label, _ in summary_fields(_view())]
        self.assertEqual(
            labels,
            [
                "基金代碼",
                "上次官方淨值",
                "估算淨值",
                "估算漲跌",
                "估算基準日",
                "基準淨值日",
                "持倉覆蓋率",
                "未覆蓋權重",
                "資料品質",
                "前視狀態",
                "模型版本",
                "Run ID",
                "寫入狀態",
            ],
        )

    def test_missing_values_render_as_dash(self):
        fields = dict(summary_fields(_view(record={}, run_id=None)))
        self.assertEqual(fields["上次官方淨值"], MISSING)
        self.assertEqual(fields["估算淨值"], MISSING)
        self.assertEqual(fields["估算漲跌"], MISSING)
        self.assertEqual(fields["持倉覆蓋率"], MISSING)
        self.assertEqual(fields["Run ID"], MISSING)
        self.assertEqual(fields["模型版本"], MISSING)

    def test_live_persistence_is_stated_from_the_view_only(self):
        saved = dict(summary_fields(_view(persisted=True)))
        missing = dict(summary_fields(_view(persisted=False)))
        self.assertEqual(saved["寫入狀態"], "已寫入（read-back 確認）")
        self.assertEqual(missing["寫入狀態"], "未寫入（僅檔案）")

    def test_stored_and_handover_state_are_distinct(self):
        stored = dict(summary_fields(_view(mode="stored")))
        handed = dict(summary_fields(_view(mode="handover", persisted=False)))
        self.assertEqual(stored["寫入狀態"], "已保存紀錄（唯讀）")
        self.assertEqual(handed["寫入狀態"], "未寫入資料庫（分析結果交接）")

    def test_four_state_look_ahead_text(self):
        expected = {
            "verified-safe": "已驗證安全（無前視偏差）",
            "estimated-disclosure": "披露日為推算值",
            "unsafe": "未通過前視檢查",
            "unknown": "前視狀態不明",
        }
        for status, text in expected.items():
            record = _record(look_ahead_status=status, look_ahead_safe=None)
            self.assertEqual(dict(summary_fields(_view(record=record)))["前視狀態"], text)

    def test_stored_boolean_look_ahead_is_not_invented_into_a_state(self):
        record = _record()
        record.pop("look_ahead_status")
        record["look_ahead_safe"] = True
        self.assertEqual(
            dict(summary_fields(_view(record=record, mode="stored")))["前視狀態"],
            "已驗證安全（布林值）",
        )
        record["look_ahead_safe"] = False
        self.assertEqual(
            dict(summary_fields(_view(record=record, mode="stored")))["前視狀態"],
            "未驗證安全（布林值）",
        )

    def test_ratios_are_unsigned_percentages(self):
        fields = dict(summary_fields(_view()))
        self.assertEqual(fields["持倉覆蓋率"], "95.3%")
        self.assertEqual(fields["未覆蓋權重"], "4.7%")


class AssetTableTests(unittest.TestCase):
    def test_columns_follow_the_spec_order(self):
        rows = asset_table_rows(_view())
        self.assertEqual(len(rows[0]), len(view_module.TABLE_COLUMNS))
        self.assertEqual(
            [heading for _, heading, _ in view_module.TABLE_COLUMNS],
            [
                "代碼", "名稱", "類型", "市場", "幣種", "權重", "基準日", "目標日",
                "本地回報", "匯率回報", "人民幣回報", "淨值貢獻", "狀態",
            ],
        )

    def test_weight_is_unsigned_and_returns_are_signed(self):
        row = asset_table_rows(_view())[0]
        self.assertEqual(row[5], "10.43%")
        self.assertEqual(row[8], "+1.26%")
        self.assertEqual(row[11], "+0.13%")

    def test_bad_status_row_shows_status_text_instead_of_numbers(self):
        row = asset_table_rows(_view())[1]
        self.assertEqual(row[10], "missing-fx")
        self.assertEqual(row[11], "missing-fx")
        self.assertEqual(row[12], "missing-fx")

    def test_unknown_weight_and_contribution_never_become_zero(self):
        rows = asset_table_rows(_view())
        self.assertEqual(rows[1][5], MISSING)
        self.assertEqual(rows[1][11], "missing-fx")
        self.assertEqual(rows[0][11], "+0.13%")

    def test_missing_dates_render_as_dash(self):
        row = asset_table_rows(_view())[1]
        self.assertEqual(row[6], MISSING)
        self.assertEqual(row[7], MISSING)


class QualityBlockTests(unittest.TestCase):
    def test_quality_block_reports_quarter_scope_disclosure_and_sources(self):
        text = "\n".join(quality_lines(_view()))
        self.assertIn("2026Q3", text)
        self.assertIn("前十大（揭露口徑）", text)
        self.assertIn("2026-10-21（推算）", text)
        self.assertIn("匯率來源：pipeline-pit", text)

    def test_warnings_are_kept_verbatim(self):
        text = "\n".join(quality_lines(_view()))
        self.assertIn("警告：持倉僅前十大，權重為披露值", text)

    def test_uncovered_weight_is_explained_not_zeroed(self):
        text = "\n".join(quality_lines(_view()))
        self.assertIn("未覆蓋權重：4.7%", text)
        self.assertIn("未估算", text)

    def test_missing_asset_rows_use_the_contract_message(self):
        text = "\n".join(
            quality_lines(_view(asset_rows=[], asset_source="", message=""))
        )
        self.assertIn(service.NO_ASSET_SOURCE_TEXT, text)

    def test_no_fabricated_source_when_the_view_has_none(self):
        text = "\n".join(quality_lines(_view(inputs={}, asset_source="")))
        self.assertNotIn("價格來源", text)


class HistoryRowTests(unittest.TestCase):
    def test_history_rows_preserve_the_service_order(self):
        rows = [
            {"fund_code": "000369", "target_nav_date": "2026-11-13", "status": "partial",
             "estimated_nav": 2.8205, "coverage_ratio": 0.953, "data_quality": "partial",
             "run_id": "run-b"},
            {"fund_code": "000001", "target_nav_date": "2026-11-12", "status": "estimated",
             "estimated_nav": 1.1, "coverage_ratio": 0.8, "data_quality": "ok",
             "run_id": "run-a"},
        ]
        rendered = history_rows(rows)
        self.assertEqual(len(rendered), 2)
        self.assertEqual(rendered[0][6], "run-b")
        self.assertEqual(rendered[1][6], "run-a")
        self.assertEqual(rendered[0][3], "2.8205")
        self.assertEqual(rendered[0][4], "95.3%")

    def test_history_missing_values_are_dashes(self):
        rendered = history_rows([{"run_id": None, "estimated_nav": None}])
        self.assertEqual(rendered[0][3], MISSING)
        self.assertEqual(rendered[0][6], MISSING)


def _pump(root, predicate, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return True
        time.sleep(0.01)
    return False


class _TkCase(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.root = tk.Tk()
        self.root.withdraw()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(self._destroy)

    def _destroy(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        gc.collect()


class WindowTests(_TkCase):
    def test_handover_renders_without_calling_the_service(self):
        with mock.patch.object(view_module, "run_estimate") as live, \
                mock.patch.object(view_module, "load_estimate") as stored:
            record = _record(status="insufficient-data", estimated_return=None)
            window = QdiiEstimateWindow(self.root, "000369", Path(self.dir.name),
                                        estimate=record)
            window.update_idletasks()
            self.assertEqual(window.view.mode, "handover")
            self.assertIn("未寫入資料庫", dict(summary_fields(window.view))["寫入狀態"])
            live.assert_not_called()
            stored.assert_not_called()
            window.close()

    def test_stored_lookup_error_is_reported_without_traceback(self):
        with mock.patch.object(
            view_module, "load_estimate",
            side_effect=LookupError("找不到估值紀錄：deadbeef"),
        ):
            window = QdiiEstimateWindow(self.root, "000369", Path(self.dir.name),
                                        mode="stored", run_id="deadbeef")
            self.assertTrue(_pump(self.root, lambda: "找不到估值紀錄" in window.lbl_headline.cget("text")))
            self.assertIn("deadbeef", window.lbl_status.cget("text"))
            window.close()

    def test_live_mode_goes_through_the_service_and_reports_persisted(self):
        saved = []
        canned = _view(persisted=True, run_id="live-1")
        with mock.patch.object(view_module, "run_estimate", return_value=canned) as live:
            window = QdiiEstimateWindow(self.root, "000369", Path(self.dir.name),
                                        on_saved=saved.append)
            self.assertTrue(_pump(self.root, lambda: window.view is not None))
            live.assert_called_once()
            self.assertEqual(live.call_args.args[0], "000369")
            self.assertEqual(dict(summary_fields(window.view))["寫入狀態"], "已寫入（read-back 確認）")
            self.assertEqual(saved, ["live-1"])
            window.close()

    def test_close_drops_a_late_result(self):
        import threading

        release = threading.Event()

        def slow(reporter, fund_code, save_dir):
            release.wait(2.0)
            return _view(run_id="late")

        with mock.patch.object(view_module, "_live_estimate_job", slow):
            window = QdiiEstimateWindow(self.root, "000369", Path(self.dir.name))
            window.refresh()
            window.close()
            release.set()
            time.sleep(0.1)
            self.assertIsNone(window.view)


def _run(run_id, fund="000369", target=date(2026, 11, 17)):
    return EstimationRun(fund_code=fund, target_nav_date=target, trade_date=target,
                         run_id=run_id)


def _result(run_id):
    return EstimationResult(run_id=run_id, status="estimated", base_nav=1.0,
                            estimated_nav=1.01, estimated_return=0.01,
                            coverage_ratio=0.5)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BrowsingIsReadOnlyTests(unittest.TestCase):
    def test_browsing_does_not_rewrite_rows_or_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            db_path = tmp / "db.sqlite"
            repo = Repository(db_path)
            try:
                repo.save_estimation(_run("run-1"), {"assets": [{"ticker": "LLY", "weight": 0.1}]},
                                     _result("run-1"))
                repo.save_estimation(_run("run-2", fund="000001"),
                                     {"assets": [{"ticker": "JNJ", "weight": 0.2}]},
                                     _result("run-2"))
            finally:
                repo.close()

            latest = tmp / "000369_qdii_estimate.json"
            latest.write_text(json.dumps({
                "run_id": "run-1",
                "holdings": [{"ticker": "LLY", "contribution": 0.001}],
            }), encoding="utf-8")
            snapshot = tmp / "000369_qdii_estimate_inputs.json"
            snapshot.write_text(json.dumps({
                "fund_code": "000369",
                "snapshots": {"run-1": {"run_id": "run-1", "holdings": [{"ticker": "LLY", "weight": 0.1}]}},
            }), encoding="utf-8")

            before_counts = self._counts(db_path)
            before_files = (_digest(latest), _digest(snapshot))

            rows = service.list_estimates(db_path=db_path)
            self.assertEqual([row["run_id"] for row in rows], ["run-1", "run-2"])
            rendered = history_rows(rows)
            self.assertEqual(len(rendered), 2)

            view = service.load_estimate("run-1", db_path=db_path, save_dir=tmp)
            self.assertEqual(view.mode, "stored")
            self.assertEqual(asset_table_rows(view)[0][0], "LLY")

            self.assertEqual(self._counts(db_path), before_counts)
            self.assertEqual((_digest(latest), _digest(snapshot)), before_files)

    @staticmethod
    def _counts(db_path):
        conn = sqlite3.connect(str(db_path))
        try:
            return {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("estimation_runs", "estimation_inputs", "estimation_results")
            }
        finally:
            conn.close()


class StoredLookAheadTests(unittest.TestCase):
    """F4: SQLite returns look_ahead_safe as 0/1, not as a Python bool."""

    def test_stored_boolean_zero_and_one_render_instead_of_a_dash(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            db_path = tmp / "db.sqlite"
            repo = Repository(db_path)
            try:
                repo.save_estimation(_run("run-1", target=date(2026, 11, 17)),
                                     {"holdings": {"quarter": "2026Q3"}},
                                     _result("run-1"))
                repo.save_estimation(_run("run-2", target=date(2026, 11, 18)),
                                     {"holdings": {"quarter": "2026Q3"}},
                                     _result("run-2"))
                stored = repo.load_estimation("run-1")
                self.assertEqual(stored["look_ahead_safe"], 0)
            finally:
                repo.close()

            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "UPDATE estimation_runs SET look_ahead_safe = 1 WHERE run_id = 'run-2'"
                )
                conn.commit()
            finally:
                conn.close()

            view_zero = service.load_estimate("run-1", db_path=db_path, save_dir=tmp)
            summary_zero = dict(summary_fields(view_zero))
            self.assertEqual(summary_zero["前視狀態"], "未驗證安全（布林值）")
            self.assertIn("未驗證安全（布林值）", "\n".join(quality_lines(view_zero)))

            view_one = service.load_estimate("run-2", db_path=db_path, save_dir=tmp)
            summary_one = dict(summary_fields(view_one))
            self.assertEqual(summary_one["前視狀態"], "已驗證安全（布林值）")
            self.assertNotIn("—", summary_one["前視狀態"])


class NestedInputsQualityTests(unittest.TestCase):
    """F5: the real inputs payload nests holdings metadata under ``holdings``."""

    def _view(self):
        record = _record(
            holdings_quarter=None, holdings_scope=None, disclosure_date=None,
            disclosure_date_estimated=None, market_data_date=None,
        )
        inputs = {
            "fund_code": "000369",
            "model_version": "2.2.0",
            "holdings": {
                "quarter": "2026Q3",
                "holding_date": "2026-09-30",
                "disclosure_date": "2026-10-21",
                "disclosure_date_estimated": True,
                "source": "eastmoney-page",
                "scope": "top10",
                "total_weight": 0.461,
            },
            "assets": [
                {"ticker": "LLY", "used_price_type": "close+adj_close"},
                {"ticker": "ROP SW", "used_price_type": "close+adj_close"},
            ],
            "fx_source": "pipeline-pit",
        }
        return _view(record=record, inputs=inputs)

    def test_nested_holdings_metadata_is_rendered(self):
        text = "\n".join(quality_lines(self._view()))
        self.assertIn("持倉季度：2026Q3", text)
        self.assertIn("口徑：前十大（揭露口徑）", text)
        self.assertIn("披露日：2026-10-21（推算）", text)
        self.assertIn("持倉來源：eastmoney-page", text)
        self.assertIn("匯率來源：pipeline-pit", text)
        self.assertIn("價格型別：close+adj_close", text)
        self.assertNotIn("價格來源：", text)
        self.assertNotIn("持倉季度：—", text)

    def test_a_payload_without_holdings_says_missing_not_wrong(self):
        view = _view(record=_record(holdings_quarter=None, holdings_scope=None,
                                    disclosure_date=None),
                     inputs={"assets": []})
        text = "\n".join(quality_lines(view))
        self.assertIn("持倉季度：—", text)
        self.assertNotIn("持倉來源：", text)


class WindowModeSwitchTests(_TkCase):
    def test_handover_refresh_switches_to_live_and_calls_the_service(self):
        canned = _view(mode="live", run_id="live-2", persisted=True)
        with mock.patch.object(view_module, "_live_estimate_job",
                               mock.Mock(side_effect=lambda reporter, fund_code, save_dir: canned)) as job:
            window = QdiiEstimateWindow(self.root, "000369", Path(self.dir.name),
                                        estimate=_record(status="estimated"))
            self.assertEqual(window.mode, "handover")
            window.refresh()
            self.assertTrue(_pump(self.root, lambda: window.view is not None
                                  and window.mode == "live"))
            self.assertEqual(window.mode, "live")
            self.assertEqual(window.run_id, "live-2")
            job.assert_called_once()
            window.close()

    def test_load_stored_updates_mode_and_reload_keeps_reloading(self):
        canned = _view(mode="stored", run_id="run-2", persisted=True)
        with mock.patch.object(view_module, "_load_estimate_job",
                               mock.Mock(side_effect=lambda reporter, run_id, save_dir: canned)) as job:
            window = QdiiEstimateWindow(self.root, "000369", Path(self.dir.name),
                                        estimate=_record(status="estimated"))
            window.load_stored("run-2")
            self.assertTrue(_pump(self.root, lambda: window.view is not None
                                  and window.mode == "stored"))
            self.assertEqual(window.run_id, "run-2")
            self.assertEqual(window.action_btn.cget("text"), "重新載入")
            window.refresh()
            self.assertTrue(_pump(self.root, lambda: job.call_count == 2))
            self.assertEqual(job.call_count, 2)
            window.close()

    def test_quality_block_scrolls_and_keeps_every_warning(self):
        warnings = [f"警告編號 {index}：" + "x" * 40 for index in range(20)]
        canned = _view(warnings=warnings, mode="live", persisted=True)
        with mock.patch.object(view_module, "run_estimate", return_value=canned):
            window = QdiiEstimateWindow(self.root, "000369", Path(self.dir.name))
            self.assertTrue(_pump(self.root, lambda: window.view is not None))
            self.assertEqual(int(window.quality_text.cget("height")), 10)
            self.assertIsInstance(window.quality_scroll, ttk.Scrollbar)
            shown = window.quality_text.get("1.0", "end")
            self.assertIn(warnings[-1], shown)
            self.assertIn(warnings[0], shown)
            window.close()


if __name__ == "__main__":
    unittest.main()
