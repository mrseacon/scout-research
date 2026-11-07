"""Prompt-Varianten für den halb-live-Satz (Plan 12.12, TA-16b): nur der Runner lädt sie, nie der Produktivpfad.

Die Dateien liegen unter `eval/prompt_variants/`; der Produktiv-Loader (`scout_research.agent.prompt_loader`) kennt nur
`prompts/system.md` und nichts aus `eval/`. Kopf, Sprachersetzung und Hash werden wie beim aktiven Prompt abgeleitet
(Hash über den **gesendeten** Text); die Version trägt das Suffix der Variante (`1.2.0-turn_ended`), damit Trace-Kopf und Bericht
die Variante erkennen lassen. `variant="active"` liefert den Produktiv-Prompt unverändert. Eine Variante kann außerdem Tool-Hints ersetzen (`VARIANT_HINTS`,
`variant_overrides`), nur für die Dauer ihrer Läufe.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from scout_research.agent.prompt_loader import SystemPrompt, load_system_prompt
from scout_research.tools.errors import ERROR_SPECS

VARIANTS_DIR = Path(__file__).resolve().parent / "prompt_variants"
VARIANT_NAMES = ("turn_ended", "p8_host_turn")
PROMPT_VARIANTS = ("active", *VARIANT_NAMES)

_HEADER = re.compile(r"\A<!--\s*scout-system-prompt\s+v(\d+\.\d+\.\d+(?:-[a-z0-9_]+)?)(?=\s).*?-->\s*", re.DOTALL)
_LANGUAGE = {"de": "Deutsch", "en": "Englisch"}


def parse_prompt(raw: str, language: str) -> SystemPrompt:
    """Kopf entfernen, Sprache einsetzen, Version und Hash liefern (wie `load_system_prompt`, mit Variantensuffix)."""
    if language not in _LANGUAGE:
        raise ValueError(f"unbekannte Ausgabesprache {language!r}")
    header = _HEADER.match(raw)
    if header is None:
        raise ValueError("Der Systemprompt hat keinen Versionskopf")
    body = raw[header.end():].replace("{output_language}", _LANGUAGE[language])
    return SystemPrompt(body, header.group(1), hashlib.sha256(body.encode("utf-8")).hexdigest())


def load_variant_prompt(variant: str, language: str = "de") -> SystemPrompt:
    if variant == "active":
        return load_system_prompt(language)
    if variant not in VARIANT_NAMES:
        raise ValueError(f"unbekannte Prompt-Variante {variant!r}")
    return parse_prompt((VARIANTS_DIR / f"{variant}.md").read_text(encoding="utf-8"), language)


VARIANT_HINTS: dict[str, dict[str, str]] = {
    "turn_ended": {"TURN_ENDED": "In diesem Zug nicht mehr ausgeführt; auf eine neue Bitte des Nutzers ist der Aufruf wieder möglich."},
}
"""Ersatz-Hints je Variante (Code -> Hinweistext des Tool-Fehlers). Plan 12.12 meint beim `TURN_ENDED`-Hinweis den Hint in
`tools/errors.py`, nicht nur den Prompt-Satz; die Variante ändert beide gemeinsam. Unter `src/` ändert sich nichts."""


@contextmanager
def variant_overrides(variant: str) -> Iterator[None]:
    """Setzt die Ersatz-Hints der Variante in `ERROR_SPECS`, solange der Block läuft, und stellt sie danach wieder her (auch bei
    einer Ausnahme). `make_error` liest `ERROR_SPECS` bei jedem Aufruf; das Modell sieht den Ersatz im `tool_result`."""
    saved = dict(ERROR_SPECS)
    try:
        for code, hint in VARIANT_HINTS.get(variant, {}).items():
            ERROR_SPECS[code] = replace(ERROR_SPECS[code], hint=hint)
        yield
    finally:
        ERROR_SPECS.clear()
        ERROR_SPECS.update(saved)
