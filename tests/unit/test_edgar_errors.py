"""Typisierte EDGAR-Fehler (L1): Mapping, Wiederholungen, Rate Limit je Versuch — und: nichts Vertrauliches in
Fehlern, Meldungen, Tracebacks und Logs (User-Agent mit Name und E-Mail, Request-Header, API-Keys)."""

import logging
import traceback
from datetime import datetime, timezone

import httpx
import pytest

from scout_research.data.edgar_client import (
    EdgarClient,
    EdgarDataNotFound,
    EdgarError,
    EdgarHttpError,
    EdgarRateLimited,
    EdgarUnavailable,
)

SECRET_UA = "Scout Research (geheim.person@example.org)"
SECRET_HEADER = "Bearer sk-test-geheim-0123456789"
SECRETS = ["geheim.person", "example.org", SECRET_UA, SECRET_HEADER, "sk-test-geheim"]
URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"


class CountingLimiter:
    def __init__(self) -> None:
        self.acquired = 0

    def acquire(self) -> None:
        self.acquired += 1


def _client(handler, sleeps: list[float] | None = None, limiter: CountingLimiter | None = None) -> EdgarClient:
    return EdgarClient(
        user_agent=SECRET_UA,
        rate_limiter=limiter,  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
        sleep=(sleeps.append if sleeps is not None else (lambda _: None)),
    )


def _status(code: int, headers: dict | None = None):
    return lambda request: httpx.Response(code, headers=headers or {}, text="body")


# --- Mapping ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 403])
def test_rate_limit_statuses_map_to_edgar_rate_limited_without_retry(status: int) -> None:
    limiter, sleeps = CountingLimiter(), []
    with _client(_status(status, {"Retry-After": "17"}), sleeps, limiter) as client:
        with pytest.raises(EdgarRateLimited) as exc:
            client.get_company_facts("320193")

    assert exc.value.status_code == status
    assert exc.value.retry_after_seconds == 17.0
    assert (limiter.acquired, sleeps) == (1, [])  # genau ein Versuch, kein Backoff


def test_rate_limited_without_retry_after_header_has_none() -> None:
    with _client(_status(429)) as client:
        with pytest.raises(EdgarRateLimited) as exc:
            client.get_company_facts("320193")
    assert exc.value.retry_after_seconds is None


def test_retry_after_http_date_is_taken_over() -> None:
    future = datetime.now(timezone.utc).replace(microsecond=0)
    header = (future.replace(year=future.year + 1)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    with _client(_status(429, {"Retry-After": header})) as client:
        with pytest.raises(EdgarRateLimited) as exc:
            client.get_company_facts("320193")
    assert exc.value.retry_after_seconds and exc.value.retry_after_seconds > 0


def test_404_maps_to_data_not_found_without_retry() -> None:
    limiter, sleeps = CountingLimiter(), []
    with _client(_status(404), sleeps, limiter) as client:
        with pytest.raises(EdgarDataNotFound) as exc:
            client.get_company_facts("320193")

    assert exc.value.status_code == 404 and exc.value.endpoint.endswith("CIK0000320193.json")
    assert (limiter.acquired, sleeps) == (1, [])


@pytest.mark.parametrize("status", [400, 401, 410])
def test_other_client_errors_map_to_generic_http_error_without_retry(status: int) -> None:
    limiter = CountingLimiter()
    with _client(_status(status), limiter=limiter) as client:
        with pytest.raises(EdgarHttpError) as exc:
            client.get_company_facts("320193")
    assert exc.value.status_code == status and limiter.acquired == 1
    assert not isinstance(exc.value, (EdgarUnavailable, EdgarRateLimited, EdgarDataNotFound))


@pytest.mark.parametrize("status", [500, 502, 503])
def test_server_errors_retry_with_backoff_then_raise_unavailable(status: int) -> None:
    limiter, sleeps = CountingLimiter(), []
    with _client(_status(status), sleeps, limiter) as client:
        with pytest.raises(EdgarUnavailable) as exc:
            client.get_company_facts("320193")

    assert exc.value.status_code == status
    assert limiter.acquired == 3  # 1 + 2 Wiederholungen, jeder Versuch läuft durch den Rate Limiter
    assert sleeps == [0.5, 1.0]  # Backoff, begrenzt


def test_network_errors_and_timeouts_retry_then_raise_unavailable() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectTimeout("zeitüberschreitung", request=request)

    sleeps: list[float] = []
    with _client(handler, sleeps) as client:
        with pytest.raises(EdgarUnavailable) as exc:
            client.get_company_facts("320193")

    assert len(attempts) == 3 and sleeps == [0.5, 1.0]
    assert exc.value.status_code is None


def test_transient_failure_then_success_returns_the_response() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json={"facts": {}})

    with _client(handler) as client:
        assert client.get_company_facts("320193") == {"facts": {}}
    assert len(calls) == 2


def test_all_typed_errors_share_one_base_and_carry_no_tool_vocabulary() -> None:
    for cls in (EdgarUnavailable, EdgarRateLimited, EdgarDataNotFound, EdgarHttpError):
        assert issubclass(cls, EdgarError)
        assert not hasattr(cls, "code") and not hasattr(cls, "retryable")  # Mapping erst im Tool-Layer


# --- Sicherheit: keine Header, kein User-Agent, keine Keys in Fehlern, Tracebacks, Logs ---------------------


def _assert_clean(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, f"{secret!r} taucht in einer Fehlerausgabe auf"


@pytest.mark.parametrize(
    "responder",
    [
        _status(429, {"Retry-After": "5", "X-Echo": SECRET_HEADER}),
        _status(403, {"X-Echo": SECRET_HEADER}),
        _status(404, {"X-Echo": SECRET_HEADER}),
        _status(400, {"X-Echo": SECRET_HEADER}),
        _status(503, {"X-Echo": SECRET_HEADER}),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("verbindung", request=request)),
        # F5b: Ausnahmen, die kein TransportError sind und früher roh (mit .request/Headern) entkamen
        lambda request: (_ for _ in ()).throw(httpx.DecodingError("kaputte kompression", request=request)),
        lambda request: (_ for _ in ()).throw(httpx.TooManyRedirects("zu viele umleitungen", request=request)),
        _status(301, {"Location": "https://data.sec.gov/anderswo", "X-Echo": SECRET_HEADER}),
        lambda request: httpx.Response(200, text="<html>kein json</html>", headers={"X-Echo": SECRET_HEADER}),
    ],
    ids=["429", "403", "404", "400", "503", "netzwerk", "decoding", "redirects", "3xx", "ungueltiges-json"],
)
def test_errors_never_contain_request_headers_user_agent_or_keys(responder, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with _client(responder) as client:
        with pytest.raises(EdgarError) as exc:
            client.get_company_facts("320193")

    error = exc.value
    rendered = "".join(traceback.format_exception(error))
    for text in (str(error), repr(error), rendered, caplog.text, *map(str, error.args)):
        _assert_clean(text)
    # keine ursprüngliche httpx-Ausnahme (und mit ihr keine Request-Header) hängt am Fehler
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__
    assert not any(isinstance(v, (httpx.Request, httpx.Response, httpx.Headers)) for v in vars(error).values())


def test_error_exposes_only_endpoint_path_and_status() -> None:
    with _client(_status(503)) as client:
        with pytest.raises(EdgarUnavailable) as exc:
            client.get_company_facts("320193")
    assert vars(exc.value).keys() <= {"endpoint", "status_code", "retry_after_seconds"}
    assert exc.value.endpoint == "/api/xbrl/companyfacts/CIK0000320193.json"


# --- F5b: nicht-Transport-Ausnahmen, Redirects und ungültiges JSON -------------------------------------------


def test_decoding_error_is_retried_and_mapped_to_unavailable() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.DecodingError("kaputt", request=request)

    with _client(handler) as client:
        with pytest.raises(EdgarUnavailable) as exc:
            client.get_company_facts("320193")
    assert len(attempts) == 3 and exc.value.status_code is None


def test_redirect_response_maps_to_http_error_without_retry() -> None:
    limiter = CountingLimiter()
    with _client(_status(302, {"Location": "https://example.org/"}), limiter=limiter) as client:
        with pytest.raises(EdgarHttpError) as exc:
            client.get_company_facts("320193")
    assert exc.value.status_code == 302 and limiter.acquired == 1


def test_invalid_json_maps_to_unavailable_with_endpoint_and_status_only() -> None:
    with _client(lambda request: httpx.Response(200, text="<html>Wartung</html>")) as client:
        with pytest.raises(EdgarUnavailable) as exc:
            client.get_company_facts("320193")
    error = exc.value
    assert error.endpoint == "/api/xbrl/companyfacts/CIK0000320193.json" and error.status_code == 200
    assert error.__cause__ is None and error.__context__ is None
