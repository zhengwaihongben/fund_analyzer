#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fund Analyzer GUI — 圖形界面版

依賴同目錄下的 fund_analyzer.py

Usage:
    python fund_analyzer_gui.py
"""
import queue
import logging
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime
import time

# 導入核心邏輯
from fund_analyzer import analyze_fund


# ============================================================
# 自定義 log handler，把日誌轉發到 queue
# ============================================================
class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        try:
            self.log_queue.put(self.format(record))
        except Exception:
            pass


# ============================================================
# GUI
# ============================================================
class FundAnalyzerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Fund Analyzer — 基金分析工具")
        self.root.geometry("860x680")
        self.root.minsize(720, 560)

        self.log_queue = queue.Queue()
        self.is_running = False
        self.result_files = {}       # {filename: Path}
        self._output_dir = None

        self._build_ui()
        self._setup_logging()

    # --------------------------------------------------------
    # UI 佈局
    # --------------------------------------------------------
    def _build_ui(self):
        # ---- 參數區 ----
        param = ttk.LabelFrame(self.root, text=" 分析參數 ", padding=10)
        param.pack(fill="x", padx=10, pady=(10, 5))

        # row 0
        ttk.Label(param, text="基金代碼:").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=4)
        self.code_var = tk.StringVar()
        ttk.Entry(param, textvariable=self.code_var, width=14).grid(row=0, column=1, sticky="w", padx=(0, 16), pady=4)

        ttk.Label(param, text="AUM (億元):").grid(row=0, column=2, sticky="w", padx=(0, 4), pady=4)
        self.aum_var = tk.StringVar()
        ttk.Entry(param, textvariable=self.aum_var, width=10).grid(row=0, column=3, sticky="w", padx=(0, 16), pady=4)

        ttk.Label(param, text="無風險利率:").grid(row=0, column=4, sticky="w", padx=(0, 4), pady=4)
        self.rf_var = tk.StringVar(value="0.02")
        ttk.Entry(param, textvariable=self.rf_var, width=8).grid(row=0, column=5, sticky="w", pady=4)

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

        # 讓輸出目錄這一列能自動伸展
        param.columnconfigure(3, weight=1)

        # ---- 按鈕列 ----
        btns = ttk.Frame(self.root)
        btns.pack(fill="x", padx=10, pady=5)

        self.run_btn = ttk.Button(btns, text="▶  開始分析", command=self.start_analysis)
        self.run_btn.pack(side="left", padx=(0, 6))

        self.open_all_btn = ttk.Button(btns, text="🌐  打開所有報告",
                                        command=self.open_all_reports, state="disabled")
        self.open_all_btn.pack(side="left", padx=(0, 6))

        self.open_dir_btn = ttk.Button(btns, text="📂  打開輸出目錄",
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
        root_logger = logging.getLogger("fund_analyzer")
        # 避免重複添加 handler
        for h in root_logger.handlers[:]:
            root_logger.removeHandler(h)
        root_logger.setLevel(logging.INFO)

        handler = QueueHandler(self.log_queue)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-7s %(message)s",
            datefmt="%H:%M:%S",
        ))
        root_logger.addHandler(handler)

        self.root.after(100, self._poll_log_queue)

    def _poll_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

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
            aum = float(self.aum_var.get()) if self.aum_var.get().strip() else None
        except ValueError:
            messagebox.showerror("錯誤", "AUM 必須是數字（可留空）。")
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
            "aum":        aum,
            "aum_date":   datetime.now().strftime("%Y-%m-%d"),
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
        threading.Thread(
            target=self._run_analysis,
            args=(fund_code, output_dir, cfg, self.ignore_cache_var.get()),
            daemon=True,
        ).start()

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
        try:
            summary = analyze_fund(
                fund_code=fund_code,
                base_output=output_dir,
                cfg=cfg,
                show=False,
                use_cache=not no_cache,
            )
            self.root.after(0, self._on_analysis_done, summary, None)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.root.after(0, self._on_analysis_done, None, e)

    def _on_analysis_done(self, summary, error):
        self.is_running = False
        self.run_btn.configure(state="normal")

        if error:
            self.status_var.set("分析失敗")
            messagebox.showerror("錯誤", f"分析失敗：\n\n{error}")
            return

        out_dir = Path(summary.get("output_dir", "."))
        if not out_dir.exists():
            self.status_var.set("分析完成，但輸出目錄不存在")
            return

        self._output_dir = out_dir
        self.open_dir_btn.configure(state="normal")

        # 收集文件（按類型 + 名稱排序）
        exts_order = {".html": 0, ".csv": 1, ".json": 2}
        files = sorted(
            [f for f in out_dir.iterdir()
             if f.is_file() and f.suffix.lower() in exts_order],
            key=lambda p: (exts_order[p.suffix.lower()], p.name),
        )

        for f in files:
            label = f.name
            self.result_files[label] = f
            prefix = "  🌐 " if f.suffix == ".html" else "  📄 "
            self.result_listbox.insert("end", prefix + label)

        n_html = sum(1 for p in self.result_files.values() if p.suffix == ".html")
        if n_html:
            self.open_all_btn.configure(state="normal")

        self.status_var.set(
            f"✅ 分析完成 — {len(self.result_files)} 個文件（{n_html} 個 HTML 報告）"
        )
        messagebox.showinfo(
            "完成",
            f"分析完成！\n\n"
            f"輸出目錄：\n{out_dir.resolve()}\n\n"
            f"生成 {len(self.result_files)} 個文件，其中 {n_html} 個 HTML 報告。\n\n"
            f"雙擊列表中的文件即可打開，或點擊「打開所有報告」。"
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
        for prefix in ("🌐", "📄"):
            if label.startswith(prefix):
                label = label[len(prefix):].strip()
        path = self.result_files.get(label)
        if path and path.exists():
            webbrowser.open(path.as_uri())
        else:
            messagebox.showwarning("提示", f"文件不存在：{label}")

    def open_all_reports(self):
        opened = 0
        for p in self.result_files.values():
            if p.suffix == ".html" and p.exists():
                webbrowser.open(p.as_uri())
                opened += 1
        if opened:
            self.status_var.set(f"已打開 {opened} 個 HTML 報告")

    def open_output_dir(self):
        if self._output_dir and self._output_dir.exists():
            webbrowser.open(self._output_dir.as_uri())


# ============================================================
# 入口
# ============================================================
def main():
    root = tk.Tk()

    # 使用較現代的主題（Windows 上可用 vista，其他平台 fallback）
    try:
        style = ttk.Style()
        for theme in ("vista", "winnative", "clam", "default"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
    except Exception:
        pass

    FundAnalyzerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()