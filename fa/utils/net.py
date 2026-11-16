"""網絡超時統一設置。

akshare / yfinance / requests 大多不暴露 timeout 參數，遇到對端半開連接
（例如 eastmoney、sina 的 TLS 握手卡住）時會無限阻塞，令整個分析停住。
這裡把 ``FA_REQUEST_TIMEOUT``（預設 30 秒）設為 socket 默認超時，讓所有
第三方庫至少在這個時間後拋出異常，由各調用點現有的 except 分支降級處理。
"""

from __future__ import annotations

import socket
import ssl

import requests.exceptions as req_exc

from fa.config import REQUEST_TIMEOUT
from fa.utils.logging_setup import get_logger

log = get_logger(__name__)

# 網絡故障分類（診斷用）。程式不依此改變重試次數，只用來把 log 寫成人看得懂的結論。
NETWORK_TLS = "tls"            # TLS 握手被中斷：SSLError / SSLEOFError / WRONG_VERSION_NUMBER
NETWORK_CONNECTION = "connection"  # 連線被對端或中間設備重置
NETWORK_TIMEOUT = "timeout"
NETWORK_DNS = "dns"
NETWORK_OTHER = "other"

_CATEGORY_TEXT = {
    NETWORK_TLS: "TLS 握手階段被中斷（網絡路徑 / 中間設備 / 對端 CDN 問題，非程式錯誤）",
    NETWORK_CONNECTION: "連線被對端關閉",
    NETWORK_TIMEOUT: "連線超時",
    NETWORK_DNS: "網域名稱解析失敗",
    NETWORK_OTHER: "其他網絡錯誤",
}


def classify_network_error(exc: BaseException) -> str:
    """把常見的網絡異常歸類，供 log 與診斷腳本使用。

    第三方庫常把真正的網絡錯誤包在自家異常裡（例如 akshare 的
    ``APIError(...) from err``），因此沿 ``__cause__``/``__context__`` 鏈找回
    底層原因，避免把 TLS 握手失敗記成「其他網絡錯誤」。
    """
    seen = 0
    current: BaseException | None = exc
    while current is not None and seen < 6:
        category = _classify_one(current)
        if category != NETWORK_OTHER:
            return category
        current = current.__cause__ or current.__context__
        seen += 1
    return _classify_one(exc)


def _classify_one(exc: BaseException) -> str:
    if isinstance(exc, (ssl.SSLError, ssl.SSLEOFError)):
        return NETWORK_TLS
    if isinstance(exc, req_exc.SSLError):
        return NETWORK_TLS
    if isinstance(exc, (req_exc.Timeout, socket.timeout, TimeoutError)):
        return NETWORK_TIMEOUT
    if isinstance(exc, socket.gaierror):
        return NETWORK_DNS
    if isinstance(exc, (req_exc.ConnectionError, req_exc.ProxyError, ConnectionError)):
        return NETWORK_CONNECTION
    text = str(exc)
    if "UNEXPECTED_EOF_WHILE_READING" in text or "WRONG_VERSION_NUMBER" in text:
        return NETWORK_TLS
    return NETWORK_OTHER


def describe_network_error(exc: BaseException) -> str:
    """``<分類中文說明>（<異常類型>）``，適合直接寫進日誌。"""
    category = classify_network_error(exc)
    return f"{_CATEGORY_TEXT[category]}（{type(exc).__name__}）"


def apply_default_timeout(timeout: float | None = None) -> float:
    """把 socket 默認超時設為 ``timeout``（None → 讀 config），返回生效值。

    ``timeout <= 0`` 表示不設超時（沿用系統默認），僅在明確需要長時間下載時使用。
    """
    value = REQUEST_TIMEOUT if timeout is None else float(timeout)
    if value > 0:
        socket.setdefaulttimeout(value)
        log.debug("socket 默認超時設為 %.1f 秒", value)
    else:
        socket.setdefaulttimeout(None)
        log.debug("socket 默認超時已關閉")
    return value
