# v0 Prompt — Screen 1: Today

Paste into v0 (works in Lovable unchanged). Build with mock data inline; Devin wires the real API later (see devin-wiring-task.md).

---

Build a "Today" screen for a personal sales (BDR) operating system. Next.js App Router, Tailwind, shadcn/ui, lucide-react. Dense but calm layout — this is a daily cockpit, not a marketing page. Dark-mode friendly.

**Header strip (one row):** date; points this month as "26.5 / 35 pts" with a thin progress bar showing two segments (credited solid, pending striped) and a goal tick; small pace chip: "+4%" green or "−12%" red; "$ projected this month" small muted; bottom-line chip: "1 VP meeting = 4.5 pts ≈ $450" (marginal value widget).

**Morning plan card:** 2-sentence narrative ("Show rate is the bottleneck (−12pts); an hour of confirmations recovers ~0.45 pts vs ~0.02 from cold outreach."), then a compact row of remaining-today counters: emails 12/18, calls 0/25 (next block 10:00–11:30), LinkedIn 4/6, confirmations due 3.

**Ranked job list (the core):** vertical list of job cards sorted by priority. Each card: stage badge (`create` slate / `convert` amber / `hold` red), job type label, account + contact + persona tier chip (C-Suite=violet, VP=blue, Director=teal, Manager=gray, IC=muted — IC cards get a subtle "low-value persona" warning icon), expected value "≈1.7 pts", one-line context ("Positive reply 2h ago — SLA 4h" with a live countdown if convert-stage), and three actions: **Start** (primary), Skip, Snooze. Convert-stage cards with SLA countdown pin to top with a subtle pulse. Strategic-account cards show a lock icon + "manual only" instead of Start.

**Collapsed sections below:** "Hygiene findings (4)" and "Completed today (7)".

Mock data: 9 jobs covering all three stages, one SLA countdown at 1h 12m remaining, one strategic-locked card, one dormancy-requalified card labeled "Net-new credit available (dormant 134d)". TypeScript, single page, components in-file or local; no global state lib; fetch from `lib/api.ts` with a mocked `getToday()` returning a typed `TodayPayload` (define the type — it mirrors `GET /api/today`).
