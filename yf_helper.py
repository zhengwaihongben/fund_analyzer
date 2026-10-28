#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""yfinance 隔離助手 —— 在獨立子進程中跑，避免 curl_cffi segfault 拖死主進程"""
import sys
import json
import io
import contextlib
import warnings

warnings.filterwarnings("ignore")

_real_stdout = sys.stdout
sys.stdout = sys.stderr  # yfinance 的 print 全走 stderr，stdout 只留 JSON


def _serialize_df(df):
    import numpy as np

    def clean(v):
        if v is None:
            return None
        try:
            f = float(v)
            if np.isnan(f) or np.isinf(f):
                return None
            return f
        except Exception:
            return None

    if hasattr(df.columns, "levels"):   # MultiIndex
        cols = [list(map(str, c)) for c in df.columns]
    else:
        cols = [str(c) for c in df.columns]

    return {
        "empty": bool(df.empty),
        "index": [str(i) for i in df.index],
        "columns": cols,
        "data": [[clean(x) for x in row] for row in df.astype(object).values.tolist()],
    }


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception as e:
        _real_stdout.write(json.dumps({"error": f"bad input: {e}"}))
        return

    try:
        import yfinance as yf
    except Exception as e:
        _real_stdout.write(json.dumps({"error": f"import yfinance failed: {e}"}))
        return

    action = payload.get("action", "download")
    try:
        if action == "download":
            tickers = payload.get("tickers") or []
            if isinstance(tickers, str):
                tickers = [tickers]
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                df = yf.download(
                    tickers,
                    period=payload.get("period", "1y"),
                    interval=payload.get("interval", "1d"),
                    progress=False,
                    group_by=payload.get("group_by", "column"),
                    prepost=payload.get("prepost", False),
                    threads=False,          # 關鍵：關閉 yfinance 內部多線程
                    auto_adjust=False,
                )
            _real_stdout.write(json.dumps(_serialize_df(df)))

        elif action == "history":
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                df = yf.Ticker(payload["ticker"]).history(period=payload.get("period", "5d"))
            _real_stdout.write(json.dumps(_serialize_df(df)))

        else:
            _real_stdout.write(json.dumps({"error": f"unknown action: {action}"}))
    except Exception as e:
        import traceback
        _real_stdout.write(json.dumps({"error": str(e), "trace": traceback.format_exc()}))


if __name__ == "__main__":
    main()