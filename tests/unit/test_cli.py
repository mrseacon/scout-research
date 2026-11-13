"""`scout-research chat --scripted` (TA-14): Ablauf gegen die Demo-Welt, Entscheidung nur über die Host-API, Strg+C, Budget,
kein Live-Modus. Kein Netzwerk: EDGAR läuft über `httpx.MockTransport`, das Modell ist das feste Skript."""

from __future__ import annotations

import ast
import io
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scout_research import cli
from scout_research.agent import model_client
from scout_research.agent.loop import AgentLoop
from scout_research.agent.model_client import ScriptedModelClient
from scout_research.agent.spend import SpendBudget
from scout_research.config import Settings
from scout_research.demo import script as demo_script
from scout_research.tools.session import Proposal

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
ASK = "Mach Comps für ZZNV"
TABLE_HEAD = "| Ticker | Periodenende | revenue |"


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Demo darf keine Verbindung öffnen (EDGAR läuft über den MockTransport)."""

    def refuse(self, *args, **kwargs):
        raise AssertionError("Netzwerkversuch in der Demo")

    monkeypatch.setattr(socket.socket, "connect", refuse)


def _run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], lines: list[str] | str,
         args: tuple[str, ...] = ("chat", "--scripted")) -> tuple[int, str, str]:
    text = lines if isinstance(lines, str) else "".join(line + "\n" for line in lines)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    code = cli.main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


class Spy:
    """Zeichnet Aufrufe von `AgentLoop.run_turn/confirm/reject` auf und ruft das Original."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.turns: list[str] = []
        self.confirms: list[tuple[Any, ...]] = []
        self.rejects: list[tuple[Any, ...]] = []
        self.loops: list[AgentLoop] = []
        run_turn, confirm, reject = AgentLoop.run_turn, AgentLoop.confirm, AgentLoop.reject

        def spy_turn(loop, msg):
            self.loops.append(loop)
            self.turns.append(msg.text)
            return run_turn(loop, msg)

        def spy_confirm(loop, proposal_id, keep, *a, **kw):
            self.confirms.append((proposal_id, tuple(keep), a, kw))
            return confirm(loop, proposal_id, keep, *a, **kw)

        def spy_reject(loop, proposal_id, *a, **kw):
            self.rejects.append((proposal_id, a, kw))
            return reject(loop, proposal_id, *a, **kw)

        monkeypatch.setattr(AgentLoop, "run_turn", spy_turn)
        monkeypatch.setattr(AgentLoop, "confirm", spy_confirm)
        monkeypatch.setattr(AgentLoop, "reject", spy_reject)


# --- Ablauf -----------------------------------------------------------------------------------------------------------


def test_scripted_chat_runs_a_complete_analysis_and_shows_table_and_commentary(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    spy = Spy(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, [ASK, "/confirm"])
    assert code == 0 and err == ""
    # Vorschlag, dann Tabelle, dann Kommentar
    assert out.index("Vorschlag für das Peer-Set:") < out.index(TABLE_HEAD) < out.index("Kommentar:")
    assert out.count(cli.RATIONALE_LABEL) == 3  # M2: je Peer das feste Label
    assert out.count(cli.DEMO_NOTICE) >= 3  # Kopfzeile, vor der Tabelle, vor dem Kommentar
    assert f"{cli.DEMO_NOTICE}\nComps-Tabelle" in out and f"{cli.DEMO_NOTICE}\nKommentar:" in out  # M4
    assert "Basis: ZZNV (Ziel)" in out[out.index("Comps-Tabelle"):out.index(TABLE_HEAD)]  # N5: Basiszeile
    assert "* EBITDA angenähert" in out and "Warnungen:\n- [W1: Peer-Set]" in out  # Legende und Warnungsliste
    assert "ZZNV (Ziel)" in out and "ZZPL" in out and "ZZOR" in out and "ZZQN" in out
    assert "42,3x*" in out  # EV/EBITDA des Ziels, vom Code gerendert
    assert "Basis: ZZNV (Ziel), GJ 2025" in out and "Peer-Median von 31,6x" in out
    assert "zurückgehalten" not in out and "Traceback" not in out
    # Die Entscheidung lief über die Host-API, nie als Text
    assert spy.turns == [ASK] and len(spy.confirms) == 1 and spy.rejects == []
    assert spy.confirms[0][3] == {} and len(spy.confirms[0][1]) == 3


def test_the_number_check_accepts_the_demo_commentary_without_open_warnings(tmp_path: Path) -> None:
    from scout_research.agent.prompt_loader import load_system_prompt
    from scout_research.demo.world import build_context
    from scout_research.tools.session import HumanInput

    ctx = build_context()
    budget = cli.make_budget(scripted=True, ledger_dir=tmp_path)
    loop = AgentLoop(ctx, demo_script.build_client(ctx), load_system_prompt("de").text, budget=budget)
    loop.run_turn(HumanInput(ASK))
    proposal = ctx.proposals[ctx.current_proposal_id]
    result = loop.confirm(proposal.id, [p["cik"] for p in proposal.peers])
    stored = ctx.commentaries[result.commentary_id]
    assert stored.status == "accepted" and stored.attempts == 1 and stored.warnings_not_addressed == []
    assert result.shown[0].kind == "commentary" and "[[" not in result.shown[0].text


def test_confirm_with_tickers_keeps_only_those_peers(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    spy = Spy(monkeypatch)
    code, out, _ = _run(monkeypatch, capsys, [ASK, "/confirm zzor"])
    assert code == 0
    table = out[out.index(TABLE_HEAD):out.index("Kommentar:")]
    assert "ZZOR" in table and "ZZPL" not in table and "ZZQN" not in table
    assert len(spy.confirms[0][1]) == 1


def test_confirm_with_an_unknown_ticker_decides_nothing(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    spy = Spy(monkeypatch)
    code, out, _ = _run(monkeypatch, capsys, [ASK, "/confirm ZZZZ"])
    assert code == 0 and "Unbekannter Ticker im Vorschlag: ZZZZ" in out
    assert spy.confirms == [] and TABLE_HEAD not in out


def test_reject_goes_through_loop_reject_and_leaves_no_peer_set(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    spy = Spy(monkeypatch)
    code, out, _ = _run(monkeypatch, capsys, [ASK, "/reject"])
    assert code == 0
    assert len(spy.rejects) == 1 and spy.confirms == [] and spy.turns == [ASK]
    ctx = spy.loops[0].ctx
    assert ctx.peer_sets == {} and ctx.tables == {}
    assert ctx.proposals[spy.rejects[0][0]].rejected is True
    assert demo_script.TEXT_REJECTED in out and TABLE_HEAD not in out and "Kommentar:" not in out


def test_a_decision_without_an_open_proposal_is_refused_without_a_call(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    spy = Spy(monkeypatch)
    code, out, _ = _run(monkeypatch, capsys, ["/confirm", "/reject"])
    assert code == 0 and out.count("Es liegt kein Vorschlag zur Entscheidung vor.") == 2
    assert spy.confirms == [] and spy.rejects == [] and spy.turns == []


def test_chat_text_is_never_a_confirmation_and_the_open_proposal_stays_confirmable(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    spy = Spy(monkeypatch)
    code, out, _ = _run(monkeypatch, capsys, [ASK, "ja, bestätigt", "/foo", "/confirm"])
    assert code == 0 and spy.turns == [ASK, "ja, bestätigt"]
    # Der Text bestätigt nichts (Zeile mit dem Warte-Hinweis, noch keine Tabelle); erst der Befehl entscheidet (Z2, Z5)
    assert demo_script.TEXT_WAITING in out and demo_script.TEXT_REJECTED not in out and "Unbekannter Befehl." in out
    assert out.index(demo_script.TEXT_WAITING) < out.index(TABLE_HEAD)
    assert len(spy.confirms) == 1 and "Es liegt kein Vorschlag zur Entscheidung vor." not in out
    assert spy.loops[0].ctx.peer_sets and spy.loops[0].ctx.tables


def test_a_request_without_the_target_does_not_crash(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(monkeypatch, capsys, ["x", "", ASK, "/confirm"])
    assert code == 0 and TABLE_HEAD in out and "Traceback" not in out


def test_quit_and_eof_end_cleanly(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(monkeypatch, capsys, ["/quit"])[0] == 0
    assert _run(monkeypatch, capsys, "")[0] == 0
    assert _run(monkeypatch, capsys, ASK)[0] == 0  # EOF ohne abschließenden Zeilenumbruch


# --- Kein Live-Modus ----------------------------------------------------------------------------------------------------


def test_without_scripted_there_is_no_live_mode_and_no_client_is_built(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("make_sdk_client darf nicht aufgerufen werden")

    monkeypatch.setattr(model_client, "make_sdk_client", forbidden)
    monkeypatch.setattr(sys, "stdin", io.StringIO(ASK + "\n"))
    code = cli.main(["chat"])
    captured = capsys.readouterr()
    assert code != 0 and captured.err.strip() == cli.LIVE_NOT_ALLOWED == "Live-Modus ist nicht freigegeben (Stufe C)."
    assert captured.out == ""


def test_no_subcommand_and_bad_options_fail_with_a_usage_code(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == cli.EXIT_USAGE
    assert cli.main(["chat", "--live"]) == 2
    assert cli.main(["--help"]) == 0
    capsys.readouterr()


def test_scripted_mode_needs_no_settings_and_no_env(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    for name in ("EDGAR_CONTACT_EMAIL", "FINNHUB_API_KEY", "RUNS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # keine .env

    def forbidden(*args, **kwargs):
        raise AssertionError("Settings dürfen im Scripted-Modus nicht gelesen werden")

    monkeypatch.setattr(Settings, "__init__", forbidden)
    code, out, _ = _run(monkeypatch, capsys, [ASK, "/confirm"])
    assert code == 0 and TABLE_HEAD in out
    assert list(tmp_path.iterdir()) == []  # weder runs/ noch ein Kostenbuch im Arbeitsordner


# --- Strg+C ---------------------------------------------------------------------------------------------------------------


def _interrupt_first_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    create = ScriptedModelClient.create
    calls: list[int] = []

    def create_once_interrupted(self, request, on_event):
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return create(self, request, on_event)

    monkeypatch.setattr(ScriptedModelClient, "create", create_once_interrupted)


def test_ctrl_c_during_a_turn_ends_the_turn_as_interrupted_and_the_session_goes_on(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _interrupt_first_model_call(monkeypatch)
    spy = Spy(monkeypatch)
    code, out, _ = _run(monkeypatch, capsys, [ASK, ASK, "/confirm"])
    assert code == 0 and "Traceback" not in out
    assert "Hinweis:" in out and "abgebrochen" in out.lower()
    assert out.index("Hinweis:") < out.index("Vorschlag für das Peer-Set:") < out.index(TABLE_HEAD)
    assert spy.turns == [ASK, ASK] and len(spy.confirms) == 1


def test_a_second_ctrl_c_aborts_only_the_turn_and_the_session_goes_on(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _interrupt_first_model_call(monkeypatch)
    finish = AgentLoop._finish
    finishes: list[int] = []

    def finish_once_interrupted(self, *args, **kwargs):
        finishes.append(1)
        if len(finishes) == 1:
            raise KeyboardInterrupt
        return finish(self, *args, **kwargs)

    monkeypatch.setattr(AgentLoop, "_finish", finish_once_interrupted)
    spy = Spy(monkeypatch)
    code, out, _ = _run(monkeypatch, capsys, [ASK, ASK, "/confirm"])
    assert code == 0 and "Traceback" not in out
    assert "Zug abgebrochen (Strg+C)." in out
    assert out.index("Zug abgebrochen") < out.index("Vorschlag für das Peer-Set:") < out.index(TABLE_HEAD)
    assert len(spy.confirms) == 1 and spy.loops[0].state.value == "idle"


def test_ctrl_c_at_the_prompt_ends_cleanly_with_130(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    class Stdin:
        def readline(self) -> str:
            raise KeyboardInterrupt

    monkeypatch.setattr(sys, "stdin", Stdin())
    assert cli.main(["chat", "--scripted"]) == cli.EXIT_INTERRUPT == 130
    out = capsys.readouterr().out
    assert "Beendet." in out and "Traceback" not in out


def test_an_internal_error_in_a_turn_prints_a_fixed_text_never_the_exception(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def boom(self, msg):
        raise RuntimeError("GEHEIM-DETAIL")

    monkeypatch.setattr(AgentLoop, "run_turn", boom)
    code, out, err = _run(monkeypatch, capsys, [ASK])
    assert code == 0 and "Interner Fehler im Zug." in out
    assert "GEHEIM-DETAIL" not in out + err and "Traceback" not in out + err


# --- Budget und Trace -------------------------------------------------------------------------------------------------------


def test_the_scripted_budget_never_touches_the_real_day_ledger(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    real = tmp_path / "real_runs"
    monkeypatch.setenv("RUNS_DIR", str(real))
    monkeypatch.chdir(tmp_path)
    code, out, _ = _run(monkeypatch, capsys, [ASK, "/confirm"], args=("chat", "--scripted", "--runs-dir", str(real)))
    assert code == 0 and TABLE_HEAD in out
    assert not (real / "spend_ledger.json").exists()
    assert [p for p in tmp_path.rglob("spend_ledger*")] == []
    assert not (tmp_path / "runs").exists()


def test_make_budget_scripted_uses_its_own_ledger_and_live_uses_the_settings(tmp_path: Path) -> None:
    scripted = cli.make_budget(scripted=True, ledger_dir=tmp_path / "demo")
    assert isinstance(scripted, SpendBudget) and scripted.ledger.path == tmp_path / "demo" / cli.DEMO_LEDGER_NAME
    with pytest.raises(ValueError):
        cli.make_budget(scripted=True)
    settings = Settings(edgar_contact_email="demo@example.invalid", runs_dir=tmp_path / "runs", _env_file=None)
    live = cli.make_budget(scripted=False, settings=settings)
    assert live.ledger.path == tmp_path / "runs" / "spend_ledger.json" and live.ledger.path != scripted.ledger.path
    assert (live.run_limit_usd, live.day_limit_usd) == (settings.budget_run_usd, settings.budget_day_usd)


def test_the_chat_books_into_a_real_budget_object(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    spy = Spy(monkeypatch)
    _run(monkeypatch, capsys, [ASK, "/confirm"])
    budget = spy.loops[0].budget
    assert isinstance(budget, SpendBudget) and budget.run_spent > 0


def test_runs_dir_gets_a_valid_trace(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, out, _ = _run(monkeypatch, capsys, [ASK, "/confirm"], args=("chat", "--scripted", "--runs-dir", str(tmp_path)))
    assert code == 0 and "Trace" not in out
    files = list(tmp_path.rglob("*.jsonl"))
    assert len(files) == 1
    events = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "header" and kinds[-1] == "end" and kinds.count("turn_end") == 2
    assert "trace_error" not in kinds and {"model_call", "tool", "commentary"} <= set(kinds)
    assert events[0]["run_mode"] == "demo"  # M4: abgeleitet aus `ctx.demo_session`, nicht übergeben


def test_an_unusable_runs_dir_is_reported_without_a_traceback(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    code, out, err = _run(monkeypatch, capsys, [ASK], args=("chat", "--scripted", "--runs-dir", str(blocker / "sub")))
    assert code == 1 and "Der Trace-Ordner ist nicht nutzbar." in out and "Traceback" not in out + err


# --- Prozess ---------------------------------------------------------------------------------------------------------------


def test_the_module_runs_as_a_process_without_proxy_with_a_dead_api_url_and_without_env(tmp_path: Path) -> None:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8",
           "ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "ANTHROPIC_API_KEY": "sk-ant-FAKE-process-test-0000"}
    done = subprocess.run([sys.executable, "-m", "scout_research.cli", "chat", "--scripted"], input=f"{ASK}\n/confirm\n",
                          capture_output=True, text=True, encoding="utf-8", env=env, cwd=tmp_path, timeout=120)
    assert done.returncode == 0, done.stderr
    assert TABLE_HEAD in done.stdout and "Kommentar:" in done.stdout and "Basis: ZZNV (Ziel)" in done.stdout
    assert "Traceback" not in done.stderr and done.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_the_process_without_scripted_fails_with_the_fixed_text(tmp_path: Path) -> None:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run([sys.executable, "-m", "scout_research.cli", "chat"], input="", capture_output=True, text=True,
                          encoding="utf-8", env=env, cwd=tmp_path, timeout=60)
    assert done.returncode == 2 and done.stderr.strip() == cli.LIVE_NOT_ALLOWED and done.stdout == ""


# --- Aufbau des Pakets -------------------------------------------------------------------------------------------------------


def test_cli_and_demo_import_nothing_from_tests_or_eval() -> None:
    offenders = []
    for path in [SRC / "scout_research" / "cli.py", *sorted((SRC / "scout_research" / "demo").glob("*.py"))]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            offenders += [f"{path.name}: {m}" for m in modules if m.split(".")[0] in {"tests", "eval", "anthropic"}]
    assert offenders == []


def test_the_entry_point_is_declared() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '[project.scripts]\nscout-research = "scout_research.cli:main"' in text


# --- Runde 2 ----------------------------------------------------------------------------------------------------------------


def test_after_a_second_ctrl_c_in_the_intro_the_open_proposal_is_still_shown_and_confirmable(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """M1 (Z19): Das zweite Strg+C trifft die Einleitung nach `propose_peer_set`; der Host zeigt trotzdem den Vorschlag aus dem
    Speicher des Codes, der Zustand ist AWAITING, und `/confirm` bestätigt genau das Gezeigte."""
    create = ScriptedModelClient.create
    calls: list[int] = []

    def interrupt_the_fourth(self, request, on_event):
        calls.append(1)
        if len(calls) == 4:  # resolve, find, propose, dann die Einleitung
            raise KeyboardInterrupt
        return create(self, request, on_event)

    finish = AgentLoop._finish
    finishes: list[int] = []

    def finish_once_interrupted(self, *args, **kwargs):
        finishes.append(1)
        if len(finishes) == 1:
            raise KeyboardInterrupt
        return finish(self, *args, **kwargs)

    monkeypatch.setattr(ScriptedModelClient, "create", interrupt_the_fourth)
    monkeypatch.setattr(AgentLoop, "_finish", finish_once_interrupted)
    spy = Spy(monkeypatch)
    code, out, _ = _run(monkeypatch, capsys, [ASK, "/confirm"])
    assert code == 0 and "Traceback" not in out
    assert out.index("Zug abgebrochen (Strg+C).") < out.index("Vorschlag für das Peer-Set:") < out.index(TABLE_HEAD)
    proposal_id = spy.confirms[0][0]
    assert proposal_id == spy.loops[0].ctx.proposals[proposal_id].id and spy.loops[0].ctx.proposals[proposal_id].confirmed


def test_a_table_made_in_a_turn_that_then_fails_is_still_shown(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """M1: Scheitert der Zug nach `compute_comps_table` (Ausnahme im Loop), zeigt die CLI die neue Tabelle aus `ctx.tables`."""
    original = AgentLoop.confirm

    def failing_confirm(self, *args, **kwargs):
        self.client._steps.insert(1, lambda request: (_ for _ in ()).throw(KeyboardInterrupt()))  # Strg+C vor dem Kommentar
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AgentLoop, "confirm", failing_confirm)
    code, out, _ = _run(monkeypatch, capsys, [ASK, "/confirm"])
    assert code == 0 and TABLE_HEAD in out and "Traceback" not in out


def _empty_ctx():
    from tests.unit.tool_world import standard_world

    return standard_world().context()


def _proposal(**kw) -> Proposal:
    base = dict(id="pp_x", candidate_set_id="cs_x", target_cik="1", peers=[], exclusions=[], user_additions=[])
    base.update(kw)
    return Proposal(**base)


def test_the_proposal_renderer_labels_rationales_exclusions_and_user_additions() -> None:
    """M2: Label vom Code, Texte so, wie sie gespeichert sind."""
    text = cli.render_proposal(_proposal(
        peers=[{"cik": "2", "ticker": "AAAA", "name": "A Corp", "rationale": "Gleiche Branche"}],
        exclusions=[{"cik": "3", "ticker": "BBBB", "name": "B Corp", "reason": "Zu klein"}],
        user_additions=[{"cik": "4", "ticker": "CCCC", "name": "C Corp", "query": "c"}]), _empty_ctx())
    assert "Ziel: nicht ermittelbar" in text  # N2: fehlt das Kandidatenset, steht das da statt nichts
    assert "Begründung (Modell-Einschätzung, nicht belegt): Gleiche Branche" in text
    assert "Ausgeschlossen:" in text and "BBBB: B Corp" in text and "Grund (Modell-Einschätzung, nicht belegt): Zu klein" in text
    assert "CCCC: C Corp (vom Nutzer genannt)" in text
    assert text.count("Modell-Einschätzung, nicht belegt") == 2 and "(vom Nutzer genannt)" in text


def test_the_demo_world_is_unmistakably_synthetic() -> None:
    from scout_research.demo.world import COMPANIES, SIC

    fixtures = "".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "tests" / "fixtures").glob("*.json")))
    for company in COMPANIES:
        assert company.ticker.startswith("ZZ") and company.name.endswith("(Demo)")
        assert f'"{company.ticker}"' not in fixtures and company.cik.lstrip("0") not in fixtures.replace('"', " ")
    assert SIC == "9999"


def test_a_part_of_the_output_that_fails_does_not_hide_the_commentary(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """N5: Fehler in der Tabellenanzeige → fester Text, der Kommentar erscheint trotzdem."""
    def boom(compact):
        raise RuntimeError("GEHEIM-DETAIL")

    monkeypatch.setattr(cli, "render_table", boom)
    code, out, err = _run(monkeypatch, capsys, [ASK, "/confirm"])
    assert code == 0 and "Hinweis: Ein Teil der Anzeige konnte nicht erstellt werden." in out
    assert "Kommentar:" in out and "Peer-Median von 31,6x" in out and "GEHEIM-DETAIL" not in out + err


def test_ctrl_c_while_flushing_the_trace_ends_with_130_and_fixed_text(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from scout_research.agent.trace import TraceWriter

    def interrupted(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(TraceWriter, "flush", interrupted)
    code, out, err = _run(monkeypatch, capsys, [ASK], args=("chat", "--scripted", "--runs-dir", str(tmp_path)))
    assert code == 130 and "Beendet." in out and "Traceback" not in out + err


def test_an_error_while_closing_the_trace_ends_with_1_and_fixed_text(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from scout_research.agent.trace import TraceWriter

    def broken(self, exc=None):
        raise RuntimeError("GEHEIM-DETAIL")

    monkeypatch.setattr(TraceWriter, "close", broken)
    code, out, err = _run(monkeypatch, capsys, [ASK], args=("chat", "--scripted", "--runs-dir", str(tmp_path)))
    assert code == 1 and "Interner Fehler außerhalb eines Zugs" in out
    assert "GEHEIM-DETAIL" not in out + err and "Traceback" not in out + err


def test_the_end_of_the_demo_script_is_a_clean_end_with_a_fixed_note(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """N11: Reichen die Schritte nicht mehr für einen Zug, endet die Demo mit „Demo beendet.“ und Exit 0."""
    monkeypatch.setattr(demo_script, "STEPS", 12)  # eine Analyse braucht 7 Aufrufe, danach bleiben 5 < 10
    spy = Spy(monkeypatch)
    code, out, _ = _run(monkeypatch, capsys, [ASK, "/confirm", "noch eine Frage", "und noch eine"])
    assert code == 0 and "Demo beendet." in out and "Interner Fehler" not in out and "Traceback" not in out
    assert spy.turns == [ASK]  # der zweite Zug wurde gar nicht erst begonnen


def test_cli_and_demo_never_reference_make_sdk_client() -> None:
    """N7: auch nicht per from-Import, Attribut oder Alias."""
    offenders = []
    for path in [SRC / "scout_research" / "cli.py", *sorted((SRC / "scout_research" / "demo").glob("*.py"))]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [node.id] if isinstance(node, ast.Name) else [node.attr] if isinstance(node, ast.Attribute) else \
                [a.name for a in node.names] + [a.asname or "" for a in node.names] if isinstance(node, (ast.Import, ast.ImportFrom)) else \
                [node.name] if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else \
                [node.value] if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 60 else []
            offenders += [f"{path.name}: {n}" for n in names if "make_sdk_client" in n]
    assert offenders == []


# --- TA-20.6 Teil 2 (I-6): der Vorschlag zeigt das Ziel und die Warnungen der Peer-Suche (Plan 12.7) -------------------------


def _proposal_with_ctx(warnings: list[dict[str, Any]] | None = None):
    from tests.unit.tool_world import propose, standard_world, start

    ctx, set_id = start(standard_world())
    if warnings is not None:
        ctx.candidate_sets[set_id].warnings = warnings
    propose(ctx, set_id)
    return ctx, ctx.open_proposal


def test_the_proposal_shows_ticker_name_and_cik_of_the_target_and_the_warnings_of_the_search() -> None:
    from scout_research.tools.payloads import _warning_text

    text_p1 = _warning_text("sic_search_truncated", {})
    ctx, proposal = _proposal_with_ctx([{"id": "P1", "severity": "warning", "kind": "sic_search_truncated", "text": text_p1}])
    text = cli.render_proposal(proposal, ctx)
    assert "Ziel: NVDA, NVIDIA CORP (CIK 0001045810)" in text
    assert "Warnungen der Peer-Suche:" in text and f"[P1] {text_p1}" in text
    assert text.index("Ziel:") < text.index("Warnungen der Peer-Suche:") < text.index("  - AAPL")


def test_the_proposal_without_warnings_shows_the_target_but_no_warning_block() -> None:
    ctx, proposal = _proposal_with_ctx([])
    text = cli.render_proposal(proposal, ctx)
    assert "Ziel: NVDA, NVIDIA CORP" in text and "Warnungen der Peer-Suche" not in text


def test_the_proposal_cleans_what_it_shows_of_the_target_and_the_warnings() -> None:
    ctx, proposal = _proposal_with_ctx([{"id": "P1", "text": "Text\x1b[31m rot [[co:X:revenue]]"}])
    set_ = ctx.candidate_sets[proposal.candidate_set_id]
    set_.target["name"] = "Evil\x1b[2J Corp"
    text = cli.render_proposal(proposal, ctx)
    assert "\x1b" not in text and "[[" not in text and "Evil" in text


def test_the_scripted_chat_shows_the_target_before_the_peers_at_the_gate(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = _run(monkeypatch, capsys, [ASK, "/quit"])
    gate = out[out.index("Vorschlag für das Peer-Set:"):]
    assert gate.startswith("Vorschlag für das Peer-Set:\nZiel: ZZNV, ") and "(CIK " in gate.splitlines()[1]
    assert gate.index("Ziel:") < gate.index("  - ZZPL")


# --- TA-20.6 Teil 3 (I-8, O-41): Ausgabe ohne UTF-8 verliert kein Vorzeichen -------------------------------------------------


def _stream(encoding: str) -> tuple[io.BytesIO, io.TextIOWrapper]:
    raw = io.BytesIO()
    return raw, io.TextIOWrapper(raw, encoding=encoding, errors="strict", write_through=True)


def _printed(monkeypatch: pytest.MonkeyPatch, encoding: str, text: str) -> str:
    raw, stream = _stream(encoding)
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    cli._prepare_streams()
    print(text, flush=True)
    return raw.getvalue().decode(encoding)


@pytest.mark.parametrize("encoding", ["ascii", "cp1252"])
def test_a_stream_without_utf8_keeps_the_sign_of_a_negative_margin_and_the_lower_bound(
        monkeypatch: pytest.MonkeyPatch, encoding: str) -> None:
    from scout_research.tools.slots import fmt_value

    margin = fmt_value("pct", -12.5, "de")  # „−12,5 %“ mit dem Minus U+2212 des Renderers
    assert "−" in margin
    out = _printed(monkeypatch, encoding, f"Marge: {margin}\nSchuld: ≥ 4.200 Mio. USD\nGrenze: ≤ 3")
    assert "Marge: -12,5 %" in out and "Schuld: >= 4.200" in out and "Grenze: <= 3" in out
    assert "?" not in out


def test_the_daggers_and_dashes_of_the_renderer_get_fixed_ascii_forms_on_ascii(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _printed(monkeypatch, "ascii", "† ‡ § … – — „x“ ’ → ×")
    assert out.rstrip("\n") == '(+) (++) (+++) ... - - "x" \' -> x'


def test_cp1252_keeps_the_characters_it_can_encode(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _printed(monkeypatch, "cp1252", "† ‡ … – „x“ ä").rstrip("\n") == "† ‡ … – „x“ ä"


@pytest.mark.parametrize("encoding", ["ascii", "cp1252"])
def test_any_other_unencodable_character_is_escaped_visibly_never_a_question_mark(
        monkeypatch: pytest.MonkeyPatch, encoding: str) -> None:
    out = _printed(monkeypatch, encoding, "Name: 二 \U0001f4af")
    assert out.rstrip("\n") == "Name: <?> <?>"  # N3: fester, ziffernfreier Platzhalter; kein nacktes „?“, keine Codepunkt-Ziffern
    assert not any(ch.isdigit() for ch in out) and "?" not in out.replace("<?>", "")


def test_a_utf8_stream_is_left_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "−12,5 % ≥ 3 † ‡ … 二"
    assert _printed(monkeypatch, "utf-8", text).rstrip("\n") == text


def test_the_translation_table_covers_every_special_character_the_renderer_uses() -> None:
    from scout_research.tools import commentary, slots

    assert slots.MINUS in cli.ASCII_FORMS and set(commentary.MARK_ORDER) - {"*"} <= set(cli.ASCII_FORMS)
    assert "≥" in cli.ASCII_FORMS and "≤" in cli.ASCII_FORMS
    assert all(form.isascii() for form in cli.ASCII_FORMS.values())


def test_the_proposal_shows_the_counts_of_the_search_and_the_period_end_of_the_target() -> None:
    """N1 (Plan 12.7): die Zähler der Peer-Suche als feste Zeile, Zahlen aus dem Code."""
    ctx, proposal = _proposal_with_ctx([])
    counts = ctx.candidate_sets[proposal.candidate_set_id].counts
    assert counts and counts["returned"] == len(ctx.candidate_sets[proposal.candidate_set_id].candidates)
    text = cli.render_proposal(proposal, ctx)
    line = next(x for x in text.splitlines() if x.startswith("Peer-Suche:"))
    for key in ("sic_matches", "without_ticker", "not_in_revenue_frame", "outside_size_range", "passed_filters", "returned"):
        assert str(counts[key]) in line
    assert f"{counts['sic_matches']} SIC-Treffer" in line and f"{counts['returned']} gezeigt" in line
    assert "Periodenende des Ziels: 25.01.2026" in text


def test_the_proposal_without_a_candidate_set_says_the_target_is_not_determinable() -> None:
    text = cli.render_proposal(_proposal(candidate_set_id="cs_fehlt"), _empty_ctx())
    assert text.splitlines()[:2] == ["Vorschlag für das Peer-Set:", "Ziel: nicht ermittelbar"]
