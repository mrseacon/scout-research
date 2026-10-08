"""Validierung des Golden-Set-Seeds (Messlineal für Phase 3, Schritt 2).

Unvollständige Seed-Dateien (`status: skeleton`) werden *gemeldet* (Warnung mit allen Lücken), brechen die
Suite aber nicht — solange Sean die Werte noch einträgt. Eine Datei mit `status: complete` muss dagegen
lückenlos sein, sonst schlägt der Test fehl. Die Reproduktionstests gegen den Extraktor überspringen
Firmen ohne vollständigen Seed.
"""

import copy
import json
import warnings
from decimal import Decimal
from pathlib import Path

import pytest

from eval.golden_seed import CORE_VALUES, load_seed, seed_files, to_usd, tolerance_usd, validate_seed

FIXTURES = Path(__file__).parent.parent / "fixtures"
EXPECTED = ["aapl", "adsk", "cdns", "msft", "nvda"]


def _complete() -> dict:
    values = {
        name: {"value": 1234.5, "unit": "millions", "source": "Income Statement, S. 28"} for name in CORE_VALUES
    }
    values["shares_outstanding"]["unit"] = "units"
    values["total_debt"]["is_lower_bound"] = False
    values["d_and_a"] = {"absent_reason": "kein Gesamt-D&A ausgewiesen", "source": "Cashflow, S. 31"}
    return {
        "status": "complete",
        "ticker": "TST",
        "cik": "0000000001",
        "period_end": "2025-09-27",
        "fiscal_year": 2025,
        "accession_number": "0000320193-25-000079",
        "checked_by": "Sean",
        "checked_on": "2026-10-06",
        "values": values,
    }


def test_complete_seed_has_no_problems() -> None:
    assert validate_seed(_complete()) == []


def test_empty_skeleton_reports_every_missing_pin_and_value() -> None:
    skeleton = {"status": "skeleton", "ticker": "TST", "cik": "0000000001", "values": {}}
    problems = validate_seed(skeleton)

    assert any("period_end" in p for p in problems)
    assert any("accession_number" in p for p in problems)
    assert any("fiscal_year" in p for p in problems)
    assert any("checked_by" in p for p in problems)
    assert any("values" in p for p in problems)


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda d: d.pop("period_end"), "period_end fehlt"),
        (lambda d: d.update(period_end="27.09.2025"), "kein ISO-Datum"),
        (lambda d: d.pop("accession_number"), "accession_number fehlt"),
        (lambda d: d.update(accession_number="320193-25-79"), "Format"),
        (lambda d: d.update(fiscal_year="2025"), "fiscal_year"),
        (lambda d: d["values"].pop("cash"), "values.cash fehlt"),
        (lambda d: d["values"]["revenue"].update(value=None), "values.revenue: weder value noch absent_reason"),
        (lambda d: d["values"]["revenue"].update(value="416.161"), "muss eine Zahl sein"),
        (lambda d: d["values"]["revenue"].update(unit="billions"), "unit"),
        (lambda d: d["values"]["revenue"].update(source="TODO: Seite"), "Platzhalter"),
        (lambda d: d["values"]["d_and_a"].update(value=5), "schließen sich aus"),
        (lambda d: d["values"]["total_debt"].pop("is_lower_bound"), "is_lower_bound"),
        (lambda d: d["values"]["shares_outstanding"].update(tolerance=-1), "tolerance"),
        (lambda d: d["values"]["shares_outstanding"].update(tolerance="1 Mio"), "tolerance"),
        (lambda d: d.update(status="done"), "status"),
    ],
)
def test_each_defect_is_reported(mutate, expected: str) -> None:
    data = copy.deepcopy(_complete())
    mutate(data)

    assert any(expected in p for p in validate_seed(data)), validate_seed(data)


def test_partial_scope_only_requires_listed_values() -> None:
    data = _complete()
    data["scope"] = "partial"
    data["values"] = {"total_debt": data["values"]["total_debt"]}

    assert validate_seed(data) == []


def test_to_usd_is_exact_and_handles_units() -> None:
    assert to_usd({"value": 416161, "unit": "millions"}) == Decimal(416_161_000_000)
    assert to_usd({"value": 98657.5, "unit": "millions"}) == Decimal(98_657_500_000)  # kein Float-Rauschen
    assert to_usd({"value": 1234, "unit": "thousands"}) == Decimal(1_234_000)
    assert to_usd({"value": 14776353000, "unit": "units"}) == Decimal(14_776_353_000)
    assert to_usd({"absent_reason": "nicht ausgewiesen"}) is None


def test_tolerance_is_converted_with_the_unit_of_the_entry() -> None:
    assert tolerance_usd({"unit": "units", "tolerance": 1_000_000}) == Decimal(1_000_000)
    assert tolerance_usd({"unit": "millions", "tolerance": 0.5}) == Decimal(500_000)
    assert tolerance_usd({"unit": "millions"}) == Decimal(0)


def test_all_expected_seed_files_exist() -> None:
    assert sorted(p.stem for p in seed_files()) == EXPECTED


@pytest.mark.parametrize("path", seed_files(), ids=lambda p: p.stem)
def test_seed_file_is_consistent_with_its_fixture(path: Path) -> None:
    data = load_seed(path)
    fixture = json.loads((FIXTURES / f"{path.stem}_companyfacts_slim.json").read_text(encoding="utf-8"))

    assert data["ticker"] == path.stem.upper()
    assert data["cik"] == f"{int(fixture['cik']):010d}"
    assert data.get("form") == "10-K"


@pytest.mark.parametrize("path", seed_files(), ids=lambda p: p.stem)
def test_seed_file_is_complete_or_reported(path: Path) -> None:
    data = load_seed(path)
    problems = validate_seed(data)

    if data.get("status") == "complete":
        assert problems == [], f"{path.name} ist als complete markiert, aber unvollständig"
    else:
        warnings.warn(
            f"Seed {path.name} unvollständig ({len(problems)} Mängel): " + "; ".join(problems),
            UserWarning,
            stacklevel=1,
        )
