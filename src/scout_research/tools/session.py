"""L3 — Sitzungsspeicher (`ToolContext`) für die Tool-Handler (Phase-3-Plan, Abschnitt 2 und 5).

Der Speicher hält Ergebnisse **vollständig** (inkl. `source_facts`); das Modell sieht nur kompakte Fassungen. Daten
fließen über opake, typisierte Handles (`cs_`, `pp_`, `ps_`, `ct_`, `fin_`), nie über Zahlen, die das Modell zurückgibt.

Die Host-API `confirm_peer_set` ist **kein Tool**: Sie steht in keiner Tool-Liste und ist nicht über MCP aufrufbar.
Eine Chatnachricht ist nie eine Bestätigung.
"""

from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from scout_research.data.edgar_client import CompanyMetadata, EdgarClient, pad_cik
from scout_research.data.market_provider import MarketDataProvider
from scout_research.domain.models import CompsTable
from scout_research.tools.errors import fail
from scout_research.tools.literal import query_is_literal
from scout_research.tools.resolve import Company, group_by_cik, normalize_ticker, resolve
from scout_research.tools.sanitize import Redactor, clean_text, is_valid_ticker

FACTS_CACHE_SIZE = 16
"""companyfacts je Sitzung als LRU (Plan, „Gemeinsame Clients“)."""

HANDLE_PREFIXES = {
    "candidate_set": "cs",
    "proposal": "pp",
    "peer_set": "ps",
    "comps_table": "ct",
    "financials": "fin",
}
Channel = Literal["cli", "ui", "mcp_elicitation", "eval"]
CHANNELS = ("cli", "ui", "mcp_elicitation", "eval")


class Deadline:
    """Kooperatives Zeitbudget je Tool (Threads sind in Python nicht hart abbrechbar). Die Handler prüfen es
    zwischen den Unternehmen; zusätzlich gelten die httpx-Timeouts."""

    def __init__(self, budget_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._end = clock() + budget_seconds

    def check(self) -> None:
        if self._clock() > self._end:
            raise fail("TOOL_TIMEOUT")


class ConfirmationError(Exception):
    """Die Host-API `confirm_peer_set` lehnt eine Bestätigung ab (Host-Fehler, kein Tool-Fehler)."""

    def __init__(self, reason: str, **details: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details


@dataclass
class Mention:
    query: str
    match: str


@dataclass
class CandidateSet:
    id: str
    target_cik: str
    target: dict[str, Any]
    period_end: str
    candidates: list[dict[str, Any]]
    """Nur die dem Modell gezeigten (sortierten, gekürzten) Kandidaten."""
    terms: list[str]
    """Namen und Ticker des Sets (Ausnahmen des Number-Checks, F18)."""
    warnings: list[dict[str, Any]] = field(default_factory=list)
    """Warnungen der Peer-Suche (`P1`): als Verweis im Text erlaubt."""


@dataclass
class Proposal:
    id: str
    candidate_set_id: str
    target_cik: str
    peers: list[dict[str, Any]]
    exclusions: list[dict[str, Any]]
    user_additions: list[dict[str, Any]]
    superseded: bool = False
    confirmed: bool = False


@dataclass
class PeerRef:
    cik: str
    ticker: str
    name: str


@dataclass
class ConfirmedPeerSet:
    id: str
    proposal_id: str
    target_cik: str
    peers: list[PeerRef]
    removed: list[str]
    added: list[str]
    channel: str
    confirmed_by: str
    confirmed_at: datetime


@dataclass
class StoredTable:
    id: str
    peer_set_id: str
    table: CompsTable
    skipped_peers: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    """Je Warnung: `id`, vollständige Fassung (mit Zahlen) und die Fassung für das Modell."""
    compact: dict[str, Any]


@dataclass
class StoredFinancials:
    id: str
    cik: str
    payload: dict[str, Any]


@dataclass
class CommentaryState:
    """Abweisungen von `submit_commentary` je Comps-Tabelle seit der letzten Nutzernachricht."""

    rejections: int = 0
    withheld: bool = False


@dataclass
class StoredCommentary:
    """Endergebnis für eine Comps-Tabelle: der Host zeigt `rendered`, nie den Modelltext."""

    comps_table_id: str
    status: str
    """accepted | withheld"""
    rendered: str
    raw_text: str | None
    attempts: int
    warnings_addressed: list[str] = field(default_factory=list)
    warnings_not_addressed: list[str] = field(default_factory=list)


class ToolContext:
    def __init__(
        self,
        edgar: EdgarClient,
        market: MarketDataProvider,
        output_language: Literal["de", "en"] = "de",
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        secrets_to_redact: list[str] | tuple[str, ...] = (),
        token_factory: Callable[[], str] = lambda: secrets.token_hex(6),
        eval_session: object | None = None,
    ) -> None:
        self.edgar = edgar
        self.market = market
        self.output_language = output_language
        self.clock = clock
        self.now = now
        self.redact = Redactor(secrets_to_redact)
        self._token_factory = token_factory
        self.eval_session = eval_session
        """Wird erst mit Schritt 8 (`EvalSessionConfig`, nur in `eval/` konstruierbar) belegt; ohne sie lehnt
        `confirm_peer_set` den Kanal `eval` ab."""
        self.deadline = Deadline(10**9, clock)

        self._user_messages: list[str] = []
        self.mentions: dict[str, list[Mention]] = {}
        self.candidate_sets: dict[str, CandidateSet] = {}
        self.proposals: dict[str, Proposal] = {}
        self.current_proposal_id: str | None = None
        self.peer_sets: dict[str, ConfirmedPeerSet] = {}
        self.tables: dict[str, StoredTable] = {}
        self.financials: dict[str, StoredFinancials] = {}
        self.market_data: dict[str, dict[str, Any]] = {}
        """Jüngstes `get_market_data`-Ergebnis je Ticker (Slots `mkt:`)."""
        self.resolved_names: dict[str, str] = {}
        self.known_sic: set[str] = set()
        self.commentary_state: dict[str, CommentaryState] = {}
        self.commentaries: dict[str, StoredCommentary] = {}
        self.commentary_log: list[dict[str, Any]] = []
        self.awaiting_confirmation = False
        self.failed_calls: dict[tuple[str, str], tuple[int, bool]] = {}
        self.last_exception: BaseException | None = None
        self.gate_log: list[dict[str, Any]] = []

        self._companies: list[Company] | None = None
        self._by_cik: dict[str, Company] = {}
        self._pinned_tickers: dict[str, str] = {}
        self._metadata: dict[str, CompanyMetadata] = {}
        self._facts: OrderedDict[str, dict] = OrderedDict()

    # --- Host-Eingang ------------------------------------------------------------------------------------

    def record_user_message(self, text: str) -> None:
        """Der Host protokolliert hier jede Nachricht, die der Mensch eingegeben hat — und nichts sonst (keine
        Tool-Ergebnisse, keine Host-Nachrichten). Eine neue Nutzernachricht hebt die Gate-Sperre auf und lässt
        Wiederholungen früher gescheiterter Aufrufe wieder zu (der Kontext hat sich geändert)."""
        self._user_messages.append(text)
        self.awaiting_confirmation = False
        self.failed_calls.clear()
        self.commentary_state.clear()  # neue Anfrage: neue Korrekturrunden für den Kommentar

    @property
    def user_messages(self) -> tuple[str, ...]:
        return tuple(self._user_messages)

    def is_user_mentioned(self, query: str, match: str) -> bool:
        return query_is_literal(query, match, self._user_messages)

    # --- Handles -----------------------------------------------------------------------------------------

    def new_handle(self, kind: str) -> str:
        return f"{HANDLE_PREFIXES[kind]}_{self._token_factory()}"

    def lookup(self, kind: str, handle: str, store: dict[str, Any]) -> Any:
        """Handle → Objekt. Falscher Typ oder unbekannt → `UNKNOWN_HANDLE` mit dem erwarteten Typ im Hinweis."""
        prefix = HANDLE_PREFIXES[kind]
        if handle in store:
            return store[handle]
        label = {"candidate_set": "candidate_set_id (cs_…)", "proposal": "proposal_id (pp_…)",
                 "peer_set": "peer_set_id (ps_…)", "comps_table": "comps_table_id (ct_…)",
                 "financials": "financials_id (fin_…)"}[kind]
        hint = f"Erwartet wird eine {label} aus einem Tool-Ergebnis dieser Sitzung."
        if not handle.startswith(prefix + "_"):
            hint += " Das übergebene Handle hat einen anderen Typ."
        raise fail("UNKNOWN_HANDLE", hint, expected=f"{prefix}_…")

    # --- Firmenverzeichnis und Ticker --------------------------------------------------------------------

    def companies(self) -> list[Company]:
        if self._companies is None:
            self._companies = group_by_cik(self.edgar.list_companies())
            self._by_cik = {c.cik: c for c in self._companies}
        return self._companies

    def company(self, cik: str) -> Company | None:
        self.companies()
        return self._by_cik.get(pad_cik(cik))

    def resolve_query(self, query: str):
        return resolve(query, self.companies())

    def pin_ticker(self, cik: str, ticker: str) -> None:
        """Der erste Ticker, der in der Sitzung für eine CIK verwendet wird, bleibt (F9)."""
        if is_valid_ticker(ticker):
            self._pinned_tickers.setdefault(pad_cik(cik), ticker)

    def ticker_for(self, cik: str) -> str | None:
        """Der **eine** Ticker der Sitzung für diese CIK — für Kursabfrage, Slots und Warnungen."""
        cik = pad_cik(cik)
        pinned = self._pinned_tickers.get(cik)
        if pinned is not None:
            return pinned
        company = self.company(cik)
        if company is None:
            return None
        for ticker in company.tickers:
            if is_valid_ticker(ticker):
                self._pinned_tickers[cik] = ticker
                return ticker
        return None

    # --- Datenzugriff mit Zwischenspeicher ---------------------------------------------------------------

    def metadata(self, cik: str) -> CompanyMetadata:
        """Metadaten mit dem Sitzungs-Ticker als erstem Eintrag von `tickers`: So nutzen L2-Warnungen und Multiples
        denselben Ticker wie Kurs und Slots, ohne dass L2 etwas davon weiß."""
        cik = pad_cik(cik)
        if cik not in self._metadata:
            self._metadata[cik] = self.edgar.get_company_metadata(cik)
        metadata = self._metadata[cik]
        ticker = self.ticker_for(cik)
        if ticker is None:
            return metadata
        return metadata.model_copy(update={"tickers": [ticker, *[t for t in metadata.tickers if t != ticker]]})

    def facts(self, cik: str) -> dict:
        cik = pad_cik(cik)
        if cik in self._facts:
            self._facts.move_to_end(cik)
            return self._facts[cik]
        data = self.edgar.get_company_facts(cik)
        self._facts[cik] = data
        while len(self._facts) > FACTS_CACHE_SIZE:
            self._facts.popitem(last=False)
        return data

    # --- Allowlist ---------------------------------------------------------------------------------------

    def note_resolved(self, cik: str, query: str, match: str) -> None:
        self.mentions.setdefault(pad_cik(cik), []).append(Mention(query, match))

    def allow_reason(self, cik: str) -> str | None:
        """Allowlist für `get_financials`/`get_market_data`/Ziel der Peer-Suche (Plan, Abschnitt 2):
        (b) Firma in einem bestätigten Peer-Set, (c) Firma, die per `resolve_company` aufgelöst wurde und deren
        Anfrage wörtlich in einer Nutzernachricht steht."""
        cik = pad_cik(cik)
        if any(cik == p.cik for ps in self.peer_sets.values() for p in ps.peers):
            return "confirmed_peer"
        if any(self.is_user_mentioned(m.query, m.match) for m in self.mentions.get(cik, [])):
            return "user_named"
        return None

    # --- Host-API: Gate ----------------------------------------------------------------------------------

    def confirm_peer_set(
        self,
        proposal_id: str,
        keep_ciks: list[str],
        add_queries: list[str] | tuple[str, ...] = (),
        channel: str = "cli",
        confirmed_by: str = "human",
    ) -> ConfirmedPeerSet:
        """Bestätigt den **jüngsten** Vorschlag — nur der Mensch über einen für Menschen gedachten Kanal.

        `keep_ciks` müssen aus dem Vorschlag stammen (Peers oder Nutzerergänzungen); `add_queries` sind vom Menschen
        eingegebene Ergänzungen und werden deterministisch aufgelöst (mehrdeutig/unbekannt → `ConfirmationError`,
        der Host fragt zurück). Ergebnis ist das bestätigte Peer-Set mit seinem `ps_`-Handle."""
        if channel not in CHANNELS:
            raise ConfirmationError("unknown_channel", channel=channel)
        if channel == "eval" and self.eval_session is None:
            raise ConfirmationError("eval_channel_requires_eval_session")
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise ConfirmationError("unknown_proposal")
        if proposal.confirmed:
            raise ConfirmationError("already_confirmed")
        if proposal.superseded or proposal_id != self.current_proposal_id:
            raise ConfirmationError("proposal_superseded")

        offered = {p["cik"]: p for p in [*proposal.peers, *proposal.user_additions]}
        keep = [pad_cik(c) for c in keep_ciks]
        unknown = [c for c in keep if c not in offered]
        if unknown:
            raise ConfirmationError("keep_not_in_proposal", ciks=unknown)
        if len(set(keep)) != len(keep):
            raise ConfirmationError("duplicate_peers")

        peers = [PeerRef(cik=c, ticker=offered[c]["ticker"], name=offered[c]["name"]) for c in keep]
        added: list[str] = []
        for query in add_queries:
            resolution = self.resolve_query(query)
            if resolution.status != "resolved" or resolution.company is None:
                raise ConfirmationError(
                    resolution.status,
                    query=query,
                    candidates=[{"cik": c.company.cik, "name": c.company.name, "tickers": c.company.tickers}
                                for c in resolution.candidates],
                )
            company = resolution.company
            if company.cik == proposal.target_cik:
                raise ConfirmationError("target_cannot_be_peer", query=query)
            if any(p.cik == company.cik for p in peers):
                raise ConfirmationError("duplicate_peers", query=query)
            if resolution.match == "ticker_exact":
                self.pin_ticker(company.cik, normalize_ticker(query))
            ticker = self.ticker_for(company.cik)
            if ticker is None:
                raise ConfirmationError("no_valid_ticker", query=query)
            peers.append(PeerRef(cik=company.cik, ticker=ticker, name=clean_text(company.name)))
            added.append(company.cik)
        if not peers:
            raise ConfirmationError("empty_peer_set")

        proposed = {p["cik"] for p in proposal.peers}
        peer_set = ConfirmedPeerSet(
            id=self.new_handle("peer_set"),
            proposal_id=proposal_id,
            target_cik=proposal.target_cik,
            peers=peers,
            removed=[c for c in proposed if c not in {p.cik for p in peers}],
            added=added,
            channel=channel,
            confirmed_by=confirmed_by,
            confirmed_at=self.now(),
        )
        self.peer_sets[peer_set.id] = peer_set
        proposal.confirmed = True
        self.awaiting_confirmation = False
        self.failed_calls.clear()
        self.gate_log.append(
            {"event": "confirmed", "proposal_id": proposal_id, "peer_set_id": peer_set.id, "channel": channel,
             "confirmed_by": confirmed_by, "confirmed_at": peer_set.confirmed_at.isoformat(),
             "proposed": sorted(proposed), "confirmed": [p.cik for p in peers], "removed": peer_set.removed,
             "added": added}
        )
        return peer_set
