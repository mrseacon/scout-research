"""get_financials und get_market_data: Provenance, Flags, Perioden, Allowlist-Zugriff (Plan, Abschnitt 2)."""

import httpx

from tests.unit.tool_world import AAPL_CIK, MSFT_CIK, NVDA_CIK, World, call, standard_world, start


def _code(result: dict) -> str:
    return result["error"]["code"]


def _ctx(w: World | None = None):
    ctx, _ = start(w or standard_world())
    return ctx


def test_financials_carry_value_unit_concept_and_accession_number() -> None:
    result = call(_ctx(), "get_financials", cik=NVDA_CIK)
    assert result["financials_id"].startswith("fin_") and result["ticker"] == "NVDA"
    assert (result["period_end"], result["fiscal_year"]) == ("2026-01-25", 2026)
    revenue = result["values"]["revenue"]
    assert revenue == {"value": 215938, "unit": "Mio. USD", "flags": [], "concept": "Revenues",
                       "accession_number": "0001045810-26-000021"}
    assert list(result["values"]) == ["revenue", "ebit", "ebitda", "net_income", "total_assets", "total_debt", "cash",
                                      "shares_outstanding"]
    assert (result["ebit_margin"], result["net_margin"], result["revenue_yoy"]) == (60.4, 55.6, 65.5)


def test_ebitda_is_flagged_as_approximation_with_both_source_concepts() -> None:
    ebitda = call(_ctx(), "get_financials", cik=NVDA_CIK, metrics=["ebitda"])["values"]["ebitda"]
    assert ebitda["flags"] == ["approximated"] and ebitda["value"] == 133230
    assert ebitda["concept"].startswith("OperatingIncomeLoss+") and ebitda["accession_number"]


def test_shares_are_reported_in_millions_of_shares() -> None:
    shares = call(_ctx(), "get_financials", cik=NVDA_CIK, metrics=["shares_outstanding"])["values"]["shares_outstanding"]
    assert shares["unit"] == "Mio. Stück" and shares["concept"] == "EntityCommonStockSharesOutstanding"
    assert 20_000 < shares["value"] < 30_000


def test_a_debt_lower_bound_is_flagged() -> None:
    w = standard_world()
    cik = w.add_real("CDNS")
    ctx, _ = start(w)
    ctx.record_user_message("Und CDNS?")
    call(ctx, "resolve_company", query="CDNS")
    debt = call(ctx, "get_financials", cik=cik, metrics=["total_debt"])["values"]["total_debt"]
    from scout_research.domain.metrics import DEBT_LOWER_BOUND_CONCEPTS

    assert debt["flags"] == ["lower_bound"] and debt["concept"] in DEBT_LOWER_BOUND_CONCEPTS


def test_a_missing_value_is_explained_not_estimated() -> None:
    w = standard_world()
    ctx, _ = start(w)
    ctx.record_user_message("Und MSFT?")
    call(ctx, "resolve_company", query="MSFT")
    values = call(ctx, "get_financials", cik=MSFT_CIK, metrics=["ebitda", "ebit"])["values"]
    assert values["ebitda"] == {"unavailable_reason": values["ebitda"]["unavailable_reason"]}
    assert "EBITDA" in values["ebitda"]["unavailable_reason"] and "value" not in values["ebitda"]
    assert values["ebit"]["value"] == 155237


def test_metrics_subset_keeps_the_requested_order_and_rejects_duplicates_and_unknown_names() -> None:
    ctx = _ctx()
    assert list(call(ctx, "get_financials", cik=NVDA_CIK, metrics=["cash", "revenue"])["values"]) == ["cash", "revenue"]
    assert _code(call(ctx, "get_financials", cik=NVDA_CIK, metrics=["cash", "cash"])) == "INVALID_ARGUMENTS"
    assert _code(call(ctx, "get_financials", cik=NVDA_CIK, metrics=["dividends"])) == "INVALID_ARGUMENTS"


def test_historical_periods_are_allowed_for_financials_without_any_market_data() -> None:
    w = standard_world()
    ctx = _ctx(w)
    w.market.calls.clear()
    result = call(ctx, "get_financials", cik=NVDA_CIK, period={"period_end": "2025-01-26"}, metrics=["revenue", "ebit"])
    assert result["period_end"] == "2025-01-26" and result["values"]["revenue"]["value"] == 130497
    assert result["revenue_yoy"] is not None and w.market.calls == []  # Financials brauchen keinen Kurs


def test_fiscal_year_selector_and_unknown_period() -> None:
    ctx = _ctx()
    assert call(ctx, "get_financials", cik=NVDA_CIK, period={"fiscal_year": 2025}, metrics=["revenue"])["period_end"] == "2025-01-26"
    error = call(ctx, "get_financials", cik=NVDA_CIK, period={"period_end": "1999-12-31"})["error"]
    assert error["code"] == "PERIOD_NOT_AVAILABLE" and "NVDA" in error["message"]
    assert "2026-01-25" in error["details"]["available_period_ends"]


def test_financials_for_an_unallowed_company_never_touch_the_network() -> None:
    w = standard_world()
    ctx = _ctx(w)
    before = len(w.requests)
    assert _code(call(ctx, "get_financials", cik=AAPL_CIK)) == "NOT_IN_ALLOWLIST"
    assert len(w.requests) == before


# --- get_market_data ---------------------------------------------------------------------------------------------


def test_market_data_with_provenance_for_the_shares() -> None:
    result = call(_ctx(), "get_market_data", ticker="nvda")
    assert result["ticker"] == "NVDA" and result["price"] == 180.0 and result["price_as_of"] == "2026-10-08"
    assert result["price_source"] == "fake" and result["price_note"] == "letzter Kurs, während US-Handelszeit intraday"
    assert result["shares_source"]["concept"] == "EntityCommonStockSharesOutstanding" and result["shares_source"]["accession_number"]
    assert 20_000 < result["shares_outstanding_mio"] < 30_000
    assert abs(result["market_cap_musd"] - 180.0 * result["shares_outstanding_mio"]) <= 180 * 0.05 + 1  # Rundung der Aktienanzahl
    assert result["unavailable_reason"] is None


def test_missing_price_is_not_an_error() -> None:
    w = standard_world()
    del w.market.prices["NVDA"]
    result = call(_ctx(w), "get_market_data", ticker="NVDA")
    assert result["price"] is None and result["market_cap_musd"] is None
    assert result["shares_outstanding_mio"] is not None and "Kein Kurs" in result["unavailable_reason"]


def test_provider_outage_is_not_an_error_either() -> None:
    w = standard_world()
    w.market.errors["NVDA"] = httpx.ReadTimeout("zu langsam")
    result = call(_ctx(w), "get_market_data", ticker="NVDA")
    assert result["price"] is None and "Kein Kurs" in result["unavailable_reason"]


def test_shares_missing_from_the_filing_are_reported() -> None:
    w = standard_world()
    del w.facts[NVDA_CIK]["facts"]["dei"]["EntityCommonStockSharesOutstanding"]
    result = call(_ctx(w), "get_market_data", ticker="NVDA")
    assert result["price"] == 180.0 and result["shares_outstanding_mio"] is None and result["market_cap_musd"] is None
    assert "Aktienanzahl" in result["unavailable_reason"]


def test_market_data_uses_the_session_ticker_for_an_alias() -> None:
    w = standard_world()
    w.tickers.append((NVDA_CIK, "NVDX", "NVIDIA CORP"))
    ctx = _ctx(w)
    result = call(ctx, "get_market_data", ticker="NVDX")  # Alias derselben CIK → derselbe Sitzungs-Ticker
    assert result["ticker"] == "NVDA" and w.market.calls[-1] == "NVDA"
