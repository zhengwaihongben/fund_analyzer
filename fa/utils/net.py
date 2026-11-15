"""網絡超時統一設置。

akshare / yfinance / requests 大多不暴露 timeout 參數，遇到對端半開連接
（例如 eastmoney、sina 的 TLS 握手卡住）時會無限阻塞，令整個分析停住。
這裡把 ``FA_REQUEST_TIMEOUT``（預設 30 秒）設為 socket 默認超時，讓所有
第三方庫至少在這個時間後拋出異常，由各調用點現有的 except 分支降級處理。
"""

from __future__ import annotations

import socket

from fa.config import REQUEST_TIMEOUT
from fa.utils.logging_setup import get_logger

log = get_logger(__name__)


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
