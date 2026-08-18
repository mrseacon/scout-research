"""L1 — Data Access: Client für SEC EDGAR.

Regeln (siehe Foundation Doc, Abschnitt 8.2):
- Jeder Request trägt einen aussagekräftigen User-Agent mit Kontakt-Email.
- Max. 10 Requests/Sekunde (SEC Fair Access Policy) → RateLimiter zwingend.
- Dieser Client liefert ausschließlich rohe/strukturierte Daten. Kein LLM-Zugriff auf L1
  (siehe Architektur-Regel Abschnitt 7.1) — nur über L3-Tools.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from scout_research.data.rate_limiter import RateLimiter

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


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
        timeout: float = 10.0,
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

    def _get_json(self, url: str) -> dict:
        self._rate_limiter.acquire()
        response = self._client.get(url)
        response.raise_for_status()
        return response.json()

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
