"""L3 — Kompakte Fassungen für das Modell (Phase-3-Plan, Abschnitt 2 „Grundsätze“, Review F4/F20).

Der Sitzungsspeicher hält alles vollständig; das Modell sieht gerundete Werte und Texte **ohne Zahlen**:
Mio. USD ohne Nachkommastellen, Multiples und Prozente mit einer Stelle (Prozent als Prozentzahl, 34.2 für 34,2 %),
`size_ratio` mit zwei Stellen. Ein Wert ungleich 0 wird nie als 0 ausgegeben. Warnungs- und Ausschlusstexte entstehen
aus Vorlagen je `kind` bzw. Code und enthalten weder Ziffern noch Firmennamen (E4 A, F3).
"""

from __future__ import annotations

from typing import Any

from scout_research.domain.metrics import (
    CASH_CONCEPTS,
    DEPRECIATION_AMORTIZATION_CONCEPTS,
    EBIT_CONCEPTS,
    NET_INCOME_CONCEPTS,
    REVENUE_CONCEPTS,
    SHARES_OUTSTANDING_CONCEPTS,
    TOTAL_ASSETS_CONCEPTS,
)
from scout_research.domain.models import CompanyMetrics, CompanyMultiples, FinancialFact, MultipleStatistics, QualityWarning
from scout_research.tools.sanitize import clean_text, is_valid_ticker

MONEY_UNIT = "Mio. USD"
SHARES_UNIT = "Mio. Stück"
UNITS = {"money": MONEY_UNIT, "multiple": "x", "pct": "%", "price": "USD je Aktie"}


def round_nonzero(value: float | None, decimals: int) -> float | int | None:
    """Rundet auf `decimals` Stellen; ein Wert ungleich 0, der dabei 0 würde, bekommt so viele Stellen wie nötig."""
    if value is None:
        return None
    for d in (decimals, decimals + 1, decimals + 2, decimals + 4, 8):
        rounded = round(value, d)
        if rounded != 0 or value == 0:
            return int(rounded) if d == 0 else rounded
    return value


def musd(value: float | None) -> float | int | None:
    return None if value is None else round_nonzero(value / 1e6, 0)


def mio_shares(value: float | None) -> float | int | None:
    return None if value is None else round_nonzero(value / 1e6, 1)


def one_decimal(value: float | None) -> float | int | None:
    return round_nonzero(value, 1)


def pct(value: float | None) -> float | int | None:
    return None if value is None else round_nonzero(value * 100, 1)


def ratio2(value: float | None) -> float | int | None:
    return round_nonzero(value, 2)


def price(value: float | None) -> float | int | None:
    return round_nonzero(value, 2)


# --- Warnungen ohne Zahlen (E4 A) --------------------------------------------------------------------------------

_PEER_SKIPPED_REASONS = {
    "de": {
        "DATA_NOT_FOUND": "bei der SEC liegen keine Daten für das Unternehmen vor",
        "REVENUE_NOT_FOUND": "in den Filings wurde kein Umsatz gefunden",
        "default": "Daten fehlen",
    },
    "en": {
        "DATA_NOT_FOUND": "the SEC has no data for the company",
        "REVENUE_NOT_FOUND": "no revenue was found in the filings",
        "default": "data is missing",
    },
}

_WARNING_TEMPLATES = {
    "de": {
        "fiscal_year_mismatch": "Das Geschäftsjahresende weicht vom Ziel ab; die Kennzahlen sind nicht periodengleich.",
        "stale_period": ("Der jüngste Jahresbericht endet ein Geschäftsjahr oder mehr vor dem Ziel; der Peer wird mit "
                         "einer älteren Periode verglichen."),
        "missing_data": "Für dieses Unternehmen fehlt ein Kernwert (siehe params.field).",
        "debt_lower_bound": ("total_debt ist nur eine Untergrenze: das Konzept schließt den kurzfristigen Anteil aus und "
                             "dieser ist nicht gemeldet."),
        "debt_lower_bound_ev": " EV und EV-Multiples sind daher ebenfalls Untergrenzen (zu niedrig); P/E ist nicht betroffen.",
        "debt_lower_bound_no_ev": (" Ein berechneter EV und die EV-Multiples wären Untergrenzen (hier mangels Marktdaten "
                                   "nicht berechnet)."),
        "outlier": "Der Wert dieses Multiples liegt {side} der erwarteten Spannbreite (IQR-Methode über das Peer-Set).",
        "outlier_above": "oberhalb",
        "outlier_below": "unterhalb",
        "too_few_datapoints": "Zu wenige Datenpunkte für dieses Multiple für eine verlässliche Ausreißer-Erkennung.",
        "peer_skipped": "Der Peer wurde übersprungen ({reason}); er fehlt in Tabelle und Statistik.",
        "sic_search_truncated": ("Die SIC-Suche hat das Seitenlimit erreicht; es kann weitere Unternehmen mit diesem "
                                 "SIC-Code geben, die nicht geprüft wurden."),
        "other": "Hinweis zur Datenqualität.",
    },
    "en": {
        "fiscal_year_mismatch": "The fiscal year end differs from the target; the figures are not for the same period.",
        "stale_period": ("The latest annual report ends a fiscal year or more before the target; the peer is compared "
                         "on an older period."),
        "missing_data": "A core value is missing for this company (see params.field).",
        "debt_lower_bound": ("total_debt is only a lower bound: the concept excludes the current portion and it is not "
                             "reported."),
        "debt_lower_bound_ev": " EV and EV multiples are therefore lower bounds as well (too low); P/E is not affected.",
        "debt_lower_bound_no_ev": (" A computed EV and the EV multiples would be lower bounds (not computed here for "
                                   "lack of market data)."),
        "outlier": "The value of this multiple lies {side} the expected range (IQR method across the peer set).",
        "outlier_above": "above",
        "outlier_below": "below",
        "too_few_datapoints": "Too few data points for this multiple for a reliable outlier test.",
        "peer_skipped": "The peer was skipped ({reason}); it is missing from the table and the statistics.",
        "sic_search_truncated": ("The SIC search reached its page limit; other companies with this SIC code may exist "
                                 "that were not checked."),
        "other": "Note on data quality.",
    },
}


_SIMPLE_WARNING_KINDS = frozenset({"fiscal_year_mismatch", "stale_period", "missing_data", "too_few_datapoints",
                                   "sic_search_truncated"})


def skip_reason(code: str, lang: str = "de") -> str:
    """Zahlenfreier Grund, warum ein Peer übersprungen wurde (Warnungstext und `skipped_peers`)."""
    reasons = _PEER_SKIPPED_REASONS[lang]
    return reasons.get(code, reasons["default"])


def warning_text(kind: str | None, params: dict[str, Any], lang: str = "de") -> str:
    """Text je Warnungsart, ohne Ziffern und ohne Firmennamen (E4 A). Auch für die vom Code angehängten Warnungen."""
    t = _WARNING_TEMPLATES[lang]
    if kind == "debt_lower_bound":
        return t["debt_lower_bound"] + (t["debt_lower_bound_ev"] if params.get("ev_available") else t["debt_lower_bound_no_ev"])
    if kind == "outlier":
        return t["outlier"].format(side=t["outlier_above"] if params.get("direction") == "above" else t["outlier_below"])
    if kind == "peer_skipped":
        return t["peer_skipped"].format(reason=skip_reason(str(params.get("code")), lang))
    return t[kind] if kind in _SIMPLE_WARNING_KINDS else t["other"]


def _warning_text(kind: str | None, params: dict[str, Any]) -> str:
    return warning_text(kind, params, "de")


def _model_params(params: dict[str, Any]) -> dict[str, Any]:
    """Nur Parameter ohne Zahlen: Wahrheitswerte und Zeichenketten ohne Ziffern (keine Daten, keine Abstände)."""
    safe: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, bool) or value is None:
            safe[key] = value
        elif isinstance(value, str) and not any(ch.isnumeric() for ch in value):
            safe[key] = clean_text(value, 80)
    return safe


def model_warning(warning: QualityWarning, warning_id: str, lang: str = "de") -> dict[str, Any]:
    company = warning.company if (is_valid_ticker(warning.company) or warning.company in ("Peer-Set", "target")) else "?"
    return {
        "id": warning_id,
        "severity": warning.severity,
        "kind": warning.kind or "other",
        "company": company,
        "field": warning.affected_field,
        "text": warning_text(warning.kind, warning.params, lang),
        "params": _model_params(warning.params),
    }


def full_warning(warning: QualityWarning, warning_id: str) -> dict[str, Any]:
    """Vollständige Fassung (mit Zahlen) für Speicher, Trace und die feste Warnungsliste der Anwendung."""
    return {"id": warning_id, **warning.model_dump(mode="json")}


# --- Ausschlussgründe ohne Zahlen --------------------------------------------------------------------------------

_DENOMINATOR_LABELS = {"ev_revenue": "Revenue", "ev_ebitda": "EBITDA", "ev_ebit": "EBIT"}

_EXCLUDED_TEMPLATES = {
    "de": {
        "historical_period": "Historische Periode: Multiples nur für die aktuelle Periode.",
        "ev_unavailable_market": "Enterprise Value nicht verfügbar (Marktdaten fehlen: Kurs oder Aktienanzahl).",
        "ev_unavailable_debt": "Enterprise Value nicht verfügbar (total_debt nicht ermittelbar).",
        "ev_unavailable_cash": "Enterprise Value nicht verfügbar (Cash nicht ermittelbar).",
        "denominator_unavailable": "{label} nicht verfügbar.",
        "denominator_not_positive": "{label} nicht positiv; Multiple nicht aussagekräftig.",
        "ev_not_positive": "Enterprise Value nicht positiv; Multiple nicht aussagekräftig.",
        "market_unavailable": "Marktdaten (Market Cap) nicht verfügbar.",
        "net_income_unavailable": "Net Income nicht verfügbar.",
        "net_income_not_positive": "Net Income nicht positiv; Multiple nicht aussagekräftig.",
        "default": "Multiple nicht verfügbar.",
        "denominator": "Nenner",
    },
    "en": {
        "historical_period": "Historical period: multiples only for the current period.",
        "ev_unavailable_market": "Enterprise value not available (market data missing: price or share count).",
        "ev_unavailable_debt": "Enterprise value not available (total_debt cannot be determined).",
        "ev_unavailable_cash": "Enterprise value not available (cash cannot be determined).",
        "denominator_unavailable": "{label} not available.",
        "denominator_not_positive": "{label} not positive; multiple not meaningful.",
        "ev_not_positive": "Enterprise value not positive; multiple not meaningful.",
        "market_unavailable": "Market data (market cap) not available.",
        "net_income_unavailable": "Net income not available.",
        "net_income_not_positive": "Net income not positive; multiple not meaningful.",
        "default": "Multiple not available.",
        "denominator": "Denominator",
    },
}


_EXCLUDED_CODES = frozenset({"historical_period", "ev_unavailable_market", "ev_unavailable_debt", "ev_unavailable_cash",
                             "denominator_unavailable", "denominator_not_positive", "ev_not_positive",
                             "market_unavailable", "net_income_unavailable", "net_income_not_positive"})


def excluded_text(field: str, code: str, lang: str = "de") -> str:
    t = _EXCLUDED_TEMPLATES[lang]
    label = _DENOMINATOR_LABELS.get(field, t["denominator"])
    return (t[code] if code in _EXCLUDED_CODES else t["default"]).format(label=label)


# --- Comps-Tabelle -----------------------------------------------------------------------------------------------


def build_row(metrics: CompanyMetrics, multiples: CompanyMultiples, ticker: str, lang: str = "de") -> dict[str, Any]:
    market = metrics.market
    return {
        "cik": metrics.company.cik,
        "ticker": ticker,
        "name": clean_text(metrics.company.name, 80),
        "period_end": metrics.period_end,
        "fiscal_year": metrics.fiscal_year,
        "price": price(market.price) if market else None,
        "price_as_of": market.as_of_date if market else None,
        "revenue": musd(metrics.revenue),
        "ebit": musd(metrics.ebit),
        "ebitda": musd(metrics.ebitda),
        "ebitda_approximated": metrics.ebitda_approximated,
        "net_income": musd(metrics.net_income),
        "total_debt": musd(metrics.total_debt),
        "total_debt_is_lower_bound": metrics.total_debt_is_lower_bound,
        "cash": musd(metrics.cash),
        "market_cap": musd(market.market_cap) if market else None,
        "enterprise_value": musd(metrics.enterprise_value),
        "ev_is_lower_bound": metrics.enterprise_value_is_lower_bound,
        "ebit_margin": pct(metrics.margins.get("ebit_margin")),
        "net_margin": pct(metrics.margins.get("net_margin")),
        "revenue_yoy": pct(metrics.growth_rates.get("revenue_yoy")),
        "ev_revenue": one_decimal(multiples.ev_revenue),
        "ev_ebitda": one_decimal(multiples.ev_ebitda),
        "ev_ebit": one_decimal(multiples.ev_ebit),
        "pe": one_decimal(multiples.pe),
        "excluded": {f: excluded_text(f, code, lang) for f, code in multiples.excluded_codes.items()},
    }


def build_statistic(stat: MultipleStatistics) -> dict[str, Any]:
    return {
        "multiple": stat.multiple_name,
        "min": one_decimal(stat.min),
        "median": one_decimal(stat.median),
        "mean": one_decimal(stat.mean),
        "max": one_decimal(stat.max),
        "n": stat.count_included,
        "excluded": list(stat.excluded_tickers),
        "lower_bound": list(stat.lower_bound_tickers),
    }


def common_price_date(rows: list[dict[str, Any]]) -> str | None:
    """Das gemeinsame Kursdatum aller Zeilen mit Kurs; weichen die Daten ab (oder fehlen alle), `None`."""
    dates = {r["price_as_of"] for r in rows if r.get("price_as_of")}
    return dates.pop() if len(dates) == 1 else None


# --- Financials mit Provenance -----------------------------------------------------------------------------------

_METRIC_CHAINS = {
    "revenue": REVENUE_CONCEPTS,
    "ebit": EBIT_CONCEPTS,
    "net_income": NET_INCOME_CONCEPTS,
    "total_assets": TOTAL_ASSETS_CONCEPTS,
    "cash": CASH_CONCEPTS,
    "shares_outstanding": SHARES_OUTSTANDING_CONCEPTS,
}

UNAVAILABLE_REASONS = {
    "revenue": "Im 10-K der Periode ist kein Umsatz ausgewiesen.",
    "ebit": "Im 10-K der Periode ist kein operatives Ergebnis (EBIT) ausgewiesen.",
    "ebitda": "EBITDA nicht verfügbar: EBIT oder Abschreibungen (D&A) sind im 10-K der Periode nicht ausgewiesen.",
    "net_income": "Im 10-K der Periode ist kein Nettoergebnis ausgewiesen.",
    "total_assets": "Im 10-K der Periode ist keine Bilanzsumme ausgewiesen.",
    "total_debt": "Gesamtschuld nicht ermittelbar: kein Gesamtkonzept und kein vollständiger Teilposten ausgewiesen.",
    "cash": "Im 10-K der Periode ist kein Kassenbestand ausgewiesen.",
    "shares_outstanding": "Die Aktienanzahl (Deckblatt) steht nicht im 10-K der Periode.",
}


def _find_fact(facts: list[FinancialFact], concepts: list[str], value: float | None) -> FinancialFact | None:
    if value is None:
        return None
    return next((f for f in facts if f.concept in concepts and f.value == value), None)


def metric_entry(metrics: CompanyMetrics, metric: str) -> dict[str, Any]:
    """Wert + Konzept + Accession Number (Provenance) für eine Kennzahl, oder der Grund, warum sie fehlt."""
    facts = metrics.source_facts
    unavailable = {"unavailable_reason": UNAVAILABLE_REASONS[metric]}

    if metric == "ebitda":
        if metrics.ebitda is None:
            return unavailable
        ebit = _find_fact(facts, EBIT_CONCEPTS, metrics.ebit)
        d_and_a = next((f for f in facts if f.concept in DEPRECIATION_AMORTIZATION_CONCEPTS), None)
        if ebit is None or d_and_a is None:  # kann nicht vorkommen, wenn EBITDA berechnet wurde
            return unavailable
        return {"value": musd(metrics.ebitda), "unit": MONEY_UNIT, "flags": ["approximated"],
                "concept": f"{ebit.concept}+{d_and_a.concept}",
                "accession_number": "+".join(dict.fromkeys([ebit.accession_number, d_and_a.accession_number]))}

    if metric == "total_debt":
        if metrics.total_debt is None:
            return unavailable
        known = {c for chain in _METRIC_CHAINS.values() for c in chain} | set(DEPRECIATION_AMORTIZATION_CONCEPTS)
        fact = next((f for f in facts if f.concept not in known and f.value == metrics.total_debt), None)
        if fact is None:
            return unavailable
        return {"value": musd(metrics.total_debt), "unit": MONEY_UNIT,
                "flags": ["lower_bound"] if metrics.total_debt_is_lower_bound else [],
                "concept": fact.concept, "accession_number": fact.accession_number}

    value = {"revenue": metrics.revenue, "ebit": metrics.ebit, "net_income": metrics.net_income,
             "total_assets": metrics.total_assets, "cash": metrics.cash}.get(metric)
    if metric == "shares_outstanding":
        fact = next((f for f in facts if f.concept in SHARES_OUTSTANDING_CONCEPTS), None)
        if fact is None:
            return unavailable
        return {"value": mio_shares(fact.value), "unit": SHARES_UNIT, "flags": [], "concept": fact.concept,
                "accession_number": fact.accession_number}
    fact = _find_fact(facts, _METRIC_CHAINS[metric], value)
    if fact is None:
        return unavailable
    return {"value": musd(value), "unit": MONEY_UNIT, "flags": [], "concept": fact.concept,
            "accession_number": fact.accession_number}
