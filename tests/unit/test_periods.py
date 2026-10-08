"""Periodenauswahl (D9, Foundation Doc 7.3.1): PeriodSelector, PeriodNotAvailable, historische Perioden,
calendar_year mit Frame-Prüfung. Offline gegen Fixtures (scripts/make_fixture.py)."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from eval.golden_seed import load_seed, seed_files
from eval.seed_compare import load_fixture
from scout_research.data.edgar_client import CompanyMetadata, EdgarClient
from scout_research.data.market_provider import PriceQuote
from scout_research.domain.comps import build_comps_table
from scout_research.domain.metrics import (
    REVENUE_CONCEPTS,
    RevenueNotFoundError,
    available_period_ends,
    build_company_metrics,
)
from scout_research.domain.multiples import MULTIPLE_NAMES, compute_multiples
from scout_research.domain.peers import FrameYearUnresolved, resolve_calendar_year
from scout_research.domain.periods import (
    HistoricalValuationNotSupported,
    PeriodNotAvailable,
    PeriodSelector,
    derive_calendar_year,
)
from tests.unit.factories import make_metrics

SEEDS = {p.stem: load_seed(p) for p in seed_files()}
QUOTE = PriceQuote(ticker="TST", price=100.0, as_of_date="2026-10-05", source="test")


def _metadata(cik: str) -> CompanyMetadata:
    return CompanyMetadata(
        cik=cik, name="Test", sic_code=None, sic_description=None, fiscal_year_end=None, tickers=["TST"], exchanges=[]
    )


def _build(ticker: str, period: PeriodSelector | None):
    seed = SEEDS[ticker]
    return build_company_metrics(seed["cik"], _metadata(seed["cik"]), load_fixture(ticker), QUOTE, period)


# --- PeriodSelector -----------------------------------------------------------------------------------------


def test_period_selector_allows_at_most_one_field() -> None:
    assert PeriodSelector().is_latest
    assert PeriodSelector(period_end=date(2025, 9, 27)).period_end == date(2025, 9, 27)
    assert PeriodSelector(fiscal_year=2025).fiscal_year == 2025
    with pytest.raises(ValidationError):
        PeriodSelector(period_end=date(2025, 9, 27), fiscal_year=2025)
    with pytest.raises(ValidationError):
        PeriodSelector(period_end="kein-datum")
    with pytest.raises(ValidationError):
        PeriodSelector(unbekannt=1)


# --- calendar_year ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "period_end,expected",
    [
        ("2025-09-27", 2025),  # AAPL, 52/53-Wochen-Jahr, letzter Samstag im September
        ("2026-01-25", 2025),  # NVDA, 52/53-Wochen-Jahr, letzter Sonntag im Januar
        ("2026-01-31", 2025),  # ADSK, 31. Januar
        ("2026-06-30", 2026),  # MSFT, 30. Juni: Grenzfall, 180 Tage zurück = 1. Januar
        ("2025-12-31", 2025),  # CDNS, Kalenderjahr
        ("2026-06-27", 2025),  # Ende 27. Juni (SYY-artig) -> Vorjahr
        ("2026-07-03", 2026),  # 3. Juli (STX-artig)
    ],
)
def test_derive_calendar_year_is_year_of_period_end_minus_180_days(period_end: str, expected: int) -> None:
    assert derive_calendar_year(period_end) == expected
    assert derive_calendar_year(date.fromisoformat(period_end)) == expected


def _frames_client(by_year: dict[int, dict[int, str]]) -> EdgarClient:
    """`by_year[Jahr][cik] = Periodenende` -> Frame mit einem Eintrag je Firma."""
    requested: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        year = int(str(request.url).rsplit("CY", 1)[1].split(".")[0])
        requested.append(year)
        data = [
            {"cik": cik, "entityName": "X", "start": "2025-01-01", "end": end, "val": 1.0, "accn": "0000000000-26-000001"}
            for cik, end in by_year.get(year, {}).items()
        ]
        return httpx.Response(200, text=json.dumps({"data": data}))

    client = EdgarClient(user_agent="Scout Research Tests test@example.com", transport=httpx.MockTransport(handler))
    client.requested_years = requested  # type: ignore[attr-defined]
    return client


def test_resolve_calendar_year_uses_derived_year_when_target_is_in_frame() -> None:
    with _frames_client({2025: {320193: "2025-09-27"}}) as client:
        resolution, frame = resolve_calendar_year(client, "0000320193", "2025-09-27")

    assert (resolution.calendar_year, resolution.frame_check) == (2025, "target_in_frame")
    assert "0000320193" in frame
    assert client.requested_years == [2025]  # kein überflüssiger Abruf


def test_resolve_calendar_year_falls_back_to_neighbor_year_and_reports_it() -> None:
    with _frames_client({2024: {789019: "2025-06-30"}, 2025: {789019: "2026-06-30"}}) as client:
        resolution, _ = resolve_calendar_year(client, "0000789019", "2025-06-30")

    assert (resolution.calendar_year, resolution.frame_check) == (2024, "neighbor_year")


def test_resolve_calendar_year_requires_matching_period_end_not_just_presence() -> None:
    # Im abgeleiteten Jahr steht das Ziel, aber mit einem anderen Geschäftsjahresende -> kein Treffer.
    with _frames_client({2025: {320193: "2024-09-28"}}) as client:
        with pytest.raises(FrameYearUnresolved) as exc:
            resolve_calendar_year(client, "0000320193", "2025-09-27")

    assert exc.value.code == "FRAME_YEAR_UNRESOLVED"
    assert exc.value.tried_years == [2025, 2024, 2026]


# --- Auswahl der Periode ------------------------------------------------------------------------------------


@pytest.mark.parametrize("ticker", sorted(SEEDS))
def test_period_end_selects_exactly_the_pinned_filing(ticker: str) -> None:
    seed = SEEDS[ticker]
    metrics = _build(ticker, PeriodSelector(period_end=seed["period_end"]))

    assert metrics.period_end == str(seed["period_end"])
    assert metrics.fiscal_year == seed["fiscal_year"]
    assert metrics.is_historical is False
    assert {f.accession_number for f in metrics.source_facts} == {seed["accession_number"]}


@pytest.mark.parametrize("ticker", sorted(SEEDS))
def test_fiscal_year_selects_the_same_period_best_effort(ticker: str) -> None:
    seed = SEEDS[ticker]
    by_fy = _build(ticker, PeriodSelector(fiscal_year=seed["fiscal_year"]))
    by_end = _build(ticker, PeriodSelector(period_end=seed["period_end"]))

    assert by_fy.period_end == by_end.period_end
    assert by_fy.revenue == by_end.revenue


@pytest.mark.parametrize("ticker", sorted(SEEDS))
def test_default_period_is_the_latest_and_equals_an_empty_selector(ticker: str) -> None:
    default = _build(ticker, None)
    empty = _build(ticker, PeriodSelector())

    assert default.model_dump(exclude={"source_facts"}) == empty.model_dump(exclude={"source_facts"})
    assert default.period_end == available_period_ends(load_fixture(ticker))[0]


@pytest.mark.parametrize(
    "selector",
    [PeriodSelector(period_end=date(2010, 1, 2)), PeriodSelector(fiscal_year=1999)],
    ids=["period_end", "fiscal_year"],
)
def test_missing_period_raises_with_available_period_ends_never_a_neighbor(selector: PeriodSelector) -> None:
    with pytest.raises(PeriodNotAvailable) as exc:
        _build("aapl", selector)

    error = exc.value
    assert error.code == "PERIOD_NOT_AVAILABLE"
    assert error.available_period_ends == available_period_ends(load_fixture("aapl"))
    assert str(SEEDS["aapl"]["period_end"]) in error.available_period_ends
    assert error.available_period_ends == sorted(error.available_period_ends, reverse=True)
    assert error.available_period_ends[0] in str(error)


def test_company_without_revenue_still_raises_revenue_not_found_for_latest() -> None:
    facts = {"facts": {"us-gaap": {}, "dei": {}}}
    with pytest.raises(RevenueNotFoundError):
        build_company_metrics("0000000001", _metadata("0000000001"), facts, QUOTE)
    assert available_period_ends(facts) == []


# --- historische Perioden -----------------------------------------------------------------------------------


def test_historical_period_gives_financials_but_no_market_data_or_multiples() -> None:
    latest = _build("aapl", None)
    older_end = available_period_ends(load_fixture("aapl"))[1]
    historical = _build("aapl", PeriodSelector(period_end=older_end))

    assert older_end < latest.period_end
    assert historical.is_historical is True
    assert historical.period_end == older_end
    assert historical.revenue is not None and historical.ebit is not None
    assert historical.market is None  # trotz übergebenem Kurs: der Kurs ist der heutige
    assert historical.enterprise_value is None
    assert historical.margins["ebit_margin"] is not None
    # Periodentreue und Filingtreue: alle Fakten gehören zur gewählten Periode und zu einem 10-K
    revenue_fact = next(f for f in historical.source_facts if f.concept in REVENUE_CONCEPTS)
    assert {f.accession_number for f in historical.source_facts} == {revenue_fact.accession_number}
    assert {f.period_end for f in historical.source_facts if f.concept != "EntityCommonStockSharesOutstanding"} == {
        older_end
    }


def test_historical_growth_is_relative_to_the_selected_period_not_the_latest() -> None:
    older_end = available_period_ends(load_fixture("aapl"))[1]
    historical = _build("aapl", PeriodSelector(period_end=older_end))
    latest = _build("aapl", None)

    assert historical.growth_rates["revenue_yoy"] is not None
    assert historical.growth_rates["revenue_yoy"] != latest.growth_rates["revenue_yoy"]


def test_historical_metrics_have_no_multiples_with_an_explicit_reason() -> None:
    older_end = available_period_ends(load_fixture("aapl"))[1]
    multiples = compute_multiples(_build("aapl", PeriodSelector(period_end=older_end)))

    assert all(getattr(multiples, name) is None for name in MULTIPLE_NAMES)
    assert set(multiples.excluded_reasons) == set(MULTIPLE_NAMES)
    assert all("Historische Periode" in reason for reason in multiples.excluded_reasons.values())


def test_comps_table_rejects_historical_target_or_peer() -> None:
    older_end = available_period_ends(load_fixture("aapl"))[1]
    historical = _build("aapl", PeriodSelector(period_end=older_end))
    current = make_metrics(ticker="PEER")

    for target, peers in ((historical, [current]), (current, [historical])):
        with pytest.raises(HistoricalValuationNotSupported) as exc:
            build_comps_table(target, peers)
        assert exc.value.code == "HISTORICAL_VALUATION_NOT_SUPPORTED"
        assert exc.value.tickers == ["TST"]


def test_current_periods_still_build_a_comps_table() -> None:
    table = build_comps_table(make_metrics(ticker="AAA"), [make_metrics(ticker="BBB")])
    assert table.target.is_historical is False


# --- Eindeutigkeit mehrerer Filings -------------------------------------------------------------------------


def test_same_period_in_two_filings_uses_the_original_10k_and_never_mixes_filings() -> None:
    def entry(value: float, accn: str, filed: str, fy: int) -> dict:
        return {
            "start": "2024-10-01", "end": "2025-09-30", "val": value, "accn": accn,
            "fy": fy, "fp": "FY", "form": "10-K", "filed": filed,
        }

    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [
                    entry(100.0, "0000000001-25-000001", "2025-11-01", 2025),   # Original
                    entry(105.0, "0000000001-26-000002", "2026-11-01", 2026),   # korrigierter Vergleichswert
                ]}},
                "OperatingIncomeLoss": {"units": {"USD": [
                    entry(10.0, "0000000001-25-000001", "2025-11-01", 2025),
                    entry(11.0, "0000000001-26-000002", "2026-11-01", 2026),
                ]}},
                "NetIncomeLoss": {"units": {"USD": [  # nur im späteren 10-K vorhanden -> gilt als nicht vorhanden
                    entry(7.0, "0000000001-26-000002", "2026-11-01", 2026),
                ]}},
            },
            "dei": {},
        }
    }
    # Die Periode ist für beide Filings dieselbe; später gibt es ein weiteres Geschäftsjahr.
    facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        {"start": "2025-10-01", "end": "2026-09-30", "val": 120.0, "accn": "0000000001-26-000002",
         "fy": 2026, "fp": "FY", "form": "10-K", "filed": "2026-11-01"}
    )

    metrics = build_company_metrics(
        "0000000001", _metadata("0000000001"), facts, None, PeriodSelector(period_end=date(2025, 9, 30))
    )

    assert (metrics.revenue, metrics.ebit) == (100.0, 10.0)  # beides aus dem Original-10-K
    assert metrics.net_income is None  # nicht aus einem späteren Filing aufgefüllt
    assert {f.accession_number for f in metrics.source_facts} == {"0000000001-25-000001"}
    assert metrics.is_historical is True
