"""Architekturtests per AST (Plan T8, A-H1, A-E1; Register SM-01, SM-02, SM-03, SM-07).

Sie machen einen falschen Aufrufer laut, bevor er Schaden anrichtet: wer das Nutzerprotokoll schreibt, wer ein Gate
bestätigt oder verwirft, und wer den Kanal `eval` benutzt.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "scout_research"
LOOP = Path("agent") / "loop.py"


def _sources() -> dict[Path, ast.Module]:
    return {p.relative_to(SRC): ast.parse(p.read_text(encoding="utf-8")) for p in sorted(SRC.rglob("*.py"))}


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def calls_of(tree: ast.AST, names: set[str]) -> list[tuple[str, int, str | None]]:
    """(Name, Zeile, Name der umgebenden Funktion) für jeden Aufruf eines der Namen."""
    found: list[tuple[str, int, str | None]] = []

    def visit(node: ast.AST, enclosing: str | None) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            enclosing = node.name
        if isinstance(node, ast.Call) and (name := _called_name(node)) in names:
            found.append((name, node.lineno, enclosing))
        for child in ast.iter_child_nodes(node):
            visit(child, enclosing)

    visit(tree, None)
    return found


def imports_of(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def eval_channel_literals(tree: ast.AST) -> list[int]:
    """Zeilen mit dem Schlüsselwortargument `channel="eval"`."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "channel" and isinstance(kw.value, ast.Constant) and kw.value.value == "eval":
                    lines.append(node.lineno)
    return lines


# --- A-H1 ---------------------------------------------------------------------------------------------------------


def test_a_h1_record_user_message_is_called_only_by_run_turn_in_the_loop() -> None:
    offenders = []
    for path, tree in _sources().items():
        for name, line, enclosing in calls_of(tree, {"record_user_message"}):
            if path != LOOP or enclosing != "run_turn":
                offenders.append(f"{path}:{line} ({enclosing})")
    assert offenders == []


def test_a_h1_human_input_is_never_constructed_in_agent_or_tools() -> None:
    offenders = []
    for path, tree in _sources().items():
        if path.parts[0] in {"agent", "tools"}:
            offenders += [f"{path}:{line}" for _, line, _ in calls_of(tree, {"HumanInput"})]
    assert offenders == []


def test_a_h1_gate_decisions_are_made_only_in_the_loop() -> None:
    offenders = []
    for path, tree in _sources().items():
        for name, line, _ in calls_of(tree, {"confirm_peer_set", "reject_proposal"}):
            if path != LOOP:
                offenders.append(f"{path}:{line} ({name})")
    assert offenders == []


def test_a_h1_the_detector_itself_finds_violations() -> None:
    """Der Test prüft sich selbst: ein Aufruf an der falschen Stelle wird gefunden."""
    tree = ast.parse("def other():\n    ctx.record_user_message(HumanInput('x'))\n    ctx.confirm_peer_set('p', [])\n")
    assert [c[0] for c in calls_of(tree, {"record_user_message"})] == ["record_user_message"]
    assert calls_of(tree, {"record_user_message"})[0][2] == "other"
    assert [c[0] for c in calls_of(tree, {"HumanInput"})] == ["HumanInput"]
    assert [c[0] for c in calls_of(tree, {"confirm_peer_set", "reject_proposal"})] == ["confirm_peer_set"]


# --- T8 / A-E1 -----------------------------------------------------------------------------------------------------


def test_t8_agent_imports_nothing_from_data() -> None:
    offenders = []
    for path, tree in _sources().items():
        if path.parts[0] == "agent":
            offenders += [f"{path}: {m}" for m in imports_of(tree) if m.startswith("scout_research.data")]
    assert offenders == []


def test_a_e1_agent_tools_and_cli_import_nothing_from_eval() -> None:
    offenders = []
    for path, tree in _sources().items():
        if path.parts[0] in {"agent", "tools", "cli"}:
            offenders += [f"{path}: {m}" for m in imports_of(tree) if m == "eval" or m.startswith(("eval.", "scout_research.eval"))]
    assert offenders == []


def test_a_e1_the_literal_eval_channel_appears_nowhere_in_the_package() -> None:
    """`channel="eval"` kommt nur in `eval/` (außerhalb von `src/`) vor."""
    offenders = []
    for path, tree in _sources().items():
        offenders += [f"{path}:{line}" for line in eval_channel_literals(tree)]
    assert offenders == []


def test_a_e1_the_detector_finds_the_literal() -> None:
    assert eval_channel_literals(ast.parse("loop.confirm(p, channel='eval')")) == [1]
    assert eval_channel_literals(ast.parse("loop.confirm(p, channel='cli')")) == []
