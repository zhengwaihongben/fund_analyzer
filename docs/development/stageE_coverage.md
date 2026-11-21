# Stage E — core module coverage (stdlib tracer)

Measured by `scripts/coverage_core.py` with `sys.settrace` while running the
whole `unittest` suite in-process, because the project does not depend on the
`coverage` package. **Line coverage only, not branch coverage.**

- Generated: 2026-11-21T11:13:29+00:00
- Python: 3.10.9
- Tests run: 992, failures: 0, errors: 0
- GUI tests excluded from the measurement: test_gui (Tk teardown aborts the interpreter under `sys.settrace`)
- Threshold: 90%

| Module | Executable lines | Executed | Coverage |
| --- | --- | --- | --- |
| `fa/analysis/returns/base.py` | 160 | 157 | 98.1% |
| `fa/analysis/returns/equity.py` | 14 | 14 | 100.0% |
| `fa/analysis/returns/etf.py` | 22 | 22 | 100.0% |
| `fa/analysis/returns/bond_etf.py` | 14 | 14 | 100.0% |
| `fa/analysis/returns/cash.py` | 39 | 39 | 100.0% |
| `fa/analysis/returns/fx.py` | 12 | 12 | 100.0% |
| `fa/analysis/valuation/portfolio_valuation.py` | 43 | 43 | 100.0% |
| `fa/analysis/nav_estimator.py` | 291 | 290 | 99.7% |
| `fa/analysis/valuation_dates.py` | 35 | 35 | 100.0% |
| `fa/utils/data_quality.py` | 78 | 78 | 100.0% |

- **Total (documented target set): 704/708 = 99.4%**

Every core module meets the 90% threshold.

## Missing executable lines

Line numbers reported by the tracer as executable but never executed.
Use them to aim the new tests; the gate is per module, not aggregate.

| Module | Missing | Line numbers |
| --- | --- | --- |
| `fa/analysis/returns/base.py` | 3 | 45, 227, 237 |
| `fa/analysis/nav_estimator.py` | 1 | 362 |
| `fa/backtesting/engine.py` | 40 | 85, 110, 112, 114, 116, 191, 223, 232, 265, 275, 284, 295, 296, 316, 357, 372, 404, 405, 427, 428, 445, 450, 455, 457, 462, 464, 493, 499, 520, 527, 528, 529, 530, 531, 533, 535, 536, 538, 542, 588 |
| `fa/backtesting/metrics.py` | 5 | 30, 31, 40, 60, 67 |
| `fa/backtesting/runner.py` | 11 | 178, 179, 213, 269, 289, 293, 369, 371, 446, 479, 487 |
| `fa/backtesting/benchmark.py` | 10 | 74, 155, 157, 166, 201, 204, 205, 225, 228, 229 |
| `fa/backtesting/data_access.py` | 10 | 66, 102, 123, 134, 137, 146, 149, 152, 158, 262 |
| `fa/backtesting/models.py` | 20 | 44, 204, 221, 274, 304, 306, 317, 318, 319, 327, 347, 349, 392, 419, 494, 497, 502, 506, 511, 515 |
| `fa/data/price_pipeline.py` | 26 | 111, 112, 122, 123, 156, 183, 256, 295, 318, 327, 328, 360, 362, 388, 400, 401, 415, 416, 436, 438, 454, 455, 457, 458, 472, 473 |
| `fa/data/fx_pipeline.py` | 19 | 162, 183, 271, 396, 405, 406, 407, 409, 410, 451, 452, 475, 476, 478, 479, 501, 502, 538, 539 |

## Additional modules (information only, not part of the documented gate)

| Module | Executable lines | Executed | Coverage |
| --- | --- | --- | --- |
| `fa/backtesting/costs.py` | 49 | 49 | 100.0% |
| `fa/backtesting/engine.py` | 393 | 353 | 89.8% |
| `fa/backtesting/metrics.py` | 111 | 106 | 95.5% |
| `fa/backtesting/runner.py` | 160 | 149 | 93.1% |
| `fa/backtesting/benchmark.py` | 111 | 101 | 91.0% |
| `fa/backtesting/data_access.py` | 97 | 87 | 89.7% |
| `fa/backtesting/models.py` | 318 | 298 | 93.7% |
| `fa/data/price_pipeline.py` | 239 | 213 | 89.1% |
| `fa/data/fx_pipeline.py` | 246 | 227 | 92.3% |

- Extra set: 1583/1724 = 91.8%
