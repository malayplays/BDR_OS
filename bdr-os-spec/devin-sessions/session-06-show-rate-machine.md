# Devin Session 6 — Show-Rate Machine

Prereq: Sessions 0–5. Attach AGENTS.md §5. This is an explicit state machine — implement it as one (table-driven transitions, not if-spaghetti).

## Task

1. `agents/show_rate_machine.py` + `models/meeting_state.py`: states `BOOKED → INVITE_SENT → ACCEPTED → CONFIRMED_24H → CONFIRMED_AM → HELD | NO_SHOW`, risk sub-transitions per AGENTS.md §5 table. Transition table is data (`show_rate_machine.yaml`); illegal transitions raise; every transition writes EventLog.
2. Job emission per transition: instant invite (value-framed agenda + "bring a colleague"), 24h-unaccepted reconfirm, OOO reschedule, T−24h content-bearing confirm, morning-of proof point, >4-days-out pull-in offer. All customer-facing ⇒ approval REQUIRED.
3. Timers via scheduler: T−24h, morning-of (8am recipient-local), invite-acceptance check at booked+24h, attendance check at start+10min (calendar/meeting-link heuristic; mock provides scripted outcome).
4. No-show detection → `meeting_no_show` event → hand off to no_show_recovery (Session 7 stub: just assert the job is created).
5. Confirmation content must reference the meeting's originating signal/brief — generic reminders are a test failure.

## Done = these pass

- `test_transition_table` — every legal transition from the YAML succeeds; every illegal pair raises; full EventLog audit trail.
- `test_happy_path_e2e` — fixture booking → invite job → (approve) → accepted → 24h confirm job at T−24h → AM job → HELD; assert exact job sequence and timing using a fake clock.
- `test_invite_not_accepted_24h` — reconfirm job fires once, not repeatedly.
- `test_ooo_reroute` — OOO autoreply mid-state → reschedule job, state preserved for rebooking.
- `test_pull_in_offer` — booking 7 days out → pull_in job; booking 2 days out → none.
- `test_no_show_handoff` — scripted no-show fixture → meeting_no_show event + recovery job created at start+10min.
- `test_confirmations_carry_content` — 24h confirm draft contains signal-derived string (assert against fixture signal evidence), and "colleague" phrase present.
- `test_all_jobs_gated` — zero customer-facing job from this machine reaches written_back without approval during DRAFT_ONLY.
