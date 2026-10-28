"""Trace-Schreiber (Plan 6, Schritt 8): T9, T12 für den Trace, LT-33 vollständig, Whitelist des Kopfs, Schlussereignis.

Die Tests laufen gegen die Test-Welt und das geskriptete Modell; nichts davon ruft das Netz."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from scout_research.agent import trace
from scout_research.agent.budgets import LoopConfig, Usage, cost_usd
from scout_research.agent.loop import LoopState
from scout_research.agent.model_client import ModelCallError
from scout_research.agent.prompt_loader import SystemPrompt, load_system_prompt, tools_hash
from scout_research.agent.trace import (
    HeaderEvent,
    TraceLeakError,
    TraceParams,
    TraceSchemaError,
    TraceWriter,
    current_git_commit,
    validate_loop_event,
    validate_trace_file,
    validate_trace_line,
)
from scout_research.config import Settings
from scout_research.tools.handlers import tool_definitions
from tests.unit.loop_harness import COMMENTARY, SYSTEM, Run, say, use
from tests.unit.test_agent_loop_core import _flow_to_proposal
from tests.unit.tool_world import AAPL_CIK, FINNHUB_KEY, MSFT_CIK, NVDA_CIK, SECRETS, standard_world

FIXED_NOW = datetime(2026, 10, 28, 12, 30, 45, tzinfo=timezone.utc)
FAKE_ANTHROPIC_KEY = "sk-ant-FAKE-0123456789abcdefghij"
ALL_SECRETS = [*SECRETS, FAKE_ANTHROPIC_KEY, "welt.tester", "example.invalid", "FAKE-FINNHUB-KEY"]
BAD_COMMENTARY = "NVDA liegt bei 25 Prozent über dem Median [[stat:ev_ebitda:median]]."
BAD_THREE = [f"NVDA liegt bei {n} Prozent über dem Median [[stat:ev_ebitda:median]]." for n in (25, 26, 27)]
LEAK = "54321"  # steht als Zahl (Token-Anzahl) im Ereignis: die Schwärzung der Strings sieht ihn nicht, die Gegenprobe schon


def _prompt(text: str = SYSTEM) -> SystemPrompt:
    return SystemPrompt(text, "0.0.0-test", hashlib.sha256(text.encode("utf-8")).hexdigest())


def _writer(run: Run, tmp_path: Path, **kw: Any) -> TraceWriter:
    return TraceWriter(run.loop, kw.pop("prompt", None) or _prompt(run.loop.system_prompt), base_dir=tmp_path, **kw)


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _of(lines: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [x for x in lines if x["event"] == event]


def _run(steps: list, **kw: Any) -> Run:
    kw.setdefault("now", lambda: FIXED_NOW)
    return Run(standard_world(), steps, **kw)


# --- T9: ein geskripteter Lauf ist schema-valide -----------------------------------------------------------------


def _scripted_full_run(tmp_path: Path, **kw: Any) -> tuple[Run, Path]:
    run = Run(standard_world(), [], now=lambda: FIXED_NOW, **kw)
    run.client._steps = [
        use(("resolve_company", {"query": "NVDA"})),
        use(("find_peer_candidates", {"target_cik": NVDA_CIK})),
        run.propose(),
        say("Hier ist mein Vorschlag."),
        run.compute(),
        run.submit(BAD_COMMENTARY),
        run.submit(COMMENTARY),
    ]
    with _writer(run, tmp_path) as tw:
        run.say("Mach Comps für NVDA")
        tw.flush()
        run.confirm(keep=(AAPL_CIK,))  # MSFT entfernt: Abweichung vom Vorschlag (V4)
        tw.flush()
    return run, tw.path


def test_t9_a_scripted_run_to_an_accepted_commentary_is_schema_valid_and_complete(tmp_path: Path) -> None:
    run, path = _scripted_full_run(tmp_path)
    models = validate_trace_file(path)  # wirft bei der ersten ungültigen Zeile
    lines = _lines(path)
    assert len(models) == len(lines) and all(validate_trace_line(json.dumps(x)) for x in lines)
    assert lines[0]["event"] == "header" and lines[0]["run_mode"] == "interactive"
    assert lines[-1]["event"] == "end" and lines[-1]["state"] == "idle" and lines[-1]["reason"] is None
    assert [x["seq"] for x in lines] == list(range(len(lines)))
    assert all(x["run_id"] == lines[0]["run_id"] for x in lines)
    assert all(a["turn"] <= b["turn"] for a, b in zip(lines, lines[1:]))

    gates = _of(lines, "gate")
    assert [g["decision"] for g in gates] == ["proposed", "confirmed"]  # die Loop-Ereignisse `gate` werden übersprungen
    proposed, confirmed = gates
    assert proposed["candidate_set"]["target_cik"] == NVDA_CIK and AAPL_CIK in proposed["candidate_set"]["candidates"]
    assert {p["cik"] for p in proposed["peers"]} == {AAPL_CIK, MSFT_CIK}
    assert all(p["rationale"] == "Ähnliches Geschäftsmodell" and p["ticker"] for p in proposed["peers"])
    assert proposed["rationale_source"]
    assert confirmed["channel"] == "cli" and confirmed["confirmed_by"] == "human"
    assert confirmed["removed"] == [MSFT_CIK] and confirmed["confirmed"] == [AAPL_CIK] and confirmed["added"] == []
    assert sorted(confirmed["proposed"]) == sorted([AAPL_CIK, MSFT_CIK])

    commentary = _of(lines, "commentary")
    assert [c["action"] for c in commentary] == ["rejected", "accepted_after_n"]
    assert [c["attempt"] for c in commentary] == [1, 2]
    assert commentary[0]["raw_text"] == BAD_COMMENTARY and commentary[0]["problems"] and commentary[0]["rendered"] is None
    accepted = commentary[1]
    assert accepted["rendered"] == run.ctx.commentaries[run.table_id].rendered and accepted["slots"]
    assert "warnings_addressed" in accepted and "warnings_not_addressed" in accepted

    # M1: Gate- und Kommentar-Einträge stehen an ihrer Stelle im Ablauf, nicht gesammelt am Ende des Flush
    kinds = [(x["event"], x.get("name") or x.get("decision")) for x in lines]
    at = kinds.index(("tool", "propose_peer_set"))
    assert kinds[at + 1] == ("gate", "proposed")
    at = kinds.index(("gate", "confirmed"))
    assert lines[at]["turn"] == 1 and lines[at + 1]["event"] == "model_call"  # vor dem ersten Aufruf des Host-Zugs
    submits = [i for i, k in enumerate(kinds) if k == ("tool", "submit_commentary")]
    assert len(submits) == 2 and all(lines[i + 1]["event"] == "commentary" for i in submits)

    calls = _of(lines, "model_call")
    assert calls and all(isinstance(c["messages"], int) and c["messages"] > 0 for c in calls)
    assert all(isinstance(c["latency_s"], float) for c in calls)
    assert [c["messages"] for c in calls] == sorted(c["messages"] for c in calls)  # der Verlauf wächst nur

    tools = _of(lines, "tool")
    first = tools[0]
    assert first["name"] == "resolve_company" and first["input"] == {"query": "NVDA"}
    assert first["result"]["status"] and first["duration_s"] >= 0 and first["tool_use_id"].startswith("toolu_")
    assert first["full_result"] is None and first["detail"] is None
    table = next(t for t in tools if t["name"] == "compute_comps_table")
    assert table["full_result"]["id"] == table["result"]["comps_table_id"]
    assert table["full_result"]["table"]["target"]  # die vollständige CompsTable, nicht nur die Fassung für das Modell
    assert [t["full_result"] for t in tools if t["name"] != "compute_comps_table"] == [None] * (len(tools) - 1)
    assert lines[-1]["model_calls"] == len(calls) and lines[-1]["last_outcome"] == "done"
    assert lines[-1]["session_cost_usd"] == pytest.approx(run.loop.session_cost)


def test_the_text_checks_carry_findings_and_the_action(tmp_path: Path) -> None:
    run = _run([say("Der Umsatz stieg um 25 Prozent."), say("Dazu liegen mir keine Zahlen vor."),
                use(("resolve_company", {"query": "NVDA"}), text="Ich suche 77 Firmen."), say("Fertig.")])
    with _writer(run, tmp_path) as tw:
        run.say("Frage")
        run.say("Mach Comps für NVDA")
    checks = _of(_lines(tw.path), "text_check")
    assert [(c["where"], c["action"]) for c in checks] == [
        ("end_turn", "correction"), ("end_turn", "shown"), ("beside_tool_use", "not_shown"), ("end_turn", "shown")]
    finding = checks[0]["findings"][0]
    assert finding["code"] == "NAKED_NUMBER" and finding["match"] and finding["end"] > finding["start"]
    assert checks[1]["findings"] == [] and checks[2]["findings"]


def test_a_second_failed_text_check_is_withheld_and_the_introduction_can_be_fixed(tmp_path: Path) -> None:
    run = _run([say("Das sind 25 Prozent."), say("Und 26 Prozent.")])
    with _writer(run, tmp_path) as tw:
        run.say("Frage")
    assert [c["action"] for c in _of(_lines(tw.path), "text_check")] == ["correction", "withheld"]

    run = _flow_to_proposal(tail=[])
    run.client._steps[3] = say("Das sind 25 Prozent.")
    with _writer(run, tmp_path) as tw:
        run.say("Mach Comps für NVDA")
    intro = [c for c in _of(_lines(tw.path), "text_check") if c["where"] == "intro"]
    assert [c["action"] for c in intro] == ["intro_fixed"] and intro[0]["findings"]


def test_run_mode_is_eval_when_the_context_has_an_eval_session(tmp_path: Path) -> None:
    run = _run([say("Hallo.")], eval_session=object())
    with _writer(run, tmp_path) as tw:
        run.say("Hallo")
    assert _lines(tw.path)[0]["run_mode"] == "eval"


# --- LT-33 vollständig ------------------------------------------------------------------------------------------


def test_lt_33_trace_has_prompt_version_both_hashes_and_both_gate_decisions_without_secrets(tmp_path: Path) -> None:
    prompt = load_system_prompt("de")
    paths = {}
    for decision in ("confirm", "reject"):
        run = _flow_to_proposal(tail=[say("Erledigt.")])
        run.loop.system_prompt = prompt.text  # der gesendete Text ist der echte Prompt
        with TraceWriter(run.loop, prompt, base_dir=tmp_path, session_id=f"s-{decision}") as tw:
            run.say("Mach Comps für NVDA")
            tw.flush()
            run.confirm() if decision == "confirm" else run.reject()
        paths[decision] = tw.path
        lines = _lines(tw.path)
        validate_trace_file(tw.path)
        header = lines[0]
        assert header["prompt_version"] == prompt.version
        assert header["prompt_sha256"] == hashlib.sha256(run.client.requests[0].system.encode("utf-8")).hexdigest()
        assert header["tools_sha256"] == tools_hash(tool_definitions())
        assert header["tools_sha256"] == tools_hash(run.client.requests[0].tools)  # die gesendete Liste
        assert header["session_id"] == f"s-{decision}"
        assert not any(secret in tw.path.read_text(encoding="utf-8") for secret in ALL_SECRETS)
    assert [g["decision"] for g in _of(_lines(paths["confirm"]), "gate")] == ["proposed", "confirmed"]
    rejected = _of(_lines(paths["reject"]), "gate")
    assert [g["decision"] for g in rejected] == ["proposed", "rejected"]
    assert rejected[1]["channel"] == "cli" and rejected[1]["rejected_by"] == "human"


def test_the_writer_refuses_a_prompt_that_is_not_the_text_the_loop_sends(tmp_path: Path) -> None:
    run = _run([say("Hallo.")])
    with pytest.raises(ValueError):
        TraceWriter(run.loop, _prompt("ein anderer Text"), base_dir=tmp_path)
    assert list(tmp_path.rglob("*.jsonl")) == []


# --- Whitelist des Kopfs ---------------------------------------------------------------------------------------


HEADER_KEYS = {"event", "seq", "ts", "run_id", "turn", "session_id", "run_mode", "git_commit", "model", "effort",
               "output_language", "prompt_version", "prompt_sha256", "tools_sha256", "params"}
PARAM_KEYS = {"max_tokens", "max_calls_per_turn", "max_tools_per_turn", "turn_seconds", "call_seconds",
              "stall_seconds", "cost_warning_usd", "cost_limit_usd", "context_limit_tokens", "price"}


def test_the_header_has_exactly_the_whitelisted_keys_and_the_model_rejects_extras(tmp_path: Path) -> None:
    run = _run([say("Hallo.")])
    with _writer(run, tmp_path, git_commit="a" * 40) as tw:
        pass
    header = _lines(tw.path)[0]
    assert set(header) == HEADER_KEYS and set(header["params"]) == PARAM_KEYS
    assert set(header["params"]["price"]) == {"input", "output", "cache_read", "cache_write"}
    assert header["git_commit"] == "a" * 40 and header["model"] == "claude-sonnet-5-5" and header["effort"] == "medium"
    assert header["output_language"] == "de" and header["seq"] == 0 and header["turn"] == 0
    assert header["params"]["cost_limit_usd"] == run.loop.config.cost_limit_usd
    with pytest.raises(ValueError):
        HeaderEvent.model_validate({**header, "settings": {"anthropic_api_key": "x"}})
    with pytest.raises(ValueError):
        TraceParams.model_validate({**header["params"], "anthropic_api_key": "x"})


def test_the_writer_takes_no_settings_and_the_header_has_no_contact_or_key(tmp_path: Path) -> None:
    run = _run([say("Hallo.")])
    with _writer(run, tmp_path) as tw:
        run.say("Hallo")
    text = tw.path.read_text(encoding="utf-8")
    assert not any(secret in text for secret in ALL_SECRETS)
    import inspect

    assert "settings" not in " ".join(inspect.signature(TraceWriter.__init__).parameters)


# --- Schlussereignis ---------------------------------------------------------------------------------------------


def test_end_is_the_last_line_after_a_failed_turn(tmp_path: Path) -> None:
    run = _run([use(("resolve_company", {"query": "A1"})), use(("resolve_company", {"query": "A2"})), say("nie")],
               config=LoopConfig(max_calls_per_turn=2))
    with _writer(run, tmp_path) as tw:
        result = run.say("Suche")
    assert result.outcome == "failed"
    lines = _lines(tw.path)
    validate_trace_file(tw.path)
    assert lines[-1]["event"] == "end" and lines[-1]["last_outcome"] == "failed"
    assert lines[-1]["last_reason"] == "limit_calls" and lines[-1]["reason"] is None and lines[-1]["error_class"] is None
    assert lines[-1]["model_calls"] == 2


def test_end_is_the_last_line_after_a_closed_session_with_the_total_cost(tmp_path: Path) -> None:
    big = Usage(input_tokens=1000, output_tokens=55_000)
    q = lambda i: ("resolve_company", {"query": f"Quuxcorp{i}"})  # noqa: E731
    run = _run([use(q(1), usage=big), use(q(2), usage=big), use(q(3), usage=big), use(q(4), usage=big), say("nie")])
    with _writer(run, tmp_path) as tw:
        result = run.say("Suche")
    assert result.outcome == "closed" and run.loop.state is LoopState.CLOSED
    lines = _lines(tw.path)
    end = lines[-1]
    assert end["event"] == "end" and end["state"] == "closed" and end["last_outcome"] == "closed"
    assert end["last_reason"] == "cost" and end["session_cost_usd"] >= 2.0 and end["model_calls"] == 4
    assert _of(lines, "turn_end")[-1]["closing"] == "cost"
    validate_trace_file(tw.path)


def test_end_is_written_with_the_exception_class_when_the_host_raises_inside_the_with_block(tmp_path: Path) -> None:
    run = _run([say("Antwort.")])
    holder: dict[str, TraceWriter] = {}
    with pytest.raises(RuntimeError, match="Host kaputt"):
        with _writer(run, tmp_path) as tw:
            holder["tw"] = tw
            run.say("Frage")
            raise RuntimeError("Host kaputt")
    lines = _lines(holder["tw"].path)
    validate_trace_file(holder["tw"].path)
    assert lines[-1]["event"] == "end" and lines[-1]["reason"] == "host_exception"
    assert lines[-1]["error_class"] == "RuntimeError" and "Host kaputt" not in json.dumps(lines[-1])
    assert _of(lines, "turn_end")  # was bis zur Ausnahme geschah, ist geflusht


def test_close_is_idempotent_and_nothing_is_written_after_it(tmp_path: Path) -> None:
    run = _run([say("Antwort.")])
    tw = _writer(run, tmp_path)
    run.say("Frage")
    tw.close()
    tw.close(RuntimeError("zu spät"))
    size = tw.path.stat().st_size
    tw.flush()
    assert tw.path.stat().st_size == size
    assert len(_of(_lines(tw.path), "end")) == 1


# --- M2 im Trace ---------------------------------------------------------------------------------------------------


def test_m2_a_failed_call_with_usage_is_a_model_error_with_cost_and_counts_in_the_end_event(tmp_path: Path) -> None:
    usage = Usage(2000, 8000, 500, 100)
    run = _run([ModelCallError("stalled", "ReadTimeout", None, "req_x", usage=usage, usage_estimated=True)])
    with _writer(run, tmp_path) as tw:
        run.say("Frage")
    lines = _lines(tw.path)
    validate_trace_file(tw.path)
    (error,) = _of(lines, "model_error")
    price = run.loop.config.price
    assert error["cost_usd"] > 0 and error["cost_usd"] == pytest.approx(cost_usd(usage, price))
    assert error["usage_estimated"] is True and error["input_tokens"] == 2000 and error["request_id"] == "req_x"
    assert lines[-1]["session_cost_usd"] == pytest.approx(error["cost_usd"]) and lines[-1]["model_calls"] == 1
    assert _of(lines, "turn_end")[0]["closing"] is None


# --- T12 für den Trace ---------------------------------------------------------------------------------------------


class _LeakyMarket:
    def get_price(self, ticker):
        raise RuntimeError(f"Anbieter kaputt: https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_KEY} "
                           f"Header User-Agent: {SECRETS[0]}")


def test_t12_no_secret_reaches_the_trace_file_not_even_through_tool_input_or_exceptions(tmp_path: Path) -> None:
    w = standard_world()
    w.market = _LeakyMarket()
    steps = [
        use(("resolve_company", {"query": f"NVDA {FAKE_ANTHROPIC_KEY}"}), ("resolve_company", {"query": "NVDA"})),
        use(("get_market_data", {"ticker": "NVDA"})),
        use(("get_market_data", {"ticker": f"NVDA {FAKE_ANTHROPIC_KEY} welt.tester@example.invalid"})),
        say("Fertig."),
    ]
    run = Run(w, steps, now=lambda: FIXED_NOW, secrets_to_redact=[*SECRETS, FAKE_ANTHROPIC_KEY])
    with _writer(run, tmp_path) as tw:
        run.say("Mach Comps für NVDA")
    text = tw.path.read_text(encoding="utf-8")
    lines = validate_trace_file(tw.path)
    assert any(x.event == "tool" and x.code == "INTERNAL_ERROR" for x in lines)  # die unerwartete Ausnahme trat auf
    assert "<redacted>" in text and "Anbieter kaputt" in text
    for secret in ALL_SECRETS:
        assert secret not in text, secret
    assert "token=" not in text.replace("token=<redacted>", "")


def test_t12_an_unexpected_exception_in_the_loop_itself_is_redacted_in_the_trace(tmp_path: Path) -> None:
    def broken(ctx, name, args):
        raise RuntimeError(f"kaputt {FAKE_ANTHROPIC_KEY} {SECRETS[1]}")

    run = Run(standard_world(), [use(("resolve_company", {"query": "NVDA"}))], dispatch=broken, now=lambda: FIXED_NOW,
              secrets_to_redact=[*SECRETS, FAKE_ANTHROPIC_KEY])
    with _writer(run, tmp_path) as tw:
        run.say("Frage")
    text = tw.path.read_text(encoding="utf-8")
    (error,) = _of(_lines(tw.path), "loop_error")
    assert error["error_class"] == "RuntimeError" and "kaputt" in error["traceback"]
    assert not any(secret in text for secret in ALL_SECRETS)


def test_t12_the_final_check_raises_loudly_when_the_recursive_redaction_is_bypassed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run([use(("resolve_company", {"query": f"NVDA {FAKE_ANTHROPIC_KEY}"})), say("Fertig.")],
               secrets_to_redact=[*SECRETS, FAKE_ANTHROPIC_KEY])
    tw = _writer(run, tmp_path)
    monkeypatch.setattr(trace, "_redact_tree", lambda value, redact: value)
    run.say("Frage")
    with pytest.raises(TraceLeakError):
        tw.flush()
    assert FAKE_ANTHROPIC_KEY not in tw.path.read_text(encoding="utf-8")  # die Zeile wurde nicht geschrieben


def test_t12_a_secret_that_only_arises_from_serialization_is_caught_by_the_final_check(tmp_path: Path) -> None:
    secret = "7654321"  # steht als Zahl im Ereignis, nicht als String: die rekursive Schwärzung sieht ihn nicht
    run = _run([say("Antwort.", usage=Usage(int(secret), 10))], secrets_to_redact=[*SECRETS, secret])
    tw = _writer(run, tmp_path)
    run.say("Frage")
    with pytest.raises(TraceLeakError):
        tw.flush()
    assert secret not in tw.path.read_text(encoding="utf-8")


def test_multiline_redacted_text_is_not_mistaken_for_a_leak(tmp_path: Path) -> None:
    def broken(ctx, name, args):
        raise RuntimeError(f"Header token={FINNHUB_KEY}\n  File x.py, line 3\nboom")

    run = Run(standard_world(), [use(("resolve_company", {"query": "NVDA"}))], dispatch=broken, now=lambda: FIXED_NOW)
    with _writer(run, tmp_path) as tw:
        run.say("Frage")
    (error,) = _of(_lines(tw.path), "loop_error")
    assert "token=<redacted>" in error["traceback"] and FINNHUB_KEY not in error["traceback"]


# --- Schema -------------------------------------------------------------------------------------------------------


def _line(**kw: Any) -> str:
    base = {"event": "turn_end", "seq": 3, "ts": FIXED_NOW.isoformat(), "run_id": "r", "turn": 1, "outcome": "done",
            "reason": None, "state": "idle", "calls": 1, "tools": 0, "closing": None, "session_cost_usd": 0.1}
    return json.dumps({**base, **kw})


def test_schema_accepts_a_valid_line_and_rejects_unknown_events_missing_fields_and_extras() -> None:
    assert validate_trace_line(_line()).event == "turn_end"
    with pytest.raises(ValueError):
        validate_trace_line(_line(event="erfunden"))
    missing = json.loads(_line())
    del missing["outcome"]
    with pytest.raises(ValueError):
        validate_trace_line(json.dumps(missing))
    with pytest.raises(ValueError):
        validate_trace_line(_line(settings={"k": 1}))
    with pytest.raises(ValueError):
        validate_trace_line(_line(ts="gestern"))
    with pytest.raises(ValueError):
        validate_trace_line("{kein json")


def test_validate_trace_file_names_the_line_of_the_first_invalid_entry(tmp_path: Path) -> None:
    run = _run([say("Hallo.")])
    with _writer(run, tmp_path) as tw:
        run.say("Hallo")
    lines = tw.path.read_text(encoding="utf-8").splitlines()
    lines[2] = json.dumps({"event": "nope"})
    broken = tmp_path / "broken.jsonl"
    broken.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"Zeile 3"):
        validate_trace_file(broken)


def test_an_invalid_event_raises_value_error_and_only_a_content_free_trace_error_is_written(tmp_path: Path) -> None:
    run = _run([say("Hallo.")])
    tw = _writer(run, tmp_path)
    before = tw.path.read_text(encoding="utf-8")
    run.loop.events.append({"event": "erfunden", "x": 1})
    with pytest.raises(ValueError):
        tw.flush()
    added = tw.path.read_text(encoding="utf-8")[len(before):]
    assert "erfunden" not in added and json.loads(added)["event"] == "trace_error"
    assert json.loads(added)["source_event"] is None  # unbekannte Ereignisart: nicht übernommen


# --- Datei, Pfad, Hilfen --------------------------------------------------------------------------------------------


def test_the_path_is_date_dir_and_run_id_the_file_is_never_overwritten(tmp_path: Path) -> None:
    run = _run([say("Hallo.")])
    with _writer(run, tmp_path) as tw:
        pass
    assert tw.path.parent == tmp_path / "2026-10-28" and tw.path.suffix == ".jsonl"
    assert re.fullmatch(r"20261028-123045-[0-9a-f]{12}", tw.path.stem) and tw.run_id == tw.path.stem
    again = _writer(run, tmp_path, run_id="fester-lauf")
    again.close()
    with pytest.raises(FileExistsError):
        _writer(run, tmp_path, run_id="fester-lauf")
    with pytest.raises(ValueError):
        _writer(run, tmp_path, run_id="../aus-dem-ordner")
    assert _lines(tw.path)[0]["ts"] == "2026-10-28T12:30:45+00:00"


def test_current_git_commit_returns_the_head_or_none(tmp_path: Path) -> None:
    head = current_git_commit(Path(__file__).resolve().parent)
    assert head is None or re.fullmatch(r"[0-9a-f]{40}", head)
    assert current_git_commit(tmp_path) is None  # kein Repository


# --- Konfiguration ----------------------------------------------------------------------------------------------------


def test_settings_runs_dir_defaults_to_runs_and_can_be_moved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUNS_DIR", raising=False)
    assert Settings(_env_file=None, edgar_contact_email="t@example.org").runs_dir == Path("runs")
    monkeypatch.setenv("RUNS_DIR", "/scout-runs-test")
    assert Settings(_env_file=None, edgar_contact_email="t@example.org").runs_dir == Path("/scout-runs-test")
    monkeypatch.setenv("RUNS_DIR", "")
    assert Settings(_env_file=None, edgar_contact_email="t@example.org").runs_dir == Path("runs")


# --- Runde 2, H1: ein fehlerhaftes Ereignis blockiert den Schreiber nicht ------------------------------------------


def _leaky_run(**kw: Any) -> Run:
    """Der erste Modellaufruf trägt `LEAK` als Token-Zahl; danach läuft alles bis zum angenommenen Kommentar."""
    run = Run(standard_world(), [], now=lambda: FIXED_NOW, secrets_to_redact=[*SECRETS, LEAK], **kw)
    run.client._steps = [
        use(("resolve_company", {"query": "NVDA"}), usage=Usage(int(LEAK), 10)),
        use(("find_peer_candidates", {"target_cik": NVDA_CIK})),
        run.propose(),
        say("Hier ist mein Vorschlag."),
        run.compute(),
        run.submit(COMMENTARY),
    ]
    return run


def test_h1_a_dropped_event_becomes_a_content_free_trace_error_line_and_later_events_are_written(tmp_path: Path) -> None:
    run = _leaky_run()
    tw = _writer(run, tmp_path)
    run.say("Mach Comps für NVDA")
    with pytest.raises(TraceLeakError):  # laut, aber erst nach allem, was sich schreiben ließ
        tw.flush()
    run.confirm(keep=(AAPL_CIK,))
    tw.flush()  # die Quelle ist weitergezählt: kein erneuter Fehler
    tw.close()
    text = tw.path.read_text(encoding="utf-8")
    lines = _lines(tw.path)
    validate_trace_file(tw.path)
    (error,) = _of(lines, "trace_error")
    assert error["source"] == "loop" and error["source_event"] == "model_call" and error["index"] == 0
    assert error["error_class"] == "TraceLeakError" and LEAK not in text
    assert set(error) == {"event", "seq", "ts", "run_id", "turn", "source", "source_event", "error_class", "index"}
    assert [g["decision"] for g in _of(lines, "gate")] == ["proposed", "confirmed"]
    assert _of(lines, "commentary") and _of(lines, "turn_end")
    assert len(_of(lines, "model_call")) == 5  # sechs Aufrufe; der fehlerhafte ist durch `trace_error` ersetzt
    assert lines[-1]["event"] == "end" and lines[-1]["dropped_events"] == 1 and lines[-1]["reason"] == "trace_error"


def test_h1_a_secret_in_a_non_string_key_is_dropped_too_and_the_writer_keeps_going(tmp_path: Path) -> None:
    run = _run([say("Antwort."), say("Gern.")], secrets_to_redact=[*SECRETS, LEAK])
    tw = _writer(run, tmp_path)
    run.say("Frage")
    run.loop.events.append({"event": "tool", "name": "resolve_company", "is_error": False, "code": None,
                            "tool_use_id": "toolu_x", "input": {int(LEAK): "x"}, "result": {}, "detail": None,
                            "duration_s": 0.0})
    with pytest.raises(TraceLeakError):
        tw.flush()
    run.say("Noch eine Frage")
    tw.flush()
    tw.close()
    lines = _lines(tw.path)
    (error,) = _of(lines, "trace_error")
    assert error["source_event"] == "tool" and LEAK not in tw.path.read_text(encoding="utf-8")
    assert len(_of(lines, "turn_end")) == 2 and lines[-1]["dropped_events"] == 1


def test_h1_a_dropped_gate_and_commentary_entries_are_counted_per_source(tmp_path: Path) -> None:
    run = _leaky_run()
    run.client._steps = run.client._steps[1:]  # ohne den fehlerhaften Aufruf: der Fehler kommt aus dem Gate-Protokoll
    run.client._steps.insert(0, use(("resolve_company", {"query": "NVDA"})))
    tw = _writer(run, tmp_path)
    run.say("Mach Comps für NVDA")
    run.ctx.gate_log.append({"event": "erfunden", "proposal_id": "pp_x"})
    run.ctx.commentary_log.append({"event": "commentary", "comps_table_id": "ct_x", "attempt": 1, "raw_text": 5,
                                   "slots": [], "problems": [], "format_chars": 0, "action": "rejected"})
    with pytest.raises(TraceSchemaError):
        tw.flush()
    tw.close()
    errors = _of(_lines(tw.path), "trace_error")
    assert [(e["source"], e["source_event"], e["error_class"]) for e in errors] == [
        ("gate", None, "TraceSchemaError"), ("commentary", "commentary", "TraceSchemaError")]
    assert errors[0]["index"] == 1 and errors[1]["index"] == 0  # Index in der jeweiligen Quelle


def test_h1_a_host_exception_wins_over_a_dropped_event_and_end_says_host_exception(tmp_path: Path) -> None:
    run = _leaky_run()
    holder: dict[str, TraceWriter] = {}
    with pytest.raises(RuntimeError, match="Host kaputt"):
        with _writer(run, tmp_path) as tw:
            holder["tw"] = tw
            run.say("Mach Comps für NVDA")
            raise RuntimeError("Host kaputt")  # `close` darf diese Ausnahme nicht durch die eigene ersetzen
    lines = _lines(holder["tw"].path)
    validate_trace_file(holder["tw"].path)
    assert lines[-1]["event"] == "end" and lines[-1]["reason"] == "host_exception"
    assert lines[-1]["error_class"] == "RuntimeError" and lines[-1]["dropped_events"] == 1
    assert _of(lines, "trace_error") and _of(lines, "turn_end")


def test_h1_close_without_host_exception_writes_end_first_and_then_raises_the_collected_error(tmp_path: Path) -> None:
    run = _leaky_run()
    tw = _writer(run, tmp_path)
    run.say("Mach Comps für NVDA")
    with pytest.raises(TraceLeakError):
        tw.close()
    lines = _lines(tw.path)  # M4 (a): `flush` scheitert beim Schließen, `end` steht trotzdem
    assert lines[-1]["event"] == "end" and lines[-1]["reason"] == "trace_error" and lines[-1]["dropped_events"] == 1
    assert lines[-1]["error_class"] == "TraceLeakError"
    tw.close()  # idempotent, wirft nicht erneut
    other = _leaky_run()
    with pytest.raises(TraceLeakError):  # auch über den Kontextmanager ohne Host-Ausnahme
        with _writer(other, tmp_path) as tw2:
            other.say("Mach Comps für NVDA")
    assert _lines(tw2.path)[-1]["event"] == "end"


def test_h1_validate_trace_file_requires_exactly_one_end_as_the_last_line(tmp_path: Path) -> None:
    run = _run([say("Hallo.")])
    with _writer(run, tmp_path) as tw:
        run.say("Hallo")
    lines = tw.path.read_text(encoding="utf-8").splitlines()
    assert lines[-1].startswith('{"event": "end"')
    cut = tmp_path / "cut.jsonl"
    cut.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(TraceSchemaError, match="end"):
        validate_trace_file(cut)
    twice = tmp_path / "twice.jsonl"
    end = json.loads(lines[-1])
    twice.write_text("\n".join([*lines, json.dumps({**end, "seq": end["seq"] + 1})]) + "\n", encoding="utf-8")
    with pytest.raises(TraceSchemaError, match="Zeile"):
        validate_trace_file(twice)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(TraceSchemaError):
        validate_trace_file(empty)


# --- M3: rohe Loop-Ereignisse gegen das Schema ---------------------------------------------------------------------


def test_m3_validate_loop_event_accepts_real_events_skips_gate_and_rejects_bad_ones() -> None:
    run = _run([use(("resolve_company", {"query": "NVDA"})), say("Fertig.")])
    run.say("Mach Comps für NVDA")
    kinds = {e["event"] for e in run.loop.events}
    assert {"model_call", "tool", "text_check", "turn_end"} <= kinds
    for event in run.loop.events:
        assert validate_loop_event(event) is None
    validate_loop_event({"event": "gate", "decision": "confirmed"})  # Positionsmarker: übersprungen
    good = next(e for e in run.loop.events if e["event"] == "turn_end")
    with pytest.raises(TraceSchemaError):
        validate_loop_event({**good, "extra": 1})
    with pytest.raises(TraceSchemaError):
        validate_loop_event({"event": "header"})
    with pytest.raises(TraceSchemaError):
        validate_loop_event({k: v for k, v in good.items() if k != "outcome"})


# --- M4 und 12.10 -----------------------------------------------------------------------------------------------------


def test_m4_b_a_secret_in_a_key_of_a_tool_input_is_redacted_never_written(tmp_path: Path) -> None:
    run = Run(standard_world(), [use(("resolve_company", {FAKE_ANTHROPIC_KEY: "NVDA", "query": "NVDA"})), say("Fertig.")],
              now=lambda: FIXED_NOW, secrets_to_redact=[*SECRETS, FAKE_ANTHROPIC_KEY])
    with _writer(run, tmp_path) as tw:
        run.say("Mach Comps für NVDA")
    text = tw.path.read_text(encoding="utf-8")
    assert FAKE_ANTHROPIC_KEY not in text and "<redacted>" in text
    tool = _of(_lines(tw.path), "tool")[0]
    assert "<redacted>" in tool["input"] and tool["input"]["query"] == "NVDA"
    validate_trace_file(tw.path)


def test_m4_c_get_financials_carries_the_stored_object_with_provenance(tmp_path: Path) -> None:
    run = _run([use(("resolve_company", {"query": "NVDA"})),
                use(("get_financials", {"cik": NVDA_CIK, "metrics": ["revenue"]})), say("Fertig.")])
    with _writer(run, tmp_path) as tw:
        run.say("Umsatz von NVDA?")
    tool = next(t for t in _of(_lines(tw.path), "tool") if t["name"] == "get_financials")
    stored = run.ctx.financials[tool["result"]["financials_id"]]
    assert tool["full_result"]["id"] == stored.id and tool["full_result"]["cik"] == NVDA_CIK
    assert tool["full_result"]["payload"] == json.loads(json.dumps(stored.payload, default=str))
    assert "source" in json.dumps(tool["full_result"]["payload"]).lower() or "accession" in json.dumps(
        tool["full_result"]["payload"]).lower()  # Provenance (Fundstelle) ist im gespeicherten Objekt


def test_m4_d_a_commentary_accepted_on_the_first_try_and_a_withheld_one_are_logged_with_rendered(tmp_path: Path) -> None:
    run = _flow_to_proposal()
    run.client._steps += [run.compute(), run.submit(COMMENTARY)]
    with _writer(run, tmp_path) as tw:
        run.say("Mach Comps für NVDA")
        tw.flush()
        run.confirm()
    (accepted,) = _of(_lines(tw.path), "commentary")
    assert accepted["action"] == "accepted" and accepted["attempt"] == 1
    assert accepted["rendered"] == run.ctx.commentaries[run.table_id].rendered

    run = _flow_to_proposal()
    run.client._steps += [run.compute(), *[run.submit(t) for t in BAD_THREE]]
    with _writer(run, tmp_path) as tw:
        run.say("Mach Comps für NVDA")
        tw.flush()
        run.confirm()
    entries = _of(_lines(tw.path), "commentary")
    assert [c["action"] for c in entries] == ["rejected", "rejected", "withheld"]
    stored = run.ctx.commentaries[run.table_id]
    assert stored.status == "withheld" and entries[-1]["rendered"] == stored.rendered
    assert entries[-1]["rendered"].startswith("Kommentar zurückgehalten")
    assert entries[-1]["warnings_addressed"] == [] and entries[0]["rendered"] is None


def test_12_10_query_strings_of_urls_never_reach_the_trace(tmp_path: Path) -> None:
    def broken(ctx, name, args):
        raise RuntimeError(f"Anbieter kaputt: https://finnhub.io/api/v1/quote?symbol=NVDA&token={FINNHUB_KEY} "
                           "und http://x.example/pfad?a=1&b=2#frag danach")

    run = Run(standard_world(), [use(("resolve_company", {"query": "NVDA"}))], dispatch=broken, now=lambda: FIXED_NOW)
    with _writer(run, tmp_path) as tw:
        run.say("Frage")
    text = tw.path.read_text(encoding="utf-8")
    (error,) = _of(_lines(tw.path), "loop_error")
    assert "symbol=" not in text and "token=" not in text and "a=1" not in text and FINNHUB_KEY not in text
    assert "https://finnhub.io/api/v1/quote?<query>" in error["traceback"] and "danach" in error["traceback"]
    assert "#frag" in error["traceback"]  # nur die Query entfällt


# --- N3 -----------------------------------------------------------------------------------------------------------------


def test_n3_git_commit_must_be_a_full_hash_and_ts_must_carry_a_time_zone(tmp_path: Path) -> None:
    run = _run([say("Hallo.")])
    with _writer(run, tmp_path, git_commit="a" * 40) as tw:
        pass
    header = _lines(tw.path)[0]
    for bad in ("main", "abc123", "A" * 40, "a" * 41):
        with pytest.raises(ValueError):
            HeaderEvent.model_validate({**header, "git_commit": bad})
    assert HeaderEvent.model_validate({**header, "git_commit": "b" * 64}).git_commit == "b" * 64
    assert HeaderEvent.model_validate({**header, "git_commit": None}).git_commit is None
    with pytest.raises(ValueError):
        validate_trace_line(_line(ts="2026-10-28T12:30:45"))
    with pytest.raises(ValueError):
        _writer(run, tmp_path, git_commit="nicht-ein-hash")


# --- Runde 3 ------------------------------------------------------------------------------------------------------------


def test_r3_an_interrupt_inside_emit_is_counted_as_dropped_and_propagates_without_a_trace_error(tmp_path: Path) -> None:
    run = _run([say("Antwort.")])
    tw = _writer(run, tmp_path)
    run.say("Frage")
    real = tw._emit

    def interrupted(raw: dict[str, Any]) -> None:
        if raw["event"] == "turn_end":
            tw._emit = real  # type: ignore[method-assign]
            raise KeyboardInterrupt
        real(raw)

    tw._emit = interrupted  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        tw.flush()
    tw.close(KeyboardInterrupt())
    lines = _lines(tw.path)
    assert _of(lines, "trace_error") == []  # keine neue Schreibarbeit während des Abbruchs
    assert lines[-1]["event"] == "end" and lines[-1]["dropped_events"] == 1
    assert lines[-1]["reason"] == "host_exception" and lines[-1]["error_class"] == "KeyboardInterrupt"


def test_r3_an_interrupt_during_the_flush_inside_close_still_ends_the_file_with_end(tmp_path: Path) -> None:
    run = _run([say("Antwort.")])
    tw = _writer(run, tmp_path)
    run.say("Frage")

    def interrupted() -> None:
        raise KeyboardInterrupt

    tw.flush = interrupted  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        tw.close()
    lines = _lines(tw.path)
    assert lines[-1]["event"] == "end" and lines[-1]["reason"] == "host_exception"
    assert lines[-1]["error_class"] == "KeyboardInterrupt" and lines[-1]["dropped_events"] == 0
    tw.close()  # bleibt geschlossen und idempotent
    assert len(_of(_lines(tw.path), "end")) == 1


def test_r3_an_invalid_git_commit_is_refused_before_any_file_or_directory_is_created(tmp_path: Path) -> None:
    run = _run([say("Hallo.")])
    target = tmp_path / "runs"
    for bad in ("nicht-ein-hash", "A" * 40, "abc"):
        with pytest.raises(ValueError):
            TraceWriter(run.loop, _prompt(run.loop.system_prompt), base_dir=target, git_commit=bad)
    assert not target.exists()
