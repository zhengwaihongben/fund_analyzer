# V2 — Database Schema (SQLite)

Database file: `data/database/fund_analyzer.db` (created on demand; git-ignored).
Implemented in `fa/data/database/` (`schema.py`, `connection.py`, `repository.py`).

## 1. Purpose

One shared store for the whole Fund Analyzer:

* historical holdings (which quarter was disclosed when),
* historical market prices and FX rates,
* official NAV series,
* every estimation run with its **complete inputs** and its result,
* backtest metrics.

The non-negotiable rule: **a finished estimate is never overwritten.** Re-running
creates a new `run_id`; the old one stays replayable.

## 2. ER diagram

```
funds 1───n holdings_snapshots 1───n holdings n───1 assets
  │                │
  │                └──────────────┐
  │                               │
  └──1──n estimation_runs 1───1 estimation_inputs
                    │
                    ├──1──1 estimation_results
                    └──1──n backtest_results

assets 1───n market_prices
fx_rates (standalone, keyed by currency+date)
official_nav (standalone, keyed by fund_code+nav_date)
```

## 3. Tables

### 3.1 `schema_version`

| Column | Type | Notes |
|--------|------|-------|
| `version` | INTEGER PK | migration id |
| `applied_at` | TEXT | ISO timestamp |

### 3.2 `funds`

| Column | Type |
|--------|------|
| `fund_code` | TEXT PK |
| `name` | TEXT |
| `fund_type` | TEXT |
| `is_qdii` | INTEGER |
| `source` | TEXT |
| `updated_at` | TEXT |

### 3.3 `assets`

| Column | Type |
|--------|------|
| `asset_id` | TEXT PK |
| `ticker` | TEXT |
| `name` | TEXT |
| `asset_type` | TEXT (enum value) |
| `market` | TEXT |
| `currency` | TEXT |
| `exchange` | TEXT |
| `created_at` | TEXT |

### 3.4 `holdings_snapshots`

| Column | Type | Notes |
|--------|------|-------|
| `snapshot_id` | TEXT PK | `sha1(fund_code|quarter)` style id |
| `fund_code` | TEXT FK → funds | |
| `quarter` | TEXT | `YYYYQn` |
| `holding_date` | TEXT | quarter end |
| `disclosure_date` | TEXT | |
| `disclosure_date_estimated` | INTEGER | |
| `source` | TEXT | |
| `created_at` | TEXT | |

`UNIQUE(fund_code, quarter)`.

### 3.5 `holdings`

| Column | Type |
|--------|------|
| `snapshot_id` | TEXT FK → holdings_snapshots |
| `asset_id` | TEXT FK → assets |
| `weight` | REAL |

`PRIMARY KEY(snapshot_id, asset_id)`.

### 3.6 `market_prices`

| Column | Type |
|--------|------|
| `ticker` | TEXT |
| `price_date` | TEXT |
| `price_type` | TEXT |
| `close` | REAL |
| `adj_close` | REAL |
| `price_timestamp` | TEXT |
| `retrieved_at` | TEXT |
| `market` | TEXT |
| `currency` | TEXT |
| `source` | TEXT |

`PRIMARY KEY(ticker, price_date, price_type)` — re-downloading the same day
updates instead of duplicating.

`price_type` stores the **available** price type (what the source provided,
e.g. `close+adj_close`), never the used one: the used type is a property of a
particular calculation and lives in `estimation_inputs`. Writing the
`PricePoint.price_type` alias here would let a computed point re-key an
existing row (V2.2.2).

### 3.7 `fx_rates`

| Column | Type |
|--------|------|
| `currency` | TEXT |
| `rate_date` | TEXT |
| `rate` | REAL (CNY per unit) |
| `source` | TEXT |
| `retrieved_at` | TEXT |

`PRIMARY KEY(currency, rate_date)`.

### 3.8 `official_nav`

| Column | Type |
|--------|------|
| `fund_code` | TEXT |
| `nav_date` | TEXT |
| `nav` | REAL |
| `source` | TEXT |

`PRIMARY KEY(fund_code, nav_date)`.

### 3.9 `estimation_runs` (identity)

| Column | Type | Notes |
|--------|------|-------|
| `run_id` | TEXT PK | uuid4 hex |
| `fund_code` | TEXT FK | |
| `mode` | TEXT | `qdii` / `standard` |
| `model_version` | TEXT | e.g. `2.0.0` |
| `trade_date` | TEXT | China trade date |
| `target_nav_date` | TEXT | |
| `base_nav_date` | TEXT | |
| `as_of` | TEXT | tz-aware |
| `created_at` | TEXT | |
| `holdings_snapshot_id` | TEXT FK | snapshot actually used (point-in-time) |
| `look_ahead_safe` | INTEGER | |
| `timezone` | TEXT | |
| `cutoff` | TEXT | |

### 3.10 `estimation_inputs` (full inputs)

| Column | Type | Notes |
|--------|------|-------|
| `run_id` | TEXT PK FK → estimation_runs | |
| `payload_json` | TEXT | the complete record: dates, holdings rows, prices, FX, warnings |

`payload_json` is the replay record: given it, the same NAV must be
reproducible without any network access.

### 3.11 `estimation_results`

| Column | Type |
|--------|------|
| `run_id` | TEXT PK FK |
| `status` | TEXT |
| `base_nav` | REAL |
| `estimated_nav` | REAL |
| `estimated_return` | REAL |
| `coverage_ratio` | REAL |
| `uncovered_weight` | REAL |
| `local_contribution` | REAL |
| `fx_contribution` | REAL |
| `data_quality` | TEXT |
| `quality_score` | REAL |
| `message` | TEXT |
| `warnings_json` | TEXT |

### 3.12 `backtest_results`

| Column | Type |
|--------|------|
| `run_id` | TEXT |
| `group_key` | TEXT (`all` / asset type / market / coverage bucket / model version) |
| `n` | INTEGER |
| `mae` | REAL |
| `rmse` | REAL |
| `mean_error` | REAL |
| `max_abs_error` | REAL |
| `computed_at` | TEXT |

`PRIMARY KEY(run_id, group_key)`.

## 4. Migrations

`schema.py` holds an ordered list of migration scripts
(`MIGRATIONS: list[tuple[int, str]]`). `connection.initialise(path)` applies
every migration whose id is greater than `schema_version.version`, then records
it. Rules:

* migrations are append-only — never edit a shipped one;
* each migration must be idempotent-safe for a fresh database;
* `PRAGMA foreign_keys = ON` and `journal_mode = WAL` are set on connect;
* **one explicit transaction per migration** (`BEGIN IMMEDIATE … COMMIT`), with
  the SQL body split into single statements executed by `conn.execute` —
  `executescript()` would issue an implicit `COMMIT` and silently break
  atomicity. A failure (including an injected crash) rolls the whole migration
  back and `schema_version` still reports the previous version;
* column additions are declared in `MIGRATION_COLUMNS[version]` and applied by a
  `PRAGMA table_info` check, because SQLite has no `ADD COLUMN IF NOT EXISTS`.

### 4.1 Version 2 (V2.3.1 time model)

`SCHEMA_VERSION = 2`. Additive only; see
`08_v231_data_foundation.md` §4 for the full statement list. New columns:

| table | new columns |
|---|---|
| `market_prices` | `market_timestamp`, `available_at`, `availability_source` (default `unknown`), `availability_note`, `fetched_at`, `first_fetched_at`, `dividend`, `split` |
| `fx_rates` | `base_currency` (default `CNY`), `market_timestamp`, `available_at`, `availability_source`, `availability_note`, `fetched_at` |
| `estimation_runs` | `look_ahead_status` (default `unknown`) |

New table `price_revisions` — the append-only ledger written before a re-download
overwrites a stored observation; it keeps every numeric column of the superseded
row (including `dividend` / `split`), `replaced_at` and `reason`.

Back-fill is conservative: legacy `market_prices.price_timestamp` is copied into
`market_timestamp` and `retrieved_at` into `fetched_at`, but `available_at`
stays `NULL` with `availability_source='unknown'`; `look_ahead_safe = 0` becomes
`unsafe`, `look_ahead_safe = 1` becomes `unknown` (never `verified-safe`, because
the boolean already lost that information). The legacy columns are still written
by the repository so V2.2 readers keep working.

### 4.2 Version 3 (V2.3.1 Stage 2.1 — FX revision ledger)

`SCHEMA_VERSION = 3`. Purely additive: one table, two indexes, no back-fill (no
database in this project holds FX rows yet) and no change to any existing column
or index.

`fx_revisions` keeps the **superseded** FX observation, mirroring
`price_revisions`: `revision_id` (autoincrement), `currency`, `rate_date`,
`rate`, `base_currency` (default `CNY`), `market_timestamp`, `available_at`,
`availability_source` (default `unknown`), `source`, `fetched_at`,
`replaced_at` (not null) and `reason`. Indexes:
`idx_fx_revisions_currency_date (currency, rate_date)` and
`idx_fx_revisions_replaced_at (replaced_at)`.

`price_revisions` is deliberately not reused: it keys on
`(ticker, price_date, price_type)` and has no currency/base column, so an FX row
would require a synthetic `FX:USD/CNY` ticker. The table is created by
Migration 003 and filled from Stage 2.2 onwards; `tests/test_migration_003.py`
proves the upgrade on a copy of the frozen baseline, the rollback on an injected
failure, the idempotent re-run and the byte-identical baseline.

### 4.3 Version 4 (V2.3.2 Stage D — backtest persistence)

`SCHEMA_VERSION = 4`. Purely additive: four new tables, no change to any existing
table, column or index, and no back-fill.

| Table | Purpose |
|---|---|
| `strategy_backtest_runs` | one row per run — `run_id` primary key, `spec_id`, `model_version`, `start`, `end`, `base_currency`, `universe_json`, `spec_json`, `costs_json`, `data_fingerprint`, `look_ahead_status`, `created_at`, `anomalies_json` |
| `strategy_backtest_metrics` | one row per run — the §7 ratios (`cagr`, `annualised_vol`, `sharpe`, `sortino`, `max_drawdown`, `calmar`, `turnover`, `hit_rate`, `exposure`, `cost_drag`, `total_costs_base`, `sessions`, `trades`) |
| `strategy_backtest_trades` | one row per trade, `seq`-ordered — session, ticker, side, quantity, prices and FX provenance, cost components, `net_base`, `reason` |
| `strategy_backtest_equity` | one row per session — cash, holdings, total, exposure, benchmark, drawdown |

`idx_bt_trades_session` indexes `(run_id, session)`; the equity table is keyed by
`(run_id, session)`. `save_backtest_run()` writes all four rows in one
transaction, so a run is never half-stored. `anomalies_json` keeps the run's
engine anomalies (`[]` when empty), so a run reloaded from the database reports
exactly what the original run recorded instead of an empty tuple. See
`docs/technical_specification/V2/10_v232_backtest_engine.md` §18.

## 5. Repository API (`fa/data/database/repository.py`)

```python
class Repository:
    def __init__(self, path: str | Path | None = None): ...

    # funds / holdings
    def upsert_fund(self, fund_code, name=None, fund_type=None, is_qdii=None, source=None): ...
    def save_snapshot(self, snapshot: HoldingSnapshot) -> str: ...      # returns snapshot_id
    def load_snapshots(self, fund_code) -> list[HoldingSnapshot]: ...
    def snapshot_as_of(self, fund_code, day: date) -> HoldingSnapshot | None: ...

    # market data
    def save_prices(self, series: PriceSeries) -> int: ...   # writes price_revisions first;
                                                             # a revised value's available_at
                                                             # is pushed to replaced_at
    def load_prices(self, ticker, start=None, end=None) -> PriceSeries: ...
    def prices_as_of(self, ticker, as_of: datetime, start=None, end=None) -> PriceSeries: ...
    def count_price_revisions(self, ticker, start=None, end=None) -> int: ...
    def save_fx(self, series: FxSeries) -> int: ...
    def load_fx(self, currency, start=None, end=None) -> FxSeries: ...

    # nav
    def save_official_nav(self, fund_code, rows) -> int: ...
    def load_official_nav(self, fund_code) -> list[tuple[date, float]]: ...

    # estimates
    def save_estimation(self, run: EstimationRun, inputs: dict,
                        result: EstimationResult) -> str: ...          # transactional
    def load_estimation(self, run_id) -> dict: ...
    def list_estimations(self, fund_code=None, limit=50) -> list[dict]: ...
    def estimation_inputs(self, run_id) -> dict: ...
```

`save_estimation` writes `estimation_runs`, `estimation_inputs` and
`estimation_results` in one transaction; a partial write is never left behind.

## 6. Acceptance criteria

* SQLite manages the main data of the Fund Analyzer.
* Every table has explicit primary keys; foreign keys are declared and enforced.
* Each estimate stores complete inputs and results, and cannot be overwritten
  (a repeated `run_id` raises instead of silently replacing).
* Any run can be reconstructed from `estimation_inputs.payload_json`.
* `holdings_snapshots` is allowed to hold few quarters (one is normal): the table
  is the durable home of whatever was obtainable, and `snapshot_as_of()` answers
  point-in-time queries over that history rather than requiring a complete series.
* Migrations upgrade an old database safely (test creates a v1-shaped DB and
  migrates it).
* Code and data are separated on disk (`fa/` vs `data/`).
