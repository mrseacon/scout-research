"""Erzeugt zugeschnittene `companyfacts`-Fixtures für Tests (ohne Netzwerk im Testlauf).

    python scripts/make_fixture.py AAPL NVDA MSFT ADSK CDNS            # lädt von der SEC
    python scripts/make_fixture.py AAPL --from-file raw_aapl.json      # schneidet eine lokale Rohdatei zu

Zuschnitt: Es bleiben nur die Konzepte, die der Extraktor (`scout_research.domain.metrics`) in seinen
Fallback-Ketten verwendet (alle Konstanten `*_CONCEPTS`), plus die dei-Cover-Page-Fakten (Shares). Die
Struktur (`facts` -> Taxonomie -> Konzept -> `units` -> Einträge) und alle Einträge je Konzept bleiben
unverändert — der Extraktor filtert selbst nach Formular und Periode. Wird `metrics.py` um ein Konzept
erweitert, wird die Konzeptliste automatisch mit erweitert; die Fixtures sind dann neu zu erzeugen.

Die Datei trägt unter `_fixture` Quell-URL, Abrufzeitpunkt und die behaltenen Konzepte. Der Abruf ist
eine Momentaufnahme (SEC-Daten ändern sich); Reproduzierbarkeit heißt: gleicher Zuschnitt, gleiche Quelle.
Die rohen SEC-Dateien werden nicht eingecheckt.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from scout_research.data.edgar_client import COMPANYFACTS_URL, pad_cik  # noqa: E402
from scout_research.domain import metrics as metrics_module  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"
MAX_BYTES = 500_000


def kept_concepts() -> dict[str, set[str]]:
    """Konzepte aus den Fallback-Ketten von `metrics.py`, nach Taxonomie."""
    us_gaap: set[str] = set()
    dei: set[str] = set(metrics_module.SHARES_OUTSTANDING_CONCEPTS)
    for name in dir(metrics_module):
        if name.endswith("_CONCEPTS") and name != "SHARES_OUTSTANDING_CONCEPTS":
            us_gaap.update(getattr(metrics_module, name))
    return {"us-gaap": us_gaap, "dei": dei}


def slim_companyfacts(raw: dict, source_url: str, retrieved_at: str) -> dict:
    keep = kept_concepts()
    facts = {
        taxonomy: {c: v for c, v in raw.get("facts", {}).get(taxonomy, {}).items() if c in concepts}
        for taxonomy, concepts in keep.items()
    }
    return {
        "_fixture": {
            "source_url": source_url,
            "retrieved_at": retrieved_at,
            "generator": "scripts/make_fixture.py",
            "kept_concepts": {t: sorted(c) for t, c in keep.items()},
            "note": "Zugeschnitten auf die Konzepte der Extraktor-Fallback-Ketten; Struktur unverändert.",
        },
        "cik": raw.get("cik"),
        "entityName": raw.get("entityName"),
        "facts": facts,
    }


def write_fixture(slim: dict, path: Path) -> int:
    text = json.dumps(slim, separators=(",", ":"), ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    size = len(text.encode("utf-8"))
    if size > MAX_BYTES:
        raise SystemExit(f"{path.name}: {size} Bytes überschreiten die Zielgröße von {MAX_BYTES}.")
    return size


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--from-file", help="lokale companyfacts-Rohdatei statt SEC-Abruf (nur ein Ticker)")
    parser.add_argument("--out", default=str(FIXTURE_DIR))
    args = parser.parse_args()
    out_dir = Path(args.out)

    if args.from_file:
        if len(args.tickers) != 1:
            raise SystemExit("--from-file erwartet genau einen Ticker.")
        raw = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
        slim = slim_companyfacts(raw, f"file:{args.from_file}", datetime.now(timezone.utc).isoformat())
        ticker = args.tickers[0].lower()
        print(f"{ticker}: {write_fixture(slim, out_dir / f'{ticker}_companyfacts_slim.json')} Bytes")
        return

    from scout_research.config import get_settings
    from scout_research.data.edgar_client import EdgarClient

    settings = get_settings()
    with EdgarClient(user_agent=settings.edgar_user_agent) as client:
        for ticker in args.tickers:
            company = client.resolve_cik(ticker)
            url = COMPANYFACTS_URL.format(cik=pad_cik(company.cik))
            raw = client.get_company_facts(company.cik)
            slim = slim_companyfacts(raw, url, datetime.now(timezone.utc).isoformat())
            size = write_fixture(slim, out_dir / f"{ticker.lower()}_companyfacts_slim.json")
            print(f"{ticker.upper()}: {size} Bytes ({url})")


if __name__ == "__main__":
    main()
