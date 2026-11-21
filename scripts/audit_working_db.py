"""Stage E, batch E-2a: read-only audit of the working database.

Prints (and optionally writes) the state the real-data smoke test depends on:
schema version, row counts, per-ticker PIT coverage, availability provenance,
legacy rows without publication metadata, revision ledger size and FX coverage.

Run with::

    python scripts/audit_working_db.py [--db PATH] [--report PATH]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "database" / "fund_analyzer.db"
DEFAULT_REPORT = ROOT / "docs" / "development" / "stageE_working_db_audit.md"

TABLES = (
    "market_prices",
    "price_revisions",
    "fx_rates",
    "fx_revisions",
    "official_nav",
    "holdings_snapshots",
    "estimation_runs",
    "strategy_backtest_runs",
    "strategy_backtest_metrics",
    "strategy_backtest_trades",
    "strategy_backtest_equity",
)


def connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"database not found: {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def count(connection: sqlite3.Connection, table: str) -> int:
    if not table_exists(connection, table):
        return -1
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def schema_version(connection: sqlite3.Connection) -> int | None:
    if not table_exists(connection, "schema_version"):
        return None
    row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row and row[0] is not None else None


def tickers(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT ticker,
               COUNT(*) AS rows,
               SUM(CASE WHEN available_at IS NOT NULL THEN 1 ELSE 0 END) AS pit_rows,
               MIN(price_date) AS first_day,
               MAX(price_date) AS last_day,
               COUNT(DISTINCT price_type) AS price_types
        FROM market_prices
        GROUP BY ticker
        ORDER BY ticker
        """
    ).fetchall()
    result = []
    for row in rows:
        sources = connection.execute(
            "SELECT availability_source, COUNT(*) FROM market_prices WHERE ticker=? GROUP BY 1",
            (row["ticker"],),
        ).fetchall()
        types = connection.execute(
            "SELECT price_type, COUNT(*) FROM market_prices WHERE ticker=? GROUP BY 1",
            (row["ticker"],),
        ).fetchall()
        result.append(
            {
                "ticker": row["ticker"],
                "rows": row["rows"],
                "pit_rows": row["pit_rows"],
                "first_day": row["first_day"],
                "last_day": row["last_day"],
                "sources": {name: total for name, total in sources},
                "types": {name: total for name, total in types},
            }
        )
    return result


def fx_summary(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT currency,
               COUNT(*) AS rows,
               SUM(CASE WHEN available_at IS NOT NULL THEN 1 ELSE 0 END) AS pit_rows,
               MIN(rate_date) AS first_day,
               MAX(rate_date) AS last_day
        FROM fx_rates
        GROUP BY currency
        ORDER BY currency
        """
    ).fetchall()
    return [dict(row) for row in rows]


def legacy_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT ticker, COUNT(*) AS legacy
        FROM market_prices
        WHERE available_at IS NULL OR availability_source = 'unknown'
        GROUP BY ticker
        ORDER BY ticker
        """
    ).fetchall()


def foreign_keys(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    if not table_exists(connection, "strategy_backtest_equity"):
        return []
    pairs = []
    for table in (
        "strategy_backtest_metrics",
        "strategy_backtest_trades",
        "strategy_backtest_equity",
    ):
        for row in connection.execute(f"PRAGMA foreign_key_list({table})"):
            pairs.append((table, f"{row['from']} → {row['table']}.{row['to']} ({row['on_delete']})"))
    return pairs


def build(connection: sqlite3.Connection, path: Path) -> list[str]:
    version = schema_version(connection)
    lines = [
        "# Stage E — working database audit (E-2a)",
        "",
        f"- Database: `{path}`",
        f"- Opened read-only (`mode=ro`)",
        f"- Schema version: {version}",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "## Row counts",
        "",
        "| Table | Rows |",
        "| --- | --- |",
    ]
    for table in TABLES:
        lines.append(f"| `{table}` | {count(connection, table)} |")

    lines += ["", "## PIT price coverage per ticker", "",
              "| Ticker | Rows | PIT rows | First | Last | Availability sources | Price types |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for item in tickers(connection):
        sources = ", ".join(f"{name}:{total}" for name, total in sorted(item["sources"].items()))
        types = ", ".join(f"{name}:{total}" for name, total in sorted(item["types"].items()))
        lines.append(
            f"| `{item['ticker']}` | {item['rows']} | {item['pit_rows']} | "
            f"{item['first_day']} | {item['last_day']} | {sources} | {types} |"
        )

    lines += ["", "## FX coverage", "", "| Currency | Rows | PIT rows | First | Last |",
              "| --- | --- | --- | --- | --- |"]
    for item in fx_summary(connection):
        lines.append(
            f"| `{item['currency']}` | {item['rows']} | {item['pit_rows']} | "
            f"{item['first_day']} | {item['last_day']} |"
        )

    legacy = legacy_rows(connection)
    lines += ["", "## Legacy rows without publication metadata", ""]
    if legacy:
        total = sum(row["legacy"] for row in legacy)
        lines.append(
            f"{total} rows are `available_at IS NULL` or `availability_source='unknown'`; "
            "they stay readable through `load_prices()` but are invisible to `prices_as_of()` "
            "and therefore never used by a backtest (decision Q1)."
        )
        lines += ["", "| Ticker | Legacy rows |", "| --- | --- |"]
        for row in legacy:
            lines.append(f"| `{row['ticker']}` | {row['legacy']} |")
    else:
        lines.append("None.")

    lines += ["", "## Backtest persistence relationships", ""]
    relations = foreign_keys(connection)
    if relations:
        for table, pair in relations:
            lines.append(f"- `{table}`: {pair}")
    else:
        lines.append("- No foreign keys declared (rows are written and read per `run_id`).")

    lines += [
        "",
        "## Notes",
        "",
        "- `prices_as_of()` is the only read path the backtesting engine uses;",
        "  `load_prices()` is the V2.2 non-PIT read and is never called by `fa/backtesting/`.",
        "- Legacy rows are retained for audit; they are not deleted and not retro-stamped.",
        "- The frozen baseline copy `docs/development/v2.2.3_baseline/database_backup.db`",
        "  remains the migration-test fixture and is not modified by this audit.",
        "",
    ]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    connection = connect(args.db)
    try:
        lines = build(connection, args.db)
    finally:
        connection.close()
    text = "\n".join(lines)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8", newline="\r\n")
        print(f"written: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
