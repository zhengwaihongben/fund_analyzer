# V2.4 — GUI Integration Acceptance Matrix



Status: **revised draft, round 2** (2026-11-20, after the independent Stage 0

review). Every row starts as `pending`; a stage is accepted only when all of its

rows are `pass` with the named evidence present in the package.



**Item count: 79** (Stage 0: 9, A: 10, B: 17, C: 19, D: 11, E: 13). The first

draft claimed 78 but actually contained 76; round 2 adds three rows for the new

requirements (B-17 live persistence, C-19 cost mode / runtime panel, D-11

`list_backtest_runs`), and the number above is the audited count of this file.



Spec: `docs/technical_specification/V2/11_v24_gui_integration.md`.

Method key: **A** = automated test, **M** = manual Windows desktop check,

**R** = code review / hash comparison.



## Stage 0 — Baseline audit and specification freeze



| ID | Acceptance item | Method | Evidence | Status |

| --- | --- | --- | --- | --- |

| S0-01 | Specification exists with §1–§13 and names every planned feature | R | `11_v24_gui_integration.md` | pass (round 3) |

| S0-02 | This matrix exists with Stage 0–E rows and audited counts (79) | R | this file | pass (79 rows) |

| S0-03 | GUI baseline audited with line counts and lifecycle flags | R | spec §3.1–§3.3 | pass |

| S0-04 | Estimation I/O audited: record vs `estimation_inputs` vs JSON, and the `repository=` requirement | R | spec §3.5, §3.6 G-05/G-14 | pass |

| S0-05 | Backtest API audited: `run_backtest`, `BacktestRun`, persistence methods, CLI renderer | R | spec §3.4 | pass |

| S0-06 | Gaps G-01…G-14 recorded, each mapped to the stage that closes it | R | spec §3.6, §13 | pass |

| S0-07 | No functional code changed by Stage 0 | A + R | package-diff tool: `added=[2 docs] changed=[2 docs] python_files_touched=[]`; `git status --short` | pass |

| S0-08 | Roadmap and overview index updated (V2.4 split into A–E, document 11 listed) | R | `07_development_roadmap.md`, `00_overview.md` | pass |

| S0-09 | Independent review of the spec and matrix recorded, with every finding dispositioned | R | reviews m11846 / m11880 / m11941 and the round-1…3 change lists | pass (accepted m11973) |



## Stage A — GUI foundation and navigation



| ID | Acceptance item | Method | Evidence | Status |

| --- | --- | --- | --- | --- |

| A-01 | Main window opens with the same title/minimum size and closes cleanly (`WM_DELETE_WINDOW` → `shutdown`) | A | `test_gui_lifecycle.py::MainWindowStageATests::test_main_window_opens_and_closes` | pass |

| A-02 | Four tabs exist with keys `analysis`/`estimation`/`backtest`/`reports` and titles 基金分析/QDII 估算/策略回測/報告與歷史 | A | `test_gui_lifecycle.py::MainWindowStageATests::test_notebook_has_four_areas` | pass |

| A-03 | Each tab can be selected, left and re-selected without error | A | `test_gui_lifecycle.py::MainWindowStageATests::test_tabs_switch` | pass |

| A-04 | Closing an area cancels its timers, suppresses late UI updates and leaves no widget access; an uncancellable daemon worker may still finish in the background (T-7) | A + R | `test_gui_worker.py::WorkerBridgeTests::test_late_message_after_close_is_dropped`, `test_gui_lifecycle.py::MainWindowStageATests::test_close_cancels_timers` | pass |

| A-05 | Closing the main window runs every registered child's close path | A | `test_gui_lifecycle.py::MainWindowStageATests::test_shutdown_closes_registered_windows` | pass |

| A-06 | A worker holds no widget reference: the service input is plain data, and the live thread target/callable/args carry no bridge, widget or window reference | A + R | `test_gui_worker.py::WorkerThreadIsolationTests` (6), `test_gui_lifecycle.py::LegacyWorkerIsolationTests` (2), `RegistryBudgetTests` (2) + `v24_evidence/A-06.md` | pass |

| A-07 | Post-market window polls on a fake scheduler and cancels on close (no real 60 s wait in tests) | A | `test_gui_lifecycle.py::PostMarketSchedulerTests::test_post_market_poll_is_scheduled_and_cancelled` | pass |

| A-08 | Shared formatters: signed percentages, coverage one decimal, missing `—`, colour never the only signal | A | `test_gui_formatting.py` (23 tests) | pass |

| A-09 | The analysis tab keeps its current behaviour (params, log pane, result list, master-report entry) | A | `test_gui.py` unchanged (11 tests) + `test_gui_lifecycle.py::MainWindowStageATests::test_analysis_tab_intact` | pass |

| A-10 | Responsiveness: a long worker run keeps the window answerable (log poll keeps delivering) | M | `v24_evidence/A-10.md` — Windows manual test completed 2026-11-20: scripted Tk session (real 000369 analysis, heartbeat p95 0 ms / max 591 ms, close in 2.25 s, no process left, restart OK) plus the lead's mouse-drag, wheel-scroll and embedded-viewer confirmation; `A-10_running.png` + `A-10_process_check.png` attached | pass |



Automated rows above cite the specific test ids; the command and the **actual**

result line for each of A-01…A-09 are recorded in

`v24_evidence/A-automated-tests.md` (environment, per-item command, real

`Ran N tests … OK` output, full-suite/compileall/diff results).



## Stage B — Estimation integration



| ID | Acceptance item | Method | Evidence | Status |

| --- | --- | --- | --- | --- |

| B-01 | Summary panel shows every field of spec §6.2 from the record, no formula in the GUI | A | `test_gui_estimation_view.py::test_summary_fields_cover_the_spec`, `::test_missing_values_render_as_dash` | pass |

| B-02 | Asset table shows ticker, name, asset type, market, currency, weight, base/target price date, local/FX/CNY return, contribution, status | A | `test_gui_estimation_view.py::test_columns_follow_the_spec_order`, `::test_weight_is_unsigned_and_returns_are_signed` | pass |

| B-03 | A row with status ≠ `ok` shows its status text instead of a number | A | `test_gui_estimation_view.py::test_bad_status_row_shows_status_text_instead_of_numbers` | pass |

| B-04 | Unknown weight is never rendered as a zero return | A | `test_gui_estimation_view.py::test_unknown_weight_and_contribution_never_become_zero` | pass |

| B-05 | `partial`, `insufficient-data`, `missing-fx`, `estimated` are visually and textually distinct | A | `test_gui_estimation_view.py::test_bad_status_row_shows_status_text_instead_of_numbers`, `test_gui_formatting.py::test_status_values` | pass |

| B-06 | Data-quality block shows quarter/scope/disclosure (推算 when estimated), price type, warnings, look-ahead status, uncovered weight | A | `test_gui_estimation_view.py::test_quality_block_reports_quarter_scope_disclosure_and_sources`, `::test_warnings_are_kept_verbatim`, `::test_uncovered_weight_is_explained_not_zeroed`, `::test_nested_holdings_metadata_is_rendered`, `::test_a_payload_without_holdings_says_missing_not_wrong` | pass |

| B-07 | Run id, model version and look-ahead status are always visible | A | `test_gui_estimation_view.py::test_summary_fields_cover_the_spec`, `::test_four_state_look_ahead_text`, `::test_stored_boolean_zero_and_one_render_instead_of_a_dash` | pass |

| B-08 | Live mode goes through `application.estimation.run_estimate`, which passes a repository; stored mode uses `load_estimate` and never calls the estimator | A | `test_application_estimation.py::test_live_uses_a_repository_and_writes_the_run`, `::test_stored_service_never_calls_the_estimator_or_writes` | pass |

| B-09 | A stored run is never overwritten; re-running creates a new run id | A | `test_application_estimation.py::test_each_live_run_gets_its_own_id` | pass |

| B-10 | History list equals the application service's list (order, filters) and loads when the tab opens | A | `test_gui_estimation_view.py::test_history_rows_preserve_the_service_order`, `test_gui_lifecycle.py::EstimationTabWiringTests::test_estimation_tab_loads_history_once_the_result_arrives` | pass |

| B-11 | Asset detail loads from `estimation_inputs` first, then `{fund}_qdii_estimate_inputs.json` → `snapshots[run_id]` supplies `contribution`; a latest `_qdii_estimate.json` whose `run_id` differs is ignored; the source label is shown; a missing `contribution` renders `—` | A | `test_application_estimation.py::test_contribution_comes_from_the_per_run_snapshot`, `::test_latest_json_of_another_run_is_ignored`, `::test_latest_json_with_the_same_run_id_is_used`, `test_gui_estimation_view.py::test_unknown_weight_and_contribution_never_become_zero` | pass |

| B-12 | When neither the SQLite inputs nor the per-run snapshot yield usable rows (and the latest JSON does not match the run), the window shows 「此執行未保存可用資產明細（僅存摘要）」 | A | `test_application_estimation.py::test_missing_asset_detail_is_announced`, `test_gui_estimation_view.py::test_missing_asset_rows_use_the_contract_message` | pass |

| B-13 | Analysis → QDII handover still works (`estimate=` path), reachable from the analysis button | A | `test_gui_estimation_view.py::test_handover_renders_without_calling_the_service`, `::test_stored_and_handover_state_are_distinct`, `test_gui_lifecycle.py::EstimationTabWiringTests::test_the_qdii_button_opens_the_handover_view`, `::test_open_handover_switches_an_existing_window` | pass |

| B-14 | Closing during estimation cancels timers and drops late messages; the repository is closed on both success and failure | A | `test_application_estimation.py::test_repository_is_closed_when_the_estimator_raises`, `test_gui_estimation_view.py::test_close_drops_a_late_result` | pass |

| B-15 | Stored-mode browsing (list → detail → back) does not rewrite the stored run, its inputs snapshot or its latest JSON | A | `test_gui_estimation_view.py::test_browsing_does_not_rewrite_rows_or_json` | pass |

| B-16 | Real desktop: a real 000369 estimate renders with its warnings and quality block | M | folded into Stage E — `v24_evidence/E-01.md` §2 and `E-01/qdii_live.png` (real 000369 live estimate with warnings and quality block) | pass |

| B-17 | A live GUI estimate is written to SQLite and appears in history immediately. `persisted` is computed by re-reading `load_estimation(run_id)`; when the read-back finds no row, the window shows 「未寫入（僅檔案）」 and history does not contain it. | A | `test_application_estimation.py::test_live_uses_a_repository_and_writes_the_run`, `::test_persistence_failure_is_reported_not_hidden`, `test_gui_estimation_view.py::test_live_mode_goes_through_the_service_and_reports_persisted` | pass |



## Stage C — Backtesting GUI



| ID | Acceptance item | Method | Evidence | Status |

| --- | --- | --- | --- | --- |

| C-01 | Form maps every `StrategySpec` field (spec id, universe, dates, capital, weighting, rebalance, target weights, momentum window, drift, benchmark, costs) | A | `tests/test_gui_backtest_form.py::FormMappingTests::test_every_spec_field_is_mapped` (14 OK) | pass |

| C-02 | Weighting changes enable/disable target weights and momentum window | A | `tests/test_gui_backtest_view.py::WindowValidationTests::test_weighting_switch_enables_the_right_fields` | pass |

| C-03 | Invalid input raises `BacktestSpecError` and no run is created | A | `…::test_invalid_spec_creates_no_run` (`state=idle`, no thread) | pass |

| C-04 | The form performs no duplicated validation — rules live in `StrategySpec` | R | `…::test_engine_validation_is_not_duplicated` + `v24_evidence/C-stage.md` §2 | pass |

| C-05 | Form JSON round trip preserves every field, including the default/custom cost distinction | A | `…::JsonRoundTripTests::test_form_to_json_and_back_preserves_the_sentinel` | pass |

| C-06 | Running a valid spec stores a run; the summary equals `run_backtest` output | A | `…::MetricsTests::test_c06_metrics_render_from_the_same_run_the_service_returns` | pass |

| C-07 | Displayed metrics equal `load_backtest_run(run_id).metrics` after reload | A | `…::test_c07_metrics_match_the_stored_row` | pass |

| C-08 | `created=False` is shown as "Existing Run Reused" with the same run id | A | `…::SummaryTests::test_reused_run_says_so` | pass |

| C-09 | Equity table and canvas use the persisted `equity_curve` — 7 columns (session, cash, holdings, total, exposure, benchmark, drawdown) | A | `tests/test_gui_backtest_view.py::TableTests::test_c09_equity_rows_have_seven_columns_including_drawdown` — drawdown checked against `load_backtest_run()` | pass |

| C-10 | Trade table shows price source, FX and FX source plus the three cost legs | A | `…::test_c09_trade_rows_carry_every_cost_leg` | pass |

| C-11 | Anomalies panel lists the run's anomalies in order; empty state 無異常 | A | `…::test_c11_anomalies_keep_their_order_and_have_an_empty_state` | pass |

| C-12 | `BacktestSpecError`, `BacktestDataMissingError`, `BacktestDataIntegrityError`, `DatabaseError` are shown with class name and message | A | `…::WindowValidationTests::test_c12_all_four_error_classes_are_shown` | pass |

| C-13 | A second run while one is running is refused (`state="running"`) | A | `…::WindowTests::test_second_submit_is_refused_while_running` | pass |

| C-14 | UI stays responsive during a run (poll loop keeps running) | A | Covered by Stage E: `v24_evidence/E-06.md` (`progress → progress → result`, exactly one terminal result) and `v24_evidence/E-01.md` (the window answered throughout the run); the Stage C scripted run recorded 423 heartbeats with max 27 ms lateness (`C-stage.md` §3) | pass (covered by E-01/E-06) |

| C-15 | Closing during a run waits at most 2 s; worker never touches a destroyed widget (T-7) | A + R | `tests/test_gui_lifecycle.py::BacktestCloseBudgetTests::test_gui_close_during_run_bounded_wait` | pass |

| C-16 | Engine/metrics/runner files unchanged (SHA-256) | R | `v24_evidence/C-16.json` — 8/8 engine files byte-identical | pass |

| C-17 | Real desktop run on the working database produces a readable summary | A | `v24_evidence/E-01.md` §2 — real 000369 backtest on the working database, `created=False` reuse, summary and every result tab rendered; `v24_evidence/C-17_report.json` + `C-17_running.png` | pass (covered by E-01) |

| C-18 | The run created in C-17 is traceable: run id → inputs → fingerprint shown in the GUI | A | `v24_evidence/E-01.md` §2/§4 — run `2b450179…`, fingerprint visible, spec id/period/capital shown; `C-17_report.json` (`fingerprint_visible: true`) | pass (covered by E-01) |

| C-19 | Cost mode and runtime configuration: default keeps `COST_MODEL_DEFAULT`, custom builds an explicit `CostModel`; runtime panel read-only | A | `…::RuntimePanelTests`, `…::SentinelTests`, `…::test_cost_mode_switch_enables_the_cost_entries` | pass |



## Stage D — Reports and history



| ID | Acceptance item | Method | Evidence | Status |

| --- | --- | --- | --- | --- |

| D-01 | Backtest renderer moved to `fa/reporting/backtest_report.py`; CLI imports the moved renderer and its output is unchanged | A | `tests/test_reports_backtest.py::RendererMoveTests::test_d01_cli_imports_the_reporting_renderer`, `…::test_d01_render_is_the_shared_text` | pass |

| D-02 | `scripts/backtest_report.py` still runs `list` (and imports `render`) unchanged | A | `tests/test_reports_backtest.py::RendererMoveTests::test_d02_cli_write_report_matches_the_renderer`, `…::test_d02_cli_list_and_show_still_read_stored_runs` | pass |

| D-03 | GUI report content equals `render_backtest(stored run)` for the same run id | A | `tests/test_reports_history.py::HistoryWindowTests::test_d03_window_lists_and_renders_the_stored_run` | pass |

| D-04 | Rendering a stored run never triggers a backtest or the estimator | A | `tests/test_application_reporting.py::ReportingTestCase::test_d04_rendering_never_runs_a_backtest_or_the_estimator` | pass |

| D-05 | Estimation report: NAV, coverage, asset contributions, attribution, quality, warnings, provenance | A | `tests/test_reports_estimation.py::EstimationReportTests` (8 tests) | pass |

| D-06 | History lists equal the service queries; filter by fund/strategy, service order kept | A | `tests/test_reports_history.py::HistoryRowMappingTests`, `tests/test_application_reporting.py::…::test_d06_history_lists_and_filters` | pass |

| D-07 | Export writes `output/reports/<run_id>.md` with exactly the rendered text | A | `tests/test_reports_history.py::HistoryWindowTests::test_d07_export_button_writes_the_same_text`, `tests/test_application_reporting.py::…::test_d07_export_backtest_matches_the_renderer` | pass |

| D-08 | Estimation export writes `output/reports/<run_id>_estimate.md` | A | `tests/test_application_reporting.py::…::test_d08_export_estimation_matches_the_renderer` | pass |

| D-09 | History is immutable: table row hashes unchanged before/after browsing and exporting | A | `tests/test_application_reporting.py::…::test_d09_browsing_and_export_do_not_change_rows` | pass |

| D-10 | Real desktop: open an old run from history, export it and open the file | M | `docs/development/v24_evidence/D-10.md`; export proven in `v24_evidence/E-10.md` (3,381 B backtest + 3,140 B estimate report) | pending (environment only: this VM has no `.md` association, so the exported files were verified by size/content, not by double-click) |

| D-11 | `Repository.list_backtest_runs(spec_id=None, limit=50)`: documented columns, `created_at DESC, run_id DESC`, limit 1..200, spec filter, read-only, consistent with `load_backtest_run` | A | `tests/test_reports_history.py::HistoryQueryTests` (5 tests) | pass |



## Stage E — Integration and acceptance



| ID | Acceptance item | Method | Evidence | Status |

| --- | --- | --- | --- | --- |

| E-01 | (Stage E checklist item — see `v24_evidence/E-01.md` §2) | A + M | `v24_evidence/E-01.md` — scripted session, 6.937 s first cold analysis (3.375 s cache hit), 13 files, QDII persisted, backtest 2 trades, 2 exports, 0.265 s close | pass |

| E-02 | Cold start: three fresh starts, median ≤ 3 s | A + M | `v24_evidence/E-02.md` §6 — same driver, readings 1.494 / 1.649 / 1.547 s, **median 1.547 s** (pre-fix fail 6.835 s kept in §5; import 0.46–0.50 s, build 1.32–1.46 s) | pass |

| E-03 | (Stage E checklist item — see `v24_evidence/E-03.md` §2) | A + M | `v24_evidence/E-03.md` — 1024×768 and 1920×1080 screenshots | pass |

| E-04 | (Stage E checklist item — see `v24_evidence/E-04.md` §2) | A + M | `v24_evidence/E-04.md` — 45-char ticker / 96-char name, 3 rows, no crash | pass |

| E-05 | (Stage E checklist item — see `v24_evidence/E-05.md` §2) | A + M | `v24_evidence/E-05.md` — date/pct/dash spot check + 23 formatter tests | pass |

| E-06 | (Stage E checklist item — see `v24_evidence/E-06.md` §2) | A + M | `v24_evidence/E-06.md` — `progress → progress → result`, 1 terminal result | pass |

| E-07 | (Stage E checklist item — see `v24_evidence/E-07.md` §2) | A + M | `v24_evidence/E-07.md` — close 0.203 s while running, no orphan process (Task Manager substituted by the same process query) | pass |

| E-08 | (Stage E checklist item — see `v24_evidence/E-08.md` §2) | A + M | `v24_evidence/E-08.md` — reopen keeps only the main window; empty analysis list; 33 stored runs at first open, 35 after two new runs (reopen) | pass |

| E-09 | (Stage E checklist item — see `v24_evidence/E-09.md` §2) | A + M | `v24_evidence/E-09.md` — real 「請輸入基金代碼」 dialog screenshot | pass |

| E-10 | (Stage E checklist item — see `v24_evidence/E-10.md` §2) | A + M | `v24_evidence/E-10.md` — history window + 2 exported reports (3,381 B / 3,140 B) | pass |

| E-11 | Full regression: `unittest discover` green, `compileall` 0, `git diff --check` 0 | A | `v24_evidence/E-11.md` — `Ran 1131 tests in 130.230s OK`, compileall 0, diff-check 0 (incl. `tests/test_gui_startup.py` intent-to-add) | pass |

| E-12 | Core coverage ≥ 90 % per module (`scripts/coverage_core.py`) and all V2.3.2 acceptance tests still pass | A | `v24_evidence/E-12.md` — 704/708 = 99.4 % (all 10 core modules ≥ 90 %), BT regression 423 tests OK | pass |

| E-13 | V2.2 public interfaces unchanged; `MIGRATION_001` identical to the V2.2 baseline and 002–004 additive (hashes recorded) | R | `v24_evidence/E-13.md` — `MIGRATION_001` hash identical, 002–004 additive; only defaulted keyword-only additions | pass |



## Status vocabulary



`pending` — not yet evidenced. `pass` — evidence present and reviewed.

`fail` — evidence contradicts the criterion; the stage cannot be accepted.

`n/a` — removed by an approved scope change, with the approval recorded.



## Evidence rules



1. Every **A** row names (a) the test module and the **specific test method**,

   (b) the command that runs it, (c) the result line for that run. A green full

   suite alone never marks a row `pass`.

2. If a row's test failed and was fixed, the matrix records the failing run and

   the fix reference (file + change) next to the row.

3. Every **M** row cites a file under `docs/development/v24_evidence/` with

   date, machine, steps, expected vs observed and screenshot names.

4. Every **R** row records what was reviewed or which files were hashed, and the

   SHA-256 values themselves.

5. A stage's rows are re-checked at the **next** stage's start; a regression

   flips the earlier row back to `fail`.
