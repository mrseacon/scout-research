"""Logik des Test-Wächters gegen echte Netzwerkaufrufe an die Anthropic-API (Plan 12.10: keine Live-Aufrufe in Tests).

Umhüllt für die ganze Sitzung `socket.getaddrinfo`, `socket.create_connection` und `socket.socket.connect`/`connect_ex`.
Ist der Host `anthropic.com`, `claude.ai` oder eine Subdomain davon, wird der Versuch protokolliert und
`AnthropicNetworkBlocked` geworfen. Andere Hosts laufen unverändert zur Originalfunktion durch.
"""

from __future__ import annotations

import socket

# Einzige Abschaltstelle des Wächters (Mutation "Wächter ausschalten").
GUARD_ENABLED = True

BLOCKED_DOMAINS = ("anthropic.com", "claude.ai")

# Protokoll aller blockierten Versuche; das Fallnetz in pytest_sessionfinish prüft es.
ATTEMPTS: list[str] = []


class AnthropicNetworkBlocked(BaseException):
    """Bewusst BaseException, damit `except Exception` den Versuch nicht verschluckt."""


def is_anthropic_host(host: object) -> bool:
    """Subdomain-Abgleich (nicht Teilstring); Groß-/Kleinschreibung und abschließender Punkt egal."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    if not isinstance(host, str):
        return False
    name = host.strip().lower().rstrip(".")
    return any(name == d or name.endswith("." + d) for d in BLOCKED_DOMAINS)


def _check(host: object) -> None:
    if is_anthropic_host(host):
        ATTEMPTS.append(host.decode("ascii", "ignore") if isinstance(host, bytes) else str(host))
        raise AnthropicNetworkBlocked(f"Netzwerkaufruf an {host!r} im Test blockiert")


def _address_host(address: object) -> object:
    return address[0] if isinstance(address, tuple) and address else None


# Originalfunktionen; die Wächter lesen sie beim Aufruf, damit Tests sie ersetzen können.
ORIGINALS = {
    "getaddrinfo": socket.getaddrinfo,
    "create_connection": socket.create_connection,
    "connect": socket.socket.connect,
    "connect_ex": socket.socket.connect_ex,
}


def guarded_getaddrinfo(host, *args, **kwargs):
    _check(host)
    return ORIGINALS["getaddrinfo"](host, *args, **kwargs)


def guarded_create_connection(address, *args, **kwargs):
    _check(_address_host(address))
    return ORIGINALS["create_connection"](address, *args, **kwargs)


def guarded_connect(self, address):
    _check(_address_host(address))
    return ORIGINALS["connect"](self, address)


def guarded_connect_ex(self, address):
    _check(_address_host(address))
    return ORIGINALS["connect_ex"](self, address)


if GUARD_ENABLED:
    socket.getaddrinfo = guarded_getaddrinfo
    socket.create_connection = guarded_create_connection
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
