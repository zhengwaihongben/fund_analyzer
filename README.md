# Fund Analyzer

A desktop research tool for Chinese public funds (公募基金): fund and holdings
analysis, QDII NAV estimation, point-in-time strategy backtesting, and a
persistent report/history catalogue — all on one local SQLite database.

**Status:** V2.4.0 — the V2.0–V2.4 plan is complete and accepted. V2.3.2
(backtesting) and V2.4 (GUI integration) were accepted on 2026-11-21; the release
record is in [`docs/development/release_manifest_v2.4.0.md`](docs/development/release_manifest_v2.4.0.md).

## Introduction

Fund Analyzer started as a NAV-and-holdings viewer for funds bought through
Alipay, and grew into a small quant workbench. It now covers four things:

1. **Fund analysis** — NAV trends, latest-quarter holdings, technical and
   risk indicators, benchmark-relative performance, interactive HTML charts.
2. **QDII NAV estimation** — fills the T+1 disclosure gap of funds that invest
   overseas, using the portfolio that was already public, the overseas closes
   and the FX move, with coverage, per-holding attribution and data quality.
3. **Point-in-time strategy backtesting** — a rule-based portfolio backtest over
   stored prices, with transaction costs, a benchmark curve, persisted runs and
   a content fingerprint that identifies "same inputs" exactly.
4. **Reports and history** — every estimate and backtest is stored once and can
   be browsed, rendered to Markdown and exported without re-running anything.

Results are interactive HTML charts for the analysis, and Markdown reports for
stored estimates/backtests (opened by the report centre or exported to
`output/reports/`).

The original motivation was concrete: funds bought through Alipay show very
little, and QDII funds (which invest in overseas markets) publish their NAV with
a one-day lag. The estimator and the backtester both exist to make decisions
with the data that was *actually available* at the time.

## Key features

### Fund analysis
- **NAV trend** — full historical unit NAV with interactive zoom.
- **Latest-quarter holdings** — donut chart of the latest disclosed quarter: top
  10 weights plus one `其他（未披露）` slice; no historical quarter series is kept.
- **Technical indicators** — SMA / BOLL / RSI / MACD, plus NAV-based KDJ,
  volatility and directional-strength **proxies** in one chart.
- **Risk metrics** — annualised return & volatility, Sharpe, Sortino, Calmar,
  Information Ratio, tracking error, max drawdown, AUM.
- **Cumulative excess return** — fund versus a chosen benchmark, aligned on
  common trading days.
- **Post-market monitor** — US pre/post-market prices of the latest top-10
  holdings, with an explicit coverage ratio and each holding's contribution.
- **Multi-market benchmarks** — US / CN / HK indices with auto-detection.
- **Fast, dependency-light fetching** — NAV from the `pingzhongdata` blob (one
  request), AKShare only as fallback; holdings from the fund home page first,
  then archive API with retry/backoff, then SQLite, then a stale disk snapshot.
- **Unified logging** — console + rotating file, a GUI log pane fed by a queue,
  `crash.log` for Python exceptions and `fatal.log` for native aborts.

### QDII NAV estimation (V2)
- **One-day estimate** of the missing latest NAV from a point-in-time portfolio.
- **Coverage and uncovered weight are always reported** — a 46 %-covered
  estimate is labelled `partial`, never `estimated`.
- **Shapley local/FX attribution**, per-holding returns and status, data-quality
  grade, model version and `run_id`.
- **Never overwrites the official NAV** — the estimate is a separate record with
  a replayable input snapshot, later scored against the published NAV (MAE /
  RMSE / bias).

### Strategy backtesting (V2.3.2)
- **`StrategySpec`** — universe, equal / fixed / momentum weighting, rebalance
  frequency and anchor, drift threshold, initial capital, benchmark, cost model.
- **Point-in-time data access** — decisions only see prices/FX published by the
  decision instant; revised prices are reconstructed per date.
- **Execution model** — decide at the session close + 30 min, fill at the next
  session's close, sell before buy, cash never goes negative.
- **Transaction costs** — commission (with a minimum), adverse slippage, FX
  spread on non-CNY legs; rounded once at 2 dp (money) / 6 dp (quantity).
- **Benchmark curve** and **metrics** — CAGR, volatility, Sharpe, Sortino,
  Calmar, turnover, hit rate, exposure, cost drag, max drawdown.
- **Persisted runs** — run id, data fingerprint, trades, equity curve, anomalies
  and metrics in SQLite; identical inputs reuse the stored run (`created=False`),
  `force` creates a new one.

### Data infrastructure
- **Multi-market** — US / CN / HK / JP trading calendars (rule-based, with
  hand-maintained 2025–2026 holidays), DST-aware session closes.
- **Multi-asset** — equity, ETF, bond ETF, cash and FX return calculators.
- **Point-in-time prices and FX** — `available_at` publication instants,
  `availability_source`, revision ledger and `prices_as_of()` / `fx_as_of()`
  queries.
- **Pipeline** — price and FX providers write through a pipeline that fills
  availability metadata, keeps a 5-session / 12-day overlap and records
  revisions instead of silently overwriting.
- **SQLite** — 4 schema versions of additive migrations, one repository for every
  table, WAL mode, migrations applied on connect.

### GUI (V2.4)
- Four areas in one window, a shared worker bridge and a window registry.
- Long tasks (analysis, estimate, backtest, history query, export) run off the
  Tk main thread and report back through a queue.
- Formatting helpers keep numbers/dates consistent and never print `0` for a
  missing value.
- Startup is lazy: only the analysis tab is built, heavy imports load with the
  window that needs them (median cold start 1.5 s on the reference machine).

## System architecture

```
                 Fund Analyzer (Tkinter GUI / CLI)
                              |
                 Application services  fa/application/
             estimation.py  backtesting.py  reporting.py
                              |
   +--------------+-----------+-----------+--------------+
   |              |                       |              |
fa/analysis/  fa/backtesting/        fa/reporting/   fa/pipeline.py
valuation,    models, data_access,   backtest +      fund analysis
returns,      engine, costs,         estimation      (NAV → charts)
risk,         benchmark, metrics,    renderers
estimators    runner
   |              |                       |
   +--------------+-----------+-----------+
                              |
                  Data layer  fa/data/
      providers (prices, FX, holdings, NAV, AUM, benchmark)
      pipelines (price, FX), asset classifier, storage
                              |
                    SQLite repository  fa/data/database/
        prices, revisions, FX, holdings, NAV, estimates, backtests
                              |
                    output files + Markdown reports
```

Design rules kept throughout:

- **The GUI never computes finance.** It formats values produced by the
  application service and shows them.
- **No wall clock in a backtest.** Decision/execution instants come from the
  spec, the calendars and the configured lags.
- **Nothing is renormalised away.** Uncovered weights, missing prices and
  unsupported assets are reported, never replaced by 0.
- **History is immutable.** Estimates and backtest runs are written once and
  read back; a display path never writes to the database.

## GUI overview

`python fund_analyzer_gui.py` (or `launch.vbs` on Windows) opens one window with
four areas:

| Area | What it does |
| :--- | :--- |
| **基金分析** (Fund analysis) | Fund code, benchmark, risk-free rate, start date, output directory; runs the analysis and lists the produced files. Buttons: 開始分析, 盘后监控(实时预估), QDII 淨值估算, 打开综合报告, 打開輸出目錄. |
| **QDII 估算** (QDII estimation) | Live estimation (through the application service, read-back persisted), stored estimation by `run_id`, and the history list with a fund filter. |
| **策略回測** (Strategy backtest) | Opens the backtest window: a `StrategySpec`-shaped form, a read-only runtime panel, and result tabs for summary, metrics, cost source, equity curve/chart, trades and anomalies. |
| **報告與歷史** (Reports & history) | Opens the history window: estimation and backtest lists, filters, a read-only preview and Markdown export. |

Behaviour worth knowing:

- Both the analysis and the QDII window can be closed while work is running; the
  close path waits at most 2 s and drops any later message instead of touching a
  destroyed widget.
- Child windows are registered; closing the main window closes them and stops
  the polling timers.
- The analysis button on the tab is the *handover* entry (shows the estimate that
  the analysis just produced); the estimation tab's 「開啟即時估算」 is the live
  entry that actually runs the estimator.

## Installation

### Requirements

- Python 3.10 or newer
- Internet connection (Eastmoney / 天天基金网 for NAV and holdings, Yahoo Finance
  for prices, FX and pre/post-market quotes)
- Packages from `requirements.txt`: `akshare`, `pandas`, `numpy`, `plotly`,
  `requests`, `yfinance`. AKShare is pinned to the `1.18.x` line
  (`>=1.18.88,<1.19.0`) because its archive interface changed behaviour between
  minor releases; 1.18.88 (Python 3.10) and 1.18.97 (conda) were verified.
- Windows / macOS / Linux
- Optional: `pywebview` for the embedded combined report (otherwise the default
  browser is used)

### Install

```bash
git clone https://github.com/YOUR_USERNAME/fund-analyzer.git
cd fund-analyzer
```

**Windows** — double-click `install.bat` (creates a project-local `.venv`), or
`install.bat` in a terminal. **macOS / Linux** — `chmod +x install.sh && ./install.sh`.

Manual equivalent:

```bash
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### Launcher

`launch.vbs` starts the GUI without a console window. The interpreter is resolved
in this order (no machine-specific path is hardcoded):

1. `python_path.txt` (first non-empty line = full path to `python.exe`)
2. `.venv\Scripts\python.exe`
3. `venv\Scripts\python.exe`
4. `%USERPROFILE%\anaconda3\envs\quant-finance\python.exe` (legacy fallback)

Each launch appends its timestamp, the chosen interpreter and the process output
to `launch_stdout.log`.

## Usage

### GUI (recommended)

```bash
python fund_analyzer_gui.py
```

1. Enter a **fund code** (6 digits, e.g. `017436`).
2. Optionally set **benchmark**, **risk-free rate**, **start date**
   (`YYYY-MM-DD`) and output directory.
3. Leave **「忽略緩存（強制刷新）」** checked to bypass the holdings cache, or
   uncheck it to reuse a cache younger than 7 days.
4. Click **「開始分析」** (AUM is fetched automatically).
5. From there: **「打开综合报告」** for the combined HTML report,
   **「盘后监控」** for the 60-second post-market monitor, **「QDII 淨值估算」**
   for the handover estimate, or double-click any result file.

The other tabs are described in [GUI overview](#gui-overview).

### Command line

```bash
python fund_analyzer.py <fund_code> [options]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `--output DIR` | `output` | Output root directory |
| `--benchmark CODE` | `.NDX` | Benchmark index; `""` skips IR and the excess-return chart |
| `--benchmark-market` | `auto` | `auto` / `us` / `cn` / `hk` |
| `--risk-free RATE` | `0.02` | Annual risk-free rate |
| `--start-date DATE` | — | Tracking start date (`YYYY-MM-DD`) |
| `--fund-type TYPE` | `auto` | `auto`, `standard` or `qdii` (force the estimate) |
| `--qdii-as-of TIME` | now | Base time for the QDII estimate (`YYYY-MM-DD[THH:MM]`) |
| `--qdii-backtest` | — | Score stored estimates against published NAV (MAE / RMSE) |
| `--no-cache` | — | Ignore the holdings cache |
| `--show` | — | Open all HTML reports in the browser |
| `-v, --verbose` | — | DEBUG logging |

```bash
python fund_analyzer.py 017436 --risk-free 0.02 --benchmark .NDX --show
python fund_analyzer.py 017436 --start-date 2023-01-01 --show
python fund_analyzer.py 000369 --fund-type qdii
python fund_analyzer.py 000369 --qdii-backtest
```

Strategy backtests, stored-report rendering and data maintenance have their own
scripts — see [Strategy backtesting](#strategy-backtesting).

## Fund analysis

### Metrics

| Metric | Formula | Interpretation |
| :--- | :--- | :--- |
| **Annualised Return** | `(∏(1 + r_t))^(252/N) − 1` (geometric) | Compound annual growth |
| **Annualised Volatility** | `std(r_t) × √252` | Total risk |
| **Sharpe Ratio** | `(Ann. Return − Risk-free) / Ann. Volatility` | Excess return per unit of total risk |
| **Sortino Ratio** | `(Ann. Return − Risk-free) / Downside Deviation` | Excess return per unit of downside risk |
| **Downside Deviation** | `√(mean(min(r_t − 252⁻¹·Rf, 0)²)) × √252` over **all** observations | Only shortfalls count; non-shortfall days contribute 0 |
| **Calmar Ratio** | `Ann. Return / |Max Drawdown|` | Return per unit of maximum loss |
| **Max Drawdown** | Largest peak-to-trough decline of NAV | Worst historical loss |
| **Tracking Error** | `std(fund_r − bench_r) × √252` on **common** dates | Volatility of active return |
| **Information Ratio** | `mean(active) × 252 / Tracking Error` | Active return per unit of tracking error |
| **Cumulative Excess Return** | `FundCum / BenchCum − 1` on common dates | Relative performance over time |

Notes:

- Risk metrics use official NAV only; they are skipped when fewer than 30 daily
  returns are available.
- The benchmark is aligned by merging prices on common dates **before** taking
  returns, so a holiday on either side cannot shift the base date.
- A CNY fund compared with a USD index is **not** currency-adjusted; the log says
  so when it happens.
- Sharpe and Sortino use the geometric annualised return as numerator (the same
  number Calmar uses) while the denominator is daily-return dispersion; a
  textbook Sharpe would annualise the arithmetic mean of daily excess returns.
  This is documented rather than silently mixed.

### Technical indicators

All indicators come from the NAV series alone (a fund has one price per day):

| Indicator | Parameters | Notes |
| :--- | :--- | :--- |
| SMA | 5 / 10 / 60 / 120 / 250 | Blank until the window is full |
| BOLL | 20, 2σ | Same warm-up rule |
| RSI | 14 (Wilder) | First value defaults to 50 |
| MACD | 12 / 26 / 9 | Histogram = `(DIF − DEA) × 2` (A-share convention) |
| KDJ | 9, 3, 3 | **NAV proxy**: NAV is high/low/close at once — it measures position within the recent 9-day range, not a stock-style KDJ |
| NAV_VOL | `EWMA(14)` of `|ΔNAV|` | **Proxy** for volatility, not ATR |
| NAV_DI_Plus / NAV_DI_Minus / NAV_DX | Wilder smoothing of `ΔNAV` | **Proxy** for directional strength, not +DI/−DI/ADX |

Charts and column names label the proxies, so a proxy is never mistaken for the
textbook indicator.

## QDII NAV estimation

A QDII fund bought before 15:00 Beijing time on day **T** is settled at the
overseas close of **T−1**; the official NAV appears on T+1. So on day T the latest
visible NAV is **T−2**, and the estimator fills that one-day gap:

```
Estimated_NAV(T−1) = Official_NAV(T−2) × (1 + R)
R = Σ wᵢ · [ (Pᵢ,T−1 / Pᵢ,T−2) × (FXᵢ,T−1 / FXᵢ,T−2) − 1 ]
```

`wᵢ` are the disclosed weights of the quarter that was already public on T−1,
`Pᵢ` the adjusted closes in the holding's own currency, `FXᵢ` the `<CUR>CNY=X`
rate. Weights are **never renormalised**: the unknown part of the portfolio is
excluded and reported as uncovered weight.

Trade dates are resolved in **Asia/Shanghai** (before 15:00 = today, after =
next China trading day), and target/base dates follow the China trading calendar
(weekends and the 2025–2026 statutory holidays), never `today − 1 day`.

Key output fields (`<code>_qdii_estimate.json`):

| Field | Meaning |
| :--- | :--- |
| `status` | `estimated` (100 % coverage), `partial` (normal case), `insufficient-data`, `up-to-date`, `invalid-target`, `no-nav`, `error` |
| `target_nav_date` / `base_nav_date` | The NAV date being estimated and the last published NAV it builds on |
| `estimated_nav` / `estimated_return` | Estimated NAV and its change |
| `coverage_ratio` / `uncovered_weight` | Covered share of the whole fund and the unknown remainder |
| `local_contribution` / `fx_contribution` | Shapley split: `C_local = w·r_local(1+r_fx/2)`, `C_fx = w·r_fx(1+r_local/2)`; they sum to `estimated_return` |
| `run_id` | Identity of this run (JSON, history CSV row and input snapshot share it) |
| `look_ahead_status` | `verified-safe`, `estimated-disclosure`, `unsafe`, `unknown` |
| `holdings_quarter` / `holdings_date` / `disclosure_date` | The quarter used, its end, and the filing date (quarter end + 21 days, marked as estimated) |
| `holdings_source` / `holdings_scope` | `eastmoney-page` / `database` / `cache` / `cache-stale`; `top10` or `full_portfolio` |
| `data_quality` / `quality_score` / `quality` | Grade, 0–100 score and the full quality report |
| `holdings[]` | Per holding: weight, currency, local/FX/CNY return, contribution, price/FX dates, status |
| `warnings[]` | Missing prices, staleness, uncovered weight, etc. |

The complete field list, statuses and persistence layout are in
[`docs/technical_specification/V2/`](docs/technical_specification/V2/).

Rules the implementation follows:

- **No look-ahead.** Only quarters disclosed on or before the target date are
  usable (`fa/data/qdii_holdings.py::select_point_in_time`); if none qualifies the
  estimate is refused rather than computed from future information. Historical
  quarters come from SQLite (saved by earlier runs), not from automatic archive
  downloads.
- **No fabricated data.** A holding without a usable price or FX rate is excluded
  from both the return and the coverage (`missing-price`, `missing-target-price`,
  `missing-base-price`, `stale-price`, `stale-base-price`, `missing-fx`,
  `stale-fx`, `stale-base-fx`, `missing-currency`); it is never a 0 % return.
- **Both interval endpoints must be fresh**, for prices and for FX, so a base
  quote from weeks ago cannot be compared with a NAV from two days ago.
- **Unknown currency is not CNY.** Only `CNY`/`RMB` skip the FX leg; a `FxSeries`
  must declare `base_currency="CNY"` — a foreign or unlabelled series is rejected.
- **Asset types come from the source**, not from the ticker shape; an unrecognised
  symbol stays `unknown` and keeps its weight.
- **Coverage is never hidden** — an estimate covering 46 % is `partial`, and the
  uncovered share is printed.
- **The official series is never overwritten.** The estimate is its own JSON plus
  one row per run in `<code>_qdii_estimates.csv`, so the published NAV can be
  compared later.
- **Inputs are reproducible.** The full input snapshot (prices, FX, dates,
  portfolio selection) is stored per `run_id`, so an old estimate can be replayed.

Scoring stored estimates:

```bash
python fund_analyzer.py 000369 --qdii-backtest
```

It reports `MAE`, `RMSE`, mean error, max absolute error and mean coverage; a
target date without published NAV is counted as `pending`, not as an error.

**Where the portfolio comes from** (latest quarter only, by design): fresh
`<code>_qdii_holdings.csv` → fund home page (latest top-10, one request) →
SQLite → last snapshot on disk (flagged `cache-stale`). Every fetched quarter is
written to the CSV cache and SQLite; older quarters are never re-downloaded from
the archive endpoint, which is unreachable from many networks.

**Fund classification:** `--fund-type auto` reads the formal type where available,
falls back to the name, otherwise stays `standard`; `qdii` forces the estimate,
`standard` disables it.

**Known limits:** the holiday table covers 2025–2026 (other years warn and fall
back to weekends); adjusted close is used as the price proxy (fees are not
modelled); bonds, cash and funds inside the portfolio count as uncovered weight;
the disclosed filing date is an estimate; supported assets are stocks, ETFs and
cash.

## Post-market estimate

`fetch_post_market_estimate()` combines US pre/post-market prices with the latest
disclosed holdings:

| Field | Meaning |
| :--- | :--- |
| `coverage_ratio` | Sum of weights (%) for which pre/post prices were obtained |
| `covered_weighted_return` | Weighted contribution of the covered holdings to NAV (%) |
| `estimate_return` | Average change of the covered holdings (contribution / coverage) |
| `total_weight` | Sum of the weights in the holdings file |
| `holdings[]` | Per holding: weight, regular close, pre/post price, change %, status |

The window shows coverage and contribution, so a 40 %-coverage number is never
presented as a whole-fund estimate. A previous analysis run is required, because
it reads `<code>_holdings_clean.csv`.

## Strategy backtesting

A point-in-time portfolio backtest over the stored SQLite prices. It is driven by
a `StrategySpec` (a JSON file or the GUI form) and records the run, metrics,
trades and equity curve in `strategy_backtest_*`.

| Concept | Behaviour |
| :--- | :--- |
| Universe | Explicit `(ticker, market, currency)` rows; CNY is the base currency |
| Weighting | `equal`, `fixed` (weights must sum to 1) or `momentum` (positive momentum normalised; no positive momentum ⇒ cash) |
| Rebalance | `daily` / `weekly` / `monthly` / `quarterly` plus an anchor, skipped when drift is below the threshold |
| Data | PIT only: `prices_as_of()` / `fx_as_of()`; no `load_prices()` shortcut, no network |
| Execution | Decision at session close + 30 min, fill at the next session's close, sells before buys |
| Costs | Commission (bps, minimum per order), slippage (recorded as a cost, not folded into the price), FX spread on non-CNY legs |
| Benchmark | Optional benchmark instrument, normalised in CNY, carried forward on missing sessions |
| Persistence | `run_id = sha256(spec, runtime, model version, data fingerprint)`; identical inputs reuse the stored run, `force` creates a new one |
| Outputs | Summary, metrics, cost source, equity curve + chart, trades, anomalies |

```bash
# submit a specification (see config/backtests/stagee_demo.json)
python scripts/backtest_report.py run --spec config/backtests/stagee_demo.json
# list stored runs, then render one report from the database
python scripts/backtest_report.py list
python scripts/backtest_report.py show <run_id>
```

Reports go to `output/reports/<run_id>.md` and contain only values read back
from the database, so a report is traceable to its run. The GUI backtest window
shows the same data plus a Tk line chart of the portfolio versus the benchmark.

Acceptance and maintenance helpers:

| Script | Purpose |
| :--- | :--- |
| `scripts/accept_backtest_synthetic.py` | Hand-checked scenarios on a throwaway database |
| `scripts/smoke_backtest_real.py` | Short real-data run with PIT-priced fills |
| `scripts/audit_working_db.py` | Read-only audit of the working database |
| `scripts/audit_backtest_retention.py` | Integrity audit (no pruning policy) |
| `scripts/ingest_market_data.py` | Documented two-year price/FX ingest through the pipelines |
| `scripts/coverage_core.py` | Stdlib statement coverage of the core modules |

The full backtest specification (benchmark rules, fingerprint content, metric
definitions, acceptance evidence) is in
[`docs/technical_specification/V2/10_v232_backtest_engine.md`](docs/technical_specification/V2/10_v232_backtest_engine.md).

## Data infrastructure

### Calendars and markets

Rule-based calendars for US, CN, HK and JP markets: weekends plus a
hand-maintained 2025–2026 holiday table, DST-aware session closes, and a
`FallbackCalendar` (weekends only) that is flagged whenever it is used. Other
venues have no reliable holiday source in this project, so they degrade to the
fallback rather than pretending to be exact.

### Prices and FX

- **Point-in-time**: every price/FX row carries `available_at`,
  `availability_source` (`observed` / `vendor_timestamp` / `inferred` / `unknown`)
  and the ledger id that produced it. `prices_as_of(ticker, as_of)` and
  `fx_as_of(currency, as_of)` apply `available_at <= as_of` **and**
  `replaced_at > as_of`, comparing real datetimes (not Julian days, whose
  resolution is ~47 µs).
- **Revisions**: a re-download that changes a stored number writes a
  `price_revisions` row; the new value becomes visible only from the revision
  instant, so an old backtest cannot silently change.
- **Pipelines**: `price_pipeline.update_prices()` and `fx_pipeline.update_fx()`
  download a window, fill availability metadata (session close + 30 min,
  FX: next London midnight + 30 min), keep an overlap, record revisions and
  refuse venue/currency mismatches.
- **Display vs decision reads**: `load_prices()` / `load_fx()` are for display;
  backtests and the estimator use the PIT queries only.

### SQLite

The durable store is `data/database/fund_analyzer.db` (git-ignored; override with
`cfg["db_path"]`). Schema version 4, additive migrations, WAL mode.

| Table group | Contents |
| :--- | :--- |
| `funds`, `assets` | Fund metadata and the asset universe |
| `holdings_snapshots`, `holdings` | Every fetched quarter with holding/disclosure dates |
| `market_prices`, `price_revisions`, `fx_rates`, `fx_revisions`, `official_nav` | Prices, revision ledgers, FX and the official NAV series |
| `estimation_runs`, `estimation_inputs`, `estimation_results` | One immutable row set per estimate |
| `strategy_backtest_runs`, `_metrics`, `_trades`, `_equity` | One immutable row set per backtest |
| `backtest_results` | Estimation-vs-official-NAV scoring |

If the database cannot be opened the analysis still runs from files only; the
database is a durability layer, not a dependency.

## Reports and history

- **Backtest reports** — a Markdown renderer shared by the CLI (`scripts/backtest_report.py`)
  and the GUI history window; sections: identity/config, performance, costs by
  side, equity curve, trades, anomalies, provenance.
- **Estimation reports** — official vs estimated NAV, coverage, asset
  contributions, attribution, quality block (quarter, scope, disclosure date,
  price type, warnings, source), look-ahead status and provenance.
- **History window** — estimation and backtest lists, filtering, a read-only
  preview and export. Every query and export reads the stored run; nothing is
  recomputed, and a test asserts that row counts and the JSON files are unchanged
  after browsing.
- **Exports** — `output/reports/<run_id>.md` and `<run_id>_estimate.md`.

## Configuration

### Environment variables

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `FA_CACHE_TTL_DAYS` | `7` | Holdings cache lifetime (`<= 0` = never expire) |
| `FA_REQUEST_TIMEOUT` | `30` | Socket default timeout for third-party HTTP |
| `FA_LOG_FILE` | `fund_analyzer.log` | Log file path |
| `FA_STALE_SESSIONS_WARN` | `0` | Stale sessions above this are flagged |
| `FA_STALE_SESSIONS_EXCLUDE` | `3` | Stale sessions at/above this are excluded |
| `FA_STALE_SESSIONS_MAX` | `10` | Beyond this a quote counts as missing |
| `FA_CASH_ASSUMPTION_RATE` | `0.0` | Rate used when a cash holding declares none |
| `FA_FX_PIPELINE` | `1` | Use the persisted PIT FX pipeline for estimates |
| `FA_FX_TIMEZONE` | `Europe/London` | Calendar day a Yahoo FX bar belongs to |
| `FA_FX_AVAILABILITY_LAG_MINUTES` | `30` | Minutes after the next London midnight a bar is published |
| `FA_PRICE_AVAILABILITY_LAG_MINUTES` | `30` | Minutes after a session close its bar is published |
| `FA_PRICE_OVERLAP_SESSIONS` | `5` | Trading days re-downloaded before the last stored price |
| `FA_PRICE_OVERLAP_CALENDAR_DAYS` | `12` | Calendar-day floor for that overlap |
| `FA_PRICE_DEFAULT_YEARS` | `2` | Default history length for a first download |
| `FA_BACKTEST_COMMISSION_BPS` | `5` | Commission in bps of gross notional |
| `FA_BACKTEST_MIN_COMMISSION` | `0.0` | Minimum commission per order (CNY) |
| `FA_BACKTEST_SLIPPAGE_BPS` | `10` | Adverse slippage in bps (recorded as a cost) |
| `FA_BACKTEST_FX_SPREAD_BPS` | `20` | FX spread in bps on non-CNY legs |
| `FA_BACKTEST_RISK_FREE_RATE` | `0.02` | Risk-free rate for Sharpe/Sortino |
| `FA_BACKTEST_DECISION_LAG_MINUTES` | `30` | Minutes after the close at which that close is visible |
| `FA_BACKTEST_EXECUTION_LAG_SESSIONS` | `1` | Sessions between decision and fill |
| `FA_BACKTEST_FRACTIONAL_SHARES` | `1` | Allow 6-dp fractional quantities |

A `CostModel` embedded in a `StrategySpec` wins over the `FA_BACKTEST_*` cost
defaults; the runtime values actually used are shown in the backtest window and
are part of the run identity.

### Log files

| File | Written by | Content |
| :--- | :--- | :--- |
| `fund_analyzer.log` | both entry points | Python log (INFO/DEBUG), 2 MB × 3 rotation |
| `launch_stdout.log` | `launch.vbs` | Launch header + process output |
| `gui_debug.log` | GUI under `pythonw.exe` | stdout/stderr fallback |
| `crash.log` | GUI | Python traceback of uncaught exceptions / Tk callback errors |
| `fatal.log` | GUI | `faulthandler` dump when a native library aborts the process |

## Output and data policy

Results are written under `output/<fund_code>/`:

```
017436_daily_nav.csv               raw NAV (also the NAV cache)
017436_trend.html                  NAV trend
017436_holdings_raw.csv            holdings cache
017436_holdings_clean.csv          latest quarter only
017436_holdings_pie.html           donut (top 10 + 其他（未披露）)
017436_daily_nav_sma_boll.csv      NAV + all indicators
017436_nav_sma_boll.html           interactive indicator chart
017436_risk_metrics.html           risk metrics
017436_excess_return.html          cumulative excess return
017436_summary.json                success flag, metrics, file list
017436_master_report.html          combined report (GUI)
017436_qdii_holdings.csv           QDII portfolio (weights + dates)
017436_qdii_estimate.json          latest estimate with its inputs
017436_qdii_estimate_inputs.json   per-run input snapshots (keyed by run_id)
017436_qdii_estimates.csv          one row per estimate, later scored
```

Rules:

- The output directory is **never wiped at the start**; derived reports from an
  older run are pruned only after a successful run.
- `summary.json` is written after success, so `"success": true` is meaningful.
- Holdings are latest-quarter only; the undisclosed remainder is never
  renormalised away.
- `data/` is an empty skeleton in git (`cache/`, `database/`,
  `exports/{backtests,estimations,holdings}/`); the live DB, caches and outputs
  are ignored.

Two scripts keep the boundary:

```bash
python scripts/clean_runtime_files.py            # list what would be removed
python scripts/clean_runtime_files.py --apply    # remove caches and scratch dirs
python scripts/package_project.py --mode review  # → dist/fund_analyzer_review.zip
```

## Testing and code quality

The suite uses the standard library (`unittest`) and also runs under `pytest`:

```bash
python -m unittest discover -s tests -t .
python -m pytest -q          # pytest.ini limits collection to tests/
```

- **Python 3.10** — 1,131 tests, `OK` (~130 s on the reference machine).
- Coverage of the documented core modules: **99.4 % (704/708)**, measured with
  `python scripts/coverage_core.py` (stdlib tracer, no `coverage` dependency);
  the per-module table and the four remaining defensive lines are in
  [`docs/development/stageE_coverage.md`](docs/development/stageE_coverage.md).
- `python -m compileall -q fa fund_analyzer.py fund_analyzer_gui.py` passes.
- The suite requires the project dependencies: modules that import `akshare`
  cannot be imported without it, and a bare interpreter reports import errors
  rather than test failures.

Areas covered: configuration and utilities; NAV/holdings/benchmark fetching;
indicators and risk metrics; calendars and price alignment; SQLite migrations and
the PIT/revision queries; return calculators and portfolio valuation; QDII
estimation end to end and its FX wiring; the backtest models, PIT access, costs,
engine, metrics, benchmark and runner; the application services for estimation,
backtesting and reporting; the report renderers; and the GUI worker, formatting,
navigation, lifecycle, startup guard and view-mode tests.

Verified second environment: Python 3.11.16 (conda) + pytest 9.2.0 — the same
tests pass except `test_gui.py::…test_analysis_result_is_marshalled_to_main_thread`,
where Tk cannot be created twice inside pytest on that build
(`_tkinter.TclError: invalid command name "tcl_findLibrary"`); it passes under
`unittest` on 3.10 and the GUI code is unaffected. Under a restricted sandbox the
conda interpreter may need write access for temporary files (`PermissionError`);
a normal user installation is not affected.

## Troubleshooting

### SSL error while fetching data

```
SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING]'))
```

This is usually a network problem with the Eastmoney `fundf10` CDN, not the code.
The fund home page is tried first (one request, normally succeeds); then the
archive API with 3 attempts per year and backoff; then the last on-disk snapshot,
so the run still produces charts and — for a QDII fund — an estimate. Since V2.1
the failure is classified before logging:

```
[000369] 2026 年持仓获取失败（已重試 3 次）: TLS 握手階段被中斷（網絡路徑 / 中間設備 / 對端 CDN 問題，非程式錯誤）（APIError）
```

Categories: TLS handshake interrupted, connection closed by peer, timeout, DNS
failure, other. If it says TLS the request never reached Eastmoney — check proxies
and a raw handshake before adding retries; retrying a broken TLS path only adds
~50 s per run. The archive host is the only source of per-quarter history, which
is why the QDII flow no longer depends on it.

An estimate reports `status: "insufficient-data"` and the CLI prints
`未產生估算：目標日 … 之前尚無已披露的 QDII 持倉` when every portfolio source
failed, or when the only snapshots were disclosed **after** the target date
(look-ahead protection).

### The program seems to hang

Some hosts accept the connection and stall in the TLS handshake.
`FA_REQUEST_TIMEOUT` (default 30 s) bounds this:

```cmd
set FA_REQUEST_TIMEOUT=15
python fund_analyzer.py 017436 -v
```

### `ModuleNotFoundError: No module named 'fund_analyzer'`

Run from the folder that contains `fund_analyzer.py`, or set the shortcut's
**Start in** field to the project folder.

### GUI closes immediately

Run `python fund_analyzer_gui.py` in a terminal to see the error. Under
`pythonw.exe` the traceback goes to `crash.log` and `gui_debug.log` instead.

If neither file has a traceback, the process died inside a native library
(Windows event log shows e.g. `0xC06D007F` in `KERNELBASE.dll`); check
`fatal.log`, written by `faulthandler`.

### The combined report does not open

`pywebview` is optional; without it the report opens in the default browser. If
neither works, open `<code>_master_report.html` manually — it embeds the
individual reports as iframes.

## Benchmark reference

| Index | Code | Market |
| :--- | :--- | :--- |
| 納斯達克100 | `.NDX` | US |
| 標普500 | `.INX` | US |
| 道瓊斯工業 | `.DJI` | US |
| 滬深300 | `sh000300` | CN |
| 中證500 | `sh000905` | CN |
| 創業板指 | `sz399006` | CN |
| 上證指數 | `sh000001` | CN |
| 恒生指數 | `HSI` | HK |
| 恒生中國企業 | `HSCEI` | HK |

`--benchmark-market auto` detects the market from the code shape:

```
.NDX        →  starts with .        →  us
sh000300    →  starts with sh/sz/bj →  cn
HSI         →  neither              →  hk
```

```bash
python fund_analyzer.py 017436 --benchmark .NDX --benchmark-market auto --show
python fund_analyzer.py 000051 --benchmark sh000300 --benchmark-market auto --show
python fund_analyzer.py 005827 --benchmark HSI --benchmark-market auto --show
python fund_analyzer.py 017436 --benchmark "" --show
```

## Project structure

```
fund_analyzer/
├── fund_analyzer.py            CLI entry point (thin shim)
├── fund_analyzer_gui.py        GUI entry point (owns the pythonw stderr guard)
├── yf_helper.py                yfinance helper, run in a child process
├── fa/
│   ├── application/            estimation.py · backtesting.py · reporting.py
│   ├── analysis/               technical · risk · valuation_dates ·
│   │                           price_alignment · nav_estimator ·
│   │                           estimation_backtest · post_market ·
│   │                           returns/ · valuation/
│   ├── backtesting/            models · data_access · engine · costs ·
│   │                           benchmark · metrics · runner
│   ├── data/                   nav · holdings · qdii_holdings · aum ·
│   │                           benchmark · asset_classifier · fund_info ·
│   │                           qdii_market · fx · price_provider ·
│   │                           price_pipeline · price_canonical ·
│   │                           fx_provider · fx_pipeline ·
│   │                           estimation_storage · market ·
│   │                           database/{connection,schema,repository}
│   ├── gui/                    app · navigation · worker · formatting ·
│   │                           qdii_estimate · backtest_window ·
│   │                           history_window · post_market · crash · launcher
│   ├── models/                 asset · holding · market_data ·
│   │                           return_result · estimation
│   ├── reporting/              backtest_report · estimation_report
│   ├── utils/                  logging_setup · net · cache · dates · tickers ·
│   │                           calendar · calendars/{base,china,us,hong_kong,japan} ·
│   │                           data_quality
│   ├── viz/                    charts · master
│   ├── cli.py  config.py  errors.py  pipeline.py
├── config/backtests/           example StrategySpec files
├── scripts/                    analysis/acceptance/maintenance helpers
├── tests/                      54 modules, stdlib unittest
├── docs/
│   ├── technical_specification/V2/   design documents and specifications
│   └── development/                  roadmap, acceptance matrix, evidence,
│                                     release manifest, backlog
├── data/                       empty skeleton (cache, database, exports)
├── install.bat / install.sh · launch.vbs · requirements.txt · pytest.ini
```

`fund_analyzer.py` / `fund_analyzer_gui.py` only keep the historic entry points
alive; all logic lives in `fa/`, one module per responsibility. `fa/gui/` is never
imported by the CLI: `fa/cli.py` and `fa/pipeline.py` stay headless, and
`fa/gui/__init__.py` exposes `main()` lazily. Every subpackage has an `__init__.py`;
`yf_helper.py` must stay next to `fund_analyzer.py`, because all yfinance calls run
in a child process so a native crash inside yfinance cannot take the main program
down.

Files created at runtime (git-ignored): the outputs above, the live database, the
five log files, and `python_path.txt` (optional interpreter override for
`launch.vbs`).

## Development documentation

| Document | Purpose |
| :--- | :--- |
| [`docs/development/fund_analyzer_project_overview.md`](docs/development/fund_analyzer_project_overview.md) | Project overview, plan and current status (hand-off document) |
| [`docs/development/v24_acceptance_matrix.md`](docs/development/v24_acceptance_matrix.md) | Stage 0/A/B/C/D/E acceptance matrix with per-item evidence |
| [`docs/development/v24_evidence/`](docs/development/v24_evidence/) | Stage E evidence files and screenshots |
| [`docs/development/stageE_coverage.md`](docs/development/stageE_coverage.md) | Per-module coverage with missing line numbers |
| [`docs/development/stageE_bt_matrix.md`](docs/development/stageE_bt_matrix.md) | BT-01…BT-14 backtest evidence matrix |
| [`docs/development/release_manifest_v2.4.0.md`](docs/development/release_manifest_v2.4.0.md) | Tag/commit, test environment, coverage, artefact verification |
| [`docs/development/backlog.md`](docs/development/backlog.md) | Non-blocking notes for the next developer |
| [`docs/technical_specification/V2/`](docs/technical_specification/V2/) | Specifications: roadmap, database schema, testing, FX pipeline, backtest engine, GUI integration |

The full backtest design (fingerprint content, execution rules, metric
definitions) is in `10_v232_backtest_engine.md`; the GUI contract is
`11_v24_gui_integration.md`; database rules are `04_database_schema.md`.

## Disclaimer

This tool is for **educational and research purposes only**. Data is fetched from
public sources (Eastmoney via AKShare, Sina, Yahoo Finance) and may be delayed or
inaccurate. Nothing here constitutes investment advice.

## Credits

- Data: [AKShare](https://github.com/akfamily/akshare) / 天天基金网 / [yfinance](https://github.com/ranaroussi/yfinance)
- Charts: [Plotly](https://plotly.com/python/)
- GUI: Tkinter / pywebview
- Much of the implementation was written with AI assistance (DeepSeek models)

## License

[MIT](./LICENSE)
