# Stage D — reports and history (evidence)

Status: **delivered 2026-11-21; review fixes F1–F5 applied 2026-11-21**,
re-submitted for independent review. D-01…D-09 and D-11 are automated; D-10 is
the real-desktop manual item and stays pending until the lead's interactive
session (same treatment as B-16/C-17).

## 1. Scope and files

| File | Purpose |
|---|---|
| `fa/data/database/repository.py` | **only addition**: `list_backtest_runs(spec_id=None, limit=50)` — one bound-parameter `SELECT … LEFT JOIN strategy_backtest_metrics`, order `created_at DESC, run_id DESC`, limit clamped to 1..200. No existing method touched. |
| `fa/reporting/__init__.py` | exports both renderers. |
| `fa/reporting/backtest_report.py` | renderer moved verbatim from `scripts/backtest_report.py` (`render`, `_pct`, `_number`), pure over a `BacktestRun`. |
| `fa/reporting/estimation_report.py` | new pure renderer over an `EstimationView` (record + inputs + merged asset rows). |
| `fa/application/reporting.py` | `render_backtest`, `render_estimation`, `export_backtest`, `export_estimation`, `history_backtests`, `history_estimations`; every repository closed in `finally`; never calls `run_backtest`/the estimator. |
| `fa/gui/history_window.py` | report centre: estimation tab + backtest tab, filters, preview `tk.Text`, export button; all reads/exports through `WorkerBridge` (module-level jobs). |
| `fa/gui/app.py` | reports placeholder replaced by an intro card + 「開啟歷史視窗」; window registered as `history`. |
| `scripts/backtest_report.py` | now imports `render` from `fa.reporting.backtest_report`; `write_report` and the three subcommands unchanged. |
| tests | `tests/test_reports_backtest.py`, `tests/test_reports_estimation.py`, `tests/test_reports_history.py`, `tests/test_application_reporting.py`. |

## 2. Acceptance evidence

| ID | Method (file::test) | Command / result |
|---|---|---|
| D-01 | `test_reports_backtest.py::RendererMoveTests::test_d01_cli_imports_the_reporting_renderer`, `…::test_d01_render_is_the_shared_text` | `python -m unittest tests.test_reports_backtest` → `Ran 4 tests … OK` |
| D-02 | `…::test_d02_cli_write_report_matches_the_renderer`, `…::test_d02_cli_list_and_show_still_read_stored_runs` | same run; `cli.main(["--db", …, "list"]) == 0` |
| D-03 | `test_reports_history.py::HistoryWindowTests::test_d03_window_lists_and_renders_the_stored_run` | real Tk window + real worker; shown text `== reporting.render_backtest(run_id)` |
| D-04 | `test_application_reporting.py::ReportingTestCase::test_d04_rendering_never_runs_a_backtest_or_the_estimator` | `run_backtest` and `estimate_qdii_nav` patched to raise |
| D-05 | `test_reports_estimation.py::EstimationReportTests` (8 tests) | sections, `—` for missing, warnings verbatim, 推算 marker, contract message, nested `inputs["holdings"]` isolated from the record |
| D-06 | `test_reports_history.py::HistoryRowMappingTests` + `test_application_reporting.py::…::test_d06_history_lists_and_filters` | order preserved, fund/spec filter, limit clamp |
| D-07 | `…::test_d07_export_button_writes_the_same_text` + `test_application_reporting.py::…::test_d07_export_backtest_matches_the_renderer` | `output/reports/<run_id>.md` equals the renderer text |
| D-08 | `test_application_reporting.py::…::test_d08_export_estimation_matches_the_renderer` | `output/reports/<run_id>_estimate.md` equals the renderer text |
| D-09 | `test_application_reporting.py::…::test_d09_browsing_and_export_do_not_change_rows` | SHA-256 over every table's rows unchanged before/after list/render/export |
| D-10 | manual | pending — see `docs/development/v24_evidence/D-10.md` |
| D-11 | `test_reports_history.py::HistoryQueryTests` (5 tests) | columns/order/limit/filter/read-only, values compared with `load_backtest_run()`; the SQL trace proves the bound `LIMIT` is 1/1/200 |

Verification run (Python 3.10.9, this machine):

```
python -m unittest tests.test_reports_backtest        → Ran 4 tests  … OK
python -m unittest tests.test_reports_estimation      → Ran 8 tests  … OK
python -m unittest tests.test_reports_history         → Ran 13 tests in 13.090s OK
python -m unittest tests.test_application_reporting   → Ran 6 tests  … OK
python -m unittest discover -s tests -t .             → Ran 1129 tests in 115.439s OK
python -m compileall -q fa tests scripts ….py         → 0
git diff --check (complete working tree)              → 0
```

`git diff --check` was run after `git add -N` on **every** new or modified file
listed in §1, including `scripts/backtest_report.py`, so whitespace defects in
the whole working tree were checked, not only in the tracked files. The
repository does track many earlier specification documents as well as
`README.md`; only the files in §1 belong to this batch.

## 3. Behaviour notes

- `list_backtest_runs` reads only `strategy_backtest_runs` and
  `strategy_backtest_metrics`; `cagr`/`max_drawdown`/`total_costs_base` are
  reported as stored, never recomputed. It is the only repository change and
  it is read-only (a test calls it twice and compares row counts).
- Both renderers are pure functions: no `sqlite3`, no network, no estimator,
  no `run_backtest`. `fa.reporting` is imported by the CLI and by the GUI.
- The GUI never imports the database layer or the renderers directly; it only
  imports `fa.application.reporting`, and its worker entry points are
  module-level so no widget crosses a thread boundary.
- Export paths: `output/reports/<run_id>.md` (backtest) and
  `output/reports/<run_id>_estimate.md` (estimation).

## 4. Known limits

- **D-10 remains manual.** A scripted run can prove the preview equals the
  renderer, but "opened a real stored run on the desktop and opened the
  exported file" needs an interactive session.
- No HTML report is generated in this batch (Markdown text only).
- History is immutable by design: there is no delete, edit or re-run action;
  removing a record is a future, separately-reviewed change.
- The CLI `list` keeps its ascending `run_id` tie-break (D-02 requires the CLI
  output to stay byte-identical); the new repository method deliberately uses
  `run_id DESC`.
- The estimation report shows whatever the stored record/snapshot carries;
  if an old run has no `estimation_inputs` row, quality fields render as `—`.
- **Attribution `Return` row is always `—` for stored runs.** The stored
  estimation row keeps `local_contribution`/`fx_contribution` but not the
  per-asset `local_return`/`fx_return`/`cny_return`, so the renderer
  deliberately shows `—` rather than recomputing an attribution from the
  persisted numbers (F5 of the review; aggregation would be a new calculation).

## 5. Review fixes (review `m13830`, 2026-11-21)

| Fix | Defect | Change | Evidence |
|---|---|---|---|
| F1 | `HistoryWindow.close()` gave each pane the full 2 s budget, so two in-flight panes measured 4.06 s | one `deadline = time.monotonic() + budget`; each pane receives only the remaining time | `test_close_with_two_running_panes_is_bounded` (< 2.2 s with both panes loading) |
| F2 | Evidence said `test_reports_backtest` ran 5 tests | corrected to **4** (total 1125 was already right) | `python -m unittest tests.test_reports_backtest` |
| F3 | diff-check statement over-claimed coverage | `scripts/backtest_report.py` also staged with `git add -N`; wording corrected (§2) | `git status --short`, `git diff --check` |
| F4 | D-11 upper clamp untested (two rows) | sqlite trace callback asserts the bound `LIMIT` values 1/1/200 | `test_d11_limit_is_clamped_and_bounded` |
| F5 | estimation pane untested; nested-input test not isolated; `_run_id` set before the text loaded; registry never removed the window; attribution row undocumented | estimation pane render + export tests, isolated nested-input test, `_pending_run_id` swapped in only on success, `windows.remove("history")` + `_history_window` assignment, limit documented above | new tests in `test_reports_history.py` / `test_reports_estimation.py` |
