# V2.4 — GUI Integration Technical Specification

Status: **approved and frozen baseline** (Stage 0 accepted 2026-11-20, round 3
review). Implementation status is tracked in `07_development_roadmap.md`; this
document is the frozen contract for Stages A–E.

Related: `00_overview.md` (§7 index), `05_module_interfaces.md`,
`06_testing_and_acceptance.md`, `07_development_roadmap.md` (§V2.4),
`10_v232_backtest_engine.md` (§7 metrics, §8 runner, §4.3 schema).

## 1. Goal

Integrate the finished V2 estimation engine and the finished V2.3.2 backtest
engine into the existing desktop application so that a user can do everything
without touching a CLI, a JSON file or SQLite directly:

1. run a fund analysis (already exists) and keep the existing reports,
2. see the **full** QDII estimation result, including its quality, provenance
   and per-asset detail,
3. browse past estimation runs,
4. configure and run a backtest from a form,
5. read the backtest result (metrics, trades, equity curve, anomalies),
6. browse stored backtest runs and produce reports from stored data.

The application is **Windows-first**; the GUI acceptance criteria are part of
the formal acceptance (see `docs/development/v24_acceptance_matrix.md`).

## 2. Non-goals (scope fence)

V2.4 is a GUI and integration stage, not a new engine stage:

- no change to backtest engine / cost / metrics semantics;
- no change to PIT price or FX reconstruction (`prices_as_of`, `fx_as_of`);
- no new migration; Migrations 001–004 stay as accepted;
- no change to the NAV estimation formula or to `EstimationResult` fields;
- no intraday estimation, no strategy optimisation, walk-forward, automated
  trading, scheduled alerts or new asset classes;
- no replacement of Tkinter (see §2.1);
- the only database-layer change permitted in V2.4 is the **additive, read-only
  `Repository.list_backtest_runs()`** (§4.6); any other schema or API change
  must be justified and reviewed before being implemented.

### 2.1 Framework decision (confirmed by the lead, 2026-11-20)

Keep Tkinter and integrate progressively.

Reasons: the main window already has a working worker/queue/log lifecycle and a
usable QDII window; the engines are finished and only need an adapter and a
presentation layer; replacing the toolkit would add risk that has nothing to do
with the financial correctness the project has spent its review effort on.

## 3. Baseline audit (Stage 0 evidence)

### 3.1 `fa/gui/app.py` (526 lines)

- `FundAnalyzerGUI.__init__` builds the Tk root (title 「Fund Analyzer —
  基金分析工具」, 860×680, minsize 720×560), `log_queue`, `result_queue`,
  thread registry and lifecycle flags.
- UI: parameter frame (fund code, risk-free rate, benchmark, output dir, start
  date, benchmark market), button bar (開始分析, 盘后监控, QDII 淨值估算,
  打开综合报告, 打開輸出目錄, 忽略緩存), log pane, result list, status bar.
- Threading: `start_analysis` → `threading.Thread(daemon=True)` →
  `_run_analysis` → `result_queue`; `_poll_result_queue` / `_poll_log_queue`
  run on `root.after(100, …)`; `QueueHandler` pipes `fa.*` log records.
- Lifecycle: `shutdown()` cancels the two `after()` ids, closes QDII and
  post-market windows, joins the worker (2 s), destroys the root.
- Buttons depending on results are disabled until the analysis succeeds; QDII
  button is enabled only when `summary["analysis_mode"] == "qdii"`.
- Report entry: `generate_master_report(...)` + `pywebview` (falls back to the
  browser). `webview.start()` is **blocking and must run on the main thread** —
  see risk R-03.

### 3.2 `fa/gui/qdii_estimate.py` (239 lines)

- `QdiiEstimateWindow(master, fund_code, save_dir, estimate=None, on_close=None)`.
- Shows headline (估算漲跌 / 無法估算（status）), ten summary fields (official,
  estimated, change, coverage, uncovered, fx, local, target date, disclosure
  date, market date), one note line, and a per-holding table with columns
  ticker / name / weight / currency / local / fx / cny / contribution.
- `refresh()` runs `estimate_qdii_nav(fund_code, save_dir)` in a worker and
  polls `after(200, …)`; `close()` cancels the poll, joins 2 s, destroys.
- **Missing for V2.4**: run id, model version, look-ahead status text, data
  quality block (sources, staleness, calendar fallback, missing data), per-asset
  price dates and status column, asset type, history/browsing, and any notion of
  "this window shows a stored run" — the window always recomputes.

### 3.3 `fa/gui/post_market.py` (217 lines)

- `PostMarketWindow(master, fund_code, save_dir, on_close=None)`; refreshes
  every 60 s (`after(60000, refresh_data)`), one in-flight fetch thread,
  poll every 1 s, `close()` cancels both timers. **Keep as is** (V2.4 only
  registers it in the window registry of Stage A).

### 3.4 Backtest surface

- Engine API: `run_backtest(spec, repository, *, now=None, force=False) ->
  (BacktestRun, created)`; `BacktestRun` carries `run_id`, `spec_id`,
  `model_version`, `start`, `end`, `base_currency`, `universe_json`,
  `spec_json`, `costs_json`, `data_fingerprint`, `look_ahead_status`,
  `created_at`, `metrics`, `trades`, `equity_curve`, `anomalies`.
- CLI only: `scripts/backtest_report.py` (`run` / `list` / `show`) renders a
  Markdown report **from the persisted run**; `render(run)` is importable but
  lives in a script, not in `fa/`.
- No GUI exists for backtests.

### 3.5 Persistence interfaces used by the GUI

| Stage | Call | Purpose | Thread |
| --- | --- | --- | --- |
| B live | `estimate_qdii_nav(..., repository=repo)` | estimate **and** write the immutable run | worker |
| B live | `Repository.list_estimations(fund_code=None, limit=50)` | refresh history after a run | worker |
| B stored | `Repository.load_estimation(run_id)` | run row + result summary | worker |
| B stored | `Repository.estimation_inputs(run_id)` | per-asset input snapshot (see G-05) | worker |
| B stored | saved JSON record (optional) | final per-asset rows incl. `contribution` | worker |
| C | `run_backtest(spec, repo, force=…)` | run and persist a backtest | worker |
| C | `Repository.count_backtest_runs()` | footer counter | worker |
| C/D | `Repository.load_backtest_run(run_id)` | rebuild one stored run | worker |
| D | `Repository.list_estimations(...)` | estimation history list | worker |
| D | **`Repository.list_backtest_runs(spec_id=None, limit=50)`** (new, §4.6) | backtest history list | worker |
| D | `run_backtest` | **must never be called by the report/history code** | — |

The GUI never calls `save_estimation`, never opens `sqlite3` itself, and never
queries on the Tk main thread.

### 3.6 Gaps this specification closes

| ID | Gap |
| --- | --- |
| G-01 | No navigation for the V2 features; only one analysis form and two child windows. |
| G-02 | The QDII window recomputes on every open; no "show stored run" mode and no run history. |
| G-03 | No run id / model version / look-ahead status / quality block in the QDII window. |
| G-04 | No per-asset price dates, asset type or per-asset status. |
| G-05 | Historical per-asset data exists in **different forms**, not as one normalized table (see below). |
| G-06 | No backtest GUI at all. |
| G-07 | No report/report-history centre; the only report entry is the analysis master report. |
| G-08 | No persisted-run report (the CLI `render` is not part of `fa/`). |
| G-09 | No shared worker helper: each window re-implements thread + queue + poll + close. |
| G-10 | No shared formatting module (percentages, CNY, dates are formatted ad hoc). |
| G-11 | No window registry: adding windows means touching `shutdown()` by hand. |
| G-12 | `webview.start()` blocks the main loop (risk R-03, not a Stage A defect). |
| G-13 | No `Repository.list_backtest_runs()`: only `count_backtest_runs()` and `load_backtest_run(run_id)` exist, so a history list has no clean read path. |
| G-14 | The GUI's current live estimate path calls `estimate_qdii_nav(fund_code, save_dir)` **without a repository**, so the run is written to JSON/CSV but not to SQLite and never appears in `list_estimations`. |

**G-05 — Historical per-asset data is stored in different forms.**

SQLite has no normalized per-asset estimation-result table. Two different data
sources exist, and they do not carry the same fields:

| Source | What it contains |
| --- | --- |
| `estimation_inputs(run_id)` (SQLite, `estimation_inputs.payload_json`) | the input snapshot built by `_inputs_snapshot()`: `fund_code`, `model_version`, `config`, `base_nav_date`, `target_nav_date`, `base_nav`, `holdings` (quarter / holding date / disclosure date / source / scope / total weight) and `assets[]` with ticker, name, `asset_type`, market, currency, `weight`, `base_price_date`/`base_price`, `target_price_date`/`target_price`, `base_fx_date`/`base_fx`, `target_fx_date`/`target_fx`, `status`, `local_return`, `fx_return`, `return_cny`, `cash_rate`, `warnings`. **No `contribution` column.** |
| per-run inputs snapshot (`output/<fund>/<fund>_qdii_estimate_inputs.json`) | `{"fund_code", "updated_at", "snapshots": {<run_id>: <record>}}`; each entry is the **whole record of that run**, so `snapshots[run_id]["holdings"]` carries `contribution` and the legacy final field names. This is the per-run supplement. |
| latest-result file (`output/<fund>/<fund>_qdii_estimate.json`) | the full record of whichever run finished **last**; overwritten on every run, so it can belong to a different run than the one being viewed. |
| run history CSV (`output/<fund>/<fund>_qdii_estimates.csv`) | one summary row per run (deduped on `run_id`); never a source for per-asset detail. |

Therefore:

- historical asset tables load **primarily** from `estimation_inputs(run_id)`;
- the per-run snapshot file
  `output/<fund>/<fund>_qdii_estimate_inputs.json` is the primary supplement:
  `snapshots[run_id]` is the whole record of that run, so `contribution` comes
  from it rather than from a latest-result file that may belong to another run;
- the latest-result `_qdii_estimate.json` is consulted **only** when its
  `run_id` equals the selected run;
- a field absent from both sources is displayed as `—` and never recomputed in
  the GUI;
- when neither source yields usable per-asset data, the window shows
  「此執行未保存可用資產明細（僅存摘要）」 instead of an empty table that could be
  mistaken for a zero portfolio.

## 4. Target architecture

### 4.1 Module layout

New application layer (no Tk imports, no SQL):

| Module | Responsibility |
| --- | --- |
| `fa/application/__init__.py` | exports the request/response dataclasses |
| `fa/application/estimation.py` | `run_estimate(fund_code, save_dir, *, as_of=None, repository_path=None) -> EstimationRecord`; opens/closes the repository **inside the worker thread**, calls `estimate_qdii_nav(..., repository=repo)`, then establishes the outcome by **reading the run back**: `run_id = record.get("run_id")`, `persisted = bool(run_id) and repo.load_estimation(run_id) is not None` (the estimator discards `_save_to_database()`'s return value, so it cannot be observed); `load_estimate(run_id, repository_path)`, `list_estimates(...)` |
| `fa/application/backtesting.py` | `run_strategy(spec, repository_path, *, force=False) -> (BacktestRun, bool)`; `load_run(run_id)`, `list_runs(...)`, `count_runs()` — owns the repository lifecycle |
| `fa/application/reporting.py` | `estimation_report(run_id)`, `backtest_report(run_id)`; returns rendered text; read-only |

New GUI modules:

| Module | Responsibility |
| --- | --- |
| `fa/gui/navigation.py` | `MainNotebook` with the four areas and the area registry; no business logic |
| `fa/gui/worker.py` | `WorkerBridge`: run `fn(*)` in a daemon thread; publish zero or more progress events and exactly one terminal result through `queue.Queue`; poll via `after()` on the Tk main thread; cancel polling on close and discard every late message |
| `fa/gui/formatting.py` | `pct`, `money`, `number`, `date_text`, `status_text`, `quality_text` — pure functions, unit-testable |
| `fa/gui/backtest_window.py` | Stage C window: form → `StrategySpec` → application service → result panels |
| `fa/gui/history_window.py` | Stage D window: estimation history + backtest history + report export |

New reporting modules (Stage D):

| Module | Responsibility |
| --- | --- |
| `fa/reporting/backtest_report.py` | `render(run) -> str`, moved out of `scripts/backtest_report.py` unchanged |
| `fa/reporting/estimation_report.py` | `render(record, inputs) -> str` for a stored estimation run |
| `fa/reporting/__init__.py` | exports |

Modified modules:

| Module | Change |
| --- | --- |
| `fa/gui/app.py` | Stage A: build the notebook, keep the analysis tab as today, register child windows, expose the four areas |
| `fa/gui/qdii_estimate.py` | Stage B: full result view, quality block, stored-run mode; keeps the class name and constructor signature (`estimate=` still accepted) |
| `fa/data/database/repository.py` | **one additive read-only method**: `list_backtest_runs()` (§4.6). No migration, no write path change. |
| `scripts/backtest_report.py` | Stage D: `render` imported from `fa.reporting.backtest_report`; CLI behaviour unchanged |

Untouched: `fa/backtesting/*` (engine, metrics, runner, costs, models,
data_access, benchmark), `fa/analysis/*`, all other `fa/data/*` modules, and all
migrations.

### 4.2 Layering rules

1. GUI reads and writes **only** through `fa/application/*`; it never imports
   `Repository`, `sqlite3`, `estimate_qdii_nav` or `run_backtest` directly.
2. The application layer owns repository creation and closing, and does so
   inside the worker thread (one repository per operation, closed in `finally`).
3. GUI never recomputes a financial formula. It may format numbers and decide
   which stored field to show.
4. No widget is touched outside the Tk main thread.
5. No network or database access on the main thread.
6. A GUI action that writes goes through the same public functions the CLI uses
   (`estimate_qdii_nav`, `run_backtest`) — the application layer adds lifecycle,
   not business rules.
7. The reporting layer is read-only: it may load a stored run but never calls
   `run_backtest` or the estimator.

### 4.3 Threading contract

| Rule | Requirement |
| --- | --- |
| T-1 | Tk objects are created, configured and destroyed only on the main thread. |
| T-2 | Work runs in `threading.Thread(daemon=True)`; the worker function returns a value or raises. |
| T-3 | Results cross threads only through a `queue.Queue`. Each action produces exactly **one terminal result** (`kind="result"`: the value or the error) plus zero or more **progress events** (`kind="progress"`). A progress event never carries a final result; the UI discards every message received after `_closed`. |
| T-4 | The UI polls with `after(interval, …)`; the poll id is stored and cancelled on close. |
| T-5 | A window refuses a second concurrent action while one is running (`state="running"`). |
| T-6 | `close()` sets `_closed = True` **before** cancelling timers. Late worker messages are discarded and must never update a destroyed widget. |
| T-7 | `close()` waits for the worker for at most 2 s. Python threads are not force-killed: if the operation has no cooperative cancellation, the daemon worker may continue until its current operation returns. The application must never claim that the operation was terminated because the window closed. |
| T-8 | A worker never calls `messagebox`, `update()`, `after()` or any widget method; errors are returned as data. |
| T-9 | A worker holds no Tk widget reference; the only inputs are plain data (paths, specs, ids). |
| T-10 | The application layer opens the repository inside the worker and closes it in `finally`; no connection outlives its operation. |
| T-11 | Where the underlying operation supports cooperative cancellation (a provider flag, a loop guard), the worker checks it between units of work; where it does not, this is documented per operation and is not a closing guarantee. |

Consequences for acceptance: closing a window must be **testable** in terms of
"no timers, no late UI update, no widget access", and in terms of "the
application process exits"; it must not promise that an uncancellable worker has
already stopped.

### 4.4 State model

Each area exposes a state machine:

```
idle -> running -> done(status) | failed(error)
done(status) -> running        (re-run)
```

`status` for an estimate is `PortfolioStatus` (`estimated` / `partial` /
`insufficient-data` / `missing-fx`); for a backtest it is the pair
`(created: bool, run_id)`. The UI must render "Existing Run Reused" when
`created is False`.

### 4.5 Formatting rules

- Percentages: two decimals, explicit sign for returns (`+0.19%`), one decimal
  for coverage.
- Money: two decimals with thousands separators; CNY suffix in headers, not in
  every cell.
- Dates: ISO `YYYY-MM-DD`; datetimes shown as local time with the zone in the
  header.
- Missing values: `—`, never `0`, never blank; `N/A` only in the legacy table.
- Colours: red for positive, green for negative (existing convention in
  `qdii_estimate.py`), muted grey for missing. Colour never carries information
  that text does not.

### 4.6 New Repository API: `list_backtest_runs()`

```python
def list_backtest_runs(
    self,
    spec_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
```

Requirements:

- read-only, `SELECT` only; no migration; no write path touched;
- SQL bound parameters, no string interpolation;
- `limit` is clamped to `1..200` (default 50) — the caller cannot request an
  unbounded scan;
- deterministic ordering: `created_at DESC, run_id DESC`;
- optional `spec_id` filter;
- returns one dict per run with at least: `run_id`, `spec_id`, `model_version`,
  `start`, `end`, `base_currency`, `created_at`, `look_ahead_status`,
  `data_fingerprint`, `sessions`, `trades`, `cagr`, `max_drawdown`,
  `total_costs_base`;
- never calls `run_backtest`, never recomputes metrics — values come from
  `strategy_backtest_runs` joined with `strategy_backtest_metrics`;
- unit tests in Stage D prove the columns, the ordering, the clamp, the filter
  and that a rendered row equals `load_backtest_run(run_id).metrics` for the
  same fields.

## 5. Stage A — GUI foundation and navigation

### 5.1 Layout

`FundAnalyzerGUI` keeps its title and minimum size. A `ttk.Notebook` fills the
window with four tabs:

| Tab key | Title | Content |
| --- | --- | --- |
| `analysis` | 基金分析 | the current parameter frame, log pane and result list (unchanged behaviour) |
| `estimation` | QDII 估算 | Stage B view (Stage A: placeholder with an explanatory label) |
| `backtest` | 策略回測 | Stage C view (Stage A: placeholder) |
| `reports` | 報告與歷史 | Stage D view (Stage A: placeholder) |

The log pane and status bar remain shared by all tabs (bottom area). The
post-market window remains a child of the analysis tab's data.

### 5.2 Registry and lifecycle

- `MainNotebook.register_window(key, factory)`; `app.shutdown()` iterates the
  registry instead of hard-coding `_qdii_window` / `_post_market_window`.
- Re-opening an already open child window raises the existing one
  (`lift()`, `focus_force()`), as `open_qdii_window` does today.
- Every area must be closeable and re-openable in one session; a closed area
  must not leave a timer behind (threads follow T-7/T-10).

### 5.3 Acceptance

See matrix Stage A (A-01…A-10): main window opens/closes; each tab renders; each
child window opens, re-opens and closes; UI remains responsive during a worker
run; no widget access from a worker; the post-market window still works;
`python -m unittest` stays green.

## 6. Stage B — Estimation integration

### 6.1 Window and modes

`QdiiEstimateWindow` gains an explicit mode:

- `mode="live"` (default): calls the application service
  `run_estimate(fund_code, save_dir, as_of=…)`, which opens a repository in the
  worker, calls `estimate_qdii_nav(fund_code, save_dir, repository=repo, ...)`
  and **therefore persists the run**. The estimator's `_save_to_database()`
  swallows database errors and its return value is discarded, so the service
  must not rely on it: persistence is established by reading the run back
  (`repo.load_estimation(run_id) is not None`). A run that cannot be read back
  is shown with 「未寫入資料庫」 and a warning, and is not listed in history;
- `mode="stored"` + `run_id`: calls `load_estimate(run_id)` and renders the
  stored run without recomputation.

The constructor keeps `estimate=` so the analysis path can hand over the fresh
record as today. The live path **must not** fall back to the old
`estimate_qdii_nav(fund_code, save_dir)` call without a repository — that path
writes no run and is exactly gap G-14.

### 6.2 Summary panel

Fields (all from the record; no computation in the GUI):

| Field | Source key | Missing rendering |
| --- | --- | --- |
| 基金代碼 | `fund_code` | — |
| 官方淨值 | `base_nav` | — |
| 估算淨值 | `estimated_nav` | — |
| 估算漲跌 | `estimated_return` | — |
| 估算基準日 | `target_nav_date` | — |
| 淨值基準日 | `base_nav_date` | — |
| 持倉覆蓋率 | `coverage_ratio` | — |
| 未覆蓋權重 | `uncovered_weight` | — |
| 資料品質 | `data_quality` | — |
| 前視偏差狀態 | `look_ahead_status` (stored) / `look_ahead_safe` (record) | — |
| 模型版本 | `model_version` | — |
| 執行 ID | `run_id` | — |
| 寫入資料庫 | `persisted` (bool, live mode only; computed by re-reading the run from SQLite) | 顯示「未寫入（僅檔案）」 |

### 6.3 Asset table

Columns: ticker, name, asset type, market, currency, weight, base price date,
target price date, local return, FX return, CNY return, contribution, status.
A row whose status is not `ok` shows its status text in the CNY/contribution
cells instead of a number; a row with an unknown weight must not be shown as a
zero return. Contribution is shown only when the loaded source provides it
(inputs normally do not, JSON normally does); otherwise `—`.

### 6.4 Data-quality block

Below the table, a labelled, multi-line block showing:

1. holdings quarter, holdings scope (`top10` / `full_portfolio`) and disclosure
   date with `（推算）` when `disclosure_date_estimated`;
2. price source and price type actually used (`available_price_type` /
   `used_price_type` where the source carries them);
3. missing/stale data counts derived from the source's `warnings` and per-asset
   statuses (displayed verbatim, never summarised away);
4. calendar fallback warnings;
5. look-ahead status text (`verified-safe` / `estimated-disclosure` / `unsafe` /
   `unknown`);
6. uncovered weight and the fact that it is not estimated.

### 6.5 Historical estimation browsing

Inside the estimation tab: a list (`application.estimation.list_estimates`)
with columns fund code, target NAV date, status, estimated NAV, coverage,
quality, run id; selection loads the summary fields and the input snapshot.
All loading happens in a worker; the UI only renders the returned data.

Asset detail resolution order (see G-05):

1. `load_estimation(run_id)` → run row + result summary (always available);
2. `estimation_inputs(run_id)` → `assets[]` (per-asset inputs, dates, FX,
   status, returns; **no contribution**);
3. `output/<fund>/<fund>_qdii_estimate_inputs.json` → `snapshots[run_id]`
   (the whole record of that run) supplements `contribution` and legacy final
   fields;
4. latest `output/<fund>/<fund>_qdii_estimate.json` only when its `run_id`
   matches the selected run (otherwise it belongs to another run);
5. any field still missing → `—`;
6. neither source usable → 「此執行未保存可用資產明細（僅存摘要）」.

The view must label which sources were used (`SQLite 輸入快照` /
`+ 執行快照 JSON` / `+ 最新 JSON（run_id 相同）`) so a reader can tell a stored
contribution from a missing one.

### 6.6 Acceptance

See matrix Stage B (B-01…B-17): window matches `EstimationResult`; no formula in
the GUI; per-asset dates and statuses correct; `partial` / `insufficient-data` /
`missing-fx` visually distinct; stored runs never overwritten; live runs appear
in history (B-17); run id, model version and look-ahead status visible; history
list matches the service; both missing-detail sources are covered.

## 7. Stage C — Backtesting GUI

### 7.1 Form

Fields map 1:1 onto `StrategySpec`; the form performs **no** validation beyond
type conversion and delegates every rule to `StrategySpec` (a `BacktestSpecError`
is shown inline next to the offending field group).

Strategy id, universe rows (ticker / market / currency), start, end, initial
capital, weighting, rebalance frequency + anchor, target weights (enabled only
for `fixed`), momentum window (enabled only for `momentum`), drift threshold,
benchmark ticker, cost model, and run-now. The form exposes **only** fields of
`StrategySpec`: `fractional_shares`, `decision_lag_minutes`,
`execution_lag_sessions` and `risk_free_rate` are **not** form inputs — they
live in `RunConfig`, are resolved from `FA_BACKTEST_*` by `resolve_config()`,
and appear read-only in the runtime panel after the run (C-19).

**Cost model semantics (must be preserved).** `StrategySpec.costs` distinguishes
"let the environment decide" from "use exactly these numbers" by object
identity: `COST_MODEL_DEFAULT` means "resolve from `FA_BACKTEST_*`"; an explicit
`CostModel(...)` — even one equal to the defaults — is honoured as given. The
form therefore offers two modes:

| Form mode | Constructed spec |
| --- | --- |
| 使用系統預設成本 | leave `costs` at its default (`COST_MODEL_DEFAULT`); the environment resolves it |
| 自訂成本 | `CostModel(commission_rate=…, min_commission=…, slippage_bps=…, fx_spread_bps=…)` |

The form must never build `CostModel()` in the "default" mode: that would pin
the current numbers and silently ignore `FA_BACKTEST_COMMISSION_BPS` and
friends.

The form can be loaded from and saved to JSON (reusing the payload shape of
`scripts/backtest_report.py::spec_from_json`); the JSON round trip must preserve
the default/custom distinction (a `"costs": null` or absent key means default).

### 7.2 Execution

`application.backtesting.run_strategy(spec, repository_path, force=?)` in a
worker; the service opens the repository, calls `run_backtest`, closes it. The
UI shows Running → Completed / Failed and, when `created is False`, the badge
"Existing Run Reused". Errors from `BacktestSpecError`, `BacktestDataError`
(incl. the missing/integrity split) and `DatabaseError` are shown with their
class name and message.

**Cost-source metadata returned by the service.** Before calling
`run_backtest()` the service records the submitted request's cost mode:

```python
request_cost_source = "default" if spec.costs is COST_MODEL_DEFAULT else "custom"
```

It returns `(run, created, request_cost_source)`. `request_cost_source`
describes **only that request**: it is not a `StrategySpec` field, not a
`RunConfig` field and **not persisted**. The persisted `BacktestRun` remains
authoritative for the effective runtime settings and cost values. When
`created is False` the service must not present `request_cost_source` as the
stored run's original source — that run may have been created by an earlier
request with a different mode — and it must never reconstruct a historical
source mode from numerical equality.

### 7.3 Result view

Summary panel: run id, strategy id, period, initial capital, model version, data
fingerprint (truncated with a copy button), look-ahead status, anomalies count.

**Runtime configuration panel (read-only).** The panel displays the effective
runtime configuration from the persisted `spec_json["runtime"]` and
`costs_json`: decision lag minutes, execution lag sessions, fractional shares,
risk-free rate, commission rate, minimum commission, slippage bps and FX
spread bps. The effective cost **values** are a saved fact; the cost **source
mode** is not persisted (`costs_payload()` and `RunConfig.as_dict()` store
numbers only, and `spec.costs is COST_MODEL_DEFAULT` leaves no trace), so the
two are displayed separately and never conflated.

| Situation | What the panel shows |
| --- | --- |
| Newly created run (`created=True`) | The effective values from the persisted run, plus the submitted request's mode from service metadata (`使用系統預設成本` when `spec.costs is COST_MODEL_DEFAULT`, otherwise `自訂成本`), labelled as this request's mode. |
| Reused run (`created=False`) | The effective values from the persisted run, plus 「成本來源未持久化（此紀錄無法確認原始模式）」. The current request's mode is not shown as the run's source. |
| Historical run (loaded by run id) | The effective values from the persisted run, plus 「成本來源未持久化（此紀錄無法確認原始模式）」 unless an explicitly persisted source mode exists (none in V2.4). |

The GUI must never infer the source mode by comparing the stored numbers with
the current environment or with `CostModel()` defaults. The form stays
read-only with respect to `RunConfig`: it exposes neither decision lag,
execution lag, fractional shares nor risk-free rate as inputs.

Metrics table: sessions, trades, CAGR, annualised volatility, Sharpe, Sortino,
max drawdown, Calmar, turnover, hit rate, exposure, cost drag, total costs.

Equity panel: table (session, cash, holdings, total, exposure, benchmark,
drawdown) plus a Tk `Canvas` line chart of total and benchmark (no new
dependency; 600×220 px).

Trades panel: session, ticker, side, quantity, price, price source, FX, FX
source, gross, commission, slippage, FX spread, net, reason.

Anomalies panel: the run's `anomalies` in order; empty state says 「無異常」.

### 7.4 Acceptance

See matrix Stage C (C-01…C-19): form values equal the constructed spec; the
cost-mode distinction round-trips; invalid input shows `BacktestSpecError` and
no run is created; results equal `run_backtest` output; displayed numbers equal
the persisted run after reload; the runtime panel shows exactly the persisted
effective runtime; for a created run the submitted request's cost mode may be
shown from service metadata, while a reused or historical run reports the
source mode as unknown and never infers it from numerical equality;
`created=False` is shown; the UI stays responsive; engine files unchanged (hash
comparison).

## 8. Stage D — Reports and history

### 8.1 Report modules

`fa/reporting/backtest_report.py` takes over `render(run)` from
`scripts/backtest_report.py` unchanged (same sections and formatting);
`scripts/backtest_report.py` imports it so the CLI and the GUI share one
renderer. The report renders the effective cost values it finds and does not
claim a cost-source mode: the stored run carries no source metadata in V2.4.

`fa/reporting/estimation_report.py` renders a stored estimation run from the
summary and the input snapshot: official vs estimated NAV, return, coverage,
uncovered weight, per-asset contributions taken from
`{fund}_qdii_estimate_inputs.json` → `snapshots[run_id]` (the latest
`_qdii_estimate.json` only when its `run_id` matches), local/FX attribution,
quality, warnings, look-ahead status, run id, model version and the input
provenance block.

### 8.2 Report centre

Two lists (estimations via `list_estimations`, backtests via
`list_backtest_runs`) with filters (fund code / strategy id), sort by date,
detail pane, and an "Export" button writing `output/reports/<run_id>.md`
(backtest) and `output/reports/<run_id>_estimate.md` (estimation). Opening your
browser on the written file is allowed; no HTML report is generated in this
stage.

History is immutable in V2.4: no delete, no overwrite, no "edit run".

### 8.3 Acceptance

See matrix Stage D (D-01…D-11): rendered report equals `render(run)` for the
same run id; the report for a stored run never triggers a backtest (asserted by
monkeypatching `run_backtest` to raise); `list_backtest_runs` matches the stored
runs (columns, order, limit, filter); exported file content equals the rendered
text; history lists equal the repository queries; no mutation of stored rows
(row counts and hashes before/after).

## 9. Stage E — Integration acceptance

End-to-end flow: launch → analysis → QDII estimate (live) → open stored run →
build strategy → run backtest → inspect metrics/trades/equity → export report →
close. Windows desktop checklist: startup time (threshold fixed in the matrix:
≤ 3 s to an interactive main window on the reference machine), layout at
1024×768 and at 1920×1080, long ticker names, table scrolling, date/number
formats, worker progress on long runs, close during a running job, reopen after
close, error dialogs for invalid input, no orphan process, no unhandled
exception in the log.

Regression: `python -m unittest discover -s tests -t .`,
`python -m compileall -q fa tests scripts fund_analyzer.py fund_analyzer_gui.py`,
`git diff --check`, core coverage ≥ 90 % (script `scripts/coverage_core.py`),
and all V2.3.2 acceptance tests unchanged.

## 10. Data flows (thread annotated)

### 10.1 Live estimation

```
user → QdiiEstimateWindow.refresh (main)
     → worker: application.estimation.run_estimate(fund_code, save_dir, as_of)
           ├─ Repository(path)                       [worker]
           ├─ estimate_qdii_nav(..., repository=repo) [worker: computes + saves run]
           ├─ persisted = repo.load_estimation(run_id) is not None [read-back]
           └─ Repository.close()                     [worker, finally]
     → queue(kind="result") → main: _update_ui(record, persisted, run_id)
     → worker: list_estimates(limit) → queue → history refresh
```

The service cannot observe `_save_to_database()`'s return value (the estimator
discards it), so persistence is established by reading the run back;
`persisted=False` shows 「未寫入資料庫」 and a warning, and the run is absent from
history. The window never pretends the run is stored.

During execution the worker may publish zero or more `kind="progress"` events
before publishing exactly one terminal `kind="result"` event. Progress events
are informational only and never replace the terminal result; the Tk main
thread consumes both kinds, and every message received after the window
enters its closed state is discarded.

### 10.2 Stored estimation

```
selection → worker: load_estimate(run_id)
                ├─ Repository.load_estimation(run_id)       [worker]
                ├─ Repository.estimation_inputs(run_id)     [worker]
                ├─ {fund}_qdii_estimate_inputs.json[run_id] [worker: contribution]
                └─ latest JSON only when run_id matches      [worker: fallback]
           → queue → main: render only (no recomputation)
```

### 10.3 Backtest

```
form (main) → StrategySpec (validation, main thread, cheap)
           → worker: application.backtesting.run_strategy(spec, path, force)
                 ├─ Repository(path)                    [worker]
                 ├─ run_backtest(spec, repo, force=…)    [worker]
                 └─ Repository.close()                   [worker, finally]
           → queue → main: summary/metrics/equity/trades/anomalies
           → optional worker reload: load_run(run_id) for the detail view
```

### 10.4 Report

```
selection → worker: application.reporting.backtest_report(run_id)
                  → Repository.load_backtest_run(run_id)     [worker]
                  → fa.reporting.render → text → file        [worker]
           → main: open file
```

`reporting.estimation_report(run_id)` follows the same pattern with
`load_estimation` + `estimation_inputs`.

### 10.5 Analysis (unchanged)

```
start_analysis → worker: analyze_fund → result_queue → master report + file list
```

## 11. Test strategy

Automated (headless-safe, Tk available on Windows CI-like runs):

| Test file | Covers |
| --- | --- |
| `tests/test_gui_formatting.py` | every formatter and missing-value rule |
| `tests/test_gui_worker.py` | worker bridge success/error/close semantics; late messages discarded; widget-free inputs |
| `tests/test_gui_lifecycle.py` | main window + notebook tabs open/close/reopen; registry; timers cancelled; fake scheduler for the 60 s post-market poll |
| `tests/test_application_estimation.py` | live service passes a repository and reports `persisted` by reading the run back from SQLite (a fake whose `load_estimation` returns `None` must yield `persisted=False`); stored mode never calls the estimator (monkeypatch raises); repository closed on both paths |
| `tests/test_application_backtesting.py` | service builds the repository per call, closes it in `finally`, propagates `created` |
| `tests/test_application_reporting.py` | report service is read-only (monkeypatched `run_backtest` raises) |
| `tests/test_gui_estimation_view.py` | record → summary/asset rows; status text; inputs-only and inputs + per-run inputs-snapshot sources; a latest JSON whose `run_id` differs is ignored; missing-detail text |
| `tests/test_gui_backtest_form.py` | form → `StrategySpec` (and nothing outside `StrategySpec`); default vs custom cost mode; JSON round trip; invalid inputs |
| `tests/test_gui_backtest_view.py` | run → panels (metrics, runtime config, equity rows, trade rows, anomalies); cost-source display for a created run (request mode), a reused run and a historical run (both unknown) |
| `tests/test_reports_backtest.py` | `render` sections; parity with the CLI renderer |
| `tests/test_reports_estimation.py` | estimation report content and provenance |
| `tests/test_reports_history.py` | history queries, filters, immutability, `list_backtest_runs` contract |

Manual Windows acceptance: the checklist in the matrix, with a named screenshot
or recorded session under `docs/development/v24_evidence/`.

GUI tests must not require network: estimation and backtest services are
injected with fakes (a callable) in the view tests. A real end-to-end desktop
pass belongs to Stage E.

## 12. Risks and mitigations

| ID | Risk | Mitigation |
| --- | --- | --- |
| R-01 | Tk widget updates from a worker cause random crashes | T-1…T-11 enforced by `WorkerBridge` and the widget-free service inputs; view tests assert the worker never receives widgets |
| R-02 | Long backtests freeze the UI | all engine calls in workers; buttons disabled; status text shows the running state |
| R-03 | `pywebview.start()` blocks the Tk main loop | keep the browser fallback; open the master report only when the user asks; known limitation |
| R-04 | Estimation asset detail is split across two sources | explicit resolution order + source label (G-05); missing fields `—`; a schema change would need separate approval |
| R-05 | GUI duplicates validation and drifts from the engine | only `StrategySpec` validates; form maps fields, never re-checks rules |
| R-06 | Report renderer diverges between CLI and GUI | one implementation in `fa/reporting`, script delegates |
| R-07 | History views accidentally write | read-only paths only; Stage D tests assert row counts unchanged |
| R-08 | A daemon worker keeps running after its window closed | accepted per T-7; the worker holds no widgets and closes its repository in `finally`; the service returns into a dead queue and the message is dropped |
| R-09 | Live estimate silently not persisted | the service returns `persisted`; the window shows 「未寫入資料庫」 and the run is missing from history (covered by B-17) |
| R-10 | `list_backtest_runs` becomes a second source of truth | it returns only stored columns; a test compares it with `load_backtest_run` |

## 13. Deliverables by stage

| Stage | Deliverables | Code touched |
| --- | --- | --- |
| 0 | this document, `v24_acceptance_matrix.md`, roadmap/overview updates | docs only |
| A | navigation + registry + worker bridge + formatting + `fa/application/` skeleton + lifecycle tests | `fa/gui/app.py`, new `navigation.py`, `worker.py`, `formatting.py`, `fa/application/{__init__,estimation,backtesting,reporting}.py` |
| B | full estimation view (live + stored), quality block, history, persistence warning | `fa/gui/qdii_estimate.py`, `fa/application/estimation.py`, view/service tests |
| C | backtest form + cost modes + run + result panels + runtime panel | new `fa/gui/backtest_window.py`, `fa/application/backtesting.py`, form/view/service tests |
| D | reports + history centre + `list_backtest_runs` | new `fa/reporting/*`, `fa/gui/history_window.py`, `fa/data/database/repository.py` (additive), `scripts/backtest_report.py` delegation |
| E | end-to-end + Windows acceptance evidence, docs, packaged ZIP | docs only |

Each stage ends with its own test evidence, an updated acceptance matrix status,
and a clean package; acceptance of one stage does not imply acceptance of the
next.
