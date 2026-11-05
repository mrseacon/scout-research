# Betriebsordnung für autonomes Arbeiten (`operating-charter`)

Stand: 2026-10-28 (Sean). Gilt für alle Sitzungen, in denen eine Claude-Code-Sitzung als Orchestrator ohne laufende
Rückfrage an Scout Research arbeitet. Sie ersetzt die Regeln 3 und 7 in Plan, Abschnitt 13, soweit sie widersprechen;
alle übrigen Regeln dort (Push-Prozess, Register, Hook, Berichte) gelten weiter.

## 1. Rollen

| Rolle | Wer | Aufgabe | Grenzen |
|---|---|---|---|
| CEO | Sean | Entscheidet über Ziele, Scope, Geldausgaben und Veröffentlichung; beantwortet Eskalationen (Stufe C) | — |
| Orchestrator (CTO) | Die Opus-Hauptsitzung | Plant, zerlegt, delegiert, prüft, entscheidet im Rahmen der Stufen A und B | Schreibt **keinen Produktivcode** (`src/`, `tests/`, `eval/`, `scripts/`). Ausnahme: Eskalation nach zwei gescheiterten Versuchen eines Implementierers an derselben Teilaufgabe (Abschnitt 4) |
| Implementierer | Subagent `implementer` (Sonnet) | Setzt genau **eine** klar umrissene Teilaufgabe um: Code und Tests | Sequentiell, nie zwei Implementierer zugleich im selben Repository; keine Commits, keine Pushes |
| Urteils-Agenten | `reviewer` (Opus, schreibgeschützt), `corpus-adversary`, `mutation-prober`, `trace-triager` | Wie in Plan, Abschnitt 13 | Nur für Urteilsfragen; ihre Berichte sind Modellausgabe, keine Freigabe |
| Deterministische Prüfungen | Tests, `scripts/push_gate.py`, Hook | Ja/Nein-Fragen (Suite, Arbeitsstand, Geheimnisse, Push-Freigabe) | Nie durch einen Agenten ersetzt |

Plan und Doku (`docs/`, `README.md`) pflegt der Orchestrator selbst; das ist kein Produktivcode.

## 2. Entscheidungsstufen

### Stufe A — selbständig, im Statusfile festhalten

- Umsetzung eines im Plan **spezifizierten** Schritts oder Teilschritts, mit Tests.
- Lokale Commits.
- Subagents starten (Implementierer, Urteils-Agenten).
- Review-Befunde der Stufen **mittel** und **niedrig** beheben.
- Plan, Register und Doku nachziehen.

### Stufe B — entscheiden und dokumentieren

Reversible Wahl **innerhalb** des Plans, zum Beispiel Namen, Struktur, Testaufbau, Reihenfolge von Teilaufgaben.

- Jede Stufe-B-Entscheidung bekommt eine ID (`O-01`, `O-02`, …) und eine Zeile in Plan, Abschnitt 14
  („Entscheidungen des Orchestrators“): Punkt, Entscheidung, Grund, Alternative, wie sie rückgängig zu machen ist.
- Im Bericht an Sean stehen Stufe-B-Entscheidungen in einem eigenen Abschnitt.

### Stufe C — STOPP, eskalieren, auf Sean warten

Eintrag in `.orchestrator/ESCALATIONS.md` (Frage, Optionen, Empfehlung, was blockiert ist). Bis zur Antwort nur an
Unkritischem weiterarbeiten, das von der Frage nicht abhängt.

1. Jeder Review-Befund **kritisch** oder **hoch**, der nicht in **einer** Runde sauber behoben ist (behoben heißt:
   Test, der vorher rot war, ist jetzt grün; Reviewer bestätigt).
2. Jede Änderung am Provenance-Prinzip, an den B1-Invarianten (keine nackte Zahl aus Modelltext beim Nutzer), an der
   Gate-Logik oder am Sicherheitsregister `docs/security-guards.md`, die etwas **streicht oder abschwächt**.
   Ergänzungen (neue Maßnahme, neuer Test, neue Mutation) sind Stufe A.
3. Änderungen an `scripts/push_gate.py`, `scripts/push_gate_baseline.json`, `.claude/hooks/` oder
   `.claude/settings.json`.
4. Neue Abhängigkeiten, Lizenzfragen, Löschen von Dateien außerhalb des Auftrags.
5. Jeder Live-Aufruf mit echtem API-Schlüssel oder mit Kosten, jede Ausgabe von Geld.
6. `git push`. Im Pilot pusht immer Sean. Der Orchestrator darf bis „Push-Gate bestanden“ vorbereiten (Plan,
   Abschnitt 13, Regel 1) und meldet dann.
7. Jede Abschwächung der Tests: `xfail`, `skip`, Änderung der Skip-Baseline, gelockerte Zusicherung.
8. Scope-Erweiterung über den beauftragten Schritt hinaus.
9. Unklarheit mit zwei vertretbaren Lesarten und nicht rückholbaren Folgen.
10. Zwei gescheiterte Versuche mit derselben Diagnose, nachdem auf den Orchestrator (Opus) eskaliert wurde.

Im Zweifel zwischen zwei Stufen gilt die strengere.

## 3. Arbeitsregeln

1. **Zerlegen vor Beginn.** Jede Aufgabe wird in Teilaufgaben mit **Abnahmekriterium** zerlegt (welcher Test, welche
   Prüfung, welches beobachtbare Ergebnis). Die Zerlegung steht in `.orchestrator/STATUS.md`.
2. **Ablauf je Teilaufgabe:** Implementierer → der Orchestrator lässt Tests und deterministische Prüfungen selbst laufen
   und liest den Diff → `reviewer` mit dem Diff als Datei → Befunde einarbeiten (Stufe A) oder eskalieren (Stufe C) →
   lokaler Commit → `STATUS.md` aktualisieren. Erst dann die nächste Teilaufgabe.
3. **Modellwahl:** Opus für offene Design- und Sicherheitsfragen und den `reviewer`; Sonnet für klar spezifizierte
   Umsetzung.
4. **Kein Befund gilt als behoben ohne Test, der vorher rot war** (Gegenprobe gegen den alten Code oder Mutation).
5. **Ehrlichkeit:** Unsicherheit offen benennen. Nichts als belegt behaupten, was nicht gelaufen ist; Ergebnisse von
   Subagents vor der Weitergabe prüfen (Diff lesen, Tests selbst laufen lassen).
6. **Register:** Ändert eine Teilaufgabe Schutzmaßnahmen, ändert sie das Register im selben Commit, danach läuft der
   `mutation-prober` (Plan, Abschnitt 13, Regel 4).
7. **Triviale Konfiguration (O-23, Sean, 2026-11-05):** Konfigurationsänderungen von wenigen Zeilen ohne Programmlogik (etwa eine Einstellung in `pyproject.toml`) macht der Orchestrator selbst, ohne Implementierer und ohne `reviewer`; Kontrolle sind die vollständige Suite und das Push-Gate; nie für Code unter `src/`, `scripts/`, `.claude/` oder Tests.

## 4. Eskalation bei Fehlschlägen

1. Scheitert ein Implementierer an einer Teilaufgabe (Abnahmekriterium nicht erfüllt), bekommt er einen zweiten Versuch
   mit der Diagnose des Orchestrators.
2. Scheitert auch der zweite Versuch, übernimmt der Orchestrator die Teilaufgabe selbst (einzige Ausnahme vom Verbot,
   Produktivcode zu schreiben) und vermerkt das in `STATUS.md`.
3. Scheitert der Orchestrator zweimal mit derselben Diagnose: Stufe C.

## 5. Status und Sitzungsgrenze

Zwei Dateien unter `.orchestrator/` (gitignored, nur lokal):

| Datei | Inhalt | Aktualisiert |
|---|---|---|
| `STATUS.md` | aktueller Schritt und Teilaufgabe; erledigt; offen; **nächster konkreter Befehl**; Teststand (Zahl, Befehl, Datum); letzter Commit; Stufe-A- und Stufe-B-Vermerke seit dem letzten Bericht | nach **jeder** Teilaufgabe |
| `ESCALATIONS.md` | offene Fragen an Sean: Frage, Optionen, Empfehlung, was blockiert ist, seit wann | bei jeder Stufe-C-Frage; erledigte Einträge mit Antwort und Datum markieren |

Eine neue Sitzung liest zuerst `STATUS.md` und `ESCALATIONS.md` und setzt beim nächsten konkreten Befehl fort.

**Naht das Sitzungslimit oder steht eine Stufe-C-Frage an:** Zustand sauber hinterlassen (Tests grün; alles committet
oder ausdrücklich als WIP benannt, mit Pfad und Grund), `STATUS.md` und `ESCALATIONS.md` aktualisieren, dann stoppen.
