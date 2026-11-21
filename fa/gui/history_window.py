# -*- coding: utf-8 -*-
"""Report centre: estimation and backtest history (V2.4 Stage D).

The window only talks to ``fa.application.reporting``.  It never imports the
database layer, never imports a renderer directly and never re-runs a
backtest or an estimate: the text shown in the preview is exactly the text
the service returns, and the export button writes that same text.
"""
from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

from fa.application import reporting
from fa.gui.formatting import MISSING, date_text, money, pct, status_text
from fa.gui.worker import ProgressReporter, WorkerBridge

ESTIMATION_COLUMNS = (
    ("run_id", "Run ID", 200),
    ("fund_code", "基金代碼", 90),
    ("target_nav_date", "目標淨值日", 100),
    ("status", "狀態", 90),
    ("estimated_nav", "估算淨值", 90),
    ("coverage_ratio", "覆蓋率", 80),
    ("data_quality", "品質", 70),
    ("created_at", "建立時間", 130),
)

BACKTEST_COLUMNS = (
    ("run_id", "Run ID", 200),
    ("spec_id", "策略 ID", 120),
    ("period", "期間", 190),
    ("sessions", "交易日", 70),
    ("trades", "交易筆數", 80),
    ("cagr", "CAGR", 80),
    ("max_drawdown", "最大回撤", 90),
    ("created_at", "建立時間", 130),
)


def estimation_history_rows(rows: list[dict]) -> list[tuple[str, ...]]:
    """Display rows in the service order; the GUI never re-sorts them."""
    result = []
    for row in rows:
        result.append((
            str(row.get("run_id") or MISSING),
            str(row.get("fund_code") or MISSING),
            date_text(row.get("target_nav_date")),
            status_text(str(row.get("status") or "")),
            money(row.get("estimated_nav")),
            pct(row.get("coverage_ratio"), digits=1, signed=False),
            status_text(str(row.get("data_quality") or "")),
            date_text(row.get("created_at")),
        ))
    return result


def backtest_history_rows(rows: list[dict]) -> list[tuple[str, ...]]:
    result = []
    for row in rows:
        start = date_text(row.get("start"))
        end = date_text(row.get("end"))
        result.append((
            str(row.get("run_id") or MISSING),
            str(row.get("spec_id") or MISSING),
            f"{start} … {end}",
            str(row.get("sessions") if row.get("sessions") is not None else MISSING),
            str(row.get("trades") if row.get("trades") is not None else MISSING),
            pct(row.get("cagr")),
            pct(row.get("max_drawdown")),
            date_text(row.get("created_at")),
        ))
    return result


# --- worker entry points (module level: no widget may cross the thread) ---

def _estimations_job(reporter: ProgressReporter, fund_code: str,
                     repository_path: str | None) -> list[dict]:
    reporter.report("讀取估算歷史…")
    return reporting.history_estimations(
        fund_code or None, repository_path=repository_path)


def _backtests_job(reporter: ProgressReporter, spec_id: str,
                   repository_path: str | None) -> list[dict]:
    reporter.report("讀取回測歷史…")
    return reporting.history_backtests(
        spec_id or None, repository_path=repository_path)


def _estimation_text_job(reporter: ProgressReporter, run_id: str,
                         repository_path: str | None,
                         save_dir: str | None) -> str:
    reporter.report("渲染估算報告…")
    return reporting.render_estimation(
        run_id, repository_path=repository_path, save_dir=save_dir)


def _backtest_text_job(reporter: ProgressReporter, run_id: str,
                       repository_path: str | None) -> str:
    reporter.report("渲染回測報告…")
    return reporting.render_backtest(run_id, repository_path=repository_path)


def _estimate_export_job(reporter: ProgressReporter, run_id: str,
                         repository_path: str | None,
                         save_dir: str | None) -> Path:
    reporter.report("匯出估算報告…")
    return reporting.export_estimation(
        run_id, repository_path=repository_path, save_dir=save_dir)


def _backtest_export_job(reporter: ProgressReporter, run_id: str,
                         repository_path: str | None) -> Path:
    reporter.report("匯出回測報告…")
    return reporting.export_backtest(run_id, repository_path=repository_path)


class _HistoryPane(ttk.Frame):
    """One list + filter + preview + export, all worker driven."""

    def __init__(self, master: tk.Misc, *, columns, list_job, text_job,
                 export_job, filter_label: str,
                 list_extra: tuple = (), detail_extra: tuple = ()) -> None:
        super().__init__(master)
        self._columns = columns
        self._list_job = list_job
        self._text_job = text_job
        self._export_job = export_job
        self._list_extra = tuple(list_extra)
        self._detail_extra = tuple(detail_extra)
        self._closed = False
        self._rows: list[dict] = []
        self._run_id: str | None = None
        self._pending_run_id: str | None = None
        self._text = ""
        self.bridge = WorkerBridge(self)
        self._busy: str | None = None

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(bar, text=filter_label).pack(side="left")
        self.filter_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.filter_var, width=18).pack(
            side="left", padx=4)
        ttk.Button(bar, text="重新整理", command=self.refresh).pack(side="left")
        self.status_var = tk.StringVar(value="就緒")
        ttk.Label(bar, textvariable=self.status_var).pack(side="right")

        panes = ttk.Panedwindow(self, orient="vertical")
        panes.pack(fill="both", expand=True, padx=6, pady=4)
        table = ttk.Frame(panes)
        self.tree = ttk.Treeview(table, columns=[c[0] for c in columns],
                                 show="headings", height=9)
        for key, title, width in columns:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w")
        scroll = ttk.Scrollbar(table, orient="vertical",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        panes.add(table, weight=2)

        preview = ttk.Frame(panes)
        self.text = tk.Text(preview, wrap="none", height=14, state="disabled")
        text_scroll = ttk.Scrollbar(preview, orient="vertical",
                                    command=self.text.yview)
        self.text.configure(yscrollcommand=text_scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        text_scroll.pack(side="right", fill="y")
        panes.add(preview, weight=3)

        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=6, pady=(0, 6))
        self.export_button = ttk.Button(actions, text="匯出",
                                        command=self.export, state="disabled")
        self.export_button.pack(side="left")
        self.path_var = tk.StringVar(value="")
        ttk.Label(actions, textvariable=self.path_var).pack(
            side="left", padx=8)

    # -- list -------------------------------------------------------------
    def refresh(self) -> None:
        if self._closed or self.bridge.is_running:
            return
        self._busy = "list"
        self.status_var.set("讀取中…")
        self.bridge.submit(self._list_job, self.filter_var.get().strip() or None,
                           *self._list_extra, on_result=self._on_list)

    def _on_list(self, ok: bool, payload: Any) -> None:
        self._busy = None
        if not ok:
            self.status_var.set(f"讀取失敗：{payload}")
            return
        self._rows = list(payload or [])
        builder = (estimation_history_rows if self._columns is ESTIMATION_COLUMNS
                   else backtest_history_rows)
        display = builder(self._rows)
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(display):
            self.tree.insert("", "end", iid=str(index), values=row)
        self.status_var.set(f"{len(display)} 筆")

    # -- detail -----------------------------------------------------------
    def _current(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return self._rows[int(selection[0])]
        except (IndexError, ValueError):
            return None

    def _on_select(self, _event=None) -> None:
        row = self._current()
        if row is None or self.bridge.is_running:
            return
        # F5: keep the pending id separate so a failed load cannot leave the
        # preview showing one run while the export button targets another.
        self._pending_run_id = str(row.get("run_id") or "")
        self._busy = "text"
        self.status_var.set("載入報告…")
        self.bridge.submit(self._text_job, self._pending_run_id,
                           *self._detail_extra, on_result=self._on_text)

    def _on_text(self, ok: bool, payload: Any) -> None:
        self._busy = None
        if not ok:
            self.status_var.set(f"載入失敗：{payload}")
            return
        self._run_id = self._pending_run_id
        self._text = str(payload)
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", self._text)
        self.text.configure(state="disabled")
        self.export_button.configure(state="normal")
        self.status_var.set("已載入報告")

    # -- export -----------------------------------------------------------
    def export(self) -> None:
        if not self._run_id or self.bridge.is_running:
            return
        self._busy = "export"
        self.status_var.set("匯出中…")
        self.bridge.submit(self._export_job, self._run_id, *self._detail_extra,
                           on_result=self._on_export)

    def _on_export(self, ok: bool, payload: Any) -> None:
        self._busy = None
        if not ok:
            self.status_var.set(f"匯出失敗：{payload}")
            return
        self.path_var.set(str(payload))
        self.status_var.set("已匯出")

    # -- lifecycle --------------------------------------------------------
    def close(self, timeout: float = 2.0) -> None:
        self._closed = True
        self.bridge.close(timeout=timeout)

    @property
    def closed(self) -> bool:
        return self._closed


class HistoryWindow(tk.Toplevel):
    """Unified history window: one pane for estimates, one for backtests."""

    def __init__(self, master: tk.Misc, *, repository_path: str | None = None,
                 save_dir: str | Path | None = None,
                 on_close: Callable[[], None] | None = None) -> None:
        super().__init__(master)
        self.title("報告與歷史 — Report centre")
        self.geometry("1180x720")
        self.minsize(900, 560)
        self._on_close = on_close
        self._closed = False
        self.repository_path = str(repository_path) if repository_path else None
        self.save_dir = str(save_dir) if save_dir else None

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        self.estimation_pane = _HistoryPane(
            notebook, columns=ESTIMATION_COLUMNS,
            list_job=_estimations_job, text_job=_estimation_text_job,
            export_job=_estimate_export_job, filter_label="基金代碼",
            list_extra=(self.repository_path,),
            detail_extra=(self.repository_path, self.save_dir))
        self.backtest_pane = _HistoryPane(
            notebook, columns=BACKTEST_COLUMNS,
            list_job=_backtests_job, text_job=_backtest_text_job,
            export_job=_backtest_export_job, filter_label="策略 ID",
            list_extra=(self.repository_path,),
            detail_extra=(self.repository_path,))
        notebook.add(self.estimation_pane, text="估算歷史")
        notebook.add(self.backtest_pane, text="回測歷史")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(50, self._initial_load)

    def _initial_load(self) -> None:
        if self._closed:
            return
        self.estimation_pane.refresh()
        self.backtest_pane.refresh()

    # -- lifecycle --------------------------------------------------------
    def close(self, *, timeout: float = 2.0) -> None:
        """Close within one shared budget, never N × 2 seconds.

        Each ``WorkerBridge.close`` joins its thread for up to the timeout it
        is given, so two panes each waiting two seconds could take four.  The
        deadline is therefore computed once and each pane only receives the
        time that is actually left (F1 of the Stage D review).
        """
        if self._closed:
            return
        self._closed = True
        try:
            budget = max(0.0, min(float(timeout), 2.0))
        except (TypeError, ValueError):
            budget = 2.0
        deadline = time.monotonic() + budget
        for pane in (self.estimation_pane, self.backtest_pane):
            remaining = max(0.0, min(2.0, deadline - time.monotonic()))
            pane.close(remaining)
        callback = self._on_close
        self._on_close = None
        if callback is not None:
            try:
                callback()
            except Exception:
                pass
        try:
            self.destroy()
        except tk.TclError:
            pass

    @property
    def closed(self) -> bool:
        return self._closed


__all__ = [
    "BACKTEST_COLUMNS",
    "ESTIMATION_COLUMNS",
    "HistoryWindow",
    "backtest_history_rows",
    "estimation_history_rows",
]
