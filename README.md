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

| Layer | Role | State (as of 2026-10-14) |
|---|---|---|
| L1 Data access | EDGAR client, market data (Finnhub), SQLite cache, rate limiting | ✅ implemented |
| L2 Domain logic | Metrics, periods, peers (SIC + size), multiples, quality checks | ✅ implemented |
| L3 Tools | Seven model tools, peer-confirmation gate, allowlist, slot renderer, number check | ✅ implemented |
| L4 Agent | System prompt, loop, state machine, budgets | 🔶 in development |
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
| Number-check corpus | 239 cases (accepted texts, bare numbers, slot errors, attacks) as a parametrised test | `tests/data/number_check_corpus.yaml` |
| Mutation probes | Removing a guard must turn tests red; 35 mutations in the last agent run (2026-10-11), none toothless (34 caught, one not applicable); several guards rest on a single test | [`docs/mutation-report-2026-10-11.md`](docs/mutation-report-2026-10-11.md), `docs/security-guards.md`, plan section 12.16 |
| Guard register | 23 register rows, each with module, tests and a mutation (two only partly implemented until trace and harness exist) | `docs/security-guards.md` |
| Independent review | A read-only reviewer agent checks diffs against the plan; findings are tracked in the plan | plan sections 12.14, 12.16 |
| Push gate | Full suite, clean tree, secret scan on added lines; a hook enforces it for the agent's own shell calls (not for pushes from a personal terminal) | `scripts/push_gate.py`, `.claude/hooks/` |

The unit and integration suite has 1044 tests (`pytest --collect-only`, as of 2026-10-28), all passing. No test is marked as live (the `live` marker selects none).

Honest limits: the golden set is five companies, a small sample. It shows the extraction path is sound on those filings, not that it is accurate in general. The agent loop has been tested only against a scripted model, not against a live one.

## Status and roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 Setup | EDGAR client with rate limiting and retries | ✅ implemented |
| 1 Data foundation | Metrics with provenance, Finnhub market data, SQLite cache | ✅ implemented |
| 2 Comps engine | Peer candidates (SIC + size), EV/Revenue, EV/EBITDA, EV/EBIT, P/E, outlier and period checks | ✅ implemented |
| 3 Agent layer | Tools, gate, slot renderer, number check, prompt, loop | 🔶 in development: tools, check and loop are committed and tested with a scripted model; trace (step 8), CLI and first live run (step 9) and live review (step 10) are not done; the three high-severity review findings on the loop are fixed, medium and low findings are open |
| 4 Interface and export | Streamlit chat, XLSX export with source sheet | ⏳ planned (`ui/` and `export/` are empty packages) |
| 5 Evaluation | Larger golden set, automated metrics | ⏳ planned |
| After | Precedent transactions, DCF and LBO support (assumptions always from the human) | ⏳ planned, order open |

## Quick start

Run by the author in a fresh clone and a new virtual environment (Python 3.13.9, 2026-10-14).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Fill `EDGAR_CONTACT_EMAIL` in `.env` with a real contact address (SEC fair-access policy). The Finnhub and Anthropic keys are optional for the steps below.

```bash
pytest                        # 1044 passed
python -m eval.seed_compare   # offline: extractor vs. golden set, uses fixtures
```

Live lookup, needs network access and your contact address, no model:

```bash
python -m scout_research.scratch --ticker AAPL
```

There is no runnable end-to-end agent yet. The `agent` extra (`pip install -e ".[agent]"`) and `ANTHROPIC_API_KEY` are only needed once the live run exists.

Note: in a folder synced by iCloud (for example `~/Desktop`) the editable install can fail with `ModuleNotFoundError`; see `docs/foundation.md`, section 9.

## Project structure

```
src/scout_research/
  data/      L1  EDGAR client, market data provider, cache, rate limiter
  domain/    L2  metrics, periods, peers, multiples, quality checks, comps
  tools/     L3  tool handlers and schemas, gate, slots, number check
  agent/     L4  model client, loop, budgets, fixed texts, versioned prompt
  export/    L5  placeholder (planned)
  ui/        L6  placeholder (planned)
docs/        foundation, phase 3 plan, guard register
eval/        golden-set seed and the extractor comparison
tests/       unit, integration, fixtures, number-check corpus
scripts/     push gate
```

## Documentation

| Document | Content |
|---|---|
| [`docs/foundation.md`](docs/foundation.md) | Vision, scope, architecture, data sources, roadmap, decision log |
| [`docs/decisions/phase-3-plan.md`](docs/decisions/phase-3-plan.md) | Agent layer design, number-check policy, loop, reviews |
| [`docs/security-guards.md`](docs/security-guards.md) | Register of guards with tests and mutations |
| [`docs/mutation-report-2026-10-11.md`](docs/mutation-report-2026-10-11.md) | Archived report of the last mutation run (step 7, phase B) |

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
