# Devin Wiring Task — Connect v0 Export to the API

Prereq: v0 exports for the 3 screens committed under `frontend/`; backend through Session 2 (Pace earnings widgets need Session 1b).

## Task

1. Replace every mocked `lib/api.ts` function with real fetches: `GET /api/today`, `GET /api/review-queue`, `GET /api/pace`, `POST /api/jobs/{id}/approve|reject|skip|snooze` (reject payload: `{reason}`; edit-approve payload: `{edited_output, diff}`). Backend is source of truth for types — generate TS types from FastAPI OpenAPI schema (`openapi-typescript`), delete hand-written ones.
2. Reconcile schema drift: where v0's mock types and the OpenAPI schema disagree, change the FRONTEND. Do not reshape API responses for the UI's convenience except where a field is genuinely missing — then add it to the API response model properly.
3. SLA countdowns and queue counts poll every 30s (SWR/react-query, either fine — pick one, note it). No websockets in v1.
4. Edit-then-approve: capture the diff client-side (before/after), send both; verify it lands in `Job.approval.edit_diff`.
5. Keyboard shortcuts (A/E/R/S, auto-advance) verified working against live API.
6. Add `frontend` service to docker-compose; `make dev` boots both; CORS configured for local only.

## Done = these pass

- Playwright suite: load Today (fixture jobs render, SLA countdown ticks), approve + reject + edit-approve flows round-trip and Job status changes verified via API, Pace renders all four sections from fixture data, strategic card shows no Start/Approve affordance, draft-only banner present on Review Queue.
- `openapi-typescript` types compile with zero `any` in `lib/api.ts`.
- Lighthouse perf ≥85 on all three screens (local).
- Zero business logic in frontend: grep test — no rate math, no point valuation, no ranking logic in `frontend/` (display-only formatting allowed).
