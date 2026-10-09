"""L3 — Tool-Handler und Dispatcher (Phase-3-Plan, Abschnitt 2 und 4; Schritt 5).

`handle_x(ctx, args) -> XResult | ToolError`. Handler enthalten keinen UI-Zustand; sie lesen und schreiben nur den
Sitzungsspeicher (`ToolContext`). Der Dispatcher validiert die Eingabe serverseitig mit Pydantic, setzt das Zeitbudget,
erzwingt die Gate-Sperre und den `LOOP_GUARD` und bildet jede Ausnahme auf einen bereinigten `ToolError` ab.

`submit_commentary` (Schritt 6) delegiert an `tools/commentary.py`: Slot-Rendering und Backstop gegen nackte Zahlen.
"""

from __future__ import annotations

import functools
import json
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from scout_research.data.edgar_client import EdgarDataNotFound, pad_cik
from scout_research.domain.comps import build_comps_table
from scout_research.domain.metrics import (
    RevenueNotFoundError,
    available_period_ends,
    build_company_metrics,
    select_revenue_anchor,
)
from scout_research.domain.models import CompanyMetrics, QualityWarning
from scout_research.domain.peers import find_peer_candidates, rank_candidates
from scout_research.domain.periods import PeriodNotAvailable, PeriodSelector
from scout_research.tools import commentary, payloads
from scout_research.tools.errors import ToolError, ToolFailure, fail, make_error, map_exception
from scout_research.tools.resolve import normalize_ticker
from scout_research.tools.sanitize import clean_text, is_valid_ticker
from scout_research.tools.schemas import (
    ALL_METRICS,
    ComputeCompsTableInput,
    FindPeerCandidatesInput,
    GetFinancialsInput,
    GetMarketDataInput,
    ProposePeerSetInput,
    ResolveCompanyInput,
    SubmitCommentaryInput,
    strict_schema,
)
from scout_research.tools.session import (
    CandidateSet,
    Deadline,
    Proposal,
    StoredFinancials,
    StoredTable,
    ToolContext,
)

DEFAULT_SIZE_RANGE = (0.2, 5.0)
RATIONALE_SOURCE = "model_assessment_unverified"


# --- Ergebnisse (kompakte Fassung für das Modell) ----------------------------------------------------------------


class ResolveCompanyResult(BaseModel):
    status: str
    company: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] | None = None
    candidate_count: int


class FindPeerCandidatesResult(BaseModel):
    candidate_set_id: str
    target: dict[str, Any]
    calendar_year_used: int
    frame_check: str
    size_range: dict[str, float]
    candidates: list[dict[str, Any]]
    counts: dict[str, Any]
    warnings: list[dict[str, Any]]


class ProposePeerSetResult(BaseModel):
    proposal_id: str
    status: str
    peers: list[dict[str, Any]]
    user_additions: list[dict[str, Any]]
    rationale_source: str


class ComputeCompsTableResult(BaseModel):
    comps_table_id: str
    basis: dict[str, Any]
    target: dict[str, Any]
    peers: list[dict[str, Any]]
    statistics: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    n_warnings_by_severity: dict[str, int]
    skipped_peers: list[dict[str, Any]]


class GetFinancialsResult(BaseModel):
    financials_id: str
    cik: str
    ticker: str | None
    name: str
    period_end: str
    fiscal_year: int
    values: dict[str, dict[str, Any]]
    ebit_margin: float | int | None
    net_margin: float | int | None
    revenue_yoy: float | int | None


class GetMarketDataResult(BaseModel):
    ticker: str
    price: float | int | None
    price_as_of: str | None
    price_source: str | None
    price_note: str
    shares_outstanding_mio: float | int | None
    shares_source: dict[str, str] | None
    market_cap_musd: float | int | None
    unavailable_reason: str | None


# --- Hilfen ------------------------------------------------------------------------------------------------------


def guarded(fn: Callable[[ToolContext, Any], BaseModel]) -> Callable[[ToolContext, Any], BaseModel | ToolError]:
    """Jede Ausnahme im Handler wird zum `ToolError`; der Loop stürzt nie wegen eines Tools ab."""

    @functools.wraps(fn)
    def wrapper(ctx: ToolContext, args: Any) -> BaseModel | ToolError:
        try:
            return fn(ctx, args)
        except ToolFailure as exc:
            return exc.error
        except Exception as exc:  # noqa: BLE001 — bewusst alles: Details gehen nur in den Trace
            ctx.last_exception = exc
            return map_exception(exc)

    return wrapper


def _not_allowed() -> ToolFailure:
    return fail("NOT_IN_ALLOWLIST")


def _require_allowed(ctx: ToolContext, cik: str) -> None:
    if ctx.allow_reason(cik) is None:
        raise _not_allowed()


def _anchor(ctx: ToolContext, cik: str, period: PeriodSelector | None):
    """Umsatz-Anker der gewählten Periode; `PeriodNotAvailable` bekommt den Ticker für die Meldung."""
    try:
        return select_revenue_anchor(cik, ctx.facts(cik), period)
    except PeriodNotAvailable as exc:
        raise ToolFailure(
            map_exception(exc, ticker=ctx.ticker_for(cik))
        ) from None


def _fetch_price(ctx: ToolContext, ticker: str | None):
    """Kurs holen; fehlende Marktdaten sind kein Fehler (Warnung im Ergebnis), Anbieterausfälle ebenso."""
    if ticker is None:
        return None
    try:
        quote = ctx.market.get_price(ticker)
    except httpx.HTTPError:
        return None
    if quote is not None and quote.ticker != ticker:
        quote = quote.model_copy(update={"ticker": ticker})
    return quote


def _build_metrics(ctx: ToolContext, cik: str, period: PeriodSelector | None = None, with_price: bool = True) -> CompanyMetrics:
    ticker = ctx.ticker_for(cik)
    quote = _fetch_price(ctx, ticker) if with_price else None
    try:
        return build_company_metrics(cik, ctx.metadata(cik), ctx.facts(cik), quote, period)
    except PeriodNotAvailable as exc:
        raise ToolFailure(map_exception(exc, ticker=ticker)) from None


def _problems(code: str, problems: list[dict[str, Any]], hint: str | None = None) -> ToolFailure:
    return fail(code, hint, problems=problems)


# --- 1 resolve_company -------------------------------------------------------------------------------------------


@guarded
def handle_resolve_company(ctx: ToolContext, args: ResolveCompanyInput) -> ResolveCompanyResult:
    resolution = ctx.resolve_query(args.query)

    if resolution.status == "resolved" and resolution.company is not None and resolution.match is not None:
        company = resolution.company
        if resolution.match == "ticker_exact":
            ctx.pin_ticker(company.cik, normalize_ticker(args.query))
        ticker = ctx.ticker_for(company.cik)
        if ticker is None:
            return ResolveCompanyResult(status="not_found", candidate_count=0)
        metadata = ctx.metadata(company.cik)
        ctx.note_resolved(company.cik, args.query, resolution.match)
        ctx.resolved_names[company.cik] = clean_text(metadata.name or company.name, 80)
        if metadata.sic_code:
            ctx.known_sic.add(clean_text(metadata.sic_code, 10))
        return ResolveCompanyResult(
            status="resolved",
            company={
                "cik": company.cik,
                "ticker": ticker,
                "tickers": [t for t in company.tickers if is_valid_ticker(t)],
                "name": clean_text(metadata.name or company.name, 80),
                "sic_code": clean_text(metadata.sic_code, 10) if metadata.sic_code else None,
                "sic_description": clean_text(metadata.sic_description, 120) if metadata.sic_description else None,
                "fiscal_year_end": clean_text(metadata.fiscal_year_end, 10) if metadata.fiscal_year_end else None,
            },
            candidate_count=1,
        )

    if resolution.status == "ambiguous":
        candidates = [
            {"cik": c.company.cik, "ticker": ctx.ticker_for(c.company.cik), "name": clean_text(c.company.name, 80),
             "match": c.match}
            for c in resolution.candidates
            if ctx.ticker_for(c.company.cik) is not None
        ]
        return ResolveCompanyResult(status="ambiguous", candidates=candidates, candidate_count=resolution.candidate_count)

    return ResolveCompanyResult(status="not_found", candidate_count=0)


# --- 2 find_peer_candidates --------------------------------------------------------------------------------------


@guarded
def handle_find_peer_candidates(ctx: ToolContext, args: FindPeerCandidatesInput) -> FindPeerCandidatesResult:
    cik = pad_cik(args.target_cik)
    _require_allowed(ctx, cik)  # E1 A: das Ziel muss selbst die Allowlist-Regel (c) erfüllen

    metadata = ctx.metadata(cik)
    ticker = ctx.ticker_for(cik)
    anchor = _anchor(ctx, cik, args.period)
    if anchor.period_end != available_period_ends(ctx.facts(cik))[0]:  # E3 A
        raise fail("HISTORICAL_VALUATION_NOT_SUPPORTED", tickers=[ticker or cik])
    if not metadata.sic_code:
        raise fail("PEER_SEARCH_NOT_POSSIBLE", reason="no_sic_code")
    if anchor.value <= 0:
        raise fail("PEER_SEARCH_NOT_POSSIBLE", reason="non_positive_revenue")

    size = args.size_range
    size_range = (size.min, size.max) if size is not None else DEFAULT_SIZE_RANGE
    ctx.deadline.check()
    result = find_peer_candidates(
        ctx.edgar, cik, metadata.sic_code, anchor.value, anchor.period_end, anchor.concept, size_range
    )
    ctx.deadline.check()

    # Kandidaten ohne gültigen Ticker werden nicht ausgegeben (F3) und zählen zu „ohne Ticker“.
    usable = [c for c in result.candidates if ctx.ticker_for(c.cik) is not None]
    dropped = len(result.candidates) - len(usable)
    ranked, truncated = rank_candidates(usable)

    candidates = [
        {"cik": c.cik, "ticker": ctx.ticker_for(c.cik), "name": clean_text(c.name, 80), "sic_code": c.sic_code,
         "revenue_musd": payloads.musd(c.revenue), "size_ratio": payloads.ratio2(c.size_ratio)}
        for c in ranked
    ]
    target = {
        "cik": cik,
        "ticker": ticker,
        "name": clean_text(metadata.name, 80),
        "sic_code": metadata.sic_code,
        "sic_description": clean_text(metadata.sic_description or "", 120),
        "period_end": anchor.period_end,
        "fiscal_year": anchor.fiscal_year,
        "revenue_musd": payloads.musd(anchor.value),
    }
    counts = result.counts
    warnings: list[dict[str, Any]] = []
    if counts.sic_search_truncated:
        warnings.append({"id": "P1", "severity": "warning", "kind": "sic_search_truncated",
                         "text": payloads._warning_text("sic_search_truncated", {})})
    result_counts = {
        "sic_matches": counts.sic_matches,
        "without_ticker": counts.without_ticker + dropped,
        "not_in_revenue_frame": counts.not_in_revenue_frame,
        "outside_size_range": counts.outside_size_range,
        "passed_filters": len(usable),
        "returned": len(candidates),
        "truncated": truncated,
        "sic_search_truncated": counts.sic_search_truncated,
    }

    set_id = ctx.new_handle("candidate_set")
    ctx.candidate_sets[set_id] = CandidateSet(
        id=set_id,
        target_cik=cik,
        target=target,
        period_end=anchor.period_end,
        candidates=candidates,
        warnings=warnings,
        terms=commentary.exempt_terms([target["name"], target["ticker"], *[x for c in candidates for x in (c["name"], c["ticker"])]]),
    )
    return FindPeerCandidatesResult(
        candidate_set_id=set_id,
        target=target,
        calendar_year_used=result.calendar_year.calendar_year,
        frame_check=result.calendar_year.frame_check,
        size_range={"min": size_range[0], "max": size_range[1]},
        candidates=candidates,
        counts=result_counts,
        warnings=warnings,
    )


# --- 3 propose_peer_set (das Gate) -------------------------------------------------------------------------------


@guarded
def handle_propose_peer_set(ctx: ToolContext, args: ProposePeerSetInput) -> ProposePeerSetResult:
    candidate_set = ctx.lookup("candidate_set", args.candidate_set_id, ctx.candidate_sets)
    by_cik = {c["cik"]: c for c in candidate_set.candidates}
    peers = [(i, pad_cik(p.cik), p.rationale) for i, p in enumerate(args.peers)]
    exclusions = [(i, pad_cik(e.cik), e.reason) for i, e in enumerate(args.notable_exclusions or [])]
    additions = list(args.user_requested_additions or [])

    # 1. Dubletten
    seen: set[str] = set()
    problems: list[dict[str, Any]] = []
    for i, cik, _ in peers:
        if cik in seen:
            problems.append({"field": f"peers[{i}].cik", "reason": "duplicate"})
        seen.add(cik)
    if problems:
        raise _problems("INVALID_ARGUMENTS", problems)

    # 2. nur Kandidaten (Peers und Ausschlüsse)
    problems = [{"field": f"peers[{i}].cik", "cik": cik} for i, cik, _ in peers if cik not in by_cik]
    problems += [{"field": f"notable_exclusions[{i}].cik", "cik": cik} for i, cik, _ in exclusions if cik not in by_cik]
    if problems:
        raise _problems("PEER_NOT_IN_CANDIDATES", problems)

    # 3. keine Slots, 4. keine Zahlen in den Begründungen (Namen mit Ziffern, z. B. „3M“, sind ausgenommen)
    texts = [(f"peers[{i}].rationale", t) for i, _, t in peers] + [
        (f"notable_exclusions[{i}].reason", t) for i, _, t in exclusions
    ]
    problems = [{"field": f, "reason": "slot_markers_not_allowed"} for f, t in texts if "[[" in t or "]]" in t]
    if problems:
        raise _problems("INVALID_ARGUMENTS", problems)
    problems = []
    check_session = commentary.build_check_session(ctx)
    check_session.terms = (*check_session.terms, *candidate_set.terms)
    for field_name, text in texts:
        found = [p.match for p in commentary.check_text(check_session, text, "rationale").problems if p.code == "NAKED_NUMBER"]
        if found:
            problems.append({"field": field_name, "found": found[:5]})
    if problems:
        raise _problems("NAKED_NUMBER", problems)

    # 5. Nutzerwünsche außerhalb der Liste: eindeutig auflösbar und wörtlich in einer Nutzernachricht (E2 A)
    user_additions: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    not_literal: list[dict[str, Any]] = []
    structural: list[dict[str, Any]] = []
    for i, addition in enumerate(additions):
        resolution = ctx.resolve_query(addition.query)
        if resolution.status != "resolved" or resolution.company is None or resolution.match is None:
            unresolved.append({"field": f"user_requested_additions[{i}].query", "reason": resolution.status})
            continue
        company = resolution.company
        if not ctx.is_user_mentioned(addition.query, resolution.match):
            not_literal.append({"field": f"user_requested_additions[{i}].query", "query": addition.query})
            continue
        if company.cik == candidate_set.target_cik or company.cik in seen or any(
            a["cik"] == company.cik for a in user_additions
        ):
            structural.append({"field": f"user_requested_additions[{i}].query", "reason": "target_or_duplicate"})
            continue
        if resolution.match == "ticker_exact":
            ctx.pin_ticker(company.cik, normalize_ticker(addition.query))
        ticker = ctx.ticker_for(company.cik)
        if ticker is None:
            unresolved.append({"field": f"user_requested_additions[{i}].query", "reason": "not_found"})
            continue
        user_additions.append(
            {"cik": company.cik, "ticker": ticker, "name": clean_text(company.name, 80), "query": clean_text(addition.query, 100)}
        )
    if unresolved:
        raise _problems("INVALID_ARGUMENTS", unresolved,
                        "Die Firma ist nicht eindeutig auflösbar; frage den Nutzer nach Ticker oder vollem Namen.")
    if not_literal:
        raise _problems("USER_ADDITION_NOT_IN_MESSAGES", not_literal)
    if structural:
        raise _problems("INVALID_ARGUMENTS", structural)

    proposal_id = ctx.new_handle("proposal")
    peer_entries = [
        {"cik": cik, "ticker": by_cik[cik]["ticker"], "name": by_cik[cik]["name"], "rationale": clean_text(rationale, 300)}
        for _, cik, rationale in peers
    ]
    for old in ctx.proposals.values():
        if not old.confirmed:
            old.superseded = True  # ein neuer Vorschlag macht das alte Handle ungültig
    ctx.proposals[proposal_id] = Proposal(
        id=proposal_id,
        candidate_set_id=candidate_set.id,
        target_cik=candidate_set.target_cik,
        peers=peer_entries,
        exclusions=[
            {"cik": cik, "ticker": by_cik[cik]["ticker"], "name": by_cik[cik]["name"], "reason": clean_text(reason, 300)}
            for _, cik, reason in exclusions
        ],
        user_additions=user_additions,
    )
    ctx.current_proposal_id = proposal_id
    ctx.awaiting_confirmation = True
    ctx.gate_log.append({"event": "proposed", "proposal_id": proposal_id, "candidate_set_id": candidate_set.id,
                         "peers": [p["cik"] for p in peer_entries], "user_additions": [a["cik"] for a in user_additions],
                         "rationale_source": RATIONALE_SOURCE})
    return ProposePeerSetResult(
        proposal_id=proposal_id,
        status="awaiting_human_confirmation",
        peers=peer_entries,
        user_additions=user_additions,
        rationale_source=RATIONALE_SOURCE,
    )


# --- 4 compute_comps_table ---------------------------------------------------------------------------------------


_SKIP_SENTENCES = {
    "de": {"REVENUE_NOT_FOUND": "In den Filings wurde kein Umsatz gefunden.",
           "DATA_NOT_FOUND": "Bei der SEC liegen keine Daten für das Unternehmen vor."},
    "en": {"REVENUE_NOT_FOUND": "No revenue was found in the filings.",
           "DATA_NOT_FOUND": "The SEC has no data for the company."},
}


def _skip_reason(exc: Exception, lang: str = "de") -> tuple[str, str]:
    code = "REVENUE_NOT_FOUND" if isinstance(exc, RevenueNotFoundError) else "DATA_NOT_FOUND"
    return code, _SKIP_SENTENCES[lang][code]


@guarded
def handle_compute_comps_table(ctx: ToolContext, args: ComputeCompsTableInput) -> ComputeCompsTableResult:
    if args.peer_set_id in ctx.proposals:  # bekannter, noch unbestätigter Vorschlag
        raise fail("PEER_SET_NOT_CONFIRMED")
    peer_set = ctx.lookup("peer_set", args.peer_set_id, ctx.peer_sets)

    ctx.deadline.check()
    target = _build_metrics(ctx, peer_set.target_cik)  # Fehler beim Ziel brechen das Tool ab

    peers: list[CompanyMetrics] = []
    skipped: list[dict[str, Any]] = []
    skip_warnings: list[QualityWarning] = []
    for ref in peer_set.peers:
        ctx.deadline.check()
        try:
            peers.append(_build_metrics(ctx, ref.cik))
        except (EdgarDataNotFound, RevenueNotFoundError) as exc:
            # E5 A: fehlende Daten → übersprungen, mit Pflicht-Warnung; Netzfehler (EdgarError sonst) brechen ab.
            code, reason = _skip_reason(exc, ctx.output_language)
            skipped.append({"ticker": ref.ticker, "code": code, "reason": reason})
            skip_warnings.append(
                QualityWarning(
                    severity="warning", company=ref.ticker, affected_field="peer", kind="peer_skipped",
                    params={"code": code}, message=f"Peer {ref.ticker} wurde übersprungen: {reason}",
                )
            )
    if not peers:
        raise fail("NO_VALID_PEERS", skipped_peers=skipped)

    table = build_comps_table(target, peers)
    table = table.model_copy(update={"warnings": [*skip_warnings, *table.warnings]})

    full_warnings = [payloads.full_warning(w, f"W{i}") for i, w in enumerate(table.warnings, start=1)]
    lang = ctx.output_language
    model_warnings = [payloads.model_warning(w, f"W{i}", lang) for i, w in enumerate(table.warnings, start=1)]

    target_ticker = ctx.ticker_for(target.company.cik) or target.company.cik
    target_row = payloads.build_row(target, table.target_multiples, target_ticker, lang)
    peer_rows = [
        payloads.build_row(m, mu, ctx.ticker_for(m.company.cik) or m.company.cik, lang)
        for m, mu in zip(table.peers, table.peer_multiples)
    ]

    def has_ev(mu) -> bool:
        return any(v is not None for v in (mu.ev_revenue, mu.ev_ebitda, mu.ev_ebit))

    severities = {"critical": 0, "warning": 0, "info": 0}
    for w in table.warnings:
        severities[w.severity] += 1
    compact = {
        "basis": {
            "target_period_end": target.period_end,
            "target_fiscal_year": target.fiscal_year,
            "price_as_of": payloads.common_price_date([target_row, *peer_rows]),
            "units": payloads.UNITS,
            "n_peers": len(peer_rows),
            "n_peers_with_ev_multiples": sum(has_ev(mu) for mu in table.peer_multiples),
        },
        "target": target_row,
        "peers": peer_rows,
        "statistics": [payloads.build_statistic(s) for s in table.statistics],
        "warnings": model_warnings,
        "n_warnings_by_severity": severities,
        "skipped_peers": skipped,
    }
    table_id = ctx.new_handle("comps_table")
    ctx.tables[table_id] = StoredTable(
        id=table_id,
        peer_set_id=peer_set.id,
        table=table,
        skipped_peers=skipped,
        warnings=[{"id": f["id"], "full": f, "model": m} for f, m in zip(full_warnings, model_warnings)],
        compact=compact,
    )
    return ComputeCompsTableResult(comps_table_id=table_id, **compact)


# --- 6 get_financials (Allowlist) --------------------------------------------------------------------------------


@guarded
def handle_get_financials(ctx: ToolContext, args: GetFinancialsInput) -> GetFinancialsResult:
    cik = pad_cik(args.cik)
    _require_allowed(ctx, cik)
    metrics = _build_metrics(ctx, cik, args.period, with_price=False)
    wanted = list(dict.fromkeys(args.metrics)) if args.metrics else list(ALL_METRICS)
    values = {m: payloads.metric_entry(metrics, m) for m in wanted}
    result = GetFinancialsResult(
        financials_id=ctx.new_handle("financials"),
        cik=cik,
        ticker=ctx.ticker_for(cik),
        name=clean_text(metrics.company.name, 80),
        period_end=metrics.period_end,
        fiscal_year=metrics.fiscal_year,
        values=values,
        ebit_margin=payloads.pct(metrics.margins.get("ebit_margin")),
        net_margin=payloads.pct(metrics.margins.get("net_margin")),
        revenue_yoy=payloads.pct(metrics.growth_rates.get("revenue_yoy")),
    )
    ctx.financials[result.financials_id] = StoredFinancials(result.financials_id, cik, result.model_dump(mode="json"))
    return result


# --- 7 get_market_data (Allowlist) -------------------------------------------------------------------------------


@guarded
def handle_get_market_data(ctx: ToolContext, args: GetMarketDataInput) -> GetMarketDataResult:
    wanted = normalize_ticker(args.ticker)
    company = next((c for c in ctx.companies() if wanted in c.tickers), None)
    if company is None:
        raise _not_allowed()  # ein unbekannter Ticker verrät nichts anderes als ein nicht freigegebener
    _require_allowed(ctx, company.cik)

    ticker = ctx.ticker_for(company.cik)
    if ticker is None:
        raise _not_allowed()
    quote = _fetch_price(ctx, ticker)

    shares: dict[str, Any] | None = None
    market_cap = None
    reason: str | None = None if quote is not None else "Kein Kurs verfügbar (Anbieter ohne Kurs oder nicht erreichbar)."
    try:
        metrics = _build_metrics(ctx, company.cik, with_price=False)
        fact = next((f for f in metrics.source_facts if f.concept == "EntityCommonStockSharesOutstanding"), None)
        if fact is not None:
            shares = {"value": fact.value, "concept": fact.concept, "accession_number": fact.accession_number}
            if quote is not None and not metrics.is_historical:
                market_cap = quote.price * fact.value
        else:
            reason = (reason + " " if reason else "") + "Aktienanzahl nicht im 10-K enthalten; Marktkapitalisierung nicht berechenbar."
    except (EdgarDataNotFound, RevenueNotFoundError):
        reason = (reason + " " if reason else "") + "Keine 10-K-Daten; Aktienanzahl und Marktkapitalisierung nicht verfügbar."

    result = GetMarketDataResult(
        ticker=ticker,
        price=payloads.price(quote.price) if quote else None,
        price_as_of=quote.as_of_date if quote else None,
        price_source=clean_text(quote.source, 30) if quote else None,
        price_note="letzter Kurs, während US-Handelszeit intraday",
        shares_outstanding_mio=payloads.mio_shares(shares["value"]) if shares else None,
        shares_source={"concept": shares["concept"], "accession_number": shares["accession_number"]} if shares else None,
        market_cap_musd=payloads.musd(market_cap),
        unavailable_reason=reason,
    )
    ctx.market_data[ticker] = result.model_dump(mode="json")
    return result


# --- 5 submit_commentary -----------------------------------------------------------------------------------------


@guarded
def handle_submit_commentary(ctx: ToolContext, args: SubmitCommentaryInput) -> commentary.SubmitCommentaryResult:
    return commentary.submit_commentary(ctx, args)


# --- Registrierung und Dispatcher --------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], BaseModel | ToolError]
    budget_seconds: float
    exclude_none: bool = False


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec(
            "resolve_company",
            "Löst einen Firmennamen oder Ticker deterministisch zu einer SEC-Firma auf. status \"resolved\": genau eine "
            "Firma; \"ambiguous\": Kandidaten zur Rückfrage beim Nutzer; \"not_found\": nach Ticker oder vollem Namen "
            "fragen. Nie raten.",
            ResolveCompanyInput, handle_resolve_company, 20, exclude_none=True,
        ),
        ToolSpec(
            "find_peer_candidates",
            "Liefert die Kandidatenliste für ein aufgelöstes Ziel (gleicher SIC-Code, Umsatz im Größenbereich), sortiert "
            "nach Größenähnlichkeit, höchstens 40. Nur für Firmen, die der Nutzer genannt hat. counts erklärt, was "
            "herausfiel und ob gekürzt wurde.",
            FindPeerCandidatesInput, handle_find_peer_candidates, 90,
        ),
        ToolSpec(
            "propose_peer_set",
            "Reicht die gewählten Peers (nur aus der Kandidatenliste) mit je einer Begründung ohne Zahlen zur "
            "Bestätigung durch den Menschen ein. Das ist das Gate: Danach endet dein Zug.",
            ProposePeerSetInput, handle_propose_peer_set, 20,
        ),
        ToolSpec(
            "compute_comps_table",
            "Berechnet die Comps-Tabelle für ein vom Menschen bestätigtes Peer-Set (peer_set_id).",
            ComputeCompsTableInput, handle_compute_comps_table, 180,
        ),
        ToolSpec(
            "submit_commentary",
            "Reicht den Kommentar zur Comps-Tabelle ein. Zahlen schreibst du nie aus: Wo ein Wert stehen soll, setzt du "
            "einen Slot; die Anwendung setzt Wert, Einheit, Format und Kennzeichnungen ein. Slots: "
            "[[co:<TICKER>:<feld>]] (Wert eines Unternehmens; Felder: revenue, ebit, ebitda, net_income, total_debt, "
            "cash, market_cap, enterprise_value, price, price_as_of, period_end, fiscal_year, ebit_margin, net_margin, "
            "revenue_yoy, ev_revenue, ev_ebitda, ev_ebit, pe), [[stat:<multiple>:<min|median|mean|max|n>]] (Multiple: "
            "ev_revenue, ev_ebitda, ev_ebit, pe), [[basis:<n_peers|n_peers_with_ev_multiples|target_period_end|"
            "target_fiscal_year|price_as_of>]], [[warn:<W-ID>]] (Verweis auf eine Warnung der Tabelle; jede Warnung der "
            "Stufe warning oder critical gehört mit ihrem Marker in den Kommentar), [[fin:<financials_id>:<metrik>]] und "
            "[[mkt:<TICKER>:<feld>]] (Werte aus get_financials und get_market_data). Schreibweise exakt, ohne "
            "Leerzeichen; direkt vor oder nach einem Slot steht keine Ziffer, kein Buchstabe, kein Vorzeichen und kein "
            "zweiter Slot. Auch Jahre, Daten, Quartale, Zahlwörter (zwei), Rangwörter (zweitgrößte) und Vergleiche wie "
            "„doppelt so hoch“ sind Zahlen und nur über Slots erlaubt. Fehlt ein Wert, schreibst du „nicht verfügbar“ "
            "mit dem Grund. Bei einer Abweisung korrigierst du alle Probleme aus details.problems auf einmal; nach "
            "der zweiten erfolglosen Korrektur wird der Kommentar zurückgehalten.",
            SubmitCommentaryInput, handle_submit_commentary, 5,
        ),
        ToolSpec(
            "get_financials",
            "Kennzahlen eines Unternehmens mit Konzept und Accession Number je Wert. Nur für das Ziel, bestätigte Peers "
            "und Firmen, die der Nutzer genannt hat.",
            GetFinancialsInput, handle_get_financials, 45,
        ),
        ToolSpec(
            "get_market_data",
            "Kurs, Aktienanzahl und Marktkapitalisierung. Nur für das Ziel, bestätigte Peers und Firmen, die der Nutzer "
            "genannt hat.",
            GetMarketDataInput, handle_get_market_data, 30,
        ),
    ]
}


def tool_definitions() -> list[dict[str, Any]]:
    """Tool-Liste für die API: deterministisch sortiert (Prompt-Caching), `strict: true`. Enthält weder
    `confirm_peer_set` noch `run_quality_checks` noch `export_deliverable`."""
    return [
        {"name": spec.name, "description": spec.description, "strict": True,
         "input_schema": strict_schema(spec.input_model)}
        for spec in sorted(TOOLS.values(), key=lambda s: s.name)
    ]


@dataclass
class ToolOutcome:
    """Ergebnis eines Tool-Aufrufs: `content` geht als `tool_result` an das Modell, `trace` nur in den Trace."""

    is_error: bool
    content: dict[str, Any]
    trace: dict[str, Any] | None = None

    def to_json(self) -> str:
        return json.dumps(self.content, ensure_ascii=False)


def _error_outcome(ctx: ToolContext, error: ToolError, exc: BaseException | None = None) -> ToolOutcome:
    trace: dict[str, Any] | None = None
    if exc is not None:
        trace = {
            "error_class": type(exc).__name__,
            "endpoint": getattr(exc, "endpoint", None),
            "status_code": getattr(exc, "status_code", None),
            "traceback": ctx.redact("".join(traceback.format_exception(exc))),
        }
    return ToolOutcome(is_error=True, content=error.to_content(), trace=trace)


def _validation_problems(exc: ValidationError) -> list[dict[str, Any]]:
    # nur Ort und Meldung — ohne `input` (könnte Modelltext wiederholen) und ohne Doku-URLs
    return [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors(include_url=False, include_input=False)]


def dispatch(ctx: ToolContext, name: str, raw_args: Any) -> ToolOutcome:
    """Führt einen Tool-Aufruf aus. Jeder `tool_use`-Block bekommt ein Ergebnis — auch unbekannte Tools."""
    spec = TOOLS.get(name)
    if spec is None:
        return _error_outcome(ctx, make_error("UNKNOWN_TOOL"))
    if ctx.awaiting_confirmation:
        return _error_outcome(ctx, make_error("AWAITING_PEER_CONFIRMATION"))

    key = (name, json.dumps(raw_args, sort_keys=True, default=str))
    failures, retryable = ctx.failed_calls.get(key, (0, False))
    if failures and (not retryable or failures >= 2):
        return _error_outcome(ctx, make_error("LOOP_GUARD"))

    def failed(error: ToolError, exc: BaseException | None = None) -> ToolOutcome:
        count, _ = ctx.failed_calls.get(key, (0, False))
        ctx.failed_calls[key] = (count + 1, error.retryable)
        return _error_outcome(ctx, error, exc)

    try:
        args = spec.input_model.model_validate(raw_args if isinstance(raw_args, dict) else {})
    except ValidationError as exc:
        return failed(make_error("INVALID_ARGUMENTS", problems=_validation_problems(exc)))

    ctx.deadline = Deadline(spec.budget_seconds, ctx.clock)
    ctx.last_exception = None
    result = spec.handler(ctx, args)
    if isinstance(result, ToolError):
        return failed(result, ctx.last_exception)
    ctx.failed_calls.pop(key, None)  # der Aufruf funktioniert jetzt — frühere Fehlschläge zählen nicht mehr
    return ToolOutcome(is_error=False, content=result.model_dump(mode="json", exclude_none=spec.exclude_none))

