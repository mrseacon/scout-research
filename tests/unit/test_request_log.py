"""Upstream-Protokoll (Plan 6, Schritt 8, TA-3): `RequestLog` hält je HTTP-Antwort nur Host, Pfad und Status — nie Query-String
(Finnhub-Key), Header (User-Agent mit Kontaktadresse) oder Body — und je Cache-Zugriff Namespace und Treffer."""

from __future__ import annotations

import json

import httpx

from scout_research.data.cache import SqliteCache
from scout_research.data.edgar_client import FRAME_CACHE_NAMESPACE, EdgarClient
from scout_research.data.http_util import RequestLog
from scout_research.data.market_provider import CachedProvider, FinnhubProvider, PriceQuote, StooqProvider
from scout_research.domain.metrics import REVENUE_CONCEPTS
from tests.unit.tool_world import FINNHUB_KEY, UA, NVDA_CIK, _NoLimit, standard_world


def _finnhub_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"c": 180.5, "t": 1760000000}, headers={"X-Echo": request.headers.get("User-Agent", "-")})


def _finnhub(log: RequestLog | None, handler=_finnhub_handler, client: httpx.Client | None = None) -> FinnhubProvider:
    client = client or httpx.Client(transport=httpx.MockTransport(handler))
    return FinnhubProvider(FINNHUB_KEY, client=client, sleep=lambda _: None, request_log=log)


def test_a_finnhub_request_is_logged_with_host_path_and_status_but_never_the_key() -> None:
    log = RequestLog()
    quote = _finnhub(log).get_price("NVDA")
    assert quote is not None
    assert log.entries == [{"kind": "http", "host": "finnhub.io", "path": "/api/v1/quote", "status": 200}]
    text = json.dumps(log.entries)
    assert FINNHUB_KEY not in text and "token" not in text and "symbol" not in text and "?" not in text


def test_every_attempt_is_logged_including_failed_ones() -> None:
    log = RequestLog()
    answers = iter([httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={"c": 1.0, "t": 1760000000})])
    _finnhub(log, handler=lambda r: next(answers)).get_price("NVDA")
    assert [e["status"] for e in log.entries] == [429, 200]


def test_an_edgar_request_is_logged_without_user_agent_or_contact_address() -> None:
    w = standard_world()
    log = RequestLog()
    client = EdgarClient(user_agent=UA, rate_limiter=_NoLimit(), transport=httpx.MockTransport(w._handler),  # type: ignore[arg-type]
                         sleep=lambda _: None, request_log=log)
    client.get_company_facts(NVDA_CIK)
    assert log.entries == [{"kind": "http", "host": "data.sec.gov", "path": f"/api/xbrl/companyfacts/CIK{NVDA_CIK}.json", "status": 200}]
    text = json.dumps(log.entries)
    for needle in (UA, "welt.tester", "example.invalid", "Scout Research", "User-Agent"):
        assert needle not in text


def test_an_edgar_query_url_is_logged_without_its_query() -> None:
    w = standard_world()
    log = RequestLog()
    client = EdgarClient(user_agent=UA, rate_limiter=_NoLimit(), transport=httpx.MockTransport(w._handler),  # type: ignore[arg-type]
                         sleep=lambda _: None, request_log=log)
    client.search_companies_by_sic("3674", max_pages=1)
    (entry,) = log.entries
    assert entry["path"] == "/cgi-bin/browse-edgar" and "3674" not in json.dumps(entry)


def test_a_failed_edgar_request_is_logged_with_its_status() -> None:
    w = standard_world()
    w.status_overrides["companyfacts/CIK"] = 404
    log = RequestLog()
    client = EdgarClient(user_agent=UA, rate_limiter=_NoLimit(), transport=httpx.MockTransport(w._handler),  # type: ignore[arg-type]
                         sleep=lambda _: None, request_log=log)
    try:
        client.get_company_facts(NVDA_CIK)
    except Exception:
        pass
    assert [e["status"] for e in log.entries] == [404]


def test_stooq_requests_are_logged_without_query() -> None:
    log = RequestLog()
    csv_body = "Symbol,Date,Time,Open,High,Low,Close,Volume\nNVDA.US,2026-10-08,22:00:00,1,2,0.5,1.5,100\n"
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=csv_body)))
    assert StooqProvider(client, request_log=log).get_price("NVDA") is not None
    assert log.entries == [{"kind": "http", "host": "stooq.com", "path": "/q/l/", "status": 200}]


def test_a_passed_client_keeps_its_own_hooks_and_gets_ours() -> None:
    seen: list[int] = []
    client = httpx.Client(transport=httpx.MockTransport(_finnhub_handler), event_hooks={"response": [lambda r: seen.append(r.status_code)]})
    log = RequestLog()
    _finnhub(log, client=client).get_price("NVDA")
    assert seen == [200] and len(log.entries) == 1
    _finnhub(log, client=client)  # derselbe Client noch einmal: der Haken wird nicht doppelt eingehängt
    client.get("https://finnhub.io/api/v1/quote")
    assert len(log.entries) == 2 and seen == [200, 200]


def test_without_a_request_log_no_hooks_are_registered() -> None:
    own = httpx.Client(transport=httpx.MockTransport(_finnhub_handler))
    provider = _finnhub(None, client=own)
    assert own.event_hooks == {"request": [], "response": []}
    stooq_client = httpx.Client()
    StooqProvider(stooq_client)
    assert stooq_client.event_hooks == {"request": [], "response": []}
    assert provider.get_price("NVDA") is not None
    edgar = EdgarClient(user_agent=UA, rate_limiter=_NoLimit(), transport=httpx.MockTransport(standard_world()._handler))  # type: ignore[arg-type]
    assert edgar._client.event_hooks == {"request": [], "response": []}


class _Primary:
    def __init__(self) -> None:
        self.calls = 0

    def get_price(self, ticker: str) -> PriceQuote | None:
        self.calls += 1
        return PriceQuote(ticker=ticker, price=10.0, as_of_date="2026-10-08", source="fake")


def test_cached_provider_logs_a_miss_and_then_a_hit() -> None:
    log = RequestLog()
    with SqliteCache(":memory:") as cache:
        provider = CachedProvider(_Primary(), None, cache, request_log=log)
        provider.get_price("NVDA")
        provider.get_price("NVDA")
    assert log.entries == [{"kind": "cache", "namespace": "market_price", "hit": False},
                           {"kind": "cache", "namespace": "market_price", "hit": True}]


def test_the_frame_cache_logs_a_miss_and_then_a_hit() -> None:
    w = standard_world()
    log = RequestLog()
    with SqliteCache(":memory:") as cache:
        def client() -> EdgarClient:
            return EdgarClient(user_agent=UA, rate_limiter=_NoLimit(), transport=httpx.MockTransport(w._handler),  # type: ignore[arg-type]
                               sleep=lambda _: None, cache=cache, request_log=log)
        concept = REVENUE_CONCEPTS[0]
        client().get_revenue_frame(concept, 2025)
        client().get_revenue_frame(concept, 2025)  # neuer Client: kein Speicher, aber der SQLite-Cache trifft
    kinds = [(e["kind"], e.get("namespace"), e.get("hit")) for e in log.entries]
    assert kinds == [("cache", FRAME_CACHE_NAMESPACE, False), ("http", None, None), ("cache", FRAME_CACHE_NAMESPACE, True)]


def test_mark_and_since_return_copies_of_the_entries_after_the_mark() -> None:
    log = RequestLog()
    log.record_cache("a", True)
    mark = log.mark()
    assert mark == 1 and log.since(mark) == []
    log.record_cache("b", False)
    entries = log.since(mark)
    assert entries == [{"kind": "cache", "namespace": "b", "hit": False}]
    entries[0]["hit"] = True
    assert log.since(mark)[0]["hit"] is False and len(log.entries) == 2
