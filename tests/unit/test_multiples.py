import pytest

from scout_research.domain.multiples import compute_multiple_statistics, compute_multiples
from tests.unit.factories import make_metrics


def test_compute_multiples_normal_case() -> None:
    metrics = make_metrics(
        ticker="ABC", revenue=1000.0, ebit=200.0, ebitda=250.0, net_income=150.0,
        total_debt=300.0, cash=100.0, price=50.0, shares_outstanding=100.0,
    )
    multiples = compute_multiples(metrics)

    ev = 50.0 * 100.0 + 300.0 - 100.0  # market_cap + debt - cash = 5200
    assert multiples.company_ticker == "ABC"
    assert multiples.ev_revenue == pytest.approx(ev / 1000.0)
    assert multiples.ev_ebitda == pytest.approx(ev / 250.0)
    assert multiples.ev_ebit == pytest.approx(ev / 200.0)
    assert multiples.pe == pytest.approx((50.0 * 100.0) / 150.0)
    assert multiples.excluded_reasons == {}


def test_negative_ebit_excludes_ev_ebit_not_a_negative_number() -> None:
    metrics = make_metrics(ebit=-50.0)
    multiples = compute_multiples(metrics)

    assert multiples.ev_ebit is None
    assert "EBIT <= 0" in multiples.excluded_reasons["ev_ebit"]
    # andere Multiples bleiben unberührt vom negativen EBIT
    assert multiples.ev_revenue is not None


def test_negative_net_income_excludes_pe() -> None:
    metrics = make_metrics(net_income=-10.0)
    multiples = compute_multiples(metrics)

    assert multiples.pe is None
    assert "Net Income <= 0" in multiples.excluded_reasons["pe"]


def test_missing_ebitda_excludes_ev_ebitda_with_reason() -> None:
    metrics = make_metrics(ebitda=None)
    multiples = compute_multiples(metrics)

    assert multiples.ev_ebitda is None
    assert multiples.excluded_reasons["ev_ebitda"] == "EBITDA nicht verfügbar"


def test_missing_market_data_excludes_all_ev_and_pe_multiples() -> None:
    metrics = make_metrics(has_market=False)
    multiples = compute_multiples(metrics)

    assert multiples.ev_revenue is None
    assert multiples.ev_ebitda is None
    assert multiples.ev_ebit is None
    assert multiples.pe is None
    assert "Enterprise Value nicht verfügbar" in multiples.excluded_reasons["ev_revenue"]
    assert "Marktdaten (Market Cap) nicht verfügbar" == multiples.excluded_reasons["pe"]


def test_zero_revenue_excludes_ev_revenue_without_division_by_zero() -> None:
    metrics = make_metrics(revenue=0.0)
    multiples = compute_multiples(metrics)

    assert multiples.ev_revenue is None
    assert "Revenue <= 0" in multiples.excluded_reasons["ev_revenue"]


def test_compute_multiple_statistics_excludes_none_values() -> None:
    peer_multiples = [
        compute_multiples(make_metrics(ticker="A", ebit=100.0)),
        compute_multiples(make_metrics(ticker="B", ebit=-50.0)),  # ev_ebit excluded
        compute_multiples(make_metrics(ticker="C", ebit=150.0)),
    ]

    stats = compute_multiple_statistics(peer_multiples, "ev_ebit")

    assert stats.count_included == 2
    assert stats.excluded_tickers == ["B"]
    assert stats.min is not None and stats.max is not None
    assert stats.median == pytest.approx(stats.mean, rel=0.5)  # beide aus 2 Werten ableitbar


def test_compute_multiple_statistics_all_excluded_returns_none_stats() -> None:
    peer_multiples = [compute_multiples(make_metrics(ticker="A", has_market=False))]

    stats = compute_multiple_statistics(peer_multiples, "pe")

    assert stats.count_included == 0
    assert stats.min is None
    assert stats.median is None
    assert stats.mean is None
    assert stats.max is None
    assert stats.excluded_tickers == ["A"]
