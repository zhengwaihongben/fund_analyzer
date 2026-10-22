# Fund Analyzer

**Note:**
- This repository is still under construction, and I may develop more functions.
- If you find any problem or any suggestion, please contact me, or upload your branch.
- Most of the codes are built by *Deepseek-v4.1-flash*.

## Introduction

A one-stop tool for analyzing Chinese mutual funds (公募基金). Input a fund code, get NAV trends, holdings, technical indicators, risk-adjusted performance metrics, and real-time pre/post-market estimates — all as interactive HTML reports.

### Motivation of developing this project

I currently invest the funds by Alipay(支付宝), but most funds on it do not provide complete information or insights, and hence I decided to develop this project. For QDII funds (which invest in US stocks), there is a severe T+1 time-lag. This tool uses US pre/post-market data to solve this "blind box" problem.

## Features

- **NAV Trend** — Full historical unit NAV with interactive zoom
- **Top 10 Holdings** — Quarterly stacked area chart showing weight changes
- **SMA / BOLL / RSI / MACD** — Comprehensive technical indicators in one interactive chart
- **Risk Metrics** — Sharpe, Sortino, Calmar, Information Ratio, Max Drawdown
- **Cumulative Excess Return** — Fund performance vs a user-selected benchmark
- **Start Date Filter** — Limit the tracking range to a specific start date
- **Multi-Market Benchmark** — Support US / CN / HK indices with auto-detection
- **Auto AUM Fetching** — Automatically fetch the latest Assets Under Management
- **Post-Market Estimate** — Fetch US pre/post-market data to estimate tonight's NAV change
- **Auto-Alert Script** — Run daily at 14:30 via Windows Task Scheduler to alert you of major anomalies

## Configuration

### Requirements

- Python 3.10 or newer
- Internet connection (for fetching data via AKShare and yfinance)
- Works on Windows / macOS / Linux

### Install dependencies

Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/fund-analyzer.git
cd fund-analyzer
```

**Windows** — double-click `install.bat`, or run in terminal:

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
pip install -r requirements.txt
```

*(Note: Make sure `yfinance` and `pywebview` are included in your `requirements.txt` for the advanced features.)*

## Usage

### Option A — GUI (recommended)

```bash
python fund_analyzer_gui.py
```

After launching:

1. Enter a **fund code** (6 digits, e.g. `017436`)
2. Optionally set a **benchmark**, **risk-free rate**, and **start date** (`YYYY-MM-DD`)
3. Click **▶ Start Analysis** (AUM is fetched automatically)
4. Wait for the log to finish. You can then:
   - Click **"打开综合报告"** to view the overall Webview report.
   - Click **"盘后监控"** to see real-time estimated NAV changes based on US pre/post-market data.

**Windows shortcut** — double-click `launch.vbs` to launch the GUI without a terminal window.

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
| `--benchmark CODE` | `.NDX` | Benchmark index; pass `""` to skip IR and excess-return chart |
| `--benchmark-market` | `auto` | Benchmark market: `auto` / `us` / `cn` / `hk` |
| `--risk-free RATE` | `0.02` | Annual risk-free rate |
| `--start-date DATE` | — | Tracking start date (YYYY-MM-DD); empty = from inception |
| `--no-cache` | — | Ignore holdings cache, force refresh |
| `--show` | — | Open all HTML reports in browser after generating |
| `-v, --verbose` | — | Verbose logging |

**Example:**

```bash
# Full analysis from inception
python fund_analyzer.py 017436 --risk-free 0.02 --benchmark .NDX --show

# Track only from a specific date
python fund_analyzer.py 017436 --start-date 2023-01-01 --show
```

## Advanced Features: Post-Market & Auto-Alerts

### 1. Post-Market Monitor (GUI)

Because QDII funds trade on US markets, the official NAV you see at 15:00 Beijing Time is actually yesterday's US close. To solve this "blind box", the GUI includes a **Post-Market Monitor**.

- **How it works**: It reads your latest holdings CSV, fetches the pre/post-market prices of the top 10 US stocks (via `yfinance`), and calculates a weighted estimated return.
- **When to use**: Around 14:30 - 14:55 Beijing Time, before you place an order on Alipay.
- **What it shows**: 
  - Yesterday's official NAV change.
  - Nasdaq 100 Futures (`NQ=F`) change.
  - Estimated tonight's change based on current pre/post-market prices.
  - Color-coded (Red for Up, Green for Down) details for each stock.

### 2. Auto-Alert Script (Windows Task Scheduler)

If you don't want to open the GUI every day, you can use the `auto_alert.py` script to run in the background automatically.

**Step 1: Configure which funds to monitor**
The first time you run `python auto_alert.py`, it generates a `alert_config.txt` in the project folder. Open it with Notepad and add your fund codes (one per line):

```text
# Monitor my QDII funds
017436
000369
```

**Step 2: Set up Windows Task Scheduler**
1. Press `Win + S`, search for **Task Scheduler** (任务计划程序).
2. Click **Create Basic Task** on the right.
3. **Name**: `FundAnalyzer_AutoAlert`.
4. **Trigger**: Daily, Start time: `14:30:00`.
5. **Action**: Start a program.
6. **Program/script**: Path to your `pythonw.exe` (e.g., `C:\Users\user\anaconda3\envs\quant-finance\pythonw.exe`).
7. **Add arguments**: `"C:\path\to\your\fund_analyzer\auto_alert.py"`.
8. **Start in**: `C:\path\to\your\fund_analyzer`.
9. Finish and open Properties. Go to the **Settings** tab and check **"Run task as soon as possible after a scheduled start is missed"**.
10. Click OK.

**What happens next?**
Every day at 14:30, it silently checks the data. If any of the following thresholds are met, a window will pop up in the bottom right corner of your screen:
- Nasdaq 100 Futures moves > ±1%.
- Estimated portfolio return > ±1.5%.
- Any top 10 stock moves > ±3% (usually due to earnings reports).

## Output

After running the main analysis, results are saved under `output/<fund_code>/`, such as:

```
output/017436/
├── 017436_daily_nav.csv               # Raw NAV data
├── 017436_trend.html                  # NAV trend chart
├── 017436_holdings_raw.csv            # Holdings (raw)
├── 017436_holdings_clean.csv          # Holdings (cleaned)
├── 017436_holdings_area.html          # Holdings stacked area chart
├── 017436_daily_nav_sma_boll.csv      # Technical indicators
├── 017436_nav_sma_boll.html           # SMA/BOLL/MACD/RSI interactive chart
├── 017436_risk_metrics.html           # Risk metrics report
├── 017436_excess_return.html          # Cumulative excess return vs benchmark
└── 017436_summary.json                # Summary of all metrics
```

All `.html` files are fully interactive — open them in any modern browser.

## Metrics Explained

| Metric | Formula | Interpretation |
| :--- | :--- | :--- |
| **Sharpe Ratio** | `(Ann. Return − Risk-free) / Ann. Volatility` | Excess return per unit of total risk |
| **Sortino Ratio** | `(Ann. Return − Risk-free) / Downside Volatility` | Excess return per unit of downside risk |
| **Calmar Ratio** | `Ann. Return / Max Drawdown` | Return per unit of maximum loss |
| **Information Ratio** | `Ann. Active Return / Tracking Error` | Active return per unit of tracking error |
| **Max Drawdown** | Largest peak-to-trough decline | Worst historical loss |
| **Cumulative Excess Return** | `Fund Cum NAV / Benchmark Cum NAV − 1` | Total alpha generated vs benchmark over time |

## Troubleshooting

### SSL error when fetching data

```
SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING]'))
```

This is a temporary network issue with the data source (Eastmoney). The program already retries up to 3 times per year. If it still fails, wait a few minutes, change network, or check proxy.

### `ModuleNotFoundError: No module named 'fund_analyzer'`

Run the program from the folder that contains `fund_analyzer.py`:

```bash
cd fund-analyzer
python fund_analyzer_gui.py
```

Or if using a Windows shortcut, set the shortcut's **Start in** field to the project folder.

### GUI closes immediately after double-clicking

Run from terminal to see the error:

```bash
python fund_analyzer_gui.py
```

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

This tool is for **educational and research purposes only**. Data is fetched from public sources (Eastmoney via AKShare, Yahoo Finance) and may be delayed or inaccurate. Nothing in this project constitutes investment advice.

## Credits

- Data: [AKShare](https://github.com/akfamily/akshare) / 天天基金网 / [yfinance](https://github.com/ranaroussi/yfinance)
- Charts: [Plotly](https://plotly.com/python/)
- GUI: Tkinter / pywebview

## License

[MIT](./LICENSE)