"""Systemprompt (Plan, Abschnitt 12.1): statisch, versioniert und im Einklang mit Tools, Fehlercodes und Slot-Grammatik.

Die ✓/✗-Beispiele des Prompts laufen durch dieselbe Prüfung wie jeder Modelltext: Der Prompt darf dem Modell nichts
beibringen, was der Check abweist (und kein Gegenbeispiel, das der Check durchließe)."""

import re
from pathlib import Path

import pytest

import scout_research.agent
from scout_research.tools import slots
from scout_research.tools.commentary import check_text
from scout_research.tools.errors import CODES, ERROR_SPECS
from scout_research.tools.handlers import TOOLS
from scout_research.tools.numbercheck import Identifiers
from scout_research.tools.slots import CheckSession

PROMPT_PATH = Path(scout_research.agent.__file__).parent / "prompts" / "system.md"
RAW = PROMPT_PATH.read_text(encoding="utf-8")
HEADER = re.match(r"<!--(.*?)-->\n", RAW, flags=re.DOTALL)
BODY = RAW[HEADER.end():] if HEADER else RAW


def test_the_prompt_carries_a_version_header_that_is_stripped_before_sending() -> None:
    assert HEADER and re.search(r"scout-system-prompt v\d+\.\d+\.\d+", HEADER.group(1))
    assert "<!--" not in BODY and BODY.startswith("Du bist Scout")


def test_the_prompt_is_static_apart_from_the_language_placeholder() -> None:
    assert re.findall(r"\{[^{}]*\}", BODY) == ["{output_language}"]
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b|\b\d{1,2}\.\d{1,2}\.20\d\d\b", BODY)  # kein Datum im Prefix
    assert not re.search(r"\b(?:cs|pp|ps|ct)_[0-9a-f]{4,}\b", BODY)  # keine echten Handles


def test_every_tool_is_named_and_confirm_peer_set_is_not_a_tool() -> None:
    for name in TOOLS:
        assert f"`{name}`" in BODY, name
    assert "`confirm_peer_set` ist kein Tool" in BODY


def test_every_error_code_is_handled_and_the_retryable_ones_are_named_as_such() -> None:
    for code in CODES:
        assert f"`{code}`" in BODY, code
    retry_line = next(line for line in BODY.splitlines() if line.startswith("- `retryable: true`"))
    assert {c for c, spec in ERROR_SPECS.items() if spec.retryable} == set(re.findall(r"`([A-Z_]+)`", retry_line))


def test_the_slot_table_lists_exactly_the_fields_the_code_accepts() -> None:
    rows = {m.group(1): m.group(0) for m in re.finditer(r"^\| `\[\[(\w+):.*$", BODY, flags=re.MULTILINE)}
    assert set(rows) == {"co", "stat", "basis", "warn", "fin", "mkt"}

    def words(row: str) -> set[str]:
        return set(re.findall(r"\b[a-z][a-z_]*\b", row.split("|")[3]))

    assert slots.CO_FIELDS <= words(rows["co"])
    assert slots.MULTIPLE_FIELDS | set(slots.STAT_AGGREGATES) <= words(rows["stat"])
    assert set(slots.BASIS_FIELDS) <= words(rows["basis"])
    assert set(slots.FIN_METRICS) | set(slots.FIN_EXTRA) <= words(rows["fin"])
    assert set(slots.MKT_FIELDS) <= words(rows["mkt"])


def _example_session() -> CheckSession:
    row = {"cik": "1", "name": "ABC Corp", "period_end": "2025-11-28", "fiscal_year": 2025, "price": 350.0,
           "price_as_of": "2026-10-08", "ev_ebitda": 17.0, "pe": 26.8, "total_debt": 6210, "excluded": {},
           "total_debt_is_lower_bound": False, "ev_is_lower_bound": False, "ebitda_approximated": True}
    table = {
        "basis": {"n_peers": 1, "n_peers_with_ev_multiples": 1, "target_period_end": "2025-11-28",
                  "target_fiscal_year": 2025, "price_as_of": "2026-10-08"},
        "target": {**row, "ticker": "ABC"},
        "peers": [{**row, "ticker": "XYZ", "name": "XYZ Inc", "pe": None, "total_debt_is_lower_bound": True,
                   "excluded": {"pe": "Net Income nicht positiv; Multiple nicht aussagekräftig."}}],
        "statistics": [{"multiple": m, "min": 1.0, "median": 1.0, "mean": 1.0, "max": 1.0, "n": 1, "excluded": [],
                        "lower_bound": []} for m in ("ev_revenue", "ev_ebitda", "ev_ebit", "pe")],
        "warnings": [{"id": "W1", "severity": "warning", "kind": "debt_lower_bound", "company": "XYZ", "params": {}}],
        "skipped_peers": [],
    }
    fin = {"financials_id": "fin_0a1b2c3d4e5f", "ticker": "ABC", "period_end": "2025-11-28", "fiscal_year": 2025,
           "values": {"revenue": {"value": 21505, "unit": "Mio. USD", "flags": []}}}
    return CheckSession(table=table, financials={"fin_0a1b2c3d4e5f": fin},
                        market={"ABC": {"ticker": "ABC", "price": 350.0, "price_as_of": "2026-10-08"}},
                        warning_ids=frozenset({"W1"}), terms=(), identifiers=Identifiers())


GOOD = [line[2:] for line in BODY.splitlines() if line.startswith("✓ ")]
BAD = [line[2:].split(" ← ")[0] for line in BODY.splitlines() if line.startswith("✗ ")]


@pytest.mark.parametrize("example", GOOD)
def test_every_positive_example_passes_the_check(example: str) -> None:
    result = check_text(_example_session(), example)
    assert result.ok, [(p.code, p.kind, p.match) for p in result.problems]


@pytest.mark.parametrize("example", BAD)
def test_every_negative_example_is_rejected(example: str) -> None:
    assert not check_text(_example_session(), example).ok


def test_the_positive_examples_cover_all_six_namespaces() -> None:
    used = {m for line in GOOD for m in re.findall(r"\[\[(\w+):", line)}
    assert used == {"co", "stat", "basis", "warn", "fin", "mkt"} and len(BAD) >= 5
