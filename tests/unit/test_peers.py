import json
from typing import Callable

import httpx

from scout_research.data.edgar_client import EdgarClient
from scout_research.domain.peers import (
    REVENUE_FRAME_CONCEPT,
    filter_candidates_by_size,
    find_peer_candidates,
    find_sic_candidates,
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


def _make_frames_body(entries: dict[int, float]) -> str:
    return json.dumps(
        {
            "data": [
                {"cik": cik, "entityName": f"Company {cik}", "start": "2024-01-01", "end": "2024-12-31", "val": val, "accn": "0000000000-24-000001"}
                for cik, val in entries.items()
            ]
        }
    )


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> EdgarClient:
    return EdgarClient(user_agent="Scout Research Tests test@example.com", transport=httpx.MockTransport(handler))


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


def test_filter_candidates_by_size_applies_02x_to_5x_range() -> None:
    from scout_research.domain.peers import CandidateCompany

    candidates = [
        CandidateCompany(cik="0000000001", ticker="TOO_SMALL", name="Tiny", sic_code="7372"),
        CandidateCompany(cik="0000000002", ticker="IN_RANGE", name="Fits", sic_code="7372"),
        CandidateCompany(cik="0000000003", ticker="TOO_BIG", name="Huge", sic_code="7372"),
        CandidateCompany(cik="0000000004", ticker="NO_DATA", name="Unknown revenue", sic_code="7372"),
    ]
    target_revenue = 1000.0

    def handler(request: httpx.Request) -> httpx.Response:
        assert REVENUE_FRAME_CONCEPT in str(request.url)
        return httpx.Response(
            200,
            text=_make_frames_body(
                {1: 100.0, 2: 900.0, 3: 6000.0}  # 0.1x, 0.9x, 6x -> nur 2 im [0.2x,5x]-Fenster
            ),
        )

    with _client(handler) as client:
        results = filter_candidates_by_size(client, candidates, target_revenue, calendar_year=2024)

    result_tickers = {r.ticker for r in results}
    assert result_tickers == {"IN_RANGE"}
    assert results[0].size_ratio == 0.9


def test_filter_candidates_by_size_zero_target_revenue_returns_empty() -> None:
    from scout_research.domain.peers import CandidateCompany

    candidates = [CandidateCompany(cik="0000000001", ticker="X", name="X Inc", sic_code="7372")]

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("sollte bei target_revenue<=0 gar nicht erst anfragen")

    with _client(handler) as client:
        results = filter_candidates_by_size(client, candidates, target_revenue=0.0, calendar_year=2024)

    assert results == []


def test_find_peer_candidates_orchestrates_sic_and_size_filter() -> None:
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
            # AAA (1111111111) passt in die Groessenspanne, BBB (2222222222) nicht
            return httpx.Response(
                200, text=_make_frames_body({9999999999: 1000.0, 1111111111: 900.0, 2222222222: 50_000.0})
            )
        raise AssertionError(f"unerwartete URL: {url}")

    with _client(handler) as client:
        peers = find_peer_candidates(
            client, target_cik="9999999999", target_sic="7372", target_revenue=1000.0, target_period_end="2024-12-31"
        )

    assert [p.ticker for p in peers] == ["AAA"]
