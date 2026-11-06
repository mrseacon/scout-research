"""Synthetische EDGAR-Welt für den Demo-Modus (TA-14): ein Ziel (ZZNV) und drei Peers (ZZOR, ZZPL, ZZQN), alle mit demselben
erfundenen SIC-Code, Geschäftsjahresende 31.12. Ticker mit dem Präfix `ZZ` und Namen mit „(Demo)“ gibt es real nicht; die
Zahlen sind erfunden. Der `EdgarClient` läuft auf einem `httpx.MockTransport`; jede Anfrage wird hier beantwortet, nichts
verlässt den Prozess."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from scout_research.data.edgar_client import EdgarClient
from scout_research.data.market_provider import PriceQuote
from scout_research.domain.metrics import REVENUE_CONCEPTS
from scout_research.tools.session import ToolContext

DEMO_USER_AGENT = "Scout Research Demo (demo@example.invalid)"
PRICE_DATE = "2026-10-08"
PERIOD_END = "2025-12-31"
PREVIOUS_PERIOD_END = "2024-12-31"
CALENDAR_YEAR = 2025
SIC = "9999"
SIC_DESCRIPTION = "Demo (erfunden)"
TARGET_TICKER = "ZZNV"


@dataclass(frozen=True)
class DemoCompany:
    cik: str
    ticker: str
    name: str
    revenue: float
    revenue_previous: float
    ebit: float
    d_and_a: float
    net_income: float
    assets: float
    cash: float
    debt: float
    shares: float
    price: float


COMPANIES = (
    DemoCompany("0009000001", "ZZNV", "Nova Demo Corp (Demo)", 12.0e9, 10.0e9, 3.0e9, 0.5e9, 2.6e9, 20e9, 4.0e9, 2.0e9, 1.0e9, 150.0),
    DemoCompany("0009000002", "ZZOR", "Orbit Demo Inc (Demo)", 9.5e9, 8.6e9, 2.0e9, 0.6e9, 1.7e9, 15e9, 3.0e9, 1.5e9, 0.8e9, 110.0),
    DemoCompany("0009000003", "ZZPL", "Pulsar Demo Inc (Demo)", 15.0e9, 13.5e9, 3.6e9, 0.9e9, 3.0e9, 26e9, 5.0e9, 3.0e9, 1.2e9, 120.0),
    DemoCompany("0009000004", "ZZQN", "Quanta Demo Ltd (Demo)", 6.0e9, 5.1e9, 1.1e9, 0.4e9, 0.9e9, 9e9, 1.2e9, 1.0e9, 0.5e9, 90.0),
)
TARGET = COMPANIES[0]


@dataclass(frozen=True)
class DemoSession:
    """Kennzeichnet die Sitzung als Demo (wie `eval_session` für die Evaluation): Der Trace leitet `run_mode: "demo"` aus
    `ToolContext.demo_session` ab, es wird nie ein Modus übergeben (SG-19). Schaltet nichts frei."""


class _NoLimit:
    """Rate Limiter ohne Wartezeit: Die Demo-Welt hat kein Netzwerk, das man schonen müsste."""

    def acquire(self) -> None:
        pass


class DemoMarket:
    """Kursanbieter ohne Netzwerk mit festen, erfundenen Kursen."""

    def get_price(self, ticker: str) -> PriceQuote | None:
        for company in COMPANIES:
            if company.ticker == ticker:
                return PriceQuote(ticker=ticker, price=company.price, as_of_date=PRICE_DATE, source="demo")
        return None


def _entry(val: float, end: str, accn: str, filed: str, fy: int, start: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": "FY", "form": "10-K", "filed": filed}
    if start is not None:
        entry["start"] = start
    return entry


def company_facts(c: DemoCompany) -> dict[str, Any]:
    """Minimale companyfacts im Format der SEC: Umsatz zweier Jahre, dazu die Kennzahlen des jüngsten Jahres."""
    accn, accn_previous = f"{c.cik}-26-000001", f"{c.cik}-25-000001"

    def flow(val: float) -> list[dict[str, Any]]:
        return [_entry(val, PERIOD_END, accn, "2026-02-20", 2025, start="2025-01-01")]

    def instant(val: float) -> list[dict[str, Any]]:
        return [_entry(val, PERIOD_END, accn, "2026-02-20", 2025)]

    revenue = [_entry(c.revenue_previous, PREVIOUS_PERIOD_END, accn_previous, "2025-02-20", 2024, start="2024-01-01"),
               *flow(c.revenue)]
    return {
        "cik": int(c.cik), "entityName": c.name,
        "facts": {
            "us-gaap": {
                REVENUE_CONCEPTS[0]: {"units": {"USD": revenue}},
                "OperatingIncomeLoss": {"units": {"USD": flow(c.ebit)}},
                "NetIncomeLoss": {"units": {"USD": flow(c.net_income)}},
                "DepreciationDepletionAndAmortization": {"units": {"USD": flow(c.d_and_a)}},
                "Assets": {"units": {"USD": instant(c.assets)}},
                "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": instant(c.cash)}},
                "LongTermDebt": {"units": {"USD": instant(c.debt)}},
            },
            "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
                _entry(c.shares, "2026-02-10", accn, "2026-02-20", 2025)]}}},
        },
    }


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    by_cik = {c.cik: c for c in COMPANIES}
    if path.endswith("company_tickers.json"):
        return httpx.Response(200, json={str(i): {"cik_str": int(c.cik), "ticker": c.ticker, "title": c.name}
                                         for i, c in enumerate(COMPANIES)})
    if "/submissions/CIK" in path:
        company = by_cik.get(path.rsplit("CIK", 1)[1].split(".")[0])
        if company is None:
            return httpx.Response(404)
        return httpx.Response(200, json={"name": company.name, "sic": SIC, "sicDescription": SIC_DESCRIPTION,
                                         "fiscalYearEnd": "1231", "tickers": [company.ticker], "exchanges": ["Nasdaq"]})
    if "/companyfacts/CIK" in path:
        company = by_cik.get(path.rsplit("CIK", 1)[1].split(".")[0])
        return httpx.Response(200, json=company_facts(company)) if company else httpx.Response(404)
    if path.endswith("browse-edgar"):
        members = [c for c in COMPANIES if request.url.params.get("SIC") == SIC and int(request.url.params.get("start", 0)) == 0]
        entries = "".join(f"<entry><content><company-info><cik>{int(c.cik)}</cik><sic>{SIC}</sic></company-info></content></entry>"
                          for c in members)
        return httpx.Response(200, text=f"<feed>{entries}</feed>")
    if "/frames/us-gaap/" in path:
        concept = path.split("/us-gaap/")[1].split("/")[0]
        year = int(path.rsplit("CY", 1)[1].split(".")[0])
        if concept != REVENUE_CONCEPTS[0] or year != CALENDAR_YEAR:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": [
            {"cik": int(c.cik), "entityName": c.name, "start": None, "end": PERIOD_END, "val": c.revenue,
             "accn": f"{c.cik}-26-000001"} for c in COMPANIES]})
    return httpx.Response(404)


def build_context(language: str = "de") -> ToolContext:
    """Ein `ToolContext` auf der Demo-Welt. Der Rate Limiter wartet nie, der Transport ist der `MockTransport`."""
    edgar = EdgarClient(user_agent=DEMO_USER_AGENT, rate_limiter=_NoLimit(),  # type: ignore[arg-type]
                        transport=httpx.MockTransport(_handler), sleep=lambda _: None)
    return ToolContext(edgar, DemoMarket(), output_language=language, demo_session=DemoSession())  # type: ignore[arg-type]
