# -*- coding: utf-8 -*-
"""Read-only audit of the data a strategy backtest would depend on (V2.3.0).

The script **never writes to the database** (``mode=ro`` URI) and never touches
the network.  It answers the questions the V2.3.0 architecture review needs:

* how much price history is actually stored, per market and per ticker;
* whether prices and FX cover the same dates;
* whether the stored series are clean (duplicates, non-positive values);
* whether the columns a backtest needs (execution price, availability time) are
  populated at all.

Usage::

    python scripts/audit_backtest_data.py
    python scripts/audit_backtest_data.py --db data/database/fund_analyzer.db \\
        --out data/exports/backtests/data_audit_report.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fa.config import PROJECT_ROOT as _ROOT  # noqa: E402  (path setup first)
from fa.utils.calendars import (  # noqa: E402
    FallbackCalendar,
    available_markets,
    calendar_for,
    normalise_market,
)

DEFAULT_DB = _ROOT / "data" / "database" / "fund_analyzer.db"
DEFAULT_OUT = _ROOT / "data" / "exports" / "backtests" / "data_audit_report.json"

TABLES = (
    "funds", "assets", "holdings_snapshots", "holdings", "market_prices",
    "fx_rates", "official_nav", "estimation_runs", "estimation_inputs",
    "estimation_results", "backtest_results",
)


def _connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"database not found: {path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in TABLES:
        try:
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.DatabaseError:
            counts[table] = -1  # table missing in this schema version
    return counts


def _schema_version(conn: sqlite3.Connection) -> int | None:
    try:
        return int(conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0])
    except sqlite3.DatabaseError:
        return None


def _sessions_between(calendar, start: date, end: date) -> int:
    if end < start:
        return 0
    return int(calendar.trading_days_between(start, end)) + 1


def _audit_prices(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT ticker, price_date, close, adj_close, price_type, price_timestamp,"
        "       source, market, currency FROM market_prices ORDER BY ticker, price_date"
    ).fetchall()
    per_ticker: dict[str, dict] = {}
    price_type_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    timestamp_missing = 0
    market_rows: dict[str, int] = {}
    non_positive: list[str] = []
    duplicate_keys: list[str] = []

    for row in rows:
        ticker = row["ticker"]
        market = normalise_market(row["market"]) or "?"
        info = per_ticker.setdefault(ticker, {
            "market": market,
            "currency": row["currency"] or "",
            "rows": 0,
            "first_date": row["price_date"],
            "last_date": row["price_date"],
            "adj_close_rows": 0,
            "sources": set(),
            "price_types": set(),
        })
        info["rows"] += 1
        info["first_date"] = min(info["first_date"], row["price_date"])
        info["last_date"] = max(info["last_date"], row["price_date"])
        if row["adj_close"] is not None:
            info["adj_close_rows"] += 1
        info["sources"].add(row["source"] or "")
        info["price_types"].add(row["price_type"] or "")
        price_type_counts[row["price_type"] or ""] = price_type_counts.get(row["price_type"] or "", 0) + 1
        source_counts[row["source"] or ""] = source_counts.get(row["source"] or "", 0) + 1
        market_rows[market] = market_rows.get(market, 0) + 1
        if row["price_timestamp"] is None:
            timestamp_missing += 1
        for field in ("close", "adj_close"):
            value = row[field]
            if value is not None and float(value) <= 0:
                non_positive.append(f"{ticker}@{row['price_date']}:{field}={value}")

    # duplicates are only possible across price_type, since the PK covers it
    for ticker, info in per_ticker.items():
        dupes = conn.execute(
            "SELECT price_date, COUNT(*) AS n FROM market_prices WHERE ticker=?"
            " GROUP BY price_date HAVING n>1", (ticker,)
        ).fetchall()
        for row in dupes:
            duplicate_keys.append(f"{ticker}@{row['price_date']} x{row['n']}")
        calendar = calendar_for(info["market"], ticker)
        info["calendar"] = getattr(calendar, "market", "?")
        info["calendar_fallback"] = isinstance(calendar, FallbackCalendar)
        start, end = date.fromisoformat(info["first_date"]), date.fromisoformat(info["last_date"])
        expected = _sessions_between(calendar, start, end)
        info["expected_sessions"] = expected
        info["session_coverage"] = round(info["rows"] / expected, 4) if expected else None
        info["sources"] = sorted(info["sources"])
        info["price_types"] = sorted(info["price_types"])

    return {
        "rows": len(rows),
        "tickers": len(per_ticker),
        "by_market": market_rows,
        "by_price_type": price_type_counts,
        "by_source": source_counts,
        "price_timestamp_missing": timestamp_missing,
        "price_timestamp_coverage": round(1 - timestamp_missing / len(rows), 4) if rows else None,
        "non_positive_values": non_positive[:20],
        "duplicate_ticker_dates": duplicate_keys[:20],
        "per_ticker": per_ticker,
    }


def _audit_fx(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT currency, rate_date, rate, source FROM fx_rates ORDER BY currency, rate_date"
    ).fetchall()
    cn = calendar_for("CN")
    per_currency: dict[str, dict] = {}
    bad_rates: list[str] = []
    for row in rows:
        info = per_currency.setdefault(row["currency"], {
            "rows": 0, "first_date": row["rate_date"], "last_date": row["rate_date"],
            "sources": set(),
        })
        info["rows"] += 1
        info["first_date"] = min(info["first_date"], row["rate_date"])
        info["last_date"] = max(info["last_date"], row["rate_date"])
        info["sources"].add(row["source"] or "")
        if float(row["rate"]) <= 0:
            bad_rates.append(f"{row['currency']}@{row['rate_date']}={row['rate']}")
    for info in per_currency.values():
        start, end = date.fromisoformat(info["first_date"]), date.fromisoformat(info["last_date"])
        expected = _sessions_between(cn, start, end)
        info["expected_cn_sessions"] = expected
        info["session_coverage"] = round(info["rows"] / expected, 4) if expected else None
        info["sources"] = sorted(info["sources"])
    return {
        "rows": len(rows),
        "currencies": len(per_currency),
        "convention": "CNY per 1 unit of foreign currency (fx_rates is CNY-based)",
        "non_positive_rates": bad_rates[:20],
        "per_currency": per_currency,
    }


def _audit_nav(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT fund_code, COUNT(*) AS n, MIN(nav_date) AS first_date,"
        "       MAX(nav_date) AS last_date FROM official_nav GROUP BY fund_code"
    ).fetchall()
    return {"funds": {row["fund_code"]: dict(row) for row in rows}}


def _audit_holdings(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT fund_code, quarter, holding_date, disclosure_date, source,"
        "       disclosure_date_estimated FROM holdings_snapshots"
        " ORDER BY fund_code, holding_date"
    ).fetchall()
    per_fund: dict[str, list] = {}
    for row in rows:
        per_fund.setdefault(row["fund_code"], []).append({
            "quarter": row["quarter"],
            "holding_date": row["holding_date"],
            "disclosure_date": row["disclosure_date"],
            "source": row["source"] or "",
            "disclosure_date_estimated": bool(row["disclosure_date_estimated"]),
        })
    return {
        "snapshots": len(rows),
        "per_fund": {code: {"quarters": items} for code, items in per_fund.items()},
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _audit_estimations(conn: sqlite3.Connection) -> dict:
    run_columns = _columns(conn, "estimation_runs")
    result_columns = _columns(conn, "estimation_results")
    look_ahead = (
        "r.look_ahead_status" if "look_ahead_status" in run_columns
        else "CASE WHEN r.look_ahead_safe=1 THEN 'safe(boolean)' ELSE 'unsafe(boolean)' END"
    )
    quality = "res.data_quality" if "data_quality" in result_columns else "NULL"
    try:
        rows = conn.execute(
            f"SELECT r.model_version AS model_version, {look_ahead} AS look_ahead,"
            f"       res.status AS status, {quality} AS data_quality,"
            f"       COUNT(*) AS n, MIN(r.target_nav_date) AS first_date,"
            f"       MAX(r.target_nav_date) AS last_date"
            f" FROM estimation_runs r JOIN estimation_results res USING (run_id)"
            f" GROUP BY r.model_version, look_ahead, res.status, data_quality"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        return {"available": False, "error": str(exc)}
    pending = conn.execute(
        "SELECT COUNT(*) FROM estimation_results WHERE status NOT IN ('estimated','partial')"
    ).fetchone()[0]
    return {
        "available": True,
        "groups": [dict(row) for row in rows],
        "unusable_runs": int(pending),
        "schema_gap": [] if "look_ahead_status" in run_columns else [
            "estimation_runs has no look_ahead_status column; only the boolean"
            " look_ahead_safe is stored, so estimated-disclosure runs cannot be"
            " separated from verified-safe runs inside the database."
        ],
    }


def _cross_checks(prices: dict, fx: dict) -> dict:
    price_dates = {
        ticker: {k for k in (info["first_date"], info["last_date"])}
        for ticker, info in prices.get("per_ticker", {}).items()
    }
    fx_currencies = set(fx.get("per_currency", {}))
    missing_currency = sorted(
        {
            info["currency"]
            for info in prices.get("per_ticker", {}).values()
            if info["currency"] and info["currency"] != "CNY"
        } - fx_currencies
    )
    return {
        "price_tickers": len(price_dates),
        "fx_currencies": sorted(fx_currencies),
        "foreign_currencies_without_fx_series": missing_currency,
    }


def build_report(db_path: Path) -> dict:
    conn = _connect(db_path)
    try:
        prices = _audit_prices(conn)
        fx = _audit_fx(conn)
        report = {
            "generated_for": "V2.3.0 architecture and data audit",
            "database": str(db_path),
            "schema_version": _schema_version(conn),
            "available_markets": available_markets(),
            "table_row_counts": _table_counts(conn),
            "market_prices": prices,
            "fx_rates": fx,
            "official_nav": _audit_nav(conn),
            "holdings": _audit_holdings(conn),
            "estimations": _audit_estimations(conn),
            "cross_checks": _cross_checks(prices, fx),
            "limitations": [
                "price_timestamp is present in the schema but no fetcher populates it,"
                " so data availability time (publication time) is not stored.",
                "A ticker+date+price_type primary key means two sources cannot coexist"
                " for the same observation; a re-download overwrites the earlier row.",
                "Calendars cover CN/US/HK/JP only; CH/GB/DE/FR/NL/CA/AU fall back to"
                " weekend-only rules and are flagged as such per ticker.",
                "FX series are CNY-based by convention; the report does not verify the"
                " vendor's direction beyond positivity.",
                "Corporate actions are not modelled: adj_close supports return analysis"
                " but does not reproduce historical share quantities or cash flows.",
            ],
        }
    finally:
        conn.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only audit of backtest data")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    report = build_report(Path(args.db))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")

    prices = report["market_prices"]
    print(f"schema_version      : {report['schema_version']}")
    print(f"market_prices       : {prices['rows']} rows / {prices['tickers']} tickers")
    print(f"price_timestamp     : {prices['price_timestamp_coverage']}")
    print(f"fx_rates            : {report['fx_rates']['rows']} rows / "
          f"{report['fx_rates']['currencies']} currencies")
    print(f"foreign ccy w/o FX  : {report['cross_checks']['foreign_currencies_without_fx_series']}")
    print(f"written             : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
