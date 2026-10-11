"""Hook .claude/hooks/require_push_gate.py: Eingabe als JSON wie von Claude Code, Wirkung über Exit-Code."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "require_push_gate.py"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    for key, value in (("user.email", "a@example.invalid"), ("user.name", "T")):
        subprocess.run(["git", "config", key, value], cwd=tmp_path, check=True)
    (tmp_path / "f").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _head(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def _marker(repo: Path, head: str | None = None, age_minutes: int = 1) -> Path:
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = repo / ".git" / "push_gate_ok"
    path.write_text(f"{head or _head(repo)} {stamp}\n", encoding="utf-8")
    return path


def _run(repo: Path, command: str, mode: str = "pre", raw: str | None = None) -> subprocess.CompletedProcess[str]:
    payload = raw if raw is not None else json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": command}}
    )
    return subprocess.run([sys.executable, str(HOOK), mode], input=payload, capture_output=True, text=True, check=False)


def test_push_without_marker_is_blocked_with_exit_2_and_a_reason(repo: Path) -> None:
    done = _run(repo, "git push --dry-run origin main")
    assert done.returncode == 2
    assert "fehlt" in done.stderr and "push_gate.py" in done.stderr


def test_push_with_a_valid_marker_passes_including_dry_run(repo: Path) -> None:
    _marker(repo)
    assert _run(repo, "git push --dry-run origin main").returncode == 0
    assert _run(repo, "git push origin main").returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "git push --force origin main",
        "git push -f origin main",
        "git push origin main --force-with-lease",
        "git push --force-with-lease=main origin main",
        "git push -fu origin main",
        "git push origin +main",
        "git push origin HEAD:+main",
        "git push --mirror",
        "git push --force-if-includes origin main",
    ],
)
def test_forced_pushes_are_always_blocked_even_with_a_valid_marker(repo: Path, command: str) -> None:
    _marker(repo)
    done = _run(repo, command)
    assert done.returncode == 2 and "Erzwungener Push" in done.stderr


def test_chaining_the_gate_and_the_push_is_blocked_even_with_a_valid_marker(repo: Path) -> None:
    _marker(repo)
    for command in (
        ".venv/bin/python scripts/push_gate.py && git push origin main",
        ".venv/bin/python scripts/push_gate.py; git push origin main",
        "git push origin main || python scripts/push_gate.py",
    ):
        done = _run(repo, command)
        assert done.returncode == 2 and "getrennte Aufrufe" in done.stderr, command


def test_a_marker_older_than_30_minutes_is_blocked(repo: Path) -> None:
    _marker(repo, age_minutes=31)
    done = _run(repo, "git push origin main")
    assert done.returncode == 2 and "30 Minuten" in done.stderr
    _marker(repo, age_minutes=29)
    assert _run(repo, "git push origin main").returncode == 0


def test_a_marker_for_another_head_is_blocked(repo: Path) -> None:
    _marker(repo, head="0" * 40)
    done = _run(repo, "git push origin main")
    assert done.returncode == 2 and "HEAD" in done.stderr
    (repo / "f").write_text("y", encoding="utf-8")
    _marker(repo)
    subprocess.run(["git", "commit", "-q", "-am", "mehr"], cwd=repo, check=True)
    assert _run(repo, "git push origin main").returncode == 2  # neuer Commit nach dem Gate


def test_commands_that_only_mention_push_are_not_pushes(repo: Path) -> None:
    for command in (
        'git commit -m "docs: git push nur nach push_gate"',
        "echo 'git push --force'",
        "git status && git log --oneline",
        "git pull origin main",
        "grep -rn push scripts/",
    ):
        assert _run(repo, command).returncode == 0, command


@pytest.mark.parametrize(
    "command",
    [
        "git -C /tmp/anderswo push origin main",
        "bash -c 'git push origin main'",
        "cd x && git push origin main",
        "env FOO=1 git push origin main",
        "git status\ngit push origin main",
    ],
)
def test_pushes_in_other_forms_are_recognised(repo: Path, command: str) -> None:
    _marker(repo, head="1" * 40)  # ungültig
    assert _run(repo, command).returncode == 2, command


def test_git_dash_c_is_blocked_even_with_a_valid_marker(repo: Path) -> None:
    _marker(repo)
    done = _run(repo, "git -C /tmp/anderswo push origin main")
    assert done.returncode == 2 and "-C" in done.stderr


def test_post_hook_deletes_the_marker_after_a_real_push_but_not_after_a_dry_run(repo: Path) -> None:
    marker = _marker(repo)
    _run(repo, "git push --dry-run origin main", mode="post")
    assert marker.exists()
    _run(repo, "git status", mode="post")
    assert marker.exists()
    done = _run(repo, "git push origin main", mode="post")
    assert done.returncode == 0 and not marker.exists()


def test_unreadable_hook_input_fails_closed(repo: Path) -> None:
    assert _run(repo, "", raw="kein json").returncode == 2
