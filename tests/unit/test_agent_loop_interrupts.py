"""Zweites Strg+C und Vorbelegung nach `_commit` (TA-14; Plan 12.16-Befund „Zweites Strg+C während `_finish`“; H1, SG-13).

Teil A: Nach Verlassen von `_run` ist `state` nie `RUNNING`, egal mit welcher Ausnahme; Verlauf und `pending` bleiben gültig,
das zweite `KeyboardInterrupt` steigt zum Host auf. Teil B: `_commit` belegt jedes `tool_use` mit `TURN_ENDED` vor, und eine
Invariante prüft das ausdrücklich."""

from __future__ import annotations

import json

import pytest

from scout_research.agent import texts
from scout_research.agent.loop import AgentLoop, LoopBusyError, LoopState, Pending, SessionClosedError, _result_block
from scout_research.agent.model_client import PlacementError
from scout_research.tools.handlers import coded_outcome
from scout_research.tools.session import HumanInput
from tests.unit.loop_harness import Run, say, shown_texts, use
from tests.unit.tool_world import NVDA_CIK, standard_world


def _fail_first(monkeypatch: pytest.MonkeyPatch, name: str, times: int) -> list[int]:
    """Lässt die ersten `times` Aufrufe von `AgentLoop.<name>` ein `KeyboardInterrupt` werfen; danach läuft das Original."""
    original = getattr(AgentLoop, name)
    calls: list[int] = []

    def wrapper(self, *args, **kwargs):
        calls.append(1)
        if len(calls) <= times:
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AgentLoop, name, wrapper)
    return calls


def _turn_ends(run: Run) -> list[dict]:
    return [e for e in run.loop.events if e["event"] == "turn_end"]


# --- Teil A ---------------------------------------------------------------------------------------------------------


def test_a_second_ctrl_c_inside_finish_leaves_the_loop_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strg+C im Modellaufruf, ein zweites in `_finish` des Abbruchzweigs: Der Host sieht das zweite `KeyboardInterrupt`,
    der Loop ist danach nicht `RUNNING`, der nächste Zug läuft, der Verlauf ist gültig."""
    run = Run(standard_world(), [KeyboardInterrupt(), say("Weiter geht es.")])
    _fail_first(monkeypatch, "_finish", 1)
    with pytest.raises(KeyboardInterrupt):
        run.say("Mach Comps für NVDA")
    assert run.loop.state is not LoopState.RUNNING
    assert run.loop.state is LoopState.IDLE
    ends = _turn_ends(run)
    assert len(ends) == 1 and ends[0]["outcome"] == "failed" and ends[0]["reason"] == "interrupted_twice"
    assert ends[0]["state"] == "idle"
    run.assert_invariants()  # inkl. Schema des Trace für jedes Ereignis und Platzierung mit dem ausstehenden Teil

    second = run.say("Noch einmal")
    assert second.outcome == "done" and shown_texts(second) == ["Weiter geht es."]
    assert run.last_user_text() == "Mach Comps für NVDANoch einmal"  # der erste Text blieb ausstehend (Z16)
    run.assert_invariants()


def test_a_second_ctrl_c_in_the_tool_phase_keeps_the_results_of_the_interrupted_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strg+C im Tool (kooperativ abgefangen), dann zweimal in `_finish`: Das Tool-Ergebnis steht in `pending`, der
    Folgezug sendet einen gültigen Verlauf."""
    calls = {"n": 0}

    def dispatch(ctx, name, args):
        calls["n"] += 1
        raise KeyboardInterrupt

    run = Run(standard_world(), [use(("resolve_company", {"query": "NVDA"})), say("Danach.")], dispatch=dispatch)
    _fail_first(monkeypatch, "_finish", 2)
    with pytest.raises(KeyboardInterrupt):
        run.say("Mach Comps für NVDA")
    assert run.loop.state is LoopState.IDLE and calls["n"] == 1
    assert len(run.loop.pending.blocks) == 1 and run.loop.pending.blocks[0]["type"] == "tool_result"
    assert json.loads(run.loop.pending.blocks[0]["content"])["error"]["code"] == "TURN_ENDED"
    run.assert_invariants()

    run.loop._dispatch = lambda ctx, name, args: coded_outcome("TURN_ENDED")  # type: ignore[assignment]
    second = run.say("Weiter")
    assert second.outcome == "done" and shown_texts(second) == ["Danach."]
    run.assert_invariants()


def test_a_second_ctrl_c_while_appending_turn_end_does_not_write_a_second_turn_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Das zweite Strg+C trifft das Anhängen von `turn_end` selbst: Der Rest-Zustand steht schon, kein `RUNNING`."""
    run = Run(standard_world(), [say("Hallo."), say("Und weiter.")])

    class Events(list):
        armed = True

        def append(self, item):
            if Events.armed and item.get("event") == "turn_end":
                Events.armed = False
                raise KeyboardInterrupt
            super().append(item)

    run.loop.events = Events()
    with pytest.raises(KeyboardInterrupt):
        run.say("Hallo")
    assert run.loop.state is LoopState.IDLE
    assert [e["reason"] for e in _turn_ends(run)] == ["interrupted_twice"]
    assert run.say("Weiter").outcome == "done"
    run.assert_invariants()


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_any_base_exception_leaving_run_leaves_a_resting_state(exc: type[BaseException], monkeypatch: pytest.MonkeyPatch) -> None:
    run = Run(standard_world(), [say("Hallo."), say("Und weiter.")])

    def boom(self, *args, **kwargs):
        raise exc

    monkeypatch.setattr(AgentLoop, "_drive", boom)
    monkeypatch.setattr(AgentLoop, "_finish", boom)  # auch der Abbruchzweig scheitert
    with pytest.raises(exc):
        run.say("Hallo")
    assert run.loop.state is LoopState.IDLE
    monkeypatch.undo()
    assert run.say("Weiter").outcome == "done"
    run.assert_invariants()


def test_the_busy_error_only_exists_during_a_turn() -> None:
    run = Run(standard_world(), [say("Hallo.")])
    seen: list[BaseException] = []

    def reenter(request):
        try:
            run.loop.run_turn(HumanInput("x"))
        except LoopBusyError as exc:
            seen.append(exc)
        return say("Hallo.")

    run.client._steps = [reenter]
    assert run.say("Hallo").outcome == "done" and len(seen) == 1
    assert run.loop.state is LoopState.IDLE


# --- Teil B ---------------------------------------------------------------------------------------------------------


def _two_tool_uses() -> object:
    return use(("resolve_company", {"query": "NVDA"}), ("find_peer_candidates", {"target_cik": NVDA_CIK}))


@pytest.mark.parametrize("exc", [KeyboardInterrupt, RuntimeError])
def test_an_abort_right_after_commit_leaves_turn_ended_for_every_tool_use(exc: type[BaseException], monkeypatch: pytest.MonkeyPatch) -> None:
    """Abbruch direkt nach `_commit`, vor dem ersten Tool: `pending` hat je `tool_use`-ID genau ein `TURN_ENDED`, und der
    Folgezug sendet einen gültigen Verlauf (der Client prüft die Platzierung bei jedem Aufruf)."""
    response = _two_tool_uses()
    run = Run(standard_world(), [response, say("Danach.")])

    def boom(self, *args, **kwargs):
        raise exc

    original = AgentLoop._run_tools
    monkeypatch.setattr(AgentLoop, "_run_tools", boom)
    result = run.say("Mach Comps für NVDA")
    monkeypatch.setattr(AgentLoop, "_run_tools", original)

    assert result.outcome == "failed" and result.reason == ("interrupted" if exc is KeyboardInterrupt else "loop_error")
    ids = [b["id"] for b in response.content if b["type"] == "tool_use"]
    assert len(ids) == 2
    assert [b["tool_use_id"] for b in run.loop.pending.blocks] == ids
    assert all(json.loads(b["content"])["error"]["code"] == "TURN_ENDED" and b["is_error"] for b in run.loop.pending.blocks)
    run.assert_invariants()

    second = run.say("Weiter")
    assert second.outcome == "done" and shown_texts(second) == ["Danach."]
    sent = run.client.requests[-1].messages[-1]["content"]
    assert [b["tool_use_id"] for b in sent if b["type"] == "tool_result"] == ids
    run.assert_invariants()


def test_check_prefilled_accepts_exactly_one_result_per_tool_use() -> None:
    from scout_research.agent.loop import check_prefilled

    uses = [{"type": "tool_use", "id": "toolu_a", "name": "x", "input": {}},
            {"type": "tool_use", "id": "toolu_b", "name": "x", "input": {}}]
    history = [{"role": "user", "content": [{"type": "text", "text": "hi"}]},
               {"role": "assistant", "content": [{"type": "text", "text": "ok"}, *uses]}]
    ended = coded_outcome("TURN_ENDED")
    good = Pending(tuple(_result_block(b, ended) for b in uses), ())
    check_prefilled(history, good)  # wirft nicht
    check_prefilled(history[:1] + [{"role": "assistant", "content": [{"type": "text", "text": "ok"}]}], Pending())


@pytest.mark.parametrize("blocks", [
    (),  # Vorbelegung leer
    ("a",),  # eines fehlt
    ("a", "a"),  # doppelt, b fehlt
    ("a", "b", "b"),  # eines zu viel
    ("b", "a"),  # falsche Reihenfolge
    ("a", "zzz"),  # fremde ID
])
def test_check_prefilled_rejects_missing_duplicate_and_foreign_results(blocks: tuple[str, ...]) -> None:
    from scout_research.agent.loop import check_prefilled

    uses = [{"type": "tool_use", "id": f"toolu_{k}", "name": "x", "input": {}} for k in ("a", "b")]
    history = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}, {"role": "assistant", "content": uses}]
    ended = coded_outcome("TURN_ENDED")
    pending = Pending(tuple(_result_block({"id": f"toolu_{k}"}, ended) for k in blocks), ())
    with pytest.raises(PlacementError):
        check_prefilled(history, pending)


def test_commit_runs_the_invariant_and_a_violation_surfaces_as_a_placement_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_commit` ruft `check_prefilled` mit dem Verlauf und `pending`; eine Verletzung bricht den Zug als Programmfehler ab."""
    import scout_research.agent.loop as loop_module

    seen: list[tuple[int, int]] = []
    original = loop_module.check_prefilled

    def spy(history, pending):
        seen.append((len(history), len(pending.blocks)))
        original(history, pending)

    monkeypatch.setattr(loop_module, "check_prefilled", spy)
    run = Run(standard_world(), [use(("resolve_company", {"query": "NVDA"})), say("Fertig.")])
    run.say("Mach Comps für NVDA")
    assert seen and seen[0][1] == 1

    def violated(history, pending):
        raise PlacementError("verletzt")

    monkeypatch.setattr(loop_module, "check_prefilled", violated)
    run = Run(standard_world(), [use(("resolve_company", {"query": "NVDA"})), say("Fertig.")])
    result = run.say("Mach Comps für NVDA")
    # N3: Der Verlauf ist nicht mehr verlässlich, die Sitzung schließt mit dem festen Text `internal`
    assert result.outcome == "closed" and result.reason == "placement" and result.state is LoopState.CLOSED
    assert shown_texts(result)[0] == texts.user_text("de", "internal")
    assert run.loop.state is LoopState.CLOSED
    end = _turn_ends(run)[-1]
    assert end["closing"] == "placement" and end["reason"] == "placement" and end["state"] == "closed"
    assert any(e["event"] == "loop_error" and e["error_class"] == "PlacementError" for e in run.loop.events)
    with pytest.raises(SessionClosedError):
        run.say("Weiter")
    run.assert_invariants()  # das Ereignis besteht das strenge Schema


def test_a_placement_error_of_the_client_is_still_a_program_error_that_rises_to_the_host() -> None:
    """Nur die Invariante nach `_commit` schließt die Sitzung; ein Platzierungsfehler anderer Herkunft steigt auf."""
    run = Run(standard_world(), [PlacementError("vom Client")])
    with pytest.raises(PlacementError):
        run.say("Hallo")
    assert run.loop.state is LoopState.IDLE and run.loop._closing is None


def test_state_running_is_only_set_inside_the_guarded_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """N1: Scheitert die Vorbereitung des Zugs (Uhr), bleibt der Loop nicht `RUNNING`."""
    run = Run(standard_world(), [say("Hallo.")])
    calls = {"n": 0}

    def clock():
        calls["n"] += 1
        raise KeyboardInterrupt

    run.loop._clock = clock
    with pytest.raises(KeyboardInterrupt):
        run.say("Hallo")
    assert run.loop.state is LoopState.IDLE


def test_turn_end_reasons_in_the_schema_cover_every_reason_the_loop_can_set() -> None:
    """N4: Jede Zeichenkette, die `loop.py` als Grund an `_finish` gibt oder in `ended`/`reason` zuweist, steht im Schema."""
    import ast
    import typing
    from pathlib import Path

    from scout_research.agent import loop as loop_module
    from scout_research.agent.trace import TurnReason

    allowed = set(typing.get_args(TurnReason))
    found: set[str] = set()
    for node in ast.walk(ast.parse(Path(loop_module.__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_finish" \
                and len(node.args) >= 3:
            arg = node.args[2]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add(arg.value)
            if isinstance(arg, ast.JoinedStr):  # f"intro_{...}": Präfix muss durch intro_* gedeckt sein
                assert arg.values[0].value == "intro_"
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {"ended", "reason"} for t in node.targets) \
                and not isinstance(node.value, ast.Call):
            found |= {c.value for c in ast.walk(node.value) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "_closing" for t in node.targets):
            found |= {c.value for c in ast.walk(node.value) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    assert found and found <= allowed, sorted(found - allowed)


def test_the_turn_end_schema_rejects_an_unknown_reason() -> None:
    from scout_research.agent.trace import TraceSchemaError, validate_loop_event

    good = {"event": "turn_end", "outcome": "done", "reason": "commentary", "state": "idle", "calls": 1, "tools": 0,
            "closing": None, "session_cost_usd": 0.0}
    validate_loop_event(good)
    for reason in ("interrupted_twice", "aborted", "not_found", "unprocessable", "budget_day", None):
        validate_loop_event({**good, "reason": reason})
    with pytest.raises(TraceSchemaError):
        validate_loop_event({**good, "reason": "gibt_es_nicht"})
