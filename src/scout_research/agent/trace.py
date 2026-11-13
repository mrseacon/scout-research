"""Trace-Schreiber der Agent-Schicht (Plan 6, 12.10; Schritt 8).

Eine Sitzung schreibt **eine** JSONL-Datei: `base_dir/<JJJJ-MM-TT>/<run_id>.jsonl`, nie überschrieben (Modus `x`). Die
Zeilen sind Pydantic-Modelle (`extra="forbid"`), je Ereignisart eins, als diskriminierte Union über das Feld `event`.
Der Schreiber liest die rohen Ereignisse des Loops (`loop.events`), das Gate-Protokoll (`ctx.gate_log`) und das
Kommentar-Protokoll (`ctx.commentary_log`); er ändert nichts davon und liest nie `Settings` (der Kopf trägt eine
Whitelist, T12, 12.10).

Jede Zeile wird **vor dem Schreiben** rekursiv geschwärzt (alle Strings, Schlüssel und Werte, `ctx.redact`), dann gegen
ihr Modell validiert, dann serialisiert. Danach folgt eine Gegenprobe auf der fertigen Zeile: Ändert die Schwärzung sie
noch, wird **nicht** geschrieben, sondern `TraceLeakError` geworfen — laut statt still (T12).

Kein Import aus `data/` oder `eval/` (T8, A-E1); die Typen von Loop und Prompt-Lader kommen nur für die Typprüfung.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import secrets
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

from scout_research.agent.model_client import ModelErrorKind
from scout_research.agent.prompt_loader import tools_hash

if TYPE_CHECKING:
    from scout_research.agent.loop import AgentLoop
    from scout_research.agent.prompt_loader import SystemPrompt

_RUN_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,100}")
_HEAD_KEYS = ("event", "seq", "ts", "run_id", "turn")
_ESCAPE = re.compile(r"\\(?:u[0-9a-fA-F]{4}|.)", re.DOTALL)
_URL_QUERY = re.compile(r"(https?://[^\s?#\"']*)\?[^\s\"'#]*")
_KNOWN_GATE_ENTRIES = frozenset(("proposed", "confirmed", "rejected", "commentary"))
_GIT_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
LOOP_EVENTS = frozenset(("model_call", "model_error", "tool", "text_check", "unusable_response", "loop_error",
                         "budget_stop", "turn_end"))
"""Ereignisarten, die der Loop selbst erzeugt (`gate` ist nur Positionsmarker und wird nicht geschrieben)."""


class TraceLeakError(Exception):
    """Eine fertige Trace-Zeile enthält nach der Schwärzung noch ein Secret. Sie wurde nicht geschrieben; die Meldung
    nennt das Ereignis, nie den Inhalt (T12)."""


class TraceSchemaError(ValueError):
    """Eine Zeile oder ein Ereignis entspricht nicht dem Schema. Die Meldung nennt Feldpfade und Gründe, nie Werte."""


# --- Schema -------------------------------------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Line(_Model):
    """Gemeinsame Felder jeder Zeile."""

    seq: int = Field(ge=0)
    ts: str
    run_id: str
    turn: int = Field(ge=0)

    @field_validator("ts")
    @classmethod
    def _iso_8601(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("ts ist kein ISO-8601-Zeitstempel") from None
        if parsed.tzinfo is None:
            raise ValueError("ts trägt keine Zeitzone")
        return value


class TracePrice(_Model):
    input: float
    output: float
    cache_read: float
    cache_write: float


class TraceParams(_Model):
    """Whitelist der Parameter im Kopf (12.10): genau diese Felder aus `LoopConfig`, nie `Settings`."""

    max_tokens: int
    max_calls_per_turn: int
    max_tools_per_turn: int
    turn_seconds: float
    call_seconds: float
    stall_seconds: float
    cost_warning_usd: float
    cost_limit_usd: float
    context_limit_tokens: int
    budget_run_usd: float | None
    budget_day_usd: float | None
    price: TracePrice


class HeaderEvent(_Line):
    event: Literal["header"]
    session_id: str
    run_mode: Literal["interactive", "eval", "demo"]
    git_commit: str | None
    model: str
    effort: str
    output_language: str
    prompt_version: str
    prompt_sha256: str
    tools_sha256: str
    params: TraceParams

    @field_validator("git_commit")
    @classmethod
    def _full_hash(cls, value: str | None) -> str | None:
        if value is not None and _GIT_COMMIT.fullmatch(value) is None:
            raise ValueError("git_commit ist kein vollständiger Hash (40 oder 64 Hexzeichen, klein)")
        return value


class ModelCallEvent(_Line):
    event: Literal["model_call"]
    stop_reason: str | None
    request_id: str | None
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cost_usd: float
    session_cost_usd: float
    tool_choice: str
    messages: int
    latency_s: float
    upper_input_tokens: int
    model: str | None = Field(default=None, max_length=100)
    """Das Modell des Aufrufs (TA-20.6): im Live-Lauf das von der API in der Antwort gemeldete (kann vom konfigurierten
    abweichen), in Skript-Läufen das angefragte. Das konfigurierte steht im Kopf (SG-19). Mit
    Vorgabe `None` wie `latency_s` unten: ältere Traces und handgebaute Ereignisse bleiben gültig; der Loop liefert es immer."""


class ModelErrorEvent(_Line):
    event: Literal["model_error"]
    kind: ModelErrorKind
    error_class: str | None
    status_code: int | None
    request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_input_tokens: int | None
    cache_creation_input_tokens: int | None
    cost_usd: float
    usage_estimated: bool
    session_cost_usd: float
    latency_s: float | None = None
    """Der Loop liefert sie für gescheiterte Aufrufe bisher nicht (Befund im Bericht zu TA-2)."""


class UpstreamRequest(_Model):
    """Eine HTTP-Antwort, die ein Tool ausgelöst hat: nur Host, Pfad und Status. Es gibt bewusst kein Feld für Query-String
    (Finnhub-Key), Header (User-Agent) oder Body (12.10, T12)."""

    host: str
    path: str
    status: int

    @field_validator("path")
    @classmethod
    def _no_query_or_fragment(cls, value: str) -> str:
        if "?" in value or "#" in value:
            raise ValueError("path trägt Query-String oder Fragment")
        return value


class CacheHit(_Model):
    """Ein Cache-Zugriff während eines Tools: Namespace und Treffer, nie der Schlüssel."""

    namespace: str
    hit: bool


class ToolEvent(_Line):
    event: Literal["tool"]
    name: str
    is_error: bool
    code: str | None
    tool_use_id: str
    input: Any
    result: dict[str, Any]
    """`outcome.content`: die Fassung für das Modell."""
    detail: dict[str, Any] | None
    """`outcome.trace` (Fehlerklasse, Endpunkt, Status, geschwärzter Stacktrace)."""
    duration_s: float
    upstream: list[UpstreamRequest] = Field(default_factory=list)
    """HTTP-Antworten, die dieses Tool ausgelöst hat (ohne Query-String und Header)."""
    cache: list[CacheHit] = Field(default_factory=list)
    """Cache-Zugriffe dieses Tools (Treffer und Fehlgriffe)."""
    full_result: dict[str, Any] | None
    """Gespeichertes Objekt mit Provenance (Comps-Tabelle bzw. Finanzdaten); vom Schreiber ergänzt."""


class Finding(_Model):
    code: str
    kind: str
    start: int
    end: int
    match: str


class ExtractedNumber(_Model):
    """Eine Zahl, die der Backstop gesehen hat: `violation` = Fundstelle der Prüfung, `exempt` = Zahl innerhalb einer
    Ausnahme (Plan 11.2, Klassen 1–6). `start`/`end` beziehen sich auf den Originaltext."""

    start: int
    end: int
    kind: str
    match: str
    status: Literal["violation", "exempt"]
    exempt_span: str | None
    """Rohtext der ganzen Ausnahmespanne („10-K“ zur Zahl „10“); bei `violation` `None`."""


class TextCheckEvent(_Line):
    event: Literal["text_check"]
    where: Literal["end_turn", "beside_tool_use", "intro"]
    problems: list[str]
    shown: bool
    findings: list[Finding]
    action: Literal["shown", "correction", "withheld", "not_shown", "intro_fixed"]
    extracted: list[ExtractedNumber]


class UnusableResponseEvent(_Line):
    event: Literal["unusable_response"]
    stop_reason: str | None
    empty: bool


class LoopErrorEvent(_Line):
    event: Literal["loop_error"]
    error_class: str
    traceback: str


class BudgetStopEvent(_Line):
    """Lauf- oder Tagesgrenze erreicht bzw. Kostenbuch nicht lesbar (TA-12b); `day_total_usd` ist dann `None`."""
    event: Literal["budget_stop"]
    reason: Literal["budget_run", "budget_day", "budget_ledger"]
    run_spent_usd: float
    day_total_usd: float | None


TurnReason = Literal[
    # Zugende des Loops (`_finish`, `ended`): Abbruch, Grenzen, Ergebnis
    "interrupted", "interrupted_twice", "aborted", "loop_error", "limit_calls", "turn_deadline", "limit_tools",
    "loop_guard", "withheld", "commentary", "placement",
    # Schließgründe (`_closing`) und Fehlerarten des Modelldienstes (`ModelErrorKind`)
    "cost", "context", "budget_run", "budget_day", "budget_ledger", "auth", "not_found", "bad_request", "unprocessable",
    "unavailable", "stalled", "deadline", "unexpected",
    # verworfene Antworten (`_unusable`)
    "refusal", "max_tokens", "empty_response", "unexpected_stop",
    # Einleitung nach dem Vorschlag: `intro_<Grund>` des gescheiterten Aufrufs bzw. des Hosts
    "intro_skipped", "intro_unusable", "intro_tool_use", "intro_fixed", "intro_unavailable", "intro_stalled",
    "intro_deadline", "intro_unexpected", "intro_bad_request", "intro_unprocessable", "intro_auth", "intro_not_found",
    "intro_interrupted", "intro_budget_run", "intro_budget_day", "intro_budget_ledger",
]
"""Alle Gründe, die `turn_end` tragen darf. Ein neuer Grund im Loop muss hier stehen (Test gleicht den Quelltext ab)."""


class TurnEndEvent(_Line):
    event: Literal["turn_end"]
    outcome: str
    reason: TurnReason | None
    state: str
    calls: int
    tools: int
    closing: Literal["cost", "context", "auth", "not_found", "bad_request", "budget_run", "budget_day", "budget_ledger",
                     "placement"] | None
    session_cost_usd: float


class CandidateSetInfo(_Model):
    id: str
    target_cik: str
    period_end: str
    candidates: list[str]


class PeerInfo(_Model):
    cik: str
    ticker: str | None
    rationale: str | None


class GateEvent(_Line):
    """Entscheidung im Gate, aus `ctx.gate_log` (vollständiger als die Loop-Ereignisse `gate`)."""

    event: Literal["gate"]
    decision: Literal["proposed", "confirmed", "rejected"]
    proposal_id: str
    decided_at: str | None = None
    # proposed
    candidate_set: CandidateSetInfo | None = None
    peers: list[PeerInfo] | None = None
    user_additions: list[str] | None = None
    rationale_source: str | None = None
    # confirmed
    peer_set_id: str | None = None
    proposed: list[str] | None = None
    confirmed: list[str] | None = None
    removed: list[str] | None = None
    added: list[str] | None = None
    confirmed_by: str | None = None
    # confirmed und rejected
    channel: str | None = None
    rejected_by: str | None = None

    @model_validator(mode="after")
    def _required_per_decision(self) -> GateEvent:
        required = {
            "proposed": ("candidate_set", "peers", "user_additions", "rationale_source"),
            "confirmed": ("peer_set_id", "channel", "confirmed_by", "proposed", "confirmed", "removed", "added"),
            "rejected": ("channel", "rejected_by"),
        }[self.decision]
        missing = [name for name in required if getattr(self, name) is None]
        if missing:
            raise ValueError(f"gate/{self.decision}: Pflichtfelder fehlen: {', '.join(missing)}")
        return self


class CommentaryEvent(_Line):
    event: Literal["commentary"]
    comps_table_id: str
    attempt: int
    raw_text: str
    slots: list[dict[str, Any]]
    problems: list[dict[str, Any]]
    format_chars: int
    extracted: list[ExtractedNumber]
    action: Literal["rejected", "withheld", "accepted", "accepted_after_n"] | None
    rendered: str | None = None
    warnings_addressed: list[str] | None = None
    warnings_not_addressed: list[str] | None = None


class TraceErrorEvent(_Line):
    """Ersatz für ein Ereignis, das Schema oder Gegenprobe nicht bestand. Ohne jeden Inhalt des Ereignisses."""

    event: Literal["trace_error"]
    source: Literal["loop", "gate", "commentary"]
    source_event: str | None
    """Ereignisart, nur wenn sie zu den bekannten gehört."""
    error_class: str
    index: int = Field(ge=0)
    """Position in der Quelle (`loop.events`, `gate_log`, `commentary_log`)."""


class EndEvent(_Line):
    event: Literal["end"]
    state: str
    session_cost_usd: float
    model_calls: int
    last_outcome: str | None
    last_reason: str | None
    reason: Literal["host_exception", "trace_error"] | None
    error_class: str | None
    dropped_events: int = Field(ge=0)


TraceEvent = Annotated[
    Union[
        HeaderEvent, ModelCallEvent, ModelErrorEvent, ToolEvent, TextCheckEvent, UnusableResponseEvent, LoopErrorEvent,
        BudgetStopEvent, TurnEndEvent, GateEvent, CommentaryEvent, TraceErrorEvent, EndEvent,
    ],
    Field(discriminator="event"),
]
_ADAPTER: TypeAdapter[Any] = TypeAdapter(TraceEvent)


def _summary(exc: ValidationError) -> str:
    """Ort und Grund je Fehler — ohne `input_value` (könnte Inhalt wiederholen)."""
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '<zeile>'}: {e['msg']}" for e in exc.errors(include_input=False,
                                                                                                          include_url=False))


def _validate(data: Any) -> Any:
    if not isinstance(data, dict):
        raise TraceSchemaError("Die Zeile ist kein JSON-Objekt")
    try:
        return _ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise TraceSchemaError(_summary(exc)) from None


def validate_trace_line(line: str) -> Any:
    """Eine Zeile gegen das Schema; gibt das Modell der Ereignisart zurück, wirft `ValueError` (`TraceSchemaError`)."""
    try:
        data = json.loads(line)
    except ValueError as exc:
        raise TraceSchemaError(f"kein gültiges JSON ({type(exc).__name__})") from None
    return _validate(data)


def validate_trace_file(path: Path) -> list[Any]:
    """Alle Zeilen einer Trace-Datei. Wirft bei der ersten ungültigen Zeile mit Zeilennummer; zusätzlich: erste Zeile ist
    der Kopf, `seq` zählt lückenlos ab 0, genau ein `end` als letzte Zeile."""
    name = Path(path).name
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    models: list[Any] = []
    for number, line in enumerate(lines, start=1):
        try:
            model = validate_trace_line(line)
            if number == 1 and model.event != "header":
                raise TraceSchemaError("Die erste Zeile ist nicht der Kopf")
            if model.seq != number - 1:
                raise TraceSchemaError(f"seq {model.seq} statt {number - 1}")
            if model.event == "end" and number != len(lines):
                raise TraceSchemaError("end steht nicht als letzte Zeile")
        except TraceSchemaError as exc:
            raise TraceSchemaError(f"{name}: Zeile {number}: {exc}") from None
        models.append(model)
    if not models or models[-1].event != "end":
        raise TraceSchemaError(f"{name}: Zeile {max(len(lines), 1)}: Die letzte Zeile ist nicht end")
    return models


def validate_loop_event(event: dict[str, Any]) -> None:
    """Prüft ein rohes Loop-Ereignis (`loop.events`) gegen sein Modell, mit Dummy-Werten für die gemeinsamen Felder und
    ohne `full_result` (das ergänzt erst der Schreiber). `gate` ist ein Positionsmarker und wird übersprungen. Wirft
    `TraceSchemaError`. Die Schleifen-Szenarien prüfen damit bei jedem Lauf das Schema mit."""
    kind = event.get("event")
    if kind == "gate":
        return
    if kind not in LOOP_EVENTS:
        raise TraceSchemaError("kein Ereignis, das der Loop erzeugt")
    raw = {**{k: v for k, v in event.items() if k != "log_pos"}, "seq": 0, "ts": "2000-01-01T00:00:00+00:00", "run_id": "dummy", "turn": 0}
    if kind == "tool":
        raw.setdefault("full_result", None)
    _validate(raw)


# --- Hilfen ------------------------------------------------------------------------------------------------------


def current_git_commit(cwd: Path | None = None) -> str | None:
    """`git rev-parse HEAD`; bei jedem Fehler (kein Repository, kein git) `None`."""
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    commit = done.stdout.strip()
    return commit if done.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) else None


def _strip_queries(text: str) -> str:
    """12.10: keine Query-Strings im Trace. `https://host/pfad?a=b` wird zu `https://host/pfad?<query>`."""
    return _URL_QUERY.sub(r"\1?<query>", text)


def _redact_tree(value: Any, redact: Callable[[str], str]) -> Any:
    """Schwärzt rekursiv alle Strings: Werte **und** Schlüssel."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {(redact(k) if isinstance(k, str) else k): _redact_tree(v, redact) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_tree(v, redact) for v in value]
    return value


def _default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    return str(value)


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_default, ensure_ascii=False))


# --- Schreiber ---------------------------------------------------------------------------------------------------


class TraceWriter:
    """Schreibt den Trace einer Sitzung. Kontextmanager; der Host ruft `flush()` nach jedem `run_turn`/`confirm`/`reject`.

    Der Kopf entsteht im Konstruktor. `flush()` schreibt alles Neue seit dem letzten Aufruf und erhöht danach `turn`: die
    Loop-Ereignisse in ihrer Reihenfolge, die neuen Einträge aus `gate_log` und `commentary_log` an ihrer Stelle (ein
    Loop-Ereignis `gate` ist Positionsmarker, nach jedem `tool` folgen die bis dahin neuen Einträge), den Rest am Ende.

    Besteht ein Ereignis Schema oder Gegenprobe nicht, wird es nicht geschrieben; an seine Stelle tritt eine Zeile
    `trace_error` ohne Inhalt, der Zähler der Quelle läuft weiter und alles Folgende wird geschrieben. `flush()` wirft den
    Fehler erst am Ende (laut, aber ohne Datenverlust). `close(exc)` schreibt in jedem Fall das Schlussereignis `end`
    (idempotent; auch nach `FAILED`, `CLOSED` und einer Ausnahme im Host) und wirft danach den gesammelten Fehler, sofern
    der Host keine Ausnahme hat: Die Ausnahme des Hosts bleibt immer die, die aufsteigt."""

    def __init__(
        self,
        loop: AgentLoop,
        prompt: SystemPrompt,
        *,
        base_dir: Path,
        session_id: str | None = None,
        git_commit: str | None = None,
        run_id: str | None = None,
    ) -> None:
        if prompt.text != loop.system_prompt:
            raise ValueError("Der Prompt entspricht nicht dem Text, den die Schleife sendet")
        self._loop = loop
        self._ctx = loop.ctx
        self._redact = loop.ctx.redact
        self._dropped = 0
        self._first_error: Exception | None = None
        if git_commit is not None and _GIT_COMMIT.fullmatch(git_commit) is None:  # vor dem Anlegen der Datei
            raise ValueError("git_commit ist kein vollständiger Hash (40 oder 64 Hexzeichen, klein)")
        now = self._now()
        self.run_id = run_id if run_id is not None else f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(6)}"
        if _RUN_ID.fullmatch(self.run_id) is None or ".." in self.run_id:
            raise ValueError("run_id darf nur Buchstaben, Ziffern, Punkt, Unterstrich und Bindestrich enthalten")
        directory = Path(base_dir) / f"{now:%Y-%m-%d}"
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{self.run_id}.jsonl"
        self._seq = 0
        self._turn = 0
        self._events_done = 0
        self._gates_done = 0
        self._commentaries_done = 0
        self._model_calls = 0
        self._last_turn_end: dict[str, Any] | None = None
        self._closed = False
        self._file = open(self.path, "x", encoding="utf-8")  # noqa: SIM115 — lebt bis `close`; nie überschreiben
        try:
            config = loop.config
            budget = loop.budget
            self._emit({
                "event": "header", "session_id": session_id or f"s-{secrets.token_hex(6)}",
                "run_mode": ("eval" if loop.ctx.eval_session is not None else
                             "demo" if loop.ctx.demo_session is not None else "interactive"),
                "git_commit": git_commit, "model": config.model, "effort": config.effort,
                "output_language": config.language, "prompt_version": prompt.version,
                "prompt_sha256": _sha256(loop.system_prompt), "tools_sha256": tools_hash(loop.tools),
                "params": {
                    "max_tokens": config.max_tokens, "max_calls_per_turn": config.max_calls_per_turn,
                    "max_tools_per_turn": config.max_tools_per_turn, "turn_seconds": config.turn_seconds,
                    "call_seconds": config.call_seconds, "stall_seconds": config.stall_seconds,
                    "cost_warning_usd": config.cost_warning_usd, "cost_limit_usd": config.cost_limit_usd,
                    "context_limit_tokens": config.context_limit_tokens,
                    "budget_run_usd": budget.run_limit_usd if budget is not None else None,
                    "budget_day_usd": budget.day_limit_usd if budget is not None else None,
                    "price": {"input": config.price.input, "output": config.price.output,
                              "cache_read": config.price.cache_read, "cache_write": config.price.cache_write},
                },
            })
        except BaseException:
            self._file.close()
            raise

    # --- Kontextmanager ---------------------------------------------------------------------------------------

    def __enter__(self) -> TraceWriter:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close(exc)
        return False  # die Ausnahme des Hosts wird nie geschluckt

    # --- Schreiben --------------------------------------------------------------------------------------------

    def flush(self) -> None:
        """Schreibt alles Neue seit dem letzten Aufruf und erhöht `turn`. Ein verworfenes Ereignis hinterlässt eine Zeile
        `trace_error`; der Fehler (`TraceLeakError` bzw. `TraceSchemaError`) steigt erst nach dem letzten schreibbaren
        Ereignis auf."""
        if self._closed:
            return
        errors: list[Exception] = []
        events = self._loop.events
        while self._events_done < len(events):
            index, event = self._events_done, events[self._events_done]
            self._events_done += 1
            kind = event.get("event")
            pos = event.get("log_pos") if isinstance(event.get("log_pos"), dict) else {}
            if kind == "gate":  # Positionsmarker: die Einträge aus `gate_log` bis hierher stehen an dieser Stelle
                self._drain("gate", errors, pos.get("gate"))
                continue
            self._guard("loop", index, kind if kind in LOOP_EVENTS else None, errors,
                        lambda event=event: self._write_loop_event(event))
            if kind == "tool" and pos:  # `proposed` steht hinter `propose_peer_set`, Einreichungen hinter `submit_commentary`
                self._drain("gate", errors, pos.get("gate"))
                self._drain("commentary", errors, pos.get("commentary"))
        self._drain("gate", errors)
        self._drain("commentary", errors)
        self._turn += 1
        if errors:
            raise next((e for e in errors if isinstance(e, TraceLeakError)), errors[0])

    def close(self, exc: BaseException | None = None) -> None:
        if self._closed:
            return
        error: Exception | None = None
        try:
            self.flush()
        except Exception as caught:  # noqa: BLE001 — wird nach `end` geworfen (ohne Host-Ausnahme)
            error = caught
        except BaseException as interrupt:  # Strg+C o. Ä. während des Flush: die Datei endet trotzdem mit `end`
            self._closed = True
            try:
                self._emit({
                    "event": "end", "state": self._loop.state.value, "session_cost_usd": self._loop.session_cost,
                    "model_calls": self._model_calls, "last_outcome": (self._last_turn_end or {}).get("outcome"),
                    "last_reason": (self._last_turn_end or {}).get("reason"), "reason": "host_exception",
                    "error_class": type(interrupt).__name__, "dropped_events": self._dropped,
                })
            except BaseException:  # noqa: BLE001, S110 — der Abbruch selbst steigt auf
                pass
            self._file.close()
            raise
        self._closed = True
        try:
            last = self._last_turn_end or {}
            origin = exc if exc is not None else self._first_error
            self._emit({
                "event": "end", "state": self._loop.state.value, "session_cost_usd": self._loop.session_cost,
                "model_calls": self._model_calls, "last_outcome": last.get("outcome"),
                "last_reason": last.get("reason"),
                "reason": "host_exception" if exc is not None else ("trace_error" if self._dropped else None),
                "error_class": type(origin).__name__ if origin is not None else None,
                "dropped_events": self._dropped,
            })
        except Exception as end_error:  # noqa: BLE001
            if exc is None and error is None:
                error = end_error
        finally:
            self._file.close()
        if error is not None and exc is None:
            raise error

    # --- Ereignisse umsetzen ------------------------------------------------------------------------------------

    def _guard(self, source: str, index: int, source_event: str | None, errors: list[Exception], write) -> None:
        """Schreibt über `write`; scheitert es, steht stattdessen `trace_error` (ohne Inhalt) in der Datei."""
        try:
            write()
        except BaseException as exc:  # noqa: BLE001 — jedes Ereignis, das sich nicht schreiben lässt, wird gezählt
            if not isinstance(exc, Exception):  # Abbruch (Strg+C): zählen, nichts mehr schreiben, sofort weiterwerfen
                self._dropped += 1
                raise
            self._dropped += 1
            errors.append(exc)
            if self._first_error is None:
                self._first_error = exc
            try:
                self._emit({"event": "trace_error", "source": source, "source_event": source_event,
                            "error_class": type(exc).__name__, "index": index})
            except Exception:  # noqa: BLE001 — selbst das nicht schreibbar: gezählt, der Fehler steigt auf
                pass

    def _drain(self, source: Literal["gate", "commentary"], errors: list[Exception], upto: int | None = None) -> None:
        """Gibt die Einträge der Quelle bis zur Position `upto` aus (ohne Angabe: alle neuen)."""
        log = self._ctx.gate_log if source == "gate" else self._ctx.commentary_log
        build = self._gate_event if source == "gate" else self._commentary_event
        limit = len(log) if not isinstance(upto, int) else min(upto, len(log))
        while (done := self._gates_done if source == "gate" else self._commentaries_done) < limit:
            if source == "gate":
                self._gates_done += 1
            else:
                self._commentaries_done += 1
            entry = log[done]
            kind = "commentary" if source == "commentary" else entry.get("event")
            self._guard(source, done, kind if kind in _KNOWN_GATE_ENTRIES else None, errors,
                        lambda entry=entry: self._emit(build(entry)))

    def _write_loop_event(self, event: dict[str, Any]) -> None:
        kind = event.get("event")
        if kind in ("model_call", "model_error"):
            self._model_calls += 1  # gezählt wird der Aufruf, auch wenn seine Zeile verworfen wird
        elif kind == "turn_end":
            self._last_turn_end = event
        raw = {k: v for k, v in event.items() if k != "log_pos"}  # nur zum Einordnen, nicht Teil der Zeile
        if kind == "tool":
            raw["full_result"] = self._full_result(event)
        self._emit(raw)

    def _full_result(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Das gespeicherte Objekt mit Provenance, gefunden über das Handle im Ergebnis (nur bei Erfolg)."""
        result = event.get("result")
        if event.get("is_error") or not isinstance(result, dict):
            return None
        if event.get("name") == "compute_comps_table":
            stored = self._ctx.tables.get(result.get("comps_table_id"))
            if stored is None:
                return None
            return _jsonable({"id": stored.id, "peer_set_id": stored.peer_set_id, "table": stored.table,
                              "skipped_peers": stored.skipped_peers, "warnings": stored.warnings})
        if event.get("name") == "get_financials":
            stored_fin = self._ctx.financials.get(result.get("financials_id"))
            if stored_fin is None:
                return None
            return _jsonable({"id": stored_fin.id, "cik": stored_fin.cik, "payload": stored_fin.payload})
        return None

    def _gate_event(self, entry: dict[str, Any]) -> dict[str, Any]:
        raw: dict[str, Any] = {"event": "gate", "decision": entry.get("event"), "proposal_id": entry.get("proposal_id")}
        if entry.get("event") == "proposed":
            candidate_set = self._ctx.candidate_sets.get(entry.get("candidate_set_id", ""))
            proposal = self._ctx.proposals.get(entry.get("proposal_id", ""))
            details = {p["cik"]: p for p in (proposal.peers if proposal else [])}
            raw.update(
                candidate_set=None if candidate_set is None else {
                    "id": candidate_set.id, "target_cik": candidate_set.target_cik, "period_end": candidate_set.period_end,
                    "candidates": [c["cik"] for c in candidate_set.candidates]},
                peers=[{"cik": cik, "ticker": details.get(cik, {}).get("ticker"),
                        "rationale": details.get(cik, {}).get("rationale")} for cik in entry.get("peers", [])],
                user_additions=entry.get("user_additions"), rationale_source=entry.get("rationale_source"))
        elif entry.get("event") == "confirmed":
            raw.update(decided_at=entry.get("confirmed_at"), peer_set_id=entry.get("peer_set_id"),
                       channel=entry.get("channel"), confirmed_by=entry.get("confirmed_by"),
                       proposed=entry.get("proposed"), confirmed=entry.get("confirmed"),
                       removed=entry.get("removed"), added=entry.get("added"))
        elif entry.get("event") == "rejected":
            raw.update(decided_at=entry.get("rejected_at"), channel=entry.get("channel"),
                       rejected_by=entry.get("rejected_by"))
        return raw

    def _commentary_event(self, entry: dict[str, Any]) -> dict[str, Any]:
        raw: dict[str, Any] = {
            "event": "commentary", "comps_table_id": entry.get("comps_table_id"), "attempt": entry.get("attempt"),
            "raw_text": entry.get("raw_text"), "slots": entry.get("slots"), "problems": entry.get("problems"),
            "format_chars": entry.get("format_chars"), "extracted": entry.get("extracted"),
            "action": entry.get("action"),
        }
        if entry.get("action") in ("accepted", "accepted_after_n", "withheld"):
            stored = self._ctx.commentaries.get(entry.get("comps_table_id", ""))
            raw["rendered"] = entry["rendered"] if "rendered" in entry else (stored.rendered if stored else None)
            raw["warnings_addressed"] = entry.get("warnings_addressed", stored.warnings_addressed if stored else None)
            raw["warnings_not_addressed"] = entry.get("warnings_not_addressed",
                                                      stored.warnings_not_addressed if stored else None)
        return raw

    # --- Schwärzen, prüfen, schreiben ---------------------------------------------------------------------------

    def _scrub(self, text: str) -> str:
        """Schwärzung, dann keine Query-Strings in URLs (12.10)."""
        return _strip_queries(self._redact(text))

    def _now(self) -> datetime:
        now = self._ctx.now()
        return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)

    def _emit(self, raw: dict[str, Any]) -> None:
        raw = {**raw, "seq": self._seq, "ts": self._now().isoformat(), "run_id": self.run_id, "turn": self._turn}
        clean = _redact_tree(raw, self._scrub)
        data = _validate(clean).model_dump(mode="json")
        ordered = {**{k: data[k] for k in _HEAD_KEYS}, **{k: v for k, v in data.items() if k not in _HEAD_KEYS}}
        line = json.dumps(ordered, ensure_ascii=False)
        # Gegenprobe auf der fertigen Zeile: ein Secret, das erst durch die Serialisierung entsteht (etwa als Zahl) oder
        # die rekursive Schwärzung umgeht, fällt hier auf. JSON-Escapes (`\n`, `\"`, `\\`) werden vorher durch ein
        # Leerzeichen ersetzt: Sonst verlängern die Muster der Schwärzung (`token=…`) ihren Treffer über ein `\n` hinaus
        # und melden einen Fehlalarm bei bereits geschwärztem, mehrzeiligem Text.
        probe = _ESCAPE.sub(" ", line)
        if self._redact(probe) != probe:
            raise TraceLeakError(f"Die Trace-Zeile (Ereignis {ordered['event']}) enthält nach der Schwärzung noch ein "
                                 "Secret; sie wurde nicht geschrieben")
        self._file.write(line + "\n")
        self._file.flush()
        self._seq += 1


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

