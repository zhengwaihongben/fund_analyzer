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
- **Top 10 Holdings** — Quarterly stacked area chart showing weight changes (holdings are fetched for at most the last 4 years)
- **Technical Indicators** — SMA / BOLL / RSI / MACD plus NAV-based KDJ, volatility and directional-strength proxies in one interactive chart
- **Risk Metrics** — Annualised return & volatility, Sharpe, Sortino, Calmar, Information Ratio, Tracking Error, Max Drawdown, AUM
- **Cumulative Excess Return** — Fund performance vs a user-selected benchmark, aligned on common trading days
- **Post-Market Estimate** — US pre/post-market prices of the latest disclosed top-10 holdings, with an explicit **coverage ratio** and each holding's contribution to the fund NAV
- **Start Date Filter** — Limit the tracking range to a specific start date
- **Multi-Market Benchmark** — Support US / CN / HK indices with auto-detection
- **Auto AUM Fetching** — Automatically fetch the latest Assets Under Management
- **Holdings Cache with TTL** — Reuse the previous holdings download for up to 7 days (`--no-cache` forces a refresh)
- **Fast, dependency-light NAV fetch** — NAV history is read straight from the `pingzhongdata` blob (one HTTP request, `re` + `json`), with AKShare as fallback; no JS engine is instantiated
- **Resilient downloads** — holdings are retried 3× per year with 2 s / 4 s backoff, and a socket-level timeout (`FA_REQUEST_TIMEOUT`) bounds hosts that stall mid-handshake
- **Unified Logging** — Console + rotating log file, GUI log pane fed through a queue, single crash-report path (`crash.log`), plus `fatal.log` (faulthandler) for native aborts that never reach Python
- **Test suite** — 48 stdlib `unittest` tests covering config, data layer, indicators, risk metrics, the pipeline and the GUI lifecycle

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
│   │   ├── holdings.py         # quarterly holdings, TTL cache, 3× retry with backoff, top-10 selection
│   │   └── market.py           # yfinance bridge: spawns yf_helper.py, no yfinance import in-process
│   ├── analysis/
│   │   ├── technical.py        # SMA / BOLL / RSI / MACD + NAV-based KDJ and volatility proxies
│   │   ├── risk.py             # annualised return & vol, Sharpe, Sortino, Calmar, IR/TE, MaxDD
│   │   └── post_market.py      # US pre/post-market estimate with coverage ratio and contribution
│   ├── viz/
│   │   ├── charts.py           # all interactive Plotly HTML charts
│   │   └── master.py           # combined report (iframes), opened by the GUI
│   ├── utils/
│   │   ├── logging_setup.py    # root logger: console + rotating file (2 MB × 3)
│   │   ├── dates.py            # quarter-label parsing (YYYYQn), latest-quarter detection
│   │   ├── tickers.py          # stock code → Yahoo ticker (0700 → 0700.HK, BRK.B → BRK-B)
│   │   ├── cache.py            # cache freshness / read / write, stale-output pruning
│   │   └── net.py              # socket default timeout, so stalled TLS handshakes fail fast
│   └── gui/
│       ├── launcher.py         # theme, excepthook, faulthandler → fatal.log, Tk main loop
│       ├── app.py              # main window, worker thread, queue-polled log pane, shutdown()
│       ├── post_market.py      # post-market monitor window (60 s refresh, single instance)
│       └── crash.py            # one crash path: log → crash.log → messagebox
├── tests/                      # stdlib unittest suite (no pytest needed)
│   ├── test_utils.py           # config merging, quarters, tickers, cache TTL, socket timeout
│   ├── test_data.py            # pingzhongdata parsing, holdings retry/backoff
│   ├── test_analysis.py        # indicators (incl. warm-up), downside deviation, Sortino, IR alignment
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

## Configuration

### Requirements

- Python 3.10 or newer
- Internet connection (AKShare / 天天基金网 for NAV and holdings, Yahoo Finance for macro factors and post-market prices)
- Python packages from `requirements.txt`: `akshare`, `pandas`, `numpy`, `plotly`, `requests`, `yfinance` (the last one is required by `yf_helper.py` for the post-market estimate)
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
| `--no-cache` | — | Ignore holdings cache, force refresh |
| `--show` | — | Open all HTML reports in the browser after generating |
| `-v, --verbose` | — | Verbose (DEBUG) logging |

**Example:**

```bash
# Full analysis from inception
python fund_analyzer.py 017436 --risk-free 0.02 --benchmark .NDX --show

# Track only from a specific date
python fund_analyzer.py 017436 --start-date 2023-01-01 --show
```

## Output

Results are saved under `output/<fund_code>/`:

```
output/017436/
├── 017436_daily_nav.csv               # Raw NAV data (also the NAV cache)
├── 017436_trend.html                  # NAV trend chart
├── 017436_holdings_raw.csv            # Holdings (raw, cached up to 7 days)
├── 017436_holdings_clean.csv          # Holdings (cleaned: QuarterLabel / Weight / StockCode / Stock)
├── 017436_holdings_area.html          # Holdings stacked area chart
├── 017436_daily_nav_sma_boll.csv      # NAV + all technical indicators
├── 017436_nav_sma_boll.html           # Interactive indicator chart
├── 017436_risk_metrics.html           # Risk metrics report
├── 017436_excess_return.html          # Cumulative excess return vs benchmark
├── 017436_summary.json                # Summary: success flag, metrics, file list
└── 017436_master_report.html          # Combined report (generated by the GUI only)
```

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

## Settings & Logs

### Environment variables

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `FA_CACHE_TTL_DAYS` | `7` | Holdings cache lifetime in days (`<= 0` = never expire) |
| `FA_REQUEST_TIMEOUT` | `30` | Socket default timeout in seconds; third-party HTTP libraries expose no timeout, so this bounds hung connections |
| `FA_LOG_FILE` | `fund_analyzer.log` | Log file path |

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
- Holdings are downloaded per year (at most the current year and the previous three, i.e. ~4 years). Each year is attempted up to 3 times with a 2 s / 4 s backoff, because the Eastmoney CDN occasionally drops the TLS handshake; a year that still fails is skipped and logged, and if every year fails the run continues with "no holdings data".
- `<fund_code>_holdings_raw.csv` is the holdings cache. A run reuses it when it is younger than `FA_CACHE_TTL_DAYS` and `use_cache` is true (`--no-cache` or unchecking the GUI box forces a refresh). A corrupt cache raises `CacheError`, is logged and re-downloaded instead of aborting.
- Outputs are pruned only after a successful run: derived HTML/CSV/JSON files that this run did not produce are removed, while raw/cache files (`*_daily_nav.csv`, `*_holdings_raw.csv`, `*_holdings_clean.csv`) and unrelated files are kept.

## Tests

The suite uses the standard library only (`unittest`, no `pytest` needed):

```bash
python -m unittest discover -s tests -t .
```

Currently 48 tests. Coverage by file: `test_utils.py` — config merging, quarter labelling, ticker normalisation, cache TTL/pruning, socket timeout; `test_data.py` — `pingzhongdata` NAV parsing, holdings retry/backoff; `test_analysis.py` — indicators (warm-up and the absence of ATR/ADX), downside deviation / Sortino against hand-computed values, IR alignment on common dates; `test_pipeline.py` — the end-to-end pipeline with network calls stubbed, cache survival, output pruning, error path; `test_gui.py` — log queue, crash handler, `shutdown()`, post-market window reuse and close.

## Troubleshooting

### SSL error when fetching data

```
SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING]'))
```

This is a temporary network issue with the data source (Eastmoney). Holdings requests are retried up to 3 times per year with backoff, and the run degrades gracefully (holdings become unavailable instead of crashing). If it keeps failing, wait a few minutes, change network, or check the proxy.

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
