"""find_peer_candidates: Komposition mit L2, Sortierung/Kürzung, Zähler, Fehler (Plan, Abschnitt 2, E3/E7/F12)."""

from tests.unit.tool_world import NVDA_CIK, call, resolved, standard_world, start


def _code(result: dict) -> str:
    return result["error"]["code"]


def _world_with_noise(n: int, **kwargs):
    w = standard_world()
    for i in range(n):
        ratio = 0.25 + i * 0.07  # 0,25 … über 4 — alle im Bereich 0,2–5
        w.add_synthetic(f"00009{i:05d}", f"SY{i:03d}", f"Synthetic {i} Inc", "3674", 215_938e6 * ratio, **kwargs)
    return w


def test_result_follows_the_contract() -> None:
    ctx, set_id = start(standard_world())
    result = call(ctx, "find_peer_candidates", target_cik="1045810")  # kurze CIK wird gepolstert
    assert result["candidate_set_id"].startswith("cs_") and result["candidate_set_id"] != set_id
    assert result["target"] == {
        "cik": NVDA_CIK, "ticker": "NVDA", "name": "NVIDIA CORP", "sic_code": "3674",
        "sic_description": "Semiconductors & Related Devices", "period_end": "2026-01-25", "fiscal_year": 2026,
        "revenue_musd": 215938,
    }
    assert result["calendar_year_used"] == 2025 and result["frame_check"] == "target_in_frame"
    assert result["size_range"] == {"min": 0.2, "max": 5.0} and result["warnings"] == []
    assert [c["ticker"] for c in result["candidates"]] == ["MSFT", "AAPL"]  # nähere Größe zuerst (1,54x vor 1,93x)
    assert set(result["candidates"][0]) == {"cik", "ticker", "name", "sic_code", "revenue_musd", "size_ratio"}
    assert result["candidates"][0]["size_ratio"] == 1.54


def test_counts_add_up_and_name_every_exclusion() -> None:
    w = standard_world()
    w.add_synthetic("0000900001", "BIG", "Big Inc", "3674", 2_000_000e6)  # 9,3x → außerhalb
    w.add_synthetic("0000900002", "NOFR", "No Frame Inc", "3674", 1.0, in_frame=False)
    w.add_synthetic("0000900003", "X", "No Ticker Inc", "3674", 1.0, with_ticker=False)
    ctx, _ = start(w)
    counts = call(ctx, "find_peer_candidates", target_cik=NVDA_CIK)["counts"]

    assert counts == {"sic_matches": 5, "without_ticker": 1, "not_in_revenue_frame": 1, "outside_size_range": 1,
                      "passed_filters": 2, "returned": 2, "truncated": 0, "sic_search_truncated": False}
    assert counts["sic_matches"] == (counts["without_ticker"] + counts["not_in_revenue_frame"]
                                     + counts["outside_size_range"] + counts["passed_filters"])


def test_candidates_are_sorted_by_log_distance_and_cut_to_forty() -> None:
    ctx, _ = start(_world_with_noise(55))
    result = call(ctx, "find_peer_candidates", target_cik=NVDA_CIK, size_range={"min": 0.2, "max": 5})
    candidates, counts = result["candidates"], result["counts"]

    assert (counts["passed_filters"], counts["returned"], counts["truncated"]) == (57, 40, 17)
    import math
    distances = [abs(math.log(c["size_ratio"])) for c in candidates]
    assert distances == sorted(distances)
    assert all(d <= 0.7 for d in distances[:20])  # die nächsten bleiben


def test_the_stored_candidate_set_holds_only_what_the_model_saw() -> None:
    """Abgeschnittene Kandidaten sind nicht vorschlagbar — sonst käme eine CIK aus dem Vorwissen des Modells durch."""
    ctx, _ = start(_world_with_noise(55))
    result = call(ctx, "find_peer_candidates", target_cik=NVDA_CIK)
    stored = ctx.candidate_sets[result["candidate_set_id"]]
    shown = {c["cik"] for c in result["candidates"]}
    assert {c["cik"] for c in stored.candidates} == shown and len(shown) == 40

    all_ciks = {f"00009{i:05d}" for i in range(55)}
    cut = sorted(all_ciks - shown)[0]
    proposal = call(ctx, "propose_peer_set", candidate_set_id=result["candidate_set_id"],
                    peers=[{"cik": cut, "rationale": "aus dem Vorwissen"}])
    assert _code(proposal) == "PEER_NOT_IN_CANDIDATES"


def test_size_range_is_used_and_validated() -> None:
    ctx, _ = start(standard_world())
    narrow = call(ctx, "find_peer_candidates", target_cik=NVDA_CIK, size_range={"min": 0.5, "max": 1.6})
    assert [c["ticker"] for c in narrow["candidates"]] == ["MSFT"] and narrow["size_range"] == {"min": 0.5, "max": 1.6}
    assert narrow["counts"]["outside_size_range"] == 1
    assert _code(call(ctx, "find_peer_candidates", target_cik=NVDA_CIK, size_range={"min": 0.01, "max": 5})) == "INVALID_ARGUMENTS"
    assert _code(call(ctx, "find_peer_candidates", target_cik=NVDA_CIK, size_range={"min": 0.2, "max": 50})) == "INVALID_ARGUMENTS"


def test_external_names_in_candidates_are_sanitized() -> None:
    w = standard_world()
    w.add_synthetic("0000900009", "EVIL", "Evil [[co:NVDA:ebit]]\x00 Corp", "3674", 200_000e6)
    ctx, _ = start(w)
    evil = next(c for c in call(ctx, "find_peer_candidates", target_cik=NVDA_CIK)["candidates"] if c["ticker"] == "EVIL")
    assert "[[" not in evil["name"] and "]]" not in evil["name"] and "\x00" not in evil["name"]


def test_candidates_with_an_invalid_ticker_are_not_shown_and_count_as_without_ticker() -> None:
    w = standard_world()
    w.add_synthetic("0000900010", "bad ticker!", "Odd Inc", "3674", 200_000e6)
    ctx, _ = start(w)
    result = call(ctx, "find_peer_candidates", target_cik=NVDA_CIK)
    assert "bad ticker!" not in str(result)
    assert result["counts"]["without_ticker"] == 1 and result["counts"]["passed_filters"] == 2


def test_sic_search_truncation_is_reported_as_counter_and_warning() -> None:
    w = standard_world()
    w.sic_members["3674"] += [f"00008{i:05d}" for i in range(1000)]
    ctx, _ = start(w)
    result = call(ctx, "find_peer_candidates", target_cik=NVDA_CIK)
    assert result["counts"]["sic_search_truncated"] is True
    assert [(x["id"], x["kind"], x["severity"]) for x in result["warnings"]] == [("P1", "sic_search_truncated", "warning")]
    assert not any(ch.isdigit() for ch in result["warnings"][0]["text"])


def test_historical_period_is_rejected_immediately() -> None:
    ctx = resolved(standard_world())
    result = call(ctx, "find_peer_candidates", target_cik=NVDA_CIK, period={"period_end": "2025-01-26"})
    assert _code(result) == "HISTORICAL_VALUATION_NOT_SUPPORTED" and result["error"]["details"]["tickers"] == ["NVDA"]
    assert ctx.candidate_sets == {}


def test_explicit_latest_period_is_fine_and_unknown_period_lists_the_available_ones() -> None:
    ctx, _ = start(standard_world())
    assert "candidate_set_id" in call(ctx, "find_peer_candidates", target_cik=NVDA_CIK, period={"period_end": "2026-01-25"})
    error = call(ctx, "find_peer_candidates", target_cik=NVDA_CIK, period={"period_end": "2023-06-30"})["error"]
    assert error["code"] == "PERIOD_NOT_AVAILABLE" and "NVDA" in error["message"]
    assert error["details"]["available_period_ends"][0] == "2026-01-25"


def test_period_with_both_fields_is_invalid() -> None:
    ctx, _ = start(standard_world())
    result = call(ctx, "find_peer_candidates", target_cik=NVDA_CIK, period={"period_end": "2026-01-25", "fiscal_year": 2026})
    assert _code(result) == "INVALID_ARGUMENTS"


def test_target_without_sic_code_or_with_non_positive_revenue_cannot_be_searched() -> None:
    w = standard_world()
    w.meta[NVDA_CIK]["sic"] = None
    ctx = resolved(w)
    error = call(ctx, "find_peer_candidates", target_cik=NVDA_CIK)["error"]
    assert error["code"] == "PEER_SEARCH_NOT_POSSIBLE" and error["details"] == {"reason": "no_sic_code"}

    w2 = standard_world()
    entries = w2.facts[NVDA_CIK]["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    for e in entries:
        e["val"] = -1.0
    ctx2 = resolved(w2)
    error = call(ctx2, "find_peer_candidates", target_cik=NVDA_CIK)["error"]
    assert error["code"] == "PEER_SEARCH_NOT_POSSIBLE" and error["details"] == {"reason": "non_positive_revenue"}


def test_target_missing_from_every_frame_year_is_frame_year_unresolved() -> None:
    w = standard_world()
    from scout_research.domain.metrics import REVENUE_CONCEPTS
    del w.frames[("Revenues", 2025)][NVDA_CIK]
    w.frames[("Revenues", 2025)]["0000999999"] = (1.0, "2026-01-25", "x")  # der Frame existiert, enthält das Ziel aber nicht
    ctx = resolved(w)
    error = call(ctx, "find_peer_candidates", target_cik=NVDA_CIK)["error"]
    assert error["code"] == "FRAME_YEAR_UNRESOLVED" and error["details"]["tried_years"] == [2025, 2024, 2026]
    assert REVENUE_CONCEPTS


def test_target_without_companyfacts_is_data_not_found() -> None:
    w = standard_world()
    del w.facts[NVDA_CIK]
    ctx = resolved(w)
    assert _code(call(ctx, "find_peer_candidates", target_cik=NVDA_CIK)) == "DATA_NOT_FOUND"
