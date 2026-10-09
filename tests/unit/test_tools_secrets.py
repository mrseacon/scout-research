"""T12 — Secrets (Review 10, F5): weder API-Key noch Kontaktadresse noch User-Agent in Tool-Ergebnissen, Modell-Payloads,
Trace-Ereignissen, Gate-Log oder Logs — auch nicht bei Upstream-Fehlern und unerwarteten Ausnahmen."""

import json
import logging

import httpx
import pytest

from scout_research.data.market_provider import FinnhubProvider
from scout_research.tools.handlers import dispatch
from tests.unit.tool_world import (
    AAPL_CIK, FINNHUB_KEY, MSFT_CIK, NVDA_CIK, SECRETS, UA, World, standard_world,
)

ALL_SECRETS = [*SECRETS, "welt.tester", "example.invalid", "FAKE-FINNHUB-KEY"]


def _finnhub(handler) -> FinnhubProvider:
    return FinnhubProvider(FINNHUB_KEY, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)


def _full_flow(w: World, market=None):
    """Nutzer, Auflösung, Kandidaten, Vorschlag, Bestätigung, Tabelle, Einzeldaten — jeder Schritt wird protokolliert."""
    if market is not None:
        w.market = market
    ctx = w.context()
    ctx.record_user_message("Comps für NVDA, dazu AAPL")
    outcomes = [dispatch(ctx, "resolve_company", {"query": "NVDA"}), dispatch(ctx, "resolve_company", {"query": "AAPL"})]
    found = dispatch(ctx, "find_peer_candidates", {"target_cik": NVDA_CIK})
    outcomes.append(found)
    if not found.is_error:
        proposal = dispatch(ctx, "propose_peer_set", {"candidate_set_id": found.content["candidate_set_id"],
                                                      "peers": [{"cik": AAPL_CIK, "rationale": "Hardware"}, {"cik": MSFT_CIK, "rationale": "Software"}]})
        outcomes.append(proposal)
        if not proposal.is_error:
            peer_set = ctx.confirm_peer_set(proposal.content["proposal_id"], [AAPL_CIK, MSFT_CIK])
            outcomes.append(dispatch(ctx, "compute_comps_table", {"peer_set_id": peer_set.id}))
    outcomes += [dispatch(ctx, "get_financials", {"cik": NVDA_CIK}), dispatch(ctx, "get_market_data", {"ticker": "NVDA"}),
                 dispatch(ctx, "get_financials", {"cik": AAPL_CIK, "period": {"period_end": "1999-01-01"}})]
    return ctx, outcomes


def _assert_clean(ctx, outcomes, caplog) -> None:
    texts = [o.to_json() for o in outcomes] + [json.dumps(o.trace, default=str) for o in outcomes if o.trace]
    texts += [json.dumps(ctx.gate_log, default=str), caplog.text]
    texts += [repr(o.content) for o in outcomes]
    for text in texts:
        for secret in ALL_SECRETS:
            assert secret not in text, f"{secret!r} in {text[:200]!r}"
        assert "token=" not in text.replace("token=<redacted>", "")


def test_a_clean_run_contains_no_secrets(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    ctx, outcomes = _full_flow(standard_world())
    assert any(not o.is_error for o in outcomes)
    _assert_clean(ctx, outcomes, caplog)


EDGAR_FAILURES = {
    "429": lambda w: w.status_overrides.update({"companyfacts/CIK": 429}),
    "403": lambda w: w.status_overrides.update({"companyfacts/CIK": 403}),
    "404": lambda w: w.status_overrides.update({"companyfacts/CIK": 404}),
    "400": lambda w: w.status_overrides.update({"submissions/CIK": 400}),
    "503": lambda w: w.status_overrides.update({"companyfacts/CIK": 503}),
    "302": lambda w: w.status_overrides.update({"companyfacts/CIK": 302}),
    "frames-503": lambda w: w.status_overrides.update({"/frames/": 503}),
    "browse-edgar-500": lambda w: w.status_overrides.update({"browse-edgar": 500}),
    "ticker-map-503": lambda w: w.status_overrides.update({"company_tickers": 503}),
    "netzwerk": lambda w: w.raisers.update({"companyfacts/CIK": lambda r: httpx.ConnectError("verbindung", request=r)}),
    "decoding": lambda w: w.raisers.update({"companyfacts/CIK": lambda r: httpx.DecodingError("kaputt", request=r)}),
    "redirects": lambda w: w.raisers.update({"/frames/": lambda r: httpx.TooManyRedirects("zu viele", request=r)}),
    "json": lambda w: w.garbage.update({"companyfacts/CIK"}),
    "json-submissions": lambda w: w.garbage.update({"submissions/CIK"}),
}


@pytest.mark.parametrize("failure", sorted(EDGAR_FAILURES))
def test_edgar_failures_leak_nothing(failure: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    w = standard_world()
    EDGAR_FAILURES[failure](w)
    ctx, outcomes = _full_flow(w)
    assert any(o.is_error for o in outcomes), "der Fehler muss im Ablauf tatsächlich auftreten"
    _assert_clean(ctx, outcomes, caplog)


FINNHUB_FAILURES = {
    "429": lambda r: httpx.Response(429, headers={"Retry-After": "0"}),
    "500": lambda r: httpx.Response(500),
    "403": lambda r: httpx.Response(403),
    "json": lambda r: httpx.Response(200, text="<html>"),
    "timeout": lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("zu langsam", request=r)),
    "netzwerk": lambda r: (_ for _ in ()).throw(httpx.ConnectError("weg", request=r)),
}


@pytest.mark.parametrize("failure", sorted(FINNHUB_FAILURES))
def test_price_provider_failures_leak_nothing(failure: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    ctx, outcomes = _full_flow(standard_world(), market=_finnhub(FINNHUB_FAILURES[failure]))
    table = next(o for o in outcomes if "comps_table_id" in o.content)
    assert table.content["target"]["price"] is None  # Anbieterausfall = fehlender Kurs, kein Fehler
    _assert_clean(ctx, outcomes, caplog)


def test_unexpected_exceptions_carrying_secrets_are_redacted_in_the_trace_and_absent_from_the_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class LeakyMarket:
        def get_price(self, ticker):
            raise RuntimeError(f"Anbieter kaputt: https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_KEY} "
                               f"Header User-Agent: {UA}")

    caplog.set_level(logging.DEBUG)
    ctx, outcomes = _full_flow(standard_world(), market=LeakyMarket())
    internal = [o for o in outcomes if o.is_error and o.content["error"]["code"] == "INTERNAL_ERROR"]
    assert internal, "die Ausnahme muss als INTERNAL_ERROR ankommen"
    trace = json.dumps([o.trace for o in internal])
    assert "Anbieter kaputt" in trace and "<redacted>" in trace  # der Trace ist brauchbar, aber geschwärzt
    _assert_clean(ctx, outcomes, caplog)


def test_the_redactor_catches_secrets_it_was_not_told_about_by_pattern() -> None:
    ctx = standard_world().context(secrets_to_redact=[])
    assert "KEY123" not in ctx.redact("GET https://x/quote?symbol=A&token=KEY123&y=1")
    assert "abc.def" not in ctx.redact("Authorization: Bearer abc.def")
