"""Feste Texte der Agent-Schleife (Plan 12.4, 12.5, 12.14.4, 12.14.5).

Alles hier ist **Konstante oder Vorlage des Codes**: nie aus Eingaben, Tool-Ergebnissen oder Modelltext
zusammengesetzt (SM-02). Texte, die der Nutzer sieht, sind zahlenfrei und per Test geprüft (U-F1, SM-05); die
Vorlagen der Host-Züge enthalten keine Ziffern.
"""

from __future__ import annotations

import re
from typing import Any

# --- Was der Nutzer sieht ---------------------------------------------------------------------------------------

USER_TEXTS: dict[str, dict[str, str]] = {
    "de": {
        "withheld": "Antwort zurückgehalten: Sie enthielt Zahlen, die sich nicht prüfen ließen.",
        "failed": "Scout ist in diesem Schritt nicht weitergekommen.",
        "intro": "Ich habe einen Peer-Vorschlag erstellt. Bitte prüfen und bestätigen, anpassen oder verwerfen.",
        "refusal": "Das Modell hat diese Anfrage abgelehnt.",
        "unavailable": "Der Modelldienst ist gerade nicht erreichbar. Bitte später erneut versuchen.",
        "auth": "API-Schlüssel oder Berechtigung prüfen.",
        "internal": "Interner Fehler: Die Anfrage konnte technisch nicht verarbeitet werden.",
        "too_long": "Die Antwort wurde verworfen, weil sie zu lang wurde. Bitte die Anfrage enger fassen.",
        "unexpected": "Die Antwort des Modells war unerwartet und wurde verworfen.",
        "cost_warning": "Hinweis: Die Kosten dieser Sitzung nähern sich dem Limit.",
        "cost_closed": "Das Kostenlimit dieser Sitzung ist erreicht. Bitte eine neue Sitzung starten.",
        "context_closed": "Die Sitzung ist zu lang geworden. Bitte eine neue Sitzung starten.",
        "interrupted": "Abgebrochen.",
        "status_working": "Scout arbeitet …",
    },
    "en": {
        "withheld": "Answer withheld: it contained numbers that could not be verified.",
        "failed": "Scout could not make progress in this step.",
        "intro": "I have prepared a peer proposal. Please review it and confirm, adjust or reject it.",
        "refusal": "The model declined this request.",
        "unavailable": "The model service is currently unavailable. Please try again later.",
        "auth": "Please check the API key or permissions.",
        "internal": "Internal error: the request could not be processed technically.",
        "too_long": "The answer was discarded because it became too long. Please narrow the request.",
        "unexpected": "The model's answer was unexpected and was discarded.",
        "cost_warning": "Note: the cost of this session is approaching its limit.",
        "cost_closed": "The cost limit of this session has been reached. Please start a new session.",
        "context_closed": "The session has become too long. Please start a new session.",
        "interrupted": "Interrupted.",
        "status_working": "Scout is working …",
    },
}

# Statuszeile je Tool (H3): der Host sieht nie den Tool-Namen aus der Modellantwort; unbekannte Namen bekommen
# `status_working`. Die Schlüssel sind genau die Tools aus `tools.handlers.TOOLS` (Test).
TOOL_STATUS: dict[str, dict[str, str]] = {
    "de": {
        "resolve_company": "Scout sucht das Unternehmen …",
        "find_peer_candidates": "Scout sucht Vergleichsunternehmen …",
        "propose_peer_set": "Scout stellt einen Peer-Vorschlag zusammen …",
        "compute_comps_table": "Scout berechnet die Vergleichstabelle …",
        "submit_commentary": "Scout prüft den Kommentar …",
        "get_financials": "Scout lädt Finanzdaten …",
        "get_market_data": "Scout lädt Marktdaten …",
    },
    "en": {
        "resolve_company": "Scout is looking up the company …",
        "find_peer_candidates": "Scout is searching for comparable companies …",
        "propose_peer_set": "Scout is preparing a peer proposal …",
        "compute_comps_table": "Scout is computing the comparison table …",
        "submit_commentary": "Scout is checking the commentary …",
        "get_financials": "Scout is loading financial data …",
        "get_market_data": "Scout is loading market data …",
    },
}

# --- Was das Modell sieht (Host-Züge, V1) -----------------------------------------------------------------------

HOST_TEMPLATES: dict[str, dict[str, str]] = {
    "de": {
        "confirmed": "Ich habe den Peer-Vorschlag bestätigt.",
        "rejected": "Ich lehne den Peer-Vorschlag ab.",
        "correction": "(Anwendung) Die letzte Antwort wurde nicht angezeigt.",
    },
    "en": {
        "confirmed": "I have confirmed the peer proposal.",
        "rejected": "I reject the peer proposal.",
        "correction": "(Application) The last answer was not displayed.",
    },
}

# Betreiber-Fakten (system-Nachricht). Handles und Ticker sind vom Code geprüft; kein Freitext.
FACT_CONFIRMED = "Peer-Set bestätigt: peer_set_id={peer_set_id}; entfernt: {removed}; ergänzt: {added}."
FACT_REJECTED = "Vorschlag {proposal_id} verworfen und nicht mehr bestätigbar. Frage nach, welche Peers gewünscht sind."
FACT_CORRECTION_HEAD = "Die letzte Antwort wurde nicht angezeigt, weil sie Zahlen oder ungültige Slots enthielt. Probleme:"
FACT_CORRECTION_TAIL = "Schreibe die Antwort ohne diese Probleme neu; setze für Werte Slots oder lass sie weg."
NONE_LABEL = "keine"

# Ausschnitt nur bei Zahl-Funden und nur aus einem geschlossenen Zeichensatz (12.14.5)
SNIPPET_KINDS = frozenset({"digits", "number_word", "magnitude_word", "ordinal", "fraction_word", "multiplicative"})
_SNIPPET = re.compile(r"^[\w.,%‰$€£¥+\-–/]{1,24}$")
TICKER_LIST = re.compile(r"^[A-Z0-9.\-]{1,10}$")


def user_text(language: str, key: str) -> str:
    return USER_TEXTS[language][key]


def tool_status(language: str, name: object) -> str:
    """Fester Statustext für ein Tool; der Name dient nur als Schlüssel und erscheint nie im Text (H3)."""
    statuses = TOOL_STATUS[language]
    return statuses[name] if isinstance(name, str) and name in statuses else USER_TEXTS[language]["status_working"]


def snippet_for(kind: str, match: str) -> str | None:
    """Der Ausschnitt eines Zahl-Funds, oder `None`, wenn er nicht übernommen werden darf."""
    if kind not in SNIPPET_KINDS:
        return None
    from scout_research.tools.sanitize import clean_text

    cleaned = clean_text(match, 80)
    return cleaned if _SNIPPET.fullmatch(cleaned) else None


def format_tickers(tickers: list[str]) -> str:
    safe = [t for t in tickers if TICKER_LIST.fullmatch(t)]
    return ", ".join(safe) if safe else NONE_LABEL


def correction_fact(problems: tuple[dict[str, Any], ...]) -> str:
    """Systemteil der Korrektur-Rückmeldung (M5): je Problem Code, Art, Position, Hinweis der Vorlage des Codes
    und — nur bei Zahl-Funden — der gefilterte Ausschnitt. Nie ein Hinweis oder Text aus Fremdinhalt."""
    from scout_research.tools.errors import ERROR_SPECS

    lines = [FACT_CORRECTION_HEAD]
    for problem in problems[:25]:
        code = str(problem.get("code", ""))
        spec = ERROR_SPECS.get(code)
        line = f"- {code if spec else 'INVALID_ARGUMENTS'} / {problem.get('kind', '')}: " \
               f"Zeichen {int(problem.get('start', 0))}–{int(problem.get('end', 0))} deiner letzten Antwort"
        snippet = snippet_for(str(problem.get("kind", "")), str(problem.get("match", "")))
        if snippet:
            line += f" („{snippet}“)"
        if spec:
            line += f". {spec.hint}"
        lines.append(line)
    lines.append(FACT_CORRECTION_TAIL)
    return "\n".join(lines)
