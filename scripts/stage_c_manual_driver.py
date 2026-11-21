# -*- coding: utf-8 -*-
"""Stage C evidence driver: a real backtest through the real window.

This is the scripted half of C-14/C-17/C-18.  It builds a seeded throwaway
database, opens :class:`BacktestWindow` exactly like the app does, submits the
form through the worker bridge, keeps pumping the Tk event loop while a
heartbeat measures main-thread lateness, screenshots the finished window and
prints the evidence as JSON.

It cannot replace the lead's interactive pass (smoothness, real mouse work);
it exists so the numbers behind C-14/C-17/C-18 can be reproduced.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402

from fa.data.database.repository import Repository  # noqa: E402
from fa.gui.backtest_window import BacktestWindow  # noqa: E402
from tests.fixtures.backtesting import build_store, make_spec  # noqa: E402

EVIDENCE = ROOT / "docs" / "development" / "v24_evidence"


def _screenshot(window: tk.Toplevel, target: Path) -> bool:
    try:
        from PIL import ImageGrab
    except Exception as exc:  # pragma: no cover - Pillow is present here
        print(f"screenshot skipped: {exc}", file=sys.stderr)
        return False
    try:
        window.update_idletasks()
        x = window.winfo_rootx()
        y = window.winfo_rooty()
        width = window.winfo_width()
        height = window.winfo_height()
        image = ImageGrab.grab(bbox=(x, y, x + width, y + height))
        image.save(target)
        return True
    except Exception:
        traceback.print_exc()
        return False


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="stage-c-"))
    database = tmpdir / "fund_analyzer.db"
    repository = Repository(database)
    build_store(repository, days=380)
    spec = make_spec(sessions=380)

    root = tk.Tk()
    root.withdraw()
    window = BacktestWindow(root, repository_path=str(database))
    window.geometry("1080x760+0+0")
    window.deiconify()
    window.update()

    window.entries["spec_id"].delete(0, "end")
    window.entries["spec_id"].insert(0, spec.spec_id)
    window.entries["start"].delete(0, "end")
    window.entries["start"].insert(0, spec.start.isoformat())
    window.entries["end"].delete(0, "end")
    window.entries["end"].insert(0, spec.end.isoformat())
    window.entries["initial_capital"].delete(0, "end")
    window.entries["initial_capital"].insert(0, str(int(spec.initial_capital)))
    window.universe_text.delete("1.0", "end")
    window.universe_text.insert(
        "1.0", "\n".join(f"{item.ticker},{item.market},{item.currency}"
                         for item in spec.universe))

    report: dict[str, object] = {
        "database": str(database),
        "spec_id": spec.spec_id,
        "start": spec.start.isoformat(),
        "end": spec.end.isoformat(),
        "initial_capital": spec.initial_capital,
        "universe": [item.ticker for item in spec.universe],
    }
    ticks = {"count": 0, "max_lateness_ms": 0.0, "late": []}
    state = {"last": time.monotonic()}

    def heartbeat() -> None:
        now = time.monotonic()
        lateness = (now - state["last"] - 0.02) * 1000.0
        state["last"] = now
        ticks["count"] += 1
        if lateness > ticks["max_lateness_ms"]:
            ticks["max_lateness_ms"] = lateness
        if lateness > 100.0:
            ticks["late"].append(round(lateness, 1))
        if not window._closed and root.winfo_exists():
            root.after(20, heartbeat)

    started = time.monotonic()
    window.submit()
    root.after(20, heartbeat)
    deadline = started + 180.0
    while time.monotonic() < deadline:
        root.update()
        if window.state in ("completed", "failed") or window._closed:
            break
        time.sleep(0.01)
    report["wall_seconds"] = round(time.monotonic() - started, 3)
    report["state"] = window.state
    report["status_text"] = window.status_var.get()
    report["heartbeat"] = {
        "ticks": ticks["count"],
        "max_lateness_ms": round(ticks["max_lateness_ms"], 1),
        "late_over_100ms": ticks["late"][:10],
    }

    if window.result is not None:
        run = window.result.run
        report["run_id"] = run.run_id
        report["created"] = window.result.created
        report["request_cost_source"] = window.result.request_cost_source
        report["trades"] = len(run.trades)
        report["equity_points"] = len(run.equity_curve)
        report["anomalies"] = len(run.anomalies)
        report["fingerprint_visible"] = bool(
            run.data_fingerprint[:12] in window.summary_text.get("1.0", "end"))
        report["metrics_rows"] = len(window.metrics_tree.get_children())
        report["equity_rows"] = len(window.equity_tree.get_children())
        report["trade_rows"] = len(window.trades_tree.get_children())
        report["runtime_rows"] = len(window.runtime_tree.get_children())
        report["cost_rows"] = len(window.cost_tree.get_children())
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        report["screenshot"] = _screenshot(
            window, EVIDENCE / "C-17_running.png")

    close_started = time.monotonic()
    window.close(timeout=2.0)
    report["close_seconds"] = round(time.monotonic() - close_started, 3)
    root.destroy()
    repository.close()
    shutil.rmtree(tmpdir, ignore_errors=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("state") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
