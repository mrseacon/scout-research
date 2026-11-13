"""TA-20.6 Teil 1 (Fable 05 I-2): Datums- und Accession-Felder werden am Rand der Datenschicht geprüft. Ein Wert, der kein
ISO-Datum `YYYY-MM-DD` (bzw. keine Accession-Nummer) ist, kommt nie bis zur Anzeige: `fmt_date` reicht nichts mehr durch,
die Modelle weisen ab, ein präpariertes `end` in den companyfacts endet als `INTERNAL_ERROR` (fail-closed, wie jede
unerwartete Datenform) und erscheint nirgends im Tool-Ergebnis."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from scout_research.data.formats import AccessionNumber
from scout_research.data.market_provider import PriceQuote
from scout_research.domain.metrics import available_period_ends
from scout_research.domain.models import FinancialFact, MarketSnapshot
from scout_research.tools.commentary import basis_line, render_table
from scout_research.tools.slots import fmt_date
from tests.unit.test_tools_commentary import _table
from tests.unit.tool_world import NVDA_CIK, call, resolved, standard_world, start

BAD_DATES = ["<b>12345</b>", "2026-1-5", "20260105", "2026-13-01", "2026-02-30", "2026-01-05\n", "", " 2026-01-05",
             "２０２６-０１-０５", "2026-01-05T00:00:00"]
_ACCESSION = TypeAdapter(AccessionNumber)
GOOD_ACCN = "0001045810-26-000021"
HOSTILE = "<b>12345</b>"


def _fact(**kw) -> FinancialFact:
    base = dict(cik="0001045810", concept="Revenues", value=1.0, unit="USD", period_start="2025-01-27", period_end="2026-01-25",
                fiscal_year=2026, fiscal_period="FY", form_type="10-K", accession_number=GOOD_ACCN, filed_date="2026-02-25",
                retrieved_at=datetime.now(timezone.utc))
    base.update(kw)
    return FinancialFact(**base)


@pytest.mark.parametrize("bad", BAD_DATES)
@pytest.mark.parametrize("field", ["period_start", "period_end", "filed_date"])
def test_a_financial_fact_rejects_dates_that_are_not_iso(field: str, bad: str) -> None:
    with pytest.raises(ValidationError):
        _fact(**{field: bad})


def test_a_financial_fact_accepts_iso_dates_and_a_missing_start() -> None:
    assert _fact(period_start=None).period_start is None


def test_joined_accession_numbers_are_accepted() -> None:
    joined = f"{GOOD_ACCN}+0001045810-26-000022"
    assert _ACCESSION.validate_python(joined) == joined and _ACCESSION.validate_python(GOOD_ACCN) == GOOD_ACCN


@pytest.mark.parametrize("bad", ["<b>1</b>", "A", "0001045810-26-00002"])
def test_a_financial_fact_rejects_accession_numbers_of_another_shape(bad: str) -> None:
    with pytest.raises(ValidationError):
        _fact(accession_number=bad)


def test_a_financial_fact_accepts_a_joined_accession_number() -> None:
    joined = f"{GOOD_ACCN}+0001045810-26-000022"
    assert _fact(accession_number=joined).accession_number == joined


@pytest.mark.parametrize("tool", ["get_financials", "find_peer_candidates"])
def test_a_prepared_accession_in_the_companyfacts_ends_as_internal_error_and_is_shown_nowhere(tool: str) -> None:
    w = standard_world()
    facts = w.facts[NVDA_CIK]["facts"]["us-gaap"]
    annual = [e for c in facts.values() for u in c["units"].values() for e in u if e["form"] == "10-K" and e["fp"] == "FY"]
    for e in annual:
        e["accn"] = HOSTILE
    ctx = resolved(w)
    result = call(ctx, tool, **({"cik": NVDA_CIK} if tool == "get_financials" else {"target_cik": NVDA_CIK}))
    assert result["error"]["code"] == "INTERNAL_ERROR"
    assert "12345" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("bad", [HOSTILE, "0001045810-26-00002", "000104581026000021", "0001045810-26-000021+", "0001045810-26-000021 ",
                                 "0001045810-26-000021\n", "0001045810-26-000021+x", ""])
def test_the_accession_type_rejects_numbers_of_another_shape(bad: str) -> None:
    with pytest.raises(ValidationError):
        _ACCESSION.validate_python(bad)


@pytest.mark.parametrize("bad", BAD_DATES)
def test_quotes_and_snapshots_reject_dates_that_are_not_iso(bad: str) -> None:
    with pytest.raises(ValidationError):
        PriceQuote(ticker="X", price=1.0, as_of_date=bad, source="s")
    with pytest.raises(ValidationError):
        MarketSnapshot(ticker="X", price=1.0, shares_outstanding=1.0, market_cap=1.0, as_of_date=bad, source="s")


@pytest.mark.parametrize("bad", BAD_DATES)
@pytest.mark.parametrize("lang", ["de", "en"])
def test_fmt_date_raises_instead_of_passing_a_string_through(bad: str, lang: str) -> None:
    with pytest.raises(ValueError):
        fmt_date(bad, lang)


def test_fmt_date_still_formats_iso_dates() -> None:
    assert fmt_date("2026-01-25", "de") == "25.01.2026" and fmt_date("2026-01-25", "en") == "Jan 25, 2026"


def test_the_basis_line_and_the_table_refuse_a_hostile_date_instead_of_showing_it() -> None:
    table = _compact_table()
    table["basis"]["target_period_end"] = HOSTILE
    with pytest.raises(ValueError):
        basis_line(table, "de")
    table = _compact_table()
    table["target"]["period_end"] = HOSTILE
    with pytest.raises(ValueError):
        render_table(table, "de")


def _compact_table() -> dict:
    ctx, table_id, _ = _table()  # dieselbe Beispieltabelle wie die Kommentar-Tests
    return json.loads(json.dumps(ctx.tables[table_id].compact))


def _poison_latest_end(w, cik: str, bad: str) -> None:
    """Setzt `end` aller Jahreseinträge (10-K, FY) mit dem jüngsten Periodenende auf `bad`."""
    facts = w.facts[cik]["facts"]["us-gaap"]
    annual = [e for c in facts.values() for u in c["units"].values() for e in u if e["form"] == "10-K" and e["fp"] == "FY"]
    latest = max(e["end"] for e in annual)
    for e in annual:
        if e["end"] == latest:
            e["end"] = bad


@pytest.mark.parametrize("bad", [HOSTILE, "20260125"])  # „20260125“ nimmt `date.fromisoformat` an, ISO im Sinn des Vertrags ist es nicht
@pytest.mark.parametrize("tool", ["get_financials", "find_peer_candidates"])
def test_a_prepared_end_in_the_companyfacts_ends_as_internal_error_and_is_shown_nowhere(tool: str, bad: str) -> None:
    w = standard_world()
    _poison_latest_end(w, NVDA_CIK, bad)
    ctx = resolved(w)  # noch kein Abruf der companyfacts
    result = call(ctx, tool, **({"cik": NVDA_CIK} if tool == "get_financials" else {"target_cik": NVDA_CIK}))
    assert result["error"]["code"] == "INTERNAL_ERROR"
    shown = json.dumps(result, ensure_ascii=False)
    assert "12345" not in shown and "<b>" not in shown and "20260125" not in shown


def test_a_prepared_end_makes_the_list_of_available_period_ends_raise_instead_of_filtering() -> None:
    w = standard_world()
    _poison_latest_end(w, NVDA_CIK, HOSTILE)
    with pytest.raises(ValueError):
        available_period_ends(w.facts[NVDA_CIK])


@pytest.mark.parametrize("tool", ["get_financials", "find_peer_candidates"])
def test_an_explicit_prior_year_with_a_broken_latest_end_is_an_error_never_the_current_period(tool: str) -> None:
    """M1: Ein stilles Filtern machte das Vorjahr zum „jüngsten“ Ende (`is_historical=False`, heutiger Kurs, E3 offen)."""
    w = standard_world()
    _poison_latest_end(w, NVDA_CIK, HOSTILE)
    ctx = resolved(w)
    period = {"period_end": "2025-01-26"}
    result = call(ctx, tool, **({"cik": NVDA_CIK} if tool == "get_financials" else {"target_cik": NVDA_CIK}), period=period)
    assert result["error"]["code"] == "INTERNAL_ERROR"
    assert "12345" not in json.dumps(result, ensure_ascii=False)


def test_a_quote_with_a_non_iso_date_is_not_passed_on_by_get_market_data() -> None:
    w = standard_world()
    ctx, _ = start(w)
    good = w.market.get_price("NVDA")
    w.market.get_price = lambda ticker: PriceQuote.model_construct(ticker=ticker, price=good.price, as_of_date=HOSTILE, source="fake")  # type: ignore[method-assign]
    result = call(ctx, "get_market_data", ticker="NVDA")
    assert result["error"]["code"] == "INTERNAL_ERROR"
    assert "12345" not in json.dumps(result, ensure_ascii=False)

