"""Periodentreue und Schuldenlogik — synthetische companyfacts, kein Netzwerk.

Jeder Test bildet einen real gemessenen Fall nach (siehe Kommentare); die Zahlen sind
vereinfacht, die Struktur (welches Konzept zu welcher Periode existiert) ist echt.
"""

import pytest

from scout_research.data.edgar_client import CompanyMetadata
from scout_research.data.market_provider import PriceQuote
from scout_research.domain.metrics import (
    RevenueNotFoundError,
    build_company_metrics,
    extract_annual_series,
    extract_latest_annual_concept,
    extract_latest_annual_revenue,
    extract_total_debt,
)

CIK = "0000000042"
FY_END = "2026-01-31"
ACCN = "0000000042-26-000001"


def entry(end: str, val: float, accn: str = ACCN, fy: int = 2026, filed: str = "2026-03-01", start: str | None = None) -> dict:
    e = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": "FY", "form": "10-K", "filed": filed}
    if start:
        e["start"] = start
    return e


def facts(us_gaap: dict[str, list[dict]] | None = None, dei: dict[str, list[dict]] | None = None) -> dict:
    return {
        "facts": {
            "us-gaap": {c: {"units": {"USD": es}} for c, es in (us_gaap or {}).items()},
            "dei": {c: {"units": {"shares": es}} for c, es in (dei or {}).items()},
        }
    }


def metadata() -> CompanyMetadata:
    return CompanyMetadata(
        cik=CIK, name="Test Corp", sic_code="7372", sic_description="Software",
        fiscal_year_end="0131", tickers=["TST"], exchanges=["Nasdaq"],
    )


# --- Umsatz / Anker ---------------------------------------------------------------------------


def test_revenue_picks_concept_with_latest_period_not_first_in_chain() -> None:
    # NVDA/PYPL: das Standard-Tag hat nur alte Jahre, das jüngste Jahr steht unter "Revenues".
    f = facts({
        "RevenueFromContractWithCustomerExcludingAssessedTax": [entry("2022-01-30", 100, fy=2022)],
        "Revenues": [entry(FY_END, 500)],
    })
    fact = extract_latest_annual_revenue(CIK, f)
    assert fact.concept == "Revenues"
    assert fact.period_end == FY_END


def test_revenue_chain_order_decides_only_on_equal_period() -> None:
    f = facts({
        "RevenueFromContractWithCustomerExcludingAssessedTax": [entry(FY_END, 500)],
        "Revenues": [entry(FY_END, 999)],
    })
    assert extract_latest_annual_revenue(CIK, f).value == 500


def test_missing_revenue_raises() -> None:
    with pytest.raises(RevenueNotFoundError):
        extract_latest_annual_revenue(CIK, facts({}))


def test_fact_from_older_period_counts_as_missing() -> None:
    f = facts({"OperatingIncomeLoss": [entry("2025-01-31", 70, fy=2025)]})
    assert extract_latest_annual_concept(CIK, f, ["OperatingIncomeLoss"], period_end=FY_END) is None


def test_stale_da_in_first_chain_concept_does_not_beat_current_period_concept() -> None:
    # ADSK: DepreciationDepletionAndAmortization endet 2016, DepreciationAndAmortization ist aktuell.
    f = facts({
        "RevenueFromContractWithCustomerExcludingAssessedTax": [entry(FY_END, 5000)],
        "OperatingIncomeLoss": [entry(FY_END, 1000)],
        "DepreciationDepletionAndAmortization": [entry("2016-01-31", 145, fy=2016)],
        "DepreciationAndAmortization": [entry(FY_END, 195)],
    })
    m = build_company_metrics(CIK, metadata(), f, None)
    assert m.ebitda == 1195  # EBIT 1000 + D&A 195 (nicht + 145 aus 2016)
    da = next(s for s in m.source_facts if "Amortization" in s.concept)
    assert da.period_end == FY_END and da.concept == "DepreciationAndAmortization"


def test_only_stale_da_leaves_ebitda_missing() -> None:
    f = facts({
        "RevenueFromContractWithCustomerExcludingAssessedTax": [entry(FY_END, 5000)],
        "OperatingIncomeLoss": [entry(FY_END, 1000)],
        "DepreciationDepletionAndAmortization": [entry("2013-01-31", 145, fy=2013)],
    })
    m = build_company_metrics(CIK, metadata(), f, None)
    assert m.ebitda is None
    assert m.ebit == 1000
    assert all(s.period_end == FY_END for s in m.source_facts)


# --- Schulden ---------------------------------------------------------------------------------


def debt(us_gaap: dict[str, list[dict]]):
    return extract_total_debt(CIK, facts(us_gaap), FY_END)


def test_long_term_debt_total_is_not_double_counted_with_current_maturities() -> None:
    # Definition: LongTermDebt enthält die current maturities bereits (INTU: 7669 = 6420 + 1249).
    d = debt({"LongTermDebt": [entry(FY_END, 7669)], "LongTermDebtCurrent": [entry(FY_END, 1249)]})
    assert d is not None and d.value == 7669 and d.concept == "LongTermDebt"


def test_noncurrent_plus_explicit_current_is_summed_with_full_provenance() -> None:
    d = debt({"LongTermDebtNoncurrent": [entry(FY_END, 6420)], "LongTermDebtCurrent": [entry(FY_END, 1249)]})
    assert d is not None and d.value == 7669
    assert d.concept == "LongTermDebtNoncurrent+LongTermDebtCurrent"
    assert d.accession_number == ACCN


def test_explicit_zero_current_portion_counts_as_reported() -> None:
    d = debt({"LongTermDebtNoncurrent": [entry(FY_END, 990)], "LongTermDebtCurrent": [entry(FY_END, 0)]})
    assert d is not None and d.value == 990


def test_noncurrent_with_only_stale_current_portion_stays_missing() -> None:
    # TEAM: LongTermDebtNoncurrent aktuell, LongTermDebtCurrent nur aus 2024 -> unvollständig.
    d = debt({"LongTermDebtNoncurrent": [entry(FY_END, 990)], "LongTermDebtCurrent": [entry("2024-01-31", 0, fy=2024)]})
    assert d is None


def test_noncurrent_alone_stays_missing() -> None:
    assert debt({"LongTermDebtNoncurrent": [entry(FY_END, 2987)]}) is None


def test_debt_current_supersedes_partial_current_components() -> None:
    # DebtCurrent = ST-Debt + current maturities (Definition) -> keine Addition weiterer Teile.
    d = debt({
        "LongTermDebtNoncurrent": [entry(FY_END, 1000)],
        "DebtCurrent": [entry(FY_END, 300)],
        "LongTermDebtCurrent": [entry(FY_END, 200)],
        "ShortTermBorrowings": [entry(FY_END, 100)],
    })
    assert d is not None and d.value == 1300


def test_commercial_paper_is_added_to_long_term_total_when_reported() -> None:
    d = debt({"LongTermDebt": [entry(FY_END, 90000)], "CommercialPaper": [entry(FY_END, 8000)]})
    assert d is not None and d.value == 98000
    assert d.concept == "LongTermDebt+CommercialPaper"


def test_commercial_paper_outside_the_total_is_added_aapl_case() -> None:
    # AAPL: LongTermDebt 90.68 = Noncurrent 78.33 + Current 12.35; CP 7.98 steht außerhalb.
    d = debt({
        "LongTermDebt": [entry(FY_END, 90.68)],
        "LongTermDebtNoncurrent": [entry(FY_END, 78.33)],
        "CommercialPaper": [entry(FY_END, 7.98)],
    })
    assert d is not None and d.value == pytest.approx(98.66)


def test_commercial_paper_already_inside_the_total_is_not_added() -> None:
    # total == noncurrent + CP  ->  CP ist der kurzfristige Teil des Totals, keine Doppelzählung.
    d = debt({
        "LongTermDebt": [entry(FY_END, 100)],
        "LongTermDebtNoncurrent": [entry(FY_END, 90)],
        "CommercialPaper": [entry(FY_END, 10)],
    })
    assert d is not None and d.value == 100


def test_short_term_borrowings_are_never_added_ibm_case() -> None:
    # IBM: 54.84 (Noncurrent) + 6.42 (ShortTermBorrowings) = 61.26 = lease-inkl. Gesamtkonzept.
    d = debt({
        "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities": [entry(FY_END, 61.26)],
        "LongTermDebtNoncurrent": [entry(FY_END, 54.84)],
        "ShortTermBorrowings": [entry(FY_END, 6.42)],
    })
    assert d is not None and d.value == pytest.approx(61.26)
    assert d.concept == "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"


def test_noncurrent_current_and_commercial_paper_are_summed_when_distinct() -> None:
    d = debt({
        "LongTermDebtNoncurrent": [entry(FY_END, 1000)],
        "LongTermDebtCurrent": [entry(FY_END, 200)],
        "CommercialPaper": [entry(FY_END, 50)],
    })
    assert d is not None and d.value == 1250


def test_combined_debt_concept_wins_over_components() -> None:
    d = debt({
        "DebtLongtermAndShorttermCombinedAmount": [entry(FY_END, 6210)],
        "LongTermDebt": [entry(FY_END, 5000)],
    })
    assert d is not None and d.value == 6210


def test_stale_combined_concept_is_ignored() -> None:
    # ADBE: DebtLongtermAndShorttermCombinedAmount nur aus 2019 -> darf nicht verwendet werden.
    d = debt({
        "DebtLongtermAndShorttermCombinedAmount": [entry("2019-11-29", 4138, fy=2019)],
        "LongTermDebtNoncurrent": [entry(FY_END, 6210)],
        "LongTermDebtCurrent": [entry(FY_END, 0)],
    })
    assert d is not None and d.value == 6210


def test_class_level_concepts_are_not_accepted_as_total() -> None:
    # CDNS: nur UnsecuredLongTermDebt (Klassenkonzept, "excluding current portion").
    assert debt({"UnsecuredLongTermDebt": [entry(FY_END, 2480)]}) is None
    assert debt({"ConvertibleLongTermNotesPayable": [entry(FY_END, 1491)]}) is None
    assert debt({"DebtInstrumentCarryingAmount": [entry(FY_END, 62286)]}) is None


def test_lease_inclusive_total_concept_is_accepted_but_noncurrent_variant_is_not() -> None:
    # EBAY: DebtAndCapitalLeaseObligations ist ein vollständiges Aggregat; das Konzept ohne
    # "IncludingCurrentMaturities" ist laut Definition nicht-kurzfristig.
    assert debt({"DebtAndCapitalLeaseObligations": [entry(FY_END, 6746)]}).value == 6746
    assert debt({"LongTermDebtAndCapitalLeaseObligations": [entry(FY_END, 5996)]}) is None


# --- Shares -----------------------------------------------------------------------------------


def _metrics_with_shares(dei: dict[str, list[dict]]):
    f = facts(
        {"RevenueFromContractWithCustomerExcludingAssessedTax": [entry(FY_END, 5000)]},
        dei,
    )
    quote = PriceQuote(ticker="TST", price=10.0, as_of_date="2026-10-02", source="finnhub")
    return build_company_metrics(CIK, metadata(), f, quote)


def test_shares_from_same_filing_are_used() -> None:
    m = _metrics_with_shares({"EntityCommonStockSharesOutstanding": [entry("2026-02-23", 211_000_000)]})
    assert m.market is not None and m.market.shares_outstanding == 211_000_000


def test_shares_from_older_filing_are_rejected() -> None:
    # Mehrklassen-Firma: Cover-Page-Fakt nur aus früherem 10-K -> darf nicht als aktuell gelten.
    older = entry("2024-02-23", 200_000_000, accn="0000000042-24-000001", fy=2024, filed="2024-03-01")
    m = _metrics_with_shares({"EntityCommonStockSharesOutstanding": [older]})
    assert m.market is None
    assert m.enterprise_value is None


def test_missing_cover_page_shares_leave_market_missing() -> None:
    # TEAM/GOOGL/META: Klassen-Fakten sind dimensional und stehen nicht in companyfacts.
    assert _metrics_with_shares({}).market is None


# --- Wachstum ---------------------------------------------------------------------------------

REV = "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_yoy_uses_two_consecutive_fiscal_years() -> None:
    f = facts({REV: [entry("2025-01-31", 400, fy=2025), entry(FY_END, 500)]})
    m = build_company_metrics(CIK, metadata(), f, None)
    assert m.growth_rates["revenue_yoy"] == pytest.approx(0.25)


def test_yoy_is_missing_when_years_are_not_consecutive() -> None:
    f = facts({REV: [entry("2023-01-31", 400, fy=2023), entry(FY_END, 500)]})
    m = build_company_metrics(CIK, metadata(), f, None)
    assert m.growth_rates["revenue_yoy"] is None


def test_annual_series_has_one_entry_per_period_end_latest_filing_wins() -> None:
    f = facts({REV: [
        entry(FY_END, 500, accn="A", filed="2026-03-01"),
        entry(FY_END, 510, accn="B", filed="2027-03-01"),  # Restatement als Vergleichsspalte
        entry("2025-01-31", 400, fy=2025),
    ]})
    series = extract_annual_series(CIK, f, [REV])
    assert [s.period_end for s in series] == [FY_END, "2025-01-31"]
    assert series[0].value == 510


# --- Ende-zu-Ende mit Anker -------------------------------------------------------------------


def test_enterprise_value_uses_only_anchor_period_facts() -> None:
    f = facts(
        {
            REV: [entry(FY_END, 5000)],
            "OperatingIncomeLoss": [entry(FY_END, 1000)],
            "NetIncomeLoss": [entry(FY_END, 800)],
            "CashAndCashEquivalentsAtCarryingValue": [entry(FY_END, 300), entry("2025-01-31", 9999, fy=2025)],
            "LongTermDebt": [entry(FY_END, 2000)],
        },
        {"EntityCommonStockSharesOutstanding": [entry("2026-02-23", 100)]},
    )
    quote = PriceQuote(ticker="TST", price=10.0, as_of_date="2026-10-02", source="finnhub")
    m = build_company_metrics(CIK, metadata(), f, quote)
    assert m.market is not None
    assert m.market.market_cap == 1000
    assert m.enterprise_value == 1000 + 2000 - 300
    assert {s.period_end for s in m.source_facts if s.concept != "EntityCommonStockSharesOutstanding"} == {FY_END}
