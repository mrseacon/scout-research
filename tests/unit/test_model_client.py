"""Modell-Client: Platzierungsprüfer (U-P1, SM-04), SDK-Adapter gegen Fake-Stream (LT-34, V3), Fristen, Ausnahmen
(LT-44, SM-09). Kein SDK, kein Netzwerk."""

from __future__ import annotations

import json
from typing import Any

import pytest

from scout_research.agent.budgets import Usage
from scout_research.agent.model_client import (
    AnthropicAdapter,
    ManualClock,
    ModelCallError,
    ModelRequest,
    PlacementError,
    build_api_kwargs,
    check_placement,
    map_sdk_exception,
)
from scout_research.tools.handlers import tool_definitions

T = lambda s: {"type": "text", "text": s}  # noqa: E731
U = lambda *b: {"role": "user", "content": list(b)}  # noqa: E731
A = lambda *b: {"role": "assistant", "content": list(b)}  # noqa: E731
S = lambda s: {"role": "system", "content": [T(s)]}  # noqa: E731
USE = lambda i: {"type": "tool_use", "id": i, "name": "resolve_company", "input": {"query": "x"}}  # noqa: E731
RES = lambda i: {"type": "tool_result", "tool_use_id": i, "content": "{}"}  # noqa: E731
THINK = {"type": "thinking", "thinking": "überlege", "signature": "sig=="}


# --- U-P1: je Regel ein abgewiesener und ein bestandener Fall --------------------------------------------------


@pytest.mark.parametrize(
    ("name", "messages"),
    [
        ("system nach assistant", [U(T("a")), A(T("b")), S("fakt")]),
        ("system nach tool_result als letztem Block", [U(T("a")), A(USE("t1")), U(RES("t1")), S("fakt")]),
        ("system nicht letzter Eintrag, danach user", [U(T("a")), S("fakt"), U(T("b"))]),
        ("system zuerst", [S("fakt"), U(T("a"))]),
        ("tool_use ohne Ergebnis", [U(T("a")), A(USE("t1")), U(T("b"))]),
        ("tool_use ohne folgenden user-Turn", [U(T("a")), A(USE("t1"))]),
        ("tool_result ohne tool_use", [U(T("a")), A(T("b")), U(RES("t9"), T("c"))]),
        ("zwei user-Turns hintereinander", [U(T("a")), U(T("b"))]),
        ("zwei assistant-Turns hintereinander", [U(T("a")), A(T("b")), A(T("c")), U(T("d"))]),
        ("letzter Eintrag assistant", [U(T("a")), A(T("b"))]),
        ("erster Eintrag assistant", [A(T("b")), U(T("a"))]),
        ("leerer Verlauf", []),
        ("tool_result nach text", [U(T("a")), A(USE("t1")), U(T("b"), RES("t1"))]),
        ("assistant nur mit Leerraum", [U(T("a")), A(T(" \n")), U(T("b"))]),
        ("assistant nur mit Thinking", [U(T("a")), A(THINK), U(T("b"))]),
        ("Leerraum-Block neben tool_use", [U(T("a")), A(T("\n\n"), USE("t1")), U(RES("t1"))]),
    ],
)
def test_u_p1_placement_violations_are_rejected(name: str, messages: list) -> None:
    with pytest.raises(PlacementError):
        check_placement(messages)


@pytest.mark.parametrize(
    "messages",
    [
        [U(T("a"))],
        [U(T("a")), S("fakt")],
        [U(T("a")), S("fakt"), A(T("b")), U(T("c"))],  # system, direkt von assistant gefolgt (12.14.1)
        [U(T("a")), A(USE("t1")), U(RES("t1"))],
        [U(T("a")), A(USE("t1"), USE("t2")), U(RES("t2"), RES("t1"), T("vorlage")), S("fakt")],
        [U(T("a")), A(USE("t1")), U(RES("t1"), T("vorlage")), S("fakt"), A(T("b")), U(T("c"))],
        [U(T("a")), A(THINK, T("b")), U(T("c"))],
    ],
)
def test_u_p1_valid_histories_pass(messages: list) -> None:
    check_placement(messages)


# --- Adapter (LT-34) -----------------------------------------------------------------------------------------------


class _Event:
    def __init__(self, kind: str) -> None:
        self.type = kind


class _Block:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
        return dict(self._data)


class _Final:
    def __init__(self, content, stop_reason="end_turn", usage=None, request_id="req_fake") -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage or {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 3,
                               "cache_creation_input_tokens": 0}
        self._request_id = request_id


class _Stream:
    def __init__(self, events, final, clock=None, raises: BaseException | None = None) -> None:
        self._events, self._final, self._clock, self._raises = events, final, clock, raises

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def __iter__(self):
        for kind, dt in self._events:
            if self._clock is not None:
                self._clock.advance(dt)
            yield _Event(kind)
        if self._raises is not None:
            raise self._raises

    def get_final_message(self):
        return self._final


class _Sdk:
    def __init__(self, stream: _Stream) -> None:
        self.calls: list[dict[str, Any]] = []
        self._stream = stream
        self.messages = self

    def stream(self, **kwargs: Any) -> _Stream:
        self.calls.append(kwargs)
        return self._stream


def _request(**kw: Any) -> ModelRequest:
    base = dict(system="SYSTEM", tools=tool_definitions(), messages=[U(T("Hallo"))], tool_choice={"type": "auto"},
                max_tokens=8000, effort="medium", model="claude-sonnet-5-5")
    base.update(kw)
    return ModelRequest(**base)


def test_lt_34_the_adapter_builds_the_request_streams_internally_and_returns_only_the_final_message() -> None:
    thinking = {"type": "thinking", "thinking": "überlege", "signature": "sig=="}
    final = _Final([_Block(thinking), _Block({"type": "text", "text": "Fertig."})])
    sdk = _Sdk(_Stream([("message_start", 0), ("content_block_delta", 1)], final))
    seen: list[str] = []
    response = AnthropicAdapter(sdk, clock=ManualClock()).create(_request(tool_choice={"type": "none"}), seen.append)

    kwargs = sdk.calls[0]
    assert kwargs["tool_choice"] == {"type": "none"} and kwargs["max_tokens"] == 8000
    assert kwargs["system"] == [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}]
    assert [t["name"] for t in kwargs["tools"]] == sorted(t["name"] for t in kwargs["tools"])
    assert all(t["strict"] is True for t in kwargs["tools"])
    assert {t["name"] for t in kwargs["tools"]}.isdisjoint({"confirm_peer_set", "reject_proposal"})
    assert seen == ["message_start", "content_block_delta"]  # nur Ereignisarten, nie Text
    assert response.content[0] == thinking  # Thinking-Block unverändert
    assert response.stop_reason == "end_turn" and response.request_id == "req_fake"
    assert response.usage == Usage(10, 5, 3, 0)


def test_the_adapter_refuses_an_invalid_history_before_anything_is_sent() -> None:
    sdk = _Sdk(_Stream([], _Final([])))
    with pytest.raises(PlacementError):
        AnthropicAdapter(sdk).create(_request(messages=[U(T("a")), A(T("b")), S("fakt")]), lambda k: None)
    assert sdk.calls == []


def test_a_stream_without_events_for_sixty_seconds_is_a_stall_and_a_long_stream_hits_the_call_deadline() -> None:
    clock = ManualClock()
    stalled = _Sdk(_Stream([("a", 1.0), ("b", 61.0)], _Final([]), clock))
    with pytest.raises(ModelCallError) as exc:
        AnthropicAdapter(stalled, clock=clock).create(_request(), lambda k: None)
    assert exc.value.kind == "stalled"

    clock = ManualClock()
    slow = _Sdk(_Stream([("a", 50.0)] * 7, _Final([]), clock))
    with pytest.raises(ModelCallError) as exc2:
        AnthropicAdapter(slow, clock=clock).create(_request(), lambda k: None)
    assert exc2.value.kind == "deadline"


def _named(name: str, **attrs: Any) -> BaseException:
    cls = type(name, (Exception,), {})
    exc = cls("Authorization: Bearer sk-ant-GEHEIM-0123456789 key=sk-ant-GEHEIM-0123456789")
    for key, value in attrs.items():
        setattr(exc, key, value)
    return exc


@pytest.mark.parametrize(
    ("name", "kind"),
    [("APIConnectionError", "unavailable"), ("APITimeoutError", "unavailable"), ("RateLimitError", "unavailable"),
     ("InternalServerError", "unavailable"), ("AuthenticationError", "auth"), ("PermissionDeniedError", "auth"),
     ("BadRequestError", "bad_request"), ("SomethingElse", "unexpected")],
)
def test_lt_44_sdk_exceptions_map_to_class_status_and_request_id_and_never_carry_the_text(name: str, kind: str) -> None:
    sdk = _Sdk(_Stream([("a", 0.0)], _Final([]), raises=_named(name, status_code=429, request_id="req_77")))
    with pytest.raises(ModelCallError) as exc:
        AnthropicAdapter(sdk, clock=ManualClock()).create(_request(), lambda k: None)
    error = exc.value
    assert error.kind == kind and error.error_class == name and error.status_code == 429 and error.request_id == "req_77"
    assert error.__cause__ is None and error.__context__ is None
    rendered = repr(error) + str(error) + json.dumps(error.args)
    assert "GEHEIM" not in rendered and "Bearer" not in rendered


def test_a_subclass_of_a_known_sdk_error_is_mapped_by_its_base_class_name() -> None:
    base = type("RateLimitError", (Exception,), {})
    sub = type("MyRateLimit", (base,), {})
    assert map_sdk_exception(sub("x")).kind == "unavailable"


def test_the_api_kwargs_carry_effort_and_adaptive_thinking_and_are_flagged_unverified() -> None:
    kwargs = build_api_kwargs(_request(effort="high"))
    assert kwargs["output_config"] == {"effort": "high"} and kwargs["thinking"] == {"type": "adaptive"}
    assert "ungeprüft" in build_api_kwargs.__doc__.lower()
