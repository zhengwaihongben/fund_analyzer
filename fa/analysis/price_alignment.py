# -*- coding: utf-8 -*-
"""Map a price/FX series onto the valuation dates the fund needs.

The V2 rules (see ``docs/technical_specification/V2/03_market_calendar_and_alignment.md``)
require the alignment to be explicit:

* a price is taken from the **last completed session on or before** the wanted
  date, and the calendar of the asset's market decides what "session" means;
* the V2 staleness policy has four bands: ``0`` sessions is normal, ``1..WARN``
  is kept with a warning, ``EXCLUDE..MAX`` is excluded as too stale and
  ``>MAX`` is excluded as severely stale.  ``EXCLUDE`` and ``MAX`` are
  configured in :mod:`fa.config` so the code and the specification cannot drift
  apart again;
* a price that is excluded is never silently reused, so the coverage ratio
  tells the truth;
* a missing series is never treated as a 0 % return.

This module only answers "which date/value do I use, and how stale is it".
Return maths lives in :mod:`fa.analysis.returns`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from fa.config import STALE_SESSIONS_EXCLUDE, STALE_SESSIONS_MAX, STALE_SESSIONS_WARN
from fa.models.market_data import FxSeries, PriceSeries
from fa.models.return_result import ReturnStatus
from fa.utils.calendars import HolidayCalendar, calendar_for

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Alignment:
    """Outcome of aligning one series to one wanted date."""

    status: ReturnStatus
    wanted_date: date | None = None
    value: float | None = None
    actual_date: date | None = None
    stale_sessions: int = 0
    max_sessions: int = STALE_SESSIONS_MAX
    excluded: bool = False
    severe: bool = False

    @property
    def ok(self) -> bool:
        """True only when a usable value came back (stale-but-kept included)."""
        if self.value is None:
            return False
        return self.status.is_ok or self.status in _USABLE_STALE

    @property
    def is_stale(self) -> bool:
        return self.status in _USABLE_STALE and not self.excluded

    def describe(self) -> str:
        if self.value is None:
            if self.excluded:
                return (f"{self.status.value}（目標日 {self.wanted_date}，"
                        f"落後 {self.stale_sessions} 個交易日，已排除）")
            return f"{self.status.value}（目標日 {self.wanted_date}）"
        suffix = f"，落後 {self.stale_sessions} 個交易日" if self.is_stale else ""
        return f"{self.actual_date} = {self.value:g}{suffix}"


_USABLE_STALE = frozenset(
    {
        ReturnStatus.STALE_PRICE,
        ReturnStatus.STALE_BASE_PRICE,
        ReturnStatus.STALE_FX,
        ReturnStatus.STALE_BASE_FX,
    }
)


def _missing_status(base: bool, fx: bool) -> ReturnStatus:
    if fx:
        return ReturnStatus.MISSING_FX
    return ReturnStatus.MISSING_BASE_PRICE if base else ReturnStatus.MISSING_TARGET_PRICE


def _stale_statuses(base: bool, fx: bool) -> tuple[ReturnStatus, ReturnStatus]:
    """``(warn_status, exclude_status)`` for the requested endpoint."""
    if fx:
        return ReturnStatus.STALE_FX, ReturnStatus.MISSING_FX
    if base:
        return ReturnStatus.STALE_BASE_PRICE, ReturnStatus.MISSING_BASE_PRICE
    return ReturnStatus.STALE_PRICE, ReturnStatus.MISSING_TARGET_PRICE


def _resolve_calendar(
    calendar: HolidayCalendar | None, market: str | None, ticker: str | None
) -> HolidayCalendar:
    if calendar is not None:
        return calendar
    return calendar_for(market, ticker)


def _align(
    lookup,
    *,
    wanted_date: date | None,
    calendar: HolidayCalendar,
    ticker: str,
    base: bool,
    fx: bool,
    warn_sessions: int,
    exclude_sessions: int,
    max_sessions: int,
) -> Alignment:
    if wanted_date is None:
        return Alignment(status=_missing_status(base, fx), wanted_date=None)
    value, actual = lookup(wanted_date)
    if value is None or actual is None:
        return Alignment(status=_missing_status(base, fx), wanted_date=wanted_date)

    stale = calendar.sessions_stale(actual, wanted_date)
    warn_status, exclude_status = _stale_statuses(base, fx)
    # 嚴重過期：遠超上限，直接排除（並保留原始「為什麼」的狀態）。
    if stale > max_sessions:
        log.debug(
            "%s 價格 %s 落後目標日 %s 共 %s 個交易日（上限 %s），視為嚴重過期並排除",
            ticker,
            actual,
            wanted_date,
            stale,
            max_sessions,
        )
        return Alignment(
            status=exclude_status,
            wanted_date=wanted_date,
            value=None,
            actual_date=actual,
            stale_sessions=stale,
            max_sessions=max_sessions,
            excluded=True,
            severe=True,
        )
    # 政策中段：達到 EXCLUDE 就排除，但保留 stale-* 狀態說明真正原因。
    if stale >= exclude_sessions:
        log.debug(
            "%s 價格 %s 落後目標日 %s 共 %s 個交易日（>= %s），排除出覆蓋率",
            ticker,
            actual,
            wanted_date,
            stale,
            exclude_sessions,
        )
        return Alignment(
            status=warn_status,
            wanted_date=wanted_date,
            value=None,
            actual_date=actual,
            stale_sessions=stale,
            max_sessions=max_sessions,
            excluded=True,
        )
    # 輕微過期：保留數值，但降級並帶警告。
    if stale > warn_sessions:
        return Alignment(
            status=warn_status,
            wanted_date=wanted_date,
            value=value,
            actual_date=actual,
            stale_sessions=stale,
            max_sessions=max_sessions,
        )
    return Alignment(
        status=ReturnStatus.OK,
        wanted_date=wanted_date,
        value=value,
        actual_date=actual,
        stale_sessions=stale,
        max_sessions=max_sessions,
    )


def align_price(
    series: PriceSeries | None,
    wanted_date: date | None,
    *,
    calendar: HolidayCalendar | None = None,
    warn_sessions: int = STALE_SESSIONS_WARN,
    exclude_sessions: int = STALE_SESSIONS_EXCLUDE,
    max_sessions: int = STALE_SESSIONS_MAX,
    base: bool = False,
) -> Alignment:
    """Align an equity/ETF price series to ``wanted_date``."""
    if series is None:
        return Alignment(status=_missing_status(base, False), wanted_date=wanted_date)
    cal = _resolve_calendar(calendar, series.market, series.ticker)
    return _align(
        series.price_on_or_before,
        wanted_date=wanted_date,
        calendar=cal,
        ticker=series.ticker,
        base=base,
        fx=False,
        warn_sessions=warn_sessions,
        exclude_sessions=exclude_sessions,
        max_sessions=max_sessions,
    )


def align_fx(
    series: FxSeries | None,
    wanted_date: date | None,
    *,
    calendar: HolidayCalendar | None = None,
    warn_sessions: int = STALE_SESSIONS_WARN,
    exclude_sessions: int = STALE_SESSIONS_EXCLUDE,
    max_sessions: int = STALE_SESSIONS_MAX,
    base: bool = False,
) -> Alignment:
    """Align a ``<CUR>CNY`` FX series to ``wanted_date``."""
    if series is None:
        return Alignment(status=ReturnStatus.MISSING_FX, wanted_date=wanted_date)
    cal = _resolve_calendar(calendar, None, None)
    return _align(
        series.rate_on_or_before,
        wanted_date=wanted_date,
        calendar=cal,
        ticker=f"{series.currency}CNY",
        base=base,
        fx=True,
        warn_sessions=warn_sessions,
        exclude_sessions=exclude_sessions,
        max_sessions=max_sessions,
    )


def describe_alignment(alignment: Alignment) -> str:
    """Short human-readable summary for logs and GUI tooltips."""
    return alignment.describe()


__all__ = ["Alignment", "align_price", "align_fx", "describe_alignment"]
