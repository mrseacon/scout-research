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
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

from scout_research.data.edgar_client import EdgarClient, FrameEntry
from scout_research.domain.metrics import REVENUE_CONCEPTS
from scout_research.domain.periods import derive_calendar_year

# Konsistent mit domain/metrics.py: dasselbe primäre Revenue-Konzept wie in der finalen
# Kennzahlen-Extraktion. Kandidaten, die ein anderes Revenue-Konzept nutzen, fehlen im
# Frame und werden mangels Vergleichsbasis übersprungen (siehe `filter_candidates_by_size`).
REVENUE_FRAME_CONCEPT = REVENUE_CONCEPTS[0]


class FrameYearUnresolved(Exception):
    """Das Zielunternehmen taucht in keinem `frames`-Jahr nahe seinem Geschäftsjahr auf — nicht raten."""

    code = "FRAME_YEAR_UNRESOLVED"

    def __init__(self, target_cik: str, period_end: str, tried_years: list[int]) -> None:
        self.target_cik = target_cik
        self.period_end = period_end
        self.tried_years = tried_years
        super().__init__(
            f"CIK {target_cik} mit Periodenende {period_end} taucht in keinem Frame der Jahre "
            f"{', '.join(map(str, tried_years))} auf; ein Größenvergleich ist so nicht möglich."
        )


class CalendarYearResolution(BaseModel):
    calendar_year: int
    frame_check: Literal["target_in_frame", "neighbor_year"]
    """`target_in_frame`: das aus der Zielperiode abgeleitete Jahr enthält das Ziel mit passendem Ende.
    `neighbor_year`: erst ein Nachbarjahr (±1) hat es enthalten."""


class CandidateCompany(BaseModel):
    """Ein Unternehmen mit passendem SIC-Code, noch ohne Größenfilter."""

    cik: str
    ticker: str
    name: str
    sic_code: str


class PeerCandidate(BaseModel):
    """Ein Kandidat, der sowohl den SIC- als auch den Größenfilter passiert hat.
    Enthält bewusst keine Begründung — die liefert das LLM-Ranking in Phase 3
    (Foundation Doc 7.4, Schritt 3)."""

    cik: str
    ticker: str
    name: str
    sic_code: str
    revenue: float
    size_ratio: float
    """`revenue / target_revenue` — Grundlage des 0,2×–5×-Filters."""
    calendar_year_used: int


def resolve_calendar_year(
    client: EdgarClient, target_cik: str, target_period_end: str | date
) -> tuple[CalendarYearResolution, dict[str, FrameEntry]]:
    """Leitet das `frames`-Kalenderjahr aus der Zielperiode ab (`Jahr(Periodenende − 180 Tage)`) und
    prüft es: das Ziel muss im Frame dieses Jahres mit **genau seinem Periodenende** auftauchen. Sonst
    werden die Nachbarjahre (±1) geprüft und andernfalls `FrameYearUnresolved` geworfen (Foundation Doc
    7.3.1). Gibt zusätzlich den geprüften Frame zurück, damit der Größenfilter ihn nicht erneut lädt."""
    period_end = target_period_end.isoformat() if isinstance(target_period_end, date) else target_period_end
    derived = derive_calendar_year(period_end)
    candidates = [(derived, "target_in_frame"), (derived - 1, "neighbor_year"), (derived + 1, "neighbor_year")]
    for year, check in candidates:
        frame = client.get_revenue_frame(REVENUE_FRAME_CONCEPT, year)
        entry = frame.get(target_cik)
        if entry is not None and entry.period_end == period_end:
            return CalendarYearResolution(calendar_year=year, frame_check=check), frame
    raise FrameYearUnresolved(target_cik, period_end, [year for year, _ in candidates])


def find_sic_candidates(
    client: EdgarClient, sic_code: str, exclude_cik: str
) -> list[CandidateCompany]:
    """Alle aktuell filenden Unternehmen mit gegebenem SIC-Code, außer dem Zielunternehmen
    selbst. Kandidaten ohne Ticker (nicht öffentlich handelbar, z. B. reine Bond-Registranten)
    werden übersprungen — sie eignen sich ohnehin nicht als Comps-Peer (keine Marktdaten)."""
    sic_matches = client.search_companies_by_sic(sic_code)
    cik_to_ticker = client.get_cik_to_ticker_map()

    candidates: list[CandidateCompany] = []
    seen_ciks: set[str] = set()

    for match in sic_matches:
        if match.cik == exclude_cik or match.cik in seen_ciks:
            continue
        entry = cik_to_ticker.get(match.cik)
        if entry is None:
            continue

        ticker, name = entry
        candidates.append(
            CandidateCompany(cik=match.cik, ticker=ticker, name=name, sic_code=match.sic_code)
        )
        seen_ciks.add(match.cik)

    return candidates


def filter_candidates_by_size(
    client: EdgarClient,
    candidates: list[CandidateCompany],
    target_revenue: float,
    calendar_year: int,
    size_range: tuple[float, float] = (0.2, 5.0),
    revenue_frame: dict[str, FrameEntry] | None = None,
) -> list[PeerCandidate]:
    """Größenfilter über den `frames`-Endpunkt (ein Bulk-Request statt N Einzelabrufen).
    Kandidaten, deren Revenue-Konzept nicht im Frame auftaucht, werden übersprungen statt
    geschätzt (siehe Modul-Docstring). Ein bereits geladener Frame (`revenue_frame`) wird wiederverwendet."""
    if target_revenue <= 0 or not candidates:
        return []

    if revenue_frame is None:
        revenue_frame = client.get_revenue_frame(REVENUE_FRAME_CONCEPT, calendar_year)
    lower_bound = target_revenue * size_range[0]
    upper_bound = target_revenue * size_range[1]

    results: list[PeerCandidate] = []
    for candidate in candidates:
        frame_entry = revenue_frame.get(candidate.cik)
        if frame_entry is None:
            continue
        if not (lower_bound <= frame_entry.value <= upper_bound):
            continue

        results.append(
            PeerCandidate(
                cik=candidate.cik,
                ticker=candidate.ticker,
                name=candidate.name,
                sic_code=candidate.sic_code,
                revenue=frame_entry.value,
                size_ratio=frame_entry.value / target_revenue,
                calendar_year_used=calendar_year,
            )
        )

    return results


def find_peer_candidates(
    client: EdgarClient,
    target_cik: str,
    target_sic: str,
    target_revenue: float,
    target_period_end: str | date,
    size_range: tuple[float, float] = (0.2, 5.0),
) -> list[PeerCandidate]:
    """Orchestriert SIC- und Größenfilter (Foundation Doc 7.3, Tool `find_peer_candidates`).
    Das Frame-Kalenderjahr wird aus dem Periodenende des Ziels abgeleitet und gegen den Frame geprüft
    (`resolve_calendar_year`) — es ist kein Parameter mehr."""
    resolution, frame = resolve_calendar_year(client, target_cik, target_period_end)
    candidates = find_sic_candidates(client, target_sic, exclude_cik=target_cik)
    return filter_candidates_by_size(
        client, candidates, target_revenue, resolution.calendar_year, size_range, revenue_frame=frame
    )
