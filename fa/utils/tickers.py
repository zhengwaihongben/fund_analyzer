"""Normalisation of AKShare stock codes into yfinance tickers."""

import re


def to_yf_ticker(stock_code):
    """把 AKShare 的股票代碼轉成 yfinance ticker，無法轉換時返回 None。

    - 港股：純數字 1~5 位（0700 / 09988）→ 四位補零 + .HK
    - 美股：字母開頭，點號寫法（BRK.B）轉連字符（BRK-B）
    - 其餘（A 股等 yfinance 無法可靠對應的代碼）直接跳過
    """
    if stock_code is None or not str(stock_code).strip():
        return None
    s = str(stock_code).strip().upper()
    if s.isdigit():
        if len(s) <= 5:
            return f"{int(s):04d}.HK"
        return None
    if re.fullmatch(r"[A-Z][A-Z0-9\-]*(?:\.[A-Z]{1,2})?", s):
        return s.replace(".", "-")
    return None
