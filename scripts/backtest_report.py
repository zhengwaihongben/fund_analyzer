"""Stage E, batch E-3: minimal backtest CLI over the persisted results.

Subcommands::

    python scripts/backtest_report.py run --spec spec.json [--force]
    python scripts/backtest_report.py list [--limit 20]
    python scripts/backtest_report.py show <run_id> [--out PATH]

Everything shown here is read back from SQLite; the report is traceable to the
stored ``run_id`` and nothing is recomputed from raw prices.  The benchmark
curve comes from the run's own ``EquityPoint.benchmark_base``, which was built
through the same PIT ``DataAccess`` rules as the portfolio.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fa.backtesting import (  # noqa: E402
    CostModel,
    Instrument,
    RebalanceFrequency,
    RebalanceRule,
    StrategySpec,
    Weighting,
    run_backtest,
)
from fa.data.database.repository import Repository  # noqa: E402
from fa.reporting.backtest_report import render  # noqa: E402

DEFAULT_DB = ROOT / "data" / "database" / "fund_analyzer.db"
REPORT_DIR = ROOT / "output" / "reports"


def _date(payload: str) -> date:
    return date.fromisoformat(str(payload))


def spec_from_json(payload: dict) -> StrategySpec:
    """Rebuild a ``StrategySpec`` from the JSON a user submits."""
    costs = payload.get("costs") or {}
    rebalance = payload.get("rebalance") or {}
    universe = [
        Instrument(str(item["ticker"]), str(item["market"]), str(item["currency"]))
        for item in payload["universe"]
    ]
    return StrategySpec(
        spec_id=str(payload["spec_id"]),
        universe=universe,
        weighting=Weighting(str(payload.get("weighting", "equal"))),
        start=_date(payload["start"]),
        end=_date(payload["end"]),
        initial_capital=float(payload["initial_capital"]),
        rebalance=RebalanceRule(
            frequency=RebalanceFrequency(str(rebalance.get("frequency", "monthly"))),
            anchor=rebalance.get("anchor"),
        ),
        target_weights={str(k): float(v) for k, v in (payload.get("target_weights") or {}).items()},
        momentum_window=payload.get("momentum_window"),
        drift_threshold=float(payload.get("drift_threshold", 0.02)),
        benchmark=payload.get("benchmark"),
        costs=CostModel(
            commission_rate=float(costs.get("commission_rate", CostModel().commission_rate)),
            min_commission=float(costs.get("min_commission", CostModel().min_commission)),
            slippage_bps=float(costs.get("slippage_bps", CostModel().slippage_bps)),
            fx_spread_bps=float(costs.get("fx_spread_bps", CostModel().fx_spread_bps)),
        ),
    )


def _opens_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def list_runs(path: Path, limit: int) -> list[sqlite3.Row]:
    connection = _opens_read_only(path)
    try:
        return connection.execute(
            """
            SELECT r.run_id, r.spec_id, r.start_date, r.end_date, r.created_at,
                   m.sessions, m.trades, m.cagr, m.max_drawdown
            FROM strategy_backtest_runs AS r
            LEFT JOIN strategy_backtest_metrics AS m ON m.run_id = r.run_id
            ORDER BY r.created_at DESC, r.run_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        connection.close()


def write_report(text: str, run_id: str, out: Path | None) -> Path:
    target = out or (REPORT_DIR / f"{run_id}.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\r\n")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="run a specification and store the result")
    run_parser.add_argument("--spec", type=Path, required=True)
    run_parser.add_argument("--force", action="store_true")
    list_parser = sub.add_parser("list", help="list stored runs")
    list_parser.add_argument("--limit", type=int, default=20)
    show_parser = sub.add_parser("show", help="render a stored run as Markdown")
    show_parser.add_argument("run_id")
    show_parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.command == "list":
        rows = list_runs(args.db, args.limit)
        if not rows:
            print("no stored backtest runs")
            return 0
        print(f"{'run_id':<64} {'spec_id':<32} {'period':<25} {'trades':>6} {'cagr':>10}")
        for row in rows:
            cagr = "—" if row["cagr"] is None else f"{row['cagr']:.4%}"
            print(
                f"{row['run_id']:<64} {row['spec_id']:<32} "
                f"{row['start_date']}…{row['end_date']} {row['trades'] or 0:>6} {cagr:>10}"
            )
        return 0

    repository = Repository(args.db)
    try:
        if args.command == "run":
            payload = json.loads(args.spec.read_text(encoding="utf-8"))
            spec = spec_from_json(payload)
            run, created = run_backtest(spec, repository, force=args.force)
            print(f"run_id={run.run_id} created={created} trades={len(run.trades)} "
                  f"sessions={len(run.equity_curve)}")
            stored = repository.load_backtest_run(run.run_id)
            if stored is None:
                print(f"run was stored but could not be reloaded: {run.run_id}")
                return 1
            path = write_report(render(stored), stored.run_id, None)
            print(f"report={path.relative_to(ROOT)}")
            return 0
        run = repository.load_backtest_run(args.run_id)
        if run is None:
            print(f"run not found: {args.run_id}")
            return 1
        path = write_report(render(run), run.run_id, args.out)
        print(f"report={path}")
        return 0
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
