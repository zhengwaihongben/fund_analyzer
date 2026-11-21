# V2 — Asset & Data Models

Implemented in `fa/models/` (`asset.py`, `holding.py`, `market_data.py`,
`return_result.py`, `estimation.py`). This document is the authoritative field
definition; the code must match it.

## 1. AssetType

| Value | Meaning | Priced? | Supported in V2? |
|-------|---------|---------|------------------|
| `equity` | listed share | yes | yes |
| `etf` | exchange-traded fund | yes | yes |
| `bond_etf` | bond ETF / bond index fund | yes | yes |
| `cash` | deposit / money-market balance | no (rate model) | yes |
| `unknown` | anything not identifiable | no | **no — excluded, flagged** |

Rules:

* Parsing is defensive (`AssetType.parse`): `"Stock"`, `"stock"`, `"equity"`
  all map to `equity`; unrecognised or empty values map to `unknown`.
* `unknown` is retained on the snapshot and counted in
  `uncovered_weight`. It is **never** silently valued as an equity.
* Classification order (`fa/data/asset_classifier.py`): manual override →
  asset type **declared by the data source** → formal-name markers
  (`ETF` / `债券ETF` / `现金` …) → `unknown`. A ticker's *shape* is never
  evidence: an ETF, a fund and an equity can share the same symbol format, so
  a plain code with no declaration stays `unknown` (V2.2.2). The QDII holdings
  builder declares `equity` only because the source table is literally
  「股票投资明细」, which is a statement by the source, not a guess from the code.

## 2. Asset

Common fields:

| Field | Type | Notes |
|-------|------|-------|
| `asset_id` | `str` | unique within a snapshot; usually the market ticker |
| `name` | `str` | display name (Chinese names allowed) |
| `asset_type` | `AssetType` | see above |
| `ticker` | `str \| None` | market code as published by the source |
| `market` | `str \| None` | `US`, `HK`, `CN`, `JP`, `CH`, `GB`, ... |
| `currency` | `str` | ISO 4217, upper case; `""` when unknown |
| `weight` | `float` | fraction of fund NAV, `0 ≤ w ≤ 1` |
| `holding_date` | `date \| None` | quarter end of the weight |
| `source` | `str` | where the row came from (`eastmoney-page`, ...) |

Per-type fields:

| Field | Applies to | Notes |
|-------|-----------|-------|
| `price_type` | equity, etf, bond_etf | `adjusted_close` (default) or `total_return` |
| `exchange` | etf, bond_etf | venue used for symbol resolution |
| `benchmark` | bond_etf | optional index for diagnostics |
| `interest_rate_source` | cash | e.g. `policy_rate`, `manual` |
| `interest_rate` | cash | annualised decimal (`0.015` = 1.5 %) |
| `extra` | any | free-form provider metadata |

Validation (`AssetValidationError`):

* `asset_id` and `name` non-empty;
* `weight` finite, `≥ 0`, `≤ 1` (a percentage such as `10.43` is rejected —
  this catches the classic "percent vs fraction" unit bug);
* `holding_date` parses as an ISO date.

Derived properties: `is_supported`, `needs_price`, `needs_interest_rate`,
`uses_fx` (true when currency is known and not CNY).

## 3. HoldingSnapshot

| Field | Type | Notes |
|-------|------|-------|
| `fund_code` | `str` | e.g. `000369` |
| `quarter` | `str \| None` | `YYYYQn` |
| `holding_date` | `date` | quarter end |
| `disclosure_date` | `date` | when weights became public |
| `disclosure_date_estimated` | `bool` | true when derived (`quarter end + lag`) |
| `source` | `str` | holdings source |
| `assets` | `list[Asset]` | |
| `warnings` | `list[str]` | |

Derived: `total_weight`, `uncovered_weight`, `by_type()`, `is_disclosed_by(day)`.

`validate()` raises `HoldingValidationError` when:

* duplicate `asset_id`s exist,
* `holding_date` or `disclosure_date` is missing,
* `disclosure_date < holding_date`,
* `total_weight > 1.0`.

## 4. Price data

### 4.1 PricePoint

| Field | Type | Notes |
|-------|------|-------|
| `price_date` | `date` | market date of the close |
| `close` | `float` | raw close |
| `adj_close` | `float \| None` | dividend/split adjusted close |
| `price_timestamp` | `datetime \| None` | exact close timestamp (tz-aware) |
| `retrieved_at` | `datetime` | when we downloaded it (defaults to now, UTC) |
| `market` | `str` | |
| `currency` | `str` | |
| `available_price_type` | `str` | what the source offered: `close+adj_close`, `close`, `fx_close`, … (filled at acquisition time) |
| `used_price_type` | `str` | which column the calculator actually used; empty until a calculator reads the point |
| `source` | `str` | vendor name |

`value` returns `adj_close` when present, else `close`. `price_type` is kept as a
backward-compatible alias for `used_price_type or available_price_type`, and
`with_used_price_type(kind)` returns a copy that records the choice.

Available and used are deliberately separate: a source can offer both `Close` and
`Adj Close` while the calculator ends up using only one of them, and for
reproducibility we need to know both what was on the table and what was taken.
The estimator's per-run input snapshot records both fields per asset
(`available_price_type`, `used_price_type`, plus `price_type_used` on the result).

### 4.2 PriceSeries

`point_on_or_before(day)` returns the last point with `price_date <= day`
(never a later one — the reproducibility rule). `price_on_or_before(day)`
returns `(value, date)`.

### 4.3 FxSeries

Holds CNY-per-unit-of-foreign-currency rates (`USD/CNY = 7.20` means 1 USD =
7.20 CNY). `rate_on_or_before(day)` mirrors `PriceSeries`.

## 5. Return result models

### 5.1 AssetReturnResult

| Field | Type |
|-------|------|
| `asset_id` | `str` |
| `status` | `ReturnStatus` |
| `local_return`, `fx_return`, `return_cny` | `float \| None` |
| `weight` | `float` |
| `base_price_date`, `target_price_date` | `date \| None` |
| `base_fx_date`, `target_fx_date` | `date \| None` |
| `currency` | `str` |
| `local_contribution`, `fx_contribution` | `float \| None` (Shapley) |
| `warnings` | `list[str]` |

`ReturnStatus`: `ok`, `missing-price`, `missing-target-price`,
`missing-base-price`, `stale-price`, `stale-base-price`, `missing-currency`,
`missing-fx`, `stale-fx`, `stale-base-fx`, `unsupported-asset`.

Only `ok` contributes to the return; only `ok` and the stale statuses
contribute to `coverage_ratio` (they are usable but lower quality).

### 5.2 PortfolioReturnResult

| Field | Notes |
|-------|-------|
| `status` | `estimated` / `partial` / `insufficient-data` |
| `estimated_return` | Σ weighted CNY returns (covered part only) |
| `covered_weight`, `uncovered_weight`, `coverage_ratio` | coverage metadata |
| `local_contribution`, `fx_contribution` | Shapley sums |
| `holdings_included`, `holdings_excluded` | counts |
| `assets` | per-asset results |

`status` is `partial` whenever `uncovered_weight > 0.001` or any asset was
excluded — a 46 %-coverage estimate must never be labelled `estimated`.

## 6. Estimation models

`EstimationRun` (identity): `run_id`, `fund_code`, `mode`, `model_version`,
`trade_date`, `target_nav_date`, `base_nav_date`, `as_of`, `created_at`,
`holdings_quarter`, `holdings_date`, `disclosure_date`,
`disclosure_date_estimated`, `holdings_source`, `look_ahead_safe`, `timezone`,
`cutoff`.

`EstimationResult`: `run_id`, `status`, `base_nav`, `estimated_nav`,
`estimated_return`, `coverage_ratio`, `uncovered_weight`,
`local_contribution`, `fx_contribution`, `data_quality`, `quality_score`,
`portfolio`, `warnings`, `message`.

`MODEL_VERSION = "2.2.0"` — bump on any change to the valuation maths so that
stored runs remain explainable.

`LookAheadStatus` replaces the old `look_ahead_safe: bool` on `EstimationRun`:
`verified-safe` (disclosure date known and before the target),
`estimated-disclosure` (the filing date is still an estimate),
`unsafe`, `unknown`. `run.look_ahead_safe` remains as a convenience property
that is true only for `verified-safe`.

`AssetReturnResult.status` uses `ReturnStatus`, which separates two questions:
`is_ok` ("nothing to report") and `is_usable` ("the number may be used, though
its data is slightly stale"). Coverage, contributions and quality gate on
`is_usable`; gating them on `is_ok` would silently drop every stale asset
instead of penalising its quality.

## 7. Python interfaces

```python
Asset(asset_id, name, asset_type=AssetType.UNKNOWN, ticker=None, market=None,
      currency="", weight=0.0, holding_date=None, source="", price_type=None,
      exchange=None, benchmark=None, interest_rate_source=None,
      interest_rate=None, extra=None)
Asset.from_dict(d) -> Asset
asset.to_dict() -> dict

HoldingSnapshot(fund_code, assets=[], quarter=None, holding_date=None,
                disclosure_date=None, disclosure_date_estimated=False,
                source="", warnings=[])
snapshot_from_rows(fund_code, rows, *, quarter=None, holding_date=None,
                   disclosure_date=None, disclosure_date_estimated=False,
                   source="") -> HoldingSnapshot

PricePoint(price_date, close, adj_close=None, price_timestamp=None,
           retrieved_at=None, market="", currency="", price_type="adjusted_close",
           source="")
PriceSeries(ticker, market="", currency="", source="", points=[])
FxSeries(currency, source="", points=[])
FxSeries.from_pairs(currency, [(date, rate), ...], source="") -> FxSeries

shapley_contributions(weight, local_return, fx_return) -> (c_local, c_fx)
```

## 8. Acceptance criteria

* Every holding produced by the data layer converts to `Asset` without loss of
  ticker, currency, market or weight.
* The four supported classes are recognised from real 000369 data; an
  unrecognised code becomes `unknown` and is excluded, not valued as equity.
* Percent-vs-fraction and negative weights raise `AssetValidationError`.
* Per-type fields extend without changing the base class.
