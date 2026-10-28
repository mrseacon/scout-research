"""T12 — Secrets (Review 10, F5): weder API-Key noch Kontaktadresse noch User-Agent in Tool-Ergebnissen, Modell-Payloads,
Trace-Ereignissen, Gate-Log oder Logs — auch nicht bei Upstream-Fehlern und unerwarteten Ausnahmen."""

import io
import json
import logging
import traceback

import httpx
import pytest

from scout_research.tools.session import HumanInput
from scout_research.data.market_provider import FinnhubProvider
from scout_research.tools.handlers import dispatch
from scout_research.tools.sanitize import Redactor, RedactingFilter
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
    ctx.record_user_message(HumanInput("Comps für NVDA, dazu AAPL"))
    outcomes = [dispatch(ctx, "resolve_company", {"query": "NVDA"}), dispatch(ctx, "resolve_company", {"query": "AAPL"})]
    found = dispatch(ctx, "find_peer_candidates", {"target_cik": NVDA_CIK})
    outcomes.append(found)
    if not found.is_error:
        proposal = dispatch(ctx, "propose_peer_set", {"candidate_set_id": found.content["candidate_set_id"],
                                                      "peers": [{"cik": AAPL_CIK, "rationale": "Hardware"}, {"cik": MSFT_CIK, "rationale": "Software"}]})
        outcomes.append(proposal)
        if not proposal.is_error:
            peer_set = ctx.confirm_peer_set(proposal.content["proposal_id"], [AAPL_CIK, MSFT_CIK])
            table = dispatch(ctx, "compute_comps_table", {"peer_set_id": peer_set.id})
            outcomes.append(table)
            if not table.is_error:  # Kommentar: einmal abgewiesen (Zahl), einmal angenommen
                tid = table.content["comps_table_id"]
                outcomes.append(dispatch(ctx, "submit_commentary", {"comps_table_id": tid, "text": "Median [[stat:pe:median]] und 40x."}))
                outcomes.append(dispatch(ctx, "submit_commentary", {"comps_table_id": tid, "text": "Median [[stat:pe:median]] ([[warn:W1]])."}))
    outcomes += [dispatch(ctx, "get_financials", {"cik": NVDA_CIK}), dispatch(ctx, "get_market_data", {"ticker": "NVDA"}),
                 dispatch(ctx, "get_financials", {"cik": AAPL_CIK, "period": {"period_end": "1999-01-01"}})]
    return ctx, outcomes


def _assert_clean(ctx, outcomes, caplog) -> None:
    texts = [o.to_json() for o in outcomes] + [json.dumps(o.trace, default=str) for o in outcomes if o.trace]
    texts += [json.dumps(ctx.gate_log, default=str), caplog.text, json.dumps(ctx.commentary_log, default=str)]
    texts += [c.rendered for c in ctx.commentaries.values()]
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


# --- T12 für Logs: der Filter hängt am Handler, nicht am Logger (Plan 12.10, N2) -------------------------------------------

FAKE_ANTHROPIC_KEY = "sk-ant-FAKE-0123456789abcdefghij"
LOG_SECRETS = [FAKE_ANTHROPIC_KEY, FINNHUB_KEY, "welt.tester@example.invalid"]
LOGGER_NAMES = ["httpx2", "anthropic._base_client", "beliebig.name"]


def _log_everything(with_filter: bool) -> str:
    """Schreibt Einträge mit allen drei Secrets über drei Logger in einen Handler und gibt dessen Ausgabe zurück."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    if with_filter:
        handler.addFilter(RedactingFilter(Redactor([*SECRETS, FAKE_ANTHROPIC_KEY])))
    loggers = [logging.getLogger(name) for name in LOGGER_NAMES]
    saved = [(lg, lg.level, lg.propagate) for lg in loggers]
    try:
        for lg in loggers:
            lg.setLevel(logging.DEBUG)
            lg.propagate = False
            lg.addHandler(handler)
        for lg in loggers:
            for secret in LOG_SECRETS:
                lg.info("als Argument: %s", secret)
                lg.warning(f"im Text: {secret} Ende")
                lg.info("Prozent 100%% und %s", {"k": secret})
                try:
                    raise RuntimeError(f"Upstream kaputt, Kopf {secret}")
                except RuntimeError:
                    lg.exception("Ausnahme beim Abruf")
            lg.info("Anfrage: GET https://finnhub.io/api/v1/quote?symbol=NVDA&token=%s", "OHNE-BEKANNTES-GEHEIMNIS")
    finally:
        for lg, level, propagate in saved:
            lg.removeHandler(handler)
            lg.setLevel(level)
            lg.propagate = propagate
    return stream.getvalue()


def test_t12_logs_a_handler_with_the_redacting_filter_never_emits_a_secret() -> None:
    out = _log_everything(with_filter=True)
    assert "Ausnahme beim Abruf" in out and "RuntimeError" in out and "Traceback" in out  # die Einträge sind brauchbar
    assert "100% und" in out
    for secret in [*LOG_SECRETS, "welt.tester", "OHNE-BEKANNTES-GEHEIMNIS"]:
        assert secret not in out, secret
    assert "<redacted>" in out


def test_t12_logs_control_the_same_entries_without_the_filter_do_contain_the_secrets() -> None:
    out = _log_everything(with_filter=False)  # Gegenprobe: sonst prüft der Test oben nichts
    for secret in [*LOG_SECRETS, "OHNE-BEKANNTES-GEHEIMNIS"]:
        assert secret in out, secret


def test_t12_logs_the_filter_survives_a_broken_format_call_and_keeps_the_record() -> None:
    filt = RedactingFilter(Redactor(LOG_SECRETS))
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "zwei %s %s " + FINNHUB_KEY, ("nur eins",), None)
    assert filt.filter(record) is True
    message = record.getMessage()
    assert FINNHUB_KEY not in message and record.args == ()
    assert "zwei %s %s" in message and "nur eins" in message and "<redacted>" in message  # die Meldung bleibt brauchbar


class _OwnTracebackHandler(logging.Handler):
    """Baut den Traceback selbst aus `record.exc_info`, statt `exc_text` zu nehmen (wie manche Fremd-Handler)."""

    def __init__(self) -> None:
        super().__init__()
        self.output: list[str] = []
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info:
            text += "".join(traceback.format_exception(*record.exc_info))
        self.output.append(text)
        self.records.append(record)


def _own_traceback_run(with_filter: bool) -> _OwnTracebackHandler:
    handler = _OwnTracebackHandler()
    if with_filter:
        handler.addFilter(RedactingFilter(Redactor(LOG_SECRETS)))
    logger = logging.getLogger("beliebig.eigener_traceback")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        try:
            raise RuntimeError(f"Upstream kaputt, Kopf {FINNHUB_KEY} und {FAKE_ANTHROPIC_KEY}")
        except RuntimeError:
            logger.exception("Ausnahme beim Abruf")
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
    return handler


def test_t12_logs_a_handler_that_builds_the_traceback_itself_from_exc_info_sees_nothing_and_no_exc_info() -> None:
    handler = _own_traceback_run(with_filter=True)
    (record,) = handler.records
    assert record.exc_info is None
    text = "\n".join(handler.output)
    assert "Ausnahme beim Abruf" in text
    assert FINNHUB_KEY not in text and FAKE_ANTHROPIC_KEY not in text
    assert "RuntimeError" in record.exc_text and "<redacted>" in record.exc_text  # der Traceback steckt geschwärzt in exc_text
    assert FINNHUB_KEY not in record.exc_text and FAKE_ANTHROPIC_KEY not in record.exc_text


def test_t12_logs_control_the_own_traceback_handler_without_the_filter_does_see_the_secrets() -> None:
    handler = _own_traceback_run(with_filter=False)
    assert FINNHUB_KEY in "\n".join(handler.output)


def test_t12_logs_stack_info_is_redacted() -> None:
    def emit_with(with_filter: bool) -> str:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        if with_filter:
            handler.addFilter(RedactingFilter(Redactor(LOG_SECRETS)))
        record = logging.LogRecord("beliebig.name", logging.WARNING, __file__, 1, "mit Stapel", (), None,
                                   sinfo=f"Stack (most recent call last):\n  File x, in f(key={FINNHUB_KEY})")
        handler.handle(record)
        return stream.getvalue()

    assert FINNHUB_KEY in emit_with(with_filter=False)  # Gegenprobe
    out = emit_with(with_filter=True)
    assert "mit Stapel" in out and "Stack (most recent call last)" in out and "<redacted>" in out
    assert FINNHUB_KEY not in out
