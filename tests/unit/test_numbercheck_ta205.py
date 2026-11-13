"""TA-20.5 (Fable-Review, mittlere Befunde): Listennummer-Ausnahme (Plan 11.2 Klasse 3), Namen mit Ziffer (I-1, N2) und
`SecretStr` für die Schlüssel der Settings. Einzelfälle des Korpus: ST-B136 bis ST-B148, ST-A37 bis ST-A41, RA-37 (Runde 2: HOCH 1 und 2, MITTEL 4, 5, NIEDRIG 7 bis 12)."""

from __future__ import annotations

import io
import logging

import pytest

from scout_research.config import Settings
from scout_research.live_guard import secrets_of
from scout_research.tools.commentary import build_check_session, check_text
from scout_research.tools.sanitize import Redactor
from tests.unit.number_check_corpus import build_session, load_corpus
from tests.unit.test_tools_commentary import _names_session

CORPUS = load_corpus()
SLOT = "[[co:ADBE:ev_ebitda]]"


def _naked(text: str, session=None) -> list[str]:
    session = session or build_session(CORPUS, "base")
    return [p.match for p in check_text(session, text, "slot_text").problems if p.code == "NAKED_NUMBER"]


# --- Teil A: Listennummer ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, found", [
    # die zwei reproduzierten Gegenproben
    ("Das Multiple betraegt\n17. Das ist hoch.", ["17"]),
    (f"Das EV liegt bei\n12) {SLOT}", ["12"]),
    # Bedingung 1 allein verletzt: Vorzeile ohne Satzende, Nummer 1
    ("Das EV liegt bei\n1. Das ist hoch.", ["1"]),
    (f"Das EV liegt bei\n1) {SLOT}", ["1"]),
    # Bedingung 2: Folge beginnt nicht bei 1
    ("Es gilt:\n2. Alpha\n3. Beta", ["2", "3"]),
    ("2. Alpha\n3. Beta", ["2", "3"]),
    # Lücke in der Folge: nur die Nummer nach der Lücke
    ("Es gilt:\n1. Alpha\n2. Beta\n4. Delta", ["4"]),
    # Wiederholung und neue Folge ohne Ende des Blocks
    ("Es gilt:\n1. Alpha\n1. Noch Alpha", ["1"]),
    # gewöhnliche Textzeile beendet den Block
    ("Es gilt:\n1. Alpha\nNoch Text.\n2. Beta", ["2"]),
    # Einheit, Faktor, Mengenwort hinter der Nummer
    ("Es gilt:\n1. x höher", ["1"]),
    ("Es gilt:\n1. fach so viel", ["1"]),
    ("Es gilt:\n1. -fach", ["1"]),
    ("Es gilt:\n1. mal", ["1"]),
    ("Es gilt:\n1. times", ["1"]),
    ("Es gilt:\n1. Peers", ["1"]),
    ("Es gilt:\n1) Unternehmen", ["1"]),
    ("Es gilt:\n1. Firmen", ["1"]),
    ("Es gilt:\n1. Werte", ["1"]),
    ("Es gilt:\n1. companies", ["1"]),
    ("Es gilt:\n1. peers", ["1"]),
])
def test_a_number_at_the_line_start_is_not_a_list_number_unless_context_and_sequence_fit(text: str, found: list[str]) -> None:
    assert _naked(text) == found, text


@pytest.mark.parametrize("text", [
    "Es gilt:\n1. Alpha\n2. Beta\n3. Gamma",
    "Das war es.\n\n1. Alpha\n2. Beta",
    "1. Alpha\n2. Beta",
    "Wichtig!\n1. Alpha",
    "Wirklich?\n1. Alpha",
    "Es gilt:\n- 1. Alpha\n- 2. Beta",
    "Es gilt:\n1) Alpha\n2) Beta\n3) Gamma",
    "Es gilt:\n1. Alpha\n\n2. Beta",  # Leerzeile hält den Block offen
    "Es gilt:\n1. Alpha.\n   weiter geht es.\n2. Beta",  # eingerückte Fortsetzung, Vorzeile endet mit Satzzeichen
    "Es gilt:\n1. Alpha\n- Punkt\n2. Beta",  # Aufzählungszeile im Block
    "Es gilt:\n1. xylophon\n2. Malerei",  # keine Einheit: Wortende zählt
    "Es gilt:\n1. Alpha\nNoch Text.\n\n1. Neuer Block",  # nach Ende des Blocks beginnt die Folge neu
])
def test_ordinary_lists_stay_exempt(text: str) -> None:
    assert _naked(text) == [], text


def test_a_list_number_from_a_non_ascii_digit_is_still_a_finding_in_a_valid_sequence() -> None:
    assert _naked("Es gilt:\n１. Alpha") == ["１"]
    assert _naked("Es gilt:\n1. Alpha\n２. Beta") == ["２"]
    assert _naked("Es gilt:\n1. Alpha\n2꣣. Beta") != []


# --- Teil B: Namen mit Ziffer (I-1, N2) ----------------------------------------------------------------------------


@pytest.mark.parametrize("names, text", [
    (["Acme Holdings 21"], "Das P/E liegt bei 21"),
    (["Group 7 Corp."], "Es gibt 7 Peers"),
    (["Q3 Metals Inc."], "Das war im Q3"),
    (["3M"], "Umsatz von 3M USD"),
    (["3M Co"], "Umsatz von 3M USD"),
])
def test_a_digit_in_a_name_does_not_free_that_digit_in_the_text(names: list[str], text: str) -> None:
    assert _naked(text, _names_session(*names)) != [], text


@pytest.mark.parametrize("names, text", [
    (["Group 7 Corp."], "Wie Group 7 Corp. oder Group 7."),
    (["Acme Holdings 21"], "Wie Acme Holdings 21 wächst es."),
    (["3M"], "Wie 3M."),
    (["3M"], "3M wächst"),
    (["3M Co"], "3M wächst, wie 3M Co."),
    (["Q3 Metals Inc."], "Wie Q3 Metals Inc. und Q3 Metals."),
])
def test_the_whole_name_and_one_word_names_stay_exempt(names: list[str], text: str) -> None:
    assert _naked(text, _names_session(*names)) == [], text


def test_n2_applies_to_the_whole_name_when_it_ends_in_a_digit_before_a_unit() -> None:
    session = _names_session("Acme Holdings 21")
    for tail in ("%", " %", " x", " Prozent", " Mio", " USD", " times", " CAD", "-USD"):
        assert _naked(f"Wie Acme Holdings 21{tail}", session) != [], tail
    assert _naked("Wie Acme Holdings 21, oder Acme Holdings 21.", session) == []


def test_exempt_terms_holds_no_single_word_of_a_multi_word_name_that_ends_in_a_digit() -> None:
    from scout_research.tools.commentary import exempt_terms

    terms = exempt_terms(["Acme Holdings 21", "Group 7 Corp.", "Q3 Metals Inc.", "3M", "3M Co"])
    assert not {"21", "7", "Q3"} & set(terms), terms
    assert {"Acme Holdings 21", "Group 7 Corp.", "Group 7", "Q3 Metals Inc.", "3M", "3M Co"} <= set(terms)


def test_candidate_set_terms_re_entering_the_session_do_not_bring_the_single_words_back() -> None:
    from tests.unit.test_tools_gate import start
    from tests.unit.tool_world import standard_world

    w = standard_world()
    w.add_synthetic("0000999007", "GRP", "Group 7 Corp.", "3674", 200e9)
    ctx, _ = start(w)
    assert ctx.candidate_sets and any("Group 7" in t for t in build_check_session(ctx).terms)
    session = build_check_session(ctx)
    assert "7" not in session.terms
    assert _naked("Das P/E liegt bei 7 und Group 7 Corp. ist ein Name.", session) == ["7"]


def test_the_candidate_of_a_follow_up_question_stays_exempt_only_as_a_whole_name() -> None:
    from tests.unit.test_ambiguous_candidates import _ambiguous, _group7_world, _problems

    ctx = _ambiguous(_group7_world(), "Mach Comps für Group", "Group 7")
    assert any(code == "NAKED_NUMBER" for code, _ in _problems(ctx, "Das P/E liegt bei 7."))
    assert any(code == "NAKED_NUMBER" for code, _ in _problems(ctx, "Es gibt 7 Peers."))
    assert _problems(ctx, "Meinst du Group 7 Corp.?") == []


# --- Teil C: SecretStr ---------------------------------------------------------------------------------------------

FAKE_KEY = "sk-ant-FAKE-ta205-0123456789abcdef"
FAKE_FINNHUB = "FAKE-FINNHUB-ta205-0123456789"


def _settings(**over) -> Settings:
    return Settings(_env_file=None, edgar_contact_email="tester@example.org", anthropic_api_key=FAKE_KEY,
                    finnhub_api_key=FAKE_FINNHUB, **over)


def test_settings_do_not_show_the_keys_in_any_standard_rendering() -> None:
    s = _settings()
    shown = [repr(s), str(s), format(s), f"{s}", f"{s!r}", repr(s.model_dump()), str(s.model_dump()), s.model_dump_json(),
             repr(s.anthropic_api_key), str(s.finnhub_api_key)]
    for text in shown:
        assert FAKE_KEY not in text and FAKE_FINNHUB not in text, text
    assert "anthropic_api_key" in repr(s)  # das Feld ist da, nur der Wert nicht


def test_readers_still_get_the_real_value() -> None:
    from scout_research.config import secret_value

    s = _settings()
    assert s.anthropic_api_key.get_secret_value() == FAKE_KEY
    assert secret_value(s.anthropic_api_key) == FAKE_KEY and secret_value(FAKE_KEY) == FAKE_KEY
    assert secret_value(None) is None and secret_value("") is None and secret_value(Settings(
        _env_file=None, edgar_contact_email="tester@example.org", anthropic_api_key="").anthropic_api_key) is None
    secrets = secrets_of(s)
    assert FAKE_KEY in secrets and FAKE_FINNHUB in secrets
    assert FAKE_KEY not in Redactor(secrets)(f"Schlüssel {FAKE_KEY} und {FAKE_FINNHUB}")


def test_the_redacting_log_handler_masks_the_key_taken_from_secret_str() -> None:
    from scout_research.live_guard import setup_logging, teardown_logging

    stream = io.StringIO()
    setup_logging(Redactor(secrets_of(_settings())), stream=stream, env={})
    try:
        logging.getLogger("anthropic").error("Schlüssel %s", FAKE_KEY)
    finally:
        teardown_logging()
    assert FAKE_KEY not in stream.getvalue()


@pytest.mark.parametrize("text", ["3. Segmente", "17. Segmente", "- 3. Segmente"])
def test_the_old_list_inputs_starting_above_one_are_now_naked_numbers(text: str) -> None:
    assert _naked(text, _names_session("Adobe Inc.")) != [], text
    assert _naked("\n".join(f"{i}. Segmente" for i in range(1, 11)), _names_session("Adobe Inc.")) == []


# --- Runde 2 ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name, text", [
    ("10x Genomics, Inc.", "Das EV/EBITDA liegt bei rund 10x."),
    ("1st Source Corp.", "Sie war 1st in der Liste."),
    ("22nd Century Group, Inc.", "Sie liegt auf dem 22nd Platz."),
    ("Group 7 Corp.", "Das P/E liegt bei 7."),
    ("Acme 2.5 Corp.", "Das P/E liegt bei 2.5."),
    ("H1 Metals Inc.", "Das war im H1."),
])
def test_a_single_word_that_is_itself_a_quantity_is_never_exempt_alone(name: str, text: str) -> None:
    assert _naked(text, _names_session(name)) != [], text


@pytest.mark.parametrize("name, text", [
    ("10x Genomics, Inc.", "10x Genomics wächst."),
    ("Five9, Inc.", "Wie Five9 wächst auch NICE."),
    ("8x8 Inc", "Wie 8x8 wächst."),
    ("Five9", "Five9 wächst."),
    ("8x8", "8x8 wächst."),
])
def test_names_that_are_not_pure_quantity_tokens_stay_exempt(name: str, text: str) -> None:
    assert _naked(text, _names_session(name)) == [], text


@pytest.mark.parametrize("text", [
    "Ein Einmaleffekt von $3M", "Ein Einmaleffekt von USD 3M", "Ein Einmaleffekt von €3M", "Ein Einmaleffekt von 3M-USD",
    "Ein Einmaleffekt von 3M US-Dollar", "Ein Einmaleffekt von 3M\nUSD", "Ein Einmaleffekt von £3M", "Wert in EUR 3M",
    "Ein Einmaleffekt von US-$3M",
])
def test_n2_also_looks_before_the_hit_and_accepts_other_separators(text: str) -> None:
    assert _naked(text, _names_session("3M Co")) != [], text
    assert _naked(text, _names_session("3M")) != [], text
    assert _naked("Wie 3M-Konzern und der 3M wächst.", _names_session("3M Co")) == []


# MITTEL 4: Fehlalarme


@pytest.mark.parametrize("text", [
    "**Einordnung:**\n1. Alpha\n2. Beta",
    "## Peers\n1. Alpha\n2. Beta",
    "__Fazit__\n\n1. Alpha",
    "Es gilt:\n1. Alpha\n   1. Unter-Alpha\n   2. Unter-Beta\n2. Beta\n3. Gamma",
    "Es gilt:\n1. Alpha\n\t1) a\n\t2) b\n2. Beta",
])
def test_markup_before_a_list_and_nested_lists_are_not_false_alarms(text: str) -> None:
    assert _naked(text, _names_session("Adobe Inc.")) == [], text


def test_nested_lists_still_need_a_sequence_per_level() -> None:
    session = _names_session("Adobe Inc.")
    assert _naked("Es gilt:\n1. Alpha\n   2. Unter-Beta", session) == ["2"]
    assert _naked("Es gilt:\n1. Alpha\n   1. a\n   3. c\n2. Beta", session) == ["3", "2"]  # nach einem Bruch beginnt die Folge neu
    assert _naked("Es gilt:\n1. Alpha\n   1. a\n3. Gamma", session) == ["3"]


# MITTEL 5: U+2028 und U+2029 als Zeilengrenze


@pytest.mark.parametrize("sep", ["\u2028", "\u2029"])
def test_line_and_paragraph_separators_are_line_breaks_for_the_list_rule(sep: str) -> None:
    session = _names_session("Adobe Inc.")
    assert _naked(f"Es gilt:\n1. Alpha{sep}Das KGV von ADBE liegt bei\n2. Das ist hoch.", session) == ["2"]
    assert _naked(f"Es gilt:{sep}1. Alpha{sep}2. Beta", session) == []
    assert _naked(f"Das KGV liegt bei{sep}1. Alpha", session) == ["1"]


# NIEDRIG 7 und 9


@pytest.mark.parametrize("word", ["Quartal", "Halbjahr", "Jahr", "Platz", "Rang", "Quarter", "place", "Quartale"])
def test_period_and_rank_words_after_a_list_number_make_it_a_quantity(word: str) -> None:
    assert _naked(f"Es gilt:\n1. {word} war gut", _names_session("Adobe Inc.")) == ["1"], word
    assert _naked("Es gilt:\n1. Quartalsbericht", _names_session("Adobe Inc.")) == []  # Wortgrenze


def test_a_sequence_up_to_twenty_is_exempt_and_the_twenty_first_is_a_finding() -> None:
    lines = [f"{i}. Alpha" for i in range(1, 22)]
    session = _names_session("Adobe Inc.")
    assert _naked("\n".join(lines[:20]), session) == []
    assert _naked("\n".join(lines), session) == ["21"]


# NIEDRIG 8: die Klasse-17-Fälle liegen in einem gültigen Folgekontext (Vorrang von Klasse 17 geprüft, nicht die Folgeregel)


def test_the_class_17_cases_sit_in_a_valid_sequence_context() -> None:
    from tests.unit.test_tools_commentary import _in_sequence_context

    assert _in_sequence_context("⼆. Segmente").startswith("Es gilt:\n1. Alpha\n") and "\n8. Alpha\n⼆. Segmente" in _in_sequence_context("⼆. Segmente")
    assert _in_sequence_context("W⼆ prüfen") == "W⼆ prüfen"


def test_class_17_beats_the_list_exemption_even_inside_a_valid_sequence() -> None:
    from scout_research.tools.numbercheck import normalize

    n = int(normalize("٣").norm)
    text = "Es gilt:\n" + "".join(f"{i}. Alpha\n" for i in range(1, n)) + "٣. Segmente"
    result = check_text(_names_session("Adobe Inc."), text, "slot_text")
    assert [p.match for p in result.problems if p.code == "NAKED_NUMBER"] == ["٣"]


# NIEDRIG 11: Fehlertexte der Settings


def test_a_validation_error_does_not_contain_the_key() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as info:
        Settings(_env_file=None, edgar_contact_email="", anthropic_api_key=FAKE_KEY, finnhub_api_key=FAKE_FINNHUB)
    text = str(info.value)
    assert FAKE_KEY not in text and FAKE_FINNHUB not in text and "edgar_contact_email" in text
    with pytest.raises(ValidationError) as info:
        Settings(_env_file=None, edgar_contact_email="tester@example.org", anthropic_api_key=FAKE_KEY, anthropic_max_tokens=3)
    assert FAKE_KEY not in str(info.value)
    with pytest.raises(ValidationError) as info:  # der Schlüssel selbst ist das fehlerhafte Eingabefeld (falscher Typ)
        Settings(_env_file=None, edgar_contact_email="tester@example.org", anthropic_api_key=[FAKE_KEY])
    assert FAKE_KEY not in str(info.value) and "anthropic_api_key" in str(info.value)


# NIEDRIG 10: die Leser bekommen ein str aus einer echten Settings-Instanz (kein Netz, kein SDK)


def test_the_finnhub_provider_factory_gets_a_str_from_real_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import scout_research.scratch as scratch

    seen: list[object] = []
    monkeypatch.setattr(scratch, "FinnhubProvider", lambda key, **kw: seen.append(key) or "provider")
    monkeypatch.setattr(scratch, "SqliteCache", lambda *a, **kw: "cache")
    monkeypatch.setattr(scratch, "CachedProvider", lambda **kw: kw)
    result, _ = scratch._market_provider(_settings())
    assert seen == [FAKE_FINNHUB] and type(seen[0]) is str and result["primary"] == "provider"
    seen.clear()
    scratch._market_provider(Settings(_env_file=None, edgar_contact_email="tester@example.org"))
    assert seen == []  # ohne Schlüssel kein Anbieter


def test_the_eval_runner_gives_the_sdk_factory_a_str_from_real_settings(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from eval.run_scenarios import main
    import eval.run_scenarios as runner
    from scout_research.agent.model_client import ScriptedModelClient
    from scout_research.agent.spend import SpendBudget, SpendLedger
    from tests.unit.test_eval_live import SCENARIO

    built: list[object] = []
    monkeypatch.setattr(runner, "make_sdk_client", lambda key: built.append(key) or "sdk")
    monkeypatch.setattr(runner, "AnthropicAdapter", lambda sdk: ScriptedModelClient([]))
    settings = _settings(runs_dir=tmp_path / "runs")
    budget = SpendBudget(1.0, 3.0, SpendLedger(tmp_path / "ledger.json"))
    main([*SCENARIO, "--live", "--out", str(tmp_path / "out")], budget_factory=lambda: budget, env={"SCOUT_EVAL_LIVE": "1"},
         isatty=lambda: True, input_fn=lambda prompt: "LIVE 3", settings_loader=lambda: settings)
    assert built and all(type(key) is str and key == FAKE_KEY for key in built)


# --- Runde 3 -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["3M Co", "3M"])
@pytest.mark.parametrize("text", [
    "Wie 3M Company wächst auch NICE.", "Das 3M-Unternehmen wächst.", "Die 3M Peers wachsen.", "Die 3M-Firmen wachsen.",
    "Wie 3M companies wachsen.", "Die 3M Werte steigen.",
])
def test_n2_ignores_company_and_count_words_after_a_name(name: str, text: str) -> None:
    assert _naked(text, _names_session(name)) == [], text


def test_company_words_still_end_a_list_exemption() -> None:
    assert _naked("Es gilt:\n1. Peers", _names_session("Adobe Inc.")) == ["1"]


def test_group_7_peers_is_a_name_mention_but_a_bare_7_peers_is_not() -> None:
    session = _names_session("Group 7 Corp.")
    assert _naked("Es gibt 7 Peers.", session) != []
    assert _naked("Group 7 Peers wachsen.", session) == []


@pytest.mark.parametrize("name, text", [
    ("21", "Das P/E liegt bei 21."),
    ("Q3", "Das war im Q3."),
    ("10x", "Das liegt bei 10x."),
    ("(Q3)", "Das war im Q3."),
    ("1st", "Er war 1st."),
])
def test_a_one_word_name_that_is_itself_a_quantity_is_never_exempt(name: str, text: str) -> None:
    assert _naked(text, _names_session(name)) != [], text


@pytest.mark.parametrize("name, text", [
    ("#1 Holdings", "Er ist #1 im Peer-Set."),
    ("-21 Corp", "Das P/E liegt bei -21."),
    ("20/20 Vision Inc.", "Das Verhältnis ist 20/20."),
    ("+7 Capital", "Das P/E liegt bei +7."),
    ("(Q3) Metals", "Das war im Q3."),
])
def test_edge_characters_do_not_hide_a_quantity_token(name: str, text: str) -> None:
    assert _naked(text, _names_session(name)) != [], text


def test_edge_characters_keep_real_names() -> None:
    assert _naked("Wie Five9 wächst.", _names_session("(Five9), Inc.")) == []
    assert _naked("Wie 20/20 Vision Inc. wächst.", _names_session("20/20 Vision Inc.")) == []


@pytest.mark.parametrize("text", [
    "Ein Einmaleffekt von Dollar 3M", "Ein Einmaleffekt von Euro 3M", "Ein Einmaleffekt von CAD 3M", "Ein Einmaleffekt von JPY 3M",
    "Ein Einmaleffekt von SGD 3M", "Ein Einmaleffekt von CHF 3M", "Ein Einmaleffekt von $-3M", "Ein Einmaleffekt von USD      3M",
    "Ein Einmaleffekt von USD\n\n   3M", "Ein Einmaleffekt von 3M  USD", "Ein Einmaleffekt von 3M\tUSD",
    "Ein Einmaleffekt von 3M\u00a0USD", "Ein Einmaleffekt von 3M \n Dollar", "Ein Einmaleffekt von 3M \u2028USD",
    "Ein Einmaleffekt von 3M HKD", "Ein Einmaleffekt von AUD 3M", "Ein Einmaleffekt von 3M - 3M EUR",
])
def test_n2_currency_before_and_any_whitespace_after(text: str) -> None:
    assert _naked(text, _names_session("3M Co")) != [], text


def test_a_currency_word_inside_another_word_does_not_count_before_a_name() -> None:
    assert _naked("Der Eurovision 3M wächst.", _names_session("3M Co")) == []
    assert _naked("Wie USDX 3M wächst.", _names_session("3M Co")) == []


@pytest.mark.parametrize("prev", ["# Das KGV liegt bei", "## Das KGV liegt bei dem Wert von heute", "# Alpha Beta Gamma Delta"])
def test_a_long_heading_without_sentence_end_is_no_valid_preceding_line(prev: str) -> None:
    assert _naked(f"{prev}\n1. Das ist hoch.", _names_session("Adobe Inc.")) == ["1"], prev


@pytest.mark.parametrize("prev", ["# Peers", "## Einordnung der Peers", "# Alpha Beta Gamma", "# Das KGV liegt bei dem Wert."])
def test_a_short_heading_or_a_sentence_is_a_valid_preceding_line(prev: str) -> None:
    assert _naked(f"{prev}\n1. Alpha\n2. Beta", _names_session("Adobe Inc.")) == [], prev
