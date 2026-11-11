"""Schutz vor und nach einem Live-Lauf (Plan 12.10, N2): Logging mit Schwärzung und Scan der Ausgaben.

Ein eigenes Modul, weil CLI und Runner (`eval/run_scenarios.py`) dasselbe brauchen; die Anbindung an die CLI steht noch aus
(Restpunkt vor deren Live-Modus). Hier steht kein Netz- und kein SDK-Zugriff, und nirgends ein Secret-Wert in einer Ausgabe.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from scout_research.config import secret_value
from scout_research.tools.sanitize import RedactingFilter, Redactor

SDK_LOGGERS = ("anthropic", "httpx", "httpx2")
MIN_SECRET_LEN = 8
"""Kürzere Werte werden beim Scan ignoriert: Sie träfen zufällig und belegten kein Leck."""
ANTHROPIC_LOG_WARNING = "Warnung: ANTHROPIC_LOG ist gesetzt; das SDK-Logging kann mehr ausgeben als vorgesehen (Plan 12.10)."
_HANDLER_MARK = "_scout_live_guard"
_saved_levels: dict[str, int] = {}


def setup_logging(redactor: Redactor, *, stream: TextIO | None = None, log_path: Path | None = None,
                  env: Mapping[str, str] | None = None) -> list[logging.Handler]:
    """SDK-Logger auf `WARNING`; je Ausgabe ein Handler am Root-Logger mit `RedactingFilter` und Formatter `%(message)s`.

    Der Filter hängt am Handler, nicht am Logger: Er wirkt dadurch auf Einträge jedes Loggers (12.10). Ein früherer Aufruf wird
    zuerst zurückgenommen. Gibt die Handler zurück (für `teardown_logging`). Ist `ANTHROPIC_LOG` gesetzt, steht eine feste Warnung
    auf stderr."""
    teardown_logging()
    for name in SDK_LOGGERS:
        logger = logging.getLogger(name)
        _saved_levels.setdefault(name, logger.level)
        logger.setLevel(logging.WARNING)
    handlers: list[logging.Handler] = [logging.StreamHandler(stream or sys.stderr)]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    root = logging.getLogger()
    for handler in handlers:
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(RedactingFilter(redactor))
        handler.setLevel(logging.WARNING)
        setattr(handler, _HANDLER_MARK, True)
        root.addHandler(handler)
    if "ANTHROPIC_LOG" in (os.environ if env is None else env):
        print(ANTHROPIC_LOG_WARNING, file=sys.stderr)
    return handlers


def teardown_logging() -> None:
    """Nimmt die Handler und die Pegel der SDK-Logger von `setup_logging` zurück und schließt die Handler."""
    for name, level in _saved_levels.items():
        logging.getLogger(name).setLevel(level)
    _saved_levels.clear()
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(handler)
        handler.close()


def secrets_of(settings: Any) -> list[str]:
    """Die Secrets eines Live-Laufs aus den Settings: beide Schlüssel, Kontaktadresse, User-Agent (leere entfallen)."""
    values = (secret_value(settings.anthropic_api_key), secret_value(settings.finnhub_api_key), settings.edgar_contact_email,
              settings.edgar_user_agent)
    return [v for v in values if isinstance(v, str) and v]


@dataclass(frozen=True)
class Finding:
    path: Path
    kind: str
    """`secret` (Wert gefunden), `unreadable` (nicht prüfbar) oder `link` (Symlink, nicht gefolgt, nicht geprüft)."""


def scan_findings(paths: Iterable[Path], secrets: Iterable[str]) -> list[Finding]:
    """Alle Dateien unter den Pfaden (rekursiv, Symlinks werden nicht verfolgt) auf die wörtlichen Secret-Werte durchsuchen.
    Gibt Fundstellen ohne Wert zurück. Werte unter `MIN_SECRET_LEN` Zeichen werden ignoriert. Was sich nicht prüfen lässt (nicht
    lesbare Datei, Symlink), wird als eigene Art gemeldet (fail closed). Ohne prüfbare Secrets entfällt der Scan."""
    needles = sorted({s.encode("utf-8") for s in secrets if s and len(s) >= MIN_SECRET_LEN})
    if not needles:
        return []
    candidates: list[Path] = []
    findings: list[Finding] = []
    for path in map(Path, paths):
        if path.is_symlink():
            findings.append(Finding(path, "link"))
        elif path.is_dir():
            def unreadable(error: OSError) -> None:
                findings.append(Finding(Path(error.filename or path), "unreadable"))

            for root, dirs, files in os.walk(path, followlinks=False, onerror=unreadable):
                for name in sorted(dirs + files):
                    entry = Path(root) / name
                    if entry.is_symlink():
                        findings.append(Finding(entry, "link"))
                    elif name in files:
                        candidates.append(entry)
        elif path.is_file():
            candidates.append(path)
    for file in dict.fromkeys(sorted(candidates)):
        try:
            data = file.read_bytes()
        except OSError:
            findings.append(Finding(file, "unreadable"))
            continue
        if any(needle in data for needle in needles):
            findings.append(Finding(file, "secret"))
    return findings


def scan_outputs(paths: Iterable[Path], secrets: Iterable[str]) -> list[Path]:
    """Die betroffenen Dateien aus `scan_findings` (Fund, nicht prüfbar oder Link), nie ein Wert."""
    return [f.path for f in scan_findings(paths, secrets)]
