#!/usr/bin/env python3
"""Hook für Bash-Aufrufe (TA-20.4, E-05): Der Agent startet keine Live-Läufe.

Konfiguration: `.claude/settings.json` (PreToolUse, Matcher `Bash`).

Blockiert (Exit 2, fester Grund auf stderr) wird jeder Bash-Befehl, dessen ganze Zeichenkette enthält
    - `SCOUT_EVAL_LIVE`, unabhängig von Groß- und Kleinschreibung, oder
    - `--live` als eigenes Argument oder als `--live=…`.
Live-Läufe startet nur der Nutzer selbst in seiner Shell. Der Grund auf stderr nennt den Ausweg für bloßes Erwähnen: eine
Commit-Nachricht mit dem Write-Werkzeug in eine Datei schreiben und `git commit -F <datei>`, zum Suchen das Grep-Werkzeug.

Die Prüfung läuft über die rohe Befehlszeichenkette, ohne Zerlegung in Segmente. Verkettungen (`&&`, `;`, `|`),
`bash -c "…"`, `env SCOUT_EVAL_LIVE=1 …`, `export …`, Zeilenumbrüche und Anführungszeichen fallen so von selbst auf.
Bewusst grob: Ein Befehl, der den Text nur erwähnt (etwa `grep -- --live README.md`), wird ebenfalls blockiert.

Regel für `--live`: `--live` darf weder von einem Wortzeichen oder `-` davor noch von einem Wortzeichen oder `-`
dahinter umgeben sein. Damit werden `--live`, `--live=1`, `"--live"` und `'--live'` erkannt, `--lively` und
`--live-xyz` (kommen heute nicht vor) und `---live` nicht. Der Marker `pytest -m live` hat kein `--` und passiert.

Bei unlesbarer oder unerwarteter Hook-Eingabe wird blockiert (Exit 2, wie im Push-Hook): kein JSON, nicht dekodierbare Bytes,
zu tief verschachteltes JSON, fehlendes `tool_input` oder `command`, `command` kein Text. Auch jeder andere Fehler im Hook
endet mit Exit 2, nie mit Exit 1 (Exit 1 ließe den Aufruf durch). Ein leerer Text als `command` startet nichts und passiert.

Grenzen: Der Hook sieht nur Bash-Aufrufe des Agenten, nicht das Terminal des Nutzers. Ein Aufruf über eine
Python-Datei, die intern `main(["--live"])` aufruft (etwa Write-Werkzeug und dann `python skript.py`), oder eine
zusammengesetzte Zeichenkette (`"--" + "live"`, `SCOUT_EVAL_$X`) wird nicht erkannt, ebenso Quoting oder Escapes mitten
im Wort (`--"live"`, Rückstrich vor dem `l`), die die Shell wieder zur Option zusammensetzt. Andere Werkzeuge als Bash (MCP-Werkzeuge,
die Code ausführen) und die Bedienung einer IDE sieht der Hook nicht. Abkürzungen der Option nimmt der Runner nicht an
(`allow_abbrev=False`). Eine Änderung an `.claude/settings*.json` oder am Hook selbst wirkt spätestens in der nächsten
Sitzung. Fehlt die Hook-Datei, endet `python3` mit Exit 2 und der Eintrag blockiert jeden Bash-Aufruf (fail-closed).
Fehlt dagegen `python3` oder ist es zu alt, endet der Aufruf nicht mit Exit 2, und der Bash-Aufruf läuft ungeprüft
durch (fail-open; vermutet nach dem dokumentierten Verhalten von Claude Code, nicht zur Laufzeit geprüft). Es ist ein Schutz gegen Versehen, keine Sicherheitsgrenze. Der tragende Schutz
ist, dass kein Key dort liegt, wo der Agent arbeitet.
"""

from __future__ import annotations

import json
import re
import sys

_LIVE_FLAG = re.compile(r"(?<![\w-])--live(?![\w-])")
_LIVE_ENV = re.compile(r"SCOUT_EVAL_LIVE", re.IGNORECASE)

_REASON = (
    "block_live_runs: Befehl enthält `--live` oder `SCOUT_EVAL_LIVE`. "
    "Live-Läufe startet nur der Nutzer selbst in seiner Shell, nicht der Agent. "
    "Zum Suchen das Grep-Werkzeug nehmen; eine Commit-Nachricht mit dem Write-Werkzeug in eine Datei schreiben "
    "und `git commit -F <datei>` aufrufen."
)


def is_live_run(command: str) -> bool:
    return bool(_LIVE_FLAG.search(command) or _LIVE_ENV.search(command))


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read())
        command = payload["tool_input"]["command"]  # fehlt etwas oder hat es die falsche Form: Fehler, also blockieren
        if not isinstance(command, str):
            raise TypeError("command ist kein Text")
    except Exception:  # noqa: BLE001 - jede unerwartete Eingabe blockiert (fail-closed), auch RecursionError und UnicodeDecodeError
        print("block_live_runs: Hook-Eingabe nicht lesbar; Bash-Aufruf aus Sicherheitsgründen blockiert.", file=sys.stderr)
        return 2

    if is_live_run(command):
        print(_REASON, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:  # noqa: BLE001 - nie mit Exit 1 enden; nur Exit 2 blockiert
        code = 2
    sys.exit(code)
