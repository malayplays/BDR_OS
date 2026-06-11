# v0 Prompt — Screen 2: Review Queue

Paste into v0 (works in Lovable unchanged).

---

Build a "Review Queue" screen for a personal BDR operating system — batched approval of AI-drafted work. Next.js App Router, Tailwind, shadcn/ui. The user checks this 3× daily and must clear it fast; optimize for keyboard-speed triage.

**Layout:** left rail of groups with counts: "⚡ Speed-to-book (2)" (always first, amber, each item shows SLA countdown), "Outreach drafts (6)", "Confirmations (3)", "Recovery sequences (1)", "CRM updates (2)", "Manager update (1)". Right pane shows the selected item.

**Item view:** context header (account, contact, persona tier chip, originating signal/trigger, expected value in pts); the draft in a clean reading pane (email drafts show subject + body; sequences show all 3 touches stacked with send-day labels — approved as ONE unit); below: agent's rationale line + confidence; any `policy_flags` rendered as yellow banners ("Rate limit: 41st outbound today — requires explicit OK"). For outreach: tabs for variants A/B/C with the agent's rationale per variant.

**Actions (sticky bar + keyboard):** Approve (A), Edit-then-approve (E — opens inline editor, diff is preserved), Reject (R — requires a one-line reason chip: "wrong tone / wrong facts / wrong timing / other"), Skip (S). Bulk: "Approve all in group" only on groups flagged low-risk. After action, auto-advance to next item.

**Guardrail banner (persistent, top):** "Draft-only mode until Sep 15 — approved items become drafts/scheduled tasks, nothing sends automatically." Strategic-account items render with a red border and Approve replaced by "Copy to clipboard for manual handling".

**Empty state:** "Queue clear. Next check-in: 3:30pm."

Mock data: 15 items across all groups incl. one with a policy flag, one strategic, one 3-touch recovery sequence, one manager-update draft. Typed `getReviewQueue()` in `lib/api.ts` mirroring `GET /api/review-queue`; actions call mocked `approveJob/rejectJob(id, payload)`.
