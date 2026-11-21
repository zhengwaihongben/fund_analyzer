# -*- coding: utf-8 -*-
"""E-09 isolated probe: capture the real invalid-input dialog, then exit.

The dialog is a native modal loop: dismissing it from inside Tk is unreliable,
so the probe schedules a capture a second after the dialog appears and then
ends its own process with ``os._exit``.  The screenshot is of the real dialog;
the process exit is recorded in the driver summary.
"""
from __future__ import annotations

import json
import os
import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab  # noqa: E402

from fa.gui.app import FundAnalyzerGUI  # noqa: E402

SHOT = ROOT / "docs" / "development" / "v24_evidence" / "E-09" / "invalid_input_dialog.png"
payload: dict[str, object] = {}


def capture_then_exit() -> None:
    try:
        image = ImageGrab.grab()
        SHOT.parent.mkdir(parents=True, exist_ok=True)
        image.save(SHOT)
        payload["shotsize"] = list(image.size)
    except Exception as exc:  # pragma: no cover
        payload["capture_error"] = repr(exc)
    payload["exited_by"] = "capture callback (modal loop still active)"
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    os._exit(0)


def main() -> int:
    root = tk.Tk()
    app = FundAnalyzerGUI(root)
    root.update_idletasks()
    root.update()
    app.code_var.set("")
    root.after(1500, capture_then_exit)
    try:
        app.start_analysis()
        payload["returned"] = "start_analysis returned without a dialog"
    except Exception as exc:  # pragma: no cover
        payload["raised"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
