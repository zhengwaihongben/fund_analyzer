# -*- coding: utf-8 -*-
"""E-02a: the GUI entry point must stay cheap to import.

A cold start used to spend most of its 6.8 s importing pandas/matplotlib and
the backtesting engine through ``fa.gui.app``.  These guards fail if someone
re-introduces a module-level heavy import or an eager tab build.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

HEAVY_MODULES = ("pandas", "matplotlib", "fa.pipeline", "fa.viz.master")


class StartupImportTests(unittest.TestCase):
    def test_importing_the_entry_point_pulls_no_heavy_module(self):
        code = (
            "import sys, fa.gui.app;"
            "heavy = [name for name in %r if name in sys.modules];"
            "print(','.join(heavy))" % (HEAVY_MODULES,)
        )
        done = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.strip(), "",
            f"heavy modules imported at startup: {done.stdout.strip()}",
        )

    def test_only_the_analysis_tab_is_built_at_startup(self):
        # Run this in a child process: creating a second Tcl interpreter inside
        # the shared test process trips Tcl's async-handler finalisation.
        code = (
            "import tkinter as tk\n"
            "from fa.gui.app import FundAnalyzerGUI\n"
            "root = tk.Tk(); root.withdraw()\n"
            "app = FundAnalyzerGUI(root)\n"
            "print('built=' + ','.join(sorted(app._tabs_built)))\n"
            "print('tree=' + str(hasattr(app, 'history_tree')))\n"
            "app._ensure_tab('estimation')\n"
            "print('after=' + ','.join(sorted(app._tabs_built)))\n"
            "print('tree2=' + str(hasattr(app, 'history_tree')))\n"
            "app.windows.close_all(timeout=0.1); app.shutdown()\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.split()
        self.assertIn("built=", lines)
        self.assertIn("tree=False", lines)
        self.assertIn("after=estimation", lines)
        self.assertIn("tree2=True", lines)


if __name__ == "__main__":
    unittest.main()
