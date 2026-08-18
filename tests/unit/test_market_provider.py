import tempfile
from pathlib import Path

import httpx
import pytest

from scout_research.data.cache import SqliteCache
from scout_research.data.market_provider import CachedProvider, FinnhubProvider, StooqProvider


def _client_with_handler(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_finnhub_provider_parses_quote() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "AAPL"
        assert request.url.params["token"] == "fake-key"
        return httpx.Response(200, json={"c": 230.5, "h": 232, "l": 229, "o": 230, "pc": 229.3, "t": 1755436800})

    provider = FinnhubProvider(api_key="fake-key", client=_client_with_handler(handler))
    quote = provider.get_price("aapl")

    assert quote is not None
    assert quote.ticker == "AAPL"
    assert quote.price == 230.5
    assert quote.source == "finnhub"


def test_finnhub_provider_returns_none_when_symbol_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"c": 0, "h": 0, "l": 0, "o": 0, "pc": 0, "t": 0})

    provider = FinnhubProvider(api_key="fake-key", client=_client_with_handler(handler))
    assert provider.get_price("NOPE") is None


def test_stooq_provider_parses_csv() -> None:
    csv_body = "Symbol,Date,Time,Open,High,Low,Close,Volume\r\nAAPL.US,2026-08-17,22:00:00,229.00,232.00,228.50,230.50,50000000\r\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=csv_body)

    provider = StooqProvider(client=_client_with_handler(handler))
    quote = provider.get_price("aapl")

    assert quote is not None
    assert quote.price == 230.50
    assert quote.as_of_date == "2026-08-17"
    assert quote.source == "stooq"


def test_cached_provider_falls_back_when_primary_fails() -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    def working_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Symbol,Date,Time,Open,High,Low,Close,Volume\r\nAAPL.US,2026-08-17,22:00:00,229,232,228.5,230.5,1\r\n")

    primary = FinnhubProvider(api_key="fake-key", client=_client_with_handler(failing_handler))
    fallback = StooqProvider(client=_client_with_handler(working_handler))

    with tempfile.TemporaryDirectory() as tmp:
        cache = SqliteCache(Path(tmp) / "cache.sqlite")
        provider = CachedProvider(primary=primary, fallback=fallback, cache=cache)
        quote = provider.get_price("AAPL")

    assert quote is not None
    assert quote.source == "stooq"


def test_cached_provider_returns_none_when_both_fail() -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    primary = FinnhubProvider(api_key="fake-key", client=_client_with_handler(failing_handler))
    fallback = StooqProvider(client=_client_with_handler(failing_handler))

    with tempfile.TemporaryDirectory() as tmp:
        cache = SqliteCache(Path(tmp) / "cache.sqlite")
        provider = CachedProvider(primary=primary, fallback=fallback, cache=cache)
        assert provider.get_price("AAPL") is None


def test_cached_provider_serves_second_call_from_cache_without_new_requests() -> None:
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"c": 230.5, "h": 0, "l": 0, "o": 0, "pc": 0, "t": 1755436800})

    primary = FinnhubProvider(api_key="fake-key", client=_client_with_handler(handler))

    with tempfile.TemporaryDirectory() as tmp:
        cache = SqliteCache(Path(tmp) / "cache.sqlite")
        provider = CachedProvider(primary=primary, fallback=None, cache=cache)
        provider.get_price("AAPL")
        provider.get_price("AAPL")

    assert call_count["n"] == 1
