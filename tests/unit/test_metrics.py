import json
from pathlib import Path

import pytest

from scout_research.domain.metrics import RevenueNotFoundError, extract_latest_annual_revenue

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def aapl_facts() -> dict:
    with open(FIXTURES / "aapl_companyfacts_trimmed.json") as f:
        return json.load(f)


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
