"""scripts/estimate_cost.py (TA-15): rechnet nur mit gepflegten Preisen, kennzeichnet jede Ausgabe als Schätzung.

Alle Preise in diesen Tests sind frei erfunden (offensichtlich krumme Testwerte), keine Listenpreise."""

from __future__ import annotations

import importlib.util
import json
import re
import tomllib
from pathlib import Path

import pytest

from scout_research.agent.budgets import PRICES, LoopConfig
from scout_research.config import Settings

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = ROOT / "config" / "prices.toml"
_spec = importlib.util.spec_from_file_location("estimate_cost", ROOT / "scripts" / "estimate_cost.py")
estimate_cost = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(estimate_cost)

MODEL = LoopConfig().model
FAKE = {"input": "1.7", "output": "13.0", "cache_read": "0.9", "cache_write": "2.3"}  # frei erfunden
ENV_NAMES = ("ANTHROPIC_MODEL", "ANTHROPIC_MAX_TOKENS", "BUDGET_RUN_USD", "BUDGET_DAY_USD")


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch) -> None:
    """Weder die echte `.env` noch die Umgebung des Entwicklers fließen ein."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)


def _prices_file(tmp_path: Path, extra: str = "", **fields: str | None) -> Path:
    values = {**FAKE, **fields}
    lines = [f'[models."{MODEL}"]', 'source = "https://example.test/preise"', 'checked = "2026-01-01"']
    lines += [f"{key} = {value}" for key, value in values.items() if value is not None]
    path = tmp_path / "prices.toml"
    path.write_text("\n".join(lines) + "\n" + extra, encoding="utf-8")
    return path


def _run(capsys, *argv: str) -> tuple[int, str, str]:
    code = estimate_cost.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


ASSUME = ["--runs", "10", "--calls-per-run", "4", "--input-tokens", "10000", "--output-tokens", "1000"]


def _event(name: str, seq: int, **fields) -> dict:
    return {"event": name, "seq": seq, "ts": "2026-01-01T00:00:00+00:00", "run_id": "r1", "turn": 0, **fields}


def _trace(tmp_path: Path, name: str = "t.jsonl", run_mode: str = "eval", model: str = MODEL, session: str = "s1",
           end_calls: int = 2, dropped: int = 0) -> Path:
    price = {"input": 1.0, "output": 1.0, "cache_read": 1.0, "cache_write": 1.0}
    params = {"max_tokens": 8000, "max_calls_per_turn": 10, "max_tools_per_turn": 12, "turn_seconds": 600.0,
              "call_seconds": 300.0, "stall_seconds": 60.0, "cost_warning_usd": 1.0, "cost_limit_usd": 2.0,
              "context_limit_tokens": 150000, "budget_run_usd": 1.0, "budget_day_usd": 3.0, "price": price}
    events = [
        _event("header", 0, session_id=session, run_mode=run_mode, git_commit=None, model=model, effort="medium",
               output_language="de", prompt_version="0", prompt_sha256="a" * 64, tools_sha256="b" * 64, params=params),
        _event("model_call", 1, stop_reason="end_turn", request_id=None, input_tokens=1000, output_tokens=200,
               cache_read_input_tokens=4000, cache_creation_input_tokens=500, cost_usd=0.0, session_cost_usd=0.0,
               tool_choice="auto", messages=1, latency_s=0.1, upper_input_tokens=0),
        _event("model_error", 2, kind="unavailable", error_class=None, status_code=529, request_id=None,
               input_tokens=100, output_tokens=None, cache_read_input_tokens=50, cache_creation_input_tokens=None,
               cost_usd=0.0, usage_estimated=True, session_cost_usd=0.0, latency_s=None),
        _event("end", 3, state="idle", session_cost_usd=0.0, model_calls=end_calls, last_outcome=None,
               last_reason=None, reason=None, error_class=None, dropped_events=dropped),
    ]
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


# --- ausgelieferte Vorlage ---------------------------------------------------------------------------------------


def test_shipped_template_aborts_and_names_the_field(capsys) -> None:
    code, out, err = _run(capsys, "--prices", str(SHIPPED), *ASSUME)
    assert code != 0
    assert out == ""
    assert "nicht vollständig gepflegt" in err and "source fehlt" in err


def test_shipped_template_contains_no_price_numbers() -> None:
    text = SHIPPED.read_text(encoding="utf-8")
    for name, entry in tomllib.loads(text)["models"].items():
        for field in estimate_cost.PRICE_FIELDS:
            assert field not in entry, f"{field} ist für {name} in der ausgelieferten Vorlage befüllt"
        assert entry["source"] == "" and entry["checked"] == ""
    # keine Ziffer außerhalb der [models."…"]-Kopfzeilen (auch nicht in Kommentaren)
    rest = re.sub(r'^\[models\."[^"\n]*"\]$', "", text, flags=re.MULTILINE)
    assert not re.search(r"\d", rest)


# --- Annahmen-Modus ------------------------------------------------------------------------------------------------


def test_assumption_mode_computes_expected_and_bounds(tmp_path, capsys) -> None:
    path = _prices_file(tmp_path)
    code, out, _ = _run(capsys, "--prices", str(path), *ASSUME, "--cache-read-share", "0.5")
    assert code == 0
    assert out.splitlines()[0].startswith("SCHÄTZUNG")
    # je Aufruf: 5000 * 1,7 + 5000 * 0,9 + 1000 * 13 = 26000 -> 0,026 USD; je Lauf 4 Aufrufe = 0,104; 10 Läufe = 1,04
    assert "0.1040 USD je Lauf" in out and "1.0400 USD gesamt" in out
    # grobe Obergrenze: 10 Läufe * 10 Aufrufe * (150000 * 2,3 + 8000 * 13) / 1e6 = 100 * 0,449 = 44,9
    assert "Grobe Obergrenze aus den Loop-Grenzen (nicht hart): 44.9000 USD" in out
    # hart: 10 Läufe * 1 USD Laufdeckel
    assert "höchstens 10.0000 USD (Läufe × Laufdeckel)" in out
    assert "https://example.test/preise" in out and "2026-01-01" in out
    assert "Cache-Write ist nicht angesetzt" in out and "SDK" in out


def test_day_cap_uses_the_precheck_margin(tmp_path, capsys) -> None:
    path = _prices_file(tmp_path)
    _, out, _ = _run(capsys, "--prices", str(path), *ASSUME, "--cache-read-share", "0.5")
    # Marge eines Aufrufs: 10000 * 2,3 + 8000 * 13 = 127000 -> 0,127 USD. Ein Lauf beginnt nur, solange
    # gebucht + Marge < 3 (O-24, `>=` sperrt). Unabhängig nachgezählt:
    started, booked = 0, 0.0
    while booked + 0.127 < 3.0:
        started += 1
        booked += 0.104
    assert started == 28
    assert f"höchstens {started} Läufe pro Tag" in out
    assert "0.1270 USD" in out


def test_run_cap_hint_includes_the_margin(tmp_path, capsys, monkeypatch) -> None:
    path = _prices_file(tmp_path)
    # 0,104 + 0,127 = 0,231 >= 0,23, aber 0,104 allein läge darunter
    monkeypatch.setenv("BUDGET_RUN_USD", "0.23")
    _, out, _ = _run(capsys, "--prices", str(path), *ASSUME, "--cache-read-share", "0.5")
    assert "erreicht den Laufdeckel" in out
    monkeypatch.setenv("BUDGET_RUN_USD", "0.24")
    _, out, _ = _run(capsys, "--prices", str(path), *ASSUME, "--cache-read-share", "0.5")
    assert "erreicht den Laufdeckel" not in out


def test_no_run_fits_under_the_day_cap(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("BUDGET_DAY_USD", "0.1")  # kleiner als die Marge von 0,127
    _, out, _ = _run(capsys, "--prices", str(_prices_file(tmp_path)), *ASSUME)
    assert "Kein Lauf passt" in out and "Läufe pro Tag" not in out


def test_inconsistent_assumptions_warn(tmp_path, capsys) -> None:
    path = str(_prices_file(tmp_path))
    # 40 Aufrufe in einem Zug (erlaubt: 10), Input über der Kontextgrenze
    _, out, _ = _run(capsys, "--prices", path, "--runs", "1", "--calls-per-run", "40", "--input-tokens", "200000",
                     "--output-tokens", "1000")
    assert "WARNUNG: 40 Aufrufe je Lauf" in out and "WARNUNG: 200000 Input-Token" in out
    _, out, _ = _run(capsys, "--prices", path, "--runs", "1", "--calls-per-run", "1", "--input-tokens", "10000000",
                     "--output-tokens", "1000000")
    assert "über der groben Obergrenze" in out
    _, out, _ = _run(capsys, "--prices", path, "--runs", "1", "--calls-per-run", "20", "--input-tokens", "1000",
                     "--output-tokens", "100", "--turns-per-run", "2")
    assert "WARNUNG: 20 Aufrufe" not in out
    with pytest.raises(SystemExit):
        estimate_cost.main([*ASSUME, "--turns-per-run", "0", "--prices", path])


# --- Konfiguration aus Umgebung und .env -----------------------------------------------------------------------------


def test_knob_defaults_equal_the_settings_defaults() -> None:
    knobs = estimate_cost.load_knobs()
    for field in ("anthropic_model", "anthropic_max_tokens", "budget_run_usd", "budget_day_usd"):
        assert getattr(knobs, field) == Settings.model_fields[field].default


def test_dotenv_is_read_without_edgar_email_and_other_fields_are_never_printed(tmp_path, capsys) -> None:
    Path(".env").write_text("BUDGET_DAY_USD=2\nFINNHUB_API_KEY=geheim-wert\nANTHROPIC_MAX_TOKENS=1000\n")
    _, out, err = _run(capsys, "--prices", str(_prices_file(tmp_path)), *ASSUME)
    assert "je Tag höchstens 2.0000 USD" in out
    assert "1000 Token Ausgabe" in out
    assert "geheim-wert" not in out + err


@pytest.mark.parametrize("name,value", [("BUDGET_DAY_USD", "7.77e999"), ("BUDGET_RUN_USD", "0"),
                                        ("BUDGET_RUN_USD", "nan"), ("ANTHROPIC_MAX_TOKENS", "12")])
def test_invalid_config_aborts_without_echoing_the_value(tmp_path, capsys, monkeypatch, name, value) -> None:
    monkeypatch.setenv(name, value)
    code, out, err = _run(capsys, "--prices", str(_prices_file(tmp_path)), *ASSUME)
    assert code != 0 and out == ""
    assert name in err and value not in err


def test_max_tokens_option_overrides_the_config(tmp_path, capsys) -> None:
    _, out, _ = _run(capsys, "--prices", str(_prices_file(tmp_path)), *ASSUME, "--max-tokens", "4000")
    assert "4000 Token Ausgabe" in out


# --- Trace-Modus -----------------------------------------------------------------------------------------------------


def test_trace_mode_sums_calls_and_errors(tmp_path, capsys) -> None:
    prices = _prices_file(tmp_path)
    code, out, _ = _run(capsys, "--prices", str(prices), "--trace", str(_trace(tmp_path)))
    assert code == 0
    assert out.splitlines()[0].startswith("SCHÄTZUNG")
    assert "input 1100, output 200, cache_read 4050, cache_write 500" in out
    # (1100 * 1,7 + 200 * 13 + 4050 * 0,9 + 500 * 2,3) / 1e6 = 9265 / 1e6
    assert "0.0093 USD" in out
    assert "Demo" not in out and "WARNUNG: Ende" not in out and "wurden nicht geschrieben" not in out
    assert "meldet" not in out


def test_demo_trace_is_flagged_and_still_starts_with_the_estimate_line(tmp_path, capsys) -> None:
    prices = _prices_file(tmp_path)
    _, out, _ = _run(capsys, "--prices", str(prices), "--trace", str(_trace(tmp_path, run_mode="demo")))
    assert out.splitlines()[0].startswith("SCHÄTZUNG")
    assert "Demo-Lauf" in out and "erfunden" in out


def test_duplicate_trace_files_count_once(tmp_path, capsys) -> None:
    prices = str(_prices_file(tmp_path))
    first = _trace(tmp_path)
    same_path = tmp_path / "x" / ".." / "t.jsonl"
    copy = _trace(tmp_path, name="kopie.jsonl")  # gleiche Sitzung unter anderem Namen
    _, out, _ = _run(capsys, "--prices", prices, "--trace", str(first), str(same_path), str(copy))
    assert "1 Datei(en)" in out and "2 doppelt" in out
    assert "input 1100, output 200" in out


def test_trace_model_must_match_and_unknown_model_aborts(tmp_path, capsys) -> None:
    prices = str(_prices_file(tmp_path))
    trace = str(_trace(tmp_path))
    code, _, err = _run(capsys, "--prices", prices, "--trace", trace, "--model", "anderes-modell")
    assert code != 0 and "weicht vom Modell im Kopf" in err
    code, _, err = _run(capsys, "--prices", prices, "--trace", str(_trace(tmp_path, "m.jsonl", model="gibt-es-nicht")))
    assert code != 0 and "Modell" in err
    code, _, err = _run(capsys, "--prices", prices, "--trace", trace, str(_trace(tmp_path, "n.jsonl", model="x",
                                                                                session="s2")))
    assert code != 0 and "verschiedene Modelle" in err


def test_trace_warns_on_dropped_events_and_call_mismatch(tmp_path, capsys) -> None:
    prices = str(_prices_file(tmp_path))
    _, out, _ = _run(capsys, "--prices", prices, "--trace", str(_trace(tmp_path, dropped=2, end_calls=5)))
    assert "2 Ereignis(se) wurden nicht geschrieben" in out
    assert "meldet 5 Aufrufe, gezählt wurden 2" in out


def test_broken_or_foreign_trace_aborts(tmp_path, capsys) -> None:
    prices = str(_prices_file(tmp_path))
    foreign = tmp_path / "fremd.jsonl"
    foreign.write_text('{"event": "model_call", "input_tokens": 5}\n', encoding="utf-8")
    code, out, err = _run(capsys, "--prices", prices, "--trace", str(foreign))
    assert code != 0 and out == "" and "defekt" in err
    binary = tmp_path / "bin.jsonl"
    binary.write_bytes(b"\xff\xfe\x00")
    code, _, err = _run(capsys, "--prices", prices, "--trace", str(binary))
    assert code != 0 and "defekt" in err
    code, _, err = _run(capsys, "--prices", prices, "--trace", str(tmp_path / "fehlt.jsonl"))
    assert code != 0


# --- ungültige Preise ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("field,value", [
    ("input", "-1.0"), ("output", "nan"), ("cache_read", '"2.0"'), ("cache_write", "inf"), ("input", "true"),
    ("output", None), ("input", "0"), ("cache_read", "0.0"),
])
def test_invalid_price_aborts(tmp_path, capsys, field, value) -> None:
    path = _prices_file(tmp_path, **{field: value})
    code, out, err = _run(capsys, "--prices", str(path), *ASSUME)
    assert code != 0 and out == ""
    assert "nicht vollständig gepflegt" in err and f"{field} " in err


def test_zero_price_message_says_positive_is_required(tmp_path, capsys) -> None:
    _, _, err = _run(capsys, "--prices", str(_prices_file(tmp_path, input="0")), *ASSUME)
    assert "muss endlich und positiv sein" in err


def test_bad_date_unknown_key_missing_source_and_unknown_model_abort(tmp_path, capsys) -> None:
    bad_date = _prices_file(tmp_path).read_text().replace("2026-01-01", "gestern")
    path = tmp_path / "d.toml"
    path.write_text(bad_date)
    code, _, err = _run(capsys, "--prices", str(path), *ASSUME)
    assert code != 0 and "checked" in err
    code, _, err = _run(capsys, "--prices", str(_prices_file(tmp_path, cache_wirte="2.0")), *ASSUME)
    assert code != 0 and "cache_wirte" in err
    path.write_text(f'[models."{MODEL}"]\ninput = 1\noutput = 1\ncache_read = 1\ncache_write = 1\n')
    code, _, err = _run(capsys, "--prices", str(path), *ASSUME)
    assert code != 0 and "source fehlt" in err
    code, _, err = _run(capsys, "--prices", str(_prices_file(tmp_path)), "--model", "gibt-es-nicht", *ASSUME)
    assert code != 0 and "Modell" in err


def test_unreadable_prices_file_aborts_with_fixed_text(tmp_path, capsys) -> None:
    path = tmp_path / "kaputt.toml"
    path.write_bytes(b"\xff\xfe\x00[")
    code, out, err = _run(capsys, "--prices", str(path), *ASSUME)
    assert code != 0 and out == "" and "nicht lesbar" in err
    path.write_text("[models\n")
    code, _, err = _run(capsys, "--prices", str(path), *ASSUME)
    assert code != 0 and "nicht lesbar" in err


# --- Abweichung zur Code-Tabelle -------------------------------------------------------------------------------------


def test_deviation_from_code_table_warns_and_equal_values_do_not(tmp_path, capsys) -> None:
    _, out, _ = _run(capsys, "--prices", str(_prices_file(tmp_path)), *ASSUME)
    assert "Der Loop rechnet den Budgetdeckel mit anderen Preisen" in out
    code = PRICES[MODEL]
    same = _prices_file(tmp_path, input=str(code.input), output=str(code.output), cache_read=str(code.cache_read),
                        cache_write=str(code.cache_write))
    _, out, _ = _run(capsys, "--prices", str(same), *ASSUME)
    assert "anderen Preisen" not in out
