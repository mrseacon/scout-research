# Scout Research

KI-gestützter Research-Assistent für Comparable Company Analysis: Peer-Identifikation,
XBRL-Datenextraktion aus SEC-Filings und Multiple-Berechnung — mit vollständiger
Quellenrückverfolgbarkeit jeder Zahl.

Die vollständige Projektspezifikation (Vision, Architektur, Datenquellen, Roadmap) steht in
[`docs/foundation.md`](docs/foundation.md).

## Status

Phase 0, 1 & 2 abgeschlossen:
- EDGAR-Client-Grundgerüst mit Rate Limiting, User-Agent und Retry-Logik
- Vollständiger v1-Kennzahlensatz (Revenue, EBIT, EBITDA-Approx, Net Income, Debt, Cash,
  Margen, YoY-Wachstum) mit vollständiger Provenance
- `MarketDataProvider`-Interface (Finnhub primär, Stooq als Fallback vorbereitet, SQLite-Cache)
- Peer-Kandidatensuche (SIC-Filter über `browse-edgar` + Größenfilter über den `frames`-Bulk-Endpunkt)
- Multiples (EV/Revenue, EV/EBITDA, EV/EBIT, P/E) mit expliziter Behandlung nicht aussagekräftiger
  Werte (negativer/fehlender Nenner → `None` + Grund, nie eine verzerrte Zahl)
- Quality Checks: IQR-basierte Ausreißer-Erkennung, Fiskaljahresende-Mismatch, fehlende Daten
- Vollständige `CompsTable`-Orchestrierung (Target + Peers + Multiples + Statistik + Warnings)

⚠ **Bekannte Lücken:**
- Stooq ist **unverifiziert und deaktiviert** (der Quote-Endpunkt hat nie funktioniert) — siehe
  [`docs/foundation.md`, Abschnitt 8.3](docs/foundation.md#83--bekannte-datenlücke-aktienkurse)
  und D7. Finnhub trägt Marktdaten allein (Throttle 55/min, 429 mit `Retry-After`).
- Datenabdeckung (30 Ticker gemessen, siehe Abschnitt 8.6): `total_debt` 20/30, `ebitda` 21/30,
  `shares` 24/30. Fehlende Werte bleiben `None` — Mehrklassen-Aktien (GOOGL, META, TEAM, ...) haben
  keine Shares aus `companyfacts`. Alle Werte gehören zur selben Berichtsperiode (Periodenanker).
- Net Debt/EBITDA ist noch nicht implementiert; es gibt keine Periodenauswahl (immer jüngstes 10-K).
- SIC-Klassifizierung ist grob (z. B. landet Salesforce nicht im selben SIC-Code wie Adobe) —
  wird in Phase 3 durch LLM-Ranking über eine breitere Kandidatenbasis kompensiert.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # EDGAR_CONTACT_EMAIL mit echter Kontakt-Adresse befüllen; FINNHUB_API_KEY optional
```

> **Nicht in einem iCloud-synchronisierten Ordner (z. B. `~/Desktop`) ablegen.** iCloud setzt `hidden`
> auf Punkt-Dateien (`.venv`, `.env`, ...); Python 3.13 überspringt dann die `.pth`-Datei des
> Editable-Installs, und `python -m scout_research...` scheitert mit `ModuleNotFoundError`
> (`pytest` läuft weiter). Siehe `docs/foundation.md`, Abschnitt 9 (Entwicklungsumgebung).
> Notbehelf: `PYTHONPATH=src python -m scout_research.scratch ...`

## Smoke-Tests

```bash
python -m scout_research.scratch --ticker AAPL                              # Phase 0: nur Revenue + Provenance
python -m scout_research.scratch --ticker AAPL --full                       # Phase 1: vollständiger Kennzahlensatz
python -m scout_research.scratch --ticker ADBE --comps INTU,ADSK,CDNS,TEAM  # Phase 2: vollständige Comps-Tabelle
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
