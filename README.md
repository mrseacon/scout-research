# Scout Research

KI-gestützter Research-Assistent für Comparable Company Analysis: Peer-Identifikation,
XBRL-Datenextraktion aus SEC-Filings und Multiple-Berechnung — mit vollständiger
Quellenrückverfolgbarkeit jeder Zahl.

Die vollständige Projektspezifikation (Vision, Architektur, Datenquellen, Roadmap) steht in
[`docs/foundation.md`](docs/foundation.md).

## Status

Phase 0 (Setup) abgeschlossen: EDGAR-Client-Grundgerüst mit Rate Limiting und User-Agent,
erster reproduzierbarer Datenabruf inkl. vollständiger Provenance.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # EDGAR_CONTACT_EMAIL mit echter Kontakt-Adresse befüllen
```

## Phase-0-Smoke-Test

```bash
python -m scout_research.scratch --ticker AAPL
```

Gibt den zuletzt gemeldeten Jahresumsatz mit Accession Number, Periode und Filing-Datum aus.
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
