# -*- coding: utf-8 -*-
"""SQLite connection management and migration runner for the V2 data layer."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from fa.config import PROJECT_ROOT
from fa.data.database.schema import MIGRATIONS, SCHEMA_VERSION

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


def initialise(conn: sqlite3.Connection) -> int:
    """Apply every pending migration; returns the resulting schema version."""
    version = _current_version(conn)
    for migration_version, sql in MIGRATIONS:
        if migration_version <= version:
            continue
        log.info("套用資料庫遷移 v%s", migration_version)
        try:
            with conn:
                conn.executescript(sql)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (migration_version, datetime.now().isoformat(timespec="seconds")),
                )
        except sqlite3.Error as exc:
            raise DatabaseError(f"資料庫遷移 v{migration_version} 失敗: {exc}") from exc
        version = migration_version
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
