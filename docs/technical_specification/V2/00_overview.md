# V2 — Overview

> Status: **V2.0 (Technical Foundation) specification.** This document set is the
> contract that V2.1+ code is written against. Numbering follows the agreed
> development plan (guideline, page 115 onwards).

## 1. Positioning

Fund Analyzer V2 is a *historical and near-real-time portfolio valuation engine
for mutual funds, with particular support for QDII funds*:

* estimate a fund's NAV for a target date from disclosed holdings plus market
  closes and FX rates,
* support several asset classes, markets and currencies,
* keep a complete, replayable history of every estimate,
* be modular enough that a new asset class or market does not require touching
  the valuation pipeline.

V2 phase 1 implements **Historical Close Estimation** only. Intraday
estimation is explicitly out of scope for this phase.

## 2. Goals

1. Value different asset classes (equity, ETF, bond ETF, cash) with one engine.
2. Handle market sessions, trading calendars and price dates correctly.
3. Persist what can be obtained, completely and immutably: holdings snapshots,
   prices, FX, official NAV, estimate runs. Historical holdings are **optional**,
   not a prerequisite: the latest obtainable quarter is always enough to estimate
   today's NAV, and any older quarters that exist locally (from earlier runs, a
   database import or another source) extend point-in-time analysis.
4. Provide a modular, extensible Python architecture with typed models.
5. Make every estimate traceable and reproducible from stored inputs.
6. Prove correctness through systematic tests, not by "it runs".

## 3. Confirmed design decisions

| # | Area | Decision |
|---|------|----------|
| 1.1 | Asset schema | unified base fields + per-type fields |
| 1.2 | Asset scope | Equity, ETF, Cash, Bond ETF |
| 1.3 | Unknown assets | kept and flagged, never auto-classified as equity |
| 2.1 | Equity return | adjusted close |
| 2.2 | ETF return | adjusted close |
| 2.3 | Bond ETF return | adjusted close / reliable total-return data |
| 2.4 | Cash return | interest-rate model |
| 2.5 | FX | everything converted to CNY |
| 2.6 | Attribution | Shapley split of local vs FX |
| 3.1 | Estimation mode | historical close |
| 3.2 | Calendar | market calendar module + official checks |
| 3.3 | Staleness | slightly stale prices usable but lower quality; severely stale excluded |
| 4.1 | Database | SQLite |
| 4.2 | History | all inputs and results stored |
| 4.3 | DB scope | shared by the whole Fund Analyzer |
| 4.4 | File layout | code / database / cache / exports / reports / docs separated |
| 5.1 | Architecture | Data / Analysis / Database / GUI layers |
| 5.2 | Models | dataclasses / typed models |
| 5.3 | Errors | Fatal / Asset-level / Warning |
| 6.1 | Tests | unit, integration, regression, acceptance |
| 6.2 | Coverage | ≥ 90 % statement coverage on core financial modules |
| 6.3 | Network tests | run separately from the ordinary suite |
| 6.4 | GUI tests | acceptance on Windows |

## 4. Architecture

```
        ┌─────────────┐
        │     GUI     │  Tk windows: estimate, backtest, quality
        └──────┬──────┘
               │ (reads results only)
        ┌──────▼──────┐
        │   Analysis  │  returns/ valuation/ backtesting/ nav_estimator
        └──┬───────┬──┘
           │       │
   ┌───────▼──┐ ┌──▼────────┐
   │   Data   │ │ Database  │  providers → repositories
   └───────┬──┘ └──┬────────┘
           │       │
        ┌──▼───────▼──┐
        │    Models   │  dataclasses shared by every layer
        └─────────────┘
```

Dependency rules:

* `fa/models` imports nothing from the rest of the package.
* `fa/data` may import `models`, `errors`, `config`, `utils`.
* `fa/analysis` may import `models`, `errors`, `utils`, `data` **read-only**
  (it never writes files itself; orchestration writes).
* `fa/gui` imports `analysis`/`pipeline` only, never providers.
* `fa/analysis/nav_estimator.py` is an **orchestrator**: it does not download,
  parse, compute per-asset returns, or write SQL.

## 5. Conventional meanings

| Concept | Definition |
|---------|------------|
| `trade_date` | China trading day on which the *user* acts (Beijing time, 15:00 cutoff) |
| `target_nav_date` | China trading day whose NAV is being estimated |
| `base_nav_date` | last published official NAV date used as the base |
| `holding_date` | quarter end the disclosed weights describe |
| `disclosure_date` | date those weights became public (may be estimated) |
| `price_date` | market date of a close price |
| `weight` | fraction of fund NAV (`0.1043` = 10.43 %), never renormalised |
| `coverage_ratio` | Σ weights of assets with usable valuation data |
| `uncovered_weight` | `max(0, 1 − Σ weight)` — unknown part, never assumed 0 % |

All datetimes are timezone-aware. Naive values are interpreted as Beijing time
only where explicitly documented (user input in the GUI/CLI).

## 6. Repository layout (target)

```
fa/
  models/        asset.py holding.py market_data.py return_result.py estimation.py
  data/          holdings/ market/ fx/ providers/ database/
  analysis/      returns/ valuation/ backtesting/ nav_estimator.py
  utils/         calendars/ data_quality.py
  gui/           qdii_estimate.py backtest.py
data/
  database/fund_analyzer.db
  cache/
  exports/{holdings,estimations,backtests}/
output/
  reports/  charts/
docs/
  architecture/  technical_specification/  development/
```

Legacy V1 modules stay where they are and keep working; migration into the V2
layout is incremental (see `07_development_roadmap.md`).

## 7. Document index

| File | Content |
|------|---------|
| `00_overview.md` | this document |
| `01_asset_data_models.md` | asset, holding, price, return and estimation models |
| `02_return_calculation.md` | formulas, worked examples, attribution |
| `03_market_calendar_and_alignment.md` | calendars, price alignment, staleness |
| `04_database_schema.md` | SQLite schema, ER diagram, migrations |
| `05_module_interfaces.md` | module I/O, error taxonomy, orchestration |
| `06_testing_and_acceptance.md` | test layers, cases, coverage, acceptance |
| `07_development_roadmap.md` | phases V2.0–V2.4 and dependencies |
