# -*- coding: utf-8 -*-
"""GUI entry point: theme, global exception hooks and the Tk main loop.

``fund_analyzer_gui.py`` stays behind as a thin, backwards compatible
launcher (it also owns the pythonw stdout/stderr guard, which must run
before anything is imported). Everything here assumes std streams are
already usable.
"""
from __future__ import annotations

import faulthandler
import sys

import tkinter as tk
from tkinter import ttk

from fa.config import PROJECT_ROOT
from fa.gui.app import FundAnalyzerGUI
from fa.gui.crash import format_exception, report_crash, show_error
from fa.utils.logging_setup import get_logger, setup_logging
from fa.utils.net import apply_default_timeout

log = get_logger(__name__)

THEME_PREFERENCE = ("vista", "winnative", "clam", "default")
FAULT_LOG = PROJECT_ROOT / "fatal.log"


def _enable_faulthandler() -> None:
    """把硬崩潰（SIGSEGV/SIGABRT 等）的 Python 堆棧寫進 fatal.log。

    純 Python 異常由 excepthook/crash.py 處理；但原生庫（tcl、curl、
    numpy 等）觸發的 abort 會直接殺死進程，只有 faulthandler 能留下線索。
    """
    if faulthandler.is_enabled():
        return
    try:
        stream = open(FAULT_LOG, "a", encoding="utf-8")
    except OSError:
        stream = sys.stderr
    if stream is None:  # pythonw 且 sys.stderr 不可用
        return
    faulthandler.enable(stream, all_threads=True)
    log.debug("faulthandler 已啟用 → %s", FAULT_LOG)


def _report_callback_exception(exc, val, tb) -> None:
    """Tk 默認回調會 print 到 stderr；pythonw 下那會讓 mainloop 二次崩潰。"""
    report_crash(format_exception(exc, val, tb),
                 source="Tk callback", title="界面回调异常")


def _apply_theme(root: tk.Tk) -> None:
    try:
        style = ttk.Style(root)
        for theme in THEME_PREFERENCE:
            if theme in style.theme_names():
                style.theme_use(theme)
                break
    except Exception:
        log.debug("主題設置失敗", exc_info=True)


def main() -> None:
    _enable_faulthandler()
    setup_logging()
    apply_default_timeout()
    sys.excepthook = show_error

    root = tk.Tk()
    root.report_callback_exception = _report_callback_exception
    _apply_theme(root)

    app = FundAnalyzerGUI(root)
    # 主窗口關閉 = 結束整個監控生命週期（取消 after 定時器、等待工作線程退出）
    root.protocol("WM_DELETE_WINDOW", app.shutdown)
    root.mainloop()
