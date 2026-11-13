"""Agent-Schleife (Plan 12, 12.14; Schritt 7, Phase B).

Eine Sitzung ist ein `AgentLoop`. Der Host ruft `run_turn(HumanInput)`, `confirm(...)`, `reject(...)`; kein anderer
Code darf das Nutzerprotokoll schreiben oder ein Gate entscheiden (A-H1). Die Schleife ruft nur
`tools.handlers.dispatch`, `tools.commentary.check_text`/`build_check_session` und den `ModelClient` — kein Import
aus `data/` (T8).

Was die Schleife durchsetzt, auch wenn das Modell die Regeln bricht (12.11): jede Antwort, die der Nutzer sieht, läuft
durch `check_text` und wird als `CheckResult.body` gezeigt, nie als Rohtext (SM-05); Text neben `tool_use` wird nie
gezeigt (L3); Host-Züge tragen einen festen Vorlagentext, der nie `HumanInput` ist (V1, SM-02); der Verlauf besteht
nur aus vollständigen Paaren, ein gescheiterter Aufruf lässt den Anfrageteil ausstehend (Z16), außer bei `bad_request`/`unprocessable`:
dann fällt der Text des Menschen weg (O-12).

`events` sind rohe Ereignisse für den Trace-Schreiber (`agent/trace.py`, Schritt 8): kein Schema, kein Schreiben.
Seit Schritt 8 tragen sie auch Argumente und Modell-Ergebnisse der Tools sowie die Fundstellen der Textprüfung (bewusste
Änderung von P3). Sie gehen nie an Host oder Nutzer und nie ungeschwärzt in eine Datei: der Schreiber schwärzt jede Zeile.
"""

from __future__ import annotations

import copy
import json
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from scout_research.agent import texts
from scout_research.agent.budgets import LoopConfig, Usage, cost_usd
from scout_research.agent.model_client import (
    ModelCallError,
    ModelClient,
    ModelRequest,
    ModelResponse,
    USABLE_STOP_REASONS,
    PlacementError,
    has_content,
    history_content,
    is_usable,
)
from scout_research.agent.spend import SpendBudget
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
    """Die Sitzung ist geschlossen (Kosten- oder Kontextgrenze, ungültiger Schlüssel, zwei abgelehnte Anfragen in Folge)."""


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
    """Ausstehender Anfrageteil: Blöcke und Betreiber-Fakten, die noch nicht erfolgreich gesendet wurden (Z16).
    `human` sind die Stellen in `blocks`, an denen ein Text des Menschen steht; nach `bad_request` fallen genau diese
    weg (O-12)."""

    blocks: tuple[dict[str, Any], ...] = ()
    facts: tuple[str, ...] = ()
    human: tuple[int, ...] = ()


@dataclass(frozen=True)
class UserTurn:
    blocks: list[dict[str, Any]]
    facts: list[str]
    human: tuple[int, ...] = ()


def build_user_turn(pending: Pending, lead: HumanInput | HostEvent, language: str = "de") -> UserTurn:
    """Der API-Inhalt eines Zugs (12.14.3). Reine Funktion ohne `ctx`: ausstehende Blöcke zuerst, dann der Text des
    Menschen bzw. der **Vorlagentext** des Host-Zugs; die Fakten stehen nur in der Systemnachricht (SM-02)."""
    templates = texts.HOST_TEMPLATES[language]
    blocks = [copy.deepcopy(b) for b in pending.blocks]
    facts = list(pending.facts)
    human = pending.human
    if isinstance(lead, HumanInput):
        human = (*human, len(blocks))
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
    return UserTurn(blocks, facts, human)


@dataclass
class _Turn:
    started: float
    calls: int = 0
    tools: int = 0
    loop_guards: int = 0
    corrections: int = 0
    notices: list[str] = field(default_factory=list)


BOUND_FIXED_TOKENS = 1000
"""Fester Sicherheitsaufschlag auf die Input-Obergrenze (Tool-Use-Systemprompt der API, Rahmen-Token; O-24)."""
BOUND_PER_MESSAGE_TOKENS = 50
"""Aufschlag je Nachricht im Request (Rahmen-Token je Nachricht; O-24)."""


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
        *,
        budget: SpendBudget | None,
    ) -> None:
        self.ctx = ctx
        self.client = client
        self.system_prompt = system_prompt
        self.config = config or LoopConfig(language=ctx.output_language)
        self.config.price  # unbekanntes Modell: sofort laut statt erst beim ersten Aufruf
        self._dispatch = dispatch
        self._on_status = on_status
        self._clock = clock or ctx.clock
        self._bound_ref: tuple[int, list[dict[str, Any]]] | None = None  # (Kontext-Token, Nachrichten) des letzten Aufrufs
        self._budget = budget  # Lauf- und Tagesgrenze (TA-12b); ohne Default: wer keinen Deckel will, übergibt bewusst `None`
        self.tools = tool_definitions()
        self.history: list[dict[str, Any]] = []
        self.pending = Pending()
        self.state = LoopState.IDLE
        self.session_cost = 0.0
        self.events: list[dict[str, Any]] = []
        self._cost_warned = False
        self._closing: str | None = None
        self._interrupted = False
        self._bad_requests = 0  # abgelehnte Anfragen in Folge, ohne erfolgreichen Aufruf dazwischen (M1)

    # --- Host-API ---------------------------------------------------------------------------------------------

    def run_turn(self, msg: HumanInput) -> TurnResult:
        self._require_ready()
        if type(msg) is not HumanInput:
            raise TypeError("run_turn verlangt HumanInput")
        if not isinstance(msg.text, str):
            raise TypeError("run_turn verlangt Text als str")
        if not msg.text.strip():
            # Die API lehnt einen leeren Text-Block ab (400). Hier, vor Protokoll und Senden, bleibt alles unverändert (M1).
            raise ValueError("run_turn verlangt einen nichtleeren Text")
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
                            "peer_set_id": peer_set.id, "channel": channel, "confirmed_by": confirmed_by,
                            "log_pos": self._log_pos()})
        return self._run(build_user_turn(self.pending, Confirmed(peer_set.id, removed, added), self.config.language))

    def reject(self, proposal_id: str, channel: str = "cli", rejected_by: str = "human") -> TurnResult:
        """Verwirft den jüngsten Vorschlag (V4) und startet den Host-Zug (Z3)."""
        self._require_ready()
        self.ctx.reject_proposal(proposal_id, channel, rejected_by)
        self.events.append({"event": "gate", "decision": "rejected", "proposal_id": proposal_id,
                            "channel": channel, "rejected_by": rejected_by, "log_pos": self._log_pos()})
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
        """Ein Zug. Nach dem Verlassen, egal womit, ist `state` nie `RUNNING` (TA-14): Ein zweites Strg+C während der
        Behandlung des ersten (Abbruchzweig, `_finish`, Anhängen von `turn_end`) steigt zum Host auf, der Loop bleibt
        benutzbar. Verlauf und `pending` sind zu jedem Zeitpunkt gültig (`_commit`, H1), das Ereignis `turn_end` trägt
        dann den Grund `interrupted_twice` (bzw. `aborted` bei einer anderen Ausnahme)."""
        mark = len(self.events)  # vor dem Zustandswechsel berechnet: nach `RUNNING` läuft nur noch, was abgesichert ist (N1)
        turn = _Turn(started=self._clock())
        try:
            self.state = LoopState.RUNNING
            self._interrupted = False
            return self._run_guarded(turn, user_turn)
        except BaseException as exc:
            self.state = self._rest_state()  # zuerst: ein weiteres Strg+C darf den Zustand nicht mehr verändern
            if not any(e.get("event") == "turn_end" for e in self.events[mark:]):
                reason = "interrupted_twice" if isinstance(exc, KeyboardInterrupt) else "aborted"
                try:
                    self.events.append(self._turn_end_event("failed", reason, turn.calls, turn.tools))
                except BaseException:  # noqa: BLE001, S110 — der Abbruch selbst steigt auf
                    pass
            raise
        finally:
            if self.state is LoopState.RUNNING:  # Absicherung: kein Pfad lässt den Loop im Zug stehen
                self.state = self._rest_state()

    def _run_guarded(self, turn: _Turn, user_turn: UserTurn) -> TurnResult:
        cost_before = self.session_cost
        result: TurnResult
        try:
            result = self._drive(turn, user_turn)
        except PlacementError as exc:
            if self._closing != "placement":  # Fehler des Verlaufs vom Client o. ä.: Programmfehler, steigt zum Host auf
                self.state = self._rest_state()
                raise
            # Die Invariante nach `_commit` ist verletzt (N3): Die Sitzung schließt, der Host sieht den festen Text
            self.events.append({"event": "loop_error", "error_class": type(exc).__name__,
                                "traceback": self.ctx.redact("".join(traceback.format_exception(exc)))})
            result = self._finish(turn, "closed", "placement", [texts.user_text(self.config.language, "internal")])
        except AssertionError:
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
        self.events.append(self._turn_end_event(result.outcome, result.reason, turn.calls, turn.tools))
        return result

    def _turn_end_event(self, outcome: str, reason: str | None, calls: int, tools: int) -> dict[str, Any]:
        return {"event": "turn_end", "outcome": outcome, "reason": reason, "state": self.state.value, "calls": calls,
                "tools": tools, "closing": self._closing, "session_cost_usd": self.session_cost}

    def _finish(self, turn: _Turn, outcome: str, reason: str | None, notices: list[str],
                shown: list[Shown] | None = None, commentary_id: str | None = None) -> TurnResult:
        items = list(shown or [])
        items += [Shown("notice", t) for t in notices]
        if self._closing == "cost":
            items.append(Shown("notice", texts.user_text(self.config.language, "cost_closed")))
        elif self._closing == "context":
            items.append(Shown("notice", texts.user_text(self.config.language, "context_closed")))
        elif self._closing in {"budget_run", "budget_day", "budget_ledger"}:
            items.append(Shown("notice", texts.user_text(self.config.language, f"{self._closing}_closed")))
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
        self.pending = Pending(tuple(blocks), tuple(facts), user_turn.human)  # bis zum ersten erfolgreichen Senden ausstehend
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
            # `tool_use` ohne Block heißt hier: Eine angekündigte Aktion fehlt. Den Text als Antwort zu zeigen, stellte eine
            # nicht erfolgte Aktion als erledigt dar, darum `unexpected_stop`. In `_intro` ist der Vorschlag schon
            # deterministisch gezeigt und die Einleitung nur Beiwerk, dort zeigt der Host den geprüften Text.
            if response.stop_reason != "end_turn":
                return self._unusable(turn, response, committed=True)

            text = "".join(b.get("text", "") for b in response.content if b.get("type") == "text")
            check = check_text(build_check_session(self.ctx), text, "slot_text")
            action = "shown" if check.ok else \
                "withheld" if (self._closing or turn.corrections >= 1) else "correction"
            self.events.append({"event": "text_check", "where": "end_turn", "problems": [p.code for p in check.problems],
                                "shown": check.ok, "findings": _findings(check.problems), "action": action,
                                "extracted": check.extracted})
            if action == "shown":
                shown = [Shown("text", check.body)] if check.body else []
                return self._finish(turn, "done", None, [], shown)
            if action == "withheld":
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
        request = self._request(blocks, facts, tool_choice)
        upper_tokens = self._upper_input_tokens(request)
        if self._budget is not None:  # Vorprüfung im schlechtesten Fall: bei erreichter Grenze gar kein Aufruf (O-24)
            reason = self._budget.exhausted(self._upper_usd(request, upper_tokens))
            if reason is not None:
                self._budget_stop(reason)
                return self._finish(turn, "closed", reason, [])
        self._status(texts.user_text(lang, "status_working"))
        turn.calls += 1
        started = self._clock()
        try:
            response = self.client.create(request, lambda kind: None)
        except ModelCallError as exc:
            # Ein gescheiterter Aufruf ist teilweise bezahlt: die bekannte Nutzung wird verbucht und die Grenzen werden
            # wie nach einem Erfolg geprüft (M2, 12.8)
            usage = exc.usage
            call_cost = cost_usd(usage, self.config.price) if usage is not None else 0.0
            self.session_cost += call_cost
            self.events.append({"event": "model_error", "kind": exc.kind, "error_class": exc.error_class,
                                "status_code": exc.status_code, "request_id": exc.request_id,
                                "input_tokens": usage.input_tokens if usage else None,
                                "output_tokens": usage.output_tokens if usage else None,
                                "cache_read_input_tokens": usage.cache_read_input_tokens if usage else None,
                                "cache_creation_input_tokens": usage.cache_creation_input_tokens if usage else None,
                                "cost_usd": call_cost, "usage_estimated": exc.usage_estimated,
                                "session_cost_usd": self.session_cost})
            try:  # nach dem Ereignis; Grenzen gelten auch, wenn Strg+C das Buchen unterbricht (fail closed)
                if self._budget is not None:
                    self._budget.charge(call_cost)
            finally:
                if usage is not None:
                    self._check_limits(turn, usage)
                self._check_budget()
            if exc.kind == "auth":
                self._closing = "auth"
                return self._finish(turn, "closed", "auth", [texts.user_text(lang, "auth")])
            if exc.kind == "not_found":  # Modell oder Endpunkt unbekannt: Konfiguration, jeder weitere Aufruf scheitert gleich
                self._closing = "not_found"
                return self._finish(turn, "closed", "not_found", [texts.user_text(lang, "not_found")])
            if exc.kind == "interrupted":
                self._interrupted = True
                return self._finish(turn, "failed", "interrupted", [texts.user_text(lang, "interrupted")])
            if exc.kind in {"bad_request", "unprocessable"}:  # 422 teilt den Pfad von 400 (TA-13, O-25)
                # Vermutlich ist der Text des Menschen die Ursache; ihn erneut zu senden, scheitert erneut (M1, O-12,
                # Abweichung von Z16). Eine zweite Ablehnung in Folge liegt am festen Verlauf: Die Sitzung schließt.
                self._discard_human_text()
                self._bad_requests += 1
                if self._bad_requests >= 2 and not self._closing:
                    self._closing = "bad_request"
            key = "unavailable" if exc.kind in {"unavailable", "stalled", "deadline"} else "internal"
            return self._finish(turn, "failed", exc.kind, [texts.user_text(lang, key)])
        self._bad_requests = 0  # ein erfolgreicher Aufruf setzt den Zähler zurück (M1)
        # Kosten und Kontext **nach jedem Aufruf** aus der gemeldeten Nutzung (V3, L1, L7)
        call_cost = cost_usd(response.usage, self.config.price)
        self.session_cost += call_cost
        self._update_bound_ref(response.usage, request.messages)
        self.events.append({"event": "model_call", "stop_reason": response.stop_reason, "request_id": response.request_id,
                            "model": response.model,
                            "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens,
                            "cache_read_input_tokens": response.usage.cache_read_input_tokens,
                            "cache_creation_input_tokens": response.usage.cache_creation_input_tokens,
                            "cost_usd": call_cost, "session_cost_usd": self.session_cost, "tool_choice": tool_choice["type"],
                            "messages": len(request.messages), "latency_s": self._clock() - started,
                            "upper_input_tokens": upper_tokens})
        try:  # nach dem Ereignis; Grenzen gelten auch, wenn Strg+C das Buchen unterbricht (fail closed)
            if self._budget is not None:
                self._budget.charge(call_cost)
        finally:
            self._check_limits(turn, response.usage)
            self._check_budget()
        return response, is_usable(response)

    def _discard_human_text(self) -> None:
        """Nach `bad_request` (M1, O-12): Es fällt nur der Text des Menschen weg. `tool_result`-Blöcke, Vorlagentext und
        Fakten eines Host-Zugs bleiben unverändert; war der Zug ein Host-Zug, bleibt `pending` ganz erhalten."""
        blocks = tuple(b for i, b in enumerate(self.pending.blocks) if i not in self.pending.human)
        self.pending = Pending(blocks, self.pending.facts)

    def _check_limits(self, turn: _Turn, usage: Usage) -> None:
        """Sitzungsgrenzen nach einem Aufruf, gelungen oder gescheitert (V3, L1, L7, M2): Kostengrenze, Kontextgrenze,
        Kostenwarnung einmal."""
        if self.session_cost >= self.config.cost_limit_usd:
            self._closing = "cost"
        elif usage.context_tokens > self.config.context_limit_tokens:
            self._closing = "context"
        if self.session_cost >= self.config.cost_warning_usd and not self._cost_warned:
            self._cost_warned = True
            turn.notices.append(texts.user_text(self.config.language, "cost_warning"))

    def _upper_input_tokens(self, request: ModelRequest) -> int:
        """Obergrenze der Input-Tokens des nächsten Aufrufs (O-24, schlechtester Fall).

        Annahme: 1 Token <= 1 Byte UTF-8, plus Aufschlag (`BOUND_FIXED_TOKENS` fest, `BOUND_PER_MESSAGE_TOKENS` je Nachricht).
        Das ist plausibel, aber erst im Live-Lauf belegt (das Trace-Feld `upper_input_tokens` im `model_call` erlaubt den
        Vergleich mit der tatsächlichen Nutzung). Es gibt keinen Deckel bei der eigenen Kontextgrenze: Sie schließt erst
        nach dem Aufruf, ein größerer Request würde gesendet und bezahlt.

        Mit gesicherter Nutzung des letzten Aufrufs (Kontext-Token aus `usage`) und einem Verlauf, der seither nur
        angehängt wurde: diese Token plus Bytes der hinzugekommenen Nachrichten. Sonst (erster Aufruf, letzter Aufruf
        ohne Nutzung, Verlauf verändert): Bytes von system + tools + messages."""
        messages = request.messages
        overhead = BOUND_FIXED_TOKENS + BOUND_PER_MESSAGE_TOKENS * len(messages)
        base = self._ref_base(messages)
        if base is not None:
            tokens, start = base
            return tokens + _json_bytes(messages[start:]) + overhead
        return _json_bytes({"system": request.system, "tools": request.tools, "messages": messages}) + overhead

    def _ref_base(self, messages: list[dict[str, Any]]) -> tuple[int, int] | None:
        """(Kontext-Token des letzten Aufrufs, Index der ersten neuen Nachricht), wenn `messages` den alten Request als
        Präfix enthält. Die trailing `system`-Nachricht (Fakten) des alten Requests bleibt außen vor: `_commit` übernimmt
        die Fakten in den Verlauf, die Ausnahme macht die Präfixprüfung nur nachsichtiger; alte Fakten werden dann ggf.
        doppelt gezählt (Überschätzung, nie Unterschätzung)."""
        ref = self._bound_ref
        if ref is None:
            return None
        tokens, old = ref
        if old and old[-1].get("role") == "system":
            old = old[:-1]
        if len(old) <= len(messages) and messages[:len(old)] == old:
            return tokens, len(old)
        return None

    def _update_bound_ref(self, usage: Usage, messages: list[dict[str, Any]]) -> None:
        """Referenz für die nächste Schranke. Ohne gemeldete Kontext-Token (<= 0) keine Referenz (volle Bytezählung).
        Gilt das Präfix, ist die Referenz monoton (`max`): Der neue Request enthält den alten, eine kleinere gemeldete
        Zahl wäre eine Unstimmigkeit der Nutzungsangabe und würde die Schranke senken."""
        tokens = usage.context_tokens
        if tokens <= 0:
            self._bound_ref = None
            return
        base = self._ref_base(messages)
        if base is not None:
            tokens = max(tokens, base[0])
        self._bound_ref = (tokens, messages)

    def _upper_usd(self, request: ModelRequest, input_tokens: int) -> float:
        """Obergrenze der Kosten des nächsten Aufrufs aus der Preistabelle der Konfiguration: Input mit dem teuersten
        Satz (Input, Cache-Read, Cache-Write), Output mit `max_tokens` zum Output-Preis."""
        price = self.config.price
        input_rate = max(price.input, price.cache_read, price.cache_write)
        return (input_tokens * input_rate + request.max_tokens * price.output) / 1_000_000

    def _next_call_upper_usd(self, request: ModelRequest) -> float:
        return self._upper_usd(request, self._upper_input_tokens(request))

    @property
    def budget(self) -> SpendBudget | None:
        return self._budget

    def _check_budget(self) -> None:
        """Lauf- und Tagesgrenze nach einem Aufruf, gelungen oder gescheitert (TA-12b); die Blöcke dieser Antwort werden
        dann wie bei der Kostengrenze nicht ausgeführt."""
        reason = self._budget.exhausted() if self._budget is not None else None
        if reason is not None:
            self._budget_stop(reason)

    def _budget_stop(self, reason: str) -> None:
        assert self._budget is not None
        if self._closing in (None, "cost", "context"):  # die längerfristige Sperre gewinnt vor Kosten- und Kontextgrenze
            self._closing = reason
        self.events.append({"event": "budget_stop", "reason": reason, "run_spent_usd": self._budget.run_spent,
                            "day_total_usd": self._budget.day_total()})

    def _commit(self, blocks, facts, response: ModelResponse) -> None:
        # Ab hier hat jedes tool_use ein Ergebnis: vorbelegt mit TURN_ENDED, Block für Block ersetzt. Eine Ausnahme
        # zwischen Commit und Ergebnis (Strg+C, Fehler im Loop) hinterlässt so keinen ungültigen Verlauf (H1, SG-13).
        # Alles wird vorher gebaut; Verlauf und `pending` wechseln direkt nacheinander (kein deepcopy dazwischen).
        entries = [{"role": "user", "content": copy.deepcopy(blocks)}]
        if facts:
            entries.append({"role": "system", "content": [{"type": "text", "text": "\n".join(facts)}]})
        entries.append({"role": "assistant", "content": history_content(response.content)})
        ended = coded_outcome("TURN_ENDED")
        uses = [b for b in response.content if b.get("type") == "tool_use"]
        prefilled = Pending(tuple(_result_block(b, ended) for b in uses), ())
        self.history.extend(entries)
        self.pending = prefilled
        try:
            check_prefilled(self.history, self.pending)  # ausdrücklich, nicht nur implizit (TA-14, Teil B)
        except PlacementError:
            self._closing = "placement"  # der Verlauf ist nicht mehr verlässlich: die Sitzung schließt (N3)
            raise

    def _unusable(self, turn: _Turn, response: ModelResponse, committed: bool = False) -> TurnResult:
        """`refusal`, `max_tokens` (V5), unerwartete Gründe, leere Antwort (H2): verworfen, nichts ausgeführt
        (Z16)."""
        lang = self.config.language
        reasons = {"refusal": ("refusal", "refusal"), "max_tokens": ("max_tokens", "too_long")}
        empty = response.stop_reason in USABLE_STOP_REASONS and not has_content(response.content)
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
                                "problems": [p.code for p in check.problems], "shown": False,
                                "findings": _findings(check.problems), "action": "not_shown",
                                "extracted": check.extracted})

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
                mark = self.ctx.request_log.mark()
                started = self._clock()
                try:
                    outcome = self._dispatch(self.ctx, name, args)
                except KeyboardInterrupt:
                    self._interrupted = True
                    outcome = coded_outcome("TURN_ENDED")
                code = outcome.content.get("error", {}).get("code") if outcome.is_error else None
                self.events.append(self._tool_event(name, block, args, outcome, code, self._clock() - started, mark))
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

    def _log_pos(self) -> dict[str, int]:
        """Länge von `gate_log` und `commentary_log` in diesem Augenblick: Der Trace-Schreiber ordnet ihre Einträge damit
        an die richtige Stelle zwischen den Ereignissen (Schritt 8, M1). Nur Positionen, kein Inhalt; er schreibt sie nicht."""
        return {"gate": len(self.ctx.gate_log), "commentary": len(self.ctx.commentary_log)}

    def _tool_event(self, name: Any, block: dict[str, Any], args: Any, outcome: ToolOutcome, code: str | None,
                    duration: float, mark: int) -> dict[str, Any]:
        """Ereignis eines ausgeführten Tools: Argumente, Ergebnis für das Modell und Detail nur für den Trace (P3 geändert;
        der Schreiber schwärzt). `upstream` und `cache` sind die Einträge des Request-Logs seit `mark` (nur Host, Pfad,
        Status bzw. Namespace und Treffer)."""
        entries = self.ctx.request_log.since(mark)
        upstream = [{k: v for k, v in e.items() if k != "kind"} for e in entries if e.get("kind") == "http"]
        cache = [{k: v for k, v in e.items() if k != "kind"} for e in entries if e.get("kind") == "cache"]
        return {"event": "tool", "name": _event_name(self, name), "is_error": outcome.is_error, "code": code,
                "tool_use_id": block.get("id"), "input": copy.deepcopy(args), "result": copy.deepcopy(outcome.content),
                "detail": copy.deepcopy(outcome.trace), "duration_s": duration, "upstream": upstream, "cache": cache,
                "log_pos": self._log_pos()}

    def _intro(self, turn: _Turn, results: list[dict[str, Any]]) -> TurnResult:
        """Z7–Z10: **ein** Aufruf mit `tool_choice: none`; keine Korrekturrunde (V2); bei Problemen oder Fehlern
        zeigt der Host die feste Einleitung, der Vorschlag gilt weiter."""
        lang = self.config.language
        fixed = Shown("intro", texts.user_text(lang, "intro"))
        if turn.calls >= self.config.max_calls_per_turn:
            return self._finish(turn, "done", "intro_skipped", [], [fixed])
        sent = self._send(turn, results, [], {"type": "none"})
        if isinstance(sent, TurnResult):
            if sent.outcome == "closed" or sent.reason == "interrupted":  # Strg+C bricht den Zug ab (P12, Z17, O-04)
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
                mark = self.ctx.request_log.mark()
                started = self._clock()
                outcome = self._dispatch(self.ctx, block.get("name", ""), block.get("input", {}))
                code = outcome.content.get("error", {}).get("code") if outcome.is_error else None
                self.events.append(self._tool_event(block.get("name", ""), block, block.get("input", {}), outcome, code,
                                                    self._clock() - started, mark))
                pending[i] = _result_block(block, outcome)
                self.pending = Pending(tuple(pending), ())
            return self._finish(turn, "done", "intro_tool_use", [], [fixed])
        # Jeder Text, der hier ankommt, läuft durch check_text — auch bei `stop_reason` `tool_use` ohne Block (SM-05)
        text = "".join(b.get("text", "") for b in response.content if b.get("type") == "text")
        check = check_text(build_check_session(self.ctx), text, "slot_text")
        shown = check.body if check.ok else None
        self.events.append({"event": "text_check", "where": "intro", "problems": [p.code for p in check.problems],
                            "shown": bool(shown), "findings": _findings(check.problems),
                            "action": "shown" if shown else "intro_fixed", "extracted": check.extracted})
        if shown:
            return self._finish(turn, "done", None, [], [Shown("intro", shown)])
        return self._finish(turn, "done", "intro_fixed", [], [fixed])


def check_prefilled(history: list[dict[str, Any]], pending: Pending) -> None:
    """Invariante nach `_commit` (H1, SG-13): Jede `tool_use`-ID der letzten Assistant-Nachricht hat genau ein Ergebnis in
    `pending`, in derselben Reihenfolge, und `pending` enthält sonst nichts. Wirft `PlacementError` (Programmfehler)."""
    last = history[-1] if history else None
    if last is None or last.get("role") != "assistant":
        raise PlacementError("nach _commit steht kein assistant-Turn am Ende des Verlaufs")
    ids = [b.get("id") for b in last["content"] if b.get("type") == "tool_use"]
    got = [b.get("tool_use_id") for b in pending.blocks if b.get("type") == "tool_result"]
    if len(set(ids)) != len(ids) or got != ids or len(pending.blocks) != len(ids):
        raise PlacementError("nach _commit hat nicht jedes tool_use genau ein vorbelegtes Ergebnis")


def _findings(problems: Sequence[Any]) -> list[dict[str, Any]]:
    """Fundstellen der Textprüfung fürs Ereignis: Code, Art, Bereich im Rohtext und der gefundene Ausschnitt."""
    return [{"code": p.code, "kind": p.kind, "start": p.start, "end": p.end, "match": p.match} for p in problems]


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _KNOWN(loop: AgentLoop) -> set[str]:
    return {t["name"] for t in loop.tools}


def _event_name(loop: AgentLoop, name: Any) -> str:
    """Tool-Name fürs Ereignis: nur bekannte Namen, sonst `<unknown>` (P3; kein Fremdtext im Trace)."""
    return name if isinstance(name, str) and name in _KNOWN(loop) else "<unknown>"


def _result_block(block: dict[str, Any], outcome: ToolOutcome) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": block["id"], "content": outcome.to_json(),
            "is_error": outcome.is_error}
