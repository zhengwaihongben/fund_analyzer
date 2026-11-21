# -*- coding: utf-8 -*-
"""Live smoke test for the Stage 2.3 FX provider.

Separate from the unit tests on purpose: this one needs the network.  It
downloads every supported pair for a two-year window and reports, per currency,
the bar count, the London trading date of the last bar, the rate, whether the
direction looks right, and when the last bar would become usable (D-1).

Usage::

    python scripts/smoke_fx_provider.py            # two years, all pairs
    python scripts/smoke_fx_provider.py --years 1  # shorter window
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fa.data.fx_provider import (  # noqa: E402
    FX_QUOTE_CONVENTIONS,
    FX_SANITY_BANDS,
    fetch_fx_bars_many,
    infer_availability,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="FX provider live smoke test")
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=int(args.years * 365.25))
    print(f"window {start} .. {end}  ({args.years} years)")

    fetches = fetch_fx_bars_many(sorted(FX_QUOTE_CONVENTIONS), start, end,
                                 timeout=args.timeout)
    failures = 0
    for code in sorted(FX_QUOTE_CONVENTIONS):
        fetch = fetches.get(code)
        symbol = FX_QUOTE_CONVENTIONS[code]
        if fetch is None or not fetch.bars:
            failures += 1
            print(f"{code:<4} {symbol:<10} FAIL  {fetch.error if fetch else 'no-result'}")
            continue
        last = fetch.bars[-1]
        low, high = FX_SANITY_BANDS[code]
        direction = "ok" if low <= last.rate <= high else "SUSPECT"
        usable = infer_availability(last.rate_date, 30)
        if fetch.error:
            direction = fetch.error
            failures += 1
        span = (fetch.bars[-1].rate_date - fetch.bars[0].rate_date).days
        print(f"{code:<4} {symbol:<10} OK    bars={len(fetch.bars):<4} "
              f"span={span}d  last={last.rate_date} {last.rate:.6f}  "
              f"dir={direction}  quote={fetch.currency_raw}  "
              f"usable_at={usable.isoformat()}")
        if fetch.warnings:
            print(f"      warnings: {', '.join(fetch.warnings)}")

    print(f"pairs={len(FX_QUOTE_CONVENTIONS)} failures={failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
