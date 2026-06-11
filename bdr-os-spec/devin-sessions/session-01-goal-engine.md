# Devin Session 1 — Goal Engine (Pure Logic + Unit Tests)

Prereq: Session 0 merged. Attach DATA_MODEL.md (the seven update rules are the spec).

## Task

Implement `backend/app/engine/` as **pure functions on plain dataclasses — zero I/O, zero imports from adapters/agents/api**. This is the heart; it must be exhaustively tested.

1. `rates.py` — rolling-window rate computation per (metric, channel) from an event list; blend `rate = (actual×n + benchmark×k)/(n+k)`; confidence tiers; 90-day baseline; cold-start mode (`k=60`, force `low` confidence) per Rule 7.
2. `cascade.py` — Rule 3 exactly as written in DATA_MODEL.md, including channel mix bounds (15%–60%), business-day spreading net of capacity, call-block sizing. Returns a `Plan` dataclass.
3. `pace.py` — FunnelState derivation from EventLog + Goal (Rule 1 math); `gap_by_stage` vs. active Plan.
4. `replan.py` — Rule 4 trigger evaluation incl. 24h debounce per reason; cold-start widened thresholds.
5. `bottleneck.py` — Rule 5 heuristic; returns stage priority + human-readable reason string.
6. `catchup.py` — Rule 6: ranked levers with estimated Δheld and attention cost; +25% daily inflation cap; `at_risk` flag with the honest remaining math when cap insufficient.
7. Wire scheduler hooks (thin glue in `scheduler/`, calling engine fns): nightly rates 02:00, Sunday cascade 21:00, hourly trigger check.

## Done = these pass

- `test_rates_recovers_ground_truth` — run rates.py over the seeded 90-day fixture timeline; recovered blended rates within ±1.5pts of generation ground truth.
- `test_blend_thin_sample` — n=5 actual=0% with benchmark 4% k=30 → blended ≈ 3.4%, confidence=low.
- `test_blend_rich_sample` — n=200 → blended within 0.5pt of actual, confidence=high.
- `test_cascade_arithmetic` — goal 8 held/month, show .70, book .55, positive .35, replies {email .04, call .08, li .08}, 20 business days → assert weekly bookings, per-channel daily touches match hand-computed values (include the hand computation as comments).
- `test_cascade_capacity` — 3 PTO days removed → daily volumes rise, weekly target unchanged.
- `test_cascade_mix_bounds` — degenerate channel rates never push any channel <15% or >60%.
- `test_replan_triggers` — each of the 4 conditions fires alone; debounce suppresses duplicate within 24h; widened thresholds in cold-start.
- `test_bottleneck_priority` — show-rate −12pts → hold; aged positive reply 5h → convert beats hold? NO — verify documented order (1 before 2); neither → create.
- `test_catchup_cap` — gap requiring +40% inflation → returns +25% plan AND at_risk=true with shortfall quantified.
- `test_plan_never_mutated` — Plan has no update path; regeneration supersedes.
- Property test (hypothesis): cascade output volumes always non-negative, finite, and goal-consistent (volumes×rates ≈ weekly target ±1%).
- Coverage ≥ 95% on `engine/`. Zero imports of SQLAlchemy/FastAPI inside `engine/` (enforce with a lint test).
