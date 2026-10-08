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

from eval.golden_seed import FLAG_FIELD, UNIT_FACTORS, load_seed, seed_files, to_usd, tolerance_usd
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
    return compare_seed(ticker, seed)


def compare_seed(ticker: str, seed: dict) -> list[dict]:
    """Vergleicht den Extraktor mit einem Seed-Dict. Jede Zeile hat `status`:

    - `match`: Extraktor = Seed (innerhalb `tolerance_abs`).
    - `known_deviation`: Extraktor weicht ab, und zwar genau wie in `known_deviations` gepinnt (ok).
    - `resolved`: ein gepinnter Eintrag existiert, aber der Extraktor stimmt jetzt mit dem Seed überein
      -> der Eintrag ist zu entfernen (nicht ok).
    - `changed`: ein gepinnter Eintrag existiert, der Extraktor liefert aber einen dritten Wert (nicht ok).
    - `mismatch`: ungepinnte Abweichung (nicht ok).
    Die Accession des Quellfilings muss dem Seed-Pin entsprechen, sonst ist die Zeile nicht ok.
    """
    metrics = extract_for_seed(ticker, seed)
    deviations = seed.get("known_deviations") or {}
    rows: list[dict] = [
        {
            "field": "period_end",
            "seed_text": str(seed["period_end"]),
            "got_text": metrics.period_end,
            "status": "match" if str(seed["period_end"]) == metrics.period_end else "mismatch",
        }
    ]
    for name, entry in seed["values"].items():
        want = to_usd(entry)
        got, concept, accession = extractor_value(metrics, name)
        diff = None if (want is None or got is None) else got - want
        matches_seed = (want is None and got is None) or (diff is not None and abs(diff) <= tolerance_usd(entry))
        row = {
            "field": name,
            "seed": want,
            "got": got,
            "diff": diff,
            "concept": concept,
            "accession": accession,
            "accession_ok": got is None or accession == str(seed["accession_number"]),
        }
        dev = deviations.get(name)
        if dev is None:
            row["status"] = "match" if matches_seed else "mismatch"
        else:
            expected = Decimal(str(dev["expected_extractor_value"])) * UNIT_FACTORS[entry["unit"]]
            row["expected"] = expected
            row["class"], row["reason"] = dev["class"], dev["reason"]
            row["status"] = "resolved" if matches_seed else ("known_deviation" if got == expected else "changed")
        rows.append(row)

        if name == "total_debt":
            flag_dev = deviations.get(FLAG_FIELD)
            flag_row = {
                "field": FLAG_FIELD,
                "seed_text": str(entry["is_lower_bound"]).lower(),
                "got_text": str(metrics.total_debt_is_lower_bound).lower(),
            }
            matches_flag = entry["is_lower_bound"] == metrics.total_debt_is_lower_bound
            if flag_dev is None:
                flag_row["status"] = "match" if matches_flag else "mismatch"
            else:
                flag_row["class"], flag_row["reason"] = flag_dev["class"], flag_dev["reason"]
                flag_row["status"] = (
                    "resolved"
                    if matches_flag
                    else ("known_deviation" if metrics.total_debt_is_lower_bound == flag_dev["expected_extractor_value"] else "changed")
                )
            rows.append(flag_row)

    for row in rows:
        row["ok"] = row["status"] in ("match", "known_deviation") and row.get("accession_ok", True)
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
            status = {"match": "OK", "known_deviation": "BEKANNTE ABWEICHUNG"}.get(row["status"], row["status"].upper())
            if "seed_text" in row:
                print(f"  {row['field']:<26} seed={row['seed_text']:>14}  extractor={row['got_text']:>14}  {status}")
            else:
                acc = "" if row["accession_ok"] else f"  accession={row['accession']}"
                print(
                    f"  {row['field']:<26} seed={_fmt(row['seed']):>16}  extractor={_fmt(row['got']):>16}  "
                    f"diff={_fmt(row['diff'], True):>14}  {status}  [{row['concept']}]{acc}"
                )
            if "reason" in row:
                print(f"      Klasse ({row['class']}): {row['reason']}")


if __name__ == "__main__":
    main()
