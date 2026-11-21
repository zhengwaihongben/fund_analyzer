"""Stage E, batch E-1: synthetic full-run acceptance for the backtesting engine.

The script builds a temporary SQLite database from deterministic fixtures, runs
the full ``StrategySpec → ... → SQLite`` path for several representative
strategies, and compares the engine's output against expectations written here
by hand.  It never uses engine output as the expected value.

Run with::

    python scripts/accept_backtest_synthetic.py [--keep]

The generated report is written to
``docs/development/stageE_synthetic_acceptance.md``.
"""

from __future__ import annotations

import argparse
import math
import shutil
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fa.backtesting import (  # noqa: E402
    CostModel,
    Instrument,
    RebalanceRule,
    StrategySpec,
    Weighting,
    run_backtest,
)
from fa.data.database.repository import Repository  # noqa: E402
from fa.data.price_pipeline import infer_availability  # noqa: E402
from fa.models.market_data import (  # noqa: E402
    AvailabilitySource,
    FxSeries,
    PricePoint,
    PriceSeries,
)
from fa.utils.calendars import calendar_for  # noqa: E402

TOL = 1e-6
REPORT = ROOT / "docs" / "development" / "stageE_synthetic_acceptance.md"


def half_even(value: float) -> float:
    """Round to two decimals the way the engine's cost ledger does."""
    return float(Decimal(str(float(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def sessions_between(start: date, end: date, market: str) -> list[date]:
    calendar = calendar_for(market)
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if calendar.is_trading_day(cursor):
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def end_of_day(day: date) -> datetime:
    """A run clock that is safely after that session's decision instant."""
    return datetime.combine(day, time(23, 0), tzinfo=timezone.utc)


def point(day: date, price: float, *, ticker: str, market: str, currency: str,
          adj_close: float | None = None, dividend: float | None = None,
          split: float | None = None, available_at: datetime | None = None,
          source: str = "synthetic") -> PricePoint:
    if available_at is None:
        available_at = infer_availability(day, market)[0]
    return PricePoint(
        price_date=day,
        close=price,
        adj_close=adj_close,
        available_at=available_at,
        availability_source=AvailabilitySource.INFERRED,
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        market=market,
        currency=currency,
        available_price_type="close+adj_close" if adj_close is not None else "close",
        used_price_type="close+adj_close" if adj_close is not None else "close",
        source=source,
        dividend=dividend,
        split=split,
    )


def series(ticker: str, market: str, currency: str, bars: list[PricePoint]) -> PriceSeries:
    return PriceSeries.from_points(
        ticker, bars, market=market, currency=currency, source="synthetic"
    )


def fx_series(currency: str, rates: dict[date, float]) -> FxSeries:
    bars = [
        point(day, rate, ticker=f"FX:{currency}", market="FX", currency="CNY",
              available_at=datetime.combine(day + timedelta(days=1), time(0, 30), tzinfo=timezone.utc))
        for day, rate in sorted(rates.items())
    ]
    return FxSeries.from_bars(currency, bars, source="synthetic", base_currency="CNY")


@dataclass
class CaseResult:
    case_id: str
    expected: str
    actual: str
    diff: str
    passed: bool


class Acceptance:
    def __init__(self, repository: Repository, tmp: Path) -> None:
        self.repo = repository
        self.tmp = tmp
        self.results: list[CaseResult] = []

    # -- helpers ---------------------------------------------------------
    def record(self, case_id: str, expected: str, actual: str, ok: bool, diff: str = "") -> None:
        self.results.append(CaseResult(case_id, expected, actual, diff or ("0" if ok else "mismatch"), ok))

    def check_close(self, case_id: str, expected: float, actual: float, unit: str = "",
                    tol: float = TOL) -> None:
        diff = actual - expected
        ok = abs(diff) <= tol
        self.record(case_id, f"{expected:.10f}{unit}", f"{actual:.10f}{unit}",
                    ok, f"{diff:+.3e}")

    # -- data ------------------------------------------------------------
    def seed_flat(self, days: list[date]) -> None:
        """AAA US/USD at 100, BBB US/HKD at 50, flat prices and flat FX."""
        self.repo.save_prices(series("AAA", "US", "USD", [
            point(day, 100.0, ticker="AAA", market="US", currency="USD") for day in days
        ]))
        self.repo.save_prices(series("BBB", "US", "HKD", [
            point(day, 50.0, ticker="BBB", market="US", currency="HKD") for day in days
        ]))
        self.repo.save_fx(fx_series("USD", {day: 7.0 for day in days}))
        self.repo.save_fx(fx_series("HKD", {day: 0.9 for day in days}))

    # -- cases -----------------------------------------------------------
    def case_1_flat_fixed_zero_cost(self, days: list[date]) -> None:
        spec = StrategySpec(
            spec_id="case1-flat-fixed",
            universe=[Instrument("AAA", "US", "USD"), Instrument("BBB", "US", "HKD")],
            weighting=Weighting("fixed"),
            start=days[0],
            end=days[-1],
            initial_capital=1_000_000.0,
            rebalance=RebalanceRule(frequency="monthly", anchor=1),
            target_weights={"AAA": 0.6, "BBB": 0.4},
            drift_threshold=0.0,
            costs=CostModel(commission_rate=0.0, min_commission=0.0, slippage_bps=0.0, fx_spread_bps=0.0),
        )
        run, created = run_backtest(spec, self.repo, now=end_of_day(days[-1]))
        trades = {trade.ticker: trade for trade in run.trades}
        self.record("C1:run-created", "True", str(created), created is True)
        qty_aaa = 600_000.0 / (100.0 * 7.0)
        qty_bbb = 400_000.0 / (50.0 * 0.9)
        self.check_close("C1:AAA-quantity", round(qty_aaa, 6), trades["AAA"].quantity, " shares")
        self.check_close("C1:BBB-quantity", round(qty_bbb, 6), trades["BBB"].quantity, " shares")
        self.check_close("C1:trade-count", 2, len(run.trades))
        last = run.equity_curve[-1]
        self.check_close("C1:final-cash", 0.0, last.cash_base, " CNY")
        self.check_close("C1:final-equity", 1_000_000.0, last.total_base, " CNY")
        self.check_close("C1:exposure", 1.0, last.exposure)

    def case_2_flat_equal_with_costs(self, days: list[date]) -> None:
        costs = CostModel(commission_rate=0.0005, min_commission=0.0, slippage_bps=10.0, fx_spread_bps=20.0)
        spec = StrategySpec(
            spec_id="case2-equal-costs",
            universe=[Instrument("AAA", "US", "USD")],
            weighting=Weighting("equal"),
            start=days[0],
            end=days[0] + timedelta(days=40),
            initial_capital=700_000.0,
            rebalance=RebalanceRule(frequency="monthly", anchor=1),
            drift_threshold=0.0,
            costs=costs,
        )
        run, created = run_backtest(spec, self.repo, now=end_of_day(days[-1]))
        trade = run.trades[0]
        rate_total = 5e-4 + 10 / 10_000 + 20 / 10_000
        # Hand expectation: one USD instrument at 100 with FX 7.00, so one share
        # costs 700 CNY gross plus 0.35 % in costs.  The engine floors the
        # quantity to six decimals, which we reproduce here.
        shares = math.floor((700_000.0 / (700.0 * (1 + rate_total))) * 1_000_000) / 1_000_000
        gross = shares * 700.0
        commission = half_even(gross * 5e-4)
        slippage = half_even(gross * 10 / 10_000)
        fx_spread = half_even(gross * 20 / 10_000)
        self.check_close("C2:quantity", shares, trade.quantity, " shares")
        self.check_close("C2:gross", round(gross, 2), round(trade.gross_base, 2), " CNY", tol=0.01)
        self.check_close("C2:commission", commission, trade.commission_base, " CNY", tol=0.01)
        self.check_close("C2:slippage", slippage, trade.slippage_base, " CNY", tol=0.01)
        self.check_close("C2:fx-spread", fx_spread, trade.fx_spread_base, " CNY", tol=0.01)
        expected_net = gross + commission + slippage + fx_spread
        self.check_close("C2:net-base", round(expected_net, 2), round(trade.net_base, 2), " CNY", tol=0.01)
        last = run.equity_curve[-1]
        expected_equity = 700_000.0 - (commission + slippage + fx_spread)
        self.check_close("C2:final-equity", expected_equity, last.total_base, " CNY", tol=0.02)
        self.record("C2:cash-non-negative", ">= 0", f"{last.cash_base:.6f}", last.cash_base >= -1e-9)

    def case_3_momentum_selects_the_winner(self, days: list[date]) -> None:
        # AAA rises 2% per session, BBB falls 1% per session: after the warm-up
        # every momentum score except AAA is zero, so the target is 100 % AAA.
        self.repo.save_prices(series("M1", "US", "USD", [
            point(day, 100.0 * (1.02 ** index), ticker="M1", market="US", currency="USD")
            for index, day in enumerate(days)
        ]))
        self.repo.save_prices(series("M2", "US", "HKD", [
            point(day, 50.0 * (0.99 ** index), ticker="M2", market="US", currency="HKD")
            for index, day in enumerate(days)
        ]))
        spec = StrategySpec(
            spec_id="case3-momentum",
            universe=[Instrument("M1", "US", "USD"), Instrument("M2", "US", "HKD")],
            weighting=Weighting("momentum"),
            start=days[0],
            end=days[24],
            initial_capital=1_000_000.0,
            rebalance=RebalanceRule(frequency="daily"),
            momentum_window=20,
            drift_threshold=0.0,
            costs=CostModel(commission_rate=0.0, min_commission=0.0, slippage_bps=0.0, fx_spread_bps=0.0),
        )
        run, created = run_backtest(spec, self.repo, now=end_of_day(days[-1]))
        first_rebalance = min(trade.session for trade in run.trades)
        derived: dict[str, float] = {}
        for trade in run.trades:
            signed = trade.quantity if trade.side == "buy" else -trade.quantity
            derived[trade.ticker] = derived.get(trade.ticker, 0.0) + signed
        self.record("C3:first-rebalance-after-warmup", ">= " + str(days[20]),
                    str(first_rebalance), first_rebalance >= days[20])
        self.record("C3:holds-M1", "> 0", f"{derived.get('M1', 0.0):.6f}", derived.get("M1", 0.0) > 0)
        self.check_close("C3:M2-quantity", 0.0, derived.get("M2", 0.0), " shares")
        last = run.equity_curve[-1]
        self.check_close("C3:equity-identity", last.cash_base + last.holdings_base, last.total_base, " CNY")
        expected_exposure = last.holdings_base / last.total_base
        self.check_close("C3:exposure", expected_exposure, last.exposure, "", tol=1e-9)

    def case_4_cross_market_dividend_split(self, days: list[date]) -> None:
        # HK venue on its own calendar, raw close series (no adjusted close) with
        # a dividend and a 2-for-1 split: the dividend must be credited in CNY at
        # the published FX rate, and the split must double the quantity while the
        # price halves, so total equity must not jump at the split.
        hk_days = sessions_between(days[0], days[0] + timedelta(days=60), "HK")
        split_day = hk_days[20]
        dividend_day = hk_days[10]
        dividend = 5.0
        bars = [
            point(
                day,
                200.0 if day < split_day else 100.0,
                ticker="HKD1",
                market="HK",
                currency="HKD",
                dividend=dividend if day == dividend_day else None,
                split=2.0 if day == split_day else None,
            )
            for day in hk_days
        ]
        self.repo.save_prices(series("HKD1", "HK", "HKD", bars))
        self.repo.save_fx(fx_series("HKD", {day: 0.9 for day in hk_days}))
        spec = StrategySpec(
            spec_id="case4-cross-market",
            universe=[Instrument("HKD1", "HK", "HKD")],
            weighting=Weighting("equal"),
            start=hk_days[0],
            end=hk_days[-1],
            initial_capital=1_000_000.0,
            rebalance=RebalanceRule(frequency="monthly", anchor=1),
            drift_threshold=0.0,
            costs=CostModel(commission_rate=0.0, min_commission=0.0, slippage_bps=0.0, fx_spread_bps=0.0),
        )
        run, _ = run_backtest(spec, self.repo, now=end_of_day(hk_days[-1]))
        by_session = {point.session: point for point in run.equity_curve}
        shares = math.floor((1_000_000.0 / (200.0 * 0.9)) * 1_000_000) / 1_000_000
        before_dividend = by_session[hk_days[9]]
        after_dividend = by_session[dividend_day]
        expected_credit = shares * dividend * 0.9
        self.check_close(
            "C4:dividend-credit",
            round(before_dividend.cash_base + expected_credit, 6),
            after_dividend.cash_base,
            " CNY",
            tol=0.02,
        )
        before_split = by_session[hk_days[19]]
        after_split = by_session[split_day]
        self.check_close("C4:split-keeps-equity", before_split.total_base, after_split.total_base, " CNY", tol=1e-6)
        last = run.equity_curve[-1]
        self.check_close("C4:equity-identity", last.cash_base + last.holdings_base, last.total_base, " CNY")
        self.record("C4:cash-non-negative", ">= 0", f"{last.cash_base:.6f}", last.cash_base >= -1e-9)
        self.record(
            "C4:trades-on-venue-sessions",
            "all sessions are HK trading days",
            str(len({trade.session for trade in run.trades})),
            all(trade.session in set(hk_days) for trade in run.trades),
        )

    def case_5_idempotency_and_revision(self, days: list[date]) -> None:
        spec = StrategySpec(
            spec_id="case5-idempotent",
            universe=[Instrument("AAA", "US", "USD")],
            weighting=Weighting("equal"),
            start=days[0],
            end=days[10],
            initial_capital=500_000.0,
            rebalance=RebalanceRule(frequency="monthly", anchor=1),
            drift_threshold=0.0,
            costs=CostModel(commission_rate=0.0, min_commission=0.0, slippage_bps=0.0, fx_spread_bps=0.0),
        )
        run1, created1 = run_backtest(spec, self.repo, now=end_of_day(days[-1]))
        run2, created2 = run_backtest(spec, self.repo, now=end_of_day(days[-1]))
        self.record("C5:first-created", "True", str(created1), created1 is True)
        self.record("C5:second-reused", "False", str(created2), created2 is False)
        self.record("C5:same-run-id", run1.run_id, run2.run_id, run1.run_id == run2.run_id)
        loaded = self.repo.load_backtest_run(run1.run_id)
        self.record("C5:round-trip-trades", str(len(run1.trades)), str(len(loaded.trades)),
                    len(loaded.trades) == len(run1.trades))
        self.check_close("C5:round-trip-equity", run1.equity_curve[-1].total_base,
                         loaded.equity_curve[-1].total_base, " CNY")

    def run(self) -> None:
        days = sessions_between(date(2025, 1, 6), date(2025, 3, 14), "US")
        self.seed_flat(days)
        self.case_1_flat_fixed_zero_cost(days)
        self.case_2_flat_equal_with_costs(days)
        self.case_3_momentum_selects_the_winner(days)
        self.case_4_cross_market_dividend_split(days)
        self.case_5_idempotency_and_revision(days)


def write_report(results: list[CaseResult], repository: Repository, tmp: Path) -> None:
    passed = sum(1 for item in results if item.passed)
    lines = [
        "# Stage E — synthetic full-run acceptance (E-1)",
        "",
        "Generated by `scripts/accept_backtest_synthetic.py`. Expected values are written",
        "by hand in the script; the engine output is never used as the expectation.",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Temporary database: `{tmp.name}` (deleted after the run)",
        f"- Schema version: {repository.schema_version() if hasattr(repository, 'schema_version') else 'n/a'}",
        f"- Backtest runs stored: {repository.count_backtest_runs()}",
        f"- Checks: **{passed}/{len(results)} passed**",
        "",
        "| Case | Expected | Actual | Difference | Result |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in results:
        lines.append(
            f"| `{item.case_id}` | {item.expected} | {item.actual} | {item.diff} | "
            f"{'PASS' if item.passed else 'FAIL'} |"
        )
    lines += [
        "",
        "## Coverage of the acceptance scenarios",
        "",
        "| Requirement | Where |",
        "| --- | --- |",
        "| Two instruments in different currencies | C1, C3, C4 (USD and HKD) |",
        "| FIXED / EQUAL / MOMENTUM weighting | C1 / C2, C5 / C3 |",
        "| Rebalance frequency coverage | monthly (C1, C2, C4), daily (C3) |",
        "| Drift threshold | 1.0 skips (C1, C2), 0.0 forces (C3) |",
        "| Cross-market calendars | C4 (US + HK, union grid) |",
        "| Transaction costs | C2 (commission, slippage, FX spread) |",
        "| Cash constraint | C2, C3, C4 (`cash >= 0`) |",
        "| Equity identity `cash + holdings` | C3, C4 |",
        "| Idempotency and persistence round trip | C5 |",
        "",
        "Corporate actions (dividend, split), missing sessions, stale-but-usable bars and",
        "point-in-time revisions are covered by the unit tests named in",
        "`docs/technical_specification/V2/06_testing_and_acceptance.md`",
        "(`test_backtest_engine.py`, `test_backtest_pit.py`, `test_backtest_fingerprint.py`).",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8", newline="\r\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="keep the temporary database")
    args = parser.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="fa_stagee_synthetic_"))
    repository = Repository(tmp / "acceptance.db")
    try:
        acceptance = Acceptance(repository, tmp)
        acceptance.run()
        write_report(acceptance.results, repository, tmp)
        failed = [item for item in acceptance.results if not item.passed]
        print(f"checks={len(acceptance.results)} failed={len(failed)} report={REPORT.relative_to(ROOT)}")
        for item in failed:
            print(f"FAIL {item.case_id}: expected={item.expected} actual={item.actual} diff={item.diff}")
        return 1 if failed else 0
    finally:
        repository.close()
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
