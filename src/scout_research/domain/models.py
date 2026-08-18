"""L2 — Domain-Modelle. Kern-Entitäten gemäß Foundation Doc Abschnitt 10.

Phase 0 enthält nur `FinancialFact` — Träger des Provenance-Prinzips. Weitere Entitäten
(CompanyMetrics, CompsTable, QualityWarning, ...) folgen in Phase 1/2.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
