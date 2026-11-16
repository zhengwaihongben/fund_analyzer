# -*- coding: utf-8 -*-
"""Repository: typed models in, database rows out (and back).

Everything that touches SQL lives here, so the analysis layer never sees a
cursor.  The one invariant the repository protects is that a finished estimate
is never overwritten: writing an existing ``run_id`` raises
:class:`DuplicateRunError` instead of silently replacing history.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from fa.data.database.connection import DatabaseError, connect, dumps, loads
from fa.models.asset import Asset, AssetType
from fa.models.estimation import EstimationResult, EstimationRun
from fa.models.holding import HoldingSnapshot
from fa.models.market_data import FxSeries, PricePoint, PriceSeries


class DuplicateRunError(DatabaseError):
    """A run with this ``run_id`` already exists; history is append-only."""


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def snapshot_id_for(fund_code: str, quarter: str) -> str:
    raw = f"{fund_code}|{quarter}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:20]


class Repository:
    """Storage facade over the V2 SQLite schema."""

    def __init__(self, path: str | Path | None = None, conn: sqlite3.Connection | None = None):
        self._conn = conn if conn is not None else connect(path)
        self._owns_conn = conn is None

    # -- lifecycle -------------------------------------------------------------
    def close(self) -> None:
        if self._owns_conn:
            self._conn.close()

    def __enter__(self) -> "Repository":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- funds -----------------------------------------------------------------
    def upsert_fund(
        self,
        fund_code: str,
        name: str | None = None,
        fund_type: str | None = None,
        is_qdii: bool | None = None,
        source: str | None = None,
    ) -> None:
        code = str(fund_code).strip()
        if not code:
            raise DatabaseError("fund_code 不可為空")
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO funds (fund_code, name, fund_type, is_qdii, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(fund_code) DO UPDATE SET
                    name = COALESCE(excluded.name, funds.name),
                    fund_type = COALESCE(excluded.fund_type, funds.fund_type),
                    is_qdii = COALESCE(excluded.is_qdii, funds.is_qdii),
                    source = COALESCE(excluded.source, funds.source),
                    updated_at = excluded.updated_at
                """,
                (
                    code,
                    name,
                    fund_type,
                    None if is_qdii is None else (1 if is_qdii else 0),
                    source,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def load_fund(self, fund_code: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM funds WHERE fund_code = ?", (str(fund_code).strip(),)
        ).fetchone()
        return dict(row) if row else None

    # -- holdings --------------------------------------------------------------
    def save_snapshot(self, snapshot: HoldingSnapshot) -> str:
        snapshot.validate()
        snapshot_id = snapshot_id_for(snapshot.fund_code, snapshot.quarter or "")
        self.upsert_fund(snapshot.fund_code, source=snapshot.source)
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO holdings_snapshots
                    (snapshot_id, fund_code, quarter, holding_date, disclosure_date,
                     disclosure_date_estimated, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    holding_date = excluded.holding_date,
                    disclosure_date = excluded.disclosure_date,
                    disclosure_date_estimated = excluded.disclosure_date_estimated,
                    source = excluded.source
                """,
                (
                    snapshot_id,
                    snapshot.fund_code,
                    snapshot.quarter or "",
                    snapshot.holding_date.isoformat(),
                    snapshot.disclosure_date.isoformat(),
                    1 if snapshot.disclosure_date_estimated else 0,
                    snapshot.source,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            for asset in snapshot.assets:
                self._conn.execute(
                    """
                    INSERT INTO assets
                        (asset_id, ticker, name, asset_type, market, currency, exchange, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(asset_id) DO UPDATE SET
                        ticker = excluded.ticker,
                        name = excluded.name,
                        asset_type = excluded.asset_type,
                        market = excluded.market,
                        currency = excluded.currency,
                        exchange = excluded.exchange
                    """,
                    (
                        asset.asset_id,
                        asset.ticker,
                        asset.name,
                        asset.asset_type.value,
                        asset.market,
                        asset.currency,
                        asset.exchange,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO holdings (snapshot_id, asset_id, weight)
                    VALUES (?, ?, ?)
                    ON CONFLICT(snapshot_id, asset_id) DO UPDATE SET weight = excluded.weight
                    """,
                    (snapshot_id, asset.asset_id, float(asset.weight)),
                )
        return snapshot_id

    def load_snapshots(self, fund_code: str) -> list[HoldingSnapshot]:
        rows = self._conn.execute(
            """
            SELECT * FROM holdings_snapshots
            WHERE fund_code = ?
            ORDER BY holding_date ASC
            """,
            (str(fund_code).strip(),),
        ).fetchall()
        return [self._snapshot_from_row(row) for row in rows]

    def snapshot_as_of(self, fund_code: str, day: date) -> HoldingSnapshot | None:
        """Newest snapshot disclosed on or before ``day`` (point-in-time)."""
        rows = self._conn.execute(
            """
            SELECT * FROM holdings_snapshots
            WHERE fund_code = ? AND disclosure_date <= ? AND holding_date <= ?
            ORDER BY holding_date DESC, disclosure_date DESC
            LIMIT 1
            """,
            (str(fund_code).strip(), day.isoformat(), day.isoformat()),
        ).fetchall()
        return self._snapshot_from_row(rows[0]) if rows else None

    def _snapshot_from_row(self, row: sqlite3.Row) -> HoldingSnapshot:
        assets: list[Asset] = []
        for item in self._conn.execute(
            """
            SELECT a.*, h.weight FROM holdings h
            JOIN assets a ON a.asset_id = h.asset_id
            WHERE h.snapshot_id = ?
            ORDER BY h.weight DESC
            """,
            (row["snapshot_id"],),
        ).fetchall():
            assets.append(
                Asset(
                    asset_id=item["asset_id"],
                    name=item["name"] or item["asset_id"],
                    asset_type=AssetType.parse(item["asset_type"]),
                    ticker=item["ticker"],
                    market=item["market"],
                    currency=item["currency"],
                    weight=item["weight"],
                    holding_date=_as_date(row["holding_date"]),
                    source=row["source"] or "",
                    exchange=item["exchange"],
                )
            )
        return HoldingSnapshot(
            fund_code=row["fund_code"],
            assets=assets,
            quarter=row["quarter"],
            holding_date=_as_date(row["holding_date"]),
            disclosure_date=_as_date(row["disclosure_date"]),
            disclosure_date_estimated=bool(row["disclosure_date_estimated"]),
            source=row["source"] or "",
        )

    # -- market prices ---------------------------------------------------------
    def save_prices(self, series: PriceSeries) -> int:
        if not series.points:
            return 0
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO market_prices
                    (ticker, price_date, price_type, close, adj_close, price_timestamp,
                     retrieved_at, market, currency, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, price_date, price_type) DO UPDATE SET
                    close = excluded.close,
                    adj_close = excluded.adj_close,
                    price_timestamp = excluded.price_timestamp,
                    retrieved_at = excluded.retrieved_at,
                    market = excluded.market,
                    currency = excluded.currency,
                    source = excluded.source
                """,
                [
                    (
                        series.ticker,
                        point.price_date.isoformat(),
                        point.price_type,
                        float(point.close),
                        None if point.adj_close is None else float(point.adj_close),
                        point.price_timestamp.isoformat() if point.price_timestamp else None,
                        point.retrieved_at.isoformat() if point.retrieved_at else None,
                        point.market or series.market,
                        point.currency or series.currency,
                        point.source or series.source,
                    )
                    for point in series.points
                ],
            )
        return len(series.points)

    def load_prices(
        self, ticker: str, start: date | None = None, end: date | None = None
    ) -> PriceSeries:
        sql = "SELECT * FROM market_prices WHERE ticker = ?"
        params: list[Any] = [str(ticker)]
        if start:
            sql += " AND price_date >= ?"
            params.append(start.isoformat())
        if end:
            sql += " AND price_date <= ?"
            params.append(end.isoformat())
        sql += " ORDER BY price_date ASC, price_type ASC"
        rows = self._conn.execute(sql, params).fetchall()
        points = [
            PricePoint(
                price_date=_as_date(row["price_date"]),
                close=row["close"],
                adj_close=row["adj_close"],
                price_timestamp=(
                    datetime.fromisoformat(row["price_timestamp"])
                    if row["price_timestamp"]
                    else None
                ),
                retrieved_at=(
                    datetime.fromisoformat(row["retrieved_at"]) if row["retrieved_at"] else None
                ),
                market=row["market"] or "",
                currency=row["currency"] or "",
                price_type=row["price_type"],
                source=row["source"] or "",
            )
            for row in rows
        ]
        market = points[0].market if points else ""
        currency = points[0].currency if points else ""
        source = points[0].source if points else ""
        return PriceSeries.from_points(
            ticker, points, market=market, currency=currency, source=source
        )

    # -- FX --------------------------------------------------------------------
    def save_fx(self, series: FxSeries) -> int:
        if not series.points:
            return 0
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO fx_rates (currency, rate_date, rate, source, retrieved_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(currency, rate_date) DO UPDATE SET
                    rate = excluded.rate,
                    source = excluded.source,
                    retrieved_at = excluded.retrieved_at
                """,
                [
                    (
                        series.currency,
                        point.price_date.isoformat(),
                        float(point.value),
                        point.source or series.source,
                        point.retrieved_at.isoformat() if point.retrieved_at else None,
                    )
                    for point in series.points
                ],
            )
        return len(series.points)

    def load_fx(self, currency: str, start: date | None = None, end: date | None = None) -> FxSeries:
        sql = "SELECT * FROM fx_rates WHERE currency = ?"
        params: list[Any] = [str(currency).upper()]
        if start:
            sql += " AND rate_date >= ?"
            params.append(start.isoformat())
        if end:
            sql += " AND rate_date <= ?"
            params.append(end.isoformat())
        sql += " ORDER BY rate_date ASC"
        rows = self._conn.execute(sql, params).fetchall()
        points = [
            PricePoint(
                price_date=_as_date(row["rate_date"]),
                close=row["rate"],
                market="FX",
                currency="CNY",
                price_type="fx_close",
                source=row["source"] or "",
                retrieved_at=(
                    datetime.fromisoformat(row["retrieved_at"]) if row["retrieved_at"] else None
                ),
            )
            for row in rows
        ]
        return FxSeries(currency=str(currency).upper(), points=points)

    # -- official NAV ----------------------------------------------------------
    def save_official_nav(
        self, fund_code: str, rows: Iterable[tuple[date, float]], source: str = "eastmoney"
    ) -> int:
        payload = [
            (str(fund_code).strip(), day.isoformat(), float(nav), source)
            for day, nav in rows
        ]
        if not payload:
            return 0
        self.upsert_fund(fund_code, source=source)
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO official_nav (fund_code, nav_date, nav, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(fund_code, nav_date) DO UPDATE SET
                    nav = excluded.nav, source = excluded.source
                """,
                payload,
            )
        return len(payload)

    def load_official_nav(self, fund_code: str) -> list[tuple[date, float]]:
        rows = self._conn.execute(
            "SELECT nav_date, nav FROM official_nav WHERE fund_code = ? ORDER BY nav_date ASC",
            (str(fund_code).strip(),),
        ).fetchall()
        return [(_as_date(row["nav_date"]), row["nav"]) for row in rows]

    def nav_on_or_before(self, fund_code: str, day: date) -> tuple[date, float] | None:
        row = self._conn.execute(
            """
            SELECT nav_date, nav FROM official_nav
            WHERE fund_code = ? AND nav_date <= ?
            ORDER BY nav_date DESC LIMIT 1
            """,
            (str(fund_code).strip(), day.isoformat()),
        ).fetchone()
        if not row:
            return None
        return _as_date(row["nav_date"]), row["nav"]

    # -- estimation runs -------------------------------------------------------
    def save_estimation(
        self,
        run: EstimationRun,
        inputs: dict[str, Any],
        result: EstimationResult,
    ) -> str:
        if result.run_id != run.run_id:
            raise DatabaseError("result.run_id 與 run.run_id 不一致")
        existing = self._conn.execute(
            "SELECT 1 FROM estimation_runs WHERE run_id = ?", (run.run_id,)
        ).fetchone()
        if existing:
            raise DuplicateRunError(f"估算 {run.run_id} 已存在，歷史紀錄不可覆蓋")
        self.upsert_fund(run.fund_code, source=run.holdings_source or "estimate")
        run_row = run.to_row()
        result_row = result.to_row()
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO estimation_runs
                        (run_id, fund_code, mode, model_version, trade_date, target_nav_date,
                         base_nav_date, as_of, created_at, holdings_snapshot_id, look_ahead_safe,
                         timezone, cutoff, holdings_source, holdings_quarter)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        run.fund_code,
                        run.mode,
                        run.model_version,
                        run.trade_date.isoformat(),
                        run.target_nav_date.isoformat(),
                        run.base_nav_date.isoformat() if run.base_nav_date else None,
                        run.as_of.isoformat() if run.as_of else None,
                        run.created_at.isoformat(timespec="seconds"),
                        None,
                        1 if run.look_ahead_safe else 0,
                        run.timezone,
                        run.cutoff,
                        run.holdings_source,
                        run.holdings_quarter,
                    ),
                )
                self._conn.execute(
                    "INSERT INTO estimation_inputs (run_id, payload_json) VALUES (?, ?)",
                    (run.run_id, dumps(inputs)),
                )
                self._conn.execute(
                    """
                    INSERT INTO estimation_results
                        (run_id, status, base_nav, estimated_nav, estimated_return,
                         coverage_ratio, uncovered_weight, local_contribution, fx_contribution,
                         data_quality, quality_score, message, warnings_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.run_id,
                        result.status.value,
                        result.base_nav,
                        result.estimated_nav,
                        result.estimated_return,
                        result.coverage_ratio,
                        result.uncovered_weight,
                        result.local_contribution,
                        result.fx_contribution,
                        result.data_quality,
                        result.quality_score,
                        result.message,
                        dumps(result.warnings),
                    ),
                )
        except sqlite3.Error as exc:
            raise DatabaseError(f"寫入估算紀錄失敗: {exc}") from exc
        return run.run_id

    def load_estimation(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            """
            SELECT r.*, s.status AS status, s.base_nav, s.estimated_nav,
                   s.estimated_return, s.coverage_ratio, s.uncovered_weight,
                   s.local_contribution, s.fx_contribution, s.data_quality,
                   s.quality_score, s.message, s.warnings_json
            FROM estimation_runs r
            LEFT JOIN estimation_results s ON s.run_id = r.run_id
            WHERE r.run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["warnings"] = loads(payload.pop("warnings_json") or "[]")
        return payload

    def estimation_inputs(self, run_id: str) -> dict:
        row = self._conn.execute(
            "SELECT payload_json FROM estimation_inputs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if not row:
            return {}
        return loads(row["payload_json"])

    def list_estimations(self, fund_code: str | None = None, limit: int = 50) -> list[dict]:
        sql = """
            SELECT r.run_id, r.fund_code, r.target_nav_date, r.base_nav_date, r.created_at,
                   r.model_version, s.status, s.estimated_nav, s.estimated_return,
                   s.coverage_ratio, s.data_quality
            FROM estimation_runs r
            LEFT JOIN estimation_results s ON s.run_id = r.run_id
        """
        params: list[Any] = []
        if fund_code:
            sql += " WHERE r.fund_code = ?"
            params.append(str(fund_code).strip())
        sql += " ORDER BY r.target_nav_date DESC, r.created_at DESC LIMIT ?"
        params.append(int(limit))
        return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    # -- backtests -------------------------------------------------------------
    def save_backtest_results(
        self, run_id: str, metrics_by_group: dict[str, dict[str, Any]]
    ) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        payload = [
            (
                run_id,
                str(group),
                int(metrics.get("n") or 0),
                metrics.get("mae"),
                metrics.get("rmse"),
                metrics.get("mean_error"),
                metrics.get("max_abs_error"),
                now,
            )
            for group, metrics in metrics_by_group.items()
        ]
        if not payload:
            return 0
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO backtest_results
                    (run_id, group_key, n, mae, rmse, mean_error, max_abs_error, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, group_key) DO UPDATE SET
                    n = excluded.n, mae = excluded.mae, rmse = excluded.rmse,
                    mean_error = excluded.mean_error, max_abs_error = excluded.max_abs_error,
                    computed_at = excluded.computed_at
                """,
                payload,
            )
        return len(payload)

    def load_backtest_results(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM backtest_results WHERE run_id = ? ORDER BY group_key", (run_id,)
        ).fetchall()
        return [dict(row) for row in rows]
