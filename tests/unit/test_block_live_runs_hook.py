"""Hook .claude/hooks/block_live_runs.py (TA-20.4, E-05): Eingabe als JSON wie von Claude Code, Wirkung über Exit-Code."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude" / "hooks" / "block_live_runs.py"
HOOK_ARG = "${CLAUDE_PROJECT_DIR}/.claude/hooks/block_live_runs.py"
PUSH_HOOK_ARG = "${CLAUDE_PROJECT_DIR}/.claude/hooks/require_push_gate.py"


def _run_raw(payload: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, check=False)


def _run(command: str) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}})
    done = _run_raw(payload.encode("utf-8"))
    return subprocess.CompletedProcess(done.args, done.returncode, done.stdout.decode("utf-8"), done.stderr.decode("utf-8"))


@pytest.mark.parametrize(
    "command",
    [
        "python -m eval.run_scenarios --live",
        ".venv/bin/python -m eval.run_scenarios --runs 3 --live",
        "SCOUT_EVAL_LIVE=1 python -m eval.run_scenarios",
        "env SCOUT_EVAL_LIVE=1 python -m eval.run_scenarios",
        "export SCOUT_EVAL_LIVE=1; python -m eval.run_scenarios",
        "cd x && python -m eval.run_scenarios --live",
        "git status | python -m eval.run_scenarios --live",
        'bash -c "python -m eval.run_scenarios --live"',
        "sh -c 'SCOUT_EVAL_LIVE=1 python -m eval.run_scenarios'",
        "git status\npython -m eval.run_scenarios --runs 3\npython -m eval.run_scenarios --live",
        "python -m eval.run_scenarios --live=1",
        'python -m eval.run_scenarios "--live"',
        "python -m eval.run_scenarios '--live=1'",
        "run\t--live\t",
        "scout_eval_live=1 python -m eval.run_scenarios",
        "Scout_Eval_Live=1 python -m eval.run_scenarios",
        "grep -- --live README.md",  # bewusst grob: auch bloßes Erwähnen wird blockiert
    ],
)
def test_live_runs_are_blocked_with_exit_2_and_a_reason(command: str) -> None:
    done = _run(command)
    assert done.returncode == 2, command
    assert "Live-Läufe startet nur" in done.stderr


def test_the_reason_names_the_way_out() -> None:
    reason = _run("python -m eval.run_scenarios --live").stderr
    assert "Grep-Werkzeug" in reason and "Write-Werkzeug" in reason and "git commit -F" in reason


@pytest.mark.parametrize(
    "command",
    [
        ".venv/bin/python -m pytest -q",
        "git status",
        "python -m eval.run_scenarios --runs 3",
        "git log --oneline",
        "ls live_guard.py",
        "pytest -m live",
        "pytest -m 'live and not slow'",
        "cat src/scout_research/live_guard.py",
        "run --lively",
        "run ---live",
        "run --live-xyz",
        "run x--live",
        "",  # leerer Text startet nichts
    ],
)
def test_ordinary_commands_pass(command: str) -> None:
    done = _run(command)
    assert done.returncode == 0, command
    assert done.stderr == ""


def _nested(depth: int) -> bytes:
    return b"[" * depth + b"]" * depth


@pytest.mark.parametrize(
    "raw",
    [
        b"kein json",
        b"",
        b"[1, 2]",
        b"{}",
        b'{"tool_input": {}}',
        b'{"tool_input": []}',
        b'{"tool_input": null}',
        b'{"tool_input": {"command": null}}',
        b'{"tool_input": {"command": 5}}',
        b'{"tool_input": {"command": 0}}',
        b'{"tool_input": {"command": false}}',
        b'{"tool_input": {"command": []}}',
        b'{"tool_input": {"command": {}}}',
        b'{"tool_input": {"command": ["--live"]}}',
        b"\xff",
        b'{"tool_input": {"command": "\xff"}}',
        _nested(100_000),
        b'{"tool_input": {"command": 1' + b"0" * 5000 + b"}}",
    ],
    ids=lambda raw: raw[:40].decode("latin-1"),
)
def test_unreadable_or_unexpected_hook_input_fails_closed_with_exit_2(raw: bytes) -> None:
    done = _run_raw(raw)
    assert done.returncode == 2, raw[:60]
    assert b"nicht lesbar" in done.stderr
    assert b"Traceback" not in done.stderr


def test_settings_register_the_hook_for_bash_and_keep_the_push_hook_entries() -> None:
    """Ohne Git, damit der Test nach dem Commit und im frischen Klon dasselbe prüft: feste Literale, feste Reihenfolge."""
    current = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    push_pre = {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3", "args": [PUSH_HOOK_ARG, "pre"]}]}
    ours = {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3", "args": [HOOK_ARG]}]}
    push_post = {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3", "args": [PUSH_HOOK_ARG, "post"]}]}
    assert set(current) == {"hooks"} and set(current["hooks"]) == {"PreToolUse", "PostToolUse"}
    assert len(current["hooks"]["PreToolUse"]) == 2
    assert current["hooks"]["PreToolUse"] == [push_pre, ours]
    assert current["hooks"]["PostToolUse"] == [push_post]
