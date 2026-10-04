"""Test-Hilfsfunktionen zum Bauen synthetischer CompanyMetrics — kein pytest-Testmodul."""

from __future__ import annotations

from scout_research.data.edgar_client import CompanyMetadata
from scout_research.domain.models import CompanyMetrics, MarketSnapshot


def make_metrics(
    ticker: str = "TST",
    cik: str = "0000000001",
    name: str = "Test Inc.",
    period_end: str = "2025-12-31",
    fiscal_year: int = 2025,
    revenue: float | None = 1000.0,
    ebit: float | None = 200.0,
    ebitda: float | None = 250.0,
    ebitda_approximated: bool = True,
    net_income: float | None = 150.0,
    total_assets: float | None = 2000.0,
    total_debt: float | None = 300.0,
    cash: float | None = 100.0,
    price: float = 50.0,
    shares_outstanding: float = 100.0,
    has_market: bool = True,
    total_debt_is_lower_bound: bool = False,
) -> CompanyMetrics:
    company = CompanyMetadata(
        cik=cik,
        name=name,
        sic_code="7372",
        sic_description="Prepackaged Software",
        fiscal_year_end="1231",
        tickers=[ticker],
        exchanges=["Nasdaq"],
    )

    market: MarketSnapshot | None = None
    enterprise_value: float | None = None
    if has_market:
        market_cap = price * shares_outstanding
        market = MarketSnapshot(
            ticker=ticker,
            price=price,
            shares_outstanding=shares_outstanding,
            market_cap=market_cap,
            as_of_date="2026-08-18",
            source="finnhub",
        )
        if total_debt is not None and cash is not None:
            enterprise_value = market_cap + total_debt - cash

    return CompanyMetrics(
        company=company,
        period_end=period_end,
        fiscal_year=fiscal_year,
        revenue=revenue,
        ebit=ebit,
        ebitda=ebitda,
        ebitda_approximated=ebitda_approximated,
        net_income=net_income,
        total_assets=total_assets,
        total_debt=total_debt,
        total_debt_is_lower_bound=total_debt_is_lower_bound,
        cash=cash,
        market=market,
        enterprise_value=enterprise_value,
        enterprise_value_is_lower_bound=total_debt_is_lower_bound and enterprise_value is not None,
        margins={
            "ebit_margin": (ebit / revenue) if ebit is not None and revenue else None,
            "net_margin": (net_income / revenue) if net_income is not None and revenue else None,
        },
        growth_rates={"revenue_yoy": None},
        source_facts=[],
    )
