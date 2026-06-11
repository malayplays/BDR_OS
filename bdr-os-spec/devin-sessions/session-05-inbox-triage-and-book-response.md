# Devin Session 5 — Inbox Triage + Speed-to-Book

One session, two agents — they're a chained pair and must be tested together (AGENTS.md §3–4). Speed-to-book is the top-priority automation in the whole system.

## Task

1. `agents/inbox_triage.py`: classification per AGENTS.md §3; continuous trigger off `watch_replies` poll (60s interval, configurable); EventLog write (`reply_received` / `positive_reply`); chains next_job per classification table.
2. Compliance fast-path: `unsubscribe` → immediate suppression-list write + kill all sequences/jobs for that contact, **no approval needed** (suppressive direction only — codify this exception in policy.yaml with a comment).
3. `agents/book_response.py`: BookDraft per AGENTS.md §4 — in-thread reply, acknowledges their words, 2 concrete slots from `find_slots(prefer ≤4 days)`, one link. `due_at = positive_reply + 4h`. Approval REQUIRED; queue payload includes SLA countdown timestamp.
4. OOO path: `ooo` classification → `reschedule_touch` job dated return+1d, sequence paused (not killed).

## Done = these pass

- `test_classification_fixtures` — all 30 fixture threads classify correctly (label the 30 in fixtures with expected class; ≥28/30 with live LLM flag, 30/30 with deterministic mock).
- `test_positive_chains_book_response_fast` — positive fixture → book_response job exists within one poll cycle, due_at correct, EventLog has positive_reply.
- `test_unsubscribe_kills_everything` — suppression written, pending jobs for contact → `skipped`, no future job creatable for contact (factory-level check).
- `test_ooo_pauses_not_kills` — sequence resumes at return date.
- `test_book_draft_slots` — drafted slots are ≤4 days out when available; >4-day slots only when calendar offers nothing sooner.
- `test_more_info_is_booking_opportunity` — Golden 1 §3: "send more info" → book_response, not a literature send.
- Golden tests for §3 and §4 examples.
