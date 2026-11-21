"""Ingest a multi-year price and FX window through the existing pipelines.

This is the documented command behind §12.1 of
``docs/technical_specification/V2/10_v232_backtest_engine.md``: a real
multi-year backtest needs more PIT price history than the working database
currently holds, and the only supported way to obtain it is Stage A's price
pipeline plus Stage 2's FX pipeline.  No new download path is introduced here.

The script talks to the vendor (Yahoo) and writes through
``update_prices()`` / ``update_fx()``, which stamp availability, keep the
revision ledger and validate quote direction.  Re-running it is safe: identical
bars only refresh fetch metadata.

Examples::

    # two years for the default universe, into the working database
    python scripts/ingest_market_data.py --years 2

    # a quick check on a throwaway database, two tickers
    python scripts/ingest_market_data.py --years 2 --tickers LLY,JNJ \
        --database _scratch/ingest.db

Exit codes: 0 when every requested ticker and currency produced at least one
bar, 1 otherwise (the per-item outcome is printed either way).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fa.data.database.repository import Repository  # noqa: E402
from fa.data.fx_pipeline import FxRequest, update_fx  # noqa: E402
from fa.data.price_pipeline import PriceRequest, update_prices  # noqa: E402

DEFAULT_TICKERS = ("LLY", "JNJ", "AZN", "NOVN", "ROP SW", "MRK", "ABBV", "UNH", "TMO", "AMGN")
DEFAULT_CURRENCIES = ("USD", "HKD", "CHF", "GBP")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--tickers",
        default=",".join(DEFAULT_TICKERS),
        help="comma-separated tickers (Eastmoney or Yahoo spelling)",
    )
    parser.add_argument(
        "--currencies",
        default=",".join(DEFAULT_CURRENCIES),
        help="comma-separated foreign currencies to store against CNY",
    )
    parser.add_argument("--years", type=int, default=2, help="calendar years to ingest")
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="last session to request (default: today)",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "data" / "database" / "fund_analyzer.db",
        help="SQLite database to write into",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.years < 1:
        print("--years must be at least 1")
        return 2
    tickers = [part.strip() for part in args.tickers.split(",") if part.strip()]
    currencies = [part.strip() for part in args.currencies.split(",") if part.strip()]
    if not tickers:
        print("no tickers requested")
        return 2
    end = args.end or date.today()
    start = end - timedelta(days=365 * args.years + args.years // 4)
    args.database.parent.mkdir(parents=True, exist_ok=True)
    repository = Repository(args.database)
    try:
        print(f"window: {start} … {end}")
        prices = update_prices(PriceRequest(tickers, start, end), repository)
        print(
            "prices: "
            f"downloads={prices.downloads} inserted={prices.inserted} "
            f"updated={prices.updated} revisions={prices.revisions}"
        )
        for ticker in tickers:
            series = prices.series.get(ticker)
            if series is None:
                print(f"  {ticker}: no bars ({prices.skipped.get(ticker, 'unknown')})")
            else:
                print(
                    f"  {ticker}: {len(series.points)} bars "
                    f"{series.points[0].price_date} … {series.points[-1].price_date}"
                )
        fx = update_fx(FxRequest(currencies, start, end), repository)
        print(
            "fx: "
            f"downloads={fx.downloads} inserted={fx.inserted} "
            f"updated={fx.updated} revisions={fx.revisions}"
        )
        for currency in currencies:
            series = fx.series.get(currency)
            if series is None:
                print(f"  {currency}: no rates ({fx.skipped.get(currency, 'unknown')})")
            else:
                print(
                    f"  {currency}: {len(series.points)} rates "
                    f"{series.points[0].price_date} … {series.points[-1].price_date}"
                )
        complete = (
            all(ticker in prices.series for ticker in tickers)
            and all(currency in fx.series for currency in currencies)
        )
        return 0 if complete else 1
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
