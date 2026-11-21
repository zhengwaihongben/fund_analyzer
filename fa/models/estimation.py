# -*- coding: utf-8 -*-
"""Estimation run / result models.

One estimation is one *run*: it has a stable ``run_id``, it records every input
it used, and its result is never overwritten by a later run.  The database
layer stores these objects (see ``fa/data/database``), and the estimator
orchestrator only ever builds them — it does not write SQL itself.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

from fa.models.return_result import PortfolioReturnResult, PortfolioStatus

#: Bumped whenever the valuation maths changes, so old runs stay explainable.
MODEL_VERSION = "2.2.0"


class LookAheadStatus(str, Enum):
    """How far the holdings of a run can be trusted to be point-in-time.

    A boolean cannot express the real situation today: the disclosure date is
    *derived* (quarter end + 21 days), so a run may be PIT-correct in shape and
    still rest on an estimated filing date.  Keeping the three states apart
    lets V2.3 separate strict results from estimated ones.
    """

    VERIFIED_SAFE = "verified-safe"      # disclosed before the target date, date known
    ESTIMATED_DISCLOSURE = "estimated-disclosure"  # PIT rule applied to an estimated date
    UNSAFE = "unsafe"                    # holdings disclosed after the target date
    UNKNOWN = "unknown"

    @property
    def is_strictly_safe(self) -> bool:
        return self is LookAheadStatus.VERIFIED_SAFE

    @classmethod
    def parse(cls, value: Any) -> "LookAheadStatus":
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().lower()
        for member in cls:
            if member.value == text:
                return member
        return cls.UNKNOWN


def _coerce_date(value: Any) -> date | None:
    """Accept a ``date``, a datetime or an ISO string (records carry strings)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).date()


def resolve_look_ahead_status(
    *, disclosure_date: date | None, disclosure_date_estimated: bool, target_date: date
) -> LookAheadStatus:
    """Classify a holdings selection against the target NAV date.

    ``disclosure_date`` is an *estimate* (quarter end + a filing lag) until the
    real filing date is known, so "the disclosure date is before the target"
    only proves safety when the date is not estimated.
    """
    disclosure_day = _coerce_date(disclosure_date)
    target_day = _coerce_date(target_date)
    if disclosure_day is None or target_day is None:
        return LookAheadStatus.UNKNOWN
    if disclosure_day > target_day:
        return LookAheadStatus.UNSAFE
    if disclosure_date_estimated:
        return LookAheadStatus.ESTIMATED_DISCLOSURE
    return LookAheadStatus.VERIFIED_SAFE


def new_run_id() -> str:
    return uuid.uuid4().hex


@dataclass
class EstimationRun:
    """Identity and timing of one estimate."""

    fund_code: str
    target_nav_date: date
    trade_date: date
    base_nav_date: date | None = None
    as_of: datetime | None = None
    mode: str = "qdii"
    model_version: str = MODEL_VERSION
    run_id: str = field(default_factory=new_run_id)
    created_at: datetime = field(default_factory=datetime.now)
    holdings_quarter: str | None = None
    holdings_date: date | None = None
    disclosure_date: date | None = None
    disclosure_date_estimated: bool = False
    holdings_source: str = ""
    look_ahead_status: LookAheadStatus = LookAheadStatus.UNKNOWN
    timezone: str = "Asia/Shanghai"
    cutoff: str = "15:00"

    def __post_init__(self) -> None:
        self.fund_code = str(self.fund_code or "").strip()
        if not self.fund_code:
            raise ValueError("EstimationRun.fund_code 不可為空")
        self.run_id = str(self.run_id or new_run_id())
        if not isinstance(self.look_ahead_status, LookAheadStatus):
            self.look_ahead_status = LookAheadStatus.parse(self.look_ahead_status)

    @property
    def look_ahead_safe(self) -> bool:
        """True only when the holdings are strictly point-in-time."""
        return self.look_ahead_status.is_strictly_safe

    def to_row(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "fund_code": self.fund_code,
            "mode": self.mode,
            "model_version": self.model_version,
            "trade_date": self.trade_date.isoformat(),
            "target_nav_date": self.target_nav_date.isoformat(),
            "base_nav_date": self.base_nav_date.isoformat() if self.base_nav_date else None,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "holdings_quarter": self.holdings_quarter,
            "holdings_date": self.holdings_date.isoformat() if self.holdings_date else None,
            "disclosure_date": self.disclosure_date.isoformat() if self.disclosure_date else None,
            "disclosure_date_estimated": 1 if self.disclosure_date_estimated else 0,
            "holdings_source": self.holdings_source,
            "look_ahead_safe": 1 if self.look_ahead_safe else 0,
            "look_ahead_status": self.look_ahead_status.value,
            "timezone": self.timezone,
            "cutoff": self.cutoff,
        }


@dataclass
class EstimationResult:
    """Outcome of a run: the estimated NAV plus its quality metadata."""

    run_id: str
    status: PortfolioStatus = PortfolioStatus.INSUFFICIENT_DATA
    base_nav: float | None = None
    estimated_nav: float | None = None
    estimated_return: float | None = None
    coverage_ratio: float = 0.0
    uncovered_weight: float = 1.0
    local_contribution: float = 0.0
    fx_contribution: float = 0.0
    data_quality: str = "unknown"
    quality_score: float | None = None
    portfolio: PortfolioReturnResult | None = None
    warnings: list[str] = field(default_factory=list)
    message: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, PortfolioStatus):
            self.status = PortfolioStatus.parse(self.status)
        self.run_id = str(self.run_id or "").strip()
        if not self.run_id:
            raise ValueError("EstimationResult.run_id 不可為空")

    @property
    def is_successful(self) -> bool:
        return self.estimated_nav is not None and self.status in (
            PortfolioStatus.ESTIMATED,
            PortfolioStatus.PARTIAL,
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "base_nav": self.base_nav,
            "estimated_nav": self.estimated_nav,
            "estimated_return": self.estimated_return,
            "coverage_ratio": self.coverage_ratio,
            "uncovered_weight": self.uncovered_weight,
            "local_contribution": self.local_contribution,
            "fx_contribution": self.fx_contribution,
            "data_quality": self.data_quality,
            "quality_score": self.quality_score,
            "message": self.message,
            "warnings": list(self.warnings),
        }
