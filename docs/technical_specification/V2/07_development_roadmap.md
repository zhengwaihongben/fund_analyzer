# V2 — Development Roadmap

Five phases, ordered by dependency rather than by document number.

| Phase | Name | Main work | Status |
|-------|------|-----------|--------|
| V2.0 | Technical Foundation | models, architecture, specification | **done** (docs + `fa/models` + `fa/utils/data_quality.py`) |
| V2.1 | Data Infrastructure | SQLite, history, calendars, price alignment, data quality | **done** (schema + repository + 4 calendars + `price_alignment`) |
| V2.2 | Multi-Asset Valuation | return calculators, portfolio valuation, Shapley | **done** (`fa/analysis/returns`, `fa/analysis/valuation`, orchestrator, Shapley) |
| V2.3 | Backtesting & Validation | historical backtests, error analysis, model versions | planned |
| V2.4 | GUI & Integration | estimate GUI, backtest GUI, reports, acceptance | planned |

## V2.0 — Technical Foundation

Delivered:

* `fa/models/`: `Asset`/`AssetType`, `HoldingSnapshot`, `PricePoint`/
  `PriceSeries`/`FxSeries`, `AssetReturnResult`/`PortfolioReturnResult`,
  `EstimationRun`/`EstimationResult`, `MODEL_VERSION`.
* `fa/utils/data_quality.py`: coverage-vs-quality separation, grades.
* `docs/technical_specification/V2/00–07`.
* Directory skeleton `data/{database,cache,exports}`, `output/{reports,charts}`.
* Unit tests for model validation and quality grading.

Exit criteria: every field/interface in the spec exists in code, models
validate input, and the docs and code agree.

## V2.1 — Data Infrastructure

Work:

1. `fa/data/database/`: schema, migrations, repository for funds, assets,
   holdings snapshots, market prices, FX, official NAV, estimation runs and
   backtest results.
2. Holdings snapshots with disclosure metadata: persist whatever quarter is
   obtainable, and support point-in-time selection over the history that exists
   (no requirement to download a complete quarterly series).
3. Multi-market calendars (`fa/utils/calendars/`: CN, US, HK, JP) with
   `market_close_datetime`.
4. Price alignment in market sessions + staleness policy (warn / exclude).
5. Data quality checks wired into the estimate record.
6. Immutable estimation snapshots (`estimation_inputs.payload_json`).

Delivered:

* `fa/data/database/{schema,connection,repository}.py` — 11 tables +
  `schema_version`, versioned migrations, repository round-trips, and a
  `DuplicateRunError` that makes a stored run immutable.
* `fa/utils/calendars/{base,china,us,hong_kong,japan}.py` — rule-based
  calendars (weekends + holiday tables for 2025–2026) with tz-aware close
  datetimes, DST for the US, and an explicit weekend-only fallback.
* `fa/analysis/price_alignment.py` — `align_price` / `align_fx` implementing
  staleness policy B (`FA_STALE_SESSIONS_WARN` = 2, `FA_STALE_SESSIONS_MAX` = 10).
* `tests/test_database.py`, `tests/test_calendars.py`,
  `tests/test_price_alignment.py` (47 tests).

Scope decision after the V2.1 review (history is optional, not a prerequisite):

* The Eastmoney archive endpoint (`fundf10.eastmoney.com/FundArchivesDatas.aspx`),
  the only source of per-quarter history, is unreachable from the reference
  machine: the TCP connection succeeds but the TLS handshake is killed at
  ~5.5 s (`SSLEOFError`, `WRONG_VERSION_NUMBER`) while sibling hosts such as
  `fund.eastmoney.com` work normally. Proxy, AKShare and request code were all
  ruled out.
* Therefore V2.1 **does not** promise automatic complete-history download. What
  it delivers: the latest obtainable quarter is fetched (one home-page request),
  every fetched quarter is merged into the local CSV cache and written to SQLite,
  and point-in-time selection works over whatever history exists locally.
* `fetch_qdii_holdings(fund_code, save_dir, use_cache=True, repository=None)`
  is latest-only: fresh cache → fund home page (`eastmoney-page`) → SQLite
  (`database`) → stale cache (`cache-stale`) → empty. It never retries the
  archive API, so a broken endpoint costs one request instead of a dozen.
* `estimate_qdii_nav()` picks the newest snapshot disclosed on or before the
  target date; when no local quarter qualifies it returns `insufficient-data`
  and says that older snapshots must be imported, instead of fetching history.
* Records expose `holdings_scope` (`top10` / `full_portfolio`) and
  `history_available` (more than one quarter known locally) so "only the top ten
  today" and "no historical snapshot" stay distinguishable. The old
  `history_complete` / `years_requested` fields are gone, together with
  `history_years()`, `is_history_complete()`, `_load_quarters()`,
  `_merge_quarters()`, `QDII_HISTORY_SUFFIX` and the
  `FA_QDII_HISTORY_MIN_QUARTERS` / `FA_QDII_HISTORY_YEARS` settings.
* Per-year archive cooldown and the "years derived from the NAV start date"
  logic were part of that abandoned flow; the standard-fund path in
  `fa/data/holdings.py` keeps its own retry/cooldown behaviour.
* Network diagnostics stay: `fa/utils/net.py::classify_network_error()` /
  `describe_network_error()` classify TLS / connection / timeout / DNS failures
  so a log line explains itself. The one-off script
  `test_eastmoney_connection.py` was deleted; its method is recorded here instead
  (print proxy env, DNS answers, a raw TLS handshake, `requests` with and without
  the environment proxy, then the raw AKShare call).
* `save_quarters_to_db(..., source=...)` stores the real origin
  (`eastmoney-page` / `eastmoney-archives`) rather than hard-coding the archive
  source, and a snapshot that came from the database is not written back.
  AKShare is pinned to `>=1.18.88,<1.19.0` in `requirements.txt`.
* Tests: latest fetch → cache → SQLite save, stale-cache fallback, database
  fallback when the page fails, no-source emptiness, and point-in-time selection
  against both the CSV cache and the SQLite repository.
* Consequences for later phases: V2.3 backtesting is limited to the history that
  has actually accumulated locally (it needs several quarters before PIT
  estimates of earlier dates become possible); importing old quarters from
  another source remains a supported, manual path.

Exit criteria: schema + migration + repository round-trip tested; a stored run
is reproducible; calendars and staleness behave per spec; the latest obtainable
quarter is fetched, cached, stored in SQLite and selectable point-in-time, with
no dependency on the unreachable archive API.

## V2.2 — Multi-Asset Valuation

Work: `fa/analysis/returns/{base,equity,etf,bond_etf,cash,fx}.py`,
`fa/analysis/valuation/portfolio_valuation.py` (weighting, coverage, Shapley),
and a slim orchestrator that replaces the v1 maths inside `nav_estimator.py`
without breaking its public JSON output.

Exit criteria: all four asset classes valued; attribution sums exactly;
coverage/status correct; integration test end-to-end.

Delivered:

* four independent calculators behind one `ReturnCalculator` protocol, each
  returning the same `AssetReturnResult` (equity, ETF, bond ETF, cash) plus the
  FX return calculator in `returns/fx.py` (distinct from the `fa/data/fx.py`
  downloader);
* `value_portfolio()` with non-renormalised weights, `coverage_ratio` vs
  `coverage_of_known`, uncovered weight and the three portfolio statuses;
* Shapley attribution (`C_local = w·r_local(1+r_fx/2)`, `C_fx = w·r_fx(1+r_local/2)`)
  replacing V1's interaction term, with `renormalised_error()` exposed so the
  identity can be asserted instead of trusted;
* `nav_estimator.py` is now coordination only (no FX maths, no portfolio maths),
  persists `data_quality`/`quality_score` per run, and still emits the legacy
  JSON contract (`estimated_nav`, `coverage_ratio`, `local_contribution`, …);
* unified staleness policy (0 / 1–2 kept / 3–10 excluded / >10 severe) in
  `fa/config.py` + `fa/analysis/price_alignment.py`;
* `MODEL_VERSION` bumped to `2.2.0`; new tests `test_return_calculators.py`,
  `test_portfolio_valuation.py`, `test_shapley.py`, `test_nav_estimator_v2.py`.

### Corrections applied after the V2.2.1 review

The review's verdict was that the structure and maths were right but the
estimate was not yet safe to call complete. What changed:

* **Stale data reaches quality.** `priced_return()` / `convert_to_cny()` now
  return `stale-*` instead of `ok`, and `ReturnStatus.is_usable` separates
  "nothing to report" from "usable but imperfect". Coverage, contributions,
  `value_portfolio()` and `assess_quality()` gate on `is_usable`, so a 1–2
  session lag lowers the score instead of vanishing.
* **Assets are classified, not assumed to be equities** —
  `fa/data/asset_classifier.py` (manual override → declared type → name
  evidence → conservative `unknown`). Price data is never evidence of type.
* **Currency consistency is enforced** — `resolve_currency()` rejects a
  declared/quote mismatch with `currency-mismatch`, and a `FxSeries` for another
  currency is rejected instead of being used.
* **Reproducible inputs** — every run stores a complete input snapshot
  (per-asset base/target prices and dates, FX rates and dates, `price_type_used`,
  calendar + fallback flag, cash rate/source/days, model version, base NAV) in
  `_qdii_estimate_inputs.json`, keyed by `run_id`, and the same payload goes to
  `estimation_inputs` in SQLite.
* **`run_id` is the identity** — the history CSV keeps one row per run, so two
  estimates for the same target date both survive.
* **Look-ahead is a status, not a boolean** (`LookAheadStatus`), so an
  estimated disclosure date is distinguishable from a verified filing date.
* **Exchange normalisation** (`NasdaqGS`/`NMS`/`NYQ` → US, `SIX Swiss Exchange`
  → CH, `LSE` → GB, …) with a `calendar_fallback` flag that costs quality points.
* **Date-range market data** (`start = base_date − 10 days`, `end = target_date`),
  database first, then Yahoo, then persisted back.
* **Input validation**: `PricePoint` rejects non-finite/non-positive prices,
  `FxSeries` rates must be positive, cash `interest_rate` must be within
  −0.2…1.0, and a non-positive cash holding period is `invalid-input`.
* **Storage moved out of the analysis layer** into
  `fa/data/estimation_storage.py`; `nav_estimator.py` no longer writes files.

**Price-type provenance split.** `PricePoint` now carries
`available_price_type` (what the source offered) and `used_price_type` (what the
calculator took), with `price_type` kept as a compatibility alias and
`with_used_price_type()` for recording the choice. The input snapshot stores both
per asset, so "the vendor only had a raw close" is distinguishable from "the
vendor had an adjusted close and we used the raw one".

**Testing environment recorded.** The README states the verified environment
(Python 3.10, 293 tests, `compileall`) and warns that test modules importing
`akshare` cannot be imported without the project dependencies.

## V2.2.2 — Reliability patch (after the second V2.2 review)

Six findings, all fixed in this round (tests: 293):

1. **No more ticker-shape classification.** `classify_asset_type` no longer maps
   a "looks like a stock code" ticker to `equity`; the order is manual override →
   source-declared type → formal-name markers → `unknown`. The QDII builder
   declares `equity` only because the source table is 「股票投资明细」; any other
   table leaves the type blank and the holding stays `unknown`.
2. **FX quote convention validated.** `convert_to_cny()` rejects a `FxSeries`
   whose `base_currency` is not `CNY` (`missing-fx`), so an EUR-per-USD series can
   never be consumed as CNY-per-USD.
3. **`available_price_type` derived from the data, not from a default.**
   `series_from_frame()` reads the frame's columns (`close` vs
   `close+adj_close`); a vendor that only supplied a raw close is recorded as
   such.
4. **Cash keeps its FX status.** `CashReturnCalculator` returns
   `fx_outcome.status`, so a stale FX leg on foreign cash reaches `stale_weight`
   and lowers the quality grade.
5. **Market metadata reaches the asset.** `build_assets()` falls back to
   `hit["market"]` (the price series' market) before the holdings row's market,
   so `calendar_for()` gets the exchange the quote actually came from.
6. **Repository writes the available price type.** `save_prices()` uses
   `point.available_price_type`; the compatibility alias is no longer persisted,
   so a computed point cannot re-key `market_prices`.

Known remaining item (deferred by decision, not forgotten): the legacy JSON
serializer (`_legacy_record` / `_legacy_row`) still lives in
`fa/analysis/nav_estimator.py`. It is architecture-only — it does not affect the
valuation maths, the data quality or the reproducibility — so it was kept out of
this round to avoid re-verifying the legacy output contract. When it moves to
`fa/serialization/legacy_estimate.py`, the emitted schema must stay byte-for-byte
compatible and the existing contract tests must pass unchanged.


## V2.2.3 — Data-integrity patch (after the third V2.2 review)

Two findings, both fixed (tests: 296):

1. **The source-declared asset type survives the SQLite write.**
   `save_quarters_to_db()` used to stamp `AssetType.EQUITY` on every row, which
   silently re-classified holdings that the source never declared. It now writes
   `AssetType.parse(row.get("asset_type"))`, so an empty declaration is stored —
   and read back — as `unknown`. Round-trip test: save a snapshot with one
   declared equity and one undeclared holding, load it back, assert
   `EQUITY` / `UNKNOWN`. This keeps the classification evidence on the path from
   the source to the database instead of re-assuming it at write time.
2. **`FxSeries.base_currency` is no longer silently defaulted.** The dataclass
   field is empty by default; `convert_to_cny()` rejects an undeclared base with
   `匯率序列缺少基準貨幣聲明（應為 CNY），拒絕換算` as well as a foreign base
   (`匯率基準貨幣 … 並非 CNY`). Internal trusted sources stay explicit instead of
   implicit: `from_pairs()` defaults to `CNY`, and the SQLite loader passes
   `base_currency="CNY"` because `fx_rates` is defined as a CNY-based table.

The scope rule for source declarations remains explicit: only 「股票投资明细」
declares `equity`; a future bond or fund table must add its own marker rather
than reuse the equity one.

## V2.3 — Backtesting & Validation

Two independent subsystems share the lower layers but never the result models:
(1) estimation validation (existing `fa/analysis/estimation_backtest.py`,
extended), and (2) strategy backtesting (new `fa/backtesting/`).

### V2.3.0 — Architecture & Data Audit (done)

Deliverables: `docs/development/v2.2.3_baseline/` (frozen before any change),
`docs/development/V2.3_codebase_audit.md`,
`docs/development/V2.3_historical_data_audit.md`,
`scripts/audit_backtest_data.py` (read-only) and
`data/exports/backtests/data_audit_report.json`. No engine code and no change to
V2.2 interfaces in this stage.

Headline results: 93 price rows (ten days, 2026-11-03 → 2026-11-16), **0 FX rows
persisted**, 1 holdings snapshot, 451 NAV sessions, `price_timestamp` coverage
0.0, and 17 estimation runs across only two target dates. Conclusion: the
estimation-validation subsystem has enough NAV history to score estimates but
not enough estimation history for meaningful metrics; the strategy backtest can
be engineered and unit-tested now but **cannot be run against real history**
until price/FX persistence, an availability-timestamp model and an explicit
holdings importer exist.

### V2.3.1 — Data foundation for backtesting

Specification: [`08_v231_data_foundation.md`](08_v231_data_foundation.md)
(accepted by the lead 2026-11-17; five stages, test-first).

Work: `available_at` on `PricePoint` (+ `market_prices` column), persist FX and
the full backtest window, schema migration 002 for `look_ahead_status`, finish
exchange normalisation (`EBS` → CH), an explicit quarterly-holdings importer,
and a frozen synthetic multi-currency fixture. Exit criteria: a run can read a
multi-year window from SQLite only; availability is recorded; no network in the
read path.

**Stage 1 — data model & migration: done (2026-11-17).** `AvailabilitySource`
plus `market_timestamp` / `available_at` / `availability_source` /
`availability_note` / `fetched_at` on `PricePoint` (V2.2 names stay as alias
properties), schema version 2 with an atomic migration runner, the
`price_revisions` ledger, and repository read/write support for the new
columns. Verified on copies of the frozen V2.2.3 baseline database (row counts
preserved, interrupted migration rolls back, re-run succeeds, V2.2 read paths
work) and by a live `000369` run that upgraded the working database in place.

**Stage 2 — FX pipeline: Stages 2.0–2.5 done (2026-11-18).**
Stage 2.0 delivered the audit
([`../development/V2.3.1_stage2_audit.md`](../development/V2.3.1_stage2_audit.md))
and the design
([`09_v231_stage2_fx_pipeline.md`](09_v231_stage2_fx_pipeline.md)). Measured
facts: a Yahoo FX daily bar is stamped at `00:00 Europe/London` of its trading
day (so `23:00Z` in BST, i.e. the previous UTC date — the current code labels
those bars one day early); all ten pairs quote CNY-per-unit; the current-day
bar is live, not closed. Confirmed by the lead: (D-1) FX bar day =
Europe/London date, `available_at = next London midnight + 30 min`, `inferred`;
(D-2) a separate `fx_revisions` table via Migration 003; the old façade's BST
date mapping is fixed and the running bar is written (PIT excludes it).

Stage 2.1 shipped Migration 003 (schema version 3): `fx_revisions` with the
twelve documented columns and two indexes, purely additive, no back-fill,
verified by 11 tests in `tests/test_migration_003.py` on a copy of the frozen
baseline (rollback on injected failure, idempotent re-run, baseline
byte-identical). Stages 2.2–2.5 (model/repository + `fx_as_of`, provider,
pipeline, integration) remain.

Stage 2.2 shipped the FX bar model and the repository/PIT layer:
`FxSeries.bars` / `pit_bars` / `from_bars()` keep each observation's full time
provenance without inventing one; `save_fx()` ledgers superseded rows into
`fx_revisions` atomically (identical rate ⇒ no revision, materially different
rate ⇒ the *stored* row is copied with its own timestamps, a vendor-dropped date
is never deleted); `fx_as_of()` is the point-in-time query and `load_fx()` keeps
its V2.2 behaviour for legacy `unknown` rows. 31 tests in
`tests/test_fx_repository.py`; suite 391. Deferred: the provider (2.3), the
pipeline (2.4) and the estimator wiring (2.5), so production `fx_rates` is still
empty.

Stage 2.3 shut that gap at the download end: `fa/data/fx_provider.py` fetches
raw Yahoo bars, maps each stamp to its **Europe/London** trading date (F-FX-01:
in BST the old code used the previous UTC day, shifting summer returns), infers
`available_at` as the end of the trading day + 30 min (D-1, `inferred`), refuses
unknown symbols instead of guessing, and flags out-of-band rates as
`quote-direction-suspect` without ever reciprocating them. `fa/data/fx.py`
became the V2.2 facade over it with unchanged signatures and a London-dated
index; `fx_series_map_from_fetch()` is the provenance-preserving path. 54 tests
in `tests/test_fx_provider.py` (mock HTTP only, including the request window and
the DST fallback) plus a live `scripts/smoke_fx_provider.py` that reported all
10 pairs inside their bands. **Stage 2.4 done**: `fa/data/fx_pipeline.py`
(`FxRequest`, `FxUpdateOutcome`, `update_fx`, `previous_fx_session`,
`next_fx_session`) decides cache/overlap, validates every bar, stamps the
availability fields and persists through the unchanged `save_fx()`; 58 tests in
`tests/test_fx_pipeline.py` after the review fixes (legacy rows with no
publication time are enriched from stored dates instead of being treated as a
cache hit, counters only move after a successful write, and provider
`warnings`/`duplicates` reach the outcome); suite 503. **Stage 2.5 done**:
`fa/config.py` gained `FX_PIPELINE_ENABLED` (`FA_FX_PIPELINE`, default on) and
`fa/analysis/nav_estimator.py` routes every currency through
`update_fx()` + `repository.fx_as_of()`, recording `fx_source`
(`pipeline-pit` / `legacy` / `none`) on the estimate and in its input snapshot.
A bar that exists but is not yet published is excluded and counted, and a
pipeline failure never silently downgrades to non-PIT rates. 8 tests in
`tests/test_fx_integration.py`, `scripts/smoke_fx_pipeline.py` for real data,
and the 000369 baseline re-run: with `as_of` 2026-11-17T00:39:20+08:00 the FX
contribution moved from −0.13994 % to **0.0** (the 2026-11-16 London bar is only
inferred-available at 2026-11-17T00:30Z), so the estimate rose from 2.8233
(−0.02365 %) to **2.8273 (+0.11655 %)** — the look-ahead defect was removed
without touching any return formula; suite 511.

### V2.3.2 — Strategy backtest engine (`fa/backtesting/`)

Specification: [`10_v232_backtest_engine.md`](10_v232_backtest_engine.md)
(**revised** 2026-11-18 after review m08248). The lead confirmed Q1–Q6 and
authorised Stage A; the revision replaced the range-summary fingerprint with a
content hash, fixed the execution instant at “execution session close + 30 min”
with the fill price equal to that close, added `initial_capital`, and wrote down
how a vendor re-scaling of historical `adj_close` must stay PIT-visible only from
its revision instant. Q7 (`prices_as_of` version reconstruction, §13.1) was
confirmed as option (a) on 2026-11-18, so Stage A is authorised and delivered. The engine is
specified before any code, per the Stage 2.5 review.

Hard prerequisite — **Stage A: price PIT foundation**. Measured on 2026-11-18,
`market_prices` holds 93 rows with `available_at` populated on **0** of them and
no `prices_as_of()` exists, while the FX leg already has `fx_as_of()`. Building
the engine on `load_prices()` would recreate the look-ahead defect Stage 2.5
removed, and a second history reader is forbidden, so Stage A stamps
availability through a `price_pipeline.py` mirroring the FX pipeline, records
revisions in the existing `price_revisions`, and adds one PIT read method. No
schema change is needed for it.

**Stage A delivered 2026-11-18** (Q7 option (a) confirmed). New
`fa/data/price_provider.py` and `fa/data/price_pipeline.py`; `Repository` gained
`prices_as_of()` and `count_price_revisions()`, and `save_prices()` now pushes a
revised value's `available_at` to `replaced_at` so a correction cannot be seen
before it happened. 48 offline tests; suite 559. Legacy rows without publication
metadata are re-downloaded rather than enriched (review m08583), and
`scripts/verify_legacy_backfill.py` reconciles the frozen baseline: 93 legacy keys,
10 provider calls, 93 stamped, 3 revisions, 0 still unknown, 0 duplicate keys.
The working database holds 5 107 rows, 5 034 PIT-visible, plus 73 pre-fix legacy
`adjusted_close` rows that stay PIT-invisible. Round 3 (review m08830) moved the
publication-metadata check out of the cache branch (an older evidence gap now has
priority over the overlap rule), made completeness per trading date through the
new `fa/data/price_canonical.py` shared by `prices_as_of()` and the pipeline, and
keeps the 73 duplicates as inert `unknown` keys; all ten tickers now plan a cache
hit on a repeat run. Round 4 (review m09026) removed the `julianday()` comparison
from `prices_as_of()` and moved the instant boundaries into Python datetime
arithmetic, because one ulp of a Julian day is about 47 µs and the 1 µs test
could not fail on a slower platform. Round 5 (review m09127) pushed the
`start`/`end` trading-date window into both UNION branches with bound
parameters, so the database no longer returns rows the caller would discard.

**Stage B delivered 2026-11-18** (authorised after the Stage A round-5 package).
New `fa/backtesting/models.py` (frozen dataclasses, `StrategySpec` validation,
`initial_capital` required, CNY-only base, long-only positions) and
`fa/backtesting/data_access.py` (PIT-only reads through `prices_as_of()` and
`fx_as_of()`, venue and currency guards, `revision_rows()` for the future
fingerprint), plus `tests/fixtures/backtesting.py` and two test modules.
`fa/__init__.py` became a lazy PEP 562 package so importing the backtest package
does not import the fetch stack, `PricePoint` gained `revision_id`, and
`_price_point_from_row()` carries it out of the `prices_as_of()` UNION. 47 new
tests; suite 618. The Stage B review fixed three issues in the same files
(`fx_rate()` now reads up to the session and takes `rate_on_or_before()`, so an
FX holiday falls back to the previous public rate instead of returning `None`;
`Instrument.market` is mandatory and every point's market/currency is checked,
closing the fail-open path; and every numeric field rejects NaN/Infinity).
65 tests in the two modules, suite 636.

**Stage C delivered 2026-11-19.** New `fa/backtesting/costs.py` (commission,
adverse slippage, FX spread, one rounding step with `ROUND_HALF_EVEN`) and
`fa/backtesting/engine.py` (session grid = union of venue calendars, decision at
close + 30 min, execution at the next session read through a PIT query, sells
before buys, cash-constrained buys, corporate actions only on raw series), plus
`tests/test_backtest_costs.py`, `tests/test_backtest_engine.py` and
`tests/test_backtest_pit.py`. Three decisions were applied: the `CostModel`
defaults now match spec §6 (5 bps / 0 / 10 bps / 20 bps), momentum uses the
positive-part normalised score (all ≤ 0 ⇒ cash), and a venue that is closed
inside the grid is carried at its last price without an anomaly. 51 new tests
(13 costs, 30 engine, 8 PIT); suite 687. The hand-computed two-asset case matches
to 1e-9 with zero costs and keeps its accounting identity with costs, no
unpublished value can change a decision (two databases differing only in an
unpublished close produce identical trades and equity curves), and `engine.py`
is proven by AST to import no database or network module and to read no clock.
A review then found three execution defects, all fixed inside `engine.py`: the fill
price used the adjusted close instead of the official close (now split into
`_execution_price()` / `_valuation_price()`), a foreign dividend without a
published rate was converted at 1:1 and invented cash (now skipped with a
`missing-fx-dividend` anomaly), and a venue closed inside the union grid was
reported as `missing-price` (now skipped silently, while a genuine gap still
raises it). `test_backtest_engine.py` grew from 22 to 30 tests.
**Stage D implementation delivered 2026-11-19; accepted 2026-11-20 (Round 9 review)** — `metrics.py` (§7 ratios, `None` for
undefined cases), `runner.py` (canonical spec, content `data_fingerprint`,
`run_id`, `run_backtest` with reuse and `force`), Migration 004 plus
`save_backtest_run`/`load_backtest_run`/`count_backtest_runs`, the
`BACKTEST_*` config keys and 41 new tests across `test_backtest_metrics`,
`test_backtest_runner`, `test_backtest_fingerprint`,
`test_backtest_execution_instant` and `test_migration_004`. The first review
then required five fixes: `dividend`/`split` in the content fingerprint, an
`anomalies_json` column so a reused run keeps its anomalies, a resolved
`RunConfig` (lags, fractional shares, risk-free rate, effective cost model)
inside the run identity and passed into the engine, `BacktestDataMissingError`
vs `BacktestDataIntegrityError` so contradictory data aborts instead of
looking absent, and the PIT benchmark (`benchmark.py`, `benchmark_base` on the
equity curve, benchmark rows in the fingerprint). That round added 29 tests
(`test_backtest_benchmark`, `test_backtest_run_identity`,
`test_backtest_data_integrity`); suite 781. Round 3 made the benchmark read session by session and put its FX in the fingerprint, and added the `COST_MODEL_DEFAULT` sentinel for explicit costs; round 4 removed `revision_id` from the payload (database bookkeeping, not a market input), rebuilt `data_fingerprint(sessions=..., lag_minutes=...)` so every decision instant hashes only what was visible then (momentum lookback included, the FX rate used recorded per session, missing markers per session, integrity still aborting), and made the benchmark value with `adj_close` when present, recording `price_source`. FX still has no version ledger (Q7), so an in-place FX correction reaches earlier instants; that limitation is documented and pinned by a test rather than hidden. Round 5 made the strategy fragment describe the FX the engine actually consumed: `fx_rate()` (which falls back to an older observation) with the observation date recorded, a momentum window taken per instrument instead of from the first universe entry's calendar, and no `fetched_at` in the payload, so a re-download that only refreshes the fetch time cannot invalidate a run; the spec header records Stage D as pending final acceptance and Stage E as unauthorised. Round 7 split the price roles: a shut venue no longer suppresses the momentum window on a rebalance day (P1-17), and `session_price` is documented as a conservative superset of the prices the engine could use (P2-17); the block is exposed through `fingerprint_session_payload()` so tests assert role composition directly.
Stage E (CLI/report surface, benchmark curve, retention) was **delivered 2026-11-20 and accepted 2026-11-21** — see
§19 of `10_v232_backtest_engine.md` and `docs/development/stageE_bt_matrix.md`.
Delivered artefacts: `scripts/accept_backtest_synthetic.py` (30 hand-checked
assertions over five scenarios), `scripts/audit_working_db.py`,
`scripts/smoke_backtest_real.py` (two trades on the working database with
PIT-priced fills), `scripts/backtest_report.py` (`run`/`list`/`show` over
persisted runs, report traceable to `run_id`), `scripts/audit_backtest_retention.py`
and `scripts/coverage_core.py`.

*Initial Stage E delivery, before Round 8 fixes:* suite 782; `compileall` and
`git diff --check` clean; the documented 90 % statement-coverage gate was **not
met** (88.2 % with the stdlib tracer, since `coverage.py` is not installed —
`returns/base.py` 85.5 %, `returns/fx.py` 66.7 %, `nav_estimator.py` 86.6 %).

*Round 8 fixes (2026-11-20):* the tracer now counts every executable statement
(module-level, class-body and function-body; docstrings excluded), which is what
`sys.settrace` actually observes because it starts before discovery; focused
tests cover the previously untested diagnostic paths; the Migration 004
self-heal is scoped to `anomalies_json`; `backtest_report run` renders the
reloaded run; the real-data smoke uses the engine's execution instant; the
two-year ingestion command is documented in §12.1. Current result: **846
tests**, coverage **99.4 %** (704/708) with every documented module ≥ 90 %
(`returns/base.py` 98.1 %, `nav_estimator.py` 99.7 %, the rest 100 %); the four
remaining defensive lines are listed in `docs/development/stageE_coverage.md`.
Stage D was independently accepted in the Round 9 review; **Stage E was accepted on 2026-11-21**, which closes V2.3.2.

Then the engine stages: models and PIT-only access → rebalance/trade/equity
simulation with costs → metrics, runner and Migration 004
(`strategy_backtest_runs` / `_metrics` / `_trades` / `_equity`);
`backtest_results` stays dedicated to estimation validation.

Exit criteria: metrics reproducible from the database; grouping available; a
model-version comparison exists; every metric recomputable from stored equity
and trades alone.

## V2.4 — GUI & Integration

Specification: `11_v24_gui_integration.md`; acceptance matrix:
`docs/development/v24_acceptance_matrix.md`. Framework decision: keep Tkinter
and integrate progressively (confirmed 2026-11-20). V2.4 is GUI and integration
only; the accepted V2/V2.3.2 engines are not modified.

| Stage | Scope | Status |
| --- | --- | --- |
| 0 | Baseline audit, specification freeze, Stage A–E acceptance matrix — **no functional code** | delivered 2026-11-20; **accepted in round 3** (m11973) |
| A | GUI foundation: four-area navigation, window registry, shared worker bridge, shared formatting, lifecycle tests | delivered 2026-11-20; round-2 A-Fix (worker widget isolation, shared close budget); A-01…A-09 automated and **A-10 manual completed 2026-11-20** (`v24_evidence/A-10.md`); **accepted** |
| B | Estimation integration (V2.4) | B-1…B-3 delivered (2026-11-21). The service layer (`fa/application/estimation.py`) proves persistence by read-back; the QDII window renders the accepted view, browses stored history and keeps the analysis handover. The first B-3 package was rejected for testing synthetic fixtures instead of the real app path and payload; the eight review items (live entry, `on_saved`, stored mode/run id, 0/1 look-ahead, nested inputs, tab auto-load, scrollable quality block, document corrections) are fixed. **accepted** (B-3 evidence: 1,048 tests). B-01…B-15 and B-17 pass; B-16 (real Windows desktop) is folded into Stage E (E-01/E-10). |
| C | Backtesting GUI: `StrategySpec` form (validation delegated to the model), worker run, metrics/trades/equity/anomalies panels, "Existing Run Reused" state | delivered 2026-11-21: `fa/application/backtesting.py` (short-lived Repository, cost-preserving JSON), `fa/gui/backtest_window.py` (form + persisted-run panels), the three new test modules; C-01…C-13, C-15, C-16 and C-19 pass after the review fix (the equity table regains its drawdown column), C-14/C-17/C-18 are the manual desktop rows; **accepted** |
| D | Reports & history: renderer moved to `fa/reporting/`, report centre for stored estimates and backtests, export, immutable history | **accepted** (D-10 environment note) — delivered 2026-11-21; review fixes F1–F5 (shared close budget, evidence counts, diff-check scope, `LIMIT` clamp proof, estimation pane) verified; D-01…D-09 and D-11 pass, D-10 manual pending; **accepted** |
| E | End-to-end + Windows desktop acceptance, regression, packaged ZIP | **accepted** — E-01…E-13 pass (D-10 remains an environment note); E-11 (`Ran 1131 tests in 130.230s OK`), E-12 (704/708 = 99.4 %, BT 423 OK) and E-13 (`MIGRATION_001` identical, 002–004 additive) pass; E-01 and E-03…E-10 **pass** in a scripted Windows session (`docs/development/v24_evidence/E-driver.json`, screenshots under `E-01/`…`E-10/`); **E-02 now passes** after the E-02a lazy-start fix — median **1.547 s** (1.494 / 1.649 / 1.547 s; was 6.835 s), import 0.46–0.50 s, window build 1.32–1.46 s |

Exit criteria: every row of the V2.4 acceptance matrix `pass` with evidence;
Windows acceptance recorded; regression suite green; docs current. Each stage is
accepted separately — opening every window is not acceptance.

## Migration policy

* V1 modules keep working until their V2 replacement passes the same tests.
* New code lives in the V2 paths; nothing is rewritten wholesale.
* Every phase ends with `compileall`, the full unittest suite, and a real-data
  smoke run on fund 000369.
