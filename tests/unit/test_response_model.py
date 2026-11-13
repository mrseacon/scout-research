"""TA-20.6 Teil 4 (Fable 04, 2.2 Punkt 5): Das von der API gemeldete Modell steht in `ModelResponse.model` und im Ereignis
`model_call` des Traces (das konfigurierte steht im Kopf, SG-19). Kein Netz: SDK-Doubles und Skript."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scout_research.agent.model_client import AnthropicAdapter, ManualClock, ModelResponse, ScriptedModelClient
from scout_research.agent.trace import TraceSchemaError, TraceWriter, validate_trace_line
from tests.unit.loop_harness import Run, say
from tests.unit.test_model_client import A, T, U, _Block, _Final, _request, _Sdk, _Stream
from tests.unit.test_trace import _lines, _of, _prompt
from tests.unit.tool_world import FINNHUB_KEY, standard_world

FIXED_NOW = datetime(2026, 10, 28, 12, 30, 45, tzinfo=timezone.utc)
REPORTED = "claude-sonnet-5-5-gemeldet"


class _StartEvent:
    type = "message_start"

    def __init__(self, model: str | None) -> None:
        self.message = SimpleNamespace(usage={"input_tokens": 1, "output_tokens": 0}, model=model)


class _StartStream(_Stream):
    """Wie `_Stream`, aber `message_start` trägt das Modell."""

    def __init__(self, start_model: str | None, final) -> None:
        super().__init__([], final)
        self._start_model = start_model

    def __iter__(self):
        yield _StartEvent(self._start_model)


def _final(model: str | None = None) -> _Final:
    final = _Final([_Block({"type": "text", "text": "ok"})])
    if model is not None:
        final.model = model
    return final


def test_the_adapter_takes_the_model_from_the_final_message() -> None:
    sdk = _Sdk(_StartStream("claude-vom-start", _final(REPORTED)))
    response = AnthropicAdapter(sdk, clock=ManualClock()).create(_request(), lambda k: None)
    assert response.model == REPORTED


def test_the_adapter_falls_back_to_message_start_and_then_to_none() -> None:
    sdk = _Sdk(_StartStream("claude-vom-start", _final()))
    assert AnthropicAdapter(sdk, clock=ManualClock()).create(_request(), lambda k: None).model == "claude-vom-start"
    sdk = _Sdk(_StartStream(None, _final()))
    assert AnthropicAdapter(sdk, clock=ManualClock()).create(_request(), lambda k: None).model is None


def test_the_adapter_ignores_a_model_that_is_not_a_string() -> None:
    final = _final()
    final.model = 42
    response = AnthropicAdapter(_Sdk(_StartStream(None, final)), clock=ManualClock()).create(_request(), lambda k: None)
    assert response.model is None


def test_the_scripted_client_reports_the_requested_model_unless_the_script_names_one() -> None:
    plain = ModelResponse([{"type": "text", "text": "a"}], "end_turn")
    named = ModelResponse([{"type": "text", "text": "b"}], "end_turn", model=REPORTED)
    client = ScriptedModelClient([plain, named])
    request = _request(messages=[U(T("x"))])
    assert client.create(request, lambda k: None).model == "claude-sonnet-5-5"
    follow = _request(messages=[U(T("x")), A(T("a")), U(T("y"))])
    assert client.create(follow, lambda k: None).model == REPORTED


def test_the_trace_writes_the_reported_model_into_the_model_call_event(tmp_path: Path) -> None:
    run = Run(standard_world(), [], now=lambda: FIXED_NOW)
    run.client._steps = [ModelResponse([{"type": "text", "text": "Hallo."}], "end_turn", model=REPORTED), say("Und noch einmal.")]
    with TraceWriter(run.loop, _prompt(run.loop.system_prompt), base_dir=tmp_path) as tw:
        run.say("Hallo")
        run.say("Nochmal")
        tw.flush()
    lines = _lines(tw.path)
    calls = _of(lines, "model_call")
    assert [c["model"] for c in calls] == [REPORTED, run.loop.config.model]  # gemeldet bzw. vom Skript-Client angefragt
    assert _of(lines, "header")[0]["model"] == run.loop.config.model  # der Kopf (SG-19) bleibt das konfigurierte Modell


def test_the_schema_accepts_null_and_older_lines_without_the_field_and_rejects_a_wrong_type(tmp_path: Path) -> None:
    run = Run(standard_world(), [say("Hallo.")], now=lambda: FIXED_NOW)
    with TraceWriter(run.loop, _prompt(run.loop.system_prompt), base_dir=tmp_path) as tw:
        run.say("Hallo")
        tw.flush()
    call = _of(_lines(tw.path), "model_call")[0]
    assert validate_trace_line(json.dumps({**call, "model": None})).model is None
    without = {k: v for k, v in call.items() if k != "model"}
    assert validate_trace_line(json.dumps(without)).model is None
    with pytest.raises(TraceSchemaError):
        validate_trace_line(json.dumps({**call, "model": 42}))
    with pytest.raises(TraceSchemaError):
        validate_trace_line(json.dumps({**call, "modell": REPORTED}))


def test_the_reported_model_goes_through_the_redaction_like_every_string(tmp_path: Path) -> None:
    run = Run(standard_world(), [ModelResponse([{"type": "text", "text": "Hallo."}], "end_turn", model=f"m {FINNHUB_KEY}")],
              now=lambda: FIXED_NOW)
    with TraceWriter(run.loop, _prompt(run.loop.system_prompt), base_dir=tmp_path) as tw:
        run.say("Hallo")
        tw.flush()
    text = tw.path.read_text(encoding="utf-8")
    assert FINNHUB_KEY not in text and _of(_lines(tw.path), "model_call")[0]["model"] == "m <redacted>"


@pytest.mark.parametrize("bad", ["x" * 101, "claude <b>evil</b>", "claude sonnet", "mod\nel", "モデル", ""])
def test_the_adapter_drops_a_reported_model_of_another_shape(bad: str) -> None:
    final = _final(bad)
    response = AnthropicAdapter(_Sdk(_StartStream(None, final)), clock=ManualClock()).create(_request(), lambda k: None)
    assert response.model is None


def test_the_adapter_keeps_a_model_name_of_the_allowed_shape() -> None:
    name = "claude-sonnet-5-5_2026.01:x"
    response = AnthropicAdapter(_Sdk(_StartStream(None, _final(name))), clock=ManualClock()).create(_request(), lambda k: None)
    assert response.model == name


def test_the_schema_limits_the_length_of_the_model(tmp_path: Path) -> None:
    run = Run(standard_world(), [say("Hallo.")], now=lambda: FIXED_NOW)
    with TraceWriter(run.loop, _prompt(run.loop.system_prompt), base_dir=tmp_path) as tw:
        run.say("Hallo")
        tw.flush()
    call = _of(_lines(tw.path), "model_call")[0]
    assert validate_trace_line(json.dumps({**call, "model": "m" * 100})).model == "m" * 100
    with pytest.raises(TraceSchemaError):
        validate_trace_line(json.dumps({**call, "model": "m" * 101}))
