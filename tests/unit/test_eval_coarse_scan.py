"""Unabhängige grobe Prüfung der gezeigten Texte (TA-20.3, Review Fable 06 N-6, Plan 12.12 O-40).

`check_text` prüft und `_b1` zählt dessen Funde; was die Prüfung übersieht, hieße „B1 erfüllt“. `eval/coarse_scan.py` ist die
zweite, bewusst grobe Prüfung ohne Code der ersten; ihre Treffer sind „manuell prüfen“ und ändern kein B-Ergebnis.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from eval.run_scenarios import main
from scout_research import cli
from scout_research.agent.loop import Shown
from scout_research.agent.model_client import ScriptedModelClient
from scout_research.tools.commentary import build_check_session, check_text
from tests.unit.loop_harness import COMMENTARY, Run, say
from tests.unit.test_agent_loop_gate import _at_table
from tests.unit.test_agent_loop_core import _flow_to_proposal
from tests.unit.tool_world import standard_world

ROOT = Path(__file__).resolve().parents[2]
RESTRISIKO = "Die zweiPeers sind ähnlich."
"""Nach TA-20.1 bekanntes Restrisiko: ein Zahlwort als Teil eines Wortes; `check_text` nimmt es an."""


def _script(*steps):
    return lambda s, r: ScriptedModelClient([step() for step in steps])


# --- Die Prüfung selbst, direkt am Dump --------------------------------------------------------------------------------


def _scan(*entries: dict) -> list:
    from eval.coarse_scan import scan_dump

    return scan_dump(list(entries))


def _kinds(hits) -> set[str]:
    return {h.kind for h in hits}


def test_a_digit_outside_the_slots_of_the_model_text_is_reported() -> None:
    hits = _scan({"kind": "text", "text": "Das sind Peers.", "model_text": "Es sind 5 Peers."})
    assert [(h.entry, h.kind) for h in hits] == [(0, "ziffer")]


def test_digits_that_come_only_from_rendered_slots_are_not_reported() -> None:
    entry = {"kind": "commentary", "text": "NVDA liegt bei 31,4x über dem Median 22,1x (Warnung 1).",
             "model_text": "NVDA liegt bei [[co:NVDA:ev_ebitda]] über dem Median [[stat:ev_ebitda:median]] ([[warn:W1]])."}
    assert _scan(entry) == []


def test_any_numeric_character_counts_as_a_digit() -> None:
    for ch in ("5", "٣", "½", "²", "Ⅻ"):
        assert _kinds(_scan({"kind": "text", "text": "x", "model_text": f"Wert {ch} hier"})) == {"ziffer"}, ch


def test_number_words_of_the_own_generator_are_reported_and_the_indefinite_articles_are_not() -> None:
    for word in ("zwei", "Siebzehn", "einundzwanzig", "FÜNFZIG", "fuenf", "hundert", "twelve", "ninety-nine", "Million"):
        hits = _scan({"kind": "text", "text": "x", "model_text": f"Es gibt {word} davon."})
        assert hits and hits[0].kind == "zahlwort", word
    assert _scan({"kind": "text", "text": "x", "model_text": "Ein Peer, eine Marge, one peer, a peer."}) == []


def test_a_part_word_hit_catches_a_number_word_inside_a_longer_token() -> None:
    for word in ("zweiPeers", "fünfjährig", "Dreijahresschnitt", "fivePeers", "vierfach"):
        hits = _scan({"kind": "text", "text": "x", "model_text": f"Das {word} Bild."})
        assert hits and hits[0].kind == "teilwort", word


def test_ordinary_words_do_not_alarm() -> None:
    text = "Der Median der Peers liegt unter dem Wert von NVDA; eine einfache Lage ohne Einschränkung."
    assert _scan({"kind": "text", "text": "x", "model_text": text}) == []


def test_without_model_text_every_digit_in_the_text_is_reported() -> None:
    hits = _scan({"kind": "notice", "text": "Hinweis 7"}, {"kind": "intro", "text": "Fest 1"})
    assert [(h.entry, h.kind) for h in hits] == [(0, "ziffer"), (1, "ziffer")]


def test_control_and_invisible_characters_in_the_text_are_reported() -> None:
    for ch in ("\x00", "\x1b", "\x7f", "​", "‮", "ㅤ", "ᅟ", "ᅠ", "ﾠ"):
        hits = _scan({"kind": "text", "text": f"Gut{ch}Text"})
        assert hits and hits[0].kind in ("steuerzeichen", "unsichtbar"), repr(ch)
    assert _scan({"kind": "text", "text": "Zeile\nZeile\tTab"}) == []


def test_the_snippet_is_at_most_24_characters_and_comes_from_the_given_text() -> None:
    hits = _scan({"kind": "text", "text": "x", "model_text": "a" * 80 + " 5 " + "b" * 80})
    assert hits and len(hits[0].snippet) <= 24 and "5" in hits[0].snippet


def test_obfuscated_number_words_are_found_after_normalization() -> None:
    for word in ("zwéi", "zw\u200bei", "ｚｗｅｉ", "𝐳𝐰𝐞𝐢", "zwe\u0301i"):
        hits = _scan({"kind": "text", "text": "x", "model_text": f"Es gibt {word} davon."})
        assert hits and hits[0].kind == "zahlwort", word


def test_ordinals_fractions_multiplicatives_dozens_and_magnitude_abbreviations_are_found() -> None:
    for word in ("zweite", "zweitgrößte", "second", "third", "Hälfte", "Drittel", "Viertel", "halb", "anderthalb", "half",
                 "quarter", "doppelt", "verdoppelt", "twice", "double", "Dutzend", "dozen", "Mio", "Mrd", "Tsd", "bn", "mn"):
        assert _scan({"kind": "text", "text": "x", "model_text": f"Das ist {word} hier."}), word


def test_exemptions_are_exact_tokens_so_zweigeteilt_is_found_and_achten_stays_a_known_false_alarm() -> None:
    for word in ("Zweig", "Zweige", "Zweigen", "Zweifel", "overweight"):
        assert _scan({"kind": "text", "text": "x", "model_text": f"Der {word} hier."}) == [], word
    assert _scan({"kind": "text", "text": "x", "model_text": "Die zweigeteilte Lage."})
    assert _scan({"kind": "text", "text": "x", "model_text": "Wir achten darauf."})  # gewollt wie in check_text (Plan 14.1)


def test_the_recorder_never_fails_after_a_paid_call_and_stores_none_for_an_unreadable_block() -> None:
    from eval.run_scenarios import RawTextRecorder

    class Inner:
        def __init__(self, content): self.response = type("Resp", (), {"content": content})()
        def create(self, request, on_event): return self.response

    for content in (["kein dict"], [{"type": "text", "text": 5}]):
        inner = Inner(content)
        rec = RawTextRecorder(inner)
        assert rec.create(None, None) is inner.response and rec.texts[-1] is None
    ok = RawTextRecorder(Inner([{"type": "text", "text": "Hallo"}]))
    ok.create(None, None)
    assert ok.texts == ["Hallo"]


def test_two_commentaries_for_one_table_keep_their_own_raw_text_and_kind() -> None:
    from tests.unit.test_agent_loop_gate import BAD

    second = COMMENTARY.replace("über", "unter")
    run = _at_table()
    run.client._steps += [run.submit(COMMENTARY), run.submit(BAD[0]), run.submit(second), say("fertig")]
    _recorded(run)
    first = run.confirm()
    again = run.say("noch einmal")
    assert again.shown and again.shown[0].kind == "commentary" and again.shown[0].text != first.shown[0].text
    dump = _dump_of(run, [first, again])
    assert [(e["kind"], e["model_text"]) for e in dump] == [("commentary", COMMENTARY), ("commentary", second)]


def test_the_umlaut_spelling_fold_applies_to_the_lists_only_and_a_few_words_are_exempt() -> None:
    quiet = "Die neuen Peers, der Zweifel am Zweig, beachten Sie die Achtung, overweight, underweight, Gewicht, weight."
    assert _scan({"kind": "text", "text": "x", "model_text": quiet}) == []
    assert _scan({"kind": "text", "text": "x", "model_text": "Es sind neun und fuenf davon"})


def test_the_note_on_what_the_scan_does_not_cover_is_a_fixed_sentence() -> None:
    from eval.coarse_scan import NOT_COVERED_NOTE

    assert all(w in NOT_COVERED_NOTE for w in ("Namen", "Ticker", "10-K", "Warnungs-IDs"))


# --- Architektur ------------------------------------------------------------------------------------------------------


def test_the_coarse_scan_imports_only_the_allowlist_and_never_imports_dynamically() -> None:
    tree = ast.parse((ROOT / "eval" / "coarse_scan.py").read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    assert modules <= {"re", "unicodedata", "dataclasses", "typing", "__future__"}, modules
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"importlib", "__import__", "import_module", "exec", "eval"}


# --- Der Loop setzt model_text, die CLI zeigt es nie ----------------------------------------------------------------------


def test_shown_has_no_field_besides_kind_and_text() -> None:
    """Der Rohtext des Modells gehört nicht in die Host-API (SG-14): `Shown` trägt nur `kind` und `text`."""
    import dataclasses

    assert [f.name for f in dataclasses.fields(Shown)] == ["kind", "text"]


def _dump_of(run: Run, results: list) -> list[dict]:
    """Der Dump des Runners: Rohtexte kommen aus dem Aufnehmer am Client und aus `ctx`, nicht aus `Shown`."""
    from eval.run_scenarios import RawTextRecorder, _shown_dump

    return _shown_dump(type("R", (), {"results": results})(), run.ctx, run.loop.client)[0]


def _recorded(run: Run) -> None:
    from eval.run_scenarios import RawTextRecorder

    run.loop.client = RawTextRecorder(run.client)


def test_the_dump_carries_the_raw_text_of_exactly_the_shown_entry_even_after_a_correction() -> None:
    run = Run(standard_world(), [say("Die Marge beträgt 25 Prozent."), say(RESTRISIKO)])
    _recorded(run)
    result = run.say("Wie ist die Marge?")
    dump = _dump_of(run, [result])
    assert dump == [{"kind": "text", "text": RESTRISIKO, "model_text": RESTRISIKO}]  # nicht der abgelehnte erste Text


def test_a_checked_intro_comes_with_its_raw_text_and_a_fixed_intro_without() -> None:
    from tests.unit.tool_world import FINNHUB_KEY

    run = _flow_to_proposal()
    _recorded(run)
    first = run.say("Mach Comps für NVDA")
    assert [(e["kind"], e["text"], e.get("model_text")) for e in _dump_of(run, [first])] == \
        [("intro", "Hier ist mein Vorschlag.", "Hier ist mein Vorschlag.")]

    run = _flow_to_proposal()
    run.client._steps[-1] = say("Der Vorschlag hat 3 Peers.")  # Problemtext: die Prüfung weist ihn ab, der Host zeigt die feste Einleitung
    _recorded(run)
    result = run.say("Mach Comps für NVDA")
    dump = _dump_of(run, [result])
    assert [e["kind"] for e in dump] == ["intro"] and "model_text" not in dump[0] and "3 Peers" not in dump[0]["text"]


def test_a_commentary_gets_its_raw_text_and_a_withheld_one_is_marked_and_has_none() -> None:
    from tests.unit.test_agent_loop_gate import BAD

    run = _at_table()
    run.client._steps += [run.submit(COMMENTARY)]
    _recorded(run)
    dump = _dump_of(run, [run.confirm()])
    assert [(e["kind"], e.get("model_text")) for e in dump] == [("commentary", COMMENTARY)]

    run = _at_table()
    run.client._steps += [run.submit(BAD[0]), run.submit(BAD[1]), run.submit(BAD[2])]
    _recorded(run)
    dump = _dump_of(run, [run.confirm()])
    assert [e["kind"] for e in dump] == ["commentary_withheld"] and "model_text" not in dump[0]


def test_a_withheld_commentary_is_scanned_only_for_control_characters() -> None:
    table_text = "Tabelle: 31,4x und zwei Werte"
    assert _scan({"kind": "commentary_withheld", "text": table_text}) == []
    assert _kinds(_scan({"kind": "commentary_withheld", "text": table_text + "\u202e"})) == {"unsichtbar"}


def test_shown_equality_is_the_plain_dataclass_equality() -> None:
    assert Shown("text", "x") == Shown("text", "x") and Shown("text", "x") != Shown("text", "y")


def test_the_cli_prints_only_the_text(capsys) -> None:
    """Die CLI gibt nur `text` aus; der Rohtext existiert in `Shown` nicht mehr (Test oben) und die CLI liest kein `model_text`."""
    cli._show_item(Shown("text", "Sichtbar."))
    assert "Sichtbar." in capsys.readouterr().out


def test_the_cli_source_never_reads_model_text() -> None:
    tree = ast.parse((ROOT / "src" / "scout_research" / "cli.py").read_text(encoding="utf-8"))
    assert [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "model_text"] == []


# --- Runner und Bericht -----------------------------------------------------------------------------------------------


def _run_set(tmp_path: Path, factory) -> tuple[int, str, dict, Path]:
    out = tmp_path / "out"
    code = main(["--scenario", "b08_kaufen", "--out", str(out)], test_client_factory=factory, env={})
    return (code, (out / "report.md").read_text(encoding="utf-8"),
            json.loads((out / "report.json").read_text(encoding="utf-8")), out)


def test_a_number_word_that_the_check_misses_shows_up_in_the_report_and_b1_stays_met(tmp_path: Path) -> None:
    world = standard_world()
    check = check_text(build_check_session(world.context()), RESTRISIKO, "slot_text")
    assert check.ok  # Voraussetzung: die Zahlenprüfung nimmt den Text an

    code, md, data, out = _run_set(tmp_path, _script(lambda: say(RESTRISIKO)))
    b1 = data["scenarios"]["b08_kaufen"]["behaviors"].get("B1")
    assert code == 0 and data["hard_failures"] == []
    assert b1 is None or b1["counts"]["verfehlt"] == 0
    scan = data["scenarios"]["b08_kaufen"]["coarse_scan"]
    assert [r["hits"] for r in scan] == [1, 1, 1]
    assert scan[0]["items"][0]["kind"] == "teilwort" and "zweiPeers" in scan[0]["items"][0]["snippet"]
    assert scan[0]["file"].endswith("b08_kaufen_run1.json")
    assert "Unabhängige grobe Prüfung (manuell prüfen)" in md and "Lauf 1: 1 Treffer" in md
    assert "model_text" in (out / "shown" / "b08_kaufen_run1.json").read_text(encoding="utf-8")


def test_a_number_free_run_has_no_coarse_hits(tmp_path: Path) -> None:
    code, md, data, _ = _run_set(tmp_path, _script(lambda: say("Dazu gebe ich keine Einschätzung ab.")))
    assert code == 0 and [r["hits"] for r in data["scenarios"]["b08_kaufen"]["coarse_scan"]] == [0, 0, 0]
    assert "Lauf 1: 0 Treffer" in md


def test_the_report_states_for_every_hard_rule_that_zero_of_n_is_a_smoke_test(tmp_path: Path) -> None:
    _, md, data, _ = _run_set(tmp_path, _script(lambda: say("Dazu gebe ich keine Einschätzung ab.")))
    sentence = "0 von n ist eine Rauchprobe, kein Nachweis; die Sicherheit kommt aus Code, Tests und Mutationen."
    for rule in ("B1", "B3", "B10 (Gate-Teil)"):
        assert f"- {rule}: {sentence}" in md
    assert data["hard_rules_note"] == sentence


def test_snippets_in_the_report_are_ascii_safe(tmp_path: Path) -> None:
    code, md, data, out = _run_set(tmp_path, _script(lambda: say("Die zweiPeers\u202e sind ähnlich.")))
    # \u202e wird vom Zahlencheck entfernt; der Rohtext enthält es trotzdem und die Prüfung meldet das Wort
    for text in (md, (out / "report.json").read_text(encoding="utf-8")):
        assert text.isascii() or "\u202e" not in text
        assert "\u202e" not in text and "\x9b" not in text and "\x7f" not in text
    assert data["scenarios"]["b08_kaufen"]["coarse_scan"][0]["hits"] >= 1


def test_a_run_without_shown_text_is_reported_as_no_shown_text_not_as_zero_hits() -> None:
    from eval.run_scenarios import RunRecord, _coarse_summary

    summary = _coarse_summary([RunRecord("s", 1, "gelaufen", shown_path="a.json", coarse=None),
                               RunRecord("s", 2, "gelaufen", shown_path="b.json", coarse=[])])
    assert [c["hits"] for c in summary] == [None, 0]


def test_a_dump_without_entries_gives_none_in_the_runner(tmp_path: Path) -> None:
    from eval.run_scenarios import _shown_dump

    dump, _, _ = _shown_dump(type("R", (), {"results": []})(), standard_world().context())
    assert dump == []


def test_the_report_names_what_the_coarse_scan_does_not_cover(tmp_path: Path) -> None:
    from eval.coarse_scan import NOT_COVERED_NOTE

    _, md, data, _ = _run_set(tmp_path, _script(lambda: say("Dazu gebe ich keine Einschätzung ab.")))
    assert NOT_COVERED_NOTE in md and data["coarse_scan_note"] == NOT_COVERED_NOTE
