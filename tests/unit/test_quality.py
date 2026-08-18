from scout_research.domain.multiples import compute_multiples
from scout_research.domain.quality import (
    check_fiscal_year_mismatch,
    check_missing_data,
    check_outliers,
    run_quality_checks,
)
from tests.unit.factories import make_metrics


def _pe_multiples(ticker: str, price: float, shares: float, net_income: float):
    metrics = make_metrics(ticker=ticker, price=price, shares_outstanding=shares, net_income=net_income)
    return compute_multiples(metrics)


def test_check_outliers_flags_the_extreme_value() -> None:
    # P/E ~10x fuer drei Peers, einer bei ~100x -> klarer IQR-Ausreisser
    peer_multiples = [
        _pe_multiples("A", price=100.0, shares=10.0, net_income=100.0),   # PE = 10
        _pe_multiples("B", price=110.0, shares=10.0, net_income=100.0),   # PE = 11
        _pe_multiples("C", price=95.0, shares=10.0, net_income=100.0),    # PE = 9.5
        _pe_multiples("D", price=1000.0, shares=10.0, net_income=100.0),  # PE = 100 (Ausreisser)
    ]

    warnings = check_outliers(peer_multiples, "pe")

    assert len(warnings) == 1
    assert warnings[0].company == "D"
    assert warnings[0].severity == "warning"


def test_check_outliers_below_min_datapoints_returns_info_warning() -> None:
    peer_multiples = [
        _pe_multiples("A", price=100.0, shares=10.0, net_income=100.0),
        _pe_multiples("B", price=110.0, shares=10.0, net_income=100.0),
    ]

    warnings = check_outliers(peer_multiples, "pe")

    assert len(warnings) == 1
    assert warnings[0].severity == "info"
    assert warnings[0].company == "Peer-Set"


def test_check_outliers_no_data_returns_empty() -> None:
    peer_multiples = [_pe_multiples("A", price=100.0, shares=10.0, net_income=-5.0)]  # PE ausgeschlossen

    assert check_outliers(peer_multiples, "pe") == []


def test_check_fiscal_year_mismatch_flags_different_month_only() -> None:
    target = make_metrics(ticker="TGT", period_end="2025-12-31")
    same_month_peer = make_metrics(ticker="SAME", period_end="2025-12-28")
    different_month_peer = make_metrics(ticker="DIFF", period_end="2026-01-31")

    warnings = check_fiscal_year_mismatch(target, [same_month_peer, different_month_peer])

    assert len(warnings) == 1
    assert warnings[0].company == "DIFF"
    assert "Fiskaljahresende" in warnings[0].message


def test_check_missing_data_flags_none_fields_with_correct_severity() -> None:
    metrics = make_metrics(ebitda=None, total_debt=None, has_market=False)

    warnings = check_missing_data(metrics)
    by_field = {w.affected_field: w for w in warnings}

    assert by_field["ebitda"].severity == "info"
    assert by_field["total_debt"].severity == "info"
    assert by_field["market"].severity == "warning"
    assert "revenue" not in by_field  # Revenue ist in diesem Testfall vorhanden


def test_check_missing_data_no_warnings_when_complete() -> None:
    metrics = make_metrics()
    assert check_missing_data(metrics) == []


def test_run_quality_checks_combines_all_checks() -> None:
    target = make_metrics(ticker="TGT", period_end="2025-12-31", cik="0000000001")
    mismatched_peer = make_metrics(ticker="MISMATCH", period_end="2025-06-30", cik="0000000002")
    incomplete_peer = make_metrics(ticker="INCOMPLETE", cik="0000000003", ebitda=None, has_market=False)
    peers = [mismatched_peer, incomplete_peer]
    peer_multiples = [compute_multiples(p) for p in peers]

    warnings = run_quality_checks(target, peers, peer_multiples)

    companies_warned = {w.company for w in warnings}
    assert "MISMATCH" in companies_warned
    assert "INCOMPLETE" in companies_warned
    assert len(warnings) > 0
