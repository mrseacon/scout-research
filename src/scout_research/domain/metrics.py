"""L2 — Domain Logic: Extraktion und Ableitung von Kennzahlen aus rohen companyfacts.

Kern-Prinzip (Foundation Doc 2.2, 8.4): Werte kommen ausschließlich aus XBRL-Fakten oder
werden deterministisch daraus berechnet. Ist ein Konzept nicht vorhanden, wird der Wert als
`None` ausgewiesen — niemals geschätzt.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scout_research.data.edgar_client import CompanyMetadata
from scout_research.data.market_provider import PriceQuote
from scout_research.domain.models import CompanyMetrics, FinancialFact, MarketSnapshot

# Fallback-Ketten: XBRL-Tagging ist zwischen Unternehmen uneinheitlich (Risikotabelle, Abschnitt 13).
REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]
EBIT_CONCEPTS = ["OperatingIncomeLoss"]
NET_INCOME_CONCEPTS = ["NetIncomeLoss", "ProfitLoss"]
TOTAL_ASSETS_CONCEPTS = ["Assets"]
TOTAL_DEBT_CONCEPTS = ["DebtLongtermAndShorttermCombinedAmount", "LongTermDebtAndCapitalLeaseObligations"]
LONG_TERM_DEBT_CONCEPTS = ["LongTermDebtNoncurrent", "LongTermDebt"]
SHORT_TERM_DEBT_CONCEPTS = ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"]
CASH_CONCEPTS = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
DEPRECIATION_AMORTIZATION_CONCEPTS = [
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "DepreciationAndAmortization",
]
SHARES_OUTSTANDING_CONCEPTS = ["EntityCommonStockSharesOutstanding"]


class RevenueNotFoundError(Exception):
    pass


def _fact_from_entry(cik: str, concept: str, unit: str, entry: dict) -> FinancialFact:
    return FinancialFact(
        cik=cik,
        concept=concept,
        value=float(entry["val"]),
        unit=unit,
        period_start=entry.get("start"),
        period_end=entry["end"],
        fiscal_year=entry["fy"],
        fiscal_period=entry["fp"],
        form_type=entry["form"],
        accession_number=entry["accn"],
        filed_date=entry["filed"],
        retrieved_at=datetime.now(timezone.utc),
    )


def extract_latest_annual_concept(
    cik: str,
    company_facts: dict,
    concepts: list[str],
    unit: str = "USD",
    taxonomy: str = "us-gaap",
    form: str = "10-K",
    fiscal_period: str = "FY",
) -> FinancialFact | None:
    """Sucht den jüngsten annual Fact über eine Konzept-Fallback-Kette. `None` statt
    Exception — Aufrufer entscheiden, ob ein fehlender Wert kritisch ist (siehe 8.4)."""
    taxonomy_facts = company_facts.get("facts", {}).get(taxonomy, {})

    for concept in concepts:
        concept_data = taxonomy_facts.get(concept)
        if not concept_data:
            continue

        entries = concept_data.get("units", {}).get(unit, [])
        annual = [e for e in entries if e.get("form") == form and e.get("fp") == fiscal_period]
        if not annual:
            continue

        latest = max(annual, key=lambda e: e["end"])
        return _fact_from_entry(cik, concept, unit, latest)

    return None


def extract_annual_series(
    cik: str,
    company_facts: dict,
    concepts: list[str],
    unit: str = "USD",
    taxonomy: str = "us-gaap",
    form: str = "10-K",
    fiscal_period: str = "FY",
) -> list[FinancialFact]:
    """Wie `extract_latest_annual_concept`, liefert aber alle annual Facts absteigend nach
    Periodenende sortiert — Grundlage für YoY-Wachstumsraten."""
    taxonomy_facts = company_facts.get("facts", {}).get(taxonomy, {})

    for concept in concepts:
        concept_data = taxonomy_facts.get(concept)
        if not concept_data:
            continue

        entries = concept_data.get("units", {}).get(unit, [])
        annual = [e for e in entries if e.get("form") == form and e.get("fp") == fiscal_period]
        if not annual:
            continue

        facts = [_fact_from_entry(cik, concept, unit, e) for e in annual]
        return sorted(facts, key=lambda f: f.period_end, reverse=True)

    return []


def extract_latest_annual_revenue(cik: str, company_facts: dict) -> FinancialFact:
    """Wie Phase 0 — wirft RevenueNotFoundError statt None, weil Revenue für einen Comps-Lauf
    unverzichtbar ist (alle Multiples hängen davon ab)."""
    fact = extract_latest_annual_concept(cik, company_facts, REVENUE_CONCEPTS)
    if fact is None:
        raise RevenueNotFoundError(
            f"Kein Revenue-Konzept aus {REVENUE_CONCEPTS} in companyfacts für CIK {cik} gefunden."
        )
    return fact


def extract_total_debt(cik: str, company_facts: dict) -> FinancialFact | None:
    """Erst ein direktes Aggregat-Konzept versuchen; sonst kurz- und langfristige
    Fremdkapitalanteile summieren, wenn beide vorhanden sind."""
    direct = extract_latest_annual_concept(cik, company_facts, TOTAL_DEBT_CONCEPTS)
    if direct is not None:
        return direct

    long_term = extract_latest_annual_concept(cik, company_facts, LONG_TERM_DEBT_CONCEPTS)
    short_term = extract_latest_annual_concept(cik, company_facts, SHORT_TERM_DEBT_CONCEPTS)
    if long_term is None or short_term is None:
        return None

    return FinancialFact(
        cik=cik,
        concept=f"{long_term.concept}+{short_term.concept}",
        value=long_term.value + short_term.value,
        unit="USD",
        period_start=long_term.period_start,
        period_end=long_term.period_end,
        fiscal_year=long_term.fiscal_year,
        fiscal_period=long_term.fiscal_period,
        form_type=long_term.form_type,
        accession_number=long_term.accession_number,
        filed_date=long_term.filed_date,
        retrieved_at=datetime.now(timezone.utc),
    )


def compute_ebitda(ebit: FinancialFact | None, d_and_a: FinancialFact | None) -> tuple[float | None, bool]:
    """EBITDA ist kein XBRL-Konzept (Foundation Doc 8.4) — approximiert als EBIT + D&A.
    Fehlt eine der Komponenten, wird EBITDA als nicht verfügbar ausgewiesen, nicht geschätzt."""
    if ebit is None or d_and_a is None:
        return None, True
    return ebit.value + d_and_a.value, True


def build_company_metrics(
    cik: str,
    company: CompanyMetadata,
    company_facts: dict,
    price_quote: PriceQuote | None,
) -> CompanyMetrics:
    """Orchestriert die Extraktion aller v1-Kennzahlen für ein Unternehmen (Foundation Doc 3.2).

    Fehlende Einzelwerte blockieren nicht den gesamten Aufbau — sie werden als `None`
    durchgereicht, damit Nutzer sehen, was fehlt, statt einen Absturz zu erleben.
    """
    revenue = extract_latest_annual_concept(cik, company_facts, REVENUE_CONCEPTS)
    ebit = extract_latest_annual_concept(cik, company_facts, EBIT_CONCEPTS)
    net_income = extract_latest_annual_concept(cik, company_facts, NET_INCOME_CONCEPTS)
    total_assets = extract_latest_annual_concept(cik, company_facts, TOTAL_ASSETS_CONCEPTS)
    total_debt = extract_total_debt(cik, company_facts)
    cash = extract_latest_annual_concept(cik, company_facts, CASH_CONCEPTS)
    d_and_a = extract_latest_annual_concept(cik, company_facts, DEPRECIATION_AMORTIZATION_CONCEPTS)
    shares = extract_latest_annual_concept(
        cik, company_facts, SHARES_OUTSTANDING_CONCEPTS, unit="shares", taxonomy="dei"
    )

    ebitda_value, ebitda_approximated = compute_ebitda(ebit, d_and_a)

    source_facts = [
        f for f in [revenue, ebit, net_income, total_assets, total_debt, cash, d_and_a, shares] if f
    ]

    market: MarketSnapshot | None = None
    enterprise_value: float | None = None
    if price_quote is not None and shares is not None:
        market_cap = price_quote.price * shares.value
        market = MarketSnapshot(
            ticker=price_quote.ticker,
            price=price_quote.price,
            shares_outstanding=shares.value,
            market_cap=market_cap,
            as_of_date=price_quote.as_of_date,
            source=price_quote.source,
        )
        if total_debt is not None and cash is not None:
            enterprise_value = market_cap + total_debt.value - cash.value

    revenue_series = extract_annual_series(cik, company_facts, REVENUE_CONCEPTS)
    revenue_growth_yoy = None
    if len(revenue_series) >= 2 and revenue_series[1].value:
        revenue_growth_yoy = (revenue_series[0].value - revenue_series[1].value) / revenue_series[1].value

    margins = {
        "ebit_margin": (ebit.value / revenue.value) if ebit and revenue and revenue.value else None,
        "net_margin": (net_income.value / revenue.value) if net_income and revenue and revenue.value else None,
    }
    growth_rates = {"revenue_yoy": revenue_growth_yoy}

    if revenue is None:
        raise RevenueNotFoundError(
            f"Kein Revenue-Konzept aus {REVENUE_CONCEPTS} in companyfacts für CIK {cik} gefunden."
        )

    return CompanyMetrics(
        company=company,
        period_end=revenue.period_end,
        fiscal_year=revenue.fiscal_year,
        revenue=revenue.value,
        ebit=ebit.value if ebit else None,
        ebitda=ebitda_value,
        ebitda_approximated=ebitda_approximated,
        net_income=net_income.value if net_income else None,
        total_assets=total_assets.value if total_assets else None,
        total_debt=total_debt.value if total_debt else None,
        cash=cash.value if cash else None,
        market=market,
        enterprise_value=enterprise_value,
        margins=margins,
        growth_rates=growth_rates,
        source_facts=source_facts,
    )
