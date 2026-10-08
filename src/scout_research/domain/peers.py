"""L2 — Peer-Kandidatensuche (Foundation Doc 7.4, Schritte 1–2).

Deckt bewusst nur die deterministische Vorstufe ab: SIC-Filter + Größenfilter. Das
LLM-Ranking (Schritt 3) und die menschliche Freigabe (Schritt 4) sind Phase 3 — hier
entsteht nur die Kandidatenliste, gegen die der Agent später arbeitet.

Zur Kandidatenquelle (Foundation Doc, offene Entscheidung D2): EDGAR bietet keinen
direkten "alle Unternehmen mit SIC X"-Endpunkt für JSON, aber `browse-edgar` (SIC-Filter,
Foundation Doc-Erweiterung in `edgar_client.search_companies_by_sic`) und `frames`
(Bulk-Konzeptwerte über alle Filer, `edgar_client.get_revenue_frame`) lösen das zusammen
ohne selbst gepflegten Index. Jede Funktion hier ist bewusst deterministisch und
seiteneffektfrei bis auf die injizierten `EdgarClient`-Aufrufe — direkt als Tool-Handler
in Phase 3 wiederverwendbar.

**Größenvergleich über alle Umsatz-Konzepte.** Firmen taggen ihren Umsatz uneinheitlich
(`RevenueFromContractWithCustomerExcludingAssessedTax`, `Revenues`, ...). Der Größenfilter lädt daher den
Frame des Konzepts mit der höchsten Priorität (`REVENUE_CONCEPTS[0]`) und die weiteren Konzepte nur, solange
Kandidaten dort fehlen. Je Firma zählt der Eintrag des Konzepts mit der höchsten Kettenpriorität.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Literal

from pydantic import BaseModel

from scout_research.data.edgar_client import EdgarClient, EdgarDataNotFound, FrameEntry
from scout_research.domain.metrics import REVENUE_CONCEPTS
from scout_research.domain.periods import derive_calendar_year

MAX_PEER_CANDIDATES = 40
"""Höchstzahl der Kandidaten, die ein Tool an das Modell gibt (Phase-3-Plan, `find_peer_candidates`)."""

REVENUE_FRAME_CONCEPT = REVENUE_CONCEPTS[0]
"""Konzept mit höchster Priorität; seine Frame-Einträge gewinnen, wenn eine Firma in mehreren vorkommt."""


class FrameYearUnresolved(Exception):
    """Das Zielunternehmen taucht in keinem `frames`-Jahr nahe seinem Geschäftsjahr auf — nicht raten."""

    code = "FRAME_YEAR_UNRESOLVED"

    def __init__(self, target_cik: str, period_end: str, tried_years: list[int], concept: str) -> None:
        self.target_cik = target_cik
        self.period_end = period_end
        self.tried_years = tried_years
        self.concept = concept
        super().__init__(
            f"CIK {target_cik} mit Periodenende {period_end} taucht im Frame von {concept} in keinem der Jahre "
            f"{', '.join(map(str, tried_years))} auf; ein Größenvergleich ist so nicht möglich."
        )


class CalendarYearResolution(BaseModel):
    calendar_year: int
    frame_check: Literal["target_in_frame", "neighbor_year"]
    """`target_in_frame`: das aus der Zielperiode abgeleitete Jahr enthält das Ziel mit passendem Ende.
    `neighbor_year`: erst ein Nachbarjahr (±1) hat es enthalten."""
    concept: str
    """Das Umsatz-Konzept, gegen dessen Frame geprüft wurde (das Konzept des Umsatz-Ankers des Ziels)."""


class CandidateCompany(BaseModel):
    """Ein Unternehmen mit passendem SIC-Code, noch ohne Größenfilter."""

    cik: str
    ticker: str
    name: str
    sic_code: str


class PeerCandidate(BaseModel):
    """Ein Kandidat, der sowohl den SIC- als auch den Größenfilter passiert hat.
    Enthält bewusst keine Begründung — die liefert das LLM-Ranking in Phase 3
    (Foundation Doc 7.4, Schritt 3). Der Umsatz trägt seine Herkunft (Provenance)."""

    cik: str
    ticker: str
    name: str
    sic_code: str
    revenue: float
    size_ratio: float
    """`revenue / target_revenue` — Grundlage des 0,2×–5×-Filters."""
    calendar_year_used: int
    revenue_concept: str
    revenue_period_end: str
    revenue_accession_number: str


class SizeFilterOutcome(BaseModel):
    candidates: list[PeerCandidate]
    not_in_revenue_frame: int
    """Kandidaten mit Ticker, die in keinem Umsatz-Frame vorkommen (Größe nicht prüfbar, nicht geschätzt)."""
    outside_size_range: int
    frames_loaded: list[str]
    """Konzepte, deren Frame für diese Suche geladen wurde (Reihenfolge der Kettenpriorität)."""


class PeerSearchCounts(BaseModel):
    sic_matches: int
    """Unternehmen mit dem SIC-Code des Ziels (ohne das Ziel)."""
    without_ticker: int
    """Davon ohne Ticker (nicht handelbar) — ausgeschlossen."""
    not_in_revenue_frame: int
    outside_size_range: int
    returned: int
    frames_loaded: list[str]
    sic_search_truncated: bool = False
    """True, wenn die SIC-Suche am Seitenlimit endete: `sic_matches` ist dann eine Untergrenze."""


class PeerSearchResult(BaseModel):
    calendar_year: CalendarYearResolution
    candidates: list[PeerCandidate]
    counts: PeerSearchCounts


class SicSearchOutcome(BaseModel):
    candidates: list[CandidateCompany]
    sic_matches: int
    without_ticker: int
    search_truncated: bool = False


def resolve_calendar_year(
    client: EdgarClient, target_cik: str, target_period_end: str | date, concept: str
) -> CalendarYearResolution:
    """Leitet das `frames`-Kalenderjahr aus der Zielperiode ab (`Jahr(Periodenende − 180 Tage)`) und
    prüft es: das Ziel muss im Frame dieses Jahres **für sein eigenes Umsatz-Konzept** (`concept`, das
    Konzept seines Umsatz-Ankers) mit **genau seinem Periodenende** auftauchen. Sonst werden die
    Nachbarjahre (±1) geprüft und andernfalls `FrameYearUnresolved` geworfen (Foundation Doc 7.3.1).
    Ein Jahr ohne Frame (404) zählt als "nicht enthalten"."""
    period_end = target_period_end.isoformat() if isinstance(target_period_end, date) else target_period_end
    derived = derive_calendar_year(period_end)
    candidates = [(derived, "target_in_frame"), (derived - 1, "neighbor_year"), (derived + 1, "neighbor_year")]
    for year, check in candidates:
        try:
            frame = client.get_revenue_frame(concept, year)
        except EdgarDataNotFound:
            continue
        entry = frame.get(target_cik)
        if entry is not None and entry.period_end == period_end:
            return CalendarYearResolution(calendar_year=year, frame_check=check, concept=concept)
    raise FrameYearUnresolved(target_cik, period_end, [year for year, _ in candidates], concept)


def search_sic_candidates(client: EdgarClient, sic_code: str, exclude_cik: str) -> SicSearchOutcome:
    """Alle aktuell filenden Unternehmen mit gegebenem SIC-Code, außer dem Zielunternehmen selbst.
    Kandidaten ohne Ticker (nicht öffentlich handelbar, z. B. reine Bond-Registranten) werden übersprungen —
    sie eignen sich ohnehin nicht als Comps-Peer (keine Marktdaten) — aber mitgezählt."""
    page = client.search_companies_by_sic_paged(sic_code)
    sic_matches = page.candidates
    cik_to_ticker = client.get_cik_to_ticker_map()

    candidates: list[CandidateCompany] = []
    seen_ciks: set[str] = set()
    without_ticker = 0

    for match in sic_matches:
        if match.cik == exclude_cik or match.cik in seen_ciks:
            continue
        seen_ciks.add(match.cik)
        entry = cik_to_ticker.get(match.cik)
        if entry is None:
            without_ticker += 1
            continue

        ticker, name = entry
        candidates.append(CandidateCompany(cik=match.cik, ticker=ticker, name=name, sic_code=match.sic_code))

    return SicSearchOutcome(
        candidates=candidates,
        sic_matches=len(seen_ciks),
        without_ticker=without_ticker,
        search_truncated=page.truncated,
    )


def find_sic_candidates(client: EdgarClient, sic_code: str, exclude_cik: str) -> list[CandidateCompany]:
    return search_sic_candidates(client, sic_code, exclude_cik).candidates


def classify_candidates_by_size(
    client: EdgarClient,
    candidates: list[CandidateCompany],
    target_revenue: float,
    calendar_year: int,
    size_range: tuple[float, float] = (0.2, 5.0),
) -> SizeFilterOutcome:
    """Größenfilter über `frames` (ein Bulk-Request je Konzept statt N Einzelabrufen), mit Zählern.

    Lazy über die Umsatz-Konzepte: zuerst `REVENUE_CONCEPTS[0]`; die weiteren Konzepte nur, solange
    Kandidaten in den bisherigen Frames fehlen. Je Kandidat zählt das Konzept mit der höchsten
    Kettenpriorität. Kandidaten, die in keinem Frame stehen, werden gezählt und übersprungen statt
    geschätzt. Frames, die es für das Jahr nicht gibt (404), zählen als leer."""
    if target_revenue <= 0 or not candidates:
        return SizeFilterOutcome(candidates=[], not_in_revenue_frame=0, outside_size_range=0, frames_loaded=[])

    found: dict[str, tuple[str, FrameEntry]] = {}
    frames_loaded: list[str] = []
    for concept in REVENUE_CONCEPTS:
        missing = [c for c in candidates if c.cik not in found]
        if not missing:
            break
        try:
            frame = client.get_revenue_frame(concept, calendar_year)
        except EdgarDataNotFound:
            continue
        frames_loaded.append(concept)
        for candidate in missing:
            entry = frame.get(candidate.cik)
            if entry is not None:
                found[candidate.cik] = (concept, entry)

    lower_bound = target_revenue * size_range[0]
    upper_bound = target_revenue * size_range[1]
    results: list[PeerCandidate] = []
    not_in_frame = outside = 0
    for candidate in candidates:
        hit = found.get(candidate.cik)
        if hit is None:
            not_in_frame += 1
            continue
        concept, entry = hit
        if not (lower_bound <= entry.value <= upper_bound):
            outside += 1
            continue
        results.append(
            PeerCandidate(
                cik=candidate.cik,
                ticker=candidate.ticker,
                name=candidate.name,
                sic_code=candidate.sic_code,
                revenue=entry.value,
                size_ratio=entry.value / target_revenue,
                calendar_year_used=calendar_year,
                revenue_concept=concept,
                revenue_period_end=entry.period_end,
                revenue_accession_number=entry.accession_number,
            )
        )
    return SizeFilterOutcome(
        candidates=results, not_in_revenue_frame=not_in_frame, outside_size_range=outside, frames_loaded=frames_loaded
    )


def filter_candidates_by_size(
    client: EdgarClient,
    candidates: list[CandidateCompany],
    target_revenue: float,
    calendar_year: int,
    size_range: tuple[float, float] = (0.2, 5.0),
) -> list[PeerCandidate]:
    return classify_candidates_by_size(client, candidates, target_revenue, calendar_year, size_range).candidates


def find_peer_candidates(
    client: EdgarClient,
    target_cik: str,
    target_sic: str,
    target_revenue: float,
    target_period_end: str | date,
    target_revenue_concept: str,
    size_range: tuple[float, float] = (0.2, 5.0),
) -> PeerSearchResult:
    """Orchestriert SIC- und Größenfilter (Foundation Doc 7.3, Tool `find_peer_candidates`).
    Das Frame-Kalenderjahr wird aus dem Periodenende des Ziels abgeleitet und gegen den Frame des
    Umsatz-Konzepts des Ziels (`target_revenue_concept`, Konzept seines Umsatz-Ankers) geprüft."""
    resolution = resolve_calendar_year(client, target_cik, target_period_end, target_revenue_concept)
    sic = search_sic_candidates(client, target_sic, exclude_cik=target_cik)
    size = classify_candidates_by_size(client, sic.candidates, target_revenue, resolution.calendar_year, size_range)
    return PeerSearchResult(
        calendar_year=resolution,
        candidates=size.candidates,
        counts=PeerSearchCounts(
            sic_matches=sic.sic_matches,
            without_ticker=sic.without_ticker,
            not_in_revenue_frame=size.not_in_revenue_frame,
            outside_size_range=size.outside_size_range,
            returned=len(size.candidates),
            frames_loaded=size.frames_loaded,
            sic_search_truncated=sic.search_truncated,
        ),
    )


def rank_candidates(
    candidates: list[PeerCandidate], limit: int = MAX_PEER_CANDIDATES
) -> tuple[list[PeerCandidate], int]:
    """Sortiert nach Größenähnlichkeit und kürzt: aufsteigend nach `|ln size_ratio|` (0,5× und 2× sind gleich weit
    vom Ziel), bei Gleichstand nach Ticker, dann CIK. Gibt die ersten `limit` und die Zahl der abgeschnittenen
    Kandidaten zurück. Deterministisch und unabhängig von der Trefferreihenfolge der SIC-Suche."""
    ordered = sorted(candidates, key=lambda c: (abs(math.log(c.size_ratio)) if c.size_ratio > 0 else math.inf, c.ticker, c.cik))
    kept = ordered[:limit]
    return kept, len(ordered) - len(kept)
