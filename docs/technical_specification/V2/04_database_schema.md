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
every migration whose id is greater than `schema_version.version`, inside a
transaction, then records it. Rules:

* migrations are append-only — never edit a shipped one;
* each migration must be idempotent-safe for a fresh database;
* `PRAGMA foreign_keys = ON` and `journal_mode = WAL` are set on connect.

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
    def save_prices(self, series: PriceSeries) -> int: ...
    def load_prices(self, ticker, start=None, end=None) -> PriceSeries: ...
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
