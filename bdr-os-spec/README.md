# BDR Operating System — Spec Pack

Build-ready specification for Malay's personal BDR OS at Cognition AI. Start date: mid-July 2026. This pack is the input to Devin (backend/wiring) and v0/Lovable (frontend). **No application code lives here — only specs and prompts.**

## Core principles (non-negotiable, repeated in every spec)

1. **Attention is the scarce resource.** Low-stakes decisions are automated; judgment calls are batched into a Review Queue checked at fixed times.
2. **CRM is the single source of record.** This system orchestrates. It never forks data or becomes a second CRM.
3. **Everything is a Job:** trigger → assigned agent → output → approval gate → write-back to system of record.
4. **60-day draft-only guardrail:** nothing customer-facing sends without explicit approval. Hard rate limits. Strategic accounts never auto-touched. Deliverability protected. Enforced in one policy module, not per-agent goodwill.
5. **Mocks first.** All integrations built against adapter interfaces with fixtures. Exception: Gmail + Google Calendar may run against Malay's personal account now. Everything else is `[CONNECT LATER]`.

## Placeholder conventions

- `[CONNECT LATER]` — integration stub awaiting Cognition tooling access (see CONNECT_LATER_CHECKLIST.md)
- `[CONFIRM WEEK 1]` — assumption to verify on the job (see WEEK_ONE_QUESTIONS.md)

**Decisions made:**
- Comp unit = **AD-accepted points** (CONFIRMED from comp plan — see COMP_MODEL.md). Cascade, EV, and dispatcher all run in Δpoints.
- CRM = **Salesforce** (confirmed system of record); API access policy still `[CONFIRM WEEK 1]`.
- Benchmark conversion rates seeded with generic outbound SaaS norms `[CONFIRM WEEK 1]` — replace with team benchmarks.
- Frontend prompts target **v0** (shadcn-native); they paste into Lovable unchanged.

## Pack contents & build order

| # | File / folder | What it is | Feeds into |
|---|---|---|---|
| 1 | `ARCHITECTURE.md` | System overview, module map, data flow | Every Devin session |
| 2 | `DATA_MODEL.md` | The 6 objects + 7 update rules as implementable logic | Sessions 0–2 |
| 2b | `COMP_MODEL.md` | Points, pay mechanics, credit pipeline, clawback log, promotion scorecard | Sessions 1b, 11; Pace screen |
| 3 | `ADAPTER_CONTRACTS.md` | 5 adapter interfaces, mocks, fixtures, connect-later registry | Session 0 |
| 4 | `AGENTS.md` | 10 agent specs with I/O contracts + golden examples | Sessions 3–11 |
| 5 | `devin-sessions/` | One ready-to-paste prompt per Devin session, with acceptance criteria | Devin |
| 6 | `frontend/` | v0/Lovable prompts for 3 screens + Devin wiring task | v0/Lovable → Devin |
| 7 | `WEEK_ONE_QUESTIONS.md` | What to confirm at Cognition before connecting anything | You, week 1 |
| 8 | `CONNECT_LATER_CHECKLIST.md` | Exact go-live steps per integration stub | You, July |

## Roadmap

**Now → start date (build against mocks)**
1. Devin Session 0: scaffold, data model, adapters + mocks, config pattern
2. Session 1: goal engine (pure logic, fully unit-tested — the heart)
3. Session 1b: points & comp engine (valuation, earnings projector, promotion scorecard, clawback gate)
4. Session 2: dispatcher
4. Sessions 3–11: worker agents, one session each (any order after 2; show-rate machine and copy agent first — highest value)
5. v0: generate three screens → export → Devin wiring session
6. Session 12: end-to-end integration against fixtures
7. Dogfood against Gmail/GCal personal adapters + fixture CRM

**Week 1 at Cognition**
- Work through WEEK_ONE_QUESTIONS.md (comp unit first)
- Get tooling/data policy clearance **before** connecting anything

**Weeks 2–4**
- Swap adapters per CONNECT_LATER_CHECKLIST.md, one at a time, read-only first
- Replace benchmark priors with team rates; keep cold-start thresholds until n is real

**Day 60**
- Review guardrail config; relax draft-only only where earned and approved.

## Repo this pack produces

```
bdr-os/
├── backend/            # FastAPI, Python — rate math lives here
│   ├── app/
│   │   ├── models/     # 6 core objects (SQLite v1 → Postgres later)
│   │   ├── engine/     # goal engine: cascade, rates, replan triggers
│   │   ├── dispatcher/ # job ranking, morning plan
│   │   ├── agents/     # 10 workers, LLM-backed (Anthropic API)
│   │   ├── adapters/   # interfaces + mocks (+ real gmail/gcal)
│   │   ├── policy/     # guardrails: draft-only, rate limits, strategic accounts
│   │   └── api/        # routes for the 3 screens
│   ├── fixtures/       # mock CRM/email/calendar/enrichment/call data
│   └── tests/
├── frontend/           # exported from v0, wired by Devin
└── .env.example        # connection registry — going live = filling keys
```
