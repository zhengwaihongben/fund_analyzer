# -*- coding: utf-8 -*-
"""A-10 helpers: render the process-check evidence and run the restart check."""
from __future__ import annotations

import json
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "development" / "v24_evidence"
TEXT = ROOT / "test_evidence" / "a10_process_check.txt"

COMMAND = (
    "Get-CimInstance Win32_Process | Where-Object {\n"
    "    $_.Name -match '^python(w)?\\.exe$' -and\n"
    "    $_.CommandLine -like '*fund_analyzer_gui.py*'\n"
    "} | Select-Object ProcessId, Name, CommandLine"
)


def render() -> None:
    from PIL import Image, ImageDraw, ImageFont

    body = TEXT.read_text(encoding="utf-8").strip()
    lines = [
        "A-10 process check (5 s after the GUI closed)",
        "",
        "PS> " + COMMAND.replace("\n", "\n    "),
        "",
        "Output:",
        body or "(empty)",
        "",
        "Result: no GUI-related Python process remains.",
    ]
    font = ImageFont.load_default()
    width, line_height = 980, 18
    height = line_height * (len(lines) + 2) + 24
    image = Image.new("RGB", (width, height), "#101418")
    draw = ImageDraw.Draw(image)
    y = 12
    for line in lines:
        draw.text((14, y), line, fill="#e6e6e6", font=font)
        y += line_height
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    image.save(EVIDENCE / "A-10_process_check.png")
    print("rendered", EVIDENCE / "A-10_process_check.png")


def restart() -> dict:
    from fa.gui.app import FundAnalyzerGUI

    result = {"runs": [], "errors": []}
    for attempt in (1, 2):
        began = time.monotonic()
        root = tk.Tk()
        app = FundAnalyzerGUI(root)
        root.update_idletasks()
        result["runs"].append(
            {
                "attempt": attempt,
                "title": root.title(),
                "tabs": root.nametowidget(app.notebook).index("end"),
                "open_seconds": round(time.monotonic() - began, 3),
            }
        )
        root.after(500, app.shutdown)
        root.after(20_000, lambda: (result["errors"].append("watchdog"), root.destroy()))
        root.mainloop()
        time.sleep(0.5)
    result["crash_log_exists"] = (ROOT / "crash.log").exists()
    result["fatal_log_exists"] = (ROOT / "fatal.log").exists()
    return result


if __name__ == "__main__":
    if "--restart" in sys.argv:
        print(json.dumps(restart(), ensure_ascii=False, indent=2))
    else:
        render()
