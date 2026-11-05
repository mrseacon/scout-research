"""404 und 422 vom Modelldienst (TA-13, O-25): Zuordnung, Verhalten der Schleife, Kosten, Texte, Schema. Kein SDK."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scout_research.agent import texts
from scout_research.agent.budgets import LoopConfig, Usage, cost_usd
from scout_research.agent.loop import LoopState, Pending, SessionClosedError
from scout_research.agent.model_client import AnthropicAdapter, ManualClock, ModelCallError, map_sdk_exception
from scout_research.agent.trace import TraceSchemaError, validate_loop_event
from tests.unit.loop_harness import Run, say, shown_texts
from tests.unit.test_model_client import _Final, _named, _request, _Sdk, _Stream
from tests.unit.test_spend_budget import _Day
from tests.unit.tool_world import standard_world

_USAGE = Usage(100_000, 10_000)


def _run(steps, **kw) -> Run:
    return Run(standard_world(), steps, **kw)


def _kinds(run: Run) -> list[str]:
    return [e["kind"] for e in run.loop.events if e["event"] == "model_error"]


# --- Zuordnung ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "status", "kind"),
    [("NotFoundError", 404, "not_found"), ("UnprocessableEntityError", 422, "unprocessable"),
     ("NotFoundError", None, "not_found"), ("UnprocessableEntityError", None, "unprocessable"),
     ("APIStatusError", 404, "not_found"), ("APIStatusError", 422, "unprocessable"),
     ("SomethingElse", 404, "not_found"), ("SomethingElse", 422, "unprocessable"),
     ("APIStatusError", 418, "unexpected"), ("APIStatusError", None, "unexpected"),
     # Klassenname geht vor Status; bestehende Zuordnungen bleiben
     ("BadRequestError", 404, "bad_request"), ("AuthenticationError", 404, "auth"),
     ("PermissionDeniedError", 422, "auth"), ("RateLimitError", 404, "unavailable"),
     ("InternalServerError", 422, "unavailable"), ("BadRequestError", 400, "bad_request"),
     ("NotFoundError", 500, "not_found"), ("UnprocessableEntityError", 404, "unprocessable")],
)
def test_ta13_sdk_exceptions_map_by_class_name_first_then_by_status(name: str, status: int | None, kind: str) -> None:
    attrs: dict[str, Any] = {"status_code": status} if status is not None else {}
    error = map_sdk_exception(_named(name, **attrs))
    assert error.kind == kind and error.error_class == name and error.status_code == status


def test_ta13_a_subclass_of_a_known_class_is_mapped_by_the_base_name() -> None:
    base = type("NotFoundError", (Exception,), {})
    assert map_sdk_exception(type("ModelNotFound", (base,), {})("x")).kind == "not_found"


@pytest.mark.parametrize(("name", "status", "kind"),
                         [("NotFoundError", 404, "not_found"), ("UnprocessableEntityError", 422, "unprocessable")])
def test_ta13_the_adapter_calls_the_sdk_exactly_once_and_does_not_retry(name: str, status: int, kind: str) -> None:
    sdk = _Sdk(_Stream([], _Final([]), raises=_named(name, status_code=status, request_id="req_1")))
    with pytest.raises(ModelCallError) as exc:
        AnthropicAdapter(sdk, clock=ManualClock()).create(_request(), lambda k: None)
    assert exc.value.kind == kind and len(sdk.calls) == 1
    assert exc.value.__cause__ is None and exc.value.__context__ is None


# --- 404: Sitzung schließt -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["de", "en"])
def test_ta13_not_found_closes_the_session_with_the_fixed_text_and_calls_the_model_once(language: str) -> None:
    run = _run([ModelCallError("not_found", "NotFoundError", 404, "req_n"), say("nie gesendet")], language=language)
    result = run.say("Frage")
    assert result.outcome == "closed" and result.reason == "not_found" and result.state is LoopState.CLOSED
    assert shown_texts(result) == [texts.user_text(language, "not_found")]
    assert len(run.client.requests) == 1
    assert _kinds(run) == ["not_found"]
    assert run.loop.events[-1]["event"] == "turn_end" and run.loop.events[-1]["closing"] == "not_found"
    assert run.loop.history == []  # der Verlauf bleibt unverändert (wie bei auth)
    with pytest.raises(SessionClosedError):
        run.say("Folgefrage")
    assert len(run.client.requests) == 1
    run.assert_invariants()


def test_ta13_not_found_is_booked_like_any_failed_call_with_usage(tmp_path: Path) -> None:
    from tests.unit.test_spend_budget import _budget

    budget = _budget(tmp_path, run=10.0, day=30.0, clock=_Day())
    run = _run([ModelCallError("not_found", "NotFoundError", 404, usage=_USAGE, usage_estimated=True)], budget=budget)
    result = run.say("Frage")
    cost = cost_usd(_USAGE, LoopConfig().price)
    assert result.reason == "not_found" and cost > 0
    assert run.loop.session_cost == pytest.approx(cost)
    assert budget.run_spent == pytest.approx(cost) and budget.ledger.day_total() == pytest.approx(cost)
    (event,) = [e for e in run.loop.events if e["event"] == "model_error"]
    assert event["kind"] == "not_found" and event["cost_usd"] == pytest.approx(cost)
    run.assert_invariants()


# --- 422: wie bad_request ----------------------------------------------------------------------------------------------


def test_ta13_unprocessable_discards_the_human_text_and_answers_like_bad_request() -> None:
    run = _run([ModelCallError("unprocessable", "UnprocessableEntityError", 422), say("Jetzt klappt es.")])
    first = run.say("Frage eins")
    assert first.outcome == "failed" and first.reason == "unprocessable" and first.state is LoopState.IDLE
    assert shown_texts(first) == [texts.user_text("de", "internal")]
    assert run.loop.pending == Pending() and run.loop.history == []
    assert _kinds(run) == ["unprocessable"]
    run.assert_invariants()
    second = run.say("Frage zwei")
    assert [b["text"] for b in run.client.requests[1].messages[-1]["content"]] == ["Frage zwei"]
    assert shown_texts(second) == ["Jetzt klappt es."]
    assert len(run.client.requests) == 2
    run.assert_invariants()


@pytest.mark.parametrize("second_kind", ["unprocessable", "bad_request"])
def test_ta13_a_second_unprocessable_rejection_in_a_row_closes_with_bad_request(second_kind: str) -> None:
    run = _run([ModelCallError("unprocessable", "UnprocessableEntityError", 422),
                ModelCallError(second_kind, "X", 422),  # type: ignore[arg-type]
                say("nie gesendet")])
    first = run.say("Frage eins")
    assert first.outcome == "failed" and first.state is LoopState.IDLE
    second = run.say("Frage zwei")
    assert second.outcome == "closed" and second.state is LoopState.CLOSED
    assert shown_texts(second) == [texts.user_text("de", "internal")]
    assert run.loop.events[-1]["closing"] == "bad_request"
    with pytest.raises(SessionClosedError):
        run.say("Frage drei")
    assert len(run.client.requests) == 2
    run.assert_invariants()


def test_ta13_a_successful_call_between_two_unprocessable_rejections_resets_the_counter() -> None:
    run = _run([ModelCallError("unprocessable", "UnprocessableEntityError", 422), say("Gerne."),
                ModelCallError("unprocessable", "UnprocessableEntityError", 422), say("Wieder da.")])
    run.say("Frage eins")
    assert shown_texts(run.say("Frage zwei")) == ["Gerne."]
    assert run.say("Frage drei").state is LoopState.IDLE
    assert shown_texts(run.say("Frage vier")) == ["Wieder da."]


def test_ta13_unprocessable_with_usage_is_booked() -> None:
    run = _run([ModelCallError("unprocessable", "UnprocessableEntityError", 422, usage=_USAGE, usage_estimated=True)])
    run.say("Frage")
    cost = cost_usd(_USAGE, LoopConfig().price)
    assert cost > 0 and run.loop.session_cost == pytest.approx(cost)


# --- Texte und Schema --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["de", "en"])
def test_ta13_the_not_found_text_exists_in_both_languages_without_digits_and_model_names(language: str) -> None:
    text = texts.USER_TEXTS[language]["not_found"]
    assert text and not any(ch.isdigit() for ch in text) and "claude" not in text.lower()
    assert texts.USER_TEXTS["de"]["not_found"] != texts.USER_TEXTS["en"]["not_found"]


def test_ta13_the_trace_schema_knows_the_new_kinds_and_closing_reason() -> None:
    end = {"event": "turn_end", "outcome": "closed", "reason": "not_found", "state": "closed", "calls": 1, "tools": 0,
           "closing": "not_found", "session_cost_usd": 0.0}
    validate_loop_event(end)
    with pytest.raises(TraceSchemaError):
        validate_loop_event({**end, "closing": "erfunden"})
    error = {"event": "model_error", "kind": "unprocessable", "error_class": None, "status_code": 422,
             "request_id": None, "input_tokens": None, "output_tokens": None, "cache_read_input_tokens": None,
             "cache_creation_input_tokens": None, "cost_usd": 0.0, "usage_estimated": False, "session_cost_usd": 0.0}
    validate_loop_event(error)
    validate_loop_event({**error, "kind": "not_found"})
    with pytest.raises(TraceSchemaError):
        validate_loop_event({**error, "kind": "erfunden"})


# --- Runde 2 (N-1 bis N-6) -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind"),
    [(400, "bad_request"), (413, "bad_request"), (401, "auth"), (403, "auth"), (404, "not_found"),
     (422, "unprocessable"), (408, "unavailable"), (409, "unavailable"), (500, "unavailable"), (503, "unavailable"),
     (529, "unavailable"), (418, "unexpected"), (200, "unexpected"), (None, "unexpected")],
)
def test_ta13_the_status_fallback_covers_the_documented_codes_when_no_class_name_matches(status, kind) -> None:
    attrs: dict[str, Any] = {"status_code": status} if status is not None else {}
    assert map_sdk_exception(_named("APIStatusError", **attrs)).kind == kind


@pytest.mark.parametrize(("name", "status", "kind"),
                         [("BadRequestError", 500, "bad_request"), ("AuthenticationError", 500, "auth"),
                          ("RateLimitError", 401, "unavailable"), ("NotFoundError", 401, "not_found"),
                          ("UnprocessableEntityError", 500, "unprocessable")])
def test_ta13_a_known_class_name_beats_the_status_fallback(name: str, status: int, kind: str) -> None:
    assert map_sdk_exception(_named(name, status_code=status)).kind == kind


def test_ta13_not_found_overrides_an_earlier_budget_closing_and_still_books_the_call(tmp_path: Path) -> None:
    from tests.unit.test_spend_budget import _budget

    cost = cost_usd(_USAGE, LoopConfig().price)
    budget = _budget(tmp_path, run=cost / 2, day=30.0, clock=_Day())
    run = _run([ModelCallError("not_found", "NotFoundError", 404, usage=_USAGE, usage_estimated=True)], budget=budget)
    result = run.say("Frage")
    assert any(e["event"] == "budget_stop" and e["reason"] == "budget_run" for e in run.loop.events)
    assert run.loop.events[-1]["closing"] == "not_found" and result.reason == "not_found"
    assert result.state is LoopState.CLOSED
    assert budget.run_spent == pytest.approx(cost) and budget.ledger.day_total() == pytest.approx(cost)
    assert run.loop.session_cost == pytest.approx(cost)
    assert shown_texts(result)[-1] == texts.user_text("de", "not_found")
    run.assert_invariants()


def test_ta13_unprocessable_with_usage_is_booked_in_the_budget_and_the_ledger(tmp_path: Path) -> None:
    from tests.unit.test_spend_budget import _budget

    budget = _budget(tmp_path, run=10.0, day=30.0, clock=_Day())
    run = _run([ModelCallError("unprocessable", "UnprocessableEntityError", 422, usage=_USAGE, usage_estimated=True)],
               budget=budget)
    run.say("Frage")
    cost = cost_usd(_USAGE, LoopConfig().price)
    assert budget.run_spent == pytest.approx(cost) and budget.ledger.day_total() == pytest.approx(cost)
    (event,) = [e for e in run.loop.events if e["event"] == "model_error"]
    assert event["kind"] == "unprocessable" and event["cost_usd"] == pytest.approx(cost)
    run.assert_invariants()


def _not_found(**kw: Any) -> ModelCallError:
    return ModelCallError("not_found", "NotFoundError", 404, **kw)


def test_ta13_not_found_in_the_introduction_call_closes_and_no_peer_set_arises() -> None:
    from tests.unit.test_agent_loop_core import _flow_to_proposal

    run = _flow_to_proposal(tail=[say("nie gesendet")])
    run.client._steps[3] = _not_found()
    result = run.say("Mach Comps für NVDA")
    assert result.state is LoopState.CLOSED and run.loop.events[-1]["closing"] == "not_found"
    assert shown_texts(result)[-1] == texts.user_text("de", "not_found")
    with pytest.raises(SessionClosedError):
        run.confirm()
    assert run.ctx.peer_sets == {}
    assert len(run.client.requests) == 4
    run.assert_invariants()


def test_ta13_not_found_after_a_tool_use_keeps_the_tool_results_and_a_valid_history() -> None:
    from scout_research.agent.model_client import check_placement
    from tests.unit.loop_harness import use

    run = _run([use(("resolve_company", {"query": "NVDA"})), _not_found(), say("nie gesendet")])
    result = run.say("Mach Comps für NVDA")
    assert result.state is LoopState.CLOSED and run.loop.events[-1]["closing"] == "not_found"
    assert len(run.client.requests) == 2
    assert [b["type"] for b in run.loop.pending.blocks] == ["tool_result"]  # die Ergebnisse bleiben ausstehend
    check_placement(run.client.requests[1].messages)
    run.assert_invariants()
    with pytest.raises(SessionClosedError):
        run.say("Folgefrage")
    assert len(run.client.requests) == 2


def test_ta13_not_found_in_the_correction_round_closes() -> None:
    run = _run([say("Der Umsatz lag bei 12,5 Mrd. Dollar."), _not_found(), say("nie gesendet")])
    result = run.say("Frage")
    assert result.state is LoopState.CLOSED and result.reason == "not_found"
    assert run.loop.events[-1]["closing"] == "not_found"
    assert len(run.client.requests) == 2
    assert shown_texts(result)[-1] == texts.user_text("de", "not_found")
    run.assert_invariants()


def test_ta13_the_trace_schema_rejects_an_unknown_model_error_kind() -> None:
    event = {"event": "model_error", "kind": "not_found", "error_class": "NotFoundError", "status_code": 404,
             "request_id": None, "input_tokens": None, "output_tokens": None, "cache_read_input_tokens": None,
             "cache_creation_input_tokens": None, "cost_usd": 0.0, "usage_estimated": False, "session_cost_usd": 0.0}
    for kind in ("not_found", "unprocessable", "bad_request", "auth", "unavailable", "stalled", "deadline",
                 "unexpected", "interrupted"):
        validate_loop_event({**event, "kind": kind})
    with pytest.raises(TraceSchemaError):
        validate_loop_event({**event, "kind": "erfunden"})
