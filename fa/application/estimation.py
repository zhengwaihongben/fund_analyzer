# -*- coding: utf-8 -*-
"""QDII 估值應用服務：Live、Stored、History 三條路徑。

設計要點
--------
* Repository 只在此模組內建立與關閉（``finally``），GUI 不持有它。
* Live 模式一定要把 ``repository=`` 傳進 :func:`estimate_qdii_nav`，並且
  用 ``load_estimation(run_id)`` **重新讀回** 來確認真的寫入。估算函數
  內部的 ``_save_to_database()`` 會吞掉資料庫錯誤且丟棄回傳值，單看函數
  正常返回不能推論已持久化。
* Stored 模式只讀取既有紀錄，**絕不**呼叫估算引擎、也不寫入任何檔案。
* 資產明細依規格順序補齊：SQLite 摘要 → ``estimation_inputs`` →
  ``{fund}_qdii_estimate_inputs.json`` 的 ``snapshots[run_id]`` →
  ``{fund}_qdii_estimate.json``（僅當 ``run_id`` 相同）→ ``—``。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fa.analysis.nav_estimator import estimate_qdii_nav
from fa.config import PROJECT_ROOT
from fa.data.database.repository import Repository
from fa.data.estimation_storage import ESTIMATE_SUFFIX, INPUTS_SUFFIX

DEFAULT_DATABASE = PROJECT_ROOT / "data" / "database" / "fund_analyzer.db"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"
MAX_HISTORY_LIMIT = 200

SOURCE_SQLITE_INPUTS = "SQLite 輸入快照"
SOURCE_RUN_SNAPSHOT = "+ 執行快照 JSON"
SOURCE_LATEST_JSON = "+ 最新 JSON（run_id 相同）"
SOURCE_LIVE_RECORD = "估值結果（未寫入資料庫）"

NO_ASSET_SOURCE_TEXT = "此執行未保存可用資產明細（僅存摘要）"


@dataclass(frozen=True)
class EstimationView:
    """GUI 需要的完整檢視資料；不含任何計算。"""

    record: dict[str, Any]
    mode: str
    run_id: str | None = None
    persisted: bool = False
    asset_rows: list[dict[str, Any]] = field(default_factory=list)
    asset_source: str = ""
    warnings: list[str] = field(default_factory=list)
    message: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)

    @property
    def has_assets(self) -> bool:
        return bool(self.asset_rows)

    def as_dict(self) -> dict[str, Any]:
        return {
            "record": self.record,
            "mode": self.mode,
            "run_id": self.run_id,
            "persisted": self.persisted,
            "asset_rows": [dict(row) for row in self.asset_rows],
            "asset_source": self.asset_source,
            "warnings": list(self.warnings),
            "message": self.message,
            "inputs": dict(self.inputs),
            "has_assets": self.has_assets,
        }


def run_estimate(
    fund_code: str,
    save_dir: Path | str,
    as_of: datetime | str | None = None,
    *,
    db_path: Path | str | None = None,
    estimator: Callable[..., dict[str, Any]] | None = None,
) -> EstimationView:
    """執行一次實際估算並確認是否真的寫入 SQLite。"""
    estimator = estimator or estimate_qdii_nav
    save_dir = Path(save_dir)
    repository = Repository(Path(db_path) if db_path else DEFAULT_DATABASE)
    try:
        record = estimator(fund_code, save_dir, as_of=as_of, repository=repository)
        if not isinstance(record, dict):
            raise TypeError("估算函數必須回傳 dict")
        run_id = record.get("run_id")
        persisted = bool(run_id) and repository.load_estimation(str(run_id)) is not None
        warnings = [str(item) for item in record.get("warnings") or []]
        inputs: dict[str, Any] = {}
        if persisted:
            inputs = repository.estimation_inputs(str(run_id))
            rows, source = _resolve_asset_rows(
                record=record,
                inputs=inputs,
                fund_code=fund_code,
                save_dir=save_dir,
                run_id=str(run_id),
            )
            message = ""
        else:
            rows = [_from_holding(item, None) for item in record.get("holdings") or []]
            source = SOURCE_LIVE_RECORD if rows else ""
            message = "未寫入資料庫"
            warnings.append("估值結果未寫入資料庫，本次不會列入歷史清單")
        return EstimationView(
            record=dict(record),
            mode="live",
            run_id=str(run_id) if run_id else None,
            persisted=persisted,
            asset_rows=rows,
            asset_source=source,
            warnings=warnings,
            message=message,
            inputs=dict(inputs) if isinstance(inputs, dict) else {},
        )
    finally:
        repository.close()


def load_estimate(
    run_id: str,
    *,
    db_path: Path | str | None = None,
    save_dir: Path | str | None = None,
) -> EstimationView:
    """讀取已存在的估值紀錄；不執行估算、不寫入任何東西。"""
    run_id = str(run_id).strip()
    if not run_id:
        raise ValueError("run_id 不可為空")
    repository = Repository(Path(db_path) if db_path else DEFAULT_DATABASE)
    try:
        summary = repository.load_estimation(run_id)
        if summary is None:
            raise LookupError(f"找不到估值紀錄：{run_id}")
        fund_code = str(summary.get("fund_code") or "")
        inputs = repository.estimation_inputs(run_id)
        base_dir = Path(save_dir) if save_dir else DEFAULT_OUTPUT_DIR / fund_code
        rows, source = _resolve_asset_rows(
            record=summary,
            inputs=inputs,
            fund_code=fund_code,
            save_dir=base_dir,
            run_id=run_id,
        )
        warnings = [str(item) for item in summary.get("warnings") or []]
        message = "" if rows else NO_ASSET_SOURCE_TEXT
        return EstimationView(
            record=summary,
            mode="stored",
            run_id=run_id,
            persisted=True,
            asset_rows=rows,
            asset_source=source,
            warnings=warnings,
            message=message,
            inputs=dict(inputs) if isinstance(inputs, dict) else {},
        )
    finally:
        repository.close()


def list_estimates(
    fund_code: str | None = None,
    limit: int = 50,
    *,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """歷史估值清單；排序與篩選完全交給 Repository。"""
    repository = Repository(Path(db_path) if db_path else DEFAULT_DATABASE)
    try:
        try:
            size = int(limit)
        except (TypeError, ValueError):
            size = 50
        size = max(1, min(size, MAX_HISTORY_LIMIT))
        return repository.list_estimations(fund_code=fund_code, limit=size)
    finally:
        repository.close()


# --------------------------------------------------------------------------- #
# 資產明細來源解析
# --------------------------------------------------------------------------- #


def _resolve_asset_rows(
    *,
    record: dict[str, Any],
    inputs: dict[str, Any],
    fund_code: str,
    save_dir: Path,
    run_id: str,
) -> tuple[list[dict[str, Any]], str]:
    """依規格順序合併資產明細，回傳 ``(rows, source_label)``。"""
    labels: list[str] = []
    base_assets = inputs.get("assets") if isinstance(inputs, dict) else None
    rows: list[dict[str, Any]] = []
    if isinstance(base_assets, list) and base_assets:
        rows = [_from_inputs(item) for item in base_assets]
        labels.append(SOURCE_SQLITE_INPUTS)

    if _needs_merge(rows):
        snapshot = _snapshot_record(save_dir, fund_code, run_id)
        if snapshot is not None:
            holdings = _valid_holdings(snapshot)
            if not rows:
                built = [_from_holding(item, None) for item in holdings]
                if built:
                    rows = built
                    labels.append(SOURCE_RUN_SNAPSHOT)
            else:
                provided = _merge_contributions(rows, holdings)
                if provided:
                    labels.append(SOURCE_RUN_SNAPSHOT)

    if _needs_merge(rows):
        latest = _read_json(Path(save_dir) / f"{fund_code}{ESTIMATE_SUFFIX}")
        if isinstance(latest, dict) and str(latest.get("run_id") or "") == run_id:
            holdings = _valid_holdings(latest)
            if not rows:
                built = [_from_holding(item, None) for item in holdings]
                if built:
                    rows = built
                    labels.append(SOURCE_LATEST_JSON)
            else:
                provided = _merge_contributions(rows, holdings)
                if provided:
                    labels.append(SOURCE_LATEST_JSON)

    if not rows:
        # 最後手段：紀錄本身若帶 holdings（Live 未持久化路徑不經過這裡）
        holdings = _valid_holdings(record)
        if holdings:
            rows = [_from_holding(item, None) for item in holdings]

    return rows, " ".join(labels)


def _valid_holdings(payload: Any) -> list[dict[str, Any]]:
    """只回傳 payload 裡真正是 dict 的 holdings 元素。

    JSON 是外部檔案：``holdings`` 可能是字串（可迭代，會逐字元產生空白列）、
    是 dict，或含有 ``None``/數字。非 dict 元素一律視為沒有資料，不能讓它們
    變成資產列，也不能因為它們讓呼叫端宣稱使用了該來源。
    """
    if not isinstance(payload, dict):
        return []
    holdings = payload.get("holdings")
    if not isinstance(holdings, list):
        return []
    return [item for item in holdings if isinstance(item, dict)]


def _snapshot_record(save_dir: Path, fund_code: str, run_id: str) -> dict[str, Any] | None:
    payload = _read_json(Path(save_dir) / f"{fund_code}{INPUTS_SUFFIX}")
    if not isinstance(payload, dict):
        return None
    snapshots = payload.get("snapshots")
    if not isinstance(snapshots, dict):
        return None
    candidate = snapshots.get(run_id)
    return candidate if isinstance(candidate, dict) else None


def _needs_merge(rows: list[dict[str, Any]]) -> bool:
    """True when some row still lacks a contribution or a usable status.

    The two fields are independent, so a row with a contribution but
    ``status="ok"`` still wants the snapshot's finer status.  Reading the
    snapshot only for missing contributions would silently skip that.
    """
    if not rows:
        return True
    for row in rows:
        if row.get("contribution") is None:
            return True
        if row.get("status") in (None, "", "ok"):
            return True
    return False


def _merge_contributions(rows: list[dict[str, Any]], holdings: list[Any]) -> int:
    """把 holdings 的 contribution/status 補進缺值的列，回傳實際補了幾列。

    contribution 與 status 各自獨立判斷：SQLite 已有 contribution 不代表
    status 已完整；反之亦然。只有真正寫入過欄位的列才會計入回傳值，
    呼叫端據此決定要不要宣稱使用了這個來源。
    """
    by_ticker: dict[str, Any] = {}
    for item in holdings:
        if isinstance(item, dict):
            ticker = str(item.get("ticker") or "").strip().upper()
            if ticker:
                by_ticker[ticker] = item
    provided_rows = 0
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        source = by_ticker.get(ticker)
        if source is None:
            continue
        provided = False
        if row.get("contribution") is None:
            value = _as_float(source.get("contribution"))
            if value is not None:
                row["contribution"] = value
                provided = True
        if row.get("status") in (None, "", "ok"):
            status = source.get("status")
            if isinstance(status, str) and status.strip() and status.strip() != row.get("status"):
                row["status"] = status.strip()
                provided = True
        if provided:
            provided_rows += 1
    return provided_rows


_FIELD_MAP = (
    ("ticker", "ticker"),
    ("name", "name"),
    ("asset_type", "asset_type"),
    ("market", "market"),
    ("currency", "currency"),
    ("weight", "weight"),
    ("base_price_date", "base_price_date"),
    ("target_price_date", "target_price_date"),
    ("local_return", "local_return"),
    ("fx_return", "fx_return"),
    ("cny_return", "return_cny"),
    ("contribution", "contribution"),
    ("status", "status"),
)


def _from_inputs(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {key: None for key, _ in _FIELD_MAP}
    return _normalise(item, item.get("contribution"))


def _from_holding(item: Any, contribution: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {key: None for key, _ in _FIELD_MAP}
    return _normalise(item, contribution if contribution is not None else item.get("contribution"))


def _normalise(source: dict[str, Any], contribution: Any) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, alias in _FIELD_MAP:
        if key == "contribution":
            row[key] = _as_float(contribution)
        elif key in ("weight", "local_return", "fx_return", "cny_return"):
            # 舊版 SQLite 快照把 CNY 回報存成 ``return_cny``，canonical 名稱是
            # ``cny_return``；兩者都要讀，但 canonical 有值時優先。
            row[key] = _as_float(_first_value(source, key, alias))
        else:
            value = source.get(key)
            if value is None and alias != key:
                value = source.get(alias)
            row[key] = value
    return row


def _first_value(source: dict[str, Any], key: str, alias: str) -> Any:
    if key in source and source[key] is not None:
        return source[key]
    if alias != key:
        return source.get(alias)
    return source.get(key)


def _as_float(value: Any) -> float | None:
    """Convert to a finite float, else ``None``.

    ``None``/``bool``/non-numeric text are not numbers, and NaN/±inf are not
    financial values: a GUI that showed ``inf`` for a weight would be lying
    about the data, so they all collapse to ``None`` (never to 0).  An integer
    too large for ``float`` raises ``OverflowError`` inside the conversion
    itself (before ``isfinite`` can help), so that path also returns ``None``.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _read_json(path: Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None
