# Scout Research

**An AI research assistant for comparable company analysis in which the model never produces a number: every figure is computed in code from SEC filings and traces back to its source.**

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![status: in development](https://img.shields.io/badge/status-in%20development-orange)

> Learning and portfolio project. Not investment advice, not a product. Output is a research aid and must be checked by a human.

## Why

Building a trading comps table means pulling revenue, EBIT, debt, cash and share counts for ten or so companies, aligning fiscal periods and checking every cell against the filing. It is slow and easy to get wrong.

Language models are fast at that kind of work and unreliable with figures: they will state a plausible multiple that exists nowhere. A tool for deal and valuation work cannot accept that.

Scout Research splits the job. Deterministic code owns every number, with provenance. The model may only choose and describe, and a human confirms the peer group before anything is computed.

## Core principle

| The model may | The model may not |
|---|---|
| Call tools with typed, schema-checked inputs | Produce, round, convert or estimate a number |
| Choose peers from the candidate list the tools return | Invent a peer that is not in that list |
| Write commentary using placeholders such as `[[stat:ev_ebitda:median]]` | Write a figure in plain text (the check rejects it) |
| Propose a peer set | Confirm its own proposal (only a human input can) |

Numbers in the final text are filled in by the application from tool results. A text with a bare number or an unknown placeholder is rejected and never shown.

## How it works

```mermaid
flowchart LR
    A[Resolve target company] --> B[Find peer candidates]
    B --> C[Model proposes peer set]
    C --> D{{Human confirms}}
    D -->|rejected| C
    D -->|confirmed| E[Compute comps table]
    E --> F[Commentary with slots]
    F --> G[Number check]
    G --> H[Render with sources]
```

| Layer | Role | State (as of 2026-11-07) |
|---|---|---|
| L1 Data access | EDGAR client, market data (Finnhub), SQLite cache, rate limiting | ✅ implemented |
| L2 Domain logic | Metrics, periods, peers (SIC + size), multiples, quality checks | ✅ implemented |
| L3 Tools | Seven model tools, peer-confirmation gate, allowlist, slot renderer, number check | ✅ implemented |
| L4 Agent | System prompt, loop, state machine, budgets and cost caps, trace writer | 🔶 loop, cost caps and trace writer implemented and tested against a scripted model; never run against a live model |
| L5 Export | XLSX with separate source sheet | ⏳ planned |
| L6 Presentation | Streamlit chat with confirmation step | ⏳ planned |

Rule: the agent (L4) has no direct access to data access (L1). Everything flows through the tools in L3, which is what makes L1 to L3 testable without a model.

## Provenance in practice

One real value from the repo's hand-checked golden set (`eval/golden_set/aapl.yaml`), reproduced by the extractor with difference zero:

| Field | Value |
|---|---|
| Company | Apple Inc., CIK 0000320193 |
| Concept | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Period | 2024-09-29 to 2025-09-27 (FY2025) |
| Value | 416,161 USD millions |
| Filing | 10-K, accession `0000320193-25-000079`, filed 2025-10-31 |

Value, concept and accession are in the golden set. Period start and filing date are from the live output of `python -m scout_research.scratch --ticker AAPL` (SEC EDGAR, no model needed).

## How it is validated

| Check | What it covers | Where |
|---|---|---|
| Golden set | 5 companies (AAPL, MSFT, NVDA, ADSK; CDNS only `total_debt`), values checked by hand against the 10-K statements, pinned to period end and accession | `eval/golden_set/` |
| Extractor vs. golden set | Offline comparison; two deviations are known and pinned with a reason (ADSK `total_debt`, CDNS lower-bound flag), the rest match, some within a stated rounding tolerance | `python -m eval.seed_compare`, `tests/unit/test_golden_reproduction.py` |
| Number-check corpus | 327 cases (accepted texts, bare numbers, slot errors, attacks) as a parametrised test, plus invariants over all Unicode code points for characters that fold to digits | `tests/data/number_check_corpus.yaml`, `tests/unit/test_tools_commentary.py` |
| Mutation probes | Removing a guard must turn tests red. Full agent run (2026-10-11): 35 mutations, none toothless (34 caught, one not applicable). Targeted agent runs on 2026-10-28: after the loop fixes 10 mutations on four guards, all caught; during step 8, 35 mutations on eight register rows, 34 caught, one not applicable (stale fragment, corrected). On 2026-11-03: 25 mutations, 24 caught, one not applicable (stale fragment, corrected and caught when re-run by hand). From 2026-11-05 to 2026-11-07 the new rows SG-23 to SG-26 and the twelve new mutations of SG-01 were probed in a throwaway clone: 58 mutations; 5 first stayed green, each was closed by a new test or by removing a needless exception, after which all 58 were caught (results noted in the register rows). Not every mutation in the register has run in its current wording, and some guards still rest on a single test | [`docs/mutation-report-2026-10-11.md`](docs/mutation-report-2026-10-11.md), `docs/security-guards.md`, plan sections 12.16, 12.17 |
| Guard register | 41 register rows (verified), about 220 mutation instructions in all (keyword count, not exact), each row with module and tests (one, the log filter, only partly implemented until the CLI installs it). A row can list several instructions ("second mutation: …"); the total counts each one. The earlier figure of 88 was this count for the 28 rows of 2026-11-03, 146 for the 32 rows of 2026-11-07 | `docs/security-guards.md` |
| Trace | One JSONL file per run: header from a whitelist, every line schema-checked and redacted, query strings removed, a final check per line; so far produced only by tests, the scripted CLI demo and evaluation dry runs | `src/scout_research/agent/trace.py`, `tests/unit/test_trace.py` |
| Evaluation harness | In evaluation, peer confirmation comes only from the case file and the code's own state, never from model text. 18 scenarios cover the behaviours B1–B13; a runner scores B1–B5, B7 and B13 from the trace as "k of n" over at least three runs (the rest are judged by hand), with hard failures for numbers in free text, a bypassed gate and a known secret in shown text. So far only dry runs against a scripted model; the live mode is locked behind an environment variable, a terminal and a typed confirmation | `eval/harness.py`, `eval/scenarios/`, `eval/run_scenarios.py`, `eval/metrics.py`, `tests/unit/test_eval_*.py` |
| Independent review | A read-only reviewer agent checks diffs against the plan; findings are tracked in the plan | plan sections 12.14, 12.16, 12.17, 14 |
| Push gate | Full suite, clean tree, secret scan on added lines; a hook enforces it for the agent's own shell calls (not for pushes from a personal terminal). A second hook blocks any shell call of the agent that mentions the live switch of the evaluation runner, a guard against accidents, not a security boundary: live runs are started by a person in their own shell, and no key lives where the agent works | `scripts/push_gate.py`, `.claude/hooks/` |

The unit and integration suite has 2747 tests (as of 2026-11-13), all passing in a fresh clone. No test is marked as live (the `live` marker selects none), and a guard (`tests/network_guard.py`, installed in `tests/conftest.py`) fails the session when a test resolves or connects to an Anthropic host name.

Honest limits: the golden set is five companies, a small sample. It shows the extraction path is sound on those filings, not that it is accurate in general. The agent loop, the trace, the CLI and the evaluation runner have been tested only against a scripted model, not against a live one. The number check rests on closed lists and patterns: reviews on 2026-11-03 found Unicode disguises that passed it and that the 239-case corpus of that time had missed (for example `💯. Text`). These are fixed, but others may exist. An external review on 2026-11-11 found further gaps (control characters, number words such as `sechzehn`, "bei einem Prozent", list numbers after a mid-sentence line break, company names whose digit word freed that number everywhere); they are fixed with tests and corpus cases, the remaining low risks are listed in plan section 14 (O-39). Number words with accents or combining marks (`zwéi`) are caught since 2026-11-07; words a human reads as numbers outside the lists, and letters from other scripts that look Latin (a Cyrillic `е` in `zwеi`), remain a documented residual risk.

## Status and roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 Setup | EDGAR client with rate limiting and retries | ✅ implemented |
| 1 Data foundation | Metrics with provenance, Finnhub market data, SQLite cache | ✅ implemented |
| 2 Comps engine | Peer candidates (SIC + size), EV/Revenue, EV/EBITDA, EV/EBIT, P/E, outlier and period checks | ✅ implemented |
| 3 Agent layer | Tools, gate, slot renderer, number check, prompt, loop, trace, evaluation harness | 🔶 in development: tools, number check, agent loop (step 7), trace writer and evaluation harness (step 8) are implemented and tested against a scripted model; nothing has run against a live model. Step 9a (everything before the first live call) is done: cost caps per run and per day, a scripted CLI demo, a cost estimator with user-maintained prices, 18 evaluation scenarios with a locked live mode, and a network guard in the tests. Open: first live run (step 9, needs a key, prices and approval), live runs and half-live evaluation (step 10). From the loop review, the high-severity findings and three of the four medium ones are fixed, one medium is partly fixed; the rest, including the low findings, is tracked in the plan |
| 4 Interface and export | Streamlit chat, XLSX export with source sheet | ⏳ planned (`ui/` and `export/` are empty packages) |
| 5 Evaluation | Larger golden set, automated metrics | ⏳ planned |
| After | Precedent transactions, DCF and LBO support (assumptions always from the human) | ⏳ planned, order open |

## Quick start

Install, tests, the extractor comparison, the CLI demo and the evaluation dry run were checked in a fresh clone and a new virtual environment (Python 3.13.9, 2026-11-07); all of them also run without a `.env`. The live lookup was not re-run on that date.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Fill `EDGAR_CONTACT_EMAIL` in `.env` with a real contact address (SEC fair-access policy). The Finnhub and Anthropic keys are optional for the steps below.

```bash
pytest                        # 2747 passed
python -m eval.seed_compare   # offline: extractor vs. golden set, uses fixtures
scout-research chat --scripted        # demo with invented companies, no network, no model
python -m eval.run_scenarios          # dry run of the 18 scenarios, report under runs/eval/
```

Live lookup, needs network access and your contact address, no model:

```bash
python -m scout_research.scratch --ticker AAPL
```

There is no live agent yet: the CLI and the evaluation runner run end to end only against a scripted model (the demo answers `Mach Comps für ZZNV` and `/confirm`). The `agent` extra (`pip install -e ".[agent]"`) and `ANTHROPIC_API_KEY` are only needed once the live run exists.

Note: in a folder synced by iCloud (for example `~/Desktop`) the editable install can fail with `ModuleNotFoundError`; see `docs/foundation.md`, section 9.

## Project structure

```
src/scout_research/
  data/      L1  EDGAR client, market data provider, cache, rate limiter
  domain/    L2  metrics, periods, peers, multiples, quality checks, comps
  tools/     L3  tool handlers and schemas, gate, slots, number check
  agent/     L4  model client, loop, budgets, trace writer, fixed texts, versioned prompt
  export/    L5  placeholder (planned)
  ui/        L6  placeholder (planned)
docs/        foundation, phase 3 plan, guard register, mutation report, operating charter
eval/        golden-set seed, extractor comparison, evaluation harness, scenarios, runner, prompt variants
tests/       unit, integration, fixtures, number-check corpus
scripts/     push gate, fixture helper, cost estimator
```

## Documentation

| Document | Content |
|---|---|
| [`docs/foundation.md`](docs/foundation.md) | Vision, scope, architecture, data sources, roadmap, decision log |
| [`docs/decisions/phase-3-plan.md`](docs/decisions/phase-3-plan.md) | Agent layer design, number-check policy, loop, reviews |
| [`docs/security-guards.md`](docs/security-guards.md) | Register of guards with tests and mutations |
| [`docs/mutation-report-2026-10-11.md`](docs/mutation-report-2026-10-11.md) | Archived report of the full mutation run of 2026-10-11 (step 7, phase B) |
| [`docs/operating-charter.md`](docs/operating-charter.md) | Roles and decision levels for agent-driven work (what the orchestrating session may decide, what goes to the human) |

The documents are written in German.

## Limitations

- US companies only (SEC EDGAR registrants).
- Prices come from Finnhub as the last price: intraday during US trading hours, otherwise the last close; the cache fixes the first fetch of the day.
- EBITDA is approximated as EBIT plus D&A and is flagged as approximated.
- Missing values stay missing, never estimated. Example: Microsoft reports no total D&A line, so `d_and_a` is absent for MSFT.
- Total debt can be a lower bound when a filing omits the current portion; it is then flagged and carried through to EV and multiples.
- The period is the latest 10-K per company; peers with a different fiscal year end are flagged, not normalised.
- Small validation sample (five companies), no live model run yet.

## Author

Sean Pölka, Wirtschaftsinformatik (HKA). [GitHub](https://github.com/mrseacon)
