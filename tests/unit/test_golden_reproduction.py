"""Der Extraktor muss die Werte des Golden-Set-Seeds exakt reproduzieren (Toleranz 0, außer der Seed nennt
eine). Der Seed ist das Messlineal: Abweichungen werden hier nicht weggetestet, sondern als *bekannte
Abweichung* mit Begründung und `xfail(strict=True)` geführt — wird eine behoben (oder der Seed
entschieden), schlägt der strikte xfail an und die Markierung muss entfernt werden.

Abgedeckt: AAPL und NVDA (52/53-Wochen-Geschäftsjahre), MSFT (Juni), ADSK (Januar), CDNS (nur total_debt).
"""

import pytest

from eval.golden_seed import load_seed, seed_files, validate_seed
from eval.seed_compare import compare

# (ticker, feld) -> Begründung. Klassifikation: a Definitionsunterschied, b Extraktorfehler, c Seedfehler.
KNOWN_DEVIATIONS: dict[tuple[str, str], str] = {
    ("msft", "d_and_a"): (
        "(a) Seed-Zeile 'Depreciation, amortization, and other' enthält 'other'; MSFT taggt dafür kein "
        "Gesamt-D&A in companyfacts. Extraktor liefert bewusst None (D12, keine Teilposten addieren)."
    ),
    ("nvda", "shares_outstanding"): (
        "(a) Seed: Bilanz (24.304 Mio.), Extraktor: Deckblatt (dei, auf 0,1 Mrd. gerundet = 24,3 Mrd.). "
        "Quelle/Rundung — Entscheidung über eine Toleranz im Seed offen."
    ),
    ("adsk", "total_debt"): (
        "(a) Seed: Buchwert 2.483 (Bilanz). Extraktor: us-gaap:LongTermDebt = 2.500 (Nominal; ADSK taggt "
        "den Nominalwert unter einem Konzept, dessen Definition 'after unamortized discount' verlangt). "
        "Entscheidung offen: Buchwert oder Nominalwert."
    ),
    ("cdns", "total_debt_is_lower_bound"): (
        "(a) Wert stimmt; der Extraktor kennzeichnet ihn als Untergrenze (D10: UnsecuredLongTermDebt "
        "schließt den kurzfristigen Anteil laut Definition aus, dieser ist nicht gemeldet). Der Seed weiß "
        "aus dem 10-K, dass es keinen gibt. Entscheidung offen."
    ),
}


def _cases() -> list:
    cases = []
    for path in seed_files():
        data = load_seed(path)
        if data.get("status") != "complete" or validate_seed(data):
            cases.append(pytest.param(path.stem, None, id=f"{path.stem}-seed-unvollständig", marks=pytest.mark.skip(reason="Seed nicht vollständig")))
            continue
        for row in compare(path.stem):
            marks = []
            reason = KNOWN_DEVIATIONS.get((path.stem, row["field"]))
            if reason:
                marks.append(pytest.mark.xfail(strict=True, reason=reason))
            cases.append(pytest.param(path.stem, row, id=f"{path.stem}-{row['field']}", marks=marks))
    return cases


@pytest.mark.parametrize("ticker,row", _cases())
def test_extractor_reproduces_seed(ticker: str, row: dict) -> None:
    assert row["ok"], {k: v for k, v in row.items() if k != "ok"}


def test_known_deviations_refer_to_existing_seed_fields() -> None:
    fields = {(p.stem, r["field"]) for p in seed_files() for r in compare(p.stem)}
    assert set(KNOWN_DEVIATIONS) <= fields


def test_all_pinned_filings_are_the_ones_the_extractor_used() -> None:
    for path in seed_files():
        for row in compare(path.stem):
            assert row.get("accession_ok", True), f"{path.stem}.{row['field']}: fremdes Filing {row.get('accession')}"
