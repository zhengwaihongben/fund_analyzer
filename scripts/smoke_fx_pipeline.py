# -*- coding: utf-8 -*-
"""Live smoke for Stage 2.5: Yahoo -> pipeline -> SQLite -> PIT read.

Runs against a temporary database, so the project's own ``fund_analyzer.db`` is
never touched.  Requires network access to Yahoo Finance; the offline unit tests
never call it.

Usage::

    python scripts/smoke_fx_pipeline.py
    python scripts/smoke_fx_pipeline.py --currencies USD HKD --days 120
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fa.data.database.repository import Repository            # noqa: E402
from fa.data.fx_pipeline import FxRequest, update_fx           # noqa: E402
from fa.data.fx_provider import london_today                   # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="FX pipeline live smoke")
    parser.add_argument("--currencies", nargs="+", default=["USD", "HKD", "CHF", "GBP"])
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()

    # Yahoo FX bars are London trading days; ``date.today()`` is the local date
    # and would ask for a bar that does not exist yet during the London evening.
    today = london_today()
    start = today - timedelta(days=args.days)
    now = datetime.now(timezone.utc)
    currencies = [code.upper() for code in args.currencies]

    with tempfile.TemporaryDirectory(prefix="fxsmoke_") as folder:
        repo = Repository(Path(folder) / "smoke.db")
        try:
            first = update_fx(FxRequest(currencies, start, today), repo, now=now)
            print(f"first   downloads={first.downloads} inserted={first.inserted} "
                  f"updated={first.updated} revisions={first.revisions} "
                  f"skipped={first.skipped}")
            for warning in first.warnings[:6]:
                print(f"        warning: {warning}")

            second = update_fx(FxRequest(currencies, start, today, prefer_cache=False),
                               repo, now=now)
            print(f"refresh downloads={second.downloads} inserted={second.inserted} "
                  f"updated={second.updated} revisions={second.revisions}")

            cached = update_fx(FxRequest(currencies, start, today), repo, now=now)
            print(f"cached  downloads={cached.downloads} series={sorted(cached.series)}")

            failures = 0
            for code in currencies:
                series = repo.load_fx(code)
                pit_now = repo.fx_as_of(code, now, start=start, end=today)
                pit_past = repo.fx_as_of(code, now - timedelta(days=1),
                                         start=start, end=today)
                avail = sum(1 for point in series.points if point.available_at)
                print(f"  {code}: rows={len(series.points)} avail={avail} "
                      f"pit_now={len(pit_now.points)} pit_prev_day={len(pit_past.points)} "
                      f"revisions={repo.count_fx_revisions(code)} "
                      f"first={series.points[0].price_date if series.points else '-'} "
                      f"last={series.points[-1].price_date if series.points else '-'}")
                if not series.points:
                    failures += 1
                elif len(pit_now.points) != len(series.points) - 1:
                    # only today's still-provisional bar may be hidden
                    print(f"        unexpected PIT gap for {code}")
                    failures += 1
            print(f"currencies={len(currencies)} failures={failures}")
            return 0 if failures == 0 else 1
        finally:
            repo.close()


if __name__ == "__main__":
    raise SystemExit(main())
