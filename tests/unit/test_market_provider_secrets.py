"""Der Finnhub-Key steht im Query-String der URL. Er darf in keiner Fehlerausgabe auftauchen: weder in
Meldung, `repr`, Traceback und Fehlerattributen noch in Logs (auch nicht in denen von httpx und CachedProvider)."""

import logging
import traceback

import httpx
import pytest

from scout_research.data.cache import SqliteCache
from scout_research.data.market_provider import (
    CachedProvider,
    FinnhubProvider,
    MarketDataUnavailable,
)

KEY = "FAKE-FINNHUB-KEY-0123456789abcdef"


def _provider(handler, **kwargs) -> FinnhubProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return FinnhubProvider(KEY, client=client, sleep=lambda _: None, **kwargs)


def _status(code: int, headers: dict | None = None):
    return lambda request: httpx.Response(code, headers=headers or {}, text="body")


def _raises(exc_type):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("fehlgeschlagen", request=request)

    return handler


FAILURES = [
    pytest.param(_raises(httpx.ConnectError), None, id="netzwerk"),
    pytest.param(_raises(httpx.ReadTimeout), None, id="timeout"),
    pytest.param(_status(429, {"Retry-After": "0"}), 429, id="429"),
    pytest.param(_status(403), 403, id="403"),
    pytest.param(_status(500), 500, id="500"),
    pytest.param(_status(404), 404, id="404"),
]


def _assert_no_key(*texts: str) -> None:
    for text in texts:
        assert KEY not in text and "token=" not in text.replace("token=<redacted>", "")


@pytest.mark.parametrize("handler,status", FAILURES)
def test_finnhub_errors_never_contain_the_api_key(handler, status, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    provider = _provider(handler)

    with pytest.raises(MarketDataUnavailable) as exc_info:
        provider.get_price("AAPL")

    error = exc_info.value
    assert isinstance(error, httpx.HTTPError)  # CachedProvider fängt weiter httpx.HTTPError
    assert error.status_code == status and error.provider == "Finnhub"
    _assert_no_key(
        str(error),
        repr(error),
        "".join(traceback.format_exception(error)),
        caplog.text,
        *map(str, error.args),
        *(str(v) for v in vars(error).values()),
    )
    # keine rohe httpx-Ausnahme (mit URL/Token) hängt am Fehler
    assert error.__cause__ is None and error.__context__ is None
    assert not any(isinstance(v, (httpx.Request, httpx.Response)) for v in vars(error).values())


def test_error_message_names_only_provider_and_status() -> None:
    with pytest.raises(MarketDataUnavailable) as exc_info:
        _provider(_status(500)).get_price("AAPL")
    assert str(exc_info.value) == "Finnhub-Abruf fehlgeschlagen (HTTP 500)"

    with pytest.raises(MarketDataUnavailable) as exc_info:
        _provider(_raises(httpx.ConnectError)).get_price("AAPL")
    assert str(exc_info.value) == "Finnhub-Abruf fehlgeschlagen (Netzwerkfehler oder Timeout)"


def test_the_key_is_still_sent_to_finnhub() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"c": 10.0, "t": 1759363200})

    assert _provider(handler).get_price("AAPL") is not None
    assert seen[0].url.params["token"] == KEY  # Bereinigung betrifft Fehler und Logs, nicht die Anfrage


def test_httpx_request_log_lines_are_redacted(caplog: pytest.LogCaptureFixture) -> None:
    """httpx loggt auf INFO jede Anfrage mit vollständiger URL. Ohne Filter stünde der Token im Log."""
    caplog.set_level(logging.DEBUG)

    _provider(lambda request: httpx.Response(200, json={"c": 10.0, "t": 1759363200})).get_price("AAPL")

    request_lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert request_lines, "httpx hat keine Anfrage geloggt: der Test prüft den Pfad nicht"
    assert all(KEY not in line for line in request_lines)
    assert any("token=<redacted>" in line for line in request_lines)


def test_cached_provider_logs_cache_and_errors_never_contain_the_key(
    caplog: pytest.LogCaptureFixture, tmp_path
) -> None:
    caplog.set_level(logging.DEBUG)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.url.params["symbol"] == "FAIL":
            return httpx.Response(500)
        return httpx.Response(200, json={"c": 10.0, "t": 1759363200})

    with SqliteCache(tmp_path / "cache.sqlite") as cache:
        provider = CachedProvider(primary=_provider(handler), fallback=None, cache=cache)

        assert provider.get_price("AAPL") is not None  # Treffer vom Anbieter
        assert provider.get_price("AAPL") is not None  # Treffer aus dem Cache
        assert provider.get_price("FAIL") is None  # Fehler -> None, nie ein Fehler mit Token
        stored = [row[0] for row in cache._conn.execute("SELECT value FROM cache")]

    assert calls["n"] >= 2
    _assert_no_key(caplog.text, *stored)
    for record in caplog.records:
        _assert_no_key(record.getMessage(), str(record.exc_text or ""))


# --- ungültige Antworten: bereinigter Fehler statt roher JSONDecodeError/ValueError --------------------------


@pytest.mark.parametrize(
    "body",
    ["<html>Wartung</html>", "[1, 2, 3]", '{"c": "abc", "t": 1700000000}', '{"c": 10, "t": 99999999999999999999}'],
    ids=["kein-json", "liste", "preis-text", "zeitstempel"],
)
def test_invalid_finnhub_response_becomes_sanitized_market_data_unavailable(body: str) -> None:
    provider = _provider(lambda request: httpx.Response(200, text=body))

    with pytest.raises(MarketDataUnavailable) as exc_info:
        provider.get_price("AAPL")

    error = exc_info.value
    assert isinstance(error, httpx.HTTPError)
    _assert_no_key(str(error), repr(error), "".join(traceback.format_exception(error)))
    assert error.__cause__ is None and error.__context__ is None
