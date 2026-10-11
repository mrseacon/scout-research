"""Harness für die Schleifen-Szenarien (Plan 12.11): Skript-Antworten, Test-Welt, Invarianten. Kein Testmodul."""

from __future__ import annotations

import itertools
from typing import Any, Callable

from scout_research.agent.budgets import LoopConfig, Usage
from scout_research.agent.loop import AgentLoop, TurnResult
from scout_research.agent.model_client import (
    ManualClock,
    ModelRequest,
    ModelResponse,
    ScriptedModelClient,
    Step,
    check_placement,
)
from scout_research.tools.session import HumanInput
from tests.unit.tool_world import AAPL_CIK, MSFT_CIK, NVDA_CIK, World

SYSTEM = "SYSTEMPROMPT (Testdouble)"
_ids = itertools.count(1)

COMMENTARY = ("NVDA liegt beim EV/EBITDA mit [[co:NVDA:ev_ebitda]] über dem Median [[stat:ev_ebitda:median]] "
              "([[warn:W1]]).")


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def tool_block(name: str, **args: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_{next(_ids):04d}", "name": name, "input": args}


def reply(*blocks: dict[str, Any], stop: str = "end_turn", usage: Usage | None = None,
          request_id: str | None = None) -> ModelResponse:
    return ModelResponse(list(blocks), stop, usage or Usage(1000, 200), request_id or f"req_{next(_ids):04d}")


def say(text: str, **kw: Any) -> ModelResponse:
    return reply(text_block(text), **kw)


def use(*calls: tuple[str, dict[str, Any]], text: str | None = None, **kw: Any) -> ModelResponse:
    blocks = [text_block(text)] if text else []
    blocks += [tool_block(name, **args) for name, args in calls]
    kw.setdefault("stop", "tool_use")
    return reply(*blocks, **kw)


class Run:
    """Eine Sitzung gegen die Test-Welt mit skriptiertem Modell."""

    def __init__(self, world: World, steps: list[Step], config: LoopConfig | None = None,
                 language: str = "de", dispatch: Callable | None = None, **ctx_kwargs: Any) -> None:
        self.clock = ManualClock()
        ctx_kwargs.setdefault("output_language", language)
        self.ctx = world.context(clock=self.clock, **ctx_kwargs)
        self.client = ScriptedModelClient(steps, clock=self.clock)
        self.status: list[str] = []
        kwargs: dict[str, Any] = {}
        if dispatch is not None:
            kwargs["dispatch"] = dispatch
        self.loop = AgentLoop(self.ctx, self.client, SYSTEM, config or LoopConfig(language=language),
                              on_status=self.status.append, clock=self.clock, **kwargs)

    # --- Aktionen ----------------------------------------------------------------------------------------------

    def say(self, text: str) -> TurnResult:
        return self.loop.run_turn(HumanInput(text))

    def confirm(self, keep: tuple[str, ...] = (AAPL_CIK, MSFT_CIK), **kw: Any) -> TurnResult:
        return self.loop.confirm(self.proposal_id, list(keep), **kw)

    def reject(self, **kw: Any) -> TurnResult:
        return self.loop.reject(self.proposal_id, **kw)

    # --- Zugriff auf Sitzungsdaten für Skript-Callables -------------------------------------------------------------

    @property
    def candidate_set_id(self) -> str:
        return next(reversed(self.ctx.candidate_sets))

    @property
    def proposal_id(self) -> str:
        return self.ctx.current_proposal_id  # type: ignore[return-value]

    @property
    def peer_set_id(self) -> str:
        return next(reversed(self.ctx.peer_sets))

    @property
    def table_id(self) -> str:
        return next(reversed(self.ctx.tables))

    def propose(self, *ciks: str) -> Callable[[ModelRequest], ModelResponse]:
        ciks = ciks or (AAPL_CIK, MSFT_CIK)
        return lambda req: use(("propose_peer_set", {
            "candidate_set_id": self.candidate_set_id,
            "peers": [{"cik": c, "rationale": "Ähnliches Geschäftsmodell"} for c in ciks]}))

    def compute(self) -> Callable[[ModelRequest], ModelResponse]:
        return lambda req: use(("compute_comps_table", {"peer_set_id": self.peer_set_id}))

    def submit(self, text: str = COMMENTARY) -> Callable[[ModelRequest], ModelResponse]:
        return lambda req: use(("submit_commentary", {"comps_table_id": self.table_id, "text": text}))

    # --- Invarianten --------------------------------------------------------------------------------------------

    def assert_invariants(self) -> None:
        """Verlauf gültig, auch mit dem ausstehenden Teil; jede Anfrage erweitert die vorige (der Client prüft das
        bei jedem Aufruf); System und Tools sind in allen Anfragen identisch."""
        history = self.loop.history
        probe = list(history) + [{"role": "user", "content": [*map(dict, self.loop.pending.blocks), text_block("x")]}]
        check_placement(probe)
        assert len({(r.system, str(r.tools)) for r in self.client.requests}) <= 1

    def last_user_text(self, request_index: int = -1) -> str:
        request = self.client.requests[request_index]
        for message in reversed(request.messages):
            if message["role"] == "user":
                return "".join(b.get("text", "") for b in message["content"] if b.get("type") == "text")
        return ""

    def tool_results(self, request_index: int = -1) -> list[dict[str, Any]]:
        """Die `tool_result`-Blöcke des letzten `user`-Turns der Anfrage."""
        import json

        out = []
        for message in reversed(self.client.requests[request_index].messages):
            if message["role"] == "user":
                for b in message["content"]:
                    if b.get("type") == "tool_result":
                        out.append(json.loads(b["content"]))
                return out
        return out


def shown_texts(result: TurnResult) -> list[str]:
    return [s.text for s in result.shown]


def codes_in(content: dict[str, Any]) -> str | None:
    return content.get("error", {}).get("code")
