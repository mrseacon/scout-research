"""Schleifen-Szenarien zu Budgets, Abbrüchen und Fehlern: LT-14/15/18–24, LT-40, LT-43–46, LT-49, LT-51/51b, LT-53/53b/53c,
Z17, Z20."""

from __future__ import annotations

import json

import pytest

from scout_research.agent import texts
from scout_research.agent.budgets import LoopConfig, Usage, cost_usd
from scout_research.agent.loop import LoopBusyError, LoopState, Pending, SessionClosedError
from scout_research.agent.model_client import ModelCallError, Streamed
from scout_research.tools.handlers import dispatch as real_dispatch
from scout_research.tools.session import HumanInput
from tests.unit.loop_harness import Run, codes_in, reply, say, shown_texts, text_block, use
from tests.unit.test_agent_loop_core import _flow_to_proposal
from tests.unit.tool_world import NVDA_CIK, standard_world


def _q(i: int) -> tuple[str, dict]:
    return "resolve_company", {"query": f"Quuxcorp{i}"}


class _Spy:
    def __init__(self, advance: float = 0.0) -> None:
        self.calls: list[str] = []
        self.advance = advance
        self.clock = None

    def __call__(self, ctx, name, args):
        self.calls.append(name)
        out = real_dispatch(ctx, name, args)
        if self.clock is not None and self.advance:
            self.clock.advance(self.advance)
        return out


def _run(steps, spy: _Spy | None = None, **kw) -> Run:
    run = Run(standard_world(), steps, dispatch=spy, **kw)
    if spy is not None:
        spy.clock = run.clock
    return run


def test_lt_14_the_loop_never_retries_on_its_own_and_the_third_identical_call_hits_the_loop_guard() -> None:
    w = standard_world()
    w.status_overrides["/companyfacts/"] = 503
    spy = _Spy()
    run = Run(w, [], dispatch=spy)
    same = ("get_financials", {"cik": NVDA_CIK, "metrics": ["revenue"]})
    run.client._steps = [use(("resolve_company", {"query": "NVDA"})), use(same), use(same), use(same),
                         say("Die Quelle ist gerade nicht erreichbar.")]
    result = run.say("Umsatz von NVDA?")
    codes = [codes_in(run.tool_results(i)[0]) for i in (2, 3, 4)]
    assert codes == ["UPSTREAM_UNAVAILABLE", "UPSTREAM_UNAVAILABLE", "LOOP_GUARD"]
    assert spy.calls == ["resolve_company"] + ["get_financials"] * 3  # genau die Aufrufe des Modells
    assert result.outcome == "done"
    run.assert_invariants()


def test_lt_15_a_second_loop_guard_in_one_turn_ends_the_turn_as_failed() -> None:
    run = _run([use(("resolve_company", {"query": "NVDA"}))] +
               [use(("get_financials", {"cik": "0000000999"}))] * 3 + [say("zu viel")])
    result = run.say("Umsatz von NVDA?")
    assert result.outcome == "failed" and result.reason == "loop_guard" and result.state is LoopState.IDLE
    assert [codes_in(run.tool_results(i)[0]) for i in (2, 3)] == ["NOT_IN_ALLOWLIST", "LOOP_GUARD"]
    assert shown_texts(result) == ["Scout ist in diesem Schritt nicht weitergekommen."]
    assert len(run.client.requests) == 4  # der fünfte Aufruf findet nicht statt
    run.assert_invariants()


def test_lt_18_an_invented_tool_is_answered_with_unknown_tool() -> None:
    run = _run([use(("do_everything", {"x": 1})), say("Dieses Tool gibt es nicht.")])
    run.say("Mach etwas")
    assert codes_in(run.tool_results(1)[0]) == "UNKNOWN_TOOL"
    run.assert_invariants()


def test_lt_19_the_eleventh_model_call_is_never_made_and_the_results_stay_pending() -> None:
    run = _run([use(_q(i)) for i in range(12)] + [say("fertig")])
    result = run.say("Suche etwas")
    assert result.outcome == "failed" and result.reason == "limit_calls" and result.calls == 10
    assert len(run.client.requests) == 10
    assert [b["type"] for b in run.loop.pending.blocks] == ["tool_result"]
    nxt = run.say("weiter")  # ein neuer Zug: frisches Budget; die ausstehenden Ergebnisse gehen voran
    assert nxt.outcome == "done" and nxt.calls == 3
    assert [b["type"] for b in run.client.requests[10].messages[-1]["content"]] == ["tool_result", "text"]
    run.assert_invariants()


def test_lt_20_blocks_above_twelve_tools_are_not_executed_and_answered_with_turn_ended() -> None:
    spy = _Spy()
    run = _run([use(*[_q(i) for i in range(14)]), say("fertig")], spy)
    result = run.say("Suche viel")
    assert result.outcome == "failed" and result.reason == "limit_tools"
    assert len(spy.calls) == 12
    pending = run.loop.pending.blocks
    assert len(pending) == 14 and all("TURN_ENDED" in b["content"] for b in pending[12:])
    run.assert_invariants()


def test_lt_21_the_turn_deadline_fails_the_turn_but_a_six_minute_turn_completes() -> None:
    spy = _Spy(advance=400)
    run = _run([use(_q(1)), use(_q(2)), use(_q(3)), say("fertig")], spy)
    result = run.say("Suche")
    assert result.outcome == "failed" and result.reason == "turn_deadline"
    assert len(run.client.requests) == 2

    spy = _Spy(advance=360)
    run = _run([use(_q(1)), say("fertig")], spy)
    assert run.say("Suche").outcome == "done"


def test_lt_22_max_tokens_discards_the_answer_without_a_retry() -> None:
    run = _run([say("Ein sehr langer Text", stop="max_tokens"), say("nie gesendet")])
    result = run.say("Erzähl viel")
    assert result.outcome == "failed" and result.reason == "max_tokens"
    assert len(run.client.requests) == 1 and run.loop.history == []
    assert [b["text"] for b in run.loop.pending.blocks] == ["Erzähl viel"]
    assert "zu lang" in shown_texts(result)[0]
    run.assert_invariants()


def test_lt_46_a_truncated_tool_use_is_neither_appended_nor_executed() -> None:
    spy = _Spy()
    run = _run([use(_q(1), stop="max_tokens"), say("nie gesendet")], spy)
    result = run.say("Suche")
    assert result.outcome == "failed" and result.reason == "max_tokens"
    assert spy.calls == [] and run.loop.history == []
    run.assert_invariants()


def test_lt_23_a_refusal_fails_the_turn_and_is_counted() -> None:
    run = _run([reply(stop="refusal")])
    result = run.say("Mach etwas Unzulässiges")
    assert result.outcome == "failed" and result.reason == "refusal" and run.loop.history == []
    assert [e["stop_reason"] for e in run.loop.events if e["event"] == "unusable_response"] == ["refusal"]
    assert run.loop.state is LoopState.IDLE


def test_lt_24_cost_warning_once_then_a_hard_stop_right_after_the_call_that_crosses_the_limit() -> None:
    big = Usage(input_tokens=1000, output_tokens=55_000)  # 0,55 $ je Aufruf
    spy = _Spy()
    run = _run([use(_q(1), usage=big), use(_q(2), usage=big), use(_q(3), usage=big),
                use(_q(4), _q(5), usage=big), say("nie gesendet")], spy)
    result = run.say("Suche")
    assert result.outcome == "closed" and result.state is LoopState.CLOSED and result.reason == "cost"
    notices = shown_texts(result)
    assert sum("nähern sich dem Limit" in t for t in notices) == 1
    assert any("Kostenlimit" in t for t in notices)
    assert len(run.client.requests) == 4 and spy.calls.count("resolve_company") == 3  # Blöcke des 4. Aufrufs: nicht ausgeführt
    assert all("TURN_ENDED" in b["content"] for b in run.loop.pending.blocks)
    end = run.loop.events[-1]
    assert end["event"] == "turn_end" and end["outcome"] == "closed" and end["session_cost_usd"] > 2.0
    with pytest.raises(SessionClosedError):
        run.say("noch eine Frage")


def test_lt_49_the_context_limit_closes_the_session_without_compaction() -> None:
    run = _run([say("Antwort", usage=Usage(input_tokens=151_000, output_tokens=10)), say("nie gesendet")])
    result = run.say("Frage")
    assert result.outcome == "closed" and result.reason == "context"
    assert "neue Sitzung" in " ".join(shown_texts(result)) and len(run.client.requests) == 1
    with pytest.raises(SessionClosedError):
        run.say("noch eine")


def test_lt_43_a_stalled_or_overlong_stream_discards_the_answer_and_the_next_turn_resends_the_request_part() -> None:
    stall = Streamed([("text_delta", "a", 1.0), ("text_delta", "b", 61.0)], then=say("nie"))
    slow = Streamed([("text_delta", "x", 50.0)] * 7, then=say("nie"))
    run = _run([stall, say("Jetzt klappt es.")])
    first = run.say("Frage eins")
    assert first.outcome == "failed" and first.reason == "stalled" and run.loop.history == []
    second = run.say("Frage zwei")
    assert [b["text"] for b in run.client.requests[1].messages[-1]["content"]] == ["Frage eins", "Frage zwei"]
    assert shown_texts(second) == ["Jetzt klappt es."]
    run.assert_invariants()

    run = _run([slow])
    result = run.say("Frage")
    assert result.reason == "deadline" and result.outcome == "failed"


@pytest.mark.parametrize(
    ("kind", "state", "needle"),
    [("unavailable", LoopState.IDLE, "nicht erreichbar"), ("auth", LoopState.CLOSED, "API-Schlüssel"),
     ("bad_request", LoopState.IDLE, "Interner Fehler"), ("unexpected", LoopState.IDLE, "Interner Fehler")],
)
def test_lt_44_every_failure_kind_gives_a_fixed_notice_and_exposes_only_class_status_and_request_id(
    kind: str, state: LoopState, needle: str
) -> None:
    error = ModelCallError(kind, "RateLimitError", 429, "req_abc")  # type: ignore[arg-type]
    run = _run([error])
    result = run.say("Frage")
    assert result.state is state and needle in shown_texts(result)[0]
    event = [e for e in run.loop.events if e["event"] == "model_error"][0]
    assert event == {"event": "model_error", "kind": kind, "error_class": "RateLimitError", "status_code": 429,
                     "request_id": "req_abc", "input_tokens": None, "output_tokens": None,
                     "cache_read_input_tokens": None, "cache_creation_input_tokens": None, "cost_usd": 0.0,
                     "usage_estimated": False, "session_cost_usd": 0.0}
    assert "Traceback" not in " ".join(shown_texts(result))


def test_lt_45_an_interrupt_during_tools_ends_the_remaining_blocks_with_turn_ended() -> None:
    holder: dict = {}

    def dispatch(ctx, name, args):
        out = real_dispatch(ctx, name, args)
        if len(holder["calls"]) == 0:
            holder["calls"].append(name)
            holder["run"].loop.interrupt()  # Strg+C trifft das erste Tool
        return out

    holder["calls"] = []
    run = Run(standard_world(), [use(_q(1), _q(2), _q(3)), say("nie")], dispatch=dispatch)
    holder["run"] = run
    result = run.say("Suche")
    assert result.outcome == "failed" and result.reason == "interrupted"
    pending = run.loop.pending.blocks
    assert len(pending) == 3 and "TURN_ENDED" not in pending[0]["content"]
    assert all("TURN_ENDED" in b["content"] for b in pending[1:])
    assert run.loop.state is LoopState.IDLE
    run.assert_invariants()


def test_lt_51_an_interrupt_between_commit_and_results_leaves_a_valid_history_and_the_next_turn_works() -> None:
    def status(text: str) -> None:  # Strg+C in der Statuszeile des ersten Tools, nach dem Commit der Antwort (H1)
        if text != texts.user_text("de", "status_working"):
            raise KeyboardInterrupt

    spy = _Spy()
    run = _run([use(_q(1), _q(2)), say("Jetzt klappt es.")], spy)
    run.loop._on_status = status
    result = run.say("Suche")
    assert result.outcome == "failed" and result.reason == "interrupted" and spy.calls == []
    assert [codes_in(json.loads(b["content"])) for b in run.loop.pending.blocks] == ["TURN_ENDED", "TURN_ENDED"]
    run.assert_invariants()
    run.loop._on_status = None
    assert shown_texts(run.say("Und jetzt?")) == ["Jetzt klappt es."]
    assert [codes_in(r) for r in run.tool_results(-1)] == ["TURN_ENDED", "TURN_ENDED"]
    run.assert_invariants()


def test_lt_51b_results_before_the_interrupt_stay_and_only_the_rest_is_turn_ended() -> None:
    statuses: list[str] = []

    def status(text: str) -> None:  # Strg+C in der Statuszeile des zweiten Tools: das erste ist schon gelaufen
        statuses.append(text)
        if len(statuses) == 3:
            raise KeyboardInterrupt

    spy = _Spy()
    resolve = ("resolve_company", {"query": "NVDA"})
    run = _run([use(resolve, resolve), say("Jetzt klappt es.")], spy)
    run.loop._on_status = status
    result = run.say("Mach Comps für NVDA")
    assert result.outcome == "failed" and result.reason == "interrupted" and spy.calls == ["resolve_company"]
    first, second = (json.loads(b["content"]) for b in run.loop.pending.blocks)
    assert first["status"] == "resolved" and first["company"]["cik"] == NVDA_CIK and codes_in(second) == "TURN_ENDED"
    run.assert_invariants()


def test_lt_53b_tool_use_stop_with_text_but_without_a_block_is_appended_and_reported_as_unexpected() -> None:
    run = _run([reply(text_block("Ich rufe gleich ein Tool auf."), stop="tool_use"), say("Jetzt klappt es.")])
    result = run.say("Frage eins")
    assert result.outcome == "failed" and result.reason == "unexpected_stop"
    assert [e["empty"] for e in run.loop.events if e["event"] == "unusable_response"] == [False]
    assert run.loop.history[-1]["role"] == "assistant" and run.loop.pending == Pending()
    run.assert_invariants()
    assert shown_texts(run.say("Frage zwei")) == ["Jetzt klappt es."]
    run.assert_invariants()


def test_lt_53c_whitespace_text_blocks_never_reach_the_history_beside_tools_or_otherwise() -> None:
    resolve = ("resolve_company", {"query": "NVDA"})
    run = _run([use(resolve, text="\n\n"), reply(text_block("Fertig."), text_block("  \n")), say("Weiter.")])
    result = run.say("Mach Comps für NVDA")
    assert [t.strip() for t in shown_texts(result)] == ["Fertig."]  # gezeigt wird der geprüfte Text
    assistant = [m["content"] for m in run.loop.history if m["role"] == "assistant"]
    assert [[b["type"] for b in c] for c in assistant] == [["tool_use"], ["text"]]
    assert all(b.get("text", "x").strip() for request in run.client.requests for m in request.messages
               if m["role"] == "assistant" for b in m["content"])
    run.assert_invariants()
    assert shown_texts(run.say("Und jetzt?")) == ["Weiter."]
    run.assert_invariants()


@pytest.mark.parametrize(
    "empty",
    [reply(), reply(stop="tool_use"), say(" \n "),
     reply({"type": "thinking", "thinking": "…", "signature": "sig=="})],
    ids=["no_blocks", "no_blocks_tool_use", "blank_text", "thinking_only"],
)
def test_lt_53_an_empty_answer_is_discarded_with_a_fixed_notice_and_the_next_turn_works(empty) -> None:
    run = _run([empty, say("Jetzt klappt es.")])
    result = run.say("Frage eins")
    assert result.outcome == "failed" and result.reason == "empty_response"
    assert shown_texts(result) == [texts.user_text("de", "unexpected")]
    assert run.loop.history == [] and [b["text"] for b in run.loop.pending.blocks] == ["Frage eins"]
    assert [e["empty"] for e in run.loop.events if e["event"] == "unusable_response"] == [True]
    run.assert_invariants()
    assert shown_texts(run.say("Frage zwei")) == ["Jetzt klappt es."]
    run.assert_invariants()


def test_z17_an_interrupt_during_a_model_call_discards_the_call_and_keeps_the_request_part() -> None:
    """Das rohe `KeyboardInterrupt` aus `client.create` kommt nur noch außerhalb des Streams vor; im Stream macht der
    Adapter daraus `ModelCallError("interrupted")` (M2, siehe `test_m2_an_interrupted_model_call_...`)."""

    def boom(request):
        raise KeyboardInterrupt

    run = _run([boom, say("Jetzt klappt es.")])
    result = run.say("Frage eins")
    assert result.outcome == "failed" and result.reason == "interrupted" and run.loop.history == []
    assert shown_texts(run.say("Frage zwei")) == ["Jetzt klappt es."]
    run.assert_invariants()


def test_the_interrupt_cancels_a_running_tool_cooperatively() -> None:
    from scout_research.tools.errors import ToolFailure
    from scout_research.tools.session import Deadline

    run = _run([])
    run.ctx.deadline = Deadline(1000, run.clock)
    run.ctx.deadline.check()
    run.loop.interrupt()
    with pytest.raises(ToolFailure) as exc:
        run.ctx.deadline.check()
    assert exc.value.error.code == "TOOL_TIMEOUT"


def test_lt_40_and_z20_input_during_a_turn_is_rejected_and_changes_nothing() -> None:
    seen: list[BaseException] = []
    statuses: list[str] = []
    run = _run([use(_q(1)), say("fertig")])

    def intruder(status: str) -> None:
        statuses.append(status)
        for attempt in (lambda: run.loop.run_turn(HumanInput("dazwischen")), lambda: run.loop.confirm("pp_1", []),
                        lambda: run.loop.reject("pp_1")):
            try:
                attempt()
            except LoopBusyError as exc:
                seen.append(exc)

    run.loop._on_status = intruder
    run.say("Suche")
    assert len(seen) == 3 * len(statuses) and seen
    assert run.ctx.user_messages == ("Suche",)
    assert "dazwischen" not in str(run.loop.history) and "dazwischen" not in str(run.client.requests)
    run.assert_invariants()


# --- M2: gescheiterte Aufrufe sind teilweise bezahlt und werden verbucht --------------------------------------------


def _price():
    return LoopConfig().price


def test_m2_a_failed_call_with_usage_raises_the_session_cost_and_the_turn_cost_by_exactly_its_cost() -> None:
    usage = Usage(2000, 8000, 500, 100)
    run = _run([ModelCallError("stalled", usage=usage, usage_estimated=True), say("Jetzt klappt es.")])
    result = run.say("Frage eins")
    expected = cost_usd(usage, _price())
    assert result.outcome == "failed" and result.reason == "stalled"
    assert run.loop.session_cost == expected and result.cost_usd == expected and expected > 0
    second = run.say("Frage zwei")
    assert second.cost_usd == pytest.approx(cost_usd(Usage(1000, 200), _price()))
    assert run.loop.session_cost == pytest.approx(expected + cost_usd(Usage(1000, 200), _price()))
    run.assert_invariants()


def test_m2_a_failed_call_without_usage_costs_nothing() -> None:
    run = _run([ModelCallError("unavailable")])
    result = run.say("Frage")
    assert run.loop.session_cost == 0.0 and result.cost_usd == 0.0 and result.outcome == "failed"


def test_m2_a_failed_call_that_crosses_the_hard_limit_closes_the_session() -> None:
    usage = Usage(1000, 195_000)  # 1,952 $; mit dem ersten Zug (0,082 $) über 2 $
    run = _run([say("Antwort", usage=Usage(1000, 8000)), ModelCallError("deadline", usage=usage, usage_estimated=True),
                say("nie gesendet")])
    first = run.say("Frage eins")
    assert first.outcome == "done" and run.loop.state is LoopState.IDLE
    assert run.loop.session_cost + cost_usd(usage, _price()) >= 2.0
    result = run.say("Frage zwei")
    assert result.outcome == "closed" and result.reason == "deadline" and result.state is LoopState.CLOSED
    assert run.loop.state is LoopState.CLOSED and len(run.client.requests) == 2
    assert any("Kostenlimit" in t for t in shown_texts(result))
    with pytest.raises(SessionClosedError):
        run.say("Frage drei")
    assert len(run.client.requests) == 2
    assert run.loop.events[-1]["session_cost_usd"] >= 2.0


def test_m2_a_failed_call_that_exceeds_the_context_limit_closes_the_session() -> None:
    run = _run([ModelCallError("stalled", usage=Usage(151_000, 8000), usage_estimated=True), say("nie gesendet")])
    result = run.say("Frage")
    assert result.outcome == "closed" and result.state is LoopState.CLOSED
    assert "neue Sitzung" in " ".join(shown_texts(result)) and len(run.client.requests) == 1


def test_m2_the_cost_warning_is_issued_once_after_a_failed_call_too() -> None:
    usage = Usage(1000, 55_000)  # 0,55 $
    run = _run([ModelCallError("stalled", usage=usage), ModelCallError("stalled", usage=usage),
                ModelCallError("stalled", usage=usage)])
    warned = [sum("nähern sich dem Limit" in t for t in shown_texts(run.say(f"Frage {i}"))) for i in range(3)]
    assert warned == [0, 1, 0]


def test_m2_the_model_error_event_carries_cost_and_the_estimate_flag() -> None:
    usage = Usage(2000, 8000, 500, 100)
    run = _run([ModelCallError("stalled", usage=usage, usage_estimated=True)])
    run.say("Frage")
    event = [e for e in run.loop.events if e["event"] == "model_error"][0]
    assert event["input_tokens"] == 2000 and event["output_tokens"] == 8000
    assert event["cache_read_input_tokens"] == 500 and event["cache_creation_input_tokens"] == 100
    assert event["cost_usd"] == cost_usd(usage, _price()) and event["usage_estimated"] is True
    assert event["session_cost_usd"] == run.loop.session_cost == event["cost_usd"]


def test_m2_an_interrupted_call_is_billed_ends_failed_and_the_next_turn_works() -> None:
    usage = Usage(1500, 8000)
    run = _run([ModelCallError("interrupted", usage=usage, usage_estimated=True), say("Jetzt klappt es.")])
    result = run.say("Frage eins")
    assert result.outcome == "failed" and result.reason == "interrupted" and run.loop.history == []
    assert shown_texts(result) == [texts.user_text("de", "interrupted")]
    assert result.cost_usd == cost_usd(usage, _price()) == run.loop.session_cost
    assert shown_texts(run.say("Frage zwei")) == ["Jetzt klappt es."]
    run.assert_invariants()


def test_m2_an_interrupted_model_call_discards_the_call_and_keeps_the_request_part() -> None:
    # wie Z17, aber so, wie der Adapter einen Strg+C im Stream meldet
    run = _run([ModelCallError("interrupted"), say("Jetzt klappt es.")])
    result = run.say("Frage eins")
    assert result.outcome == "failed" and result.reason == "interrupted" and run.loop.history == []
    assert shown_texts(result) == [texts.user_text("de", "interrupted")]
    second = run.say("Frage zwei")
    assert [b["text"] for b in run.client.requests[1].messages[-1]["content"]] == ["Frage eins", "Frage zwei"]
    assert shown_texts(second) == ["Jetzt klappt es."]
    run.assert_invariants()


def test_m2_an_interrupt_during_the_introduction_fails_the_turn_but_keeps_the_proposal_confirmable() -> None:
    usage = Usage(1500, 8000)
    run = _flow_to_proposal(tail=[say("Bestätigt, ich rechne.")])
    run.client._steps[3] = ModelCallError("interrupted", usage=usage, usage_estimated=True)
    result = run.say("Mach Comps für NVDA")
    assert result.outcome == "failed" and result.reason == "interrupted"
    assert shown_texts(result) == [texts.user_text("de", "interrupted")]  # nicht die feste Einleitung (P12, Z17)
    assert result.state is LoopState.AWAITING_PEER_CONFIRMATION and run.ctx.open_proposal is not None
    assert [e for e in run.loop.events if e["event"] == "model_error"][0]["cost_usd"] == cost_usd(usage, _price())
    assert run.loop.session_cost > cost_usd(usage, _price())  # die Aufrufe davor zählen auch
    run.assert_invariants()
    assert run.confirm().outcome == "done"
    run.assert_invariants()


def test_m2_a_stalled_introduction_keeps_the_fixed_introduction() -> None:
    run = _flow_to_proposal(tail=[say("Bestätigt, ich rechne.")])
    run.client._steps[3] = ModelCallError("stalled")
    result = run.say("Mach Comps für NVDA")
    assert result.outcome == "done" and result.reason == "intro_stalled"
    assert [(s.kind, s.text) for s in result.shown] == [("intro", texts.user_text("de", "intro"))]
    assert result.state is LoopState.AWAITING_PEER_CONFIRMATION
    run.assert_invariants()


def test_m2_the_turn_end_event_names_the_closing_reason_even_when_the_failure_kind_is_the_reason() -> None:
    run = _run([ModelCallError("deadline", usage=Usage(1000, 200_000), usage_estimated=True)])  # 2,002 $
    run.say("Frage")
    assert run.loop.events[-1]["event"] == "turn_end" and run.loop.events[-1]["closing"] == "cost"
    assert run.loop.events[-1]["reason"] == "deadline"

    run = _run([ModelCallError("stalled", usage=Usage(151_000, 8000), usage_estimated=True)])
    run.say("Frage")
    assert run.loop.events[-1]["closing"] == "context"

    run = _run([ModelCallError("stalled")])
    run.say("Frage")
    assert run.loop.events[-1]["closing"] is None


# --- M1 (12.16): leere Eingabe und abgelehnte Anfrage vergiften den Verlauf nicht -----------------------------------


def _snapshot(run: Run) -> tuple:
    loop = run.loop
    return (loop.pending, json.dumps(loop.history, sort_keys=True), run.ctx.user_messages, list(loop.events), loop.state)


def _has_empty_text(request) -> bool:
    return any(b.get("type") == "text" and not b.get("text", "").strip()
               for m in request.messages for b in m["content"])


@pytest.mark.parametrize("blank", ["", " ", "\n\t  \n"])
def test_m1_empty_or_whitespace_input_is_rejected_before_anything_is_recorded_or_sent(blank: str) -> None:
    run = _run([say("Gerne."), say("Verstanden.")])
    run.say("Frage eins")
    before = _snapshot(run)
    with pytest.raises(ValueError):
        run.say(blank)
    assert _snapshot(run) == before and len(run.client.requests) == 1
    assert shown_texts(run.say("Frage zwei")) == ["Verstanden."]
    assert not any(_has_empty_text(r) for r in run.client.requests)
    run.assert_invariants()


@pytest.mark.parametrize("not_text", [None, b""])
def test_m1_input_that_is_not_text_raises_type_error_and_changes_nothing(not_text) -> None:
    run = _run([say("Gerne.")])
    before = _snapshot(run)
    with pytest.raises(TypeError):
        run.say(not_text)
    assert _snapshot(run) == before and run.client.requests == []
    assert shown_texts(run.say("Frage")) == ["Gerne."]
    run.assert_invariants()


def test_m1_empty_input_on_a_fresh_session_leaves_it_untouched_and_the_first_real_turn_works() -> None:
    run = _run([say("Gerne.")])
    before = _snapshot(run)
    with pytest.raises(ValueError):
        run.say("")
    assert _snapshot(run) == before and run.client.requests == []
    assert shown_texts(run.say("Frage")) == ["Gerne."]
    run.assert_invariants()


def test_m1_a_rejected_request_with_plain_user_text_is_discarded_and_the_next_turn_sends_only_the_new_text() -> None:
    run = _run([ModelCallError("bad_request", "BadRequestError", 400, "req_x"), say("Jetzt klappt es.")])
    first = run.say("Frage eins")
    assert first.outcome == "failed" and first.reason == "bad_request" and first.state is LoopState.IDLE
    assert run.loop.pending == Pending() and run.loop.history == []
    run.assert_invariants()
    second = run.say("Frage zwei")
    assert [b["text"] for b in run.client.requests[1].messages[-1]["content"]] == ["Frage zwei"]
    assert shown_texts(second) == ["Jetzt klappt es."]
    run.assert_invariants()


def test_m1_a_rejected_request_keeps_the_tool_results_unchanged() -> None:
    spy = _Spy()
    run = _run([use(_q(1), _q(2)), ModelCallError("bad_request", "BadRequestError", 400, "req_x"),
                say("Jetzt klappt es.")], spy)
    first = run.say("Suche")
    assert first.outcome == "failed" and first.reason == "bad_request" and spy.calls == ["resolve_company"] * 2
    uses = [b["id"] for b in run.loop.history[-1]["content"] if b["type"] == "tool_use"]
    kept = run.loop.pending.blocks
    assert [b["tool_use_id"] for b in kept] == uses and len(uses) == 2 and run.loop.pending.facts == ()
    assert all("TURN_ENDED" not in b["content"] for b in kept)  # die echten Ergebnisse, nicht TURN_ENDED (O-12)
    run.assert_invariants()
    second = run.say("Neue Frage")
    sent = run.client.requests[-1].messages[-1]["content"]
    assert [b for b in sent if b["type"] == "tool_result"] == list(kept)
    assert [b["text"] for b in sent if b["type"] == "text"] == ["Neue Frage"]
    assert shown_texts(second) == ["Jetzt klappt es."]
    run.assert_invariants()


def test_m1_after_a_failed_tool_turn_a_rejected_user_turn_drops_only_the_user_text() -> None:
    run = _run([use(_q(1)), ModelCallError("unavailable"), ModelCallError("bad_request", "BadRequestError", 400),
                say("Jetzt klappt es.")])
    run.say("Suche")
    results = run.loop.pending.blocks
    assert len(results) == 1 and "TURN_ENDED" not in results[0]["content"]
    assert run.say("Frage zwei").reason == "bad_request"
    assert run.loop.pending.blocks == results and run.loop.pending.facts == ()  # nur der Text des Menschen fällt weg
    run.assert_invariants()
    assert shown_texts(run.say("Frage drei")) == ["Jetzt klappt es."]
    sent = run.client.requests[-1].messages[-1]["content"]
    assert [b for b in sent if b["type"] == "tool_result"] == list(results)
    assert [b["text"] for b in sent if b["type"] == "text"] == ["Frage drei"]
    run.assert_invariants()


def test_m1_a_rejected_request_drops_every_earlier_user_text_still_pending_too() -> None:
    run = _run([ModelCallError("stalled"), ModelCallError("bad_request", "BadRequestError", 400), say("Jetzt klappt es.")])
    run.say("Frage null")
    assert run.say("Frage eins").reason == "bad_request"
    assert run.loop.pending == Pending()
    assert shown_texts(run.say("Frage zwei")) == ["Jetzt klappt es."]
    assert [b["text"] for b in run.client.requests[-1].messages[-1]["content"]] == ["Frage zwei"]
    run.assert_invariants()


def test_m1_a_rejected_host_turn_keeps_template_and_fact_and_the_next_turn_sends_them_again() -> None:
    run = _flow_to_proposal(tail=[ModelCallError("bad_request", "BadRequestError", 400), say("Jetzt klappt es.")])
    run.say("Mach Comps für NVDA")
    result = run.confirm()
    assert result.outcome == "failed" and result.reason == "bad_request" and result.state is LoopState.IDLE
    assert run.ctx.peer_sets and any(e["event"] == "confirmed" for e in run.ctx.gate_log)  # das Gate gilt weiter
    template = texts.HOST_TEMPLATES["de"]["confirmed"]
    assert [b["text"] for b in run.loop.pending.blocks] == [template] and len(run.loop.pending.facts) == 1
    run.assert_invariants()
    assert shown_texts(run.say("Weiter bitte")) == ["Jetzt klappt es."]
    messages = run.client.requests[-1].messages
    assert [b["text"] for b in messages[-2]["content"]] == [template, "Weiter bitte"]
    assert messages[-1]["role"] == "system" and "Peer-Set bestätigt" in messages[-1]["content"][0]["text"]
    run.assert_invariants()


def test_m1_a_rejected_introduction_keeps_the_proposal_result_and_the_proposal_confirmable() -> None:
    run = _flow_to_proposal(tail=[say("Bestätigt, ich rechne.")])
    run.client._steps[3] = ModelCallError("bad_request", "BadRequestError", 400)
    result = run.say("Mach Comps für NVDA")
    assert result.outcome == "done" and result.reason == "intro_bad_request"
    assert result.state is LoopState.AWAITING_PEER_CONFIRMATION and run.ctx.open_proposal is not None
    uses = [b["id"] for b in run.loop.history[-1]["content"] if b["type"] == "tool_use"]
    kept = run.loop.pending.blocks
    assert [b["tool_use_id"] for b in kept] == uses and len(uses) == 1 and "TURN_ENDED" not in kept[0]["content"]
    run.assert_invariants()
    assert run.confirm().outcome == "done"
    run.assert_invariants()


def test_m1_two_rejected_requests_in_a_row_close_the_session() -> None:
    run = _run([ModelCallError("bad_request", "BadRequestError", 400), ModelCallError("bad_request", "BadRequestError", 400),
                say("nie gesendet")])
    first = run.say("Frage eins")
    assert first.outcome == "failed" and first.state is LoopState.IDLE
    second = run.say("Frage zwei")
    assert second.outcome == "closed" and second.reason == "bad_request" and second.state is LoopState.CLOSED
    assert shown_texts(second) == [texts.user_text("de", "internal")]
    assert run.loop.events[-1]["event"] == "turn_end" and run.loop.events[-1]["closing"] == "bad_request"
    with pytest.raises(SessionClosedError):
        run.say("Frage drei")
    assert len(run.client.requests) == 2
    run.assert_invariants()


def test_m1_a_cost_limit_already_set_stays_the_closing_reason_on_the_second_rejection() -> None:
    run = _run([ModelCallError("bad_request", "BadRequestError", 400),
                ModelCallError("bad_request", "BadRequestError", 400, usage=Usage(1000, 200_000), usage_estimated=True)])
    run.say("Frage eins")
    result = run.say("Frage zwei")
    assert result.outcome == "closed" and result.state is LoopState.CLOSED
    assert run.loop.events[-1]["event"] == "turn_end" and run.loop.events[-1]["closing"] == "cost"
    assert any("Kostenlimit" in t for t in shown_texts(result))
    run.assert_invariants()


def test_m1_a_successful_call_between_two_rejected_requests_resets_the_counter() -> None:
    run = _run([ModelCallError("bad_request", "BadRequestError", 400), say("Gerne."),
                ModelCallError("bad_request", "BadRequestError", 400), say("Wieder da.")])
    run.say("Frage eins")
    assert shown_texts(run.say("Frage zwei")) == ["Gerne."]
    third = run.say("Frage drei")
    assert third.outcome == "failed" and third.state is LoopState.IDLE
    assert shown_texts(run.say("Frage vier")) == ["Wieder da."]
    run.assert_invariants()


@pytest.mark.parametrize("kind", ["unavailable", "stalled"])
def test_m1_other_failure_kinds_keep_the_pending_part_and_never_count_towards_closing(kind: str) -> None:
    run = _run([ModelCallError(kind), ModelCallError(kind), ModelCallError(kind),  # type: ignore[arg-type]
                say("Jetzt klappt es.")])
    for i in range(3):
        assert run.say(f"Frage {i}").state is LoopState.IDLE
    assert [b["text"] for b in run.loop.pending.blocks] == ["Frage 0", "Frage 1", "Frage 2"]
    assert shown_texts(run.say("Frage 3")) == ["Jetzt klappt es."]
    run.assert_invariants()


# --- M4 (12.16): Mutationen, die die Suite überlebt hatten -------------------------------------------------------------


@pytest.mark.parametrize(
    ("usage", "closing"),
    [(Usage(input_tokens=151_000, output_tokens=10), "context"), (Usage(input_tokens=1000, output_tokens=200_000), "cost")],
    ids=["context", "cost"],  # Kostengrenze: 2,002 $
)
def test_m4_a_problem_text_at_the_limit_is_withheld_without_a_further_call(usage: Usage, closing: str) -> None:
    """Trifft der Aufruf, der die Sitzungsgrenze überschreitet, einen Text mit Problem, startet keine Korrekturrunde: Ein
    weiterer Aufruf in einer schließenden Sitzung wäre ein Aufruf zu viel (am Kopf von `_drive` gibt es keinen Schutz)."""
    run = _run([say("Antwort 42", usage=usage), say("nie gesendet")])
    result = run.say("Frage")
    assert result.outcome == "closed" and result.state is LoopState.CLOSED and result.reason == "withheld"
    assert run.loop.events[-1]["event"] == "turn_end" and run.loop.events[-1]["closing"] == closing
    notices = shown_texts(result)
    assert texts.user_text("de", "withheld") in notices and not any("42" in t for t in notices)
    assert len(run.client.requests) == 1 and result.calls == 1
    check = [e for e in run.loop.events if e["event"] == "text_check"]
    assert [(e["shown"], e["action"]) for e in check] == [(False, "withheld")]
    with pytest.raises(SessionClosedError):
        run.say("noch eine")


def _dispatch_interrupted_at(position: int):
    """`dispatch`, bei dem das Tool mit der laufenden Nummer `position` (1-basiert) Strg+C erhält, direkt im Aufruf."""
    calls: list[str] = []

    def dispatch(ctx, name, args):
        calls.append(name)
        if len(calls) == position:
            raise KeyboardInterrupt
        return real_dispatch(ctx, name, args)

    dispatch.calls = calls  # type: ignore[attr-defined]
    return dispatch


def _interrupted_run(position: int = 2) -> tuple[Run, object, object]:
    dispatch = _dispatch_interrupted_at(position)
    resolve = ("resolve_company", {"query": "NVDA"})
    run = Run(standard_world(), [use(resolve, _q(1), _q(2)), say("Jetzt klappt es.")], dispatch=dispatch)
    return run, dispatch, run.say("Mach Comps für NVDA")


def test_m4_an_interrupt_raised_by_a_tool_leaves_the_user_view_and_the_history_as_after_a_cooperative_interrupt() -> None:
    """Für Nutzer und Verlauf ist der Zweig `except KeyboardInterrupt` in `_run_tools` seit H1 nicht nötig: `_run` fängt
    Strg+C ebenfalls, und der Block steht in `_commit` schon als `TURN_ENDED` im Verlauf. Dieser Test muss auch ohne den
    Zweig grün bleiben; den Unterschied im Trace prüft der nächste."""
    run, dispatch, result = _interrupted_run()
    assert result.outcome == "failed" and result.reason == "interrupted" and result.state is LoopState.IDLE
    assert shown_texts(result) == [texts.user_text("de", "interrupted")] and result.calls == 1
    assert dispatch.calls == ["resolve_company", "resolve_company"]  # das dritte Tool läuft nicht mehr
    first, second, third = (json.loads(b["content"]) for b in run.loop.pending.blocks)
    assert first["status"] == "resolved" and first["company"]["cik"] == NVDA_CIK
    assert codes_in(second) == "TURN_ENDED" and codes_in(third) == "TURN_ENDED"
    end = run.loop.events[-1]
    assert end["event"] == "turn_end" and end["outcome"] == "failed" and end["reason"] == "interrupted" and end["tools"] == 2
    run.assert_invariants()
    assert shown_texts(run.say("Und jetzt?")) == ["Jetzt klappt es."]
    assert [codes_in(r) if "error" in r else r["status"] for r in run.tool_results(-1)] == ["resolved", "TURN_ENDED", "TURN_ENDED"]
    run.assert_invariants()


def test_m4_an_interrupt_raised_by_a_tool_is_recorded_as_a_started_tool_and_the_rest_is_turn_ended() -> None:
    """Das Ereignis `tool` ist im Trace die einzige Spur, dass das Tool gestartet wurde (es kann Nebenwirkungen gehabt
    haben): Es steht für den unterbrochenen Aufruf mit `TURN_ENDED`, für die übrigen Blöcke nicht."""
    run, dispatch, result = _interrupted_run()
    assert result.outcome == "failed" and result.reason == "interrupted"
    tools = [e for e in run.loop.events if e["event"] == "tool"]
    assert len(tools) == 2
    assert tools[0]["is_error"] is False and tools[0]["code"] is None
    assert tools[1]["is_error"] is True and tools[1]["code"] == "TURN_ENDED" and tools[1]["name"] == "resolve_company"
    assert [e["event"] for e in run.loop.events].index("turn_end") > run.loop.events.index(tools[1])
    run.assert_invariants()
    assert shown_texts(run.say("Und jetzt?")) == ["Jetzt klappt es."]
    run.assert_invariants()
