# Scout Research — Foundation Document

**Projektname:** Scout Research
**Owner:** Sean Pölka
**Status:** In Entwicklung — Phase 0–2 abgeschlossen, Härtungs-Session vor Phase 3 durchgeführt, strategische Ausrichtung festgelegt (v1.6)
**Dokumentversion:** 1.6.4 — Basis für alle folgenden Code-Sessions

---

## 0. Wie dieses Dokument zu benutzen ist

Dieses Dokument ist die **Single Source of Truth** für das Projekt. Zu Beginn jeder Code-Session
wird es (oder der relevante Abschnitt) in den Kontext geladen. Es beantwortet:

- *Was* wird gebaut und *warum* (Abschnitte 1–5)
- *Wie* es technisch aussieht (Abschnitte 6–10)
- *Wie* Fortschritt gemessen wird (Abschnitte 11–12)
- *Wie* das Projekt nach außen erzählt wird (Abschnitt 14)

**Regel:** Wenn eine Entscheidung während einer Session getroffen wird, die diesem Dokument
widerspricht, wird das Dokument aktualisiert — nicht der Code stillschweigend abweichen gelassen.
Offene Entscheidungen stehen gesammelt in Abschnitt 13.

---

## 1. Vision & Problem Statement

### 1.1 Das Problem

Comparable Company Analysis (Comps) ist eine der drei Standard-Bewertungsmethoden im Investment
Banking, Private Equity und in der Transaction-Advisory-Beratung. Der Aufbau eines Comps-Sets ist
für einen Junior Analyst gleichzeitig:

- **Zeitintensiv:** Peer-Identifikation, Datenextraktion aus Filings, Normalisierung und
  Multiple-Berechnung kosten pro Analyse mehrere Stunden.
- **Fehleranfällig:** Manuelles Abtippen von Finanzkennzahlen, inkonsistente Fiskaljahre,
  unterschiedliche Bilanzierungsstichtage.
- **Kognitiv anspruchslos in weiten Teilen:** Das eigentliche Urteil (Ist dieser Peer wirklich
  vergleichbar? Ist dieses Multiple aussagekräftig?) macht vielleicht 20 % der Zeit aus — 80 %
  ist Datenbeschaffung und Formatierung.

Genau diese 80 % sind automatisierbar, ohne dass Urteilsvermögen verloren geht.

### 1.2 Die Vision

**Ein KI-gestützter Support-Teammember, der die Datenbeschaffungs- und Aufbereitungsarbeit einer
Comparable-Company-Analyse übernimmt — damit der Mensch seine Zeit für Bewertung, Interpretation
und Entscheidung einsetzt.**

Das Leitbild ist **nicht** "autonomer Analyst-Ersatz", sondern **"exzellenter Junior, der dir
zuarbeitet"**. Konkret bedeutet das:

| Der Agent macht | Der Mensch macht |
|---|---|
| Peer-Kandidaten vorschlagen (mit Begründung) | Peer-Set final freigeben / anpassen |
| Kennzahlen aus Filings ziehen | Plausibilität und Ausreißer bewerten |
| Multiples berechnen und Tabelle bauen | Bewertungsschlussfolgerung ziehen |
| Datenqualitätsprobleme flaggen | Entscheiden, wie damit umgegangen wird |
| Deliverable exportieren | Deliverable verwenden und verantworten |

### 1.3 Warum das der richtige Zuschnitt ist

1. **Baubar:** Ein klar abgegrenzter, testbarer Task statt eines offenen Forschungsproblems.
2. **Evaluierbar:** Comps haben eine überprüfbare Ground Truth — man kann messen, ob der Agent
   richtig liegt. Ein autonomer "Investment-Urteiler" ist praktisch nicht evaluierbar.
3. **Glaubwürdig:** Entspricht exakt der Art, wie Beratungen und Banken KI real einsetzen —
   Augmentation statt Substitution.
4. **Anschlussfähig:** Comps sind die Grundlage für spätere Bausteine (Precedent Transactions,
   DCF-Support, LBO-/Returns-Screening; Reihenfolge offen, D13). Das Projekt kann organisch wachsen —
   Richtung und Grenzen stehen in 1.4.

### 1.4 Strategische Ausrichtung (Entscheidung vom 2026-10-05)

**Entscheidung:** Scout Research ist ein spezialisierter Zuarbeiter für Private Equity und Deal-Analyse
(Valuation, Transaction Advisory). Es ist kein Werkzeug für Consulting-Frameworks (Marktgröße, Issue
Trees, Porter, 2x2, Folienlogik).

**Begründung:**
- Das Provenance-Prinzip ("das Modell erzeugt keine Zahl, jede Zahl hat eine Quelle") passt zu
  Finanzmodellen, nicht zu Strukturieren und Formulieren.
- Finanzmodelle haben eine prüfbare Ground Truth, daher funktioniert das Golden Set nur auf dieser Seite.
- Scope und Zeit: Breite in zwei Richtungen würde das Projekt verwässern.

**Ausbau des Workflows (nach Abschluss von Phase 5, jeweils mit eigenem Golden Set):**
1. Trading Comps (Kern, Phase 0–5)
2. Precedent Transactions aus 8-K und Merger-Proxies
3. DCF-Unterstützung (Annahmen immer vom Menschen)
4. LBO-/Returns-Screening (IRR, MoM; Annahmen wie Einstiegsmultiple, Leverage und Exit immer vom Menschen)
5. Optional: Due-Diligence-Support (Red Flags in 10-Ks)

Reihenfolge von 2–4 offen (D13).

**Prinzipien für jeden Baustein:** Das Modell erzeugt keine Zahl. Annahmen setzt der Mensch und bestätigt
sie explizit. Jede Zahl trägt ihre Quelle. Jeder Baustein hat eine eigene Evaluation, bevor er als fertig
gilt.

**Consulting als Ausgabeform:** Nach Phase 4 erzeugt Scout eine One-Pager-Zusammenfassung im
Consulting-Stil (Kernaussage zuerst, drei Beobachtungen, Risiken), ausschließlich aus Tool-Ergebnissen und
ohne neue Zahlen. Format offen (D14).

**Abgrenzung zu Arcticon:** Consulting-Frameworks sind kein Ziel von Scout, sondern ein Kandidat für
Arcticon. Arcticon kann Scout nach Phase 5 über einen MCP-Server nutzen (D15, Zeitpunkt offen). Regeln
dafür: Arcticon reicht Scouts strukturierte Tabellen durch und erzählt Zahlen nicht nach, und das
Peer-Gate wird nie automatisch bestätigt.

**Positionierung:** Scout belegt Valuation- und Deal-Kompetenz. Im CV und in Gesprächen wird nur
behauptet, was gebaut ist.

**Nicht-Ziele (strategisch):** Keine Framework-Engine. Kein Ausbau auf weitere Modelltypen vor Abschluss
der Evaluation.

---

## 2. Value Proposition — Abgrenzung zum "ChatGPT-Wrapper"

Das zentrale Risiko dieses Projekttyps ist, dass er wie ein dünner LLM-Wrapper aussieht. Vier
Eigenschaften verhindern das explizit und müssen von Tag 1 an im Design verankert sein:

### 2.1 Provenance / Nachvollziehbarkeit (wichtigstes Differenzierungsmerkmal)

**Jede einzelne Zahl im Output trägt ihre Herkunft mit sich:** CIK, Accession Number des Filings,
XBRL-Konzept, Periode, Einheit und Abrufzeitpunkt. Es gibt im gesamten System **keine Zahl, die
ein Sprachmodell generiert hat.** Zahlen kommen ausschließlich aus deterministischem Code, der
strukturierte Daten abruft; das LLM darf Zahlen nur *auswählen, gruppieren und kommentieren*,
niemals *produzieren*.

Das ist der Satz, der im Interview den Unterschied macht:
> "Das Modell trifft keine numerischen Aussagen. Es orchestriert Tools, die Zahlen aus
> Primärquellen ziehen — jede Zelle in der Ausgabe lässt sich bis zum konkreten SEC-Filing
> zurückverfolgen."

### 2.2 Deterministische Rechenschicht

Multiples, Mediane, Wachstumsraten und Margen werden in Python berechnet, nicht vom Modell.
Das LLM sieht Ergebnisse, rechnet aber nicht selbst.

### 2.3 Explizite Unsicherheits- und Qualitätssignale

Der Agent liefert nicht nur Zahlen, sondern auch Warnungen: fehlende Perioden, abweichende
Fiskaljahresenden, Ausreißer-Multiples, ungewöhnliche Einheiten, Peers mit zu geringer
Größenähnlichkeit.

### 2.4 Strukturiertes Deliverable statt Chat-Text

Ergebnis ist eine echte Comps-Tabelle (Excel/CSV/Markdown) mit Quellenblatt — kein Fließtext,
den man abtippen muss.

---

## 3. Scope v1

### 3.1 Der Kern-Workflow

```
Nutzer nennt Zielunternehmen
        ↓
[1] Unternehmen auflösen  → CIK, Ticker, SIC-Code, Name
        ↓
[2] Peer-Kandidaten vorschlagen  → mit Begründung je Kandidat
        ↓
[3] MENSCH BESTÄTIGT / ÄNDERT PEER-SET     ← bewusster Human-in-the-Loop-Punkt
        ↓
[4] Finanzkennzahlen je Peer abrufen  → aus XBRL-Daten
        ↓
[5] Marktdaten ergänzen  → Kurs, Shares Outstanding → Market Cap, EV
        ↓
[6] Multiples berechnen  → deterministisch in Python
        ↓
[7] Comps-Tabelle bauen  → inkl. Min / Median / Mean / Max
        ↓
[8] Qualitätsprüfung  → Ausreißer, Lücken, Inkonsistenzen flaggen
        ↓
[9] Kommentar des Agenten  → qualitative Einordnung, keine neuen Zahlen
        ↓
[10] Export  → XLSX / CSV / Markdown mit Quellenblatt
```

### 3.2 Kennzahlen-Umfang v1

**Financials (aus XBRL):** Revenue, EBIT, Net Income, Total Assets, Total Debt, Cash &
Equivalents, Shares Outstanding.
*EBITDA:* nicht direkt als XBRL-Konzept verfügbar → wird als EBIT + D&A approximiert, mit
expliziter Kennzeichnung als approximiert (siehe 8.4).

**Marktdaten:** Aktienkurs, Market Cap, Enterprise Value (Market Cap + Total Debt − Cash).

**Multiples:** EV/Revenue, EV/EBITDA, EV/EBIT, P/E.

**Kennzahlen:** EBIT-Marge, Net-Marge, Umsatzwachstum YoY, Verschuldungsgrad (Net Debt/EBITDA).
*Stand v1.5: EBIT-Marge, Net-Marge und Umsatzwachstum YoY sind implementiert (YoY nur zwischen zwei
direkt aufeinanderfolgenden Geschäftsjahren). Der **Verschuldungsgrad Net Debt/EBITDA ist noch nicht
implementiert** — der frühere Stand "Phase 1: alle v1-Kennzahlen" war in diesem Punkt zu weit gefasst.*

### 3.3 Erfolgskriterium für v1

> Ich gebe ein US-börsennotiertes Unternehmen an und erhalte innerhalb weniger Minuten eine
> nachvollziehbare, quellenbelegte Comps-Tabelle mit 5–10 Peers, die ich ohne Nacharbeit an den
> Zahlen weiterverwenden kann.

---

## 4. Nicht-Ziele (v1)

Explizite Abgrenzung — schützt vor Scope Creep und macht das Projekt im Gespräch glaubwürdiger:

- ❌ **Keine Investment-Empfehlungen** (kein "kaufen/halten/verkaufen", keine Kursziele)
- ❌ **Keine autonome Ausführung ohne Freigabe** — Peer-Set wird immer bestätigt
- ❌ **Kein DCF-Modell in v1** (geplanter späterer Baustein, siehe 1.4; Reihenfolge D13)
- ❌ **Keine Precedent Transactions in v1** (geplanter späterer Baustein, siehe 1.4; Reihenfolge D13)
- ❌ **Kein LBO-/Returns-Screening in v1** (siehe 1.4)
- ❌ **Keine Framework-Engine** (Marktgröße, Issue Trees, Porter, 2x2) — strategisch, nicht nur v1 (1.4)
- ❌ **Kein Ausbau auf weitere Modelltypen vor Abschluss der Evaluation** (Phase 5)
- ❌ **Keine Nicht-US-Unternehmen** (EDGAR deckt primär US-Registranten ab)
- ❌ **Keine privaten Unternehmen als Zielobjekt in v1** (siehe 8.5 für den PE-Bezug)
- ❌ **Keine Echtzeit-Kurse** (End-of-Day reicht vollständig)
- ❌ **Kein Multi-User-Betrieb, kein Auth, kein Deployment** in v1
- ❌ **Keine eigene Modell-Trainierung / Fine-Tuning**

---

## 5. Zielgruppe & Nutzungskontext

**Primärnutzer:** Sean (Analyst-in-Ausbildung) — reale Nutzung für eigene Analysen, Uni-Projekte,
Interview-Vorbereitung (Case-Studies mit echten Comps).

**Sekundärer Nutzungskontext:** Portfolio-Artefakt für Bewerbungen im Bereich Technology
Consulting / AI & Data / Corporate Finance.

**Kein Ziel:** Externe Nutzer, kommerzielles Produkt, Nutzerkonten. (Falls das später kommt,
ist es ein eigenes Projekt mit eigener Spezifikation — siehe Idee "Personal Finance Copilot".)

---

## 6. Interaktionsmodell

### 6.1 Entscheidung: Hybrid — Chat-Interaktion + strukturiertes Deliverable

Begründung aus der Analyse der drei Optionen:

| Ansatz | Vorteil | Nachteil | Entscheidung |
|---|---|---|---|
| Reiner Chat | Iterativ, demo-freundlich, bekannte Technik (Sentinel) | Output bleibt unstrukturiert im Chat | Als Interaktionsschicht ✅ |
| Reiner Dokument-Workflow | Direkt nutzbares Deliverable | Schwerfällig für schnelle Rückfragen, hoher Aufwand | Als Output-Schicht ✅ |
| Reines CLI | Schnellster Bau, Fokus auf Logik | Nicht demonstrierbar, kein Teamgefühl | Nur intern für Tests ⚠️ |

**Resultat:** Streamlit-Chat als Interaktionsebene, jede abgeschlossene Analyse landet als
strukturiertes Artefakt (XLSX/CSV/MD) auf der Platte. Genau wie mit einem echten Junior: man
spricht mit ihm, bekommt aber am Ende ein Sheet.

### 6.2 Interaktionsprinzipien

1. **Der Agent fragt nach, statt zu raten.** Bei Mehrdeutigkeit ("Apple" → Apple Inc. oder
   Apple Hospitality REIT?) wird zurückgefragt.
2. **Der Agent zeigt seinen Plan, bevor er teure Schritte ausführt.**
3. **Der Agent begründet Peer-Vorschläge** (warum ist dieses Unternehmen vergleichbar?).
4. **Der Agent sagt, was er nicht weiß.** Fehlende Daten werden benannt, nicht geschätzt.
5. **Der Mensch behält Kontrolle über das Peer-Set** — der einzige harte Gate im Workflow.

### 6.3 Beispiel-Dialog (Zielbild)

```
User:  Bau mir ein Comps-Set für Adobe.
Agent: Ich habe Adobe Inc. gefunden (CIK 0000796343, Ticker ADBE, SIC 7372 –
       Prepackaged Software). Ich schlage folgende 8 Peers vor:

       - Salesforce (CRM)      → gleiche SIC, ähnliche Größenordnung Umsatz
       - Autodesk (ADSK)       → Design-Software, ähnliches Geschäftsmodell
       - Intuit (INTU)         → SaaS, ähnliche Marge
       ...
       Ausgeschlossen: Microsoft (Umsatz >20× Ziel, Konglomerat).

       Peer-Set so übernehmen, oder anpassen?

User:  Nimm Intuit raus, füg Salesforce-Konkurrent HubSpot dazu.
Agent: [ruft Tools auf] ... Comps-Tabelle erstellt. 7 Peers, FY2025-Daten.
       ⚠ Hinweis: HubSpot hat negatives EBIT → EV/EBIT und P/E nicht aussagekräftig,
         in der Median-Berechnung ausgeschlossen.
       ⚠ Hinweis: Autodesk hat abweichendes Fiskaljahresende (31.01.), Daten sind
         nicht periodengleich.
       → Export: comps_adobe_2026-08-18.xlsx
```

---

## 7. Architektur

### 7.1 Layer-Modell

Bewusst analog zur Sentinel-Architektur (Wiedererkennbarkeit im CV, erprobtes Muster):

```
┌─────────────────────────────────────────────────────┐
│  L6  PRESENTATION      Streamlit Chat UI            │
├─────────────────────────────────────────────────────┤
│  L5  EXPORT            XLSX / CSV / MD + Quellblatt │
├─────────────────────────────────────────────────────┤
│  L4  AGENT             LLM-Orchestrierung, Tool-Loop│
│                        System-Prompt, Konversation  │
├─────────────────────────────────────────────────────┤
│  L3  TOOLS             Tool-Definitionen & Schemas  │
│                        (Schnittstelle Agent↔Domäne) │
├─────────────────────────────────────────────────────┤
│  L2  DOMAIN LOGIC      Peer-Auswahl, Multiples,     │
│                        Normalisierung, QA-Checks    │
├─────────────────────────────────────────────────────┤
│  L1  DATA ACCESS       EDGAR-Client, Market-Client, │
│                        Cache, Rate Limiting         │
└─────────────────────────────────────────────────────┘
```

**Harte Regel:** L4 (Agent) hat keinen direkten Zugriff auf L1. Alle Daten fließen durch L3-Tools.
Das macht das System testbar (L1–L3 ohne LLM testbar) und verhindert halluzinierte Zahlen.

**Zweite harte Regel (v1.5, Festlegung zur UI-Vision, siehe 12.1):** L1–L5 wissen nichts über die UI.
Ausgaben sind strukturiert (Pydantic-Modelle, JSON-serialisierbar); Darstellung, Formatierung und
Interaktion leben ausschließlich in L6. (Die CLI-Ausgabe in `scratch.py` ist ein Test-Hilfsmittel,
kein Teil von L1–L5.)

### 7.2 Der Agent-Loop

```
1. Nutzer-Nachricht → Konversationshistorie
2. LLM-Aufruf mit Tool-Definitionen
3. Falls tool_use → Tool ausführen (deterministischer Python-Code)
4. Tool-Ergebnis zurück in Historie
5. Zurück zu 2, bis LLM eine Textantwort ohne Tool-Aufruf liefert
6. Antwort + ggf. Deliverable rendern
```

Sicherheitsnetze: maximale Iterationszahl pro Turn (z. B. 10), Timeout je Tool, strukturiertes
Fehler-Feedback an den Agenten statt harter Crash.

### 7.3 Tool-Katalog (v1)

| Tool | Input | Output | Notiz |
|---|---|---|---|
| `resolve_company` | Name oder Ticker | CIK, Name, Ticker, SIC, Exchange | Fuzzy-Match, gibt bei Mehrdeutigkeit Kandidatenliste |
| `find_peer_candidates` | CIK, Größenfilter | Liste möglicher Peers + Begründung | SIC-basiert + Größenfilter (siehe 7.4) |
| `get_financials` | CIK, Konzeptliste, Perioden | Fakten mit Provenance | XBRL companyfacts |
| `get_market_data` | Ticker | Kurs, Shares Outstanding, Datum | Externe Quelle, siehe 8.3 |
| `compute_comps_table` | Ziel-CIK, Peer-CIKs, Periode | Vollständige Comps-Struktur | Ruft intern L2 auf |
| `run_quality_checks` | Comps-Struktur | Liste von Warnungen | Ausreißer, Lücken, FY-Mismatch |
| `export_deliverable` | Comps-Struktur, Format | Dateipfad | XLSX / CSV / MD |

**Stand der Umsetzung (v1.5)** — die L2-Funktionen hinter den Tools existieren (Phase 1/2), die Tools
selbst (L3) noch nicht (Phase 3). Bekannte Abweichungen zwischen Katalog und L2-Funktionen:

- `resolve_company`: nur **exakter Ticker** (`EdgarClient.resolve_cik`); Fuzzy-Match und
  Mehrdeutigkeits-Kandidatenliste fehlen.
- `find_peer_candidates`: liefert **keine Begründung** (bewusst Phase 3, LLM-Ranking). Die L2-Funktion
  leitet `calendar_year` seit Phase 3, Schritt 2 aus dem Periodenende des Ziels ab und prüft es gegen den
  Frame (7.3.1); der Parameter entfällt auch im Tool-Schema.
- `get_financials` / `compute_comps_table`: L2 unterstützt die Periodenauswahl seit Phase 3, Schritt 2
  (`build_company_metrics(..., period=PeriodSelector)`, 7.3.1, D9); die Tools (L3) stehen noch aus.
  Keine Konzeptauswahl.
  `get_financials` ohne Konzeptliste: feste v1-Kennzahlen.
- `get_market_data`: Kurs aus Finnhub, Shares aus `companyfacts`; bei **Mehrklassen-Aktien** sind
  Shares so nicht ermittelbar (siehe 8.6, D11).

#### 7.3.1 Periodenauswahl (D9 — in L2 umgesetzt, Phase 3 Schritt 2; Tools stehen aus)

Optionaler Parameter `period` an `get_financials` und `compute_comps_table` (für das Ziel). Ohne
`period` bleibt es beim heutigen Verhalten: jüngstes 10-K (FY) je Unternehmen.

**Umsetzung in L2 (Phase 3, Schritt 2)** — `domain/periods.py`, `domain/metrics.py`, `domain/peers.py`:
- `build_company_metrics(..., period: PeriodSelector | None)`; `PeriodNotAvailable` (Code
  `PERIOD_NOT_AVAILABLE`) nennt die verfügbaren Geschäftsjahresenden (Perioden mit Jahresumsatz in einem
  10-K).
- **Ein Filing je Periode:** Alle Fakten einer Periode stammen aus dem 10-K, das den Umsatz-Anker liefert.
  Steht eine Periode in mehreren 10-Ks (Original und spätere Vergleichswerte, ggf. korrigiert), gilt das
  **zuerst eingereichte** (10-K des Berichtsjahres); ein Wert, den dieses 10-K nicht enthält, gilt als
  nicht vorhanden und wird nicht aus einem späteren Filing aufgefüllt. **Spätere Restatements (korrigierte
  Vergleichswerte in Folge-10-Ks) werden nicht berücksichtigt** — bestätigt Sean, 2026-10-08. Für die
  jüngste Periode ist das folgenlos (es gibt kein späteres 10-K).
- **Historische Perioden** (nicht die jüngste): `CompanyMetrics.is_historical = True`, kein Kurs, keine
  Marktkapitalisierung, kein EV; `compute_multiples` weist alle Multiples mit Grund aus;
  `build_comps_table` wirft `HistoricalValuationNotSupported` (Code `HISTORICAL_VALUATION_NOT_SUPPORTED`).
- **YoY-Wachstum** bezieht sich auf den Anker der gewählten Periode (nicht auf das jüngste Jahr).
- `peers.resolve_calendar_year` leitet `calendar_year` ab, verlangt das Ziel im Frame mit **genau seinem
  Periodenende**, prüft sonst ±1 und wirft `FrameYearUnresolved` (Code `FRAME_YEAR_UNRESOLVED`).
- Gemessen am Golden-Set-Seed (`eval/golden_set/`, 5 Firmen, gepinnt an `period_end` + Accession):
  siehe `tests/unit/test_golden_reproduction.py`.

```python
class PeriodSelector(BaseModel):         # höchstens ein Feld gesetzt; kein Feld = "latest"
    period_end: date | None = None       # KANONISCH: exaktes Geschäftsjahresende
    fiscal_year: int | None = None       # BEST EFFORT, siehe unten
```

- **`period_end` ist kanonisch** und eindeutig. **`fiscal_year` ist Best Effort:** gemeint ist das
  SEC-Feld `fy` des 10-K (`DocumentFiscalYearFocus`), das jeder Filer selbst setzt. Die Benennung ist
  nicht einheitlich und kann bei abweichendem Geschäftsjahresende von der Kalenderjahr-Intuition
  abweichen; dasselbe `fiscal_year` kann bei verschiedenen Firmen verschiedene Zeiträume meinen. Für
  Vergleiche zwischen Unternehmen ist daher `period_end` zu verwenden; die aufgelöste Periode steht
  immer im Ergebnis (jeder Fakt trägt `period_end`).
- **Fehlende Periode:** strukturierter Fehler `PeriodNotAvailable` mit den verfügbaren
  Geschäftsjahresenden — **nie** ein stilles "nächstes Jahr". Ein Peer ohne passende Periode fehlt mit
  Warnung.
- **`peer_period_mode`** (Teil des freigegebenen Vorschlags): `"own_latest"` (Default, heutiges
  Verhalten) oder `"match_target"` — je Peer das Geschäftsjahr, dessen Ende dem des Ziels am nächsten
  liegt (±183 Tage), der Abstand wird als Warnung ausgewiesen.
- **Multiples nur für die aktuelle Periode (v1):** Kurse sind immer "heute". EV, EV/Revenue,
  EV/EBITDA, EV/EBIT, P/E und die Peer-Statistik gibt es daher **nur für die jeweils aktuelle Periode**.
  **Historische Perioden liefern nur Financials, Margen und Wachstum.** Offenes Umsetzungsdetail
  (vor der Implementierung zu bestätigen): ob `compute_comps_table` mit historischer Periode abgelehnt
  wird oder nur Kennzahlen ohne Multiples liefert.
- **Anker (8.6):** Wird eine Periode gewählt, ist der Umsatz dieser Periode der Anker; alle anderen
  Fakten folgen unverändert.
- **`calendar_year` verschwindet aus dem Tool-Schema.** `find_peer_candidates` leitet ihn intern aus
  der Zielperiode ab: **`calendar_year = Jahr(Periodenende − 180 Tage)`**. Gegen den `frames`-Endpunkt
  geprüft (2026-10-04): in welchem `CY…`-Frame taucht das Geschäftsjahr einer Firma tatsächlich auf?
  Messreihe 1 (30 Firmen): "Jahr des Periodenendes" 24/30, "Mitte der Periode" 28/30. Messreihe 2
  (31 Firmen mit Geschäftsjahresende in allen Monaten, gezielt um die Juni-Grenze): Offset 150 Tage
  23/31, 165 Tage 29/31, **180 Tage 31/31**, 183 Tage 25/31. Grenzfälle: Ende 27./28. Juni (SYY, LRCX)
  → CY2025; 30. Juni (MSFT, ADP, KLAC, PG, TEAM) → CY2026; 3. Juli (STX, WDC) → CY2026. Die naheliegende
  "Mehrheit der Tage"-Regel scheitert genau an den 30.-Juni-Firmen. **Vorbehalt:** beobachtetes,
  undokumentiertes SEC-Verhalten; die Marge am 28.–30. Juni beträgt einen Tag. **Absicherung bei der
  Umsetzung:** das Ziel muss selbst im abgeleiteten Frame mit passendem `end` auftauchen, sonst werden
  die Nachbarjahre (±1) geprüft und andernfalls ein Fehler geworfen statt zu raten.

#### 7.3.2 Fehlercodes und typisierte EDGAR-Fehler (Phase 3, Schritt 3)

L1 (`data/edgar_client.py`) wirft typisierte Fehler ohne Tool-Begriffe; L2 wirft die fachlichen Fehler mit
`code`-Attribut. Das Mapping auf die Tool-Codes (vollständige Liste: `docs/decisions/phase-3-plan.md`) macht der
Tool-Layer.

| Quelle | Fehler | Tool-Code | Wiederholbar |
|---|---|---|---|
| L1 | `EdgarUnavailable` (Netz/Timeout/5xx, 2 Wiederholungen mit Backoff) | `UPSTREAM_UNAVAILABLE` | ja |
| L1 | `EdgarRateLimited` (429/403, `retry_after_seconds` aus `Retry-After`) | `UPSTREAM_RATE_LIMITED` | nein |
| L1 | `EdgarDataNotFound` (404, z. B. kein `companyfacts`) | `DATA_NOT_FOUND` | nein |
| L1 | `EdgarHttpError` (sonstige Statuscodes) | `INTERNAL_ERROR` | nein |
| L2 | `PeriodNotAvailable` | `PERIOD_NOT_AVAILABLE` | nein |
| L2 | `HistoricalValuationNotSupported` | `HISTORICAL_VALUATION_NOT_SUPPORTED` | nein |
| L2 | `FrameYearUnresolved` | `FRAME_YEAR_UNRESOLVED` | nein |

**Sicherheit:** Fehlerobjekte, Meldungen, Logs und Traces enthalten keine Request-Header — nie den User-Agent
mit Name und E-Mail — und keine API-Keys; EDGAR-Fehler tragen nur Endpunkt-Pfad und Statuscode und hängen die
ursprüngliche httpx-Ausnahme nicht an (`tests/unit/test_edgar_errors.py`).

**Peer-Suche (Schritt 3):** Der Größenfilter nutzt alle vier Umsatz-Konzepte der Kette, lazy (das Konzept
höchster Priorität zuerst, weitere nur für noch fehlende Kandidaten; je Firma gewinnt die höchste Priorität) und
liefert Zähler (`sic_matches`, `without_ticker`, `not_in_revenue_frame`, `outside_size_range`, `returned`,
geladene Frames). Die Gültigkeit des abgeleiteten `calendar_year` wird gegen den Frame des Konzepts geprüft, das
der Umsatz-Anker des Ziels tatsächlich benutzt (Regressionsfall: NVDA, `Revenues`).

### 7.4 Peer-Identifikation — das methodisch heikelste Stück

Naive SIC-Code-Suche liefert schlechte Peers (SIC-Codes sind grob und teils veraltet). Kaskade
für v1:

1. **Basis:** Alle Unternehmen mit gleichem SIC-Code (4-stellig), die aktuell Filings einreichen.
2. **Größenfilter:** Umsatz zwischen 0,2× und 5× des Zielunternehmens (heuristisch, konfigurierbar).
3. **LLM-Ranking:** Das Modell bewertet die gefilterte Kandidatenliste auf Geschäftsmodell-Ähnlichkeit
   und begründet jeden Vorschlag. **Wichtig:** Das LLM wählt *aus einer vom Code erzeugten Liste
   aus* — es erfindet keine Peers. (Verhindert halluzinierte Unternehmen.)
4. **Mensch bestätigt.**

Das Zusammenspiel „deterministische Vorauswahl → LLM-Urteil → menschliche Freigabe" ist selbst
ein erzählenswertes Design-Pattern.

---

## 8. Datenquellen

### 8.1 Entscheidung: SEC EDGAR als alleinige Finanzdatenquelle

**Warum nicht PE-Daten:** Echte Private-Equity-Daten (Deal-Multiples, Private-Company-Financials,
Cap Tables) liegen praktisch vollständig hinter Paywalls (PitchBook, Preqin, Refinitiv) mit
Jahreskosten im vierstelligen Bereich aufwärts. Kostenlose Alternativen in brauchbarer Qualität
existieren nicht.

**Warum das kein Verlust ist:** Comparable Public Company Analysis ist eine Kernmethode, die
PE-Analysten *ohnehin* täglich anwenden — auch zur Bewertung privater Zielunternehmen wird über
öffentliche Vergleichsunternehmen gearbeitet. Der Skill ist identisch, die Datengrundlage nur
zugänglicher.

### 8.2 SEC EDGAR — technische Eckpunkte

- **Kostenlos, keine Registrierung, keine API-Keys.**
- **Pflicht:** Ein aussagekräftiger `User-Agent`-Header mit Kontaktinformation. Ohne diesen
  werden Requests blockiert (SEC Fair Access Policy).
- **Rate Limit:** max. 10 Requests/Sekunde. → Rate Limiter in L1 zwingend einbauen.
  *Umsetzung (v1.5):* gleitendes Fenster, jeder Versuch inklusive Retries läuft durch `acquire()`.
  Das Limit gilt **pro `EdgarClient`-Instanz** (eigener Limiter) — pro Prozess nur eine Instanz
  verwenden, sonst vervielfacht sich die Rate. SEC-Antworten 429/403 werden **nicht** wiederholt. Fehler sind
  typisiert (siehe 7.3.2); Netzwerkfehler und 5xx werden bis zu zweimal mit Backoff wiederholt.
- Relevante Endpunkte (Stand der Planung — in Session 1 verifizieren):
  - Company-Tickers-Mapping (Ticker → CIK)
  - `submissions` (Filing-Historie je Unternehmen)
  - `companyfacts` (alle XBRL-Fakten eines Unternehmens)
  - `companyconcept` (ein Konzept über die Zeit)
  - `frames` (ein Konzept über *viele* Unternehmen für eine Periode → besonders wertvoll für Comps)
  - EDGAR Full-Text Search

### 8.3 ⚠ Bekannte Datenlücke: Aktienkurse

**EDGAR enthält keine Aktienkurse.** Ohne Kurs kein Market Cap, ohne Market Cap kein Enterprise
Value, ohne EV keine EV-Multiples — also kein Comps-Set. Das ist der kritischste externe
Abhängigkeitspunkt des Projekts.

*Shares Outstanding* ist in EDGAR vorhanden (Cover-Page-Daten der Filings). Fehlt nur der Kurs.

#### Entscheidung D1 (getroffen, Stand 08/2026)

**Primär: Finnhub. Stooq-Fallback verworfen — nie verifiziert, in der Standard-Verdrahtung nicht eingehängt (siehe unten, D7). yfinance: nur optional für lokale Experimente.**

Bedarfsanalyse: ~10 Ticker pro Comps-Lauf, nur End-of-Day-Kurse. Der eigentliche Engpass ist der
Golden-Set-Lauf (10 Ziele × ~10 Peers ≈ 100 Kursabrufe pro Evaluationsdurchgang).

| Quelle | Free-Limit | Stabilität | Entscheidung |
|---|---|---|---|
| **Finnhub** | ~60 Calls/Minute, kein Kreditkarte | Offizielle API mit Key + Doku; Client-Throttle 55/min, 429 mit `Retry-After` (siehe unten) | ✅ **Primär** |
| **Stooq** | keine Limits, **kein API-Key** | CSV-Download, kein echtes API; **nie erfolgreich verifiziert** (siehe unten) | ⛔ **unverifiziert / deaktiviert** |
| **yfinance** | undokumentiert, schwankend | Inoffiziell — Yahoo hat sein API 2017 abgeschaltet; Library nutzt interne Endpunkte ohne Stabilitätsgarantie, bricht gelegentlich | ⚠️ nur optional |
| **Alpha Vantage** | 25 Requests/**Tag** | Stabil, aber Limit zu eng | ❌ als Primärquelle |

**Wildcard Alpha Vantage:** Der Anbieter stellt verifizierten Open-Source- und Bildungsprojekten
unbegrenzte Requests bereit. Dieses Projekt erfüllt beide Kriterien — ein Antrag lohnt sich,
sobald das Repo öffentlich ist. Dann als dritter Provider hinter demselben Interface einhängbar.

#### ⚠ Stooq: unverifiziert und deaktiviert (korrigiert in v1.5; Stand 2026-10-04)

Die Aussage in v1.3/v1.4, `StooqProvider` sei "implementiert und nur blockiert", war zu
optimistisch. Tatsächlicher Befund:

- Der historische Endpunkt `/q/d/l/` antwortet mit einer **JS-Proof-of-Work-Challenge**
  (Anubis-artiger Bot-Schutz) statt mit CSV.
- Der vom Provider genutzte Quote-Endpunkt `/q/l/?s=aapl.us&f=sd2t2ohlcv&h&e=csv` antwortet mit
  **"page does not exist"** — er hat in keiner Session Daten geliefert.
- Das erwartete CSV-Format ist **angenommen**, die Provider-Tests sind gemockt. Der Provider wurde
  also nie gegen echte Stooq-Daten verifiziert.

Konsequenz: `StooqProvider` bleibt als Interface-Platzhalter im Code, ist aber in der
Standard-Verdrahtung **nicht als Fallback eingehängt** (`fallback=None`). **Finnhub ist die einzige
funktionierende Quelle.** Offene Optionen unverändert: (a) Headless-Browser-Bypass (aufwendig,
fragil), (b) Alpha-Vantage-Bildungslizenz vorziehen (D6), (c) dritten keyless Fallback evaluieren
→ **D7** (Abschnitt 13).

#### Finnhub: Throttle und 429 (v1.5)

`FinnhubProvider` drosselt auf **55 Requests/Minute** (Konfiguration `FINNHUB_REQUESTS_PER_MINUTE`,
gleitendes 60-s-Fenster — damit ist auch ein serverseitig fest ausgerichtetes Minutenfenster sicher).
**Jeder HTTP-Versuch zählt**, auch Wiederholungen nach Netzwerkfehlern und nach 429. Bei **429**
wird `Retry-After` (Sekunden oder HTTP-Datum) abgewartet, ohne Header 5 s × Versuchsnummer; maximal
2 Wiederholungen, und verlangt der Server mehr als 120 s, wird nicht blockiert, sondern der Fehler
an `CachedProvider` gegeben ("Marktdaten nicht verfügbar" statt Hänger). Finnhubs `/quote` liefert
den **letzten Kurs** — während der US-Börsenzeit also intraday, sonst den letzten Schlusskurs; das
weicht von der Annahme "End-of-Day" (Abschnitt 4) ab, und der Cache friert den ersten Abruf des Tages ein.

#### Prüfung Finnhub `stock/profile2` für Mehrklassen-Aktien (D11 — nur Befund, nichts eingebaut)

Live-Test im Free-Tier (2026-10-04) für TEAM, GOOGL, META, mit AAPL als Kontrolle: **HTTP 200**, kein
Premium-Fehler. Die Antwort enthält `marketCapitalization` (Mio. USD) und `shareOutstanding` (Mio.).

| Ticker | `shareOutstanding` | `marketCapitalization` | MarketCap ÷ (Kurs × Shares) |
|---|---|---|---|
| TEAM | 253,77 Mio. | 47.562 Mio. | 0,998 |
| GOOGL | 12.230 Mio. | 4.201.005 Mio. | 1,000 |
| META | 2.538,42 Mio. | 1.854.788 Mio. | 1,004 |
| AAPL (Kontrolle) | 14.687,36 Mio. | 4.869.932 Mio. | 0,994 |

- **Plausibilität:** TEAM gegen die Cover Page des 10-K geprüft: Class A 159.005.198 + Class B
  94.133.617 = 253.138.815; `profile2` liegt +0,25 % darüber (anderer Stichtag). AAPL: 14.687 Mio.
  (`profile2`) gegen 14.776 Mio. (EDGAR-Cover) = 0,6 % Differenz. GOOGL und META liegen in der
  erwarteten Größenordnung der Summe aller Klassen, sind aber nicht gegen die Cover Page geprüft.
- **Schwächere Provenance:** keine Accession Number und **kein Stichtag** in der Antwort, die Quelle
  ist ein Vendor und nicht das Filing — das berührt das Primärquellen-Prinzip (2.1). Als Shares-Quelle
  wäre nur eine ausdrücklich gekennzeichnete Ersatzquelle vertretbar.
- **Kosten:** `profile2` + `quote` = 2 Calls pro Ticker; bei 55/min halbiert sich der Durchsatz
  (~27 Ticker/min).
- **Nebenbefund:** Finnhub liefert `x-ratelimit-limit: 60`, `-remaining` und `-reset` mit; der Throttle
  könnte sich daran orientieren (nicht umgesetzt).
- **Entscheidung (Sean, 2026-10-04):** D11 bleibt zurückgestellt.

#### Cache-Design löst das Rate-Limit strukturell

Schlusskurse ändern sich pro Handelstag genau einmal. Der Cache soll deshalb auf
`(ticker, trading_date)` geschlüsselt werden. Folge: **Ein wiederholter Golden-Set-Lauf kostet null
API-Calls.**

*Abweichung im Code (v1.5):* der Schlüssel ist `(ticker, UTC-Kalendertag des Aufrufs)`, nicht der
Handelstag. "Null API-Calls bei Wiederholung" gilt daher nur **innerhalb desselben UTC-Tages**; am
Wochenende oder an Feiertagen entsteht pro Kalendertag ein eigener Eintrag (und ein Call), obwohl
sich der Kurs nicht geändert hat. Ein Schlüssel auf den tatsächlichen Handelstag (Datum aus der
Provider-Antwort) steht noch aus. Der Throttle (oben) ist damit für den ersten Lauf eines Tages nötig,
der Cache löst das Limit-Thema nicht allein.

#### Pflicht-Kapselung

Kursquelle hinter einem eigenen Interface (`MarketDataProvider`) kapseln. Provider-Wechsel darf
genau eine Datei betreffen. **Nicht optional.** Konkrete Struktur:

```
MarketDataProvider (Protocol)  -> liefert PriceQuote
  ├── FinnhubProvider    (primär,  benötigt FINNHUB_API_KEY, Throttle 55/min)
  ├── StooqProvider      (UNVERIFIZIERT, deaktiviert — nicht eingehängt)
  └── CachedProvider     (Decorator, wrapped einen Provider + SQLite-Cache)
```

*Abweichung im Code (v1.5):* die Provider liefern einen schlanken **`PriceQuote`** (Ticker, Kurs,
Datum, Quelle). Der in Abschnitt 10 beschriebene **`MarketSnapshot`** (inkl. `shares_outstanding`
und `market_cap`) wird erst in L2 (`build_company_metrics`) aus Kurs und den EDGAR-Cover-Page-Shares
zusammengesetzt — ohne Shares (z. B. Mehrklassen-Aktien) gibt es keinen Snapshot.

Verhalten: `CachedProvider` → Cache-Hit? zurückgeben. Sonst Primär-Provider. Bei Fehler/429 →
automatisch Fallback-Provider. Bei Fehlschlag beider → `MarketSnapshot` = None und
QualityWarning erzeugen, **niemals** einen Wert schätzen.

### 8.4 ⚠ Bekannte Datenlücke: EBITDA

EBITDA ist keine GAAP-Kennzahl und existiert nicht als eigenständiges XBRL-Konzept. Approximation:
`EBITDA ≈ Operating Income + Depreciation & Amortization`. D&A stammt üblicherweise aus der
Kapitalflussrechnung. Konsequenzen:

- EBITDA muss **immer als approximiert gekennzeichnet** werden.
- Bei fehlenden D&A-Daten: EBITDA als nicht verfügbar ausweisen, **nicht schätzen**.
- Diese Transparenz ist ein Feature, kein Mangel — sie zeigt Verständnis für die Datenlage.

*Messung (v1.5, 30 Ticker):* EBITDA ist nur bei **21/30** Firmen ermittelbar. Bei MSFT, GOOGL, ORCL,
INTU, CRWD, TXN, WDAY und AMD ist kein *Gesamt*-D&A-Konzept getaggt, nur Teilposten
(`Depreciation`, `AmortizationOfIntangibleAssets`, `OtherDepreciationAndAmortization`, ...). Ein
Zusammensetzen aus Teilposten ist **nicht sicher**: Bei ADSK ergibt Depreciation + Amortization-Posten
193 Mio. gegenüber 195 Mio. gemeldetem Gesamt-D&A, bei CDNS 217 Mio. gegenüber 228 Mio. (−5 %), und bei
MSFT ist `Depreciation` (34,3 Mrd.) vermutlich schon die ganze Cashflow-Zeile, sodass
`AmortizationOfIntangibleAssets` doppelt zählen würde. → **Entschieden (D12): EBITDA bleibt `None`,
es werden keine Teilposten addiert.**

### 8.5 Der PE-Bezug bleibt erhalten

Auch ohne PE-Daten ist der PE-Bezug erzählbar:
- Comps sind Standard-Handwerkszeug jedes PE-Analysten.
- Nach Phase 5 kann **Precedent Transactions** aus M&A-bezogenen Filings (8-K, Merger-Proxies) ein
  weiterer Baustein werden (Reihenfolge der Bausteine offen, D13; siehe 1.4) —
  dort stehen reale Transaktionsdaten (Kaufpreis, Struktur), die sonst nur kostenpflichtig
  verfügbar sind. Das ist ein echtes Alleinstellungsmerkmal für später.

### 8.6 Periodentreue, Konzept-Ketten und Schuldenregel (Härtungs-Session, 2026-10-04)

#### Befund: veraltete Fakten in den Phasen 1 und 2

Der Extraktor aus Phase 1 nahm je Kennzahl das **erste Konzept der Fallback-Kette mit irgendeiner
Historie** und davon den jüngsten Wert — ohne zu prüfen, ob dieser Wert zur Berichtsperiode des
Unternehmens gehört. Firmen wechseln XBRL-Tags; das alte Tag bleibt mit Altdaten in `companyfacts`.
Messung über 30 Ticker (AAPL, MSFT, GOOGL, AMZN, META, NVDA, ORCL, CRM, ADBE, INTU, ADSK, CDNS, SNPS,
NOW, WDAY, TEAM, HUBS, PANW, FTNT, DDOG, ZS, CRWD, IBM, CSCO, QCOM, TXN, AMD, PYPL, EBAY, UBER):

- **8/30 Firmen** (ADBE, ADSK, AMD, EBAY, GOOGL, NVDA, PYPL, WDAY) hatten Fakten aus der falschen Periode.
- Beispiele: NVDA-**Umsatz** FY2022 neben EBIT 2026; PYPL-Umsatz 2019; GOOGL-Umsatz ein Jahr zurück;
  ADSK-D&A aus **2016** (EBITDA = EBIT 2026 + D&A 2016); **ADBE-Gesamtschuld aus 2019**
  (4,14 statt 6,21 Mrd.); EBAY-Schuld 2023.
- **Folge:** Die in Phase 1/2 berichteten Live-Läufe waren für das Ziel **Adobe** fehlerhaft (EV und
  alle EV-Multiples auf Basis der 2019er Schuld). AAPL, GOOGL, CSCO u. a. wichen ebenfalls ab (siehe
  Schuldenregel). Die Aussage "Phase 1/2 live verifiziert" galt nur für die damals getesteten Firmen.
- Zusätzlich war `LongTermDebtAndCapitalLeaseObligations` fälschlich als Gesamtschuld geführt; laut
  Definition ist es **nicht-kurzfristig**.

#### Prinzip: Periodenanker

Die Berichtsperiode eines Unternehmens wird **einmal** festgelegt: Ende des jüngsten Jahresumsatzes,
wobei über die ganze Umsatz-Kette das Konzept mit dem **jüngsten Periodenende** gewinnt (die
Kettenreihenfolge entscheidet nur bei Gleichstand). **Jeder andere Fakt muss exakt auf dieses
Periodenende fallen** — ein Wert aus einer früheren Periode gilt als *nicht vorhanden*, nicht als
Ersatz. Cover-Page-Shares tragen das Einreichdatum und werden stattdessen an **dasselbe 10-K-Filing**
(Accession Number) wie der Umsatz gebunden. YoY-Wachstum gibt es nur zwischen zwei direkt
aufeinanderfolgenden Geschäftsjahren (350–380 Tage Abstand der Periodenenden).

#### Messung vorher / nachher (30 Ticker)

| | Baseline (v1.4) | v1.5 |
|---|---|---|
| Firmen mit Fakten aus falscher Periode | 8 / 30 | **0 / 30** |
| `total_debt` verfügbar | 21 / 30 (teils veraltet) | 20 / 30 exakt (periodenrichtig) **+ 9 / 30 als Untergrenze mit Flag (D10)**, 1 / 30 fehlt (HUBS) |
| `ebitda` verfügbar | 23 / 30 (teils veraltet) | 21 / 30 (periodenrichtig) |
| `shares` verfügbar | 25 / 30 (teils veraltet) | 24 / 30 (periodenrichtig) |

Der nominelle Rückgang ist gewollt: die fehlenden Werte waren zuvor veraltet oder falsch. Größte
Korrekturen: GOOGL-Schuld 10,9 → 49,1 Mrd.; ADBE 4,14 → 6,21; CSCO 22,9 → 29,5; AAPL 90,7 → 98,7
(Commercial Paper); NVDA/PYPL/GOOGL-Umsatz jetzt aus dem richtigen Jahr.

#### Schuldenregel

Gelesen aus den Definitionen in `companyfacts` (nicht aus der Erinnerung) und an Zahlen geprüft:

| Konzept | Bedeutung (Definition) | Verwendung |
|---|---|---|
| `DebtLongtermAndShorttermCombinedAmount` | LT inkl. current maturities + ST | Gesamtschuld |
| `DebtAndCapitalLeaseObligations` | ST + LT, inkl. Leasing | Gesamtschuld (nachrangig, Leasing enthalten) |
| `LongTermDebt` | LT-Schuld inkl. current maturities (= Noncurrent + Current: AAPL, MSFT, NVDA, CRM, SNPS, INTU exakt) | Gesamtschuld |
| `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities` | LT inkl. Leasing, inkl. current | Gesamtschuld (nachrangig) |
| `LongTermDebtNoncurrent` | **ohne** current maturities | Teil — exakt nur mit explizit gemeldetem kurzfristigem Anteil, sonst **Untergrenze** (D10) |
| `DebtCurrent` | ST-Debt + current maturities | Ergänzung zu Noncurrent |
| `LongTermDebtCurrent` | nur current maturities der LT-Schuld | Ergänzung zu Noncurrent |
| `CommercialPaper` | Commercial Paper | addiert, außer erkennbar enthalten |
| `ShortTermBorrowings` | uneinheitlich getaggt (IBM: current maturities, schon im Total) | **nie addiert** |
| `UnsecuredLongTermDebt`, `SecuredLongTermDebt`, `LongTermNotesPayable`, `LongTermNotesAndLoans`, `ConvertibleLongTermNotesPayable`, `ConvertibleDebtNoncurrent` | Klassen-/Instrumentkonzepte, deren Definition den kurzfristigen Anteil **ausdrücklich ausschließt** ("excluding current portion", "net of the amount due in the next twelve months") | nie als Gesamtschuld; seit D10 nur als **Untergrenze** |
| `SeniorNotes`, `ConvertibleNotesPayable`, `DebtInstrumentCarryingAmount` | Klassen-/Instrumentkonzepte ohne ausdrücklichen Ausschluss; `DebtInstrumentCarryingAmount` kann ein Einzelinstrument meinen | **nicht akzeptiert** |
| `LongTermDebtAndCapitalLeaseObligations` | nicht-kurzfristig, inkl. Leasing | nicht als Total (früher fälschlich) |

**Regel:** Ein Konzept zählt nur dann als Gesamtschuld, wenn seine Definition die Gesamtheit abdeckt —
oder wenn jeder Teil, den seine Definition ausschließt, **explizit gemeldet** ist (auch als 0).
Fehlt ein ausgeschlossener Teil, ist die **exakte** Gesamtschuld nicht ermittelbar — es bleibt `None`
oder, bei ausdrücklichem Ausschluss des kurzfristigen Anteils, die gekennzeichnete Untergrenze (unten, D10). "Explizit" heißt:
Wert für **genau diese Periode**; ein 0-Wert aus dem Vorjahr zählt nicht (TEAM). Die Provenance nennt
alle verwendeten Konzepte (`LongTermDebt+CommercialPaper`) und Accession Numbers. Leasing ist nur
enthalten, soweit das gewählte Aggregat es enthält (IBM, EBAY) — der Konzeptname zeigt es.

Bekannte Restabweichungen: AMZN: `ShortTermBorrowings` (0,46 Mrd., < 1 %) fehlen bewusst.

**Untergrenze statt `None` (D10, entschieden und implementiert 2026-10-04).** Lässt sich keine exakte
Gesamtschuld bilden, wird dennoch ein Wert ausgewiesen — aber nur, wenn die **Definition** eines
vorhandenen Konzepts den kurzfristigen Anteil **ausdrücklich ausschließt** (Konzeptliste: siehe Tabelle,
im Code `DEBT_LOWER_BOUND_CONCEPTS`). Jedes dieser Konzepte ist eine Teilmenge der Gesamtschuld; das
**Maximum** der für genau diese Periode gemeldeten Werte ist daher eine gültige Untergrenze. Das Muster
entspricht der EBITDA-Approximation (8.4): nie stillschweigend, immer gekennzeichnet:

- Feld `CompanyMetrics.total_debt_is_lower_bound`; daraus `enterprise_value_is_lower_bound`,
  `CompanyMultiples.ev_multiples_are_lower_bound` (EV/Revenue, EV/EBITDA, EV/EBIT; **P/E nie
  betroffen**) und `MultipleStatistics.lower_bound_tickers` (die Statistik mischt dann exakte Werte
  und Untergrenzen).
- `QualityWarning` mit Severity `warning` (`affected_field = total_debt`), die das verwendete Konzept
  nennt und festhält, dass EV und EV-Multiples zu niedrig sind.
- **Export-Anforderung (Phase 4):** der Export muss Untergrenzen (und approximiertes EBITDA) sichtbar
  kennzeichnen und die zugehörigen Warnings mitführen. Es gibt noch keinen Export; bis dahin kennzeichnet
  die CLI (`scratch.py`) die Werte mit `*` und weist "x/N Peers, davon y mit Flag" aus.
- Eine exakte Summe trägt nie das Flag. Die Provenance bleibt lückenlos (das Konzept steht in
  `source_facts`).

#### Verbleibende Lücken (30 Ticker, nach Härtung und D10)

- **`total_debt`:** 20 exakt; **9 als Untergrenze** (CDNS, NOW, WDAY, TEAM, PANW, DDOG, ZS, CRWD, PYPL);
  **1 fehlt** (HUBS — gar kein Schuldkonzept getaggt). Sensitivität am Beispiel CDNS: selbst die
  vollständige `UnsecuredLongTermDebt` (2,48 Mrd.) wäre nur **2,6 % des EV**.
- **`shares` fehlt (6):** CRWD, DDOG, GOOGL, META, TEAM, WDAY — durchweg **Mehrklassen-Aktien**: die
  Cover-Page-Fakten sind dimensional (Class A/B) und stehen nicht in `companyfacts`. Die Zahlen stehen
  im Filing selbst (`R1.htm`, z. B. TEAM: Class A 159.005.198, Class B 94.133.617). → **D11**
  (zurückgestellt; `profile2`-Befund in 8.3).
- **`ebitda` fehlt (9):** AMD, CRWD, GOOGL, IBM (kein EBIT), INTU, MSFT, ORCL, TXN, WDAY — bleibt `None`
  (**D12 entschieden**, keine Teilposten).

---

## 9. Tech Stack

| Ebene | Wahl | Begründung |
|---|---|---|
| Sprache | Python 3.11+ | Konsistenz mit Sentinel |
| Agent-Framework | **Kein Framework** — eigener Tool-Loop gegen die LLM-API | Lerneffekt, volle Kontrolle, kein Framework-Overhead; im Interview deutlich besser erklärbar als "ich habe LangChain benutzt" |
| LLM | Anthropic API (Claude) | Tool-Use nativ, passt zu deinen MCP-/Agent-Zertifikaten |
| UI | Streamlit | Bereits beherrscht, schnelle Iteration |
| Datenzugriff | `httpx` | Sync + async fähig, moderne API |
| Marktdaten | Finnhub REST (einzige funktionierende Quelle) | Siehe 8.3 — direkt per HTTP, keine SDK-Abhängigkeit nötig; `StooqProvider` nur als deaktivierter Platzhalter (D7) |
| Datenmodelle | `pydantic` | Validierung + saubere Tool-Schemas |
| Tabellen | `pandas` | — |
| Export | `openpyxl` (XLSX), stdlib (CSV/MD) | — |
| Persistenz/Cache | SQLite | Analog Sentinel, kein Server nötig |
| Tests | `pytest` | Analog Sentinel |
| Config | `.env` + `pydantic-settings` | Keine Keys im Code |

**Kostenrahmen:** EDGAR kostenlos, Kursdaten kostenlos, einzige laufende Kosten sind LLM-API-Calls.
Bei diesem Nutzungsprofil (Einzelnutzer, wenige Analysen/Tag) sind das geringe Beträge.
Kostenhebel: aggressives Caching von EDGAR-Daten, kompakte Tool-Ergebnisse (keine Roh-JSONs ins
Kontextfenster kippen), günstigeres Modell für einfache Schritte.
*Stand v1.6.4: `companyfacts` werden **nicht** gecacht (jeder Lauf lädt sie neu); gecacht werden Kurse (siehe
8.3) und die großen `frames`-Abrufe (Speicher je Client + SQLite, 7 Tage Gültigkeit). `pandas` und `openpyxl` sind deklariert, aber bis Phase 4 ungenutzt.*

### Entwicklungsumgebung (Befund 2026-10-04)

**Projekt und venv nicht in einem iCloud-synchronisierten Ordner ablegen** (z. B. `~/Desktop` bei aktivierter
"Schreibtisch & Dokumente"-Synchronisierung). Belegt gemessen: Im iCloud-Desktop setzt macOS binnen
~2 Sekunden das Flag `hidden` auf jede Datei und jeden Ordner mit Punkt-Namen (`.venv` samt Inhalt,
`.env`, `.gitignore`, `.pytest_cache`); in `/private/tmp` und `~` passiert das nicht. Python 3.13 überspringt
`.pth`-Dateien mit diesem Flag **ohne Fehlermeldung** — der Editable-Install (`pip install -e .`)
funktioniert dann nicht mehr, `python -m scout_research...` scheitert mit `ModuleNotFoundError`, während
`pytest` (`pythonpath = ["src"]` in `pyproject.toml`) weiterläuft. Das Flag kehrt nach `chflags -R nohidden .venv`
von selbst zurück. Abhilfe: Projekt außerhalb von iCloud ablegen (venv neu anlegen — venvs sind nicht
verschiebbar) oder vorübergehend `PYTHONPATH=src` setzen. Zusätzlich liegt die SQLite-Kursdatei im
Projektordner; SQLite-Dateien in synchronisierten Ordnern sind ein Korruptionsrisiko.

---

## 10. Datenmodell (Kern-Entitäten)

Konzeptionell — konkrete Pydantic-Implementierung in Session 1:

```
Company
  cik, ticker, name, sic_code, sic_description, fiscal_year_end

FinancialFact                      ← Träger des Provenance-Prinzips
  cik, concept, value, unit, period_start, period_end,
  fiscal_year, fiscal_period, form_type, accession_number,
  filed_date, retrieved_at

MarketSnapshot
  ticker, price, shares_outstanding, market_cap,
  as_of_date, source

CompanyMetrics
  company, period, revenue, ebit, ebitda (approximated: bool),
  net_income, total_debt, cash, enterprise_value,
  margins, growth_rates, source_facts[]   ← Rückverweis auf FinancialFacts

CompsTable
  target: CompanyMetrics
  peers: CompanyMetrics[]
  multiples: dict
  statistics: {min, median, mean, max, count_included}
  warnings: QualityWarning[]
  created_at, period_basis

QualityWarning
  severity (info | warning | critical), company, message, affected_field
```

**Design-Regel:** `CompanyMetrics` referenziert immer die zugrundeliegenden `FinancialFact`s.
Es darf keinen Wert im Output geben, der nicht auf mindestens einen Fact zurückführbar ist.

**Umsetzung und Abweichungen (v1.5).** Die obige Skizze ist konzeptionell; der Code weicht bewusst ab:

- `CompanyMetrics`: statt `period` die Felder `period_end` + `fiscal_year`; `ebitda` + `ebitda_approximated`
  getrennt; zusätzlich `total_assets`, `market` (optional) und `enterprise_value`. Alle Werte gehören
  zur Periode des Umsatz-Ankers (8.6). Untergrenzen-Kennzeichnung (D10): `total_debt_is_lower_bound`
  und `enterprise_value_is_lower_bound`.
- `CompsTable`: statt `multiples: dict` die typisierten Listen `target_multiples` / `peer_multiples`
  (`CompanyMultiples`, nicht aussagekräftige Multiples als `None` + `excluded_reasons`) und `statistics`
  als Liste von `MultipleStatistics` (je Multiple min/median/mean/max/count_included/excluded_tickers
  und `lower_bound_tickers`); `CompanyMultiples.ev_multiples_are_lower_bound` kennzeichnet EV-Multiples
  auf Basis einer Schuld-Untergrenze.
- `CompanyMultiples` trägt keine eigenen Fact-Referenzen — die Provenance läuft über `CompsTable.target`
  / `.peers` und deren `source_facts`. Zusammengesetzte Fakten (Schuld) nennen alle Konzepte im `concept`
  (`LongTermDebt+CommercialPaper`) und alle Accession Numbers (`A+B`).
- `MarketSnapshot` entsteht in L2 aus `PriceQuote` (L1) + Cover-Page-Shares (siehe 8.3).

---

## 11. Evaluation — wie wird "gut" gemessen

Ohne Evaluation ist es ein Demo-Projekt. Mit Evaluation ist es Engineering. Das ist einer der
größten Differenzierungspunkte gegenüber typischen Studentenprojekten.

### 11.1 Golden Set

5–10 Zielunternehmen aus verschiedenen Branchen, für die **manuell** ein Referenz-Comps-Set
erstellt wird (Peers + geprüfte Kennzahlen). Aufwand: einige Stunden — lohnt sich absolut, weil
alle folgenden Änderungen dagegen messbar werden.

### 11.2 Metriken

| Metrik | Definition | Zielwert v1 |
|---|---|---|
| **Peer Overlap** | Jaccard-Ähnlichkeit vorgeschlagenes vs. Referenz-Peer-Set | ≥ 0,5 |
| **Numerische Korrektheit** | Anteil Kennzahlen innerhalb Toleranz zur Referenz | ≥ 95 % |
| **Provenance-Vollständigkeit** | Anteil Ausgabewerte mit vollständiger Quellangabe | 100 % (hart) |
| **Halluzinationsrate** | Anzahl Zahlen/Unternehmen ohne Quellbeleg | 0 (hart) |
| **Coverage** | Anteil Anfragen, die ohne manuellen Eingriff durchlaufen | ≥ 80 % |
| **Latenz** | Zeit bis fertige Tabelle | < 3 Min |
| **Kosten/Analyse** | LLM-Kosten pro vollständigem Comps-Lauf | tracken, nicht limitieren |

### 11.3 Testebenen

- **Unit (L1–L2):** Multiples-Berechnung, Normalisierung, QA-Checks — vollständig ohne LLM testbar.
- **Integration (L3):** Tools gegen aufgezeichnete EDGAR-Responses (Fixtures, keine Live-Calls).
- **Agent-Level (L4):** Golden-Set-Läufe, manuell bewertet + automatisierte Metriken.
- **Regression:** Golden Set läuft vor jedem größeren Merge.

### 11.4 Golden-Set-Seed und bekannte Abweichungen (Stand 2026-10-08)

**Seed.** `eval/golden_set/{aapl,msft,nvda,adsk,cdns}.yaml`: von Hand gegen die Abschlüsse der 10-Ks geprüfte
Werte (GuV, Bilanz, Kapitalflussrechnung, Schuldennote, Deckblatt) — nicht aus `companyfacts`, nicht aus
R-Seiten, nicht aus dem Extraktor. Der Seed ist das Messlineal und an `period_end` und Accession des 10-K
gepinnt, nie an "jüngstes 10-K". Einheit: Millionen USD (Shares: Stück; CDNS berichtet in Tausend, der Seed
führt Millionen). `total_debt` = ausgewiesene Finanzschulden ohne Leasing, `cash` = nur "Cash and cash
equivalents" (Marketable Securities und Leasingverbindlichkeiten stehen in `notes`). CDNS prüft nur `total_debt`.
Vergleich: `python eval/seed_compare.py`; Test: `tests/unit/test_golden_reproduction.py` (Toleranz 0, außer
Seed-Feld `tolerance_abs`).

**Bekannte Abweichungen** werden nicht weggetestet, sondern im Seed unter `known_deviations` je Feld mit
`seed_value`, `expected_extractor_value`, `class` (a Definitionsunterschied, b Extraktorfehler, c möglicher
Seedfehler) und `reason` gepinnt. Der Test schlägt an, wenn der Extraktor vom Pin abweicht — wird die
Abweichung behoben, ist der Eintrag zu entfernen; ändert sich der Wert, ist neu zu bewerten.

| Firma, Feld | Seed | Extraktor | Behandlung |
|---|---|---|---|
| ADSK `total_debt` | 2.483 Mio. (Buchwert, Bilanz) | 2.500 Mio. | **Bekannte Abweichung, Klasse (a).** ADSK taggt den Nominalwert unter `us-gaap:LongTermDebt`, dessen Definition den Buchwert ("after unamortized discount") verlangt; der Buchwert steckt in `LongTermNotesPayable`. Kein Fallback auf ein anderes Konzept (Sean, 2026-10-08). |
| CDNS `total_debt_is_lower_bound` | false | true | **Bekannte Abweichung, Klasse (a).** Wert stimmt; D10 markiert `UnsecuredLongTermDebt` als Untergrenze, weil der kurzfristige Anteil nicht gemeldet ist. Der Seed weiß aus dem 10-K, dass es keinen gibt. D10-Logik unverändert. |
| NVDA `shares_outstanding` | 24.304 Mio. (Bilanz) | 24,3 Mrd. (Deckblatt) | **Keine Abweichung:** Seed-Feld `tolerance_abs` = 50 Mio., weil das Deckblatt auf 0,1 Mrd. gerundet ist. |
| MSFT `d_and_a` | nicht ausgewiesen (`absent_reason`) | `None` | **Keine Abweichung.** Kein Gesamtposten D&A; die Kapitalflussrechnung nennt "Depreciation, amortization, and other" 38.534 Mio. (enthält Sonstiges), die Teilposten (34.300 + 4.700 = 39.000) stimmen nicht überein. Der Wert steht als Referenz in `notes`; `None` ist korrekt (D12). |

Außerdem gilt für ADSK `shares_outstanding` eine Toleranz von ±1 Mio. (Deckblatt nur auf Mio. gerundet) und
für ADSK `d_and_a` ("Depreciation, amortization, and accretion") stimmen Seed und Extraktor überein.

---

## 12. Roadmap

### Phase 0 — Setup (1 Session)
- Repo-Struktur, venv, Dependencies, `.env`-Handling
- EDGAR-Client-Grundgerüst mit User-Agent + Rate Limiter
- Erster erfolgreicher Abruf: Ticker → CIK → companyfacts
- **DoD:** Ein Skript zieht reproduzierbar Umsatz eines Unternehmens inkl. Quellangabe

### Phase 1 — Datenfundament (2–3 Sessions)
- Pydantic-Modelle, SQLite-Cache
- `MarketDataProvider`-Interface + erste Implementierung
- Kennzahlen-Extraktion inkl. EBITDA-Approximation und Fehlerbehandlung
- **DoD:** Für ein beliebiges Unternehmen liegen alle v1-Kennzahlen strukturiert vor

### Phase 2 — Comps-Engine (2 Sessions)
- Peer-Kandidatensuche (SIC + Größenfilter)
- Multiples-Berechnung, Statistiken, Ausreißerlogik
- Quality Checks
- **DoD:** Manuell übergebenes Peer-Set → vollständige Comps-Tabelle in Python
- **Nachtrag v1.5:** Die Härtungs-Session vor Phase 3 hat Fehler in der Datenextraktion aus Phase 1
  korrigiert (veraltete Fakten, Schuldenregel — siehe 8.6). Die in Phase 1/2 berichteten
  Live-Ergebnisse (insbesondere EV/Multiples für Adobe) wurden dadurch teilweise ungültig. Live-Gate der
  Härtung ("≥ 4 von 5 Peers mit EV-Multiples", Adobe gegen INTU, ADSK, CDNS, TEAM, CRM): zunächst **3/5
  (verfehlt)**; nach Umsetzung von D10 **4/5, davon 1 mit Flag (CDNS, Untergrenze)**. TEAM bleibt ohne
  Shares (D11 zurückgestellt).

### Phase 3 — Agent-Schicht (2–3 Sessions)
- Tool-Definitionen, Agent-Loop, System-Prompt
- Peer-Ranking durch LLM (nur Auswahl, kein Erfinden)
- Fehler-Feedback-Mechanik
- **DoD:** Natürlichsprachlicher Auftrag → korrekte Comps-Tabelle

### Phase 4 — Interface & Export (1–2 Sessions)
- Streamlit-Chat mit Peer-Bestätigungs-Gate
- XLSX-Export inkl. separatem Quellenblatt
- Export kennzeichnet **approximiertes EBITDA und Untergrenzen** (`total_debt`, EV, EV-Multiples)
  sichtbar und führt die zugehörigen Warnings mit (siehe 8.4, 8.6)
- **DoD:** Kompletter Workflow ohne Terminal nutzbar

### Phase 5 — Evaluation & Härtung (1–2 Sessions)
- Golden Set aufbauen, Metriken automatisieren
- README, Architekturdiagramm, Demo-Material
- **DoD:** Messbare Qualitätsaussagen möglich, Projekt vorzeigbar

### Danach (nach Phase 5, Entscheidung vom 2026-10-05 — siehe 1.4)
Neue Bausteine, jeweils mit eigenem Golden Set und eigener Evaluation, bevor sie als fertig gelten;
**Reihenfolge von 2–4 offen (D13):**
1. Trading Comps (Phase 0–5, Kern)
2. Precedent Transactions aus 8-K und Merger-Proxies
3. DCF-Unterstützung (Annahmen immer vom Menschen)
4. LBO-/Returns-Screening (IRR, MoM; Annahmen immer vom Menschen)
5. Optional: Due-Diligence-Support (Red Flags in 10-Ks)

Daneben: **MCP-Server** für die Arcticon-Anbindung (Zeitpunkt und Zuschnitt offen, D15) und der
**Consulting-One-Pager** als Ausgabeform nach Phase 4 (Format offen, D14). Multi-Perioden-Trends und
Sektor-Screening bleiben Kandidaten ohne Zuordnung. Das frühere Memo-Entwurfsmodul entfällt als
eigener Baustein; der One-Pager deckt den Bedarf ab. Eine Framework-Engine ist kein Ziel (1.4).

### 12.1 UI-Vision (Phase 6)

**Ziel.** Eine moderne, professionelle Oberfläche, leicht "fancy", aber finance-tauglich. Die
**Lesbarkeit der Daten hat Vorrang** vor Effekten.

**Werkzeug.** Entwurf und Prototyp mit **Claude Design**. Offen: Exportformat und Übernahme in den
Code (→ D8).

**Prinzipien.**
- Ruhige Palette; Hell- und Dunkelmodus.
- Dichte, gut lesbare Tabellen mit tabellarischen Ziffern.
- Jede Zahl ist per Klick/Hover bis zur **Provenance** aufklappbar: CIK, Accession Number,
  XBRL-Konzept, Periode.
- Qualitätswarnungen sind nach Severity (info / warning / critical) klar getrennt.
- Die Peer-Bestätigung ist ein eigener, deutlicher Schritt.
- Export mit einem Klick.

**Kernscreens.** Chat/Arbeitsbereich · Peer-Auswahl · Comps-Tabelle mit Provenance-Drilldown · Verlauf.

**Technik (D8).** Streamlit reicht für Phase 4. Für Phase 6 ist wahrscheinlich ein eigenes Frontend
(z. B. Next.js/TypeScript) über eine API-Schicht nötig.

**Regel ab jetzt.** L1–L5 wissen nichts über die UI; Ausgaben sind strukturiert (siehe 7.1). Das ist
heute schon die Voraussetzung dafür, dass die Provenance-Drilldowns und die Kennzeichnung von
Näherungen/Untergrenzen (8.4, 8.6) später ohne Umbau darstellbar sind.

**Nicht-Ziel.** Kein UI-Aufwand vor abgeschlossener Evaluation (Phase 5). Gemeint ist die
Phase-6-Oberfläche; die Streamlit-Oberfläche aus Phase 4 bleibt wie geplant.

---

## 13. Offene Entscheidungen & Risiken

| # | Offene Frage | Status |
|---|---|---|
| ~~D1~~ | ~~Welche Kursdatenquelle?~~ | ✅ **Entschieden:** Finnhub primär. Der ursprünglich vorgesehene Stooq-Fallback ist unverifiziert und deaktiviert (siehe 8.3, D7). |
| ~~D2~~ | ~~Peer-Suche über `frames`-Endpunkt oder eigener SIC-Index?~~ | ✅ **Entschieden:** `browse-edgar` (SIC-Filter, paginiert) + `frames` (Bulk-Größenfilter), kein selbst gepflegter Index nötig (siehe 7.4, `domain/peers.py`) |
| D3 | Kalenderjahr- oder Fiskaljahr-Normalisierung bei abweichenden FY-Enden? | Teilweise: Abweichungen werden erkannt und geflaggt (nur Warnungen, kein Ausschluss): Fiskaljahresende weicht ab (gefalteter Abstand der Periodenenden > ±14 Tage) und veraltetes 10-K (Peer-Ende ≥ 300 Tage vor dem des Ziels) — `check_fiscal_year_mismatch`, `check_stale_period`. Echte Normalisierung (z. B. Trailing-Twelve-Months-Angleichung) bleibt offen. |
| ~~D4~~ | ~~Ausreißer-Definition (IQR-basiert? feste Schwellen?)~~ | ✅ **Entschieden:** IQR-basiert (Tukey-Fences, 1,5×), ab n≥4 Datenpunkten je Multiple — siehe `domain/quality.py` |
| ~~D5~~ | ~~Projektname final~~ | ✅ **Entschieden:** **Scout Research** (siehe Änderungshistorie 1.2; der Tabelleneintrag war veraltet) |
| D6 | Alpha-Vantage-Bildungslizenz beantragen, sobald Repo öffentlich? | offen — Phase 5 |
| D7 | Stooq ist unverifiziert/deaktiviert (nie funktionierender Fallback, siehe 8.3) — Ersatz nötig? | offen — vor Phase 5, Finnhub trägt v1 allein |
| D8 | UI-Vision (Phase 6): Entwurf/Prototyp mit **Claude Design** — Exportformat und Übernahme in den Code offen; Frontend-Technik: Streamlit (Phase 4) vs. eigenes Frontend (z. B. Next.js/TypeScript) über eine API-Schicht (Phase 6). Siehe 12.1. | offen — vor Phase 6 |
| ~~D9~~ | ~~Periodenauswahl: optionaler `period`-Parameter~~ | ✅ **Schema freigegeben** (7.3.1): `period_end` kanonisch, `fiscal_year` Best Effort; Multiples nur für die aktuelle Periode, historische Perioden nur Financials/Margen/Wachstum; `calendar_year` aus dem Tool-Schema, Ableitung `Jahr(Periodenende − 180 Tage)` gegen `frames` geprüft. **In L2 umgesetzt** (Phase 3, Schritt 2, siehe 7.3.1); die Tools stehen aus. |
| ~~D10~~ | ~~Gesamtschuld bei fehlendem kurzfristigem Anteil~~ | ✅ **Entschieden und implementiert:** gekennzeichnete Untergrenze (`total_debt_is_lower_bound`), nur für Konzepte mit ausdrücklichem Ausschluss des kurzfristigen Anteils; sichtbar in EV, EV-Multiples, Statistik und als QualityWarning; Export-Kennzeichnung ist Phase-4-Anforderung (8.6). |
| D11 | Shares bei Mehrklassen-Aktien (GOOGL, META, TEAM, DDOG, CRWD, WDAY): Cover Page (`R1.htm`) parsen oder Finnhub `profile2` nutzen? | **zurückgestellt** (Sean, 2026-10-04). `profile2` ist im Free-Tier verfügbar und plausibel, aber ohne Stichtag/Accession (8.3). Nichts eingebaut. |
| ~~D12~~ | ~~EBITDA ohne Gesamt-D&A-Konzept~~ | ✅ **Entschieden:** bleibt `None`, keine Teilposten addieren (8.4). |
| D13 | Reihenfolge nach den Comps: Precedent Transactions, DCF, LBO (siehe 1.4) | offen, nach Phase 5 |
| D14 | Format des Consulting-One-Pagers (Markdown, DOCX, PDF, Folie) | offen, vor Phase 4 klären |
| D15 | Zeitpunkt und Zuschnitt des MCP-Servers für die Arcticon-Anbindung | offen, nach Phase 5 |

| Risiko | Wahrscheinlichkeit | Gegenmaßnahme |
|---|---|---|
| Kursdatenquelle bricht weg | **eingetreten (Stooq war nie verifiziert)** | Provider-Interface + Cache; Finnhub trägt v1 allein (D7); Throttle 55/min und `Retry-After`-Behandlung (8.3) |
| XBRL-Konzepte uneinheitlich zwischen Unternehmen | **hoch — eingetreten** (8/30 Firmen mit Fakten aus falscher Periode, siehe 8.6) | Periodenanker, Konzept mit jüngster Periode gewinnt, Schuldenregel, explizites "nicht verfügbar" statt Schätzung |
| Mehrklassen-Aktien: Shares/Market Cap fehlen | **hoch** (6/30) | D11; bis dahin klare Warnung "market nicht verfügbar" |
| Projekt/venv in iCloud-Ordner | eingetreten | Siehe 9, Entwicklungsumgebung: Projekt außerhalb von iCloud ablegen |
| Peer-Qualität schwach | mittel | Human-in-the-Loop-Gate rettet jeden Fall |
| Scope Creep | **hoch** | Abschnitt 4 und 1.4 als harte Grenze behandeln |
| LLM-Kosten steigen | niedrig | Caching, kompakte Tool-Outputs |

---

## 14. Positionierung — wie das Projekt erzählt wird

### 14.1 CV-Formulierung (Entwurf)

> **Scout Research** (Python, LLM Tool-Use, SEC EDGAR) — *2026*
> - Entwicklung eines KI-gestützten Research-Assistenten, der Comparable-Company-Analysen
>   automatisiert: Peer-Identifikation, XBRL-Datenextraktion aus SEC-Filings und
>   Multiple-Berechnung mit vollständiger Quellenrückverfolgbarkeit jeder Kennzahl
> - Architektur mit strikter Trennung zwischen LLM-Orchestrierung und deterministischer
>   Rechenschicht — numerische Ergebnisse stammen ausschließlich aus validierten Primärquellen
> - Human-in-the-Loop-Design mit expliziter Freigabe des Peer-Sets sowie automatisierten
>   Datenqualitätsprüfungen (Ausreißer, Periodeninkonsistenzen, fehlende Daten)
> - Evaluationsframework mit manuell erstelltem Golden Set zur Messung von Peer-Übereinstimmung,
>   numerischer Korrektheit und Halluzinationsfreiheit

*Positionierung (1.4):* Scout belegt Valuation- und Deal-Kompetenz (Private Equity, Transaction
Advisory). Im CV und in Gesprächen wird nur behauptet, was gebaut ist — Precedent Transactions, DCF
und LBO-Screening erst, wenn der jeweilige Baustein mit Golden Set evaluiert ist. Consulting-Frameworks
sind kein Teil von Scout (Kandidat für Arcticon).

### 14.2 Interview-Kernaussagen

1. **Zur Designphilosophie:** "Ich habe bewusst keinen autonomen Agenten gebaut, sondern einen
   Zuarbeiter. Die 80 % Datenarbeit übernimmt das System, das Urteil bleibt beim Menschen — das
   entspricht der Art, wie KI in Beratung und Finance real eingesetzt wird."

2. **Zur Halluzinationsfrage** (kommt garantiert): "Das Modell kann keine Zahlen halluzinieren,
   weil es keine Zahlen erzeugt. Es orchestriert Tools; jede Zelle im Output trägt CIK,
   Accession Number und XBRL-Konzept ihres Ursprungsfilings."

3. **Zur Evaluation:** "Ich habe ein Golden Set von Hand gebaut und messe Peer-Overlap und
   numerische Abweichung gegen diese Referenz. Ohne Messung ist eine Demo nur eine Anekdote."

4. **Zur Abgrenzung von Sentinel:** "Sentinel ist eine quantitative Risk-Engine — Mathematik auf
   Portfoliodaten. Scout Research ist ein Agentic-System — Orchestrierung, Tool-Design und
   Verlässlichkeit unter Unsicherheit. Zwei bewusst unterschiedliche Kompetenzfelder."

### 14.3 Anschluss an dein bestehendes Profil

- MCP-Sicherheitsseminar → Tool-Design, Trust Boundaries, kontrollierter Agentenzugriff
- Anthropic-Zertifikate (MCP Advanced, Agent Skills) → praktisch untermauert statt nur zertifiziert
- Finanzwirtschaft (1,7er Bereich) + Statistik/BI → fachliche Fundierung der Bewertungsmethodik
- Sentinel → Layered Architecture, Streamlit, pytest, SQLite als bereits erprobtes Fundament

---

## 15. Vorgeschlagene Repo-Struktur

Soll-Struktur; Markierung `[fehlt]` = noch nicht vorhanden (Stand v1.5), `[neu]` = vorhanden, aber
im ursprünglichen Entwurf nicht aufgeführt.

```
scout-research/
├── README.md
├── docs/
│   ├── foundation.md              ← dieses Dokument
│   ├── architecture.md            [fehlt]
│   └── decisions/                 [fehlt] ADRs für D1–D5 (leerer Ordner, von git nicht getrackt)
├── src/scout_research/
│   ├── config.py
│   ├── scratch.py                 [neu] CLI-Smoke-Tests (--full, --comps)
│   ├── data/                      # L1
│   │   ├── edgar_client.py
│   │   ├── market_provider.py     # Interface + Implementierungen
│   │   ├── cache.py
│   │   └── rate_limiter.py
│   ├── domain/                    # L2
│   │   ├── models.py
│   │   ├── metrics.py
│   │   ├── peers.py
│   │   ├── multiples.py
│   │   ├── quality.py
│   │   └── comps.py               [neu] CompsTable-Orchestrierung
│   ├── tools/                     # L3  (nur __init__.py)
│   │   ├── definitions.py         [fehlt] Phase 3
│   │   └── handlers.py            [fehlt] Phase 3
│   ├── agent/                     # L4  (nur __init__.py)
│   │   ├── loop.py                [fehlt] Phase 3
│   │   └── prompts.py             [fehlt] Phase 3
│   ├── export/                    # L5  (nur __init__.py)
│   │   ├── excel.py               [fehlt] Phase 4
│   │   └── markdown.py            [fehlt] Phase 4
│   └── ui/                        # L6  (nur __init__.py)
│       └── app.py                 [fehlt] Phase 4
├── tests/
│   ├── unit/                      # inkl. factories.py [neu], test_metrics_periods.py, test_throttle.py
│   ├── integration/               (leerer Ordner, von git nicht getrackt)
│   └── fixtures/                  # aufgezeichnete EDGAR-Responses
├── eval/
│   ├── golden_set/                (leerer Ordner, von git nicht getrackt)
│   └── run_eval.py                [fehlt] Phase 5
└── pyproject.toml
```

---

## 16. Startpunkt für Session 1

*(Historisch — Phase 0 ist abgeschlossen. Die Definition of Done gilt weiter, setzt aber einen
funktionierenden Editable-Install voraus; siehe 9, Entwicklungsumgebung. Für Phase 3 wird zusätzlich
`ANTHROPIC_API_KEY` in `.env` benötigt — Stand 2026-10-04 ist er dort leer.)*

**Ziel der ersten Code-Session (Phase 0):**

1. Repo + Struktur + `pyproject.toml` + venv
2. `config.py` mit User-Agent und API-Key-Handling über `.env`
3. `rate_limiter.py` (max. 10 req/s)
4. `edgar_client.py` mit drei Funktionen:
   - Ticker/Name → CIK
   - CIK → Company-Metadaten (Name, SIC, FY-Ende)
   - CIK → companyfacts (roh)
5. Erster Test: Umsatz eines bekannten Unternehmens abrufen, inkl. vollständiger Provenance
6. Ein `pytest`-Test, der das gegen eine gespeicherte Fixture prüft

**Definition of Done:** `python -m scout_research.scratch --ticker AAPL` gibt Umsatz mit
Accession Number, Periode und Filing-Datum aus. Kein LLM beteiligt.

**Mitzubringen in die Session:**
- Dieses Dokument (oder Abschnitte 7–10 + 16)
- Finnhub-API-Key (kostenlos, Signup ohne Kreditkarte) — für Phase 1, nicht für Phase 0 nötig
- Anthropic-API-Key (erst ab Phase 3 nötig)
- Eine echte Kontakt-E-Mail für den EDGAR-User-Agent (Pflicht laut SEC Fair Access Policy)

---

## 17. Änderungshistorie

| Version | Datum | Änderung |
|---|---|---|
| 1.0 | 2026-08-18 | Erstfassung |
| 1.1 | 2026-08-18 | D1 entschieden (Finnhub/Stooq), Cache-Design ergänzt, Provider-Kapselung konkretisiert, D6 aufgenommen |
| 1.2 | 2026-08-18 | Projekt final auf **Scout Research** umbenannt (vorher Arbeitstitel "Analyst Copilot") |
| 1.3 | 2026-08-18 | Phase 0 & 1 umgesetzt. D7 aufgenommen: Stooq-Fallback seit 08/2026 durch Bot-Schutz blockiert, Finnhub trägt v1 vorerst allein (siehe 8.3) |
| 1.4 | 2026-08-19 | Phase 2 umgesetzt (Comps-Engine). D2 entschieden (`browse-edgar` + `frames`, kein eigener Index), D4 entschieden (IQR/Tukey, n≥4). D3 teilweise: Mismatch-Erkennung steht, Normalisierung bleibt offen |
| 1.5 | 2026-10-04 | **Härtungs-Session vor Phase 3.** Nachgeführte Abweichungen (§0-Regel): Net Debt/EBITDA nicht implementiert (3.2); Tool-Katalog vs. L2-Stand (7.3); Rate-Limit pro Client-Instanz (8.2); Cache-Schlüssel = UTC-Kalendertag statt Handelstag, `/quote` = letzter Kurs statt EOD, `PriceQuote` statt `MarketSnapshot` aus Providern (8.3); Datenmodell-Abweichungen (10); EDGAR nicht gecacht, pandas/openpyxl ungenutzt (9); Repo-Struktur (15). **Korrekturen:** Stooq ist unverifiziert/deaktiviert, der Quote-Endpunkt hat nie funktioniert (8.3, D7); D5 geschlossen. **Neu:** 8.6 — Befund *veraltete Fakten aus falscher Periode* (8/30 Firmen, u. a. ADBE-Schuld aus 2019), Periodenanker, Schuldenregel mit Definitionsbelegen; Finnhub-Throttle (55/min, jeder Versuch zählt) und 429/`Retry-After`; Entwicklungsumgebung (iCloud setzt `hidden` auf Punkt-Dateien → Editable-Install bricht); D8–D12 aufgenommen. Phase-1/2-Live-Ergebnisse für Adobe sind ungültig (Nachtrag in 12). **Entscheidungen (Sean, 2026-10-04):** D12 `None` ohne Teilposten; D9 Periodenschema freigegeben (`period_end` kanonisch, `fiscal_year` Best Effort, Multiples nur aktuelle Periode, `calendar_year` aus dem Tool-Schema, Ableitung `Jahr(Periodenende − 180 Tage)` gegen `frames` geprüft: 31/31 — 7.3.1), noch nicht implementiert; D10 **implementiert** — gekennzeichnete Schuld-Untergrenze (`total_debt_is_lower_bound`, sichtbar in EV, EV-Multiples, Statistik, QualityWarning; 8.6); D11 zurückgestellt, `profile2`-Befund in 8.3; UI-Vision (Phase 6) als 12.1 eingefügt, D8 konkretisiert; zweite harte Regel in 7.1 (L1–L5 wissen nichts über die UI). **Live-Gate (≥ 4/5 Peers mit EV-Multiples):** vor D10 verfehlt (3/5), nach D10 **4/5, davon 1 mit Flag**. |
| 1.6 | 2026-10-05 | **Strategische Ausrichtung** als 1.4 eingefügt: Scout ist Zuarbeiter für Private Equity und Deal-Analyse (Valuation, Transaction Advisory), kein Consulting-Framework-Werkzeug; Bausteinfolge (Comps → Precedent Transactions, DCF, LBO-/Returns-Screening → optional Due-Diligence-Support), Prinzipien je Baustein, Consulting-One-Pager als Ausgabeform nach Phase 4, Abgrenzung zu Arcticon (MCP-Server nach Phase 5). Widersprüche abgeglichen: §1.3 Punkt 4, §4 (Precedent/DCF nicht mehr "v2/v3", neue strategische Nicht-Ziele), §8.5, Roadmap "Danach" (Memo-Entwurfsmodul entfällt), §13 Risiko Scope Creep, §14.1 Positionierung. **Neu offen:** D13 (Reihenfolge nach den Comps), D14 (Format One-Pager), D15 (MCP-Server/Arcticon). Kein Code geändert. |
| 1.6.1 | 2026-10-05 | Phase 3, Schritt 1: Stooq-Reste bereinigt (§8.3 D1-Überschrift, §9 Tech-Stack, §13 D1) — Stooq ist kein Fallback mehr, sondern ein deaktivierter Platzhalter (D7). Neue Config-Felder `anthropic_model` (Default `claude-sonnet-5-5`), `anthropic_compare_model`, `anthropic_effort`, `output_language`; Extra `agent` mit `anthropic>=1,<2`; pytest-Marker `live`. Plan: `docs/decisions/phase-3-plan.md`. |
| 1.6.2 | 2026-10-08 | Phase 3, Schritt 2: D9 in L2 umgesetzt (`PeriodSelector`, `PeriodNotAvailable`, `HistoricalValuationNotSupported`, `calendar_year` mit Frame-Prüfung, ein Filing je Periode, erstes Filing gewinnt; 7.3/7.3.1). Golden-Set-Seed (5 Firmen) eingetragen; vier bekannte Abweichungen zum Seed dokumentiert und offen (`tests/unit/test_golden_reproduction.py`). |
| 1.6.3 | 2026-10-08 | Golden-Set: §11.4 neu (Seed, `known_deviations`, vier entschiedene Fälle: ADSK `total_debt` und CDNS-Untergrenze als gepinnte Abweichungen, NVDA-Shares mit `tolerance_abs`, MSFT `d_and_a` als nicht ausgewiesen); §7.3.1: spätere Restatements werden nicht berücksichtigt. Kein Extraktor-Hack, D10/D12 unverändert. |
| 1.6.4 | 2026-10-08 | Phase 3, Schritt 3 (Daten-Härtung): Periodenprüfung nach Tagen statt Monat (gefalteter Abstand ±14 Tage → "Fiskaljahresende weicht ab"; Peer-Ende ≥ 300 Tage vor dem Ziel → "veraltetes 10-K"; nur Warnungen; D3-Zeile); Größenfilter über alle Umsatz-Konzepte (lazy, Zähler) und Fix `resolve_calendar_year` (Frame des Anker-Konzepts); Frames-Cache (SQLite, 7 Tage); typisierte EDGAR-Fehler inkl. neuem Code `DATA_NOT_FOUND` (7.3.2); keine Header/Keys in Fehlern. |

---

*Ende Dokumentversion 1.6.4*
