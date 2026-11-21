# V2.3.1 — Data Foundation Technical Specification

Status: **proposed, awaiting confirmation**. No V2.3.1 code is written until this
document is accepted (V2.3.0 lead decision, m05782).

Scope: everything the strategy backtest and the estimation validation need in
order to answer *"what did the system know at time T?"* — nothing about the
backtest engine itself.

Inputs to this spec: [V2.3.0 codebase audit](../development/V2.3_codebase_audit.md),
[V2.3.0 historical data audit](../development/V2.3_historical_data_audit.md),
[04_database_schema](04_database_schema.md), [03_market_calendar_and_alignment](03_market_calendar_and_alignment.md).

---

## 1. Purpose and invariants

Today the database can say *which date* a price belongs to, but not *when the
system could have known it*. V2.3.1 fixes that, and makes historical data
append-only and reproducible.

Five invariants govern every design decision below:

| # | Invariant | Consequence |
|---|---|---|
| I-1 | A price is only usable for point-in-time decisions when its availability time is recorded **and** not later than the decision time. | NULL `available_at` never qualifies; the pipeline must record it or mark it `unknown`. |
| I-2 | Inferred times are never presented as observed times. | `availability_source` is mandatory; `inferred` is legal but always visible in output and quality. |
| I-3 | Stored history is append-only at the *value* level. | A vendor revision adds a row to a revision table; the current row may change only with the revision recorded. |
| I-4 | Missing data is never silently substituted (no 1.0 FX, no 0 % return, no "today's close"). | Absence propagates as a status, never as a number. |
| I-5 | V2.2 public interfaces, output files and CLI behaviour do not change. | Every schema change is additive; every new behaviour is opt-in. |

---

## 2. Time model

### 2.1 The three timestamps

| Field | Meaning | Who produces it | Example (US close 2026-11-16) |
|---|---|---|---|
| `market_timestamp` | The instant the price was **formed** in the market (session close, exchange-local, stored as UTC). | Calendar (`MarketCalendar.market_close_datetime`) | `2026-11-16T21:00:00Z` (16:00 EST) |
| `available_at` | Earliest instant the value **could be consumed by the system**. | Fetcher / pipeline, with `availability_source` | `2026-11-16T21:15:00Z` if inferred from close + lag |
| `fetched_at` | When **this installation** downloaded it. | Fetcher, always observed | `2026-11-17T09:02:11Z` |

These are never collapsed into one column. `market_timestamp` answers "what does
this price describe", `available_at` answers "may I use it yet", `fetched_at`
answers "when did we see it".

### 2.2 Availability provenance

```python
class AvailabilitySource(str, Enum):
    OBSERVED = "observed"            # vendor/API statefully reported it as available now
    VENDOR_TIMESTAMP = "vendor_timestamp"  # vendor supplied a publication timestamp
    INFERRED = "inferred"            # derived from a documented rule (close + lag)
    UNKNOWN = "unknown"              # not determinable
```

Rules:

1. `available_at` may be NULL only together with `availability_source='unknown'`.
2. `availability_source='inferred'` must also store the rule that produced it
   (`availability_note`, e.g. `"close+30m (Yahoo EOD, no publication time)"`).
3. An `observed` value requires evidence — the fetcher must have literally seen
   the data at that time (e.g. Eastmoney NAV present in a live response).
4. The PIT query (§7) uses `availability_source IN ('observed','vendor_timestamp','inferred')`.
   `unknown` rows exist for display/audit but are invisible to PIT consumers.

### 2.3 Time zone and DST rules

- All timestamps are stored as ISO-8601 **UTC** strings ending in `Z`
  (`2026-11-16T21:00:00Z`). No naive datetimes anywhere in storage.
- `market_timestamp` is computed from the exchange calendar's close in that
  exchange's tz for that *date* — this is what makes DST correct: the US close
  is 21:00 UTC in EST and 20:00 UTC in EDT, and the same date yields different
  UTC instants across the DST boundary.
- Dates (`price_date`, `rate_date`, `nav_date`) remain *market-local* calendar
  dates; they are never converted.
- Display converts to `Asia/Shanghai`; storage never does.

### 2.4 Default availability policy (configurable, documented)

| Source | `market_timestamp` | `available_at` default | `availability_source` |
|---|---|---|---|
| Yahoo chart (equity/ETF/FX EOD) | exchange close (`calendar.market_close_datetime`) | close + `PRICE_AVAILABILITY_LAG_MINUTES` (default **30**) | `inferred` |
| Eastmoney NAV (`pingzhongdata`) | fund NAV publication time unknown → NULL | fetch moment, only when the latest NAV date is the previous China trading day | `observed` (value only) / `inferred` (older rows) |
| Manual import (holdings, prices) | from the file | from the file, else NULL | `vendor_timestamp` / `unknown` |

New config keys (`fa/config.py`, env-overridable):
`PRICE_AVAILABILITY_LAG_MINUTES=30`, `FX_AVAILABILITY_LAG_MINUTES=30`,
`PRICE_STALE_SESSIONS_MAX` (already `STALE_SESSIONS_MAX=10`), and
`PRICE_REVISION_TOLERANCE=1e-8`.

---

## 3. Data model changes

### 3.1 `PricePoint`

Current: `price_date, close, adj_close, price_timestamp, retrieved_at, market, currency, available_price_type, used_price_type, source`.

Proposed (all additive, defaults keep V2.2 call sites working):

```python
@dataclass
class PricePoint:
    price_date: date
    close: float
    adj_close: float | None = None
    market_timestamp: datetime | None = None      # was price_timestamp
    available_at: datetime | None = None
    availability_source: AvailabilitySource = UNKNOWN
    availability_note: str = ""
    fetched_at: datetime | None = None            # was retrieved_at
    market: str = ""
    currency: str = ""
    available_price_type: str = ""
    used_price_type: str = ""
    source: str = ""
    dividend: float | None = None                 # optional, per share, on this date
    split: float | None = None                    # optional, split ratio on this date
```

- `price_timestamp` / `retrieved_at` remain as read-only aliases (deprecated),
  so V2.2 code and stored payloads keep working.
- Validation adds: `available_at` and `market_timestamp` must be tz-aware
  datetimes if present (`MarketDataError` otherwise).

### 3.2 `FxSeries` and FX points

FX reuses `PricePoint` for the observation but the semantic fields must be
explicit — a bare number is not enough:

```python
@dataclass
class FxSeries:
    currency: str          # foreign currency, e.g. "CHF"
    base_currency: str = ""  # "CNY"; required for any conversion
    source: str = ""
    points: list[PricePoint] = ...
    @property
    def direction(self) -> str: return "foreign_to_base"   # 1 CHF = rate CNY
```

- `rate` is `CNY per 1 unit of currency`. A CHECK constraint forbids values ≤ 0.
- Inversion is never inferred: a series whose `base_currency != 'CNY'` is
  rejected by `convert_to_cny()` (already the case in V2.2.3).
- Each point carries `price_date` (= observation date), `market_timestamp`,
  `available_at`, `availability_source`, `source`, `fetched_at`.

### 3.3 Corporate actions

`close`, `adj_close`, `dividend`, `split` are four distinct facts:

- `close` — as printed that day (raw).
- `adj_close` — vendor-adjusted for splits and distributions; used for returns.
- `dividend` / `split` — recorded when the vendor supplies them (Yahoo
  `events=div,splits`), otherwise NULL. They are **not** derived, and NULL means
  "not fetched", not "no event".

The backtest must never reconstruct returns by dividing raw closes across an
ex-date; it uses `adj_close` and records `used_price_type`.

---

## 4. Database Migration 002

Migration 002 is **additive only** (no table rebuild, no column drop), so it can
run on a V2.2.2 database with data in it. `SCHEMA_VERSION` becomes `2`;
`MIGRATIONS` gains `(2, MIGRATION_002)` and `MIGRATION_COLUMNS[2]` carries the
column additions.

```sql
-- 1. price time semantics (additive; guarded by a PRAGMA table_info check in
--    fa/data/database/connection.py because SQLite has no ADD COLUMN IF NOT EXISTS)
ALTER TABLE market_prices ADD COLUMN market_timestamp TEXT;
ALTER TABLE market_prices ADD COLUMN available_at TEXT;
ALTER TABLE market_prices ADD COLUMN availability_source TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE market_prices ADD COLUMN availability_note TEXT;
ALTER TABLE market_prices ADD COLUMN fetched_at TEXT;
ALTER TABLE market_prices ADD COLUMN first_fetched_at TEXT;
ALTER TABLE market_prices ADD COLUMN dividend REAL;
ALTER TABLE market_prices ADD COLUMN split REAL;
UPDATE market_prices SET market_timestamp = price_timestamp
 WHERE market_timestamp IS NULL AND price_timestamp IS NOT NULL;
UPDATE market_prices SET fetched_at = retrieved_at
 WHERE fetched_at IS NULL AND retrieved_at IS NOT NULL;
UPDATE market_prices SET first_fetched_at = fetched_at
 WHERE first_fetched_at IS NULL AND fetched_at IS NOT NULL;

-- 2. FX semantics
ALTER TABLE fx_rates ADD COLUMN base_currency TEXT NOT NULL DEFAULT 'CNY';
ALTER TABLE fx_rates ADD COLUMN market_timestamp TEXT;
ALTER TABLE fx_rates ADD COLUMN available_at TEXT;
ALTER TABLE fx_rates ADD COLUMN availability_source TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE fx_rates ADD COLUMN availability_note TEXT;
ALTER TABLE fx_rates ADD COLUMN fetched_at TEXT;
UPDATE fx_rates SET fetched_at = retrieved_at
 WHERE fetched_at IS NULL AND retrieved_at IS NOT NULL;

-- 3. look-ahead status (F-01)
ALTER TABLE estimation_runs ADD COLUMN look_ahead_status TEXT NOT NULL DEFAULT 'unknown';
UPDATE estimation_runs
   SET look_ahead_status = CASE WHEN look_ahead_safe = 0 THEN 'unsafe' ELSE 'unknown' END;

-- 4. append-only revisions of previously stored observations (I-3)
CREATE TABLE IF NOT EXISTS price_revisions (
    revision_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    price_date          TEXT NOT NULL,
    price_type          TEXT,
    close               REAL,
    adj_close           REAL,
    market_timestamp    TEXT,
    available_at        TEXT,
    availability_source TEXT,
    availability_note   TEXT,
    fetched_at          TEXT,
    first_fetched_at    TEXT,
    market              TEXT,
    currency            TEXT,
    source              TEXT,
    dividend            REAL,
    split               REAL,
    replaced_at         TEXT NOT NULL,
    reason              TEXT
);

CREATE INDEX IF NOT EXISTS idx_prices_available_at ON market_prices (ticker, available_at);
CREATE INDEX IF NOT EXISTS idx_fx_available_at ON fx_rates (currency, available_at);
CREATE INDEX IF NOT EXISTS idx_revisions_ticker_date ON price_revisions (ticker, price_date);
```

Notes and constraints:

- **Back-fill is conservative.** The legacy boolean already lost the four-state
  information: `look_ahead_safe = 1` cannot be restored to `verified-safe`, so it
  becomes `unknown`; only `look_ahead_safe = 0` carries information and becomes
  `unsafe`. (Decision of the lead, 2026-11-17. `estimation_inputs.payload_json`
  was checked and does not contain the field, so there is nothing to restore
  from.) A consequence: old runs are excluded from strict-PIT analysis until
  they are re-produced — which is honest.
- Existing price/FX rows get `availability_source='unknown'` and no
  `available_at` — honest: the system did not record when it learned them, and
  no later code may pretend otherwise. They stay readable for audit but are
  excluded from PIT queries (§6).
- `market_prices` keeps its current primary key, so re-downloading the same
  observation updates the current row **after** writing the previous value into
  `price_revisions` (implemented in `Repository._record_price_revisions`).
  `price_revisions` keeps every meaningful numeric column, including
  `dividend` and `split`, even though V2.3.1 does not populate them yet. A
  future migration 003 may rebuild the table with a revision-aware key if that
  is ever needed; 002 deliberately does not rebuild.
- Migration execution contract (implemented in `fa/data/database/connection.py`):
  one **explicit** transaction per migration (`BEGIN IMMEDIATE … COMMIT`),
  statements executed one by one with `conn.execute` — never `executescript`,
  which issues an implicit `COMMIT` and would break atomicity — and the
  `schema_version` row inserted **last**. Any failure (including a simulated
  crash) rolls back everything, so an interrupted run can simply be re-run;
  `initialise()` is idempotent.

### 4.1 Upgrade contract

- Target: any database at version 1 (V2.2.2/V2.2.3 users) upgrades to version 2
  in place, keeping all rows.
- Verified by tests on a **copy of `v2.2.3_baseline/database_backup.db`** (not a
  fresh database): row counts unchanged, new columns present with defaults,
  estimation history readable, `look_ahead_status` backfilled conservatively.
- V2.2 code paths must still work against a version-2 database (columns are
  added with defaults; nothing is renamed, the legacy
  `price_timestamp` / `retrieved_at` columns are still filled by the
  repository so old readers keep working).

---

## 5. Pipelines

### 5.1 Historical price pipeline (`fa/data/prices.py`, new)

Input:

```python
PriceRequest(
    tickers: list[str],
    start: date, end: date,
    interval: str = "1d",
    prefer_cache: bool = True,
)
```

Algorithm:

1. For each ticker, read `load_prices(ticker, start, end)` from SQLite.
2. Determine the missing tail `max(stored_date)+1 .. end` (and any leading gap).
3. Fetch only the missing range from Yahoo (`fetch_holding_prices(..., start, end)`).
4. Build `PriceSeries`, set `market_timestamp` from the calendar close, set
   `available_at = market_timestamp + lag` with `availability_source='inferred'`.
5. Persist via `save_prices()` which (a) inserts unseen observations,
   (b) records revisions before overwriting a changed value, (c) never deletes.
6. Return `PriceBatch(series: dict[str, PriceSeries], failures: list[Failure],
   fetched: int, revised: int)`.

Output invariants: every returned point has a `market_timestamp`; prices are
positive and finite; no point has `price_date > end`; the same call is
idempotent (second call fetches nothing new).

### 5.2 FX pipeline (`fa/data/fx.py`, extended)

Input `FxRequest(currencies, start, end, prefer_cache=True)`; output
`dict[str, FxSeries]` with `base_currency='CNY'` and the same time semantics.
Missing currency → absent from the dict (never a 1.0 series); the caller
(`nav_estimator`) already turns an absent series into `missing-fx`.

Persisted columns: `rate`, `base_currency`, `rate_date`, `market_timestamp`,
`available_at`, `availability_source`, `source`, `fetched_at`.

### 5.3 Incremental update rules

| Rule | Behaviour |
|---|---|
| Overlap window | Re-request `last_stored - 5 sessions .. end` to catch vendor fixes. |
| Unchanged value | Update `fetched_at` only; no revision row. |
| Changed value (|Δ| > `PRICE_REVISION_TOLERANCE`) | Write the **old** row into `price_revisions` (`reason='vendor-revision'`), then update the current row. |
| Row disappears from vendor | Keep stored row untouched; log `vendor-missing`; never delete. |
| Duplicate in one response | Keep last, log `duplicate-in-response`, count in report. |
| Non-positive / non-finite | Reject the observation, log `anomaly`, keep the stored value. |
| Jump > `PRICE_JUMP_WARN` (default 50 %) | Accept but flag `anomaly=jump` in the fetch report (corporate actions make this legitimate). |

---

## 6. PIT query rules

### 6.1 Definition

A row is **usable at `as_of`** iff

```
available_at IS NOT NULL
AND available_at <= as_of
AND availability_source IN ('observed','vendor_timestamp','inferred')
```

`unknown` rows are excluded from PIT reads and reported as
`availability_unknown` in the query result — they may still be displayed to a
human, but a backtest must not trade on them.

### 6.2 Required API (Repository, additive)

```python
def prices_as_of(ticker, as_of: datetime, *, start=None, end=None) -> PriceSeries
def fx_as_of(currency, as_of: datetime, *, start=None, end=None) -> FxSeries
def snapshot_as_of(fund_code, day: date) -> HoldingSnapshot | None   # exists today
def valuation_inputs_as_of(fund_code, target_nav_date, as_of) -> dict  # §6.4
```

All three return **only** rows satisfying §6.1. If a ticker has no usable rows
the series is empty (not silently extended with later data).

### 6.3 `as_of` semantics

- `as_of` is a tz-aware instant (UTC or Beijing; converted internally).
- A backtest on decision time `T` calls `prices_as_of(ticker, T)`; the returned
  last point is the newest price the strategy could have seen.
- Querying with `as_of = now` must return the same rows as the current table
  (minus `unknown`), i.e. the PIT layer is a filter, not a separate store.

### 6.4 Valuation inputs as of a date

`valuation_inputs_as_of(fund_code, target_nav_date, as_of)` returns the exact
bundle a re-run would need:

```python
{
  "fund_code", "target_nav_date", "as_of",
  "base_nav": {"date", "nav", "available_at"},
  "holdings": {"quarter", "holding_date", "disclosure_date",
                "disclosure_date_estimated", "source"},
  "prices": {ticker: {"base_date", "base_price", "target_date", "target_price",
                       "price_type", "available_at"}},
  "fx": {currency: {"base_date", "base_rate", "target_date", "target_rate",
                    "available_at"}},
  "calendars": {ticker: {"market", "fallback"}},
  "missing": [{"ticker", "reason"}],
}
```

This is the object V2.3.2 consumes and V2.3.3 stores per run. It must be
reproducible: calling it twice with the same `as_of` returns identical numbers
unless a revision was recorded, in which case the newer revision is used and its
`revision` id is included.

---

## 7. Holdings and calendars (Stage 4)

- **Holdings importer** `fa/data/holdings_import.py`: CSV/JSON → `HoldingSnapshot`
  (fund_code, quarter, holding_date, disclosure_date, weights). The disclosure
  date is **mandatory** and, when not obtained from a filing, must be entered as
  estimated (`disclosure_date_estimated=True`). Import validates weights
  (0–1, sum ≤ 1.0 + tolerance) and refuses duplicates for the same quarter
  unless `--replace` is explicit (which then records the previous snapshot).
- **Calendar fix**: `EBS` → `CH` in `fa/utils/calendars/__init__.py`
  (`_EXCHANGE_NAMES`), plus `SIX`, `SWX`, `XSWX`. A Swiss calendar is added as a
  holiday table for 2025–2026 (stage 4) or, if the table is not ready, CH stays
  on the weekend fallback **but the fallback must be recorded** (already the
  case) and the report must say so.

---

## 8. Synthetic dataset (Stage 5)

`tests/fixtures/synthetic_markets/` — a frozen, committed dataset:

- 2 years of daily closes for: 3 US tickers (USD), 2 Swiss (CHF), 2 UK (GBP),
  1 HK (HKD); plus `Close`, `AdjClose`, `Dividend`, `Split` columns.
- Daily FX for USD/CHF/GBP/HKD → CNY.
- Known corporate actions: one 2-for-1 split, one 1.5 % dividend.
- Known gap: one US holiday, one Swiss holiday, one DST boundary week.
- Expected outputs committed alongside (`expected_returns.json`): asset-level
  local/FX/CNY returns and portfolio return for 4 rebalance dates.

Purpose: the engine and the pipelines get a **network-free, byte-stable**
reference. Any change in numbers is a deliberate, reviewable test update.

---

## 9. Test plan (test-first per stage)

### Stage 1 — Data model & migration (implemented)
- `tests/test_time_model.py`: tz-aware validation; DST boundary (US close
  2026-03-06 = 21:00Z, 2026-03-09 = 20:00Z); naive datetime rejected;
  `AvailabilitySource` parsing; alias properties still work.
- `tests/test_migration_002.py`:
  - upgrade on a **copy of the V2.2.3 baseline db** → version 2, all row counts
    unchanged, new columns present, `look_ahead_status` backfilled
    conservatively (`0 → unsafe`, `1 → unknown`, never `verified-safe`);
  - fresh database → version 2 directly;
  - interrupted migration (injected exception between statements) → version
    stays 1, schema unchanged, re-run succeeds;
  - V2.2 read paths still work on the upgraded db (`load_prices`, `load_fx`,
    `load_nav`, `load_estimation`);
  - the frozen baseline file is byte-identical before and after the run.
  - new availability fields survive a repository round trip.

### Stage 2 — FX pipeline
- missing currency never yields a 1.0 series;
- direction: a fixture where inverted data would produce a visibly different
  number, asserting we never invert;
- incremental update writes a revision when a rate changes, none when identical;
- `fx_as_of` excludes a row whose `available_at` is after `as_of`;
- `unknown` availability is excluded and reported.

### Stage 3 — Price pipeline
- idempotent second fetch (0 new rows);
- revision recorded on vendor change, old value preserved;
- duplicate rows in one response collapsed and counted;
- jump/anomaly flagged, not silently dropped;
- `prices_as_of` never returns a row from `price_date > as_of`'s market close;
- `used_price_type` recorded for adjusted vs raw.

### Stage 4 — Holdings & calendar
- importer validates weights, requires disclosure date, refuses duplicates;
- `EBS` maps to CH; `calendar_for("EBS", "ROP SW")` returns a CH calendar;
- point-in-time selection still ignores an undisclosed quarter.

### Stage 5 — Synthetic dataset & end-to-end validation
- `valuation_inputs_as_of` reproduces the committed expected bundle;
- full pipeline over the fixture produces the committed expected returns
  (±1e-10);
- cross-market, cross-timezone, multi-currency reconciliation:
  `Σ(w·r_CNY) == R_fund` and `Σ C_local + Σ C_fx == R_fund`.

### Acceptance additions from the review

| Requirement | Test |
|---|---|
| A. No unverified `available_at` treated as verified | PIT queries exclude `unknown`; `inferred` flagged in output |
| A. No future prices | `prices_as_of(past)` cannot see later rows |
| A. Timezones unified, DST tested | `test_time_model.py` |
| B. No unhandled duplicates | pipeline test collapses + counts them |
| B. FX direction never inverted | fixture test above |
| B. Missing FX never 1.0 | `missing-fx` status test |
| B. Updates never destroy history | revision table test |
| C. Migration from V2.2.2 | baseline-copy test |
| C. Old data preserved | row-count + checksum test |
| C. Migration tested on upgrade, not only fresh | same |
| C. Interrupted migration recovery | injected-failure test |
| D. V2.2 interfaces untouched | full suite (332 today) stays green; CLI output diff |

---

## 10. Completion criteria (from the review, mapped)

| # | Criterion | Verification |
|---|---|---|
| 1 | ≥2 years of multi-currency prices stored | audit script reports `session_coverage ≥ 0.95` per ticker over the window |
| 2 | Corresponding FX stored | `fx_rates` has all currencies used, same coverage bar |
| 3 | Every row has market time and availability, unknowns marked | `availability_source` distribution reported; `unknown` count visible |
| 4 | Query data available at a historical instant | `prices_as_of` / `fx_as_of` tests |
| 5 | Rebuild holdings + valuation inputs for a date | `valuation_inputs_as_of` round-trip test |
| 6 | Migration preserves V2.2 data and behaviour | migration tests + full suite |
| 7 | Synthetic data validates cross-market/tz/currency | fixture tests |

---

## 11. Out of scope

- The backtest engine, strategy models, trade simulation (V2.3.2).
- UI for any of the above (V2.4).
- Intraday/real-time prices, tick data, order books.
- Automatic quarterly-holdings download (dropped by decision in V2.1); the
  importer is manual by design.
- Modelling dividends/splits independently of the vendor's `adj_close` — we
  record them, we do not recompute total return from them in V2.3.1.

---

## 12. Decisions (answered by the lead, 2026-11-17)

1. **Availability lag defaults** — accepted: Yahoo history is stored with
   `availability_source='inferred'`, `available_at = market close + 30 minutes`,
   and an `availability_note` that states the rule. It is never described as the
   vendor's publication time.
2. **`unknown` handling in PIT** — accepted: excluded from PIT queries, kept for
   audit. `PricePoint.is_pit_usable` encodes this.
3. **Dividends/splits** — accepted: nullable columns only in V2.3.1, no event
   fetching, returns keep using `adj_close`.
4. **Historical window** — accepted: default two years, `--years 5` available.
5. **Migration back-fill** — corrected by the lead: `look_ahead_safe = 1` maps to
   `unknown`, not `verified-safe` (the boolean already lost the distinction).
   Implemented and covered by `tests/test_migration_002.py`.

### 12.1 Stage 1 status (implemented and verified)

Delivered: `AvailabilitySource` + the new `PricePoint` time fields (with the
V2.2 aliases `price_timestamp` / `retrieved_at` kept as properties that still
reflect the new fields), schema version 2 with `MIGRATION_002`, an atomic
migration runner, the `price_revisions` ledger written on re-download changes,
and repository read/write support for the new columns (legacy columns are kept
in sync so V2.2 readers still see the timestamps).

Verification: `python -m compileall -q fa tests fund_analyzer.py
fund_analyzer_gui.py` → 0; `python -m unittest discover -s tests -t .` →
332 tests OK; migration exercised on copies of
`docs/development/v2.2.3_baseline/database_backup.db` (row counts preserved,
baseline file byte-identical); a live `000369` run upgraded the working database
to version 2 in place and produced an estimate as before.

Not done in Stage 1 (deliberately): populating `available_at` for new fetches
(stage 3 pipeline), FX persistence (stage 2), `EBS → CH` (stage 4).

### 12.2 Review fixes on top of Stage 1 (2026-11-17)

The lead's review asked for five local fixes; all are implemented and covered
by tests, without redesigning the migration or the repository.

1. **Legacy time columns cannot be wiped.** The `market_prices` upsert uses
   `price_timestamp = COALESCE(excluded.price_timestamp,
   market_prices.price_timestamp)` (same for `retrieved_at`), so a point that
   only carries the new fields never NULLs the columns V2.2 readers use.
   Test: `test_database.py::MarketDataTests::test_upsert_without_timestamps_keeps_the_legacy_columns`.
2. **FX base currency is explicit.** `Repository.save_fx()` raises
   `DatabaseError` when `FxSeries.base_currency` is empty or not `CNY`; the
   silent `or "CNY"` fallback is gone (wrong direction would invert returns).
   `FxSeries.from_pairs()` keeps its documented CNY default because it is an
   internal, defined construction.
   Tests: `test_fx_without_base_currency_is_rejected`,
   `test_fx_with_non_cny_base_is_rejected`.
3. **Revision triggers are complete and documented.** `close`, `adj_close`,
   `dividend` and `split` always trigger a revision; `market_timestamp` does so
   only when a known stored instant is *changed* (`NULL → value` is
   enrichment). Availability metadata (`available_at`, `availability_source`,
   `availability_note`, `source`, `market`, `currency`, `fetched_at`) is
   enrichment and never triggers. `price_revisions.reason` names the changed
   fields.
   Tests: `test_dividend_only_change_is_recorded_as_a_revision`,
   `test_split_only_change_is_recorded_as_a_revision`,
   `test_availability_enrichment_is_not_a_revision`.
4. **Storage is UTC, serialisation is `Z`.** `PricePoint.__post_init__` and both
   alias setters run every instant through `_to_utc()`, `to_dict()` writes
   `iso_utc()` (`…Z`), and the repository writes `iso_utc()` into every
   timestamp column.
   Tests: `test_time_model.py::TimezoneTests::test_aware_timestamps_are_normalised_to_utc`,
   `UtcSerialisationTests`, `test_database.py::test_timestamps_are_stored_as_utc_with_z`.
5. **V2.2 construction compatibility restored.** `PricePoint` now has a custom
   `__init__` that accepts `price_timestamp=` / `retrieved_at=` alongside the
   new names; supplying both names for the same instant raises
   `MarketDataError` (no undefined winner), and unknown keyword arguments raise
   `TypeError`.
   Tests: `test_legacy_keyword_names_still_construct_a_point`,
   `test_new_and_legacy_names_together_are_rejected`.

Verification after the fixes: 344 tests OK, `compileall` 0, and a live `000369`
run (`2.824 → 2.8266`, `+0.09 %`, coverage 46.1 %). The live run also
exposed one unrelated data-loss bug on the Database fallback path:
`load_quarters_from_db()` did not carry `asset_type`, so every holding read back
from SQLite became `unknown` and nothing could be valued offline. It now copies
the stored declared type; covered by
`test_qdii.py::...::test_holdings_frame_roundtrips_through_the_database`.

### 12.3 Second review round: three consistency fixes (2026-11-17)

The reviewer re-read the pasted code and found three inconsistencies plus one
copy-compatibility risk. All four are fixed; the data model and Migration 002
are unchanged.

1. **`available_at` / `availability_source` is now bidirectional.** Previously
   only "an instant without provenance" was rejected, so `None + observed`
   was accepted even though the model states that an empty `available_at` must
   stay `unknown`. `PricePoint.__post_init__` now also raises when
   `available_at is None` and the source is not `UNKNOWN`.
   Test: `test_time_model.py::AvailabilitySourceTests::test_source_without_available_at_is_rejected`
   (covers `observed`, `vendor_timestamp`, `inferred`).
2. **The revision ledger compares the value the upsert will actually store.**
   `dividend` / `split` are written with `COALESCE(excluded, 舊值)`, so an
   incoming `None` means "not provided" and the stored value survives — yet the
   old comparison flagged it as changed and recorded a revision that never
   happened. `_record_price_revisions()` now compares the *effective* value
   (`incoming if incoming is not None else stored`) for the COALESCE fields and
   keeps comparing `close` / `adj_close` directly, because those are overwritten.
   Tests: `test_database.py::MarketDataTests::test_dividend_not_provided_is_not_a_revision`,
   `test_split_not_provided_is_not_a_revision` (the existing
   dividend-only / split-only tests still prove that a real change is recorded).
3. **`market` / `currency` / `source` are real enrichment now.** The comment
   claimed they were enrichment but the SQL overwrote them, so a later download
   that carried empty strings wiped the stored market and currency. The upsert
   uses `COALESCE(NULLIF(excluded.<col>, ''), market_prices.<col>)`.
   Test: `test_database.py::MarketDataTests::test_upsert_without_market_currency_source_keeps_the_stored_values`.
4. **`with_used_price_type()` copy compatibility.** Because `PricePoint` is
   `@dataclass(init=False)`, `dataclasses.replace()` re-enters the custom
   `__init__`. A test now pins that the copy keeps both time fields, the
   availability metadata, dividend/split, market/currency/source and both V2.2
   aliases.
   Test: `test_time_model.py::LegacyAliasTests::test_with_used_price_type_preserves_every_field`.

Verification after the fixes: `python -m compileall -q fa tests
fund_analyzer.py fund_analyzer_gui.py` → 0; `python -m unittest discover -s
tests -t .` → **349 tests OK**; live `000369` run reproduced
`2.8240 → 2.8266 (+0.09 %)`, coverage 46.1 %, 10 holdings valued, and the
working database kept `market_prices` 93 rows with `0` revision rows (no
spurious ledger entries), `market` / `currency` / `source` non-empty on every
row, `fx_rates` still `0` (Stage 2 has not run). `pytest` is not installed in
this environment (the project runner is `unittest discover`).

### 12.4 pytest verification (2026-11-17, second environment)

The reviewer asked for `python -m pytest -q`. pytest was subsequently installed
in the `quant-finance` conda environment
(`C:\Users\user\anaconda3\envs\quant-finance\python.exe`, Python 3.11.16,
pytest 9.2.0), which is a *different* interpreter from the project's default
Python 3.10, so the run doubles as a portability check.

Two environment problems surfaced, neither of them a code defect:

1. Bare `pytest -q` at the repository root collected `_tmp/` (68 sandbox
   leftovers from the earlier conda 3.13 era) and
   `docs/development/v2.2.3_baseline/test_results.txt`, producing collection
   errors. Fixed by adding `pytest.ini` with `testpaths = tests` and a
   `norecursedirs` list; the project's own directories are unchanged.
2. The first real run reported `1 failed, 348 passed`:
   `tests/test_analysis.py::...::test_writes_latest_quarter_pie_and_clean_csv`
   asserted the literal string `最新季度持仓` in the generated HTML, but the
   plotly version in that environment serialises the chart title as
   `\u6700\u65b0...` escapes. The assertion was environment-dependent, so the
   test now normalises `\uXXXX` sequences before matching
   (`re.sub(r"\\u([0-9a-fA-F]{4})", ...)`), accepting both literal and escaped
   output. The chart itself was always correct.

Final: `python -m pytest -q` → **349 passed, 19 subtests passed in 23.19s**;
`python -m unittest discover -s tests -t .` on Python 3.10 → **349 tests OK**.
