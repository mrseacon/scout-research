import json
from pathlib import Path
from typing import Callable

import httpx
import pytest

from scout_research.data.cache import SqliteCache
from scout_research.data.edgar_client import EdgarClient
from scout_research.domain.metrics import REVENUE_CONCEPTS
from scout_research.domain.peers import (
    REVENUE_FRAME_CONCEPT,
    CandidateCompany,
    FrameYearUnresolved,
    classify_candidates_by_size,
    filter_candidates_by_size,
    find_peer_candidates,
    find_sic_candidates,
    search_sic_candidates,
)

TICKER_MAP_BODY = json.dumps(
    {
        "0": {"cik_str": 1111111111, "ticker": "AAA", "title": "Alpha Software Inc."},
        "1": {"cik_str": 2222222222, "ticker": "BBB", "title": "Beta Systems Corp"},
        # 3333333333 hat bewusst keinen Ticker-Eintrag -> nicht handelbar, muss rausfallen
    }
)

SIC_ATOM_BODY = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry title="ARRAY(0x1)">
    <content type="text/xml">
      <company-info name="ARRAY(0x1)">
        <cik>1111111111</cik>
        <sic>7372</sic>
      </company-info>
    </content>
  </entry>
  <entry title="ARRAY(0x2)">
    <content type="text/xml">
      <company-info name="ARRAY(0x2)">
        <cik>2222222222</cik>
        <sic>7372</sic>
      </company-info>
    </content>
  </entry>
  <entry title="ARRAY(0x3)">
    <content type="text/xml">
      <company-info name="ARRAY(0x3)">
        <cik>3333333333</cik>
        <sic>7372</sic>
      </company-info>
    </content>
  </entry>
  <entry title="ARRAY(0x4)">
    <content type="text/xml">
      <company-info name="ARRAY(0x4)">
        <cik>9999999999</cik>
        <sic>7372</sic>
      </company-info>
    </content>
  </entry>
</feed>"""


def _frame_body(entries: dict[int, float], end: str = "2024-12-31") -> str:
    return json.dumps(
        {
            "data": [
                {"cik": cik, "entityName": f"Company {cik}", "start": "2024-01-01", "end": end, "val": val, "accn": f"0000000000-25-{cik % 1000000:06d}"}
                for cik, val in entries.items()
            ]
        }
    )


def _client(handler: Callable[[httpx.Request], httpx.Response], cache: SqliteCache | None = None) -> EdgarClient:
    return EdgarClient(
        user_agent="Scout Research Tests test@example.com",
        transport=httpx.MockTransport(handler),
        cache=cache,
        sleep=lambda _: None,
    )


def _concept_of(request: httpx.Request) -> str:
    return str(request.url).split("/us-gaap/")[1].split("/")[0]


def _by_concept_handler(frames: dict[str, dict[int, float]], requested: list[str] | None = None):
    """Liefert je Umsatz-Konzept einen eigenen Frame; unbekannte Konzepte haben keinen Frame (404)."""

    def handler(request: httpx.Request) -> httpx.Response:
        concept = _concept_of(request)
        if requested is not None:
            requested.append(concept)
        if concept not in frames:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=_frame_body(frames[concept]))

    return handler


def test_find_sic_candidates_crossreferences_tickers_and_excludes_target() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "company_tickers.json" in str(request.url):
            return httpx.Response(200, text=TICKER_MAP_BODY)
        if "browse-edgar" in str(request.url):
            start = request.url.params.get("start", "0")
            if start == "0":
                return httpx.Response(200, text=SIC_ATOM_BODY)
            return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        raise AssertionError(f"unerwartete URL: {request.url}")

    with _client(handler) as client:
        candidates = find_sic_candidates(client, "7372", exclude_cik="9999999999")

    tickers = {c.ticker for c in candidates}
    assert tickers == {"AAA", "BBB"}  # 3333333333 (kein Ticker) und Target ausgeschlossen


def _candidates(*ciks: int) -> list[CandidateCompany]:
    return [CandidateCompany(cik=f"{cik:010d}", ticker=f"T{cik}", name=f"Co {cik}", sic_code="7372") for cik in ciks]


def test_filter_candidates_by_size_applies_02x_to_5x_range() -> None:
    candidates = _candidates(1, 2, 3, 4)
    target_revenue = 1000.0
    handler = _by_concept_handler({c: {1: 100.0, 2: 900.0, 3: 6000.0} for c in REVENUE_CONCEPTS})

    with _client(handler) as client:
        results = filter_candidates_by_size(client, candidates, target_revenue, calendar_year=2024)

    assert {r.ticker for r in results} == {"T2"}
    assert results[0].size_ratio == 0.9


def test_filter_candidates_by_size_zero_target_revenue_returns_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("sollte bei target_revenue<=0 gar nicht erst anfragen")

    with _client(handler) as client:
        assert filter_candidates_by_size(client, _candidates(1), target_revenue=0.0, calendar_year=2024) == []


def test_size_filter_loads_further_concepts_only_for_missing_candidates() -> None:
    requested: list[str] = []
    frames = {REVENUE_CONCEPTS[0]: {1: 900.0}, REVENUE_CONCEPTS[2]: {2: 800.0}}
    with _client(_by_concept_handler(frames, requested)) as client:
        outcome = classify_candidates_by_size(client, _candidates(1, 2), 1000.0, 2024)

    assert {c.ticker: c.revenue_concept for c in outcome.candidates} == {
        "T1": REVENUE_CONCEPTS[0],
        "T2": REVENUE_CONCEPTS[2],
    }
    assert requested == list(REVENUE_CONCEPTS[:3])  # Konzept 2 hat 404 (leer), Konzept 3 liefert Kandidat 2; Konzept 4 unnötig
    assert outcome.frames_loaded == [REVENUE_CONCEPTS[0], REVENUE_CONCEPTS[2]]


def test_size_filter_stops_loading_once_every_candidate_is_found() -> None:
    requested: list[str] = []
    with _client(_by_concept_handler({REVENUE_CONCEPTS[0]: {1: 900.0, 2: 800.0}}, requested)) as client:
        classify_candidates_by_size(client, _candidates(1, 2), 1000.0, 2024)

    assert requested == [REVENUE_CONCEPTS[0]]  # keine weiteren Frames, wenn niemand fehlt


def test_candidate_in_several_frames_takes_the_highest_priority_concept() -> None:
    frames = {REVENUE_CONCEPTS[0]: {1: 900.0}, REVENUE_CONCEPTS[1]: {1: 4000.0, 2: 500.0}}
    with _client(_by_concept_handler(frames)) as client:
        outcome = classify_candidates_by_size(client, _candidates(1, 2), 1000.0, 2024)

    by_ticker = {c.ticker: c for c in outcome.candidates}
    assert by_ticker["T1"].revenue == 900.0 and by_ticker["T1"].revenue_concept == REVENUE_CONCEPTS[0]
    assert by_ticker["T2"].revenue_concept == REVENUE_CONCEPTS[1]
    assert by_ticker["T1"].revenue_accession_number.startswith("0000000000-25-")  # Provenance der Größe


def test_size_filter_counts_every_exclusion() -> None:
    frames = {REVENUE_CONCEPTS[0]: {1: 100.0, 2: 900.0, 3: 6000.0}}
    with _client(_by_concept_handler(frames)) as client:
        outcome = classify_candidates_by_size(client, _candidates(1, 2, 3, 4), 1000.0, 2024)

    assert [c.ticker for c in outcome.candidates] == ["T2"]
    assert outcome.outside_size_range == 2  # 0,1x und 6x
    assert outcome.not_in_revenue_frame == 1  # Kandidat 4 steht in keinem Frame


def test_search_sic_candidates_counts_matches_and_missing_tickers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "company_tickers.json" in url:
            return httpx.Response(200, text=TICKER_MAP_BODY)
        start = request.url.params.get("start", "0")
        return httpx.Response(200, text=SIC_ATOM_BODY if start == "0" else '<feed xmlns="http://www.w3.org/2005/Atom"></feed>')

    with _client(handler) as client:
        outcome = search_sic_candidates(client, "7372", exclude_cik="9999999999")

    assert outcome.sic_matches == 3  # 1111111111, 2222222222, 3333333333 (Ziel 9999999999 ausgenommen)
    assert outcome.without_ticker == 1  # 3333333333
    assert [c.ticker for c in outcome.candidates] == ["AAA", "BBB"]


def _orchestration_handler(frame_by_concept: dict[str, dict[int, float]], requested: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "company_tickers.json" in url:
            return httpx.Response(200, text=TICKER_MAP_BODY)
        if "browse-edgar" in url:
            start = request.url.params.get("start", "0")
            if start == "0":
                return httpx.Response(200, text=SIC_ATOM_BODY)
            return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        if "frames" in url:
            return _by_concept_handler(frame_by_concept, requested)(request)
        raise AssertionError(f"unerwartete URL: {url}")

    return handler


def test_find_peer_candidates_orchestrates_sic_and_size_filter_with_counts() -> None:
    frames = {REVENUE_CONCEPTS[0]: {9999999999: 1000.0, 1111111111: 900.0, 2222222222: 50_000.0}}
    with _client(_orchestration_handler(frames)) as client:
        result = find_peer_candidates(
            client,
            target_cik="9999999999",
            target_sic="7372",
            target_revenue=1000.0,
            target_period_end="2024-12-31",
            target_revenue_concept=REVENUE_CONCEPTS[0],
        )

    assert [p.ticker for p in result.candidates] == ["AAA"]
    assert result.calendar_year.calendar_year == 2024 and result.calendar_year.frame_check == "target_in_frame"
    counts = result.counts
    assert (counts.sic_matches, counts.without_ticker, counts.not_in_revenue_frame) == (3, 1, 0)
    assert (counts.outside_size_range, counts.returned) == (1, 1)


def test_regression_target_with_revenues_concept_is_checked_against_its_own_frame() -> None:
    """Schritt-2-Fehler: `resolve_calendar_year` prüfte das Ziel nur im Frame des ersten Konzepts. NVDA taggt
    den Umsatz als `Revenues` und stand dort nie -> `FRAME_YEAR_UNRESOLVED`."""
    nvda_cik, nvda_end = 1045810, "2026-01-25"
    assert REVENUE_CONCEPTS[0] != "Revenues"
    frames = {
        REVENUE_CONCEPTS[0]: {1111111111: 900.0},  # NVDA fehlt hier
        "Revenues": {nvda_cik: 215_938_000_000.0},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "frames" in str(request.url):
            concept = _concept_of(request)
            if concept in frames:
                return httpx.Response(200, text=_frame_body(frames[concept], end=nvda_end))
            return httpx.Response(404, text="not found")
        return _orchestration_handler({})(request)

    with _client(handler) as client:
        result = find_peer_candidates(
            client,
            target_cik=f"{nvda_cik:010d}",
            target_sic="7372",
            target_revenue=215_938_000_000.0,
            target_period_end=nvda_end,
            target_revenue_concept="Revenues",
        )
        # und mit dem falschen (ersten) Konzept bleibt der alte Fehler sichtbar:
        with pytest.raises(FrameYearUnresolved):
            find_peer_candidates(
                client,
                target_cik=f"{nvda_cik:010d}",
                target_sic="7372",
                target_revenue=215_938_000_000.0,
                target_period_end=nvda_end,
                target_revenue_concept=REVENUE_CONCEPTS[0],
            )

    assert result.calendar_year.calendar_year == 2025  # 2026-01-25 minus 180 Tage = 2025-07-29
    assert result.calendar_year.concept == "Revenues"
    assert result.calendar_year.frame_check == "target_in_frame"


def test_frames_are_cached_in_memory_and_in_sqlite_with_seven_day_ttl(tmp_path: Path) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text=_frame_body({1: 900.0}))

    now = [1_000_000.0]
    db = tmp_path / "cache.sqlite"
    with SqliteCache(db, clock=lambda: now[0]) as cache:
        with _client(handler, cache) as client:
            first = client.get_revenue_frame("Revenues", 2025)
            assert client.get_revenue_frame("Revenues", 2025) is first  # Speicher: kein zweiter Abruf
            assert len(requests) == 1

        now[0] += 6 * 24 * 3600  # 6 Tage später: neuer Client liest aus SQLite
        with _client(handler, cache) as client:
            cached = client.get_revenue_frame("Revenues", 2025)
            assert cached == first and len(requests) == 1

        now[0] += 2 * 24 * 3600  # 8 Tage nach dem Schreiben: abgelaufen -> neuer Abruf
        with _client(handler, cache) as client:
            client.get_revenue_frame("Revenues", 2025)
            assert len(requests) == 2
