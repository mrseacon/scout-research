"""Live-Sperre des Runners, Schutz nach dem Lauf (12.10) und Prompt-Varianten (TA-16b). Kein echter Modellaufruf: Die Live-Fabrik
ist in jedem Test eine Attrappe, `anthropic` wird nie importiert."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import eval.run_scenarios as runner
from eval.run_scenarios import main
from scout_research.agent.model_client import ScriptedModelClient
from scout_research.agent.spend import SpendBudget, SpendLedger
from scout_research.cli import LIVE_NOT_ALLOWED



@pytest.fixture(autouse=True)
def _no_sdk_import():
    """Kein Test dieser Datei darf `anthropic` importieren (vor und nach jedem Test verglichen)."""
    before = "anthropic" in sys.modules
    yield
    assert before or "anthropic" not in sys.modules


SCENARIO = ["--scenario", "b03_ueberspringen"]
FAKE_KEY = "sk-ant-FAKE-0123456789abcdef"
FAKE_FINNHUB = "FAKE-FINNHUB-live-0123456789"
FAKE_MAIL = "live.tester@example.invalid"


def _settings(tmp_path: Path, **over) -> SimpleNamespace:
    values = dict(anthropic_api_key=FAKE_KEY, finnhub_api_key=FAKE_FINNHUB, edgar_contact_email=FAKE_MAIL,
                  edgar_user_agent=f"Scout Research ({FAKE_MAIL})", anthropic_model="claude-sonnet-5-5", anthropic_effort="medium",
                  anthropic_max_tokens=8000, output_language="de", budget_run_usd=1.0,
                  budget_day_usd=3.0, runs_dir=tmp_path / "runs")
    values.update(over)
    return SimpleNamespace(**values)


class Live:
    """Eine Live-Umgebung mit Attrappen; zählt, was aufgerufen wird."""

    def __init__(self, tmp_path: Path, *, env: dict | None = None, tty: bool = True, answer: str | BaseException = "LIVE 3",
                 settings: SimpleNamespace | None = None, factory=None) -> None:
        self.tmp_path = tmp_path
        self.env = {"SCOUT_EVAL_LIVE": "1"} if env is None else env
        self.tty, self.answer = tty, answer
        self.settings = settings or _settings(tmp_path)
        self.factory_calls: list[tuple[str, int]] = []
        self.input_calls = self.tty_calls = self.settings_calls = 0
        self.factory = factory or self._default_factory
        self.out = tmp_path / "out"

    def _default_factory(self, scenario, run):
        self.factory_calls.append((scenario.scenario_id, run))
        return runner.dry_client(scenario, run)

    def _isatty(self) -> bool:
        self.tty_calls += 1
        return self.tty

    def _input(self, prompt: str) -> str:
        self.input_calls += 1
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer

    def _settings_loader(self):
        self.settings_calls += 1
        return self.settings

    def run(self, extra: list[str] | None = None) -> int:
        budget = SpendBudget(1.0, 3.0, SpendLedger(self.tmp_path / "ledger.json"))
        return main([*SCENARIO, "--live", "--out", str(self.out), *(extra or [])], live_client_factory=self.factory,
                    budget_factory=lambda: budget, env=self.env, isatty=self._isatty, input_fn=self._input,
                    settings_loader=self._settings_loader)


# --- Sperre -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["no_env", "env_zero", "env_true", "no_tty", "wrong_input", "lowercase", "eof", "ctrl_c", "key_missing"])
def test_every_missing_condition_refuses_with_the_fixed_text(tmp_path: Path, capsys, case: str) -> None:
    kw: dict = {"no_env": {"env": {}}, "env_zero": {"env": {"SCOUT_EVAL_LIVE": "0"}}, "env_true": {"env": {"SCOUT_EVAL_LIVE": "true"}},
                "no_tty": {"tty": False}, "wrong_input": {"answer": "LIVE 4"}, "lowercase": {"answer": "live 3"},
                "eof": {"answer": EOFError()}, "ctrl_c": {"answer": KeyboardInterrupt()}, "key_missing": {}}[case]
    live = Live(tmp_path, **kw)
    if case == "key_missing":
        live.settings = _settings(tmp_path, anthropic_api_key=None)
    assert live.run() == 2
    err = capsys.readouterr().err
    assert live.factory_calls == [] and not live.out.exists()
    assert (runner.NO_KEY_TEXT if case == "key_missing" else LIVE_NOT_ALLOWED) in err
    if case != "key_missing":
        assert err.strip() == LIVE_NOT_ALLOWED


def test_without_env_neither_the_terminal_nor_stdin_nor_the_settings_are_touched(tmp_path: Path, capsys) -> None:
    live = Live(tmp_path, env={})
    assert live.run() == 2
    assert (live.tty_calls, live.input_calls, live.settings_calls) == (0, 0, 0)


def test_without_a_terminal_stdin_and_settings_are_not_read(tmp_path: Path, capsys) -> None:
    live = Live(tmp_path, tty=False)
    assert live.run() == 2
    assert (live.tty_calls, live.input_calls, live.settings_calls) == (1, 0, 0)


def test_full_release_calls_the_factory_once_per_run_and_marks_the_report_live(tmp_path: Path, capsys) -> None:
    live = Live(tmp_path)
    assert live.run() == 0
    assert live.factory_calls == [("b03_ueberspringen", 1), ("b03_ueberspringen", 2), ("b03_ueberspringen", 3)]
    text = (live.out / "report.md").read_text(encoding="utf-8")
    data = json.loads((live.out / "report.json").read_text(encoding="utf-8"))
    assert "LIVE" in text and "TROCKENLAUF" not in text and data["header"]["mode"] == "live" and data["live"] is True
    shown = capsys.readouterr().out
    assert "1 Szenarien x 3 Läufe = 3 Gesamtläufe" in shown and "claude-sonnet-5-5" in shown and "1.0 $ je Lauf, 3.0 $ je Tag" in shown
    assert "Heute bereits gebucht" in shown and "scripts/estimate_cost.py" in shown
    assert FAKE_KEY not in shown


def test_the_default_factory_is_built_only_after_the_gate_and_gets_the_key(tmp_path: Path, monkeypatch, capsys) -> None:
    built: list[str] = []

    def fake_adapter(sdk):
        return ScriptedModelClient([])

    monkeypatch.setattr(runner, "make_sdk_client", lambda key: built.append(key) or "sdk")
    monkeypatch.setattr(runner, "AnthropicAdapter", fake_adapter)
    live = Live(tmp_path, answer="nein")
    budget = SpendBudget(1.0, 3.0, SpendLedger(tmp_path / "l.json"))
    kwargs = dict(budget_factory=lambda: budget, env=live.env, isatty=live._isatty, input_fn=live._input,
                  settings_loader=live._settings_loader)
    assert main([*SCENARIO, "--live", "--out", str(live.out)], **kwargs) == 2 and built == []
    live.answer = "LIVE 3"
    main([*SCENARIO, "--live", "--out", str(live.out)], **kwargs)
    assert built == [FAKE_KEY] * 3  # je Lauf ein Adapter, mit dem Key aus den Settings


def test_an_unreadable_ledger_refuses_the_live_run(tmp_path: Path, capsys) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("kein json", encoding="utf-8")
    live = Live(tmp_path)
    assert main([*SCENARIO, "--live", "--out", str(live.out)], live_client_factory=live.factory,
                budget_factory=lambda: SpendBudget(1.0, 3.0, SpendLedger(broken)), env=live.env, isatty=live._isatty,
                input_fn=live._input, settings_loader=live._settings_loader) == 2
    assert runner.LEDGER_UNREADABLE_TEXT in capsys.readouterr().err and live.input_calls == 0 and not live.out.exists()


def test_unreadable_settings_give_a_fixed_text_without_the_exception(tmp_path: Path, capsys) -> None:
    live = Live(tmp_path)

    def broken():
        raise ValueError(f"enthält {FAKE_KEY}")

    assert main([*SCENARIO, "--live", "--out", str(live.out)], live_client_factory=live.factory, env=live.env,
                isatty=live._isatty, input_fn=live._input, settings_loader=broken) == 2
    err = capsys.readouterr().err
    assert runner.NO_SETTINGS_TEXT in err and FAKE_KEY not in err


# --- Schutz nach dem Lauf -----------------------------------------------------------------------------------------------


def _leaking(where: str, secret: str):
    """Ein Lauf, dessen Ausgabe nachträglich ein Secret enthält: `where` ist die Datei, in die es geschrieben wird."""
    real = runner.write_report

    def write(result, out):
        report = real(result, out)
        target = {"report": out / "report.md", "shown": next((out / "shown").glob("*.json")),
                  "trace": next((out / "traces").rglob("*.jsonl"))}[where]
        with target.open("a", encoding="utf-8") as fh:
            fh.write(f"\nleck: {secret}\n")
        return report

    return write


@pytest.mark.parametrize("where", ["shown", "report", "trace"])
def test_a_secret_in_the_output_gives_exit_6_with_the_file_name_but_not_the_value(tmp_path: Path, monkeypatch, capsys, where: str) -> None:
    from tests.unit.tool_world import FINNHUB_KEY

    monkeypatch.setattr(runner, "write_report", _leaking(where, FINNHUB_KEY))
    out = tmp_path / "out"
    assert main([*SCENARIO, "--out", str(out)], env={}) == 6
    err = capsys.readouterr().err
    name = {"shown": "shown", "report": "report.md", "trace": ".jsonl"}[where]
    assert name in err and FINNHUB_KEY not in err and runner.SECRET_FOUND_TEXT.split("{")[0] in err


def test_exit_6_outranks_a_hard_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.unit.tool_world import FINNHUB_KEY

    real = runner.evaluate_run

    def failing(path, scenario, **kw):
        verdict = real(path, scenario, **kw)
        verdict.hard_failures.append("B3: erfunden")
        return verdict

    monkeypatch.setattr(runner, "evaluate_run", failing)
    monkeypatch.setattr(runner, "write_report", _leaking("report", FINNHUB_KEY))
    assert main([*SCENARIO, "--out", str(tmp_path / "out")], env={}) == 6


def test_live_scan_uses_the_settings_secrets_and_ignores_short_values(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(runner, "write_report", _leaking("report", FAKE_KEY))
    live = Live(tmp_path)
    assert live.run() == 6
    err = capsys.readouterr().err
    assert "report.md" in err and FAKE_KEY not in err


def test_live_scan_ignores_secrets_below_eight_characters(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(runner, "write_report", _leaking("report", "kurz"))
    live = Live(tmp_path, settings=_settings(tmp_path, finnhub_api_key="kurz"))
    assert live.run() == 0


def test_live_logging_is_set_up_before_the_first_run_and_removed_afterwards(tmp_path: Path, capsys) -> None:
    import logging

    seen: list[int] = []

    def factory(scenario, run):
        seen.append(sum(getattr(h, "_scout_live_guard", False) for h in logging.getLogger().handlers))
        logging.getLogger("irgendein.modul").warning("Schlüssel %s", FAKE_KEY)
        return runner.dry_client(scenario, run)

    live = Live(tmp_path, factory=factory)
    assert live.run() == 0
    assert seen and all(n >= 1 for n in seen)
    assert not any(getattr(h, "_scout_live_guard", False) for h in logging.getLogger().handlers)
    log = (live.out / "run.log").read_text(encoding="utf-8")
    assert FAKE_KEY not in log and "<redacted>" in log


# --- Prompt-Variante im Runner ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["turn_ended", "p8_host_turn"])
def test_the_runner_loads_the_variant_and_trace_and_report_carry_its_version(tmp_path: Path, variant: str) -> None:
    from scout_research.agent.trace import validate_trace_file

    out = tmp_path / "out"
    assert main([*SCENARIO, "--prompt-variant", variant, "--out", str(out)], env={}) == 0
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert data["header"]["prompt_version"] == f"1.2.0-{variant}" and data["header"]["prompt_variant"] == variant
    assert f"v1.2.0-{variant}, Variante {variant}" in (out / "report.md").read_text(encoding="utf-8")
    trace = next((out / "traces").rglob("*.jsonl"))
    assert validate_trace_file(trace)[0].prompt_version == f"1.2.0-{variant}"


# --- Korrekturrunde -----------------------------------------------------------------------------------------------------


def test_preview_request_report_and_trace_header_use_the_model_of_the_settings(tmp_path: Path, monkeypatch, capsys) -> None:
    from scout_research.agent import budgets
    from scout_research.agent.trace import validate_trace_file

    monkeypatch.setitem(budgets.PRICES, "claude-test-9", budgets.PRICES["claude-sonnet-5-5"])
    clients: list[ScriptedModelClient] = []

    def factory(scenario, run):
        clients.append(runner.dry_client(scenario, run))
        return clients[-1]

    live = Live(tmp_path, factory=factory, settings=_settings(tmp_path, anthropic_model="claude-test-9",
                                                              anthropic_effort="high", anthropic_max_tokens=4321))
    assert live.run() == 0
    preview = capsys.readouterr().out
    assert "Modell: claude-test-9" in preview
    requests = [r for c in clients for r in c.requests]
    assert requests and {(r.model, r.effort, r.max_tokens) for r in requests} == {("claude-test-9", "high", 4321)}
    assert json.loads((live.out / "report.json").read_text(encoding="utf-8"))["header"]["model"] == "claude-test-9"
    header = validate_trace_file(next((live.out / "traces").rglob("*.jsonl")))[0]
    assert header.model == "claude-test-9" and header.effort == "high"


def test_the_scan_also_runs_when_the_run_raises_and_a_leak_gives_exit_6(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.unit.tool_world import FINNHUB_KEY

    real = runner.write_report

    def failing(result, out):
        next((out / "traces").rglob("*.jsonl")).open("a", encoding="utf-8").write(f"\n{FINNHUB_KEY}\n")
        raise RuntimeError("Bericht kaputt")

    monkeypatch.setattr(runner, "write_report", failing)
    assert main([*SCENARIO, "--out", str(tmp_path / "out")], env={}) == 6
    err = capsys.readouterr().err
    assert ".jsonl" in err and FINNHUB_KEY not in err and "Lauf zusätzlich abgebrochen: RuntimeError" in err
    assert "Bericht kaputt" not in err
    monkeypatch.setattr(runner, "write_report", lambda r, o: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        main([*SCENARIO, "--out", str(tmp_path / "out2")], env={})
    monkeypatch.setattr(runner, "write_report", lambda r, o: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        main([*SCENARIO, "--out", str(tmp_path / "out3")], env={})
    assert real is not None


def test_live_without_an_injected_budget_books_into_the_day_ledger_of_the_settings(tmp_path: Path, capsys) -> None:
    live = Live(tmp_path)
    kwargs = dict(live_client_factory=live.factory, env=live.env, isatty=live._isatty, input_fn=live._input,
                  settings_loader=live._settings_loader)
    assert main([*SCENARIO, "--live", "--out", str(live.out)], **kwargs) == 0
    ledger = live.settings.runs_dir / "spend_ledger.json"
    assert ledger.exists() and sum(json.loads(ledger.read_text(encoding="utf-8")).values()) > 0
    assert not (live.out / "ledger").exists()
    assert "Test-Hook (Budget)" not in (live.out / "report.md").read_text(encoding="utf-8")


def test_an_injected_budget_in_live_mode_is_marked_in_the_report(tmp_path: Path, capsys) -> None:
    live = Live(tmp_path)
    assert live.run() == 0
    assert json.loads((live.out / "report.json").read_text(encoding="utf-8"))["budget_hook"] is True
    assert "Test-Hook (Budget)" in (live.out / "report.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("answer", [" LIVE 3", "LIVE 3 ", "LIVE  3", "LIVE 3\t"])
def test_the_confirmation_is_compared_exactly(tmp_path: Path, capsys, answer: str) -> None:
    live = Live(tmp_path, answer=answer)
    assert live.run() == 2 and live.factory_calls == [] and not live.out.exists()


@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_only_the_line_ending_is_removed_from_the_confirmation(tmp_path: Path, capsys, ending: str) -> None:
    live = Live(tmp_path, answer="LIVE 3" + ending)
    assert live.run() == 0


def test_without_stdin_the_live_run_is_refused(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", None)
    live = Live(tmp_path)
    assert main([*SCENARIO, "--live", "--out", str(live.out)], live_client_factory=live.factory, env=live.env,
                input_fn=live._input, settings_loader=live._settings_loader) == 2
    assert capsys.readouterr().err.strip() == LIVE_NOT_ALLOWED and live.input_calls == 0 and not live.out.exists()


def test_live_needs_a_new_or_empty_output_folder(tmp_path: Path, capsys) -> None:
    live = Live(tmp_path)
    live.out.mkdir()
    (live.out / ".env").write_text("fremd", encoding="utf-8")
    assert live.run() == 2
    assert runner.OUT_NOT_EMPTY_TEXT in capsys.readouterr().err and live.input_calls == 0 and live.factory_calls == []
    (live.out / ".env").unlink()
    assert live.run() == 0  # leerer Ordner ist erlaubt


def test_a_link_and_an_unreadable_file_in_the_output_give_exit_6_with_their_own_texts(tmp_path: Path, monkeypatch, capsys) -> None:
    real = runner.write_report

    def write(result, out):
        report = real(result, out)
        (out / "link.md").symlink_to(tmp_path)
        locked = out / "gesperrt.md"
        locked.write_text("x", encoding="utf-8")
        locked.chmod(0)
        return report

    monkeypatch.setattr(runner, "write_report", write)
    out = tmp_path / "out"
    try:
        assert main([*SCENARIO, "--out", str(out)], env={}) == 6
    finally:
        (out / "gesperrt.md").chmod(0o644)
    err = capsys.readouterr().err
    assert "Nicht geprüft (Link): link.md" in err and "Nicht prüfbar (Datei nicht lesbar): gesperrt.md" in err


# --- Variante: Ersatz-Hint von TURN_ENDED -------------------------------------------------------------------------------


def _turn_ended_client(scenario, run):
    from tests.unit.loop_harness import say, use

    calls = [("resolve_company", {"query": f"Firma{i}"}) for i in range(13)]  # das dreizehnte Tool übersteigt die Grenze je Zug
    return ScriptedModelClient([use(*calls), say("Fertig.")])


def test_the_variant_replaces_the_turn_ended_hint_only_during_its_runs(tmp_path: Path) -> None:
    from eval.scenarios import load_all
    from eval.variants import VARIANT_HINTS
    from scout_research.tools.errors import ERROR_SPECS

    original = ERROR_SPECS["TURN_ENDED"].hint
    scenario = [s for s in load_all() if s.scenario_id == "b03_ueberspringen"]
    seen: dict[str, list[str]] = {}
    for variant in ("active", "turn_ended"):
        clients: list[ScriptedModelClient] = []

        def factory(s, r):
            clients.append(_turn_ended_client(s, r))
            return clients[-1]

        runner.run_set(scenario, 3, tmp_path / variant, prompt_variant=variant, test_client_factory=factory)
        text = json.dumps([r.messages for c in clients for r in c.requests], ensure_ascii=False)
        assert "TURN_ENDED" in text
        seen[variant] = [VARIANT_HINTS["turn_ended"]["TURN_ENDED"] in text, original in text]
        assert ERROR_SPECS["TURN_ENDED"].hint == original  # danach wiederhergestellt
    assert seen == {"active": [False, True], "turn_ended": [True, False]}


def test_the_hint_is_restored_when_a_run_raises(tmp_path: Path, monkeypatch) -> None:
    from eval.scenarios import load_all
    from scout_research.tools.errors import ERROR_SPECS

    original = dict(ERROR_SPECS)
    monkeypatch.setattr(runner, "_run_one", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Programmfehler")))
    with pytest.raises(RuntimeError):
        runner.run_set(load_all()[:1], 3, tmp_path, prompt_variant="turn_ended")
    assert ERROR_SPECS == original


def test_a_failing_scan_counts_as_a_finding(tmp_path: Path, monkeypatch, capsys) -> None:
    def broken(paths, secrets):
        raise OSError("kaputt")

    monkeypatch.setattr(runner, "scan_findings", broken)
    assert main([*SCENARIO, "--out", str(tmp_path / "out")], env={}) == 6
    assert runner.SCAN_FAILED_TEXT in capsys.readouterr().err


def test_a_symlink_as_output_folder_is_refused_in_live_mode(tmp_path: Path, capsys) -> None:
    live = Live(tmp_path)
    target = tmp_path / "ziel"
    target.mkdir()
    live.out.symlink_to(target)
    assert live.run() == 2
    assert runner.OUT_NOT_EMPTY_TEXT in capsys.readouterr().err and live.settings_calls == 0 and live.input_calls == 0


def test_a_model_without_a_price_is_refused_before_the_preview(tmp_path: Path, capsys) -> None:
    live = Live(tmp_path, settings=_settings(tmp_path, anthropic_model="modell-ohne-preis"))
    assert live.run() == 2
    captured = capsys.readouterr()
    assert runner.NO_PRICE_TEXT in captured.err and "Live-Lauf:" not in captured.out
    assert live.input_calls == 0 and live.factory_calls == [] and not live.out.exists()


def test_every_main_call_in_the_runner_tests_passes_env() -> None:
    """Kein Test darf gegen die echte Umgebung laufen: Jeder `main(...)`-Aufruf gibt `env=` an oder reicht `**kwargs` mit `env`
    durch (dort ist `env` im Wörterbuch; hier nur geprüft, dass es in der Datei so gebaut wird)."""
    import ast

    offenders = []
    for name in ("test_eval_runner.py", "test_eval_live.py"):
        path = Path(__file__).with_name(name)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "main":
                if not any(k.arg == "env" or k.arg is None for k in node.keywords):
                    offenders.append(f"{name}:{node.lineno}")
    assert offenders == []
