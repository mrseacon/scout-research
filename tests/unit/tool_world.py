"""Test-Welt für die Tool-Handler (Phase 3, Schritt 5): ein `EdgarClient` auf `httpx.MockTransport`, der aus den
Slim-Fixtures (echte companyfacts) und synthetischen Firmen die SEC-Endpunkte nachbildet, plus ein Fake-Kursanbieter.
Kein Netzwerk. Kein pytest-Testmodul.

Vereinfachung: Die Frames ordnen jede Firma dem Kalenderjahr zu, das `derive_calendar_year` für ihr Periodenende
liefert — wie bei der SEC. Nur MSFT (Ende 30.06.2026) liegt damit im Frame CY2026 und nicht im Frame der Januar-Firmen;
`WorldBuilder.frame_year_override` verschiebt Firmen bei Bedarf in ein anderes Jahr.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from scout_research.data.edgar_client import EdgarClient
from scout_research.data.market_provider import PriceQuote
from scout_research.domain.metrics import REVENUE_CONCEPTS, extract_latest_annual_revenue
from scout_research.domain.periods import derive_calendar_year
from scout_research.tools.session import ToolContext

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
UA = "Scout Research (welt.tester@example.invalid)"
FINNHUB_KEY = "FAKE-FINNHUB-KEY-0123456789abcdef"
SECRETS = [UA, "welt.tester@example.invalid", FINNHUB_KEY]

#               cik            ticker  name                         sic     fye
REAL = {
    "NVDA": ("0001045810", "NVIDIA CORP", "3674", "Semiconductors & Related Devices", "0125"),
    "AAPL": ("0000320193", "Apple Inc.", "3571", "Electronic Computers", "0927"),
    "MSFT": ("0000789019", "MICROSOFT CORPORATION", "7372", "Services-Prepackaged Software", "0630"),
    "ADSK": ("0000769397", "Autodesk, Inc.", "7372", "Services-Prepackaged Software", "0131"),
    "CDNS": ("0000813672", "CADENCE DESIGN SYSTEMS, INC.", "7372", "Services-Prepackaged Software", "1231"),
}


class FakeMarket:
    """Kursanbieter ohne Netzwerk. `prices` bildet Ticker auf Kurse ab; `errors` löst Ausnahmen aus."""

    def __init__(self, prices: dict[str, float] | None = None, as_of: str = "2026-10-08") -> None:
        self.prices = dict(prices or {})
        self.as_of = as_of
        self.errors: dict[str, BaseException] = {}
        self.calls: list[str] = []

    def get_price(self, ticker: str) -> PriceQuote | None:
        self.calls.append(ticker)
        if ticker in self.errors:
            raise self.errors[ticker]
        if ticker not in self.prices:
            return None
        return PriceQuote(ticker=ticker, price=self.prices[ticker], as_of_date=self.as_of, source="fake")


class World:
    def __init__(self) -> None:
        self.tickers: list[tuple[str, str, str]] = []  # (cik, ticker, name) in Dateireihenfolge
        self.meta: dict[str, dict[str, Any]] = {}
        self.facts: dict[str, dict] = {}
        self.sic_members: dict[str, list[str]] = {}
        self.frames: dict[tuple[str, int], dict[str, tuple[float, str, str]]] = {}
        self.status_overrides: dict[str, int] = {}  # URL-Pfad-Teilstring -> Statuscode
        self.requests: list[httpx.Request] = []
        self.market = FakeMarket()

    # --- Aufbau --------------------------------------------------------------------------------------------

    def add_real(self, ticker: str) -> str:
        cik, name, sic, sic_desc, fye = REAL[ticker]
        facts = json.loads((FIXTURES / f"{ticker.lower()}_companyfacts_slim.json").read_text())
        self.facts[cik] = facts
        self._register(cik, ticker, name, sic, sic_desc, fye)
        anchor = extract_latest_annual_revenue(cik, facts)
        year = derive_calendar_year(anchor.period_end)
        self.frames.setdefault((anchor.concept, year), {})[cik] = (anchor.value, anchor.period_end, anchor.accession_number)
        return cik

    def add_synthetic(
        self, cik: str, ticker: str, name: str, sic: str, revenue: float, concept: str = REVENUE_CONCEPTS[0],
        period_end: str = "2026-01-25", year: int = 2025, in_frame: bool = True, with_ticker: bool = True,
    ) -> str:
        """Firma ohne companyfacts (404) — nur für Kandidatenlisten."""
        if with_ticker:
            self._register(cik, ticker, name, sic, "Test", "0125")
        else:
            self.meta[cik] = {"name": name, "sic": sic, "sicDescription": "Test", "fiscalYearEnd": "0125", "tickers": [], "exchanges": []}
            self.sic_members.setdefault(sic, []).append(cik)
        if in_frame:
            self.frames.setdefault((concept, year), {})[cik] = (revenue, period_end, f"{cik}-26-000001")
        return cik

    def _register(self, cik: str, ticker: str, name: str, sic: str, sic_desc: str, fye: str) -> None:
        self.tickers.append((cik, ticker, name))
        self.meta[cik] = {"name": name, "sic": sic, "sicDescription": sic_desc, "fiscalYearEnd": fye,
                          "tickers": [t for c, t, _ in self.tickers if c == cik], "exchanges": ["Nasdaq"]}
        for c, t, _ in self.tickers:
            if c == cik:
                self.meta[cik]["tickers"] = [x for cc, x, _ in self.tickers if cc == cik]
        self.sic_members.setdefault(sic, [])
        if cik not in self.sic_members[sic]:
            self.sic_members[sic].append(cik)

    # --- Transport -----------------------------------------------------------------------------------------

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        path = request.url.path
        for fragment, status in self.status_overrides.items():
            if fragment in url:
                return httpx.Response(status, text="override")

        if path.endswith("company_tickers.json"):
            return httpx.Response(200, json={
                str(i): {"cik_str": int(cik), "ticker": ticker, "title": name} for i, (cik, ticker, name) in enumerate(self.tickers)
            })
        if "/submissions/CIK" in path:
            cik = path.rsplit("CIK", 1)[1].split(".")[0]
            return httpx.Response(200, json=self.meta[cik]) if cik in self.meta else httpx.Response(404)
        if "/companyfacts/CIK" in path:
            cik = path.rsplit("CIK", 1)[1].split(".")[0]
            return httpx.Response(200, json=self.facts[cik]) if cik in self.facts else httpx.Response(404)
        if path.endswith("browse-edgar"):
            sic = request.url.params["SIC"]
            start = int(request.url.params["start"])
            members = self.sic_members.get(sic, [])[start : start + 100]
            entries = "".join(
                f"<entry><content><company-info><cik>{int(c)}</cik><sic>{sic}</sic></company-info></content></entry>"
                for c in members
            )
            return httpx.Response(200, text=f"<feed>{entries}</feed>")
        if "/frames/us-gaap/" in path:
            concept = path.split("/us-gaap/")[1].split("/")[0]
            year = int(path.rsplit("CY", 1)[1].split(".")[0])
            frame = self.frames.get((concept, year))
            if frame is None:
                return httpx.Response(404)
            return httpx.Response(200, json={"data": [
                {"cik": int(c), "entityName": c, "start": None, "end": end, "val": val, "accn": accn}
                for c, (val, end, accn) in frame.items()
            ]})
        return httpx.Response(404)

    def edgar(self) -> EdgarClient:
        return EdgarClient(user_agent=UA, transport=httpx.MockTransport(self._handler), sleep=lambda _: None)

    def context(self, **kwargs: Any) -> ToolContext:
        kwargs.setdefault("secrets_to_redact", SECRETS)
        counter = iter(range(1, 10_000))
        kwargs.setdefault("token_factory", lambda: f"{next(counter):04d}")
        return ToolContext(self.edgar(), self.market, **kwargs)


def standard_world() -> World:
    """NVDA (Ziel, SIC 3674) mit den Kandidaten AAPL und MSFT (beide im Größenbereich), dazu Rauschen."""
    w = World()
    w.add_real("NVDA")
    # Ziel- und Kandidaten-SIC: AAPL und MSFT im SIC von NVDA, damit sie als Kandidaten auftauchen
    w.sic_members.setdefault("3674", [])
    for ticker in ("AAPL", "MSFT"):
        cik = w.add_real(ticker)
        w.meta[cik]["sic"] = "3674"
        w.sic_members["3674"].append(cik)
        w.sic_members[REAL[ticker][2]] = [c for c in w.sic_members[REAL[ticker][2]] if c != cik]
    # MSFT (Ende 30.06.2026) liegt bei der SEC im Frame CY2026; die Test-Welt legt es zusätzlich in CY2025, damit es
    # neben NVDA als Kandidat erscheint (Frame-Zuordnung hier bewusst vereinfacht).
    w.frames[(REVENUE_CONCEPTS[0], 2025)][REAL["MSFT"][0]] = (331_839_000_000.0, "2026-06-30", "0001193125-26-323660")
    w.market.prices.update({"NVDA": 180.0, "AAPL": 250.0, "MSFT": 500.0})
    return w


NVDA_CIK, AAPL_CIK, MSFT_CIK = REAL["NVDA"][0], REAL["AAPL"][0], REAL["MSFT"][0]


def call(ctx: ToolContext, name: str, **args: Any) -> dict[str, Any]:
    """Dispatch, Ergebnis als Dict; bei Fehlern `{"error": {...}}`."""
    from scout_research.tools.handlers import dispatch

    return dispatch(ctx, name, args).content


def propose(ctx: ToolContext, candidate_set_id: str, ciks: tuple[str, ...] = (AAPL_CIK, MSFT_CIK), **extra: Any) -> dict[str, Any]:
    peers = [{"cik": cik, "rationale": "Ähnliches Geschäftsmodell"} for cik in ciks]
    return call(ctx, "propose_peer_set", candidate_set_id=candidate_set_id, peers=peers, **extra)


def start(w: World, user_message: str = "Mach Comps für NVDA") -> tuple[ToolContext, str]:
    """Nutzer nennt das Ziel, das Modell löst es auf und sucht Kandidaten. Gibt (ctx, candidate_set_id) zurück."""
    ctx = w.context()
    ctx.record_user_message(user_message)
    assert call(ctx, "resolve_company", query="NVDA")["status"] == "resolved"
    found = call(ctx, "find_peer_candidates", target_cik=NVDA_CIK)
    return ctx, found["candidate_set_id"]


def confirmed(w: World, ciks: tuple[str, ...] = (AAPL_CIK, MSFT_CIK)) -> tuple[ToolContext, str]:
    """Kompletter Weg bis zum bestätigten Peer-Set. Gibt (ctx, peer_set_id) zurück."""
    ctx, set_id = start(w)
    proposal = propose(ctx, set_id, ciks)
    return ctx, ctx.confirm_peer_set(proposal["proposal_id"], list(ciks)).id
