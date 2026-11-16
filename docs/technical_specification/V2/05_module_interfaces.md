# V2 — Module Interfaces

Layering: **Data / Analysis / Database / GUI**, with dataclasses as the only
shared vocabulary.

## 1. Module map

```
fa/
  models/            asset.py holding.py market_data.py return_result.py estimation.py
  data/
    holdings/        fetch + point-in-time snapshot selection
    market/          price providers (yahoo chart, yfinance bridge)
    fx/              CNY-per-unit FX series
    providers/       shared HTTP helpers, symbol resolution, rate limiting
    database/        schema.py connection.py repository.py
  analysis/
    returns/         base.py equity.py etf.py bond_etf.py cash.py fx.py
    valuation/       portfolio_valuation.py (weighting + coverage + Shapley)
    backtesting/     runs + metrics + grouping
    nav_estimator.py orchestrator only
    valuation_dates.py  trade date / target date / base date resolution
  utils/
    calendars/       base.py us.py china.py hong_kong.py japan.py
    data_quality.py  quality grade from coverage + staleness
  gui/               qdii_estimate.py backtest.py
```

V1 modules (`fa/data/holdings.py`, `fa/analysis/portfolio_return.py`, ...) keep
working during the migration; new code must not import from them.

## 2. Input / output contract

| Module | Input | Output |
|--------|-------|--------|
| Holdings history | fund code, target date | `HoldingSnapshot` (point-in-time, selected from locally available quarters), disclosure metadata |
| Market data | `Asset`, date range | `PriceSeries` |
| FX data | currency, date range | `FxSeries` |
| Market calendar | market, date | trading session / `datetime` |
| Data quality | prices, FX, holdings | `QualityReport` |
| Return calculator | `Asset`, market data | `AssetReturnResult` |
| Portfolio valuation | `HoldingSnapshot`, `[AssetReturnResult]` | `PortfolioReturnResult` |
| NAV estimator | base NAV, `PortfolioReturnResult`, dates | `EstimationRun` + `EstimationResult` |
| Repository | models | database rows / models |
| Backtesting | estimated NAV, official NAV | error metrics |

## 3. Return calculator interface

```python
class ReturnCalculator(Protocol):
    asset_types: frozenset[AssetType]

    def calculate(
        self,
        asset: Asset,
        *,
        base_date: date,
        target_date: date,
        prices: PriceSeries | None,
        fx: FxSeries | None,
        calendar: MarketCalendar | None = None,
    ) -> AssetReturnResult: ...
```

Rules:

* Return the same dataclass shape regardless of asset class.
* Use `PriceSeries.point_on_or_before` style selection only — never a future point.
* Report which dates were actually used.
* Never raise for missing data: return a status. Raise only for programming
  errors (e.g. negative weight already impossible by validation).

## 4. Orchestrator contract (`nav_estimator.py`)

It may: resolve dates, ask the data layer for a PIT snapshot, ask the return
calculators for per-asset results, call portfolio valuation, build
`EstimationRun`/`EstimationResult`, and ask the repository to persist.

It may **not**: perform HTTP, parse provider HTML/JSON, apply FX maths itself,
decide staleness thresholds inline, or write SQL.

```python
def estimate_nav(fund_code: str, *, as_of: datetime | None = None,
                 target_nav_date: date | None = None,
                 repository: Repository | None = None) -> EstimationOutcome: ...
```

## 5. Error taxonomy (three tiers)

| Tier | Examples | Handling |
|------|----------|----------|
| **Fatal** | fund does not exist, no base NAV, no PIT holdings at all, DB write failure | abort the run, report a clear message, no estimate written |
| **Asset-level** | missing price, missing FX, unknown currency, unsupported asset type | exclude that asset, lower coverage, keep going |
| **Warning** | slightly stale price, disclosure date is an estimate, base NAV one session old | keep going, record warning, lower quality grade |

Implementation: fatal errors raise subclasses of
`fa.errors.FundAnalyzerError`; asset-level problems are `ReturnStatus` values;
warnings are plain strings stored on the result and the run record.

## 6. Dependency rules (enforced in review)

* `fa.models` imports nothing from the package.
* `fa.data.*` imports `models`, `errors`, `utils`.
* `fa.analysis.*` imports `models`, `errors`, `utils`, and *reads* `data`.
* `fa.gui.*` imports `analysis`/`pipeline` only.
* No module under `fa/analysis` writes files or SQL.

## 7. GUI contract

`QdiiEstimateWindow` displays, from one `EstimationResult` + `EstimationRun`:
estimated NAV and change, coverage ratio, uncovered weight, local/FX
contributions, data quality grade, target/base/market data dates, disclosure date
(with a 「（推算）」 marker when estimated), per-asset rows including status and
actual price dates, and all warnings. A failed estimate shows the reason, never
a blank table pretending success.

## 8. Acceptance criteria

* Every module has a documented input and output (this document).
* Core data crosses module boundaries as dataclasses, not loose dicts.
* `nav_estimator.py` contains no download, no FX arithmetic, no SQL.
* Dependency direction is respected (import-graph test).
* Errors are classified into the three tiers, and each tier has a test.
