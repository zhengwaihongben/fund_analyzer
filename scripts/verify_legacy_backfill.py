# -*- coding: utf-8 -*-
"""Verify the Q1 rule on the frozen V2.2.3 baseline: every legacy row must be
re-downloaded before it can be PIT-visible.

The baseline copy is upgraded in place (a temporary file, never the original) and
then ``update_prices()`` runs against the live provider.  Rows are tracked by
``(ticker, price_date, price_type)`` so a re-download that writes a second key is
reported as a duplicate instead of silently looking like an update.

Usage:
    python scripts/verify_legacy_backfill.py            # print the table
    python scripts/verify_legacy_backfill.py --write    # also write the report
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fa.data.database.repository import Repository  # noqa: E402
from fa.data.price_pipeline import PriceRequest, update_prices  # noqa: E402
from fa.models.market_data import iso_utc  # noqa: E402
from fa.utils.logging_setup import setup_logging  # noqa: E402

BASELINE = ROOT / "docs" / "development" / "v2.2.3_baseline" / "database_backup.db"
REPORT = ROOT / "docs" / "development" / "stageA_legacy_backfill.md"
PIT_SOURCES = {"observed", "vendor_timestamp", "inferred"}


def _rows(repository: Repository) -> dict[tuple[str, str, str], dict]:
    rows = repository._conn.execute(
        "SELECT ticker, price_date, price_type, close, adj_close, fetched_at, "
        "available_at, availability_source FROM market_prices"
    ).fetchall()
    return {
        (row["ticker"], row["price_date"], row["price_type"]): dict(row) for row in rows
    }


def run(write_report: bool) -> int:
    setup_logging()
    if not BASELINE.exists():
        raise SystemExit(f"baseline missing: {BASELINE}")
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "baseline_copy.db"
        shutil.copy2(BASELINE, copy)
        repository = Repository(copy)
        try:
            legacy = _rows(repository)
            by_ticker: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
            for key in legacy:
                by_ticker[key[0]].append(key)
            now = datetime.now(timezone.utc)
            stamp = iso_utc(now)
            table: list[tuple[str, int, int, int, int, int, int]] = []
            for ticker, keys in sorted(by_ticker.items()):
                days = [date.fromisoformat(key[1]) for key in keys]
                request = PriceRequest([ticker], min(days), max(days))
                outcome = update_prices(request, repository, now=now)
                after = _rows(repository)
                returned = available = still_unknown = 0
                for key in keys:
                    row = after.get(key)
                    if row is None:
                        continue
                    if str(row["fetched_at"]) == stamp:
                        returned += 1
                    if row["available_at"] is not None and row["availability_source"] in PIT_SOURCES:
                        available += 1
                    if row["available_at"] is None or row["availability_source"] == "unknown":
                        still_unknown += 1
                per_day: dict[str, int] = defaultdict(int)
                for key in after:
                    if key[0] == ticker:
                        per_day[key[1]] += 1
                duplicates = sum(1 for count in per_day.values() if count > 1)
                pit_rows = len(repository.prices_as_of(ticker, now).points)
                pit_later = len(
                    repository.prices_as_of(ticker, datetime.now(timezone.utc)).points
                )
                table.append(
                    (
                        ticker,
                        len(keys),
                        outcome.downloads,
                        returned,
                        available,
                        outcome.revisions,
                        pit_rows,
                        pit_later,
                        still_unknown,
                        duplicates,
                    )
                )
            pit_now = sum(item[6] for item in table)
            pit_after = sum(item[7] for item in table)
            lines = [
                "| Ticker | Legacy keys | Provider calls | Returned by vendor | Row has availability | Revisions | PIT at run instant | PIT after run | Still unknown | Dates with two keys |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
            for item in table:
                lines.append(
                    f"| {item[0]} | {item[1]} | {item[2]} | {item[3]} | {item[4]} | "
                    f"{item[5]} | {item[6]} | {item[7]} | {item[8]} | {item[9]} |"
                )
            totals = [sum(item[index] for item in table) for index in range(1, 10)]
            lines.append(
                f"| **Total** | **{totals[0]}** | **{totals[1]}** | **{totals[2]}** | "
                f"**{totals[3]}** | **{totals[4]}** | **{totals[5]}** | **{totals[6]}** | "
                f"**{totals[7]}** | **{totals[8]}** |"
            )
            body = "\n".join(lines)
            print(body)
            print(f"prices_as_of at run instant: {pit_now}; immediately afterwards: {pit_after}")
            if write_report:
                REPORT.parent.mkdir(parents=True, exist_ok=True)
                text = (
                    "# Stage A — legacy price backfill evidence\r\n\r\n"
                    "The frozen baseline (`docs/development/v2.2.3_baseline/"
                    "database_backup.db`) holds the 93 V2.2 rows whose "
                    "`availability_source` is `unknown` and `available_at` is NULL, "
                    "all under `price_type='adjusted_close'`. A copy is upgraded to "
                    "schema 3 and `update_prices()` runs against the live Yahoo "
                    "provider, so a row can only become PIT-visible if the vendor "
                    "returned that session in this run (Q1).\r\n\r\n"
                    f"Run instant: `{now.isoformat()}`\r\n\r\n"
                    f"{body}\r\n\r\n"
                    f"`prices_as_of(run instant)` returned {pit_now} rows across the "
                    f"10 tickers, and {pit_after} rows immediately afterwards.\r\n\r\n"
                    "Reading the columns: *Returned by vendor* counts legacy keys "
                    "whose stored row was rewritten by this run (`fetched_at` equals "
                    "the run instant). *Revisions* counts numbers the vendor changed; "
                    "a revised row's `available_at` is pushed to the revision instant, "
                    "which is a fraction of a second later than the run's `now`, so the "
                    "revision is correctly invisible at `now` and visible in the next "
                    "query — that is why the two PIT columns differ, and the difference "
                    "must equal the revision count. *Still "
                    "unknown* counts legacy keys the vendor did not return; they keep "
                    "their old `fetched_at` and stay PIT-invisible. *Dates with two "
                    "keys* must stay at zero — a re-download that invents a second "
                    "`price_type` for an existing session would split the history in "
                    "two.\r\n"
                )
                REPORT.write_bytes(text.encode("utf-8"))
                print(f"report written: {REPORT}")
        finally:
            repository.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the Q1 legacy backfill rule.")
    parser.add_argument("--write", action="store_true", help="write the markdown report")
    args = parser.parse_args()
    return run(args.write)


if __name__ == "__main__":
    raise SystemExit(main())
