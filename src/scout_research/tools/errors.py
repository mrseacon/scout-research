"""L3 — Fehlerformat für das Modell (Phase-3-Plan, Abschnitt 2 „Fehlerformat“, Review F16).

Jeder Fehler ist ein `ToolError` mit `code`, `message`, `retryable`, `details` und `hint`. Texte entstehen aus einer
**Vorlage je Code** mit freigegebenen Detailfeldern — nie aus `str(exc)`. Ausnahmetexte, Endpunkt-Pfade und
Stacktraces gehen nur in den Trace (und dort durch den `Redactor`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field

from scout_research.data.edgar_client import (
    EdgarDataNotFound,
    EdgarError,
    EdgarHttpError,
    EdgarRateLimited,
    EdgarUnavailable,
)
from scout_research.domain.metrics import RevenueNotFoundError
from scout_research.domain.peers import FrameYearUnresolved
from scout_research.domain.periods import HistoricalValuationNotSupported, PeriodNotAvailable
from scout_research.tools.sanitize import clean_value


@dataclass(frozen=True)
class ErrorSpec:
    retryable: bool
    message: str
    hint: str


ERROR_SPECS: dict[str, ErrorSpec] = {
    "INVALID_ARGUMENTS": ErrorSpec(False, "Die Eingabe ist ungültig.", "Prüfe die Felder in details.problems und rufe das Tool korrigiert auf."),
    "UNKNOWN_HANDLE": ErrorSpec(False, "Das Handle ist unbekannt oder hat den falschen Typ.", "Verwende ein Handle, das ein Tool in dieser Sitzung geliefert hat."),
    "PERIOD_NOT_AVAILABLE": ErrorSpec(False, "Für {ticker} gibt es kein 10-K für {requested}.", "Wähle eines der verfügbaren Periodenenden oder lass period weg."),
    "HISTORICAL_VALUATION_NOT_SUPPORTED": ErrorSpec(False, "Peer-Suche und Multiples gibt es nur für das jüngste Geschäftsjahr.", "Lass period weg. Historische Zahlen, Margen und Wachstum liefert get_financials."),
    "TARGET_REVENUE_NOT_FOUND": ErrorSpec(False, "Für das Unternehmen wurde kein Umsatz in den 10-K-Daten gefunden.", "Das Unternehmen kann so nicht analysiert werden; sag das dem Nutzer."),
    "PEER_SEARCH_NOT_POSSIBLE": ErrorSpec(False, "Für dieses Ziel ist keine Peer-Suche möglich ({reason}).", "Erkläre dem Nutzer, dass die Peer-Suche für dieses Unternehmen nicht möglich ist."),
    "NO_VALID_PEERS": ErrorSpec(False, "Für keinen Peer des bestätigten Sets konnten Kennzahlen berechnet werden.", "Erkläre dem Nutzer, welche Peers fehlen (details.skipped_peers); schlage ein neues Peer-Set vor."),
    "PEER_SET_NOT_CONFIRMED": ErrorSpec(False, "Dieser Vorschlag ist noch nicht vom Menschen bestätigt.", "Warte auf die Host-Nachricht mit der peer_set_id; Bestätigen kann nur der Mensch."),
    "NOT_IN_ALLOWLIST": ErrorSpec(False, "Für dieses Unternehmen sind keine Einzeldaten freigegeben.", "Freigegeben sind nur bestätigte Peers und Firmen, die der Nutzer selbst genannt hat (Name oder Ticker wörtlich, per resolve_company aufgelöst). Frage den Nutzer."),
    "PEER_NOT_IN_CANDIDATES": ErrorSpec(False, "Mindestens ein Peer steht nicht in der Kandidatenliste.", "Wähle Peers ausschließlich aus candidates. Nutzerwünsche außerhalb der Liste gehören in user_requested_additions."),
    "USER_ADDITION_NOT_IN_MESSAGES": ErrorSpec(False, "Die genannte Firma steht nicht wörtlich in einer Nutzernachricht.", "Ergänze nur Firmen, die der Nutzer selbst genannt hat; Name oder Ticker unverändert übernehmen. Sonst frage den Nutzer."),
    "SLOT_UNKNOWN": ErrorSpec(False, "Ein Slot ist unbekannt oder falsch aufgebaut.", "Prüfe die Slot-Grammatik in der Tool-Beschreibung und die Ticker und Felder der Tabelle."),
    "SLOT_VALUE_UNAVAILABLE": ErrorSpec(False, "Ein Slot zeigt auf einen nicht verfügbaren Wert.", "Schreibe stattdessen „nicht verfügbar“ mit dem Grund aus der Tabelle."),
    "NAKED_NUMBER": ErrorSpec(False, "Der Text enthält eine ausgeschriebene Zahl.", "Schreibe keine Zahlen aus; verwende Slots (Kommentar) bzw. lass die Zahl weg (Begründungen)."),
    "COMMENTARY_WITHHELD": ErrorSpec(False, "Der Kommentar wurde zurückgehalten: auch nach zwei Korrekturen enthielt er Zahlen oder ungültige Slots.", "Schreibe keinen weiteren Kommentar zu dieser Tabelle; der Nutzer sieht stattdessen die Tabelle und die Warnungen."),
    "FRAME_YEAR_UNRESOLVED": ErrorSpec(False, "Das Ziel taucht in keinem Vergleichsjahr der SEC-Daten auf; ein Größenvergleich ist nicht möglich.", "Erkläre dem Nutzer, dass die Peer-Suche für dieses Ziel nicht zuverlässig möglich ist."),
    "DATA_NOT_FOUND": ErrorSpec(False, "Die SEC hat für diese Anfrage keine Daten.", "Erkläre dem Nutzer, dass für das Unternehmen keine Daten vorliegen."),
    "UPSTREAM_UNAVAILABLE": ErrorSpec(True, "Die Datenquelle {source} ist gerade nicht erreichbar.", "Du darfst den Aufruf einmal wiederholen; sonst erkläre dem Nutzer, dass die Quelle nicht erreichbar ist."),
    "UPSTREAM_RATE_LIMITED": ErrorSpec(False, "Die Datenquelle {source} hat die Anfrage wegen eines Abruflimits abgelehnt.", "Wiederhole den Aufruf nicht; erkläre dem Nutzer, dass er später erneut versuchen soll."),
    "TOOL_TIMEOUT": ErrorSpec(True, "Das Zeitbudget des Tools ist überschritten.", "Du darfst den Aufruf einmal wiederholen; sonst erkläre dem Nutzer die Verzögerung."),
    "LOOP_GUARD": ErrorSpec(False, "Dieser identische Aufruf ist bereits fehlgeschlagen.", "Wiederhole ihn nicht; ändere die Eingabe oder erkläre dem Nutzer, was fehlt."),
    "UNKNOWN_TOOL": ErrorSpec(False, "Dieses Tool gibt es nicht.", "Verwende nur Tools aus der Tool-Liste. Eine Bestätigung des Peer-Sets ist kein Tool, sondern Sache des Menschen."),
    "AWAITING_PEER_CONFIRMATION": ErrorSpec(False, "Das Peer-Set wartet auf die Bestätigung durch den Menschen; bis dahin werden keine weiteren Tools ausgeführt.", "Beende deinen Zug mit einer kurzen Einleitung zum Vorschlag."),
    "INTERNAL_ERROR": ErrorSpec(False, "Es ist ein interner Fehler aufgetreten.", "Erkläre dem Nutzer, dass die Anfrage technisch nicht beantwortet werden konnte; gib keine technischen Details weiter."),
}

CODES = frozenset(ERROR_SPECS)
"""Alle Fehlercodes des Tool-Vertrags."""

RETRYABLE = frozenset(code for code, spec in ERROR_SPECS.items() if spec.retryable)


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "?"


class ToolError(BaseModel):
    """Der Inhalt eines Fehler-`tool_result` (`{"error": {...}}`)."""

    code: str
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)
    hint: str

    def to_content(self) -> dict[str, Any]:
        return {"error": self.model_dump(mode="json")}


def make_error(code: str, hint: str | None = None, **details: Any) -> ToolError:
    """Baut einen Fehler aus der Vorlage des Codes. `details` werden bereinigt (Länge, Steuerzeichen, `[[`) und
    füllen Platzhalter in der Meldung; `hint` überschreibt nur bei Bedarf den Standardhinweis."""
    spec = ERROR_SPECS[code]
    clean = {k: clean_value(v) for k, v in details.items() if v is not None}
    message = spec.message.format_map(_Safe({k: v for k, v in clean.items() if isinstance(v, (str, int, float))}))
    return ToolError(code=code, message=message, retryable=spec.retryable, details=clean, hint=hint or spec.hint)


class ToolFailure(Exception):
    """Wird in Handlern geworfen, um einen bestimmten `ToolError` zurückzugeben."""

    def __init__(self, error: ToolError) -> None:
        super().__init__(error.code)
        self.error = error


def fail(code: str, hint: str | None = None, **details: Any) -> ToolFailure:
    return ToolFailure(make_error(code, hint, **details))


def _requested_label(exc: PeriodNotAvailable) -> str:
    requested = exc.requested
    return f"Periodenende {requested.period_end}" if requested.period_end is not None else f"Fiscal Year {requested.fiscal_year}"


def map_exception(exc: BaseException, ticker: str | None = None) -> ToolError:
    """Bildet L1-/L2-Ausnahmen auf Tool-Fehler ab (Plan: „L1-Fehlerklassen und Mapping“). Alles Unbekannte wird
    `INTERNAL_ERROR` ohne Ausnahmetext."""
    if isinstance(exc, ToolFailure):
        return exc.error
    if isinstance(exc, PeriodNotAvailable):
        return make_error("PERIOD_NOT_AVAILABLE", ticker=ticker or exc.cik, requested=_requested_label(exc),
                          available_period_ends=exc.available_period_ends)
    if isinstance(exc, HistoricalValuationNotSupported):
        return make_error("HISTORICAL_VALUATION_NOT_SUPPORTED", tickers=exc.tickers)
    if isinstance(exc, FrameYearUnresolved):
        return make_error("FRAME_YEAR_UNRESOLVED", period_end=exc.period_end, tried_years=exc.tried_years)
    if isinstance(exc, RevenueNotFoundError):
        return make_error("TARGET_REVENUE_NOT_FOUND")
    if isinstance(exc, EdgarUnavailable):
        return make_error("UPSTREAM_UNAVAILABLE", source="SEC EDGAR")
    if isinstance(exc, EdgarRateLimited):
        return make_error("UPSTREAM_RATE_LIMITED", source="SEC EDGAR", retry_after_seconds=exc.retry_after_seconds)
    if isinstance(exc, EdgarDataNotFound):
        return make_error("DATA_NOT_FOUND")
    if isinstance(exc, (EdgarHttpError, EdgarError)):
        return make_error("INTERNAL_ERROR")
    if isinstance(exc, httpx.HTTPError):
        return make_error("UPSTREAM_UNAVAILABLE", source="Kursanbieter")
    return make_error("INTERNAL_ERROR")
