"""L3 — Namensabgleich für `resolve_company` (Phase-3-Plan, A10): deterministisch, ohne LLM, ohne neue Abhängigkeit.

1. Exakter Ticker → `resolved`.
2. Exakter normalisierter Name, eindeutig (nach CIK) → `resolved`; mehrere CIKs → `ambiguous`.
3. Präfix-/Tokentreffer und `difflib` (Cutoff 0,85) liefern **nur Kandidaten** (`ambiguous`, höchstens 5), nie eine
   automatische Auflösung.
Ticker werden normalisiert (`.`/`/` → `-`, `$` ignoriert), mehrere Ticker derselben CIK zählen als eine Firma.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Literal

from scout_research.data.edgar_client import CompanyLookup

MAX_CANDIDATES = 5
FUZZY_CUTOFF = 0.85
_STOP_WORDS = {"inc", "corp", "corporation", "co", "ltd", "limited", "plc", "holdings", "holding", "group", "the", "company"}

MatchKind = Literal["ticker_exact", "name_exact", "name_prefix", "name_fuzzy"]


def normalize_ticker(text: str) -> str:
    return re.sub(r"[./\s]+", "-", text.strip().lstrip("$").upper())


def normalize_name(text: str) -> str:
    """Kleinschreibung, ohne Satzzeichen, ohne Rechtsformzusätze."""
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    return " ".join(w for w in words if w not in _STOP_WORDS)


@dataclass
class Company:
    cik: str
    name: str
    tickers: list[str]  # in der Reihenfolge der SEC-Ticker-Map; der erste ist der Haupt-Ticker


@dataclass
class CandidateMatch:
    company: Company
    match: MatchKind


@dataclass
class Resolution:
    status: Literal["resolved", "ambiguous", "not_found"]
    company: Company | None = None
    match: MatchKind | None = None
    candidates: list[CandidateMatch] = field(default_factory=list)
    candidate_count: int = 0


def group_by_cik(entries: list[CompanyLookup]) -> list[Company]:
    by_cik: dict[str, Company] = {}
    for entry in entries:
        company = by_cik.get(entry.cik)
        if company is None:
            by_cik[entry.cik] = Company(cik=entry.cik, name=entry.name, tickers=[entry.ticker.upper()])
        elif entry.ticker.upper() not in company.tickers:
            company.tickers.append(entry.ticker.upper())
    return list(by_cik.values())


def resolve(query: str, companies: list[Company]) -> Resolution:
    ticker_query = normalize_ticker(query)
    if ticker_query:
        for company in companies:
            if ticker_query in company.tickers:
                return Resolution("resolved", company, "ticker_exact", candidate_count=1)

    normalized = normalize_name(query)
    if not normalized:
        return Resolution("not_found")

    norm = {c.cik: normalize_name(c.name) for c in companies}
    exact = [c for c in companies if norm[c.cik] == normalized]
    if len(exact) == 1:
        return Resolution("resolved", exact[0], "name_exact", candidate_count=1)
    if len(exact) > 1:
        top = [CandidateMatch(c, "name_exact") for c in sorted(exact, key=lambda c: c.name)[:MAX_CANDIDATES]]
        return Resolution("ambiguous", candidates=top, candidate_count=len(exact))

    query_tokens = normalized.split()
    prefix_hits: list[Company] = []
    for company in companies:
        name = norm[company.cik]
        tokens = name.split()
        if name.startswith(normalized) and (len(name) == len(normalized) or name[len(normalized)] == " "):
            prefix_hits.append(company)
        elif all(any(t.startswith(q) for t in tokens) for q in query_tokens):
            prefix_hits.append(company)
    prefix_hits.sort(key=lambda c: (len(norm[c.cik]), c.name))

    chosen: list[CandidateMatch] = [CandidateMatch(c, "name_prefix") for c in prefix_hits]
    seen = {c.company.cik for c in chosen}
    by_name = {norm[c.cik]: c for c in companies}
    for name in difflib.get_close_matches(normalized, list(by_name), n=MAX_CANDIDATES, cutoff=FUZZY_CUTOFF):
        company = by_name[name]
        if company.cik not in seen:
            seen.add(company.cik)
            chosen.append(CandidateMatch(company, "name_fuzzy"))

    if not chosen:
        return Resolution("not_found")
    return Resolution("ambiguous", candidates=chosen[:MAX_CANDIDATES], candidate_count=len(chosen))
