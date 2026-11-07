"""Runner der Szenarien (TA-16a): Trockenlauf, Bericht „k von n“, Abbruch, Exit-Codes, Harness nach `CLOSED`, `budget=` in `eval/`."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

import eval.run_scenarios as runner
from eval.harness import EvalCase, run_case
from eval.run_scenarios import main
from scout_research.agent.loop import AgentLoop, LoopState
from scout_research.agent.model_client import ModelCallError, ScriptedModelClient
from scout_research.cli import LIVE_NOT_ALLOWED
from tests.unit.tool_world import standard_world

TWO = ["--scenario", "b01_comps_nvda", "--scenario", "b03_ueberspringen"]
EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"


@pytest.fixture(autouse=True)
def _no_sdk_import():
    """Kein Runner-Test darf `anthropic` importieren (vor und nach jedem Test verglichen)."""
    before = "anthropic" in sys.modules
    yield
    assert before or "anthropic" not in sys.modules


def _report(out: Path) -> tuple[str, dict]:
    return (out / "report.md").read_text(encoding="utf-8"), json.loads((out / "report.json").read_text(encoding="utf-8"))


def test_dry_run_two_scenarios_three_runs(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    assert main([*TWO, "--runs", "3", "--out", str(out)], env={}) == 0
    text, data = _report(out)
    assert "TROCKENLAUF – kein Messergebnis" in text and data["dry_run"] is True
    assert "B1: 3 von 3 erfüllt" in text and "B3: 3 von 3 erfüllt" in text
    assert "%" not in text and "%" not in json.dumps(data, ensure_ascii=False)
    assert data["header"]["prompt_version"] and data["header"]["model"] and data["header"]["mode"] == "dry"
    assert data["hard_failures"] == [] and data["aborted"] is None
    assert len(list((out / "traces").rglob("*.jsonl"))) == 6


def test_fewer_than_three_runs_is_a_usage_error(tmp_path: Path, capsys) -> None:
    assert main([*TWO, "--runs", "2", "--out", str(tmp_path / "o")], env={}) == 2
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize("argv", [["--scenario", "gibt_es_nicht"], ["--prompt-variant", "gibt_es_nicht"], ["--bogus"]])
def test_usage_errors_exit_2(tmp_path: Path, argv: list[str], capsys) -> None:
    assert main([*argv, "--out", str(tmp_path / "o")], env={}, isatty=lambda: False) == 2  # nie echte Umgebung oder stdin


def test_live_is_refused_before_any_client_is_built(tmp_path: Path, capsys) -> None:
    calls: list[int] = []
    assert main([*TWO, "--live", "--out", str(tmp_path / "o")], test_client_factory=lambda s, r: calls.append(1), env={}) == 2
    assert LIVE_NOT_ALLOWED in capsys.readouterr().err and calls == [] and not (tmp_path / "o").exists()


def test_auth_aborts_the_set_and_the_rest_is_not_run(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    code = main([*TWO, "--out", str(out)],
                test_client_factory=lambda s, r: ScriptedModelClient([ModelCallError("auth", status_code=401)]), env={})
    assert code == 4
    text, data = _report(out)
    assert data["aborted"] == "auth" and "Satz abgebrochen" in text
    summary = data["scenarios"]["b01_comps_nvda"]
    assert len(summary["not_run"]) == 2 and len(data["scenarios"]["b03_ueberspringen"]["not_run"]) == 3
    assert len(list((out / "traces").rglob("*.jsonl"))) == 1


def test_budget_run_ends_only_that_run(tmp_path: Path) -> None:
    from eval.scenarios import load_all
    from scout_research.agent.spend import SpendBudget, SpendLedger

    scenarios = [s for s in load_all() if s.scenario_id == "b03_ueberspringen"]
    result = runner.run_set(scenarios, 3, tmp_path, budget_factory=lambda: SpendBudget(0.0001, 3.0, SpendLedger(tmp_path / "l.json")))
    assert result.aborted is None and len(result.records) == 3
    assert all(r.status != "nicht gelaufen" for r in result.records)


def test_a_hard_failure_gives_exit_3(tmp_path: Path, monkeypatch) -> None:
    real = runner.evaluate_run

    def failing(path, scenario, **kw):
        verdict = real(path, scenario, **kw)
        verdict.hard_failures.append("B3: erfunden für den Test")
        return verdict

    monkeypatch.setattr(runner, "evaluate_run", failing)
    out = tmp_path / "out"
    assert main(["--scenario", "b03_ueberspringen", "--out", str(out)], env={}) == 3
    text, data = _report(out)
    assert "erfunden für den Test" in text and len(data["hard_failures"]) == 3 and data["hard_failures"][0]["trace"]


def test_a_crash_in_a_run_makes_it_invalid_not_passed(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main(["--scenario", "b03_ueberspringen", "--out", str(out)], test_client_factory=lambda s, r: ScriptedModelClient([]), env={}) == 5
    text, data = _report(out)
    summary = data["scenarios"]["b03_ueberspringen"]
    assert summary["n_valid"] == 0 and len(summary["invalid"]) == 3
    assert "Harte Regeln in 3 Läufen nicht prüfbar" in text and data["not_checkable_runs"] == 3


# --- Harness: nach CLOSED keine weiteren Züge -----------------------------------------------------------------------


def test_harness_skips_the_remaining_turns_after_closed(tmp_path: Path) -> None:
    from scout_research.agent.spend import SpendBudget, SpendLedger

    case = EvalCase("c1", ("eins", "zwei", "drei"))
    client = ScriptedModelClient([ModelCallError("auth", status_code=401)])
    seen: list[AgentLoop] = []

    def make_loop(config):
        ctx = standard_world().context(eval_session=config)
        loop = AgentLoop(ctx, client, "SYSTEM", budget=SpendBudget(1.0, 3.0, SpendLedger(tmp_path / "l.json")))
        seen.append(loop)
        return loop

    result = run_case(case, make_loop)
    assert seen[0].state is LoopState.CLOSED and len(result.results) == 1 and len(client.requests) == 1
    assert {"kind": "closed_skipped", "turns": 2} in result.deviations


# --- Architektur: budget= in eval/ ------------------------------------------------------------------------------------


def _agent_loop_calls_without_budget(tree: ast.AST) -> list[int]:
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id == "AgentLoop")
                                           or (isinstance(node.func, ast.Attribute) and node.func.attr == "AgentLoop")):
            kw = next((k for k in node.keywords if k.arg == "budget"), None)
            if kw is None or (isinstance(kw.value, ast.Constant) and kw.value.value is None):
                bad.append(node.lineno)
    return bad


def test_every_agent_loop_call_in_eval_passes_a_budget() -> None:
    offenders = [f"{p.name}:{line}" for p in sorted(EVAL_DIR.rglob("*.py"))
                 for line in _agent_loop_calls_without_budget(ast.parse(p.read_text(encoding="utf-8")))]
    assert offenders == []
    assert any("AgentLoop(" in p.read_text(encoding="utf-8") for p in EVAL_DIR.glob("*.py"))


def test_the_budget_detector_finds_missing_and_none() -> None:
    assert _agent_loop_calls_without_budget(ast.parse("AgentLoop(c, m, s)")) == [1]
    assert _agent_loop_calls_without_budget(ast.parse("AgentLoop(c, m, s, budget=None)")) == [1]
    assert _agent_loop_calls_without_budget(ast.parse("AgentLoop(c, m, s, budget=b)")) == []


# --- Korrekturrunde: Exit-Codes, Modus, Ende-zu-Ende, Abbruch, Commit ---------------------------------------------------


def _script(*steps_factory):
    """Client-Fabrik: je Lauf ein frisches Skript (die Schritte entstehen neu, `ScriptedModelClient` verbraucht sie)."""
    return lambda s, r: ScriptedModelClient([step() if callable(step) else step for step in steps_factory])


def _e2e(tmp_path: Path, factory) -> tuple[int, dict]:
    out = tmp_path / "out"
    code = main(["--scenario", "b08_kaufen", "--out", str(out)], test_client_factory=factory, env={})
    return code, _report(out)[1]


def test_e2e_compute_before_confirmation_is_a_hard_failure(tmp_path: Path) -> None:
    from tests.unit.loop_harness import say, use

    factory = _script(lambda: use(("compute_comps_table", {"peer_set_id": "ps_0001"})), lambda: say("Fertig."))
    code, data = _e2e(tmp_path, factory)
    assert code == 3 and len(data["hard_failures"]) == 3
    assert all("B3" in h["rule"] for h in data["hard_failures"])


def test_e2e_a_confirm_peer_set_tool_call_is_a_hard_failure(tmp_path: Path) -> None:
    from tests.unit.loop_harness import say, use

    factory = _script(lambda: use(("confirm_peer_set", {"proposal_id": "p1"})), lambda: say("Fertig."))
    code, data = _e2e(tmp_path, factory)
    assert code == 3 and "UNKNOWN_TOOL" in data["hard_failures"][0]["rule"]


def test_e2e_a_number_free_normal_case_is_exit_0(tmp_path: Path) -> None:
    from tests.unit.loop_harness import say

    code, data = _e2e(tmp_path, _script(lambda: say("Dazu gebe ich keine Einschätzung ab.")))
    assert code == 0 and data["hard_failures"] == [] and data["not_checkable_runs"] == 0


def test_e2e_a_redacted_shown_text_is_a_hard_failure(tmp_path: Path) -> None:
    from tests.unit.loop_harness import say
    from tests.unit.tool_world import UA

    code, data = _e2e(tmp_path, _script(lambda: say(f"Mein Kontakt: {UA}")))
    assert code == 3 and any("B10-Secret" in h["rule"] for h in data["hard_failures"])
    assert UA not in json.dumps(data, ensure_ascii=False)


@pytest.mark.parametrize("text", ["Das sind bearer bonds, keine Zahlen.", "Setze key=value im Formular."])
def test_e2e_a_general_pattern_hit_is_only_a_figure(tmp_path: Path, text: str) -> None:
    from tests.unit.loop_harness import say

    out = tmp_path / "out"
    assert main(["--scenario", "b10_api_key", "--out", str(out)], test_client_factory=_script(lambda: say(text)), env={}) == 0
    data = _report(out)[1]
    secret = data["scenarios"]["b10_api_key"]["behaviors"]["B10-Secret"]
    assert data["hard_failures"] == [] and secret["counts"]["erfüllt"] == 3 and sum(secret["measures"]["pattern_redactions"]) == 3


def test_non_dry_modes_need_both_factories_and_dry_rejects_a_client_factory(tmp_path: Path) -> None:
    from eval.scenarios import load_all

    sc = load_all()[:1]
    with pytest.raises(ValueError):
        runner.run_set(sc, 3, tmp_path, mode="live")
    with pytest.raises(ValueError):
        runner.run_set(sc, 3, tmp_path, mode="live", client_factory=runner.dry_client)
    with pytest.raises(ValueError):
        runner.run_set(sc, 3, tmp_path, mode="dry", client_factory=runner.dry_client)
    with pytest.raises(ValueError):
        runner.run_set(sc, 3, tmp_path, mode="live", client_factory=runner.dry_client, budget_factory=lambda: None,
                       test_client_factory=runner.dry_client)


def test_the_dry_banner_only_in_dry_mode_and_the_test_hook_is_marked(tmp_path: Path) -> None:
    from eval.scenarios import load_all

    sc = [s for s in load_all() if s.scenario_id == "b08_kaufen"]
    live = runner.run_set(sc, 3, tmp_path / "a", mode="live", client_factory=runner.dry_client,
                          budget_factory=lambda: runner.make_budget(scripted=True, ledger_dir=tmp_path / "l"))
    assert runner.build_report(live)["banner"] is None and "TROCKENLAUF" not in runner.render_markdown(runner.build_report(live), sc)
    hooked = runner.run_set(sc, 3, tmp_path / "b", test_client_factory=runner.dry_client)
    report = runner.build_report(hooked)
    assert report["banner"] and report["test_hook"] is True and "Test-Hook" in runner.render_markdown(report, sc)
    assert report["budget_hook"] is False
    budgeted = runner.run_set(sc, 3, tmp_path / "c", budget_factory=lambda: runner.make_budget(scripted=True, ledger_dir=tmp_path / "m"))
    marked = runner.render_markdown(runner.build_report(budgeted), sc)
    assert runner.build_report(budgeted)["budget_hook"] is True and "Test-Hook (Budget)" in marked
    with pytest.raises(ValueError):
        runner.run_set(sc, 3, tmp_path / "d", mode="x")


def _ledger_budget(tmp_path: Path, kind: str):
    from scout_research.agent.spend import SpendBudget, SpendLedger

    if kind == "budget_day":
        return lambda: SpendBudget(1.0, 0.0001, SpendLedger(tmp_path / "day.json"))
    broken = tmp_path / "broken.json"
    broken.write_text("kein json", encoding="utf-8")
    return lambda: SpendBudget(1.0, 3.0, SpendLedger(broken))


@pytest.mark.parametrize("kind", ["auth", "not_found", "budget_day", "budget_ledger"])
def test_every_abort_closing_stops_the_set_with_exit_4(tmp_path: Path, kind: str) -> None:
    out = tmp_path / "out"
    kwargs: dict = {}
    if kind in ("auth", "not_found"):
        kwargs["test_client_factory"] = lambda s, r: ScriptedModelClient([ModelCallError(kind)])
    else:
        kwargs["budget_factory"] = _ledger_budget(tmp_path, kind)
    assert main(["--scenario", "b08_kaufen", "--scenario", "b09_dcf", "--out", str(out)], **kwargs, env={}) == 4
    data = _report(out)[1]
    assert data["aborted"] == kind
    assert len(data["scenarios"]["b08_kaufen"]["not_run"]) == 2 and len(data["scenarios"]["b09_dcf"]["not_run"]) == 3


def test_the_closing_is_read_from_the_trace_of_a_run_with_an_exception(tmp_path: Path, monkeypatch) -> None:
    real = runner.run_case

    def crashing(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("nach dem Lauf")

    monkeypatch.setattr(runner, "run_case", crashing)
    out = tmp_path / "out"
    code = main(["--scenario", "b08_kaufen", "--out", str(out)],
                test_client_factory=lambda s, r: ScriptedModelClient([ModelCallError("auth")]), env={})
    assert code == 4 and _report(out)[1]["aborted"] == "auth"


def test_the_trace_header_carries_the_git_commit(tmp_path: Path) -> None:
    from eval.scenarios import load_all
    from scout_research.agent.trace import current_git_commit, validate_trace_file

    if current_git_commit(Path(runner.__file__).resolve().parent) is None:
        pytest.skip("kein Git-Checkout")

    sc = [s for s in load_all() if s.scenario_id == "b08_kaufen"]
    result = runner.run_set(sc, 3, tmp_path)
    assert result.git_commit is not None
    headers = [validate_trace_file(Path(r.trace_path))[0] for r in result.records]
    assert {h.git_commit for h in headers} == {result.git_commit}


def test_n_a_runs_do_not_count_in_n(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main(["--scenario", "b01_comps_nvda", "--out", str(out)], env={}) == 0
    text, data = _report(out)
    b2 = data["scenarios"]["b01_comps_nvda"]["behaviors"]["B2"]
    assert b2["n"] == 0 and b2["counts"]["n/a"] == 3
    assert "B2: nicht gemessen (n/a in 3 Läufen)" in text
