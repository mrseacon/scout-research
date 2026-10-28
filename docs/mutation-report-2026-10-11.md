# Mutationsbericht 2026-10-11 (Schritt 7, Phase B)

Archiv des Berichts, den der Subagent `mutation-prober` am 2026-10-11 nach Schritt 7, Phase B geliefert hat
(Lauf `a5fc936e`, Stand Commit `bfd2cb3`, Worktree inzwischen entfernt). Zusammenfassung in Plan 12.16.

- **Wortlaut** unverändert aus der Sitzung übernommen; nur absolute Pfade sind auf Pfade relativ zum Repository gekürzt.
- **Seither berichtigt:** Die Abschnitte zu SG-11a (Fragment) und SG-03b (Kontext) unter „Register veraltet /
  mehrdeutig“ sind im Register erledigt (Commit `d17b4d7`, Plan 12.16). Die Mutation von SG-11a wurde in diesem Lauf
  nicht ausgeführt.
- Ein früherer Lauf am selben Tag prüfte einen veralteten Stand und ist verworfen; er ist nicht archiviert.
- Der Bericht ist Modellausgabe, keine Freigabe.

---

Mutationsprüfung des Registers `docs/security-guards.md`, SG-01 bis SG-17 (Stand Commit bfd2cb3).

**Vorbereitung:** Worktree .claude/worktrees/agent-a5fc936e61de60d4e (git-dir unter .git/worktrees/, nicht das Hauptverzeichnis). `git checkout --detach bfd2cb3` ausgeführt. Das Register enthält SG-13 bis SG-17, und tests/unit/test_agent_loop_core.py existiert. `scout_research.__file__` zeigt in den Worktree (src/). Interpreter: .venv des Hauptverzeichnisses, nur zum Ausführen. Ausgangslage: alle Zeilen grün.

Ich habe je Zeile genau die im Register genannten Testfunktionen als Node-IDs ausgeführt (parametrisierte Tests vollständig, bei den Punkten „…" per Namenspräfix). Wo das Register ganze Dateien nennt (test_agent_loop_*.py, test_tools_find.py, test_system_prompt.py), lief die ganze Datei.

## Ergebnis (jede Mutation einzeln; „rot" = Zahl der verschiedenen roten Testfunktionen, parametrisierte Fälle zusammengefasst)

| ID | Mutation | Ergebnis | rot (Anzahl, Namen) |
|---|---|---|---|
| SG-01 | scan_numbers-Schleife durch `[]` | angeschlagen | 167 Fälle: `test_corpus_case[FT-…]`, `test_a_number_inserted_anywhere_into_safe_text_is_never_accepted`, … |
| SG-02 | `return SlotOk(...)` am Anfang von resolve_slot | angeschlagen | 3: `test_corpus_case`, `test_mutation_without_slot_validation_the_corpus_fails`, `test_fin_and_mkt_slots_resolve_against_the_tool_results_the_model_saw` |
| SG-03a | `if False:` statt awaiting_confirmation | angeschlagen | 3 (alle genannten) |
| SG-03b (1) | `if proposal.confirmed:` → `if False:` in confirm_peer_set | angeschlagen | 1: `test_a_proposal_can_be_confirmed_only_once` (nur dieser) |
| SG-03b (2, zweite) | superseded-Bedingung → `if False:` in confirm_peer_set | angeschlagen | 1: `test_a_new_proposal_invalidates_the_old_handle` (nur dieser) |
| SG-03c | `if args.peer_set_id in ctx.proposals:` → `if False:` | angeschlagen | 1: `test_compute_with_a_proposal_id_instead_of_a_peer_set_id_is_rejected` (nur dieser) |
| SG-04a | `return True` | angeschlagen | 4: `test_literal_rule_matrix`, `test_any_user_message_counts_and_no_messages_never_do`, `test_a_lowercase_ticker_the_user_wrote_in_lowercase_does_not_count`, `test_user_requested_addition_needs_a_literal_mention_in_a_user_message` |
| SG-04b | `if self.mentions.get(cik):` | angeschlagen | 2: `test_a_company_the_model_resolved_alone_is_not_allowed`, `test_a_target_the_user_never_named_is_rejected_for_the_peer_search` |
| SG-04c (1) | `if False:` statt `type(msg) is not HumanInput` | angeschlagen | 2: `test_record_user_message_accepts_only_human_input_and_leaves_the_allowlist_unchanged`, `test_a_subclass_of_human_input_is_not_accepted` |
| SG-04c (2, zweite) | `record_user_message(HumanInput(...))` in confirm | angeschlagen | 5: `test_lt_35_…`, `test_lt_36_…`, `test_lt_50_…`, `test_a_h1_record_user_message_is_called_only_by_run_turn_in_the_loop`, `test_a_h1_human_input_is_never_constructed_in_agent_or_tools` |
| SG-05 (1) | dispatch-Bedingung → `if False:` | angeschlagen | 7 (alle genannten) |
| SG-05 (2, zweite) | `loop_guards >= 2` → `if False:` | angeschlagen | 1: `test_lt_15_a_second_loop_guard_in_one_turn_ends_the_turn_as_failed` (nur dieser) |
| SG-06 | `if False:` statt `_closing or ended or _interrupted` | angeschlagen | 3: `test_lt_27_…`, `test_lt_24_…`, `test_lt_45_…` (`test_lt_20_…` bleibt grün) |
| SG-07 | `raise failure from httpx.HTTPError(str(headers))` in _get | angeschlagen | 1 Funktion, 4 Fälle: `test_errors_never_contain_request_headers_user_agent_or_keys` (nur dieser) |
| SG-08a | beide `raise failure ... from HTTPError(api_key)` | angeschlagen | 2: `test_finnhub_errors_never_contain_the_api_key`, `test_invalid_finnhub_response_becomes_sanitized_market_data_unavailable` (10 Fälle) |
| SG-08b | addFilter-Zeile gelöscht | angeschlagen | 2: `test_httpx_request_log_lines_are_redacted`, `test_cached_provider_logs_cache_and_errors_never_contain_the_key` |
| SG-09 (1) | redact in `_error_outcome` entfernt | angeschlagen | 1: `test_unexpected_exceptions_carrying_secrets_are_redacted_in_the_trace_and_absent_from_the_content` (nur dieser) |
| SG-09 (2, zweite) | redact in `AgentLoop._run` entfernt | angeschlagen | 1: `test_lt_17b_an_unexpected_exception_in_the_loop_fails_the_turn_and_the_event_is_redacted` (nur dieser) |
| SG-10 (1) | beide `[[`/`]]`-Maskierungszeilen gelöscht | angeschlagen | 4: `test_external_names_in_candidates_are_sanitized` (test_tools_find), `test_external_names_are_sanitized_before_they_reach_the_model`, `test_control_characters_whitespace_and_length_are_normalized`, `test_slot_delimiters_cannot_survive_sanitizing` |
| SG-10 (2, zweite) | Steuerzeichen-Zeile → `text = text` | angeschlagen | 3: dieselben ohne `test_slot_delimiters_…` |
| SG-11a | `if self._clock() > self._end:` → `if False:` | **nicht anwendbar (Register veraltet)** | Ausgangslage grün (2 Tests), Fragment nicht gefunden |
| SG-11b (1) | `session_cost >= cost_limit_usd` → `if False:` | angeschlagen | 1: `test_lt_24_…` (nur dieser) |
| SG-11b (2, weitere) | `turn.calls >= max_calls_per_turn` | angeschlagen | 1: `test_lt_19_…` (nur dieser) |
| SG-11b (3, weitere) | `elif turn.tools >= max_tools_per_turn` | angeschlagen | 1: `test_lt_20_…` (nur dieser) |
| SG-11b (4, weitere) | `_clock() - turn.started > turn_seconds` | angeschlagen | 1: `test_lt_21_…` (nur dieser) |
| SG-11b (5, weitere) | `usable = True` | angeschlagen | 2: `test_lt_22_…`, `test_lt_46_…` |
| SG-12 | `elif False:` statt context_tokens-Vergleich | angeschlagen | 1: `test_lt_49_…` (nur dieser) |
| SG-13 (1) | `return` am Anfang von check_placement | angeschlagen | 2 Funktionen, 14 Fälle: `test_u_p1_placement_violations_are_rejected`, `test_the_adapter_refuses_an_invalid_history_before_anything_is_sent` |
| SG-13 (2, zweite) | `if False:` statt `state is RUNNING` in `_require_ready` | angeschlagen | 1: `test_lt_40_and_z20_input_during_a_turn_is_rejected_and_changes_nothing` (nur dieser) |
| SG-14 | `Shown("text", text)` statt `check.body` | angeschlagen | 1: `test_lt_05_fin_and_mkt_slots_before_the_table_render_with_value_and_unit` (nur dieser) |
| SG-15 (1) | `return cleaned` ohne Zeichensatzfilter | angeschlagen | 2: `test_u_m5_the_snippet_filter_at_the_edges`, `test_lt_42_…` (9 Fälle) |
| SG-15 (2, zweite) | `if kind not in SNIPPET_KINDS:` → `if False:` | angeschlagen | 2: `test_u_m5_only_number_findings_ever_get_a_snippet`, `test_u_m5_the_correction_fact_uses_the_code_template_hint_and_never_the_problem_hint_or_reason` |
| SG-16 | `proposal.rejected = True` → `pass` | angeschlagen | 3: `test_lt_38_…`, `test_a_rejected_proposal_cannot_be_rejected_again_and_a_confirmed_one_cannot_be_rejected`, `test_reject_proposal_makes_the_proposal_unconfirmable_lifts_the_block_and_is_logged` |
| SG-17 (1) | `import eval.seed_compare` vor dem Docstring von agent/loop.py | angeschlagen | 1: `test_a_e1_agent_tools_and_cli_import_nothing_from_eval` (nur dieser) |
| SG-17 (2, zweite) | `_X = dict(channel="eval")` | angeschlagen | 1: `test_a_e1_the_literal_eval_channel_appears_nowhere_in_the_package` (nur dieser) |

Zeilen mit Status `geplant`: gibt es im Register nicht.

## Zahnlos
Keine. Von 35 geprüften Mutationen sind 34 angeschlagen, eine war nicht anwendbar.

## Register veraltet / mehrdeutig
- **SG-11a:** Das Fragment `if self._clock() > self._end:` existiert in `src/scout_research/tools/session.py::Deadline.check` nicht mehr. Der Code lautet `if self._cancelled or self._clock() > self._end:` (Zeile 56, seit Strg+C-Abbruch Z18). Ich habe nichts geraten und nichts mutiert. Das Register braucht das aktualisierte Fragment, und es sollte klar sein, ob nur der Zeit- oder auch der Abbruchzweig geprüft werden soll. Der Abbruchzweig (`_cancelled`) steht ohnehin in keinem genannten Test.
- **SG-03b:** Beide Fragmente (`if proposal.confirmed:` und `if proposal.superseded or proposal_id != self.current_proposal_id:`) kommen in session.py zweimal vor, in `reject_proposal` (Z. 388/390) und in `confirm_peer_set` (Z. 422/424). Wörtlich angewendet wäre die Mutation mehrdeutig. Ich habe sie, wie das Register es verlangt, nur in `confirm_peer_set` angewendet, über den Folgetext `raise ConfirmationError("proposal_superseded")\n\n        offered`. Empfehlung: Fragment mit dem Kontext im Register präzisieren.
- Hinweis SG-11b: Das Register sagt, die Bedingung `if response.stop_reason != "end_turn":` bleibe unverändert. Das ist sinngemäß in Ordnung und wurde nicht mutiert.

## Hinweise zur Robustheit (kein Mangel, nur Einzeltest-Abhängigkeiten)
Folgende Mutationen hängen an genau einer Testfunktion. Fiele dieser Test oder würde er abgeschwächt, wäre die Maßnahme ungeschützt:
- SG-14: nur `test_lt_05_…`. `test_lt_03_…`, `test_lt_04_…`, `test_lt_41_…`, `test_lt_47_…`, `test_lt_48_…` und `test_u_f1_…` bleiben bei dieser Mutation grün, obwohl das Register sie nennt. Wahrscheinlich ist `text` in diesen Fällen schon gleich `check.body`, oder sie prüfen Zusammenhänge, die die Mutation nicht stört.
- SG-03c nur `test_compute_with_a_proposal_id_instead_of_a_peer_set_id_is_rejected`, SG-03b je Mutation ein Test.
- SG-05 (2) nur `test_lt_15_…`.
- SG-06 nur drei der fünf genannten Tests schlagen an, `test_lt_20_…` und der Dispatch-Test `[TURN_ENDED]` nicht.
- SG-07 nur `test_errors_never_contain_request_headers_user_agent_or_keys`.
- SG-08a: `test_tools_secrets.py::test_price_provider_failures_leak_nothing` und `test_error_message_names_only_provider_and_status` bleiben grün (die anderen beiden fangen es).
- SG-09 jede Mutation genau ein Test (`test_unexpected_exceptions_…` bzw. `test_lt_17b_…`).
- SG-11b (1) bis (4) und SG-12 je ein Test.
- SG-13 (2) nur `test_lt_40_and_z20_…`.
- SG-17 je Mutation ein Test.

## Beleg
- Worktree: .claude/worktrees/agent-a5fc936e61de60d4e (HEAD losgelöst bei bfd2cb3).
- `git status --short` am Ende: leer.
- Während des Laufs wurden SG-08a und SG-08b nach einem Fehler in meinem Hilfsskript neu gestartet. Die Zahlen oben stammen aus dem sauberen Wiederholungslauf. Die Hilfsskripte liegen nur im Scratchpad (Scratchpad der Sitzung), nicht im Repository.

Bericht ist Modellausgabe, keine Freigabe.
