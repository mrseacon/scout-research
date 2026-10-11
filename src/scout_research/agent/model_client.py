"""Modell-Client der Agent-Schleife (Plan 12.2, 12.9, 12.14.4, 12.14.5; V3, V5).

- `ModelClient`-Protokoll: ein Aufruf liefert die **fertige** Antwort. Der Adapter streamt intern und puffert
  vollständig; vor `check_text` erreicht nichts den Host (SM-05). Die Ereignisse des Streams gehen nur als
  Aktivitätsmeldung (`on_event(kind)`) an den Loop, nie mit Text.
- `check_placement`: der Platzierungsprüfer (12.14.4) läuft vor **jedem** Senden, auch im `ScriptedModelClient`.
- `ScriptedModelClient`: Tests ohne API; prüft zusätzlich die Invarianten (Verlauf nur angehängt, System und Tools
  byte-identisch).
- `AnthropicAdapter`: Adapter zum SDK. Das SDK wird **nicht** importiert, sondern als Objekt übergeben
  (`make_sdk_client` importiert es erst bei Gebrauch); in Schritt 7 wird nichts gegen die echte API gerufen.

**Sicherheit:** SDK-Ausnahmen werden auf `ModelCallError` abgebildet, die nur Klasse, `status_code` und `request_id`
trägt — nie `str(exc)`, nie `__cause__` (SM-09).
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from scout_research.agent.budgets import Usage

Message = dict[str, Any]


class PlacementError(Exception):
    """Der Verlauf verletzt eine Platzierungsregel der API (12.14.1); ein Programmfehler, nie ein Modellfehler."""


class ModelCallError(Exception):
    """Ein Modellaufruf ist gescheitert. Nur `kind`, Klasse, Status und `request_id` — nie der Text der Ausnahme."""

    def __init__(
        self,
        kind: Literal["unavailable", "auth", "bad_request", "stalled", "deadline", "unexpected"],
        error_class: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(kind)
        self.kind = kind
        self.error_class = error_class
        self.status_code = status_code
        self.request_id = request_id


@dataclass(frozen=True)
class ModelRequest:
    system: str
    tools: list[dict[str, Any]]
    messages: list[Message]
    tool_choice: dict[str, str]
    max_tokens: int
    effort: str
    model: str


@dataclass
class ModelResponse:
    content: list[dict[str, Any]]
    stop_reason: str
    usage: Usage = field(default_factory=Usage)
    request_id: str | None = None


OnEvent = Callable[[str], None]


class ModelClient(Protocol):
    def create(self, request: ModelRequest, on_event: OnEvent) -> ModelResponse: ...


# --- Platzierungsprüfer (12.14.4) -----------------------------------------------------------------------------


def check_placement(messages: list[Message]) -> None:
    """Wirft `PlacementError`, wenn der Verlauf eine Regel verletzt:

    1. Der erste Eintrag ist `user`; der letzte ist `user` oder `system` (kein Prefill).
    2. Jede `system`-Nachricht folgt direkt auf einen `user`-Turn, dessen letzter Block ein `text`-Block ist, und ist
       letzter Eintrag oder wird direkt von einem `assistant`-Turn gefolgt (12.14.1).
    3. Jedes `tool_use` hat genau ein `tool_result` im unmittelbar folgenden `user`-Turn, und dort steht kein
       `tool_result` ohne `tool_use` des vorangehenden `assistant`-Turns.
    4. Keine zwei aufeinanderfolgenden Turns derselben Rolle."""
    if not messages:
        raise PlacementError("leerer Verlauf")
    if messages[0]["role"] != "user":
        raise PlacementError("erster Eintrag ist kein user-Turn")
    if messages[-1]["role"] == "assistant":
        raise PlacementError("letzter Eintrag ist ein assistant-Turn")
    last = len(messages) - 1
    for i, message in enumerate(messages):
        role = message["role"]
        if role not in {"user", "assistant", "system"}:
            raise PlacementError(f"unbekannte Rolle {role!r}")
        if not isinstance(message.get("content"), list) or not message["content"]:
            raise PlacementError(f"Eintrag {i}: Inhalt fehlt")
        if i and role == messages[i - 1]["role"]:
            raise PlacementError(f"Eintrag {i}: zwei {role}-Turns hintereinander")
        if role == "system":
            before = messages[i - 1] if i else None
            if before is None or before["role"] != "user":
                raise PlacementError(f"Eintrag {i}: system muss direkt auf einen user-Turn folgen")
            if before["content"][-1].get("type") != "text":
                raise PlacementError(f"Eintrag {i}: system direkt nach einem tool_result (letzter Block ist kein text)")
            if i != last and messages[i + 1]["role"] != "assistant":
                raise PlacementError(f"Eintrag {i}: system ist weder letzter Eintrag noch von assistant gefolgt")
        if role == "assistant":
            uses = [b["id"] for b in message["content"] if b.get("type") == "tool_use"]
            if uses:
                nxt = messages[i + 1] if i < last else None
                if nxt is None or nxt["role"] != "user":
                    raise PlacementError(f"Eintrag {i}: tool_use ohne folgenden user-Turn")
                results = [b.get("tool_use_id") for b in nxt["content"] if b.get("type") == "tool_result"]
                if sorted(results) != sorted(uses):
                    raise PlacementError(f"Eintrag {i}: tool_use und tool_result passen nicht zusammen")
        if role == "user":
            results = [b for b in message["content"] if b.get("type") == "tool_result"]
            if results:
                before = messages[i - 1] if i else None
                ids = {b["id"] for b in before["content"] if b.get("type") == "tool_use"} if before and before["role"] == "assistant" else set()
                if any(r.get("tool_use_id") not in ids for r in results):
                    raise PlacementError(f"Eintrag {i}: tool_result ohne tool_use im vorangehenden assistant-Turn")
            kinds = [b.get("type") for b in message["content"]]
            if "tool_result" in kinds and "text" in kinds and kinds.index("text") < len(kinds) - 1 - kinds[::-1].index("tool_result"):
                raise PlacementError(f"Eintrag {i}: tool_result nach einem text-Block")


# --- Frist- und Stillstandswächter -----------------------------------------------------------------------------


class StreamWatch:
    """Stillstand (kein Ereignis seit `stall_seconds`) und Gesamtfrist je Aufruf (V3); geprüft zwischen Ereignissen."""

    def __init__(self, clock: Callable[[], float], stall_seconds: float, call_seconds: float) -> None:
        self._clock = clock
        self._stall = stall_seconds
        self._total = call_seconds
        self._start = self._last = clock()

    def tick(self) -> None:
        now = self._clock()
        if now - self._last > self._stall:
            raise ModelCallError("stalled")
        if now - self._start > self._total:
            raise ModelCallError("deadline")
        self._last = now


class ManualClock:
    """Uhr für Tests: bewegt sich nur durch `advance`."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- ScriptedModelClient --------------------------------------------------------------------------------------


@dataclass
class Streamed:
    """Eine Antwort, die als Stream ankommt: `events` sind `(Art, Text, Sekunden seit dem letzten Ereignis)`;
    danach folgt `then` (Antwort oder Ausnahme)."""

    events: list[tuple[str, str, float]]
    then: ModelResponse | BaseException


Step = ModelResponse | BaseException | Streamed | Callable[[ModelRequest], Any]


class ScriptedModelClient:
    """Liefert vorgegebene Schritte der Reihe nach. Hält jede Anfrage als Kopie in `requests`.

    Invarianten, die **jeder** Aufruf prüft (12.11): Platzierung (`check_placement`); `system` und `tools`
    byte-identisch zum ersten Aufruf; der Verlauf beginnt mit dem zuletzt **akzeptierten** Stand (nur anhängen;
    ein gescheiterter Aufruf verändert ihn nicht)."""

    def __init__(self, steps: list[Step], clock: ManualClock | None = None,
                 stall_seconds: float = 60.0, call_seconds: float = 300.0) -> None:
        self._steps = list(steps)
        self.clock = clock or ManualClock()
        self._stall = stall_seconds
        self._total = call_seconds
        self.requests: list[ModelRequest] = []
        self.responses: list[ModelResponse] = []
        self._base: list[Message] = []
        self._fingerprint: str | None = None

    def create(self, request: ModelRequest, on_event: OnEvent) -> ModelResponse:
        check_placement(request.messages)
        fingerprint = json.dumps([request.system, request.tools], sort_keys=True, ensure_ascii=False)
        if self._fingerprint is None:
            self._fingerprint = fingerprint
        elif fingerprint != self._fingerprint:
            raise AssertionError("system oder tools haben sich zwischen zwei Aufrufen geändert (Prompt-Caching)")
        if request.messages[: len(self._base)] != self._base:
            raise AssertionError("der Verlauf wurde nicht nur angehängt")
        self.requests.append(copy.deepcopy(request))

        if not self._steps:
            raise AssertionError("ScriptedModelClient: kein Schritt mehr im Skript")
        step = self._steps.pop(0)
        if callable(step) and not isinstance(step, (ModelResponse, BaseException, Streamed)):
            step = step(request)
        if isinstance(step, Streamed):
            watch = StreamWatch(self.clock, self._stall, self._total)
            for kind, _text, dt in step.events:
                self.clock.advance(dt)
                watch.tick()
                on_event(kind)
            step = step.then
        if isinstance(step, BaseException):
            raise step
        self.responses.append(step)
        if step.stop_reason in {"end_turn", "tool_use"}:  # nur brauchbare Antworten kommen in den festen Verlauf (Z16)
            self._base = copy.deepcopy(request.messages) + [{"role": "assistant", "content": copy.deepcopy(step.content)}]
        return step


# --- SDK-Adapter ----------------------------------------------------------------------------------------------


def build_api_kwargs(request: ModelRequest) -> dict[str, Any]:
    """Argumente für `messages.stream`. **Ungeprüft** gegen die echte API (Schritt 9): Namen und Form folgen
    Plan 12.2 (`system` gecacht, `tools`, `tool_choice`, `output_config.effort`, adaptives Thinking)."""
    return {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "system": [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}],
        "tools": request.tools,
        "tool_choice": request.tool_choice,
        "messages": request.messages,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": request.effort},
    }


_UNAVAILABLE = {"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError"}
_AUTH = {"AuthenticationError", "PermissionDeniedError"}


def map_sdk_exception(exc: BaseException) -> ModelCallError:
    names = {c.__name__ for c in type(exc).__mro__}
    kind: Literal["unavailable", "auth", "bad_request", "unexpected"]
    if names & _AUTH:
        kind = "auth"
    elif names & _UNAVAILABLE:
        kind = "unavailable"
    elif "BadRequestError" in names:
        kind = "bad_request"
    else:
        kind = "unexpected"
    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    return ModelCallError(
        kind,
        error_class=type(exc).__name__,
        status_code=status if isinstance(status, int) else None,
        request_id=request_id if isinstance(request_id, str) else None,
    )


def _block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return copy.deepcopy(block)
    return block.model_dump(exclude_none=True)


def _usage_of(raw: Any) -> Usage:
    def count(name: str) -> int:
        value = raw.get(name) if isinstance(raw, dict) else getattr(raw, name, 0)
        return int(value or 0)

    return Usage(count("input_tokens"), count("output_tokens"),
                 count("cache_read_input_tokens"), count("cache_creation_input_tokens"))


class AnthropicAdapter:
    """Streamt intern, puffert vollständig, gibt nur die fertige Nachricht zurück. Das SDK-Objekt wird übergeben."""

    def __init__(self, sdk_client: Any, clock: Callable[[], float] = time.monotonic,
                 stall_seconds: float = 60.0, call_seconds: float = 300.0) -> None:
        self._sdk = sdk_client
        self._clock = clock
        self._stall = stall_seconds
        self._total = call_seconds

    def create(self, request: ModelRequest, on_event: OnEvent) -> ModelResponse:
        check_placement(request.messages)
        watch = StreamWatch(self._clock, self._stall, self._total)
        failure: ModelCallError | None = None
        final: Any = None
        try:
            with self._sdk.messages.stream(**build_api_kwargs(request)) as stream:
                for event in stream:
                    watch.tick()
                    on_event(str(getattr(event, "type", "event")))
                final = stream.get_final_message()
                watch.tick()
        except ModelCallError as exc:
            failure = exc
        except Exception as exc:  # noqa: BLE001 — jede SDK-Ausnahme wird auf Klasse/Status/request_id reduziert
            failure = map_sdk_exception(exc)
        if failure is not None:
            raise failure  # außerhalb des except-Blocks: weder __cause__ noch __context__ (SM-09)

        request_id = getattr(final, "_request_id", None) or getattr(final, "request_id", None)
        return ModelResponse(
            content=[_block_to_dict(b) for b in final.content],
            stop_reason=str(final.stop_reason),
            usage=_usage_of(final.usage),
            request_id=request_id if isinstance(request_id, str) else None,
        )


def make_sdk_client(api_key: str):  # pragma: no cover — erst im Live-Lauf (Schritt 9)
    """Baut den SDK-Client. **Ungeprüft** und in Schritt 7 nie aufgerufen: `anthropic` ist optional und der
    Schlüssel bleibt leer. `max_retries=2` und der Stillstands-Timeout folgen 12.14.5."""
    import anthropic

    return anthropic.Anthropic(
        api_key=api_key,
        max_retries=2,
        timeout=anthropic.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0),
    )
