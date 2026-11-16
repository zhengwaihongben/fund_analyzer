# -*- coding: utf-8 -*-
"""Output-directory housekeeping and the tiny CSV cache layer.

Every cached artefact is a plain CSV sitting next to the reports (no hidden
state, no separate directory), so a user can inspect, copy or delete it by
hand. Freshness is decided from the file mtime and
:data:`fa.config.CACHE_TTL_DAYS`.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from fa.config import CACHE_TTL_DAYS
from fa.errors import CacheError
from fa.utils.logging_setup import get_logger

log = get_logger(__name__)

# 原始數據與下游模組依賴的中間數據：任何時候都不會被清理
DATA_FILE_SUFFIXES = (
    "_daily_nav.csv",
    "_holdings_raw.csv",
    "_holdings_clean.csv",
    "_qdii_holdings.csv",
    "_qdii_estimates.csv",
    "_qdii_estimate_inputs.json",
)


def cache_age_days(path: Path) -> float:
    """Age of ``path`` in days (negative values are clamped to 0)."""
    try:
        age = (time.time() - path.stat().st_mtime) / 86400.0
    except OSError:
        return float("inf")
    return max(age, 0.0)


def is_cache_fresh(path: Path, max_age_days: float | None = None) -> bool:
    """True when ``path`` exists and is younger than the cache policy."""
    if not path.exists():
        return False
    limit = CACHE_TTL_DAYS if max_age_days is None else max_age_days
    if limit is None or limit <= 0:
        return True
    return cache_age_days(path) <= limit


def read_cache_df(path: Path) -> pd.DataFrame:
    """Read a cached CSV as strings, raising :class:`CacheError` on corruption."""
    try:
        return pd.read_csv(path, dtype=str)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise CacheError(f"緩存文件無法讀取 {path.name}: {exc}") from exc


def write_cache_df(df: pd.DataFrame, path: Path) -> Path:
    """Persist ``df`` as a UTF-8-BOM CSV (Excel friendly) and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _prune_stale_outputs(save_dir: Path, fund_code: str, keep: set) -> None:
    """清理上一次運行殘留、本次未重新生成的派生報告。

    只在分析成功結束後調用；原始數據文件（DATA_FILE_SUFFIXES）永不刪除，
    因此分析失敗時不會破壞上一次的成功結果。
    """
    for p in save_dir.glob(f"{fund_code}_*"):
        if not p.is_file() or p.name in keep:
            continue
        if p.name.endswith(DATA_FILE_SUFFIXES):
            continue
        if p.suffix.lower() not in (".html", ".csv", ".json"):
            continue
        try:
            p.unlink()
            log.debug(f"[{fund_code}] 清理殘留文件: {p.name}")
        except OSError as e:
            log.debug(f"[{fund_code}] 清理殘留文件失敗 {p.name}: {e}")
