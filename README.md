# Fund Analyzer

**Note:**
- This repository is still under construction, and I may develop more functions.
- If you find any problem or any suggestion, please contact me, or upload your branch.
- Most of the codes are built by *Deepseek-v4.1-flash*.

## Introduction

A one-stop tool for analyzing Chinese mutual funds (公募基金).
Input a fund code, get NAV trends, holdings, technical indicators,
and risk-adjusted performance metrics — all as interactive HTML reports.

### Motivation of developing this project

I currently invest the funds by Alipay(支付宝), but most funds on it do not provide a complete information or insights, and hence I devided to develop this project.

## Features

- **NAV Trend** — Full historical unit NAV with interactive zoom
- **Top 10 Holdings** — Quarterly stacked area chart showing weight changes
- **SMA / BOLL** — 5/10/60/120/250-day moving averages + Bollinger Bands
- **Risk Metrics** — Sharpe, Sortino, Calmar, Information Ratio, Max Drawdown
- **Cumulative Excess Return** — Fund performance vs a user-selected benchmark
- **Start Date Filter** — Limit the tracking range to a specific start date
- **Multi-Market Benchmark** — Support US / CN / HK indices with auto-detection
- **AUM Annotation** — Optional AUM shown on the risk report
- **Local Caching** — Holdings data cached locally for fast re-runs

## Configuration

### Requirements

- Python 3.10 or newer
- Internet connection (for fetching data via AKShare)
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

## Usage

### Option A — GUI (recommended)

```bash
python fund_analyzer_gui.py
```

After launching:

1. Enter a **fund code** (6 digits, e.g. `017436`)
2. Optionally fill in **AUM** (亿元), **risk-free rate**, and **benchmark**
3. Optionally set a **start date** (`YYYY-MM-DD`) to limit the tracking range;
   leave it empty to track from the fund's inception
4. Click **▶ Start Analysis**
5. Wait for the log to finish, then double-click any file in the result list to open it

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
| `--aum VALUE` | — | Fund AUM in 亿元 (optional) |
| `--start-date DATE` | — | Tracking start date (YYYY-MM-DD); empty = from inception |
| `--no-cache` | — | Ignore holdings cache, force refresh |
| `--show` | — | Open all HTML reports in browser after generating |
| `-v, --verbose` | — | Verbose logging |

**Example:**

```bash
# Full analysis from inception
python fund_analyzer.py 017436 --aum 47.06 --risk-free 0.02 --benchmark .NDX --show

# Track only from a specific date
python fund_analyzer.py 017436 --start-date 2023-01-01 --aum 47.06 --show
```

## Output

After running, results are saved under `output/<fund_code>/`, such as:

```
output/017436/
├── 017436_daily_nav.csv               # Raw NAV data
├── 017436_trend.html                  # NAV trend chart
├── 017436_holdings_raw.csv            # Holdings (raw)
├── 017436_holdings_clean.csv          # Holdings (cleaned)
├── 017436_holdings_area.html          # Holdings stacked area chart
├── 017436_daily_nav_sma_boll.csv      # Technical indicators
├── 017436_nav_sma_boll.html           # SMA/BOLL interactive chart
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

This is a temporary network issue with the data source (Eastmoney).
The program already retries up to 3 times per year. If it still fails:

- Wait a few minutes and retry
- Try a different network (mobile hotspot, VPN)
- Check your firewall / proxy settings

### `ModuleNotFoundError: No module named 'fund_analyzer'`

Run the program from the folder that contains `fund_analyzer.py`:

```bash
cd fund-analyzer
python fund_analyzer_gui.py
```

Or if using a Windows shortcut, set the shortcut's **Start in** field to the
project folder.

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
### 使用範例

#### 華寶納斯達克精選（017436）

| 參數 | 值 |
| :--- | :--- |
| 基金代碼 | `017436` |
| 基準指數 | `.NDX` |
| 基準市場 | `auto` |

### 兩個參數的作用

| 參數 | 作用 | 建議填什麼 |
| :--- | :--- | :--- |
| **基準指數** | 比賽對手是誰 | 基金合同裡的「業績比較基準」 |
| **基準市場** | 去哪個接口拉數據 | 一般填 `auto` 就行 |

### 影響的輸出

填了基準指數後，會多出兩個東西：

1. **Information Ratio（IR）** — 顯示在風險指標圖頂部
2. **累計超額收益曲線** — 單獨一張 HTML 圖（`{基金代碼}_excess_return.html`）

**兩個都留空**：程式跳過 IR 和超額收益圖，其他功能完全正常。


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

This tool is for **educational and research purposes only**. Data is fetched
from public sources (Eastmoney via AKShare) and may be delayed or inaccurate.
Nothing in this project constitutes investment advice.

## Credits

- Data: [AKShare](https://github.com/akfamily/akshare) / 天天基金网
- Charts: [Plotly](https://plotly.com/python/)
- GUI: Tkinter

## License

[MIT](./LICENSE)