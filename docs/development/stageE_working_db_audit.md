# Stage E — working database audit (E-2a)

- Database: `C:\Users\user\Desktop\Finance\codes\alipay_funds\fund_analyzer\data\database\fund_analyzer.db`
- Opened read-only (`mode=ro`)
- Schema version: 4
- Generated: 2026-11-19T16:31:12+00:00

## Row counts

| Table | Rows |
| --- | --- |
| `market_prices` | 5107 |
| `price_revisions` | 0 |
| `fx_rates` | 30 |
| `fx_revisions` | 0 |
| `official_nav` | 452 |
| `holdings_snapshots` | 1 |
| `estimation_runs` | 25 |
| `strategy_backtest_runs` | 0 |
| `strategy_backtest_metrics` | 0 |
| `strategy_backtest_trades` | 0 |
| `strategy_backtest_equity` | 0 |

## PIT price coverage per ticker

| Ticker | Rows | PIT rows | First | Last | Availability sources | Price types |
| --- | --- | --- | --- | --- | --- | --- |
| `ABBV` | 510 | 501 | 2024-11-18 | 2026-11-17 | inferred:501, unknown:9 | adjusted_close:9, close+adj_close:501 |
| `AMGN` | 510 | 501 | 2024-11-18 | 2026-11-17 | inferred:501, unknown:9 | adjusted_close:9, close+adj_close:501 |
| `AZN` | 517 | 507 | 2024-11-18 | 2026-11-18 | inferred:507, unknown:10 | adjusted_close:10, close+adj_close:507 |
| `JNJ` | 510 | 501 | 2024-11-18 | 2026-11-17 | inferred:501, unknown:9 | adjusted_close:9, close+adj_close:501 |
| `LLY` | 510 | 501 | 2024-11-18 | 2026-11-17 | inferred:501, unknown:9 | adjusted_close:9, close+adj_close:501 |
| `MRK` | 510 | 501 | 2024-11-18 | 2026-11-17 | inferred:501, unknown:9 | adjusted_close:9, close+adj_close:501 |
| `NOVN` | 510 | 510 | 2024-11-18 | 2026-11-18 | inferred:510 | adjusted_close:10, close+adj_close:500 |
| `ROP SW` | 510 | 510 | 2024-11-18 | 2026-11-18 | inferred:510 | adjusted_close:10, close+adj_close:500 |
| `TMO` | 510 | 501 | 2024-11-18 | 2026-11-17 | inferred:501, unknown:9 | adjusted_close:9, close+adj_close:501 |
| `UNH` | 510 | 501 | 2024-11-18 | 2026-11-17 | inferred:501, unknown:9 | adjusted_close:9, close+adj_close:501 |

## FX coverage

| Currency | Rows | PIT rows | First | Last |
| --- | --- | --- | --- | --- |
| `CHF` | 10 | 10 | 2026-11-03 | 2026-11-16 |
| `GBP` | 10 | 10 | 2026-11-03 | 2026-11-16 |
| `USD` | 10 | 10 | 2026-11-03 | 2026-11-16 |

## Legacy rows without publication metadata

73 rows are `available_at IS NULL` or `availability_source='unknown'`; they stay readable through `load_prices()` but are invisible to `prices_as_of()` and therefore never used by a backtest (decision Q1).

| Ticker | Legacy rows |
| --- | --- |
| `ABBV` | 9 |
| `AMGN` | 9 |
| `AZN` | 10 |
| `JNJ` | 9 |
| `LLY` | 9 |
| `MRK` | 9 |
| `TMO` | 9 |
| `UNH` | 9 |

## Backtest persistence relationships

- `strategy_backtest_metrics`: run_id → strategy_backtest_runs.run_id (CASCADE)
- `strategy_backtest_trades`: run_id → strategy_backtest_runs.run_id (CASCADE)
- `strategy_backtest_equity`: run_id → strategy_backtest_runs.run_id (CASCADE)

## Notes

- `prices_as_of()` is the only read path the backtesting engine uses;
  `load_prices()` is the V2.2 non-PIT read and is never called by `fa/backtesting/`.
- Legacy rows are retained for audit; they are not deleted and not retro-stamped.
- The frozen baseline copy `docs/development/v2.2.3_baseline/database_backup.db`
  remains the migration-test fixture and is not modified by this audit.
