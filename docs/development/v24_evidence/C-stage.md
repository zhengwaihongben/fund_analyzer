# Stage C — Backtest GUI (C-01 … C-19)

Status: **delivered 2026-11-21; C-14 / C-17 / C-18 remain manual (pending).**

The engine is not touched by Stage C. `fa/backtesting/*.py` is byte-identical
to the previously delivered package (see §C-16). Everything below reads the
persisted `BacktestRun`; no panel recomputes a metric or a cost.

## 1. Files

New:

- `fa/application/backtesting.py` — `run_strategy`, `load_run`, `count_runs`,
  `BacktestRunResult(run, created, request_cost_source)`, and the
  cost-preserving `spec_to_json` / `spec_from_json`.
- `fa/gui/backtest_window.py` — `BacktestForm` (conversion only),
  `BacktestWindow`, and the pure view helpers (`summary_rows`,
  `runtime_rows`, `cost_source_rows`, `metrics_rows`, `equity_rows`,
  `trade_rows`, `anomaly_lines`, `equity_series`).
- `tests/test_application_backtesting.py` (15), `tests/test_gui_backtest_form.py`
  (14), `tests/test_gui_backtest_view.py` (20).
- `scripts/stage_c_manual_driver.py` — scripted half of C-14/C-17/C-18.
- This file, `C-16.json`, `C-17_report.json`, `C-17_running.png`.

Modified: `fa/gui/app.py` (backtest tab + window registration),
`docs/development/v24_acceptance_matrix.md`, `docs/technical_specification/V2/07_development_roadmap.md`,
`README.md`.

## 2. Item evidence

| Item | Method | Command | Result |
| --- | --- | --- | --- |
| C-01 | A | `python -m unittest tests.test_gui_backtest_form` | `Ran 14 tests … OK` |
| C-02 | A | `… test_gui_backtest_view.WindowValidationTests.test_weighting_switch_enables_the_right_fields` | pass |
| C-03 | A | `… test_invalid_spec_creates_no_run` | pass (`state=idle`, no worker thread) |
| C-04 | R | `… test_engine_validation_is_not_duplicated` | pass — the reason string comes from `StrategySpec` |
| C-05 | A | `… JsonRoundTripTests` (default sentinel + explicit model) | pass |
| C-06 | A | `… test_c06_metrics_render_from_the_same_run_the_service_returns` | pass |
| C-07 | A | `… test_c07_metrics_match_the_stored_row` | pass (after `load_backtest_run`) |
| C-08 | A | `… test_reused_run_says_so` + `C-13` badge text | pass |
| C-09 | A | `… test_c09_equity_rows_have_seven_columns_including_drawdown` | 7 columns incl. drawdown, value equal to the stored `equity_curve[].drawdown` |
| C-10 | A | `… test_c09_trade_rows_carry_every_cost_leg` | 14 columns incl. commission/slippage/FX spread/net |
| C-11 | A | `… test_c11_anomalies_keep_their_order_and_have_an_empty_state` | pass (`無異常`) |
| C-12 | A | `… test_c12_all_four_error_classes_are_shown` | four classes shown as `ClassName: message` |
| C-13 | A | `… test_second_submit_is_refused_while_running` | pass |
| C-14 | M | `python scripts/stage_c_manual_driver.py` | **manual** — scripted run below (progress text now shown during the run) |
| C-15 | A + R | `python -m unittest tests.test_gui_lifecycle` | `Ran 24 tests … OK` (`test_gui_close_during_run_bounded_wait`) |
| C-16 | R | `docs/development/v24_evidence/C-16.json` | 8/8 engine files byte-identical |
| C-17 | M | driver + screenshot | **manual** — scripted run below |
| C-18 | M | driver: `fingerprint_visible: true`, run id displayed | **manual** — traceability shown |
| C-19 | A | `… RuntimePanelTests`, `… SentinelTests`, `… cost_mode_switch_enables_the_cost_entries` | pass |

## 3. C-14 / C-17 / C-18 scripted session

`python scripts/stage_c_manual_driver.py` (seeded throwaway database, real
`BacktestWindow`, real `run_backtest` through `WorkerBridge`):

- window 2025-01-06 → 2026-06-19, 2 instruments, capital 1,000,000 CNY;
- run **completed** in 5.75 s wall time, 16 trades, 374 equity points,
  7 anomalies;
- main-thread heartbeat: 423 ticks at 20 ms, **max lateness 27 ms**, none
  above 100 ms (`C-17_report.json`);
- summary shows the run id and the full fingerprint (`fingerprint_visible:
  true`), metrics table 13 rows, equity table 374 rows, trades 16 rows,
  runtime panel 8 rows, cost panel 5 rows;
- closing the window took **0.078 s**;
- screenshot: `C-17_running.png`; raw JSON: `C-17_report.json`.

These numbers prove the window keeps answering while the engine runs and that
the run id / fingerprint are visible, but C-14 ("feels smooth"), C-17 ("looks
right on a real desktop") and C-18 still need the lead's interactive pass.
The procedure is the same ten steps as `A-10.md`, pointed at the strategy
window.

## 4. Verification

- `python -m unittest tests.test_application_backtesting` → `Ran 15 tests … OK`
- `python -m unittest tests.test_gui_backtest_form tests.test_gui_backtest_view` → `Ran 34 tests … OK`
- `python -m unittest tests.test_gui_lifecycle` → `Ran 24 tests … OK`
- full suite → `Ran 1098 tests … OK` (62.296 s, Python 3.10.9)
- `python -m compileall -q fa tests scripts fund_analyzer.py fund_analyzer_gui.py` → 0
- `git diff --check` → 0 (untracked files added with `git add -N` before the check)

## 5. Known limits

- C-14/C-17/C-18 are pending manual confirmation; the scripted run is
  reproducible but is not the lead's desktop session.
- The scripted session uses a seeded throwaway database, not the working one:
  `data/database/fund_analyzer.db` has 30 FX rows (2026-11 only), so a
  multi-month 2025 run there would skip every foreign order for lack of FX.
- The equity chart is a minimal canvas (no axes/legend); it plots total vs
  benchmark only.
- Reused/historical runs cannot show their original cost mode — the mode was
  never persisted (documented in the spec and the panel text).
