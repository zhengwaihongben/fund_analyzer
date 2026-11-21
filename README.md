# Fund Analyzer

**Note:**
- This repository is still under construction, and I may develop more functions.
- If you find any problem or any suggestion, please contact me, or upload your branch.
- Most of the codes are built by *Deepseek-v4.1-flash*.
- Since the Phase-3 refactor the implementation lives in the `fa/` package; `fund_analyzer.py` and `fund_analyzer_gui.py` are thin entry points kept for backwards compatibility.

## Introduction

A one-stop tool for analyzing Chinese mutual funds (公募基金). Input a fund code, get NAV trends, holdings, technical indicators, risk-adjusted performance metrics, and real-time pre/post-market estimates — all as interactive HTML reports.

### Motivation of developing this project

I currently invest the funds by Alipay(支付宝), but most funds on it do not provide complete information or insights, and hence I decided to develop this project. For QDII funds (which invest in US stocks), there is a severe T+1 time-lag. This tool uses US pre/post-market data to solve this "blind box" problem.

## Features

- **NAV Trend** — Full historical unit NAV with interactive zoom
- **Latest-Quarter Holdings** — Donut chart of the latest disclosed quarter's top 10 weights plus the undisclosed remainder; no historical quarter series is kept (the fund home page is fetched first, one request)
- **Technical Indicators** — SMA / BOLL / RSI / MACD plus NAV-based KDJ, volatility and directional-strength proxies in one interactive chart
- **Risk Metrics** — Annualised return & volatility, Sharpe, Sortino, Calmar, Information Ratio, Tracking Error, Max Drawdown, AUM
- **Cumulative Excess Return** — Fund performance vs a user-selected benchmark, aligned on common trading days
- **Post-Market Estimate** — US pre/post-market prices of the latest disclosed top-10 holdings, with an explicit **coverage ratio** and each holding's contribution to the fund NAV
- **QDII NAV Estimate (V2)** — Estimates the fund's missing latest NAV from the portfolio that was already public before the target day, the overseas closes and the FX move, reporting coverage, uncovered weight, per-holding returns, Shapley local/FX attribution and a data-quality grade, plus a separate estimated-NAV record (with a replayable input snapshot) instead of overwriting the official series
- **China trading calendar** — Beijing-timezone 15:00 cutoff plus the 2025–2026 statutory holiday table, so valuation dates follow the real trading calendar instead of `today − 1 day`
- **Start Date Filter** — Limit the tracking range to a specific start date
- **Multi-Market Benchmark** — Support US / CN / HK indices with auto-detection
- **Auto AUM Fetching** — Automatically fetch the latest Assets Under Management
- **Holdings Cache with TTL** — Reuse the previous holdings download for up to 7 days (`--no-cache` forces a refresh)
- **Fast, dependency-light NAV fetch** — NAV history is read straight from the `pingzhongdata` blob (one HTTP request, `re` + `json`), with AKShare as fallback; no JS engine is instantiated
- **Resilient downloads** — holdings are retried 3× per year with 2 s / 4 s backoff, and a socket-level timeout (`FA_REQUEST_TIMEOUT`) bounds hosts that stall mid-handshake
- **Unified Logging** — Console + rotating log file, GUI log pane fed through a queue, single crash-report path (`crash.log`), plus `fatal.log` (faulthandler) for native aborts that never reach Python
- **Test suite** — 1131 stdlib `unittest` tests covering config, data layer, indicators, risk metrics, QDII estimation, point-in-time holdings, FX persistence/PIT, price PIT and revision reconstruction, the backtest model/access layer, the engine and costs, the metrics, runner and benchmark, the pipeline, the estimator's FX wiring and the GUI lifecycle, the Stage C service (`fa/application/backtesting.py`), the backtest form conversion and the persisted-run panels, the Stage D report renderers (backtest + estimation) and the read-only history query, and the report-centre window

> There is **no** built-in scheduled alert script in this repository. Use your OS scheduler (e.g. Windows Task Scheduler) to run the CLI if you want periodic alerts.

## Project Structure

```
fund_analyzer/
├── fund_analyzer.py            # CLI entry point — thin shim: `from fa import *` + main()
├── fund_analyzer_gui.py        # GUI entry point — thin shim; owns the pythonw stdout/stderr guard
├── yf_helper.py                # yfinance helper, executed in a child process (required)
├── fa/                         # implementation package (Phase-3 module split)
│   ├── __init__.py             # public API re-exports (__all__), keeps `import fund_analyzer` working
│   ├── config.py               # PROJECT_ROOT, DEFAULT_CONFIG, env-driven settings, merge_config()
│   ├── errors.py               # exception hierarchy (FundAnalyzerError & friends)
│   ├── pipeline.py             # analyze_fund(): NAV → holdings → indicators → risk → excess → summary
│   ├── cli.py                  # argparse CLI; enables the socket default timeout at start-up
│   ├── data/
│   │   ├── aum.py              # latest fund size (AUM) lookup
│   │   ├── nav.py              # NAV history: parse pingzhongdata blob, AKShare fallback, CSV cache
│   │   ├── benchmark.py        # benchmark closes for US / CN / HK indices with auto-detection
│   │   ├── holdings.py         # latest-quarter holdings (home page first), TTL cache, top-10 selection
│   │   ├── qdii_holdings.py    # QDII portfolio record: weights, quarter/holding/disclosure dates
│   │   ├── qdii_market.py      # Yahoo chart API: ticker → exchange/currency, adjusted closes
│   │   ├── fx.py               # <CUR>CNY=X series and the CNY-base conversion of a holding return
│   │   ├── fund_info.py        # fund type detection (auto / standard / qdii) with manual override
│   │   └── market.py           # yfinance bridge: spawns yf_helper.py, no yfinance import in-process
│   ├── analysis/
│   │   ├── technical.py        # SMA / BOLL / RSI / MACD + NAV-based KDJ and volatility proxies
│   │   ├── risk.py             # annualised return & vol, Sharpe, Sortino, Calmar, IR/TE, MaxDD
│   │   ├── valuation_dates.py  # trade date → target/base NAV date alignment (15:00 cut-off)
│   │   ├── price_alignment.py  # last observation on/before a date + staleness verdict
│   │   ├── returns/            # V2 return calculators: equity, ETF, bond ETF, cash, FX
│   │   ├── valuation/          # V2 portfolio valuation: weights, coverage, Shapley attribution
│   │   ├── portfolio_return.py # V1 per-holding return (kept for the legacy pipeline)
│   │   ├── nav_estimator.py    # QDII orchestrator: dates → prices → FX → calculators → portfolio → DB/JSON
│   │   ├── estimation_backtest.py  # reconcile estimates with official NAV → MAE / RMSE / bias
│   │   └── post_market.py      # US pre/post-market estimate with coverage ratio and contribution
│   ├── viz/
│   │   ├── charts.py           # all interactive Plotly HTML charts
│   │   └── master.py           # combined report (iframes), opened by the GUI
│   ├── utils/
│   │   ├── logging_setup.py    # root logger: console + rotating file (2 MB × 3)
│   │   ├── calendar.py         # business-day arithmetic used by the date alignment
│   │   ├── dates.py            # quarter-label parsing (YYYYQn), latest-quarter detection
│   │   ├── tickers.py          # stock code → Yahoo ticker (0700 → 0700.HK, BRK.B → BRK-B)
│   │   ├── cache.py            # cache freshness / read / write, stale-output pruning
│   │   └── net.py              # socket default timeout, so stalled TLS handshakes fail fast
│   └── gui/
│       ├── launcher.py         # theme, excepthook, faulthandler → fatal.log, Tk main loop
│       ├── app.py              # main window, worker thread, queue-polled log pane, shutdown()
│       ├── qdii_estimate.py    # QDII estimate window (estimated vs official NAV, coverage, contributions)
│       ├── post_market.py      # post-market monitor window (60 s refresh, single instance)
│       └── crash.py            # one crash path: log → crash.log → messagebox
├── tests/                      # test suite (stdlib unittest; `pytest -q` also works)
│   ├── test_utils.py           # config merging, quarters, tickers, cache TTL, socket timeout
│   ├── test_data.py            # pingzhongdata parsing, holdings retry/backoff
│   ├── test_analysis.py        # indicators (incl. warm-up), downside deviation, Sortino, IR alignment
│   ├── test_models.py          # V2 dataclasses: asset, holding, prices, return results, quality
│   ├── test_calendars.py       # CN/US/HK/JP sessions, holidays, DST, staleness
│   ├── test_database.py        # SQLite migration, repository round-trips, point-in-time
│   ├── test_price_alignment.py # last-observation rule and the four staleness bands
│   ├── test_return_calculators.py  # equity / ETF / bond ETF / cash / FX + registry
│   ├── test_portfolio_valuation.py # weights, coverage, uncovered, status
│   ├── test_shapley.py         # local + FX == CNY contribution, portfolio identity
│   ├── test_qdii.py            # calendar, valuation dates, holding returns, FX, estimator, backtest
│   ├── test_nav_estimator_v2.py    # orchestrated flow end-to-end, no network, DB persistence
│   ├── test_quality_wiring.py      # stale prices / FX / assumption-rate cash reaching quality
│   ├── test_pipeline.py        # end-to-end pipeline with network calls stubbed
│   └── test_gui.py             # log queue, crash handler, shutdown(), post-market window reuse
├── install.bat / install.sh    # create .venv and install requirements
├── launch.vbs                  # Windows launcher without a console window
├── requirements.txt
└── README.md
```

Notes on the split:

- `fund_analyzer.py` / `fund_analyzer_gui.py` only keep the historic entry points alive (`python fund_analyzer.py 017436` and double-clicking `launch.vbs` still work). All logic lives in `fa/`, one module per responsibility, and `fa/__init__.py` re-exports the old public names.
- `fa/gui/` must not be imported by the CLI: `fa/gui/app.py` imports Tkinter, while `fa/cli.py`/`fa/pipeline.py` stay headless. `fa/gui/__init__.py` exposes `main()` for the GUI shim.
- Every subpackage (`data/`, `analysis/`, `viz/`, `utils/`, `gui/`) and `tests/` also has an `__init__.py` that marks it as a package (they carry only a docstring, except `fa/gui/__init__.py` which re-exports `main`).
- `yf_helper.py` must stay next to `fund_analyzer.py`: all yfinance calls are executed in a child process through it, so a native crash inside yfinance cannot take down the main program.

Files created at runtime (not in git):

| File | Purpose |
| :--- | :--- |
| `output/<fund_code>/` | Analysis results (charts, CSVs, `summary.json`, `master_report.html`) |
| `fund_analyzer.log` | Rotating application log (see [Log files](#log-files)) |
| `launch_stdout.log` | Launcher header + process output, appended on every `launch.vbs` run |
| `gui_debug.log` | Only when `sys.stdout`/`sys.stderr` are `None` (`pythonw.exe`) |
| `crash.log` | Python traceback of uncaught exceptions / Tk callback errors |
| `fatal.log` | `faulthandler` dump of all threads when a native library aborts the process |
| `python_path.txt` | Optional: first line overrides the interpreter used by `launch.vbs` |

## Project layout and packaging

Everything in the working tree belongs to one of three categories:

| Category | Examples | In git | In archives |
| :--- | :--- | :--- | :--- |
| Source, tests, docs | `fa/`, `tests/`, `docs/`, `scripts/`, `pytest.ini` | yes | both |
| Project data | `data/database/fund_analyzer.db` (live DB), `docs/development/v2.2.3_baseline/` (frozen fixture) | live DB no, baseline yes | live DB never, baseline always |
| Runtime artefacts | `__pycache__/`, `*.pyc`, `.pytest_cache/`, `_tmp/`, `_pytmp/`, `pytest-cache-files-*/`, `output/`, `*.log` | no | no |

Two scripts keep that boundary:

```bash
python scripts/clean_runtime_files.py            # list what would be removed
python scripts/clean_runtime_files.py --apply    # remove caches and scratch directories

python scripts/package_project.py --mode full    # → dist/fund_analyzer_full.zip
python scripts/package_project.py --mode review  # → dist/fund_analyzer_review.zip
```

- **`full`** (備份／移交／部署) — the whole project except caches, scratch directories, the live database and logs; generated `output/` is included.
- **`review`** (程式碼審查) — only `fa/`, `tests/`, `docs/`, `scripts/`, `pytest.ini`, `README.md` and `.gitignore`.

Both archives are deterministic (sorted entries, fixed timestamps) and the packaging script refuses to finish if an archive still contains a cache, scratch directory, log, or any database other than the frozen `docs/development/v2.2.3_baseline/database_backup.db` — that one is a migration-test fixture and must travel with `docs/`.

`data/` is kept as an empty skeleton (`cache/`, `database/`, `exports/{backtests,estimations,holdings}/` with `.gitkeep` files), so a fresh clone has the directories the code expects. A few directories left behind by an earlier sandboxed test run are ACL-locked and cannot be deleted from a normal shell (`_tmp/`, `_pytmp/`, `pytest-cache-files-*`); they are ignored by git and excluded from both archives, and an elevated PowerShell can remove them if they ever get in the way.

## Configuration

### Requirements

- Python 3.10 or newer
- Internet connection (AKShare / 天天基金网 for NAV and holdings, Yahoo Finance for macro factors and post-market prices)
- Python packages from `requirements.txt`: `akshare`, `pandas`, `numpy`, `plotly`, `requests`, `yfinance` (the last one is required by `yf_helper.py` for the post-market estimate). AKShare is pinned to the `1.18.x` line (`>=1.18.88,<1.19.0`) because its fund holdings archive interface has changed behaviour between minor releases; 1.18.88 (Python 3.10) and 1.18.97 (conda `quant-finance`) are the versions this project was verified against.
- Works on Windows / macOS / Linux
- Optional: `pywebview` — used to show the combined report in an embedded window; without it the report opens in the default browser

### Install dependencies

Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/fund-analyzer.git
cd fund-analyzer
```

**Windows** — double-click `install.bat` (it creates a project-local `.venv`), or run in terminal:

```cmd
install.bat
```

**macOS / Linux** — run:

```bash
chmod +x install.sh
./install.sh
```

Or install manually:

```bash
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### Launcher (`launch.vbs`)

Double-clicking `launch.vbs` starts the GUI without a console window. The interpreter is resolved in this order — no machine-specific absolute path is hardcoded:

1. `python_path.txt` in the project folder (first non-empty line = full path to `python.exe`; use it to override temporarily)
2. `.venv\Scripts\python.exe` (created by `install.bat`)
3. `venv\Scripts\python.exe`
4. `%USERPROFILE%\anaconda3\envs\quant-finance\python.exe` (legacy fallback)

Every launch appends its timestamp, the chosen interpreter and the script path to `launch_stdout.log`, and the process output is appended there as well.

## Usage

### Option A — GUI (recommended)

```bash
python fund_analyzer_gui.py
```

After launching:

1. Enter a **fund code** (6 digits, e.g. `017436`)
2. Optionally set a **benchmark**, **risk-free rate**, **start date** (`YYYY-MM-DD`) and output directory
3. Leave **"忽略緩存（強制刷新）"** checked to bypass the holdings cache, or uncheck it to reuse a cache younger than 7 days
4. Click **「開始分析」** (AUM is fetched automatically)
5. When the log finishes you can:
   - Click **「📊 打开综合报告(视窗)」** to view the combined report (all charts in one page).
   - Click **「盘后监控(实时预估)」** to see the post-market estimate; it refreshes itself every 60 seconds and reuses the same window.
   - Click **「QDII 淨值估算」** (enabled only when the fund is detected as QDII) to see the estimated latest NAV, its coverage and uncovered weight, the local/FX split (the disclosure date carries a 「（推算）」 marker), and each holding's contribution; 「重新估算」 refreshes it without re-running the whole analysis.
   - Click **「打開輸出目錄」** to open the output folder, or double-click any file in the result list to open it.

Closing the main window stops the log polling, the 60-second post-market refresh and waits for the worker thread before exiting.

### Option B — Command line

```bash
python fund_analyzer.py <fund_code> [options]
```

**Arguments:**

| Argument | Description |
| :--- | :--- |
| `fund_code` | 6-digit fund code, e.g. `017436` |

**Options:**

| Option | Default | Description |
| :--- | :--- | :--- |
| `--output DIR` | `output` | Output root directory |
| `--benchmark CODE` | `.NDX` | Benchmark index; pass `""` to skip IR and the excess-return chart |
| `--benchmark-market` | `auto` | Benchmark market: `auto` / `us` / `cn` / `hk` |
| `--risk-free RATE` | `0.02` | Annual risk-free rate |
| `--start-date DATE` | — | Tracking start date (YYYY-MM-DD); empty = from inception |
| `--fund-type TYPE` | `auto` | `auto` (detect from fund data), `standard`, or `qdii` (force the QDII estimate) |
| `--qdii-as-of TIME` | now | Base time for the QDII estimate (`YYYY-MM-DD[THH:MM]`), for back-filling a past day |
| `--qdii-backtest` | — | Reconcile stored QDII estimates with the official NAV and print MAE / RMSE |
| `--no-cache` | — | Ignore holdings cache, force refresh |
| `--show` | — | Open all HTML reports in the browser after generating |
| `-v, --verbose` | — | Verbose (DEBUG) logging |

**Example:**

```bash
# Full analysis from inception
python fund_analyzer.py 017436 --risk-free 0.02 --benchmark .NDX --show

# Track only from a specific date
python fund_analyzer.py 017436 --start-date 2023-01-01 --show

# QDII fund: estimate the missing latest NAV, then score past estimates
python fund_analyzer.py 000369 --fund-type qdii
python fund_analyzer.py 000369 --qdii-backtest
```

## Output

Results are saved under `output/<fund_code>/`:

```
output/017436/
├── 017436_daily_nav.csv               # Raw NAV data (also the NAV cache)
├── 017436_trend.html                  # NAV trend chart
├── 017436_holdings_raw.csv            # Holdings (raw, cached up to 7 days)
├── 017436_holdings_clean.csv          # Holdings, latest quarter only (QuarterLabel / Weight / StockCode / Stock)
├── 017436_holdings_pie.html           # Latest-quarter holdings donut chart
├── 017436_daily_nav_sma_boll.csv      # NAV + all technical indicators
├── 017436_nav_sma_boll.html           # Interactive indicator chart
├── 017436_risk_metrics.html           # Risk metrics report
├── 017436_excess_return.html          # Cumulative excess return vs benchmark
├── 017436_summary.json                # Summary: success flag, metrics, file list
├── 017436_master_report.html          # Combined report (generated by the GUI only)
├── 017436_qdii_holdings.csv           # QDII portfolio record (latest quarter, weights + dates)
├── 017436_qdii_estimate.json          # Latest QDII NAV estimate, with every input it used
├── 017436_qdii_estimate_inputs.json   # Full input snapshot per target date (prices, FX, dates) for replay
└── 017436_qdii_estimates.csv          # One row per estimate, later reconciled with the official NAV
```

The four `_qdii_*` files are produced only when the fund is classified as QDII.

Notes:

- Files that could not be produced in this run (e.g. holdings when the data source is unavailable) are simply absent and are `null` in `summary.json`.
- The output directory is **never wiped at the start** of an analysis, so caches survive. Derived reports left over from a previous run are pruned only after the run succeeds; if the run fails, the previous results stay untouched.
- `summary.json` is written after the run is known to succeed, so `"success": true` is meaningful; on failure it contains `"error"`.
- All `.html` files are fully interactive — open them in any modern browser.

## Metrics Explained

| Metric | Formula | Interpretation |
| :--- | :--- | :--- |
| **Annualised Return** | `(∏(1 + r_t))^(252/N) − 1` (geometric, from daily returns) | Compound annual growth of the NAV series |
| **Annualised Volatility** | `std(r_t) × √252` | Total risk |
| **Sharpe Ratio** | `(Ann. Return − Risk-free) / Ann. Volatility` | Excess return per unit of total risk |
| **Sortino Ratio** | `(Ann. Return − Risk-free) / Downside Deviation` | Excess return per unit of downside risk |
| **Downside Deviation** | `√(mean(min(r_t − 252⁻¹·Rf, 0)²)) × √252` over **all** observations | Only shortfalls below the (daily) risk-free target count; non-shortfall days contribute 0 instead of being dropped |
| **Calmar Ratio** | `Ann. Return / |Max Drawdown|` | Return per unit of maximum loss |
| **Max Drawdown** | Largest peak-to-trough decline of NAV | Worst historical loss |
| **Tracking Error** | `std(fund_r − bench_r) × √252`, both computed **on common trading dates** | Volatility of active return |
| **Information Ratio** | `mean(active) × 252 / Tracking Error` | Active return per unit of tracking error |
| **Cumulative Excess Return** | `FundCum / BenchCum − 1`, where both are compounded on common dates | Relative performance vs benchmark over time |

Additional remarks:

- Risk metrics and indicators are computed on official NAV values only.
- The benchmark is aligned by *merging prices on common dates first, then taking returns on the merged frame*, so a holiday on either side cannot shift the return base date.
- A CNY-denominated fund compared with a USD index (e.g. `.NDX`) is **not** currency-adjusted; the log prints a note when that happens.
- Both Sharpe and Sortino use the **geometric** annualised return as numerator (the same number Calmar uses), while the denominator is built from daily-return dispersion. A textbook Sharpe would annualise the arithmetic mean of daily excess returns instead; the difference is small for daily data but is kept geometric here for consistency. Documented rather than silently mixed.
- Metrics are skipped when fewer than 30 daily returns are available.

## Technical Indicators

All indicators are derived from the NAV series alone (a fund has only one price per day):

| Indicator | Parameters | Notes |
| :--- | :--- | :--- |
| SMA | 5 / 10 / 60 / 120 / 250 | Value is blank until the window is full (`min_periods = window`) |
| BOLL | 20, 2σ | Same warm-up rule |
| RSI | 14 (Wilder smoothing) | First value defaults to 50 |
| MACD | 12 / 26 / 9 | Histogram = `(DIF − DEA) × 2` (A-share convention) |
| KDJ | 9, 3, 3 | **NAV proxy**: NAV is used as high/low/close simultaneously — it measures the position of NAV within its recent 9-day range, not a stock-style KDJ |
| NAV_VOL | `EWMA(14)` of `|ΔNAV|` | **Proxy** for volatility; *not* a standard ATR (no high/low data) |
| NAV_DI_Plus / NAV_DI_Minus / NAV_DX | Wilder smoothing of `ΔNAV` | **Proxy** for directional strength; *not* standard +DI/−DI/ADX |

Charts and column names label these proxies explicitly, so a proxy is never mistaken for the textbook indicator.

## Post-Market Estimate

`fetch_post_market_estimate()` combines US pre/post-market prices with the latest disclosed holdings:

| Field | Meaning |
| :--- | :--- |
| `coverage_ratio` | Sum of weights (%) for which post-market prices were actually obtained |
| `covered_weighted_return` | Weighted contribution of the covered holdings to the fund NAV, in % (uncovered holdings assumed unchanged) |
| `estimate_return` | Average change of the covered holdings only (= contribution / coverage), i.e. normalised to full coverage |
| `total_weight` | Sum of the weights present in the holdings file |
| `holdings[]` | Per-holding: weight, regular close, pre/post price, change %, status (`非美股代碼` / `数据缺失`) |

The GUI shows the coverage ratio and the contribution, so a 40 %-coverage estimate is never presented as a whole-fund estimate. A previous analysis run is required, because the estimate reads `<fund_code>_holdings_clean.csv`.

## QDII NAV Estimate (V2)

A QDII fund bought before 15:00 Beijing time on day **T** is settled at the overseas close of **T−1**; that official NAV is published on T+1. When you analyse the fund on day T, the latest NAV you can see is therefore **T−2**. The estimator fills that one-day gap:

```
Estimated_NAV(T−1) = Official_NAV(T−2) × (1 + R)
R = Σ wᵢ · [ (Pᵢ,T−1 / Pᵢ,T−2) × (FXᵢ,T−1 / FXᵢ,T−2) − 1 ]
```

`wᵢ` are the disclosed weights of the quarter that was **already public on T−1** (see point-in-time below), `Pᵢ` the adjusted closes of each holding in its own currency, and `FXᵢ` the `<CUR>CNY=X` rate. Weights are **never renormalised to 100 %**: the unknown part of the portfolio (bonds, cash, undisclosed names) is excluded and reported as an uncovered weight instead.

The trade date is resolved in **Asia/Shanghai**: before 15:00 the trade date is today, from 15:00 onward it is the next China trading day; target and base dates are then picked with the China trading calendar (weekends **and** the 2025–2026 statutory holidays), never with `today − 1 day`.

| Field (`_qdii_estimate.json`) | Meaning |
| :--- | :--- |
| `status` | `estimated` (100 % coverage), `partial` (some weight uncovered — the normal case), `insufficient-data`, `up-to-date`, `invalid-target`, `no-nav`, `error` |
| `trade_date` | China trading day the estimate was made on |
| `timezone` / `cutoff` / `as_of_beijing` | The clock used (`Asia/Shanghai`), the 15:00 cutoff, and the exact timestamp the dates were derived from |
| `target_nav_date` / `base_nav_date` | The NAV date being estimated and the last published NAV it is built on |
| `estimated_nav` / `estimated_return` | Estimated NAV and its change vs the base |
| `coverage_ratio` | Σ weights of holdings whose price **and** FX were obtained — a fraction of the whole fund |
| `coverage_of_known` | The same sum divided by the disclosed weights (i.e. coverage of the known portfolio) |
| `uncovered_weight` | `1 − coverage_ratio`: the share treated as a 0 % return, reported explicitly because it is unknown, not zero |
| `local_contribution` / `fx_contribution` | Shapley attribution of the estimate: `C_local = w·r_local(1+r_fx/2)`, `C_fx = w·r_fx(1+r_local/2)`; the two add up to `estimated_return` exactly |
| `run_id` | Identity of this run; the estimate JSON, the history CSV row and the stored input snapshot all share it, so two runs for the same target date never overwrite each other |
| `look_ahead_status` | `verified-safe` (filing date known and before the target), `estimated-disclosure` (the filing date is still the quarter-end + 21-day estimate), `unsafe`, `unknown`; `look_ahead_safe` is true only for `verified-safe` |
| `holdings_quarter` / `holdings_date` / `disclosure_date` / `disclosure_date_estimated` | The quarter actually used, its quarter end, the filing date assumed for it (quarter end + 21 days, marked as **estimated** rather than fact) |
| `quarters_available` | The quarters that exist locally (cache + database) |
| `holdings_source` | Where the weights came from: `eastmoney-page` (latest quarter top-10 from the fund home page), `database` (SQLite snapshot), `cache` (fresh snapshot) or `cache-stale` (older snapshot reused because every online source failed) |
| `holdings_scope` | `top10` (Eastmoney publishes only the top ten, the normal case) or `full_portfolio` |
| `history_available` | Whether more than one quarter exists locally, i.e. whether a date-specific estimate has anything older to fall back on |
| `market_data_date` | Latest date actually present in the price data |
| `holdings[]` | Per holding: weight, currency, market, local return, FX return, CNY return, **Shapley** contribution, `price_type_used`, price/FX dates, status, notes |
| `model_version` | `2.2.0` — the V2 estimation model that produced the record |
| `data_quality` / `quality_score` / `quality` | `high` (≥85) / `medium` (≥60) / `low` and the 0–100 score; `quality` is the full report (stale weight, excluded weight, assumption-rate weight, calendar fallback, reasons) |
| `inputs` | Complete per-run input snapshot: base/target prices with dates and `price_type_used`, FX rates with dates, calendar (and whether it fell back to weekends), cash rate/source/days, config and model version |
| `warnings[]` | Missing prices, stale data, stale portfolio, uncovered weight, etc. |

Rules the implementation follows:

- **No look-ahead bias.** Only quarters whose disclosure date is on or before the target date may be used (`fa/data/qdii_holdings.py::select_point_in_time`); if no locally available quarter qualifies the estimate is refused (`insufficient-data`) rather than computed from future information. The tool does **not** download historical quarters automatically any more — older quarters have to come from SQLite (imported or saved by earlier runs).
- **No fabricated data.** A holding without a usable price or FX rate is excluded from both the return and the coverage ratio (status `missing-price`, `missing-target-price`, `missing-base-price`, `stale-price`, `stale-base-price`, `missing-fx`, `stale-fx`, `stale-base-fx`, `missing-currency`); it is never treated as a 0 % return.
- **Both endpoints of the interval must be fresh.** Staleness is checked on the target **and** the base observation, for prices and for FX, so a base quote from three weeks ago cannot be silently compared with a NAV two days ago.
- **Unknown currency is not CNY.** An empty/unrecognised currency yields `missing-currency` and is excluded; only `CNY`/`RMB` skip the FX leg. A `FxSeries` must declare its `base_currency`: a foreign base is rejected (`匯率基準貨幣 … 並非 CNY`) and so is a missing declaration (`匯率序列缺少基準貨幣聲明`) — an EUR-per-USD series must never be consumed as CNY-per-USD, and an unlabelled series is not silently assumed to be CNY either (`from_pairs()` / the SQLite loader declare `CNY` explicitly).
- **Asset types come from the source, not from the ticker.** A symbol that merely looks like a stock code stays `unknown` (excluded, weight retained) unless a manual override, the data source, or the formal name says what it is; the QDII builder declares `equity` only because the Eastmoney table is 「股票投资明细」. Adding a REIT or commodity ETF later must not need a change to the portfolio maths.
- **Coverage is never hidden.** An estimate covering 46 % of the fund is labelled `partial`, never `estimated`, and the uncovered share is printed (`未覆蓋 53.9% 的資產按 0% 回報處理`).
- **The estimate never overwrites the official series.** It is written to its own JSON, plus one row per run in `_qdii_estimates.csv`, which keeps the official NAV (once published) next to the estimate so the error can be scored later.
- **Inputs are reproducible.** Besides the summary record, the complete input snapshot of every estimate (per-holding prices, FX rates, the dates they were quoted on, the portfolio selection) is merged into `<fund_code>_qdii_estimate_inputs.json`, keyed by `run_id`, so an old estimate can be replayed and its error traced to one holding. Two estimates for the same target date are two runs, and both are kept.

**Backtest.** `--qdii-backtest` reconciles every stored estimate with the official NAV as soon as it is published and reports `MAE`, `RMSE`, `Mean Error`, `Max Abs Error` and the mean coverage:

```bash
python fund_analyzer.py 000369 --qdii-backtest
```

An estimate whose target date has no published NAV yet is counted as `pending`, not as an error.

**Where the portfolio comes from.** The QDII portfolio is **latest-quarter only, by design**: the Eastmoney archive endpoint that serves older quarters is unreachable from many networks, and waiting for it turned one bad afternoon into a permanently "missing history" state. The estimator only needs the newest public portfolio, so the chain is:

1. a fresh `<fund_code>_qdii_holdings.csv` snapshot (unless caching was disabled);
2. the fund home page (`fund.eastmoney.com/<code>.html`), which renders the **latest disclosed quarter's top 10** with weights — one request, reported as `holdings_source: "eastmoney-page"` and `holdings_scope: "top10"`;
3. the SQLite database (`data/database/fund_analyzer.db`, `holdings_source: "database"`), when the network is down but an earlier run already stored a snapshot;
4. the last snapshot on disk, flagged `holdings_source: "cache-stale"` and mentioned in `warnings[]`.

Every fetched quarter is merged into the local CSV cache and written to SQLite, so older quarters accumulate locally and remain available to `select_point_in_time`; they are simply never downloaded from the archive API any more. When the page fetch fails, the request is not retried in a loop — the database and the on-disk snapshot are tried instead.

**Fund classification.** `--fund-type auto` (default) reads the fund's formal type where the data source exposes it, falls back to the fund name, and otherwise stays `standard` without guessing; `--fund-type qdii` forces the estimate, `--fund-type standard` disables it. A standard fund simply skips the whole QDII block.

**V2 limitations:** the holiday table covers 2025–2026 only (other years fall back to weekends, with a warning); `Adjusted Close` is used as the price proxy (dividends/splits are handled, fees are not); bonds, cash and funds held inside the portfolio are counted as uncovered weight; the disclosed filing date is an estimate (quarter end + 21 days), not the statutory publication date; the estimator supports stocks, ETFs and cash only.

## Settings & Logs

### Environment variables

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `FA_CACHE_TTL_DAYS` | `7` | Holdings cache lifetime in days (`<= 0` = never expire) |
| `FA_REQUEST_TIMEOUT` | `30` | Socket default timeout in seconds; third-party HTTP libraries expose no timeout, so this bounds hung connections |
| `FA_LOG_FILE` | `fund_analyzer.log` | Log file path |
| `FA_STALE_SESSIONS_WARN` | `0` | Stale sessions above this value are flagged (0 = flag any staleness) |
| `FA_STALE_SESSIONS_EXCLUDE` | `3` | Stale sessions at/above this value are excluded from the return and coverage |
| `FA_STALE_SESSIONS_MAX` | `10` | Absolute cap: beyond it the quote counts as missing, not stale |
| `FA_CASH_ASSUMPTION_RATE` | `0.0` | Annual rate used when a cash holding declares none (warning is emitted) |
| `FA_FX_PIPELINE` | `1` | Use the persisted point-in-time FX pipeline for QDII estimates (`0`/`false`/`no`/`off` falls back to the legacy download path, which has no PIT guarantee) |
| `FA_FX_TIMEZONE` | `Europe/London` | Timezone whose calendar day a Yahoo FX bar belongs to |
| `FA_FX_AVAILABILITY_LAG_MINUTES` | `30` | Minutes after the next London midnight when a bar is treated as published |
| `FA_PRICE_AVAILABILITY_LAG_MINUTES` | `30` | Minutes after a session close when its daily bar is treated as published |
| `FA_PRICE_OVERLAP_SESSIONS` | `5` | Trading days re-downloaded before the last stored price when updating |
| `FA_PRICE_OVERLAP_CALENDAR_DAYS` | `12` | Calendar-day floor for that overlap (whichever reaches further back wins) |
| `FA_BACKTEST_COMMISSION_BPS` | `5` | Commission in basis points of the gross notional (the spec's own `CostModel` wins when it is set) |
| `FA_BACKTEST_MIN_COMMISSION` | `0.0` | Minimum commission per order in CNY |
| `FA_BACKTEST_SLIPPAGE_BPS` | `10` | Adverse slippage in basis points, recorded as a cost rather than folded into the fill price |
| `FA_BACKTEST_FX_SPREAD_BPS` | `20` | FX spread in basis points on a non-CNY conversion leg |
| `FA_BACKTEST_RISK_FREE_RATE` | `0.02` | Annual risk-free rate used by Sharpe and Sortino |
| `FA_BACKTEST_DECISION_LAG_MINUTES` | `30` | Minutes after the session close at which the strategy may look at that close |
| `FA_BACKTEST_EXECUTION_LAG_SESSIONS` | `1` | Sessions between the decision and the executed fill |
| `FA_BACKTEST_FRACTIONAL_SHARES` | `1` | Allow 6-dp fractional quantities (`0` = whole shares) |
| `FA_PRICE_DEFAULT_YEARS` | `2` | Default history length for a first price download |

### Log files

| File | Written by | Content |
| :--- | :--- | :--- |
| `fund_analyzer.log` | both entry points | Python log (`INFO`, or `DEBUG` with `-v`), rotated at 2 MB with 3 backups |
| `launch_stdout.log` | `launch.vbs` | Launch header (timestamp, interpreter, script) + process stdout/stderr (appended) |
| `gui_debug.log` | GUI under `pythonw.exe` | `stdout`/`stderr` fallback, because both are `None` in a windowless process |
| `crash.log` | GUI | Full traceback of uncaught exceptions and Tk callback errors |
| `fatal.log` | GUI | `faulthandler` dump (all thread stacks) when a native library aborts the process — e.g. `0xC06D007F`, which leaves no Python traceback at all |

All `*.log` files are git-ignored.

## Data & Cache Policy

- NAV history is re-fetched on every run. It is read from the `pingzhongdata` JSON blob on `fund.eastmoney.com` and parsed with `re` + `json`; if that fails, the tool falls back to AKShare's `fund_open_fund_info_em`. Avoiding the AKShare path by default keeps the V8 isolate (`py_mini_racer`) out of the process, which is both faster and safer in the GUI's worker thread.
- Holdings are **latest-quarter only**. The fund home page (`https://fund.eastmoney.com/<code>.html`) is tried first, because one request already contains the latest disclosed quarter's top 10; only if that fails does the tool fall back to the per-year archive API (current year and the previous three, each attempted up to 3 times with a 2 s / 4 s backoff, then a 10-minute host cooldown) and finally to any older snapshot on disk. No historical quarter series is kept: `<fund_code>_holdings_clean.csv` holds the latest quarter only, and the chart is a donut of the top 10 plus one `其他（未披露）` slice for the rest of the fund — the undisclosed part is never renormalised away.
- `<fund_code>_holdings_raw.csv` is the holdings cache. A run reuses it when it is younger than `FA_CACHE_TTL_DAYS` and `use_cache` is true (`--no-cache` or unchecking the GUI box forces a refresh). A corrupt cache raises `CacheError`, is logged and re-downloaded instead of aborting. Disabling the cache only skips the *fresh* snapshot: if every online source fails, an older snapshot is still used and logged, because throwing good data away helps nobody.
- `<fund_code>_qdii_holdings.csv` is the QDII portfolio cache (same TTL rule). It stores weights plus the quarter, holding and disclosure dates, so the estimator can reuse a portfolio without re-deriving its dates. A refresh merges the new quarter into this file instead of replacing older ones, so locally accumulated history survives.
- Outputs are pruned only after a successful run: derived HTML/CSV/JSON files that this run did not produce are removed, while raw/cache files (`*_daily_nav.csv`, `*_holdings_raw.csv`, `*_holdings_clean.csv`, `*_qdii_holdings.csv`, `*_qdii_estimates.csv`) and unrelated files are kept.

## Database (SQLite)

V2 keeps a local SQLite database at `data/database/fund_analyzer.db` (override with `cfg["db_path"]`, git-ignored). It is the durable version of what the JSON/CSV files hold:

| Table | Contents |
| :--- | :--- |
| `funds`, `assets` | Fund metadata and the asset universe (ticker, type, market, currency) |
| `holdings_snapshots`, `holdings` | Every fetched quarter, with holding and disclosure dates, queryable point-in-time |
| `market_prices`, `fx_rates`, `official_nav` | Prices, FX rates and the official NAV series |
| `estimation_runs`, `estimation_inputs`, `estimation_results` | One immutable row set per estimate: the run metadata, the full input payload and the result — never overwritten |
| `backtest_results` | Error metrics per run/group |

The QDII pipeline uses it in both directions: each fetched quarter is written on the way in (so a later run can rebuild point-in-time holdings offline, reported as `holdings_source: "database"`), and every estimate is stored with its `run_id` on the way out. If the database cannot be opened the analysis still runs from files only — the database is a durability layer, not a dependency.

## Backtesting (V2.3.2)

A point-in-time portfolio backtest runs through the stored SQLite prices with
execution at the next session's close (+30 min availability lag) and records
runs, metrics, trades and the equity curve in `strategy_backtest_*`.

```bash
# submit a specification (see config/backtests/stagee_demo.json)
python scripts/backtest_report.py run --spec config/backtests/stagee_demo.json
# list stored runs, then render one report from the database
python scripts/backtest_report.py list
python scripts/backtest_report.py show <run_id>
```

Reports are written to `output/reports/<run_id>.md` and contain only values read
back from the database, so a report is traceable to its run. Acceptance helpers:

* `python scripts/accept_backtest_synthetic.py` — hand-checked scenarios on a
  throwaway database (writes `docs/development/stageE_synthetic_acceptance.md`).
* `python scripts/audit_working_db.py` — read-only audit of the working database.
* `python scripts/smoke_backtest_real.py` — short real-data run (writes
  `docs/development/stageE_real_smoke.md`).
* `python scripts/audit_backtest_retention.py` — integrity audit, no pruning.
* `python scripts/coverage_core.py` — stdlib statement coverage of the core
  modules (the `coverage` package is not a dependency).

*Initial Stage E delivery, before Round 8 fixes:* the gate was not met — the
tracer reported 88.2 %, with `fa/analysis/returns/base.py`,
`fa/analysis/returns/fx.py` and `fa/analysis/nav_estimator.py` below the
threshold. After the Round 8 coverage work the gate is met (see below). Stage D was accepted in Round 9 and **Stage E was accepted on 2026-11-21**,
which closes V2.3.2.

## Tests

The suite is written against the standard library (`unittest`) and also runs under `pytest`:

```bash
python -m unittest discover -s tests -t .
python -m pytest -q          # 兩者皆可；pytest.ini 已把收集範圍限定在 tests/
```

Verified environments:

- Python 3.10 — `python -m unittest discover -s tests -t .` → 1131 tests, `OK` (130 s)
- Python 3.11.16 (Anaconda `quant-finance`) + pytest 9.2.0 — `python -m pytest -q` → `510 passed, 35 subtests passed, 1 failed in 46.19s` (measured while the suite had 511 tests; the newer `test_price_pit.py` cases were not re-run under that interpreter yet); the single failure is `test_gui.py::…test_analysis_result_is_marshalled_to_main_thread`, where Tk cannot be created a second time inside pytest on that build (`_tkinter.TclError: invalid command name "tcl_findLibrary"`) — the same test passes under `unittest` on 3.10 and the GUI code is unaffected
- `python -m compileall -q fa fund_analyzer.py fund_analyzer_gui.py` passes

在受限沙箱下，conda 直譯器需要在專案目錄寫入暫存檔，第一次執行可能因權限被拒絕（`PermissionError`）；一般使用者的 conda 環境不受此限。

Note: the suite requires the project dependencies to be installed (see [Requirements](#requirements)). Test modules that import `akshare` — `test_data.py`, `test_qdii.py`, `test_pipeline.py` — cannot be imported when it is missing, so a bare interpreter will report import errors rather than test failures. `requirements.txt` pins the versions this project was verified against; install it into the project `.venv` with `install.bat` / `install.sh`.

Currently 1131 tests. Coverage by file: `test_application_estimation.py` — the estimation application service against a real temporary SQLite database: live runs pass a Repository into the estimator and verify persistence by reading the run back, a failed write is reported as `persisted=False` instead of looking saved, stored runs never call the estimator or write, asset detail resolves SQLite inputs → per-run JSON snapshot → latest JSON only when the `run_id` matches with the source label exposed, missing weight/contribution stay `—` (never 0), numeric fields reject NaN/±infinity instead of passing them through, and the canonical `cny_return`/legacy `return_cny` spellings both resolve, unknown runs raise `LookupError`, and history filtering/limit clamping match the Repository; `test_gui_startup.py` — the GUI entry stays cheap to import (no pandas/matplotlib) and only the analysis tab is built at startup; `test_gui_estimation_view.py` — the QDII view's pure presentation layer (13 summary fields with `—` for missing values, spec-ordered asset columns, status text instead of numbers for non-`ok` rows, quarter/scope/disclosure and source labels in the quality block, history rows in service order) plus the window modes through `WorkerBridge` (handover never calls the service, a stored `LookupError` is a message not a traceback, live `persisted` text comes only from the view, a late result is dropped after close) and a real-SQLite read-only browsing check that row counts and both JSON files are unchanged, `test_gui_lifecycle.py` — four-area notebook, window registry with a shared close budget, shutdown closing registered children, the fake-scheduler post-market contract and the QDII/post-market thread-isolation checks, `test_gui_worker.py` — one terminal result per task, progress ordering, late-message dropping after close, live-thread widget isolation (`_target`/`__self__`/`__closure__`/args) and the 2 s close clamp, `test_gui_formatting.py` — signed percentages, one-decimal coverage, `—` for missing values and colour never carrying the only signal, `test_backtest_data_access.py` — PIT-only price reads (a legacy row without `available_at` is invisible, `Repository.load_prices` is never called), per-point venue and quote-currency guards (an empty or mixed market/currency is rejected, `NYQ` and `US` still compare equal, `Instrument.market` is mandatory), FX publication gating with `rate_on_or_before()` falling back to an earlier session (`fx_rate()` on a holiday returns the last public rate, never an unpublished one, and CNY needs no series), `revision_id` identity carried out of `prices_as_of()`, and the isolation checks (a subprocess import of `fa.backtesting` loads none of `requests`/`yfinance`/`akshare`; an AST scan forbids `sqlite3`, `execute*` and `load_prices` inside the package); `test_backtest_costs.py` — commission, adverse slippage and FX spread against hand formulas (5 bps with no floor, a 2.0 minimum, 10 bps slippage in both directions, 20 bps on a foreign conversion, zero for CNY), half-even rounding at 2 dp and 6 dp, and rejection of negative/NaN/infinite notional; `test_backtest_engine.py` — a fill at the official close even when `adj_close` differs (and the post-trade mark still uses `adj_close`), a foreign dividend credited only when its FX rate is published (never at a silent 1:1), a venue closed inside the union grid skipping its orders without a `missing-price` anomaly while a real gap still raises one, the hand-computed two-asset case at 1e-9 (zero costs) and its accounting identity with costs, equal/fixed/momentum weights (the positive-part normalisation and cash when every score is non-positive), the four rebalance frequencies and the drift threshold, sells before buys, cash-constrained scaling without a negative balance, whole-share mode, a missing price or FX skipping only what it must, dividends and splits on a raw close series, the equity curve and drawdown, and determinism; `test_backtest_pit.py` — a price published one microsecond after the decision instant is invisible, an execution price published late is never substituted, two databases differing only in an unpublished close produce identical trades and equity curves (while publishing it changes the run), a revision does not change decisions made before it, and legacy rows without `available_at` are never used; `test_backtest_models.py` — `StrategySpec` validation (required `initial_capital`, CNY-only base, unique universe, fixed weights summing to 1, momentum window floor, anchor ranges), NaN/Infinity rejection on every numeric field, immutable weights, long-only positions/cash, and `BacktestMetrics` reporting `None` rather than `0` for an undefined ratio; `test_price_pit.py` — the price provider's vendor-stamp conversion (US opening bell, venues east of UTC), window/duplicate/corruption handling, the window-edge cache that also demands publication metadata, the download plan giving an older evidence gap priority over the overlap rule, legacy rows without availability being re-downloaded instead of trusted, per-date canonical completeness (a superseded `unknown` duplicate no longer forces a download), the five-session overlap, validation that never inverts a quote, revision rows that keep the stored version and push the new availability to the revision instant, `prices_as_of()` boundaries compared in Python at full datetime precision (visible exactly at `available_at`, not one microsecond earlier, the old version still visible one microsecond before `replaced_at` and gone at it, identical for every timezone), the `start`/`end` window pushed into both UNION branches (inclusive edges, a same-date revision still resolved by instant, a revision outside the range never leaking) and the execution instant at close + 30 min; `test_fx_integration.py` — the estimator's FX switch and point-in-time reads (an unpublished bar is excluded and counted, the same bar appears once published, a pipeline failure never downgrades to non-PIT rates, `DatabaseError` falls back to the legacy download path, and the switch/CNY/no-repository paths report the right `fx_source`); `test_fx_pipeline.py` — cache-window completeness (edges, not the newest date, plus legacy rows whose publication time is missing are enriched from the stored dates instead of being mistaken for a usable cache), the five-session/five-trading-day overlap, per-bar validation, availability stamping, PIT boundaries and multi-currency isolation; `test_fx_provider.py` — London trading-date mapping at the GMT/BST boundaries, the EU-rule offset fallback against `ZoneInfo` for every day 2025–2030, bar parsing with range/duplicate/corruption handling, direction suspicion without transformation, CNY and unsupported-currency no-ops, every HTTP failure path, the facade's unchanged signatures, and `fx_series_map_from_fetch()` provenance; `test_fx_repository.py` — FX bars keep their full time provenance through a database round trip, an identical re-download creates no revision, a materially different rate is ledged with the *stored* row's own timestamps, within-tolerance and enrichment-only writes stay out of the ledger, a vendor-dropped date is never deleted, and `fx_as_of()` honours every publication boundary while `load_fx()` still sees legacy rows; `test_time_model.py` — tz-aware timestamps, the three time fields, availability provenance and DST at the New York close; `test_migration_002.py` — upgrade on a copy of the frozen V2.2.3 baseline (row preservation, conservative `look_ahead_status` back-fill, interrupted-migration rollback, re-run), plus the V2.2 read paths against a version-2 database; `test_migration_003.py` — the `fx_revisions` ledger on the same frozen baseline (2 → 3, documented columns and indexes, empty ledger, untouched row counts/price/nav data, exactly one new table, idempotent re-run, injected-failure rollback, byte-identical baseline); `test_backtest_metrics.py` — every §7 ratio against a hand formula (CAGR, annualised volatility, Sharpe with a daily risk-free rate, downside-deviation Sortino, maximum drawdown, Calmar, turnover, FIFO hit rate, exposure, cost drag), `None` instead of `0` when a ratio is undefined, and an empty curve rejected; `test_backtest_runner.py` — the full persistence round trip (run, metrics, trades with `seq`, equity curve), canonical spec ordering, `run_id` composition with `initial_capital`, idempotent second call, `force=True` producing a `-N` suffix, a capital change producing a new run, naive `now` rejected, and a failed write leaving zero runs; `test_backtest_fingerprint.py` — a revision moves the content digest while the row count, date range and `max(fetched_at)` stay equal, the digest is built **per decision instant** and uses a conservative session-price superset for every open venue session, while a MOMENTUM strategy additionally hashes the exact last `window + 1` observations consumed by the engine on each rebalance day, including closed venues (the shared `is_rebalance_day()` helper keeps engine and fingerprint in step; pre-run-start bars are excluded from FIXED/EQUAL inputs, but remain included for MOMENTUM when they fall within a consumed `window + 1` observation tail) (a correction published later leaves every earlier session's digest identical while the post-publication window changes), the FX input is the rate the engine actually consumed (`data_access.fx_rate()` plus its observation date, so an older observation that still prices the session is hashed instead of reported missing, and the same rate on a different date is distinguishable), the momentum window is taken per venue (a sparse Hong Kong series keeps its whole window while a dense US bar older than every window stays out), a plain re-download that only refreshes `fetched_at` leaves the digest unchanged, a new run after a published revision gets a new id, identical data reuses the stored run, and two databases with different histories hash differently; `test_backtest_execution_instant.py` — the execution bar is invisible at the close, visible at close + 30 min, the recorded fill is the official close with `price_source="close"`, and a late publication is never substituted; `test_backtest_benchmark.py` — the benchmark read through the same PIT path **session by session at that session's own decision instant** (a bar published later cannot fill an earlier point, a late FX rate cannot convert it, a shut venue carries, an open venue without data is empty plus `benchmark-missing-price`/`benchmark-missing-fx`) and its consumed prices (with the price actually used and `price_source`) and per-session FX rates joining the content fingerprint (a revised GBP/CNY moves the digest even for a USD-only strategy); `test_backtest_run_identity.py` — a dividend-only and a split-only correction changing the post-publication window's digest while every earlier session's digest stays identical, anomalies surviving a cache hit and a reload, and changed execution settings or a changed `FA_BACKTEST_COMMISSION_BPS` producing a new run (an explicit `CostModel`, even the default-valued one, still winning through the `COST_MODEL_DEFAULT` sentinel); `test_backtest_data_integrity.py` — venue flips, empty venues, currency mismatches and a non-CNY FX base aborting `prices()`, `run_engine()` and `data_fingerprint()` while a genuine hole stays `BacktestDataMissingError`; `test_migration_004.py` — the four backtest tables on the frozen baseline (3 → 4, documented columns and index, untouched legacy rows, idempotent re-run, injected-failure rollback, fresh database, additive-only SQL); `test_database.py` — schema creation, point-in-time snapshot queries, price upsert semantics (legacy time columns kept, `dividend`/`split` revisions only when the stored value really changes, enrichment columns never wiped by an empty value), FX base-currency rejection, estimation runs and backtest rows; `test_utils.py` — config merging, quarter labelling, ticker normalisation, cache TTL/pruning, socket timeout; `test_data.py` — `pingzhongdata` NAV parsing, holdings retry/backoff, fund-home-page holdings parsing and the page/stale-snapshot fallbacks; `test_analysis.py` — indicators (warm-up and the absence of ATR/ADX), downside deviation / Sortino against hand-computed values, IR alignment on common dates; `test_qdii.py` — China calendar and 15:00 cutoff, valuation-date resolution, point-in-time portfolio selection, per-holding returns with target/base staleness, unknown-currency rejection, coverage aggregation and local/FX/interaction attribution, Yahoo symbol/currency mapping, FX series, portfolio fallback order, the estimator and its input snapshot, one-row-per-`run_id` history and the backtest metrics; `test_quality_wiring.py` — stale prices reaching the quality grade (2 sessions kept, 3 excluded), stale FX, assumption-rate cash, unknown assets and asset classification (source-declared types only, no ticker-shape guessing); `test_pipeline.py` — the end-to-end pipeline with network calls stubbed, cache survival, output pruning, error path, QDII estimate wiring; `test_gui.py` — log queue, crash handler, `shutdown()`, post-market window reuse and close.

Additional suites added while closing the Stage E coverage gate: `test_returns_coverage_paths.py` — the uncovered `returns/base.py` and `returns/fx.py` diagnostics (`series_from_frame()` on empty/columnless/unparsable rows, NaN close falling back to `adj_close`, the `MISSING_FX`/`STALE_BASE_FX`/`MISSING_TARGET_PRICE`/`STALE_BASE_PRICE`/`UNSUPPORTED_ASSET` conversions); `test_nav_estimator_coverage_paths.py` — the estimator's database, FX and holdings failure paths (a PIT read failure drops only that currency, a `DatabaseError` falls back to the legacy download, an unpublished currency is counted, stale-snapshot and old-disclosure warnings, a cache-first `_market_data()`); `test_core_coverage_edges.py` — a USD cash holding without FX, an unknown return status graded as unsupported, the `base_nav_fresh` and `calendar_fallback` penalties, and `renormalised_error()` without an estimate; `test_returns_calculator_guards.py` — each concrete calculator rejecting a non-`Asset` input.

Coverage is measured with `python scripts/coverage_core.py` (stdlib `sys.settrace`, because `coverage.py` is not a dependency): every documented core module is above the 90 % threshold and the total is **99.4 %** (704/708). The measurement counts every executable statement — module-level, class-body and function-body, docstrings excluded — which is what `sys.settrace` can observe because it is installed before `unittest` discovery. The per-module table, the missing line numbers and the explanation of the four remaining defensive lines are in `docs/development/stageE_coverage.md` and spec §19.4.

## Troubleshooting

### SSL error when fetching data

```
SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING]'))
```

This is a temporary network issue with the data source (Eastmoney, usually the `fundf10` CDN). Because only the latest quarter is needed, the fund home page's top 10 is tried first and normally succeeds in one request; if it fails too, the per-year archive API is retried up to 3 times per year with backoff, and the last snapshot on disk is used as a final fallback, so the run still produces charts and — for a QDII fund — a NAV estimate. If it keeps failing, wait a few minutes, change network, or check the proxy.

Since V2.1 the failure is classified before it is logged, so the message tells you which layer broke:

```
[000369] 2026 年持仓获取失败（已重試 3 次）: TLS 握手階段被中斷（網絡路徑 / 中間設備 / 對端 CDN 問題，非程式錯誤）（APIError）
```

Categories: TLS handshake interrupted, connection closed by peer, timeout, DNS failure, other. If it says TLS, the request never reached Eastmoney — a quick manual check (print the proxy environment, resolve the host, try a raw TLS handshake, then plain `requests` with and without the environment proxy) tells a local proxy/TLS problem from a server-side one. Do not add retries before that check: retrying a broken TLS path just adds ~50 s to every run. Note also that the archive host (`fundf10.eastmoney.com`) is the only source of per-quarter history, which is why the QDII flow no longer depends on it — see "Where the portfolio comes from".

The QDII estimate reports `status: "insufficient-data"` and the CLI prints `未產生估算：目標日 … 之前尚無已披露的 QDII 持倉` in two cases: **every** portfolio source failed (no archive, no reachable home page, no snapshot), or the only snapshots on disk were disclosed **after** the target date (look-ahead protection — the estimator refuses to use future information). That is deliberate, because an estimate without an admissible portfolio would be fabricated.

### The program seems to hang

Some data hosts occasionally accept the connection and then stall during the TLS handshake. The tool applies `FA_REQUEST_TIMEOUT` (default 30 s) as the socket default timeout, so such a connection raises and the step degrades; lower the value for faster failure, e.g.:

```cmd
set FA_REQUEST_TIMEOUT=15
python fund_analyzer.py 017436 -v
```

### `ModuleNotFoundError: No module named 'fund_analyzer'`

Run the program from the folder that contains `fund_analyzer.py`:

```bash
cd fund-analyzer
python fund_analyzer_gui.py
```

Or if using a Windows shortcut, set the shortcut's **Start in** field to the project folder.

### GUI closes immediately after double-clicking

Run it from a terminal to see the error:

```bash
python fund_analyzer_gui.py
```

Under `pythonw.exe` (no console) any traceback is written to `crash.log` and `gui_debug.log` instead of a terminal.

If the window vanishes with **no** traceback in either file, the process died inside a native library (Windows event log shows an exception code such as `0xC06D007F` and the faulting module is `KERNELBASE.dll`). Check `fatal.log`, which `faulthandler` writes on such aborts, and mention its content in a bug report.

### The combined report does not open

`pywebview` is optional: without it the tool falls back to the default browser. If neither works, open `<fund_code>_master_report.html` manually — it embeds the individual reports as iframes.

## 基準指數代碼參考 Benchmark Reference

### 常見基準指數

| 指數 | 代碼 | 市場 |
| :--- | :--- | :--- |
| 納斯達克100 | `.NDX` | 美股 |
| 標普500 | `.INX` | 美股 |
| 道瓊斯工業 | `.DJI` | 美股 |
| 滬深300 | `sh000300` | A股 |
| 中證500 | `sh000905` | A股 |
| 創業板指 | `sz399006` | A股 |
| 上證指數 | `sh000001` | A股 |
| 恒生指數 | `HSI` | 港股 |
| 恒生中國企業 | `HSCEI` | 港股 |

### 基準市場對照表

**基準市場** 參數告訴程式去哪裡拉數據。不同市場用不同的 AKShare 接口：

| 市場 | AKShare 接口 | 代碼格式 |
| :--- | :--- | :--- |
| 美股 | `index_us_stock_sina` | 以 `.` 開頭，如 `.NDX` |
| A股 | `stock_zh_index_daily_em` | 以 `sh` / `sz` / `bj` 開頭，如 `sh000300` |
| 港股 | `stock_hk_index_daily_em` | 直接是名字或拼音，如 `HSI` |

**選擇 `auto` 時，程式自動根據代碼格式判斷：**

```
.NDX        →  以 . 開頭       →  us
sh000300    →  sh/sz/bj 開頭   →  cn
HSI         →  都不是          →  hk
```

### CLI 用法

```bash
# 美股基準
python fund_analyzer.py 017436 --benchmark .NDX --benchmark-market auto --show

# A股基準
python fund_analyzer.py 000051 --benchmark sh000300 --benchmark-market auto --show

# 港股基準
python fund_analyzer.py 005827 --benchmark HSI --benchmark-market auto --show

# 跳過基準
python fund_analyzer.py 017436 --benchmark "" --show
```

## Disclaimer

This tool is for **educational and research purposes only**. Data is fetched from public sources (Eastmoney via AKShare, Sina, Yahoo Finance) and may be delayed or inaccurate. Nothing in this project constitutes investment advice.

## Credits

- Data: [AKShare](https://github.com/akfamily/akshare) / 天天基金网 / [yfinance](https://github.com/ranaroussi/yfinance)
- Charts: [Plotly](https://plotly.com/python/)
- GUI: Tkinter / pywebview

## License

[MIT](./LICENSE)

## Development records

- `docs/development/fund_analyzer_project_overview.md` — project overview, plan and current status (hand-off document).
- `docs/development/v24_acceptance_matrix.md` — Stage 0/A/B/C/D/E acceptance matrix with per-item evidence.
- `docs/development/backlog.md` — non-blocking review notes kept for the next developer.
- `docs/development/v24_evidence/` — the Stage E evidence files and screenshots.
