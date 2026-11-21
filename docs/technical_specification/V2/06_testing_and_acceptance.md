# V2 — Testing & Acceptance Criteria

## 1. Layers

| Layer | Purpose | Example |
|-------|---------|---------|
| Unit | one function, hand-computed expectation | equity return, FX conversion, Shapley split |
| Integration | modules working together | holdings → prices → FX → NAV → DB |
| Regression | old behaviour still works | the existing V1 suite plus the V2 suites (781 at the end of Stage D round 7; 783 after the Stage E self-heal test; 846 after the Stage E coverage round) |
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

The target set is `fa/analysis/returns/`, `fa/analysis/valuation/`,
`fa/analysis/nav_estimator.py`, `fa/analysis/valuation_dates.py`,
`fa/utils/data_quality.py`, at **≥ 90 % statement coverage**. Coverage is a
hygiene metric, not proof — every financial formula also needs a hand-computed
expected value in a test.

`coverage.py` is **not installed** and adding a dependency for this is not
allowed, so the measurement uses a `sys.settrace` tracer
(`scripts/coverage_core.py`, statement lines only, no branch coverage). The
result is recorded, not assumed:

*Initial Stage E delivery, before Round 8 fixes:* suite **782 tests**;
**88.2 %** (665/754 statement lines) for the documented target set, measured
with a denominator that counted function bodies only; the 90 % gate was
**not met**. Below the threshold at that time: `fa/analysis/returns/base.py`
85.5 %, `fa/analysis/returns/fx.py` 66.7 %, `fa/analysis/nav_estimator.py`
86.6 %. These figures are history, not the current state.

Run:

```bash
python scripts/coverage_core.py            # writes docs/development/stageE_coverage.md
```

Current result (Round 9, Python 3.10.9). The denominator is the whole module —
module-level, class-body and function-body statements, docstrings excluded —
because `sys.settrace()` is installed before `unittest` discovery, so
import-time lines are observable:

| Measurement | Result |
| --- | --- |
| Full test suite | **846 tests** |
| Coverage measurement suite | **835 tests** (11 GUI tests excluded) |
| Executable statements (documented target set) | **708** |
| Executed statements | **704** |
| Overall core target coverage | **99.4 %** |
| Per-module requirement | every target module ≥ 90 % — `returns/base.py` 98.1 %, `nav_estimator.py` 99.7 %, the other eight 100 % |
| Additional modules (information only) | 91.8 % (1583/1724) |

The 11 GUI tests are excluded from the coverage measurement only because Tk
teardown under `sys.settrace` aborts the interpreter; they remain part of the
normal full suite. Four statements in the target set are still never executed
(`returns/base.py` lines 45, 227, 237 and `nav_estimator.py` line 362); each is
a defensive branch documented in the V2.3.2 specification §19.4.
`docs/development/stageE_coverage.md` is the authoritative per-module report.

**The 90 % coverage gate is met.**

Stage status: V2.3.2 Stages A, B, C, D and **E all accepted** (Stage E accepted
2026-11-21); **V2.4 Stages 0–E accepted 2026-11-21**. No further plan version is
scheduled.

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
| V2.2 | four asset calculators + portfolio valuation + Shapley attribution; coverage/status correct; integration test end-to-end; after the V2.2.1 review also: stale status reaches quality, assets are classified (not all equity), currency mismatch is rejected, inputs are stored per `run_id`, and look-ahead status is a four-value enum |
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
