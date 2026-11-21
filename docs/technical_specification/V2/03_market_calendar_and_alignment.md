# V2 — Market Calendar & Price Alignment

Implemented (V2.1) in `fa/utils/calendars/` (`base.py`, `us.py`, `china.py`,
`hong_kong.py`, `japan.py`) on top of the existing `fa/utils/calendar.py`
helpers.

## 1. Why this exists

A QDII fund may hold US, Hong Kong, Japanese and European assets simultaneously.
Their sessions, holidays and time zones differ, so "yesterday" is not one date.
Using the same calendar date for every asset silently compares prices from
different sessions, and using a price formed *after* the target time is
look-ahead bias.

## 2. Calendar interface

```python
class MarketCalendar(Protocol):
    market: str            # "US" | "HK" | "CN" | "JP"
    timezone: str          # IANA name of the exchange's local time zone

    def is_trading_day(self, day: date) -> bool: ...
    def previous_trading_day(self, day: date) -> date: ...
    def next_trading_day(self, day: date) -> date: ...
    def market_close_datetime(self, day: date) -> datetime: ...   # tz-aware
    def trading_days_between(self, start: date, end: date) -> int: ...
```

Chinese market: `fa.utils.calendars.china.ChinaCalendar` wraps the V1
`fa/utils/calendar.py` implementation (weekends + the 2025–2026 holiday table).
US/HK/JP calendars are rule-based (weekends + their published holiday sets for
the supported years) and **must** be cross-checked against the exchange's
official calendar when a year is added — that is why the constructors warn when
a year is outside the table:

```
WARNING 市場日曆未收錄 2027 年，將僅按週末判斷 (market=US)
```

Using a mature third-party calendar package remains the preferred long-term
option; the rule-based version exists so V2 has no hard dependency today.

## 3. Price alignment rules

For each asset and each valuation pair (`base_date`, `target_date`):

1. Pick `P_target` = last price with `price_date <= target_date` **in that
   asset's own market**, and `P_base` = last price with `price_date <= base_date`.
2. Record both dates on the result (`target_price_date`, `base_price_date`).
3. Compute staleness in **market sessions**, not calendar days:

   ```
   stale_sessions = calendar.trading_days_between(price_date, wanted_date) − 1
   ```

4. Apply the staleness policy (§4) to decide `ok` / `stale-*` / exclude.
5. Never move `price_date` forward to "fill" a hole, and never use a price
   whose `price_date > target_date` — even if it exists in the dataset.

Historical backtests follow exactly the same rule. There is no "use whatever
is closest" mode.

## 4. Staleness policy (option B — usable but lower confidence)

| Stale sessions | Alignment flag | Status | Treatment |
|----------------|----------------|--------|-----------|
| 0 | `ok` | `ok` | normal |
| 1–2 | `excluded=False`, kept | `stale-price` / `stale-base-price` / `stale-fx` / `stale-base-fx` | included, warning, quality penalty |
| 3–10 | `excluded=True`, no value | same `stale-*` name | excluded from the return, lowers coverage |
| > 10 | `excluded=True`, `severe=True` | `missing-*` | excluded, treated as a data gap |

Thresholds live in `fa/config.py` so they can be tuned without touching logic:

* `STALE_SESSIONS_WARN = 0` (`FA_STALE_SESSIONS_WARN`) — stale sessions above
  this value get the `stale-*` status. Zero means "any staleness is flagged".
* `STALE_SESSIONS_EXCLUDE = 3` (`FA_STALE_SESSIONS_EXCLUDE`) — at or above this
  the price is excluded; the band 1..EXCLUDE−1 stays included *with* a warning.
* `STALE_SESSIONS_MAX = 10` (`FA_STALE_SESSIONS_MAX`) — absolute safety cap;
  beyond it the observation is treated as missing rather than "stale".

`Alignment.ok` is only true when a value survived, so an excluded stale price
can never leak into coverage by accident. Both endpoints are checked: a fresh
target with a stale base produces `stale-base-price`, because the interval
itself is unreliable.

## 5. Coverage vs Data Quality

* **Coverage Ratio** — how much weight had usable data at all.
* **Data Quality** — how trustworthy that data was (see
  `fa/utils/data_quality.py`: score 0–100, grade `high` / `medium` / `low`).

Stale data reduces the grade but does not reduce coverage; missing data reduces
coverage. The estimate record stores both, plus the affected weights and the
actual price dates.

## 6. Time zone conventions

* All stored datetimes are timezone-aware; comparisons never rely on the host
  clock's zone.
* User-facing "as of" values without a zone are interpreted as **Beijing time**
  (`Asia/Shanghai`), because the product is used for Chinese funds.
* The China 15:00 cutoff determines the trade date (`order_trade_date` in
  `fa/analysis/valuation_dates.py`).

## 7. Reproducibility

Every estimate stores, for each asset: `price_date`, `base_price_date`,
`fx_date`, `base_fx_date`, `price_type`, `source`, `currency` and the staleness
verdict. Re-running the same run from stored inputs must reproduce the same
NAV; a change in vendor data must be visible as a *new* run, never as a silent
edit of an old one.

## 8. Acceptance criteria

* Weekends and the 2025/2026 China holiday table are respected (CNY/US/HK/JP).
* `market_close_datetime` returns a tz-aware instant for the exchange's zone.
* A price dated after the target date is never selected (test constructs such a
  series deliberately).
* Slightly stale prices are kept with a warning; severely stale prices are
  excluded and reduce coverage.
* Every price carries `source`, `price_type`, `price_date`, `retrieved_at`.
* Vendor exchange names are normalised before a calendar is picked
  (`NasdaqGS`/`NYQ` → US, `HKG` → HK, `JPX` → JP, `SIX Swiss Exchange` → CH,
  `LSE` → GB); an unrecognised exchange falls back to the weekend-only calendar,
  which sets `calendar_fallback=True` in the record and costs 5 quality points.
* The date window fetched for market data is the valuation interval itself
  (`start = base_date − 10 days`, `end = target_date`), so an estimate for an old
  target date does not use this month's quotes.
