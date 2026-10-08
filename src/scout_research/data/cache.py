"""L1 — SQLite Key-Value-Cache.

Zweck (Foundation Doc 8.3): Schlusskurse ändern sich pro Handelstag genau einmal. Wird der
Cache auf (namespace, key) geschlüsselt — z. B. ("market_price", "AAPL:2026-08-18") — kostet
ein wiederholter Lauf am selben Handelstag null API-Calls.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


class SqliteCache:
    def __init__(
        self, db_path: str | Path = "scout_research_cache.sqlite", clock: Callable[[], float] = time.time
    ) -> None:
        self._clock = clock
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
        # Ältere Cache-Dateien kennen `stored_at` noch nicht: Spalte nachrüsten (Altzeilen: NULL).
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(cache)")}
        if "stored_at" not in columns:
            self._conn.execute("ALTER TABLE cache ADD COLUMN stored_at REAL")
        self._conn.commit()

    def get(self, namespace: str, key: str, max_age_seconds: float | None = None) -> Any | None:
        """Wert oder `None`. Mit `max_age_seconds` gilt ein älterer Eintrag (und jede Altzeile ohne
        Zeitstempel) als nicht vorhanden; ohne bleibt er unbegrenzt gültig."""
        row = self._conn.execute(
            "SELECT value, stored_at FROM cache WHERE namespace = ? AND key = ?", (namespace, key)
        ).fetchone()
        if row is None:
            return None
        if max_age_seconds is not None and (row[1] is None or self._clock() - row[1] > max_age_seconds):
            return None
        return json.loads(row[0])

    def set(self, namespace: str, key: str, value: Any) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (namespace, key, value, stored_at) VALUES (?, ?, ?, ?)",
            (namespace, key, json.dumps(value, separators=(",", ":")), self._clock()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteCache":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
