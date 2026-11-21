# -*- coding: utf-8 -*-
"""Live smoke test for the V2.3.2 Stage A price pipeline.

Uses a throwaway SQLite database and the real Yahoo endpoint; it never opens
``data/database/fund_analyzer.db``.  Run it when you want evidence that the
pipeline works against live data, separately from the offline unit tests::

    python scripts/smoke_price_pipeline.py
    python scripts/smoke_price_pipeline.py --tickers LLY,ABBVIE --days 400
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fa.data.database.repository import Repository  # noqa: E402
from fa.data.price_pipeline import PriceRequest, update_prices  # noqa: E402

DEFAULT_TICKERS = ["LLY", "JNJ", "ABBV", "MRK", "UNH", "ROP SW", "NOVN", "AZN", "TMO", "AMGN"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Price pipeline live smoke test")
    parser.add_argument(
        "--tickers",
        default=",".join(DEFAULT_TICKERS),
        help="逗號分隔的 ticker 清單",
    )
    parser.add_argument("--days", type=int, default=730, help="回溯天數（預設兩年）")
    parser.add_argument("--keep", action="store_true", help="保留臨時資料庫以便檢查")
    args = parser.parse_args(argv)

    tickers = [item.strip() for item in args.tickers.split(",") if item.strip()]
    today = date.today()
    request = PriceRequest(tickers, today - timedelta(days=args.days), today)

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "smoke_prices.db"
        repository = Repository(path)
        try:
            now = datetime.now(timezone.utc)
            first = update_prices(request, repository, now=now)
            print(
                f"first downloads={first.downloads} inserted={first.inserted} "
                f"updated={first.updated} revisions={first.revisions} "
                f"skipped={first.skipped}"
            )
            forced = PriceRequest(tickers, request.start, request.end, prefer_cache=False)
            refresh = update_prices(forced, repository, now=now)
            print(
                f"refresh downloads={refresh.downloads} inserted={refresh.inserted} "
                f"updated={refresh.updated} revisions={refresh.revisions} "
                f"skipped={refresh.skipped}"
            )
            cached = update_prices(request, repository, now=now)
            print(
                f"cached downloads={cached.downloads} series={sorted(cached.series)} "
                f"skipped={cached.skipped}"
            )

            moment = now
            failures = 0
            for ticker in tickers:
                series = repository.load_prices(ticker, request.start, request.end)
                if not series.points:
                    failures += 1
                    print(f"{ticker:8s} no rows")
                    continue
                pit = repository.prices_as_of(ticker, moment, request.start, request.end)
                with_availability = sum(1 for p in series.points if p.available_at is not None)
                revisions = repository.count_price_revisions(ticker, request.start, request.end)
                first_day = series.points[0].price_date.isoformat()
                last_day = series.points[-1].price_date.isoformat()
                print(
                    f"{ticker:8s} rows={len(series.points)} avail={with_availability} "
                    f"pit={len(pit.points)} revisions={revisions} "
                    f"first={first_day} last={last_day} market={series.market or '-'}"
                )
            print(f"tickers={len(tickers)} failures={failures}")
        finally:
            repository.close()
        if args.keep:
            print(f"db kept at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
