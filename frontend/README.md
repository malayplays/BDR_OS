# BDR OS — Frontend

> **Placeholder** — frontend implementation is handled in dedicated frontend sessions.

Tech stack (ARCHITECTURE.md §7): Next.js + Tailwind + shadcn/ui (v0 export).

The frontend talks only to FastAPI. No business logic lives here — it renders API
responses and posts decisions.

## Screens (ARCHITECTURE.md §5)

1. **Today** — `GET /api/today`: ranked job list, morning plan summary.
2. **Review Queue** — `GET /api/review-queue`, `POST /api/jobs/{id}/approve|reject|edit`.
3. **Pace** — `GET /api/pace`: goal cascade, funnel rates, pace gap, catch-up levers.
