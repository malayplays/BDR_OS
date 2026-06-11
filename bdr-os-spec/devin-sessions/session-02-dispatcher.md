# Devin Session 2 — Dispatcher

Prereq: Sessions 0, 1, 1b merged (EV is denominated in Δpoints — needs `engine/points.py`). Attach DATA_MODEL.md (Rule 5, Job object), COMP_MODEL.md §5, AGENTS.md §11.

## Task

`backend/app/dispatcher/`:

1. **Job factory** — consumes Plan + events + agent-chain requests → creates Jobs with `funnel_stage`, `expected_value` (from live blended rates via the EV formulas in DATA_MODEL.md Object 6), `due_at` (convert-stage: +4h on positive replies), `estimated_minutes` per job_type from `dispatcher/effort.yaml`.
2. **Ranker** — `priority_score = expected_value / estimated_minutes`, multiplied by urgency boost (due within 4h: ×3; overdue: ×5), stage-gated by `bottleneck.py` output (bottleneck stage's jobs sort above others regardless of score — Rule 5 is a gate, not a weight).
3. **Morning plan** (07:30 + on replan) — assembles Today payload: ranked jobs, plan summary (touches by channel remaining, call blocks, confirmations due), bottleneck narrative (template-based; LLM polish optional behind a flag, default off).
4. **API**: `GET /api/today`, `POST /api/jobs/{id}/skip|snooze` (snooze re-enters tomorrow with decayed boost).
5. Auto-approve lane: job types whitelisted in `policy.yaml` bypass queue — verify the whitelist refuses `customer_facing`-tagged types while `now < DRAFT_ONLY_UNTIL` (config-load-time assertion, app refuses to boot otherwise).

## Done = these pass

- `test_ev_formulas` — EV per (job type × persona tier) matches DATA_MODEL.md reference values given seed rates; VP outreach outranks IC outreach even at 3× IC reply rate (persona arbitrage).
- `test_ic_demotion` — IC-persona create-jobs require explicit override to rank above Manager+ (COMP_MODEL.md §5).
- `test_ranking_order` — given a mixed bag (cold outreach, aged positive reply, 24h confirmation), order = confirmation/convert per bottleneck state; assert exact order for 3 scripted bottleneck scenarios from Rule 5.
- `test_stage_gate_dominates` — a 0.005-EV hold job outranks a 0.385-EV convert job ONLY when hold is the bottleneck stage.
- `test_due_at_sla` — positive_reply event → book_response job due_at = event+4h; at +4h ranking boost ×3 applied.
- `test_morning_plan_payload` — /api/today schema matches frontend contract (include JSON schema snapshot test).
- `test_autoapprove_lane_safety` — adding `outreach_draft` to whitelist with DRAFT_ONLY active → app boot fails with clear error.
- `test_snooze_decay` — snoozed job returns next day with boost decayed, never silently dropped.
