from datetime import date

import pytest

from scout_research.domain.multiples import compute_multiples
from scout_research.domain.quality import (
    PERIOD_TOLERANCE_DAYS,
    STALE_PERIOD_DAYS,
    check_fiscal_year_mismatch,
    check_missing_data,
    check_outliers,
    check_stale_period,
    folded_offset_days,
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


def test_folded_offset_ignores_whole_years() -> None:
    assert folded_offset_days(date(2026, 1, 31), date(2025, 2, 1)) == -1  # 52/53-Wochen-Jahr, 364 Tage
    assert folded_offset_days(date(2026, 2, 1), date(2025, 1, 31)) == 1
    assert folded_offset_days(date(2025, 12, 31), date(2025, 9, 27)) == 95
    assert folded_offset_days(date(2025, 12, 31), date(2025, 12, 31)) == 0
    assert -182 <= folded_offset_days(date(2025, 6, 30), date(2025, 12, 31)) <= 182


@pytest.mark.parametrize(
    "peer_end,expect_warning",
    [
        ("2025-12-28", False),  # 3 Tage: 52/53-Wochen-Jitter
        ("2026-01-01", False),  # Monatsgrenze, 1 Tag
        ("2026-01-14", False),  # genau an der Toleranz (14 Tage)
        ("2026-01-15", True),   # 15 Tage: darüber
        ("2026-01-31", True),   # 31 Tage
        ("2025-06-30", True),   # ~halbes Jahr versetzt
    ],
)
def test_check_fiscal_year_mismatch_uses_a_day_tolerance_not_the_month(peer_end: str, expect_warning: bool) -> None:
    target = make_metrics(ticker="TGT", period_end="2025-12-31")
    peer = make_metrics(ticker="PEER", period_end=peer_end, cik="0000000002")

    warnings = check_fiscal_year_mismatch(target, [peer])

    assert (len(warnings) == 1) == expect_warning
    if warnings:
        assert warnings[0].company == "PEER" and warnings[0].severity == "warning"
        assert "Fiskaljahresende weicht ab" in warnings[0].message
        assert warnings[0].affected_field == "period_end"


def test_52_53_week_year_ends_across_a_month_boundary_raise_no_false_alarm() -> None:
    # Ziel endet am 01.02.2026 (53-Wochen-Jahr), Peer am 31.01.2026 bzw. 25.01.2026 (letzte Sonntage/Samstage)
    target = make_metrics(ticker="TGT", period_end="2026-02-01")
    peers = [
        make_metrics(ticker="JAN31", period_end="2026-01-31", cik="0000000002"),
        make_metrics(ticker="JAN25", period_end="2026-01-25", cik="0000000003"),
    ]
    assert check_fiscal_year_mismatch(target, peers) == []
    assert check_stale_period(target, peers) == []


def test_peer_with_previous_years_10k_in_the_same_month_is_flagged_as_stale_not_as_misaligned() -> None:
    target = make_metrics(ticker="TGT", period_end="2025-09-27")
    old_peer = make_metrics(ticker="OLD", period_end="2024-09-28", cik="0000000002")  # ein Jahr alt, gleicher Monat

    assert check_fiscal_year_mismatch(target, [old_peer]) == []  # Abstand gefaltet 1 Tag: ausgerichtet
    warnings = check_stale_period(target, [old_peer])

    assert len(warnings) == 1 and warnings[0].company == "OLD" and warnings[0].severity == "warning"
    assert "Veraltetes 10-K" in warnings[0].message and "2024-09-28" in warnings[0].message


@pytest.mark.parametrize(
    "peer_end,expect_stale",
    [("2025-03-04", False), ("2025-03-03", True), ("2025-12-31", False), ("2026-06-30", False)],
)
def test_stale_threshold_is_300_days_and_ignores_newer_peers(peer_end: str, expect_stale: bool) -> None:
    target = make_metrics(ticker="TGT", period_end="2025-12-28")  # 300 Tage nach 2025-03-03
    peer = make_metrics(ticker="PEER", period_end=peer_end, cik="0000000002")

    assert bool(check_stale_period(target, [peer])) == expect_stale


def test_misaligned_and_stale_peer_gets_both_warnings() -> None:
    target = make_metrics(ticker="TGT", period_end="2025-12-31")
    peer = make_metrics(ticker="BOTH", period_end="2024-06-30", cik="0000000002")

    messages = [w.message for w in check_fiscal_year_mismatch(target, [peer]) + check_stale_period(target, [peer])]
    assert any("Fiskaljahresende weicht ab" in m for m in messages)
    assert any("Veraltetes 10-K" in m for m in messages)


def test_thresholds_are_the_decided_values() -> None:
    assert (PERIOD_TOLERANCE_DAYS, STALE_PERIOD_DAYS) == (14, 300)


def test_run_quality_checks_includes_the_stale_period_check() -> None:
    target = make_metrics(ticker="TGT", period_end="2025-12-31")
    old_peer = make_metrics(ticker="OLD", period_end="2024-12-31", cik="0000000002")
    warnings = run_quality_checks(target, [old_peer], [compute_multiples(old_peer)])
    assert any("Veraltetes 10-K" in w.message for w in warnings)


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


# --- E4: jede Warnung trägt Art und Parameter für den Tool-Layer ---------------------------------------------


def test_every_warning_kind_is_set_and_carries_its_parameters() -> None:
    target = make_metrics(ticker="TGT", period_end="2025-12-31")
    peers = [
        make_metrics(ticker="MIS", cik="0000000002", period_end="2025-06-30"),
        make_metrics(ticker="OLD", cik="0000000003", period_end="2024-12-31", ebit=None),
        make_metrics(ticker="DBT", cik="0000000004", total_debt_is_lower_bound=True),
    ]
    peer_multiples = [compute_multiples(p) for p in peers]

    warnings = run_quality_checks(target, peers, peer_multiples)

    assert all(w.kind for w in warnings)
    by_kind = {w.kind: w for w in warnings if w.company in ("MIS", "OLD", "DBT")}
    assert by_kind["fiscal_year_mismatch"].params["offset_days"] == folded_offset_days(
        date(2025, 6, 30), date(2025, 12, 31)
    )
    assert by_kind["stale_period"].params["gap_days"] == 365
    assert by_kind["missing_data"].params == {"field": "ebit"}
    assert by_kind["debt_lower_bound"].params["ev_available"] is True
    assert any(w.kind == "too_few_datapoints" for w in warnings)


def test_outlier_warning_has_direction_and_fences() -> None:
    peer_multiples = [
        _pe_multiples("A", price=100.0, shares=10.0, net_income=100.0),
        _pe_multiples("B", price=110.0, shares=10.0, net_income=100.0),
        _pe_multiples("C", price=95.0, shares=10.0, net_income=100.0),
        _pe_multiples("D", price=1000.0, shares=10.0, net_income=100.0),
    ]
    (warning,) = check_outliers(peer_multiples, "pe")
    assert warning.kind == "outlier"
    assert warning.params["direction"] == "above" and warning.params["multiple"] == "pe"
    assert warning.params["upper_fence"] < warning.params["value"]
