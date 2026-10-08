import sqlite3
import tempfile
from pathlib import Path

from scout_research.data.cache import SqliteCache


def test_set_then_get_roundtrips() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "cache.sqlite"
        with SqliteCache(db_path) as cache:
            cache.set("market_price", "AAPL:2026-08-17", {"price": 230.5, "source": "finnhub"})
            result = cache.get("market_price", "AAPL:2026-08-17")

    assert result == {"price": 230.5, "source": "finnhub"}


def test_missing_key_returns_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "cache.sqlite"
        with SqliteCache(db_path) as cache:
            assert cache.get("market_price", "UNKNOWN:2026-08-17") is None


def test_set_overwrites_existing_key() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "cache.sqlite"
        with SqliteCache(db_path) as cache:
            cache.set("market_price", "AAPL:2026-08-17", {"price": 230.5})
            cache.set("market_price", "AAPL:2026-08-17", {"price": 231.0})
            result = cache.get("market_price", "AAPL:2026-08-17")

    assert result == {"price": 231.0}


def test_max_age_expires_entries_but_plain_get_does_not() -> None:
    now = [100.0]
    with tempfile.TemporaryDirectory() as tmp:
        with SqliteCache(Path(tmp) / "cache.sqlite", clock=lambda: now[0]) as cache:
            cache.set("edgar_frame", "k", [1, 2, 3])
            now[0] += 3600
            assert cache.get("edgar_frame", "k", max_age_seconds=7200) == [1, 2, 3]
            assert cache.get("edgar_frame", "k", max_age_seconds=1800) is None  # abgelaufen
            assert cache.get("edgar_frame", "k") == [1, 2, 3]  # ohne TTL unbegrenzt gültig (Kurs-Cache)


def test_existing_cache_file_without_timestamp_column_is_migrated() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "old.sqlite"
        old = sqlite3.connect(db_path)
        old.execute("CREATE TABLE cache (namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (namespace, key))")
        old.execute("INSERT INTO cache VALUES ('market_price', 'AAPL:2026-08-17', '{\"price\": 1.0}')")
        old.commit()
        old.close()

        with SqliteCache(db_path) as cache:
            assert cache.get("market_price", "AAPL:2026-08-17") == {"price": 1.0}  # Altzeile lesbar
            assert cache.get("market_price", "AAPL:2026-08-17", max_age_seconds=10) is None  # ohne Zeitstempel: abgelaufen
