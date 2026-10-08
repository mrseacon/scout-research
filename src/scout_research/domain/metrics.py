"""L2 — Domain Logic: Extraktion und Ableitung von Kennzahlen aus rohen companyfacts.

Kern-Prinzipien (Foundation Doc 2.2, 8.4):
- Werte kommen ausschließlich aus XBRL-Fakten oder werden deterministisch daraus berechnet.
  Ist ein Wert nicht ermittelbar, wird er als `None` ausgewiesen — niemals geschätzt.
- **Periodentreue:** Die Berichtsperiode eines Unternehmens wird einmal festgelegt (Ende der
  jüngsten Umsatz-Periode, "Anker"). Jeder andere Fakt muss exakt auf diese Periode fallen —
  ein Wert aus einem früheren Jahr gilt als nicht vorhanden, nicht als Ersatz.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from scout_research.data.edgar_client import CompanyMetadata
from scout_research.data.market_provider import PriceQuote
from scout_research.domain.models import CompanyMetrics, FinancialFact, MarketSnapshot
from scout_research.domain.periods import PeriodNotAvailable, PeriodSelector

# Fallback-Ketten: XBRL-Tagging ist zwischen Unternehmen uneinheitlich (Risikotabelle, Abschnitt 13).
# Reihenfolge = Priorität bei gleicher Periode. Eine Kette darf nie ein *älteres* Konzept
# gegenüber einem Konzept mit jüngerer Periode bevorzugen (Firmen wechseln Tags).
REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]
EBIT_CONCEPTS = ["OperatingIncomeLoss"]
NET_INCOME_CONCEPTS = ["NetIncomeLoss", "ProfitLoss"]
TOTAL_ASSETS_CONCEPTS = ["Assets"]
CASH_CONCEPTS = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
DEPRECIATION_AMORTIZATION_CONCEPTS = [
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "DepreciationAndAmortization",
]
SHARES_OUTSTANDING_CONCEPTS = ["EntityCommonStockSharesOutstanding"]

# Schulden: nur *aggregierte* Konzepte, deren Definition (aus companyfacts gelesen, siehe
# docs/foundation.md 8.6) die jeweilige Gesamtheit abdeckt. Klassen- oder instrumentenbezogene
# Konzepte (UnsecuredLongTermDebt, SeniorNotes, ConvertibleNotesPayable, DebtInstrumentCarryingAmount, ...)
# sind bewusst NICHT enthalten: sie können unvollständig sein, ohne dass man es sieht.
# Leasing: die *Gesamt*-Konzepte "...AndCapitalLeaseObligations..." sind vollständige Aggregate und
# stehen mit niedrigster Priorität in den Ketten; der verwendete Konzeptname steht in der Provenance.
# `LongTermDebtAndCapitalLeaseObligations` (ohne Zusatz) ist laut Definition nicht-kurzfristig und
# daher kein Gesamtkonzept.
DEBT_TOTAL_CONCEPTS = [
    "DebtLongtermAndShorttermCombinedAmount",  # LT inkl. current maturities + ST
    "DebtAndCapitalLeaseObligations",  # ST + LT inkl. Leasingverbindlichkeiten
]
LONG_TERM_DEBT_TOTAL_CONCEPTS = [
    "LongTermDebt",  # LT inkl. current maturities (INTU: = Noncurrent + Current)
    "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
]
LONG_TERM_DEBT_NONCURRENT_CONCEPTS = ["LongTermDebtNoncurrent"]  # Definition: OHNE current maturities
DEBT_CURRENT_CONCEPTS = ["DebtCurrent"]  # ST-Debt + current maturities
LONG_TERM_DEBT_CURRENT_CONCEPTS = ["LongTermDebtCurrent"]  # nur current maturities der LT-Schuld
# Commercial Paper steht bei AAPL außerhalb von LongTermDebt (LTD = Noncurrent + Current exakt) und
# wird addiert, sofern es nicht erkennbar schon enthalten ist (`_is_contained`).
# `ShortTermBorrowings` wird NIE addiert: Filer nutzen das Tag uneinheitlich — IBM taggt damit die
# current maturities, die im Gesamtkonzept bereits stecken (54,84 + 6,42 = 61,26 Mrd.).
COMMERCIAL_PAPER_CONCEPTS = ["CommercialPaper"]
_CONTAINMENT_TOLERANCE = 0.005

# Untergrenze der Gesamtschuld (D10): nur Konzepte, deren Definition (Volltext in companyfacts
# gelesen) den kurzfristigen Anteil AUSDRÜCKLICH ausschließt ("excluding ...", "net of the amount
# due in the next twelve months"). Jedes davon ist eine Teilmenge der Gesamtschuld, daher ist das
# Maximum der vorhandenen Werte eine gültige Untergrenze. Nicht aufgenommen: Konzepte, die nur
# "classified as noncurrent" sagen (LongTermDebtAndCapitalLeaseObligations, lease-inklusiv),
# DebtInstrumentCarryingAmount (kann ein Einzelinstrument sein) und ShortTermBorrowings.
DEBT_LOWER_BOUND_CONCEPTS = [
    "LongTermDebtNoncurrent",  # "excluding amounts to be repaid within one year"
    "UnsecuredLongTermDebt",  # "excluding current portion" (nur unbesicherte Schuld)
    "SecuredLongTermDebt",  # "excluding the current portion" (nur besicherte Schuld)
    "LongTermNotesPayable",  # "excluding current portion" (nur Notes)
    "LongTermNotesAndLoans",  # "excluding current portion"
    "ConvertibleLongTermNotesPayable",  # "excluding current portion" (nur Wandelanleihen)
    "ConvertibleDebtNoncurrent",  # "net of the amount due in the next twelve months"
]

# Max. Abstand zweier aufeinanderfolgender Geschäftsjahresenden für eine YoY-Rate (52/53-Wochen-Jahre).
_YOY_GAP_DAYS = (350, 380)


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


def _annual_entries(
    taxonomy_facts: dict,
    concept: str,
    unit: str,
    form: str,
    fiscal_period: str,
    period_end: str | None,
    accession_number: str | None,
) -> list[dict]:
    entries = taxonomy_facts.get(concept, {}).get("units", {}).get(unit, [])
    return [
        e
        for e in entries
        if e.get("form") == form
        and e.get("fp") == fiscal_period
        and (period_end is None or e.get("end") == period_end)
        and (accession_number is None or e.get("accn") == accession_number)
    ]


def extract_latest_annual_concept(
    cik: str,
    company_facts: dict,
    concepts: list[str],
    unit: str = "USD",
    taxonomy: str = "us-gaap",
    form: str = "10-K",
    fiscal_period: str = "FY",
    period_end: str | None = None,
    accession_number: str | None = None,
) -> FinancialFact | None:
    """Jüngster annual Fakt über eine Konzept-Fallback-Kette. `None` statt Exception —
    Aufrufer entscheiden, ob ein fehlender Wert kritisch ist (siehe 8.4).

    - Ohne `period_end`: gewinnt das Konzept mit dem **jüngsten** Periodenende; die
      Kettenreihenfolge entscheidet nur bei Gleichstand. (Früher gewann das erste Konzept mit
      irgendeiner Historie — das lieferte z. B. 2022er Umsatz für NVDA.)
    - Mit `period_end`: nur Fakten exakt dieser Periode; ältere Werte zählen als fehlend.
    - Mehrere Einträge für dasselbe Konzept und Periodenende (das 10-K des Berichtsjahres und spätere
      10-Ks mit Vergleichswerten, ggf. korrigiert): es gilt der **zuerst eingereichte**, also das 10-K des
      Berichtsjahres selbst.
    - Mit `accession_number`: nur Fakten aus genau diesem Filing (für Cover-Page-Daten).
    """
    taxonomy_facts = company_facts.get("facts", {}).get(taxonomy, {})

    best: tuple[tuple[str, int], str, dict] | None = None
    for priority, concept in enumerate(concepts):
        annual = _annual_entries(
            taxonomy_facts, concept, unit, form, fiscal_period, period_end, accession_number
        )
        if not annual:
            continue
        latest_end = max(e["end"] for e in annual)
        latest = min((e for e in annual if e["end"] == latest_end), key=lambda e: e["filed"])
        rank = (latest["end"], -priority)
        if best is None or rank > best[0]:
            best = (rank, concept, latest)

    if best is None:
        return None
    return _fact_from_entry(cik, best[1], unit, best[2])


def extract_annual_series(
    cik: str,
    company_facts: dict,
    concepts: list[str],
    unit: str = "USD",
    taxonomy: str = "us-gaap",
    form: str = "10-K",
    fiscal_period: str = "FY",
) -> list[FinancialFact]:
    """Alle annual Fakten *des Konzepts mit dem jüngsten Periodenende*, absteigend sortiert,
    je Periodenende ein Eintrag (bei mehrfacher Meldung der zuletzt eingereichte) — Grundlage
    für YoY-Wachstumsraten."""
    taxonomy_facts = company_facts.get("facts", {}).get(taxonomy, {})

    best: tuple[tuple[str, int], str, list[dict]] | None = None
    for priority, concept in enumerate(concepts):
        annual = _annual_entries(taxonomy_facts, concept, unit, form, fiscal_period, None, None)
        if not annual:
            continue
        rank = (max(e["end"] for e in annual), -priority)
        if best is None or rank > best[0]:
            best = (rank, concept, annual)

    if best is None:
        return []

    by_end: dict[str, dict] = {}
    for entry in best[2]:
        current = by_end.get(entry["end"])
        if current is None or entry["filed"] > current["filed"]:
            by_end[entry["end"]] = entry

    facts = [_fact_from_entry(cik, best[1], unit, e) for e in by_end.values()]
    return sorted(facts, key=lambda f: f.period_end, reverse=True)


def extract_latest_annual_revenue(cik: str, company_facts: dict) -> FinancialFact:
    """Wirft RevenueNotFoundError statt None, weil Revenue für einen Comps-Lauf unverzichtbar
    ist (alle Multiples hängen davon ab) und die Berichtsperiode festlegt."""
    fact = extract_latest_annual_concept(cik, company_facts, REVENUE_CONCEPTS)
    if fact is None:
        raise RevenueNotFoundError(
            f"Kein Revenue-Konzept aus {REVENUE_CONCEPTS} in companyfacts für CIK {cik} gefunden."
        )
    return fact


def _sum_facts(cik: str, parts: list[FinancialFact | None]) -> FinancialFact | None:
    """Summiert mehrere Fakten derselben Periode zu einem Fakt, dessen `concept` alle
    Bestandteile nennt (Provenance). Ein einzelner Bestandteil wird unverändert zurückgegeben."""
    present = [p for p in parts if p is not None]
    if not present:
        return None
    if len(present) == 1:
        return present[0]

    first = present[0]
    return FinancialFact(
        cik=cik,
        concept="+".join(p.concept for p in present),
        value=sum(p.value for p in present),
        unit=first.unit,
        period_start=first.period_start,
        period_end=first.period_end,
        fiscal_year=first.fiscal_year,
        fiscal_period=first.fiscal_period,
        form_type=first.form_type,
        accession_number="+".join(dict.fromkeys(p.accession_number for p in present)),
        filed_date=max(p.filed_date for p in present),
        retrieved_at=datetime.now(timezone.utc),
    )


def _is_contained(total: FinancialFact, noncurrent: FinancialFact, extra: FinancialFact) -> bool:
    """True, wenn `extra` erkennbar bereits in `total` steckt: total == noncurrent + extra
    (z. B. ist ein "kurzfristiger" Posten exakt der Teil, der zur Gesamtschuld fehlt)."""
    return abs(total.value - (noncurrent.value + extra.value)) <= _CONTAINMENT_TOLERANCE * max(total.value, 1.0)


def extract_total_debt(cik: str, company_facts: dict, period_end: str) -> FinancialFact | None:
    """Gesamtschuld (ohne Leasingverbindlichkeiten) zur Periode `period_end`.

    Regel: Ein Konzept zählt nur dann als Gesamtschuld, wenn seine Definition die Gesamtheit
    abdeckt — oder wenn jeder Teil, den seine Definition ausschließt, explizit gemeldet ist
    (auch als 0). Fehlt ein ausgeschlossener Teil (z. B. kurzfristiger Anteil bei
    `LongTermDebtNoncurrent`), ist die Zahl unvollständig und bleibt `None`.
    """

    def get(chain: list[str]) -> FinancialFact | None:
        return extract_latest_annual_concept(cik, company_facts, chain, period_end=period_end)

    combined = get(DEBT_TOTAL_CONCEPTS)
    if combined is not None:
        return combined

    noncurrent = get(LONG_TERM_DEBT_NONCURRENT_CONCEPTS)
    debt_current = get(DEBT_CURRENT_CONCEPTS)
    if noncurrent is not None and debt_current is not None:
        return _sum_facts(cik, [noncurrent, debt_current])  # DebtCurrent enthält ST + current LTD

    commercial_paper = get(COMMERCIAL_PAPER_CONCEPTS)

    long_term_total = get(LONG_TERM_DEBT_TOTAL_CONCEPTS)
    if long_term_total is not None:
        if commercial_paper is not None and noncurrent is not None and _is_contained(
            long_term_total, noncurrent, commercial_paper
        ):
            commercial_paper = None
        return _sum_facts(cik, [long_term_total, commercial_paper])

    if noncurrent is not None:
        current_ltd = get(LONG_TERM_DEBT_CURRENT_CONCEPTS)
        if current_ltd is not None:
            if commercial_paper is not None and abs(commercial_paper.value - current_ltd.value) <= (
                _CONTAINMENT_TOLERANCE * max(current_ltd.value, 1.0)
            ):
                commercial_paper = None  # identisch mit den current maturities -> schon enthalten
            return _sum_facts(cik, [noncurrent, current_ltd, commercial_paper])

    return None


def extract_debt_lower_bound(cik: str, company_facts: dict, period_end: str) -> FinancialFact | None:
    """Untergrenze der Gesamtschuld, wenn `extract_total_debt` keine exakte Zahl liefert.

    Nimmt das Maximum der Konzepte aus `DEBT_LOWER_BOUND_CONCEPTS`, die für genau diese Periode
    gemeldet sind. Der Aufrufer muss das Ergebnis als Untergrenze kennzeichnen
    (`CompanyMetrics.total_debt_is_lower_bound`). Gibt es keines, bleibt die Schuld `None`.
    """
    facts = [
        extract_latest_annual_concept(cik, company_facts, [concept], period_end=period_end)
        for concept in DEBT_LOWER_BOUND_CONCEPTS
    ]
    present = [f for f in facts if f is not None]
    return max(present, key=lambda f: f.value) if present else None


def compute_ebitda(ebit: FinancialFact | None, d_and_a: FinancialFact | None) -> tuple[float | None, bool]:
    """EBITDA ist kein XBRL-Konzept (Foundation Doc 8.4) — approximiert als EBIT + D&A.
    Fehlt eine der Komponenten, wird EBITDA als nicht verfügbar ausgewiesen, nicht geschätzt."""
    if ebit is None or d_and_a is None:
        return None, True
    return ebit.value + d_and_a.value, True


def available_period_ends(company_facts: dict) -> list[str]:
    """Geschäftsjahresenden mit Jahresumsatz in einem 10-K (über die ganze Umsatz-Kette), neueste zuerst.
    Nur für diese Perioden lässt sich ein Periodenanker bilden."""
    taxonomy_facts = company_facts.get("facts", {}).get("us-gaap", {})
    ends = {
        e["end"]
        for concept in REVENUE_CONCEPTS
        for e in _annual_entries(taxonomy_facts, concept, "USD", "10-K", "FY", None, None)
    }
    return sorted(ends, reverse=True)


def select_revenue_anchor(cik: str, company_facts: dict, period: PeriodSelector | None = None) -> FinancialFact:
    """Der Umsatz-Anker der gewählten Periode (Foundation Doc 7.3.1, 8.6).

    Ohne Auswahl: das jüngste Geschäftsjahr (wie bisher). Mit `period_end`: exakt dieses Ende. Mit
    `fiscal_year` (Best Effort, SEC-Feld `fy`): das Ende des 10-K-Jahresabschlusses mit diesem `fy` — die
    Vergleichswerte der Vorjahre in einem 10-K tragen dasselbe `fy`, daher zählt das jüngste Ende darunter.
    Fehlt die Periode, wird `PeriodNotAvailable` mit den verfügbaren Enden geworfen — nie ein anderes Jahr.
    """
    if period is None or period.is_latest:
        return extract_latest_annual_revenue(cik, company_facts)

    if period.period_end is not None:
        wanted_end = period.period_end.isoformat()
    else:
        taxonomy_facts = company_facts.get("facts", {}).get("us-gaap", {})
        ends_for_fy = [
            e["end"]
            for concept in REVENUE_CONCEPTS
            for e in _annual_entries(taxonomy_facts, concept, "USD", "10-K", "FY", None, None)
            if e.get("fy") == period.fiscal_year
        ]
        wanted_end = max(ends_for_fy) if ends_for_fy else None

    fact = (
        extract_latest_annual_concept(cik, company_facts, REVENUE_CONCEPTS, period_end=wanted_end)
        if wanted_end is not None
        else None
    )
    if fact is None:
        raise PeriodNotAvailable(cik, period, available_period_ends(company_facts))
    return fact


def restrict_to_filing(company_facts: dict, accession_number: str) -> dict:
    """Nur Einträge aus genau einem Filing. Verhindert, dass zu einer Periode Werte aus verschiedenen
    10-Ks gemischt werden (Originalwert aus dem Berichtsjahr + korrigierter Vergleichswert aus einem
    späteren 10-K). Für die jüngste Periode ändert das nichts — es gibt kein späteres 10-K."""
    return {
        **company_facts,
        "facts": {
            taxonomy: {
                concept: {
                    **data,
                    "units": {
                        unit: [e for e in entries if e.get("accn") == accession_number]
                        for unit, entries in data.get("units", {}).items()
                    },
                }
                for concept, data in concepts.items()
            }
            for taxonomy, concepts in company_facts.get("facts", {}).items()
        },
    }


def _yoy_growth(anchor: FinancialFact, series: list[FinancialFact]) -> float | None:
    """Wachstum des Ankerumsatzes gegenüber dem direkt vorhergehenden Geschäftsjahr (350–380 Tage
    Abstand der Periodenenden). Ohne Vorjahr in der Reihe: `None`."""
    anchor_end = date.fromisoformat(anchor.period_end)
    for previous in series:
        gap = (anchor_end - date.fromisoformat(previous.period_end)).days
        if _YOY_GAP_DAYS[0] <= gap <= _YOY_GAP_DAYS[1]:
            if not previous.value:
                return None
            return (anchor.value - previous.value) / previous.value
    return None


def build_company_metrics(
    cik: str,
    company: CompanyMetadata,
    company_facts: dict,
    price_quote: PriceQuote | None,
    period: PeriodSelector | None = None,
) -> CompanyMetrics:
    """Orchestriert die Extraktion aller v1-Kennzahlen für ein Unternehmen (Foundation Doc 3.2).

    Fehlende Einzelwerte blockieren nicht den gesamten Aufbau — sie werden als `None`
    durchgereicht, damit Nutzer sehen, was fehlt, statt einen Absturz zu erleben. Alle Werte
    gehören zur Periode des Ankers (siehe Modul-Docstring) und stammen aus **dem 10-K, das den Umsatz-
    Anker liefert**.

    `period` wählt das Geschäftsjahr (D9); ohne Angabe das jüngste. Für eine **historische** Periode
    (nicht die jüngste) werden Kurs und Marktkapitalisierung nicht verwendet — der Kurs ist der heutige,
    Multiples gibt es nur für die aktuelle Periode (`CompanyMetrics.is_historical`).
    """
    revenue = select_revenue_anchor(cik, company_facts, period)
    anchor = revenue.period_end
    is_historical = anchor != available_period_ends(company_facts)[0]
    filing_facts = restrict_to_filing(company_facts, revenue.accession_number)

    def at_anchor(chain: list[str]) -> FinancialFact | None:
        return extract_latest_annual_concept(cik, filing_facts, chain, period_end=anchor)

    ebit = at_anchor(EBIT_CONCEPTS)
    net_income = at_anchor(NET_INCOME_CONCEPTS)
    total_assets = at_anchor(TOTAL_ASSETS_CONCEPTS)
    total_debt = extract_total_debt(cik, filing_facts, anchor)
    total_debt_is_lower_bound = False
    if total_debt is None:
        total_debt = extract_debt_lower_bound(cik, filing_facts, anchor)
        total_debt_is_lower_bound = total_debt is not None
    cash = at_anchor(CASH_CONCEPTS)
    d_and_a = at_anchor(DEPRECIATION_AMORTIZATION_CONCEPTS)
    # Cover-Page-Daten tragen das Datum des Einreichens, nicht der Periode — daher Bindung an
    # dasselbe 10-K-Filing wie der Umsatz statt an das Periodenende.
    shares = extract_latest_annual_concept(
        cik,
        filing_facts,
        SHARES_OUTSTANDING_CONCEPTS,
        unit="shares",
        taxonomy="dei",
        accession_number=revenue.accession_number,
    )

    ebitda_value, ebitda_approximated = compute_ebitda(ebit, d_and_a)

    source_facts = [
        f for f in [revenue, ebit, net_income, total_assets, total_debt, cash, d_and_a, shares] if f
    ]

    market: MarketSnapshot | None = None
    enterprise_value: float | None = None
    if price_quote is not None and shares is not None and not is_historical:
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

    revenue_growth_yoy = _yoy_growth(revenue, extract_annual_series(cik, company_facts, REVENUE_CONCEPTS))

    margins = {
        "ebit_margin": (ebit.value / revenue.value) if ebit and revenue.value else None,
        "net_margin": (net_income.value / revenue.value) if net_income and revenue.value else None,
    }
    growth_rates = {"revenue_yoy": revenue_growth_yoy}

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
        total_debt_is_lower_bound=total_debt_is_lower_bound,
        cash=cash.value if cash else None,
        market=market,
        enterprise_value=enterprise_value,
        enterprise_value_is_lower_bound=total_debt_is_lower_bound and enterprise_value is not None,
        is_historical=is_historical,
        margins=margins,
        growth_rates=growth_rates,
        source_facts=source_facts,
    )
