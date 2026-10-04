"""Phase-0 Definition of Done:

    python -m scout_research.scratch --ticker AAPL

gibt den Umsatz eines Unternehmens mit vollständiger Provenance aus. Kein LLM beteiligt.

Phase-1-Erweiterung:

    python -m scout_research.scratch --ticker AAPL --full

ergänzt den vollständigen v1-Kennzahlensatz (EBIT, EBITDA-Approx, Net Income, Debt, Cash,
Market Cap, EV, Margen, Wachstum). Fehlt eine Marktdatenquelle, wird das explizit ausgewiesen
statt einen Wert zu schätzen (siehe 8.3/8.4).

Phase-2-Erweiterung:

    python -m scout_research.scratch --ticker ADBE --comps INTU,ADSK,CDNS,TEAM

baut eine vollständige CompsTable gegen ein manuell übergebenes Peer-Set (Foundation Doc 3.1,
Schritte 4–9 minus LLM-Ranking/HITL-Gate — die kommen in Phase 3). Kein LLM beteiligt.
"""

from __future__ import annotations

import argparse
import sys

from scout_research.config import get_settings
from scout_research.data.cache import SqliteCache
from scout_research.data.edgar_client import CompanyLookup, EdgarClient, TickerNotFoundError
from scout_research.data.market_provider import (
    CachedProvider,
    FinnhubProvider,
    MarketDataProvider,
    PriceQuote,
)
from scout_research.data.rate_limiter import RateLimiter
from scout_research.domain.comps import build_comps_table
from scout_research.domain.metrics import RevenueNotFoundError, build_company_metrics, extract_latest_annual_revenue
from scout_research.domain.models import CompanyMetrics


class _NullProvider:
    """Platzhalter-Primärprovider, wenn kein Finnhub-Key konfiguriert ist."""

    def get_price(self, ticker: str) -> PriceQuote | None:
        return None


def _market_provider(settings) -> tuple[MarketDataProvider, SqliteCache]:
    # Ein einziger Provider (und damit ein einziger Throttle) für Target und alle Peers.
    # Stooq ist unverifiziert/deaktiviert (Foundation Doc 8.3, D7) und hier nicht eingehängt.
    primary = (
        FinnhubProvider(settings.finnhub_api_key, requests_per_minute=settings.finnhub_requests_per_minute)
        if settings.finnhub_api_key
        else _NullProvider()
    )
    cache = SqliteCache()
    return CachedProvider(primary=primary, fallback=None, cache=cache), cache


def _build_metrics(
    client: EdgarClient, market_provider: MarketDataProvider, company: CompanyLookup
) -> CompanyMetrics:
    metadata = client.get_company_metadata(company.cik)
    facts = client.get_company_facts(company.cik)
    try:
        price_quote = market_provider.get_price(company.ticker)
    except Exception:
        price_quote = None
    return build_company_metrics(company.cik, metadata, facts, price_quote)


def _print_basic(client: EdgarClient, company: CompanyLookup) -> None:
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


def _fmt(value: float | None, suffix: str = "") -> str:
    return f"{value:,.0f}{suffix}" if value is not None else "nicht verfügbar"


def _fmt_pct(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "nicht verfügbar"


def _fmt_multiple(value: float | None) -> str:
    return f"{value:.1f}x" if value is not None else "n/a"


def _print_full(client: EdgarClient, company: CompanyLookup, settings) -> None:
    market_provider, cache = _market_provider(settings)
    with cache:
        try:
            metrics = _build_metrics(client, market_provider, company)
        except RevenueNotFoundError as exc:
            print(f"Fehler: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"{metrics.company.name} ({company.ticker}, CIK {company.cik})  —  FY{metrics.fiscal_year}")
    print(f"SIC {metrics.company.sic_code} — {metrics.company.sic_description}")
    print()
    print(f"Revenue:            {_fmt(metrics.revenue, ' USD')}")
    print(f"EBIT:               {_fmt(metrics.ebit, ' USD')}")
    print(f"EBITDA (approx.):   {_fmt(metrics.ebitda, ' USD')}")
    print(f"Net Income:         {_fmt(metrics.net_income, ' USD')}")
    print(f"Total Assets:       {_fmt(metrics.total_assets, ' USD')}")
    print(f"Total Debt:         {_fmt(metrics.total_debt, ' USD')}")
    print(f"Cash:               {_fmt(metrics.cash, ' USD')}")
    print()
    if metrics.market is not None:
        print(f"Kurs:               {metrics.market.price} ({metrics.market.source}, {metrics.market.as_of_date})")
        print(f"Shares Outstanding: {_fmt(metrics.market.shares_outstanding)}")
        print(f"Market Cap:         {_fmt(metrics.market.market_cap, ' USD')}")
        print(f"Enterprise Value:   {_fmt(metrics.enterprise_value, ' USD')}")
    else:
        print("Marktdaten:         nicht verfügbar (kein Kurs von Finnhub — FINNHUB_API_KEY in .env —")
        print("                    oder Shares Outstanding nicht aus companyfacts ermittelbar, z. B. bei")
        print("                    Mehrklassen-Aktien)")
    print()
    print(f"EBIT-Marge:         {_fmt_pct(metrics.margins['ebit_margin'])}")
    print(f"Net-Marge:          {_fmt_pct(metrics.margins['net_margin'])}")
    print(f"Umsatzwachstum YoY: {_fmt_pct(metrics.growth_rates['revenue_yoy'])}")


def _print_comps(
    client: EdgarClient, target_company: CompanyLookup, settings, peer_tickers: list[str]
) -> None:
    market_provider, cache = _market_provider(settings)
    with cache:
        try:
            target_metrics = _build_metrics(client, market_provider, target_company)
        except RevenueNotFoundError as exc:
            print(f"Fehler (Target): {exc}", file=sys.stderr)
            sys.exit(1)

        peer_metrics: list[CompanyMetrics] = []
        for ticker in peer_tickers:
            try:
                peer_company = client.resolve_cik(ticker)
            except TickerNotFoundError as exc:
                print(f"Warnung: {exc} — übersprungen.", file=sys.stderr)
                continue
            try:
                peer_metrics.append(_build_metrics(client, market_provider, peer_company))
            except RevenueNotFoundError as exc:
                print(f"Warnung: {exc} — {ticker} übersprungen.", file=sys.stderr)

    if not peer_metrics:
        print("Fehler: kein gültiger Peer im übergebenen Set.", file=sys.stderr)
        sys.exit(1)

    table = build_comps_table(target_metrics, peer_metrics)

    print(f"Comps-Tabelle: {target_metrics.company.name} ({target_company.ticker})  —  {table.period_basis}")
    print()

    header = f"{'Ticker':10s} {'EV/Rev':>8s} {'EV/EBITDA':>10s} {'EV/EBIT':>9s} {'P/E':>8s}"
    print(header)
    print("-" * len(header))
    tm = table.target_multiples
    print(
        f"{tm.company_ticker:10s} {_fmt_multiple(tm.ev_revenue):>8s} {_fmt_multiple(tm.ev_ebitda):>10s} "
        f"{_fmt_multiple(tm.ev_ebit):>9s} {_fmt_multiple(tm.pe):>8s}  (Target)"
    )
    for pm in table.peer_multiples:
        print(
            f"{pm.company_ticker:10s} {_fmt_multiple(pm.ev_revenue):>8s} {_fmt_multiple(pm.ev_ebitda):>10s} "
            f"{_fmt_multiple(pm.ev_ebit):>9s} {_fmt_multiple(pm.pe):>8s}"
        )
    print("-" * len(header))
    for s in table.statistics:
        print(
            f"{s.multiple_name:10s} min={_fmt_multiple(s.min):>6s}  median={_fmt_multiple(s.median):>6s}  "
            f"mean={_fmt_multiple(s.mean):>6s}  max={_fmt_multiple(s.max):>6s}  (n={s.count_included})"
        )

    print()
    print(f"{len(table.warnings)} Warnings:")
    for w in table.warnings:
        print(f"  [{w.severity}] {w.company}: {w.message}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scout Research — Scratch-Skript")
    parser.add_argument("--ticker", required=True, help="Börsenticker des Zielunternehmens, z.B. ADBE")
    parser.add_argument(
        "--full", action="store_true", help="Vollständiger v1-Kennzahlensatz statt nur Revenue (Phase 1)"
    )
    parser.add_argument(
        "--comps",
        metavar="PEER1,PEER2,...",
        help="Komma-getrennte Peer-Ticker für eine vollständige Comps-Tabelle (Phase 2)",
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

        if args.comps:
            peer_tickers = [t.strip().upper() for t in args.comps.split(",") if t.strip()]
            _print_comps(client, company, settings, peer_tickers)
        elif args.full:
            _print_full(client, company, settings)
        else:
            _print_basic(client, company)


if __name__ == "__main__":
    main()
