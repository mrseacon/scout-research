"""submit_commentary mit den echten Handlern (Plan, Abschnitt 11; Schritt 6, T2): Rendering, Korrekturrunden und
Rückhalt, Pflichtwarnungen (O4), Basiszeile (O5), englische Vorlagen, fin:/mkt:-Slots (O1 B), Prüfung vor der Tabelle.
Das Prüfkorpus selbst läuft in `test_number_check_corpus.py`."""

import pytest

from scout_research.tools.commentary import build_check_session, check_text
from scout_research.tools.handlers import dispatch
from tests.unit.tool_world import AAPL_CIK, MSFT_CIK, NVDA_CIK, call, confirmed, propose, standard_world, start


def _table(w=None, **context_kwargs):
    w = w or standard_world()
    if context_kwargs:
        ctx = w.context(**context_kwargs)
        ctx.record_user_message("Mach Comps für NVDA")
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
    ctx.record_user_message("Versuch es bitte noch einmal")
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

    ctx.record_user_message("nochmal")
    covered = _submit(ctx, tid, "AAPL fehlt in der Tabelle ([[warn:W1]]).")
    assert covered["warnings_not_addressed"] == [] or "W1" not in covered["warnings_not_addressed"]
    assert "AAPL fehlt in der Tabelle ([W1: AAPL])." in ctx.commentaries[tid].rendered

    ctx.record_user_message("und AAPL?")
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
    ctx.record_user_message("weiter")
    assert _code(_submit(ctx, tid, f"[[fin:{fid}:total_assets]]")) == "SLOT_UNKNOWN"  # nicht abgefragt
    assert _code(_submit(ctx, tid, "[[fin:fin_zzzz:revenue]]")) == "SLOT_UNKNOWN"
    ctx.record_user_message("und weiter")  # die dritte Abweisung in einer Runde würde den Kommentar zurückhalten
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


@pytest.mark.parametrize("text", [
    "ADBE ist günstiger als der Median", "keine Peers", "ein einziger Peer", "Die Quelle, z. B. das 10-K, nennt es.",
    "U.S.A. und e.g. sind Abkürzungen", "Das P/E ist hoch, siehe oben", "a b c bleibt harmlos",
])
def test_ordinary_text_without_numbers_passes(text: str) -> None:
    assert check_text(_names_session("Adobe Inc."), text, "slot_text").ok, text
