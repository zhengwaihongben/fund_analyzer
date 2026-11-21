# -*- coding: utf-8 -*-
"""Repository: typed models in, database rows out (and back).

Everything that touches SQL lives here, so the analysis layer never sees a
cursor.  The one invariant the repository protects is that a finished estimate
is never overwritten: writing an existing ``run_id`` raises
:class:`DuplicateRunError` instead of silently replacing history.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from fa.config import PRICE_REVISION_TOLERANCE
from fa.data.database.connection import DatabaseError, connect, dumps, loads
from fa.data.price_canonical import PIT_AVAILABILITY_SOURCES, version_key
from fa.models.asset import Asset, AssetType
from fa.models.estimation import EstimationResult, EstimationRun
from fa.models.holding import HoldingSnapshot
from fa.models.market_data import (
    AvailabilitySource,
    FxSeries,
    PricePoint,
    PriceSeries,
    iso_utc,
)


class DuplicateRunError(DatabaseError):
    """A run with this ``run_id`` already exists; history is append-only."""


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _as_dt(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp, returning ``None`` for empty values."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


#: Fields whose change on re-download means the previous observation was wrong:
#: the superseded row is copied into ``price_revisions`` first.  Availability
#: metadata (``available_at`` / ``availability_source`` / ``availability_note`` /
#: ``source`` / ``market`` / ``currency`` / ``fetched_at``) is *enrichment* and
#: deliberately does **not** trigger a revision: it can only be filled in later,
#: and treating it as a correction would flood the ledger.
REVISION_NUMERIC_FIELDS = ("close", "adj_close", "dividend", "split")

# ``PIT_AVAILABILITY_SOURCES`` and ``version_key`` come from
# ``fa/data/price_canonical.py`` (imported above): the whitelist and the
# canonical-version rule have exactly one definition, shared with the pipeline,
# so a query and a cache decision can never disagree about what counts as usable.


def _close_enough(left: Any, right: Any, tolerance: float = 1e-9) -> bool:
    """Whether two stored prices are the same number (``None`` equals ``None``)."""
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def _timestamp_corrected(stored: Any, incoming: datetime | None) -> bool:
    """Whether a known stored ``market_timestamp`` is being *changed*.

    ``NULL → value`` is enrichment (the vendor only now told us the instant);
    two different known instants are a correction of the observation itself.
    """
    if incoming is None:
        return False
    previous = _as_dt(stored)
    if previous is None:
        return False
    return previous.astimezone(timezone.utc) != incoming.astimezone(timezone.utc)


def _fx_revision_fields(
    row: Any, point: PricePoint, tolerance: float = PRICE_REVISION_TOLERANCE
) -> list[str]:
    """Which fields of an existing FX row the incoming bar actually corrects.

    Only two things make the previous observation *wrong*:

    * ``rate`` — a different number for the same trading date beyond the
      tolerance (float noise must not create a revision);
    * ``market_timestamp`` — a known instant being replaced by another known
      instant.

    Filling in ``available_at`` / ``availability_source`` later is not a
    correction: it is information that did not exist before.  Reporting an
    empty list therefore means "nothing to ledger, just refresh the row".
    """
    changed: list[str] = []
    if not _close_enough(row["rate"], point.value, tolerance):
        changed.append("rate")
    if _timestamp_corrected(row["market_timestamp"], point.market_timestamp):
        changed.append("market_timestamp")
    return changed


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
            revisions = self._record_price_revisions(series)
            self._conn.executemany(
                """
                INSERT INTO market_prices
                    (ticker, price_date, price_type, close, adj_close,
                     market_timestamp, available_at, availability_source, availability_note,
                     fetched_at, first_fetched_at, market, currency, source,
                     dividend, split, price_timestamp, retrieved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, price_date, price_type) DO UPDATE SET
                    close = excluded.close,
                    adj_close = excluded.adj_close,
                    market_timestamp = COALESCE(excluded.market_timestamp,
                                                market_prices.market_timestamp),
                    available_at = COALESCE(excluded.available_at, market_prices.available_at),
                    availability_source = CASE
                        WHEN excluded.availability_source = 'unknown'
                        THEN market_prices.availability_source
                        ELSE excluded.availability_source END,
                    availability_note = COALESCE(NULLIF(excluded.availability_note, ''),
                                                 market_prices.availability_note),
                    fetched_at = COALESCE(excluded.fetched_at, market_prices.fetched_at),
                    first_fetched_at = COALESCE(market_prices.first_fetched_at,
                                                excluded.first_fetched_at),
                    market = COALESCE(NULLIF(excluded.market, ''), market_prices.market),
                    currency = COALESCE(NULLIF(excluded.currency, ''), market_prices.currency),
                    source = COALESCE(NULLIF(excluded.source, ''), market_prices.source),
                    dividend = COALESCE(excluded.dividend, market_prices.dividend),
                    split = COALESCE(excluded.split, market_prices.split),
                    -- 舊欄位同步鏡射，但不可被「沒有帶時間」的新點清空：
                    -- V2.2 讀取路徑仍然只認 price_timestamp / retrieved_at。
                    price_timestamp = COALESCE(excluded.price_timestamp,
                                               market_prices.price_timestamp),
                    retrieved_at = COALESCE(excluded.retrieved_at,
                                            market_prices.retrieved_at)
                """,
                [
                    self._price_row(series, point, revisions)
                    for point in series.points
                ],
            )
        return len(series.points)

    def _price_row(
        self,
        series: PriceSeries,
        point: PricePoint,
        revisions: dict[tuple[str, str], str],
    ) -> tuple[Any, ...]:
        """One ``market_prices`` row, with availability corrected for revisions.

        A revised number only became knowable when the re-download happened, so
        its ``available_at`` is pushed forward to ``replaced_at`` when the stored
        availability is earlier.  Pushing it back (or inheriting the original
        bar's availability) would make the corrected value visible in the past
        and quietly rewrite history for anyone replaying that date.
        """
        available = point.available_at
        replaced = revisions.get((point.price_date.isoformat(), point.available_price_type))
        if replaced is not None and available is not None:
            replaced_at = _as_dt(replaced)
            if replaced_at is not None and replaced_at > available:
                available = replaced_at
        return (
            series.ticker,
            point.price_date.isoformat(),
            # market_prices.price_type 存的是「來源提供了什麼」，不是
            # 「這次計算用了什麼」；用 price_type 別名會在計算後把
            # used 寫進來，讓同一根 K 線在不同 run 之間換 key。
            point.available_price_type,
            float(point.close),
            None if point.adj_close is None else float(point.adj_close),
            iso_utc(point.market_timestamp),
            iso_utc(available),
            point.availability_source.value,
            point.availability_note or None,
            iso_utc(point.fetched_at),
            iso_utc(point.fetched_at),
            point.market or series.market,
            point.currency or series.currency,
            point.source or series.source,
            point.dividend,
            point.split,
            # 舊欄位同步鏡射，讓 V2.2 的讀取路徑仍拿得到時間。
            iso_utc(point.market_timestamp),
            iso_utc(point.fetched_at),
        )

    def _record_price_revisions(self, series: PriceSeries) -> dict[tuple[str, str], str]:
        """Keep the superseded row when a re-download changes the observation.

        Trigger fields are ``close``, ``adj_close``, ``dividend`` and ``split``
        (a corrected corporate action changes the return just as much as a
        corrected close) plus ``market_timestamp`` when both the stored and the
        incoming value are known and differ.  Filling a previously unknown
        availability timestamp, note or source is *enrichment*, not a
        correction, so it does not create a revision row.

        Returns ``{(price_date, price_type): replaced_at}`` for the rows that were
        superseded, which :meth:`save_prices` needs to push the new value's
        ``available_at`` forward to the moment the revision happened.
        """
        revisions: dict[tuple[str, str], str] = {}
        dates = sorted({point.price_date for point in series.points})
        if not dates:
            return revisions
        rows = self._conn.execute(
            "SELECT * FROM market_prices WHERE ticker = ? AND price_date BETWEEN ? AND ?",
            (series.ticker, dates[0].isoformat(), dates[-1].isoformat()),
        ).fetchall()
        existing = {(row["price_date"], row["price_type"]): row for row in rows}
        now = iso_utc(datetime.now(timezone.utc))
        for point in series.points:
            previous = existing.get((point.price_date.isoformat(), point.available_price_type))
            if previous is None:
                continue
            # close / adj_close 直接覆寫；dividend / split 走 COALESCE。後者傳 None
            # 表示「這次沒有提供」，資料庫會保留舊值，因此要比較的是「寫入後實際
            # 生效的值」，否則會記下一筆根本沒發生的修訂。
            direct = ("close", "adj_close")
            changed = [
                name for name in direct
                if not _close_enough(previous[name], getattr(point, name))
            ]
            for name in REVISION_NUMERIC_FIELDS:
                if name in direct:
                    continue
                incoming = getattr(point, name)
                effective = previous[name] if incoming is None else incoming
                if not _close_enough(previous[name], effective):
                    changed.append(name)
            if _timestamp_corrected(previous["market_timestamp"], point.market_timestamp):
                changed.append("market_timestamp")
            if not changed:
                continue
            self._conn.execute(
                """
                INSERT INTO price_revisions
                    (ticker, price_date, price_type, close, adj_close, market_timestamp,
                     available_at, availability_source, availability_note, fetched_at,
                     first_fetched_at, market, currency, source, dividend, split,
                     replaced_at, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    previous["ticker"],
                    previous["price_date"],
                    previous["price_type"],
                    previous["close"],
                    previous["adj_close"],
                    previous["market_timestamp"],
                    previous["available_at"],
                    previous["availability_source"],
                    previous["availability_note"],
                    previous["fetched_at"],
                    previous["first_fetched_at"],
                    previous["market"],
                    previous["currency"],
                    previous["source"],
                    previous["dividend"],
                    previous["split"],
                    now,
                    "re-download replaced the stored " + ", ".join(changed),
                ),
            )
            revisions[(previous["price_date"], previous["price_type"])] = now
        return revisions

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
        points = [self._price_point_from_row(row) for row in rows]
        market = points[0].market if points else ""
        currency = points[0].currency if points else ""
        source = points[0].source if points else ""
        return PriceSeries.from_points(
            ticker, points, market=market, currency=currency, source=source
        )

    @staticmethod
    def _price_point_from_row(row: Any) -> PricePoint:
        """Build a :class:`PricePoint` from a ``market_prices`` row.

        Shared with :meth:`prices_as_of`, whose rows come from a union of
        ``market_prices`` and ``price_revisions`` and therefore carry every
        column of the former.
        """
        keys = row.keys()
        return PricePoint(
            price_date=_as_date(row["price_date"]),
            close=row["close"],
            adj_close=row["adj_close"],
            market_timestamp=_as_dt(
                row["market_timestamp"] if "market_timestamp" in keys
                else row["price_timestamp"]
            ),
            available_at=_as_dt(row["available_at"] if "available_at" in keys else None),
            availability_source=(
                row["availability_source"] if "availability_source" in keys
                else AvailabilitySource.UNKNOWN
            ),
            availability_note=(
                row["availability_note"] if "availability_note" in keys else ""
            ) or "",
            fetched_at=_as_dt(
                row["fetched_at"] if "fetched_at" in keys else row["retrieved_at"]
            ),
            market=row["market"] or "",
            currency=row["currency"] or "",
            available_price_type=row["price_type"],
            source=row["source"] or "",
            dividend=row["dividend"] if "dividend" in keys else None,
            split=row["split"] if "split" in keys else None,
            revision_id=(
                int(row["revision_id"] or 0) if "revision_id" in keys else 0
            ),
        )

    # -- point-in-time prices --------------------------------------------------
    def count_price_revisions(
        self, ticker: str, start: date | None = None, end: date | None = None
    ) -> int:
        """Number of superseded price rows recorded for ``ticker``."""
        sql = "SELECT COUNT(*) AS n FROM price_revisions WHERE ticker = ?"
        params: list[Any] = [str(ticker)]
        if start is not None:
            sql += " AND price_date >= ?"
            params.append(start.isoformat())
        if end is not None:
            sql += " AND price_date <= ?"
            params.append(end.isoformat())
        row = self._conn.execute(sql, params).fetchone()
        return int(row["n"])

    def prices_as_of(
        self,
        ticker: str,
        as_of: datetime,
        start: date | None = None,
        end: date | None = None,
    ) -> PriceSeries:
        """Read the price version that was public at ``as_of``.

        Every candidate must satisfy both conditions: it was published by
        ``as_of`` (``available_at <= as_of``) and, if it has since been
        superseded, it had not been replaced yet (``replaced_at > as_of``).  The
        current row has no ``replaced_at``, treated as +∞.  Without the second
        condition a re-download would rewrite what a backtest could have known
        on an old date — precisely the look-ahead this foundation exists to
        prevent.

        The two instant comparisons run in Python on timezone-aware datetimes,
        not in SQL.  ``julianday()`` is a double and near 2026 an ulp is roughly
        40 µs, so ``available_at - 1 µs`` and ``available_at`` compare equal there
        and a price would become visible before publication; string comparison is
        no better because ``iso_utc()`` trims trailing zeros (``…00Z`` sorts after
        ``…00.5Z``).  Neither can honour the contract at microsecond precision.

        The optional ``start``/``end`` window, in contrast, is a plain trading
        date, so it is pushed into both branches of the UNION: without it a
        two-year backtest read every stored year of that ticker only to discard
        the extra dates in Python.
        """
        if not isinstance(as_of, datetime):
            raise DatabaseError("prices_as_of(as_of=…) 必須是帶時區的 datetime")
        if as_of.tzinfo is None:
            raise DatabaseError("prices_as_of(as_of=…) 必須帶時區")
        moment = as_of.astimezone(timezone.utc)
        whitelist = ", ".join("?" for _ in PIT_AVAILABILITY_SOURCES)
        date_filter = ""
        date_params: list[str] = []
        if start is not None:
            date_filter += " AND price_date >= ?"
            date_params.append(start.isoformat())
        if end is not None:
            date_filter += " AND price_date <= ?"
            date_params.append(end.isoformat())
        union = f"""
            SELECT ticker, price_date, price_type, close, adj_close, market_timestamp,
                   available_at, availability_source, availability_note, fetched_at,
                   first_fetched_at, market, currency, source, dividend, split,
                   0 AS revision_id, NULL AS superseded_at
              FROM market_prices
             WHERE ticker = ?
               AND available_at IS NOT NULL
               AND availability_source IN ({whitelist}){date_filter}
            UNION ALL
            SELECT ticker, price_date, price_type, close, adj_close, market_timestamp,
                   available_at, availability_source, availability_note, fetched_at,
                   first_fetched_at, market, currency, source, dividend, split,
                   revision_id, replaced_at AS superseded_at
              FROM price_revisions
             WHERE ticker = ?
               AND available_at IS NOT NULL
               AND availability_source IN ({whitelist}){date_filter}
        """
        params: list[Any] = [
            str(ticker), *PIT_AVAILABILITY_SOURCES, *date_params,
            str(ticker), *PIT_AVAILABILITY_SOURCES, *date_params,
        ]
        rows = self._conn.execute(union, params).fetchall()

        best: dict[str, Any] = {}
        for row in rows:
            available = _as_dt(row["available_at"])
            if available is None or available > moment:
                continue
            superseded = _as_dt(row["superseded_at"])
            if superseded is not None and superseded <= moment:
                continue
            key = row["price_date"]
            current = best.get(key)
            if current is None or self._pit_preference(row) >= self._pit_preference(current):
                best[key] = row
        points = [
            self._price_point_from_row(best[key]) for key in sorted(best)
        ]
        market = points[0].market if points else ""
        currency = points[0].currency if points else ""
        source = points[0].source if points else ""
        return PriceSeries.from_points(
            ticker, points, market=market, currency=currency, source=source
        )

    @staticmethod
    def _pit_preference(row: Any) -> tuple[datetime, int, int, int, str]:
        """Order two visible versions of the same date.

        Delegates to :func:`fa.data.price_canonical.version_key`, the same pure
        rule the price pipeline uses to choose the canonical version of a trading
        date.  Later availability wins (fresher evidence at that instant); on a
        tie prefer ``adj_close``, then the richer price type, then a revision row,
        and finally the type name so the order never depends on row order.

        The availability instant is parsed before comparing: stored ISO strings
        differ in fractional precision (``iso_utc()`` trims zeros), so comparing
        the raw text would misorder ``…00Z`` against ``…00.5Z``.
        """
        return version_key(
            _as_dt(row["available_at"]),
            row["adj_close"],
            row["price_type"],
            bool(row["revision_id"]),
        )

    # -- FX --------------------------------------------------------------------
    def save_fx(self, series: FxSeries) -> int:
        """Upsert an FX series and ledger the versions it replaces.

        Returns the number of rows written (the V2.2 contract is unchanged).
        A row only becomes a *revision* when that trading date already exists
        and the vendor now reports a materially different number; a first
        write, an identical re-download, or filling in an availability time
        that was unknown before is enrichment.  A date the vendor stops
        reporting is never deleted, and a bar without time information is
        stored as-is rather than being given an invented timestamp.
        """
        if not series.points:
            return 0
        base = str(series.base_currency or "").strip().upper()
        if not base:
            raise DatabaseError(
                f"FX 序列 {series.currency} 未聲明 base_currency；"
                "匯率方向必須由來源明確聲明（本專案只接受 CNY 基準），不可預設")
        if base != "CNY":
            raise DatabaseError(
                f"FX 序列 {series.currency} 的 base_currency 為 {base}；"
                "本專案只儲存 CNY 基準（FX = CNY per 1 單位外幣），"
                "其他方向會被讀成反向報酬")
        replaced_at = datetime.now(timezone.utc)
        with self._conn:
            self._record_fx_revisions(series, replaced_at=replaced_at)
            self._conn.executemany(
                """
                INSERT INTO fx_rates
                    (currency, rate_date, rate, source, retrieved_at, base_currency,
                     market_timestamp, available_at, availability_source,
                     availability_note, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(currency, rate_date) DO UPDATE SET
                    rate = excluded.rate,
                    source = excluded.source,
                    retrieved_at = excluded.retrieved_at,
                    base_currency = excluded.base_currency,
                    market_timestamp = COALESCE(excluded.market_timestamp,
                                                fx_rates.market_timestamp),
                    available_at = COALESCE(excluded.available_at, fx_rates.available_at),
                    availability_source = CASE
                        WHEN excluded.availability_source = 'unknown'
                        THEN fx_rates.availability_source
                        ELSE excluded.availability_source END,
                    availability_note = COALESCE(NULLIF(excluded.availability_note, ''),
                                                 fx_rates.availability_note),
                    fetched_at = COALESCE(excluded.fetched_at, fx_rates.fetched_at)
                """,
                [
                    (
                        series.currency,
                        point.price_date.isoformat(),
                        float(point.value),
                        point.source or series.source,
                        iso_utc(point.fetched_at),
                        base,
                        iso_utc(point.market_timestamp),
                        iso_utc(point.available_at),
                        point.availability_source.value,
                        point.availability_note or None,
                        iso_utc(point.fetched_at),
                    )
                    for point in series.points
                ],
            )
        return len(series.points)

    def _record_fx_revisions(self, series: FxSeries, *, replaced_at: datetime) -> int:
        """Copy superseded FX rows into ``fx_revisions`` before they are overwritten.

        The copy is of the **stored** row — its own rate and its own
        ``market_timestamp`` / ``available_at`` / ``availability_source`` /
        ``source`` / ``fetched_at`` — never of the incoming bar: the whole point
        of the ledger is to preserve what the previous observation claimed.
        """
        dates = sorted({point.price_date for point in series.points})
        rows = self._conn.execute(
            "SELECT * FROM fx_rates WHERE currency = ? AND rate_date BETWEEN ? AND ?",
            (series.currency, dates[0].isoformat(), dates[-1].isoformat()),
        ).fetchall()
        stored = {row["rate_date"]: row for row in rows}
        written = 0
        for point in series.points:
            row = stored.get(point.price_date.isoformat())
            if row is None:
                continue
            changed = _fx_revision_fields(row, point)
            if not changed:
                continue
            keys = row.keys()
            self._conn.execute(
                """
                INSERT INTO fx_revisions
                    (currency, rate_date, rate, base_currency, market_timestamp,
                     available_at, availability_source, source, fetched_at,
                     replaced_at, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["currency"],
                    row["rate_date"],
                    row["rate"],
                    (row["base_currency"] or "CNY")
                    if "base_currency" in keys else "CNY",
                    row["market_timestamp"] if "market_timestamp" in keys else None,
                    row["available_at"] if "available_at" in keys else None,
                    (row["availability_source"] or "unknown")
                    if "availability_source" in keys else "unknown",
                    row["source"],
                    (row["fetched_at"] or row["retrieved_at"])
                    if "fetched_at" in keys else row["retrieved_at"],
                    iso_utc(replaced_at),
                    "fx revision replaced " + ", ".join(changed),
                ),
            )
            written += 1
        return written

    @staticmethod
    def _fx_point_from_row(row: Any) -> PricePoint:
        """Rebuild one FX bar, keeping every time field the row carries."""
        keys = row.keys()
        fetched = row["fetched_at"] if "fetched_at" in keys else None
        return PricePoint(
            price_date=_as_date(row["rate_date"]),
            close=row["rate"],
            market="FX",
            currency="CNY",
            available_price_type="fx_close",
            used_price_type="fx_close",
            source=row["source"] or "",
            market_timestamp=_as_dt(
                row["market_timestamp"] if "market_timestamp" in keys else None
            ),
            available_at=_as_dt(
                row["available_at"] if "available_at" in keys else None
            ),
            availability_source=(
                row["availability_source"] if "availability_source" in keys
                else AvailabilitySource.UNKNOWN
            ),
            availability_note=(
                row["availability_note"] if "availability_note" in keys else ""
            ) or "",
            fetched_at=_as_dt(fetched or row["retrieved_at"]),
        )

    @staticmethod
    def _fx_base_of(rows: list[Any]) -> str:
        if rows and "base_currency" in rows[0].keys() and rows[0]["base_currency"]:
            return rows[0]["base_currency"]
        # fx_rates 只存 CNY 基準（FX_TICKERS 的約定），所以基準是明確的，不是預設值。
        return "CNY"

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
        points = [self._fx_point_from_row(row) for row in rows]
        return FxSeries(currency=str(currency).upper(), points=points,
                        base_currency=self._fx_base_of(rows), source="database")

    def fx_as_of(
        self,
        currency: str,
        as_of: datetime,
        start: date | None = None,
        end: date | None = None,
    ) -> FxSeries:
        """Point-in-time FX series: only rates that were public at ``as_of``.

        The visibility gate is ``available_at`` alone — *when the number became
        knowable* — never the calendar date the caller happens to have in its
        own timezone.  Filtering on ``rate_date <= as_of.date()`` used to drop
        rows that were already public whenever the caller's local date sat one
        day behind London (e.g. ``2026-11-16T23:30-08:00`` is
        ``2026-11-17T07:30Z``, after a ``2026-11-17`` bar became available at
        ``00:30Z``).  ``rate_date`` is a London trading date, so comparing it to
        the caller's local date was never a valid PIT rule.

        ``start`` / ``end`` stay available as an explicit window on the trading
        date when the caller asks for one; they are not a visibility rule.

        A row is visible only when it carries a real publication instant from
        one of the four-state model's *usable* sources (whitelist, not "any
        string except unknown").  Legacy rows without availability information
        stay readable through :meth:`load_fx` (V2.2 behaviour) but are
        deliberately invisible here — an FX rate whose availability nobody
        recorded cannot be used to prove a backtest.
        """
        if not isinstance(as_of, datetime):
            raise DatabaseError(
                "fx_as_of 的 as_of 必須是 datetime；只給日期無法判斷當天稍晚的公告")
        if as_of.tzinfo is None:
            raise DatabaseError(
                "fx_as_of 的 as_of 必須帶時區（naive datetime 無法與 available_at 比較）")
        code = str(currency or "").strip().upper()
        if not code:
            raise DatabaseError("fx_as_of 需要貨幣代碼")
        placeholders = ", ".join("?" for _ in PIT_AVAILABILITY_SOURCES)
        sql = (
            "SELECT * FROM fx_rates WHERE currency = ?"
            " AND available_at IS NOT NULL"
            f" AND availability_source IN ({placeholders})"
            # julianday() 會解析 ISO 文字裡的時區與小數秒；字串比較會在
            # "…00Z" 與 "…00.5Z" 這種情況下排錯順序。
            " AND julianday(available_at) <= julianday(?)"
        )
        params: list[Any] = [code, *PIT_AVAILABILITY_SOURCES, iso_utc(as_of)]
        if start:
            sql += " AND rate_date >= ?"
            params.append(start.isoformat())
        if end:
            sql += " AND rate_date <= ?"
            params.append(end.isoformat())
        sql += " ORDER BY rate_date ASC"
        rows = self._conn.execute(sql, params).fetchall()
        points = [self._fx_point_from_row(row) for row in rows]
        return FxSeries(currency=code, points=points,
                        base_currency=self._fx_base_of(rows), source="database")

    def count_fx_revisions(
        self, currency: str, start: date | None = None, end: date | None = None
    ) -> int:
        sql = "SELECT COUNT(*) AS n FROM fx_revisions WHERE currency = ?"
        params: list[Any] = [str(currency).upper()]
        if start:
            sql += " AND rate_date >= ?"
            params.append(start.isoformat())
        if end:
            sql += " AND rate_date <= ?"
            params.append(end.isoformat())
        row = self._conn.execute(sql, params).fetchone()
        return int(row["n"]) if row else 0

    def last_fx_date(self, currency: str) -> date | None:
        row = self._conn.execute(
            "SELECT MAX(rate_date) AS day FROM fx_rates WHERE currency = ?",
            (str(currency).upper(),),
        ).fetchone()
        return _as_date(row["day"]) if row else None

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
                         look_ahead_status, timezone, cutoff, holdings_source, holdings_quarter)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        run.look_ahead_status.value,
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

    # -- V2.3.2 strategy backtests (Stage D, Migration 004) --------------------
    def price_metadata(self, ticker: str) -> tuple[str, str] | None:
        """``(market, currency)`` declared by the stored rows for ``ticker``.

        Used for instruments the strategy does not hold (the benchmark),
        whose venue and currency must come from stored metadata rather than
        from the ticker's shape.  ``None`` means no row at all.
        """
        code = str(ticker or "").strip().upper()
        if not code:
            return None
        rows = self._conn.execute(
            "SELECT market, currency FROM market_prices WHERE ticker = ? "
            "ORDER BY price_date DESC LIMIT 10", (code,)).fetchall()
        for row in rows:
            market = str(row["market"] or "").strip()
            currency = str(row["currency"] or "").strip()
            if market or currency:
                return market, currency
        return None

    def save_backtest_run(self, run: Any) -> None:
        """Persist one strategy backtest and all of its rows atomically.

        The run, its metrics, every trade and every equity point are written in
        a single transaction: a failure leaves no partially stored run, which is
        what lets :func:`fa.backtesting.runner.run_backtest` claim idempotency
        (spec §4.4 / §8).
        """
        from fa.backtesting.models import BacktestRun

        if not isinstance(run, BacktestRun):
            raise DatabaseError(f"save_backtest_run 需要 BacktestRun: {run!r}")
        metrics = run.metrics.as_dict()
        trade_rows = [
            (
                run.run_id,
                index,
                trade.session.isoformat(),
                trade.ticker,
                trade.side,
                float(trade.quantity),
                float(trade.price_local),
                trade.price_source or "",
                None if trade.fx_rate is None else float(trade.fx_rate),
                trade.fx_source,
                float(trade.gross_base),
                float(trade.commission_base),
                float(trade.slippage_base),
                float(trade.fx_spread_base),
                float(trade.net_base),
                trade.reason,
            )
            for index, trade in enumerate(run.trades)
        ]
        equity_rows = [
            (
                run.run_id,
                point.session.isoformat(),
                float(point.cash_base),
                float(point.holdings_base),
                float(point.total_base),
                float(point.exposure),
                point.benchmark_base,
                point.drawdown,
            )
            for point in run.equity_curve
        ]
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO strategy_backtest_runs
                        (run_id, spec_id, model_version, start_date, end_date,
                         base_currency, universe_json, spec_json, costs_json,
                         data_fingerprint, look_ahead_status, created_at,
                         anomalies_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        run.spec_id,
                        run.model_version,
                        run.start.isoformat(),
                        run.end.isoformat(),
                        run.base_currency,
                        run.universe_json,
                        run.spec_json,
                        run.costs_json,
                        run.data_fingerprint,
                        run.look_ahead_status,
                        iso_utc(run.created_at),
                        json.dumps(list(run.anomalies), ensure_ascii=False),
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO strategy_backtest_metrics
                        (run_id, sessions, cagr, annualised_vol, sharpe, sortino,
                         max_drawdown, calmar, turnover, hit_rate, exposure,
                         cost_drag, trades, total_costs_base)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        int(metrics["sessions"]),
                        metrics["cagr"],
                        metrics["annualised_vol"],
                        metrics["sharpe"],
                        metrics["sortino"],
                        metrics["max_drawdown"],
                        metrics["calmar"],
                        metrics["turnover"],
                        metrics["hit_rate"],
                        metrics["exposure"],
                        metrics["cost_drag"],
                        int(metrics["trades"]),
                        float(metrics["total_costs_base"]),
                    ),
                )
                if trade_rows:
                    self._conn.executemany(
                        """
                        INSERT INTO strategy_backtest_trades
                            (run_id, seq, session, ticker, side, quantity,
                             price_local, price_source, fx_rate, fx_source,
                             gross_base, commission_base, slippage_base,
                             fx_spread_base, net_base, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        trade_rows,
                    )
                self._conn.executemany(
                    """
                    INSERT INTO strategy_backtest_equity
                        (run_id, session, cash_base, holdings_base, total_base,
                         exposure, benchmark_base, drawdown)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    equity_rows,
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRunError(
                f"回測 {run.run_id} 已存在，不可覆寫: {exc}") from exc
        except sqlite3.Error as exc:
            raise DatabaseError(f"寫入回測 {run.run_id} 失敗: {exc}") from exc

    def load_backtest_run(self, run_id: str) -> Any:
        """Rebuild a stored backtest, or ``None`` when it was never written."""
        from fa.backtesting.models import (
            BacktestMetrics,
            BacktestRun,
            EquityPoint,
            Trade,
        )

        row = self._conn.execute(
            """
            SELECT r.*, m.sessions, m.cagr, m.annualised_vol, m.sharpe, m.sortino,
                   m.max_drawdown, m.calmar, m.turnover, m.hit_rate, m.exposure,
                   m.cost_drag, m.trades AS metrics_trades, m.total_costs_base
            FROM strategy_backtest_runs r
            LEFT JOIN strategy_backtest_metrics m ON m.run_id = r.run_id
            WHERE r.run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        trade_rows = self._conn.execute(
            """
            SELECT * FROM strategy_backtest_trades
            WHERE run_id = ? ORDER BY seq
            """,
            (run_id,),
        ).fetchall()
        equity_rows = self._conn.execute(
            """
            SELECT * FROM strategy_backtest_equity
            WHERE run_id = ? ORDER BY session
            """,
            (run_id,),
        ).fetchall()
        metrics = BacktestMetrics(
            sessions=int(row["sessions"] or 0),
            trades=int(row["metrics_trades"] or 0),
            cagr=row["cagr"],
            annualised_vol=row["annualised_vol"],
            sharpe=row["sharpe"],
            sortino=row["sortino"],
            max_drawdown=row["max_drawdown"],
            calmar=row["calmar"],
            turnover=row["turnover"],
            hit_rate=row["hit_rate"],
            exposure=row["exposure"],
            cost_drag=row["cost_drag"],
            total_costs_base=float(row["total_costs_base"] or 0.0),
        )
        trades = tuple(
            Trade(
                session=_as_date(item["session"]),
                ticker=item["ticker"],
                side=item["side"],
                quantity=float(item["quantity"]),
                price_local=float(item["price_local"]),
                price_source=item["price_source"] or "",
                fx_rate=None if item["fx_rate"] is None else float(item["fx_rate"]),
                fx_source=item["fx_source"],
                gross_base=float(item["gross_base"]),
                commission_base=float(item["commission_base"]),
                slippage_base=float(item["slippage_base"]),
                fx_spread_base=float(item["fx_spread_base"]),
                net_base=float(item["net_base"]),
                reason=item["reason"] or "",
            )
            for item in trade_rows
        )
        equity_curve = tuple(
            EquityPoint(
                session=_as_date(item["session"]),
                cash_base=float(item["cash_base"]),
                holdings_base=float(item["holdings_base"]),
                total_base=float(item["total_base"]),
                exposure=float(item["exposure"]),
                benchmark_base=item["benchmark_base"],
                drawdown=item["drawdown"],
            )
            for item in equity_rows
        )
        created_at = _as_dt(row["created_at"])
        if created_at is None:
            raise DatabaseError(f"回測 {run_id} 的 created_at 無法解析")
        return BacktestRun(
            run_id=row["run_id"],
            spec_id=row["spec_id"],
            model_version=row["model_version"],
            start=_as_date(row["start_date"]),
            end=_as_date(row["end_date"]),
            base_currency=row["base_currency"] or "CNY",
            universe_json=row["universe_json"],
            spec_json=row["spec_json"],
            costs_json=row["costs_json"],
            data_fingerprint=row["data_fingerprint"],
            look_ahead_status=row["look_ahead_status"] or "unknown",
            created_at=created_at,
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
            anomalies=tuple(json.loads(row["anomalies_json"] or "[]")),
        )

    def count_backtest_runs(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM strategy_backtest_runs").fetchone()
        return int(row["n"] if row else 0)

    def list_backtest_runs(self, spec_id: str | None = None,
                           limit: int = 50) -> list[dict]:
        """Read-only summary rows, newest first, for history browsing.

        Only the four tables written by Migration 004 are read, and only
        with bound parameters: the numbers in ``strategy_backtest_metrics``
        were computed by the runner and are reported as stored, never
        recomputed here.  Ordering is ``created_at DESC, run_id DESC`` so a
        re-run of the same strategy sorts before its original, while a
        second call with the same arguments is idempotent.
        """
        try:
            size = int(limit)
        except (TypeError, ValueError) as exc:
            raise DatabaseError(f"limit 必須是整數: {limit!r}") from exc
        if isinstance(limit, bool):
            raise DatabaseError(f"limit 必須是整數: {limit!r}")
        size = max(1, min(size, 200))
        sql = [
            "SELECT r.run_id, r.spec_id, r.model_version, r.start_date,",
            "       r.end_date, r.base_currency, r.created_at,",
            "       r.look_ahead_status, r.data_fingerprint,",
            "       m.sessions, m.trades, m.cagr, m.max_drawdown,",
            "       m.total_costs_base",
            "  FROM strategy_backtest_runs r",
            "  LEFT JOIN strategy_backtest_metrics m ON m.run_id = r.run_id",
        ]
        params: list[Any] = []
        if spec_id is not None and str(spec_id).strip():
            sql.append(" WHERE r.spec_id = ?")
            params.append(str(spec_id).strip())
        sql.append(" ORDER BY r.created_at DESC, r.run_id DESC LIMIT ?")
        params.append(size)
        try:
            rows = self._conn.execute("\n".join(sql), params).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"讀取回測清單失敗: {exc}") from exc
        result = []
        for row in rows:
            result.append({
                "run_id": row["run_id"],
                "spec_id": row["spec_id"],
                "model_version": row["model_version"],
                "start": _as_date(row["start_date"]),
                "end": _as_date(row["end_date"]),
                "base_currency": row["base_currency"] or "CNY",
                "created_at": _as_dt(row["created_at"]),
                "look_ahead_status": row["look_ahead_status"] or "unknown",
                "data_fingerprint": row["data_fingerprint"],
                "sessions": int(row["sessions"] or 0),
                "trades": int(row["trades"] or 0),
                "cagr": row["cagr"],
                "max_drawdown": row["max_drawdown"],
                "total_costs_base": row["total_costs_base"],
            })
        return result
