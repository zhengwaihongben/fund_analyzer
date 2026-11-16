# -*- coding: utf-8 -*-
"""SQLite schema for the V2 data layer.

Migrations are **append-only**: every entry in :data:`MIGRATIONS` is a
``(version, sql)`` pair applied in order, and a shipped entry is never edited.
That is what lets an existing user database upgrade safely instead of being
rebuilt (see docs/technical_specification/V2/04_database_schema.md).
"""
from __future__ import annotations

#: Current schema version, i.e. the highest applied migration.
SCHEMA_VERSION = 1

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

#: Ordered migrations; append new versions, never edit old ones.
MIGRATIONS: list[tuple[int, str]] = [
    (1, MIGRATION_001),
]
