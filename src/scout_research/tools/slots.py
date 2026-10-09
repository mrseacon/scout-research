"""L3 — Slots: Syntax, Gültigkeit und Rendering (Phase-3-Plan, Abschnitt 11.4; Schritt 6).

Das Modell schreibt `[[co:ADBE:ev_ebitda]]`, der Code setzt den Wert aus dem Sitzungsspeicher ein. Aufgelöst wird
gegen die **kompakten** Payloads, die das Modell gesehen hat (Comps-Tabelle, `get_financials`, `get_market_data`):
Was das Modell sah, ist, was gerendert wird — dieselben Rundungsfunktionen, kein zweites Format.

Namensräume: `co` (Firmenwert der Tabelle), `stat` (Peer-Statistik), `basis`, `warn` (Warnungs-Marker), `fin`
(Wert aus `get_financials`, gebunden an das `fin_`-Handle) und `mkt` (Wert aus `get_market_data`). Die Grammatik ist
streng: exakte Schreibweise, keine Leerzeichen, keine stille Korrektur — einzige Normalisierung ist `.`↔`-` im Ticker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from scout_research.tools.numbercheck import Identifiers

# --- Sitzungsdaten, gegen die Slots aufgelöst werden --------------------------------------------------------------


@dataclass
class CheckSession:
    """Alles, was die Prüfung eines Modelltexts aus der Sitzung braucht — unabhängig von `ToolContext`, damit das
    Korpus dieselbe Prüfung ohne Handler fahren kann."""

    language: str = "de"
    table: dict[str, Any] | None = None
    """Kompakte Comps-Tabelle (`StoredTable.compact`), auf die sich `co`, `stat`, `basis` und `warn` beziehen."""
    table_id: str | None = None
    financials: dict[str, dict[str, Any]] = field(default_factory=dict)
    """`financials_id` → kompaktes `get_financials`-Ergebnis."""
    market: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Ticker → kompaktes `get_market_data`-Ergebnis (jüngstes je Ticker)."""
    warning_ids: frozenset[str] = frozenset()
    """Alle Warnungs-IDs der Sitzung (`W…` der Tabelle, `P…` der Peer-Suche): als Verweis im Text erlaubt."""
    terms: tuple[str, ...] = ()
    """Namen und Ticker der Sitzung (Kandidaten, Tabelle, aufgelöste Firmen)."""
    identifiers: Identifiers = Identifiers()


# --- Felder -------------------------------------------------------------------------------------------------------

MONEY_FIELDS = frozenset({"revenue", "ebit", "ebitda", "net_income", "total_debt", "cash", "market_cap", "enterprise_value"})
PCT_FIELDS = frozenset({"ebit_margin", "net_margin", "revenue_yoy"})
MULTIPLE_FIELDS = frozenset({"ev_revenue", "ev_ebitda", "ev_ebit", "pe"})
DATE_FIELDS = frozenset({"price_as_of", "period_end"})
CO_FIELDS = MONEY_FIELDS | PCT_FIELDS | MULTIPLE_FIELDS | DATE_FIELDS | {"price", "fiscal_year"}
MARKET_DEPENDENT = frozenset({"price", "market_cap", "enterprise_value"}) | MULTIPLE_FIELDS
EV_FIELDS = frozenset({"enterprise_value", "ev_revenue", "ev_ebitda", "ev_ebit"})
STAT_AGGREGATES = ("min", "median", "mean", "max", "n")
BASIS_FIELDS = ("n_peers", "n_peers_with_ev_multiples", "target_period_end", "target_fiscal_year", "price_as_of")
FIN_METRICS = ("revenue", "ebit", "ebitda", "net_income", "total_assets", "total_debt", "cash", "shares_outstanding")
FIN_EXTRA = ("ebit_margin", "net_margin", "revenue_yoy", "period_end", "fiscal_year")
MKT_FIELDS = ("price", "price_as_of", "shares_outstanding", "market_cap")

# --- Ergebnisse ---------------------------------------------------------------------------------------------------


@dataclass
class SlotOk:
    text: str
    path: str
    marks: str = ""
    firm: str | None = None
    """Ticker, dessen Etikett nötig ist, wenn er im selben Satz fehlt (O6)."""
    price_date: str | None = None
    """Kursdatum der Zeile, falls es als Zusatz erscheinen muss (abweichende Kursdaten)."""
    warning_id: str | None = None


@dataclass
class SlotFail:
    code: str
    kind: str
    hint: str
    reason: str | None = None


# --- Texte (de/en) ------------------------------------------------------------------------------------------------

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MINUS = "−"


def fmt_date(iso: str, lang: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return str(iso)
    return f"{d.day:02d}.{d.month:02d}.{d.year}" if lang == "de" else f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


def _number(value: float | int, base_decimals: int, lang: str) -> str:
    """Betrag ohne Vorzeichen; mindestens `base_decimals` Stellen, mehr nur, wenn der Payload-Wert mehr trägt (ein Wert
    ungleich 0 wird nie als 0 ausgegeben, `round_nonzero`)."""
    d = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    decimals = max(base_decimals, -d.as_tuple().exponent if d.as_tuple().exponent < 0 else 0)
    text = format(abs(d).quantize(Decimal(1).scaleb(-decimals)), ",f")
    if lang == "de":
        text = text.translate({ord(","): ".", ord("."): ","})
    return text


def fmt_value(kind: str, value: float | int, lang: str) -> str:
    """Formatierter Wert ohne Untergrenzen-Präfix und ohne Marken. `kind`: money | pct | multiple | price | shares |
    int."""
    sign = MINUS if value < 0 else ""
    if kind == "money":
        n = _number(value, 0, lang)
        return f"{sign}{n} Mio. USD" if lang == "de" else f"USD {sign}{n}m"
    if kind == "pct":
        n = _number(value, 1, lang)
        return f"{sign}{n} %" if lang == "de" else f"{sign}{n}%"
    if kind == "multiple":
        return f"{sign}{_number(value, 1, lang)}x"
    if kind == "price":
        n = _number(value, 2, lang)
        return f"{sign}{n} USD" if lang == "de" else f"USD {sign}{n}"
    if kind == "shares":
        n = _number(value, 1, lang)
        return f"{sign}{n} Mio. Stück" if lang == "de" else f"{sign}{n}m shares"
    return f"{sign}{value}"


def _kind_of(field_name: str) -> str:
    if field_name in MONEY_FIELDS:
        return "money"
    if field_name in PCT_FIELDS:
        return "pct"
    if field_name in MULTIPLE_FIELDS:
        return "multiple"
    if field_name == "price":
        return "price"
    return "int"


def _generic_reason(lang_field: str) -> str:
    return f"Für diesen Wert liegt kein Eintrag vor ({lang_field})."


def tickers_equal(a: str, b: str) -> bool:
    """`BRK.B` und `BRK-B` sind derselbe Ticker (F9); sonst exakt, auch in der Groß-/Kleinschreibung."""
    return a.replace(".", "-") == b.replace(".", "-")


def ticker_pattern(ticker: str) -> str:
    """Regex für einen Ticker im Text, `.` und `-` austauschbar, mit Wortgrenzen."""
    body = "".join("[.\\-]" if ch in ".-" else re.escape(ch) for ch in ticker)
    return rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])"


# --- Auflösung ----------------------------------------------------------------------------------------------------


def _find_row(table: dict[str, Any], ticker: str) -> dict[str, Any] | None:
    for row in [table["target"], *table["peers"]]:
        if tickers_equal(row["ticker"], ticker):
            return row
    return None


def _row_path(table: dict[str, Any], row: dict[str, Any], field_name: str) -> str:
    where = "target" if row is table["target"] else f"peers[{row['ticker']}]"
    return f"{where}.{field_name}"


def _ticker_hint(table: dict[str, Any]) -> str:
    tickers = ", ".join([table["target"]["ticker"], *[p["ticker"] for p in table["peers"]]])
    return f"Verwende einen Ticker der Tabelle: {tickers}."


def _unknown(kind: str, hint: str) -> SlotFail:
    return SlotFail("SLOT_UNKNOWN", kind, hint)


def _no_table() -> SlotFail:
    return _unknown("no_table", "Es gibt noch keine Comps-Tabelle; vorher sind nur fin:- und mkt:-Slots möglich.")


def resolve_slot(inner: str, session: CheckSession) -> SlotOk | SlotFail:
    """Löst den Inhalt eines `[[…]]`-Slots auf. Jede Abweichung von der Grammatik ist `SLOT_UNKNOWN`; ein vorhandener
    Slot ohne Wert ist `SLOT_VALUE_UNAVAILABLE` mit dem Grund aus dem Tool-Ergebnis."""
    parts = inner.split(":")
    prefix = parts[0]
    arity = {"co": 3, "stat": 3, "basis": 2, "warn": 2, "fin": 3, "mkt": 3}.get(prefix)
    if arity is None:
        return _unknown("unknown_prefix", "Erlaubte Slots: co:<TICKER>:<feld>, stat:<multiple>:<min|median|mean|max|n>, "
                        "basis:<feld>, warn:<W-ID>, fin:<financials_id>:<metrik>, mkt:<TICKER>:<feld>.")
    if len(parts) != arity or any(not p or p != p.strip() for p in parts):
        return _unknown("syntax", f"Der Slot {prefix}: hat {arity} Teile, getrennt durch Doppelpunkte, ohne Leerzeichen.")
    lang = session.language
    if prefix in ("co", "stat", "basis", "warn") and session.table is None:
        return _no_table()
    table = session.table
    if prefix == "co":
        return _resolve_co(table, parts[1], parts[2], lang)
    if prefix == "stat":
        return _resolve_stat(table, parts[1], parts[2], lang)
    if prefix == "basis":
        return _resolve_basis(table, parts[1], lang)
    if prefix == "warn":
        return _resolve_warn(table, parts[1], lang)
    if prefix == "fin":
        return _resolve_fin(session, parts[1], parts[2])
    return _resolve_mkt(session, parts[1], parts[2])


def _resolve_co(table: dict[str, Any], ticker: str, field_name: str, lang: str) -> SlotOk | SlotFail:
    row = _find_row(table, ticker)
    if row is None:
        skipped = next((s for s in table.get("skipped_peers", []) if tickers_equal(s["ticker"], ticker)), None)
        if skipped is not None:
            return SlotFail("SLOT_VALUE_UNAVAILABLE", "peer_skipped", "Schreibe stattdessen „nicht verfügbar“ mit dem Grund.",
                            reason=skipped.get("reason"))
        return _unknown("unknown_ticker", _ticker_hint(table))
    if field_name not in CO_FIELDS:
        return _unknown("unknown_field", "Erlaubte Felder: " + ", ".join(sorted(CO_FIELDS)) + ".")
    value = row.get(field_name)
    if value is None:
        reason = row.get("excluded", {}).get(field_name) or _generic_reason(field_name)
        return SlotFail("SLOT_VALUE_UNAVAILABLE", "value_none", "Schreibe stattdessen „nicht verfügbar“ mit dem Grund.",
                        reason=reason)
    if field_name in DATE_FIELDS:
        text, marks = fmt_date(str(value), lang), ""
    elif field_name == "fiscal_year":
        text, marks = str(value), ""
    else:
        text, marks = fmt_value(_kind_of(field_name), value, lang), ""
        lower = (field_name == "total_debt" and row.get("total_debt_is_lower_bound")) or (
            field_name in EV_FIELDS and row.get("ev_is_lower_bound"))
        if lower:
            text = "≥ " + text
        if (field_name == "ebitda" and row.get("ebitda_approximated")) or field_name == "ev_ebitda":
            marks = "*"
    price_date = row.get("price_as_of") if (field_name in MARKET_DEPENDENT and table["basis"].get("price_as_of") is None) else None
    return SlotOk(text, _row_path(table, row, field_name), marks, firm=row["ticker"], price_date=price_date)


def _resolve_stat(table: dict[str, Any], multiple: str, aggregate: str, lang: str) -> SlotOk | SlotFail:
    if multiple not in MULTIPLE_FIELDS:
        return _unknown("unknown_field", "Statistiken gibt es nur für: " + ", ".join(sorted(MULTIPLE_FIELDS)) + ".")
    if aggregate not in STAT_AGGREGATES:
        return _unknown("unknown_field", "Erlaubte Aggregate: " + ", ".join(STAT_AGGREGATES) + ".")
    stat = next((s for s in table["statistics"] if s["multiple"] == multiple), None)
    if stat is None:
        return _unknown("unknown_field", "Für dieses Multiple liegt keine Statistik vor.")
    path = f"statistics[{multiple}].{aggregate}"
    if aggregate == "n":
        return SlotOk(str(stat["n"]), path)
    value = stat.get(aggregate)
    if value is None:
        return SlotFail("SLOT_VALUE_UNAVAILABLE", "stat_empty", "Schreibe stattdessen „nicht verfügbar“ mit dem Grund.",
                        reason="Kein Peer hat einen Wert für dieses Multiple." if lang == "de" else "No peer has a value for this multiple.")
    marks = ""
    if multiple == "ev_ebitda":
        marks += "*"
    if stat.get("lower_bound"):
        marks += "†"
    if stat.get("excluded"):
        marks += "‡"
    if table["basis"].get("price_as_of") is None:
        marks += "§"
    return SlotOk(f"{fmt_value('multiple', value, lang)} (n = {stat['n']})", path, marks)


def _resolve_basis(table: dict[str, Any], name: str, lang: str) -> SlotOk | SlotFail:
    if name not in BASIS_FIELDS:
        return _unknown("unknown_field", "Erlaubte Basisfelder: " + ", ".join(BASIS_FIELDS) + ".")
    value = table["basis"].get(name)
    if value is None:
        return SlotFail("SLOT_VALUE_UNAVAILABLE", "price_date_mixed", "Schreibe stattdessen „nicht verfügbar“ mit dem Grund.",
                        reason="Die Kurse der Zeilen stammen von verschiedenen Tagen." if lang == "de"
                        else "The prices of the rows are from different dates.")
    text = fmt_date(str(value), lang) if name in ("target_period_end", "price_as_of") else str(value)
    return SlotOk(text, f"basis.{name}")


def _resolve_warn(table: dict[str, Any], warning_id: str, lang: str) -> SlotOk | SlotFail:
    warning = next((w for w in table["warnings"] if w["id"] == warning_id), None)
    if warning is None:
        ids = ", ".join(w["id"] for w in table["warnings"])
        return _unknown("unknown_warning", f"Warnungs-IDs dieser Tabelle: {ids}.")
    who = warning["company"]
    if who == "Peer-Set" and lang == "en":
        who = "peer set"
    return SlotOk(f"[{warning['id']}: {who}]", f"warnings[{warning['id']}]", warning_id=warning["id"])


def _resolve_fin(session: CheckSession, handle: str, metric: str) -> SlotOk | SlotFail:
    result = session.financials.get(handle)
    if result is None:
        return _unknown("unknown_handle", "Die financials_id muss aus einem get_financials-Ergebnis dieser Sitzung stammen.")
    lang = session.language
    path = f"financials[{handle}].{metric}"
    ticker = result.get("ticker")
    if metric in ("period_end", "fiscal_year", "ebit_margin", "net_margin", "revenue_yoy"):
        value = result.get(metric)
        if value is None:
            return SlotFail("SLOT_VALUE_UNAVAILABLE", "value_none", "Schreibe stattdessen „nicht verfügbar“ mit dem Grund.",
                            reason=_generic_reason(metric))
        if metric == "period_end":
            return SlotOk(fmt_date(str(value), lang), path, firm=ticker)
        if metric == "fiscal_year":
            return SlotOk(str(value), path, firm=ticker)
        return SlotOk(fmt_value("pct", value, lang), path, firm=ticker)
    if metric not in FIN_METRICS:
        return _unknown("unknown_field", "Erlaubte Metriken: " + ", ".join([*FIN_METRICS, *FIN_EXTRA]) + ".")
    entry = result.get("values", {}).get(metric)
    if entry is None:
        return _unknown("metric_not_in_result", "Diese Metrik wurde in get_financials nicht abgefragt.")
    if "value" not in entry or entry["value"] is None:
        return SlotFail("SLOT_VALUE_UNAVAILABLE", "value_none", "Schreibe stattdessen „nicht verfügbar“ mit dem Grund.",
                        reason=entry.get("unavailable_reason") or _generic_reason(metric))
    text = fmt_value("shares" if entry.get("unit") == "Mio. Stück" else "money", entry["value"], lang)
    flags = entry.get("flags", [])
    if "lower_bound" in flags:
        text = "≥ " + text
    return SlotOk(text, path, "*" if "approximated" in flags else "", firm=ticker)


def _resolve_mkt(session: CheckSession, ticker: str, field_name: str) -> SlotOk | SlotFail:
    result = next((r for t, r in session.market.items() if tickers_equal(t, ticker)), None)
    if result is None:
        return _unknown("unknown_ticker", "Marktdaten gibt es nur für Ticker, für die get_market_data in dieser Sitzung "
                        "aufgerufen wurde.")
    if field_name not in MKT_FIELDS:
        return _unknown("unknown_field", "Erlaubte Felder: " + ", ".join(MKT_FIELDS) + ".")
    lang = session.language
    key = {"price": "price", "price_as_of": "price_as_of", "shares_outstanding": "shares_outstanding_mio",
           "market_cap": "market_cap_musd"}[field_name]
    value = result.get(key)
    path = f"market[{result.get('ticker', ticker)}].{field_name}"
    if value is None:
        return SlotFail("SLOT_VALUE_UNAVAILABLE", "value_none", "Schreibe stattdessen „nicht verfügbar“ mit dem Grund.",
                        reason=result.get("unavailable_reason") or _generic_reason(field_name))
    text = {"price": lambda: fmt_value("price", value, lang), "price_as_of": lambda: fmt_date(str(value), lang),
            "shares_outstanding": lambda: fmt_value("shares", value, lang),
            "market_cap": lambda: fmt_value("money", value, lang)}[field_name]()
    return SlotOk(text, path, firm=result.get("ticker", ticker))
