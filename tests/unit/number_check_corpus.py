"""Lader und Vergleich für das Prüfkorpus des Number-Checks (`tests/data/number_check_corpus.yaml`, Plan 11.6).

Das Korpus beschreibt die Sitzung mit **vollen** Werten (USD, Anteile, Faktoren). Hier werden sie mit denselben
Funktionen wie im Tool-Payload (`payloads.musd`, `pct`, `one_decimal`, `price`) in die kompakte Fassung gebracht, die
das Modell sieht und aus der gerendert wird. Kein pytest-Testmodul.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from scout_research.tools import payloads
from scout_research.tools.commentary import check_text, exempt_terms
from scout_research.tools.numbercheck import Identifiers
from scout_research.tools.slots import CheckSession

CORPUS_PATH = Path(__file__).resolve().parents[1] / "data" / "number_check_corpus.yaml"
MONEY = ("revenue", "ebit", "ebitda", "net_income", "total_debt", "cash", "market_cap", "enterprise_value")
PCT = ("ebit_margin", "net_margin", "revenue_yoy")
MULTIPLES = ("ev_revenue", "ev_ebitda", "ev_ebit", "pe")


def load_corpus() -> dict[str, Any]:
    return yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for name in MONEY:
        out[name] = payloads.musd(row.get(name))
    for name in PCT:
        out[name] = payloads.pct(row.get(name))
    for name in MULTIPLES:
        out[name] = payloads.one_decimal(row.get(name))
    out["price"] = payloads.price(row.get("price"))
    out.setdefault("cik", f"cik-{row['ticker']}")
    return out


def _compact_table(raw: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    table = {
        "comps_table_id": raw["comps_table_id"],
        "basis": dict(raw["basis"]),
        "target": _compact_row(raw["target"]),
        "peers": [_compact_row(p) for p in raw["peers"]],
        "statistics": [
            {"multiple": s["multiple"], "n": s["n"], "excluded": list(s["excluded"]), "lower_bound": list(s["lower_bound"]),
             **{agg: payloads.one_decimal(s[agg]) for agg in ("min", "median", "mean", "max")}}
            for s in raw["statistics"]
        ],
        "warnings": [{**w, "params": dict(w.get("params", {}))} for w in raw["warnings"]],
        "skipped_peers": list(raw["skipped_peers"]),
    }
    for add in variant.get("add_peers", []):
        source = next(p for p in table["peers"] if p["ticker"] == add["from"])
        table["peers"].append({**copy.deepcopy(source), "ticker": add["ticker"], "name": add["name"]})
    for path, value in (variant.get("overrides") or {}).items():
        parts = path.split(".")
        if parts[0] == "peers":
            next(p for p in table["peers"] if p["ticker"] == parts[1])[parts[2]] = value
        else:
            table[parts[0]][parts[1]] = value
    return table


def _compact_financials(raw: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    values = {
        metric: {"value": payloads.musd(entry["value"]), "unit": payloads.MONEY_UNIT, "flags": [],
                 "concept": "x", "accession_number": entry["accession_number"]}
        for metric, entry in raw["values"].items()
    }
    return {"financials_id": raw["financials_id"], "cik": raw["cik"], "ticker": raw["ticker"],
            "name": names.get(raw["cik"], raw["ticker"]), "period_end": raw["period_end"],
            "fiscal_year": raw["fiscal_year"], "values": values, "ebit_margin": payloads.pct(raw.get("ebit_margin")),
            "net_margin": payloads.pct(raw.get("net_margin")), "revenue_yoy": payloads.pct(raw.get("revenue_yoy"))}


def build_session(corpus: dict[str, Any], variant_name: str) -> CheckSession:
    shared = corpus["session"]["shared"]
    variant = corpus["session"]["variants"][variant_name]
    table = _compact_table(corpus["session"]["comps_table"], variant) if variant["comps_table"] else None
    names = {r["cik"]: r["name"] for r in shared["resolved"]}
    candidate_set = shared["candidate_set"]
    candidates = candidate_set["candidates"]

    ciks = {r["cik"] for r in shared["resolved"]} | {candidate_set["target"]["cik"], *[c["cik"] for c in candidates]}
    sics = {candidate_set["target"]["sic_code"], *[c["sic_code"] for c in candidates]}
    financials = {f["financials_id"]: _compact_financials(f, names) for f in shared["financials"]}
    accessions = {e["accession_number"] for f in financials.values() for e in f["values"].values()}
    warning_ids = {w["id"] for w in candidate_set["warnings"]} | ({w["id"] for w in table["warnings"]} if table else set())

    strings: list[str | None] = [r["name"] for r in shared["resolved"]] + [r["ticker"] for r in shared["resolved"]]
    strings += [candidate_set["target"]["name"], candidate_set["target"]["ticker"]]
    strings += [x for c in candidates for x in (c["name"], c["ticker"])]
    if table:
        strings += [x for r in [table["target"], *table["peers"]] for x in (r["name"], r["ticker"])]
    return CheckSession(
        language=variant["output_language"],
        table=table,
        table_id=table["comps_table_id"] if table else None,
        financials=financials,
        market={m["ticker"]: dict(m) for m in shared["market_data"]},
        warning_ids=frozenset(warning_ids),
        terms=tuple(exempt_terms(strings)),
        identifiers=Identifiers(frozenset(ciks), frozenset(sics), frozenset(accessions)),
    )


def _expected_findings(case: dict[str, Any]) -> list[tuple[str, int, int]]:
    out = []
    for item in case.get("findings") or []:
        code, match = (case["expect"][0], item) if isinstance(item, str) else (item["code"], item["match"])
        start = case["text"].find(match)
        assert start >= 0, f"{case['id']}: Fundstelle {match!r} steht nicht im Text"
        out.append((code, start, start + len(match)))
    return out


def evaluate(case: dict[str, Any], session: CheckSession) -> list[str]:
    """Abweichungen eines Falls von der Erwartung; leer = der Fall besteht."""
    result = check_text(session, case["text"], case["profile"])
    errors: list[str] = []
    reported = {(p.code, p.start, p.end, p.match) for p in result.problems}

    if case["expect"] == "accepted":
        if result.problems:
            errors.append(f"unerwartet abgewiesen: {[(p.code, p.kind, p.match) for p in result.problems]}")
        if "rendered" in case and result.body != case["rendered"]:
            errors.append(f"gerendert {result.body!r}, erwartet {case['rendered']!r}")
        if "footnotes" in case and result.marks != case["footnotes"]:
            errors.append(f"Marken {result.marks}, erwartet {case['footnotes']}")
        return errors

    codes = {p.code for p in result.problems}
    if codes != set(case["expect"]):
        errors.append(f"Codes {sorted(codes)}, erwartet {sorted(case['expect'])}; gemeldet {sorted((c, m) for c, _, _, m in reported)}")
    expected = _expected_findings(case)
    for code, a, b in expected:
        hits = [p for p in result.problems if p.code == code and p.start < b and a < p.end]
        if len(hits) != 1:
            errors.append(f"Fundstelle {case['text'][a:b]!r} ({code}) von {len(hits)} Problemen überdeckt: {[h.match for h in hits]}")
    for p in result.problems:
        if not any(code == p.code and p.start < b and a < p.end for code, a, b in expected):
            errors.append(f"Fehlalarm {p.code}/{p.kind}: {p.match!r}")
    return errors
