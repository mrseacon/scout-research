# Scout Research — Foundation Document

**Projektname:** Scout Research
**Owner:** Sean Pölka
**Status:** Pre-Development / Spezifikationsphase
**Dokumentversion:** 1.0 — Basis für alle folgenden Code-Sessions

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
4. **Anschlussfähig:** Comps sind die Grundlage für spätere Module (Precedent Transactions,
   DCF-Support, Memo-Entwürfe). Das Projekt kann organisch wachsen.

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

### 3.3 Erfolgskriterium für v1

> Ich gebe ein US-börsennotiertes Unternehmen an und erhalte innerhalb weniger Minuten eine
> nachvollziehbare, quellenbelegte Comps-Tabelle mit 5–10 Peers, die ich ohne Nacharbeit an den
> Zahlen weiterverwenden kann.

---

## 4. Nicht-Ziele (v1)

Explizite Abgrenzung — schützt vor Scope Creep und macht das Projekt im Gespräch glaubwürdiger:

- ❌ **Keine Investment-Empfehlungen** (kein "kaufen/halten/verkaufen", keine Kursziele)
- ❌ **Keine autonome Ausführung ohne Freigabe** — Peer-Set wird immer bestätigt
- ❌ **Kein DCF-Modell** (potenziell v3)
- ❌ **Keine Precedent Transactions** (potenziell v2)
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

*Shares Outstanding* ist in EDGAR vorhanden (Cover-Page-Daten der Filings). Fehlt nur der Kurs.

#### Entscheidung D1 (getroffen, Stand 08/2026)

**Primär: Finnhub. Fallback: Stooq. yfinance: nur optional für lokale Experimente.**

Bedarfsanalyse: ~10 Ticker pro Comps-Lauf, nur End-of-Day-Kurse. Der eigentliche Engpass ist der
Golden-Set-Lauf (10 Ziele × ~10 Peers ≈ 100 Kursabrufe pro Evaluationsdurchgang).

| Quelle | Free-Limit | Stabilität | Entscheidung |
|---|---|---|---|
| **Finnhub** | ~60 Calls/Minute, kein Kreditkarte | Offizielle API mit Key + Doku | ✅ **Primär** |
| **Stooq** | keine Limits, **kein API-Key** | CSV-Download, kein echtes API, aber sehr robust | ✅ **Fallback** |
| **yfinance** | undokumentiert, schwankend | Inoffiziell — Yahoo hat sein API 2017 abgeschaltet; Library nutzt interne Endpunkte ohne Stabilitätsgarantie, bricht gelegentlich | ⚠️ nur optional |
| **Alpha Vantage** | 25 Requests/**Tag** | Stabil, aber Limit zu eng | ❌ als Primärquelle |

**Wildcard Alpha Vantage:** Der Anbieter stellt verifizierten Open-Source- und Bildungsprojekten
unbegrenzte Requests bereit. Dieses Projekt erfüllt beide Kriterien — ein Antrag lohnt sich,
sobald das Repo öffentlich ist. Dann als dritter Provider hinter demselben Interface einhängbar.

#### Cache-Design löst das Rate-Limit strukturell

Schlusskurse ändern sich pro Handelstag genau einmal. Der Cache wird deshalb auf
`(ticker, trading_date)` geschlüsselt. Folge: **Ein wiederholter Golden-Set-Lauf kostet null
API-Calls.** Damit ist das Limit-Thema gelöst und nicht nur umgangen.

#### Pflicht-Kapselung

Kursquelle hinter einem eigenen Interface (`MarketDataProvider`) mit einheitlichem Rückgabetyp
(`MarketSnapshot`) kapseln. Provider-Wechsel darf genau eine Datei betreffen. **Nicht optional.**
Konkrete Struktur:

```
MarketDataProvider (Protocol)
  ├── FinnhubProvider    (primär,  benötigt FINNHUB_API_KEY)
  ├── StooqProvider      (fallback, keyless)
  └── CachedProvider     (Decorator, wrapped einen Provider + SQLite-Cache)
```

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

### 8.5 Der PE-Bezug bleibt erhalten

Auch ohne PE-Daten ist der PE-Bezug erzählbar:
- Comps sind Standard-Handwerkszeug jedes PE-Analysten.
- v2 kann **Precedent Transactions** aus M&A-bezogenen Filings (8-K, Merger-Proxies) ergänzen —
  dort stehen reale Transaktionsdaten (Kaufpreis, Struktur), die sonst nur kostenpflichtig
  verfügbar sind. Das ist ein echtes Alleinstellungsmerkmal für später.

---

## 9. Tech Stack

| Ebene | Wahl | Begründung |
|---|---|---|
| Sprache | Python 3.11+ | Konsistenz mit Sentinel |
| Agent-Framework | **Kein Framework** — eigener Tool-Loop gegen die LLM-API | Lerneffekt, volle Kontrolle, kein Framework-Overhead; im Interview deutlich besser erklärbar als "ich habe LangChain benutzt" |
| LLM | Anthropic API (Claude) | Tool-Use nativ, passt zu deinen MCP-/Agent-Zertifikaten |
| UI | Streamlit | Bereits beherrscht, schnelle Iteration |
| Datenzugriff | `httpx` | Sync + async fähig, moderne API |
| Marktdaten | Finnhub REST (primär), Stooq CSV (Fallback) | Siehe 8.3 — beide direkt per HTTP, keine SDK-Abhängigkeit nötig |
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

### Phase 3 — Agent-Schicht (2–3 Sessions)
- Tool-Definitionen, Agent-Loop, System-Prompt
- Peer-Ranking durch LLM (nur Auswahl, kein Erfinden)
- Fehler-Feedback-Mechanik
- **DoD:** Natürlichsprachlicher Auftrag → korrekte Comps-Tabelle

### Phase 4 — Interface & Export (1–2 Sessions)
- Streamlit-Chat mit Peer-Bestätigungs-Gate
- XLSX-Export inkl. separatem Quellenblatt
- **DoD:** Kompletter Workflow ohne Terminal nutzbar

### Phase 5 — Evaluation & Härtung (1–2 Sessions)
- Golden Set aufbauen, Metriken automatisieren
- README, Architekturdiagramm, Demo-Material
- **DoD:** Messbare Qualitätsaussagen möglich, Projekt vorzeigbar

### Danach (v2+, optional)
Precedent Transactions aus M&A-Filings · Multi-Perioden-Trends · Sektor-Screening ·
Memo-Entwurfsmodul · MCP-Server-Variante der Tools (starker Anschluss an dein Seminar)

---

## 13. Offene Entscheidungen & Risiken

| # | Offene Frage | Status |
|---|---|---|
| ~~D1~~ | ~~Welche Kursdatenquelle?~~ | ✅ **Entschieden:** Finnhub primär, Stooq Fallback (siehe 8.3) |
| D2 | Peer-Suche über `frames`-Endpunkt oder eigener SIC-Index? | offen — Phase 2 |
| D3 | Kalenderjahr- oder Fiskaljahr-Normalisierung bei abweichenden FY-Enden? | offen — Phase 2 |
| D4 | Ausreißer-Definition (IQR-basiert? feste Schwellen?) | offen — Phase 2 |
| D5 | Projektname final | offen — vor Phase 5 |
| D6 | Alpha-Vantage-Bildungslizenz beantragen, sobald Repo öffentlich? | offen — Phase 5 |

| Risiko | Wahrscheinlichkeit | Gegenmaßnahme |
|---|---|---|
| Kursdatenquelle bricht weg | mittel | Provider-Interface + automatischer Fallback + Cache |
| XBRL-Konzepte uneinheitlich zwischen Unternehmen | **hoch** | Konzept-Fallback-Ketten, explizites "nicht verfügbar" statt Schätzung |
| Peer-Qualität schwach | mittel | Human-in-the-Loop-Gate rettet jeden Fall |
| Scope Creep | **hoch** | Abschnitt 4 als harte Grenze behandeln |
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

```
scout-research/
├── README.md
├── docs/
│   ├── foundation.md              ← dieses Dokument
│   ├── architecture.md
│   └── decisions/                 ← ADRs für D1–D5
├── src/scout_research/
│   ├── config.py
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
│   │   └── quality.py
│   ├── tools/                     # L3
│   │   ├── definitions.py
│   │   └── handlers.py
│   ├── agent/                     # L4
│   │   ├── loop.py
│   │   └── prompts.py
│   ├── export/                    # L5
│   │   ├── excel.py
│   │   └── markdown.py
│   └── ui/                        # L6
│       └── app.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/                  # aufgezeichnete EDGAR-Responses
├── eval/
│   ├── golden_set/
│   └── run_eval.py
└── pyproject.toml
```

---

## 16. Startpunkt für Session 1

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

---

*Ende Dokumentversion 1.2*
