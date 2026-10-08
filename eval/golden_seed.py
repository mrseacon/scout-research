"""Golden-Set-Seed: Laden, Validieren, Umrechnen (Foundation Doc 11.1, Plan Phase 3 Schritt 2).

Der Seed besteht aus von Hand geprüften Werten (Messlineal). Er ist an **Geschäftsjahresende
(`period_end`) und Accession Number des 10-K** gepinnt, nicht an "jüngstes 10-K". Werte stehen so in
der Datei, wie sie im 10-K stehen (`unit: millions|thousands|units`); `to_usd` rechnet exakt
(`Decimal`, keine Gleitkommafehler) in USD bzw. Stück um. Der Extraktor darf nie gegen eigene Ausgaben
gemessen werden — dieses Modul kennt ihn deshalb nicht.

    python eval/golden_seed.py            # Status aller Seed-Dateien
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

SEED_DIR = Path(__file__).parent / "golden_set"
STATUSES = ("skeleton", "complete")
UNIT_FACTORS = {"millions": Decimal(1_000_000), "thousands": Decimal(1_000), "units": Decimal(1)}
CORE_VALUES = (
    "revenue",
    "ebit",
    "net_income",
    "total_assets",
    "total_debt",
    "cash",
    "d_and_a",
    "shares_outstanding",
)
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_PLACEHOLDER = re.compile(r"todo|platzhalter|tbd|^\s*$", re.IGNORECASE)


def load_seed(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def seed_files() -> list[Path]:
    return sorted(SEED_DIR.glob("*.yaml"))


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_seed(data: dict) -> list[str]:
    """Liste der Mängel; leer = vollständig. Meldet jedes fehlende Feld einzeln."""
    problems: list[str] = []

    if data.get("status") not in STATUSES:
        problems.append(f"status muss einer von {STATUSES} sein")
    for key in ("ticker", "cik", "checked_by"):
        if _is_blank(data.get(key)):
            problems.append(f"{key} fehlt")
    if not isinstance(data.get("fiscal_year"), int) or isinstance(data.get("fiscal_year"), bool):
        problems.append("fiscal_year fehlt oder ist keine ganze Zahl")

    period_end = data.get("period_end")
    if _is_blank(period_end):
        problems.append("period_end fehlt (Pin)")
    else:
        try:
            date.fromisoformat(str(period_end))
        except ValueError:
            problems.append(f"period_end ist kein ISO-Datum (JJJJ-MM-TT): {period_end!r}")
    accession = data.get("accession_number")
    if _is_blank(accession):
        problems.append("accession_number fehlt (Pin)")
    elif not _ACCESSION.match(str(accession)):
        problems.append(f"accession_number hat nicht das Format 0000000000-00-000000: {accession!r}")
    if _is_blank(data.get("checked_on")):
        problems.append("checked_on fehlt")

    values = data.get("values")
    if not isinstance(values, dict) or not values:
        problems.append("values fehlt oder ist leer")
        return problems

    required = set(values) if data.get("scope") == "partial" else set(CORE_VALUES)
    for name in sorted(required - set(values)):
        problems.append(f"values.{name} fehlt")

    for name in sorted(required & set(values)):
        entry = values[name]
        if not isinstance(entry, dict):
            problems.append(f"values.{name} muss ein Mapping sein")
            continue
        value, reason = entry.get("value"), entry.get("absent_reason")
        if _is_blank(value) and _is_blank(reason):
            problems.append(f"values.{name}: weder value noch absent_reason")
        elif not _is_blank(value) and not _is_blank(reason):
            problems.append(f"values.{name}: value und absent_reason schließen sich aus")
        elif not _is_blank(value):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append(f"values.{name}.value muss eine Zahl sein (kein Text, keine Tausenderpunkte)")
            if entry.get("unit") not in UNIT_FACTORS:
                problems.append(f"values.{name}.unit muss einer von {tuple(UNIT_FACTORS)} sein")
        tolerance = entry.get("tolerance_abs")
        if tolerance is not None and (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or tolerance < 0):
            problems.append(f"values.{name}.tolerance_abs muss eine Zahl >= 0 sein (in der Einheit des Eintrags)")
        source = entry.get("source")
        if _is_blank(source) or _PLACEHOLDER.search(str(source)):
            problems.append(f"values.{name}.source fehlt oder ist noch ein Platzhalter")
        if name == "total_debt" and not _is_blank(value) and not isinstance(entry.get("is_lower_bound"), bool):
            problems.append("values.total_debt.is_lower_bound muss true oder false sein")
    problems += _validate_known_deviations(data)
    return problems


DEVIATION_CLASSES = ("a", "b", "c")
FLAG_FIELD = "total_debt_is_lower_bound"
_DEVIATION_KEYS = ("seed_value", "expected_extractor_value", "class", "reason")


def _validate_known_deviations(data: dict) -> list[str]:
    """`known_deviations` pinnt bekannte Abweichungen des Extraktors vom Seed. Je Feld: `seed_value` (muss dem
    Seed-Wert entsprechen), `expected_extractor_value` (muss davon abweichen), `class` (a Definitions-
    unterschied, b Extraktorfehler, c möglicher Seedfehler), `reason`. Zahlen stehen in der Einheit des
    Feldes (`unit`); für `total_debt_is_lower_bound` sind es Wahrheitswerte."""
    deviations = data.get("known_deviations")
    if deviations is None:
        return []
    if not isinstance(deviations, dict):
        return ["known_deviations muss ein Mapping Feld -> Eintrag sein"]

    problems: list[str] = []
    values = data.get("values") or {}
    for field, dev in deviations.items():
        label = f"known_deviations.{field}"
        is_flag = field == FLAG_FIELD
        base = values.get("total_debt" if is_flag else field)
        if not isinstance(base, dict):
            problems.append(f"{label}: Feld kommt in values nicht vor")
            continue
        if not isinstance(dev, dict):
            problems.append(f"{label} muss ein Mapping sein")
            continue
        for key in _DEVIATION_KEYS:
            if _is_blank(dev.get(key)) and not (key in ("seed_value", "expected_extractor_value") and dev.get(key) is False):
                problems.append(f"{label}.{key} fehlt")
        if dev.get("class") not in DEVIATION_CLASSES:
            problems.append(f"{label}.class muss einer von {DEVIATION_CLASSES} sein")
        if any(key not in dev for key in ("seed_value", "expected_extractor_value")):
            continue
        seed_value, expected = dev["seed_value"], dev["expected_extractor_value"]
        if is_flag:
            if not isinstance(seed_value, bool) or not isinstance(expected, bool):
                problems.append(f"{label}: seed_value und expected_extractor_value müssen true/false sein")
            elif seed_value != base.get("is_lower_bound"):
                problems.append(f"{label}.seed_value passt nicht zu values.total_debt.is_lower_bound")
            elif seed_value == expected:
                problems.append(f"{label}: expected_extractor_value gleicht seed_value — keine Abweichung")
            continue
        numeric = (
            isinstance(seed_value, (int, float)) and not isinstance(seed_value, bool)
            and isinstance(expected, (int, float)) and not isinstance(expected, bool)
        )
        if not numeric or _is_blank(base.get("value")):
            problems.append(f"{label}: seed_value/expected_extractor_value müssen Zahlen sein (und das Feld einen Wert haben)")
        elif Decimal(str(seed_value)) != Decimal(str(base["value"])):
            problems.append(f"{label}.seed_value passt nicht zu values.{field}.value")
        elif Decimal(str(expected)) == Decimal(str(seed_value)):
            problems.append(f"{label}: expected_extractor_value gleicht seed_value — keine Abweichung")
    return problems


def to_usd(entry: dict) -> Decimal | None:
    """Wert exakt in USD (bzw. Stück bei Shares). `None`, wenn laut Seed nicht gemeldet (`absent_reason`)."""
    if _is_blank(entry.get("value")):
        return None
    return Decimal(str(entry["value"])) * UNIT_FACTORS[entry["unit"]]


def tolerance_usd(entry: dict) -> Decimal:
    """Zulässige absolute Abweichung (gleiche Einheit wie `to_usd`); 0, wenn der Eintrag keine nennt."""
    return Decimal(str(entry.get("tolerance_abs", 0))) * UNIT_FACTORS[entry["unit"]]


def main() -> None:
    for path in seed_files():
        data = load_seed(path)
        problems = validate_seed(data)
        state = "vollständig" if not problems else f"unvollständig ({len(problems)} Mängel)"
        print(f"{path.name}: status={data.get('status')} — {state}")
        for problem in problems:
            print(f"    - {problem}")


if __name__ == "__main__":
    main()
