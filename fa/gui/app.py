"""Main Tkinter window: run an analysis, then expose the generated reports.

Layout (V2.4 Stage A): a notebook with four areas plus one shared log pane
and one shared status bar.  Only the fund-analysis area is wired to the
financial pipeline in this stage; the other three are placeholders whose
content arrives in Stages B-D.
"""
from __future__ import annotations

import logging
import queue
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from fa.gui.crash import show_error
from fa.gui.navigation import MainNotebook, WindowRegistry
from fa.gui.worker import WorkerBridge
from fa.utils.logging_setup import get_logger

# Heavy imports (pandas/matplotlib, the backtesting engine, the QDII/backtest/
# history windows) are deliberately deferred into the handlers that need them:
# importing them at module import time made a cold GUI start take 6.8 s instead
# of the 3 s budget (V2.4 E-02).  See docs/development/v24_evidence/E-02.md.

log = get_logger(__name__)

__all__ = ["FundAnalyzerGUI", "QueueHandler", "show_error"]


class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        try:
            self.log_queue.put(self.format(record))
        except Exception:
            pass


def _analysis_job(reporter, fund_code, output_dir, cfg, no_cache):
    """Worker-side analysis: plain data in, plain data out (no Tk objects).

    ``reporter`` is the task-scoped :class:`fa.gui.worker.ProgressReporter`;
    the analysis itself has no progress channel to feed today, but accepting
    it keeps the worker signature uniform and keeps the bridge out of reach.
    """
    del reporter
    from fa.pipeline import analyze_fund

    return analyze_fund(
        fund_code=fund_code,
        base_output=output_dir,
        cfg=cfg,
        show=False,
        use_cache=not no_cache,
    )


_PLACEHOLDERS = {
    "estimation": (
        "QDII 估算",
        "此功能將於 Stage B 開放。\n\n"
        "預計支援：\n"
        "• QDII 基金淨值估算\n"
        "• 估算結果與資料品質展示\n"
        "• 歷史估算記錄查閱",
    ),
    "backtest": (
        "策略回測",
        "此功能將於 Stage C 開放。\n\n"
        "預計支援：\n"
        "• 策略設定與送單\n"
        "• 回測執行與既有結果重用\n"
        "• 績效、交易與淨值曲線",
    ),
    "reports": (
        "報告與歷史",
        "此功能將於 Stage D 開放。\n\n"
        "預計支援：\n"
        "• 估算歷史與回測歷史\n"
        "• 詳細結果頁與報告匯出",
    ),
}


def _history_job(reporter, fund_code):
    """Worker-side history read; returns plain dictionaries."""
    reporter.report("正在讀取估值歷史…")
    from fa.application.estimation import list_estimates

    return list_estimates(fund_code or None, limit=100)


class FundAnalyzerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Fund Analyzer — 基金分析工具")
        self.root.geometry("860x680")
        self.root.minsize(720, 560)

        self.log_queue = queue.Queue()
        self.result_queue = queue.Queue()   # 保留給分析結果的相容通道
        self.is_running = False
        self.result_files = {}       # {filename: Path}
        self._output_dir = None
        self._master_report_path = None
        self._post_market_window = None
        self._qdii_window = None
        self._qdii_estimate = None
        self._backtest_window = None

        # 生命週期：shutdown() 之後所有 after() 定時器都不再重新排隊
        self._history_loaded = False
        self._history_rows = []

        self._closed = False
        self._worker = None
        self._log_poll_id = None
        self._result_poll_id = None

        self._build_ui()
        self._setup_logging()

        self.worker = WorkerBridge(self.root)
        self.windows = WindowRegistry(self.root)
        self.notebook.registry = self.windows
        self.windows.register("qdii_estimate", self._make_qdii_window)
        self.windows.register("backtest", self._make_backtest_window)
        self.windows.register("history", self._make_history_window)
        self.history_worker = WorkerBridge(root)
        self._history_rows = []
        self.windows.register("post_market", self._make_post_market_window)

    # --------------------------------------------------------
    # UI 佈局
    # --------------------------------------------------------
    def _build_ui(self):
        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True)

        self.notebook = MainNotebook(main)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        self.analysis_tab = self.notebook.add_area("analysis")
        self.estimation_tab = self.notebook.add_area("estimation")
        self.backtest_tab = self.notebook.add_area("backtest")
        self.reports_tab = self.notebook.add_area("reports")

        # E-02a: only the analysis tab is built at startup.  The other three
        # pull pandas/matplotlib/engine code on first use, which would blow the
        # cold-start budget; they are created when their tab is first selected.
        self._tab_builders = {
            "estimation": self._build_estimation_tab,
            "backtest": self._build_backtest_tab,
            "reports": self._build_reports_tab,
        }
        self._tab_frames = {
            "estimation": self.estimation_tab,
            "backtest": self.backtest_tab,
            "reports": self.reports_tab,
        }
        self._tabs_built: set[str] = set()
        self._build_analysis_tab(self.analysis_tab)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_shared_log_panel(main)

        self.status_var = tk.StringVar(value="就緒")
        status = ttk.Label(main, textvariable=self.status_var,
                           relief="sunken", anchor="w", padding=(6, 2))
        status.pack(fill="x", side="bottom")

    def _build_analysis_tab(self, parent: ttk.Frame):        # ---- 參數區 ----
        param = ttk.LabelFrame(parent, text=" 分析參數 ", padding=10)
        param.pack(fill="x", padx=10, pady=(10, 5))

        ttk.Label(param, text="基金代碼:").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=4)
        self.code_var = tk.StringVar()
        ttk.Entry(param, textvariable=self.code_var, width=14).grid(row=0, column=1, sticky="w", padx=(0, 16), pady=4)

        ttk.Label(param, text="無風險利率:").grid(row=0, column=2, sticky="w", padx=(0, 4), pady=4)
        self.rf_var = tk.StringVar(value="0.02")
        ttk.Entry(param, textvariable=self.rf_var, width=8).grid(row=0, column=3, sticky="w", pady=4)

        ttk.Label(param, text="基準指數:").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=4)
        self.bench_var = tk.StringVar(value=".NDX")
        ttk.Entry(param, textvariable=self.bench_var, width=14).grid(row=1, column=1, sticky="w", padx=(0, 16), pady=4)

        ttk.Label(param, text="輸出根目錄:").grid(row=1, column=2, sticky="w", padx=(0, 4), pady=4)
        self.output_var = tk.StringVar(value="output")
        ttk.Entry(param, textvariable=self.output_var, width=30).grid(
            row=1, column=3, columnspan=3, sticky="we", padx=(0, 0), pady=4)

        ttk.Label(param, text="起始日期:").grid(row=2, column=0, sticky="w", padx=(0, 4), pady=4)
        self.start_date_var = tk.StringVar()   # 留空 = 从成立日追踪
        ttk.Entry(param, textvariable=self.start_date_var, width=14).grid(
            row=2, column=1, sticky="w", padx=(0, 16), pady=4)

        ttk.Label(param,
                  text="格式 YYYY-MM-DD，留空 = 從基金成立日追蹤",
                  foreground="#888888").grid(
            row=2, column=2, columnspan=4, sticky="w", padx=(0, 0), pady=4)

        ttk.Label(param, text="基準市場:").grid(row=3, column=0, sticky="w", padx=(0, 4), pady=4)
        self.bench_market_var = tk.StringVar(value="auto")
        market_combo = ttk.Combobox(
            param, textvariable=self.bench_market_var,
            values=["auto", "us", "cn", "hk"],
            state="readonly", width=8,
        )
        market_combo.grid(row=3, column=1, sticky="w", padx=(0, 16), pady=4)

        ttk.Label(param,
                  text="auto=自動判斷（.NDX=美股，sh000300=A股，HSI=港股）",
                  foreground="#888888").grid(
            row=3, column=2, columnspan=4, sticky="w", padx=(0, 0), pady=4)

        param.columnconfigure(3, weight=1)

        # ---- 按鈕列 ----
        btns = ttk.Frame(parent)
        btns.pack(fill="x", padx=10, pady=5)

        self.run_btn = ttk.Button(btns, text="開始分析", command=self.start_analysis)
        self.run_btn.pack(side="left", padx=(0, 6))

        self.post_market_btn = ttk.Button(btns, text="盘后监控(实时预估)",
                                          command=self.open_post_market_window, state="disabled")
        self.post_market_btn.pack(side="left", padx=(0, 6))

        self.qdii_btn = ttk.Button(btns, text="QDII 淨值估算",
                                   command=self.open_qdii_handover, state="disabled")
        self.qdii_btn.pack(side="left", padx=(0, 6))

        # No emoji in this label: resolving an emoji glyph made this single
        # button cost 2.0 s of the cold start (E-02a measurement).
        self.open_all_btn = ttk.Button(btns, text="打开综合报告(视窗)",
                                       command=self.open_master_report, state="disabled")
        self.open_all_btn.pack(side="left", padx=(0, 6))

        self.open_dir_btn = ttk.Button(btns, text="打開輸出目錄",
                                       command=self.open_output_dir, state="disabled")
        self.open_dir_btn.pack(side="left", padx=(0, 6))

        self.ignore_cache_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btns, text="忽略緩存（強制刷新）",
                        variable=self.ignore_cache_var).pack(side="right")

        # ---- 結果列表 ----
        result_frame = ttk.LabelFrame(parent, text=" 生成的文件（雙擊打開） ", padding=4)
        result_frame.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.result_listbox = tk.Listbox(
            result_frame, height=7, font=("Consolas", 9),
            activestyle="none", selectbackground="#3b6ea5", selectforeground="white",
        )
        self.result_listbox.pack(fill="both", expand=True, side="left")

        scrollbar = ttk.Scrollbar(result_frame, orient="vertical",
                                  command=self.result_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.result_listbox.configure(yscrollcommand=scrollbar.set)
        self.result_listbox.bind("<Double-Button-1>", self._on_result_double_click)
        self.result_listbox.bind("<Return>", self._on_result_double_click)

    def _build_shared_log_panel(self, parent: ttk.Frame):
        log_frame = ttk.LabelFrame(parent, text=" 運行日誌 ", padding=4)
        log_frame.pack(fill="both", expand=False, padx=8, pady=(0, 4))
        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=12, state="disabled",
            font=("Consolas", 9), background="#1e1e1e", foreground="#d4d4d4",
            insertbackground="#d4d4d4", wrap="word",
        )
        self.log_text.pack(fill="both", expand=True)

    # --------------------------------------------------------
    # logging 設置
    # --------------------------------------------------------
    def _setup_logging(self):
        # 掛在 root logger 上：分析模組現在按 fa.* 命名空間記日誌，
        # 只掛 "fund_analyzer" 會讓整個日誌面板收不到任何訊息。
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)

        for h in root_logger.handlers[:]:
            if isinstance(h, QueueHandler):
                root_logger.removeHandler(h)

        handler = QueueHandler(self.log_queue)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-7s %(message)s",
            datefmt="%H:%M:%S",
        ))
        root_logger.addHandler(handler)

        self._log_poll_id = self.root.after(100, self._poll_log_queue)
        self._result_poll_id = self.root.after(100, self._poll_result_queue)

    def _poll_log_queue(self):
        if self._closed:
            return
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self._log_poll_id = self.root.after(100, self._poll_log_queue)

    def _poll_result_queue(self):
        """主線程輪詢分析結果，保證所有 GUI 更新都發生在主線程。"""
        if self._closed:
            return
        try:
            while True:
                status, payload = self.result_queue.get_nowait()
                if status == "success":
                    self._on_analysis_done(payload, None)
                else:
                    self._on_analysis_done(None, payload)
        except queue.Empty:
            pass
        self._result_poll_id = self.root.after(100, self._poll_result_queue)

    def shutdown(self):
        """關閉窗口：停掉定時器與背景任務，關閉子視窗，再銷毀 Tk。"""
        self._closed = True
        for after_id in (self._log_poll_id, self._result_poll_id):
            if after_id:
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    log.debug("取消定時器失敗: %s", after_id, exc_info=True)
        self._log_poll_id = self._result_poll_id = None

        for bridge in (self.worker, self.history_worker):
            try:
                bridge.close()
            except Exception:
                log.debug("關閉 WorkerBridge 失敗", exc_info=True)
        self._worker = None
        self.history_worker = None

        try:
            self.windows.close_all()
        except Exception:
            log.debug("關閉子視窗失敗", exc_info=True)
        self._qdii_window = None
        self._post_market_window = None

        self.root.destroy()

    def _append_log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # --------------------------------------------------------
    # 分析流程
    # --------------------------------------------------------
    def start_analysis(self):
        if self.is_running:
            messagebox.showinfo("提示", "分析正在進行中，請稍候。")
            return

        fund_code = self.code_var.get().strip()
        if not fund_code:
            messagebox.showwarning("提示", "請輸入基金代碼。")
            return
        if not fund_code.isdigit() or len(fund_code) != 6:
            if not messagebox.askyesno("確認", f"基金代碼 '{fund_code}' 不是 6 位數字，仍要繼續嗎？"):
                return

        try:
            rf = float(self.rf_var.get())
        except ValueError:
            messagebox.showerror("錯誤", "無風險利率必須是數字。")
            return

        benchmark = self.bench_var.get().strip() or None
        output_dir = Path(self.output_var.get().strip() or "output")

        start_date_raw = self.start_date_var.get().strip()
        start_date = None
        if start_date_raw:
            start_date = self._parse_date(start_date_raw)
            if start_date is None:
                messagebox.showerror(
                    "錯誤",
                    f"起始日期格式無法識別：{start_date_raw}\n\n"
                    f"請使用 YYYY-MM-DD，例如 2023-01-01。\n"
                    f"留空則從基金成立日開始追蹤。"
                )
                return

        cfg = {
            "risk_free": rf,
            "benchmark": benchmark,
            "benchmark_market": self.bench_market_var.get() or "auto",
            "start_date": start_date,
        }

        self.result_listbox.delete(0, "end")
        self.result_files = {}
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self.open_all_btn.configure(state="disabled")
        self.open_dir_btn.configure(state="disabled")
        self.qdii_btn.configure(state="disabled")
        self._qdii_estimate = None
        self.run_btn.configure(state="disabled")
        self.status_var.set(f"正在分析 {fund_code} ...")
        self.is_running = True

        try:
            self.worker.submit(
                _analysis_job, fund_code, output_dir, cfg,
                self.ignore_cache_var.get(),
                on_result=self._on_worker_result,
            )
        except RuntimeError as exc:
            self.is_running = False
            self.run_btn.configure(state="normal")
            self.status_var.set("分析未啟動")
            messagebox.showwarning("提示", str(exc))
            return
        self._worker = self.worker.thread

    def _on_worker_result(self, ok, payload):
        """Main thread: turn the worker's terminal result into UI state."""
        if ok:
            self._on_analysis_done(payload, None)
        else:
            self._on_analysis_done(None, payload)

    @staticmethod
    def _parse_date(s: str):
        """解析日期字符串，支持多种常见格式。失败返回 None。"""
        s = s.strip()
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    def _on_analysis_done(self, summary, error):
        self.is_running = False
        self.run_btn.configure(state="normal")

        if error:
            self.status_var.set("分析失敗")
            messagebox.showerror("錯誤", f"分析失敗：\n\n{error}")
            return

        if not summary.get("success", False):
            msg = summary.get("error") or "未知原因"
            self.status_var.set(f"分析失敗：{msg}")
            messagebox.showerror("分析失敗", f"基金 {summary.get('fund_code', '?')} 分析未完成。\n\n原因：{msg}")
            return

        out_dir = Path(summary.get("output_dir", "."))
        if not out_dir.exists():
            self.status_var.set("分析完成，但輸出目錄不存在")
            return

        self._output_dir = out_dir
        self.open_dir_btn.configure(state="normal")

        self._qdii_estimate = summary.get("qdii_estimate")
        is_qdii = summary.get("analysis_mode") == "qdii"
        self.qdii_btn.configure(state="normal" if is_qdii else "disabled")

        from fa.viz.master import generate_master_report

        master_path = generate_master_report(summary["fund_code"], out_dir)
        self._master_report_path = master_path

        exts_order = {".html": 0, ".csv": 1, ".json": 2}
        files = sorted(
            [f for f in out_dir.iterdir()
             if f.is_file() and f.suffix.lower() in exts_order],
            key=lambda p: (exts_order[p.suffix.lower()], p.name),
        )

        ignore_list = ["trend.html", "excess_return.html"]

        for f in files:
            if any(x in f.name for x in ignore_list) and f.name != master_path.name:
                continue

            label = f.name
            self.result_files[label] = f
            prefix = "  [HTML] " if f.suffix == ".html" else "  [FILE] "
            self.result_listbox.insert("end", prefix + label)

        n_html = sum(1 for p in self.result_files.values() if p.suffix == ".html")
        for btn in (self.open_all_btn, self.post_market_btn, self.open_dir_btn):
            btn.configure(state="normal")

        self.status_var.set(
            f"分析完成 — {len(self.result_files)} 個文件（{n_html} 個 HTML 報告）"
        )

    # --------------------------------------------------------
    # 事件處理
    # --------------------------------------------------------
    def _on_result_double_click(self, event=None):
        sel = self.result_listbox.curselection()
        if not sel:
            return
        label = self.result_listbox.get(sel[0]).strip()
        for prefix in ("[HTML]", "[FILE]"):
            if label.startswith(prefix):
                label = label[len(prefix):].strip()
        path = self.result_files.get(label)
        if path and path.exists():
            webbrowser.open(path.as_uri())
        else:
            messagebox.showwarning("提示", f"文件不存在：{label}")

    # ---- 頁籤延後建構 -------------------------------------------------
    def _ensure_tab(self, key: str) -> None:
        """Build a tab's content the first time it is shown (E-02a)."""
        if key in self._tabs_built:
            return
        builder = self._tab_builders.get(key)
        frame = self._tab_frames.get(key)
        if builder is None or frame is None:
            return
        builder(frame)
        self._tabs_built.add(key)

    def _on_tab_changed(self, _event=None):
        try:
            selected = str(self.notebook.select())
        except Exception:
            return
        matched = next(
            (key for key, frame in self._tab_frames.items()
             if str(frame) == selected),
            None,
        )
        if matched is None:
            return
        self._ensure_tab(matched)
        if matched != "estimation" or getattr(self, "_closed", False):
            return
        if not self._history_loaded:
            self.refresh_estimation_history()

    def _build_reports_tab(self, parent: ttk.Frame):
        card = ttk.LabelFrame(parent, text=" 報告與歷史 ", padding=16)
        card.pack(fill="both", expand=True, padx=16, pady=16)
        ttk.Label(
            card,
            text=("檢視已保存的 QDII 估算與策略回測紀錄，預覽渲染後的 Markdown 報告，"
                  "並匯出到 output/reports。\n\n"
                  "報告內容一律讀自已保存的資料，不會重新估算或重跑回測。"),
            justify="left", foreground="#555555",
        ).pack(anchor="w")
        ttk.Button(card, text="開啟歷史視窗",
                   command=self.open_history_window).pack(anchor="w", pady=(12, 0))

    # ---- QDII 估算頁籤 -------------------------------------------------
    def _build_backtest_tab(self, parent: ttk.Frame):
        """Stage C: the backtest tab is an entry to the strategy window."""
        card = ttk.LabelFrame(parent, text=" 策略回測 ", padding=16)
        card.pack(fill="both", expand=True, padx=16, pady=16)
        ttk.Label(
            card, justify="left", foreground="#555555",
            text=("設定 StrategySpec 形狀的策略參數，交由 V2.3.2 回測引擎執行。\n"
                  "所有結果面板都讀自已保存的 BacktestRun，不會在 GUI 重新計算。"),
        ).pack(anchor="w")
        ttk.Button(card, text="開啟回測視窗",
                   command=self.open_backtest_window).pack(anchor="w", pady=(12, 0))

    def _build_estimation_tab(self, parent: ttk.Frame):
        from fa.gui.qdii_estimate import HISTORY_COLUMNS

        head = ttk.LabelFrame(parent, text=" 歷史估值紀錄 ", padding=10)
        head.pack(fill="x", padx=12, pady=(8, 4))

        filter_row = ttk.Frame(head)
        filter_row.pack(fill="x", pady=(0, 6))
        ttk.Label(filter_row, text="基金代碼：").pack(side="left")
        self.history_code_var = tk.StringVar(value="")
        ttk.Entry(filter_row, textvariable=self.history_code_var, width=12).pack(
            side="left", padx=(0, 8))
        self.history_btn = ttk.Button(
            filter_row, text="重新整理", command=self.refresh_estimation_history)
        self.history_btn.pack(side="left")
        ttk.Button(
            filter_row, text="開啟即時估算", command=self.open_qdii_window
        ).pack(side="left", padx=(8, 0))
        self.history_status = ttk.Label(filter_row, text="", foreground="#777777")
        self.history_status.pack(side="right")

        keys = tuple(key for key, _, _ in HISTORY_COLUMNS)
        self.history_tree = ttk.Treeview(
            head, columns=keys, show="headings", height=9)
        for key, heading, width in HISTORY_COLUMNS:
            self.history_tree.heading(key, text=heading)
            anchor = "w" if key == "status" else "center"
            self.history_tree.column(key, width=width, anchor=anchor)
        self.history_tree.pack(fill="both", expand=True)
        self.history_tree.bind("<Double-1>", lambda _event: self.open_estimate_detail())

        # Loading is triggered by selecting the tab (and by 重新整理); the tab
        # must not stay blank until a button is pressed (B-3 review D5).  The
        # notebook binding itself is installed once in _build_ui (E-02a).
        actions = ttk.Frame(parent)
        actions.pack(fill="x", padx=12)
        ttk.Button(actions, text="載入選取的紀錄",
                   command=self.open_estimate_detail).pack(side="left")

    def refresh_estimation_history(self, *_args):
        if self._closed or self.history_worker.is_running:
            return
        self.history_status.configure(text="讀取中…")
        self.history_worker.submit(
            _history_job,
            self.history_code_var.get().strip(),
            on_result=self._on_history_result,
        )

    def _on_history_result(self, ok, payload):
        if self._closed:
            return
        if not ok:
            self.history_status.configure(text=f"讀取失敗：{payload}")
            return
        self._history_loaded = True
        self._history_rows = list(payload or [])
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        from fa.gui.qdii_estimate import history_rows

        for row, raw in zip(history_rows(self._history_rows), self._history_rows):
            self.history_tree.insert("", "end", values=row, iid=str(raw.get("run_id")))
        if self._history_rows:
            self.history_status.configure(text=f"{len(self._history_rows)} 筆紀錄")
        else:
            self.history_status.configure(text="尚無估值紀錄")

    def open_estimate_detail(self):
        selection = self.history_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "請先選取一筆估值紀錄。")
            return
        run_id = str(selection[0])
        existing = self.windows.get("qdii_estimate")
        if existing is not None:
            existing.load_stored(run_id)
            existing.lift()
            return
        window = self.windows.open(
            "qdii_estimate",
            fund_code=self.history_code_var.get().strip() or "—",
            output_dir=self._output_dir,
            estimate=None,
            mode="stored",
            run_id=run_id,
            on_saved=self.refresh_estimation_history,
        )
        self._qdii_window = window

    # ---- 子視窗 -------------------------------------------------------
    def _make_qdii_window(self, parent, *, fund_code, output_dir, estimate,
                          mode="live", run_id=None, on_saved=None):
        from fa.gui.qdii_estimate import QdiiEstimateWindow

        return QdiiEstimateWindow(
            parent, fund_code, output_dir, estimate=estimate,
            on_close=self._on_qdii_window_closed,
            mode=mode, run_id=run_id, on_saved=on_saved,
        )

    def _make_backtest_window(self, parent):
        from fa.gui.backtest_window import BacktestWindow

        return BacktestWindow(parent, on_close=self._on_backtest_window_closed)

    def _on_backtest_window_closed(self):
        self.windows.remove("backtest")

    def open_backtest_window(self):
        self.windows.open("backtest")

    def open_history_window(self):
        self.windows.open("history")

    def _make_history_window(self, parent, **kwargs):
        from fa.gui.history_window import HistoryWindow

        window = HistoryWindow(
            parent,
            repository_path=kwargs.get("repository_path"),
            save_dir=kwargs.get("save_dir", self._output_dir),
            on_close=self._on_history_closed,
        )
        self._history_window = window
        return window

    def _on_history_closed(self):
        self._history_window = None
        try:
            self.windows.remove("history")
        except Exception:
            pass

    def _make_post_market_window(self, parent, *, fund_code, output_dir):
        from fa.gui.post_market import PostMarketWindow

        return PostMarketWindow(
            parent, fund_code, output_dir,
            on_close=self._on_post_market_closed,
        )

    def open_post_market_window(self):
        if not self._output_dir or not self._output_dir.exists():
            messagebox.showwarning("提示", "请先运行一次分析生成持仓数据。")
            return

        fund_code = self.code_var.get().strip()
        if not fund_code:
            return

        window = self.windows.open(
            "post_market", fund_code=fund_code, output_dir=self._output_dir)
        self._post_market_window = window

    def _on_post_market_closed(self):
        self._post_market_window = None
        self.windows.remove("post_market")

    def open_qdii_window(self):
        """打開 QDII 淨值估算窗口，固定走 live 路徑（重新估算可用）。"""
        if not self._output_dir or not self._output_dir.exists():
            messagebox.showwarning("提示", "請先執行一次分析。")
            return

        fund_code = self.code_var.get().strip()
        if not fund_code:
            return

        existing = self.windows.get("qdii_estimate")
        if existing is not None:
            # A re-used window may still be in handover/stored mode; the live
            # button must mean "estimate now", not "look at the old view".
            starter = getattr(existing, "start_live", None)
            if callable(starter):
                starter()
            existing.lift()
            self._qdii_window = existing
            return

        window = self.windows.open(
            "qdii_estimate", fund_code=fund_code, output_dir=self._output_dir,
            estimate=None, mode="live", run_id=None,
            on_saved=self.refresh_estimation_history)
        self._qdii_window = window

    def open_qdii_handover(self):
        """Open the analysis handover view (never claims persistence)."""
        if not self._qdii_estimate:
            messagebox.showinfo("提示", "本次分析沒有 QDII 估值結果可交接。")
            return
        if not self._output_dir or not self._output_dir.exists():
            messagebox.showwarning("提示", "請先執行一次分析。")
            return
        fund_code = self.code_var.get().strip() or str(
            self._qdii_estimate.get("fund_code") or "—")

        existing = self.windows.get("qdii_estimate")
        if existing is not None:
            # The open window may be live/stored; the analysis button means
            # "show the handover record", so switch it instead of lifting a
            # stale view (B-3 review: handover was unreachable).
            shower = getattr(existing, "show_handover", None)
            if callable(shower):
                shower(self._qdii_estimate)
            existing.lift()
            self._qdii_window = existing
            return

        window = self.windows.open(
            "qdii_estimate", fund_code=fund_code, output_dir=self._output_dir,
            estimate=self._qdii_estimate, mode="handover", run_id=None,
            on_saved=self.refresh_estimation_history)
        self._qdii_window = window

    def _on_qdii_window_closed(self):
        self._qdii_window = None
        self.windows.remove("qdii_estimate")

    def open_master_report(self):
        path = self._master_report_path
        if path is None or not path.exists():
            messagebox.showwarning("提示", "综合报告尚未生成。")
            return

        try:
            import webview
            webview.create_window(
                title="Fund Analyzer 综合报告",
                url=self._master_report_path.as_uri(),
                width=1280,
                height=900,
                background_color="#f0f2f5",
            )
            webview.start()
        except ImportError as e:
            log.warning("pywebview 不可用，回退到瀏覽器: %s", e)
            webbrowser.open(self._master_report_path.as_uri())
        except Exception as e:
            log.exception("pywebview 打開失敗")
            messagebox.showerror(
                "错误",
                f"无法打开视窗: {e}\n将回退到默认浏览器。",
            )
            webbrowser.open(self._master_report_path.as_uri())

    def open_output_dir(self):
        if self._output_dir and self._output_dir.exists():
            webbrowser.open(self._output_dir.as_uri())
