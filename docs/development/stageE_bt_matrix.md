# Stage E — BT-01…BT-14 evidence matrix

Stage: V2.3.2 Stage E (integration and acceptance). Each row names the criterion
from `docs/technical_specification/V2/10_v232_backtest_engine.md` §11 and the
artefact that demonstrates it. Acceptance is the lead's decision; this matrix
records evidence, not a verdict.

**Acceptance record:** V2.3.2 Stage E accepted 2026-11-21 — BT-01…BT-14 all
PASS, the regression set stayed green (423 BT-related tests OK, full suite
1131 OK), and core coverage is 704/708 = 99.4 % (E-12).

| ID | Criterion (short) | Evidence | Result |
| --- | --- | --- | --- |
| BT-01 | Every engine price/FX read is PIT | `fa/backtesting/data_access.py` (`prices` → `Repository.prices_as_of`, `fx` → `fx_as_of`, no `load_prices`); `tests/test_backtest_data_access.py::PriceAccessTests::test_load_prices_is_never_called`; `tests/test_backtest_engine.py::IsolationTests` (AST scan); `scripts/smoke_backtest_real.py` trade-price check | PASS |
| BT-02 | A price published later cannot change an earlier decision | `tests/test_backtest_pit.py::UnpublishedIsolationTests` (adversarial pair 300 vs 999 published `2027-01-01` ⇒ identical trades and equity) | PASS |
| BT-03 | Same spec + same data ⇒ same run | `tests/test_backtest_runner.py::test_identical_data_reuses_the_stored_run`; `tests/test_backtest_engine.py::RebalanceTests` determinism; `scripts/accept_backtest_synthetic.py` C5 | PASS |
| BT-04 | Repeat request ⇒ `created=False`, no rows written | `tests/test_backtest_runner.py::test_identical_data_reuses_the_stored_run`; `scripts/accept_backtest_synthetic.py` C5; `scripts/smoke_backtest_real.py` idempotency check; live demo run (`config/backtests/stagee_demo.json` submitted twice, second call `created=False`, run count unchanged) | PASS |
| BT-05 | Costs match hand values and appear on every trade | `tests/test_backtest_costs.py` (13 tests, hand formulas); `tests/test_backtest_engine.py::ExecutionPriceTests`/`CostTests`; `scripts/accept_backtest_synthetic.py` C2; report cost table in `output/reports/<run_id>.md` | PASS |
| BT-06 | Cash never negative; unfunded buys scaled | `tests/test_backtest_engine.py::ExecutionTests`; `scripts/accept_backtest_synthetic.py` C2 (`0 ≤ cash ≤ 0.01`); `scripts/smoke_backtest_real.py` cash check | PASS |
| BT-07 | Metrics equal hand values, `None` where undefined | `tests/test_backtest_metrics.py` (16 tests, hand formulas, `None` cases) | PASS |
| BT-08 | Persistence round-trips | `tests/test_backtest_runner.py` round trip; `scripts/accept_backtest_synthetic.py` C5; `scripts/smoke_backtest_real.py` reload + metrics equality | PASS |
| BT-09 | Migration 004 additive/idempotent/rollback on frozen baseline | `tests/test_migration_004.py` (9 tests incl. fault injection and baseline hash) | PASS |
| BT-10 | No network import inside `fa/backtesting/`; suite makes no HTTP request | `tests/test_backtest_engine.py::IsolationTests` (AST: `sqlite3`, `requests`, `yfinance`, `akshare`, `random`, `os`, `load_prices`, `execute*`); `fa/__init__.py` lazy exports; suite runs offline | PASS |
| BT-11 | Estimation subsystem and V2.2 interfaces untouched | The ZIP has no `.git`, so reproducibility is by content hash: `fa/analysis/estimation_backtest.py` = `6440C73D8893177041165B2A9F78B67ED62361DCF019C0590CCD2CC2EB203B8D`, `fa/analysis/nav_estimator.py` = `B43443FC23A64919833463A8EF5551E9EEF7F02F4F6BA5BB073C6F0DD147C91F` (unchanged in Stage D and E; `nav_estimator.py` carries the accepted Stage 2.5 FX integration). | PASS |
| BT-12 | Docs match code and counts | This matrix, spec §19, `07_development_roadmap.md`, `06_testing_and_acceptance.md`, `README.md` all state the current 846-test count, keep the historical counts explicit, and describe the Stage E artefacts | PASS |
| BT-13 | Fill at execution close, visible at close + 30 min | `tests/test_backtest_execution_instant.py` (bar invisible at the close, visible at +30 min, fill equals the close); `tests/test_price_pit.py` execution-instant tests | PASS |
| BT-14 | `data_fingerprint` is a content hash | `tests/test_backtest_fingerprint.py` (future-revision invariance, published-revision change, per-session reads, per-venue momentum windows, no `fetched_at`/`revision_id`); `tests/test_backtest_run_identity.py` | PASS |

## Stage E artefacts

| Item | Path |
| --- | --- |
| Synthetic acceptance (E-1) | `scripts/accept_backtest_synthetic.py` → `docs/development/stageE_synthetic_acceptance.md` |
| Working-database audit (E-2a) | `scripts/audit_working_db.py` → `docs/development/stageE_working_db_audit.md` |
| Real-data smoke (E-2b) | `scripts/smoke_backtest_real.py` → `docs/development/stageE_real_smoke.md` |
| CLI / report (E-3) | `scripts/backtest_report.py` (`run`/`list`/`show`), `config/backtests/stagee_demo.json`, `output/reports/<run_id>.md` |
| Retention audit (E-4) | `scripts/audit_backtest_retention.py` → `docs/development/stageE_retention_audit.md` |
| Coverage (E-5) | `scripts/coverage_core.py` → `docs/development/stageE_coverage.md` |
| Docs (E-5) | spec §19, roadmap, `06_testing_and_acceptance.md`, README |

## Coverage gate (closed in the round-8 fixes)

The stdlib tracer (`coverage.py` is not a dependency) now reports **99.4 %**
(704/708) over the documented target set; every module is above the 90 %
threshold. The per-module numbers and the four remaining line numbers are in
`docs/development/stageE_coverage.md` and spec §19.4; those four lines are
defensive branches that the type contract makes unreachable from public API
calls, and each is explained there.
