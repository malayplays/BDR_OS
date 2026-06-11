# Devin Session 7 — No-Show Recovery Agent

Prereq: Session 6. Attach AGENTS.md §6.

## Task

1. `agents/no_show_recovery.py`: T+10min polite reschedule (two one-click times ≤3 days out, zero guilt language), then 3-touch sequence: +1d value nudge, +3d channel-switch call task, +7d graceful close.
2. **Pre-draft thread check** (Golden negative): if contact replied around meeting time ("running late"), suppress recovery entirely.
3. Sequence kill: any inbound reply from contact → remaining touches `skipped` instantly (wire to inbox_triage events).
4. Approval: the whole 3-touch sequence approves as ONE Review Queue unit; sends are then scheduled drafts (still drafts during DRAFT_ONLY — scheduled = the job sits ready for manual send with a due_at reminder).
5. Reschedule success → `meeting_rescheduled` event → re-enters show-rate machine at BOOKED.

## Done = these pass

- `test_t10_draft` — no-show event → draft within simulated 10min, contains 2 slots ≤3 days out, no guilt phrasing (lint against banned phrases list: "you missed", "no-show", "waited").
- `test_thread_check_suppression` — "running late" fixture → zero recovery jobs.
- `test_sequence_kill_on_reply` — reply after touch 1 → touches 2–3 skipped.
- `test_single_approval_unit` — queue shows one item containing all 3 touches; approving schedules all.
- `test_rebook_reenters_machine` — reschedule → show-rate machine state BOOKED with new event ref.
- Golden tests for AGENTS.md §6.
