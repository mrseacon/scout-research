"""L1 — SQLite Key-Value-Cache.

Zweck (Foundation Doc 8.3): Schlusskurse ändern sich pro Handelstag genau einmal. Wird der
Cache auf (namespace, key) geschlüsselt — z. B. ("market_price", "AAPL:2026-08-18") — kostet
ein wiederholter Lauf am selben Handelstag null API-Calls.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class SqliteCache:
    def __init__(self, db_path: str | Path = "scout_research_cache.sqlite") -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (namespace, key)
            )
            """
        )
        self._conn.commit()

    def get(self, namespace: str, key: str) -> Any | None:
        row = self._conn.execute(
            "SELECT value FROM cache WHERE namespace = ? AND key = ?", (namespace, key)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, namespace: str, key: str, value: Any) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (namespace, key, value) VALUES (?, ?, ?)",
            (namespace, key, json.dumps(value)),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteCache":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
