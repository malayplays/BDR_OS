# Devin Session 12 — End-to-End Integration Against Fixtures

Prereq: ALL prior sessions merged. Attach ARCHITECTURE.md §4 (data flow) + this file.

## Task

1. **Simulation harness** (`tests/e2e/sim.py`): drives the whole system on a fake clock against fixtures — replay `event_timeline.json` day by day, run schedulers, auto-approve queue items via API (simulating Malay's 3 daily check-ins), assert system behavior.
2. **Scenario suite** (each a named scenario file):
   - `happy_week`: signal → brief → copy → (approve) → draft → scripted positive reply → book_response within SLA → booking → full show-rate machine → HELD → scribe → reporting. Assert EventLog tells the complete story and every customer-facing artifact passed an approval.
   - `no_show_week`: scripted no-show → recovery → rebook → HELD.
   - `behind_pace`: doctored timeline puts pace_gap at −20% → replan fires once (debounced), catch-up levers ranked correctly, +25% cap honored, Pace API shows at_risk honestly.
   - `rate_drift`: show rate drops 12pts → next morning's Today leads with hold-stage.
   - `guardrail_gauntlet`: strategic account in every flow → zero automated touches; rate-limit day (41+ outbound) → flagged; DRAFT_ONLY end-to-end → zero `send` calls anywhere (spy on EmailAdapter).
   - `cold_start`: empty personal history → benchmark-driven plan, low confidence everywhere, widened thresholds.
   - `credit_pipeline`: VP meeting booked → held → AD-accepted (+5 credited pts, provenance complete); second meeting no-shows (0 pts) → recovery → held → accepted; one acceptance lags 4d → hygiene nudge fires; earnings projector and promotion scorecard reflect final state exactly (assert $ math per COMP_MODEL.md §6).
3. **Optional live leg** (`RUN_LIVE=1`): Gmail + GCal real adapters against personal account — create one real draft + one real calendar event in a test calendar, verify, clean up.
4. Wire frontend build into docker-compose if frontend/ has been exported (else skip, don't block).
5. Produce `RUNBOOK.md`: start system, daily operation, approving via UI, reading Pace, swapping an adapter to live.

## Done = these pass

- All 6 scenarios green in CI with deterministic LLM mock.
- Full-suite coverage ≥85% overall; `engine/` still ≥95%.
- `guardrail_gauntlet` includes a meta-test: grep production code for any adapter write call not threading a `Verdict` — must be zero.
- `make demo` runs `happy_week` visibly (printed narrative of jobs/approvals/events) — this is the demo for week-1 conversations at Cognition.
