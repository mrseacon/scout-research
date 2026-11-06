"""Kostenschätzer: rechnet Token mit vom Nutzer gepflegten Preisen in US-Dollar um (TA-15).

    python scripts/estimate_cost.py --trace runs/2026-11-06/x.jsonl [...]
    python scripts/estimate_cost.py --runs 39 --calls-per-run 6 --input-tokens 20000 --output-tokens 1500

Die Ausgabe ist eine SCHÄTZUNG, keine Abrechnung. Die Preise stehen in `config/prices.toml` und werden **nicht** vom
Skript oder aus dem Code übernommen; fehlt ein Wert, bricht das Skript ab und rechnet nie mit Ersatzwerten.
Kein Netzwerk, kein Modellaufruf.
"""

from __future__ import annotations

import argparse
import math
import sys
import tomllib
from datetime import date
from pathlib import Path

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from scout_research.agent.budgets import PRICES, LoopConfig  # noqa: E402
from scout_research.agent.trace import validate_trace_file  # noqa: E402
from scout_research.config import Settings  # noqa: E402

PRICE_FIELDS = ("input", "output", "cache_read", "cache_write")
ENTRY_KEYS = frozenset(("source", "checked", *PRICE_FIELDS))
DEFAULT_PRICES = ROOT / "config" / "prices.toml"
_KNOB_FIELDS = ("anthropic_model", "anthropic_max_tokens", "budget_run_usd", "budget_day_usd")


class PricesError(Exception):
    """Preisdatei, Konfiguration oder Trace-Datei sind unbrauchbar; die Meldung nennt nie Werte aus der `.env`."""


class Knobs(BaseSettings):
    """Die Felder aus `Settings`, die das Skript braucht (Modell, `max_tokens`, Deckel). Gleiche Quellen (Umgebung,
    `.env`), gleiche Defaults (aus `Settings.model_fields`), aber ohne `EDGAR_CONTACT_EMAIL` zu verlangen."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_model: str = Settings.model_fields["anthropic_model"].default
    anthropic_max_tokens: int = Settings.model_fields["anthropic_max_tokens"].default
    budget_run_usd: float = Settings.model_fields["budget_run_usd"].default
    budget_day_usd: float = Settings.model_fields["budget_day_usd"].default

    @field_validator(*_KNOB_FIELDS, mode="before")
    @classmethod
    def _blank_means_default(cls, value: object, info) -> object:
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value

    @field_validator("anthropic_max_tokens")
    @classmethod
    def _max_tokens(cls, value: int) -> int:
        if value < 256:
            raise ValueError("mindestens 256")
        return value

    @field_validator("budget_run_usd", "budget_day_usd")
    @classmethod
    def _positive(cls, value: float) -> float:
        if not (value > 0 and math.isfinite(value)):
            raise ValueError("endlich und größer als 0")
        return value


def load_knobs() -> Knobs:
    """Liest Umgebung und `.env`; bei ungültigen Werten nennt der Abbruch nur die Feldnamen, nie die Werte."""
    try:
        return Knobs()
    except ValidationError as exc:
        fields = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err["loc"]})
        raise PricesError(f"Ungültige Konfiguration ({', '.join(fields)}): Wert muss gültig, endlich und positiv "
                          "sein. Keine Rechnung mit Ersatzwerten.") from None


def _incomplete(path: Path, field: str, why: str) -> PricesError:
    return PricesError(f"Preise in {path} sind nicht vollständig gepflegt: {field} {why}. "
                       "Keine Rechnung mit Ersatzwerten.")


def load_prices(path: Path, model: str) -> dict:
    """Liest die Preise eines Modells; jede Lücke ist ein Fehler."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        raise PricesError(f"Preisdatei {path} nicht gefunden.") from None
    except (tomllib.TOMLDecodeError, OSError, ValueError):
        raise PricesError(f"Preisdatei {path} nicht lesbar (kein gültiges TOML in UTF-8).") from None
    models = data.get("models")
    unknown_top = sorted(set(data) - {"models"})
    if unknown_top:
        raise PricesError(f"Preise in {path} sind nicht vollständig gepflegt: unbekannter Schlüssel "
                          f"{unknown_top[0]!r}. Keine Rechnung mit Ersatzwerten.")
    entry = models.get(model) if isinstance(models, dict) else None
    if not isinstance(entry, dict):
        raise PricesError(f"Preise in {path} sind nicht vollständig gepflegt: Modell {model!r} fehlt. "
                          "Keine Rechnung mit Ersatzwerten.")
    unknown = sorted(set(entry) - ENTRY_KEYS)
    if unknown:
        raise _incomplete(path, f"unbekannter Schlüssel {unknown[0]!r}", "(Tippfehler?)")
    out: dict = {}
    for field in ("source", "checked"):
        value = entry.get(field)
        if field == "checked" and isinstance(value, str) and value.strip():
            try:
                value = date.fromisoformat(value.strip())
            except ValueError:
                raise _incomplete(path, field, "ist kein Datum (JJJJ-MM-TT)") from None
        if isinstance(value, date):
            value = value.isoformat()
        if not isinstance(value, str) or not value.strip():
            raise _incomplete(path, field, "fehlt")
        out[field] = value.strip()
    for field in PRICE_FIELDS:
        if field not in entry:
            raise _incomplete(path, field, "fehlt")
        value = entry[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _incomplete(path, field, "ist keine Zahl")
        if not (math.isfinite(value) and value > 0):
            raise _incomplete(path, field, "muss endlich und positiv sein (> 0 verlangt)")
        out[field] = float(value)
    return out


def usd(tokens: dict[str, float], prices: dict) -> float:
    return sum(tokens[field] * prices[field] for field in PRICE_FIELDS) / 1_000_000


def read_traces(paths: list[Path]) -> dict:
    """Prüft jede Datei mit `validate_trace_file` und summiert Token aus `model_call` und `model_error`.

    Doppelt übergebene Dateien (gleicher aufgelöster Pfad oder gleiche Sitzung) zählen einmal."""
    keys = {"input": "input_tokens", "output": "output_tokens", "cache_read": "cache_read_input_tokens",
            "cache_write": "cache_creation_input_tokens"}
    result = {"totals": dict.fromkeys(PRICE_FIELDS, 0), "modes": set(), "models": set(), "calls": 0, "files": 0,
              "skipped": 0, "warnings": []}
    seen_paths: set[Path] = set()
    seen_sessions: set[tuple[str, str]] = set()
    for path in paths:
        resolved = Path(path).resolve()
        if resolved in seen_paths:
            result["skipped"] += 1
            continue
        seen_paths.add(resolved)
        try:
            events = validate_trace_file(resolved)
        except (ValueError, OSError):
            raise PricesError(f"Trace-Datei {path} ist defekt oder keine gültige Trace-Datei des Agenten "
                              "(Schema, Kopf, seq oder end). Keine Rechnung.") from None
        header, end = events[0], events[-1]
        session = (header.run_id, header.session_id)
        if session in seen_sessions:
            result["skipped"] += 1
            continue
        seen_sessions.add(session)
        result["files"] += 1
        result["modes"].add(header.run_mode)
        result["models"].add(header.model)
        counted = 0
        for event in events:
            if event.event in ("model_call", "model_error"):
                counted += 1
                for field, key in keys.items():
                    value = getattr(event, key)
                    if value is not None:
                        result["totals"][field] += value
        result["calls"] += counted
        if end.dropped_events > 0:
            result["warnings"].append(f"WARNUNG: {path.name}: {end.dropped_events} Ereignis(se) wurden nicht "
                                      "geschrieben; die Summe ist zu niedrig.")
        if end.model_calls != counted:
            result["warnings"].append(f"WARNUNG: {path.name}: Das Ende meldet {end.model_calls} Aufrufe, gezählt "
                                      f"wurden {counted}; die Summe ist unvollständig.")
    return result


def _money(value: float) -> str:
    return f"{value:,.4f} USD"


def _deviation(prices: dict, model: str) -> str | None:
    code = PRICES.get(model)
    if code is None:
        return None
    diff = [f for f in PRICE_FIELDS if not math.isclose(prices[f], getattr(code, f), rel_tol=1e-9, abs_tol=1e-12)]
    if not diff:
        return None
    return (f"WARNUNG: Der Loop rechnet den Budgetdeckel mit anderen Preisen (src/scout_research/agent/budgets.py: "
            f"{', '.join(diff)}). Deckel und Schätzung weichen dann voneinander ab; die Code-Tabelle wird hier nicht "
            "geändert.")


def _head(path: Path, prices: dict) -> list[str]:
    return [f"SCHÄTZUNG – keine Abrechnung. Preise aus {path} (Quelle: {prices['source']}, "
            f"geprüft: {prices['checked']})."]


def estimate_assumptions(args, prices: dict, config: LoopConfig, knobs: Knobs) -> list[str]:
    calls, runs, turns_per_run = args.calls_per_run, args.runs, args.turns_per_run
    share = args.cache_read_share
    per_call = {"input": args.input_tokens * (1 - share), "output": args.output_tokens,
                "cache_read": args.input_tokens * share, "cache_write": 0}
    per_run = usd(per_call, prices) * calls
    expected = per_run * runs
    # Grobe Obergrenze: jeder Zug nutzt alle erlaubten Aufrufe, jeder Aufruf liest das volle Kontextfenster zum
    # teuersten Eingabepreis (Input, Cache-Read oder Cache-Write) und schreibt max_tokens zum Ausgabepreis. Nicht hart:
    # der Loop sendet den Aufruf, der die Kontextgrenze überschreitet, noch ab; sie greift erst danach (O-24).
    top_in = max(prices["input"], prices["cache_read"], prices["cache_write"])
    per_turn_upper = config.max_calls_per_turn * (config.context_limit_tokens * top_in
                                                  + config.max_tokens * prices["output"]) / 1_000_000
    turns = runs * turns_per_run
    upper = per_turn_upper * turns
    run_cap, day_cap = knobs.budget_run_usd, knobs.budget_day_usd
    # Marge der Vorprüfung (O-24): ein Aufruf wird gesperrt, wenn gebucht + Obergrenze des nächsten Aufrufs >= Deckel.
    margin = (args.input_tokens * top_in + config.max_tokens * prices["output"]) / 1_000_000
    lines = [
        f"Modell: {config.model}",
        f"Annahmen: {runs} Läufe zu je {turns_per_run} Zug/Zügen, je Lauf {calls} Aufrufe, je Aufruf "
        f"{args.input_tokens} Input-Token (davon {share:.0%} Cache-Read, kein Cache-Write) und "
        f"{args.output_tokens} Output-Token.",
        f"Erwartet: {_money(per_run)} je Lauf, {_money(expected)} gesamt.",
        f"Grobe Obergrenze aus den Loop-Grenzen (nicht hart): {_money(upper)} für {turns} Züge "
        f"({config.max_calls_per_turn} Aufrufe je Zug, {config.context_limit_tokens} Token Kontext, "
        f"{config.max_tokens} Token Ausgabe je Aufruf; Annahme: voller Kontext zum höchsten Eingabepreis).",
        "Die Kontextgrenze greift erst nach dem Aufruf, der sie überschreitet; ein Aufruf je Sitzung kann darüber "
        "liegen. Die Zahl ist daher keine harte Schranke.",
        f"Spanne: {_money(expected)} (erwartet) bis {_money(upper)} (grobe Obergrenze); die Wirklichkeit hängt von den "
        "angenommenen Token ab. Das SDK kann Anfragen wiederholen; solche Versuche stehen nicht im Trace.",
        f"Hart sind nur die Deckel (SG-24): {run_cap} USD je Lauf (BUDGET_RUN_USD), {day_cap} USD je Tag "
        f"(BUDGET_DAY_USD). Harte Schranke für {runs} Läufe: höchstens {_money(runs * run_cap)} (Läufe × Laufdeckel), "
        f"je Tag höchstens {_money(day_cap)}.",
        "Cache-Write ist nicht angesetzt: Wird Kontext neu in den Cache geschrieben, sind die Kosten höher.",
    ]
    warnings = []
    if calls > config.max_calls_per_turn * turns_per_run:
        warnings.append(f"WARNUNG: {calls} Aufrufe je Lauf sind mehr als der Loop erlaubt "
                        f"({config.max_calls_per_turn} je Zug × {turns_per_run} Zug/Züge).")
    if args.input_tokens > config.context_limit_tokens:
        warnings.append(f"WARNUNG: {args.input_tokens} Input-Token je Aufruf liegen über der Kontextgrenze "
                        f"({config.context_limit_tokens}).")
    if expected > upper:
        warnings.append("WARNUNG: Die erwartete Summe liegt über der groben Obergrenze; die Annahmen passen nicht "
                        "zu den Loop-Grenzen.")
    if per_run + margin >= run_cap:
        lines.append(f"Hinweis: Ein erwarteter Lauf ({_money(per_run)}) plus Marge eines Aufrufs ({_money(margin)}) "
                     f"erreicht den Laufdeckel ({run_cap} USD); die Vorprüfung sperrt dann Aufrufe.")
    if per_run > 0:
        # Ein Lauf beginnt nur, solange gebucht + Marge < Tagesdeckel; nach k Läufen sind k × per_run gebucht.
        room = day_cap - margin
        fits = math.ceil(room / per_run) if room > 0 else 0
        if fits == 0:
            lines.append(f"Tagesdeckel: Kein Lauf passt unter {day_cap} USD (Marge eines Aufrufs {_money(margin)}).")
        else:
            lines.append(f"Tagesdeckel: Bei der erwarteten Schätzung beginnen höchstens {fits} Läufe pro Tag "
                         f"(gebucht + Marge {_money(margin)} muss unter {day_cap} USD bleiben, danach sperrt die "
                         "Vorprüfung).")
            if runs > fits:
                lines.append(f"Hinweis: {runs} Läufe brauchen mindestens {math.ceil(runs / fits)} Tage.")
    return lines + warnings


def estimate_traces(args, prices: dict, config: LoopConfig, info: dict) -> list[str]:
    totals = info["totals"]
    lines = []
    if "demo" in info["modes"]:
        lines.append("ACHTUNG: Die Trace-Datei stammt aus einem Demo-Lauf (run_mode demo). Die Token sind erfunden "
                     "und nicht als Grundlage für eine Schätzung geeignet.")
    skipped = f", {info['skipped']} doppelt übergeben (übersprungen)" if info["skipped"] else ""
    lines.append(f"Modell: {config.model}; {info['files']} Datei(en){skipped}, {info['calls']} Aufrufe "
                 "(model_call und model_error).")
    lines.append("Token: " + ", ".join(f"{field} {totals[field]}" for field in PRICE_FIELDS) + ".")
    lines.append(f"Kosten aus den Token der Datei: {_money(usd(totals, prices))}.")
    lines.append("Unsicherheit: Token gescheiterter Aufrufe können fehlen oder geschätzt sein; das SDK kann Anfragen "
                 "wiederholen, solche Versuche stehen nicht im Trace.")
    return lines + info["warnings"]


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("muss >= 0 sein")
    return value


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("muss >= 1 sein")
    return value


def _share(text: str) -> float:
    value = float(text)
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError("muss zwischen 0 und 1 liegen")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kostenschätzung (keine Abrechnung); rechnet nur mit gepflegten Preisen. "
                                     "Modell, max_tokens und Deckel kommen wie beim Loop aus Umgebung und .env.")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--model", default=None, help="Default: ANTHROPIC_MODEL; im Trace-Modus der Kopf der Datei")
    parser.add_argument("--max-tokens", type=_positive_int, default=None, help="Default: ANTHROPIC_MAX_TOKENS")
    parser.add_argument("--trace", type=Path, nargs="+")
    parser.add_argument("--runs", type=_non_negative_int)
    parser.add_argument("--calls-per-run", type=_non_negative_int)
    parser.add_argument("--input-tokens", type=_non_negative_int)
    parser.add_argument("--output-tokens", type=_non_negative_int)
    parser.add_argument("--cache-read-share", type=_share, default=0.0)
    parser.add_argument("--turns-per-run", type=_positive_int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    assumptions = [args.runs, args.calls_per_run, args.input_tokens, args.output_tokens]
    if args.trace is None and any(v is None for v in assumptions):
        parser.error("entweder --trace oder alle vier: --runs, --calls-per-run, --input-tokens, --output-tokens")
    if args.trace is not None and any(v is not None for v in assumptions):
        parser.error("--trace und der Annahmen-Modus schließen sich aus")
    try:
        knobs = load_knobs()
        info = None
        model = args.model or knobs.anthropic_model
        if args.trace:
            info = read_traces(args.trace)
            if len(info["models"]) != 1:
                raise PricesError("Die Trace-Dateien nennen verschiedene Modelle; sie lassen sich nicht gemeinsam "
                                  "rechnen.")
            (header_model,) = info["models"]
            if args.model and args.model != header_model:
                raise PricesError(f"--model {args.model!r} weicht vom Modell im Kopf der Trace-Datei ab "
                                  f"({header_model!r}). Keine Rechnung.")
            model = header_model
        prices = load_prices(args.prices, model)
        config = LoopConfig(model=model, max_tokens=args.max_tokens or knobs.anthropic_max_tokens)
        lines = _head(args.prices, prices)
        warning = _deviation(prices, model)
        body = estimate_traces(args, prices, config, info) if info else estimate_assumptions(args, prices, config, knobs)
    except PricesError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("\n".join(lines + body + ([warning] if warning else [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
