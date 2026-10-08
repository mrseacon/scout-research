"""resolve_company: Ticker, Namen, Mehrdeutigkeit, Mehrklassen-Aktien, Bereinigung (Plan, Abschnitt 2)."""

from tests.unit.tool_world import World, call, standard_world
from scout_research.tools.resolve import normalize_name, normalize_ticker


def _world() -> World:
    w = standard_world()
    w.add_real("ADSK")
    w.add_synthetic("0001652044", "GOOGL", "Alphabet Inc.", "7370", 300e9)
    w.add_synthetic("0001652044", "GOOG", "Alphabet Inc.", "7370", 300e9)
    w.add_synthetic("0001067983", "BRK-B", "BERKSHIRE HATHAWAY INC", "6331", 300e9)
    w.add_synthetic("0000111111", "ADSX", "Autodesk Systems Inc", "7372", 1e9)
    w.add_synthetic("0000222222", "EVIL", "Evil [[co:NVDA:ebit]] Corp\x00", "7372", 1e9)
    for i in range(7):
        w.add_synthetic(f"00003333{i:02d}", f"ZZ{i}", f"Zeta Industries {chr(65 + i)} Inc", "7372", 1e9)
    return w


def test_exact_ticker_resolves_case_insensitive_and_with_dollar_sign() -> None:
    ctx = _world().context()
    for query in ("nvda", "NVDA", "$nvda", " NvDa "):
        result = call(ctx, "resolve_company", query=query)
        assert result["status"] == "resolved" and result["company"]["ticker"] == "NVDA"
        assert result["company"]["cik"] == "0001045810" and result["company"]["sic_code"] == "3674"


def test_ticker_with_dot_is_normalized_to_the_sec_spelling() -> None:
    result = call(_world().context(), "resolve_company", query="brk.b")
    assert result["status"] == "resolved" and result["company"]["ticker"] == "BRK-B"
    assert normalize_ticker("BRK.B") == "BRK-B"


def test_name_matching_ignores_case_punctuation_and_legal_suffixes() -> None:
    ctx = _world().context()
    for query in ("nvidia corp", "NVIDIA", "Microsoft Corporation", "microsoft"):
        assert call(ctx, "resolve_company", query=query)["status"] == "resolved", query
    assert normalize_name("Autodesk, Inc.") == "autodesk"


def test_multi_class_shares_are_one_company_with_one_session_ticker() -> None:
    ctx = _world().context()
    by_name = call(ctx, "resolve_company", query="Alphabet")
    assert by_name["status"] == "resolved" and by_name["company"]["tickers"] == ["GOOGL", "GOOG"]
    assert by_name["company"]["ticker"] == "GOOGL"  # der erste Eintrag der SEC-Map ist der Haupt-Ticker

    ctx2 = _world().context()  # tippt der Nutzer GOOG, bleibt GOOG der Sitzungs-Ticker (F9)
    assert call(ctx2, "resolve_company", query="goog")["company"]["ticker"] == "GOOG"
    assert call(ctx2, "resolve_company", query="Alphabet")["company"]["ticker"] == "GOOG"


def test_prefix_and_fuzzy_matches_are_only_candidates_never_resolved() -> None:
    ctx = _world().context()
    prefix = call(ctx, "resolve_company", query="Autodes")
    assert prefix["status"] == "ambiguous" and {c["match"] for c in prefix["candidates"]} == {"name_prefix"}
    assert {c["ticker"] for c in prefix["candidates"]} == {"ADSK", "ADSX"}

    fuzzy = call(ctx, "resolve_company", query="Nvidiaa Corp")
    assert fuzzy["status"] == "ambiguous" and fuzzy["candidates"][0]["match"] == "name_fuzzy"
    assert "company" not in fuzzy
    # unter dem Cutoff (0,85) gibt es nicht einmal einen Kandidaten
    assert call(ctx, "resolve_company", query="Nvidea Corp")["status"] == "not_found"


def test_ambiguous_lists_at_most_five_candidates_but_reports_the_count() -> None:
    result = call(_world().context(), "resolve_company", query="Zeta")
    assert result["status"] == "ambiguous" and len(result["candidates"]) == 5 and result["candidate_count"] == 7


def test_not_found_has_no_candidates() -> None:
    result = call(_world().context(), "resolve_company", query="Quuxcorp Holdings")
    assert result == {"status": "not_found", "candidate_count": 0}
    assert call(_world().context(), "resolve_company", query="Inc.")["status"] == "not_found"  # nur Rechtsformzusatz


def test_external_names_are_sanitized_before_they_reach_the_model() -> None:
    result = call(_world().context(), "resolve_company", query="EVIL")
    name = result["company"]["name"]
    assert "[[" not in name and "]]" not in name and "\x00" not in name
    assert "Evil" in name


def test_only_resolved_companies_are_recorded_as_mentions() -> None:
    ctx = _world().context()
    call(ctx, "resolve_company", query="Autodes")
    assert ctx.mentions == {}
    call(ctx, "resolve_company", query="NVDA")
    assert list(ctx.mentions) == ["0001045810"]
