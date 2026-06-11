# Devin Session 8 — Call Prep Agent

Prereq: Session 3 (AgentBase). Attach AGENTS.md §7.

## Task

1. `agents/call_prep.py`: T−30min trigger before call blocks and booked meetings; 5-min pre-call card per AGENTS.md §7 schema (who/why_now/last_interaction/goal/likely_objections×2/the_one_thing_to_show). Hard length budget: card renders ≤ 600 chars total (phone screen).
2. Inputs assembled from: brief (if exists), thread history, past transcripts via CallRecordingAdapter, signal, funnel context. Missing inputs degrade gracefully — card still generates with what exists, gaps marked "(no prior contact)".
3. Auto-approved, ephemeral: attached to the Today-screen job payload, no write-back.
4. Continuity rule (Golden): if thread contains a prior objection + response, `last_interaction` must instruct picking up that thread, not restarting pitch.

## Done = these pass

- `test_card_schema_and_budget` — all fields present, ≤600 chars.
- `test_graceful_degradation` — cold account (no brief/thread/transcript) → valid card, gaps marked.
- `test_continuity` — fixture with prior "we have Copilot" exchange → last_interaction references it and instructs continuation.
- `test_timing` — fake clock: card job created at T−30min, attached to the meeting's Today entry.
- Golden test for AGENTS.md §7.
