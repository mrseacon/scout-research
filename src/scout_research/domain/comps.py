"""L2 — CompsTable-Orchestrierung (Foundation Doc 7.3, Tool `compute_comps_table`).

Kombiniert multiples.py und quality.py zur vollständigen `CompsTable`. Reine Funktion:
Eingabe ist bereits fertig extrahierte `CompanyMetrics` (Phase 1) — kein direkter L1-Zugriff.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scout_research.domain.models import CompanyMetrics, CompsTable
from scout_research.domain.multiples import MULTIPLE_NAMES, compute_multiple_statistics, compute_multiples
from scout_research.domain.quality import run_quality_checks


def build_comps_table(target: CompanyMetrics, peers: list[CompanyMetrics]) -> CompsTable:
    """Baut die vollständige Comps-Struktur: Multiples für Target und jeden Peer,
    Min/Median/Mean/Max-Statistik je Multiple über die Peers, und Quality Warnings.
    """
    if not peers:
        raise ValueError("build_comps_table benötigt mindestens einen Peer.")

    target_multiples = compute_multiples(target)
    peer_multiples = [compute_multiples(peer) for peer in peers]

    statistics = [
        compute_multiple_statistics(peer_multiples, multiple_name) for multiple_name in MULTIPLE_NAMES
    ]

    warnings = run_quality_checks(target, peers, peer_multiples)

    fiscal_years = {target.fiscal_year, *(peer.fiscal_year for peer in peers)}
    period_basis = (
        f"FY{target.fiscal_year} (Ziel)"
        if len(fiscal_years) == 1
        else f"FY{target.fiscal_year} (Ziel), Peers variieren — siehe Warnings zu Fiskaljahresende"
    )

    return CompsTable(
        target=target,
        peers=peers,
        target_multiples=target_multiples,
        peer_multiples=peer_multiples,
        statistics=statistics,
        warnings=warnings,
        created_at=datetime.now(timezone.utc),
        period_basis=period_basis,
    )
