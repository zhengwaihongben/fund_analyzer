# V2.3.2 — Strategy Backtest Engine Technical Specification

Status: **accepted** (2026-11-18, after review m08248 and the Q7 ruling).

Stage A — Price PIT Foundation: **accepted** (§15).
Stage B — Models & Data Access: **accepted** (§16).
Stage C — Engine & Costs: **accepted** (§17).
Stage D — Metrics, Runner & Schema: **accepted** (2026-11-20, Round 9; §18).
Stage E — Integration & Acceptance: **delivered 2026-11-20, pending final
independent acceptance** (§19).

The lead accepted the six decisions in §13 and chose option (a) for Q7 (§13.1).
Stages A–D have completed independent review and are accepted. Stage E has been
delivered and is awaiting final independent acceptance. V2.4 has not started.
This revision corrected the execution-instant rule (§5.3), replaced the
range-summary fingerprint with a content fingerprint (§4.4), added
`initial_capital` (§4.1) and wrote down the re-adjusted-history rule (§5.10).

Provenance: Stage 2.5 review (m08168) concluded that the next stage must be
designed before it is implemented, and that the engine must consume the V2.3
point-in-time (PIT) data layer rather than grow a second history reader. Data
facts in §2 were measured against the working database on 2026-11-18.

---

## 1. Goal and non-goals

### 1.1 Goal

A deterministic, PIT-safe, multi-currency **strategy** backtest that runs
entirely against SQLite:

* input is a frozen `StrategySpec` plus a database snapshot, never the network;
* every decision uses only data whose `available_at` is at or before the
  decision instant, and every trade uses only data available at its execution
  instant;
* the result is reproducible: the same spec and the same data fingerprint must
  produce the same run, and a second identical request must return the stored
  run instead of writing another one;
* costs (commission, slippage, FX spread), cash, dividends and splits are part
  of the model, not corrections applied afterwards;
* metrics are computed from the stored equity curve and trades, not recomputed
  by a second code path.

### 1.2 Non-goals (explicit)

Live or paper trading; intraday or tick data; short selling, margin, options,
futures; fund-of-fund replication from holdings snapshots; parameter
optimisation or walk-forward search; GUI work (that is V2.4); a second
historical price store or a separate "backtest cache"; changing the estimation
pipeline (`fa/analysis/nav_estimator.py`) or the V2.2 public interfaces;
replacing `fa/analysis/estimation_backtest.py`, which stays the *estimation
validation* subsystem.

---

## 2. Current data reality and the prerequisite

### 2.1 Measured state (2026-11-18, read-only)

| Item | Value |
|---|---|
| `market_prices` rows | **93**, 10 tickers, 2026-11-03 → 2026-11-16 |
| `market_prices.available_at` populated | **0 / 93** |
| `market_prices.availability_source` | `unknown` on all 93 |
| `price_revisions` | 0 |
| `fx_rates` rows | **30** (USD/CHF/GBP × 10 sessions), all `inferred` with `available_at` |
| `fx_revisions` | 0 |
| `official_nav` | 452 |
| `holdings_snapshots` / `holdings` | 1 / 10 |
| `estimation_runs` | 25 |
| `backtest_results` | 0 (reserved for estimation validation) |
| schema version | 3 |

So the FX leg is PIT-capable (`fx_as_of()`), and the **price leg is not**: the
availability columns exist (Migration 002) but nothing has ever written them,
and there is no `prices_as_of()`.

### 2.2 Why this blocks V2.3.2

The engine's decision rule is "use what was knowable then". `Repository.load_prices()`
returns every stored row regardless of when it became available, so an engine
built on it would be exposed to exactly the look-ahead class that Stage 2.5
removed from the estimator — the 2026-11-16 FX bar that only became available at
2026-11-17 08:30 Beijing produced a −0.1399 % phantom contribution. The roadmap
forbids a second reader, so the only sound options are:

1. build the missing price-PIT layer on the existing columns and ledger; or
2. run the backtest on non-PIT prices and label the results as such.

Option 2 makes every historical backtest untrustworthy and would need the same
work later; this specification adopts **option 1 as Stage A**, part of V2.3.2.

### 2.3 Stage A — price PIT foundation (reuses V2.3 infrastructure)

| Item | Design |
|---|---|
| Provider | extend the Yahoo chart adapter so it keeps the raw bar stamp as `market_timestamp` instead of normalising to UTC midnight; a daily bar's availability is inferred as **session close + 30 min** (`availability_source='inferred'`, note states the inference) using `calendar_for(market).session(day).close` — the same lag `§5.3` uses for the execution instant |
| Pipeline | `fa/data/price_pipeline.py`, same shape as `fx_pipeline`: `PriceRequest`, `PriceUpdateOutcome`, `update_prices()`; window-edge cache, five-session overlap, per-bar validation, revisions into the existing `price_revisions`, full time fields stamped before the write |
| Repository | add `prices_as_of(ticker, as_of, start=None, end=None)` mirroring `fx_as_of()` (`available_at IS NOT NULL`, `julianday(available_at) <= julianday(?)`, explicit source whitelist) **plus version reconstruction**: the value returned is the one current at `as_of`, see §5.10. `load_prices()` stays non-PIT for V2.2 callers |
| Legacy rows | Q1 is settled (§13): re-download the window through the pipeline. Existing `unknown`/NULL rows stay PIT-invisible until then and are never back-filled with a guessed publication time |
| Re-adjusted history | a later download that changes a historical `adj_close` (or `close`) writes the superseded version to `price_revisions` first; the new value becomes PIT-visible only at `replaced_at`, and a query before that instant sees the old value (§5.10, Q7) |
| Tests | PIT boundaries, `unknown` excluded, revision ledger on re-download, pre/post-revision visibility, pipeline idempotency, no schema change in Stage A |

Stage A changes no schema and no V2.2 interface; it fills columns and adds one
read method.

---

## 3. Architecture and module boundaries

```
models.py      frozen dataclasses only            (no I/O, no SQL)
   ▲
data_access.py PIT-only read wrappers             (Repository in, series out)
   ▲
engine.py      rebalance → target → trades → equity (pure; deterministic)
   ▲
costs.py       fee/slippage/FX-spread functions    (pure)
metrics.py     equity+trades → metrics             (pure; independent of analysis/risk.py)
   ▲
runner.py      orchestration, run_id, persistence (Repository writes)
```

| Module | Allowed to | Must never |
|---|---|---|
| `fa/backtesting/models.py` | define dataclasses, validation | import `sqlite3`, `requests`, `Repository` |
| `fa/backtesting/data_access.py` | call `prices_as_of`, `fx_as_of`, `load_official_nav` | call `load_prices` (non-PIT), write anything, hit the network |
| `fa/backtesting/engine.py` | simulate sessions, produce trades/equity | import `Repository`, `pandas`, `requests`; read the clock |
| `fa/backtesting/costs.py` | pure cost maths | own config parsing |
| `fa/backtesting/metrics.py` | compute metrics from arrays | import `fa/analysis/risk.py` |
| `fa/backtesting/runner.py` | resolve config, call engine, persist | contain strategy maths |

No network call may occur anywhere in `fa/backtesting/`; a test asserts that the
package imports neither `requests` nor `yfinance`/`akshare`. No SQL may appear
outside `data_access.py` and `runner.py`'s persistence calls.

---

## 4. Data model

### 4.1 `StrategySpec` (frozen)

| Field | Type | Meaning |
|---|---|---|
| `spec_id` | `str` | caller-supplied label, part of the fingerprint |
| `universe` | `tuple[Instrument, ...]` | ticker + market + currency; CNY is not an instrument |
| `weighting` | `Weighting` enum | `equal`, `fixed` (weights in `target_weights`), `momentum` |
| `target_weights` | `dict[str, float]` | required for `fixed`, must sum to 1 ± 1e-6 |
| `momentum_window` | `int` | sessions of lookback; required for `momentum`, ≥ 20 |
| `rebalance` | `RebalanceRule` | `frequency` (`daily`/`weekly`/`monthly`/`quarterly`) + `anchor` (weekday 0–4 for weekly; day-of-month for monthly/quarterly) |
| `drift_threshold` | `float` | skip a rebalance when max |w_target − w_held| ≤ threshold (default 0.02; 0 disables) |
| `start` / `end` | `date` | inclusive; `start` must be ≥ 2 years before `end` only as a warning, not an error |
| `base_currency` | `str` | must be `"CNY"` in V2.3.2 |
| `initial_capital` | `float` | starting cash in CNY, strictly `> 0`; **required, no default**, because it changes position sizes, the minimum commission gate and CAGR. It is part of the canonical spec JSON, so changing it changes `run_id` |
| `benchmark` | `str \| None` | optional ticker read through the same PIT path |
| `costs` | `CostModel` | §6 |

Validation raises `BacktestSpecError` with a Chinese message; partial specs never
reach the engine.

### 4.2 Simulation objects

`Position(ticker, quantity, price, market_value_base)`,
`Trade(session, ticker, side, quantity, price_local, price_source, fx_rate,
fx_source, gross_base, commission_base, slippage_base, fx_spread_base,
net_base, reason)`,
`EquityPoint(session, cash_base, holdings_base, total_base, exposure,
benchmark_base=None, drawdown)`,

`BacktestMetrics` — fields exactly as persisted in §4.4, all `float|None` so a
run that cannot compute one (e.g. no negative returns ⇒ Sortino) reports `None`
rather than a fake zero.

### 4.3 Persistence schema (Migration 004, additive)

```sql
CREATE TABLE IF NOT EXISTS strategy_backtest_runs (
    run_id             TEXT PRIMARY KEY,
    spec_id            TEXT NOT NULL,
    model_version      TEXT NOT NULL,
    start_date         TEXT NOT NULL,
    end_date           TEXT NOT NULL,
    base_currency      TEXT NOT NULL DEFAULT 'CNY',
    universe_json      TEXT NOT NULL,
    spec_json          TEXT NOT NULL,
    costs_json         TEXT NOT NULL,
    data_fingerprint   TEXT NOT NULL,
    look_ahead_status  TEXT NOT NULL DEFAULT 'unknown',
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_backtest_metrics (
    run_id             TEXT PRIMARY KEY REFERENCES strategy_backtest_runs(run_id) ON DELETE CASCADE,
    sessions           INTEGER NOT NULL,
    cagr               REAL,
    annualised_vol     REAL,
    sharpe             REAL,
    sortino            REAL,
    max_drawdown       REAL,
    calmar             REAL,
    turnover           REAL,
    hit_rate           REAL,
    exposure           REAL,
    cost_drag          REAL,
    trades             INTEGER NOT NULL,
    total_costs_base   REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS strategy_backtest_trades (
    run_id             TEXT NOT NULL REFERENCES strategy_backtest_runs(run_id) ON DELETE CASCADE,
    seq                INTEGER NOT NULL,
    session            TEXT NOT NULL,
    ticker             TEXT NOT NULL,
    side               TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity           REAL NOT NULL,
    price_local        REAL NOT NULL,
    price_source       TEXT NOT NULL,
    fx_rate            REAL,
    fx_source          TEXT,
    gross_base         REAL NOT NULL,
    commission_base    REAL NOT NULL,
    slippage_base      REAL NOT NULL,
    fx_spread_base     REAL NOT NULL,
    net_base           REAL NOT NULL,
    reason             TEXT,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS strategy_backtest_equity (
    run_id             TEXT NOT NULL REFERENCES strategy_backtest_runs(run_id) ON DELETE CASCADE,
    session            TEXT NOT NULL,
    cash_base          REAL NOT NULL,
    holdings_base      REAL NOT NULL,
    total_base         REAL NOT NULL,
    exposure           REAL NOT NULL,
    benchmark_base     REAL,
    drawdown           REAL,
    PRIMARY KEY (run_id, session)
);

CREATE INDEX IF NOT EXISTS idx_bt_trades_session
    ON strategy_backtest_trades (run_id, session);
```

`backtest_results` keeps its estimation-validation meaning and is not reused.
Migration 004 only adds tables and indexes; no existing table changes, so the
frozen baseline test pattern of Migrations 002/003 applies unchanged.

### 4.4 `run_id` and the idempotency contract

`run_id = sha256(canonical_json(spec) + model_version + data_fingerprint)`.
`canonical_json(spec)` contains every `StrategySpec` field **including
`initial_capital`**, keys sorted, floats written with `repr()` so two equal specs
serialise identically.

`data_fingerprint` is a **content hash of the PIT inputs seen at each decision
instant**, not a range summary and not the final database state:

1. `run_backtest()` walks the session grid; for session `D` the decision instant
   is `D close + BACKTEST_DECISION_LAG_MINUTES`. Each block is produced by the
   public helper `fingerprint_session_payload()`, which exposes the two price roles
   so tests can check their composition instead of inferring it from digests:
   - `role = "session_price"`: the PIT bar for `D`, recorded only when that
     instrument's own venue is open. It is a **conservative superset** — the price
     the engine *could* use, not proof that it read one; a position that is flat and
     unpriced may never ask. There is no global `lookback_start`.
   - `role = "momentum_window"`: the instrument's last `window + 1` visible
     observations, read on **every** rebalance day for **every** instrument,
     regardless of whether that venue traded `D`. This is not a superset choice:
     `_Engine._momentum_weights()` asks every ticker for its series even when the
     venue is shut, so skipping a closed venue would drop an input the engine uses
     (review round 7, P1-17).
   - A venue that is open but has no published bar contributes a
     `{"missing": …}` marker; a closed venue contributes no `session_price` row.
2. Per price row: `ticker`, `role`, `date`, `close`, `adj_close`, `dividend`,
   `split`, `market_timestamp`, `available_at`, `availability_source`, `source`.
   `revision_id` is deliberately excluded: it is database bookkeeping, not a
   market input, so a revision published later cannot change the digest of an
   earlier instant. `fetched_at` is also excluded: `save_prices()` refreshes it on
   every re-download, including downloads where no value changed.
3. Per FX input: `currency`, `session`, the `rate` used and its `rate_date`. The
   repository keeps no FX version history (decision Q7), so a row payload would
   leak a later correction into earlier sessions; the value the run consumed is
   what counts. FX rows are a conservative superset: every session records the rate
   the engine would use, whether or not that session traded.
4. Each session block carries its `session` date and `decision_instant`, so
   identical rows at different instants cannot collide.
5. Blocks are serialised as newline-delimited canonical JSON (keys sorted, floats
   via `repr()`, instants as stored UTC with a `Z` suffix, `null` kept explicit),
   with the benchmark's consumed rows appended:
   `data_fingerprint = sha256(model_version + schema_version + payload)`.

Why the range summary was rejected: revising an earlier date leaves
`row_count`, `min(date)`, `max(date)` and `max(fetched_at)` unchanged, so the old
backtest would be silently reused after its inputs changed. Why a single final
instant is also wrong: it describes only the latest version, so two databases
with different early versions but identical latest rows would hash the same, and
a January decision taken on close 100 could not be told apart from one taken
after a February revision. Only a value that was genuinely visible at a decision
instant changes that instant's block.

`now`/`created_at` stay excluded, so two runs on identical data share an id.
`run_backtest()` returns `(run, created: bool)`; `created=False` means the stored
run was returned and nothing was written. `force=True` appends a `-N` suffix and
writes a new run (used only when the fingerprint algorithm itself changes).

---

## 5. Execution semantics (the PIT contract)

1. **Session grid.** Union of trading sessions of the instruments' markets over
   `[start, end]`, taken from `calendar_for(market)`. A date on which one market
   is closed but another is open is still a portfolio session; the closed
   market's prices simply are not available for that date.
2. **Decision instant.** For a rebalance on session `D`, signals use data with
   `available_at <= D close + BACKTEST_DECISION_LAG_MINUTES` (default 30). The
   decision is therefore "what we knew after the close".
3. **Execution.** Orders execute at the **next session's close** (default
   `BACKTEST_EXECUTION_LAG_SESSIONS = 1`). The simulated fill price is that
   session's official close, and the price is read with a PIT query made at
   `execution_instant = execution_session close +
   BACKTEST_EXECUTION_LAG_MINUTES` (default 30) — the same lag the availability
   rule uses (§2.3). Querying at the decision instant would be look-ahead in one
   direction; querying *at* the execution close would be wrong in the other,
   because that close only becomes `available_at` 30 minutes later, so the bar
   would not be visible to its own trade. The instant only has to be late enough
   for the value to be public; the price itself is the close. FX is converted at
   the same `execution_instant`. Q2 is settled (§13).
4. **Missing data.** A missing price or FX rate at execution skips that order
   and records an anomaly; the weight stays in cash. The run never substitutes a
   nearby date silently.
5. **Cash.** Never negative; buys are scaled down to available cash. Every buy
   and sell pays commission and slippage; every currency conversion pays the FX
   spread. Cash sits in CNY.
6. **Shares.** Fractional shares by default (`BACKTEST_FRACTIONAL_SHARES = 1`);
   when disabled, quantities floor to whole shares.
7. **Dividends and splits.** Price series are read as total-return series where
   `adj_close` exists (the stored `adj_close` already embeds dividends and
   splits); when only `close` exists, the engine may credit `dividend` as cash on
   the ex-date and apply `split` to quantities, and must then mark the run
   `look_ahead_status`/quality accordingly. Q3 is settled (§13); the choice is
   recorded on the run row and is never implicit. Because `adj_close` is derived,
   it is also subject to §5.10: a vendor re-scaling of past bars is a revision,
   not a retroactive fact.
8. **Prohibition.** `engine.py` and `metrics.py` must not import `Repository`;
   only `data_access.py` reads, and only through `prices_as_of` / `fx_as_of`. A
   test greps the package for `load_prices(` and fails if it appears outside
   `data_access.py`.
9. **Momentum.** Signals use adjusted closes from `prices_as_of(...)` for the
   lookback window ending at the decision session. A momentum value is a return
   over `momentum_window` intervals, so it needs `momentum_window + 1`
   observations: when an instrument has fewer visible observations than that, it
   is excluded from the target and recorded as `momentum-excluded`, never
   back-filled from a shorter window.
10. **Re-adjusted history.** `adj_close` is a *derived* series: a later dividend
    or split makes the vendor re-scale older bars. That is a data revision, not
    a fact that always existed, so it must not leak backwards. Stage A stores the
    superseded version in `price_revisions` and `prices_as_of()` returns the
    version that was current at `as_of` (§13.1, Q7). A backtest run before the
    re-scaling therefore keeps the values it could actually have known.

---

## 6. Cost model

| Component | Default | Applied to |
|---|---|---|
| `BACKTEST_COMMISSION_BPS` | 5.0 | both sides, on gross base notional |
| `BACKTEST_MIN_COMMISSION` | 0.0 | per order, base currency |
| `BACKTEST_SLIPPAGE_BPS` | 10.0 | adverse to the trade direction, on gross notional |
| `BACKTEST_FX_SPREAD_BPS` | 20.0 | each conversion leg when currency ≠ CNY |

Costs are computed once per order by `costs.py`, stored on the trade row, and
summed into `total_costs_base` and `cost_drag`. Rounding: cash and cost amounts
to 2 decimals, quantities to 6 decimals; rounding never changes the recorded
price.

---

## 7. Metrics

All from the stored equity curve and trade list; no metric may reprocess prices.

| Metric | Definition |
|---|---|
| `cagr` | `(total_end / total_start) ** (365.25 / days) − 1`, `days = last_session − first_session` |
| `annualised_vol` | `std(daily log returns of total_base, ddof=1) * sqrt(252)` |
| `sharpe` | `mean(daily excess) / std(daily excess, ddof=1) * sqrt(252)`, excess over `BACKTEST_RISK_FREE_RATE` converted to a daily rate |
| `sortino` | `mean(daily excess) * 252 / (std(min(excess, 0), ddof=1) * sqrt(252))`; `None` when no negative excesses |
| `max_drawdown` | `min(eq / running_max(eq) − 1)`, reported as a negative fraction |
| `calmar` | `cagr / abs(max_drawdown)`; `None` when drawdown is 0 |
| `turnover` | `Σ|trade net notional| / (2 * mean total_base) / years` |
| `hit_rate` | share of closed round trips with positive PnL (FIFO matching), `None` with no round trips |
| `exposure` | mean of `holdings_base / total_base` over sessions |
| `cost_drag` | `total_costs_base / mean total_base / years` |

`metrics.py` is independent of `fa/analysis/risk.py`: that module scores NAV
series of a fund (daily returns, downside deviation over all observations), which
is a different object from a portfolio equity curve with cash and trades.

---

## 8. Runner

```python
def run_backtest(
    spec: StrategySpec,
    repository: Repository,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> tuple[BacktestRun, bool]:
    ...
```

Steps: validate spec → build the session grid → for each session, read only what
PIT allows → decide/execute → accumulate equity → compute metrics → compute the
fingerprint → insert or return the stored run. Writes happen in one transaction
through `Repository`; a failure leaves no partial run. `now` is injectable and
defaults to `datetime.now(timezone.utc)`; it appears only in `created_at`, never
in the fingerprint.

---

## 9. Configuration

| Key | Env | Default |
|---|---|---|
| `BACKTEST_COMMISSION_BPS` | `FA_BACKTEST_COMMISSION_BPS` | 5.0 |
| `BACKTEST_MIN_COMMISSION` | `FA_BACKTEST_MIN_COMMISSION` | 0.0 |
| `BACKTEST_SLIPPAGE_BPS` | `FA_BACKTEST_SLIPPAGE_BPS` | 10.0 |
| `BACKTEST_FX_SPREAD_BPS` | `FA_BACKTEST_FX_SPREAD_BPS` | 20.0 |
| `BACKTEST_RISK_FREE_RATE` | `FA_BACKTEST_RISK_FREE_RATE` | 0.02 |
| `BACKTEST_DECISION_LAG_MINUTES` | `FA_BACKTEST_DECISION_LAG_MINUTES` | 30 |
| `BACKTEST_EXECUTION_LAG_SESSIONS` | `FA_BACKTEST_EXECUTION_LAG_SESSIONS` | 1 |
| `BACKTEST_FRACTIONAL_SHARES` | `FA_BACKTEST_FRACTIONAL_SHARES` | 1 |
| reuse | `PRICE_JUMP_WARN`, `PRICE_REVISION_TOLERANCE`, `STALE_SESSIONS_*` | as today |

Config is read once per run by `runner.py` and passed explicitly downwards;
`engine.py` never reads the environment.

---

## 10. Testing strategy

No test may touch the network; every price/FX series comes from a temporary
SQLite database built by the fixture.

| File | Covers |
|---|---|
| `tests/test_price_pit.py` | Stage A: availability stamping, `prices_as_of` boundaries (`<`, `=`, `>`), `unknown`/NULL excluded, revision ledger on re-download, a re-scaled `adj_close` returning the pre-revision value before `replaced_at` and the revised value after it, pipeline idempotency |
| `tests/test_backtest_fingerprint.py` | `data_fingerprint` changes when a consumed value or its availability changes while `row_count`/date range/`max(fetched_at)` stay identical; `initial_capital` changes `run_id`; identical data + spec reuse the stored run |
| `tests/test_backtest_execution_instant.py` | at the execution close the bar is **not** visible (close + 30 min rule), at close + 30 min it is, and the recorded fill price is that session's close |
| `tests/test_backtest_models.py` | spec validation (weights sum, momentum window, CNY base, date order), dataclass immutability, error messages |
| `tests/test_backtest_data_access.py` | wrapper returns only PIT rows, missing series reported not invented, no `load_prices` use |
| `tests/test_backtest_engine.py` | hand-computed two-asset, three-session run: target weights, trade quantities, cash, equity; drift threshold; no negative cash; skipped order path |
| `tests/test_backtest_costs.py` | commission/slippage/FX-spread arithmetic, min commission, rounding |
| `tests/test_backtest_metrics.py` | each metric against independently computed numbers; `None` cases (no drawdown, no negative excess, no round trips) |
| `tests/test_backtest_runner.py` | persistence round trip, idempotent second run (`created=False`), fingerprint stability, `force=True`, transaction rollback on injected failure, Migration 004 rollback/idempotency on a baseline copy |
| `tests/test_backtest_pit.py` | adversarial leakage: a price whose `available_at` is after the decision instant must not change the target; a bar published mid-run must only affect later sessions; legacy NULL rows never used |

Synthetic fixture: deterministic arithmetic series for two currencies plus a CNY
cash leg, one dividend, one split, one missing session, one stale-but-usable
session. Expected values are written by hand in the test, not produced by the
engine.

---

## 11. Stages, gates and acceptance

| Stage | Content | Gate |
|---|---|---|
| **A — price PIT** | §2.3: stamping, `update_prices`, `prices_as_of` with version reconstruction (§5.10), tests | PIT boundaries green, re-scaled history reconstructs the pre-revision value, no schema change, V2.2 paths unchanged, suite green |
| **B — models & access** | `models.py`, `data_access.py`, fixture — **done, see §16** | spec validation, PIT-only reads proven, no network import |
| **C — engine & costs** | `engine.py`, `costs.py` — **done, see §17** | hand-computed run matches to 1e-9; no look-ahead; no negative cash |
| **D — metrics, runner, schema** | `metrics.py`, `runner.py`, Migration 004 — **delivered and accepted 2026-11-20, see §18** | metrics match hand values; idempotent run; migration rollback/idempotency on the frozen baseline |
| **E — acceptance** | synthetic full run, real short-window run on the working database, CLI/report, retention audit, docs, archive — **delivered 2026-11-20, see §19** | BT-01…BT-14 below, suite green, `compileall`, `git diff --check` |

Acceptance criteria:

| ID | Criterion |
|---|---|
| BT-01 | Every price/FX read in the engine goes through a PIT query; no `load_prices` in `fa/backtesting/` |
| BT-02 | A price published after the decision instant cannot change that decision (adversarial test) |
| BT-03 | Same spec + same data ⇒ same `run_id`, same trades, same metrics |
| BT-04 | A repeated request returns `created=False` and writes no rows |
| BT-05 | Costs match the hand-computed values and appear on every trade row |
| BT-06 | Cash never goes negative; unfunded buys are scaled, not dropped silently |
| BT-07 | Metrics equal the hand-computed values and report `None` where undefined |
| BT-08 | Persistence round-trips: runs/metrics/trades/equity match the in-memory result |
| BT-09 | Migration 004 is additive, idempotent, and rolls back cleanly on a copy of the frozen baseline |
| BT-10 | No network import inside `fa/backtesting/`; the suite makes no HTTP request |
| BT-11 | The estimation-validation subsystem and V2.2 public interfaces are untouched |
| BT-12 | Docs (this file, roadmap, testing doc, README) match the delivered code and test counts |
| BT-13 | The fill price is the execution session's close and is read at `close + 30 min`; a query at the close itself cannot see it |
| BT-14 | `data_fingerprint` is a content hash: a revised historical value changes it even when row count, date range and `max(fetched_at)` do not |

---

## 12. Risks

| Risk | Consequence | Mitigation |
|---|---|---|
| Only ten days of price history persisted | a real multi-year backtest is impossible until prices are ingested | Stage A's pipeline plus a documented two-year ingest command in Stage E; acceptance uses the synthetic fixture for correctness and the short real window only as a smoke test |
| Yahoo `adj_close` semantics | dividends/splits may be double counted if treated as both adjustment and cash | one policy per run, recorded on the run row (§5.7, Q3) |
| Calendar gaps (`EBS`/Swiss, EU/US half days) | a session may be assumed open when it was not | rule-based calendars shipped in V2.1; unknown years already warn; Stage A records `calendar_fallback` when a market has no calendar |
| Universe is a fixed ticker list | survivorship and membership bias | stated as a limitation: this engine tests *strategies on instruments*, not "the fund's historical holdings" |
| Close-only execution | results differ from open-execution intuition | documented explicit assumption; `BACKTEST_EXECUTION_LAG_SESSIONS` exists so a future data set with opens can be tested without changing strategy code |

### 12.1 Documented two-year ingestion (Stage E)

A multi-year backtest needs a wider PIT price window than the working database
holds. The supported command uses the existing Stage A / Stage 2 pipelines — no
new download path and no invented CLI:

```text
python scripts/ingest_market_data.py --years 2
```

It resolves `start = end − (365 × years + years // 4)` days with
`end = today` (or `--end YYYY-MM-DD`), calls
`fa.data.price_pipeline.update_prices(PriceRequest(tickers, start, end), repository)`
for the default ten-ticker universe and
`fa.data.fx_pipeline.update_fx(FxRequest(currencies, start, end), repository)`
for `USD/HKD/CHF/GBP`, writing into `data/database/fund_analyzer.db` unless
`--database` names another file. Both pipelines stamp `available_at`, keep the
revision ledger and validate quote direction, and a re-run only refreshes fetch
metadata, so the command is idempotent.

The script prints `downloads / inserted / updated / revisions` per call plus the
first and last stored date for every ticker and currency, and exits non-zero if
any requested item produced no bars. It needs network access; validation runs
here used a throwaway database (`--database _scratch/ingest.db --tickers LLY,JNJ`)
so the working database was not widened by the acceptance run itself.

---

## 13. Decisions (confirmed by the lead, 2026-11-18)

The six questions below were answered in review m08248. They are decisions, not
open items; the table records the effect each one had on this document.

| # | Decision | Effect on the design |
|---|---|---|
| Q1 | **Re-download** the legacy window through the price pipeline. `unknown`/NULL rows stay PIT-invisible until then; a publication time is never guessed | Stage A must run the pipeline against the working database before a backtest is accepted; calendar back-fill is forbidden |
| Q2 | **Next session close**, PIT query at that close + 30 min, fill price equal to that session's close | §5.3 rewritten; BT-13 tests the boundary |
| Q3 | **`adj_close` preferred**; raw `close` + `dividend` only as an explicitly recorded fallback | §5.7 records the policy on the run row; the fallback is never silent |
| Q4 | **Persist the equity curve** in `strategy_backtest_equity` | Migration 004 keeps the fourth table; BT-08 cross-checks equity against trades and metrics |
| Q5 | **Fixed instrument list** from `StrategySpec.universe` | holdings-driven universes are deferred; the survivorship limitation stays documented |
| Q6 | **Stage A is part of V2.3.2** and may not be skipped or replaced by non-PIT reads | Stage A is a hard gate before Stage B |

### 13.1 One design point still to confirm: versioned reconstruction (Q7)

Q3's caveat — a future dividend re-scaling older `adj_close` values — cannot be
handled by `available_at` alone, because the *bar* was public long before the
revision. Proposed rule:

* a re-download that changes `close`/`adj_close` writes the superseded version to
  `price_revisions` (V2.3.1 already does this) and stamps `replaced_at`;
* `prices_as_of(ticker, as_of)` returns, for each date, the latest version whose
  `available_at <= as_of` **and** whose supersession instant (`replaced_at`, or
  +∞ for the current row) is later than `as_of`;
* a run made before the revision therefore sees the pre-revision value and a run
  after it sees the revised one; neither inherits the other's timing.

This needs one decision because it makes `prices_as_of()` stricter than
`fx_as_of()`, which has no such reconstruction (FX rates are not re-scaled by
dividends). Options: (a) prices only, recorded as a known FX gap; (b) mirror it
for FX revisions as well; (c) skip it and accept that an adjustment rewrites the
past. Recommendation: **(a)**, with the FX gap documented. Stage A does not start
until the lead confirms this point.

**Confirmed 2026-11-18**: option (a). `prices_as_of()` requires both
`available_at <= as_of` and (`replaced_at > as_of` or current row); FX revision
visibility stays a documented limitation.

---

## 14. See also

* [07_development_roadmap](07_development_roadmap.md) — stage ordering.
* [08_v231_data_foundation](08_v231_data_foundation.md) — the time model and
  `AvailabilitySource` invariants Stage A reuses.
* [09_v231_stage2_fx_pipeline](09_v231_stage2_fx_pipeline.md) — the FX pipeline
  whose structure `price_pipeline.py` mirrors.
* [04_database_schema](04_database_schema.md) — migration rules (explicit
  transaction, no `executescript`, pragma-guarded columns).
* [06_testing_and_acceptance](06_testing_and_acceptance.md) — suite
  expectations and regression gates.

---

## 15. Stage A status — delivered (2026-11-18)

Authorised in m08323 with the locked scope: price pipeline, availability
metadata, `price_revisions` ledger and `prices_as_of()`, **no schema change and
no V2.2 public-interface change**. Delivered as specified; stages B–E not
started.

### 15.1 Deliverables

| File | Status | Role |
| --- | --- | --- |
| `fa/data/price_provider.py` | new | Yahoo daily bars with the vendor stamp kept (`PriceBar`, `PriceFetch`, `bar_trading_date`, `fetch_price_bars`); no SQLite, no store decision |
| `fa/data/price_pipeline.py` | new | `PriceRequest`, `PriceUpdateOutcome`, `update_prices()`, `infer_availability()`, window-edge cache, overlap, validation |
| `fa/data/database/repository.py` | modified | `save_prices()` bumps a revised value's `available_at` to `replaced_at`; new `prices_as_of()`, `count_price_revisions()`, shared `_price_point_from_row()` |
| `fa/config.py` | modified | `PRICE_AVAILABILITY_LAG_MINUTES` (30), `PRICE_OVERLAP_SESSIONS` (5), `PRICE_OVERLAP_CALENDAR_DAYS` (12), `PRICE_DEFAULT_YEARS` (2), all `FA_*`-overridable |
| `tests/test_price_pit.py` | new | 52 offline tests (provider, cache, validation, revisions, PIT, execution instant, legacy re-download, download plan, duplicate keys, microsecond boundaries) |
| `scripts/smoke_price_pipeline.py` | new | live smoke on a throwaway database |
| `scripts/verify_legacy_backfill.py` | new | re-downloads the frozen baseline's legacy rows on a copy and checks the result line by line |
| `docs/development/stageA_legacy_backfill.md` | new | the resulting 93-row reconciliation table |

Not touched: `fa/models/market_data.py`, `fa/data/database/schema.py` (schema
stays version 3), `fa/data/fx*.py`, `fa/analysis/nav_estimator.py`, the CLI and
the GUI.

### 15.2 Behaviour

* **Availability**: `available_at` = that market's session close +
  `PRICE_AVAILABILITY_LAG_MINUTES`, `availability_source='inferred'`, note says
  the bar is stamped at the session open and treated as the close. US summer is
  20:30Z, winter 21:30Z; a venue whose calendar falls back to the weekend rule is
  stamped at 16:30Z with an explicit `calendar-fallback` warning.
* **Cache**: complete only when both window edges are covered **and** every
  stored point is PIT-usable. The tail edge uses the last *completed* session, so
  a running session does not force needless re-downloads. A date-complete series
  whose rows carry legacy `unknown` metadata is **not** a cache hit: the pipeline
  re-downloads it from the first evidence-less session so only a fresh vendor
  response can earn an `available_at` (decision Q1). An unchanged re-download is
  metadata enrichment, not a revision, and adds no `price_revisions` row.
* **Incremental**: a genuine window gap fetches from `request.start`; a tail gap
  re-fetches from `min(5 sessions back, 12 calendar days back)`.
* **Validation**: non-finite/≤0 closes dropped (`invalid-rate`), out-of-window
  bars dropped (`out-of-range`), duplicates keep the latest stamp
  (`duplicate-date`), >50 % moves kept and flagged (`jump`). Nothing is inverted.
* **Revision identity**: a changed `close`/`adj_close` copies the *stored* row
  into `price_revisions` and pushes the new row's `available_at` to
  `replaced_at`, so the corrected number cannot be visible before the revision.
* **PIT**: `prices_as_of(ticker, as_of)` requires
  `available_at <= as_of` and (`replaced_at > as_of` or current), whitelists
  `observed`/`vendor_timestamp`/`inferred`, compares with `julianday()`, and
  returns the freshest visible version per date. `load_prices()` is unchanged and
  still serves the legacy rows.
* **Venue integrity**: a re-download whose vendor exchange normalises differently
  from the stored one is rejected (`market-mismatch`) instead of mixing NYSE and
  SIX prices under one ticker; a transient transport error never falls back to a
  foreign listing.

### 15.3 Verification

| Check | Result |
| --- | --- |
| `python -m unittest tests.test_price_pit` | 60 tests OK |
| `python -m unittest discover -s tests -t .` | **571 tests OK** |
| `python -m compileall -q fa tests scripts fund_analyzer.py fund_analyzer_gui.py` | exit 0 |
| `git diff --check` | clean |
| Live smoke (`smoke_price_pipeline.py --days 90`, 10 tickers) | first `downloads=10 inserted=635`; forced refresh `inserted=0 updated=90 revisions=0`; cached `downloads=0`; per ticker `rows=63–65`, all with availability, `pit` one less than rows for the live session, `failures=0` |
| Frozen baseline re-download (`verify_legacy_backfill.py`) | 93 legacy keys → 10 provider calls → 93 returned, all 93 rows carry availability, 0 still `unknown`, 0 dates with two keys, 3 revisions; `prices_as_of` 90 rows at the run instant and 93 immediately afterwards (the three revised rows become public at their revision instant) |
| Working database | 5 107 rows, 5 034 PIT-visible; 73 legacy `adjusted_close` rows from the pre-fix run still `unknown` (see §15.4) |

The working database was upgraded live during this stage. Its first Stage A run
happened before the type-preservation fix (§15.5), so 73 of its legacy
`adjusted_close` rows still share a date with a re-downloaded `close+adj_close`
row and remain `unknown`; the frozen baseline copy, which was re-downloaded with
the fixed code, shows the intended outcome of 93/93.

### 15.4 Known limits

* `availability_source` is always `inferred` — Yahoo exposes no publication
  instant; the note records the derivation.
* The 2024 part of a two-year window has no holiday table (`市場日曆未收錄 2024
  年`), so sessions there fall back to weekends-only for edge checks; 2025–2026
  are hand-maintained.
* The working database still carries 73 legacy `adjusted_close` rows that share a
  date with the canonical `close+adj_close` row. They are PIT-invisible and
  harmless to `prices_as_of()`, but deleting or re-keying them is a data-migration
  decision that this stage does not take.
* The pristine pre-Stage-A copy of the working database was overwritten by an
  earlier run of this stage (`%TEMP%\dsh-bLaW4p\fa_pre_stageA.db` now reflects the
  already-enriched state). The frozen baseline under
  `docs/development/v2.2.3_baseline/` is the surviving pre-change evidence.
* FX revision visibility is **not** reconstructed (Q7 option (a) choice) and is
  documented as a known limitation of the backtest input fingerprint.

### 15.5 Review fixes on top of Stage A (2026-11-18)

Review m08583 rejected acceptance on one point: the cache could return a
date-complete series whose rows had no publication time and "enrich" it with an
inferred `available_at`, which asserts a publication moment nobody observed and
contradicts decision Q1.

| Fix | Where | Behaviour now |
| --- | --- | --- |
| Legacy rows must be re-downloaded | `fa/data/price_pipeline.py` | `_plan_download()` returns `(False, first_incomplete_date)` when the window is date-complete but any point lacks PIT metadata; `update_prices()` calls the provider from that date and never invents availability locally (`_enrich_availability()` removed) |
| Provider failure leaves the rows invisible | same | a failed download is reported under `skipped[ticker]`; the legacy rows keep `unknown` and stay outside `prices_as_of()` |
| One row per session for legacy types | `_validate_bars()` | the stored `price_type` for a date is reused (`adjusted_close` included), so a re-download updates the legacy row in place instead of adding a second key; a session never ends up with two rows |
| Evidence instead of assertion | `scripts/verify_legacy_backfill.py` | re-downloads the frozen baseline and prints, per ticker, provider calls, vendor coverage, availability, revisions, PIT visibility before/after and duplicate keys |

The reconciliation over the frozen baseline is in
`docs/development/stageA_legacy_backfill.md`: 93 legacy keys, 10 provider calls,
93 returned and stamped, 3 revisions, 0 still `unknown`, 0 duplicate keys.

### 15.6 Review fixes on top of Stage A, round 3 (2026-11-18)

Review m08830 accepted the Q1 direction but rejected acceptance again on two cache
defects and one docstring:

| Defect | Fix | Behaviour now |
| --- | --- | --- |
| The metadata check was nested inside the cache branch | `_plan_download()` | `_first_incomplete_date()` and `_complete_window()` are evaluated **before** any branch; a cache hit requires `prefer_cache`, both edges covered and no evidence gap; otherwise `fetch_start = max(request.start, min(overlap-or-window-start, first_incomplete))`, so a short tail and `prefer_cache=False` can no longer skip an older evidence-less session |
| One unknown duplicate row per date made a session look incomplete forever | `fa/data/price_canonical.py` (new) | completeness is judged **per trading date** on the canonical version (`canonical_points_by_day()`), and the same pure `version_key()` now backs both `prices_as_of()` and `_validate_bars()`, so a query and a download decision cannot disagree; the 73 legacy duplicates stay in the table, stay `unknown`, and no longer trigger re-downloads |
| Module docstring still described in-place enrichment | `fa/data/price_pipeline.py` | rewritten to the Q1 rule (only a fresh vendor response earns availability) |

Tests BT-PIT-01…12 were added in `tests/test_price_pit.py`:
`PriceDownloadPlanTests` (complete ⇒ cache hit, first gap, tail gap plus an
earlier gap, interior gap, `prefer_cache=False` still covers the gap, empty
store, history gap) and `PriceDuplicateKeyTests` (superseded unknown key needs no
download; unknown-only session is downloaded; re-download updates the canonical
type without a third key; a 73-session/146-row fixture reaches a cache hit and a
second run downloads nothing).

Production reconciliation (read-only, working database):

| Check | Result |
| --- | --- |
| Rows before / after this round | 5 107 / 5 107 — no row deleted |
| `available_at IS NULL` | 73, all `price_type='adjusted_close'`, all `unknown`, retained |
| Sessions with two keys | 93, every one exactly 2 rows (`adjusted_close` + `close+adj_close`) |
| Canonical sessions | ABBV/AMGN/JNJ/LLY/MRK/TMO/UNH 501, AZN 507, NOVN/ROP SW 500; every canonical session has publication evidence (`unusable=0` for all ten tickers) |
| Second-run plan | `cache_hit=True`, `fetch_start=None` for **10/10** tickers — a repeat run downloads nothing |
| `prices_as_of(now)` sources | `inferred` only; counts 501/506/499 versus canonical 501/507/500 because the newest bar of AZN, NOVN and ROP SW is today's running session (2026-11-18), stamped available at 16:30Z, i.e. still in the future at the 13:29Z probe |

### 15.7 Review fix on top of Stage A, round 4 — instant precision (2026-11-18)

Review m09026 found the last blocking defect: `prices_as_of()` compared instants
with SQLite `julianday()`, which cannot represent a microsecond.

Measured on this machine (SQLite 3.39.4):

```
julianday('2026-02-27T22:00:00Z')          = 2461099.4166666665
julianday('2026-02-27T22:00:00.000001Z')   = 2461099.4166666665
difference                                  = 0.0 days  (0.0 s)
one ulp of a Julian day                     ≈ 47 µs
```

So `available_at - 1 µs` compared *equal* to `available_at`, and a price became
visible one microsecond too early; the same equality could pick the wrong
historical version through `replaced_at`. A float gap of up to ~47 µs is
indistinguishable, so the defect was not limited to the exact 1 µs probe. String
comparison was rejected for the same class of reason: `iso_utc()` trims trailing
zeros, so `…00Z` and `…00.5Z` are not lexicographically ordered by time.

The fix keeps SQL responsible only for the ticker, the availability whitelist,
`available_at IS NOT NULL` and the optional date window; the two instant
boundaries are decided in Python (timezone-aware datetimes normalised to UTC):

```python
available = _as_dt(row["available_at"])
if available is None or available > moment:
    continue
superseded = _as_dt(row["superseded_at"])
if superseded is not None and superseded <= moment:
    continue
```

The version tie-break also parses `available_at` before ordering, so
`version_key()` never compares two differently-trimmed ISO strings.

Boundary regressions, all against the same stored row and its revision:

| Test | `as_of` | Expected |
| --- | --- | --- |
| PIT-13 | `available_at − 1 µs` | invisible |
| PIT-14 | `available_at` | visible |
| PIT-15 | `available_at + 1 µs` | visible |
| PIT-16 | `replaced_at − 1 µs` | old version visible |
| PIT-17 | `replaced_at` | only the new version |
| PIT-18 | `replaced_at + 1 µs` | only the new version |

Verification after this round: **571 tests** in the suite, **60 tests** in
`tests/test_price_pit.py`, `compileall` exit 0, `git diff --check` clean. No data
was rewritten: the 93 legacy rows, the 73 inert duplicates and the 10/10
cache-hit plan are unchanged (this round touches only the read comparison).

### 15.8 Review fix on top of Stage A, round 5 — date window pushdown (2026-11-18)

Review m09127 accepted the microsecond comparison but withheld sign-off because
`prices_as_of()` still read **every** stored year of a ticker and then dropped
the out-of-window dates in Python. The window is a plain trading-date filter, so
it belongs in SQL and must apply to **both** UNION branches — applying it to one
branch would let the other leak dates outside the range.

The fix adds a bound-parameter filter to each branch:

```python
date_filter = ""
date_params: list[str] = []
if start is not None:
    date_filter += " AND price_date >= ?"
    date_params.append(start.isoformat())
if end is not None:
    date_filter += " AND price_date <= ?"
    date_params.append(end.isoformat())
```

with `params = [ticker, *whitelist, *date_params, ticker, *whitelist, *date_params]`
and the Python-level `date.fromisoformat()` filtering removed. Instant
comparisons and `_pit_preference()` are untouched: `available_at`/`replaced_at`
remain Python decisions, because `price_date` is a date the query names exactly
while the instants must stay at microsecond precision.

| Test | Window | Expected |
| --- | --- | --- |
| DATE-01 | none | every visible date |
| DATE-02 | `start` only | from the start edge |
| DATE-03 | `end` only | up to the end edge |
| DATE-04 | both | the closed range |
| DATE-05 | `start` = a session | that session included |
| DATE-06 | `end` = a session | that session included |
| DATE-07 | one date, current + revision | the version visible at each instant |
| DATE-08 | revision outside the range | it does not leak into the result |

Verification after this round: **571 tests** in the suite, **60 tests** in
`tests/test_price_pit.py`, `compileall` exit 0, `git diff --check` clean. The
production database was not modified: it still holds 5 107 rows, 73
`adjusted_close` rows without availability and 0 revisions.

## 16. Stage B status — delivered (2026-11-18)

Stage B is the model-and-access gate: it pins what a strategy *is* and proves
that every price a backtest reads is the version public at the instant it asks.
No simulation, cost or metric code exists yet.

### 16.1 Deliverables

| Path | Status | Content |
|---|---|---|
| `fa/backtesting/__init__.py` | new | re-exports the fifteen public names; docstring records why the package must not import the fetch stack |
| `fa/backtesting/models.py` | new | `Instrument`, `RebalanceRule`, `CostModel`, `StrategySpec`, `Position`, `Trade`, `EquityPoint`, `BacktestMetrics`, `BacktestSpecError`, `with_initial_capital`; frozen dataclasses only, no I/O |
| `fa/backtesting/data_access.py` | new | `DataAccess` over `Repository` (`prices`, `price_at`, `fx`, `fx_rate`, `official_nav_history`, `revision_rows`) and `utc_now()` |
| `tests/fixtures/backtesting.py` | new | synthetic repository (`build_store`), instrument helpers, `make_spec` |
| `tests/test_backtest_models.py` | new | 29 tests on spec validation and the simulation objects |
| `tests/test_backtest_data_access.py` | new | 18 tests on PIT reads, venue/currency guards and isolation |
| `fa/models/market_data.py` | modified | `PricePoint.revision_id` (`0` = current row, negative rejected) so a fingerprint can name the version it consumed |
| `fa/data/database/repository.py` | modified | `_price_point_from_row()` carries `revision_id` from the `prices_as_of()` UNION |
| `fa/__init__.py` | modified | lazy PEP 562 exports so `import fa.backtesting` does not drag in `requests`/`yfinance`/`akshare`; `__all__` unchanged |

Untouched, as required: `fa/data/database/schema.py` (schema stays 3, no
Migration 004 in Stage B), the FX modules, `nav_estimator.py`, the CLI and the
GUI.

### 16.2 Behaviour

- `StrategySpec` refuses the mistakes that would silently change what the run
  means: an empty or duplicated universe, missing/zero/negative
  `initial_capital` (no default), any base currency other than CNY, a CNY
  instrument (the base is not a holding), fixed weights that do not cover the
  universe or do not sum to 1 within `1e-6`, `target_weights` alongside a rule
  weighting, a momentum window below 20 sessions, and anchor values outside each
  frequency's range. `RebalanceRule(frequency="daily")` forces `anchor=None`.
  Long-only is the contract: `Position` rejects a negative quantity and
  `EquityPoint` rejects negative cash, so leverage cannot appear by accident.
- `DataAccess.prices()` goes through `prices_as_of()` only. It rejects an empty
  result, a venue that differs from the instrument (`normalise_exchange`, so
  `NYQ` and `US` agree while `SIX` and `NYSE` do not) and a differing quote
  currency; `price_at()` returns exactly one session or raises.
- FX is publication-gated too: CNY needs no series (`fx()` returns `None`,
  `fx_rate()` returns `(1.0, session)`), a foreign series must be quoted
  `CNY per 1 unit`, and `fx_rate()` uses `rate_on_or_before()` so a stale rate is
  visible as a stale *date* rather than silently bridging a gap.
- `revision_rows()` exposes `(ticker, date, revision_id)` so the Stage D
  fingerprint can include the version identity without reopening the database.

### 16.3 Verification

| Check | Result |
|---|---|
| `python -m unittest tests.test_backtest_models tests.test_backtest_data_access` | **65 tests OK** (35 + 30) |
| `python -m unittest discover -s tests -t .` | **636 tests OK** |
| `python -m compileall -q fa tests scripts fund_analyzer.py fund_analyzer_gui.py` | exit 0 |
| `git diff --check` | clean |
| No network import (BT-10) | a subprocess imports `fa.backtesting` and prints none of `requests`/`yfinance`/`akshare` |
| PIT-only reads (BT-01) | monkeypatching `Repository.load_prices` to raise does not affect `DataAccess`; an AST scan forbids `sqlite3`, `execute*`, `load_prices` and the fetch libraries inside `fa/backtesting/` |
| Revision identity | the pre-revision close is visible with `revision_id > 0`, the current close with `0` |

The isolation scan is AST-based on purpose: `data_access.py` *mentions*
`load_prices()` in its docstring precisely to say it is never used, and a
substring scan would flag the explanation instead of the call.

### 16.4 Known limits

- No engine, cost or metric code exists yet; `BacktestMetrics` is a container
  that Stage D will populate, and `as_dict()` reports `None` (not `0`) for an
  undefined ratio so a missing CAGR cannot be read as a flat strategy.
- `official_nav_history()` returns `[(date, nav)]` without a publication
  instant, so it is reporting-only — a simulation must not treat it as a PIT
  signal.
- The fixture series are synthetic (`LLY` 100 + i, `0700` 400 + 2i, USD 7.10,
  HKD 0.91); they exist to pin behaviour, not to produce plausible returns.
- The fingerprint itself belongs to Stage D; Stage B only guarantees the inputs
  it will need (`available_at`, `revision_id`).

### 16.5 Review fixes on top of Stage B (2026-11-18)

The Stage B review (ZIP `…000124`, SHA-256 `2C7CB6C9…`) accepted the structure
and asked for three changes before Stage C: an FX lookup that could not use an
earlier session's rate, a fail-open market/currency guard, and missing NaN /
Infinity validation. All three are fixed here; no other module changed.

| ID | Defect | Fix |
|---|---|---|
| **FX** | `fx_rate()` called `self.fx(currency, as_of, start=session, end=session)`, so an FX holiday returned `None` even when the previous session's rate was public and usable — the SQL range and `rate_on_or_before()` disagreed about what the query meant | the read is now `self.fx(currency, as_of, end=session)`: every rate public by `as_of` up to `session`, then `rate_on_or_before(session)` picks the latest one. The publication gate is unchanged, so a bar that is not yet published still cannot be used |
| **MARKET (a)** | `Instrument(market="")` was accepted, `normalise_exchange("")` returned an empty string, and `_check_market()` skipped the comparison — an empty market disabled the guard | `Instrument.__post_init__` now requires a non-empty market |
| **MARKET (b)** | a stored point with an empty `market` was silently accepted | `_check_market()` rejects an empty stored venue instead of skipping it |
| **MARKET (c)** | only the first point's currency was checked while every point's market was | `_check_currency()` now takes the whole series and checks every point, and rejects a missing `currency` |
| **NaN/INF** | `NaN < 0` is `False`, so `Position(quantity=nan)`, `EquityPoint(cash_base=nan)`, `CostModel(min_commission=inf)` and `StrategySpec(initial_capital=inf)` all passed the sign checks | new `_require_finite()` helper applied to every numeric field that reaches a calculation: `StrategySpec.initial_capital`/`drift_threshold`, all four `CostModel` fields, `Position.quantity`/`price`/`market_value_base`, `Trade.quantity`/`price_local`/`fx_rate`/the four cost amounts/`gross_base`/`net_base`, and `EquityPoint`'s four values plus optional `benchmark_base`/`drawdown` |

Regression tests: `FxFallbackTestCase` (FX-01 same session, FX-02 missing session
falls back, FX-03 gap between two sessions, FX-04 an unpublished rate is never
used but becomes usable once public, FX-05 no earlier rate returns `None`),
`MarketMetadataTests` (empty instrument market, empty stored market, mixed
venues, and `NYQ`/`US` still comparing equal), `CurrencyMetadataTests` (empty
stored currency, a second point in another currency, a consistent series) and
`FiniteNumberTests` (NaN, +inf and −inf on every listed field, with zero still
allowed where it was allowed).

Verification after this round: **65 tests** in the two Stage B modules (35 + 30),
**636 tests** in the suite, `compileall` exit 0, `git diff --check` clean. The
production database was not modified.

## 17. Stage C status — delivered (2026-11-19)

Stage C is the engine gate: costs, the session grid, rebalancing, order
execution and valuation, all reading through `DataAccess` only.
`fa/backtesting/costs.py` and `fa/backtesting/engine.py` are new; the fixture
gained `raw_price_point()`; `fa/backtesting/__init__.py` exports `EngineResult`,
`run_engine` and `TradeCosts`. `models.py` changed in exactly one place: the
`CostModel` defaults, so they match §6 (decision C-D1 below).

### 17.1 Decisions applied

| ID | Decision | Effect |
|---|---|---|
| C-D1 | The `CostModel` defaults follow §6: commission 5 bps, minimum 0, slippage 10 bps, FX spread 20 bps | `CostModel()` now means 0.0005 / 0.0 / 10.0 / 20.0, replacing the shipped 0.0003 / 5.0 / 5.0 / 10.0 that contradicted the specification |
| C-D2 | Momentum is the positive part, normalised: `S_i = max(M_i, 0)`, `w_i = S_i / ΣS_j` | all scores ≤ 0 ⇒ the portfolio holds cash; a ticker with fewer than `L + 1` visible sessions is excluded with a `momentum-excluded` anomaly, never a shortened window |

### 17.2 Behaviour worth pinning

- **Session grid** — the union of every universe venue's trading days. It is
  built by walking the date range and asking each calendar, because
  `HolidayCalendar.trading_days_between()` returns a *count*, not days.
- **Decision and execution instants** — a session's decision uses prices public
  at that session's close + 30 min. A decision taken at `t` executes at `t + 1`,
  and the fill price is the `t + 1` close read through a PIT query at
  `t + 1` close + 30 min. A price or FX rate that is not public at the query
  instant skips that order (`missing-price` / `missing-fx`) — it is never
  replaced by a nearby or stale value.
- **Closed venues inside the grid** — a US holiday inside a Hong Kong session is
  expected, not a data defect: the position is carried at its last price with no
  anomaly. A genuine gap on a session the venue *did* trade reports
  `missing-price`.
- **Execution** — sells before buys; a buy bisects its quantity against
  `gross + commission + slippage + FX spread`, floors the result to the persisted
  6-dp quantum when cash-limited, then steps down one quantum until it fits
  (100 iterations maximum). Cash never goes negative and a trade that would not
  change the stored quantity is skipped.
- **Costs** — recorded on the trade, not folded into `price_local`, so a later
  metric cannot charge them twice. Rounding happens once, at 2 dp for money and
  6 dp for quantities, with `ROUND_HALF_EVEN`.
- **Corporate actions** — applied only when the series has no `adj_close`:
  dividends are credited as cash, splits scale the quantity, and the first such
  ticker is reported as `raw-price-fallback`. A series that mixes both modes
  reports `mixed-price-mode`.
- **Determinism** — no clock, randomness, environment or database handle; the
  AST isolation test forbids `sqlite3`, `requests`, `yfinance`, `akshare`,
  `load_prices`, `execute*` and `datetime.now`/`date.today` in `engine.py`.

### 17.3 Gate evidence (C-G1…C-G10)

| Gate | Evidence |
|---|---|
| C-G1 costs match hand arithmetic | `tests/test_backtest_costs.py` (13 tests) uses literal hand formulas: 10 000 gross → 5.0 commission; a 2.0 floor; 100 000 → 100.0 slippage; 60 000 foreign notional → 120.0 FX spread; CNY → 0; `0.625 → 0.62` (half-even); NaN/inf/negative rejected; a zero-cost model yields zeros |
| C-G2 hand case to 1e-9 | `HandComputedTests`: AAA 100 / BBB 40, FX 7.0, capital 700 000, fixed 0.6/0.4, one rebalance — 600 and 1 000 shares, cash 0, total 700 000, exposure 1.0, asserted with `delta=1e-9` |
| C-G3 direction, quantity, costs | the same test with §6 costs: AAA gross 420 000, commission 210.00, slippage 420.00, FX spread 840.00, net 421 470.00; the second buy is cash-limited and the accounting identities hold within 0.01 |
| C-G4 cash never negative | `ExecutionTests` (daily rebalance, 30 sessions) and the cash-limited assertion in the hand case |
| C-G5 no silent substitution | `price_at()` returning nothing skips the order; `test_eng_14` keeps the other order intact; `PricePointInTimeTests` in the data layer prove the query itself |
| C-G6 unpublished price/FX never affects a decision | `tests/test_backtest_pit.py`: PIT-02 (one microsecond late is invisible), PIT-04 (a late execution price is not substituted), PIT-05 (two databases differing only in an unpublished value produce identical trades *and* equity curves), PIT-06 (publishing it does change the run), PIT-08 (legacy rows without `available_at` are never used) |
| C-G7 identical inputs, identical run | `test_eng_20_same_inputs_produce_the_same_run` compares trades, equity curve and anomalies |
| C-G8 no DB/network/clock | `IsolationTests` parses `engine.py` with `ast` and rejects the forbidden imports and calls |
| C-G9 suite green | 679 tests |
| C-G10 `compileall` / `git diff --check` | both exit 0 |

### 17.4 Verification

| Check | Result |
|---|---|
| `python -m unittest tests.test_backtest_costs` | 13 tests OK |
| `python -m unittest tests.test_backtest_engine` | 30 tests OK |
| `python -m unittest tests.test_backtest_pit` | 8 tests OK |
| `python -m unittest discover -s tests -t .` | **687 tests OK** (38.8 s) |
| `python -m compileall -q fa tests scripts fund_analyzer.py fund_analyzer_gui.py` | exit 0 |
| `git diff --check` | clean |

The production database was not touched by this stage: the engine only reads
through `DataAccess`, and every test uses a temporary SQLite file.

### 17.5 Known limits

- `EquityPoint.benchmark_base` stays `None`; a benchmark series arrives with the
  metrics stage.
- A trade records `price_source` (`adj_close`/`close`) but not the revision id
  that supplied it; the content fingerprint of Stage D is what will pin that.
- Whole-share mode floors a buy to an integer, so a target smaller than one
  share can round to zero and skip the order (no anomaly: nothing was lost).
- FX spread is charged in the base currency on the converted notional; a future
  multi-leg conversion would need a per-leg split.

### 17.6 Review fixes on top of Stage C (2026-11-19)

A code review of the delivered package found three execution defects. All three
were in `fa/backtesting/engine.py`; nothing else changed except the tests and the
documents listed in the roadmap.

| ID | Defect | Fix | Regression tests |
|---|---|---|---|
| P1-1 | `_price_of()` served both the fill and the mark, so a trade filled at the adjusted close. With `close = 100` and `adj_close = 95`, a 100-share buy was booked as 9 500 instead of 10 000 — which then moved the quantity, commission, slippage, FX spread and cash. | Split into `_execution_price()` (always `close`, per §5.3 "the fill price is that session's official close") and `_valuation_price()` (`adj_close` when present). `_execute()`, `_buy()` and `_sell()` use the execution price; `_valuation()` and the momentum window use the valuation price; `Trade.price_local` now carries the official close and `price_source` says `close`. | `ExecutionPriceTests`: the fill is 100 with `price_source="close"` and the quantity comes from 100, not 95; the post-trade mark is still 95; a raw series with no `adj_close` marks at its close |
| P1-2 | `_apply_corporate_actions()` did `self._fx(...) or (1.0, session)`, so an unpublished rate silently became 1:1 and 100 USD of dividend arrived as 100 CNY, inventing cash. | The dividend is credited only when a rate is published (`_fx()` already returns `1.0` for CNY); otherwise the engine records `missing-fx-dividend: …` and leaves cash unchanged. Splits are unaffected because they never need a rate. | `DividendFxTests`: with a rate the cash rises by `quantity × dividend × rate`; with the rate removed from the ex-date on, cash does not move and the anomaly is present (no 1:1 fallback) |
| P1-3 | `_execute()` reported `missing-price` whenever `_point()` returned `None`, but a venue closed inside the union grid returns `None` by design. Every cross-market holiday therefore produced a false data-quality anomaly. | Execution now checks `_market_trades()` first and skips the order silently — no anomaly, no stale-price fill, no change in position or cash. A venue that *is* open but has no price still records `missing-price`. | `ClosedMarketExecutionTests`: on 2025-01-20 (US holiday, Hong Kong open) the US order is skipped with no `missing-price`, Hong Kong keeps trading, the closed position is still marked, and a genuine gap on a US trading day still raises `missing-price: LLY` |

Two corrections are worth stating explicitly because they are easy to reintroduce:

- Forgetting to change the *call sites* is the actual P1-1 bug; changing only the
  function definition would leave the old behaviour in place. The regression
  tests assert `Trade.price_local` and the derived quantity, not the helper name.
- A closed venue must never be repaired by carrying the previous close into a
  fill. The engine keeps that price for *valuation* only; orders for a closed
  venue are dropped, and the next rebalance decides whether to try again.

The CNY branch of P1-2 cannot be exercised at engine level: `Instrument` rejects a
CNY position ("人民幣是計價基準，不是持倉幣別", a Stage B decision), so a CNY
dividend has no instrument to attach to. The underlying rule — `DataAccess.fx_rate()`
returns `(1.0, session)` for CNY without consulting the database — is already
pinned by `tests/test_backtest_data_access.py::FxAccessTests`.

## 18. Stage D status — delivered (2026-11-19)

Stage D adds the metrics, the runner and the persistence layer. The engine, the
cost model and the Stage B modules are unchanged.

### 18.1 Deliverables

| File | Change |
|---|---|
| `fa/backtesting/metrics.py` | new — `compute_metrics()` implementing §7 |
| `fa/backtesting/runner.py` | new — `RunConfig`, `resolve_config()`, `canonical_spec()`, `data_fingerprint()`, `run_id_for()`, `run_backtest()` |
| `fa/backtesting/benchmark.py` | new — `benchmark_levels()`, `benchmark_rows()` (PIT benchmark series in CNY) |
| `fa/backtesting/data_access.py` | `BacktestDataMissingError` / `BacktestDataIntegrityError`, `series_for()`, `instrument_metadata()` |
| `fa/backtesting/models.py` | `BacktestRun` container (frozen dataclass) only |
| `fa/data/database/schema.py` | `SCHEMA_VERSION = 4`, `MIGRATION_004` (incl. `anomalies_json`) |
| `fa/data/database/repository.py` | `save_backtest_run()`, `load_backtest_run()`, `count_backtest_runs()`, `price_metadata()` |
| `fa/backtesting/engine.py` | catches `BacktestDataMissingError` only — integrity errors abort |
| `fa/config.py` | the §9 `BACKTEST_*` keys with `FA_BACKTEST_*` overrides |
| `fa/backtesting/__init__.py` | exports `BacktestRun`, `compute_metrics`, `run_backtest` |
| `tests/test_backtest_metrics.py` | 16 tests |
| `tests/test_backtest_runner.py` | 11 tests |
| `tests/test_backtest_fingerprint.py` | 4 tests |
| `tests/test_backtest_execution_instant.py` | 4 tests |
| `tests/test_backtest_benchmark.py` | 12 tests |
| `tests/test_backtest_run_identity.py` | 10 tests |
| `tests/test_backtest_data_integrity.py` | 7 tests |
| `tests/test_migration_004.py` | 9 tests (7 on the frozen baseline, 2 statement audits) |

Untouched: `fa/backtesting/costs.py`, the FX modules, the price pipeline,
`fa/analysis/nav_estimator.py`, the CLI and the GUI. No Migration 001–003
statement changed.

### 18.2 Behaviour

- **Metrics** follow §7 literally. Undefined ratios are `None`, never `0`:
  `sortino` without a negative excess return, `calmar` without a drawdown,
  `hit_rate` without a closed round trip.
- **`data_fingerprint()` hashes content, not a range summary.** Per row it covers
  `ticker, currency, date, close, adj_close, market_timestamp, available_at,
  availability_source, source, fetched_at, revision_id, dividend, split`; the
  benchmark's own PIT rows follow under the same encoding; a *missing* ticker
  (`BacktestDataMissingError`) is recorded as `missing`. The digest is
  `sha256("{MODEL_VERSION}|{SCHEMA_VERSION}|{payload}")`. A revision that lands
  inside the window therefore changes the fingerprint even when the row count,
  the date range and `max(fetched_at)` are unchanged, and a dividend-only or
  split-only correction changes it too — both were defects this stage had to
  close.
- **Integrity is not absence.** `DataAccess` raises `BacktestDataMissingError`
  for a hole in valid data (no rows, no published FX) and
  `BacktestDataIntegrityError` when the data contradicts the instrument (venue
  mismatch, per-point currency mismatch, FX base ≠ CNY, missing metadata). The
  engine catches only the first — an integrity error aborts the run — and the
  fingerprint only ever encodes the first as `missing`, so a broken dataset
  cannot look like a reproducible one.
- **Effective execution settings are part of the identity.** `resolve_config()`
  resolves decision lag, execution lag, fractional shares, risk-free rate and
  the effective `CostModel`; `canonical_spec(spec, runtime=…)` embeds them, so
  `run_id` changes whenever any of them changes, and `spec_json`/`costs_json`
  persist exactly what ran. Precedence: an explicit `CostModel` on the spec wins;
  the environment block is only the default, applied when the spec left
  `CostModel()` untouched. The resolved values — not merely their hash — are what
  `run_engine()` and `compute_metrics()` receive.
- **`run_id` = `sha256(canonical_json(spec, runtime) + MODEL_VERSION +
  data_fingerprint)`.** `initial_capital` is part of the canonical spec, so the
  same data with a different capital is a different run.
  `run_backtest(..., force=True)` bypasses the reuse check and suffixes the id
  with `-N`.
- **Anomalies persist.** `strategy_backtest_runs.anomalies_json` stores them, so
  a cache hit from `load_backtest_run()` returns the same anomalies as the
  original run instead of `()`.
- **The benchmark follows the same PIT path, session by session.**
  `benchmark_series()` reads each session through `DataAccess.series_for()`
  **as of that session's own decision instant** (the same `close + lag` the
  engine uses) on the benchmark's own venue and currency, and converts with the
  FX rate public at that same instant — a bar or rate published later can never
  fill an earlier point. It normalises the first usable level to
  `initial_capital`, carries the previous level only while its venue is closed,
  and leaves the point empty with `benchmark-missing-price` /
  `benchmark-missing-fx` / `benchmark-unavailable` when the venue is open and the
  number is not yet public. It values the benchmark with `adj_close` when the
  vendor provides one and the raw close otherwise, and the fingerprint records
  the price actually used plus `price_source`. The consumed prices and the
  per-session FX rates join the data fingerprint, so revising GBP/CNY changes the
  identity of a run whose strategy never touched GBP. `run_backtest()` writes the
  levels into `EquityPoint.benchmark_base` and appends the benchmark anomalies.
- **Persistence is one transaction.** `save_backtest_run()` writes the run, its
  metrics, the trades (`seq` order) and the equity curve inside a single
  `with self._conn:` block; a `UNIQUE` violation becomes `DuplicateRunError`.
- **`run_backtest()` reuses a stored run** when the `run_id` already exists and
  `force` is false, returning `(run, False)`; it never re-runs the engine just to
  recompute numbers.

### 18.3 Gate D evidence

| Gate | Evidence |
|---|---|
| BT-07 metrics | `tests/test_backtest_metrics.py` — every ratio against a hand formula, `None` for undefined cases, empty curve rejected |
| BT-08 persistence | `tests/test_backtest_runner.py` — round trip, idempotent second call, `force=True` → `base-1`, injected trigger failure leaves zero runs |
| BT-09 fingerprint | `tests/test_backtest_fingerprint.py` — a revision moves the digest while count/range/`max(fetched_at)` stay equal; a revision published later leaves the earlier sessions' digest unchanged and changes the post-publication window; two databases with different histories hash differently |
| BT-14 execution instant | `tests/test_backtest_execution_instant.py` — the bar is invisible at the close, visible at close + 30 min, the recorded fill is the session close with `price_source == "close"`, and a late publication is never substituted |
| Migration 004 | `tests/test_migration_004.py` — 3 → 4 on the frozen baseline, documented columns (incl. `anomalies_json`) and index, untouched legacy rows, idempotent re-run, rollback on injected failure, fresh database reaches 4, additive-only SQL |
| Corporate-action identity | `tests/test_backtest_run_identity.py::FingerprintCorporateActionTests` — a dividend-only and a split-only correction change the post-publication window's digest while every earlier session's digest stays identical |
| Anomaly round trip | `tests/test_backtest_run_identity.py::AnomalyPersistenceTests` — a run with anomalies returns the same tuple from a cache hit and from `load_backtest_run()`; an empty tuple stays `()` |
| Runtime identity | `tests/test_backtest_run_identity.py::ConfigIdentityTests` / `CostConfigTests` — a changed execution lag, risk-free rate or whole-share flag yields a new `run_id`, and `FA_BACKTEST_COMMISSION_BPS` visibly moves the commission line while an explicit zero `CostModel` wins |
| Integrity aborts | `tests/test_backtest_data_integrity.py` — venue flip, empty venue, currency mismatch and a non-CNY FX base raise `BacktestDataIntegrityError` from `prices()`, `run_engine()` and `data_fingerprint()`; a genuine hole stays `BacktestDataMissingError` |
| Benchmark (P1-6) | `tests/test_backtest_benchmark.py::BenchmarkPitTests` — a price published after its session never fills it, a price published at the decision instant does, a late FX rate cannot convert an earlier session, a closed venue carries, and an open venue without data is `None` + anomaly (never forward-filled) |
| Benchmark identity (P1-7) | `tests/test_backtest_benchmark.py::BenchmarkFingerprintTests` — the consumed prices (with the price actually used) and the per-session FX rates join the digest, a revised benchmark price or GBP rate published afterwards moves it, and a CNY benchmark contributes no FX rows |

### 18.4 Verification

| Command | Result |
|---|---|
| `python -m unittest tests.test_backtest_metrics tests.test_backtest_runner tests.test_backtest_fingerprint tests.test_backtest_execution_instant tests.test_migration_004` | `Ran 41 tests … OK` (Stage D delivery) |
| `python -m unittest tests.test_backtest_benchmark tests.test_backtest_run_identity tests.test_backtest_data_integrity` | `Ran 33 tests … OK` (rounds 2–3) |
| `python -m unittest tests.test_backtest_fingerprint tests.test_backtest_benchmark tests.test_backtest_run_identity tests.test_backtest_data_integrity tests.test_backtest_runner` | `Ran 54 tests … OK` (round 4) |
| `python -m unittest tests.test_backtest_benchmark tests.test_backtest_run_identity tests.test_backtest_data_integrity tests.test_backtest_runner tests.test_backtest_fingerprint` | `Ran 60 tests … OK` (round 5; measured by the reviewer's own run and reproduced here) |
| `python -m unittest tests.test_backtest_fingerprint tests.test_backtest_benchmark tests.test_backtest_run_identity tests.test_backtest_data_integrity tests.test_backtest_runner` | `Ran 65 tests … OK` (round 6) |
| `python -m unittest discover -s tests -t .` | `Ran 778 tests … OK` |
| `python -m compileall -q fa tests fund_analyzer.py fund_analyzer_gui.py` | exit 0 |
| `git diff --check` | exit 0 |

### 18.5 Known limits

- The equity curve is stored per session; a very long run (10 years, daily) is
  roughly 2 500 rows per run — acceptable at this scale, but there is no
  retention policy yet.
- `look_ahead_status` is written as `unknown` for every new run because the
  backtest engine reads only PIT data; it is not verified per run.
- `benchmark_base` uses `adj_close` when the vendor provides it and the raw close
  otherwise; the fingerprint records which one was used. An index without an
  adjusted series therefore remains a price-return approximation.
- FX has no PIT version history (decision Q7). `save_fx()` keeps a revised rate's
  original `available_at`, so an in-place FX correction becomes visible to
  decision instants before it landed, and an earlier window's fingerprint changes
  when the correction is written. Prices do not have this problem: `save_prices()`
  pushes availability to `replaced_at` and `prices_as_of()` reconstructs the
  previous version. Removing the limitation needs an FX version ledger, which is
  out of scope for V2.3.2 and is pinned by
  `FingerprintFxTests.test_an_in_place_fx_revision_still_reaches_earlier_sessions`.
- `StrategySpec.costs` and the environment defaults are resolved once per run and
  persisted in `spec_json`; editing `fa/config.py` between two runs changes the
  identity only because the resolved block is hashed, which is intended.

### 18.6 Review fixes on top of Stage D (2026-11-19)

| Defect | Fix | Test |
|---|---|---|
| Fingerprint omitted `dividend` and `split`, so a corporate-action-only correction reused a stale run | `_row_payload()` emits both as None-safe floats; `benchmark_rows()` joined the payload | `FingerprintCorporateActionTests` |
| `BacktestRun.anomalies` was not persisted, so a cache hit returned `()` | `anomalies_json TEXT NOT NULL DEFAULT '[]'` in Migration 004, written with `json.dumps(..., ensure_ascii=False)`, read with `json.loads(... or "[]")` | `AnomalyPersistenceTests` |
| Effective execution settings were outside the run identity, and four `BACKTEST_*` cost keys were never read | `RunConfig` + `resolve_config()`, embedded in `canonical_spec(spec, runtime=…)`/`run_id_for(..., runtime)`, persisted in `spec_json`/`costs_json`, and passed into `run_engine()`/`compute_metrics()` | `ConfigIdentityTests`, `CostConfigTests` |
| Any `BacktestDataError` became `None`, so a contradictory dataset looked like absent data | `BacktestDataMissingError` vs `BacktestDataIntegrityError`; the engine catches only the former and the fingerprint encodes only the former | `tests/test_backtest_data_integrity.py` |
| `benchmark_base` stayed `None` although the benchmark was agreed for Stage D | `fa/backtesting/benchmark.py` reads it PIT, converts to CNY, normalises to `initial_capital`, carries on closed venues, records anomalies, and joins the fingerprint | `tests/test_backtest_benchmark.py` |

The review also confirmed that the defect "silently treating an integrity error
as missing data" was deferred from the Stage C round; it is closed here.

### 18.7 Round-3 review fixes (2026-11-19)

| Defect | Fix | Test |
|---|---|---|
| The benchmark curve looked ahead: the whole window was read at the run's final instant, so a February publication filled January | `benchmark_series()` reads each session at its own decision instant, converts FX at that instant, carries only on a closed venue and leaves an open venue without data empty plus an anomaly | `BenchmarkPitTests` |
| Benchmark FX was outside the fingerprint, so a revised GBP/CNY reused the old run | the consumed prices (close/adj_close, the price used and its source) *and* the per-session FX rates join `data_fingerprint()` | `BenchmarkFingerprintTests` |
| An explicit `CostModel()` was indistinguishable from an omitted one, so `FA_BACKTEST_COMMISSION_BPS` overrode it | `COST_MODEL_DEFAULT` sentinel compared by identity in `resolve_config()` | `CostConfigTests.test_an_explicit_default_cost_model_is_still_explicit` |

`benchmark_levels()` / `benchmark_rows()` lost their `as_of` argument: a single instant for a multi-session curve is exactly the defect above.

### 18.8 Round-4 review fixes (2026-11-19)

| Defect | Fix | Test |
|---|---|---|
| `revision_id` sat in the fingerprint payload, so a correction published later changed the digest of instants that never saw it | `_row_payload()` and `BenchmarkSeries.fingerprint_rows()` drop `revision_id`; the value, its `available_at`, `availability_source` and source remain | `test_a_future_revision_does_not_change_the_earlier_fingerprint`, `test_published_revision_changes_the_fingerprint` |
| The strategy leg hashed one final instant while the benchmark was per-session, so a January decision and a March revision were indistinguishable | `data_fingerprint(sessions=…, lag_minutes=…)` walks the grid, records only rows that became visible since the previous instant (including the momentum lookback), records the FX rate used per session, records a missing marker per session, and still aborts on integrity errors | `FingerprintContentTests`, `FingerprintFxTests`, `BackendRunIdentityTests.test_two_databases_with_different_histories_hash_differently` |
| The benchmark valued with `close` while the spec claimed a total-return approximation | `benchmark_series()` prefers `adj_close`, falls back to `close`, and its rows carry `price`/`price_source`, so a dividend-driven `adj_close` revision changes the digest | `test_rows_carry_the_price_actually_used`, `test_an_adjusted_close_is_preferred_for_the_level` |

Numbers: `Ran 54 tests … OK` for the five affected modules, `Ran 767 tests … OK`
for the whole suite, `compileall` and `git diff --check` clean.

One part of the review request cannot be satisfied in this scope: a future FX
revision is visible to earlier decision instants, because FX has no version
ledger (Q7 reconstructs prices only). `FingerprintFxTests` pins the behaviour
rather than hiding it, and §18.5 records what an FX ledger would need.

### 18.9 Round-5 review fixes (2026-11-19)

| Defect | Fix | Test |
|---|---|---|
| The strategy FX fragment read a bounded window (`start` bound) and then `rate_on_or_before()`, while the engine calls `fx_rate()`, which falls back to an older observation; a usable rate could therefore be hashed as `missing` | the fragment now calls `data_access.fx_rate(currency, instant, session)` and records `{currency, session, rate, rate_date}`; the observation date is part of the digest because the engine writes it into every trade | `StrategyFxInputTests.test_an_observation_before_the_lookback_is_used_not_missing`, `test_the_observation_date_is_part_of_the_digest`, `test_a_missing_rate_stays_missing` |
| The momentum lookback came from `calendar_for(spec.universe[0].market)`, so a Hong Kong venue inside a US-first universe lost observations the engine consumes | the lookback is gone; each instrument is read without a start bound and the fingerprint hashes the last `window + 1` observations (the momentum input set), or the whole visible series when the spec has no momentum | `CrossMarketMomentumFingerprintTests.test_a_sparse_venue_keeps_its_whole_momentum_window`, `test_history_outside_every_window_stays_out` |
| `fetched_at` sat in the price payload although `save_prices()` refreshes it on every re-download, so an identical download invalidated runs without any market input changing | `_row_payload()` dropped `fetched_at`; `date`, `close`, `adj_close`, `dividend`, `split`, `market_timestamp`, `available_at`, `availability_source` and `source` remain | `FingerprintContentTests.test_identical_redownload_does_not_change_fingerprint` |

Numbers: `Ran 60 tests … OK` for the five affected modules, `Ran 773 tests … OK`
for the whole suite (47.0 s), `compileall` and `git diff --check` clean. The spec
header was corrected at the same time: at that point Stage D was **pending final
independent acceptance**, not delivered-and-closed, and Stage E was unauthorised
(Stage D was accepted in Round 9 and Stage E was delivered in the Stage E work;
the header at the top of this document records the current state).

The FX version-ledger limitation of §18.5 is untouched by this round: Q7 option
(a) stands, no migration changed, and
`FingerprintFxTests.test_an_in_place_fx_revision_still_reaches_earlier_sessions`
still pins the behaviour. The round-5 defect was different — the fragment did
not describe the rate the engine used — and is closed by the first row above.
### 18.10 Round-6 review fixes (2026-11-19)

| Defect | Fix | Test |
|---|---|---|
| A no-momentum strategy hashed the whole visible price series, so two databases differing only in a bar from before the run start produced different digests (a cache miss, not a wrong reuse) | `data_fingerprint()` now hashes only the reads the engine makes: FIXED/EQUAL record the `price_at()` observation per open session with `role = "session_price"`; MOMENTUM adds the last `window + 1` observations with `role = "momentum_window"` **only on rebalance days**; a closed venue contributes nothing and an open venue with no bar contributes a `missing` marker. The rebalance rule was extracted into the pure `fa/backtesting/engine.py::is_rebalance_day()`, called by both the engine and the fingerprint, so the two cannot drift | `FixedStrategyFingerprintTests`, `CrossMarketMomentumFingerprintTests` |
| §4.4 still described a global `lookback_start` and still listed `fetched_at` among the hashed price fields | items 1–3 of §4.4 rewritten: per-session instants, per-strategy read sets with roles, the field list without `fetched_at`/`revision_id`, and the FX conservative-superset note | documentation |
| §5.9 said an instrument needs `momentum_window` observations, but a momentum value spans `window + 1` observations | reworded to `momentum_window + 1`, with the `momentum-excluded` outcome named | documentation |
| `07_development_roadmap.md` still declared Stage D delivered while the spec header said pending acceptance | roadmap now says "implementation delivered 2026-11-19; pending final independent acceptance" and Stage E "Not started / Not authorised" | documentation |
| The five-module count was documented as 59 | §18.4/§18.9 corrected to the reviewer's measured 60 | documentation |

Numbers: `Ran 65 tests … OK` for the five affected modules, `Ran 778 tests … OK`
for the whole suite (55.1 s), `compileall` and `git diff --check` clean.

The FX fragment still records a rate for every session rather than only the
sessions where the engine spent cash; the reviewer accepted that conservative
superset for this round and asked for it to be documented instead of changed
(see §4.4 item 3). An engine-side input trace remains a future option.

### 18.11 Round-7 review fixes (2026-11-19)

| Defect | Fix | Test |
|---|---|---|
| **P1-17** ⇒ the price loop `continue`d when the instrument's venue was shut, so the `momentum_window` read was skipped too. `_Engine._momentum_weights()` reads every ticker's series regardless of that venue's state, so a Hong Kong holiday inside a US rebalance session could keep an input out of the digest and produce a wrong cache hit | the block is now produced by the new public `fingerprint_session_payload()`, which applies **two** calendar rules: `session_price` only when the venue trades; `momentum_window` on every rebalance day for every instrument, open or shut | `ClosedVenueMomentumFingerprintTests::test_momentum_window_is_hashed_when_venue_is_closed` (US open, HK closed on 2025-01-29, monthly anchor 29, changed HK bar inside the engine's 21-observation tail) |
| **P2-17** — §4.4 claimed the hash records "only the reads the engine itself makes", but `session_price` is recorded for every open session even when a flat position never asks | reworded: `session_price` is a **conservative superset** of the prices the engine could use; only `momentum_window` is a strict reconstruction of an engine read | documentation, plus the role test below |
| The two roles were only checked indirectly through digests | `fingerprint_session_payload()` returns the block, and the new role test asserts the composition directly: shut venue ⇒ `{momentum_window}` only; open rebalance venue ⇒ `{session_price, momentum_window}`; open non-rebalance venue ⇒ `{session_price}`; a shut venue on a non-rebalance day ⇒ nothing | `test_roles_split_session_price_from_momentum_window`, `test_preconditions` |

Numbers: `Ran 98 tests … OK` for the six affected modules, `Ran 781 tests … OK`
for the whole suite (41.2 s), `compileall` and `git diff --check` clean.

---

## 19. Stage E status — delivered 2026-11-20 (pending final independent acceptance)

Stage E is integration and acceptance only: no engine, cost, metric or schema
rule changed. Everything below reads the code that Stages A–D delivered.

### 19.1 Deliverables

| File | Purpose |
|---|---|
| `scripts/accept_backtest_synthetic.py` | E-1: five scenarios on throwaway SQLite, expected values from hand formulas (`V_t = C_t + Σ q·P·FX`), written to `docs/development/stageE_synthetic_acceptance.md` |
| `scripts/audit_working_db.py` | E-2a: read-only audit (schema, counts, PIT coverage, availability sources, legacy rows, FX) → `docs/development/stageE_working_db_audit.md` |
| `scripts/smoke_backtest_real.py` | E-2b: short-window real run on the working database, PIT fill verification → `docs/development/stageE_real_smoke.md` |
| `scripts/backtest_report.py` | E-3: `run` / `list` / `show` over persisted runs; report rendered from SQLite and traceable to `run_id` |
| `config/backtests/stagee_demo.json` | E-3: the example specification submitted through the CLI |
| `scripts/audit_backtest_retention.py` | E-4: retention audit (no deletion) → `docs/development/stageE_retention_audit.md` |
| `scripts/coverage_core.py` | E-5: stdlib-tracer statement coverage → `docs/development/stageE_coverage.md` |
| `docs/development/stageE_bt_matrix.md` | BT-01…BT-14 evidence matrix |

No Stage E business rule changed. Two files under `fa/` did change, to fix a
proven defect rather than to add a feature: `fa/data/database/schema.py` declares
`REVIEW_COLUMN_REPAIRS` (the `anomalies_json` column that was added to Migration
004 while V2.3.2 was still under review) and `fa/data/database/connection.py`
applies exactly that list to an already-applied v4 database. The repair is
deliberately scoped to that one column: replaying `MIGRATION_COLUMNS` for an
applied version would re-add a Migration 002 column without its backfill SQL and
leave the database half-migrated. `tests/test_migration_004.py` covers both the
repair and its scope.

The working database gained two smoke runs and one CLI demo run
(`strategy_backtest_runs` 3 rows); no other table was modified.

### 19.2 Evidence

- **E-1**: `checks=30 failed=0` across FIXED, EQUAL-with-costs, MOMENTUM,
  HK dividend/split and idempotency; every expectation is a hand formula, and
  the report records expected/actual/diff per check.
- **E-2a**: schema 4; `market_prices` 5 107 rows (all foreign tickers with PIT
  coverage 501–510), `fx_rates` 30 rows (CHF/GBP/USD, 2026-11-03…16, all PIT),
  73 legacy `adjusted_close` rows still `unknown` and therefore PIT-invisible
  (decision Q1), `price_revisions` 0.
- **E-2b**: window 2026-11-04…2026-11-17 on ABBV+AMGN, 2 trades, final equity
  100 205.72 CNY, costs 348.78 CNY, every fill equal to the PIT close at the
  execution instant, cash never negative, persistence and metrics reproduced
  after reload, duplicate submission `created=False`.
- **E-3**: `list` shows the stored runs; `show` renders a report whose numbers
  come from the four `strategy_backtest_*` tables (identity, runtime config,
  resolved costs, metrics, cost-by-side, equity curve with benchmark column,
  trades with price/FX source).
- **E-4**: seven integrity checks pass (every run has metrics; no orphans; the
  equity point count equals `sessions`; trade rows equal `trades`; no duplicate
  fingerprint+spec). No pruning is implemented and no retention period is
  chosen — that needs the lead's approval.
- **BT-01…BT-14**: see `docs/development/stageE_bt_matrix.md`; every row maps to
  a test file or a Stage E artefact.

### 19.3 Verification

| Command | Result |
|---|---|
| `python scripts/accept_backtest_synthetic.py` | `checks=30 failed=0` |
| `python scripts/audit_working_db.py` | schema 4, 5 107 price rows, 73 legacy rows reported |
| `python scripts/smoke_backtest_real.py` | `trades=2 failed_checks=0` (the stored run was reused, `created=False`; every fill is still re-checked against the execution instant) |
| `python scripts/backtest_report.py list` | runs listed from SQLite |
| `python scripts/backtest_report.py show <run_id>` | Markdown report written under `output/reports/` |
| `python scripts/audit_backtest_retention.py` | 7/7 integrity checks pass |
| `python scripts/coverage_core.py` | every documented core module ≥ 90 %, total **99.4 %** (704/708) |
| `python -m unittest discover -s tests -t .` | `Ran 846 tests … OK` (Python 3.10.9) |
| `python -m compileall -q fa tests scripts fund_analyzer.py fund_analyzer_gui.py` | exit 0 |
| `git diff --check` | exit 0 |

### 19.4 Known limits and the coverage gate

- The working database only holds a short PIT window (FX from 2026-11-03), so
  the real-data run is a pipeline check, not a performance claim. A multi-year
  ingest needs the documented command in §12.1 and live downloads.
- The benchmark curve is exercised by the synthetic cases and the unit tests;
  the real-data smoke run does not configure one, because the working database
  has no benchmark FX beyond the same two-week window.
- **The 90 % statement-coverage gate is met** for every module in the
  documented target set, measured with the stdlib tracer (`coverage.py` is still
  not a dependency; statement lines only, no branches). The denominator counts
  **every** executable statement — module-level, class-body and function-body,
  docstrings excluded — because the tracer is installed before `unittest`
  discovery, so import-time lines are observable; the earlier function-body-only
  denominator was wrong and is superseded. Total 704/708 = 99.4 % (base 98.1 %,
  `nav_estimator.py` 99.7 %, the other eight modules 100 %); the per-module
  table, the missing line numbers and the coverage-process test count (835, GUI
  excluded) are regenerated on every run in
  `docs/development/stageE_coverage.md`.
- Four executable lines remain uncovered, and each is a path this test suite
  cannot reach without weakening the type contract:
  - `fa/analysis/returns/base.py:45` — the `...` body of the `ReturnCalculator`
    Protocol; it never executes by definition.
  - `fa/analysis/returns/base.py:227` — `target_align` not usable *after* the
    base alignment passed. `align_fx` excludes an unusable target by building a
    missing outcome, which is the path the test suite exercises instead, so this
    defensive branch is only reachable with a hand-built alignment object.
  - `fa/analysis/returns/base.py:237` — a zero base rate. `FxSeries` rejects
    non-positive rates at construction, so a zero can only come from a
    duck-typed stub; the guard stays as defence in depth.
  - `fa/analysis/nav_estimator.py:362` — `except (TypeError, ValueError): pass`
    around the disclosure-age calculation. `select_point_in_time` already drops
    rows whose `holding_date` does not parse, so the guard only fires for a
    selection dict built by hand. It is kept because a bad snapshot must not
    abort an estimate.
- Stage D was **accepted** in the Round 9 review (2026-11-20). Stage E is
  **delivered but not declared accepted**; only the lead can close it.

### 19.5 Round-8 review fixes (2026-11-20)

| # | Finding | Fix | Evidence |
|---|---|---|---|
| 1 | P1: the 90 % coverage gate was not met (88.2 %) | Added focused tests for the uncovered diagnostic paths of `returns/base.py`, `returns/fx.py` and `nav_estimator.py`, plus the calculator type guards and the quality-penalty branches; the tracer counts every executable statement (module-level, class-body and function-body; docstrings excluded) and reports missing line numbers. | `tests/test_returns_coverage_paths.py` (24), `tests/test_nav_estimator_coverage_paths.py` (28), `tests/test_core_coverage_edges.py` (8), `tests/test_returns_calculator_guards.py` (3), coverage 99.4 % (704/708) |
| 2 | P2: self-heal replayed `MIGRATION_COLUMNS` for every applied version | `MIGRATION_COLUMNS` is back to Migration 002 only; the Migration 004 compatibility column lives in `REVIEW_COLUMN_REPAIRS` and is applied only when `schema_version >= 4`. | `tests/test_migration_004.py::Migration004SelfHealTests` incl. the scoped repair test |
| 3 | P2: `backtest_report run` rendered the in-memory run | `run` reloads `repository.load_backtest_run(run_id)` and renders that object; a failed reload is an explicit error. | `scripts/backtest_report.py` |
| 4 | P2: the real smoke used a hard-coded 23:00 UTC instant | Uses `decision_instant(spec, session, lag_minutes=30)`, the engine's rule; the report was regenerated. | `scripts/smoke_backtest_real.py`, `docs/development/stageE_real_smoke.md` |
| 5 | P2: documentation inconsistencies | §19.1 discloses the two database files, §12.1 documents the two-year ingest, README and `06` distinguish the historical counts from the current 846, and the BT matrix only claims PASS where evidence exists. | this document, `README.md`, `06_testing_and_acceptance.md` |
| 6 | P2: BT-11 was not reproducible from the ZIP (no `.git`) | SHA-256 of the two files recorded in the BT matrix; neither file was modified in this round. | `estimation_backtest.py` `6440C73D…`, `nav_estimator.py` `B43443FC…` |
| 7 | Final verification | `coverage_core.py`, the full suite, `compileall` and `git diff --check` all pass; counts and reports regenerated. | see §19.3 |

### 19.6 Round-9 review fixes (2026-11-20)

| # | Finding | Fix | Evidence |
|---|---|---|---|
| 1 | P1: coverage counted only function bodies | `executable_lines()` now walks every `ast.stmt` in the module (module-level, class-body, function-body; docstrings excluded). The earlier justification — "discovery imports `fa` before `sys.settrace()`" — was wrong: the tracer is installed first, so import-time lines are observable. | `scripts/coverage_core.py`; `docs/development/stageE_coverage.md` regenerated: 704/708 = 99.4 %, every documented module ≥ 90 % |
| 2 | P1: the summary quoted `portfolio_valuation.py` 97.4 % while the report showed 100 % | The 97.4 % came from the pre-fix run (old denominator, before the missing tests landed). The figure quoted in §19.3, §19.4, README and the BT matrix is the regenerated report's: 43/43 = 100 %. | §19.3, §19.4 |
| 3 | P2: README line 34 said 782 tests | Current count is 846. The 782 figure survives only in the Stage E history labelled as such. | `README.md` |
| 4 | P2: the roadmap recorded the pre-Round-8 state without a history label | The Stage E block is split into *Initial Stage E delivery, before Round 8 fixes* and *Round 8 fixes*, carrying 846 tests and 99.4 % coverage. | `07_development_roadmap.md` |

Verification after these fixes (Python 3.10.9): `python scripts/coverage_core.py`
→ 835 tests, 704/708 = 99.4 %; `python -m unittest discover -s tests -t .` →
846 tests `OK`; `python -m compileall -q fa tests scripts fund_analyzer.py
fund_analyzer_gui.py` → exit 0; `git diff --check` → exit 0.

Stage status after Round 9: A, B, C and **D accepted**; **E delivered, pending
independent acceptance**; V2.4 not started.
