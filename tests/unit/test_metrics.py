import json
from pathlib import Path

import pytest

from scout_research.data.edgar_client import CompanyMetadata
from scout_research.data.market_provider import PriceQuote
from scout_research.domain.metrics import (
    RevenueNotFoundError,
    build_company_metrics,
    extract_latest_annual_revenue,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def aapl_facts() -> dict:
    with open(FIXTURES / "aapl_companyfacts_trimmed.json") as f:
        return json.load(f)


@pytest.fixture
def aapl_facts_full() -> dict:
    with open(FIXTURES / "aapl_companyfacts_full.json") as f:
        return json.load(f)


@pytest.fixture
def aapl_metadata() -> CompanyMetadata:
    return CompanyMetadata(
        cik="0000320193",
        name="Apple Inc.",
        sic_code="3571",
        sic_description="Electronic Computers",
        fiscal_year_end="0927",
        tickers=["AAPL"],
        exchanges=["Nasdaq"],
    )


def test_extract_latest_annual_revenue_has_full_provenance(aapl_facts: dict) -> None:
    fact = extract_latest_annual_revenue(cik="0000320193", company_facts=aapl_facts)

    assert fact.value == 416_161_000_000
    assert fact.unit == "USD"
    assert fact.fiscal_year == 2025
    assert fact.fiscal_period == "FY"
    assert fact.form_type == "10-K"
    assert fact.period_start == "2024-09-29"
    assert fact.period_end == "2025-09-27"
    assert fact.accession_number == "0000320193-25-000079"
    assert fact.filed_date == "2025-10-31"
    assert fact.concept == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_extract_latest_annual_revenue_picks_max_period_end(aapl_facts: dict) -> None:
    fact = extract_latest_annual_revenue(cik="0000320193", company_facts=aapl_facts)

    all_ends = [
        entry["end"]
        for entry in aapl_facts["facts"]["us-gaap"][fact.concept]["units"]["USD"]
        if entry.get("form") == "10-K" and entry.get("fp") == "FY"
    ]
    assert fact.period_end == max(all_ends)


def test_missing_revenue_concepts_raises() -> None:
    empty_facts = {"facts": {"us-gaap": {}}}

    with pytest.raises(RevenueNotFoundError):
        extract_latest_annual_revenue(cik="0000000000", company_facts=empty_facts)


def test_build_company_metrics_full_extraction(aapl_facts_full: dict, aapl_metadata: CompanyMetadata) -> None:
    price = PriceQuote(ticker="AAPL", price=230.50, as_of_date="2026-08-17", source="finnhub")

    metrics = build_company_metrics("0000320193", aapl_metadata, aapl_facts_full, price)

    assert metrics.revenue == 416_161_000_000
    assert metrics.ebit == 133_050_000_000
    assert metrics.net_income == 112_010_000_000
    assert metrics.ebitda_approximated is True
    assert metrics.ebitda is not None and metrics.ebitda > metrics.ebit

    assert metrics.market is not None
    assert metrics.market.market_cap == pytest.approx(230.50 * metrics.market.shares_outstanding)
    assert metrics.enterprise_value == pytest.approx(
        metrics.market.market_cap + metrics.total_debt - metrics.cash
    )

    assert metrics.margins["ebit_margin"] == pytest.approx(metrics.ebit / metrics.revenue)
    assert metrics.margins["net_margin"] == pytest.approx(metrics.net_income / metrics.revenue)

    assert len(metrics.source_facts) >= 6


def test_build_company_metrics_without_price_quote_omits_market_data(
    aapl_facts_full: dict, aapl_metadata: CompanyMetadata
) -> None:
    metrics = build_company_metrics("0000320193", aapl_metadata, aapl_facts_full, price_quote=None)

    assert metrics.market is None
    assert metrics.enterprise_value is None
    assert metrics.revenue is not None
