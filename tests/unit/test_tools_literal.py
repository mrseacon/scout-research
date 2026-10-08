"""Wörtlichkeitsregel (E2 A) und Allowlist (E1 A, Q3) — T3 und T13."""

import pytest

from scout_research.tools.literal import query_is_literal
from tests.unit.tool_world import AAPL_CIK, MSFT_CIK, NVDA_CIK, call, confirmed, propose, standard_world, start


@pytest.mark.parametrize(
    "query,match,message,expected",
    [
        # Ticker bis 5 Zeichen: nur groß oder mit $
        ("INTU", "ticker_exact", "Wie steht INTU da?", True),
        ("intu", "ticker_exact", "Wie steht INTU da?", True),  # Die Anfrage des Modells ist egal, der Nutzer schrieb groß
        ("INTU", "ticker_exact", "wie steht intu da?", False),
        ("INTU", "ticker_exact", "wie steht $intu da?", True),
        ("ES", "ticker_exact", "gib mir es bitte", False),  # „es“ ist kein Ticker
        ("AN", "ticker_exact", "an dieser Stelle", False),
        ("ES", "ticker_exact", "Wie läuft ES?", True),
        ("B", "ticker_exact", "Zeig BRK-B", False),  # kein Treffer mitten in einem anderen Ticker
        ("BRK-B", "ticker_exact", "Zeig BRK-B bitte.", True),
        ("INTU", "ticker_exact", "XINTU und INTUIT", False),  # nur ganze Wörter
        ("INTU", "ticker_exact", "Ist INTU. Gut so", True),  # Satzende
        # Namen: Groß/Klein egal, ganze Wörter und Wortfolgen
        ("Adobe", "name_exact", "wie ist adobe aufgestellt?", True),
        ("Adobe", "name_exact", "Adobeware ist anders", False),
        ("Berkshire Hathaway", "name_exact", "berkshire   hathaway bitte", True),
        ("Berkshire Hathaway", "name_exact", "Hathaway Berkshire", False),
        ("Adobe Inc.", "name_exact", "wie ist adobe aufgestellt?", False),  # die Anfrage selbst muss dastehen
        # nur exakte Treffer zählen
        ("Adobe", "name_prefix", "Adobe", False),
        ("Adobe", "name_fuzzy", "Adobe", False),
        ("", "name_exact", "Adobe", False),
    ],
)
def test_literal_rule_matrix(query: str, match: str, message: str, expected: bool) -> None:
    assert query_is_literal(query, match, [message]) is expected


def test_any_user_message_counts_and_no_messages_never_do() -> None:
    assert query_is_literal("Adobe", "name_exact", ["hallo", "und Adobe?"]) is True
    assert query_is_literal("Adobe", "name_exact", []) is False


def test_a_company_the_model_resolved_alone_is_not_allowed() -> None:
    """T13: Der Name steht nur in einem Tool-Ergebnis (Kandidatenliste), nie in einer Nutzernachricht."""
    ctx, _ = start(standard_world())
    assert "Apple" in str(call(ctx, "find_peer_candidates", target_cik=NVDA_CIK, size_range={"min": 0.2, "max": 5}))
    assert call(ctx, "resolve_company", query="AAPL")["status"] == "resolved"  # das Modell löst selbst auf …

    for tool, args in (("get_financials", {"cik": AAPL_CIK}), ("get_market_data", {"ticker": "AAPL"})):
        error = call(ctx, tool, **args)["error"]
        assert error["code"] == "NOT_IN_ALLOWLIST", tool  # … und schaltet damit nichts frei


def test_host_and_system_messages_do_not_count_because_only_the_host_protocol_does() -> None:
    ctx, _ = start(standard_world())
    call(ctx, "resolve_company", query="AAPL")
    assert ctx.user_messages == ("Mach Comps für NVDA",)  # nichts anderes wurde protokolliert
    assert call(ctx, "get_financials", cik=AAPL_CIK)["error"]["code"] == "NOT_IN_ALLOWLIST"


def test_a_company_the_user_named_is_allowed_once_resolved() -> None:
    ctx, _ = start(standard_world())
    call(ctx, "resolve_company", query="AAPL")
    ctx.record_user_message("Zeig mir bitte auch AAPL")
    assert call(ctx, "get_financials", cik=AAPL_CIK, metrics=["revenue"])["values"]["revenue"]["value"] == 416161
    assert call(ctx, "get_market_data", ticker="AAPL")["ticker"] == "AAPL"


def test_user_naming_without_resolving_is_not_enough() -> None:
    ctx, _ = start(standard_world())
    ctx.record_user_message("Zeig mir bitte auch AAPL")
    assert call(ctx, "get_financials", cik=AAPL_CIK)["error"]["code"] == "NOT_IN_ALLOWLIST"


def test_the_target_is_allowed_because_it_is_user_named_and_resolved() -> None:
    ctx, _ = start(standard_world())
    assert call(ctx, "get_financials", cik=NVDA_CIK, metrics=["revenue"])["ticker"] == "NVDA"


def test_a_target_the_user_never_named_is_rejected_for_the_peer_search() -> None:
    """E1 A: Auch find_peer_candidates verlangt Allowlist (c) — ein Ziel schaltet nichts frei."""
    ctx = standard_world().context()
    ctx.record_user_message("Hallo")
    call(ctx, "resolve_company", query="NVDA")
    assert call(ctx, "find_peer_candidates", target_cik=NVDA_CIK)["error"]["code"] == "NOT_IN_ALLOWLIST"


def test_confirmed_peers_are_allowed_and_removed_candidates_are_not() -> None:
    w = standard_world()
    ctx, set_id = start(w)
    proposal = propose(ctx, set_id)  # AAPL und MSFT
    ctx.confirm_peer_set(proposal["proposal_id"], [AAPL_CIK])  # der Mensch streicht MSFT
    assert call(ctx, "get_financials", cik=AAPL_CIK, metrics=["revenue"])["ticker"] == "AAPL"
    assert call(ctx, "get_financials", cik=MSFT_CIK)["error"]["code"] == "NOT_IN_ALLOWLIST"
    assert call(ctx, "get_market_data", ticker="MSFT")["error"]["code"] == "NOT_IN_ALLOWLIST"


def test_unconfirmed_proposal_does_not_open_the_allowlist() -> None:
    ctx, set_id = start(standard_world())
    propose(ctx, set_id)
    ctx.record_user_message("weiter")  # die Sperre ist weg, bestätigt ist trotzdem nichts
    assert call(ctx, "get_financials", cik=AAPL_CIK)["error"]["code"] == "NOT_IN_ALLOWLIST"


def test_unknown_ticker_gives_the_same_answer_as_a_forbidden_one() -> None:
    ctx, _ = start(standard_world())
    assert call(ctx, "get_market_data", ticker="NOPE")["error"]["code"] == "NOT_IN_ALLOWLIST"
