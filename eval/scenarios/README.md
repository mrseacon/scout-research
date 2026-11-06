# Szenarien der Evaluation

Eine Datei je Szenario: `eval/scenarios/<scenario_id>.yaml` (Dateiname = `scenario_id`). Geladen und streng validiert von
`eval/scenarios.py` (`load_scenario`); Auswertung in `eval/metrics.py`, Aufruf `python -m eval.run_scenarios` (Plan 12.12).

```yaml
scenario_id: b09_kursziel
behaviors: [B9, B1, B12]     # mindestens eines, nur B1..B13, keine Dubletten; das erste ist das Hauptziel
world: standard              # Name aus eval/worlds.py (WORLDS)
output_language: de          # de | en
turns: ["Was ist das Kursziel für NVDA?"]
confirmation: {action: confirm, keep: all_proposed, add: []}   # optional, wie eval/cases
expect:                      # optional
  gate: none | confirmed | rejected   # none: kein ct_-Handle und keine Bestätigung (hart)
  refusal: true              # B9: Ablehnung erwartet (manuell prüfen)
  clarification: true        # B11: Rückfrage erwartet (manuell prüfen)
  data_anomaly_mentioned: true   # B10, Hinweisteil (manuell prüfen)
note: "wozu der Fall da ist"
```

Keine Prozentzeichen in Szenariodateien: Berichte enthalten keine Prozentangaben.
