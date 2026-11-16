# -*- coding: utf-8 -*-
"""Valuation-date alignment for QDII NAV estimates.

The fund, the overseas exchanges and the calendar all disagree about what
"yesterday" means, so the estimator never computes a date inline.  This module
produces the four dates an estimate is defined by:

``trade_date``      the China trading day the order would belong to
``target_nav_date`` the fund NAV date being estimated
``base_nav_date``   the latest official NAV date used as the starting point
``nav_lag_days``    China trading days between ``base_nav_date`` and the target

``trade_date`` follows the real settlement rule: an order placed **before 15:00
Beijing** belongs to ``today`` (when today is a China trading day), an order
placed after the cut-off belongs to the next China trading day.  "Today" is
always evaluated in Beijing time (:func:`fa.utils.calendar.beijing_now`), never
with the host machine's local clock, so running the tool abroad cannot shift the
target date by one day.  Weekends and public holidays come from
:mod:`fa.utils.calendar`; ``today - 1 day`` is never used anywhere.
"""
from __future__ import annotations

from datetime import datetime, time as dtime

import pandas as pd

from fa.utils.calendar import (
    cn_trading_days_between,
    is_cn_trading_day,
    next_cn_trading_day,
    previous_cn_trading_day,
    to_beijing,
    to_date,
)
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

# 北京時間 15:00 之後下單，仍然只能對已收市的海外交易時段估值
CHINA_CUTOFF = dtime(15, 0)
# 記錄在結果裡，讓下游／使用者知道日期是怎麼算出來的
TIMEZONE_NAME = "Asia/Shanghai"


def _nav_dates(nav_dates) -> list:
    if nav_dates is None:
        return []
    if isinstance(nav_dates, pd.Series):
        nav_dates = nav_dates.tolist()
    return sorted({to_date(d) for d in nav_dates})


def order_trade_date(as_of: datetime | None = None):
    """China trading day an order placed at ``as_of`` belongs to.

    Before the 15:00 cut-off on a China trading day the order is part of that
    day; otherwise it is part of the next China trading day.
    """
    moment = to_beijing(as_of)
    today = moment.date()
    if moment.time() < CHINA_CUTOFF and is_cn_trading_day(today):
        return today, moment
    return next_cn_trading_day(today), moment


def resolve_valuation_dates(nav_dates, as_of: datetime | None = None,
                            target_nav_date=None) -> dict:
    """Work out the date set for one estimate.

    ``nav_dates`` are the dates for which an **official** NAV is already
    published.  ``target_nav_date`` forces a specific target (used by the
    backtest); otherwise it is derived from ``as_of`` in Beijing time.
    """
    trade_day, moment = order_trade_date(as_of)
    common = {"timezone": TIMEZONE_NAME,
              "cutoff": CHINA_CUTOFF.strftime("%H:%M"),
              "as_of_beijing": moment.isoformat(timespec="seconds")}

    if target_nav_date is not None:
        target = to_date(target_nav_date)
        if target >= trade_day:
            return {
                **common,
                "trade_date": trade_day.isoformat(),
                "target_nav_date": target.isoformat(),
                "base_nav_date": None,
                "nav_lag_days": 0,
                "status": "invalid-target",
                "note": "目標日期必須早於交易日期",
            }
    else:
        target = previous_cn_trading_day(trade_day)

    published = [d for d in _nav_dates(nav_dates) if d <= target]
    if not published:
        return {
            **common,
            "trade_date": trade_day.isoformat(),
            "target_nav_date": target.isoformat(),
            "base_nav_date": None,
            "nav_lag_days": 0,
            "status": "no-nav",
            "note": "沒有可用於估算的官方淨值",
        }

    if published[-1] == target:
        return {
            **common,
            "trade_date": trade_day.isoformat(),
            "target_nav_date": target.isoformat(),
            "base_nav_date": target.isoformat(),
            "nav_lag_days": 0,
            "status": "up-to-date",
            "note": "官方淨值已更新，無需估算",
        }

    base = published[-1]
    return {
        **common,
        "trade_date": trade_day.isoformat(),
        "target_nav_date": target.isoformat(),
        "base_nav_date": base.isoformat(),
        "nav_lag_days": cn_trading_days_between(base, target),
        "status": "ok",
        "note": None,
    }
