"""Lader des Systemprompts (Plan 12.1, N1): Kopf entfernen, Sprache einsetzen, Version und Hash liefern.

Die Datei `prompts/system.md` ist reines Datenverzeichnis ohne `__init__.py` und wird als Package-Data installiert
(`pyproject.toml`); geladen wird über `importlib.resources`. Der Platzhalter `{output_language}` wird per
`str.replace` ersetzt, nie per `str.format` (Slot-Beispiele enthalten geschweifte Klammern).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

_HEADER = re.compile(r"\A<!--\s*scout-system-prompt\s+v(\d+\.\d+\.\d+)\b.*?-->\s*", re.DOTALL)
_LANGUAGE = {"de": "Deutsch", "en": "Englisch"}


@dataclass(frozen=True)
class SystemPrompt:
    text: str
    version: str
    sha256: str
    """SHA-256 des **gesendeten** Texts (nach Ersetzung)."""


def load_system_prompt(language: str = "de") -> SystemPrompt:
    if language not in _LANGUAGE:
        raise ValueError(f"unbekannte Ausgabesprache {language!r}")
    raw = resources.files("scout_research.agent").joinpath("prompts/system.md").read_text(encoding="utf-8")
    header = _HEADER.match(raw)
    if header is None:
        raise ValueError("Der Systemprompt hat keinen Versionskopf")
    body = raw[header.end():].replace("{output_language}", _LANGUAGE[language])
    return SystemPrompt(body, header.group(1), hashlib.sha256(body.encode("utf-8")).hexdigest())


def tools_hash(tools: list[dict[str, Any]]) -> str:
    """SHA-256 der serialisierten Tool-Liste (N7; Trace-Kopf)."""
    blob = json.dumps(tools, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
