"""L2 — Domain Logic: Extraktion von Kennzahlen aus rohen companyfacts.

Phase 0 deckt nur Revenue ab (Grundlage für den ersten End-to-End-Test). Die vollständige
Kennzahlen-Extraktion (EBIT, Net Income, EBITDA-Approximation, ...) folgt in Phase 1.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scout_research.domain.models import FinancialFact

# Fallback-Kette: XBRL-Tagging für Umsatz ist zwischen Unternehmen uneinheitlich
# (siehe Foundation Doc, Risikotabelle Abschnitt 13). Erster Treffer gewinnt.
REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]


class RevenueNotFoundError(Exception):
    pass


def extract_latest_annual_revenue(cik: str, company_facts: dict) -> FinancialFact:
    """Sucht den jüngsten annual (10-K, fp=FY) Revenue-Fact über die Konzept-Fallback-Kette.

    Wirft RevenueNotFoundError, wenn keines der bekannten Konzepte vorhanden ist — es wird
    niemals ein Wert geschätzt (siehe Foundation Doc 8.4-Prinzip, analog für alle Kennzahlen).
    """
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})

    for concept in REVENUE_CONCEPTS:
        concept_data = us_gaap.get(concept)
        if not concept_data:
            continue

        usd_entries = concept_data.get("units", {}).get("USD", [])
        annual_entries = [
            entry
            for entry in usd_entries
            if entry.get("form") == "10-K" and entry.get("fp") == "FY"
        ]
        if not annual_entries:
            continue

        latest = max(annual_entries, key=lambda entry: entry["end"])

        return FinancialFact(
            cik=cik,
            concept=concept,
            value=float(latest["val"]),
            unit="USD",
            period_start=latest.get("start"),
            period_end=latest["end"],
            fiscal_year=latest["fy"],
            fiscal_period=latest["fp"],
            form_type=latest["form"],
            accession_number=latest["accn"],
            filed_date=latest["filed"],
            retrieved_at=datetime.now(timezone.utc),
        )

    raise RevenueNotFoundError(
        f"Kein Revenue-Konzept aus {REVENUE_CONCEPTS} in companyfacts für CIK {cik} gefunden."
    )
