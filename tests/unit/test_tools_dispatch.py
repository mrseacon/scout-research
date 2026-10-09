"""Dispatcher: Fehlerformat, Codes (T1), LOOP_GUARD, UNKNOWN_TOOL (Plan, Abschnitt 2 und 4)."""

import re
from pathlib import Path

import httpx
import pytest

from scout_research.tools.errors import CODES, RETRYABLE
from scout_research.tools.handlers import TOOLS, dispatch
from tests.unit.tool_world import AAPL_CIK, MSFT_CIK, NVDA_CIK, World, call, confirmed, propose, resolved, standard_world, start

PLAN = Path(__file__).resolve().parents[2] / "docs" / "decisions" / "phase-3-plan.md"
STEP_6_CODES = {"SLOT_UNKNOWN", "SLOT_VALUE_UNAVAILABLE"}
"""Entstehen erst in `submit_commentary` (Schritt 6); ihre Erreichbarkeit prüft T2 dort."""


# --- T1: jeder Code ist erreichbar und kommt als is_error-Ergebnis ----------------------------------------------


def _err(ctx, name: str, **args):
    outcome = dispatch(ctx, name, args)
    assert outcome.is_error, outcome.content
    return outcome


def _scn_invalid_arguments():
    return _err(standard_world().context(), "resolve_company")


def _scn_unknown_handle():
    ctx, _ = start(standard_world())
    return _err(ctx, "propose_peer_set", candidate_set_id="cs_nope", peers=[{"cik": AAPL_CIK, "rationale": "x"}])


def _scn_period_not_available():
    ctx, _ = start(standard_world())
    return _err(ctx, "get_financials", cik=NVDA_CIK, period={"period_end": "1999-12-31"})


def _scn_historical():
    return _err(resolved(standard_world()), "find_peer_candidates", target_cik=NVDA_CIK, period={"period_end": "2025-01-26"})


def _scn_target_revenue():
    w = standard_world()
    ctx, ps = confirmed(w)
    w.facts[NVDA_CIK] = {"facts": {"us-gaap": {}, "dei": {}}, "cik": 1045810}
    ctx._facts.clear()
    return _err(ctx, "compute_comps_table", peer_set_id=ps)


def _scn_peer_search_not_possible():
    w = standard_world()
    w.meta[NVDA_CIK]["sic"] = None
    return _err(resolved(w), "find_peer_candidates", target_cik=NVDA_CIK)


def _scn_no_valid_peers():
    w = standard_world()
    ctx, ps = confirmed(w)
    del w.facts[AAPL_CIK], w.facts[MSFT_CIK]
    return _err(ctx, "compute_comps_table", peer_set_id=ps)


def _scn_not_confirmed():
    ctx, set_id = start(standard_world())
    proposal = propose(ctx, set_id)
    ctx.record_user_message("weiter")
    return _err(ctx, "compute_comps_table", peer_set_id=proposal["proposal_id"])


def _scn_allowlist():
    ctx, _ = start(standard_world())
    return _err(ctx, "get_market_data", ticker="AAPL")


def _scn_peer_not_in_candidates():
    ctx, set_id = start(standard_world())
    return _err(ctx, "propose_peer_set", candidate_set_id=set_id, peers=[{"cik": "0000999999", "rationale": "x"}])


def _scn_user_addition():
    ctx, set_id = start(standard_world())
    return _err(ctx, "propose_peer_set", candidate_set_id=set_id, peers=[{"cik": AAPL_CIK, "rationale": "x"}],
                user_requested_additions=[{"query": "MSFT"}])  # löst auf, steht aber in keiner Nutzernachricht


def _scn_naked_number():
    ctx, set_id = start(standard_world())
    return _err(ctx, "propose_peer_set", candidate_set_id=set_id, peers=[{"cik": AAPL_CIK, "rationale": "40 Prozent"}])


def _scn_frame_year():
    w = standard_world()
    del w.frames[("Revenues", 2025)][NVDA_CIK]
    w.frames[("Revenues", 2025)]["0000999999"] = (1.0, "2026-01-25", "x")
    return _err(resolved(w), "find_peer_candidates", target_cik=NVDA_CIK)


def _scn_data_not_found():
    w = standard_world()
    del w.facts[NVDA_CIK]
    return _err(resolved(w), "find_peer_candidates", target_cik=NVDA_CIK)


def _scn_unavailable():
    w = standard_world()
    ctx, ps = confirmed(w)
    w.status_overrides[f"companyfacts/CIK{AAPL_CIK}"] = 500
    return _err(ctx, "compute_comps_table", peer_set_id=ps)


def _scn_rate_limited():
    w = standard_world()
    ctx, ps = confirmed(w)
    w.status_overrides[f"companyfacts/CIK{AAPL_CIK}"] = 429
    return _err(ctx, "compute_comps_table", peer_set_id=ps)


def _scn_timeout():
    ctx, ps = confirmed(standard_world())
    ticks = iter(range(0, 10_000, 100))
    ctx.clock = lambda: next(ticks)
    return _err(ctx, "compute_comps_table", peer_set_id=ps)


def _scn_loop_guard():
    ctx = standard_world().context()
    _err(ctx, "resolve_company")
    return _err(ctx, "resolve_company")


def _scn_unknown_tool():
    return _err(standard_world().context(), "confirm_peer_set", proposal_id="pp_1")


def _scn_awaiting():
    ctx, set_id = start(standard_world())
    propose(ctx, set_id)
    return _err(ctx, "resolve_company", query="NVDA")


def _scn_internal():
    ctx, _ = start(standard_world())
    ctx.market.errors["NVDA"] = RuntimeError("kaputt")
    return _err(ctx, "get_market_data", ticker="NVDA")


SCENARIOS = {
    "INVALID_ARGUMENTS": _scn_invalid_arguments, "UNKNOWN_HANDLE": _scn_unknown_handle,
    "PERIOD_NOT_AVAILABLE": _scn_period_not_available, "HISTORICAL_VALUATION_NOT_SUPPORTED": _scn_historical,
    "TARGET_REVENUE_NOT_FOUND": _scn_target_revenue, "PEER_SEARCH_NOT_POSSIBLE": _scn_peer_search_not_possible,
    "NO_VALID_PEERS": _scn_no_valid_peers, "PEER_SET_NOT_CONFIRMED": _scn_not_confirmed,
    "NOT_IN_ALLOWLIST": _scn_allowlist, "PEER_NOT_IN_CANDIDATES": _scn_peer_not_in_candidates,
    "USER_ADDITION_NOT_IN_MESSAGES": _scn_user_addition, "NAKED_NUMBER": _scn_naked_number,
    "FRAME_YEAR_UNRESOLVED": _scn_frame_year, "DATA_NOT_FOUND": _scn_data_not_found,
    "UPSTREAM_UNAVAILABLE": _scn_unavailable, "UPSTREAM_RATE_LIMITED": _scn_rate_limited,
    "TOOL_TIMEOUT": _scn_timeout, "LOOP_GUARD": _scn_loop_guard, "UNKNOWN_TOOL": _scn_unknown_tool,
    "AWAITING_PEER_CONFIRMATION": _scn_awaiting, "INTERNAL_ERROR": _scn_internal,
}


def test_every_code_is_covered_by_a_scenario_or_belongs_to_step_6() -> None:
    assert set(SCENARIOS) | STEP_6_CODES == set(CODES) and not set(SCENARIOS) & STEP_6_CODES


@pytest.mark.parametrize("code", sorted(SCENARIOS))
def test_every_code_is_reachable_and_comes_back_as_an_error_result(code: str) -> None:
    outcome = SCENARIOS[code]()
    error = outcome.content["error"]
    assert outcome.is_error and error["code"] == code
    assert set(error) == {"code", "message", "retryable", "details", "hint"}
    assert error["retryable"] is (code in RETRYABLE) and error["message"] and error["hint"]


def test_a_runtime_error_in_a_handler_becomes_internal_error_without_crashing() -> None:
    outcome = _scn_internal()
    assert outcome.content["error"]["code"] == "INTERNAL_ERROR" and "kaputt" not in outcome.to_json()
    assert outcome.trace["error_class"] == "RuntimeError" and "kaputt" in outcome.trace["traceback"]  # nur im Trace


def test_plan_table_and_code_list_agree() -> None:
    section = PLAN.read_text().split("### Fehlerformat", 1)[1].split("**Meldungen an das Modell", 1)[0]
    rows = re.findall(r"^\| `([A-Z_]+)` \| ([^|]+) \|", section, flags=re.MULTILINE)
    assert {code for code, _ in rows} == set(CODES)
    assert {code for code, retry in rows if retry.strip().startswith("ja")} == set(RETRYABLE)


# --- Eingabe ------------------------------------------------------------------------------------------------------


def test_invalid_arguments_report_location_and_message_but_neither_input_nor_urls() -> None:
    ctx = standard_world().context()
    outcome = dispatch(ctx, "resolve_company", {"query": "x" * 101, "extra": "GEHEIM-EINGABE"})
    problems = outcome.content["error"]["details"]["problems"]
    assert {p["loc"] for p in problems} == {"query", "extra"}
    text = outcome.to_json()
    assert "GEHEIM-EINGABE" not in text and "pydantic" not in text and "http" not in text


def test_non_object_arguments_are_invalid_not_a_crash() -> None:
    ctx = standard_world().context()
    for raw in (None, "NVDA", [1, 2], 5):
        assert dispatch(ctx, "resolve_company", raw).content["error"]["code"] == "INVALID_ARGUMENTS"


def test_tool_inputs_are_validated_on_the_server_for_every_bound() -> None:
    ctx, set_id = start(standard_world())
    ctx.record_user_message("x")
    too_many = [{"cik": str(100 + i), "rationale": "x"} for i in range(16)]
    cases = [
        ("resolve_company", {"query": ""}),
        ("find_peer_candidates", {"target_cik": "abc"}),
        ("find_peer_candidates", {"target_cik": "12345678901"}),
        ("propose_peer_set", {"candidate_set_id": set_id, "peers": []}),
        ("propose_peer_set", {"candidate_set_id": set_id, "peers": too_many}),
        ("propose_peer_set", {"candidate_set_id": set_id, "peers": [{"cik": AAPL_CIK, "rationale": "x" * 301}]}),
        ("get_market_data", {"ticker": "A" * 11}),
        ("get_financials", {"cik": NVDA_CIK, "period": {"period_end": "kein-datum"}}),
    ]
    for name, args in cases:
        assert dispatch(ctx, name, args).content["error"]["code"] == "INVALID_ARGUMENTS", (name, args)


# --- LOOP_GUARD ---------------------------------------------------------------------------------------------------


def test_identical_call_after_a_non_retryable_error_is_blocked() -> None:
    ctx, _ = start(standard_world())
    first = dispatch(ctx, "get_market_data", {"ticker": "AAPL"})
    second = dispatch(ctx, "get_market_data", {"ticker": "AAPL"})
    assert first.content["error"]["code"] == "NOT_IN_ALLOWLIST" and second.content["error"]["code"] == "LOOP_GUARD"
    assert dispatch(ctx, "get_market_data", {"ticker": "MSFT"}).content["error"]["code"] == "NOT_IN_ALLOWLIST"  # anderer Aufruf


def test_argument_order_does_not_defeat_the_guard() -> None:
    ctx, _ = start(standard_world())
    dispatch(ctx, "get_financials", {"cik": AAPL_CIK, "metrics": ["cash"]})
    assert dispatch(ctx, "get_financials", {"metrics": ["cash"], "cik": AAPL_CIK}).content["error"]["code"] == "LOOP_GUARD"


def test_a_retryable_error_may_be_repeated_once() -> None:
    w = standard_world()
    ctx, ps = confirmed(w)
    w.status_overrides[f"companyfacts/CIK{AAPL_CIK}"] = 503
    codes = [dispatch(ctx, "compute_comps_table", {"peer_set_id": ps}).content["error"]["code"] for _ in range(3)]
    assert codes == ["UPSTREAM_UNAVAILABLE", "UPSTREAM_UNAVAILABLE", "LOOP_GUARD"]


def test_a_new_user_message_allows_the_call_again() -> None:
    ctx, _ = start(standard_world())
    assert dispatch(ctx, "get_market_data", {"ticker": "AAPL"}).is_error
    ctx.record_user_message("Zeig mir auch AAPL")
    call(ctx, "resolve_company", query="AAPL")
    assert not dispatch(ctx, "get_market_data", {"ticker": "AAPL"}).is_error


def test_a_success_clears_the_failure_memory() -> None:
    w = standard_world()
    ctx, ps = confirmed(w)
    w.status_overrides[f"companyfacts/CIK{AAPL_CIK}"] = 503
    assert dispatch(ctx, "compute_comps_table", {"peer_set_id": ps}).is_error
    w.status_overrides.clear()
    assert not dispatch(ctx, "compute_comps_table", {"peer_set_id": ps}).is_error
    assert ctx.failed_calls == {}


def test_the_gate_block_does_not_count_towards_the_loop_guard() -> None:
    ctx, set_id = start(standard_world())
    proposal = propose(ctx, set_id)
    codes = [dispatch(ctx, "resolve_company", {"query": "NVDA"}).content["error"]["code"] for _ in range(3)]
    assert codes == ["AWAITING_PEER_CONFIRMATION"] * 3
    ctx.confirm_peer_set(proposal["proposal_id"], [AAPL_CIK])
    assert not dispatch(ctx, "resolve_company", {"query": "NVDA"}).is_error


def test_the_registry_holds_exactly_the_six_step_5_tools_with_budgets() -> None:
    assert {n: s.budget_seconds for n, s in TOOLS.items()} == {
        "resolve_company": 20, "find_peer_candidates": 90, "propose_peer_set": 20, "compute_comps_table": 180,
        "get_financials": 45, "get_market_data": 30}
