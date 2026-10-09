"""L3 — `submit_commentary`: Prüfung, Slot-Rendering und Pflichtbestandteile (Phase-3-Plan, Abschnitt 11; Schritt 6).

Ablauf einer Einreichung: Der Modelltext wird normalisiert (`numbercheck`), Slots werden aufgelöst (`slots`), alles
übrige wird nach nackten Zahlen abgesucht. Es gibt **alle** Probleme auf einmal zurück, damit eine Korrekturrunde
reicht. Bei Erfolg setzt der Code die Werte ein und hängt die festen Bestandteile an (Basiszeile, Fußnoten, nicht
angesprochene Warnungen); angezeigt wird nie der Modelltext. Zwei Korrekturrunden: bei der dritten Abweisung wird der
Kommentar zurückgehalten.

`check_text` ist dieselbe Prüfung für jeden anderen Modelltext (Rückfragen, Einleitung, Antworten); sie gilt seit
Entscheidung O1 B in jeder Sitzung, mit oder ohne Tabelle. Profile: `slot_text` (Slots erlaubt, Zahlen nur dort) und
`rationale` (Peer-Begründungen: weder Slots noch Zahlen).
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel

from scout_research.tools import payloads
from scout_research.tools.errors import fail
from scout_research.tools.numbercheck import (
    Identifiers,
    Normalized,
    exempt_spans,
    has_trigger,
    mask,
    normalize,
    scan_numbers,
)
from scout_research.tools.schemas import SubmitCommentaryInput
from scout_research.tools.session import CommentaryState, StoredCommentary, StoredTable, ToolContext
from scout_research.tools.slots import (
    CheckSession,
    SlotFail,
    SlotOk,
    fmt_date,
    fmt_value,
    resolve_slot,
    ticker_pattern,
)

Profile = Literal["slot_text", "rationale"]
MAX_REJECTIONS = 3
"""Bei der dritten Abweisung (zwei Korrekturrunden, Q2) wird der Kommentar zurückgehalten."""
MARK_ORDER = "*†‡§"
REQUIRED_SEVERITIES = ("warning", "critical")
_CODE_ORDER = ("SLOT_UNKNOWN", "SLOT_VALUE_UNAVAILABLE", "NAKED_NUMBER", "INVALID_ARGUMENTS")


# --- Ergebnisse der Prüfung --------------------------------------------------------------------------------------


@dataclass
class Problem:
    code: str
    kind: str
    start: int
    end: int
    """Bereich im **Rohtext**, wie ihn das Modell geschrieben hat."""
    match: str
    hint: str
    reason: str | None = None

    def as_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"code": self.code, "kind": self.kind, "match": self.match[:80], "hint": self.hint}
        if self.reason:
            detail["reason"] = self.reason
        return detail


@dataclass
class CheckResult:
    problems: list[Problem]
    body: str | None
    """Gerenderter Text (Slots ersetzt); `None`, wenn es Probleme gibt oder das Profil keine Slots kennt."""
    marks: list[str] = field(default_factory=list)
    slots: list[dict[str, str]] = field(default_factory=list)
    """Je Slot: Rohtext und Feldpfad (Provenance, Trace)."""
    warning_markers: set[str] = field(default_factory=set)
    format_chars: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems

    def primary_code(self) -> str:
        return min((p.code for p in self.problems), key=_CODE_ORDER.index)


# --- Slot-Konstrukte im Text -------------------------------------------------------------------------------------


@dataclass
class Construct:
    a: int
    b: int
    kind: str
    """slot | nested | unclosed | stray | alt"""
    inner: str = ""


_ALT_SINGLE = re.compile(r"\[(?:co|stat|basis|warn|fin|mkt)\s*:[^\[\]]*\]", re.IGNORECASE)
_ALT_BRACES = re.compile(r"\{\{[^{}]*\}\}")


def find_constructs(norm: str) -> list[Construct]:
    found: list[Construct] = []
    stack: list[list[Any]] = []
    i, n = 0, len(norm)
    while i < n - 1:
        pair = norm[i: i + 2]
        if pair == "[[":
            if stack:
                stack[-1][1] = True
            stack.append([i, False])
            i += 2
        elif pair == "]]":
            if stack:
                a, nested = stack.pop()
                if not stack:
                    found.append(Construct(a, i + 2, "nested" if nested else "slot", norm[a + 2: i]))
            else:
                found.append(Construct(i, i + 2, "stray"))
            i += 2
        else:
            i += 1
    if stack:
        a = stack[0][0]
        end = a + 2
        while end < n and not norm[end].isspace():
            end += 1
        found.append(Construct(a, end, "unclosed"))
    covered = [(c.a, c.b) for c in found]
    for pattern in (_ALT_SINGLE, _ALT_BRACES):
        for m in pattern.finditer(norm):
            if not any(a <= m.start() < b for a, b in covered):
                found.append(Construct(m.start(), m.end(), "alt"))
    return sorted(found, key=lambda c: c.a)


# --- Prüfung -----------------------------------------------------------------------------------------------------

_HINTS = {
    "digits": "Schreibe keine Zahl aus (auch Jahre, Daten und Quartale nicht); setze einen Slot oder lass sie weg.",
    "number_word": "Ein Zahlwort ist eine Zahl; nenne stattdessen die Unternehmen oder setze einen Slot.",
    "magnitude_word": "Größenordnungen wie „Milliarden“ sind Mengenangaben; setze einen Slot oder lass sie weg.",
    "ordinal": "Ränge wie „zweitgrößte“ sind gezählt; formuliere ohne Rang oder setze einen Slot.",
    "fraction_word": "Anteile in Worten sind Rechnungen; formuliere ohne Anteil.",
    "multiplicative": "Vergleiche wie „doppelt so hoch“ sind Rechnungen; formuliere ohne Faktor.",
}
_SYNTAX_HINT = ("Slots haben die Form [[co:<TICKER>:<feld>]], [[stat:<multiple>:<aggregat>]], [[basis:<feld>]], "
                "[[warn:<W-ID>]], [[fin:<financials_id>:<metrik>]] oder [[mkt:<TICKER>:<feld>]]: doppelte eckige "
                "Klammern, Doppelpunkte, keine Leerzeichen und nicht verschachtelt.")
_FORBIDDEN_BEFORE = set("+-−–—±~≈≥≤<>=$€£#&")
_FORBIDDEN_AFTER = set("%$€£°^²³")


@lru_cache(maxsize=64)
def _trigger_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(t for t in dict.fromkeys(terms) if t and has_trigger(t))



def _adjacency_problems(norm: str, valid: list[tuple[Construct, SlotOk]]) -> list[tuple[int, int, str]]:
    """Slots dürfen mit ihrer Umgebung keine neue Zahl bilden (Plan 11.4): zwei Slots ohne Trennzeichen
    („44“ aus zweimal „4“), Ziffern oder Vorzeichen davor/danach."""
    out: list[tuple[int, int, str]] = []
    paired_before: set[int] = set()
    paired_after: set[int] = set()
    for i in range(len(valid) - 1):
        left, right = valid[i][0], valid[i + 1][0]
        if re.fullmatch(r"[ \t ]*", norm[left.b: right.a]):
            out.append((left.a, right.b, "Zwischen zwei Slots muss ein Zeichen stehen, das kein Leerraum ist."))
            paired_after.add(i)
            paired_before.add(i + 1)
    for i, (c, _) in enumerate(valid):
        if i not in paired_before and c.a > 0:
            ch = norm[c.a - 1]
            if ch.isalnum() or ch in _FORBIDDEN_BEFORE:
                out.append((c.a - 1, c.b, "Vor einem Slot darf kein Buchstabe, keine Ziffer und kein Vorzeichen stehen."))
        if i not in paired_after and c.b < len(norm):
            ch = norm[c.b]
            if ch.isalnum() or ch in _FORBIDDEN_AFTER:
                out.append((c.a, c.b + 1, "Nach einem Slot darf kein Buchstabe, keine Ziffer und kein Prozentzeichen stehen."))
    return out


def check_text(session: CheckSession, text: str, profile: Profile = "slot_text") -> CheckResult:
    n = normalize(text)
    norm = n.norm
    problems: list[Problem] = []

    def add(code: str, kind: str, a: int, b: int, hint: str, reason: str | None = None) -> None:
        ra, rb = n.raw_span(a, b)
        problems.append(Problem(code, kind, ra, rb, text[ra:rb], hint, reason))

    constructs = find_constructs(norm)
    valid: list[tuple[Construct, SlotOk]] = []
    invalid_spans: list[tuple[int, int]] = []
    if profile == "rationale":
        for c in constructs:
            add("INVALID_ARGUMENTS", "slot_markers_not_allowed", c.a, c.b, "In Begründungen sind weder Slots noch Zahlen erlaubt.")
    else:
        for c in constructs:
            outcome = resolve_slot(c.inner, session) if c.kind == "slot" else SlotFail("SLOT_UNKNOWN", c.kind, _SYNTAX_HINT)
            if isinstance(outcome, SlotOk):
                valid.append((c, outcome))
            else:
                invalid_spans.append((c.a, c.b))
                add(outcome.code, outcome.kind, c.a, c.b, outcome.hint, outcome.reason)
        for a, b, hint in _adjacency_problems(norm, valid):
            add("SLOT_UNKNOWN", "slot_adjacent", a, b, hint)

    exempt = exempt_spans(norm, session.warning_ids, session.identifiers, _trigger_terms(tuple(session.terms)))
    masked = mask(norm, [*exempt, *[(c.a, c.b) for c, _ in valid]])
    for finding in scan_numbers(masked, invalid_spans):
        add("NAKED_NUMBER", finding.kind, finding.start, finding.end, _HINTS[finding.kind])
    problems.sort(key=lambda p: (p.start, p.end, p.code))

    slots = [{"slot": text[n.raw_span(c.a, c.b)[0]: n.raw_span(c.a, c.b)[1]], "path": ok.path} for c, ok in valid]
    markers = {ok.warning_id for _, ok in valid if ok.warning_id}
    if problems or profile != "slot_text":
        return CheckResult(problems, None, slots=slots, warning_markers=markers, format_chars=n.format_chars)
    body, marks = _render_body(n, valid, session)
    return CheckResult([], body, marks, slots, markers, n.format_chars)


def _render_body(n: Normalized, valid: list[tuple[Construct, SlotOk]], session: CheckSession) -> tuple[str, list[str]]:
    lang = session.language
    outside = mask(n.norm, [(c.a, c.b) for c, _ in valid])
    bounds = [(m.start(), m.end()) for m in re.finditer(r"[.!?](?=\s|$)|\n", outside)]
    ends = [e for _, e in bounds]
    starts = [s for s, _ in bounds]
    used: set[str] = set()
    pieces: list[str] = []
    pos = 0
    for c, ok in valid:
        lo = ends[bisect.bisect_right(ends, c.a) - 1] if bisect.bisect_right(ends, c.a) else 0
        k = bisect.bisect_left(starts, c.b)
        hi = starts[k] if k < len(starts) else len(outside)
        sentence = outside[lo:hi]
        text = ok.text + ok.marks
        used.update(ok.marks)
        extras: list[str] = []
        if ok.firm and not re.search(ticker_pattern(ok.firm), sentence):
            extras.append(ok.firm)
        if ok.price_date:
            extras.append(("Kurs vom " if lang == "de" else "price as of ") + fmt_date(ok.price_date, lang))
        if extras:
            text += f" ({', '.join(extras)})"
        ca, cb = n.clean_span(c.a, c.b)
        pieces += [n.clean[pos:ca], text]
        pos = cb
    pieces.append(n.clean[pos:])
    return "".join(pieces), [m for m in MARK_ORDER if m in used]


# --- Sitzungssicht für die Prüfung ------------------------------------------------------------------------------


def exempt_terms(strings: list[str | None]) -> list[str]:
    """Namen und Ticker der Sitzung, die selbst nach Zahlen aussehen („3M“, „1-800-Flowers“, „Five Below“): Sie sind
    Namen aus Tool-Ergebnissen, keine Mengen (Review F18). Ausgenommen wird der ganze Name, ein Einzelwort mit Ziffer
    („3M“) und jede Wortfolge aus mindestens zwei Wörtern, die einen Zahlwort-Treffer enthält („Five Below“). Ein
    einzelnes Zahlwort („five“) wird **nie** ausgenommen — sonst schaltete der Name einer Firma („Five Below“, Ticker
    FIVE) das Zahlwort im ganzen Text frei."""
    terms: list[str] = []
    for text in strings:
        if not text:
            continue
        terms.append(text)
        words = [w.strip(".,;:()") for w in text.split()]
        terms += [w for w in words if any(ch.isdigit() for ch in w)]
        terms += [" ".join(words[i:j]) for i in range(len(words)) for j in range(i + 2, len(words) + 1)
                  if has_trigger(" ".join(words[i:j]))]
    return [t for t in dict.fromkeys(terms) if t and has_trigger(t)]


def build_check_session(ctx: ToolContext, stored: StoredTable | None = None) -> CheckSession:
    """Die Sitzungsdaten, gegen die ein Modelltext geprüft wird. Ohne `stored` gilt die jüngste Tabelle der Sitzung."""
    if stored is None and ctx.tables:
        stored = next(reversed(ctx.tables.values()))
    table = stored.compact if stored is not None else None
    warning_ids = {w["id"] for w in table["warnings"]} if table else set()
    ciks: set[str] = set(ctx.mentions)
    sics: set[str] = set(ctx.known_sic)
    names: list[str | None] = list(ctx.resolved_names.values())
    for cs in ctx.candidate_sets.values():
        warning_ids |= {w["id"] for w in cs.warnings}
        ciks |= {cs.target_cik, *[c["cik"] for c in cs.candidates]}
        sics |= {x for x in [cs.target.get("sic_code"), *[c.get("sic_code") for c in cs.candidates]] if x}
        names += cs.terms
    if table:
        for row in [table["target"], *table["peers"]]:
            ciks.add(row["cik"])
            names += [row["name"], row["ticker"]]
    accessions: set[str] = set()
    for fin in ctx.financials.values():
        ciks.add(fin.cik)
        for entry in fin.payload.get("values", {}).values():
            accessions |= {a for a in str(entry.get("accession_number", "")).split("+") if a}
    for result in ctx.market_data.values():
        src = result.get("shares_source") or {}
        accessions |= {a for a in str(src.get("accession_number", "")).split("+") if a}
    return CheckSession(
        language=ctx.output_language,
        table=table,
        table_id=stored.id if stored else None,
        financials={fid: f.payload for fid, f in ctx.financials.items()},
        market=dict(ctx.market_data),
        warning_ids=frozenset(warning_ids),
        terms=tuple(exempt_terms(names)),
        identifiers=Identifiers(frozenset(ciks), frozenset(sics), frozenset(accessions)),
    )


# --- Feste Bestandteile (vom Code erzeugt, nie vom Modell) -------------------------------------------------------

_MULTIPLE_LABELS = {"ev_revenue": ("EV/Umsatz", "EV/Revenue"), "ev_ebitda": ("EV/EBITDA", "EV/EBITDA"),
                    "ev_ebit": ("EV/EBIT", "EV/EBIT"), "pe": ("P/E", "P/E")}
_TEXTS = {
    "de": {
        "basis": "Basis: {target} (Ziel), GJ {fy}, Periodenende {end}; Kurse {prices}; {n} Peers, davon {m} mit EV-Multiples.",
        "prices_same": "vom {date}", "prices_mixed": "von verschiedenen Tagen",
        "skipped": " Übersprungen: {tickers}.",
        "fn_*": "* EBITDA angenähert als EBIT + D&A.",
        "fn_†": "† Enthält Untergrenzen (wahrer Wert höher): {tickers}.",
        "fn_‡": "‡ Nicht in der Statistik enthalten: {items}.",
        "fn_‡_item": "{multiple} ohne {tickers}{reason}",
        "fn_§": "§ Die Kurse stammen von verschiedenen Tagen.",
        "unaddressed": "Im Kommentar nicht angesprochene Warnungen (vom System ergänzt):",
        "withheld": ("Kommentar zurückgehalten: Er enthielt wiederholt Zahlen oder Slots, die sich nicht prüfen ließen. "
                     "Angezeigt werden die Tabelle und die Warnungen."),
        "warnings": "Warnungen:",
        "target": "Ziel", "stat": {"min": "Min", "median": "Median", "mean": "Mittel", "max": "Max"},
        "no_warnings": "Keine Warnungen.",
    },
    "en": {
        "basis": "Basis: {target} (target), FY {fy}, period end {end}; prices {prices}; {n} peers, {m} with EV multiples.",
        "prices_same": "as of {date}", "prices_mixed": "from different dates",
        "skipped": " Skipped: {tickers}.",
        "fn_*": "* EBITDA approximated as EBIT + D&A.",
        "fn_†": "† Includes lower bounds (true value higher): {tickers}.",
        "fn_‡": "‡ Not included in the statistic: {items}.",
        "fn_‡_item": "{multiple} without {tickers}{reason}",
        "fn_§": "§ The prices are from different dates.",
        "unaddressed": "Warnings not addressed in the commentary (added by the system):",
        "withheld": ("Commentary withheld: it repeatedly contained numbers or slots that could not be verified. The table "
                     "and the warnings are shown instead."),
        "warnings": "Warnings:",
        "target": "Target", "stat": {"min": "Min", "median": "Median", "mean": "Mean", "max": "Max"},
        "no_warnings": "No warnings.",
    },
}


def basis_line(table: dict[str, Any], lang: str) -> str:
    """Feste Basiszeile (O5 B): Ziel, Periode, Kursdatum, Anzahl Peers — vom Code aus der Tabelle."""
    t = _TEXTS[lang]
    basis = table["basis"]
    prices = (t["prices_same"].format(date=fmt_date(basis["price_as_of"], lang)) if basis.get("price_as_of")
              else t["prices_mixed"])
    line = t["basis"].format(target=table["target"]["ticker"], fy=basis["target_fiscal_year"],
                             end=fmt_date(basis["target_period_end"], lang), prices=prices, n=basis["n_peers"],
                             m=basis["n_peers_with_ev_multiples"])
    skipped = [s["ticker"] for s in table.get("skipped_peers", [])]
    return line + (t["skipped"].format(tickers=", ".join(skipped)) if skipped else "")


def _footnotes(table: dict[str, Any], marks: list[str], used_stats: list[str], lang: str) -> list[str]:
    t = _TEXTS[lang]
    idx = 0 if lang == "de" else 1
    lines: list[str] = []
    for mark in marks:
        if mark == "†":
            tickers = sorted({x for s in table["statistics"] if s["multiple"] in used_stats for x in s["lower_bound"]})
            lines.append(t["fn_†"].format(tickers=", ".join(tickers)))
        elif mark == "‡":
            rows = {r["ticker"]: r for r in [table["target"], *table["peers"]]}
            items = []
            for s in table["statistics"]:
                if s["multiple"] in used_stats and s["excluded"]:
                    why = {rows[x]["excluded"].get(s["multiple"]) for x in s["excluded"] if x in rows} - {None}
                    reason = f" ({'; '.join(sorted(why))})" if why else ""
                    items.append(t["fn_‡_item"].format(multiple=_MULTIPLE_LABELS[s["multiple"]][idx],
                                                       tickers=", ".join(s["excluded"]), reason=reason))
            lines.append(t["fn_‡"].format(items="; ".join(items)))
        else:
            lines.append(t[f"fn_{mark}"])
    return lines


def _warning_line(warning: dict[str, Any], lang: str) -> str:
    who = warning["company"] if not (warning["company"] == "Peer-Set" and lang == "en") else "peer set"
    return f"- [{warning['id']}: {who}] ({warning['severity']}) {payloads.warning_text(warning.get('kind'), warning.get('params', {}), lang)}"


def assemble_commentary(table: dict[str, Any], result: CheckResult, missing: list[str], lang: str) -> str:
    """Basiszeile, gerenderter Text, Fußnoten und der Block nicht angesprochener Pflichtwarnungen (O4 D)."""
    t = _TEXTS[lang]
    used_stats = [m.group(1) for s in result.slots if (m := re.match(r"statistics\[(\w+)\]", s["path"]))]
    parts = [basis_line(table, lang), "", result.body or ""]
    notes = _footnotes(table, result.marks, used_stats, lang)
    if notes:
        parts += ["", *notes]
    if missing:
        by_id = {w["id"]: w for w in table["warnings"]}
        parts += ["", t["unaddressed"], *[_warning_line(by_id[i], lang) for i in missing]]
    return "\n".join(parts)


def _cell(row: dict[str, Any], name: str, lang: str) -> str:
    value = row.get(name)
    if value is None:
        return "–"
    kind = "money" if name in ("revenue", "ebit", "ebitda", "enterprise_value") else "multiple"
    text = fmt_value(kind, value, lang)
    if (name == "enterprise_value" or name.startswith("ev_")) and row.get("ev_is_lower_bound"):
        text = "≥ " + text
    return text + ("*" if name in ("ebitda", "ev_ebitda") and row.get("ebitda_approximated", True) else "")


def render_withheld(table: dict[str, Any], lang: str) -> str:
    """Hinweis „Kommentar zurückgehalten“, deterministisch gerenderte Tabelle und Warnungsliste (Plan, Abschnitt 3)."""
    t = _TEXTS[lang]
    columns = ["revenue", "ebit", "ebitda", "enterprise_value", "ev_revenue", "ev_ebitda", "ev_ebit", "pe"]
    head = ["Ticker", "Periodenende" if lang == "de" else "Period end", *columns]
    lines = [t["withheld"], "", basis_line(table, lang), "", "| " + " | ".join(head) + " |",
             "|" + "---|" * len(head)]
    for label, row in [(f"{table['target']['ticker']} ({t['target']})", table["target"]), *[(p["ticker"], p) for p in table["peers"]]]:
        lines.append("| " + " | ".join([label, fmt_date(row["period_end"], lang), *[_cell(row, c, lang) for c in columns]]) + " |")
    for agg in ("min", "median", "mean", "max"):
        cells = {s["multiple"]: (fmt_value("multiple", s[agg], lang) + f" (n = {s['n']})") if s.get(agg) is not None else "–"
                 for s in table["statistics"]}
        lines.append("| " + " | ".join([t["stat"][agg], "", "", "", "", "", *[cells.get(m, "–") for m in ("ev_revenue", "ev_ebitda", "ev_ebit", "pe")]]) + " |")
    lines += ["", t["warnings"], *([_warning_line(w, lang) for w in table["warnings"]] or [t["no_warnings"]])]
    return "\n".join(lines)


# --- submit_commentary ------------------------------------------------------------------------------------------


class SubmitCommentaryResult(BaseModel):
    status: str
    slots_used: int
    warnings_addressed: list[str]
    warnings_not_addressed: list[str]


def submit_commentary(ctx: ToolContext, args: SubmitCommentaryInput) -> SubmitCommentaryResult:
    """Prüft und rendert den Kommentar zu einer Comps-Tabelle. Abweisungen sind normale Tool-Fehler; die dritte
    Abweisung hält den Kommentar zurück (`COMMENTARY_WITHHELD`)."""
    stored = ctx.lookup("comps_table", args.comps_table_id, ctx.tables)
    state = ctx.commentary_state.setdefault(stored.id, CommentaryState())
    if state.withheld:
        raise fail("COMMENTARY_WITHHELD")
    session = build_check_session(ctx, stored)
    result = check_text(session, args.text, "slot_text")
    attempt = state.rejections + 1
    log: dict[str, Any] = {"event": "commentary", "comps_table_id": stored.id, "attempt": attempt, "raw_text": args.text,
                           "slots": result.slots, "problems": [p.as_detail() for p in result.problems],
                           "format_chars": result.format_chars}
    ctx.commentary_log.append(log)

    if result.problems:
        state.rejections += 1
        if state.rejections >= MAX_REJECTIONS:
            state.withheld = True
            log["action"] = "withheld"
            ctx.commentaries[stored.id] = StoredCommentary(
                stored.id, "withheld", render_withheld(stored.compact, session.language), args.text, attempt)
            raise fail("COMMENTARY_WITHHELD")
        log["action"] = "rejected"
        raise fail(result.primary_code(), problems=[p.as_detail() for p in result.problems[:25]])

    table = stored.compact
    required = [w["id"] for w in table["warnings"] if w["severity"] in REQUIRED_SEVERITIES]
    addressed = [i for i in (w["id"] for w in table["warnings"]) if i in result.warning_markers]
    missing = [i for i in required if i not in result.warning_markers]
    rendered = assemble_commentary(table, result, missing, session.language)
    log.update(action="accepted" if attempt == 1 else "accepted_after_n", warnings_addressed=addressed,
               warnings_not_addressed=missing, rendered=rendered)
    ctx.commentaries[stored.id] = StoredCommentary(stored.id, "accepted", rendered, args.text, attempt, addressed, missing)
    return SubmitCommentaryResult(status="accepted", slots_used=len(result.slots), warnings_addressed=addressed,
                                  warnings_not_addressed=missing)

