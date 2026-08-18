"""L2 — Domain-Modelle. Kern-Entitäten gemäß Foundation Doc Abschnitt 10.

Phase 1 ergänzt MarketSnapshot und CompanyMetrics. CompsTable und QualityWarning folgen in Phase 2.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

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
    cash: float | None

    market: MarketSnapshot | None
    enterprise_value: float | None

    margins: dict[str, float | None]
    growth_rates: dict[str, float | None]

    source_facts: list[FinancialFact]
