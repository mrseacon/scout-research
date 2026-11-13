"""TA-20.2, Fable 04 Befund V5: Kandidaten einer mehrdeutigen Auflösung in der Sitzungssicht der Prüfung (Plan 11.2,
Klasse 4 Namen der Sitzung, Klasse 6 Kennungen mit Präfix). Die Kandidaten schalten nichts frei (Allowlist E1/E2)."""

from __future__ import annotations

import pytest

from eval.worlds import ambiguous_target
from scout_research.tools.commentary import build_check_session, check_text
from scout_research.tools.session import HumanInput
from tests.unit.loop_harness import Run, say, shown_texts, use
from tests.unit.tool_world import call

NOVA, NOVS = "0009990011", "0009990012"
QUESTION = ("Meinst du Nova Semiconductor Inc. (NOVA, CIK 0009990011) oder Nova Semiconductors Corp. "
            "(NOVS, CIK 0009990012)?")


def _digit_world():
    w = ambiguous_target()
    w.add_synthetic("0009990021", "ZTAD", "Zeta 3 Systems Inc.", "3674", revenue=9.0e10, year=2025)
    w.add_synthetic("0009990022", "ZTAS", "Zeta Systems Corp.", "3674", revenue=8.5e10, year=2025)
    w.market.prices.update({"ZTAD": 40.0, "ZTAS": 35.0})
    return w


def _ambiguous(world=None, message="Mach Comps für Nova", query="Nova Semi"):
    ctx = (world or ambiguous_target()).context()
    ctx.record_user_message(HumanInput(message))
    assert call(ctx, "resolve_company", query=query)["status"] == "ambiguous"
    return ctx


def _problems(ctx, text, profile="slot_text"):
    result = check_text(build_check_session(ctx), text, profile)
    return [(p.code, text[p.start:p.end]) for p in result.problems]


def test_the_follow_up_question_with_both_names_tickers_and_prefixed_ciks_is_accepted() -> None:
    ctx = _ambiguous()
    assert not ctx.tables
    assert _problems(ctx, QUESTION) == []


def test_a_candidate_name_with_a_digit_is_accepted_in_the_same_situation() -> None:
    ctx = _ambiguous(_digit_world(), "Mach Comps für Zeta", "Zeta Sys")
    text = "Meinst du Zeta 3 Systems Inc. (ZTAD, CIK 0009990021) oder Zeta Systems Corp. (ZTAS, CIK 0009990022)?"
    assert _problems(ctx, text) == []


@pytest.mark.parametrize("text", [
    "Meinst du CIK 0009990099?",                 # keine Kandidaten-CIK
    "Meinst du Nova Semiconductor Inc. (0009990011)?",  # Kandidaten-CIK ohne Präfix
    "Wir haben 2 Kandidaten.",                   # nackte Zahl
])
def test_counter_samples_stay_rejected(text: str) -> None:
    ctx = _ambiguous()
    assert any(code == "NAKED_NUMBER" for code, _ in _problems(ctx, text))


def test_the_candidate_ciks_are_unknown_without_the_ambiguous_resolution() -> None:
    ctx = ambiguous_target().context()
    assert any(code == "NAKED_NUMBER" for code, _ in _problems(ctx, QUESTION))


def test_candidates_of_an_ambiguous_resolution_unlock_nothing() -> None:
    ctx = _ambiguous()
    for cik in (NOVA, NOVS):
        assert ctx.allow_reason(cik) is None
        assert cik not in ctx.mentions and cik not in ctx.resolved_names
        for tool, args in (("get_financials", {"cik": cik}), ("find_peer_candidates", {"target_cik": cik})):
            assert call(ctx, tool, **args)["error"]["code"] == "NOT_IN_ALLOWLIST"


def test_lt_the_follow_up_question_after_ambiguous_is_shown_without_a_correction_round() -> None:
    run = Run(ambiguous_target(), [use(("resolve_company", {"query": "Nova Semi"})), say(QUESTION)])
    result = run.say("Mach Comps für Nova")
    assert shown_texts(result) == [QUESTION] and result.outcome == "done"
    assert len(run.client.requests) == 2
    run.assert_invariants()


def _group7_world():
    w = ambiguous_target()
    w.add_synthetic("0009990031", "GRPA", "Group 7 Corp.", "3674", revenue=9.0e10, year=2025)
    w.add_synthetic("0009990032", "GRPB", "Group 7 Holdings Corp.", "3674", revenue=8.5e10, year=2025)
    w.market.prices.update({"GRPA": 40.0, "GRPB": 35.0})
    return w


def test_a_candidate_name_exempts_only_the_whole_name_not_its_single_words() -> None:
    ctx = _ambiguous(_digit_world(), "Mach Comps für Zeta", "Zeta Sys")
    assert any(code == "NAKED_NUMBER" for code, _ in _problems(ctx, "Wir haben 3 Peers."))
    assert _problems(ctx, "Meinst du Zeta 3 Systems Inc.?") == []


def test_a_candidate_with_a_digit_word_does_not_free_that_digit_in_the_whole_text() -> None:
    ctx = _ambiguous(_group7_world(), "Mach Comps für Group", "Group 7")
    assert "0009990031" in ctx.ambiguous_candidates
    assert any(code == "NAKED_NUMBER" for code, _ in _problems(ctx, "Das P/E liegt bei 7."))
    assert _problems(ctx, "Meinst du Group 7 Corp.?") == []


def _duplicate_name_world():
    w = ambiguous_target()
    w.add_synthetic("0009990041", "DUPA", "Dupla Systems Inc.", "3674", revenue=9.0e10, year=2025)
    w.add_synthetic("0009990042", "DUPB", "Dupla Systems Inc.", "3674", revenue=8.5e10, year=2025)
    w.market.prices.update({"DUPA": 40.0, "DUPB": 35.0})
    return w


def _assert_nothing_unlocked(ctx) -> None:
    assert ctx.ambiguous_candidates
    for cik in ctx.ambiguous_candidates:
        assert ctx.allow_reason(cik) is None
        for tool, args in (("get_financials", {"cik": cik}), ("get_market_data", {"ticker": ctx.company(cik).tickers[0]}),
                           ("find_peer_candidates", {"target_cik": cik})):
            assert call(ctx, tool, **args)["error"]["code"] == "NOT_IN_ALLOWLIST", (tool, cik)


def test_candidates_stay_locked_when_the_user_message_contains_the_query_literally() -> None:
    _assert_nothing_unlocked(_ambiguous(message="Mach Comps für Nova Semi"))


def test_candidates_of_an_exact_name_duplicate_stay_locked_even_with_the_literal_query() -> None:
    ctx = _ambiguous(_duplicate_name_world(), "Mach Comps für Dupla Systems Inc.", "Dupla Systems Inc.")
    assert sorted(ctx.ambiguous_candidates) == ["0009990041", "0009990042"]
    _assert_nothing_unlocked(ctx)


def test_an_ambiguous_candidate_without_a_ticker_is_not_made_known_to_the_check() -> None:
    w = ambiguous_target()
    w.add_synthetic("0009990051", "ORBA", "Orbit Alpha Inc.", "3674", revenue=9.0e10, year=2025)
    w.add_synthetic("0009990052", "BAD$X", "Orbit Alpha Corp.", "3674", revenue=8.5e10, year=2025)  # Ticker ungültig: ticker_for → None
    ctx = _ambiguous(w, "Mach Comps für Orbit Alpha", "Orbit Alpha")
    assert list(ctx.ambiguous_candidates) == ["0009990051"]
    assert any(code == "NAKED_NUMBER" for code, _ in _problems(ctx, "Meinst du CIK 0009990052?"))
    assert _problems(ctx, "Meinst du CIK 0009990051?") == []


def _five9_world():
    w = ambiguous_target()
    w.add_synthetic("0009990061", "FIVN", "Five9 Corp.", "3674", revenue=9.0e10, year=2025)
    w.add_synthetic("0009990062", "FIVX", "Five9 Holdings Inc.", "3674", revenue=8.5e10, year=2025)
    w.market.prices.update({"FIVN": 40.0, "FIVX": 35.0})
    return w


def test_a_single_word_of_an_ambiguous_candidate_name_is_not_exempt() -> None:
    ctx = _ambiguous(_five9_world(), "Mach Comps für Five", "Five9")
    assert sorted(ctx.ambiguous_candidates) == ["0009990061", "0009990062"]
    assert not ctx.resolved_names and not ctx.candidate_sets and not ctx.tables
    assert any(code == "NAKED_NUMBER" for code, _ in _problems(ctx, "Five9 wächst."))
    assert _problems(ctx, "Meinst du Five9 Corp. oder Five9 Holdings Inc.?") == []
