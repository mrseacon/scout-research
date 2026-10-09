"""compute_comps_table: Handle-Semantik, kompakte Fassung ohne Zahlen in Texten, E5-Fehlerverhalten (Plan, Abschnitt 2)."""

import json

import httpx

from scout_research.tools.session import ToolContext
from tests.unit.tool_world import AAPL_CIK, MSFT_CIK, NVDA_CIK, World, call, confirmed, standard_world


def _code(result: dict) -> str:
    return result["error"]["code"]


def _compute(w: World, **kwargs) -> tuple[ToolContext, dict]:
    ctx, peer_set_id = confirmed(w)
    return ctx, call(ctx, "compute_comps_table", peer_set_id=peer_set_id, **kwargs)


def _has_digit(text: str) -> bool:
    return any(ch.isnumeric() for ch in text)


def test_result_follows_the_contract_and_rounds_for_the_model() -> None:
    ctx, result = _compute(standard_world())
    assert result["comps_table_id"].startswith("ct_")
    assert result["basis"] == {"target_period_end": "2026-01-25", "target_fiscal_year": 2026, "price_as_of": "2026-10-08",
                               "units": {"money": "Mio. USD", "multiple": "x", "pct": "%", "price": "USD je Aktie"},
                               "n_peers": 2, "n_peers_with_ev_multiples": 2}
    target = result["target"]
    assert target["ticker"] == "NVDA" and target["revenue"] == 215938 and isinstance(target["revenue"], int)
    assert target["ebit_margin"] == 60.4 and target["ev_ebitda"] == 32.8 and target["ebitda_approximated"] is True
    assert [p["ticker"] for p in result["peers"]] == ["AAPL", "MSFT"]
    assert {s["multiple"] for s in result["statistics"]} == {"ev_revenue", "ev_ebitda", "ev_ebit", "pe"}
    assert result["skipped_peers"] == []
    # die Zeilenfelder sind die Slot-Felder des Plans
    assert set(target) >= {"cik", "ticker", "name", "period_end", "fiscal_year", "price", "price_as_of", "revenue", "ebit",
                           "ebitda", "ebitda_approximated", "net_income", "total_debt", "total_debt_is_lower_bound", "cash",
                           "market_cap", "enterprise_value", "ev_is_lower_bound", "ebit_margin", "net_margin",
                           "revenue_yoy", "ev_revenue", "ev_ebitda", "ev_ebit", "pe", "excluded"}


def test_the_model_never_gets_source_facts_but_the_session_keeps_them() -> None:
    ctx, result = _compute(standard_world())
    text = json.dumps(result)
    for forbidden in ("source_facts", "accession_number", "retrieved_at", "filed_date", "concept"):
        assert forbidden not in text
    stored = ctx.tables[result["comps_table_id"]]
    assert stored.table.target.source_facts and stored.table.peers[0].source_facts
    assert stored.table.target.revenue == 215938e6  # volle Präzision im Speicher, nicht die gerundete Fassung


def test_warning_and_exclusion_texts_contain_no_digits_and_no_company_names() -> None:
    ctx, result = _compute(standard_world())
    assert result["warnings"], "die Test-Welt soll Warnungen erzeugen"
    for warning in result["warnings"]:
        assert not _has_digit(warning["text"]), warning
        assert "NVIDIA" not in warning["text"] and "Apple" not in warning["text"]
        assert set(warning) == {"id", "severity", "kind", "company", "field", "text", "params"}
        assert all(isinstance(v, (bool, type(None))) or not _has_digit(str(v)) for v in warning["params"].values())
    for row in (result["target"], *result["peers"]):
        assert all(not _has_digit(reason) for reason in row["excluded"].values())
    # die vollständigen Fassungen mit Zahlen bleiben im Speicher (für die feste Warnungsliste)
    full = ctx.tables[result["comps_table_id"]].warnings[0]["full"]
    assert full["id"] == "W1" and any(ch.isdigit() for ch in full["message"])


def test_numeric_warning_parameters_stay_out_of_the_model_payload() -> None:
    ctx, result = _compute(standard_world())
    mismatch = next(w for w in result["warnings"] if w["kind"] == "fiscal_year_mismatch")
    assert mismatch["params"] == {}  # Abstand in Tagen und Periodenenden stehen nur im Speicher
    stored = next(w for w in ctx.tables[result["comps_table_id"]].warnings if w["full"]["kind"] == "fiscal_year_mismatch")
    assert "offset_days" in stored["full"]["params"]


def test_warning_ids_and_severity_counts() -> None:
    _, result = _compute(standard_world())
    assert [w["id"] for w in result["warnings"]] == [f"W{i}" for i in range(1, len(result["warnings"]) + 1)]
    counts = result["n_warnings_by_severity"]
    assert sum(counts.values()) == len(result["warnings"])
    assert counts["warning"] == sum(w["severity"] == "warning" for w in result["warnings"])


def test_compute_takes_no_period() -> None:
    ctx, peer_set_id = confirmed(standard_world())
    assert _code(call(ctx, "compute_comps_table", peer_set_id=peer_set_id, period={"fiscal_year": 2024})) == "INVALID_ARGUMENTS"


def test_missing_prices_become_exclusions_and_warnings_not_errors() -> None:
    w = standard_world()
    del w.market.prices["AAPL"]
    _, result = _compute(w)
    aapl = next(p for p in result["peers"] if p["ticker"] == "AAPL")
    assert aapl["price"] is None and aapl["enterprise_value"] is None and aapl["ev_revenue"] is None
    assert set(aapl["excluded"]) == {"ev_revenue", "ev_ebitda", "ev_ebit", "pe"}
    assert result["basis"]["n_peers_with_ev_multiples"] == 1
    assert any(w["kind"] == "missing_data" and w["company"] == "AAPL" and w["field"] == "market" for w in result["warnings"])


def test_a_price_provider_outage_is_a_missing_price_not_a_failure() -> None:
    w = standard_world()
    w.market.errors["MSFT"] = httpx.ConnectError("weg")
    _, result = _compute(w)
    assert next(p for p in result["peers"] if p["ticker"] == "MSFT")["price"] is None


def test_price_dates_that_differ_leave_the_basis_date_empty() -> None:
    class Mixed:
        def get_price(self, ticker):
            from scout_research.data.market_provider import PriceQuote
            return PriceQuote(ticker=ticker, price=100.0, as_of_date="2026-10-07" if ticker == "AAPL" else "2026-10-08", source="x")

    w = standard_world()
    w.market = Mixed()
    _, result = _compute(w)
    assert result["basis"]["price_as_of"] is None
    assert {r["price_as_of"] for r in (result["target"], *result["peers"])} == {"2026-10-07", "2026-10-08"}


# --- E5: Fehlerverhalten je Peer ---------------------------------------------------------------------------------


def test_a_peer_without_data_is_skipped_with_a_mandatory_warning_first_in_the_list() -> None:
    w = standard_world()
    ctx, peer_set_id = confirmed(w)
    del w.facts[MSFT_CIK]  # die SEC kennt für MSFT jetzt keine companyfacts mehr (404)
    result = call(ctx, "compute_comps_table", peer_set_id=peer_set_id)

    assert result["skipped_peers"] == [{"ticker": "MSFT", "code": "DATA_NOT_FOUND", "reason": "Bei der SEC liegen keine Daten für das Unternehmen vor."}]
    first = result["warnings"][0]
    assert (first["id"], first["severity"], first["kind"], first["company"]) == ("W1", "warning", "peer_skipped", "MSFT")
    assert not _has_digit(first["text"]) and first["params"] == {"code": "DATA_NOT_FOUND"}
    assert [p["ticker"] for p in result["peers"]] == ["AAPL"] and result["basis"]["n_peers"] == 1
    # sichtbar auch in der gespeicherten Tabelle
    assert ctx.tables[result["comps_table_id"]].table.warnings[0].kind == "peer_skipped"


def test_a_peer_without_revenue_is_skipped_with_its_own_code() -> None:
    w = standard_world()
    ctx, peer_set_id = confirmed(w)
    w.facts[MSFT_CIK] = {"facts": {"us-gaap": {}, "dei": {}}, "cik": 789019}
    result = call(ctx, "compute_comps_table", peer_set_id=peer_set_id)
    assert result["skipped_peers"][0]["code"] == "REVENUE_NOT_FOUND" and result["warnings"][0]["kind"] == "peer_skipped"


def test_a_network_failure_fails_the_whole_tool_and_is_retryable() -> None:
    w = standard_world()
    ctx, peer_set_id = confirmed(w)
    w.status_overrides[f"companyfacts/CIK{MSFT_CIK}"] = 503
    error = call(ctx, "compute_comps_table", peer_set_id=peer_set_id)["error"]
    assert error["code"] == "UPSTREAM_UNAVAILABLE" and error["retryable"] is True
    assert not ctx.tables  # kein halbes Ergebnis


def test_rate_limiting_fails_the_tool_without_retry_and_passes_retry_after() -> None:
    w = standard_world()
    ctx, peer_set_id = confirmed(w)
    w.status_overrides[f"companyfacts/CIK{AAPL_CIK}"] = 429
    error = call(ctx, "compute_comps_table", peer_set_id=peer_set_id)["error"]
    assert error["code"] == "UPSTREAM_RATE_LIMITED" and error["retryable"] is False


def test_all_peers_skipped_is_no_valid_peers_and_names_them() -> None:
    w = standard_world()
    ctx, peer_set_id = confirmed(w)
    del w.facts[AAPL_CIK], w.facts[MSFT_CIK]
    error = call(ctx, "compute_comps_table", peer_set_id=peer_set_id)["error"]
    assert error["code"] == "NO_VALID_PEERS" and [s["ticker"] for s in error["details"]["skipped_peers"]] == ["AAPL", "MSFT"]


def test_target_without_data_fails_the_tool() -> None:
    w = standard_world()
    ctx, peer_set_id = confirmed(w)
    del w.facts[NVDA_CIK]
    ctx._facts.clear()  # die Fakten des Ziels liegen aus der Kandidatensuche im Sitzungs-Zwischenspeicher
    assert _code(call(ctx, "compute_comps_table", peer_set_id=peer_set_id)) == "DATA_NOT_FOUND"


def test_target_without_revenue_is_target_revenue_not_found() -> None:
    w = standard_world()
    ctx, peer_set_id = confirmed(w)
    w.facts[NVDA_CIK] = {"facts": {"us-gaap": {}, "dei": {}}, "cik": 1045810}
    ctx._facts.clear()
    assert _code(call(ctx, "compute_comps_table", peer_set_id=peer_set_id)) == "TARGET_REVENUE_NOT_FOUND"


def test_the_deadline_is_checked_between_companies() -> None:
    w = standard_world()
    ticks = iter(range(0, 10_000, 100))  # jede Uhrablesung vergeht 100 s — mehr als das Budget von 180 s
    ctx, peer_set_id = confirmed(w)
    ctx.clock = lambda: next(ticks)
    error = call(ctx, "compute_comps_table", peer_set_id=peer_set_id)["error"]
    assert error["code"] == "TOOL_TIMEOUT" and error["retryable"] is True


# --- Ticker-Konsistenz (F9) --------------------------------------------------------------------------------------


def test_one_ticker_per_cik_is_used_for_price_rows_statistics_and_warnings() -> None:
    w = standard_world()
    w.tickers.append((AAPL_CIK, "APC", "Apple Inc."))  # zweiter Ticker derselben CIK
    w.meta[AAPL_CIK]["tickers"] = ["APC", "AAPL"]  # submissions nennt eine andere Reihenfolge
    w.market.prices["APC"] = 1.0
    ctx, peer_set_id = confirmed(w)
    result = call(ctx, "compute_comps_table", peer_set_id=peer_set_id)

    assert ctx.ticker_for(AAPL_CIK) == "AAPL"
    assert w.market.calls.count("APC") == 0 and "AAPL" in w.market.calls
    assert [p["ticker"] for p in result["peers"]] == ["AAPL", "MSFT"]
    assert all(w["company"] != "APC" for w in result["warnings"])
    assert all("APC" not in s["excluded"] + s["lower_bound"] for s in result["statistics"])
