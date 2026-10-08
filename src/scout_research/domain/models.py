"""L2 — Domain-Modelle. Kern-Entitäten gemäß Foundation Doc Abschnitt 10.

Phase 1 ergänzt MarketSnapshot und CompanyMetrics. Phase 2 ergänzt QualityWarning,
CompanyMultiples, MultipleStatistics und CompsTable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from scout_research.data.edgar_client import CompanyMetadata


class FinancialFact(BaseModel):
    """Eine einzelne Kennzahl mit vollständiger Herkunft. Keine Zahl im System existiert
    ohne diese Provenance-Angaben (siehe Foundation Doc 2.1, 10)."""

    cik: str
    concept: str
    value: float
    unit: str
    period_start: str | None
    period_end: str
    fiscal_year: int
    fiscal_period: str
    form_type: str
    accession_number: str
    filed_date: str
    retrieved_at: datetime


class MarketSnapshot(BaseModel):
    """Kurs + daraus abgeleitete Marktkapitalisierung. `shares_outstanding` stammt aus
    EDGAR-Cover-Page-Daten (nicht vom Kurs-Provider) — siehe Foundation Doc 8.3."""

    ticker: str
    price: float
    shares_outstanding: float
    market_cap: float
    as_of_date: str
    source: str


class CompanyMetrics(BaseModel):
    """Vollständiger Kennzahlensatz für ein Unternehmen zu einer Periode.

    Design-Regel (Foundation Doc 10): Jeder Wert muss über `source_facts` auf mindestens
    einen `FinancialFact` zurückführbar sein. Werte, die nicht ermittelt werden konnten,
    sind `None` — es wird niemals geschätzt (siehe 8.4).
    """

    company: CompanyMetadata
    period_end: str
    fiscal_year: int

    revenue: float | None
    ebit: float | None
    ebitda: float | None
    ebitda_approximated: bool
    net_income: float | None
    total_assets: float | None
    total_debt: float | None
    total_debt_is_lower_bound: bool = False
    """True, wenn `total_debt` nur eine Untergrenze ist: es stammt aus einem Konzept, dessen
    Definition den kurzfristigen Anteil ausdrücklich ausschließt, und dieser Anteil ist für die
    Periode nicht gemeldet (Foundation Doc 8.6, D10). Muster wie `ebitda_approximated`."""
    cash: float | None

    market: MarketSnapshot | None
    enterprise_value: float | None
    enterprise_value_is_lower_bound: bool = False
    """True, wenn `enterprise_value` berechnet wurde und `total_debt` eine Untergrenze ist —
    der EV ist dann ebenfalls eine Untergrenze (zu niedrig)."""

    is_historical: bool = False
    """True, wenn die Periode nicht das jüngste Geschäftsjahr ist (D9). Dann gibt es weder Kurs noch
    Marktkapitalisierung noch EV: Multiples nur für die aktuelle Periode (Foundation Doc 7.3.1)."""

    margins: dict[str, float | None]
    growth_rates: dict[str, float | None]

    source_facts: list[FinancialFact]


class QualityWarning(BaseModel):
    """Ein Datenqualitäts-Hinweis (Foundation Doc 2.3, 11). Severity ist nie eine
    Fehlermeldung, die den Lauf abbricht — der Mensch entscheidet, was damit passiert
    (HITL-Prinzip, siehe 6.2)."""

    severity: Literal["info", "warning", "critical"]
    company: str
    """Ticker oder 'target' — welches Unternehmen betroffen ist."""
    message: str
    affected_field: str | None


class CompanyMultiples(BaseModel):
    """Bewertungs-Multiples für ein Unternehmen. Nicht aussagekräftige Multiples (z. B.
    EV/EBITDA bei negativem EBITDA) werden als `None` geführt, nicht als verzerrte Zahl —
    der Grund steht in `excluded_reasons` (siehe Foundation Doc 3.2, Aufgabenstellung Punkt 2).

    Trägt bewusst keine eigenen `FinancialFact`-Referenzen: jedes Multiple ist eine reine
    Ableitung aus Feldern von `CompanyMetrics` (enterprise_value, revenue, ebitda, ebit,
    net_income), deren Provenance bereits vollständig über `CompsTable.target`/`.peers`
    (inkl. `source_facts`) nachvollziehbar ist. Keine redundante Duplikation der Kette.
    """

    company_ticker: str
    ev_revenue: float | None
    ev_ebitda: float | None
    ev_ebit: float | None
    pe: float | None
    excluded_reasons: dict[str, str]
    ev_multiples_are_lower_bound: bool = False
    """True, wenn EV/Revenue, EV/EBITDA und EV/EBIT auf einer Untergrenze der Gesamtschuld
    beruhen (der wahre Wert ist ≥ dem gezeigten). P/E ist nie betroffen."""


class MultipleStatistics(BaseModel):
    """Min/Median/Mean/Max eines Multiples über das Peer-Set (Foundation Doc 10)."""

    multiple_name: str
    min: float | None
    median: float | None
    mean: float | None
    max: float | None
    count_included: int
    excluded_tickers: list[str]
    lower_bound_tickers: list[str] = Field(default_factory=list)
    """Peers, deren in die Statistik eingegangener Wert eine Untergrenze ist (D10). Die
    Statistik mischt dann exakte Werte und Untergrenzen."""


class CompsTable(BaseModel):
    """Das vollständige Comps-Deliverable (Foundation Doc 10)."""

    target: CompanyMetrics
    peers: list[CompanyMetrics]
    target_multiples: CompanyMultiples
    peer_multiples: list[CompanyMultiples]
    statistics: list[MultipleStatistics]
    warnings: list[QualityWarning]
    created_at: datetime
    period_basis: str
