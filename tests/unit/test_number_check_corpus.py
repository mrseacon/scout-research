"""T2 — Abnahme des Number-Checks gegen das Prüfkorpus (`tests/data/number_check_corpus.yaml`, Plan 11.6).

Jeder Fall des Korpus ist ein eigener Test. Kein Fall wird übersprungen oder umgangen; die als `residual` markierten
Fälle sind dokumentierte Grenzen des Checks und werden mit ihrer Erwartung („durchgelassen“) geprüft.

Mutationsproben (wie bei T12): Schaltet man den Backstop bzw. die Slot-Prüfung ab, muss das Korpus Fehlschläge melden.
"""

from collections import Counter

import pytest

from scout_research.tools import commentary
from scout_research.tools.slots import SlotOk
from tests.unit.number_check_corpus import build_session, evaluate, load_corpus

CORPUS = load_corpus()
CASES = CORPUS["cases"]


def _id(case: dict) -> str:
    return case["id"] + ("-residual" if case.get("residual") else "")


@pytest.mark.parametrize("case", CASES, ids=[_id(c) for c in CASES])
def test_corpus_case(case: dict) -> None:
    errors = evaluate(case, build_session(CORPUS, case["session"]))
    assert not errors, f"{case['id']} ({case['category']}): {case['text']!r}\n" + "\n".join(errors)


def test_corpus_has_the_agreed_size_and_shape() -> None:
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)) and len(CASES) >= 200
    langs = Counter(c["lang"] for c in CASES)
    assert langs["de"] > 100 and langs["en"] >= 25
    assert {c["profile"] for c in CASES} == {"slot_text", "rationale"}
    assert sum(1 for c in CASES if c["category"].startswith("attack")) >= 30
    for case in CASES:
        assert case["expect"] == "accepted" or (case["findings"] and case["reason"]), case["id"]
        assert case["session"] in CORPUS["session"]["variants"], case["id"]
        assert CORPUS["session"]["variants"][case["session"]]["output_language"] == case["lang"], case["id"]


def test_the_only_documented_exceptions_are_the_known_residual_risks() -> None:
    """Zwei Fälle lässt der Check bewusst durch: eine qualitative Menge ohne Zahl und einen gültigen Slot mit falscher
    Kennzahl. Wer den Check verschärft, ändert diese Liste bewusst (Plan 11.2, 11.7)."""
    residual = sorted(c["id"] for c in CASES if c.get("residual"))
    assert residual == ["ST-A10", "ST-A24"]
    assert all(c["expect"] == "accepted" for c in CASES if c.get("residual"))


def _failing(cases: list[dict]) -> set[str]:
    return {c["id"] for c in cases if evaluate(c, build_session(CORPUS, c["session"]))}


def test_mutation_without_the_number_backstop_the_corpus_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _failing(CASES) == set()  # Ausgangslage: alles grün
    monkeypatch.setattr(commentary, "scan_numbers", lambda masked, invalid=(): [])
    failing = _failing(CASES)
    expects_naked = {c["id"] for c in CASES if c["expect"] != "accepted" and "NAKED_NUMBER" in c["expect"]}
    assert expects_naked and expects_naked <= failing, sorted(expects_naked - failing)
    assert len(failing) >= 100


def test_mutation_without_slot_validation_the_corpus_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _failing(CASES) == set()
    monkeypatch.setattr(commentary, "resolve_slot", lambda inner, session: SlotOk("0", "mutiert"))
    failing = _failing(CASES)
    # `{{…}}`, verschachtelte und einfache Klammern erkennt die Syntaxprüfung vor der Auflösung; alle übrigen
    # Gültigkeitsfälle (ST-C) hängen an der Auflösung.
    syntax_only = {"ST-C12", "ST-C13", "ST-C23"}
    expects_resolution = {c["id"] for c in CASES if c["id"].startswith("ST-C")} - syntax_only
    assert expects_resolution <= failing, sorted(expects_resolution - failing)
    assert {"ST-A02", "ST-A31"} <= failing  # und die korrekt gerenderten Fälle zeigen den Platzhalter statt des Werts


# --- Robustheit: zufällige Texte (fester Seed) -----------------------------------------------------------------------

import random  # noqa: E402

_PIECES = ["ADBE", "liegt", "über", "dem", "Median", "[[", "]]", "[[co:ADBE:pe]]", "[[stat:pe:n]]", "[[warn:W2]]", "{{", "}}",
           "[", "]", ":", "\n", " ", "  ", "10-K", "W2", "W9", "​", " ", "٣", "１", "²", "½", "Ⅻ", "🔟", ".", ",",
           "-", "%", "x", "Q", "FY", "zwei", "Dutzend", "doppelt", "so", "hoch", "ein", "Prozent", "zweit", "3M", "CIK",
           "0000796343", "„", "“", "`", "*", "_", "ct_0001", "&#49;", "ß", "é", "́"]
_NUMBERS = ["17", "17,0x", "1 2 . 5", "٣٢", "１７", "²⁶", "½", "Ⅻ", "🔟", "2025-11-28", "FY2025", "Q3", "21,5 Mrd. USD", "zwei",
            "zwölf", "doppelt so", "ein Prozent", "z w e i", "tausend", "Hälfte", "zweitgrößte", "dozen"]
_SAFE = ["ADBE liegt", "der Median", "über", "Peers", "und", "wegen des 10-K", "[[co:ADBE:pe]]", "(siehe oben)", "\n- "]


def test_check_text_never_raises_and_reports_consistent_positions() -> None:
    rng = random.Random(20261009)
    sessions = [build_session(CORPUS, v) for v in ("base", "base_en", "pre_table")]
    for _ in range(3000):
        text = "".join(rng.choice(_PIECES) for _ in range(rng.randint(0, 14)))
        for session in sessions:
            for profile in ("slot_text", "rationale"):
                result = commentary.check_text(session, text, profile)
                for p in result.problems:
                    assert 0 <= p.start < p.end <= len(text) and p.match == text[p.start:p.end], (text, p)
                if result.ok and profile == "slot_text":
                    assert "[[" not in result.body and "]]" not in result.body, (text, result.body)


def test_a_number_inserted_anywhere_into_safe_text_is_never_accepted() -> None:
    rng = random.Random(7)
    session = build_session(CORPUS, "base")
    for _ in range(2000):
        parts = [rng.choice(_SAFE) for _ in range(rng.randint(1, 4))]
        parts.insert(rng.randint(0, len(parts)), rng.choice(_NUMBERS))
        text = rng.choice([" ", "  ", ", ", " und "]).join(parts)
        for profile in ("slot_text", "rationale"):
            result = commentary.check_text(session, text, profile)
            assert not result.ok, (profile, text)
