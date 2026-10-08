"""propose_peer_set als Gate, Host-API confirm_peer_set, Handles — T3 und T4 (Plan, Abschnitt 2 und 5)."""

import pytest

from scout_research.tools.handlers import dispatch, tool_definitions
from scout_research.tools.session import ConfirmationError
from tests.unit.tool_world import AAPL_CIK, MSFT_CIK, NVDA_CIK, call, confirmed, propose, standard_world, start


def _code(result: dict) -> str:
    return result["error"]["code"]


def _problems(result: dict) -> list:
    return result["error"]["details"]["problems"]


# --- Vorschlag ---------------------------------------------------------------------------------------------------


def test_a_valid_proposal_awaits_human_confirmation_and_labels_the_rationale_source() -> None:
    ctx, set_id = start(standard_world())
    result = propose(ctx, set_id)

    assert result["status"] == "awaiting_human_confirmation" and result["proposal_id"].startswith("pp_")
    assert result["rationale_source"] == "model_assessment_unverified"
    assert [p["ticker"] for p in result["peers"]] == ["AAPL", "MSFT"] and result["user_additions"] == []
    assert ctx.awaiting_confirmation and ctx.gate_log[-1]["event"] == "proposed"


def test_peer_outside_the_candidate_list_is_rejected_even_with_a_real_cik() -> None:
    w = standard_world()
    ctx, set_id = start(w)
    result = propose(ctx, set_id, (AAPL_CIK, "0000789020"))
    assert _code(result) == "PEER_NOT_IN_CANDIDATES"
    assert _problems(result) == [{"field": "peers[1].cik", "cik": "0000789020"}]
    assert not ctx.awaiting_confirmation  # abgewiesen: kein Gate-Zustand


def test_the_target_itself_is_not_a_candidate() -> None:
    ctx, set_id = start(standard_world())
    assert _code(propose(ctx, set_id, (NVDA_CIK,))) == "PEER_NOT_IN_CANDIDATES"


def test_duplicate_peers_are_invalid() -> None:
    ctx, set_id = start(standard_world())
    result = propose(ctx, set_id, (AAPL_CIK, AAPL_CIK))
    assert _code(result) == "INVALID_ARGUMENTS" and _problems(result)[0]["reason"] == "duplicate"


def test_cik_is_padded_so_short_ciks_match() -> None:
    ctx, set_id = start(standard_world())
    assert propose(ctx, set_id, ("320193",))["status"] == "awaiting_human_confirmation"


@pytest.mark.parametrize("rationale", ["Umsatz von 5 Mrd", "ähnlich wie 3 Firmen", "zwei ist keine Ziffer, aber 1 ist eine"])
def test_digits_in_a_rationale_are_rejected_as_numbers(rationale: str) -> None:
    ctx, set_id = start(standard_world())
    result = call(ctx, "propose_peer_set", candidate_set_id=set_id, peers=[{"cik": AAPL_CIK, "rationale": rationale}])
    assert _code(result) == "NAKED_NUMBER" and _problems(result)[0]["field"] == "peers[0].rationale"


def test_all_number_problems_are_reported_in_one_answer() -> None:
    ctx, set_id = start(standard_world())
    result = call(ctx, "propose_peer_set", candidate_set_id=set_id, peers=[
        {"cik": AAPL_CIK, "rationale": "Rund 40 Prozent"}, {"cik": MSFT_CIK, "rationale": "Nur 2 Segmente"}],
        notable_exclusions=[{"cik": MSFT_CIK, "reason": "Zu groß, 10x"}])
    assert [p["field"] for p in _problems(result)] == ["peers[0].rationale", "peers[1].rationale", "notable_exclusions[0].reason"]


def test_slot_markers_in_a_rationale_are_rejected() -> None:
    ctx, set_id = start(standard_world())
    result = call(ctx, "propose_peer_set", candidate_set_id=set_id, peers=[{"cik": AAPL_CIK, "rationale": "siehe [[co:AAPL:ebit]]"}])
    assert _code(result) == "INVALID_ARGUMENTS" and _problems(result)[0]["reason"] == "slot_markers_not_allowed"


def test_company_names_with_digits_are_exempt_from_the_number_check() -> None:
    w = standard_world()
    w.add_synthetic("0000999001", "MMM", "3M Co", "3674", 200e9)
    ctx, set_id = start(w)
    result = call(ctx, "propose_peer_set", candidate_set_id=set_id,
                  peers=[{"cik": "0000999001", "rationale": "Wie 3M, breit aufgestellt"}])
    assert result["status"] == "awaiting_human_confirmation"
    ctx.record_user_message("nochmal")  # die Gate-Sperre des ersten Vorschlags aufheben
    assert _code(call(ctx, "propose_peer_set", candidate_set_id=set_id,
                      peers=[{"cik": AAPL_CIK, "rationale": "Wie 3M und 5 weitere"}])) == "NAKED_NUMBER"


def test_exclusions_must_come_from_the_candidate_list() -> None:
    ctx, set_id = start(standard_world())
    result = propose(ctx, set_id, notable_exclusions=[{"cik": "0000789020", "reason": "zu groß"}])
    assert _code(result) == "PEER_NOT_IN_CANDIDATES" and _problems(result)[0]["field"] == "notable_exclusions[0].cik"


# --- Nutzerwünsche außerhalb der Liste ----------------------------------------------------------------------------


def _addition_world():
    w = standard_world()
    w.add_real("ADSK")  # SIC 7372: kein Kandidat des Ziels (SIC 3674)
    return w


def test_user_requested_addition_needs_a_literal_mention_in_a_user_message() -> None:
    ctx, set_id = start(_addition_world())
    result = propose(ctx, set_id, user_requested_additions=[{"query": "ADSK"}])
    assert _code(result) == "USER_ADDITION_NOT_IN_MESSAGES"
    assert _problems(result)[0] == {"field": "user_requested_additions[0].query", "query": "ADSK"}
    assert not ctx.awaiting_confirmation


def test_user_requested_addition_with_a_literal_mention_is_accepted() -> None:
    ctx, set_id = start(_addition_world())
    ctx.record_user_message("Nimm bitte auch Autodesk dazu")
    result = propose(ctx, set_id, user_requested_additions=[{"query": "Autodesk"}])
    assert result["status"] == "awaiting_human_confirmation"
    assert result["user_additions"][0]["ticker"] == "ADSK" and result["user_additions"][0]["query"] == "Autodesk"


def test_a_lowercase_ticker_the_user_wrote_in_lowercase_does_not_count() -> None:
    ctx, set_id = start(_addition_world())
    ctx.record_user_message("nimm adsk dazu")
    assert _code(propose(ctx, set_id, user_requested_additions=[{"query": "adsk"}])) == "USER_ADDITION_NOT_IN_MESSAGES"
    ctx.record_user_message("nimm $adsk dazu")
    assert propose(ctx, set_id, user_requested_additions=[{"query": "adsk"}])["status"] == "awaiting_human_confirmation"


def test_ambiguous_or_unknown_additions_are_invalid_not_guessed() -> None:
    w = _addition_world()
    w.add_synthetic("0000111111", "ADSX", "Autodesk Systems Inc", "7372", 1e9)
    ctx, set_id = start(w)
    ctx.record_user_message("Autodes und Quuxcorp")
    for query in ("Autodes", "Quuxcorp"):
        result = propose(ctx, set_id, user_requested_additions=[{"query": query}])
        assert _code(result) == "INVALID_ARGUMENTS" and _problems(result)[0]["reason"] in ("ambiguous", "not_found")


def test_addition_cannot_be_the_target_or_a_duplicate() -> None:
    ctx, set_id = start(standard_world())
    ctx.record_user_message("NVDA und AAPL")
    assert _code(propose(ctx, set_id, user_requested_additions=[{"query": "NVDA"}])) == "INVALID_ARGUMENTS"
    assert _code(propose(ctx, set_id, user_requested_additions=[{"query": "AAPL"}])) == "INVALID_ARGUMENTS"


# --- Gate-Zustand ------------------------------------------------------------------------------------------------


def test_every_tool_call_after_a_proposal_is_blocked_until_confirmation_or_a_new_user_message() -> None:
    ctx, set_id = start(standard_world())
    proposal = propose(ctx, set_id)
    for name, args in (("resolve_company", {"query": "NVDA"}), ("get_market_data", {"ticker": "NVDA"}),
                       ("compute_comps_table", {"peer_set_id": "ps_x"}), ("find_peer_candidates", {"target_cik": NVDA_CIK})):
        outcome = dispatch(ctx, name, args)
        assert outcome.is_error and outcome.content["error"]["code"] == "AWAITING_PEER_CONFIRMATION", name
    ctx.confirm_peer_set(proposal["proposal_id"], [AAPL_CIK])
    assert call(ctx, "resolve_company", query="NVDA")["status"] == "resolved"


def test_a_chat_message_lifts_the_block_but_never_confirms() -> None:
    ctx, set_id = start(standard_world())
    proposal = propose(ctx, set_id)
    ctx.record_user_message("ja, passt")  # eine Chatnachricht ist nie eine Bestätigung
    assert not ctx.awaiting_confirmation and ctx.peer_sets == {}
    assert _code(call(ctx, "compute_comps_table", peer_set_id=proposal["proposal_id"])) == "PEER_SET_NOT_CONFIRMED"


def test_compute_with_a_proposal_id_instead_of_a_peer_set_id_is_rejected() -> None:
    ctx, set_id = start(standard_world())
    proposal = propose(ctx, set_id)
    ctx.confirm_peer_set(proposal["proposal_id"], [AAPL_CIK])
    assert _code(call(ctx, "compute_comps_table", peer_set_id=proposal["proposal_id"])) == "PEER_SET_NOT_CONFIRMED"


def test_compute_without_any_confirmation_is_rejected() -> None:
    ctx, set_id = start(standard_world())
    propose(ctx, set_id)
    ctx.record_user_message("weiter")
    assert _code(call(ctx, "compute_comps_table", peer_set_id="ps_0099")) == "UNKNOWN_HANDLE"


def test_handles_of_the_wrong_type_say_which_type_is_expected() -> None:
    ctx, set_id = start(standard_world())
    result = call(ctx, "propose_peer_set", candidate_set_id="ps_0001", peers=[{"cik": AAPL_CIK, "rationale": "x"}])
    assert _code(result) == "UNKNOWN_HANDLE" and "cs_" in result["error"]["hint"] and "anderen Typ" in result["error"]["hint"]
    assert set_id.startswith("cs_")


def test_a_new_proposal_invalidates_the_old_handle() -> None:
    ctx, set_id = start(standard_world())
    first = propose(ctx, set_id)
    ctx.record_user_message("ändere bitte")
    second = propose(ctx, set_id, (AAPL_CIK,))
    with pytest.raises(ConfirmationError) as exc:
        ctx.confirm_peer_set(first["proposal_id"], [AAPL_CIK])
    assert exc.value.reason == "proposal_superseded"
    assert ctx.confirm_peer_set(second["proposal_id"], [AAPL_CIK]).peers[0].ticker == "AAPL"


def test_a_proposal_can_be_confirmed_only_once() -> None:
    ctx, set_id = start(standard_world())
    proposal = propose(ctx, set_id)
    ctx.confirm_peer_set(proposal["proposal_id"], [AAPL_CIK])
    with pytest.raises(ConfirmationError) as exc:
        ctx.confirm_peer_set(proposal["proposal_id"], [AAPL_CIK])
    assert exc.value.reason == "already_confirmed"


# --- Host-API confirm_peer_set -----------------------------------------------------------------------------------


def test_confirm_records_channel_deviation_and_timestamp() -> None:
    ctx, set_id = start(standard_world())
    proposal = propose(ctx, set_id)
    peer_set = ctx.confirm_peer_set(proposal["proposal_id"], [AAPL_CIK], channel="ui", confirmed_by="sean")

    assert peer_set.id.startswith("ps_") and [p.ticker for p in peer_set.peers] == ["AAPL"]
    assert peer_set.removed == [MSFT_CIK] and peer_set.added == [] and peer_set.channel == "ui"
    event = ctx.gate_log[-1]
    assert event["event"] == "confirmed" and event["proposed"] == sorted([AAPL_CIK, MSFT_CIK])
    assert event["channel"] == "ui" and event["confirmed_by"] == "sean" and event["removed"] == [MSFT_CIK]


def test_the_human_can_add_companies_by_query_which_are_resolved_deterministically() -> None:
    w = standard_world()
    w.add_real("ADSK")
    ctx, set_id = start(w)
    proposal = propose(ctx, set_id, (AAPL_CIK,))
    peer_set = ctx.confirm_peer_set(proposal["proposal_id"], [AAPL_CIK], add_queries=["ADSK"])
    assert [p.ticker for p in peer_set.peers] == ["AAPL", "ADSK"] and peer_set.added == ["0000769397"]
    assert call(ctx, "get_market_data", ticker="ADSK")["ticker"] == "ADSK"  # bestätigter Peer → Allowlist (b)


def test_ambiguous_or_unknown_human_additions_are_returned_to_the_host() -> None:
    w = standard_world()
    w.add_real("ADSK")
    w.add_synthetic("0000111111", "ADSX", "Autodesk Systems Inc", "7372", 1e9)
    ctx, set_id = start(w)
    proposal = propose(ctx, set_id, (AAPL_CIK,))
    with pytest.raises(ConfirmationError) as exc:
        ctx.confirm_peer_set(proposal["proposal_id"], [AAPL_CIK], add_queries=["Autodes"])
    assert exc.value.reason == "ambiguous" and len(exc.value.details["candidates"]) == 2
    with pytest.raises(ConfirmationError) as exc:
        ctx.confirm_peer_set(proposal["proposal_id"], [AAPL_CIK], add_queries=["Quuxcorp"])
    assert exc.value.reason == "not_found"


@pytest.mark.parametrize(
    "keep,adds,reason",
    [
        ([], [], "empty_peer_set"),
        (["0000999999"], [], "keep_not_in_proposal"),
        ([AAPL_CIK, AAPL_CIK], [], "duplicate_peers"),
        ([AAPL_CIK], ["NVDA"], "target_cannot_be_peer"),
        ([AAPL_CIK], ["AAPL"], "duplicate_peers"),
    ],
)
def test_confirm_rejects_invalid_sets(keep: list[str], adds: list[str], reason: str) -> None:
    ctx, set_id = start(standard_world())
    proposal = propose(ctx, set_id)
    with pytest.raises(ConfirmationError) as exc:
        ctx.confirm_peer_set(proposal["proposal_id"], keep, add_queries=adds)
    assert exc.value.reason == reason


def test_eval_channel_is_rejected_without_an_eval_session_and_unknown_channels_too() -> None:
    ctx, set_id = start(standard_world())
    proposal = propose(ctx, set_id)
    with pytest.raises(ConfirmationError) as exc:
        ctx.confirm_peer_set(proposal["proposal_id"], [AAPL_CIK], channel="eval")
    assert exc.value.reason == "eval_channel_requires_eval_session"
    with pytest.raises(ConfirmationError) as exc:
        ctx.confirm_peer_set(proposal["proposal_id"], [AAPL_CIK], channel="model")
    assert exc.value.reason == "unknown_channel"
    assert ctx.peer_sets == {}


def test_confirm_peer_set_is_no_tool_and_cannot_be_called_by_the_model() -> None:
    names = {t["name"] for t in tool_definitions()}
    assert "confirm_peer_set" not in names
    ctx, set_id = start(standard_world())
    propose(ctx, set_id)
    ctx.record_user_message("weiter")
    outcome = dispatch(ctx, "confirm_peer_set", {"proposal_id": "pp_0003", "keep_ciks": [AAPL_CIK]})
    assert outcome.content["error"]["code"] == "UNKNOWN_TOOL" and ctx.peer_sets == {}
