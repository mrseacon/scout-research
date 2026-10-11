#!/usr/bin/env python3
"""Hook für Bash-Aufrufe (Plan, Abschnitt 13): `git push` nur nach bestandenem `scripts/push_gate.py`.

Konfiguration: `.claude/settings.json` (PreToolUse und PostToolUse, Matcher `Bash`).

    require_push_gate.py pre    PreToolUse: blockiert (Exit 2, Meldung auf stderr), wenn
        - der Marker `<git-dir>/push_gate_ok` fehlt, älter als 30 Minuten ist oder nicht zum aktuellen HEAD passt,
        - der Push erzwungen wird (`--force`, `-f`, `--force-with-lease`, `--force-if-includes`, `+refspec`,
          `--mirror`) — immer,
        - derselbe Befehl `push_gate.py` und `git push` verkettet,
        - `git -C`/`--git-dir`/`--work-tree` den Push aus einem anderen Verzeichnis lenkt.
    require_push_gate.py post   PostToolUse (läuft nur nach erfolgreichem Aufruf): löscht den Marker nach einem
        echten Push (nicht nach `--dry-run`).

Grenzen: Der Hook sieht nur Bash-Aufrufe des Agenten, nicht das Terminal des Nutzers, und erkennt `git push` nur in
der Form `git [Optionen] push`; Aliasse oder Skripte, die intern pushen, erkennt er nicht.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

MARKER_NAME = "push_gate_ok"
MAX_AGE = timedelta(minutes=30)

_SEPARATORS = ";&|\n()`"
_WRAPPERS = {"env", "command", "time", "nohup", "sudo", "exec", "builtin", "nice", "xargs"}
_SHELLS = {"bash", "sh", "zsh", "dash"}
_GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
_REDIRECT_OR_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass
class Push:
    args: list[str]
    global_opts: list[str] = field(default_factory=list)

    @property
    def dry_run(self) -> bool:
        return any(a == "--dry-run" or (a.startswith("-") and not a.startswith("--") and "n" in a[1:]) for a in self.args)

    def force_reason(self) -> str | None:
        for arg in self.args:
            if arg in {"--force", "--force-if-includes", "--mirror"} or arg.startswith("--force-with-lease"):
                return arg.split("=")[0]
            if re.fullmatch(r"-[A-Za-z]*f[A-Za-z]*", arg):
                return "-f"
            if arg.startswith("+") or ":+" in arg:
                return "+refspec"
        return None


def split_segments(command: str) -> list[list[str]]:
    """Zerlegt einen Shell-Befehl in Segmente aus Wörtern; Anführungszeichen schützen Trenner und Wörter."""
    segments: list[list[str]] = [[]]
    word: list[str] = []
    in_word = False
    quote: str | None = None
    i = 0

    def end_word() -> None:
        nonlocal in_word
        if in_word:
            segments[-1].append("".join(word))
            word.clear()
            in_word = False

    while i < len(command):
        ch = command[i]
        if quote == "'":
            if ch == "'":
                quote = None
            else:
                word.append(ch)
        elif quote == '"':
            if ch == "\\" and i + 1 < len(command):
                i += 1
                word.append(command[i])
            elif ch == '"':
                quote = None
            else:
                word.append(ch)
        elif ch in "'\"":
            quote, in_word = ch, True
        elif ch == "\\" and i + 1 < len(command):
            i += 1
            word.append(command[i])
            in_word = True
        elif ch in _SEPARATORS:
            end_word()
            if segments[-1]:
                segments.append([])
        elif ch.isspace():
            end_word()
        else:
            word.append(ch)
            in_word = True
        i += 1
    end_word()
    return [s for s in segments if s]


def find_pushes(command: str, depth: int = 0) -> list[Push]:
    pushes: list[Push] = []
    for tokens in split_segments(command):
        i = 0
        while i < len(tokens) and (_REDIRECT_OR_ASSIGN.match(tokens[i]) or Path(tokens[i]).name in _WRAPPERS):
            i += 1
        rest = tokens[i:]
        if not rest:
            continue
        name = Path(rest[0]).name
        if depth < 3 and name in _SHELLS:
            for j, tok in enumerate(rest[1:], start=1):
                if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", tok) and j + 1 < len(rest):
                    pushes += find_pushes(rest[j + 1], depth + 1)
            continue
        if depth < 3 and name == "eval":
            pushes += find_pushes(" ".join(rest[1:]), depth + 1)
            continue
        if name != "git":
            continue
        j, opts = 1, []
        while j < len(rest) and rest[j].startswith("-"):
            if rest[j] in _GIT_OPTS_WITH_VALUE and j + 1 < len(rest):
                opts += rest[j : j + 2]
                j += 2
            else:
                opts.append(rest[j])
                j += 1
        if j < len(rest) and rest[j] == "push":
            pushes.append(Push(rest[j + 1 :], opts))
    return pushes


def _git(cwd: str, *args: str) -> str | None:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def marker_problem(cwd: str, now: datetime | None = None) -> str | None:
    """None, wenn der Marker gültig ist; sonst der Grund."""
    git_dir = _git(cwd, "rev-parse", "--absolute-git-dir")
    head = _git(cwd, "rev-parse", "HEAD")
    if git_dir is None or head is None:
        return "Git-Verzeichnis oder HEAD nicht lesbar"
    marker = Path(git_dir) / MARKER_NAME
    if not marker.is_file():
        return f"Marker .git/{MARKER_NAME} fehlt"
    parts = marker.read_text(encoding="utf-8").split()
    if len(parts) != 2:
        return "Marker unlesbar"
    marker_head, stamp = parts
    try:
        written = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return "Zeitstempel im Marker unlesbar"
    if (now or datetime.now(timezone.utc)) - written > MAX_AGE:
        return "Marker ist älter als 30 Minuten"
    if marker_head != head:
        return "Marker passt nicht zum aktuellen HEAD"
    return None


def decide(command: str, cwd: str, now: datetime | None = None) -> list[str]:
    """Liste der Gründe, den Befehl zu blockieren; leer = erlaubt."""
    pushes = find_pushes(command)
    if not pushes:
        return []
    reasons: list[str] = []
    for push in pushes:
        force = push.force_reason()
        if force:
            reasons.append(f"Erzwungener Push ({force}) ist immer blockiert.")
        if any(o in {"-C", "--git-dir", "--work-tree"} or o.startswith("--git-dir=") for o in push.global_opts):
            reasons.append("Push mit -C/--git-dir/--work-tree ist blockiert; push aus dem Projektverzeichnis.")
    if "push_gate.py" in command:
        reasons.append("push_gate.py und git push in einem Aufruf: getrennte Aufrufe, erst das Gate, dann der Push.")
    problem = marker_problem(cwd, now)
    if problem:
        reasons.append(f"{problem}. Zuerst `.venv/bin/python scripts/push_gate.py` in eigenem Aufruf ausführen.")
    return reasons


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "pre"
    try:
        payload = json.load(sys.stdin)
        command = payload.get("tool_input", {}).get("command") or ""
        cwd = payload.get("cwd") or os.getcwd()
    except (json.JSONDecodeError, AttributeError):
        print("require_push_gate: Hook-Eingabe nicht lesbar; Bash-Aufruf aus Sicherheitsgründen blockiert.", file=sys.stderr)
        return 2

    if mode == "post":
        pushes = find_pushes(command)
        if pushes and not any(p.dry_run for p in pushes):
            git_dir = _git(cwd, "rev-parse", "--absolute-git-dir")
            if git_dir:
                (Path(git_dir) / MARKER_NAME).unlink(missing_ok=True)
        return 0

    reasons = decide(command, cwd)
    if reasons:
        print("git push blockiert:\n- " + "\n- ".join(reasons), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
