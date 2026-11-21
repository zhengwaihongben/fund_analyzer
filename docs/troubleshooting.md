# Troubleshooting

Symptom → cause → what to do. The log files referenced here are created next to
the entry points; the README lists them under Configuration.


## SSL error while fetching data

```
SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING]'))
```

This is usually a network problem with the Eastmoney `fundf10` CDN, not the code.
The fund home page is tried first (one request, normally succeeds); then the
archive API with 3 attempts per year and backoff; then the last on-disk snapshot,
so the run still produces charts and — for a QDII fund — an estimate. Since V2.1
the failure is classified before logging:

```
[000369] 2026 年持仓获取失败（已重試 3 次）: TLS 握手階段被中斷（網絡路徑 / 中間設備 / 對端 CDN 問題，非程式錯誤）（APIError）
```

Categories: TLS handshake interrupted, connection closed by peer, timeout, DNS
failure, other. If it says TLS the request never reached Eastmoney — check proxies
and a raw handshake before adding retries; retrying a broken TLS path only adds
~50 s per run. The archive host is the only source of per-quarter history, which
is why the QDII flow no longer depends on it.

An estimate reports `status: "insufficient-data"` and the CLI prints
`未產生估算：目標日 … 之前尚無已披露的 QDII 持倉` when every portfolio source
failed, or when the only snapshots were disclosed **after** the target date
(look-ahead protection).

## The program seems to hang

Some hosts accept the connection and stall in the TLS handshake.
`FA_REQUEST_TIMEOUT` (default 30 s) bounds this:

```cmd
set FA_REQUEST_TIMEOUT=15
python fund_analyzer.py 017436 -v
```

## `ModuleNotFoundError: No module named 'fund_analyzer'`

Run from the folder that contains `fund_analyzer.py`, or set the shortcut's
**Start in** field to the project folder.

## GUI closes immediately

Run `python fund_analyzer_gui.py` in a terminal to see the error. Under
`pythonw.exe` the traceback goes to `crash.log` and `gui_debug.log` instead.

If neither file has a traceback, the process died inside a native library
(Windows event log shows e.g. `0xC06D007F` in `KERNELBASE.dll`); check
`fatal.log`, written by `faulthandler`.

## The combined report does not open

`pywebview` is optional; without it the report opens in the default browser. If
neither works, open `<code>_master_report.html` manually — it embeds the
individual reports as iframes.
