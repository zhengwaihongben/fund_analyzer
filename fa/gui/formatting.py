"""Pure formatting helpers shared by every GUI area.

The functions here are deliberately dependency-free: no Tkinter, no database,
no network.  A widget may colour a value, but the text itself must always say
what it is (``+0.19%``, ``—``), so colour never becomes the only signal.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

__all__ = [
    "MISSING",
    "color_for",
    "date_text",
    "money",
    "number",
    "pct",
    "quality_text",
    "status_text",
]

MISSING = "—"

_QUALITY = {
    "ok": "正常",
    "low": "偏低",
    "medium": "中等",
    "high": "高",
    "partial": "部分覆蓋",
    "insufficient": "資料不足",
    "failed": "失敗",
    "unknown": "未知",
}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    return False


def pct(value: Any, *, digits: int = 2, signed: bool = True) -> str:
    """Format a fraction as a percentage: ``0.0019`` → ``+0.19%``."""
    if _is_missing(value):
        return MISSING
    try:
        number_value = float(value)
    except (TypeError, ValueError):
        return MISSING
    sign = "+" if signed and number_value > 0 else ""
    return f"{sign}{number_value * 100:.{digits}f}%"


def money(value: Any, *, digits: int = 2) -> str:
    """Format an amount with thousands separators: ``1234.5`` → ``1,234.50``."""
    if _is_missing(value):
        return MISSING
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return MISSING


def number(value: Any, *, digits: int = 2) -> str:
    """Format a plain number; ``None`` is missing, never zero."""
    if _is_missing(value):
        return MISSING
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return MISSING


def date_text(value: Any) -> str:
    """Format an ISO date/datetime as ``YYYY-MM-DD``."""
    if value is None:
        return MISSING
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return MISSING
    return text[:10]


def status_text(value: Any) -> str:
    """Map a result/estimate status to Chinese display text."""
    if value is None:
        return MISSING
    key = str(value).strip().lower()
    if not key:
        return MISSING
    return _QUALITY.get(key, str(value))


def quality_text(value: Any) -> str:
    """Map a data-quality level to Chinese display text."""
    if value is None:
        return MISSING
    key = str(value).strip().lower()
    if not key:
        return MISSING
    return _QUALITY.get(key, str(value))


def color_for(value: Any) -> str:
    """Colour hint for a signed number: red up, green down, grey missing.

    Chinese market convention: red = positive, green = negative.  Callers must
    still print the sign, because colour alone is not an accessible signal.
    """
    if _is_missing(value):
        return "#777777"
    try:
        number_value = float(value)
    except (TypeError, ValueError):
        return "#777777"
    if number_value > 0:
        return "#c62828"
    if number_value < 0:
        return "#2e7d32"
    return "#333333"

_SCOPE = {
    "top10": "前十大（揭露口徑）",
    "full_portfolio": "完整組合",
}

_LOOK_AHEAD = {
    "verified-safe": "已驗證安全（無前視偏差）",
    "estimated-disclosure": "披露日為推算值",
    "unsafe": "未通過前視檢查",
    "unknown": "前視狀態不明",
}


def scope_text(value: Any) -> str:
    """Holdings disclosure scope, or ``—`` when the source did not say."""
    if _is_missing(value):
        return MISSING
    return _SCOPE.get(str(value), str(value))


def look_ahead_text(value: Any, *, safe: Any = None) -> str:
    """Four-state look-ahead text, with the legacy boolean as a fallback.

    Stored estimation rows only carry ``look_ahead_safe``, which SQLite returns
    as the integer ``0``/``1`` (not a Python ``bool``), so the fallback tests
    truthiness instead of identity.  The boolean is shown as
    已驗證安全／未驗證安全 and never invented into one of the four states.
    """
    text = str(value) if not _is_missing(value) else ""
    if text and text != "unknown":
        return _LOOK_AHEAD.get(text, text)
    if safe is not None:
        return "已驗證安全（布林值）" if bool(safe) else "未驗證安全（布林值）"
    if text == "unknown":
        return _LOOK_AHEAD["unknown"]
    return MISSING
