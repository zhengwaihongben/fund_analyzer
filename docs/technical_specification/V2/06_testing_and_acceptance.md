# V2 — Testing & Acceptance Criteria

## 1. Layers

| Layer | Purpose | Example |
|-------|---------|---------|
| Unit | one function, hand-computed expectation | equity return, FX conversion, Shapley split |
| Integration | modules working together | holdings → prices → FX → NAV → DB |
| Regression | old behaviour still works | the existing V1 suite (113 tests) |
| Acceptance | the feature really meets the requirement | PIT, calendar, coverage, reproducibility, GUI |

Rules:

* Keep every existing V1 test green.
* Core financial modules target **≥ 90 % statement coverage**.
* Network tests are separated from the ordinary suite
  (`tests/network/`, opt-in via `FA_NETWORK_TESTS=1`, never run by default).
* GUI tests run on Windows and are part of acceptance.

## 2. Required test cases

| Test case | Expected result |
|-----------|-----------------|
| PIT holdings | a quarter disclosed after the target date is never used |
| Equity return | `P1/P0 − 1` matches hand computation |
| ETF return | adjusted close used, raw close kept |
| Bond ETF return | total-return handling correct, price-only rejected |
| Cash return | `(1 + y/365)^Δd − 1` matches hand computation |
| FX conversion | direction correct (rising USD/CNY raises CNY return) |
| Portfolio return | Σ w·r weighted correctly; weights never renormalised |
| Shapley attribution | local + FX == total contribution |
| Market calendar | weekends and 2025/2026 holidays correct for CN/US/HK/JP |
| Stale price | kept with warning at 1–2 sessions, excluded beyond the cap |
| Missing FX | asset excluded, never assumed CNY |
| Coverage | uncovered weight reported, status `partial` |
| Reproducibility | an old run is unchanged by new downloads |
| Database migration | an older DB upgrades without data loss |
| GUI | estimate window shows quality/coverage/warnings on Windows |

## 3. Coverage targets

Measured with `coverage.py` over `fa/analysis/returns/`,
`fa/analysis/valuation/`, `fa/analysis/nav_estimator.py`,
`fa/analysis/valuation_dates.py`, `fa/utils/data_quality.py`:

* statement coverage ≥ 90 % for the modules above;
* coverage is a hygiene metric, not proof — every financial formula also needs
  a hand-computed expected value in a test.

Run:

```bash
python -m coverage run -m unittest discover -s tests -t .
python -m coverage report --include="fa/analysis/*,fa/utils/data_quality.py"
```

## 4. Test data

* Synthetic fixtures only for the ordinary suite (no network).
* Real fund data (000369) used for a small integration test with recorded
  fixtures in `tests/fixtures/`, so results are deterministic.
* Network tests may hit Yahoo/Eastmoney but must be tolerant of failure and are
  never part of CI.

## 5. Acceptance criteria per phase

| Phase | Acceptance |
|-------|-----------|
| V2.0 | models exist and validate; spec docs written; directory layout created; unit tests for models/quality pass |
| V2.1 | SQLite schema + migrations work; repository round-trips snapshots/prices/fx/nav/runs; a run is reproducible from stored inputs; calendars correct; data quality graded; a latest-quarter-only portfolio is enough to value NAV (no archive-history download required — history is optional and only widens point-in-time coverage) |
| V2.2 | four asset calculators + portfolio valuation + Shapley attribution; coverage/status correct; integration test end-to-end |
| V2.3 | backtest over stored runs: MAE/RMSE/mean error/max abs error, grouped by asset type, market and coverage bucket |
| V2.4 | GUI shows estimate with quality/coverage/warnings; backtest window; README updated; full regression suite green |

## 6. Definition of done for a V2 feature

1. Dataclass contract added/updated in `fa/models`.
2. Spec section in `docs/technical_specification/V2/` updated first.
3. Unit tests with hand-computed numbers.
4. Integration test through the repository.
5. Acceptance test matching the table above.
6. `python -m unittest discover -s tests -t .` green, no skipped-by-accident
   tests, and no network access during the run.
