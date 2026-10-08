"""Vergleich Extraktor ↔ Golden-Set-Seed (Messlineal), gepinnt an period_end und Accession des Seeds.

    python eval/seed_compare.py [aapl msft ...]

Zeigt je Firma und Feld Seed-Wert, Extraktor-Wert, Abweichung und Quellfiling. Die Klassifikation einer
Abweichung (a Definitionsunterschied / b Extraktor-Bug / c möglicher Seed-Fehler) macht ein Mensch.
Läuft offline gegen die Fixtures in tests/fixtures/.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

from eval.golden_seed import load_seed, seed_files, to_usd, tolerance_usd
from scout_research.data.edgar_client import CompanyMetadata
from scout_research.data.market_provider import PriceQuote
from scout_research.domain import metrics as metrics_module
from scout_research.domain.metrics import build_company_metrics
from scout_research.domain.models import CompanyMetrics
from scout_research.domain.periods import PeriodSelector

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"

_DEBT_CONCEPTS = set(
    metrics_module.DEBT_TOTAL_CONCEPTS
    + metrics_module.LONG_TERM_DEBT_TOTAL_CONCEPTS
    + metrics_module.LONG_TERM_DEBT_NONCURRENT_CONCEPTS
    + metrics_module.DEBT_CURRENT_CONCEPTS
    + metrics_module.LONG_TERM_DEBT_CURRENT_CONCEPTS
    + metrics_module.COMMERCIAL_PAPER_CONCEPTS
    + metrics_module.DEBT_LOWER_BOUND_CONCEPTS
)
_FIELD_CONCEPTS = {
    "revenue": set(metrics_module.REVENUE_CONCEPTS),
    "ebit": set(metrics_module.EBIT_CONCEPTS),
    "net_income": set(metrics_module.NET_INCOME_CONCEPTS),
    "total_assets": set(metrics_module.TOTAL_ASSETS_CONCEPTS),
    "cash": set(metrics_module.CASH_CONCEPTS),
    "d_and_a": set(metrics_module.DEPRECIATION_AMORTIZATION_CONCEPTS),
    "shares_outstanding": set(metrics_module.SHARES_OUTSTANDING_CONCEPTS),
}


def load_fixture(ticker: str) -> dict:
    return json.loads((FIXTURES / f"{ticker.lower()}_companyfacts_slim.json").read_text(encoding="utf-8"))


def extract_for_seed(ticker: str, seed: dict) -> CompanyMetrics:
    """Extraktor-Lauf für genau die im Seed gepinnte Periode (nicht "jüngstes 10-K")."""
    cik = seed["cik"]
    company = CompanyMetadata(
        cik=cik, name=ticker, sic_code=None, sic_description=None, fiscal_year_end=None, tickers=[ticker], exchanges=[]
    )
    quote = PriceQuote(ticker=ticker, price=1.0, as_of_date="2026-01-01", source="seed-compare")
    period = PeriodSelector(period_end=seed["period_end"])
    return build_company_metrics(cik, company, load_fixture(ticker), quote, period)


def extractor_value(metrics: CompanyMetrics, field: str) -> tuple[Decimal | None, str, str]:
    """(Wert, Konzept, Accession) des Extraktors für ein Seed-Feld, aus den `source_facts`."""
    if field == "total_debt":
        fact = next((f for f in metrics.source_facts if set(f.concept.split("+")) <= _DEBT_CONCEPTS), None)
    else:
        fact = next((f for f in metrics.source_facts if f.concept in _FIELD_CONCEPTS[field]), None)
    if fact is None:
        return None, "-", "-"
    return Decimal(str(fact.value)), fact.concept, fact.accession_number


def compare(ticker: str) -> list[dict]:
    seed = next(load_seed(p) for p in seed_files() if p.stem == ticker.lower())
    metrics = extract_for_seed(ticker, seed)
    rows: list[dict] = [
        {
            "field": "period_end",
            "seed_text": str(seed["period_end"]),
            "got_text": metrics.period_end,
            "ok": str(seed["period_end"]) == metrics.period_end,
        }
    ]
    for name, entry in seed["values"].items():
        want = to_usd(entry)
        got, concept, accession = extractor_value(metrics, name)
        diff = None if (want is None or got is None) else got - want
        value_ok = (want is None and got is None) or (diff is not None and abs(diff) <= tolerance_usd(entry))
        accession_ok = got is None or accession == str(seed["accession_number"])
        rows.append(
            {
                "field": name,
                "seed": want,
                "got": got,
                "diff": diff,
                "concept": concept,
                "accession": accession,
                "accession_ok": accession_ok,
                "ok": value_ok and accession_ok,
            }
        )
        if name == "total_debt":
            rows.append(
                {
                    "field": "total_debt_is_lower_bound",
                    "seed_text": str(entry["is_lower_bound"]).lower(),
                    "got_text": str(metrics.total_debt_is_lower_bound).lower(),
                    "ok": entry["is_lower_bound"] == metrics.total_debt_is_lower_bound,
                }
            )
    return rows


def _fmt(value: Decimal | None, signed: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value:+,.0f}" if signed else f"{value:,.0f}"


def main() -> None:
    wanted = {a.lower() for a in sys.argv[1:]}
    for path in seed_files():
        if wanted and path.stem not in wanted:
            continue
        print(f"\n== {path.stem.upper()} ==")
        for row in compare(path.stem):
            flag = "OK" if row["ok"] else "ABWEICHUNG"
            if "seed_text" in row:
                print(f"  {row['field']:<26} seed={row['seed_text']:>14}  extractor={row['got_text']:>14}  {flag}")
                continue
            acc = "" if row["accession_ok"] else f"  accession={row['accession']}"
            print(
                f"  {row['field']:<26} seed={_fmt(row['seed']):>16}  extractor={_fmt(row['got']):>16}  "
                f"diff={_fmt(row['diff'], True):>14}  {flag}  [{row['concept']}]{acc}"
            )


if __name__ == "__main__":
    main()
