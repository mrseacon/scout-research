"""Runner der Szenarien (Plan 12.12, TA-16a): `python -m eval.run_scenarios`.

Jeder Lauf hat seine eigene Welt, seinen eigenen Loop und seinen eigenen Trace (über `run_case` aus `eval/harness.py`, Modus
eval). Ausgewertet wird nur der Trace (`eval/metrics.py`). Der Standardmodus ist der Trockenlauf: ein skriptiertes Modell
antwortet zahlenfrei mit `end_turn`; er prüft die Pipeline (Welt, Loop, Trace, Auswertung, Bericht) ohne Kosten und liefert
kein Messergebnis. Den Live-Modus gibt es noch nicht (Stufe C): `--live` endet mit festem Text, ohne einen Client zu bauen.

Exit-Codes: 0 fertig (auch bei weichen Verfehlungen), 2 Bedienfehler, 3 harter Ausfall in irgendeinem Lauf, 4 Satz abgebrochen
(`auth`, `not_found`, `budget_day`, `budget_ledger`), 5 mindestens ein ungültiger Lauf (Trace schema-ungültig, fehlend oder
Ausnahme im Lauf: die harten Regeln waren dort nicht prüfbar). Rangfolge bei mehreren: 3 vor 4 vor 5 vor 0.

Modus und Fabriken laufen nicht auseinander: `dry` baut immer den Trockenlauf-Client und ein eigenes Kostenbuch im
Ausgabeordner (ein Test-Hook `test_client_factory` ist im Bericht gekennzeichnet); jeder andere Modus verlangt ausdrücklich
Client- und Budget-Fabrik (Budget aus `SpendBudget.from_settings`, also das Tagesbuch), sonst `ValueError`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval.harness import EvalCase, EvalSessionConfig, run_case
from eval.metrics import ERFUELLT, MANUELL, NA, STATUSES, VERFEHLT, RunVerdict, closing_of, evaluate_run
from eval.scenarios import Scenario, load_all
from eval.worlds import KNOWN_SECRETS, WORLDS
from scout_research.agent.budgets import LoopConfig, Usage
from scout_research.agent.loop import AgentLoop
from scout_research.agent.model_client import ModelClient, ModelResponse, ScriptedModelClient
from scout_research.agent.prompt_loader import load_system_prompt
from scout_research.agent.spend import SpendBudget
from scout_research.agent.trace import current_git_commit
from scout_research.cli import LIVE_NOT_ALLOWED, make_budget

MIN_RUNS = 3
DEFAULT_RUNS = 3
EXIT_OK, EXIT_USAGE, EXIT_HARD, EXIT_ABORTED, EXIT_INVALID = 0, 2, 3, 4, 5
ABORT_CLOSINGS = frozenset(("auth", "not_found", "budget_day", "budget_ledger"))
DRY_TEXT = "Trockenlauf: keine Modellantwort."
DRY_BANNER = "TROCKENLAUF – kein Messergebnis"
MODES = ("dry", "live")
PROMPT_VARIANTS = ("active",)
"""Bis TA-16b nur der aktive Prompt."""

ClientFactory = Callable[[Scenario, int], ModelClient]
BudgetFactory = Callable[[], SpendBudget]


@dataclass
class RunRecord:
    scenario_id: str
    run: int
    status: str
    """`gelaufen`, `ungültig` oder `nicht gelaufen`."""
    reason: str | None = None
    trace_path: str | None = None
    shown_path: str | None = None
    closing: str | None = None
    deviations: list[dict[str, Any]] = field(default_factory=list)
    verdict: RunVerdict | None = None


@dataclass
class SetResult:
    scenarios: list[Scenario]
    runs: int
    mode: str
    prompt_variant: str
    prompt_version: str
    model: str
    git_commit: str | None
    started: str
    records: list[RunRecord] = field(default_factory=list)
    aborted: str | None = None
    test_hook: bool = False
    """Ein Test-Hook hat den Client des Trockenlaufs ersetzt."""
    budget_hook: bool = False
    """Eine übergebene `budget_factory` hat im Modus dry das Kostenbuch im Ausgabeordner ersetzt."""

    @property
    def invalid(self) -> list[RunRecord]:
        return [r for r in self.records if r.status == "ungültig"]

    @property
    def hard_failures(self) -> list[tuple[RunRecord, str]]:
        return [(r, line) for r in self.records if r.verdict is not None for line in r.verdict.hard_failures]


def dry_client(scenario: Scenario, run: int) -> ModelClient:
    """Das Modell des Trockenlaufs: je Zug eine zahlenfreie Antwort mit `end_turn`, kein Tool."""
    steps = [ModelResponse([{"type": "text", "text": DRY_TEXT}], "end_turn", Usage(1000, 50), f"req_dry_{i}")
             for i in range(len(scenario.turns))]
    return ScriptedModelClient(steps)


def _shown_dump(result: Any, ctx: Any) -> tuple[list[dict[str, str]], int, int]:
    """Die gezeigten Texte (geschwärzt), die Zahl der Texte mit wörtlichem Treffer eines bekannten Secrets (hart) und die
    Zahl der Texte, die nur ein allgemeines Muster trifft (`key=…`, `Bearer …`; Kennzahl). Nie ein Wert."""
    shown = [s for r in result.results for s in r.shown]
    literal = pattern = 0
    for s in shown:
        if any(secret in s.text for secret in KNOWN_SECRETS):
            literal += 1
            continue
        pattern += ctx.redact(s.text) != s.text
    return [{"kind": s.kind, "text": ctx.redact(s.text)} for s in shown], literal, pattern


def _run_one(scenario: Scenario, run: int, out_dir: Path, client_factory: ClientFactory,
             budget_factory: BudgetFactory, git_commit: str | None = None) -> RunRecord:
    record = RunRecord(scenario.scenario_id, run, "ungültig")
    prompt = load_system_prompt(scenario.output_language)
    case = EvalCase(scenario.scenario_id, scenario.turns, scenario.confirmation, scenario.path)
    holder: dict[str, Any] = {}

    def make_loop(config: EvalSessionConfig) -> AgentLoop:
        world = WORLDS[scenario.world]()
        ctx = world.context(eval_session=config, output_language=scenario.output_language)
        budget = budget_factory()
        loop = AgentLoop(ctx, client_factory(scenario, run), prompt.text, LoopConfig(language=scenario.output_language),
                         budget=budget)
        holder["ctx"] = ctx
        return loop

    trace_dir = out_dir / "traces" / scenario.scenario_id / f"run{run}"
    try:
        result = run_case(case, make_loop, trace_dir=trace_dir, prompt=prompt, git_commit=git_commit)
    except Exception as exc:  # noqa: BLE001 — ein Programmfehler im Lauf macht ihn ungültig, nicht den Satz
        record.reason = f"Ausnahme im Lauf: {type(exc).__name__}"
        traces = sorted(trace_dir.rglob("*.jsonl"))  # der Schreiber hat `end` geschrieben: den Schließgrund trotzdem lesen
        if traces:
            record.trace_path = str(traces[-1])
            record.closing = closing_of(traces[-1])
        return record
    finally:
        ctx = holder.get("ctx")
        if ctx is not None:
            ctx.edgar.close()
    record.deviations = result.deviations
    record.trace_path = str(result.trace_path) if result.trace_path else None
    shown = out_dir / "shown" / f"{scenario.scenario_id}_run{run}.json"
    shown.parent.mkdir(parents=True, exist_ok=True)
    dump, secret_hits, pattern_hits = _shown_dump(result, holder["ctx"])
    shown.write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
    record.shown_path = str(shown)
    closed = next((r.reason for r in result.results if r.outcome == "closed"), None)
    if record.trace_path is None:
        record.reason = "kein Trace"
        return record
    record.verdict = evaluate_run(record.trace_path, scenario, secret_redactions=secret_hits,
                                    pattern_redactions=pattern_hits)
    record.closing = record.verdict.closing or closed
    if not record.verdict.valid:
        record.reason = record.verdict.error
        return record
    record.status = "gelaufen"
    return record


def run_set(scenarios: list[Scenario], runs: int, out_dir: Path, *, mode: str = "dry", prompt_variant: str = "active",
            client_factory: ClientFactory | None = None, budget_factory: BudgetFactory | None = None,
            test_client_factory: ClientFactory | None = None) -> SetResult:
    """Führt alle Läufe nacheinander aus; bricht den Satz ab, wenn ein Lauf mit `auth`, `not_found`, `budget_day` oder
    `budget_ledger` endet (die übrigen Läufe stehen dann als `nicht gelaufen` im Ergebnis).

    `dry`: Trockenlauf-Client und eigenes Kostenbuch im Ausgabeordner; `client_factory` ist dort verboten, nur der gekennzeichnete
    Test-Hook `test_client_factory` ersetzt den Client. Jeder andere Modus verlangt `client_factory` und `budget_factory`."""
    out_dir = Path(out_dir)
    if mode not in MODES:
        raise ValueError(f"mode muss einer von {MODES} sein, nicht {mode!r}")
    budget_hook = mode == "dry" and budget_factory is not None
    if mode == "dry":
        if client_factory is not None:
            raise ValueError("mode dry: der Client ist der Trockenlauf-Client (nur test_client_factory ersetzt ihn)")
        client_factory = test_client_factory or dry_client
        if budget_factory is None:
            ledger_dir = out_dir / "ledger"
            budget_factory = lambda: make_budget(scripted=True, ledger_dir=ledger_dir)  # noqa: E731 — nie das Tagesbuch
    else:
        if test_client_factory is not None:
            raise ValueError("test_client_factory gilt nur im Modus dry")
        if client_factory is None or budget_factory is None:
            raise ValueError(f"mode {mode}: client_factory und budget_factory (SpendBudget.from_settings) sind Pflicht")
    prompt = load_system_prompt("de")
    result = SetResult(scenarios, runs, mode, prompt_variant, prompt.version, LoopConfig().model,
                       current_git_commit(Path(__file__).resolve().parent), datetime.now(timezone.utc).isoformat(),
                       test_hook=mode == "dry" and test_client_factory is not None, budget_hook=budget_hook)
    for scenario in scenarios:
        for run in range(1, runs + 1):
            if result.aborted is not None:
                result.records.append(RunRecord(scenario.scenario_id, run, "nicht gelaufen",
                                                f"Satz abgebrochen ({result.aborted})"))
                continue
            record = _run_one(scenario, run, out_dir, client_factory, budget_factory, result.git_commit)
            result.records.append(record)
            if record.closing in ABORT_CLOSINGS:
                result.aborted = record.closing
    return result


# --- Bericht --------------------------------------------------------------------------------------------------------


def _valid(records: list[RunRecord]) -> list[RunRecord]:
    return [r for r in records if r.status == "gelaufen" and r.verdict is not None]


def _scenario_summary(scenario: Scenario, records: list[RunRecord]) -> dict[str, Any]:
    valid = _valid(records)
    behaviors: dict[str, Any] = {}
    keys = [*scenario.behaviors] + (["B10-Secret", "B10-Befolgt", "B10-Hinweis"] if "B10" in scenario.behaviors else [])
    for key in keys:
        results = [(r, r.verdict.results[key]) for r in valid if r.verdict is not None]
        counts = {s: sum(1 for _, b in results if b.status == s) for s in STATUSES}
        counted = len(results) - counts[NA]  # n/a-Läufe (nichts zu messen) zählen nicht in n
        measures: dict[str, Any] = {}
        for _, b in results:
            for name, value in b.measures.items():
                measures.setdefault(name, []).append(value)
        behaviors[key] = {
            "k": counts[ERFUELLT], "n": counted, "counts": counts, "measures": measures,
            "manual": [{"run": r.run, "trace": r.trace_path, "shown": r.shown_path} for r, b in results if b.status == MANUELL],
            "failed_runs": [r.run for r, b in results if b.status == VERFEHLT],
        }
    return {
        "behaviors": behaviors, "n_valid": len(valid),
        "invalid": [{"run": r.run, "reason": r.reason} for r in records if r.status == "ungültig"],
        "not_run": [{"run": r.run, "reason": r.reason} for r in records if r.status == "nicht gelaufen"],
    }


def build_report(result: SetResult) -> dict[str, Any]:
    scenarios = {s.scenario_id: _scenario_summary(s, [r for r in result.records if r.scenario_id == s.scenario_id])
                 for s in result.scenarios}
    return {
        "dry_run": result.mode == "dry", "banner": DRY_BANNER if result.mode == "dry" else None,
        "not_checkable_runs": len(result.invalid), "test_hook": result.test_hook, "budget_hook": result.budget_hook,
        "header": {"prompt_version": result.prompt_version, "prompt_variant": result.prompt_variant, "model": result.model,
                   "git_commit": result.git_commit, "mode": result.mode, "started": result.started,
                   "runs_per_scenario": result.runs},
        "aborted": result.aborted,
        "hard_failures": [{"scenario": r.scenario_id, "run": r.run, "trace": r.trace_path, "rule": line}
                          for r, line in result.hard_failures],
        "deviations": [{"scenario": r.scenario_id, "run": r.run, "deviations": r.deviations}
                       for r in result.records if r.deviations],
        "notes": [{"scenario": r.scenario_id, "run": r.run, "note": n}
                  for r in result.records if r.verdict is not None for n in r.verdict.notes],
        "scenarios": scenarios,
    }


def render_markdown(report: dict[str, Any], scenarios: list[Scenario]) -> str:
    head = report["header"]
    lines: list[str] = []
    if report["banner"]:
        lines += [f"# {report['banner']}", ""]
    lines += ["# Bericht der Szenarien", "",
              f"- Prompt: v{head['prompt_version']}, Variante {head['prompt_variant']}",
              f"- Modell: {head['model']}", f"- Git-Commit: {head['git_commit'] or 'unbekannt'}",
              f"- Modus: {head['mode']}" + (" (Test-Hook ersetzt den Client)" if report["test_hook"] else "")
              + (" (Test-Hook (Budget))" if report["budget_hook"] else ""),
              f"- Zeitpunkt: {head['started']}",
              f"- Läufe je Szenario: {head['runs_per_scenario']}", ""]
    if report["not_checkable_runs"]:
        lines += [f"**Harte Regeln in {report['not_checkable_runs']} Läufen nicht prüfbar** (ungültige Läufe, Exit 5).", ""]
    lines += ["## Harte Ausfälle", ""]
    if report["hard_failures"]:
        lines += [f"- {h['scenario']}, Lauf {h['run']}: {h['rule']} (Trace: {h['trace']})" for h in report["hard_failures"]]
    else:
        lines.append("Keine.")
    if report["aborted"]:
        lines += ["", f"## Satz abgebrochen", "", f"Grund: {report['aborted']}. Die übrigen Läufe sind „nicht gelaufen“."]
    if report["deviations"]:
        lines += ["", "## Abweichungen der Harness", ""]
        lines += [f"- {d['scenario']}, Lauf {d['run']}: {json.dumps(d['deviations'], ensure_ascii=False)}" for d in report["deviations"]]
    if report["notes"]:
        lines += ["", "## Hinweise", ""]
        lines += [f"- {n['scenario']}, Lauf {n['run']}: {n['note']}" for n in report["notes"]]
    for scenario in scenarios:
        summary = report["scenarios"][scenario.scenario_id]
        lines += ["", f"## {scenario.scenario_id}", "", scenario.note, "",
                  f"Gültige Läufe: {summary['n_valid']}; ungültig: {len(summary['invalid'])}; "
                  f"nicht gelaufen: {len(summary['not_run'])}"]
        lines += [f"- ungültig, Lauf {i['run']}: {i['reason']}" for i in summary["invalid"]]
        lines += [f"- nicht gelaufen, Lauf {i['run']}: {i['reason']}" for i in summary["not_run"]]
        lines.append("")
        for key, b in summary["behaviors"].items():
            c = b["counts"]
            if b["n"] == 0:
                lines.append(f"- {key}: nicht gemessen (n/a in {c[NA]} Läufen)")
            elif c[MANUELL] and not (c[ERFUELLT] or c[VERFEHLT]):
                lines.append(f"- {key}: {c[MANUELL]} Läufe manuell prüfen")
                lines += [f"    - Lauf {m['run']}: Trace {m['trace']}; gezeigte Texte {m['shown']}" for m in b["manual"]]
            else:
                lines.append(f"- {key}: {b['k']} von {b['n']} erfüllt (verfehlt {c[VERFEHLT]}, manuell {c[MANUELL]}); n/a in {c[NA]} Läufen")
                if c[MANUELL]:
                    lines += [f"    - Lauf {m['run']}: Trace {m['trace']}" for m in b["manual"]]
            for name, values in b["measures"].items():
                total = round(sum(values), 6)
                lines.append(f"    - {name}: Summe {total}, je Lauf {values}")
    return "\n".join(lines) + "\n"


def write_report(result: SetResult, out_dir: Path) -> dict[str, Any]:
    report = build_report(result)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(report, result.scenarios), encoding="utf-8")
    return report


# --- Einstieg -------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m eval.run_scenarios", description="Szenarien der Evaluation (Plan 12.12)")
    parser.add_argument("--scenario", action="append", default=None, metavar="ID", help="Szenario (mehrfach); ohne Angabe alle")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help=f"Läufe je Szenario (mindestens {MIN_RUNS})")
    parser.add_argument("--out", type=Path, default=None, help="Ausgabeordner (Standard runs/eval/<Zeitstempel>/)")
    parser.add_argument("--prompt-variant", default="active", choices=PROMPT_VARIANTS)
    parser.add_argument("--live", action="store_true", help="Live-Modus (nicht freigegeben)")
    return parser


def main(argv: list[str] | None = None, *, test_client_factory: ClientFactory | None = None,
         budget_factory: BudgetFactory | None = None, scenarios_dir: Path | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    if args.live:  # vor allem anderen: kein Client, kein SDK (Stufe C)
        print(LIVE_NOT_ALLOWED, file=sys.stderr)
        return EXIT_USAGE
    if args.runs < MIN_RUNS:
        print(f"Mindestens {MIN_RUNS} Läufe je Szenario (Plan 12.12, Stichprobe).", file=sys.stderr)
        return EXIT_USAGE
    try:
        available = load_all(scenarios_dir) if scenarios_dir is not None else load_all()
    except ValueError as exc:
        print(f"Szenariodatei ungültig: {exc}", file=sys.stderr)
        return EXIT_USAGE
    by_id = {s.scenario_id: s for s in available}
    wanted = args.scenario or list(by_id)
    unknown = [w for w in wanted if w not in by_id]
    if unknown:
        print(f"Unbekannte Szenarien: {', '.join(unknown)}", file=sys.stderr)
        return EXIT_USAGE
    missing = sorted({by_id[w].world for w in wanted} - WORLDS.keys())
    if missing:
        print(f"Unbekannte Welten: {', '.join(missing)}", file=sys.stderr)
        return EXIT_USAGE
    out = args.out or Path("runs") / "eval" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    result = run_set([by_id[w] for w in dict.fromkeys(wanted)], args.runs, out, prompt_variant=args.prompt_variant,
                     test_client_factory=test_client_factory, budget_factory=budget_factory)
    write_report(result, out)
    print(f"Bericht: {out / 'report.md'}")
    if result.hard_failures:
        return EXIT_HARD
    if result.aborted:
        return EXIT_ABORTED
    return EXIT_INVALID if result.invalid else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
