# -*- coding: utf-8 -*-
"""SQLite connection management and migration runner for the V2 data layer.

Every migration runs inside one explicit transaction.  That matters because
``sqlite3.executescript()`` issues an implicit COMMIT before executing, which
silently destroys atomicity — an interrupted upgrade would leave half a schema
behind.  Here the SQL body is split into single statements and executed with
``conn.execute`` between ``BEGIN IMMEDIATE`` and ``COMMIT``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from fa.config import PROJECT_ROOT
from fa.data.database.schema import (
    MIGRATION_COLUMNS,
    MIGRATIONS,
    REVIEW_COLUMN_REPAIRS,
    SCHEMA_VERSION,
)

log = logging.getLogger(__name__)

#: Default database location (code and data are kept apart on disk).
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "database" / "fund_analyzer.db"


class DatabaseError(RuntimeError):
    """Raised when the database cannot be opened, migrated or written."""


def default_db_path() -> Path:
    return Path(DEFAULT_DB_PATH)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open (and migrate) the database, returning a ready connection."""
    db_path = Path(path) if path else default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:  # pragma: no cover - filesystem failure
        raise DatabaseError(f"無法開啟資料庫 {db_path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    initialise(conn)
    return conn


def _current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


def _split_statements(sql: str) -> Iterable[str]:
    """Split a migration body into single statements, dropping ``--`` comments."""
    for chunk in str(sql or "").split(";"):
        lines = [
            line for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        statement = "\n".join(lines).strip()
        if statement:
            yield statement


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _apply_columns(
    conn: sqlite3.Connection, columns: dict[str, dict[str, str]]
) -> None:
    """Add missing columns (SQLite has no ``ADD COLUMN IF NOT EXISTS``)."""
    for table, additions in columns.items():
        existing = _table_columns(conn, table)
        if not existing:
            continue  # table created by the SQL body will already have them
        for name, ddl in additions.items():
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            log.debug("遷移新增欄位 %s.%s", table, name)


def initialise(
    conn: sqlite3.Connection,
    *,
    _statement_hook: Callable[[str], None] | None = None,
) -> int:
    """Apply every pending migration; returns the resulting schema version.

    ``_statement_hook`` is a test seam used by the fault-injection tests: it is
    called with each statement right before execution, so a test can raise and
    assert that the whole migration rolled back.
    """
    version = _current_version(conn)
    for migration_version, sql in MIGRATIONS:
        if migration_version <= version:
            continue
        log.info("套用資料庫遷移 v%s", migration_version)
        if conn.in_transaction:
            conn.rollback()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _apply_columns(conn, MIGRATION_COLUMNS.get(migration_version, {}))
            for statement in _split_statements(sql):
                if _statement_hook is not None:
                    _statement_hook(statement)
                conn.execute(statement)
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (migration_version, datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        except Exception as exc:  # sqlite or injected failure
            conn.rollback()
            raise DatabaseError(f"資料庫遷移 v{migration_version} 失敗: {exc}") from exc
        version = migration_version
    # Deliberately narrow repair: `anomalies_json` was added to Migration 004
    # while V2.3.2 was still under review, so a database created by the earlier
    # build reports version 4 without the column.  Only this explicit list is
    # repaired.  Re-adding a Migration 002 column here would leave its backfill
    # SQL unrun and the database half-migrated, so MIGRATION_COLUMNS is never
    # replayed for an already-applied migration.
    if version >= 4:
        try:
            conn.execute("BEGIN IMMEDIATE")
            _apply_columns(conn, REVIEW_COLUMN_REPAIRS)
            conn.commit()
        except Exception as exc:  # pragma: no cover - defensive
            conn.rollback()
            raise DatabaseError(f"資料庫欄位修補失敗: {exc}") from exc
    return version


def schema_version(conn: sqlite3.Connection) -> int:
    return _current_version(conn)


def expected_version() -> int:
    return SCHEMA_VERSION


def dumps(payload: object) -> str:
    """Stable JSON for the replayable input snapshot."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def loads(text: str) -> dict:
    return json.loads(text) if text else {}
