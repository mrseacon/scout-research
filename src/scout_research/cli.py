"""Kommandozeile `scout-research` (TA-14): `chat --scripted` führt eine komplette Analyse gegen die Demo-Welt und das feste
Modellskript (`scout_research.demo`) vor. Einen Live-Modus gibt es nicht (Stufe C): ohne `--scripted` endet der Aufruf mit einem
festen Text, ohne einen SDK-Client zu bauen.

Das Nutzerprotokoll schreibt nur diese Eingabeschicht: Text des Menschen geht als `HumanInput` an `loop.run_turn`, die
Entscheidung über den Vorschlag nur über `loop.confirm`/`loop.reject` (Befehle `/confirm`, `/reject`), nie als Text (A-H1).
Angezeigt wird, was der Code aus gespeicherten Daten rendert (Tabelle) bzw. der Loop als `Shown` liefert; nie ein Rohtext des
Modells und nie `str(exc)`.

Strg+C: Während eines Zugs endet der Zug als `interrupted` (der Loop fängt das `KeyboardInterrupt`), die CLI läuft weiter. Ein
zweites Strg+C, das den Loop selbst verlässt, bricht nur den Zug ab (der Loop ist danach benutzbar, TA-14 Teil A), die CLI
läuft ebenfalls weiter: Der Mensch hat nichts verloren und kann mit einem Strg+C am Prompt beenden. Strg+C am Prompt endet
sauber mit Exit-Code 130 (Konvention für SIGINT), EOF mit 0.
"""

from __future__ import annotations

import argparse
import codecs
import sys
import tempfile
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from scout_research.agent.loop import AgentLoop, LoopBusyError, LoopState, SessionClosedError, Shown, TurnResult
from scout_research.agent.prompt_loader import load_system_prompt
from scout_research.agent.spend import SpendBudget, SpendLedger
from scout_research.agent.trace import TraceWriter
from scout_research.demo.script import build_client
from scout_research.demo.world import TARGET_TICKER, build_context
from scout_research.tools.commentary import basis_line, render_table, render_warnings, table_legend
from scout_research.data.formats import is_iso_date
from scout_research.tools.sanitize import clean_text
from scout_research.tools.slots import fmt_date
from scout_research.tools.session import ConfirmationError, HumanInput, Proposal, ToolContext

LIVE_NOT_ALLOWED = "Live-Modus ist nicht freigegeben (Stufe C)."
EXIT_USAGE = 2
EXIT_INTERRUPT = 130
DEMO_RUN_LIMIT_USD = 1.0
DEMO_DAY_LIMIT_USD = 3.0
DEMO_LEDGER_NAME = "spend_ledger_demo.json"
LANGUAGE = "de"
DEMO_NOTICE = "Demo – erfundene Daten"
RATIONALE_LABEL = "Modell-Einschätzung, nicht belegt"
USER_ADDITION_LABEL = "vom Nutzer genannt"

BANNER = (f"Scout Research, Demo-Modus (--scripted): erfundene Firmen, festes Skript, kein Netzwerk.\n"
          f"{DEMO_NOTICE}. Beispiel: Mach Comps für {TARGET_TICKER}. Befehle: /confirm [TICKER ...], /reject, /quit. Strg+D beendet.")


def make_budget(*, scripted: bool, ledger_dir: Path | None = None, settings: Any = None) -> SpendBudget:
    """Der Budgetdeckel einer Sitzung. Scripted: eigenes Kostenbuch in `ledger_dir` (ein temporärer Ordner des Aufrufers),
    nie das Tagesbuch unter `runs_dir`: Demo-Kosten sind erfunden und dürfen den echten Tagesdeckel nicht belasten. Der
    spätere Live-Modus nimmt `SpendBudget.from_settings(settings)`."""
    if scripted:
        if ledger_dir is None:
            raise ValueError("scripted verlangt ein eigenes Verzeichnis für das Kostenbuch")
        return SpendBudget(DEMO_RUN_LIMIT_USD, DEMO_DAY_LIMIT_USD, SpendLedger(Path(ledger_dir) / DEMO_LEDGER_NAME))
    return SpendBudget.from_settings(settings)


# --- Ausgabe ------------------------------------------------------------------------------------------------------


def _out(text: str = "") -> None:
    print(text, flush=True)


def _guard(part: Callable[[], None]) -> None:
    """Jeder Ausgabeteil einzeln: Ein Fehler in einem Teil verhindert die Anzeige der übrigen nicht; fester Text, nie `str(exc)`."""
    try:
        part()
    except Exception:  # noqa: BLE001
        _out("Hinweis: Ein Teil der Anzeige konnte nicht erstellt werden.")


_COUNT_KEYS = ("sic_matches", "without_ticker", "not_in_revenue_frame", "outside_size_range", "passed_filters", "returned")


def _target_and_warnings(proposal: Proposal, ctx: ToolContext) -> list[str]:
    """Ziel (Ticker, Name, CIK) und Warnungen der Peer-Suche aus dem Kandidatenset des Vorschlags (Plan 12.7, TA-20.6 I-6):
    nur Tool-Daten des Codes, bereinigt; die Warnungstexte sind feste Texte des Codes. Dazu die Zähler der Peer-Suche (Zahlen
    aus dem Code) und das Periodenende des Ziels. Fehlt das Set, steht eine feste Zeile da (N2), nie nichts."""
    candidate_set = ctx.candidate_sets.get(proposal.candidate_set_id)
    if candidate_set is None:
        return ["Ziel: nicht ermittelbar"]
    target = candidate_set.target
    lines = [f"Ziel: {clean_text(target.get('ticker') or '?', 20)}, {clean_text(target.get('name', ''), 80)} "
             f"(CIK {clean_text(target.get('cik') or proposal.target_cik, 20)})"]
    if is_iso_date(candidate_set.period_end):
        lines.append(f"Periodenende des Ziels: {fmt_date(candidate_set.period_end, LANGUAGE)}")
    counts = candidate_set.counts
    if all(isinstance(counts.get(k), int) for k in _COUNT_KEYS):
        lines.append(f"Peer-Suche: {counts['sic_matches']} SIC-Treffer, {counts['without_ticker']} ohne Ticker, "
                     f"{counts['not_in_revenue_frame']} nicht im Umsatzvergleich, {counts['outside_size_range']} außerhalb der "
                     f"Größenspanne, {counts['passed_filters']} nach den Filtern, {counts['returned']} gezeigt")
    if candidate_set.warnings:
        lines.append("Warnungen der Peer-Suche:")
        lines += [f"  - [{clean_text(w.get('id', '?'), 10)}] {clean_text(w.get('text', ''), 300)}" for w in candidate_set.warnings]
    return lines


def render_proposal(proposal: Proposal, ctx: ToolContext) -> str:
    """Der Vorschlag, deterministisch aus dem Speicher des Codes: zuerst Ziel, Zähler und Warnungen der Peer-Suche,
    je Peer Ticker, Name und Begründung mit dem festen Label
    (R6: durch den Code, nicht durch das Modell), Ausschlüsse mit Grund, Nutzerergänzungen als „vom Nutzer genannt“. Die
    Texte stehen so da, wie `propose_peer_set` sie (bereinigt) gespeichert hat."""
    lines = ["Vorschlag für das Peer-Set:", *_target_and_warnings(proposal, ctx)]
    for peer in proposal.peers:
        lines.append(f"  - {peer.get('ticker', '?')}: {peer.get('name', '')}")
        if peer.get("rationale"):
            lines.append(f"      Begründung ({RATIONALE_LABEL}): {peer['rationale']}")
    for peer in proposal.user_additions:
        lines.append(f"  - {peer.get('ticker', '?')}: {peer.get('name', '')} ({USER_ADDITION_LABEL})")
    if proposal.exclusions:
        lines.append("Ausgeschlossen:")
        for item in proposal.exclusions:
            lines.append(f"  - {item.get('ticker', '?')}: {item.get('name', '')}")
            if item.get("reason"):
                lines.append(f"      Grund ({RATIONALE_LABEL}): {item['reason']}")
    lines.append("Entscheide mit /confirm [TICKER ...] oder /reject.")
    return "\n".join(lines)


def _show_table(compact: dict[str, Any]) -> None:
    _out(DEMO_NOTICE)
    _out("Comps-Tabelle (Werte in Mio. USD):")
    _out(basis_line(compact, LANGUAGE))
    _out()
    _out(render_table(compact, LANGUAGE))
    legend = table_legend(compact, LANGUAGE)
    if legend:
        _out()
        for line in legend:
            _out(line)
    _out()
    for line in render_warnings(compact, LANGUAGE):
        _out(line)
    _out()


def _show_item(item: Shown) -> None:
    if item.kind == "commentary":
        _out(DEMO_NOTICE)
    _out({"commentary": "Kommentar:", "notice": "Hinweis:", "intro": "Scout:", "text": "Scout:"}[item.kind])
    _out(item.text)
    _out()


def _show(loop: AgentLoop, result: TurnResult | None, tables_before: set[str]) -> None:
    """Zeigt, was der Code gespeichert hat, auch nach einer Ausnahme im Zug (Z19): neue Tabellen aus `ctx.tables`, die
    Ergebnisse des Zugs (falls es eines gibt) und den offenen Vorschlag, wenn der Loop auf die Entscheidung wartet."""
    ctx = loop.ctx
    for table_id in [t for t in ctx.tables if t not in tables_before]:
        _guard(lambda t=table_id: _show_table(ctx.tables[t].compact))
    for item in result.shown if result is not None else []:
        _guard(lambda i=item: _show_item(i))
    proposal = ctx.open_proposal
    if proposal is not None and loop.state is LoopState.AWAITING_PEER_CONFIRMATION:
        _guard(lambda: _out(render_proposal(proposal, ctx)))


# --- Sitzung --------------------------------------------------------------------------------------------------------


def _read_line() -> str | None:
    """Eine Eingabezeile; `None` bei EOF. Der Prompt geht auf stdout, damit ein Eingabeskript per Pipe lesbar bleibt."""
    sys.stdout.write("> ")
    sys.stdout.flush()
    line = sys.stdin.readline()
    return None if line == "" else line.rstrip("\n")


def _flush(writer: TraceWriter | None) -> None:
    if writer is None:
        return
    try:
        writer.flush()
    except Exception:  # noqa: BLE001 — der Trace ist Beiwerk; Details nie ausgeben
        _out("Hinweis: Der Trace konnte nicht vollständig geschrieben werden.")


def _demo_exhausted(loop: AgentLoop) -> bool:
    """Das Skript hat nicht mehr genug Schritte für einen weiteren Zug: sauberes Ende statt eines Fehlers (N11)."""
    remaining = getattr(loop.client, "remaining", None)
    return remaining is not None and remaining < loop.config.max_calls_per_turn


def _play(loop: AgentLoop, writer: TraceWriter | None, call: Callable[[], TurnResult]) -> bool:
    """Führt einen Host-Aufruf aus und zeigt das Ergebnis. `False`, wenn die Sitzung danach zu Ende ist."""
    if _demo_exhausted(loop):
        _out("Demo beendet.")
        return False
    ctx = loop.ctx
    tables_before = set(ctx.tables)
    result: TurnResult | None = None
    try:
        result = call()
    except KeyboardInterrupt:
        _out("Zug abgebrochen (Strg+C).")
    except (LoopBusyError, SessionClosedError):
        _out("Die Sitzung nimmt keine Eingabe mehr an.")
        return False
    except ConfirmationError:
        _out("Die Entscheidung wurde abgelehnt; es hat sich nichts geändert.")
    except Exception:  # noqa: BLE001 — nie ein Traceback, nie `str(exc)`
        _out("Interner Fehler im Zug.")
    try:
        _show(loop, result, tables_before)
    except KeyboardInterrupt:
        _out("Anzeige abgebrochen (Strg+C).")
    _flush(writer)
    return loop.state is not LoopState.CLOSED


def _decide(loop: AgentLoop, writer: TraceWriter | None, command: str, words: list[str]) -> bool:
    """`/confirm [TICKER ...]` und `/reject`: nur über die Host-API des Loops, nur für den offenen Vorschlag (auch nach
    einer Chatnachricht, Z2/Z5)."""
    proposal = loop.ctx.open_proposal
    if proposal is None:
        _out("Es liegt kein Vorschlag zur Entscheidung vor.")
        return True
    if command == "/reject":
        return _play(loop, writer, lambda: loop.reject(proposal.id))
    offered = {p["ticker"]: p["cik"] for p in [*proposal.peers, *proposal.user_additions]}
    wanted = [w.upper() for w in words]
    unknown = [w for w in wanted if w not in offered]
    if unknown:
        _out("Unbekannter Ticker im Vorschlag: " + ", ".join(unknown))
        return True
    keep = [offered[t] for t in wanted] if wanted else list(offered.values())
    return _play(loop, writer, lambda: loop.confirm(proposal.id, keep))


def chat_loop(loop: AgentLoop, writer: TraceWriter | None) -> int:
    _out(BANNER)
    while True:
        try:
            line = _read_line()
        except KeyboardInterrupt:
            _out()
            _out("Beendet.")
            return EXIT_INTERRUPT
        if line is None:
            _out()
            return 0
        text = line.strip()
        if not text:
            continue
        words = text.split()
        if words[0] in ("/quit", "/exit"):
            return 0
        if words[0] in ("/confirm", "/reject"):
            keep_going = _decide(loop, writer, words[0], words[1:])
        elif text.startswith("/"):
            _out("Unbekannter Befehl.")
            continue
        else:
            keep_going = _play(loop, writer, lambda: loop.run_turn(HumanInput(text)))
        if not keep_going:
            return 0


def run_scripted(runs_dir: Path | None) -> int:
    """Die Demo-Sitzung. Strg+C oder ein Fehler außerhalb eines Zugs (Trace schreiben/schließen) enden mit festem Text:
    130 bzw. 1, nie mit einem Traceback (N6)."""
    try:
        ctx = build_context(LANGUAGE)
        prompt = load_system_prompt(LANGUAGE)
        with tempfile.TemporaryDirectory(prefix="scout-demo-") as ledger_dir, ctx.edgar:
            budget = make_budget(scripted=True, ledger_dir=Path(ledger_dir))
            loop = AgentLoop(ctx, build_client(ctx), prompt.text, budget=budget)
            try:
                writer = TraceWriter(loop, prompt, base_dir=runs_dir) if runs_dir is not None else None
            except (OSError, ValueError):
                _out("Der Trace-Ordner ist nicht nutzbar.")
                return 1
            with writer if writer is not None else nullcontext():
                return chat_loop(loop, writer)
    except KeyboardInterrupt:
        _out()
        _out("Beendet.")
        return EXIT_INTERRUPT
    except Exception:  # noqa: BLE001 — nie ein Traceback, nie `str(exc)`
        _out("Interner Fehler außerhalb eines Zugs; die Sitzung ist beendet.")
        return 1


# --- Einstieg -------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scout-research", description="Scout Research, Research-Assistent für Trading Comps")
    sub = parser.add_subparsers(dest="command")
    chat = sub.add_parser("chat", help="Gespräch mit Scout")
    chat.add_argument("--scripted", action="store_true", help="Demo gegen die synthetische Welt und das feste Skript")
    chat.add_argument("--runs-dir", type=Path, default=None, help="Ordner für den Trace (ohne Angabe wird keiner geschrieben)")
    return parser


ASCII_FORMS = {
    "\u2212": "-", "≥": ">=", "≤": "<=", "†": "(+)", "‡": "(++)", "§": "(+++)", "…": "...", "–": "-", "—": "-",
    "„": '"', "“": '"', "”": '"', "‘": "'", "’": "'", "‚": "'", "→": "->", "←": "<-", "↔": "<->", "≈": "~", "±": "+/-",
    "×": "x", "\u00a0": " ", "€": "EUR", "£": "GBP", "¥": "JPY",
}
"""Feste ASCII-Formen der Sonderzeichen, die Renderer und feste Texte nutzen (TA-20.6, I-8, O-41). Sie gelten nur für Zeichen,
die der Strom nicht kodieren kann: Ein „−“ (U+2212) darf nie zu „?“ werden, sonst verliert eine negative Marge ihr Vorzeichen."""
OUTPUT_ERRORS = "scout_ascii_forms"
UNKNOWN_FORM = "<?>"


def _ascii_forms_or_escape(exc: UnicodeError) -> tuple[str, int]:
    """Fehlerbehandlung der Kodierung: bekannte Zeichen in ihre feste ASCII-Form, jedes andere als fester, ziffernfreier
    Platzhalter `<?>` (kein nacktes „?“; ein Codepunkt-Escape brächte Ziffern wie `2012` auf den Schirm, N3)."""
    if not isinstance(exc, UnicodeEncodeError):
        raise exc
    parts = [ASCII_FORMS.get(ch, UNKNOWN_FORM) for ch in exc.object[exc.start : exc.end]]
    return "".join(parts), exc.end


def _prepare_streams() -> None:
    """Nie an der Kodierung der Ausgabe scheitern (Pipe ohne UTF-8-Locale): was der Strom nicht kodieren kann, wird in eine
    feste ASCII-Form übersetzt oder sichtbar escaped (I-8); ein UTF-8-Strom bleibt unverändert."""
    codecs.register_error(OUTPUT_ERRORS, _ascii_forms_or_escape)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors=OUTPUT_ERRORS)
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _prepare_streams()
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse beendet selbst; als Rückgabewert weiterreichen
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    if args.command != "chat":
        parser.print_usage(sys.stderr)
        return EXIT_USAGE
    if not args.scripted:
        print(LIVE_NOT_ALLOWED, file=sys.stderr)
        return EXIT_USAGE
    return run_scripted(args.runs_dir)


if __name__ == "__main__":
    raise SystemExit(main())
