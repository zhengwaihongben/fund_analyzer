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


## V2.3 — Backtesting & Validation

Work: store estimates per run, match to later official NAV, compute
MAE/RMSE/mean error/max abs error, group by asset type, market, coverage bucket
and model version, and emit a backtest report.

Exit criteria: metrics reproducible from the database; grouping available; a
model-version comparison exists.

## V2.4 — GUI & Integration

Work: integrate the new estimate into the QDII window (assets, price dates,
quality, coverage, warnings), add a backtest window, update README, run the full
regression suite, and perform the Windows acceptance run.

Exit criteria: GUI acceptance on Windows; regression suite green; docs current.

## Migration policy

* V1 modules keep working until their V2 replacement passes the same tests.
* New code lives in the V2 paths; nothing is rewritten wholesale.
* Every phase ends with `compileall`, the full unittest suite, and a real-data
  smoke run on fund 000369.
