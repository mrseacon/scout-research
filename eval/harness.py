"""Eval-Harness: Fälle aus `eval/cases/*.yaml` gegen einen `AgentLoop` (Plan 12.14.6, V6; Register SM-07).

Die Harness ist der **einzige** Ort, der in der Evaluation bestätigt oder verwirft: über `loop.confirm(...,
channel="eval", confirmed_by="eval:<case_id>")` bzw. `loop.reject(...)`, in einer Sitzung mit `EvalSessionConfig`. Die
Entscheidung ist eine Funktion aus Falldatei und Code-Speicher (`ctx.proposals`); die Harness liest weder Modelltext noch
`TurnResult.shown`. Ein Ticker der Fallliste, der nicht vorgeschlagen wurde, wird als Abweichung protokolliert und nie
ergänzt. `agent/`, `tools/` und `cli/` importieren dieses Modul nicht (A-E1); `EvalSessionConfig` steht nur hier.

    Falldatei: eval/cases/README.md
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from scout_research.agent.loop import AgentLoop, LoopState, TurnResult
from scout_research.agent.prompt_loader import SystemPrompt
from scout_research.agent.trace import TraceWriter
from scout_research.tools.resolve import normalize_ticker
from scout_research.tools.session import ConfirmationError, HumanInput, Proposal

CASES_DIR = Path(__file__).parent / "cases"
ACTIONS = ("confirm", "reject", "none")
_CASE_KEYS = {"case_id", "turns", "confirmation"}
_CONFIRMATION_KEYS = {"action", "keep", "add"}
_CASE_ID = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class EvalSessionConfig:
    """Kennzeichnet eine Sitzung als Evaluation. Wer sie an `ToolContext(eval_session=...)` übergibt, schaltet den Kanal
    `eval` frei (`confirm_peer_set`/`reject_proposal` lehnen ihn sonst ab, E-03). Nur hier definiert (A-E1)."""

    case_id: str
    case_path: str


@dataclass(frozen=True)
class Confirmation:
    """Bestätigungsregel eines Falls (V6). Standard: `confirm` mit allen vorgeschlagenen Peers."""

    action: Literal["confirm", "reject", "none"] = "confirm"
    keep: Literal["all_proposed"] | tuple[str, ...] = "all_proposed"
    add: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    turns: tuple[str, ...]
    """Nachrichten des (simulierten) Nutzers, der Reihe nach."""
    confirmation: Confirmation = Confirmation()
    path: str = ""


@dataclass(frozen=True)
class Decision:
    """Die Entscheidung der Harness: `keep` und `add` leer bei `reject`."""

    action: Literal["confirm", "reject"]
    proposal_id: str
    keep: tuple[str, ...] = ()
    """CIKs, wie an `loop.confirm` übergeben."""
    add: tuple[str, ...] = ()


@dataclass
class CaseResult:
    case_id: str
    decisions: list[Decision] = field(default_factory=list)
    deviations: list[dict[str, Any]] = field(default_factory=list)
    """Abweichungen vom Fall: `{"kind": "not_proposed", "ticker": ...}`, `{"kind": "empty_keep"}` (nichts zu bestätigen,
    es wurde nicht bestätigt) und `{"kind": "confirmation_error", "code": ...}` (der Loop lehnte die Entscheidung ab,
    keine Entscheidung getroffen) und `{"kind": "closed_skipped", "turns": k}` (die Sitzung war geschlossen, k Züge
    des Falls wurden ausgelassen)."""
    results: list[TurnResult] = field(default_factory=list)
    table_ids: list[str] = field(default_factory=list)
    trace_path: Path | None = None


# --- Falldatei ----------------------------------------------------------------------------------------------------


def string_list(value: Any, name: str, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValueError(f"{name}: erwartet wird eine {'' if allow_empty else 'nicht leere '}Liste von Texten")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{name}: jeder Eintrag muss ein nicht leerer Text sein")
    return tuple(item.strip() if name != "turns" else item for item in value)


def parse_confirmation(raw: Any) -> Confirmation:
    if not isinstance(raw, dict):
        raise ValueError("confirmation: erwartet wird ein Objekt mit action, keep, add")
    unknown = sorted(str(k) for k in raw.keys() - _CONFIRMATION_KEYS)
    if unknown:
        raise ValueError(f"confirmation: unbekannte Schlüssel {unknown}")
    action = raw.get("action", "confirm")
    if action not in ACTIONS:
        raise ValueError(f"confirmation.action muss einer von {ACTIONS} sein, nicht {action!r}")
    keep = raw.get("keep", "all_proposed")
    if keep != "all_proposed":
        if isinstance(keep, str):
            raise ValueError("confirmation.keep: erwartet wird 'all_proposed' oder eine Liste von Tickern")
        keep = string_list(keep, "confirmation.keep", allow_empty=False)
    add = string_list(raw.get("add", []), "confirmation.add", allow_empty=True)
    if action != "confirm" and keep != "all_proposed":
        raise ValueError(f"confirmation.keep wirkt nur bei action: confirm, nicht bei {action!r}")
    if action != "confirm" and add:
        raise ValueError(f"confirmation.add wirkt nur bei action: confirm, nicht bei {action!r}")
    return Confirmation(action, keep, add)


def load_case(path: str | Path) -> EvalCase:
    """Lädt und validiert eine Falldatei streng: unbekannte Schlüssel, eine falsche `action`, leere `turns` und eine
    `case_id`, die nicht dem Dateinamen entspricht, werfen `ValueError` mit dem Feldnamen."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: erwartet wird ein Objekt mit case_id, turns, confirmation")
    unknown = sorted(str(k) for k in data.keys() - _CASE_KEYS)
    if unknown:
        raise ValueError(f"{path.name}: unbekannte Schlüssel {unknown}")
    case_id = data.get("case_id")
    if not isinstance(case_id, str) or _CASE_ID.fullmatch(case_id) is None:
        raise ValueError("case_id: erwartet wird ein Text aus Buchstaben, Ziffern, Unterstrich und Bindestrich")
    if case_id != path.stem:
        raise ValueError(f"case_id {case_id!r} entspricht nicht dem Dateinamen {path.name!r}")
    turns = string_list(data.get("turns"), "turns", allow_empty=False)
    confirmation = parse_confirmation(data["confirmation"]) if "confirmation" in data else Confirmation()
    return EvalCase(case_id, turns, confirmation, str(path))


# --- Lauf ---------------------------------------------------------------------------------------------------------


def _decide(proposal: Proposal, rule: Confirmation) -> tuple[list[str], list[dict[str, Any]]]:
    """`keep` (CIKs) und Abweichungen, deterministisch aus dem Vorschlag im Speicher des Codes. Tickers werden auf beiden
    Seiten mit `normalize_ticker` verglichen (`BRK.B` und `BRK-B` sind derselbe Ticker)."""
    offered = {p["cik"]: p for p in [*proposal.peers, *proposal.user_additions]}
    if rule.keep == "all_proposed":
        return list(offered), []
    wanted = {normalize_ticker(t) for t in rule.keep}
    keep = [cik for cik, p in offered.items() if normalize_ticker(str(p.get("ticker", ""))) in wanted]
    proposed = {normalize_ticker(str(p.get("ticker", ""))) for p in offered.values()}
    missing = {normalize_ticker(t): t for t in rule.keep if normalize_ticker(t) not in proposed}
    return keep, [{"kind": "not_proposed", "ticker": ticker} for ticker in missing.values()]


def run_case(
    case: EvalCase,
    make_loop: Callable[[EvalSessionConfig], AgentLoop],
    *,
    trace_dir: Path | None = None,
    prompt: SystemPrompt | None = None,
    git_commit: str | None = None,
) -> CaseResult:
    """Spielt einen Fall durch. `make_loop` bekommt die `EvalSessionConfig` und baut `ToolContext(...,
    eval_session=config)` und den Loop. Mit `trace_dir` entsteht ein Trace (`prompt` ist dann Pflicht und muss der Text
    sein, den der Loop sendet; `git_commit` kommt in den Trace-Kopf); `flush()` läuft nach jedem Host-Aufruf."""
    if trace_dir is not None and prompt is None:
        raise ValueError("prompt: mit trace_dir ist der Systemprompt Pflicht")
    config = EvalSessionConfig(case.case_id, case.path)
    loop = make_loop(config)
    outcome = CaseResult(case.case_id)
    writer = TraceWriter(loop, prompt, base_dir=trace_dir, git_commit=git_commit) if trace_dir is not None and prompt is not None else None
    with writer if writer is not None else nullcontext():
        for index, text in enumerate(case.turns):
            outcome.results.append(loop.run_turn(HumanInput(text)))
            _flush(writer)
            if loop.state is not LoopState.CLOSED and not outcome.decisions and case.confirmation.action != "none" \
                    and outcome.results[-1].state is LoopState.AWAITING_PEER_CONFIRMATION:
                _decide_gate(case, loop, outcome)
                _flush(writer)
            if loop.state is LoopState.CLOSED:  # TA-16a: eine geschlossene Sitzung nimmt keine Züge mehr an
                skipped = len(case.turns) - index - 1
                if skipped:
                    outcome.deviations.append({"kind": "closed_skipped", "turns": skipped})
                break
        outcome.table_ids = [handle for handle in loop.ctx.tables if handle.startswith("ct_")]
    outcome.trace_path = writer.path if writer is not None else None
    return outcome


def _flush(writer: TraceWriter | None) -> None:
    if writer is not None:
        writer.flush()


def _decide_gate(case: EvalCase, loop: AgentLoop, outcome: CaseResult) -> None:
    ctx = loop.ctx
    proposal = ctx.proposals.get(ctx.current_proposal_id or "")
    if proposal is None:
        raise RuntimeError("Der Loop wartet auf eine Bestätigung, aber ctx hat keinen aktuellen Vorschlag")
    rule, who = case.confirmation, f"eval:{case.case_id}"
    try:
        if rule.action == "reject":
            outcome.results.append(loop.reject(proposal.id, channel="eval", rejected_by=who))
            outcome.decisions.append(Decision("reject", proposal.id))
            return
        keep, deviations = _decide(proposal, rule)
        outcome.deviations += deviations
        if not keep and not rule.add:  # nichts zu bestätigen: gar nicht erst versuchen (O-11)
            outcome.deviations.append({"kind": "empty_keep"})
            return
        outcome.results.append(loop.confirm(proposal.id, keep, list(rule.add), channel="eval", confirmed_by=who))
        outcome.decisions.append(Decision("confirm", proposal.id, tuple(keep), rule.add))
    except ConfirmationError as exc:  # der Loop hat nichts geändert (Z4): protokollieren, keine Entscheidung (O-11)
        outcome.deviations.append({"kind": "confirmation_error", "code": exc.reason})
