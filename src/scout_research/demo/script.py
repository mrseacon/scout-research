"""Festes Modellskript für den Demo-Modus (TA-14): eine komplette Analyse als `ScriptedModelClient`.

Jeder Schritt ist eine Funktion, die aus dem Stand der Sitzung (`ToolContext`, bisherige Tool-Aufrufe im Verlauf) die
nächste Antwort ableitet. Das Skript liest nie den Text des Menschen; es erzeugt keine Zahl, sondern setzt Slots, die der
Code beim Prüfen und Rendern füllt (Number-Check). Ablauf: Ziel auflösen, Kandidaten suchen, Peer-Set vorschlagen, Einleitung;
nach der Bestätigung durch den Menschen (Host-API, nie Modelltext) Comps-Tabelle berechnen und Kommentar einreichen."""

from __future__ import annotations

import itertools
from typing import Any

from scout_research.agent.budgets import Usage
from scout_research.agent.model_client import ModelRequest, ModelResponse, ScriptedModelClient
from scout_research.demo.world import TARGET, TARGET_TICKER
from scout_research.tools.session import ToolContext

STEPS = 60
"""Länge des Skripts. Der Host beendet die Demo, wenn für einen Zug (`max_calls_per_turn`) nicht mehr genug Schritte da sind."""
USAGE = Usage(1000, 200)
_ids = itertools.count(1)

TEXT_WAITING = "Ich warte auf deine Entscheidung zum Vorschlag."
TEXT_REJECTED = "Der Vorschlag wurde verworfen. Nenne mir andere Peers oder ein anderes Ziel."
TEXT_DONE = "Die Analyse liegt vor."
TEXT_INTRO = "Hier ist mein Vorschlag für das Peer-Set."


def _tool(name: str, **args: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_demo{next(_ids):04d}", "name": name, "input": args}


def _reply(*blocks: dict[str, Any], stop: str) -> ModelResponse:
    return ModelResponse(list(blocks), stop, USAGE, f"req_demo{next(_ids):04d}")


def _use(name: str, **args: Any) -> ModelResponse:
    return _reply(_tool(name, **args), stop="tool_use")


def _say(text: str) -> ModelResponse:
    return _reply({"type": "text", "text": text}, stop="end_turn")


def _used(request: ModelRequest) -> set[str]:
    """Namen der Tools, die im Verlauf **erfolgreich** liefen (ein Fehler oder `TURN_ENDED` zählt nicht)."""
    names = {b["id"]: b["name"] for m in request.messages if m["role"] == "assistant"
             for b in m["content"] if b.get("type") == "tool_use"}
    return {names[b["tool_use_id"]] for m in request.messages if m["role"] == "user" for b in m["content"]
            if b.get("type") == "tool_result" and not b.get("is_error") and b["tool_use_id"] in names}


def commentary_text(ctx: ToolContext, table_id: str) -> str:
    """Kommentar nur aus Slots und Wörtern; jede Warnung der Stufe `warning` oder `critical` bekommt ihren Marker."""
    required = [w["id"] for w in ctx.tables[table_id].compact["warnings"] if w["severity"] in ("warning", "critical")]
    markers = " ".join(f"[[warn:{i}]]" for i in required)
    return (f"{TARGET_TICKER} liegt beim EV/EBITDA mit [[co:{TARGET_TICKER}:ev_ebitda]] gegenüber einem Peer-Median von "
            f"[[stat:ev_ebitda:median]] bei [[basis:n_peers]] Peers; beim EV/Umsatz sind es [[co:{TARGET_TICKER}:ev_revenue]] "
            f"gegenüber [[stat:ev_revenue:median]]." + (f" Zu beachten: {markers}." if markers else ""))


def step(ctx: ToolContext, request: ModelRequest) -> ModelResponse:
    used = _used(request)
    if "resolve_company" not in used:
        return _use("resolve_company", query=TARGET_TICKER)
    if "find_peer_candidates" not in used:
        return _use("find_peer_candidates", target_cik=TARGET.cik)
    if "propose_peer_set" not in used:
        set_id = next(reversed(ctx.candidate_sets))
        peers = [{"cik": c["cik"], "rationale": "Ähnliches Geschäftsmodell"} for c in ctx.candidate_sets[set_id].candidates]
        return _use("propose_peer_set", candidate_set_id=set_id, peers=peers)
    if request.tool_choice.get("type") == "none":
        return _say(TEXT_INTRO)
    if ctx.peer_sets and "compute_comps_table" not in used:
        return _use("compute_comps_table", peer_set_id=next(reversed(ctx.peer_sets)))
    if ctx.tables and "submit_commentary" not in used:
        table_id = next(reversed(ctx.tables))
        return _use("submit_commentary", comps_table_id=table_id, text=commentary_text(ctx, table_id))
    if ctx.open_proposal is not None:  # der Vorschlag gilt weiter, auch nach einer Chatnachricht (Z2, Z5)
        return _say(TEXT_WAITING)
    proposal = ctx.proposals.get(ctx.current_proposal_id or "")
    if proposal is not None and proposal.rejected:
        return _say(TEXT_REJECTED)
    return _say(TEXT_DONE)


class DemoClient(ScriptedModelClient):
    """Der skriptierte Client: `STEPS` Schritte, jeder entscheidet erst beim Aufruf aus dem Stand der Sitzung."""

    @property
    def remaining(self) -> int:
        """Anzahl der noch vorhandenen Schritte; der Host beendet die Demo, bevor ein Zug sie überziehen könnte (N11)."""
        return len(self._steps)


def build_client(ctx: ToolContext) -> DemoClient:
    return DemoClient([lambda request: step(ctx, request) for _ in range(STEPS)])
