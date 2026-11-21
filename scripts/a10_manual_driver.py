# -*- coding: utf-8 -*-
"""A-10 driver: exercise the real Tk window while a worker runs.

It performs the mechanical parts of the manual procedure (real window, real
worker threads, programmatic drag/resize, tab switches, log scrolling, a real
`000369` analysis, closing during an active task) and measures how late the
25 ms main-thread heartbeat runs.  It cannot judge visual smoothness.
"""
from __future__ import annotations

import json
import logging
import statistics
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "development" / "v24_evidence"

HEARTBEAT_MS = 25
SLOW_SECONDS = 8.0
ANALYSIS_TIMEOUT = 240.0
OUTDIR = ROOT / "_a10_out"

report: dict = {
    "phases": [],
    "heartbeat_count": 0,
    "heartbeat_max_lateness_ms": 0.0,
    "heartbeat_p95_lateness_ms": 0.0,
    "screenshot": None,
    "result_items": 0,
    "report_path": None,
    "log_lines_after_analysis": 0,
    "shutdown_seconds": None,
    "worker_alive_after_close": None,
    "errors": [],
}

lateness: list[float] = []
_stop = threading.Event()


def _slow_job(reporter, seconds):
    """Module-level: logs progress so the GUI log pane must keep appending."""
    log = logging.getLogger("fa.a10")
    end = time.monotonic() + float(seconds)
    ticks = 0
    while time.monotonic() < end:
        ticks += 1
        log.info("A-10 背景任務心跳 #%d（%.1fs）", ticks, time.monotonic() - (end - seconds))
        reporter.report(ticks)
        time.sleep(0.2)
    log.info("A-10 背景任務完成，共 %d 次", ticks)
    return ticks


def _grab(app):
    try:
        from PIL import ImageGrab  # noqa: PLC0415 - optional dependency
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"screenshot unavailable: {exc}")
        return
    root = app.root
    root.update_idletasks()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    try:
        image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"screenshot failed: {exc}")
        return
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    target = EVIDENCE / "A-10_running.png"
    image.save(target)
    report["screenshot"] = str(target)


def main() -> int:
    from fa.gui.app import FundAnalyzerGUI

    root = tk.Tk()
    app = FundAnalyzerGUI(root)
    started = time.monotonic()
    next_tick = [started]

    def heartbeat():
        now = time.monotonic()
        delay = (now - next_tick[0]) * 1000.0 - HEARTBEAT_MS
        lateness.append(max(delay, 0.0))
        report["heartbeat_count"] += 1
        next_tick[0] = now + HEARTBEAT_MS / 1000.0
        if not _stop.is_set():
            root.after(HEARTBEAT_MS, heartbeat)

    def finish(code=0):
        _stop.set()
        if lateness:
            ordered = sorted(lateness)
            report["heartbeat_max_lateness_ms"] = round(max(lateness), 2)
            report["heartbeat_p95_lateness_ms"] = round(
                ordered[int(len(ordered) * 0.95) - 1], 2
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if code:
            sys.exit(code)

    def phase_slow():
        report["phases"].append({"name": "slow-task", "started": time.monotonic() - started})
        done = {}

        def on_result(ok, payload):
            done["ok"] = ok
            done["payload"] = payload
            report["phases"].append(
                {"name": "slow-task", "finished": time.monotonic() - started, "ok": ok}
            )
            root.after(100, phase_analysis)

        app.worker.submit(_slow_job, SLOW_SECONDS, on_result=on_result)
        report["thread_target"] = getattr(app.worker.thread, "_target", None).__name__
        report["thread_holds_self"] = getattr(app.worker.thread._target, "__self__", None) is not None

        def drag_resize():
            for geometry in ("900x720+120+80", "860x680+240+140", "1000x760+100+60"):
                app.root.geometry(geometry)
                app.root.update_idletasks()
            report["geometry_ok"] = True

        def type_text():
            app.code_var.set("000001")
            observed = app.code_var.get()
            app.code_var.set("000369")
            report["typing_ok"] = observed == "000001" and app.code_var.get() == "000369"

        def switch_tabs():
            order = [app.estimation_tab, app.backtest_tab, app.reports_tab, app.analysis_tab]
            for tab in order:
                app.notebook.select(tab)
                app.root.update_idletasks()
            report["tabs_ok"] = app.notebook.index(app.notebook.select()) == 0

        def scroll_log():
            app.log_text.yview_moveto(0.0)
            app.log_text.yview_moveto(1.0)
            app.root.update_idletasks()
            report["scroll_ok"] = True

        def snapshot():
            app.status_var.set("A-10：背景任務執行中")
            _grab(app)
            report["log_chars_during_run"] = len(app.log_text.get("1.0", "end"))

        for delay, func in (
            (600, drag_resize),
            (1200, type_text),
            (1800, switch_tabs),
            (2400, scroll_log),
            (3000, snapshot),
        ):
            root.after(delay, func)

    def phase_analysis():
        report["phases"].append({"name": "real-analysis", "started": time.monotonic() - started})
        app.code_var.set("000369")
        app.output_var.set(str(OUTDIR))
        app.start_date_var.set("2025-01-01")
        OUTDIR.mkdir(exist_ok=True)
        deadline = time.monotonic() + ANALYSIS_TIMEOUT

        def poll():
            if not app.is_running:
                report["phases"].append(
                    {
                        "name": "real-analysis",
                        "finished": time.monotonic() - started,
                        "status": app.status_var.get(),
                    }
                )
                report["result_items"] = app.result_listbox.size()
                report["report_path"] = str(getattr(app, "_master_report_path", "") or "")
                report["log_lines_after_analysis"] = len(app.log_text.get("1.0", "end").splitlines())
                root.after(200, phase_close)
                return
            if time.monotonic() > deadline:
                report["errors"].append("analysis timeout")
                root.after(200, phase_close)
                return
            root.after(200, poll)

        try:
            app.start_analysis()
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"analysis failed to start: {exc}")
            root.after(200, phase_close)
            return
        report["analysis_worker_target"] = getattr(app.worker.thread, "_target", None).__name__
        root.after(200, poll)

    def phase_close():
        report["phases"].append({"name": "close-during-run", "started": time.monotonic() - started})

        def close_now():
            app.worker.submit(_slow_job, 10.0)
            root.after(1200, do_close)

        def do_close():
            begin = time.monotonic()
            app.shutdown()
            elapsed = time.monotonic() - begin
            report["shutdown_seconds"] = round(elapsed, 3)
            thread = app.worker.thread
            report["worker_alive_after_close"] = bool(thread and thread.is_alive())
            report["phases"].append(
                {"name": "close-during-run", "finished": time.monotonic() - started}
            )
            finish(0)

        close_now()

    root.after(HEARTBEAT_MS, heartbeat)
    root.after(300, phase_slow)
    root.after(400_000, lambda: (report["errors"].append("watchdog"), finish(1)))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
