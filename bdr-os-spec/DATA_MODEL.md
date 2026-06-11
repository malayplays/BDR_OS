# DATA_MODEL.md — Six Objects, Seven Update Rules

SQLAlchemy models, SQLite v1 (Postgres-portable types only). All timestamps UTC ISO-8601. All enums are Python `StrEnum` stored as TEXT. JSON columns use SQLAlchemy `JSON` type.

---

## Object 1: Goal

All targets live here, nowhere else. Hierarchy: year → quarter → month (week targets are *derived* by the Plan, not stored as Goals).

| Field | Type | Notes |
|---|---|---|
| id | UUID PK | |
| unit | enum | **`points` (CONFIRMED — AD-accepted points per COMP_MODEL.md)**; enum keeps `qualified_held_meeting` etc. for portability |
| target_value | float | from COMP_MODEL.md §8 ramp table (M1=0 … M4+=35 quota; personal targets higher) |
| period_type | enum | `year` \| `quarter` \| `month` |
| period_start, period_end | date | inclusive |
| parent_goal_id | UUID FK→Goal, nullable | month→quarter→year |
| edited_at | datetime | any change fires replan trigger (Rule 4c) |

Invariants: children of a parent must sum to parent.target_value (validation warning, not hard block — comp plans can be seasonal). Exactly one active goal chain per unit.

## Object 2: EventLog

Immutable, append-only. Everything downstream derives from this. Never UPDATE or DELETE; corrections are new events with `reverses_event_id`.

| Field | Type | Notes |
|---|---|---|
| id | UUID PK | |
| event_type | enum | `touch_sent`, `reply_received`, `positive_reply`, `meeting_booked`, `meeting_held` (canonical for the comp plan's "occurred"), `meeting_no_show`, `meeting_rescheduled`, `meeting_cancelled`, `ad_accepted`, `ad_rejected`, `s1_reached`, `s2_reached`, `credit_clawed_back`, `dormancy_requalified`, `invite_accepted`, `invite_declined`, `ooo_autoreply`, `bounce`, `unsubscribe` — credit pipeline per COMP_MODEL.md §3 |
| persona_tier | enum, nullable | `global_c_suite` \| `vp_level` \| `director` \| `manager` \| `ic` — on meeting events; drives point valuation |
| points_value | float, nullable | resolved from comp_plan.yaml at `ad_accepted`/`s1_reached`/`s2_reached` time; stored for audit |
| channel | enum, nullable | `email` \| `call` \| `linkedin` \| n/a |
| account_ref | str | CRM ID — reference only, never a local account table |
| contact_ref | str, nullable | CRM ID |
| job_id | UUID FK→Job, nullable | attribution: which job produced this |
| occurred_at | datetime | event time (not ingest time) |
| source | enum | `crm` \| `email` \| `calendar` \| `manual` \| `mock` |
| payload | JSON | source-specific raw detail |
| reverses_event_id | UUID, nullable | correction mechanism |
| ingested_at | datetime | |

Index: (event_type, occurred_at), (account_ref), (job_id). Dedupe on (source, payload.source_id).

## Object 3: ConversionRates

Live model, one row per (metric, channel, computed_at) — history kept for drift detection.

| Field | Type | Notes |
|---|---|---|
| id | UUID PK | |
| metric | enum | `reply_rate`, `positive_reply_rate`, `book_rate`, `show_rate`, `qualify_rate`, `ad_accept_rate` (occurred→AD-accepted; clawback insurance metric) |
| persona_tier | enum, nullable | reply/book rates also tracked per persona tier where n allows — VP-level rates differ from IC and the cascade needs both |
| channel | enum, nullable | per-channel for reply/book; null for show/qualify |
| window_days | int | 30 default |
| n_sample | int | denominator count in window |
| actual_rate | float, nullable | null if n_sample = 0 |
| benchmark_rate | float | team benchmark [CONFIRM WEEK 1]; seeded with placeholders below |
| k_strength | int | prior weight (pseudo-observations), default 30; cold start 60 |
| blended_rate | float | **rate = (actual×n + benchmark×k) / (n + k)** |
| confidence | enum | `low` (n<20) \| `medium` (20–75) \| `high` (>75) |
| baseline_90d | float, nullable | 90-day blended baseline for drift detection (Rule 4b) |
| computed_at | datetime | |

Seed benchmarks (generic outbound SaaS — **[CONFIRM WEEK 1]**, replace with Cognition team rates):
`reply_rate email=0.04, call(connect)=0.08, linkedin=0.08; positive_reply_rate=0.35 of replies; book_rate=0.55 of positive; show_rate=0.70; qualify_rate=0.60`

## Object 4: FunnelState

One row per active goal period, recomputed event-driven; snapshot history nightly for trends.

| Field | Type | Notes |
|---|---|---|
| id | UUID PK | |
| goal_id | UUID FK | |
| as_of | datetime | |
| counts | JSON | `{touches: {email, call, linkedin}, replies, positive_replies, booked, held, no_shows, ad_accepted, s1, s2}` period-to-date |
| points | JSON | `{credited, pending, projected}` per COMP_MODEL.md §3 — pace runs on credited+pending |
| persona_mix | JSON | booked + credited meetings by persona tier — feeds the arbitrage dashboard |
| pct_goal | float | (credited+pending points) ÷ point target |
| pct_period_elapsed | float | business days elapsed ÷ business days total |
| pace_gap | float | pct_goal − pct_period_elapsed (negative = behind) |
| gap_by_stage | JSON | per stage: actual vs. expected-at-this-point given current Plan |
| at_risk | bool | set by catch-up logic when inflation cap exceeded |

## Object 5: Plan

Cascade output. **Derived, never hand-edited** — no PATCH endpoint exists; the only way to change a Plan is to change a Goal or let rates move, then regenerate. Old plans kept (`superseded_at`).

| Field | Type | Notes |
|---|---|---|
| id | UUID PK | |
| goal_id | UUID FK | |
| week_start | date | Monday |
| weekly_bookings_required | float | |
| weekly_held_target | float | |
| daily_allocation | JSON | per business day: `{email_touches, calls, linkedin_touches, call_blocks: [{start,end}], confirmations_due: int}` |
| rates_snapshot | JSON | blended rates used — full audit of the math |
| capacity | JSON | `{business_days, pto_dates, blocked_hours}` |
| generated_at, superseded_at | datetime | |
| replan_reason | enum | `weekly_cascade` \| `pace_gap` \| `rate_drift` \| `goal_edited` \| `capacity_change` |

## Object 6: Job

The dispatch unit. One table, one lifecycle.

| Field | Type | Notes |
|---|---|---|
| id | UUID PK | |
| job_type | enum | see AGENTS.md (e.g., `research_brief`, `outreach_draft`, `reply_triage`, `confirmation_24h`, `no_show_recovery`, ...) |
| funnel_stage | enum | `create` \| `convert` \| `hold` |
| agent | str | agent name from AGENTS.md |
| trigger | JSON | `{kind: event|schedule|manual|plan, ref: ...}` |
| account_ref, contact_ref | str, nullable | CRM IDs |
| status | enum | `pending` → `in_progress` → `awaiting_approval` → (`approved` \| `rejected` \| `edited_approved`) → `written_back`; terminal also: `expired`, `failed`, `skipped` |
| expected_value | float | **Δ points** = Δheld × persona_points × ad_accept_rate (see reference below) |
| priority_score | float | dispatcher output; expected_value ÷ estimated_minutes, recency-boosted |
| input_payload | JSON | what the agent receives |
| output | JSON | AgentOutput (draft, brief, etc.) |
| policy_flags | JSON | guardrail verdicts |
| approval | JSON | `{decided_by, decided_at, edit_diff, rejection_reason}` |
| write_back_ref | str, nullable | external ID created (CRM task ID, Gmail draft ID...) |
| due_at, created_at, updated_at | datetime | convert-stage jobs get tight due_at (speed-to-book) |

Expected-value reference in **Δpoints** (v1 constants × persona points × ad_accept≈0.9; recompute from live rates):
`EV(outreach_draft, VP) = .04×.35×.55×.70 × 5 × .9 ≈ 0.024 pts/touch` vs `IC ≈ 0.0035` — persona arbitrage lives here
`EV(book_response, VP positive) = .55×.70 × 5 × .9 ≈ 1.73 pts`
`EV(confirmation_24h, VP) ≈ 0.10 × 5 × .9 ≈ 0.45` ; `EV(no_show_recovery, VP) ≈ 0.25 × 5 × .9 ≈ 1.1` (no-show = ZERO credit makes hold-stage brutal-priority)
`EV(dormancy_requalify job) = P(re-engage) × VP path ≈ high — the 120-day goldmine ranks with warm work`
Month-end accelerator awareness: when month-to-date pts ≥ quota, dispatcher may annotate jobs with marginal-$ (×$100/pt) — display only, ranking stays in points.

---

## The Seven Update Rules (implementable logic)

### Rule 1 — Event-driven FunnelState
On every EventLog insert: increment `counts`, recompute pct/gap fields, persist. Then call `replan.check_triggers()`. Single transaction. Idempotent via event dedupe.

### Rule 2 — Nightly rate recompute (02:00)
For each (metric, channel): count numerator/denominator events in trailing `window_days`, compute `actual_rate`, blend with prior, set confidence, store new row. Update `baseline_90d` as the same blend over 90 days.

### Rule 3 — Weekly cascade (Sunday 21:00) — runs in POINTS
```
remaining_pts    = goal.target_pts − funnel.points.credited − funnel.points.pending
avg_pts_per_held = Σ(persona_mix_target_i × points_i × ad_accept_rate)   # mix target favors VP+ per COMP_MODEL.md §5
weekly_held      = (remaining_pts / remaining_weeks(capacity)) / avg_pts_per_held
bookings_needed  = weekly_held / show_rate.blended
positives_needed = bookings_needed / book_rate.blended
# per channel c, allocate by current blended channel rates:
touches_c = positives_needed × mix_c / (reply_rate_c × positive_reply_rate)
# mix_c starts ⅓/⅓/⅓, shifts toward higher (reply×positive×book) channels, bounded 15%–60% per channel
```
Spread across business days net of capacity. Emit new Plan, supersede old. Call blocks sized from `calls/day ÷ dials_per_hour (≈12 [CONFIRM WEEK 1])`.

### Rule 4 — Trigger replans (checked on every event + hourly)
Fire cascade off-cycle when any of: (a) `|pace_gap| > 0.15`; (b) any `|blended − baseline_90d| > 0.10` absolute; (c) Goal.edited_at changed; (d) capacity change (PTO/holiday added). Debounce: max one auto-replan per 24h per reason.

### Rule 5 — Bottleneck rule (dispatcher, each morning)
Allocate tomorrow's effort by marginal value (held meetings recovered per hour of attention). v1 heuristic, in priority order:
1. `show_rate` down >10pts vs. baseline → hold-stage jobs first
2. any `positive_reply` event unactioned >4h → convert-stage first (speed-to-book)
3. else create-stage per Plan volumes
Within a stage, sort by `priority_score`.

### Rule 6 — Catch-up logic (when pace_gap < −0.15)
Propose ranked levers, each with estimated Δheld and cost-to-attention:
1. Pull in far-out booked meetings (>5 days) → hold-stage pull-in jobs
2. Revive stalled positive threads + recent closed-lost → convert-stage
3. Shift channel mix toward highest-converting (within bounds)
4. Raise raw volume — **daily inflation capped at +25% over Plan**
If cap can't close the gap: set `FunnelState.at_risk=true`, surface "goal at risk" on Pace screen with the honest math. Never silently inflate.

### Rule 7 — Cold start (weeks 1–6)
Config flag `COLD_START=true`: `k_strength=60`, all rates marked `low` confidence regardless of n, replan thresholds widened (pace 15%→25%, drift 10→15pts), catch-up levers advisory-only (no auto job creation). Exit when every core metric reaches n≥30, then thresholds tighten automatically.
