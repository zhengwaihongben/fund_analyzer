# -*- coding: utf-8 -*-
"""Data quality grading for V2 estimates.

V2 keeps two separate questions apart:

* **Coverage Ratio** — how much of the fund do we have usable data for?
* **Data Quality** — how reliable is that data?

A stale price may still be used (policy B), but the estimate's quality grade
drops and the affected weight is recorded, so a 46 %-coverage estimate built on
week-old prices can never look like a fresh full-coverage one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from fa.models.return_result import ReturnStatus


class QualityGrade(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"

    @classmethod
    def from_score(cls, score: float) -> "QualityGrade":
        if score >= 85.0:
            return cls.HIGH
        if score >= 60.0:
            return cls.MEDIUM
        return cls.LOW


#: Statuses that make an asset unusable rather than merely less reliable.
HARD_FAILURES = frozenset(
    {
        ReturnStatus.MISSING_PRICE,
        ReturnStatus.MISSING_TARGET_PRICE,
        ReturnStatus.MISSING_BASE_PRICE,
        ReturnStatus.MISSING_CURRENCY,
        ReturnStatus.MISSING_FX,
        ReturnStatus.UNSUPPORTED_ASSET,
    }
)

#: Statuses that keep the asset but lower confidence.
SOFT_WARNINGS = frozenset(
    {
        ReturnStatus.STALE_PRICE,
        ReturnStatus.STALE_BASE_PRICE,
        ReturnStatus.STALE_FX,
        ReturnStatus.STALE_BASE_FX,
    }
)


@dataclass
class QualityReport:
    grade: QualityGrade = QualityGrade.UNKNOWN
    score: float = 0.0
    coverage_ratio: float = 0.0
    stale_weight: float = 0.0
    excluded_weight: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade.value,
            "score": round(self.score, 2),
            "coverage_ratio": self.coverage_ratio,
            "stale_weight": self.stale_weight,
            "excluded_weight": self.excluded_weight,
            "reasons": list(self.reasons),
        }


def assess_quality(
    results: Iterable[Any],
    *,
    disclosure_date_estimated: bool = False,
    base_nav_fresh: bool = True,
) -> QualityReport:
    """Grade an estimation from its per-asset return results.

    ``results`` may contain :class:`~fa.models.return_result.AssetReturnResult`
    instances or dicts with the same keys (``status``, ``weight``).
    """
    score = 100.0
    stale_weight = 0.0
    excluded_weight = 0.0
    covered_weight = 0.0
    reasons: list[str] = []

    for item in results:
        status = item.status if hasattr(item, "status") else item.get("status")
        weight = float(
            (item.weight if hasattr(item, "weight") else item.get("weight", 0.0)) or 0.0
        )
        try:
            status = ReturnStatus(status)
        except ValueError:
            status = ReturnStatus.UNSUPPORTED_ASSET
        if status.is_ok:
            covered_weight += weight
        elif status in SOFT_WARNINGS:
            covered_weight += weight
            stale_weight += weight
        elif status in HARD_FAILURES:
            excluded_weight += weight
        else:
            excluded_weight += weight

    if stale_weight > 0:
        score -= min(30.0, stale_weight * 100.0 * 0.5)
        reasons.append(f"輕微過期價格影響 {stale_weight * 100:.1f}% 的權重")
    if excluded_weight > 0:
        score -= min(40.0, excluded_weight * 100.0)
        reasons.append(f"{excluded_weight * 100:.1f}% 的權重因資料不足被排除")
    coverage = max(0.0, min(1.0, covered_weight))
    if coverage < 1.0:
        score -= (1.0 - coverage) * 40.0
        reasons.append(f"持倉覆蓋率僅 {coverage * 100:.1f}%")
    if disclosure_date_estimated:
        score -= 5.0
        reasons.append("持倉披露日為推算值")
    if not base_nav_fresh:
        score -= 10.0
        reasons.append("基準 NAV 並非目標日前最後一個交易日")

    score = max(0.0, min(100.0, score))
    return QualityReport(
        grade=QualityGrade.from_score(score),
        score=score,
        coverage_ratio=coverage,
        stale_weight=stale_weight,
        excluded_weight=excluded_weight,
        reasons=reasons,
    )
