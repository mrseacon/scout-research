"""submit_commentary mit den echten Handlern (Plan, Abschnitt 11; Schritt 6, T2): Rendering, Korrekturrunden und
Rückhalt, Pflichtwarnungen (O4), Basiszeile (O5), englische Vorlagen, fin:/mkt:-Slots (O1 B), Prüfung vor der Tabelle.
Das Prüfkorpus selbst läuft in `test_number_check_corpus.py`."""

import pytest

from scout_research.tools.session import HumanInput
from scout_research.tools.commentary import build_check_session, check_text
from scout_research.tools.handlers import dispatch
from tests.unit.tool_world import AAPL_CIK, MSFT_CIK, NVDA_CIK, call, confirmed, propose, standard_world, start


def _table(w=None, **context_kwargs):
    w = w or standard_world()
    if context_kwargs:
        ctx = w.context(**context_kwargs)
        ctx.record_user_message(HumanInput("Mach Comps für NVDA"))
        call(ctx, "resolve_company", query="NVDA")
        set_id = call(ctx, "find_peer_candidates", target_cik=NVDA_CIK)["candidate_set_id"]
        proposal = propose(ctx, set_id)
        ps = ctx.confirm_peer_set(proposal["proposal_id"], [AAPL_CIK, MSFT_CIK]).id
    else:
        ctx, ps = confirmed(w)
    table = call(ctx, "compute_comps_table", peer_set_id=ps)
    return ctx, table["comps_table_id"], table


def _submit(ctx, table_id: str, text: str) -> dict:
    return call(ctx, "submit_commentary", comps_table_id=table_id, text=text)


def _code(result: dict) -> str:
    return result["error"]["code"]


# --- Annahme und Rendering ---------------------------------------------------------------------------------------


def test_an_accepted_commentary_is_rendered_by_the_code_with_basis_line_footnotes_and_missing_warnings() -> None:
    ctx, tid, _ = _table()
    result = _submit(ctx, tid, "NVDA liegt beim EV/EBITDA mit [[co:NVDA:ev_ebitda]] über dem Median [[stat:ev_ebitda:median]] ([[warn:W1]]).")
    assert result == {"status": "accepted", "slots_used": 3, "warnings_addressed": ["W1"], "warnings_not_addressed": ["W2"]}

    stored = ctx.commentaries[tid]
    lines = stored.rendered.split("\n")
    assert lines[0] == "Basis: NVDA (Ziel), GJ 2026, Periodenende 25.01.2026; Kurse vom 08.10.2026; 2 Peers, davon 2 mit EV-Multiples."
    assert "NVDA liegt beim EV/EBITDA mit 32,8x* über dem Median 26,0x (n = 1)*‡ ([W1: AAPL])." in lines
    assert "* EBITDA angenähert als EBIT + D&A." in lines
    assert "‡ Nicht in der Statistik enthalten: EV/EBITDA ohne MSFT (EBITDA nicht verfügbar.)." in lines
    assert lines[-2:] == ["Im Kommentar nicht angesprochene Warnungen (vom System ergänzt):",
                          "- [W2: MSFT] (warning) Das Geschäftsjahresende weicht vom Ziel ab; die Kennzahlen sind nicht periodengleich."]
    assert stored.status == "accepted" and stored.attempts == 1 and stored.raw_text.startswith("NVDA liegt")
    assert ctx.commentary_log[-1]["action"] == "accepted" and ctx.commentary_log[-1]["slots"][0]["path"] == "target.ev_ebitda"


def test_only_warnings_from_stage_warning_up_are_mandatory_and_info_markers_count_as_addressed() -> None:
    ctx, tid, table = _table()
    assert [w["id"] for w in table["warnings"] if w["severity"] != "info"] == ["W1", "W2"]
    result = _submit(ctx, tid, "Beide Peers weichen ab ([[warn:W1]], [[warn:W2]]); MSFT fehlt EBITDA ([[warn:W3]]).")
    assert result["warnings_addressed"] == ["W1", "W2", "W3"] and result["warnings_not_addressed"] == []
    assert "nicht angesprochen" not in ctx.commentaries[tid].rendered
    assert "[W3: MSFT]" in ctx.commentaries[tid].rendered


def test_a_bare_warning_id_in_the_text_is_not_coverage() -> None:
    ctx, tid, _ = _table()
    result = _submit(ctx, tid, "Siehe W1 und W2.")
    assert result["status"] == "accepted" and result["warnings_not_addressed"] == ["W1", "W2"]


def test_the_model_text_with_slots_never_reaches_the_user_unrendered() -> None:
    ctx, tid, _ = _table()
    _submit(ctx, tid, "Median: [[stat:pe:median]].")
    assert "[[" not in ctx.commentaries[tid].rendered and "]]" not in ctx.commentaries[tid].rendered


# --- Abweisung, Korrekturrunden, Rückhalt -------------------------------------------------------------------------


def test_a_rejection_reports_all_problems_at_once_with_the_most_specific_code() -> None:
    ctx, tid, _ = _table()
    result = _submit(ctx, tid, "ORCL [[co:ORCL:pe]], MSFT [[co:MSFT:ev_ebitda]], NVDA 32,8x und zwei Peers.")
    assert _code(result) == "SLOT_UNKNOWN"
    problems = result["error"]["details"]["problems"]
    assert [(p["code"], p["kind"]) for p in problems] == [
        ("SLOT_UNKNOWN", "unknown_ticker"), ("SLOT_VALUE_UNAVAILABLE", "value_none"),
        ("NAKED_NUMBER", "digits"), ("NAKED_NUMBER", "number_word")]
    assert problems[1]["reason"] == "EBITDA nicht verfügbar."  # Grund aus dem Tool-Ergebnis, nie ein Ersatzwert
    assert "[[" not in str(result)  # Slot-Begrenzer werden in Fehlerdetails maskiert


def test_two_correction_rounds_then_the_commentary_is_withheld_and_the_table_is_shown_instead() -> None:
    ctx, tid, _ = _table()
    assert _code(_submit(ctx, tid, "NVDA 32,8x")) == "NAKED_NUMBER"
    assert _code(_submit(ctx, tid, "NVDA 33x")) == "NAKED_NUMBER"
    assert tid not in ctx.commentaries  # nach zwei Abweisungen noch nichts zurückgehalten
    third = _submit(ctx, tid, "NVDA 34x")
    assert _code(third) == "COMMENTARY_WITHHELD" and third["error"]["retryable"] is False

    stored = ctx.commentaries[tid]
    assert stored.status == "withheld" and stored.attempts == 3
    assert stored.rendered.startswith("Kommentar zurückgehalten:")
    for expected in ("| NVDA (Ziel) |", "| AAPL |", "| MSFT |", "[W1: AAPL]", "Min", "Median", "(n = 1)"):
        assert expected in stored.rendered, expected
    assert "34x" not in stored.rendered
    assert ctx.commentary_log[-1]["action"] == "withheld"
    # auch ein korrekter Kommentar wird jetzt nicht mehr angenommen, bis der Mensch wieder schreibt
    assert _code(_submit(ctx, tid, "Median: [[stat:pe:median]].")) == "COMMENTARY_WITHHELD"
    ctx.record_user_message(HumanInput("Versuch es bitte noch einmal"))
    assert _submit(ctx, tid, "Median: [[stat:pe:median]].")["status"] == "accepted"
    assert ctx.commentaries[tid].status == "accepted"


def test_an_accepted_commentary_after_corrections_is_logged_as_such() -> None:
    ctx, tid, _ = _table()
    assert _code(_submit(ctx, tid, "NVDA 32,8x")) == "NAKED_NUMBER"
    assert _submit(ctx, tid, "NVDA [[co:NVDA:ev_ebitda]]")["status"] == "accepted"
    assert [e["action"] for e in ctx.commentary_log] == ["rejected", "accepted_after_n"]
    assert ctx.commentaries[tid].attempts == 2


def test_the_same_rejected_text_hits_the_loop_guard_instead_of_counting_again() -> None:
    ctx, tid, _ = _table()
    assert _code(_submit(ctx, tid, "NVDA 32,8x")) == "NAKED_NUMBER"
    assert _code(_submit(ctx, tid, "NVDA 32,8x")) == "LOOP_GUARD"
    assert ctx.commentary_state[tid].rejections == 1


def test_an_unknown_or_wrong_typed_handle_is_unknown_handle() -> None:
    ctx, tid, _ = _table()
    assert _code(_submit(ctx, "ct_nope", "x")) == "UNKNOWN_HANDLE"
    assert _code(_submit(ctx, "pp_0001", "x")) == "UNKNOWN_HANDLE"


def test_the_gate_still_blocks_submit_commentary_while_a_proposal_awaits_confirmation() -> None:
    ctx, set_id = start(standard_world())
    propose(ctx, set_id)
    assert _code(call(ctx, "submit_commentary", comps_table_id="ct_x", text="x")) == "AWAITING_PEER_CONFIRMATION"


# --- Pflichtwarnung für übersprungene Peers (E5, O4) -------------------------------------------------------------


def test_a_skipped_peer_is_a_mandatory_warning_that_the_code_adds_when_the_model_forgets_it() -> None:
    w = standard_world()
    ctx, ps = confirmed(w)
    del w.facts[AAPL_CIK]
    ctx._facts.clear()
    table = call(ctx, "compute_comps_table", peer_set_id=ps)
    assert table["skipped_peers"][0]["ticker"] == "AAPL" and table["warnings"][0]["kind"] == "peer_skipped"
    tid = table["comps_table_id"]

    bare = _submit(ctx, tid, "Median: [[stat:pe:median]].")
    assert bare["warnings_not_addressed"][0] == "W1"
    assert "- [W1: AAPL] (warning) Der Peer wurde übersprungen (bei der SEC liegen keine Daten für das Unternehmen vor)" in ctx.commentaries[tid].rendered
    assert "Übersprungen: AAPL." in ctx.commentaries[tid].rendered.split("\n")[0]

    ctx.record_user_message(HumanInput("nochmal"))
    covered = _submit(ctx, tid, "AAPL fehlt in der Tabelle ([[warn:W1]]).")
    assert covered["warnings_not_addressed"] == [] or "W1" not in covered["warnings_not_addressed"]
    assert "AAPL fehlt in der Tabelle ([W1: AAPL])." in ctx.commentaries[tid].rendered

    ctx.record_user_message(HumanInput("und AAPL?"))
    refused = _submit(ctx, tid, "AAPL: [[co:AAPL:revenue]]")
    problem = refused["error"]["details"]["problems"][0]
    assert _code(refused) == "SLOT_VALUE_UNAVAILABLE" and problem["kind"] == "peer_skipped"
    assert problem["reason"] == "Bei der SEC liegen keine Daten für das Unternehmen vor."


# --- Englisch (Sprache der Sitzung) ------------------------------------------------------------------------------


def test_english_session_renders_english_templates_and_formats() -> None:
    ctx, tid, table = _table(output_language="en")
    assert table["warnings"][0]["text"].startswith("The fiscal year end differs")
    assert table["peers"][1]["excluded"]["ev_ebitda"] == "EBITDA not available."
    result = _submit(ctx, tid, "NVDA trades at [[co:NVDA:ev_ebitda]] versus a median of [[stat:ev_ebitda:median]] ([[warn:W1]]).")
    assert result["warnings_not_addressed"] == ["W2"]
    rendered = ctx.commentaries[tid].rendered
    assert rendered.split("\n")[0] == ("Basis: NVDA (target), FY 2026, period end Jan 25, 2026; prices as of Oct 8, 2026; "
                                       "2 peers, 2 with EV multiples.")
    assert "NVDA trades at 32.8x* versus a median of 26.0x (n = 1)*‡ ([W1: AAPL])." in rendered
    assert "* EBITDA approximated as EBIT + D&A." in rendered
    assert "‡ Not included in the statistic: EV/EBITDA without MSFT (EBITDA not available.)." in rendered
    assert "Warnings not addressed in the commentary (added by the system):" in rendered
    assert "- [W2: MSFT] (warning) The fiscal year end differs from the target" in rendered


# --- fin:- und mkt:-Slots, Prüfung ohne Tabelle (O1 B) ------------------------------------------------------------


def test_fin_and_mkt_slots_resolve_against_the_tool_results_the_model_saw() -> None:
    ctx, tid, _ = _table()
    fin = call(ctx, "get_financials", cik=NVDA_CIK, metrics=["revenue", "ebitda"])
    call(ctx, "get_market_data", ticker="NVDA")
    fid = fin["financials_id"]
    text = f"NVDA: Umsatz [[fin:{fid}:revenue]], EBITDA [[fin:{fid}:ebitda]], Kurs [[mkt:NVDA:price]]."
    assert _submit(ctx, tid, text)["status"] == "accepted"
    assert "NVDA: Umsatz 215.938 Mio. USD, EBITDA 133.230 Mio. USD*, Kurs 180,00 USD." in ctx.commentaries[tid].rendered
    ctx.record_user_message(HumanInput("weiter"))
    assert _code(_submit(ctx, tid, f"[[fin:{fid}:total_assets]]")) == "SLOT_UNKNOWN"  # nicht abgefragt
    assert _code(_submit(ctx, tid, "[[fin:fin_zzzz:revenue]]")) == "SLOT_UNKNOWN"
    ctx.record_user_message(HumanInput("und weiter"))  # die dritte Abweisung in einer Runde würde den Kommentar zurückhalten
    assert _code(_submit(ctx, tid, "[[mkt:AAPL:price]]")) == "SLOT_UNKNOWN"  # get_market_data nie für AAPL aufgerufen


def test_check_text_before_any_table_allows_identifiers_and_form_names_but_no_numbers_or_table_slots() -> None:
    ctx, set_id = start(standard_world())
    session = build_check_session(ctx)
    assert session.table is None
    ok = check_text(session, "Meinen Sie NVIDIA CORP (CIK 0001045810, SIC-Code 3674)? Laut 10-K ist das Ziel eindeutig.")
    assert ok.ok, [(p.kind, p.match) for p in ok.problems]
    bad = check_text(session, "Es gibt drei Kandidaten, davon 40 gezeigt; [[co:NVDA:pe]] fehlt noch.")
    assert {(p.code, p.kind) for p in bad.problems} == {("NAKED_NUMBER", "number_word"), ("NAKED_NUMBER", "digits"),
                                                         ("SLOT_UNKNOWN", "no_table")}
    wrong_cik = check_text(session, "Meinen Sie CIK 0001045811?")
    assert [p.match for p in wrong_cik.problems] == ["0001045811"]


# --- Peer-Begründungen: typisierter Backstop statt „keine Ziffer“ ---------------------------------------------------


@pytest.mark.parametrize("rationale", ["Verkauft Hardware und, laut 10-K, Dienste", "B2B-Abos für Großkunden",
                                       "Wie 3M ein breit diversifizierter Konzern"])
def test_rationales_may_contain_form_names_domain_terms_and_candidate_names(rationale: str) -> None:
    w = standard_world()
    w.add_synthetic("0000999001", "MMM", "3M Co", "3674", 200e9)
    ctx, set_id = start(w)
    result = call(ctx, "propose_peer_set", candidate_set_id=set_id, peers=[{"cik": AAPL_CIK, "rationale": rationale}])
    assert result["status"] == "awaiting_human_confirmation"


@pytest.mark.parametrize("rationale", ["Zwei Segmente", "Zweitgrößter Anbieter", "Doppelt so groß wie das Ziel", "Seit Jahren Marktführer, Umsatz 5"])
def test_rationales_with_number_words_ranks_or_comparisons_are_rejected(rationale: str) -> None:
    ctx, set_id = start(standard_world())
    result = call(ctx, "propose_peer_set", candidate_set_id=set_id, peers=[{"cik": AAPL_CIK, "rationale": rationale}])
    assert _code(result) == "NAKED_NUMBER" and result["error"]["details"]["problems"][0]["field"] == "peers[0].rationale"
    assert not dispatch(ctx, "resolve_company", {"query": "NVDA"}).is_error  # abgewiesen: keine Gate-Sperre


# --- Ausnahmen für Namen dürfen keine Zahlwörter freischalten ---------------------------------------------------


def _names_session(*names: str):
    from scout_research.tools.commentary import exempt_terms
    from scout_research.tools.slots import CheckSession

    return CheckSession(terms=tuple(exempt_terms(list(names))))


def test_a_company_named_like_a_number_word_does_not_whitelist_that_word() -> None:
    session = _names_session("Five Below, Inc.", "FIVE", "Nine Energy Service, Inc.", "TWO", "3M Co", "Twenty-First Century Fox, Inc.")
    ok = lambda text: check_text(session, text, "rationale").ok  # noqa: E731
    # der Name selbst, in der Schreibweise der Sitzung, und Ticker in Großschreibung sind Namen
    assert ok("Ähnlich wie Five Below und Nine Energy Service.") and ok("FIVE und TWO sind Ticker.") and ok("Wie 3M oder 3m.")
    assert ok("Wie Twenty-First Century Fox.")
    # aber das Zahlwort allein bleibt eine Zahl
    for text in ("Five Peers", "five peers", "Nine Segmente", "Wir haben two Peers", "Hat nine Segmente"):
        assert not ok(text), text


# --- Umgehungsversuche, die das Korpus nicht einzeln enthält --------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Das sind ein Prozent.", "That is one percent.", "anderthalb Mal", "eineinhalb Jahre", "nullkommafünf", "fünfkommafünf",
    "🔟 Peers", "z w e i Peers", "e.i.n.s", "Z​wei Peers", "&frac12;", "𝟏𝟕", "1️⃣", "two-thirds", "one seven point five",
    "dreihundert", "Dreißig", "ein Zehntel",
])
def test_further_obfuscated_numbers_are_caught(text: str) -> None:
    session = _names_session("Adobe Inc.")
    assert not check_text(session, text, "slot_text").ok, text


# TA-8: Zahlzeichen mit angehängtem Zeichen (Plan 11.1 Punkt 2, Klasse 17) und unsichtbare Variantenzeichen

_MARKED_NUMBERS = [
    # (Text, Fundstelle im Rohtext: das Zahlzeichen samt Anhang bzw. die ganze Folge bis zur letzten Ziffer)
    ("٣\ufe0f٥\ufe0f %", "٣\ufe0f٥"),
    ("٣\u0301٥\u0301 %", "٣\u0301٥\u0301"),
    ("\U0001F51F\ufe0f", "\U0001F51F"),
    ("三\u0301 Prozent", "三\u0301"),
    ("Ⅻ\u0301", "Ⅻ\u0301"),
    ("१\ufe0f२\ufe0f", "१\ufe0f२"),
    ("zwe\ufe0fi Peers", "zwe\ufe0fi"),
    ("1\u034f2", "1\u034f2"),
    ("١\U000E0100٢", "١\U000E0100٢"),
    ("１\u0301７\u0301 %", "１\u0301７\u0301"),
]


@pytest.mark.parametrize(("text", "finding"), _MARKED_NUMBERS)
def test_numbers_with_appended_marks_or_invisible_variation_selectors_are_found_at_the_raw_position(text: str, finding: str) -> None:
    result = check_text(_names_session("Adobe Inc."), text, "slot_text")
    assert not result.ok, text
    assert {p.code for p in result.problems} == {"NAKED_NUMBER"}, [(p.code, p.kind) for p in result.problems]
    a = text.index(finding)
    assert any(p.start <= a and p.end >= a + len(finding) for p in result.problems), [(p.start, p.end, p.match) for p in result.problems]


@pytest.mark.parametrize("text", ["⚠\ufe0f Bitte prüfen", "Nestlé", "Nestle\u0301", "Café", "Cafe\u0301 ist ein Wort", "\u2764\ufe0f gefällt"])
def test_variation_selectors_without_a_number_and_accents_in_words_stay_accepted(text: str) -> None:
    result = check_text(_names_session("Adobe Inc."), text, "slot_text")
    assert result.ok, [(p.code, p.kind, p.match) for p in result.problems]


def test_invisible_variation_selectors_are_removed_and_counted_like_format_chars() -> None:
    assert check_text(_names_session("Adobe Inc."), "⚠\ufe0f Bitte prüfen \U000E0100x\u034f", "slot_text").format_chars == 3
    assert check_text(_names_session("Adobe Inc."), "a\u180bb\u180cc\u180dd\u180fe", "slot_text").format_chars == 4
    assert check_text(_names_session("Adobe Inc."), "Nestle\u0301", "slot_text").format_chars == 0  # sichtbarer Akzent bleibt


def test_keycap_form_is_still_found_after_the_variation_selector_is_removed() -> None:
    result = check_text(_names_session("Adobe Inc."), "Platz 1\ufe0f\u20e3", "slot_text")
    assert not result.ok and {p.code for p in result.problems} == {"NAKED_NUMBER"}


# TA-9: Zahlzeichen, die erst NFKC erzeugt, sichtbare Ziffern der Kategorie Mn, Bildzeichen mit Zahl, Khmer-Variantenzeichen

_FURTHER_NUMBERS = ["⼆ Prozent", "🈔 Peers", "🉁", "ADBE꣣꣥", "💯 % Marge", "⚀ Peers", "🔞", "zwe\u17b4i Peers", "zwe\u17b5i Peers",
                    "\U0001F51F\u0301", "🔢 Peers", "\U0001F4AF\u0301 %", "🆛 Peers", "🆢"]


@pytest.mark.parametrize("text", _FURTHER_NUMBERS)
def test_numbers_made_by_nfkc_visible_mark_digits_and_pictographs_are_caught(text: str) -> None:
    result = check_text(_names_session("Adobe Inc."), text, "slot_text")
    assert not result.ok, text
    assert {p.code for p in result.problems} == {"NAKED_NUMBER"}, [(p.code, p.kind) for p in result.problems]


def test_exact_raw_positions_of_a_nfkc_made_number_and_of_a_mark_digit_behind_letters() -> None:
    session = _names_session("Adobe Inc.")
    assert [(p.start, p.end) for p in check_text(session, "⼆ Prozent", "slot_text").problems] == [(0, 1)]
    assert [(p.start, p.end) for p in check_text(session, "ADBE꣣꣥", "slot_text").problems] == [(0, 6)]


@pytest.mark.parametrize("text", ["👍 Gut", "🔥 Stark", "⚠\ufe0f Bitte prüfen", "🆕 Neu", "🚀 Rakete"])
def test_pictographs_without_a_number_stay_accepted(text: str) -> None:
    assert check_text(_names_session("Adobe Inc."), text, "slot_text").ok, text


def test_invariant_normalize_leaves_no_non_ascii_numeric_character_for_any_code_point() -> None:
    """Plan 11.1 Punkt 2: Kein Zeichen mit `isnumeric()` außer ASCII-Ziffern darf in `norm` überleben, auch nicht mit
    angehängtem Zeichen. Die Codepunkte werden zur Laufzeit erzeugt, damit neue Unicode-Versionen mitgeprüft werden."""
    import sys
    import unicodedata

    from scout_research.tools.numbercheck import normalize

    leaks = []
    for cp in range(0x80, sys.maxunicode + 1):
        ch = chr(cp)
        if unicodedata.category(ch) in ("Cn", "Co", "Cs"):
            continue
        for appended in ("", "\u0301"):
            norm = normalize(ch + appended).norm
            if any(not c.isascii() and c.isnumeric() for c in norm):
                leaks.append(f"U+{cp:04X}{'+U+0301' if appended else ''}")
    assert not leaks, leaks[:20]


def test_invariant_every_visible_mark_digit_behind_a_letter_is_a_finding() -> None:
    import sys
    import unicodedata

    digits = [chr(cp) for cp in range(0x80, sys.maxunicode + 1)
              if unicodedata.category(chr(cp)).startswith("M") and "DIGIT" in unicodedata.name(chr(cp), "")]
    assert len(digits) >= 17
    session = _names_session("Adobe Inc.")
    missed = [f"U+{ord(d):04X}" for d in digits if check_text(session, "a" + d, "slot_text").ok]
    assert not missed, missed


# Unicode-Eigenschaft Default_Ignorable_Code_Point, soweit Kategorie Mn (UCD 15/16, DerivedCoreProperties.txt)
_DEFAULT_IGNORABLE_MN = {0x034F, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F, *range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0)}


def test_invisible_marks_are_exactly_the_default_ignorable_mn_characters() -> None:
    import unicodedata

    from scout_research.tools.numbercheck import _INVISIBLE_MARKS

    assert all(unicodedata.category(chr(cp)) == "Mn" for cp in _DEFAULT_IGNORABLE_MN)
    assert set(_INVISIBLE_MARKS) == _DEFAULT_IGNORABLE_MN


def test_an_accepted_text_with_a_variation_selector_is_rendered_without_it_and_a_slot_with_one_resolves_like_with_a_cf_char() -> None:
    ctx, tid, _ = _table()
    session = build_check_session(ctx)
    plain = check_text(session, "NVDA ⚠ liegt mit [[co:NVDA:ev_ebitda]] vorn.")
    assert plain.ok
    for invisible in ("\ufe0f", "\u200b"):
        result = check_text(session, f"NVDA ⚠{invisible} liegt mit [[co:NVDA{invisible}:ev_ebitda]] vorn.")
        assert result.ok, [(p.code, p.match) for p in result.problems]
        assert result.body == plain.body and invisible not in result.body
        assert result.format_chars == 2
    assert "32,8x" in plain.body


# TA-10: Klasse 17 geht vor; keine Ausnahme (Listennummer, Warnungs-ID, Formname …) gilt für eine Stelle, die aus einem
# Nicht-ASCII-Zeichen entstanden ist

_EXEMPT_COLLISIONS = ["💯. Segmente", "百. Segmente", "⼆. Segmente", "٣. Segmente", "١٧. Segmente", "①. Segmente", "🈔) Peers",
                      "- ⼆. Segmente", "1꣣. Segmente", "⚀. Segmente", "W⼆ prüfen", "１０-K", "Das １０-K nennt es."]


def _collision_session():
    from scout_research.tools.slots import CheckSession

    return CheckSession(warning_ids=frozenset({"W9", "W1"}))


@pytest.mark.parametrize("text", _EXEMPT_COLLISIONS)
def test_an_exemption_never_covers_a_digit_that_came_from_a_non_ascii_character(text: str) -> None:
    result = check_text(_collision_session(), text, "slot_text")
    assert not result.ok and result.body is None, text
    assert {p.code for p in result.problems} == {"NAKED_NUMBER"}, [(p.code, p.kind, p.match) for p in result.problems]
    violations = [(e["start"], e["end"]) for e in result.extracted if e["status"] == "violation"]
    assert violations and all(any(p.start == a and p.end == b for p in result.problems) for a, b in violations), result.extracted
    assert not any(e["status"] == "exempt" and e["start"] < violations[0][1] and violations[0][0] < e["end"] for e in result.extracted)


@pytest.mark.parametrize("text", ["3. Segmente", "17. Segmente", "- 3. Segmente", "Das 10-K nennt es.", "Siehe W1.", "Wie 3M oder 3m."])
def test_ascii_exemptions_stay_valid(text: str) -> None:
    from scout_research.tools.commentary import exempt_terms
    from scout_research.tools.slots import CheckSession

    session = CheckSession(warning_ids=frozenset({"W1"}), terms=tuple(exempt_terms(["3M Co"])))
    result = check_text(session, text, "slot_text")
    assert result.ok, [(p.code, p.match) for p in result.problems]


def test_foreign_digits_holds_exactly_the_norm_indices_of_digits_from_non_ascii_sources() -> None:
    from scout_research.tools.numbercheck import normalize

    assert normalize("a ٣. b").foreign_digits == frozenset({2})
    assert normalize("a 3. b").foreign_digits == frozenset()
    assert normalize("3\u0301").foreign_digits == frozenset()  # ASCII-Ziffer mit Anhang
    assert normalize("1꣣").foreign_digits == frozenset({1})  # die Mn-Ziffer, nicht die ASCII-Ziffer davor
    assert normalize("１７").foreign_digits == frozenset({0, 1})
    assert normalize("⼆").norm == "9" and normalize("⼆").foreign_digits == frozenset({0})


def test_invariant_every_character_that_folds_to_an_ascii_digit_is_a_finding_even_as_a_list_number() -> None:
    import sys
    import unicodedata

    from scout_research.tools.numbercheck import _MARK_DIGITS, _PICTOGRAPH_NUMBERS, normalize

    session = _names_session("Adobe Inc.")
    chars = {chr(cp) for cp in range(0x80, sys.maxunicode + 1)
             if unicodedata.category(chr(cp)) not in ("Cn", "Co", "Cs") and any("0" <= c <= "9" for c in normalize(chr(cp)).norm)}
    chars |= {chr(cp) for cp in (*_MARK_DIGITS, *_PICTOGRAPH_NUMBERS)}
    assert len(chars) > 500
    missed = [f"U+{ord(c):04X}" for c in sorted(chars) if check_text(session, c + ". Segmente von ADBE", "slot_text").ok]
    assert not missed, missed[:20]


@pytest.mark.parametrize("text", [
    "ADBE ist günstiger als der Median", "keine Peers", "ein einziger Peer", "Die Quelle, z. B. das 10-K, nennt es.",
    "U.S.A. und e.g. sind Abkürzungen", "Das P/E ist hoch, siehe oben", "a b c bleibt harmlos",
])
def test_ordinary_text_without_numbers_passes(text: str) -> None:
    assert check_text(_names_session("Adobe Inc."), text, "slot_text").ok, text


# --- TA-7: extrahierte Zahlen für den Trace (Plan 6, Zeile „Backstop“) -----------------------------------------------


def _spans(entries: list[dict], status: str) -> list[tuple[int, int]]:
    return [(e["start"], e["end"]) for e in entries if e["status"] == status]


def test_extracted_numbers_carry_both_statuses_with_raw_positions_even_behind_invisible_format_chars() -> None:
    ctx, tid, _ = _table()
    session = build_check_session(ctx)
    text = "\u200bSiehe \u200cW1 laut 10-K: \u200b25 Prozent und 2\u200b6."  # unsichtbare Formatzeichen (Cf) vor und in den Funden
    result = check_text(session, text)
    assert {e["status"] for e in result.extracted} == {"exempt", "violation"}
    assert all(set(e) == {"start", "end", "kind", "match", "status", "exempt_span"} for e in result.extracted)
    assert all(text[e["start"]: e["end"]] == e["match"] for e in result.extracted)
    exempt = {e["match"] for e in result.extracted if e["status"] == "exempt"}
    assert "W1" in exempt and any(m.startswith("10") for m in exempt)
    # M2: die ganze Ausnahmespanne steht im Eintrag, hinter dem Formatzeichen wie davor; Verstöße tragen keine
    spans = {e["match"]: e["exempt_span"] for e in result.extracted if e["status"] == "exempt"}
    assert spans == {"W1": "W1", "10": "10-K"}
    assert all(e["exempt_span"] is None for e in result.extracted if e["status"] == "violation")
    violations = [e for e in result.extracted if e["status"] == "violation"]
    assert [(e["start"], e["end"]) for e in violations] == [(p.start, p.end) for p in result.problems if p.code == "NAKED_NUMBER"]
    assert len(violations) == 2 and "2\u200b6" in {e["match"] for e in violations}  # der Fund über das Formatzeichen hinweg bleibt einer
    assert result.extracted == sorted(result.extracted, key=lambda e: (e["start"], e["end"]))


def test_extracted_numbers_are_also_filled_when_the_text_is_accepted_and_a_warning_id_in_a_valid_slot_does_not_count() -> None:
    ctx, tid, _ = _table()
    session = build_check_session(ctx)
    result = check_text(session, "Laut 10-K und Form 10-K ([[warn:W1]]) gilt W1 für NVDA [[co:NVDA:ev_ebitda]].")
    assert result.ok and result.body is not None
    assert result.extracted and {e["status"] for e in result.extracted} == {"exempt"}
    assert [e["match"] for e in result.extracted].count("W1") == 1  # das W1 im gültigen Slot zählt nicht
    assert [e["match"] for e in result.extracted].count("10") == 2  # zweimal „10-K“, je Fundstelle ein Eintrag


def test_overlapping_exempt_spans_yield_one_entry_per_finding_with_the_longest_span() -> None:
    from scout_research.tools.numbercheck import exempt_spans, normalize

    session = _names_session("3M Co")  # Begriffe „3M Co“ und „3M“: beide Spannen decken die erste „3M“ ab
    text = "3M Co und 3M"
    n = normalize(text)
    spans = exempt_spans(n.norm, session.warning_ids, session.identifiers, session.terms)
    assert sorted(spans) == [(0, 2), (0, 5), (10, 12)]  # der Beleg: die Spannen überlappen wirklich
    result = check_text(session, text)
    assert result.ok
    assert result.extracted == [
        {"start": 0, "end": 2, "kind": "digits", "match": "3M", "status": "exempt", "exempt_span": "3M Co"},
        {"start": 10, "end": 12, "kind": "digits", "match": "3M", "status": "exempt", "exempt_span": "3M"}]


def test_a_company_name_with_a_number_word_is_exempt_as_a_number_word_with_the_whole_name_as_span() -> None:
    session = _names_session("Five Below, Inc.", "FIVE")
    result = check_text(session, "Ähnlich wie Five Below.")
    assert result.ok and result.extracted == [
        {"start": 12, "end": 16, "kind": "number_word", "match": "Five", "status": "exempt", "exempt_span": "Five Below"}]


def test_fixed_raw_positions_of_exempt_entries_behind_format_chars_and_multi_character_folding() -> None:
    ctx, tid, _ = _table()
    session = build_check_session(ctx)
    # Index: ﬃ=0 ' '=1 ZWSP=2 W=3 1=4 ' '=5 u=6 n=7 d=8 ' '=9 ㎏=10 ' '=11 SHY=12 1=13 0=14 U+2011=15 K=16 ' '=17 1=18 ZWSP=19 0=20 -=21 K=22
    # „ﬃ“ wird zu „ffi“, „㎏“ zu „kg“ (je ein Zeichen mehr im normalisierten Text); die Rohtext-Positionen bleiben die
    # des Originals.
    text = "ﬃ ​W1 und ㎏ ­10‑K 1​0-K"
    result = check_text(session, text)
    assert result.ok
    assert [(e["start"], e["end"], e["match"], e["exempt_span"]) for e in result.extracted] == [
        (3, 5, "W1", "W1"), (13, 15, "10", "10‑K"), (18, 21, "1​0", "1​0-K")]


def test_slot_cases_in_extracted_a_numeric_field_name_is_a_violation_but_unknown_ids_and_embedded_digits_are_not() -> None:
    ctx, tid, _ = _table()
    session = build_check_session(ctx)
    numeric = check_text(session, "[[co:NVDA:25]] x")
    assert [(e["status"], e["match"], e["exempt_span"]) for e in numeric.extracted] == [("violation", "25", None)]
    assert check_text(session, "[[warn:W9]] x").extracted == []
    assert check_text(session, "[[co:NVDA:ev25]] x").extracted == []


def test_text_without_a_number_and_text_of_valid_slots_only_extract_nothing() -> None:
    ctx, tid, _ = _table()
    session = build_check_session(ctx)
    plain = check_text(session, "NVDA wird am oberen Ende der Peers bewertet.")
    assert plain.ok and plain.extracted == []
    slots_only = check_text(session, "NVDA: [[co:NVDA:ev_ebitda]] gegen [[stat:ev_ebitda:median]].")
    assert slots_only.ok and slots_only.slots and slots_only.extracted == []
    rejected = check_text(session, "Unbekannt: [[co:NVDA:gibts_nicht]] und [[nope]].")
    assert not rejected.ok and rejected.extracted == []  # Slot-Probleme sind keine Zahlen
    assert check_text(session, "").extracted == []


def test_the_rationale_profile_fills_extracted_like_any_other() -> None:
    ctx, tid, _ = _table()
    result = check_text(build_check_session(ctx), "Laut 10-K 25 Prozent", "rationale")
    assert not result.ok and {e["status"] for e in result.extracted} == {"exempt", "violation"}


def test_the_violation_entries_are_exactly_the_naked_number_problems_over_the_whole_corpus() -> None:
    from tests.unit.number_check_corpus import build_session, load_corpus

    corpus = load_corpus()
    sessions: dict[str, object] = {}
    accepted = rejected = exempt_seen = 0
    for case in corpus["cases"]:
        session = sessions.setdefault(case["session"], build_session(corpus, case["session"]))
        result = check_text(session, case["text"], case["profile"])
        naked = sorted((p.start, p.end, p.kind) for p in result.problems if p.code == "NAKED_NUMBER")
        violations = sorted((e["start"], e["end"], e["kind"]) for e in result.extracted if e["status"] == "violation")
        assert violations == naked, case["id"]
        assert all((e["exempt_span"] is None) == (e["status"] == "violation") for e in result.extracted), case["id"]
        assert all(case["text"][e["start"]: e["end"]] == e["match"] for e in result.extracted), case["id"]
        assert all(e["status"] in ("violation", "exempt") for e in result.extracted), case["id"]
        keys = [(e["start"], e["end"]) for e in result.extracted if e["status"] == "exempt"]
        assert len(keys) == len(set(keys)), case["id"]
        accepted += result.ok
        rejected += not result.ok
        exempt_seen += bool(keys)
    assert accepted and rejected and exempt_seen  # beide Rückgabewege und die Ausnahmen kommen im Korpus wirklich vor


# --- TA-20, Fable 02 Befund 1: Steuerzeichen (Kategorie Cc) im gezeigten Text (O-33) --------------------------------

import unicodedata  # noqa: E402

_CONTROL_CHARS = [chr(c) for c in (*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)) if chr(c) not in "\n\t"]
_KEPT_WHITESPACE = "\n\t"


def _has_bad_control(text: str) -> bool:
    return any(unicodedata.category(ch) == "Cc" and ch not in _KEPT_WHITESPACE for ch in text)


def _slot_session():
    ctx, _, _ = _table()
    return build_check_session(ctx)


def test_the_control_character_list_covers_c0_del_and_c1_without_tab_and_newline() -> None:
    assert len(_CONTROL_CHARS) == 32 - 2 + 1 + 32 and "\r" in _CONTROL_CHARS and "\x1b" in _CONTROL_CHARS


@pytest.mark.parametrize("ch", _CONTROL_CHARS, ids=[f"U+{ord(c):04X}" for c in _CONTROL_CHARS])
def test_a_control_character_anywhere_is_accepted_and_never_reaches_the_body(ch: str) -> None:
    session = _slot_session()
    texts = [
        "NVDA liegt beim EV/EBITDA bei [[co:NVDA:ev_ebitda]]." + ch + "Text",     # direkt hinter dem Slot
        "NVDA liegt beim EV/EBITDA bei [[co:NVDA:ev_ebitda]]." + ch * 3 + "   ",  # mehrfach, mit Leerzeichen
        "NVDA liegt beim EV/EBITDA bei [[co:NVDA:ev_ebitda]], im Ver" + ch + "gleich teuer.",  # mitten im Wort
        ch + "NVDA liegt beim EV/EBITDA bei [[co:NVDA:ev_ebitda]].",              # am Satzanfang
        "NVDA liegt beim EV/EBITDA bei [" + ch + "[co:NVDA:ev_ebitda]].",         # im Slot-Beginn
    ]
    for text in texts:
        result = check_text(session, text, "slot_text")
        assert result.ok, (text, [(p.code, p.kind) for p in result.problems])
        assert result.body is not None and not _has_bad_control(result.body), (text, result.body)
        assert result.format_chars == text.count(ch)


def test_newline_and_tab_stay_in_the_body() -> None:
    result = check_text(_slot_session(), "NVDA: [[co:NVDA:ev_ebitda]].\n\nWeiterer\tAbsatz.\n- Punkt", "slot_text")
    assert result.ok and result.body is not None
    assert "\n\nWeiterer\tAbsatz.\n- Punkt" in result.body and result.format_chars == 0


@pytest.mark.parametrize(("text", "found"), [
    ("Es gibt zw\x00ei Peers.", "zw\x00ei"),
    ("Es gibt sech\rzig Peers.", "sech\rzig"),
    ("Es gibt fünf\x1bzig Peers.", "fünf\x1bzig"),
    ("Es gibt 1\b5 Peers.", "1\b5"),
    ("Es gibt dr\x9bei Peers.", "dr\x9bei"),
])
def test_numbers_torn_by_a_control_character_are_found_at_the_raw_position(text: str, found: str) -> None:
    for profile in ("slot_text", "rationale"):
        result = check_text(_names_session("Adobe Inc."), text, profile)
        assert not result.ok and {p.code for p in result.problems} == {"NAKED_NUMBER"}, (text, profile)
        a = text.index(found)
        assert any(p.start < a + len(found) and a < p.end for p in result.problems), (text, profile, result.problems)


def test_format_chars_counts_the_removed_control_characters() -> None:
    from scout_research.tools.numbercheck import normalize

    n = normalize("a\rb\x1b[Kc\x00\x7f\x85d\ne\tf​")
    assert n.clean == "ab[Kcd\ne\tf" and n.format_chars == 6  # \r \x1b \x00 \x7f \x85 + U+200B
    assert n.norm == n.clean
