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
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

import httpx
from pydantic import BaseModel

from scout_research.data.cache import SqliteCache
from scout_research.data.http_util import parse_retry_after as _parse_retry_after
from scout_research.data.rate_limiter import RateLimiter

FINNHUB_DEFAULT_REQUESTS_PER_MINUTE = 55
"""Finnhub Free-Tier: ~60 Calls/Minute (Foundation Doc 8.3). 55 lässt Spielraum für
Netzwerk-Jitter — das Fenster gleitet, daher ist auch ein fest ausgerichtetes Minutenfenster sicher."""

MAX_RETRY_AFTER_SECONDS = 120.0
"""Verlangt der Server eine längere Pause, wird nicht blockiert: der Fehler geht an den
Aufrufer (CachedProvider -> Fallback bzw. "Marktdaten nicht verfügbar")."""

_DEFAULT_429_BACKOFF_SECONDS = 5.0
"""Wartezeit (mal Versuchsnummer), wenn ein 429 ohne verwertbaren Retry-After kommt."""


def _get_with_retry(
    client: httpx.Client,
    url: str,
    params: dict | None = None,
    max_retries: int = 2,
    rate_limiter: RateLimiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """GET mit Throttle und Retry.

    - **Jeder Versuch zählt:** `rate_limiter.acquire()` läuft vor jedem Request, auch vor
      Wiederholungen nach Netzwerkfehlern und nach 429.
    - Netzwerkfehler (`TransportError`): bis zu `max_retries` Wiederholungen mit Backoff.
    - **429:** Wartezeit aus `Retry-After` (Sekunden oder HTTP-Datum), sonst 5 s * Versuchsnummer;
      verlangt der Server mehr als `MAX_RETRY_AFTER_SECONDS`, wird sofort abgebrochen.
    - Alle anderen Statusfehler werden sofort durchgereicht (kein Retry).
    Nach ausgeschöpften Versuchen wird der letzte Fehler geworfen (`httpx.HTTPError`), den
    `CachedProvider` in die Fallback-Kaskade übersetzt.
    """
    last_error: httpx.HTTPError | None = None
    for attempt in range(max_retries + 1):
        if rate_limiter is not None:
            rate_limiter.acquire()
        try:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 or attempt == max_retries:
                raise
            wait = _parse_retry_after(exc.response.headers.get("Retry-After"))
            if wait is None:
                wait = _DEFAULT_429_BACKOFF_SECONDS * (attempt + 1)
            if wait > MAX_RETRY_AFTER_SECONDS:
                raise
            last_error = exc
            sleep(wait)
        except httpx.TransportError as exc:
            last_error = exc
            if attempt < max_retries:
                sleep(0.5 * (attempt + 1))
    raise last_error  # type: ignore[misc]


class MarketDataUnavailable(httpx.HTTPError):
    """Bereinigter Fehler eines Kursanbieters (Netz, Timeout oder HTTP-Status).

    **Sicherheit:** Der Finnhub-Key steht im Query-String der URL, und die URL steckt in jeder rohen httpx-
    Ausnahme. Dieser Fehler trägt deshalb nur Anbieter und Statuscode — keine URL, keinen Token, keine
    angehängte httpx-Ausnahme (weder `__cause__` noch `__context__`). Er bleibt eine Unterklasse von
    `httpx.HTTPError`, damit `CachedProvider` ihn wie bisher in die Fallback-Kaskade übersetzt."""

    def __init__(self, provider: str, status_code: int | None = None) -> None:
        reason = f"HTTP {status_code}" if status_code is not None else "Netzwerkfehler oder Timeout"
        super().__init__(f"{provider}-Abruf fehlgeschlagen ({reason})")
        self.provider = provider
        self.status_code = status_code


_SECRET_QUERY_PARAM = re.compile(r"(?i)\b(token|apikey|api_key)=[^&\s\"']+")


class _RedactSecretsFilter(logging.Filter):
    """httpx loggt jede Anfrage mit vollständiger URL ("HTTP Request: GET https://...?token=KEY ..."). Der Filter
    ersetzt Geheimnis-Parameter, bevor ein Handler den Eintrag sieht."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _SECRET_QUERY_PARAM.sub(r"\1=<redacted>", message)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


logging.getLogger("httpx").addFilter(_RedactSecretsFilter())


class PriceQuote(BaseModel):
    ticker: str
    price: float
    as_of_date: str
    source: str


class MarketDataProvider(Protocol):
    def get_price(self, ticker: str) -> PriceQuote | None: ...


class FinnhubProvider:
    """Primär-Provider (Foundation Doc 8.3). Benötigt FINNHUB_API_KEY.

    Jeder HTTP-Versuch läuft durch den Throttle (`requests_per_minute`, Standard 55);
    429-Antworten werden gemäß `Retry-After` abgewartet (siehe `_get_with_retry`).
    `/quote` liefert den *letzten* Kurs — während der US-Börsenzeit also intraday,
    außerhalb den letzten Schlusskurs (Foundation Doc 8.3, Abweichung zu "End-of-Day").
    """

    BASE_URL = "https://finnhub.io/api/v1/quote"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        rate_limiter: RateLimiter | None = None,
        requests_per_minute: int = FINNHUB_DEFAULT_REQUESTS_PER_MINUTE,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=10.0)
        self._rate_limiter = rate_limiter or RateLimiter(requests_per_minute, period_seconds=60.0)
        self._sleep = sleep

    def get_price(self, ticker: str) -> PriceQuote | None:
        # Fehler werden außerhalb des except-Blocks neu geworfen: so hängt weder __cause__ noch __context__
        # die rohe httpx-Ausnahme an (deren Meldung die URL mit dem Token enthält).
        failure: MarketDataUnavailable | None = None
        try:
            response = _get_with_retry(
                self._client,
                self.BASE_URL,
                params={"symbol": ticker.upper(), "token": self._api_key},
                rate_limiter=self._rate_limiter,
                sleep=self._sleep,
            )
        except httpx.HTTPStatusError as exc:
            failure = MarketDataUnavailable("Finnhub", exc.response.status_code)
        except httpx.HTTPError:
            failure = MarketDataUnavailable("Finnhub")
        if failure is not None:
            raise failure
        data = response.json()

        price = data.get("c")
        timestamp = data.get("t")
        if not price or not timestamp:
            return None

        as_of = datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
        return PriceQuote(ticker=ticker.upper(), price=float(price), as_of_date=as_of, source="finnhub")


class StooqProvider:
    """Fallback-Provider, keyless — **UNVERIFIZIERT und standardmäßig DEAKTIVIERT** (D7).

    Diese Klasse hat nie gegen echte Stooq-Daten funktioniert: der hier genutzte Endpunkt
    (`/q/l/`) antwortet (Stand 2026-10-04) mit "page does not exist", der Historien-Endpunkt
    (`/q/d/l/`) mit einer JS-Proof-of-Work-Challenge. Das CSV-Format ist angenommen, die
    Tests sind gemockt. Sie bleibt als Interface-Platzhalter erhalten, wird aber in der
    Standard-Verdrahtung (`scratch.py`) nicht mehr als Fallback eingehängt.
    """

    BASE_URL = "https://stooq.com/q/l/"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=10.0)

    def get_price(self, ticker: str) -> PriceQuote | None:
        response = _get_with_retry(
            self._client,
            self.BASE_URL,
            params={"s": f"{ticker.lower()}.us", "f": "sd2t2ohlcv", "h": "", "e": "csv"},
        )

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
