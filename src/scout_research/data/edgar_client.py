"""L1 — Data Access: Client für SEC EDGAR.

Regeln (siehe Foundation Doc, Abschnitt 8.2):
- Jeder Request trägt einen aussagekräftigen User-Agent mit Kontakt-Email.
- Max. 10 Requests/Sekunde (SEC Fair Access Policy) → RateLimiter zwingend.
- Dieser Client liefert ausschließlich rohe/strukturierte Daten. Kein LLM-Zugriff auf L1
  (siehe Architektur-Regel Abschnitt 7.1) — nur über L3-Tools.
"""

from __future__ import annotations

import re
import time

import httpx
from pydantic import BaseModel

from scout_research.data.rate_limiter import RateLimiter

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
BROWSE_EDGAR_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/{taxonomy}/{concept}/{unit}/CY{year}.json"


class CompanyLookup(BaseModel):
    cik: str
    ticker: str
    name: str


class CompanyMetadata(BaseModel):
    cik: str
    name: str
    sic_code: str | None
    sic_description: str | None
    fiscal_year_end: str | None
    tickers: list[str]
    exchanges: list[str]


class SicCandidate(BaseModel):
    """Ergebnis der SIC-Suche über browse-edgar. Nur CIK + SIC — der Firmenname im
    Atom-Feed dieses Endpunkts ist wegen eines langjährigen SEC-seitigen Bugs unbrauchbar
    (liefert "ARRAY(0x...)"). Name/Ticker werden vom Aufrufer über die Ticker-Map
    kreuzreferenziert (siehe `domain/peers.py`)."""

    cik: str
    sic_code: str


class FrameEntry(BaseModel):
    """Ein Wert aus dem `frames`-Endpunkt: ein XBRL-Konzept für ein Unternehmen in einer
    Periode. Liefert Fakten für tausende Unternehmen in einem einzigen Request — genutzt für
    den Größenfilter in der Peer-Suche, nicht für die finalen Comps-Zahlen (dafür weiterhin
    die präzise Extraktion aus companyfacts, siehe `domain/metrics.py`)."""

    cik: str
    entity_name: str
    value: float
    period_start: str | None
    period_end: str
    accession_number: str


class TickerNotFoundError(Exception):
    pass


def pad_cik(cik: str | int) -> str:
    """SEC-Endpunkte erwarten eine 10-stellige, nullgepolsterte CIK."""
    return str(cik).zfill(10)


class EdgarClient:
    def __init__(
        self,
        user_agent: str,
        rate_limiter: RateLimiter | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._rate_limiter = rate_limiter or RateLimiter(max_requests=10, period_seconds=1.0)
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            transport=transport,
            timeout=timeout,
        )
        self._ticker_map_cache: dict[str, dict] | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "EdgarClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _get(self, url: str, max_retries: int = 2) -> httpx.Response:
        """`browse-edgar` (legacy CGI) reagiert gelegentlich mit Timeouts, obwohl der
        Endpunkt selbst verfügbar ist — ein einfacher Retry mit Backoff behebt das, ohne
        die Rate-Limit-Logik zu verändern (jeder Versuch respektiert weiterhin 10 req/s)."""
        last_error: httpx.TransportError | None = None
        for attempt in range(max_retries + 1):
            self._rate_limiter.acquire()
            try:
                response = self._client.get(url)
                response.raise_for_status()
                return response
            except httpx.TransportError as exc:
                last_error = exc
                if attempt < max_retries:
                    time.sleep(0.5 * (attempt + 1))
        raise last_error  # type: ignore[misc]

    def _get_json(self, url: str) -> dict:
        return self._get(url).json()

    def _load_ticker_map(self) -> dict[str, dict]:
        if self._ticker_map_cache is None:
            raw = self._get_json(TICKERS_URL)
            self._ticker_map_cache = {
                entry["ticker"].upper(): entry for entry in raw.values()
            }
        return self._ticker_map_cache

    def resolve_cik(self, ticker: str) -> CompanyLookup:
        """Ticker → CIK, Name. Wirft TickerNotFoundError bei keinem Treffer.

        v1 löst nur exakte Ticker auf; Name-Fuzzy-Matching und Mehrdeutigkeits-
        Behandlung folgt in Phase 3 (siehe Foundation Doc 7.3, `resolve_company`-Tool).
        """
        ticker_map = self._load_ticker_map()
        entry = ticker_map.get(ticker.upper())
        if entry is None:
            raise TickerNotFoundError(f"Kein Unternehmen für Ticker '{ticker}' gefunden.")
        return CompanyLookup(
            cik=pad_cik(entry["cik_str"]),
            ticker=entry["ticker"],
            name=entry["title"],
        )

    def get_cik_to_ticker_map(self) -> dict[str, tuple[str, str]]:
        """CIK → (Ticker, Name), abgeleitet aus derselben (gecachten) Ticker-Map wie
        `resolve_cik`. Genutzt für die Kreuzreferenzierung von SIC-Suchergebnissen, deren
        Atom-Feed keine brauchbaren Firmennamen liefert (siehe `SicCandidate`)."""
        ticker_map = self._load_ticker_map()
        return {pad_cik(entry["cik_str"]): (entry["ticker"], entry["title"]) for entry in ticker_map.values()}

    def get_company_metadata(self, cik: str) -> CompanyMetadata:
        data = self._get_json(SUBMISSIONS_URL.format(cik=pad_cik(cik)))
        return CompanyMetadata(
            cik=pad_cik(cik),
            name=data.get("name", ""),
            sic_code=data.get("sic"),
            sic_description=data.get("sicDescription"),
            fiscal_year_end=data.get("fiscalYearEnd"),
            tickers=data.get("tickers", []),
            exchanges=data.get("exchanges", []),
        )

    def get_company_facts(self, cik: str) -> dict:
        """Rohe XBRL companyfacts-Struktur. Parsing/Normalisierung passiert in L2."""
        return self._get_json(COMPANYFACTS_URL.format(cik=pad_cik(cik)))

    def _get_text(self, url: str) -> str:
        return self._get(url).text

    def search_companies_by_sic(
        self, sic_code: str, form_type: str = "10-K", max_pages: int = 10
    ) -> list[SicCandidate]:
        """Alle Unternehmen mit gegebenem SIC-Code, die aktuell `form_type`-Filings
        einreichen (Foundation Doc 7.4, Schritt 1). Paginiert über `browse-edgar`
        (100 Treffer/Seite). Der Atom-Feed liefert keine brauchbaren Firmennamen —
        siehe `SicCandidate`-Docstring.
        """
        candidates: list[SicCandidate] = []
        page_size = 100

        for page in range(max_pages):
            start = page * page_size
            url = (
                f"{BROWSE_EDGAR_URL}?action=getcompany&SIC={sic_code}&type={form_type}"
                f"&dateb=&owner=include&count={page_size}&start={start}&output=atom"
            )
            body = self._get_text(url)
            entries = re.findall(r"<entry[^>]*>.*?</entry>", body, re.DOTALL)
            if not entries:
                break

            for entry in entries:
                cik_match = re.search(r"<cik>(\d+)</cik>", entry)
                sic_match = re.search(r"<sic>(\d+)</sic>", entry)
                if cik_match and sic_match:
                    candidates.append(
                        SicCandidate(cik=pad_cik(cik_match.group(1)), sic_code=sic_match.group(1))
                    )

            if len(entries) < page_size:
                break

        return candidates

    def get_revenue_frame(
        self, concept: str, calendar_year: int, taxonomy: str = "us-gaap", unit: str = "USD"
    ) -> dict[str, FrameEntry]:
        """Ein XBRL-Konzept über *alle* Filer eines Kalenderjahres in einem Request
        (Foundation Doc 8.2 — "besonders wertvoll für Comps"). Rückgabe als CIK → FrameEntry.
        """
        data = self._get_json(
            FRAMES_URL.format(taxonomy=taxonomy, concept=concept, unit=unit, year=calendar_year)
        )
        result: dict[str, FrameEntry] = {}
        for entry in data.get("data", []):
            cik = pad_cik(entry["cik"])
            result[cik] = FrameEntry(
                cik=cik,
                entity_name=entry.get("entityName", ""),
                value=float(entry["val"]),
                period_start=entry.get("start"),
                period_end=entry["end"],
                accession_number=entry.get("accn", ""),
            )
        return result
