"""Phase-0 Definition of Done:

    python -m scout_research.scratch --ticker AAPL

gibt den Umsatz eines Unternehmens mit vollständiger Provenance aus. Kein LLM beteiligt.

Phase-1-Erweiterung:

    python -m scout_research.scratch --ticker AAPL --full

ergänzt den vollständigen v1-Kennzahlensatz (EBIT, EBITDA-Approx, Net Income, Debt, Cash,
Market Cap, EV, Margen, Wachstum). Fehlt eine Marktdatenquelle (kein Finnhub-Key, Stooq
blockiert), wird das explizit ausgewiesen statt einen Wert zu schätzen (siehe 8.3/8.4).
"""

from __future__ import annotations

import argparse
import sys

from scout_research.config import get_settings
from scout_research.data.cache import SqliteCache
from scout_research.data.edgar_client import EdgarClient, TickerNotFoundError
from scout_research.data.market_provider import CachedProvider, FinnhubProvider, PriceQuote, StooqProvider
from scout_research.data.rate_limiter import RateLimiter
from scout_research.domain.metrics import RevenueNotFoundError, extract_latest_annual_revenue, build_company_metrics


class _NullProvider:
    """Platzhalter-Primärprovider, wenn kein Finnhub-Key konfiguriert ist."""

    def get_price(self, ticker: str) -> PriceQuote | None:
        return None


def _print_basic(client: EdgarClient, company) -> None:
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


def _print_full(client: EdgarClient, company, settings) -> None:
    metadata = client.get_company_metadata(company.cik)
    facts = client.get_company_facts(company.cik)

    primary = FinnhubProvider(settings.finnhub_api_key) if settings.finnhub_api_key else _NullProvider()
    with SqliteCache() as cache:
        market_provider = CachedProvider(primary=primary, fallback=StooqProvider(), cache=cache)
        try:
            price_quote = market_provider.get_price(company.ticker)
        except Exception:
            price_quote = None

    try:
        metrics = build_company_metrics(company.cik, metadata, facts, price_quote)
    except RevenueNotFoundError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        sys.exit(1)

    def fmt(value: float | None, suffix: str = "") -> str:
        return f"{value:,.0f}{suffix}" if value is not None else "nicht verfügbar"

    def fmt_pct(value: float | None) -> str:
        return f"{value:.1%}" if value is not None else "nicht verfügbar"

    print(f"{metadata.name} ({company.ticker}, CIK {company.cik})  —  FY{metrics.fiscal_year}")
    print(f"SIC {metadata.sic_code} — {metadata.sic_description}")
    print()
    print(f"Revenue:            {fmt(metrics.revenue, ' USD')}")
    print(f"EBIT:               {fmt(metrics.ebit, ' USD')}")
    print(f"EBITDA (approx.):   {fmt(metrics.ebitda, ' USD')}")
    print(f"Net Income:         {fmt(metrics.net_income, ' USD')}")
    print(f"Total Assets:       {fmt(metrics.total_assets, ' USD')}")
    print(f"Total Debt:         {fmt(metrics.total_debt, ' USD')}")
    print(f"Cash:               {fmt(metrics.cash, ' USD')}")
    print()
    if metrics.market is not None:
        print(f"Kurs:               {metrics.market.price} ({metrics.market.source}, {metrics.market.as_of_date})")
        print(f"Shares Outstanding: {fmt(metrics.market.shares_outstanding)}")
        print(f"Market Cap:         {fmt(metrics.market.market_cap, ' USD')}")
        print(f"Enterprise Value:   {fmt(metrics.enterprise_value, ' USD')}")
    else:
        print("Marktdaten:         nicht verfügbar (kein Finnhub-Key gesetzt und Stooq-Fallback")
        print("                    liefert aktuell keine Daten — siehe FINNHUB_API_KEY in .env)")
    print()
    print(f"EBIT-Marge:         {fmt_pct(metrics.margins['ebit_margin'])}")
    print(f"Net-Marge:          {fmt_pct(metrics.margins['net_margin'])}")
    print(f"Umsatzwachstum YoY: {fmt_pct(metrics.growth_rates['revenue_yoy'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scout Research — Scratch-Skript")
    parser.add_argument("--ticker", required=True, help="Börsenticker, z.B. AAPL")
    parser.add_argument(
        "--full", action="store_true", help="Vollständiger v1-Kennzahlensatz statt nur Revenue (Phase 1)"
    )
    args = parser.parse_args()

    settings = get_settings()
    rate_limiter = RateLimiter(max_requests=settings.edgar_requests_per_second)

    with EdgarClient(user_agent=settings.edgar_user_agent, rate_limiter=rate_limiter) as client:
        try:
            company = client.resolve_cik(args.ticker)
        except TickerNotFoundError as exc:
            print(f"Fehler: {exc}", file=sys.stderr)
            sys.exit(1)

        if args.full:
            _print_full(client, company, settings)
        else:
            _print_basic(client, company)


if __name__ == "__main__":
    main()
