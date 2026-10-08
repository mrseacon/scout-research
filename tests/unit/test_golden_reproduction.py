"""Der Extraktor muss die Werte des Golden-Set-Seeds exakt reproduzieren (Toleranz 0, außer der Seed nennt
`tolerance_abs`). Der Seed ist das Messlineal: Abweichungen werden nicht weggetestet, sondern im Seed unter
`known_deviations` mit erwartetem Extraktorwert, Klasse (a/b/c) und Begründung *gepinnt*. Der Test schlägt an,
wenn der Extraktor von diesem Pin abweicht — in beide Richtungen (behoben -> Eintrag entfernen; verändert ->
neu bewerten).

Abgedeckt: AAPL und NVDA (52/53-Wochen-Geschäftsjahre), MSFT (Juni), ADSK (Januar), CDNS (nur total_debt).
"""

import copy
from decimal import Decimal

import pytest

from eval.golden_seed import load_seed, seed_files, validate_seed
from eval.seed_compare import compare, compare_seed


def _cases() -> list:
    cases = []
    for path in seed_files():
        data = load_seed(path)
        if data.get("status") != "complete" or validate_seed(data):
            cases.append(pytest.param(path.stem, None, id=f"{path.stem}-seed-unvollständig", marks=pytest.mark.skip(reason="Seed nicht vollständig")))
            continue
        for row in compare(path.stem):
            cases.append(pytest.param(path.stem, row, id=f"{path.stem}-{row['field']}"))
    return cases


@pytest.mark.parametrize("ticker,row", _cases())
def test_extractor_reproduces_seed_or_matches_its_pinned_deviation(ticker: str, row: dict) -> None:
    hint = {
        "resolved": "Abweichung behoben: known_deviations-Eintrag im Seed entfernen",
        "changed": "Extraktor liefert weder den Seed-Wert noch den gepinnten Wert: neu bewerten",
        "mismatch": "ungepinnte Abweichung vom Seed",
    }.get(row["status"], "Quellfiling entspricht nicht der gepinnten Accession")
    assert row["ok"], f"{hint}: " + str({k: v for k, v in row.items() if k not in ("reason",)})


def test_the_four_decided_cases() -> None:
    rows = {(t, r["field"]): r for t in ("msft", "nvda", "adsk", "cdns") for r in compare(t)}

    # MSFT d_and_a: kein Gesamtposten -> Seed absent_reason, Extraktor None, keine Abweichung
    msft = rows[("msft", "d_and_a")]
    assert (msft["seed"], msft["got"], msft["status"]) == (None, None, "match")

    # NVDA shares: Bilanz vs. gerundetes Deckblatt, innerhalb tolerance_abs = 50 Mio.
    nvda = rows[("nvda", "shares_outstanding")]
    assert nvda["status"] == "match" and nvda["diff"] != 0 and abs(nvda["diff"]) <= Decimal(50_000_000)

    # ADSK total_debt: Buchwert (Seed) vs. Nominalwert (LongTermDebt), gepinnt
    adsk = rows[("adsk", "total_debt")]
    assert adsk["status"] == "known_deviation" and adsk["class"] == "a"
    assert (adsk["seed"], adsk["got"]) == (Decimal(2_483_000_000), Decimal(2_500_000_000))

    # CDNS: Wert stimmt, Untergrenzen-Markierung (D10) gepinnt
    cdns_value, cdns_flag = rows[("cdns", "total_debt")], rows[("cdns", "total_debt_is_lower_bound")]
    assert cdns_value["status"] == "match"
    assert (cdns_flag["status"], cdns_flag["seed_text"], cdns_flag["got_text"]) == ("known_deviation", "false", "true")


def test_all_comparisons_pass_without_expected_failures() -> None:
    for path in seed_files():
        assert all(row["ok"] for row in compare(path.stem)), path.stem


def test_resolved_deviation_fails_so_the_pin_gets_removed() -> None:
    seed = copy.deepcopy(load_seed(next(p for p in seed_files() if p.stem == "adsk")))
    seed["values"]["total_debt"]["value"] = 2500  # Seed entspricht jetzt dem Extraktor (z. B. nach Entscheidung)
    seed["known_deviations"]["total_debt"].update(seed_value=2500, expected_extractor_value=2483)

    row = next(r for r in compare_seed("adsk", seed) if r["field"] == "total_debt")
    assert row["status"] == "resolved" and not row["ok"]


def test_changed_extractor_value_fails_a_pinned_deviation() -> None:
    seed = copy.deepcopy(load_seed(next(p for p in seed_files() if p.stem == "adsk")))
    seed["known_deviations"]["total_debt"]["expected_extractor_value"] = 2499  # Pin passt nicht mehr

    row = next(r for r in compare_seed("adsk", seed) if r["field"] == "total_debt")
    assert row["status"] == "changed" and not row["ok"]


def test_unpinned_deviation_fails() -> None:
    seed = copy.deepcopy(load_seed(next(p for p in seed_files() if p.stem == "adsk")))
    del seed["known_deviations"]

    row = next(r for r in compare_seed("adsk", seed) if r["field"] == "total_debt")
    assert row["status"] == "mismatch" and not row["ok"]


def test_flag_deviation_is_pinned_in_both_directions() -> None:
    seed = copy.deepcopy(load_seed(next(p for p in seed_files() if p.stem == "cdns")))
    seed["known_deviations"][ "total_debt_is_lower_bound"]["expected_extractor_value"] = False
    changed = next(r for r in compare_seed("cdns", seed) if r["field"] == "total_debt_is_lower_bound")
    assert changed["status"] == "changed" and not changed["ok"]

    del seed["known_deviations"]
    unpinned = next(r for r in compare_seed("cdns", seed) if r["field"] == "total_debt_is_lower_bound")
    assert unpinned["status"] == "mismatch" and not unpinned["ok"]


def test_wrong_filing_is_not_ok() -> None:
    seed = copy.deepcopy(load_seed(next(p for p in seed_files() if p.stem == "aapl")))
    seed["accession_number"] = "0000320193-24-000123"  # FY2024-10-K, Periode passt aber nicht dazu

    # Periode bleibt FY2025 gepinnt -> der Extraktor nutzt das FY2025-Filing, das der Pin nicht nennt
    rows = compare_seed("aapl", seed)
    assert any(not r["ok"] for r in rows if "accession_ok" in r)
