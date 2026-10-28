"""Agent-Schleife (Plan 12, 12.14; Schritt 7, Phase B).

Eine Sitzung ist ein `AgentLoop`. Der Host ruft `run_turn(HumanInput)`, `confirm(...)`, `reject(...)`; kein anderer
Code darf das Nutzerprotokoll schreiben oder ein Gate entscheiden (A-H1). Die Schleife ruft nur
`tools.handlers.dispatch`, `tools.commentary.check_text`/`build_check_session` und den `ModelClient` — kein Import
aus `data/` (T8).

Was die Schleife durchsetzt, auch wenn das Modell die Regeln bricht (12.11): jede Antwort, die der Nutzer sieht, läuft
durch `check_text` und wird als `CheckResult.body` gezeigt, nie als Rohtext (SM-05); Text neben `tool_use` wird nie
gezeigt (L3); Host-Züge tragen einen festen Vorlagentext, der nie `HumanInput` ist (V1, SM-02); der Verlauf besteht
nur aus vollständigen Paaren, ein gescheiterter Aufruf lässt den Anfrageteil ausstehend (Z16).

`events` sind rohe Ereignisse für den Trace-Schreiber (Schritt 8): Schlüssel und Werte ohne Text und ohne Secrets,
kein Schema, kein Schreiben.
"""

from __future__ import annotations

import copy
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from scout_research.agent import texts
from scout_research.agent.budgets import LoopConfig, cost_usd
from scout_research.agent.model_client import (
    ModelCallError,
    ModelClient,
    ModelRequest,
    ModelResponse,
    USABLE_STOP_REASONS,
    PlacementError,
    is_usable,
)
from scout_research.tools.commentary import build_check_session, check_text
from scout_research.tools.handlers import ToolOutcome, coded_outcome, dispatch as default_dispatch, tool_definitions
from scout_research.tools.session import (
    Confirmed,
    CorrectionRequest,
    HostEvent,
    HumanInput,
    Rejected,
    ToolContext,
)


class LoopState(str, Enum):
    IDLE = "idle"
    AWAITING_PEER_CONFIRMATION = "awaiting_peer_confirmation"
    RUNNING = "running"
    CLOSED = "closed"


class LoopBusyError(Exception):
    """Während eines Zugs nimmt der Loop keine Eingabe an (Z20)."""


class SessionClosedError(Exception):
    """Die Sitzung ist geschlossen (Kosten- oder Kontextgrenze, ungültiger Schlüssel)."""


@dataclass(frozen=True)
class Shown:
    """Ein Text für den Nutzer: `text` (geprüfter `body`), `intro` (Einleitung), `notice` (fester Hinweis),
    `commentary` (gerenderter Kommentar aus `ctx.commentaries`)."""

    kind: Literal["text", "intro", "notice", "commentary"]
    text: str


@dataclass
class TurnResult:
    outcome: Literal["done", "failed", "closed"]
    state: LoopState
    shown: list[Shown] = field(default_factory=list)
    reason: str | None = None
    cost_usd: float = 0.0
    calls: int = 0
    commentary_id: str | None = None
    proposal_id: str | None = None


@dataclass(frozen=True)
class Pending:
    """Ausstehender Anfrageteil: Blöcke und Betreiber-Fakten, die noch nicht erfolgreich gesendet wurden (Z16)."""

    blocks: tuple[dict[str, Any], ...] = ()
    facts: tuple[str, ...] = ()


@dataclass(frozen=True)
class UserTurn:
    blocks: list[dict[str, Any]]
    facts: list[str]


def build_user_turn(pending: Pending, lead: HumanInput | HostEvent, language: str = "de") -> UserTurn:
    """Der API-Inhalt eines Zugs (12.14.3). Reine Funktion ohne `ctx`: ausstehende Blöcke zuerst, dann der Text des
    Menschen bzw. der **Vorlagentext** des Host-Zugs; die Fakten stehen nur in der Systemnachricht (SM-02)."""
    templates = texts.HOST_TEMPLATES[language]
    blocks = [copy.deepcopy(b) for b in pending.blocks]
    facts = list(pending.facts)
    if isinstance(lead, HumanInput):
        blocks.append({"type": "text", "text": lead.text})
    elif isinstance(lead, Confirmed):
        blocks.append({"type": "text", "text": templates["confirmed"]})
        facts.append(texts.FACT_CONFIRMED.format(
            peer_set_id=lead.peer_set_id, removed=texts.format_tickers(list(lead.removed)),
            added=texts.format_tickers(list(lead.added))))
    elif isinstance(lead, Rejected):
        blocks.append({"type": "text", "text": templates["rejected"]})
        facts.append(texts.FACT_REJECTED.format(proposal_id=lead.proposal_id))
    elif isinstance(lead, CorrectionRequest):
        blocks.append({"type": "text", "text": templates["correction"]})
        facts.append(texts.correction_fact(lead.problems))
    else:
        raise TypeError(f"unbekannter Auslöser eines Zugs: {type(lead).__name__}")
    return UserTurn(blocks, facts)


@dataclass
class _Turn:
    started: float
    calls: int = 0
    tools: int = 0
    loop_guards: int = 0
    corrections: int = 0
    notices: list[str] = field(default_factory=list)


class AgentLoop:
    def __init__(
        self,
        ctx: ToolContext,
        client: ModelClient,
        system_prompt: str,
        config: LoopConfig | None = None,
        dispatch: Callable[[ToolContext, str, Any], ToolOutcome] = default_dispatch,
        on_status: Callable[[str], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.ctx = ctx
        self.client = client
        self.system_prompt = system_prompt
        self.config = config or LoopConfig(language=ctx.output_language)
        self.config.price  # unbekanntes Modell: sofort laut statt erst beim ersten Aufruf
        self._dispatch = dispatch
        self._on_status = on_status
        self._clock = clock or ctx.clock
        self.tools = tool_definitions()
        self.history: list[dict[str, Any]] = []
        self.pending = Pending()
        self.state = LoopState.IDLE
        self.session_cost = 0.0
        self.events: list[dict[str, Any]] = []
        self._cost_warned = False
        self._closing: str | None = None
        self._interrupted = False

    # --- Host-API ---------------------------------------------------------------------------------------------

    def run_turn(self, msg: HumanInput) -> TurnResult:
        self._require_ready()
        if type(msg) is not HumanInput:
            raise TypeError("run_turn verlangt HumanInput")
        self.ctx.record_user_message(msg)
        return self._run(build_user_turn(self.pending, msg, self.config.language))

    def confirm(self, proposal_id: str, keep_ciks: list[str], add_queries: Sequence[str] = (),
                channel: str = "cli", confirmed_by: str = "human") -> TurnResult:
        """Bestätigt den jüngsten Vorschlag (nur der Mensch über den Host) und startet den Host-Zug (Z2).
        Ungültige Bestätigungen werfen `ConfirmationError` und ändern nichts (Z4)."""
        self._require_ready()
        proposal = self.ctx.proposals.get(proposal_id)
        peer_set = self.ctx.confirm_peer_set(proposal_id, keep_ciks, add_queries, channel, confirmed_by)
        tickers = {p["cik"]: p.get("ticker", "") for p in [*(proposal.peers if proposal else []),
                                                           *(proposal.user_additions if proposal else [])]}
        tickers.update({p.cik: p.ticker for p in peer_set.peers})
        removed = tuple(tickers.get(c, "") for c in peer_set.removed)
        added = tuple(tickers.get(c, "") for c in peer_set.added)
        self.events.append({"event": "gate", "decision": "confirmed", "proposal_id": proposal_id,
                            "peer_set_id": peer_set.id, "channel": channel, "confirmed_by": confirmed_by})
        return self._run(build_user_turn(self.pending, Confirmed(peer_set.id, removed, added), self.config.language))

    def reject(self, proposal_id: str, channel: str = "cli", rejected_by: str = "human") -> TurnResult:
        """Verwirft den jüngsten Vorschlag (V4) und startet den Host-Zug (Z3)."""
        self._require_ready()
        self.ctx.reject_proposal(proposal_id, channel, rejected_by)
        self.events.append({"event": "gate", "decision": "rejected", "proposal_id": proposal_id,
                            "channel": channel, "rejected_by": rejected_by})
        return self._run(build_user_turn(self.pending, Rejected(proposal_id), self.config.language))

    def interrupt(self) -> None:
        """Strg+C: Das laufende Tool endet kooperativ (`Deadline`), restliche Blöcke werden `TURN_ENDED` (Z18)."""
        self._interrupted = True
        self.ctx.deadline.cancel()

    # --- Zug ----------------------------------------------------------------------------------------------------

    def _require_ready(self) -> None:
        if self.state is LoopState.RUNNING:
            raise LoopBusyError("Während eines Zugs wird keine Eingabe angenommen")
        if self.state is LoopState.CLOSED:
            raise SessionClosedError("Die Sitzung ist geschlossen")

    def _rest_state(self) -> LoopState:
        if self._closing:
            return LoopState.CLOSED
        return LoopState.AWAITING_PEER_CONFIRMATION if self.ctx.awaiting_confirmation else LoopState.IDLE

    def _status(self, text: str) -> None:
        if self._on_status is not None:
            self._on_status(text)

    def _run(self, user_turn: UserTurn) -> TurnResult:
        self.state = LoopState.RUNNING
        self._interrupted = False
        turn = _Turn(started=self._clock())
        cost_before = self.session_cost
        result: TurnResult
        try:
            result = self._drive(turn, user_turn)
        except (PlacementError, AssertionError):
            self.state = self._rest_state()
            raise
        except KeyboardInterrupt:
            result = self._finish(turn, "failed", "interrupted", [texts.user_text(self.config.language, "interrupted")])
        except Exception as exc:  # noqa: BLE001 — Ausnahmen im Loop selbst: FAILED, Details nur im Trace
            self.events.append({"event": "loop_error", "error_class": type(exc).__name__,
                                "traceback": self.ctx.redact("".join(traceback.format_exception(exc)))})
            result = self._finish(turn, "failed", "loop_error", [texts.user_text(self.config.language, "internal")])
        self.state = self._rest_state()
        result.state = self.state
        result.cost_usd = self.session_cost - cost_before
        result.calls = turn.calls
        self.events.append({"event": "turn_end", "outcome": result.outcome, "reason": result.reason,
                            "state": self.state.value, "calls": turn.calls, "tools": turn.tools,
                            "session_cost_usd": self.session_cost})
        return result

    def _finish(self, turn: _Turn, outcome: str, reason: str | None, notices: list[str],
                shown: list[Shown] | None = None, commentary_id: str | None = None) -> TurnResult:
        items = list(shown or [])
        items += [Shown("notice", t) for t in notices]
        if self._closing == "cost":
            items.append(Shown("notice", texts.user_text(self.config.language, "cost_closed")))
        elif self._closing == "context":
            items.append(Shown("notice", texts.user_text(self.config.language, "context_closed")))
        items += [Shown("notice", t) for t in turn.notices]
        if self._closing:
            outcome = "closed"
            reason = reason or self._closing
        proposal = self.ctx.open_proposal
        return TurnResult(outcome, self._rest_state(), items, reason,  # type: ignore[arg-type]
                          commentary_id=commentary_id, proposal_id=proposal.id if proposal else None)

    def _drive(self, turn: _Turn, user_turn: UserTurn) -> TurnResult:
        lang = self.config.language
        cfg = self.config
        blocks, facts = list(user_turn.blocks), list(user_turn.facts)
        self.pending = Pending(tuple(blocks), tuple(facts))  # bis zum ersten erfolgreichen Senden ausstehend
        tool_choice = {"type": "auto"}
        while True:
            if self._interrupted:
                return self._finish(turn, "failed", "interrupted", [texts.user_text(lang, "interrupted")])
            if turn.calls >= cfg.max_calls_per_turn:
                return self._finish(turn, "failed", "limit_calls", [texts.user_text(lang, "failed")])
            if self._clock() - turn.started > cfg.turn_seconds:
                return self._finish(turn, "failed", "turn_deadline", [texts.user_text(lang, "failed")])

            sent = self._send(turn, blocks, facts, tool_choice)
            if isinstance(sent, TurnResult):
                return sent
            response, usable = sent
            if not usable:
                return self._unusable(turn, response)

            self._commit(blocks, facts, response)
            tool_uses = [b for b in response.content if b.get("type") == "tool_use"]
            if tool_uses:
                outcome = self._run_tools(turn, response, tool_uses)
                if isinstance(outcome, TurnResult):
                    return outcome
                blocks, facts = outcome, []
                self.pending = Pending(tuple(blocks), ())
                continue
            if response.stop_reason != "end_turn":
                return self._unusable(turn, response, committed=True)

            text = "".join(b.get("text", "") for b in response.content if b.get("type") == "text")
            check = check_text(build_check_session(self.ctx), text, "slot_text")
            self.events.append({"event": "text_check", "where": "end_turn", "problems": [p.code for p in check.problems],
                                "shown": check.ok})
            if check.ok:
                shown = [Shown("text", check.body)] if check.body else []
                return self._finish(turn, "done", None, [], shown)
            if self._closing or turn.corrections >= 1:
                return self._finish(turn, "done", "withheld", [texts.user_text(lang, "withheld")])
            turn.corrections += 1
            problems = tuple({"code": p.code, "kind": p.kind, "start": p.start, "end": p.end, "match": p.match}
                             for p in check.problems)
            follow = build_user_turn(Pending(), CorrectionRequest(problems), lang)
            blocks, facts = follow.blocks, follow.facts
            self.pending = Pending(tuple(blocks), tuple(facts))

    # --- Senden und Verbuchen -----------------------------------------------------------------------------------

    def _request(self, blocks: list[dict[str, Any]], facts: list[str], tool_choice: dict[str, str]) -> ModelRequest:
        messages = copy.deepcopy(self.history)
        messages.append({"role": "user", "content": copy.deepcopy(blocks)})
        if facts:
            messages.append({"role": "system", "content": [{"type": "text", "text": "\n".join(facts)}]})
        return ModelRequest(self.system_prompt, self.tools, messages, tool_choice, self.config.max_tokens,
                            self.config.effort, self.config.model)

    def _send(self, turn: _Turn, blocks, facts, tool_choice) -> TurnResult | tuple[ModelResponse, bool]:
        """Ein Modellaufruf. Gibt `(Antwort, brauchbar)` oder ein fertiges `TurnResult` bei Fehler zurück."""
        lang = self.config.language
        self._status(texts.user_text(lang, "status_working"))
        turn.calls += 1
        try:
            response = self.client.create(self._request(blocks, facts, tool_choice), lambda kind: None)
        except ModelCallError as exc:
            self.events.append({"event": "model_error", "kind": exc.kind, "error_class": exc.error_class,
                                "status_code": exc.status_code, "request_id": exc.request_id})
            if exc.kind == "auth":
                self._closing = "auth"
                return self._finish(turn, "closed", "auth", [texts.user_text(lang, "auth")])
            key = "unavailable" if exc.kind in {"unavailable", "stalled", "deadline"} else "internal"
            return self._finish(turn, "failed", exc.kind, [texts.user_text(lang, key)])
        # Kosten und Kontext **nach jedem Aufruf** aus der gemeldeten Nutzung (V3, L1, L7)
        call_cost = cost_usd(response.usage, self.config.price)
        self.session_cost += call_cost
        self.events.append({"event": "model_call", "stop_reason": response.stop_reason, "request_id": response.request_id,
                            "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens,
                            "cache_read_input_tokens": response.usage.cache_read_input_tokens,
                            "cache_creation_input_tokens": response.usage.cache_creation_input_tokens,
                            "cost_usd": call_cost, "session_cost_usd": self.session_cost, "tool_choice": tool_choice["type"]})
        if self.session_cost >= self.config.cost_limit_usd:
            self._closing = "cost"
        elif response.usage.context_tokens > self.config.context_limit_tokens:
            self._closing = "context"
        if self.session_cost >= self.config.cost_warning_usd and not self._cost_warned:
            self._cost_warned = True
            turn.notices.append(texts.user_text(lang, "cost_warning"))
        return response, is_usable(response)

    def _commit(self, blocks, facts, response: ModelResponse) -> None:
        self.history.append({"role": "user", "content": copy.deepcopy(blocks)})
        if facts:
            self.history.append({"role": "system", "content": [{"type": "text", "text": "\n".join(facts)}]})
        self.history.append({"role": "assistant", "content": copy.deepcopy(response.content)})
        # Ab hier hat jedes tool_use ein Ergebnis: vorbelegt mit TURN_ENDED, Block für Block ersetzt. Eine Ausnahme
        # zwischen Commit und Ergebnis (Strg+C, Fehler im Loop) hinterlässt so keinen ungültigen Verlauf (H1, SG-13).
        ended = coded_outcome("TURN_ENDED")
        uses = [b for b in response.content if b.get("type") == "tool_use"]
        self.pending = Pending(tuple(_result_block(b, ended) for b in uses), ())

    def _unusable(self, turn: _Turn, response: ModelResponse, committed: bool = False) -> TurnResult:
        """`refusal`, `max_tokens` (V5), unerwartete Gründe, leere Antwort (H2): verworfen, nichts ausgeführt
        (Z16)."""
        lang = self.config.language
        reasons = {"refusal": ("refusal", "refusal"), "max_tokens": ("max_tokens", "too_long")}
        empty = response.stop_reason in USABLE_STOP_REASONS and not committed  # Grund brauchbar, aber ohne Inhalt
        reason, key = ("empty_response", "unexpected") if empty else \
            reasons.get(response.stop_reason, ("unexpected_stop", "unexpected"))
        self.events.append({"event": "unusable_response", "stop_reason": response.stop_reason, "empty": empty})
        return self._finish(turn, "failed", reason, [texts.user_text(lang, key)])

    # --- Tools ----------------------------------------------------------------------------------------------------

    def _run_tools(self, turn: _Turn, response: ModelResponse, tool_uses: list[dict[str, Any]]) -> TurnResult | list[dict[str, Any]]:
        lang = self.config.language
        cfg = self.config
        # Text neben tool_use: nie gezeigt, aber geprüft und gezählt (L3)
        beside = "".join(b.get("text", "") for b in response.content if b.get("type") == "text")
        if beside.strip():
            check = check_text(build_check_session(self.ctx), beside, "slot_text")
            self.events.append({"event": "text_check", "where": "beside_tool_use",
                                "problems": [p.code for p in check.problems], "shown": False})

        results = list(self.pending.blocks)  # vorbelegt in _commit (TURN_ENDED)
        ended: str | None = None
        proposed = False
        commentary_id: str | None = None
        for i, block in enumerate(tool_uses):
            name, args = block.get("name", ""), block.get("input", {})
            if self._closing or ended or self._interrupted:
                outcome = coded_outcome("TURN_ENDED")
            elif turn.tools >= cfg.max_tools_per_turn:
                ended = "limit_tools"
                outcome = coded_outcome("TURN_ENDED")
            elif turn.started + cfg.turn_seconds < self._clock():
                ended = "turn_deadline"
                outcome = coded_outcome("TURN_ENDED")
            else:
                self._status(texts.tool_status(lang, name))  # nie der rohe Name aus der Antwort (H3, SG-14)
                turn.tools += 1
                try:
                    outcome = self._dispatch(self.ctx, name, args)
                except KeyboardInterrupt:
                    self._interrupted = True
                    outcome = coded_outcome("TURN_ENDED")
                code = outcome.content.get("error", {}).get("code") if outcome.is_error else None
                self.events.append({"event": "tool", "name": _event_name(self, name), "is_error": outcome.is_error,
                                    "code": code})
                if code == "LOOP_GUARD":
                    turn.loop_guards += 1
                    if turn.loop_guards >= 2:
                        ended = "loop_guard"
                if name == "submit_commentary" and ((not outcome.is_error and outcome.content.get("status") == "accepted")
                                                    or code == "COMMENTARY_WITHHELD"):
                    ended = "commentary"
                    commentary_id = args.get("comps_table_id") if isinstance(args, dict) else None
                if name == "propose_peer_set" and not outcome.is_error:
                    proposed = True
            results[i] = _result_block(block, outcome)
            self.pending = Pending(tuple(results), ())

        self.pending = Pending(tuple(results), ())
        if self._interrupted:
            return self._finish(turn, "failed", "interrupted", [texts.user_text(lang, "interrupted")])
        if self._closing:
            return self._finish(turn, "closed", self._closing, [])
        if ended == "commentary":
            stored = self.ctx.commentaries.get(commentary_id or "")
            shown = [Shown("commentary", stored.rendered)] if stored else []
            return self._finish(turn, "done", "commentary", [], shown, commentary_id)
        if ended in {"limit_tools", "turn_deadline", "loop_guard"}:
            return self._finish(turn, "failed", ended, [texts.user_text(lang, "failed")])
        if proposed:
            return self._intro(turn, results)
        return results

    def _intro(self, turn: _Turn, results: list[dict[str, Any]]) -> TurnResult:
        """Z7–Z10: **ein** Aufruf mit `tool_choice: none`; keine Korrekturrunde (V2); bei Problemen oder Fehlern
        zeigt der Host die feste Einleitung, der Vorschlag gilt weiter."""
        lang = self.config.language
        fixed = Shown("intro", texts.user_text(lang, "intro"))
        if turn.calls >= self.config.max_calls_per_turn:
            return self._finish(turn, "done", "intro_skipped", [], [fixed])
        sent = self._send(turn, results, [], {"type": "none"})
        if isinstance(sent, TurnResult):
            if sent.outcome == "closed":
                return sent
            return self._finish(turn, "done", f"intro_{sent.reason}", [], [fixed])
        response, usable = sent
        if not usable:
            self.events.append({"event": "unusable_response", "stop_reason": response.stop_reason,
                                "empty": response.stop_reason in USABLE_STOP_REASONS})
            return self._finish(turn, "done", "intro_unusable", [], [fixed])
        self._commit(results, [], response)
        tool_uses = [b for b in response.content if b.get("type") == "tool_use"]
        if tool_uses:  # Z10: trotz tool_choice none — der Dispatcher antwortet AWAITING_PEER_CONFIRMATION
            pending = list(self.pending.blocks)  # vorbelegt in _commit (TURN_ENDED, H1)
            for i, block in enumerate(tool_uses):
                turn.tools += 1
                outcome = self._dispatch(self.ctx, block.get("name", ""), block.get("input", {}))
                self.events.append({"event": "tool", "name": _event_name(self, block.get("name", "")),
                                    "is_error": outcome.is_error,
                                    "code": outcome.content.get("error", {}).get("code") if outcome.is_error else None})
                pending[i] = _result_block(block, outcome)
                self.pending = Pending(tuple(pending), ())
            return self._finish(turn, "done", "intro_tool_use", [], [fixed])
        text = "".join(b.get("text", "") for b in response.content if b.get("type") == "text")
        check = check_text(build_check_session(self.ctx), text, "slot_text")
        self.events.append({"event": "text_check", "where": "intro", "problems": [p.code for p in check.problems],
                            "shown": check.ok})
        if check.ok and check.body:
            return self._finish(turn, "done", None, [], [Shown("intro", check.body)])
        return self._finish(turn, "done", "intro_fixed", [], [fixed])


def _KNOWN(loop: AgentLoop) -> set[str]:
    return {t["name"] for t in loop.tools}


def _event_name(loop: AgentLoop, name: Any) -> str:
    """Tool-Name fürs Ereignis: nur bekannte Namen, sonst `<unknown>` (P3; kein Fremdtext im Trace)."""
    return name if isinstance(name, str) and name in _KNOWN(loop) else "<unknown>"


def _result_block(block: dict[str, Any], outcome: ToolOutcome) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": block["id"], "content": outcome.to_json(),
            "is_error": outcome.is_error}
