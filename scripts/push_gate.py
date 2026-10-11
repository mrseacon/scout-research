#!/usr/bin/env python3
"""Push-Gate (Plan, Abschnitt 13): deterministische Prüfungen vor jedem `git push`.

Aufruf aus der Repo-Wurzel, in einem **eigenen** Aufruf (nie verkettet mit `git push`):

    .venv/bin/python scripts/push_gate.py

Prüft: a) volle Suite (kein xfail/xpass, nicht mehr Skips als in `scripts/push_gate_baseline.json`),
b) sauberer Arbeitsstand, `.env` weder getrackt noch ungeschützt, c) nur die **hinzugefügten** Zeilen von
`git diff origin/main..HEAD` auf echte Geheimnisse, d) Commit-Liste. Bei Erfolg schreibt es
`<git-dir>/push_gate_ok` (HEAD-Hash und Zeitstempel); bei Misserfolg löscht es die Datei und endet mit Code 1.

Geheimnis-Treffer werden nur als „Treffer: Art, Datei:Zeile“ ausgegeben — nie der Wert.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "push_gate_baseline.json"
MARKER_NAME = "push_gate_ok"
MIN_SECRET_LENGTH = 4
PLACEHOLDER_VALUES = {"you@example.com"}
SECRET_KEYS = ("FINNHUB_API_KEY", "ANTHROPIC_API_KEY", "EDGAR_CONTACT_EMAIL")


@dataclass(frozen=True)
class Hit:
    kind: str
    path: str
    line: int

    def __str__(self) -> str:
        return f"Treffer: {self.kind}, {self.path}:{self.line}"


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True, text=True, check=False)


# --- c) Geheimnisse in hinzugefügten Zeilen -----------------------------------------------------------------------


def parse_env(path: Path) -> dict[str, str]:
    """Liest `KEY=value`-Zeilen ohne Kommentare; Anführungszeichen um den Wert werden entfernt."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def user_agent_from_config(env_path: Path) -> str | None:
    """Der User-Agent-Wert, gebildet von der Konfiguration selbst (`Settings.edgar_user_agent`)."""
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from scout_research.config import Settings

        return Settings(_env_file=str(env_path)).edgar_user_agent  # type: ignore[call-arg]
    except Exception:  # noqa: BLE001 — ohne Konfiguration lässt sich der Wert nicht bilden; der Aufrufer meldet das
        return None
    finally:
        sys.path.pop(0)


def collect_secrets(env: dict[str, str], user_agent: str | None, owner_email: str | None) -> list[tuple[str, str]]:
    """(Art, Wert)-Paare; zu kurze Werte und Platzhalter werden übersprungen."""
    candidates: list[tuple[str, str | None]] = [(key, env.get(key)) for key in SECRET_KEYS]
    candidates.append(("User-Agent-Wert", user_agent))
    candidates.append(("E-Mail des Repo-Besitzers", owner_email))
    seen: set[tuple[str, str]] = set()
    secrets: list[tuple[str, str]] = []
    for kind, value in candidates:
        if not value or len(value) < MIN_SECRET_LENGTH or value.lower() in PLACEHOLDER_VALUES:
            continue
        if (kind, value) not in seen:
            seen.add((kind, value))
            secrets.append((kind, value))
    return secrets


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def added_lines(diff: str):
    """Liefert (Datei, Zeilennummer, Text) für jede hinzugefügte Zeile eines `git diff -U0`."""
    path: str | None = None
    lineno = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:]
            path = None if target == "/dev/null" else target[2:] if target.startswith("b/") else target
        elif raw.startswith("--- ") or raw.startswith("diff --git"):
            continue
        elif raw.startswith("@@"):
            match = _HUNK.match(raw)
            lineno = int(match.group(1)) if match else 0
        elif raw.startswith("+") and path is not None:
            yield path, lineno, raw[1:]
            lineno += 1


def scan_added_lines(diff: str, secrets: list[tuple[str, str]]) -> list[Hit]:
    folded = [(kind, value.lower()) for kind, value in secrets]
    hits: list[Hit] = []
    for path, lineno, text in added_lines(diff):
        low = text.lower()
        for kind, value in folded:
            if value in low:
                hits.append(Hit(kind, path, lineno))
    return hits


# --- a) Suite ---------------------------------------------------------------------------------------------------

_COUNT = re.compile(r"(\d+) (passed|failed|skipped|xfailed|xpassed|errors?|deselected)")


def parse_summary(output: str) -> dict[str, int] | None:
    """Zählt aus der letzten pytest-Zusammenfassung (`=== 3 passed, 1 skipped in 2s ===` oder `-q`-Form)."""
    for line in reversed(output.splitlines()):
        found = _COUNT.findall(line)
        if found and (" in " in line or line.startswith("=")):
            counts: dict[str, int] = {}
            for number, word in found:
                counts["error" if word.startswith("error") else word] = int(number)
            return counts
    return None


def judge_suite(counts: dict[str, int] | None, exit_code: int, max_skipped: int) -> list[str]:
    if counts is None:
        return ["Zusammenfassung der Suite nicht lesbar"]
    problems: list[str] = []
    if exit_code != 0 or counts.get("failed", 0) or counts.get("error", 0):
        problems.append(f"Suite nicht grün (failed={counts.get('failed', 0)}, error={counts.get('error', 0)})")
    if counts.get("xfailed", 0) or counts.get("xpassed", 0):
        problems.append(f"xfail/xpass vorhanden (xfailed={counts.get('xfailed', 0)}, xpassed={counts.get('xpassed', 0)})")
    if counts.get("skipped", 0) > max_skipped:
        problems.append(f"mehr Skips als Baseline ({counts.get('skipped', 0)} > {max_skipped})")
    if not counts.get("passed", 0):
        problems.append("keine bestandenen Tests")
    return problems


def run_suite(max_skipped: int) -> tuple[list[str], str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rfEsxX", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    counts = parse_summary(proc.stdout)
    detail = [ln for ln in proc.stdout.splitlines() if ln.startswith(("FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS"))]
    summary = ", ".join(f"{n} {k}" for k, n in (counts or {}).items()) or "keine Zusammenfassung"
    return judge_suite(counts, proc.returncode, max_skipped), "\n".join([summary, *detail])


# --- b) Arbeitsstand --------------------------------------------------------------------------------------------


def check_worktree() -> list[str]:
    problems: list[str] = []
    status = git("status", "--porcelain").stdout.strip()
    if status:
        problems.append(f"Arbeitsstand nicht sauber ({len(status.splitlines())} Einträge)")
    if git("ls-files", ".env").stdout.strip():
        problems.append(".env ist getrackt")
    if git("check-ignore", "-q", ".env").returncode != 0:
        problems.append(".env steht nicht in .gitignore")
    return problems


# --- Marker -----------------------------------------------------------------------------------------------------


def marker_path() -> Path:
    return Path(git("rev-parse", "--absolute-git-dir").stdout.strip()) / MARKER_NAME


def write_marker(head: str, now: datetime | None = None) -> None:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker_path().write_text(f"{head} {stamp}\n", encoding="utf-8")


def clear_marker() -> None:
    marker_path().unlink(missing_ok=True)


# --- Ablauf -----------------------------------------------------------------------------------------------------


def main() -> int:
    failures: list[str] = []

    max_skipped = json.loads(BASELINE.read_text(encoding="utf-8"))["max_skipped"]
    suite_problems, suite_detail = run_suite(max_skipped)
    print(f"a) Suite: {'bestanden' if not suite_problems else 'NICHT bestanden'} — {suite_detail}")
    failures += suite_problems

    tree_problems = check_worktree()
    print(f"b) Arbeitsstand/.env: {'bestanden' if not tree_problems else 'NICHT bestanden'}")
    for problem in tree_problems:
        print(f"   {problem}")
    failures += tree_problems

    fetch = git("fetch", "-q", "origin")
    if fetch.returncode != 0:
        print("   Hinweis: git fetch fehlgeschlagen; origin/main kann veraltet sein")
    env_path = ROOT / ".env"
    owner = git("config", "user.email").stdout.strip() or None
    user_agent = user_agent_from_config(env_path)
    if user_agent is None:
        failures.append("User-Agent-Wert nicht bildbar (Konfiguration/.env unlesbar)")
    secrets = collect_secrets(parse_env(env_path), user_agent, owner)
    diff = git("diff", "-U0", "--no-color", "origin/main..HEAD")
    if diff.returncode != 0:
        failures.append("git diff origin/main..HEAD fehlgeschlagen")
    hits = scan_added_lines(diff.stdout, secrets)
    print(f"c) Geheimnisse in hinzugefügten Zeilen ({len(secrets)} Werte geprüft): "
          f"{'keine Treffer' if not hits else 'NICHT bestanden'}")
    for hit in hits:
        print(f"   {hit}")
    failures += [str(hit) for hit in hits]

    log = git("log", "--format=%h %an %s", "origin/main..HEAD").stdout.strip()
    print("d) Commits origin/main..HEAD:")
    print("\n".join(f"   {ln}" for ln in log.splitlines()) if log else "   (keine)")

    if failures:
        clear_marker()
        print("\nPush-Gate: FEHLGESCHLAGEN — kein Marker geschrieben.")
        return 1
    head = git("rev-parse", "HEAD").stdout.strip()
    write_marker(head)
    print(f"\nPush-Gate: bestanden für {head[:10]} — Marker geschrieben (30 Minuten gültig). Push in eigenem Aufruf.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
