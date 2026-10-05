"""Prüft, dass der Fixture-Zuschnitt (scripts/make_fixture.py) das Extraktor-Verhalten nicht ändert."""

import importlib.util
import json
from pathlib import Path

import pytest

from scout_research.data.edgar_client import CompanyMetadata
from scout_research.data.market_provider import PriceQuote
from scout_research.domain.metrics import build_company_metrics

ROOT = Path(__file__).parent.parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SLIM_FIXTURES = sorted(FIXTURES.glob("*_companyfacts_slim.json"))

_spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "scripts" / "make_fixture.py")
make_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_fixture)


def _metadata(cik: str) -> CompanyMetadata:
    return CompanyMetadata(
        cik=cik, name="Test", sic_code=None, sic_description=None, fiscal_year_end=None, tickers=["TST"], exchanges=[]
    )


def _metrics_dump(cik: str, facts: dict) -> dict:
    quote = PriceQuote(ticker="TST", price=100.0, as_of_date="2026-10-05", source="test")
    metrics = build_company_metrics(cik, _metadata(cik), facts, quote)
    dump = metrics.model_dump(mode="json")
    for fact in dump["source_facts"]:
        fact.pop("retrieved_at")  # Abrufzeitpunkt ist je Aufruf verschieden
    return dump


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_slimming_the_full_aapl_fixture_does_not_change_extractor_output() -> None:
    full = _load("aapl_companyfacts_full.json")
    slim = make_fixture.slim_companyfacts(full, "file:test", "2026-10-05T00:00:00+00:00")

    assert _metrics_dump("0000320193", slim) == _metrics_dump("0000320193", full)


def test_slimming_keeps_structure_and_documents_origin() -> None:
    full = _load("aapl_companyfacts_full.json")
    slim = make_fixture.slim_companyfacts(full, "file:test", "2026-10-05T00:00:00+00:00")

    assert slim["_fixture"]["source_url"] == "file:test"
    assert slim["_fixture"]["retrieved_at"] == "2026-10-05T00:00:00+00:00"
    assert set(slim["facts"]) == {"us-gaap", "dei"}
    kept = slim["facts"]["us-gaap"]["Assets"]
    assert kept == full["facts"]["us-gaap"]["Assets"]  # Einträge je Konzept unverändert


def test_slimming_drops_unused_concepts() -> None:
    raw = {
        "cik": 1,
        "entityName": "X",
        "facts": {
            "us-gaap": {"Assets": {"units": {}}, "SomethingUnused": {"units": {}}},
            "dei": {"EntityCommonStockSharesOutstanding": {"units": {}}, "EntityPublicFloat": {"units": {}}},
            "srt": {"Whatever": {"units": {}}},
        },
    }
    slim = make_fixture.slim_companyfacts(raw, "file:test", "2026-10-05T00:00:00+00:00")

    assert set(slim["facts"]["us-gaap"]) == {"Assets"}
    assert set(slim["facts"]["dei"]) == {"EntityCommonStockSharesOutstanding"}
    assert "srt" not in slim["facts"]


@pytest.mark.parametrize("path", SLIM_FIXTURES, ids=lambda p: p.name.split("_")[0])
def test_committed_slim_fixtures_are_documented_and_small(path: Path) -> None:
    slim = json.loads(path.read_text(encoding="utf-8"))

    assert slim["_fixture"]["source_url"].startswith("https://data.sec.gov/api/xbrl/companyfacts/CIK")
    assert slim["_fixture"]["retrieved_at"]
    assert path.stat().st_size < make_fixture.MAX_BYTES


@pytest.mark.parametrize("path", SLIM_FIXTURES, ids=lambda p: p.name.split("_")[0])
def test_committed_slim_fixtures_are_anchored_to_one_period(path: Path) -> None:
    slim = json.loads(path.read_text(encoding="utf-8"))
    dump = _metrics_dump(f"{int(slim['cik']):010d}", slim)

    assert dump["revenue"] is not None
    anchored = [f for f in dump["source_facts"] if f["concept"] != "EntityCommonStockSharesOutstanding"]
    assert {f["period_end"] for f in anchored} == {dump["period_end"]}


def test_slim_aapl_contains_every_entry_of_the_older_aapl_fixture() -> None:
    """`aapl_companyfacts_full.json` ist nicht vollständig (nur 8 us-gaap-Konzepte, noch aus Phase 1) und
    kennt z. B. `CommercialPaper`/`LongTermDebt` nicht. Die frisch abgerufene, zugeschnittene Fixture muss
    aber jedes Konzept der älteren Datei mit identischen Einträgen enthalten — ein Unterschied im
    Extraktor-Ergebnis stammt dann nur aus den zusätzlichen Konzepten, nicht aus geänderten Daten.
    (Einmalig gegen einen frischen SEC-Abruf geprüft: Zuschnitt und Rohdaten liefern identische Metriken.)"""
    slim = _load("aapl_companyfacts_slim.json")
    old = _load("aapl_companyfacts_full.json")

    for taxonomy, concepts in old["facts"].items():
        for concept, value in concepts.items():
            assert slim["facts"][taxonomy].get(concept) == value, f"{taxonomy}/{concept} weicht ab"
