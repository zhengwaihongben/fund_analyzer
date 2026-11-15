"""Isolated yfinance access (subprocess helper, avoids curl_cffi crashes)."""

from pathlib import Path
import json
import subprocess
import sys

import pandas as pd

from fa.config import PROJECT_ROOT
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

_YF_HELPER = PROJECT_ROOT / "yf_helper.py"


def _yf_run(payload: dict, timeout: int = 90) -> dict:
    """在子進程中調用 yfinance，返回 dict。崩潰不影響主進程。"""
    if not _YF_HELPER.exists():
        return {"error": "yf_helper.py not found"}
    
    # 【修復】如果是 pythonw.exe，替換為 python.exe 啟動子進程，防止管道死鎖
    exe = sys.executable
    if exe.endswith("pythonw.exe"):
        exe_py = exe.replace("pythonw.exe", "python.exe")
        if Path(exe_py).exists():
            exe = exe_py

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
        
    try:
        proc = subprocess.run(
            [exe, str(_YF_HELPER)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": f"subprocess failed: {e}"}

    if proc.returncode != 0:
        return {"error": f"subprocess exit {proc.returncode}: {(proc.stderr or '')[-500:]}"}

    out = (proc.stdout or "").strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return {"error": f"invalid JSON from helper: {out[-500:]}"}


def _yf_download_df(tickers, period="1y", interval="1d",
                    prepost=False, group_by="column"):
    """yf.download 的隔離版本。返回 DataFrame 或 None。"""
    res = _yf_run({
        "action": "download",
        "tickers": tickers if isinstance(tickers, list) else [tickers],
        "period": period,
        "interval": interval,
        "prepost": prepost,
        "group_by": group_by,
    })
    if "error" in res or res.get("empty", True):
        log.warning(f"yf_download 失敗: {res.get('error', 'empty')}")
        return None

    index = pd.to_datetime(res["index"])
    cols = res["columns"]
    if cols and isinstance(cols[0], list):
        columns = pd.MultiIndex.from_tuples([tuple(c) for c in cols])
    else:
        columns = cols
    df = pd.DataFrame(res["data"], index=index, columns=columns)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def _yf_ticker_history(ticker, period="5d"):
    """yf.Ticker(t).history() 的隔離版本。返回 DataFrame 或 None。"""
    res = _yf_run({"action": "history", "ticker": ticker, "period": period})
    if "error" in res or res.get("empty", True):
        log.warning(f"yf_history({ticker}) 失敗: {res.get('error', 'empty')}")
        return None
    df = pd.DataFrame(
        res["data"],
        index=pd.to_datetime(res["index"]),
        columns=res["columns"],
    )
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df
