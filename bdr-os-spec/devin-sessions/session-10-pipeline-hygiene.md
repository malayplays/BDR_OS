# Devin Session 10 — Pipeline Hygiene Agent

Prereq: Sessions 0–6. Attach AGENTS.md §9.

## Task

1. `agents/pipeline_hygiene.py`: nightly 02:30 sweep over CRM tasks, threads, meetings, sequences per AGENTS.md §9 inputs list. Mostly deterministic queries; LLM only for the report narrative.
2. Auto-fix whitelist (data-hygiene only, in `policy.yaml`): close own stale BDR-OS-created tasks, repair missing EventLog↔job links, mark dead sequences. Everything else → proposed jobs into the normal gated flow.
3. Detection rules (each a pure function, individually testable): unreplied thread >5d, booked meeting w/ unaccepted invite & no reconfirm, contact ≥3 touches no reply 21d → suggest sequence end, no-show without recovery sequence, CRM task overdue >3d.
4. Strategic accounts: findings become manual-only tasks, never draft jobs (Golden negative).
5. HygieneReport persisted; surfaced as a collapsed group on Today.

## Done = these pass

- `test_each_detection_rule` — one fixture scenario per rule, fires exactly once, no false positive on clean data.
- `test_autofix_whitelist_only` — sweep on fixtures: auto-fixed items ⊆ whitelist; everything customer-adjacent is `pending` jobs.
- `test_strategic_manual_only` — stale strategic-account thread → manual task, zero draft jobs.
- `test_idempotent` — running sweep twice produces no duplicate jobs (dedupe on finding fingerprint).
- `test_show_rate_risk_flag` — Golden 1: unaccepted-invite meeting 3 days out → reconfirm proposal flagged "show-rate risk".
