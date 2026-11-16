# -*- coding: utf-8 -*-
"""Fund classification (QDII vs. standard) with manual override.

The GUI does not ask the user whether a fund is a QDII; the type is read from
the fund's own profile.  Three sources are tried, from most to least formal:

1. the mobile "basic information" JSON (``FTYPE``),
2. the ``jbgk`` profile page (``基金类型`` row),
3. the ``pingzhongdata`` blob, which only exposes the fund name -- used as a
   *fallback signal* (``source="name"``) and clearly marked as such.

If nothing can be determined the result is ``unknown`` and the caller must
fall back to the standard analysis rather than guessing.
"""
from __future__ import annotations

import re

import requests

from fa.config import REQUEST_TIMEOUT
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

MOBILE_URL = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNNBasicInformation"
JBGK_URL = "https://fundf10.eastmoney.com/jbgk_{code}.html"
PINGZHONG_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Referer": "https://fund.eastmoney.com/",
}

_NAME_RE = re.compile(r'var\s+fS_name\s*=\s*"([^"]*)"')
_JBGK_TYPE_RE = re.compile(r"基金类型</th>\s*<td[^>]*>(.*?)</td>", re.DOTALL)
_JBGK_NAME_RE = re.compile(r"基金全称</th>\s*<td[^>]*>(.*?)</td>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

MODES = ("auto", "standard", "qdii")


def _clean_html(text: str) -> str:
    return _TAG_RE.sub("", text or "").replace("&nbsp;", " ").strip()


def _get(url: str, timeout: float | None = None):
    """GET with the shared timeout; returns the response or ``None``."""
    try:
        return requests.get(url, headers=_HEADERS,
                            timeout=timeout or REQUEST_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - 分類失敗不應中斷分析
        log.debug("基金資料請求失敗 %s: %s", url, exc)
        return None


def _from_mobile_api(fund_code: str) -> dict | None:
    resp = _get(f"{MOBILE_URL}?FCODE={fund_code}&deviceid=1"
                "&plat=Android&product=EFund&version=6.2.8")
    if resp is None or resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    data = payload.get("Datas") or payload.get("data") or {}
    if not isinstance(data, dict):
        return None

    fund_type = None
    for key, value in data.items():
        if "TYPE" in str(key).upper() and isinstance(value, str) and value.strip():
            fund_type = value.strip()
            break
    name = None
    for key in ("SHORTNAME", "FNAME", "FundName"):
        if data.get(key):
            name = str(data[key]).strip()
            break
    if not fund_type and not name:
        return None
    return {
        "name": name,
        "fund_type": fund_type,
        "is_qdii": _is_qdii_label(fund_type),
        "source": "eastmoney-mobile",
        "confidence": "high" if fund_type else "low",
    }


def _from_jbgk(fund_code: str) -> dict | None:
    resp = _get(JBGK_URL.format(code=fund_code), timeout=REQUEST_TIMEOUT)
    if resp is None or resp.status_code != 200 or not resp.text:
        return None
    match = _JBGK_TYPE_RE.search(resp.text)
    name_match = _JBGK_NAME_RE.search(resp.text)
    fund_type = _clean_html(match.group(1)) if match else None
    name = _clean_html(name_match.group(1)) if name_match else None
    if not fund_type and not name:
        return None
    return {
        "name": name,
        "fund_type": fund_type,
        "is_qdii": _is_qdii_label(fund_type),
        "source": "eastmoney-jbgk",
        "confidence": "high" if fund_type else "low",
    }


def _from_pingzhong(fund_code: str) -> dict | None:
    resp = _get(PINGZHONG_URL.format(code=fund_code))
    if resp is None or resp.status_code != 200:
        return None
    match = _NAME_RE.search(resp.text)
    if not match:
        return None
    name = match.group(1).strip()
    by_name = _is_qdii_label(name)
    return {
        "name": name,
        "fund_type": None,
        "is_qdii": by_name,
        "source": "name",
        "confidence": "low",
    }


def _is_qdii_label(label: str | None) -> bool | None:
    """``True``/``False`` from a type-or-name label; ``None`` when silent."""
    if not label:
        return None
    text = str(label).upper()
    if "QDII" in text:
        return True
    if "海外" in text or "跨境" in text or "全球" in text:
        return True
    return False


def fetch_fund_profile(fund_code: str) -> dict:
    """Fund name + type, from the most formal source that answers."""
    for fetcher in (_from_mobile_api, _from_jbgk, _from_pingzhong):
        profile = fetcher(fund_code)
        if profile:
            log.info(f"[{fund_code}] 基金類型: "
                     f"{profile.get('fund_type') or '未知'}（來源 {profile['source']}）")
            return profile
    log.warning(f"[{fund_code}] 無法識別基金類型")
    return {
        "name": None,
        "fund_type": None,
        "is_qdii": None,
        "source": "unknown",
        "confidence": "none",
    }


def resolve_fund_mode(fund_code: str, mode: str = "auto") -> tuple[str, dict]:
    """Return ``(mode, profile)`` where mode is ``"standard"`` or ``"qdii"``.

    ``mode="qdii"`` / ``"standard"`` force the behaviour; ``"auto"`` follows
    the detected fund type and falls back to ``standard`` when the type is
    unknown (never guessing from keywords alone unless that is all we have,
    in which case the profile's ``confidence`` says ``low``).
    """
    requested = str(mode or "auto").strip().lower()
    if requested not in MODES:
        raise ValueError(f"未知的基金模式: {mode}")

    profile = fetch_fund_profile(fund_code)
    if requested == "qdii":
        return "qdii", {**profile, "mode_source": "manual"}
    if requested == "standard":
        return "standard", {**profile, "mode_source": "manual"}

    if profile.get("is_qdii"):
        return "qdii", {**profile, "mode_source": "auto"}
    if profile.get("source") == "unknown":
        log.warning(f"[{fund_code}] 基金類型未知，改用一般分析流程")
        return "standard", {**profile, "mode_source": "fallback"}
    return "standard", {**profile, "mode_source": "auto"}
