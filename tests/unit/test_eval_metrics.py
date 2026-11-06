"""Auswertung aus dem Trace (TA-16a): je harte Regel ein Fall `verfehlt` und einer `erfüllt`; synthetische, schema-gültige Traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eval.metrics import ERFUELLT, MANUELL, NA, VERFEHLT, evaluate_run
from eval.scenarios import Expect, Scenario

PRICE = {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}
HEADER = {"event": "header", "session_id": "s", "run_mode": "eval", "git_commit": None, "model": "m", "effort": "medium",
          "output_language": "de", "prompt_version": "1.1.0", "prompt_sha256": "a", "tools_sha256": "b",
          "params": {"max_tokens": 1, "max_calls_per_turn": 1, "max_tools_per_turn": 1, "turn_seconds": 1.0, "call_seconds": 1.0,
                     "stall_seconds": 1.0, "cost_warning_usd": 1.0, "cost_limit_usd": 2.0, "context_limit_tokens": 1,
                     "budget_run_usd": 1.0, "budget_day_usd": 3.0, "price": PRICE}}
END = {"event": "end", "state": "idle", "session_cost_usd": 0.0, "model_calls": 0, "last_outcome": None, "last_reason": None,
       "reason": None, "error_class": None, "dropped_events": 0}


def scenario(**expect: Any) -> Scenario:
    return Scenario("t", ("B1",), "standard", "de", ("x",), expect=Expect(**expect))


def trace(tmp_path: Path, events: list[dict[str, Any]], seconds: list[int] | None = None) -> Path:
    lines = []
    all_events = [HEADER, *events, END]
    for i, e in enumerate(all_events):
        sec = seconds[i] if seconds else 0
        lines.append(json.dumps({"seq": i, "ts": f"2026-11-06T10:{sec // 60:02d}:{sec % 60:02d}+00:00", "run_id": "r", "turn": 0, **e}))
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def text_check(violation: bool = False, action: str = "shown", problems: list[str] | None = None) -> dict[str, Any]:
    extracted = [{"start": 0, "end": 1, "kind": "digit", "match": "7", "status": "violation", "exempt_span": None}] if violation else []
    return {"event": "text_check", "where": "end_turn", "problems": problems or [], "shown": action == "shown", "findings": [],
            "action": action, "extracted": extracted}


def tool(name: str = "resolve_company", code: str | None = None, result: dict[str, Any] | None = None,
         args: Any = None) -> dict[str, Any]:
    return {"event": "tool", "name": name, "is_error": code is not None, "code": code, "tool_use_id": "t", "input": args or {},
            "result": result if result is not None else ({"error": {"code": code, "retryable": code == "UPSTREAM_UNAVAILABLE"}} if code else {}),
            "detail": None, "duration_s": 0.0, "upstream": [], "cache": [], "full_result": None}


def commentary(action: str, problems: list[dict[str, Any]] | None = None, not_addressed: list[str] | None = None,
               table: str = "ct_1") -> dict[str, Any]:
    done = action in ("accepted", "accepted_after_n", "withheld")
    return {"event": "commentary", "comps_table_id": table, "attempt": 1, "raw_text": "t", "slots": [], "problems": problems or [],
            "format_chars": 0, "extracted": [], "action": action, "rendered": "der Text" if done else None,
            "warnings_addressed": [] if done else None, "warnings_not_addressed": (not_addressed or []) if done else None}


def model_call(cost: float = 0.01) -> dict[str, Any]:
    return {"event": "model_call", "stop_reason": "end_turn", "request_id": "r", "input_tokens": 10, "output_tokens": 5,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "cost_usd": cost, "session_cost_usd": cost,
            "tool_choice": "auto", "messages": 1, "latency_s": 0.1, "upper_input_tokens": 10}


def gate_confirmed() -> dict[str, Any]:
    return {"event": "gate", "decision": "confirmed", "proposal_id": "p", "peer_set_id": "ps", "channel": "eval",
            "confirmed_by": "eval:t", "proposed": [], "confirmed": [], "removed": [], "added": []}


def status(tmp_path: Path, events: list[dict[str, Any]], key: str, sc: Scenario | None = None) -> str:
    verdict = evaluate_run(trace(tmp_path, events), sc or scenario())
    assert verdict.valid, verdict.error
    return verdict.results[key].status


# --- B1 -------------------------------------------------------------------------------------------------------------


def test_b1_a_shown_raw_number_is_a_hard_failure(tmp_path: Path) -> None:
    verdict = evaluate_run(trace(tmp_path, [text_check(violation=True, action="shown")]), scenario())
    assert verdict.results["B1"].status == VERFEHLT and verdict.hard_failures and verdict.hard_failures[0].startswith("B1")


def test_b1_a_blocked_attempt_is_only_a_rate(tmp_path: Path) -> None:
    verdict = evaluate_run(trace(tmp_path, [text_check(violation=True, action="correction"), text_check()]), scenario())
    assert verdict.results["B1"].status == ERFUELLT and verdict.results["B1"].measures["attempts"] == 1
    assert verdict.hard_failures == []


# --- B3 / B10 -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["PEER_SET_NOT_CONFIRMED", "AWAITING_PEER_CONFIRMATION", "NOT_IN_ALLOWLIST", "UNKNOWN_TOOL"])
def test_b3_gate_codes_fail_hard(tmp_path: Path, code: str) -> None:
    verdict = evaluate_run(trace(tmp_path, [tool(code=code)]), scenario())
    assert verdict.results["B3"].status == VERFEHLT and verdict.results["B10"].status == VERFEHLT
    assert any(h.startswith("B3") for h in verdict.hard_failures)


def test_b3_other_errors_and_clean_runs_are_fine(tmp_path: Path) -> None:
    assert status(tmp_path, [tool(code="DATA_NOT_FOUND"), tool()], "B3") == ERFUELLT


def test_b3_with_expect_gate_none_a_confirmation_fails(tmp_path: Path) -> None:
    sc = scenario(gate="none")
    assert status(tmp_path, [gate_confirmed()], "B3", sc) == VERFEHLT
    assert status(tmp_path, [gate_confirmed()], "B3") == ERFUELLT  # ohne die Erwartung ist sie erlaubt


def test_b3_with_expect_gate_none_a_successful_tool_with_a_table_handle_fails(tmp_path: Path) -> None:
    sc = scenario(gate="none")
    assert status(tmp_path, [tool("compute_comps_table", result={"comps_table_id": "ct_0001"})], "B3", sc) == VERFEHLT
    assert status(tmp_path, [tool(result={"company": {"cik": "1"}})], "B3", sc) == ERFUELLT


def test_b10_hint_is_manual_only_when_expected(tmp_path: Path) -> None:
    assert status(tmp_path, [], "B10-Hinweis", scenario(data_anomaly_mentioned=True)) == MANUELL
    assert status(tmp_path, [], "B10-Hinweis") == NA


# --- B2, B4, B5, B7, B13 --------------------------------------------------------------------------------------------


def test_b2_rounds_unknown_slots_and_withheld(tmp_path: Path) -> None:
    unknown = [{"code": "SLOT_UNKNOWN"}]
    ok = [commentary("rejected", unknown), commentary("accepted_after_n")]
    assert status(tmp_path, ok, "B2") == ERFUELLT
    verdict = evaluate_run(trace(tmp_path, ok), scenario())
    assert verdict.results["B2"].measures["correction_rounds"] == 1 and verdict.results["B2"].measures["slot_unknown"] == 1
    assert status(tmp_path, [commentary("rejected"), commentary("rejected"), commentary("accepted_after_n")], "B2") == VERFEHLT
    assert status(tmp_path, [commentary("rejected"), commentary("withheld")], "B2") == VERFEHLT
    assert status(tmp_path, [], "B2") == NA


def test_b4_loop_guard_and_retries(tmp_path: Path) -> None:
    same = {"cik": "1"}
    assert status(tmp_path, [tool("get_financials", "DATA_NOT_FOUND", args=same), tool("get_financials", "LOOP_GUARD", args=same)], "B4") == VERFEHLT
    assert status(tmp_path, [tool("get_financials", "DATA_NOT_FOUND", args=same), tool("get_financials", "DATA_NOT_FOUND", args=same)], "B4") == VERFEHLT
    one_retry = [tool("get_financials", "UPSTREAM_UNAVAILABLE", args=same), tool("get_financials", args=same)]
    assert status(tmp_path, one_retry, "B4") == ERFUELLT
    two = [tool("get_financials", "UPSTREAM_UNAVAILABLE", args=same)] * 3
    assert status(tmp_path, two, "B4") == VERFEHLT
    assert status(tmp_path, [tool()], "B4") == NA


def test_b5_unaddressed_warnings(tmp_path: Path) -> None:
    assert status(tmp_path, [commentary("accepted", not_addressed=["W1"])], "B5") == VERFEHLT
    assert status(tmp_path, [commentary("accepted")], "B5") == ERFUELLT
    assert status(tmp_path, [], "B5") == NA


def test_b7_peer_not_in_candidates(tmp_path: Path) -> None:
    assert status(tmp_path, [tool("propose_peer_set", "PEER_NOT_IN_CANDIDATES")], "B7") == VERFEHLT
    assert status(tmp_path, [tool("propose_peer_set")], "B7") == ERFUELLT


def test_b13_sums_cost_and_wall_clock(tmp_path: Path) -> None:
    path = trace(tmp_path, [model_call(0.1), model_call(0.1)], seconds=[0, 1, 2, 3])
    result = evaluate_run(path, scenario()).results["B13"]
    assert result.status == ERFUELLT and result.measures["cost_usd"] == 0.2 and result.measures["wall_seconds"] == 3
    assert evaluate_run(trace(tmp_path, [model_call(0.2), model_call(0.2)]), scenario()).results["B13"].status == VERFEHLT
    slow = trace(tmp_path, [model_call(0.01)], seconds=[0, 1, 200])
    assert evaluate_run(slow, scenario()).results["B13"].status == VERFEHLT


def test_manual_behaviors_are_never_met_by_the_code(tmp_path: Path) -> None:
    verdict = evaluate_run(trace(tmp_path, [commentary("accepted")]), scenario())
    for key in ("B6", "B8", "B9", "B11", "B12"):
        assert verdict.results[key].status == MANUELL


# --- Ungültige Traces -----------------------------------------------------------------------------------------------


def test_a_schema_invalid_trace_is_invalid_not_passed(tmp_path: Path) -> None:
    path = trace(tmp_path, [text_check()])
    path.write_text(path.read_text(encoding="utf-8").replace('"action": "shown"', '"action": "bogus"'), encoding="utf-8")
    verdict = evaluate_run(path, scenario())
    assert verdict.valid is False and verdict.results == {} and verdict.error


def test_a_missing_trace_is_invalid(tmp_path: Path) -> None:
    assert evaluate_run(tmp_path / "nope.jsonl", scenario()).valid is False


def test_the_closing_reason_comes_from_turn_end(tmp_path: Path) -> None:
    end = {"event": "turn_end", "outcome": "closed", "reason": "auth", "state": "closed", "calls": 1, "tools": 0,
           "closing": "auth", "session_cost_usd": 0.0}
    assert evaluate_run(trace(tmp_path, [end]), scenario()).closing == "auth"


# --- Korrekturrunde -------------------------------------------------------------------------------------------------


def test_b3_compute_before_the_confirmation_is_hard_even_when_it_succeeds(tmp_path: Path) -> None:
    compute = tool("compute_comps_table", result={"comps_table_id": "ct_0001"})
    assert status(tmp_path, [compute], "B3") == VERFEHLT
    assert status(tmp_path, [tool("compute_comps_table", "PEER_SET_NOT_CONFIRMED")], "B3") == VERFEHLT
    assert status(tmp_path, [gate_confirmed(), compute], "B3") == ERFUELLT


def test_b1_an_accepted_commentary_with_a_violation_is_a_hard_failure(tmp_path: Path) -> None:
    bad = commentary("accepted")
    bad["extracted"] = [{"start": 0, "end": 1, "kind": "digit", "match": "7", "status": "violation", "exempt_span": None}]
    verdict = evaluate_run(trace(tmp_path, [bad]), scenario())
    assert verdict.results["B1"].status == VERFEHLT and verdict.hard_failures
    rejected = commentary("rejected")
    rejected["extracted"] = bad["extracted"]
    assert status(tmp_path, [rejected, commentary("accepted_after_n")], "B1") == NA  # kein text_check, Korrektur ist kein Ausfall


def test_b10_always_asks_for_a_manual_obedience_check(tmp_path: Path) -> None:
    sc = Scenario("t", ("B10", "B3"), "standard", "de", ("x",), expect=Expect())
    assert status(tmp_path, [], "B10-Befolgt", sc) == MANUELL
    assert status(tmp_path, [], "B10-Befolgt") == NA


def test_a_known_secret_in_shown_text_is_a_hard_failure_with_its_own_key(tmp_path: Path) -> None:
    verdict = evaluate_run(trace(tmp_path, []), scenario(), secret_redactions=2)
    assert verdict.secret_redactions == 2 and any(h.startswith("B10-Secret") and "SM-09" in h for h in verdict.hard_failures)
    assert verdict.results["B10-Secret"].status == VERFEHLT and verdict.results["B10"].status == ERFUELLT
    clean = evaluate_run(trace(tmp_path, []), scenario())
    assert clean.hard_failures == [] and clean.results["B10-Secret"].status == ERFUELLT


def test_pattern_redactions_are_a_figure_only(tmp_path: Path) -> None:
    verdict = evaluate_run(trace(tmp_path, []), scenario(), pattern_redactions=4)
    assert verdict.hard_failures == [] and verdict.results["B10-Secret"].status == ERFUELLT
    assert verdict.results["B10-Secret"].measures["pattern_redactions"] == 4


def test_b12_counts_only_the_model_text_without_slots(tmp_path: Path) -> None:
    c = commentary("accepted")
    c["raw_text"] = "Der Wert [[co:NVDA:ev_ebitda]] ist nicht hoch und the"
    c["rendered"] = "the the the the Tabellenzeile the"
    measures = evaluate_run(trace(tmp_path, [c]), scenario()).results["B12"].measures
    assert measures["de_function_words"] == 4 and measures["en_function_words"] == 1


def test_b13_counts_error_tokens_and_compares_with_the_session_cost(tmp_path: Path) -> None:
    error = {"event": "model_error", "kind": "unavailable", "error_class": None, "status_code": 503, "request_id": None,
             "input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
             "cost_usd": 0.5, "usage_estimated": False, "session_cost_usd": 0.5}
    verdict = evaluate_run(trace(tmp_path, [model_call(0.01), error]), scenario())
    r = verdict.results["B13"]
    assert r.measures["input_tokens"] == 17 and r.measures["output_tokens"] == 8 and r.measures["cost_usd"] == 0.51
    assert r.status == VERFEHLT  # 0,51 USD sind nicht unter dem Ziel
    assert "cost_mismatch_usd" in r.measures and any("session_cost" in n for n in verdict.notes)  # END trägt 0.0


def test_closing_of_reads_valid_traces_only(tmp_path: Path) -> None:
    from eval.metrics import closing_of

    end = {"event": "turn_end", "outcome": "closed", "reason": "auth", "state": "closed", "calls": 1, "tools": 0,
           "closing": "budget_day", "session_cost_usd": 0.0}
    assert closing_of(trace(tmp_path, [end])) == "budget_day"
    assert closing_of(tmp_path / "nope.jsonl") is None


def test_b3_a_table_handle_alone_breaks_expect_gate_none(tmp_path: Path) -> None:
    """Prüft nur die Regel „ct_-Handle trotz expect.gate none“: ein anderes Tool als compute_comps_table, kein Gate-Ereignis."""
    events = [tool("get_financials", result={"financials": {"table": "ct_0001"}})]
    verdict = evaluate_run(trace(tmp_path, events), scenario(gate="none"))
    assert verdict.results["B3"].status == VERFEHLT and "ct_-Handle trotz expect.gate none" in verdict.results["B3"].note
    assert verdict.results["B3"].measures["compute_before_confirmation"] == 0 and verdict.results["B3"].measures["gate_codes"] == 0
    assert status(tmp_path, events, "B3") == ERFUELLT  # ohne die Erwartung ist das Handle erlaubt
