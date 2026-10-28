# Falldateien der Eval-Harness

Eine Datei je Fall: `eval/cases/<case_id>.yaml` (die `case_id` ist der Dateiname ohne Endung). Geladen und streng
validiert von `eval/harness.py` (`load_case`); die Regel steht vor dem Lauf fest (Plan 12.14.6, V6).

```yaml
case_id: adbe_basic
turns: ["Mach Comps für ADBE"]        # Nachrichten des (simulierten) Nutzers, der Reihe nach; mindestens eine
confirmation:                          # optional; Standard: {action: confirm, keep: all_proposed, add: []}
  action: confirm | reject | none
  keep: all_proposed | [TICKER, ...]
  add: [Anfrage, ...]
```

- `action: confirm` bestätigt den offenen Vorschlag, `reject` verwirft ihn, `none` tut nichts (B3-Fälle: es darf kein
  `ct_`-Handle entstehen). Je Fall höchstens eine Entscheidung; sie fällt nach dem ersten Zug, der auf eine Bestätigung wartet.
- `keep: all_proposed` behält alle vorgeschlagenen Peers. Eine Tickerliste behält die Schnittmenge mit dem Vorschlag, in
  der Reihenfolge des Vorschlags. Ein Ticker, der nicht vorgeschlagen wurde, wird als Abweichung
  (`{"kind": "not_proposed", "ticker": ...}`) protokolliert und nicht ergänzt.
- Tickers werden mit `normalize_ticker` verglichen (`BRK.B` = `BRK-B`). Nutzerergänzungen des Vorschlags
  (`user_requested_additions`) gelten als vorgeschlagen.
- `add` sind Anfragen (Ticker oder Name), die als Ergänzung mit der Bestätigung gehen. `keep` als Liste und `add` sind nur
  bei `confirm` erlaubt (sonst `ValueError`).
- Lehnt der Loop die Entscheidung ab (`ConfirmationError`), bricht der Fall nicht ab: Die Abweichung
  `{"kind": "confirmation_error", "code": ...}` wird protokolliert, es gibt keine Entscheidung. Bleibt nach dem Abgleich
  weder `keep` noch `add` übrig, wird nicht bestätigt (`{"kind": "empty_keep"}`).
- Unbekannte Schlüssel, eine andere `action`, leere `turns` oder eine `case_id`, die nicht zum Dateinamen passt, sind
  ein Fehler (`ValueError` mit dem Feldnamen).
- Die Entscheidung kommt allein aus dieser Datei und dem Vorschlag im Speicher des Codes, nie aus Modelltext.
