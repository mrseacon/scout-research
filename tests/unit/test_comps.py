import pytest

from scout_research.domain.comps import build_comps_table
from scout_research.domain.multiples import MULTIPLE_NAMES
from tests.unit.factories import make_metrics


def test_build_comps_table_end_to_end() -> None:
    target = make_metrics(
        ticker="TGT", cik="0000000001", period_end="2025-12-31", fiscal_year=2025,
        revenue=1000.0, ebit=200.0, ebitda=250.0, net_income=150.0,
    )
    peer_a = make_metrics(
        ticker="PEERA", cik="0000000002", period_end="2025-12-31", fiscal_year=2025,
        revenue=900.0, ebit=180.0, ebitda=220.0, net_income=130.0,
    )
    peer_b = make_metrics(
        ticker="PEERB", cik="0000000003", period_end="2026-03-31", fiscal_year=2026,
        revenue=1100.0, ebit=-20.0, ebitda=240.0, net_income=140.0,  # negatives EBIT
    )

    table = build_comps_table(target, [peer_a, peer_b])

    assert table.target.company.tickers == ["TGT"]
    assert len(table.peers) == 2
    assert table.target_multiples.company_ticker == "TGT"
    assert len(table.peer_multiples) == 2

    # negatives EBIT beim Peer -> ev_ebit ausgeschlossen, nicht als negative Zahl
    peer_b_multiples = next(m for m in table.peer_multiples if m.company_ticker == "PEERB")
    assert peer_b_multiples.ev_ebit is None
    assert "EBIT <= 0" in peer_b_multiples.excluded_reasons["ev_ebit"]

    assert {s.multiple_name for s in table.statistics} == set(MULTIPLE_NAMES)

    # Fiskaljahresende-Mismatch (Dez vs. Maerz) muss geflaggt sein
    assert any("Fiskaljahresende" in w.message for w in table.warnings)
    assert "PEERB" in table.period_basis or "variieren" in table.period_basis


def test_build_comps_table_requires_at_least_one_peer() -> None:
    target = make_metrics()
    with pytest.raises(ValueError):
        build_comps_table(target, [])


def test_build_comps_table_same_fiscal_year_period_basis_is_clean() -> None:
    target = make_metrics(ticker="TGT", period_end="2025-12-31", fiscal_year=2025)
    peer = make_metrics(ticker="PEER", cik="0000000002", period_end="2025-12-31", fiscal_year=2025)

    table = build_comps_table(target, [peer])

    assert table.period_basis == "FY2025 (Ziel)"
