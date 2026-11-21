# V2.3.1 Stage 2 — FX Pipeline Technical Specification

Status: **accepted; stages 2.1–2.5 delivered** (2026-11-18). Decisions D-1/D-2
were confirmed by the lead at m06904; delivery evidence for each sub-stage lives
in §13 (2.1), §14 (2.2), §15 (2.3), §16 (2.4) and §17 (2.5).

Provenance: work order m06736 ("按照這份描述開始開發 stage 2，完全採用建議");
evidence in [V2.3.1 Stage 2 audit](../development/V2.3.1_stage2_audit.md);
schema rules in [04_database_schema](04_database_schema.md);
time-model invariants in
[08_v231_data_foundation](08_v231_data_foundation.md) §2.

---

## 1. Scope

Turn the current *download-only* FX helper into a pipeline:

| Responsibility | Module | Allowed to |
|---|---|---|
| Fetch | `fa/data/fx_provider.py` (new adapter, `fa/data/fx.py` kept as façade) | HTTP only |
| Validate / normalise / schedule updates | `fa/data/fx_pipeline.py` (new) | pure logic, calls provider + repository |
| Store / read / point-in-time query | `fa/data/database/repository.py` | SQLite only |
| Consume | `fa/analysis/returns/*`, `fa/analysis/price_alignment.py` | unchanged |

No HTTP, SQL or cleaning code may appear inside `fa/analysis/nav_estimator.py`;
it keeps orchestrating only.

### 1.1 Out of scope (explicit)

Yahoo equity/ETF price pipeline, `prices_as_of()`, populating `available_at` in
the stock fetch layer, holdings importer, `EBS → CH` calendar mapping, Swiss
holidays, synthetic dataset, backtest engine, and any refactor of `fa/data/`
beyond what FX needs.

---

## 2. Decisions this stage settles

### D-1 — What a Yahoo FX daily bar *is* (measured, not assumed)

Evidence (probe `scripts/probe_fx_source.py`, 2026-11-17, 10 pairs):

| Observation | Value |
|---|---|
| `meta.exchangeName` / `instrumentType` | `CCY` / `CURRENCY` (no exchange) |
| `meta.exchangeTimezoneName` | `Europe/London`, `gmtoffset=0` |
| `currentTradingPeriod.regular` | `00:00→23:59 UTC` of the day (a full day, not a session close) |
| Daily bar stamps in GMT (Nov) | exactly `00:00:00Z` |
| Daily bar stamps in BST (Mar 29–Oct 25) | `23:00:00Z` of the **previous** UTC day (= `00:00 Europe/London`) |
| 1-year stamp histogram | `Mon 53, Tue 53, Wed 52, Thu 52, Fri 22, Sat 0, Sun 30` — a one-day UTC shift for the 30 BST weeks |
| Hourly bars | continuous `00:00–23:00Z`, 49 h gap Fri 23:00 → Mon 00:00 |
| Current-day bar | live: at `2026-11-17T07:39Z` the 11-17 bar close equalled `meta.regularMarketPrice` exactly (6.6649) |

**Decision.** A bar belongs to a **Europe/London calendar date** `D`. The vendor
stamp is `D 00:00 Europe/London` (the *start* of that day). Therefore:

| Field | Value |
|---|---|
| `rate_date` | London date `D` derived from the stamp (`ZoneInfo("Europe/London")`), **never** the UTC date |
| `market_timestamp` | the vendor stamp itself, normalised to UTC (`D 00:00Z` in GMT, `D-1 23:00Z` in BST) |
| `market_timestamp` semantics | *bar start*, explicitly not a close — the doc strings and the note say so |
| `available_at` | `(D + 1 day) 00:00 Europe/London + FX_AVAILABILITY_LAG_MINUTES` |
| `availability_source` | `inferred` |
| `availability_note` | `"Yahoo FX daily bar stamped at Europe/London day start; treated as closed at the end of the London day; available at next-day 00:00 London + 30 min (inferred)"` |

Consequences, all intentional:

1. A still-running bar (today) has `available_at` in the **future**, so point-in-
   time queries exclude it without a special "drop today" rule. `load_fx()`
   (non-PIT, V2.2 behaviour) still returns it.
2. In BST the previous implementation labelled every bar one day early
   (`_frame_from_chart` normalises to UTC midnight). Using the London date fixes
   that; see finding F-FX-01 in the audit.
3. `available_at` is an *inference* and is labelled as such; it is never
   presented as a vendor timestamp.

Rejected alternatives: (a) treat the stamp as a UTC close (falsifiable — the
bar's close at 07:39Z is still moving); (b) use `regularMarketTime` as the
close (it is the last tick at fetch time, not a session close); (c) invent a
session close from a stock market's calendar (FX has no single session).

### D-2 — Separate `fx_revisions` table via Migration 003

Agreed with the work order. `price_revisions` keys on `(ticker, price_date,
price_type)` and has no currency/base column; storing FX there would require a
synthetic `FX:USD/CNY` ticker, which is exactly the kind of fake-instrument
shortcut the project has banned elsewhere.

Migration 003 is **purely additive**: one table, two indexes, no back-fill
(`fx_rates` is empty in every database in this project today), no change to any
existing column or index.

---

## 3. Data model

### 3.1 `fx_rates` (existing table, semantics tightened)

Already has every needed column since Migration 002: `base_currency`,
`market_timestamp`, `available_at`, `availability_source`, `availability_note`,
`fetched_at`. Stage 2 changes only *who fills them*.

Invariants enforced by the pipeline (not by hope):

| # | Invariant |
|---|---|
| X-1 | `base_currency = 'CNY'`; the row means **CNY per 1 unit of `currency`** |
| X-2 | `rate` is finite and `> 0` |
| X-3 | `rate_date` is a London calendar date and lies inside the requested window |
| X-4 | CNY itself is never stored (no synthetic 1.0 series); a foreign asset without a rate stays `missing-fx` |
| X-5 | Inverted vendor data is **rejected**, never reciprocated into shape |
| X-6 | `availability_source` is `inferred` for pipeline-written rows; `unknown` rows remain readable by `load_fx()` but are outside PIT |

### 3.2 `fx_revisions` (new, Migration 003)

```sql
CREATE TABLE IF NOT EXISTS fx_revisions (
    revision_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    currency           TEXT NOT NULL,
    rate_date          TEXT NOT NULL,
    rate               REAL NOT NULL,
    base_currency      TEXT NOT NULL DEFAULT 'CNY',
    market_timestamp   TEXT,
    available_at       TEXT,
    availability_source TEXT NOT NULL DEFAULT 'unknown',
    source             TEXT,
    fetched_at         TEXT,
    replaced_at        TEXT NOT NULL,
    reason             TEXT
);
CREATE INDEX IF NOT EXISTS idx_fx_revisions_currency_date
    ON fx_revisions (currency, rate_date);
CREATE INDEX IF NOT EXISTS idx_fx_revisions_replaced_at
    ON fx_revisions (replaced_at);
```

The ledger stores the **superseded** value (old row), matching
`price_revisions`' convention. `reason` names the changed fields, e.g.
`"vendor revision replaced rate"` or `"vendor revision replaced rate, available_at"`.

### 3.3 Which changes create a revision

| Change | Revision? | Why |
|---|---|---|
| `rate` differs by more than `PRICE_REVISION_TOLERANCE` | **yes** | stored history must stay append-only at value level (I-3) |
| first observation of a date | no | it is an insert, nothing is overwritten |
| identical rate re-downloaded | no | only `fetched_at` is refreshed |
| `market_timestamp` corrected from NULL to a value | no | enrichment |
| `market_timestamp` corrected between two known instants | **yes** | changes "what this row describes" |
| `availability_*` refined (unknown → inferred) | no | metadata enrichment; documented in the note |
| row disappears from the vendor payload | no | vendor drop never deletes stored history |

---

## 4. Module interfaces

### 4.1 `fa/data/fx_provider.py` (new)

```python
FX_TIMEZONE = "Europe/London"

@dataclass(frozen=True)
class FxBar:
    rate_date: date
    rate: float
    market_timestamp: datetime      # aware, UTC
    source: str = "yahoo"

@dataclass
class FxFetch:
    currency: str
    symbol: str
    bars: list[FxBar] = field(default_factory=list)
    currency_raw: str | None = None
    error: str | None = None        # human-readable; bars may still be non-empty

def bar_trading_date(stamp: datetime, tz: str = FX_TIMEZONE) -> date
def fetch_fx_bars(currency: str, start: date, end: date,
                  *, timeout: float | None = None) -> FxFetch
def fetch_fx_bars_many(currencies: Iterable[str], start: date, end: date
                       ) -> dict[str, FxFetch]
```

* Provider is **read-only**: it never touches SQLite, never retries more than
  the existing single HTTP attempt, never invents a bar.
* `CNY` returns `FxFetch(currency="CNY", symbol="", bars=[], error=None)` — an
  explicit no-op, not a 1.0 series.
* Unsupported currency → `error="unsupported-currency"`, empty bars.
* The adapter asserts the documented convention from
  `FX-QUOTE-CONVENTION` (see §6) and reports `error="quote-direction-suspect"`
  when a bar falls outside the sanity band; it does **not** transform the value.

`fa/data/fx.py` stays the public façade: `fetch_fx_series()` /
`fetch_fx_rates()` / `fx_series_map()` keep their signatures and are
re-implemented on `fetch_fx_bars`, so V2.2 callers see corrected dates and the
same types.

#### 4.1.1 London time without a hard dependency

`zoneinfo.ZoneInfo("Europe/London")` is used when importable (it is on both
verified interpreters, with `tzdata` 2025.2 / 2026.4 installed), but
`requirements.txt` does not declare `tzdata` and
`fa/utils/calendar.py:31-35` deliberately avoided `ZoneInfo` because a bare
Windows interpreter may have no tz database. Since BST cannot be expressed as
a fixed offset, the adapter carries a documented fallback:

```python
def london_offset(moment: datetime) -> timedelta:
    """UTC+1 from the last Sunday of March 01:00 UTC to the last Sunday of
    October 01:00 UTC, else UTC+0 (EU rule; ZoneInfo when available)."""
```

A test asserts, for every day from 2025-01-01 to 2030-12-31, that the fallback
and `ZoneInfo` agree on both the date mapping and the offset. If `tzdata` is
later added to `requirements.txt`, the fallback stays as the tested safety net
rather than dead code.

### 4.2 `fa/data/fx_pipeline.py` (new)

```python
@dataclass
class FxRequest:
    currencies: list[str]
    start: date
    end: date
    prefer_cache: bool = True

@dataclass
class FxUpdateOutcome:
    series: dict[str, FxSeries]      # ready for the valuation engine
    inserted: int = 0
    updated: int = 0
    revisions: int = 0
    downloads: int = 0
    skipped: dict[str, str] = field(default_factory=dict)   # currency -> reason
    anomalies: list[dict] = field(default_factory=list)     # {currency, rate_date, kind, value}
    warnings: list[str] = field(default_factory=list)

def infer_availability(rate_date: date,
                       lag_minutes: int = FX_AVAILABILITY_LAG_MINUTES) -> datetime
def update_fx(request: FxRequest, repository: Repository, *,
              provider: Callable | None = None,
              now: datetime | None = None) -> FxUpdateOutcome
```

`update_fx` never raises for a data gap: a currency that cannot be fetched is
reported in `skipped` and omitted from `series` (the caller already turns a
missing series into `missing-fx`).

Stage 2.4 additionally exports two pure session helpers used by the cache rule:

```python
def previous_fx_session(day: date) -> date   # Monday–Friday rule only
def next_fx_session(day: date) -> date
```

They intentionally ignore vendor holidays: a holiday bar that never appears
must be an *allowed* interior gap, not a permanent cache miss, and demanding
every weekday would re-download two years of history on every run. The rule is
therefore "weekends are gaps we know; anything else is data the vendor may
legitimately omit".

### 4.3 `Repository` additions (`fa/data/database/repository.py`)

| Method | Contract |
|---|---|
| `save_fx(series) -> int` | **unchanged signature and return value** (rows written). Now also: records superseded rows into `fx_revisions` inside the same transaction, uses `COALESCE` for enrichment fields, rejects empty / non-CNY base as today |
| `fx_as_of(currency, as_of, start=None, end=None) -> FxSeries` | PIT query: `available_at IS NOT NULL AND availability_source IN ('observed','vendor_timestamp','inferred') AND julianday(available_at) <= julianday(:as_of)`; visibility depends on the instant only (no `rate_date <= date(as_of)` — that compared a London trading date with the caller's local date and hid already-public bars); `start`/`end` are an optional trading-date window, not a visibility rule; returns `FxSeries(base_currency="CNY", source="database")` |
| `count_fx_revisions(currency, start=None, end=None) -> int` | pipeline reporting without changing `save_fx`'s return type |
| `load_fx(...)` | **unchanged** (V2.2 read path, includes `unknown`) |

Revision comparison lives in the repository because it must be atomic with the
upsert; the *decision* to re-fetch an overlap window lives in the pipeline.

---

## 5. Update algorithm (2.4)

```
for currency in request.currencies:
    if currency == CNY: skip("base-currency")
    last = repository.last_fx_date(currency)
    fetch_start = request.start if last is None
                  else max(request.start, last - FX_OVERLAP_SESSIONS sessions→days)
    if request.prefer_cache and last is not None and last >= target_session(request.end):
        series = repository.fx_as_of(...) or load_fx(...)   # no download
    else:
        fetch = provider(currency, fetch_start, request.end)
        bars = normalise(fetch.bars)          # dates, dedupe, sort, validate
        series = FxSeries.from_bars(bars, base_currency="CNY")
        written = repository.save_fx(series)
```

* Overlap: **5 sessions** before the last stored date (a session is converted to
  calendar days with `FX_OVERLAP_CALENDAR_DAYS = 2 × sessions + 2`, because a
  5-session window spans 7–9 calendar days including weekends). Never "just the
  tail gap".
* Idempotency: running the same request twice must insert 0 new rows
  (acceptance FX-07).
* Cache: a fully covered window performs **no** download (FX-04).
* Failure: a provider error leaves existing rows untouched (no transaction is
  ever opened before validation passes).

---

## 6. Validation rules

| Rule | Action |
|---|---|
| `rate` not finite, or `<= 0` | drop the bar, `anomaly=invalid-rate`, warn |
| `rate_date` outside `[start, end]` | drop, `anomaly=out-of-range` |
| duplicate `rate_date` in one payload | keep the last, `anomaly=duplicate-date` (counted) |
| `|rate_t / rate_{t-1} − 1| > PRICE_JUMP_WARN` (50 %) | **keep**, `anomaly=jump` — never silently dropped, never smoothed |
| bar outside `FX_SANITY_BANDS[currency]` | keep, `anomaly=implausible`, `warnings` carry `quote-direction-suspect` |
| base currency cannot be established | reject the whole series (`skipped[currency]="missing-base-currency"`) |
| inverted pair detected (rate = 1/expected within tolerance) | reject, never reciprocate (`skipped[currency]="inverted-quote"`) |

`FX_SANITY_BANDS` (evidence from the probe, wide enough for real moves,
narrow enough that a reciprocal cannot pass):

| USD | HKD | CHF | GBP | EUR | JPY | CAD | AUD | SGD | TWD |
|---|---|---|---|---|---|---|---|---|---|
| 5.0–9.5 | 0.65–1.15 | 6.0–11.0 | 7.0–12.0 | 6.5–10.0 | 0.02–0.09 | 3.5–7.0 | 3.0–6.5 | 4.0–7.0 | 0.14–0.40 |

`FX_QUOTE_CONVENTIONS` records the audited truth: every symbol in `FX_TICKERS`
quotes CNY-per-unit, including the irregular `USD → "CNY=X"`; a symbol not in
this table is refused rather than assumed.

---

## 7. Point-in-time semantics

* `fx_as_of(currency, as_of)` is the only PIT entry point; it applies the three
  Stage-1 PIT rules (non-null `available_at`, `<= as_of`, known source).
* `align_fx()` (staleness bands) is unchanged and runs *after* the PIT filter:
  PIT answers "may I look at this row", staleness answers "how old is it".
* `unknown` legacy rows stay in `load_fx()`; `fx_availability_summary()` is not
  added — the audit query in `scripts/` covers it.
* A row whose `available_at` is in the future is invisible to PIT, including
  today's running bar (D-1).

---

## 8. Configuration (`fa/config.py`)

| Key | Default | Meaning |
|---|---|---|
| `FX_TIMEZONE` | `"Europe/London"` | bar day boundary (D-1) |
| `FX_AVAILABILITY_LAG_MINUTES` | `30` | inference lag after the London day ends |
| `FX_OVERLAP_SESSIONS` | `5` | re-fetch window before the last stored date |
| `FX_OVERLAP_CALENDAR_DAYS` | `12` | calendar translation of the above |
| `FX_DEFAULT_YEARS` | `2` | default history window (`--years 5` still accepted) |
| `PRICE_REVISION_TOLERANCE` | `1e-8` | absolute rate difference that counts as a revision |
| `PRICE_JUMP_WARN` | `0.50` | jump flag threshold |
| `FA_FX_PIPELINE` | `"1"` | 2.5 integration switch; `"0"` restores the legacy downloader path |

Environment override names follow the existing `FA_*` convention.

---

## 9. Acceptance mapping (FX-01 … FX-12)

| ID | Criterion | Where it is proven |
|---|---|---|
| FX-01 | direction: stored value is CNY per 1 foreign unit | `test_fx_provider.py::…direction…`, live smoke |
| FX-02 | full time fields on every new row | `test_fx_pipeline.py::…time_fields…` |
| FX-03 | no synthetic 1.0 for CNY, no series for unknown currency | `test_fx_pipeline.py::…cny…` |
| FX-04 | full cache ⇒ no download | `test_fx_pipeline.py::…cache…` |
| FX-05 | incremental update includes the 5-session overlap | `test_fx_pipeline.py::…overlap…` |
| FX-06 | material change keeps the old value in `fx_revisions` | `test_fx_repository.py::…revision…` |
| FX-07 | identical repeat request inserts nothing | `test_fx_pipeline.py::…idempotent…` |
| FX-08 | PIT excludes `available_at` in the future and NULL | `test_fx_repository.py::…pit…` |
| FX-09 | `unknown` rows readable by `load_fx`, excluded from PIT | `test_fx_repository.py::…legacy_unknown…` |
| FX-10 | V2.2 public API unchanged (`fetch_fx_series`, `fx_series_map`, `save_fx`, `load_fx`) | `test_fx_compat.py` |
| FX-11 | valuation engine unchanged: CNY needs no FX, foreign values or `missing-fx` | `test_fx_integration.py` |
| FX-12 | full suite + `compileall` + independent Migration 003 test | §11 |

Migration 003 proof (`tests/test_migration_003.py`): upgrade the frozen
`docs/development/v2.2.3_baseline/database_backup.db` copy → table exists,
`fx_revisions` empty, all row counts unchanged, `PRICE`/`ESTIMATION` data
untouched, schema version 3, re-run idempotent, baseline byte-identical.

---

## 10. Test plan

| File | Covers |
|---|---|
| `tests/test_fx_model.py` | `FxBar`/`FxSeries` construction: CNY base accepted, empty/non-CNY base rejected, zero/negative rate rejected, inverted never reciprocated, `bar_trading_date` across GMT/BST (2026-03-28, 2026-03-29, 2026-10-24, 2026-10-25) |
| `tests/test_fx_provider.py` | adapter mapping from a canned Yahoo payload (no network): stamps → London dates, missing close dropped, `error` propagation, unsupported currency, CNY no-op |
| `tests/test_fx_pipeline.py` | cache hit (no download), gap download + overlap, idempotency, multi-currency, failure keeps data, anomaly flags (jump, duplicate, out-of-range) |
| `tests/test_fx_repository.py` | save/load round trip, revision cases (new/identical/changed), vendor drop retained, `fx_as_of` `< = >` cases, NULL excluded, unknown excluded, inferred included, base CNY |
| `tests/test_migration_003.py` | upgrade from the frozen baseline, table + indexes, idempotency, byte-identical baseline |
| `tests/test_fx_compat.py` | V2.2 façade signatures and behaviour (`fetch_fx_series` returns a float Series indexed by date, `fx_series_map` builds CNY series) |
| `tests/test_fx_integration.py` | `convert_to_cny` unchanged; QDII valuation with a DB-backed FX series; `missing-fx` when absent |

All offline tests use a fake provider; the real-data smoke test is a separate
script (`scripts/smoke_fx_pipeline.py`, 2.5) and is never part of the unit suite.

---

## 11. Stage-2 sub-stage gates

| Gate | Deliverable | Exit condition |
|---|---|---|
| 2.1 | Migration 003 + `fx_revisions` + `test_migration_003.py` | **done 2026-11-17** — see §13 |
| 2.2 | `FxSeries.bars`, repository revision + `fx_as_of` + `test_fx_repository.py` | **done 2026-11-17** — see §14 |
| 2.3 | `fx_provider.py` + corrected London dates + `test_fx_provider.py` | **done 2026-11-17** — see §15 |
| 2.4 | `fx_pipeline.py` + config + `test_fx_pipeline.py` | **done 2026-11-17** — see §16 |
| 2.5 | `nav_estimator` integration behind `FA_FX_PIPELINE` + live smoke + docs | **done 2026-11-18** — see §17 |

Each gate runs `python -m compileall -q fa tests` plus the full suite (511 tests
after 2.5); 2.5 additionally runs the 000369 CLI estimate and the real-data
smoke script.

---

## 12. Open questions for review

1. **Fix the legacy date mapping?** `fetch_fx_series()` currently labels bars one
   day early in BST (F-FX-01). Re-implementing it on `fetch_fx_bars` fixes this
   silently for existing callers. That changes one observation per summer
   estimate; the alternative is to leave the façade on the old path and accept a
   known-wrong date. Recommendation: fix, with the smoke test showing the
   before/after estimate.
2. **Write today's running bar at all?** The pipeline writes what the vendor
   returns; a later fetch of the same date may then create a revision row. The
   alternative is to skip bars whose London day has not ended, which keeps the
   ledger quieter but loses the intraday estimate's FX leg until midnight.
   Recommendation: write it (PIT already excludes it), and let the revision
   ledger record the settlement.

**Both confirmed by the lead on 2026-11-17** — fix the façade's BST date
mapping (keeping its signature and return type), and write the running bar;
`available_at` alone keeps it out of PIT results.

---

## 13. Stage 2.1 status — delivered (2026-11-17)

Migration 003 is in `fa/data/database/schema.py` (`SCHEMA_VERSION = 3`,
`MIGRATION_003`, appended to `MIGRATIONS`); it is one `CREATE TABLE` plus two
`CREATE INDEX` statements and nothing else, so the existing atomic runner
(`BEGIN IMMEDIATE` … `COMMIT`, `_split_statements`) applies it unchanged and no
`MIGRATION_COLUMNS` entry is needed.

Delivered exactly as specified in §3.2: `fx_revisions` with the twelve columns
in the documented order, `idx_fx_revisions_currency_date` and
`idx_fx_revisions_replaced_at`, no back-fill, no change to any existing column
or index.

`tests/test_migration_003.py` (11 tests) upgrades a **copy** of the frozen
V2.2.3 baseline from version 2 to version 3 and asserts:

| Test | Assertion |
|---|---|
| `test_version_moves_from_2_to_3` | pre-state is 2, post-state is 3 |
| `test_fx_revisions_table_has_the_documented_columns` | column names and order match §3.2 |
| `test_both_indexes_exist` | both indexes present, and exactly those two indexes are new |
| `test_ledger_starts_empty` | `COUNT(*) = 0` |
| `test_every_legacy_row_count_is_unchanged` | all twelve legacy tables keep their counts |
| `test_price_estimation_and_nav_data_are_unchanged` | `market_prices`/`estimation_runs` fingerprints and `official_nav` sum unchanged |
| `test_no_existing_column_or_table_changes` | `market_prices`/`fx_rates` columns identical; only `fx_revisions` is a new table |
| `test_rerun_is_idempotent` | `initialise()` twice returns 3, counts and empty ledger unchanged |
| `test_rollback_keeps_version_two_and_no_table` | injected failure ⇒ `DatabaseError`, version stays 2, table absent, retry lands it |
| `FreshDatabaseTests` ×2 | a fresh database reaches 3 with the same columns/indexes; a revision row round-trips with `base_currency='CNY'` and `availability_source='unknown'` defaults |

The baseline file is hash-checked before and after every test (`tearDown`), so
the delivered database is provably byte-identical.

Verification: `python -m compileall -q fa tests fund_analyzer.py
fund_analyzer_gui.py` → 0; `python -m unittest discover -s tests -t .` →
**360 tests OK** (was 349; the 11 new ones are `tests/test_migration_003.py`).
`tests/test_migration_002.py` now asserts `schema_version == SCHEMA_VERSION`
instead of the literal `2`, because the two version assertions there are about
"002 landed", not "this is the newest schema".

Not started (deliberately): 2.2 repository/PIT work, 2.3 provider, 2.4 pipeline,
2.5 integration.

---

## 14. Stage 2.2 status — delivered (2026-11-17)

Scope honoured exactly: model + repository + PIT query + tests. No provider, no
pipeline, no façade change, no migration change (`SCHEMA_VERSION` stays 3).

**Model (`fa/models/market_data.py`).** `FxSeries.bars` is a property returning
`points`, so a "bar" carries the rate (`close`), its trading date
(`price_date`), the instant it describes (`market_timestamp`), when it became
knowable (`available_at` + `availability_source` + `availability_note`) and when
this copy was fetched (`fetched_at`) — one list, two names, no divergence.
`FxSeries.pit_bars` filters to bars with a real publication instant.
`FxSeries.from_bars()` builds a series from observations without inventing an
`available_at`, and refuses an empty / non-CNY `base_currency` or a non-positive
rate. `from_pairs()` is untouched, so V2.2 callers and the
`save_fx()`-rejects-non-CNY test keep their behaviour.

**Repository (`fa/data/database/repository.py`).** `save_fx(series) -> int` keeps
its signature and returns rows written. Before the upsert it calls
`_record_fx_revisions()`, which copies any superseded row into `fx_revisions`
*inside the same transaction*; the copy is of the stored row (its own
`market_timestamp`, `available_at`, `availability_source`, `source`,
`fetched_at`), never of the incoming bar. Trigger fields, computed by
`_fx_revision_fields()`:

| Situation | Ledger? |
|---|---|
| new trading date | no (insert) |
| same date, `|new − old| <= PRICE_REVISION_TOLERANCE` (1e-8) | no; only `fetched_at` refreshes |
| same date, materially different rate | yes — `reason = "fx revision replaced rate"` |
| known `market_timestamp` replaced by a different known instant | yes — `rate, market_timestamp` |
| `available_at` / `availability_source` / note filled in later | no (enrichment) |
| vendor stops reporting a date | no — the stored row is kept |

`fx_as_of(currency, as_of, start=None, end=None)` is a pure instant query
(revised after review, see §14.1): a row is visible when
`available_at IS NOT NULL AND availability_source IN ('observed','vendor_timestamp','inferred') AND julianday(available_at) <= julianday(:as_of)`.
It rejects a naive or date-only `as_of`. `rate_date` is *not* a visibility
filter — it is a London trading date, and comparing it with the caller's local
date hid data that was already public. `start`/`end` remain an optional window
on the trading date for callers that ask for one. `load_fx()` is unchanged in
behaviour (legacy `unknown` rows remain readable) and now shares
`_fx_point_from_row()` with the PIT path. `count_fx_revisions()` and
`last_fx_date()` support pipeline reporting without changing `save_fx`'s return
type. `PRICE_REVISION_TOLERANCE` lives in `fa/config.py` (env
`FA_PRICE_REVISION_TOLERANCE`), next to a declared-but-unused `PRICE_JUMP_WARN`
for stage 2.4.

**Tests.** `tests/test_fx_repository.py` — 31 tests in five classes:
`FxBarModelTests` (five fields survive, `pit_bars`, ambiguous base rejected,
non-positive rate rejected, `from_pairs` unchanged), `FxPersistenceTests`
(round trip, no revision for new rows, identical rate refreshes only
`fetched_at`, changed rate ledged, ledger keeps the stored row's own timestamps,
tolerance boundary below/above, vendor-dropped date retained, missing time never
fabricated, enrichment not ledged, base rejection writes nothing, empty series
no-op), `FxPointInTimeTests` (before/at/after publication, NULL invisible but
`load_fx` visible, inferred bar visible only after the London-midnight
`available_at`, naive/date/empty-currency rejection, `start`/`end` window) and
`FxPointInTimeTimezoneTests` (§14.1: one instant written as UTC / Asia/Shanghai
/ America/Los_Angeles / Europe/London yields the same rows at every boundary;
the reviewer's `-08:00` counterexample is now visible; `unknown` and illegal
sources are excluded while all three usable sources are included).

Verification: `python -m compileall -q fa tests fund_analyzer.py
fund_analyzer_gui.py` → 0; `python -m unittest discover -s tests -t .` →
**391 tests OK** (was 385); `git diff --check` clean.

Unresolved / deliberately deferred: `available_at` is still not populated by
any fetcher (2.3/2.4), `fx_rates` still has 0 production rows (nothing wires
`save_fx` yet — 2.5).

### 14.1 Review fixes on top of Stage 2.2 (2026-11-17)

Two PIT defects found in review, plus the tests that pin them:

1. **`rate_date <= date(as_of)` removed.** The reviewer's counterexample:
   `as_of = 2026-11-16T23:30-08:00` is `2026-11-17T07:30Z`, i.e. *after* the
   `2026-11-17` bar became available at `00:30Z`, yet `as_of.date()` is the
   16th, so the old filter dropped a bar that was already public. Visibility is
   now decided by `available_at` alone, which is exactly what D-1 computes.
2. **Source whitelist.** `availability_source <> 'unknown'` became
   `IN ('observed','vendor_timestamp','inferred')` (`PIT_AVAILABILITY_SOURCES`
   in `repository.py`): a typo or a future source value must not become
   PIT-visible merely by not being the word "unknown".
3. **`julianday()` comparison.** `available_at <= ?` compared ISO text; two
   formats that both serialise correctly (`…00Z` vs `…00.5Z`, or a hand-written
   `+08:00`) can sort in the wrong order as strings. `julianday()` parses the
   offset and fractional seconds, so the earlier "unresolved" string-comparison
   caveat is closed.

   Known limitation, accepted by review: `julianday()` is a floating-point
   Julian Day, so its absolute resolution is roughly 10 µs. FX data arrives at
   minute/second granularity (bar stamps are whole seconds, `available_at` is a
   whole minute), where the ordering is exact. If a future stage ever needs
   sub-microsecond publication boundaries, the comparison must move to integer
   epoch microseconds (`unixepoch(x, 'subsec')`) or a stored epoch column —
   do not assume the current form is exact at that resolution.

Tests added (6): `test_same_instant_in_every_timezone_gives_the_same_result`,
`test_timezone_equivalence_at_every_boundary` (before/at/after both publication
instants × three zones), `test_caller_local_date_behind_london_does_not_hide_public_data`
(the counterexample, also asserted in UTC),
`test_unknown_source_with_an_available_at_is_excluded`,
`test_illegal_source_is_excluded`, `test_whitelist_keeps_every_usable_source`
(rows written by direct SQL, because the model cannot express an illegal
source).

---

## 15. Stage 2.3 status — delivered (2026-11-17)

**Scope actually built.** FX provider only: download, conversion to London
trading dates, validation and error reporting. No SQLite, no retries, no
persistence decision, no change to the NAV estimator, the pipeline, the GUI,
the CLI, the Repository or the migrations.

Files:

* `fa/data/fx_provider.py` — new. `FxBar`, `FxFetch`, `bar_trading_date()`,
  `london_offset()` (+ EU-rule fallback), `infer_availability()`,
  `fetch_fx_bars()`, `fetch_fx_bars_many()`, `FX_QUOTE_CONVENTIONS`,
  `FX_SANITY_BANDS`.
* `fa/data/fx.py` — now the V2.2 facade over the provider. `fetch_fx_series()`
  and `fetch_fx_rates()` keep their signatures and return pandas series, but the
  index is the **London** trading date (F-FX-01 fixed). New
  `fx_series_map_from_fetch()` keeps `market_timestamp`, `available_at`
  (D-1, `inferred`), `availability_note` and `fetched_at`; `fx_series_map()`
  and `rate_on()` are unchanged for existing callers.
* `fa/config.py` — `FX_TIMEZONE` (env `FA_FX_TIMEZONE`, default
  `Europe/London`) and `FX_AVAILABILITY_LAG_MINUTES` (env
  `FA_FX_AVAILABILITY_LAG_MINUTES`, default 30).
* `tests/test_fx_provider.py` — 54 tests, mock HTTP only: London date mapping
  at the GMT/BST boundaries, `_eu_rule_offset` agreeing with `ZoneInfo` for
  every day 2025–2030, the request window covering the first/last London day in
  winter, summer and both switch days, the fallback's `_london_midnight_utc`
  agreeing with `ZoneInfo` for every day 2025–2030 (including the 23-hour and
  25-hour days), `infer_availability` in winter, summer and on the switch days,
  `london_today()` following London rather than UTC, bar parsing, window
  filtering, duplicate handling (keep last, counted), per-bar corruption,
  direction suspicion without transformation, non-CNY `meta.currency`,
  HTTP/timeout/network/JSON failures, CNY and unsupported-currency no-ops,
  facade signatures and default window, and `fx_series_map_from_fetch()`
  provenance.
* `scripts/smoke_fx_provider.py` — live smoke (separate from the suite, needs
  the network): per-pair bar count, span, last London date/rate, direction,
  `meta.currency`, inferred `usable_at`, and a final
  `pairs=10 failures=0` line.

**Validation order** in `fetch_fx_bars()`: empty → `missing-currency`; CNY →
explicit no-op (never a 1.0 series); symbol not audited →
`unsupported-currency`; download error (`timeout`, `network-error: …`,
`http-N`, `invalid-json`, `no-result: …`); `meta.currency != CNY` →
`non-cny-quote`; no bars → `no-bars`; outside `FX_SANITY_BANDS` →
`quote-direction-suspect` with the values **kept unchanged** (never
reciprocated). Bars with non-finite or non-positive closes are dropped per bar
so one corrupt value cannot discard the payload; duplicates keep the last
observation of the day.

**Live evidence** (2026-11-17, two-year window `2024-11-17 .. 2026-11-17`, all
10 pairs `OK` in two runs): 517 bars each, 729-day span, last bar
`2026-11-17`, `quote=CNY`, `usable_at=2026-11-18T00:30Z`, and
`pairs=10 failures=0`. Rates (USD ≈ 6.65, HKD ≈ 0.8494, CHF ≈ 7.93, GBP ≈
8.797, EUR ≈ 7.328, JPY ≈ 0.04183, CAD ≈ 4.65, AUD ≈ 4.48, SGD ≈ 5.174,
TWD ≈ 0.2019) were inside their bands on every run; the decimals shift between
runs because the last bar is live, which is exactly why D-1 keeps it out of
point-in-time reads. Each pair warned `dropped-bars: 5`; inspection showed these
are genuine market holidays where Yahoo returns a row with a null close
(2025-01-01, 2025-04-18, 2025-04-21, 2025-12-25, 2026-01-01) — holidays, not
lost data.

**Verification.** `python -m compileall -q fa tests scripts fund_analyzer.py
fund_analyzer_gui.py` → 0; `python -m unittest discover -s tests -t .` →
**445 tests OK** (391 + 54); `git diff --check` clean.

**Unresolved / deliberately deferred.** Nothing here writes to SQLite:
`fx_rates` still has 0 production rows, and `available_at` reaches only callers
that use `fx_series_map_from_fetch()`. Wiring the download → persistence →
valuation path (`update_fx`, cache, overlap window) is Stage 2.4; the NAV
estimator integration and the 000369 before/after evidence are Stage 2.5. The
provider does not retry: a transient network failure surfaces as an `error`
string and is the pipeline's decision.

### 15.1 Review fixes on top of Stage 2.3 (2026-11-17)

Three defects found in review, all real:

1. **The request window used UTC midnights (P1).** `_http_chart()` built
   `period1`/`period2` from `00:00Z` while the provider's trading day is
   London's. In BST the bar of day *D* is stamped `23:00Z` of *D−1*, i.e.
   **before** a UTC-midnight `period1`, so Yahoo could omit the first bar the
   caller asked for (and `period2` cut the last one). Fixed by deriving both
   bounds from `_london_midnight_utc()`: `period1 = London midnight of start`,
   `period2 = London midnight of end + 1 day` (still exclusive). Tests:
   winter/summer windows, both switch days, and a full-year loop asserting
   every London day is at least 23 h long. Live check (2026-11-17): windows
   starting on `2026-07-01` (BST), `2026-03-27`, `2026-10-22` and `2026-01-05`
   all returned the first *and* last London day.
2. **The DST fallback midnight was off by an hour (P1).** The no-tzdata path
   subtracted the UTC offset measured at **noon**, which differs from the offset
   at midnight on the two switch days:
   `2026-03-29` gave `23:00Z` on the 28th instead of `2026-03-29 00:00Z`, and
   `2026-10-25` gave `00:00Z` instead of `2026-10-24 23:00Z`. `infer_availability()`
   inherited the error whenever the next London day was a switch day. Fixed by
   solving the fixed point `midnight_utc = 00:00Z − offset(midnight_utc)`
   (one refinement step). Tests: fallback vs `ZoneInfo` for **every** day
   2025–2030, both switch days, a normal BST day, and
   `infer_availability()` on the day before each switch.
3. **The facade's default `end` used the UTC date (P2).** Between `23:00Z` and
   midnight London is already on the next date, so `fetch_fx_series()` asked for
   a window one day short. Added `london_today()` (ZoneInfo, EU-rule fallback)
   and made `_window()` default to it; `_window()` now also accepts an injected
   `today` so the behaviour is testable without freezing the clock. Tests: the
   default end equals London's date, an explicit `end` still wins, and
   `fetch_fx_series()` forwards `(2026-05-23, 2026-07-02)` for `1mo` when
   London's today is `2026-07-02`.

Additionally, the review package was missing the root entry scripts, so the
documented `compileall` command could not be reproduced from the archive.
`scripts/package_project.py` now includes `fund_analyzer.py`,
`fund_analyzer_gui.py`, `requirements.txt`, `yf_helper.py`, `install.bat`,
`install.sh`, `launch.vbs` and `LICENSE` in review mode. The spec header no
longer says "proposed"; it records that 2.1–2.3 are delivered and 2.4–2.5 are
pending.

Verification after the fixes: `compileall` → 0 (including the root scripts);
`unittest discover` → **445 tests OK**; `pytest -q` (Python 3.11.16, pytest
9.2.0) → **445 passed, 35 subtests passed in 18.73s**; `git diff --check`
clean; live smoke → `pairs=10 failures=0`; live 000369 run completed
(`run=57ad229a`, coverage 46.1 %). The 000369 estimate moved from −0.05 % to
−0.15 %, but the *base NAV itself changed* between the two runs (2.8240 →
2.8170, i.e. a newer official NAV was published), so that difference is not
attributable to the window fix; the isolated evidence for the fix is the
live window probe above.

## 16. Stage 2.4 status — delivered (2026-11-17)

### 16.1 What was delivered

| File | Change |
|---|---|
| `fa/data/fx_pipeline.py` | **new** — `FxRequest`, `FxUpdateOutcome`, `update_fx()`, `previous_fx_session()`, `next_fx_session()`, `availability_note()` |
| `fa/config.py` | `FX_OVERLAP_SESSIONS` (`FA_FX_OVERLAP_SESSIONS`, 5), `FX_OVERLAP_CALENDAR_DAYS` (`FA_FX_OVERLAP_CALENDAR_DAYS`, 12), `FX_DEFAULT_YEARS` (`FA_FX_DEFAULT_YEARS`, 2) |
| `tests/test_fx_pipeline.py` | **new** — 58 tests: six behaviour classes, legacy-metadata enrichment, failure reporting, request/contract tests |

`fa/data/database/repository.py`, `fa/data/database/schema.py`,
`fa/models/market_data.py`, `fa/data/fx_provider.py`, `fa/data/fx.py`,
`fa/analysis/nav_estimator.py` were **not touched** in this stage.

### 16.2 Behaviour, as implemented

* **Cache completeness is judged on the window edges** (`next_fx_session(start)`
  ≤ first stored, last stored ≥ `previous_fx_session(end)`), never on the newest
  date alone. An interior hole is allowed: Yahoo really does omit vendor
  holidays, and treating those as damage would re-download the full window on
  every run.
* **A cache hit also requires publication metadata.** Stage 2.2 keeps legacy rows
  with `available_at = NULL` and `availability_source = unknown` readable through
  `load_fx()`, but those rows never enter `fx_as_of()`. When the dates cover the
  window and the times are missing, the pipeline rebuilds the bars from the
  stored rates and the known London-date rule, writes them through the
  repository's enrichment path (rate untouched, no revision), counts them as
  `updated`, and reports `metadata-enriched` — it does not download two years of
  history just to fill timestamps.
* **History gap ⇒ fetch from `request.start`; tail gap ⇒ fetch the overlap**
  (at least `FX_OVERLAP_SESSIONS`=5 trading days and at least
  `FX_OVERLAP_CALENDAR_DAYS`=12 calendar days — whichever reaches further back);
  `prefer_cache=False` ⇒ fetch the
  whole requested window. Cache hit ⇒ zero downloads, zero writes, zero
  revisions.
* **Validation is independent of the provider**: NaN/inf/≤0 dropped, dates
  outside `[start, end]` dropped, duplicate dates keep the latest
  `market_timestamp`, jumps above `PRICE_JUMP_WARN` kept and flagged, rates
  outside `FX_SANITY_BANDS` kept and flagged — unless *every* bar is inside the
  inverted band, in which case the series is rejected as `inverted-quote` and
  nothing is written. An inverted series is never reciprocated.
* **Every written bar carries the full time model**: provider `market_timestamp`,
  `available_at = infer_availability(rate_date)` (next London midnight + 30 min),
  `availability_source = inferred`, the explanatory `availability_note`, and one
  shared `fetched_at` per batch (injectable `now`, never per-bar `datetime.now()`).
* **Nothing is written before the whole series validates**; per-currency
  isolation means a timeout, an unsupported code or a database error only lands
  in `skipped`. No retries in this version, by decision (D-16B).
* `inserted` counts new trading dates, `updated` counts existing dates touched
  by the upsert, and `revisions` is the *delta* of
  `count_fx_revisions()` around `save_fx()` — a re-download that only refreshes
  `fetched_at` is `updated`, not a revision.

### 16.3 Acceptance mapping (Stage 2.4 scope)

| ID | Evidence |
|---|---|
| FX-01 direction | live probe: USD 6.66…/HKD 0.849/CHF 7.94/GBP 8.79, all inside bands; inverted synthetic series rejected (`test_inverted_quote_rejects_the_whole_series`) |
| FX-02 full time fields | `FxPipelineAvailabilityTests` (7 tests); live probe `avail=517/517` for all four currencies |
| FX-03 no synthetic 1.0 | `test_cny_is_skipped_as_base_currency`; no CNY series is ever produced |
| FX-04 cache ⇒ no download | `test_complete_cache_makes_no_download`; live probe third call `downloads=0` |
| FX-05 overlap ≥5 sessions | `test_overlap_window_is_twelve_calendar_days`, `test_overlap_never_starts_before_the_request` |
| FX-06 revisions retained | `test_vendor_revision_creates_a_ledger_row`, `test_vendor_dropped_date_keeps_the_stored_row` |
| FX-07 idempotency | live probe second call: `inserted=0 updated=2068 revisions=0`; `test_identical_second_update_adds_no_revision`, `test_repeated_identical_request_keeps_the_row_count` |
| FX-08 PIT excludes the future | live probe with `now=2026-11-17T12:00Z`: `rows=517`, `pit=516` — the 2026-11-17 bar becomes available at `00:30Z` on the 18th |
| FX-09 legacy unknown readable, not PIT | `tests/test_fx_repository.py` (`unknown` invisible to `fx_as_of`, visible to `load_fx`) — unchanged by this stage |
| FX-10 V2.2 API unchanged | `fa/data/fx.py` untouched; `save_fx`/`load_fx`/`fx_as_of` untouched; suite 445 → 503 green |

### 16.4 Verification

| Command | Result |
|---|---|
| `python -m unittest tests.test_fx_pipeline` | **58 tests OK** (standalone) |
| `python -m unittest discover -s tests -t .` | **503 tests OK** |
| `python -m pytest -q` (3.11.16 / pytest 9.2.0) | **503 passed, 35 subtests passed in 45.51s** |
| `python -m compileall -q fa tests fund_analyzer.py fund_analyzer_gui.py` | 0 |
| `git diff --check` | clean |

Live end-to-end probe (throwaway database, real Yahoo, 2 years, USD/HKD/CHF/GBP,
`now = 2026-11-17T12:00Z`): first call `downloads=4 inserted=2068 revisions=0
skipped={}`, second call with `prefer_cache=False` `inserted=0 updated=2068
revisions=0`, third call `downloads=0` (cache hit); per currency `rows=517
avail=517 pit=516`, `base=CNY`, first `2024-11-18`, last `2026-11-17`;
`fx_as_of(2026-05-01T…)` returned 374 rows ending `2026-04-30`. The production
database `data/database/fund_analyzer.db` was **not** written to by this stage
(`fx_rates` still 0 rows).

### 16.5 Deliberate decisions and reported gaps

* **D-16A cache completeness** — window edges + session boundary, interior
  holiday gaps allowed (as recommended in the Stage 2.4 plan §十六 A).
* **D-16B no retry** — the provider stays single-shot; failures are reported,
  never retried (plan §十六 B). Revisit only with measured failure rates.
* No cache-file layer beyond SQLite: `prefer_cache=True` means "the database is
  the cache"; a separate on-disk FX cache was not introduced.
* `available_at` is inferred, never observed — a Yahoo publication timestamp
  does not exist.
* The production `fx_rates` table was still empty at the end of Stage 2.4
  because nothing called `update_fx()` in the application yet; Stage 2.5 wired
  it (see §17) and the table is now populated.
* `FxUpdateOutcome.warnings` duplicates some anomaly information in prose; both
  are kept on purpose (structured for tests, prose for logs).

### 16.6 Review fixes on top of Stage 2.4 (2026-11-17)

Three functional defects and three smaller issues were found in the packaged
review. Nothing here changes the architecture, the schema, the repository or
the provider.

| Issue | Fix |
|---|---|
| **P1 — legacy rows could be a cache hit** | `_has_complete_availability()` joins the edge test: a row counts as complete only when `available_at` is set *and* the source is `observed`/`vendor_timestamp`/`inferred`. A date-complete but metadata-poor series goes through `_enrich_availability()`, which copies every rate and stock stamp verbatim and fills the times from `infer_availability(rate_date)`, then writes through the repository's enrichment upsert. Rates never change, no revision is created, and the next run is a genuine cache hit. |
| **P1 — counters moved before the write** | `count_fx_revisions → save_fx → count_fx_revisions` are one `try`, and `inserted`/`updated`/`revisions` are added only after it returns. A `DatabaseError` now skips the currency with zero phantom rows in the outcome. |
| **P1 — revision counting outside isolation** | Both `count_fx_revisions()` calls sit inside the per-currency `try/except`, so a revision-count failure skips that currency instead of aborting the whole multi-currency update. |
| **P2 — provider diagnostics dropped** | `FxFetch.warnings` are prefixed with the currency and appended to `outcome.warnings`; `FxFetch.duplicates` becomes an anomaly `kind="provider-duplicate-date"` (with `count`, `rate_date=None`) plus a prose warning. The pipeline's own duplicate finding keeps `kind="duplicate-date"` so the two counts are never confused. |
| **P2 — `FX_OVERLAP_SESSIONS` unused** | `_overlap_start()` now walks back `FX_OVERLAP_SESSIONS` trading days *and* `FX_OVERLAP_CALENDAR_DAYS` calendar days and keeps the earlier date, so both settings demonstrably affect the window. |
| **P2/P3 — `downloads` and per-currency logs** | `downloads` is incremented **before** the provider call, so it counts attempts (including raises). The log line now prints per-currency `inserted`/`updated`/`revisions` instead of running totals. |

Eleven tests were added (`FxPipelineLegacyMetadataTests` ×4,
`FxPipelineFailureReportingTests` ×5, overlap ×2), bringing
`tests/test_fx_pipeline.py` to **58** and the suite to **503**
(`unittest` 503 OK; `pytest -q` 503 passed, 35 subtests, 45.51 s).

---

## 17. Stage 2.5 status — delivered (2026-11-18)

Stage 2.5 wires the pipeline into the estimator behind a switch and makes the
FX leg of every estimate point-in-time (PIT). It changes no return formula, no
price alignment, no Shapley attribution and no schema.

### 17.1 What was delivered

| File | Change |
|---|---|
| `fa/config.py` | `FX_PIPELINE_ENABLED = os.environ.get("FA_FX_PIPELINE", "1").strip().lower() not in ("0", "false", "no", "off")` — the documented switch; default on. |
| `fa/analysis/nav_estimator.py` | `_pit_moment(as_of)` normalises the estimate instant (naive input is read as UTC and warns); `_resolve_fx(currencies, repository, *, start, end, as_of)` replaces the former direct `fetch_fx_rates()` call and returns `(fx_map, source, warnings)`; `estimate_qdii_nav()` records `fx_source` on the record and in `inputs`, and extends the warning list with the FX notes. |
| `tests/test_fx_integration.py` (new) | 8 tests: PIT excludes a bar published after `as_of`; the same bar appears once published; a pipeline failure never falls back to non-PIT rates (`fetch_fx_rates` asserted not called); `DatabaseError` falls back to the legacy download path; switch off → legacy; CNY-only → `none`; missing repository → legacy; `_pit_moment` timezone handling. |
| `tests/test_nav_estimator_v2.py`, `tests/test_qdii.py` | Estimator helpers now seed a deterministic USD series into the temporary repository and patch `update_fx`, so the suite stays offline (it previously reached Yahoo from those helpers). |
| `scripts/smoke_fx_pipeline.py` (new) | Real-data smoke: temp SQLite, first update / forced refresh / cached call, per-currency rows, availability, `fx_as_of` at two instants, revisions, `failures=` counter. |

### 17.2 Behaviour, as implemented

| Situation | `fx_source` | Behaviour |
|---|---|---|
| switch off (`FA_FX_PIPELINE=0`) | `legacy` | `fetch_fx_rates()` as before; no pipeline call, no PIT guarantee. |
| repository missing | `legacy` | Same, plus 「沒有資料庫可用，FX 改用舊下載路徑（無 PIT 保證）」. |
| `update_fx()` raises `DatabaseError` | `legacy` | Falls back to the downloader with 「FX pipeline 無法寫入資料庫（…），改用舊下載路徑（無 PIT 保證）」 — the database could not accept the write, so there is nothing to read back. |
| any other pipeline exception | `pipeline-pit` | Warning 「FX pipeline 失敗：…」 and **no legacy fallback**; the estimate reads whatever PIT rates are already stored, so an affected currency can end up as `missing-fx`. |
| PIT read fails for one currency | `pipeline-pit` | Per-currency warning 「…PIT 匯率讀取失敗」; the other currencies proceed. |
| currencies only CNY/empty | `none` | No FX series needed, no warning. |
| normal | `pipeline-pit` | `update_fx()` persists/refreshes the bars first, then every rate is read back through `repository.fx_as_of(currency, moment, start, end)`. |

* The series returned by `update_fx()` is deliberately **not** used: it may
  contain the still-running London bar, which is not yet published.
* A bar that exists in `fx_rates` but whose `available_at` is after `as_of` is
  counted and reported (`"USD: 1 筆匯率尚未公布，已按 PIT 排除"`), never used.
* A pipeline or PIT-query failure is reported and that currency yields no rate
  (`missing-fx` downstream) — there is no silent downgrade to non-PIT rates.

### 17.3 Live acceptance evidence

Reproduction conditions, stated so the run can be judged: the NAV input is the
**frozen** baseline CSV (`docs/development/v2.2.3_baseline/cli_run/000369/`,
ends `2026-11-13,2.824`); holdings are the 2026Q3 snapshot stored in the working
SQLite database (the same ten names and weights as the baseline); prices and FX
come from Yahoo at run time on 2026-11-18. Local returns in the two JSON files
agree, so the comparison isolates the FX leg; a fully hermetic re-run would need
the baseline price snapshot as well, which the working tree does not keep.

Smoke (`python scripts/smoke_fx_pipeline.py`, temp database):

```
first   downloads=4 inserted=260 updated=0 revisions=0 skipped={}
refresh downloads=4 inserted=0  updated=260 revisions=0
cached  downloads=0 series=['CHF','GBP','HKD','USD']
per currency: rows=65 avail=65 pit_now=64 pit_prev_day=63 revisions=0
currencies=4 failures=0
```

CLI, exactly as requested
(`python fund_analyzer.py 000369 --fund-type qdii --qdii-as-of 2026-11-17T00:39:20+08:00`):
exit 0, `[QDII] 000369 未產生估算：官方淨值已更新，無需估算`. Reason: the working
NAV CSV now already contains the 2026-11-16 official NAV, so
`resolve_valuation_dates()` reports `up-to-date` (`fa/analysis/valuation_dates.py:107-116`)
— there is nothing left to estimate at that instant. This is a data-advance
effect, not a Stage 2.5 defect; the Stage 2.4 baseline instant was produced when
that CSV ended at 2026-11-13.

The baseline instant was therefore reproduced against the frozen baseline CSV
(`docs/development/v2.2.3_baseline/cli_run/000369/`, NAV ends `2026-11-13,2.824`)
with the production repository, same `as_of`:

| Field | Stage 2.4 baseline | Stage 2.5 (PIT) |
|---|---|---|
| target / base NAV date | 2026-11-16 / 2026-11-13 | 2026-11-16 / 2026-11-13 |
| `fx_source` | (none — raw download) | `pipeline-pit` |
| estimated NAV | **2.8233** | **2.8273** |
| estimated return | **−0.02365 %** (−0.00023651) | **+0.11655 %** (+0.00116555) |
| **FX contribution** | **−0.13994 %** (−0.00139935) | **0.0** |
| local contribution | +0.11628 % (0.00116284) | +0.11655 % (0.00116555) |
| interaction contribution | 0.0 | 0.0 |
| coverage | 46.09 % | 46.09 % |

Values are read straight from the two JSON files
(`docs/development/v2.2.3_baseline/cli_run/000369/000369_qdii_estimate.json` and
`docs/development/stage2.5_acceptance/000369_qdii_estimate.json`). The baseline
total return is exactly `0.0011628384 − 0.0013993515 = −0.0002365131`, i.e.
2.8233 NAV. **The estimated NAV changes**, and both legs move:

* FX leg — the negative, not-yet-published return is removed:
  `0.00139935 × 2.824 ≈ 0.00395` of NAV.
* Local leg — it rises by `+0.0000027106372`, and that is **not** a rounding
  error: it is the Shapley interaction allocation. `fa/models/return_result.py:76`
  (`shapley_contributions`) splits each asset's CNY return as
  `C_local = w·r_local·(1 + r_fx/2)` and `C_fx = w·r_fx·(1 + r_local/2)`, so
  `C_local + C_fx == w·r_CNY` exactly. When the FX return is zeroed by PIT the
  `(1 + r_fx/2)` factor becomes 1, and the reallocated term is
  `−Σ w·r_local·r_fx/2` over the ten holdings. Two input sets must not be
  confused, because they differ in the last digits:

  | Inputs | `−Σ w·r_local·r_fx/2` | Agreement with the reported ΔC_local |
  |---|---|---|
  | In-run, full-precision weights and FX returns (the inputs that produced both JSONs) | `+0.0000027106372` | equals the reported difference `0.0011655490014208746 − 0.0011628383641824292 = +0.00000271063724` at the precision the JSON prints |
  | Weights and `fx_return` **as serialised in the JSON** (weights 4 dp, `fx_return` 6 dp) | `+0.0000027104827` | `+1.545e-10` below the reported difference |

  So `+0.0000027106` is the number the run itself produced, while
  `2.7104827e-6` is what a reader recomputing from the stored JSON will obtain;
  the `1.5e-10` gap is the rounding of the serialised weights and FX returns, not
  floating-point noise in the attribution. Per-asset local returns and weights
  agree between the two files at the JSON's precision.
* `interaction_contribution` is `0.0` in both runs because the cross term
  `w·r_local·r_fx` is already distributed inside the two Shapley contributions;
  it is not a third bucket that could be non-zero.

`ΔR = 0.0013993515 + 0.0000027106 = 0.0014020621`, times the base NAV `2.824`
gives `0.0039594`, i.e. `2.8233 → 2.8273`. That increase is the expected result
of the PIT fix, not a formula change.

Both estimates use the same ten holdings, the same target/base NAV dates, the
same market dates and the same per-asset local returns; only the holdings
provenance label differs (`eastmoney-page` in the baseline, `cache` in the
re-run) because the re-run read the snapshot from SQLite. Weights agree to the
JSON's float precision, so the local-contribution difference is entirely the
Shapley reallocation described above, not float noise and not a change in the
underlying returns.

The FX contribution collapses to zero because the 2026-11-16 London bar is only
inferred-available at 2026-11-17T00:30Z, i.e. after the estimate instant
2026-11-17T00:39:20+08:00 = 2026-11-16T16:39:20Z; the estimate used the
2026-11-13 rate under the staleness rule instead (per-holding status
`stale-fx`).

Database state after the acceptance run (production `data/database/fund_analyzer.db`,
backed up beforehand to `%TEMP%\dsh-9XKplT\fa_pre_stage25.db`): `fx_rates` holds
10 rows for each of USD/CHF/GBP (all `inferred`, e.g. USD 2026-11-12 6.670300,
2026-11-13 6.670400, 2026-11-16 6.671000, `available_at` 2026-11-13/14/17T00:30Z),
`fx_revisions` 0, `fx_as_of("USD", 2026-11-17T00:39:20+08:00)` → 9 rows ending
2026-11-13, and `000369_qdii_estimate.json` (produced in the acceptance
directory) records `fx_source="pipeline-pit"` and `fx_contribution=0.0`.

### 17.4 Reported gaps

* Stage 2.5 was implemented from the work order in this repository because the
  packaging described in the request (`fund_analyzer_v2.3_stage2.5_review.zip`,
  `scripts/smoke_fx_pipeline.py`, `tests/test_fx_integration.py`) did not exist
  in this workspace; the test counts reported here (511) are this tree's, not
  another environment's.
* `available_at` remains `inferred` — Yahoo publishes no FX timestamp.
* The plain CLI command cannot reproduce the baseline instant any more because
  the working NAV data advanced; the baseline CSV is the reproducible pin.
* The switch is read at import time (`fa.config`), so a process must be
  restarted to change `FA_FX_PIPELINE`.
* **GUI test environment difference (recorded, test kept).** In this workspace's
  Python 3.11.16 / pytest 9.2.0 environment, `python -m pytest -q` reports
  `510 passed, 35 subtests, 1 failed`: `test_gui.py::…test_analysis_result_is_marshalled_to_main_thread`
  fails because the second `tk.Tk()` in that process raises
  `_tkinter.TclError: invalid command name "tcl_findLibrary"`, while `Tk()`
  works from a standalone script and the same test passes under
  `python -m unittest` on Python 3.10. The reviewer's Python 3.13 /
  pytest 9.0.2 environment did not reproduce it. It is therefore recorded as an
  environment-specific Tk re-initialisation issue, not a code regression; the
  test is retained rather than skipped or deleted.
* **External re-run (reviewer's environment, for the record).** Python 3.13,
  pytest 9.0.2, temporary import-only `akshare` shim: `unittest` 511 tests
  (8 skipped), `pytest` 503 passed / 8 skipped / 35 subtests, `compileall` 0.
  Those numbers come from that environment and do not replace the 511-test
  `unittest` run on Python 3.10 recorded in §17.3.

