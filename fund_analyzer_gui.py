#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fund Analyzer GUI — 圖形界面版

依賴同目錄下的 fa/ 套件（Phase 3 模組拆分後，本文件僅為啟動器）。

Usage:
    python fund_analyzer_gui.py
"""
import os
import sys
from pathlib import Path

# ============================================================
# 關鍵：pythonw.exe 下 sys.stdout / sys.stderr 為 None
# 任何 print / traceback.print_exc / Tkinter 默認回調處理
# 都會拋 AttributeError，導致主線程或 mainloop 崩潰（閃退）
# 這裡統一兜底到一個日誌文件，便於排查
# ============================================================
if sys.stdout is None or sys.stderr is None:
    _debug_log = Path(__file__).resolve().parent / "gui_debug.log"
    try:
        _fh = open(_debug_log, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _fh
        if sys.stderr is None:
            sys.stderr = _fh
    except OSError:
        # 最後兜底：寫入 devnull，保證不是 None
        _devnull = open(os.devnull, "w", encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = _devnull
        if sys.stderr is None:
            sys.stderr = _devnull

from fa.gui.launcher import main  # noqa: E402

if __name__ == "__main__":
    main()
