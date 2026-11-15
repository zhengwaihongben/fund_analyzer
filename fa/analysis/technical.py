"""Technical indicators computed from the fund NAV series."""

from pathlib import Path

import numpy as np
import pandas as pd

from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

def compute_technical(df: pd.DataFrame, cfg: dict,
                      save_dir: Path, fund_code: str) -> pd.DataFrame:
    log.info(f"[{fund_code}] 计算 SMA / BOLL / RSI / MACD ...")
    # 【修正】窗口未滿時不輸出指標值：min_periods=1 會讓均線在第 2 根 K 線就
    # 出現，那些點其實只是 1~2 天的平均，容易被誤讀成有效信號。
    for w in cfg["sma_windows"]:
        df[f"SMA{w}"] = df["NAV"].rolling(window=w, min_periods=w).mean()

    n, k = cfg["boll_n"], cfg["boll_k"]
    df["BOLL_Mid"]   = df["NAV"].rolling(window=n, min_periods=n).mean()
    df["BOLL_Std"]   = df["NAV"].rolling(window=n, min_periods=n).std()
    df["BOLL_Upper"] = df["BOLL_Mid"] + k * df["BOLL_Std"]
    df["BOLL_Lower"] = df["BOLL_Mid"] - k * df["BOLL_Std"]

    # 计算 RSI (默认周期 14)
    delta = df["NAV"].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))
    df["RSI"] = df["RSI"].fillna(50)

    # 新增：计算 MACD (12, 26, 9)
    exp12 = df["NAV"].ewm(span=12, adjust=False).mean()
    exp26 = df["NAV"].ewm(span=26, adjust=False).mean()
    df["MACD_DIF"] = exp12 - exp26
    df["MACD_DEA"] = df["MACD_DIF"].ewm(span=9, adjust=False).mean()
    df["MACD_Hist"] = (df["MACD_DIF"] - df["MACD_DEA"]) * 2  # A股习惯乘2，更直观

    # ================= KDJ (9,3,3)：NAV-based 代理 =================
    # 基金只有每日淨值、沒有最高/最低價，這裡把 NAV 同時當成 H/L/C，
    # 算出來的 K/D/J 只是「淨值在近 9 日區間中的相對位置」的平滑，
    # 不等同股票 KDJ；欄位名不變，圖例與軸標題已標註 NAV proxy。
    low_min = df["NAV"].rolling(window=9, min_periods=9).min()
    high_max = df["NAV"].rolling(window=9, min_periods=9).max()
    rsv = (df["NAV"] - low_min) / (high_max - low_min + 1e-12) * 100
    df["KDJ_K"] = rsv.ewm(com=2, adjust=False).mean()
    df["KDJ_D"] = df["KDJ_K"].ewm(com=2, adjust=False).mean()
    df["KDJ_J"] = 3 * df["KDJ_K"] - 2 * df["KDJ_D"]

    # ================= NAV 波動度 / NAV 方向強度：NAV-based 代理 =================
    # 【修正】原本的 ATR / +DI / -DI / ADX 是拿淨值硬套「有高低價」的公式
    # （TR = |ΔNAV|、以 NAV 當 H/L/C），名稱會讓人誤以為是標準 ATR/ADX。
    # 標準公式需要每日 high/low/close，基金數據無法提供，故改名為 proxy：
    #   NAV_VOL      : |ΔNAV| 的 EWMA(14)，尺度類似 ATR 但不是 ATR
    #   NAV_DI_Plus/Minus : 把 ΔNAV 當 +DM / -DM 代入 Wilder 平滑的近似方向指標
    #   NAV_DX       : 上述近似 DI 的 DX 平滑值，類似 ADX 的趨勢強度 proxy
    nav_step = df["NAV"].diff()
    nav_vol = nav_step.abs().ewm(alpha=1/14, adjust=False).mean()
    df["NAV_VOL"] = nav_vol

    up_move = nav_step
    down_move = -nav_step
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index
    )
    plus_di = 100 * (plus_dm.ewm(alpha=1/14, adjust=False).mean() / (nav_vol + 1e-12))
    minus_di = 100 * (minus_dm.ewm(alpha=1/14, adjust=False).mean() / (nav_vol + 1e-12))

    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-12)
    df["NAV_DI_Plus"] = plus_di
    df["NAV_DI_Minus"] = minus_di
    df["NAV_DX"] = dx.ewm(alpha=1/14, adjust=False).mean()

    out = save_dir / f"{fund_code}_daily_nav_sma_boll.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")

    log.info(f"[{fund_code}] 技术指标 CSV → {out.name}")
    return df
