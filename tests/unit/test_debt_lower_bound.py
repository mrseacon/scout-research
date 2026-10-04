"""D10: Gesamtschuld als Untergrenze (Flag) — Extraktion, Propagation, Statistik, Warnung.

Kein Netzwerk. Die Strukturen bilden gemessene Fälle nach (TEAM: nur LongTermDebtNoncurrent und ein
0-Wert für den kurzfristigen Anteil aus dem Vorjahr; CDNS: nur UnsecuredLongTermDebt; PANW:
ConvertibleDebtNoncurrent).
"""

import pytest

from scout_research.data.market_provider import PriceQuote
from scout_research.domain.comps import build_comps_table
from scout_research.domain.metrics import (
    build_company_metrics,
    extract_debt_lower_bound,
    extract_total_debt,
)
from scout_research.domain.multiples import compute_multiple_statistics, compute_multiples
from scout_research.domain.quality import check_debt_lower_bound, run_quality_checks
from tests.unit.factories import make_metrics
from tests.unit.test_metrics_periods import ACCN, CIK, FY_END, REV, entry, facts, metadata

QUOTE = PriceQuote(ticker="TST", price=10.0, as_of_date="2026-10-02", source="finnhub")
SHARES = {"EntityCommonStockSharesOutstanding": [entry("2026-02-23", 100)]}


def lower_bound(us_gaap: dict[str, list[dict]]):
    return extract_debt_lower_bound(CIK, facts(us_gaap), FY_END)


# --- Extraktion --------------------------------------------------------------------------------


def test_noncurrent_only_is_a_lower_bound_not_an_exact_total() -> None:
    f = facts({"LongTermDebtNoncurrent": [entry(FY_END, 990)]})
    assert extract_total_debt(CIK, f, FY_END) is None
    bound = extract_debt_lower_bound(CIK, f, FY_END)
    assert bound is not None and bound.value == 990 and bound.concept == "LongTermDebtNoncurrent"


def test_stale_current_portion_still_gives_only_a_lower_bound_team_case() -> None:
    f = facts({
        "LongTermDebtNoncurrent": [entry(FY_END, 990)],
        "LongTermDebtCurrent": [entry("2024-01-31", 0, fy=2024)],
    })
    assert extract_total_debt(CIK, f, FY_END) is None
    assert extract_debt_lower_bound(CIK, f, FY_END).value == 990


@pytest.mark.parametrize(
    "concept",
    [
        "UnsecuredLongTermDebt",  # CDNS
        "SecuredLongTermDebt",
        "LongTermNotesPayable",
        "LongTermNotesAndLoans",
        "ConvertibleLongTermNotesPayable",  # NOW, ZS, DDOG
        "ConvertibleDebtNoncurrent",  # PANW
    ],
)
def test_concepts_with_explicit_current_portion_exclusion_are_accepted_as_lower_bound(concept: str) -> None:
    bound = lower_bound({concept: [entry(FY_END, 2480)]})
    assert bound is not None and bound.concept == concept and bound.value == 2480


def test_maximum_of_available_concepts_is_used() -> None:
    # Jedes Konzept ist eine Teilmenge der Gesamtschuld -> das Maximum ist eine gültige Untergrenze.
    bound = lower_bound({
        "LongTermNotesPayable": [entry(FY_END, 500)],
        "LongTermDebtNoncurrent": [entry(FY_END, 900)],
        "ConvertibleLongTermNotesPayable": [entry(FY_END, 300)],
    })
    assert bound.value == 900 and bound.concept == "LongTermDebtNoncurrent"


@pytest.mark.parametrize(
    "concept",
    [
        "LongTermDebtAndCapitalLeaseObligations",  # nur "classified as noncurrent", lease-inklusiv
        "DebtInstrumentCarryingAmount",  # kann ein Einzelinstrument sein
        "ShortTermBorrowings",
        "SeniorNotes",
        "FinanceLeaseLiability",
    ],
)
def test_concepts_without_explicit_exclusion_are_not_accepted(concept: str) -> None:
    assert lower_bound({concept: [entry(FY_END, 1000)]}) is None


def test_lower_bound_from_older_period_is_not_used() -> None:
    assert lower_bound({"LongTermDebtNoncurrent": [entry("2025-01-31", 990, fy=2025)]}) is None


def test_no_debt_concept_at_all_stays_missing_hubs_case() -> None:
    assert lower_bound({}) is None


# --- build_company_metrics ---------------------------------------------------------------------


def _build(us_gaap: dict[str, list[dict]]):
    base = {REV: [entry(FY_END, 5000)], "CashAndCashEquivalentsAtCarryingValue": [entry(FY_END, 300)]}
    return build_company_metrics(CIK, metadata(), facts({**base, **us_gaap}, SHARES), QUOTE)


def test_exact_total_never_carries_the_flag() -> None:
    m = _build({"LongTermDebt": [entry(FY_END, 2000)]})
    assert m.total_debt == 2000
    assert m.total_debt_is_lower_bound is False
    assert m.enterprise_value_is_lower_bound is False


def test_lower_bound_flows_into_enterprise_value_and_is_flagged() -> None:
    m = _build({"LongTermDebtNoncurrent": [entry(FY_END, 990)]})
    assert m.total_debt == 990 and m.total_debt_is_lower_bound is True
    assert m.enterprise_value == 1000 + 990 - 300  # Market Cap 10*100
    assert m.enterprise_value_is_lower_bound is True
    debt_fact = next(f for f in m.source_facts if f.concept == "LongTermDebtNoncurrent")
    assert debt_fact.accession_number == ACCN  # Provenance bleibt lückenlos


def test_enterprise_value_flag_is_false_when_ev_is_not_computable() -> None:
    m = build_company_metrics(CIK, metadata(), facts({REV: [entry(FY_END, 5000)],
                                                      "LongTermDebtNoncurrent": [entry(FY_END, 990)]}), None)
    assert m.total_debt_is_lower_bound is True
    assert m.enterprise_value is None
    assert m.enterprise_value_is_lower_bound is False


# --- Multiples / Statistik ---------------------------------------------------------------------


def test_ev_multiples_carry_the_flag_but_pe_is_unaffected() -> None:
    mu = compute_multiples(make_metrics(total_debt_is_lower_bound=True))
    assert mu.ev_multiples_are_lower_bound is True
    assert mu.ev_revenue is not None and mu.pe is not None


def test_exact_company_has_no_multiples_flag() -> None:
    assert compute_multiples(make_metrics()).ev_multiples_are_lower_bound is False


def test_statistics_list_lower_bound_tickers_for_ev_multiples_only() -> None:
    peers = [
        compute_multiples(make_metrics(ticker="EXACT")),
        compute_multiples(make_metrics(ticker="BOUND", total_debt_is_lower_bound=True)),
    ]
    ev = compute_multiple_statistics(peers, "ev_revenue")
    pe = compute_multiple_statistics(peers, "pe")
    assert ev.lower_bound_tickers == ["BOUND"]
    assert pe.lower_bound_tickers == []


def test_lower_bound_company_without_ev_multiple_is_not_listed() -> None:
    m = make_metrics(ticker="BOUND", total_debt_is_lower_bound=True, ebitda=None)
    mu = compute_multiples(m)
    assert mu.ev_ebitda is None
    assert compute_multiple_statistics([mu], "ev_ebitda").lower_bound_tickers == []


# --- QualityWarning ----------------------------------------------------------------------------


def test_warning_is_emitted_with_severity_warning_and_names_the_field() -> None:
    warnings = check_debt_lower_bound(make_metrics(ticker="CDNS", total_debt_is_lower_bound=True))
    assert len(warnings) == 1
    w = warnings[0]
    assert w.severity == "warning" and w.company == "CDNS" and w.affected_field == "total_debt"
    assert "Untergrenze" in w.message and "P/E ist nicht betroffen" in w.message


def test_warning_does_not_claim_an_ev_when_none_was_computed() -> None:
    # TEAM: Schuld ist Untergrenze, aber ohne Marktdaten gibt es keinen EV.
    m = make_metrics(ticker="TEAM", total_debt_is_lower_bound=True, has_market=False)
    assert m.enterprise_value is None
    message = check_debt_lower_bound(m)[0].message
    assert "nicht berechnet" in message and "sind damit ebenfalls Untergrenzen" not in message


def test_warning_names_the_concept_when_available() -> None:
    m = _build({"UnsecuredLongTermDebt": [entry(FY_END, 2480)]})
    assert "UnsecuredLongTermDebt" in check_debt_lower_bound(m)[0].message


def test_no_warning_for_exact_total() -> None:
    assert check_debt_lower_bound(make_metrics()) == []


def test_run_quality_checks_covers_target_and_peers() -> None:
    target = make_metrics(ticker="TGT", total_debt_is_lower_bound=True)
    peer = make_metrics(ticker="PEER", cik="0000000002", total_debt_is_lower_bound=True)
    exact = make_metrics(ticker="OK", cik="0000000003")
    companies = {
        w.company for w in run_quality_checks(target, [peer, exact], [compute_multiples(p) for p in (peer, exact)])
        if w.affected_field == "total_debt"
    }
    assert companies == {"TGT", "PEER"}


# --- Ende-zu-Ende ------------------------------------------------------------------------------


def test_comps_table_exposes_flags_in_multiples_statistics_and_warnings() -> None:
    target = make_metrics(ticker="TGT", cik="0000000001")
    exact = make_metrics(ticker="EXACT", cik="0000000002")
    bound = make_metrics(ticker="BOUND", cik="0000000003", total_debt_is_lower_bound=True)

    table = build_comps_table(target, [exact, bound])

    by_ticker = {m.company_ticker: m for m in table.peer_multiples}
    assert by_ticker["BOUND"].ev_multiples_are_lower_bound and not by_ticker["EXACT"].ev_multiples_are_lower_bound
    ev_stats = next(s for s in table.statistics if s.multiple_name == "ev_revenue")
    assert ev_stats.lower_bound_tickers == ["BOUND"] and ev_stats.count_included == 2
    assert any(w.company == "BOUND" and w.affected_field == "total_debt" for w in table.warnings)
    assert table.peers[1].total_debt_is_lower_bound is True
