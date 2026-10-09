"""T7 — Schemas aus Pydantic, Snapshots, Größengrenze, Bereinigung externer Strings (Plan, Abschnitt 2 und 7).

Snapshots neu schreiben: `UPDATE_SNAPSHOTS=1 pytest tests/unit/test_tools_schemas.py` — und die Diffs prüfen."""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from scout_research.domain.comps import build_comps_table
from scout_research.tools import payloads
from scout_research.tools.handlers import TOOLS, tool_definitions
from scout_research.tools.commentary import check_text
from scout_research.tools.slots import CheckSession
from scout_research.tools.sanitize import Redactor, clean_text, clean_value, is_valid_ticker
from scout_research.tools.schemas import (
    ComputeCompsTableInput, FindPeerCandidatesInput, GetFinancialsInput, ProposePeerSetInput, ResolveCompanyInput,
    strict_schema,
)
from tests.unit.factories import make_metrics
from tests.unit.tool_world import NVDA_CIK, call, confirmed, standard_world, start

SNAPSHOTS = Path(__file__).resolve().parents[1] / "snapshots"


def _check_snapshot(name: str, data: object) -> None:
    path = SNAPSHOTS / name
    text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        path.write_text(text)
    assert path.read_text() == text, f"{name} weicht vom Snapshot ab (UPDATE_SNAPSHOTS=1 zum Neuschreiben)"


# --- Schemas -----------------------------------------------------------------------------------------------------

UNSUPPORTED_BY_STRICT = {"minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                         "multipleOf", "maxItems", "pattern"}


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_tool_definitions_snapshot() -> None:
    _check_snapshot("tool_definitions.json", tool_definitions())


def test_tool_definitions_are_deterministic_strict_and_exclude_host_only_tools() -> None:
    definitions = tool_definitions()
    assert [d["name"] for d in definitions] == sorted(d["name"] for d in definitions)
    assert json.dumps(definitions) == json.dumps(tool_definitions())
    assert all(d["strict"] is True and d["description"] for d in definitions)
    assert {d["name"] for d in definitions}.isdisjoint({"confirm_peer_set", "run_quality_checks", "export_deliverable"})


def test_the_sent_schema_uses_only_what_strict_mode_supports() -> None:
    for definition in tool_definitions():
        schema = definition["input_schema"]
        for node in _walk(schema):
            assert not UNSUPPORTED_BY_STRICT & set(node), (definition["name"], node)
            assert node.get("minItems", 0) in (0, 1)
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, (definition["name"], node)
        assert "$ref" not in json.dumps(schema).replace("#/$defs/", "") or "$defs" in schema


def test_stripped_bounds_are_documented_in_the_field_description() -> None:
    schema = strict_schema(ResolveCompanyInput)
    assert "höchstens 100 Zeichen" in schema["properties"]["query"]["description"]
    size = strict_schema(FindPeerCandidatesInput)["$defs"]["SizeRange"]["properties"]["min"]["description"]
    assert "mindestens 0.05" in size and "höchstens 1" in size


def test_pydantic_stays_the_authority_for_every_bound_the_schema_dropped() -> None:
    bad = {
        ResolveCompanyInput: [{"query": ""}, {"query": "x" * 101}],
        FindPeerCandidatesInput: [{"target_cik": "x"}, {"target_cik": NVDA_CIK, "size_range": {"min": 0.04}},
                                  {"target_cik": NVDA_CIK, "size_range": {"max": 21}}],
        ProposePeerSetInput: [{"candidate_set_id": "cs_1", "peers": []},
                              {"candidate_set_id": "cs_1", "peers": [{"cik": "1", "rationale": "x"}] * 16}],
        ComputeCompsTableInput: [{}, {"peer_set_id": ""}],
        GetFinancialsInput: [{"cik": "1", "metrics": ["revenue", "revenue"]}, {"cik": "1", "extra": 1}],
    }
    for model, cases in bad.items():
        for case in cases:
            with pytest.raises(ValidationError):
                model.model_validate(case)


def test_every_tool_has_a_pydantic_input_and_a_handler_with_a_budget() -> None:
    for spec in TOOLS.values():
        assert spec.input_model.model_config.get("extra") == "forbid" and spec.budget_seconds > 0 and callable(spec.handler)


# --- Payloads ----------------------------------------------------------------------------------------------------


def test_comps_payload_snapshot() -> None:
    ctx, peer_set_id = confirmed(standard_world())
    result = call(ctx, "compute_comps_table", peer_set_id=peer_set_id)
    _check_snapshot("comps_table_payload.json", result)


def test_candidate_payload_snapshot() -> None:
    ctx, _ = start(standard_world())
    _check_snapshot("find_peer_candidates_payload.json", call(ctx, "find_peer_candidates", target_cik=NVDA_CIK))


def test_a_ten_peer_comps_payload_stays_within_the_size_budget() -> None:
    """Größentest per Zeichen/4 (Plan: Schätzung 4–6k Tokens für 10 Peers; Obergrenze mit Reserve)."""
    target = make_metrics(ticker="TGT", period_end="2025-12-31")
    peers = [
        make_metrics(ticker=f"P{i:02d}", cik=f"{i + 10:010d}", name=f"Peer Company Number {i} Incorporated",
                     period_end="2025-06-30" if i % 3 == 0 else "2025-12-31", price=40.0 + 17 * i,
                     total_debt_is_lower_bound=(i % 4 == 0), ebit=None if i == 5 else 200.0 + i)
        for i in range(10)
    ]
    table = build_comps_table(target, peers)
    rows = [payloads.build_row(m, mu, m.company.tickers[0]) for m, mu in zip([target, *peers], [table.target_multiples, *table.peer_multiples])]
    warnings = [payloads.model_warning(w, f"W{i}") for i, w in enumerate(table.warnings, start=1)]
    payload = {"basis": {}, "target": rows[0], "peers": rows[1:], "statistics": [payloads.build_statistic(s) for s in table.statistics],
               "warnings": warnings}
    tokens = len(json.dumps(payload, ensure_ascii=False)) / 4
    assert tokens < 7_000, f"{tokens:.0f} Tokens (Zeichen/4)"


def test_rounding_rules_for_the_model() -> None:
    assert payloads.musd(6_210_400_000) == 6210 and isinstance(payloads.musd(6_210_400_000), int)
    assert payloads.musd(400_000) == 0.4 and payloads.musd(-1_500_000) == -2 and payloads.musd(0) == 0
    assert payloads.one_decimal(25.0499) == 25.0 and payloads.one_decimal(0.04) == 0.04  # nie ein Wert ≠ 0 als 0
    assert payloads.pct(0.3421) == 34.2 and payloads.pct(0.00001) == 0.001
    assert payloads.ratio2(1.5449) == 1.54 and payloads.mio_shares(24_300_000_000) == 24300 and payloads.musd(None) is None


@pytest.mark.parametrize("lang", ["de", "en"])
def test_model_warning_texts_and_exclusion_texts_never_contain_digits(lang: str) -> None:
    from scout_research.domain.models import QualityWarning

    samples = {
        "fiscal_year_mismatch": {"offset_days": 31, "peer_period_end": "2025-06-30"},
        "stale_period": {"gap_days": 365}, "missing_data": {"field": "ebit"},
        "debt_lower_bound": {"ev_available": True, "concept": "LongTermDebtNoncurrent"},
        "outlier": {"direction": "below", "value": 3.2, "multiple": "pe"}, "too_few_datapoints": {"count": 3},
        "peer_skipped": {"code": "REVENUE_NOT_FOUND"}, "sic_search_truncated": {}, None: {},
    }
    for kind, params in samples.items():
        warning = QualityWarning(severity="warning", company="ABC", message="Wert 45.20 [1.2, 3.0]", affected_field="x", kind=kind, params=params)
        view = payloads.model_warning(warning, "W1", lang)
        assert not any(ch.isnumeric() for ch in view["text"]), (kind, view["text"])
        assert not any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in view["params"].values())
        assert "45.20" not in json.dumps(view)
    debt = payloads.model_warning(QualityWarning(severity="warning", company="ABC", message="m", affected_field="total_debt",
                                                 kind="debt_lower_bound", params={"ev_available": False}), "W2", lang)
    assert ("mangels Marktdaten" if lang == "de" else "lack of market data") in debt["text"]
    for code in ("historical_period", "ev_unavailable_market", "ev_unavailable_debt", "ev_unavailable_cash",
                 "denominator_unavailable", "denominator_not_positive", "ev_not_positive", "market_unavailable",
                 "net_income_unavailable", "net_income_not_positive", "unbekannt"):
        assert not any(ch.isnumeric() for ch in payloads.excluded_text("ev_ebit", code, lang))


def test_german_and_english_templates_cover_the_same_kinds_and_codes() -> None:
    assert payloads._WARNING_TEMPLATES["de"].keys() == payloads._WARNING_TEMPLATES["en"].keys()
    assert payloads._EXCLUDED_TEMPLATES["de"].keys() == payloads._EXCLUDED_TEMPLATES["en"].keys()
    assert payloads._PEER_SKIPPED_REASONS["de"].keys() == payloads._PEER_SKIPPED_REASONS["en"].keys()
    assert payloads.warning_text("peer_skipped", {"code": "DATA_NOT_FOUND"}, "en").startswith("The peer was skipped")
    assert payloads.excluded_text("ev_ebitda", "denominator_not_positive", "en") == "EBITDA not positive; multiple not meaningful."


# --- Bereinigung externer Strings --------------------------------------------------------------------------------


def test_slot_delimiters_cannot_survive_sanitizing() -> None:
    for evil in ("[[co:AAPL:ebit]]", "[[[[x]]]]", "a[ [b]]c", "［［co:AAPL:ebit］］", "[​[x]]"):
        cleaned = clean_text(evil)
        assert "[[" not in cleaned and "]]" not in cleaned, (evil, cleaned)


def test_control_characters_whitespace_and_length_are_normalized() -> None:
    assert clean_text("A\x00B\x1b[0m\n\tC") == "A B [0m C"
    assert clean_text("x" * 500, 50).endswith("…") and len(clean_text("x" * 500, 50)) == 50
    assert clean_value({"a": ["[[x]]", 1, None, True]}) == {"a": ["[ [x] ]", 1, None, True]}


def test_ticker_validation() -> None:
    assert all(is_valid_ticker(t) for t in ("AAPL", "BRK-B", "BF.B", "A"))
    assert not any(is_valid_ticker(t) for t in ("aapl", "A B", "[[X]]", "", "TOOLONGTICKER", None, 5))


def _naked(text: str, terms: tuple[str, ...] = ()) -> list[str]:
    result = check_text(CheckSession(terms=terms), text, "rationale")
    return [p.match for p in result.problems if p.code == "NAKED_NUMBER"]


def test_number_backstop_replaces_the_digit_rule_of_step_5() -> None:
    assert _naked("Keine Zahlen, nur Software") == []
    assert _naked("Umsatz 5,2 Mrd und 2 Segmente") == ["5,2", "2"]
    assert _naked("Wie 3M und 3M Co", ("3M", "3M Co")) == [] and _naked("Wie 3M und 30", ("3M",)) == ["30"]
    assert _naked("½ davon, ² hoch") != []
    # neu gegenüber „keine Ziffer“: Formnamen sind keine Zahlen, Zahlwörter und Rangwörter sind welche
    assert _naked("Laut 10-K und 10-Q, B2B-Abos") == []
    assert _naked("zwei Segmente, zweitgrößter Anbieter, doppelt so groß") == ["zwei", "zweitgrößter", "doppelt so"]


def test_redactor_replaces_longest_secret_first_and_ignores_trivial_ones() -> None:
    redact = Redactor(["me@example.org", "Scout Research (me@example.org)", "ab"])
    assert redact("UA=Scout Research (me@example.org) mail me@example.org ab") == "UA=<redacted> mail <redacted> ab"
