# -*- coding: utf-8 -*-
"""SQLite schema for the V2 data layer.

Migrations are **append-only**: every entry in :data:`MIGRATIONS` is a
``(version, sql)`` pair applied in order, and a shipped entry is never edited.
That is what lets an existing user database upgrade safely instead of being
rebuilt (see docs/technical_specification/V2/04_database_schema.md).

Some migrations cannot be expressed as plain SQL: SQLite has no
``ADD COLUMN IF NOT EXISTS``, so adding a column to an existing table needs an
introspection step first.  Those steps live in :data:`MIGRATION_COLUMNS` and
run inside the same transaction as the SQL body.
"""
from __future__ import annotations

#: Current schema version, i.e. the highest applied migration.
SCHEMA_VERSION = 4

MIGRATION_001 = """
CREATE TABLE IF NOT EXISTS funds (
    fund_code   TEXT PRIMARY KEY,
    name        TEXT,
    fund_type   TEXT,
    is_qdii     INTEGER,
    source      TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS assets (
    asset_id    TEXT PRIMARY KEY,
    ticker      TEXT,
    name        TEXT,
    asset_type  TEXT NOT NULL,
    market      TEXT,
    currency    TEXT,
    exchange    TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS holdings_snapshots (
    snapshot_id              TEXT PRIMARY KEY,
    fund_code                TEXT NOT NULL REFERENCES funds(fund_code),
    quarter                  TEXT NOT NULL,
    holding_date             TEXT NOT NULL,
    disclosure_date          TEXT NOT NULL,
    disclosure_date_estimated INTEGER NOT NULL DEFAULT 0,
    source                   TEXT,
    created_at               TEXT NOT NULL,
    UNIQUE (fund_code, quarter)
);

CREATE TABLE IF NOT EXISTS holdings (
    snapshot_id TEXT NOT NULL REFERENCES holdings_snapshots(snapshot_id) ON DELETE CASCADE,
    asset_id    TEXT NOT NULL REFERENCES assets(asset_id),
    weight      REAL NOT NULL,
    PRIMARY KEY (snapshot_id, asset_id)
);

CREATE TABLE IF NOT EXISTS market_prices (
    ticker          TEXT NOT NULL,
    price_date      TEXT NOT NULL,
    price_type      TEXT NOT NULL DEFAULT 'adjusted_close',
    close           REAL NOT NULL,
    adj_close       REAL,
    price_timestamp TEXT,
    retrieved_at    TEXT,
    market          TEXT,
    currency        TEXT,
    source          TEXT,
    PRIMARY KEY (ticker, price_date, price_type)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    currency     TEXT NOT NULL,
    rate_date    TEXT NOT NULL,
    rate         REAL NOT NULL,
    source       TEXT,
    retrieved_at TEXT,
    PRIMARY KEY (currency, rate_date)
);

CREATE TABLE IF NOT EXISTS official_nav (
    fund_code TEXT NOT NULL,
    nav_date  TEXT NOT NULL,
    nav       REAL NOT NULL,
    source    TEXT,
    PRIMARY KEY (fund_code, nav_date)
);

CREATE TABLE IF NOT EXISTS estimation_runs (
    run_id                TEXT PRIMARY KEY,
    fund_code             TEXT NOT NULL REFERENCES funds(fund_code),
    mode                  TEXT NOT NULL DEFAULT 'qdii',
    model_version         TEXT NOT NULL,
    trade_date            TEXT NOT NULL,
    target_nav_date       TEXT NOT NULL,
    base_nav_date         TEXT,
    as_of                 TEXT,
    created_at            TEXT NOT NULL,
    holdings_snapshot_id  TEXT REFERENCES holdings_snapshots(snapshot_id),
    look_ahead_safe       INTEGER NOT NULL DEFAULT 1,
    timezone              TEXT,
    cutoff                TEXT,
    holdings_source       TEXT,
    holdings_quarter      TEXT
);

CREATE TABLE IF NOT EXISTS estimation_inputs (
    run_id       TEXT PRIMARY KEY REFERENCES estimation_runs(run_id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS estimation_results (
    run_id              TEXT PRIMARY KEY REFERENCES estimation_runs(run_id) ON DELETE CASCADE,
    status              TEXT NOT NULL,
    base_nav            REAL,
    estimated_nav       REAL,
    estimated_return    REAL,
    coverage_ratio      REAL,
    uncovered_weight    REAL,
    local_contribution  REAL,
    fx_contribution     REAL,
    data_quality        TEXT,
    quality_score       REAL,
    message             TEXT,
    warnings_json       TEXT
);

CREATE TABLE IF NOT EXISTS backtest_results (
    run_id      TEXT NOT NULL REFERENCES estimation_runs(run_id) ON DELETE CASCADE,
    group_key   TEXT NOT NULL DEFAULT 'all',
    n           INTEGER NOT NULL,
    mae         REAL,
    rmse        REAL,
    mean_error  REAL,
    max_abs_error REAL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (run_id, group_key)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_fund_date
    ON holdings_snapshots (fund_code, holding_date);
CREATE INDEX IF NOT EXISTS idx_prices_ticker_date
    ON market_prices (ticker, price_date);
CREATE INDEX IF NOT EXISTS idx_fx_currency_date
    ON fx_rates (currency, rate_date);
CREATE INDEX IF NOT EXISTS idx_nav_fund_date
    ON official_nav (fund_code, nav_date);
CREATE INDEX IF NOT EXISTS idx_runs_fund_target
    ON estimation_runs (fund_code, target_nav_date);
"""

#: v2 — the V2.3.1 time model.  Columns are added by introspection (SQLite has
#: no ``ADD COLUMN IF NOT EXISTS``) inside the migration transaction; the SQL
#: body below back-fills them and creates the revision ledger.
MIGRATION_002_COLUMNS: dict[str, dict[str, str]] = {
    "market_prices": {
        "market_timestamp": "TEXT",
        "available_at": "TEXT",
        "availability_source": "TEXT NOT NULL DEFAULT 'unknown'",
        "availability_note": "TEXT",
        "fetched_at": "TEXT",
        "first_fetched_at": "TEXT",
        "dividend": "REAL",
        "split": "REAL",
    },
    "fx_rates": {
        "base_currency": "TEXT NOT NULL DEFAULT 'CNY'",
        "market_timestamp": "TEXT",
        "available_at": "TEXT",
        "availability_source": "TEXT NOT NULL DEFAULT 'unknown'",
        "availability_note": "TEXT",
        "fetched_at": "TEXT",
    },
    "estimation_runs": {
        "look_ahead_status": "TEXT NOT NULL DEFAULT 'unknown'",
    },
}

MIGRATION_002 = """
-- Legacy timestamps keep their value under the new names.  Rows written before
-- V2.3.1 never had an available_at, so they stay 'unknown' (excluded from PIT
-- queries but kept for audit) instead of being upgraded to a verified state.
UPDATE market_prices
   SET market_timestamp = price_timestamp
 WHERE market_timestamp IS NULL AND price_timestamp IS NOT NULL;

UPDATE market_prices
   SET fetched_at = retrieved_at
 WHERE fetched_at IS NULL AND retrieved_at IS NOT NULL;

UPDATE market_prices
   SET first_fetched_at = fetched_at
 WHERE first_fetched_at IS NULL AND fetched_at IS NOT NULL;

UPDATE fx_rates
   SET fetched_at = retrieved_at
 WHERE fetched_at IS NULL AND retrieved_at IS NOT NULL;

-- The boolean already lost the four-state information: safe = 1 cannot be
-- restored to 'verified-safe', so it becomes 'unknown'.  Only safe = 0 carries
-- information and becomes 'unsafe'.
UPDATE estimation_runs
   SET look_ahead_status = CASE WHEN look_ahead_safe = 0 THEN 'unsafe' ELSE 'unknown' END;

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

CREATE INDEX IF NOT EXISTS idx_revisions_ticker_date
    ON price_revisions (ticker, price_date);
CREATE INDEX IF NOT EXISTS idx_prices_available_at
    ON market_prices (ticker, available_at);
CREATE INDEX IF NOT EXISTS idx_fx_available_at
    ON fx_rates (currency, available_at);
"""

#: v3 — the V2.3.1 Stage 2 FX revision ledger.  Purely additive: one table,
#: two indexes, no back-fill (``fx_rates`` is empty in every database this
#: project has produced) and no change to any existing column or index.
#: ``price_revisions`` is not reused because it keys on
#: ``(ticker, price_date, price_type)`` — an FX row would need a synthetic
#: ``FX:USD/CNY`` ticker, which the project bans elsewhere.
MIGRATION_003 = """
CREATE TABLE IF NOT EXISTS fx_revisions (
    revision_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    currency            TEXT NOT NULL,
    rate_date           TEXT NOT NULL,
    rate                REAL NOT NULL,
    base_currency       TEXT NOT NULL DEFAULT 'CNY',
    market_timestamp    TEXT,
    available_at        TEXT,
    availability_source TEXT NOT NULL DEFAULT 'unknown',
    source              TEXT,
    fetched_at          TEXT,
    replaced_at         TEXT NOT NULL,
    reason              TEXT
);

CREATE INDEX IF NOT EXISTS idx_fx_revisions_currency_date
    ON fx_revisions (currency, rate_date);
CREATE INDEX IF NOT EXISTS idx_fx_revisions_replaced_at
    ON fx_revisions (replaced_at);
"""

#: v4 — the V2.3.2 Stage D strategy-backtest tables.  Purely additive: four
#: tables plus one index, no change to any existing table, so the frozen-baseline
#: test pattern of Migrations 002/003 applies unchanged.  ``backtest_results``
#: keeps its estimation-validation meaning and is not reused.
MIGRATION_004 = """
CREATE TABLE IF NOT EXISTS strategy_backtest_runs (
    run_id             TEXT PRIMARY KEY,
    spec_id            TEXT NOT NULL,
    model_version      TEXT NOT NULL,
    start_date         TEXT NOT NULL,
    end_date           TEXT NOT NULL,
    base_currency      TEXT NOT NULL DEFAULT 'CNY',
    universe_json      TEXT NOT NULL,
    spec_json          TEXT NOT NULL,
    costs_json         TEXT NOT NULL,
    data_fingerprint   TEXT NOT NULL,
    look_ahead_status  TEXT NOT NULL DEFAULT 'unknown',
    created_at         TEXT NOT NULL,
    anomalies_json     TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS strategy_backtest_metrics (
    run_id             TEXT PRIMARY KEY REFERENCES strategy_backtest_runs(run_id) ON DELETE CASCADE,
    sessions           INTEGER NOT NULL,
    cagr               REAL,
    annualised_vol     REAL,
    sharpe             REAL,
    sortino            REAL,
    max_drawdown       REAL,
    calmar             REAL,
    turnover           REAL,
    hit_rate           REAL,
    exposure           REAL,
    cost_drag          REAL,
    trades             INTEGER NOT NULL,
    total_costs_base   REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS strategy_backtest_trades (
    run_id             TEXT NOT NULL REFERENCES strategy_backtest_runs(run_id) ON DELETE CASCADE,
    seq                INTEGER NOT NULL,
    session            TEXT NOT NULL,
    ticker             TEXT NOT NULL,
    side               TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity           REAL NOT NULL,
    price_local        REAL NOT NULL,
    price_source       TEXT NOT NULL,
    fx_rate            REAL,
    fx_source          TEXT,
    gross_base         REAL NOT NULL,
    commission_base    REAL NOT NULL,
    slippage_base      REAL NOT NULL,
    fx_spread_base     REAL NOT NULL,
    net_base           REAL NOT NULL,
    reason             TEXT,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS strategy_backtest_equity (
    run_id             TEXT NOT NULL REFERENCES strategy_backtest_runs(run_id) ON DELETE CASCADE,
    session            TEXT NOT NULL,
    cash_base          REAL NOT NULL,
    holdings_base      REAL NOT NULL,
    total_base         REAL NOT NULL,
    exposure           REAL NOT NULL,
    benchmark_base     REAL,
    drawdown           REAL,
    PRIMARY KEY (run_id, session)
);

CREATE INDEX IF NOT EXISTS idx_bt_trades_session
    ON strategy_backtest_trades (run_id, session);
"""

#: Ordered migrations; append new versions, never edit old ones.
MIGRATIONS: list[tuple[int, str]] = [
    (1, MIGRATION_001),
    (2, MIGRATION_002),
    (3, MIGRATION_003),
    (4, MIGRATION_004),
]

#: Per-version column additions applied before the SQL body.
MIGRATION_COLUMNS: dict[int, dict[str, dict[str, str]]] = {
    2: MIGRATION_002_COLUMNS,
}

#: Repair list for a column added to an already-applied migration while V2.3.2
#: was still under review.  ``anomalies_json`` belongs to Migration 004; a
#: database created by the earlier build reports version 4 without it.
#: :func:`connection.initialise` applies exactly this list — never the whole of
#: :data:`MIGRATION_COLUMNS` for an applied version, because re-adding a
#: Migration 002 column would leave its backfill SQL unrun.
REVIEW_COLUMN_REPAIRS: dict[str, dict[str, str]] = {
    "strategy_backtest_runs": {"anomalies_json": "TEXT NOT NULL DEFAULT '[]'"},
}
