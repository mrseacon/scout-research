"""L2 — Multiples-Berechnung (Foundation Doc 3.2).

Kern-Prinzip: Ein nicht aussagekräftiges Multiple (negativer/fehlender Nenner) wird als
`None` mit Begründung geführt — niemals als verzerrte Zahl (z. B. negatives EV/EBITDA).
Reine Funktionen, direkt als Tool-Handler in Phase 3 wiederverwendbar.
"""

from __future__ import annotations

import statistics as stats

from scout_research.domain.models import CompanyMetrics, CompanyMultiples, MultipleStatistics

MULTIPLE_NAMES = ["ev_revenue", "ev_ebitda", "ev_ebit", "pe"]

MULTIPLE_GETTERS = {
    "ev_revenue": lambda m: m.ev_revenue,
    "ev_ebitda": lambda m: m.ev_ebitda,
    "ev_ebit": lambda m: m.ev_ebit,
    "pe": lambda m: m.pe,
}


def _company_ticker(metrics: CompanyMetrics) -> str:
    if metrics.market is not None:
        return metrics.market.ticker
    return metrics.company.tickers[0] if metrics.company.tickers else metrics.company.cik


def compute_multiples(metrics: CompanyMetrics) -> CompanyMultiples:
    """EV/Revenue, EV/EBITDA, EV/EBIT, P/E für ein Unternehmen. Jeder Ausschluss trägt
    einen menschenlesbaren Grund in `excluded_reasons`."""
    excluded_reasons: dict[str, str] = {}
    ev = metrics.enterprise_value

    def _ev_missing_reason() -> str:
        # EV = Market Cap + Total Debt - Cash: die Ursache nennen, die tatsächlich fehlt.
        if metrics.market is None:
            return "Enterprise Value nicht verfügbar (Marktdaten fehlen: Kurs oder Shares)"
        if metrics.total_debt is None:
            return "Enterprise Value nicht verfügbar (Total Debt nicht ermittelbar)"
        return "Enterprise Value nicht verfügbar (Cash nicht ermittelbar)"

    def _ev_multiple(name: str, denominator: float | None, denominator_label: str) -> float | None:
        if ev is None:
            excluded_reasons[name] = _ev_missing_reason()
            return None
        if denominator is None:
            excluded_reasons[name] = f"{denominator_label} nicht verfügbar"
            return None
        if denominator <= 0:
            excluded_reasons[name] = f"{denominator_label} <= 0, Multiple nicht aussagekräftig"
            return None
        if ev <= 0:
            excluded_reasons[name] = "Enterprise Value <= 0, Multiple nicht aussagekräftig"
            return None
        return ev / denominator

    ev_revenue = _ev_multiple("ev_revenue", metrics.revenue, "Revenue")
    ev_ebitda = _ev_multiple("ev_ebitda", metrics.ebitda, "EBITDA")
    ev_ebit = _ev_multiple("ev_ebit", metrics.ebit, "EBIT")

    pe: float | None = None
    if metrics.market is None:
        excluded_reasons["pe"] = "Marktdaten (Market Cap) nicht verfügbar"
    elif metrics.net_income is None:
        excluded_reasons["pe"] = "Net Income nicht verfügbar"
    elif metrics.net_income <= 0:
        excluded_reasons["pe"] = "Net Income <= 0, Multiple nicht aussagekräftig"
    else:
        pe = metrics.market.market_cap / metrics.net_income

    return CompanyMultiples(
        company_ticker=_company_ticker(metrics),
        ev_revenue=ev_revenue,
        ev_ebitda=ev_ebitda,
        ev_ebit=ev_ebit,
        pe=pe,
        excluded_reasons=excluded_reasons,
        ev_multiples_are_lower_bound=metrics.enterprise_value_is_lower_bound,
    )


def compute_multiple_statistics(
    peer_multiples: list[CompanyMultiples], multiple_name: str
) -> MultipleStatistics:
    """Min/Median/Mean/Max eines Multiples über das Peer-Set, ohne die Peers, bei denen
    dieses Multiple nicht aussagekräftig war."""
    getter = MULTIPLE_GETTERS[multiple_name]
    is_ev_multiple = multiple_name.startswith("ev_")

    included: list[float] = []
    excluded_tickers: list[str] = []
    lower_bound_tickers: list[str] = []
    for peer in peer_multiples:
        value = getter(peer)
        if value is None:
            excluded_tickers.append(peer.company_ticker)
        else:
            included.append(value)
            if is_ev_multiple and peer.ev_multiples_are_lower_bound:
                lower_bound_tickers.append(peer.company_ticker)

    if not included:
        return MultipleStatistics(
            multiple_name=multiple_name,
            min=None,
            median=None,
            mean=None,
            max=None,
            count_included=0,
            excluded_tickers=excluded_tickers,
        )

    return MultipleStatistics(
        multiple_name=multiple_name,
        min=min(included),
        median=stats.median(included),
        mean=stats.mean(included),
        max=max(included),
        count_included=len(included),
        excluded_tickers=excluded_tickers,
        lower_bound_tickers=lower_bound_tickers,
    )
