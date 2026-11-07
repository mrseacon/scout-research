"""Prompt-Varianten für den halb-live-Satz (TA-16b): nicht aktiv, nur eine Änderung, konsistent, nur vom Runner geladen."""

from __future__ import annotations

import ast
import difflib
import inspect
import re
from pathlib import Path

import pytest

from eval.variants import PROMPT_VARIANTS, VARIANT_NAMES, VARIANTS_DIR, load_variant_prompt, parse_prompt
from scout_research.agent.prompt_loader import load_system_prompt
import tests.unit.test_system_prompt as tsp
from tests.unit.test_system_prompt import PROMPT_PATH

ACTIVE_SHA256 = {"de": "91e9816016108cd56fbc16ea503729f65b0ceca7962ac7710843b69b595b28c7",
                 "en": "88015f46d2cc7a21b328023a6fa9d5ec264efd0f364ea7d248975c2c3264bbc3"}
"""Hash des gesendeten aktiven Prompts v1.1.0, festgehalten vor TA-16b: ändert ihn jemand, schlägt der Test an."""
SRC = Path(__file__).resolve().parents[2] / "src"
HEADER = re.compile(r"\A<!--.*?-->\n", re.DOTALL)


def _body(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    return raw[HEADER.match(raw).end():]  # type: ignore[union-attr]


def test_the_active_prompt_is_unchanged_and_the_loader_serves_it_without_a_variant() -> None:
    for language in ("de", "en"):
        active = load_system_prompt(language)
        assert active.version == "1.1.0" and active.sha256 == ACTIVE_SHA256[language]
        assert load_variant_prompt("active", language) == active
    assert PROMPT_PATH.read_text(encoding="utf-8").startswith("<!-- scout-system-prompt v1.1.0 ")
    assert set(PROMPT_VARIANTS) == {"active", *VARIANT_NAMES}


def test_the_variant_parser_derives_version_and_hash_like_the_active_loader() -> None:
    raw = PROMPT_PATH.read_text(encoding="utf-8")
    for language in ("de", "en"):
        assert parse_prompt(raw, language) == load_system_prompt(language)


@pytest.mark.parametrize("name", VARIANT_NAMES)
def test_a_variant_has_its_own_version_and_a_hash_of_the_sent_text(name: str) -> None:
    import hashlib

    variant = load_variant_prompt(name)
    assert variant.version == f"1.2.0-{name}" and variant.sha256 != ACTIVE_SHA256["de"]
    assert variant.sha256 == hashlib.sha256(variant.text.encode("utf-8")).hexdigest()
    assert "<!--" not in variant.text and "{output_language}" not in variant.text and "Sprache: Deutsch." in variant.text
    head = (VARIANTS_DIR / f"{name}.md").read_text(encoding="utf-8").split("-->")[0]
    assert "Variante für den halb-live-Satz, nicht aktiv" in head and "Basis v1.1.0" in head


@pytest.mark.parametrize("name", VARIANT_NAMES)
def test_a_variant_differs_from_the_active_prompt_in_exactly_one_contiguous_section(name: str) -> None:
    """Zeilenweiser Vergleich: genau ein nicht gleicher Block (`get_opcodes`) heißt eine zusammenhängende Änderung. Die Größe ist
    begrenzt, damit aus 'eine Änderung' kein Umbau wird: höchstens zwei Zeilen des aktiven Prompts ersetzt, höchstens drei neu."""
    old = _body(PROMPT_PATH).splitlines()
    new = _body(VARIANTS_DIR / f"{name}.md").splitlines()
    changes = [op for op in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if op[0] != "equal"]
    assert len(changes) == 1
    _, i1, i2, j1, j2 = changes[0]
    assert i2 - i1 <= 2 and j2 - j1 <= 3


def test_the_added_text_of_the_p8_variant_has_no_digits_and_gives_no_instructions() -> None:
    old = _body(PROMPT_PATH).splitlines()
    new = _body(VARIANTS_DIR / "p8_host_turn.md").splitlines()
    added = [line for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if tag != "equal"
             for line in new[j1:j2]]
    assert added and not re.search(r"\d", "\n".join(added))
    assert "Bei einer Bestätigung fährst du" not in "\n".join(added) and "gilt nichts davon als entschieden" in "\n".join(added)


def test_the_turn_ended_variant_changes_the_prompt_sentence_and_declares_the_replacement_hint() -> None:
    from eval.variants import VARIANT_HINTS

    old = _body(PROMPT_PATH).splitlines()
    turn = [line for line in difflib.ndiff(old, _body(VARIANTS_DIR / "turn_ended.md").splitlines()) if line[:2] in ("- ", "+ ")]
    assert len(turn) == 2 and "TURN_ENDED" in turn[0]
    assert "in diesem Zug nicht mehr ausgeführt; auf eine neue Bitte des Nutzers ist der Aufruf wieder möglich" in turn[1]
    assert "weil der Zug schon beendet war" not in turn[1]
    assert set(VARIANT_HINTS) == {"turn_ended"}


@pytest.mark.parametrize("name", ["active", *VARIANT_NAMES])
def test_the_active_prompt_and_every_variant_pass_the_same_consistency_checks(name: str, monkeypatch) -> None:
    """Dieselben Prüfungen wie `test_system_prompt.py` (Kopf, statischer Text, Tools, Fehlercodes, Slot-Tabelle, ✓/✗-Beispiele),
    ausgeführt gegen den Text der Variante: Die Modulwerte werden ersetzt, die Testfunktionen selbst bleiben unverändert."""
    path = PROMPT_PATH if name == "active" else VARIANTS_DIR / f"{name}.md"
    raw = path.read_text(encoding="utf-8")
    header = re.match(r"<!--(.*?)-->\n", raw, flags=re.DOTALL)
    assert header is not None
    body = raw[header.end():]
    good = [line[2:] for line in body.splitlines() if line.startswith("✓ ")]
    bad = [line[2:].split(" ← ")[0] for line in body.splitlines() if line.startswith("✗ ")]
    for attr, value in (("RAW", raw), ("HEADER", header), ("BODY", body), ("GOOD", good), ("BAD", bad)):
        monkeypatch.setattr(tsp, attr, value)
    for check in (tsp.test_the_prompt_carries_a_version_header_that_is_stripped_before_sending,
                  tsp.test_the_prompt_is_static_apart_from_the_language_placeholder,
                  tsp.test_every_tool_is_named_and_confirm_peer_set_is_not_a_tool,
                  tsp.test_every_error_code_is_handled_and_the_retryable_ones_are_named_as_such,
                  tsp.test_the_slot_table_lists_exactly_the_fields_the_code_accepts,
                  tsp.test_the_positive_examples_cover_all_six_namespaces):
        check()
    assert good and bad
    for example in good:
        tsp.test_every_positive_example_passes_the_check(example)
    for example in bad:
        tsp.test_every_negative_example_is_rejected(example)


# --- Architektur: nur der Runner liest Varianten ---------------------------------------------------------------------------


def _reads_variants(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "prompt_variants" in node.value:
            return True
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "eval" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "eval":
            return True
    return False


def test_nothing_under_src_reads_the_prompt_variants() -> None:
    offenders = [str(p.relative_to(SRC)) for p in sorted(SRC.rglob("*.py")) if _reads_variants(p.read_text(encoding="utf-8"))]
    assert offenders == []
    assert "prompt_variants" not in (SRC / "scout_research" / "agent" / "prompt_loader.py").read_text(encoding="utf-8")


def test_the_detector_finds_a_reader() -> None:
    assert _reads_variants("p = base / 'eval' / 'prompt_variants'") and _reads_variants("from eval.variants import x")
    assert not _reads_variants("p = base / 'prompts' / 'system.md'")


CHECKS_RUN_FOR_VARIANTS = {
    "test_the_prompt_carries_a_version_header_that_is_stripped_before_sending",
    "test_the_prompt_is_static_apart_from_the_language_placeholder",
    "test_every_tool_is_named_and_confirm_peer_set_is_not_a_tool",
    "test_every_error_code_is_handled_and_the_retryable_ones_are_named_as_such",
    "test_the_slot_table_lists_exactly_the_fields_the_code_accepts",
    "test_the_positive_examples_cover_all_six_namespaces",
    "test_every_positive_example_passes_the_check",
    "test_every_negative_example_is_rejected",
}
EXCEPTIONS: dict[str, str] = {}
"""Ausnahmen von der Wiederverwendung (Name -> Grund). Derzeit gibt es keine."""


def test_every_check_of_the_system_prompt_tests_runs_for_the_variants_or_has_a_stated_exception() -> None:
    collected = {name for name, fn in inspect.getmembers(tsp, inspect.isfunction) if name.startswith("test_") and fn.__module__ == tsp.__name__}
    assert collected, "keine Tests gefunden"
    assert collected == CHECKS_RUN_FOR_VARIANTS | set(EXCEPTIONS)
    assert all(EXCEPTIONS.values())
    source = inspect.getsource(test_the_active_prompt_and_every_variant_pass_the_same_consistency_checks)
    assert all(f"tsp.{name}" in source for name in CHECKS_RUN_FOR_VARIANTS)
