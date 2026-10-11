"""scripts/push_gate.py: Geheimnisprüfung nur über hinzugefügte Zeilen, Ausgabe ohne Wert, Marker."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import push_gate

# Erfundene Werte; keine echten Schlüssel oder Adressen.
FINNHUB = "fh_FAKE_0123456789abcdef"
ANTHROPIC = "sk-ant-FAKE-9876543210zyxwvu"
CONTACT = "kontakt.fake@example.invalid"
OWNER = "besitzer.fake@example.invalid"
USER_AGENT = f"Fake Projekt ({CONTACT})"
ENV = {
    "FINNHUB_API_KEY": FINNHUB,
    "ANTHROPIC_API_KEY": ANTHROPIC,
    "EDGAR_CONTACT_EMAIL": CONTACT,
    "EDGAR_APP_NAME": "Fake Projekt",
}
VALUES = (FINNHUB, ANTHROPIC, CONTACT, OWNER, USER_AGENT)


def _secrets() -> list[tuple[str, str]]:
    return push_gate.collect_secrets(ENV, USER_AGENT, OWNER)


def _diff(path: str, *added: str, start: int = 1, removed: tuple[str, ...] = ()) -> str:
    body = "".join(f"-{r}\n" for r in removed) + "".join(f"+{a}\n" for a in added)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,{len(removed)} +{start},{len(added)} @@\n{body}"
    )


def test_each_kind_of_secret_is_found_with_file_and_line_but_the_report_never_contains_the_value() -> None:
    diff = _diff(
        "src/mod.py",
        "x = 1",
        f"key = '{FINNHUB}'",
        f"other = '{ANTHROPIC}'",
        f"mail = '{CONTACT}'",
        f"owner = '{OWNER}'",
        start=10,
    )
    hits = push_gate.scan_added_lines(diff, _secrets())
    kinds = {(h.kind, h.path, h.line) for h in hits}
    assert ("FINNHUB_API_KEY", "src/mod.py", 11) in kinds
    assert ("ANTHROPIC_API_KEY", "src/mod.py", 12) in kinds
    assert ("EDGAR_CONTACT_EMAIL", "src/mod.py", 13) in kinds
    assert ("E-Mail des Repo-Besitzers", "src/mod.py", 14) in kinds
    for hit in hits:
        text = str(hit)
        assert text.startswith("Treffer: ")
        assert not any(value.lower() in text.lower() for value in VALUES)


def test_the_user_agent_value_is_found_but_the_word_and_the_public_project_name_are_not() -> None:
    clean = _diff("a.md", "Der User-Agent enthält den Projektnamen Fake Projekt und Scout Research.", "you@example.com")
    assert push_gate.scan_added_lines(clean, _secrets()) == []
    dirty = _diff("a.md", f"UA: {USER_AGENT}")
    kinds = {h.kind for h in push_gate.scan_added_lines(dirty, _secrets())}
    assert "User-Agent-Wert" in kinds


def test_removed_and_context_lines_are_ignored_only_added_lines_count() -> None:
    diff = (
        "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
        f"@@ -1,2 +1,2 @@\n-old = '{FINNHUB}'\n+new = 'harmlos'\n"
    )
    assert push_gate.scan_added_lines(diff, _secrets()) == []


def test_matching_is_case_insensitive_and_reports_the_new_file_line_numbers() -> None:
    diff = _diff("f.txt", "a", "b", f"c {FINNHUB.upper()}", start=40)
    assert [(h.path, h.line) for h in push_gate.scan_added_lines(diff, _secrets())] == [("f.txt", 42)]


def test_short_values_and_the_placeholder_are_not_used_as_secrets() -> None:
    secrets = push_gate.collect_secrets(
        {"FINNHUB_API_KEY": "abc", "ANTHROPIC_API_KEY": "", "EDGAR_CONTACT_EMAIL": "you@example.com"}, None, None
    )
    assert secrets == []


def test_author_metadata_is_not_part_of_the_scanned_text() -> None:
    # `git diff` enthält keine Autoren-Metadaten; der Scan arbeitet nur auf Diff-Zeilen
    diff = _diff("f.txt", "nichts Besonderes")
    assert push_gate.scan_added_lines(diff, _secrets()) == []


def test_env_file_parsing_ignores_comments_and_strips_quotes(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(f"# Kommentar\nFINNHUB_API_KEY=\"{FINNHUB}\"\n\nLEER=\nANTHROPIC_API_KEY={ANTHROPIC}\n", encoding="utf-8")
    parsed = push_gate.parse_env(env)
    assert parsed["FINNHUB_API_KEY"] == FINNHUB and parsed["ANTHROPIC_API_KEY"] == ANTHROPIC and parsed["LEER"] == ""


@pytest.mark.parametrize(
    ("output", "exit_code", "max_skipped", "expect_problem"),
    [
        ("847 passed in 5.39s", 0, 0, False),
        ("846 passed, 1 skipped in 5s", 0, 0, True),
        ("846 passed, 1 skipped in 5s", 0, 1, False),
        ("845 passed, 1 xfailed in 5s", 0, 0, True),
        ("845 passed, 1 xpassed in 5s", 0, 0, True),
        ("1 failed, 846 passed in 5s", 1, 0, True),
        ("no tests ran in 0.01s", 5, 0, True),
    ],
)
def test_suite_verdict(output: str, exit_code: int, max_skipped: int, expect_problem: bool) -> None:
    problems = push_gate.judge_suite(push_gate.parse_summary(output), exit_code, max_skipped)
    assert bool(problems) is expect_problem


def test_unreadable_suite_output_fails_closed() -> None:
    assert push_gate.judge_suite(push_gate.parse_summary("völlig anderer Text"), 0, 0)


def test_the_baseline_file_is_valid_and_zero_skips_today() -> None:
    import json

    assert json.loads(push_gate.BASELINE.read_text(encoding="utf-8")) == {"max_skipped": 0}


# --- Ende-zu-Ende in einem Wegwerf-Repo (Suite und User-Agent werden ersetzt) -------------------------------------


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    _git(work, "config", "user.email", OWNER)
    _git(work, "config", "user.name", "Fake")
    _git(work, "checkout", "-q", "-b", "main")
    (work / ".gitignore").write_text(".env\n", encoding="utf-8")
    (work / ".env").write_text("\n".join(f"{k}={v}" for k, v in ENV.items()) + "\n", encoding="utf-8")
    (work / "a.txt").write_text("start\n", encoding="utf-8")
    _git(work, "add", ".gitignore", "a.txt")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "push", "-q", "origin", "main")
    monkeypatch.setattr(push_gate, "ROOT", work)
    monkeypatch.setattr(push_gate, "run_suite", lambda max_skipped: ([], "1 passed"))
    monkeypatch.setattr(push_gate, "user_agent_from_config", lambda env_path: USER_AGENT)
    return work


def test_gate_passes_and_writes_a_marker_with_head_and_timestamp(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "a.txt").write_text("harmlos\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "harmlos")
    assert push_gate.main() == 0
    marker = repo / ".git" / "push_gate_ok"
    head, stamp = marker.read_text(encoding="utf-8").split()
    assert head == _git(repo, "rev-parse", "HEAD") and stamp.endswith("Z")
    assert "harmlos" in capsys.readouterr().out  # Commit-Liste


def test_gate_fails_on_a_secret_reports_only_kind_file_line_and_removes_the_marker(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / ".git" / "push_gate_ok").write_text("alt 2020-01-01T00:00:00Z\n", encoding="utf-8")
    (repo / "config.py").write_text(f"x = 1\nTOKEN = '{FINNHUB}'\n", encoding="utf-8")
    _git(repo, "add", "config.py")
    _git(repo, "commit", "-q", "-m", "leak")
    assert push_gate.main() == 1
    out = capsys.readouterr().out
    assert "Treffer: FINNHUB_API_KEY, config.py:2" in out
    assert not any(value in out for value in VALUES)
    assert not (repo / ".git" / "push_gate_ok").exists()


def test_gate_fails_on_a_dirty_tree(repo: Path) -> None:
    (repo / "neu.txt").write_text("ungetrackt\n", encoding="utf-8")
    assert push_gate.main() == 1
    assert not (repo / ".git" / "push_gate_ok").exists()


def test_gate_fails_when_env_is_tracked(repo: Path) -> None:
    _git(repo, "add", "-f", ".env")
    _git(repo, "commit", "-q", "-m", "env")
    assert push_gate.main() == 1
