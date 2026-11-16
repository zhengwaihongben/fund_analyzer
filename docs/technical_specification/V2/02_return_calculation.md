# V2 — Return Calculation

All return maths lives in `fa/analysis/returns/` (V2.2) and returns
`AssetReturnResult` objects (see `01_asset_data_models.md`). This document fixes
the formulas; code must not invent alternatives.

## 1. Notation

| Symbol | Meaning |
|--------|---------|
| `w_i` | weight of asset *i* (fraction of NAV) |
| `P_i,t` | price used at date *t* (adjusted close when available) |
| `FX_c,t` | CNY per unit of currency *c* at date *t* |
| `r_i,local` | local-currency return of asset *i* |
| `r_i,fx` | FX return of asset *i* |
| `r_i,CNY` | total CNY return of asset *i* |
| `R_fund` | portfolio return |

Direction convention: **`FX = CNY per unit of foreign currency`**
(`USD/CNY = 7.20` ⇒ 1 USD = 7.20 CNY).

## 2. Equity / ETF / Bond ETF

```
r_local = P_target / P_base − 1
```

* `P` is the **adjusted close** (dividends and splits reinvested). The raw
  close is kept on the same `PricePoint` for data checks and must be reported
  when `price_type == "adjusted_close"` was substituted for missing adjusted
  data.
* Bond ETFs may use a reliable total-return series instead; a bond ETF's price
  return alone is **not** accepted as its total return.
* Individual bonds and derivatives are out of scope for V2.

## 3. Cash

Daily compounding with an annualised rate `y` over `Δd` calendar days:

```
1 + r_cash = ∏_{d=1..Δd} (1 + y/365)  =  (1 + y/365)^Δd
```

Simplification when `y` is constant: `r_cash = (1 + y/365)^Δd − 1`.

If no rate is available, the asset is marked with
`interest_rate_source = "assumption"` **and** the assumed rate is written into
the estimate record. Cash is never silently assigned 0 %.

## 4. FX conversion

```
1 + r_CNY = (1 + r_local) · (1 + r_fx)
r_fx = FX_target / FX_base − 1
```

* CNY and RMB are the base: `r_fx = 0`.
* Unknown currency ⇒ `missing-currency`, asset excluded (never assumed CNY).
* Missing FX ⇒ `missing-fx`; stale FX ⇒ `stale-fx` / `stale-base-fx` with a
  quality penalty.

Worked example (000369 / LLY, weight 0.1043):

```
P_base = 100.00, P_target = 110.00  ⇒ r_local = +10.00 %
FX_base = 7.00, FX_target = 7.07    ⇒ r_fx    =  +1.00 %
r_CNY = 1.10 × 1.01 − 1             = +11.10 %
contribution = 0.1043 × 0.1110      =  +1.1577 %
```

## 5. Portfolio return

```
R_fund = Σ_{i: status ok} w_i · r_i,CNY
```

Two rules:

1. **Never renormalise** the known weights to 100 %. A 46 %-coverage portfolio
   estimates 46 % of the fund; the other 54 % is *unknown*.
2. `coverage_ratio = Σ_{i: usable} w_i` and `uncovered_weight = max(0, 1 − Σ w_i)`.
   Both are reported, and `status = partial` whenever `uncovered_weight > 0.001`.

## 6. Shapley attribution

To make the local and FX parts add up exactly to the CNY contribution:

```
C_local,i = w_i · r_local,i · (1 + r_fx,i / 2)
C_fx,i    = w_i · r_fx,i    · (1 + r_local,i / 2)
```

Then

```
C_local,i + C_fx,i = w_i · (r_local + r_fx + r_local·r_fx)
                   = w_i · r_CNY,i
```

Portfolio attribution sums: `local_contribution = Σ C_local,i`,
`fx_contribution = Σ C_fx,i`, and
`local_contribution + fx_contribution == R_fund` (to floating-point tolerance).

There is therefore **no separate interaction term** in V2 — the V1 code's
`interaction_contribution` is superseded by the symmetric split.

Worked check with the example above:

```
C_local = 0.1043 × 0.10 × 1.005 = 0.01048215
C_fx    = 0.1043 × 0.01 × 1.05  = 0.00109515
sum     = 0.0115773  = 0.1043 × 0.1111
```

## 7. Estimate of NAV

```
NAV_target = NAV_base · (1 + R_fund)
```

`NAV_base` is the last official NAV published on or before `target_nav_date`.
If no such NAV exists, the run fails with `no-nav` (fatal error class) rather
than inventing a base.

The estimated NAV is stored separately from the official NAV and is never
written into the official series.

## 8. Missing / stale data

| Condition | Status | Effect |
|-----------|--------|--------|
| no price series at all | `missing-price` | excluded |
| no price on/before target | `missing-target-price` | excluded |
| no price on/before base | `missing-base-price` | excluded |
| target price older than policy | `stale-price` | included + warning + quality penalty |
| base price older than policy | `stale-base-price` | included + warning + quality penalty |
| currency unknown | `missing-currency` | excluded |
| no FX on/before target | `missing-fx` | excluded |
| FX base missing | `missing-fx` | excluded |
| stale FX | `stale-fx` / `stale-base-fx` | included + warning |

Round only for display: internal maths keeps full precision; stored values are
rounded to 8 decimals, display to 2–4 as the GUI chooses.

## 9. Acceptance criteria

* Each supported asset class has its own calculator function, unit-tested
  against hand-computed values (the four examples in §2–§4 appear as tests).
* FX direction is verified by a test where a rising `USD/CNY` increases the CNY
  return of a flat USD asset.
* `local_contribution + fx_contribution` equals `R_fund` exactly (test uses
  `assertAlmostEqual` on the same numbers).
* Missing inputs never produce a plausible-looking complete estimate: coverage
  and status both reflect the gap.

## 10. V2.2 implementation map

| Module | Responsibility |
|--------|----------------|
| `fa/analysis/returns/base.py` | `ReturnCalculator` protocol, `priced_return()`, `convert_to_cny()`, `series_from_frame()` |
| `fa/analysis/returns/equity.py` | equity, adjusted close, raw-close fallback allowed |
| `fa/analysis/returns/etf.py` | ETF, exchange/quote-currency identification |
| `fa/analysis/returns/bond_etf.py` | bond ETF, total return required (raw close rejected) |
| `fa/analysis/returns/cash.py` | cash, `(1 + y/365)^Δd − 1`, assumption fallback |
| `fa/analysis/returns/fx.py` | FX return calculator (`compute_fx_return`) |
| `fa/analysis/returns/__init__.py` | registry + `calculate_asset()` dispatch |
| `fa/analysis/valuation/portfolio_valuation.py` | weights, coverage, status, Shapley aggregation |
| `fa/analysis/nav_estimator.py` | orchestrator only: no FX maths, no portfolio maths |

Concrete rules implemented in code:

* **Adjusted close is preferred everywhere.** Equity/ETF may fall back to the raw
  close with the warning `缺少調整後收盤價，改用原始收盤價（僅為近似）`; bond ETFs
  may not, and return `unsupported-asset` instead.
* **Cash never defaults to 0 %.** Without `asset.interest_rate` the calculator
  uses `CASH_ASSUMPTION_RATE` (`FA_CASH_ASSUMPTION_RATE`, default `0.0`), marks
  `rate_source="assumption"` and emits a warning — the rate is stored in the
  record either way.
* **Unknown assets are preserved but never valued** (`unsupported-asset`), so the
  uncovered weight stays honest.
* **Every status name is shared** by all calculators (`ReturnStatus`), so the
  portfolio layer never branches on asset class.

