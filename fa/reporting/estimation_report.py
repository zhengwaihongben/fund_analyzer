# -*- coding: utf-8 -*-
"""Estimation report renderer (V2.4 Stage D, D-03/D-05).

Pure function over an ``EstimationView`` produced by
``fa.application.estimation``: it never opens the database, never reads the
per-run JSON files itself and never recomputes a return or a contribution.
The view already carries the merged asset rows and the inputs snapshot, so
whatever the stored record does not contain is shown as ``—``.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

MISSING = "—"
NO_ASSET_SOURCE_TEXT = "此執行未保存可用資產明細（僅存摘要）"

_SCOPE = {"top10": "前十大（揭露口徑）", "full_portfolio": "完整組合"}


def _pct(value: Any) -> str:
    number = _float(value)
    return MISSING if number is None else f"{number * 100:.2f}%"


def _pct_signed(value: Any) -> str:
    number = _float(value)
    return MISSING if number is None else f"{number * 100:+.2f}%"


def _text(value: Any) -> str:
    if value is None or value == "":
        return MISSING
    return str(value)


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _date(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if isinstance(value, str) and value.strip():
        return value.strip()[:10]
    return MISSING


def _holdings_meta(inputs: dict[str, Any]) -> dict[str, Any]:
    nested = inputs.get("holdings") if isinstance(inputs, dict) else None
    return nested if isinstance(nested, dict) else {}


def _price_types(inputs: dict[str, Any]) -> list[str]:
    assets = inputs.get("assets") if isinstance(inputs, dict) else None
    if not isinstance(assets, list):
        return []
    seen: list[str] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        label = item.get("used_price_type") or item.get("available_price_type")
        if isinstance(label, str) and label.strip() and label.strip() not in seen:
            seen.append(label.strip())
    return seen


def _warnings(record: dict[str, Any], view: Any) -> list[str]:
    values = getattr(view, "warnings", None)
    if not values:
        values = record.get("warnings")
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if str(item).strip()]


def render(view: Any) -> str:
    """Render one estimation view as Markdown, values taken as stored."""
    record = getattr(view, "record", None) or {}
    inputs = getattr(view, "inputs", None) or {}
    rows = list(getattr(view, "asset_rows", None) or [])
    meta = _holdings_meta(inputs)
    run_id = getattr(view, "run_id", None) or record.get("run_id")
    mode = getattr(view, "mode", "") or record.get("mode", "")

    lines = [
        f"# Estimation report — `{_text(run_id)}`",
        "",
        f"- Fund code: `{_text(record.get('fund_code'))}`",
        f"- Run id: `{_text(run_id)}`",
        f"- Model version: `{_text(record.get('model_version'))}`",
        f"- Created: {_text(record.get('created_at'))}",
        f"- Mode: {_text(mode)}",
        "",
        "## NAV",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Official NAV | {_number(record.get('base_nav'))} |",
        f"| Estimated NAV | {_number(record.get('estimated_nav'))} |",
        f"| Estimated return | {_pct_signed(record.get('estimated_return'))} |",
        f"| Target NAV date | {_date(record.get('target_nav_date'))} |",
        f"| Base NAV date | {_date(record.get('base_nav_date'))} |",
        f"| Coverage ratio | {_pct(record.get('coverage_ratio'))} |",
        f"| Uncovered weight | {_pct(record.get('uncovered_weight'))} |",
        "",
        "## Asset contributions",
        "",
    ]
    if rows:
        lines += [
            "| Ticker | Name | Weight | Contribution | Status |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            weight = _pct(row.get("weight"))
            contribution = _pct_signed(row.get("contribution"))
            lines.append(
                f"| `{_text(row.get('ticker'))}` | {_text(row.get('name'))} | "
                f"{weight} | {contribution} | {_text(row.get('status'))} |")
    else:
        lines.append(getattr(view, "message", "") or NO_ASSET_SOURCE_TEXT)

    lines += [
        "",
        "## Attribution",
        "",
        "| Field | Local | FX | CNY |",
        "| --- | --- | --- | --- |",
        f"| Return | {_pct_signed(record.get('local_return'))} | "
        f"{_pct_signed(record.get('fx_return'))} | "
        f"{_pct_signed(record.get('cny_return'))} |",
        f"| Contribution | {_pct_signed(record.get('local_contribution'))} | "
        f"{_pct_signed(record.get('fx_contribution'))} | {MISSING} |",
        "",
        "## Data quality",
        "",
        f"- Holdings quarter: {_text(record.get('holdings_quarter') or meta.get('quarter'))}",
        f"- Holdings scope: {_text(_SCOPE.get(str(record.get('holdings_scope') or meta.get('scope')), record.get('holdings_scope') or meta.get('scope')))}",
        f"- Disclosure date: {_date(record.get('disclosure_date') or meta.get('disclosure_date'))}"
        + ("（推算）" if _estimated(record, meta) else ""),
        f"- Holdings source: {_text(record.get('holdings_source') or meta.get('source'))}",
        f"- Price types: {', '.join(_price_types(inputs)) or MISSING}",
        f"- Look-ahead status: `{_text(record.get('look_ahead_status'))}`",
        "",
    ]
    warnings = _warnings(record, view)
    lines.append("### Warnings")
    lines.append("")
    if warnings:
        lines.extend(f"- {item}" for item in warnings)
    else:
        lines.append("- （無）")

    source = getattr(view, "asset_source", "") or ""
    lines += [
        "",
        "## Provenance",
        "",
        f"- Asset detail source: {_text(source)}",
        f"- Inputs snapshot present: {'是' if inputs else '否'}",
        f"- Snapshot run id matches: {'是' if _snapshot_matches(inputs, run_id) else MISSING}",
        "- All numbers above come from the persisted estimation record and its",
        "  inputs snapshot; this report does not re-estimate or recompute anything.",
        "",
    ]
    return "\n".join(lines)


def _number(value: Any) -> str:
    number = _float(value)
    return MISSING if number is None else f"{number:.4f}"


def _estimated(record: dict[str, Any], meta: dict[str, Any]) -> bool:
    flag = record.get("disclosure_date_estimated")
    if flag is None:
        flag = meta.get("disclosure_date_estimated")
    return bool(flag)


def _snapshot_matches(inputs: dict[str, Any], run_id: Any) -> bool:
    if not isinstance(inputs, dict):
        return False
    candidate = inputs.get("run_id")
    if candidate is None or run_id is None:
        return False
    return str(candidate) == str(run_id)


__all__ = ["render", "NO_ASSET_SOURCE_TEXT"]
