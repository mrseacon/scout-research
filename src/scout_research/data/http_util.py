"""L1 — kleine HTTP-Helfer, die EDGAR- und Marktdaten-Client gemeinsam nutzen."""

from __future__ import annotations

import email.utils
import math
from datetime import datetime, timezone
from typing import Any

import httpx


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """`Retry-After` ist laut HTTP entweder eine Zahl Sekunden oder ein HTTP-Datum."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        seconds = None
    if seconds is not None:
        return max(seconds, 0.0) if math.isfinite(seconds) else None

    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max((when - (now or datetime.now(timezone.utc))).total_seconds(), 0.0)


class RequestLog:
    """Protokoll der Upstream-Zugriffe für den Trace (Plan 6, Schritt 8).

    **Sicherheit:** Ein HTTP-Eintrag trägt nur Host, Pfad und Statuscode — nie den Query-String (Finnhub-Key im
    `token`-Parameter), nie Header (User-Agent mit Kontaktadresse) und nie den Body. Ein Cache-Eintrag trägt nur den
    Namespace und ob es ein Treffer war, nie den Schlüssel."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record_response(self, response: httpx.Response) -> None:
        """Als httpx-Event-Hook für `response` gedacht; läuft für jeden Versuch, auch für Fehlerstatus."""
        url = response.request.url
        self.entries.append({"kind": "http", "host": url.host, "path": url.path, "status": response.status_code})

    def record_cache(self, namespace: str, hit: bool) -> None:
        self.entries.append({"kind": "cache", "namespace": namespace, "hit": hit})

    def mark(self) -> int:
        """Aktuelle Länge: `since(mark)` liefert alles, was danach hinzukam."""
        return len(self.entries)

    def since(self, mark: int) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self.entries[mark:]]

    def attach(self, client: httpx.Client) -> None:
        """Hängt `record_response` an den `response`-Hook des Clients (öffentliche API `event_hooks`); vorhandene Haken
        bleiben, derselbe Haken wird nicht doppelt eingehängt."""
        hooks = client.event_hooks
        response_hooks = list(hooks.get("response", []))
        if self.record_response not in response_hooks:
            response_hooks.append(self.record_response)
            client.event_hooks = {**hooks, "response": response_hooks}
