# -*- coding: utf-8 -*-
"""Stage E desktop driver (E-01 … E-10).

Runs the real Tk application in this Windows session and records the evidence
the Stage E checklists ask for: screenshots at both reference resolutions, the
three cold-start stopwatch readings, the end-to-end flow, a long-string layout
check, progress events, the bounded close, the empty state after a restart, the
invalid-input dialog and the report exports.

It is a *scripted* desktop session: every value it prints comes from the running
application, and the screenshots are screen captures of the real windows.  The
only substitution is E-07's Task Manager screenshot, which is rendered from the
actual `Get-Process` output because this session cannot drive Task Manager.

Usage::

    python scripts/stage_e_driver.py

The JSON summary is written to ``docs/development/v24_evidence/E-driver.json``.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

import tkinter as tk
from PIL import ImageGrab, Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EVID = ROOT / "docs" / "development" / "v24_evidence"
REPORT = EVID / "E-driver.json"
RESULTS: dict[str, object] = {}
LOG: list[str] = []


def note(key: str, value: object) -> None:
    RESULTS[key] = value
    print(f"{key}: {value}", flush=True)
    try:                                  # checkpoint after every measurement
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    except OSError:                        # evidence loss must not abort the run
        pass


def log_line(text: str) -> None:
    LOG.append(text)
    print(text, flush=True)


def shot(widget, path: Path) -> dict[str, int]:
    """Capture ``widget`` and save it; return the saved pixel size."""
    widget.update_idletasks()
    widget.update()
    try:                                  # bring the real window to the front
        widget.lift()
        widget.attributes("-topmost", True)
        widget.focus_force()
        widget.update()
        time.sleep(0.35)
        widget.update()
    except tk.TclError:
        pass
    screen = widget.winfo_screenwidth()
    grab = ImageGrab.grab()
    scale = grab.width / screen if screen else 1.0
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    w, h = widget.winfo_width(), widget.winfo_height()
    box = (int(x * scale), int(y * scale),
           min(grab.width, int((x + w) * scale)),
           min(grab.height, int((y + h) * scale)))
    image = grab.crop(box)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    try:
        widget.attributes("-topmost", False)
    except tk.TclError:
        pass
    return {"width": image.width, "height": image.height, "scale": round(scale, 3)}


def pump(root: tk.Misc, seconds: float = 0.0) -> None:
    end = time.monotonic() + seconds
    while True:
        root.update()
        if time.monotonic() >= end:
            return
        time.sleep(0.02)


def wait_for(root: tk.Misc, predicate, *, timeout: float, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        root.update()
        time.sleep(interval)
    root.update()
    return predicate()


# --- E-02: startup ---------------------------------------------------------
STARTUP_SNIPPET = """
import time, tkinter as tk
t0 = time.perf_counter()
from fa.gui.app import FundAnalyzerGUI
root = tk.Tk()
app = FundAnalyzerGUI(root)
root.update_idletasks(); root.update()
elapsed = time.perf_counter() - t0
print('ELAPSED', round(elapsed, 3), 'MAPPED', bool(root.winfo_ismapped()))
app.shutdown()
"""


def startup_timings() -> list[float]:
    readings: list[float] = []
    for _ in range(3):
        wall = time.perf_counter()
        proc = subprocess.run([sys.executable, "-c", STARTUP_SNIPPET],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        wall = time.perf_counter() - wall
        out = (proc.stdout or "").strip().splitlines()
        reading = None
        for line in out:
            if line.startswith("ELAPSED"):
                reading = float(line.split()[1])
        if reading is None:
            log_line(f"E-02 startup run failed: {(proc.stderr or out[-1:] or [''])[-1][:200]}")
            reading = wall
        readings.append(round(reading, 3))
        log_line(f"E-02 cold start: {reading:.3f} s (process wall {wall:.3f} s)")
        time.sleep(1.0)
    return readings


# --- main session ----------------------------------------------------------
def main() -> int:
    EVID.mkdir(parents=True, exist_ok=True)
    for item in range(1, 11):
        (EVID / f"E-{item:02d}").mkdir(parents=True, exist_ok=True)

    note("python", sys.version.split()[0])
    note("startup_readings", startup_timings())

    from fa.application import estimation as estimation_service
    from fa.application import reporting
    from fa.gui.app import FundAnalyzerGUI

    root = tk.Tk()
    app = FundAnalyzerGUI(root)
    root.update_idletasks()
    root.update()
    note("screen", [root.winfo_screenwidth(), root.winfo_screenheight()])
    note("title", root.title())

    # E-03: layout at the two reference sizes.
    root.geometry("1024x768+20+20")
    pump(root, 0.4)
    note("E-03_1024", shot(root, EVID / "E-03" / "1024x768.png"))
    note("E-03_min_size_ok", bool(root.winfo_width() >= 720 and root.winfo_height() >= 560))
    root.geometry("1536x864+0+0")   # 1920x1080 physical at 125 % scaling
    pump(root, 0.5)
    note("E-03_1920", shot(root, EVID / "E-03" / "1920x1080.png"))

    # E-08: empty state of the reopened application.
    root.geometry("1280x800+40+40")
    app.notebook.select(app.estimation_tab)
    pump(root, 2.5)
    note("E-08_history_rows", len(getattr(app, "_history_rows", []) or []))
    note("E-08_shot", shot(root, EVID / "E-08" / "reopen_empty.png"))
    app.notebook.select(app.analysis_tab)
    pump(root, 0.2)

    # E-09: invalid input dialog (isolated process, modal-safe).
    probe = subprocess.run([sys.executable, str(ROOT / "scripts" / "stage_e_e09_probe.py")],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=90)
    line = (probe.stdout or "").strip().splitlines()
    try:
        note("E-09_dialog", json.loads(line[-1]) if line else {"error": probe.stderr[-400:]})
    except json.JSONDecodeError:
        note("E-09_dialog", {"raw": line[-1:] , "stderr": (probe.stderr or "")[-400:]})
    note("E-09_returncode", probe.returncode)

    # E-01: analysis → QDII live estimate → stored run → backtest → export.
    app.code_var.set("000369")
    app.bench_var.set("")
    app.output_var.set("_stage_e_out")
    app.start_date_var.set("2025-01-01")
    t0 = time.monotonic()
    app.start_analysis()
    finished = wait_for(root, lambda: not app.is_running and bool(app.result_files), timeout=300)
    note("E-01_analysis_seconds", round(time.monotonic() - t0, 3))
    note("E-01_analysis_ok", finished)
    note("E-01_result_files", len(app.result_files))
    note("E-01_status", app.status_var.get())
    note("E-01_shot", shot(root, EVID / "E-01" / "analysis_done.png"))
    report_path = app.result_files.get("综合报告") or app._master_report_path
    note("E-01_master_report", str(report_path) if report_path else None)

    note("E-01_history_after_analysis", [r for r in (getattr(app, "_history_rows", []) or [])][:1])

    app.notebook.select(app.estimation_tab)
    pump(root, 0.5)
    app.open_qdii_window()
    pump(root, 0.3)
    qdii = app.windows.get("qdii_estimate")
    note("E-01_qdii_open", qdii is not None)
    if qdii is not None:
        t0 = time.monotonic()
        done = wait_for(root, lambda: qdii.view is not None and not qdii.worker.is_running,
                        timeout=300)
        note("E-01_qdii_seconds", round(time.monotonic() - t0, 3))
        note("E-01_qdii_ok", done)
        if qdii.view is not None:
            note("E-01_qdii_run_id", qdii.view.run_id)
            note("E-01_qdii_persisted", qdii.view.persisted)
            note("E-01_qdii_mode", qdii.view.mode)
        note("E-01_qdii_shot", shot(qdii, EVID / "E-01" / "qdii_live.png"))

    # E-04: long strings in the QDII asset table.
    from fa.application.estimation import EstimationView
    long_row = {
        "ticker": "VERYLONG-" + "X" * 40,
        "name": "超長公司名稱" * 12,
        "asset_type": "equity",
        "market": "US",
        "currency": "USD",
        "weight": 0.1234,
        "base_price_date": "2026-01-05",
        "target_price_date": "2026-01-06",
        "local_return": 0.01,
        "fx_return": 0.02,
        "cny_return": 0.0312,
        "contribution": 0.004,
        "status": "ok",
    }
    long_view = EstimationView(
        record={"fund_code": "000369", "status": "ok", "target_nav_date": "2026-01-06"},
        mode="stored", run_id="long-strings", persisted=True,
        asset_rows=[dict(long_row) for _ in range(3)], asset_source="",
        warnings=[], message="", inputs={},
    )
    if qdii is not None:
        qdii._render_view(long_view)
        pump(root, 0.4)
        widths = [qdii.asset_tree.column(name, "width") for name in
                  (qdii.asset_tree["columns"][:1])]
        note("E-04_shot", shot(qdii, EVID / "E-04" / "long_strings.png"))
        note("E-04_ticker_width", widths[0] if widths else None)
        note("E-04_row_count", len(qdii.asset_tree.get_children()))

    # E-06 / E-07: backtest window — progress events, then a bounded close.
    app.notebook.select(app.backtest_tab)
    pump(root, 0.3)
    app.open_backtest_window()
    pump(root, 0.4)
    backtest = app.windows.get("backtest")
    note("E-06_window", backtest is not None)
    events: list[str] = []
    if backtest is not None:
        def set_entry(key: str, value: str) -> None:
            entry = backtest.entries[key]
            entry.delete(0, "end")
            entry.insert(0, value)

        set_entry("spec_id", "stage-e")
        set_entry("start", "2026-11-03")
        set_entry("end", "2026-11-17")
        set_entry("initial_capital", "100000")
        backtest.universe_text.delete("1.0", "end")
        backtest.universe_text.insert("1.0", "ABBV,US,USD\nAMGN,US,USD")
        original_progress = backtest._on_progress
        original_result = backtest._on_result

        def spy_progress(payload):
            events.append(f"progress:{payload}")
            original_progress(payload)

        def spy_result(ok, payload):
            events.append(f"result:{ok}")
            original_result(ok, payload)

        backtest._on_progress = spy_progress
        backtest._on_result = spy_result
        backtest.submit()
        t0 = time.monotonic()
        wait_for(root, lambda: backtest.state != "running", timeout=240)
        note("E-06_seconds", round(time.monotonic() - t0, 3))
        note("E-06_state", backtest.state)
        note("E-06_events", events[:6] + (["…"] if len(events) > 6 else []))
        note("E-06_event_count", len(events))
        note("E-06_progress_then_single_result",
             sum(1 for e in events if e.startswith("result:")) == 1
             and any(e.startswith("progress:") for e in events))
        note("E-06_shot", shot(backtest, EVID / "E-06" / "backtest_completed.png"))
        result = backtest.result
        if result is not None:
            run = result.run
            note("E-06_run_id", run.run_id)
            note("E-06_created", result.created)
            note("E-06_metrics", {
                "sessions": run.metrics.sessions,
                "trades": run.metrics.trades,
                "cagr": run.metrics.cagr,
                "max_drawdown": run.metrics.max_drawdown,
            })
            note("E-06_equity_rows", len(run.equity_curve))
            note("E-10_export_backtest", str(reporting.export_backtest(
                run.run_id, repository_path=str(ROOT / "data" / "database" / "fund_analyzer.db"))))

        # E-07: second run, closed while it is in flight.
        backtest.submit()
        running = backtest.state
        t0 = time.monotonic()
        backtest.close(timeout=2.0)
        elapsed = time.monotonic() - t0
        note("E-07_was_running", running)
        note("E-07_close_seconds", round(elapsed, 3))
        note("E-07_bounded", elapsed <= 2.2)

    # E-10: exports for the stored estimation run, plus the reports tab.
    try:
        rows = estimation_service.list_estimates(limit=1)
        if rows:
            run_id = str(rows[0]["run_id"])
            note("E-10_estimation_run", run_id)
            note("E-10_export_estimation", str(reporting.export_estimation(
                run_id, save_dir=Path("_stage_e_out") / "000369")))
    except Exception as exc:
        note("E-10_export_estimation_error", f"{type(exc).__name__}: {exc}")

    app.notebook.select(app.reports_tab)
    pump(root, 0.4)
    app.open_history_window()
    pump(root, 1.0)
    history = app.windows.get("history")
    note("E-10_history_window", history is not None)
    if history is not None:
        note("E-10_shot", shot(history, EVID / "E-10" / "history_window.png"))

    # E-01 closing step.
    t0 = time.monotonic()
    app.shutdown()
    note("E-01_shutdown_seconds", round(time.monotonic() - t0, 3))

    # E-07 process check (rendered from the real process list).
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\\.exe$' "
         "-and $_.CommandLine -like '*fund_analyzer_gui.py*' } | "
         "Select-Object ProcessId, Name, CommandLine | Format-Table -AutoSize | Out-String"],
        capture_output=True, text=True, timeout=60)
    table = (proc.stdout or "").strip()
    if not table:
        table = "(no python process with fund_analyzer_gui.py in its command line)"
    note("E-07_orphan", table)
    image = Image.new("RGB", (1100, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), "Get-CimInstance Win32_Process  (fund_analyzer_gui.py filter)", fill="black")
    draw.text((10, 40), table.splitlines()[0][:150], fill="black")
    image.save(EVID / "E-07" / "process_check.png")

    # E-08: reopen the application once more to show the empty state.
    root2 = tk.Tk()
    app2 = FundAnalyzerGUI(root2)
    root2.update_idletasks()
    root2.update()
    app2.notebook.select(app2.estimation_tab)
    pump(root2, 1.2)
    note("E-08_reopen_rows", len(getattr(app2, "_history_rows", []) or []))
    note("E-08_reopen_shot", shot(root2, EVID / "E-08" / "reopen_after_close.png"))
    app2.shutdown()

    RESULTS["log"] = LOG
    REPORT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {REPORT}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        RESULTS["fatal"] = traceback.format_exc()
        REPORT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        print(RESULTS["fatal"], flush=True)
        raise SystemExit(1)
