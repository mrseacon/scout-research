"""Phase-0 Definition of Done:

    python -m scout_research.scratch --ticker AAPL

gibt den Umsatz eines Unternehmens mit vollständiger Provenance aus (Accession Number,
Periode, Filing-Datum). Kein LLM beteiligt — reiner L1/L2-Pfad.
"""

from __future__ import annotations

import argparse
import sys

from scout_research.config import get_settings
from scout_research.data.edgar_client import EdgarClient, TickerNotFoundError
from scout_research.data.rate_limiter import RateLimiter
from scout_research.domain.metrics import RevenueNotFoundError, extract_latest_annual_revenue


def main() -> None:
    parser = argparse.ArgumentParser(description="Scout Research — Phase 0 Scratch-Skript")
    parser.add_argument("--ticker", required=True, help="Börsenticker, z.B. AAPL")
    args = parser.parse_args()

    settings = get_settings()
    rate_limiter = RateLimiter(max_requests=settings.edgar_requests_per_second)

    with EdgarClient(user_agent=settings.edgar_user_agent, rate_limiter=rate_limiter) as client:
        try:
            company = client.resolve_cik(args.ticker)
        except TickerNotFoundError as exc:
            print(f"Fehler: {exc}", file=sys.stderr)
            sys.exit(1)

        metadata = client.get_company_metadata(company.cik)
        facts = client.get_company_facts(company.cik)

        try:
            revenue = extract_latest_annual_revenue(company.cik, facts)
        except RevenueNotFoundError as exc:
            print(f"Fehler: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"{metadata.name} ({company.ticker}, CIK {company.cik})")
    print(f"SIC {metadata.sic_code} — {metadata.sic_description}")
    print()
    print(f"Umsatz (FY{revenue.fiscal_year}): {revenue.value:,.0f} {revenue.unit}")
    print(f"  Periode:            {revenue.period_start} bis {revenue.period_end}")
    print(f"  Konzept:            {revenue.concept}")
    print(f"  Form:               {revenue.form_type}")
    print(f"  Accession Number:   {revenue.accession_number}")
    print(f"  Filed:              {revenue.filed_date}")
    print(f"  Retrieved (UTC):    {revenue.retrieved_at.isoformat()}")


if __name__ == "__main__":
    main()
