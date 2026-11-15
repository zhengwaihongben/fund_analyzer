"""Post-market estimate built from holdings regular/pre-market quotes."""

from pathlib import Path

import pandas as pd

from fa.data.holdings import top_holdings
from fa.data.market import _yf_download_df, _yf_ticker_history
from fa.utils.logging_setup import get_logger
from fa.utils.tickers import to_yf_ticker


log = get_logger(__name__)

def fetch_post_market_estimate(fund_code: str, save_dir: Path) -> dict:
    """
    读取最新持仓，获取盘后/盘前数据，计算预估涨跌幅（批量请求优化版）。
    """
    result = {
        "status": "success",
        "fund_code": fund_code,
        "yesterday_official_return": None,
        "estimate_return": None,            # 已覆蓋持倉的平均漲跌幅（%）
        "covered_weighted_return": None,    # 已覆蓋持倉對淨值的加權貢獻（%）
        "coverage_ratio": None,             # 有盤後數據的持倉佔基金淨值比重（0~1）
        "total_weight": None,               # 前十大持倉合計權重（0~1）
        "nq_futures_return": None,
        "holdings": [],
        "message": ""
    }

    # 1. 获取昨日官方净值涨跌幅
    nav_csv = save_dir / f"{fund_code}_daily_nav.csv"
    if nav_csv.exists():
        try:
            df_nav = pd.read_csv(nav_csv)
            if len(df_nav) >= 2:
                last_nav = df_nav["NAV"].iloc[-1]
                prev_nav = df_nav["NAV"].iloc[-2]
                result["yesterday_official_return"] = (last_nav / prev_nav - 1) * 100
        except Exception as e:
            log.warning(f"[{fund_code}] 讀取昨日淨值失敗: {e}")

    # 2. 获取纳指期货数据
    try:
        nq_hist = _yf_ticker_history("NQ=F", period="5d")
        if nq_hist is not None and len(nq_hist) >= 2:
            nq_last = nq_hist['Close'].iloc[-1]
            nq_prev = nq_hist['Close'].iloc[-2]
            result["nq_futures_return"] = (nq_last / nq_prev - 1) * 100
    except Exception as e:
        log.warning(f"[{fund_code}] 纳指期货获取失败: {e}")

    # 3. 获取最新持仓
    clean_csv = save_dir / f"{fund_code}_holdings_clean.csv"
    if not clean_csv.exists():
        result["status"] = "error"
        result["message"] = "找不到持仓数据，请先在主界面运行一次分析。"
        return result

    try:
        df_holdings = pd.read_csv(clean_csv)
        latest = top_holdings(df_holdings, 10)
    except Exception as e:
        result["status"] = "error"
        result["message"] = f"读取持仓失败: {e}"
        return result

    # 收集所有需要查询的 Ticker（过滤掉非美股代码）
    ticker_map = {}  # 映射关系：原始代码 -> 清洗后的yf代码
    for _, row in latest.iterrows():
        raw_ticker = str(row["StockCode"]).strip()
        # 港股數字代碼、美股 BRK.B 等寫法需轉換後才能交給 yfinance
        yf_ticker = to_yf_ticker(raw_ticker)
        if yf_ticker:
            ticker_map[raw_ticker] = yf_ticker
            
    tickers_list = list(ticker_map.values())
    if not tickers_list:
         result["status"] = "error"
         result["message"] = "持仓中无美股代码。"
         return result

    # 4. 批量获取日线数据（获取昨收价）
    daily_close = {}
    df_daily_all = _yf_download_df(tickers_list, period="5d", group_by="column")
    if df_daily_all is not None:
        try:
            if isinstance(df_daily_all.columns, pd.MultiIndex):
                for t in tickers_list:
                    try:
                        col = df_daily_all[("Close", t)].dropna()
                        if not col.empty:
                            daily_close[t] = col.iloc[-1]
                    except KeyError:
                        log.debug("[%s] 日線缺少 %s 的收盤價", fund_code, t)
            else:
                col = df_daily_all["Close"].dropna()
                if not col.empty:
                    daily_close[tickers_list[0]] = col.iloc[-1]
        except Exception as e:
            log.warning(f"[{fund_code}] 解析日线失败: {e}")

    # 5. 批量获取分钟线数据（包含盘前/盘后）
    latest_price = {}
    df_intra_all = _yf_download_df(tickers_list, period="5d", interval="1m",
                                    prepost=True, group_by="column")
    if df_intra_all is not None:
        try:
            if isinstance(df_intra_all.columns, pd.MultiIndex):
                for t in tickers_list:
                    try:
                        col = df_intra_all[("Close", t)].dropna()
                        if not col.empty:
                            latest_price[t] = col.iloc[-1]
                    except KeyError:
                        log.debug("[%s] 分鐘線缺少 %s 的收盤價", fund_code, t)
            else:
                col = df_intra_all["Close"].dropna()
                if not col.empty:
                    latest_price[tickers_list[0]] = col.iloc[-1]
        except Exception as e:
            log.warning(f"[{fund_code}] 解析分钟线失败: {e}")

    # 6. 组装结果并计算
    total_weight_valid = 0.0
    weighted_return = 0.0
    missing_data = False

    for _, row in latest.iterrows():
        raw_ticker = str(row["StockCode"]).strip()
        name = row["Stock"]
        weight = float(row["Weight"]) / 100.0

        stock_info = {
            "ticker": raw_ticker,
            "name": name,
            "weight": weight * 100,
            "regular_price": None,
            "post_price": None,
            "change_pct": None,
            "status": ""
        }

        if raw_ticker not in ticker_map:
            stock_info["status"] = "非美股代码"
            result["holdings"].append(stock_info)
            missing_data = True
            continue

        t = ticker_map[raw_ticker]
        reg_p = daily_close.get(t)
        latest_p = latest_price.get(t)

        if reg_p is not None:
            stock_info["regular_price"] = reg_p
        if latest_p is not None:
            stock_info["post_price"] = latest_p

        if reg_p and latest_p and reg_p > 0:
            change = (latest_p / reg_p - 1) * 100
            # 如果变化极小，可能是非交易时段盘前价格未动，归零处理
            if abs(change) < 0.001:
                change = 0.0

            stock_info["change_pct"] = change
            weighted_return += change * weight
            total_weight_valid += weight
        else:
            stock_info["status"] = "数据缺失"
            missing_data = True

        result["holdings"].append(stock_info)

    # 7. 计算综合预估（明確區分「平均漲跌幅」與「對淨值的貢獻」）
    total_weight_all = float(
        pd.to_numeric(latest["Weight"], errors="coerce").fillna(0).sum()
    ) / 100.0
    result["total_weight"] = round(total_weight_all, 4)

    if total_weight_valid > 0:
        coverage = float(total_weight_valid)
        # 已覆蓋持倉對基金淨值的加權貢獻（未覆蓋部分按「不變」處理）
        contribution = float(weighted_return)
        result["coverage_ratio"] = round(coverage, 4)
        result["covered_weighted_return"] = round(contribution, 4)
        # 已覆蓋部分的平均漲跌幅（把覆蓋率正規化掉，僅供參考）
        result["estimate_return"] = round(contribution / coverage, 4)

        if missing_data:
            result["message"] = (
                f"⚠️ 僅 {coverage*100:.1f}% 的持倉取得盤後數據；"
                f"預估漲跌為已覆蓋部分平均，對淨值貢獻約 {contribution:+.2f}%"
            )
        else:
            result["message"] = (
                f"覆蓋 {coverage*100:.1f}% 持倉，對淨值貢獻約 {contribution:+.2f}%"
            )
    else:
        result["message"] = "❌ 所有股票均无法获取盘后数据，无法预估"

    return result
