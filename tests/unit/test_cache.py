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
