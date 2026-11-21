# Usage

How to run Fund Analyzer from the GUI or the command line. Installation and the
analysis reference live in the [README](../README.md); backtest commands are in
the README section [Strategy backtesting](../README.md#strategy-backtesting).


## GUI (recommended)

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

## Command line

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
