# ARCHITECTURE.md — BDR OS System Overview

## 1. What this system is

A personal orchestration layer that turns an annual quota into a daily, ranked, mostly-pre-done to-do list. It watches the funnel (EventLog), keeps a live conversion model (ConversionRates), recomputes the plan when reality drifts (goal engine), dispatches work to LLM agents (dispatcher), and batches everything needing human judgment into a Review Queue. The CRM remains the system of record; this system holds **orchestration state only** (goals, rates, plans, jobs) plus an event mirror.

## 2. Design principles → architectural consequences

| Principle | Consequence in code |
|---|---|
| Attention is scarce | Agents pre-compute outputs; UI is 3 screens; approvals are batched, never interrupt-driven |
| CRM = source of record | No customer/account/activity tables of our own beyond cached references + EventLog mirror. All writes go *out* through adapters |
| Everything is a Job | One `Job` table, one lifecycle, one approval gate, one write-back path. Agents never write to external systems directly |
| 60-day draft-only | A single `policy/` module wraps every adapter write. Agents physically cannot bypass it |
| Mocks first | Adapter pattern with interface + mock + fixtures. Real impls register via `.env` connection registry |

## 3. Module map

```
backend/app/
├── models/        # SQLAlchemy: Goal, EventLog, ConversionRates, FunnelState, Plan, Job
├── engine/        # PURE FUNCTIONS, no I/O — fully unit-testable
│   ├── rates.py        # blended rate math, rolling windows, confidence
│   ├── cascade.py      # goal ÷ rates → weekly/daily plan
│   ├── pace.py         # FunnelState derivation, gap calc
│   ├── replan.py       # trigger evaluation (the 4 replan conditions)
│   ├── bottleneck.py   # marginal-value stage allocation heuristic
│   └── catchup.py      # ranked lever proposals, +25% inflation cap
├── dispatcher/    # consumes engine output → creates/ranks Jobs → morning plan
├── agents/        # 10 workers; each: run(job) -> AgentOutput. LLM calls live here
│   └── base.py         # AgentBase: contract enforcement, prompt assembly, retries
├── adapters/      # interfaces + mocks (+ real gmail/gcal now)
│   ├── interfaces.py   # CRMAdapter, EmailAdapter, CalendarAdapter, EnrichmentAdapter, CallRecordingAdapter
│   ├── mock/           # fixture-backed implementations
│   ├── gmail/ gcal/    # REAL — personal account, validates the pattern
│   └── registry.py     # .env-driven: ADAPTER_CRM=mock|salesforce|hubspot ...
├── policy/        # guardrails — single chokepoint for ALL outbound writes
│   ├── guardrails.py   # draft_only_until, rate limits, strategic-account block
│   └── deliverability.py # send windows, per-domain caps, warmup schedule
├── scheduler/     # APScheduler: nightly rates, Sunday cascade, event-driven hooks
└── api/           # FastAPI routes serving exactly the 3 screens + webhooks
```

**Rule: `engine/` imports nothing from `adapters/`, `agents/`, or `api/`.** It is pure math on plain dataclasses. This is what makes the goal engine trustworthy and testable before any integration exists.

## 4. Data flow

### 4.1 Event ingestion (the only way state changes)
```
CRM / Gmail / GCal / (mock fixtures)
        │  poll or webhook
        ▼
adapters → normalize → EventLog (append-only)
        ▼
engine.pace.update_funnel_state(event)        # Rule 1: real-time
        ▼
engine.replan.check_triggers(funnel, rates)   # Rule 4: fire replan?
        ▼ (if triggered or scheduled)
engine.cascade.compute_plan(goal, rates, calendar_capacity)
        ▼
dispatcher: plan + bottleneck rule → Jobs (ranked by expected_value)
```

### 4.2 Job lifecycle (every unit of work, no exceptions)
```
TRIGGER ──► Job(pending) ──► agent.run() ──► Job(awaiting_approval) ──► REVIEW QUEUE
                                                    │ approve              │ reject/edit
                                                    ▼                      ▼
                                        policy.guardrails.check()    Job(rejected) + reason
                                                    ▼                 logged for agent tuning
                                        adapter.write_back()
                                                    ▼
                                        Job(written_back) + EventLog entry
```
- **Auto-approve lane:** only job types whitelisted in config as zero-customer-impact (e.g., CRM note logging, internal briefs) skip the queue. During days 0–60 the whitelist excludes anything customer-facing — no exceptions.
- Rejections store a reason; the copy agent consumes rejection reasons as few-shot steering.

### 4.3 Daily rhythm
| Time | What runs | Output |
|---|---|---|
| Nightly 02:00 | rates recompute (Rule 2); pipeline hygiene sweep | fresh ConversionRates; hygiene jobs |
| Sunday 21:00 | weekly cascade (Rule 3) | new Plan |
| 07:30 daily | dispatcher morning plan | Today screen: ranked jobs |
| Fixed check-ins (e.g., 11:30 / 15:30 / 17:30) | nothing — *you* check the Review Queue | batch approvals |
| Continuous | event ingestion, trigger replans, speed-to-book + show-rate machine timers | real-time jobs (convert-stage jumps the queue order, still gated) |

## 5. The three screens (frontend contract)

1. **Today** — `GET /api/today`: ranked job list with expected_value, stage badges, one-tap "start/skip/snooze"; morning plan summary (touches by channel, call blocks, confirmations due).
2. **Review Queue** — `GET /api/review-queue`, `POST /api/jobs/{id}/approve|reject|edit`: batched drafts grouped by type; diff view for edits; bulk approve for low-risk groups.
3. **Pace** — `GET /api/pace`: goal cascade tree, funnel rates (with confidence badges), pace gap by stage, active catch-up levers with accept/dismiss.

Frontend is generated in v0, exported to `frontend/`, wired by Devin. No business logic in the frontend — it renders API responses and posts decisions.

## 6. Guardrail module (not a footnote)

`policy/guardrails.py` exposes one function every write-back must pass:

```python
def check(action: WriteBackAction) -> Verdict:  # ALLOW | BLOCK | REQUIRE_APPROVAL
```
Config (`.env` + `policy.yaml`):
- `DRAFT_ONLY_UNTIL=2026-09-15`  # start + 60 days [CONFIRM WEEK 1 — set from actual day 1]
- `MAX_NEW_OUTBOUND_PER_DAY=40`, `MAX_TOUCHES_PER_CONTACT_PER_WEEK=3` [CONFIRM WEEK 1]
- `STRATEGIC_ACCOUNTS=[...]` — hard block on any automated touch; jobs targeting them are created as *manual-only* with agent-prepared materials
- Deliverability: send windows 8am–6pm recipient-local, per-domain daily caps, no sends Sat/Sun [CONFIRM WEEK 1 vs. team norms]

Violations don't fail silently: they convert the job to `awaiting_approval` with a `policy_flag`, surfaced in the Review Queue with the reason.

## 7. Tech stack

- Python 3.12, FastAPI, SQLAlchemy, SQLite (file db, v1) → Postgres later (no SQLite-only features; use SQLAlchemy types that port cleanly)
- APScheduler for cron-like jobs (v1; swap for Celery/Temporal only if needed — don't pre-build)
- Anthropic API for agents (personal key now); model per agent configurable in `agents.yaml`
- Frontend: Next.js + Tailwind + shadcn/ui (v0 export), talks only to FastAPI
- Monorepo, single `docker-compose up` for local dev; `pytest` everywhere; fixtures in JSON

## 8. What this system deliberately does NOT do (v1)

No auto-sending ever in v1 (even post-60-day, sending graduates one job type at a time). No lead scoring ML. No multi-user. No mobile app. No browser extension. No data warehouse. Resist all of these until the core loop has run for 30 days live.
