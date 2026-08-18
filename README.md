# Scout Research

KI-gestützter Research-Assistent für Comparable Company Analysis: Peer-Identifikation,
XBRL-Datenextraktion aus SEC-Filings und Multiple-Berechnung — mit vollständiger
Quellenrückverfolgbarkeit jeder Zahl.

Die vollständige Projektspezifikation (Vision, Architektur, Datenquellen, Roadmap) steht in
[`docs/foundation.md`](docs/foundation.md).

## Status

Phase 0 & 1 abgeschlossen:
- EDGAR-Client-Grundgerüst mit Rate Limiting und User-Agent
- Vollständiger v1-Kennzahlensatz (Revenue, EBIT, EBITDA-Approx, Net Income, Debt, Cash,
  Margen, YoY-Wachstum) mit vollständiger Provenance
- `MarketDataProvider`-Interface (Finnhub primär, Stooq als Fallback vorbereitet, SQLite-Cache)

⚠ **Bekannte Lücke:** Stooq blockiert aktuell automatisierte Requests per Bot-Schutz — siehe
[`docs/foundation.md`, Abschnitt 8.3](docs/foundation.md#83--bekannte-datenlücke-aktienkurse)
und offene Entscheidung D7. Ohne `FINNHUB_API_KEY` sind Marktdaten (Market Cap, EV) aktuell
nicht verfügbar; alle anderen Kennzahlen funktionieren unabhängig davon.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # EDGAR_CONTACT_EMAIL mit echter Kontakt-Adresse befüllen; FINNHUB_API_KEY optional
```

## Smoke-Tests

```bash
python -m scout_research.scratch --ticker AAPL          # Phase 0: nur Revenue + Provenance
python -m scout_research.scratch --ticker AAPL --full   # Phase 1: vollständiger Kennzahlensatz
```

Kein LLM beteiligt — reiner Data-Access/Domain-Pfad (L1/L2).

## Tests

```bash
pytest
```

## Architektur

Layered, analog zum Sentinel-Projekt — siehe [`docs/foundation.md`](docs/foundation.md#7-architektur)
für Details.

```
L6 Presentation → L5 Export → L4 Agent → L3 Tools → L2 Domain Logic → L1 Data Access
```

**Harte Regel:** Keine Zahl im Output stammt vom LLM. Alle Kennzahlen werden deterministisch
aus SEC-EDGAR-Primärdaten berechnet; das Modell wählt, gruppiert und kommentiert — es rechnet nicht.
