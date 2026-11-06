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
        if path.parts[0] in {"agent", "tools", "cli", "cli.py", "demo"}:
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


EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"
EVAL_SESSION_CONFIG = "EvalSessionConfig"


def mentions_of(tree: ast.AST, name: str) -> list[int]:
    """Zeilen, in denen `name` im Code vorkommt: als Name, Attribut, Klasse, Import oder Textliteral (Annotation als
    String). Docstrings zählen nicht (sie beschreiben, sie benutzen nichts)."""
    docstrings = {
        id(node.value)  # Docstrings und Attribut-Docstrings: Textzeilen, die als eigene Anweisung stehen
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    }
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            lines.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == name:
            lines.append(node.lineno)
        elif isinstance(node, ast.ClassDef) and node.name == name:
            lines.append(node.lineno)
        elif isinstance(node, ast.alias) and name in {node.name, node.asname}:
            lines.append(getattr(node, "lineno", 0))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings \
                and name in node.value:
            lines.append(node.lineno)
    return lines


def test_a_e1_eval_session_config_is_defined_only_in_eval_and_never_mentioned_in_src() -> None:
    """`EvalSessionConfig` ist nur in `eval/` konstruierbar (SM-07): Im Paket steht sie nirgends im Code, `ToolContext`
    kennt sie nur als `object | None`; definiert ist sie genau einmal, in `eval/harness.py`."""
    offenders = [f"{path}:{line}" for path, tree in _sources().items() for line in mentions_of(tree, EVAL_SESSION_CONFIG)]
    assert offenders == []
    definitions = [
        f"{path.relative_to(EVAL_DIR)}:{node.lineno}"
        for path in sorted(EVAL_DIR.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef) and node.name == EVAL_SESSION_CONFIG
    ]
    assert len(definitions) == 1 and definitions[0].startswith("harness.py:")


def test_a_e1_the_mention_detector_finds_code_but_not_docstrings() -> None:
    assert mentions_of(ast.parse("x = EvalSessionConfig('a', 'b')"), EVAL_SESSION_CONFIG) == [1]
    assert mentions_of(ast.parse("from eval.harness import EvalSessionConfig"), EVAL_SESSION_CONFIG) == [1]
    assert mentions_of(ast.parse("def f(c: 'EvalSessionConfig'): ..."), EVAL_SESSION_CONFIG) == [1]
    assert mentions_of(ast.parse("def f():\n    \"\"\"EvalSessionConfig\"\"\"\n"), EVAL_SESSION_CONFIG) == []
    assert mentions_of(ast.parse("x = 1\n\"\"\"EvalSessionConfig\"\"\"\n"), EVAL_SESSION_CONFIG) == []


GATE_CALLS = {"confirm", "reject", "confirm_peer_set", "reject_proposal"}


def eval_session_arguments(tree: ast.AST) -> list[int]:
    """Zeilen mit dem Schlüsselwortargument `eval_session=<etwas anderes als die Konstante None>`."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "eval_session" and not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
    ]


def eval_channel_positionals(tree: ast.AST) -> list[int]:
    """Zeilen, in denen einem Aufruf von `confirm`/`reject`/`confirm_peer_set`/`reject_proposal` das Literal `"eval"`
    positional übergeben wird (`channel` ist dort das vierte bzw. zweite Argument)."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _called_name(node) in GATE_CALLS
        for arg in node.args
        if isinstance(arg, ast.Constant) and arg.value == "eval"
    ]


def test_a_e1_nothing_in_the_package_passes_an_eval_session_other_than_none() -> None:
    """Der Kanal `eval` wird nur durch eine Sitzung mit `EvalSessionConfig` frei (SM-07): Im Paket gibt kein Aufruf
    ein `eval_session=` weiter, das nicht die Konstante `None` ist."""
    offenders = [f"{path}:{line}" for path, tree in _sources().items() for line in eval_session_arguments(tree)]
    assert offenders == []


def test_a_e1_no_gate_call_in_the_package_passes_the_eval_channel_positionally() -> None:
    offenders = [f"{path}:{line}" for path, tree in _sources().items() for line in eval_channel_positionals(tree)]
    assert offenders == []


def test_a_e1_the_eval_session_detector_finds_a_value_but_not_none() -> None:
    assert eval_session_arguments(ast.parse("ToolContext(edgar, market, eval_session=object())")) == [1]
    assert eval_session_arguments(ast.parse("ToolContext(edgar, market, eval_session=config)")) == [1]
    assert eval_session_arguments(ast.parse("ToolContext(edgar, market, eval_session=None)")) == []
    assert eval_session_arguments(ast.parse("ToolContext(edgar, market)")) == []


def test_a_e1_the_positional_channel_detector_finds_the_literal() -> None:
    assert eval_channel_positionals(ast.parse("loop.confirm(p, [], [], 'eval')")) == [1]
    assert eval_channel_positionals(ast.parse("loop.reject(p, 'eval')")) == [1]
    assert eval_channel_positionals(ast.parse("ctx.confirm_peer_set(p, [], (), 'eval')")) == [1]
    assert eval_channel_positionals(ast.parse("ctx.reject_proposal(p, 'eval')")) == [1]
    assert eval_channel_positionals(ast.parse("loop.confirm(p, [], [], 'cli')")) == []
    assert eval_channel_positionals(ast.parse("other.send(p, 'eval')")) == []


# --- Trace: wer die Loop-Ereignisse liest (Schritt 8, M2) ------------------------------------------------------------

EVENT_READERS = {Path("agent") / "loop.py", Path("agent") / "trace.py"}


def events_accesses(tree: ast.AST) -> list[tuple[int, ast.expr]]:
    """(Zeile, Objekt) für jeden Zugriff auf ein Attribut `events`."""
    return [(n.lineno, n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "events"]


def test_only_loop_and_trace_writer_touch_the_loop_events() -> None:
    """Die rohen Ereignisse tragen Argumente und Modell-Ergebnisse (P3 geändert): Außer dem Loop (schreibt) und dem
    Trace-Schreiber (liest, schwärzt) greift kein Modul unter `src/scout_research` darauf zu. Einzige bekannte
    Ausnahme: `Streamed.events` des Test-Doubles in `agent/model_client.py` (`step.events`, ein anderes Objekt)."""
    offenders = []
    for path, tree in _sources().items():
        if path in EVENT_READERS:
            continue
        for line, owner in events_accesses(tree):
            if path == Path("agent") / "model_client.py" and isinstance(owner, ast.Name) and owner.id == "step":
                continue
            offenders.append(f"{path}:{line}")
    assert offenders == []


def test_the_events_detector_itself_finds_violations() -> None:
    tree = ast.parse("def f(loop):\n    return loop.events\n\ndef g(ctx):\n    ctx.loop.events.append(1)\n")
    assert [line for line, _ in events_accesses(tree)] == [2, 5]
