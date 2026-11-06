"""Szenarien der Evaluation (Plan 12.12, TA-16a): Dateien `eval/scenarios/<scenario_id>.yaml`, streng geladen.

Ein Szenario nennt die gemessenen Verhalten B1 bis B13, die Welt (`eval/worlds.py`), die Sprache, die Nutzerzüge, die
Bestätigungsregel (wie `eval/cases`) und optional Erwartungen. Die Erwartungen steuern nichts im Lauf; sie sagen der
Auswertung (`eval/metrics.py`), was zu prüfen ist. Format: `eval/scenarios/README.md`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from eval.harness import Confirmation, parse_confirmation, string_list

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
BEHAVIORS = tuple(f"B{n}" for n in range(1, 14))
LANGUAGES = ("de", "en")
GATE_EXPECTATIONS = ("none", "confirmed", "rejected")
_SCENARIO_KEYS = {"scenario_id", "behaviors", "world", "output_language", "turns", "confirmation", "expect", "note"}
_EXPECT_KEYS = {"gate", "refusal", "clarification", "data_anomaly_mentioned"}
_SCENARIO_ID = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class Expect:
    gate: str | None = None
    """`none`: kein `ct_`-Handle und keine Bestätigung darf entstehen (hart); `confirmed`/`rejected`: Erwartung, nur Hinweis."""
    refusal: bool = False
    clarification: bool = False
    data_anomaly_mentioned: bool = False


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    behaviors: tuple[str, ...]
    """Der erste Eintrag ist das Hauptziel."""
    world: str
    output_language: str
    turns: tuple[str, ...]
    confirmation: Confirmation = Confirmation()
    expect: Expect = Expect()
    note: str = ""
    path: str = ""


def _parse_behaviors(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("behaviors: erwartet wird eine nicht leere Liste")
    unknown = [b for b in value if not isinstance(b, str) or b not in BEHAVIORS]
    if unknown:
        raise ValueError(f"behaviors: nur B1 bis B13 erlaubt, nicht {unknown!r}")
    if len(set(value)) != len(value):
        raise ValueError("behaviors: keine Dubletten")
    return tuple(value)


def _parse_expect(raw: Any) -> Expect:
    if not isinstance(raw, dict):
        raise ValueError("expect: erwartet wird ein Objekt")
    unknown = sorted(str(k) for k in raw.keys() - _EXPECT_KEYS)
    if unknown:
        raise ValueError(f"expect: unbekannte Schlüssel {unknown}")
    gate = raw.get("gate")
    if gate is not None and gate not in GATE_EXPECTATIONS:
        raise ValueError(f"expect.gate muss einer von {GATE_EXPECTATIONS} sein, nicht {gate!r}")
    flags: dict[str, bool] = {}
    for key in ("refusal", "clarification", "data_anomaly_mentioned"):
        if key in raw:
            if not isinstance(raw[key], bool):
                raise ValueError(f"expect.{key}: erwartet wird true oder false")
            flags[key] = raw[key]
    return Expect(gate, **flags)


_GATE_ACTION = {"none": "none", "confirmed": "confirm", "rejected": "reject"}


def _check_gate_consistency(expect: Expect, confirmation: Confirmation) -> None:
    """`expect.gate` und `confirmation.action` müssen zusammenpassen: `none` mit `none`, `confirmed` mit `confirm`, `rejected`
    mit `reject`. Ohne `confirmation` gilt der Standard `confirm`; wer `gate: none` erwartet, schreibt `action: none` aus."""
    if expect.gate is not None and _GATE_ACTION[expect.gate] != confirmation.action:
        raise ValueError(f"expect.gate {expect.gate!r} passt nicht zu confirmation.action {confirmation.action!r}")


def load_scenario(path: str | Path) -> Scenario:
    """Lädt und validiert eine Szenariodatei streng. Unbekannte Schlüssel, falsche Typen und eine `scenario_id`, die nicht
    dem Dateinamen entspricht, werfen `ValueError` mit dem Feldnamen."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: erwartet wird ein Objekt")
    unknown = sorted(str(k) for k in data.keys() - _SCENARIO_KEYS)
    if unknown:
        raise ValueError(f"{path.name}: unbekannte Schlüssel {unknown}")
    scenario_id = data.get("scenario_id")
    if not isinstance(scenario_id, str) or _SCENARIO_ID.fullmatch(scenario_id) is None:
        raise ValueError("scenario_id: erwartet wird ein Text aus Buchstaben, Ziffern, Unterstrich und Bindestrich")
    if scenario_id != path.stem:
        raise ValueError(f"scenario_id {scenario_id!r} entspricht nicht dem Dateinamen {path.name!r}")
    behaviors = _parse_behaviors(data.get("behaviors"))
    world = data.get("world")
    if not isinstance(world, str) or not world.strip():
        raise ValueError("world: erwartet wird der Name einer Welt")
    language = data.get("output_language")
    if language not in LANGUAGES:
        raise ValueError(f"output_language muss einer von {LANGUAGES} sein, nicht {language!r}")
    turns = string_list(data.get("turns"), "turns", allow_empty=False)
    confirmation = parse_confirmation(data["confirmation"]) if "confirmation" in data else Confirmation()
    expect = _parse_expect(data["expect"]) if "expect" in data else Expect()
    note = data.get("note", "")
    if not isinstance(note, str):
        raise ValueError("note: erwartet wird ein Text")
    _check_gate_consistency(expect, confirmation)
    return Scenario(scenario_id, behaviors, world, language, turns, confirmation, expect, note, str(path))


def load_all(directory: Path = SCENARIOS_DIR) -> list[Scenario]:
    """Alle Szenarien des Ordners, nach Dateiname sortiert."""
    return [load_scenario(p) for p in sorted(Path(directory).glob("*.yaml"))]
