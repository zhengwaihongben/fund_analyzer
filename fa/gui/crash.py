# -*- coding: utf-8 -*-
"""統一崩潰處理：日誌 -> crash.log -> 錯誤對話框。

GUI 的頂層 excepthook、Tk 回調異常、以及各處的 try/except 都走這裡，
避免同一段「log + 寫檔 + 彈窗」在三個地方各寫一遍、並且各自用
``except Exception: pass`` 把線索吞掉。
"""
from __future__ import annotations

import sys
import traceback

from tkinter import messagebox

from fa.config import PROJECT_ROOT
from fa.utils.logging_setup import get_logger

log = get_logger(__name__)

CRASH_LOG = PROJECT_ROOT / "crash.log"


def format_exception(exc_type, exc_value, exc_traceback) -> str:
    return "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))


def write_crash_log(text: str, source: str = "") -> None:
    """崩潰信息落盤；失敗只記 debug 日誌，絕不讓處理器自己再拋異常。"""
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            if source:
                f.write(f"[{source}]\n")
            f.write(text.rstrip() + "\n\n")
    except OSError:
        log.debug("無法寫入 crash.log", exc_info=True)


def report_crash(text: str, source: str = "", title: str = "程序发生异常") -> None:
    """記錄 + 落盤 + 彈窗，三步互不影響。"""
    log.critical("未捕獲異常 (%s):\n%s", source or "-", text)
    write_crash_log(text, source)
    try:
        messagebox.showerror(title, f"发生错误:\n\n{text[-1000:]}")
    except Exception:
        log.debug("無法顯示錯誤對話框", exc_info=True)


def show_error(exc_type, exc_value, exc_traceback) -> None:
    """``sys.excepthook`` 的實現：記錄之後退出。"""
    report_crash(format_exception(exc_type, exc_value, exc_traceback),
                 source="top-level")
    sys.exit(1)
