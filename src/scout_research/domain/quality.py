"""L2 — Quality Checks (Foundation Doc 2.3, 11, Tool `run_quality_checks`).

Jede Warnung ist ein Hinweis für den Menschen, kein Abbruchgrund — das Peer-Set-Gate
(Foundation Doc 6.2) bleibt der einzige harte Kontrollpunkt. Reine Funktionen.
"""

from __future__ import annotations

import statistics as stats

from scout_research.domain.models import CompanyMetrics, CompanyMultiples, QualityWarning
from scout_research.domain.multiples import MULTIPLE_GETTERS, MULTIPLE_NAMES

MIN_DATAPOINTS_FOR_OUTLIER_CHECK = 4
"""IQR-Quartilsschätzung ist bei kleineren Samples zu instabil (D4, siehe Foundation Doc 13)."""

IQR_FENCE_MULTIPLIER = 1.5
"""Klassische Tukey-Fences (Q1 - 1.5*IQR, Q3 + 1.5*IQR)."""


def _company_ticker(metrics: CompanyMetrics) -> str:
    return metrics.market.ticker if metrics.market else (
        metrics.company.tickers[0] if metrics.company.tickers else metrics.company.cik
    )


def check_outliers(peer_multiples: list[CompanyMultiples], multiple_name: str) -> list[QualityWarning]:
    """IQR-basierte Ausreißer-Erkennung (D4). Unter `MIN_DATAPOINTS_FOR_OUTLIER_CHECK`
    Werten wird kein IQR erzwungen, sondern eine Info-Warnung ausgegeben."""
    getter = MULTIPLE_GETTERS[multiple_name]
    values_by_ticker = [(m.company_ticker, getter(m)) for m in peer_multiples if getter(m) is not None]

    if not values_by_ticker:
        return []

    if len(values_by_ticker) < MIN_DATAPOINTS_FOR_OUTLIER_CHECK:
        return [
            QualityWarning(
                severity="info",
                company="Peer-Set",
                message=(
                    f"Nur {len(values_by_ticker)} Datenpunkt(e) für {multiple_name} verfügbar — "
                    f"zu wenige für eine verlässliche Ausreißer-Erkennung "
                    f"(mindestens {MIN_DATAPOINTS_FOR_OUTLIER_CHECK} nötig)."
                ),
                affected_field=multiple_name,
            )
        ]

    values = [v for _, v in values_by_ticker]
    q1, _, q3 = stats.quantiles(sorted(values), n=4, method="inclusive")
    iqr = q3 - q1
    lower_fence = q1 - IQR_FENCE_MULTIPLIER * iqr
    upper_fence = q3 + IQR_FENCE_MULTIPLIER * iqr

    warnings: list[QualityWarning] = []
    for ticker, value in values_by_ticker:
        if value < lower_fence or value > upper_fence:
            warnings.append(
                QualityWarning(
                    severity="warning",
                    company=ticker,
                    message=(
                        f"{multiple_name}={value:.2f} liegt außerhalb der erwarteten Spannbreite "
                        f"[{lower_fence:.2f}, {upper_fence:.2f}] (IQR-Methode über das Peer-Set)."
                    ),
                    affected_field=multiple_name,
                )
            )
    return warnings


def check_fiscal_year_mismatch(
    target: CompanyMetrics, peers: list[CompanyMetrics]
) -> list[QualityWarning]:
    """Flaggt Peers, deren Fiskaljahresende (Monat) vom Zielunternehmen abweicht —
    Kennzahlen sind dann nicht exakt periodengleich (Foundation Doc 6.3 Beispiel: Autodesk)."""
    target_month = target.period_end[5:7]
    warnings: list[QualityWarning] = []

    for peer in peers:
        peer_month = peer.period_end[5:7]
        if peer_month == target_month:
            continue
        warnings.append(
            QualityWarning(
                severity="warning",
                company=_company_ticker(peer),
                message=(
                    f"Fiskaljahresende weicht ab: {peer.company.name} berichtet zum "
                    f"{peer.period_end}, Ziel zum {target.period_end} — Kennzahlen sind "
                    f"nicht exakt periodengleich."
                ),
                affected_field="period_end",
            )
        )
    return warnings


def check_missing_data(metrics: CompanyMetrics) -> list[QualityWarning]:
    """Flaggt fehlende Kernfelder für ein Unternehmen. `market` fehlt am schwersten
    (blockiert alle EV-Multiples und P/E), daher `warning`; einzelne Kennzahlen nur `info`."""
    ticker = _company_ticker(metrics)
    field_severity = {
        "revenue": "warning",
        "market": "warning",
        "ebit": "info",
        "ebitda": "info",
        "net_income": "info",
        "total_debt": "info",
        "cash": "info",
    }

    warnings: list[QualityWarning] = []
    for field_name, severity in field_severity.items():
        if getattr(metrics, field_name) is None:
            warnings.append(
                QualityWarning(
                    severity=severity,  # type: ignore[arg-type]
                    company=ticker,
                    message=f"{field_name} für {metrics.company.name} nicht verfügbar.",
                    affected_field=field_name,
                )
            )
    return warnings


def run_quality_checks(
    target: CompanyMetrics,
    peers: list[CompanyMetrics],
    peer_multiples: list[CompanyMultiples],
) -> list[QualityWarning]:
    """Orchestriert alle Checks (Foundation Doc 7.3, Tool `run_quality_checks`)."""
    warnings: list[QualityWarning] = []
    warnings += check_fiscal_year_mismatch(target, peers)
    warnings += check_missing_data(target)
    for peer in peers:
        warnings += check_missing_data(peer)
    for multiple_name in MULTIPLE_NAMES:
        warnings += check_outliers(peer_multiples, multiple_name)
    return warnings
