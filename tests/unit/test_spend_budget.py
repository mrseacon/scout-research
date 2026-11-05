"""Budgetdeckel je Lauf und je Tag (TA-12b, Plan 12.8): Kostenbuch, `SpendBudget`, Anbindung an den Loop, Schema, Settings."""

from __future__ import annotations

import ast
import json
import multiprocessing
import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from scout_research.agent import texts
from scout_research.agent.budgets import LoopConfig, Price, Usage, cost_usd
from scout_research.agent.loop import AgentLoop
from scout_research.agent.model_client import ModelCallError, ScriptedModelClient
from scout_research.agent.spend import LedgerError, SpendBudget, SpendLedger
from scout_research.agent.trace import TraceSchemaError, validate_loop_event
from scout_research.config import Settings
from scout_research.tools.session import HumanInput
from tests.unit.loop_harness import Run, say, use
from tests.unit.tool_world import standard_world

_PRICE = LoopConfig().price
_USAGE = Usage(100_000, 10_000)  # 0,30 $
_COST = cost_usd(_USAGE, _PRICE)
_SMALL = Usage(1_000, 200)


class _Day:
    def __init__(self, day: date = date(2026, 11, 5)) -> None:
        self.day = day

    def __call__(self) -> date:
        return self.day


def _budget(tmp_path: Path, run: float = 1.0, day: float = 3.0, clock=None) -> SpendBudget:
    return SpendBudget(run, day, SpendLedger(tmp_path / "ledger.json", clock or _Day()))


def _run(steps, budget: SpendBudget | None, **kw) -> Run:
    return Run(standard_world(), steps, budget=budget, **kw)


def _json_len(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _expected_first_upper(request, config: LoopConfig | None = None) -> float:
    """Unabhängig nachgerechnete Schranke des ersten Aufrufs: Bytes(system, tools, messages) + 1000 + 50 je Nachricht,
    zum teuersten Input-Satz, plus max_tokens zum Output-Preis."""
    config = config or LoopConfig()
    price = config.price
    tokens = _json_len({"system": request.system, "tools": request.tools, "messages": request.messages}) \
        + 1000 + 50 * len(request.messages)
    return (tokens * max(price.input, price.cache_read, price.cache_write) + config.max_tokens * price.output) / 1e6


def _first_request(text: str = "Frage", **kw):
    probe = Run(standard_world(), [say("Hallo", usage=_SMALL)], **kw)
    probe.say(text)
    return probe.client.requests[0]


# --- Kostenbuch ----------------------------------------------------------------------------------------------------


def test_ledger_missing_file_is_zero_and_add_persists_per_utc_day(tmp_path: Path) -> None:
    day = _Day()
    ledger = SpendLedger(tmp_path / "sub" / "ledger.json", day)
    assert ledger.day_total() == 0.0
    ledger.add(0.25)
    ledger.add(0.5)
    assert json.loads((tmp_path / "sub" / "ledger.json").read_text()) == {"2026-11-05": 0.75}
    assert not list((tmp_path / "sub").glob("*.tmp"))
    day.day = date(2026, 11, 6)
    assert ledger.day_total() == 0.0
    ledger.add(0.1)
    assert json.loads((tmp_path / "sub" / "ledger.json").read_text()) == {"2026-11-05": 0.75, "2026-11-06": 0.1}


@pytest.mark.parametrize("content", ["{kaputt", "[1, 2]", '{"2026-11-05": -1}', '{"2026-11-05": "viel"}',
                                     '{"2026-11-05": true}', '{"2026-11-05": NaN}', ""])
def test_ledger_broken_file_raises_ledger_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(content)
    ledger = SpendLedger(path, _Day())
    with pytest.raises(LedgerError):
        ledger.day_total()
    with pytest.raises(LedgerError):
        ledger.add(0.1)
    assert path.read_text() == content  # ein defektes Buch wird nie überschrieben


# --- Grenzwerte ----------------------------------------------------------------------------------------------------


def test_run_limit_is_inclusive(tmp_path: Path) -> None:
    budget = _budget(tmp_path, run=0.5)
    budget.charge(0.4999)
    assert budget.exhausted() is None
    budget.charge(0.0001)
    assert budget.run_spent == pytest.approx(0.5)
    budget.run_spent = 0.5
    assert budget.exhausted() == "budget_run"


def test_day_limit_is_inclusive(tmp_path: Path) -> None:
    budget = _budget(tmp_path, run=10.0, day=2.0)
    budget.charge(1.999)
    assert budget.exhausted() is None
    budget.ledger.add(0.001)
    assert budget.ledger.day_total() == pytest.approx(2.0)
    (tmp_path / "ledger.json").write_text(json.dumps({"2026-11-05": 2.0}))
    assert budget.exhausted() == "budget_day"


def test_broken_ledger_locks_the_budget(tmp_path: Path) -> None:
    budget = _budget(tmp_path)
    (tmp_path / "ledger.json").write_text("{kaputt")
    assert budget.exhausted() == "budget_ledger"


def test_unreadable_ledger_path_locks_after_the_charge(tmp_path: Path) -> None:
    (tmp_path / "ledger.json").mkdir()  # ein Ordner an der Stelle der Datei: schon das Lesen scheitert
    budget = _budget(tmp_path)
    budget.charge(0.1)
    assert budget.exhausted() == "budget_ledger"


def test_write_failure_alone_locks_the_budget_and_leaves_no_temp_file(tmp_path: Path, monkeypatch) -> None:
    """M1: Lesen geht, nur das Ersetzen scheitert; das Buch bleibt unverändert, die temp-Datei wird entfernt."""
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"2026-11-05": 0.5}))
    budget = _budget(tmp_path)
    assert budget.ledger.day_total() == 0.5 and budget.exhausted() is None

    def failing_replace(src, dst) -> None:
        raise PermissionError("kein Schreibrecht")

    monkeypatch.setattr("scout_research.agent.spend.os.replace", failing_replace)
    budget.charge(0.1)
    assert budget.exhausted() == "budget_ledger"
    assert json.loads(path.read_text()) == {"2026-11-05": 0.5}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ledger.json", "ledger.json.lock"]


@pytest.mark.parametrize("content", ["[" * 100_000, '{"2026-11-05": ' + "9" * 400 + "}"])
def test_ledger_recursion_and_overflow_become_ledger_error(tmp_path: Path, content: str) -> None:
    """N2: tief verschachteltes JSON (RecursionError) und eine Zahl jenseits von float (OverflowError)."""
    path = tmp_path / "ledger.json"
    path.write_text(content)
    ledger = SpendLedger(path, _Day())
    with pytest.raises(LedgerError):
        ledger.day_total()
    with pytest.raises(LedgerError):
        ledger.add(0.1)
    assert _budget(tmp_path).exhausted() == "budget_ledger"


def _fixed_day() -> date:
    return date(2026, 11, 5)


def _book_many(path: str, count: int, amount: float) -> None:
    ledger = SpendLedger(Path(path), _fixed_day)
    for _ in range(count):
        ledger.add(amount)


def test_two_processes_booking_in_parallel_lose_no_booking(tmp_path: Path) -> None:
    """H1: zwei echte Prozesse buchen je N-mal; die Summe ist exakt (0,25 ist binär exakt)."""
    path = tmp_path / "ledger.json"
    count = 150
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_book_many, args=(str(path), count, 0.25)) for _ in range(2)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(120)
        assert proc.exitcode == 0
    assert json.loads(path.read_text()) == {"2026-11-05": 2 * count * 0.25}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ledger.json", "ledger.json.lock"]


def test_day_total_carries_over_between_runs_and_the_day_change_resets_only_the_day(tmp_path: Path) -> None:
    day = _Day()
    first = _budget(tmp_path, run=10.0, day=1.0, clock=day)
    first.charge(1.0)
    second = _budget(tmp_path, run=10.0, day=1.0, clock=day)
    assert second.run_spent == 0.0 and second.exhausted() == "budget_day"
    day.day = date(2026, 11, 6)
    assert first.run_spent == 1.0  # der Lauf-Zähler bleibt
    assert second.exhausted() is None
    assert first.exhausted() is None  # run_limit 10, Tag neu


def test_from_settings_puts_the_ledger_under_runs_dir(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, edgar_contact_email="tester@example.org", runs_dir=tmp_path,
                        budget_run_usd=0.5, budget_day_usd=1.5)
    budget = SpendBudget.from_settings(settings)
    assert (budget.run_limit_usd, budget.day_limit_usd) == (0.5, 1.5)
    assert budget.ledger.path == tmp_path / "spend_ledger.json"


# --- Loop ----------------------------------------------------------------------------------------------------------


def test_loop_charges_run_and_ledger_and_closes_at_the_run_limit(tmp_path: Path) -> None:
    small = cost_usd(_SMALL, _PRICE)
    budget = _budget(tmp_path, run=0.2)
    # der zweite Aufruf meldet mehr Nutzung als seine Schranke (Zusicherung des Skripts): die Nachprüfung schließt
    run = _run([say("Hallo", usage=_SMALL), say("Guten Tag", usage=_USAGE), say("nie")], budget)
    first = run.say("Frage eins")
    assert first.outcome == "done" and budget.run_spent == pytest.approx(small)
    second = run.say("Frage zwei")
    assert second.outcome == "closed" and second.reason == "budget_run"
    assert budget.run_spent == pytest.approx(small + _COST)
    assert budget.ledger.day_total() == pytest.approx(small + _COST)
    stops = [e for e in run.loop.events if e["event"] == "budget_stop"]
    assert len(stops) == 1 and stops[0]["reason"] == "budget_run"
    assert stops[0]["run_spent_usd"] == pytest.approx(small + _COST)
    assert run.loop.events[-1]["closing"] == "budget_run"
    run.assert_invariants()


def test_closed_turn_ends_with_the_fixed_budget_notice_as_its_last_item(tmp_path: Path) -> None:
    run = _run([say("Rohtext des Modells", usage=_USAGE)], _budget(tmp_path, run=_COST))
    result = run.say("Frage")
    assert result.outcome == "closed"
    shown = [s.text for s in result.shown]
    assert texts.user_text("de", "budget_run_closed") in shown
    assert shown[-1] == texts.user_text("de", "budget_run_closed")  # der feste Text steht als Hinweis am Ende
    assert [x.kind for x in result.shown][-1] == "notice"


def test_failed_call_with_usage_is_booked_in_run_and_ledger_and_can_close_the_run(tmp_path: Path) -> None:
    budget = _budget(tmp_path, run=_COST)
    run = _run([ModelCallError("stalled", usage=_USAGE, usage_estimated=True), say("nie")], budget)
    result = run.say("Frage")
    assert result.outcome == "closed" and result.reason == "stalled"
    assert budget.run_spent == pytest.approx(_COST) and budget.ledger.day_total() == pytest.approx(_COST)
    assert run.loop.events[-1]["closing"] == "budget_run"
    assert texts.user_text("de", "budget_run_closed") in [s.text for s in result.shown]
    run.assert_invariants()


def test_second_run_on_the_same_ledger_starts_with_the_first_ones_total_and_makes_no_call(tmp_path: Path) -> None:
    first = _budget(tmp_path, day=100.0)
    run1 = _run([say("Hallo", usage=_USAGE), say("Guten Tag", usage=_USAGE)], first)
    run1.say("a")
    run1.say("b")
    second = _budget(tmp_path, day=_COST * 2)
    run2 = _run([], second)  # ein Aufruf würde das leere Skript auslösen
    result = run2.say("c")
    assert result.outcome == "closed" and result.reason == "budget_day"
    assert run2.client.requests == []
    assert texts.user_text("de", "budget_day_closed") in [s.text for s in result.shown]
    stop = next(e for e in run2.loop.events if e["event"] == "budget_stop")
    assert stop["reason"] == "budget_day" and stop["run_spent_usd"] == 0.0
    run2.assert_invariants()


def test_broken_ledger_blocks_the_call(tmp_path: Path) -> None:
    (tmp_path / "ledger.json").write_text("{kaputt")
    run = _run([], _budget(tmp_path))
    result = run.say("Frage")
    assert result.outcome == "closed" and result.reason == "budget_ledger"
    assert run.client.requests == []
    stop = next(e for e in run.loop.events if e["event"] == "budget_stop")
    assert stop["day_total_usd"] is None
    assert texts.user_text("de", "budget_ledger_closed") in [s.text for s in result.shown]
    run.assert_invariants()


def test_run_spans_two_loops_with_the_same_budget(tmp_path: Path) -> None:
    budget = _budget(tmp_path, run=_COST * 2)
    run_a = _run([say("Hallo", usage=_USAGE)], budget)
    run_b = _run([say("Guten Tag", usage=_USAGE), say("nie")], budget)
    assert run_a.say("a").outcome == "done"
    assert budget.run_spent == pytest.approx(_COST)
    assert run_b.say("b").reason == "budget_run"
    assert budget.run_spent == pytest.approx(2 * _COST)
    run_c = _run([], budget)
    assert run_c.say("c").reason == "budget_run" and run_c.client.requests == []


def test_explicit_budget_none_means_no_cap_and_no_budget_stop() -> None:
    run = Run(standard_world(), [say("Hallo", usage=_USAGE)], budget=None)
    assert run.say("a").outcome == "done"
    assert not any(e["event"] == "budget_stop" for e in run.loop.events)


# --- Schema und Texte ------------------------------------------------------------------------------------------------


def test_budget_stop_event_is_accepted_by_the_schema_and_checked_strictly() -> None:
    event = {"event": "budget_stop", "reason": "budget_day", "run_spent_usd": 0.3, "day_total_usd": 3.1}
    validate_loop_event(event)
    validate_loop_event({**event, "reason": "budget_ledger", "day_total_usd": None})
    for bad in ({**event, "reason": "anderes"}, {**event, "extra": 1}, {**event, "run_spent_usd": "1"}):
        with pytest.raises(TraceSchemaError):
            validate_loop_event(bad)


@pytest.mark.parametrize("closing", ["budget_run", "budget_day", "budget_ledger"])
def test_turn_end_accepts_the_new_closing_reasons(closing: str) -> None:
    validate_loop_event({"event": "turn_end", "outcome": "closed", "reason": closing, "state": "closed", "calls": 0,
                         "tools": 0, "closing": closing, "session_cost_usd": 0.0})


@pytest.mark.parametrize("language", ["de", "en"])
def test_budget_texts_exist_and_are_free_of_digits(language: str) -> None:
    for key in ("budget_run_closed", "budget_day_closed", "budget_ledger_closed"):
        value = texts.user_text(language, key)
        assert value and not any(ch.isdigit() for ch in value)


# --- Settings ------------------------------------------------------------------------------------------------------


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, edgar_contact_email="tester@example.org", **kw)


def test_settings_budget_defaults_and_blank_means_default(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings()
    assert (s.budget_run_usd, s.budget_day_usd) == (1.0, 3.0)
    monkeypatch.setenv("BUDGET_RUN_USD", "")
    monkeypatch.setenv("BUDGET_DAY_USD", " ")
    s = _settings()
    assert (s.budget_run_usd, s.budget_day_usd) == (1.0, 3.0)


@pytest.mark.parametrize("field", ["budget_run_usd", "budget_day_usd"])
@pytest.mark.parametrize("value", [0, -1, -0.01])
def test_settings_reject_zero_and_negative_budgets(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        _settings(**{field: value})


# --- Vorprüfung im schlechtesten Fall (O-24) -----------------------------------------------------------------------


def test_upper_bound_of_the_first_call_is_computed_independently(tmp_path: Path) -> None:
    """M-B (1): Bytes(system, tools, messages) + 1000 + 50 je Nachricht, teuerster Input-Satz, max_tokens zum Output-Preis."""
    request = _first_request()
    price = Price(input=2.0, output=10.0, cache_read=0.2, cache_write=3.0)  # Cache-Write ist hier der teuerste Satz
    config = LoopConfig(prices={"claude-sonnet-5-5": price})
    run = Run(standard_world(), [], config=config)
    assert run.loop._next_call_upper_usd(request) == pytest.approx(_expected_first_upper(request, config), rel=1e-12)
    assert run.loop._next_call_upper_usd(request) > _expected_first_upper(request)  # der Satz 3,0 zählt


def test_tools_count_towards_the_upper_bound() -> None:
    request = _first_request()
    run = Run(standard_world(), [])
    full = run.loop._next_call_upper_usd(request)
    without_tools = run.loop._next_call_upper_usd(replace(request, tools=[]))
    assert without_tools < full
    assert full - without_tools == pytest.approx((_json_len(request.tools) - _json_len([])) * 2.5 / 1e6, rel=1e-9)


def test_a_request_far_above_the_context_limit_is_not_capped_at_it(tmp_path: Path) -> None:
    """M-B (3) / H-A: Kein Deckel bei `context_limit_tokens`; bei passendem Limit erfolgt kein Aufruf."""
    config = LoopConfig(context_limit_tokens=1000)
    big = "Frage " + "x" * 20_000
    request = _first_request(big, config=config)
    run = Run(standard_world(), [], config=config)
    upper = run.loop._next_call_upper_usd(request)
    capped = (1000 * 2.5 + config.max_tokens * 10.0) / 1e6
    assert upper > capped and upper == pytest.approx(_expected_first_upper(request, config), rel=1e-12)
    blocked = _run([], _budget(tmp_path / "a", run=upper), config=config)
    assert blocked.say(big).reason == "budget_run" and blocked.client.requests == []
    # ein Limit, das nur den gedeckelten Wert überstiege, hätte den Aufruf zugelassen; hier erfolgt er nicht
    assert capped < upper
    allowed = _run([say("Hallo", usage=_SMALL)], _budget(tmp_path / "b", run=upper + 1e-6), config=config)
    allowed.say(big)
    assert len(allowed.client.requests) == 1


def test_follow_up_call_uses_the_reported_usage_plus_the_bytes_of_what_was_appended() -> None:
    """M-B (4): Schranke des zweiten Aufrufs = Kontext-Token des ersten + Bytes der neuen Nachrichten + Aufschlag."""
    run = Run(standard_world(), [say("Hallo", usage=Usage(2_000, 300, 40, 10)), say("Guten Tag", usage=_SMALL)])
    run.say("Frage eins")
    run.say("Frage zwei")
    first, second = run.client.requests
    appended = second.messages[len(first.messages):]
    assert len(appended) == 2  # Antwort des Assistenten und die neue Nutzernachricht
    tokens = 2_000 + 40 + 10 + _json_len(appended) + 1000 + 50 * len(second.messages)
    events = [e for e in run.loop.events if e["event"] == "model_call"]
    assert events[1]["upper_input_tokens"] == tokens
    assert events[0]["upper_input_tokens"] == _json_len(
        {"system": first.system, "tools": first.tools, "messages": first.messages}) + 1000 + 50 * len(first.messages)
    for e in events:
        assert e["upper_input_tokens"] >= e["input_tokens"] + e["cache_read_input_tokens"] \
            + e["cache_creation_input_tokens"]
    run.assert_invariants()


def test_a_reference_that_is_not_a_prefix_falls_back_to_the_full_byte_count() -> None:
    run = Run(standard_world(), [say("Hallo", usage=_SMALL), say("Guten Tag", usage=_SMALL)])
    run.say("Frage eins")
    run.loop._bound_ref = (10, [{"role": "user", "content": [{"type": "text", "text": "ganz anders"}]}])
    run.say("Frage zwei")
    second = run.client.requests[1]
    expected = _json_len({"system": second.system, "tools": second.tools, "messages": second.messages}) \
        + 1000 + 50 * len(second.messages)
    assert [e for e in run.loop.events if e["event"] == "model_call"][1]["upper_input_tokens"] == expected


def test_precheck_run_limit_is_exact(tmp_path: Path) -> None:
    upper = _expected_first_upper(_first_request())
    below = _run([say("Hallo", usage=_SMALL)], _budget(tmp_path / "a", run=upper + 1e-6))
    below.say("Frage")  # der Aufruf findet statt
    assert len(below.client.requests) == 1
    equal = _run([], _budget(tmp_path / "b", run=upper))
    result = equal.say("Frage")
    assert result.outcome == "closed" and result.reason == "budget_run" and equal.client.requests == []
    above = _run([], _budget(tmp_path / "c", run=upper / 2))
    assert above.say("Frage").reason == "budget_run" and above.client.requests == []


def test_precheck_day_limit_is_exact_and_separate_from_the_run_limit(tmp_path: Path) -> None:
    upper = _expected_first_upper(_first_request())
    (tmp_path / "ledger.json").write_text(json.dumps({"2026-11-05": 0.5}))
    below = _run([say("Hallo", usage=_SMALL)], _budget(tmp_path, run=1000.0, day=0.5 + upper + 1e-6))
    below.say("Frage")
    assert len(below.client.requests) == 1
    (tmp_path / "ledger.json").write_text(json.dumps({"2026-11-05": 0.5}))
    equal = _run([], _budget(tmp_path, run=1000.0, day=0.5 + upper))
    result = equal.say("Frage")
    assert result.outcome == "closed" and result.reason == "budget_day" and equal.client.requests == []
    stop = next(e for e in equal.loop.events if e["event"] == "budget_stop")
    assert stop["reason"] == "budget_day" and stop["day_total_usd"] == 0.5
    equal.assert_invariants()


def test_a_response_with_tool_use_that_crosses_the_limit_dispatches_nothing(tmp_path: Path) -> None:
    """M3: Die Kosten dieser Antwort reißen die Grenze; ihr tool_use wird nicht ausgeführt (TURN_ENDED)."""
    called: list[str] = []

    def spy(ctx, name, args):
        called.append(name)
        raise AssertionError("darf nicht ausgeführt werden")

    steps = [use(("compute_comps_table", {"peer_set_id": "ps_1"}), usage=_USAGE), say("nie")]
    run = _run(steps, _budget(tmp_path, run=_COST), dispatch=spy)
    result = run.say("Frage")
    assert result.outcome == "closed" and result.reason == "budget_run"
    assert called == [] and not any(e["event"] == "tool" for e in run.loop.events)
    assert "TURN_ENDED" in str(run.loop.history) + str(run.loop.pending)
    assert [e["reason"] for e in run.loop.events if e["event"] == "budget_stop"] == ["budget_run"]
    assert run.loop.events[-1]["event"] == "turn_end" and run.loop.events[-1]["closing"] == "budget_run"
    assert len(run.client.requests) == 1
    run.assert_invariants()


def test_budget_reason_wins_over_cost_and_context(tmp_path: Path) -> None:
    """N1: Reißt ein Aufruf zugleich Kosten- bzw. Kontextgrenze und Budget, ist das Budget der Schließgrund."""
    cost_config = LoopConfig(cost_limit_usd=_COST / 2)
    run = _run([say("Hallo", usage=_USAGE)], _budget(tmp_path / "a", run=_COST), config=cost_config)
    assert run.say("a").reason == "budget_run" and run.loop.events[-1]["closing"] == "budget_run"
    context_config = LoopConfig(context_limit_tokens=1000)
    run = _run([say("Hallo", usage=_USAGE)], _budget(tmp_path / "b", run=_COST), config=context_config)
    assert run.say("a").reason == "budget_run" and run.loop.events[-1]["closing"] == "budget_run"
    run = _run([say("Hallo", usage=_USAGE)], _budget(tmp_path / "c", run=1000.0, day=_COST), config=cost_config)
    assert run.say("a").reason == "budget_day"
    # ohne Budgetgrund bleibt es bei der Sitzungsgrenze
    run = _run([say("Hallo", usage=_USAGE)], _budget(tmp_path / "d"), config=cost_config)
    assert run.say("a").reason == "cost"


# --- Kopf des Trace und Architektur --------------------------------------------------------------------------------


def test_trace_header_carries_the_budget_limits(tmp_path: Path) -> None:
    from tests.unit.test_trace import _lines, _writer

    run = _run([say("Hallo")], _budget(tmp_path / "led", run=0.5, day=1.5))
    with _writer(run, tmp_path / "t") as tw:
        pass
    params = _lines(tw.path)[0]["params"]
    assert params["budget_run_usd"] == 0.5 and params["budget_day_usd"] == 1.5
    run = _run([say("Hallo")], None)
    with _writer(run, tmp_path / "u") as tw:
        pass
    params = _lines(tw.path)[0]["params"]
    assert params["budget_run_usd"] is None and params["budget_day_usd"] is None


def _loop_call_problems(source: str, allow_none: bool) -> list[int]:
    """Zeilen der `AgentLoop(...)`-Aufrufe ohne `budget=` bzw. (wenn `allow_none` nicht gilt) mit `budget=None`."""
    bad: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None) != "AgentLoop":
            continue
        values = [k.value for k in node.keywords if k.arg == "budget"]
        if not values or (not allow_none and isinstance(values[0], ast.Constant) and values[0].value is None):
            bad.append(node.lineno)
    return bad


def test_loop_call_scan_flags_missing_budget_and_none_outside_tests() -> None:
    assert _loop_call_problems("AgentLoop(a, b, c)", True) == [1]
    assert _loop_call_problems("x.AgentLoop(a, budget=None)", True) == []
    assert _loop_call_problems("x.AgentLoop(a, budget=None)", False) == [1]
    assert _loop_call_problems("AgentLoop(a, budget=SpendBudget.from_settings(s))", False) == []
    assert _loop_call_problems("AgentLoop(a, budget=b)", False) == []


def test_every_agent_loop_construction_passes_budget_explicitly() -> None:
    """H2/M-C: AST-Scan; in src, eval und scripts muss `budget=` ein echter Wert sein (nicht `None`), unter tests ist
    `budget=None` erlaubt."""
    root = Path(__file__).resolve().parents[2]
    problems: list[str] = []
    found = 0
    for folder in ("src", "eval", "scripts", "tests"):
        for path in sorted((root / folder).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            found += source.count("AgentLoop")
            problems += [f"{path.relative_to(root)}:{n}" for n in _loop_call_problems(source, folder == "tests")]
    assert found, "der Scan findet keinen einzigen AgentLoop (Harness fehlt?)"
    assert problems == []


# --- Sperre mit Frist, fsync, Reihenfolge ---------------------------------------------------------------------------


def test_a_lock_held_from_outside_ends_in_ledger_error_after_the_deadline(tmp_path: Path) -> None:
    import fcntl
    import time

    ledger = SpendLedger(tmp_path / "ledger.json", _Day(), lock_timeout_s=0.2)
    with open(tmp_path / "ledger.json.lock", "a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        started = time.monotonic()
        with pytest.raises(LedgerError):
            ledger.add(0.1)
        assert time.monotonic() - started < 3.0
    assert not (tmp_path / "ledger.json").exists()
    ledger.add(0.1)  # nach Freigabe geht es wieder
    assert ledger.day_total() == pytest.approx(0.1)
    # über das Budget: gesperrtes Buch = `budget_ledger`
    budget = SpendBudget(1.0, 3.0, ledger)
    with open(tmp_path / "ledger.json.lock", "a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        budget.charge(0.1)
    assert budget.exhausted() == "budget_ledger"


def test_the_ledger_is_synced_before_it_replaces_the_old_one(tmp_path: Path, monkeypatch) -> None:
    order: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr("scout_research.agent.spend.os.fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr("scout_research.agent.spend.os.replace",
                        lambda a, b: (order.append("replace"), real_replace(a, b))[1])
    SpendLedger(tmp_path / "ledger.json", _Day()).add(0.1)
    assert order == ["fsync", "replace"]


def test_the_model_call_event_is_recorded_before_the_booking(tmp_path: Path, monkeypatch) -> None:
    """Ein Strg+C während `charge` darf das Ereignis nicht verlieren."""
    budget = _budget(tmp_path)

    def interrupted(cost: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(budget, "charge", interrupted)
    run = _run([say("Hallo", usage=_SMALL)], budget)
    run.loop.run_turn(HumanInput("Frage"))  # der Loop fängt Strg+C ab und beendet den Zug
    assert [e["event"] for e in run.loop.events if e["event"] == "model_call"] == ["model_call"]


def _interrupt_ledger(budget: SpendBudget, monkeypatch) -> None:
    def boom(cost: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(budget.ledger, "add", boom)


def test_ctrl_c_during_the_booking_of_a_successful_call_locks_the_budget_and_keeps_the_event(tmp_path, monkeypatch) -> None:
    budget = _budget(tmp_path)
    _interrupt_ledger(budget, monkeypatch)
    run = _run([say("Hallo", usage=_SMALL)], budget)
    result = run.loop.run_turn(HumanInput("Frage"))
    assert [e["event"] for e in run.loop.events].count("model_call") == 1
    assert budget.exhausted() == "budget_ledger"
    assert result.outcome == "closed" and run.loop.events[-1]["closing"] == "budget_ledger"


def test_ctrl_c_during_the_booking_of_a_failed_call_locks_the_budget_and_keeps_the_event(tmp_path, monkeypatch) -> None:
    budget = _budget(tmp_path)
    _interrupt_ledger(budget, monkeypatch)
    run = _run([ModelCallError("unavailable", usage=_USAGE, usage_estimated=True)], budget)
    run.loop.run_turn(HumanInput("Frage"))
    kinds = [e["event"] for e in run.loop.events]
    assert "model_error" in kinds
    assert budget.exhausted() == "budget_ledger"
    assert run.loop.events[-1]["closing"] == "budget_ledger"


@pytest.mark.parametrize("config, usage, closing", [
    (LoopConfig(cost_limit_usd=_COST / 2), _USAGE, "cost"),
    (LoopConfig(context_limit_tokens=1000), _USAGE, "context"),
])
def test_ctrl_c_during_the_booking_still_applies_the_session_limits(tmp_path, monkeypatch, config, usage, closing) -> None:
    budget = _budget(tmp_path, run=1000.0, day=1000.0)

    def boom(cost: float) -> None:  # unterbricht, ohne das Budget selbst zu sperren: nur die Sitzungsgrenze kann greifen
        raise KeyboardInterrupt

    monkeypatch.setattr(budget, "charge", boom)
    run = _run([say("Hallo", usage=usage)], budget, config=config)
    run.loop.run_turn(HumanInput("Frage"))
    assert run.loop.events[-1]["closing"] == closing


def test_a_failed_call_keeps_the_old_reference_for_the_next_bound() -> None:
    run = Run(standard_world(), [say("Hallo", usage=Usage(2_000, 300)), ModelCallError("unavailable"),
                                 say("Guten Tag", usage=_SMALL)])
    run.say("Frage eins")
    run.say("Frage zwei")  # transienter Fehler
    run.say("Frage drei")
    first, _, third = run.client.requests
    appended = third.messages[len(first.messages):]
    expected = 2_000 + _json_len(appended) + 1000 + 50 * len(third.messages)
    assert [e for e in run.loop.events if e["event"] == "model_call"][1]["upper_input_tokens"] == expected


def test_usage_without_context_tokens_gives_no_reference() -> None:
    run = Run(standard_world(), [say("Hallo", usage=Usage(0, 0)), say("Guten Tag", usage=_SMALL)])
    run.say("Frage eins")
    run.say("Frage zwei")
    second = run.client.requests[1]
    full = _json_len({"system": second.system, "tools": second.tools, "messages": second.messages}) \
        + 1000 + 50 * len(second.messages)
    assert [e for e in run.loop.events if e["event"] == "model_call"][1]["upper_input_tokens"] == full


def test_the_reference_never_shrinks_while_the_prefix_holds() -> None:
    run = Run(standard_world(), [say("a", usage=Usage(5_000, 100)), say("b", usage=Usage(1_000, 100)),
                                 say("c", usage=_SMALL)])
    for text in ("eins", "zwei", "drei"):
        run.say(text)
    second, third = run.client.requests[1], run.client.requests[2]
    appended = third.messages[len(second.messages):]
    expected = 5_000 + _json_len(appended) + 1000 + 50 * len(third.messages)
    assert [e for e in run.loop.events if e["event"] == "model_call"][2]["upper_input_tokens"] == expected
