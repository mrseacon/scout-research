"""L3 — Bereinigung externer Strings und Schutz vor Secrets in Tool-Ausgaben (Phase-3-Plan, R7/F3/F5).

Firmennamen, SIC-Beschreibungen und Konzeptnamen stammen aus Filings und von Kursanbietern. Bevor sie an das Modell
gehen, werden Steuerzeichen entfernt, die Länge begrenzt und die Slot-Begrenzer `[[`/`]]` maskiert — sonst könnte ein
Firmenname einen Slot einschleusen.
"""

from __future__ import annotations

import re
import unicodedata

TICKER_PATTERN = re.compile(r"^[A-Z0-9.\-]{1,10}$")
_SECRET_QUERY = re.compile(r"(?i)\b(token|apikey|api_key|key)=[^&\s\"']+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")


def is_valid_ticker(ticker: object) -> bool:
    """Ticker aus der SEC-Map werden nur ausgegeben, wenn sie diesem Muster entsprechen (F3)."""
    return isinstance(ticker, str) and TICKER_PATTERN.fullmatch(ticker) is not None


def clean_text(value: object, max_len: int = 100) -> str:
    """Bereinigt einen externen String für die Ausgabe an das Modell: NFKC, keine Steuerzeichen, einfache
    Leerzeichen, `[[`/`]]` maskiert, auf `max_len` Zeichen gekürzt."""
    text = unicodedata.normalize("NFKC", str(value))
    text = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\[(?=\[)", "[ ", text)
    text = re.sub(r"\](?=\])", "] ", text)
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def clean_value(value: object, max_len: int = 200) -> object:
    """Wie `clean_text`, rekursiv über Listen und Dictionaries; Zahlen, Wahrheitswerte und `None` bleiben."""
    if isinstance(value, str):
        return clean_text(value, max_len)
    if isinstance(value, dict):
        return {clean_text(k, 60): clean_value(v, max_len) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_value(v, max_len) for v in value]
    return value


class Redactor:
    """Schwärzt Secrets in Texten, die in einen Trace oder ein Log gehen (Stacktraces, Ausnahmetexte).

    Bekannte Secrets (API-Keys, User-Agent, Kontaktadresse) werden wörtlich ersetzt; zusätzlich werden
    `token=`/`apikey=`-Parameter und Bearer-Token erkannt."""

    def __init__(self, secrets: list[str] | tuple[str, ...] = ()) -> None:
        self._secrets: list[str] = []
        for secret in secrets:
            self.add_secret(secret)

    def add_secret(self, secret: str | None) -> None:
        if secret and len(secret) >= 4 and secret not in self._secrets:
            self._secrets.append(secret)
            self._secrets.sort(key=len, reverse=True)  # längere zuerst: der User-Agent vor seiner E-Mail

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "<redacted>")
        text = _SECRET_QUERY.sub(r"\1=<redacted>", text)
        return _BEARER.sub("Bearer <redacted>", text)
