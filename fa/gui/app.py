"""Main Tkinter window: run an analysis, then expose the generated reports."""
from __future__ import annotations

import logging
import queue
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from fa.gui.crash import show_error
from fa.gui.post_market import PostMarketWindow
from fa.pipeline import analyze_fund
from fa.utils.logging_setup import get_logger
from fa.viz.master import generate_master_report

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

class FundAnalyzerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Fund Analyzer — 基金分析工具")
        self.root.geometry("860x680")
        self.root.minsize(720, 560)

        self.log_queue = queue.Queue()
        self.result_queue = queue.Queue()   # 工作線程 -> 主線程 的結果通道
        self.is_running = False
        self.result_files = {}       # {filename: Path}
        self._output_dir = None
        self._master_report_path = None
        self._post_market_window = None

        # 生命週期：shutdown() 之後所有 after() 定時器都不再重新排隊
        self._closed = False
        self._worker = None
        self._log_poll_id = None
        self._result_poll_id = None

        self._build_ui()
        self._setup_logging()

    # --------------------------------------------------------
    # UI 佈局
    # --------------------------------------------------------
    def _build_ui(self):
        # ---- 參數區 ----
        param = ttk.LabelFrame(self.root, text=" 分析參數 ", padding=10)
        param.pack(fill="x", padx=10, pady=(10, 5))

        # row 0 (移除 AUM，调整排版)
        ttk.Label(param, text="基金代碼:").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=4)
        self.code_var = tk.StringVar()
        ttk.Entry(param, textvariable=self.code_var, width=14).grid(row=0, column=1, sticky="w", padx=(0, 16), pady=4)

        ttk.Label(param, text="無風險利率:").grid(row=0, column=2, sticky="w", padx=(0, 4), pady=4)
        self.rf_var = tk.StringVar(value="0.02")
        ttk.Entry(param, textvariable=self.rf_var, width=8).grid(row=0, column=3, sticky="w", pady=4)

        # row 1
        ttk.Label(param, text="基準指數:").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=4)
        self.bench_var = tk.StringVar(value=".NDX")
        ttk.Entry(param, textvariable=self.bench_var, width=14).grid(row=1, column=1, sticky="w", padx=(0, 16), pady=4)

        ttk.Label(param, text="輸出根目錄:").grid(row=1, column=2, sticky="w", padx=(0, 4), pady=4)
        self.output_var = tk.StringVar(value="output")
        ttk.Entry(param, textvariable=self.output_var, width=30).grid(
            row=1, column=3, columnspan=3, sticky="we", padx=(0, 0), pady=4)

        # row 2：起始日期
        ttk.Label(param, text="起始日期:").grid(row=2, column=0, sticky="w", padx=(0, 4), pady=4)
        self.start_date_var = tk.StringVar()   # 留空 = 从成立日追踪
        ttk.Entry(param, textvariable=self.start_date_var, width=14).grid(
            row=2, column=1, sticky="w", padx=(0, 16), pady=4)

        ttk.Label(param,
                text="格式 YYYY-MM-DD，留空 = 從基金成立日追蹤",
                foreground="#888888").grid(
            row=2, column=2, columnspan=4, sticky="w", padx=(0, 0), pady=4)
        
        # row 3：基准市场
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

        # 讓輸出目錄這一列能自動伸展
        param.columnconfigure(3, weight=1)

        # ---- 按鈕列 ---- (确保这里定义了 btns)
        btns = ttk.Frame(self.root)
        btns.pack(fill="x", padx=10, pady=5)

        self.run_btn = ttk.Button(btns, text="開始分析", command=self.start_analysis)
        self.run_btn.pack(side="left", padx=(0, 6))

        # 新增：盘后监控按钮
        self.post_market_btn = ttk.Button(btns, text="盘后监控(实时预估)", 
                                          command=self.open_post_market_window, state="disabled")
        self.post_market_btn.pack(side="left", padx=(0, 6))

        self.open_all_btn = ttk.Button(btns, text="📊 打开综合报告(视窗)",
                                        command=self.open_master_report, state="disabled")
        self.open_all_btn.pack(side="left", padx=(0, 6))

        self.open_dir_btn = ttk.Button(btns, text="打開輸出目錄",
                                        command=self.open_output_dir, state="disabled")
        self.open_dir_btn.pack(side="left", padx=(0, 6))

        self.ignore_cache_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btns, text="忽略緩存（強制刷新）",
                         variable=self.ignore_cache_var).pack(side="right")

        # ---- 日誌區 ----
        log_frame = ttk.LabelFrame(self.root, text=" 運行日誌 ", padding=4)
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=14, state="disabled",
            font=("Consolas", 9), background="#1e1e1e", foreground="#d4d4d4",
            insertbackground="#d4d4d4", wrap="word",
        )
        self.log_text.pack(fill="both", expand=True)

        # ---- 結果列表 ----
        result_frame = ttk.LabelFrame(self.root, text=" 生成的文件（雙擊打開） ", padding=4)
        result_frame.pack(fill="both", expand=False, padx=10, pady=(5, 10))

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

        # ---- 狀態列 ----
        self.status_var = tk.StringVar(value="就緒")
        status = ttk.Label(self.root, textvariable=self.status_var,
                            relief="sunken", anchor="w", padding=(6, 2))
        status.pack(fill="x", side="bottom")

    # --------------------------------------------------------
    # logging 設置
    # --------------------------------------------------------
    def _setup_logging(self):
        # 掛在 root logger 上：分析模組現在按 fa.* 命名空間記日誌，
        # 只掛 "fund_analyzer" 會讓整個日誌面板收不到任何訊息。
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)

        # 避免重複添加 handler（setup_logging 也會在 root 上掛檔案 / 控制台）
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
        """關閉窗口：停掉定時器、等後台分析線程退出，再銷毀 Tk。"""
        self._closed = True
        for after_id in (self._log_poll_id, self._result_poll_id):
            if after_id:
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    log.debug("取消定時器失敗: %s", after_id, exc_info=True)
        self._log_poll_id = self._result_poll_id = None

        if self._post_market_window is not None:
            try:
                self._post_market_window.close()
            except Exception:
                log.debug("關閉盤後監控窗口失敗", exc_info=True)
            self._post_market_window = None

        if self._worker is not None and self._worker.is_alive():
            # 分析線上程裡做的是網絡 + 計算，daemon=True 保證即使超時也能退出
            self._worker.join(timeout=2.0)
            if self._worker.is_alive():
                log.warning("後台分析線程在 2 秒內未結束，強制關閉窗口")

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

        # 解析起始日期（支持多种格式）
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
            "risk_free":  rf,
            "benchmark":  benchmark,
            "benchmark_market": self.bench_market_var.get() or "auto",
            "start_date": start_date,
        }
        
        # 重置 UI
        self.result_listbox.delete(0, "end")
        self.result_files = {}
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self.open_all_btn.configure(state="disabled")
        self.open_dir_btn.configure(state="disabled")
        self.run_btn.configure(state="disabled")
        self.status_var.set(f"正在分析 {fund_code} ...")
        self.is_running = True

        # 後台線程執行（避免卡 UI）
        self._worker = threading.Thread(
            target=self._run_analysis,
            args=(fund_code, output_dir, cfg, self.ignore_cache_var.get()),
            daemon=True,
        )
        self._worker.start()

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

    def _run_analysis(self, fund_code, output_dir, cfg, no_cache):
        """後台線程：只負責計算與下載，絕不直接操作 Tkinter 控件。"""
        try:
            summary = analyze_fund(
                fund_code=fund_code,
                base_output=output_dir,
                cfg=cfg,
                show=False,
                use_cache=not no_cache,
            )
            self.result_queue.put(("success", summary))
        except Exception as e:
            log.exception("分析线程异常")
            self.result_queue.put(("error", e))

    def _on_analysis_done(self, summary, error):
        self.is_running = False
        self.run_btn.configure(state="normal")

        # 情况 1：线程里抛了异常
        if error:
            self.status_var.set("分析失敗")
            messagebox.showerror("錯誤", f"分析失敗：\n\n{error}")
            return

        # 情况 2：函数内部提前终止
        if not summary.get("success", False):
            msg = summary.get("error") or "未知原因"
            self.status_var.set(f"分析失敗：{msg}")
            messagebox.showerror("分析失敗", f"基金 {summary.get('fund_code', '?')} 分析未完成。\n\n原因：{msg}")
            return

        # 情况 3：真正成功
        out_dir = Path(summary.get("output_dir", "."))
        if not out_dir.exists():
            self.status_var.set("分析完成，但輸出目錄不存在")
            return

        self._output_dir = out_dir
        self.open_dir_btn.configure(state="normal")

        # 生成汇总报告
        master_path = generate_master_report(summary["fund_code"], out_dir)
        self._master_report_path = master_path

        # 收集文件（按類型 + 名稱排序）
        exts_order = {".html": 0, ".csv": 1, ".json": 2}
        files = sorted(
            [f for f in out_dir.iterdir()
             if f.is_file() and f.suffix.lower() in exts_order],
            key=lambda p: (exts_order[p.suffix.lower()], p.name),
        )

        # 忽略不需要展示的特定文件
        ignore_list = ["trend.html", "excess_return.html"]

        for f in files:
            if any(x in f.name for x in ignore_list) and f.name != master_path.name:
                continue
            
            label = f.name
            self.result_files[label] = f
            prefix = "  [HTML] " if f.suffix == ".html" else "  [FILE] "
            self.result_listbox.insert("end", prefix + label)

        # 分析完成後才啟用依賴結果的三個按鈕
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
        # 去掉前綴 emoji
        for prefix in ("[HTML]", "[FILE]"):
            if label.startswith(prefix):
                label = label[len(prefix):].strip()
        path = self.result_files.get(label)
        if path and path.exists():
            webbrowser.open(path.as_uri())
        else:
            messagebox.showwarning("提示", f"文件不存在：{label}")

    def open_post_market_window(self):
        if not self._output_dir or not self._output_dir.exists():
            messagebox.showwarning("提示", "请先运行一次分析生成持仓数据。")
            return

        # 同一時間只保留一個監控窗口：重複點擊時把已有窗口提到前面，
        # 而不是再開一個 60 秒輪詢的線程。
        existing = self._post_market_window
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return

        fund_code = self.code_var.get().strip()
        if not fund_code:
            return

        self._post_market_window = PostMarketWindow(
            self.root, fund_code, self._output_dir,
            on_close=self._on_post_market_closed)

    def _on_post_market_closed(self):
        self._post_market_window = None

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
