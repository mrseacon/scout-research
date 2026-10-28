"""Schleifen-Szenarien zu Budgets, Abbrüchen und Fehlern: LT-14/15/18–24, LT-40, LT-43–46, LT-49, LT-51, LT-53,
Z17, Z20."""

from __future__ import annotations

import json

import pytest

from scout_research.agent import texts
from scout_research.agent.budgets import Usage
from scout_research.agent.loop import LoopBusyError, LoopState, SessionClosedError
from scout_research.agent.model_client import ModelCallError, Streamed
from scout_research.tools.handlers import dispatch as real_dispatch
from scout_research.tools.session import HumanInput
from tests.unit.loop_harness import Run, codes_in, reply, say, shown_texts, use
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
                     "request_id": "req_abc"}
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
