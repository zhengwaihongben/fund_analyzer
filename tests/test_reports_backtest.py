# -*- coding: utf-8 -*-
"""Stage D — backtest report renderer move (D-01, D-02)."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fa.backtesting import run_backtest
from fa.data.database.repository import Repository
from fa.reporting.backtest_report import render as reporting_render
from tests.fixtures.backtesting import NOW, build_store, make_spec


def _load_cli():
    path = Path(__file__).resolve().parents[1] / "scripts" / "backtest_report.py"
    spec = importlib.util.spec_from_file_location("btreport_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RendererMoveTests(unittest.TestCase):
    """D-01/D-02: one implementation shared by the CLI and the GUI."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "render.db")
        self.addCleanup(self.repository.close)
        store = build_store(self.repository, days=40)
        spec = make_spec(store, sessions=40)
        self.run, _ = run_backtest(spec, self.repository, now=NOW)

    def test_d01_cli_imports_the_reporting_renderer(self):
        cli = _load_cli()
        self.assertIs(cli.render, reporting_render)
        self.assertFalse(hasattr(cli, "_pct"))

    def test_d01_render_is_the_shared_text(self):
        text = reporting_render(self.run)
        self.assertIn(f"# Backtest report — `{self.run.run_id}`", text)
        for heading in ("## Identity and configuration", "## Performance",
                        "## Costs by side", "## Equity curve", "## Trades",
                        "## Provenance"):
            self.assertIn(heading, text)
        self.assertIn("| Drawdown |", text)
        self.assertIn(f"`{self.run.data_fingerprint}`", text)
        self.assertEqual(text, reporting_render(self.run))

    def test_d02_cli_write_report_matches_the_renderer(self):
        cli = _load_cli()
        target = Path(self._tmp.name) / "report.md"
        written = cli.write_report(
            reporting_render(self.run), self.run.run_id, target)
        text = Path(written).read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertEqual(text, reporting_render(self.run))

    def test_d02_cli_list_and_show_still_read_stored_runs(self):
        cli = _load_cli()
        db = Path(self._tmp.name) / "render.db"
        rows = cli.list_runs(db, 10)
        self.assertEqual([row["run_id"] for row in rows], [self.run.run_id])
        listing = cli.main(["--db", str(db), "list"])
        self.assertEqual(listing, 0)


if __name__ == "__main__":
    unittest.main()
