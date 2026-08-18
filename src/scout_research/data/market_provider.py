"""L1 — Marktdaten-Provider. Siehe Foundation Doc 8.3 (Entscheidung D1) und 9.

Kapselung hinter einem einheitlichen Interface ist nicht optional: ein Provider-Wechsel darf
genau eine Datei betreffen. `CachedProvider` prüft zuerst den Cache, dann den Primär-Provider,
und fällt bei Fehler/429 automatisch auf den Fallback-Provider zurück. Schlagen beide fehl,
wird `None` zurückgegeben und eine QualityWarning erzeugt (Phase 2) — es wird **niemals**
ein Kurs geschätzt.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Protocol

import httpx
from pydantic import BaseModel

from scout_research.data.cache import SqliteCache


class PriceQuote(BaseModel):
    ticker: str
    price: float
    as_of_date: str
    source: str


class MarketDataProvider(Protocol):
    def get_price(self, ticker: str) -> PriceQuote | None: ...


class FinnhubProvider:
    """Primär-Provider (Foundation Doc 8.3). Benötigt FINNHUB_API_KEY."""

    BASE_URL = "https://finnhub.io/api/v1/quote"

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=10.0)

    def get_price(self, ticker: str) -> PriceQuote | None:
        response = self._client.get(
            self.BASE_URL, params={"symbol": ticker.upper(), "token": self._api_key}
        )
        response.raise_for_status()
        data = response.json()

        price = data.get("c")
        timestamp = data.get("t")
        if not price or not timestamp:
            return None

        as_of = datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
        return PriceQuote(ticker=ticker.upper(), price=float(price), as_of_date=as_of, source="finnhub")


class StooqProvider:
    """Fallback-Provider, keyless (Foundation Doc 8.3).

    Stand 2026-08: Stooq schützt seine öffentlichen Endpunkte inzwischen mit einer
    JS-basierten Proof-of-Work-Challenge — einfache HTTP-Requests werden abgewiesen.
    Das Interface bleibt bewusst so implementiert, wie es funktionieren *sollte*; ein
    funktionierender Fallback ist eine offene Entscheidung (vgl. Risikotabelle Abschnitt 13).
    """

    BASE_URL = "https://stooq.com/q/l/"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=10.0)

    def get_price(self, ticker: str) -> PriceQuote | None:
        response = self._client.get(
            self.BASE_URL,
            params={"s": f"{ticker.lower()}.us", "f": "sd2t2ohlcv", "h": "", "e": "csv"},
        )
        response.raise_for_status()

        reader = csv.DictReader(io.StringIO(response.text))
        row = next(reader, None)
        if not row or row.get("Close") in (None, "N/D"):
            return None

        return PriceQuote(
            ticker=ticker.upper(),
            price=float(row["Close"]),
            as_of_date=row["Date"],
            source="stooq",
        )


class CachedProvider:
    """Deckt Cache-Lookup + Fallback-Kaskade in einem Provider-Interface ab."""

    def __init__(
        self,
        primary: MarketDataProvider,
        fallback: MarketDataProvider | None,
        cache: SqliteCache,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._cache = cache

    def get_price(self, ticker: str) -> PriceQuote | None:
        today = datetime.now(timezone.utc).date().isoformat()
        cache_key = f"{ticker.upper()}:{today}"

        cached = self._cache.get("market_price", cache_key)
        if cached is not None:
            return PriceQuote.model_validate(cached)

        quote = self._fetch_with_fallback(ticker)
        if quote is not None:
            self._cache.set("market_price", cache_key, quote.model_dump())
        return quote

    def _fetch_with_fallback(self, ticker: str) -> PriceQuote | None:
        try:
            quote = self._primary.get_price(ticker)
            if quote is not None:
                return quote
        except httpx.HTTPError:
            pass

        if self._fallback is None:
            return None

        try:
            return self._fallback.get_price(ticker)
        except httpx.HTTPError:
            return None
